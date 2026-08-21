"""Dynamic evaluation metrics and configurable quality judging."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from plugins.base import AgentResponse
from shared.llm_client import LLMClient, chat_with_repair


# 评测 LLM 偶尔返回空/格式错误的输出。重试时追加修正提示并抬高温度，
# 避免温度 0 的确定性请求复现同样的坏输出。提示会列出完整的期望 schema。
def build_dynamic_repair_prompt(dimensions: list[dict[str, Any]]) -> str:
    dimension_names = ", ".join(
        f'"{dimension["name"]}" (0-{dimension.get("max_score", 0)})'
        for dimension in dimensions
    )
    return (
        "\n\nYour previous response was empty, not valid JSON, or did not match the "
        "required schema. Output ONLY a single JSON object with exactly these fields: "
        f'"score" (0-100), "dimension_scores" (an object with {dimension_names}), '
        '"reason" (string), "strengths" (array of strings), '
        '"weaknesses" (array of strings), "hallucination_detected" (bool), '
        '"task_completed" (bool), "matched_facts" (int), "total_facts" (int), '
        '"recall_helped" (bool). Do not wrap it in markdown code fences and do not '
        "add any other text."
    )


def _parse_evaluation_json(content: str) -> dict[str, Any]:
    raw = _loads_evaluator_json(content)
    if not isinstance(raw, dict):
        raise ValueError("evaluation JSON is not an object")
    return raw


def _loads_evaluator_json(content: str) -> Any:
    """宽松解析评测输出中的单个 JSON 对象。

    评测 LLM 的输出可能带 markdown 围栏、JSON 后跟尾注、尾随逗号等，
    逐级容错后仍失败才抛 ValueError，触发 repair 重试。
    """
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("JSON object missing")
    candidate = text[start:]
    try:
        # 只解析第一个 JSON 值，不吞掉其后的尾注（贪婪正则的常见陷阱）。
        value, _ = json.JSONDecoder().raw_decode(candidate)
        return value
    except json.JSONDecodeError:
        pass
    # 轻量修复：去除尾随逗号后重试。
    repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # 退化为 Python 字面量解析（true/false/null 与 Python 关键字近似）。
    literal = re.sub(r"\btrue\b", "True", repaired)
    literal = re.sub(r"\bfalse\b", "False", literal)
    literal = re.sub(r"\bnull\b", "None", literal)
    try:
        return ast.literal_eval(literal)
    except (ValueError, SyntaxError):
        raise ValueError("JSON object missing") from None


def load_evaluator_config(path: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"评测器配置文件不存在: {source}")
    import yaml

    with source.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return payload if isinstance(payload, dict) else {}


def collect_round_metrics(
    round_data: dict[str, Any],
    response: AgentResponse,
    memory_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reply = response.text or ""
    extra = response.extra or {}
    items = memory_items or response.memory_items or []
    return {
        "round_id": round_data.get("id", ""),
        "query": round_data.get("query", ""),
        "reply": reply,
        "reply_length": len(reply),
        "query_length": len(str(round_data.get("query") or "")),
        "ttft_ms": round(response.ttft_ms, 1) if response.ttft_ms is not None else None,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "cached_tokens": response.cached_tokens,
        "prefetch_committed": response.prefetch_committed,
        "elapsed_s": extra.get("elapsed_s"),
        "retrieval_latency_s": extra.get("retrieval_latency_s"),
        "llm_latency_s": extra.get("llm_latency_s"),
        "tool_call_count": extra.get("tool_call_count", 0),
        "iterations": extra.get("iterations", 1),
        "is_new_session": bool(round_data.get("new_session")),
        "is_injection": bool(round_data.get("is_injection")),
        "complexity": round_data.get("complexity", ""),
        "ground_facts": round_data.get("ground_facts", []),
        "error": response.error or "",
        "relevant_memory": json.dumps(items, ensure_ascii=False),
    }


def compute_summary(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    queries = [row for row in rounds if not row.get("is_injection")]
    ttft = [row["ttft_ms"] for row in queries if row.get("ttft_ms") is not None]
    cached = [row["cached_tokens"] for row in queries if row.get("cached_tokens")]
    prompt = [row["prompt_tokens"] for row in queries if row.get("prompt_tokens")]
    lengths = [row["reply_length"] for row in queries]
    elapsed = [row["elapsed_s"] for row in queries if row.get("elapsed_s") is not None]
    retrieval_lat = [row["retrieval_latency_s"] for row in queries if row.get("retrieval_latency_s")]
    llm_lat = [row["llm_latency_s"] for row in queries if row.get("llm_latency_s")]
    completion = [row["completion_tokens"] for row in queries if row.get("completion_tokens")]
    tool_calls = [row["tool_call_count"] for row in queries if row.get("tool_call_count")]
    iterations = [row["iterations"] for row in queries if row.get("iterations")]
    ordered_ttft = sorted(ttft)
    return {
        "total_queries": len(queries),
        "total_rounds": len(rounds),
        "errors": sum(bool(row.get("error")) for row in queries),
        "prefetch_committed_count": sum(
            bool(row.get("prefetch_committed")) for row in queries
        ),
        "avg_ttft_ms": round(sum(ttft) / len(ttft), 1) if ttft else None,
        "median_ttft_ms": (
            round(ordered_ttft[len(ordered_ttft) // 2], 1)
            if ordered_ttft
            else None
        ),
        "p95_ttft_ms": (
            round(ordered_ttft[int(len(ordered_ttft) * 0.95)], 1)
            if len(ordered_ttft) >= 2
            else None
        ),
        "avg_cached_tokens": (
            round(sum(cached) / len(cached), 1) if cached else None
        ),
        "avg_prompt_tokens": (
            round(sum(prompt) / len(prompt), 1) if prompt else None
        ),
        "avg_reply_length": (
            round(sum(lengths) / len(lengths), 1) if lengths else 0
        ),
        "avg_elapsed_s": round(sum(elapsed) / len(elapsed), 3) if elapsed else None,
        "avg_retrieval_latency_s": (
            round(sum(retrieval_lat) / len(retrieval_lat), 3)
            if retrieval_lat
            else None
        ),
        "avg_llm_latency_s": round(sum(llm_lat) / len(llm_lat), 3) if llm_lat else None,
        "avg_completion_tokens": (
            round(sum(completion) / len(completion), 1)
            if completion
            else None
        ),
        "avg_tool_call_count": (
            round(sum(tool_calls) / len(tool_calls), 1) if tool_calls else 0
        ),
        "avg_iterations": (
            round(sum(iterations) / len(iterations), 1) if iterations else 1
        ),
    }


def evaluate_quality(
    llm: LLMClient,
    evaluator_config: dict[str, Any],
    query: str,
    reply: str,
    ground_facts: list[str],
    recalled_memories: str = "",
) -> dict[str, Any]:
    dimensions = evaluator_config.get("dimensions") or []
    template = str(evaluator_config.get("evaluate_prompt") or "")
    if not template:
        return {"error": "evaluate_prompt missing in config", "score": None}
    criteria = "\n".join(
        f"{index}. {dimension.get('display_name', dimension.get('name', ''))} "
        f"(0-{dimension.get('max_score', 0)}分): "
        f"{dimension.get('description', '')}"
        for index, dimension in enumerate(dimensions, 1)
    )
    prompt = template.format(
        query=query,
        reply=reply,
        ground_facts=(
            "\n".join(f"- {fact}" for fact in ground_facts)
            if ground_facts
            else "N/A"
        ),
        recalled_memories=recalled_memories or "N/A",
        dimension_criteria=criteria,
    )
    dimension_info = {
        dimension["name"]: {
            "display_name": dimension.get(
                "display_name",
                dimension.get("name", ""),
            ),
            "max_score": dimension.get("max_score", 0),
        }
        for dimension in dimensions
    }
    try:
        raw = chat_with_repair(
            llm,
            "You are a response quality evaluator. Output only valid JSON.",
            prompt,
            repair_prompt=build_dynamic_repair_prompt(dimensions),
            parse=_parse_evaluation_json,
        )
    except Exception as exc:
        return {"error": str(exc), "score": None, "dimension_info": dimension_info}

    dimension_scores: dict[str, float] = {}
    raw_scores = raw.get("dimension_scores") or {}
    for dimension in dimensions:
        name = dimension["name"]
        maximum = float(dimension["max_score"])
        try:
            score = float(raw_scores.get(name, raw.get(name, 0)))
        except (TypeError, ValueError):
            score = 0
        dimension_scores[name] = min(max(0, score), maximum)
    try:
        total = float(raw.get("score", 0))
    except (TypeError, ValueError):
        total = 0
    return {
        "score": min(max(0, total), 100),
        "dimension_scores": dimension_scores,
        "dimension_info": dimension_info,
        "quality_reason": raw.get("reason", ""),
        "strengths": raw.get("strengths") or [],
        "weaknesses": raw.get("weaknesses") or [],
        "hallucination_detected": raw.get("hallucination_detected"),
        "task_completed": raw.get("task_completed"),
        "matched_facts": raw.get("matched_facts"),
        "total_facts": raw.get("total_facts"),
        "recall_helped": raw.get("recall_helped"),
    }
