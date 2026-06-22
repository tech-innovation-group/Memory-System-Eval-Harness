from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ...vikingboat_alignment import (
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    alignment_metadata,
)
from ..base import PluginTaskSpec


SafePath = Callable[[str], Path]
InferDatasetFormat = Callable[[Path], str]
ResolveToken = Callable[[dict[str, Any], Path], str]


def bool_value(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def build_openviking_qa_command(
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
    out_dir = safe_path(str(payload.get("openviking_qa_out_dir") or str(run_dir / "openviking_qa")))
    output_file = str(out_dir / "openviking_memory_qa_results.csv")
    host = str(payload.get("host") or "127.0.0.1")
    port = str(payload.get("port") or "1933")
    server_url = str(payload.get("server_url") or f"http://{host}:{port}")
    workspace = str(payload.get("workspace") or defaults.get("openviking_workspace") or defaults.get("workspace") or "")
    vikingbot_workspace = str(payload.get("vikingbot_workspace") or defaults.get("vikingbot_workspace") or "")
    token = payload.get("answer_token") or payload.get("judge_token") or resolve_judge_token(payload, config)
    group_chat = bool_value(payload.get("group_chat"), False)
    vikingbot_identity_mode = str(payload.get("vikingbot_identity_mode") or "sender_session")
    prompt_mode = str(payload.get("prompt_mode") or "vikingbot_aligned")
    tool_set = str(payload.get("openviking_tool_set") or VIKINGBOT_TOOL_SET)
    top_k = int(payload.get("top_k") or VIKINGBOT_INITIAL_SEARCH_LIMIT)
    tool_search_limit = int(payload.get("tool_search_limit") or VIKINGBOT_TOOL_SEARCH_LIMIT)
    tool_min_score = float(payload.get("tool_min_score") or VIKINGBOT_TOOL_MIN_SCORE)
    max_iterations = int(payload.get("max_iterations") or VIKINGBOT_MAX_ITERATIONS)
    judge_base_url = payload.get("judge_base_url") or payload.get("answer_base_url") or defaults.get("judge_base_url") or ""
    judge_model = payload.get("judge_model") or payload.get("answer_model") or defaults.get("judge_model") or defaults.get("answer_model") or "gpt-5.5"
    judge_token = payload.get("judge_token") or token
    judge_every = int(payload.get("judge_every") or 0)
    judge_parallel = int(payload.get("judge_parallel") or 6)
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/openviking_memory_qa.py"),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--sample",
        str(payload.get("sample") or "conv-30"),
        "--openviking-url",
        server_url,
        "--workspace",
        workspace,
        "--account",
        str(payload.get("account") or defaults.get("account") or "default"),
        "--user-id",
        str(payload.get("ov_user_id") or "default"),
        "--agent-id",
        str(payload.get("ov_agent_id") or "default"),
        "--answer-base-url",
        payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or "",
        "--answer-model",
        payload.get("answer_model") or payload.get("judge_model") or defaults.get("answer_model") or defaults.get("judge_model") or "gpt-5.5",
        "--answer-token",
        str(token or ""),
        "--judge-base-url",
        str(judge_base_url),
        "--judge-model",
        str(judge_model),
        "--judge-token",
        str(judge_token or ""),
        "--judge-every",
        str(judge_every),
        "--judge-parallel",
        str(judge_parallel),
        "--judge-timeout-s",
        str(payload.get("judge_timeout_s") or payload.get("timeout_s") or 90),
        "--judge-retries",
        str(payload.get("judge_retries") or payload.get("model_retries") or 5),
        "--top-k",
        str(top_k),
        "--prompt-mode",
        prompt_mode,
        "--openviking-tool-set",
        tool_set,
        "--tool-search-limit",
        str(tool_search_limit),
        "--tool-min-score",
        str(tool_min_score),
        "--max-iterations",
        str(max_iterations),
        "--retrieval-retries",
        str(payload.get("retrieval_retries") or 2),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
    ]
    if payload.get("questions"):
        command += ["--questions", str(payload.get("questions") or "")]
    if payload.get("random_count"):
        command += ["--random-count", str(payload.get("random_count"))]
    if vikingbot_workspace:
        command += ["--vikingbot-workspace", vikingbot_workspace]
    if payload.get("memory_users"):
        command += ["--memory-users", str(payload.get("memory_users") or "")]
    command.append("--group-chat" if group_chat else "--no-group-chat")
    command.append("--openviking-tool-loop" if bool_value(payload.get("openviking_tool_loop"), True) else "--no-openviking-tool-loop")
    command.append("--read-openviking-content" if bool_value(payload.get("read_openviking_content"), True) else "--no-read-openviking-content")
    command.append("--no-query-expansion")
    command.append("--no-lexical-fallback")
    command.append("--no-archive-fallback")
    command.append("--no-read-memory-files")
    command.append("--initial-agent-memory")
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or "LoCoMo 自定义 Agent OpenViking QA",
        metadata={
            **alignment_metadata("openviking", "memorybench_agent_openviking_adapter"),
            "task_kind": "openviking_qa",
            "workspace": workspace,
            "vikingbot_workspace": vikingbot_workspace,
            "sample": str(payload.get("sample") or "conv-30"),
            "eval_engine": "memorybench_agent_openviking",
            "agent_type": "memorybench_agent",
            "prompt_mode": prompt_mode,
            "group_chat": group_chat,
            "memory_user_strategy": "memory_users_override" if payload.get("memory_users") else ("vikingbot_group_chat" if group_chat else "sender_sample_namespace"),
            "vikingbot_identity_mode": vikingbot_identity_mode,
            "openviking_tool_loop": bool_value(payload.get("openviking_tool_loop"), True),
            "openviking_tool_set": tool_set,
            "tool_set": VIKINGBOT_TOOL_SET,
            "tool_search_limit": tool_search_limit,
            "tool_min_score": tool_min_score,
            "read_openviking_content": bool_value(payload.get("read_openviking_content"), True),
            "max_iterations": max_iterations,
            "top_k": top_k,
            "judge_every": judge_every,
            "judge_parallel": judge_parallel,
            "initial_search_limit": VIKINGBOT_INITIAL_SEARCH_LIMIT,
            "initial_score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
        },
    )


