"""Concurrent read/write load generation with per-request instrumentation.

Every request produces a :class:`RequestRecord`; nothing is swallowed.
Clients used here are always built with ``max_retries=0`` so error rates
and latency distributions are not masked by client-side retries, and
timeouts are classified separately. Write transactions are a complete
injection flow (open -> add xN -> commit submit -> commit done via poll)
with the four stages timed individually.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import socket
import threading
import time
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from backends.echomem.client import EchoMemClient
from performance.prepare import ANCHOR_PREFIX, WRITE_ANCHOR_PREFIX, TenantContext
from performance.scenarios import SceneRun

logger = logging.getLogger("performance.loadgen")


def _content_hash(content: str) -> str:
    """Deterministic hash of one injected message (reconciliation key)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def is_anchor_query(query: str) -> bool:
    """Whether a read query is an anchor token (must always be recallable)."""
    return ANCHOR_PREFIX in query or WRITE_ANCHOR_PREFIX in query


def _items_match_expected_terms(items: list[Any], expected_terms: list[str]) -> bool:
    """Verify that Search returned evidence for the intended synthetic fact."""
    if not items:
        return False
    if not expected_terms:
        return True
    evidence = "\n".join(
        json.dumps(item.to_dict(), ensure_ascii=False)
        if hasattr(item, "to_dict")
        else str(item)
        for item in items
    ).lower()
    return all(str(term).lower() in evidence for term in expected_terms)


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Parse the ``Retry-After`` header of a 429 into seconds (or None)."""
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    raw = headers.get("Retry-After") or ""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # HTTP-date form unsupported; fall back to client backoff


# 服务端拒绝原因在响应中的别名（header 按小写匹配，payload 按原样匹配）。
_REASON_CODE_ALIASES = (
    "reason_code",
    "reasonCode",
    "error_code",
    "errorCode",
    "x-reason-code",
)
_REASON_CODE_HEADER_ALIASES = tuple(alias.lower() for alias in _REASON_CODE_ALIASES)


def extract_reason_code(exc: BaseException) -> str:
    """从失败响应提取服务端拒绝原因（reason_code）。

    对 ``HTTPError`` 先查 ``.headers``（小写 key 匹配别名 reason_code /
    reasonCode / error_code / errorCode / x-reason-code），再尝试解析
    ``exc.read()`` 的 JSON body（顶层 + ``error``/``meta`` 嵌套对象）。
    找不到返回 ""；非 HTTPError 返回 ""。
    """
    if not isinstance(exc, urllib.error.HTTPError):
        return ""
    headers = getattr(exc, "headers", None) or getattr(exc, "hdrs", None)
    if headers is not None:
        for key, value in headers.items():
            if str(key).lower() in _REASON_CODE_HEADER_ALIASES and str(value or "").strip():
                return str(value)
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    candidates: list[dict[str, Any]] = [payload]
    for key in ("error", "meta"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for alias in _REASON_CODE_ALIASES:
            value = candidate.get(alias)
            if value is not None and str(value).strip():
                return str(value)
    return ""


def retry_decision(
    exc: BaseException,
    *,
    max_retries: int,
    attempt: int,
    backoff_s: float,
) -> tuple[bool, float]:
    """Decide whether a failed commit_submit attempt is retryable.

    Returns ``(retryable, wait_s)``. Retryable: HTTP 429 (honouring
    ``Retry-After`` when present), HTTP 408/409/425, any 5xx, timeouts and
    connection errors. Non-retryable business 4xx are not retried.
    ``max_retries == 0`` means no retry at all.
    """
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code == 429:
            return True, _retry_after_seconds(exc) or backoff_s * attempt
        if code in (408, 409, 425):
            return True, backoff_s * attempt
        if code < 500:
            return False, 0.0
        return True, backoff_s * attempt
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True, backoff_s * attempt
    if isinstance(exc, urllib.error.URLError):
        return True, backoff_s * attempt
    return False, 0.0


@dataclass
class RequestRecord:
    """One measured operation with wall-clock start and completion times."""

    scene_key: str
    step_conc: int
    tenant_idx: int
    op: str
    stage_ms: float
    status: str  # ok | error
    error_type: str  # "" | timeout | http_4xx | http_5xx | connection | other
    ts_ms: float
    start_ts_ms: float | None = None
    request_id: str = ""
    http_status: int | None = None
    session_id: str = ""
    archive_id: str = ""
    extra: str = ""  # e.g. "burst"
    # -- write retry instrumentation (commit_submit) ---------------------
    retry_count: int = 0
    retried: bool = False
    retry_total_wait_ms: float = 0.0
    final_success: bool = False
    # -- per-request retry contract (commit_submit) ----------------------
    retry_after_s: float | None = None  # 最后一次失败尝试的 HTTP 429 Retry-After（秒）
    reason_code: str = ""  # 失败响应中的服务端拒绝原因（payload/header 别名提取）
    # -- message-level reconciliation (add) ------------------------------
    message_id: str = ""
    content_hash: str = ""
    content_bytes: int = 0
    # -- search quality assertion (read) ----------------------------------
    query: str = ""
    hit_count: int = 0
    real_recall: bool = False
    quality_ok: bool = True
    # Core orchestrator reported a degraded response (engine skipped /
    # saturated): an empty result is a capacity artifact, not a recall
    # failure, and must not be counted as a quality failure.
    degraded: bool = False
    query_kind: str = ""
    expected_terms: str = ""
    recall_matched: bool | None = None

    def to_csv_row(self) -> dict[str, Any]:
        return {
            "scene": self.scene_key,
            "step_conc": self.step_conc,
            "tenant_idx": self.tenant_idx,
            "op": self.op,
            "stage_ms": round(self.stage_ms, 3),
            "status": self.status,
            "error_type": self.error_type,
            "http_status": self.http_status,
            "ts_ms": round(self.ts_ms, 3),
            "start_ts_ms": (
                round(self.start_ts_ms, 3)
                if self.start_ts_ms is not None
                else None
            ),
            "request_id": self.request_id,
            "session_id": self.session_id,
            "archive_id": self.archive_id,
            "extra": self.extra,
            "retry_count": self.retry_count,
            "retried": self.retried,
            "retry_total_wait_ms": round(self.retry_total_wait_ms, 3),
            "final_success": self.final_success,
            "retry_after_s": self.retry_after_s,
            "reason_code": self.reason_code,
            "message_id": self.message_id,
            "content_hash": self.content_hash,
            "content_bytes": self.content_bytes,
            "hit_count": self.hit_count,
            "real_recall": self.real_recall,
            "quality_ok": self.quality_ok,
            "degraded": self.degraded,
            "query_kind": self.query_kind,
            "query": self.query,
            "expected_terms": self.expected_terms,
            "recall_matched": self.recall_matched,
        }


def classify_error(exc: BaseException) -> str:
    """Map an exception to a coarse error bucket for reporting."""
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return "timeout"
    if isinstance(exc, urllib.error.HTTPError):
        return "http_4xx" if exc.code < 500 else "http_5xx"
    if isinstance(exc, urllib.error.URLError):
        return "connection"
    return "other"


def _call_with_request_id(
    method: Any,
    *args: Any,
    request_id: str,
    **kwargs: Any,
) -> Any:
    """Use request correlation when the client supports it.

    A few local test doubles and third-party client adapters still expose
    the pre-correlation method signatures. Keep the load generator usable
    with them while real EchoMem clients receive ``X-Request-ID``.
    """
    try:
        return method(*args, request_id=request_id, **kwargs)
    except TypeError as exc:
        text = str(exc)
        if "request_id" not in text or "unexpected keyword" not in text:
            raise
        return method(*args, **kwargs)


def mix_token_sequence(read: int, write: int, total: int) -> list[str]:
    """Deterministic read/write token sequence honoring the ratio."""
    if read + write == 0:
        raise ValueError("read:write ratio must not be 0:0")
    sequence: list[str] = []
    while len(sequence) < total:
        sequence.extend(["read"] * read + ["write"] * write)
    return sequence[:total]


def split_threads(total: int, ratio: tuple[int, int]) -> tuple[int, int]:
    """Split worker threads by a read:write ratio (read first, rest write)."""
    read, write = ratio
    if read + write == 0:
        raise ValueError("read:write ratio must not be 0:0")
    read_threads = round(total * read / (read + write))
    return read_threads, total - read_threads


@dataclass
class WriteTransactionResult:
    ok: bool
    session_id: str
    anchor: str
    records: list[RequestRecord] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    content_hashes: list[str] = field(default_factory=list)
    archive_id: str = ""


def run_write_transaction(
    client: EchoMemClient,
    *,
    scene_key: str,
    step_conc: int,
    tenant_idx: int,
    seq: int,
    messages_per_session: int,
    commit_poll_timeout_s: float,
    commit_poll_interval_s: float = 0.2,
    extra: str = "",
    seed_anchor: str = "",
    commit_retry_max: int = 0,
    commit_retry_backoff_s: float = 0.5,
) -> WriteTransactionResult:
    """One full injection transaction with per-stage timing.

    The final message carries the transaction anchor; when an anchor is
    supplied for consistency probing the content embeds it.

    ``commit_submit`` is retried up to ``commit_retry_max`` times when the
    rejection is retryable (HTTP 429 with Retry-After, 408/409/425, 5xx,
    timeouts, connection errors). Non-retryable business 4xx fail the
    transaction immediately. Every accepted message is recorded with its
    message id and content hash for message-level reconciliation.
    """
    records: list[RequestRecord] = []
    result = WriteTransactionResult(ok=False, session_id="", anchor="")
    result.records = records  # same list; all failure paths return with records attached
    anchor = seed_anchor or f"{WRITE_ANCHOR_PREFIX}-{tenant_idx}-{seq}"
    transaction_id = f"tx-{uuid.uuid4().hex}"

    def record(
        op: str, ms: float, status: str, error_type: str = "", **extra_fields: Any
    ) -> None:
        completed_ts_ms = time.time() * 1000
        start_ts_ms = extra_fields.pop(
            "start_ts_ms",
            completed_ts_ms - max(0.0, float(ms)),
        )
        records.append(
            RequestRecord(
                scene_key=scene_key,
                step_conc=step_conc,
                tenant_idx=tenant_idx,
                op=op,
                stage_ms=ms,
                status=status,
                error_type=error_type,
                ts_ms=completed_ts_ms,
                start_ts_ms=start_ts_ms,
                request_id=str(extra_fields.pop("request_id", "") or transaction_id),
                session_id=result.session_id,
                extra=extra,
                **extra_fields,
            )
        )

    started = time.perf_counter()
    open_request_id = f"{transaction_id}-open"
    try:
        result.session_id = _call_with_request_id(
            client.open_session,
            title="perf-write-tx",
            request_id=open_request_id,
        )
    except Exception as exc:
        record(
            "open",
            (time.perf_counter() - started) * 1000,
            "error",
            classify_error(exc),
            request_id=open_request_id,
            http_status=getattr(exc, "code", None),
        )
        return result
    record("open", (time.perf_counter() - started) * 1000, "ok", request_id=open_request_id)

    for msg_idx in range(messages_per_session):
        last = msg_idx == messages_per_session - 1
        # 每条消息 content 必须唯一（末条携带 anchor 供 search 探测），
        # 否则 server_no_duplicate 会把客户端主动写入的重复内容误判为服务端重复。
        content = (
            f"压测写入会话消息 {anchor}-{msg_idx}" if last else f"压测写入会话消息-{msg_idx}"
        )
        started = time.perf_counter()
        add_request_id = f"{transaction_id}-add-{msg_idx}"
        try:
            resp = _call_with_request_id(
                client.add_message,
                result.session_id,
                "user",
                content,
                request_id=add_request_id,
            )
        except Exception as exc:
            record(
                "add",
                (time.perf_counter() - started) * 1000,
                "error",
                classify_error(exc),
                request_id=add_request_id,
                http_status=getattr(exc, "code", None),
            )
            return result
        message_id = str(
            resp.get("message_id")
            or resp.get("id")
            or resp.get("msg_id")
            or ""
        ) if isinstance(resp, dict) else ""
        result.message_ids.append(message_id)
        result.content_hashes.append(_content_hash(content))
        record(
            "add",
            (time.perf_counter() - started) * 1000,
            "ok",
            message_id=message_id,
            content_hash=_content_hash(content),
            content_bytes=len(content.encode("utf-8")),
            request_id=add_request_id,
        )

    started = time.perf_counter()
    attempts = 0
    retried = False
    total_wait_ms = 0.0
    last_retry_after_s: float | None = None
    last_reason_code = ""
    while True:
        attempts += 1
        commit_request_id = f"{transaction_id}-commit"
        try:
            archive_id = _call_with_request_id(
                client.commit_session,
                result.session_id,
                request_id=commit_request_id,
            )
            break
        except Exception as exc:
            error_type = classify_error(exc)
            # 记录最后一次失败尝试的 retry 契约字段（429 Retry-After / reason_code）
            last_retry_after_s = _retry_after_seconds(exc)
            last_reason_code = extract_reason_code(exc)
            retryable, wait_s = retry_decision(
                exc,
                max_retries=commit_retry_max,
                attempt=attempts,
                backoff_s=commit_retry_backoff_s,
            )
            if not retryable or attempts > commit_retry_max:
                record(
                    "commit_submit",
                    (time.perf_counter() - started) * 1000,
                    "error",
                    error_type,
                    retry_count=attempts - 1,
                    retried=retried,
                    retry_total_wait_ms=total_wait_ms,
                    retry_after_s=last_retry_after_s,
                    reason_code=last_reason_code,
                    request_id=commit_request_id,
                    http_status=getattr(exc, "code", None),
                )
                return result
            retried = True
            if wait_s > 0:
                time.sleep(wait_s)
                total_wait_ms += wait_s * 1000
    record(
        "commit_submit",
        (time.perf_counter() - started) * 1000,
        "ok",
        archive_id=archive_id,
        retry_count=attempts - 1,
        retried=retried,
        retry_total_wait_ms=total_wait_ms,
        final_success=True,
        retry_after_s=last_retry_after_s,
        reason_code=last_reason_code,
        request_id=commit_request_id,
    )
    result.archive_id = archive_id

    started = time.perf_counter()
    try:
        commit = client.poll_commit(
            result.session_id,
            archive_id,
            timeout_s=commit_poll_timeout_s,
            poll_interval_s=commit_poll_interval_s,
        )
    except Exception as exc:
        record(
            "commit_done",
            (time.perf_counter() - started) * 1000,
            "error",
            classify_error(exc),
            archive_id=archive_id,
        )
        return result
    if commit.status == "completed":
        record("commit_done", commit.elapsed_s * 1000, "ok", archive_id=archive_id)
        result.ok = True
    elif commit.status == "timeout":
        record(
            "commit_done",
            commit.elapsed_s * 1000,
            "error",
            "commit_timeout",
            archive_id=archive_id,
        )
    else:
        record(
            "commit_done",
            commit.elapsed_s * 1000,
            "error",
            "commit_failed",
            archive_id=archive_id,
        )
    result.anchor = anchor
    result.records = records
    return result


class RateLimiter:
    """Minimal thread-safe fixed-rate gate (read / commit rate limiting)."""

    def __init__(self, rps: float) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self._rps = rps
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + 1.0 / self._rps
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


@dataclass
class SceneResult:
    scene_key: str
    records: list[RequestRecord]
    wall_s: float
    burst_start_s: float | None = None
    burst_end_s: float | None = None


AnchorWrite = tuple[int, str, str]  # (tenant_idx, session_id, anchor)


@dataclass
class PreparedWrite:
    """One barrier session ready to commit: opened, messages added, not committed."""

    client: EchoMemClient
    tenant_idx: int
    session_id: str
    message_ids: list[str]
    content_hashes: list[str]
    anchor: str
    archive_id: str = ""
    reconciliation_registered: bool = False


class LoadGenerator:
    """Executes one :class:`SceneRun` at a time over prepared tenants."""

    def __init__(
        self,
        *,
        top_k: int = 5,
        timeout_s: float = 10.0,
        commit_poll_timeout_s: float = 120.0,
        commit_poll_interval_s: float = 0.2,
        rps: float | None = None,
        per_tenant_rps: float | None = None,
        commit_rpm: float = 0.0,
        per_tenant_commit_rpm: float | None = None,
        commit_retry_max: int = 0,
        commit_retry_backoff_s: float = 0.5,
        barrier_prepare_concurrency: int = 4,
        barrier_wave_size: int = 32,
        barrier_drain_timeout_s: float = 10.0,
        client_connection_error_abort_threshold: int = 100,
    ) -> None:
        self.top_k = top_k
        self.timeout_s = timeout_s
        self.commit_poll_timeout_s = commit_poll_timeout_s
        self.commit_poll_interval_s = commit_poll_interval_s
        if rps and per_tenant_rps:
            raise ValueError("rps and per_tenant_rps are mutually exclusive")
        if commit_rpm > 0 and per_tenant_commit_rpm:
            raise ValueError("commit_rpm and per_tenant_commit_rpm are mutually exclusive")
        if per_tenant_rps is not None and per_tenant_rps <= 0:
            raise ValueError("per_tenant_rps must be > 0")
        if per_tenant_commit_rpm is not None and per_tenant_commit_rpm <= 0:
            raise ValueError("per_tenant_commit_rpm must be > 0")
        self.rate_limiter = RateLimiter(rps) if rps else None
        self.per_tenant_rate = per_tenant_rps
        self._tenant_rate_limiters: dict[int, RateLimiter] = {}
        self.commit_rate_limiter = (
            RateLimiter(commit_rpm / 60.0) if commit_rpm > 0 else None
        )
        self.per_tenant_commit_rate = per_tenant_commit_rpm
        self._tenant_commit_rate_limiters: dict[int, RateLimiter] = {}
        self.commit_retry_max = commit_retry_max
        self.commit_retry_backoff_s = commit_retry_backoff_s
        if barrier_prepare_concurrency < 1:
            raise ValueError("barrier_prepare_concurrency must be >= 1")
        if barrier_wave_size < 1:
            raise ValueError("barrier_wave_size must be >= 1")
        if barrier_drain_timeout_s < 0:
            raise ValueError("barrier_drain_timeout_s must be >= 0")
        if client_connection_error_abort_threshold < 0:
            raise ValueError("client_connection_error_abort_threshold must be >= 0")
        self.barrier_prepare_concurrency = barrier_prepare_concurrency
        self.barrier_wave_size = barrier_wave_size
        self.barrier_drain_timeout_s = barrier_drain_timeout_s
        self.client_connection_error_abort_threshold = (
            client_connection_error_abort_threshold
        )
        self._client_connection_errors = 0
        self._client_diagnostic_lock = threading.Lock()
        self._client_resource_exhausted = threading.Event()
        self._last_write_anchors: list[AnchorWrite] = []
        self._reconciliation_candidates: list[tuple[int, str, list[str], list[str], str]] = []
        # commit barrier 准备阶段产生的 add 记录（run_commit_barrier 持有其引用）
        self._barrier_prep_records: list[RequestRecord] = []

    def _read_limiter(self, tenant_idx: int) -> RateLimiter | None:
        if self.per_tenant_rate is None:
            return self.rate_limiter
        limiter = self._tenant_rate_limiters.get(tenant_idx)
        if limiter is None:
            limiter = RateLimiter(self.per_tenant_rate)
            self._tenant_rate_limiters[tenant_idx] = limiter
        return limiter

    def _commit_limiter(self, tenant_idx: int) -> RateLimiter | None:
        if self.per_tenant_commit_rate is None:
            return self.commit_rate_limiter
        limiter = self._tenant_commit_rate_limiters.get(tenant_idx)
        if limiter is None:
            limiter = RateLimiter(self.per_tenant_commit_rate / 60.0)
            self._tenant_commit_rate_limiters[tenant_idx] = limiter
        return limiter

    def reset_client_diagnostics(self) -> None:
        """Reset per-scene client-side transport diagnostics."""
        with self._client_diagnostic_lock:
            self._client_connection_errors = 0
            self._client_resource_exhausted.clear()

    def note_client_connection_error(self) -> bool:
        """Record one client transport error and return whether to abort."""
        with self._client_diagnostic_lock:
            self._client_connection_errors += 1
            threshold = self.client_connection_error_abort_threshold
            if threshold > 0 and self._client_connection_errors >= threshold:
                self._client_resource_exhausted.set()
                return True
            return False

    def client_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics that distinguish client exhaustion from service errors."""
        with self._client_diagnostic_lock:
            count = self._client_connection_errors
            exhausted = self._client_resource_exhausted.is_set()
        return {
            "connection_errors": count,
            "abort_threshold": self.client_connection_error_abort_threshold,
            "client_resource_exhausted": exhausted,
            "verdict": "CLIENT_RESOURCE_EXHAUSTED" if exhausted else "NONE",
        }

    # -- single operations ------------------------------------------------

    def _read_once(
        self,
        client: EchoMemClient,
        query: str,
        *,
        scene_key: str,
        step_conc: int,
        tenant_idx: int,
        session_id: str = "",
        query_kind: str = "",
        expected_terms: list[str] | None = None,
    ) -> RequestRecord:
        started = time.perf_counter()
        hit_count = 0
        real_recall = False
        quality_ok = True
        degraded = False
        http_status: int | None = None
        reason_code = ""
        normalized_expected_terms = [
            str(term).strip() for term in (expected_terms or []) if str(term).strip()
        ]
        recall_matched: bool | None = None
        request_id = f"search-{uuid.uuid4().hex}"
        try:
            items, meta = _call_with_request_id(
                client.search_with_meta,
                query,
                top_k=self.top_k,
                agent_id="",
                timeout_s=self.timeout_s,
                request_id=request_id,
            )
            status, error = "ok", ""
            hit_count = len(items)
            degraded = bool(meta.get("degraded_reasons")) or str(
                meta.get("status") or ""
            ).lower() == "degraded"
            real_recall = (
                bool(meta.get("has_explain"))
                or bool(meta.get("has_debug"))
                or bool(hit_count)
            )
            if query_kind == "recall" or is_anchor_query(query):
                recall_matched = _items_match_expected_terms(
                    items, normalized_expected_terms
                )
        except Exception as exc:
            status, error = "error", classify_error(exc)
            if isinstance(exc, urllib.error.HTTPError):
                http_status = int(exc.code)
                reason_code = extract_reason_code(exc)
        if status == "ok" and (query_kind == "recall" or is_anchor_query(query)):
            # A degraded empty result is a capacity artifact (engine skipped /
            # saturated), not a recall defect: only clean empty results from
            # a pre-verified recall query are counted as quality failures.
            quality_ok = bool(recall_matched) or degraded
        return RequestRecord(
            scene_key=scene_key,
            step_conc=step_conc,
            tenant_idx=tenant_idx,
            op="read",
            stage_ms=(time.perf_counter() - started) * 1000,
            status=status,
            error_type=error,
            ts_ms=time.time() * 1000,
            start_ts_ms=time.time() * 1000 - (time.perf_counter() - started) * 1000,
            http_status=http_status,
            request_id=request_id,
            reason_code=reason_code,
            session_id=session_id,
            query=query,
            hit_count=hit_count,
            real_recall=real_recall,
            quality_ok=quality_ok,
            degraded=degraded,
            query_kind=query_kind,
            expected_terms=",".join(normalized_expected_terms),
            recall_matched=recall_matched,
        )

    # -- worker loops ------------------------------------------------------

    def _read_loop(
        self,
        stop: threading.Event,
        tenant: TenantContext,
        *,
        scene_key: str,
        step_conc: int,
        start_event: threading.Event | None = None,
    ) -> list[RequestRecord]:
        records: list[RequestRecord] = []
        queries = tenant.queries or ["hello"]
        raw_query_kinds = getattr(tenant, "query_kinds", None)
        query_kinds = (
            list(raw_query_kinds)
            if isinstance(raw_query_kinds, (list, tuple))
            else ["fallback"] * len(queries)
        )
        active_sessions = tenant.active_session_ids
        raw_expected_terms = getattr(tenant, "recall_expected_terms", {})
        expected_terms_by_query = (
            raw_expected_terms if isinstance(raw_expected_terms, dict) else {}
        )
        cursor = 0
        if start_event is not None:
            start_event.wait()
        while not stop.is_set():
            limiter = self._read_limiter(tenant.idx)
            if limiter is not None:
                limiter.acquire()
            query = queries[cursor % len(queries)]
            query_kind = query_kinds[cursor % len(query_kinds)] if query_kinds else "fallback"
            session_id = (
                active_sessions[cursor % len(active_sessions)]
                if active_sessions
                else ""
            )
            cursor += 1
            records.append(
                record := self._read_once(
                    tenant.client,
                    query,
                    scene_key=scene_key,
                    step_conc=step_conc,
                    tenant_idx=tenant.idx,
                    session_id=session_id,
                    query_kind=query_kind,
                    expected_terms=list(expected_terms_by_query.get(query) or []),
                )
            )
            if record.error_type == "connection" and self.note_client_connection_error():
                stop.set()
                break
        return records

    def _write_loop(
        self,
        stop: threading.Event,
        tenant: TenantContext,
        seq_counter: itertools.count,
        *,
        scene_key: str,
        step_conc: int,
        messages_per_session: int,
    ) -> list[RequestRecord]:
        records: list[RequestRecord] = []
        anchors: list[AnchorWrite] = []
        while not stop.is_set():
            limiter = self._commit_limiter(tenant.idx)
            if limiter is not None:
                limiter.acquire()
            seq = next(seq_counter)
            result = run_write_transaction(
                tenant.client,
                scene_key=scene_key,
                step_conc=step_conc,
                tenant_idx=tenant.idx,
                seq=seq,
                messages_per_session=messages_per_session,
                commit_poll_timeout_s=self.commit_poll_timeout_s,
                commit_poll_interval_s=self.commit_poll_interval_s,
                commit_retry_max=self.commit_retry_max,
                commit_retry_backoff_s=self.commit_retry_backoff_s,
            )
            records.extend(result.records)
            if any(
                record.error_type == "connection" and self.note_client_connection_error()
                for record in result.records
            ):
                stop.set()
                break
            if result.ok:
                anchors.append((tenant.idx, result.session_id, result.anchor))
                self._reconciliation_candidates.append(
                    (
                        tenant.idx,
                        result.session_id,
                        result.message_ids,
                        result.content_hashes,
                        result.archive_id,
                    )
                )
        self._last_write_anchors.extend(anchors)
        return records

    # -- scene runner ------------------------------------------------------

    def run_scene(
        self,
        scene: SceneRun,
        tenants: list[TenantContext],
        messages_per_session: int,
    ) -> SceneResult:
        """Run one scene for its duration and return all records."""
        self.reset_client_diagnostics()
        scene_key = scene.key
        total_workers = len(tenants) * scene.per_tenant_conc
        has_commit_stream = (
            self.commit_rate_limiter is not None
            or self.per_tenant_commit_rate is not None
        )
        # A rate-based K scene has independent read and commit streams. With
        # one tenant/worker, integer 1:1 splitting otherwise creates only a
        # writer, producing a misleading "completed" run with zero Search.
        if (
            scene.scene_id == "K"
            and self.rate_limiter is not None
            and has_commit_stream
        ):
            total_workers = max(2, total_workers)
        stop = threading.Event()
        started_wall = time.time()
        burst_start: float | None = None
        burst_end: float | None = None

        def tenant_for(index: int) -> TenantContext:
            return tenants[index % len(tenants)]

        if scene.scene_id in ("A", "D"):
            read_count, write_count = total_workers, 0
        elif scene.scene_id == "B":
            read_count, write_count = 0, total_workers
        elif scene.scene_id == "K" and not has_commit_stream:
            # K is also used by the capacity ladder.  An explicit
            # ``--commit-rpm 0`` means Search-only measurement; treating it
            # as the normal mixed split would still launch writer threads and
            # make capacity results depend on unrelated Commit timeouts.
            read_count, write_count = total_workers, 0
        else:  # C
            read_count, write_count = split_threads(total_workers, scene.mix or (1, 1))
        self._last_write_anchors.clear()
        self._reconciliation_candidates.clear()

        futures: list[Any] = []
        with ThreadPoolExecutor(
            max_workers=total_workers, thread_name_prefix="perf-load"
        ) as pool:
            seq_counter = itertools.count()
            for index in range(read_count):
                tenant = tenant_for(index)
                futures.append(
                    pool.submit(
                        self._read_loop,
                        stop,
                        tenant,
                        scene_key=scene_key,
                        step_conc=scene.per_tenant_conc,
                    )
                )
            for index in range(read_count, read_count + write_count):
                tenant = tenant_for(index)
                futures.append(
                    pool.submit(
                        self._write_loop,
                        stop,
                        tenant,
                        seq_counter,
                        scene_key=scene_key,
                        step_conc=scene.per_tenant_conc,
                        messages_per_session=messages_per_session,
                    )
                )

            if scene.scene_id == "D":
                delay = max(0.0, scene.duration_s - scene.burst_window_s) / 2.0
                time.sleep(delay)
                burst_start = time.time()
                burst_records = self._run_burst(
                    scene,
                    tenants,
                    messages_per_session,
                    burst_tenant_idx=tenants[0].idx if tenants else None,
                )
                burst_end = time.time()
                remaining = started_wall + scene.duration_s - time.time()
                if remaining > 0:
                    time.sleep(remaining)
            else:
                time.sleep(scene.duration_s)

            stop.set()
            wait(futures)

        records: list[RequestRecord] = []
        for future in futures:
            records.extend(future.result())
        if scene.scene_id == "D":
            records.extend(burst_records)  # type: ignore[name-defined]
        return SceneResult(
            scene_key=scene_key,
            records=records,
            wall_s=time.time() - started_wall,
            burst_start_s=burst_start,
            burst_end_s=burst_end,
        )

    def _run_burst(
        self,
        scene: SceneRun,
        tenants: list[TenantContext],
        messages_per_session: int,
        burst_tenant_idx: int | None = None,
    ) -> list[RequestRecord]:
        """Saturate the server with K parallel write transactions (scene D).

        When ``burst_tenant_idx`` is set, every burst write uses that one
        tenant, so isolation granularity can compare same-tenant vs
        cross-tenant read latency inside the burst window.
        """
        count = scene.burst_commits
        step_conc = scene.per_tenant_conc
        records: list[RequestRecord] = []
        seq_counter = itertools.count()

        def submit(client: Any, tenant_idx: int) -> Any:
            return pool.submit(
                run_write_transaction,
                client,
                scene_key=scene.key,
                step_conc=step_conc,
                tenant_idx=tenant_idx,
                seq=next(seq_counter),
                messages_per_session=messages_per_session,
                commit_poll_timeout_s=self.commit_poll_timeout_s,
                commit_poll_interval_s=self.commit_poll_interval_s,
                extra="burst",
                commit_retry_max=self.commit_retry_max,
                commit_retry_backoff_s=self.commit_retry_backoff_s,
            )

        with ThreadPoolExecutor(
            max_workers=min(count, 8), thread_name_prefix="perf-burst"
        ) as pool:
            if burst_tenant_idx is not None:
                tenant = tenants[burst_tenant_idx % len(tenants)]
                futures = [submit(tenant.client, tenant.idx) for _ in range(count)]
            else:
                futures = [
                    submit(
                        tenants[i % len(tenants)].client,
                        tenants[i % len(tenants)].idx,
                    )
                    for i in range(count)
                ]
            for future in futures:
                result = future.result()
                records.extend(result.records)
        return records

    # -- write-read consistency probing -------------------------------------

    def run_consistency_checks(
        self,
        tenants: list[TenantContext],
        *,
        scene_key: str,
        step_conc: int,
        max_checks: int = 3,
        max_wait_s: float = 30.0,
    ) -> list[RequestRecord]:
        """Probe how long committed content takes to become searchable.

        Uses the anchors of the most recently completed write transactions.
        """
        records: list[RequestRecord] = []
        for tenant_idx, session_id, anchor in self._last_write_anchors[-max_checks:]:
            client = tenants[tenant_idx].client
            started = time.perf_counter()
            deadline = time.monotonic() + max_wait_s
            hit = False
            while time.monotonic() < deadline:
                try:
                    items = client.search(anchor, top_k=self.top_k, timeout_s=self.timeout_s)
                except Exception as exc:
                    records.append(
                        RequestRecord(
                            scene_key=scene_key,
                            step_conc=step_conc,
                            tenant_idx=tenant_idx,
                            op="consistent_check",
                            stage_ms=(time.perf_counter() - started) * 1000,
                            status="error",
                            error_type=classify_error(exc),
                            ts_ms=time.time() * 1000,
                            session_id=session_id,
                        )
                    )
                    break
                if any(
                    anchor in (item.content or "") or anchor in (item.uri or "")
                    for item in items
                ):
                    hit = True
                    break
                time.sleep(0.5)
            if hit:
                records.append(
                    RequestRecord(
                        scene_key=scene_key,
                        step_conc=step_conc,
                        tenant_idx=tenant_idx,
                        op="consistent_check",
                        stage_ms=(time.perf_counter() - started) * 1000,
                        status="ok",
                        error_type="",
                        ts_ms=time.time() * 1000,
                        session_id=session_id,
                    )
                )
            else:
                records.append(
                    RequestRecord(
                        scene_key=scene_key,
                        step_conc=step_conc,
                        tenant_idx=tenant_idx,
                        op="consistent_check",
                        stage_ms=(time.perf_counter() - started) * 1000,
                        status="error",
                        error_type="consistency_timeout",
                        ts_ms=time.time() * 1000,
                        session_id=session_id,
                    )
                )
        return records

    # -- commit barrier（预提交会话 + 并发 commit 风暴） --------------------

    def _barrier_tenant_counts(self, scene: SceneRun, tenant_count: int) -> list[int]:
        """按 ``scene.barrier_distribution`` 把 ``barrier_commits`` 分给各租户。

        uniform: 均分（余数给前几个）；zipf: rank 1..N 权重 1/rank^s 归一后
        按比例取整（余数补首位）；explicit: 直接用 ``barrier_tenant_counts``
        （长度必须 == 租户数，总和即 barrier_commits）。
        """
        total = scene.barrier_commits
        distribution = scene.barrier_distribution
        if tenant_count < 1:
            raise ValueError("commit barrier 需要至少一个租户")
        if distribution == "uniform":
            base, remainder = divmod(total, tenant_count)
            return [base + (1 if index < remainder else 0) for index in range(tenant_count)]
        if distribution == "zipf":
            exponent = scene.barrier_zipf_exponent
            weights = [1.0 / (rank ** exponent) for rank in range(1, tenant_count + 1)]
            weight_sum = sum(weights)
            counts = [int(total * weight / weight_sum) for weight in weights]
            counts[0] += total - sum(counts)
            return counts
        if distribution == "explicit":
            counts = list(scene.barrier_tenant_counts or [])
            if len(counts) != tenant_count:
                raise ValueError(
                    f"explicit barrier 分布需要 {tenant_count} 个租户计数，"
                    f"实际 {len(counts)}"
                )
            if sum(counts) != total:
                raise ValueError(
                    f"explicit barrier 计数总和 {sum(counts)} != "
                    f"barrier_commits {total}"
                )
            return counts
        raise ValueError(f"unknown barrier distribution: {distribution}")

    def prepare_write_sessions(
        self,
        tenant: TenantContext,
        count: int,
        messages_per_session: int,
        *,
        scene_key: str,
        step_conc: int,
        extra: str = "",
    ) -> list[PreparedWrite]:
        """并发准备 barrier 会话；每个 worker 内仍按会话串行写入。"""
        if count <= 1 or self.barrier_prepare_concurrency <= 1:
            return self._prepare_write_sessions_serial(
                tenant,
                count,
                messages_per_session,
                scene_key=scene_key,
                step_conc=step_conc,
                extra=extra,
                start_seq=0,
            )
        chunk_size = max(
            1,
            (count + self.barrier_prepare_concurrency - 1)
            // self.barrier_prepare_concurrency,
        )
        chunks = [
            (start, min(chunk_size, count - start))
            for start in range(0, count, chunk_size)
        ]
        with ThreadPoolExecutor(
            max_workers=min(self.barrier_prepare_concurrency, len(chunks)),
            thread_name_prefix="perf-barrier-prep",
        ) as pool:
            futures = [
                pool.submit(
                    self._prepare_write_sessions_serial,
                    tenant,
                    chunk_count,
                    messages_per_session,
                    scene_key=scene_key,
                    step_conc=step_conc,
                    extra=extra,
                    start_seq=start,
                )
                for start, chunk_count in chunks
            ]
            prepared: list[PreparedWrite] = []
            for future in futures:
                prepared.extend(future.result())
            return prepared

    def _prepare_write_sessions_serial(
        self,
        tenant: TenantContext,
        count: int,
        messages_per_session: int,
        *,
        scene_key: str,
        step_conc: int,
        extra: str = "",
        start_seq: int = 0,
    ) -> list[PreparedWrite]:
        """为单个租户准备 count 个「已 open + 已 add 消息、未 commit」的会话。

        内容与 :func:`run_write_transaction` 一致（末条携带 PERFTAIL 锚词，
        其余消息唯一）。每个会话 open + 逐条 add，add 记录（op="add"，
        status/error_type 同现有）写入 ``self._barrier_prep_records``；
        open/add 失败时给该 session 记录 error 并跳过（不 abort 整体）。
        """
        prepared: list[PreparedWrite] = []
        records = self._barrier_prep_records
        seq_counter = itertools.count(start_seq)
        for _ in range(count):
            seq = next(seq_counter)
            anchor = f"{WRITE_ANCHOR_PREFIX}-{tenant.idx}-{seq}"
            session_id = ""
            try:
                session_id = tenant.client.open_session(title="perf-barrier-prep")
            except Exception as exc:
                records.append(
                    RequestRecord(
                        scene_key=scene_key,
                        step_conc=step_conc,
                        tenant_idx=tenant.idx,
                        op="open",
                        stage_ms=0.0,
                        status="error",
                        error_type=classify_error(exc),
                        ts_ms=time.time() * 1000,
                        extra=extra,
                    )
                )
                continue
            records.append(
                RequestRecord(
                    scene_key=scene_key,
                    step_conc=step_conc,
                    tenant_idx=tenant.idx,
                    op="open",
                    stage_ms=0.0,
                    status="ok",
                    error_type="",
                    ts_ms=time.time() * 1000,
                    session_id=session_id,
                    extra=extra,
                )
            )
            message_ids: list[str] = []
            content_hashes: list[str] = []
            failed = False
            for msg_idx in range(messages_per_session):
                last = msg_idx == messages_per_session - 1
                content = (
                    f"压测写入会话消息 {anchor}-{msg_idx}"
                    if last
                    else f"压测写入会话消息-{msg_idx}"
                )
                started = time.perf_counter()
                try:
                    resp = tenant.client.add_message(session_id, "user", content)
                except Exception as exc:
                    records.append(
                        RequestRecord(
                            scene_key=scene_key,
                            step_conc=step_conc,
                            tenant_idx=tenant.idx,
                            op="add",
                            stage_ms=(time.perf_counter() - started) * 1000,
                            status="error",
                            error_type=classify_error(exc),
                            ts_ms=time.time() * 1000,
                            session_id=session_id,
                            extra=extra,
                        )
                    )
                    failed = True
                    break
                message_id = (
                    str(
                        resp.get("message_id")
                        or resp.get("id")
                        or resp.get("msg_id")
                        or ""
                    )
                    if isinstance(resp, dict)
                    else ""
                )
                message_ids.append(message_id)
                content_hashes.append(_content_hash(content))
                records.append(
                    RequestRecord(
                        scene_key=scene_key,
                        step_conc=step_conc,
                        tenant_idx=tenant.idx,
                        op="add",
                        stage_ms=(time.perf_counter() - started) * 1000,
                        status="ok",
                        error_type="",
                        ts_ms=time.time() * 1000,
                        session_id=session_id,
                        extra=extra,
                        message_id=message_id,
                        content_hash=_content_hash(content),
                        content_bytes=len(content.encode("utf-8")),
                    )
                )
            if failed:
                continue
            prepared.append(
                PreparedWrite(
                    client=tenant.client,
                    tenant_idx=tenant.idx,
                    session_id=session_id,
                    message_ids=message_ids,
                    content_hashes=content_hashes,
                    anchor=anchor,
                )
            )
        return prepared

    def run_commit_barrier(
        self,
        scene: SceneRun,
        tenants: list[TenantContext],
        messages_per_session: int,
    ) -> list[RequestRecord]:
        """对所有 PreparedWrite 并发 commit，并轮询完成。

        1. 按 ``scene.barrier_distribution`` 计算每租户 commit 数；
        2. ``prepare_write_sessions`` 准备全部会话（计时窗外）；
        3. 线程池（max_workers=min(总 commit 数, 64)）并发 commit_session，
           每条 op="commit_submit" 记录（含 retry_after_s/reason_code/extra="barrier"）；
        4. 对所有成功 commit 轮询完成（op="commit_done"）。
        """
        if scene.barrier_commits <= 0:
            raise ValueError("barrier_commits must be > 0")
        prepared, records = self.prepare_commit_barrier(
            scene, tenants, messages_per_session
        )
        return self.commit_prepared_barrier(scene, prepared, records)

    def prepare_commit_barrier(
        self,
        scene: SceneRun,
        tenants: list[TenantContext],
        messages_per_session: int,
    ) -> tuple[list[PreparedWrite], list[RequestRecord]]:
        """Prepare barrier sessions outside the measured contention window.

        Preparation performs real ``open``/``add`` calls and may invoke the
        embedding/LLM path.  It must be separated from the Search-vs-Commit
        pressure window; otherwise slow model-backed preparation can consume
        the case timeout before any Commit request is submitted.
        """
        records: list[RequestRecord] = []
        self._barrier_prep_records = records
        try:
            counts = self._barrier_tenant_counts(scene, len(tenants))
            prep_jobs = [
                (tenant, count)
                for tenant, count in zip(tenants, counts)
                if count > 0
            ]
            prepared: list[PreparedWrite] = []
            if prep_jobs:
                with ThreadPoolExecutor(
                    max_workers=min(len(prep_jobs), self.barrier_prepare_concurrency),
                    thread_name_prefix="perf-tenant-prep",
                ) as pool:
                    futures = [
                        pool.submit(
                            self.prepare_write_sessions,
                            tenant,
                            count,
                            messages_per_session,
                            scene_key=scene.key,
                            step_conc=scene.per_tenant_conc,
                            extra="barrier",
                        )
                        for tenant, count in prep_jobs
                    ]
                    for future in futures:
                        prepared.extend(future.result())
            return prepared, records
        finally:
            self._barrier_prep_records = []

    def commit_prepared_barrier(
        self,
        scene: SceneRun,
        prepared: list[PreparedWrite],
        records: list[RequestRecord],
        *,
        poll_timeout_s: float | None = None,
    ) -> list[RequestRecord]:
        """Submit and poll already-prepared barrier sessions.

        This is deliberately separate from :meth:`prepare_commit_barrier` so
        callers can begin Search and Commit at the same wall-clock instant.
        """
        total = len(prepared)
        effective_poll_timeout_s = (
            self.commit_poll_timeout_s
            if poll_timeout_s is None
            else max(0.0, min(self.commit_poll_timeout_s, poll_timeout_s))
        )

        def submit(pre: PreparedWrite) -> None:
            started = time.perf_counter()
            try:
                archive_id = pre.client.commit_session(pre.session_id)
            except Exception as exc:
                records.append(
                    RequestRecord(
                        scene_key=scene.key,
                        step_conc=scene.per_tenant_conc,
                        tenant_idx=pre.tenant_idx,
                        op="commit_submit",
                        stage_ms=(time.perf_counter() - started) * 1000,
                        status="error",
                        error_type=classify_error(exc),
                        ts_ms=time.time() * 1000,
                        session_id=pre.session_id,
                        extra="barrier",
                        retry_after_s=_retry_after_seconds(exc),
                        reason_code=extract_reason_code(exc),
                    )
                )
                return
            pre.archive_id = archive_id
            records.append(
                RequestRecord(
                    scene_key=scene.key,
                    step_conc=scene.per_tenant_conc,
                    tenant_idx=pre.tenant_idx,
                    op="commit_submit",
                    stage_ms=(time.perf_counter() - started) * 1000,
                    status="ok",
                    error_type="",
                    ts_ms=time.time() * 1000,
                    session_id=pre.session_id,
                    archive_id=archive_id,
                    extra="barrier",
                    retry_after_s=None,
                    reason_code="",
                )
            )

        if total:
            with ThreadPoolExecutor(
                # The barrier still records all requested commits, but limits
                # in-flight work so a 128/260 request case does not turn into
                # an uncontrolled memory spike on the target service.
                max_workers=min(total, 64, self.barrier_wave_size),
                thread_name_prefix="perf-barrier",
            ) as pool:
                futures = [pool.submit(submit, pre) for pre in prepared]
                for future in futures:
                    future.result()

        def poll_one(prepared_write: PreparedWrite) -> None:
            if not prepared_write.archive_id:
                return
            started = time.perf_counter()
            try:
                commit = prepared_write.client.poll_commit(
                    prepared_write.session_id,
                    prepared_write.archive_id,
                    timeout_s=effective_poll_timeout_s,
                    poll_interval_s=self.commit_poll_interval_s,
                )
            except Exception as exc:
                records.append(
                    RequestRecord(
                        scene_key=scene.key,
                        step_conc=scene.per_tenant_conc,
                        tenant_idx=prepared_write.tenant_idx,
                        op="commit_done",
                        stage_ms=(time.perf_counter() - started) * 1000,
                        status="error",
                        error_type=classify_error(exc),
                        ts_ms=time.time() * 1000,
                        session_id=prepared_write.session_id,
                        archive_id=prepared_write.archive_id,
                        extra="barrier",
                    )
                )
                return
            status = "ok" if commit.status == "completed" else "error"
            error_type = (
                ""
                if commit.status == "completed"
                else "commit_timeout"
                if commit.status == "timeout"
                else "commit_failed"
            )
            records.append(
                RequestRecord(
                    scene_key=scene.key,
                    step_conc=scene.per_tenant_conc,
                    tenant_idx=prepared_write.tenant_idx,
                    op="commit_done",
                    stage_ms=commit.elapsed_s * 1000,
                    status=status,
                    error_type=error_type,
                    ts_ms=time.time() * 1000,
                    session_id=prepared_write.session_id,
                    archive_id=prepared_write.archive_id,
                    extra="barrier",
                )
            )
            if status == "ok" and not prepared_write.reconciliation_registered:
                self._reconciliation_candidates.append(
                    (
                        prepared_write.tenant_idx,
                        prepared_write.session_id,
                        prepared_write.message_ids,
                        prepared_write.content_hashes,
                        prepared_write.archive_id,
                    )
                )
                prepared_write.reconciliation_registered = True

        pollable = [pre for pre in prepared if pre.archive_id]
        if pollable:
            # Keep status polling concurrent with submission. Serial polling
            # makes independent Commit waits add up to N*timeout.
            with ThreadPoolExecutor(
                max_workers=min(len(pollable), 64, self.barrier_wave_size),
                thread_name_prefix="perf-barrier-poll",
            ) as pool:
                futures = [pool.submit(poll_one, pre) for pre in pollable]
                for future in futures:
                    future.result()
        return records

    def run_commit_barrier_window(
        self,
        scene: SceneRun,
        tenants: list[TenantContext],
        messages_per_session: int,
        *,
        drain_timeout_s: float | None = None,
    ) -> list[RequestRecord]:
        """Prepare and submit a barrier, then drain status for a bounded window.

        Accepted Commit submissions remain visible even when model-backed
        completion is slow. Unfinished statuses are explicitly recorded as
        ``commit_timeout`` rather than extending the measured Search window.
        """
        prepared, records = self.prepare_commit_barrier(
            scene, tenants, messages_per_session
        )
        return self.commit_prepared_barrier(
            scene,
            prepared,
            records,
            poll_timeout_s=(
                self.barrier_drain_timeout_s
                if drain_timeout_s is None
                else drain_timeout_s
            ),
        )

    # -- N×N 隔离探针 ------------------------------------------------------

    def run_nxn_isolation_probe(
        self,
        tenants: list[TenantContext],
        markers_per_tenant: int = 5,
    ) -> tuple[list[RequestRecord], dict[str, Any]]:
        """N×N 租户隔离探针：每个 writer 写私有 marker，验证同租户命中/跨租户不命中。

        语义照搬 stress ``run_isolation_probe``：每个 (writer, marker, reader)
        三元组做一次精确 marker 查询，同租户期望命中（可重试 2 次、间隔 1s），
        跨租户期望不命中（1 次）。写阶段失败记环境错误记录并继续下一租户。
        返回 (records, summary_dict)。
        """
        records: list[RequestRecord] = []
        marker_count = max(1, int(markers_per_tenant))

        def env_error(tenant_idx: int, reason: str, error_type: str = "other") -> None:
            records.append(
                RequestRecord(
                    scene_key="I@1",
                    step_conc=1,
                    tenant_idx=tenant_idx,
                    op="isolation_probe",
                    stage_ms=0.0,
                    status="error",
                    error_type=error_type,
                    ts_ms=time.time() * 1000,
                    extra=json.dumps({"reason": reason}),
                )
            )

        if len(tenants) < 2:
            return records, {
                "status": "INCONCLUSIVE",
                "reason": "requires at least two tenants",
                "probe_count": 0,
                "markers_per_tenant": marker_count,
                "expected_probe_count": 0,
                "invalid_probe_count": 0,
                "same_tenant_hits": 0,
                "same_tenant_total": 0,
                "cross_tenant_false_positives": 0,
                "cross_tenant_total": 0,
            }

        writers: dict[int, dict[str, Any]] = {}
        for tenant in tenants:
            writer = tenant.idx
            client = tenant.client
            markers = [
                f"echomem-isolation-{writer}-{uuid.uuid4().hex}"
                for _ in range(marker_count)
            ]
            try:
                session_id = client.open_session(title="perf-isolation-writer")
            except Exception as exc:
                env_error(writer, f"writer {writer} open_session failed", classify_error(exc))
                continue
            try:
                for marker in markers:
                    client.add_message(
                        session_id, "user", f"Tenant {writer} private marker {marker}"
                    )
            except Exception as exc:
                env_error(writer, f"writer {writer} add_message failed", classify_error(exc))
                continue
            try:
                archive_id = client.commit_session(session_id)
            except Exception as exc:
                env_error(writer, f"writer {writer} commit failed", classify_error(exc))
                continue
            try:
                commit = client.poll_commit(
                    session_id,
                    archive_id,
                    timeout_s=self.commit_poll_timeout_s,
                    poll_interval_s=self.commit_poll_interval_s,
                )
            except Exception as exc:
                env_error(writer, f"writer {writer} poll failed", classify_error(exc))
                continue
            if commit.status != "completed":
                env_error(writer, f"writer {writer} commit not completed: {commit.status}")
                continue
            writers[writer] = {"markers": markers, "session_id": session_id}

        for writer, writer_data in writers.items():
            for marker in writer_data["markers"]:
                for reader_tenant in tenants:
                    reader = reader_tenant.idx
                    same_tenant = writer == reader
                    attempts = 2 if same_tenant else 1
                    found = False
                    latency_ms = 0.0
                    try:
                        for attempt in range(attempts):
                            started = time.perf_counter()
                            items, _ = reader_tenant.client.search_with_meta(
                                marker, top_k=5, agent_id="", timeout_s=self.timeout_s
                            )
                            latency_ms = (time.perf_counter() - started) * 1000
                            found = any(
                                marker in (item.content or "") or marker in (item.uri or "")
                                for item in items
                            )
                            if found or attempt + 1 >= attempts:
                                break
                            time.sleep(1.0)
                    except Exception as exc:
                        records.append(
                            RequestRecord(
                                scene_key="I@1",
                                step_conc=1,
                                tenant_idx=reader,
                                op="isolation_probe",
                                stage_ms=latency_ms,
                                status="error",
                                error_type=classify_error(exc),
                                ts_ms=time.time() * 1000,
                                session_id=writer_data["session_id"],
                                extra=json.dumps(
                                    {
                                        "writer": writer,
                                        "reader": reader,
                                        "same_tenant": same_tenant,
                                        "marker_found": False,
                                        "expected": same_tenant,
                                        "latency_ms": round(latency_ms, 3),
                                    }
                                ),
                            )
                        )
                        continue
                    records.append(
                        RequestRecord(
                            scene_key="I@1",
                            step_conc=1,
                            tenant_idx=reader,
                            op="isolation_probe",
                            stage_ms=latency_ms,
                            status="ok",
                            error_type="",
                            ts_ms=time.time() * 1000,
                            session_id=writer_data["session_id"],
                            extra=json.dumps(
                                {
                                    "writer": writer,
                                    "reader": reader,
                                    "same_tenant": same_tenant,
                                    "marker_found": found,
                                    "expected": same_tenant,
                                    "latency_ms": round(latency_ms, 3),
                                }
                            ),
                        )
                    )

        invalid = 0
        same_hits = 0
        same_total = 0
        cross_fp = 0
        cross_total = 0
        for rec in records:
            if rec.status != "ok":
                continue
            try:
                info = json.loads(rec.extra or "")
            except ValueError:
                continue
            if not isinstance(info, dict):
                continue
            found = bool(info.get("marker_found"))
            expected = bool(info.get("expected"))
            if found != expected:
                invalid += 1
            if info.get("same_tenant"):
                same_total += 1
                same_hits += 1 if found else 0
            else:
                cross_total += 1
                cross_fp += 1 if found else 0
        expected_probe_count = len(writers) * len(tenants) * marker_count
        interrupted = any(rec.status == "error" for rec in records)
        if interrupted:
            status = "INCONCLUSIVE"
        elif invalid or len(records) != expected_probe_count:
            status = "FAIL"
        else:
            status = "PASS"
        summary_dict: dict[str, Any] = {
            "status": status,
            "reason": (
                "探针执行中断"
                if interrupted
                else (
                    f"{invalid} 条探针命中与预期不符"
                    if invalid
                    else "全部同租户命中、跨租户不命中"
                )
            ),
            "probe_count": len(records),
            "markers_per_tenant": marker_count,
            "expected_probe_count": expected_probe_count,
            "invalid_probe_count": invalid,
            "same_tenant_hits": same_hits,
            "same_tenant_total": same_total,
            "cross_tenant_false_positives": cross_fp,
            "cross_tenant_total": cross_total,
        }
        return records, summary_dict

    # -- message-level reconciliation --------------------------------------

    def run_reconciliation(
        self,
        tenants: list[TenantContext],
        *,
        max_sessions: int = 20,
    ) -> list[dict[str, Any]]:
        """Collect per-session reconciliation data for the write audit.

        For the most recently completed write transactions this pulls the
        server-side message list, archive terminal state and (when exposed)
        the atom source turn ids, alongside the client-side injected message
        ids and content hashes. Judgment is a pure function in
        ``metrics_calc.reconcile_messages``; endpoint failures are recorded
        as ``*_available=False`` instead of aborting.
        """
        data: list[dict[str, Any]] = []
        candidates = self._reconciliation_candidates[-max_sessions:]
        # Keep reconciliation scoped to the just-finished scene. Without
        # draining this list, later scenes repeatedly re-read old sessions
        # and can hide which workload produced a persistence gap.
        self._reconciliation_candidates = []
        for tenant_idx, session_id, client_ids, client_hashes, archive_id in candidates:
            client = tenants[tenant_idx].client
            entry: dict[str, Any] = {
                "tenant_idx": tenant_idx,
                "session_id": session_id,
                "client_ids": client_ids,
                "client_hashes": client_hashes,
                "archive_id": archive_id,
                "server_ids": [],
                "server_hashes": [],
                "archive_status": "",
                "atom_source_turn_ids": [],
                "history_available": False,
                "archive_available": False,
                "atoms_available": False,
            }
            try:
                history = client.session_history(
                    session_id, limit=max(200, len(client_hashes))
                )
                entry["server_ids"] = [
                    str(item.get("id") or "") for item in history if item.get("id")
                ]
                entry["server_hashes"] = [
                    _content_hash(str(item.get("content") or "")) for item in history
                ]
                entry["history_available"] = True
            except Exception as exc:
                logger.warning("对账 history 获取失败 session=%s: %s", session_id, exc)
            try:
                archives = client.session_archives(session_id, limit=50)
                # The server exposes committed archives without a status
                # field: an archive present in this list has reached its
                # terminal committed state.
                entry["archive_status"] = "completed" if archives else ""
                entry["archive_available"] = True
            except Exception as exc:
                logger.warning("对账 archives 获取失败 session=%s: %s", session_id, exc)
            if archive_id:
                try:
                    memories = client.commit_memories(session_id, archive_id)
                    entry["atom_source_turn_ids"] = _extract_source_turn_ids(memories)
                    entry["atoms_available"] = True
                except Exception as exc:
                    logger.warning(
                        "对账 commit_memories 获取失败 session=%s: %s", session_id, exc
                    )
            data.append(entry)
        return data


def _extract_source_turn_ids(memories: dict[str, Any]) -> list[str]:
    """Collect atom source_turn_ids from a commit memories payload, if any.

    The payload shape is not contractual across servers; both a flat
    ``source_turn_ids`` list and a nested atoms list are tolerated. Missing
    data yields an empty list (reconciliation marks atoms as unavailable).
    """
    ids: list[str] = []
    for key in ("source_turn_ids", "source_ids", "message_ids"):
        value = memories.get(key)
        if isinstance(value, list):
            ids.extend(str(item) for item in value)
    atoms = memories.get("atoms") or memories.get("items") or []
    if isinstance(atoms, list):
        for atom in atoms:
            if not isinstance(atom, dict):
                continue
            for key in ("source_turn_ids", "source_ids", "message_ids"):
                value = atom.get(key)
                if isinstance(value, list):
                    ids.extend(str(item) for item in value)
    return [item for item in ids if item]
