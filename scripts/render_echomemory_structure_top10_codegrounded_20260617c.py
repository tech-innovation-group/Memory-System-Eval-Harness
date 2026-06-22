#!/usr/bin/env python3
from __future__ import annotations

from html import escape
from pathlib import Path


OUT = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_structure_top10_codegrounded_20260617c.html"
)


STRUCTURE = [
    {
        "name": "1. Session / Write Path",
        "summary": "消息先持久化到 session，再触发 atom-first pipeline。这里已经不是简单聊天记录，而是 append-only stream + readiness 状态机。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/session_service.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py",
        ],
        "strength": "有 commit、messages.jsonl、readiness、token budget、去重、元认知内容过滤，写入侧已经有 memory system 的样子。",
        "gap": "写入完成不等于 QA-ready；当前 readiness 仍偏流水线完成语义，离“这类题现在可答”还有距离。",
    },
    {
        "name": "2. Atom -> Organized Middle Layer",
        "summary": "atom 被投影成 profile / overview / topic_dossier / entities / events / temporal_tree。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
        ],
        "strength": "这层很关键，说明 EchoMemory 已经不是 flat vector recall，而是试图做可治理的中层记忆。",
        "gap": "topic_dossier 还偏浅聚合；temporal_tree 主要是时间桶，不是更强的事件时间推理结构；profile/overview 仍是 markdown merge 风格。",
    },
    {
        "name": "3. Graph / Episode Layer",
        "summary": "atom 和 organized memory 会同步到 graph；episode 也已经接入。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/episode_sync.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/episode/memory_service.py",
        ],
        "strength": "graph 不是摆设，已经有 atom/fact/event/entity/image_evidence 等节点雏形。",
        "gap": "关系路径、时间状态变化、可解释 path trace 还不够强，很多图证据更像 sidecar 而不是 backbone。",
    },
    {
        "name": "4. Planner / Retrieval",
        "summary": "QueryPlanner 已经区分 visual / relational / profile / longitudinal / temporal / experience；SearchService 负责分层检索和拼装。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/fusion/evidence_composer.py",
        ],
        "strength": "已经有 query family、L0/L1/L2、graph-first、query_time_anchor，这条主骨架是对的。",
        "gap": "reader 选择还偏启发式；composition 还不够 slot-aware；文本 fallback 仍是兜底主力之一。",
    },
    {
        "name": "5. Contract / Self-check / Answerability",
        "summary": "evidence_contract 定义题型所需证据，自检层检查 coverage 和时间/路径缺口，answerability_gate 负责结构化决策。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/answerability_gate.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/session_readiness.py",
        ],
        "strength": "这已经超出很多 memory repo：系统开始关心“这些证据能不能支撑回答”，而不是只看召回数。",
        "gap": "现在大多还是 advisory；真正决定 expand / abstain / defer 的执行闭环还没有完全收紧。",
    },
]