def build_openviking_generic_qa_command(
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
    fmt = str(payload.get("dataset_format") or payload.get("format") or "generic")
    out_dir = safe_path(str(payload.get("openviking_generic_qa_out_dir") or str(run_dir / "openviking_generic_qa")))
    output_file = str(out_dir / "openviking_generic_qa_results.csv")
    host = str(payload.get("host") or "127.0.0.1")
    port = str(payload.get("port") or "1933")
    server_url = str(payload.get("server_url") or f"http://{host}:{port}")
    workspace = str(payload.get("workspace") or defaults.get("openviking_workspace") or defaults.get("workspace") or "")
    token = payload.get("answer_token") or payload.get("judge_token") or resolve_judge_token(payload, config)
    answer_base_url = payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or ""
    answer_model = payload.get("answer_model") or payload.get("judge_model") or defaults.get("answer_model") or defaults.get("judge_model") or "gpt-5.5"
    judge_base_url = payload.get("judge_base_url") or answer_base_url
    judge_model = payload.get("judge_model") or answer_model
    auto_judge = bool_value(payload.get("auto_judge"), True)
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/openviking_generic_qa.py"),
        "--dataset",
        str(data),
        "--format",
        fmt,
        "--out-dir",
        str(out_dir),
        "--namespace",
        str(payload.get("memory_namespace") or payload.get("experiment_name") or run_dir.name),
        "--openviking-url",
        server_url,
        "--openviking-api-key",
        str(payload.get("openviking_api_key") or payload.get("root_api_key") or ""),
        "--account",
        str(payload.get("account") or defaults.get("account") or "default"),
        "--user-id",
        str(payload.get("ov_user_id") or payload.get("user_id") or "default"),
        "--agent-id",
        str(payload.get("ov_agent_id") or payload.get("agent_id") or "default"),
        "--identity-mode",
        str(payload.get("identity_mode") or "isolated_sample"),
        "--top-k",
        str(payload.get("top_k") or payload.get("chatTopK") or 8),
        "--retrieval-retries",
        str(payload.get("retrieval_retries") or 2),
        "--answer-base-url",
        str(answer_base_url),
        "--answer-model",
        str(answer_model),
        "--judge-base-url",
        str(judge_base_url),
        "--judge-model",
        str(judge_model),
        "--judge-parallel",
        str(payload.get("judge_parallel") or payload.get("parallel") or 4),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--commit-timeout-s",
        str(payload.get("commit_timeout_s") or 300),
    ]
    if payload.get("count") not in (None, ""):
        command += ["--count", str(payload.get("count"))]
    if payload.get("questions"):
        command += ["--questions", str(payload.get("questions") or "")]
    if bool_value(payload.get("import_only"), False):
        command.append("--import-only")
    if bool_value(payload.get("retry_failed"), False):
        command.append("--retry-failed")
    if bool_value(payload.get("resume"), True) is False:
        command.append("--no-resume")
    if payload.get("sample") not in (None, "", "all"):
        command += ["--sample", str(payload.get("sample"))]
    command.append("--read-openviking-content" if bool_value(payload.get("read_openviking_content"), True) else "--no-read-openviking-content")
    if auto_judge:
        command.append("--judge-after")
    if bool_value(payload.get("official_eval_after"), fmt in {"longmemeval", "hotpotqa"}):
        command.append("--official-eval-after")
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or f"{fmt} formal OpenViking QA",
        metadata={
            "task_kind": "openviking_generic_qa",
            "dataset_format": fmt,
            "workspace": workspace,
            "server_url": server_url,
            "eval_engine": "openviking_generic_qa",
            "prompt_mode": "strict_openviking_memory",
            "openviking_tool_set": "search_find",
            "read_openviking_content": bool_value(payload.get("read_openviking_content"), True),
            "identity_mode": str(payload.get("identity_mode") or "isolated_sample"),
            "auto_judge": auto_judge,
            "import_only": bool_value(payload.get("import_only"), False),
            "top_k": int(payload.get("top_k") or payload.get("chatTopK") or 8),
            "questions": str(payload.get("questions") or ""),
        },
    )


