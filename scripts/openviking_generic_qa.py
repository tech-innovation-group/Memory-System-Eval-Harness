#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import importlib.util
import os
import re
import subprocess
import sys
import time
import uuid
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import benchmark_adapter
from openviking_locomo_import import OpenVikingHTTP, import_one_openviking_session
from openviking_memory_qa import (
    ModelCallError,
    call_openai,
    classify_model_error,
    compact,
    hit_score,
    openviking_find,
    openviking_post_json,
    openviking_read_content,
    token_estimate,
)

LONGMEMEVAL_TIME_FORMAT = "%Y/%m/%d (%a) %H:%M"


def safe_slug(value: Any, limit: int = 72) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-._")
    return (text or "sample")[:limit]


def build_longmemeval_agent_id(sample_id: str | int) -> str:
    digest = hashlib.md5(str(sample_id).encode("utf-8")).hexdigest()[:12]
    return f"lm_{digest}"


def build_longmemeval_user_id(sample_id: str | int) -> str:
    digest = hashlib.md5(f"user:{sample_id}".encode("utf-8")).hexdigest()[:12]
    return f"lm_user_{digest}"


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return [part.strip() for part in text.split(",") if part.strip()]
    return []


def csv_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(key for row in rows for key in row.keys()))


def read_dataset(path: Path) -> Any:
    return benchmark_adapter.read_dataset(path)


def build_jobs(fmt: str, data: Any, count: int, sample: str) -> tuple[list[benchmark_adapter.Job], list[dict[str, Any]]]:
    limit = count or None
    if fmt == "auto":
        raise ValueError("format must be resolved before build_jobs")
    if fmt == "locomo":
        return benchmark_adapter.locomo_jobs(data, limit, sample)
    if fmt == "longmemeval":
        return benchmark_adapter.longmemeval_jobs(data, limit, sample)
    if fmt == "hotpotqa":
        return benchmark_adapter.hotpotqa_jobs(data, limit, sample)
    return benchmark_adapter.generic_jobs(fmt, data, limit, sample)


def selected_question_ids(args: argparse.Namespace) -> set[str]:
    return {item.strip() for item in str(getattr(args, "questions", "") or "").split(",") if item.strip()}


def job_matches_question_filter(job: benchmark_adapter.Job, question_filter: set[str]) -> bool:
    if not question_filter:
        return True
    candidates = {
        str(job.question_id or "").strip(),
        str(job.native_question_id or "").strip(),
        str(job.sample_id or "").strip(),
    }
    return bool(question_filter.intersection(candidates))


def iter_job_plans(args: argparse.Namespace):
    limit = args.count or None
    question_filter = selected_question_ids(args)
    emitted = 0
    if args.dataset_format == "locomo":
        data = read_dataset(args.dataset_path)
        jobs, plans = benchmark_adapter.locomo_jobs(data, limit, args.sample, question_filter or None)
        for job, plan in zip(jobs, plans):
            emitted += 1
            yield emitted, job, plan
        return
    for raw_index, raw in benchmark_adapter.iter_payload_from_path(args.dataset_path):
        if args.dataset_format == "longmemeval":
            built = benchmark_adapter.longmemeval_job_plan(raw, raw_index, args.sample)
        elif args.dataset_format == "hotpotqa":
            built = benchmark_adapter.hotpotqa_job_plan(raw, raw_index, args.sample)
        else:
            built = benchmark_adapter.generic_job_plan(args.dataset_format, raw, raw_index, args.sample)
        if built is None:
            continue
        job, plan = built
        if not job_matches_question_filter(job, question_filter):
            continue
        emitted += 1
        yield emitted, job, plan
        if limit and emitted >= limit:
            return


def planned_job_count(args: argparse.Namespace) -> int | None:
    question_count = len(selected_question_ids(args))
    if question_count:
        return question_count
    if args.count:
        return args.count
    if args.sample not in ("", "all"):
        return None
    return benchmark_adapter.count_payload_items_from_path(args.dataset_path)


