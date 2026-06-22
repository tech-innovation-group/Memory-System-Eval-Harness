#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


FAILURE_HEALTH = {"api_error", "timeout", "rate_limited", "retrieval_empty", "retrieval_error", "question_timeout"}


def retrieval_failed(row: dict[str, str]) -> bool:
    status = str(row.get("retrieval_status") or "").lower()
    return status not in {"", "ok"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def is_failed(row: dict[str, str]) -> bool:
    return (
        str(row.get("model_status") or "").lower() == "failed"
        or str(row.get("answer_status") or "").lower() == "failed"
        or retrieval_failed(row)
        or str(row.get("health_status") or "").lower() in FAILURE_HEALTH
    )


def is_recovered(row: dict[str, str]) -> bool:
    return (
        str(row.get("model_status") or "").lower() != "failed"
        and str(row.get("answer_status") or "").lower() != "failed"
        and not retrieval_failed(row)
        and str(row.get("health_status") or "").lower() not in FAILURE_HEALTH
    )


def question_key(row: dict[str, str]) -> str:
    return row.get("question_id") or "|".join(
        [row.get("sample_id", ""), row.get("question", ""), row.get("answer", "")]
    )


def retry_history_path(input_path: Path) -> Path:
    return Path(f"{input_path}.retry_failed_history.json")


def write_retry_history(input_path: Path, attempt: dict[str, Any]) -> None:
    path = retry_history_path(input_path)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    attempts.append(attempt)
    payload = {
        "input": str(input_path),
        "attempt_count": len(attempts),
        "latest": attempt,
        "attempts": attempts[-20:],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_retry_command(args: argparse.Namespace, question_ids: list[str], round_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("echomemory_memory_qa.py")),
        "--dataset",
        str(Path(args.dataset).expanduser().resolve()),
        "--out-dir",
        str(round_dir),
        "--sample",
        "all",
        "--questions",
        ",".join(question_ids),
        "--echomem-root",
        args.echomem_root,
        "--workspace",
        args.workspace,
        "--account",
        args.account,
        "--user-id",
        args.user_id,
        "--agent-id",
        args.agent_id,
        "--prompt-mode",
        args.prompt_mode,
        "--top-k",
        str(args.top_k),
        "--score-threshold",
        str(args.score_threshold),
        "--memory-budget-chars",
        str(args.memory_budget_chars),
        "--user-memory-budget-chars",
        str(args.user_memory_budget_chars),
        "--agent-memory-budget-chars",
        str(args.agent_memory_budget_chars),
        "--retrieval-mode",
        args.retrieval_mode,
        "--answer-base-url",
        args.answer_base_url,
        "--answer-model",
        args.answer_model,
        "--model-retries",
        str(args.model_retries),
        "--timeout-s",
        str(args.timeout_s),
        "--question-timeout-s",
        str(args.question_timeout_s),
        "--tool-set",
        args.tool_set,
        "--tool-search-limit",
        str(args.tool_search_limit),
        "--tool-min-score",
        str(args.tool_min_score),
        "--tool-log-chars",
        str(args.tool_log_chars),
        "--prefetch-read-count",
        str(args.prefetch_read_count),
        "--prefetch-context-chars",
        str(args.prefetch_context_chars),
        "--max-iterations",
        str(args.max_iterations),
    ]
    if args.echomem_config:
        command += ["--echomem-config", args.echomem_config]
    if args.retrieval_mode == "local":
        command.append("--local-session-summaries" if args.local_session_summaries else "--no-local-session-summaries")
        command.append("--local-atoms" if args.local_atoms else "--no-local-atoms")
        command.append("--local-messages" if args.local_messages else "--no-local-messages")
        command.append("--local-timeline-hints" if args.local_timeline_hints else "--no-local-timeline-hints")
        command.append("--local-memory-artifacts" if args.local_memory_artifacts else "--no-local-memory-artifacts")
    else:
        command += [
            "--no-local-session-summaries",
            "--no-local-atoms",
            "--no-local-messages",
            "--no-local-timeline-hints",
            "--no-local-memory-artifacts",
        ]
    command.append("--vikingboat-tool-loop" if args.vikingboat_tool_loop else "--no-vikingboat-tool-loop")
    command.append("--vikingboat-compat" if args.vikingboat_compat else "--no-vikingboat-compat")
    command.append("--initial-tool-prefetch" if args.initial_tool_prefetch else "--no-initial-tool-prefetch")
    command.append("--fallback-to-one-shot" if args.fallback_to_one_shot else "--no-fallback-to-one-shot")
    if args.answer_token:
        command += ["--answer-token", args.answer_token]
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description="Retry failed EchoMemory QA rows and merge successful retries back into the source CSV.")
    parser.add_argument("--input", required=True, help="Original EchoMemory QA result CSV")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--echomem-root", required=True)
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--prompt-mode", choices=["one_shot", "vikingboat_lite", "vikingboat_compat"], default="one_shot")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--score-threshold", type=float, default=0.1)
    parser.add_argument("--memory-budget-chars", type=int, default=6000)
    parser.add_argument("--user-memory-budget-chars", type=int, default=4000)
    parser.add_argument("--agent-memory-budget-chars", type=int, default=2000)
    parser.add_argument("--retrieval-mode", choices=["search", "local", "both"], default="search")
    parser.add_argument("--answer-base-url", required=True)
    parser.add_argument("--answer-model", default="gpt-5.5")
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--question-timeout-s", type=int, default=600)
    parser.add_argument("--tool-set", default="search_read")
    parser.add_argument("--tool-search-limit", type=int, default=20)
    parser.add_argument("--tool-min-score", type=float, default=0.35)
    parser.add_argument("--tool-log-chars", type=int, default=1200)
    parser.add_argument("--prefetch-read-count", type=int, default=4)
    parser.add_argument("--prefetch-context-chars", type=int, default=5000)
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_true", default=False)
    parser.add_argument("--no-vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_false")
    parser.add_argument("--vikingboat-compat", dest="vikingboat_compat", action="store_true", default=False)
    parser.add_argument("--no-vikingboat-compat", dest="vikingboat_compat", action="store_false")
    parser.add_argument("--initial-tool-prefetch", dest="initial_tool_prefetch", action="store_true", default=False)
    parser.add_argument("--no-initial-tool-prefetch", dest="initial_tool_prefetch", action="store_false")
    parser.add_argument("--fallback-to-one-shot", dest="fallback_to_one_shot", action="store_true", default=True)
    parser.add_argument("--no-fallback-to-one-shot", dest="fallback_to_one_shot", action="store_false")
    parser.add_argument("--local-session-summaries", dest="local_session_summaries", action="store_true", default=False)
    parser.add_argument("--no-local-session-summaries", dest="local_session_summaries", action="store_false")
    parser.add_argument("--local-atoms", dest="local_atoms", action="store_true", default=False)
    parser.add_argument("--no-local-atoms", dest="local_atoms", action="store_false")
    parser.add_argument("--local-messages", dest="local_messages", action="store_true", default=False)
    parser.add_argument("--no-local-messages", dest="local_messages", action="store_false")
    parser.add_argument("--local-timeline-hints", dest="local_timeline_hints", action="store_true", default=False)
    parser.add_argument("--no-local-timeline-hints", dest="local_timeline_hints", action="store_false")
    parser.add_argument("--local-memory-artifacts", dest="local_memory_artifacts", action="store_true", default=False)
    parser.add_argument("--no-local-memory-artifacts", dest="local_memory_artifacts", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(input_path)
    failed_rows = [row for row in rows if is_failed(row)]
    question_ids = [row.get("question_id", "") for row in failed_rows if row.get("question_id")]
    question_ids = list(dict.fromkeys(question_ids))
    summary: dict[str, Any] = {
        "input": str(input_path),
        "backend": "echomemory",
        "failed_rows": len(failed_rows),
        "question_ids": question_ids,
        "dry_run": bool(args.dry_run),
    }
    if not question_ids or args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return

    round_dir = out_dir / "retry_failed_round1"
    round_dir.mkdir(parents=True, exist_ok=True)
    command = build_retry_command(args, question_ids, round_dir)
    redacted = ["******" if index and command[index - 1] in {"--answer-token"} else item for index, item in enumerate(command)]
    print("$ " + " ".join(redacted), flush=True)
    proc = subprocess.run(command, cwd=str(Path(__file__).resolve().parent.parent), text=True)
    summary["returncode"] = proc.returncode
    retry_csv = round_dir / "echomemory_memory_qa_results.csv"
    summary["retry_csv"] = str(retry_csv)
    if proc.returncode != 0 or not retry_csv.exists():
        summary["merged"] = False
        (out_dir / "retry_failed_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit(proc.returncode or 1)

    retry_rows = read_csv(retry_csv)
    retry_by_key = {question_key(row): row for row in retry_rows}
    replaced = 0
    still_failed = 0
    recovered_question_ids: list[str] = []
    still_failed_question_ids: list[str] = []
    missing_retry_result_question_ids: list[str] = []
    merged_rows = []
    for row in rows:
        key = question_key(row)
        retry_row = retry_by_key.get(key)
        if retry_row and is_recovered(retry_row):
            merged_rows.append(retry_row)
            replaced += 1
            qid = str(retry_row.get("question_id") or row.get("question_id") or "").strip()
            if qid and qid not in recovered_question_ids:
                recovered_question_ids.append(qid)
        else:
            merged_rows.append(row)
            if retry_row and is_failed(retry_row):
                still_failed += 1
                qid = str(retry_row.get("question_id") or row.get("question_id") or "").strip()
                if qid and qid not in still_failed_question_ids:
                    still_failed_question_ids.append(qid)
            elif not retry_row:
                qid = str(row.get("question_id") or "").strip()
                if qid and qid not in missing_retry_result_question_ids:
                    missing_retry_result_question_ids.append(qid)

    backup = input_path.with_suffix(input_path.suffix + ".before_failed_retry.bak")
    backup.write_text(input_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    write_csv(input_path, merged_rows)
    summary.update(
        {
            "merged": True,
            "backup": str(backup),
            "retried_rows": len(retry_rows),
            "replaced_rows": replaced,
            "still_failed_after_retry": still_failed,
            "recovered_question_ids": recovered_question_ids,
            "still_failed_question_ids": still_failed_question_ids,
            "missing_retry_result_question_ids": missing_retry_result_question_ids,
        }
    )
    retry_summary_path = out_dir / "retry_failed_summary.json"
    retry_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_retry_history(
        input_path,
        {
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "echomemory",
            "retry_csv": str(retry_csv),
            "retry_summary": str(retry_summary_path),
            "failed_questions_before": len(question_ids),
            "retried_rows": len(retry_rows),
            "recovered_questions": len(recovered_question_ids),
            "still_failed_questions": len(still_failed_question_ids),
            "missing_retry_result_questions": len(missing_retry_result_question_ids),
            "recovered_question_ids": recovered_question_ids,
            "still_failed_question_ids": still_failed_question_ids,
            "missing_retry_result_question_ids": missing_retry_result_question_ids,
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
