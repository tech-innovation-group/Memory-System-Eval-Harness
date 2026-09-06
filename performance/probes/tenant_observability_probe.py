#!/usr/bin/env python3
"""Collect and verify EchoMem's protected per-tenant/lane black-box snapshot."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _get_json(endpoint: str, token: str, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        headers={
            "Accept": "application/json",
            **({"X-EchoMem-Test-Token": token} if token else {}),
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body) if body else {}
        if not isinstance(payload, dict):
            raise ValueError("observability response must be an object")
        payload["_http_status"] = response.status
        return payload


def collect(
    *,
    base_url: str,
    endpoint: str,
    token: str,
    expected_tenants: list[str],
    expected_lanes: list[str],
    timeout_s: float,
) -> dict[str, Any]:
    resolved_endpoint = endpoint.strip() or (
        base_url.rstrip("/") + "/api/inspect/tenant-observability"
    )
    result: dict[str, Any] = {
        "created_at": now(),
        "base_url": base_url,
        "endpoint": resolved_endpoint,
        "real_http": True,
        "mock_model": False,
        "expected_tenants": expected_tenants,
        "expected_lanes": expected_lanes,
        "public_prometheus_labels_exclude_tenant": True,
    }
    try:
        payload = _get_json(resolved_endpoint, token, timeout_s)
    except urllib.error.HTTPError as exc:
        result.update(
            {
                "status": INCONCLUSIVE if exc.code == 404 else FAIL,
                "http_status": exc.code,
                "reason": (
                    "逐租户观测接口未启用或 token 不匹配"
                    if exc.code == 404
                    else f"逐租户观测接口 HTTP {exc.code}"
                ),
            }
        )
        return result
    except (OSError, urllib.error.URLError, ValueError) as exc:
        result.update(
            {
                "status": FAIL,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        )
        return result

    rows = payload.get("rows")
    rows = rows if isinstance(rows, list) else []
    normalized_rows: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        normalized_rows.append(
            {
                **item,
                "tenant_id": str(
                    item.get("tenant_id")
                    or item.get("tenant")
                    or item.get("tenantId")
                    or ""
                ),
                "lane": str(
                    item.get("lane")
                    or item.get("lane_name")
                    or item.get("laneName")
                    or ""
                ),
            }
        )
    row_map = {
        (item["tenant_id"], item["lane"]): item
        for item in normalized_rows
        if item["tenant_id"] and item["lane"]
    }
    required_fields = (
        "queued",
        "wait_seconds_total",
        "exec_seconds_total",
        "rejected_total",
    )
    missing: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for tenant_id in expected_tenants:
        for lane in expected_lanes:
            row = row_map.get((tenant_id, lane))
            fields_present = bool(
                row is not None and all(field in row for field in required_fields)
            )
            if not fields_present:
                missing.append({"tenant_id": tenant_id, "lane": lane})
            else:
                observed.append(
                    {
                        "tenant_id": tenant_id,
                        "lane": lane,
                        **{field: row[field] for field in required_fields},
                        "accepted_total": row.get("accepted_total"),
                        "completed_total": row.get("completed_total"),
                        "failed_total": row.get("failed_total"),
                    }
                )
    result.update(
        {
            "status": PASS if not missing else INCONCLUSIVE,
            "http_status": payload.get("_http_status"),
            "generated_at": payload.get("generated_at"),
            "tenant_count": payload.get("tenant_count"),
            "lane_count": payload.get("lane_count"),
            "row_count": len(normalized_rows),
            "rows": observed,
            "unexpected": [
                {
                    "tenant_id": item["tenant_id"],
                    "lane": item["lane"],
                }
                for item in normalized_rows
                if (
                    item["tenant_id"] not in expected_tenants
                    or item["lane"] not in expected_lanes
                )
            ],
            "missing": missing,
            "required_fields": list(required_fields),
            "reason": (
                "每个预期租户/lane 均返回四元组"
                if not missing
                else "部分预期租户/lane 没有完整四元组快照"
            ),
        }
    )
    result.pop("_http_status", None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("ECHOMEM_TENANT_OBSERVABILITY_URL", ""),
    )
    parser.add_argument("--token", default="")
    parser.add_argument("--token-env", default="ECHOMEM_TEST_CONTROL_TOKEN")
    parser.add_argument("--expected-tenants", required=True)
    parser.add_argument("--expected-lanes", required=True)
    parser.add_argument("--timeout-s", type=float, default=15.0)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    token = str(args.token or os.environ.get(args.token_env, "")).strip()
    result = collect(
        base_url=args.base_url,
        endpoint=args.endpoint,
        token=token,
        expected_tenants=_csv(args.expected_tenants),
        expected_lanes=_csv(args.expected_lanes),
        timeout_s=args.timeout_s,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
