from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from ...vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)
from ..base import PluginTaskSpec


SafePath = Callable[[str], Path]
ResolveToken = Callable[[dict[str, Any], Path], str]


def looks_like_echomem_root(path: Path) -> bool:
    return (
        ((path / "packages" / "echomem" / "src").exists() and (path / "packages" / "echofs" / "src").exists())
        or ((path / "echomem").exists() and (path / "pyproject.toml").exists())
    )


def default_echomem_root() -> Path:
    preferred_local_roots = [
        Path.home() / "Code" / "echomemory" / "echo_memory_v006",
        Path.home() / "Code" / "echomemory" / "echo_memory",
    ]
    candidates = [
        os.environ.get("ECHOMEM_ROOT"),
        os.environ.get("ECHOMEMORY_ROOT"),
        *preferred_local_roots,
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw)).expanduser()
        if looks_like_echomem_root(path):
            return path
    fallback = os.environ.get("ECHOMEM_ROOT") or preferred_local_roots[0]
    return Path(str(fallback)).expanduser()


def echomem_root_value(payload: dict[str, Any]) -> str:
    return str(payload.get("echomem_root") or payload.get("echomemRoot") or default_echomem_root())


def bool_value(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def payload_value(payload: dict[str, Any], key: str, default: Any) -> Any:
    value = payload.get(key)
    return default if value in (None, "") else value


def _python_command(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "/" not in text and not text.startswith("."):
        return text
    path = Path(text).expanduser()
    return str(path) if path.exists() else None


def echomemory_python(payload: dict[str, Any]) -> str:
    root = Path(echomem_root_value(payload)).expanduser()
    candidates = [
        payload.get("python"),
        payload.get("echomem_python"),
        os.environ.get("ECHOMEM_PYTHON"),
        os.environ.get("ECHOMEMORY_PYTHON"),
        root / ".venv/bin/python",
        Path.home() / "openviking-env/bin/python",
        "python3",
    ]
    for raw in candidates:
        command = _python_command(raw)
        if command:
            return command
    return "python3"


def build_echomemory_import_command(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
) -> PluginTaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("echomemory_import_out_dir") or str(run_dir / "echomemory_import")))
    output_file = str(out_dir / "echomemory_import_summary.json")
    workspace = str(payload.get("workspace") or payload.get("echomemory_workspace") or str(run_dir / "echomemory_workspace"))
    command = [
        "/usr/bin/env",
        echomemory_python(payload),
        str(root / "scripts/echomemory_locomo_import.py"),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--echomem-root",
        echomem_root_value(payload),
        "--workspace",
        workspace,
        "--account",
        str(payload.get("account") or "default"),
        "--user-id",
        str(payload.get("user_id") or payload.get("em_user_id") or "default"),
        "--agent-id",
        str(payload.get("agent_id") or payload.get("em_agent_id") or "default"),
        "--sample",
        str(payload.get("sample") or "all"),
    ]
    if payload.get("echomem_config"):
        command += ["--echomem-config", str(payload["echomem_config"])]
    if payload.get("session_mode"):
        command += ["--session-mode", str(payload["session_mode"])]
    if payload.get("session_start"):
        command += ["--session-start", str(payload["session_start"])]
    if payload.get("session_end"):
        command += ["--session-end", str(payload["session_end"])]
    if payload.get("max_sessions"):
        command += ["--max-sessions", str(payload["max_sessions"])]
    skip_session_commit = bool_value(payload.get("skip_session_commit"), False)
    import_wait_mode = str(payload.get("import_wait_mode") or ("fast" if bool_value(payload.get("defer_artifact_wait"), False) else "full")).strip().lower()
    defer_artifact_wait = bool_value(payload.get("defer_artifact_wait"), import_wait_mode == "fast")
    default_commit_wait = 20 if skip_session_commit else (8 if defer_artifact_wait else 300)
    default_flush_timeout = 45 if skip_session_commit else (15 if defer_artifact_wait else 600)
    default_flush_attempts = 1 if skip_session_commit else (0 if defer_artifact_wait else 2)
    command += [
        "--import-wait-mode",
        "fast" if defer_artifact_wait else "full",
        "--commit-wait-s",
        str(payload_value(payload, "commit_wait_s", default_commit_wait)),
        "--commit-call-timeout-s",
        str(payload_value(payload, "commit_call_timeout_s", 300)),
        "--flush-call-timeout-s",
        str(payload_value(payload, "flush_call_timeout_s", default_flush_timeout)),
        "--flush-attempts",
        str(payload_value(payload, "flush_attempts", default_flush_attempts)),
    ]
    if defer_artifact_wait:
        command.append("--defer-artifact-wait")
    if bool_value(payload.get("continue_on_session_error"), False):
        command.append("--continue-on-session-error")
    if bool_value(payload.get("fallback_to_mock"), False):
        command.append("--fallback-to-mock")
    if skip_session_commit:
        command.append("--skip-session-commit")
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or "LoCoMo EchoMemory import",
        metadata={
            "task_kind": "echomemory_import",
            "backend": "echomemory",
            "workspace": workspace,
            "sample": str(payload.get("sample") or "all"),
            "session_limit": int(payload.get("max_sessions") or 0),
        },
    )


