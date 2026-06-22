from __future__ import annotations

import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import graph_report as graph_report_service
from . import reports as report_service
from . import runs as run_service
from .plugins.service import plugin_service
from .tasking import redacted_command


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def format_score(summary: dict[str, Any]) -> str:
    accuracy = summary.get("accuracy")
    return "待 Judge" if accuracy is None else f"{accuracy * 100:.1f}%"


def format_metric_percent(value: Any, fallback: str = "-") -> str:
    if value in (None, "", "-"):
        return fallback
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return fallback


def format_metric_seconds(value: Any, fallback: str = "-") -> str:
    if value in (None, "", "-"):
        return fallback
    try:
        return f"{float(value):.2f}s"
    except (TypeError, ValueError):
        return fallback


def csv_rows_limited(path: Path, limit: int = 10000) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for index, row in enumerate(reader):
            if index >= limit:
                break
            rows.append(row)
    return rows


def first_json_list(text: str, limit: int = 3) -> list[dict[str, Any]]:
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data[:limit] if isinstance(item, dict)]


def compact_text(value: Any, limit: int = 260) -> str:
    text = str(value or "").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def display_value(value: Any, fallback: str = "-") -> str:
    if value in (None, "", "-"):
        return fallback
    return str(value)


def display_ratio(numerator: Any, denominator: Any, fallback: str = "-") -> str:
    left = display_value(numerator, fallback="")
    right = display_value(denominator, fallback="")
    return f"{left}/{right}" if left and right else fallback


def config_value(config: dict[str, Any], *keys: str, fallback: str = "-") -> str:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


def summary_value(summary: dict[str, Any], summary_json: dict[str, Any], *keys: str, fallback: str = "-") -> str:
    for key in keys:
        value = summary.get(key)
        if value not in (None, ""):
            return str(value)
        value = summary_json.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


def native_top_k_label(value: str, prompt_mode: str) -> str:
    if str(prompt_mode or "").strip() == "native_vikingbot_cli":
        return "native VikingBot internal"
    return value


def counts_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    return " · ".join(f"{key}:{count}" for key, count in sorted(value.items(), key=lambda item: str(item[0])))


def percent_text(count: Any, total: Any) -> str:
    try:
        count_i = int(float(count or 0))
        total_i = int(float(total or 0))
    except (TypeError, ValueError):
        return "-"
    if total_i <= 0:
        return "-"
    return f"{count_i / total_i * 100:.1f}%"


def attribution_owner_label(value: Any) -> str:
    labels = {
        "model": "模型/API",
        "retrieval": "检索/记忆",
        "agent_prompt": "Agent Prompt",
        "context_engineering": "上下文工程",
        "agent": "Agent 回答",
        "judge": "Judge",
        "none": "无",
    }
    key = str(value or "-")
    return labels.get(key, key)


def attribution_kind_label(value: Any) -> str:
    labels = {
        "time": "时间题",
        "list": "列表/聚合题",
        "fact": "事实题",
    }
    key = str(value or "-")
    return labels.get(key, key)


def attribution_mode_label(value: Any) -> str:
    labels = {
        "correct": "已正确",
        "pending_judge": "待 Judge",
        "model_api_error": "模型/API 异常",
        "retrieval_error": "检索异常",
        "unknown_with_evidence": "有证据但 Unknown",
        "no_relevant_memory": "未召回可用记忆",
        "time_reasoning_error": "时间题推理错误",
        "list_aggregation_error": "列表/聚合遗漏",
        "evidence_mismatch": "证据与答案不一致",
        "semantic_mismatch": "语义错配或幻觉",
    }
    key = str(value or "-")
    return labels.get(key, key)


def attribution_severity_class(value: Any) -> str:
    key = str(value or "warn").lower()
    if key == "bad":
        return "bad"
    if key == "ok":
        return "ok"
    return "warn"


