#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_generic_dual_backbone_explained_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_generic_dual_backbone_explained_20260615.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def shift_day(ymd: str, delta: int) -> str:
    dt = datetime.fromisoformat(ymd)
    return (dt + timedelta(days=delta)).strftime("%Y-%m-%d")


def infer_event_time(text: str, write_time: str) -> str:
    explicit = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if explicit:
        return explicit.group(1)
    anchor = write_time[:10]
    lowered = text.lower()
    if "yesterday" in lowered:
        return shift_day(anchor, -1)
    if "last week" in lowered:
        return shift_day(anchor, -7)
    if "two days ago" in lowered:
        return shift_day(anchor, -2)
    if "tomorrow" in lowered:
        return shift_day(anchor, 1)
    return anchor


def token_overlap(query: str, content: str) -> float:
    q = set(re.findall(r"[A-Za-z_]+|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", query.lower()))
    c = set(re.findall(r"[A-Za-z_]+|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", content.lower()))
    if not q:
        return 0.0
    return round(len(q & c) / max(len(q), 1), 3)


@dataclass
class Observation:
    obs_id: str
    role: str
    modality: str
    content: str
    mention_time: str
    write_time: str
    event_time: str
    caption: str = ""
    ocr: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    obj: str
    statement: str
    event_time: str
    mention_time: str
    write_time: str
    source_obs_id: str


@dataclass
class TemporalBlock:
    block_id: str
    level: str
    key: str
    content: str
    source_refs: list[str] = field(default_factory=list)


@dataclass
class GraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str
    evidence: str


@dataclass
class Readiness:
    messages_persisted: bool = False
    atoms_ready: bool = False
    tree_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False


@dataclass
class QueryPlan:
    family: str
    primary_reader: str
    supporting_readers: list[str]
    required_layers: list[str]
    note: str


