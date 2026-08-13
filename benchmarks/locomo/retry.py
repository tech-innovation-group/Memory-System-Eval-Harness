#!/usr/bin/env python3
"""Retry failed or missing LoCoMo QA rows against existing memory."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.locomo.selection import parse_question_ids
from benchmarks.locomo.dataset import load_dataset
from shared.recovery import (
    merge_recovered_rows,
    qa_row_failed,
    question_id,
    read_csv,
    recovery_question_ids,
    write_csv,
)


def question_key(row: dict[str, str]) -> str:
    return question_id(row)


def qa_row_recovered(row: dict[str, str]) -> bool:
    return bool(question_key(row)) and not qa_row_failed(row)


def retry_question_ids(
    mode: str,
    original_rows: list[dict[str, str]],
    expected_question_ids: list[str],
) -> list[str]:
    return recovery_question_ids(
        mode,
        original_rows,
        expected_question_ids,
    )


def merge_retry_rows(
    original_rows: list[dict[str, str]],
    retry_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    merged, summary = merge_recovered_rows(original_rows, retry_rows)
    return (
        [dict(row) for row in merged],
        {
            "replaced": summary["replaced"],
            "appended": summary["appended"],
            "recovered": summary["recovered"],
        },
    )


def latest_qa_csv(root: Path) -> Path | None:
    candidates = list(root.glob("*/qa_results.csv"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def build_retry_command(
    *,
    project_root: Path,
    dataset: Path,
    sample: str,
    question_ids: list[str],
    round_dir: Path,
    resume_source: Path,
    eval_args: list[str],
) -> list[str]:
    return [
        sys.executable,
        str(project_root / "benchmarks" / "locomo" / "run_eval.py"),
        "--dataset",
        str(dataset),
        "--sample",
        sample,
        "--question-ids",
        ",".join(question_ids),
        "--resume-qa",
        str(resume_source),
        "--out-dir",
        str(round_dir),
        *eval_args,
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retry failed or missing LoCoMo QA rows using existing memory"
    )
    parser.add_argument("--input", required=True, help="Existing qa_results.csv")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="all")
    parser.add_argument(
        "--mode",
        choices=["failed", "missing", "failed-or-missing"],
        default="failed",
    )
    parser.add_argument("--output", default="", help="Merged CSV path")
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "eval_args",
        nargs=argparse.REMAINDER,
        help="Additional run_eval.py arguments after --",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    project_root = Path(__file__).resolve().parents[2]
    input_path = Path(args.input).expanduser().resolve()
    dataset_path = Path(args.dataset).expanduser().resolve()
    output_root = Path(args.out_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    original_rows = read_csv(input_path)
    jobs, _plans = load_dataset(dataset_path, sample_filter=args.sample)
    expected_ids = [job.question_id for job in jobs]
    question_ids = retry_question_ids(args.mode, original_rows, expected_ids)
    eval_args = list(args.eval_args)
    if eval_args[:1] == ["--"]:
        eval_args = eval_args[1:]

    round_dir = output_root / (
        "retry_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )
    command = build_retry_command(
        project_root=project_root,
        dataset=dataset_path,
        sample=args.sample,
        question_ids=question_ids,
        round_dir=round_dir,
        resume_source=input_path,
        eval_args=eval_args,
    )
    summary: dict[str, Any] = {
        "mode": args.mode,
        "input": str(input_path),
        "dataset": str(dataset_path),
        "sample": args.sample,
        "question_ids": question_ids,
        "question_count": len(question_ids),
        "dry_run": bool(args.dry_run),
        "command": command,
    }
    if not question_ids or args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    completed = subprocess.run(command, cwd=project_root, text=True)
    summary["returncode"] = completed.returncode
    retry_csv = latest_qa_csv(round_dir)
    summary["retry_csv"] = str(retry_csv or "")
    if completed.returncode != 0 or retry_csv is None:
        summary["merged"] = False
        summary_path = output_root / "retry_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise SystemExit(completed.returncode or 1)

    retry_rows = read_csv(retry_csv)
    merged_rows, merge_stats = merge_retry_rows(original_rows, retry_rows)
    if args.in_place:
        output_path = input_path
    elif args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = input_path.with_name(
            f"{input_path.stem}.retried{input_path.suffix}"
        )
    write_csv(output_path, merged_rows)
    summary.update(merge_stats)
    summary["merged"] = True
    summary["output"] = str(output_path)
    summary["remaining_failed"] = sum(
        1 for row in merged_rows if qa_row_failed(row)
    )
    summary_path = output_root / "retry_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