def build_openviking_import_command(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
) -> PluginTaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("openviking_import_out_dir") or str(run_dir / "openviking_import")))
    output_file = str(out_dir / "openviking_import_summary.json")
    host = str(payload.get("host") or "127.0.0.1")
    port = str(payload.get("port") or "19080")
    server_url = str(payload.get("server_url") or f"http://{host}:{port}")
    group_chat = bool_value(payload.get("group_chat"), True)
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/openviking_locomo_import.py"),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--openviking-url",
        server_url,
        "--api-key",
        str(payload.get("root_api_key") or ""),
        "--account",
        str(payload.get("account") or "default"),
        "--sample",
        str(payload.get("sample") or "all"),
        "--commit-timeout-s",
        str(payload.get("commit_timeout_s") or 300),
    ]
    if payload.get("ov_user_id"):
        command += ["--user-id", str(payload["ov_user_id"])]
    if payload.get("ov_agent_id"):
        command += ["--agent-id", str(payload["ov_agent_id"])]
    if payload.get("session_mode"):
        command += ["--session-mode", str(payload["session_mode"])]
    if payload.get("max_sessions"):
        command += ["--max-sessions", str(payload["max_sessions"])]
    command.append("--group-chat" if group_chat else "--no-group-chat")
    return PluginTaskSpec(
        command=command,
        output_file=output_file,
        name=payload.get("name") or "LoCoMo OpenViking commit import",
        metadata={
            "task_kind": "openviking_import",
            "server_url": server_url,
            "sample": str(payload.get("sample") or "all"),
            "session_limit": int(payload.get("max_sessions") or 0),
            "group_chat": group_chat,
            "identity_mode": "sample_id_user_agent" if not payload.get("ov_user_id") and not payload.get("ov_agent_id") else "fixed_user_agent",
        },
    )
