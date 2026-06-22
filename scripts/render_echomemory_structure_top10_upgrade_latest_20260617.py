from __future__ import annotations

from pathlib import Path
from html import escape


ROOT = Path("/Users/chx/locomo-eval-web")
OUT = (
    ROOT
    / "web"
    / "static"
    / "generated-reports"
    / "echomemory_structure_top10_upgrade_latest_20260617.html"
)


PAPERS = [
    {
        "title": "LoCoMo: Evaluating Very Long-Term Conversational Memory of LLM Agents",
        "venue": "ACL 2024",
        "url": "https://aclanthology.org/2024.acl-long.747/",
        "takeaway": "真正难点不是检索到一点相关文本，而是跨 session 时间推理、人物关系串联、冲突信息处理。",
        "for_echomem": "要把 temporal / relation / multi-session 题型当成一等公民，不能只靠通用向量召回。",
    },
    {
        "title": "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory",
        "venue": "ICLR 2025 Poster",
        "url": "https://openreview.net/forum?id=pZiyCaVuti",
        "takeaway": "长期记忆系统除了 recall，还要评估 temporal reasoning、knowledge update、abstention。",
        "for_echomem": "Self-check 不能只是写诊断；它要真的影响 second pass / abstain / answerability gate。",
    },
    {
        "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models",
        "venue": "NeurIPS 2024 Poster",
        "url": "https://openreview.net/forum?id=hkujvAPVsg",
        "takeaway": "图结构不是展示层，而是高价值的 recall index；seed 质量决定后续图扩散质量。",
        "for_echomem": "GraphSeedPlanner 该从 topic/entity/event anchor 出发，而不只是 regex + vector hit。",
    },
    {
        "title": "In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents",
        "venue": "ACL 2025 Long",
        "url": "https://aclanthology.org/2025.acl-long.413/",
        "takeaway": "记忆写入不是 append 完就结束，应该有 prospective / retrospective consolidation。",
        "for_echomem": "Organized projector 需要从“批量 markdown merge”进化到“持续整合 topic / persona / change log”。",
    },
    {
        "title": "Flexibly Utilize Memory for Long-Term Conversation via a Fragment-then-Compose Framework",
        "venue": "EMNLP 2025",
        "url": "https://aclanthology.org/2025.emnlp-main.1069/",
        "takeaway": "先取碎片，再按问题类型重组，比一次性拼大摘要更稳。",
        "for_echomem": "L2 应加 fragment composition 层，减少 overview / dossier 把细粒度事实抹平。",
    },
    {
        "title": "3DLLM-Mem: Long-Term Spatial-Temporal Memory for Embodied 3D Large Language Model",
        "venue": "NeurIPS 2025 Poster",
        "url": "https://openreview.net/forum?id=q5QaTQcUbS",
        "takeaway": "working memory token 去选择 episodic memory，而不是把所有历史都平铺到 prompt 里。",
        "for_echomem": "未来多模态或长上下文版本，应该做 query-conditioned evidence routing，而不是统一拼接。",
    },
    {
        "title": "G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems",
        "venue": "NeurIPS 2025 Spotlight",
        "url": "https://openreview.net/forum?id=mmIAp3cVS0",
        "takeaway": "memory 需要分层，也需要 role / agent 归属，不然协作信息会串。",
        "for_echomem": "schema 要支持 speaker / role / ownership / source-agent，不该只当普通文本块。",
    },
    {
        "title": "MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents",
        "venue": "ICLR 2026 Poster",
        "url": "https://openreview.net/forum?id=XY8AaxDSLb",
        "takeaway": "不是存得越多越好；需要压缩、保留、丢弃、重写的生命周期策略。",
        "for_echomem": "应加入 salience / freshness / contradiction-aware consolidation，而不是无限增长。",
    },
    {
        "title": "H-Mem: Hybrid Multi-Dimensional Memory Management for Long-Context Conversational Agents",
        "venue": "EACL 2026 Long",
        "url": "https://aclanthology.org/2026.eacl-long.363/",
        "takeaway": "时间树和语义树并行存储，再由 mode controller 决定走哪条检索路径。",
        "for_echomem": "现在 temporal_tree 有了，但 semantic tree / mode controller 还不够强。",
    },
    {
        "title": "Look Back to Reason Forward: Revisitable Memory for Long-Context LLM Agents",
        "venue": "ICLR 2026 Poster",
        "url": "https://openreview.net/forum?id=1cymflI2Lh",
        "takeaway": "长程问答里，一次线性读完就丢，经常会误删后面真正有用的证据。",
        "for_echomem": "second pass 应升级为 revisitable retrieval，而不是只补一点 supporting evidence。",
    },
]


