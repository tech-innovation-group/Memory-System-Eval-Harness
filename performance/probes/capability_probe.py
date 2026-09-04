#!/usr/bin/env python3
"""Probe optional EchoMem contracts using real HTTP only.

Only an explicit HTTP 404 proves that an endpoint is not implemented. Missing
configuration, missing test identity, and transport failures are
INCONCLUSIVE: the harness must not turn its own lack of an adapter into a
claim about EchoMem.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    method: str = "GET",
    body: dict[str, Any] | None = None,
    auth_key: str = "",
    auth_header: str = "X-Auth-Key",
    timeout_s: float = 10.0,
    preserve_raw: bool = False,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if auth_key:
        headers[auth_header] = auth_key
    started = time.monotonic()
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
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
                "elapsed_s": time.monotonic() - started,
                "headers": {str(k).lower(): str(v) for k, v in response.headers.items()},
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
            "elapsed_s": time.monotonic() - started,
            "headers": {str(k).lower(): str(v) for k, v in exc.headers.items()},
            "payload": payload,
            "error": f"HTTP {exc.code}",
        }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "status_code": None,
            "elapsed_s": time.monotonic() - started,
            "payload": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def request_cursor_uri(
    base_url: str,
    uri: str,
    *,
    auth_key: str = "",
    auth_header: str = "X-Auth-Key",
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """Read an ``echo://`` document through EchoMem's public fs/read API."""
    result = request(
        base_url,
        f"/fs/read?uri={urllib.parse.quote(uri, safe=':/')}",
        auth_key=auth_key,
        auth_header=auth_header,
        timeout_s=timeout_s,
        preserve_raw=True,
    )
    payload = result.get("payload")
    document: Any = {}
    if isinstance(payload, dict):
        fs_result = payload.get("result")
        if isinstance(fs_result, dict) and isinstance(fs_result.get("text"), str):
            try:
                document = json.loads(fs_result["text"])
            except json.JSONDecodeError:
                document = {"raw": fs_result["text"][-4000:]}
    result["cursor_uri"] = uri
    result["document"] = document
    return result


