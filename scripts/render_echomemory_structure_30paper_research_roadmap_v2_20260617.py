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
    / "echomemory_structure_30paper_research_roadmap_v2_20260617.html"
)


@dataclass(frozen=True)
class Paper:
    idx: int
    axis: str
    title: str
    source: str
    link: str
    code_anchor: str
    observed_now: str
    proposed_change: str


PAPERS: list[Paper] = [
    Paper(1, "Benchmarks", "LoCoMo", "ACL 2024", "https://aclanthology.org/2024.acl-long.747/", "temporal/query_resolver.py + search_service.py", "系统已区分 temporal / relational / longitudinal family，也开始显式处理 query_time_anchor。", "把时间题从“召回到一点时间文本”升级到“story-time / mention-time / write-time 仲裁”。"),
    Paper(2, "Benchmarks", "LongMemEval", "ICLR 2025", "https://openreview.net/forum?id=pZiyCaVuti", "self_check.py + evidence_contract.py + search_service.py", "已有 evidence contract 和 self-check，但仍偏诊断。", "把 self-check 变成可执行 gate，支持 expand / abstain / defer。"),
    Paper(3, "Benchmarks", "LongMemEval-V2", "2026 / benchmark line", "https://arxiv.org/abs/2605.12493", "readiness.py + session_service.py", "已有 qa_ready / organized_ready / core_ready。", "把 readiness 从“流水线完成”升级到“证据可用、问题可答”。"),
    Paper(4, "Benchmarks", "When Stored Evidence Stops Being Usable", "2026", "https://arxiv.org/abs/2605.07313", "self_check.py", "现在能看 coverage_ratio、missing_types、event_time/path_grounding 缺口。", "引入 evidence usability audit，让“写进去了但回答仍错”可定位。"),
    Paper(5, "Benchmarks", "WhenLoss", "2026", "https://arxiv.org/abs/2605.24579", "atom_first_pipeline.py + search_service.py", "导入与检索已有分开的日志入口。", "把 formation failure、retrieval failure、answer-time failure 明确打散记录。"),
    Paper(6, "Benchmarks", "Regimes", "2026", "https://arxiv.org/abs/2606.10241", "eval harness + search policy", "当前本地研究包已经开始按机制做小 benchmark。", "继续扩成 held-out 机制实验，而不是只看总准确率。"),

    Paper(7, "Time", "TiMem", "2026", "https://arxiv.org/abs/2601.02845", "organized_projector/projector.py + temporal/query_resolver.py", "现在 event_time 会流入 organized event 和 temporal tree block。", "按事件发生时间重整中层结构，而不是只按写入/提及时间落桶。"),
    Paper(8, "Time", "H-Mem", "EACL 2026", "https://aclanthology.org/2026.eacl-long.363/", "query_planner.py + search_service.py", "当前已是 tree + graph 的混合路由雏形。", "补一个真正的 mode controller，统一时间题、关系题、经历题的 reader schedule。"),
    Paper(9, "Time", "Evaluating Very Long-Term Conversational Memory of LLM Agents", "ACL 2024", "https://arxiv.org/abs/2402.17753", "temporal/query_resolver.py", "已支持 yesterday / last week / this month 等相对时间解析。", "增加不确定时间、时间区间、冲突日期的显式 schema。"),
    Paper(10, "Time", "Benchmarking Chat Assistants on Long-Term Interactive Memory", "ICLR 2025", "https://arxiv.org/abs/2410.10813", "evidence_contract.py + self_check.py", "event_time 与 mention_time 已在 evidence check 中区分。", "回答前新增 time-axis arbitration，禁止 mention-time 冒充 event-time。"),
    Paper(11, "Time", "Look Back to Reason Forward", "ICLR 2026", "https://openreview.net/forum?id=1cymflI2Lh", "search_service.py second_pass", "已有 second pass supporting evidence 补召回。", "把一次补召回升级成 revisitable retrieval，允许围绕时间冲突再读一轮。"),

    Paper(12, "Hierarchy", "RAPTOR", "2024", "https://arxiv.org/abs/2401.18059", "organized_projector/projector.py", "已有 overview / topic_dossier / temporal blocks。", "从 markdown merge 进一步升级成稳定层级表示。"),
    Paper(13, "Hierarchy", "MemoRAG", "2024", "https://arxiv.org/abs/2409.05591", "search_service.py", "现在是 L0 -> L1 -> L2 layered recall。", "把 coarse guide 和 fine evidence 的职责进一步分清，减少 overview 压平细节。"),
    Paper(14, "Hierarchy", "Hierarchical Memory for High-Efficiency Long-Term Reasoning", "2025", "https://arxiv.org/abs/2507.22925", "query_planner.py + evidence_composer.py", "现有计划器已按问题族指定 primary_reader / supporting_readers。", "补 route budget 与 type-aware fragment compose。"),
    Paper(15, "Hierarchy", "In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents", "ACL 2025", "https://aclanthology.org/2025.acl-long.413/", "organized_projector/projector.py + self_check.py", "代码里已经隐约分出 prospective consolidation 与 retrospective review。", "把 topic/persona/change-log 做成持续整合层，不再只靠批量拼接。"),
    Paper(16, "Hierarchy", "Flexibly Utilize Memory for Long-Term Conversation via a Fragment-then-Compose Framework", "EMNLP 2025", "https://aclanthology.org/2025.emnlp-main.1069/", "search_service.py + fusion/evidence_composer.py", "现有系统会融合多层证据，但没有专门的 fragment compose phase。", "加 fragment-first retrieval，再按问题槽位 compose。"),

    Paper(17, "Topic", "Infini Memory", "2026", "https://arxiv.org/abs/2606.10677", "organized_projector/projector.py topic_dossier", "topic_dossier 已经是 EchoMemory 比较特别的一层。", "补 canonical topic id、aliases、active ranges、entity links。"),
    Paper(18, "Topic", "G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems", "NeurIPS 2025", "https://openreview.net/forum?id=mmIAp3cVS0", "atom_first_pipeline.py + graph/sync.py", "消息里已有 role，图里已有 entity / event / fact。", "把 speaker / owner / source-agent 变成 memory schema 的一等字段。"),
    Paper(19, "Topic", "MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents", "ICLR 2026", "https://openreview.net/forum?id=XY8AaxDSLb", "atom_first_pipeline.py + organized_projector/projector.py", "当前系统能增量提取，但还缺 salience-driven rewrite / forget。", "加入 salience、stability、contradiction_count 等 lifecycle 信号。"),
    Paper(20, "Topic", "MemoryBank", "AAAI 2024", "https://ojs.aaai.org/index.php/AAAI/article/view/29880", "profile / overview organization", "现有 organized memory 已在做 persona / preference / event 聚合。", "把长期 persona 更新做成差分，而不是只持续追加。"),

    Paper(21, "Graph", "HippoRAG", "NeurIPS 2024", "https://openreview.net/forum?id=hkujvAPVsg", "planner/graph_seed_planner.py + search_service.py", "graph recall 已经能 seed、diffuse、回到 evidence。", "把 graph 从加分项升为关系题主 backbone，并强化 seed 质量。"),
    Paper(22, "Graph", "GraphReader", "2024", "https://arxiv.org/abs/2406.14550", "graph_seed_planner.py + search_service.py", "已有 path_grounding contract 和 graph second pass。", "把 seed -> hop -> verify 三阶段显式写进结果 trace。"),
    Paper(23, "Graph", "LEGO-GraphRAG", "2024", "https://arxiv.org/abs/2411.05844", "graph_seed_planner.py", "图检索在实现上已相对独立。", "进一步解耦 seed planner、diffusion policy、path rerank。"),
    Paper(24, "Graph", "3DLLM-Mem", "NeurIPS 2025", "https://openreview.net/forum?id=q5QaTQcUbS", "query_planner.py visual mode + graph/sync.py", "当前 QueryPlanner 已经把 visual query 单独识别。", "未来把 image_evidence node、region grounding 和图路径统一起来。"),
    Paper(25, "Graph", "Zep / temporal KG line", "2025", "https://arxiv.org/abs/2501.13956", "graph/sync.py", "图中已经有 fact / event / entity / involves / evidence_of。", "补 explicit superseded edges、temporal state update、conflict edge。"),

    Paper(26, "Lifecycle", "Mem0", "2025", "https://arxiv.org/abs/2504.19413", "atom_first_pipeline.py", "写入链已按 extract / merge / sync / project 分阶段。", "把 hot-path ingest 和 cold-path consolidation 更彻底拆开。"),
    Paper(27, "Lifecycle", "LightMem", "2025", "https://arxiv.org/abs/2510.18866", "readiness.py + atom_first_pipeline.py", "已有 strict/core readiness mode。", "让快速可答与完整归档成为两个清晰产品级状态。"),
    Paper(28, "Lifecycle", "MemOS", "2025", "https://arxiv.org/abs/2505.22101", "session_service.py + readiness.py", "当前 session meta 已经像一个轻量 memory OS 控制面。", "把不同记忆层的状态、预算、异常正式暴露为 governable resource。"),

    Paper(29, "Policy", "Self-RAG", "ICLR 2024", "https://openreview.net/forum?id=hSyW5go0v8", "self_check.py + search_service.py", "检索后自检已存在，但执行力度不够。", "让 recommendation 真正控制 expand / abstain / answer。"),
    Paper(30, "Policy", "MIRIX", "2025", "https://arxiv.org/abs/2507.07957", "query_planner.py + evidence_contract.py + graph/sync.py", "代码中已经能识别 visual query，也有 image_evidence contract 占位。", "把 typed multimodal memory 做成原生节点，而不是以后补。"),
]


