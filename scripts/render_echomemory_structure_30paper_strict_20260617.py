#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path


ROOT = Path("/Users/chx/locomo-eval-web")
OUT = (
    ROOT
    / "web"
    / "static"
    / "generated-reports"
    / "echomemory_structure_30paper_strict_20260617.html"
)


@dataclass(frozen=True)
class Paper:
    idx: int
    title: str
    bucket: str
    source_quality: str
    source: str
    link: str
    echo_layer: str
    current_truth: str
    next_change: str


PAPERS: list[Paper] = [
    Paper(1, "LoCoMo", "Benchmark pressure", "official benchmark / ACL", "ACL 2024", "https://aclanthology.org/2024.acl-long.747/", "temporal/query_resolver.py + search_service.py", "已经区分 temporal / relational / longitudinal family，并显式处理 query_time_anchor。", "把时间题从“命中一段时间文本”升级成 story-time / mention-time / write-time 仲裁。"),
    Paper(2, "LongMemEval", "Benchmark pressure", "official benchmark / ICLR", "ICLR 2025", "https://openreview.net/forum?id=pZiyCaVuti", "self_check.py + evidence_contract.py + search_service.py", "已有 evidence contract、自检与 second pass 雏形。", "把自检从诊断层推成执行层，支持 expand / abstain / defer。"),
    Paper(3, "LongMemEval-V2", "Benchmark pressure", "preprint benchmark line", "2026", "https://arxiv.org/abs/2605.12493", "readiness.py + session_service.py", "已有 qa_ready / organized_ready / core_ready。", "把 readiness 从流水线完成信号升级成“当前题型可答”的可验证状态。"),
    Paper(4, "When Stored Evidence Stops Being Usable", "Benchmark pressure", "preprint analysis", "2026", "https://arxiv.org/abs/2605.07313", "self_check.py", "已经能记录 coverage_ratio、missing_types、event_time/path_grounding 缺口。", "新增 evidence usability audit，定位“写进去了但仍答错”的具体断点。"),
    Paper(5, "WhenLoss", "Benchmark pressure", "preprint analysis", "2026", "https://arxiv.org/abs/2605.24579", "atom_first_pipeline.py + search_service.py", "导入与检索已有分开的日志入口。", "把 formation failure、retrieval failure、answer-time failure 明确拆开。"),

    Paper(6, "TiMem", "Temporal semantics", "preprint", "2026", "https://arxiv.org/abs/2601.02845", "organized_projector/projector.py + temporal/query_resolver.py", "event_time 已流入 organized event 与 temporal tree block。", "按事件发生时间重整中层结构，而不是只按写入/提及时间落桶。"),
    Paper(7, "Evaluating Very Long-Term Conversational Memory of LLM Agents", "Temporal semantics", "benchmark paper / ACL-adjacent pressure", "2024", "https://arxiv.org/abs/2402.17753", "temporal/query_resolver.py", "已支持 yesterday / last week / this month 等相对时间解析。", "加入不确定时间、时间区间、冲突日期的显式 schema。"),
    Paper(8, "Benchmarking Chat Assistants on Long-Term Interactive Memory", "Temporal semantics", "benchmark paper / ICLR-adjacent pressure", "2024", "https://arxiv.org/abs/2410.10813", "evidence_contract.py + self_check.py", "event_time 与 mention_time 已在 evidence check 中区分。", "回答前新增 time-axis arbitration，禁止 mention-time 冒充 event-time。"),
    Paper(9, "Look Back to Reason Forward", "Temporal semantics", "official venue / OpenReview", "ICLR 2026", "https://openreview.net/forum?id=1cymflI2Lh", "search_service.py second_pass", "已有 second-pass supporting evidence 补召回。", "把一次补召回升级成 revisitable retrieval，允许围绕时间冲突再读一轮。"),

    Paper(10, "RAPTOR", "Hierarchy", "high-impact primary source", "2024", "https://arxiv.org/abs/2401.18059", "organized_projector/projector.py", "已有 overview / topic_dossier / temporal blocks。", "从 markdown merge 升级成稳定层级表示。"),
    Paper(11, "MemoRAG", "Hierarchy", "high-impact primary source", "2024", "https://arxiv.org/abs/2409.05591", "search_service.py", "现在是 L0 -> L1 -> L2 layered recall。", "把 coarse guide 和 fine evidence 的职责进一步分清，减少 overview 压平细节。"),
    Paper(12, "Hierarchical Memory for High-Efficiency Long-Term Reasoning", "Hierarchy", "preprint", "2025", "https://arxiv.org/abs/2507.22925", "query_planner.py + evidence_composer.py", "计划器已按问题族指定 primary_reader / supporting_readers。", "补 route budget 与 type-aware fragment compose。"),
    Paper(13, "In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents", "Hierarchy", "official paper / ACL", "ACL 2025", "https://aclanthology.org/2025.acl-long.413/", "organized_projector/projector.py + self_check.py", "代码里已经隐约分出 prospective consolidation 与 retrospective review。", "把 topic/persona/change-log 做成持续整合层，而不是只靠批量拼接。"),
    Paper(14, "Flexibly Utilize Memory for Long-Term Conversation via a Fragment-then-Compose Framework", "Hierarchy", "official paper / EMNLP", "EMNLP 2025", "https://aclanthology.org/2025.emnlp-main.1069/", "search_service.py + fusion/evidence_composer.py", "现有系统会融合多层证据，但没有专门的 fragment compose phase。", "加 fragment-first retrieval，再按问题槽位 compose。"),

    Paper(15, "Infini Memory", "Topic middle layer", "preprint", "2026", "https://arxiv.org/abs/2606.10677", "organized_projector/projector.py topic_dossier", "topic_dossier 已是 EchoMemory 比较特别的一层。", "补 canonical topic id、aliases、active ranges、entity links。"),
    Paper(16, "MemoryBank", "Topic middle layer", "official paper / AAAI", "AAAI 2024", "https://ojs.aaai.org/index.php/AAAI/article/view/29880", "profile / overview organization", "organized memory 已在做 persona / preference / event 聚合。", "把长期 persona 更新做成差分，而不是持续追加。"),
    Paper(17, "MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents", "Topic middle layer", "official venue / OpenReview", "ICLR 2026", "https://openreview.net/forum?id=XY8AaxDSLb", "atom_first_pipeline.py + organized_projector/projector.py", "当前系统能增量提取，但还缺 salience-driven rewrite / forget。", "加入 salience、stability、contradiction_count 等 lifecycle 信号。"),

    Paper(18, "HippoRAG", "Graph recall", "official venue / OpenReview", "NeurIPS 2024", "https://openreview.net/forum?id=hkujvAPVsg", "planner/graph_seed_planner.py + search_service.py", "graph recall 已能 seed、diffuse、回到 evidence。", "把 graph 从加分项升为关系题主 backbone，并强化 seed 质量。"),
    Paper(19, "GraphReader", "Graph recall", "high-impact primary source", "2024", "https://arxiv.org/abs/2406.14550", "graph_seed_planner.py + search_service.py", "已有 path_grounding contract 和 graph second pass。", "把 seed -> hop -> verify 三阶段显式写进结果 trace。"),
    Paper(20, "LEGO-GraphRAG", "Graph recall", "high-impact primary source", "2024", "https://arxiv.org/abs/2411.05844", "graph_seed_planner.py", "图检索在实现上已相对独立。", "进一步解耦 seed planner、diffusion policy、path rerank。"),
    Paper(21, "Zep / temporal KG line", "Graph recall", "preprint / system line", "2025", "https://arxiv.org/abs/2501.13956", "graph/sync.py", "图中已有 fact / event / entity / involves / evidence_of。", "补 explicit superseded edges、temporal state updates 和 conflict edges。"),

    Paper(22, "Mem0", "Lifecycle + systems", "high-impact primary source", "2025", "https://arxiv.org/abs/2504.19413", "session_service.py + atom_first_pipeline.py", "已经从 append-only stream 出发，而不是直接改写 profile blob。", "继续强化 selective extraction、memory promotion 与 write budget。"),
    Paper(23, "LightMem", "Lifecycle + systems", "preprint", "2025", "https://arxiv.org/abs/2510.18866", "readiness.py + pipeline scheduling", "已有 fast path / heavy stage / qa_ready 的分离方向。", "把 online-light / offline-heavy consolidation 做成明确产品层。"),
    Paper(24, "MemOS", "Lifecycle + systems", "high-impact primary source", "2025", "https://arxiv.org/abs/2505.22101", "runtime/container.py + readiness.py", "已经有 service container、resource service、readiness plane。", "把 readiness、resource、memory plane 做成更统一的 memory OS control plane。"),
    Paper(25, "AgentIR", "Lifecycle + systems", "preprint", "2026", "https://arxiv.org/abs/2605.25092", "query_planner.py + search_service.py", "已经在按 query family 做 retrieval cascade。", "按 workload / family 动态切换 cascade 深度和 budget。"),

    Paper(26, "Self-RAG", "Answerability + policy", "official venue / OpenReview", "NeurIPS 2023 foundational", "https://openreview.net/forum?id=hSyW5go0v8", "self_check.py + answerability_gate.py + search_service.py", "已有自检、证据契约和 answerability gate 雏形。", "继续把 gate 从 metadata 变成稳定控制回路。"),
    Paper(27, "Mem-T", "Answerability + policy", "preprint", "2026", "https://arxiv.org/abs/2601.23014", "self_check.py + logs", "现在已经有 expand / abstain / not_ready 的机制原型。", "把这些动作系统性记成 policy trace，后面才能谈 learned controller。"),
    Paper(28, "D-MEM", "Answerability + policy", "preprint", "2026", "https://arxiv.org/abs/2603.14597", "policy/answerability_gate.py", "当前 gate 已经从 advisory 往 executive 走。", "先把 rule-based gate 做稳，再考虑 reward-shaped policy。"),

    Paper(29, "MIRIX", "Multimodal memory", "high-impact primary source", "2025", "https://arxiv.org/abs/2507.07957", "resource_service.py + graph/sync.py + planner/query_planner.py", "资源层已经能把 image 转成 structured image_evidence 并同步进图。", "把 visual write path 做成论文主线，而不只是 query-time 附件。"),
    Paper(30, "3DLLM-Mem / visual memory line", "Multimodal memory", "official venue / OpenReview", "NeurIPS 2025", "https://openreview.net/forum?id=q5QaTQcUbS", "query_planner.py visual mode + graph/sync.py", "visual query 已被单独识别，evidence_contract 也已有 image_evidence 类型。", "统一 image node、OCR、owner link、event/fact grounding，让视觉路径可审计。"),
]


