#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


TERMINAL_STATUSES = {"succeeded", "failed", "done", "cancelled", "canceled"}


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh run_dir/report.html for a live generic benchmark until completion.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--csv", default="")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--idle-exit-rounds", type=int, default=8)
    parser.add_argument("--title", default="")
    parser.add_argument("--mirror-output", action="append", default=[], help="Optional HTML path(s) to mirror the refreshed report into after each render.")
    parser.add_argument("--mirror-final-output", action="append", default=[], help="Optional HTML path(s) to mirror the final completed report into once terminal.")
    parser.add_argument("--diagnostic-title", default="")
    parser.add_argument("--mirror-diagnostic-output", action="append", default=[], help="Optional HTML path(s) to mirror the refreshed diagnostic report into after each render.")
    parser.add_argument("--mirror-final-diagnostic-output", action="append", default=[], help="Optional HTML path(s) to mirror the final completed diagnostic into once terminal.")
    return parser.parse_args()


def detect_csv(run_dir: Path, explicit: str) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    candidates = sorted(run_dir.glob("**/*_results.csv"))
    if not candidates:
        raise FileNotFoundError(f"no benchmark csv found under {run_dir}")
    return candidates[0].resolve()


def run_once(run_dir: Path, csv_path: Path, title: str) -> None:
    script = Path(__file__).with_name("render_generic_benchmark_live_report.py")
    cmd = [
        sys.executable,
        str(script),
        "--run-dir",
        str(run_dir),
        "--csv",
        str(csv_path),
    ]
    if title:
        cmd.extend(["--title", title])
    subprocess.run(cmd, check=False)


def run_diagnostic_once(run_dir: Path, csv_path: Path, title: str) -> None:
    script = Path(__file__).with_name("render_generic_benchmark_health_diagnostic.py")
    cmd = [
        sys.executable,
        str(script),
        "--run-dir",
        str(run_dir),
        "--csv",
        str(csv_path),
    ]
    if title:
        cmd.extend(["--title", title])
    subprocess.run(cmd, check=False)


def mirror_file(source_path: Path, mirror_outputs: list[str]) -> None:
    if not mirror_outputs or not source_path.exists():
        return
    for raw_target in mirror_outputs:
        target = Path(raw_target).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)


def terminal_status(run_dir: Path, output_dir: Path) -> str:
    manifest_status = str(read_json(run_dir / "manifest.json").get("status") or "").strip().lower()
    summary_status = str(read_json(output_dir / "summary.json").get("status") or "").strip().lower()
    running_status = str(read_json(output_dir / "running_summary.json").get("status") or "").strip().lower()
    for status in (manifest_status, summary_status, running_status):
        if status:
            return status
    return ""


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    csv_path = detect_csv(run_dir, args.csv)
    output_dir = csv_path.parent
    previous_rows = -1
    idle_rounds = 0
    while True:
      run_once(run_dir, csv_path, args.title)
      mirror_file((run_dir / "report.html").resolve(), list(args.mirror_output or []))
      if args.diagnostic_title or args.mirror_diagnostic_output:
          run_diagnostic_once(run_dir, csv_path, args.diagnostic_title or args.title or "Generic Benchmark Diagnostic")
          mirror_file((run_dir / "diagnostic.html").resolve(), list(args.mirror_diagnostic_output or []))
      rows = sum(1 for _ in csv_path.open("r", encoding="utf-8", errors="replace")) - 1 if csv_path.exists() else 0
      idle_rounds = idle_rounds + 1 if rows == previous_rows else 0
      previous_rows = rows
      status = terminal_status(run_dir, output_dir)
      if status in TERMINAL_STATUSES:
          mirror_file((run_dir / "report.html").resolve(), list(args.mirror_final_output or []))
          mirror_file((run_dir / "diagnostic.html").resolve(), list(args.mirror_final_diagnostic_output or []))
      if status in TERMINAL_STATUSES and idle_rounds >= max(1, int(args.idle_exit_rounds)):
          return 0
      time.sleep(max(2.0, float(args.interval or 15.0)))


if __name__ == "__main__":
    raise SystemExit(main())
