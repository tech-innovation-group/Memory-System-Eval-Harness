"""真实 HTTP 四类用户/Session 并发拓扑矩阵探针。"""

from __future__ import annotations

import concurrent.futures
import json
import math
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from performance.ctx import Ctx
from performance.targets.echomem.probes.failure_evidence import failure_evidence, reference
from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval
from performance.targets.echomem.probes._client import (
    EchoMemHTTP,
    extract_archive,
    load_tenant_specs,
    status_from,
)

PASS = "PASS"
INCONCLUSIVE = "INCONCLUSIVE"
MIN_FAIRNESS_USERS = 4


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 3)


def _jain(values: list[float]) -> float | None:
    values = [value for value in values if value >= 0]
    denominator = len(values) * sum(value * value for value in values)
    return round(sum(values) ** 2 / denominator, 6) if denominator else None


def _capacity_levels(values: Any, maximum: int | None = None) -> list[int]:
    configured = sorted({max(1, int(value)) for value in (values or [16, 32, 64, 128])})
    limit = max(configured[-1], int(maximum or configured[-1]))
    while configured[-1] < limit:
        configured.append(min(limit, configured[-1] * 2))
    return configured


def _generator_workers(
    level: int,
    user_count: int,
    per_session_concurrency: int,
    sessions_per_user: int,
) -> int:
    return min(
        level,
        max(1, user_count * per_session_concurrency * sessions_per_user),
    )


class _InflightCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0

    def call(self, operation: Callable[[], Any]) -> Any:
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        try:
            return operation()
        finally:
            with self._lock:
                self._active -= 1


