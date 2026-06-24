from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .runs import agent_type_for, tail_file


SECRET_MARKERS = ("password", "token", "key", "secret")
SECRET_ARGS = {
    "--token",
    "--api-key",
    "--key",
    "--password",
    "--judge-api-key",
    "--answer-token",
    "--judge-token",
    "--openviking-api-key",
}


def _recent_log_lines(path: Path, limit_bytes: int = 256_000) -> list[str]:
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    read_size = min(size, limit_bytes)
    try:
        with path.open("rb") as f:
            if read_size < size:
                f.seek(size - read_size)
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def _read_json_file(path_value: str | Path | None) -> dict[str, Any]:
    path = Path(path_value) if path_value else None
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def redact_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    for key in list(redacted):
        if any(marker in key.lower() for marker in SECRET_MARKERS):
            redacted[key] = "******" if redacted[key] else ""
    return redacted


def redacted_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for item in command:
        if redact_next:
            redacted.append("******")
            redact_next = False
            continue
        redacted.append(item)
        if item in SECRET_ARGS:
            redact_next = True
    return redacted


def config_hash(redacted_config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(redacted_config, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def write_manifest(task: Any, payload: dict[str, Any], run_dir: Path) -> None:
    redacted_config = redact_manifest_payload(payload)
    cfg_hash = config_hash(redacted_config)
    agent_type = agent_type_for(str(getattr(task, "kind", "")), payload)
    public = task.public() if hasattr(task, "public") else {}
    command = getattr(task, "display_command", None) or redacted_command(list(getattr(task, "command", []) or []))
    manifest = {
        "schema_version": 1,
        "id": getattr(task, "id", ""),
        "name": getattr(task, "name", ""),
        "agent_type": agent_type,
        "experiment_version": payload.get("experiment_version") or "",
        "experiment_tags": payload.get("experiment_tags") or "",
        "experiment_notes": payload.get("experiment_notes") or "",
        "config_hash": cfg_hash,
        "kind": getattr(task, "kind", ""),
        "dataset_format": redacted_config.get("dataset_format") or "",
        "status": getattr(task, "status", ""),
        "created_at": datetime.fromtimestamp(getattr(task, "created_at", 0)).isoformat(timespec="seconds") if getattr(task, "created_at", None) else None,
        "started_at": datetime.fromtimestamp(getattr(task, "started_at", 0)).isoformat(timespec="seconds") if getattr(task, "started_at", None) else None,
        "ended_at": datetime.fromtimestamp(getattr(task, "ended_at", 0)).isoformat(timespec="seconds") if getattr(task, "ended_at", None) else None,
        "duration_s": public.get("duration"),
        "returncode": getattr(task, "returncode", None),
        "pid": getattr(task, "pid", None),
        "cwd": getattr(task, "cwd", ""),
        "command": command,
        "output_file": getattr(task, "output_file", ""),
        "log_file": getattr(task, "log_file", ""),
        "summary": getattr(task, "summary", {}),
        "error": getattr(task, "error", ""),
        "config": redacted_config,
        "config_snapshot_file": str(run_dir / "config_snapshot.json"),
    }
    snapshot = {
        "schema_version": 1,
        "run_id": getattr(task, "id", ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config_hash": cfg_hash,
        "agent_type": agent_type,
        "config": redacted_config,
        "command": manifest["command"],
            "artifacts": {
                "output_file": getattr(task, "output_file", ""),
                "log_file": getattr(task, "log_file", ""),
                "pid": getattr(task, "pid", None),
                "manifest_file": str(run_dir / "manifest.json"),
            },
        }
    (run_dir / "config_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        task.manifest_file = str(path)
    except Exception:
        pass


def task_log_diagnostics(task: Any) -> dict[str, Any]:
    """Collect task and OpenViking log warnings without exposing secrets."""
    logs: list[dict[str, Any]] = []
    task_log = getattr(task, "log_file", "")
    if task_log:
        logs.append({"name": "task", "path": Path(task_log)})
    meta = getattr(task, "meta", {}) if isinstance(getattr(task, "meta", {}), dict) else {}
    config = meta.get("config") if isinstance(meta, dict) else {}
    if isinstance(config, dict) and config.get("openviking_server_log"):
        logs.append({"name": "openviking", "path": Path(str(config["openviking_server_log"]))})

    rate_limit_hits: list[str] = []
    model_api_error_hits: list[str] = []
    retrieval_retry_hits: list[str] = []
    embedding_timeout_hits: list[str] = []
    embedding_circuit_breaker_hits: list[str] = []
    generic_failure_hits: list[str] = []
    rate_limit_count = 0
    model_api_error_count = 0
    retrieval_retry_count = 0
    embedding_timeout_count = 0
    embedding_circuit_breaker_count = 0
    generic_failure_count = 0
    token_usage = {
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "llm_total_tokens": 0,
        "llm_call_count": 0,
        "answer_prompt_tokens": 0,
        "answer_completion_tokens": 0,
        "answer_total_tokens": 0,
        "retrieval_tokens_est": 0,
        "retrieval_tokens_est_total": 0,
        "total_injection_tokens_est": 0,
        "import_llm_prompt_tokens": 0,
        "import_llm_completion_tokens": 0,
        "import_llm_total_tokens": 0,
        "import_embedding_total_tokens": 0,
        "import_total_tokens": 0,
        "search_intent_total_tokens": 0,
        "search_intent_call_count": 0,
        "embedding_total_tokens": 0,
        "embedding_call_count": 0,
    }
    call_site_usage: dict[str, dict[str, int]] = {}
    for item in logs:
        try:
            info = tail_file(item["path"], 24000)
        except Exception:
            continue
        prefix = f"{item['name']}: "
        rate_limit_hits.extend(prefix + line for line in info.get("rate_limit_hits", []))
        model_api_error_hits.extend(prefix + line for line in info.get("model_api_error_hits", []))
        retrieval_retry_hits.extend(prefix + line for line in info.get("retrieval_retry_hits", []))
        embedding_timeout_hits.extend(prefix + line for line in info.get("embedding_timeout_hits", []))
        embedding_circuit_breaker_hits.extend(prefix + line for line in info.get("embedding_circuit_breaker_hits", []))
        generic_failure_hits.extend(prefix + line for line in info.get("generic_failure_hits", []))
        rate_limit_count += int(info.get("rate_limit_count") or len(info.get("rate_limit_hits", [])))
        model_api_error_count += int(info.get("model_api_error_count") or len(info.get("model_api_error_hits", [])))
        retrieval_retry_count += int(info.get("retrieval_retry_count") or len(info.get("retrieval_retry_hits", [])))
        embedding_timeout_count += int(info.get("embedding_timeout_count") or len(info.get("embedding_timeout_hits", [])))
        embedding_circuit_breaker_count += int(
            info.get("embedding_circuit_breaker_count") or len(info.get("embedding_circuit_breaker_hits", []))
        )
        generic_failure_count += int(info.get("generic_failure_count") or len(info.get("generic_failure_hits", [])))
        usage = info.get("token_usage") if isinstance(info.get("token_usage"), dict) else {}
        for key in (
            "llm_input_tokens",
            "llm_output_tokens",
            "llm_total_tokens",
            "llm_call_count",
            "answer_prompt_tokens",
            "answer_completion_tokens",
            "answer_total_tokens",
            "retrieval_tokens_est",
            "retrieval_tokens_est_total",
            "total_injection_tokens_est",
            "import_llm_prompt_tokens",
            "import_llm_completion_tokens",
            "import_llm_total_tokens",
            "import_embedding_total_tokens",
            "import_total_tokens",
            "search_intent_total_tokens",
            "search_intent_call_count",
            "embedding_total_tokens",
            "embedding_call_count",
        ):
            token_usage[key] += int(usage.get(key) or 0)
        usage_sites = usage.get("call_sites") if isinstance(usage.get("call_sites"), dict) else {}
        for site_name, site_usage in usage_sites.items():
            if not isinstance(site_usage, dict):
                continue
            bucket = call_site_usage.setdefault(
                str(site_name),
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0},
            )
            for key in ("input_tokens", "output_tokens", "total_tokens", "call_count"):
                bucket[key] += int(site_usage.get(key) or 0)

    model_issue_hits = (
        rate_limit_hits
        + model_api_error_hits
        + retrieval_retry_hits
        + embedding_timeout_hits
        + embedding_circuit_breaker_hits
    )
    model_issue_count = (
        rate_limit_count
        + model_api_error_count
        + retrieval_retry_count
        + embedding_timeout_count
        + embedding_circuit_breaker_count
    )
    token_usage["call_sites"] = {key: value for key, value in call_site_usage.items() if any(int(v or 0) for v in value.values())}
    token_usage = {key: value for key, value in token_usage.items() if value not in (0, {}, None)}
    return {
        "rate_limit_hits": rate_limit_hits[-20:],
        "rate_limit_count": rate_limit_count,
        "model_api_error_hits": model_api_error_hits[-20:],
        "model_api_error_count": model_api_error_count,
        "retrieval_retry_hits": retrieval_retry_hits[-20:],
        "retrieval_retry_count": retrieval_retry_count,
        "embedding_timeout_hits": embedding_timeout_hits[-20:],
        "embedding_timeout_count": embedding_timeout_count,
        "embedding_circuit_breaker_hits": embedding_circuit_breaker_hits[-20:],
        "embedding_circuit_breaker_count": embedding_circuit_breaker_count,
        "generic_failure_hits": generic_failure_hits[-20:],
        "generic_failure_count": generic_failure_count,
        "model_issue_hits": model_issue_hits[-20:],
        "model_issue_count": model_issue_count,
        "token_usage": token_usage,
    }


def _locomo_import_totals(task: Any) -> dict[str, Any]:
    meta = getattr(task, "meta", {}) if isinstance(getattr(task, "meta", {}), dict) else {}
    config = meta.get("config") if isinstance(meta, dict) else {}
    if not isinstance(config, dict):
        return {}
    data_path = str(config.get("data") or "")
    sample_filter = str(config.get("sample") or "all")
    try:
        session_start = int(config.get("session_start") or config.get("sessionStart") or 0)
    except Exception:
        session_start = 0
    try:
        session_end = int(config.get("session_end") or config.get("sessionEnd") or 0)
    except Exception:
        session_end = 0
    if not data_path:
        return {}
    try:
        data = json.loads(Path(data_path).expanduser().read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    sample_count = 0
    session_count = 0
    message_count = 0
    by_sample: dict[str, dict[str, int]] = {}
    for index, sample in enumerate(data):
        if not isinstance(sample, dict):
            continue
        sample_id = str(sample.get("sample_id") or f"sample_{index}")
        if sample_filter not in {"", "all", str(index), sample_id}:
            continue
        sample_count += 1
        conv = sample.get("conversation") or {}
        sample_sessions = 0
        sample_messages = 0
        if isinstance(conv, dict):
            for key, value in conv.items():
                if re.fullmatch(r"session_\d+", str(key)) and isinstance(value, list):
                    number_match = re.search(r"(\d+)$", str(key))
                    number = int(number_match.group(1)) if number_match else 0
                    if session_start > 0 and number < session_start:
                        continue
                    if session_end > 0 and number > session_end:
                        continue
                    sample_sessions += 1
                    sample_messages += len(value)
        session_count += sample_sessions
        message_count += sample_messages
        by_sample[sample_id] = {"sessions": sample_sessions, "messages": sample_messages}
    return {
        "sample_count": sample_count,
        "session_count": session_count,
        "message_count": message_count,
        "by_sample": by_sample,
        "session_start": session_start,
        "session_end": session_end,
    }


def _compact_import_preview(text: Any, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _command_option(task: Any, option: str) -> str:
    command = list(getattr(task, "command", []) or [])
    flag = f"--{option}"
    try:
        index = command.index(flag)
    except ValueError:
        return ""
    if index + 1 >= len(command):
        return ""
    value = str(command[index + 1] or "")
    return "" if value.startswith("--") else value


def _task_config(task: Any) -> dict[str, Any]:
    meta = getattr(task, "meta", {}) if isinstance(getattr(task, "meta", {}), dict) else {}
    config = meta.get("config") if isinstance(meta, dict) else {}
    return config if isinstance(config, dict) else {}


def _csv_row_count(path_like: Any) -> int:
    path = Path(str(path_like or "")).expanduser()
    if not path.exists() or not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle))
    except Exception:
        return 0


def _locomo_turn_preview(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    if raw.get("text"):
        parts.append(str(raw["text"]))
    if raw.get("blip_caption"):
        parts.append(f"image: {raw['blip_caption']}")
    if raw.get("query"):
        parts.append(f"query: {raw['query']}")
    return _compact_import_preview(" ".join(parts))


def _sample_matches(sample: dict[str, Any], index: int, sample_key: str) -> bool:
    sample_id = str(sample.get("sample_id") or f"sample_{index}")
    value = str(sample_key or "").strip()
    return value in {"", "all", "*", sample_id, str(index)}


def _locomo_current_import_preview(
    task: Any,
    *,
    sample: str,
    session_label: str,
    message_index: int,
    message_total: int,
    phase: str,
) -> dict[str, Any] | None:
    config = _task_config(task)
    data_path = str(config.get("data") or config.get("dataset") or _command_option(task, "dataset") or "")
    if not data_path:
        return None
    try:
        data = json.loads(Path(data_path).expanduser().read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    sample_from_label = ""
    session_key = ""
    if "/" in session_label:
        sample_from_label, session_key = session_label.split("/", 1)
    else:
        session_key = session_label
    sample_key = sample or sample_from_label or str(config.get("sample") or _command_option(task, "sample") or "")
    sample_entry = next((item for index, item in enumerate(data) if isinstance(item, dict) and _sample_matches(item, index, sample_key)), None)
    if not isinstance(sample_entry, dict):
        sample_entry = next((item for item in data if isinstance(item, dict)), None)
    if not isinstance(sample_entry, dict):
        return None
    conv = sample_entry.get("conversation") or {}
    if not isinstance(conv, dict):
        return None
    if not session_key or not isinstance(conv.get(session_key), list):
        session_key = next((key for key, value in conv.items() if re.fullmatch(r"session_\d+", str(key)) and isinstance(value, list)), "")
    messages = conv.get(session_key) if session_key else []
    if not isinstance(messages, list) or not messages:
        return None
    total = int(message_total or len(messages))
    index = max(1, min(len(messages), int(message_index or total or len(messages))))
    raw = messages[index - 1] if isinstance(messages[index - 1], dict) else {}
    sample_id = str(sample_entry.get("sample_id") or sample_key or "")
    note = (
        "当前 session 已提交，正在归档/索引；展示该 session 最近提交的消息。"
        if str(phase or "").startswith("commit")
        else "当前正在写入长期记忆后端。"
    )
    return {
        "source": "dataset",
        "sample": sample_id,
        "session": f"{sample_id}/{session_key}" if session_key else session_label,
        "message_index": index,
        "message_total": total,
        "role": str(raw.get("speaker") or raw.get("role") or ""),
        "dia_id": str(raw.get("dia_id") or ""),
        "content": _locomo_turn_preview(raw),
        "note": note,
    }


def _locomo_session_progress_index(session_label: str, locomo_totals: dict[str, Any]) -> int:
    """Return the 1-based progress position for a LoCoMo session label."""
    match = re.search(r"(?:^|/)session_(\d+)$", str(session_label or ""))
    if not match:
        return 0
    number = int(match.group(1))
    try:
        session_start = int(locomo_totals.get("session_start") or 0)
    except Exception:
        session_start = 0
    if session_start > 0:
        return max(1, number - session_start + 1)
    return number


def _locomo_session_progress_display_current(
    completed_count: int,
    total_count: int,
    current_label: str,
    locomo_totals: dict[str, Any],
) -> int:
    """Return a conservative visible progress count for LoCoMo imports.

    Progress bars should track completed sessions only. The current in-flight
    session is described in text, not counted as finished.
    """
    total = max(int(total_count or 0), 0)
    completed = max(int(completed_count or 0), 0)
    current_index = _locomo_session_progress_index(current_label, locomo_totals)
    if total <= 0:
        return completed
    if current_index > 0:
        return min(max(completed, min(current_index - 1, total)), total)
    return min(completed, total)


def _stabilize_session_progress(
    task: Any,
    *,
    phase: str,
    unit: str,
    current: int,
    total: int,
    session_label: str,
    current_import: dict[str, Any] | None,
) -> tuple[int, int]:
    """Keep visible LoCoMo session progress monotonic across tail-log windows.

    `task_progress()` only inspects the tail of the log file. When the visible
    window no longer includes earlier `[commit] ... status=completed` lines, the
    reconstructed completed-session count can briefly fall back to zero even
    though the import has already advanced. Cache the best known session
    progress for the task and never let the rendered completed count move
    backwards while the same run is active.
    """
    if str(unit or "") != "sessions":
        return current, total

    try:
        meta = task.meta if isinstance(getattr(task, "meta", None), dict) else {}
    except Exception:
        return current, total
    if not isinstance(meta, dict):
        return current, total

    cache = meta.get("_session_progress_cache")
    if not isinstance(cache, dict):
        cache = {}

    phase_text = str(phase or "")
    session_text = str(session_label or (current_import or {}).get("session") or "")
    current_value = max(int(current or 0), 0)
    total_value = max(int(total or 0), 0)

    cached_current = max(int(cache.get("current") or 0), 0)
    cached_total = max(int(cache.get("total") or 0), 0)
    cached_session = str(cache.get("session_label") or "")

    if total_value <= 0:
        total_value = cached_total
    else:
        total_value = max(total_value, cached_total)

    stable_current = current_value
    if current_value < cached_current:
        same_or_newer_session = not session_text or not cached_session or session_text >= cached_session
        same_commit_phase = phase_text.startswith("commit") or str(cache.get("phase") or "").startswith("commit")
        if same_or_newer_session and same_commit_phase:
            stable_current = cached_current

    stable_current = min(max(stable_current, 0), total_value) if total_value > 0 else max(stable_current, 0)

    meta["_session_progress_cache"] = {
        "current": stable_current,
        "total": total_value,
        "phase": phase_text,
        "session_label": session_text,
    }
    task.meta = meta
    return stable_current, total_value


def _echomemory_import_summary_progress(task: Any) -> dict[str, Any]:
    if str(getattr(task, "kind", "") or "") != "echomemory_import":
        return {}
    summary = _read_json_file(getattr(task, "output_file", ""))
    status = str(summary.get("status") or "").strip()
    records = summary.get("records")
    if not isinstance(records, list) or not records:
        return {}
    record = next((item for item in records if isinstance(item, dict)), None)
    if not isinstance(record, dict):
        return {}
    try:
        done = int(record.get("progress_sessions_done") or record.get("session_count") or 0)
    except Exception:
        done = 0
    try:
        total = int(record.get("progress_sessions_total") or record.get("original_session_count") or 0)
    except Exception:
        total = 0
    if total <= 0:
        return {}
    session_records = record.get("session_records") if isinstance(record.get("session_records"), list) else []
    last_session = next(
        (
            str(item.get("session_key") or item.get("session_id") or "")
            for item in reversed(session_records)
            if isinstance(item, dict) and str(item.get("session_key") or item.get("session_id") or "").strip()
        ),
        "",
    )
    sample_id = str(record.get("sample_id") or summary.get("sample") or "")
    expected_messages = int(summary.get("expected_messages") or record.get("expected_messages") or 0)
    submitted_messages = int(summary.get("submitted_messages") or record.get("submitted_messages") or 0)
    if status == "ECHOMEMORY_IMPORT_FINALIZING":
        try:
            done = int(summary.get("finalizing_sessions_done") or 0)
        except Exception:
            done = 0
        try:
            total = int(summary.get("finalizing_sessions_total") or total or 0)
        except Exception:
            total = total
        current_session = str(summary.get("current_finalizing_session") or last_session or sample_id)
        return {
            "current": max(done, 0),
            "total": max(total, 0),
            "session_label": current_session,
            "sample": sample_id,
            "expected_messages": expected_messages,
            "submitted_messages": submitted_messages,
            "status": status,
            "phase": "commit:finalizing",
        }
    return {
        "current": max(done, 0),
        "total": max(total, 0),
        "session_label": f"{sample_id}/{last_session}" if sample_id and last_session and "/" not in last_session else (last_session or sample_id),
        "sample": sample_id,
        "expected_messages": expected_messages,
        "submitted_messages": submitted_messages,
        "status": status,
    }


def _compact_preview_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _latest_csv_row(path_value: str | Path | None) -> dict[str, Any]:
    path = Path(path_value) if path_value else None
    if not path or not path.exists():
        return {}
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return {}
    return rows[-1] if rows else {}


def task_progress(task: Any) -> dict[str, Any] | None:
    kind = str(getattr(task, "kind", "") or "")
    log_file = str(getattr(task, "log_file", "") or "")
    config = _task_config(task)
    dataset_format = str(config.get("dataset_format") or config.get("format") or "").strip().lower()
    if kind not in {
        "local_agent",
        "openviking_qa",
        "openviking_generic_qa",
        "openviking_import",
        "echomemory_qa",
        "echomemory_import",
        "echomemory_generic_qa",
        "echomemory_qa_retry_failed",
        "openviking_qa_retry_failed",
        "openviking_qa_retry_missing",
    } or not log_file:
        return None
    log_path = Path(log_file)
    if not log_path.exists():
        return None

    current = total = 0
    phase = ""
    detail = ""
    indeterminate = False
    unit = "items"
    commit_completed_count = 0
    commit_total_count = 0
    commit_expected_count = 0
    completed_samples: set[str] = set()
    current_sample = ""
    current_session_label = ""
    current_session_expected = 0
    current_session_added = 0
    current_sample_sessions = 0
    current_flush_attempt = 0
    current_flush_total = 0
    current_import: dict[str, Any] | None = None
    qa_preview: dict[str, Any] | None = None
    generic_item_current = 0
    generic_item_total = 0
    generic_item_stage = ""
    generic_answered_rows = 0
    awaiting_echomem_commit = False
    warnings: list[str] = []
    is_memory_import = kind in {"openviking_import", "echomemory_import"}
    is_generic_memory_qa = kind in {"openviking_generic_qa", "echomemory_generic_qa"}
    is_generic_question_benchmark = is_generic_memory_qa and dataset_format != "locomo"
    is_echomemory_postprocess_kind = kind in {
        "echomemory_import",
        "echomemory_qa",
        "echomemory_generic_qa",
        "echomemory_qa_retry_failed",
    }
    backend_label = "EchoMemory" if kind.startswith("echomemory") else "OpenViking"
    locomo_totals = _locomo_import_totals(task) if (is_memory_import or (is_generic_memory_qa and dataset_format == "locomo")) else {}
    summary_progress = _echomemory_import_summary_progress(task)
    try:
        for line in _recent_log_lines(log_path):
            match = re.search(r"\[(import|qa)\]\s+(\d+)/(\d+)\s+(.*)", line)
            if match:
                phase = match.group(1)
                current = int(match.group(2))
                total = int(match.group(3))
                unit = "questions" if phase == "qa" else "samples"
                detail = match.group(4).strip()
                if phase == "qa":
                    question_id = match.group(4).strip().split(" ", 1)[0] if match.group(4).strip() else ""
                    question_text = match.group(4).strip().split(" ", 1)[1] if " " in match.group(4).strip() else match.group(4).strip()
                    qa_preview = {
                        "question_id": question_id,
                        "question": _compact_preview_text(question_text or detail, 220),
                        "answer": "",
                        "source": "log",
                    }
                indeterminate = False
                if is_generic_question_benchmark:
                    generic_item_stage = phase
                    generic_item_current = int(match.group(2))
                    generic_item_total = int(match.group(3))

            sample_match = re.search(r"\[import\]\s+sample=([^\s]+).*?\bsessions=(\d+)", line)
            if sample_match:
                current_sample = sample_match.group(1)
                current_sample_sessions = int(sample_match.group(2))
                commit_expected_count = max(commit_expected_count, current_sample_sessions)

            session_start_match = re.search(r"\[import\]\s+session=\S+\s+label=([^\s]+)\s+expected_messages=(\d+)", line)
            if session_start_match:
                current_session_label = session_start_match.group(1)
                current_session_expected = int(session_start_match.group(2))
                current_session_added = 0
                awaiting_echomem_commit = False

            message_match = re.search(r"^\[message\]\s+({.*})\s*$", line)
            if message_match:
                try:
                    raw_message = json.loads(message_match.group(1))
                except Exception:
                    raw_message = {}
                if isinstance(raw_message, dict):
                    label = str(raw_message.get("label") or raw_message.get("session") or "")
                    current_import = {
                        "source": "log",
                        "sample": str(raw_message.get("sample") or current_sample or ""),
                        "session": label,
                        "message_index": int(raw_message.get("message_index") or raw_message.get("index") or 0),
                        "message_total": int(raw_message.get("message_total") or raw_message.get("total") or 0),
                        "role": str(raw_message.get("role_id") or raw_message.get("speaker") or raw_message.get("role") or ""),
                        "dia_id": str(raw_message.get("dia_id") or ""),
                        "content": _compact_import_preview(raw_message.get("content") or raw_message.get("text") or ""),
                        "note": "当前正在写入长期记忆后端。",
                    }

            ov_match = re.search(r"\[verify\].*added_total=(\d+)/(\d+)", line)
            if ov_match:
                phase = "import"
                current_session_added = int(ov_match.group(1))
                current_session_expected = int(ov_match.group(2))
                awaiting_echomem_commit = (
                    is_echomemory_postprocess_kind
                    and current_session_expected > 0
                    and current_session_added >= current_session_expected
                )
                if is_memory_import and locomo_totals.get("session_count"):
                    total = int(locomo_totals["session_count"])
                    current = _locomo_session_progress_display_current(
                        commit_completed_count,
                        total,
                        current_session_label,
                        locomo_totals,
                    )
                    unit = "sessions"
                    detail = (
                        f"{current_session_label or current_sample}: "
                        f"submitted {current_session_added}/{current_session_expected} messages to {backend_label}"
                    )
                else:
                    current = current_session_added
                    total = current_session_expected
                    unit = "messages"
                    detail = f"session messages submitted to {backend_label}"
                indeterminate = False

            if is_echomemory_postprocess_kind and "call_site=atom_extraction" in line:
                latency_match = re.search(r"latency=([0-9.]+)ms", line)
                latency_s = ""
                if latency_match:
                    try:
                        latency_s = f"{float(latency_match.group(1)) / 1000:.1f}s"
                    except Exception:
                        latency_s = ""
                phase = "commit:atom_extraction"
                if is_generic_question_benchmark:
                    total = max(total, generic_item_total, 1)
                    current = max(generic_item_current, 1)
                    unit = "questions"
                elif is_memory_import:
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, total, 1))
                    current = _locomo_session_progress_display_current(
                        commit_completed_count,
                        total,
                        current_session_label,
                        locomo_totals,
                    )
                    unit = "sessions"
                else:
                    if total <= 0:
                        total = max(current_session_expected, current_session_added, 1)
                    current = min(max(current_session_added, 1), total)
                    unit = "messages" if current_session_expected else unit or "items"
                detail = (
                    f"{current_session_label or current_sample or 'current sample'}: "
                    f"EchoMemory 正在抽取长期记忆原子"
                )
                if latency_s:
                    detail += f" · 最近一次 {latency_s}"
                indeterminate = True

            if is_echomemory_postprocess_kind and "Atomic extraction output appears truncated" in line:
                if "atom_extraction_truncated" not in warnings:
                    warnings.append("atom_extraction_truncated")
                phase = "commit:atom_truncated"
                if is_generic_question_benchmark:
                    total = max(total, generic_item_total, 1)
                    current = max(generic_item_current, 1)
                    unit = "questions"
                elif is_memory_import:
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, total, 1))
                    current = _locomo_session_progress_display_current(
                        commit_completed_count,
                        total,
                        current_session_label,
                        locomo_totals,
                    )
                    unit = "sessions"
                else:
                    if total <= 0:
                        total = max(current_session_expected, current_session_added, 1)
                    current = min(max(current_session_added, 1), total)
                    unit = "messages" if current_session_expected else unit or "items"
                detail = (
                    f"{current_session_label or current_sample or 'current sample'}: "
                    f"atom extraction 输出被截断，EchoMemory 仍在继续处理"
                )
                indeterminate = True

            generic_flush_match = re.search(
                r"\[flush\]\s+session=([^\s]+)\s+attempt=(\d+).*?(?:atom_pipeline_index=(\d+)/(\d+))?.*?(?:elapsed=([0-9.]+)s)?",
                line,
            )
            if is_echomemory_postprocess_kind and generic_flush_match:
                current_flush_attempt = int(generic_flush_match.group(2) or 0)
                current_flush_total = int(generic_flush_match.group(4) or generic_flush_match.group(2) or 0)
                atom_index = int(generic_flush_match.group(3) or 0)
                atom_total = int(generic_flush_match.group(4) or 0)
                elapsed_s = generic_flush_match.group(5) or ""
                phase = "commit:atom_flush"
                if is_generic_question_benchmark:
                    total = max(total, generic_item_total, 1)
                    current = max(generic_item_current, 1)
                    unit = "questions"
                elif is_memory_import:
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, total, 1))
                    current = _locomo_session_progress_display_current(
                        commit_completed_count,
                        total,
                        current_session_label,
                        locomo_totals,
                    )
                    unit = "sessions"
                else:
                    total = max(total, current_session_expected or total or 1)
                    current = min(current_session_added or total, total)
                    unit = "messages" if current_session_expected else (unit or "items")
                detail = (
                    f"{current_session_label or current_sample or generic_flush_match.group(1)}: "
                    f"EchoMemory 正在做 atom flush"
                )
                if atom_index and atom_total:
                    detail += f" ({atom_index}/{atom_total})"
                elif current_flush_attempt:
                    detail += f" (attempt {current_flush_attempt}"
                    if current_flush_total and current_flush_total != current_flush_attempt:
                        detail += f"/{current_flush_total}"
                    detail += ")"
                if elapsed_s:
                    detail += f" · {elapsed_s}s"
                indeterminate = True

            generic_commit_match = re.search(
                r"\[commit\]\s+(.+?)\s+complete=(True|False|true|false).*?(?:atom_pipeline_index=(\d+)/(\d+))?.*?(?:flush_complete=(True|False|true|false))?",
                line,
            )
            if is_echomemory_postprocess_kind and generic_commit_match:
                complete = generic_commit_match.group(2).lower() == "true"
                sample_label = generic_commit_match.group(1)
                atom_index = int(generic_commit_match.group(3) or 0)
                atom_total = int(generic_commit_match.group(4) or 0)
                flush_complete = str(generic_commit_match.group(5) or "").lower() == "true"
                phase = "commit:done" if complete else ("commit:partial" if flush_complete else "commit:indexing")
                if is_generic_question_benchmark:
                    total = max(total, generic_item_total, 1)
                    current = max(generic_item_current, 1)
                    unit = "questions"
                elif is_memory_import:
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, commit_total_count, 1))
                    current = _locomo_session_progress_display_current(
                        commit_completed_count + (1 if complete else 0),
                        total,
                        sample_label,
                        locomo_totals,
                    )
                    unit = "sessions"
                else:
                    if total <= 0:
                        total = max(current_session_expected, current_session_added, 1)
                    current = total if complete else min(max(current_session_added, 1), total)
                    unit = "messages" if current_session_expected else unit
                detail = f"{sample_label}: EchoMemory commit complete={complete}"
                if atom_index and atom_total:
                    detail += f" · atom {atom_index}/{atom_total}"
                if flush_complete and not complete:
                    detail += " · flush complete, waiting for remaining artifacts"
                indeterminate = not complete

            commit_accepted_match = re.search(r"\[commit\]\s+(.+?)\s+status=accepted\s+task_id=", line)
            if commit_accepted_match:
                commit_total_count += 1

            commit_completed_match = re.search(r"\[commit\]\s+task=.*status=completed", line)
            if commit_completed_match:
                commit_completed_count += 1

            commit_match = re.search(r"\[commit\]\s+task=.*status=(\w+)", line)
            if commit_match:
                phase = f"commit:{commit_match.group(1)}"
                if is_memory_import:
                    current = _locomo_session_progress_display_current(
                        commit_completed_count,
                        int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, commit_total_count, total, 1)),
                        current_session_label,
                        locomo_totals,
                    )
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, commit_total_count, total, 1))
                    unit = "sessions"
                    detail = f"{backend_label} extracting memories from {current_session_label or current_sample or 'sessions'}"
                    indeterminate = False
                elif commit_total_count > 0:
                    current = commit_completed_count
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, commit_total_count))
                    unit = "sessions"
                    detail = f"{backend_label} extracting memories from {current_session_label or current_sample or 'sessions'}"
                    indeterminate = False
                else:
                    current = total if total else 0
                    unit = "messages"
                    detail = f"{backend_label} commit task {commit_match.group(1)}"
                    indeterminate = True

            commit_start_match = re.search(r"\[commit\]\s+(.+?)\s+status=(\w+)\s+task_id=([^\s]+)", line)
            if commit_start_match:
                phase = f"commit:{commit_start_match.group(2)}"
                if is_memory_import:
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, commit_total_count, total, 1))
                    current = _locomo_session_progress_display_current(
                        commit_completed_count,
                        total,
                        commit_start_match.group(1),
                        locomo_totals,
                    )
                    unit = "sessions"
                    detail = f"Commit submitted for {commit_start_match.group(1)}"
                    indeterminate = False
                elif commit_total_count > 0:
                    current = commit_completed_count
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, commit_total_count))
                    unit = "sessions"
                    detail = f"Commit submitted for {commit_start_match.group(1)}"
                    indeterminate = False
                else:
                    current = total if total else 0
                    unit = "messages"
                    detail = f"Commit submitted for {commit_start_match.group(1)}"
                    indeterminate = True

            echomem_commit_match = re.search(r"\[commit\]\s+(.+?)\s+complete=(True|False|true|false)", line)
            if kind == "echomemory_import" and echomem_commit_match:
                complete = echomem_commit_match.group(2).lower() == "true"
                commit_total_count += 1
                if complete:
                    commit_completed_count += 1
                awaiting_echomem_commit = False
                phase = "commit:done" if complete else "commit:incomplete"
                total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, commit_total_count))
                current = _locomo_session_progress_display_current(
                    commit_completed_count,
                    total,
                    echomem_commit_match.group(1),
                    locomo_totals,
                )
                unit = "sessions"
                detail = f"EchoMemory commit for {echomem_commit_match.group(1)} complete={complete}"
                indeterminate = False

            done_match = re.search(r"\[done\]\s+sample=([^\s]+)\s+integrity=", line)
            if done_match and total:
                completed_samples.add(done_match.group(1))
                phase = "commit:done"
                if commit_total_count > 0:
                    current = commit_completed_count or commit_total_count
                    total = int(locomo_totals.get("session_count") or max(commit_expected_count, commit_total_count))
                    unit = "sessions"
                else:
                    current = total
                    unit = "messages"
                detail = f"{backend_label} commit returned; verify summary for extracted memories"
                indeterminate = False
    except OSError:
        return None

    if kind == "echomemory_import" and summary_progress:
        summary_phase = str(summary_progress.get("phase") or "")
        if summary_phase == "commit:finalizing":
            total = int(summary_progress.get("total") or 0)
            current = int(summary_progress.get("current") or 0)
            phase = summary_phase
            indeterminate = False
        else:
            total = max(int(total or 0), int(summary_progress.get("total") or 0))
            current = max(int(current or 0), int(summary_progress.get("current") or 0))
        if not current_session_label:
            current_session_label = str(summary_progress.get("session_label") or "")
        if not current_sample:
            current_sample = str(summary_progress.get("sample") or current_sample or "")
        if unit != "sessions":
            unit = "sessions"
        if summary_phase == "commit:finalizing":
            detail = (
                f"{current_session_label or current_sample or 'current sample'}: "
                f"正在 finalizing 完整性 {current}/{total}"
            )
        elif not phase or phase == "import":
            phase = "commit:indexing" if getattr(task, "status", "") == "running" else (phase or "commit")
        submitted = int(summary_progress.get("submitted_messages") or 0)
        expected = int(summary_progress.get("expected_messages") or 0)
        if summary_phase == "commit:finalizing":
            pass
        elif submitted > 0 and expected > 0:
            detail = (
                f"{current_session_label or current_sample or 'current sample'}: "
                f"已完成 {current}/{total} 个 session，累计消息 {submitted}/{expected}"
            )
        elif not detail:
            detail = f"{current_session_label or current_sample or 'current sample'}: 已完成 {current}/{total} 个 session"

    if is_generic_question_benchmark:
        answered_rows = _csv_row_count(getattr(task, "output_file", ""))
        generic_answered_rows = answered_rows
        configured_total = 0
        try:
            configured_total = int(config.get("count") or _command_option(task, "count") or 0)
        except Exception:
            configured_total = 0
        inferred_total = max(generic_item_total, configured_total, answered_rows, total)
        if inferred_total > 0:
            total = inferred_total
            current = max(generic_item_current, current if unit == "questions" else 0, answered_rows)
            if (
                getattr(task, "status", "") == "running"
                and answered_rows < total
                and not str(phase or "").startswith("qa")
            ):
                current = max(current, min(answered_rows + 1, total))
            unit = "questions"
            if not phase:
                phase = generic_item_stage or ("qa" if answered_rows else "running")
            question_scope = f"{dataset_format or 'generic_qa'} question {current}/{total}"
            if detail:
                detail = f"{question_scope} · {detail}"
            else:
                detail = question_scope

    if total <= 0:
        return None

    if is_echomemory_postprocess_kind and awaiting_echomem_commit:
        phase = "commit:indexing"
        if kind == "echomemory_import":
            unit = "sessions"
            total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, total))
            current = _locomo_session_progress_display_current(
                commit_completed_count,
                total,
                current_session_label,
                locomo_totals,
            )
            detail = (
                f"{current_session_label or current_sample}: "
                f"EchoMemory is committing and indexing {current_session_added}/{current_session_expected} messages"
            )
        elif is_generic_question_benchmark:
            unit = "questions"
            total = max(total, generic_item_total, generic_answered_rows, 1)
            if getattr(task, "status", "") == "running" and generic_answered_rows < total:
                current = max(current, min(generic_answered_rows + 1, total))
            else:
                current = max(current, generic_answered_rows)
            question_scope = f"{dataset_format or 'generic_qa'} question {current}/{total}"
            detail = (
                f"{question_scope} · "
                f"{current_session_label or current_sample or 'current sample'}: "
                f"EchoMemory 已提交消息，正在后台整理、抽取并索引长期记忆"
            )
        else:
            total = max(total, current_session_expected or total or 1)
            current = min(max(current_session_added, 1), total)
            unit = "messages" if current_session_expected else (unit or "items")
            detail = (
                f"{current_session_label or current_sample or 'current sample'}: "
                f"EchoMemory 已提交消息，正在后台整理、抽取并索引长期记忆"
            )
        indeterminate = True

    if kind == "openviking_import":
        diagnostics = task_log_diagnostics(task)
        if diagnostics.get("embedding_timeout_count") or diagnostics.get("embedding_circuit_breaker_count"):
            warnings.append("embedding_timeout_or_circuit_breaker")
            if phase.startswith("commit:") and getattr(task, "status", "") == "running":
                phase = "commit:embedding_retry"
                detail = f"OpenViking memory extraction is waiting for embedding retry/indexing ({current_session_label or current_sample or 'current session'})"

    ended_at = getattr(task, "ended_at", None)
    started_at = getattr(task, "started_at", None)
    created_at = getattr(task, "created_at", time.time())
    elapsed = (ended_at or time.time()) - (started_at or created_at)
    eta = None
    if getattr(task, "status", "") == "running" and current > 0 and current < total:
        eta = max(0, round((elapsed / current) * (total - current), 1))

    if not current_import and (is_memory_import or (is_generic_memory_qa and dataset_format == "locomo")):
        current_import = _locomo_current_import_preview(
            task,
            sample=current_sample,
            session_label=current_session_label,
            message_index=current_session_added,
            message_total=current_session_expected,
            phase=phase,
        )

    if is_memory_import:
        current, total = _stabilize_session_progress(
            task,
            phase=phase,
            unit=unit,
            current=current,
            total=total,
            session_label=current_session_label,
            current_import=current_import,
        )

    if kind in {
        "openviking_qa",
        "echomemory_qa",
        "openviking_qa_retry_failed",
        "openviking_qa_retry_missing",
        "openviking_generic_qa",
        "echomemory_generic_qa",
        "local_agent",
    }:
        latest_row = _latest_csv_row(getattr(task, "output_file", ""))
        if latest_row:
            latest_answer = _compact_preview_text(
                latest_row.get("response")
                or latest_row.get("hypothesis")
                or latest_row.get("prediction")
                or latest_row.get("model_answer")
                or latest_row.get("model_response")
                or "",
                320,
            )
            latest_question = _compact_preview_text(latest_row.get("question") or "", 220)
            latest_question_id = str(latest_row.get("question_id") or latest_row.get("sample_id") or "").strip()
            if latest_question_id or latest_question or latest_answer:
                if not qa_preview:
                    qa_preview = {
                        "question_id": latest_question_id,
                        "question": latest_question,
                        "answer": latest_answer,
                        "source": "csv",
                    }
                elif latest_question_id and latest_question_id == str(qa_preview.get("question_id") or "").strip():
                    qa_preview = {
                        **qa_preview,
                        "question": str(qa_preview.get("question") or latest_question or ""),
                        "answer": latest_answer,
                        "source": "csv",
                    }

    return {
        "current": current,
        "total": total,
        "pct": round(current / total * 100, 1),
        "phase": phase,
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": eta,
        "unit": unit,
        "detail": detail,
        "indeterminate": indeterminate and getattr(task, "status", "") == "running",
        "warnings": warnings,
        "sample": current_sample,
        "session_label": current_session_label,
        "current_import": current_import,
        "qa_preview": qa_preview,
        "completed_samples": len(completed_samples),
        "total_samples": locomo_totals.get("sample_count") if locomo_totals else None,
        "total_messages": locomo_totals.get("message_count") if locomo_totals else None,
    }
