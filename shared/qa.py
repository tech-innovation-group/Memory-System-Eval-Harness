"""Shared QA result contract: QAResult dataclass + CSV field schema.

Agent-specific QA flows (retrieve -> prompt -> LLM, tool-call loops, etc.)
live inside each agent plugin, not here. This module only defines the
cross-cutting result type that all agents produce and all benchmarks consume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("eval.qa")

BASE_QA_FIELDS = (
    "question_id",
    "sample_id",
    "category",
    "question",
    "answer",
    "response",
    "retrieval_error",
    "llm_error",
    "elapsed_s",
    "end_to_end_ms",
    "retrieval_latency_ms",
    "injection_total_ms",
    "llm_total_ms",
    "prompt_tokens",
    "completion_tokens",
    "answer_prompt_tokens",
    "answer_completion_tokens",
    "answer_total_tokens",
    "model_retry_count",
    "num_retrieved",
    "retrieval_count",
    "retrieval_status",
    "answer_status",
    "model_status",
    "health_status",
    "tool_call_count",
    "iterations",
    "qa_profile",
    "evidence_policy",
    "evidence_origin",
    "retrieval_source_mode",
    "platform_evidence_injection_enabled",
    "qa_memory_writeback_enabled",
)


@dataclass
class QAResult:
    """Result of a single QA question."""

    question_id: str
    question: str
    answer: str
    response: str
    retrieval_items: list[dict[str, Any]] = field(default_factory=list)
    retrieval_error: str = ""
    llm_error: str = ""
    elapsed_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_call_count: int = 0
    iterations: int = 1
    qa_profile: str = "vikingboat0411"
    sample_id: str = ""
    category: str = ""
    retrieval_latency_s: float = 0.0
    orchestration_latency_s: float = 0.0
    llm_latency_s: float = 0.0
    model_retry_count: int | None = None
    model_usage_observed: bool = False
    retrieval_status: str = ""
    answer_status: str = ""
    model_status: str = ""
    health_status: str = ""
    trace: dict[str, Any] = field(default_factory=dict)

    def resolved_statuses(self) -> tuple[str, str, str, str]:
        retrieval = self.retrieval_status or (
            "error"
            if self.retrieval_error
            else "ok" if self.retrieval_items else "empty"
        )
        response = self.response.strip()
        answer = self.answer_status or (
            "failed"
            if self.llm_error
            else "empty_or_unknown"
            if not response or response.lower() == "unknown"
            else "ok"
        )
        model = self.model_status or (
            "failed" if self.llm_error else "ok"
        )
        if self.health_status:
            health = self.health_status
        elif self.retrieval_error:
            health = "retrieval_error"
        elif retrieval != "ok":
            health = "retrieval_empty"
        elif self.llm_error:
            lowered = self.llm_error.lower()
            health = (
                "question_timeout"
                if "deadline" in lowered or "timed out" in lowered
                else "api_error"
            )
        elif answer != "ok":
            health = "answer_empty"
        else:
            health = "ok"
        return retrieval, answer, model, health

    def to_csv_row(self) -> dict[str, str]:
        retrieval, answer, model, health = self.resolved_statuses()
        answer_total = self.prompt_tokens + self.completion_tokens
        usage_observed = self.model_usage_observed or answer_total > 0
        trace = self.trace or {}
        initial_search_via_mcp = bool(trace.get("initial_search_via_mcp"))
        if initial_search_via_mcp:
            evidence_origin = "echomem_mcp"
            retrieval_source_mode = "mcp_initial_search"
            platform_evidence_injection_enabled = "true"
        elif self.qa_profile == "echomem_mcp":
            evidence_origin = "echomem_mcp"
            retrieval_source_mode = "mcp_tools" if self.tool_call_count else "mcp_only_no_retrieval"
            platform_evidence_injection_enabled = "false"
        elif self.qa_profile in ("vikingbot_docs", "echomem_mcp_documents"):
            evidence_origin = "echomemory_http_api"
            retrieval_source_mode = "resource_search"
            platform_evidence_injection_enabled = "false"
        else:
            evidence_origin = "echomemory_http_api"
            retrieval_source_mode = "echo_http_native"
            platform_evidence_injection_enabled = "false"
        return {
            "question_id": self.question_id,
            "sample_id": self.sample_id,
            "category": self.category,
            "question": self.question,
            "answer": self.answer,
            "response": self.response,
            "retrieval_error": self.retrieval_error,
            "llm_error": self.llm_error,
            "elapsed_s": f"{self.elapsed_s:.2f}",
            "end_to_end_ms": f"{self.elapsed_s * 1000:.1f}",
            "retrieval_latency_ms": f"{self.retrieval_latency_s * 1000:.1f}",
            "injection_total_ms": (
                f"{(self.retrieval_latency_s + self.orchestration_latency_s) * 1000:.1f}"
            ),
            "llm_total_ms": f"{self.llm_latency_s * 1000:.1f}",
            "prompt_tokens": str(self.prompt_tokens),
            "completion_tokens": str(self.completion_tokens),
            "answer_prompt_tokens": (
                str(self.prompt_tokens) if usage_observed else ""
            ),
            "answer_completion_tokens": (
                str(self.completion_tokens) if usage_observed else ""
            ),
            "answer_total_tokens": str(answer_total) if usage_observed else "",
            "model_retry_count": (
                str(self.model_retry_count)
                if self.model_retry_count is not None
                else ""
            ),
            "num_retrieved": str(len(self.retrieval_items)),
            "retrieval_count": str(len(self.retrieval_items)),
            "retrieval_status": retrieval,
            "answer_status": answer,
            "model_status": model,
            "health_status": health,
            "tool_call_count": str(self.tool_call_count),
            "iterations": str(self.iterations),
            "qa_profile": self.qa_profile,
            "evidence_policy": "blackbox",
            "evidence_origin": evidence_origin,
            "retrieval_source_mode": retrieval_source_mode,
            "platform_evidence_injection_enabled": platform_evidence_injection_enabled,
            "qa_memory_writeback_enabled": "false",
        }
