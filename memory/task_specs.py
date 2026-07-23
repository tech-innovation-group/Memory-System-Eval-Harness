from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SafePath = Callable[[str], Path]
InferDatasetFormat = Callable[[Path], str]
LoadDefaults = Callable[[Path], dict[str, Any]]
ResolveToken = Callable[[dict[str, Any], Path], str]
EnsureJudgeColumns = Callable[[Path], None]


@dataclass(frozen=True)
class TaskSpec:
    command: list[str]
    output_file: str
    name: str
    metadata: dict[str, Any] | None = None


def build_adapter_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
    infer_dataset_format: InferDatasetFormat,
) -> TaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    fmt = str(payload.get("dataset_format") or infer_dataset_format(data))
    out_dir = safe_path(str(payload.get("adapter_out_dir") or str(run_dir / "adapter")))
    output_file = str(out_dir / "benchmark_adapter_results.csv")
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/benchmark_adapter.py"),
        "--dataset",
        str(data),
        "--format",
        fmt,
        "--out-dir",
        str(out_dir),
        "--memory-mode",
        str(payload.get("memory_safety_mode") or "read_only_recommended"),
        "--namespace",
        str(payload.get("memory_namespace") or payload.get("experiment_name") or run_dir.name),
        "--mode",
        "dry-run",
        "--timeout-s",
        str(payload.get("timeout_s") or 180),
    ]
    if payload.get("count"):
        command += ["--count", str(payload["count"])]
    return TaskSpec(command, output_file, payload.get("name") or f"{fmt} adapter dry-run")


def build_local_agent_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
    infer_dataset_format: InferDatasetFormat,
) -> TaskSpec:
    data = safe_path(str(payload.get("data") or str(default_data)))
    fmt = str(payload.get("dataset_format") or infer_dataset_format(data))
    out_dir = safe_path(str(payload.get("local_agent_out_dir") or str(run_dir / "local_agent")))
    output_file = str(out_dir / "local_agent_results.csv")
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/local_memory_agent.py"),
        "--dataset",
        str(data),
        "--format",
        fmt,
        "--out-dir",
        str(out_dir),
        "--namespace",
        str(payload.get("memory_namespace") or payload.get("experiment_name") or run_dir.name),
        "--top-k",
        str(payload.get("local_agent_top_k") or 4),
        "--sample",
        str(payload.get("sample") or "all"),
    ]
    if payload.get("count"):
        command += ["--count", str(payload["count"])]
    if payload.get("questions"):
        command += ["--questions", str(payload["questions"])]
    return TaskSpec(command, output_file, payload.get("name") or f"{fmt} local memory-test agent")


def build_judge_task(
    payload: dict[str, Any],
    config: Path,
    root: Path,
    safe_path: SafePath,
    load_defaults: LoadDefaults,
    resolve_token: ResolveToken,
    ensure_judge_columns: EnsureJudgeColumns,
) -> TaskSpec:
    input_file = safe_path(str(payload.get("input") or ""))
    ensure_judge_columns(input_file)
    defaults = load_defaults(config)
    token = resolve_token(payload, config)
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/local_judge.py"),
        "--input",
        str(input_file),
        "--base-url",
        payload.get("judge_base_url") or defaults.get("judge_base_url") or "https://ark.cn-beijing.volces.com/api/v3",
        "--model",
        payload.get("judge_model") or defaults.get("judge_model") or "doubao-seed-2-0-pro-260215",
        "--parallel",
        str(payload.get("parallel") or 10),
    ]
    if payload.get("only_pending"):
        command += ["--only-pending"]
    if payload.get("question_ids"):
        command += ["--question-ids", str(payload.get("question_ids") or "")]
    if payload.get("row_indexes"):
        command += ["--row-indexes", str(payload.get("row_indexes") or "")]
    if payload.get("category"):
        command += ["--category", str(payload.get("category") or "")]
    if payload.get("query"):
        command += ["--query", str(payload.get("query") or "")]
    if payload.get("min_tokens") not in (None, ""):
        command += ["--min-tokens", str(payload.get("min_tokens"))]
    if payload.get("max_tokens") not in (None, ""):
        command += ["--max-tokens", str(payload.get("max_tokens"))]
    return TaskSpec(command, str(input_file), payload.get("name") or "Judge")


