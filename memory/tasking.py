from __future__ import annotations

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


def task_progress(task: Any) -> dict[str, Any] | None:
    kind = str(getattr(task, "kind", "") or "")
    log_file = str(getattr(task, "log_file", "") or "")
    if kind not in {
        "local_agent",
        "openviking_qa",
        "openviking_generic_qa",
        "openviking_import",
        "echomemory_qa",
        "echomemory_import",
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
    current_import: dict[str, Any] | None = None
    awaiting_echomem_commit = False
    is_memory_import = kind in {"openviking_import", "echomemory_import"}
    backend_label = "EchoMemory" if kind.startswith("echomemory") else "OpenViking"
    locomo_totals = _locomo_import_totals(task) if is_memory_import else {}
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.search(r"\[(import|qa)\]\s+(\d+)/(\d+)\s+(.*)", line)
            if match:
                phase = match.group(1)
                current = int(match.group(2))
                total = int(match.group(3))
                unit = "questions" if phase == "qa" else "samples"
                detail = match.group(4).strip()
                indeterminate = False

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
                    kind == "echomemory_import"
                    and current_session_expected > 0
                    and current_session_added >= current_session_expected
                )
                if is_memory_import and locomo_totals.get("session_count"):
                    total = int(locomo_totals["session_count"])
                    session_index = _locomo_session_progress_index(current_session_label, locomo_totals)
                    current = min(max(session_index, commit_completed_count + 1, current), total)
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

            commit_accepted_match = re.search(r"\[commit\]\s+(.+?)\s+status=accepted\s+task_id=", line)
            if commit_accepted_match:
                commit_total_count += 1

            commit_completed_match = re.search(r"\[commit\]\s+task=.*status=completed", line)
            if commit_completed_match:
                commit_completed_count += 1

            commit_match = re.search(r"\[commit\]\s+task=.*status=(\w+)", line)
            if commit_match:
                phase = f"commit:{commit_match.group(1)}"
                if commit_total_count > 0:
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
                if commit_total_count > 0:
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
                session_index = _locomo_session_progress_index(echomem_commit_match.group(1), locomo_totals)
                current = min(max(session_index, commit_completed_count if complete else commit_total_count, current), total)
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

    if total <= 0:
        return None

    if kind == "echomemory_import" and awaiting_echomem_commit:
        phase = "commit:indexing"
        unit = "sessions"
        total = int(locomo_totals.get("session_count") or max(commit_expected_count, current_sample_sessions, total))
        session_index = _locomo_session_progress_index(current_session_label, locomo_totals)
        current = min(max(session_index, commit_completed_count + 1, current), total)
        detail = (
            f"{current_session_label or current_sample}: "
            f"EchoMemory is committing and indexing {current_session_added}/{current_session_expected} messages"
        )
        indeterminate = False

    warnings: list[str] = []
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

    if not current_import and is_memory_import:
        current_import = _locomo_current_import_preview(
            task,
            sample=current_sample,
            session_label=current_session_label,
            message_index=current_session_added,
            message_total=current_session_expected,
            phase=phase,
        )

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
        "completed_samples": len(completed_samples),
        "total_samples": locomo_totals.get("sample_count") if locomo_totals else None,
        "total_messages": locomo_totals.get("message_count") if locomo_totals else None,
    }
