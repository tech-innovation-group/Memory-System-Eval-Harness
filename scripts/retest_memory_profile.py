#!/usr/bin/env python3
"""Run a bounded live Search retest for memory_profile at C=16 and C=64.

This deliberately does not seed or commit memories. It uses an existing
owner-only identity file and saves raw Prometheus before/after snapshots for
each concurrency window.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.capacity_seed import CapacityActor
from performance.targets.echomem.acceptance.semantic_corpus import (
    assess_retrieval,
    build_fixed_fact_corpus,
)
from performance.targets.echomem.probes._client import EchoMemHTTP
from scripts.reproduce_memory_profile import histogram_delta


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_metrics(path: Path, base_url: str) -> None:
    with urllib.request.urlopen(base_url.rstrip("/") + "/metrics", timeout=10) as response:
        path.write_bytes(response.read())


def _load_actor(path: Path, base_url: str, tenant_index: int) -> CapacityActor:
    if path.stat().st_mode & 0o077:
        raise ValueError("identity file must be owner-only (0600)")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("identity file must contain a JSON list")
    matches = [row for row in rows if row.get("tenant_index") == tenant_index and row.get("user_index") == 0]
    if len(matches) != 1:
        raise ValueError("identity file must contain exactly one requested tenant/user")
    row = matches[0]
    client_info = row.get("client") or {}
    auth_key = str(client_info.get("auth_key") or "")
    if not auth_key:
        raise ValueError("requested identity has no auth key")
    client = EchoMemHTTP(
        base_url,
        auth_key,
        timeout_s=60,
        tenant_id=str(client_info.get("tenant_id") or ""),
        user_id=str(client_info.get("user_id") or ""),
        account_id=str(client_info.get("account_id") or ""),
        agent_id=str(client_info.get("agent_id") or "echomem-stress"),
    )
    return CapacityActor(
        tenant_index, 0, client, build_fixed_fact_corpus(f"tenant-{tenant_index}/user-0"),
        row.get("write_session") or "",
    )


def _ratio(low: dict[str, Any], high: dict[str, Any], key: str) -> float | None:
    def value(stats: dict[str, Any]) -> float | None:
        if key == "mean_s":
            return stats.get("mean_seconds")
        return (stats.get("percentiles_seconds") or {}).get(key.removesuffix("_s"))

    left, right = value(low), value(high)
    if left in (None, 0) or right is None:
        return None
    return round(right / left, 6)


def _status_counts(measurement: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in measurement.get("rows", []):
        if row.get("op") != "read" or not row.get("sent"):
            continue
        key = str(row.get("http_status"))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.concurrency != "16,64":
        raise ValueError("concurrency must be exactly 16,64 for this comparison")
    output = args.out
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    actor = _load_actor(args.identities, args.base_url, args.tenant_index)
    sample = actor.corpus["recall_queries"][0]
    started = time.monotonic()
    preflight = actor.client.request(
        "POST", "/api/retrieval/search",
        {"query": sample["query"], "agent_id": actor.client.agent_id, "limit": 10,
         "include_debug": True, "include_explain": True},
        timeout_s=args.request_timeout_s, operation="search",
    )
    quality = assess_retrieval(preflight.payload, sample)
    result: dict[str, Any] = {
        "status": "RUNNING", "base_url": args.base_url,
        "identity_file": str(args.identities), "tenant_index": args.tenant_index,
        "memory_profile_stage": "memory_profile", "concurrency_levels": [16, 64],
        "warmup_s": args.warmup_s, "duration_s": args.duration_s,
        "request_timeout_s": args.request_timeout_s,
        "preflight": {"http_status": preflight.status_code,
                       "elapsed_s": time.monotonic() - started,
                       "quality_ok": quality.get("quality_ok"),
                       "hit_count": quality.get("hit_count"),
                       "result_structure_valid": quality.get("result_structure_valid")},
        "levels": [],
    }
    _write_json(output / "run.json", result)
    for level in (16, 64):
        measure([actor], duration_s=args.warmup_s, q=1, search_workers=level,
                target_concurrency=level, request_timeout_s=args.request_timeout_s,
                load_mode="search", seed=1000 + level)
        before = output / f"C{level}-before.prom"
        after = output / f"C{level}-after.prom"
        _write_metrics(before, args.base_url)
        measurement = measure([actor], duration_s=args.duration_s, q=1,
                              search_workers=level, target_concurrency=level,
                              request_timeout_s=args.request_timeout_s,
                              load_mode="search", seed=2000 + level)
        _write_metrics(after, args.base_url)
        measurement_path = output / f"C{level}-measurement.json"
        _write_json(measurement_path, measurement)
        result["levels"].append({
            "concurrency": level, "measurement_file": measurement_path.name,
            "metrics_before": before.name, "metrics_after": after.name,
            "search": {"planned": measurement.get("planned_search"),
                        "sent": sum(1 for row in measurement.get("rows", [])
                                    if row.get("op") == "read" and row.get("sent")),
                        "peak_inflight_requests": level,
                        "http_status_counts": _status_counts(measurement)},
            "memory_profile": histogram_delta(before, after),
        })
        result["status"] = "PARTIAL"
        _write_json(output / "run.json", result)

    low = result["levels"][0]["memory_profile"]
    high = result["levels"][1]["memory_profile"]
    result["comparison"] = {
        "formula": "C64 memory_profile / C16 memory_profile",
        "ratios": {key: _ratio(low, high, key) for key in ("mean_s", "p50_s", "p95_s", "p99_s")},
        "comparison_ready": bool(low.get("observations") and high.get("observations")),
    }
    result["status"] = "MEASURED" if result["comparison"]["comparison_ready"] else "NO_STAGE_SAMPLE"
    _write_json(output / "run.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--identities", type=Path, required=True)
    parser.add_argument("--tenant-index", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", default="16,64")
    parser.add_argument("--warmup-s", type=float, default=2)
    parser.add_argument("--duration-s", type=float, default=10)
    parser.add_argument("--request-timeout-s", type=float, default=60)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.out),
                      "preflight": result["preflight"],
                      "comparison": result.get("comparison")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
