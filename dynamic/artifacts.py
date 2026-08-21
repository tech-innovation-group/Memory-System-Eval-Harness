"""Dynamic evaluation result, dataset, and quality-report artifacts."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from typing import Any

from tqdm import tqdm

from dynamic.metrics import compute_summary, evaluate_quality
from shared.eval_base import EvalRun
from shared.llm_client import LLMClient


def safe_json_list(value: str) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def build_v2_dataset(
    theme: str,
    background_memories: list[dict],
    dataset_queries: list[dict],
    rounds: list[dict],
    inject_session_id: str = "",
    inject_user_id: str = "",
) -> dict[str, Any]:
    sessions: dict[str, list[dict]] = {}
    for row in rounds:
        session_id = str(row.get("session_id") or "")
        if session_id:
            sessions.setdefault(session_id, []).append(row)
    conversation: dict[str, Any] = {}
    for session_id, session_rounds in sessions.items():
        turns: list[dict[str, Any]] = []
        for row in session_rounds:
            turns.extend([
                {
                    "round_id": row.get("round_id", ""),
                    "speaker": "user",
                    "text": row.get("query", ""),
                    "ground_facts": row.get("ground_facts", []),
                },
                {
                    "round_id": row.get("round_id", ""),
                    "speaker": "assistant",
                    "text": row.get("reply", ""),
                    "recalled_memories": safe_json_list(
                        row.get("relevant_memory", "")
                    ),
                    "quality_score": row.get("quality_score"),
                },
            ])
        conversation[session_id] = {
            "session_id": session_id,
            "is_new": bool(
                session_rounds
                and session_rounds[0].get("is_new_session")
            ),
            "turns": turns,
        }
    quality_scores = [
        row["quality_score"]
        for row in rounds
        if row.get("quality_score") is not None
    ]
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "theme": theme,
        "inject_session_id": inject_session_id or None,
        "inject_user_id": inject_user_id or None,
        "background_memories": [
            {
                "id": memory.get("id", ""),
                "text": memory.get("text", ""),
                "source_round": memory.get("source_round", -1),
            }
            for memory in background_memories
        ],
        "dataset_queries": dataset_queries,
        "samples": [{
            "sample_id": (
                "dynamic_eval_" + datetime.now().strftime("%Y%m%d%H%M%S")
            ),
            "conversation": conversation,
            "metadata": {
                "total_rounds": len(rounds),
                "new_session_count": sum(
                    bool(row.get("is_new_session")) for row in rounds
                ),
                "avg_quality_score": (
                    round(sum(quality_scores) / len(quality_scores), 1)
                    if quality_scores
                    else 0
                ),
            },
        }],
    }


def build_v2_quality_report(
    rounds: list[dict],
    summary: dict[str, Any],
    theme: str = "",
) -> dict[str, Any]:
    query_rounds = [
        row
        for row in rounds
        if not row.get("is_injection") and row.get("reply")
    ]
    results: list[dict[str, Any]] = []
    for row in query_rounds:
        recalled = safe_json_list(row.get("relevant_memory", ""))
        results.append({
            "round_id": row.get("round_id", ""),
            "query": row.get("query", ""),
            "reply": row.get("reply", ""),
            "session_id": row.get("session_id", ""),
            "is_new_session": row.get("is_new_session", False),
            "quality_score": row.get("quality_score"),
            "dimension_scores": row.get("dimension_scores"),
            "dimension_info": row.get("dimension_info"),
            "quality_reason": row.get("quality_reason", ""),
            "quality_error": row.get("quality_error", ""),
            "strengths": row.get("strengths"),
            "weaknesses": row.get("weaknesses"),
            "hallucination_detected": row.get("hallucination_detected"),
            "task_completed": row.get("task_completed"),
            "ttft_ms": row.get("ttft_ms"),
            "cached_tokens": row.get("cached_tokens", 0),
            "prompt_tokens": row.get("prompt_tokens", 0),
            "recalled_memories_count": len(recalled),
            "ground_facts_count": len(row.get("ground_facts", [])),
            "relevant_memory": recalled,
        })
    quality_scores = [
        row["quality_score"]
        for row in query_rounds
        if row.get("quality_score") is not None
    ]
    dimensions: dict[str, list[float]] = {}
    for row in query_rounds:
        for name, score in (row.get("dimension_scores") or {}).items():
            dimensions.setdefault(name, []).append(score)
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "theme": theme,
        "total_queries": len(query_rounds),
        "avg_ttft_ms": summary.get("avg_ttft_ms"),
        "avg_cached_tokens": summary.get("avg_cached_tokens"),
        "new_session_count": sum(
            bool(row.get("is_new_session")) for row in query_rounds
        ),
        "summary": {
            "avg_quality_score": (
                round(sum(quality_scores) / len(quality_scores), 1)
                if quality_scores
                else None
            ),
            "avg_dimension_scores": (
                {
                    name: round(sum(scores) / len(scores), 1)
                    for name, scores in dimensions.items()
                }
                or None
            ),
            "total_recalled_memories": sum(
                len(safe_json_list(row.get("relevant_memory", "")))
                for row in query_rounds
            ),
        },
        "results": results,
    }


def save_results(
    run: EvalRun,
    all_rounds: list[dict],
    all_facts: dict[str, str],
    llm: LLMClient,
    config: dict[str, Any],
    evaluator_config: dict[str, Any],
    theme: str = "",
    background_memories: list[dict] | None = None,
    dataset_queries: list[dict] | None = None,
    inject_session_id: str = "",
    inject_user_id: str = "",
) -> None:
    log = run.logger
    summary = compute_summary(all_rounds)
    (run.result_dir / "dynamic_results.json").write_text(
        json.dumps({
            "testId": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "config": config,
            "summary": summary,
            "facts": all_facts,
            "rounds": all_rounds,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    with (run.result_dir / "dynamic_results.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "round_id",
                "session_id",
                "question_id",
                "query",
                "reply",
                "gold_answer",
                "reply_length",
                "query_length",
                "ttft_ms",
                "cached_tokens",
                "prompt_tokens",
                "prefetch_committed",
                "completion_tokens",
                "elapsed_s",
                "retrieval_latency_s",
                "llm_latency_s",
                "tool_call_count",
                "iterations",
                "is_new_session",
                "is_injection",
                "complexity",
                "error",
                "relevant_memory",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(all_rounds)

    query_rounds = [
        row
        for row in all_rounds
        if not row.get("is_injection") and row.get("reply")
    ]
    if query_rounds and all_facts:
        log.info("使用配置驱动评测器评估 %d 条回复...", len(query_rounds))
        for row in tqdm(query_rounds, desc="质量评估", unit="q"):
            ground_texts = [
                all_facts.get(fact_id, fact_id)
                for fact_id in (row.get("ground_facts") or [])
            ]
            result = evaluate_quality(
                llm,
                evaluator_config,
                row.get("query", ""),
                row.get("reply", ""),
                ground_texts,
                row.get("relevant_memory", ""),
            )
            row.update({
                "quality_score": result.get("score"),
                "dimension_scores": result.get("dimension_scores"),
                "dimension_info": result.get("dimension_info"),
                "quality_reason": result.get("quality_reason", ""),
                "quality_error": result.get("error", ""),
                "strengths": result.get("strengths"),
                "weaknesses": result.get("weaknesses"),
                "hallucination_detected": result.get(
                    "hallucination_detected"
                ),
                "task_completed": result.get("task_completed"),
            })
        quality_report = build_v2_quality_report(
            all_rounds,
            summary,
            theme,
        )
        (run.result_dir / "quality_report.json").write_text(
            json.dumps(
                quality_report,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        log.info(
            "质量评估: avg_quality_score=%s",
            quality_report["summary"]["avg_quality_score"],
        )

    dataset = build_v2_dataset(
        theme,
        background_memories or [],
        dataset_queries or [],
        all_rounds,
        inject_session_id,
        inject_user_id,
    )
    dataset_path = run.result_dir / "dataset.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("数据集已保存: %s", dataset_path)

    quality_scores = [
        row["quality_score"]
        for row in query_rounds
        if row.get("quality_score") is not None
    ]
    evaluator_errors = sum(
        bool(row.get("quality_error")) for row in query_rounds
    )
    full_summary = {
        "status": (
            "failed"
            if summary.get("errors") or evaluator_errors or not query_rounds
            else "completed"
        ),
        "benchmark": "dynamic",
        "mode": config.get("mode", ""),
        **summary,
        "quality_overall_score": (
            round(sum(quality_scores) / len(quality_scores), 1)
            if quality_scores
            else None
        ),
        "quality_evaluator_errors": evaluator_errors,
    }
    run.save_summary(full_summary)
    if full_summary["status"] != "completed":
        log.error("动态评测包含运行错误或没有可评分 query，结果不能作为正式分数")
        raise SystemExit(2)
    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info(
        "avg_ttft=%sms avg_cached=%s queries=%d errors=%d",
        summary.get("avg_ttft_ms"),
        summary.get("avg_cached_tokens"),
        summary.get("total_queries"),
        summary.get("errors"),
    )
