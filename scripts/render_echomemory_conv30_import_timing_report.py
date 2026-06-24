#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def compact(text: Any, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_dt(value: str, assume_local: bool = False) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ if assume_local else UTC)
    return dt


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "-"
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def fmt_num(value: float | int, digits: int = 1) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.{digits}f}"


def fmt_secs(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.1f}s"


def html_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def account_root(workspace: Path, account: str) -> Path:
    for candidate in (workspace / account / account, workspace / account, workspace):
        if (candidate / "sessions").exists():
            return candidate
    return workspace / account / account


def token_rows(path: Path, start_utc: datetime | None = None, end_utc: datetime | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        timestamp = parse_dt(str(row.get("timestamp") or ""))
        if start_utc and timestamp and timestamp < start_utc:
            continue
        if end_utc and timestamp and timestamp > end_utc:
            continue
        row["_timestamp"] = timestamp
        row["input_tokens"] = int(row.get("input_tokens") or 0)
        row["output_tokens"] = int(row.get("output_tokens") or 0)
        row["total_tokens"] = int(row.get("total_tokens") or 0)
        row["latency_ms"] = float(row.get("latency_ms") or 0.0)
        rows.append(row)
    return rows


@dataclass
class SessionSnapshot:
    session_id: str
    title: str
    label: str
    expected_messages: int
    actual_messages: int
    first_message_at: datetime | None
    last_message_at: datetime | None
    updated_at: datetime | None
    commit_index: int
    pending_tokens: int
    total_tokens: int
    overview_exists: bool
    abstract_exists: bool
    source: str
    commit_elapsed_s: float | None
    commit_status: str
    retrieval_ready: bool
    cursor_complete: bool
    qa_ready: bool
    integrity: str
    warning: str


def parse_session_messages(path: Path) -> tuple[int, datetime | None, datetime | None]:
    if not path.exists():
        return 0, None, None
    count = 0
    first: datetime | None = None
    last: datetime | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        count += 1
        ts = parse_dt(str(row.get("created_at") or ""))
        if ts and first is None:
            first = ts
        if ts:
            last = ts
    return count, first, last


def collect_sessions(
    workspace: Path,
    account: str,
    summary: dict[str, Any],
    started_local: datetime | None,
) -> list[SessionSnapshot]:
    root = account_root(workspace, account)
    sessions_root = root / "sessions"
    by_id: dict[str, SessionSnapshot] = {}
    records = ((summary.get("records") or [{}])[0].get("session_records") or [])
    expected_by_id = {str(item.get("session_id") or ""): item for item in records}

    for session_dir in sorted(sessions_root.glob("sess-*")):
        meta_path = session_dir / "meta.json"
        meta = read_json(meta_path) if meta_path.exists() else {}
        title = str(meta.get("title") or session_dir.name)
        if "conv-30/session_" not in title:
            continue
        updated_at = parse_dt(str(meta.get("updated_at") or meta.get("created_at") or ""))
        if started_local and updated_at and updated_at < started_local:
            continue
        actual_messages, first_at, last_at = parse_session_messages(session_dir / "messages.jsonl")
        record = expected_by_id.get(session_dir.name, {})
        by_id[session_dir.name] = SessionSnapshot(
            session_id=session_dir.name,
            title=title,
            label=title,
            expected_messages=int(record.get("expected_messages") or 0),
            actual_messages=actual_messages,
            first_message_at=first_at,
            last_message_at=last_at,
            updated_at=updated_at,
            commit_index=int(meta.get("commit_index") or -1),
            pending_tokens=int(meta.get("pending_tokens") or 0),
            total_tokens=int(meta.get("total_tokens") or 0),
            overview_exists=(session_dir / "overview.md").exists(),
            abstract_exists=(session_dir / "abstract.md").exists(),
            source="workspace",
            commit_elapsed_s=float((record.get("commit_response") or {}).get("elapsed_s")) if record.get("commit_response") else None,
            commit_status=str((record.get("commit_response") or {}).get("status") or ""),
            retrieval_ready=bool(record.get("retrieval_ready_after_commit")),
            cursor_complete=bool(record.get("cursor_complete_after_commit")),
            qa_ready=bool(record.get("qa_ready_after_commit")),
            integrity=str(record.get("integrity") or ""),
            warning=str(record.get("commit_warning") or ""),
        )

    for record in records:
        session_id = str(record.get("session_id") or "")
        if not session_id or session_id in by_id:
            continue
        by_id[session_id] = SessionSnapshot(
            session_id=session_id,
            title=record.get("label") or session_id,
            label=record.get("label") or session_id,
            expected_messages=int(record.get("expected_messages") or 0),
            actual_messages=int(record.get("submitted_messages") or 0),
            first_message_at=None,
            last_message_at=None,
            updated_at=None,
            commit_index=int(((record.get("commit_artifacts") or {}).get("commit_index")) or -1),
            pending_tokens=0,
            total_tokens=0,
            overview_exists=False,
            abstract_exists=False,
            source="summary",
            commit_elapsed_s=float((record.get("commit_response") or {}).get("elapsed_s")) if record.get("commit_response") else None,
            commit_status=str((record.get("commit_response") or {}).get("status") or ""),
            retrieval_ready=bool(record.get("retrieval_ready_after_commit")),
            cursor_complete=bool(record.get("cursor_complete_after_commit")),
            qa_ready=bool(record.get("qa_ready_after_commit")),
            integrity=str(record.get("integrity") or ""),
            warning=str(record.get("commit_warning") or ""),
        )
    return sorted(by_id.values(), key=lambda item: item.label)


def aggregate_tokens(rows: list[dict[str, Any]]) -> dict[str, Any]:
    call_sites: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
    })
    chat_total_latency_ms = 0.0
    embedding_total_latency_ms = 0.0
    zero_ms_embedding_count = 0

    for row in rows:
        call_site = str(row.get("call_site") or "unknown")
        bucket = call_sites[call_site]
        bucket["calls"] += 1
        bucket["input_tokens"] += int(row.get("input_tokens") or 0)
        bucket["output_tokens"] += int(row.get("output_tokens") or 0)
        bucket["total_tokens"] += int(row.get("total_tokens") or 0)
        bucket["latency_ms"] += float(row.get("latency_ms") or 0.0)
        if call_site == "embedding":
            embedding_total_latency_ms += float(row.get("latency_ms") or 0.0)
            if float(row.get("latency_ms") or 0.0) <= 0.1:
                zero_ms_embedding_count += 1
        else:
            chat_total_latency_ms += float(row.get("latency_ms") or 0.0)

    return {
        "call_sites": dict(call_sites),
        "chat_total_latency_ms": chat_total_latency_ms,
        "embedding_total_latency_ms": embedding_total_latency_ms,
        "combined_total_latency_ms": chat_total_latency_ms + embedding_total_latency_ms,
        "chat_input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows if str(row.get("call_site")) != "embedding"),
        "chat_output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows if str(row.get("call_site")) != "embedding"),
        "chat_total_tokens": sum(int(row.get("total_tokens") or 0) for row in rows if str(row.get("call_site")) != "embedding"),
        "zero_ms_embedding_count": zero_ms_embedding_count,
    }


