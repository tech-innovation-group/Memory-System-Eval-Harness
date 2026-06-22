#!/usr/bin/env python3
from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_structure_top10_paper_analysis_20260617.html"
)


@dataclass(frozen=True)
class ModuleNote:
    name: str
    path: str
    role: str
    issue: str


@dataclass(frozen=True)
class PaperNote:
    title: str
    venue: str
    link: str
    key_takeaway: str
    closest_module: str
    suggested_change: str


MODULES = [
    ModuleNote(
        name="OrganizedProjector",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
        role="把 atom 投影成 profile / overview / topic_dossier / events / temporal_tree。",
        issue="topic_dossier 目前主要靠 subject/object 浅分桶；temporal_tree 主要是年月日桶，不是更强的时间推理结构。",
    ),
    ModuleNote(
        name="QueryPlanner",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py",
        role="把问题路由到 temporal / relational / profile / longitudinal / visual 等查询模式。",
        issue="已经有 query family 概念，但 reader 选择仍偏启发式，缺更稳定的 typed planning 和更强的 retry policy。",
    ),
    ModuleNote(
        name="SearchService",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
        role="总调度入口，负责分层检索、二次补召回、budget 和结果拼装。",
        issue="保留了 3 秒文本兜底扫描和 max_files=200，说明全文扫树仍是兜底主力之一，这对规模化很危险。",
    ),
    ModuleNote(
        name="Evidence Contract",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py",
        role="定义某类问题需要哪些证据类型，比如 temporal 题需要 event_time、relational 题需要 path grounding。",
        issue="契约已经成型，但仍主要用来做 coverage 诊断，还没有彻底进入 answer gating 主流程。",
    ),
    ModuleNote(
        name="SelfCheckPolicy",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
        role="根据证据覆盖情况，给出 expand_supporting_evidence / consider_unknown 等建议。",
        issue="现在大多还是 advisory 角色，离“强执行 gate”只差一步，但这一步正好最影响准确率。",
    ),
    ModuleNote(
        name="GraphSeedPlanner",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/graph_seed_planner.py",
        role="根据 query、vector hits 和实体名去找图扩散的 seed 节点。",
        issue="seed 生成还比较启发式；图检索已经接进来了，但还没成为真正的 backbone。",
    ),
    ModuleNote(
        name="MemoryGraphSync",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py",
        role="把 atom/fact/event/entity 映射成图节点和边。",
        issue="已经具备 temporal graph 的雏形，但时间边、状态变化边、路径可解释性还不够强。",
    ),
    ModuleNote(
        name="TemporalQueryResolver",
        path="/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py",
        role="把 yesterday / last week / 本月 这类相对时间表达对齐到 query_time_anchor。",
        issue="这是很好的起点，但目前还只是轻量规则解析，不是更完整的相对时间与事件时间对齐层。",
    ),
]