AXIS_ORDER = [
    "Benchmarks",
    "Time",
    "Hierarchy",
    "Topic",
    "Graph",
    "Lifecycle",
    "Policy",
]


KEY_FILES = [
    "/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/graph_seed_planner.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
    "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/fusion/evidence_composer.py",
]


def _file_link(path: str) -> str:
    return f'<a href="file://{escape(path)}">{escape(Path(path).name)}</a>'


def _paper_rows(axis: str) -> str:
    rows: list[str] = []
    for p in PAPERS:
        if p.axis != axis:
            continue
        rows.append(
            f"""
            <tr>
              <td class="num">{p.idx}</td>
              <td>
                <a href="{escape(p.link)}" target="_blank" rel="noreferrer">{escape(p.title)}</a>
                <div class="source">{escape(p.source)}</div>
              </td>
              <td><code>{escape(p.code_anchor)}</code></td>
              <td>{escape(p.observed_now)}</td>
              <td>{escape(p.proposed_change)}</td>
            </tr>
            """
        )
    return "".join(rows)


def main() -> None:
    sections: list[str] = []
    for axis in AXIS_ORDER:
        count = sum(1 for p in PAPERS if p.axis == axis)
        if not count:
            continue
        sections.append(
            f"""
            <section class="card">
              <div class="row">
                <h2>{escape(axis)}</h2>
                <span class="badge">{count} papers</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Paper</th>
                    <th>Closest Code</th>
                    <th>What EchoMemory already shows</th>
                    <th>Best generic next step</th>
                  </tr>
                </thead>
                <tbody>{_paper_rows(axis)}</tbody>
              </table>
            </section>
            """
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory 30-Paper Research Roadmap v2</title>
  <style>
    :root {{
      --bg:#f5f7fb; --panel:#ffffff; --line:#dbe3ee; --text:#172233; --muted:#607286;
      --blue:#245cff; --blue-soft:#eef4ff; --green:#10895f; --amber:#ad6900;
      --shadow:0 10px 26px rgba(18,32,51,.06);
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:var(--bg); color:var(--text);
      font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    }}
    .page {{ max-width:1320px; margin:0 auto; padding:28px 20px 60px; }}
    .hero,.card,.panel {{
      background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow);
    }}
    .hero {{ padding:28px; margin-bottom:18px; background:linear-gradient(135deg,#fff 0%,#f0f6ff 100%); }}
    .card,.panel {{ padding:18px 20px; margin-bottom:16px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .kpi {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:16px; }}
    .kpi > div {{ border:1px solid var(--line); background:#fbfcff; border-radius:10px; padding:12px; }}
    .kpi strong {{ display:block; font-size:22px; margin-bottom:4px; }}
    h1,h2,h3 {{ margin:0 0 10px; line-height:1.28; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:20px; }}
    p {{ margin:8px 0; }}
    .muted {{ color:var(--muted); }}
    .callout {{
      margin-top:14px; padding:12px 14px; border-radius:10px; border-left:4px solid var(--blue);
      background:var(--blue-soft);
    }}
    .badge {{
      display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px;
      background:#eef3ff; color:var(--blue); border:1px solid #d6e2ff;
    }}
    .row {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:8px; }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); padding:10px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#f7f9fc; color:var(--muted); font-size:12px; }}
    .num {{ width:38px; color:var(--blue); }}
    .source {{ margin-top:4px; color:var(--muted); font-size:12px; }}
    code {{
      font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
      background:#f3f6fb; border:1px solid #e0e7f1; border-radius:4px; padding:1px 5px; font-size:12px;
      word-break:break-all;
    }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:6px 0; }}
    @media (max-width: 980px) {{
      .grid, .kpi {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory 结构分析 + 30 篇近两年研究路线图 v2</h1>
      <p class="muted">
        这页不是单纯列书目，而是把 <b>30 篇高相关论文/benchmark</b> 压到 EchoMemory 的真实代码结构上，
        直接回答三个问题：<b>现在代码已经像什么、离论文里的理想形态还差什么、下一步怎么改才是泛化性的结构改进</b>。
      </p>
      <div class="kpi">
        <div><strong>30</strong><span class="muted">papers / benchmark signals</span></div>
        <div><strong>7</strong><span class="muted">research axes</span></div>
        <div><strong>10</strong><span class="muted">main code anchors</span></div>
        <div><strong>0</strong><span class="muted">dataset keyword hacks recommended</span></div>
      </div>
      <div class="callout">
        核心结论：EchoMemory 已经不是“只有向量检索的 memory demo”，而是有了 <b>atom, topic dossier, temporal tree, graph, evidence contract, self-check</b> 这些真正可继续做研究的骨架。
        但它距离一篇更强的 CVPR / memory systems 论文，还缺三步：<b>把 self-check 执行化</b>、<b>把 topic 语义层做稳定</b>、<b>把时间与图路径做成更强的答前仲裁</b>。
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>当前代码骨架</h2>
        <ul>
          <li><b>写入层：</b><code>atom_first_pipeline.py</code> 已经是消息到 atom 的单一事实源。</li>
          <li><b>组织层：</b><code>projector.py</code> 已生成 <code>profile / overview / topic_dossier / events / temporal_tree</code>。</li>
          <li><b>图层：</b><code>graph/sync.py</code> 已同步 <code>fact / event / entity</code> 节点和结构边。</li>
          <li><b>规划层：</b><code>query_planner.py</code> 已按问题族区分 temporal / relational / longitudinal / visual。</li>
          <li><b>校验层：</b><code>evidence_contract.py</code> 和 <code>self_check.py</code> 已开始检验证据类型是否够答题。</li>
        </ul>
      </div>
      <div class="panel">
        <h2>最重要的通用改进方向</h2>
        <ul>
          <li><b>Time：</b>不要只解析 yesterday，要做 story-time / mention-time / write-time 仲裁。</li>
          <li><b>Topic：</b>把 topic dossier 从浅分桶升级成稳定语义层。</li>
          <li><b>Graph：</b>关系题和视觉题让图成为主 recall backbone，而不只是补充证据。</li>
          <li><b>Policy：</b>让 self-check 真正控制 expand / abstain / answer。</li>
          <li><b>Lifecycle：</b>把 fast ingest 和 full consolidation 拆成清楚的产品状态。</li>
        </ul>
      </div>
    </section>

    <section class="panel">
      <h2>关键代码入口</h2>
      <p class="muted">这几份文件是理解 EchoMemory 结构和后续改造的主入口：</p>
      <ul>
        {''.join(f"<li>{_file_link(path)}</li>" for path in KEY_FILES)}
      </ul>
    </section>

    {''.join(sections)}

    <section class="panel">
      <h2>一句话研究判断</h2>
      <p>
        如果要继续往“能投稿”的方向走，我会把论文主张收敛成一句非常实在的话：
        <b>EchoMemory 不是在比谁能塞更多上下文，而是在做一种带时间、主题、图和答前证据契约的长期记忆系统。</b>
      </p>
      <p class="muted">
        这也解释了为什么下一阶段最重要的不是再换一个模型，而是让 <code>topic_dossier</code>、<code>graph recall</code>、<code>time arbitration</code>、<code>self-check gate</code> 真正闭环。
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
