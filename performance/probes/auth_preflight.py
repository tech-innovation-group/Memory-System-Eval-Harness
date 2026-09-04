"""Preflight real tenant identities before a stress suite starts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from performance.probes._client import load_tenant_specs


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""


def open_session(
    base_url: str,
    tenant: Any,
    *,
    timeout_s: float,
    auth_header: str = "X-Auth-Key",
) -> dict[str, Any]:
    body = {
        "agent_id": tenant.agent_id,
        "metadata": {
            "title": "stress-auth-preflight",
            "account_id": tenant.account_id or tenant.tenant_id,
            "user_id": tenant.user_id or f"stress-{tenant.tenant_id}",
            "tenant_id": tenant.tenant_id,
        },
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if auth_header.lower() == "authorization":
        headers["Authorization"] = (
            tenant.auth_key
            if tenant.auth_key.lower().startswith("bearer ")
            else f"Bearer {tenant.auth_key}"
        )
    else:
        headers[auth_header] = tenant.auth_key
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/sessions/open",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            return {
                "tenant_id": tenant.tenant_id,
                "auth_key_sha256_12": key_fingerprint(tenant.auth_key),
                "http_status": response.status,
                "elapsed_s": round(time.monotonic() - started, 3),
                "session_opened": response.status < 300,
                "error": "",
                "response_status": str(
                    payload.get("status") or payload.get("state") or ""
                ) if isinstance(payload, dict) else "",
            }
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except OSError:
            raw = ""
        reason = ""
        try:
            payload = json.loads(raw) if raw else {}
            if isinstance(payload, dict):
                reason = str(
                    payload.get("detail")
                    or payload.get("message")
                    or payload.get("error")
                    or payload.get("code")
                    or ""
                )[:300]
        except json.JSONDecodeError:
            reason = raw[:300]
        return {
            "tenant_id": tenant.tenant_id,
            "auth_key_sha256_12": key_fingerprint(tenant.auth_key),
            "http_status": exc.code,
            "elapsed_s": round(time.monotonic() - started, 3),
            "session_opened": False,
            "error": f"HTTP {exc.code}" + (f": {reason}" if reason else ""),
        }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {
            "tenant_id": tenant.tenant_id,
            "auth_key_sha256_12": key_fingerprint(tenant.auth_key),
            "http_status": None,
            "elapsed_s": round(time.monotonic() - started, 3),
            "session_opened": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run(
    base_url: str,
    tenant_config: str | Path,
    *,
    timeout_s: float = 5.0,
    tenant_count: int = 0,
    auth_header: str = "X-Auth-Key",
) -> dict[str, Any]:
    tenants = load_tenant_specs(tenant_config, tenant_count=tenant_count)
    # Authentication is an independent read-only check per tenant. Running
    # these checks concurrently keeps a 32-tenant preflight bounded by the
    # slowest credential instead of the sum of all credential latencies.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tenants)))) as executor:
        results = list(
            executor.map(
                lambda tenant: open_session(
                    base_url, tenant, timeout_s=timeout_s, auth_header=auth_header
                ),
                tenants,
            )
        )
    failed = [item for item in results if not item["session_opened"]]
    passed = [item for item in results if item["session_opened"]]
    return {
        "created_at": now(),
        "base_url": base_url,
        "real_http": True,
        "status": "PASS" if not failed else "PARTIAL" if passed else "ENVIRONMENT_ERROR",
        "tenant_count": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "usable_tenant_ids": [item["tenant_id"] for item in passed],
        "results": results,
        "reason": (
            "all selected tenant credentials can open a real session"
            if not failed
            else (
                f"{len(passed)}/{len(results)} tenant credentials can open a real session; "
                "only scenarios within the usable tenant count may run"
                if passed
                else "tenant authentication/configuration failed before workload started"
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="EchoMem tenant auth preflight")
    parser.add_argument("--base-url", default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--tenant-config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout-s", type=float, default=5.0)
    parser.add_argument("--tenant-count", type=int, default=0)
    parser.add_argument("--auth-header", default="X-Auth-Key")
    args = parser.parse_args()
    result = run(
        args.base_url,
        args.tenant_config,
        timeout_s=max(0.1, args.timeout_s),
        tenant_count=max(0, args.tenant_count),
        auth_header=args.auth_header,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "tenant_count", "passed", "failed", "reason")}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
