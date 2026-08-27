#!/usr/bin/env python3
"""Render an auditable HTML summary for the EchoMem PR stress queue."""

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def text(value):
    return html.escape(str(value if value not in (None, "") else "-"))


def parse_timestamp(value):
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def load_summary(results_root, job_id):
    candidates = (
        results_root / job_id / f"formal_{job_id}" / "summary.json",
        results_root / job_id / f"stress_{job_id}" / "summary.json",
    )
    for path in candidates:
        payload = read_json(path, {})
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def aggregate_counts(summary):
    commits_submitted = commits_completed = searches_submitted = searches_succeeded = 0
    for item in summary.get("aggregates") or []:
        commits_submitted += int(item.get("commit_submitted") or 0)
        commits_completed += int(item.get("commit_completed") or 0)
        searches_submitted += int(item.get("search_submitted") or 0)
        searches_succeeded += int(item.get("search_succeeded") or 0)
    return (
        commits_submitted,
        commits_completed,
        searches_submitted,
        searches_succeeded,
    )


def evidence_label(summary):
    reasons = []
    gate_result = summary.get("release_gate_evaluation") or {}
    for failure in gate_result.get("failures") or []:
        if isinstance(failure, dict):
            reason = failure.get("reason") or failure.get("gate")
            if reason:
                reasons.append(str(reason))
    runs = summary.get("runs") or []
    if not runs and summary:
        runs = [summary]
    for run in runs:
        details = run.get("details") or {}
        if details.get("server_observation_complete") is False:
            reasons.append("缺少逐请求服务端时间戳")
        isolation = details.get("isolation") or {}
        if isolation.get("status") == "INCONCLUSIVE":
            reasons.append(str(isolation.get("reason") or "隔离证据不足"))
        if run.get("status") in {"FAIL", "ENVIRONMENT_ERROR"}:
            reasons.append(str(details.get("reason") or "运行失败"))
    if reasons:
        return "INCONCLUSIVE", sorted(set(reasons))
    return "可判定", []


def monitor_sample_count(path):
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def find_monitor_path(results_root):
    for candidate in (
        results_root / "pr-stress-monitor-v2.jsonl",
        results_root / "pr-stress-monitor.jsonl",
    ):
        if candidate.is_file():
            return candidate
    return results_root / "pr-stress-monitor-v2.jsonl"


def is_formal_server_observe_job(job):
    """Keep legacy client-policy runs out of the formal campaign totals."""
    if job.get("test_type") != "stress" or job.get("source_ref") != "pr":
        return False
    config = job.get("stress_config") or {}
    return (
        config.get("formal_suite") is True
        and config.get("scheduler_policy") == "server-observe"
        and config.get("client_admission") == "disabled"
        and int(config.get("commit_workers") or 0) >= 64
        and int(config.get("search_workers") or 0) >= 64
    )


