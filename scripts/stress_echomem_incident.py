"""Run a repeatable EchoMem incident-path load test through HTTP.

The default workflow is:

    POST /api/sessions/open
    POST /api/sessions/{session_id}/messages
    POST /api/sessions/{session_id}/commit

The client records the first response, retries commit requests rejected with
HTTP 429 according to ``Retry-After`` and exponential backoff, and keeps both
initial and final outcomes visible. Use ``--poll-commits`` when the server
returns an archive id and you also want to measure asynchronous commit
completion.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


TERMINAL_COMMIT_STATES = {"completed", "done", "success", "failed", "error"}
EXPECTED_OPEN = {200}
EXPECTED_MESSAGE = {200}
EXPECTED_COMMIT = {200, 201, 202}
DEFAULT_COMMIT_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0
DEFAULT_POLL_RETRIES = 3
DEFAULT_POLL_BACKOFF = 1.0


def percentile(values: list[float], percentile_value: float) -> float | None:
    """Return a linear-interpolated percentile, or None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * weight,
        3,
    )


def extract_archive_id(payload: Any) -> str:
    """Extract archive/task ids from known EchoMem response envelopes."""
    if not isinstance(payload, dict):
        return ""
    for key in ("archive_id", "task_id", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    for key in ("result", "data", "commit"):
        archive_id = extract_archive_id(payload.get(key))
        if archive_id:
            return archive_id
    return ""


def extract_commit_state(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    status = payload.get("status") or payload.get("state") or payload.get("stage")
    if isinstance(status, dict):
        return extract_commit_state(status)
    if status:
        return str(status).lower()
    for key in ("result", "data", "commit"):
        state = extract_commit_state(payload.get(key))
        if state:
            return state
    return ""


def response_summary(response: httpx.Response) -> dict[str, Any]:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text[:500]
    return {"status": response.status_code, "body": body}


def _nested_number(payload: Any, keys: tuple[str, ...]) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        if isinstance(value, str):
            try:
                return max(0.0, float(value))
            except ValueError:
                pass
    for key in ("result", "data", "commit", "error", "detail"):
        found = _nested_number(payload.get(key), keys)
        if found is not None:
            return found
    return None


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Read retry delay from Retry-After or a structured JSON response."""
    value = response.headers.get("Retry-After")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(
                    0.0,
                    (retry_at - datetime.now(timezone.utc)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    try:
        payload: Any = response.json()
    except ValueError:
        return None
    return _nested_number(
        payload,
        ("retry_after_s", "retry_after_seconds", "retry_after"),
    )


def classify_workflow(row: dict[str, Any]) -> str:
    if row.get("exception"):
        return f"exception:{row['exception']}"
    if row.get("open_status") not in EXPECTED_OPEN:
        return f"http:open:{row.get('open_status')}"
    if row.get("message_status") not in EXPECTED_MESSAGE:
        return f"http:message:{row.get('message_status')}"
    commit_mode = row.get("commit_mode", "explicit")
    if commit_mode == "explicit":
        if row.get("commit_status") not in EXPECTED_COMMIT:
            return f"http:commit:{row.get('commit_status')}"
    elif row.get("commit_poll_requested") and not row.get("archive_id"):
        return "auto_commit:missing_archive_id"
    if row.get("commit_poll_state") == "failed":
        return "commit:failed"
    if row.get("commit_poll_state") == "timeout":
        return "commit:timeout"
    if row.get("commit_poll_requested") and not row.get("archive_id"):
        return "commit:missing_archive_id"
    return "ok"


async def post_step(
    client: httpx.AsyncClient,
    path: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    response = await client.post(path, json=payload, headers=headers)
    return response, round((time.perf_counter() - started) * 1000, 3)


async def post_commit_with_retry(
    client: httpx.AsyncClient,
    path: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    max_retries: int,
    retry_backoff_s: float,
    idempotency_key: str,
) -> tuple[httpx.Response, float, dict[str, Any]]:
    """Submit commit and retry only queue-pressure responses."""
    started = time.perf_counter()
    attempts = 0
    retry_delays: list[float] = []
    first_status: int | None = None
    response: httpx.Response | None = None
    max_attempts = max(1, max_retries + 1)
    commit_headers = {**headers, "Idempotency-Key": idempotency_key}
    while attempts < max_attempts:
        attempts += 1
        response = await client.post(path, json=payload, headers=commit_headers)
        if first_status is None:
            first_status = response.status_code
        if response.status_code != 429 or attempts >= max_attempts:
            break
        server_delay = retry_after_seconds(response)
        delay = max(
            server_delay if server_delay is not None else 0.0,
            retry_backoff_s * (2 ** (attempts - 1)),
        )
        retry_delays.append(round(delay, 3))
        await asyncio.sleep(delay)
    assert response is not None
    return response, round((time.perf_counter() - started) * 1000, 3), {
        "commit_initial_status": first_status,
        "commit_attempts": attempts,
        "commit_retries": max(0, attempts - 1),
        "commit_retry_delays_s": retry_delays,
        "commit_retry_exhausted": response.status_code == 429,
        "commit_idempotency_key": idempotency_key,
    }


async def poll_commit(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    archive_id: str,
    headers: dict[str, str],
    timeout_s: float,
    interval_s: float,
    max_retries: int,
    retry_backoff_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    polls = 0
    deadline = time.perf_counter() + timeout_s
    last_body: Any = None
    poll_http_counts: Counter[str] = Counter()
    transient_failures = 0
    while time.perf_counter() < deadline:
        try:
            response = await client.get(
                f"/api/sessions/{session_id}/commits/{archive_id}",
                headers=headers,
            )
        except httpx.HTTPError as exc:
            transient_failures += 1
            if transient_failures > max_retries:
                return {
                    "commit_poll_state": "failed",
                    "commit_poll_raw_state": type(exc).__name__,
                    "commit_poll_error": str(exc)[:500],
                    "commit_poll_count": polls,
                    "commit_poll_elapsed_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                    "commit_poll_http_counts": dict(poll_http_counts),
                }
            await asyncio.sleep(retry_backoff_s * (2 ** (transient_failures - 1)))
            continue
        polls += 1
        poll_http_counts[str(response.status_code)] += 1
        last_body = response_summary(response)
        if response.status_code in {401, 403, 404}:
            return {
                "commit_poll_state": "failed",
                "commit_poll_raw_state": f"http_{response.status_code}",
                "commit_poll_count": polls,
                "commit_poll_elapsed_ms": round(
                    (time.perf_counter() - started) * 1000, 3
                ),
                "commit_poll_body": last_body.get("body"),
                "commit_poll_http_counts": dict(poll_http_counts),
            }
        if response.status_code == 429 or response.status_code >= 500:
            transient_failures += 1
            if transient_failures > max_retries:
                return {
                    "commit_poll_state": "failed",
                    "commit_poll_raw_state": f"http_{response.status_code}",
                    "commit_poll_count": polls,
                    "commit_poll_elapsed_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                    "commit_poll_body": last_body.get("body"),
                    "commit_poll_http_counts": dict(poll_http_counts),
                }
            await asyncio.sleep(retry_backoff_s * (2 ** (transient_failures - 1)))
            continue
        state = extract_commit_state(last_body.get("body"))
        if state in TERMINAL_COMMIT_STATES:
            return {
                "commit_poll_state": (
                    "completed"
                    if state in {"completed", "done", "success"}
                    else "failed"
                ),
                "commit_poll_raw_state": state,
                "commit_poll_status": response.status_code,
                "commit_poll_count": polls,
                "commit_poll_elapsed_ms": round(
                    (time.perf_counter() - started) * 1000,
                    3,
                ),
                "commit_poll_body": last_body.get("body"),
                "commit_poll_http_counts": dict(poll_http_counts),
            }
        await asyncio.sleep(interval_s)
    return {
        "commit_poll_state": "timeout",
        "commit_poll_count": polls,
        "commit_poll_elapsed_ms": round(
            (time.perf_counter() - started) * 1000,
            3,
        ),
        "commit_poll_body": last_body,
        "commit_poll_http_counts": dict(poll_http_counts),
    }


async def run_workflow(
    client: httpx.AsyncClient,
    *,
    index: int,
    stage: int,
    tenant: dict[str, str],
    message_size: int,
    messages_per_session: int,
    commit_mode: str,
    headers: dict[str, str],
    poll_commits: bool,
    poll_timeout_s: float,
    poll_interval_s: float,
    commit_retries: int,
    retry_backoff_s: float,
    poll_retries: int,
    poll_backoff_s: float,
    run_id: str,
    deferred_polling: bool,
    scheduled_at: float | None = None,
) -> dict[str, Any]:
    agent_id = tenant["agent_id"]
    tenant_name = tenant["name"]
    tenant_headers = dict(headers)
    if tenant.get("auth_key"):
        tenant_headers["X-Auth-Key"] = tenant["auth_key"]
    session_id = f"incident-{run_id}-{stage}-{index}"
    started = time.perf_counter()
    row: dict[str, Any] = {
        "index": index,
        "stage": stage,
        "session_id": session_id,
        "tenant": tenant_name,
        "agent_id": agent_id,
        "commit_mode": commit_mode,
        "started_at": time.time(),
    }
    if scheduled_at is not None:
        row["scheduled_at"] = scheduled_at
        row["arrival_lag_ms"] = round(
            max(0.0, row["started_at"] - scheduled_at) * 1000,
            3,
        )
    try:
        response, elapsed = await post_step(
            client,
            "/api/sessions/open",
            payload={"agent_id": agent_id, "session_id": session_id},
            headers=tenant_headers,
        )
        row["open_ms"] = elapsed
        row["open_status"] = response.status_code
        row["open_body"] = response_summary(response)["body"]
        if response.status_code not in EXPECTED_OPEN:
            return row

        message_total_ms = 0.0
        message_bodies: list[Any] = []
        archive_id = ""
        for message_index in range(messages_per_session):
            response, elapsed = await post_step(
                client,
                f"/api/sessions/{session_id}/messages",
                payload={
                    "role": "user",
                    "content": (
                        f"Incident load test stage {stage} item {index} "
                        f"message {message_index}. "
                        + ("x" * max(0, message_size - 52))
                    ),
                },
                headers=tenant_headers,
            )
            message_total_ms += elapsed
            message_body = response_summary(response)["body"]
            message_bodies.append(message_body)
            row["message_status"] = response.status_code
            if response.status_code not in EXPECTED_MESSAGE:
                row["message_ms"] = round(message_total_ms, 3)
                row["message_body"] = message_body
                return row
            archive_id = extract_archive_id(message_body) or archive_id
        row["message_ms"] = round(message_total_ms, 3)
        row["message_count"] = messages_per_session
        row["message_body"] = message_bodies[-1] if message_bodies else None

        if commit_mode == "explicit":
            idempotency_key = f"{run_id}:{session_id}:commit"
            response, elapsed, retry_info = await post_commit_with_retry(
                client,
                f"/api/sessions/{session_id}/commit",
                payload={},
                headers=tenant_headers,
                max_retries=commit_retries,
                retry_backoff_s=retry_backoff_s,
                idempotency_key=idempotency_key,
            )
            commit_response = response_summary(response)
            row["commit_ms"] = elapsed
            row["commit_status"] = response.status_code
            row.update(retry_info)
            row["commit_body"] = commit_response["body"]
            archive_id = extract_archive_id(commit_response["body"])
        else:
            row["commit_ms"] = 0.0
            row["commit_status"] = None
            row["commit_body"] = None
        row["archive_id"] = archive_id
        row["commit_poll_requested"] = poll_commits
        row["request_duration_ms"] = round(
            (time.perf_counter() - started) * 1000, 3
        )
        row["request_completed_at"] = time.time()
        if (
            poll_commits
            and not deferred_polling
            and (
                commit_mode == "auto"
                or row.get("commit_status") in EXPECTED_COMMIT
            )
            and row["archive_id"]
        ):
            row.update(
                await poll_commit(
                    client,
                    session_id=session_id,
                    archive_id=row["archive_id"],
                    headers=tenant_headers,
                    timeout_s=poll_timeout_s,
                    interval_s=poll_interval_s,
                    max_retries=poll_retries,
                    retry_backoff_s=poll_backoff_s,
                )
            )
            row["commit_completion_ms"] = round(
                (time.time() - float(row["started_at"])) * 1000,
                3,
            )
        row["window_commit_poll_state"] = row.get("commit_poll_state")
    except Exception as exc:
        row["exception"] = type(exc).__name__
        row["error"] = str(exc)[:500]
    finally:
        row["duration_ms"] = round(
            (time.perf_counter() - started) * 1000,
            3,
        )
        row["result"] = classify_workflow(row)
    return row


async def run_context_probe(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    index: int,
    agent_id: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {"index": index, "session_id": session_id}
    try:
        response = await client.post(
            "/api/retrieval/build_context",
            json={
                "session_id": session_id,
                "current_user_message": "What context do you remember?",
                "agent_id": agent_id,
                "limit": 5,
                "max_chars": 2000,
            },
            headers=headers,
        )
        row.update(response_summary(response))
    except Exception as exc:
        row["status"] = f"EXC:{type(exc).__name__}"
        row["error"] = str(exc)[:500]
    row["duration_ms"] = round(
        (time.perf_counter() - started) * 1000,
        3,
    )
    return row


def build_metrics(
    workflows: list[dict[str, Any]],
    *,
    elapsed_ms: float,
    requested_workflows: int,
    concurrency: int,
    stage: int,
    arrival_rate: float = 0.0,
) -> dict[str, Any]:
    durations = [
        float(row["duration_ms"])
        for row in workflows
        if "duration_ms" in row
    ]
    commit_durations = [
        float(row["commit_ms"])
        for row in workflows
        if "commit_ms" in row
    ]
    request_durations = [
        float(row["request_duration_ms"])
        for row in workflows
        if "request_duration_ms" in row
    ]
    completion_durations = [
        float(row["commit_completion_ms"])
        for row in workflows
        if "commit_completion_ms" in row
    ]
    accepted = sum(
        int(
            row.get("commit_status") in EXPECTED_COMMIT
            or (
                row.get("commit_mode") == "auto"
                and bool(row.get("archive_id"))
            )
        )
        for row in workflows
    )
    polled = any("commit_poll_state" in row for row in workflows)
    completed = sum(
        row.get("commit_poll_state") == "completed"
        for row in workflows
    )
    window_completed = sum(
        row.get("window_commit_poll_state") == "completed"
        for row in workflows
    )
    window_timeouts = sum(
        row.get("window_commit_poll_state") == "timeout"
        for row in workflows
    )
    window_deferred = sum(
        bool(row.get("archive_id"))
        and row.get("window_commit_poll_state") is None
        for row in workflows
    )
    failed = sum(row.get("result") != "ok" for row in workflows)
    initial_429 = sum(
        int(row.get("commit_initial_status") == 429)
        for row in workflows
    )
    recovered_after_429 = sum(
        int(
            row.get("commit_initial_status") == 429
            and row.get("commit_status") in EXPECTED_COMMIT
            and not row.get("commit_retry_exhausted")
        )
        for row in workflows
    )
    final_429 = sum(
        int(
            bool(row.get("commit_retry_exhausted"))
            and row.get("commit_status") == 429
        )
        for row in workflows
    )
    total_commit_retries = sum(
        int(row.get("commit_retries") or 0)
        for row in workflows
    )
    commit_poll_missing_id = sum(
        row.get("result") == "commit:missing_archive_id"
        for row in workflows
    )
    auto_commit_missing_id = sum(
        row.get("result") == "auto_commit:missing_archive_id"
        for row in workflows
    )
    poll_http_counts: Counter[str] = Counter()
    failure_details: Counter[str] = Counter()
    for row in workflows:
        poll_http_counts.update(row.get("commit_poll_http_counts") or {})
        if row.get("result") != "ok":
            poll_body = row.get("commit_poll_body")
            detail = ""
            if isinstance(poll_body, dict):
                detail = str(
                    poll_body.get("error")
                    or poll_body.get("detail")
                    or poll_body.get("message")
                    or ""
                ).strip()
                status = poll_body.get("status")
                if isinstance(status, dict):
                    detail = str(
                        status.get("error")
                        or status.get("detail")
                        or status.get("message")
                        or detail
                    ).strip()
            if detail:
                failure_details[detail[:240]] += 1
            elif row.get("error"):
                failure_details[str(row["error"])[:240]] += 1
            else:
                failure_details[str(row.get("result", "unknown"))] += 1
    elapsed_s = elapsed_ms / 1000
    request_started = [
        float(row["started_at"])
        for row in workflows
        if "started_at" in row
    ]
    request_completed = [
        float(row["request_completed_at"])
        for row in workflows
        if "request_completed_at" in row
    ]
    request_submission_elapsed_s = (
        max(request_completed) - min(request_started)
        if request_started and request_completed
        else 0.0
    )
    arrival_lags = [
        float(row["arrival_lag_ms"])
        for row in workflows
        if "arrival_lag_ms" in row
    ]
    return {
        "stage": stage,
        "concurrency": concurrency,
        "target_arrival_rate": arrival_rate or None,
        "requested_workflows": requested_workflows,
        "finished_workflows": len(workflows),
        "accepted_commits": accepted,
        "commit_initial_429": initial_429,
        "commit_recovered_after_429": recovered_after_429,
        "commit_final_429": final_429,
        "commit_total_retries": total_commit_retries,
        "completed_commits": completed if polled else None,
        "window_completed_commits": window_completed if polled else None,
        "window_commit_timeouts": window_timeouts if polled else None,
        "window_deferred_commits": window_deferred if polled else None,
        "final_completed_commits": completed if polled else None,
        "commit_poll_missing_archive_id": commit_poll_missing_id,
        "auto_commit_missing_archive_id": auto_commit_missing_id,
        "commit_poll_http_counts": dict(poll_http_counts),
        "failed_workflows": failed,
        "workflow_success_rate": round(
            (len(workflows) - failed) / len(workflows),
            4,
        ) if workflows else 0,
        "elapsed_ms": round(elapsed_ms, 3),
        "workflows_per_second": round(
            len(workflows) / elapsed_s,
            3,
        ) if elapsed_s > 0 else 0,
        "request_submission_elapsed_ms": round(
            request_submission_elapsed_s * 1000,
            3,
        ),
        "requests_completed_per_second": round(
            len(request_completed) / request_submission_elapsed_s,
            3,
        ) if request_submission_elapsed_s > 0 else 0,
        "arrival_lag_ms": {
            "p50": percentile(arrival_lags, 50),
            "p95": percentile(arrival_lags, 95),
            "p99": percentile(arrival_lags, 99),
        },
        "workflow_latency_ms": {
            "p50": percentile(durations, 50),
            "p95": percentile(durations, 95),
            "p99": percentile(durations, 99),
        },
        "commit_latency_ms": {
            "p50": percentile(commit_durations, 50),
            "p95": percentile(commit_durations, 95),
            "p99": percentile(commit_durations, 99),
        },
        "request_latency_ms": {
            "p50": percentile(request_durations, 50),
            "p95": percentile(request_durations, 95),
            "p99": percentile(request_durations, 99),
        },
        "commit_completion_latency_ms": {
            "p50": percentile(completion_durations, 50),
            "p95": percentile(completion_durations, 95),
            "p99": percentile(completion_durations, 99),
        },
        "result_counts": dict(
            Counter(str(row.get("result", "missing")) for row in workflows)
        ),
        "failure_details": dict(failure_details.most_common()),
        "http_counts": dict(
            Counter(
                f"{step}:{row.get(f'{step}_status')}"
                for row in workflows
                for step in ("open", "message", "commit")
                if row.get(f"{step}_status") is not None
            )
        ),
    }


def build_tenant_metrics(workflows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in workflows:
        grouped.setdefault(str(row.get("tenant") or "default"), []).append(row)
    metrics: dict[str, Any] = {}
    for tenant_name, rows in sorted(grouped.items()):
        completion_durations = [
            float(row["commit_completion_ms"])
            for row in rows
            if "commit_completion_ms" in row
        ]
        accepted = sum(
            int(
                row.get("commit_status") in EXPECTED_COMMIT
                or (
                    row.get("commit_mode") == "auto"
                    and bool(row.get("archive_id"))
                )
            )
            for row in rows
        )
        completed = sum(
            row.get("commit_poll_state") == "completed"
            for row in rows
        )
        metrics[tenant_name] = {
            "workflows": len(rows),
            "accepted_commits": accepted,
            "window_completed_commits": sum(
                row.get("window_commit_poll_state") == "completed"
                for row in rows
            ),
            "final_completed_commits": completed,
            "initial_429": sum(
                row.get("commit_initial_status") == 429
                for row in rows
            ),
            "final_429": sum(
                row.get("commit_status") == 429
                for row in rows
            ),
            "failed_workflows": sum(
                row.get("result") != "ok"
                for row in rows
            ),
            "completion_latency_ms": {
                "p50": percentile(completion_durations, 50),
                "p95": percentile(completion_durations, 95),
                "p99": percentile(completion_durations, 99),
            },
        }
    return metrics


async def run_stage(
    client: httpx.AsyncClient,
    *,
    stage: int,
    workflows_count: int,
    concurrency: int,
    arrival_rate: float,
    args: argparse.Namespace,
    headers: dict[str, str],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    async def execute(index: int, scheduled_at: float | None = None) -> None:
        results.append(
            await run_workflow(
                client,
                index=index,
                stage=stage,
                tenant=args.tenants[index % len(args.tenants)],
                message_size=args.message_size,
                messages_per_session=args.messages_per_session,
                commit_mode=args.commit_mode,
                headers=headers,
                poll_commits=args.poll_commits,
                poll_timeout_s=args.poll_timeout,
                poll_interval_s=args.poll_interval,
                commit_retries=args.commit_retries,
                retry_backoff_s=args.retry_backoff,
                poll_retries=args.poll_retries,
                poll_backoff_s=args.poll_backoff,
                run_id=args.run_id,
                deferred_polling=args.deferred_polling,
                scheduled_at=scheduled_at,
            )
        )

    started = time.perf_counter()
    if arrival_rate > 0:
        stage_epoch = time.time()
        semaphore = asyncio.Semaphore(min(concurrency, workflows_count))

        async def scheduled_workflow(index: int) -> None:
            target_offset = index / arrival_rate
            delay = started + target_offset - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            async with semaphore:
                await execute(index, stage_epoch + target_offset)

        await asyncio.gather(
            *(scheduled_workflow(index) for index in range(workflows_count))
        )
    else:
        queue: asyncio.Queue[int] = asyncio.Queue()
        for index in range(workflows_count):
            queue.put_nowait(index)

        async def worker() -> None:
            while True:
                try:
                    index = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await execute(index)
                finally:
                    queue.task_done()

        await asyncio.gather(
            *(worker() for _ in range(min(concurrency, workflows_count)))
        )
    elapsed_ms = (time.perf_counter() - started) * 1000
    results.sort(key=lambda row: row["index"])
    return {
        "_elapsed_ms": elapsed_ms,
        "metrics": build_metrics(
            results,
            elapsed_ms=elapsed_ms,
            requested_workflows=workflows_count,
            concurrency=concurrency,
            stage=stage,
            arrival_rate=arrival_rate,
        ),
        "workflows": results,
        "tenant_metrics": build_tenant_metrics(results),
    }


async def drain_commits(
    client: httpx.AsyncClient,
    workflows: list[dict[str, Any]],
    *,
    headers: dict[str, str],
    timeout_s: float,
    interval_s: float,
    poll_concurrency: int,
    poll_retries: int,
    poll_backoff_s: float,
    tenants: list[dict[str, str]],
) -> dict[str, int]:
    """Continue polling accepted commits after stage workers finish."""
    pending = [
        row for row in workflows
        if row.get("archive_id")
        and row.get("commit_poll_state") not in {"completed", "failed"}
    ]
    deadline = time.perf_counter() + timeout_s
    semaphore = asyncio.Semaphore(max(1, poll_concurrency))
    tenant_by_name = {tenant["name"]: tenant for tenant in tenants}

    async def drain_one(row: dict[str, Any]) -> None:
        async with semaphore:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return
            previous_count = int(row.get("commit_poll_count") or 0)
            previous_elapsed = float(row.get("commit_poll_elapsed_ms") or 0.0)
            tenant = tenant_by_name.get(str(row.get("tenant") or ""), {})
            row_headers = dict(headers)
            if tenant.get("auth_key"):
                row_headers["X-Auth-Key"] = tenant["auth_key"]
            update = await poll_commit(
                client,
                session_id=str(row["session_id"]),
                archive_id=str(row["archive_id"]),
                headers=row_headers,
                timeout_s=remaining,
                interval_s=interval_s,
                max_retries=poll_retries,
                retry_backoff_s=poll_backoff_s,
            )
            row.update(update)
            row["commit_poll_count"] = previous_count + int(
                update.get("commit_poll_count") or 0
            )
            row["commit_poll_elapsed_ms"] = round(
                previous_elapsed
                + float(update.get("commit_poll_elapsed_ms") or 0.0),
                3,
            )
            row["commit_completion_ms"] = round(
                (
                    time.time()
                    - float(row.get("started_at") or time.time())
                ) * 1000,
                3,
            )
            row["result"] = classify_workflow(row)

    await asyncio.gather(*(drain_one(row) for row in pending))
    completed = sum(row.get("commit_poll_state") == "completed" for row in pending)
    failed = sum(row.get("commit_poll_state") == "failed" for row in pending)
    return {
        "pending_before": len(pending),
        "completed": completed,
        "failed": failed,
        "remaining": sum(
            row.get("commit_poll_state") not in {"completed", "failed"}
            for row in pending
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_tenants(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.tenant_config:
        path = Path(args.tenant_config).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not payload:
            raise ValueError("--tenant-config must contain a non-empty JSON list")
        tenants: list[dict[str, str]] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"tenant config item {index} must be an object")
            agent_id = str(item.get("agent_id") or "").strip()
            if not agent_id:
                raise ValueError(f"tenant config item {index} is missing agent_id")
            tenants.append(
                {
                    "name": str(item.get("name") or agent_id).strip(),
                    "agent_id": agent_id,
                    "auth_key": str(item.get("auth_key") or "").strip(),
                }
            )
        return tenants
    agent_ids = args.agent_ids or [args.agent_id]
    return [
        {"name": agent_id, "agent_id": agent_id, "auth_key": ""}
        for agent_id in agent_ids
    ]


async def run(args: argparse.Namespace) -> None:
    if args.tenant_config and args.agent_ids:
        raise ValueError("--tenant-config and --agent-ids are mutually exclusive")
    if args.messages_per_session < 1:
        raise ValueError("--messages-per-session must be at least 1")
    args.run_id = args.run_id or uuid4().hex[:12]
    args.tenants = load_tenants(args)
    tenant_names = [tenant["name"] for tenant in args.tenants]
    if len(set(tenant_names)) != len(tenant_names):
        raise ValueError("tenant names must be unique")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(
        connect=args.connect_timeout,
        read=args.read_timeout,
        write=args.write_timeout,
        pool=args.read_timeout,
    )
    limits = httpx.Limits(
        max_connections=args.max_connections,
        max_keepalive_connections=args.max_connections,
    )
    headers = {"X-Auth-Key": args.auth_key} if args.auth_key else {}
    stages = args.stages or [args.concurrency]
    rates = args.rates or [0.0] * len(stages)
    if len(rates) == 1 and len(stages) > 1:
        rates = rates * len(stages)
    if len(rates) != len(stages):
        raise ValueError("--rates must contain one value or match --stages")
    if any(rate < 0 for rate in rates):
        raise ValueError("--rates values must be non-negative")
    workflows_count = args.workflows or args.concurrency
    all_stages: list[dict[str, Any]] = []
    stage_drains: list[dict[str, int]] = []
    async with httpx.AsyncClient(
        base_url=args.url.rstrip("/"),
        timeout=timeout,
        limits=limits,
    ) as client:
        for stage, (concurrency, arrival_rate) in enumerate(
            zip(stages, rates),
            start=1,
        ):
            stage_result = await run_stage(
                client,
                stage=stage,
                workflows_count=workflows_count,
                concurrency=concurrency,
                arrival_rate=arrival_rate,
                args=args,
                headers=headers,
            )
            all_stages.append(stage_result)
            if (
                args.drain_between_stages
                and args.poll_commits
                and args.drain_timeout > 0
            ):
                stage_drain = await drain_commits(
                    client,
                    stage_result["workflows"],
                    headers=headers,
                    timeout_s=args.drain_timeout,
                    interval_s=args.poll_interval,
                    poll_concurrency=args.poll_concurrency,
                    poll_retries=args.poll_retries,
                    poll_backoff_s=args.poll_backoff,
                    tenants=args.tenants,
                )
                stage_drains.append({"stage": stage, **stage_drain})
            if args.stage_cooldown > 0 and stage < len(stages):
                await asyncio.sleep(args.stage_cooldown)

        probes: list[dict[str, Any]] = []
        if args.context_probes and all_stages:
            sessions = [
                row
                for row in all_stages[-1]["workflows"]
                if row.get("open_status") == 200
            ][: args.context_probes]
            probes = await asyncio.gather(
                *(
                    run_context_probe(
                        client,
                        session_id=str(row["session_id"]),
                        index=index,
                        agent_id=str(row["agent_id"]),
                        headers={
                            **headers,
                            **(
                                {"X-Auth-Key": tenant["auth_key"]}
                                if tenant.get("auth_key")
                                else {}
                            ),
                        },
                    )
                    for index, row in enumerate(sessions)
                    for tenant in args.tenants
                    if tenant["name"] == row["tenant"]
                )
            )

        drain = {
            "pending_before": 0,
            "completed": 0,
            "failed": 0,
            "remaining": 0,
        }
        if (
            args.poll_commits
            and args.drain_timeout > 0
            and not args.drain_between_stages
        ):
            workflows = [
                row
                for stage in all_stages
                for row in stage["workflows"]
            ]
            drain = await drain_commits(
                client,
                workflows,
                headers=headers,
                timeout_s=args.drain_timeout,
                interval_s=args.poll_interval,
                poll_concurrency=args.poll_concurrency,
                poll_retries=args.poll_retries,
                poll_backoff_s=args.poll_backoff,
                tenants=args.tenants,
            )

        for stage in all_stages:
            stage["metrics"] = build_metrics(
                stage["workflows"],
                elapsed_ms=stage["_elapsed_ms"],
                requested_workflows=workflows_count,
                concurrency=stage["metrics"]["concurrency"],
                stage=stage["metrics"]["stage"],
                arrival_rate=float(
                    stage["metrics"].get("target_arrival_rate") or 0.0
                ),
            )
            stage["tenant_metrics"] = build_tenant_metrics(stage["workflows"])

    payload = {
        "url": args.url,
        "run_id": args.run_id,
        "stages": [stage["metrics"] for stage in all_stages],
        "tenant_metrics": {
            str(stage["metrics"]["stage"]): stage["tenant_metrics"]
            for stage in all_stages
        },
        "overall_tenant_metrics": build_tenant_metrics(
            [
                row
                for stage in all_stages
                for row in stage["workflows"]
            ]
        ),
        "context_probe_count": len(probes),
        "context_statuses": dict(
            Counter(str(row.get("status")) for row in probes)
        ),
        "drain": drain,
        "stage_drains": stage_drains,
        "config": {
            "workflows": workflows_count,
            "stages": stages,
            "rates": rates,
            "max_connections": args.max_connections,
            "message_size": args.message_size,
            "messages_per_session": args.messages_per_session,
            "commit_mode": args.commit_mode,
            "tenants": [
                {"name": tenant["name"], "agent_id": tenant["agent_id"]}
                for tenant in args.tenants
            ],
            "poll_commits": args.poll_commits,
            "poll_timeout": args.poll_timeout,
            "poll_interval": args.poll_interval,
            "poll_retries": args.poll_retries,
            "poll_backoff": args.poll_backoff,
            "poll_concurrency": args.poll_concurrency,
            "deferred_polling": args.deferred_polling,
            "drain_between_stages": args.drain_between_stages,
            "stage_cooldown": args.stage_cooldown,
            "commit_retries": args.commit_retries,
            "retry_backoff": args.retry_backoff,
            "drain_timeout": args.drain_timeout,
        },
        "workflows": [
            row
            for stage in all_stages
            for row in stage["workflows"]
        ],
        "context_probes": probes,
    }
    (output / "client_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "summary.json").write_text(
        json.dumps(
            {
                "url": args.url,
                "run_id": args.run_id,
                "drain": payload["drain"],
                "stage_drains": payload["stage_drains"],
                "config": payload["config"],
                "stages": payload["stages"],
                "tenant_metrics": payload["tenant_metrics"],
                "overall_tenant_metrics": payload["overall_tenant_metrics"],
                "context_statuses": payload["context_statuses"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_csv(
        output / "workflows.csv",
        payload["workflows"],
    )
    print(json.dumps(payload["stages"], ensure_ascii=False, indent=2))
    print(f"output={output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18101")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument(
        "--stages",
        type=int,
        nargs="+",
        help="Concurrency stages, e.g. --stages 10 50 100 300",
    )
    parser.add_argument(
        "--rates",
        type=float,
        nargs="+",
        help=(
            "Open-loop workflow arrival rates per second; provide one value "
            "for all stages or one per stage"
        ),
    )
    parser.add_argument(
        "--workflows",
        type=int,
        default=0,
        help="Workflows per stage; defaults to the first concurrency value",
    )
    parser.add_argument("--agent-id", default="incident-load")
    parser.add_argument(
        "--agent-ids",
        nargs="+",
        help="Round-robin workflows across multiple agent ids",
    )
    parser.add_argument(
        "--tenant-config",
        default="",
        help=(
            "JSON list of {name, agent_id, auth_key}; auth keys are never "
            "written to results"
        ),
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Unique run id; generated when omitted",
    )
    parser.add_argument("--context-probes", type=int, default=0)
    parser.add_argument("--message-size", type=int, default=128)
    parser.add_argument("--messages-per-session", type=int, default=1)
    parser.add_argument(
        "--commit-mode",
        choices=("explicit", "auto"),
        default="explicit",
        help="Explicitly POST commit, or rely on server auto-commit",
    )
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--poll-commits", action="store_true")
    parser.add_argument("--poll-timeout", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--poll-retries", type=int, default=DEFAULT_POLL_RETRIES)
    parser.add_argument("--poll-backoff", type=float, default=DEFAULT_POLL_BACKOFF)
    parser.add_argument("--poll-concurrency", type=int, default=32)
    parser.add_argument(
        "--deferred-polling",
        action="store_true",
        help="Submit all workflows first and resolve commit states in drain",
    )
    parser.add_argument(
        "--commit-retries",
        type=int,
        default=DEFAULT_COMMIT_RETRIES,
        help="Retry count for commit HTTP 429 responses",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=DEFAULT_RETRY_BACKOFF,
        help="Minimum exponential backoff in seconds for commit retries",
    )
    parser.add_argument(
        "--drain-timeout",
        type=float,
        default=300.0,
        help="Extra seconds to poll accepted commits after all stages finish",
    )
    parser.add_argument(
        "--drain-between-stages",
        action="store_true",
        help="Drain accepted commits before starting the next concurrency stage",
    )
    parser.add_argument(
        "--stage-cooldown",
        type=float,
        default=0.0,
        help="Idle seconds between concurrency stages",
    )
    parser.add_argument("--max-connections", type=int, default=1000)
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--read-timeout", type=float, default=90.0)
    parser.add_argument("--write-timeout", type=float, default=30.0)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
