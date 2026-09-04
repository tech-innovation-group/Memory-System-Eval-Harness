#!/usr/bin/env python3
"""Probe concurrent Commit behavior on one real EchoMem session."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._client import EchoMemHTTP, extract_archive, load_tenant_specs, status_from
except ImportError:
    from _client import EchoMemHTTP, extract_archive, load_tenant_specs, status_from


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def poll(client: EchoMemHTTP, session_id: str, archive_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    states: list[str] = []
    while time.monotonic() < deadline:
        response = client.commit_status(session_id, archive_id)
        payload = response.payload if isinstance(response.payload, dict) else {}
        state = status_from(payload)
        state = state.lower() if state else ""
        if state:
            states.append(state)
        if state in {"completed", "complete", "succeeded", "success", "failed", "error"}:
            return {
                "status_code": response.status_code,
                "state": state,
                "states": states,
            }
        time.sleep(0.5)
    return {"status_code": None, "state": "timeout", "states": states}


def run_case(client: EchoMemHTTP, concurrency: int, timeout_s: float) -> dict[str, Any]:
    session_id, _ = client.open_session(client.tenant_id, f"pr421-concurrent-{uuid.uuid4().hex}")
    message_ids: list[str] = []
    for index in range(concurrency):
        message_id = f"pr421-concurrent-message-{uuid.uuid4().hex}"
        response = client.add_message(
            session_id,
            message_id,
            f"PR421 concurrent commit probe {index} {uuid.uuid4().hex}",
        )
        if response.status_code is None or response.status_code >= 400:
            return {
                "status": "FAIL",
                "phase": "seed",
                "session_id": session_id,
                "message_status": response.status_code,
                "error": response.error,
            }
        message_ids.append(message_id)

    def submit(index: int) -> dict[str, Any]:
        started = time.monotonic()
        response = client.commit(session_id)
        return {
            "index": index,
            "status_code": response.status_code,
            "elapsed_s": time.monotonic() - started,
            "request_id": response.headers.get("X-Request-ID", ""),
            "operation_id": (
                response.payload.get("operation_id")
                if isinstance(response.payload, dict)
                else ""
            ),
            "archive_id": extract_archive(response.payload),
            "error": response.error,
        }

    submissions: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(submit, index) for index in range(concurrency)]
        for future in as_completed(futures):
            submissions.append(future.result())

    accepted = [item for item in submissions if item["status_code"] in {200, 202}]
    terminal: list[dict[str, Any]] = []
    poll_items = [
        item for item in accepted if item.get("archive_id")
    ]
    # Poll all accepted operations concurrently. Sequential polling can make
    # the test's deadline depend on the number of accepted commits.
    with ThreadPoolExecutor(max_workers=max(1, len(poll_items))) as executor:
        futures = {
            executor.submit(
                poll, client, session_id, item["archive_id"], timeout_s
            ): item["archive_id"]
            for item in poll_items
        }
        for future in as_completed(futures):
            terminal.append({
                "archive_id": futures[future],
                "poll": future.result(),
            })

    archive_ids = [item["archive_id"] for item in accepted if item.get("archive_id")]
    operation_ids = [item["operation_id"] for item in accepted if item.get("operation_id")]
    failed_terminal = [
        item for item in terminal
        if item["poll"].get("state") not in {"completed", "complete", "succeeded", "success"}
    ]
    status = "PASS" if accepted and not failed_terminal else "FAIL"
    return {
        "status": status,
        "session_id": session_id,
        "seed_messages": len(message_ids),
        "concurrency": concurrency,
        "submissions": submissions,
        "accepted_count": len(accepted),
        "rejected_count": len(submissions) - len(accepted),
        "archive_ids": archive_ids,
        "unique_archive_ids": len(set(archive_ids)),
        "operation_ids": operation_ids,
        "unique_operation_ids": len(set(operation_ids)),
        "terminal": terminal,
        "duplicate_acceptance": len(archive_ids) != len(set(archive_ids)),
        "failed_terminal_count": len(failed_terminal),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-s", type=float, default=120)
    parser.add_argument("--auth-header", default="X-Auth-Key")
    args = parser.parse_args()

    specs = load_tenant_specs(args.tenant_config)
    if not specs:
        raise RuntimeError("tenant config is empty")
    spec = specs[0]
    client = EchoMemHTTP(
        args.base_url,
        spec.auth_key,
        tenant_id=spec.tenant_id,
        user_id=spec.user_id,
        account_id=spec.account_id,
        agent_id="pr421-concurrent-commit",
        auth_header=args.auth_header,
    )
    result = {
        "created_at": now(),
        "base_url": args.base_url,
        "tenant": spec.tenant_id,
        "real_http": True,
        "mock_model": False,
        "case": run_case(client, max(2, args.concurrency), args.timeout_s),
        "interpretation": {
            "idempotency": "NOT_VERIFIED",
            "reason": "The public API has no documented idempotency key or replay contract.",
            "version_conflict": "OBSERVED_FROM_HTTP_AND_TERMINAL_STATES",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["case"], ensure_ascii=False))
    return 0 if result["case"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
