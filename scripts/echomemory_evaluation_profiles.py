from __future__ import annotations

import argparse
from typing import Any


EVALUATION_PROFILE_CUSTOM = "custom"
EVALUATION_PROFILE_LEGACY_77 = "legacy-77"
EVALUATION_PROFILE_TEST_BEST = "test-best"
EVALUATION_PROFILE_CHOICES = (
    EVALUATION_PROFILE_CUSTOM,
    EVALUATION_PROFILE_LEGACY_77,
    EVALUATION_PROFILE_TEST_BEST,
)


LOCOMO_EVALUATION_PROFILES: dict[str, dict[str, Any]] = {
    EVALUATION_PROFILE_LEGACY_77: {
        "historical_result": "77.78% (63/81)",
        "prompt_context_mode": "legacy_eval",
        "prompt_system_mode": "legacy_eval",
        "session_context_mode": "group",
        "current_time_mode": "question_time",
        "initial_retrieval_query_mode": "question_only",
        "answer_temperature": 0.7,
        "omit_answer_temperature": True,
        "top_k": 25,
        "memory_budget_chars": 6000,
        "user_memory_budget_chars": 4000,
        "agent_memory_budget_chars": 2000,
        "retrieval_ranker": "score",
        "retrieval_uri_dedup": False,
        "tool_set": "vikingbot_native_safe",
        "tool_search_limit": 20,
        "tool_query_dedup_scope": "question",
        "search_tool_target_uri_schema": True,
        "tool_search_pool_multiplier": 1,
        "initial_tool_prefetch": False,
        "max_iterations": 50,
        "answer_refinement": False,
        "qa_memory_injection": True,
    },
    EVALUATION_PROFILE_TEST_BEST: {
        "historical_result": "85.19% (69/81)",
        "prompt_context_mode": "vikingbot_aligned",
        "prompt_system_mode": "vikingbot_aligned",
        "session_context_mode": "single",
        "current_time_mode": "runtime",
        "initial_retrieval_query_mode": "vikingbot_prompt",
        "answer_temperature": 0.7,
        "omit_answer_temperature": False,
        "top_k": 25,
        "memory_budget_chars": 6000,
        "user_memory_budget_chars": 4000,
        "agent_memory_budget_chars": 2000,
        "retrieval_ranker": "score",
        "retrieval_uri_dedup": False,
        "vikingboat_tool_loop": True,
        "tool_set": "vikingbot_native_safe",
        "tool_search_limit": 20,
        "tool_query_dedup_scope": "turn",
        "search_tool_target_uri_schema": False,
        "tool_search_pool_multiplier": 1,
        "initial_tool_prefetch": False,
        "max_iterations": 50,
        "answer_refinement": False,
        "qa_memory_injection": True,
    },
}


def apply_evaluation_profile(args: argparse.Namespace) -> dict[str, Any]:
    profile_name = str(
        getattr(args, "evaluation_profile", EVALUATION_PROFILE_CUSTOM)
        or EVALUATION_PROFILE_CUSTOM
    ).strip()
    if profile_name == EVALUATION_PROFILE_CUSTOM:
        return {}
    try:
        profile = LOCOMO_EVALUATION_PROFILES[profile_name]
    except KeyError as exc:
        choices = ", ".join(EVALUATION_PROFILE_CHOICES)
        raise ValueError(
            f"Unknown evaluation profile {profile_name!r}; expected one of: {choices}"
        ) from exc

    resolved: dict[str, Any] = {}
    for field, value in profile.items():
        if field == "historical_result":
            continue
        setattr(args, field, value)
        resolved[field] = value
    return resolved


def evaluation_profile_metadata(args: argparse.Namespace) -> dict[str, Any]:
    profile_name = str(
        getattr(args, "evaluation_profile", EVALUATION_PROFILE_CUSTOM)
        or EVALUATION_PROFILE_CUSTOM
    ).strip()
    profile = LOCOMO_EVALUATION_PROFILES.get(profile_name, {})
    return {
        "evaluation_profile": profile_name,
        "evaluation_profile_historical_result": str(
            profile.get("historical_result") or ""
        ),
        "evaluation_profile_resolved_settings": dict(
            getattr(args, "evaluation_profile_resolved_settings", {}) or {}
        ),
    }