def classify_probe(
    name: str,
    result: dict[str, Any],
    *,
    required_keys: tuple[str, ...] = (),
    expect_list_key: str = "",
) -> dict[str, Any]:
    status_code = result.get("status_code")
    if status_code == 404:
        status = NOT_IMPLEMENTED
        reason = "endpoint returned HTTP 404"
    elif status_code is None:
        status = INCONCLUSIVE
        reason = "transport failure; capability could not be observed"
    elif not 200 <= int(status_code) < 300:
        status = FAIL
        reason = f"endpoint returned HTTP {status_code}"
    else:
        payload = result.get("payload")
        missing = [
            key for key in required_keys
            if not isinstance(payload, dict) or payload.get(key) in (None, "")
        ]
        if expect_list_key and (
            not isinstance(payload, dict) or not isinstance(payload.get(expect_list_key), list)
        ):
            missing.append(expect_list_key)
        status = FAIL if missing else PASS
        reason = "response satisfies the configured contract" if not missing else (
            f"missing response fields: {', '.join(missing)}"
        )
    return {
        "name": name,
        "status": status,
        "http_status": status_code,
        "elapsed_s": result.get("elapsed_s"),
        "reason": reason,
        "error": result.get("error", ""),
        "payload": result.get("payload", {}),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    auth_key = args.auth_key or os.getenv(args.auth_key_env, "")
    checks: list[dict[str, Any]] = []

    checks.append(classify_probe(
        "health/version",
        request(args.base_url, args.health_path, auth_key=auth_key,
                auth_header=args.auth_header, timeout_s=args.timeout_s),
    ))
    optional = [
        ("operation/idempotency", args.operation_path, args.operation_keys, ""),
        ("version/conflict", args.conflict_path, args.conflict_keys, ""),
        ("cache/TTL", args.ttl_path, args.ttl_keys, ""),
        ("engine status/degradation", args.engine_path, args.engine_keys, ""),
        ("fault control", args.fault_path, args.fault_keys, ""),
    ]
    if args.cursor_path:
        optional.insert(
            0,
            (
                "cursor/message-set",
                args.cursor_path,
                (),
                getattr(args, "message_list_key", "message_ids"),
            ),
        )
    for name, path, keys, list_key in optional:
        if not path:
            checks.append({
                "name": name,
                "status": INCONCLUSIVE,
                "http_status": None,
                "reason": "no probe endpoint configured; capability was not externally tested",
                "payload": {},
            })
            continue
        if "{session}" in path and not args.session_id:
            checks.append({
                "name": name,
                "status": INCONCLUSIVE,
                "http_status": None,
                "reason": "endpoint requires --session-id; capability was not externally tested",
                "payload": {},
            })
            continue
        path = path.replace("{session}", args.session_id)
        result = request(
            args.base_url,
            path,
            method="POST" if name == "fault control" else "GET",
            body={"action": "status"} if name == "fault control" else None,
            auth_key=auth_key,
            auth_header=args.auth_header,
            timeout_s=args.timeout_s,
        )
        checks.append(classify_probe(name, result, required_keys=keys, expect_list_key=list_key))
    if getattr(args, "cursor_uri_template", ""):
        if not args.session_id:
            checks.append({
                "name": "cursor/message-set",
                "status": INCONCLUSIVE,
                "http_status": None,
                "reason": "cursor URI requires --session-id; capability was not externally tested",
                "payload": {},
            })
        else:
            cursor_uri = args.cursor_uri_template.format(session=args.session_id)
            result = request_cursor_uri(
                args.base_url,
                cursor_uri,
                auth_key=auth_key,
                auth_header=args.auth_header,
                timeout_s=args.timeout_s,
            )
            check = classify_probe("cursor/message-set", result)
            check["cursor_uri"] = cursor_uri
            check["document_keys"] = (
                sorted(result["document"])
                if isinstance(result.get("document"), dict)
                else []
            )
            checks.append(check)

    metric_result = request(
        args.base_url, args.metrics_path, auth_key=auth_key,
        auth_header=args.auth_header, timeout_s=args.timeout_s, preserve_raw=True,
    )
    metric_check = classify_probe("Prometheus B7 metrics", metric_result)
    raw = metric_result.get("payload", {}).get("raw", "") if isinstance(metric_result.get("payload"), dict) else ""
    required_metrics = {
        "lane_queued": "echomem_lane_queued",
        "lane_wait": "echomem_lane_wait_seconds",
        "lane_exec": "echomem_lane_exec_seconds",
        "lane_rejected": "echomem_lane_rejected_total",
        "engine_exec": "echomem_engine_fanout_exec_seconds",
        "engine_skipped": "echomem_engine_fanout_skipped_total",
    }
    if metric_check["status"] == PASS:
        present = {
            key: metric in raw
            for key, metric in required_metrics.items()
        }
        metric_check["required_metrics"] = required_metrics
        metric_check["present"] = present
        metric_check["missing"] = [key for key, value in present.items() if not value]
        if metric_check["missing"]:
            metric_check["status"] = INCONCLUSIVE
            metric_check["reason"] = "metrics endpoint is reachable but B7 families are incomplete"
    checks.append(metric_check)

    statuses = [item["status"] for item in checks]
    overall = (
        FAIL if FAIL in statuses
        else INCONCLUSIVE if INCONCLUSIVE in statuses
        else NOT_IMPLEMENTED if NOT_IMPLEMENTED in statuses
        else PASS
    )
    return {
        "status": overall,
        "created_at": now(),
        "base_url": args.base_url,
        "real_http": True,
        "mock_model": False,
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
    parser = argparse.ArgumentParser(description="Probe optional real EchoMem capabilities")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--auth-key-env", default="ECHOMEM_AUTH_KEY")
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument("--health-path", default="/health")
    parser.add_argument("--metrics-path", default="/metrics")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--cursor-path", default="")
    parser.add_argument(
        "--cursor-uri-template",
        default="",
        help="通过 /fs/read 读取 echo:// 文档，例如 echo://sessions/{session}/current/commit_cursor.json",
    )
    parser.add_argument("--message-list-key", default="message_ids")
    parser.add_argument("--operation-path", default="")
    parser.add_argument("--operation-keys", nargs="*", default=["operation_id"])
    parser.add_argument("--conflict-path", default="")
    parser.add_argument("--conflict-keys", nargs="*", default=["version", "conflict_count"])
    parser.add_argument("--ttl-path", default="")
    parser.add_argument("--ttl-keys", nargs="*", default=["ttl_seconds"])
    parser.add_argument("--engine-path", default="")
    parser.add_argument("--engine-keys", nargs="*", default=["status"])
    parser.add_argument("--fault-path", default="")
    parser.add_argument("--fault-keys", nargs="*", default=["status"])
    parser.add_argument("--timeout-s", type=float, default=10.0)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
