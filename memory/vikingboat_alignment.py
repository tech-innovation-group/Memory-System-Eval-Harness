from __future__ import annotations

from typing import Any


VIKINGBOT_ALIGNMENT_PROFILE = "vikingboat_context_v1"

# ContextBuilder / MemoryStore preloads memory into the prompt with broad recall.
VIKINGBOT_INITIAL_SEARCH_LIMIT = 30
VIKINGBOT_INITIAL_MIN_SCORE = 0.1
VIKINGBOT_USER_MEMORY_BUDGET_CHARS = 4000
VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS = 2000

# VikingSearchTool uses limit=20; the initial memory block above stays at 30.
VIKINGBOT_TOOL_SEARCH_LIMIT = 20
VIKINGBOT_TOOL_MIN_SCORE = 0.35
VIKINGBOT_TOOL_SET = "vikingbot_native_safe"
VIKINGBOT_MAX_ITERATIONS = 50


def alignment_metadata(backend: str, route: str = "") -> dict[str, Any]:
    return {
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "backend": backend,
        "backend_route": route,
        "initial_search_limit": VIKINGBOT_INITIAL_SEARCH_LIMIT,
        "initial_score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
        "tool_search_limit": VIKINGBOT_TOOL_SEARCH_LIMIT,
        "tool_min_score": VIKINGBOT_TOOL_MIN_SCORE,
        "tool_set": VIKINGBOT_TOOL_SET,
        "max_iterations": VIKINGBOT_MAX_ITERATIONS,
        "user_memory_budget_chars": VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
        "agent_memory_budget_chars": VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
        "user_agent_memory_split": True,
        "link_only_when_over_budget": True,
        "raw_turn_fallback": False,
    }


__all__ = [
    "VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS",
    "VIKINGBOT_ALIGNMENT_PROFILE",
    "VIKINGBOT_INITIAL_MIN_SCORE",
    "VIKINGBOT_INITIAL_SEARCH_LIMIT",
    "VIKINGBOT_MAX_ITERATIONS",
    "VIKINGBOT_TOOL_MIN_SCORE",
    "VIKINGBOT_TOOL_SEARCH_LIMIT",
    "VIKINGBOT_TOOL_SET",
    "VIKINGBOT_USER_MEMORY_BUDGET_CHARS",
    "alignment_metadata",
]
