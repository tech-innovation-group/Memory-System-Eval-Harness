#!/usr/bin/env python3
from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_structure_30paper_roadmap_20260617.html"
)


@dataclass(frozen=True)
class Paper:
    idx: int
    title: str
    venue_year: str
    link: str
    axis: str
    module: str
    current_status: str
    next_step: str


PAPERS: list[Paper] = [
    Paper(1, "LoCoMo", "ACL 2024", "https://arxiv.org/abs/2402.17753", "benchmark pressure", "temporal/query_resolver.py", "partial", "把 relative time 从 metadata 升级成显式时间推理对象"),
    Paper(2, "LongMemEval", "ICLR 2025", "https://arxiv.org/abs/2410.10813", "benchmark pressure", "search_service.py + query_planner.py", "partial", "把 retrieval plan 从关键词路由升级为 evidence-contract 驱动"),
    Paper(3, "LongMemEval-V2", "2026", "https://arxiv.org/abs/2605.12493", "benchmark pressure", "planner / readiness", "partial", "引入更真实的 task-facing readiness / usability logging"),
    Paper(4, "Regimes", "2026", "https://arxiv.org/abs/2606.10241", "evaluation loop", "paper/eval harness", "missing", "增加 held-out gated improvement loop"),
    Paper(5, "When Stored Evidence Stops Being Usable", "2026", "https://arxiv.org/abs/2605.07313", "evaluation pressure", "self_check.py", "partial", "从“存储是否成功”转向“证据是否可用”评估"),
    Paper(6, "WhenLoss", "2026", "https://arxiv.org/abs/2605.24579", "diagnostics", "ingest + search split", "partial", "把写入失败和检索失败拆开记录"),
    Paper(7, "RAPTOR", "2024", "https://arxiv.org/abs/2401.18059", "hierarchy", "organized_projector.py", "partial", "把 temporal tree / overview 继续做成稳定层级"),
    Paper(8, "MemoRAG", "2024", "https://arxiv.org/abs/2409.05591", "coarse-to-fine", "search_service.py", "partial", "让 summary/dossier 真正充当 coarse guide 而不是附带读物"),
    Paper(9, "GraphReader", "2024", "https://arxiv.org/abs/2406.14550", "graph exploration", "graph_seed_planner.py", "partial", "把 seed -> path -> verify 做成 staged 图探索"),
    Paper(10, "ByteRover", "2026", "https://arxiv.org/abs/2604.01599", "hierarchical memory", "overall architecture", "partial", "把层级结构正式定义成 system contract"),
    Paper(11, "TiMem", "2026", "https://arxiv.org/abs/2601.02845", "temporal consolidation", "organized_projector.py", "partial", "按事件时间而不是写入时间组织中层"),
    Paper(12, "Hierarchical Memory for High-Efficiency Long-Term Reasoning", "2025", "https://arxiv.org/abs/2507.22925", "hierarchy", "search_service.py", "partial", "优化 coarse-to-fine route planning"),
    Paper(13, "HippoRAG", "NeurIPS 2024", "https://arxiv.org/abs/2405.14831", "graph recall", "graph_seed_planner.py + search_service.py", "partial", "把 graph recall 前移成主 backbone"),
    Paper(14, "From RAG to Memory", "2025", "https://arxiv.org/abs/2502.14802", "continual memory", "AtomFirstPipeline", "partial", "把 commit 后的 memory evolution 做成持续过程"),
    Paper(15, "Zep", "2025", "https://arxiv.org/abs/2501.13956", "temporal KG", "graph/sync.py", "partial", "增强 temporal graph 的显式时间边与状态更新"),
    Paper(16, "LEGO-GraphRAG", "2024", "https://arxiv.org/abs/2411.05844", "modular graph", "graph_seed_planner.py", "partial", "图检索组件化，seed/rerank/read 解耦"),
    Paper(17, "H-Mem", "2026", "https://arxiv.org/abs/2605.15701", "hybrid tree+graph", "overall architecture", "partial", "把 tree + graph 的混合检索正式写进系统契约"),
    Paper(18, "APEX-MEM", "2026", "https://arxiv.org/abs/2604.14362", "semi-structured temporal reasoning", "query_planner.py", "partial", "把 temporal-relational family 单独建模"),
    Paper(19, "Mem0", "2025", "https://arxiv.org/abs/2504.19413", "memory lifecycle", "AtomFirstPipeline", "partial", "拆分 hot-path ingest 与 cold-path consolidation"),
    Paper(20, "LightMem", "2025", "https://arxiv.org/abs/2510.18866", "light/heavy split", "AtomFirstPipeline", "partial", "导入快路径和整理慢路径分离"),
    Paper(21, "MemOS", "2025", "https://arxiv.org/abs/2505.22101", "memory OS", "overall architecture", "partial", "把记忆层做成 governed resource 而不是散模块"),
    Paper(22, "Infini Memory", "2026", "https://arxiv.org/abs/2606.10677", "topic documents", "topic_dossier", "partial", "把 topic_dossier 从浅分桶升级为可维护主题文档"),
    Paper(23, "AgentIR", "2026", "https://arxiv.org/abs/2605.25092", "cascade retrieval", "search_service.py", "partial", "工作负载自适应 cascade 检索"),
    Paper(24, "ConvMemory", "2026", "https://arxiv.org/abs/2605.28062", "reranking/conflict edit", "evidence_contract.py", "missing", "加入 learned rerank / conflict repair 层"),
    Paper(25, "MIRIX", "2025", "https://arxiv.org/abs/2507.07957", "typed multimodal memory", "graph/sync.py", "partial", "把 image_evidence 做成一等公民"),
    Paper(26, "Mem-T", "2026", "https://arxiv.org/abs/2601.23014", "memory policy", "self_check.py", "missing", "把 retrieval policy 的回报信号记录下来"),
    Paper(27, "E-mem", "2026", "https://arxiv.org/abs/2601.21714", "episodic reconstruction", "episode retriever", "partial", "加强 episode 不是只保留外壳"),
    Paper(28, "D-MEM", "2026", "https://arxiv.org/abs/2603.14597", "reward-routed memory", "planner/policy", "missing", "增加 importance / reward-sensitive retention"),
    Paper(29, "Field-Theoretic Memory", "2026", "https://arxiv.org/abs/2602.21220", "continuous memory", "future direction", "missing", "作为对照设计，不急着直接工程化"),
    Paper(30, "Self-RAG", "2023 foundational", "https://openreview.net/forum?id=hSyW5go0v8", "self-reflection", "self_check.py + second pass", "partial", "把 advisory self-check 升级成真正可执行的检索反思"),
]


