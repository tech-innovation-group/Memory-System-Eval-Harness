#!/usr/bin/env python3
"""Orchestrate real fault, recovery, and cursor reconciliation cases."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(
    command: list[str],
    output: Path,
    *,
    timeout_s: float,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(0.1, timeout_s),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "timed_out": True,
            "timeout_s": timeout_s,
            "stdout": (exc.stdout or "")[-8000:],
            "stderr": (exc.stderr or "")[-8000:],
        }
    payload: dict[str, Any] = {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }
    if output.exists():
        try:
            payload["result"] = json.loads(output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run configured real EchoMem fault suite")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--commit-csv", type=Path)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--cursor-url-template", default="")
    parser.add_argument(
        "--cursor-uri-template",
        default="echo://sessions/{session}/current/commit_cursor.json",
    )
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument(
        "--case-timeout-s",
        type=float,
        default=300.0,
        help="每个故障/恢复/对账子探针的最大执行时间",
    )
    args = parser.parse_args()
    if args.case_timeout_s <= 0:
        parser.error("--case-timeout-s must be > 0")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []

    for index, fault in enumerate(plan.get("faults", []), start=1):
        output = args.out_dir / f"fault-{index:02d}.json"
        command = [
            sys.executable, str(Path(__file__).with_name("fault_injection.py")),
            "--kind", str(fault["kind"]), "--out", str(output),
        ]
        for flag in ("command", "endpoint", "action", "container", "signal", "timeout_s"):
            if fault.get(flag) not in (None, ""):
                command.extend([f"--{flag.replace('_', '-')}", str(fault[flag])])
        cases.append({
            "kind": fault["kind"],
            "execution": run(command, output, timeout_s=args.case_timeout_s),
        })

    fault_kinds = {
        str(case.get("kind", ""))
        for case in cases
        if str(case.get("kind", "")).startswith(("llm-", "vector-"))
    }
    if fault_kinds:
        fault_results = [
            (case["execution"].get("result") or {}).get("status", INCONCLUSIVE)
            for case in cases
            if case.get("kind") in fault_kinds
        ]
        aggregate_status = (
            FAIL if FAIL in fault_results
            else INCONCLUSIVE if INCONCLUSIVE in fault_results or NOT_IMPLEMENTED in fault_results
            else PASS
        )
        cases.append({
            "kind": "llm-vector-faults",
            "execution": {
                "result": {
                    "status": aggregate_status,
                    "fault_kinds": sorted(fault_kinds),
                    "reason": "all configured LLM/vector controls completed"
                    if aggregate_status == PASS
                    else "one or more configured LLM/vector controls did not complete",
                }
            },
        })

    recovery = plan.get("recovery")
    if recovery:
        output = args.out_dir / "recovery.json"
        command = [
            sys.executable, str(Path(__file__).with_name("recovery.py")),
            "--health-url", recovery["health_url"], "--out", str(output),
        ]
        for flag in ("pid", "container", "restart_command", "wait_s", "poll_s"):
            if recovery.get(flag) not in (None, ""):
                command.extend([f"--{flag.replace('_', '-')}", str(recovery[flag])])
        cases.append({
            "kind": "kill-9-recovery",
            "execution": run(command, output, timeout_s=args.case_timeout_s),
        })

    cursor = plan.get("cursor")
    if cursor and args.commit_csv:
        output = args.out_dir / "cursor-reconciliation.json"
        command = [
            sys.executable, str(Path(__file__).with_name("cursor_reconcile.py")),
            "--commit-csv", str(args.commit_csv),
            "--base-url", str(args.base_url),
            "--cursor-url-template", str(cursor.get("url_template", args.cursor_url_template)),
            "--cursor-uri-template", str(cursor.get("uri_template", args.cursor_uri_template)),
            "--auth-key", args.auth_key, "--auth-header", args.auth_header,
            "--out", str(output),
        ]
        if cursor.get("strict", False):
            command.append("--strict")
        cases.append({
            "kind": "cursor-reconciliation",
            "execution": run(command, output, timeout_s=args.case_timeout_s),
        })
    elif cursor:
        cases.append({
            "kind": "cursor-reconciliation",
            "execution": {"result": {
                "status": INCONCLUSIVE,
                "reason": "commit CSV is required for cursor reconciliation; capability was not externally tested",
            }},
        })

    statuses = [
        (case["execution"].get("result") or {}).get("status", INCONCLUSIVE)
        for case in cases
    ]
    status = (
        FAIL if FAIL in statuses
        else INCONCLUSIVE if INCONCLUSIVE in statuses or NOT_IMPLEMENTED in statuses
        else PASS
    )
    result = {
        "status": status,
        "created_at": now(),
        "plan": str(args.plan),
        "cases": cases,
        "summary": {
            "total": len(cases),
            "pass": statuses.count(PASS),
            "fail": statuses.count(FAIL),
            "not_implemented": statuses.count(NOT_IMPLEMENTED),
        },
    }
    output = args.out_dir / "fault-suite.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if status == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
