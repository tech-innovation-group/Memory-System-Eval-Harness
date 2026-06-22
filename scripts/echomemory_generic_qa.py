#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import sys
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import benchmark_adapter
from echomemory_common import (
    DEFAULT_ECHOMEM_ROOT,
    ensure_echomem_imports,
    sdk_ctx_kwargs,
    workspace_token_usage_summary,
    write_echomem_config,
    write_json,
)
from echomemory_locomo_import import import_one_session, token_estimate
from echomemory_memory_qa import (
    ECHOMEMORY_BACKEND_ROUTE,
    VIKINGBOT_ALIGNED_PROMPT_MODES,
    answer_question,
    csv_fieldnames,
    normalize_echomemory_tool_set,
    normalize_retrieval_mode,
    token_usage_json,
)
from memory.vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_ALIGNMENT_PROFILE,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)
from openviking_generic_qa import (
    import_record_from_row,
    load_existing_csv,
    row_key,
    run_judge,
    run_official_eval,
    should_retry_row,
    official_metric_summary,
)
from echomemory_wait_and_eval import (
    expected_session_count,
    require_memory_ready_or_exit,
    run_and_log,
    wait_for_async_memory_stability,
    write_status,
)


def safe_slug(value: Any, limit: int = 72) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip()).strip("-._")
    return (text or "sample")[:limit]


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


def read_dataset(path: Path) -> Any:
    return benchmark_adapter.read_dataset(path)


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


def sample_identity(args: argparse.Namespace, sample_id: str) -> tuple[str, str]:
    if args.identity_mode == "fixed":
        return args.user_id or "default", args.agent_id or "default"
    base = safe_slug(args.namespace, 40)
    sample = safe_slug(sample_id, 64)
    return f"{safe_slug(args.user_prefix)}-{base}-{sample}", f"{safe_slug(args.agent_prefix)}-{base}-{sample}"


def normalized_created_at(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y/%m/%d (%a) %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return ""


def import_messages_from_plan(dataset_format: str, sample_id: str, namespace: str, plan: dict[str, Any]) -> list[dict[str, Any]]:
    documents = list(plan.get("memory_documents") or [])
    if documents:
        messages: list[dict[str, Any]] = []
        for index, doc in enumerate(documents, 1):
            time_text = str(doc.get("time") or "").strip()
            created_at = normalized_created_at(time_text)
            title = str(doc.get("title") or doc.get("doc_id") or f"document_{index}").strip()
            text = str(doc.get("text") or "").strip()
            if not text:
                continue
            if dataset_format == "longmemeval" and "Conversation turns:" in text:
                current_session_id = title or f"session_{index}"
                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("source_dataset:") or line.startswith("session_id:") or line.startswith("time:"):
                        continue
                    if line == "Conversation turns:":
                        continue
                    if not line.startswith("turn_"):
                        continue
                    prefix, _, content = line.partition(":")
                    turn_label = prefix.strip()
                    role = turn_label.split()[-1] if " " in turn_label else "message"
                    content = content.strip()
                    if not content:
                        continue
                    message_text = (
                        f"[benchmark memory]\n"
                        f"dataset_format: {dataset_format}\n"
                        f"sample_id: {sample_id}\n"
                        f"namespace: {namespace}\n"
                        f"document_index: {index}\n"
                        f"title: {title or '-'}\n"
                        f"time: {time_text or '-'}\n"
                        f"session_id: {current_session_id}\n\n"
                        f"[session_date={time_text or '-'}] [{role}] {current_session_id} {turn_label}: {content}"
                    )
                    messages.append(
                        {
                            "role": "assistant" if str(role).lower() in {"assistant", "agent"} else "user",
                            "content": message_text,
                            "created_at": created_at,
                            "role_id": str(role),
                            "speaker": str(role),
                            "dia_id": f"{sample_id}:doc:{index}:{turn_label}",
                        }
                    )
                continue
            content = (
                f"[benchmark memory]\n"
                f"dataset_format: {dataset_format}\n"
                f"sample_id: {sample_id}\n"
                f"namespace: {namespace}\n"
                f"document_index: {index}\n"
                f"title: {title or '-'}\n"
                f"time: {time_text or '-'}\n\n"
                f"{text}"
            )
            messages.append(
                {
                    "role": "user",
                    "content": content,
                    "created_at": created_at,
                    "role_id": "benchmark_memory",
                    "speaker": "benchmark_memory",
                    "dia_id": f"{sample_id}:doc:{index}",
                }
            )
        if messages:
            return messages
    messages = []
    for index, event in enumerate(list(plan.get("events") or []), 1):
        time_text = str(event.get("time") or "").strip()
        created_at = normalized_created_at(time_text)
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
                "created_at": created_at,
                "role_id": "benchmark_memory",
                "speaker": "benchmark_memory",
                "dia_id": f"{sample_id}:event:{index}",
            }
        )
    return messages