def int_value(value: Any, default: int = 0) -> int:
    if value in (None, "", "-"):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def bool_disabled(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in {"", "-", "false", "0", "no", "none", "disabled"}


def bool_enabled(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in {"true", "1", "yes", "on", "enabled"}


def text_value(value: Any) -> str:
    return str(value if value is not None else "").strip()


def lower_value(value: Any) -> str:
    return text_value(value).lower()


def bool_enabled_or_unknown(value: Any, *unknown_values: str) -> bool:
    lowered = lower_value(value)
    return bool_enabled(value) or lowered in {"", "-", *unknown_values}


def bool_disabled_or_unknown(value: Any) -> bool:
    return bool_disabled(value)


def vikingbot_alignment_gate(
    *,
    prompt_mode: Any,
    prompt_aligned: Any,
    tool_loop: Any,
    tool_set: Any,
    content_read: Any,
    group_chat: Any,
    identity_mode: Any,
    channel: Any,
    memory_user_strategy: Any,
    initial_agent_memory: Any,
    query_expansion: Any,
    lexical_fallback: Any,
    archive_fallback: Any,
    memory_file_read: Any,
    raw_turn_fallback: Any = "",
) -> dict[str, Any]:
    """Classify whether a run is comparable with the simplified VikingBot agent."""
    prompt = lower_value(prompt_mode)
    tool_set_text = lower_value(tool_set)
    loop = lower_value(tool_loop)
    content = lower_value(content_read)
    identity = lower_value(identity_mode)
    chat_channel = lower_value(channel)
    memory_strategy = lower_value(memory_user_strategy)
    custom_prompt_modes = {"vikingbot_aligned", "vikingboat_compat", "vikingboat_lite"}
    native = prompt in {"native_vikingbot_cli", ""}
    custom = prompt in custom_prompt_modes
    prompt_ok = bool_enabled(prompt_aligned) or native or custom
    tool_loop_ok = loop in {"native", "true", "1", "yes", "on", ""}
    native_tool_set_ok = tool_set_text in {"native_vikingbot_cli", ""}
    custom_tool_set_ok = tool_set_text in {"vikingbot_native_safe", "vikingboat_default", "vikingbot_openviking", "search_read", ""}
    tool_set_ok = native_tool_set_ok if native else custom_tool_set_ok
    content_ok = content in {"native", "true", "1", "yes", "on", ""}
    group_chat_ok = bool_enabled_or_unknown(group_chat)
    identity_ok = identity in {"sender_session", ""}
    channel_ok = chat_channel in {"cli", ""}
    memory_user_ok = memory_strategy in {"sender_sample_namespace", "vikingbot_group_chat", "memory_users_override", ""}
    agent_memory_ok = native or bool_enabled_or_unknown(initial_agent_memory)
    no_extra_context = (
        bool_disabled_or_unknown(query_expansion)
        and bool_disabled_or_unknown(lexical_fallback)
        and bool_disabled_or_unknown(archive_fallback)
        and bool_disabled_or_unknown(memory_file_read)
        and bool_disabled_or_unknown(raw_turn_fallback)
    )
    comparable = prompt_ok and tool_loop_ok and tool_set_ok and group_chat_ok and identity_ok and channel_ok and memory_user_ok and agent_memory_ok and no_extra_context
    # Native VikingBot reads through its own tools. For custom agents, OpenViking-compatible tools
    # may expose search/read semantics through the selected backend instead of native CLI paths.
    if native:
        comparable = comparable and content_ok
    status = "pass" if comparable else "warn"
    mode = "native_vikingbot" if native else ("custom_vikingbot_lite" if custom else "non_vikingbot_prompt")
    return {
        "status": status,
        "mode": mode,
        "comparable": comparable,
        "checks": {
            "prompt": prompt_ok,
            "tool_loop": tool_loop_ok,
            "tool_set": tool_set_ok,
            "content_read": content_ok,
            "group_chat": group_chat_ok,
            "agent_memory": agent_memory_ok,
            "identity": identity_ok,
            "channel": channel_ok,
            "memory_users": memory_user_ok,
            "no_extra_context": no_extra_context,
        },
    }


def gate_status(worst: list[str]) -> str:
    if "fail" in worst:
        return "fail"
    if "warn" in worst:
        return "warn"
    return "pass"


def safe_command_text(command: Any) -> str:
    if isinstance(command, list):
        return " ".join(redacted_command([str(item) for item in command]))
    if not command:
        return ""
    text = str(command)
    text = re.sub(r"(--(?:answer-token|judge-token|openviking-api-key|api-key|token|key|password)\s+)\S+", r"\1******", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "******", text)
    return text


def command_options(command: Any) -> dict[str, Any]:
    if not isinstance(command, list):
        return {}
    parsed: dict[str, Any] = {}
    index = 0
    while index < len(command):
        item = str(command[index])
        if not item.startswith("--"):
            index += 1
            continue
        key = item[2:].replace("-", "_")
        next_value = command[index + 1] if index + 1 < len(command) else None
        if next_value is None or str(next_value).startswith("--"):
            parsed[key] = False if item.startswith("--no-") else True
            index += 1
        else:
            parsed[key] = next_value
            index += 2
    return parsed


def backend_display_name(backend: Any) -> str:
    value = str(backend or "").strip().lower()
    if value == "echomemory":
        return "EchoMemory"
    if value == "openviking":
        return "OpenViking"
    return str(backend or "未知后端")


def dataset_display_name(dataset_format: Any, dataset_path: Any) -> str:
    fmt = str(dataset_format or "").strip().lower()
    if fmt == "locomo":
        return "LoCoMo"
    if fmt:
        return fmt
    path_text = str(dataset_path or "").lower()
    if "locomo" in path_text:
        return "LoCoMo"
    return "未知数据集"


def first_metric(summary: dict[str, Any], summary_json: dict[str, Any], config: dict[str, Any], *keys: str, fallback: str = "-") -> str:
    for key in keys:
        for source in (summary, summary_json, config):
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return str(value)
    return fallback


def enrich_config_from_summary(config: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """Backfill config from summary.json for script-created runs without manifest.json."""
    summary_json = summary.get("summary_json") if isinstance(summary.get("summary_json"), dict) else {}
    enriched = dict(config)
    aliases = {
        "backend": ("backend",),
        "dataset_format": ("dataset_format",),
        "data": ("dataset", "data"),
        "sample": ("sample",),
        "questions": ("questions",),
        "workspace": ("workspace", "openviking_workspace", "echomemory_workspace"),
        "account": ("account",),
        "answer_model": ("answer_model", "model"),
        "answer_base_url": ("answer_base_url",),
        "judge_model": ("judge_model",),
        "judge_base_url": ("judge_base_url",),
        "embedding_model": ("embedding_model", "embed_model"),
        "prompt_mode": ("prompt_mode",),
        "top_k": ("top_k", "initial_search_limit"),
        "score_threshold": ("score_threshold", "initial_score_threshold"),
        "tool_search_limit": ("tool_search_limit",),
        "tool_min_score": ("tool_min_score",),
        "max_iterations": ("max_iterations",),
        "echomem_root": ("echomem_root",),
        "echomem_config": ("echomem_config",),
        "alignment_backend_route": ("alignment_backend_route", "backend_route"),
        "memory_tool_loop_enabled": ("memory_tool_loop_enabled", "openviking_tool_loop_enabled"),
        "memory_tool_set": ("memory_tool_set", "openviking_tool_set", "tool_set"),
        "memory_content_read_enabled": ("memory_content_read_enabled", "openviking_content_read_enabled"),
        "memory_tool_names": ("memory_tool_names",),
    }
    for target, source_keys in aliases.items():
        if enriched.get(target) not in (None, ""):
            continue
        value = None
        for source in (summary_json, summary):
            if not isinstance(source, dict):
                continue
            for key in source_keys:
                value = source.get(key)
                if value not in (None, ""):
                    break
            if value not in (None, ""):
                break
        if value not in (None, ""):
            enriched[target] = value
    return enriched


def live_duration_seconds(record: dict[str, Any], manifest: dict[str, Any]) -> Any:
    duration = record.get("duration_s")
    if manifest.get("status") != "running":
        return duration
    started_at = manifest.get("started_at") or record.get("created_at")
    if not started_at:
        return duration
    try:
        started = datetime.fromisoformat(str(started_at))
        return round(max(0.0, (datetime.now() - started).total_seconds()), 1)
    except Exception:
        return duration


def read_config_snapshot(manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    candidates = []
    if manifest.get("config_snapshot_file"):
        candidates.append(Path(str(manifest["config_snapshot_file"])))
    candidates.append(run_dir / "config_snapshot.json")
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = read_json(path)
        except Exception:
            continue
        return data if isinstance(data, dict) else {}
    return {}


def merged_config(manifest: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    config = {}
    if isinstance(snapshot.get("config"), dict):
        config.update(snapshot["config"])
    if isinstance(manifest.get("config"), dict):
        config.update(manifest["config"])
    command = manifest.get("command") or snapshot.get("command") or []
    opts = command_options(command)
    aliases = {
        "dataset": "data",
        "format": "dataset_format",
        "openviking_url": "openviking_url",
        "workspace": "workspace",
        "vikingbot_workspace": "vikingbot_workspace",
        "account": "account",
        "user_id": "ov_user_id",
        "agent_id": "ov_agent_id",
        "top_k": "top_k",
        "prompt_mode": "prompt_mode",
        "openviking_tool_loop": "openviking_tool_loop",
        "openviking_tool_set": "openviking_tool_set",
        "read_openviking_content": "read_openviking_content",
        "group_chat": "group_chat",
        "initial_agent_memory": "initial_agent_memory",
        "max_iterations": "max_iterations",
        "answer_base_url": "answer_base_url",
        "answer_model": "answer_model",
        "judge_base_url": "judge_base_url",
        "judge_model": "judge_model",
        "model_retries": "model_retries",
        "timeout_s": "timeout_s",
        "sample": "sample",
        "questions": "questions",
    }
    for option, key in aliases.items():
        value = opts.get(option)
        if value not in (None, ""):
            config[key] = value
    if not config.get("openviking_url") and config.get("host") and config.get("port"):
        config["openviking_url"] = f"http://{config['host']}:{config['port']}"
    return config


def context_composition(summary: dict[str, Any], summary_json: dict[str, Any], config: dict[str, Any], backend: str = "openviking") -> list[tuple[str, str]]:
    prompt_mode_text = summary_value(summary, summary_json, "prompt_mode", fallback=config_value(config, "prompt_mode"))
    top_k_text = native_top_k_label(config_value(config, "top_k", "local_agent_top_k"), prompt_mode_text)
    if backend == "echomemory":
        tool_loop_label = "Memory tool loop"
        tool_set_label = "Memory tool set"
        content_read_label = "Memory content read"
        tool_loop_value = summary_value(summary, summary_json, "memory_tool_loop_enabled", "openviking_tool_loop_enabled", fallback=config_value(config, "memory_tool_loop_enabled", "openviking_tool_loop"))
        tool_set_value = summary_value(summary, summary_json, "memory_tool_set", "openviking_tool_set", fallback=config_value(config, "memory_tool_set", "openviking_tool_set"))
        content_read_value = summary_value(summary, summary_json, "memory_content_read_enabled", "openviking_content_read_enabled", fallback=config_value(config, "memory_content_read_enabled", "read_openviking_content"))
    else:
        tool_loop_label = "OpenViking tool loop"
        tool_set_label = "OpenViking tool set"
        content_read_label = "OpenViking content read"
        tool_loop_value = summary_value(summary, summary_json, "openviking_tool_loop_enabled", fallback=config_value(config, "openviking_tool_loop"))
        tool_set_value = summary_value(summary, summary_json, "openviking_tool_set", fallback=config_value(config, "openviking_tool_set"))
        content_read_value = summary_value(summary, summary_json, "openviking_content_read_enabled", fallback=config_value(config, "read_openviking_content"))
    return [
        ("Prompt mode", prompt_mode_text),
        ("VikingBot prompt aligned", summary_value(summary, summary_json, "vikingbot_prompt_aligned")),
        ("VikingBot identity mode", summary_value(summary, summary_json, "vikingbot_identity_mode", fallback=config_value(config, "vikingbot_identity_mode"))),
        ("VikingBot channel", summary_value(summary, summary_json, "vikingbot_channel")),
        ("VikingBot local workspace", summary_value(summary, summary_json, "vikingbot_workspace", fallback=config_value(config, "vikingbot_workspace"))),
        ("VikingBot bootstrap files", summary_value(summary, summary_json, "vikingbot_bootstrap_files")),
        ("VikingBot skills", summary_value(summary, summary_json, "vikingbot_skill_names")),
        ("Group chat", summary_value(summary, summary_json, "group_chat", fallback=config_value(config, "group_chat"))),
        ("Memory user strategy", summary_value(summary, summary_json, "memory_user_strategy")),
        ("Initial agent memory", summary_value(summary, summary_json, "initial_agent_memory_enabled", fallback=config_value(config, "initial_agent_memory"))),
        (tool_loop_label, tool_loop_value),
        (tool_set_label, tool_set_value),
        (content_read_label, content_read_value),
        ("Max iterations", summary_value(summary, summary_json, "max_iterations", fallback=config_value(config, "max_iterations"))),
        ("Avg iteration", summary_value(summary, summary_json, "avg_iteration")),
        ("Rows with tool calls", summary_value(summary, summary_json, "tool_call_rows")),
        ("Tool calls total", summary_value(summary, summary_json, "tool_call_total")),
        ("Tool call names", counts_text(summary.get("tool_name_counts") or summary_json.get("tool_name_counts"))),
        ("Retrieval mode", summary_value(summary, summary_json, "retrieval_mode")),
        ("Retrieval limit", top_k_text),
        ("Query expansion", summary_value(summary, summary_json, "query_expansion_enabled")),
        ("Lexical fallback", summary_value(summary, summary_json, "lexical_fallback_enabled")),
        ("Session archive fallback", summary_value(summary, summary_json, "archive_fallback_enabled")),
        ("Memory file read fallback", summary_value(summary, summary_json, "memory_file_read_enabled")),
        ("Long-term memory hits", summary_value(summary, summary_json, "memory_hit_total")),
        ("Avg long-term memory hits", summary_value(summary, summary_json, "avg_memory_hit_count", "avg_retrieval_count")),
        ("Session archive hits", summary_value(summary, summary_json, "archive_fallback_total")),
        ("Retrieval tokens est total", "n/a"),
        ("Injection tokens est total", "n/a"),
        ("Answer tokens total", summary_value(summary, summary_json, "answer_total_tokens")),
    ]


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def report_backend(record: dict[str, Any], manifest: dict[str, Any], config: dict[str, Any], summary: dict[str, Any]) -> str:
    summary_json = summary.get("summary_json") if isinstance(summary.get("summary_json"), dict) else {}
    candidates = [
        manifest.get("backend"),
        (manifest.get("metadata") or {}).get("backend") if isinstance(manifest.get("metadata"), dict) else "",
        config.get("backend"),
        summary.get("backend"),
        summary_json.get("backend"),
        manifest.get("kind"),
        record.get("kind"),
        record.get("agent_type"),
        manifest.get("agent_type"),
    ]
    haystack = " ".join(str(item or "") for item in candidates).lower()
    if "echomemory" in haystack or "echomem" in haystack:
        return "echomemory"
    if "openviking" in haystack or "viking" in haystack:
        return "openviking"
    return "openviking"


def import_integrity_unavailable(backend: str, reason: str, *, workspace: str = "", account: str = "default", sample: str = "") -> dict[str, Any]:
    memory_label = "EchoMemory" if backend == "echomemory" else "OpenViking"
    return {
        "backend": backend,
        "memory_label": memory_label,
        "status": "not_available",
        "verification_level": "warn",
        "reason": reason,
        "workspace": workspace,
        "account": account,
        "sample": sample,
        "samples": "-",
        "complete_samples": "-",
        "incomplete_samples": "-",
        "expected_messages": "-",
        "submitted_messages": "-",
        "session_count": "-",
        "memory_files": "-",
        "summary_extracted_memories": "-",
        "pending_after_commit_total": "-",
        "checks": [{"name": "导入完整性", "ok": False, "level": "warn", "message": reason}],
    }


def latest_import_integrity(
    run_dir: Path,
    config: dict[str, Any],
    *,
    backend: str = "openviking",
    data_path: Path | None = None,
    sample: str = "",
) -> dict[str, Any]:
    runs_root = run_dir.parent
    workspace = config_value(config, "openviking_workspace", "ov_workspace", "workspace", "echomemory_workspace", fallback="")
    account = config_value(config, "account", "ov_account", fallback="default")
    user_id = config_value(config, "ov_user_id", "user_id", "em_user_id", fallback="default")
    sample = "" if str(sample or "").strip().lower() in {"", "-", "all", "*", "全部"} else str(sample).strip()
    if backend == "echomemory":
        if not workspace or workspace == "-":
            return import_integrity_unavailable(
                backend,
                "当前 EchoMemory run 缺少 workspace 配置，无法可靠校验导入完整性。",
                workspace=workspace,
                account=account,
                sample=sample,
            )
        try:
            result = plugin_service.import_integrity(
                "echomemory",
                Path(workspace).expanduser(),
                account,
                runs_root,
                data_path or Path(config_value(config, "data", "dataset", fallback="")),
                sample,
                None,
                user_id,
            )
        except Exception as exc:
            return import_integrity_unavailable(
                backend,
                f"EchoMemory 导入完整性不可验证：{exc}",
                workspace=workspace,
                account=account,
                sample=sample,
            )
        result["run_id"] = result.get("run_id") or result.get("summary_path") or "-"
        result["output_file"] = result.get("summary_path") or ""
        result["memory_dir"] = result.get("memory_root") or result.get("account_path") or ""
        result["extracted_memories"] = result.get("summary_extracted_memories") or ""
        result["pending_after_commit_total"] = result.get("pending_after_commit_total", 0)
        return result

    candidates: list[tuple[float, Path, dict[str, Any], dict[str, Any]]] = []
    if not runs_root.exists():
        return {}
    if not workspace or workspace == "-":
        return import_integrity_unavailable(
            backend,
            "当前 OpenViking run 缺少 workspace 配置，无法可靠匹配导入 summary。",
            workspace=workspace,
            account=account,
            sample=sample,
        )
    import_patterns = ["openviking_import_*/manifest.json"]
    for pattern in import_patterns:
      for manifest_path in runs_root.glob(pattern):
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        import_config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
        import_workspace = config_value(import_config, "openviking_workspace", "ov_workspace", "workspace", fallback="")
        if workspace and import_workspace and workspace != import_workspace:
            continue
        output_file = manifest.get("output_file") or ""
        output_path = Path(output_file) if output_file else None
        if not output_path or not output_path.exists():
            continue
        try:
            summary = read_json(output_path)
        except Exception:
            continue
        if not isinstance(summary, dict) or not summary.get("records"):
            continue
        candidates.append((manifest_path.stat().st_mtime, manifest_path.parent, manifest, summary))
    if not candidates:
        return import_integrity_unavailable(
            backend,
            "没有找到匹配当前 workspace/account/sample 的 OpenViking 导入 summary。",
            workspace=workspace,
            account=account,
            sample=sample,
        )

    _, import_run_dir, manifest, summary = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    records = [record for record in (summary.get("records") or []) if isinstance(record, dict)]
    session_count = 0
    pending_after_commit_total = 0
    incomplete_records = []
    extracted_memories = 0
    for record in records:
        sessions = [session for session in (record.get("session_records") or []) if isinstance(session, dict)]
        session_count += len(sessions)
        if record.get("integrity") != "complete":
            incomplete_records.append(record.get("sample_id") or record.get("session_id") or "-")
        for session in sessions:
            try:
                pending_after_commit_total += int(session.get("pending_message_count_after_commit") or 0)
            except Exception:
                pass
            after = session.get("session_after_commit") or {}
            memories = after.get("memories_extracted") or {}
            try:
                extracted_memories += int(memories.get("total") or 0)
            except Exception:
                pass
    memory_files = 0
    memory_dir = None
    if workspace:
        memory_dir = Path(workspace) / "viking" / account / "user" / user_id / "memories"
        memory_files = count_files(memory_dir)

    return {
        "backend": backend,
        "memory_label": "OpenViking",
        "run_id": manifest.get("id") or import_run_dir.name,
        "run_dir": str(import_run_dir),
        "output_file": manifest.get("output_file") or "",
        "log_file": manifest.get("log_file") or "",
        "status": summary.get("status") or manifest.get("status") or "-",
        "samples": summary.get("samples"),
        "complete_samples": summary.get("complete_samples"),
        "incomplete_samples": summary.get("incomplete_samples"),
        "expected_messages": summary.get("expected_messages"),
        "submitted_messages": summary.get("submitted_messages"),
        "session_count": session_count or (manifest.get("summary") or {}).get("session_count"),
        "extracted_memories": extracted_memories or (manifest.get("summary") or {}).get("extracted_memories"),
        "pending_after_commit_total": pending_after_commit_total,
        "incomplete_records": incomplete_records,
        "workspace": workspace,
        "account": account,
        "user_id": user_id,
        "memory_dir": str(memory_dir) if memory_dir else "",
        "memory_files": memory_files,
    }


def echomemory_graph_dir_from_import_integrity(import_integrity: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    seen: set[str] = set()
    for key in ("account_path", "memory_root", "memory_dir"):
        value = str(import_integrity.get(key) or "").strip()
        if not value or value == "-":
            continue
        base = Path(value).expanduser()
        candidates.extend([base, base / "memory", base / "memory" / ".graph", base / ".graph"])
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_dir() and candidate.name == ".graph":
            return candidate
        if candidate.is_dir():
            nested = candidate / ".graph"
            if nested.is_dir():
                return nested
    return None


def maybe_export_echomemory_graph_report(
    run_dir: Path,
    record: dict[str, Any],
    *,
    current_backend: str,
    import_integrity: dict[str, Any] | None,
) -> dict[str, Any]:
    if current_backend != "echomemory" or not isinstance(import_integrity, dict):
        return {}
    graph_dir = echomemory_graph_dir_from_import_integrity(import_integrity)
    if not graph_dir:
        return {}
    result: dict[str, Any] = {"graph_dir": str(graph_dir)}
    try:
        graph_report_html = graph_report_service.render_graph_report_html(
            graph_dir,
            run_title=str(record.get("name") or record.get("id") or ""),
            run_dir=str(run_dir),
        )
        graph_report_path = run_dir / "graph_report.html"
        graph_report_path.write_text(graph_report_html, encoding="utf-8")
        result["graph_report_html_file"] = str(graph_report_path)
    except Exception as exc:
        result["graph_report_error"] = str(exc)
    return result


def export_report(run_dir: Path, active_run_ids: set[str] | None = None) -> dict[str, Any]:
    detail = run_service.run_detail(run_dir, active_run_ids)
    if not detail:
        raise FileNotFoundError("run not found")

    record = detail["record"]
    manifest = detail.get("manifest") or {}
    duration_s = live_duration_seconds(record, manifest)
    config_snapshot = read_config_snapshot(manifest, run_dir)
    config = merged_config(manifest, config_snapshot)
    agent_type = manifest.get("agent_type") or record.get("agent_type") or run_service.agent_type_for(
        str(record.get("kind") or ""),
        config,
    )
    summary = record.get("summary") or {}
    output_file = record.get("output_file") or ""
    output_path = Path(output_file) if output_file else None
    if output_path and output_path.exists() and output_path.suffix.lower() == ".json":
        parsed_json_summary = report_service.parse_json_run_summary(output_path)
        if parsed_json_summary.get("dataset_format") == "chenmo":
            summary = {**summary, **parsed_json_summary}
            current_backend = report_backend(record, manifest, config, summary)
            dataset_path_text = config_value(config, "data", "dataset", fallback="")
            sample_filter = config_value(config, "sample", fallback="all")
            if sample_filter in {"", "-"}:
                sample_filter = "all"
            dataset_path_for_integrity = Path(dataset_path_text).expanduser() if dataset_path_text and dataset_path_text != "-" else None
            import_integrity = latest_import_integrity(
                run_dir,
                config,
                backend=current_backend,
                data_path=dataset_path_for_integrity,
                sample=sample_filter,
            )
            graph_report_artifacts = maybe_export_echomemory_graph_report(
                run_dir,
                record,
                current_backend=current_backend,
                import_integrity=import_integrity,
            )
            source_md = run_dir / "chenmo_analysis.md"
            report_path = run_dir / "report.md"
            report_html_path = run_dir / "report.html"
            if source_md.exists():
                report_text = source_md.read_text(encoding="utf-8", errors="replace")
            else:
                categories = summary.get("categories") or {}
                category_lines = [
                    f"- {name}: {counts.get('CORRECT', 0)} passed / {counts.get('CORRECT', 0) + counts.get('WRONG', 0)} total"
                    for name, counts in sorted(categories.items(), key=lambda item: str(item[0]))
                ]
                report_text = "\n".join([
                    f"# {record.get('name') or record.get('id')}",
                    "",
                    "## Summary",
                    "",
                    f"- Run ID: `{record.get('id')}`",
                    f"- Status: `{record.get('status')}`",
                    "- Dataset: `ChenMo`",
                    f"- Rows: `{summary.get('rows', '-')}`",
                    f"- Graded: `{summary.get('graded', '-')}`",
                    f"- Correct: `{summary.get('correct', '-')}`",
                    f"- Wrong: `{summary.get('wrong', '-')}`",
                    f"- Formal Judge score: `{format_score(summary)}`",
                    f"- Pass rate: `{summary.get('pass_rate', '-')}`",
                    f"- Avg score: `{summary.get('avg_score', '-')}`",
                    f"- Answer model: `{summary.get('answer_model', '-')}`",
                    f"- Embedding model: `{summary.get('embedding_model', '-')}`",
                    f"- Scenario: `{summary.get('scenario_path', '-')}`",
                    f"- Output JSON: `{output_file}`",
                    "",
                    "## Category Breakdown",
                    "",
                    *(category_lines or ["- No category breakdown available."]),
                    "",
                ])
            report_path.write_text(report_text, encoding="utf-8")
            source_html = run_dir / "chenmo_report.html"
            if source_html.exists():
                html_report = source_html.read_text(encoding="utf-8", errors="replace")
            else:
                html_report = (
                    "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                    f"<title>{html.escape(str(record.get('name') or record.get('id') or 'ChenMo Report'))}</title>"
                    "<body>"
                    f"<pre>{html.escape(report_text)}</pre>"
                    "</body></html>"
                )
            report_html_path.write_text(html_report, encoding="utf-8")
            return {
                "report_file": str(report_path),
                "report_html_file": str(report_html_path),
                "text": report_text,
                **graph_report_artifacts,
            }
    if output_path and output_path.exists() and output_path.suffix.lower() == ".csv":
        summary = {**summary, **report_service.parse_csv_summary(output_path)}
    config = enrich_config_from_summary(config, summary)

    analysis = None
    if output_path and output_path.exists():
        analysis_path = output_path.with_suffix(".wrong_analysis.json")
        if analysis_path.exists():
            analysis = read_json(analysis_path)
            if not isinstance(analysis, dict) or "failure_attribution" not in analysis:
                analysis = report_service.analyze_wrong_answers(output_path, analysis_path)
        else:
            analysis = report_service.analyze_wrong_answers(output_path, analysis_path)

    log_info = run_service.tail_file(Path(record.get("log_file") or "")) if record.get("log_file") else {}
    rate_hits = log_info.get("rate_limit_hits", [])
    rate_hit_count = int(log_info.get("rate_limit_count") or len(rate_hits))
    model_api_error_hits = log_info.get("model_api_error_hits", [])
    model_api_error_count = int(log_info.get("model_api_error_count") or len(model_api_error_hits))
    retrieval_retry_hits = log_info.get("retrieval_retry_hits", [])
    retrieval_retry_count = int_value(log_info.get("retrieval_retry_count") or len(retrieval_retry_hits))
    embedding_timeout_hits = log_info.get("embedding_timeout_hits", [])
    embedding_timeout_count = int(log_info.get("embedding_timeout_count") or len(embedding_timeout_hits))
    embedding_circuit_breaker_hits = log_info.get("embedding_circuit_breaker_hits", [])
    embedding_circuit_breaker_count = int(
        log_info.get("embedding_circuit_breaker_count") or len(embedding_circuit_breaker_hits)
    )
    clusters = ((analysis or {}).get("failure_clusters") or {}).get("clusters") or []
    attribution = ((analysis or {}).get("failure_attribution") or {})
    attribution_buckets = attribution.get("buckets") if isinstance(attribution.get("buckets"), list) else []
    dataset_path_text = config_value(config, "data", "dataset", fallback="")
    sample_filter = config_value(config, "sample", fallback="all")
    if sample_filter in {"", "-"}:
        sample_filter = "all"
    current_backend = report_backend(record, manifest, config, summary)
    dataset_path_for_integrity = Path(dataset_path_text).expanduser() if dataset_path_text and dataset_path_text != "-" else None
    import_integrity = latest_import_integrity(
        run_dir,
        config,
        backend=current_backend,
        data_path=dataset_path_for_integrity,
        sample=sample_filter,
    )
    graph_report_artifacts = maybe_export_echomemory_graph_report(
        run_dir,
        record,
        current_backend=current_backend,
        import_integrity=import_integrity,
    )
    import_log_info = (
        run_service.tail_file(Path(str(import_integrity.get("log_file"))))
        if import_integrity.get("log_file")
        else {}
    )
    import_rate_hit_count = int_value(import_log_info.get("rate_limit_count"))
    import_model_api_error_count = int_value(import_log_info.get("model_api_error_count"))
    import_retrieval_retry_count = int_value(import_log_info.get("retrieval_retry_count"))
    import_embedding_timeout_count = int_value(import_log_info.get("embedding_timeout_count"))
    import_embedding_circuit_breaker_count = int_value(import_log_info.get("embedding_circuit_breaker_count"))
    import_generic_failure_count = int_value(import_log_info.get("generic_failure_count"))
    qa_audit: dict[str, Any] = {}
    if output_path and output_path.exists() and output_path.suffix.lower() == ".csv":
        dataset_path = Path(dataset_path_text).expanduser() if dataset_path_text and dataset_path_text != "-" else None
        try:
            qa_audit = run_service.qa_diagnostics(output_path, dataset_path, sample_filter)
        except Exception as exc:
            qa_audit = {"error": str(exc), "input": str(output_path)}

    all_rows = csv_rows_limited(output_path, 50000) if output_path else []
    indexed_rows = list(enumerate(all_rows))
    wrong_examples = [(idx, row) for idx, row in indexed_rows if run_service.row_grade(row) == "WRONG"][:8]
    all_pending_rows = [row for row in all_rows if run_service.row_grade(row) == "UNSCORED"]
    pending_examples = [(idx, row) for idx, row in indexed_rows if run_service.row_grade(row) == "UNSCORED"][:8]
    correct_examples = [(idx, row) for idx, row in indexed_rows if run_service.row_grade(row) == "CORRECT"][:3]
    failed_examples = [
        (idx, row)
        for idx, row in indexed_rows
        if str(row.get("model_status") or "").lower() == "failed"
        or str(row.get("answer_status") or "").lower() == "failed"
        or str(row.get("health_status") or "").lower() == "api_error"
    ][:8]
    unknown_examples = [
        (idx, row)
        for idx, row in indexed_rows
        if str(row.get("model_status") or "").lower() != "failed"
        and (
            str(row.get("answer_status") or "").lower() == "empty_or_unknown"
            or str(row.get("response") or "").strip().lower() in {"unknown", ""}
        )
    ][:8]

    pending_csv = ""
    if output_path and all_pending_rows:
        try:
            pending_csv = run_service.export_pending_csv(output_path).get("output", "")
        except Exception:
            pending_csv = str(output_path.with_name(output_path.stem + ".pending_judge.csv"))

    category_lines = []
    for cat, counts in sorted((summary.get("categories") or {}).items(), key=lambda item: str(item[0])):
        graded = counts.get("CORRECT", 0) + counts.get("WRONG", 0)
        acc = f"{counts.get('CORRECT', 0) / graded * 100:.1f}%" if graded else "待 Judge"
        category_lines.append(
            f"| C{cat or '-'} | {counts.get('CORRECT', 0)} | {counts.get('WRONG', 0)} | {counts.get('UNSCORED', 0)} | {acc} |"
        )

    summary_json = summary.get("summary_json") or {}
    exact_match = summary.get("exact_match_reference")
    if exact_match is None:
        exact_match = summary_json.get("exact_match_rate")
    exact_match_text = "n/a" if exact_match is None else f"{exact_match * 100:.1f}%"
    official_metric_name = str(summary.get("official_metric") or summary_json.get("official_metric") or "").strip()
    official_metric_scope = str(summary.get("official_metric_scope") or summary_json.get("official_metric_scope") or "").strip()
    official_score_raw = summary.get("official_score") if summary.get("official_score") is not None else summary_json.get("official_score")
    official_score_text = format_metric_percent(official_score_raw)
    official_answer_em_raw = summary.get("official_answer_em") if summary.get("official_answer_em") is not None else summary_json.get("official_answer_em")
    official_answer_f1_raw = summary.get("official_answer_f1") if summary.get("official_answer_f1") is not None else summary_json.get("official_answer_f1")
    official_answer_em_text = format_metric_percent(official_answer_em_raw)
    official_answer_f1_text = format_metric_percent(official_answer_f1_raw)
    avg_memory_injection_time_text = format_metric_seconds(summary.get("avg_memory_injection_time_s", summary_json.get("avg_memory_injection_time_s")))
    total_memory_injection_time_text = format_metric_seconds(summary.get("total_memory_injection_time_s", summary_json.get("total_memory_injection_time_s")))
    avg_qa_time_text = format_metric_seconds(summary.get("avg_qa_time_s", summary_json.get("avg_qa_time_s", summary.get("avg_time"))))
    total_qa_time_text = format_metric_seconds(summary.get("total_qa_time_s", summary_json.get("total_qa_time_s")))
    avg_end_to_end_time_text = format_metric_seconds(summary.get("avg_end_to_end_time_s", summary_json.get("avg_end_to_end_time_s")))
    total_end_to_end_time_text = format_metric_seconds(summary.get("total_end_to_end_time_s", summary_json.get("total_end_to_end_time_s")))

    context_items = context_composition(summary, summary_json, config, current_backend)
    context_lines = [f"- {label}: `{value}`" for label, value in context_items]
    source_mix_items = [
        ("Long-term memory hits", summary_value(summary, summary_json, "memory_hit_total")),
        ("Avg memory hits/question", summary_value(summary, summary_json, "avg_memory_hit_count", "avg_retrieval_count")),
        ("Session archive fallback hits", summary_value(summary, summary_json, "archive_fallback_total")),
        ("Avg session fallback/question", summary_value(summary, summary_json, "avg_archive_fallback_count")),
        ("Rows with retrieval errors", summary_value(summary, summary_json, "retrieval_error_rows")),
        ("Health counts", counts_text(summary.get("health_counts") or summary_json.get("health_counts"))),
    ]
    token_items = [
        ("Answer prompt tokens", summary_value(summary, summary_json, "answer_prompt_tokens")),
        ("Answer completion tokens", summary_value(summary, summary_json, "answer_completion_tokens")),
        ("Answer total tokens", summary_value(summary, summary_json, "answer_total_tokens")),
        ("Retrieval tokens est", summary_value(summary, summary_json, "retrieval_tokens_est_total", "retrieval_tokens_est")),
        ("Total injection tokens est", summary_value(summary, summary_json, "total_injection_tokens_est", "retrieval_tokens_est_total", "retrieval_tokens_est")),
        ("Avg injection tokens est", summary_value(summary, summary_json, "avg_injection_tokens_est", "avg_retrieval_tokens_est")),
        ("Total memory injection time", summary_value(summary, summary_json, "total_memory_injection_time_s")),
        ("Avg memory injection time", summary_value(summary, summary_json, "avg_memory_injection_time_s")),
        ("Total QA time", summary_value(summary, summary_json, "total_qa_time_s")),
        ("Avg QA time", summary_value(summary, summary_json, "avg_qa_time_s", "avg_time")),
        ("Total end-to-end time", summary_value(summary, summary_json, "total_end_to_end_time_s")),
        ("Avg end-to-end time", summary_value(summary, summary_json, "avg_end_to_end_time_s")),
    ]
    model_health_items = [
        ("Model OK rows", summary_value(summary, summary_json, "model_ok_count")),
        ("Model failed rows", summary_value(summary, summary_json, "model_failed_count")),
        ("Answer failed rows", summary_value(summary, summary_json, "answer_failed_count")),
        ("Answer empty/unknown rows", summary_value(summary, summary_json, "answer_empty_or_unknown_count")),
        ("Unknown response rows", summary_value(summary, summary_json, "unknown_response_count")),
        ("Empty response rows", summary_value(summary, summary_json, "empty_response_count")),
        ("Rows with retries", summary_value(summary, summary_json, "rows_with_model_retries")),
        ("Retry total", summary_value(summary, summary_json, "model_retry_total")),
        ("Model status counts", counts_text(summary.get("model_status_counts") or summary_json.get("model_status_counts"))),
        ("Answer health counts", counts_text(summary.get("health_counts") or summary_json.get("health_counts"))),
    ]
    import_label = str(import_integrity.get("memory_label") or ("EchoMemory" if current_backend == "echomemory" else "OpenViking"))
    import_checks = import_integrity.get("checks") if isinstance(import_integrity.get("checks"), list) else []
    import_check_failures = [
        item
        for item in import_checks
        if isinstance(item, dict) and (item.get("level") == "fail" or (item.get("ok") is False and item.get("level") != "warn"))
    ]
    import_check_warnings = [
        item
        for item in import_checks
        if isinstance(item, dict) and item.get("level") == "warn" and item.get("ok") is False
    ]
    import_extracted_display = (
        import_integrity.get("extracted_memories")
        or import_integrity.get("summary_extracted_memories")
        or "-"
    )
    import_integrity_items = [
        ("Memory backend", import_label),
        ("Import run", display_value(import_integrity.get("run_id"))),
        ("Import status", display_value(import_integrity.get("status"))),
        ("Verification", str(import_integrity.get("reason") or f"checks fail={len(import_check_failures)} warn={len(import_check_warnings)}")),
        ("Complete samples", display_ratio(import_integrity.get("complete_samples"), import_integrity.get("samples"))),
        ("Incomplete samples", display_value(import_integrity.get("incomplete_samples"))),
        ("Submitted messages", display_ratio(import_integrity.get("submitted_messages"), import_integrity.get("expected_messages"))),
        ("Sessions", display_value(import_integrity.get("session_count"))),
        ("Pending after commit", display_value(import_integrity.get("pending_after_commit_total"))),
        ("Extracted / atoms", display_value(import_extracted_display)),
        ("Artifact files", display_value(import_integrity.get("memory_files"))),
        ("Memory root", str(import_integrity.get("memory_dir") or import_integrity.get("memory_root") or import_integrity.get("account_path") or "-")),
        ("Import summary", display_value(import_integrity.get("output_file"))),
        ("Import log", display_value(import_integrity.get("log_file"))),
    ]
    qa_audit_status = "not available"
    if qa_audit:
        audit_has_error = bool(qa_audit.get("error"))
        missing_count = int(qa_audit.get("missing_questions_count") or 0)
        duplicate_count = int(qa_audit.get("duplicate_question_ids_count") or 0)
        unexpected_count = int(qa_audit.get("unexpected_question_ids_count") or 0)
        retryable_count = int(qa_audit.get("retryable_failed_questions") or 0)
        expected_count = qa_audit.get("expected_questions")
        unique_count = qa_audit.get("unique_question_ids")
        rows_count = qa_audit.get("rows")
        qa_audit_status = (
            "pass"
            if not audit_has_error
            and expected_count not in (None, "")
            and int(expected_count or 0) == int(unique_count or 0)
            and int(rows_count or 0) >= int(expected_count or 0)
            and missing_count == 0
            and duplicate_count == 0
            and unexpected_count == 0
            and retryable_count == 0
            else "needs attention"
        )
    qa_audit_items = [
        ("Audit status", qa_audit_status),
        ("Dataset expected questions", str(qa_audit.get("expected_questions", "-"))),
        ("CSV rows", str(qa_audit.get("rows", summary.get("rows", "-")))),
        ("Unique question ids", str(qa_audit.get("unique_question_ids", "-"))),
        ("Missing questions", str(qa_audit.get("missing_questions_count", "-"))),
        ("Duplicate question ids", str(qa_audit.get("duplicate_question_ids_count", "-"))),
        ("Unexpected question ids", str(qa_audit.get("unexpected_question_ids_count", "-"))),
        ("Retryable failed questions", str(qa_audit.get("retryable_failed_questions", "-"))),
        ("Retryable failed rows", str(qa_audit.get("retryable_failed_rows", "-"))),
        ("Pending Judge rows", str((summary.get("result_counts") or {}).get("UNSCORED", 0))),
        ("Graded rows", str(summary.get("graded", "-"))),
        ("Diagnostics error", str(qa_audit.get("error") or "-")),
    ]
    pending_judge_rows = int_value((summary.get("result_counts") or {}).get("UNSCORED", 0))
    rows_total = int_value(summary.get("rows", summary_json.get("count", 0)))
    graded_rows = int_value(summary.get("graded", 0))
    model_failed_rows = int_value(
        summary.get("model_failed_count", summary_json.get("model_failed_count", (summary.get("model_status_counts") or {}).get("failed", 0)))
    )
    answer_failed_rows = int_value(summary.get("answer_failed_count", summary_json.get("answer_failed_count", 0)))
    retryable_failed_rows = int_value(qa_audit.get("retryable_failed_rows", summary.get("retryable_failed_rows", 0)))
    model_retry_rows = int_value(summary.get("rows_with_model_retries", summary_json.get("rows_with_model_retries", 0)))
    model_retry_total = int_value(summary.get("model_retry_total", summary_json.get("model_retry_total", 0)))
    qa_log_failure_count = int_value(log_info.get("generic_failure_count"))
    qa_log_warning_count = (
        rate_hit_count
        + model_api_error_count
        + retrieval_retry_count
        + embedding_timeout_count
        + embedding_circuit_breaker_count
    )
    import_log_warning_count = (
        import_rate_hit_count
        + import_model_api_error_count
        + import_retrieval_retry_count
        + import_embedding_timeout_count
        + import_embedding_circuit_breaker_count
    )

    prompt_mode_text = summary_value(summary, summary_json, "prompt_mode", fallback=config_value(config, "prompt_mode"))
    top_k_text = native_top_k_label(config_value(config, "top_k", "local_agent_top_k"), prompt_mode_text)
    vikingbot_prompt_text = summary_value(summary, summary_json, "vikingbot_prompt_aligned")
    if current_backend == "echomemory":
        backend_url_label = "EchoMemory SDK Root"
        backend_url_value = config_value(config, "echomem_root", "echomemory_root", fallback="-")
        tool_loop_label = "Memory tool loop"
        tool_set_label = "Memory tool set"
        content_read_label = "Memory content read"
        tool_loop_text = summary_value(
            summary,
            summary_json,
            "memory_tool_loop_enabled",
            "openviking_tool_loop_enabled",
            fallback=config_value(config, "memory_tool_loop_enabled", "openviking_tool_loop"),
        )
        tool_set_text = summary_value(
            summary,
            summary_json,
            "memory_tool_set",
            "openviking_tool_set",
            fallback=config_value(config, "memory_tool_set", "openviking_tool_set"),
        )
        content_read_text = summary_value(
            summary,
            summary_json,
            "memory_content_read_enabled",
            "openviking_content_read_enabled",
            fallback=config_value(config, "memory_content_read_enabled", "read_openviking_content"),
        )
    else:
        backend_url_label = "OpenViking URL"
        backend_url_value = config_value(config, "openviking_url", "server_url")
        tool_loop_label = "OpenViking tool loop"
        tool_set_label = "OpenViking tool set"
        content_read_label = "OpenViking content read"
        tool_loop_text = summary_value(
            summary,
            summary_json,
            "openviking_tool_loop_enabled",
            fallback=config_value(config, "openviking_tool_loop"),
        )
        tool_set_text = summary_value(summary, summary_json, "openviking_tool_set", fallback=config_value(config, "openviking_tool_set"))
        content_read_text = summary_value(
            summary,
            summary_json,
            "openviking_content_read_enabled",
            fallback=config_value(config, "read_openviking_content"),
        )
    max_iterations_text = summary_value(summary, summary_json, "max_iterations", fallback=config_value(config, "max_iterations"))
    group_chat_text = summary_value(summary, summary_json, "group_chat", fallback=config_value(config, "group_chat"))
    memory_user_strategy_text = summary_value(summary, summary_json, "memory_user_strategy")
    initial_agent_memory_text = summary_value(summary, summary_json, "initial_agent_memory_enabled", fallback=config_value(config, "initial_agent_memory"))
    identity_mode_text = summary_value(summary, summary_json, "vikingbot_identity_mode", fallback=config_value(config, "vikingbot_identity_mode"))
    vikingbot_channel_text = summary_value(summary, summary_json, "vikingbot_channel")
    vikingbot_workspace_text = summary_value(summary, summary_json, "vikingbot_workspace", fallback=config_value(config, "vikingbot_workspace"))
    query_expansion_text = summary_value(summary, summary_json, "query_expansion_enabled")
    lexical_fallback_text = summary_value(summary, summary_json, "lexical_fallback_enabled")
    archive_fallback_text = summary_value(summary, summary_json, "archive_fallback_enabled")
    memory_file_read_text = summary_value(summary, summary_json, "memory_file_read_enabled")
    raw_turn_fallback_text = summary_value(summary, summary_json, "raw_turn_fallback")
    vikingbot_alignment = vikingbot_alignment_gate(
        prompt_mode=prompt_mode_text,
        prompt_aligned=vikingbot_prompt_text,
        tool_loop=tool_loop_text,
        tool_set=tool_set_text,
        content_read=content_read_text,
        group_chat=group_chat_text,
        identity_mode=identity_mode_text,
        channel=vikingbot_channel_text,
        memory_user_strategy=memory_user_strategy_text,
        initial_agent_memory=initial_agent_memory_text,
        query_expansion=query_expansion_text,
        lexical_fallback=lexical_fallback_text,
        archive_fallback=archive_fallback_text,
        memory_file_read=memory_file_read_text,
        raw_turn_fallback=raw_turn_fallback_text,
    )
    vikingbot_status = str(vikingbot_alignment.get("status") or "warn")
    vikingbot_mode = str(vikingbot_alignment.get("mode") or "unknown")

    run_completion_status = "pass" if str(record.get("status") or "").lower() == "succeeded" else "fail"
    import_missing = not bool(import_integrity)
    import_expected = int_value(import_integrity.get("expected_messages"))
    import_submitted = int_value(import_integrity.get("submitted_messages"))
    import_samples = int_value(import_integrity.get("samples"))
    import_complete_samples = int_value(import_integrity.get("complete_samples"))
    import_incomplete_samples = int_value(import_integrity.get("incomplete_samples"))
    import_pending_after = int_value(import_integrity.get("pending_after_commit_total"))
    import_extracted = int_value(import_integrity.get("extracted_memories"))
    import_memory_files = int_value(import_integrity.get("memory_files"))
    import_status_text = str(import_integrity.get("status") or "").lower()
    import_verification_level = str(import_integrity.get("verification_level") or "").lower()
    import_integrity_status = (
        "fail"
        if import_missing
        or import_check_failures
        or import_status_text == "incomplete"
        or (import_samples and import_complete_samples != import_samples)
        or import_incomplete_samples
        or (import_expected and import_submitted != import_expected)
        or import_pending_after
        or import_generic_failure_count
        else "warn"
        if import_check_warnings
        or import_verification_level == "warn"
        or import_status_text in {"warning", "not_available"}
        or (current_backend == "openviking" and (import_extracted == 0 or import_memory_files == 0))
        or import_log_warning_count
        else "pass"
    )
    if import_status_text == "not_available" or import_integrity.get("reason"):
        import_integrity_detail = f"{import_label}; {display_value(import_integrity.get('reason'), '导入完整性不可验证')}"
    else:
        import_integrity_detail = (
            f"{import_label}; samples={display_ratio(import_integrity.get('complete_samples'), import_integrity.get('samples'))}; "
            f"messages={display_ratio(import_integrity.get('submitted_messages'), import_integrity.get('expected_messages'))}; "
            f"pending_after_commit={display_value(import_integrity.get('pending_after_commit_total'))}; "
            f"extracted={display_value(import_extracted_display)}; artifacts={display_value(import_integrity.get('memory_files'))}"
        )
    qa_coverage_status = "pass" if qa_audit_status == "pass" else ("warn" if qa_audit_status == "not available" else "fail")
    judge_completion_status = "pass" if pending_judge_rows == 0 and (not rows_total or graded_rows >= rows_total) else "fail"
    model_final_status = "pass" if model_failed_rows == 0 and answer_failed_rows == 0 and retryable_failed_rows == 0 else "fail"
    retry_warning_status = "warn" if qa_log_warning_count or model_retry_rows or model_retry_total else "pass"
    qa_log_status = "fail" if qa_log_failure_count else retry_warning_status
    gate_items = [
        ("Run completion", run_completion_status, str(record.get("status") or "-")),
        (
            "VikingBot parameter alignment",
            vikingbot_status,
            (
                f"mode={vikingbot_mode}; retrieval_limit={top_k_text}; prompt_mode={prompt_mode_text}; prompt_aligned={vikingbot_prompt_text}; "
                f"identity={identity_mode_text}; group_chat={group_chat_text}; memory_users={memory_user_strategy_text}; "
                f"initial_agent_memory={initial_agent_memory_text}; "
                f"tool_loop={tool_loop_text}; tool_set={tool_set_text}; content_read={content_read_text}; "
                f"max_iterations={max_iterations_text}; query_expansion={query_expansion_text}; "
                f"lexical={lexical_fallback_text}; archive={archive_fallback_text}; memory_file={memory_file_read_text}; "
                f"raw_turn_fallback={raw_turn_fallback_text}"
            ),
        ),
        (
            "Memory import integrity",
            import_integrity_status,
            import_integrity_detail,
        ),
        (
            "Import log failures",
            "fail" if import_generic_failure_count else ("warn" if import_log_warning_count else "pass"),
            f"fatal={import_generic_failure_count}; retry_or_embedding_warnings={import_log_warning_count}",
        ),
        ("QA coverage", qa_coverage_status, f"audit={qa_audit_status}; rows={rows_total}; pending_judge={pending_judge_rows}; retryable={retryable_failed_rows}"),
        ("Judge completion", judge_completion_status, f"graded={graded_rows}/{rows_total}; pending={pending_judge_rows}"),
        ("Model final failures", model_final_status, f"model_failed={model_failed_rows}; answer_failed={answer_failed_rows}; retryable_rows={retryable_failed_rows}"),
        (
            "QA log warnings",
            qa_log_status,
            f"fatal={qa_log_failure_count}; model_api_retries={model_api_error_count}; retrieval_retries={retrieval_retry_count}; rate_limits={rate_hit_count}; embedding_timeouts={embedding_timeout_count}; retry_rows={model_retry_rows}; retry_total={model_retry_total}",
        ),
    ]
    gate_overall_status = gate_status([status for _, status, _ in gate_items])
    gate_verdict = {
        "pass": "trusted_full_chain",
        "warn": "trusted_with_warnings",
        "fail": "untrusted_chain",
    }[gate_overall_status]
    backend_name = backend_display_name(current_backend)
    dataset_name = dataset_display_name(config_value(config, "dataset_format"), config_value(config, "data", "dataset"))
    sample_text = config_value(config, "sample", fallback="-")
    question_filter_text = config_value(config, "questions", fallback="")
    expected_questions_text = str(qa_audit.get("expected_questions") or summary_json.get("count") or rows_total or "-")
    result_rows_text = str(summary.get("rows") or summary_json.get("count") or "-")
    correct_rows = int_value(summary.get("correct", summary_json.get("correct", 0)))
    wrong_rows = int_value(summary.get("wrong", summary_json.get("wrong", 0)))
    formal_score = format_score(summary)
    tool_names_value = summary_value(summary, summary_json, "memory_tool_names")
    if tool_names_value == "-":
        raw_tool_names = config.get("memory_tool_names")
        tool_names_value = ", ".join(raw_tool_names) if isinstance(raw_tool_names, list) else display_value(raw_tool_names)
    retrieval_config_text = (
        f"top_k={top_k_text} · score_threshold={first_metric(summary, summary_json, config, 'score_threshold', 'initial_score_threshold')} · "
        f"tool_search_limit={first_metric(summary, summary_json, config, 'tool_search_limit')} · tool_min_score={first_metric(summary, summary_json, config, 'tool_min_score')}"
    )
    backend_note = (
        "本次使用 EchoMemory 作为记忆后端，Agent 调用 memory_search / memory_read_many 等 memory_* 工具读取 EchoMemory 记忆；不是 OpenViking 记忆结果。"
        if current_backend == "echomemory"
        else "本次使用 OpenViking 作为记忆后端，Agent 调用 OpenViking 兼容工具读取 user/agent memory。"
    )
    overview_items = [
        ("评测数据集", dataset_name),
        ("样本范围", sample_text if sample_text not in {"", "-"} else "全部样本"),
        ("题目数量", f"{expected_questions_text} 题"),
        ("结果行数", f"{result_rows_text} 行"),
        ("记忆后端", backend_name),
        ("后端路由", summary_value(summary, summary_json, "alignment_backend_route", "backend_route", fallback=config_value(config, "alignment_backend_route"))),
        ("账户", config_value(config, "account", "ov_account")),
        ("记忆空间", config_value(config, "workspace", "openviking_workspace", "ov_workspace")),
        ("答案模型", config_value(config, "answer_model", "model")),
        ("Judge", f"{graded_rows}/{result_rows_text} 已判 · {correct_rows} 对 / {wrong_rows} 错"),
        ("准确率", formal_score),
        ("官方指标", f"{official_metric_name or '-'} · {official_score_text}"),
        ("答案 EM / F1", f"{official_answer_em_text} / {official_answer_f1_text}"),
        ("平均注入 / QA / 端到端", f"{avg_memory_injection_time_text} / {avg_qa_time_text} / {avg_end_to_end_time_text}"),
        ("总注入 / QA / 端到端", f"{total_memory_injection_time_text} / {total_qa_time_text} / {total_end_to_end_time_text}"),
        ("检索参数", retrieval_config_text),
    ]
    overview_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in overview_items
    )
    report_scope_text = (
        f"{dataset_name} {sample_text if sample_text not in {'', '-'} else '全部样本'} · "
        f"{expected_questions_text} 题 · {backend_name} 后端"
    )
    import_integrity_note = (
        "自动匹配同一 workspace/account 的最近一次 EchoMemory import，检查 LoCoMo conversation 是否完整写入并可检索。"
        if current_backend == "echomemory"
        else "自动匹配同一 workspace/account 的最近一次 OpenViking import，检查 LoCoMo conversation 是否完整提交并 commit。"
    )
    attribution_total = int_value(attribution.get("total"), rows_total)
    attribution_problem_rows = int_value(attribution.get("problem_rows"))
    attribution_correct_rows = int_value(attribution.get("correct_rows"))
    attribution_retryable_rows = int_value(attribution.get("retryable_rows"))
    attribution_owner_counts = attribution.get("owner_counts") if isinstance(attribution.get("owner_counts"), dict) else {}
    attribution_kind_counts = attribution.get("question_kind_counts") if isinstance(attribution.get("question_kind_counts"), dict) else {}
    attribution_mode_counts = attribution.get("mode_counts") if isinstance(attribution.get("mode_counts"), dict) else {}
    attribution_severity_counts = attribution.get("severity_counts") if isinstance(attribution.get("severity_counts"), dict) else {}
    top_bucket = attribution_buckets[0] if attribution_buckets else {}
    top_owner_items = sorted(
        ((str(key), int_value(value)) for key, value in attribution_owner_counts.items() if str(key) != "none"),
        key=lambda item: (-item[1], item[0]),
    )
    top_owner_text = attribution_owner_label(top_owner_items[0][0]) if top_owner_items else "-"
    top_bucket_count = int_value(top_bucket.get("count"))
    top_bucket_text = (
        f"{attribution_mode_label(top_bucket.get('mode'))} · {top_bucket_count} 行 · {percent_text(top_bucket_count, attribution_total)}"
        if top_bucket
        else "-"
    )
    mode_count = lambda key: int_value(attribution_mode_counts.get(key))  # noqa: E731
    pending_attribution_rows = mode_count("pending_judge")
    model_api_error_rows = mode_count("model_api_error")
    retrieval_error_attr_rows = mode_count("retrieval_error")
    no_relevant_memory_rows = mode_count("no_relevant_memory")
    unknown_with_evidence_rows = mode_count("unknown_with_evidence")
    time_reasoning_rows = mode_count("time_reasoning_error")
    list_aggregation_rows = mode_count("list_aggregation_error")
    evidence_mismatch_rows = mode_count("evidence_mismatch")
    semantic_mismatch_rows = mode_count("semantic_mismatch")

    diagnostic_actions: list[tuple[str, str, str, str]] = []

    def add_diagnostic(status: str, title: str, detail: str, metric: str) -> None:
        diagnostic_actions.append((status, title, detail, metric))

    if import_integrity_status == "fail":
        add_diagnostic(
            "bad",
            "先查记忆注入完整性",
            "导入样本、消息数、commit pending 或 import 日志存在失败，当前准确率不能代表真实记忆能力。",
            f"samples={import_complete_samples}/{import_samples} · messages={import_submitted}/{import_expected}",
        )
    if model_api_error_rows or model_final_status == "fail":
        add_diagnostic(
            "bad",
            "先重跑模型/API 异常题",
            "限流、超时、空响应或 answer failed 属于执行链路问题，建议先重跑这些题再讨论准确率。",
            f"model/API={model_api_error_rows} · retryable={attribution_retryable_rows}",
        )
    if retrieval_error_attr_rows or no_relevant_memory_rows:
        add_diagnostic(
            "bad" if retrieval_error_attr_rows else "warn",
            "检查检索与 workspace/account",
            "检索异常或未召回可用记忆通常和导入空间、account 隔离、top-k、query 或后端索引状态有关。",
            f"retrieval_error={retrieval_error_attr_rows} · no_memory={no_relevant_memory_rows}",
        )
    if unknown_with_evidence_rows:
        add_diagnostic(
            "warn",
            "有证据但回答 Unknown",
            "Relevant memory 已经返回，但模型仍回避回答，优先看 prompt、证据排序、回答约束和上下文截断。",
            f"unknown_with_evidence={unknown_with_evidence_rows}",
        )
    if pending_judge_rows or pending_attribution_rows:
        add_diagnostic(
            "warn",
            "补跑 Judge",
            "待 Judge 不应显示为 0% 准确率，也不能和 WRONG 混在一起统计。",
            f"pending={max(pending_judge_rows, pending_attribution_rows)}",
        )
    if time_reasoning_rows:
        add_diagnostic(
            "warn",
            "单独回归时间题",
            "时间题需要同时保留 event time、current date 和相对时间原文，适合做专项回归集。",
            f"time_errors={time_reasoning_rows}",
        )
    if list_aggregation_rows:
        add_diagnostic(
            "warn",
            "检查多条记忆聚合",
            "列表/聚合题需要合并多段证据，容易因为证据截断或排序导致遗漏。",
            f"list_errors={list_aggregation_rows}",
        )
    if vikingbot_status != "pass":
        add_diagnostic(
            "warn",
            "确认 VikingBot 对齐参数",
            "prompt、工具循环、group chat、initial memory 或 fallback 开关未完全对齐时，不宜和原生 VikingBot 直接比准确率。",
            f"alignment={vikingbot_status}",
        )
    if not diagnostic_actions and attribution_total:
        add_diagnostic(
            "ok",
            "当前优先看真实错题",
            "执行链路、Judge 和导入门禁没有突出阻断，下一步可直接分析语义错配、证据错配和具体题型。",
            f"wrong_like={evidence_mismatch_rows + semantic_mismatch_rows}",
        )
    elif not diagnostic_actions:
        add_diagnostic("warn", "等待结果 CSV", "当前报告缺少可分析行，先确认 QA 是否完成并生成结果 CSV。", "rows=0")

    diagnostic_overall_status = "fail" if any(item[0] == "bad" for item in diagnostic_actions) else ("warn" if any(item[0] == "warn" for item in diagnostic_actions) else "pass")
    diagnostic_overall_class = {
        "pass": "diagnostic-pass",
        "warn": "diagnostic-warn",
        "fail": "diagnostic-fail",
    }[diagnostic_overall_status]
    first_fix_title = diagnostic_actions[0][1]
    first_fix_detail = diagnostic_actions[0][2]
    diagnostic_lines = [
        f"- First fix: `{first_fix_title}` · {first_fix_detail}",
        f"- Top owner: `{top_owner_text}`",
        f"- Top failure bucket: `{top_bucket_text}`",
        f"- Retryable rows: `{attribution_retryable_rows}` · {percent_text(attribution_retryable_rows, attribution_total)}",
        f"- Pending Judge rows: `{pending_judge_rows}` · {percent_text(pending_judge_rows, rows_total)}",
        f"- Unknown with evidence: `{unknown_with_evidence_rows}`",
        f"- Retrieval/no-memory rows: `{retrieval_error_attr_rows + no_relevant_memory_rows}`",
        f"- Time reasoning rows: `{time_reasoning_rows}`",
    ]
    gate_lines = [
        f"- Gate status: `{gate_overall_status}`",
        f"- Gate verdict: `{gate_verdict}`",
        *[f"- {label}: `{status}` · {detail}" for label, status, detail in gate_items],
    ]
    source_mix_lines = [f"- {label}: `{value}`" for label, value in source_mix_items]
    token_lines = [f"- {label}: `{value}`" for label, value in token_items]
    model_health_lines = [f"- {label}: `{value}`" for label, value in model_health_items]
    import_integrity_lines = [f"- {label}: `{value}`" for label, value in import_integrity_items]
    qa_audit_lines = [f"- {label}: `{value}`" for label, value in qa_audit_items]
    attribution_lines = [
        f"- Total rows: `{attribution.get('total', '-')}`",
        f"- Problem rows: `{attribution.get('problem_rows', '-')}`",
        f"- Correct rows: `{attribution.get('correct_rows', '-')}`",
        f"- Retryable rows: `{attribution.get('retryable_rows', '-')}`",
        f"- Owners: `{counts_text(attribution.get('owner_counts'))}`",
        f"- Question kinds: `{counts_text(attribution.get('question_kind_counts'))}`",
        f"- Modes: `{counts_text(attribution.get('mode_counts'))}`",
    ]
    command_text = safe_command_text(manifest.get("command") or config_snapshot.get("command") or "")

    lines = [
        f"# {record.get('name') or record.get('id')}",
        "",
        "## Summary",
        "",
        f"- Run ID: `{record.get('id')}`",
        f"- Status: `{record.get('status')}`",
        f"- Kind: `{record.get('kind')}`",
        f"- Agent type: `{agent_type}`",
        f"- Created: `{record.get('created_at')}`",
        f"- Duration: `{duration_s if duration_s is not None else '-'}` seconds",
        f"- Formal Judge score: `{format_score(summary)}`",
        f"- Official metric: `{official_metric_name or '-'} · {official_score_text}`",
        f"- Official metric scope: `{official_metric_scope or '-'}`",
        f"- HotpotQA answer EM / F1: `{official_answer_em_text}` / `{official_answer_f1_text}`",
        f"- Rows: `{summary.get('rows', '-')}`",
        f"- Graded: `{summary.get('graded', '-')}`",
        f"- Pending Judge: `{(summary.get('result_counts') or {}).get('UNSCORED', 0)}`",
        f"- Exact match reference: `{summary.get('simple_correct', summary_json.get('exact_match_count', '-'))}/{summary.get('rows', summary_json.get('count', '-'))} · {exact_match_text}`",
        f"- Avg time/question: `{round(summary.get('avg_time'), 2) if summary.get('avg_time') is not None else '-'}` seconds",
        f"- Avg memory injection time: `{avg_memory_injection_time_text}`",
        f"- Total memory injection time: `{total_memory_injection_time_text}`",
        f"- Avg QA time: `{avg_qa_time_text}`",
        f"- Total QA time: `{total_qa_time_text}`",
        f"- Avg end-to-end time: `{avg_end_to_end_time_text}`",
        f"- Total end-to-end time: `{total_end_to_end_time_text}`",
        "",
        "## 本次评测说明",
        "",
        f"- 评测数据集：`{dataset_name}`",
        f"- 样本范围：`{sample_text if sample_text not in {'', '-'} else '全部样本'}`",
        f"- 题目数量：`{expected_questions_text}` 题",
        f"- 结果行数：`{result_rows_text}` 行",
        f"- 记忆后端：`{backend_name}`",
        f"- 后端路由：`{summary_value(summary, summary_json, 'alignment_backend_route', 'backend_route', fallback=config_value(config, 'alignment_backend_route'))}`",
        f"- 账户：`{config_value(config, 'account', 'ov_account')}`",
        f"- 记忆空间：`{config_value(config, 'workspace', 'openviking_workspace', 'ov_workspace')}`",
        f"- Agent / 答案模型：`{agent_type}` / `{config_value(config, 'answer_model', 'model')}`",
        f"- Judge：`{graded_rows}/{result_rows_text}` 已判，`{correct_rows}` 对 / `{wrong_rows}` 错",
        f"- 准确率：`{formal_score}`",
        f"- 检索参数：`{retrieval_config_text}`",
        f"- 工具：`{tool_names_value}`",
        f"- 说明：{backend_note}",
        "",
        "## What To Fix First",
        "",
        *diagnostic_lines,
        "",
        "## End-to-End Health Gate",
        "",
        *gate_lines,
        "",
        "## Config Snapshot",
        "",
        f"- Config hash: `{manifest.get('config_hash') or record.get('config_hash') or '-'}`",
        f"- Account: `{config_value(config, 'account', 'ov_account')}`",
        f"- Workspace: `{config_value(config, 'openviking_workspace', 'ov_workspace', 'workspace')}`",
        f"- User / Agent: `{config_value(config, 'ov_user_id', 'user_id')}` / `{config_value(config, 'ov_agent_id', 'agent_id')}`",
        f"- Dataset: `{config_value(config, 'data', 'dataset')}`",
        f"- Sample / Questions: `{config_value(config, 'sample')}` / `{config_value(config, 'questions')}`",
        f"- Answer model: `{config_value(config, 'answer_model', 'model')}`",
        f"- Answer base URL: `{config_value(config, 'answer_base_url')}`",
        f"- Judge model: `{config_value(config, 'judge_model')}`",
        f"- Judge base URL: `{config_value(config, 'judge_base_url')}`",
        f"- Embedding model: `{config_value(config, 'embedding_model', 'embed_model')}`",
        f"- {backend_url_label}: `{backend_url_value}`",
        f"- Retrieval limit: `{top_k_text}`",
        f"- Prompt mode: `{prompt_mode_text}`",
        f"- {tool_loop_label}: `{tool_loop_text}`",
        f"- {tool_set_label}: `{tool_set_text}`",
        f"- {content_read_label}: `{content_read_text}`",
        f"- Max iterations: `{max_iterations_text}`",
        "",
        "## Context Composition",
        "",
        *context_lines,
        "",
        "## Import Integrity",
        "",
        *import_integrity_lines,
        "",
        "## QA Coverage Audit",
        "",
        *qa_audit_lines,
        "",
        "## Context Source Mix",
        "",
        *source_mix_lines,
        "",
        "## Token Summary",
        "",
        *token_lines,
        "",
        "## Model And Retrieval Health",
        "",
        *model_health_lines,
        "",
        "## Reliability",
        "",
        f"- Rate-limit warnings: `{rate_hit_count}`",
        f"- Model/API retry warnings: `{model_api_error_count}`",
        f"- Retrieval retry warnings: `{retrieval_retry_count}`",
        f"- Embedding timeout warnings: `{embedding_timeout_count}`",
        f"- Embedding circuit-breaker warnings: `{embedding_circuit_breaker_count}`",
        f"- Model retry rows: `{summary.get('rows_with_model_retries', summary_json.get('rows_with_model_retries', '-'))}`",
        f"- Model retry total: `{summary.get('model_retry_total', summary_json.get('model_retry_total', '-'))}`",
        f"- Model failed rows: `{summary.get('model_failed_count', summary_json.get('model_failed_count', (summary.get('model_status_counts') or {}).get('failed', 0)))}`",
        f"- Answer failed rows: `{summary.get('answer_failed_count', summary_json.get('answer_failed_count', '-'))}`",
        f"- Answer empty/unknown rows: `{summary.get('answer_empty_or_unknown_count', summary_json.get('answer_empty_or_unknown_count', '-'))}`",
        f"- Unknown response rows: `{summary.get('unknown_response_count', summary_json.get('unknown_response_count', '-'))}`",
        f"- Empty response rows: `{summary.get('empty_response_count', summary_json.get('empty_response_count', '-'))}`",
        "- Total injection tokens est: `n/a`",
        "- Avg injection tokens est: `n/a`",
        "",
        "## Failure Attribution",
        "",
        *attribution_lines,
        "",
    ]

    action_items = attribution.get("action_items") if isinstance(attribution.get("action_items"), list) else []
    if action_items:
        lines += ["### Suggested Actions", ""]
        for item in action_items[:8]:
            lines.append(f"- {item}")
        lines.append("")
    if attribution_buckets:
        lines += ["### Attribution Buckets", ""]
        for bucket in attribution_buckets[:12]:
            lines.append(
                f"- {bucket.get('label') or bucket.get('mode')}: `{bucket.get('count', 0)}` rows · owner `{bucket.get('owner', '-')}` · retryable `{bucket.get('retryable', False)}`"
            )
            reason = compact_text(bucket.get("reason"), 360)
            if reason:
                lines.append(f"  - {reason}")
            for example in (bucket.get("examples") or [])[:2]:
                lines.append(
                    f"  - `{example.get('question_id') or example.get('row_index')}` · {compact_text(example.get('question'), 220)}"
                )
        lines.append("")
    else:
        lines += ["- No attribution buckets available.", ""]

    lines += [
        "## Artifacts",
        "",
        f"- Output CSV: `{output_file or '-'}`",
        f"- Pending CSV: `{pending_csv or '-'}`",
        f"- Wrong brief CSV: `{(analysis or {}).get('wrong_questions_brief') or '-'}`",
        f"- Manifest: `{record.get('manifest_file') or '-'}`",
        f"- Config snapshot: `{manifest.get('config_snapshot_file') or '-'}`",
        f"- Log: `{record.get('log_file') or '-'}`",
        "",
        "## Category Breakdown",
        "",
        "| Category | Correct | Wrong | Pending | Accuracy |",
        "| --- | ---: | ---: | ---: | ---: |",
        *(category_lines or ["| - | 0 | 0 | 0 | - |"]),
        "",
        "## Failure Clusters",
        "",
    ]

    if clusters:
        for cluster in clusters[:12]:
            lines.append(f"- {cluster.get('label')}: {cluster.get('count')} cases")
    else:
        lines.append("- No failure clusters available.")

    lines += ["", "## Log Diagnostics", ""]
    if rate_hit_count:
        lines.append(f"- Rate-limit / quota / throttle hits: `{rate_hit_count}`")
        for hit in rate_hits[-8:]:
            lines.append(f"  - {compact_text(hit, 520)}")
    else:
        lines.append("- No rate-limit or quota warnings detected in the full log scan.")
    if model_api_error_count:
        lines.append(f"- Model API retry/error hits: `{model_api_error_count}`")
        for hit in model_api_error_hits[-8:]:
            lines.append(f"  - {compact_text(hit, 520)}")
    else:
        lines.append("- No model API retry lines detected in the full log scan.")
    if retrieval_retry_count:
        lines.append(f"- Retrieval retry hits: `{retrieval_retry_count}`")
        for hit in retrieval_retry_hits[-8:]:
            lines.append(f"  - {compact_text(hit, 520)}")
    else:
        lines.append("- No retrieval retry lines detected in the full log scan.")
    if embedding_timeout_count:
        lines.append(f"- Embedding timeout/requeue hits: `{embedding_timeout_count}`")
        for hit in embedding_timeout_hits[-8:]:
            lines.append(f"  - {compact_text(hit, 520)}")
    if embedding_circuit_breaker_count:
        lines.append(f"- Embedding circuit-breaker hits: `{embedding_circuit_breaker_count}`")
        for hit in embedding_circuit_breaker_hits[-8:]:
            lines.append(f"  - {compact_text(hit, 520)}")

    lines += ["", "## Wrong Examples", ""]
    if wrong_examples:
        for index, (row_index, row) in enumerate(wrong_examples, 1):
            evidence = first_json_list(row.get("relevant_memory") or "", 2)
            lines += [
                f"### {index}. `{row.get('question_id') or '-'}` · `{row.get('sample_id') or '-'}` · C{row.get('category') or '-'}",
                "",
                f"- CSV row index: `{row_index}`",
                f"- Detail query: `path={output_file}&index={row_index}`",
                f"- Question: {compact_text(row.get('question'), 360)}",
                f"- Gold: {compact_text(row.get('answer'), 360)}",
                f"- Agent Response: {compact_text(row.get('response'), 520)}",
                f"- Judge/Reasoning: {compact_text(row.get('reasoning'), 520)}",
            ]
            if evidence:
                lines.append("- Evidence:")
                for item in evidence:
                    label = item.get("uri") or item.get("time") or f"rank {item.get('rank', '-')}"
                    score = item.get("score", "-")
                    text = item.get("abstract") or item.get("text") or item.get("content") or ""
                    lines.append(f"  - `{compact_text(label, 120)}` score `{score}`: {compact_text(text, 420)}")
            lines.append("")
    else:
        lines.append("- No wrong examples in preview.")

    lines += [
        "",
        "## Pending Judge",
        "",
        f"- Pending rows: `{len(all_pending_rows)}`",
        f"- Pending CSV: `{pending_csv or '-'}`",
        "",
        "### Pending Examples",
        "",
    ]
    if pending_examples:
        for row_index, row in pending_examples:
            lines += [
                f"- `{row.get('question_id') or '-'}` · `{row.get('sample_id') or '-'}` · C{row.get('category') or '-'}`",
                f"  - CSV row index: `{row_index}`",
                f"  - Detail query: `path={output_file}&index={row_index}`",
                f"  - Question: {compact_text(row.get('question'), 300)}",
                f"  - Gold: {compact_text(row.get('answer'), 220)}",
                f"  - Agent Response: {compact_text(row.get('response'), 300)}",
            ]
    else:
        lines.append("- No pending Judge rows.")

    lines += ["", "## Correct Examples", ""]
    if correct_examples:
        for row_index, row in correct_examples:
            lines.append(f"- row `{row_index}` · `{row.get('question_id') or '-'}` {compact_text(row.get('question'), 260)}")
    else:
        lines.append("- No correct rows available.")
    lines += ["", "## Model/API Failed Examples", ""]
    if failed_examples:
        for row_index, row in failed_examples:
            lines += [
                f"- row `{row_index}` · `{row.get('question_id') or '-'}` · `{row.get('sample_id') or '-'}` · C{row.get('category') or '-'}",
                f"  - Health / retrieval / answer / model: `{row.get('health_status') or '-'}` / `{row.get('retrieval_status') or '-'}` / `{row.get('answer_status') or '-'}` / `{row.get('model_status') or '-'}`",
                f"  - Error: `{compact_text(row.get('model_error') or row.get('reasoning') or '-', 360)}`",
                f"  - Question: {compact_text(row.get('question'), 300)}",
            ]
    else:
        lines.append("- No model/API failed rows in current CSV.")
    lines += ["", "## Unknown / Empty Answer Examples", ""]
    if unknown_examples:
        for row_index, row in unknown_examples:
            lines += [
                f"- row `{row_index}` · `{row.get('question_id') or '-'}` · `{row.get('sample_id') or '-'}` · C{row.get('category') or '-'}",
                f"  - Health / answer / model: `{row.get('health_status') or '-'}` / `{row.get('answer_status') or '-'}` / `{row.get('model_status') or '-'}`",
                f"  - Question: {compact_text(row.get('question'), 300)}",
                f"  - Agent Response: {compact_text(row.get('response'), 240)}",
            ]
    else:
        lines.append("- No unknown/empty answer rows in current CSV.")
    lines += [
        "",
        "## Command",
        "",
        "```bash",
        command_text,
        "```",
        "",
    ]

    report_text = "\n".join(lines)
    report_path = run_dir / "report.md"
    report_path.write_text(report_text, encoding="utf-8")

    wrong_cluster_lines = []
    if clusters:
        for cluster in clusters[:10]:
            examples_text = " | ".join(
                compact_text((example or {}).get("question") or "", 90)
                for example in (cluster.get("examples") or [])[:3]
            )
            wrong_cluster_lines.append(
                f"<article class='cluster-card'><strong>{html.escape(str(cluster.get('label') or '-'))}</strong>"
                f"<span>{html.escape(str(cluster.get('count') or 0))} 题</span>"
                f"<p>{html.escape(examples_text or '无示例')}</p></article>"
            )

    wrong_examples_html = []
    for row_index, row in wrong_examples[:6]:
        wrong_examples_html.append(
            f"<article class='example-card wrong'><strong>{html.escape(str(row.get('question_id') or '-'))} · C{html.escape(str(row.get('category') or '-'))}</strong>"
            f"<p><b>Q</b> {html.escape(compact_text(row.get('question'), 220))}</p>"
            f"<p><b>Gold</b> {html.escape(compact_text(row.get('answer'), 220))}</p>"
            f"<p><b>Agent</b> {html.escape(compact_text(row.get('response'), 260))}</p>"
            f"<p><b>Judge</b> {html.escape(compact_text(row.get('reasoning'), 260))}</p>"
            f"<small>row {row_index}</small></article>"
        )

    pending_examples_html = []
    for row_index, row in pending_examples[:6]:
        pending_examples_html.append(
            f"<article class='example-card pending'><strong>{html.escape(str(row.get('question_id') or '-'))} · C{html.escape(str(row.get('category') or '-'))}</strong>"
            f"<p><b>Q</b> {html.escape(compact_text(row.get('question'), 220))}</p>"
            f"<p><b>Agent</b> {html.escape(compact_text(row.get('response'), 260))}</p>"
            f"<small>row {row_index}</small></article>"
        )
    failed_examples_html = []
    for row_index, row in failed_examples[:6]:
        failed_examples_html.append(
            f"<article class='example-card failed'><strong>{html.escape(str(row.get('question_id') or '-'))} · C{html.escape(str(row.get('category') or '-'))}</strong>"
            f"<p><b>Q</b> {html.escape(compact_text(row.get('question'), 220))}</p>"
            f"<p><b>Status</b> {html.escape(compact_text('/'.join([row.get('health_status') or '-', row.get('retrieval_status') or '-', row.get('answer_status') or '-', row.get('model_status') or '-']), 180))}</p>"
            f"<p><b>Error</b> {html.escape(compact_text(row.get('model_error') or row.get('reasoning') or '-', 260))}</p>"
            f"<small>row {row_index}</small></article>"
        )
    unknown_examples_html = []
    for row_index, row in unknown_examples[:6]:
        unknown_examples_html.append(
            f"<article class='example-card unknown'><strong>{html.escape(str(row.get('question_id') or '-'))} · C{html.escape(str(row.get('category') or '-'))}</strong>"
            f"<p><b>Q</b> {html.escape(compact_text(row.get('question'), 220))}</p>"
            f"<p><b>Agent</b> {html.escape(compact_text(row.get('response'), 260))}</p>"
            f"<small>row {row_index} · {html.escape(str(row.get('answer_status') or '-'))}</small></article>"
        )

    diagnostic_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in [
            ("第一优先", first_fix_title),
            ("主要责任侧", top_owner_text),
            ("最大问题桶", top_bucket_text),
            ("可重跑行", f"{attribution_retryable_rows} · {percent_text(attribution_retryable_rows, attribution_total)}"),
            ("待 Judge", f"{pending_judge_rows} · {percent_text(pending_judge_rows, rows_total)}"),
            ("有证据但 Unknown", str(unknown_with_evidence_rows)),
            ("检索/无记忆", str(retrieval_error_attr_rows + no_relevant_memory_rows)),
            ("时间题错误", str(time_reasoning_rows)),
        ]
    )
    diagnostic_action_html = "".join(
        f"<article class='diagnostic-action {html.escape(status)}'>"
        f"<div><span>{'P0' if status == 'bad' else 'P1' if status == 'warn' else 'OK'}</span><strong>{html.escape(title)}</strong></div>"
        f"<p>{html.escape(detail)}</p>"
        f"<code>{html.escape(metric)}</code>"
        "</article>"
        for status, title, detail, metric in diagnostic_actions[:8]
    )

    config_html = "".join(
        f"<article class='kpi'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in [
            ("Memory Backend", backend_name),
            ("Dataset", f"{dataset_name} / {sample_text if sample_text not in {'', '-'} else '全部'}"),
            ("Duration", str(duration_s if duration_s is not None else "-")),
            ("Answer Model", config_value(config, "answer_model", "model")),
            ("Judge Model", config_value(config, "judge_model")),
            ("Context", summary_value(summary, summary_json, "retrieval_mode")),
        ]
    )
    config_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><code>{html.escape(value)}</code></article>"
        for label, value in [
            ("Memory Backend", backend_name),
            ("Config hash", str(manifest.get("config_hash") or record.get("config_hash") or "-")),
            ("Account", config_value(config, "account", "ov_account")),
            ("Workspace", config_value(config, "openviking_workspace", "ov_workspace", "workspace")),
            ("User / Agent", f"{config_value(config, 'ov_user_id', 'user_id')} / {config_value(config, 'ov_agent_id', 'agent_id')}"),
            ("VikingBot identity", summary_value(summary, summary_json, "vikingbot_identity_mode", fallback=config_value(config, "vikingbot_identity_mode"))),
            ("VikingBot Channel", summary_value(summary, summary_json, "vikingbot_channel")),
            ("Group chat", summary_value(summary, summary_json, "group_chat", fallback=config_value(config, "group_chat"))),
            ("Initial agent memory", summary_value(summary, summary_json, "initial_agent_memory_enabled", fallback=config_value(config, "initial_agent_memory"))),
            ("Dataset", config_value(config, "data", "dataset")),
            ("Sample", config_value(config, "sample")),
            ("Questions", config_value(config, "questions")),
            (backend_url_label, backend_url_value),
            ("VikingBot Workspace", summary_value(summary, summary_json, "vikingbot_workspace", fallback=config_value(config, "vikingbot_workspace"))),
            ("VikingBot Bootstrap", summary_value(summary, summary_json, "vikingbot_bootstrap_files")),
            ("VikingBot Skills", summary_value(summary, summary_json, "vikingbot_skill_names")),
            ("Answer Base URL", config_value(config, "answer_base_url")),
            ("Answer Model", config_value(config, "answer_model", "model")),
            ("Judge Base URL", config_value(config, "judge_base_url")),
            ("Judge Model", config_value(config, "judge_model")),
            ("Embedding Model", config_value(config, "embedding_model", "embed_model")),
            ("Retrieval Limit", top_k_text),
            ("Prompt Mode", prompt_mode_text),
            (tool_loop_label, tool_loop_text),
            (tool_set_label, tool_set_text),
            (content_read_label, content_read_text),
            ("Max Iterations", max_iterations_text),
            ("Model retries", config_value(config, "model_retries")),
            ("Timeout seconds", config_value(config, "timeout_s")),
        ]
    )
    import_integrity_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><code>{html.escape(value)}</code></article>"
        for label, value in import_integrity_items
    )
    qa_audit_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><code>{html.escape(value)}</code></article>"
        for label, value in qa_audit_items
    )
    context_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in context_items
    )
    source_mix_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in source_mix_items
    )
    token_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in token_items
    )
    model_health_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in model_health_items
    )
    attribution_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>"
        for label, value in [
            ("总行数", str(attribution_total or "-")),
            ("问题行", f"{attribution_problem_rows} · {percent_text(attribution_problem_rows, attribution_total)}"),
            ("正确行", f"{attribution_correct_rows} · {percent_text(attribution_correct_rows, attribution_total)}"),
            ("可重跑", f"{attribution_retryable_rows} · {percent_text(attribution_retryable_rows, attribution_total)}"),
            ("严重级别", counts_text(attribution_severity_counts)),
            ("责任侧", " · ".join(f"{attribution_owner_label(key)}:{value}" for key, value in sorted(attribution_owner_counts.items())) or "-"),
            ("题型", " · ".join(f"{attribution_kind_label(key)}:{value}" for key, value in sorted(attribution_kind_counts.items())) or "-"),
            ("失败桶", " · ".join(f"{attribution_mode_label(key)}:{value}" for key, value in sorted(attribution_mode_counts.items())) or "-"),
        ]
    )
    attribution_action_html = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in (action_items[:8] if action_items else ["当前没有可操作建议。"])
    )
    attribution_bucket_html_parts = []
    for bucket in attribution_buckets[:10]:
        severity_class = attribution_severity_class(bucket.get("severity"))
        examples_html = []
        for example in (bucket.get("examples") or [])[:3]:
            evidence = example.get("evidence") if isinstance(example.get("evidence"), dict) else {}
            evidence_text = compact_text(evidence.get("content") or evidence.get("uri") or "", 280)
            examples_html.append(
                "<article class='attribution-example'>"
                f"<strong>{html.escape(str(example.get('question_id') or example.get('row_index') or '-'))} · {html.escape(str(example.get('sample_id') or '-'))} · C{html.escape(str(example.get('category') or '-'))}</strong>"
                f"<p><b>Q</b> {html.escape(compact_text(example.get('question'), 220))}</p>"
                f"<p><b>Gold</b> {html.escape(compact_text(example.get('gold'), 200))}</p>"
                f"<p><b>Agent</b> {html.escape(compact_text(example.get('response'), 240))}</p>"
                f"<p><b>Evidence</b> {html.escape(evidence_text or '无 evidence 摘要')}</p>"
                "</article>"
            )
        examples_block = "".join(examples_html) or "<p class='muted'>暂无样本。</p>"
        attribution_bucket_html_parts.append(
            f"<article class='attribution-bucket {severity_class}'>"
            "<div class='bucket-head'>"
            f"<div><span>{html.escape(attribution_mode_label(bucket.get('mode')))}</span><strong>{html.escape(str(bucket.get('label') or attribution_mode_label(bucket.get('mode'))))}</strong></div>"
            f"<code>{html.escape(str(bucket.get('count') or 0))} 行 · {html.escape(attribution_owner_label(bucket.get('owner')))} · {'可重跑' if bucket.get('retryable') else '需分析'}</code>"
            "</div>"
            f"<p>{html.escape(compact_text(bucket.get('reason'), 420))}</p>"
            f"<div class='attribution-examples'>{examples_block}</div>"
            "</article>"
        )
    attribution_bucket_html = "".join(attribution_bucket_html_parts) or "<p class='muted'>暂无错误归因桶；可能还没有结果 CSV 或尚未运行 Judge。</p>"
    gate_panel_class = {
        "pass": "gate-pass",
        "warn": "gate-warn",
        "fail": "gate-fail",
    }.get(gate_overall_status, "gate-warn")
    gate_cards_html = "".join(
        f"<article><span>{html.escape(label)}</span><code>{html.escape(status)} · {html.escape(detail)}</code></article>"
        for label, status, detail in gate_items
    )

    html_report = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(record.get('name') or record.get('id') or 'Run Report')}</title>
  <style>
    body {{ margin:0; font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#f6f8fb; color:#10233f; }}
    .page {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px 56px; }}
    h1,h2,h3,p {{ margin:0; }}
    .hero {{ display:grid; gap:10px; margin-bottom:24px; }}
    .hero p {{ color:#5b6b84; max-width:860px; }}
    .kpis {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; margin-bottom:24px; }}
    .kpi, .panel, .cluster-card, .example-card, .attribution-bucket, .attribution-example, .diagnostic-action {{ border:1px solid #dbe3ef; background:#fff; border-radius:8px; }}
    .kpi {{ padding:14px 16px; min-width:0; }}
    .kpi span {{ display:block; color:#667790; font-size:12px; font-weight:700; }}
    .kpi strong {{ display:block; font-size:20px; margin-top:4px; overflow-wrap:anywhere; }}
    .info-grid {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:10px; }}
    .info-grid article {{ min-width:0; padding:10px; border:1px solid #e0e7f1; border-radius:7px; background:#f9fbff; }}
    .info-grid span {{ display:block; color:#667790; font-size:11px; font-weight:800; text-transform:uppercase; }}
    .info-grid code, .info-grid strong {{ display:block; margin-top:5px; color:#10233f; font-size:12px; overflow-wrap:anywhere; white-space:normal; }}
    .grid {{ display:grid; grid-template-columns: 1.2fr 1fr; gap:16px; margin-bottom:16px; }}
    .panel {{ padding:16px; display:grid; gap:12px; }}
    .panel h2 {{ font-size:16px; }}
    .overview-panel {{ border-left:4px solid #2563eb; }}
    .overview-note {{ color:#334155; background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:10px 12px; }}
    .diagnostic-panel {{ border:1px solid #c7d7ee; background:linear-gradient(180deg,#fff,#f7fbff); }}
    .diagnostic-pass {{ border-left:4px solid #16a34a; }}
    .diagnostic-warn {{ border-left:4px solid #f59e0b; }}
    .diagnostic-fail {{ border-left:4px solid #b91c1c; background:#fffafa; }}
    .diagnostic-summary {{ display:grid; grid-template-columns: 1.2fr .8fr; gap:12px; align-items:stretch; }}
    .diagnostic-primary {{ padding:14px; border-radius:8px; background:#10233f; color:#fff; display:grid; gap:6px; }}
    .diagnostic-primary span {{ color:#b8c7db; font-size:11px; font-weight:800; text-transform:uppercase; }}
    .diagnostic-primary strong {{ font-size:19px; }}
    .diagnostic-primary p {{ color:#d9e3f2; }}
    .diagnostic-actions {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:10px; }}
    .diagnostic-action {{ min-width:0; padding:12px; display:grid; gap:8px; }}
    .diagnostic-action.bad {{ border-left:4px solid #b91c1c; background:#fff7f7; }}
    .diagnostic-action.warn {{ border-left:4px solid #f59e0b; }}
    .diagnostic-action.ok {{ border-left:4px solid #16a34a; background:#f7fff9; }}
    .diagnostic-action span {{ display:block; color:#667790; font-size:11px; font-weight:800; }}
    .diagnostic-action strong {{ display:block; margin-top:2px; }}
    .diagnostic-action code {{ justify-self:start; color:#334155; background:#f7f9fc; border:1px solid #e0e7f1; border-radius:999px; padding:3px 8px; white-space:normal; overflow-wrap:anywhere; }}
    .muted {{ color:#667790; }}
    .cluster-list, .example-list, .attribution-list, .attribution-examples {{ display:grid; gap:10px; }}
    .cluster-card, .example-card {{ padding:12px 14px; display:grid; gap:6px; }}
    .cluster-card span, .example-card small {{ color:#667790; }}
    .example-card.wrong {{ border-left:4px solid #ef4444; }}
    .example-card.pending {{ border-left:4px solid #f59e0b; }}
    .example-card.failed {{ border-left:4px solid #b91c1c; background:#fff7f7; }}
    .example-card.unknown {{ border-left:4px solid #7c3aed; background:#fbf9ff; }}
    .attribution-actions {{ margin:0; padding-left:20px; display:grid; gap:8px; color:#334155; }}
    .attribution-bucket {{ padding:14px; display:grid; gap:10px; }}
    .attribution-bucket.bad {{ border-left:4px solid #b91c1c; background:#fff7f7; }}
    .attribution-bucket.warn {{ border-left:4px solid #f59e0b; }}
    .attribution-bucket.ok {{ border-left:4px solid #16a34a; }}
    .bucket-head {{ display:flex; align-items:start; justify-content:space-between; gap:12px; }}
    .bucket-head span {{ display:block; color:#667790; font-size:11px; font-weight:800; text-transform:uppercase; }}
    .bucket-head strong {{ display:block; margin-top:3px; }}
    .bucket-head code {{ color:#334155; background:#f7f9fc; border:1px solid #e0e7f1; border-radius:999px; padding:4px 8px; white-space:nowrap; }}
    .attribution-example {{ padding:10px 12px; background:#fbfdff; display:grid; gap:5px; }}
    .audit-pass, .gate-pass {{ border-left:4px solid #16a34a; }}
    .audit-warn, .gate-warn {{ border-left:4px solid #f59e0b; }}
    .gate-fail {{ border-left:4px solid #b91c1c; background:#fff7f7; }}
    code, pre {{ font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; padding:14px; background:#f7f9fc; border-radius:8px; border:1px solid #e0e7f1; }}
    @media (max-width: 960px) {{ .kpis, .grid, .info-grid, .diagnostic-summary, .diagnostic-actions {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>{html.escape(record.get('name') or record.get('id') or 'Run Report')}</h1>
      <p>{html.escape(report_scope_text)}。报告包含配置快照、上下文组成、正式 Judge 状态、token 消耗、错题聚类、待 Judge 样本和链路日志摘要。</p>
    </section>
    <section class="kpis">
      <article class="kpi"><span>Agent</span><strong>{html.escape(agent_type)}</strong></article>
      <article class="kpi"><span>Rows</span><strong>{html.escape(str(summary.get('rows', '-')))}</strong></article>
      <article class="kpi"><span>Formal Judge</span><strong>{html.escape(format_score(summary))}</strong></article>
      <article class="kpi"><span>Pending</span><strong>{html.escape(str((summary.get('result_counts') or {}).get('UNSCORED', 0)))}</strong></article>
      <article class="kpi {gate_panel_class}"><span>Gate</span><strong>{html.escape(gate_overall_status)}</strong></article>
      {config_html}
    </section>
    <section class="panel overview-panel" style="margin-bottom:16px">
      <h2>本次评测说明</h2>
      <p class="overview-note">{html.escape(backend_note)}</p>
      <div class="info-grid">{overview_cards_html}</div>
    </section>
    <section class="panel diagnostic-panel {diagnostic_overall_class}" style="margin-bottom:16px">
      <h2>先修什么</h2>
      <div class="diagnostic-summary">
        <article class="diagnostic-primary">
          <span>First Fix</span>
          <strong>{html.escape(first_fix_title)}</strong>
          <p>{html.escape(first_fix_detail)}</p>
        </article>
        <div class="info-grid">{diagnostic_cards_html}</div>
      </div>
      <div class="diagnostic-actions">{diagnostic_action_html}</div>
    </section>
    <section class="panel {gate_panel_class}" style="margin-bottom:16px">
      <h2>全链路健康门禁</h2>
      <p class="muted">把运行状态、VikingBot 参数对齐、记忆注入、import/QA 日志、题目覆盖、Judge 和最终模型失败合成一个验收结论：{html.escape(gate_verdict)}。</p>
      <div class="info-grid">{gate_cards_html}</div>
    </section>
    <section class="panel" style="margin-bottom:16px">
      <h2>配置快照</h2>
      <p class="muted">这些字段来自 manifest、config_snapshot 和实际执行命令合并结果，用于复现实验。</p>
      <div class="info-grid">{config_cards_html}</div>
    </section>
	    <section class="panel" style="margin-bottom:16px">
	      <h2>记忆注入完整性</h2>
	      <p class="muted">{html.escape(import_integrity_note)}</p>
	      <div class="info-grid">{import_integrity_cards_html}</div>
	    </section>
	    <section class="panel {'audit-pass' if qa_audit_status == 'pass' else 'audit-warn'}" style="margin-bottom:16px">
	      <h2>QA 覆盖审计</h2>
	      <p class="muted">从数据集和结果 CSV 直接校验题目覆盖、缺失题、重复题、可恢复失败题和 Judge 完成度。</p>
	      <div class="info-grid">{qa_audit_cards_html}</div>
	    </section>
    <section class="panel" style="margin-bottom:16px">
      <h2>上下文组成</h2>
      <p class="muted">展示回答上下文中检索、fallback、evidence 和 token 的组成，方便判断分数是否来自正式记忆检索还是诊断兜底。</p>
      <div class="info-grid">{context_cards_html}</div>
    </section>
    <section class="grid">
      <article class="panel">
        <h2>Context Source Mix</h2>
        <p class="muted">区分长期 memory、session archive fallback、检索错误和回答健康状态。</p>
        <div class="info-grid">{source_mix_cards_html}</div>
      </article>
      <article class="panel">
        <h2>Token Summary</h2>
        <p class="muted">用于估算成本，也能判断上下文是否异常膨胀。</p>
        <div class="info-grid">{token_cards_html}</div>
      </article>
    </section>
    <section class="panel" style="margin-bottom:16px">
      <h2>Model And Retrieval Health</h2>
      <p class="muted">记录模型成功/失败、retry 和 answer health，定位限流、空答案、检索异常等问题。</p>
      <div class="info-grid">{model_health_cards_html}</div>
    </section>
    <section class="panel" style="margin-bottom:16px">
      <h2>错误归因</h2>
      <p class="muted">把 WRONG、待 Judge、模型/API 异常、检索异常和 Unknown 分开，避免把接口问题、待判分和真实能力问题混在同一个准确率里。</p>
      <div class="info-grid">{attribution_cards_html}</div>
      <ol class="attribution-actions">{attribution_action_html}</ol>
      <div class="attribution-list">{attribution_bucket_html}</div>
    </section>
    <section class="grid">
      <article class="panel">
        <h2>错题聚类</h2>
        <div class="cluster-list">{''.join(wrong_cluster_lines) or "<p class='muted'>暂无 WRONG 聚类，可能还未完成 Judge。</p>"}</div>
      </article>
	      <article class="panel">
	        <h2>日志摘要</h2>
        <p class="muted">Rate-limit hits: {rate_hit_count} · Model/API retry hits: {model_api_error_count} · Retrieval retry hits: {retrieval_retry_count} · Embedding timeout hits: {embedding_timeout_count}</p>
        <pre>{html.escape(chr(10).join((rate_hits + model_api_error_hits + retrieval_retry_hits + embedding_timeout_hits + embedding_circuit_breaker_hits)[-12:]) if (rate_hits or model_api_error_hits or retrieval_retry_hits or embedding_timeout_hits or embedding_circuit_breaker_hits) else 'No retry, rate-limit, or embedding warnings detected.')}</pre>
	      </article>
    </section>
    <section class="grid">
      <article class="panel">
        <h2>错题样本</h2>
        <div class="example-list">{''.join(wrong_examples_html) or "<p class='muted'>暂无 WRONG 样本。</p>"}</div>
      </article>
      <article class="panel">
        <h2>待 Judge 样本</h2>
        <div class="example-list">{''.join(pending_examples_html) or "<p class='muted'>当前没有 pending 样本。</p>"}</div>
      </article>
    </section>
    <section class="grid">
      <article class="panel">
        <h2>模型/API 失败样本</h2>
        <p class="muted">这些是重试后仍失败的真实执行问题，不能和模型保守回答 unknown 混为一谈。</p>
        <div class="example-list">{''.join(failed_examples_html) or "<p class='muted'>当前没有模型/API 失败样本。</p>"}</div>
      </article>
      <article class="panel">
        <h2>Unknown / Empty Answer 样本</h2>
        <p class="muted">这些通常表示模型拿到记忆后仍未能形成答案，需要和接口中断分开分析。</p>
        <div class="example-list">{''.join(unknown_examples_html) or "<p class='muted'>当前没有 unknown/empty 样本。</p>"}</div>
      </article>
    </section>
    <section class="panel">
      <h2>Markdown 原文</h2>
      <pre>{html.escape(report_text)}</pre>
    </section>
  </div>
</body>
</html>
"""
    report_html_path = run_dir / "report.html"
    report_html_path.write_text(html_report, encoding="utf-8")
    return {
        "report_file": str(report_path),
        "report_html_file": str(report_html_path),
        "text": report_text,
        **graph_report_artifacts,
    }
