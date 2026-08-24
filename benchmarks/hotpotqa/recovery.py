#!/usr/bin/env python3
"""Inspect and merge failed or missing HotpotQA QA rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.hotpotqa.dataset import load_dataset
from shared.recovery import (
    merge_recovered_rows,
    qa_row_failed,
    question_id,
    read_csv,
    recovery_question_ids,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect failed/missing HotpotQA questions and optionally "
            "merge a successful retry QA CSV"
        )
    )
    parser.add_argument("--qa", required=True, help="Original qa_results.csv")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--sample", default="all")
    parser.add_argument(
        "--mode",
        choices=["failed", "missing", "failed-or-missing"],
        default="failed-or-missing",
    )
    parser.add_argument("--retry-qa", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--out-dir", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    qa_path = Path(args.qa).expanduser().resolve()
    dataset_path = Path(args.dataset).expanduser().resolve()
    output_dir = Path(args.out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(qa_path)
    jobs, _plans = load_dataset(dataset_path, sample_filter=args.sample)
    expected_ids = [job.question_id for job in jobs]
    selected_ids = recovery_question_ids(args.mode, rows, expected_ids)
    summary: dict[str, Any] = {
        "mode": args.mode,
        "qa": str(qa_path),
        "dataset": str(dataset_path),
        "sample": args.sample,
        "expected_questions": len(expected_ids),
        "observed_questions": len({
            question_id(row) for row in rows if question_id(row)
        }),
        "recovery_question_ids": selected_ids,
        "recovery_count": len(selected_ids),
        "retry_command": [
            str(_PROJECT_ROOT / "benchmarks" / "hotpotqa" / "run_eval.py"),
            "--dataset",
            str(dataset_path),
            "--question-ids",
            ",".join(selected_ids),
        ],
    }
    if args.retry_qa:
        retry_path = Path(args.retry_qa).expanduser().resolve()
        merged, merge_summary = merge_recovered_rows(
            rows,
            read_csv(retry_path),
        )
        output_path = (
            Path(args.output).expanduser().resolve()
            if args.output
            else output_dir / "qa_results.recovered.csv"
        )
        write_csv(output_path, merged)
        summary.update({
            "retry_qa": str(retry_path),
            "output": str(output_path),
            "merge": merge_summary,
            "remaining_failures": [
                question_id(row) for row in merged if qa_row_failed(row)
            ],
        })
    summary_path = output_dir / "recovery_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
