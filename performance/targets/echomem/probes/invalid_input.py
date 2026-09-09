"""Exercise EchoMem negative HTTP contracts without mixing them into load metrics."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes._client import load_tenant_specs


def _call(base_url: str, method: str, path: str, *, body: bytes | None,
          headers: dict[str, str], timeout_s: float) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(base_url.rstrip("/") + path, data=body,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response.read()
            return {"http_status": response.status, "elapsed_s": time.monotonic() - started}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return {"http_status": exc.code, "elapsed_s": time.monotonic() - started,
                "error_code": str(payload.get("error_code") or payload.get("code") or payload.get("error") or "")[:128]}
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {"http_status": None, "elapsed_s": time.monotonic() - started,
                "transport_error": type(exc).__name__}


def run(ctx: Ctx) -> None:
    params = ctx.params
    specs = load_tenant_specs(Path(str(params.get("tenant_config") or "")), tenant_count=1)
    if not specs:
        ctx.check("invalid-input", status="INCONCLUSIVE", reason="no tenant credential")
        return
    tenant = specs[0]
    timeout_s = max(0.1, float(params.get("timeout_s", 10)))
    auth_header = str(params.get("auth_header") or "X-Auth-Key")
    json_headers = {"Content-Type": "application/json", "Accept": "application/json",
                    auth_header: tenant.auth_key}
    cases = [
        ("missing_auth", "POST", "/api/retrieval/search", b"{}",
         {"Content-Type": "application/json"}, {401, 403, 404}),
        ("invalid_auth", "POST", "/api/retrieval/search", b"{}",
         {"Content-Type": "application/json", auth_header: "invalid-stress-key"}, {401, 403}),
        ("malformed_json", "POST", "/api/retrieval/search", b"{not-json", json_headers, {400, 422}),
        ("missing_search_fields", "POST", "/api/retrieval/search", b"{}", json_headers, {400, 422}),
        ("negative_search_limit", "POST", "/api/retrieval/search",
         json.dumps({"query": "boundary", "agent_id": tenant.agent_id, "limit": -1}).encode(),
         json_headers, {400, 422}),
        ("invalid_open_metadata", "POST", "/api/sessions/open",
         json.dumps({"agent_id": tenant.agent_id, "metadata": []}).encode(), json_headers, {400, 422}),
        ("unknown_session_message", "POST", "/api/sessions/__stress_missing__/messages",
         json.dumps({"role": "user", "content": "boundary"}).encode(), json_headers, {400, 404}),
        ("unknown_session_commit", "POST", "/api/sessions/__stress_missing__/commit",
         json.dumps({"metadata": {}}).encode(), json_headers, {400, 404}),
        ("unknown_commit_status", "GET",
         "/api/sessions/__stress_missing__/commits/__stress_missing__",
         None, json_headers, {400, 404}),
        ("unknown_commit_memories", "GET",
         "/api/sessions/__stress_missing__/commits/__stress_missing__/memories",
         None, json_headers, {400, 404}),
        ("unknown_session_history", "GET", "/api/sessions/__stress_missing__/history?limit=1",
         None, json_headers, {400, 404}),
        ("unknown_session_archives", "GET", "/api/sessions/__stress_missing__/archives?limit=1",
         None, json_headers, {400, 404}),
        ("missing_fs_uri", "GET", "/fs/read", None, json_headers, {400, 404, 422}),
        ("fault_control_without_token", "GET", "/api/inspect/test-control/fault",
         None, {"Accept": "application/json"}, {401, 403, 404}),
        ("tenant_observability_without_token", "GET", "/api/inspect/tenant-observability",
         None, {"Accept": "application/json"}, {401, 403, 404}),
    ]
    for name, value in (("array", []), ("null", None), ("string", "invalid")):
        cases.append((f"nonobject_json_{name}", "POST", "/api/retrieval/search",
                      json.dumps(value).encode(), json_headers, {400, 422}))
    invalid_fields = [
        ("query_null", "query", None), ("query_number", "query", 12),
        ("query_array", "query", []), ("query_object", "query", {}),
        ("query_bool", "query", True), ("query_empty", "query", ""),
        ("limit_zero", "limit", 0), ("limit_bool", "limit", True),
        ("limit_string", "limit", "1"), ("limit_fraction", "limit", 1.5),
        ("timeout_zero", "timeout_ms", 0), ("timeout_negative", "timeout_ms", -1),
        ("timeout_bool", "timeout_ms", True), ("timeout_string", "timeout_ms", "1"),
        ("timeout_overflow", "timeout_ms", 2_147_483_648),
        ("input_association_string", "input_association", "true"),
    ]
    for name, field, value in invalid_fields:
        body = {"query": "boundary", "agent_id": tenant.agent_id, field: value}
        cases.append((name, "POST", "/api/retrieval/search",
                      json.dumps(body).encode(), json_headers, {400, 422}))
    for name, value in (("array", [1]), ("pairs", [["key", "value"]]),
                        ("string", "invalid"), ("number", 0), ("bool", False)):
        body = {"agent_id": tenant.agent_id, "metadata": value}
        cases.append((f"open_metadata_{name}", "POST", "/api/sessions/open",
                      json.dumps(body).encode(), json_headers, {400, 422}))
    token = os.getenv(str(params.get("token_env") or "ECHOMEM_TEST_CONTROL_TOKEN"), "")
    if token:
        cases.append(("invalid_fault_type", "POST", "/api/inspect/test-control/fault",
                      json.dumps({"action": "enable", "tenant_id": tenant.tenant_id,
                                  "fault_type": "invalid"}).encode(),
                      {"Content-Type": "application/json", "X-EchoMem-Test-Token": token}, {400, 422}))
    results = []
    for name, method, path, body, headers, expected in cases:
        result = _call(ctx.base_url, method, path, body=body, headers=headers, timeout_s=timeout_s)
        code = result.get("http_status")
        results.append({"case": name, "method": method, "path": path,
                        "expected_status": sorted(expected), "accepted_invalid_input": code is not None and 200 <= code < 300,
                        "contract_ok": code in expected, **result})
    passed = sum(row["contract_ok"] for row in results)
    status = "PASS" if passed == len(results) else "FAIL" if all(row["http_status"] is not None for row in results) else "INCONCLUSIVE"
    ctx.check("invalid-input", status=status,
              reason=f"negative contracts matched {passed}/{len(results)}",
              detail=json.dumps({"expected_cases": len(results), "observed_cases": len(results),
                                 "passed_cases": passed, "cases": results}, ensure_ascii=False))
