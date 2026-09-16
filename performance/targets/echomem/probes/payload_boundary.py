"""真实 API 与 MCP 请求体边界、超长 Message/Commit 探针。"""

from __future__ import annotations

import json
import hashlib
import re
import time
import uuid
from pathlib import Path
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval
from performance.targets.echomem.probes._client import (
    EchoMemHTTP,
    extract_archive,
    load_tenant_specs,
    status_from,
)

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"

_LONG_FACTS = (
    ("first", "the project owner is Rowan", "Who owns the project in the long Commit?"),
    ("middle", "the review room is Cedar", "Which review room is recorded in the long Commit?"),
    ("last", "the approval code is ORBIT-47", "What approval code is recorded in the long Commit?"),
)


def _case_outcome(row: dict[str, Any]) -> str:
    code = row.get("http_status")
    if not row.get("dispatched", True):
        return "SETUP_FAILED"
    if code is None:
        return "TRANSPORT_FAILED"
    if code >= 500:
        return "SERVER_ERROR"
    if code in {401, 403, 404, 405}:
        return "AUTH_OR_ENDPOINT_BLOCKED"
    if code == 429:
        return "RATE_LIMITED"
    if code in {400, 413, 415, 422}:
        return "INPUT_REJECTED"
    if 200 <= code < 300:
        if row["encoding"] == "binary" and row["content_bytes"] > 0:
            return "INVALID_BINARY_ACCEPTED"
        return "ACCEPTED_NOT_PERSISTENCE_PROOF"
    return "UNEXPECTED_STATUS"


def _safe_result(result: Any) -> dict[str, Any]:
    return {
        "http_status": result.status_code,
        "elapsed_ms": round(result.elapsed_s * 1000, 3),
        "reason_code": result.reason_code,
        "transport_error_type": result.transport_error_type,
        "accepted": result.status_code is not None and 200 <= result.status_code < 300,
    }


