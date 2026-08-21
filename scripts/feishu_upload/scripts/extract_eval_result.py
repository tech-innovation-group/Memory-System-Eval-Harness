#!/usr/bin/env python3
"""Extract evaluation result metrics from a result directory and output as flat JSON.

Reads summary.json, config.json, tip.txt, and (for hotpotqa) qa_results.csv
from the given result directory, extracts fields according to the Feishu table
schema, rounds all float values to 4 decimal places, and prints a flat JSON
object to stdout. Does NOT make any network requests and does NOT touch
credentials.

Usage:
    python extract_eval_result.py <result_dir>
"""

import csv
import io
import json
import math
import os
import statistics
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


def _to_float(value):
    """Parse a CSV cell as float; None for empty/invalid values."""
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _percentile(values, quantile):
    """Linear-interpolation percentile, matching locomo blackbox.percentile."""
    cleaned = sorted(values)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    position = (len(cleaned) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return cleaned[lower]
    return cleaned[lower] + (cleaned[upper] - cleaned[lower]) * (position - lower)


def _metric_stats(values, scale=1.0):
    """avg/p50/p95/p99 over a column, optionally scaled (e.g. ms -> s)."""
    vals = [value / scale for value in values if math.isfinite(value)]
    if not vals:
        return {}
    return {
        "avg": statistics.fmean(vals),
        "p50": _percentile(vals, 0.50),
        "p95": _percentile(vals, 0.95),
        "p99": _percentile(vals, 0.99),
    }


def _elapsed_seconds(start_iso, end_iso):
    """Wall-clock seconds between two ISO 8601 timestamps."""
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return (end - start).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return None


def _load_qa_results(result_dir):
    """Load per-question rows from qa_results.csv (empty list if absent)."""
    path = os.path.join(result_dir, "qa_results.csv")
    if not os.path.isfile(path):
        return []
    # retrieval_items_json 等列可能远超默认字段上限（128KB）
    csv.field_size_limit(sys.maxsize)
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _hotpotqa_metrics(rows, summary):
    """Aggregate latency/token/health metrics from qa_results.csv.

    hotpotqa 的 summary.json 不写 strict_blackbox.metrics（locomo 才有）；
    这里按 locomo blackbox 的同款公式从逐题 csv 补齐，供既有字段提取复用。
    """
    def column(field):
        return [v for v in (_to_float(row.get(field)) for row in rows) if v is not None]

    e2e = _metric_stats(column("end_to_end_ms"), scale=1000.0)
    retrieval = _metric_stats(column("retrieval_latency_ms"), scale=1000.0)
    answer_tokens = _metric_stats(column("answer_total_tokens"))

    status_fields = ("retrieval_status", "answer_status", "model_status", "health_status")
    status_rows = [r for r in rows if all(r.get(k) not in (None, "") for k in status_fields)]
    successful = [
        r for r in status_rows
        if all(str(r.get(k)).lower() == "ok" for k in status_fields)
    ]
    retrieval_rows = [r for r in rows if r.get("retrieval_count") not in (None, "")]
    empty_retrieval = [
        r for r in retrieval_rows if _to_float(r.get("retrieval_count")) == 0
    ]

    answer_token_total = sum(column("answer_total_tokens"))
    # qa_results.csv 无 answer_em 列（在 eval_results.csv），用 summary 的 answer_em × 题目数估算正确数。
    correct = round((summary.get("answer_em") or 0) * len(rows))
    resume = summary.get("resume") or {}
    # 续跑延续原始启动时间：优先 resume.original_started_at（runner 写入），
    # 其次源目录的 summary；都拿不到则置空（无法确认真整批耗时）。
    start_iso = summary.get("run_started_at")
    if bool(resume.get("enabled")) and (resume.get("reused_qa") or 0) > 0:
        start_iso = resume.get("original_started_at")
        if not start_iso:
            source_summary_path = os.path.join(resume.get("source") or "", "summary.json")
            if os.path.isfile(source_summary_path):
                try:
                    with open(source_summary_path, encoding="utf-8") as f:
                        start_iso = json.load(f).get("run_started_at")
                except (OSError, ValueError):
                    pass
    wall_clock_s = _elapsed_seconds(start_iso, summary.get("run_finished_at"))

    return {
        "categories": {},
        "end_to_end_s": e2e,
        "retrieval_latency_s": retrieval,
        "batch_wall_clock_s": wall_clock_s,
        "qa_throughput_qps": round(len(rows) / wall_clock_s, 6) if wall_clock_s else None,
        "visible_model_total_tokens": answer_token_total if rows else None,
        "tokens_per_correct": answer_token_total / correct if rows and correct else None,
        "answer_total_tokens": answer_tokens,
        "judge_total_tokens": {},
        "request_success_rate": (
            len(successful) / len(status_rows) if status_rows else None
        ),
        "failure_rate": (
            (len(status_rows) - len(successful)) / len(status_rows)
            if status_rows
            else None
        ),
        "empty_retrieval_rate": (
            len(empty_retrieval) / len(retrieval_rows) if retrieval_rows else None
        ),
    }


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

    qa_rows = _load_qa_results(result_dir)

    # --- Navigate nested structures ---
    cfg = config.get("config", {})
    agent_opts = summary.get("agent_options", {})
    diagnosis = summary.get("diagnosis", {})
    failure_breakdown = diagnosis.get("failure_breakdown", [])

    # --- Assemble flat fields dict ---
    fields = {}

    # A. 标识与元数据（上传人由 skill 收集；备注取自 tip.txt，用户可在上传时覆盖）
    run_id = os.path.basename(os.path.normpath(result_dir))
    fields["运行ID"] = run_id
    fields["运行时间"] = _iso_to_ms(summary.get("run_started_at"))
    fields["备注"] = tip

    # B. 评测配置
    benchmark = summary.get("benchmark")
    metrics = _safe_get(summary, "strict_blackbox", "metrics", default={})
    if not metrics and benchmark == "hotpotqa" and qa_rows:
        # hotpotqa 的 summary 不聚合延迟/token/健康度，从 qa_results.csv 按 locomo 同款公式补齐。
        metrics = _hotpotqa_metrics(qa_rows, summary)
    categories = metrics.get("categories", {})
    # 样本过滤器：locomo 写在 summary 顶层（如 conv-30）；hotpotqa 按实际运行题目数表示。
    sample_filter = summary.get("sample_filter") or cfg.get("sample_filter")
    if benchmark == "hotpotqa":
        # hotpotqa 的样本过滤器 = 实际运行题目数：0 题 → 仅 benchmark 名；x 题 → hotpotqa-x。
        qcount = summary.get("total_questions") or summary.get("qa_count")
        sample_filter = str(qcount) if qcount else None
        fields["Benchmark"] = f"hotpotqa-{sample_filter}" if sample_filter else "hotpotqa"
    else:
        # Benchmark 列值为 benchmark 与样本过滤器的组合，如 locomo + conv-30 → locomo-conv-30；
        # 无样本过滤器时仅写 benchmark 名。
        fields["Benchmark"] = (
            f"{benchmark}-{sample_filter}" if benchmark and sample_filter else (benchmark or sample_filter)
        )
    fields["样本过滤器"] = sample_filter
    fields["记忆后端"] = cfg.get("memory_backend")
    fields["Agent插件"] = cfg.get("agent_plugin")
    qa_profile = summary.get("qa_profile")
    if qa_profile is None and qa_rows:
        # hotpotqa 的 qa_profile 逐题记录在 qa_results.csv（运行内恒定，取第一行）。
        qa_profile = qa_rows[0].get("qa_profile") or None
    fields["QA Profile"] = qa_profile
    fields["LLM模型"] = cfg.get("llm_model")
    # 温度: prefer agent_options, fall back to config
    fields["温度"] = agent_opts.get("llm_temperature", cfg.get("llm_temperature"))
    fields["MaxTokens"] = cfg.get("llm_max_tokens")
    fields["TopK"] = summary.get("top_k", cfg.get("top_k"))
    fields["记忆预算字符"] = summary.get("memory_budget_chars", cfg.get("memory_budget_chars"))
    fields["并发数"] = summary.get("qa_parallelism", cfg.get("concurrency"))
    tools_enabled = summary.get("tools_enabled")
    if tools_enabled is None and benchmark == "hotpotqa":
        # hotpotqa 的 summary 不写 tools_enabled 布尔，按 tool_call_total > 0 推断。
        tools_enabled = (summary.get("tool_call_total") or 0) > 0
    fields["工具调用启用"] = bool(tools_enabled) if tools_enabled is not None else None

    # C. 核心指标
    fields["运行状态"] = summary.get("status")
    fields["总问题数"] = summary.get("total_questions")
    if benchmark != "hotpotqa":
        # judge/准确率 系列为 locomo 专属；hotpotqa 用下面的 EM/F1 六列，不输出这些字段。
        fields["正确数"] = summary.get("judge_correct")
        fields["错误数"] = summary.get("judge_wrong")
        fields["准确率"] = summary.get("accuracy")
        fields["Cat1准确率"] = _safe_get(categories, "1", "accuracy")
        fields["Cat2准确率"] = _safe_get(categories, "2", "accuracy")
        fields["Cat4准确率"] = _safe_get(categories, "4", "accuracy")
    fields["检索覆盖率"] = diagnosis.get("retrieval_coverage")
    if benchmark == "hotpotqa":
        # hotpotqa 核心指标为 EM/F1（locomo 是 judge 准确率，语义不同，单独成列）。
        fields["AnswerEM"] = summary.get("answer_em")
        fields["AnswerF1"] = summary.get("answer_f1")
        fields["JointEM"] = summary.get("joint_em")
        fields["JointF1"] = summary.get("joint_f1")
        fields["SupportingFactsEM"] = summary.get("supporting_facts_em")
        fields["SupportingFactsF1"] = summary.get("supporting_facts_f1")

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
    if benchmark != "hotpotqa":
        # hotpotqa 无 judge 阶段，不输出 JudgeTokens平均。
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
