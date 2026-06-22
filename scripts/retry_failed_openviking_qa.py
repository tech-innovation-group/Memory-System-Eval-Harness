#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


FAILURE_HEALTH = {"api_error", "timeout", "rate_limited", "retrieval_empty", "retrieval_error"}


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
        str(Path(__file__).with_name("openviking_memory_qa.py")),
        "--dataset",
        str(Path(args.dataset).expanduser().resolve()),
        "--out-dir",
        str(round_dir),
        "--sample",
        "all",
        "--questions",
        ",".join(question_ids),
        "--openviking-url",
        args.openviking_url,
        "--workspace",
        args.workspace,
        "--account",
        args.account,
        "--user-id",
        args.user_id,
        "--agent-id",
        args.agent_id,
        "--vikingbot-identity-mode",
        args.vikingbot_identity_mode,
        "--top-k",
        str(args.top_k),
        "--prompt-mode",
        args.prompt_mode,
        "--openviking-tool-set",
        args.openviking_tool_set,
        "--max-iterations",
        str(args.max_iterations),
        "--retrieval-retries",
        str(args.retrieval_retries),
        "--model-retries",
        str(args.model_retries),
        "--timeout-s",
        str(args.timeout_s),
        "--answer-base-url",
        args.answer_base_url,
        "--answer-model",
        args.answer_model,
    ]
    command.append("--group-chat" if args.group_chat else "--no-group-chat")
    command.append("--initial-agent-memory" if args.initial_agent_memory else "--no-initial-agent-memory")
    command.append("--openviking-tool-loop" if args.openviking_tool_loop else "--no-openviking-tool-loop")
    command.append("--read-openviking-content" if args.read_openviking_content else "--no-read-openviking-content")
    command += ["--no-query-expansion", "--no-lexical-fallback", "--no-archive-fallback", "--no-read-memory-files"]
    if args.vikingbot_workspace:
        command += ["--vikingbot-workspace", args.vikingbot_workspace]
    if args.memory_users:
        command += ["--memory-users", args.memory_users]
    if args.openviking_api_key:
        command += ["--openviking-api-key", args.openviking_api_key]
    if args.answer_token:
        command += ["--answer-token", args.answer_token]
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description="Retry failed MemoryBench Agent OpenViking QA rows and merge successful retries back into the source CSV.")
    parser.add_argument("--input", required=True, help="Original MemoryBench Agent OpenViking QA result CSV")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--openviking-url", default="http://127.0.0.1:1933")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--vikingbot-workspace", default="")
    parser.add_argument("--openviking-api-key", default="")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--memory-users", default="")
    parser.add_argument("--group-chat", dest="group_chat", action="store_true", default=True)
    parser.add_argument("--no-group-chat", dest="group_chat", action="store_false")
    parser.add_argument("--initial-agent-memory", dest="initial_agent_memory", action="store_true", default=True)
    parser.add_argument("--no-initial-agent-memory", dest="initial_agent_memory", action="store_false")
    parser.add_argument("--vikingbot-identity-mode", choices=["sender_session", "fixed"], default="sender_session")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--prompt-mode", choices=["vikingbot_aligned", "strict_memory"], default="vikingbot_aligned")
    parser.add_argument("--openviking-tool-loop", dest="openviking_tool_loop", action="store_true", default=True)
    parser.add_argument("--no-openviking-tool-loop", dest="openviking_tool_loop", action="store_false")
    parser.add_argument(
        "--openviking-tool-set",
        choices=["vikingboat_default", "vikingbot_native_safe", "vikingbot_openviking", "search_only"],
        default="vikingbot_native_safe",
    )
    parser.add_argument("--read-openviking-content", dest="read_openviking_content", action="store_true", default=True)
    parser.add_argument("--no-read-openviking-content", dest="read_openviking_content", action="store_false")
    parser.add_argument("--max-iterations", type=int, default=50)
    parser.add_argument("--retrieval-retries", type=int, default=2)
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--answer-base-url", required=True)
    parser.add_argument("--answer-model", default="gpt-5.5")
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
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
    redacted = ["******" if index and command[index - 1] in {"--answer-token", "--openviking-api-key"} else item for index, item in enumerate(command)]
    print("$ " + " ".join(redacted), flush=True)
    proc = subprocess.run(command, cwd=str(Path(__file__).resolve().parent.parent), text=True)
    summary["returncode"] = proc.returncode
    retry_csv = round_dir / "openviking_memory_qa_results.csv"
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
    (out_dir / "retry_failed_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_retry_history(
        input_path,
        {
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "openviking",
            "retry_csv": str(retry_csv),
            "retry_summary": str(out_dir / "retry_failed_summary.json"),
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