class GenericDualBackboneNano:
    """
    A generic EchoMemory-style teaching nano.

    It keeps exactly six ideas:
    1. append-only stream
    2. atom formation
    3. three-clock time
    4. temporal-tree backbone
    5. relation/image graph backbone
    6. typed evidence contract + second pass

    The design goal is explanation, not benchmark optimization.
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.blocks: list[TemporalBlock] = []
        self.edges: list[GraphEdge] = []
        self.readiness = Readiness()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def append(
        self,
        *,
        role: str,
        content: str,
        write_time: str,
        modality: str = "text",
        caption: str = "",
        ocr: str = "",
    ) -> None:
        merged = content.strip() if modality == "text" else "\n".join(x for x in [caption.strip(), ocr.strip()] if x)
        obs = Observation(
            obs_id=f"obs-{len(self.observations):03d}",
            role=role,
            modality=modality,
            content=merged,
            mention_time=write_time,
            write_time=write_time,
            event_time=infer_event_time(merged, write_time),
            caption=caption.strip(),
            ocr=ocr.strip(),
        )
        self.observations.append(obs)
        self.readiness.messages_persisted = True
        self.readiness.atoms_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    def build(self) -> None:
        self.atoms = self._extract_atoms()
        self.readiness.atoms_ready = True
        self.blocks = self._build_temporal_blocks()
        self.readiness.tree_ready = bool(self.blocks)
        self.edges = self._build_graph()
        self.readiness.graph_ready = bool(self.edges)
        self.readiness.qa_ready = (
            self.readiness.messages_persisted
            and self.readiness.atoms_ready
            and self.readiness.tree_ready
            and self.readiness.graph_ready
        )

    # ------------------------------------------------------------------
    # Query path
    # ------------------------------------------------------------------

    def plan(self, query: str) -> QueryPlan:
        q = query.lower().strip()
        if re.search(r"\b(when|date|time|yesterday|last week|before|after)\b|什么时候|日期|时间|昨天|上周|之前|之后", q):
            return QueryPlan(
                family="temporal",
                primary_reader="temporal_tree",
                supporting_readers=["graph"],
                required_layers=["temporal_tree", "event", "event_time"],
                note="chronology-heavy queries should enter via time structure first",
            )
        if re.search(r"\b(who|relationship|related|introduced|helped|worked with|married)\b|谁|关系|相关|介绍|帮助|合作|结婚", q):
            return QueryPlan(
                family="relational",
                primary_reader="graph",
                supporting_readers=["temporal_tree"],
                required_layers=["graph", "fact", "path_grounding"],
                note="relation-heavy queries need graph evidence and path grounding",
            )
        if re.search(r"\b(photo|image|screenshot|shown|address|visual)\b|图片|截图|照片|图里|图中|地址", q):
            return QueryPlan(
                family="visual",
                primary_reader="graph",
                supporting_readers=["temporal_tree"],
                required_layers=["image_evidence", "fact"],
                note="visual queries need first-class image evidence, not only OCR-like text",
            )
        return QueryPlan(
            family="general",
            primary_reader="mixed",
            supporting_readers=[],
            required_layers=["fact"],
            note="general factual queries can use both backbones",
        )

    def search(self, query: str, query_time: str) -> dict[str, Any]:
        plan = self.plan(query)
        if not self.readiness.qa_ready:
            return {
                "query": query,
                "query_time": query_time,
                "plan": asdict(plan),
                "readiness": asdict(self.readiness),
                "allowed_to_answer": False,
                "note": "messages are persisted but memory is not QA-ready yet",
                "hits": [],
                "coverage": {},
            }

        hits: list[dict[str, Any]] = []
        readers_used: list[str] = []

        def run_reader(reader: str, bonus: float) -> None:
            if reader == "temporal_tree":
                hits.extend(self._search_tree(query, query_time, bonus=bonus))
            elif reader == "graph":
                hits.extend(self._search_graph(query, bonus=bonus))
            readers_used.append(reader)

        if plan.primary_reader == "temporal_tree":
            run_reader("temporal_tree", 0.08)
        elif plan.primary_reader == "graph":
            run_reader("graph", 0.08)
        else:
            run_reader("temporal_tree", 0.04)
            run_reader("graph", 0.04)

        coverage = self._coverage(plan.required_layers, hits)
        second_pass_readers: list[str] = []
        if coverage["missing"]:
            for reader in plan.supporting_readers:
                if reader in readers_used:
                    continue
                second_pass_readers.append(reader)
                run_reader(reader, 0.03)
                coverage = self._coverage(plan.required_layers, hits)
                if not coverage["missing"]:
                    break

        hits = sorted(
            hits,
            key=lambda item: (float(item["score"]), str(item.get("event_time", ""))),
            reverse=True,
        )[:8]
        return {
            "query": query,
            "query_time": query_time,
            "plan": asdict(plan),
            "readiness": asdict(self.readiness),
            "allowed_to_answer": True,
            "readers_used": readers_used,
            "second_pass_readers": second_pass_readers,
            "coverage": coverage,
            "hits": hits,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for obs in self.observations:
            if obs.role != "user":
                continue
            if obs.modality == "image":
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type="image_evidence",
                        subject="image_observation",
                        predicate="shows",
                        obj=(obs.ocr or obs.caption or obs.content)[:96],
                        statement=obs.content,
                        event_time=obs.event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                    )
                )
                continue

            patterns = [
                (r"([A-Z][a-z]+)\s+joined\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "joined", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+left\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "left", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+signed\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "signed", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+helped\s+([A-Z][a-z]+)\s+prepare\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "{0}", "helped", "{1}::{2}", "{3}"),
                (r"([A-Z][a-z]+)\s+married\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "{0}", "married_to", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+presented\s+the\s+(.+?)\s+yesterday", "event", "{0}", "presented", "{1}", ""),
                (r"([A-Z][a-z]+)\s+had\s+the\s+(.+?)\s+last week", "event", "{0}", "had", "{1}", ""),
            ]
            matched = False
            for pattern, atom_type, subj_t, pred_t, obj_t, evt_t in patterns:
                m = re.search(pattern, obs.content)
                if not m:
                    continue
                g = m.groups()
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=atom_type,
                        subject=subj_t.format(*g).strip(),
                        predicate=pred_t.format(*g).strip(),
                        obj=obj_t.format(*g).strip(),
                        statement=obs.content,
                        event_time=(evt_t.format(*g).strip() if evt_t else obs.event_time) or obs.event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                    )
                )
                matched = True
            if matched:
                continue
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(atoms):03d}",
                    atom_type="fact",
                    subject="unknown",
                    predicate="mentions",
                    obj=obs.content[:80],
                    statement=obs.content,
                    event_time=obs.event_time,
                    mention_time=obs.mention_time,
                    write_time=obs.write_time,
                    source_obs_id=obs.obs_id,
                )
            )
        return atoms

    def _build_temporal_blocks(self) -> list[TemporalBlock]:
        blocks: list[TemporalBlock] = []
        grouped: dict[str, list[Atom]] = {}
        for atom in self.atoms:
            key = atom.event_time[:10]
            grouped.setdefault(key, []).append(atom)
        for key, atoms in sorted(grouped.items()):
            content = "\n".join(f"- {atom.event_time}: {atom.statement}" for atom in atoms)
            blocks.append(
                TemporalBlock(
                    block_id=f"day:{key}",
                    level="day",
                    key=key,
                    content=content,
                    source_refs=[atom.atom_id for atom in atoms],
                )
            )
        return blocks

    def _build_graph(self) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for atom in self.atoms:
            if atom.subject and atom.obj:
                edges.append(
                    GraphEdge(
                        edge_id=f"{atom.atom_id}:fact",
                        source_id=atom.subject,
                        target_id=atom.obj,
                        relation_type=atom.predicate,
                        evidence=atom.statement,
                    )
                )
            if atom.event_time:
                edges.append(
                    GraphEdge(
                        edge_id=f"{atom.atom_id}:time",
                        source_id=atom.statement[:48],
                        target_id=atom.event_time,
                        relation_type="happened_at",
                        evidence=atom.atom_id,
                    )
                )
            if atom.atom_type == "image_evidence":
                edges.append(
                    GraphEdge(
                        edge_id=f"{atom.atom_id}:image",
                        source_id="image_evidence",
                        target_id=atom.obj,
                        relation_type="shows",
                        evidence=atom.statement,
                    )
                )
        return edges

    def _search_tree(self, query: str, query_time: str, bonus: float) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for block in self.blocks:
            score = token_overlap(query, block.content) + bonus
            if "yesterday" in query.lower() and block.key == shift_day(query_time[:10], -1):
                score += 0.3
            if "last week" in query.lower():
                week_start = shift_day(query_time[:10], -7)
                if week_start <= block.key <= query_time[:10]:
                    score += 0.2
            if score > bonus:
                hits.append(
                    {
                        "layer": "temporal_tree",
                        "source": block.block_id,
                        "score": round(score, 3),
                        "content": block.content,
                        "event_time": block.key,
                        "trace": {"event_time": block.key, "source_refs": block.source_refs},
                    }
                )
        return hits

    def _search_graph(self, query: str, bonus: float) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for edge in self.edges:
            content = f"{edge.source_id} --{edge.relation_type}--> {edge.target_id} | {edge.evidence}"
            score = token_overlap(query, content) + bonus
            if score <= bonus:
                continue
            layer = "graph"
            if edge.relation_type == "shows":
                layer = "image_evidence"
            elif edge.relation_type == "happened_at":
                layer = "fact"
            hits.append(
                {
                    "layer": layer,
                    "source": edge.edge_id,
                    "score": round(score, 3),
                    "content": content,
                    "event_time": edge.target_id if edge.relation_type == "happened_at" else "",
                    "trace": {
                        "path_edge_ids": [edge.edge_id],
                        "event_time": edge.target_id if edge.relation_type == "happened_at" else "",
                    },
                }
            )
        return hits

    def _coverage(self, required_layers: list[str], hits: list[dict[str, Any]]) -> dict[str, Any]:
        present = {str(hit.get("layer", "")).strip() for hit in hits if str(hit.get("layer", "")).strip()}
        matched: list[str] = []
        for item in required_layers:
            if item == "event_time":
                if any(str(hit.get("trace", {}).get("event_time", "")).strip() for hit in hits):
                    matched.append(item)
            elif item == "path_grounding":
                if any(hit.get("trace", {}).get("path_edge_ids") for hit in hits):
                    matched.append(item)
            elif item in present:
                matched.append(item)
            elif item == "fact" and ("fact" in present or "graph" in present):
                matched.append(item)
        missing = [item for item in required_layers if item not in matched]
        return {
            "required": required_layers,
            "present_layers": sorted(present),
            "matched": matched,
            "missing": missing,
            "coverage_ratio": round(len(matched) / max(len(required_layers), 1), 3),
            "contract_ok": not missing,
        }


def build_demo() -> tuple[GenericDualBackboneNano, list[dict[str, str]]]:
    mem = GenericDualBackboneNano()
    mem.append(
        role="user",
        content="Maya signed the Riverside lease on 2026-03-03, but I only mentioned it after returning from travel.",
        write_time="2026-03-10T09:00:00Z",
    )
    mem.append(
        role="user",
        content="Nora helped Maya prepare the visa checklist on 2026-04-02.",
        write_time="2026-04-03T09:00:00Z",
    )
    mem.append(
        role="user",
        content="Maya presented the keynote deck yesterday.",
        write_time="2026-05-10T08:00:00Z",
    )
    mem.append(
        role="user",
        content="Maya had the investor board review last week.",
        write_time="2026-05-18T09:00:00Z",
    )
    mem.append(
        role="user",
        content="",
        write_time="2026-03-10T09:05:00Z",
        modality="image",
        caption="Lease document screenshot",
        ocr="Riverside Lease Agreement Rua Augusta 14 Lisbon",
    )
    mem.build()
    queries = [
        {
            "query": "When did Maya sign the lease?",
            "query_time": "2026-03-10T10:00:00Z",
            "why": "temporal question should prefer tree first and preserve event_time",
        },
        {
            "query": "Who helped Maya with the visa checklist?",
            "query_time": "2026-04-04T10:00:00Z",
            "why": "relational question should require graph-style support and path grounding",
        },
        {
            "query": "What happened yesterday?",
            "query_time": "2026-05-10T21:00:00Z",
            "why": "relative-time question should resolve against query anchor, not write time",
        },
        {
            "query": "What address was shown in the screenshot?",
            "query_time": "2026-03-11T08:00:00Z",
            "why": "visual question should require image_evidence, not only plain OCR text overlap",
        },
    ]
    return mem, queries


def render_html(payload: dict[str, Any]) -> str:
    rows = []
    for q in payload["queries"]:
        coverage = q["result"]["coverage"]
        rows.append(
            f"""
            <tr>
              <td>{esc(q['query'])}</td>
              <td><code>{esc(q['result']['plan']['family'])}</code></td>
              <td><code>{esc(q['result']['plan']['primary_reader'])}</code></td>
              <td><code>{esc(q['result']['readers_used'])}</code></td>
              <td><code>{esc(q['result']['second_pass_readers'])}</code></td>
              <td>{esc(coverage['coverage_ratio'])}</td>
              <td>{esc(coverage['contract_ok'])}</td>
            </tr>
            """
        )

    details = []
    for q in payload["queries"]:
        hit_lines = []
        for hit in q["result"]["hits"]:
            hit_lines.append(
                f"<li><code>{esc(hit['layer'])}</code> · score={esc(hit['score'])} · {esc(hit['content'])}</li>"
            )
        details.append(
            f"""
            <section class="panel">
              <h2>{esc(q['query'])}</h2>
              <p class="muted">{esc(q['why'])}</p>
              <p><b>plan</b>: <code>{esc(q['result']['plan'])}</code></p>
              <p><b>coverage</b>: <code>{esc(q['result']['coverage'])}</code></p>
              <ul>{''.join(hit_lines)}</ul>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Generic Dual-Backbone Explained</title>
  <style>
    :root {{
      --bg:#f5f7fb; --panel:#fff; --line:#dbe3ee; --text:#172233; --muted:#627487;
      --blue:#245cff; --blue-soft:#eef4ff; --green:#10895f; --green-soft:#edf9f3;
      --amber:#a86800; --amber-soft:#fff7ea; --shadow:0 12px 30px rgba(18, 32, 51, 0.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .page {{ max-width:1160px; margin:0 auto; padding:28px 20px 56px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; box-shadow:var(--shadow); }}
    .hero {{ padding:28px; margin-bottom:16px; background:linear-gradient(135deg,#fff 0%,#eef4ff 100%); }}
    .panel {{ padding:18px; margin-bottom:16px; box-shadow:none; }}
    h1,h2 {{ margin:0 0 10px; line-height:1.28; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:20px; padding-bottom:8px; border-bottom:1px solid var(--line); }}
    p {{ margin:8px 0; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:6px 0; }}
    .muted {{ color:var(--muted); }}
    .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }}
    .chip {{ display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px; font-weight:700; border:1px solid #cfdcff; background:var(--blue-soft); color:var(--blue); }}
    .callout {{ border-left:4px solid var(--blue); background:#f4f8ff; padding:12px 14px; border-radius:6px; margin-top:12px; }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:14px; }}
    th,td {{ border:1px solid var(--line); padding:10px; vertical-align:top; text-align:left; }}
    th {{ background:#f4f7fd; }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; background:#f3f6fb; border:1px solid #e0e7f1; border-radius:4px; padding:1px 5px; font-size:12px; word-break:break-all; }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Generic Dual-Backbone Explained</h1>
      <p class="muted">
        这是一个真正尽量“去 benchmark 化”的教学 nano。它不靠数据集关键词表，而是只保留六个最核心机制：
        append-only stream、atom formation、three-clock time、temporal tree、relation/image graph、typed evidence contract。
      </p>
      <div class="chips">
        <span class="chip">generic nano</span>
        <span class="chip">three-clock</span>
        <span class="chip">dual backbone</span>
        <span class="chip">readiness</span>
        <span class="chip">typed contract</span>
      </div>
      <div class="callout">
        这个文件最适合回答一个问题：<b>为什么 EchoMemory 不应该被理解成“大向量库 + top-k”，而应该被理解成一个 stream-to-structure memory system。</b>
      </div>
    </section>

    <section class="panel">
      <h2>1. 这个 nano 和主仓的映射</h2>
      <table>
        <thead><tr><th>nano 概念</th><th>主仓对应</th><th>作用</th></tr></thead>
        <tbody>
          <tr><td><code>append()</code></td><td><code>index_engine/session_service.py</code></td><td>append-only 写入与 readiness 失效</td></tr>
          <tr><td><code>_extract_atoms()</code></td><td><code>workers/atom_first_pipeline.py</code></td><td>从原始 turn 提炼长期事实</td></tr>
          <tr><td><code>_build_temporal_blocks()</code></td><td><code>workers/organized_projector/projector.py</code></td><td>把事实排进时间结构</td></tr>
          <tr><td><code>_build_graph()</code></td><td><code>index_engine/graph/sync.py</code></td><td>把 relation / visual evidence 接成结构图</td></tr>
          <tr><td><code>plan()</code></td><td><code>index_engine/planner/query_planner.py</code></td><td>按 query family 选择主 backbone</td></tr>
          <tr><td><code>_coverage()</code> + second pass</td><td><code>policy/evidence_contract.py</code> + <code>policy/self_check.py</code></td><td>不是只看相似度，而是看证据族是否齐全</td></tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>2. Demo 结果总览</h2>
      <table>
        <thead><tr><th>Query</th><th>Family</th><th>Primary</th><th>Readers Used</th><th>Second Pass</th><th>Coverage</th><th>Contract OK</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>

    {''.join(details)}
  </div>
</body>
</html>
"""


def main() -> None:
    mem, queries = build_demo()
    payload = {
        "readiness": asdict(mem.readiness),
        "observations": [asdict(obs) for obs in mem.observations],
        "atoms": [asdict(atom) for atom in mem.atoms],
        "temporal_blocks": [asdict(block) for block in mem.blocks],
        "graph_edges": [asdict(edge) for edge in mem.edges],
        "queries": [],
    }
    for item in queries:
        payload["queries"].append(
            {
                "query": item["query"],
                "query_time": item["query_time"],
                "why": item["why"],
                "result": mem.search(item["query"], item["query_time"]),
            }
        )
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
