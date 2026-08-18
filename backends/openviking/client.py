"""OpenViking HTTP backend client with commit polling and retrieval.

Moved from plugins/openviking_mcp/memory_client.py. The client logic is unchanged;
only the import path for BaseHTTPMemoryClient and SearchResult has changed
to point at backends.memory_types.
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backends.memory_types import BaseHTTPMemoryClient, SearchResult

logger = logging.getLogger("openviking_client")


class OpenVikingClient(BaseHTTPMemoryClient):
    """Thin HTTP client for the OpenViking REST API.

    Handles session open/message/commit/search with retry, logging, and
    commit-status polling. Memory files are read from the local workspace
    filesystem (OpenViking has no /fs HTTP endpoints).
    Document resources are injected via /api/v1/resources (temp_upload +
    add_resource) and searched under the user resources space.
    """

    DEFAULT_USER_TARGET_URI = "viking://user/memories/"
    DEFAULT_AGENT_TARGET_URI = "viking://agent/memories/"
    RESOURCE_TARGET_URI = "viking://user/resources/"
    DEFAULT_SCORE_THRESHOLD = 0.0
    _RESOURCE_AUX_BASENAMES = {".abstract.md", ".overview.md"}
    _DONE_STATUSES = ("completed", "succeeded", "done")
    _FAILED_STATUSES = ("failed", "error", "cancelled", "canceled", "unknown")

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
        # resource path ("user/...") -> add_resource task_id, for index waiting
        self._resource_tasks: dict[str, str] = {}
        # injected resource paths (rel to resources root, e.g. "hotpotqa/x")
        self._resource_dirs: set[str] = set()

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

    # -- document resources (HotpotQA documents mode) --------------------

    @staticmethod
    def _unwrap_result(resp: dict[str, Any]) -> dict[str, Any]:
        """OpenViking responses wrap payloads under ``result``; unwrap if so."""
        result = resp.get("result")
        return result if isinstance(result, dict) else resp

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        """Normalize bare tag values to OpenViking's strict ``k=v`` format.

        The server rejects plain values (``["hotpotqa"]`` -> 400); bare
        values are prefixed with a default ``source=`` key.
        """
        normalized: list[str] = []
        for item in tags or []:
            value = str(item).strip()
            if "=" not in value:
                value = f"source={value}"
            if value not in normalized:
                normalized.append(value)
        return normalized

    def _post_raw(
        self,
        path: str,
        data: bytes,
        content_type: str,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """POST a raw (non-JSON) body, e.g. multipart temp file upload."""
        url = f"{self.base_url}{path}"
        headers = self._headers()
        headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        return self._do_request(req, timeout_s=timeout_s)

    def _upload_temp_file(
        self,
        content: str,
        filename: str,
        *,
        timeout_s: float | None = None,
    ) -> str:
        """Upload content as a temp file, return the ``temp_file_id``."""
        safe_name = re.sub(r'[^\w.\-]+', "_", filename) or "doc.md"
        boundary = f"----ov-eval-{uuid.uuid4().hex}"
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        resp = self._post_raw(
            "/api/v1/resources/temp_upload",
            head + content.encode("utf-8") + tail,
            f"multipart/form-data; boundary={boundary}",
            timeout_s=timeout_s,
        )
        payload = self._unwrap_result(resp)
        temp_file_id = str(payload.get("temp_file_id") or "")
        if not temp_file_id:
            raise RuntimeError(f"temp_upload returned no temp_file_id: {resp}")
        return temp_file_id

    def add_resource(
        self,
        path: str,
        content: str,
        *,
        name: str = "",
        content_type: str = "text/markdown",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inject one document into the OpenViking user resources space.

        The content is uploaded as a temp file and added at
        ``viking://user/resources/<path>`` with ``processing_mode=vectors_only``
        (chunk+embed only, no LLM semantic tasks — requires openviking
        >= 0.4.12). The returned task id is remembered so
        :meth:`wait_for_resource_index` can poll it.
        """
        clean_path = str(path).lstrip("/")
        source_name = name or clean_path.rsplit("/", 1)[-1]
        temp_file_id = self._upload_temp_file(
            content, source_name, timeout_s=max(600.0, self.timeout_s)
        )
        body: dict[str, Any] = {
            "temp_file_id": temp_file_id,
            "source_name": source_name,
            "to": f"{self.RESOURCE_TARGET_URI}{clean_path}",
            # Empty reason: OpenViking skips the per-resource "reason memory"
            # LLM session_commit (resource_service._link_resource_reason_memory),
            # which otherwise blocks the add_resource task on a slow LLM call.
            "reason": "",
            "instruction": "",
            "wait": False,
            "processing_mode": "vectors_only",
            "tags": self._normalize_tags(tags),
            "tag_mode": "replace",
        }
        resp = self._post(
            "/api/v1/resources", body, timeout_s=max(600.0, self.timeout_s)
        )
        payload = self._unwrap_result(resp)
        task_id = str(payload.get("task_id") or resp.get("task_id") or "")
        status = str(payload.get("status") or resp.get("status") or "accepted")
        resource_key = f"user/{clean_path}"
        if task_id:
            self._resource_tasks[resource_key] = task_id
        self._resource_dirs.add(clean_path)
        self._log.info(
            "added resource %s -> task %s (status=%s)", resource_key, task_id or "-", status
        )
        return {"task_id": task_id, "status": status, "path": resource_key}

    def wait_for_resource_index(
        self,
        paths: list[str],
        *,
        timeout_s: float = 3600.0,
        poll_interval_s: float = 2.0,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Wait until every added resource finishes indexing (or fails).

        Polls the add_resource task of each path under
        ``/api/v1/tasks/{task_id}`` until it reaches a terminal status.
        ``progress(done, total)`` is invoked after each poll pass with the
        number of paths that reached a terminal status, for progress bars.
        """
        pending: list[str] = [
            p for p in paths if self._resource_tasks.get(p)
        ]
        failures: dict[str, str] = {}
        deadline = time.monotonic() + timeout_s
        while pending:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"resource indexing not finished after {timeout_s:g}s "
                    f"({len(pending)} pending: {pending[:5]})"
                )
            still_pending: list[str] = []
            for path in pending:
                task_id = self._resource_tasks[path]
                try:
                    status = self._parse_commit_status(
                        self._fetch_commit_status(path, task_id)
                    )
                except Exception as exc:
                    failures[path] = f"{type(exc).__name__}: {exc}"
                    self._log.warning("resource %s task poll failed: %s", path, exc)
                    continue
                if status in self._FAILED_STATUSES:
                    failures[path] = status or "failed"
                elif status not in self._DONE_STATUSES:
                    still_pending.append(path)
            pending = still_pending
            if progress is not None:
                progress(len(paths) - len(pending), len(paths))
            if pending:
                time.sleep(poll_interval_s)
        return {"indexed": len(paths) - len(failures), "failed": failures}

    def search_resources(
        self,
        query: str,
        limit: int = 8,
        tags: list[str] | None = None,
        paths: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search the user resources space; read each hit's content locally.

        Returns items with ``path`` relative to the resources root (e.g.
        ``hotpotqa/<slug>-<hash>``) so HotpotQA evidence can resolve titles
        via the corpus ``path_title_map``, plus ``text``/``score``/``uri``.
        """
        raw_items = self._search_once(
            query, self.RESOURCE_TARGET_URI, limit, timeout_s
        )
        results: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or item.get("path") or "")
            if not uri:
                continue
            rel = uri.split("resources/", 1)[1] if "resources/" in uri else uri
            rel = rel.strip("/")
            basename = rel.rsplit("/", 1)[-1]
            if basename in self._RESOURCE_AUX_BASENAMES:
                continue
            # The server stores the uploaded file under the target path and
            # may append the source filename as the last segment; normalize
            # back to the injected resource path so HotpotQA evidence can
            # resolve titles via the corpus path_title_map.
            parent = rel.rsplit("/", 1)[0] if "/" in rel else rel
            if parent and parent in self._resource_dirs:
                rel = parent
            content = self.fs_read(uri, timeout_s=timeout_s)
            if not content:
                content = str(item.get("abstract") or item.get("content") or "")
            results.append({
                "path": rel,
                "uri": uri,
                "source_uri": uri,
                "score": float(item.get("score") or item.get("similarity") or 0.0),
                "text": content,
                "abstract": str(item.get("abstract") or ""),
                "chunk_index": None,
            })
        return results
