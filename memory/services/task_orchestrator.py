from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import accounts as account_service


ECHOMEMORY_TASK_KINDS = {
    "echomemory_qa",
    "echomemory_generic_qa",
    "echomemory_import",
    "echomemory_qa_retry_failed",
}

DIRECT_COMMAND_KINDS = {
    "adapter",
    "local_agent",
    "openviking_qa",
    "openviking_import",
    "openviking_generic_qa",
    "echomemory_qa",
    "echomemory_generic_qa",
    "echomemory_import",
    "echomemory_qa_retry_failed",
    "openviking_qa_retry_failed",
    "openviking_qa_retry_missing",
    "judge",
    "stats",
}

WORKSPACE_SCOPED_TASK_KINDS = ECHOMEMORY_TASK_KINDS | {
    "openviking_import",
    "openviking_qa",
    "openviking_generic_qa",
    "openviking_qa_retry_failed",
    "openviking_qa_retry_missing",
}


@dataclass(frozen=True)
class TaskOrchestratorContext:
    safe_path: Callable[..., Any]
    default_repo: Path
    default_output_dir: Path
    default_config: Path
    default_cli_config: Path
    resolve_judge_token: Callable[..., Any]
    resolve_echomemory_runtime_env: Callable[..., Any]
    skip_model_preflight: Callable[..., Any]
    openai_compatible_chat_preflight: Callable[..., Any]
    ensure_task_model_preflight: Callable[..., Any]
    now_slug: Callable[[], str]
    restart_openviking_for_workspace: Callable[..., Any]
    prepare_connection_files: Callable[..., Any]
    redact_manifest_payload: Callable[..., Any]
    build_single_command: Callable[..., Any]
    build_pipeline_script: Callable[..., Any]
    build_distributed_script: Callable[..., Any]
    task_cls: type
    redacted_command: Callable[..., Any]
    write_manifest: Callable[..., Any]
    register_task: Callable[..., Any]
    start_task_thread: Callable[..., Any]


def normalize_task_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    dataset_format = str(normalized.get("dataset_format") or normalized.get("format") or "").strip().lower()
    if kind in {"openviking_generic_qa", "echomemory_generic_qa"}:
        normalized["dataset_format"] = dataset_format or "generic"
    elif dataset_format:
        normalized["dataset_format"] = dataset_format
    if kind in WORKSPACE_SCOPED_TASK_KINDS:
        backend = str(
            normalized.get("backend")
            or normalized.get("memoryBackend")
            or ("echomemory" if kind in ECHOMEMORY_TASK_KINDS else "openviking")
        ).strip()
        account = str(normalized.get("account") or "default").strip() or "default"
        workspace = str(
            normalized.get("workspace")
            or normalized.get("echomemory_workspace")
            or normalized.get("memoryWorkspace")
            or normalized.get("ovWorkspace")
            or normalized.get("openviking_workspace")
            or ""
        ).strip()
        if workspace:
            resolved = account_service.resolve_workspace_root(workspace, account, backend)
            normalized["workspace"] = resolved
            if backend == "echomemory":
                normalized["echomemory_workspace"] = resolved
            else:
                normalized["openviking_workspace"] = resolved
    return normalized