STRUCTURE = [
    {
        "name": "写入层",
        "summary": "消息进入后，先抽 atoms，再派生 organized memories，再同步时间块 / 图索引。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py",
        ],
        "strength": "已经不是纯 summary memory，而是有 atom -> organized -> graph 的分层。",
        "gap": "organized projector 仍偏批处理拼接，topic / persona / contradiction 还不够结构化。",
    },
    {
        "name": "组织层",
        "summary": "profile / overview / topic_dossier / entities / events / temporal blocks 由 atoms 继续派生。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
        ],
        "strength": "topic_dossier 和 temporal blocks 已经让它比单一摘要更像真正记忆系统。",
        "gap": "profile、overview、entity 还是 markdown merge 风格，难做 canonical state、差分更新和冲突消解。",
    },
    {
        "name": "规划层",
        "summary": "QueryPlanner 已区分 visual / relational / profile / longitudinal / temporal / experience。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py",
        ],
        "strength": "已经有 evidence contract 思想，不再是所有问题都走一套召回。",
        "gap": "mode controller 还偏规则触发，缺少更强的 query rewrite、fragment composition 和 dynamic routing。",
    },
    {
        "name": "检索层",
        "summary": "SearchService 走 L0 -> L1 -> L2，插入 graph-first、atom sidecar、text fallback、compound expansion。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/graph_seed_planner.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py",
        ],
        "strength": "检索面已经覆盖 vector / atom / graph / temporal / topic_dossier 多通道。",
        "gap": "graph seed 仍偏 heuristic；temporal resolver 还不够处理模糊时间、冲突时间和时间区间。",
    },
    {
        "name": "校验层",
        "summary": "Evidence contract、retrieval gating、self-check、second pass 已具雏形。",
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/retrieval_gating.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
        ],
        "strength": "这已经比很多 memory repo 更先进，因为它开始问“证据类型够不够”。",
        "gap": "目前 self-check 主要是 advisory；它没有成为真正的 answerability gate。",
    },
]


