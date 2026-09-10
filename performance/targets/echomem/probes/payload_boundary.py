"""真实 API 与 MCP 请求体边界、超长 Message/Commit 探针。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes._client import (
    EchoMemHTTP,
    extract_archive,
    load_tenant_specs,
    status_from,
)

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


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


def _poll_commit(client: EchoMemHTTP, session_id: str, archive_id: str,
                 timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.commit_status(session_id, archive_id)
        state = status_from(response.payload)
        last = {**_safe_result(response), "state": state}
        if state in {"completed", "done", "success", "failed", "error"}:
            break
        time.sleep(0.5)
    return last


def _mcp_add_memory(params: dict[str, Any], tenant: Any, content: str) -> dict[str, Any]:
    base_url = str(params.get("mcp_base_url") or "").strip()
    if not base_url:
        return {"status": "BLOCKED", "reason": "mcp_base_url is not configured"}
    try:
        from plugins.echomem_mcp.mcp_client import McpClient

        client = McpClient(base_url, auth_key=tenant.auth_key,
                           timeout_s=float(params.get("mcp_timeout_s", 120)))
        client.initialize()
        arguments = dict(params.get("mcp_add_memory_arguments") or {})
        arguments.setdefault("content", content)
        arguments.setdefault("session_id", f"stress-{uuid.uuid4().hex}")
        started = time.perf_counter()
        result = client.call_tool(
            str(params.get("mcp_add_memory_tool") or "add_memory"), arguments
        )
        return {"status": "PASS", "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "result_nonempty": bool(result)}
    except Exception as exc:  # noqa: BLE001 - MCP failures are classified, never hidden.
        return {"status": "FAIL", "error_type": type(exc).__name__}


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
    session_id, _ = client.open_session(tenant.tenant_id, "payload-boundary")
    rows: list[dict[str, Any]] = []

    for size in sizes:
        content = "x" * size
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
        search = client.request_bytes(
            "POST", "/api/retrieval/search", search_raw,
            content_type="application/json", timeout_s=timeout_s,
        )
        rows.append({"api": "search", "encoding": "text", "content_bytes": size,
                     "wire_bytes": len(search_raw), **_safe_result(search)})

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

    commit_chars = max(1, int(params.get("commit_content_chars", 1048576)))
    chunk_chars = max(1, int(params.get("commit_chunk_chars", 262144)))
    commit_session, _ = client.open_session(tenant.tenant_id, "oversized-commit")
    add_results = []
    remaining = commit_chars
    while remaining > 0:
        chunk = "c" * min(chunk_chars, remaining)
        response = client.add_message(commit_session, uuid.uuid4().hex, chunk)
        add_results.append(_safe_result(response))
        remaining -= len(chunk)
        if response.status_code is None or response.status_code >= 400:
            break
    commit = client.commit(commit_session, idempotency_key=f"stress-{uuid.uuid4().hex}")
    archive_id = extract_archive(commit.payload)
    terminal = _poll_commit(client, commit_session, archive_id,
                            float(params.get("commit_timeout_s", 600))) if archive_id else {}
    long_commit = {
        "requested_chars": commit_chars,
        "chunks_attempted": len(add_results),
        "chunks_accepted": sum(bool(row["accepted"]) for row in add_results),
        "submit": _safe_result(commit),
        "archive_id_present": bool(archive_id),
        "terminal": terminal,
    }

    mcp_chars = max(1, int(params.get("mcp_add_memory_chars", 1048576)))
    mcp = _mcp_add_memory(params, tenant, "m" * mcp_chars)
    detail = {
        "sizes_bytes": sizes,
        "cases_total": len(rows),
        "cases": rows,
        "long_commit": long_commit,
        "mcp_add_memory": {"requested_chars": mcp_chars, **mcp},
    }
    transport_failures = sum(row["http_status"] is None for row in rows)
    binary_accepted = sum(
        row["encoding"] == "binary" and row["content_bytes"] > 0 and row["accepted"]
        for row in rows
    )
    commit_completed = terminal.get("state") in {"completed", "done", "success"}
    if binary_accepted or mcp["status"] == "FAIL":
        status = FAIL
    elif transport_failures or mcp["status"] == "BLOCKED" or not commit_completed:
        status = INCONCLUSIVE
    else:
        status = PASS
    ctx.check(
        "payload-boundary",
        status=status,
        reason=(f"observed {len(rows)} exact-body cases; transport failures={transport_failures}; "
                f"binary accepted={binary_accepted}; long commit state={terminal.get('state') or 'unknown'}; "
                f"MCP={mcp['status']}"),
        detail=json.dumps(detail, ensure_ascii=False),
    )