def _json_bytes(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _natural_payload(size: int, label: str) -> str:
    """Return an exact-length readable payload with facts at three positions."""
    first = f"{label} first fact: {_LONG_FACTS[0][1]}. "
    middle = f"{label} middle fact: {_LONG_FACTS[1][1]}. "
    last = f"{label} last fact: {_LONG_FACTS[2][1]}. "
    filler = ("This natural-language stress record preserves context, decisions, and follow-up details "
              "so persistence and retrieval are tested with realistic content. ")
    if size <= len(first) + len(middle) + len(last):
        return (first + middle + last)[:size]
    available = size - len(first) - len(middle) - len(last)
    body = (filler * (available // len(filler) + 1))[:available]
    midpoint = len(body) // 2
    return first + body[:midpoint] + middle + body[midpoint:] + last


def _poll_commit(client: EchoMemHTTP, session_id: str, archive_id: str,
                 timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.commit_status(session_id, archive_id)
        state = status_from(response.payload)
        last = {**_safe_result(response), "state": state}
        if last["accepted"] and state in {"completed", "done", "success", "failed", "error"}:
            break
        time.sleep(0.5)
    return last


def _mcp_add_memory(params: dict[str, Any], tenant: Any, content: str, verifier=None) -> dict[str, Any]:
    base_url = str(params.get("mcp_base_url") or "").strip()
    if not base_url:
        return {"status": "BLOCKED", "reason": "mcp_base_url is not configured"}
    try:
        from plugins.echomem_mcp.mcp_client import McpClient

        client = McpClient(base_url, auth_key=tenant.auth_key,
                           timeout_s=float(params.get("mcp_timeout_s", 120)))
        client.initialize()
        arguments = dict(params.get("mcp_add_memory_arguments") or {})
        arguments.setdefault(str(params.get("mcp_content_field") or "user_message"), content)
        arguments.setdefault("session_id", f"stress-{uuid.uuid4().hex}")
        arguments.setdefault("client_turn_id", uuid.uuid4().hex)
        started = time.perf_counter()
        result = client.call_tool(
            str(params.get("mcp_add_memory_tool") or "add_memory"), arguments
        )
        evidence = {"status": INCONCLUSIVE, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "result_nonempty": bool(result), "persistence_verified": False,
                    "expected_chars": len(content),
                    "expected_sha256": hashlib.sha256(content.encode('utf-8')).hexdigest()}
        if verifier is None:
            return {**evidence, "reason_code": "HISTORY_VERIFIER_NOT_CONFIGURED"}
        history = verifier.get_history(arguments["session_id"])
        evidence["history"] = _safe_result(history)
        if not evidence["history"]["accepted"]:
            return {**evidence, "reason_code": "HISTORY_READ_FAILED"}
        def contents(value):
            if isinstance(value, dict):
                if value.get("role") == "user" and isinstance(value.get("content"), str):
                    yield value["content"]
                for child in value.values():
                    yield from contents(child)
            elif isinstance(value, list):
                for child in value:
                    yield from contents(child)
        messages = list(contents(history.payload))
        matched = any(message == content for message in messages)
        return {**evidence, "status": PASS if matched else FAIL,
                "persistence_verified": matched, "history_user_messages": len(messages),
                "reason_code": "FULL_CONTENT_MATCH" if matched else "FULL_CONTENT_MISMATCH"}
    except Exception as exc:  # noqa: BLE001 - MCP failures are classified, never hidden.
        message = str(exc)
        status_match = re.search(r"MCP HTTP (\d{3})\b", message)
        return {"status": "FAIL", "error_type": type(exc).__name__,
                "http_status": int(status_match.group(1)) if status_match else None,
                "reason_code": "INVALID_HOST" if "Invalid Host header" in message else
                    "TOOL_ARGUMENT_ERROR" if "validation error" in message.lower() else "MCP_REQUEST_FAILED"}


def run(ctx: Ctx) -> None:
    params = ctx.params
    try:
        tenant = load_tenant_specs(Path(str(params.get("tenant_config") or "")), tenant_count=1)[0]
    except (OSError, ValueError, IndexError) as exc:
        ctx.check("payload-boundary", status=INCONCLUSIVE,
                  reason=f"tenant config unavailable: {type(exc).__name__}")
        return

    timeout_s = max(1.0, float(params.get("timeout_s", 60)))
    client = EchoMemHTTP(
        ctx.base_url, tenant.auth_key, timeout_s=timeout_s,
        tenant_id=tenant.tenant_id, user_id=tenant.user_id,
        account_id=tenant.account_id, agent_id=tenant.agent_id,
        auth_header=str(params.get("auth_header") or "X-Auth-Key"),
    )
    sizes = sorted({max(0, int(value)) for value in params.get(
        "sizes_bytes", [0, 1, 1024, 65536, 262144, 524288, 1048576]
    )})
    rows: list[dict[str, Any]] = []
    pending_searches = []

    for size in sizes:
        try:
            session_id, _ = client.open_session(tenant.tenant_id, f"payload-{size}")
        except Exception as exc:
            for api, encoding in (("message", "text"), ("search", "text"), ("commit", "text/plain"),
                                  ("message", "binary"), ("commit", "binary"), ("search", "binary")):
                rows.append({"api": api, "encoding": encoding, "content_bytes": size,
                             "wire_bytes": None, "http_status": None, "elapsed_ms": None,
                             "accepted": False, "dispatched": False,
                             "reason_code": "SESSION_SETUP_FAILED", "transport_error_type": type(exc).__name__})
            continue
        content = _natural_payload(size, f"boundary-{size}-byte message")
        message_body = {"role": "user", "content": content,
                        "metadata": {"stress_message_id": f"size-{size}"}}
        message_raw = _json_bytes(message_body)
        message = client.request_bytes(
            "POST", f"/api/sessions/{session_id}/messages", message_raw,
            content_type="application/json", timeout_s=timeout_s,
        )
        rows.append({"api": "message", "encoding": "text", "content_bytes": size,
                     "wire_bytes": len(message_raw), **_safe_result(message)})

        search_body = {"query": content, "agent_id": tenant.agent_id,
                       "session_id": session_id, "limit": 10}
        search_raw = _json_bytes(search_body)

        # Commit is a control API, not a text-storage API. Test raw textual
        # bodies separately from the valid long-message -> Commit sequence.
        commit_text = client.request_bytes(
            "POST", f"/api/sessions/{session_id}/commit", content.encode("utf-8"),
            content_type="text/plain", timeout_s=timeout_s,
        )
        rows.append({"api": "commit", "encoding": "text/plain", "content_bytes": size,
                     "wire_bytes": size, **_safe_result(commit_text)})

        for api, path in (
            ("message", f"/api/sessions/{session_id}/messages"),
            ("commit", f"/api/sessions/{session_id}/commit"),
            ("search", "/api/retrieval/search"),
        ):
            binary = client.request_bytes(
                "POST", path, b"\x00" * size,
                content_type="application/octet-stream", timeout_s=timeout_s,
            )
            rows.append({"api": api, "encoding": "binary", "content_bytes": size,
                         "wire_bytes": size, **_safe_result(binary)})

        pending_searches.append((size, search_raw))

    commit_sizes = sorted({max(1, int(value)) for value in params.get(
        "commit_content_sizes", [params.get("commit_content_chars", 1048576)]
    )})
    chunk_chars = max(1, int(params.get("commit_chunk_chars", 262144)))
    skip_commit = bool(params.get("skip_long_commit", False))
    skip_mcp = bool(params.get("skip_mcp", False))
    mcp_chars = max(1, int(params.get("mcp_add_memory_chars", 1048576)))
    mcp = ({"status": "NOT_SELECTED"} if skip_mcp else _mcp_add_memory(
        params, tenant, _natural_payload(mcp_chars, "MCP add_memory"), verifier=client
    ))
    long_commits = ([{"requested_chars": size, "status": "NOT_SELECTED"} for size in commit_sizes]
                    if skip_commit else
                    [_long_commit(client, tenant.tenant_id, size, chunk_chars,
                                  float(params.get("commit_timeout_s", 600)))
                     for size in commit_sizes])
    # Keep the largest result under the old key for existing report consumers.
    long_commit = long_commits[-1]

    # Finish protocol checks and write evidence before a long Search can stall the instance.
    for size, search_raw in pending_searches:
        search = client.request_bytes("POST", "/api/retrieval/search", search_raw,
                                      content_type="application/json", timeout_s=timeout_s)
        rows.append({"api": "search", "encoding": "text", "content_bytes": size,
                     "wire_bytes": len(search_raw), **_safe_result(search)})
    recovery = _post_boundary_search(client, tenant.tenant_id, timeout_s)
    for row in rows:
        row["outcome"] = _case_outcome(row)
    detail = {
        "sizes_bytes": sizes,
        "cases_total": len(rows),
        "cases_dispatched": sum(row.get("dispatched", True) for row in rows),
        "cases": rows,
        "long_commit": long_commit,
        "long_commits": long_commits,
        "mcp_add_memory": {"requested_chars": mcp_chars, **mcp},
        "post_boundary_search": recovery,
        "outcome_counts": {outcome: sum(row["outcome"] == outcome for row in rows)
                           for outcome in sorted({row["outcome"] for row in rows})},
    }
    transport_failures = sum(row["http_status"] is None for row in rows)
    binary_accepted = sum(
        row["encoding"] == "binary" and row["content_bytes"] > 0 and row["accepted"]
        for row in rows
    )
    commit_completed = skip_commit or all(
        row.get("accepted_chars") == row.get("requested_chars")
        and (row.get("terminal") or {}).get("accepted")
        and (row.get("terminal") or {}).get("state") in {"completed", "done", "success"}
        for row in long_commits
    )
    commit_semantically_verified = skip_commit or all(
        row.get("semantic_readback", {}).get("status") == PASS for row in long_commits
    )
    server_errors = sum(row["outcome"] == "SERVER_ERROR" for row in rows)
    blocked_cases = sum(row["outcome"] in {"AUTH_OR_ENDPOINT_BLOCKED", "RATE_LIMITED", "UNEXPECTED_STATUS"}
                        for row in rows)
    if (server_errors or binary_accepted or mcp["status"] == "FAIL"
            or any((row.get("terminal") or {}).get("state") in {"failed", "error"}
                   for row in long_commits)):
        status = FAIL
    elif (blocked_cases or transport_failures or (not skip_mcp and mcp["status"] != "PASS")
          or not commit_completed or not commit_semantically_verified or not recovery["accepted"]):
        status = INCONCLUSIVE
    else:
        status = PASS
    ctx.check(
        "payload-boundary",
        status=status,
        reason=(f"planned {len(rows)} exact-body cases; dispatched={detail['cases_dispatched']}; transport/setup failures={transport_failures}; "
                f"server errors={server_errors}; blocked cases={blocked_cases}; binary accepted={binary_accepted}; long Commit states="
                f"{[str((row.get('terminal') or {}).get('state') or row.get('status') or 'unknown') for row in long_commits]}; "
                f"MCP={mcp['status']}; long Commit readback={[(row.get('semantic_readback') or {}).get('status', 'NOT_RUN') for row in long_commits]}; "
                f"post-boundary Search HTTP={recovery['http_status']}"),
        detail=json.dumps(detail, ensure_ascii=False),
    )


def _post_boundary_search(client, tenant_id: str, timeout_s: float) -> dict[str, Any]:
    """A healthy small Search after malformed/oversized traffic proves recovery."""
    try:
        session_id, _ = client.open_session(tenant_id, "post-boundary-recovery")
    except Exception as exc:  # noqa: BLE001 - report a classified setup failure.
        return {"accepted": False, "http_status": None, "error_type": type(exc).__name__}
    body = {
        "query": "Retrieve the post-boundary health check.",
        "agent_id": str(client.agent_id or ""),
        "session_id": session_id,
        "limit": 10,
    }
    result = client.request_bytes(
        "POST", "/api/retrieval/search", _json_bytes(body),
        content_type="application/json", timeout_s=timeout_s,
    )
    return {"session_created": bool(session_id), **_safe_result(result)}


def _long_commit(client, tenant_id, commit_chars, chunk_chars, timeout_s):
    try:
        commit_session, _ = client.open_session(tenant_id, "oversized-commit")
    except Exception as exc:
        return {"requested_chars": commit_chars, "accepted_chars": 0, "all_content_accepted": False,
                "reason_code": "SESSION_SETUP_FAILED", "error_type": type(exc).__name__, "terminal": {}}
    add_results = []
    content = _natural_payload(commit_chars, "long Commit")
    remaining = len(content)
    accepted_chars = 0
    offset = 0
    while remaining > 0:
        chunk = content[offset:offset + min(chunk_chars, remaining)]
        response = client.add_message(commit_session, uuid.uuid4().hex, chunk)
        add_results.append(_safe_result(response))
        remaining -= len(chunk)
        if response.status_code is None or not 200 <= response.status_code < 300:
            break
        accepted_chars += len(chunk)
        offset += len(chunk)
    commit = client.commit(commit_session, idempotency_key=f"stress-{uuid.uuid4().hex}")
    archive_id = extract_archive(commit.payload)
    terminal = _poll_commit(client, commit_session, archive_id, timeout_s) if archive_id else {}
    semantic_readback = _long_commit_readback(
        client, commit_session, timeout_s,
        enabled=(terminal.get("accepted") and terminal.get("state") in {"completed", "done", "success"}),
    )
    return {
        "requested_chars": commit_chars,
        "accepted_chars": accepted_chars,
        "all_content_accepted": accepted_chars == commit_chars,
        "chunks_attempted": len(add_results),
        "chunks_accepted": sum(bool(row["accepted"]) for row in add_results),
        "submit": _safe_result(commit),
        "archive_id_present": bool(archive_id),
        "terminal": terminal,
        "semantic_readback": semantic_readback,
    }


def _long_commit_readback(client, session_id: str, timeout_s: float, *, enabled: bool) -> dict[str, Any]:
    """Verify facts at the first, middle and last portions after Commit completes."""
    if not enabled:
        return {"status": "NOT_RUN", "reason_code": "COMMIT_NOT_COMPLETED", "samples": []}
    samples = []
    for position, expected, query in _LONG_FACTS:
        result = client.search(session_id, query, min(timeout_s, 60.0))
        contract = {"id": f"long-commit-{position}", "query_type": "recall",
                    "aliases": [expected], "match_policy": "all"}
        quality = assess_retrieval(result.payload, contract)
        samples.append({"position": position, "http_status": result.status_code,
                        "elapsed_ms": round(result.elapsed_s * 1000, 3),
                        "quality_ok": bool(result.status_code == 200 and quality["quality_ok"]),
                        "reason_code": result.reason_code,
                        "transport_error_type": result.transport_error_type})
    return {"status": PASS if all(sample["quality_ok"] for sample in samples) else FAIL,
            "samples": samples}
