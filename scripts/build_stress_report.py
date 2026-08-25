"""Build a self-contained HTML report from EchoMem stress-test artifacts."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def num(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):,.{digits}f}"


def metric_value(stage: dict[str, Any], key: str) -> Any:
    value: Any = stage
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def bar_chart(
    stages: list[dict[str, Any]],
    *,
    key: str,
    title: str,
    scale: float = 1.0,
    percent: bool = False,
) -> str:
    width, height = 760, 260
    left, bottom, top = 58, 220, 32
    values = [
        float(metric_value(stage, key) or 0) * scale
        for stage in stages
    ]
    maximum = max(values or [1.0])
    maximum = maximum or 1.0
    bars = []
    labels = []
    for index, (stage, value) in enumerate(zip(stages, values)):
        x = left + index * ((width - left - 24) / max(1, len(stages)))
        bar_width = max(24, (width - left - 48) / max(1, len(stages)) * 0.62)
        bar_height = (bottom - top) * value / maximum
        y = bottom - bar_height
        raw_value = metric_value(stage, key)
        label = pct(raw_value) if percent else num(raw_value)
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="3" fill="#2563eb"/>'
            f'<text x="{x + bar_width / 2:.1f}" y="{max(top - 4, y - 7):.1f}" '
            f'text-anchor="middle" class="value">{esc(label)}</text>'
        )
        labels.append(
            f'<text x="{x + bar_width / 2:.1f}" y="242" '
            f'text-anchor="middle" class="label">C{esc(stage.get("concurrency"))}</text>'
        )
    return f"""
    <div class="chart">
      <h3>{esc(title)}</h3>
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">
        <line x1="{left}" y1="{bottom}" x2="{width - 18}" y2="{bottom}" class="axis"/>
        <line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis"/>
        {''.join(bars)}
        {''.join(labels)}
      </svg>
    </div>
    """


def load_report_data(result_dir: Path) -> dict[str, Any]:
    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    workflows_path = result_dir / "client_results.json"
    payload = json.loads(workflows_path.read_text(encoding="utf-8"))
    summary["_workflows"] = payload.get("workflows", [])
    return summary


def build_report(data: dict[str, Any], source: Path) -> str:
    stages = data.get("stages", [])
    workflows = data.get("_workflows", [])
    total = len(workflows)
    failed = sum(row.get("result") != "ok" for row in workflows)
    accepted = sum(
        row.get("commit_status") in {200, 201, 202}
        for row in workflows
    )
    final_completed = sum(
        row.get("commit_poll_state") == "completed" for row in workflows
    )
    failure_details: dict[str, int] = {}
    for stage in stages:
        for detail, count in (stage.get("failure_details") or {}).items():
            failure_details[detail] = failure_details.get(detail, 0) + int(count)

    cards = "".join(
        f'<div class="card"><div class="k">{esc(label)}</div>'
        f'<div class="v">{esc(value)}</div></div>'
        for label, value in (
            ("总 workflow", total),
            ("commit 已接受", accepted),
            ("最终完成", f"{final_completed}/{accepted}" if accepted else "0/0"),
            ("workflow 成功率", pct((total - failed) / total if total else 0)),
        )
    )
    stage_rows = []
    for stage in stages:
        stage_rows.append(
            "<tr>"
            f"<td>C{esc(stage.get('concurrency'))}</td>"
            f"<td>{esc(stage.get('requested_workflows'))}</td>"
            f"<td>{esc(stage.get('accepted_commits'))}</td>"
            f"<td>{esc(stage.get('final_completed_commits'))}</td>"
            f"<td>{pct(stage.get('workflow_success_rate'))}</td>"
            f"<td>{num(stage.get('requests_completed_per_second'))}</td>"
            f"<td>{num((stage.get('workflow_latency_ms') or {}).get('p95'))} ms</td>"
            f"<td>{esc(stage.get('result_counts') or {})}</td>"
            "</tr>"
        )
    detail_rows = "".join(
        f"<tr><td>{esc(detail)}</td><td>{esc(count)}</td></tr>"
        for detail, count in sorted(
            failure_details.items(), key=lambda item: -item[1]
        )
    ) or "<tr><td colspan='2'>未发现结构化失败原因</td></tr>"
    config = json.dumps(data.get("config", {}), ensure_ascii=False, indent=2)
    conclusion = (
        "本轮压测的 HTTP 接收链路正常，但异步 commit 存在最终失败；"
        "应优先修复/配置 EchoMem 的下游 LLM，而不是把 202 视为成功。"
        if failed
        else "本轮未观察到 workflow 失败；仍需结合更高并发和服务端资源指标判断容量上限。"
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 压测报告 {esc(data.get("run_id"))}</title>
<style>
:root {{ color-scheme: light; --ink:#172033; --muted:#667085; --line:#e5e7eb;
  --blue:#2563eb; --bg:#f5f7fb; --red:#b42318; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif }}
main {{ max-width:1200px; margin:0 auto; padding:34px 24px 56px }}
h1 {{ margin:0 0 5px; font-size:28px }} h2 {{ margin:28px 0 12px; font-size:19px }}
h3 {{ margin:0 0 8px; font-size:15px }} .muted {{ color:var(--muted) }}
.cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:24px 0 }}
.card,.panel {{ background:white; border:1px solid var(--line); border-radius:8px }}
.card {{ padding:16px 18px }} .k {{ color:var(--muted); font-size:12px }}
.v {{ margin-top:4px; font-size:25px; font-weight:700 }}
.callout {{ border-left:4px solid var(--blue); background:#eef4ff; padding:14px 16px;
  border-radius:4px }} table {{ width:100%; border-collapse:collapse; background:white }}
th,td {{ padding:10px 12px; border-bottom:1px solid var(--line); text-align:left;
  vertical-align:top }} th {{ color:var(--muted); font-weight:600; font-size:12px }}
.charts {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px }}
.chart {{ padding:16px; background:white; border:1px solid var(--line); border-radius:8px }}
svg {{ width:100%; height:auto }} .axis {{ stroke:#cbd5e1; stroke-width:1 }}
.value {{ fill:var(--ink); font-size:12px }} .label {{ fill:var(--muted); font-size:12px }}
pre {{ overflow:auto; padding:14px; background:#111827; color:#d1d5db; border-radius:6px }}
@media(max-width:760px) {{ .cards,.charts {{ grid-template-columns:1fr 1fr }}
  main {{ padding:22px 14px }} table {{ font-size:12px }} th,td {{ padding:8px }} }}
</style></head><body><main>
<h1>EchoMem Incident API 压测报告</h1>
<div class="muted">run_id: <code>{esc(data.get("run_id"))}</code> ·
目标: <code>{esc(data.get("url"))}</code> · 结果目录: <code>{esc(source)}</code></div>
<div class="cards">{cards}</div>
<div class="callout"><strong>结论：</strong>{esc(conclusion)}</div>
<h2>阶段指标</h2><div class="panel"><table><thead><tr>
<th>并发</th><th>请求数</th><th>接受</th><th>最终完成</th><th>成功率</th>
<th>提交吞吐</th><th>workflow P95</th><th>结果分类</th></tr></thead>
<tbody>{''.join(stage_rows)}</tbody></table></div>
<h2>可视化</h2><div class="charts">
{bar_chart(stages,key="workflow_success_rate",title="Workflow 成功率",scale=1,percent=True)}
{bar_chart(stages,key="final_completed_commits",title="最终完成 commit 数")}
{bar_chart(stages,key="requests_completed_per_second",title="请求完成吞吐 workflows/s")}
{bar_chart(stages,key="workflow_latency_ms.p95",title="Workflow 延迟 P95 (ms)")}
</div>
<h2>失败根因</h2><div class="panel"><table><thead><tr><th>原因</th><th>次数</th></tr>
</thead><tbody>{detail_rows}</tbody></table></div>
<h2>运行配置</h2><pre>{esc(config)}</pre>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = load_report_data(args.result_dir.expanduser().resolve())
    report = build_report(data, args.result_dir.expanduser().resolve())
    args.output.expanduser().resolve().write_text(report, encoding="utf-8")
    print(args.output.expanduser().resolve())


if __name__ == "__main__":
    main()
