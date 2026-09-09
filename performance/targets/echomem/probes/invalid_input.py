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
        ("malformed_json", "POST", "/api/retrieval/search", b"{not-json", json_headers, {400, 422}),
        ("missing_search_fields", "POST", "/api/retrieval/search", b"{}", json_headers, {400, 422}),
        ("invalid_open_metadata", "POST", "/api/sessions/open",
         json.dumps({"agent_id": tenant.agent_id, "metadata": []}).encode(), json_headers, {400, 422}),
        ("unknown_session", "GET", "/api/sessions/__stress_missing__/history?limit=1",
         None, json_headers, {400, 404}),
    ]
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
