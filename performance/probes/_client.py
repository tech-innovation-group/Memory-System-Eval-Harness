"""EchoMem HTTP 客户端与探针共享契约。

stress 探针（限流/并发/故障/恢复/对账）共用同一份真实 HTTP 客户端与
tenant 规格解析，保证各探针在同一套鉴权与响应契约下运行。仅使用标准库。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

@dataclass
class HttpResult:
    method: str
    path: str
    status_code: int | None
    elapsed_s: float
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retry_after_s: float | None = None
    admission_wait_s: float = 0.0
    admission_queue_depth: int = 0
    admission_order: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    server_received_at: str = ""
    server_queue_entered_at: str = ""
    server_execution_started_at: str = ""
    server_finished_at: str = ""
    server_queue_depth: int | None = None
    server_active_workers: int | None = None
    server_terminal_status: str = ""
    reason_code: str = ""

    @property
    def request_id(self) -> str:
        for key in ("x-request-id", "x-correlation-id", "request-id"):
            if self.headers.get(key):
                return self.headers[key]
        for key in ("request_id", "requestId", "trace_id", "traceId"):
            value = self.payload.get(key) if isinstance(self.payload, dict) else None
            if value:
                return str(value)
        return ""

def _nested_observability(payload: dict[str, Any]) -> dict[str, Any]:
    """Find optional server timing data without assuming one response schema."""
    candidates: list[dict[str, Any]] = [payload]
    for key in ("telemetry", "observability", "debug", "meta", "metadata", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    merged: dict[str, Any] = {}
    for candidate in candidates:
        for key, value in candidate.items():
            merged.setdefault(str(key), value)
    return merged


def _server_observability(
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Extract optional server-side queue evidence from payloads or headers."""
    nested = _nested_observability(payload)
    aliases: dict[str, tuple[str, ...]] = {
        "server_received_at": (
            "received_at", "server_received_at", "serverReceivedAt",
            "x_received_at", "x-server-received-at",
        ),
        "server_queue_entered_at": (
            "queue_entered_at", "server_queue_entered_at", "queueEnteredAt",
            "x_queue_entered_at", "x-server-queue-entered-at",
        ),
        "server_execution_started_at": (
            "execution_started_at", "server_execution_started_at",
            "executionStartedAt", "started_at_server", "x-server-execution-started-at",
        ),
        "server_finished_at": (
            "finished_at", "server_finished_at", "finishedAt",
            "x_finished_at", "x-server-finished-at",
        ),
        "server_queue_depth": (
            "queue_depth", "server_queue_depth", "queueDepth", "x-queue-depth",
        ),
        "server_active_workers": (
            "active_workers", "server_active_workers", "activeWorkers",
            "x-active-workers",
        ),
        "server_terminal_status": (
            "terminal_status", "server_terminal_status", "terminalStatus",
        ),
        "reason_code": (
            "reason_code", "reasonCode", "error_code", "errorCode",
            "x-reason-code",
        ),
    }
    result: dict[str, Any] = {}
    for target, names in aliases.items():
        value: Any = None
        for name in names:
            if name in nested:
                value = nested[name]
                break
            header_name = name.lower()
            if header_name in headers:
                value = headers[header_name]
                break
        if value is None or value == "":
            continue
        if target in {"server_queue_depth", "server_active_workers"}:
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                continue
        result[target] = str(value) if target.startswith("server_") and target not in {
            "server_queue_depth", "server_active_workers"
        } else value
    return result


@dataclass
class TenantSpec:
    """One independently authenticated tenant for a real isolation run."""

    tenant_id: str
    auth_key: str
    user_id: str = ""
    account_id: str = ""
    agent_id: str = "echomem-stress"
    auth_key_source: str = "explicit"


def load_tenant_specs(
    path: str | Path,
    environ: dict[str, str] | None = None,
    tenant_count: int = 0,
) -> list[TenantSpec]:
    """Load tenant identities while keeping credentials out of report data."""
    env = environ if environ is not None else os.environ
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    entries = payload.get("tenants") if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise ValueError("tenant config must contain a non-empty tenants list")
    if tenant_count > 0:
        entries = entries[:tenant_count]
    specs: list[TenantSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"tenant config entry {index} must be an object")
        tenant_id = str(item.get("tenant_id") or item.get("id") or "").strip()
        if not tenant_id:
            raise ValueError(f"tenant config entry {index} has no tenant_id")
        if tenant_id in seen:
            raise ValueError(f"duplicate tenant_id: {tenant_id}")
        seen.add(tenant_id)
        key_env = str(item.get("auth_key_env") or "").strip()
        auth_key = str(item.get("auth_key") or "").strip()
        source = "explicit"
        if key_env:
            auth_key = str(env.get(key_env) or "").strip()
            source = f"env:{key_env}"
        if not auth_key:
            raise ValueError(
                f"tenant {tenant_id} has no auth key; set {key_env or 'auth_key'}"
            )
        specs.append(
            TenantSpec(
                tenant_id=tenant_id,
                auth_key=auth_key,
                user_id=str(item.get("user_id") or "").strip(),
                account_id=str(item.get("account_id") or tenant_id).strip() or tenant_id,
                agent_id=str(item.get("agent_id") or "echomem-stress").strip(),
                auth_key_source=source,
            )
        )
    return specs
