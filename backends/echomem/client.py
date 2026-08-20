"""EchoMemory HTTP backend client with commit polling, retrieval, and identity isolation.

Moved from plugins/echomem_mcp/memory_client.py.
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from typing import Any

from backends.memory_types import BaseHTTPMemoryClient, SearchResult

logger = logging.getLogger("echomem_client")


class EchoMemClient(BaseHTTPMemoryClient):
    """Thin HTTP client for EchoMem's REST API.

    Handles session open/message/commit/search/log-query with retry,
    logging, and commit-status polling. Uses urllib so there are zero
    Third-party deps.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8010",
        auth_key: str = "",
        account: str = "default",
        user_id: str = "default",
        agent_id: str = "default",
        workspace: str = "",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
        log_access_key: str = "",
    ):
        super().__init__(
            base_url,
            account=account,
            user_id=user_id,
            agent_id=agent_id,
            workspace=workspace,
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_backoff_s=retry_backoff_s,
        )
        self.auth_key = auth_key
        self.log_access_key = log_access_key

    # -- low-level HTTP -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.auth_key:
            h["X-Auth-Key"] = self.auth_key
        return h

    # -- commit polling hooks -------------------------------------------

    def _fetch_commit_status(self, session_id: str, archive_id: str) -> dict[str, Any]:
        return self._get(f"/api/sessions/{session_id}/commits/{archive_id}")

    def _parse_commit_status(self, resp: dict[str, Any]) -> str:
        raw_status = resp.get("status")
        if isinstance(raw_status, dict):
            return (
                raw_status.get("status")
                or raw_status.get("stage")
                or raw_status.get("state")
                or ""
            ).lower()
        return (
            raw_status
            or resp.get("stage")
            or resp.get("state")
            or ""
        ).lower()

    def _extract_commit_error(self, resp: dict[str, Any], status: str) -> str:
        raw_status = resp.get("status")
        if isinstance(raw_status, dict):
            return raw_status.get("error", status)
        return resp.get("error", status)

    # -- session lifecycle -----------------------------------------------

    def health(self) -> dict[str, Any]:
        """Verify that the EchoMem HTTP service is reachable."""
        return self._get("/health")

    def provision_isolated_identity(self, label: str) -> dict[str, str]:
        """Create a tenant/user/key and switch this client to that identity."""
        tenant_response = self._post("/api/auth/tenants", {"name": label})
        tenant = tenant_response.get("tenant", {})
        tenant_id = str(tenant.get("tenant_id") or "") if isinstance(tenant, dict) else ""
        bootstrap_key = (
            str(tenant.get("bootstrap_key") or "")
            if isinstance(tenant, dict)
            else ""
        )
        if not tenant_id:
            raise RuntimeError(f"tenant provisioning returned no tenant id: {tenant_response}")

        provisioning_headers = (
            {"X-EchoMem-Bootstrap-Key": bootstrap_key}
            if bootstrap_key
            else None
        )
        try:
            user_response = self._post(
                f"/api/auth/tenants/{tenant_id}/users",
                {},
                headers=provisioning_headers,
            )
            user = user_response.get("user", {})
            user_id = str(user.get("user_id") or "") if isinstance(user, dict) else ""
            if not user_id:
                raise RuntimeError(
                    f"user provisioning returned no user id: {user_response}"
                )

            key_response = self._post(
                f"/api/auth/tenants/{tenant_id}/users/{user_id}/key",
                {},
                headers=provisioning_headers,
            )
            auth_key = str(key_response.get("auth_key") or "")
            if not auth_key:
                raise RuntimeError(
                    f"key provisioning returned no auth key: {key_response}"
                )
        finally:
            provisioning_headers = None

        self.auth_key = auth_key
        self.account = tenant_id
        self.user_id = user_id
        return {"tenant_id": tenant_id, "user_id": user_id}

    def delete_current_identity(self) -> None:
        """Delete the tenant selected by the current auth key."""
        response = self._post("/api/auth/account/delete", {})
        if str(response.get("status") or "").lower() != "deleted":
            raise RuntimeError(f"account deletion was not confirmed: {response}")

    def open_session(self, title: str = "") -> str:
        """Create a new session, return its id."""
        body: dict[str, Any] = {
            "agent_id": self.agent_id,
            "metadata": {
                "title": title,
                "account_id": self.account,
                "user_id": self.user_id,
            },
        }
        if title:
            body["title"] = title
        if self.workspace:
            body["workspace"] = self.workspace
        resp = self._post("/api/sessions/open", body)
        sid = resp.get("session_id") or resp.get("id") or ""
        if not sid:
            scope = resp.get("scope", {})
            if isinstance(scope, dict):
                sid = scope.get("session_id") or ""
        if not sid:
            raise RuntimeError(f"open_session returned no id: {resp}")
        self._log.info("opened session %s (%s)", sid, title)
        return sid

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        created_at: str = "",
        role_id: str = "",
    ) -> dict[str, Any]:
        """Append one message to a session."""
        body: dict[str, Any] = {
            "role": role,
            "content": content,
        }
        metadata: dict[str, Any] = {}
        if created_at:
            body["created_at"] = created_at
            metadata["created_at"] = created_at
        if role_id:
            body["role_id"] = role_id
            body["name"] = role_id
            metadata["role_id"] = role_id
        if metadata:
            body["metadata"] = metadata
        return self._post(f"/api/sessions/{session_id}/messages", body)

    def commit_session(self, session_id: str, keep_recent_count: int = 0) -> str:
        """Commit a session, return the archive_id."""
        body: dict[str, Any] = {
            "metadata": {"keep_recent_count": int(keep_recent_count or 0)}
        }
        resp = self._post(f"/api/sessions/{session_id}/commit", body)
        aid = resp.get("archive_id") or resp.get("task_id") or ""
        if not aid:
            result = resp.get("result", {})
            if isinstance(result, dict):
                aid = result.get("archive_id") or result.get("task_id") or ""
        if not aid:
            aid = resp.get("id", "")
        self._log.info("committed session %s -> archive %s", session_id, aid)
        return aid

    def commit_status(self, session_id: str, archive_id: str) -> dict[str, Any]:
        """Poll commit status. Returns the raw status dict."""
        return self._get(f"/api/sessions/{session_id}/commits/{archive_id}")

    def has_archives(self, session_id: str) -> bool:
        """Check whether a session has any committed archives."""
        resp = self._get(f"/api/sessions/{session_id}/commits")
        archives = resp.get("archives") or resp.get("commits") or []
        return bool(archives)

    # -- retrieval ------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        """Search EchoMem for memory items."""
        body: dict[str, Any] = {
            "query": query,
            "agent_id": agent_id or self.agent_id,
            "limit": top_k,
            "include_explain": False,
            "include_debug": True,
        }
        if session_id:
            body["session_id"] = session_id
        resp = self._post("/api/retrieval/search", body, timeout_s=timeout_s)
        items = resp.get("result", {}).get("items", []) if "result" in resp else resp.get("items", [])
        return [SearchResult.from_dict(item) for item in items]

    def fetch_logs(
        self,
        *,
        tenant_id: str = "",
        user_id: str = "",
        request_id: str = "",
        event: str = "",
        route: str = "",
        since: str = "",
        until: str = "",
        limit: int = 200,
        log_access_key: str = "",
        max_pages: int = 25,
    ) -> dict[str, Any]:
        """Query EchoMem core logs, scoped to the run's tenant/user.

        Calls ``GET /api/logs`` and pages through all matching records so
        the result covers every log line for the requested identity (e.g.
        injected memories plus QA of one eval run). Only non-empty filters
        are sent; ``limit`` is capped at 200 (the API maximum) and
        pagination stops on ``page.has_more`` or after *max_pages*.
        """
        if not tenant_id and not user_id and not request_id:
            raise ValueError("fetch_logs requires at least one of tenant_id/user_id/request_id")
        access_key = log_access_key or self.log_access_key
        query: dict[str, Any] = {}
        if tenant_id:
            query["tenant_id"] = tenant_id
        if user_id:
            query["user_id"] = user_id
        if request_id:
            query["request_id"] = request_id
        if event:
            query["event"] = event
        if route:
            query["route"] = route
        if since:
            query["since"] = since
        if until:
            query["until"] = until
        query["limit"] = min(int(limit or 200), 200)

        items: list[dict[str, Any]] = []
        page: dict[str, Any] = {}
        diagnostics: dict[str, Any] = {}
        offset = 0
        for _ in range(max_pages):
            query["offset"] = offset
            url = f"{self.base_url}/api/logs?{urllib.parse.urlencode(query)}"
            headers = self._headers()
            if access_key:
                headers["X-Log-Access-Key"] = access_key
            req = urllib.request.Request(url, headers=headers, method="GET")
            result = self._do_request(req)
            result = result.get("result", result) if isinstance(result, dict) else {}
            page_items = result.get("items", []) or []
            items.extend(page_items)
            page = result.get("page", {}) if isinstance(result, dict) else {}
            if isinstance(result, dict) and "diagnostics" in result:
                diagnostics = result.get("diagnostics") or {}
            if not page.get("has_more"):
                break
            returned_this_page = len(page_items)
            if returned_this_page <= 0:
                break
            offset += returned_this_page
        return {
            "query": query,
            "items": items,
            "page": page,
            "diagnostics": diagnostics,
        }

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        """Read a public EchoMem filesystem URI."""
        response = self._get("/fs/read", {"uri": uri}, timeout_s=timeout_s)
        result = response.get("result") or {}
        return str(
            response.get("content")
            or response.get("text")
            or (result.get("content") if isinstance(result, dict) else "")
            or (result.get("text") if isinstance(result, dict) else "")
            or ""
        )

    def fs_list(
        self,
        uri: str,
        *,
        recursive: bool = False,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """List public EchoMemory filesystem entries."""
        if recursive:
            return self.fs_glob(
                uri.rstrip("/") + "/**",
                timeout_s=timeout_s,
            )
        response = self._get(
            "/fs/ls",
            {"uri": uri},
            timeout_s=timeout_s,
        )
        result = response.get("result")
        entries = (
            result.get("entries")
            if isinstance(result, dict)
            else response.get("entries")
        )
        return [
            dict(item)
            for item in (entries or [])
            if isinstance(item, dict)
        ]

    def fs_glob(
        self,
        pattern: str,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find public EchoMemory filesystem entries by glob pattern."""
        response = self._post(
            "/fs/glob",
            {"pattern": pattern},
            timeout_s=timeout_s,
        )
        result = response.get("result")
        entries = (
            result.get("entries")
            if isinstance(result, dict)
            else response.get("entries")
        )
        return [
            dict(item)
            for item in (entries or [])
            if isinstance(item, dict)
        ]
