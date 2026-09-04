#!/usr/bin/env python3
"""Run a real process/container kill-9 recovery observation."""

from __future__ import annotations

import argparse
import json
import os
import signal
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


def health(url: str, timeout: float) -> tuple[bool, int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300, response.status, response.read().decode(errors="replace")[-1000:]
    except (OSError, urllib.error.URLError) as exc:
        return False, None, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--container", default="")
    parser.add_argument("--restart-command", default=os.getenv("ECHOMEM_RESTART_COMMAND", ""))
    parser.add_argument("--wait-s", type=float, default=120)
    parser.add_argument("--poll-s", type=float, default=2)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    started = now()
    if not args.pid and not args.container:
        result = {
            "status": INCONCLUSIVE,
            "reason": "pid or container is required; recovery was not externally exercised",
        }
    else:
        before = health(args.health_url, 5)
        try:
            if args.container:
                killed = subprocess.run(
                    ["docker", "kill", "--signal", "KILL", args.container],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if killed.returncode != 0:
                    result = {
                        "status": (
                            INCONCLUSIVE
                            if killed.returncode == 127
                            else FAIL
                        ),
                        "before_health": before[0],
                        "kill": {
                            "control": "docker",
                            "returncode": killed.returncode,
                            "stderr": killed.stderr[-2000:],
                        },
                        "reason": "container kill command is unavailable or failed",
                    }
                    result.update(
                        {
                            "started_at": started,
                            "finished_at": now(),
                            "health_url": args.health_url,
                        }
                    )
                    args.out = args.out.expanduser()
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.write_text(
                        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    print(json.dumps(result, ensure_ascii=False))
                    return 0 if result["status"] == PASS else 2
                kill_result = {
                    "control": "docker",
                    "returncode": killed.returncode,
                    "stderr": killed.stderr[-2000:],
                }
            else:
                os.kill(args.pid, signal.SIGKILL)
                kill_result = {"control": "pid", "pid": args.pid, "signal": "SIGKILL"}
        except FileNotFoundError as exc:
            result = {
                "status": INCONCLUSIVE,
                "before_health": before[0],
                "reason": f"kill control is unavailable: {exc}",
            }
            result.update(
                {
                    "started_at": started,
                    "finished_at": now(),
                    "health_url": args.health_url,
                }
            )
            args.out = args.out.expanduser()
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, ensure_ascii=False))
            return 2
        restart = None
        if args.restart_command:
            restart = subprocess.Popen(args.restart_command, shell=True, start_new_session=True)
        else:
            restart = None
        deadline = time.monotonic() + max(0.1, args.wait_s)
        observations = []
        recovered = False
        while time.monotonic() < deadline:
            observation = health(args.health_url, 5)
            observations.append({"at": now(), "healthy": observation[0], "status_code": observation[1]})
            if observation[0]:
                recovered = True
                break
            time.sleep(max(0.1, args.poll_s))
        result = {
            "status": PASS if before[0] and recovered else FAIL,
            "before_health": before[0],
            "kill": kill_result,
            "restart_command_supplied": bool(args.restart_command),
            "recovered": recovered,
            "observations": observations,
            "recovery_time_s": (
                (len(observations) - 1) * max(0.1, args.poll_s) if recovered else None
            ),
        }
        if restart is not None and restart.poll() is not None:
            result["restart_returncode"] = restart.returncode
    result.update({"started_at": started, "finished_at": now(), "health_url": args.health_url})
    args.out = args.out.expanduser()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
