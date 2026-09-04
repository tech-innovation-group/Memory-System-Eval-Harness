"""Shared memory types: result dataclasses, client protocol, HTTP base, null client.

These types are used by agent plugins that support memory injection.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol


# ------------------------------------------------------------------ #
#  Result types                                                       #
# ------------------------------------------------------------------ #

@dataclass
class CommitResult:
    """Outcome of a session commit + poll cycle."""

    session_id: str
    archive_id: str
    status: str
    elapsed_s: float
    polls: int
    error: str = ""


@dataclass
class SearchResult:
    """Normalized memory item returned by search."""

    uri: str
    score: float
    content: str = ""
    memory_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchResult":
        return cls(
            uri=(
                data.get("uri")
                or data.get("evidence_uri")
                or data.get("source_uri")
                or data.get("path")
                or data.get("id")
                or ""
            ),
            score=float(data.get("score", 0.0)),
            content=(
                data.get("content")
                or data.get("text")
                or data.get("preview")
                or data.get("abstract")
                or data.get("overview")
                or data.get("summary")
                or ""
            ),
            memory_type=(
                data.get("memory_type")
                or data.get("type")
                or data.get("kind")
                or ""
            ),
            metadata=dict(data),
        )

    def to_dict(self) -> dict[str, Any]:
        """Preserve native evidence metadata while exposing normalized fields."""
        return {
            **self.metadata,
            "uri": self.uri,
            "score": self.score,
            "content": self.content,
            "memory_type": self.memory_type,
        }


# ------------------------------------------------------------------ #
#  Client protocol                                                     #
# ------------------------------------------------------------------ #

class MemoryClient(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        """Retrieve memory relevant to a query."""

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        """Read full content for a memory URI."""

    def fs_list(
        self,
        uri: str,
        *,
        recursive: bool = False,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """List memory filesystem entries."""

    def fs_glob(
        self,
        pattern: str,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find memory filesystem entries by glob pattern."""


# ------------------------------------------------------------------ #
#  Shared HTTP transport                                               #
# ------------------------------------------------------------------ #

