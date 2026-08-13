#!/usr/bin/env python3
"""Extract evaluation result metrics from a result directory and output as flat JSON.

Reads summary.json, config.json, and tip.txt from the given result directory,
extracts ~52 fields according to the Feishu table schema, rounds all float values
to 4 decimal places, and prints a flat JSON object to stdout. Does NOT make any
network requests and does NOT touch credentials.

Usage:
    python extract_eval_result.py <result_dir>
"""

import io
import json
import os
import sys
from datetime import datetime


# Ensure UTF-8 output on Windows (default is GBK/CP936 when redirected)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _safe_get(data: dict, *keys, default=None):
    """Safely navigate nested dicts."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _iso_to_ms(iso_str: str):
    """Convert ISO 8601 string to Unix milliseconds (Feishu date field format)."""
    if not iso_str:
        return None
    try:
        # Handle trailing 'Z'
        cleaned = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _failure_count(failure_breakdown, mode_name):
    """Extract count for a given failure mode from failure_breakdown array."""
    if not isinstance(failure_breakdown, list):
        return 0
    for item in failure_breakdown:
        if isinstance(item, dict) and item.get("mode") == mode_name:
            return item.get("count", 0)
    return 0


def extract(result_dir: str) -> dict:
    """Extract all fields from the result directory."""

    # --- Read source files ---
    summary_path = os.path.join(result_dir, "summary.json")
    config_path = os.path.join(result_dir, "config.json")
    tip_path = os.path.join(result_dir, "tip.txt")

    if not os.path.isfile(summary_path):
        print(f"ERROR: summary.json not found in {result_dir}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(config_path):
        print(f"ERROR: config.json not found in {result_dir}", file=sys.stderr)
        sys.exit(1)

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    tip = ""
    if os.path.isfile(tip_path):
        with open(tip_path, "r", encoding="utf-8") as f:
            tip = f.read().strip()

    # --- Navigate nested structures ---
    cfg = config.get("config", {})
    agent_opts = summary.get("agent_options", {})
    metrics = _safe_get(summary, "strict_blackbox", "metrics", default={})
    categories = metrics.get("categories", {})
    diagnosis = summary.get("diagnosis", {})
    failure_breakdown = diagnosis.get("failure_breakdown", [])

    # --- Assemble flat fields dict ---
    fields = {}

    # A. 标识与元数据 (上传人 and 备注 are NOT included here; collected by skill)
    run_id = os.path.basename(os.path.normpath(result_dir))
    fields["运行ID"] = run_id
    fields["运行时间"] = _iso_to_ms(summary.get("run_started_at"))
    fields["标注"] = tip

    # B. 评测配置
    benchmark = summary.get("benchmark")
    sample_filter = summary.get("sample_filter")
    # Benchmark 列值为 benchmark 与样本过滤器的组合，如 locomo + conv-30 → locomo-conv-30；
    # 无样本过滤器时仅写 benchmark 名。
    fields["Benchmark"] = (
        f"{benchmark}-{sample_filter}" if benchmark and sample_filter else (benchmark or sample_filter)
    )
    fields["样本过滤器"] = sample_filter
    fields["记忆后端"] = cfg.get("memory_backend")
    fields["Agent插件"] = cfg.get("agent_plugin")
    fields["QA Profile"] = summary.get("qa_profile")
    fields["LLM模型"] = cfg.get("llm_model")
    # 温度: prefer agent_options, fall back to config
    fields["温度"] = agent_opts.get("llm_temperature", cfg.get("llm_temperature"))
    fields["MaxTokens"] = cfg.get("llm_max_tokens")
    fields["TopK"] = summary.get("top_k", cfg.get("top_k"))
    fields["记忆预算字符"] = summary.get("memory_budget_chars", cfg.get("memory_budget_chars"))
    fields["并发数"] = summary.get("qa_parallelism", cfg.get("concurrency"))
    tools_enabled = summary.get("tools_enabled")
    fields["工具调用启用"] = bool(tools_enabled) if tools_enabled is not None else None

    # C. 核心指标
    fields["运行状态"] = summary.get("status")
    fields["总问题数"] = summary.get("total_questions")
    fields["正确数"] = summary.get("judge_correct")
    fields["错误数"] = summary.get("judge_wrong")
    fields["准确率"] = summary.get("accuracy")
    fields["Cat1准确率"] = _safe_get(categories, "1", "accuracy")
    fields["Cat2准确率"] = _safe_get(categories, "2", "accuracy")
    fields["Cat4准确率"] = _safe_get(categories, "4", "accuracy")
    fields["检索覆盖率"] = diagnosis.get("retrieval_coverage")

    # D. 延迟指标
    fields["平均耗时s"] = summary.get("avg_qa_elapsed_s")
    fields["E2E平均s"] = _safe_get(metrics, "end_to_end_s", "avg")
    fields["E2E_P50_s"] = _safe_get(metrics, "end_to_end_s", "p50")
    fields["E2E_P95_s"] = _safe_get(metrics, "end_to_end_s", "p95")
    fields["E2E_P99_s"] = _safe_get(metrics, "end_to_end_s", "p99")
    fields["检索延迟平均s"] = _safe_get(metrics, "retrieval_latency_s", "avg")
    fields["检索延迟P95_s"] = _safe_get(metrics, "retrieval_latency_s", "p95")
    fields["批次耗时s"] = metrics.get("batch_wall_clock_s")
    fields["QA吞吐QPS"] = metrics.get("qa_throughput_qps")

    # E. Token 消耗
    fields["总PromptTokens"] = summary.get("total_prompt_tokens")
    fields["总CompletionTokens"] = summary.get("total_completion_tokens")
    fields["可见模型总Tokens"] = metrics.get("visible_model_total_tokens")
    fields["每正确答案Tokens"] = metrics.get("tokens_per_correct")
    fields["AnswerTokens平均"] = _safe_get(metrics, "answer_total_tokens", "avg")
    fields["AnswerTokens_P95"] = _safe_get(metrics, "answer_total_tokens", "p95")
    fields["JudgeTokens平均"] = _safe_get(metrics, "judge_total_tokens", "avg")

    # F. 健康度
    fields["请求成功率"] = metrics.get("request_success_rate")
    fields["失败率"] = metrics.get("failure_rate")
    fields["空检索率"] = metrics.get("empty_retrieval_rate")

    # G. 工具与迭代
    fields["工具调用总数"] = summary.get("tool_call_total")
    fields["平均迭代轮数"] = summary.get("avg_iterations")

    # H. 失败诊断
    fields["失败_证据未用"] = _failure_count(failure_breakdown, "evidence_unused")
    fields["失败_时序推理"] = _failure_count(failure_breakdown, "temporal_reasoning")
    fields["失败_证据不匹配"] = _failure_count(failure_breakdown, "evidence_mismatch")

    # I. 记忆配置
    fields["记忆来源"] = summary.get("memory_source")
    fields["记忆复用来源"] = _safe_get(summary, "memory_reuse", "source")

    # --- Round all float values to 4 decimal places ---
    for key, val in fields.items():
        if isinstance(val, float):
            fields[key] = round(val, 4)

    return fields


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_eval_result.py <result_dir>", file=sys.stderr)
        sys.exit(1)

    result_dir = sys.argv[1]
    if not os.path.isdir(result_dir):
        print(f"ERROR: {result_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    fields = extract(result_dir)
    json.dump(fields, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
