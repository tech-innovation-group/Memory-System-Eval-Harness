"""LongMemEval retry selection and shard artifact merging."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.longmemeval.dataset import load_dataset
from shared.recovery import (
    merge_recovered_rows,
    qa_row_failed,
    question_id,
    read_csv,
    recovery_question_ids,
    write_csv,
)


def merge_csv_files(
    paths: Iterable[str | Path],
    destination: str | Path,
    *,
    prefer_successful: bool = True,
) -> list[dict[str, Any]]:
    merged_by_id: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for path in paths:
        source = Path(path)
        if not source.is_file():
            continue
        for row in read_csv(source):
            row_id = question_id(row)
            if not row_id:
                anonymous.append(row)
                continue
            previous = merged_by_id.get(row_id)
            if previous is None or (
                prefer_successful
                and qa_row_failed(previous)
                and not qa_row_failed(row)
            ):
                merged_by_id[row_id] = row
    rows = [
        merged_by_id[key]
        for key in sorted(merged_by_id)
    ] + anonymous
    write_csv(destination, rows)
    return rows


def merge_shard_artifacts(
    run_dirs: Iterable[str | Path],
    output_dir: str | Path,
) -> dict[str, Any]:
    sources = [Path(path) for path in run_dirs]
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    artifact_rows: dict[str, list[dict[str, Any]]] = {}
    for filename in (
        "import_results.csv",
        "qa_results.csv",
        "eval_results.csv",
    ):
        paths = [source / filename for source in sources]
        artifact_rows[filename] = merge_csv_files(
            paths,
            destination / filename,
            prefer_successful=filename == "qa_results.csv",
        )

    summaries: list[dict[str, Any]] = []
    for source in sources:
        path = source / "summary.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            summaries.append(payload)

    qa_rows = artifact_rows["qa_results.csv"]
    eval_rows = artifact_rows["eval_results.csv"]
    graded_rows = [
        row for row in eval_rows
        if not str(row.get("judge_error") or "").strip()
    ]
    correct = sum(
        1
        for row in graded_rows
        if str(row.get("correct") or "").strip().lower()
        in {"1", "true", "yes"}
    )
    summary = {
        "status": (
            "completed"
            if summaries
            and all(item.get("status") == "completed" for item in summaries)
            and not any(qa_row_failed(row) for row in qa_rows)
            else "failed"
        ),
        "benchmark": "longmemeval",
        "mode": "parallel-merged",
        "shards": len(sources),
        "shard_summaries": len(summaries),
        "total_questions": len(qa_rows),
        "qa_count": len(qa_rows),
        "qa_errors": sum(1 for row in qa_rows if qa_row_failed(row)),
        "correct": correct,
        "total": len(graded_rows),
        "overall_accuracy": (
            correct / len(graded_rows) if graded_rows else 0.0
        ),
        "total_prompt_tokens": sum(
            int(float(row.get("prompt_tokens") or 0)) for row in qa_rows
        ),
        "total_completion_tokens": sum(
            int(float(row.get("completion_tokens") or 0)) for row in qa_rows
        ),
        "source_result_dirs": [str(path) for path in sources],
    }
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect failed/missing LongMemEval questions and optionally "
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
            str(
                Path(__file__).resolve().parents[2]
                / "benchmarks"
                / "longmemeval"
                / "run_eval.py"
            ),
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
