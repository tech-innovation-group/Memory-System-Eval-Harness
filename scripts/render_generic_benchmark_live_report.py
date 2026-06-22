#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def safe_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def safe_int(value: Any) -> int | None:
    raw = safe_float(value)
    return int(raw) if raw is not None else None


def percent(value: Any) -> str:
    number = safe_float(value)
    return "-" if number is None else f"{number * 100:.2f}%"


def seconds_text(value: Any) -> str:
    number = safe_float(value)
    return "-" if number is None else f"{number:.2f}s"


def pick(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def format_duration_seconds(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    seconds = max(0, int(round(number)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def parse_iso_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_flexible_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = parse_iso_timestamp(text)
    if parsed is not None:
        return parsed
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def normalize_stage_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    mapping = {
        "importing_memory": "import",
        "waiting_async_memory_settle": "settle",
        "judge_after": "judge",
        "official_eval_after": "official",
        "succeeded": "done",
        "done": "done",
    }
    return mapping.get(text, text)


def read_tail_text(path: Path, max_bytes: int = 4000000) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def parse_log_progress(run_dir: Path) -> dict[str, Any]:
    text = read_tail_text(run_dir / "run.log")
    if not text:
        return {}
    import_index = 0
    import_total = 0
    qa_index = 0
    qa_total = 0
    for match in re.finditer(r"\[import\]\s+(\d+)/(\d+)\s+", text):
        import_index = int(match.group(1))
        import_total = int(match.group(2))
    for match in re.finditer(r"\[qa\]\s+(\d+)/(\d+)\s+", text):
        qa_index = int(match.group(1))
        qa_total = int(match.group(2))
    total = max(import_total, qa_total, 0)
    current = max(import_index, qa_index, 0)
    if total <= 0 and current <= 0:
        return {}
    phase = "qa" if qa_index >= import_index and qa_index > 0 else "import"
    detail = f"{phase} {current}/{total}" if total > 0 else phase
    return {
        "current": current,
        "total": total,
        "phase": phase,
        "detail": detail,
    }


def parse_log_health(run_dir: Path) -> dict[str, Any]:
    text = read_tail_text(run_dir / "run.log")
    if not text:
        return {}
    patterns = {
        "import_timeouts": r"\[import-timeout\]\s+\d+/\d+\s+",
        "graph_sync_failures": r"Graph sync .* failed after .*",
        "episode_projection_failures": r"Episode projection failed:",
        "entity_persistence_failures": r"Entity-anchored persistence failed:",
        "commit_incomplete": r"\[commit\].*complete=False",
        "flush_incomplete": r"\[commit\].*flush_complete=False",
        "model_gateway_failures": r"ModelGatewayError:",
    }
    counts = {key: len(re.findall(pattern, text)) for key, pattern in patterns.items()}
    latest_timeout = ""
    timeout_matches = list(re.finditer(r"(\[import-timeout\]\s+\d+/\d+\s+sample=\S+\s+timeout_s=\d+)", text))
    if timeout_matches:
        latest_timeout = timeout_matches[-1].group(1)
    latest_graph = ""
    graph_matches = list(re.finditer(r"(Graph sync .* failed after .*|Episode projection failed:.*|Entity-anchored persistence failed:.*)", text))
    if graph_matches:
        latest_graph = graph_matches[-1].group(1)
    latest_model_issue = ""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        if "Access denied" in line or "Arrearage" in line or "overdue-payment" in line:
            latest_model_issue = line
            break
        if not latest_model_issue and "ModelGatewayError:" in line:
            latest_model_issue = line
    return {
        **counts,
        "latest_import_timeout": latest_timeout,
        "latest_backend_failure": latest_graph,
        "latest_model_issue": latest_model_issue,
    }


def analyze_row_health(rows: list[dict[str, str]]) -> dict[str, Any]:
    counts = {
        "pending_judge_rows": 0,
        "import_failed_rows": 0,
        "pending_async_rows": 0,
        "model_failed_rows": 0,
        "retrieval_error_rows": 0,
        "not_ok_health_rows": 0,
    }
    latest_import_issue = ""
    tail_import_failed_streak = 0
    tail_pending_async_streak = 0
    recent_rows = rows[-5:]
    recent_import_failed_rows = 0
    recent_pending_async_rows = 0
    for row in reversed(rows):
        health = str(row.get("health_status") or "").strip()
        import_integrity = str(row.get("import_integrity") or "").strip()
        if health == "import_failed":
            tail_import_failed_streak += 1
        else:
            break
    for row in reversed(rows):
        import_integrity = str(row.get("import_integrity") or "").strip()
        if import_integrity == "pending_async_memory":
            tail_pending_async_streak += 1
        else:
            break
    for row in recent_rows:
        if str(row.get("health_status") or "").strip() == "import_failed":
            recent_import_failed_rows += 1
        if str(row.get("import_integrity") or "").strip() == "pending_async_memory":
            recent_pending_async_rows += 1
    for row in rows:
        result = str(row.get("result") or "").strip()
        health = str(row.get("health_status") or "").strip()
        import_status = str(row.get("import_status") or "").strip()
        import_integrity = str(row.get("import_integrity") or "").strip()
        model_status = str(row.get("model_status") or "").strip()
        retrieval_status = str(row.get("retrieval_status") or "").strip()
        if result == "NEEDS_JUDGE":
            counts["pending_judge_rows"] += 1
        if result == "ECHOMEMORY_IMPORT_FAILED" or health == "import_failed":
            counts["import_failed_rows"] += 1
            latest_import_issue = latest_import_issue or f"{row.get('question_id') or row.get('sample_id') or ''} · {result or health}"
        if import_integrity == "pending_async_memory" or import_status == "ECHOMEMORY_IMPORT_INCOMPLETE":
            counts["pending_async_rows"] += 1
            latest_import_issue = latest_import_issue or f"{row.get('question_id') or row.get('sample_id') or ''} · {import_status or import_integrity}"
        if model_status and model_status != "ok":
            counts["model_failed_rows"] += 1
        if retrieval_status and retrieval_status != "ok":
            counts["retrieval_error_rows"] += 1
        if health and health != "ok":
            counts["not_ok_health_rows"] += 1
    return {
        **counts,
        "latest_import_issue": latest_import_issue,
        "tail_import_failed_streak": tail_import_failed_streak,
        "tail_pending_async_streak": tail_pending_async_streak,
        "recent_import_failed_rows": recent_import_failed_rows,
        "recent_pending_async_rows": recent_pending_async_rows,
    }


def fetch_task_progress(api_base: str, task_id: str) -> dict[str, Any]:
    base = str(api_base or "").strip().rstrip("/")
    task = str(task_id or "").strip()
    if not base or not task:
        return {}
    try:
        with urlrequest.urlopen(f"{base}/api/tasks", timeout=5) as response:
            payload = json.load(response)
    except (OSError, ValueError, urlerror.URLError, TimeoutError):
        return {}
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not isinstance(tasks, list):
        return {}
    for item in tasks:
        if not isinstance(item, dict) or str(item.get("id") or "").strip() != task:
            continue
        progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
        return {
            "status": item.get("status"),
            "current": progress.get("current"),
            "total": progress.get("total"),
            "phase": progress.get("phase"),
            "detail": progress.get("detail"),
            "eta_seconds": progress.get("eta_seconds"),
            "elapsed_seconds": progress.get("elapsed_seconds"),
            "warnings": progress.get("warnings"),
            "current_import": progress.get("current_import"),
        }
    return {}


def verdict_badge(row: dict[str, str]) -> tuple[str, str]:
    result = str(row.get("result") or row.get("simple_grade") or row.get("simple_match") or "").strip().upper()
    if result in {"CORRECT", "MATCH"}:
        return "correct", "CORRECT"
    if result == "WRONG":
        return "wrong", "WRONG"
    return "pending", "PENDING"


def derive_metrics(
    *,
    summary: dict[str, Any],
    running: dict[str, Any],
    judge: dict[str, Any],
    official: dict[str, Any],
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
    task_progress: dict[str, Any],
    log_progress: dict[str, Any],
    log_health: dict[str, Any],
    row_health: dict[str, Any],
    status_json: dict[str, Any],
) -> dict[str, Any]:
    effective_progress = dict(task_progress or {})
    status_current = safe_int(status_json.get("job_index")) if isinstance(status_json, dict) else None
    status_total = safe_int(status_json.get("job_total")) if isinstance(status_json, dict) else None
    status_stage = str(status_json.get("stage") or "").strip() if isinstance(status_json, dict) else ""
    status_sample = str(status_json.get("sample") or status_json.get("question_id") or "").strip() if isinstance(status_json, dict) else ""
    if not safe_int(effective_progress.get("current")) and not safe_int(effective_progress.get("total")):
        effective_progress.update(log_progress or {})
    elif safe_int(effective_progress.get("total")) is not None and safe_int(effective_progress.get("current")) is not None:
        current = safe_int(effective_progress.get("current")) or 0
        total = safe_int(effective_progress.get("total")) or 0
        hinted_total = safe_int(log_progress.get("total")) if isinstance(log_progress, dict) else None
        hinted_current = safe_int(log_progress.get("current")) if isinstance(log_progress, dict) else None
        if hinted_total and hinted_total > total and current >= total:
            effective_progress["total"] = hinted_total
            effective_progress["current"] = max(current, hinted_current or 0)
            if log_progress.get("phase"):
                effective_progress["phase"] = log_progress.get("phase")
            if log_progress.get("detail"):
                effective_progress["detail"] = log_progress.get("detail")
    if status_total and ((safe_int(effective_progress.get("total")) or 0) < status_total or (safe_int(effective_progress.get("current")) or 0) < (status_current or 0)):
        effective_progress["total"] = status_total
        if status_current is not None:
            effective_progress["current"] = status_current
    if status_stage:
        phase = normalize_stage_label(status_stage)
        effective_progress["phase"] = phase
        if status_current is not None and status_total is not None:
            effective_progress["detail"] = f"{phase} {status_current}/{status_total}"
    if status_sample and not effective_progress.get("current_import"):
        effective_progress["current_import"] = status_sample
    summary_json = summary.get("summary_json") if isinstance(summary.get("summary_json"), dict) else {}
    manifest_status = str(manifest.get("status") or "").strip().lower()
    summary_status = str(summary.get("status") or "").strip().lower()
    running_status = str(running.get("status") or "").strip().lower()
    status = manifest_status or summary_status or running_status or ("running" if rows else "unknown")
    dataset_format = str(
        pick(
            summary.get("dataset_format"),
            summary_json.get("dataset_format"),
            manifest.get("dataset_format"),
            manifest.get("config", {}).get("dataset_format") if isinstance(manifest.get("config"), dict) else None,
        ) or ""
    ).strip().lower()
    rows_count = safe_int(pick(running.get("rows"), summary.get("rows"), summary_json.get("rows"), summary_json.get("count"))) or len(rows)
    metrics = {
        "status": status,
        "dataset_format": dataset_format,
        "rows": rows_count,
        "graded": safe_int(pick(judge.get("graded"), summary.get("graded"), summary_json.get("graded"))),
        "judge_accuracy": safe_float(pick(judge.get("accuracy"), summary.get("accuracy"), summary_json.get("accuracy"))),
        "official_metric": str(pick(summary.get("official_metric"), summary_json.get("official_metric")) or "").strip(),
        "official_metric_scope": str(pick(summary.get("official_metric_scope"), summary_json.get("official_metric_scope"), official.get("metric_scope")) or "").strip(),
        "official_score": safe_float(pick(summary.get("official_score"), summary_json.get("official_score"))),
        "answer_em": safe_float(pick(summary.get("official_answer_em"), summary_json.get("official_answer_em"), official.get("answer_em"))),
        "answer_f1": safe_float(pick(summary.get("official_answer_f1"), summary_json.get("official_answer_f1"), official.get("answer_f1"))),
        "total_memory_injection_time_s": safe_float(pick(running.get("total_memory_injection_time_s"), summary.get("total_memory_injection_time_s"), summary_json.get("total_memory_injection_time_s"))),
        "avg_memory_injection_time_s": safe_float(pick(running.get("avg_memory_injection_time_s"), summary.get("avg_memory_injection_time_s"), summary_json.get("avg_memory_injection_time_s"))),
        "total_memory_settle_wait_time_s": safe_float(pick(running.get("total_memory_settle_wait_time_s"), summary.get("total_memory_settle_wait_time_s"), summary_json.get("total_memory_settle_wait_time_s"))),
        "avg_memory_settle_wait_time_s": safe_float(pick(running.get("avg_memory_settle_wait_time_s"), summary.get("avg_memory_settle_wait_time_s"), summary_json.get("avg_memory_settle_wait_time_s"))),
        "total_qa_time_s": safe_float(pick(running.get("total_qa_time_s"), summary.get("total_qa_time_s"), summary_json.get("total_qa_time_s"))),
        "avg_qa_time_s": safe_float(pick(running.get("avg_qa_time_s"), summary.get("avg_qa_time_s"), summary_json.get("avg_qa_time_s"), summary.get("avg_time"))),
        "total_end_to_end_time_s": safe_float(pick(running.get("total_end_to_end_time_s"), summary.get("total_end_to_end_time_s"), summary_json.get("total_end_to_end_time_s"))),
        "avg_end_to_end_time_s": safe_float(pick(running.get("avg_end_to_end_time_s"), summary.get("avg_end_to_end_time_s"), summary_json.get("avg_end_to_end_time_s"))),
        "last_question_id": str(pick(running.get("last_question_id"), summary.get("last_question_id"), summary_json.get("last_question_id")) or "").strip(),
        "updated_at": str(pick(running.get("updated_at"), summary.get("updated_at"), summary_json.get("updated_at")) or "").strip(),
        "progress_current": safe_int(effective_progress.get("current")),
        "progress_total": safe_int(effective_progress.get("total")),
        "progress_phase": str(effective_progress.get("phase") or "").strip(),
        "progress_detail": str(effective_progress.get("detail") or "").strip(),
        "progress_eta_seconds": safe_float(effective_progress.get("eta_seconds")),
        "progress_elapsed_seconds": safe_float(effective_progress.get("elapsed_seconds")),
        "progress_current_import": str(effective_progress.get("current_import") or "").strip(),
        "progress_warnings": effective_progress.get("warnings") if isinstance(effective_progress.get("warnings"), list) else [],
        "import_timeout_count": safe_int(log_health.get("import_timeouts")) or 0,
        "graph_sync_failure_count": safe_int(log_health.get("graph_sync_failures")) or 0,
        "episode_projection_failure_count": safe_int(log_health.get("episode_projection_failures")) or 0,
        "entity_persistence_failure_count": safe_int(log_health.get("entity_persistence_failures")) or 0,
        "commit_incomplete_count": safe_int(log_health.get("commit_incomplete")) or 0,
        "flush_incomplete_count": safe_int(log_health.get("flush_incomplete")) or 0,
        "model_gateway_failure_count": safe_int(log_health.get("model_gateway_failures")) or 0,
        "latest_import_timeout": str(log_health.get("latest_import_timeout") or "").strip(),
        "latest_backend_failure": str(log_health.get("latest_backend_failure") or "").strip(),
        "latest_model_issue": str(log_health.get("latest_model_issue") or "").strip(),
        "pending_judge_rows": safe_int(row_health.get("pending_judge_rows")) or 0,
        "import_failed_rows": safe_int(row_health.get("import_failed_rows")) or 0,
        "pending_async_rows": safe_int(row_health.get("pending_async_rows")) or 0,
        "tail_import_failed_streak": safe_int(row_health.get("tail_import_failed_streak")) or 0,
        "tail_pending_async_streak": safe_int(row_health.get("tail_pending_async_streak")) or 0,
        "recent_import_failed_rows": safe_int(row_health.get("recent_import_failed_rows")) or 0,
        "recent_pending_async_rows": safe_int(row_health.get("recent_pending_async_rows")) or 0,
        "model_failed_rows": safe_int(row_health.get("model_failed_rows")) or 0,
        "retrieval_error_rows": safe_int(row_health.get("retrieval_error_rows")) or 0,
        "not_ok_health_rows": safe_int(row_health.get("not_ok_health_rows")) or 0,
        "latest_import_issue": str(row_health.get("latest_import_issue") or "").strip(),
        "import_timeout_s": safe_int((manifest.get("config") or {}).get("import_timeout_s") if isinstance(manifest.get("config"), dict) else None),
        "commit_call_timeout_s": safe_int((manifest.get("config") or {}).get("commit_call_timeout_s") if isinstance(manifest.get("config"), dict) else None),
    }
    freshness_source = str(pick(status_json.get("checked_at"), metrics["updated_at"]) or "").strip()
    freshness_dt = parse_flexible_timestamp(freshness_source)
    freshness_age_seconds = None
    if freshness_dt is not None:
        now_dt = datetime.now(freshness_dt.tzinfo) if freshness_dt.tzinfo is not None else datetime.now()
        freshness_age_seconds = max(0.0, (now_dt - freshness_dt).total_seconds())
    metrics["freshness_source"] = freshness_source
    metrics["freshness_age_seconds"] = freshness_age_seconds
    if metrics["progress_elapsed_seconds"] is None:
        started_at = parse_iso_timestamp(manifest.get("started_at") or manifest.get("created_at"))
        if started_at is not None:
            metrics["progress_elapsed_seconds"] = max(0.0, (datetime.now(started_at.tzinfo) - started_at).total_seconds())
    if metrics["progress_eta_seconds"] is None and metrics["progress_elapsed_seconds"] and metrics["progress_current"] and metrics["progress_total"]:
        current = float(metrics["progress_current"] or 0)
        total = float(metrics["progress_total"] or 0)
        elapsed = float(metrics["progress_elapsed_seconds"] or 0)
        if current > 0 and total > current and elapsed > 0:
            metrics["progress_eta_seconds"] = max(0.0, elapsed / current * (total - current))
    if metrics["official_score"] is None and metrics["answer_f1"] is not None:
        metrics["official_score"] = metrics["answer_f1"]
    if not metrics["official_metric"] and metrics["answer_f1"] is not None:
        metrics["official_metric"] = "answer_f1"
    warnings: list[str] = []
    import_timeout_s = metrics["import_timeout_s"]
    commit_call_timeout_s = metrics["commit_call_timeout_s"]
    if import_timeout_s and commit_call_timeout_s and import_timeout_s < commit_call_timeout_s:
        warnings.append(
            f"当前 run 的 import_timeout_s={import_timeout_s} 小于 commit_call_timeout_s={commit_call_timeout_s}。这会让导入更早超时，可能把结果中的 import_failed/pending_async 拉高。"
        )
    if (metrics["import_failed_rows"] or 0) > 0:
        warnings.append(f"当前已经累计 {metrics['import_failed_rows']} 行 import_failed。最终准确率需要结合这部分导入失败一起解读。")
    if (metrics["pending_async_rows"] or 0) > 0:
        warnings.append(f"当前还有 {metrics['pending_async_rows']} 行 pending_async_memory，说明部分记忆写入尚未稳定落盘。")
    if (metrics["tail_import_failed_streak"] or 0) >= 2:
        warnings.append(f"最近连续 {metrics['tail_import_failed_streak']} 行都是 import_failed，新增样本暂时没有进入正常 QA。")
    if (metrics["tail_pending_async_streak"] or 0) >= 3:
        warnings.append(f"最近连续 {metrics['tail_pending_async_streak']} 行都是 pending_async_memory，说明尾部样本仍在等待落稳。")
    if (metrics["recent_import_failed_rows"] or 0) >= 3:
        warnings.append(f"最近 5 行里有 {metrics['recent_import_failed_rows']} 行 import_failed。")
    if (metrics["recent_pending_async_rows"] or 0) >= 3:
        warnings.append(f"最近 5 行里有 {metrics['recent_pending_async_rows']} 行 pending_async_memory。")
    latest_model_issue = str(metrics.get("latest_model_issue") or "").strip()
    if latest_model_issue:
        if any(token in latest_model_issue.lower() for token in ("arrearage", "access denied", "overdue-payment")):
            warnings.append("后台日志已经出现模型服务 Arrearage / Access denied。当前尾部 import_failed 和 pending_async 升高大概率与这个外部错误直接相关。")
        else:
            warnings.append(f"后台日志最近还有模型服务异常：{latest_model_issue[:220]}")
    freshness_age_seconds = safe_float(metrics.get("freshness_age_seconds"))
    if freshness_age_seconds is not None and freshness_age_seconds >= 180 and metrics["status"] not in {"succeeded", "failed", "done", "cancelled", "canceled"}:
        warnings.append(
            f"运行态已经 {format_duration_seconds(freshness_age_seconds)} 没有刷新。当前任务可能停滞，建议检查 run.log 和后台进程。"
        )
    metrics["run_warnings"] = warnings
    return metrics


def render_report(
    *,
    title: str,
    output_path: Path,
    run_dir: Path,
    csv_path: Path,
    manifest: dict[str, Any],
    summary: dict[str, Any],
    running: dict[str, Any],
    judge: dict[str, Any],
    official: dict[str, Any],
    rows: list[dict[str, str]],
    task_progress: dict[str, Any],
    status_json: dict[str, Any],
) -> str:
    log_progress = parse_log_progress(run_dir)
    log_health = parse_log_health(run_dir)
    row_health = analyze_row_health(rows)
    metrics = derive_metrics(
        summary=summary,
        running=running,
        judge=judge,
        official=official,
        manifest=manifest,
        rows=rows,
        task_progress=task_progress,
        log_progress=log_progress,
        log_health=log_health,
        row_health=row_health,
        status_json=status_json,
    )
    terminal_statuses = {"succeeded", "failed", "done", "cancelled", "canceled"}
    auto_refresh = metrics["status"] not in terminal_statuses
    recent_rows = rows[-40:]
    task_name = str(pick(manifest.get("name"), manifest.get("id"), title) or title)
    task_id = str(pick(manifest.get("id"), run_dir.name) or run_dir.name)
    task_status = metrics["status"] or "unknown"
    dataset_label = metrics["dataset_format"] or str(pick(summary.get("dataset_format"), "generic"))
    progress_text = "-"
    if metrics["progress_current"] is not None and metrics["progress_total"] is not None:
        progress_text = f"{metrics['progress_current']} / {metrics['progress_total']}"
    phase_text = metrics["progress_phase"] or "-"
    eta_text = format_duration_seconds(metrics["progress_eta_seconds"]) if auto_refresh else "-"
    elapsed_text = format_duration_seconds(metrics["progress_elapsed_seconds"])
    rows_per_hour = (
        ((metrics["rows"] or 0) / metrics["progress_elapsed_seconds"] * 3600.0)
        if (metrics["rows"] or 0) > 0 and (metrics["progress_elapsed_seconds"] or 0) > 0
        else None
    )
    finish_eta_text = "-"
    if auto_refresh and metrics["progress_eta_seconds"] is not None:
        finish_eta_text = (datetime.now() + timedelta(seconds=float(metrics["progress_eta_seconds"]))).strftime("%Y-%m-%d %H:%M:%S")
    ended_at_text = str(manifest.get("ended_at") or "").strip()
    returncode_text = str(manifest.get("returncode") if manifest.get("returncode") is not None else "-")
    summary_path = csv_path.parent / "summary.json"
    judge_path = csv_path.parent / "judge_summary.json"
    hotpot_path = csv_path.parent / "hotpotqa_answer_summary.json"
    artifact_items = [
        ("summary.json", summary_path.exists()),
        ("judge_summary.json", judge_path.exists()),
        ("hotpotqa_answer_summary.json", hotpot_path.exists()),
    ]
    cards = [
        ("任务状态", task_status),
        ("结果行数", str(metrics["rows"] or 0)),
        ("当前进度", progress_text),
        ("当前阶段", phase_text),
        ("运行态新鲜度", format_duration_seconds(metrics["freshness_age_seconds"])),
        ("预计剩余", eta_text),
        ("当前吞吐", "-" if rows_per_hour is None else f"{rows_per_hour:.2f} 行/小时"),
        ("尾部导入超时", str(metrics["import_timeout_count"])),
        ("待异步落稳", str(metrics["pending_async_rows"])),
        ("健康异常行", str(metrics["not_ok_health_rows"])),
        ("Judge 准确率", percent(metrics["judge_accuracy"])),
        ("官方指标", f"{metrics['official_metric'] or '-'} {percent(metrics['official_score']) if metrics['official_score'] is not None else '-'}"),
        ("答案 EM", percent(metrics["answer_em"])),
        ("答案 F1", percent(metrics["answer_f1"])),
        ("平均注入时间", seconds_text(metrics["avg_memory_injection_time_s"])),
        ("总注入时间", seconds_text(metrics["total_memory_injection_time_s"])),
        ("平均记忆落稳等待", seconds_text(metrics["avg_memory_settle_wait_time_s"])),
        ("总记忆落稳等待", seconds_text(metrics["total_memory_settle_wait_time_s"])),
        ("平均 QA 时间", seconds_text(metrics["avg_qa_time_s"])),
        ("总 QA 时间", seconds_text(metrics["total_qa_time_s"])),
        ("平均端到端", seconds_text(metrics["avg_end_to_end_time_s"])),
        ("总端到端", seconds_text(metrics["total_end_to_end_time_s"])),
    ]
    warning_html = (
        "<section class='card' style='margin-top:12px'><strong>运行风险提示</strong>"
        + "".join(f"<p>{esc(item)}</p>" for item in metrics["run_warnings"])
        + "</section>"
    ) if metrics["run_warnings"] else ""
    diagnostic_path = run_dir / "diagnostic.html"
    diagnostic_link = "diagnostic.html" if diagnostic_path.exists() else ""
    table_rows = []
    for index, row in enumerate(recent_rows, max(1, len(rows) - len(recent_rows) + 1)):
        badge_class, badge_text = verdict_badge(row)
        table_rows.append(
            f"""
            <tr>
              <td>{index}</td>
              <td>{esc(row.get("question_id") or row.get("sample_id") or "")}</td>
              <td><span class="badge {badge_class}">{badge_text}</span></td>
              <td>{esc(row.get("health_status") or "-")}</td>
              <td>{seconds_text(row.get("memory_injection_time_s"))}</td>
              <td>{seconds_text(row.get("memory_settle_wait_elapsed_s"))}</td>
              <td>{seconds_text(row.get("qa_time_s") or row.get("time_cost"))}</td>
              <td>{seconds_text(row.get("end_to_end_time_s"))}</td>
              <td>{esc((row.get("response") or "")[:180])}</td>
            </tr>
            """
        )
    command = manifest.get("command")
    if isinstance(command, list):
        command_text = " ".join(str(item) for item in command)
    else:
        command_text = str(command or "")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {f'<meta http-equiv="refresh" content="20">' if auto_refresh else ''}
  <title>{esc(title)}</title>
  <style>
    :root {{
      --bg:#ffffff; --ink:#111827; --muted:#4b5563; --line:#e5e7eb; --soft:#f8fafc;
      --good:#166534; --good-bg:#f0fdf4; --bad:#b91c1c; --bad-bg:#fef2f2; --pending:#92400e; --pending-bg:#fffbeb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
    header {{ padding:28px 24px 18px; border-bottom:1px solid var(--line); }}
    main {{ max-width:1380px; margin:0 auto; padding:20px 24px 44px; }}
    h1 {{ margin:0 0 6px; font-size:28px; }}
    h2 {{ margin:28px 0 12px; font-size:20px; }}
    p, li {{ margin:6px 0; font-size:14px; }}
    code, pre {{ font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    code {{ background:var(--soft); padding:2px 5px; border-radius:4px; }}
    pre {{ white-space:pre-wrap; word-break:break-word; background:var(--soft); border:1px solid var(--line); border-radius:8px; padding:12px; }}
    .small {{ color:var(--muted); font-size:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .card {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .stat {{ margin-top:8px; font-size:24px; font-weight:700; }}
    .path-grid {{ display:grid; grid-template-columns:180px 1fr; gap:8px 12px; align-items:start; }}
    .path-grid strong {{ font-size:13px; color:var(--muted); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; font-size:13px; }}
    th {{ background:var(--soft); position:sticky; top:0; }}
    .badge {{ display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; font-weight:600; }}
    .badge.correct {{ color:var(--good); background:var(--good-bg); }}
    .badge.wrong {{ color:var(--bad); background:var(--bad-bg); }}
    .badge.pending {{ color:var(--pending); background:var(--pending-bg); }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
    .button-link {{ display:inline-flex; align-items:center; justify-content:center; min-height:34px; padding:0 12px; border:1px solid var(--line); border-radius:8px; background:#fff; color:var(--ink); text-decoration:none; }}
    .button-link:hover {{ background:var(--soft); text-decoration:none; }}
    @media (max-width:1100px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media (max-width:700px) {{ header, main {{ padding-left:16px; padding-right:16px; }} .grid {{ grid-template-columns:1fr; }} .path-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<header>
  <div class="small">生成时间：{esc(datetime.now().isoformat(timespec="seconds"))}</div>
  <h1>{esc(title)}</h1>
  <p>{esc(task_name)}</p>
  <p class="small">这份报告会在任务运行期间持续刷新；任务结束后自动变成最终版。{esc('当前浏览器页每 20 秒自动刷新。' if auto_refresh else '任务已结束，当前页不再自动刷新。')}</p>
  <div class="actions">
    {f'<a class="button-link" href="{esc(diagnostic_link)}" target="_blank" rel="noreferrer">运行诊断</a>' if diagnostic_link else ''}
  </div>
</header>
<main>
  <section class="card">
    <div class="path-grid">
      <strong>Task ID</strong><div><code>{esc(task_id)}</code></div>
      <strong>Dataset</strong><div><code>{esc(dataset_label)}</code></div>
      <strong>Run Dir</strong><div><code>{esc(run_dir)}</code></div>
      <strong>CSV</strong><div><code>{esc(csv_path)}</code></div>
      <strong>Report</strong><div><code>{esc(output_path)}</code></div>
      <strong>最近完成题</strong><div><code>{esc(metrics["last_question_id"] or "-")}</code></div>
      <strong>运行态更新时间</strong><div><code>{esc(metrics["updated_at"] or "-")}</code></div>
      <strong>运行态新鲜度</strong><div><code>{esc(format_duration_seconds(metrics["freshness_age_seconds"]))}</code></div>
      <strong>当前阶段</strong><div><code>{esc(phase_text)}</code></div>
      <strong>进度</strong><div><code>{esc(progress_text)}</code></div>
      <strong>已运行</strong><div><code>{esc(elapsed_text)}</code></div>
      <strong>预计剩余</strong><div><code>{esc(eta_text)}</code></div>
      <strong>预计完成</strong><div><code>{esc(finish_eta_text)}</code></div>
      <strong>当前明细</strong><div><code>{esc(metrics["progress_detail"] or "-")}</code></div>
      <strong>当前记忆写入</strong><div><code>{esc(metrics["progress_current_import"] or "-")}</code></div>
      <strong>官方指标范围</strong><div><code>{esc(metrics["official_metric_scope"] or "-")}</code></div>
      <strong>结束时间</strong><div><code>{esc(ended_at_text or "-")}</code></div>
      <strong>返回码</strong><div><code>{esc(returncode_text)}</code></div>
    </div>
  </section>

  {f"<section class='card' style='margin-top:12px'><strong>运行告警</strong><p>{esc('；'.join(str(item) for item in metrics['progress_warnings']) or '当前没有运行态告警。')}</p></section>" if auto_refresh else ""}

  <section class='card' style='margin-top:12px'>
    <strong>运行健康</strong>
    <div class="path-grid" style="margin-top:10px">
      <strong>日志尾部 import-timeout</strong><div><code>{esc(metrics["import_timeout_count"])}</code></div>
      <strong>日志尾部 Graph sync 失败</strong><div><code>{esc(metrics["graph_sync_failure_count"])}</code></div>
      <strong>日志尾部 Projection 失败</strong><div><code>{esc(metrics["episode_projection_failure_count"])}</code></div>
      <strong>日志尾部 Entity persistence 失败</strong><div><code>{esc(metrics["entity_persistence_failure_count"])}</code></div>
      <strong>日志尾部 commit incomplete</strong><div><code>{esc(metrics["commit_incomplete_count"])}</code></div>
      <strong>日志尾部 flush incomplete</strong><div><code>{esc(metrics["flush_incomplete_count"])}</code></div>
      <strong>日志尾部模型服务错误</strong><div><code>{esc(metrics["model_gateway_failure_count"])}</code></div>
      <strong>import_failed 行</strong><div><code>{esc(metrics["import_failed_rows"])}</code></div>
      <strong>pending_async 行</strong><div><code>{esc(metrics["pending_async_rows"])}</code></div>
      <strong>模型异常行</strong><div><code>{esc(metrics["model_failed_rows"])}</code></div>
      <strong>检索异常行</strong><div><code>{esc(metrics["retrieval_error_rows"])}</code></div>
    </div>
    <p>{esc(metrics["latest_import_timeout"] or "最近没有新的 import-timeout。")}</p>
    <p>{esc(metrics["latest_backend_failure"] or "最近没有新的 graph/projection/persistence 失败。")}</p>
    <p>{esc(metrics["latest_model_issue"] or "最近没有新的模型服务错误。")}</p>
    <p>{esc(metrics["latest_import_issue"] or "最近结果行没有新的导入异常摘要。")}</p>
    <p class="small">日志类计数只基于近期日志尾部；结果行计数基于当前 CSV 全量已写结果。</p>
  </section>

  {warning_html}

  <section class='card' style='margin-top:12px'>
    <strong>最终产物状态</strong>
    <div class="path-grid" style="margin-top:10px">
      {''.join(f"<strong>{esc(label)}</strong><div><code>{'ready' if exists else 'pending'}</code></div>" for label, exists in artifact_items)}
    </div>
  </section>

  <section>
    <h2>核心指标</h2>
    <div class="grid">
      {''.join(f"<article class='card'><div class='label'>{esc(label)}</div><div class='stat'>{esc(value)}</div></article>" for label, value in cards)}
    </div>
  </section>

  <section>
    <h2>最近结果行</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Question ID</th>
            <th>Judge</th>
            <th>Health</th>
            <th>注入</th>
            <th>落稳等待</th>
            <th>QA</th>
            <th>端到端</th>
            <th>Response</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows) if table_rows else "<tr><td colspan='9'>当前还没有结果行。</td></tr>"}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>命令</h2>
    <pre>{esc(command_text or "-")}</pre>
  </section>
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a live HTML report for a generic benchmark run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--running-summary", default="")
    parser.add_argument("--judge-summary", default="")
    parser.add_argument("--official-summary", default="")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--title", default="Generic Benchmark Live Report")
    parser.add_argument("--output", default="")
    parser.add_argument("--tasks-api-base", default="http://127.0.0.1:19181")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    csv_path = Path(args.csv).expanduser().resolve()
    output_dir = csv_path.parent
    summary_path = Path(args.summary).expanduser().resolve() if args.summary else (output_dir / "summary.json")
    running_path = Path(args.running_summary).expanduser().resolve() if args.running_summary else (output_dir / "running_summary.json")
    status_path = output_dir / "generic_qa_status.json"
    judge_path = Path(args.judge_summary).expanduser().resolve() if args.judge_summary else (output_dir / "judge_summary.json")
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else (run_dir / "manifest.json")
    if args.official_summary:
        official_path = Path(args.official_summary).expanduser().resolve()
    else:
        official_path = output_dir / "hotpotqa_answer_summary.json"
        if not official_path.exists():
            official_path = output_dir / "longmemeval_official_summary.json"
    output_path = Path(args.output).expanduser().resolve() if args.output else (run_dir / "report.html")

    summary = read_json(summary_path)
    running = read_json(running_path)
    status_json = read_json(status_path)
    judge = read_json(judge_path)
    official = read_json(official_path)
    manifest = read_json(manifest_path)
    rows = read_csv(csv_path)
    task_progress = fetch_task_progress(args.tasks_api_base, str(manifest.get("id") or run_dir.name))
    output_path.write_text(
        render_report(
            title=args.title,
            output_path=output_path,
            run_dir=run_dir,
            csv_path=csv_path,
            manifest=manifest,
            summary=summary,
            running=running,
            judge=judge,
            official=official,
            rows=rows,
            task_progress=task_progress,
            status_json=status_json,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
