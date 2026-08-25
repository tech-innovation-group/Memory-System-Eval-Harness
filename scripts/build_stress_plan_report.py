"""Build a combined HTML review of the PR345 stress plan and real runs."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def load(path: Path) -> dict[str, Any]:
    return json.loads((path / "summary.json").read_text(encoding="utf-8"))


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}%" if d else "n/a"


def num(value: Any, digits: int = 1) -> str:
    return "n/a" if value is None else f"{float(value):,.{digits}f}"


def svg_bars(
    items: list[tuple[str, float]],
    *,
    title: str,
    suffix: str = "",
    maximum: float | None = None,
) -> str:
    width, height = 780, 270
    left, bottom, top = 58, 225, 32
    maximum = maximum or max([value for _, value in items] or [1.0])
    maximum = max(maximum, 1.0)
    slot = (width - left - 24) / max(len(items), 1)
    bars: list[str] = []
    for index, (label, value) in enumerate(items):
        x = left + index * slot + slot * 0.18
        bar_width = slot * 0.62
        bar_height = (bottom - top) * value / maximum
        y = bottom - bar_height
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="3" fill="#2563eb"/>'
            f'<text x="{x + bar_width / 2:.1f}" y="{max(top - 5, y - 7):.1f}" '
            f'text-anchor="middle" class="value">{esc(num(value))}{esc(suffix)}</text>'
            f'<text x="{x + bar_width / 2:.1f}" y="247" '
            f'text-anchor="middle" class="label">{esc(label)}</text>'
        )
    return f"""
    <div class="chart"><h3>{esc(title)}</h3>
    <svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">
      <line x1="{left}" y1="{bottom}" x2="{width - 18}" y2="{bottom}" class="axis"/>
      <line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis"/>
      {''.join(bars)}
    </svg></div>
    """


def build_report(
    single: dict[str, Any],
    multi: dict[str, Any],
    open_loop: dict[str, Any],
) -> str:
    single_stages = single["stages"]
    multi_stages = multi["stages"]
    open_stages = open_loop["stages"]
    single_total = sum(int(stage["requested_workflows"]) for stage in single_stages)
    single_done = sum(int(stage["final_completed_commits"] or 0) for stage in single_stages)
    single_failed = sum(int(stage["failed_workflows"]) for stage in single_stages)
    multi_total = sum(int(stage["requested_workflows"]) for stage in multi_stages)
    multi_done = sum(int(stage["final_completed_commits"] or 0) for stage in multi_stages)
    open_total = sum(int(stage["requested_workflows"]) for stage in open_stages)
    open_done = sum(int(stage["final_completed_commits"] or 0) for stage in open_stages)

    multi_tenants = multi.get("overall_tenant_metrics", {})
    tenant_rows = "".join(
        f"<tr><td>{esc(name)}</td><td>{m['workflows']}</td>"
        f"<td>{m['final_completed_commits']}</td><td>{m['failed_workflows']}</td>"
        f"<td>{num((m.get('completion_latency_ms') or {}).get('p95'))} ms</td></tr>"
        for name, m in sorted(multi_tenants.items())
    )
    single_rows = "".join(
        f"<tr><td>C{stage['concurrency']}</td><td>{stage['requested_workflows']}</td>"
        f"<td>{stage['final_completed_commits']}</td><td>{stage['failed_workflows']}</td>"
        f"<td>{esc(stage.get('failure_details') or {})}</td>"
        f"<td>{num((stage.get('commit_completion_latency_ms') or {}).get('p95'))} ms</td></tr>"
        for stage in single_stages
    )
    open_rows = "".join(
        f"<tr><td>{stage.get('target_arrival_rate')}/s</td>"
        f"<td>{stage['requested_workflows']}</td><td>{stage['final_completed_commits']}</td>"
        f"<td>{pct(stage['final_completed_commits'], stage['accepted_commits'])}</td>"
        f"<td>{num((stage.get('arrival_lag_ms') or {}).get('p95'))} ms</td>"
        f"<td>{num((stage.get('commit_completion_latency_ms') or {}).get('p95'))} ms</td></tr>"
        for stage in open_stages
    )
    single_chart = svg_bars(
        [(f"C{s['concurrency']}", float(s["failed_workflows"])) for s in single_stages],
        title="单租户各并发档位失败数",
        maximum=max([float(s["requested_workflows"]) for s in single_stages] or [1]),
    )
    completion_chart = svg_bars(
        [
            (f"C{s['concurrency']}", float((s["commit_completion_latency_ms"] or {}).get("p95") or 0))
            for s in single_stages
        ],
        title="单租户 commit 完成延迟 P95",
        suffix=" ms",
    )
    open_chart = svg_bars(
        [
            (f"{s['target_arrival_rate']}/s", float((s["commit_completion_latency_ms"] or {}).get("p95") or 0))
            for s in open_stages
        ],
        title="固定到达率下 commit 完成延迟 P95",
        suffix=" ms",
    )
    tenant_chart = svg_bars(
        [
            (name, float((m.get("completion_latency_ms") or {}).get("p95") or 0))
            for name, m in sorted(multi_tenants.items())
        ],
        title="多租户完成延迟 P95",
        suffix=" ms",
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PR345 压测方案实测评估</title>
<style>
:root {{ --ink:#172033; --muted:#667085; --line:#e5e7eb; --bg:#f5f7fb;
--blue:#2563eb; --green:#147a52; --amber:#9a6700; --red:#b42318; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif }}
main {{ max-width:1200px; margin:auto; padding:30px 22px 60px }}
h1 {{ margin:0 0 5px; font-size:28px }} h2 {{ margin:28px 0 12px; font-size:19px }}
h3 {{ margin:0 0 8px; font-size:15px }} .muted {{ color:var(--muted) }}
.callout,.panel,.chart,.card {{ background:#fff; border:1px solid var(--line); border-radius:8px }}
.callout {{ padding:15px 17px; border-left:4px solid var(--blue); margin:20px 0 }}
.callout.warn {{ border-left-color:var(--amber); background:#fffaf0 }}
.cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:22px 0 }}
.card {{ padding:15px 17px }} .k {{ color:var(--muted); font-size:12px }}
.v {{ font-size:24px; font-weight:700; margin-top:4px }}
table {{ width:100%; border-collapse:collapse }} th,td {{ text-align:left; padding:9px 11px;
border-bottom:1px solid var(--line); vertical-align:top }} th {{ color:var(--muted);
font-size:12px; font-weight:600 }} .charts {{ display:grid; grid-template-columns:1fr 1fr;
gap:14px }} .chart {{ padding:15px }} svg {{ width:100%; height:auto }}
.axis {{ stroke:#cbd5e1; stroke-width:1 }} .value {{ fill:var(--ink); font-size:12px }}
.label {{ fill:var(--muted); font-size:12px }} ul {{ margin:8px 0 8px 20px }}
code {{ background:#f1f3f5; padding:1px 4px; border-radius:3px }}
@media(max-width:760px) {{ .cards,.charts {{ grid-template-columns:1fr 1fr }}
main {{ padding:20px 12px }} table {{ font-size:12px }} th,td {{ padding:7px }} }}
</style></head><body><main>
<h1>PR345 压测方案实测评估</h1>
<div class="muted">基于 PR345 方案与当前 PR #12 压测脚本，实测日期：2026-08-25</div>
<div class="cards">
<div class="card"><div class="k">单租户 workflow</div><div class="v">{single_total}</div></div>
<div class="card"><div class="k">单租户最终完成</div><div class="v">{single_done}/{single_total}</div></div>
<div class="card"><div class="k">多租户最终完成</div><div class="v">{multi_done}/{multi_total}</div></div>
<div class="card"><div class="k">Open-loop 最终完成</div><div class="v">{open_done}/{open_total}</div></div>
</div>
<div class="callout"><strong>总体结论：</strong>
当前方案已经能区分请求接收、窗口内完成和 drain 后最终完成，并能覆盖闭环、 多租户与固定到达率。
本次实测中，四租户和 open-loop 场景均未出现 429 或最终失败；单租户 320 个 workflow 中
317 个最终完成，3 个失败，失败来自 embedding admission 超时和一次 atomic extraction window。
因此当前瓶颈更像是 provider admission / 异步处理长尾，而不是已被证明的全局队列溢出。</div>
<div class="callout warn"><strong>方案仍有缺口：</strong>
当前脚本没有采集服务端 queue depth、CPU/RSS、event-loop block、provider 分阶段指标到统一结果目录；
本次只能用 EchoMem JSONL 做事后解释，尚不能证明 PR345 报告中关于 GIL 或共享 event loop 的具体根因。</div>

<h2>一、单租户饱和实测</h2>
<div class="panel"><table><thead><tr><th>并发</th><th>请求数</th><th>最终完成</th>
<th>失败</th><th>失败原因</th><th>完成 P95</th></tr></thead><tbody>{single_rows}</tbody></table></div>
<div class="charts">{single_chart}{completion_chart}</div>
<p>单租户结果：{single_done}/{single_total} 完成，失败 {single_failed} 个。
没有观察到 HTTP 429，说明本次 80 workflow/阶段的提交方式没有触发租户配额拒绝；
但高并发和 provider admission 仍造成异步失败与明显长尾。</p>

<h2>二、多租户公平性实测</h2>
<div class="panel"><table><thead><tr><th>租户</th><th>workflow</th><th>完成</th>
<th>失败</th><th>完成 P95</th></tr></thead><tbody>{tenant_rows}</tbody></table></div>
<div class="charts">{tenant_chart}</div>
<p>四个租户各 40 个 workflow，全部完成，P95 约
{num(max((m.get('completion_latency_ms') or {}).get('p95', 0) for m in multi_tenants.values()))} ms
以内，当前样本下没有明显租户倾斜。但这只证明本轮负载下的公平性，不能证明全局队列上限 256。</p>

<h2>三、Open-loop 固定到达率实测</h2>
<div class="panel"><table><thead><tr><th>目标到达率</th><th>请求数</th><th>最终完成</th>
<th>最终完成率</th><th>到达延迟 P95</th><th>完成 P95</th></tr></thead>
<tbody>{open_rows}</tbody></table></div>
<div class="charts">{open_chart}</div>
<p>5、10、20 workflows/s 均能按目标速率发起，arrival lag P95 约 1.3ms 以内，
且 300/300 commit 最终完成。当前服务在 20/s 下未出现明显拒绝，但完成延迟 P95
从约 19.8s 增至约 33.8s，说明队列压力已经反映在异步长尾上。</p>

<h2>四、方案评估结论</h2>
<div class="panel"><table><thead><tr><th>方案能力</th><th>本次状态</th><th>结论</th></tr></thead>
<tbody>
<tr><td>窗口完成与最终 drain 分离</td><td>已验证</td><td>必要，避免把 202 或轮询超时当最终成功/失败。</td></tr>
<tr><td>多租户公平性</td><td>已补测</td><td>本轮 4 租户无失败、无明显 P95 差异。</td></tr>
<tr><td>固定到达率</td><td>已补测</td><td>20/s 仍能排空，但延迟长尾上升。</td></tr>
<tr><td>全局队列 256 饱和点</td><td>未证明</td><td>需更大总在途量和服务端 queue depth 采集。</td></tr>
<tr><td>自动 commit 阈值路径</td><td>未覆盖</td><td>本次仍是显式 commit，需单独跑 auto 模式。</td></tr>
<tr><td>GIL/event loop 根因</td><td>未证明</td><td>只能表述为共享串行/provider admission 瓶颈候选。</td></tr>
</tbody></table></div>
<h2>五、下一步建议</h2>
<ul>
<li>增加服务端 queue depth、active workers、provider admission wait、CPU/RSS 采集。</li>
<li>单独执行 <code>--commit-mode auto --messages-per-session 4</code>，验证自动 commit。</li>
<li>把 20/s 继续提升到 40/s、80/s，并延长 steady-state duration，寻找真正饱和点。</li>
<li>对 provider admission 超时增加错误分类和可恢复重试统计。</li>
</ul>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", type=Path, required=True)
    parser.add_argument("--multi", type=Path, required=True)
    parser.add_argument("--open-loop", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(load(args.single), load(args.multi), load(args.open_loop))
    args.output.expanduser().resolve().write_text(report, encoding="utf-8")
    print(args.output.expanduser().resolve())


if __name__ == "__main__":
    main()
