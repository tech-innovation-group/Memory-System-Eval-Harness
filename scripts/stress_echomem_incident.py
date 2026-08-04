"""Stress the EchoMem incident path through the real HTTP API.

The server is expected to be started separately. Each workflow performs:
open -> append message -> commit. The commit is asynchronous; a 202 only
means that the background commit was accepted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx


async def run_workflow(
    client: httpx.AsyncClient,
    *,
    index: int,
    concurrency: int,
    agent_id: str,
) -> dict[str, Any]:
    session_id = f"incident-{concurrency}-{index}"
    started = time.perf_counter()
    result: dict[str, Any] = {
        "index": index,
        "session_id": session_id,
        "concurrency": concurrency,
    }
    try:
        response = await client.post(
            "/api/sessions/open",
            json={"agent_id": agent_id, "session_id": session_id},
        )
        result["open_status"] = response.status_code
        if response.status_code != 200:
            result["open_body"] = response.text[:500]
            return result

        response = await client.post(
            f"/api/sessions/{session_id}/messages",
            json={
                "role": "user",
                "content": f"Incident load test commit {concurrency} item {index}.",
            },
        )
        result["message_status"] = response.status_code
        if response.status_code != 200:
            result["message_body"] = response.text[:500]
            return result

        response = await client.post(
            f"/api/sessions/{session_id}/commit",
            json={},
        )
        result["commit_status"] = response.status_code
        result["commit_body"] = response.text[:500]
    except Exception as exc:
        result["exception"] = type(exc).__name__
        result["error"] = str(exc)[:500]
    finally:
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


async def run_context_probe(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    index: int,
    agent_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {"index": index, "session_id": session_id}
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
        )
        result["status"] = response.status_code
        result["body"] = response.text[:500]
    except Exception as exc:
        result["status"] = f"EXC:{type(exc).__name__}"
        result["error"] = str(exc)[:500]
    finally:
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def write_client_log(path: Path, workflows: list[dict[str, Any]]) -> None:
    lines = [
        "# EchoMem incident stress client log",
        "# Workflow: POST open -> POST messages -> POST commit",
    ]
    for row in workflows:
        fields = [
            f"index={row.get('index')}",
            f"session_id={row.get('session_id')}",
            f"open_status={row.get('open_status')}",
            f"message_status={row.get('message_status')}",
            f"commit_status={row.get('commit_status')}",
            f"duration_ms={row.get('duration_ms')}",
        ]
        if row.get("exception"):
            fields.extend(
                [f"exception={row['exception']}", f"error={row.get('error', '')}"]
            )
        lines.append("[client] " + " ".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_context_log(path: Path, probes: list[dict[str, Any]]) -> None:
    lines = ["# EchoMem build_context client probe log"]
    for row in probes:
        fields = [
            f"index={row.get('index')}",
            f"session_id={row.get('session_id')}",
            f"status={row.get('status')}",
            f"duration_ms={row.get('duration_ms')}",
        ]
        if row.get("error"):
            fields.append(f"error={row['error']}")
        lines.append("[client] " + " ".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    async with httpx.AsyncClient(
        base_url=args.url.rstrip("/"),
        timeout=timeout,
        limits=limits,
    ) as client:
        started = time.perf_counter()
        workflows = await asyncio.gather(
            *(
                run_workflow(
                    client,
                    index=index,
                    concurrency=args.concurrency,
                    agent_id=args.agent_id,
                )
                for index in range(args.concurrency)
            )
        )
        probes: list[dict[str, Any]] = []
        if args.context_probes:
            sessions = [
                row["session_id"]
                for row in workflows
                if row.get("open_status") == 200
            ][: args.context_probes]
            probes = await asyncio.gather(
                *(
                    run_context_probe(
                        client,
                        session_id=session_id,
                        index=index,
                        agent_id=args.agent_id,
                    )
                    for index, session_id in enumerate(sessions)
                )
            )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    payload = {
        "url": args.url,
        "concurrency": args.concurrency,
        "elapsed_ms": elapsed_ms,
        "workflow_statuses": dict(
            Counter(
                str(row.get("commit_status", row.get("exception", "missing")))
                for row in workflows
            )
        ),
        "context_statuses": dict(Counter(str(row["status"]) for row in probes)),
        "workflows": workflows,
        "context_probes": probes,
    }
    (output / "client_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_client_log(output / "client_request.log", workflows)
    if probes:
        write_context_log(output / "build_context_request.log", probes)
    print(json.dumps(payload["workflow_statuses"], ensure_ascii=False))
    print(f"elapsed_ms={elapsed_ms}")
    print(f"output={output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18101")
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--agent-id", default="incident-load")
    parser.add_argument("--context-probes", type=int, default=0)
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--read-timeout", type=float, default=90.0)
    parser.add_argument("--write-timeout", type=float, default=30.0)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
