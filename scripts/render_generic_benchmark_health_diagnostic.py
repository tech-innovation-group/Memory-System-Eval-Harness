#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory.reports import parse_csv_summary, wrong_clusters_for_csv  # noqa: E402


TERMINAL_STATUSES = {"succeeded", "failed", "done", "cancelled", "canceled"}


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
    number = safe_float(value)
    return int(number) if number is not None else None


def percent(value: Any) -> str:
    number = safe_float(value)
    return "-" if number is None else f"{number * 100:.2f}%"


def seconds_text(value: Any) -> str:
    number = safe_float(value)
    return "-" if number is None else f"{number:.2f}s"


def clip(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def pick(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def format_duration_seconds(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    total = max(0, int(round(number)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


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


def parse_log_runtime_health(run_dir: Path) -> dict[str, Any]:
    text = read_tail_text(run_dir / "run.log")
    if not text:
        return {}
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
        "model_gateway_failures": len(re.findall(r"ModelGatewayError:", text)),
        "latest_model_issue": latest_model_issue,
    }

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
    return {
        "current": current,
        "total": total,
        "phase": phase,
        "detail": f"{phase} {current}/{total}" if total > 0 else phase,
    }


def count_values(rows: list[dict[str, str]], field: str) -> list[tuple[str, int]]:
    counts = Counter()
    for row in rows:
        value = str(row.get(field) or "").strip() or "-"
        counts[value] += 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def row_number_key(row: dict[str, str]) -> int:
    for key in ("question_index", "row_index"):
        value = safe_int(row.get(key))
        if value is not None:
            return value
    return -1


def problem_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        judge = str(row.get("result") or row.get("simple_grade") or row.get("simple_match") or "").strip().upper()
        health = str(row.get("health_status") or "").strip().lower()
        model_status = str(row.get("model_status") or "").strip().lower()
        retrieval_status = str(row.get("retrieval_status") or "").strip().lower()
        import_integrity = str(row.get("import_integrity") or "").strip().lower()
        if judge not in {"CORRECT", "MATCH"} or health not in {"", "ok"} or model_status not in {"", "ok"} or retrieval_status not in {"", "ok"} or import_integrity in {"pending_async_memory"}:
            result.append(row)
    return result


def top_rows(rows: list[dict[str, str]], field: str, limit: int = 10) -> list[dict[str, str]]:
    scored = []
    for row in rows:
        value = safe_float(row.get(field))
        if value is None:
            continue
        scored.append((value, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:limit]]


def parse_relevant_memory(row: dict[str, str], limit: int = 2) -> list[dict[str, str]]:
    raw = str(row.get("relevant_memory") or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    memories = []
    for item in payload[:limit]:
        if not isinstance(item, dict):
            continue
        memories.append(
            {
                "uri": str(item.get("uri") or "").strip(),
                "score": str(item.get("score") or "").strip(),
                "content": clip(item.get("content") or item.get("text") or "", 180),
            }
        )
    return memories


def render_memory_snippet(memories: list[dict[str, str]], fallback: str = "-") -> str:
    if not memories:
        return esc(fallback)
    parts = []
    for item in memories:
        parts.append(
            f"<div><code>{esc(item.get('uri') or '-')}</code>"
            f"{' · score ' + esc(item.get('score')) if item.get('score') else ''}"
            f"<br>{esc(item.get('content') or '-')}</div>"
        )
    return "".join(parts)


def render_breakdown_table(title: str, items: list[tuple[str, int]]) -> str:
    rows = "".join(
        f"<tr><td><code>{esc(label)}</code></td><td>{count}</td></tr>"
        for label, count in items
    ) or "<tr><td colspan='2'>-</td></tr>"
    return f"""
    <section class="card">
      <h3>{esc(title)}</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>值</th><th>行数</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """


def render_slowest_table(title: str, rows: list[dict[str, str]], field: str) -> str:
    body = []
    for row in rows:
        body.append(
            f"""
            <tr>
              <td>{esc(row.get("question_id") or row.get("sample_id") or "")}</td>
              <td>{esc(row.get("category") or "-")}</td>
              <td>{seconds_text(row.get(field))}</td>
              <td>{esc(row.get("health_status") or "-")}</td>
              <td>{esc(clip(row.get("question"), 120))}</td>
              <td>{esc(clip(row.get("response"), 120))}</td>
            </tr>
            """
        )
    if not body:
        body.append("<tr><td colspan='6'>当前还没有可用耗时行。</td></tr>")
    return f"""
    <section class="card">
      <h3>{esc(title)}</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Question ID</th>
              <th>Category</th>
              <th>耗时</th>
              <th>Health</th>
              <th>Question</th>
              <th>Response</th>
            </tr>
          </thead>
          <tbody>{''.join(body)}</tbody>
        </table>
      </div>
    </section>
    """


def render_problem_examples(rows: list[dict[str, str]]) -> str:
    body = []
    for row in rows[:20]:
        memories = parse_relevant_memory(row)
        body.append(
            f"""
            <tr>
              <td>{esc(row.get("question_id") or row.get("sample_id") or "")}</td>
              <td>{esc(row.get("result") or row.get("simple_grade") or row.get("simple_match") or "-")}</td>
              <td>{esc(row.get("health_status") or "-")}</td>
              <td>{esc(row.get("import_integrity") or "-")}</td>
              <td>{seconds_text(row.get("memory_injection_time_s"))}</td>
              <td>{seconds_text(row.get("qa_time_s") or row.get("time_cost"))}</td>
              <td>{esc(clip(row.get("question"), 120))}</td>
              <td>{esc(clip(row.get("reasoning"), 140))}</td>
              <td>{render_memory_snippet(memories, clip(row.get("context_preview"), 160) or "-")}</td>
            </tr>
            """
        )
    if not body:
        body.append("<tr><td colspan='9'>当前还没有问题行。</td></tr>")
    return f"""
    <section class="card">
      <h2>当前问题样本</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Question ID</th>
              <th>Judge</th>
              <th>Health</th>
              <th>Import Integrity</th>
              <th>注入</th>
              <th>QA</th>
              <th>Question</th>
              <th>Reasoning</th>
              <th>Top Evidence</th>
            </tr>
          </thead>
          <tbody>{''.join(body)}</tbody>
        </table>
      </div>
    </section>
    """


def render_problem_detail_cards(rows: list[dict[str, str]]) -> str:
    cards = []
    for row in rows[:12]:
        memories = parse_relevant_memory(row, limit=3)
        cards.append(
            f"""
            <article class="card problem-detail-card" data-question-id="{esc(row.get("question_id") or row.get("sample_id") or "-")}">
              <h3>{esc(row.get("question_id") or row.get("sample_id") or "-")}</h3>
              <p><strong>Question:</strong> {esc(row.get("question") or "-")}</p>
              <p><strong>Judge:</strong> {esc(row.get("result") or row.get("simple_grade") or row.get("simple_match") or "-")} · <strong>Health:</strong> {esc(row.get("health_status") or "-")} · <strong>Import:</strong> {esc(row.get("import_integrity") or "-")}</p>
              <p><strong>注入/QA/端到端:</strong> {seconds_text(row.get("memory_injection_time_s"))} / {seconds_text(row.get("qa_time_s") or row.get("time_cost"))} / {seconds_text(row.get("end_to_end_time_s"))}</p>
              <p><strong>Response:</strong> {esc(clip(row.get("response"), 240))}</p>
              <p><strong>Reasoning:</strong> {esc(clip(row.get("reasoning"), 260))}</p>
              <p><strong>Top Evidence:</strong></p>
              <div class="evidence-stack">{render_memory_snippet(memories, clip(row.get("context_preview"), 220) or "-")}</div>
            </article>
            """
        )
    if not cards:
        return ""
    return f"""
    <section class="section-stack">
      <h2>问题样本详情</h2>
      <div class="breakdowns">{''.join(cards)}</div>
    </section>
    """


def render_snapshot_section(status_json: dict[str, Any]) -> str:
    snapshot = status_json.get("snapshot") if isinstance(status_json.get("snapshot"), dict) else {}
    if not snapshot:
        return ""
    items = [
        ("workspace", snapshot.get("workspace")),
        ("account", snapshot.get("account")),
        ("sample", snapshot.get("sample")),
        ("session_count", snapshot.get("session_count")),
        ("submitted_messages", snapshot.get("submitted_messages")),
        ("complete_sessions", snapshot.get("complete_sessions")),
        ("abstract_count", snapshot.get("abstract_count")),
        ("overview_count", snapshot.get("overview_count")),
        ("atom_count", snapshot.get("atom_count")),
        ("graph_count", snapshot.get("graph_count")),
        ("vector_count", snapshot.get("vector_count")),
        ("signature", snapshot.get("signature")),
    ]
    rows = "".join(
        f"<strong>{esc(label)}</strong><div><code>{esc(value if value not in (None, '') else '-')}</code></div>"
        for label, value in items
    )
    return f"""
    <section class="card" id="diagnosticSnapshotSection">
      <h2>当前记忆快照</h2>
      <div class="path-grid">{rows}</div>
    </section>
    """


def render_failure_buckets(analysis: dict[str, Any]) -> str:
    attribution = analysis.get("failure_attribution") if isinstance(analysis.get("failure_attribution"), dict) else {}
    buckets = attribution.get("buckets") if isinstance(attribution.get("buckets"), list) else []
    action_items = attribution.get("action_items") if isinstance(attribution.get("action_items"), list) else []
    summary_cards = [
        ("问题行", attribution.get("problem_rows")),
        ("可重试", attribution.get("retryable_rows")),
        ("Owner:model", (attribution.get("owner_counts") or {}).get("model")),
        ("Owner:retrieval", (attribution.get("owner_counts") or {}).get("retrieval")),
        ("Owner:judge", (attribution.get("owner_counts") or {}).get("judge")),
    ]
    rows = []
    for item in buckets[:12]:
        examples = item.get("examples") if isinstance(item.get("examples"), list) else []
        example_text = " / ".join(
            clip(
                f"{example.get('question_id') or example.get('sample_id') or '-'}: {example.get('question') or ''}",
                120,
            )
            for example in examples[:2]
        ) or "-"
        rows.append(
            f"""
            <tr>
              <td>{esc(item.get("label") or item.get("mode") or "-")}</td>
              <td>{esc(item.get("severity") or "-")}</td>
              <td>{esc(item.get("owner") or "-")}</td>
              <td>{esc("yes" if item.get("retryable") else "no")}</td>
              <td>{esc(item.get("count") or 0)}</td>
              <td>{esc(item.get("reason") or "-")}</td>
              <td>{esc(example_text)}</td>
            </tr>
            """
        )
    if not rows:
        rows.append("<tr><td colspan='7'>当前还没有 failure attribution 数据。</td></tr>")
    actions = "".join(f"<li>{esc(item)}</li>" for item in action_items) or "<li>当前没有额外 action item。</li>"
    cards = "".join(
        f"<article class='mini-card'><div class='mini-label'>{esc(label)}</div><div class='mini-stat'>{esc(value if value not in (None, '') else '-')}</div></article>"
        for label, value in summary_cards
    )
    return f"""
    <section class="card">
      <h2>失败归因</h2>
      <div class="mini-grid">{cards}</div>
      <div class="table-wrap" style="margin-top:12px">
        <table>
          <thead>
            <tr>
              <th>Bucket</th>
              <th>Severity</th>
              <th>Owner</th>
              <th>Retryable</th>
              <th>Count</th>
              <th>Reason</th>
              <th>Examples</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
      <h3 style="margin-top:16px">建议动作</h3>
      <ul>{actions}</ul>
    </section>
    """


def render_failure_clusters(analysis: dict[str, Any]) -> str:
    clusters = ((analysis.get("failure_clusters") or {}).get("clusters")) if isinstance(analysis.get("failure_clusters"), dict) else []
    rows = []
    for item in (clusters or [])[:12]:
        top_samples = item.get("top_samples") if isinstance(item.get("top_samples"), list) else []
        sample_text = " / ".join(f"{sample}:{count}" for sample, count in top_samples[:3]) or "-"
        rows.append(
            f"""
            <tr>
              <td>{esc(item.get("label") or "-")}</td>
              <td>{esc(item.get("count") or 0)}</td>
              <td>{esc(sample_text)}</td>
            </tr>
            """
        )
    if not rows:
        rows.append("<tr><td colspan='3'>当前还没有 failure cluster 数据。</td></tr>")
    return f"""
    <section class="card">
      <h2>失败簇</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Cluster</th><th>Count</th><th>Top Samples</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    </section>
    """


def render_report(
    *,
    title: str,
    output_path: Path,
    run_dir: Path,
    csv_path: Path,
    manifest: dict[str, Any],
    running: dict[str, Any],
    summary_json: dict[str, Any],
    judge: dict[str, Any],
    status_json: dict[str, Any],
    csv_summary: dict[str, Any],
    rows: list[dict[str, str]],
    wrong_analysis: dict[str, Any],
) -> str:
    task_name = str(pick(manifest.get("name"), manifest.get("id"), title) or title)
    task_id = str(pick(manifest.get("id"), run_dir.name) or run_dir.name)
    summary_status = str(pick(manifest.get("status"), running.get("status"), summary_json.get("status")) or "").strip().lower()
    auto_refresh = summary_status not in TERMINAL_STATUSES
    dataset_format = str(pick(manifest.get("dataset_format"), manifest.get("config", {}).get("dataset_format") if isinstance(manifest.get("config"), dict) else None, summary_json.get("dataset_format")) or "").strip()
    progress_text = "-"
    log_progress = parse_log_progress(run_dir)
    task_current = safe_int(log_progress.get("current")) if isinstance(log_progress, dict) else None
    task_total = safe_int(log_progress.get("total")) if isinstance(log_progress, dict) else None
    status_current = safe_int(status_json.get("job_index"))
    status_total = safe_int(status_json.get("job_total"))
    if task_current is not None and task_total is not None:
        progress_text = f"{task_current} / {task_total}"
    elif status_current is not None and status_total is not None:
        progress_text = f"{status_current} / {status_total}"
    elif safe_int(running.get("rows")) is not None:
        progress_text = f"{safe_int(running.get('rows'))} / -"
    live_report_exists = (run_dir / "report.html").exists()
    manifest_config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    import_timeout_s = safe_int(manifest_config.get("import_timeout_s"))
    commit_call_timeout_s = safe_int(manifest_config.get("commit_call_timeout_s"))
    settle_timeout_s = safe_int(status_json.get("stabilize_timeout_seconds"))
    stable_hits = safe_int(status_json.get("stable_hits"))
    required_stable_hits = safe_int(status_json.get("required_stable_hits"))
    settle_timed_out = status_json.get("timed_out")
    import_failed_rows = (csv_summary.get("health_counts") or {}).get("import_failed") or sum(1 for row in rows if str(row.get("health_status") or "") == "import_failed")
    pending_async_rows = (csv_summary.get("health_counts") or {}).get("pending_async_memory") or sum(1 for row in rows if str(row.get("import_integrity") or "") == "pending_async_memory")
    tail_import_failed_streak = 0
    tail_pending_async_streak = 0
    recent_rows = rows[-5:]
    recent_import_failed_rows = 0
    recent_pending_async_rows = 0
    for row in reversed(rows):
        if str(row.get("health_status") or "") == "import_failed":
            tail_import_failed_streak += 1
        else:
            break
    for row in reversed(rows):
        if str(row.get("import_integrity") or "") == "pending_async_memory":
            tail_pending_async_streak += 1
        else:
            break
    for row in recent_rows:
        if str(row.get("health_status") or "") == "import_failed":
            recent_import_failed_rows += 1
        if str(row.get("import_integrity") or "") == "pending_async_memory":
            recent_pending_async_rows += 1
    freshness_source = pick(status_json.get("checked_at"), running.get("updated_at"), summary_json.get("updated_at")) or "-"
    freshness_dt = parse_flexible_timestamp(freshness_source)
    freshness_age_seconds = None
    if freshness_dt is not None:
        now_dt = datetime.now(freshness_dt.tzinfo) if freshness_dt.tzinfo is not None else datetime.now()
        freshness_age_seconds = max(0.0, (now_dt - freshness_dt).total_seconds())
    log_runtime_health = parse_log_runtime_health(run_dir)
    cards = [
        ("当前进度", progress_text),
        ("当前阶段", normalize_stage_label(status_json.get("stage")) or "-"),
        ("运行态新鲜度", format_duration_seconds(freshness_age_seconds)),
        ("结果行数", pick(running.get("rows"), csv_summary.get("count"), len(rows)) or 0),
        ("pending_async", (csv_summary.get("health_counts") or {}).get("pending_async_memory") or sum(1 for row in rows if str(row.get("import_integrity") or "") == "pending_async_memory")),
        ("import_failed", (csv_summary.get("health_counts") or {}).get("import_failed") or sum(1 for row in rows if str(row.get("health_status") or "") == "import_failed")),
        ("Judge 准确率", percent(pick(judge.get("accuracy"), csv_summary.get("accuracy")))),
        ("平均注入", seconds_text(pick(running.get("avg_memory_injection_time_s"), csv_summary.get("avg_memory_injection_time_s")))),
        ("平均 QA", seconds_text(pick(running.get("avg_qa_time_s"), csv_summary.get("avg_qa_time_s")))),
        ("平均端到端", seconds_text(pick(running.get("avg_end_to_end_time_s"), csv_summary.get("avg_end_to_end_time_s")))),
        ("总注入", seconds_text(pick(running.get("total_memory_injection_time_s"), csv_summary.get("total_memory_injection_time_s")))),
        ("总 QA", seconds_text(pick(running.get("total_qa_time_s"), csv_summary.get("total_qa_time_s")))),
        ("总端到端", seconds_text(pick(running.get("total_end_to_end_time_s"), csv_summary.get("total_end_to_end_time_s")))),
    ]
    warning_lines: list[str] = []
    if import_timeout_s and commit_call_timeout_s and import_timeout_s < commit_call_timeout_s:
        warning_lines.append(
            f"当前 run 的 import_timeout_s={import_timeout_s} 小于 commit_call_timeout_s={commit_call_timeout_s}。这会让导入比 commit 调用本身更早超时，当前 import_failed 需要按这个配置风险解读。"
        )
    if import_failed_rows:
        warning_lines.append(f"当前已经累计 {import_failed_rows} 行 import_failed。")
    if pending_async_rows:
        warning_lines.append(f"当前还有 {pending_async_rows} 行 pending_async_memory，说明部分记忆写入仍在等待稳定。")
    if tail_import_failed_streak >= 2:
        warning_lines.append(f"最近连续 {tail_import_failed_streak} 行都是 import_failed，新增样本暂时没有进入正常 QA。")
    if tail_pending_async_streak >= 3:
        warning_lines.append(f"最近连续 {tail_pending_async_streak} 行都是 pending_async_memory，说明尾部样本仍在等待落稳。")
    if recent_import_failed_rows >= 3:
        warning_lines.append(f"最近 5 行里有 {recent_import_failed_rows} 行 import_failed。")
    if recent_pending_async_rows >= 3:
        warning_lines.append(f"最近 5 行里有 {recent_pending_async_rows} 行 pending_async_memory。")
    latest_model_issue = str(log_runtime_health.get("latest_model_issue") or "").strip()
    if latest_model_issue:
        if any(token in latest_model_issue.lower() for token in ("arrearage", "access denied", "overdue-payment")):
            warning_lines.append("后台日志已经出现模型服务 Arrearage / Access denied。当前尾部 import_failed 和 pending_async 升高大概率与这个外部错误直接相关。")
        else:
            warning_lines.append(f"后台日志最近还有模型服务异常：{clip(latest_model_issue, 220)}")
    if normalize_stage_label(status_json.get("stage")) == "settle":
        warning_lines.append(
            f"当前处于 settle 阶段，stable_hits={stable_hits if stable_hits is not None else '-'} / {required_stable_hits if required_stable_hits is not None else '-'}。"
        )
    if settle_timed_out:
        warning_lines.append("当前 settle 阶段已经超时。")
    if freshness_age_seconds is not None and freshness_age_seconds >= 180 and summary_status not in TERMINAL_STATUSES:
        warning_lines.append(
            f"运行态已经 {format_duration_seconds(freshness_age_seconds)} 没有刷新。当前任务可能停滞，建议检查 run.log 和后台进程。"
        )
    paths = [
        ("Task ID", task_id),
        ("Dataset", dataset_format or "-"),
        ("Status", summary_status or "-"),
        ("Run Dir", str(run_dir)),
        ("CSV", str(csv_path)),
        ("Live Report", "report.html" if live_report_exists else "-"),
        ("当前样本", pick(status_json.get("sample"), status_json.get("question_id"), (status_json.get("snapshot") or {}).get("sample") if isinstance(status_json.get("snapshot"), dict) else None) or "-"),
        ("更新于", pick(status_json.get("checked_at"), running.get("updated_at"), summary_json.get("updated_at")) or "-"),
        ("运行态新鲜度", format_duration_seconds(freshness_age_seconds)),
        ("Import Timeout", import_timeout_s or safe_int(status_json.get("import_timeout_s")) or "-"),
        ("Settle Timeout", settle_timeout_s or "-"),
        ("Stable Hits", f"{stable_hits if stable_hits is not None else '-'} / {required_stable_hits if required_stable_hits is not None else '-'}"),
        ("Timed Out", "yes" if settle_timed_out else "no"),
        ("Commit Call Timeout", commit_call_timeout_s or "-"),
        ("Model Gateway Failures", safe_int(log_runtime_health.get("model_gateway_failures")) or 0),
        ("Latest Model Issue", clip(log_runtime_health.get("latest_model_issue") or "-", 220)),
    ]
    breakdown_grid = "".join([
        render_breakdown_table("Import Status", count_values(rows, "import_status")),
        render_breakdown_table("Import Integrity", count_values(rows, "import_integrity")),
        render_breakdown_table("Health Status", count_values(rows, "health_status")),
        render_breakdown_table("Model Status", count_values(rows, "model_status")),
        render_breakdown_table("Retrieval Status", count_values(rows, "retrieval_status")),
    ])
    slow_sections = "".join([
        render_slowest_table("最慢注入题", top_rows(rows, "memory_injection_time_s"), "memory_injection_time_s"),
        render_slowest_table("最慢 QA 题", top_rows(rows, "qa_time_s"), "qa_time_s"),
        render_slowest_table("最慢端到端题", top_rows(rows, "end_to_end_time_s"), "end_to_end_time_s"),
    ])
    current_problems = sorted(problem_rows(rows), key=lambda row: safe_float(row.get("end_to_end_time_s")) or 0, reverse=True)
    card_html = "".join(
        f"<article class='stat-card'><div class='stat-label'>{esc(label)}</div><div class='stat-value'>{esc(value)}</div></article>"
        for label, value in cards
    )
    path_rows = "".join(f"<strong>{esc(label)}</strong><div><code>{esc(value)}</code></div>" for label, value in paths)
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
      --good:#166534; --good-bg:#f0fdf4; --bad:#b91c1c; --bad-bg:#fef2f2; --warn:#92400e; --warn-bg:#fffbeb;
      --link:#1d4ed8;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
    header {{ padding:28px 24px 18px; border-bottom:1px solid var(--line); }}
    main {{ max-width:1440px; margin:0 auto; padding:20px 24px 44px; }}
    h1 {{ margin:0 0 8px; font-size:28px; }}
    h2 {{ margin:0 0 12px; font-size:20px; }}
    h3 {{ margin:0 0 10px; font-size:16px; }}
    p, li {{ margin:6px 0; font-size:14px; }}
    a {{ color:var(--link); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    code {{ background:var(--soft); padding:2px 5px; border-radius:4px; font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    .small {{ color:var(--muted); font-size:12px; }}
    .card {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .cards {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .stat-card {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .stat-label, .mini-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .stat-value, .mini-stat {{ margin-top:8px; font-size:24px; font-weight:700; }}
    .mini-grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; }}
    .mini-card {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--soft); }}
    .path-grid {{ display:grid; grid-template-columns:180px 1fr; gap:8px 12px; align-items:start; }}
    .path-grid strong {{ font-size:13px; color:var(--muted); }}
    .section-stack {{ display:grid; gap:12px; }}
    .breakdowns {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; font-size:13px; }}
    th {{ background:var(--soft); position:sticky; top:0; }}
    .callout {{ border:1px solid var(--line); border-radius:8px; padding:12px 14px; background:var(--soft); margin-top:12px; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
    .button-link {{ display:inline-flex; align-items:center; justify-content:center; min-height:34px; padding:0 12px; border:1px solid var(--line); border-radius:8px; background:#fff; color:var(--ink); text-decoration:none; }}
    .button-link:hover {{ background:var(--soft); text-decoration:none; }}
    .evidence-stack {{ display:grid; gap:8px; font-size:12px; }}
    @media (max-width:1200px) {{ .cards, .mini-grid, .breakdowns {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media (max-width:760px) {{ header, main {{ padding-left:16px; padding-right:16px; }} .cards, .mini-grid, .breakdowns {{ grid-template-columns:1fr; }} .path-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<header>
  <div class="small">生成时间：{esc(datetime.now().isoformat(timespec="seconds"))}</div>
  <h1>{esc(title)}</h1>
  <p>{esc(task_name)}</p>
  <p class="small">这页聚焦运行诊断，不只显示进度，还把注入记忆耗时、QA 耗时、导入完整性和失败分桶直接展开。{esc('当前页每 20 秒自动刷新。' if auto_refresh else '任务已结束，当前页不再自动刷新。')}</p>
  <div class="actions">
    {f'<a class="button-link" href="report.html" target="_blank" rel="noreferrer">打开 Live 报告</a>' if live_report_exists else ''}
  </div>
</header>
<main class="section-stack">
  <section class="card">
    <div class="path-grid">{path_rows}</div>
    <div class="callout">
      {esc("当前 run 已失败结束。这里保留的是失败前最后一个题位点和终态诊断。" if summary_status == "failed" else ("当前 run 已结束。这里保留的是终态诊断。" if summary_status in TERMINAL_STATUSES else "当前 run 还没有完成。这里的当前进度直接读运行态文件和日志口径，而不是只看平台任务卡片的汇总口径。"))}
    </div>
    {"".join(f"<div class='callout' style='margin-top:10px'>{esc(line)}</div>" for line in warning_lines)}
  </section>

  {render_snapshot_section(status_json)}

  <section>
    <h2>核心诊断</h2>
    <div class="cards">{card_html}</div>
  </section>

  <section class="breakdowns">{breakdown_grid}</section>

  {render_failure_buckets(wrong_analysis)}

  {render_failure_clusters(wrong_analysis)}

  <section class="breakdowns">{slow_sections}</section>

  {render_problem_examples(current_problems)}

  {render_problem_detail_cards(current_problems)}
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a diagnostic HTML report for a generic benchmark run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--title", default="Generic Benchmark Diagnostic")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    csv_path = Path(args.csv).expanduser().resolve()
    output_dir = csv_path.parent
    output_path = Path(args.output).expanduser().resolve() if args.output else (run_dir / "diagnostic.html")

    manifest = read_json(run_dir / "manifest.json")
    running = read_json(output_dir / "running_summary.json")
    summary_json = read_json(output_dir / "summary.json")
    judge = read_json(output_dir / "judge_summary.json")
    status_json = read_json(output_dir / "generic_qa_status.json")
    rows = read_csv(csv_path)
    csv_summary = parse_csv_summary(csv_path)
    wrong_info = wrong_clusters_for_csv(csv_path)
    wrong_analysis = wrong_info.get("analysis") if isinstance(wrong_info.get("analysis"), dict) else {}

    output_path.write_text(
        render_report(
            title=args.title,
            output_path=output_path,
            run_dir=run_dir,
            csv_path=csv_path,
            manifest=manifest,
            running=running,
            summary_json=summary_json,
            judge=judge,
            status_json=status_json,
            csv_summary=csv_summary,
            rows=rows,
            wrong_analysis=wrong_analysis,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