PRIORITIES = [
    {
        "level": "P0",
        "title": "把 self-check 从“诊断器”变成“执行器”",
        "why": "这条收益最大，也最贴近 LongMemEval / LoCoMo 的真实难点。现在代码能识别 missing event_time/path_grounding，但大多数时候只记录下来。",
        "change": [
            "当 temporal query 缺 event_time，仅有 mention_time 时，强制触发更窄的 event read-back，而不是直接放行。",
            "当 relational query 缺 path_grounding 时，强制 graph re-read 或直接降级 abstain。",
            "把 second pass 结果写回 structured decision，而不是只塞 budget_consumed 日志。",
        ],
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
        ],
        "paper_links": ["LongMemEval", "Look Back to Reason Forward", "LoCoMo"],
    },
    {
        "level": "P0",
        "title": "把 topic_dossier 做成真正的 semantic tree / canonical topic state",
        "why": "现在 topic_dossier 已经有了，但更像把 atoms 归到若干 dossier。它还不是一个能稳定承接 topic evolution 的语义层。",
        "change": [
            "给 dossier 增加 canonical topic id、aliases、linked entities、active time ranges。",
            "把 overview/profile 的增量 merge 改成 state update + provenance delta。",
            "对 topic 做 split / merge，避免一个 dossier 无限变胖。",
        ],
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
        ],
        "paper_links": ["H-Mem", "Reflective Memory Management", "Fragment-then-Compose"],
    },
    {
        "level": "P0",
        "title": "强化三时钟时间模型，而不是只做相对时间解析",
        "why": "现在已经有 query_time_anchor 和 event_time / mention_time 区分，这是好底子；但还不够支撑 yesterday / before / mentioned on / happened on 这类冲突题。",
        "change": [
            "引入 interval time、uncertain time、time conflict resolution。",
            "在 evidence contract 里显式区分 story-time hit 和 mention-time hit。",
            "回答前做 time-axis arbitration，禁止用 mention time 冒充 event time。",
        ],
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
        ],
        "paper_links": ["LoCoMo", "LongMemEval", "H-Mem"],
    },
    {
        "level": "P1",
        "title": "把 graph retrieval 从“可选加分项”升级为主 recall backbone 之一",
        "why": "现在 graph path 已有 planner，但 seed 构造还比较浅，导致很多时候图检索像补充证据，而不是主证据。",
        "change": [
            "seed 不仅来自 vector hit，还来自 dossier anchor、recent entities、time-filtered events。",
            "graph diffusion policy 引入 query family aware hop budget。",
            "把 path grounding 作为结果对象的一等字段，而不是 trace 里的可选信息。",
        ],
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/graph_seed_planner.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
        ],
        "paper_links": ["HippoRAG", "G-Memory"],
    },
    {
        "level": "P1",
        "title": "引入 fragment-then-compose 检索，而不是过度依赖 overview 大块文本",
        "why": "很多时间题和细节题掉分，根源是 overview / dossier 把事实压平了，后续又缺一个细粒度重组步骤。",
        "change": [
            "L2 先收 small fact/event fragments，再按 question schema 做 compose。",
            "compose 时优先保留时间、主体、宾语、否定、数量这些槽位。",
            "对 composed answer 计算 evidence completeness，而不是只看 top confidence。",
        ],
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/fusion/evidence_composer.py",
        ],
        "paper_links": ["Fragment-then-Compose", "LongMemEval"],
    },
    {
        "level": "P2",
        "title": "加入 salience / forgetting / memory lifecycle",
        "why": "长期运行时，不做生命周期控制会导致 memory 膨胀、topic 漂移、老旧结论污染新答案。",
        "change": [
            "为 atom / dossier / entity 增加 salience、freshness、stability、conflict_count。",
            "把 rewrite / archive / decay 做成后台 job，而不是每次检索都背全部历史。",
            "做 explicit superseded edges，支持 knowledge update。",
        ],
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py",
        ],
        "paper_links": ["MEM1", "Reflective Memory Management"],
    },
    {
        "level": "P2",
        "title": "把 multimodal evidence 变成原生节点，而不是将来再补",
        "why": "当前 QueryPlanner 已有 visual mode，这是好信号；但 ingestion 到 retrieval 的视觉证据链仍不完整。",
        "change": [
            "image / OCR / screenshot / region grounding 统一落到 image_evidence node。",
            "让 graph 和 temporal tree 能引用 multimodal node。",
            "让 self-check 能判断“图像证据有，但事实 grounding 不足”的情况。",
        ],
        "files": [
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
            "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
        ],
        "paper_links": ["3DLLM-Mem"],
    },
]


def _file_item(path: str) -> str:
    return f'<li><a href="file://{escape(path)}">{escape(path)}</a></li>'