def event_messages(dataset_format: str, sample_id: str, namespace: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for index, event in enumerate(events, 1):
        time_text = str(event.get("time") or "").strip()
        text = str(event.get("text") or "").strip()
        if not text:
            continue
        content = (
            f"[benchmark memory]\n"
            f"dataset_format: {dataset_format}\n"
            f"sample_id: {sample_id}\n"
            f"namespace: {namespace}\n"
            f"event_index: {index}\n"
            f"time: {time_text or '-'}\n\n"
            f"{text}"
        )
        messages.append(
            {
                "role": "user",
                "content": content,
                "parts": [{"type": "text", "text": content}],
            }
        )
    return messages


def sample_identity(args: argparse.Namespace, sample_id: str) -> tuple[str, str]:
    if str(getattr(args, "dataset_format", "") or "").strip().lower() == "longmemeval":
        return build_longmemeval_user_id(sample_id), build_longmemeval_agent_id(sample_id)
    if args.identity_mode == "fixed":
        return args.user_id or "default", args.agent_id or "default"
    base = safe_slug(args.namespace, 40)
    sample = safe_slug(sample_id, 64)
    return f"{safe_slug(args.user_prefix)}-{base}-{sample}", f"{safe_slug(args.agent_prefix)}-{base}-{sample}"


def load_official_longmemeval_prompt_builder() -> Any | None:
    candidates = [
        Path.home() / "Code" / "openviking" / "versions" / "v0.4.4" / "benchmark" / "longmemeval" / "openviking" / "longmemeval_prompts.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("openviking_longmemeval_prompts", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            helper = getattr(module, "get_answer_generation_prompt", None)
            if callable(helper):
                return helper
        except Exception:
            continue
    return None


OFFICIAL_LONGMEMEVAL_PROMPT_BUILDER = load_official_longmemeval_prompt_builder()


def load_official_longmemeval_runner() -> Any | None:
    candidates = [
        Path.home() / "Code" / "openviking" / "versions" / "v0.4.4" / "benchmark" / "longmemeval" / "openviking" / "run_eval.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("openviking_longmemeval_run_eval", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            helper = getattr(module, "run_single_search_context_answer", None)
            if callable(helper):
                return helper
        except Exception:
            continue
    return None


OFFICIAL_LONGMEMEVAL_RUNNER = load_official_longmemeval_runner()


def parse_longmemeval_datetime(date_str: str) -> Any | None:
    try:
        from datetime import datetime

        return datetime.strptime(str(date_str or "").strip(), LONGMEMEVAL_TIME_FORMAT)
    except Exception:
        return None


def effective_document_import_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "document_import_mode", "") or "").strip().lower()
    if mode and mode != "auto":
        return mode
    if str(getattr(args, "dataset_format", "") or "").strip().lower() == "longmemeval":
        return "session_commit"
    return "source_documents"


def user_memory_uri(user_id: str) -> str:
    return f"viking://user/{user_id}/memories/"


def group_source_documents(documents: list[dict[str, str]], max_chars: int) -> list[dict[str, str]]:
    if max_chars <= 0 or not documents:
        return documents
    grouped: list[dict[str, str]] = []
    current: list[dict[str, str]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        start = current[0]
        end = current[-1]
        doc_id = f"{start.get('doc_id') or 'doc'}__to__{end.get('doc_id') or 'doc'}"
        title = f"{start.get('title') or start.get('doc_id') or 'doc'} -> {end.get('title') or end.get('doc_id') or 'doc'}"
        grouped.append(
            {
                "doc_id": doc_id,
                "title": title,
                "time": str(start.get("time") or ""),
                "text": "\n\n--- source document boundary ---\n\n".join(str(item.get("text") or "") for item in current),
            }
        )
        current = []
        current_chars = 0

    for doc in documents:
        text_len = len(str(doc.get("text") or ""))
        if current and current_chars + text_len > max_chars:
            flush()
        current.append(doc)
        current_chars += text_len
        if text_len >= max_chars:
            flush()
    flush()
    return grouped


def source_documents_from_plan(plan: dict[str, Any], args: argparse.Namespace | None = None) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for item in plan.get("memory_documents") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        documents.append(
            {
                "doc_id": str(item.get("doc_id") or item.get("title") or f"doc_{len(documents) + 1}"),
                "title": str(item.get("title") or item.get("doc_id") or f"doc_{len(documents) + 1}"),
                "time": str(item.get("time") or ""),
                "text": text,
            }
        )
    if documents:
        group_chars = int(getattr(args, "document_group_chars", 0) or 0) if args else 0
        dataset_format = str(getattr(args, "dataset_format", "") or "").lower() if args else ""
        if dataset_format == "longmemeval" and group_chars > 0:
            return group_source_documents(documents, group_chars)
        return documents

    events = list(plan.get("events") or [])
    chunk_size = 12
    for start in range(0, len(events), chunk_size):
        chunk = events[start : start + chunk_size]
        lines = ["Source memory events:"]
        first_time = ""
        for offset, event in enumerate(chunk, 1):
            if not isinstance(event, dict):
                continue
            event_time = str(event.get("time") or "").strip()
            if event_time and not first_time:
                first_time = event_time
            prefix = f"{start + offset}."
            if event_time:
                prefix += f" [{event_time}]"
            lines.append(f"{prefix} {event.get('text') or ''}")
        text = "\n".join(lines).strip()
        if text:
            documents.append(
                {
                    "doc_id": f"events_{start + 1:04d}_{start + len(chunk):04d}",
                    "title": f"events {start + 1}-{start + len(chunk)}",
                    "time": first_time,
                    "text": text,
                }
            )
    return documents


def benchmark_document_content(args: argparse.Namespace, sample_id: str, index: int, doc: dict[str, str]) -> str:
    lines = [
        "[benchmark source memory]",
        f"dataset_format: {args.dataset_format}",
        f"sample_id: {sample_id}",
        f"namespace: {args.namespace}",
        f"document_index: {index}",
        f"document_id: {doc.get('doc_id') or '-'}",
        f"title: {doc.get('title') or '-'}",
        f"time: {doc.get('time') or '-'}",
        "",
        str(doc.get("text") or "").strip(),
    ]
    return "\n".join(lines).strip() + "\n"


def benchmark_document_uri(args: argparse.Namespace, user_id: str, sample_id: str, index: int, doc: dict[str, str]) -> str:
    namespace = safe_slug(args.namespace, 48)
    fmt = safe_slug(args.dataset_format, 24)
    sample = safe_slug(sample_id, 48)
    doc_id = safe_slug(doc.get("doc_id") or doc.get("title") or f"doc-{index}", 48)
    return f"viking://user/{user_id}/memories/events/benchmark/{namespace}/{fmt}/{sample}/{index:04d}-{doc_id}.md"


def write_openviking_content(args: argparse.Namespace, user_id: str, agent_id: str, uri: str, content: str) -> dict[str, Any]:
    mode = "create"
    last_error = ""
    for attempt in range(max(1, args.document_write_retries + 1)):
        payload = {
            "uri": uri,
            "content": content,
            "mode": mode,
            "wait": bool(args.document_write_wait),
            "timeout": max(30, args.timeout_s),
        }
        try:
            return openviking_post_json(
                args.openviking_url,
                "/api/v1/content/write",
                payload,
                args.account,
                user_id,
                agent_id,
                args.openviking_api_key,
                timeout=max(45, args.timeout_s),
            )
        except Exception as exc:
            message = str(exc)
            last_error = message
            lower = message.lower()
            if mode == "create" and ("409" in message or "already" in lower or "exists" in lower):
                mode = "replace"
                continue
            if "resource is busy" not in lower and "busy" not in lower:
                raise
            read_args = argparse.Namespace(**vars(args))
            read_args.user_id = user_id
            read_args.agent_id = agent_id
            existing = openviking_read_content(read_args, uri, timeout=max(30, args.timeout_s))
            if existing and "[benchmark source memory]" in existing and len(existing) >= max(100, len(content) // 3):
                print(f"[document-memory] busy but readable uri={uri}", flush=True)
                return {"context_type": "content", "semantic_status": "readable_after_busy", "vector_status": "busy_readable"}
            sleep_s = min(20.0, 1.5 * (attempt + 1))
            print(f"[document-memory] busy retry={attempt + 1}/{args.document_write_retries} uri={uri} sleep={sleep_s:.1f}s", flush=True)
            time.sleep(sleep_s)
    raise RuntimeError(last_error or f"OpenViking content/write failed for {uri}")


def materialize_benchmark_documents(
    args: argparse.Namespace,
    sample_id: str,
    plan: dict[str, Any],
    user_id: str,
    agent_id: str,
    import_dir: Path,
) -> dict[str, Any]:
    documents = source_documents_from_plan(plan, args)
    if not documents:
        return {
            "document_memory_status": "no_documents",
            "document_memory_count": 0,
            "document_memory_uris": [],
            "document_memory_errors": [],
            "document_memory_tokens_est": 0,
        }

    written: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, doc in enumerate(documents, 1):
        uri = benchmark_document_uri(args, user_id, sample_id, index, doc)
        content = benchmark_document_content(args, sample_id, index, doc)
        try:
            if args.document_write_interval_s and index > 1:
                time.sleep(max(0.0, float(args.document_write_interval_s)))
            result = write_openviking_content(args, user_id, agent_id, uri, content)
            written.append(
                {
                    "doc_id": doc.get("doc_id") or f"doc_{index}",
                    "title": doc.get("title") or "",
                    "time": doc.get("time") or "",
                    "uri": uri,
                    "bytes": len(content.encode("utf-8")),
                    "result": {
                        "context_type": result.get("context_type") if isinstance(result, dict) else "",
                        "semantic_status": result.get("semantic_status") if isinstance(result, dict) else "",
                        "vector_status": result.get("vector_status") if isinstance(result, dict) else "",
                    },
                }
            )
        except Exception as exc:
            errors.append(f"{uri}: {compact(str(exc), 300)}")
    (import_dir / f"{safe_slug(sample_id)}_documents.json").write_text(json.dumps(written, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "document_memory_status": "ok" if written and not errors else ("partial" if written else "failed"),
        "document_memory_count": len(written),
        "document_memory_uris": [item["uri"] for item in written],
        "document_memory_errors": errors,
        "document_memory_tokens_est": sum(token_estimate(benchmark_document_content(args, sample_id, index, doc)) for index, doc in enumerate(documents, 1)),
    }


def ensure_document_memory(
    args: argparse.Namespace,
    sample_id: str,
    plan: dict[str, Any],
    record: dict[str, Any],
    import_dir: Path,
) -> dict[str, Any]:
    if not args.document_memory:
        return record
    import_mode = effective_document_import_mode(args)
    if import_mode == "session_commit":
        return record
    existing_uris = json_list(record.get("document_memory_uris"))
    if existing_uris and str(record.get("document_memory_status") or "") in {"ok", "partial"}:
        return record
    user_id = str(record.get("user_id") or sample_identity(args, sample_id)[0])
    agent_id = str(record.get("agent_id") or sample_identity(args, sample_id)[1])
    doc_record = materialize_benchmark_documents(args, sample_id, plan, user_id, agent_id, import_dir)
    record.update(doc_record)
    doc_status = str(doc_record.get("document_memory_status") or "")
    if doc_record.get("document_memory_count"):
        if doc_status == "ok":
            record["status"] = "OPENVIKING_DOCUMENT_IMPORT_DONE"
            record["integrity"] = "complete"
        elif doc_status == "partial":
            record["status"] = "OPENVIKING_DOCUMENT_IMPORT_PARTIAL"
            record["integrity"] = "partial"
        else:
            record["status"] = str(record.get("status") or "OPENVIKING_IMPORT_DONE")
        record["memory_uri"] = user_memory_uri(user_id)
    elif doc_status == "failed" and record.get("integrity") != "complete":
        record["status"] = "OPENVIKING_IMPORT_FAILED"
        record["integrity"] = "failed"
    return record


def import_sample_memory(
    args: argparse.Namespace,
    sample_id: str,
    plan: dict[str, Any],
    import_dir: Path,
) -> dict[str, Any]:
    user_id, agent_id = sample_identity(args, sample_id)
    events = list(plan.get("events") or [])
    import_mode = effective_document_import_mode(args)
    if args.document_memory and import_mode == "source_documents" and source_documents_from_plan(plan, args):
        return {
            "sample_id": sample_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "status": "OPENVIKING_DOCUMENT_IMPORT_PENDING",
            "integrity": "source_documents",
            "expected_messages": 0,
            "submitted_messages": 0,
            "session_id": "",
            "memory_uri": user_memory_uri(user_id),
            "estimated_import_tokens": 0,
            "error": "",
        }
    session_batches = list(plan.get("session_batches") or [])
    if import_mode == "session_commit" and session_batches:
        client = OpenVikingHTTP(
            args.openviking_url,
            args.openviking_api_key,
            args.account,
            user_id,
            agent_id,
            args.timeout_s,
        )
        session_records: list[dict[str, Any]] = []
        estimated_import_tokens = 0
        try:
            for batch in session_batches:
                batch_messages = list(batch.get("messages") or [])
                session_key = str(batch.get("session_key") or f"session_{len(session_records) + 1}")
                if not batch_messages:
                    continue
                estimated_import_tokens += sum(token_estimate(str(msg.get("content") or "")) for msg in batch_messages)
                session_id = (
                    f"longmemeval-{safe_slug(sample_id, 40)}-{safe_slug(session_key, 40)}-{uuid.uuid4().hex[:8]}"
                )
                rec = import_one_openviking_session(
                    args,
                    client,
                    session_id,
                    batch_messages,
                    f"{args.dataset_format}/{sample_id}/{session_key}",
                )
                rec["session_key"] = session_key
                rec["date_time"] = str(batch.get("date_time") or "")
                session_records.append(rec)
            if not session_records:
                raise RuntimeError("no session batches were imported")
            expected_messages = sum(int(item.get("expected_messages") or 0) for item in session_records)
            submitted_messages = sum(int(item.get("submitted_messages") or 0) for item in session_records)
            pending_messages = sum(int(item.get("pending_message_count_after_commit") or 0) for item in session_records)
            integrity = (
                "complete"
                if session_records and all(str(item.get("integrity") or "") == "complete" for item in session_records)
                else "incomplete"
            )
            record = {
                "sample_id": sample_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "status": "OPENVIKING_IMPORT_DONE" if integrity == "complete" else "OPENVIKING_IMPORT_INCOMPLETE",
                "integrity": integrity,
                "session_id": session_records[0]["session_id"] if len(session_records) == 1 else f"longmemeval-{safe_slug(sample_id, 40)}-*",
                "session_count": len(session_records),
                "session_records": session_records,
                "expected_messages": expected_messages,
                "submitted_messages": submitted_messages,
                "live_message_count_before_commit": expected_messages,
                "pending_message_count_after_commit": pending_messages,
                "live_complete_before_commit": submitted_messages == expected_messages,
                "archive_complete_after_commit": pending_messages == 0,
                "memory_uri": user_memory_uri(user_id),
                "estimated_import_tokens": estimated_import_tokens,
                "error": "",
                "document_import_mode_used": import_mode,
            }
            (import_dir / f"{safe_slug(sample_id)}_messages.json").write_text(
                json.dumps(session_batches, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return record
        except Exception as exc:
            return {
                "sample_id": sample_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "status": "OPENVIKING_IMPORT_FAILED",
                "integrity": "failed",
                "expected_messages": sum(len(list(batch.get("messages") or [])) for batch in session_batches),
                "submitted_messages": 0,
                "session_id": "",
                "memory_uri": user_memory_uri(user_id),
                "estimated_import_tokens": sum(
                    token_estimate(str(msg.get("content") or ""))
                    for batch in session_batches
                    for msg in list(batch.get("messages") or [])
                ),
                "error": str(exc),
                "document_import_mode_used": import_mode,
            }

    messages = event_messages(args.dataset_format, sample_id, args.namespace, events)
    if not messages:
        return {
            "sample_id": sample_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "status": "NO_EVENTS",
            "integrity": "no_events",
            "expected_messages": 0,
            "submitted_messages": 0,
            "session_id": "",
            "error": "no memory events recognized for sample",
        }
    client = OpenVikingHTTP(
        args.openviking_url,
        args.openviking_api_key,
        args.account,
        user_id,
        agent_id,
        args.timeout_s,
    )
    session_id = f"generic-{safe_slug(args.namespace, 36)}-{safe_slug(args.dataset_format, 24)}-{safe_slug(sample_id, 52)}-{uuid.uuid4().hex[:8]}"
    try:
        record = import_one_openviking_session(args, client, session_id, messages, f"{args.dataset_format}/{sample_id}")
        record.update(
            {
                "sample_id": sample_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "status": "OPENVIKING_IMPORT_DONE" if record.get("integrity") == "complete" else "OPENVIKING_IMPORT_INCOMPLETE",
                "memory_uri": user_memory_uri(user_id),
                "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
                "document_import_mode_used": import_mode,
            }
        )
        (import_dir / f"{safe_slug(sample_id)}_messages.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        return record
    except Exception as exc:
        return {
            "sample_id": sample_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "status": "OPENVIKING_IMPORT_FAILED",
            "integrity": "failed",
            "expected_messages": len(messages),
            "submitted_messages": 0,
            "session_id": session_id,
            "memory_uri": user_memory_uri(user_id),
            "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
            "error": str(exc),
            "document_import_mode_used": import_mode,
        }


def read_hit_content(args: argparse.Namespace, uri: str, user_id: str, agent_id: str) -> str:
    if not args.read_openviking_content or not uri:
        return ""
    ctx_args = argparse.Namespace(**vars(args))
    ctx_args.user_id = user_id
    ctx_args.agent_id = agent_id
    try:
        return openviking_read_content(ctx_args, uri, timeout=max(30, args.timeout_s))
    except Exception:
        return ""


SCAFFOLD_MEMORY_MARKERS = (
    "Access when reviewing user history",
    "Use this directory to access user's personalized memories",
    "User's long-term memory storage",
    "Preferences organized by topic",
    "Each entity stored independently",
    "Events are historical records",
    "Contains memory types like preferences",
)


def is_scaffold_memory_hit(uri: str, text: str) -> bool:
    if not uri:
        return False
    basename = uri.rsplit("/", 1)[-1]
    if basename not in {".overview.md", ".abstract.md"}:
        return False
    return any(marker.lower() in text.lower() for marker in SCAFFOLD_MEMORY_MARKERS)


def candidate_search_limit(top_k: int) -> int:
    return max(top_k, min(80, top_k * 6))


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "had", "has",
    "have", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "to", "was", "were",
    "what", "when", "where", "which", "who", "whom", "why", "with",
    "about", "after", "before", "current", "date", "tell", "that", "this", "your",
}


def query_terms(job: benchmark_adapter.Job) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{1,}", str(job.question or "").lower())
    return [
        term
        for term in terms
        if term not in STOPWORDS
        and len(term) > 2
        and not re.fullmatch(r"\d+", term)
    ]


def lexical_doc_score(job: benchmark_adapter.Job, text: str) -> float:
    lower = text.lower()
    terms = query_terms(job)
    if not terms:
        return 0.0
    score = 0.0
    for term in terms:
        count = lower.count(term)
        if count:
            score += min(4, count)
    bigrams = zip(terms, terms[1:])
    for first, second in bigrams:
        if f"{first} {second}" in lower:
            score += 3
    return score / max(1, len(terms))


def document_header(text: str, limit: int = 420) -> str:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            if lines:
                break
            continue
        if line.lower().startswith(("conversation turns:", "turn_")):
            break
        lines.append(line)
        if sum(len(item) for item in lines) > limit:
            break
    return compact("\n".join(lines), limit)


def text_windows(text: str, window_chars: int, overlap_chars: int) -> list[tuple[int, str]]:
    source = str(text or "")
    if len(source) <= window_chars:
        return [(0, source)] if source else []
    step = max(200, window_chars - overlap_chars)
    windows: list[tuple[int, str]] = []
    for start in range(0, len(source), step):
        chunk = source[start:start + window_chars]
        if chunk.strip():
            windows.append((start, chunk))
        if start + window_chars >= len(source):
            break
    return windows


def window_score(terms: list[str], chunk: str) -> float:
    lower = chunk.lower()
    score = 0.0
    for term in terms:
        count = lower.count(term)
        if count:
            score += min(5, count) * (1.0 + min(8, len(term)) / 10.0)
    for first, second in zip(terms, terms[1:]):
        if f"{first} {second}" in lower:
            score += 4.0
    return score


def best_document_snippet(job: benchmark_adapter.Job, content: str, limit: int) -> str:
    source = str(content or "")
    if not source:
        return ""
    if len(source) <= limit:
        return compact(source, limit)
    terms = query_terms(job)
    header = document_header(source)
    chunk_limit = max(700, limit - len(header) - 120)
    windows = text_windows(source, min(max(chunk_limit, 1200), 2400), 450)
    if not windows:
        return compact(source, limit)
    ranked = sorted(
        ((window_score(terms, chunk), start, chunk) for start, chunk in windows),
        key=lambda item: (item[0], -item[1]),
        reverse=True,
    )
    selected: list[tuple[int, str]] = []
    used = len(header) + 80 if header else 0
    positive = [item for item in ranked if item[0] > 0] or ranked[:1]
    for score, start, chunk in positive:
        snippet = compact(chunk, max(500, min(chunk_limit, limit - used - 40)))
        if not snippet:
            continue
        block = f"[snippet offset={start} score={score:.2f}]\n{snippet}"
        if selected and used + len(block) + 20 > limit:
            continue
        selected.append((start, block))
        used += len(block) + 20
        if len(selected) >= 2:
            break
    parts = [header] if header else []
    parts.extend(block for _, block in sorted(selected, key=lambda item: item[0]))
    return compact("\n\n".join(part for part in parts if part), limit)


def document_fallback_items(
    args: argparse.Namespace,
    job: benchmark_adapter.Job,
    import_record: dict[str, Any],
    user_id: str,
    agent_id: str,
) -> tuple[list[dict[str, Any]], str]:
    if not args.document_memory:
        return [], ""
    uris = [str(uri) for uri in json_list(import_record.get("document_memory_uris")) if str(uri or "").strip()]
    if not uris:
        return [], ""
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    for uri in uris:
        content = read_hit_content(args, uri, user_id, agent_id)
        if not content:
            errors.append(f"empty_read:{uri}")
            continue
        score = lexical_doc_score(job, content)
        snippet = best_document_snippet(job, content, args.evidence_item_chars)
        candidates.append(
            {
                "uri": uri,
                "score": score,
                "source": "openviking_content_read_document_fallback",
                "abstract": snippet,
                "target_uri": user_memory_uri(user_id),
                "scaffold": False,
            }
        )
    candidates.sort(key=lambda item: (float(item.get("score") or 0), len(str(item.get("abstract") or ""))), reverse=True)
    if not candidates:
        return [], "; ".join(errors[:5])
    positives = [item for item in candidates if float(item.get("score") or 0) > 0]
    return (positives or candidates)[: args.top_k], "; ".join(errors[:5])


def evidence_items(
    args: argparse.Namespace,
    job: benchmark_adapter.Job,
    import_record: dict[str, Any],
    user_id: str,
    agent_id: str,
) -> tuple[list[dict[str, Any]], str]:
    target_uri = user_memory_uri(user_id)
    last_error = ""
    search_limit = candidate_search_limit(args.top_k)
    try:
        hits = openviking_find(
            args.openviking_url,
            question_prompt(job),
            args.account,
            user_id,
            agent_id,
            args.openviking_api_key,
            search_limit,
            target_uri,
            args.retrieval_retries,
        )
    except Exception as exc:
        hits = []
        last_error = str(exc)
    candidates: list[dict[str, Any]] = []
    for item in sorted(hits, key=hit_score, reverse=True)[:search_limit]:
        uri = str(item.get("uri") or item.get("path") or item.get("id") or "")
        content = read_hit_content(args, uri, user_id, agent_id)
        abstract = str(item.get("abstract") or item.get("content") or item.get("text") or item.get("summary") or "")
        text = best_document_snippet(job, content, args.evidence_item_chars) if content else compact(abstract, args.evidence_item_chars)
        candidates.append(
            {
                "uri": uri,
                "score": item.get("score") or item.get("similarity") or 0,
                "source": item.get("source") or "openviking_search",
                "abstract": text,
                "target_uri": target_uri,
                "scaffold": is_scaffold_memory_hit(uri, text),
            }
        )
    specific = [item for item in candidates if not item.get("scaffold")]
    doc_error = ""
    if len(specific) < args.top_k or not specific:
        doc_items, doc_error = document_fallback_items(args, job, import_record, user_id, agent_id)
        seen_uris = {str(item.get("uri") or "") for item in specific}
        for item in doc_items:
            if str(item.get("uri") or "") not in seen_uris:
                specific.append(item)
                seen_uris.add(str(item.get("uri") or ""))
    chosen = (specific or candidates)[: args.top_k]
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(chosen, 1):
        item = dict(item)
        item["rank"] = index
        item.pop("scaffold", None)
        normalized.append(item)
    if doc_error:
        last_error = "; ".join(part for part in [last_error, f"document_fallback={doc_error}"] if part)
    return normalized, last_error


def evidence_block(items: list[dict[str, Any]], max_chars: int) -> str:
    if not items:
        return "(no OpenViking memories retrieved)"
    blocks: list[str] = []
    total = 0
    for item in items:
        text = (
            f"<memory rank=\"{item.get('rank')}\" score=\"{item.get('score')}\" uri=\"{item.get('uri')}\">\n"
            f"{item.get('abstract') or ''}\n"
            f"</memory>"
        )
        needed = len(text) + (2 if blocks else 0)
        if blocks and total + needed > max_chars:
            break
        blocks.append(text)
        total += needed
    return "\n\n".join(blocks)


def question_prompt(job: benchmark_adapter.Job) -> str:
    if job.query_time and job.query_time != "-":
        return f"Current date: {job.query_time}. {job.question}"
    return job.question


def job_question_type(job: benchmark_adapter.Job) -> str:
    return str(getattr(job, "question_type", "") or getattr(job, "category", "") or "").strip()


def build_answer_messages(
    job: benchmark_adapter.Job,
    evidence: str,
    items: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], str]:
    if (
        str(job.dataset_format or "").strip().lower() == "longmemeval"
        and callable(OFFICIAL_LONGMEMEVAL_PROMPT_BUILDER)
    ):
        search_results = []
        for item in items or []:
            memory = str(item.get("abstract") or item.get("content") or "").strip()
            if not memory:
                continue
            search_results.append(
                {
                    "memory": memory,
                    "score": item.get("score", 0.0),
                    "raw_rank": item.get("rank"),
                }
            )
        prompt = OFFICIAL_LONGMEMEVAL_PROMPT_BUILDER(
            question=job.question,
            search_results=search_results,
            question_date=job.query_time or "unknown",
        )
        question_type = job_question_type(job)
        if question_type:
            prompt = f"Question Type: {question_type}\n\n{prompt}"
        return [{"role": "user", "content": prompt}], "official_longmemeval_v044_prompt"
    system = (
        "You are answering a formal memory benchmark question.\n"
        "Use only the retrieved OpenViking memories provided in the user message.\n"
        "Do not use outside knowledge, hidden context, or the gold answer.\n"
        "If the retrieved memories do not contain enough information, answer exactly: unknown.\n"
        "Keep the answer concise and factual."
    )
    user = (
        f"Dataset: {job.dataset_format}\n"
        f"Sample: {job.sample_id}\n"
        f"Question: {question_prompt(job)}\n\n"
        f"Retrieved OpenViking memories:\n{evidence}\n\n"
        "Answer:"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], "strict_openviking_memory"


def simple_grade(expected: str, actual: str) -> str:
    return benchmark_adapter.simple_grade(expected, actual)


def answer_job(
    args: argparse.Namespace,
    job: benchmark_adapter.Job,
    import_record: dict[str, Any],
) -> dict[str, Any]:
    started = time.time()
    user_id = str(import_record.get("user_id") or sample_identity(args, job.sample_id)[0])
    agent_id = str(import_record.get("agent_id") or sample_identity(args, job.sample_id)[1])
    target_uri = user_memory_uri(user_id)
    if (
        str(job.dataset_format or "").strip().lower() == "longmemeval"
        and callable(OFFICIAL_LONGMEMEVAL_RUNNER)
    ):
        (
            response,
            token_usage,
            time_cost,
            iteration,
            tools_used_names,
            retrieved_uris_by_iteration,
            model_input_prompt,
        ) = OFFICIAL_LONGMEMEVAL_RUNNER(
            question=job.question,
            question_type=job_question_type(job),
            question_time=job.query_time,
            sender_id=user_id,
            session_id=agent_id,
            openviking_url=args.openviking_url,
            timeout=args.timeout_s,
            single_search_context_limit=int(getattr(args, "single_search_context_limit", 10) or 10),
            single_search_rerank_limit=int(getattr(args, "single_search_rerank_limit", 10) or 10),
            single_search_max_context_chars=int(getattr(args, "single_search_max_context_chars", 4000) or 4000),
            debug_print_model_input=bool(getattr(args, "debug_print_model_input", False)),
        )
        response = str(response or "").strip()
        model_error = response if response.startswith("[SINGLE SEARCH ERROR]") else ""
        retrieval_lists = list(retrieved_uris_by_iteration or [])
        retrieval_ok = any((item.get("context_uris") or item.get("retrieved_uris") or []) for item in retrieval_lists if isinstance(item, dict))
        answer_ok = bool(response) and response.lower() != "unknown" and not model_error
        health = "ok" if retrieval_ok and answer_ok and not model_error else ("retrieval_empty" if not retrieval_ok else "model_failed")
        relevant_memory = []
        for payload in retrieval_lists:
            if not isinstance(payload, dict):
                continue
            for uri in payload.get("context_uris") or payload.get("retrieved_uris") or []:
                relevant_memory.append({"uri": uri})
        return {
            **benchmark_adapter.asdict(job),
            "response": response,
            "simple_grade": simple_grade(job.answer, response),
            "result": "",
            "reasoning": "; ".join(
                [
                    "official OpenViking LongMemEval single_search_context",
                    f"backend=openviking",
                    f"import_status={import_record.get('status')}",
                    "pending judge",
                ]
            ),
            "time_cost": f"{time_cost:.4f}",
            "backend": "openviking",
            "eval_engine": "openviking_generic_qa",
            "namespace": args.namespace,
            "dataset_path": str(args.dataset_path),
            "memory_uri": target_uri,
            "qa_user_id": user_id,
            "qa_agent_id": agent_id,
            "identity_mode": args.identity_mode,
            "import_session_id": str(import_record.get("session_id") or ""),
            "import_status": str(import_record.get("status") or ""),
            "import_integrity": str(import_record.get("integrity") or ""),
            "import_expected_messages": str(import_record.get("expected_messages") or 0),
            "import_submitted_messages": str(import_record.get("submitted_messages") or 0),
            "import_error": str(import_record.get("error") or ""),
            "document_memory_status": str(import_record.get("document_memory_status") or ""),
            "document_memory_count": str(import_record.get("document_memory_count") or 0),
            "document_memory_uris": json.dumps(json_list(import_record.get("document_memory_uris")), ensure_ascii=False),
            "document_memory_errors": json.dumps(json_list(import_record.get("document_memory_errors")), ensure_ascii=False),
            "relevant_memory": json.dumps(relevant_memory, ensure_ascii=False),
            "retrieval_query_plan": json.dumps([question_prompt(job)], ensure_ascii=False),
            "retrieval_mode": "official_openviking_single_search_context",
            "retrieval_count": str(sum(len((item.get("context_uris") or item.get("retrieved_uris") or [])) for item in retrieval_lists if isinstance(item, dict))),
            "memory_hit_count": str(sum(len((item.get("context_uris") or item.get("retrieved_uris") or [])) for item in retrieval_lists if isinstance(item, dict))),
            "archive_fallback_count": "0",
            "retrieval_tokens_est": str(int(token_usage.get("memory_prompt_tokens") or 0)),
            "context_preview": compact(json.dumps(retrieval_lists, ensure_ascii=False), 3000),
            "prompt_mode": "official_longmemeval_v044_prompt",
            "openviking_tool_loop_enabled": "false",
            "openviking_tool_set": json.dumps(tools_used_names, ensure_ascii=False),
            "openviking_content_read_enabled": bool_text(True),
            "prompt_message_count": "1",
            "prompt_preview": compact(model_input_prompt, 5000),
            "answer_prompt_tokens": str(token_usage.get("prompt_tokens") or 0),
            "answer_completion_tokens": str(token_usage.get("completion_tokens") or 0),
            "answer_total_tokens": str(token_usage.get("total_tokens") or 0),
            "model_status": "ok" if not model_error else "failed",
            "model_retry_count": "0",
            "model_error_kind": "single_search_error" if model_error else "",
            "model_error": model_error,
            "retrieval_status": "ok" if retrieval_ok else "empty",
            "retrieval_error": model_error,
            "answer_status": "ok" if answer_ok else ("failed" if model_error else "empty_or_unknown"),
            "health_status": health,
            "tools_used_names": json.dumps(tools_used_names, ensure_ascii=False),
            "retrieved_uris_by_iteration": json.dumps(retrieved_uris_by_iteration, ensure_ascii=False),
            "iteration": str(iteration),
        }

    items, retrieval_error = evidence_items(args, job, import_record, user_id, agent_id)
    retrieval_ok = bool(items)
    evidence = evidence_block(items, args.evidence_chars)
    messages, prompt_mode = build_answer_messages(job, evidence, items)
    model_result: dict[str, Any]
    if args.answer_token:
        try:
            model_result = call_openai(
                args.answer_base_url,
                args.answer_model,
                args.answer_token,
                messages,
                args.timeout_s,
                args.model_retries,
            )
        except ModelCallError as exc:
            model_result = {
                "answer": "",
                "prompt_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                "completion_tokens": 0,
                "total_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                "model_retry_count": exc.retry_count,
                "model_error_kind": exc.error_kind,
                "model_error": str(exc),
            }
    else:
        model_result = {
            "answer": "",
            "prompt_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
            "completion_tokens": 0,
            "total_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
            "model_retry_count": 0,
            "model_error_kind": "no_answer_token",
            "model_error": "answer token is missing; no model call was made",
        }
    response = str(model_result.get("answer") or "").strip()
    model_ok = bool(response) and not model_result.get("model_error_kind")
    answer_ok = bool(response) and response.lower() != "unknown"
    if retrieval_error:
        health = "retrieval_error"
    elif retrieval_ok and model_ok:
        health = "ok"
    else:
        health = "retrieval_empty" if not retrieval_ok else str(model_result.get("model_error_kind") or "model_failed")
    reasoning_parts = [
        "formal OpenViking generic QA",
        f"backend=openviking",
        f"import_status={import_record.get('status')}",
        "pending judge",
    ]
    if retrieval_error:
        reasoning_parts.append(f"retrieval_error={compact(retrieval_error, 220)}")
    if model_result.get("model_error"):
        reasoning_parts.append(f"model_error={compact(model_result.get('model_error'), 220)}")
    return {
        **benchmark_adapter.asdict(job),
        "response": response,
        "simple_grade": simple_grade(job.answer, response),
        "result": "",
        "reasoning": "; ".join(reasoning_parts),
        "time_cost": f"{time.time() - started:.4f}",
        "backend": "openviking",
        "eval_engine": "openviking_generic_qa",
        "namespace": args.namespace,
        "dataset_path": str(args.dataset_path),
        "memory_uri": target_uri,
        "qa_user_id": user_id,
        "qa_agent_id": agent_id,
        "identity_mode": args.identity_mode,
        "import_session_id": str(import_record.get("session_id") or ""),
        "import_status": str(import_record.get("status") or ""),
        "import_integrity": str(import_record.get("integrity") or ""),
        "import_expected_messages": str(import_record.get("expected_messages") or 0),
        "import_submitted_messages": str(import_record.get("submitted_messages") or 0),
        "import_error": str(import_record.get("error") or ""),
        "document_memory_status": str(import_record.get("document_memory_status") or ""),
        "document_memory_count": str(import_record.get("document_memory_count") or 0),
        "document_memory_uris": json.dumps(json_list(import_record.get("document_memory_uris")), ensure_ascii=False),
        "document_memory_errors": json.dumps(json_list(import_record.get("document_memory_errors")), ensure_ascii=False),
        "relevant_memory": json.dumps(items, ensure_ascii=False),
        "retrieval_query_plan": json.dumps([question_prompt(job)], ensure_ascii=False),
        "retrieval_mode": "openviking_search_find_plus_document_read_fallback" if any(str(item.get("source") or "") == "openviking_content_read_document_fallback" for item in items) else "openviking_search_find",
        "retrieval_count": str(len(items)),
        "memory_hit_count": str(len(items)),
        "archive_fallback_count": "0",
        "retrieval_tokens_est": str(token_estimate(evidence)),
        "context_preview": compact(evidence, 3000),
        "prompt_mode": prompt_mode,
        "openviking_tool_loop_enabled": "false",
        "openviking_tool_set": "search_find_content_read",
        "openviking_content_read_enabled": bool_text(bool(args.read_openviking_content)),
        "prompt_message_count": str(len(messages)),
        "prompt_preview": compact(json.dumps(messages, ensure_ascii=False), 5000),
        "answer_prompt_tokens": str(model_result.get("prompt_tokens") or 0),
        "answer_completion_tokens": str(model_result.get("completion_tokens") or 0),
        "answer_total_tokens": str(model_result.get("total_tokens") or 0),
        "model_status": "ok" if model_ok else "failed",
        "model_retry_count": str(model_result.get("model_retry_count") or 0),
        "model_error_kind": str(model_result.get("model_error_kind") or ""),
        "model_error": str(model_result.get("model_error") or ""),
        "retrieval_status": "ok" if retrieval_ok else "empty",
        "retrieval_error": retrieval_error,
        "answer_status": "ok" if answer_ok else ("failed" if model_result.get("model_error_kind") else "empty_or_unknown"),
        "health_status": health,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = csv_fieldnames(rows) if rows else list(benchmark_adapter.Job.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def running_summary_payload(rows: list[dict[str, Any]], *, status: str, csv_path: Path) -> dict[str, Any]:
    sums = {
        "memory_injection_time_s": 0.0,
        "memory_settle_wait_elapsed_s": 0.0,
        "qa_time_s": 0.0,
        "end_to_end_time_s": 0.0,
    }
    counts = {key: 0 for key in sums}
    last_question_id = ""
    for row in rows:
        last_question_id = str(row.get("question_id") or row.get("native_question_id") or row.get("sample_id") or last_question_id)
        for key in ("memory_injection_time_s", "memory_settle_wait_elapsed_s", "end_to_end_time_s"):
            value = safe_float(row.get(key))
            if value is None:
                continue
            sums[key] += value
            counts[key] += 1
        qa_value = safe_float(row.get("qa_time_s"))
        if qa_value is None:
            qa_value = safe_float(row.get("time_cost"))
        if qa_value is not None:
            sums["qa_time_s"] += qa_value
            counts["qa_time_s"] += 1

    def avg(key: str) -> float | None:
        count = counts[key]
        return round(sums[key] / count, 4) if count else None

    def total(key: str) -> float | None:
        return round(sums[key], 4) if count_values(key) else None

    def count_values(key: str) -> int:
        return counts[key]

    return {
        "rows": len(rows),
        "last_question_id": last_question_id,
        "total_memory_injection_time_s": total("memory_injection_time_s"),
        "avg_memory_injection_time_s": avg("memory_injection_time_s"),
        "total_memory_settle_wait_time_s": total("memory_settle_wait_elapsed_s"),
        "avg_memory_settle_wait_time_s": avg("memory_settle_wait_elapsed_s"),
        "total_qa_time_s": total("qa_time_s"),
        "avg_qa_time_s": avg("qa_time_s"),
        "total_end_to_end_time_s": total("end_to_end_time_s"),
        "avg_end_to_end_time_s": avg("end_to_end_time_s"),
        "status": status,
        "csv_path": str(csv_path),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_running_summary(path: Path, rows: list[dict[str, Any]], *, status: str, csv_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(running_summary_payload(rows, status=status, csv_path=csv_path), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_existing_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("question_id") or row.get("sample_id") or row.get("native_question_id") or "").strip()


def job_key(job: benchmark_adapter.Job) -> str:
    return str(job.question_id or job.sample_id or "").strip()


def should_retry_row(row: dict[str, Any], retry_failed: bool, retry_empty_answers: bool = False) -> bool:
    if not retry_failed:
        return False
    health = str(row.get("health_status") or "").lower()
    model_status = str(row.get("model_status") or "").lower()
    answer_status = str(row.get("answer_status") or "").lower()
    response = str(row.get("response") or "").strip()
    retrieval_error = str(row.get("retrieval_error") or "").strip()
    return (
        (retry_empty_answers and not response)
        or bool(retrieval_error)
        or model_status == "failed"
        or answer_status == "failed"
        or (retry_empty_answers and answer_status == "empty_or_unknown")
        or health not in {"", "ok"}
    )


def import_record_from_row(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    sample_id = str(row.get("sample_id") or "").strip()
    if not sample_id:
        return None
    user_id = str(row.get("qa_user_id") or sample_identity(args, sample_id)[0])
    agent_id = str(row.get("qa_agent_id") or sample_identity(args, sample_id)[1])
    return {
        "sample_id": sample_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "status": row.get("import_status") or "OPENVIKING_IMPORT_RESUMED",
        "integrity": row.get("import_integrity") or "unknown",
        "session_id": row.get("import_session_id") or "",
        "memory_uri": row.get("memory_uri") or user_memory_uri(user_id),
        "expected_messages": row.get("import_expected_messages") or 0,
        "submitted_messages": row.get("import_submitted_messages") or 0,
        "error": row.get("import_error") or "",
        "document_memory_status": row.get("document_memory_status") or "",
        "document_memory_count": row.get("document_memory_count") or 0,
        "document_memory_uris": json_list(row.get("document_memory_uris")),
        "document_memory_errors": json_list(row.get("document_memory_errors")),
        "resumed_from_csv": True,
    }


def load_existing_import_records(import_dir: Path, existing_rows: list[dict[str, str]], args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    summary_path = import_dir / "openviking_generic_import_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for item in summary.get("records") or []:
                sample_id = str(item.get("sample_id") or "").strip()
                if sample_id:
                    records[sample_id] = item
        except Exception as exc:
            print(f"[resume] could not read import summary: {exc}", flush=True)
    for row in existing_rows:
        item = import_record_from_row(row, args)
        if item and item["sample_id"] not in records:
            records[item["sample_id"]] = item
    return records


def run_judge(args: argparse.Namespace, csv_path: Path) -> dict[str, Any]:
    if not args.judge_after:
        return {"enabled": False}
    if str(getattr(args, "dataset_format", "") or "").strip().lower() == "hotpotqa":
        return {"enabled": False, "reason": "not_applicable_for_hotpotqa"}
    base_url = args.judge_base_url or args.answer_base_url
    model = args.judge_model or args.answer_model
    token = args.judge_token or args.answer_token
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "local_judge.py"),
        "--input",
        str(csv_path),
        "--base-url",
        base_url,
        "--model",
        model,
        "--parallel",
        str(args.judge_parallel),
        "--timeout-s",
        str(args.timeout_s),
        "--retries",
        str(args.model_retries),
    ]
    env = os.environ.copy()
    if token:
        env["LOCOMO_JUDGE_TOKEN"] = token
    print(f"[judge] enabled=true model={model} base_url={base_url or '-'} token={'set' if token else 'missing; heuristic fallback'}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\n"), flush=True)
    rc = proc.wait()
    summary_path = csv_path.parent / "judge_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return {"enabled": True, "returncode": rc, "summary": summary, "summary_path": str(summary_path)}


def run_official_eval(args: argparse.Namespace, csv_path: Path) -> dict[str, Any]:
    if not args.official_eval_after:
        return {"enabled": False}
    script_dir = Path(__file__).resolve().parent
    env = os.environ.copy()
    token = args.judge_token or args.answer_token
    if token:
        env["LOCOMO_JUDGE_TOKEN"] = token
    if args.dataset_format == "longmemeval":
        cmd = [
            sys.executable,
            str(script_dir / "longmemeval_official_eval.py"),
            "--csv",
            str(csv_path),
            "--reference",
            str(args.dataset_path),
            "--out-dir",
            str(csv_path.parent),
            "--base-url",
            args.judge_base_url or args.answer_base_url,
            "--model",
            args.judge_model or args.answer_model,
            "--parallel",
            str(args.judge_parallel),
            "--timeout-s",
            str(args.timeout_s),
            "--retries",
            str(args.model_retries),
            "--only-missing",
        ]
        summary_path = csv_path.parent / "longmemeval_official_summary.json"
    elif args.dataset_format == "hotpotqa":
        cmd = [
            sys.executable,
            str(script_dir / "hotpotqa_answer_eval.py"),
            "--csv",
            str(csv_path),
            "--reference",
            str(args.dataset_path),
            "--out-dir",
            str(csv_path.parent),
        ]
        summary_path = csv_path.parent / "hotpotqa_answer_summary.json"
    else:
        return {
            "enabled": False,
            "reason": f"no official scorer wired for dataset_format={args.dataset_format}",
        }

    print(f"[official-eval] enabled=true format={args.dataset_format}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\n"), flush=True)
    rc = proc.wait()
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return {"enabled": True, "returncode": rc, "summary": summary, "summary_path": str(summary_path)}


def official_metric_summary(dataset_format: str, official_eval: dict[str, Any]) -> dict[str, Any]:
    summary = official_eval.get("summary") if isinstance(official_eval, dict) else {}
    if not isinstance(summary, dict) or not summary:
        return {}
    if dataset_format == "longmemeval":
        return {
            "official_metric": "overall_accuracy",
            "official_score": summary.get("overall_accuracy"),
            "official_task_averaged_accuracy": summary.get("task_averaged_accuracy"),
            "official_abstention_accuracy": summary.get("abstention_accuracy"),
        }
    if dataset_format == "hotpotqa":
        return {
            "official_metric": "answer_f1",
            "official_score": summary.get("answer_f1"),
            "official_answer_em": summary.get("answer_em"),
            "official_answer_f1": summary.get("answer_f1"),
            "official_metric_scope": summary.get("metric_scope"),
        }
    return {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run formal generic memory benchmarks through OpenViking import, retrieval, LLM answer, and optional Judge.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--format", dest="dataset_format", default="auto")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="all")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--questions", default="", help="Comma-separated question_id/native_question_id/sample_id list to run exactly.")
    parser.add_argument("--namespace", default="")
    parser.add_argument("--openviking-url", default="http://127.0.0.1:1933")
    parser.add_argument("--openviking-api-key", default="")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--identity-mode", choices=["isolated_sample", "fixed"], default="isolated_sample")
    parser.add_argument("--user-prefix", default="eval-user")
    parser.add_argument("--agent-prefix", default="eval-agent")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--retrieval-retries", type=int, default=2)
    parser.add_argument("--read-openviking-content", dest="read_openviking_content", action="store_true", default=True)
    parser.add_argument("--no-read-openviking-content", dest="read_openviking_content", action="store_false")
    parser.add_argument("--document-memory", dest="document_memory", action="store_true", default=True)
    parser.add_argument("--no-document-memory", dest="document_memory", action="store_false")
    parser.add_argument("--document-write-wait", action="store_true", default=True)
    parser.add_argument("--no-document-write-wait", dest="document_write_wait", action="store_false")
    parser.add_argument("--document-write-retries", type=int, default=20)
    parser.add_argument("--document-write-interval-s", type=float, default=1.0)
    parser.add_argument("--document-import-mode", choices=["auto", "source_documents", "session_commit", "both"], default="auto")
    parser.add_argument("--document-group-chars", type=int, default=60000, help="Group LongMemEval source documents into larger OpenViking content writes; 0 disables grouping.")
    parser.add_argument("--single-search-context-limit", type=int, default=10)
    parser.add_argument("--single-search-rerank-limit", type=int, default=10)
    parser.add_argument("--single-search-max-context-chars", type=int, default=4000)
    parser.add_argument("--debug-print-model-input", action="store_true")
    parser.add_argument("--evidence-chars", type=int, default=9000)
    parser.add_argument("--evidence-item-chars", type=int, default=1800)
    parser.add_argument("--answer-base-url", default=os.environ.get("JUDGE_BASE_URL", ""))
    parser.add_argument("--answer-model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--judge-after", action="store_true")
    parser.add_argument("--official-eval-after", action="store_true")
    parser.add_argument("--import-only", action="store_true", help="Only write benchmark memory documents and import summary; skip QA/model/Judge.")
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--judge-token", default="")
    parser.add_argument("--judge-parallel", type=int, default=4)
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--commit-timeout-s", type=int, default=300)
    parser.add_argument("--wait-commit", action="store_true", default=True)
    parser.add_argument("--resume", dest="resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--retry-failed", action="store_true", help="Re-run existing rows whose model/retrieval/answer status is not healthy.")
    parser.add_argument("--retry-empty-answers", action="store_true", help="Also re-run existing rows whose model returned an empty or unknown answer.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.dataset_path = Path(args.dataset).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    import_dir = out_dir / "openviking_import"
    import_dir.mkdir(parents=True, exist_ok=True)
    if not args.namespace:
        args.namespace = f"{args.dataset_format}-{int(time.time())}-{uuid.uuid4().hex[:6]}"

    if args.dataset_format == "auto":
        data = read_dataset(args.dataset_path)
        args.dataset_format = benchmark_adapter.infer_format(args.dataset_path, data)
    total_jobs = planned_job_count(args)
    total_label = str(total_jobs) if total_jobs is not None else "?"
    csv_path = out_dir / "openviking_generic_qa_results.csv"
    running_summary_path = out_dir / "running_summary.json"
    existing_rows = load_existing_csv(csv_path) if args.resume else []
    existing_by_key = {row_key(row): row for row in existing_rows if row_key(row)}
    resumed_existing_rows = len(existing_by_key)
    skipped_existing_rows = 0
    rerun_existing_rows = 0

    print(
        f"[qa] dataset={args.dataset_path} format={args.dataset_format} jobs={total_label} "
        f"openviking={args.openviking_url} namespace={args.namespace}",
        flush=True,
    )
    if existing_by_key:
        print(f"[resume] existing_rows={len(existing_by_key)} retry_failed={bool(args.retry_failed)} csv={csv_path}", flush=True)
    import_records: dict[str, dict[str, Any]] = load_existing_import_records(import_dir, existing_rows, args) if args.resume else {}
    rows: list[dict[str, Any]] = []
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
    processed_jobs = 0
    for index, job, plan in iter_job_plans(args):
        processed_jobs = index
        key = job_key(job)
        existing = existing_by_key.get(key)
        if existing and not should_retry_row(existing, args.retry_failed, args.retry_empty_answers):
            rows.append(existing)
            skipped_existing_rows += 1
            if job.sample_id not in import_records:
                item = import_record_from_row(existing, args)
                if item:
                    import_records[job.sample_id] = item
            print(f"[resume] skip {index}/{total_label} {job.question_id}", flush=True)
            continue
        if existing:
            rerun_existing_rows += 1
            print(f"[resume] retry {index}/{total_label} {job.question_id}", flush=True)
        if job.sample_id not in import_records:
            print(f"[import] {index}/{total_label} sample={job.sample_id} events={len(plan.get('events') or [])}", flush=True)
            import_records[job.sample_id] = import_sample_memory(args, job.sample_id, plan, import_dir)
        elif args.document_import_mode == "both" and str(import_records[job.sample_id].get("resumed_from_csv") or "").lower() == "true":
            import_records[job.sample_id] = import_sample_memory(args, job.sample_id, plan, import_dir)
        import_records[job.sample_id] = ensure_document_memory(
            args,
            job.sample_id,
            plan,
            import_records[job.sample_id],
            import_dir,
        )
        if args.import_only:
            doc_count = import_records[job.sample_id].get("document_memory_count") or 0
            print(f"[import-only] {index}/{total_label} sample={job.sample_id} document_memory_count={doc_count}", flush=True)
            continue
        print(f"[qa] {index}/{total_label} {job.question_id} {compact(job.question, 90)}", flush=True)
        try:
            row = answer_job(args, job, import_records[job.sample_id])
        except Exception as exc:
            user_id, agent_id = sample_identity(args, job.sample_id)
            row = {
                **benchmark_adapter.asdict(job),
                "response": "",
                "simple_grade": "NEEDS_JUDGE",
                "result": "",
                "reasoning": f"[QA ERROR] {exc}",
                "time_cost": "0",
                "backend": "openviking",
                "eval_engine": "openviking_generic_qa",
                "namespace": args.namespace,
                "dataset_path": str(args.dataset_path),
                "memory_uri": user_memory_uri(user_id),
                "qa_user_id": user_id,
                "qa_agent_id": agent_id,
                "identity_mode": args.identity_mode,
                "relevant_memory": "[]",
                "retrieval_count": "0",
                "memory_hit_count": "0",
                "archive_fallback_count": "0",
                "retrieval_tokens_est": "0",
                "answer_total_tokens": "0",
                "model_status": "failed",
                "model_error_kind": classify_model_error(str(exc)),
                "model_error": str(exc),
                "retrieval_status": "unknown",
                "answer_status": "failed",
                "health_status": classify_model_error(str(exc)),
            }
        rows.append(row)
        write_csv(csv_path, rows)
        write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)

    if processed_jobs == 0:
        raise SystemExit(f"no jobs found in {args.dataset_path}")
    write_csv(csv_path, rows)
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
    judge_result = {"enabled": False, "reason": "import_only"} if args.import_only else run_judge(args, csv_path)
    official_eval_result = {"enabled": False, "reason": "import_only"} if args.import_only else run_official_eval(args, csv_path)

    import_summary = {
        "status": "OPENVIKING_GENERIC_IMPORT_DONE",
        "records": list(import_records.values()),
        "samples": len(import_records),
        "complete_samples": sum(1 for item in import_records.values() if item.get("integrity") == "complete"),
        "failed_samples": sum(1 for item in import_records.values() if item.get("integrity") == "failed"),
        "no_event_samples": sum(1 for item in import_records.values() if item.get("integrity") == "no_events"),
        "expected_messages": sum(int(item.get("expected_messages") or 0) for item in import_records.values()),
        "submitted_messages": sum(int(item.get("submitted_messages") or 0) for item in import_records.values()),
        "estimated_import_tokens": sum(int(item.get("estimated_import_tokens") or 0) for item in import_records.values()),
    }
    (import_dir / "openviking_generic_import_summary.json").write_text(json.dumps(import_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    tool_counts: Counter = Counter()
    health_counts: Counter = Counter(str(row.get("health_status") or "unknown") for row in rows)
    for row in rows:
        try:
            tool_counts.update(json.loads(str(row.get("tool_call_name_counts") or "{}")))
        except Exception:
            pass
    judged_summary = judge_result.get("summary") if isinstance(judge_result, dict) else {}
    if not isinstance(judged_summary, dict):
        judged_summary = {}
    summary = {
        "status": "OPENVIKING_GENERIC_IMPORT_ONLY_DONE" if args.import_only else "OPENVIKING_GENERIC_QA_DONE",
        "dataset_format": args.dataset_format,
        "dataset": str(args.dataset_path),
        "sample": args.sample,
        "count": len(rows),
        "rows": len(rows),
        "output_csv": str(csv_path),
        "openviking_url": args.openviking_url,
        "account": args.account,
        "namespace": args.namespace,
        "identity_mode": args.identity_mode,
        "backend": "openviking",
        "eval_engine": "openviking_generic_qa",
        "answer_model": args.answer_model,
        "judge_after": bool(args.judge_after),
        "judge": judge_result,
        "official_eval_after": bool(args.official_eval_after),
        "official_eval": official_eval_result,
        "graded": judged_summary.get("graded"),
        "correct": judged_summary.get("correct"),
        "wrong": judged_summary.get("wrong"),
        "accuracy": judged_summary.get("accuracy"),
        "import_summary": import_summary,
        "import_summary_path": str(import_dir / "openviking_generic_import_summary.json"),
        "retrieval_ok_count": sum(1 for row in rows if row.get("retrieval_status") == "ok"),
        "retrieval_empty_count": sum(1 for row in rows if row.get("retrieval_status") == "empty"),
        "model_ok_count": sum(1 for row in rows if row.get("model_status") == "ok"),
        "model_failed_count": sum(1 for row in rows if row.get("model_status") == "failed"),
        "answer_ok_count": sum(1 for row in rows if row.get("answer_status") == "ok"),
        "answer_total_tokens": sum(int(row.get("answer_total_tokens") or 0) for row in rows),
        "retrieval_tokens_est": sum(int(row.get("retrieval_tokens_est") or 0) for row in rows),
        "memory_hit_total": sum(int(row.get("memory_hit_count") or 0) for row in rows),
        "avg_retrieval_count": round(sum(int(row.get("retrieval_count") or 0) for row in rows) / len(rows), 2) if rows else 0,
        "health_counts": dict(health_counts),
        "tool_name_counts": dict(tool_counts),
        "resume_enabled": bool(args.resume),
        "resumed_existing_rows": resumed_existing_rows,
        "skipped_existing_rows": skipped_existing_rows,
        "rerun_existing_rows": rerun_existing_rows,
    }
    summary.update(official_metric_summary(args.dataset_format, official_eval_result))
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    judge_failed = judge_result.get("enabled") and int(judge_result.get("returncode") or 0) != 0
    official_failed = official_eval_result.get("enabled") and int(official_eval_result.get("returncode") or 0) != 0
    final_status = "failed" if (judge_failed or official_failed) else "succeeded"
    write_running_summary(running_summary_path, rows, status=final_status, csv_path=csv_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if judge_failed or official_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