def build_stats_task(payload: dict[str, Any], root: Path, safe_path: SafePath) -> TaskSpec:
    input_file = safe_path(str(payload.get("input") or ""))
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/local_stats.py"),
        "--input",
        str(input_file),
    ]
    return TaskSpec(command, str(input_file), payload.get("name") or "统计结果")


def build_openviking_qa_retry_failed_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
    config: Path,
    resolve_token: ResolveToken,
) -> TaskSpec:
    input_file = safe_path(str(payload.get("input") or ""))
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("retry_out_dir") or str(run_dir / "openviking_qa_retry_failed")))
    token = payload.get("answer_token") or resolve_token(payload, config)
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/retry_failed_openviking_qa.py"),
        "--input",
        str(input_file),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--openviking-url",
        str(payload.get("openviking_url") or payload.get("server_url") or f"http://{payload.get('host') or '127.0.0.1'}:{payload.get('port') or '1933'}"),
        "--workspace",
        str(payload.get("workspace") or payload.get("openviking_workspace") or ""),
        "--vikingbot-workspace",
        str(payload.get("vikingbot_workspace") or ""),
        "--account",
        str(payload.get("account") or "default"),
        "--user-id",
        str(payload.get("ov_user_id") or payload.get("user_id") or "default"),
        "--agent-id",
        str(payload.get("ov_agent_id") or payload.get("agent_id") or "default"),
        "--vikingbot-identity-mode",
        str(payload.get("vikingbot_identity_mode") or "sender_session"),
        "--top-k",
        str(payload.get("top_k") or 30),
        "--prompt-mode",
        str(payload.get("prompt_mode") or "vikingbot_aligned"),
        "--openviking-tool-set",
        str(payload.get("openviking_tool_set") or "vikingbot_native_safe"),
        "--max-iterations",
        str(payload.get("max_iterations") or 50),
        "--retrieval-retries",
        str(payload.get("retrieval_retries") or 2),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--answer-base-url",
        str(payload.get("answer_base_url") or payload.get("judge_base_url") or ""),
        "--answer-model",
        str(payload.get("answer_model") or payload.get("judge_model") or "deepseek-v4-flash"),
        "--answer-token",
        str(token or ""),
    ]
    command.append("--openviking-tool-loop" if str(payload.get("openviking_tool_loop", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-openviking-tool-loop")
    command.append("--read-openviking-content" if str(payload.get("read_openviking_content", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-read-openviking-content")
    command.append("--group-chat" if str(payload.get("group_chat", False)).strip().lower() not in {"0", "false", "no", "off"} else "--no-group-chat")
    command.append("--initial-agent-memory" if str(payload.get("initial_agent_memory", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-initial-agent-memory")
    if payload.get("memory_users"):
        command += ["--memory-users", str(payload.get("memory_users") or "")]
    if payload.get("openviking_api_key") or payload.get("root_api_key"):
        command += ["--openviking-api-key", str(payload.get("openviking_api_key") or payload.get("root_api_key"))]
    return TaskSpec(command, str(input_file), payload.get("name") or "Retry failed OpenViking QA rows")


def build_openviking_qa_retry_missing_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
    config: Path,
    resolve_token: ResolveToken,
) -> TaskSpec:
    input_file = safe_path(str(payload.get("input") or ""))
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("retry_out_dir") or str(run_dir / "openviking_qa_retry_missing")))
    token = payload.get("answer_token") or resolve_token(payload, config)
    question_ids = str(payload.get("question_ids") or payload.get("questions") or "")
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/retry_missing_openviking_qa.py"),
        "--input",
        str(input_file),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--question-ids",
        question_ids,
        "--openviking-url",
        str(payload.get("openviking_url") or payload.get("server_url") or f"http://{payload.get('host') or '127.0.0.1'}:{payload.get('port') or '1933'}"),
        "--workspace",
        str(payload.get("workspace") or payload.get("openviking_workspace") or ""),
        "--vikingbot-workspace",
        str(payload.get("vikingbot_workspace") or ""),
        "--account",
        str(payload.get("account") or "default"),
        "--user-id",
        str(payload.get("ov_user_id") or payload.get("user_id") or "default"),
        "--agent-id",
        str(payload.get("ov_agent_id") or payload.get("agent_id") or "default"),
        "--vikingbot-identity-mode",
        str(payload.get("vikingbot_identity_mode") or "sender_session"),
        "--top-k",
        str(payload.get("top_k") or 30),
        "--prompt-mode",
        str(payload.get("prompt_mode") or "vikingbot_aligned"),
        "--openviking-tool-set",
        str(payload.get("openviking_tool_set") or "vikingbot_native_safe"),
        "--max-iterations",
        str(payload.get("max_iterations") or 50),
        "--retrieval-retries",
        str(payload.get("retrieval_retries") or 2),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--answer-base-url",
        str(payload.get("answer_base_url") or payload.get("judge_base_url") or ""),
        "--answer-model",
        str(payload.get("answer_model") or payload.get("judge_model") or "deepseek-v4-flash"),
        "--answer-token",
        str(token or ""),
    ]
    command.append("--openviking-tool-loop" if str(payload.get("openviking_tool_loop", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-openviking-tool-loop")
    command.append("--read-openviking-content" if str(payload.get("read_openviking_content", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-read-openviking-content")
    command.append("--group-chat" if str(payload.get("group_chat", False)).strip().lower() not in {"0", "false", "no", "off"} else "--no-group-chat")
    command.append("--initial-agent-memory" if str(payload.get("initial_agent_memory", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-initial-agent-memory")
    if payload.get("memory_users"):
        command += ["--memory-users", str(payload.get("memory_users") or "")]
    if payload.get("openviking_api_key") or payload.get("root_api_key"):
        command += ["--openviking-api-key", str(payload.get("openviking_api_key") or payload.get("root_api_key"))]
    return TaskSpec(command, str(input_file), payload.get("name") or "Retry missing OpenViking QA questions")


def build_echomemory_qa_retry_missing_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
    config: Path,
    resolve_token: ResolveToken,
) -> TaskSpec:
    input_file = safe_path(str(payload.get("input") or ""))
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("retry_out_dir") or str(run_dir / "echomemory_qa_retry_missing")))
    token = payload.get("answer_token") or resolve_token(payload, config)
    question_ids = str(payload.get("question_ids") or payload.get("questions") or "")
    fmt = str(payload.get("dataset_format") or payload.get("format") or "").strip().lower()
    default_top_k = 8 if fmt == "hotpotqa" else 30
    retrieval_mode = "search"
    echomem_base_url = str(payload.get("echomem_base_url") or payload.get("echomemBaseUrl") or "").strip()
    echomem_auth_key = str(payload.get("echomem_auth_key") or payload.get("echomemAuthKey") or "").strip()
    if not echomem_base_url:
        raise ValueError("Retry missing EchoMemory QA requires echomem_base_url for HTTP black-box retrieval")
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/retry_missing_echomemory_qa.py"),
        "--input",
        str(input_file),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--question-ids",
        question_ids,
        "--echomem-root",
        str(payload.get("echomem_root") or ""),
        "--echomem-config",
        str(payload.get("echomem_config") or ""),
        "--echomem-transport",
        "http",
        "--echomem-base-url",
        echomem_base_url,
        "--echomem-http-timeout-s",
        str(payload.get("echomem_http_timeout_s") or 60),
        "--workspace",
        str(payload.get("workspace") or payload.get("echomemory_workspace") or ""),
        "--account",
        str(payload.get("account") or "default"),
        "--user-id",
        str(payload.get("em_user_id") or payload.get("user_id") or "default"),
        "--agent-id",
        str(payload.get("em_agent_id") or payload.get("agent_id") or "default"),
        "--prompt-mode",
        str(payload.get("prompt_mode") or "vikingbot_agent_aligned"),
        "--top-k",
        str(payload.get("top_k") or default_top_k),
        "--score-threshold",
        str(payload.get("score_threshold") or 0.1),
        "--memory-budget-chars",
        str(payload.get("memory_budget_chars") or 6000),
        "--user-memory-budget-chars",
        str(payload.get("user_memory_budget_chars") or 4000),
        "--agent-memory-budget-chars",
        str(payload.get("agent_memory_budget_chars") or 2000),
        "--retrieval-mode",
        retrieval_mode,
        "--answer-base-url",
        str(payload.get("answer_base_url") or payload.get("judge_base_url") or ""),
        "--answer-model",
        str(payload.get("answer_model") or payload.get("judge_model") or "deepseek-v4-flash"),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--question-timeout-s",
        str(payload.get("question_timeout_s") or 600),
        "--qa-parallelism",
        str(payload.get("qa_parallelism") or 5),
        "--tool-set",
        str(payload.get("tool_set") or payload.get("openviking_tool_set") or "search_read"),
        "--tool-search-limit",
        str(payload.get("tool_search_limit") or 20),
        "--tool-min-score",
        str(payload.get("tool_min_score") or 0.35),
        "--tool-log-chars",
        str(payload.get("tool_log_chars") or 1200),
        "--prefetch-read-count",
        str(payload.get("prefetch_read_count") or 4),
        "--prefetch-context-chars",
        str(payload.get("prefetch_context_chars") or 5000),
        "--max-iterations",
        str(payload.get("max_iterations") or 50),
    ]
    command.append("--qa-memory-injection" if str(payload.get("qa_memory_injection", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-qa-memory-injection")
    if echomem_auth_key:
        command += ["--echomem-auth-key", echomem_auth_key]
    if token:
        command += ["--answer-token", str(token)]
    command.append("--vikingboat-tool-loop" if str(payload.get("vikingboat_tool_loop", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-vikingboat-tool-loop")
    command.append("--initial-tool-prefetch" if str(payload.get("initial_tool_prefetch", False)).strip().lower() not in {"0", "false", "no", "off"} else "--no-initial-tool-prefetch")
    return TaskSpec(command, str(input_file), payload.get("name") or "Retry missing EchoMemory QA questions")


def build_echomemory_qa_retry_failed_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
    config: Path,
    resolve_token: ResolveToken,
) -> TaskSpec:
    input_file = safe_path(str(payload.get("input") or ""))
    data = safe_path(str(payload.get("data") or str(default_data)))
    out_dir = safe_path(str(payload.get("retry_out_dir") or str(run_dir / "echomemory_qa_retry_failed")))
    output_file = str(out_dir / "retry_failed_round1" / "echomemory_memory_qa_results.csv")
    token = payload.get("answer_token") or resolve_token(payload, config)
    fmt = str(payload.get("dataset_format") or payload.get("format") or "").strip().lower()
    default_top_k = 8 if fmt == "hotpotqa" else 30
    retrieval_mode = "search"
    echomem_base_url = str(payload.get("echomem_base_url") or payload.get("echomemBaseUrl") or "").strip()
    echomem_auth_key = str(payload.get("echomem_auth_key") or payload.get("echomemAuthKey") or "").strip()
    if not echomem_base_url:
        raise ValueError("Retry failed EchoMemory QA requires echomem_base_url for HTTP black-box retrieval")
    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/retry_failed_echomemory_qa.py"),
        "--input",
        str(input_file),
        "--dataset",
        str(data),
        "--out-dir",
        str(out_dir),
        "--echomem-root",
        str(payload.get("echomem_root") or ""),
        "--echomem-config",
        str(payload.get("echomem_config") or ""),
        "--echomem-transport",
        "http",
        "--echomem-base-url",
        echomem_base_url,
        "--echomem-http-timeout-s",
        str(payload.get("echomem_http_timeout_s") or 60),
        "--workspace",
        str(payload.get("workspace") or payload.get("echomemory_workspace") or ""),
        "--account",
        str(payload.get("account") or "default"),
        "--user-id",
        str(payload.get("em_user_id") or payload.get("user_id") or "default"),
        "--agent-id",
        str(payload.get("em_agent_id") or payload.get("agent_id") or "default"),
        "--prompt-mode",
        str(payload.get("prompt_mode") or "vikingbot_agent_aligned"),
        "--top-k",
        str(payload.get("top_k") or default_top_k),
        "--score-threshold",
        str(payload.get("score_threshold") or 0.1),
        "--memory-budget-chars",
        str(payload.get("memory_budget_chars") or 6000),
        "--user-memory-budget-chars",
        str(payload.get("user_memory_budget_chars") or 4000),
        "--agent-memory-budget-chars",
        str(payload.get("agent_memory_budget_chars") or 2000),
        "--retrieval-mode",
        retrieval_mode,
        "--answer-base-url",
        str(payload.get("answer_base_url") or payload.get("judge_base_url") or ""),
        "--answer-model",
        str(payload.get("answer_model") or payload.get("judge_model") or "deepseek-v4-flash"),
        "--model-retries",
        str(payload.get("model_retries") or 5),
        "--timeout-s",
        str(payload.get("timeout_s") or 120),
        "--question-timeout-s",
        str(payload.get("question_timeout_s") or 600),
        "--qa-parallelism",
        str(payload.get("qa_parallelism") or 5),
        "--tool-set",
        str(payload.get("tool_set") or payload.get("openviking_tool_set") or "search_read"),
        "--tool-search-limit",
        str(payload.get("tool_search_limit") or 20),
        "--tool-min-score",
        str(payload.get("tool_min_score") or 0.35),
        "--tool-log-chars",
        str(payload.get("tool_log_chars") or 1200),
        "--prefetch-read-count",
        str(payload.get("prefetch_read_count") or 4),
        "--prefetch-context-chars",
        str(payload.get("prefetch_context_chars") or 5000),
        "--max-iterations",
        str(payload.get("max_iterations") or 50),
    ]
    command.append("--qa-memory-injection" if str(payload.get("qa_memory_injection", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-qa-memory-injection")
    if echomem_auth_key:
        command += ["--echomem-auth-key", echomem_auth_key]
    if token:
        command += ["--answer-token", str(token)]
    command.append("--vikingboat-tool-loop" if str(payload.get("vikingboat_tool_loop", True)).strip().lower() not in {"0", "false", "no", "off"} else "--no-vikingboat-tool-loop")
    command.append("--initial-tool-prefetch" if str(payload.get("initial_tool_prefetch", False)).strip().lower() not in {"0", "false", "no", "off"} else "--no-initial-tool-prefetch")
    return TaskSpec(command, output_file, payload.get("name") or "Retry failed EchoMemory QA rows")


def build_local_pipeline_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    default_data: Path,
    safe_path: SafePath,
    infer_dataset_format: InferDatasetFormat,
) -> TaskSpec:
    runner = str(payload.get("runner") or "local_agent")
    if runner != "local_agent":
        raise ValueError("外部 runner 已移除；流水线只支持 MemoryBench 本地基线或 OpenViking QA")
    return build_local_agent_task(payload, run_dir, root, default_data, safe_path, infer_dataset_format)


def build_echoagent_live_task(
    payload: dict[str, Any],
    run_dir: Path,
    root: Path,
    safe_path: SafePath,
) -> TaskSpec:
    """Build task spec for EchoAgent live interaction test."""
    out_dir = safe_path(str(payload.get("out_dir") or str(run_dir / "echoagent_live")))
    out_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "/usr/bin/env",
        "python3",
        str(root / "scripts/echoagent_live_test.py"),
        "--echoagent-url",
        str(payload.get("echoagent_url") or "http://127.0.0.1:31020"),
        "--echomem-url",
        str(payload.get("echomem_url") or "http://127.0.0.1:8010"),
        "--username",
        str(payload.get("username") or "test_user"),
        "--password",
        str(payload.get("password") or "test_password"),
        "--num-batches",
        str(payload.get("num_batches") or 3),
        "--queries-per-batch",
        str(payload.get("queries_per_batch") or 5),
        "--new-session-ratio",
        str(payload.get("new_session_ratio") or 0.3),
        "--typing-speed-ms",
        str(payload.get("typing_speed_ms") or 200),
        "--typing-jitter-ms",
        str(payload.get("typing_jitter_ms") or 20),
        "--scenario-model",
        str(payload.get("scenario_model") or "deepseek-v4-flash"),
        "--out-dir",
        str(out_dir),
    ]

    if payload.get("custom_scenario"):
        command.extend(["--custom-scenario", str(payload.get("custom_scenario"))])
    if payload.get("scenario_base_url"):
        command.extend(["--scenario-base-url", str(payload.get("scenario_base_url"))])
    if payload.get("scenario_api_key"):
        command.extend(["--scenario-api-key", str(payload.get("scenario_api_key"))])
    if payload.get("memory_engine_endpoint"):
        command.extend(["--memory-engine-endpoint", str(payload.get("memory_engine_endpoint"))])
    if payload.get("user_simulator_config"):
        command.extend(["--user-simulator-config", str(payload.get("user_simulator_config"))])
    if payload.get("evaluator_config"):
        command.extend(["--evaluator-config", str(payload.get("evaluator_config"))])

    output_file = str(out_dir / "echoagent_live_test_results.json")
    return TaskSpec(command, output_file, payload.get("name") or "EchoAgent 交互评测")