async def import_sample_memory(
    args: argparse.Namespace,
    sdk: Any,
    sample_id: str,
    plan: dict[str, Any],
    import_dir: Path,
) -> dict[str, Any]:
    started = time.time()
    user_id, agent_id = sample_identity(args, sample_id)
    messages = import_messages_from_plan(args.dataset_format, sample_id, args.namespace, plan)
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
            "memory_uri": f"echo://{args.account}/memories/",
            "estimated_import_tokens": 0,
            "error": "no memory events recognized for sample",
        }
    requested_session_id = f"generic-{safe_slug(args.namespace, 36)}-{safe_slug(args.dataset_format, 24)}-{safe_slug(sample_id, 52)}-{uuid.uuid4().hex[:8]}"
    import_args = argparse.Namespace(**vars(args))
    import_args.user_id = user_id
    import_args.agent_id = agent_id
    record = await import_one_session(import_args, sdk, requested_session_id, messages, f"{args.dataset_format}/{sample_id}")
    record.update(
        {
            "sample_id": sample_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "status": "ECHOMEMORY_IMPORT_DONE" if record.get("integrity") == "complete" else "ECHOMEMORY_IMPORT_INCOMPLETE",
            "memory_uri": f"echo://{args.account}/memories/",
            "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
            "requested_session_id": requested_session_id,
            "import_elapsed_s": round(time.time() - started, 4),
            "import_commit_elapsed_s": round(float((record.get("commit_response") or {}).get("elapsed_s") or 0.0), 4),
            "import_flush_elapsed_s": round(float((record.get("atom_flush") or {}).get("elapsed_s") or 0.0), 4),
            "import_artifact_wait_elapsed_s": round(float((record.get("commit_artifacts") or {}).get("wait_elapsed_s") or 0.0), 4),
        }
    )
    write_json(import_dir / f"{safe_slug(sample_id)}_messages.json", messages)
    return record


