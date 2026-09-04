#!/usr/bin/env python3
"""Regenerate report.html from an existing run's summary.json + metrics_samples.csv.

Usage: python performance/regen_report.py <out_dir>

summary.json 中已有的 process_findings（压测过程确认的问题）会随报告一起渲染；
findings 的注入由数据准备方在 summary.json 中维护，本脚本只负责重建报告。
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from performance.monitor import (
    COMMIT_QUEUE_DEPTH,
    CPU_SECONDS,
    HTTP_INFLIGHT,
    PROCESS_THREADS,
    RESIDENT_MEMORY,
    MetricsFrame,
    MetricsMonitor,
)
from performance.report import build_html, save_html

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("performance/results/20260828_183423_470050")

summary = json.load(open(OUT / "summary.json", encoding="utf-8"))

# --- rebuild monitor frames (only the 5 chart metrics) from metrics_samples.csv ---
NEEDED = {
    RESIDENT_MEMORY,
    PROCESS_THREADS,
    COMMIT_QUEUE_DEPTH,
    HTTP_INFLIGHT,
    CPU_SECONDS,
}
frames_by_ts: dict[float, MetricsFrame] = {}
n_rows = 0
with open(OUT / "metrics_samples.csv", encoding="utf-8") as handle:
    reader = csv.reader(handle)
    header = next(reader)  # ts, metric, labels, value
    for row in reader:
        try:
            ts_s, metric, labels_s, value_s = row
            ts = float(ts_s)
        except (ValueError, IndexError):
            continue
        if metric not in NEEDED:
            continue
        n_rows += 1
        frame = frames_by_ts.get(ts)
        if frame is None:
            frame = MetricsFrame(ts=ts)
            frames_by_ts[ts] = frame
        try:
            labels = json.loads(labels_s) if labels_s else {}
        except json.JSONDecodeError:
            labels = {}
        frame.samples.setdefault(metric, []).append((labels, float(value_s)))

monitor = MetricsMonitor("http://127.0.0.1:8010")
monitor.frames = [frames_by_ts[ts] for ts in sorted(frames_by_ts)]
print(f"rebuilt frames={len(monitor.frames)} metric_rows={n_rows}")

if not monitor.frames:
    print("ERROR: no frames rebuilt; report.html not generated")
    sys.exit(1)

t_first = monitor.frames[0].ts
t_last = monitor.frames[-1].ts
chart_series = {
    "rss_mb": [
        (ts, round(value / 1024 / 1024, 2))
        for ts, value in monitor.gauge_series(RESIDENT_MEMORY, t_first, t_last)
    ],
    "threads": monitor.gauge_series(PROCESS_THREADS, t_first, t_last),
    "commit_queue": monitor.gauge_series(COMMIT_QUEUE_DEPTH, t_first, t_last),
    "inflight": monitor.gauge_series(HTTP_INFLIGHT, t_first, t_last),
    "cpu_percent": monitor.cpu_utilization_series(t_first, t_last),
}
print(
    "chart series lens:",
    {k: len(v) for k, v in chart_series.items()},
)
thread_peak = max((v for _, v in chart_series["threads"]), default=None)
inflight_peak = max((v for _, v in chart_series["inflight"]), default=None)
print(f"monitor-sampled threads_peak={thread_peak} inflight_peak={inflight_peak}")

html_text = build_html(summary, chart_series)
save_html(OUT, html_text)
print(f"report.html saved: {OUT / 'report.html'} ({len(html_text)} bytes)")
