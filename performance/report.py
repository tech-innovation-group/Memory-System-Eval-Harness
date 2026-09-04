"""Artifacts: config.json, requests.csv, metrics_samples.csv, summary.json, report.html.

The HTML report is fully self-contained (inline CSS + hand-rolled SVG
charts, no external scripts) so it can be shared as a single file.
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from performance.loadgen import RequestRecord
from performance.metrics_calc import FEATURE_LABELS
from performance.monitor import MetricsMonitor

CSV_HEADERS = [
    "scene",
    "step_conc",
    "tenant_idx",
    "op",
    "stage_ms",
    "status",
    "error_type",
    "http_status",
    "ts_ms",
    "start_ts_ms",
    "request_id",
    "session_id",
    "archive_id",
    "extra",
    "retry_count",
    "retried",
    "retry_total_wait_ms",
    "final_success",
    "retry_after_s",
    "reason_code",
    "message_id",
    "content_hash",
    "content_bytes",
    "hit_count",
    "real_recall",
    "quality_ok",
    "degraded",
    "query_kind",
    "query",
    "expected_terms",
    "recall_matched",
]


def write_config(out_dir: Path, payload: dict[str, Any]) -> Path:
    path = out_dir / "config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_requests_csv(out_dir: Path, records: list[RequestRecord]) -> Path:
    path = out_dir / "requests.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_csv_row())
    return path


def write_metrics_csv(out_dir: Path, monitor: MetricsMonitor) -> Path:
    """Flatten every sampled frame into a long-format CSV."""
    path = out_dir / "metrics_samples.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "metric", "labels", "value"])
        for frame in monitor.frames:
            for name, samples in frame.samples.items():
                for labels, value in samples:
                    writer.writerow(
                        [
                            round(frame.ts, 3),
                            name,
                            json.dumps(labels, ensure_ascii=False, sort_keys=True),
                            value,
                        ]
                    )
    return path


def write_summary(out_dir: Path, payload: dict[str, Any]) -> Path:
    path = out_dir / "summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------- #
#  Minimal SVG chart helpers (self-contained, no external deps)           #
# ---------------------------------------------------------------------- #

_SVG_W = 640
_SVG_H = 220


def _svg_axes(
    max_value: float,
    *,
    pad_l: int = 44,
    pad_b: int = 26,
    pad_t: int = 18,
    pad_r: int = 8,
) -> tuple[int, int, int, int, float]:
    plot_w = _SVG_W - pad_l - pad_r
    plot_h = _SVG_H - pad_t - pad_b
    scale = plot_h / max_value if max_value > 0 else 1.0
    return pad_l, pad_t, plot_w, plot_h, scale


def _estimate_text_width(text: str, font_size: float = 10.0) -> float:
    """粗估文本像素宽度：CJK 按 1em，ASCII 按 0.55em（用于图例自适应排布）。"""
    return sum(font_size * (1.0 if ord(ch) > 0x2E80 else 0.55) for ch in text)


def _legend_html(series: list[tuple[str, str]], unit: str = "") -> str:
    """图例：色块 + 文字按估算宽度逐项排布，整体右对齐排在单位左侧。

    避免固定槽位下长标签（如 commit_submit）顶到下一项的色块，以及长标题
    压到图例区：图例贴右缘、标题靠左，两者互不侵占。
    """
    gap = 12.0
    swatch_w = 10.0
    text_gap = 4.0
    widths = [_estimate_text_width(label) for label, _ in series]
    total = sum(swatch_w + text_gap + w for w in widths) + gap * max(0, len(series) - 1)
    unit_w = _estimate_text_width(unit) if unit else 0.0
    x = _SVG_W - 8 - (unit_w + 12 if unit else 0.0) - total
    parts: list[str] = []
    for (label, color), w in zip(series, widths):
        parts.append(
            f'<rect x="{x:.1f}" y="2" width="{swatch_w:.0f}" height="10" fill="{color}"/>'
            f'<text x="{x + swatch_w + text_gap:.1f}" y="11" font-size="10">{html.escape(label)}</text>'
        )
        x += swatch_w + text_gap + w + gap
    if unit:
        parts.append(
            f'<text x="{_SVG_W - 8}" y="12" font-size="10" text-anchor="end">{html.escape(unit)}</text>'
        )
    return "".join(parts)


def _bar_chart(labels: list[str], series: list[dict[str, Any]], title: str, unit: str = "") -> str:
    """Grouped bars: series is a list of {label, values[], color}."""
    if not labels or not series:
        return f"<p>{html.escape(title)}：无数据</p>"
    all_values = [v for s in series for v in s["values"]]
    max_value = max(all_values) * 1.1 if all_values else 1.0
    # x 轴场景名旋转 -45° 展示，SVG 加高并预留底部空间，避免长标签重叠或被裁切
    pad_l, pad_t, plot_w, plot_h, scale = _svg_axes(max_value, pad_b=58)
    svg_h = _SVG_H + 52
    group_w = plot_w / len(labels)
    bar_w = min(26.0, group_w / (len(series) + 0.8))

    parts: list[str] = [
        f'<svg width="{_SVG_W}" height="{svg_h}" viewBox="0 0 {_SVG_W} {svg_h}">',
        f'<text x="{pad_l}" y="14" font-size="12">{html.escape(title)}</text>',
    ]
    # grid lines
    for i in range(5):
        y = pad_t + plot_h * (1 - i / 4)
        parts.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{_SVG_W - 8}" y2="{y:.1f}" '
            f'stroke="#ddd" stroke-width="1"/>'
        )
        parts.append(f'<text x="4" y="{y + 3:.1f}" font-size="9" fill="#888">{max_value * i / 4:.4g}</text>')
    for gi, label in enumerate(labels):
        cx = pad_l + group_w * gi + group_w / 2
        for si, series_entry in enumerate(series):
            values = series_entry["values"]
            value = (values[gi] if gi < len(values) else 0.0) or 0.0
            h_px = value * scale
            x = cx - (len(series) * bar_w) / 2 + si * bar_w
            parts.append(
                f'<rect x="{x:.1f}" y="{pad_t + plot_h - h_px:.1f}" width="{bar_w:.1f}" '
                f'height="{h_px:.1f}" fill="{series_entry["color"]}" rx="1"/>'
            )
        label_y = svg_h - 18
        parts.append(
            f'<text x="{cx:.1f}" y="{label_y}" font-size="9" text-anchor="end" '
            f'transform="rotate(-45 {cx:.1f} {label_y})">{html.escape(str(label))}</text>'
        )
    # 图例按估算宽度自适应排布、右对齐在单位左侧，避免色块/文字/标题重叠
    parts.append(f"<g>{_legend_html([(s['label'], s['color']) for s in series], unit)}</g></svg>")
    return "".join(parts)


def _line_chart(
    series: list[tuple[str, list[tuple[float, float]], str]],
    title: str,
) -> str:
    """Multiple x-time series as a single line chart (x in seconds)."""
    if not series:
        return f"<p>{html.escape(title)}：无数据</p>"
    points = [p for _, s, _ in series for p in s]
    if not points:
        return f"<p>{html.escape(title)}：无数据</p>"
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    y_max = max(ys) * 1.1 if ys else 1.0
    y_min = min(ys) * 0.9 if ys else 0.0
    span_y = y_max - y_min
    if span_y <= 0:
        span_y = 1.0
    pad_l, pad_t, plot_w, plot_h, _ = _svg_axes(max(1.0, span_y))
    x0, x1 = min(xs), max(xs)
    span = (x1 - x0) or 1.0

    def to_xy(x: float, y: float) -> tuple[float, float]:
        px = pad_l + plot_w * (x - x0) / span
        py = pad_t + plot_h * (1 - (y - y_min) / span_y)
        return px, py

    parts: list[str] = [
        f'<svg width="{_SVG_W}" height="{_SVG_H}" viewBox="0 0 {_SVG_W} {_SVG_H}">',
        f'<text x="{pad_l}" y="14" font-size="12">{html.escape(title)}</text>',
    ]
    for i in range(5):
        y = y_min + (y_max - y_min) * i / 4
        _, py = to_xy(x0, y)
        parts.append(f'<line x1="{pad_l}" y1="{py:.1f}" x2="{_SVG_W - 8}" y2="{py:.1f}" stroke="#ddd"/>')
        parts.append(f'<text x="4" y="{py + 3:.1f}" font-size="9" fill="#888">{y:.4g}</text>')
    for name, samples, color in series:
        if not samples:
            continue
        path = " ".join(
            f'{"L" if i else "M"}{to_xy(x, y)[0]:.1f},{to_xy(x, y)[1]:.1f}'
            for i, (x, y) in enumerate(samples)
        )
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5"/>')
    # 图例按估算宽度自适应排布、右对齐，避免色块/文字重叠
    parts.append(f"<g>{_legend_html([(name, color) for name, _, color in series])}</g></svg>")
    return "".join(parts)


def _stat_table(rows: list[tuple[str, ...]], headers: list[str]) -> str:
    thead = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f'<table border="1" cellspacing="0" cellpadding="4"><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>'


def _fmt(value: Any) -> str:
    """Render a possibly-None number compactly (percentiles, rates, ratios)."""
    if value is None:
        return ""
    if isinstance(value, float):
        if value == 0:
            return "0"
        if abs(value) >= 1000 or abs(value) < 0.001:
            return f"{value:.3g}"
        return f"{value:.3f}"
    return str(value)


def _note(text: str) -> str:
    return f"<p class='note'>{text}</p>"


def _methodology(summary: dict[str, Any]) -> str:
    """测试方法：压测对象、负载模型、并发模型、种子数据、观测与判定方式。

    全部取值来自 ``summary.config`` / ``data_scale`` / ``server``，保证方法描述
    与实际运行参数一一对应（支撑事实）。
    """
    config = summary.get("config") or {}
    server = summary.get("server") or {}
    c = lambda key, default="": config.get(key, default)
    steps = ", ".join(str(x) for x in (config.get("concurrency_steps") or [])) or "?"

    params: list[tuple[str, str]] = [
        ("压测对象", str(server.get("base_url", c("echomem_url", "")))),
        ("请求超时 (s)", str(c("timeout_s", ""))),
        ("检索 top_k", str(c("top_k", ""))),
        ("租户数", str(c("tenants", ""))),
        ("并发档（每租户线程）", steps),
        ("场景集", ", ".join(str(x) for x in (config.get("scenario_ids") or []))),
        ("读写混合比例 (C)", ", ".join(str(x) for x in (config.get("mix_ratios") or []))),
        ("每场景每档时长 (s)", str(c("duration_s", ""))),
        ("注入洪峰 (D)", f"{c('burst_commits', '')} 个提交 / {c('burst_window_s', '')}s 窗口"),
        ("种子数据", f"{c('seed_source', '')}：每租户 {c('seed_sessions_per_tenant', '')} 会话 × {c('messages_per_session', '')} 消息"),
        ("metrics 采样间隔 (s)", str(c("metrics_interval_s", ""))),
        ("劣化判定阈值", f"{c('degradation_threshold', '')}x（读 P95 劣化 ≥ 阈值判 FAIL）"),
        ("commit 轮询超时 (s)", str(c("commit_poll_timeout_s", ""))),
        ("身份模式", f"{c('auth_mode', '')}（cleanup_identities={c('cleanup_identities', '')}）"),
        ("运行模式", str(c("mode", ""))),
    ]

    paragraphs = [
        "<p>本压测对<strong>已运行中的 EchoMem 记忆服务</strong>做只读观测（不改服务端代码），"
        "以多租户高并发读写注入度量吞吐、延迟、读写干扰、资源占用与四项特性保证。</p>",
        "<p><strong>负载模型</strong>：读 = 检索（search）；写 = 完整写事务四段 "
        "<code>open → add → commit_submit(202) → commit_done(poll)</code>，逐段独立计时，"
        "段失败即中断该事务；客户端不重试（重试会掩盖错误率并扭曲延迟分布）。</p>",
        f"<p><strong>并发模型</strong>：{c('tenants', '?')} 个隔离租户 × 每租户并发档 {steps}；"
        f"场景矩阵按「场景主序、并发次序列」展开，每场景每档运行 {c('duration_s', '?')}s。</p>",
        f"<p><strong>种子数据</strong>：{c('seed_source', '')} 源，每租户注入 "
        f"{c('seed_sessions_per_tenant', '?')} 会话 × {c('messages_per_session', '?')} 条带锚词消息"
        "（种子注入不计入压测计时，但保证检索索引有真实内容）。</p>",
        f"<p><strong>观测方式</strong>：客户端逐请求计时（P50/P95/P99 线性插值）；"
        f"同时每 {c('metrics_interval_s', '?')}s 抓取服务端 Prometheus /metrics"
        "（进程 CPU/RSS/线程、recall/http/commit 直方图、commit 队列、inflight）形成双视角证据。</p>",
        "<p><strong>判定方法</strong>：四项特性独立判 PASS / FAIL / INCONCLUSIVE——"
        f"①commit 成功保证（202 接受后必须 completed）+ 检索优先级（注入洪峰读 P95 劣化 &lt; {c('degradation_threshold', '2')}x）；"
        "②租户公平性（租户间读 P95 max/min 比 &lt; 3x）；"
        "③无内存泄漏（RSS 上升斜率 &lt; 5 MB/min）；"
        "④资源利用率时间线。任一 FAIL 则总体 FAIL。</p>",
        f"<p>本次运行状态 <strong>{html.escape(str(summary.get('status', '')))}</strong>，"
        f"起止 {html.escape(str(summary.get('started_at', '')))} → "
        f"{html.escape(str(summary.get('finished_at', '')))}。</p>",
    ]
    return (
        "<h2>测试方法</h2>"
        + "".join(paragraphs)
        + "<h3>压测参数（支撑事实）</h3>"
        + _stat_table(params, ["参数", "值"])
    )


_SCENARIO_DEFS: list[tuple[str, str, str]] = [
    ("A", "纯读基线", "全部工作线程执行检索（search），无写入注入，作为 C / D 场景劣化对照的基线。"),
    ("B", "纯写注入", "全部线程执行完整写事务（open → add → commit_submit → commit_done），"
     "度量四段延迟、提交回拒与写后读一致性。"),
    ("C", "读写混合", "按 read:write 比例分配读 / 写线程，度量读写相互干扰下双方延迟的变化。"),
    ("D", "注入洪峰", "读持续运行，同时在短窗口内注入 N 个并行写事务，检出「写注入是否阻塞检索」。"),
]


def _scenarios_section(summary: dict[str, Any]) -> str:
    """测试场景：场景定义 + 本次实际执行的矩阵 + 每场景服务端资源快照。

    矩阵顺序与 summary.scenes 的写入顺序（实际运行顺序）一致；每场景服务端
    快照来自 /metrics 场景窗口采样，是「每组场景」的支撑事实之一。
    """
    scenes = summary.get("scenes") or {}
    matrix = " → ".join(str(key) for key in scenes)
    defs = _stat_table(
        [(sid, name, desc) for sid, name, desc in _SCENARIO_DEFS],
        ["场景", "名称", "负载与用途"],
    )
    rows: list[tuple[str, ...]] = []
    for key, scene in scenes.items():
        res = scene.get("resource") or {}
        recall = res.get("recall_duration") or {}
        http = res.get("http_duration") or {}
        commit = res.get("commit_duration") or {}
        rows.append(
            (
                str(key),
                str(int(res.get("threads_max") or 0)),
                str(int(res.get("python_threads_max") or 0)),
                str(int(res.get("http_inflight_max") or 0)),
                str(int(res.get("commit_queue_depth_max") or 0)),
                _fmt(recall.get("p50")),
                _fmt(recall.get("p95")),
                _fmt(http.get("p50")),
                _fmt(commit.get("p50")),
            )
        )
    return (
        "<h2>测试场景</h2>"
        f"<p class='note'>本次共执行 {len(scenes)} 组场景，执行顺序：{matrix}。</p>"
        + defs
        + "<h3>每场景服务端资源快照（支撑事实）</h3>"
        + _note("服务端视角：场景窗口内线程 / inflight / commit 队列峰值，及 recall / http / commit "
                "服务端直方图分位数（秒）。客户端视角的每场景读 / 写延迟与吞吐见「压测结果」。")
        + _stat_table(
            rows,
            ["场景", "线程峰值", "Python线程", "inflight峰值", "commit队列峰值", "recall P50(s)", "recall P95(s)", "HTTP P50(s)", "commit P50(s)"],
        )
    )


def _metric_glossary(summary: dict[str, Any]) -> str:
    """指标字典：每个指标的含义 / 算法 + 本次实测值（支撑事实）。"""
    scenes = summary.get("scenes") or {}
    degradation = summary.get("degradation") or {}
    consistency = summary.get("consistency") or {}
    resources = summary.get("resources") or {}
    durability = summary.get("commit_durability") or {}
    fairness = summary.get("tenant_fairness") or {}
    commit_lat = summary.get("commit_latency") or {}

    all_read = [
        ((scenes[key].get("ops") or {}).get(key) or {}).get("read") or {}
        for key in scenes
    ]
    read_qps = [r["qps"] for r in all_read if r.get("qps") is not None]
    read_rates = [r["error_rate"] for r in all_read if r.get("error_rate") is not None]
    worst = max(
        ((float(v.get("p95") or 0.0), key) for key, v in degradation.items()),
        default=(0.0, "-"),
    )
    fairness_ratios = [
        v.get("p95_max_min_ratio") for v in fairness.values() if v.get("p95_max_min_ratio") is not None
    ]
    trend = resources.get("rss_trend") or {}

    rows: list[tuple[str, str, str]] = [
        (
            "QPS",
            "每秒完成的操作数 = count ÷ 场景墙钟时长，反映吞吐上限",
            f"读 QPS 范围 {_fmt(min(read_qps))} ~ {_fmt(max(read_qps))}" if read_qps else "无读数据",
        ),
        (
            "P50 / P95 / P99 (ms)",
            "该操作全部样本耗时升序排列后线性插值分位数；P95 表示 95% 请求在其内完成",
            "各场景见「压测结果」读 / 写延迟表",
        ),
        (
            "avg / max / min (ms)",
            "平均 / 最大 / 最小耗时",
            "各场景见「压测结果」延迟表",
        ),
        (
            "error_rate",
            "错误请求数 ÷ 总请求数；错误分类 timeout / http_4xx / http_5xx / connection / other",
            f"读错误率范围 {_fmt(min(read_rates))} ~ {_fmt(max(read_rates))}" if read_rates else "无读数据",
        ),
        (
            "劣化倍数 (degradation)",
            "目标场景读 Pxx ÷ 同并发 A 基线读 Pxx；>1 表示写负载期间读延迟被抬高",
            f"本次最大 P95 劣化 {_fmt(worst[0])}x（{worst[1]}）" if worst[0] else "无劣化对照",
        ),
        (
            "写后读一致性 (ms)",
            "写事务完成后轮询检索直至命中锚词的耗时窗口，越小表示写后立即可见",
            f"count={consistency.get('count')}, P50={_fmt(consistency.get('p50_ms'))}ms, "
            f"超时={consistency.get('timeouts')}" if consistency else "未测量",
        ),
        (
            "commit 异步完成延迟 (ms)",
            "commit_submit(202) 到观察到 completed 的等待时长（commit_done 阶段）",
            f"count={commit_lat.get('count')}, P50={_fmt(commit_lat.get('p50_ms'))}ms, "
            f"P95={_fmt(commit_lat.get('p95_ms'))}ms" if commit_lat else "未测量",
        ),
        (
            "commit 成功保证",
            "202 接受的提交最终必须 completed；violations = 接受后最终失败数",
            f"接受 {durability.get('submit_ok_total')} / 完成 {durability.get('accepted_done_ok')} / "
            f"违规 {durability.get('guarantee_violations')}" if durability else "未测量",
        ),
        (
            "租户公平性 P95 max/min 比",
            "场景内各租户读 P95 的最大值 ÷ 最小值；≥3x 判不均衡",
            f"最大比 {_fmt(max(fairness_ratios))}x" if fairness_ratios else "单租户 / 无数据",
        ),
        (
            "Commit 吞吐 Jain",
            "按租户分别统计同一窗口内 completed Commit 吞吐，再计算 Jain；1.0 表示完全均衡",
            (
                "按场景见「租户公平性」表；不与 Search 延迟混算"
                if fairness
                else "未测量"
            ),
        ),
        (
            "Search 延迟效用 Jain",
            "按租户将 Search P95 转为 1000 ÷ P95(ms) 的效用后计算 Jain；1.0 表示延迟完全均衡",
            (
                "按场景见「租户公平性」表；原始 P95 仍单独保留"
                if fairness
                else "未测量"
            ),
        ),
        (
            "租户最慢多等 (ms)",
            "slowest_waits_extra_ms：最慢租户比最快租户多等的读 P95 时长",
            "见「特性量化分析」",
        ),
        (
            "CPU 利用率 (%)",
            "服务端 /metrics 帧差换算的单核百分比（均值 / 峰值）",
            f"均值 {_fmt(resources.get('cpu_util_mean_percent'))}% / "
            f"峰值 {_fmt(resources.get('cpu_util_max_percent'))}%" if resources else "未采集",
        ),
        (
            "RSS (MB)",
            "进程常驻内存：基线 / 峰值 / 冷却后未回落量",
            f"基线 {_fmt(resources.get('rss_baseline_mb'))} / 峰值 {_fmt(resources.get('rss_peak_mb'))} / "
            f"未回落 {_fmt(resources.get('rss_unsettled_mb'))}" if resources else "未采集",
        ),
        (
            "RSS 上升斜率 (MB/min)",
            "RSS 时序最小二乘斜率；≥5 MB/min 判疑似泄漏",
            f"{_fmt(trend.get('slope_mb_per_min'))} MB/min（R²={_fmt(trend.get('r2'))}）" if trend else "未采集",
        ),
        (
            "线程 / commit 队列 / inflight",
            "服务端进程线程峰值、commit 队列深度峰值、HTTP 在途请求峰值",
            f"{_fmt(resources.get('threads_max'))} / {_fmt(resources.get('commit_queue_max'))} / "
            f"{_fmt(resources.get('http_inflight_max'))}" if resources else "未采集",
        ),
    ]
    return (
        "<h2>指标字典</h2>"
        + _note("下表说明本报告每个指标的含义与计算方式，并给出本次运行的实测值（支撑事实）；"
                "分场景数值见「压测结果」各表与图表。")
        + _stat_table(rows, ["指标", "含义（如何计算）", "本报告实测"])
    )


def _degradation_chart(summary: dict[str, Any]) -> str:
    """劣化倍数图：每个 _vs_ 对照的 P50/P95/P99 分组柱状图。"""
    degradation = summary.get("degradation") or {}
    if not degradation:
        return ""
    labels = [key.partition("_vs_")[0] for key in degradation]
    series = [
        {"label": "P50", "values": [float(f.get("p50") or 0.0) for f in degradation.values()], "color": "#7bc96f"},
        {"label": "P95", "values": [float(f.get("p95") or 0.0) for f in degradation.values()], "color": "#e9b949"},
        {"label": "P99", "values": [float(f.get("p99") or 0.0) for f in degradation.values()], "color": "#e26d5c"},
    ]
    return (
        "<h3>写负载下读延迟劣化倍数（目标场景 vs 同并发 A 基线）</h3>"
        + _bar_chart(labels, series, "劣化倍数（1.0 = 与基线持平）", "x")
    )


def _write_stage_chart(summary: dict[str, Any]) -> str:
    """写事务四段延迟图：写场景的 open / add / commit_submit / commit_done P50。"""
    scenes = summary.get("scenes") or {}
    labels: list[str] = []
    open_v: list[float] = []
    add_v: list[float] = []
    submit_v: list[float] = []
    done_v: list[float] = []
    for key in sorted(scenes):
        ops = ((scenes[key].get("ops") or {}).get(key) or {})
        if not any(op in ops for op in ("open", "add", "commit_submit", "commit_done")):
            continue
        labels.append(key)
        open_v.append(float((ops.get("open") or {}).get("p50_ms") or 0.0))
        add_v.append(float((ops.get("add") or {}).get("p50_ms") or 0.0))
        submit_v.append(float((ops.get("commit_submit") or {}).get("p50_ms") or 0.0))
        done_v.append(float((ops.get("commit_done") or {}).get("p50_ms") or 0.0))
    if not labels:
        return ""
    return _bar_chart(
        labels,
        [
            {"label": "open", "values": open_v, "color": "#4c8bf5"},
            {"label": "add", "values": add_v, "color": "#7bc96f"},
            {"label": "commit_submit", "values": submit_v, "color": "#e9b949"},
            {"label": "commit_done", "values": done_v, "color": "#e26d5c"},
        ],
        "写事务四段延迟 P50 (ms)",
        "ms",
    )


def _fairness_chart(summary: dict[str, Any]) -> str:
    """租户公平性图：每个多租户场景各租户的读 P95 分组柱状图。"""
    fairness = summary.get("tenant_fairness") or {}
    labels: list[str] = []
    per_tenant: dict[int, list[float]] = {}
    palette = ["#4c8bf5", "#e26d5c", "#7bc96f", "#e9b949", "#9b6dff"]
    for key, fair in fairness.items():
        tenants = fair.get("tenants") or []
        if len(tenants) < 2:
            continue
        labels.append(str(key))
        for row in tenants:
            per_tenant.setdefault(int(row.get("tenant_idx") or 0), []).append(
                float(row.get("p95_ms") or 0.0)
            )
    if not labels:
        return ""
    series = [
        {"label": f"租户 {idx}", "values": values, "color": palette[idx % len(palette)]}
        for idx, values in sorted(per_tenant.items())
    ]
    return (
        "<h3>各场景租户间读 P95 (ms)</h3>"
        + _bar_chart(labels, series, "租户公平性：同场景不同租户读延迟对比", "ms")
    )


def build_html(summary: dict[str, Any], chart_series: dict[str, Any]) -> str:
    """Assemble the self-contained report page from summary + time series."""
    scenes = summary.get("scenes", {})
    read_rows: list[tuple[str, ...]] = []
    write_rows: list[tuple[str, ...]] = []
    failed_rows: list[tuple[str, ...]] = []
    for key in sorted(scenes):
        scene = scenes[key]
        if scene.get("status") == "failed":
            failed_rows.append(
                (
                    key,
                    str(scene.get("scene_id", "")),
                    str(scene.get("per_tenant_conc", "")),
                    html.escape(str(scene.get("error", ""))),
                )
            )
            continue
        ops = ((scene.get("ops") or {}).get(key) or {})
        read = ops.get("read") or {}
        write = ops.get("commit_done") or {}
        read_rows.append(
            (
                key,
                read.get("count", 0),
                read.get("qps", ""),
                read.get("p50_ms", ""),
                read.get("p95_ms", ""),
                read.get("p99_ms", ""),
                read.get("error_rate", ""),
            )
        )
        write_rows.append(
            (
                key,
                write.get("count", 0),
                write.get("p50_ms", ""),
                write.get("p95_ms", ""),
                write.get("p99_ms", ""),
                write.get("error_rate", ""),
            )
        )

    config = summary.get("config") or {}
    summary_block = (
        '<h3>判定摘要</h3><ul>'
        + "".join(f"<li>{html.escape(str(value))}</li>" for value in _summary_bullets(summary))
        + "</ul>"
    )
    verdicts = summary.get("feature_verdicts") or {}
    verdict_block = _verdict_table(verdicts)
    quantified_block = _quantified_section(verdicts)

    blocks: list[str] = []

    # -- 报告概述 + 测试方法 + 测试场景 + 指标字典 ------------------------------
    server = summary.get("server") or {}
    blocks.append(
        "<h2>报告概述</h2>"
        + _stat_table(
            [
                ("压测对象", str(server.get("base_url", config.get("echomem_url", "")))),
                ("运行状态", str(summary.get("status", ""))),
                ("开始时间", str(summary.get("started_at", ""))),
                ("结束时间", str(summary.get("finished_at", ""))),
                ("生成器", str(summary.get("generator", ""))),
            ],
            ["项目", "值"],
        )
    )
    if summary.get("status") == "failed" or summary.get("run_error"):
        blocks.append(
            "<h3>压测执行失败</h3>"
            + _note("本次压测执行异常中断，以下为失败原因与已收集到的部分数据。")
            + "<p style='color:#e26d5c;font-weight:bold'>"
            + html.escape(str(summary.get("error") or summary.get("run_error") or ""))
            + "</p>"
        )
    blocks.append(_methodology(summary))
    blocks.append(_scenarios_section(summary))
    blocks.append(_metric_glossary(summary))

    # -- 特性结论 / 量化分析 / 判定摘要 ----------------------------------------
    blocks.append(verdict_block)
    blocks.append(_baseline_validity_note(summary))
    if quantified_block:
        blocks.append(quantified_block)
    blocks.append(summary_block)

    # -- 压测结果：客户端读 / 写指标 -------------------------------------------
    blocks.append(
        "<h2>压测结果</h2>"
        + _note("客户端视角：逐请求计时统计。QPS = 场景墙钟时长内完成请求数 ÷ 秒；"
                "P50/P95/P99 为耗时升序线性插值分位数；错误分类见各表下方说明。")
    )
    if failed_rows:
        blocks.append(
            "<h3>执行失败场景</h3>"
            + _note("以下场景执行异常中断，未产出该场景的读 / 写指标；其余场景结果不受影响。")
            + _stat_table(failed_rows, ["场景", "类型", "并发/租户", "失败原因"])
        )
    if read_rows:
        labels = [row[0] for row in read_rows]
        blocks.append(
            _bar_chart(
                labels,
                [
                    {"label": "QPS", "values": [float(row[2] or 0) for row in read_rows], "color": "#4c8bf5"},
                ],
                "各场景读吞吐 (QPS)",
                "qps",
            )
        )
        blocks.append(
            _bar_chart(
                labels,
                [
                    {"label": "P50", "values": [float(row[3] or 0) for row in read_rows], "color": "#7bc96f"},
                    {"label": "P95", "values": [float(row[4] or 0) for row in read_rows], "color": "#e9b949"},
                    {"label": "P99", "values": [float(row[5] or 0) for row in read_rows], "color": "#e26d5c"},
                ],
                "读延迟分位数 (ms)",
                "ms",
            )
        )
        blocks.append(
            "<h3>读指标（支撑事实）</h3>"
            + _note("P50/P95/P99 单位 ms；error_rate = 错误请求 ÷ 总请求，细分 timeout / "
                    "http_4xx / http_5xx / connection / other（见 summary.scenes[*].ops）。")
            + _stat_table(read_rows, ["场景", "请求数", "QPS", "P50(ms)", "P95(ms)", "P99(ms)", "错误率"])
        )
    if write_rows:
        blocks.append(
            "<h3>写指标（commit_done 阶段，ms）</h3>"
            + _note("写事务完整路径 open → add → commit_submit → commit_done；本表为 commit_done 阶段"
                    "（从提交成功到观察到异步完成）的等待时长，四段分解见「写事务四段延迟」图。")
            + _stat_table(write_rows, ["场景", "事务数", "P50", "P95", "P99", "错误率"])
        )
    stage_chart = _write_stage_chart(summary)
    if stage_chart:
        blocks.append(
            "<h2>写事务四段延迟</h2>"
            + _note("open / add / commit_submit 为同步前置段，commit_done 为异步完成等待；P50 单位 ms。")
            + stage_chart
        )

    # -- 读写干扰：劣化倍数 -----------------------------------------------------
    degradation = summary.get("degradation", {})
    if degradation:
        blocks.append(_degradation_chart(summary))
        rows = [
            (key, value.get("p50", ""), value.get("p95", ""), value.get("p99", ""))
            for key, value in degradation.items()
        ]
        blocks.append(
            "<h3>注入阻塞检索判定（支撑事实，劣化倍数）</h3>"
            + _note(
                "劣化倍数 = 目标场景读 Pxx ÷ 同并发 A 基线读 Pxx；>1 表示写负载期间读延迟上升，"
                f"≥ 阈值 {config.get('degradation_threshold', '2')}x 判 FAIL。"
            )
            + _stat_table(rows, ["对照", "P50", "P95", "P99"])
        )

    signals = summary.get("signals", {})
    if signals.get("signals_found"):
        blocks.append(
            "<h3>信号（支撑事实）</h3>"
            + _note("自动检出的异常信号列表（劣化超阈值 / 请求积压 / engine 调用激增等）。")
            + "<ul>"
            + "".join(f"<li>{html.escape(str(s))}</li>" for s in signals["signals_found"])
            + "</ul>"
        )

    # -- 压测过程确认的问题（EchoMem 侧缺陷/观察） -----------------------------
    findings = summary.get("process_findings") or []
    if findings:
        findings_html = ["<h3>压测过程发现的问题</h3>"]
        findings_html.append(
            _note("压测执行期间确认并核实的问题记录；severity=bug 为确认的 EchoMem 缺陷，"
                  "observation 为观察项（含压测工具/环境问题，供区分）。")
        )
        for finding in findings:
            if isinstance(finding, dict):
                title = html.escape(str(finding.get("title", "")))
                scope = str(finding.get("scope", ""))
                severity = str(finding.get("severity", ""))
                evidence = html.escape(str(finding.get("evidence", "")))
                impact = html.escape(str(finding.get("impact", "")))
                badge = f'<span style="color:#e26d5c;font-weight:bold">[{html.escape(severity)}]</span>'
                scope_txt = f'<span style="color:#888">({html.escape(scope)})</span>' if scope else ""
                findings_html.append(
                    f"<p><b>{title}</b> {badge} {scope_txt}<br/>"
                    f"{impact}<br/>"
                    f"<span style='color:#555'>证据：{evidence}</span></p>"
                )
            else:
                findings_html.append(f"<p>{html.escape(str(finding))}</p>")
        blocks.append("".join(findings_html))

    resources = summary.get("resources", {})
    if resources:
        rss_trend = resources.get("rss_trend") or {}
        rss_norm = resources.get("rss_normalized") or {}
        norm_rows = []
        if rss_norm:
            net_trend = rss_norm.get("net_trend") or {}
            norm_rows = [
                ("注入数据量 (MB)", str(rss_norm.get("injected_mb"))),
                ("净 RSS 峰值 (MB)", str(rss_norm.get("net_peak_mb"))),
                ("净 RSS 冷却后 (MB)", str(rss_norm.get("net_settled_mb"))),
                ("净 RSS 上升斜率 (MB/min)", str(net_trend.get("slope_mb_per_min"))),
            ]
        blocks.append(
            "<h3>资源与内存趋势（支撑事实）</h3>"
            + _note("CPU 为帧差换算单核百分比；RSS 基线为压测前采样，未回落 = 冷却后与基线差（负值表示低于基线）；"
                    "上升斜率 ≥ 5 MB/min 判疑似泄漏。归一校正：净 RSS = 原始 RSS − 按注入消息字节估计的索引增长，"
                    "正常数据增长不计为泄漏。")
            + _stat_table(
                [
                    ("CPU 均值 (%)", str(resources.get("cpu_util_mean_percent"))),
                    ("RSS 基线 (MB)", str(resources.get("rss_baseline_mb"))),
                    ("RSS 原始峰值 (MB)", str(resources.get("rss_peak_mb"))),
                    ("RSS 冷却后 (MB)", str(resources.get("rss_settled_mb"))),
                    ("RSS 未回落 (MB)", str(resources.get("rss_unsettled_mb"))),
                    ("RSS 上升斜率 (MB/min)", str(rss_trend.get("slope_mb_per_min"))),
                    ("RSS 拟合 R²", str(rss_trend.get("r2"))),
                    ("线程峰值", str(resources.get("threads_max"))),
                    ("commit 队列峰值", str(resources.get("commit_queue_max"))),
                ]
                + norm_rows,
                ["指标", "值"],
            )
        )

    lines = [
        ("CPU 利用率 (%)", chart_series.get("cpu_percent", []), "#ff7f0e"),
        ("RSS (MB)", chart_series.get("rss_mb", []), "#4c8bf5"),
        ("线程数", chart_series.get("threads", []), "#7bc96f"),
        ("commit 队列深度", chart_series.get("commit_queue", []), "#e26d5c"),
        ("inflight 请求", chart_series.get("inflight", []), "#9b6dff"),
    ]
    lines = [(name, samples, color) for name, samples, color in lines if samples]
    if lines:
        blocks.append(
            "<h3>资源利用率随时间变化（每系列独立子图，量纲各异）</h3>"
            + _note("原始采样时序见 metrics_samples.csv；图中横轴为压测起止时间（秒）。")
        )
        for name, samples, color in lines:
            blocks.append(_line_chart([(name, samples, color)], name))

    durability = summary.get("commit_durability", {})
    if durability:
        simple_rows = [
            (str(key), str(value))
            for key, value in durability.items()
            if not isinstance(value, dict)
        ]
        blocks.append(
            "<h3>commit 成功保证（特性 1，支撑事实）</h3>"
            + _note("202 接受的提交最终必须 completed；accepted_done_poll_timeout 表示观测窗口"
                    "（--commit-poll-timeout-s）到期，不代表 commit 本身失败。")
            + _stat_table(simple_rows, ["指标", "值"])
        )
        rejected = durability.get("submit_rejected_breakdown")
        if rejected:
            blocks.append(
                "<h4>提交阶段拒绝分类（不可重试）</h4>"
                + _stat_table(
                    [(str(key), str(count)) for key, count in rejected.items()],
                    ["错误类型", "次数"],
                )
            )

    fairness = summary.get("tenant_fairness", {})
    if fairness:
        blocks.append(_fairness_chart(summary))
        rows = [
            (
                key,
                str(len(fair.get("tenants", []))),
                str(fair.get("p95_max_min_ratio", "")),
                str(fair.get("p95_cv", "")),
                "均衡" if fair.get("balanced") else "不均衡",
            )
            for key, fair in fairness.items()
        ]
        blocks.append(
            "<h3>租户公平性（特性 2，支撑事实）</h3>"
            + _note(
                "公平性只比较不同租户；Commit 吞吐 Jain 与 Search 延迟效用 Jain 分开计算。"
                "Search 延迟先转换为 1000 ÷ P95(ms) 的效用，不能直接把原始延迟当作越大越好。"
            )
            + _stat_table(
                [
                    (
                        key,
                        str(len(fair.get("tenants", []))),
                        str(fair.get("p95_max_min_ratio", "")),
                        str(fair.get("p95_cv", "")),
                        str(fair.get("commit_throughput_jain", "")),
                        str(fair.get("search_latency_utility_jain", "")),
                        "均衡" if fair.get("balanced") else "不均衡",
                    )
                    for key, fair in fairness.items()
                ],
                [
                    "场景",
                    "租户数",
                    "读 P95 max/min 比",
                    "读 P95 CV",
                    "Commit 吞吐 Jain",
                    "Search 延迟效用 Jain",
                    "结论",
                ],
            )
        )

    # -- 扩展目标支撑事实：重试 / 对账 / 质量断言 / 隔离 / 错误类型 / 故障注入 / 预检 --
    blocks.append(_extended_evidence_section(summary))

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>EchoMem 性能压测报告</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;color:#222}"
        "table{border-collapse:collapse;font-size:13px}td,th{padding:4px 10px}"
        "h2,h3{margin-top:28px}"
        "p.note{color:#555;font-size:12px;margin:6px 0 10px 0}"
        ".v-pass{background:#e8f5e9;color:#1b5e20}.v-fail{background:#fdecea;color:#b71c1c}"
        ".v-inconclusive{background:#f5f5f5;color:#616161}"
        ".v-overall{font-weight:bold;font-size:15px}</style></head><body>"
        + "".join(blocks)
        + "</body></html>"
    )


def _extended_evidence_section(summary: dict[str, Any]) -> str:
    """扩展目标的支撑事实：重试 / 对账 / 质量断言 / 隔离 / 错误类型 / 故障 / 预检。"""
    blocks: list[str] = ["<h2>扩展目标支撑事实</h2>"]

    retry = summary.get("write_retry") or {}
    if retry:
        rows = [
            ("commit_submit 总数", str(retry.get("submit_total"))),
            ("重试过的事务数", str(retry.get("retried_total"))),
            ("首次尝试成功（原始值）", f"{retry.get('first_attempt_ok')}（成功率 {retry.get('first_attempt_rate')}）"),
            ("重试后最终成功（重试后值）", f"{retry.get('final_ok')}（成功率 {retry.get('final_success_rate')}）"),
            ("重试耗尽仍失败", str(retry.get("retry_exhausted_failures"))),
        ]
        retried_errors = retry.get("retried_errors") or {}
        if retried_errors:
            rows += [(f"重试失败分类 {key}", str(value)) for key, value in retried_errors.items()]
        blocks.append(
            "<h3>写事务重试（特性 5，支撑事实）</h3>"
            + _note("429+Retry-After 与 5xx/超时/连接按退避重试（上限 --commit-retry-max）；"
                    "业务 4xx 不重试。原始值=首次尝试口径，重试后值=最终成功口径。")
            + _stat_table(rows, ["指标", "值"])
        )

    reconciliation = summary.get("reconciliation") or {}
    sessions = reconciliation.get("sessions") or []
    if sessions:
        check_rows: list[tuple[str, ...]] = []
        for session in sessions:
            for check in session.get("checks") or []:
                ok = check.get("ok")
                status = {True: "通过", False: "失败", None: "不可用"}.get(ok, str(ok))
                check_rows.append(
                    (str(session.get("session_id"))[:24], str(check.get("name")), status, str(check.get("detail")))
                )
        blocks.append(
            "<h3>消息对账与去重（特性 5，支撑事实）</h3>"
            + _note(f"总体: {reconciliation.get('verdict')} — {reconciliation.get('reason')}。"
                    "核验: 客户端消息全集 ⊆ 服务端 history、服务端无重复、archive completed、"
                    "atom source_turn_ids 无重复且 ⊆ 客户端 message_id；接口缺失标记「不可用」。")
            + _stat_table(check_rows, ["会话", "核验项", "结果", "明细"])
        )

    quality = summary.get("search_quality") or {}
    if quality:
        gated = quality.get("gated_read_stats") or {}
        rows = [
            ("read 总数（ok）", str(quality.get("total"))),
            (
                "已验证 recall 查询数",
                str(quality.get("recall_total", quality.get("anchor_total"))),
            ),
            (
                "recall 未召回（干净质量失败）",
                str(quality.get("recall_failures", quality.get("anchor_failures"))),
            ),
            (
                "recall 降级响应（引擎跳过/饱和）",
                str(quality.get("recall_degraded", quality.get("anchor_degraded"))),
            ),
            ("降级响应总数（degraded）", str(quality.get("degraded_total"))),
            ("no-recall 日常查询数", str(quality.get("no_recall_total", quality.get("ordinary_total")))),
            ("real_recall 无法判定", str(quality.get("undetermined_real_recall"))),
            ("hit_count P50/P95", f"{quality.get('hit_count_p50')} / {quality.get('hit_count_p95')}"),
            ("实际召回 read 的 P95 (ms)", str(gated.get("p95_ms"))),
        ]
        kind_stats = quality.get("query_kind_stats") or {}
        if kind_stats:
            rows.append(("query kind 覆盖", ", ".join(f"{k}={v.get('count')}" for k, v in kind_stats.items())))
        blocks.append(
            "<h3>search 质量断言（特性 6，支撑事实）</h3>"
            + _note("recall 流量只使用“预注入后已验证命中”的 query，必须再次召回（hit_count≥1）；"
                    "干净空结果计为失败。若服务端标记 degraded（引擎跳过/饱和），空结果是容量伪影而非"
                    "召回缺陷，单独统计。no-recall 日常查询允许空结果，用于观察正常空结果路径的稳定性和"
                    "延迟；延迟仍按 query kind 分开统计。")
            + _stat_table(rows, ["指标", "值"])
        )

    isolation = summary.get("isolation") or {}
    if isolation:
        iso_rows: list[tuple[str, ...]] = []
        for scene_key, group in isolation.items():
            same = group.get("same_tenant") or {}
            cross = group.get("cross_tenant") or {}
            iso_rows.append(
                (
                    scene_key,
                    str(group.get("burst_tenant_idx")),
                    str(same.get("count")),
                    str(same.get("p95_ms")),
                    str(group.get("same_tenant_degradation")),
                    str(cross.get("count")),
                    str(cross.get("p95_ms")),
                    str(group.get("cross_tenant_degradation")),
                    str(group.get("verdict")),
                )
            )
        blocks.append(
            "<h3>读写隔离细粒度（特性 7，支撑事实）</h3>"
            + _note("洪峰窗口内 read 按租户分组：同租户=发起洪峰写入的租户自己的 search；跨租户=其它租户。"
                    "判定: 跨租户 P95 劣化 ≤ 同租户劣化 且均 < --degradation-threshold。")
            + _stat_table(
                iso_rows,
                ["场景", "洪峰租户", "同租户数", "同租户P95", "同租户劣化",
                 "跨租户数", "跨租户P95", "跨租户劣化", "结论"],
            )
        )

    error_type = summary.get("error_type_validation") or {}
    if error_type:
        rows = [("结论", str(error_type.get("verdict"))), ("依据", str(error_type.get("reason")))]
        breakdown = error_type.get("observed_breakdown") or {}
        rows += [(f"观测 {key}", str(value)) for key, value in breakdown.items()]
        blocks.append(
            "<h3>服务端错误类型正确性（特性 8，支撑事实）</h3>"
            + _note("对每种拒绝/故障场景断言服务端返回的错误类型与预期一致（429 带 Retry-After、"
                    "400 模型名、5xx 模型故障、超时挂起、终态 failed），作为其它判定成立的前提。")
            + _stat_table(rows, ["指标", "值"])
        )

    fault = summary.get("fault_injection") or {}
    if fault.get("stages"):
        rows = [
            (
                str(stage.get("stage")),
                str(stage.get("behavior")),
                str(stage.get("expected_error_type")),
                str(stage.get("observed_error_type")),
                str(stage.get("requests")),
                str(stage.get("recovered")),
                "通过" if stage.get("ok") else "失败",
            )
            for stage in fault.get("stages") or []
        ]
        blocks.append(
            "<h3>故障注入（特性 9，mock 可控故障语义证据）</h3>"
            + _note(f"总体: {fault.get('verdict')} — {fault.get('reason')}。注入对象为外部 LLM/embedding 端点"
                    "（engine api_base 指向 mock，只改配置不改服务端）；500→5xx、挂起→超时、429→Retry-After，"
                    "恢复阶段须成功。真实容量证据另测，两类证据禁止互相替代。")
            + _stat_table(rows, ["阶段", "注入行为", "期望错误类型", "观测错误类型", "请求数", "恢复", "结论"])
        )

    preflight = summary.get("preflight") or {}
    if preflight:
        rows = [("预检结论", "通过" if preflight.get("ok") else "失败"),
                ("engine 数", str(preflight.get("engines_checked"))),
                ("配置摘要 (SHA-256)", str(preflight.get("digest")))]
        for engine in preflight.get("engines") or []:
            rows.append(
                (
                    f"engine {engine.get('id')} ({engine.get('kind')})",
                    f"模型 {engine.get('model')} 状态 {engine.get('status')} "
                    f"code={engine.get('code')} 耗时 {engine.get('elapsed_s')}s",
                )
            )
        blocks.append(
            "<h3>模型与配置预检门禁（特性 10，支撑事实）</h3>"
            + _note("逐 engine 解析 api_key_env/api_base/model，检查环境变量、对每个 endpoint 发最小真实请求、"
                    "校验模型名被支持；任一失败即停止压测并归类环境/依赖失败。")
            + _stat_table(rows, ["指标", "值"])
        )

    return "".join(blocks)


def _baseline_validity_note(summary: dict[str, Any], threshold: float = 0.2) -> str:
    """A 纯读基线错误率过高时，劣化倍数判定失真，显式警示（假 PASS 检测）。

    劣化倍数以 A 基线的读延迟为分母；当 A 自身已大量超时/报错时比值趋近 1，
    会把真实劣化误判为「无劣化」。逐并发档检查 A 基线错误率，超阈值则告警，
    并提示总体 PASS 可能是基线失效导致的假 PASS。
    """
    scenes = summary.get("scenes") or {}
    verdicts = summary.get("feature_verdicts") or {}
    overall = verdicts.get("overall", "")
    bad: list[str] = []
    for key, scene in scenes.items():
        if not key.startswith("A@"):
            continue
        read = ((scene.get("ops") or {}).get(key) or {}).get("read") or {}
        count = read.get("count") or 0
        errors = read.get("errors_total") or 0
        if count <= 0:
            continue
        err_rate = errors / count
        if err_rate > threshold:
            bad.append(
                f"<li><b>{html.escape(key)}</b> 纯读基线错误率 {err_rate:.0%}（{errors}/{count}），"
                f"超过阈值 {threshold:.0%}：该档劣化倍数分母已失真，此并发档的 PASS/FAIL 判定不可信。</li>"
            )
    if not bad:
        return ""
    head = (
        "<h3>基线有效性警示</h3>"
        + _note(
            "劣化倍数 = 目标场景读 P95 ÷ 同并发 A 纯读基线读 P95；A 基线自身超时/报错会使比值趋近 1，"
            "掩盖真实劣化（假 PASS）。以下并发档的 A 基线已失效："
        )
    )
    if overall == "PASS":
        head += (
            "<p style='color:#e26d5c;font-weight:bold'>总体结论为 PASS，但上述基线档已失效："
            "此 PASS 是基线自身劣化导致的假 PASS，不代表系统在该并发下表现良好；"
            "请以「压测过程发现的问题」与各场景原始延迟/错误率数据为准。</p>"
        )
    elif overall == "FAIL":
        head += (
            "<p style='color:#e9b949'>总体结论为 FAIL，但上述基线档已失效："
            "这些档位之间的劣化对比无意义，请核对结论所依赖的档位是否仍有效。</p>"
        )
    return head + "<ul>" + "".join(bad) + "</ul>"


def _verdict_table(verdicts: dict[str, Any]) -> str:
    """Feature-verdict table: 特性 | 结论 | 依据（含判定分层与 SLO 口径）。"""
    features = verdicts.get("features") or {}
    overall = verdicts.get("overall", "INCONCLUSIVE")
    labels = FEATURE_LABELS
    verdict_text = {
        "PASS": "通过",
        "FAIL": "不通过",
        "INCONCLUSIVE": "数据不足",
        "not_run": "未执行",
        "known_limit": "已知限制",
        "env_error": "环境/依赖失败",
    }
    css_class = {
        "PASS": "v-pass",
        "FAIL": "v-fail",
        "INCONCLUSIVE": "v-inconclusive",
        "not_run": "v-inconclusive",
        "known_limit": "v-inconclusive",
        "env_error": "v-fail",
    }
    rows = []
    for feature_key, title in labels.items():
        entry = features.get(feature_key) or {}
        verdict = entry.get("verdict", "INCONCLUSIVE")
        evidence = (
            f' <span style="color:#888">[{html.escape(str(entry.get("evidence_type", "real")))}]</span>'
            if entry.get("evidence_type")
            else ""
        )
        rows.append(
            "<tr>"
            f'<td>{html.escape(title)}{evidence}</td>'
            f'<td class="{css_class.get(verdict, "")}">{html.escape(verdict_text.get(verdict, verdict))}</td>'
            f'<td>{html.escape(entry.get("reason", ""))}</td>'
            "</tr>"
        )
    if features.get("commit_guarantee", {}).get("sub"):
        subs = features["commit_guarantee"]["sub"]
        for sub_key, sub in subs.items():
            sub_title = {
                "durability": "   ├─ commit 成功保证（202 接受后必须 completed）",
                "retrieval_precedence": "   ├─ search 优先级（注入洪峰下读延迟不劣化）",
            }.get(sub_key, sub_key)
            verdict = sub.get("verdict", "INCONCLUSIVE")
            rows.append(
                "<tr>"
                f'<td>{html.escape(sub_title)}</td>'
                f'<td class="{css_class.get(verdict, "")}">{html.escape(verdict_text.get(verdict, verdict))}</td>'
                f'<td>{html.escape(sub.get("reason", ""))}</td>'
                "</tr>"
            )
    overall_class = css_class.get(overall, "")
    overall_text = verdict_text.get(overall, overall)
    return (
        "<h2>特性结论</h2>"
        f"<table border='1' cellspacing='0' cellpadding='4'>"
        "<thead><tr><th>特性</th><th>结论</th><th>判定依据</th></tr></thead>"
        f"<tbody>{''.join(rows)}"
        f'<tr class="v-overall"><td>总体结论</td>'
        f'<td class="{overall_class}">{html.escape(overall_text)}</td>'
        "<td>任一特性“不通过/环境依赖失败”则总体同判；存在“数据不足”且无失败时同判数据不足；"
        "未执行/已知限制不拉低总体</td></tr>"
        "</tbody></table>"
        + _layers_and_slo(verdicts)
    )


def _layers_and_slo(verdicts: dict[str, Any]) -> str:
    """判定分层（real/mock 证据分节）+ SLO 口径表。"""
    blocks: list[str] = []
    layers = verdicts.get("verdict_layers") or {}
    if layers:
        blocks.append("<h3>判定分层（证据类型）</h3>")
        for layer, mapping in layers.items():
            if not mapping:
                continue
            rows = [(html.escape(k), str(v)) for k, v in mapping.items()]
            blocks.append(
                _note(
                    "real = 真实容量证据（真实服务端/模型）；mock = 可控故障语义证据（mock provider）。"
                    "两类证据分节报告，禁止互相替代。"
                )
                + f"<h4>证据类型: {html.escape(layer)}</h4>"
                + _stat_table(rows, ["特性", "结论"])
            )
    slo = verdicts.get("slo_accounting") or {}
    if slo:
        rows = [
            (html.escape(key), str(value.get("numerator", "")), str(value.get("denominator", "")),
             str(value.get("window", "")), "含" if value.get("retry_included") else "不含")
            for key, value in slo.items()
        ]
        blocks.append(
            "<h3>SLO 口径</h3>"
            + _note("每个指标的分子 / 分母 / 时间窗口 / 是否含客户端重试；涉及重试的指标同时输出原始值与重试后值。")
            + _stat_table(rows, ["指标", "分子", "分母", "时间窗口", "含重试"])
        )
    return "".join(blocks)


def _quantified_section(verdicts: dict[str, Any]) -> str:
    """特性量化分析：每个特性的关键测量值两列表（dict 递归拍平）。

    紧跟在结论表之后：除 PASS/FAIL 之外，给出「满足到什么程度」——
    洪峰读延迟劣化的绝对毫秒数、租户间最坏等待差、RSS 增长率与小时
    推算、CPU/RSS 时间线极值。
    """
    features = verdicts.get("features") or {}
    if not features:
        return ""
    blocks = ["<h2>特性量化分析</h2>"]
    labels = FEATURE_LABELS
    for feature_key, title in labels.items():
        measurements = (features.get(feature_key) or {}).get("measurements")
        if not measurements:
            continue
        rows: list[tuple[str, str]] = []
        _flatten_measurements("", measurements, rows)
        blocks.append(
            "<h3>" + html.escape(title) + "</h3>"
            + _stat_table(rows, ["指标", "值"])
        )
    return "".join(blocks)


def _flatten_measurements(prefix: str, value: Any, rows: list[tuple[str, str]]) -> None:
    """Recursively flatten a measurements dict into (indicator, value) rows."""
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten_measurements(f"{prefix}.{key}" if prefix else key, child, rows)
    else:
        rows.append((prefix, "" if value is None else str(value)))


def _summary_bullets(summary: dict[str, Any]) -> list[str]:
    bullets: list[str] = []
    resources = summary.get("resources", {})
    total_reads = sum(
        (scene.get("read") or {}).get("count", 0) for scene in summary.get("scenes", {}).values()
    )
    bullets.append(f"总读请求：{total_reads}")
    degradation = summary.get("degradation", {})
    if degradation:
        worst = max(
            ((float(value.get("p99") or 0), key) for key, value in degradation.items()),
            default=(0.0, ""),
        )
        bullets.append(f"最大 P99 劣化：{worst[0]:.2f}x（{worst[1]}）")
    if resources.get("rss_unsettled_mb"):
        bullets.append(
            f"内存未回落：{resources['rss_unsettled_mb']} MB（可能泄漏）"
        )
    if summary.get("consistency"):
        consistency = summary["consistency"]
        bullets.append(
            f"写后读一致性：P50={consistency.get('p50_ms')}ms "
            f"P95={consistency.get('p95_ms')}ms 超时={consistency.get('timeouts')}"
        )
    reconciliation = summary.get("reconciliation") or {}
    if reconciliation.get("sessions"):
        bullets.append(
            f"消息对账：{reconciliation.get('verdict')}（{reconciliation.get('reason', '')}）"
        )
    retry = summary.get("write_retry") or {}
    if retry.get("submit_total"):
        bullets.append(
            f"commit 提交：原始成功率 {retry.get('first_attempt_rate')} → "
            f"重试后 {retry.get('final_success_rate')}（重试耗尽失败 "
            f"{retry.get('retry_exhausted_failures')}）"
        )
    return bullets


def save_html(out_dir: Path, html_text: str) -> Path:
    path = out_dir / "report.html"
    path.write_text(html_text, encoding="utf-8")
    return path


def chart_series_from_metrics_csv(csv_path: Path) -> dict[str, list[tuple[float, float]]]:
    """Rebuild the report time-line series from ``metrics_samples.csv``.

    Matches ``monitor._value`` semantics: for each (ts, metric) the values
    across label sets are summed (``metrics_samples.csv`` is the flattened
    per-(ts, metric, labels) long format). The CPU series is derived from the
    ``echomem_process_cpu_seconds_total`` counter via frame deltas, same as
    ``monitor.cpu_utilization_series``.
    """
    if not csv_path.exists():
        return {}
    per_metric: dict[str, dict[float, float]] = {}
    with open(csv_path, encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if not row or row[0] == "ts":
                continue
            try:
                ts, metric, _labels, value = float(row[0]), row[1], row[2], float(row[3])
            except (ValueError, IndexError):
                continue
            frame = per_metric.setdefault(metric, {})
            frame[ts] = frame.get(ts, 0.0) + value

    def gauge(name: str) -> list[tuple[float, float]]:
        return sorted(per_metric.get(name, {}).items())

    cpu: list[tuple[float, float]] = []
    prev_ts: float | None = None
    prev_value: float | None = None
    for ts, value in gauge("echomem_process_cpu_seconds_total"):
        if prev_ts is not None and prev_value is not None and ts > prev_ts:
            span = ts - prev_ts
            if span > 0:
                cpu.append((ts, round(max(0.0, value - prev_value) / span * 100.0, 2)))
        prev_ts, prev_value = ts, value

    return {
        "rss_mb": [
            (ts, round(value / 1024 / 1024, 2))
            for ts, value in gauge("echomem_process_resident_memory_bytes")
        ],
        "threads": gauge("echomem_process_threads"),
        "commit_queue": gauge("echomem_session_commit_queue_depth"),
        "inflight": gauge("echomem_http_requests_inflight"),
        "cpu_percent": cpu,
    }


def _cpu_stats_from_csv(csv_path: Path, t0: float, t1: float) -> tuple[float | None, float | None]:
    """Recompute CPU mean/max percent over [t0, t1] from ``metrics_samples.csv``.

    Mirrors run-time ``monitor.counter_delta`` / ``cpu_utilization_series``:
    the ``echomem_process_cpu_seconds_total`` counter is summed across its
    ``mode=user/system`` label sets per frame; mean = counter delta ÷ wall ×
    100, max = max of per-frame utilization percentages. Returns (None, None)
    when the window or the series is missing.
    """
    if not csv_path.exists():
        return None, None
    counter: dict[float, float] = {}
    with open(csv_path, encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if not row or row[0] == "ts":
                continue
            if row[1] != "echomem_process_cpu_seconds_total":
                continue
            try:
                ts, value = float(row[0]), float(row[3])
            except (ValueError, IndexError):
                continue
            counter[ts] = counter.get(ts, 0.0) + value
    ts_values = sorted(counter.items())
    if not ts_values:
        return None, None
    before = next(((t, v) for t, v in ts_values if t0 <= t <= t1), None)
    if before is None:
        before = next(((t, v) for t, v in reversed(ts_values) if t <= t0), None)
    after = next(((t, v) for t, v in reversed(ts_values) if t <= t1), None)
    if before is None or after is None:
        return None, None
    wall = t1 - t0
    mean = round((after[1] - before[1]) / wall * 100.0, 2) if wall > 0 else None
    percents: list[float] = []
    prev_ts: float | None = None
    prev_value: float | None = None
    for ts, value in ts_values:
        if ts < t0 or ts > t1:
            continue
        if prev_ts is not None and prev_value is not None and ts > prev_ts:
            span = ts - prev_ts
            if span > 0:
                percents.append(max(0.0, value - prev_value) / span * 100.0)
        prev_ts, prev_value = ts, value
    maxp = round(max(percents), 2) if percents else None
    return mean, maxp


def _patch_cpu_from_csv(summary: dict[str, Any], csv_path: Path) -> None:
    """Backfill summary CPU stats from ``metrics_samples.csv``.

    Fixes runs whose ``summary.json`` recorded CPU 0/missing (e.g. produced
    before the ``echomem_process_cpu_seconds_total`` counter name was used).
    Idempotent: recomputes the same semantics as the run, so fresh runs are
    untouched.
    """
    scenes = summary.get("scenes") or {}
    windows = [
        (float(sc["window_s"][0]), float(sc["window_s"][1]))
        for sc in scenes.values()
        if sc.get("window_s")
    ]
    if not windows:
        return
    t0 = min(w[0] for w in windows)
    t1 = max(w[1] for w in windows)
    mean, maxp = _cpu_stats_from_csv(csv_path, t0, t1)
    if mean is None:
        return
    resources = summary.setdefault("resources", {})
    resources["cpu_util_mean_percent"] = mean
    if maxp is not None:
        resources["cpu_util_max_percent"] = maxp
    timeline = (
        ((summary.get("feature_verdicts") or {}).get("features") or {})
        .get("resource_timeline") or {}
    ).get("measurements")
    if isinstance(timeline, dict):
        timeline["cpu_util_mean_percent"] = mean
        if maxp is not None:
            timeline["cpu_util_max_percent"] = maxp


def regenerate_report(out_dir: Path) -> Path:
    """Rebuild ``report.html`` from an existing run's artifacts.

    Reads ``summary.json`` + ``metrics_samples.csv`` already written by a
    previous run, so an enhanced report can be produced without re-running
    the stress test. CPU summary stats are backfilled from the CSV so a
    report regenerated after a counter-name fix shows real CPU numbers.
    """
    out_dir = Path(out_dir)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    csv_path = out_dir / "metrics_samples.csv"
    chart_series = chart_series_from_metrics_csv(csv_path)
    _patch_cpu_from_csv(summary, csv_path)
    return save_html(out_dir, build_html(summary, chart_series))


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m performance.report <results_dir>")
        raise SystemExit(2)
    path = regenerate_report(Path(sys.argv[1]))
    print(f"report regenerated: {path}")
