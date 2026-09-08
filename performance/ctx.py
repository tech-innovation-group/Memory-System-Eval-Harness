"""Scene API: the single interface scenario code interacts with.

A scene file imports :class:`Ctx` and defines plain functions that take
one ``ctx`` argument.  The engine drives those functions on a worker
pool; the ``ctx`` object provides the seven primitives a scenario needs
(HTTP request, assertion, poll, data pick, runtime identity, phase
injection, profile read) plus free-form recording.  Everything here is
shared, engine-agnostic machinery — scenario authors never reimplement
transport, timing or classification.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from performance.records import RequestRecord

# 服务端拒绝原因在响应中的别名（header 按小写匹配，payload 按原样匹配）。
_REASON_CODE_ALIASES = (
    "reason_code",
    "reasonCode",
    "error_code",
    "errorCode",
    "x-reason-code",
)
_REASON_CODE_HEADER_ALIASES = tuple(alias.lower() for alias in _REASON_CODE_ALIASES)


class AssertionFailure(RuntimeError):
    """Raised by :meth:`Response.require_status` on an unexpected HTTP status."""


@dataclass
class Response:
    """One measured HTTP response.  Already recorded by the engine."""

    op: str
    status: str  # ok | error
    error_type: str
    http_status: int | None
    elapsed_ms: float
    body: dict[str, Any] | None  # parsed JSON, or None when not JSON
    body_text: str
    record: RequestRecord

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def json(self) -> dict[str, Any] | None:
        return self.body

    def require_status(self, code: int) -> "Response":
        """Raise :class:`AssertionFailure` unless the HTTP status matches."""
        if self.http_status != code:
            raise AssertionFailure(
                f"{self.op}: expected HTTP {code}, got {self.http_status}"
            )
        return self


@dataclass
class PollResult:
    """Outcome of a poll-until loop (commit_done etc.).  Already recorded."""

    op: str
    status: str  # completed | timeout | failed | stopped
    elapsed_ms: float
    polls: int
    body: dict[str, Any] | None
    record: RequestRecord | None


PROBE_STATUSES = ("PASS", "FAIL", "NOT_IMPLEMENTED", "INCONCLUSIVE")


@dataclass
class ProbeCheck:
    """One assertion result produced by a probe run.

    ``status`` is one of the four probe outcomes: ``PASS`` / ``FAIL`` /
    ``NOT_IMPLEMENTED`` / ``INCONCLUSIVE``.  ``detail`` carries the raw
    observation (payload, error text) without polluting ``reason``.
    """

    name: str
    status: str
    reason: str = ""
    elapsed_s: float | None = None
    detail: str = ""


@dataclass
class Phase:
    """A one-shot load injection scheduled at a wall-clock offset."""

    at_s: float  # seconds from scene start
    fn: Callable[["Ctx"], None]
    count: int
    max_workers: int
    name: str = "phase"
    tenant_idx: int | None = None
    tenant_counts: dict[int, int] | None = None


class Ctx:
    """Per-worker scene context handed to every task function call.

    Constructed by the engine; scenario code only reads it.
    """

    def __init__(
        self,
        *,
        scene: str,
        worker_id: int,
        tenant_idx: int,
        headers: dict[str, str],
        base_url: str,
        read_timeout_s: float,
        params: dict[str, Any],
        duration_s: float,
        stop: threading.Event,
        record_fn: Callable[[RequestRecord], None],
        seq_fn: Callable[[], int],
        choose_fn: Callable[[Sequence[Any]], Any],
        phases: list[Phase],
        checks: list[ProbeCheck] | None = None,
        extra: str = "",
        tenant_count: int = 1,
    ):
        self._scene = scene
        self._worker_id = worker_id
        self._tenant_idx = tenant_idx
        self._headers = headers
        self._base_url = base_url.rstrip("/")
        self._read_timeout_s = read_timeout_s
        self._params = params
        self._duration_s = duration_s
        self._stop = stop
        self._record_fn = record_fn
        self._seq_fn = seq_fn
        self._choose_fn = choose_fn
        self._phases = phases
        self._checks = checks
        self.extra = extra
        self._last_record: RequestRecord | None = None
        self._tenant_count = max(1, tenant_count)

    # -- runtime identity / data ------------------------------------------

    @property
    def worker_id(self) -> int:
        return self._worker_id

    @property
    def tenant_idx(self) -> int:
        return self._tenant_idx

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def params(self) -> dict[str, Any]:
        return self._params

    @property
    def duration_s(self) -> float:
        return self._duration_s

    @property
    def tenant_count(self) -> int:
        """Number of tenants in the profile (>= 1)."""
        return self._tenant_count

    def next_seq(self) -> int:
        """Next value of the scene-wide shared sequence counter."""
        return self._seq_fn()

    def choose(self, items: Sequence[Any]) -> Any:
        """Pick the next item in round-robin order (thread-safe)."""
        return self._choose_fn(items)

    # -- request primitives ------------------------------------------------

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        op: str | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
        extra: str | None = None,
        **fields: Any,
    ) -> Response:
        return self.request("POST", path, body=body, op=op, params=params,
                            headers=headers, timeout_s=timeout_s, extra=extra, **fields)

    def get(
        self,
        path: str,
        *,
        op: str | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
        extra: str | None = None,
        **fields: Any,
    ) -> Response:
        return self.request("GET", path, op=op, params=params, headers=headers,
                            timeout_s=timeout_s, extra=extra, **fields)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        op: str | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
        extra: str | None = None,
        **fields: Any,
    ) -> Response:
        """Send one request, measure it, record it, and return a Response.

        Every transport outcome (HTTP error, timeout, connection error) is
        recorded as an ``error`` record and returned — it never raises.
        ``fields`` are attached to the produced record (e.g. ``query``,
        ``session_id``, ``content_hash``); response-derived fields can be
        attached afterwards with :meth:`note`.
        """
        op = op or _default_op(path)
        started = time.perf_counter()
        try:
            status, error_type, http_status, body_text, body_json, reason = _do_request(
                self._base_url,
                method,
                path,
                body=body,
                params=params,
                headers=headers,
                base_headers=self._headers,
                timeout_s=timeout_s if timeout_s is not None else self._read_timeout_s,
            )
        except TransportError as exc:
            status, error_type, http_status, body_text, body_json, reason = (
                "error", exc.error_type, None, "", None, "",
            )
        record = self._make_record(
            op=op,
            stage_ms=(time.perf_counter() - started) * 1000,
            status=status,
            error_type=error_type,
            http_status=http_status,
            extra=extra if extra is not None else self.extra,
            **fields,
        )
        record.reason_code = reason
        self._emit(record)
        return Response(
            op=op,
            status=status,
            error_type=error_type,
            http_status=http_status,
            elapsed_ms=record.stage_ms,
            body=body_json,
            body_text=body_text,
            record=record,
        )

    def poll(
        self,
        path: str,
        *,
        op: str = "poll",
        interval_s: float = 0.2,
        timeout_s: float = 600.0,
        until: Callable[[dict[str, Any]], bool] | None = None,
        failed_statuses: tuple[str, ...] = ("failed", "error"),
        on_response: Callable[[float, int | None, dict[str, Any] | None, str], None] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        extra: str | None = None,
        **fields: Any,
    ) -> PollResult:
        """Poll a status endpoint until done, failed, or timed out.

        Mirrors ``performance/``'s ``poll_commit``: a terminal 4xx (except
        408/409/425/429) fails immediately, transport errors keep polling,
        ``until`` defaults to status ``completed|done|success``.  Records a
        single result record: ok / ``commit_timeout`` / ``commit_failed``.
        """
        start = time.perf_counter()
        polls = 0
        while True:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if timeout_s > 0 and time.perf_counter() - start > timeout_s:
                record = self._make_record(
                    op=op, stage_ms=elapsed_ms, status="error",
                    error_type="commit_timeout", http_status=None,
                    extra=extra if extra is not None else self.extra, **fields,
                )
                self._emit(record)
                return PollResult(op, "timeout", elapsed_ms, polls, None, record)

            polls += 1
            request_started_ms = time.time() * 1000
            try:
                status, error_type, http_status, body_text, body_json, reason = _do_request(
                    self._base_url,
                    "GET",
                    path,
                    params=params,
                    headers=headers,
                    base_headers=self._headers,
                    timeout_s=self._read_timeout_s,
                )
            except TransportError as exc:
                status, error_type, http_status, body_text, body_json, reason = (
                    "error", exc.error_type, None, "", None, "",
                )
            elapsed_ms = (time.perf_counter() - start) * 1000
            if on_response is not None:
                on_response(request_started_ms, http_status, body_json, error_type)
            if http_status is not None and 400 <= http_status < 500 \
                    and http_status not in (408, 409, 425, 429):
                record = self._make_record(
                    op=op, stage_ms=elapsed_ms, status="error",
                    error_type="commit_failed", http_status=http_status,
                    extra=extra if extra is not None else self.extra, **fields,
                )
                record.reason_code = reason
                self._emit(record)
                return PollResult(op, "failed", elapsed_ms, polls, body_json, record)

            if error_type:
                # transport-level failure: keep polling until deadline
                if self._sleep(interval_s):
                    return PollResult(op, "stopped", elapsed_ms, polls, None, None)
                continue

            done = _status_done(body_json, until)
            if done:
                record = self._make_record(
                    op=op, stage_ms=elapsed_ms, status="ok", error_type="",
                    http_status=http_status,
                    extra=extra if extra is not None else self.extra, **fields,
                )
                self._emit(record)
                return PollResult(op, "completed", elapsed_ms, polls, body_json, record)

            state = str(
                (body_json or {}).get("status")
                or (body_json or {}).get("stage")
                or (body_json or {}).get("state")
                or ""
            ).lower()
            if state in failed_statuses:
                record = self._make_record(
                    op=op, stage_ms=elapsed_ms, status="error",
                    error_type="commit_failed", http_status=http_status,
                    extra=extra if extra is not None else self.extra, **fields,
                )
                self._emit(record)
                return PollResult(op, "failed", elapsed_ms, polls, body_json, record)

            if self._sleep(interval_s):
                return PollResult(op, "stopped", elapsed_ms, polls, None, None)

    # -- manual / post-hoc recording --------------------------------------

    def record(
        self,
        *,
        op: str,
        stage_ms: float,
        status: str,
        error_type: str = "",
        http_status: int | None = None,
        session_id: str = "",
        extra: str | None = None,
        **fields: Any,
    ) -> RequestRecord:
        """Record an observation not produced by a request or poll."""
        record = self._make_record(
            op=op, stage_ms=stage_ms, status=status, error_type=error_type,
            http_status=http_status, session_id=session_id,
            extra=extra if extra is not None else self.extra, **fields,
        )
        self._emit(record)
        return record

    def note(self, **fields: Any) -> None:
        """Attach response-derived fields to this context's last record."""
        if self._last_record is not None:
            for key, value in fields.items():
                setattr(self._last_record, key, value)

    # -- probe assertions (probe runs only) --------------------------------

    def check(
        self,
        name: str,
        *,
        status: str,
        reason: str = "",
        elapsed_s: float | None = None,
        detail: str = "",
    ) -> None:
        """Record one probe assertion result.

        ``status`` must be one of ``PASS`` / ``FAIL`` / ``NOT_IMPLEMENTED``
        / ``INCONCLUSIVE``.  Only available on probe contexts (the probe
        runner injects a checks list); a scene context raises.
        """
        if self._checks is None:
            raise RuntimeError(
                "ctx.check is only available inside a probe run (scene contexts "
                "record requests, not probe assertions)"
            )
        if status not in PROBE_STATUSES:
            raise ValueError(f"invalid probe status {status!r}; expected one of "
                             f"{', '.join(PROBE_STATUSES)}")
        self._checks.append(
            ProbeCheck(name=name, status=status, reason=reason,
                       elapsed_s=elapsed_s, detail=detail)
        )

    # -- phase injection (schedule hook only) ------------------------------

    def at_time(
        self,
        at_s: float,
        fn: Callable[["Ctx"], None],
        *,
        count: int = 1,
        max_workers: int = 1,
        name: str = "phase",
        tenant_idx: int | None = None,
        tenant_counts: dict[int, int] | None = None,
    ) -> None:
        """Schedule ``fn`` to run ``count`` times at ``at_s`` seconds from
        scene start, with at most ``max_workers`` concurrent executions.

        ``tenant_idx`` runs every job as one tenant (default: the first
        tenant).  ``tenant_counts`` distributes the jobs across tenants —
        a ``{tenant_idx: count}`` map, run together in one concurrent
        phase (``count`` is then the sum of the map and must not be
        passed as the total)."""
        if at_s < 0 or count < 1 or max_workers < 1:
            raise ValueError("at_time requires at_s >= 0, count >= 1, max_workers >= 1")
        if tenant_counts is not None:
            if not tenant_counts:
                raise ValueError("at_time tenant_counts must be non-empty")
            if any(index < 0 or jobs < 1 for index, jobs in tenant_counts.items()):
                raise ValueError(
                    "at_time tenant_counts requires tenant_idx >= 0 and count >= 1"
                )
            count = sum(tenant_counts.values())
        self._phases.append(
            Phase(at_s=at_s, fn=fn, count=count, max_workers=max_workers,
                  name=name, tenant_idx=tenant_idx, tenant_counts=tenant_counts)
        )

    def at_ratio(
        self,
        ratio: float,
        fn: Callable[["Ctx"], None],
        *,
        count: int = 1,
        max_workers: int = 1,
        name: str = "phase",
        tenant_idx: int | None = None,
        tenant_counts: dict[int, int] | None = None,
    ) -> None:
        """Like :meth:`at_time`, offset relative to ``duration_s``."""
        self.at_time(ratio * self._duration_s, fn, count=count,
                     max_workers=max_workers, name=name, tenant_idx=tenant_idx,
                     tenant_counts=tenant_counts)

    # -- internals ---------------------------------------------------------

    def _make_record(
        self,
        *,
        op: str,
        stage_ms: float,
        status: str,
        error_type: str,
        http_status: int | None,
        session_id: str = "",
        extra: str | None = None,
        **fields: Any,
    ) -> RequestRecord:
        record = RequestRecord(
            scene=self._scene,
            worker_id=self._worker_id,
            tenant_idx=self._tenant_idx,
            op=op,
            stage_ms=round(stage_ms, 3),
            status=status,
            error_type=error_type,
            ts_ms=time.time() * 1000,
            http_status=http_status,
            session_id=session_id,
            extra=extra if extra is not None else self.extra,
        )
        for key, value in fields.items():
            setattr(record, key, value)
        return record

    def _emit(self, record: RequestRecord) -> None:
        self._last_record = record
        self._record_fn(record)

    def _sleep(self, interval_s: float) -> bool:
        """Sleep between polls; True when the engine is stopping."""
        return self._stop.wait(interval_s)