PAPERS = [
    {
        "title": "LoCoMo: Evaluating Very Long-Term Conversational Memory of LLM Agents",
        "venue": "ACL 2024",
        "url": "https://aclanthology.org/2024.acl-long.747/",
        "insight": "真正难点是时间、关系、多跳和跨 session 一致性，不是简单记住一条事实。",
        "modules": "TemporalQueryResolver / OrganizedProjector / QueryPlanner",
        "upgrade": "把 temporal_tree 和 event memory 做成三时钟结构：story_time / mention_time / write_time，回答前做时间轴仲裁。",
    },
    {
        "title": "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory",
        "venue": "ICLR 2025",
        "url": "https://openreview.net/forum?id=pZiyCaVuti",
        "insight": "长期记忆要拆成 formation、retrieval、reading、abstention，不是单一 recall 指标。",
        "modules": "SearchService / EvidenceContract / SelfCheckPolicy",
        "upgrade": "把当前 self-check 从诊断器推进成执行器，让 coverage 缺口真实影响 second pass 和 abstain。",
    },
    {
        "title": "HippoRAG",
        "venue": "NeurIPS 2024",
        "url": "https://openreview.net/forum?id=hkujvAPVsg",
        "insight": "图结构应该成为主召回 backbone，而不是向量检索后的装饰层。",
        "modules": "GraphSeedPlanner / SearchService / MemoryGraphSync",
        "upgrade": "关系题优先 graph-first；seed 不只来自 regex 或向量，还要来自 dossier anchor、recent entity、time-filtered event。",
    },
    {
        "title": "In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents",
        "venue": "ACL 2025",
        "url": "https://aclanthology.org/2025.acl-long.413/",
        "insight": "memory 不是 append 完就结束，还需要 retrospective consolidation 和 revision。",
        "modules": "AtomFirstPipeline / OrganizedProjector",
        "upgrade": "把 overview/profile/topic_dossier 从 merge 文本升级为 state + delta + provenance 的持续整合对象。",
    },
    {
        "title": "Fragment-then-Compose for Long-Term Conversation",
        "venue": "EMNLP 2025",
        "url": "https://aclanthology.org/2025.emnlp-main.1069/",
        "insight": "比起直接塞大摘要，先取片段、再按题型重组，细节题更稳。",
        "modules": "EvidenceComposer / SearchService",
        "upgrade": "L2 先收 small fact/event fragments，再按时间、主体、关系槽位 compose，而不是只拼 dossier/overview。",
    },
    {
        "title": "3DLLM-Mem: Long-Term Spatial-Temporal Memory for Embodied 3D Large Language Model",
        "venue": "NeurIPS 2025",
        "url": "https://openreview.net/forum?id=q5QaTQcUbS",
        "insight": "多模态记忆不是 OCR 一把梭；需要结构化、可链接的视觉证据。",
        "modules": "MemoryGraphSync / QueryPlanner / SelfCheckPolicy",
        "upgrade": "把 image_evidence 提升成真正的一等节点，挂上 owner / event / fact link，并让自检能识别“有图无 grounding”。",
    },
    {
        "title": "G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems",
        "venue": "NeurIPS 2025",
        "url": "https://openreview.net/forum?id=mmIAp3cVS0",
        "insight": "层级记忆需要 role / owner / source-agent 的归属，否则协作信息容易串。",
        "modules": "SessionService / AtomFirstPipeline / GraphSync",
        "upgrade": "扩展 schema，显式保留 speaker / role_id / ownership，让 topic/graph 不只看文本内容。",
    },
    {
        "title": "MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents",
        "venue": "ICLR 2026",
        "url": "https://openreview.net/forum?id=XY8AaxDSLb",
        "insight": "不是存得越多越好；需要 salience、压缩、保留和重写策略。",
        "modules": "AtomFirstPipeline / OrganizedProjector / EpisodeMemoryService",
        "upgrade": "加入 salience / freshness / contradiction-aware consolidation，控制 dossier 和 entity 卡片膨胀。",
    },
    {
        "title": "H-Mem: Hybrid Multi-Dimensional Memory Management for Long-Context Conversational Agents",
        "venue": "EACL 2026",
        "url": "https://aclanthology.org/2026.eacl-long.363/",
        "insight": "时间树和语义树并行，再由 mode controller 决定走哪条路径。",
        "modules": "OrganizedProjector / QueryPlanner / SessionReadinessPolicy",
        "upgrade": "把 topic_dossier 强化成 semantic tree；让 family-aware readiness 真正按题型要求的 memory plane 解锁。",
    },
    {
        "title": "Look Back to Reason Forward: Revisitable Memory for Long-Context LLM Agents",
        "venue": "ICLR 2026",
        "url": "https://openreview.net/forum?id=1cymflI2Lh",
        "insight": "一次线性读完历史并不稳，强系统应该能 revisit 证据。",
        "modules": "SearchService / SelfCheckPolicy / AnswerabilityGate",
        "upgrade": "把 second pass 从“补一点 supporting evidence”升级成 revisitable retrieval loop，但仍保持 typed contract，不做数据集关键词 hack。",
    },
]


PRIORITIES = [
    {
        "level": "P0",
        "title": "让 self-check 变成执行闭环",
        "why": "收益最大，也最不依赖数据集特判。",
        "items": [
            "temporal 缺 event_time 时，强制继续取更窄的 event/story-time 证据，而不是只给 caution。",
            "relational 缺 path_grounding 时，优先 graph revisit；补不出来就 abstain。",
            "answerability_gate 的 decision 要反向影响 SearchService，而不只写进 budget_consumed。",
        ],
    },
    {
        "level": "P0",
        "title": "把三时钟时间模型做完整",
        "why": "时间题是长期记忆最稳定的共性难点。",
        "items": [
            "在 event / temporal_tree / graph 节点里明确区分 story / mention / write 轴。",
            "自检显式判定 mention-time 冒充 event-time 的风险。",
            "支持 interval、uncertain time、conflict resolution，而不是只做 yesterday 这类规则解析。",
        ],
    },
    {
        "level": "P0",
        "title": "加强 topic_dossier 作为真正中层",
        "why": "现在方向对，但还不够稳定。",
        "items": [
            "给 dossier 增加 canonical topic id、aliases、active ranges、linked entities。",
            "支持 split / merge / superseded，避免一个 dossier 无限长胖。",
            "把 markdown merge 改成 state object + delta update。",
        ],
    },
    {
        "level": "P1",
        "title": "把 graph 从 second-pass 配角变成主 backbone 之一",
        "why": "关系题、多跳题、多模态 grounding 都会吃到红利。",
        "items": [
            "graph seed 同时吃 dossier anchor、recent entity、time-filtered event。",
            "path grounding 变成结果对象一等字段，不只是 trace 可选值。",
            "把 event/state transition edge 做得更强，而不只是 about / involves / has_fact。",
        ],
    },
    {
        "level": "P1",
        "title": "做 fragment-then-compose 而不是只拼大摘要",
        "why": "对细节题和时间题更稳，也更泛化。",
        "items": [
            "先召回细粒度 fact/event fragment。",
            "按 query family 的槽位要求做重组。",
            "最终对 composed answer 再跑 evidence completeness 检查。",
        ],
    },
]


