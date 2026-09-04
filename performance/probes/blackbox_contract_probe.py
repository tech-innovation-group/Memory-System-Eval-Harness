#!/usr/bin/env python3
"""Probe EchoMem's existing black-box contracts after a real stress run.

The harness does not require a new EchoMem endpoint.  It reuses a session and
archive recorded by the runner, then checks the public history/archive/status
APIs, the existing commit cursor file, and Prometheus metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

PASS = "PASS"
FAIL = "FAIL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request(
    base_url: str,
    path: str,
    *,
    auth_key: str,
    auth_header: str,
    timeout_s: float,
    preserve_raw: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers={"Accept": "application/json", auth_header: auth_key}
        if auth_key
        else {"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload: Any = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw if preserve_raw else raw[-4000:]}
            return {
                "status_code": response.status,
                "elapsed_s": round(time.monotonic() - started, 6),
                "payload": payload,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw if preserve_raw else raw[-4000:]}
        return {
            "status_code": exc.code,
            "elapsed_s": round(time.monotonic() - started, 6),
            "payload": payload,
            "error": f"HTTP {exc.code}",
        }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "status_code": None,
            "elapsed_s": round(time.monotonic() - started, 6),
            "payload": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def classify(name: str, result: dict[str, Any], *, allow_empty: bool = True) -> dict[str, Any]:
    code = result.get("status_code")
    if code == 404:
        status, reason = NOT_IMPLEMENTED, "EchoMem 明确返回 HTTP 404"
    elif code is None:
        status, reason = INCONCLUSIVE, "传输失败，无法判断 EchoMem 是否支持该能力"
    elif not 200 <= int(code) < 300:
        status, reason = FAIL, f"接口返回 HTTP {code}"
    elif allow_empty or result.get("payload") not in ({}, None):
        status, reason = PASS, "接口可通过真实 HTTP 访问"
    else:
        status, reason = INCONCLUSIVE, "接口返回为空，无法判断响应契约"
    return {
        "name": name,
        "status": status,
        "http_status": code,
        "elapsed_s": result.get("elapsed_s"),
        "reason": reason,
        "error": result.get("error", ""),
        "payload_keys": sorted(result["payload"]) if isinstance(result.get("payload"), dict) else [],
    }


def first_completed(path: Path, tenant: str = "") -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if tenant and str(row.get("tenant") or "") != tenant:
            continue
        if (
            str(row.get("status") or "").lower()
            in {"completed", "complete", "success", "succeeded"}
        ):
            return row
    return {}


def probe(args: argparse.Namespace) -> dict[str, Any]:
    row = first_completed(args.commit_csv, args.tenant)
    session = str(row.get("session_id") or "")
    archive = str(row.get("archive_id") or "")
    checks: list[dict[str, Any]] = []
    if not session:
        return {
            "status": INCONCLUSIVE,
            "created_at": now(),
            "real_http": True,
            "reason": "commit_results.csv 中没有已完成 Commit 的 session_id",
            "checks": checks,
        }
    if not archive:
        # Older runner artifacts did not persist archive_id. Keep probing the
        # session-scoped contracts, but make the missing operation identity
        # explicit instead of discarding otherwise useful evidence.
        checks.append({
            "name": "commit_identity",
            "status": INCONCLUSIVE,
            "http_status": None,
            "reason": "旧版 commit_results.csv 未记录 archive_id，无法定位单次 commit_status",
            "payload_keys": [],
        })

    paths = [
        ("history", f"/api/sessions/{quote(session, safe='')}/history?limit=200"),
        ("archives", f"/api/sessions/{quote(session, safe='')}/archives?limit=200"),
    ]
    if archive:
        paths.extend([
            (
                "commit_status",
                f"/api/sessions/{quote(session, safe='')}/commits/{quote(archive, safe='')}",
            ),
            (
                "commit_memories",
                f"/api/sessions/{quote(session, safe='')}/commits/{quote(archive, safe='')}/memories",
            ),
        ])
    for name, path in paths:
        checks.append(
            classify(
                name,
                request(
                    args.base_url,
                    path,
                    auth_key=args.auth_key,
                    auth_header=args.auth_header,
                    timeout_s=args.timeout_s,
                ),
            )
        )

    cursor_uri = args.cursor_uri_template.format(session=session)
    checks.append(
        classify(
            "commit_cursor",
            request(
                args.base_url,
                f"/fs/read?uri={quote(cursor_uri, safe=':/')}",
                auth_key=args.auth_key,
                auth_header=args.auth_header,
                timeout_s=args.timeout_s,
            ),
        )
    )

    metrics = request(
        args.base_url,
        args.metrics_path,
        auth_key=args.auth_key,
        auth_header=args.auth_header,
        timeout_s=args.timeout_s,
        preserve_raw=True,
    )
    metric = classify("metrics", metrics)
    metric_text = ""
    payload = metrics.get("payload")
    if isinstance(payload, dict):
        metric_text = str(payload.get("raw") or "")
    required = {
        "lane_queued": "echomem_lane_queued",
        "lane_wait": "echomem_lane_wait_seconds",
        "lane_exec": "echomem_lane_exec_seconds",
        "lane_rejected": "echomem_lane_rejected_total",
        "engine_exec": "echomem_engine_fanout_exec_seconds",
        "engine_skipped": "echomem_engine_fanout_skipped_total",
    }
    if metric.get("status") == PASS:
        metric["present"] = {key: value in metric_text for key, value in required.items()}
        metric["missing"] = [key for key, present in metric["present"].items() if not present]
        if metric["missing"]:
            metric["status"] = INCONCLUSIVE
            metric["reason"] = "metrics 可访问，但 PR449 B7/fan-out 指标族不完整"
    checks.append(metric)

    statuses = [str(item["status"]) for item in checks]
    status = (
        FAIL if FAIL in statuses
        else INCONCLUSIVE if INCONCLUSIVE in statuses
        else NOT_IMPLEMENTED if NOT_IMPLEMENTED in statuses
        else PASS
    )
    return {
        "status": status,
        "created_at": now(),
        "real_http": True,
        "mock_model": False,
        "base_url": args.base_url,
        "session_id": session,
        "archive_id": archive,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "pass": statuses.count(PASS),
            "fail": statuses.count(FAIL),
            "inconclusive": statuses.count(INCONCLUSIVE),
            "not_implemented": statuses.count(NOT_IMPLEMENTED),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe EchoMem black-box contracts")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--commit-csv", required=True, type=Path)
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--auth-key-env", default="")
    parser.add_argument("--tenant", default="")
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument("--metrics-path", default="/metrics")
    parser.add_argument(
        "--cursor-uri-template",
        default="echo://sessions/{session}/current/commit_cursor.json",
    )
    parser.add_argument("--timeout-s", type=float, default=10.0)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not args.auth_key and args.auth_key_env:
        args.auth_key = os.getenv(args.auth_key_env, "")
    result = probe(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result.get("summary", {"status": result["status"]}), ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
