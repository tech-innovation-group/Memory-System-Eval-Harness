#!/usr/bin/env python3
"""Run PR397 missing cases that are observable through the real EchoMem API.

This module deliberately does not infer idempotency from two successful HTTP
requests. A service must expose and document an idempotency key or equivalent
operation identity before that property can be accepted.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._client import EchoMemHTTP, extract_archive, load_tenant_specs, status_from
except ImportError:
    from _client import EchoMemHTTP, extract_archive, load_tenant_specs, status_from

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def has_marker(payload: Any, marker: str) -> bool:
    return marker in json.dumps(payload, ensure_ascii=False, default=str)


def message_ids_from_payload(payload: Any) -> set[str]:
    """Extract persisted message IDs from history/archive/cursor-shaped payloads."""
    if not isinstance(payload, dict):
        return set()
    found: set[str] = set()
    containers: list[Any] = [payload]
    for key in ("history", "archive", "result", "data", "cursor"):
        value = payload.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for item in containers:
        for key in (
            "messages",
            "message_ids",
            "items",
            "committed_message_ids",
            "source_turn_ids",
        ):
            values = item.get(key) if isinstance(item, dict) else None
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict):
                    message_id = value.get("id") or value.get("message_id")
                    if message_id:
                        found.add(str(message_id))
                elif value not in (None, ""):
                    found.add(str(value))
    return found


def read_commit_cursor(client: EchoMemHTTP, session_id: str) -> dict[str, Any]:
    """Read the durable per-session cursor via EchoMem's existing /fs/read API."""
    response = client.fs_read(
        f"echo://sessions/{session_id}/current/commit_cursor.json"
    )
    payload = response.payload if isinstance(response.payload, dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    text = result.get("text") if isinstance(result, dict) else ""
    try:
        cursor = json.loads(text) if isinstance(text, str) and text else {}
    except json.JSONDecodeError:
        cursor = {}
    return {
        "status_code": response.status_code,
        "error": response.error,
        "available": bool(cursor),
        "cursor": cursor,
    }


def poll_commit(
    client: EchoMemHTTP,
    session_id: str,
    archive_id: str,
    *,
    timeout_s: float,
    interval_s: float,
) -> dict[str, Any]:
    started = time.monotonic()
    states: list[str] = []
    responses: list[dict[str, Any]] = []
    terminal = {"completed", "complete", "succeeded", "success", "failed", "error"}
    while time.monotonic() - started <= timeout_s:
        response = client.commit_status(session_id, archive_id)
        payload = response.payload if isinstance(response.payload, dict) else {}
        state = status_from(payload)
        if state:
            states.append(state)
        responses.append({
            "at": now(),
            "status_code": response.status_code,
            "state": state,
            "error": response.error,
        })
        if state in terminal:
            return {
                "status": PASS if state in {"completed", "complete", "succeeded", "success"} else FAIL,
                "final_state": state,
                "states": states,
                "responses": responses,
                "elapsed_s": time.monotonic() - started,
            }
        time.sleep(max(0.05, interval_s))
    return {
        "status": INCONCLUSIVE,
        "reason": "commit did not reach a terminal state before drain deadline",
        "states": states,
        "responses": responses,
        "elapsed_s": time.monotonic() - started,
    }


def run_consistency(
    client: EchoMemHTTP,
    tenant: str,
    *,
    commit_timeout_s: float,
    search_timeout_s: float,
    visibility_timeout_s: float,
) -> dict[str, Any]:
    marker = f"pr397-consistency-{tenant}-{uuid.uuid4().hex}"
    session_id, _ = client.open_session(tenant, f"pr397-consistency-{tenant}")
    message_id = f"message-{uuid.uuid4().hex}"
    added = client.add_message(session_id, message_id, marker)
    commit = client.commit(session_id)
    archive_id = extract_archive(commit.payload) if isinstance(commit.payload, dict) else ""
    result: dict[str, Any] = {
        "tenant": tenant,
        "session_id": session_id,
        "marker": marker,
        "message_status": added.status_code,
        "commit_status_code": commit.status_code,
        "archive_id": archive_id,
        "started_at": now(),
    }
    if not archive_id:
        result.update({
            "status": FAIL,
            "reason": "accepted Commit did not return archive_id",
            "finished_at": now(),
        })
        return result
    state = poll_commit(client, session_id, archive_id, timeout_s=commit_timeout_s, interval_s=1)
    result["commit_state"] = state
    if state["status"] != PASS:
        result.update({"status": state["status"], "finished_at": now()})
        return result
    # Search is a semantic read and can miss an otherwise persisted marker.
    # Check the service's durable/readback APIs separately before classifying
    # the result as a persistence failure.
    expected_message_ids = {message_id}
    readback: list[dict[str, Any]] = []
    for name, response in (
        ("history", client.get_history(session_id)),
        ("archive", client.get_archive(session_id, archive_id)),
        ("commit_memories", client.get_commit_memories(session_id, archive_id)),
    ):
        payload = response.payload if isinstance(response.payload, dict) else {}
        observed_ids = message_ids_from_payload(payload)
        readback.append({
            "kind": name,
            "status_code": response.status_code,
            "contains_marker": has_marker(payload, marker),
            "expected_message_ids": sorted(expected_message_ids),
            "observed_message_ids": sorted(observed_ids),
            "message_ids_complete": expected_message_ids <= observed_ids,
            "error": response.error,
        })
    cursor_observation = read_commit_cursor(client, session_id)
    cursor_ids = message_ids_from_payload(cursor_observation.get("cursor"))
    readback.append({
        "kind": "commit_cursor",
        "status_code": cursor_observation.get("status_code"),
        "available": cursor_observation.get("available"),
        "expected_message_ids": sorted(expected_message_ids),
        "observed_message_ids": sorted(cursor_ids),
        "message_ids_complete": expected_message_ids <= cursor_ids,
        "error": cursor_observation.get("error", ""),
    })
    result["readback"] = readback
    readback_visible = any(
        item.get("contains_marker") or item.get("message_ids_complete")
        for item in readback
    )
    result["message_set_reconciliation"] = {
        "expected": sorted(expected_message_ids),
        "observed_by_source": {
            item["kind"]: item.get("observed_message_ids", [])
            for item in readback
        },
        "complete_sources": [
            item["kind"] for item in readback if item.get("message_ids_complete")
        ],
        "status": (
            PASS
            if any(item.get("message_ids_complete") for item in readback)
            else INCONCLUSIVE
        ),
    }
    commit_completed_monotonic = time.monotonic()
    first_visible_s: float | None = None
    deadline = time.monotonic() + visibility_timeout_s
    searches: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        search_started = time.monotonic()
        response = client.search(
            session_id,
            f"Return the exact marker {marker}",
            timeout_s=search_timeout_s,
        )
        payload = response.payload if isinstance(response.payload, dict) else {}
        visible = has_marker(payload, marker)
        searches.append({
            "at": now(),
            "status_code": response.status_code,
            "visible": visible,
            "elapsed_s": time.monotonic() - search_started,
            "result_count": len(payload.get("items") or []) if isinstance(payload, dict) else 0,
        })
        if visible:
            first_visible_s = time.monotonic()
            break
        time.sleep(1)
    result.update({
        "status": (
            PASS
            if first_visible_s is not None
            else INCONCLUSIVE
            if readback_visible
            else FAIL
        ),
        "search_visibility_status": PASS if first_visible_s is not None else FAIL,
        "persistence_readback_status": PASS if readback_visible else FAIL,
        "search_missed_persisted_marker": bool(
            first_visible_s is None and readback_visible
        ),
        "first_visible_at": now() if first_visible_s is not None else "",
        "visibility_latency_s": (
            first_visible_s - commit_completed_monotonic
            if first_visible_s is not None
            else None
        ),
        "not_visible_after_completed": first_visible_s is None,
        "searches": searches,
        "finished_at": now(),
    })
    return result


def run_state_machine(
    client: EchoMemHTTP,
    tenant: str,
    *,
    commit_timeout_s: float,
) -> dict[str, Any]:
    session_id, _ = client.open_session(tenant, f"pr397-state-{tenant}")
    client.add_message(session_id, f"message-{uuid.uuid4().hex}", f"state-machine-{uuid.uuid4().hex}")
    commit = client.commit(session_id)
    archive_id = extract_archive(commit.payload) if isinstance(commit.payload, dict) else ""
    if not archive_id:
        return {
            "status": FAIL,
            "tenant": tenant,
            "reason": "accepted Commit did not return archive_id",
        }
    result = poll_commit(
        client, session_id, archive_id, timeout_s=commit_timeout_s, interval_s=0.5
    )
    states = result.get("states") or []
    regressions = [
        {"from": previous, "to": current, "index": index}
        for index, (previous, current) in enumerate(zip(states, states[1:]), start=1)
        if previous in {"completed", "complete", "succeeded", "success"}
        and current not in {"completed", "complete", "succeeded", "success"}
    ]
    result.update({
        "tenant": tenant,
        "session_id": session_id,
        "archive_id": archive_id,
        "regressions": regressions,
        "status": FAIL if regressions else result["status"],
    })
    return result


def run_cold_warm(
    client: EchoMemHTTP,
    tenant: str,
    *,
    search_timeout_s: float,
) -> dict[str, Any]:
    session_id, _ = client.open_session(tenant, f"pr397-cold-warm-{tenant}")
    latencies: list[float] = []
    statuses: list[int | None] = []
    for index in range(4):
        started = time.monotonic()
        response = client.search(
            session_id, f"cold-warm probe {index}", timeout_s=search_timeout_s
        )
        latencies.append(time.monotonic() - started)
        statuses.append(response.status_code)
    return {
        "status": PASS if all(code and 200 <= code < 300 for code in statuses) else FAIL,
        "tenant": tenant,
        "session_id": session_id,
        "cold_search_s": latencies[0],
        "warm_search_s": latencies[1:],
        "status_codes": statuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument("--max-tenants", type=int, default=0)
    parser.add_argument("--commit-timeout-s", type=float, default=120)
    parser.add_argument("--search-timeout-s", type=float, default=40)
    parser.add_argument("--visibility-timeout-s", type=float, default=60)
    args = parser.parse_args()
    specs = load_tenant_specs(args.tenant_config)
    if args.max_tenants > 0:
        specs = specs[: args.max_tenants]
    cases: list[dict[str, Any]] = []
    for spec in specs:
        client = EchoMemHTTP(
            args.base_url,
            spec.auth_key,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            account_id=spec.account_id,
            agent_id=spec.agent_id,
            auth_header=args.auth_header,
        )
        try:
            cases.append({
                "kind": "write-after-read",
                "result": run_consistency(
                    client,
                    spec.tenant_id,
                    commit_timeout_s=args.commit_timeout_s,
                    search_timeout_s=args.search_timeout_s,
                    visibility_timeout_s=args.visibility_timeout_s,
                ),
            })
            cases.append({
                "kind": "commit-state-machine",
                "result": run_state_machine(
                    client,
                    spec.tenant_id,
                    commit_timeout_s=args.commit_timeout_s,
                ),
            })
            cases.append({
                "kind": "cold-warm-search",
                "result": run_cold_warm(
                    client,
                    spec.tenant_id,
                    search_timeout_s=args.search_timeout_s,
                ),
            })
        except Exception as exc:
            cases.append({
                "kind": "runtime",
                "result": {
                    "status": FAIL,
                    "tenant": spec.tenant_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            })
        cases.append({
            "kind": "commit-idempotency",
            "result": {
                "status": INCONCLUSIVE,
                "tenant": spec.tenant_id,
                "reason": (
                    "普通 Session Commit 没有公开 Idempotency-Key/operation replay 契约；"
                    "该结论不能由 history、archive 或 cursor 单独证明"
                ),
            },
        })
        cases.append({
            "kind": "memory-garden-idempotency-version",
            "result": {
                "status": INCONCLUSIVE,
                "tenant": spec.tenant_id,
                "reason": (
                    "Memory Garden 的幂等和版本冲突属于独立操作接口，"
                    "不应与普通 Session Commit 结果混合；需要专门的 Memory Unit 测试输入"
                ),
            },
        })
    statuses = [str((case.get("result") or {}).get("status")) for case in cases]
    overall = FAIL if FAIL in statuses else INCONCLUSIVE if INCONCLUSIVE in statuses or NOT_IMPLEMENTED in statuses else PASS
    payload = {
        "status": overall,
        "created_at": now(),
        "base_url": args.base_url,
        "real_http": True,
        "mock_model": False,
        "cases": cases,
        "summary": {
            "total": len(cases),
            "pass": statuses.count(PASS),
            "fail": statuses.count(FAIL),
            "inconclusive": statuses.count(INCONCLUSIVE),
            "not_implemented": statuses.count(NOT_IMPLEMENTED),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if overall == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
