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
from performance.targets.echomem.probes._client import EchoMemHTTP, load_tenant_specs

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
    latencies = [float(row["elapsed_ms"]) for row in rows if row.get("http_status") is not None]
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
            result = client.commit(session_id, idempotency_key=f"topology-{uuid.uuid4().hex}")
        finally:
            if lock:
                lock.release()
        status = result.status_code if message.status_code is not None and message.status_code < 400 else message.status_code
        return {"tenant_id": tenant_id, "operation": "commit", "payload_class": "large",
                "http_status": status, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "reason_code": result.reason_code or message.reason_code,
                "transport_error_type": result.transport_error_type or message.transport_error_type}
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

    levels = sorted({max(1, int(value)) for value in params.get("levels", [16, 32, 64, 128])})
    max_level = min(128, max(levels))
    levels = [value for value in levels if value <= max_level]
    sessions_per_user = max(2, int(params.get("sessions_per_user", 2)))
    requests_per_level = max(1, int(params.get("requests_per_level", 128)))
    within_session_concurrency = max(2, int(params.get("within_session_concurrency", 4)))
    timeout_s = max(1.0, float(params.get("timeout_s", 60)))
    small_query = str(params.get("small_query") or "What is the stress marker?")
    large_query = str(params.get("large_query") or (small_query + " " + "context " * 2048))
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
    session_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
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
            calls = []
            for index in range(requests_per_level):
                actor = index % user_count
                session = (multi_sessions[actor][(index // user_count) % sessions_per_user]
                           if use_multi else single_sessions[actor])
                heterogeneous = topology == "heterogeneous-users"
                is_large = bool(heterogeneous and actor % 2)
                lock = session_locks[session] if per_session == 1 else None
                if is_large:
                    calls.append(_commit_call(clients[actor], tenants[actor].tenant_id,
                                              session, large_query, lock))
                else:
                    calls.append(_search_call(clients[actor], tenants[actor].tenant_id,
                                              session, small_query, timeout_s, "small", lock))
            effective_workers = min(level, max(1, user_count * per_session))
            rows, elapsed = _run_calls(calls, effective_workers)
            summary = _summary(rows, elapsed)
            matrix.append({"level": level, "topology": topology,
                           "requested_concurrency": level,
                           "requested_users": required_users, "actual_users": user_count,
                           "sessions_per_user": sessions_per_user if use_multi else 1,
                           "per_session_concurrency": per_session,
                           "fully_realized": effective_workers == level and user_count == required_users,
                           **summary})

    incomplete = sum(not row["fully_realized"] for row in matrix)
    ctx.check(
        "concurrency-topology",
        status=INCONCLUSIVE if incomplete else PASS,
        reason=(f"measured {len(matrix)} topology/level cases through real HTTP; "
                f"{incomplete} cases lacked enough independent tenant credentials"),
        detail=json.dumps({"levels": levels, "available_tenants": len(tenants),
                           "matrix": matrix}, ensure_ascii=False),
    )
