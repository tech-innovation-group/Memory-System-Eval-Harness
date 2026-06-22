#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path("/Users/chx/locomo-eval-web")
ECHO_ROOT = Path("/Users/chx/Code/echomemory/echo_memory_v010")
PY = ECHO_ROOT / ".venv/bin/python"
DATASET = ROOT / "dataset/locomo10.json"
BASELINE_MANIFEST = ROOT / "runs/echomemory_v010_subset20_baseline_20260615/subset20_manifest.json"


def load_token() -> str:
    data = json.loads(BASELINE_MANIFEST.read_text(encoding="utf-8"))
    eval_cmd = data.get("eval_cmd") or []
    for index, arg in enumerate(eval_cmd):
        if arg == "--answer-token" and index + 1 < len(eval_cmd):
            return str(eval_cmd[index + 1] or "")
    raise RuntimeError("unable to find --answer-token in baseline manifest")


def read_manifest(run_dir: Path) -> dict:
    path = run_dir / "conv30_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_finished_question_ids(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    return {str(row.get("question_id") or "").strip() for row in rows if str(row.get("question_id") or "").strip()}


def merge_csvs(base_csv: Path, shard_csv: Path) -> None:
    base_rows = list(csv.DictReader(base_csv.open(encoding="utf-8"))) if base_csv.exists() else []
    shard_rows = list(csv.DictReader(shard_csv.open(encoding="utf-8"))) if shard_csv.exists() else []
    rows_by_qid: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in base_rows:
        qid = str(row.get("question_id") or "").strip()
        if not qid:
            continue
        rows_by_qid[qid] = row
        order.append(qid)
    for row in shard_rows:
        qid = str(row.get("question_id") or "").strip()
        if not qid:
            continue
        if qid not in rows_by_qid:
            order.append(qid)
        rows_by_qid[qid] = row
    merged_rows = [rows_by_qid[qid] for qid in order if qid in rows_by_qid]
    if not merged_rows:
        return
    fieldnames: list[str] = []
    for row in merged_rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with base_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged_rows)


def copy_recall_logs(shard_dir: Path, target_dir: Path) -> None:
    for path in shard_dir.glob("q*.recall.json"):
        shutil.copy2(path, target_dir / path.name)


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def run_subprocess(cmd: list[str], env: dict[str, str], cwd: Path, log_file: Path) -> None:
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"\n[resume] cmd={' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, stdout=log, stderr=log)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed with code {proc.returncode}: {' '.join(cmd)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume an interrupted EchoMemory conv-30 answer-refine full81 run in shards.")
    parser.add_argument("--run-dir", required=True, help="Run directory created by run_echomemory_v010_conv30_baseline_answer_refine.sh")
    parser.add_argument("--chunk-size", type=int, default=4)
    parser.add_argument("--max-batches", type=int, default=0, help="Optional limit on number of batches to run this invocation.")
    parser.add_argument("--sleep-s", type=float, default=2.0)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    qa_dir = run_dir / "echomemory_qa_answer_refine"
    csv_path = qa_dir / "echomemory_memory_qa_results.csv"
    manifest = read_manifest(run_dir)
    all_questions = list(manifest.get("question_ids") or [])
    if not all_questions:
        raise RuntimeError("manifest has no question_ids")

    finished = load_finished_question_ids(csv_path)
    remaining = [qid for qid in all_questions if qid not in finished]
    print(f"[resume] finished={len(finished)} remaining={len(remaining)} run_dir={run_dir}")
    if not remaining:
        print("[resume] nothing to do")
        return

    env = os.environ.copy()
    token = load_token()
    env["JUDGE_TOKEN"] = token
    env["LOCOMO_JUDGE_TOKEN"] = token
    env["DASHSCOPE_API_KEY"] = token
    env["JUDGE_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    env["JUDGE_MODEL"] = "deepseek-v4-flash"
    env["ECHOMEM_CHAT_API_KEY"] = token
    env["ECHOMEM_CHAT_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    env["ECHOMEM_CHAT_MODEL"] = "deepseek-v4-flash"
    env["ECHOMEM_CHAT_PROVIDER"] = "deepseek"

    batches = chunked(remaining, args.chunk_size)
    if args.max_batches and args.max_batches > 0:
        batches = batches[: args.max_batches]

    resume_root = run_dir / "resume_shards"
    resume_root.mkdir(parents=True, exist_ok=True)
    resume_log = run_dir / "conv30_answer_refine_resume.log"

    for batch_index, batch in enumerate(batches, 1):
        shard_dir = resume_root / f"batch_{batch_index:03d}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        shard_out = shard_dir / "echomemory_qa_resume"
        questions_arg = ",".join(batch)
        cmd = [
            str(PY),
            str(ROOT / "scripts/echomemory_memory_qa.py"),
            "--dataset", str(DATASET),
            "--out-dir", str(shard_out),
            "--sample", "conv-30",
            "--questions", questions_arg,
            "--echomem-root", str(ECHO_ROOT),
            "--workspace", str(manifest["workspace"]),
            "--account", str(manifest["account"]),
            "--user-id", str(manifest.get("user_id") or "default"),
            "--agent-id", str(manifest.get("agent_id") or "default"),
            "--prompt-mode", "vikingboat_lite",
            "--answer-base-url", "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "--answer-model", "deepseek-v4-flash",
            "--answer-token", token,
            "--top-k", "30",
            "--score-threshold", "0.75",
            "--memory-budget-chars", "6000",
            "--user-memory-budget-chars", "4000",
            "--agent-memory-budget-chars", "2000",
            "--retrieval-mode", "search",
            "--retrieval-ranker", "score",
            "--retrieval-uri-dedup",
            "--search-overview-enrichment",
            "--tool-set", "search_read",
            "--tool-search-limit", "20",
            "--tool-min-score", "0.35",
            "--tool-log-chars", "1200",
            "--prefetch-read-count", "4",
            "--prefetch-context-chars", "5000",
            "--max-iterations", "50",
            "--no-vikingboat-tool-loop",
            "--no-vikingboat-compat",
            "--no-initial-tool-prefetch",
            "--answer-refinement",
            "--fallback-to-one-shot",
        ]
        print(f"[resume] batch {batch_index}/{len(batches)} questions={questions_arg}")
        run_subprocess(cmd, env, ROOT, resume_log)
        shard_csv = shard_out / "echomemory_memory_qa_results.csv"
        merge_csvs(csv_path, shard_csv)
        copy_recall_logs(shard_out, qa_dir)
        time.sleep(args.sleep_s)

    finished_after = load_finished_question_ids(csv_path)
    remaining_after = [qid for qid in all_questions if qid not in finished_after]
    print(f"[resume] after batches finished={len(finished_after)} remaining={len(remaining_after)}")
    if remaining_after:
        print("[resume] skipping judge because the run is still incomplete")
        return

    judge_cmd = [
        str(PY),
        str(ROOT / "scripts/local_judge.py"),
        "--input", str(csv_path),
        "--base-url", "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "--model", "deepseek-v4-flash",
        "--token", token,
        "--parallel", "10",
        "--timeout-s", "120",
        "--retries", "5",
    ]
    run_subprocess(judge_cmd, env, ROOT, resume_log)
    print("[resume] judge completed")


if __name__ == "__main__":
    main()