def build_echomemory_qa_command(
    payload: dict[str, Any],
    run_dir: Path,
    config: Path,
    root: Path,
    default_data: Path,
    defaults: dict[str, Any],
    safe_path: SafePath,
    resolve_judge_token: ResolveToken,
) -> PluginTaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("echomemory_qa_out_dir") or str(run_dir / "echomemory_qa")))
    output_file = str(out_dir / "echomemory_memory_qa_results.csv")
    workspace = str(payload.get("workspace") or payload.get("echomemory_workspace") or str(run_dir / "echomemory_workspace"))
    token = payload.get("answer_token") or payload.get("judge_token") or resolve_judge_token(payload, config)
    prompt_mode = str(payload.get("prompt_mode") or "vikingboat_lite")
    vikingboat_compat = bool_value(payload.get("vikingboat_compat"), prompt_mode == "vikingboat_compat")
    initial_tool_prefetch = bool_value(payload.get("initial_tool_prefetch"), False if vikingboat_compat else True)
    max_iterations = int(payload_value(payload, "max_iterations", VIKINGBOT_MAX_ITERATIONS if vikingboat_compat else 8))
    score_threshold = float(payload_value(payload, "score_threshold", VIKINGBOT_INITIAL_MIN_SCORE))
    tool_search_limit = int(payload_value(payload, "tool_search_limit", VIKINGBOT_TOOL_SEARCH_LIMIT))
    tool_min_score = float(payload_value(payload, "tool_min_score", VIKINGBOT_TOOL_MIN_SCORE))
    requested_tool_set = str(payload.get("tool_set") or (VIKINGBOT_TOOL_SET if vikingboat_compat else "search_read"))
    tool_set = "vikingboat_default" if requested_tool_set == VIKINGBOT_TOOL_SET else requested_tool_set
    top_k = int(payload_value(payload, "top_k", VIKINGBOT_INITIAL_SEARCH_LIMIT))
    user_budget_chars = int(payload_value(payload, "user_memory_budget_chars", VIKINGBOT_USER_MEMORY_BUDGET_CHARS))
    agent_budget_chars = int(payload_value(payload, "agent_memory_budget_chars", VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS))
    memory_budget_chars = int(payload_value(payload, "memory_budget_chars", user_budget_chars + agent_budget_chars))
    command = [
        "/usr/bin/env",
        echomemory_python(payload),
        str(root / "scripts/echomemory_memory_qa.py"),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--sample",
        str(payload.get("sample") or "conv-30"),
        "--echomem-root",
        echomem_root_value(payload),
        "--workspace",
        workspace,
        "--account",
        str(payload.get("account") or defaults.get("account") or "default"),
        "--user-id",
        str(payload.get("user_id") or payload.get("em_user_id") or "default"),
        "--agent-id",
        str(payload.get("agent_id") or payload.get("em_agent_id") or "default"),
        "--prompt-mode",
        prompt_mode,
        "--top-k",
        str(top_k),
        "--score-threshold",
        str(score_threshold),
        "--memory-budget-chars",
        str(memory_budget_chars),
        "--user-memory-budget-chars",
        str(user_budget_chars),
        "--agent-memory-budget-chars",
        str(agent_budget_chars),
        "--retrieval-mode",
        str(payload.get("retrieval_mode") or "both"),
        "--answer-base-url",
        payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or "",
        "--answer-model",
        payload.get("answer_model") or payload.get("judge_model") or defaults.get("answer_model") or defaults.get("judge_model") or "gpt-5.5",
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--question-timeout-s",
        str(payload.get("question_timeout_s") or 600),
        "--tool-set",
        tool_set,
        "--tool-search-limit",
        str(tool_search_limit),
        "--tool-min-score",
        str(tool_min_score),
        "--tool-log-chars",
        str(payload.get("tool_log_chars") or 1200),
        "--prefetch-read-count",
        str(payload.get("prefetch_read_count") or 4),
        "--prefetch-context-chars",
        str(payload.get("prefetch_context_chars") or 5000),
        "--max-iterations",
        str(max_iterations),
    ]
    if payload.get("echomem_config"):
        command += ["--echomem-config", str(payload["echomem_config"])]
    if payload.get("questions"):
        command += ["--questions", str(payload.get("questions") or "")]
    if payload.get("random_count"):
        command += ["--random-count", str(payload.get("random_count"))]
    if bool_value(payload.get("local_messages"), False):
        command.append("--local-messages")
    else:
        command.append("--no-local-messages")
    if bool_value(payload.get("vikingboat_tool_loop"), True):
        command.append("--vikingboat-tool-loop")
    else:
        command.append("--no-vikingboat-tool-loop")
    if vikingboat_compat:
        command.append("--vikingboat-compat")
    else:
        command.append("--no-vikingboat-compat")
    if initial_tool_prefetch:
        command.append("--initial-tool-prefetch")
    else:
        command.append("--no-initial-tool-prefetch")
    if bool_value(payload.get("fallback_to_one_shot"), True):
        command.append("--fallback-to-one-shot")
    else:
        command.append("--no-fallback-to-one-shot")
    if bool_value(payload.get("fallback_to_mock"), False):
        command.append("--fallback-to-mock")
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or "LoCoMo EchoMemory QA",
        metadata={
            **alignment_metadata("echomemory", "custom_agent_echomemory_sdk_memory_tools"),
            "task_kind": "echomemory_qa",
            "backend": "echomemory",
            "workspace": workspace,
            "sample": str(payload.get("sample") or "conv-30"),
            "prompt_mode": prompt_mode,
            "vikingboat_compat": vikingboat_compat,
            "memory_tool_loop_enabled": bool_value(payload.get("vikingboat_tool_loop"), True),
            "memory_tool_set": tool_set,
            "memory_tool_set_requested": requested_tool_set,
            "tool_search_limit": tool_search_limit,
            "tool_min_score": tool_min_score,
            "initial_search_limit": top_k,
            "initial_score_threshold": score_threshold,
            "initial_tool_prefetch_enabled": initial_tool_prefetch,
            "prefetch_read_count": int(payload.get("prefetch_read_count") or 4),
            "prefetch_context_chars": int(payload.get("prefetch_context_chars") or 5000),
            "max_iterations": max_iterations,
            "top_k": top_k,
            "score_threshold": score_threshold,
            "memory_budget_chars": memory_budget_chars,
            "user_memory_budget_chars": user_budget_chars,
            "agent_memory_budget_chars": agent_budget_chars,
            "local_messages": bool_value(payload.get("local_messages"), False),
            "local_session_summaries": False if vikingboat_compat else True,
            "local_atoms": False if vikingboat_compat else True,
            "local_timeline_hints": False if vikingboat_compat else True,
        },
    )
