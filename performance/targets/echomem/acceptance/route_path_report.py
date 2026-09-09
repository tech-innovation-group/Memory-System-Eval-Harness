"""Render measured Search paths without inferring individual stage durations."""

from html import escape
import argparse
import json
import math
from pathlib import Path


def render_route_paths(summaries: list[tuple[str, dict]]) -> str:
    labels = {"fast_path": "快速路径（未调用意图 LLM）", "intent_llm": "意图 LLM 路径",
              "unobserved": "路由层未观测"}

    def number(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))

    def fmt(value):
        if not number(value):
            return "未采集"
        return f"{value:.3f}" if isinstance(value, float) else str(value)

    charts, rows = [], []
    for context, summary in summaries:
        paths = summary.get("route_path_timings") or {}
        if not paths:
            rows.append(f'<tr><td>{escape(context)}</td><td colspan="14">'
                        f'旧汇总缺少路径统计；已发 {fmt(summary.get("sent"))}，需要原始请求重新汇总。</td></tr>')
            continue
        ceiling = max((t.get("p95_s") for t in paths.values()
                       if number(t.get("p95_s"))), default=0) or 1
        bars = []
        for key, label in labels.items():
            timing = paths.get(key, {})
            fraction = timing.get("fraction_of_sent")
            cells = [escape(context), label, fmt(timing.get("observations")),
                     f'{100*fraction:.1f}%' if number(fraction) else "未采集"]
            cells += [fmt(timing.get(field)) for field in (
                "latency_observations", "latency_missing_or_invalid", "mean_s", "p50_s",
                "p95_s", "min_s", "max_s", "errors", "transport_or_http_errors", "degraded")]
            cells.append(fmt(summary.get("atomic_p95_s")))
            rows.append('<tr>' + ''.join(
                f'<td style="min-width:{180 if index == 0 else 160 if index == 1 else 70}px">{cell}</td>'
                for index, cell in enumerate(cells)) + '</tr>')
            value = timing.get("p95_s")
            color = {"fast_path": "#287c70", "intent_llm": "#b34b38", "unobserved": "#66747d"}[key]
            width = 100 * max(0, value) / ceiling if number(value) else 0
            bars.append(f'<div style="margin:10px 0"><span>{label}: '
                        f'{fmt(value)} s · n={fmt(timing.get("observations"))}</span>'
                        f'<div style="height:12px;background:#e0e6e8">'
                        f'<div style="height:100%;width:{width:.2f}%;background:{color}"></div></div></div>')
        charts.append(f'<div style="min-width:0"><h4>{escape(context)}</h4>{"".join(bars)}</div>')
    headers = ["窗口 / 身份", "路径", "已发样本", "占全部已发", "有计时", "缺失/无效计时",
               "平均 s", "P50 s", "P95 s", "最小 s", "最大 s", "严格无效", "HTTP/传输错误", "降级", "整窗口 Atomic P95 s"]
    body = ''.join(rows) or '<tr><td colspan="15">尚无 Search 样本。</td></tr>'
    return ('<details><summary>Search 路由路径延迟拆解</summary>'
            '<p>按 executed_layers 分组，展示整条 Search 耗时；这不是 LLM 独立阶段计时。'
            '失败请求保留在分母，缺失计时单列；P95 使用 nearest-rank。'
            'Atomic 列为该窗口所有路径的引擎统计，不与路径 P95 相加。各窗口柱图独立缩放。</p>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:24px">'
            + ''.join(charts) + '</div><div class="scroll"><table><thead><tr>'
            + ''.join(f'<th>{h}</th>' for h in headers) + '</tr></thead><tbody>'
            + body + '</tbody></table></div></details>')


def publish_route_paths(measurement: dict, output: Path, label: str) -> dict:
    """Publish only aggregate numbers; raw responses and identities stay private."""
    from performance.targets.echomem.acceptance.capacity_statistics import search_summary

    reads = [row for row in measurement.get("rows", []) if row.get("op") == "read"]
    summary = search_summary(reads)
    public = {key: summary[key] for key in (
        "planned", "sent", "not_sent", "success", "errors", "mean_s", "p50_s", "p95_s",
        "latency_observations", "latency_missing_or_invalid",
        "atomic_p95_s", "atomic_observations", "route_path_timings")}
    public["percentile_method"] = "nearest-rank"
    output.parent.mkdir(parents=True, exist_ok=True)
    data_path = output.with_suffix(".json")
    if data_path == output:
        raise ValueError("output must be an HTML path")
    data_path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    content = render_route_paths([(label, public)]).replace('<details>', '<details open>', 1)
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Search 路径延迟实测</title>
<style>*{{box-sizing:border-box}}body{{margin:0;font:15px/1.7 system-ui,sans-serif;color:#26343a;background:#f5f7f8;letter-spacing:0}}
main{{max-width:1320px;margin:auto;padding:24px}}h1{{font-size:26px}}summary{{font-size:20px;font-weight:600;cursor:pointer}}
.scroll{{overflow:auto;margin-top:24px}}table{{width:100%;border-collapse:collapse;background:white;font-size:13px}}
th,td{{padding:10px;border-bottom:1px solid #dae1e5;text-align:left}}th{{white-space:nowrap;background:#e7edef}}
p,h4{{overflow-wrap:anywhere}}a{{color:#186b91}}@media(max-width:600px){{main{{padding:14px}}}}
</style></head><body><main><h1>Search 路径延迟实测</h1>
<p>{escape(label)}；计划 {public['planned']}，已发 {public['sent']}，严格有效 {public['success']}，
严格无效 {public['errors']}。仅重新汇总已有请求，没有启动新负载。</p>
{content}<p><a href="{escape(data_path.name, quote=True)}">下载脱敏统计 JSON</a></p></main></body></html>'''
    output.write_text(document, encoding="utf-8")
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurement", type=Path, help="Private measurement JSON containing rows")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="Search 测量窗口")
    args = parser.parse_args()
    if args.measurement.resolve() in {args.output.resolve(), args.output.with_suffix('.json').resolve()}:
        parser.error("output must not overwrite input evidence")
    publish_route_paths(json.loads(args.measurement.read_text(encoding="utf-8")), args.output, args.label)


if __name__ == "__main__":
    main()