NANO_GUIDE = [
    {
        "name": "统一 nano 主实现",
        "path": "/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v17.py",
        "why": "最适合理解完整主干：stream -> atoms -> topic dossier -> temporal blocks -> graph -> readiness -> answerability。",
    },
    {
        "name": "统一结构消融",
        "path": "/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_unified_structure_ablation_20260617.py",
        "why": "能直接看出 three-clock time、topic dossier、typed answerability gate 分别值多少钱。",
    },
    {
        "name": "自检执行器消融",
        "path": "/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_selfcheck_executor_ablation.py",
        "why": "对应当前主代码最重要的升级方向：把 advisory self-check 变成 executive policy。",
    },
    {
        "name": "视觉写入桥接",
        "path": "/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_visual_ingest_bridge.py",
        "why": "如果要走 CVPR 路线，这个方向最重要：证明多模态质量来自写入侧结构化，而不只是 query-time patch。",
    },
]


def file_link(path: str) -> str:
    return f'<a href="file://{escape(path)}">{escape(path)}</a>'


def render_structure() -> str:
    blocks: list[str] = []
    for item in STRUCTURE:
        files = "".join(f"<li>{file_link(path)}</li>" for path in item["files"])
        blocks.append(
            f"""
            <section class="card">
              <h3>{escape(item['name'])}</h3>
              <p>{escape(item['summary'])}</p>
              <p><strong>现在的长处：</strong>{escape(item['strength'])}</p>
              <p><strong>主要短板：</strong>{escape(item['gap'])}</p>
              <ul class="files">{files}</ul>
            </section>
            """
        )
    return "".join(blocks)


