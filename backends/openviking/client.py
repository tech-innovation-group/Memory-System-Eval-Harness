"""OpenViking HTTP backend client with commit polling and retrieval.

Moved from plugins/openviking_mcp/memory_client.py. The client logic is unchanged;
only the import path for BaseHTTPMemoryClient and SearchResult has changed
to point at backends.memory_types.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backends.memory_types import BaseHTTPMemoryClient, SearchResult

logger = logging.getLogger("openviking_client")


class OpenVikingClient(BaseHTTPMemoryClient):
    """Thin HTTP client for the OpenViking REST API.

    Handles session open/message/commit/search with retry, logging, and
    commit-status polling. Memory files are read from the local workspace
    filesystem (OpenViking has no /fs HTTP endpoints).
    """

    DEFAULT_USER_TARGET_URI = "viking://user/memories/"
    DEFAULT_AGENT_TARGET_URI = "viking://agent/memories/"
    DEFAULT_SCORE_THRESHOLD = 0.0

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:19080",
        api_key: str = "",
        account: str = "default",
        user_id: str = "default",
        agent_id: str = "default",
        workspace: str = "",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
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
        self.api_key = api_key

    # -- low-level HTTP -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "X-OpenViking-Account": self.account or "default",
            "X-OpenViking-User": self.user_id or "default",
            "X-OpenViking-Agent": self.agent_id or "default",
        }
        if self.api_key:
            h["X-API-Key"] = self.api_key
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # -- identity isolation --------------------------------------------

    @property
    def auth_key(self) -> str:
        return self.api_key

    def provision_isolated_identity(self, label: str) -> dict[str, str]:
        """Generate a unique OpenViking account for evaluation isolation."""
        safe_label = "".join(c if c.isalnum() or c in "-_." else "-" for c in label)[:60].strip("-")
        new_account = f"eval-{safe_label}-{uuid.uuid4().hex[:8]}"
        self.account = new_account
        self._log.info("provisioned isolated account: %s", new_account)
        return {"tenant_id": new_account, "user_id": self.user_id}

    def delete_current_identity(self) -> None:
        """No server-side tenant deletion in OpenViking; data persists in workspace."""
        self._log.info("identity %s retained in workspace %s", self.account, self.workspace)

    # -- commit polling hooks -------------------------------------------

    def _fetch_commit_status(self, session_id: str, archive_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/tasks/{archive_id}")

    def _commit_failed_statuses(self) -> tuple[str, ...]:
        return ("failed", "error", "cancelled", "canceled")

    def _parse_commit_status(self, resp: dict[str, Any]) -> str:
        result = resp.get("result") or {}
        return str(
            result.get("status")
            or result.get("stage")
            or result.get("state")
            or resp.get("status")
            or ""
        ).lower()

    def _extract_commit_error(self, resp: dict[str, Any], status: str) -> str:
        result = resp.get("result") or {}
        return str(result.get("error") or resp.get("error") or status)

    # -- session lifecycle -----------------------------------------------

    def health(self) -> dict[str, Any]:
        """Verify that the OpenViking HTTP service is reachable."""
        try:
            return self._get("/api/v1/sessions", {"limit": 1})
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                return {"status": "ok", "note": f"HTTP {e.code}"}
            raise

    def open_session(self, title: str = "") -> str:
        """Create a new OpenViking session, return its id."""
        safe_title = "".join(
            c if c.isalnum() or c in "-_." else "-" for c in (title or "session")
        )[:60].strip("-")
        session_id = f"eval-{safe_title}-{uuid.uuid4().hex[:12]}"
        resp = self._post("/api/v1/sessions", {"session_id": session_id})
        sid = resp.get("session_id") or resp.get("id") or session_id
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
        """Append one message to an OpenViking session."""
        normalized_role = role if role in ("user", "assistant") else "user"
        body: dict[str, Any] = {
            "role": normalized_role,
            "role_id": role_id or role,
            "content": content,
            "parts": [{"type": "text", "text": content}],
            "created_at": created_at or datetime.now().isoformat(timespec="seconds"),
        }
        return self._post(f"/api/v1/sessions/{session_id}/messages", body)

    def commit_session(self, session_id: str, keep_recent_count: int = 0) -> str:
        """Commit a session, return the task_id for polling."""
        resp = self._post(f"/api/v1/sessions/{session_id}/commit", {})
        task_id = resp.get("task_id") or resp.get("id") or ""
        if not task_id:
            result = resp.get("result", {})
            if isinstance(result, dict):
                task_id = result.get("task_id") or result.get("id") or ""
        status = str(resp.get("status") or "").lower()
        if not task_id and status in ("accepted", "committed", "ok", "completed"):
            task_id = session_id
        self._log.info("committed session %s -> task %s", session_id, task_id)
        return task_id

    # -- retrieval ------------------------------------------------------

    def _search_once(
        self,
        query: str,
        target_uri: str,
        limit: int,
        timeout_s: float | None,
    ) -> list[dict[str, Any]]:
        """Single OpenViking search call, returning raw result items."""
        body: dict[str, Any] = {
            "query": query,
            "target_uri": target_uri,
            "limit": limit,
            "score_threshold": self.DEFAULT_SCORE_THRESHOLD,
        }
        try:
            resp = self._post("/api/v1/search/find", body, timeout_s=timeout_s)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._log.warning("search endpoint not found for target_uri=%s", target_uri)
                return []
            raise
        result = resp.get("result", resp)
        if isinstance(result, list):
            return result[:limit]
        if isinstance(result, dict):
            items = (
                result.get("items")
                or result.get("results")
                or result.get("hits")
                or result.get("memories")
                or result.get("resources")
                or []
            )
            if isinstance(result.get("memories"), list) and isinstance(result.get("resources"), list):
                items = result["memories"] + result["resources"]
            return items[:limit] if isinstance(items, list) else []
        return []

    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        """Search OpenViking for memory items (user + agent scopes)."""
        prev_agent = self.agent_id
        if agent_id:
            self.agent_id = agent_id
        try:
            user_items = self._search_once(
                query, self.DEFAULT_USER_TARGET_URI, top_k, timeout_s,
            )
            agent_items = self._search_once(
                query, self.DEFAULT_AGENT_TARGET_URI, top_k, timeout_s,
            )
        finally:
            self.agent_id = prev_agent

        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for item in user_items + agent_items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("uri") or item.get("path") or item.get("id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)

        merged.sort(
            key=lambda item: float(item.get("score") or item.get("similarity") or 0.0),
            reverse=True,
        )
        return [SearchResult.from_dict(item) for item in merged[:top_k]]

    # -- filesystem (local workspace) -----------------------------------

    def _uri_to_local_path(self, uri: str) -> Path | None:
        """Map a viking:// URI to a local filesystem path under workspace."""
        if not self.workspace or not uri.startswith("viking://"):
            return None
        rel = uri.removeprefix("viking://").lstrip("/")
        if not rel:
            return None
        return Path(self.workspace).expanduser() / "viking" / self.account / rel

    def _local_to_uri(self, path: Path) -> str:
        """Build a viking:// URI from a local workspace path."""
        try:
            account_root = Path(self.workspace).expanduser() / "viking" / self.account
            rel = path.relative_to(account_root)
        except (ValueError, TypeError):
            return ""
        return "viking://" + str(rel).replace(os.sep, "/")

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        """Read memory content from the local workspace filesystem."""
        path = self._uri_to_local_path(uri)
        if not path or not path.exists() or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            self._log.warning("fs_read failed for %s: %s", uri, e)
            return ""

    def fs_list(
        self,
        uri: str,
        *,
        recursive: bool = False,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """List entries under a viking:// URI in the local workspace."""
        if recursive:
            return self.fs_glob(uri.rstrip("/") + "/**/*", timeout_s=timeout_s)
        path = self._uri_to_local_path(uri)
        if not path or not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for child in sorted(path.iterdir()):
            entries.append({
                "uri": self._local_to_uri(child),
                "path": str(child),
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else 0,
            })
        return entries

    def fs_glob(
        self,
        pattern: str,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find entries matching a glob pattern under the workspace."""
        if not self.workspace or not pattern.startswith("viking://"):
            return []
        rel = pattern.removeprefix("viking://")
        account_root = Path(self.workspace).expanduser() / "viking" / self.account
        entries: list[dict[str, Any]] = []
        try:
            for path in sorted(account_root.glob(rel)):
                entries.append({
                    "uri": self._local_to_uri(path),
                    "path": str(path),
                    "name": path.name,
                    "is_dir": path.is_dir(),
                    "size": path.stat().st_size if path.is_file() else 0,
                })
        except (OSError, ValueError) as e:
            self._log.warning("fs_glob failed for %s: %s", pattern, e)
        return entries

    # -- console logs ---------------------------------------------------

    def fetch_console_logs(self) -> dict[str, Any]:
        """Fetch all four console API endpoints and return a combined dict."""
        today = datetime.now().strftime("%Y-%m-%d")
        logs: dict[str, Any] = {}
        endpoints: list[tuple[str, str, dict[str, Any] | None]] = [
            ("dashboard_summary", "/api/v1/console/dashboard/summary", None),
            ("tokens", "/api/v1/console/tokens", {
                "start_date": today, "end_date": today, "bucket": "day",
            }),
            ("context_commits", "/api/v1/console/context-commits", {
                "start_date": today, "end_date": today, "bucket": "hour",
            }),
            ("audit", "/api/v1/console/audit", {
                "page": 1, "page_size": 100,
            }),
        ]
        for name, path, query in endpoints:
            try:
                logs[name] = self._get(path, query)
            except Exception as e:
                logs[name] = {"error": str(e)}
        return logs