class BaseHTTPMemoryClient(ABC):
    """Shared HTTP transport for memory clients using urllib.

    Provides _post, _get, _do_request (with retry and deadline handling),
    and poll_commit (template method).  Subclasses implement _headers()
    and the session/search/fs methods specific to their API.
    """

    def __init__(
        self,
        base_url: str,
        *,
        account: str = "default",
        user_id: str = "default",
        agent_id: str = "default",
        workspace: str = "",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.account = account
        self.user_id = user_id
        self.agent_id = agent_id
        self.workspace = workspace
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self._log = logging.getLogger(self.__class__.__name__)
        # A client is normally shared by all workers for one tenant.  Serialize
        # status polls so a commit wave cannot turn into a poll storm.
        self._commit_poll_gate = threading.Lock()
        self._next_commit_poll_at = 0.0

    # -- abstract hooks -------------------------------------------------

    @abstractmethod
    def _headers(self) -> dict[str, str]:
        """Return HTTP headers for requests."""

    # -- low-level HTTP -------------------------------------------------

    def _post(
        self,
        path: str,
        body: dict | None = None,
        *,
        timeout_s: float | None = None,
        headers: dict[str, str] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body or {}).encode()
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        if request_id:
            request_headers["X-Request-ID"] = request_id
        req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
        return self._do_request(req, timeout_s=timeout_s)

    def _get(
        self,
        path: str,
        query: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request_headers = self._headers()
        if request_id:
            request_headers["X-Request-ID"] = request_id
        req = urllib.request.Request(url, headers=request_headers, method="GET")
        return self._do_request(req, timeout_s=timeout_s)

    def _do_request(
        self,
        req: urllib.request.Request,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        request_timeout = self.timeout_s if timeout_s is None else max(0.001, timeout_s)
        deadline = time.monotonic() + request_timeout
        # max_retries counts retries after the initial request. A value of
        # zero therefore still performs one request, but never retries it.
        for attempt in range(1, max(0, self.max_retries) + 2):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"request deadline exceeded after {request_timeout:g}s")
                with urllib.request.urlopen(req, timeout=min(self.timeout_s, remaining)) as resp:
                    raw = resp.read().decode("utf-8")
                    if not raw:
                        return {}
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                finally:
                    e.close()
                # Preserve bounded diagnostics for callers that write
                # structured evidence; credentials are never included here.
                e.echomem_status = e.code
                e.echomem_url = req.full_url
                e.echomem_body = body
                last_err = e
                self._log.warning(
                    "HTTP %s %s -> %d %s (attempt %d/%d)",
                    req.method, req.full_url, e.code, body, attempt, self.max_retries,
                )
                if e.code >= 500 and attempt <= self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"request deadline exceeded after {request_timeout:g}s"
                        ) from e
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    raise
            except Exception as e:
                last_err = e
                self._log.warning(
                    "Request %s failed: %s (attempt %d/%d)",
                    req.full_url, e, attempt, self.max_retries,
                )
                if attempt <= self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"request deadline exceeded after {request_timeout:g}s"
                        ) from e
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    raise
        if last_err is not None:
            raise last_err
        raise RuntimeError(f"request failed after {self.max_retries} retries")

    # -- commit polling (template method) -------------------------------

    def poll_commit(
        self,
        session_id: str,
        archive_id: str,
        timeout_s: float = 600.0,
        poll_interval_s: float = 2.0,
    ) -> CommitResult:
        """Poll commit until completed/failed or timeout.

        timeout_s=0 means wait indefinitely.
        """
        if not archive_id:
            self._log.error(
                "poll_commit called with empty archive_id - commit did not return "
                "an archive id, cannot poll status"
            )
            return CommitResult(
                session_id, archive_id, "failed", 0.0, 0,
                error="empty archive_id - commit returned no archive id",
            )

        start = time.monotonic()
        polls = 0
        while True:
            polls += 1
            elapsed = time.monotonic() - start
            if timeout_s > 0 and elapsed > timeout_s:
                self._log.warning(
                    "commit poll timeout: session=%s archive=%s (%.1fs, %d polls)",
                    session_id, archive_id, elapsed, polls,
                )
                return CommitResult(session_id, archive_id, "timeout", elapsed, polls)

            try:
                with self._commit_poll_gate:
                    # Keep the poll gate clock separate from the request
                    # deadline clock.  This also avoids coupling the gate to
                    # callers that inject a monotonic clock for deadlines.
                    now = time.time()
                    wait_s = self._next_commit_poll_at - now
                    if wait_s > 0:
                        time.sleep(wait_s)
                    resp = self._fetch_commit_status(session_id, archive_id)
                    # Keep a small minimum spacing even when callers pass a
                    # very small interval.  The normal interval remains the
                    # caller's requested value.
                    self._next_commit_poll_at = (
                        now + max(0.1, poll_interval_s)
                    )
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500 and e.code not in (408, 409, 425, 429):
                    self._log.error(
                        "commit status returned terminal HTTP %d: session=%s archive=%s",
                        e.code, session_id, archive_id,
                    )
                    return CommitResult(
                        session_id, archive_id, "failed", elapsed, polls,
                        error=f"HTTP {e.code} while polling commit",
                    )
                self._log.warning(
                    "commit status poll error (poll %d): %s", polls, e,
                )
                delay = self._commit_retry_after(e, poll_interval_s)
                time.sleep(delay)
                continue
            except Exception as e:
                self._log.warning(
                    "commit status poll error (poll %d): %s", polls, e,
                )
                time.sleep(poll_interval_s)
                continue

            status = self._parse_commit_status(resp)

            if status in ("completed", "done", "success"):
                self._log.info(
                    "commit completed: session=%s archive=%s (%.1fs, %d polls)",
                    session_id, archive_id, elapsed, polls,
                )
                return CommitResult(session_id, archive_id, "completed", elapsed, polls)

            if status in self._commit_failed_statuses():
                self._log.error(
                    "commit failed: session=%s archive=%s status=%s",
                    session_id, archive_id, status,
                )
                error_msg = self._extract_commit_error(resp, status)
                return CommitResult(
                    session_id, archive_id, "failed", elapsed, polls,
                    error=error_msg,
                )

            time.sleep(poll_interval_s)

    def _commit_retry_after(
        self,
        error: urllib.error.HTTPError,
        default_s: float,
    ) -> float:
        """Return a bounded retry delay for a transient status-poll error."""
        retry_after: float | None = None
        headers = getattr(error, "headers", None)
        if headers is not None:
            raw_header = headers.get("Retry-After")
            if raw_header:
                try:
                    retry_after = float(raw_header)
                except (TypeError, ValueError):
                    retry_after = None

        # EchoMem also reports retry_after_s in its JSON error body.  The body
        # is attached by _do_request and is intentionally bounded there.
        if retry_after is None:
            raw_body = getattr(error, "echomem_body", "")
            try:
                body = json.loads(raw_body) if raw_body else {}
                retry_after = float(body.get("retry_after_s", 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                retry_after = None

        if retry_after is None or retry_after <= 0:
            retry_after = default_s
        if error.code == 429:
            retry_after = max(1.0, retry_after)
        return min(max(retry_after, 0.1), 30.0)

    def _fetch_commit_status(self, session_id: str, archive_id: str) -> dict[str, Any]:
        """Fetch commit status from the backend. Override for different endpoints."""
        raise NotImplementedError

    def _parse_commit_status(self, resp: dict[str, Any]) -> str:
        """Extract a lowercase status string from the poll response."""
        return str(
            resp.get("status")
            or resp.get("stage")
            or resp.get("state")
            or ""
        ).lower()

    def _commit_failed_statuses(self) -> tuple[str, ...]:
        return ("failed", "error")

    def _extract_commit_error(self, resp: dict[str, Any], status: str) -> str:
        return resp.get("error", status)

    # -- utility --------------------------------------------------------

    def close(self) -> None:
        """No persistent connection to close; kept for API symmetry."""
        pass

    def __enter__(self) -> "BaseHTTPMemoryClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ------------------------------------------------------------------ #
#  Null client                                                         #
# ------------------------------------------------------------------ #

class NullMemoryClient:
    """MemoryClient implementation that does nothing and returns empty results.

    Used by plugins that do not support memory injection (e.g. bare_llm)
    so benchmark code that calls memory_client.search(...) etc. works
    without conditional branches.
    """

    account = "default"
    user_id = "default"
    agent_id = "default"
    auth_key = ""

    def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    def open_session(self, title: str = "") -> str:
        return ""

    def add_message(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    def commit_session(self, session_id: str, keep_recent_count: int = 0) -> str:
        return ""

    def poll_commit(self, session_id: str, archive_id: str, timeout_s: float = 600.0, poll_interval_s: float = 2.0):
        return CommitResult(session_id, archive_id, "completed", 0.0, 0)

    def has_archives(self, session_id: str) -> bool:
        return False

    def search(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ) -> list[SearchResult]:
        return []

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
        return {}

    def reindex_all_resources(self) -> dict[str, Any]:
        return {}

    def resource_index_status(self, path: str) -> dict[str, Any]:
        return {}

    def wait_for_resource_index(
        self,
        paths: list[str],
        *,
        timeout_s: float = 3600.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        return {"indexed": len(paths), "failed": {}}

    def search_resources(
        self,
        query: str,
        limit: int = 8,
        tags: list[str] | None = None,
        paths: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def fs_read(self, uri: str, *, timeout_s: float | None = None) -> str:
        return ""

    def fs_list(self, uri: str, *, recursive: bool = False, timeout_s: float | None = None) -> list[dict[str, Any]]:
        return []

    def fs_glob(self, pattern: str, *, timeout_s: float | None = None) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        pass

    def __enter__(self) -> "NullMemoryClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass
