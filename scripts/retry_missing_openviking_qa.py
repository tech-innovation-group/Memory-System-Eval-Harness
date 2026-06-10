#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


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


def build_command(args: argparse.Namespace, question_ids: list[str], round_dir: Path) -> list[str]:
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


def redacted(command: list[str]) -> list[str]:
    secret_flags = {"--answer-token", "--openviking-api-key"}
    return ["******" if index and command[index - 1] in secret_flags else item for index, item in enumerate(command)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Retry missing MemoryBench Agent OpenViking QA questions and append them to the source CSV.")
    parser.add_argument("--input", required=True, help="Original MemoryBench Agent OpenViking QA result CSV")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--question-ids", required=True)
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
    original_rows = read_csv(input_path)
    existing_ids = {row.get("question_id", "") for row in original_rows if row.get("question_id")}
    question_ids = [item.strip() for item in args.question_ids.split(",") if item.strip()]
    question_ids = [qid for qid in dict.fromkeys(question_ids) if qid not in existing_ids]
    summary: dict[str, Any] = {
        "input": str(input_path),
        "requested_questions": len([item for item in args.question_ids.split(",") if item.strip()]),
        "missing_questions": len(question_ids),
        "question_ids": question_ids,
        "dry_run": bool(args.dry_run),
    }
    if not question_ids or args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        (out_dir / "retry_missing_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    round_dir = out_dir / "retry_missing_round1"
    round_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(args, question_ids, round_dir)
    print("$ " + " ".join(redacted(command)), flush=True)
    proc = subprocess.run(command, cwd=str(Path(__file__).resolve().parent.parent), text=True)
    summary["returncode"] = proc.returncode
    retry_csv = round_dir / "openviking_memory_qa_results.csv"
    summary["retry_csv"] = str(retry_csv)
    if proc.returncode != 0 or not retry_csv.exists():
        summary["merged"] = False
        (out_dir / "retry_missing_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit(proc.returncode or 1)

    retry_rows = read_csv(retry_csv)
    retry_rows = [row for row in retry_rows if row.get("question_id") in set(question_ids)]
    retry_by_id = {row.get("question_id", ""): row for row in retry_rows if row.get("question_id")}
    appended_rows = [retry_by_id[qid] for qid in question_ids if qid in retry_by_id]
    backup = input_path.with_suffix(input_path.suffix + ".before_missing_retry.bak")
    backup.write_text(input_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    write_csv(input_path, original_rows + appended_rows)
    summary.update(
        {
            "merged": True,
            "backup": str(backup),
            "retried_rows": len(retry_rows),
            "appended_rows": len(appended_rows),
            "still_missing_after_retry": len(question_ids) - len(appended_rows),
        }
    )
    (out_dir / "retry_missing_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
