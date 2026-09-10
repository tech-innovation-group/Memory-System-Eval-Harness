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
from performance.targets.echomem.probes._client import (
    EchoMemHTTP, extract_archive, load_tenant_specs, status_from,
)

PASS = "PASS"
INCONCLUSIVE = "INCONCLUSIVE"


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


def _summary(rows: list[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    latencies = [float(row["elapsed_ms"]) for row in rows]
    codes = Counter(str(row.get("http_status") or "transport") for row in rows)
    per_tenant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        per_tenant[str(row["tenant_id"])].append(row)
    rates = []
    tenant_rows = []
    for tenant_id, samples in sorted(per_tenant.items()):
        completed = sum(row.get("http_status") is not None and 200 <= row["http_status"] < 300
                        for row in samples)
        rates.append(completed / max(elapsed_s, 0.001))
        tenant_rows.append({"tenant_id": tenant_id, "offered": len(samples),
                            "completed_2xx": completed,
                            "p95_ms": _percentile([row["elapsed_ms"] for row in samples], .95)})
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
        "tenants": tenant_rows,
        "latency_scope": "all attempts including transport failures",
        "fairness_scope": "HTTP acceptance only; 202 is not commit completion",
    }


def _run_calls(calls: list[Callable[[], dict[str, Any]]], workers: int) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = list(pool.map(lambda call: call(), calls))
    return rows, time.perf_counter() - started


def _search_call(client: EchoMemHTTP, tenant_id: str, session_id: str,
                 query: str, timeout_s: float, payload_class: str,
                 lock: threading.Lock | None = None) -> Callable[[], dict[str, Any]]:
    def call() -> dict[str, Any]:
        if lock:
            lock.acquire()
        try:
            result = client.search(session_id, query, timeout_s)
        finally:
            if lock:
                lock.release()
        return {"tenant_id": tenant_id, "operation": "search", "payload_class": payload_class,
                "http_status": result.status_code, "elapsed_ms": round(result.elapsed_s * 1000, 3),
                "reason_code": result.reason_code,
                "transport_error_type": result.transport_error_type}
    return call


def _commit_call(client: EchoMemHTTP, tenant_id: str, session_id: str,
                 content: str, lock: threading.Lock | None = None) -> Callable[[], dict[str, Any]]:
    def call() -> dict[str, Any]:
        if lock:
            lock.acquire()
        started = time.perf_counter()
        try:
            message = client.add_message(session_id, uuid.uuid4().hex, content)
            result = (client.commit(session_id, idempotency_key=f"topology-{uuid.uuid4().hex}")
                      if message.status_code is not None and 200 <= message.status_code < 300
                      else message)
        finally:
            if lock:
                lock.release()
        status = result.status_code if message.status_code is not None and message.status_code < 400 else message.status_code
        return {"tenant_id": tenant_id, "operation": "commit", "payload_class": "large",
                "session_id": session_id, "archive_id": extract_archive(result.payload),
                "http_status": status, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "reason_code": result.reason_code or message.reason_code,
                "transport_error_type": result.transport_error_type or message.transport_error_type}
    return call


def _drain(rows: list[dict[str, Any]], clients: dict[str, EchoMemHTTP],
           timeout_s: float) -> dict[str, Any]:
    accepted = [row for row in rows if row.get("operation") == "commit"
                and row.get("http_status") == 202]
    pending = list(accepted)
    started = time.monotonic()
    deadline = time.monotonic() + timeout_s
    counts: Counter[str] = Counter()
    per_tenant = {row["tenant_id"]: {"accepted": 0, "completed": 0, "failed": 0}
                  for row in accepted if "tenant_id" in row}
    for row in accepted:
        if "tenant_id" in row:
            per_tenant[row["tenant_id"]]["accepted"] += 1
    while pending and time.monotonic() < deadline:
        for row in list(pending):
            if time.monotonic() >= deadline:
                break
            if not row.get("archive_id"):
                continue
            response = clients[row["tenant_id"]].commit_status(row["session_id"], row["archive_id"])
            state = status_from(response.payload)
            if response.status_code == 200 and state in {"completed", "done", "success", "failed", "error"}:
                terminal = "completed" if state in {"completed", "done", "success"} else "failed"
                counts[terminal] += 1
                per_tenant[row["tenant_id"]][terminal] += 1
                row["terminal_state"] = state
                pending.remove(row)
        if pending:
            time.sleep(.5)
    return {"accepted_202": len(accepted), "completed": counts["completed"],
            "failed": counts["failed"], "pending": len(pending),
            "drained": not pending, "timeout_s": timeout_s,
            "elapsed_s": round(time.monotonic() - started, 3), "tenants": per_tenant}


def _bounded_call(call: Callable[[], dict[str, Any]], gate: threading.Semaphore,
                  state: dict[str, int], mutex: threading.Lock) -> dict[str, Any]:
    with gate:
        with mutex:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            return call()
        finally:
            with mutex:
                state["active"] -= 1


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

    levels = sorted({max(1, int(value)) for value in params.get("levels", [16, 32, 64, 128])})
    if any(level > 128 for level in levels):
        raise ValueError("levels above 128 require an explicit larger test plan")
    sessions_per_user = max(2, int(params.get("sessions_per_user", 2)))
    requests_per_level = max(1, int(params.get("requests_per_level", 128)))
    within_session_concurrency = max(2, int(params.get("within_session_concurrency", 4)))
    timeout_s = max(1.0, float(params.get("timeout_s", 60)))
    small_query = str(params.get("small_query") or "What is the stress marker?")
    large_chars = max(1, int(params.get("large_commit_chars", 65536)))
    fact = "My project review is on September 18 at 10 AM in meeting room Cedar. "
    large_query = str(params.get("large_query") or (fact * (large_chars // len(fact) + 1))[:large_chars])
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
    for level in levels:
        topologies = (
            ("many-users-one-session-serial", level, 1, False),
            ("many-users-many-sessions-serial", math.ceil(level / sessions_per_user), 1, True),
            ("many-users-one-session-concurrent",
             math.ceil(level / within_session_concurrency), within_session_concurrency, False),
            ("heterogeneous-users", min(4, level), math.ceil(level / min(4, level)), False),
        )
        for topology, required_users, per_session, use_multi in topologies:
            user_count = min(required_users, len(tenants))
            if user_count != required_users:
                matrix.append({"level": level, "topology": topology,
                               "requested_users": required_users, "actual_users": user_count,
                               "fully_realized": False, "status": INCONCLUSIVE,
                               "offered": requests_per_level, "sent": 0,
                               "reason": "insufficient independent tenant credentials"})
                continue
            calls = []
            gates: dict[str, threading.Semaphore] = {}
            state = {"active": 0, "peak": 0}
            mutex = threading.Lock()
            for index in range(requests_per_level):
                actor = index % user_count
                session = (multi_sessions[actor][(index // user_count) % sessions_per_user]
                           if use_multi else single_sessions[actor])
                heterogeneous = topology == "heterogeneous-users"
                is_large = bool(heterogeneous and actor % 2)
                gate = gates.setdefault(session, threading.Semaphore(per_session))
                if is_large:
                    operation = _commit_call(clients[actor], tenants[actor].tenant_id,
                                              session, large_query)
                else:
                    operation = _search_call(clients[actor], tenants[actor].tenant_id,
                                              session, small_query, timeout_s, "small")
                calls.append(lambda operation=operation, gate=gate:
                             _bounded_call(operation, gate, state, mutex))
            effective_workers = min(level, user_count * per_session * (sessions_per_user if use_multi else 1))
            rows, elapsed = _run_calls(calls, effective_workers)
            summary = _summary(rows, elapsed)
            drain = _drain(rows, {t.tenant_id: c for t, c in zip(tenants, clients)},
                           float(params.get("drain_timeout_s", 300)))
            matrix.append({"level": level, "topology": topology,
                           "requested_concurrency": level,
                           "requested_users": required_users, "actual_users": user_count,
                           "sessions_per_user": sessions_per_user if use_multi else 1,
                           "per_session_concurrency": per_session,
                           "configured_workers": effective_workers,
                           "peak_active_operations": state["peak"],
                           "fully_realized": state["peak"] == level,
                           "drain": drain,
                           "operations": {op: _summary([r for r in rows if r["operation"] == op], elapsed)
                                          for op in {r["operation"] for r in rows}},
                           "samples": rows,
                           **summary})
            if not drain["drained"]:
                ctx.check("concurrency-topology", status=INCONCLUSIVE,
                          reason="previous-case-commit-backlog-unresolved; later cases not run",
                          detail=json.dumps({"matrix": matrix, "levels": levels,
                                             "available_tenants": len(tenants)}, ensure_ascii=False))
                return

    incomplete = sum(not row["fully_realized"] for row in matrix)
    ctx.check(
        "concurrency-topology",
        status=INCONCLUSIVE if incomplete else PASS,
        reason=(f"measured {len(matrix)} topology/level cases through real HTTP; "
                f"{incomplete} cases did not realize requested concurrency"),
        detail=json.dumps({"levels": levels, "available_tenants": len(tenants),
                           "matrix": matrix}, ensure_ascii=False),
    )