PAPERS = [
    PaperNote(
        title="LoCoMo: Evaluating Very Long-Term Conversational Memory of LLM Agents",
        venue="ACL 2024",
        link="https://arxiv.org/abs/2402.17753",
        key_takeaway="长程多 session 记忆里，真正难的是时间、因果和跨会话一致性，而不是简单记住一个事实。",
        closest_module="TemporalQueryResolver + OrganizedProjector",
        suggested_change="把 temporal_tree 从日期桶升级成 story time / mention time / write time 三轴结构，并让 planner 对时间题强制走这层。",
    ),
    PaperNote(
        title="LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory",
        venue="ICLR 2025",
        link="https://arxiv.org/abs/2410.10813",
        key_takeaway="长时记忆系统可以拆成 indexing、retrieval、reading 三阶段，且 temporal reasoning、abstention 都要单独考。",
        closest_module="QueryPlanner + SearchService + Evidence Contract",
        suggested_change="把当前 query family 再往前推进一步：显式区分 indexing strategy、retrieval plan、answer gate，而不是只在检索层修补。",
    ),
    PaperNote(
        title="RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval",
        venue="2024",
        link="https://arxiv.org/abs/2401.18059",
        key_takeaway="树状中层表示能把 raw evidence 和 high-level summary 连接起来，减少纯扁平召回的噪声。",
        closest_module="OrganizedProjector",
        suggested_change="让 overview / topic_dossier / temporal_tree 成为真正的 coarse-to-fine 树，而不是几个平行目录。",
    ),
    PaperNote(
        title="HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models",
        venue="NeurIPS 2024",
        link="https://arxiv.org/abs/2405.14831",
        key_takeaway="图式联想召回对多跳、多实体、跨片段问题很重要，尤其适合纯向量容易漏掉的关联。",
        closest_module="GraphSeedPlanner + SearchService",
        suggested_change="把 graph recall 从 second-pass 补充，提升成 relational/compare/why 类问题的主检索 backbone。",
    ),
    PaperNote(
        title="Zep: A Temporal Knowledge Graph Architecture for Agent Memory",
        venue="2025",
        link="https://arxiv.org/abs/2501.13956",
        key_takeaway="时间感知图不是简单 entity graph，而是要把历史关系和状态变化写进图里。",
        closest_module="MemoryGraphSync",
        suggested_change="在 graph sync 中增加状态转移和显式时间约束边，不只是 has_fact / about / involves 这类静态边。",
    ),
    PaperNote(
        title="From RAG to Memory: Non-Parametric Continual Learning for Large Language Models",
        venue="2025",
        link="https://arxiv.org/abs/2502.14802",
        key_takeaway="memory 不该只是一轮写入一次读取，而是要持续 consolidation、更新和演化。",
        closest_module="Ingest pipeline + background consolidation",
        suggested_change="把 hot-path ingest 和 cold-path organization/repair 拆得更彻底，让 commit 成为起点而不是终点。",
    ),
    PaperNote(
        title="MemOS: An Operating System for Memory-Augmented Generation in Large Language Models",
        venue="2025",
        link="https://arxiv.org/abs/2505.22101",
        key_takeaway="memory 应该被视为一等资源，要有表示、组织、治理和生命周期约束。",
        closest_module="whole echomem architecture",
        suggested_change="把 atom / dossier / tree / graph / policy 明确声明成系统 contract，并补上 readiness、integrity、governance 元数据。",
    ),
    PaperNote(
        title="LightMem: Lightweight and Efficient Memory-Augmented Generation",
        venue="2025",
        link="https://arxiv.org/abs/2510.18866",
        key_takeaway="在线路径要轻，离线路径要重；快写入和慢整理分离，才能兼顾效率和质量。",
        closest_module="SearchService + ingest workers",
        suggested_change="把全文 tree scan 继续边缘化，把轻量在线召回固定预算化，把较重的 topic/graph 整理放到离线。",
    ),
    PaperNote(
        title="TiMem: Temporal-Hierarchical Memory Consolidation for Long-Horizon Conversational Agents",
        venue="2026",
        link="https://arxiv.org/abs/2601.02845",
        key_takeaway="时间应是第一组织原则；同时需要 temporal hierarchy、semantic-guided consolidation 和 complexity-aware recall。",
        closest_module="TemporalQueryResolver + OrganizedProjector + QueryPlanner",
        suggested_change="让 temporal_tree 不止做日期分桶，还要支持时间跨度、相对时间和复杂度感知的 recall 路由。",
    ),
    PaperNote(
        title="Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection",
        venue="ICLR 2024",
        link="https://arxiv.org/abs/2310.11511",
        key_takeaway="retrieval 是否够、回答是否该 abstain，不能只靠一次性拼 prompt，要有反思与 critique 环。",
        closest_module="Evidence Contract + SelfCheckPolicy",
        suggested_change="把现在的 self_check 从 advisory 升成强制 gate：缺哪类证据就补哪类；补不出来就保守答。",
    ),
]


def esc(text: str) -> str:
    return html.escape(str(text))


def render_module_cards() -> str:
    cards = []
    for item in MODULES:
        cards.append(
            f"""
            <div class="module-card">
              <h3>{esc(item.name)}</h3>
              <p><strong>作用：</strong>{esc(item.role)}</p>
              <p><strong>当前短板：</strong>{esc(item.issue)}</p>
              <p class="path">{esc(item.path)}</p>
            </div>
            """
        )
    return "".join(cards)


def render_paper_rows() -> str:
    rows = []
    for idx, item in enumerate(PAPERS, start=1):
        rows.append(
            f"""
            <tr>
              <td>{idx}</td>
              <td><a href="{esc(item.link)}" target="_blank" rel="noreferrer">{esc(item.title)}</a><br /><span class="muted">{esc(item.venue)}</span></td>
              <td>{esc(item.key_takeaway)}</td>
              <td>{esc(item.closest_module)}</td>
              <td>{esc(item.suggested_change)}</td>
            </tr>
            """
        )
    return "".join(rows)


