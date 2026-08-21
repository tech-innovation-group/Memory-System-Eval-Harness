"""Run a repeatable EchoMem incident-path load test through HTTP.

The default workflow is:

    POST /api/sessions/open
    POST /api/sessions/{session_id}/messages
    POST /api/sessions/{session_id}/commit

The client does not retry requests. This keeps rejection, timeout, and
connection-error rates visible instead of hiding them behind a retry loop.
Use ``--poll-commits`` when the server returns an archive id and you also want
to measure asynchronous commit completion.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx


TERMINAL_COMMIT_STATES = {"completed", "done", "success", "failed", "error"}
EXPECTED_OPEN = {200}
EXPECTED_MESSAGE = {200}
EXPECTED_COMMIT = {200, 201, 202}


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


def classify_workflow(row: dict[str, Any]) -> str:
    if row.get("exception"):
        return f"exception:{row['exception']}"
    if row.get("open_status") not in EXPECTED_OPEN:
        return f"http:open:{row.get('open_status')}"
    if row.get("message_status") not in EXPECTED_MESSAGE:
        return f"http:message:{row.get('message_status')}"
    if row.get("commit_status") not in EXPECTED_COMMIT:
        return f"http:commit:{row.get('commit_status')}"
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


async def poll_commit(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    archive_id: str,
    headers: dict[str, str],
    timeout_s: float,
    interval_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    polls = 0
    deadline = time.perf_counter() + timeout_s
    last_body: Any = None
    while time.perf_counter() < deadline:
        response = await client.get(
            f"/api/sessions/{session_id}/commits/{archive_id}",
            headers=headers,
        )
        polls += 1
        last_body = response_summary(response)
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
    }


async def run_workflow(
    client: httpx.AsyncClient,
    *,
    index: int,
    stage: int,
    agent_id: str,
    message_size: int,
    headers: dict[str, str],
    poll_commits: bool,
    poll_timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    session_id = f"incident-{stage}-{index}"
    started = time.perf_counter()
    row: dict[str, Any] = {
        "index": index,
        "stage": stage,
        "session_id": session_id,
        "started_at": time.time(),
    }
    try:
        response, elapsed = await post_step(
            client,
            "/api/sessions/open",
            payload={"agent_id": agent_id, "session_id": session_id},
            headers=headers,
        )
        row["open_ms"] = elapsed
        row["open_status"] = response.status_code
        row["open_body"] = response_summary(response)["body"]
        if response.status_code not in EXPECTED_OPEN:
            return row

        response, elapsed = await post_step(
            client,
            f"/api/sessions/{session_id}/messages",
            payload={
                "role": "user",
                "content": (
                    f"Incident load test stage {stage} item {index}. "
                    + ("x" * max(0, message_size - 42))
                ),
            },
            headers=headers,
        )
        row["message_ms"] = elapsed
        row["message_status"] = response.status_code
        row["message_body"] = response_summary(response)["body"]
        if response.status_code not in EXPECTED_MESSAGE:
            return row

        response, elapsed = await post_step(
            client,
            f"/api/sessions/{session_id}/commit",
            payload={},
            headers=headers,
        )
        commit_response = response_summary(response)
        row["commit_ms"] = elapsed
        row["commit_status"] = response.status_code
        row["commit_body"] = commit_response["body"]
        row["archive_id"] = extract_archive_id(commit_response["body"])
        row["commit_poll_requested"] = poll_commits
        if (
            poll_commits
            and response.status_code in EXPECTED_COMMIT
            and row["archive_id"]
        ):
            row.update(
                await poll_commit(
                    client,
                    session_id=session_id,
                    archive_id=row["archive_id"],
                    headers=headers,
                    timeout_s=poll_timeout_s,
                    interval_s=poll_interval_s,
                )
            )
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
    accepted = sum(
        row.get("commit_status") in EXPECTED_COMMIT
        for row in workflows
    )
    polled = any("commit_poll_state" in row for row in workflows)
    completed = sum(
        row.get("commit_poll_state") == "completed"
        for row in workflows
    )
    failed = sum(row.get("result") != "ok" for row in workflows)
    commit_poll_missing_id = sum(
        row.get("result") == "commit:missing_archive_id"
        for row in workflows
    )
    elapsed_s = elapsed_ms / 1000
    return {
        "stage": stage,
        "concurrency": concurrency,
        "requested_workflows": requested_workflows,
        "finished_workflows": len(workflows),
        "accepted_commits": accepted,
        "completed_commits": completed if polled else None,
        "commit_poll_missing_archive_id": commit_poll_missing_id,
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
        "result_counts": dict(
            Counter(str(row.get("result", "missing")) for row in workflows)
        ),
        "http_counts": dict(
            Counter(
                f"{step}:{row.get(f'{step}_status')}"
                for row in workflows
                for step in ("open", "message", "commit")
                if f"{step}_status" in row
            )
        ),
    }


async def run_stage(
    client: httpx.AsyncClient,
    *,
    stage: int,
    workflows_count: int,
    concurrency: int,
    args: argparse.Namespace,
    headers: dict[str, str],
) -> dict[str, Any]:
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(workflows_count):
        queue.put_nowait(index)
    results: list[dict[str, Any]] = []

    async def worker() -> None:
        while True:
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                results.append(
                    await run_workflow(
                        client,
                        index=index,
                        stage=stage,
                        agent_id=args.agent_id,
                        message_size=args.message_size,
                        headers=headers,
                        poll_commits=args.poll_commits,
                        poll_timeout_s=args.poll_timeout,
                        poll_interval_s=args.poll_interval,
                    )
                )
            finally:
                queue.task_done()

    started = time.perf_counter()
    await asyncio.gather(
        *(worker() for _ in range(min(concurrency, workflows_count)))
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    results.sort(key=lambda row: row["index"])
    return {
        "metrics": build_metrics(
            results,
            elapsed_ms=elapsed_ms,
            requested_workflows=workflows_count,
            concurrency=concurrency,
            stage=stage,
        ),
        "workflows": results,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


async def run(args: argparse.Namespace) -> None:
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
    workflows_count = args.workflows or args.concurrency
    all_stages: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=args.url.rstrip("/"),
        timeout=timeout,
        limits=limits,
    ) as client:
        for stage, concurrency in enumerate(stages, start=1):
            all_stages.append(
                await run_stage(
                    client,
                    stage=stage,
                    workflows_count=workflows_count,
                    concurrency=concurrency,
                    args=args,
                    headers=headers,
                )
            )

        probes: list[dict[str, Any]] = []
        if args.context_probes and all_stages:
            sessions = [
                row["session_id"]
                for row in all_stages[-1]["workflows"]
                if row.get("open_status") == 200
            ][: args.context_probes]
            probes = await asyncio.gather(
                *(
                    run_context_probe(
                        client,
                        session_id=session_id,
                        index=index,
                        agent_id=args.agent_id,
                        headers=headers,
                    )
                    for index, session_id in enumerate(sessions)
                )
            )

    payload = {
        "url": args.url,
        "stages": [stage["metrics"] for stage in all_stages],
        "context_probe_count": len(probes),
        "context_statuses": dict(
            Counter(str(row.get("status")) for row in probes)
        ),
        "config": {
            "workflows": workflows_count,
            "stages": stages,
            "max_connections": args.max_connections,
            "message_size": args.message_size,
            "poll_commits": args.poll_commits,
            "poll_timeout": args.poll_timeout,
            "poll_interval": args.poll_interval,
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
                "config": payload["config"],
                "stages": payload["stages"],
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
        "--workflows",
        type=int,
        default=0,
        help="Workflows per stage; defaults to the first concurrency value",
    )
    parser.add_argument("--agent-id", default="incident-load")
    parser.add_argument("--context-probes", type=int, default=0)
    parser.add_argument("--message-size", type=int, default=128)
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--poll-commits", action="store_true")
    parser.add_argument("--poll-timeout", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-connections", type=int, default=1000)
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--read-timeout", type=float, default=90.0)
    parser.add_argument("--write-timeout", type=float, default=30.0)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