def render_papers() -> str:
    rows: list[str] = []
    for idx, item in enumerate(PAPERS, start=1):
        rows.append(
            f"""
            <tr>
              <td class="num">{idx}</td>
              <td><a href="{escape(item['url'])}" target="_blank" rel="noreferrer">{escape(item['title'])}</a><div class="venue">{escape(item['venue'])}</div></td>
              <td>{escape(item['insight'])}</td>
              <td>{escape(item['modules'])}</td>
              <td>{escape(item['upgrade'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_priorities() -> str:
    parts: list[str] = []
    for item in PRIORITIES:
        bullets = "".join(f"<li>{escape(line)}</li>" for line in item["items"])
        parts.append(
            f"""
            <div class="priority">
              <div class="pill">{escape(item['level'])}</div>
              <h3>{escape(item['title'])}</h3>
              <p class="muted">{escape(item['why'])}</p>
              <ul>{bullets}</ul>
            </div>
            """
        )
    return "".join(parts)


def render_nano() -> str:
    rows: list[str] = []
    for item in NANO_GUIDE:
        rows.append(
            f"""
            <tr>
              <td>{escape(item['name'])}</td>
              <td>{file_link(item['path'])}</td>
              <td>{escape(item['why'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def main() -> None:
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory 结构 Top10 代码分析</title>
  <style>
    :root {{
      --bg:#f5f7fb; --panel:#fff; --line:#dde5ef; --text:#182333; --muted:#607286;
      --blue:#245cff; --blue-soft:#eef4ff; --green:#0f8c60; --green-soft:#eefaf4;
      --amber:#a86400; --amber-soft:#fff7ea; --shadow:0 14px 34px rgba(18,32,51,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .page {{ max-width:1320px; margin:0 auto; padding:28px 20px 56px; }}
    .hero,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); }}
    .hero {{ padding:28px; margin-bottom:16px; background:linear-gradient(135deg,#fff 0%,#eef4ff 100%); }}
    .card {{ padding:18px 20px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 10px; line-height:1.28; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:20px; }}
    h3 {{ font-size:16px; }}
    p {{ margin:8px 0; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:6px 0; }}
    .muted {{ color:var(--muted); }}
    .kpi {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi > div {{ border:1px solid var(--line); border-radius:10px; background:#fbfcff; padding:12px; }}
    .kpi strong {{ display:block; font-size:24px; margin-bottom:4px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .priority-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .priority {{ border:1px solid var(--line); border-radius:10px; padding:14px; background:#fbfcff; }}
    .pill {{ display:inline-block; padding:4px 10px; border-radius:999px; background:var(--blue-soft); color:var(--blue); font-size:12px; font-weight:700; margin-bottom:8px; }}
    .note {{ margin-top:14px; padding:12px 14px; border-left:4px solid var(--blue); background:var(--blue-soft); border-radius:8px; }}
    .files {{ margin-top:10px; }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); padding:10px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#f7f9fc; color:var(--muted); font-size:12px; }}
    .num {{ width:42px; color:var(--blue); }}
    .venue {{ margin-top:4px; color:var(--muted); font-size:12px; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; background:#f3f6fb; border:1px solid #dfe7f1; border-radius:4px; padding:1px 5px; font-size:12px; word-break:break-all; }}
    @media (max-width: 980px) {{
      .grid, .kpi, .priority-grid {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory 结构分析：结合近两年 10 篇顶会论文看怎么改</h1>
      <p class="muted">
        这页只做一件事：对着 EchoMemory 当前真代码，判断它已经具备哪些长期记忆骨架，
        再结合 10 篇高相关顶会论文，给出不依赖数据集关键词 hack 的通用升级方向。
      </p>
      <div class="kpi">
        <div><strong>5</strong><span class="muted">主结构层</span></div>
        <div><strong>10</strong><span class="muted">高相关顶会论文</span></div>
        <div><strong>5</strong><span class="muted">优先改进方向</span></div>
        <div><strong>4</strong><span class="muted">现成 nano 入口</span></div>
      </div>
      <div class="note">
        核心判断：<b>EchoMemory 已经不是 flat RAG</b>。它真正差的不是“再加一个检索器”，
        而是把 <code>topic_dossier / temporal_tree / graph / self-check / answerability</code> 这些已经存在的结构，收紧成一个更强的执行闭环。
      </div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>一句话结论</h2>
        <ul>
          <li>写入侧方向是对的：先 atom，再 organized，再 graph/episode。</li>
          <li>规划侧方向也是对的：已经有 query family，而不是所有问题走一套 recall。</li>
          <li>最缺的是执行层：很多 contract / self-check 还只是“看出来了”，没有“真生效”。</li>
          <li>如果要冲论文，最有说服力的主线是：<b>three-clock time + topic-centered middle layer + path-grounded graph + executive answerability</b>。</li>
        </ul>
      </div>
      <div class="card">
        <h2>为什么不建议数据集关键词 hack</h2>
        <ul>
          <li>LoCoMo 只是把长期记忆的共性问题放大了，不代表系统只该服务一个 benchmark。</li>
          <li>关键词 patch 往往掩盖的是结构问题：时间轴不清、topic 中层不稳、graph path 不够强、自检不执行。</li>
          <li>真正能泛化的改法，应该落在 memory schema、planner、contract、policy 上，而不是 query 字面表。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>当前结构拆解</h2>
      {render_structure()}
    </section>

    <section class="card">
      <h2>10 篇论文怎么映射到 EchoMemory</h2>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>论文</th>
            <th>关键启发</th>
            <th>最相关模块</th>
            <th>建议改法</th>
          </tr>
        </thead>
        <tbody>
          {render_papers()}
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>最值得先做的改进</h2>
      <div class="priority-grid">
        {render_priorities()}
      </div>
    </section>

    <section class="card">
      <h2>推荐的 nano 理解入口</h2>
      <table>
        <thead>
          <tr>
            <th style="width:20%">模块</th>
            <th style="width:42%">路径</th>
            <th>为什么看它</th>
          </tr>
        </thead>
        <tbody>
          {render_nano()}
        </tbody>
      </table>
      <div class="note">
        如果后面要写 CVPR 风格论文，我建议把主叙事收敛成两条：一条是 <b>time + topic + graph + gate</b> 的通用结构线，
        一条是 <b>visual ingest -> image_evidence -> grounded answerability</b> 的多模态增强线。这样既不靠 benchmark hack，也更像真正的方法论文。
      </div>
    </section>
  </div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
