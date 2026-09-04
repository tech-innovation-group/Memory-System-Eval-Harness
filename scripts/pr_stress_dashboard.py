#!/usr/bin/env python3
"""Build a small live dashboard for queued EchoMem PR stress jobs.

This script intentionally uses only the Python standard library so it can run
on the evaluation host without installing another reporting dependency.
"""

from __future__ import print_function

import html
import json
import os
import time
from datetime import datetime, timezone


def read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError):
        return fallback


def fmt(value):
    return html.escape(str(value or "-"))


def parse_timestamp(value):
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def progress_age_seconds(job, now=None):
    progress = job.get("progress") or {}
    parsed = parse_timestamp(progress.get("updated_at") or job.get("started_at"))
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - parsed).total_seconds())


def status_label(job, age_s):
    status = str(job.get("status") or "unknown")
    if status == "running" and age_s is not None and age_s >= 900:
        return "运行中 · 疑似停滞"
    return status


def summary_for(job_id, results_root):
    for name in ("formal_%s/summary.json" % job_id, "stress_%s/summary.json" % job_id):
        payload = read_json(os.path.join(results_root, name), {})
        if payload:
            return payload
    return {}


def result_text(summary):
    aggregates = summary.get("aggregates") or []
    if not aggregates:
        return "-"
    statuses = []
    for item in aggregates:
        statuses.append(
            "%s: C %s/%s, S %s/%s"
            % (
                item.get("scenario", "-"),
                item.get("commit_completed", 0),
                item.get("commit_submitted", 0),
                item.get("search_succeeded", 0),
                item.get("search_submitted", 0),
            )
        )
    return "<br>".join(html.escape(value) for value in statuses[:5])


def render(jobs, results_root, public_base_url):
    rows = []
    current_time = datetime.now(timezone.utc)
    for job in jobs:
        if job.get("test_type") != "stress":
            continue
        progress = job.get("progress") or {}
        age_s = progress_age_seconds(job, current_time)
        stalled = (
            job.get("status") == "running"
            and age_s is not None
            and age_s >= 900
        )
        state = status_label(job, age_s)
        progress_detail = "%s%%<br><small>%s / %s" % (
            fmt(progress.get("percent", 0)),
            fmt(progress.get("phase")),
            fmt(progress.get("label")),
        )
        if age_s is not None:
            progress_detail += "<br><small class='%s'>最后进度 %.1f 分钟前</small>" % (
                "warn" if stalled else "muted",
                age_s / 60.0,
            )
        progress_detail += "</small>"
        summary = summary_for(str(job.get("id") or ""), results_root)
        job_id = str(job.get("id") or "")
        detail = "%s/jobs/%s" % (public_base_url.rstrip("/"), job_id)
        rows.append(
            "<tr>"
            "<td><a href='%s'>%s</a></td>"
            "<td><b>PR %s</b><br><small>%s</small></td>"
            "<td><code>%s</code></td>"
            "<td><span class='status status-%s'>%s</span><br><small>%s</small></td>"
            "<td>%s</td>"
            "<td><small>创建 %s<br>开始 %s</small></td>"
            "<td>%s</td>"
            "</tr>"
            % (
                html.escape(detail, quote=True),
                html.escape(job_id),
                fmt(job.get("pr_number")),
                fmt(job.get("source_label")),
                fmt(job.get("pr_head") or job.get("commit_sha") or "-"),
                html.escape(str(job.get("status") or "unknown")),
                fmt(state),
                fmt(job.get("message")),
                progress_detail,
                fmt(job.get("created_at")),
                fmt(job.get("started_at")),
                result_text(summary),
            )
        )
    if not rows:
        rows.append("<tr><td colspan='7'>暂无压测任务</td></tr>")
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    document = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem PR 压测总览</title>
<style>
body{margin:0;background:#f3f6f7;color:#17212b;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
main{max-width:1600px;margin:auto;padding:28px 20px 60px}
header{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:18px}
h1{margin:0;font-size:25px}.muted,small{color:#6d7b87}
.panel{background:#fff;border:1px solid #dce4e8;padding:18px;margin-top:12px}
.scroll{overflow:auto}table{width:100%%;border-collapse:collapse;white-space:nowrap}
th,td{padding:10px 9px;border-bottom:1px solid #e1e7ea;text-align:left;vertical-align:top}
th{background:#fafbfc;color:#6d7b87}a{color:#286aa6}
.status{display:inline-block;padding:2px 7px;border-radius:4px;font-size:12px}
.status-running{background:#e5f6ef;color:#176d58}.status-queued{background:#fff5d6;color:#8a6413}
.status-completed{background:#dff3e8;color:#166b4f}.status-failed{background:#fde8e6;color:#a23a32}
.status-interrupted{background:#eee;color:#555}.warn{color:#a23a32;font-weight:700}
.muted{color:#6d7b87}code{font-size:12px}
</style></head><body><main>
<header><div><h1>EchoMem PR 压测总览</h1>
<div class="muted">真实模型 · 服务端观察模式 · 单并发队列</div></div>
<div class="muted">更新时间：%s</div></header>
<section class="panel"><div class="scroll"><table>
<thead><tr><th>任务</th><th>目标</th><th>PR head / commit</th><th>状态</th>
<th>进度</th><th>时间</th><th>结果摘要</th></tr></thead><tbody>%s</tbody>
</table></div></section>
<section class="panel"><small>正式套件默认不使用测试平台客户端调度策略；
每个场景按 server-observe 运行，详情页保留原始日志、CSV、服务端遥测和报告。</small></section>
</main></body></html>""" % (now, "".join(rows))
    return document


def main():
    jobs_path = os.environ.get("JOBS_PATH", "/opt/memory-eval-web/data/jobs.json")
    results_root = os.environ.get(
        "RESULTS_ROOT", "/opt/memory-eval-harness-latest/results"
    )
    output_path = os.environ.get(
        "OUTPUT_PATH", os.path.join(results_root, "pr-stress-dashboard.html")
    )
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8081")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    once = os.environ.get("ONCE", "").lower() in {"1", "true", "yes"}
    while True:
        jobs = read_json(jobs_path, [])
        document = render(jobs if isinstance(jobs, list) else [], results_root, public_base_url)
        temporary = output_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(document)
        os.replace(temporary, output_path)
        if once:
            return
        time.sleep(float(os.environ.get("INTERVAL_S", "60")))


if __name__ == "__main__":
    main()