class EchoMemHTTP:
    def __init__(
        self,
        base_url: str,
        auth_key: str = "",
        timeout_s: float = 60.0,
        *,
        tenant_id: str = "",
        user_id: str = "",
        account_id: str = "",
        agent_id: str = "echomem-stress",
        admission: Any | None = None,
        auth_header: str = "X-Auth-Key",
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_key = auth_key
        self.timeout_s = timeout_s
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.account_id = account_id or tenant_id
        self.agent_id = agent_id
        self.admission = admission
        self.auth_header = auth_header
        self.last_identity: dict[str, Any] = {}

    def _request_raw(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> HttpResult:
        started = time.monotonic()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_key:
            if self.auth_header.lower() == "authorization":
                headers["Authorization"] = (
                    self.auth_key
                    if self.auth_key.lower().startswith("bearer ")
                    else f"Bearer {self.auth_key}"
                )
            else:
                headers[self.auth_header] = self.auth_key
        data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s or self.timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {"raw": raw[:10000]}
                headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                observability = _server_observability(payload, headers)
                return HttpResult(
                    method, path, response.status, time.monotonic() - started,
                    payload, headers=headers, **observability,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw[:1000]}
            retry_after = exc.headers.get("Retry-After")
            try:
                retry_after_value = float(retry_after) if retry_after else None
            except ValueError:
                retry_after_value = None
            headers = {
                str(key).lower(): str(value)
                for key, value in exc.headers.items()
            }
            observability = _server_observability(payload, headers)
            return HttpResult(
                method, path, exc.code, time.monotonic() - started,
                payload, f"HTTP {exc.code}", retry_after_value, headers=headers,
                **observability,
            )
        except Exception as exc:  # transport errors are environment errors at scenario level
            return HttpResult(method, path, None, time.monotonic() - started, {}, f"{type(exc).__name__}: {exc}")

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        *,
        operation: str = "setup",
    ) -> HttpResult:
        tenant = self.tenant_id or self.account_id or "default"
        wait_s = queue_depth = order = 0
        if self.admission and operation in {"commit", "search"}:
            wait_s, queue_depth, order = self.admission.acquire(operation, tenant)
        try:
            result = self._request_raw(method, path, body, timeout_s)
        finally:
            if self.admission and operation in {"commit", "search"}:
                self.admission.release(operation, tenant)
        result.admission_wait_s = wait_s
        result.admission_queue_depth = queue_depth
        result.admission_order = order
        return result

    def setup_request_with_retry(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        max_attempts: int = 5,
        backoff_s: float = 1.0,
        max_backoff_s: float = 15.0,
    ) -> HttpResult:
        """Retry setup-only HTTP 429 responses without hiding workload 429s.

        Session/message provisioning is a precondition for a scenario. A
        server-side tenant limiter can legitimately reject a burst there, so
        honor Retry-After before declaring the environment unusable. The
        returned result is always the final attempt; workload calls continue
        to use request() directly and preserve every rejection in results.
        """
        limit = max(1, int(max_attempts))
        for attempt in range(limit):
            result = self.request(method, path, body)
            if result.status_code != 429 or attempt + 1 >= limit:
                return result
            retry_after = result.retry_after_s
            delay = (
                max(0.0, float(retry_after))
                if retry_after is not None
                else min(max(0.0, backoff_s) * (2**attempt), max_backoff_s)
            )
            if delay:
                time.sleep(delay)
        raise AssertionError("unreachable setup retry loop")

    def health(self) -> HttpResult:
        return self.request("GET", "/health")

    def service_diagnosis(self) -> str:
        """Return a safe endpoint diagnosis for common wrong-port failures."""
        response = self._request_raw("GET", "/api-doc/openapi.json", timeout_s=5.0)
        payload = response.payload if isinstance(response.payload, dict) else {}
        title = str((payload.get("info") or {}).get("title") or "").strip()
        if title:
            return f"openapi title={title}"
        if response.status_code == 404:
            return "no /api-doc/openapi.json"
        return f"openapi status={response.status_code or 'transport_error'}"

    def open_session(
        self,
        tenant: str,
        session_name: str,
        *,
        retry_rate_limit: bool = True,
    ) -> tuple[str, str]:
        request = self.setup_request_with_retry if retry_rate_limit else self.request
        result = request("POST", "/api/sessions/open", {
            "agent_id": self.agent_id,
            "metadata": {
                "title": session_name,
                "account_id": self.account_id or tenant,
                "user_id": self.user_id or f"stress-{tenant}",
                "tenant_id": self.tenant_id or tenant,
            },
        })
        if result.status_code is None or result.status_code >= 400:
            diagnosis = ""
            if result.status_code == 404:
                try:
                    diagnosis = f"; endpoint diagnosis: {self.service_diagnosis()}"
                except Exception as exc:
                    diagnosis = f"; endpoint diagnosis unavailable: {type(exc).__name__}"
            raise RuntimeError(
                f"open session failed for {tenant}: "
                f"{result.error or result.payload}{diagnosis}"
            )
        scope = result.payload.get("scope") or {}
        identity: dict[str, Any] = {}
        for key in (
            "tenant_id",
            "account_id",
            "workspace_id",
            "user_id",
            "organization_id",
            "scope",
        ):
            value = result.payload.get(key)
            if value in (None, ""):
                value = scope.get(key) if isinstance(scope, dict) else None
            if value not in (None, ""):
                identity[key] = value
        self.last_identity = identity
        session_id = result.payload.get("session_id") or result.payload.get("id") or scope.get("session_id")
        if not session_id:
            raise RuntimeError(f"open session returned no session_id: {result.payload}")
        return str(session_id), tenant

    def add_message(
        self,
        session_id: str,
        message_id: str,
        content: str,
        *,
        retry_rate_limit: bool = False,
    ) -> HttpResult:
        request = self.setup_request_with_retry if retry_rate_limit else self.request
        return request("POST", f"/api/sessions/{session_id}/messages", {
            "role": "user", "content": content, "metadata": {"stress_message_id": message_id},
        })

    def get_history(self, session_id: str, limit: int = 200) -> HttpResult:
        return self.request(
            "GET",
            f"/api/sessions/{session_id}/history?limit={max(1, min(200, int(limit)))}",
        )

    def get_archive(self, session_id: str, archive_id: str) -> HttpResult:
        return self.request(
            "GET",
            f"/api/sessions/{session_id}/archives/{archive_id}",
        )

    def get_commit_memories(self, session_id: str, archive_id: str) -> HttpResult:
        return self.request(
            "GET",
            f"/api/sessions/{session_id}/commits/{archive_id}/memories",
        )

    def fs_read(self, uri: str) -> HttpResult:
        """Read an EchoMem echo:// file through the existing read-only API."""
        return self.request("GET", f"/fs/read?uri={quote(uri, safe=':/')}")

    def commit(
        self,
        session_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> HttpResult:
        body: dict[str, Any] = {"metadata": {"keep_recent_count": 0}}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return self.request(
            "POST",
            f"/api/sessions/{session_id}/commit",
            body,
            operation="commit",
        )

    def commit_status(self, session_id: str, archive_id: str) -> HttpResult:
        return self.request(
            "GET",
            f"/api/sessions/{session_id}/commits/{archive_id}",
            # Polling observes an existing commit and must not consume a
            # mutation-lane admission slot or inflate commit queue latency.
            operation="commit_poll",
        )

    def search(self, session_id: str, query: str, timeout_s: float) -> HttpResult:
        return self.request("POST", "/api/retrieval/search", {
            "query": query, "agent_id": self.agent_id, "session_id": session_id,
            "limit": 10, "include_explain": False, "include_debug": True,
        }, timeout_s=timeout_s, operation="search")

def status_from(payload: dict[str, Any]) -> str:
    raw = payload.get("status")
    if isinstance(raw, dict):
        raw = raw.get("status") or raw.get("stage") or raw.get("state")
    return str(raw or payload.get("stage") or payload.get("state") or "").lower()


def extract_archive(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if not isinstance(result, dict):
        result = {}
    return str(payload.get("archive_id") or payload.get("task_id") or result.get("archive_id") or result.get("task_id") or payload.get("id") or "")


def extract_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the server-assigned message object from an add-message response."""
    candidates: list[Any] = [payload]
    for key in ("message", "result", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        message_id = (
            candidate.get("id")
            or candidate.get("message_id")
            or candidate.get("messageId")
        )
        if message_id not in (None, ""):
            return {
                "id": str(message_id),
                "role": candidate.get("role"),
                "content": candidate.get("content"),
                "metadata": candidate.get("metadata") or {},
            }
    return {}