def render(jobs, results_root, public_base_url):
    stress_jobs = [job for job in jobs if is_formal_server_observe_job(job)]
    completed = sum(job.get("status") == "completed" for job in stress_jobs)
    running = sum(job.get("status") == "running" for job in stress_jobs)
    queued = sum(job.get("status") == "queued" for job in stress_jobs)
    failed = sum(job.get("status") in {"failed", "interrupted"} for job in stress_jobs)
    rows = []
    findings = []
    stalled_jobs = []

    for job in stress_jobs:
        job_id = str(job.get("id") or "")
        summary = load_summary(results_root, job_id)
        submitted, committed, search_submitted, search_succeeded = aggregate_counts(summary)
        label, reasons = evidence_label(summary)
        if reasons:
            findings.extend(f"PR {job.get('pr_number')}: {reason}" for reason in reasons)
        progress = job.get("progress") or {}
        progress_updated_at = progress.get("updated_at") or job.get("started_at")
        progress_age_s = None
        if progress_updated_at:
            parsed = parse_timestamp(progress_updated_at)
            if parsed is not None:
                progress_age_s = max(
                    0.0,
                    (datetime.now(timezone.utc) - parsed).total_seconds(),
                )
        stalled = (
            job.get("status") == "running"
            and progress_age_s is not None
            and progress_age_s >= 900
        )
        if stalled:
            stalled_jobs.append(
                f"PR {job.get('pr_number')}: 已超过 {progress_age_s / 60:.1f} 分钟没有新的阶段进度"
            )
        detail = f"{public_base_url.rstrip('/')}/jobs/{job_id}"
        progress_text = (
            f"{text(progress.get('percent', 0))}%"
            + (
                f"<br><small class='warn'>疑似停滞 {progress_age_s / 60:.1f} 分钟</small>"
                if stalled
                else ""
            )
        )
        rows.append(
            "<tr>"
            f"<td><a href='{html.escape(detail, quote=True)}'>{text(job_id)}</a></td>"
            f"<td><b>PR {text(job.get('pr_number'))}</b><br><small>{text(job.get('source_label'))}</small></td>"
            f"<td><span class='status status-{text(job.get('status'))}'>{text(job.get('status'))}</span>"
            f"<br><small>{text(progress.get('phase') or progress.get('label'))}</small></td>"
            f"<td>{text(job.get('develop_commit_sha') or job.get('merge_base_sha'))}<br>"
            f"<small>merge {text(job.get('merge_commit_sha') or job.get('commit_sha'))}</small></td>"
            f"<td>{progress_text}<br><small>{text(progress.get('last_log'))}</small></td>"
            f"<td>C {committed}/{submitted}<br>S {search_succeeded}/{search_submitted}</td>"
            f"<td><span class='evidence evidence-{label}'>{text(label)}</span><br>"
            f"<small>{text('; '.join(reasons))}</small></td>"
            "</tr>"
        )

    if not rows:
        rows.append("<tr><td colspan='7'>暂无正式压测任务</td></tr>")
    if not findings:
        findings.append("目前没有从已生成结果中提取到新的异常；仍需以完整结果和服务端遥测为准。")
    else:
        findings = sorted(set(findings))
    findings.extend(stalled_jobs)

    now = datetime.now(timezone.utc).isoformat()
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem PR 长时间压测最终汇总</title>
<style>
body{{margin:0;background:#f3f6f7;color:#17212b;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{max-width:1680px;margin:auto;padding:28px 22px 64px}}h1{{margin:0;font-size:26px}}
.muted,small{{color:#6d7b87}}header{{display:flex;justify-content:space-between;gap:18px;align-items:end}}
.grid{{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:10px;margin:20px 0}}
.card,.panel{{background:#fff;border:1px solid #dce4e8;padding:16px;margin-top:12px}}
.card strong{{display:block;font-size:26px;margin-top:4px}}.scroll{{overflow:auto}}
table{{width:100%;border-collapse:collapse;min-width:1100px}}th,td{{padding:10px 9px;border-bottom:1px solid #e1e7ea;text-align:left;vertical-align:top}}
th{{background:#fafbfc;color:#6d7b87;position:sticky;top:0}}a{{color:#286aa6}}code{{font-size:12px}}
.status,.evidence{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:12px}}
.status-running{{background:#e5f6ef;color:#176d58}}.status-queued{{background:#fff5d6;color:#8a6413}}
.status-completed{{background:#dff3e8;color:#166b4f}}.status-failed,.status-interrupted{{background:#fde8e6;color:#a23a32}}
.warn{{color:#a23a32;font-weight:700}}
.evidence-INCONCLUSIVE{{background:#fff5d6;color:#8a6413}}.evidence-可判定{{background:#dff3e8;color:#166b4f}}
li{{margin:7px 0}}.note{{border-left:3px solid #286aa6;padding-left:12px}}
@media(max-width:800px){{.grid{{grid-template-columns:repeat(2,1fr)}}header{{display:block}}}}
</style></head><body><main>
<header><div><h1>EchoMem PR 长时间压测最终汇总</h1>
<div class="muted">真实模型 · 真实 HTTP · server-observe · 测试平台单并发</div></div>
<div class="muted">生成时间：{text(now)}</div></header>
<div class="grid">
<div class="card"><small>正式任务</small><strong>{len(stress_jobs)}</strong></div>
<div class="card"><small>已完成</small><strong>{completed}</strong></div>
<div class="card"><small>运行中</small><strong>{running}</strong></div>
<div class="card"><small>排队中</small><strong>{queued}</strong></div>
<div class="card"><small>失败/中断</small><strong>{failed}</strong></div>
</div>
<section class="panel"><p class="note">本页只统计本轮正式 PR 服务端观测任务：不使用 FIFO、Search 优先、双通道或租户公平等客户端策略。
请求由多个真实租户直接发往 EchoMem；调度、公平性和队列结论必须有服务端时间戳或服务端日志支持。
没有足够证据的项目标记为 <b>INCONCLUSIVE</b>。</p></section>
<section class="panel"><h2>PR 结果</h2><div class="scroll"><table>
<thead><tr><th>任务</th><th>目标</th><th>状态</th><th>版本基线</th><th>进度</th>
<th>工作负载</th><th>证据结论</th></tr></thead><tbody>{''.join(rows)}</tbody>
</table></div></section>
<section class="panel"><h2>当前发现</h2><ul>{''.join(f'<li>{text(item)}</li>' for item in findings)}</ul></section>
<section class="panel"><h2>审计说明</h2>
<p>每个 PR 的详情页保留原始日志、请求 CSV、资源采样、服务端 metrics 和独立 HTML。
服务器长时间监控样本数：{monitor_sample_count(find_monitor_path(results_root))}。
监控会把运行任务超过 15 分钟没有阶段进度更新标记为“疑似停滞”，但不会仅凭这一项自动判定失败。
如果某个 PR 仍在运行，当前页只展示已生成的结果，不代表该 PR 已完成。</p></section>
</main></body></html>"""


def main():
    jobs_path = Path(os.getenv("JOBS_PATH", "/opt/memory-eval-web/data/jobs.json"))
    results_root = Path(os.getenv("RESULTS_ROOT", "/opt/memory-eval-harness/results"))
    output_path = Path(
        os.getenv("OUTPUT_PATH", str(results_root / "pr-stress-final-report.html"))
    )
    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8081")
    payload = read_json(jobs_path, [])
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    document = render(jobs if isinstance(jobs, list) else [], results_root, public_base_url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(output_path)
    print(output_path)


if __name__ == "__main__":
    main()