def render() -> None:
    paper_rows = []
    for idx, paper in enumerate(PAPERS, start=1):
        paper_rows.append(
            f"""
            <tr>
              <td class="num">{idx}</td>
              <td><a href="{escape(paper["url"])}">{escape(paper["title"])}</a><div class="venue">{escape(paper["venue"])}</div></td>
              <td>{escape(paper["takeaway"])}</td>
              <td>{escape(paper["for_echomem"])}</td>
            </tr>
            """
        )

    structure_blocks = []
    for item in STRUCTURE:
        structure_blocks.append(
            f"""
            <section class="card">
              <div class="kicker">{escape(item["name"])}</div>
              <p class="summary">{escape(item["summary"])}</p>
              <div class="grid2">
                <div>
                  <h4>现在的优点</h4>
                  <p>{escape(item["strength"])}</p>
                </div>
                <div>
                  <h4>主要缺口</h4>
                  <p>{escape(item["gap"])}</p>
                </div>
              </div>
              <details>
                <summary>相关代码</summary>
                <ul>
                  {''.join(_file_item(f) for f in item["files"])}
                </ul>
              </details>
            </section>
            """
        )

    priority_blocks = []
    for item in PRIORITIES:
        priority_blocks.append(
            f"""
            <section class="card priority">
              <div class="row">
                <span class="pill {escape(item["level"].lower())}">{escape(item["level"])}</span>
                <h3>{escape(item["title"])}</h3>
              </div>
              <p>{escape(item["why"])}</p>
              <h4>建议改法</h4>
              <ul>
                {''.join(f"<li>{escape(x)}</li>" for x in item["change"])}
              </ul>
              <h4>优先看这些文件</h4>
              <ul>
                {''.join(_file_item(f) for f in item["files"])}
              </ul>
              <div class="paper-tags">
                {''.join(f'<span class="tag">{escape(x)}</span>' for x in item["paper_links"])}
              </div>
            </section>
            """
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EchoMemory 结构分析与 10 篇论文改进建议</title>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #121935;
      --panel2: #182141;
      --text: #e9eefc;
      --muted: #aeb8d6;
      --line: #2a3562;
      --blue: #7db3ff;
      --cyan: #67e8f9;
      --green: #8ee6b1;
      --yellow: #f3cf74;
      --red: #ff9b9b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #0b1020 0%, #0d1328 100%);
      color: var(--text);
      line-height: 1.6;
    }}
    .wrap {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 80px;
    }}
    .hero {{
      padding: 28px;
      border: 1px solid var(--line);
      background: radial-gradient(circle at top left, rgba(125,179,255,0.18), transparent 42%), var(--panel);
      border-radius: 12px;
      margin-bottom: 20px;
    }}
    h1, h2, h3, h4 {{ margin: 0 0 10px; }}
    h1 {{ font-size: 32px; }}
    h2 {{ font-size: 22px; margin-top: 28px; }}
    h3 {{ font-size: 18px; }}
    h4 {{ font-size: 15px; color: var(--cyan); }}
    p, li {{ color: var(--muted); }}
    .lead {{
      color: var(--text);
      font-size: 16px;
      margin-top: 10px;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }}
    .tag, .pill {{
      display: inline-flex;
      align-items: center;
      height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--text);
      background: rgba(255,255,255,0.04);
      font-size: 13px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .grid2 {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
    }}
    .kicker {{
      color: var(--blue);
      font-size: 13px;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .summary {{ color: var(--text); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: var(--panel);
    }}
    th, td {{
      padding: 14px 12px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      text-align: left;
    }}
    th {{ color: var(--cyan); font-size: 14px; }}
    td .venue {{ font-size: 12px; color: var(--yellow); margin-top: 6px; }}
    td.num {{ width: 44px; color: var(--blue); }}
    a {{ color: #9bc2ff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    details {{
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
    }}
    summary {{
      cursor: pointer;
      color: var(--text);
    }}
    .row {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .pill.p0 {{ background: rgba(255,155,155,0.12); color: var(--red); }}
    .pill.p1 {{ background: rgba(243,207,116,0.12); color: var(--yellow); }}
    .pill.p2 {{ background: rgba(142,230,177,0.12); color: var(--green); }}
    .callout {{
      border-left: 4px solid var(--cyan);
      padding: 12px 14px;
      background: rgba(103,232,249,0.06);
      color: var(--text);
      border-radius: 8px;
      margin: 16px 0 0;
    }}
    .paper-tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}
    @media (max-width: 900px) {{
      .grid, .grid2 {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
      table, thead, tbody, tr, th, td {{ display: block; }}
      thead {{ display: none; }}
      td {{ border-bottom: 1px solid var(--line); }}
      td.num {{ width: auto; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory 结构分析 + 顶会 10 篇论文改进建议</h1>
      <p class="lead">一句话结论：<b>EchoMemory 已经有了“像真正记忆系统”的骨架</b>，尤其是 atom、topic dossier、temporal tree、graph、evidence contract 这些分层都在；但它还差最后那几步关键闭环，尤其是 <b>self-check 执行化、真正的语义层 topic state、强一点的图召回、以及更严格的时间仲裁</b>。</p>
      <div class="meta">
        <span class="tag">代码分析基于 /Users/chx/Code/echomemory/echo_memory</span>
        <span class="tag">论文范围以 ACL / EMNLP / NeurIPS / ICLR / EACL 为主</span>
        <span class="tag">强调可泛化改进，不走数据集关键词 hack</span>
      </div>
      <div class="callout">
        如果只让我先做三件事，我会选：<b>1)</b> self-check 变成真正的 answerability gate，<b>2)</b> topic_dossier 升级成 canonical semantic tree，<b>3)</b> 把 graph seed + time arbitration 做扎实。
      </div>
    </section>

    <h2>1. 当前结构怎么看</h2>
    <div class="grid">
      {''.join(structure_blocks)}
    </div>

    <h2>2. 哪 10 篇论文最值得拿来压 EchoMemory</h2>
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>论文</th>
          <th>核心启发</th>
          <th>落到 EchoMemory 的含义</th>
        </tr>
      </thead>
      <tbody>
        {''.join(paper_rows)}
      </tbody>
    </table>

    <h2>3. 代码层面最值得改的点</h2>
    {''.join(priority_blocks)}

    <h2>4. 我对这套系统的判断</h2>
    <section class="card">
      <h3>它已经做对了什么</h3>
      <ul>
        <li>不是只做 summary memory，而是已经有 <b>atom -> organized -> temporal / graph</b> 的分层。</li>
        <li>QueryPlanner 已经在试着把问题类型和证据类型绑定，这条路是对的。</li>
        <li>Self-check / evidence contract 虽然还不够硬，但方向非常正确，很多 repo 甚至还没走到这一步。</li>
      </ul>
      <h3>它现在最像“半成品”的地方</h3>
      <ul>
        <li><b>self-check 还偏 advisory</b>，所以经常能识别问题，却没真正阻止错误答案继续流出。</li>
        <li><b>topic 语义层不够稳定</b>，很多信息还是大块合并，后面检索时难免被压平。</li>
        <li><b>图召回还没完全成为主骨架</b>，不少时候 graph 更像额外补充，而不是 first-class backbone。</li>
        <li><b>时间处理只做了第一步</b>，相对时间 anchor 有了，但 story-time / mention-time / write-time 冲突仲裁还不完整。</li>
      </ul>
      <h3>为什么我认为它有潜力</h3>
      <p>因为最难补的不是“再加一个向量索引”，而是结构观。EchoMemory 现在的代码里，已经能看到结构观：time、topic、graph、evidence type、second pass。接下来不是推倒重来，而是把这些半成形部件接成闭环。</p>
    </section>

    <h2>5. 参考代码入口</h2>
    <section class="card">
      <ul>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py">/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py</a></li>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py">/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py</a></li>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/graph_seed_planner.py">/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/graph_seed_planner.py</a></li>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py">/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py</a></li>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py">/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py</a></li>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/retrieval_gating.py">/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/retrieval_gating.py</a></li>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py">/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py</a></li>
        <li><a href="file:///Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py">/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py</a></li>
      </ul>
    </section>
  </div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    render()
