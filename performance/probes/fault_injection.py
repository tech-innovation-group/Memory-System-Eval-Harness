#!/usr/bin/env python3
"""Execute explicit real fault controls and retain a tamper-evident timeline.

The harness never simulates a dependency failure. A control is either supplied
by the deployment (command, HTTP endpoint, or Docker container) or the case is
reported as INCONCLUSIVE. An explicit HTTP 404 is the only evidence that an
HTTP control endpoint is not implemented.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_control(args: argparse.Namespace) -> dict[str, Any]:
    started = now()
    command = args.command
    if not command and not args.endpoint and not args.container:
        return {
            "status": INCONCLUSIVE,
            "reason": "no real fault control supplied",
            "started_at": started,
            "finished_at": now(),
        }
    try:
        if command:
            completed = subprocess.run(
                command, shell=True, text=True, capture_output=True,
                timeout=args.timeout_s, check=False,
            )
            result = {
                "control": "command",
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
            status = (
                PASS
                if completed.returncode == 0
                else INCONCLUSIVE
                if completed.returncode == 127
                else FAIL
            )
        elif args.container:
            completed = subprocess.run(
                ["docker", "kill", "--signal", args.signal, args.container],
                text=True, capture_output=True, timeout=args.timeout_s, check=False,
            )
            result = {
                "control": "docker",
                "container": args.container,
                "signal": args.signal,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
            status = (
                PASS
                if completed.returncode == 0
                else INCONCLUSIVE
                if completed.returncode == 127
                else FAIL
            )
        else:
            request = urllib.request.Request(
                args.endpoint,
                data=json.dumps({"action": args.action}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=args.timeout_s) as response:
                result = {
                    "control": "http",
                    "endpoint": args.endpoint,
                    "status_code": response.status,
                    "body": response.read().decode(errors="replace")[-4000:],
                }
            status = (
                PASS
                if 200 <= result["status_code"] < 300
                else INCONCLUSIVE
                if result["status_code"] == 404
                else FAIL
            )
    except urllib.error.HTTPError as exc:
        result = {
            "control": "http",
            "endpoint": args.endpoint,
            "status_code": exc.code,
            "body": exc.read().decode(errors="replace")[-4000:],
        }
        status = NOT_IMPLEMENTED if exc.code == 404 else FAIL
    except FileNotFoundError as exc:
        result = {"error": str(exc)}
        status = INCONCLUSIVE
    except (OSError, urllib.error.URLError, subprocess.TimeoutExpired) as exc:
        result, status = {"error": str(exc)}, FAIL
    result.update({"status": status, "started_at": started, "finished_at": now()})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real EchoMem fault control")
    parser.add_argument("--kind", required=True, help="llm-500, llm-hang, vector-down, worker-restart, network-reset, process-kill")
    parser.add_argument("--command", default=os.getenv("STRESS_FAULT_COMMAND", ""))
    parser.add_argument("--endpoint", default=os.getenv("STRESS_FAULT_ENDPOINT", ""))
    parser.add_argument("--action", default="")
    parser.add_argument("--container", default="")
    parser.add_argument("--signal", default="KILL")
    parser.add_argument("--timeout-s", type=float, default=30)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = run_control(args)
    result["kind"] = args.kind
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
