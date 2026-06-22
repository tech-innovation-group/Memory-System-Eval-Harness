#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


OUT = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_cvpr_related_work_table_20260617.html"
)


@dataclass(frozen=True)
class Row:
    cluster: str
    title: str
    venue: str
    link: str
    relevance: str
    gap_for_echomemory: str


ROWS: list[Row] = [
    Row("Benchmarks", "LoCoMo", "ACL 2024", "https://aclanthology.org/2024.acl-long.747/", "Long conversational memory under temporal, relational, and multi-hop pressure.", "Pushes EchoMemory to separate query families instead of treating memory as one pooled retrieval task."),
    Row("Benchmarks", "LongMemEval", "ICLR 2025", "https://openreview.net/forum?id=pZiyCaVuti", "Lifecycle-aware memory benchmark; retrieval and answerability pressures are separable.", "Motivates evidence contracts, readiness, and answer-time self-check."),
    Row("Benchmarks", "LongMemEval-V2", "2026 benchmark line", "https://arxiv.org/abs/2605.12493", "Extends memory pressure toward richer agent-style tasks.", "Suggests EchoMemory should evaluate more than persona recall and include operational memory states."),

    Row("Temporal / hierarchy", "RAPTOR", "2024", "https://arxiv.org/abs/2401.18059", "Hierarchical abstraction is a retrieval substrate rather than a cosmetic summary layer.", "Supports temporal tree and organized middle layers."),
    Row("Temporal / hierarchy", "MemoRAG", "2024", "https://arxiv.org/abs/2409.05591", "Coarse memory can guide fine retrieval instead of replacing it.", "Matches EchoMemory’s L0/L1/L2 layered retrieval direction."),
    Row("Temporal / hierarchy", "TiMem", "2026", "https://arxiv.org/abs/2601.02845", "Temporal hierarchy should be a first-class memory organization principle.", "Points toward stronger write-time use of story-time, not only query-time correction."),
    Row("Temporal / hierarchy", "Reflective Memory Management for Long-term Personalized Dialogue Agents", "ACL 2025", "https://aclanthology.org/2025.acl-long.413/", "Memory should be consolidated and revised, not just appended.", "Highlights the need for stronger middle-layer maintenance and retrospective updates."),
    Row("Temporal / hierarchy", "Fragment-then-Compose for Long-Term Conversation", "EMNLP 2025", "https://aclanthology.org/2025.emnlp-main.1069/", "Retrieval should return fragments that are composed according to the question’s demanded slots.", "Resonates with family-aware readers and typed second pass."),

    Row("Graph memory", "HippoRAG", "NeurIPS 2024", "https://openreview.net/forum?id=hkujvAPVsg", "Graph can be a primary recall backbone, not a sidecar.", "Supports graph-first retrieval for relational questions."),
    Row("Graph memory", "GraphReader", "2024", "https://arxiv.org/abs/2406.14550", "Graph retrieval is stronger when modeled as staged exploration rather than a one-shot context dump.", "Encourages explicit path-grounding and hop traces."),
    Row("Graph memory", "LEGO-GraphRAG", "2024", "https://arxiv.org/abs/2411.05844", "Graph retrieval benefits from modular decomposition.", "Matches EchoMemory’s split among seeding, diffusion, and answer-time checking."),
    Row("Graph memory", "Zep temporal KG line", "2025", "https://arxiv.org/abs/2501.13956", "Temporal graph memory benefits from explicit event/entity/time structure.", "Aligns with richer graph edges and temporal state transitions."),

    Row("Memory systems", "Mem0", "2025", "https://arxiv.org/abs/2504.19413", "Production-ready memory needs selective extraction and usable consolidation.", "Encourages disciplined write-path design instead of monolithic indexing."),
    Row("Memory systems", "LightMem", "2025", "https://arxiv.org/abs/2510.18866", "Online-light and offline-heavy memory paths can coexist.", "Reinforces fast-ingest versus full-consolidation separation."),
    Row("Memory systems", "MemOS", "2025", "https://arxiv.org/abs/2505.22101", "Memory should be governed as a system resource.", "Supports EchoMemory’s readiness plane and service-container view."),
    Row("Memory systems", "Infini Memory", "2026", "https://arxiv.org/abs/2606.10677", "Maintainable topic documents help bridge flat facts and global summaries.", "Directly points to strengthening topic dossier as a stable middle layer."),

    Row("Answer-time policy", "Self-RAG", "NeurIPS 2023", "https://openreview.net/forum?id=hSyW5go0v8", "Retrieval should be self-reflective rather than unconditional.", "Provides the conceptual basis for self-check and answerability gating."),
    Row("Answer-time policy", "Mem-T", "2026", "https://arxiv.org/abs/2601.23014", "Memory actions can be treated as explicit policy decisions.", "Suggests logging expand / abstain / answer actions before learning them."),
    Row("Answer-time policy", "D-MEM", "2026", "https://arxiv.org/abs/2603.14597", "Agentic memory can be routed by learned policy signals.", "Supports moving from advisory self-check to executive control."),

    Row("Multimodal memory", "MIRIX", "2025", "https://arxiv.org/abs/2507.07957", "Typed multimodal memory planes matter.", "Supports image_evidence as a first-class node type."),
    Row("Multimodal memory", "3DLLM-Mem line", "NeurIPS 2025", "https://openreview.net/forum?id=q5QaTQcUbS", "Visual memory requires structural grounding, not OCR alone.", "Points toward image nodes with owner/event/fact links rather than late visual patching."),
]


def main() -> None:
    current_cluster = None
    body_parts: list[str] = []
    for row in ROWS:
        if row.cluster != current_cluster:
            current_cluster = row.cluster
            body_parts.append(
                f'<tr class="cluster"><td colspan="5">{row.cluster}</td></tr>'
            )
        body_parts.append(
            "<tr>"
            f'<td><a href="{row.link}" target="_blank" rel="noreferrer">{row.title}</a><div class="venue">{row.venue}</div></td>'
            f"<td>{row.relevance}</td>"
            f"<td>{row.gap_for_echomemory}</td>"
            "</tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory CVPR Related Work Table</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1220px; margin:0 auto; padding:28px 20px 54px; }}
    .hero,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px 24px; margin-bottom:16px; }}
    h1,h2 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    p {{ margin:0 0 10px; }}
    .muted {{ color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; }}
    .cluster td {{ background:#f7f9fd; color:#1f3f82; font-weight:700; }}
    .venue {{ margin-top:4px; color:var(--muted); font-size:12px; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM Related Work Main Table</h1>
      <p class="muted">
        这页按更接近论文正文的方式，把近期最相关的工作压成几个簇：benchmark pressure、temporal hierarchy、graph memory、memory systems、answer-time policy、multimodal memory。
        它不是完整 bibliography，而是“正文里最该出现的那张 related-work 主表”。
      </p>
    </section>
    <section class="card">
      <h2>Paper-facing Summary</h2>
      <table>
        <thead>
          <tr>
            <th style="width:28%">Work</th>
            <th style="width:36%">What it contributes</th>
            <th>Why it matters for EchoMemory</th>
          </tr>
        </thead>
        <tbody>
          {''.join(body_parts)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