def render_html(report: dict[str, Any]) -> str:
    finished = bool(report["run_finished"])
    summary_status_class = "ok" if finished and report["manifest_status"] == "succeeded" else ("bad" if report["manifest_status"] == "failed" else "")
    state_sentence = (
        "当前 run 已结束，以下是最终完结口径。"
        if finished
        else "run 仍在运行，以下是运行中快照，不是最终完结口径。"
    )
    timing_rows = [
        ["Bootstrap runtime ready", fmt_secs(report["bootstrap_elapsed_s"]), "run.log `[bootstrap] runtime_ready`"],
        ["Full run elapsed", fmt_secs(report["run_elapsed_s"]), "manifest `started_at -> ended_at`"],
        ["Message write wall span", fmt_secs(report["message_write_span_s"]), "first persisted message -> last persisted message"],
        ["Message write sum", fmt_secs(report["message_write_total_s"]), "sum of per-session write windows"],
        ["Accepted commit time (finished sessions sum)", fmt_secs(report["commit_elapsed_total_s"]), f"{report['finished_sessions']} finished sessions"],
        ["Message persistence time (latest active session write window)", fmt_secs(report["pending_session_write_s"]), "first message -> last message in `messages.jsonl`"],
        ["Chat LLM latency total", fmt_secs(report["chat_llm_latency_s"]), "from `metrics/llm_tokens/*.jsonl`"],
        ["Embedding latency total", fmt_secs(report["embedding_latency_s"]), "embedding rows currently report 0 tokens"],
        ["Combined model latency total", fmt_secs(report["combined_latency_s"]), "chat + embedding"],
        ["Non-LLM remainder", fmt_secs(report["non_llm_remainder_s"]), "run elapsed - combined model latency"],
    ]

    call_site_rows: list[list[str]] = []
    for call_site, bucket in sorted(
        (report["token_summary"]["call_sites"] or {}).items(),
        key=lambda item: (-int(item[1]["total_tokens"]), item[0]),
    ):
        call_site_rows.append([
            f"<code>{esc(call_site)}</code>",
            fmt_num(int(bucket["calls"]), 0),
            fmt_num(int(bucket["input_tokens"]), 0),
            fmt_num(int(bucket["output_tokens"]), 0),
            fmt_num(int(bucket["total_tokens"]), 0),
            fmt_secs(float(bucket["latency_ms"]) / 1000.0),
        ])

    session_rows: list[list[str]] = []
    for item in report["sessions"]:
        status_bits = []
        if item["commit_index"] >= 0:
            status_bits.append(f"commit={item['commit_index']}")
        else:
            status_bits.append("commit=-1")
        if item["retrieval_ready"]:
            status_bits.append("retrieval_ready")
        if item["cursor_complete"]:
            status_bits.append("cursor_complete")
        if item["qa_ready"]:
            status_bits.append("qa_ready")
        if item["overview_exists"]:
            status_bits.append("overview")
        if item["abstract_exists"]:
            status_bits.append("abstract")
        session_rows.append([
            f"<strong>{esc(item['label'])}</strong><div class='sub'>{esc(item['session_id'])}</div>",
            f"{item['actual_messages']}/{item['expected_messages'] or '-'}",
            esc(" · ".join(status_bits) or "-"),
            fmt_dt(item["first_message_at"]),
            fmt_dt(item["last_message_at"]),
            fmt_secs(item["commit_elapsed_s"]),
            esc(item["commit_status"] or item["integrity"] or "-"),
            esc(compact(item["warning"], 120) if item["warning"] else "-"),
        ])

    anomaly_items = "".join(
        f"<li>{esc(item)}</li>" for item in report["anomalies"]
    )
    evidence_items = "".join(
        f"<li><code>{esc(path)}</code></li>" for path in report["paths"]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory conv-30 导入时间与异常快照</title>
  <style>
    :root {{
      --bg:#f5f5f7; --panel:#ffffff; --text:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7;
      --soft:#f2f2f5; --blue:#0b63ce; --amber:#9a6700; --red:#c9342f; --green:#1d9b5f;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.65 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; }}
    main {{ max-width:1200px; margin:0 auto; padding:28px 18px 64px; }}
    h1,h2,h3,p,ul {{ margin:0; }}
    .hero {{ background:linear-gradient(135deg,#ffffff,#eef4ff); border:1px solid var(--line); border-radius:18px; padding:28px; margin-bottom:18px; }}
    .hero h1 {{ font-size:30px; line-height:1.2; margin-bottom:10px; }}
    .hero p {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:18px; }}
    .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px; }}
    .stat strong {{ display:block; font-size:24px; line-height:1.1; margin-bottom:6px; }}
    .section {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:20px; margin-top:16px; }}
    .section h2 {{ font-size:20px; margin-bottom:12px; }}
    .section h3 {{ font-size:16px; margin-bottom:8px; }}
    .sub {{ color:var(--muted); font-size:12px; margin-top:4px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }}
    .chip {{ background:var(--soft); border:1px solid var(--line); border-radius:999px; padding:5px 10px; font-size:12px; }}
    .warn {{ color:var(--amber); }}
    .bad {{ color:var(--red); }}
    .ok {{ color:var(--green); }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
    th, td {{ border-bottom:1px solid var(--line); padding:10px 8px; text-align:left; vertical-align:top; }}
    th {{ font-size:12px; color:var(--muted); font-weight:600; }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; background:var(--soft); padding:2px 5px; border-radius:6px; }}
    ul {{ padding-left:18px; display:grid; gap:8px; }}
    .note {{ color:var(--muted); margin-top:8px; }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>EchoMemory conv-30 导入时间与异常快照</h1>
      <p>生成时间：{esc(report["generated_at"])} · manifest：<strong class="{summary_status_class}">{esc(report["manifest_status"])}</strong> · summary：<strong class="{summary_status_class}">{esc(report["summary_status"])}</strong> · {esc(state_sentence)}</p>
      <div class="chips">
        <div class="chip">account: <code>{esc(report["account"])}</code></div>
        <div class="chip">workspace: <code>{esc(report["workspace"])}</code></div>
        <div class="chip">sample: <code>{esc(report["sample"])}</code></div>
        <div class="chip">mode: <code>fast import + defer artifact wait</code></div>
      </div>
      <div class="grid">
        <div class="stat"><strong>{esc(report["finished_sessions"])}/{esc(report["expected_sessions"])}</strong><span>已进入 commit 记录的 sessions</span></div>
        <div class="stat"><strong>{esc(report["pending_session_label"] or "-")}</strong><span>当前卡住的 session</span></div>
        <div class="stat"><strong>{fmt_num(report["token_summary"]["chat_total_tokens"], 0)}</strong><span>Chat LLM total tokens</span></div>
        <div class="stat"><strong>{fmt_secs(report["combined_latency_s"])}</strong><span>模型总耗时（chat + embedding）</span></div>
      </div>
    </section>

    <section class="section">
      <h2>时间拆分</h2>
      {html_table(["阶段", "耗时", "说明"], timing_rows)}
      <p class="note">这里的“commit time”是导入脚本里 `commit_session_full(...)` 被接受所花的时间，不等于严格 QA-ready。fast mode 下，atom/graph/cursor 会在 commit accepted 之后继续异步推进，所以 summary 即使是 `ASYNC_SETTLING`，manifest 也可能已经 `succeeded`。</p>
    </section>

    <section class="section">
      <h2>Token / LLM 明细</h2>
      <div class="grid">
        <div class="stat"><strong>{fmt_num(report["token_summary"]["chat_input_tokens"], 0)}</strong><span>Chat input tokens</span></div>
        <div class="stat"><strong>{fmt_num(report["token_summary"]["chat_output_tokens"], 0)}</strong><span>Chat output tokens</span></div>
        <div class="stat"><strong>{fmt_num(report["token_summary"]["chat_total_tokens"], 0)}</strong><span>Chat total tokens</span></div>
        <div class="stat"><strong>{fmt_num(report["token_summary"]["zero_ms_embedding_count"], 0)}</strong><span>0.0ms embedding rows</span></div>
      </div>
      {html_table(["call_site", "calls", "input", "output", "total", "latency"], call_site_rows or [["-", "-", "-", "-", "-", "-"]])}
      <p class="note">embedding 调用当前仍记录为 0 token，这是 EchoMemory 现有 observability 的已知限制；但 latency 会被记录下来。</p>
    </section>

    <section class="section">
      <h2>Session 进度</h2>
      {html_table(["session", "messages", "状态信号", "首条写入", "末条写入", "commit accepted", "状态", "warning"], session_rows or [["-", "-", "-", "-", "-", "-", "-", "-"]])}
    </section>

    <section class="section">
      <h2>当前异常点</h2>
      <ul>{anomaly_items}</ul>
    </section>

    <section class="section">
      <h2>证据路径</h2>
      <ul>{evidence_items}</ul>
    </section>
  </main>
</body>
</html>
"""


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest_path = run_dir / "manifest.json"
    summary_path = run_dir / "echomemory_import" / "echomemory_import_summary.json"
    log_path = run_dir / "run.log"
    manifest = read_json(manifest_path)
    summary = read_json(summary_path)
    manifest_status = str(manifest.get("status") or "")
    run_finished = manifest_status in {"succeeded", "failed", "interrupted"}
    started_local = parse_dt(str(manifest.get("started_at") or ""), assume_local=True)
    ended_local = parse_dt(str(manifest.get("ended_at") or ""), assume_local=True)
    report_now = datetime.now(LOCAL_TZ)
    workspace = Path(str(summary.get("workspace") or args.workspace or "")).expanduser().resolve()
    account = str(summary.get("account") or args.account or "default")

    bootstrap_elapsed_s = 0.0
    sidecar_corrupt_count = 0
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "[bootstrap] runtime_ready elapsed_s=" in line:
                try:
                    bootstrap_elapsed_s = float(line.strip().split("elapsed_s=")[-1])
                except Exception:
                    pass
            if "Typed sidecar corrupt" in line:
                sidecar_corrupt_count += 1

    started_utc = started_local.astimezone(UTC) if started_local else None
    token_path = Path(args.token_jsonl).expanduser().resolve()
    ended_utc = ended_local.astimezone(UTC) if ended_local else None
    rows = token_rows(token_path, start_utc=started_utc, end_utc=ended_utc)
    token_summary = aggregate_tokens(rows)

    sessions = collect_sessions(workspace, account, summary, started_local)
    finished_sessions = sum(1 for item in sessions if item.commit_elapsed_s is not None)
    expected_sessions = max(
        len(((summary.get("records") or [{}])[0].get("session_records") or [])),
        len(sessions),
        19,
    )
    commit_elapsed_total_s = sum(float(item.commit_elapsed_s or 0.0) for item in sessions if item.commit_elapsed_s is not None)
    message_write_total_s = sum(
        max(0.0, (item.last_message_at - item.first_message_at).total_seconds())
        for item in sessions
        if item.first_message_at and item.last_message_at
    )
    first_message_at = min((item.first_message_at for item in sessions if item.first_message_at), default=None)
    last_message_at = max((item.last_message_at for item in sessions if item.last_message_at), default=None)
    message_write_span_s = max(0.0, (last_message_at - first_message_at).total_seconds()) if first_message_at and last_message_at else 0.0

    pending = next((item for item in sessions if item.commit_elapsed_s is None), None)
    pending_session_write_s = 0.0
    if pending and pending.first_message_at and pending.last_message_at:
        pending_session_write_s = max(0.0, (pending.last_message_at - pending.first_message_at).total_seconds())

    anomalies: list[str] = []
    if not run_finished:
        anomalies.append("run 仍处于 running，`manifest.json` 还没有 `ended_at`。")
    if pending:
        anomalies.append(
            f"导入脚本当前只对前 {finished_sessions} 个 session 生成了 commit 记录；workspace 里已经出现 `{pending.label}` 的完整 messages.jsonl，但还没有进入 `[commit]` 收口。"
        )
        anomalies.append(
            f"`{pending.label}` 当前 meta 状态为 `commit_index={pending.commit_index}`、`pending_tokens={pending.pending_tokens}`。"
        )
    anomalies.append(
        f"`run.log` 中多次出现 `Typed sidecar corrupt for default/metrics/llm_tokens/2026-06-22.jsonl`，说明 token 侧边车会被删后重建；这也是 summary 聚合 token 经常显示 0 的直接原因。"
    )
    anomalies.append(
        f"embedding 调用当前仍大量记录为 0 token，且累计观测到 {token_summary['zero_ms_embedding_count']} 条 0.0ms embedding 行；因此 embedding token 口径不可信，只能用 latency 口径。"
    )
    if run_finished and summary.get("status") == "ECHOMEMORY_IMPORT_ASYNC_SETTLING":
        anomalies.append("该轮导入已经以 `manifest.status=succeeded` 收口，但 `summary.status=ECHOMEMORY_IMPORT_ASYNC_SETTLING`；这说明平台只等待到 commit accepted，不等待全部异步记忆产物追平。")

    paths = [
        str(manifest_path),
        str(summary_path),
        str(log_path),
        str(token_path),
        str(workspace),
    ]
    if pending:
        session_dir = account_root(workspace, account) / "sessions" / pending.session_id
        paths.extend([
            str(session_dir / "messages.jsonl"),
            str(session_dir / "meta.json"),
        ])

    return {
        "generated_at": fmt_dt(report_now),
        "manifest_status": manifest_status,
        "summary_status": str(summary.get("status") or manifest.get("status") or "UNKNOWN"),
        "run_finished": run_finished,
        "account": account,
        "workspace": str(workspace),
        "sample": str(summary.get("sample") or ""),
        "bootstrap_elapsed_s": bootstrap_elapsed_s,
        "message_write_total_s": message_write_total_s,
        "message_write_span_s": message_write_span_s,
        "commit_elapsed_total_s": commit_elapsed_total_s,
        "pending_session_write_s": pending_session_write_s,
        "chat_llm_latency_s": token_summary["chat_total_latency_ms"] / 1000.0,
        "embedding_latency_s": token_summary["embedding_total_latency_ms"] / 1000.0,
        "combined_latency_s": token_summary["combined_total_latency_ms"] / 1000.0,
        "run_elapsed_s": max(0.0, ((ended_local or report_now) - started_local).total_seconds()) if started_local else 0.0,
        "non_llm_remainder_s": max(0.0, (max(0.0, ((ended_local or report_now) - started_local).total_seconds()) if started_local else 0.0) - (token_summary["combined_total_latency_ms"] / 1000.0)),
        "token_summary": token_summary,
        "sessions": [
            {
                "session_id": item.session_id,
                "label": item.label,
                "expected_messages": item.expected_messages,
                "actual_messages": item.actual_messages,
                "first_message_at": item.first_message_at,
                "last_message_at": item.last_message_at,
                "updated_at": item.updated_at,
                "commit_index": item.commit_index,
                "pending_tokens": item.pending_tokens,
                "total_tokens": item.total_tokens,
                "overview_exists": item.overview_exists,
                "abstract_exists": item.abstract_exists,
                "commit_elapsed_s": item.commit_elapsed_s,
                "commit_status": item.commit_status,
                "retrieval_ready": item.retrieval_ready,
                "cursor_complete": item.cursor_complete,
                "qa_ready": item.qa_ready,
                "integrity": item.integrity,
                "warning": item.warning,
            }
            for item in sessions
        ],
        "finished_sessions": finished_sessions,
        "expected_sessions": expected_sessions,
        "pending_session_label": pending.label if pending else "",
        "anomalies": anomalies,
        "paths": paths,
        "sidecar_corrupt_count": sidecar_corrupt_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a live EchoMemory conv-30 import timing report.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--token-jsonl", required=True)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--account", default="")
    parser.add_argument("--out-html", required=True)
    parser.add_argument("--mirror-html", default="")
    args = parser.parse_args()

    report = build_report(args)
    out_html = Path(args.out_html).expanduser().resolve()
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(report), encoding="utf-8")
    if args.mirror_html:
        mirror = Path(args.mirror_html).expanduser().resolve()
        mirror.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out_html, mirror)
    print(out_html)


if __name__ == "__main__":
    main()