def import_record_summary(import_records: dict[str, dict[str, Any]], workspace: str, account: str) -> dict[str, Any]:
    summary = {
        "status": "ECHOMEMORY_GENERIC_IMPORT_DONE",
        "records": list(import_records.values()),
        "samples": len(import_records),
        "complete_samples": sum(1 for item in import_records.values() if item.get("integrity") == "complete"),
        "pending_async_samples": sum(1 for item in import_records.values() if item.get("integrity") == "pending_async_memory"),
        "partial_samples": sum(1 for item in import_records.values() if item.get("integrity") == "partial"),
        "failed_samples": sum(1 for item in import_records.values() if item.get("integrity") == "failed"),
        "no_event_samples": sum(1 for item in import_records.values() if item.get("integrity") == "no_events"),
        "expected_messages": sum(int(item.get("expected_messages") or 0) for item in import_records.values()),
        "submitted_messages": sum(int(item.get("submitted_messages") or 0) for item in import_records.values()),
        "estimated_import_tokens": sum(int(item.get("estimated_import_tokens") or 0) for item in import_records.values()),
        "memory_injection_time_s_total": round(sum(float(item.get("memory_injection_time_s") or item.get("import_elapsed_s") or 0.0) for item in import_records.values()), 4),
        "memory_settle_wait_time_s_total": round(sum(float(item.get("memory_settle_wait_elapsed_s") or 0.0) for item in import_records.values()), 4),
        "repair_time_s_total": round(sum(float(item.get("repair_elapsed_s") or 0.0) for item in import_records.values()), 4),
    }
    sample_count = max(1, len(import_records)) if import_records else 0
    if sample_count:
        summary["avg_memory_injection_time_s"] = round(float(summary["memory_injection_time_s_total"]) / sample_count, 4)
        summary["avg_memory_settle_wait_time_s"] = round(float(summary["memory_settle_wait_time_s_total"]) / sample_count, 4)
        summary["avg_repair_time_s"] = round(float(summary["repair_time_s_total"]) / sample_count, 4)
    summary.update(workspace_token_usage_summary(workspace, account))
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = csv_fieldnames(rows) if rows else list(benchmark_adapter.Job.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
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
        return round(sums[key], 4) if counts[key] else None

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
    payload = running_summary_payload(rows, status=status, csv_path=csv_path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def open_sdk_runtime(open_runtime: Any, sdk_cls: Any, config_path: Path) -> tuple[Any, Any]:
    runtime = await open_runtime(str(config_path))
    return runtime, sdk_cls(runtime)


async def close_sdk_runtime(runtime: Any, *, drain_pending: bool) -> None:
    if runtime is None:
        return
    stop = getattr(runtime, "stop", None)
    if not callable(stop):
        return
    try:
        await stop(drain_pending=drain_pending)
    except TypeError:
        await stop()


def failed_import_record(
    args: argparse.Namespace,
    sample_id: str,
    plan: dict[str, Any],
    *,
    error: str,
    elapsed_s: float = 0.0,
    user_id: str = "",
    agent_id: str = "",
) -> dict[str, Any]:
    messages = import_messages_from_plan(args.dataset_format, sample_id, args.namespace, plan)
    safe_user_id = user_id or sample_identity(args, sample_id)[0]
    safe_agent_id = agent_id or sample_identity(args, sample_id)[1]
    return {
        "sample_id": sample_id,
        "user_id": safe_user_id,
        "agent_id": safe_agent_id,
        "status": "ECHOMEMORY_IMPORT_FAILED",
        "integrity": "failed",
        "expected_messages": len(messages),
        "submitted_messages": 0,
        "session_id": "",
        "memory_uri": f"echo://{args.account}/memories/",
        "estimated_import_tokens": sum(token_estimate(str(msg.get("content") or "")) for msg in messages),
        "error": str(error or "import failed"),
        "requested_session_id": "",
        "import_elapsed_s": round(float(elapsed_s or 0.0), 4),
        "import_commit_elapsed_s": 0.0,
        "import_flush_elapsed_s": 0.0,
        "import_artifact_wait_elapsed_s": 0.0,
        "memory_settle_wait_elapsed_s": 0.0,
        "repair_elapsed_s": 0.0,
        "memory_injection_time_s": round(float(elapsed_s or 0.0), 4),
    }


def failed_row_from_import(
    job: benchmark_adapter.Job,
    args: argparse.Namespace,
    record: dict[str, Any],
    *,
    error_kind: str,
    error_message: str,
    health_status: str = "import_failed",
) -> dict[str, Any]:
    memory_injection_time_s = round(float(record.get("memory_injection_time_s") or record.get("import_elapsed_s") or 0.0), 4)
    return {
        **benchmark_adapter.asdict(job),
        "response": "",
        "simple_grade": "NEEDS_JUDGE",
        "result": "",
        "reasoning": f"[IMPORT ERROR] {error_message}",
        "time_cost": "0",
        "backend": "echomemory",
        "eval_engine": "echomemory_generic_qa",
        "namespace": args.namespace,
        "dataset_path": str(args.dataset_path),
        "memory_uri": str(record.get("memory_uri") or "echo://user/memories/"),
        "qa_user_id": str(record.get("user_id") or ""),
        "qa_agent_id": str(record.get("agent_id") or ""),
        "identity_mode": args.identity_mode,
        "relevant_memory": "[]",
        "retrieval_count": "0",
        "memory_hit_count": "0",
        "retrieval_tokens_est": "0",
        "answer_prompt_tokens": "0",
        "answer_completion_tokens": "0",
        "answer_total_tokens": "0",
        "token_usage": token_usage_json(0, 0, 0),
        "model_status": "failed",
        "model_error_kind": error_kind,
        "model_error": str(error_message),
        "retrieval_status": "unknown",
        "answer_status": "failed",
        "health_status": health_status,
        "import_session_id": str(record.get("session_id") or ""),
        "import_status": str(record.get("status") or ""),
        "import_integrity": str(record.get("integrity") or ""),
        "import_expected_messages": str(record.get("expected_messages") or 0),
        "import_submitted_messages": str(record.get("submitted_messages") or 0),
        "import_error": str(record.get("error") or error_message),
        "import_elapsed_s": str(record.get("import_elapsed_s") or 0),
        "memory_settle_wait_elapsed_s": str(record.get("memory_settle_wait_elapsed_s") or 0),
        "repair_elapsed_s": str(record.get("repair_elapsed_s") or 0),
        "memory_injection_time_s": str(memory_injection_time_s),
        "qa_time_s": "0",
        "end_to_end_time_s": str(memory_injection_time_s),
    }


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
    import_dir = out_dir / "echomemory_import"
    import_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "generic_qa_status.json"
    repair_log = out_dir / "generic_qa_repair.log"
    repair_summary_path = out_dir / "echomemory_repair_summary.json"
    config_path = Path(args.echomem_config).expanduser().resolve() if args.echomem_config else write_echomem_config(
        out_dir,
        args.account,
        args.workspace,
        root,
        args.fallback_to_mock,
    )
    runtime = None
    sdk = None
    runtime_generation = 0

    async def recycle_runtime(reason: str) -> None:
        nonlocal runtime, sdk, runtime_generation
        if runtime is not None:
            await close_sdk_runtime(
                runtime,
                drain_pending=not bool(getattr(args, "defer_artifact_wait", False)),
            )
        runtime, sdk = await open_sdk_runtime(open_runtime, EchoMemSDK, config_path)
        runtime_generation += 1
        print(f"[runtime] generation={runtime_generation} reason={reason}", flush=True)

    await recycle_runtime("startup")
    csv_path = out_dir / "echomemory_generic_qa_results.csv"
    running_summary_path = out_dir / "running_summary.json"
    existing_rows = load_existing_csv(csv_path) if args.resume else []
    existing_by_key = {row_key(row): row for row in existing_rows if row_key(row)}
    resumed_existing_rows = len(existing_by_key)
    skipped_existing_rows = 0
    rerun_existing_rows = 0
    import_records: dict[str, dict[str, Any]] = {}
    import_summary_path = import_dir / "echomemory_generic_import_summary.json"
    if args.resume and import_summary_path.exists():
        try:
            payload = json.loads(import_summary_path.read_text(encoding="utf-8"))
            for item in payload.get("records") or []:
                sample_id = str(item.get("sample_id") or "").strip()
                if sample_id:
                    import_records[sample_id] = item
        except Exception as exc:
            print(f"[resume] could not read import summary: {exc}", flush=True)
    for row in existing_rows:
        item = import_record_from_row(row, args)
        if item and item["sample_id"] not in import_records:
            import_records[item["sample_id"]] = item

    total_jobs = planned_job_count(args)
    total_label = str(total_jobs) if total_jobs is not None else "?"
    print(
        f"[qa] dataset={args.dataset_path} format={args.dataset_format} jobs={total_label} "
        f"backend=echomemory root={root} namespace={args.namespace}",
        flush=True,
    )
    if existing_by_key:
        print(f"[resume] existing_rows={len(existing_by_key)} retry_failed={bool(args.retry_failed)} csv={csv_path}", flush=True)

    rows: list[dict[str, Any]] = []
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
    processed_jobs = 0
    recycle_every = max(0, int(getattr(args, "runtime_recycle_every", 0) or 0))
    import_timeout_s = max(0, int(getattr(args, "import_timeout_s", 0) or 0))
    runtime_jobs = 0
    fast_wait_mode = str(getattr(args, "import_wait_mode", "") or "").strip().lower() == "fast"
    print(
        f"[runtime] recycle_every={recycle_every} import_wait_mode={'fast' if fast_wait_mode else 'full'} "
        f"defer_artifact_wait={bool(getattr(args, 'defer_artifact_wait', False))} "
        f"import_timeout_s={import_timeout_s}",
        flush=True,
    )
    try:
        for index, job, plan in iter_job_plans(args):
            processed_jobs = index
            key = str(job.question_id or job.sample_id or "").strip()
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
            if recycle_every and runtime_jobs >= recycle_every:
                await recycle_runtime(f"recycle_after_{runtime_jobs}_jobs")
                runtime_jobs = 0
            if existing:
                rerun_existing_rows += 1
                print(f"[resume] retry {index}/{total_label} {job.question_id}", flush=True)
            if job.sample_id not in import_records:
                print(f"[import] {index}/{total_label} sample={job.sample_id} events={len(plan.get('events') or [])}", flush=True)
                write_status(
                    status_path,
                    {
                        "stage": "importing_memory",
                        "sample": job.sample_id,
                        "question_id": job.question_id,
                        "job_index": index,
                        "job_total": total_jobs,
                        "import_timeout_s": import_timeout_s,
                    },
                )
                import_started = time.time()
                try:
                    if import_timeout_s > 0:
                        import_records[job.sample_id] = await asyncio.wait_for(
                            import_sample_memory(args, sdk, job.sample_id, plan, import_dir),
                            timeout=import_timeout_s,
                        )
                    else:
                        import_records[job.sample_id] = await import_sample_memory(args, sdk, job.sample_id, plan, import_dir)
                except asyncio.TimeoutError:
                    elapsed_s = time.time() - import_started
                    print(
                        f"[import-timeout] {index}/{total_label} sample={job.sample_id} timeout_s={import_timeout_s}",
                        flush=True,
                    )
                    import_records[job.sample_id] = failed_import_record(
                        args,
                        job.sample_id,
                        plan,
                        error=f"TimeoutError: import exceeded {import_timeout_s}s",
                        elapsed_s=elapsed_s,
                    )
                except Exception as exc:
                    print(f"[import-error] {index}/{total_label} sample={job.sample_id} error={exc}", flush=True)
                    import_records[job.sample_id] = failed_import_record(
                        args,
                        job.sample_id,
                        plan,
                        error=f"{type(exc).__name__}: {exc}",
                        elapsed_s=time.time() - import_started,
                    )
                import_summary = import_record_summary(import_records, args.workspace, args.account)
                write_json(import_summary_path, import_summary)
            if args.import_only:
                runtime_jobs += 1
                print(f"[import-only] {index}/{total_label} sample={job.sample_id}", flush=True)
                continue
            record = import_records[job.sample_id]
            if str(record.get("integrity") or "").strip().lower() == "failed":
                row = failed_row_from_import(
                    job,
                    args,
                    record,
                    error_kind="import_failed",
                    error_message=str(record.get("error") or "import failed"),
                )
                rows.append(row)
                write_csv(csv_path, rows)
                write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
                runtime_jobs += 1
                continue
            import_summary = import_record_summary(import_records, args.workspace, args.account)
            expected_sessions = expected_session_count(import_summary)
            try:
                write_status(
                    status_path,
                    {
                        "stage": "waiting_async_memory_settle",
                        "import_summary": str(import_summary_path),
                        "import_status": str(import_summary.get("status") or ""),
                        "sample": job.sample_id,
                        "expected_sessions": expected_sessions,
                    },
                )
                stabilize_started = time.time()
                stabilize_result = wait_for_async_memory_stability(
                    workspace=Path(args.workspace).expanduser().resolve(),
                    account=args.account,
                    sample=job.sample_id,
                    expected_sessions_total=expected_sessions,
                    stabilize_timeout_seconds=min(
                        int(args.stabilize_timeout_seconds),
                        45 if fast_wait_mode else int(args.stabilize_timeout_seconds),
                    ),
                    poll_seconds=int(args.poll_seconds),
                    stability_polls=1 if fast_wait_mode else int(args.stability_polls),
                    status_path=status_path,
                    import_summary=import_summary_path,
                    import_status=str(import_summary.get("status") or ""),
                )
                stabilize_elapsed_s = round(time.time() - stabilize_started, 4)
                import_records[job.sample_id]["memory_settle_wait_elapsed_s"] = stabilize_elapsed_s
                repair_code = 0
                repair_elapsed_s = 0.0
                if not stabilize_result.get("ready") and args.repair_before_qa and not fast_wait_mode:
                    repair_cmd = [
                        sys.executable,
                        str(ROOT / "scripts" / "echomemory_repair_sessions.py"),
                        "--out-dir",
                        str(out_dir),
                        "--echomem-root",
                        str(root),
                        "--workspace",
                        args.workspace,
                        "--account",
                        args.account,
                        "--user-id",
                        args.user_id,
                        "--agent-id",
                        args.agent_id,
                        "--sample",
                        job.sample_id,
                        "--include-complete",
                        "--commit-wait-s",
                        str(args.repair_commit_wait_s),
                        "--flush-call-timeout-s",
                        str(args.repair_flush_call_timeout_s),
                        "--flush-attempts",
                        str(args.repair_flush_attempts),
                    ]
                    write_status(
                        status_path,
                        {
                            "stage": "running_repair",
                            "repair_log": str(repair_log),
                            "repair_cmd": repair_cmd,
                            "sample": job.sample_id,
                            "memory_settle": stabilize_result,
                        },
                    )
                    repair_started = time.time()
                    repair_code = run_and_log(repair_cmd, repair_log, dict(os.environ))
                    repair_elapsed_s = round(time.time() - repair_started, 4)
                    import_records[job.sample_id]["repair_elapsed_s"] = repair_elapsed_s
                    if repair_code == 0:
                        stabilize_started = time.time()
                        stabilize_result = wait_for_async_memory_stability(
                            workspace=Path(args.workspace).expanduser().resolve(),
                            account=args.account,
                            sample=job.sample_id,
                            expected_sessions_total=expected_sessions,
                            stabilize_timeout_seconds=min(int(args.stabilize_timeout_seconds), 180),
                            poll_seconds=max(5, int(args.poll_seconds)),
                            stability_polls=max(1, int(args.stability_polls)),
                            status_path=status_path,
                            import_summary=import_summary_path,
                            import_status=str(import_summary.get("status") or ""),
                        )
                        stabilize_elapsed_s = round(stabilize_elapsed_s + (time.time() - stabilize_started), 4)
                        import_records[job.sample_id]["memory_settle_wait_elapsed_s"] = stabilize_elapsed_s
                if fast_wait_mode and not stabilize_result.get("ready"):
                    print(f"[fast-wait] sample={job.sample_id} proceeding before async artifacts fully stabilize", flush=True)
                require_memory_ready_or_exit(
                    status_path=status_path,
                    stage="qa_blocked_memory_not_ready",
                    import_summary=import_summary_path,
                    import_status=str(import_summary.get("status") or ""),
                    stabilize_result=stabilize_result,
                    expected_sessions=expected_sessions,
                    allow_partial=fast_wait_mode,
                )
            except SystemExit as exc:
                message = f"memory not ready before QA (exit={exc.code})"
                import_records[job.sample_id]["status"] = "ECHOMEMORY_IMPORT_FAILED"
                import_records[job.sample_id]["integrity"] = "failed"
                import_records[job.sample_id]["error"] = message
                import_records[job.sample_id]["memory_injection_time_s"] = round(
                    float(import_records[job.sample_id].get("import_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("memory_settle_wait_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("repair_elapsed_s") or 0.0),
                    4,
                )
                write_json(import_summary_path, import_record_summary(import_records, args.workspace, args.account))
                row = failed_row_from_import(
                    job,
                    args,
                    import_records[job.sample_id],
                    error_kind="memory_not_ready",
                    error_message=message,
                    health_status="memory_not_ready",
                )
                rows.append(row)
                write_csv(csv_path, rows)
                write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
                runtime_jobs += 1
                continue
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                import_records[job.sample_id]["status"] = "ECHOMEMORY_IMPORT_FAILED"
                import_records[job.sample_id]["integrity"] = "failed"
                import_records[job.sample_id]["error"] = message
                import_records[job.sample_id]["memory_injection_time_s"] = round(
                    float(import_records[job.sample_id].get("import_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("memory_settle_wait_elapsed_s") or 0.0)
                    + float(import_records[job.sample_id].get("repair_elapsed_s") or 0.0),
                    4,
                )
                write_json(import_summary_path, import_record_summary(import_records, args.workspace, args.account))
                row = failed_row_from_import(
                    job,
                    args,
                    import_records[job.sample_id],
                    error_kind="import_pipeline_error",
                    error_message=message,
                    health_status="import_pipeline_error",
                )
                rows.append(row)
                write_csv(csv_path, rows)
                runtime_jobs += 1
                continue
            record = import_records[job.sample_id]
            record["memory_ready_before_qa"] = bool(stabilize_result.get("ready"))
            record["memory_wait_mode"] = "fast" if fast_wait_mode else "full"
            record["memory_injection_time_s"] = round(
                float(record.get("import_elapsed_s") or 0.0)
                + float(record.get("memory_settle_wait_elapsed_s") or 0.0)
                + float(record.get("repair_elapsed_s") or 0.0),
                4,
            )
            qa_args = argparse.Namespace(**vars(args))
            qa_args.user_id = str(record.get("user_id") or args.user_id)
            qa_args.agent_id = str(record.get("agent_id") or args.agent_id)
            print(f"[qa] {index}/{total_label} {job.question_id} {job.question[:90]}", flush=True)
            try:
                if args.question_timeout_s and args.question_timeout_s > 0:
                    row = await asyncio.wait_for(answer_question(qa_args, sdk, job, out_dir=out_dir, question_no=index), timeout=args.question_timeout_s)
                else:
                    row = await answer_question(qa_args, sdk, job, out_dir=out_dir, question_no=index)
            except asyncio.TimeoutError:
                row = {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[QA ERROR] question exceeded timeout_s={args.question_timeout_s}",
                    "time_cost": str(round(args.question_timeout_s, 3)),
                    "backend": "echomemory",
                    "eval_engine": "echomemory_generic_qa",
                    "namespace": args.namespace,
                    "dataset_path": str(args.dataset_path),
                    "memory_uri": str(record.get("memory_uri") or "echo://user/memories/"),
                    "qa_user_id": str(record.get("user_id") or ""),
                    "qa_agent_id": str(record.get("agent_id") or ""),
                    "identity_mode": args.identity_mode,
                    "relevant_memory": "[]",
                    "retrieval_count": "0",
                    "memory_hit_count": "0",
                    "retrieval_tokens_est": "0",
                    "answer_prompt_tokens": "0",
                    "answer_completion_tokens": "0",
                    "answer_total_tokens": "0",
                    "token_usage": token_usage_json(0, 0, 0),
                    "model_status": "failed",
                    "model_error_kind": "question_timeout",
                    "model_error": f"question exceeded timeout_s={args.question_timeout_s}",
                    "retrieval_status": "unknown",
                    "answer_status": "failed",
                    "health_status": "question_timeout",
                }
            except Exception as exc:
                row = {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[QA ERROR] {exc}",
                    "time_cost": "0",
                    "backend": "echomemory",
                    "eval_engine": "echomemory_generic_qa",
                    "namespace": args.namespace,
                    "dataset_path": str(args.dataset_path),
                    "memory_uri": str(record.get("memory_uri") or "echo://user/memories/"),
                    "qa_user_id": str(record.get("user_id") or ""),
                    "qa_agent_id": str(record.get("agent_id") or ""),
                    "identity_mode": args.identity_mode,
                    "relevant_memory": "[]",
                    "retrieval_count": "0",
                    "memory_hit_count": "0",
                    "retrieval_tokens_est": "0",
                    "answer_prompt_tokens": "0",
                    "answer_completion_tokens": "0",
                    "answer_total_tokens": "0",
                    "token_usage": token_usage_json(0, 0, 0),
                    "model_status": "failed",
                    "model_error_kind": "api_error",
                    "model_error": str(exc),
                    "retrieval_status": "unknown",
                    "answer_status": "failed",
                    "health_status": "api_error",
                }
            row.update(
                {
                    "backend": "echomemory",
                    "eval_engine": "echomemory_generic_qa",
                    "namespace": args.namespace,
                    "dataset_path": str(args.dataset_path),
                    "memory_uri": str(record.get("memory_uri") or row.get("memory_uri") or "echo://user/memories/"),
                    "qa_user_id": str(record.get("user_id") or row.get("qa_user_id") or ""),
                    "qa_agent_id": str(record.get("agent_id") or row.get("qa_agent_id") or ""),
                    "identity_mode": args.identity_mode,
                    "import_session_id": str(record.get("session_id") or ""),
                    "import_status": str(record.get("status") or ""),
                    "import_integrity": str(record.get("integrity") or ""),
                    "import_expected_messages": str(record.get("expected_messages") or 0),
                    "import_submitted_messages": str(record.get("submitted_messages") or 0),
                    "import_error": str(record.get("error") or ""),
                    "import_elapsed_s": str(record.get("import_elapsed_s") or 0),
                    "memory_settle_wait_elapsed_s": str(record.get("memory_settle_wait_elapsed_s") or 0),
                    "repair_elapsed_s": str(record.get("repair_elapsed_s") or 0),
                    "memory_injection_time_s": str(record.get("memory_injection_time_s") or 0),
                    "qa_time_s": str(row.get("time_cost") or 0),
                    "end_to_end_time_s": str(
                        round(
                            float(record.get("memory_injection_time_s") or 0.0)
                            + float(row.get("time_cost") or 0.0),
                            4,
                        )
                    ),
                }
            )
            rows.append(row)
            write_csv(csv_path, rows)
            write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
            runtime_jobs += 1
    finally:
        await close_sdk_runtime(
            runtime,
            drain_pending=not bool(getattr(args, "defer_artifact_wait", False)),
        )

    if processed_jobs == 0:
        raise SystemExit(f"no jobs found in {args.dataset_path}")
    write_csv(csv_path, rows)
    write_running_summary(running_summary_path, rows, status="running", csv_path=csv_path)
    import_summary = import_record_summary(import_records, args.workspace, args.account)
    write_json(import_summary_path, import_summary)
    judge_result = {"enabled": False, "reason": "import_only"} if args.import_only else run_judge(args, csv_path)
    official_eval_result = {"enabled": False, "reason": "import_only"} if args.import_only else run_official_eval(args, csv_path)
    health_counts: Counter = Counter(str(row.get("health_status") or "unknown") for row in rows)
    tool_counts: Counter = Counter()
    for row in rows:
        try:
            tool_counts.update(json.loads(str(row.get("tool_call_name_counts") or "{}")))
        except Exception:
            pass
    judged_summary = judge_result.get("summary") if isinstance(judge_result, dict) else {}
    if not isinstance(judged_summary, dict):
        judged_summary = {}
    summary = {
        **alignment_metadata("echomemory", ECHOMEMORY_BACKEND_ROUTE),
        "status": "ECHOMEMORY_GENERIC_IMPORT_ONLY_DONE" if args.import_only else "ECHOMEMORY_GENERIC_QA_DONE",
        "dataset_format": args.dataset_format,
        "dataset": str(args.dataset_path),
        "sample": args.sample,
        "count": len(rows),
        "rows": len(rows),
        "output_csv": str(csv_path),
        "backend": "echomemory",
        "eval_engine": "echomemory_generic_qa",
        "echomem_root": str(root),
        "echomem_config": str(config_path),
        "workspace": str(Path(args.workspace).expanduser().resolve()),
        "account": args.account,
        "namespace": args.namespace,
        "identity_mode": args.identity_mode,
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
        "import_summary_path": str(import_summary_path),
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
        "prompt_mode": args.prompt_mode,
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "vikingbot_prompt_aligned": args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES,
        "vikingboat_compat": bool(args.vikingboat_compat),
        "memory_tool_loop_enabled": bool(args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES and args.vikingboat_tool_loop),
        "memory_tool_set": args.tool_set,
        "top_k": args.top_k,
        "score_threshold": args.score_threshold,
        "retrieval_mode": args.retrieval_mode,
        "total_memory_injection_time_s": round(sum(float(row.get("memory_injection_time_s") or 0.0) for row in rows), 4),
        "avg_memory_injection_time_s": round(
            sum(float(row.get("memory_injection_time_s") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "total_memory_settle_wait_time_s": round(sum(float(row.get("memory_settle_wait_elapsed_s") or 0.0) for row in rows), 4),
        "avg_memory_settle_wait_time_s": round(
            sum(float(row.get("memory_settle_wait_elapsed_s") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "total_qa_time_s": round(sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows), 4),
        "avg_qa_time_s": round(
            sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "total_end_to_end_time_s": round(sum(float(row.get("end_to_end_time_s") or 0.0) for row in rows), 4),
        "avg_end_to_end_time_s": round(
            sum(float(row.get("end_to_end_time_s") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
        "avg_time": round(
            sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in rows) / len(rows),
            4,
        ) if rows else None,
    }
    summary.update(
        {
            key: import_summary.get(key)
            for key in (
                "llm_input_tokens",
                "llm_output_tokens",
                "llm_total_tokens",
                "llm_call_count",
                "import_llm_prompt_tokens",
                "import_llm_completion_tokens",
                "import_llm_total_tokens",
                "import_embedding_total_tokens",
                "import_total_tokens",
                "search_intent_total_tokens",
                "search_intent_call_count",
                "embedding_total_tokens",
                "embedding_call_count",
                "call_sites",
            )
            if key in import_summary
        }
    )
    summary.update(official_metric_summary(args.dataset_format, official_eval_result))
    write_json(out_dir / "summary.json", summary)
    final_status = "failed" if judge_result.get("enabled") and int(judge_result.get("returncode") or 0) != 0 else "succeeded"
    write_running_summary(running_summary_path, rows, status=final_status, csv_path=csv_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if judge_result.get("enabled") and int(judge_result.get("returncode") or 0) != 0:
        raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generic memory benchmarks through EchoMemory local SDK import, retrieval, LLM answer, and optional Judge.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--format", dest="dataset_format", default="auto")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="all")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--questions", default="")
    parser.add_argument("--namespace", default="")
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--workspace", default="/tmp/locomo-eval-echomemory")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--identity-mode", choices=["isolated_sample", "fixed"], default="isolated_sample")
    parser.add_argument("--user-prefix", default="eval-user")
    parser.add_argument("--agent-prefix", default="eval-agent")
    parser.add_argument("--prompt-mode", choices=["vikingboat_lite", "vikingboat_compat", "one_shot"], default="one_shot")
    parser.add_argument("--vikingboat-compat", dest="vikingboat_compat", action="store_true")
    parser.add_argument("--no-vikingboat-compat", dest="vikingboat_compat", action="store_false")
    parser.add_argument("--top-k", type=int, default=VIKINGBOT_INITIAL_SEARCH_LIMIT)
    parser.add_argument("--score-threshold", type=float, default=VIKINGBOT_INITIAL_MIN_SCORE)
    parser.add_argument("--memory-budget-chars", type=int, default=VIKINGBOT_USER_MEMORY_BUDGET_CHARS + VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS)
    parser.add_argument("--user-memory-budget-chars", type=int, default=VIKINGBOT_USER_MEMORY_BUDGET_CHARS)
    parser.add_argument("--agent-memory-budget-chars", type=int, default=VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS)
    parser.add_argument("--retrieval-mode", choices=["find", "search", "both", "local"], default="search")
    parser.add_argument("--retrieval-ranker", choices=["diversified", "score"], default="score")
    parser.add_argument("--no-local-session-summaries", dest="local_session_summaries", action="store_false")
    parser.add_argument("--local-session-summaries", dest="local_session_summaries", action="store_true")
    parser.add_argument("--no-local-atoms", dest="local_atoms", action="store_false")
    parser.add_argument("--local-atoms", dest="local_atoms", action="store_true")
    parser.add_argument("--local-messages", dest="local_messages", action="store_true")
    parser.add_argument("--no-local-messages", dest="local_messages", action="store_false")
    parser.add_argument("--no-local-timeline-hints", dest="local_timeline_hints", action="store_false")
    parser.add_argument("--local-timeline-hints", dest="local_timeline_hints", action="store_true")
    parser.add_argument("--local-score-threshold", type=float, default=0.08)
    parser.add_argument("--local-summary-max", type=int, default=12)
    parser.add_argument("--local-atom-max", type=int, default=24)
    parser.add_argument("--local-message-max", type=int, default=16)
    parser.add_argument("--local-message-window", type=int, default=1)
    parser.add_argument("--no-local-memory-artifacts", dest="local_memory_artifacts", action="store_false")
    parser.add_argument("--local-memory-artifacts", dest="local_memory_artifacts", action="store_true")
    parser.add_argument("--local-artifact-max", type=int, default=24)
    parser.add_argument("--vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_true")
    parser.add_argument("--no-vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_false")
    parser.add_argument("--tool-set", choices=["vikingboat_default", "search_read", "search_only", VIKINGBOT_TOOL_SET], default="search_read")
    parser.add_argument("--tool-search-limit", type=int, default=VIKINGBOT_TOOL_SEARCH_LIMIT)
    parser.add_argument("--tool-min-score", type=float, default=VIKINGBOT_TOOL_MIN_SCORE)
    parser.add_argument("--tool-log-chars", type=int, default=1200)
    parser.add_argument("--prefetch-read-count", type=int, default=4)
    parser.add_argument("--prefetch-context-chars", type=int, default=5000)
    parser.add_argument("--initial-tool-prefetch", dest="initial_tool_prefetch", action="store_true")
    parser.add_argument("--no-initial-tool-prefetch", dest="initial_tool_prefetch", action="store_false")
    parser.add_argument("--answer-base-url", default=os.environ.get("JUDGE_BASE_URL", ""))
    parser.add_argument("--answer-model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--judge-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--judge-parallel", type=int, default=4)
    parser.add_argument("--judge-after", action="store_true")
    parser.add_argument("--official-eval-after", action="store_true")
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--question-timeout-s", type=int, default=600)
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument("--resume", dest="resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-empty-answers", action="store_true")
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parser.add_argument("--fallback-to-mock", action="store_true")
    parser.add_argument("--fallback-to-one-shot", dest="fallback_to_one_shot", action="store_true")
    parser.add_argument("--no-fallback-to-one-shot", dest="fallback_to_one_shot", action="store_false")
    parser.add_argument("--import-wait-mode", choices=["fast", "full"], default="full")
    parser.add_argument("--defer-artifact-wait", action="store_true")
    parser.add_argument("--skip-session-commit", action="store_true")
    parser.add_argument("--continue-on-session-error", action="store_true")
    parser.add_argument("--commit-wait-s", type=int, default=300)
    parser.add_argument("--commit-call-timeout-s", type=int, default=300)
    parser.add_argument("--flush-call-timeout-s", type=int, default=600)
    parser.add_argument("--flush-attempts", type=int, default=2)
    parser.add_argument("--stabilize-timeout-seconds", type=int, default=300)
    parser.add_argument("--stability-polls", type=int, default=3)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--repair-before-qa", action="store_true", default=True)
    parser.add_argument("--no-repair-before-qa", dest="repair_before_qa", action="store_false")
    parser.add_argument("--repair-flush-call-timeout-s", type=int, default=600)
    parser.add_argument("--repair-flush-attempts", type=int, default=2)
    parser.add_argument("--repair-commit-wait-s", type=int, default=300)
    parser.add_argument("--runtime-recycle-every", type=int, default=50)
    parser.add_argument("--import-timeout-s", type=int, default=180)
    parser.set_defaults(
        vikingboat_compat=False,
        local_session_summaries=False,
        local_atoms=False,
        local_messages=False,
        local_timeline_hints=False,
        local_memory_artifacts=False,
        vikingboat_tool_loop=False,
        initial_tool_prefetch=False,
        fallback_to_one_shot=True,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    answer_base_url = str(args.answer_base_url or "").strip()
    answer_model = str(args.answer_model or "").strip()
    answer_token = str(args.answer_token or "").strip()
    judge_token = str(args.judge_token or "").strip()
    if answer_base_url and not os.environ.get("ECHOMEM_CHAT_BASE_URL"):
        os.environ["ECHOMEM_CHAT_BASE_URL"] = answer_base_url
    if answer_base_url and not os.environ.get("DASHSCOPE_BASE_URL"):
        os.environ["DASHSCOPE_BASE_URL"] = answer_base_url
    if answer_model and not os.environ.get("ECHOMEM_CHAT_MODEL"):
        os.environ["ECHOMEM_CHAT_MODEL"] = answer_model
    if answer_token and not os.environ.get("ECHOMEM_CHAT_API_KEY"):
        os.environ["ECHOMEM_CHAT_API_KEY"] = answer_token
    if answer_token and not os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["DASHSCOPE_API_KEY"] = answer_token
    if judge_token and not os.environ.get("LOCOMO_JUDGE_TOKEN"):
        os.environ["LOCOMO_JUDGE_TOKEN"] = judge_token
    args.dataset_path = Path(args.dataset).expanduser().resolve()
    if args.dataset_format == "auto":
        data = read_dataset(args.dataset_path)
        args.dataset_format = benchmark_adapter.infer_format(args.dataset_path, data)
    args.dataset_format = str(args.dataset_format or "generic").strip().lower() or "generic"
    args.retrieval_mode = normalize_retrieval_mode(args.retrieval_mode)
    args.tool_set = normalize_echomemory_tool_set(args.tool_set, vikingboat_compat=bool(args.vikingboat_compat))
    if not args.namespace:
        args.namespace = f"{args.dataset_format}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    if args.random_count:
        random.seed(args.random_seed)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