def _slug_fragment(value: str, *, max_length: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-").lower()
    if len(text) <= max_length:
        return text
    return text[-max_length:]


def _workspace_run_fragment(kind: str, payload: dict[str, Any]) -> str:
    if kind not in WORKSPACE_SCOPED_TASK_KINDS:
        return ""
    workspace = str(
        payload.get("workspace")
        or payload.get("echomemory_workspace")
        or payload.get("memoryWorkspace")
        or payload.get("ovWorkspace")
        or payload.get("openviking_workspace")
        or ""
    ).strip()
    if not workspace:
        return ""
    name = Path(workspace).expanduser().name.strip()
    if not name:
        return ""
    for prefix in ("echomem_workspace_", "openviking_workspace_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return _slug_fragment(name)


def _is_transient_model_preflight_status(status: Any, error: Any = "") -> bool:
    normalized = str(status or "").strip().lower()
    if normalized in {
        "timeout",
        "timeouterror",
        "urlerror",
        "remotedisconnected",
        "connectionreseterror",
        "connectionabortederror",
        "connectionrefusederror",
    }:
        return True
    if normalized.isdigit():
        code = int(normalized)
        if code in {408, 409, 425, 429, 500, 502, 503, 504}:
            return True
    message = str(error or "").strip().lower()
    return any(
        marker in message
        for marker in (
            "timed out",
            "temporary failure",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection refused",
            "remote end closed connection",
            "remote disconnected",
            "read operation timed out",
        )
    )


def _is_fatal_model_preflight_result(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict) or result.get("ok"):
        return False
    status = str(result.get("status") or "").strip().lower()
    error = str(result.get("error") or "")
    if status in {"missing_base_url", "missing_model", "missing_api_key"}:
        return True
    lowered = error.lower()
    if any(
        marker in lowered
        for marker in (
            "model_not_found",
            "does not exist or you do not have access",
            "invalid_request_error",
            "authentication failed",
            "autherror",
            "invalid api key",
            "incorrect api key",
        )
    ):
        return True
    return not _is_transient_model_preflight_status(status, error)


def create_task(
    kind: str,
    payload: dict[str, Any],
    *,
    context: TaskOrchestratorContext,
    find_duplicate_active_task: Callable[..., Any],
    find_conflicting_active_locomo_qa: Callable[..., Any],
    duplicate_error_cls: type[Exception],
    conflict_error_cls: type[Exception],
) -> Any:
    payload = normalize_task_payload(kind, payload)
    duplicate_task = find_duplicate_active_task(kind, payload)
    if duplicate_task:
        raise duplicate_error_cls(duplicate_task)
    active_locomo_qa = find_conflicting_active_locomo_qa(kind, payload)
    if active_locomo_qa:
        raise conflict_error_cls(active_locomo_qa)

    repo = context.safe_path(payload.get("repo") or str(context.default_repo))
    output_dir = context.safe_path(payload.get("output_dir") or str(context.default_output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    config = context.safe_path(payload.get("config") or str(context.default_config))
    cli_config = context.safe_path(payload.get("cli_config") or str(context.default_cli_config))
    judge_token = context.resolve_judge_token(payload, config)

    echomemory_env: dict[str, str] = {}
    if kind in ECHOMEMORY_TASK_KINDS:
        echomemory_env = context.resolve_echomemory_runtime_env(payload, config, judge_token)
        embedding_token = str(echomemory_env.get("token") or "").strip()
        chat_token = str(echomemory_env.get("chat_token") or "").strip()
        missing_runtime_keys: list[str] = []
        if not embedding_token:
            missing_runtime_keys.append("DASHSCOPE_API_KEY")
        if not chat_token:
            missing_runtime_keys.append("ECHOMEM_CHAT_API_KEY")
        if missing_runtime_keys and not payload.get("fallback_to_mock"):
            raise ValueError(
                "EchoMemory 导入/QA 启动前检查失败：缺少 "
                + "、".join(missing_runtime_keys)
                + "。请在页面或环境变量中分别补齐 embedding/chat provider key 后再运行。"
            )
        if chat_token and not context.skip_model_preflight(payload):
            preflight = context.openai_compatible_chat_preflight(
                str(echomemory_env.get("chat_base") or "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                str(echomemory_env.get("chat_model") or "deepseek-v4-flash"),
                chat_token,
                timeout_s=45,
            )
            if not preflight.get("ok") and _is_fatal_model_preflight_result(preflight):
                raise ValueError(
                    "EchoMemory 模型预检失败："
                    f"{preflight.get('model') or ''} @ {preflight.get('base_url') or ''} "
                    f"status={preflight.get('status')} · {preflight.get('error') or 'unknown error'}"
                )

    context.ensure_task_model_preflight(kind, payload, config, echomemory_env)

    workspace_fragment = _workspace_run_fragment(kind, payload)
    run_id_parts = [kind, context.now_slug()]
    if workspace_fragment:
        run_id_parts.append(workspace_fragment)
    run_id_parts.append(uuid.uuid4().hex[:6])
    run_id = "_".join(run_id_parts)
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "run.log"
    meta: dict[str, Any] = {}
    if kind == "openviking_import":
        meta["openviking"] = context.restart_openviking_for_workspace(payload, run_dir, config)

    needs_openviking_connection_files = kind not in ECHOMEMORY_TASK_KINDS and (
        payload.get("port")
        or payload.get("server_url")
        or payload.get("host")
        or payload.get("root_api_key")
        or payload.get("account")
    )
    if needs_openviking_connection_files:
        config, cli_config = context.prepare_connection_files(payload, run_dir, config, cli_config)

    env = {
        "OPENVIKING_CONFIG_FILE": str(config),
        "OPENVIKING_CLI_CONFIG_FILE": str(cli_config),
        "PYTHONUNBUFFERED": "1",
        "LOCOMO_TASK_PAYLOAD_JSON": json.dumps(context.redact_manifest_payload(payload), ensure_ascii=False),
    }
    if judge_token:
        env["LOCOMO_JUDGE_TOKEN"] = judge_token

    if kind in ECHOMEMORY_TASK_KINDS:
        embedding_token = str(echomemory_env.get("token") or "").strip()
        chat_token = str(echomemory_env.get("chat_token") or "").strip()
        if not embedding_token and chat_token:
            embedding_token = chat_token
        if not chat_token and embedding_token:
            chat_token = embedding_token
        if embedding_token and chat_token:
            env["DASHSCOPE_API_KEY"] = embedding_token
            env["ECHOMEM_CHAT_API_KEY"] = chat_token
            explicit_answer_token = payload.get("answer_token") or payload.get("judge_token")
            if kind in {"echomemory_qa", "echomemory_generic_qa", "echomemory_qa_retry_failed"} and not explicit_answer_token:
                env["LOCOMO_JUDGE_TOKEN"] = chat_token
            env["ECHOMEM_CHAT_PROVIDER"] = str(echomemory_env.get("chat_provider") or "deepseek")
            env["ECHOMEM_CHAT_MODEL"] = str(echomemory_env.get("chat_model") or "deepseek-v4-flash")
            env["ECHOMEM_CHAT_BASE_URL"] = str(echomemory_env.get("chat_base") or "https://dashscope.aliyuncs.com/compatible-mode/v1")
            env["DASHSCOPE_BASE_URL"] = str(echomemory_env.get("dashscope_base") or "https://dashscope.aliyuncs.com/compatible-mode/v1")
        if kind in {"echomemory_import", "echomemory_qa", "echomemory_generic_qa", "echomemory_qa_retry_failed"}:
            env.setdefault("ECHOMEM_AUTO_COMMIT_THRESHOLD", "0")
            env.setdefault("ECHOMEM_AUTO_FLUSH_ON_MESSAGE_PERSISTED", "false")

    if kind in DIRECT_COMMAND_KINDS:
        command, output_file, name = context.build_single_command(kind, payload, run_dir, config)
    elif kind == "pipeline":
        command, output_file, name = context.build_pipeline_script(payload, run_dir, config)
    elif kind == "distributed":
        command, output_file, name = context.build_distributed_script(payload, run_dir, config, cli_config)
    elif kind == "custom":
        raise ValueError("custom command runner is disabled in the web harness")
    else:
        raise ValueError(f"unknown task kind: {kind}")

    task = context.task_cls(
        id=run_id,
        kind=kind,
        name=name,
        command=command,
        display_command=context.redacted_command(command),
        cwd=str(repo),
        output_file=output_file,
        log_file=str(log_file),
        run_dir=str(run_dir),
        manifest_file=str(run_dir / "manifest.json"),
        env=env,
        meta={"config": context.redact_manifest_payload(payload), **meta},
    )
    context.write_manifest(task, payload, run_dir)
    context.register_task(task)
    context.start_task_thread(task)
    return task