def esc(text: str) -> str:
    return html.escape(str(text))


def badge(status: str) -> str:
    css = {
        "already strong": "good",
        "partial": "warn",
        "missing": "bad",
    }.get(status, "warn")
    return f'<span class="badge {css}">{esc(status)}</span>'


def section_rows(axis: str) -> str:
    rows = []
    for paper in [p for p in PAPERS if p.axis == axis]:
        rows.append(
            "<tr>"
            f"<td>{paper.idx}</td>"
            f"<td><a href=\"{esc(paper.link)}\" target=\"_blank\" rel=\"noreferrer\">{esc(paper.title)}</a><br /><span class=\"muted\">{esc(paper.venue_year)}</span></td>"
            f"<td><code>{esc(paper.module)}</code></td>"
            f"<td>{badge(paper.current_status)}</td>"
            f"<td>{esc(paper.next_step)}</td>"
            "</tr>"
        )
    return "".join(rows)


def render() -> str:
    axes = [
        "benchmark pressure",
        "evaluation loop",
        "evaluation pressure",
        "diagnostics",
        "hierarchy",
        "coarse-to-fine",
        "graph exploration",
        "hierarchical memory",
        "temporal consolidation",
        "graph recall",
        "continual memory",
        "temporal KG",
        "modular graph",
        "hybrid tree+graph",
        "semi-structured temporal reasoning",
        "memory lifecycle",
        "light/heavy split",
        "memory OS",
        "topic documents",
        "cascade retrieval",
        "reranking/conflict edit",
        "typed multimodal memory",
        "memory policy",
        "episodic reconstruction",
        "reward-routed memory",
        "continuous memory",
        "self-reflection",
    ]
    grouped = []
    for axis in axes:
        subset = [p for p in PAPERS if p.axis == axis]
        if not subset:
            continue
        grouped.append(
            f"""
            <section class="card">
              <h2>{esc(axis)}</h2>
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Paper</th>
                    <th>Closest Code</th>
                    <th>Current</th>
                    <th>Best Generic Next Step</th>
                  </tr>
                </thead>
                <tbody>{section_rows(axis)}</tbody>
              </table>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory 30-Paper Structure Roadmap</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#142033; --muted:#627086; --line:#d9e2ee;
      --blue:#2563eb; --green:#0f766e; --amber:#b45309; --red:#b42318;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1260px; margin:0 auto; padding:28px 20px 48px; }}
    .hero,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px 22px; margin-bottom:16px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .kpi {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi > div {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:#fbfcfe; }}
    .kpi strong {{ display:block; font-size:22px; margin-bottom:4px; }}
    h1,h2,h3 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:28px; }}
    h2 {{ font-size:19px; }}
    p,li,td,th {{ font-size:14px; }}
    .muted {{ color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); padding:10px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#fafbfc; color:var(--muted); font-size:12px; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; background:#f2f4f8; padding:1px 4px; border-radius:4px; font-size:12px; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; border:1px solid var(--line); }}
    .good {{ color:var(--green); background:#ecfdf5; }}
    .warn {{ color:var(--amber); background:#fff7ed; }}
    .bad {{ color:var(--red); background:#fff1f2; }}
    .note {{ padding:12px 14px; border-radius:8px; background:#f7fbff; border:1px solid #dbeafe; }}
    @media (max-width: 960px) {{ .grid,.kpi {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory 结构分析 + 30 篇论文改进路线</h1>
      <p class="muted">
        这页不是泛泛的 related work，而是把近两年 30 篇高相关论文，直接映射到
        <code>echomem</code> 当前代码结构上：哪些已经有雏形，哪些只是半成品，下一步最值钱的通用改法是什么。
      </p>
      <div class="kpi">
        <div><strong>30</strong><span class="muted">papers mapped to code</span></div>
        <div><strong>7</strong><span class="muted">关键代码锚点：atom / dossier / tree / graph / planner / contract / self-check</span></div>
        <div><strong>5</strong><span class="muted">最重要结构方向：time, topic, graph, policy, lifecycle</span></div>
        <div><strong>0</strong><span class="muted">不建议的数据集关键词 hack</span></div>
      </div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>当前 echomem 骨架</h2>
        <ul>
          <li><code>AtomFirstPipeline</code>: 把消息流转成原子记忆。</li>
          <li><code>OrganizedProjector</code>: 生成 <code>profile / overview / topic_dossier / events / temporal_tree</code>。</li>
          <li><code>MemoryGraphSync</code>: 维护 <code>atom/fact/event/entity</code> 图。</li>
          <li><code>QueryPlanner</code>: 做 temporal / relational / longitudinal / visual family 路由。</li>
          <li><code>evidence_contract + self_check</code>: 做答前证据覆盖检查。</li>
        </ul>
      </div>
      <div class="card">
        <h2>最大结构缺口</h2>
        <ul>
          <li><strong>时间：</strong><code>event_time / mention_time / write_time</code> 已有，但还不是强推理对象。</li>
          <li><strong>主题：</strong><code>topic_dossier</code> 现在更像浅分桶，不像稳定主题文档。</li>
          <li><strong>图：</strong>graph recall 已接入，但 seed/path/rerank 仍偏启发式。</li>
          <li><strong>策略：</strong><code>self_check</code> 仍偏 advisory，不够强执行。</li>
          <li><strong>生命周期：</strong>hot-path ingest 和 cold-path consolidation 还不够彻底分离。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>我认为最值得优先改的顺序</h2>
      <ol>
        <li>把 <code>self_check</code> 变成真正可执行的 targeted re-retrieve + abstain gate。</li>
        <li>把 <code>topic_dossier</code> 从 subject/object 浅分桶升级为可维护主题文档。</li>
        <li>把 temporal tree 升级为支持 interval / relative-time / temporal-relation 的时间结构。</li>
        <li>把 graph retrieval 前移成 backbone，而不是 sparse fallback。</li>
        <li>增加 lifecycle / usability / readiness 这类真实系统评测，而不只看答对率。</li>
      </ol>
      <div class="note">
        这五步都可以做成<strong>通用结构改进</strong>，不需要针对 LoCoMo 或某个数据集加关键词表。
      </div>
    </section>

    <section class="card">
      <h2>Nano 实现怎么理解</h2>
      <ul>
        <li>最小参考实现：<code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14.py</code></li>
        <li>双 backbone toy benchmark：<code>nano_dual_backbone_benchmark.py</code></li>
        <li>显式 planner ablation：<code>nano_explicit_planner_ablation.py</code></li>
        <li>topic dossier 通用性 benchmark：<code>nano_topic_dossier_generalization_benchmark.py</code></li>
        <li>paraphrase robustness benchmark：<code>nano_reference_impl_v14_paraphrase_benchmark.py</code></li>
      </ul>
      <p class="muted">
        如果你只想先理解方法，不看主仓复杂工程，先从 <code>nano_reference_impl_v14.py</code> 和上面四个小实验走，会清楚很多。
      </p>
    </section>

    {''.join(grouped)}

    <section class="card">
      <h2>论文推进建议</h2>
      <ul>
        <li><strong>主稿主线：</strong>不要写成“大而全记忆系统”，而是写成 <code>contract-driven dual-backbone long-horizon memory</code>。</li>
        <li><strong>最强 claim：</strong>机制级别已经能成立，尤其是 time / graph / dossier / readiness / self-check 这几条。</li>
        <li><strong>当前缺口：</strong>真实 benchmark 规模还不够，multimodal 的大规模证据也不够。</li>
        <li><strong>最适合 CVPR 的角度：</strong>如果继续走 CVPR，建议把 visual grounding + temporal memory + graph evidence 的交叉点讲得更强。</li>
      </ul>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    OUT_HTML.write_text(render(), encoding="utf-8")
    print(str(OUT_HTML))


if __name__ == "__main__":
    main()