def render() -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory 结构分析与 Top10 论文改进建议</title>
  <style>
    :root {{
      --bg:#f5f7fb;
      --panel:#ffffff;
      --text:#132033;
      --muted:#637083;
      --line:#dde4ee;
      --blue:#2563eb;
      --blue-soft:#eff6ff;
      --amber:#b45309;
      --green:#0f766e;
      --red:#b42318;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      background:var(--bg);
      color:var(--text);
      font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;
    }}
    .wrap {{
      max-width:1280px;
      margin:0 auto;
      padding:28px 20px 56px;
    }}
    .hero,.card {{
      background:var(--panel);
      border:1px solid var(--line);
      border-radius:8px;
      padding:22px 24px;
      margin-bottom:16px;
    }}
    h1,h2,h3 {{
      margin:0 0 12px;
      line-height:1.25;
    }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    h3 {{ font-size:16px; }}
    p,li,td,th {{ font-size:14px; }}
    .muted {{ color:var(--muted); }}
    .kpis {{
      display:grid;
      grid-template-columns:repeat(4,minmax(0,1fr));
      gap:12px;
      margin-top:14px;
    }}
    .kpi {{
      border:1px solid var(--line);
      border-radius:8px;
      padding:12px 14px;
      background:#fbfcfe;
    }}
    .kpi strong {{
      display:block;
      font-size:22px;
      margin-bottom:4px;
    }}
    .grid-2 {{
      display:grid;
      grid-template-columns:repeat(2,minmax(0,1fr));
      gap:16px;
    }}
    .modules {{
      display:grid;
      grid-template-columns:repeat(2,minmax(0,1fr));
      gap:14px;
    }}
    .module-card {{
      border:1px solid var(--line);
      border-radius:8px;
      padding:14px;
      background:#fbfcfe;
    }}
    .path {{
      margin-top:10px;
      padding:8px 10px;
      border-radius:6px;
      background:#f3f6fb;
      color:#415066;
      font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
      font-size:12px;
      overflow-wrap:anywhere;
    }}
    table {{
      width:100%;
      border-collapse:collapse;
      font-size:13px;
    }}
    th,td {{
      border-top:1px solid var(--line);
      padding:10px 8px;
      text-align:left;
      vertical-align:top;
    }}
    th {{
      background:#fafbfc;
      color:var(--muted);
      font-size:12px;
    }}
    ul,ol {{
      margin:8px 0 0 20px;
      padding:0;
    }}
    a {{
      color:var(--blue);
      text-decoration:none;
    }}
    a:hover {{ text-decoration:underline; }}
    code {{
      background:#f2f4f8;
      padding:1px 4px;
      border-radius:4px;
      font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
      font-size:12px;
    }}
    .note {{
      border-left:4px solid var(--blue);
      background:var(--blue-soft);
      padding:12px 14px;
      border-radius:6px;
      margin-top:12px;
    }}
    .callout {{
      display:grid;
      grid-template-columns:repeat(3,minmax(0,1fr));
      gap:12px;
    }}
    .callout > div {{
      border:1px solid var(--line);
      border-radius:8px;
      padding:14px;
      background:#fbfcfe;
    }}
    @media (max-width: 980px) {{
      .kpis,.grid-2,.modules,.callout {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory 结构分析，结合 10 篇代表性论文看怎么改</h1>
      <p class="muted">
        这份分析不是泛泛聊论文，而是直接对着当前 <code>echomem</code> 的真实代码结构看：
        它已经具备哪些“长期记忆系统”的骨架，短板卡在哪，参考近两年高相关论文后，最值得优先做哪些通用改造。
      </p>
      <div class="kpis">
        <div class="kpi"><strong>8</strong><span class="muted">关键模块已成型</span></div>
        <div class="kpi"><strong>10</strong><span class="muted">代表性论文直接映射到代码</span></div>
        <div class="kpi"><strong>4</strong><span class="muted">最该优先补强的结构方向</span></div>
        <div class="kpi"><strong>0</strong><span class="muted">不建议数据集关键词 hack</span></div>
      </div>
    </section>

    <section class="grid-2">
      <div class="card">
        <h2>先说结论</h2>
        <ol>
          <li><strong>echomem 已经不是简单的 RAG。</strong> 它已经有 atom、organized memory、topic dossier、temporal tree、graph、planner、evidence contract、self check。</li>
          <li><strong>真正缺的不是“再多一层”。</strong> 而是把这些层收紧成一个更强的检索决策闭环。</li>
          <li><strong>最影响效果的四个方向：</strong> 时间结构、主题中层、图检索 backbone、answer-time gate。</li>
          <li><strong>最不该做的事：</strong> 针对 LoCoMo 或某个数据集加关键词表去 patch。</li>
        </ol>
      </div>
      <div class="card">
        <h2>我对当前结构的判断</h2>
        <ul>
          <li><strong>写入侧：</strong> atom-first 很对，说明系统知道要把原始对话压成可治理记忆。</li>
          <li><strong>组织侧：</strong> profile / overview / topic_dossier / temporal_tree 的方向是对的，但 topic 和 time 还不够稳。</li>
          <li><strong>图侧：</strong> graph 已经接进主流程，不是摆设；只是现在仍偏 second-pass。</li>
          <li><strong>答题侧：</strong> evidence contract 和 self check 已经出现，这一步非常关键，说明系统开始从“检索到了吗”走向“这些证据够不够回答”。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>核心代码骨架</h2>
      <div class="modules">
        {render_module_cards()}
      </div>
    </section>

    <section class="card">
      <h2>10 篇代表性论文，分别在提醒 echomem 改什么</h2>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>论文</th>
            <th>核心启发</th>
            <th>最相关模块</th>
            <th>对应改法</th>
          </tr>
        </thead>
        <tbody>
          {render_paper_rows()}
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>如果现在就开始改，我建议按这个顺序</h2>
      <div class="callout">
        <div>
          <h3>1. 先把 Self Check 变成强执行 Gate</h3>
          <p>现在 <code>self_check.py</code> 已经能识别 temporal anchor 不够、graph path 不够、event_time 不够，这是非常好的基础。下一步就是别只报诊断，要真正驱动 targeted re-retrieve 和 abstain。</p>
        </div>
        <div>
          <h3>2. 重做 Topic Dossier</h3>
          <p>当前 <code>_infer_topic_key()</code> 基本是 subject/object slug。这个层如果不稳，跨 session 的主题连续性就会散掉。应该升级成实体归一化 + 时间窗口 + 关系聚类的主题文档。</p>
        </div>
        <div>
          <h3>3. 让 Temporal Tree 真正支持时间推理</h3>
          <p>现在 temporal tree 还是“年/月/日桶”。需要再往前走一步，把相对时间、事件发生时间、提到时间、写入时间都正式纳入 recall 和 filtering。</p>
        </div>
      </div>
      <div class="note">
        这三步都是<strong>通用结构改造</strong>，不会把系统绑死在某一个 benchmark 上。
      </div>
    </section>

    <section class="card">
      <h2>更具体一点：每个方向可以怎么落地</h2>
      <ol>
        <li><strong>时间：</strong>在 <code>OrganizedProjector</code> 中给 event 和 dossier 增加更稳定的时间区间表示；在 <code>TemporalQueryResolver</code> 中保留相对时间解析，但把解析结果继续传给 planner 和 graph filter。</li>
        <li><strong>主题：</strong>给 <code>topic_dossier</code> 增加“主题签名”而不是只用 subject/object；签名应来自实体集合、关系模式、时间窗口和持续性。</li>
        <li><strong>图：</strong>把 <code>GraphSeedPlanner</code> 从“有条件就补一下”改成 relational/compare/why 类问题的默认主路径；同时要求 path grounding 明确写回 trace。</li>
        <li><strong>答题策略：</strong>让 <code>evidence_contract</code> 成为最终回答前的强门槛。没有 event_time、没有 path grounding、没有 required type，就继续补召回或保守答。</li>
        <li><strong>生命周期：</strong>把 ingest、projection、graph sync、vector sync、repair、readiness 分成明确阶段，并给每阶段打状态。这样 memory 就真的像 MemOS 说的“一等资源”。</li>
      </ol>
    </section>

    <section class="card">
      <h2>一句话判断</h2>
      <p>
        <strong>echomem 最有价值的地方</strong>，不是它已经做完了，而是它已经长出了一个很像“下一代 agent memory system”的骨架：
        <code>raw stream → atom → organized mid-layer → temporal/tree + graph → planner → evidence contract → self check</code>。
        现在最值得做的，是把这些骨架之间的约束补齐，让它从“有很多好部件”变成“有强闭环的系统”。
      </p>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    OUT_HTML.write_text(render(), encoding="utf-8")
    print(OUT_HTML)


if __name__ == "__main__":
    main()
