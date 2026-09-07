#!/usr/bin/env python3
"""Collect and verify EchoMem's protected per-tenant/lane black-box snapshot."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def expected_lanes_from_config(path: str | Path) -> list[str]:
    """Derive the effective scheduler lanes from the deployed JSON config.

    Explicit ``lanes`` declarations win. For native configs without that
    field, active provider paths are mapped to their runtime lane names. This
    deliberately has no fixed four/five-lane fallback: an unknown config is a
    missing prerequisite, not permission to fabricate an expected matrix.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    explicit: list[str] = []
    inferred: set[str] = set()

    def visit(value: Any, parts: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            if value.get("enabled") is False:
                return
            lanes = value.get("lanes")
            if isinstance(lanes, list):
                explicit.extend(str(item).strip() for item in lanes if str(item).strip())
            path_text = ".".join(parts).lower()
            if value.get("api_base") and value.get("model"):
                if "query_embedding" in path_text or "embedding" in path_text:
                    inferred.add("recall_query_embedding")
                elif "intent" in path_text:
                    inferred.add("recall_intent_llm")
                elif "rerank" in path_text:
                    inferred.add("recall_rerank")
                elif "recall" in path_text or "engine" in path_text:
                    inferred.add("recall_engine")
            for key, child in value.items():
                visit(child, (*parts, str(key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*parts, str(index)))

    visit(payload)
    lanes = explicit or sorted(inferred | ({"commit"} if inferred else set()))
    return list(dict.fromkeys(lanes))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _non_negative_number(value: Any, *, integer: bool = False) -> bool:
    """Validate a counter/gauge without accepting bool or NaN as evidence."""
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(number) or number < 0:
        return False
    return not integer or number.is_integer()


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
    duplicate_rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for item in normalized_rows:
        key = (item["tenant_id"], item["lane"])
        if not key[0] or not key[1]:
            continue
        if key in seen_keys:
            duplicate_rows.append({"tenant_id": key[0], "lane": key[1]})
        seen_keys.add(key)
    required_fields = (
        "queued",
        "wait_seconds_total",
        "exec_seconds_total",
        "rejected_total",
        "accepted_total",
        "completed_total",
        "failed_total",
    )
    missing: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
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
                integer_fields = {
                    "queued", "rejected_total", "accepted_total",
                    "completed_total", "failed_total",
                }
                invalid_fields = [
                    field
                    for field in required_fields
                    if not _non_negative_number(
                        row.get(field),
                        integer=field in integer_fields,
                    )
                ]
                if invalid_fields:
                    invalid.append(
                        {
                            "tenant_id": tenant_id,
                            "lane": lane,
                            "fields": invalid_fields,
                        }
                    )
                observed.append(
                    {
                        "tenant_id": tenant_id,
                        "lane": lane,
                        **{field: row[field] for field in required_fields},
                    }
                )
    empty_expectations = not expected_tenants or not expected_lanes
    complete = bool(
        expected_tenants
        and expected_lanes
        and not missing
        and not invalid
        and not duplicate_rows
    )
    result.update(
        {
            "status": PASS if complete else INCONCLUSIVE,
            "http_status": payload.get("_http_status"),
            "generated_at": payload.get("generated_at"),
            "process_id": payload.get("process_id") or payload.get("pid"),
            "process_started_at": payload.get("process_started_at") or payload.get("started_at"),
            "boot_id": payload.get("boot_id"),
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
            "invalid": invalid,
            "duplicate_rows": duplicate_rows,
            "empty_expectations": empty_expectations,
            "required_fields": list(required_fields),
            "reason": (
                "每个预期租户/lane 均返回合法且唯一的四元组"
                if complete
                else (
                    "未提供预期租户或 lane，无法形成完整分母"
                    if empty_expectations
                    else (
                    "逐租户/lane 观测值存在类型、范围或重复键问题"
                    if invalid or duplicate_rows
                    else "部分预期租户/lane 没有完整四元组快照"
                    )
                )
            ),
        }
    )
    result.pop("_http_status", None)
    return result


def run(ctx) -> None:
    import os
    params = ctx.params
    token = os.environ.get(str(params.get("token_env", "ECHOMEM_TEST_CONTROL_TOKEN")), "")
    if not token:
        result = {"status": INCONCLUSIVE, "reason": "Missing test control token"}
    else:
        result = collect(
            base_url=ctx.base_url, endpoint=str(params.get("endpoint", "")), token=token,
            expected_tenants=list(params.get("expected_tenants", [])),
            expected_lanes=list(params.get("expected_lanes", [])),
            timeout_s=float(params.get("timeout_s", 15)),
        )
    ctx.check("tenant-observability", status=result["status"],
              reason=result.get("reason", ""), detail=json.dumps(result, ensure_ascii=False))
