#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import os
import re
import signal
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from echomemory_common import (
    DEFAULT_ECHOMEM_ROOT,
    ctx,
    ensure_echomem_imports,
    sdk_ctx_kwargs,
    workspace_token_usage_summary,
    write_echomem_config,
    write_json,
)


class HardTimeoutError(TimeoutError):
    pass


@contextmanager
def hard_timeout(seconds: float, label: str):
    timeout = float(seconds or 0)
    if timeout <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)

    def _raise_timeout(_signum, _frame):
        raise HardTimeoutError(f"{label} exceeded {timeout:g}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    cached = getattr(args, "_runtime_config_cache", None)
    if isinstance(cached, dict):
        return cached
    path = Path(str(getattr(args, "echomem_config", "") or "")).expanduser()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = read_yaml(path)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    setattr(args, "_runtime_config_cache", data)
    return data


def config_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    current: Any = data
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def abstract_required(args: argparse.Namespace) -> bool:
    cfg = runtime_config(args)
    return bool(config_get(cfg, "session.generate_abstract", True))


def summarize_sample_progress(
    args: argparse.Namespace,
    sample_index: int,
    sample_id: str,
    original_session_count: int,
    session_batches: list[dict[str, Any]],
    estimated_tokens: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = sum(int(item.get("expected_messages") or 0) for item in records)
    submitted = sum(int(item.get("submitted_messages") or 0) for item in records)
    live_complete = bool(records) and all(item.get("live_complete_before_commit") for item in records)
    archive_complete = bool(records) and all(item.get("archive_complete_after_commit") for item in records)
    atom_memory_complete = bool(records) and all(item.get("atom_memory_complete_after_commit") for item in records)
    retrieval_ready = bool(records) and all(item.get("retrieval_ready_after_commit") for item in records)
    cursor_complete = bool(records) and all(item.get("cursor_complete_after_commit") for item in records)
    qa_ready = bool(records) and all(item.get("qa_ready_after_commit") for item in records)
    pending_async_memory = bool(records) and any(str(item.get("integrity") or "") == "pending_async_memory" for item in records)
    integrity = (
        "complete" if qa_ready else (
            "pending_async_memory"
            if archive_complete and (pending_async_memory or retrieval_ready or atom_memory_complete or cursor_complete)
            else ("partial" if archive_complete or atom_memory_complete or retrieval_ready else "incomplete")
        )
    )
    commit_warnings = [str(item.get("commit_warning") or "") for item in records if item.get("commit_warning")]
    return {
        "sample_index": sample_index,
        "sample_id": sample_id,
        "session_id": records[0]["session_id"] if len(records) == 1 else f"echomem-locomo-{sample_id}-*",
        "session_mode": args.session_mode,
        "session_count": len(records),
        "original_session_count": original_session_count,
        "session_start": int(args.session_start or 0),
        "session_end": int(args.session_end or 0),
        "session_limit": int(args.max_sessions or 0),
        "session_records": records,
        "expected_messages": expected,
        "submitted_messages": submitted,
        "live_message_count_before_commit": expected,
        "pending_message_count_after_commit": 0,
        "live_complete_before_commit": live_complete and submitted == expected,
        "archive_complete_after_commit": archive_complete,
        "atom_memory_complete_after_commit": atom_memory_complete,
        "retrieval_ready_after_commit": retrieval_ready,
        "cursor_complete_after_commit": cursor_complete,
        "qa_ready_after_commit": qa_ready,
        "pending_async_memory_after_commit": pending_async_memory,
        "commit_warnings": commit_warnings,
        "integrity": integrity,
        "integrity_stage": (
            "qa_ready"
            if qa_ready
            else (
                "cursor_complete"
                if cursor_complete
                else (
                    "atom_memory_complete"
                    if atom_memory_complete
                    else (
                        "retrieval_ready"
                        if retrieval_ready
                        else ("async_memory_pending" if integrity == "pending_async_memory" else ("archive_complete" if archive_complete else "incomplete"))
                    )
                )
            )
        ),
        "estimated_import_tokens": estimated_tokens,
        "progress_sessions_done": len(records),
        "progress_sessions_total": len(session_batches),
    }


def build_import_summary(
    args: argparse.Namespace,
    root: Path,
    config_path: Path,
    records: list[dict[str, Any]],
    *,
    status: str,
    status_explanation: str,
    running: bool = False,
) -> dict[str, Any]:
    complete = sum(1 for item in records if item["integrity"] == "complete")
    pending_async = sum(1 for item in records if item["integrity"] == "pending_async_memory")
    partial = sum(1 for item in records if item["integrity"] == "partial")
    archive_complete = sum(1 for item in records if item.get("archive_complete_after_commit"))
    atom_memory_complete = sum(1 for item in records if item.get("atom_memory_complete_after_commit"))
    retrieval_ready = sum(1 for item in records if item.get("retrieval_ready_after_commit"))
    cursor_complete = sum(1 for item in records if item.get("cursor_complete_after_commit"))
    qa_ready = sum(1 for item in records if item.get("qa_ready_after_commit"))
    commit_warnings = [
        warning
        for item in records
        for warning in (item.get("commit_warnings") or [])
        if warning
    ]
    summary = {
        "status": status,
        "backend": "echomemory",
        "running": running,
        "samples": len(records),
        "complete_samples": complete,
        "pending_async_samples": pending_async,
        "partial_samples": partial,
        "incomplete_samples": len(records) - complete - pending_async - partial,
        "archive_complete_samples": archive_complete,
        "atom_memory_complete_samples": atom_memory_complete,
        "retrieval_ready_samples": retrieval_ready,
        "cursor_complete_samples": cursor_complete,
        "qa_ready_samples": qa_ready,
        "status_explanation": status_explanation,
        "warnings": commit_warnings,
        "expected_messages": sum(item["expected_messages"] for item in records),
        "submitted_messages": sum(item["submitted_messages"] for item in records),
        "estimated_import_tokens": sum(item["estimated_import_tokens"] for item in records),
        "workspace": str(Path(args.workspace).expanduser().resolve()),
        "account": args.account,
        "sample": args.sample,
        "echomem_root": str(root),
        "echomem_config": str(config_path),
        "session_limit": int(args.max_sessions or 0),
        "session_start": int(args.session_start or 0),
        "session_end": int(args.session_end or 0),
        "records": records,
    }
    summary.update(workspace_token_usage_summary(args.workspace, args.account))
    return summary


def write_running_summary(
    out_dir: Path,
    args: argparse.Namespace,
    root: Path,
    config_path: Path,
    current_records: list[dict[str, Any]],
) -> None:
    summary = build_import_summary(
        args,
        root,
        config_path,
        current_records,
        status="ECHOMEMORY_IMPORT_RUNNING",
        status_explanation="Import is still running; this summary is a live progress snapshot and may not include all sessions yet.",
        running=True,
    )
    write_json(out_dir / "echomemory_import_summary.json", summary)


def write_bootstrap_summary(
    out_dir: Path,
    args: argparse.Namespace,
    root: Path,
    config_path: Path,
    *,
    status: str,
    status_explanation: str,
    running: bool,
    error: str = "",
) -> None:
    summary = build_import_summary(
        args,
        root,
        config_path,
        [],
        status=status,
        status_explanation=status_explanation,
        running=running,
    )
    if error:
        summary["error"] = error
    write_json(out_dir / "echomemory_import_summary.json", summary)


def compact(text: Any, limit: int = 900) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def exception_text(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def is_fatal_model_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
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
    )


def sanitize_model_error(text: Any) -> str:
    value = str(text or "").strip()
    value = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", value)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._-]{8,}", "Bearer ***", value, flags=re.I)
    return value[:1200]


def openai_compatible_chat_preflight(base_url: str, model: str, token: str, timeout_s: float = 45) -> dict[str, Any]:
    base = str(base_url or "").strip().rstrip("/")
    model = str(model or "").strip()
    token = str(token or "").strip()
    if not base:
        return {"ok": False, "status": "missing_base_url", "error": "缺少 Base URL。"}
    if not model:
        return {"ok": False, "status": "missing_model", "error": "缺少模型名。"}
    if not token:
        return {"ok": False, "status": "missing_api_key", "error": "缺少 API Key。"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_s or 45)) as response:
            raw = response.read(4096).decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            return {
                "ok": True,
                "status": response.status,
                "base_url": base,
                "model": model,
                "content_len": len(text),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", "replace")
        return {
            "ok": False,
            "status": exc.code,
            "base_url": base,
            "model": model,
            "error": sanitize_model_error(body),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": exc.__class__.__name__,
            "base_url": base,
            "model": model,
            "error": sanitize_model_error(exc),
        }


def openai_compatible_embedding_preflight(base_url: str, model: str, token: str, timeout_s: float = 45) -> dict[str, Any]:
    base = str(base_url or "").strip().rstrip("/")
    model = str(model or "").strip()
    token = str(token or "").strip()
    if not base:
        return {"ok": False, "status": "missing_base_url", "error": "缺少 Base URL。"}
    if not model:
        return {"ok": False, "status": "missing_model", "error": "缺少 embedding 模型名。"}
    if not token:
        return {"ok": False, "status": "missing_api_key", "error": "缺少 embedding API Key。"}
    payload = {
        "model": model,
        "input": "ping",
    }
    request = urllib.request.Request(
        f"{base}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_s or 45)) as response:
            raw = response.read(4096).decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
            vector_size = len(((data.get("data") or [{}])[0].get("embedding") or []))
            return {
                "ok": True,
                "status": response.status,
                "base_url": base,
                "model": model,
                "vector_size": vector_size,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", "replace")
        return {
            "ok": False,
            "status": exc.code,
            "base_url": base,
            "model": model,
            "error": sanitize_model_error(body),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": exc.__class__.__name__,
            "base_url": base,
            "model": model,
            "error": sanitize_model_error(exc),
        }


def retry_preflight(callable_obj: Any, *args: Any, attempts: int = 3, **kwargs: Any) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(1, max(1, int(attempts)) + 1):
        last = callable_obj(*args, **kwargs)
        last["attempt"] = attempt
        if last.get("ok"):
            return last
        status = str(last.get("status") or "")
        if status not in {"TimeoutError", "URLError", "RemoteDisconnected"}:
            return last
        time.sleep(min(2 * attempt, 8))
    return last


def import_model_preflight(out_dir: Path) -> dict[str, Any]:
    dashscope_base = str(os.environ.get("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    embedding_token = str(os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ECHOMEM_API_KEY") or "").strip()
    embedding_model = str(os.environ.get("ECHOMEM_EMBEDDING_MODEL") or "text-embedding-v3").strip()
    chat_base = str(os.environ.get("ECHOMEM_CHAT_BASE_URL") or dashscope_base).strip()
    chat_token = str(os.environ.get("ECHOMEM_CHAT_API_KEY") or embedding_token).strip()
    chat_model = str(os.environ.get("ECHOMEM_CHAT_MODEL") or "deepseek-v4-flash").strip()
    embedding = retry_preflight(
        openai_compatible_embedding_preflight,
        dashscope_base,
        embedding_model,
        embedding_token,
        timeout_s=30,
        attempts=3,
    )
    chat = retry_preflight(
        openai_compatible_chat_preflight,
        chat_base,
        chat_model,
        chat_token,
        timeout_s=45,
        attempts=3,
    )
    status = "ok" if embedding.get("ok") and chat.get("ok") else "fail"
    report = {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "embedding": embedding,
        "chat": chat,
        "note": "导入前预检只验证 provider key / base_url / model 是否可用，不代表 overview / atom / graph 已经生成。",
    }
    write_json(out_dir / "echomemory_model_preflight.json", report)
    return report


async def commit_session_full(sdk: Any, session_id: str, context: dict[str, str]) -> Any:
    runtime = getattr(sdk, "_runtime", None)
    services = getattr(runtime, "services", None)
    session_service = getattr(services, "session", None)
    if session_service is not None:
        request_ctx = sdk._ctx(**context)
        try:
            return await session_service.commit(session_id, request_ctx, keep_recent_count=0)
        except TypeError as exc:
            if "keep_recent_count" not in str(exc):
                raise
            return await session_service.commit(session_id, request_ctx)
    try:
        return await sdk.commit_session(session_id, ctx=context, keep_recent_count=0)
    except TypeError as exc:
        if "keep_recent_count" not in str(exc):
            raise
        return await sdk.commit_session(session_id, ctx=context)


def token_estimate(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4) if text else 0


def session_number(key: str) -> int:
    return int(str(key).split("_")[1])


def locomo_samples(data: list[dict[str, Any]], sample_filter: str) -> list[tuple[int, dict[str, Any]]]:
    rows = []
    for index, sample in enumerate(data):
        sample_id = str(sample.get("sample_id") or f"sample_{index}")
        if sample_filter not in ("", "all") and sample_filter not in {str(index), sample_id}:
            continue
        rows.append((index, sample))
    return rows


def parse_datetime(value: str) -> datetime | None:
    value = str(value or "").strip()
    for fmt in ("%I:%M %p on %d %B, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def format_turn_time(base_dt: datetime | None, idx: int) -> str:
    if not base_dt:
        return ""
    return (base_dt + timedelta(seconds=idx)).isoformat()


def find_session_dir(workspace: str, account: str, session_id: str) -> Path | None:
    root = Path(workspace).expanduser().resolve()
    candidates = [
        root / account / account / "sessions" / session_id,
        root / account / "sessions" / session_id,
        root / "sessions" / session_id,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    try:
        return next(root.rglob(f"sessions/{session_id}"))
    except StopIteration:
        return None


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    try:
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    except Exception:
        return []


def safe_int(value: Any, default: int = -1) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def extraction_cursor(meta: dict[str, Any]) -> str:
    return str(
        meta.get("atom_last_extracted_turn_id")
        or meta.get("last_extracted_turn_id")
        or ""
    )


def extraction_timestamp(meta: dict[str, Any]) -> str:
    return str(
        meta.get("atom_last_extracted_at")
        or meta.get("last_extracted_at")
        or ""
    )


def account_memory_root(workspace: str, account: str) -> Path:
    root = Path(workspace).expanduser().resolve()
    for candidate in (root / account / account, root / account, root):
        if (candidate / "memory").exists() or (candidate / "sessions").exists():
            return candidate
    return root / account / account


def reset_extraction_cursor(session_dir: Path) -> bool:
    meta_path = session_dir / "meta.json"
    meta = read_json_file(meta_path)
    if not meta:
        return False
    meta["atom_pipeline_index"] = -1
    meta["last_extracted_turn_id"] = ""
    meta["last_extracted_at"] = ""
    if "atom_last_extracted_turn_id" in meta:
        meta["atom_last_extracted_turn_id"] = ""
    if "atom_last_extracted_at" in meta:
        meta["atom_last_extracted_at"] = ""
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return True


def count_memory_artifacts(workspace: str, account: str) -> dict[str, Any]:
    memory_root = account_memory_root(workspace, account)
    atoms_dir = memory_root / "memory" / ".structured" / "atoms"
    atoms_bundle = memory_root / "memory" / ".structured" / "atoms.json"
    relations_dir = memory_root / "memory" / ".structured" / "relations"
    graph_root = memory_root / "memory" / ".graph"
    graph_nodes_dir = graph_root / "nodes"
    graph_edges_dir = graph_root / "edges"
    graph_adjacency_dir = graph_root / "adjacency"
    vector_root = Path(workspace).expanduser().resolve() / account / "system" / "vector_index" / account
    vector_meta = vector_root / "meta.json"
    vector_items = 0
    if vector_meta.exists():
        meta = read_json_file(vector_meta)
        if isinstance(meta.get("str_to_int"), dict):
            vector_items = len(meta["str_to_int"])
        elif isinstance(meta.get("int_to_str"), dict):
            vector_items = len(meta["int_to_str"])

    def json_count(root_dir: Path) -> int:
        return len(list(root_dir.rglob("*.json"))) if root_dir.exists() else 0

    atoms_count = len(list(atoms_dir.glob("*.json"))) if atoms_dir.exists() else 0
    if atoms_count == 0 and atoms_bundle.exists():
        payload = read_json_file(atoms_bundle)
        if isinstance(payload, dict):
            bundled_atoms = payload.get("atoms")
            if isinstance(bundled_atoms, dict):
                atoms_count = len(bundled_atoms)
            elif isinstance(bundled_atoms, list):
                atoms_count = len(bundled_atoms)
        elif isinstance(payload, list):
            atoms_count = len(payload)

    return {
        "memory_root": str(memory_root),
        "atoms_count": atoms_count,
        "relations_count": len(list(relations_dir.glob("*.json"))) if relations_dir.exists() else 0,
        "graph_root": str(graph_root),
        "graph_exists": graph_root.exists(),
        "graph_nodes_count": json_count(graph_nodes_dir),
        "graph_edges_count": json_count(graph_edges_dir),
        "graph_adjacency_count": json_count(graph_adjacency_dir),
        "vector_index_path": str(vector_root),
        "vector_index_exists": (vector_root / "index.bin").exists(),
        "vector_meta_exists": vector_meta.exists(),
        "vector_items": vector_items,
    }


def report_to_dict(report: Any) -> dict[str, Any]:
    return {
        "atoms_added": int(getattr(report, "atoms_added", 0) or 0),
        "atoms_updated": int(getattr(report, "atoms_updated", 0) or 0),
        "atoms_invalidated": int(getattr(report, "atoms_invalidated", 0) or 0),
        "relations_added": int(getattr(report, "relations_added", 0) or 0),
        "extraction_granularity": str(getattr(report, "extraction_granularity", "") or ""),
        "extraction_turns_processed": int(getattr(report, "extraction_turns_processed", 0) or 0),
        "extraction_atoms_extracted": int(getattr(report, "extraction_atoms_extracted", 0) or 0),
        "extraction_failures": int(getattr(report, "extraction_failures", 0) or 0),
    }


async def flush_atom_pipeline(
    args: argparse.Namespace,
    sdk: Any,
    session_id: str,
    *,
    expected_message_count: int,
    expected_last_message_id: str,
) -> dict[str, Any]:
    runtime = getattr(sdk, "_runtime", None)
    services = getattr(runtime, "services", None)
    pipeline = getattr(services, "atom_pipeline", None)
    if pipeline is None:
        return {"available": False, "complete": False, "attempts": [], "error": "atom_pipeline unavailable"}

    request_ctx = sdk._ctx(**sdk_ctx_kwargs(sdk, args.account, args.user_id, args.agent_id, session_id))
    expected_index = expected_message_count - 1
    attempts: list[dict[str, Any]] = []
    final_state: dict[str, Any] = {}
    started_all = time.time()
    previous_atom_index = -1
    previous_last_turn_id = ""
    for attempt in range(1, max(1, int(args.flush_attempts)) + 1):
        started = time.time()
        try:
            base_flush_timeout = max(1.0, float(args.flush_call_timeout_s))
            flush_timeout = base_flush_timeout * attempt
            with hard_timeout(flush_timeout, f"atom_pipeline.ingest_message({session_id})"):
                report = await asyncio.wait_for(
                    pipeline.ingest_message(session_id, request_ctx),
                    timeout=flush_timeout,
                )
            error = ""
        except Exception as exc:
            report = None
            error = exception_text(exc)
            print(
                f"[flush] session={session_id} attempt={attempt} status=error "
                f"elapsed={time.time() - started:.1f}s error={compact(error, 300)}",
                flush=True,
            )
        timed_out = error in {"TimeoutError", "HardTimeoutError"} or "exceeded" in error
        session_dir = find_session_dir(args.workspace, args.account, session_id)
        meta = read_json_file(session_dir / "meta.json") if session_dir else {}
        atom_index = safe_int(meta.get("atom_pipeline_index"), -1) if meta else -1
        last_turn_id = extraction_cursor(meta) if meta else ""
        cursor_ok = bool(last_turn_id) and (not expected_last_message_id or last_turn_id == expected_last_message_id)
        memory_artifacts = count_memory_artifacts(args.workspace, args.account)
        artifacts_ready = bool(
            int(memory_artifacts.get("atoms_count") or 0) > 0
            or int(memory_artifacts.get("relations_count") or 0) > 0
            or int(memory_artifacts.get("graph_nodes_count") or 0) > 0
            or int(memory_artifacts.get("graph_edges_count") or 0) > 0
        )
        final_state = {
            "session_dir": str(session_dir or ""),
            "atom_last_extracted_turn_id": last_turn_id,
            "atom_last_extracted_turn_id_ok": cursor_ok,
            "atom_pipeline_index": atom_index,
            "expected_atom_pipeline_index": expected_index,
            "expected_last_message_id": expected_last_message_id,
            "memory_artifacts": memory_artifacts,
            "artifacts_ready": artifacts_ready,
        }
        attempts.append({
            "attempt": attempt,
            "elapsed_s": round(time.time() - started, 3),
            "error": error,
            "timed_out": timed_out,
            **report_to_dict(report),
            **final_state,
        })
        if not error:
            print(
                f"[flush] session={session_id} attempt={attempt} "
                f"atom_pipeline_index={atom_index}/{expected_index} "
                f"cursor_ok={cursor_ok} elapsed={time.time() - started:.1f}s",
                flush=True,
            )
        flush_ready = bool(
            atom_index >= expected_index
            or (cursor_ok and artifacts_ready)
        )
        if not error and flush_ready and not int(attempts[-1].get("extraction_failures") or 0):
            break
        if error and is_fatal_model_error(error):
            break
        progressed = atom_index > previous_atom_index or (
            bool(last_turn_id) and last_turn_id != previous_last_turn_id
        )
        if timed_out:
            if attempt >= max(1, int(args.flush_attempts)):
                break
            # Do not clear the extraction cursor just because organized
            # artifacts have not landed yet. EchoMemory v0.0.6 often advances
            # last_extracted_turn_id before atoms/graph are fully materialized,
            # and auto-resetting here causes the platform to throw away real
            # async progress and re-run the same extraction windows again.
            if cursor_ok and not artifacts_ready and atom_index < expected_index:
                print(
                    "[flush] "
                    f"session={session_id} attempt={attempt} keep_cursor_after_timeout "
                    f"atom_pipeline_index={atom_index}/{expected_index} "
                    f"cursor={last_turn_id or '-'} artifacts_ready={artifacts_ready}",
                    flush=True,
                )
            print(
                "[flush] "
                f"session={session_id} attempt={attempt} timeout_continue "
                f"progressed={progressed} atom_pipeline_index={atom_index}/{expected_index} "
                f"next_timeout_s={flush_timeout + base_flush_timeout:.1f}",
                flush=True,
            )
            previous_atom_index = atom_index
            previous_last_turn_id = last_turn_id
            await asyncio.sleep(1.0 if progressed else 0.5)
            continue
        previous_atom_index = atom_index
        previous_last_turn_id = last_turn_id
        await asyncio.sleep(0.5)
    last_attempt = attempts[-1] if attempts else {}
    complete = bool(
        (
            expected_message_count == 0
            or final_state.get("atom_pipeline_index", -1) >= expected_index
            or (
                final_state.get("atom_last_extracted_turn_id_ok")
                and final_state.get("artifacts_ready")
            )
        )
        and not int(last_attempt.get("extraction_failures") or 0)
        and not str(last_attempt.get("error") or "")
    )
    return {
        "available": True,
        "complete": complete,
        "elapsed_s": round(time.time() - started_all, 3),
        "attempts": attempts,
        **final_state,
    }


def collect_commit_artifact_state(
    args: argparse.Namespace,
    session_id: str,
    *,
    expected_message_count: int = 0,
    expected_last_message_id: str = "",
) -> dict[str, Any]:
    session_dir = find_session_dir(args.workspace, args.account, session_id)
    meta = read_json_file(session_dir / "meta.json") if session_dir else {}
    messages_path = session_dir / "messages.jsonl" if session_dir else Path("__missing__")
    stored_messages = read_jsonl_file(messages_path) if session_dir else []
    stored_message_count = len(stored_messages)
    actual_last_message_id = str(stored_messages[-1].get("message_id") or "") if stored_messages else ""
    target_message_count = expected_message_count or stored_message_count
    target_last_message_id = expected_last_message_id or actual_last_message_id
    expected_index = target_message_count - 1
    commit_index = safe_int(meta.get("commit_index"), -1) if meta else -1
    atom_index = safe_int(meta.get("atom_pipeline_index"), -1) if meta else -1
    abstract_path = session_dir / "abstract.md" if session_dir else Path("__missing__")
    overview_path = session_dir / "overview.md" if session_dir else Path("__missing__")
    abstract_ok = abstract_path.exists() and bool(abstract_path.read_text(encoding="utf-8", errors="replace").strip())
    overview_ok = overview_path.exists() and bool(overview_path.read_text(encoding="utf-8", errors="replace").strip())
    require_abstract = abstract_required(args)
    abstract_ready = abstract_ok if require_abstract else True
    cursor = extraction_cursor(meta) if meta else ""
    commit_ok = expected_index < 0 or commit_index >= expected_index
    atom_index_ok = expected_index < 0 or atom_index >= expected_index
    cursor_ok = bool(cursor) and (not target_last_message_id or cursor == target_last_message_id)
    extraction_ok = atom_index_ok or cursor_ok
    memory_artifacts = count_memory_artifacts(args.workspace, args.account)
    vector_ready = bool(memory_artifacts.get("vector_items", 0) > 0 or memory_artifacts.get("vector_index_exists"))
    if getattr(args, "skip_session_commit", False):
        memory_artifacts_ok = bool(
            stored_message_count >= target_message_count
            and atom_index_ok
            and (
                memory_artifacts.get("atoms_count", 0) > 0
                or vector_ready
            )
        )
    else:
        memory_artifacts_ok = bool(
            abstract_ready
            and overview_ok
            and (
                memory_artifacts.get("atoms_count", 0) > 0
                or (atom_index_ok and vector_ready)
            )
        )
    extraction_complete_by = (
        "atom_pipeline_index" if atom_index_ok else ("atom_last_extracted_turn_id" if cursor_ok else ("memory_artifacts" if memory_artifacts_ok else ""))
    )
    strict_complete = bool(
        commit_ok
        and extraction_ok
        and abstract_ready
        and overview_ok
        and memory_artifacts_ok
    )
    return {
        "session_dir": str(session_dir or ""),
        "meta_exists": bool(meta),
        "stored_message_count": stored_message_count,
        "expected_message_count": target_message_count,
        "last_message_id": actual_last_message_id,
        "expected_last_message_id": target_last_message_id,
        "commit_index": commit_index,
        "expected_commit_index": expected_index,
        "commit_index_ok": commit_ok,
        "atom_pipeline_index": atom_index,
        "expected_atom_pipeline_index": expected_index,
        "atom_pipeline_index_ok": atom_index_ok,
        "atom_last_extracted_turn_id": cursor,
        "atom_last_extracted_turn_id_ok": cursor_ok,
        "atom_last_extracted_at": extraction_timestamp(meta) if meta else "",
        "session_last_extracted_turn_id": str(meta.get("last_extracted_turn_id") or "") if meta else "",
        "session_last_extracted_at": str(meta.get("last_extracted_at") or "") if meta else "",
        "extraction_complete_by": extraction_complete_by,
        "pending_tokens": safe_int(meta.get("pending_tokens"), 0) if meta else None,
        "abstract_required": require_abstract,
        "abstract_exists": abstract_path.exists(),
        "abstract_nonempty": abstract_ok,
        "overview_exists": overview_path.exists(),
        "overview_nonempty": overview_ok,
        "memory_artifacts": memory_artifacts,
        "session_commit_skipped": bool(getattr(args, "skip_session_commit", False)),
        "vector_ready": vector_ready,
        "legacy_commit_complete": commit_ok and extraction_ok and abstract_ready and overview_ok,
        "retrieval_ready": memory_artifacts_ok,
        "cursor_complete": extraction_ok,
        "qa_ready": strict_complete,
        "complete": strict_complete,
    }


async def wait_for_commit_artifacts(
    args: argparse.Namespace,
    session_id: str,
    *,
    expected_message_count: int = 0,
    expected_last_message_id: str = "",
) -> dict[str, Any]:
    deadline = time.time() + max(0, float(args.commit_wait_s))
    last_state: dict[str, Any] = {}
    while True:
        last_state = collect_commit_artifact_state(
            args,
            session_id,
            expected_message_count=expected_message_count,
            expected_last_message_id=expected_last_message_id,
        )
        if last_state["complete"] or time.time() >= deadline:
            last_state["wait_elapsed_s"] = round(max(0.0, float(args.commit_wait_s) - max(0.0, deadline - time.time())), 3)
            return last_state
        await asyncio.sleep(2)


def build_session_batches(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    conv = sample.get("conversation") or {}
    keys = [key for key, value in conv.items() if re.fullmatch(r"session_\d+", str(key)) and isinstance(value, list)]
    keys.sort(key=session_number)
    sessions: list[dict[str, Any]] = []
    total_tokens = 0
    for key in keys:
        base_dt = parse_datetime(str(conv.get(f"{key}_date_time") or ""))
        messages: list[dict[str, Any]] = []
        for idx, raw in enumerate(conv.get(key) or []):
            if not isinstance(raw, dict):
                continue
            speaker = raw.get("speaker") or raw.get("role") or "speaker"
            dia_id = raw.get("dia_id") or f"{key}:{idx}"
            parts = []
            if raw.get("text"):
                parts.append(str(raw["text"]))
            if raw.get("blip_caption"):
                parts.append(f"image: {raw['blip_caption']}")
            if raw.get("query"):
                parts.append(f"query: {raw['query']}")
            if not parts:
                continue
            turn_time = format_turn_time(base_dt, idx)
            time_prefix = f"[session_date={conv.get(f'{key}_date_time')}]"
            if turn_time:
                time_prefix += f" [turn_time={turn_time}]"
            # Keep LoCoMo's original session/turn time in the text as a visible
            # anchor for relative expressions like "yesterday" and "last week".
            content = compact(f"{time_prefix} [{speaker}] {dia_id}: {' '.join(parts)}")
            role = "assistant" if str(speaker).lower() in {"assistant", "agent"} else "user"
            item = {"role": role, "content": content}
            if turn_time:
                item["created_at"] = turn_time
            item["role_id"] = str(speaker)
            item["speaker"] = str(speaker)
            item["dia_id"] = str(dia_id)
            messages.append(item)
        total_tokens += sum(token_estimate(msg["content"]) for msg in messages)
        if messages:
            sessions.append({"session_key": key, "date_time": str(conv.get(f"{key}_date_time") or ""), "messages": messages})
    return sessions, total_tokens


async def import_one_session(args: argparse.Namespace, sdk: Any, session_id: str, messages: list[dict[str, Any]], label: str) -> dict[str, Any]:
    print(f"[import] session={session_id} label={label} expected_messages={len(messages)}", flush=True)
    context = sdk_ctx_kwargs(sdk, args.account, args.user_id, args.agent_id, session_id)
    created = await sdk.create_session(title=label, ctx=context)
    actual_session_id = created["session_id"]
    added = 0
    last_added_message_id = ""
    for msg in messages:
        added_ref = await sdk.add_message(
            actual_session_id,
            msg.get("role") or "user",
            msg.get("content") or "",
            ctx=context,
            created_at=msg.get("created_at") or "",
            role_id=msg.get("role_id") or msg.get("role") or "",
        )
        last_added_message_id = str(added_ref.get("message_id") or last_added_message_id)
        added += 1
        print(
            "[message] "
            + json.dumps(
                {
                    "label": label,
                    "message_index": added,
                    "message_total": len(messages),
                    "role": msg.get("role") or "",
                    "role_id": msg.get("role_id") or "",
                    "speaker": msg.get("speaker") or msg.get("role_id") or "",
                    "dia_id": msg.get("dia_id") or "",
                    "content": compact(msg.get("content") or "", 260),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if added == len(messages) or added % 25 == 0:
            print(f"[verify] {label} added_total={added}/{len(messages)}", flush=True)
    before = await sdk.get_history(actual_session_id, ctx=context)
    started = time.time()
    if args.skip_session_commit:
        task = type("SkippedCommit", (), {"task_id": f"commit-{actual_session_id}-skipped", "status": "skipped"})()
    else:
        commit_timeout = max(1.0, float(args.commit_call_timeout_s))
        with hard_timeout(commit_timeout, f"commit_session({actual_session_id})"):
            task = await asyncio.wait_for(
                commit_session_full(sdk, actual_session_id, context),
                timeout=commit_timeout,
            )
    elapsed = time.time() - started
    auto_flush_on_message = str(os.environ.get("ECHOMEM_AUTO_FLUSH_ON_MESSAGE_PERSISTED", "true")).strip().lower() in {"1", "true", "yes", "on"}
    fast_import = bool(getattr(args, "defer_artifact_wait", False) or str(getattr(args, "import_wait_mode", "full")).lower() == "fast")
    if fast_import:
        atom_flush = {
            "available": True,
            "complete": False,
            "deferred": True,
            "elapsed_s": 0.0,
            "attempts": [],
            "atom_pipeline_index": -1,
            "expected_atom_pipeline_index": added - 1,
            "atom_last_extracted_turn_id": "",
            "atom_last_extracted_turn_id_ok": False,
            "artifacts_ready": False,
        }
        commit_artifacts = collect_commit_artifact_state(
            args,
            actual_session_id,
            expected_message_count=added,
            expected_last_message_id=last_added_message_id,
        )
        commit_artifacts["deferred"] = True
        commit_artifacts["wait_elapsed_s"] = 0.0
        print(
            "[commit] "
            f"{label} async_settling enabled commit_index={commit_artifacts.get('commit_index')}/{added - 1} "
            f"atom_pipeline_index={commit_artifacts.get('atom_pipeline_index')}/{added - 1}",
            flush=True,
        )
    elif auto_flush_on_message:
        atom_flush = await flush_atom_pipeline(
            args,
            sdk,
            actual_session_id,
            expected_message_count=added,
            expected_last_message_id=last_added_message_id,
        )
        if not atom_flush.get("complete"):
            last_attempt = (atom_flush.get("attempts") or [{}])[-1]
            print(
                "[warning] "
                f"{label} atom_flush_incomplete "
                f"atom_pipeline_index={atom_flush.get('atom_pipeline_index')}/{added - 1} "
                f"error={compact(last_attempt.get('error') or '', 300)}",
                flush=True,
            )
        commit_artifacts = await wait_for_commit_artifacts(
            args,
            actual_session_id,
            expected_message_count=added,
            expected_last_message_id=last_added_message_id,
        )
    else:
        atom_flush = {
            "available": True,
            "complete": True,
            "deferred": True,
            "skipped": True,
            "elapsed_s": 0.0,
            "attempts": [],
        }
        commit_artifacts = await wait_for_commit_artifacts(
            args,
            actual_session_id,
            expected_message_count=added,
            expected_last_message_id=last_added_message_id,
        )
    print(
        "[commit] "
        f"{label} complete={commit_artifacts.get('complete')} "
        f"commit_index={commit_artifacts.get('commit_index')}/{added - 1} "
        f"atom_pipeline_index={commit_artifacts.get('atom_pipeline_index')}/{added - 1} "
        f"flush_complete={atom_flush.get('complete')}",
        flush=True,
    )
    after = await sdk.get_history(actual_session_id, ctx=context)
    commit_status = str(getattr(task, "status", "") or "").lower()
    archive_complete = bool(
        commit_artifacts.get("legacy_commit_complete")
        or commit_artifacts.get("complete")
        or (
            fast_import
            and len(before) == len(messages)
            and commit_status in {"accepted", "pending", "queued", "running", "succeeded", "completed", "ok"}
        )
    )
    atom_memory_complete = bool(atom_flush.get("complete"))
    retrieval_ready = bool(commit_artifacts.get("retrieval_ready"))
    cursor_complete = bool(commit_artifacts.get("cursor_complete"))
    session_complete = bool(
        len(before) == len(messages)
        and archive_complete
        and atom_memory_complete
        and retrieval_ready
        and cursor_complete
    )
    integrity = (
        "complete"
        if session_complete
        else (
            "pending_async_memory"
            if archive_complete and (fast_import or retrieval_ready or atom_memory_complete or cursor_complete)
            else ("partial" if archive_complete or retrieval_ready or atom_memory_complete else "incomplete")
        )
    )
    v005_commit_pending = (
        commit_status == "pending"
        and bool(commit_artifacts.get("atom_pipeline_index_ok"))
        and bool(commit_artifacts.get("vector_ready"))
    )
    return {
        "session_id": actual_session_id,
        "requested_session_id": session_id,
        "expected_messages": len(messages),
        "submitted_messages": added,
        "live_message_count_before_commit": len(before),
        "pending_message_count_after_commit": len(after),
        "live_complete_before_commit": len(before) == len(messages),
        "archive_complete_after_commit": archive_complete,
        "atom_memory_complete_after_commit": atom_memory_complete,
        "retrieval_ready_after_commit": retrieval_ready,
        "cursor_complete_after_commit": cursor_complete,
        "qa_ready_after_commit": session_complete,
        "pending_async_memory_after_commit": bool(integrity == "pending_async_memory"),
        "last_added_message_id": last_added_message_id,
        "commit_keep_recent_count": 0,
        "session_commit_skipped": bool(args.skip_session_commit),
        "atom_flush": atom_flush,
        "commit_artifacts": commit_artifacts,
        "commit_warning": (
            "EchoMemory returned commit task status=pending, but strict QA-ready artifacts are already complete."
            if v005_commit_pending
            else "Retrieval artifacts are available, but atom flush or extraction cursor has not fully caught up yet; keep waiting before QA."
            if retrieval_ready and (not atom_memory_complete or not cursor_complete)
            else "Fast import only waits for message persistence and commit acceptance; atom/graph generation continues asynchronously in EchoMemory."
            if fast_import and archive_complete and not session_complete
            else "Session commit was skipped; integrity is based on persisted messages and atom pipeline artifacts."
            if args.skip_session_commit
            else ""
        ),
        "integrity": integrity,
        "integrity_stage": (
            "qa_ready"
            if session_complete
            else (
                "cursor_complete"
                if cursor_complete
                else (
                    "atom_memory_complete"
                    if atom_memory_complete
                    else ("retrieval_ready" if retrieval_ready else ("async_memory_pending" if integrity == "pending_async_memory" else ("archive_complete" if archive_complete else "incomplete")))
                )
            )
        ),
        "create_response": created,
        "commit_response": {"task_id": getattr(task, "task_id", ""), "status": getattr(task, "status", "accepted"), "elapsed_s": round(elapsed, 4)},
    }


async def import_sample(
    args: argparse.Namespace,
    sdk: Any,
    sample_index: int,
    sample: dict[str, Any],
    out_dir: Path,
    *,
    echomem_root: Path,
    config_path: Path,
) -> dict[str, Any]:
    sample_id = str(sample.get("sample_id") or f"sample_{sample_index}")
    session_batches, estimated_tokens = build_session_batches(sample)
    original_session_count = len(session_batches)
    session_start = int(args.session_start or 0)
    session_end = int(args.session_end or 0)
    if session_start > 0 or session_end > 0:
        filtered_batches = []
        for batch in session_batches:
            number = session_number(batch["session_key"])
            if session_start > 0 and number < session_start:
                continue
            if session_end > 0 and number > session_end:
                continue
            filtered_batches.append(batch)
        session_batches = filtered_batches
        estimated_tokens = sum(
            token_estimate(msg["content"])
            for batch in session_batches
            for msg in batch["messages"]
        )
    if int(args.max_sessions or 0) > 0:
        session_batches = session_batches[: int(args.max_sessions)]
    print(
        f"[import] sample={sample_id} mode={args.session_mode} "
        f"sessions={len(session_batches)} session_start={session_start or ''} session_end={session_end or ''}",
        flush=True,
    )
    records = []
    for batch in session_batches:
        if args.session_mode == "locomo":
            suffix = batch["session_key"].replace("session_", "s")
            session_id = f"echomem-locomo-{sample_id}-{suffix}-{uuid.uuid4().hex[:8]}"
            label = f"{sample_id}/{batch['session_key']}"
            messages = batch["messages"]
        else:
            break
        try:
            rec = await import_one_session(args, sdk, session_id, messages, label)
        except Exception as exc:
            print(f"[error] {label} import_failed={type(exc).__name__}: {compact(exc, 500)}", flush=True)
            rec = {
                "session_id": session_id,
                "requested_session_id": session_id,
                "expected_messages": len(messages),
                "submitted_messages": 0,
                "live_message_count_before_commit": 0,
                "pending_message_count_after_commit": 0,
                "live_complete_before_commit": False,
                "archive_complete_after_commit": False,
                "last_added_message_id": "",
                "commit_keep_recent_count": 0,
                "atom_flush": {"complete": False, "error": compact(exc, 500)},
                "commit_artifacts": {"complete": False, "error": compact(exc, 500)},
                "integrity": "incomplete",
                "error": f"{type(exc).__name__}: {compact(exc, 500)}",
            }
        rec["session_key"] = batch["session_key"]
        rec["date_time"] = batch["date_time"]
        records.append(rec)
        write_running_summary(
            out_dir,
            args,
            echomem_root,
            config_path,
            [
                summarize_sample_progress(
                    args,
                    sample_index,
                    sample_id,
                    original_session_count,
                    session_batches,
                    estimated_tokens,
                    records,
                )
            ],
        )
        if rec.get("integrity") not in {"complete", "pending_async_memory"} and not args.continue_on_session_error:
            print(f"[error] {label} failed; stopping sample import early", flush=True)
            break
    if args.session_mode == "single":
        messages = [msg for batch in session_batches for msg in batch["messages"]]
        try:
            rec = await import_one_session(args, sdk, f"echomem-locomo-{sample_id}-{uuid.uuid4().hex[:8]}", messages, sample_id)
        except Exception as exc:
            print(f"[error] {sample_id} import_failed={type(exc).__name__}: {compact(exc, 500)}", flush=True)
            rec = {
                "session_id": f"echomem-locomo-{sample_id}-failed-{uuid.uuid4().hex[:8]}",
                "requested_session_id": f"echomem-locomo-{sample_id}-failed",
                "expected_messages": len(messages),
                "submitted_messages": 0,
                "live_message_count_before_commit": 0,
                "pending_message_count_after_commit": 0,
                "live_complete_before_commit": False,
                "archive_complete_after_commit": False,
                "last_added_message_id": "",
                "commit_keep_recent_count": 0,
                "atom_flush": {"complete": False, "error": compact(exc, 500)},
                "commit_artifacts": {"complete": False, "error": compact(exc, 500)},
                "integrity": "incomplete",
                "error": f"{type(exc).__name__}: {compact(exc, 500)}",
            }
        rec["session_key"] = "all"
        rec["date_time"] = ""
        records.append(rec)
        write_running_summary(
            out_dir,
            args,
            echomem_root,
            config_path,
            [
                summarize_sample_progress(
                    args,
                    sample_index,
                    sample_id,
                    original_session_count,
                    session_batches,
                    estimated_tokens,
                    records,
                )
            ],
        )
    write_json(out_dir / f"{sample_id}_messages.json", session_batches)
    return summarize_sample_progress(
        args,
        sample_index,
        sample_id,
        original_session_count,
        session_batches,
        estimated_tokens,
        records,
    )


async def run(args: argparse.Namespace) -> None:
    root = ensure_echomem_imports(args.echomem_root)
    try:
        from echomem.protocol.local_sdk.sdk import EchoMemSDK
        from echomem.runtime.runtime import open_runtime
    except ModuleNotFoundError:
        from echomem.entrypoints.plugins.echoagent.sdk import EchoMemSDK
        from echomem.runtime.bootstrap import open_runtime

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.echomem_config).expanduser().resolve() if args.echomem_config else write_echomem_config(
        out_dir,
        args.account,
        args.workspace,
        root,
        args.fallback_to_mock,
    )
    # Keep downstream readiness checks aligned with the runtime file that was
    # actually used for this import run. Without this, helper functions like
    # abstract_required(args) fall back to default values even when the freshly
    # generated runtime config disables abstract generation.
    args.echomem_config = str(config_path)
    if hasattr(args, "_runtime_config_cache"):
        delattr(args, "_runtime_config_cache")
    if not args.skip_model_preflight and not args.fallback_to_mock:
        preflight = import_model_preflight(out_dir)
        print(
            "[preflight] embedding="
            f"{preflight.get('embedding', {}).get('status')} "
            f"chat={preflight.get('chat', {}).get('status')} "
            f"file={out_dir / 'echomemory_model_preflight.json'}",
            flush=True,
        )
        if preflight.get("status") != "ok":
            failures = []
            for label in ("embedding", "chat"):
                item = preflight.get(label) or {}
                if item.get("ok"):
                    continue
                failures.append(
                    f"{label}: {item.get('model') or '-'} @ {item.get('base_url') or '-'} "
                    f"status={item.get('status')} · {item.get('error') or 'unknown error'}"
                )
            raise SystemExit(
                "ECHOMEMORY_IMPORT_PREFLIGHT_FAILED: "
                + " | ".join(failures)
                + f" | details={out_dir / 'echomemory_model_preflight.json'}"
            )
    write_bootstrap_summary(
        out_dir,
        args,
        root,
        config_path,
        status="ECHOMEMORY_IMPORT_BOOTSTRAPPING",
        status_explanation="Model preflight passed. EchoMemory runtime is opening; no session has been written yet.",
        running=True,
    )
    runtime_open_timeout = max(1.0, float(args.runtime_open_timeout_s))
    print(
        f"[bootstrap] opening_runtime timeout_s={runtime_open_timeout:g} config={config_path}",
        flush=True,
    )
    runtime_started = time.time()
    try:
        with hard_timeout(runtime_open_timeout, f"open_runtime({config_path})"):
            runtime = await asyncio.wait_for(
                open_runtime(str(config_path)),
                timeout=runtime_open_timeout,
            )
    except Exception as exc:
        message = compact(exc, 500)
        write_bootstrap_summary(
            out_dir,
            args,
            root,
            config_path,
            status="ECHOMEMORY_IMPORT_BOOTSTRAP_FAILED",
            status_explanation="EchoMemory runtime failed before session import started.",
            running=False,
            error=message,
        )
        print(f"[error] runtime_open_failed={type(exc).__name__}: {message}", flush=True)
        raise
    print(
        f"[bootstrap] runtime_ready elapsed_s={time.time() - runtime_started:.3f}",
        flush=True,
    )
    sdk = EchoMemSDK(runtime)
    data = read_json(Path(args.dataset).expanduser().resolve())
    if not isinstance(data, list):
        raise ValueError("LoCoMo dataset must be a JSON list")
    samples = locomo_samples(data, args.sample)
    if not samples:
        raise ValueError(f"no LoCoMo sample matched: {args.sample}")
    print(f"[start] dataset={args.dataset} samples={len(samples)} backend=echomemory root={root}", flush=True)
    records = [
        await import_sample(
            args,
            sdk,
            index,
            sample,
            out_dir,
            echomem_root=root,
            config_path=config_path,
        )
        for index, sample in samples
    ]
    complete = sum(1 for item in records if item["integrity"] == "complete")
    pending_async = sum(1 for item in records if item["integrity"] == "pending_async_memory")
    partial = sum(1 for item in records if item["integrity"] == "partial")
    archive_complete = sum(1 for item in records if item.get("archive_complete_after_commit"))
    retrieval_ready = sum(1 for item in records if item.get("retrieval_ready_after_commit"))
    qa_ready = sum(1 for item in records if item.get("qa_ready_after_commit"))
    summary = build_import_summary(
        args,
        root,
        config_path,
        records,
        status=(
            "ECHOMEMORY_IMPORT_DONE"
            if qa_ready == len(records)
            else (
                "ECHOMEMORY_IMPORT_ASYNC_SETTLING"
                if qa_ready + pending_async == len(records) and pending_async
                else ("ECHOMEMORY_IMPORT_PARTIAL" if pending_async or partial or archive_complete or retrieval_ready else "ECHOMEMORY_IMPORT_INCOMPLETE")
            )
        ),
        status_explanation=(
            "All selected sessions are QA-ready; commit cursor, atom cursor, overview, abstract, and retrieval artifacts are all complete."
            if qa_ready == len(records)
            else (
                "All selected sessions have been written and commit has been triggered, but at least one session is still waiting for atom/cursor/graph consolidation."
                if qa_ready + pending_async == len(records) and pending_async
                else (
                    "Selected sessions are partially ready; some retrieval or archive artifacts exist, but not every session reached strict QA-ready state."
                    if pending_async or partial or archive_complete or retrieval_ready
                    else "Selected sessions did not fully commit."
                )
            )
        ),
        running=False,
    )
    write_json(out_dir / "echomemory_import_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if summary["incomplete_samples"] and not (summary["partial_samples"] or summary["pending_async_samples"]):
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import LoCoMo conversations into EchoMemory.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--workspace", default="/tmp/locomo-eval-echomemory")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--sample", default="all")
    parser.add_argument("--session-mode", choices=["locomo", "single"], default="locomo")
    parser.add_argument("--session-start", type=int, default=0, help="First LoCoMo session number to import, inclusive.")
    parser.add_argument("--session-end", type=int, default=0, help="Last LoCoMo session number to import, inclusive.")
    parser.add_argument("--max-sessions", type=int, default=0)
    parser.add_argument("--import-wait-mode", choices=["full", "fast"], default="full")
    parser.add_argument("--commit-wait-s", type=float, default=300.0)
    parser.add_argument("--commit-call-timeout-s", type=float, default=300.0)
    parser.add_argument("--flush-call-timeout-s", type=float, default=600.0)
    parser.add_argument("--flush-attempts", type=int, default=3)
    parser.add_argument("--runtime-open-timeout-s", type=float, default=180.0)
    parser.add_argument("--defer-artifact-wait", action="store_true", default=False)
    parser.add_argument("--skip-session-commit", action="store_true", default=False)
    parser.add_argument("--skip-model-preflight", action="store_true", default=False)
    parser.add_argument("--continue-on-session-error", action="store_true", default=False)
    parser.add_argument("--fallback-to-mock", action="store_true", default=False)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