KEY_FILES = [
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/session_service.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/resource_service.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/fusion/evidence_composer.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/answerability_gate.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
]


def file_link(path: str) -> str:
    name = Path(path).name
    return f'<a href="file://{escape(path)}">{escape(name)}</a>'


def badge_class(source_quality: str) -> str:
    if "official" in source_quality or "benchmark" in source_quality:
        return "ok"
    if "high-impact" in source_quality:
        return "info"
    if "foundational" in source_quality:
        return "warn"
    return "mutedbadge"


def row_html(paper: Paper) -> str:
    return f"""
    <tr>
      <td class="num">{paper.idx}</td>
      <td>
        <a href="{escape(paper.link)}" target="_blank" rel="noreferrer">{escape(paper.title)}</a>
        <div class="source">{escape(paper.source)}</div>
        <div class="badgerow"><span class="badge {badge_class(paper.source_quality)}">{escape(paper.source_quality)}</span></div>
      </td>
      <td><code>{escape(paper.echo_layer)}</code></td>
      <td>{escape(paper.current_truth)}</td>
      <td>{escape(paper.next_change)}</td>
    </tr>
    """


def section_html(bucket: str) -> str:
    rows = "".join(row_html(p) for p in PAPERS if p.bucket == bucket)
    count = sum(1 for p in PAPERS if p.bucket == bucket)
    return f"""
    <section class="panel">
      <div class="rowhead">
        <h2>{escape(bucket)}</h2>
        <span class="badge info">{count} papers</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Paper</th>
            <th>Closest EchoMemory Layer</th>
            <th>Current Code Truth</th>
            <th>Best Generic Next Step</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


def main() -> None:
    buckets = []
    seen = set()
    for paper in PAPERS:
        if paper.bucket not in seen:
            seen.add(paper.bucket)
            buckets.append(paper.bucket)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory 结构分析 + 严格 30 篇路线图</title>
  <style>
    :root {{
      --bg:#f5f7fb; --panel:#ffffff; --line:#dde5ef; --text:#182333; --muted:#607286;
      --blue:#245cff; --blue-soft:#eef4ff; --green:#0f8c60; --green-soft:#eefaf4;
      --amber:#a86400; --amber-soft:#fff7ea; --shadow:0 14px 34px rgba(18,32,51,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .page {{ max-width:1340px; margin:0 auto; padding:28px 20px 56px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); }}
    .hero {{ padding:28px; margin-bottom:16px; background:linear-gradient(135deg,#fff 0%,#eef4ff 100%); }}
    .panel {{ padding:18px 20px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 10px; line-height:1.28; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:20px; }}
    p {{ margin:8px 0; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:6px 0; }}
    .muted {{ color:var(--muted); }}
    .kpi {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi > div {{ border:1px solid var(--line); border-radius:10px; background:#fbfcff; padding:12px; }}
    .kpi strong {{ display:block; font-size:24px; margin-bottom:4px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .callout {{ margin-top:14px; padding:12px 14px; border-left:4px solid var(--blue); background:var(--blue-soft); border-radius:8px; }}
    .rowhead {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:8px; }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); padding:10px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#f7f9fc; color:var(--muted); font-size:12px; }}
    .num {{ width:42px; color:var(--blue); }}
    .source {{ margin-top:4px; color:var(--muted); font-size:12px; }}
    .badgerow {{ margin-top:6px; }}
    .badge {{ display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px; font-weight:700; }}
    .badge.ok {{ background:var(--green-soft); color:var(--green); }}
    .badge.info {{ background:var(--blue-soft); color:var(--blue); }}
    .badge.warn {{ background:var(--amber-soft); color:var(--amber); }}
    .badge.mutedbadge {{ background:#f1f4f8; color:#5f7287; }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; background:#f3f6fb; border:1px solid #dfe7f1; border-radius:4px; padding:1px 5px; font-size:12px; word-break:break-all; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    @media (max-width: 980px) {{
      .grid, .kpi {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory 结构分析 + 严格 30 篇近两年路线图</h1>
      <p class="muted">
        这页专门解决一个很实际的问题：前面的材料里，“顶会/官方 benchmark”“高价值 primary source”“前沿 preprint”混在一起时，容易让论文口径发虚。
        所以这里把 30 篇重新整理成一份 <b>更严格的 research map</b>：既保留最有用的工作，也明确标出 source quality。
      </p>
      <div class="kpi">
        <div><strong>30</strong><span class="muted">recent papers/signals</span></div>
        <div><strong>11</strong><span class="muted">key code anchors</span></div>
        <div><strong>6</strong><span class="muted">structural buckets</span></div>
        <div><strong>0</strong><span class="muted">dataset keyword hacks recommended</span></div>
      </div>
      <div class="callout">
        核心判断不变，但表述更严格了：<b>EchoMemory 已有值得研究的骨架</b>，真正要补的是
        <b>time arbitration、topic middle layer stability、path-grounded graph recall、answerability execution、multimodal write-time structuring</b>。
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>当前代码真相</h2>
        <ul>
          <li><b>不是 flat RAG：</b>已经有 stream、atom、organized layer、graph、planner、readiness、resource 这些层。</li>
          <li><b>不是只有文本：</b><code>resource_service.py</code> 已经能把 image 转成 <code>image_evidence</code> 并同步进图。</li>
          <li><b>不是只有检索：</b><code>evidence_contract.py</code>、<code>self_check.py</code>、<code>answerability_gate.py</code> 已经把“可不可以答”拉进主流程。</li>
        </ul>
      </div>
      <div class="panel">
        <h2>最值得优先补的 5 个改进</h2>
        <ul>
          <li><b>Time：</b>回答前显式仲裁 story / mention / write 三轴时间。</li>
          <li><b>Topic：</b>让 <code>topic_dossier</code> 从浅聚合升级成稳定语义中层。</li>
          <li><b>Graph：</b>关系题与视觉题优先返回 path-grounded evidence，而不是只给 node hit。</li>
          <li><b>Policy：</b>把 self-check 从 advisory 彻底推进成 executive gate。</li>
          <li><b>Multimodal：</b>让视觉写入路径成为论文主线，而不是额外 feature。</li>
        </ul>
      </div>
    </section>

    <section class="panel">
      <h2>关键文件入口</h2>
      <ul>
        {"".join(f"<li>{file_link(path)}</li>" for path in KEY_FILES)}
      </ul>
    </section>

    {"".join(section_html(bucket) for bucket in buckets)}

    <section class="panel">
      <h2>怎么把这些收敛成一篇论文</h2>
      <p>
        如果是偏 memory / agent systems 口径，最稳的题眼是：
        <b>contract-driven long-horizon multimodal memory with a topic middle layer and answerability-aware routing</b>。
      </p>
      <p>
        如果想更贴近 CVPR 风格，就要把 <b>visual write-time structuring</b> 和 <b>image-grounded memory QA / retrieval</b> 再做重一点，
        让视觉不只是一个子实验，而是主结果的一部分。
      </p>
    </section>
  </div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
