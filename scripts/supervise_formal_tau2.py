#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SIM_ROOT = ROOT / "external" / "tau2-bench" / "data" / "simulations"
LAUNCHER = ROOT / "scripts" / "launch_formal_tau2.py"
STATUS_SCRIPT = ROOT / "scripts" / "formal_benchmark_status.py"
HTML_SCRIPT = ROOT / "scripts" / "render_formal_benchmark_plan_html.py"

FORMAL_RUNS = {
    "airline": "formal_tau2_airline_base_gpt55_20260606",
    "retail": "formal_tau2_retail_base_gpt55_20260606",
    "telecom": "formal_tau2_telecom_base_gpt55_20260606",
    "banking_knowledge": "formal_tau2_banking_knowledge_bm25_gpt55_20260606",
}

EXPECTED = {
    "airline": 50,
    "retail": 114,
    "telecom": 114,
    "banking_knowledge": 97,
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{now()}] {message}"
    print(line, flush=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def result_count(domain: str) -> int:
    run_name = FORMAL_RUNS[domain]
    result_path = SIM_ROOT / run_name / "results.json"
    data = read_json(result_path)
    sims = data.get("simulations") if isinstance(data, dict) else None
    return len(sims) if isinstance(sims, list) else 0


def is_complete(domain: str) -> bool:
    return result_count(domain) >= EXPECTED[domain]


def etime_seconds(value: str) -> int:
    days = 0
    rest = value.strip()
    if "-" in rest:
        day_text, rest = rest.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            days = 0
    parts = rest.split(":")
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return 0
    if len(nums) == 3:
        hours, minutes, seconds = nums
    elif len(nums) == 2:
        hours, minutes, seconds = 0, nums[0], nums[1]
    else:
        return 0
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def active_tau2_processes(domain: str) -> list[dict[str, Any]]:
    run_name = FORMAL_RUNS[domain]
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,etime=,command="],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return []
    processes: list[dict[str, Any]] = []
    current_pid = os.getpid()
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, etime, command = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        args = command.split()
        if run_name in command and is_tau2_run_command(args):
            process_kind = "tau2_run"
        elif is_tau2_launcher_command(args, domain):
            process_kind = "launcher"
        else:
            continue
        processes.append({"pid": pid, "etime": etime, "age_seconds": etime_seconds(etime), "kind": process_kind})
    return processes


def active_tau2_pids(domain: str) -> list[int]:
    return [item["pid"] for item in active_tau2_processes(domain)]


def is_tau2_run_command(args: list[str]) -> bool:
    if not args:
        return False
    names = [Path(arg).name.lower() for arg in args]
    for index, name in enumerate(names):
        if index > 2:
            break
        if name == "tau2" and index + 1 < len(args) and args[index + 1] == "run":
            return True
        if name in {"python", "python3", "python3.12"} and index + 2 < len(args):
            if args[index + 1] == "-m" and args[index + 2] in {"tau2", "tau2.cli"} and "run" in args[index + 3:index + 5]:
                return True
    return False


def is_tau2_launcher_command(args: list[str], domain: str) -> bool:
    if not args:
        return False
    names = [Path(arg).name.lower() for arg in args]
    for index, name in enumerate(names[:3]):
        if name not in {"python", "python3", "python3.12"}:
            continue
        if index + 1 >= len(args) or Path(args[index + 1]).name != "launch_formal_tau2.py":
            continue
        tail = args[index + 2:]
        for offset, value in enumerate(tail):
            if value == "--domains":
                domains: list[str] = []
                for candidate in tail[offset + 1:]:
                    if candidate.startswith("--"):
                        break
                    domains.append(candidate)
                return domain in domains
        return False
    return False


def refresh_status(log_path: Path) -> None:
    for script in (STATUS_SCRIPT, HTML_SCRIPT):
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if proc.returncode != 0:
            log(log_path, f"refresh failed script={script.name} rc={proc.returncode} output={proc.stdout[-500:]}")


def latest_active_elapsed_seconds(domain: str) -> int:
    run_name = FORMAL_RUNS[domain]
    run_log = SIM_ROOT / run_name / "run.log"
    if not run_log.exists():
        return 0
    try:
        lines = run_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return 0
    for line in reversed(lines):
        if not line.startswith("Status: "):
            continue
        values = [int(item) for item in re.findall(r"\((\d+)s(?:\s+R\d+)?\)", line)]
        return max(values, default=0)
    return 0


def result_age_seconds(domain: str) -> float | None:
    result_path = SIM_ROOT / FORMAL_RUNS[domain] / "results.json"
    if not result_path.exists():
        return None
    try:
        return time.time() - result_path.stat().st_mtime
    except Exception:
        return None


def terminate_stale_tau2(domain: str, processes: list[dict[str, Any]], args: argparse.Namespace, log_path: Path) -> bool:
    threshold = int(args.timeout) + int(args.stall_grace_seconds)
    if not processes:
        return False
    active_elapsed = latest_active_elapsed_seconds(domain)
    oldest_process = max(int(item.get("age_seconds") or 0) for item in processes)
    result_age = result_age_seconds(domain)
    result_stale = result_age is None or result_age > threshold
    if active_elapsed <= threshold or oldest_process <= threshold or not result_stale:
        return False
    pids = [int(item["pid"]) for item in processes]
    log(
        log_path,
        f"domain={domain} stale_timeout pids={pids} active_elapsed={active_elapsed}s oldest_process={oldest_process}s result_age={round(result_age, 1) if result_age is not None else '-'}s; sending SIGTERM",
    )
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    return True


def launch_domain(domain: str, args: argparse.Namespace, log_path: Path) -> int:
    cmd = [
        sys.executable,
        str(LAUNCHER),
        "--domains",
        domain,
        "--max-concurrency",
        str(args.max_concurrency),
        "--timeout",
        str(args.timeout),
        "--log-level",
        args.log_level,
    ]
    if args.config:
        cmd.extend(["--config", args.config])
    run_name = FORMAL_RUNS[domain]
    log(log_path, f"launch domain={domain} rows={result_count(domain)}/{EXPECTED[domain]} cmd={' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=ROOT)
    while proc.poll() is None:
        active_processes = active_tau2_processes(domain)
        if active_processes:
            pids = [item["pid"] for item in active_processes]
            if terminate_stale_tau2(domain, active_processes, args, log_path):
                refresh_status(log_path)
                time.sleep(min(10, args.poll_seconds))
                continue
            log(log_path, f"domain={domain} launcher_active pids={pids} rows={result_count(domain)}/{EXPECTED[domain]}; waiting")
        else:
            log(log_path, f"domain={domain} launcher_active rows={result_count(domain)}/{EXPECTED[domain]}; waiting_for_tau2_child")
        time.sleep(args.poll_seconds)
    code = int(proc.returncode or 0)
    log(log_path, f"domain={domain} launcher_exit={code} rows={result_count(domain)}/{EXPECTED[domain]}")
    refresh_status(log_path)
    return code


def supervise(args: argparse.Namespace) -> int:
    log_path = Path(args.log_file)
    failures: dict[str, int] = {domain: 0 for domain in args.domains}
    log(log_path, f"supervisor start domains={','.join(args.domains)}")
    while True:
        all_done = True
        for domain in args.domains:
            run_name = FORMAL_RUNS[domain]
            rows = result_count(domain)
            if rows >= EXPECTED[domain]:
                log(log_path, f"domain={domain} complete rows={rows}/{EXPECTED[domain]}")
                continue
            all_done = False
            active_processes = active_tau2_processes(domain)
            pids = [item["pid"] for item in active_processes]
            if pids:
                if terminate_stale_tau2(domain, active_processes, args, log_path):
                    refresh_status(log_path)
                    time.sleep(min(10, args.poll_seconds))
                    break
                log(log_path, f"domain={domain} active pids={pids} rows={rows}/{EXPECTED[domain]}; waiting")
                refresh_status(log_path)
                time.sleep(args.poll_seconds)
                break
            if failures[domain] >= args.max_retries:
                log(log_path, f"domain={domain} retry_limit rows={rows}/{EXPECTED[domain]} failures={failures[domain]}")
                return 2
            code = launch_domain(domain, args, log_path)
            if code != 0 and not is_complete(domain):
                failures[domain] += 1
                log(log_path, f"domain={domain} incomplete after nonzero exit; retry={failures[domain]}/{args.max_retries}")
                time.sleep(args.retry_sleep_seconds)
                break
        if all_done:
            refresh_status(log_path)
            log(log_path, "supervisor complete all domains")
            return 0
        if args.once:
            refresh_status(log_path)
            log(log_path, "supervisor once exit")
            return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervise formal tau2-bench base runs without starting duplicates.")
    parser.add_argument("--domains", nargs="+", default=["airline", "retail", "telecom", "banking_knowledge"], choices=sorted(FORMAL_RUNS))
    parser.add_argument("--config", default="")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--stall-grace-seconds", type=int, default=180)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--retry-sleep-seconds", type=int, default=300)
    parser.add_argument("--max-retries", type=int, default=20)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-file", default=str(ROOT / "runs" / "formal_tau2_supervisor_20260606.log"))
    args = parser.parse_args()
    return supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