# --------------------------------------------------------------------- #
#  Transport                                                            #
# --------------------------------------------------------------------- #

class TransportError(Exception):
    """A transport-level failure, classified for recording."""


# Per-thread keep-alive HTTP/1.1 connections.  A load engine must not open
# a new TCP connection per request: connection setup would dominate latency
# measurements and exhaust client sockets under load churn.
_connection_local = threading.local()


def _reuse_connection(base_url: str, timeout_s: float) -> http.client.HTTPConnection:
    parsed = urllib.parse.urlsplit(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    conn = getattr(_connection_local, "conn", None)
    if conn is None or conn._reuse_key != (host, port):
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout_s)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
        conn._reuse_key = (host, port)
        _connection_local.conn = conn
    return conn


def _drop_connection() -> None:
    conn = getattr(_connection_local, "conn", None)
    if conn is not None:
        with contextlib.suppress(OSError):
            conn.close()
        _connection_local.conn = None


def _do_request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    base_headers: dict[str, str] | None = None,
    timeout_s: float,
) -> tuple[str, str, int | None, str, dict[str, Any] | None, str]:
    """Execute one request; never raises for HTTP/transport failures.

    Returns ``(status, error_type, http_status, body_text, body_json,
    reason_code)``.  ``status`` is ``ok`` for any 2xx/3xx response; all
    other outcomes are ``error`` with a classified ``error_type``.
    """
    url = path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request_headers = dict(base_headers or {})
    if headers:
        request_headers.update(headers)
    if payload is not None and "Content-Type" not in {
        key.lower() for key in request_headers
    }:
        request_headers["Content-Type"] = "application/json"

    for attempt in (0, 1):
        conn = _reuse_connection(base_url, timeout_s)
        try:
            conn.request(method, url, body=payload, headers=request_headers)
        except TimeoutError as exc:
            _drop_connection()
            raise _transport_fail("timeout") from exc
        except (OSError, http.client.HTTPException) as exc:
            # The connection failed before this request's bytes were sent
            # (stale keep-alive or refused connect); retry once on a fresh
            # connection.  A send-phase failure never duplicates a write.
            _drop_connection()
            if attempt == 0:
                continue
            raise _transport_fail("connection") from exc
        try:
            response = conn.getresponse()
        except TimeoutError as exc:
            _drop_connection()
            raise _transport_fail("timeout") from exc
        except (OSError, http.client.HTTPException) as exc:
            # The request may already have been delivered; report it, never
            # retry (a retry could duplicate a write).
            _drop_connection()
            raise _transport_fail("connection") from exc
        try:
            raw = response.read()
        except TimeoutError as exc:
            _drop_connection()
            raise _transport_fail("timeout") from exc
        except (OSError, http.client.HTTPException) as exc:
            _drop_connection()
            raise _transport_fail("connection") from exc
        body_text = raw.decode("utf-8", errors="replace")
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        http_status = response.status
        if 200 <= http_status < 400:
            return "ok", "", http_status, body_text, _parse_json(body_text), ""
        error_type = "http_4xx" if http_status < 500 else "http_5xx"
        reason = _extract_reason_code(response_headers, raw)
        return "error", error_type, http_status, body_text, _parse_json(body_text), reason
    raise AssertionError("unreachable: _do_request retry loop")


def _transport_fail(error_type: str) -> TransportError:
    err = TransportError(error_type)
    err.error_type = error_type
    return err


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _status_done(body: dict[str, Any] | None, until: Callable[[dict[str, Any]], bool] | None) -> bool:
    if body is None:
        return False
    if until is not None:
        return bool(until(body))
    return str(body.get("status") or "").lower() in ("completed", "done", "success")


def _default_op(path: str) -> str:
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    return segment or "request"


def _extract_reason_code(headers: dict[str, str], body: bytes) -> str:
    """提取失败响应中的服务端拒绝原因（reason_code）。

    header 按小写 key 匹配别名；payload 顶层 + ``error``/``meta`` 嵌套查找。
    """
    for key, value in headers.items():
        if key in _REASON_CODE_HEADER_ALIASES and str(value or "").strip():
            return str(value)
    body_text = body.decode("utf-8", errors="replace") if body else ""
    parsed = _parse_json(body_text)
    if not parsed:
        return ""
    candidates: list[dict[str, Any]] = [parsed]
    for key in ("error", "meta"):
        nested = parsed.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for alias in _REASON_CODE_ALIASES:
            value = candidate.get(alias)
            if value is not None and str(value).strip():
                return str(value)
    return ""
