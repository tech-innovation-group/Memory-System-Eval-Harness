#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from pathlib import Path
from typing import Any

import benchmark_adapter
from echomemory_common import open_echomem_sdk, write_echomem_config
from echomemory_memory_qa import answer_question, hotpotqa_disable_answer_tooling, normalize_echomemory_tool_set, normalize_retrieval_mode


def load_jobs(dataset_path: Path, question_ids: list[str]) -> list[benchmark_adapter.Job]:
    data = benchmark_adapter.read_dataset(dataset_path)
    jobs, _plans = benchmark_adapter.hotpotqa_jobs(data, None)
    wanted = set(question_ids)
    return [job for job in jobs if job.question_id in wanted]


def flush_rows(out_dir: Path, rows: list[dict[str, Any]]) -> Path:
    csv_path = out_dir / "probe_results.csv"
    if rows:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    (out_dir / "probe_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path


async def main_async(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    os.environ["DASHSCOPE_API_KEY"] = args.answer_token
    os.environ["ECHOMEM_CHAT_API_KEY"] = args.answer_token
    os.environ["ECHOMEM_CHAT_BASE_URL"] = args.answer_base_url
    os.environ["ECHOMEM_CHAT_MODEL"] = args.answer_model
    os.environ["DASHSCOPE_BASE_URL"] = args.answer_base_url

    config_path = write_echomem_config(
        out_dir,
        args.account,
        args.workspace,
        echomem_root=args.echomem_root,
        fallback_to_mock=False,
        user_id=args.user_id,
    )
    sdk, runtime, _layout = await open_echomem_sdk(
        echomem_root=args.echomem_root,
        workspace=args.workspace,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        config_path=config_path,
    )
    try:
        rows: list[dict[str, str]] = []
        for idx, job in enumerate(load_jobs(Path(args.dataset), args.questions), 1):
            try:
                row = await answer_question(args, sdk, job, out_dir=out_dir, question_no=idx)
            except Exception as exc:
                row = {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "result": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "reasoning": f"probe_error: {exc}",
                    "time_cost": "0",
                    "tool_call_count": "0",
                    "model_tool_call_count": "0",
                    "retrieval_count": "0",
                    "retrieval_query_plan": "[]",
                    "model_error_kind": "probe_exception",
                    "model_error": str(exc),
                    "answer_status": "probe_exception",
                    "health_status": "probe_exception",
                }
            rows.append(row)
            flush_rows(out_dir, rows)
        csv_path = flush_rows(out_dir, rows)
        print(str(csv_path), flush=True)
    finally:
        if runtime is not None:
            await runtime.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--echomem-root", required=True)
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--prompt-mode", default="vikingboat_lite")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--score-threshold", type=float, default=0.1)
    parser.add_argument("--memory-budget-chars", type=int, default=6000)
    parser.add_argument("--user-memory-budget-chars", type=int, default=4000)
    parser.add_argument("--agent-memory-budget-chars", type=int, default=2000)
    parser.add_argument("--retrieval-mode", default="search")
    parser.add_argument("--retrieval-ranker", default="score")
    parser.add_argument("--answer-base-url", required=True)
    parser.add_argument("--answer-model", required=True)
    parser.add_argument("--answer-token", required=True)
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--tool-set", default="vikingboat_default")
    parser.add_argument("--tool-search-limit", type=int, default=20)
    parser.add_argument("--tool-min-score", type=float, default=0.35)
    parser.add_argument("--tool-log-chars", type=int, default=1200)
    parser.add_argument("--prefetch-read-count", type=int, default=4)
    parser.add_argument("--prefetch-context-chars", type=int, default=5000)
    parser.add_argument("--max-iterations", type=int, default=50)
    parser.add_argument("--fallback-to-one-shot", action="store_true", default=False)
    parser.add_argument("--vikingboat-compat", action="store_true", default=False)
    parser.add_argument("--vikingboat-tool-loop", action="store_true", default=False)
    parser.add_argument("--initial-tool-prefetch", action="store_true", default=False)
    parser.add_argument("--search-overview-enrichment", action="store_true", default=True)
    parser.add_argument("--exclude-session-summaries", action="store_true", default=False)
    parser.add_argument("--qa-memory-injection", action="store_true", default=True)
    parser.add_argument("--compat-allow-local-evidence", action="store_true", default=False)
    parser.add_argument("--compat-allow-initial-prefetch", action="store_true", default=False)
    parser.add_argument("--answer-refinement", action="store_true", default=False)
    parser.add_argument("--toolloop-rescue-on-toollike-answer", action="store_true", default=False)
    parser.add_argument("--local-session-summaries", action="store_true", default=False)
    parser.add_argument("--local-atoms", action="store_true", default=False)
    parser.add_argument("--local-messages", action="store_true", default=False)
    parser.add_argument("--local-timeline-hints", action="store_true", default=False)
    parser.add_argument("--local-memory-artifacts", action="store_true", default=False)
    parser.add_argument("--local-segments", action="store_true", default=False)
    parser.add_argument("--segment-readback", action="store_true", default=False)
    parser.add_argument("--retrieval-uri-dedup", action="store_true", default=True)
    args = parser.parse_args()
    args.dataset_format = "hotpotqa"
    args.questions = [part.strip() for part in str(args.questions).split(",") if part.strip()]
    args.retrieval_mode = normalize_retrieval_mode(args.retrieval_mode)
    args.tool_set = normalize_echomemory_tool_set(args.tool_set, vikingboat_compat=bool(args.vikingboat_compat))
    hotpotqa_disable_answer_tooling(args)
    return args


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