class _SessionInflight:
    """Observe actual operation overlap globally and for every session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, int] = defaultdict(int)
        self.peaks: dict[str, int] = defaultdict(int)
        self.peak = 0

    def call(self, session_id: str, operation: Callable[[], Any]) -> Any:
        with self._lock:
            self._active[session_id] += 1
            self.peaks[session_id] = max(self.peaks[session_id], self._active[session_id])
            self.peak = max(self.peak, sum(self._active.values()))
        try:
            return operation()
        finally:
            with self._lock:
                self._active[session_id] -= 1


class _CommitBacklog:
    """Count accepted Commit tasks until their observed terminal state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0

    def accepted(self) -> None:
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)

    def resolved(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def active(self) -> int:
        with self._lock:
            return self._active


def _natural_long_text(tenant_id: str, size: int, kind: str) -> str:
    """Deterministic long natural-language content with retrievable anchors."""
    first = f"Tenant {tenant_id} {kind} first fact: the launch owner is Rowan. "
    middle = f"Tenant {tenant_id} {kind} middle fact: the review happens in Cedar room. "
    last = f"Tenant {tenant_id} {kind} last fact: the approval code is ORBIT-47. "
    filler = ("This is a realistic stress-test note describing decisions, context, and follow-up work. "
              "It is intentionally readable so long-write persistence can be checked semantically. ")
    if size <= len(first) + len(middle) + len(last):
        return (first + middle + last)[:size]
    remaining = size - len(first) - len(middle) - len(last)
    repeated = (filler * (remaining // len(filler) + 1))[:remaining]
    midpoint = len(repeated) // 2
    return first + repeated[:midpoint] + middle + repeated[midpoint:] + last


def _summary(rows: list[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    latencies = [float(row["elapsed_ms"]) for row in rows if row.get("elapsed_ms") is not None]
    codes = Counter(str(row.get("http_status") or "transport") for row in rows)
    per_tenant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    per_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        per_tenant[str(row["tenant_id"])].append(row)
        per_user[str(row.get("user_id") or row["tenant_id"])].append(row)
    rates = []
    search_rates = []
    commit_rates = []
    tenant_rows = []
    for tenant_id, samples in sorted(per_tenant.items()):
        completed = sum(row.get("http_status") is not None and 200 <= row["http_status"] < 300
                        for row in samples)
        strict_search = sum(
            row.get("operation") == "search" and bool(row.get("quality_ok"))
            for row in samples
        )
        completed_commits = sum(
            row.get("operation") == "commit" and row.get("terminal_state") == "completed"
            for row in samples
        )
        rates.append(completed / max(elapsed_s, 0.001))
        search_rates.append(strict_search / max(elapsed_s, 0.001))
        commit_rates.append(completed_commits / max(elapsed_s, 0.001))
        tenant_rows.append({"tenant_id": tenant_id, "offered": len(samples),
                            "completed_2xx": completed,
                            "strict_search_completed": strict_search,
                            "commit_completed": completed_commits,
                            "p95_ms": _percentile([row["elapsed_ms"] for row in samples], .95)})
    user_rows = []
    user_rates = []
    user_groups: dict[str, list[float]] = defaultdict(list)
    for user_id, samples in sorted(per_user.items()):
        completed = sum(row.get("http_status") is not None and 200 <= row["http_status"] < 300
                        for row in samples)
        strict = sum(bool(row.get("quality_ok")) or row.get("terminal_state") == "completed"
                     for row in samples)
        rate = strict / max(elapsed_s, .001)
        payload_classes = sorted({str(row.get("payload_class") or "unknown") for row in samples})
        user_rates.append(rate)
        for payload_class in payload_classes:
            user_groups[payload_class].append(rate)
        user_rows.append({"user_id": user_id, "offered": len(samples), "completed_2xx": completed,
                          "strict_completed": strict, "throughput_rps": round(rate, 3),
                          "p95_ms": _percentile([row["elapsed_ms"] for row in samples], .95),
                          "payload_classes": payload_classes})
    searches = [row for row in rows if row.get("operation") == "search"]
    commits = [row for row in rows if row.get("operation") == "commit"]
    task_refs = {row.get("archive_ref") for row in commits if row.get("archive_ref")}
    operational_failures = [
        row for row in rows
        if row.get("http_status") is None
        or row.get("http_status") == 429
        or int(row.get("http_status") or 0) >= 500
        or row.get("reason_code") in {
            "HTTP_INGRESS_SATURATED", "HTTP_LANE_SATURATED", "RETRIEVAL_BUSY",
            "COMMIT_QUEUE_FULL", "TENANT_RATE_LIMITED",
        }
        or row.get("terminal_state") in {
            "failed", "error", "timeout", "missing_archive", "message_rejected",
        }
    ]
    return {
        "offered": len(rows),
        "completed_2xx": sum(
            row.get("http_status") is not None and 200 <= row["http_status"] < 300
            for row in rows
        ),
        "http_counts": dict(codes), "elapsed_s": round(elapsed_s, 3),
        "throughput_rps_2xx": round(sum(rates), 3),
        "p50_ms": _percentile(latencies, .50), "p95_ms": _percentile(latencies, .95),
        "p99_ms": _percentile(latencies, .99), "tenant_throughput_jain": _jain(rates),
        "search_strict_throughput_jain": _jain(search_rates) if searches else None,
        "commit_completion_throughput_jain": _jain(commit_rates) if commits else None,
        "actual_sessions": len({str(row.get("session_id")) for row in rows if row.get("session_id")}),
        "search_offered": len(searches),
        "search_2xx": sum(row.get("http_status") is not None
                           and 200 <= row["http_status"] < 300 for row in searches),
        "search_quality_observed": sum(bool(row.get("quality_observed")) for row in searches),
        "search_quality_ok": sum(bool(row.get("quality_ok")) for row in searches),
        "search_quality_failures": sum(
            bool(row.get("quality_observed")) and not bool(row.get("quality_ok"))
            for row in searches
        ),
        "search_recall_hits": sum(bool(row.get("recall_hit")) for row in searches),
        "search_degraded": sum(bool(row.get("degraded")) for row in searches),
        "commit_offered": len(commits),
        "commit_unique_archives": len(task_refs),
        "commit_observations_with_archive": sum(bool(row.get("archive_ref")) for row in commits),
        "commit_repeated_archive_observations": sum(bool(row.get("archive_ref")) for row in commits) - len(task_refs),
        "commit_unique_failed_archives": len({row["archive_ref"] for row in commits if row.get("archive_ref") and row.get("terminal_state") in {"failed", "error"}}),
        "commit_failure_categories": dict(Counter(category for row in commits for category in row.get("terminal_evidence", {}).get("categories", []))),
        "commit_accepted": sum(bool(row.get("accepted")) for row in commits),
        "commit_completed": sum(row.get("terminal_state") == "completed" for row in commits),
        "commit_failed": sum(row.get("terminal_state") in {"failed", "error"} for row in commits),
        "commit_timed_out": sum(row.get("terminal_state") == "timeout" for row in commits),
        "commit_missing_receipt": sum(
            row.get("terminal_state") == "missing_archive" for row in commits
        ),
        "operational_failures": len(operational_failures),
        "boundary_reasons": dict(Counter(
            str(row.get("reason_code") or row.get("transport_error_type")
                or row.get("terminal_state") or row.get("http_status"))
            for row in operational_failures
        )),
        "tenants": tenant_rows,
        "users": user_rows,
        "fairness_user_count": len(user_rates),
        "user_throughput_jain": (_jain(user_rates)
                                 if len(user_rates) >= MIN_FAIRNESS_USERS else None),
        "user_group_throughput_jain": {
            group: (_jain(values) if len(values) >= MIN_FAIRNESS_USERS else None)
            for group, values in sorted(user_groups.items())
        },
    }


def _run_calls(
    calls: list[Callable[[], dict[str, Any]]], workers: int,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = list(pool.map(lambda call: call(), calls))
    return rows, time.perf_counter() - started


def _session_call(operation: Callable[[], dict[str, Any]], gate: threading.Semaphore,
                  session_id: str = "", observed: _SessionInflight | None = None) -> dict[str, Any]:
    with gate:
        return observed.call(session_id, operation) if observed else operation()


def _search_call(client: EchoMemHTTP, tenant_id: str, session_id: str,
                 sample: dict[str, Any] | str | None, timeout_s: float, payload_class: str,
                 lock: threading.Lock | None = None,
                 inflight: _InflightCounter | None = None,
                 user_id: str = "", search_inflight: _InflightCounter | None = None) -> Callable[[], dict[str, Any]]:
    def call() -> dict[str, Any]:
        query = sample.get("query") if isinstance(sample, dict) else str(
            sample or "What is the stress marker?"
        )
        if lock:
            lock.acquire()
        try:
            operation = lambda: client.search(session_id, query, timeout_s)
            search_operation = (lambda: search_inflight.call(operation)) if search_inflight else operation
            result = inflight.call(search_operation) if inflight else search_operation()
        finally:
            if lock:
                lock.release()
        quality = assess_retrieval(result.payload, sample) if isinstance(sample, dict) else None
        return {"tenant_id": tenant_id, "user_id": user_id or tenant_id, "session_id": session_id,
                "operation": "search", "payload_class": payload_class,
                "http_status": result.status_code, "elapsed_ms": round(result.elapsed_s * 1000, 3),
                "reason_code": result.reason_code,
                "transport_error_type": result.transport_error_type,
                "request_ref": reference(getattr(result, "request_id", "")),
                "quality_observed": quality is not None,
                "quality_ok": bool(result.status_code == 200 and quality and quality["quality_ok"]),
                "recall_hit": bool(quality and quality["matched_expected_fact"]),
                "hit_count": quality.get("hit_count") if quality else None,
                "degraded": quality.get("degraded") if quality else None}
    return call


def _commit_call(client: EchoMemHTTP, tenant_id: str, session_id: str,
                 content: str, poll_timeout_s: float,
                 lock: threading.Lock | None = None,
                 inflight: _InflightCounter | None = None,
                 submit_inflight: _InflightCounter | None = None,
                 backlog: _CommitBacklog | None = None,
                 payload_class: str = "large", user_id: str = "") -> Callable[[], dict[str, Any]]:
    def call() -> dict[str, Any]:
        if lock:
            lock.acquire()
        started = time.perf_counter()
        try:
            message_call = lambda: client.add_message(session_id, uuid.uuid4().hex, content)
            message = inflight.call(message_call) if inflight else message_call()
            if message.status_code is None or message.status_code >= 400:
                return {
                    "tenant_id": tenant_id, "user_id": user_id or tenant_id, "session_id": session_id,
                    "operation": "commit", "payload_class": payload_class, "http_status": message.status_code,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "reason_code": message.reason_code,
                    "transport_error_type": message.transport_error_type,
                    "accepted": False, "archive_id_present": False,
                    "terminal_state": "message_rejected", "poll_count": 0,
                }
            commit_call = lambda: client.commit(
                session_id, idempotency_key=f"topology-{uuid.uuid4().hex}"
            )
            submit_operation = (lambda: submit_inflight.call(commit_call)) if submit_inflight else commit_call
            result = inflight.call(submit_operation) if inflight else submit_operation()
        finally:
            if lock:
                lock.release()
        status = result.status_code if message.status_code is not None and message.status_code < 400 else message.status_code
        archive_id = extract_archive(result.payload) if result.status_code in {200, 202} else ""
        tracked_backlog = bool(archive_id and result.status_code in {200, 202})
        if tracked_backlog and backlog:
            backlog.accepted()
        terminal_state = (
            "missing_archive" if result.status_code in {200, 202} and not archive_id else ""
        )
        poll_count = 0
        terminal_evidence = {}
        last_poll_status = None
        transitions = []
        deadline = time.monotonic() + poll_timeout_s
        try:
            while archive_id and time.monotonic() < deadline:
                poll_call = lambda: client.commit_status(session_id, archive_id)
                observed = inflight.call(poll_call) if inflight else poll_call()
                poll_count += 1
                last_poll_status = getattr(observed, "status_code", None)
                terminal_evidence = failure_evidence(observed.payload)
                new_state = status_from(observed.payload)
                if not transitions or transitions[-1]["state"] != new_state:
                    transitions.append({"state": new_state, "elapsed_ms": round((time.perf_counter()-started)*1000, 3),
                                        "http_status": last_poll_status, "evidence": terminal_evidence})
                terminal_state = new_state
                if terminal_state in {"complete", "completed", "succeeded", "success"}:
                    terminal_state = "completed"
                    break
                if terminal_state in {"failed", "error"}:
                    break
                time.sleep(0.5)
            if archive_id and terminal_state not in {"completed", "failed", "error"}:
                terminal_state = "timeout"
        finally:
            if tracked_backlog and backlog:
                backlog.resolved()
        return {"tenant_id": tenant_id, "user_id": user_id or tenant_id, "session_id": session_id,
                "operation": "commit", "payload_class": payload_class,
                "http_status": status, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "reason_code": result.reason_code or message.reason_code,
                "transport_error_type": result.transport_error_type or message.transport_error_type,
                "accepted": result.status_code in {200, 202},
                "archive_id_present": bool(archive_id), "terminal_state": terminal_state,
                "archive_ref": reference(tenant_id + ":" + session_id + ":" + archive_id) if archive_id else "",
                "archive_id_ref": reference(archive_id),
                "commit_ref": reference(result.payload.get("commit_id")),
                "submit_evidence": failure_evidence(result.payload),
                "terminal_evidence": terminal_evidence, "last_poll_http_status": last_poll_status,
                "state_transitions": transitions, "observed_at": time.time(),
                "poll_count": poll_count}
    return call


def run(ctx: Ctx) -> None:
    params = ctx.params
    try:
        tenants = load_tenant_specs(Path(str(params.get("tenant_config") or "")))
    except (OSError, ValueError) as exc:
        ctx.check("concurrency-topology", status=INCONCLUSIVE,
                  reason=f"tenant config unavailable: {type(exc).__name__}")
        return
    if not tenants:
        ctx.check("concurrency-topology", status=INCONCLUSIVE, reason="no tenants")
        return

    levels = _capacity_levels(
        params.get("levels"),
        int(params["max_concurrency"]) if params.get("max_concurrency") else None,
    )
    sessions_per_user = max(2, int(params.get("sessions_per_user", 2)))
    requests_per_level = max(1, int(params.get("requests_per_level", 128)))
    within_session_concurrency = max(2, int(params.get("within_session_concurrency", 4)))
    timeout_s = max(1.0, float(params.get("timeout_s", 60)))
    poll_timeout_s = max(1.0, float(params.get("commit_poll_timeout_s", params.get("drain_timeout_s", 300))))
    stop_after_boundary = bool(params.get("stop_after_boundary", True))
    require_quality = bool(params.get("require_recall_quality", True))
    queries = params.get("queries") if isinstance(params.get("queries"), dict) else {}
    large_chars = max(1, int(params.get("large_commit_chars", 65536)))
    medium_chars = max(1, int(params.get("medium_commit_chars", 8192)))
    operation_modes = [str(mode).strip().lower() for mode in params.get(
        "operation_modes", ["search", "commit", "mixed"]
    ) if str(mode).strip()]
    invalid_modes = sorted(set(operation_modes) - {"search", "commit", "mixed"})
    if invalid_modes:
        ctx.check("concurrency-topology", status=INCONCLUSIVE,
                  reason=f"unsupported operation_modes: {', '.join(invalid_modes)}")
        return
    clients = [EchoMemHTTP(ctx.base_url, tenant.auth_key, timeout_s=timeout_s,
                           tenant_id=tenant.tenant_id, user_id=tenant.user_id,
                           account_id=tenant.account_id, agent_id=tenant.agent_id,
                           auth_header=str(params.get("auth_header") or "X-Auth-Key"))
               for tenant in tenants]
    single_sessions = []
    multi_sessions: list[list[str]] = []
    for tenant, client in zip(tenants, clients):
        session, _ = client.open_session(tenant.tenant_id, "topology-single")
        single_sessions.append(session)
        sessions = [client.open_session(tenant.tenant_id, f"topology-{index}")[0]
                    for index in range(sessions_per_user)]
        multi_sessions.append(sessions)

    matrix = []
    backlog_unresolved = False
    measured_levels = []
    first_boundary: dict[str, Any] | None = None
    for level in levels:
        level_boundary = False
        topologies = (
            ("many-users-one-session-serial", level, 1, False),
            ("many-users-many-sessions-serial", math.ceil(level / sessions_per_user), 1, True),
            ("many-users-one-session-concurrent",
             math.ceil(level / within_session_concurrency), within_session_concurrency, False),
            ("heterogeneous-users", level, 1, False),
        )
        for topology, required_users, per_session, use_multi in topologies:
            if params.get("topologies") and topology not in params["topologies"]:
                continue
            user_count = min(required_users, len(tenants))
            requested_modes = ["mixed"] if topology == "heterogeneous-users" else [
                mode for mode in operation_modes if mode in {"search", "commit"}
            ]
            for mode in requested_modes:
                calls = []
                http_inflight = _InflightCounter()
                search_inflight = _InflightCounter()
                submit_inflight = _InflightCounter()
                operation_inflight = _SessionInflight()
                commit_backlog = _CommitBacklog()
                gates: dict[str, threading.Semaphore] = {}
                offered_requests = max(requests_per_level, level)
                for index in range(offered_requests):
                    actor = index % user_count
                    session = (multi_sessions[actor][(index // user_count) % sessions_per_user]
                               if use_multi else single_sessions[actor])
                    user_id = str(tenants[actor].user_id or tenants[actor].tenant_id)
                    gate = gates.setdefault(session, threading.Semaphore(per_session))
                    payload_class = "small"
                    operation_kind = mode
                    content = ""
                    if mode == "mixed":
                        group = actor % 3
                        operation_kind = "search" if group == 0 else "commit"
                        payload_class = ("small" if group == 0 else "medium" if group == 1 else "large")
                    if operation_kind == "commit":
                        chars = medium_chars if payload_class == "medium" else large_chars
                        content = _natural_long_text(tenants[actor].tenant_id, chars, payload_class)
                        operation = _commit_call(
                            clients[actor], tenants[actor].tenant_id, session, content,
                            poll_timeout_s, None, http_inflight, submit_inflight,
                            commit_backlog, payload_class, user_id,
                        )
                    else:
                        operation = _search_call(
                            clients[actor], tenants[actor].tenant_id, session,
                            queries.get(tenants[actor].tenant_id), timeout_s, payload_class,
                            None, http_inflight, user_id, search_inflight,
                        )
                    calls.append(lambda operation=operation, gate=gate, session=session:
                                 _session_call(operation, gate, session, operation_inflight))
                session_width = sessions_per_user if use_multi else 1
                effective_workers = _generator_workers(
                    level, user_count, per_session, session_width,
                )
                rows, elapsed = _run_calls(calls, effective_workers)
                summary = _summary(rows, elapsed)
                session_peaks = dict(sorted(operation_inflight.peaks.items()))
                row = {"level": level, "topology": topology, "operation_mode": mode,
                       "requested_concurrency": level,
                       "requested_users": required_users, "actual_users": user_count,
                       "sessions_per_user": sessions_per_user if use_multi else 1,
                       "per_session_concurrency": per_session,
                       "generator_workers": effective_workers,
                       "observed_inflight_peak": http_inflight.peak,
                       "peak_active_operations": operation_inflight.peak,
                       "per_session_observed_peaks": session_peaks,
                       "search_http_inflight_peak": search_inflight.peak,
                       "commit_submit_http_inflight_peak": submit_inflight.peak,
                       "unfinished_commit_peak": commit_backlog.peak,
                       "unfinished_commit_final": commit_backlog.active,
                       "samples": rows,
                       "operations": {op: _summary([r for r in rows if r["operation"] == op], elapsed)
                                      for op in {r["operation"] for r in rows}},
                       "drain": {"completed": summary["commit_completed"],
                                 "failed": summary["commit_failed"],
                                 "pending": summary["commit_timed_out"] + summary["commit_missing_receipt"]},
                       "fully_realized": (
                           effective_workers == level
                           and user_count == required_users
                           and operation_inflight.peak >= level
                           and all(peak <= per_session for peak in session_peaks.values())
                       ),
                       **summary}
                matrix.append(row)
                if summary["commit_timed_out"] or summary["commit_missing_receipt"]:
                    backlog_unresolved = True
                if summary["operational_failures"]:
                    level_boundary = True
                    first_boundary = first_boundary or {
                        "level": level, "topology": topology, "operation_mode": mode,
                        "reasons": summary["boundary_reasons"],
                    }
                if backlog_unresolved:
                    break
            if backlog_unresolved:
                break
        measured_levels.append(level)
        if backlog_unresolved or (level_boundary and stop_after_boundary):
            break

    incomplete = sum(not row["fully_realized"] for row in matrix)
    quality_missing = sum(
        row["search_offered"] - row["search_quality_observed"] for row in matrix
    )
    evidence_complete = not backlog_unresolved and incomplete == 0 and (not require_quality or quality_missing == 0)
    ctx.check(
        "concurrency-topology",
        status=PASS if evidence_complete else INCONCLUSIVE,
        reason=(f"measured {len(matrix)} topology/level cases through real HTTP; "
                f"incomplete topology cases={incomplete}; missing recall-quality samples={quality_missing}; "
                f"boundary={first_boundary or 'not observed within configured maximum'}"),
        detail=json.dumps({"planned_levels": levels, "measured_levels": measured_levels,
                           "stop_reason": "previous-case-commit-backlog-unresolved" if backlog_unresolved else None,
                           "available_tenants": len(tenants),
                           "boundary_status": "observed" if first_boundary else "not_observed_within_cap",
                           "first_boundary": first_boundary, "matrix": matrix}, ensure_ascii=False),
    )
