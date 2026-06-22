#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_teaching_output.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_teaching_20260613.html")


@dataclass
class Message:
    message_id: str
    role: str
    content: str
    created_at: str
    story_time: str = ""
    role_id: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    statement: str
    subject: str
    predicate: str
    obj: str
    mention_time: str
    story_time: str


@dataclass
class TreeBlock:
    block_id: str
    level: str
    key: str
    lines: list[str]


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    story_time: str = ""


@dataclass
class Edge:
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class Readiness:
    messages_persisted: bool = False
    atoms_ready: bool = False
    tree_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False


@dataclass
class Plan:
    family: str
    primary_backbone: str
    supporting_backbones: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Hit:
    source: str
    layer: str
    score: float
    content: str


class TeachingDualBackboneMemory:
    """
    A deliberately tiny and readable nano implementation.

    It keeps only the pieces that matter for explaining the paper idea:
    1. append-only messages
    2. event/relation atoms with story time
    3. temporal tree for chronology
    4. relation graph for entity/event traversal
    5. planner-routed retrieval
    6. readiness gate
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.atoms: list[Atom] = []
        self.tree_blocks: list[TreeBlock] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.readiness = Readiness()

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def append_message(self, role: str, content: str, created_at: str, role_id: str = "") -> None:
        story_time = self._infer_story_time(content, created_at)
        self.messages.append(
            Message(
                message_id=f"msg-{len(self.messages):03d}",
                role=role,
                content=content.strip(),
                created_at=created_at,
                story_time=story_time,
                role_id=role_id,
            )
        )
        self.readiness.messages_persisted = True
        self.readiness.atoms_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def run_hot_path(self) -> None:
        self.atoms = []
        for msg in self.messages:
            if msg.role != "user":
                continue
            self.atoms.extend(self._extract_atoms(msg))
        self.readiness.atoms_ready = True
        self.readiness.qa_ready = False

    def run_cold_path(self) -> None:
        self.tree_blocks = self._build_temporal_tree()
        self.nodes, self.edges = self._build_relation_graph()
        self.readiness.tree_ready = True
        self.readiness.graph_ready = True
        self.readiness.qa_ready = (
            self.readiness.messages_persisted
            and self.readiness.atoms_ready
            and self.readiness.tree_ready
            and self.readiness.graph_ready
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def plan(self, query: str) -> Plan:
        q = query.lower()
        if re.search(r"\bafter\b|\bbefore\b|\bplan\b|之后|之前|计划|打算", q, re.I):
            return Plan(
                family="temporal_relational",
                primary_backbone="graph",
                supporting_backbones=["tree"],
                reason="Order-sensitive relation questions need event graph plus chronology support.",
            )
        if re.search(r"\bwhen\b|\bdate\b|什么时候|哪天|日期|时间|上个月|之前|之后", q, re.I):
            return Plan(
                family="temporal",
                primary_backbone="tree",
                supporting_backbones=["graph"],
                reason="Chronology questions should first navigate time structure.",
            )
        if re.search(r"\bwho\b|\bwhich company\b|\brelationship\b|谁|关系|哪个公司|谁帮|谁介绍", q, re.I):
            return Plan(
                family="relational",
                primary_backbone="graph",
                supporting_backbones=["tree"],
                reason="Relation questions should first traverse entity/event links.",
            )
        return Plan(
            family="general",
            primary_backbone="tree",
            supporting_backbones=["graph"],
            reason="Default to concise chronology-first retrieval.",
        )

    def search(self, query: str) -> dict[str, Any]:
        plan = self.plan(query)
        if plan.primary_backbone == "tree":
            hits = self._search_tree(query) + self._search_graph(query, offset=0.05)
        else:
            hits = self._search_graph(query) + self._search_tree(query, offset=0.05)
        merged = self._dedup_sort(hits)
        return {
            "query": query,
            "plan": asdict(plan),
            "readiness": asdict(self.readiness),
            "hits": [asdict(hit) for hit in merged[:6]],
        }

    # ------------------------------------------------------------------
    # Atoms
    # ------------------------------------------------------------------

    def _extract_atoms(self, msg: Message) -> list[Atom]:
        text = msg.content
        atoms: list[Atom] = []
        patterns = [
            (r"(.+?) 于 (\d{4}-\d{2}-\d{2}) 加入了 (.+)", "event", "joined"),
            (r"(.+?) 于 (\d{4}-\d{2}-\d{2}) 离开了 (.+)", "event", "left"),
            (r"(.+?) 于 (\d{4}-\d{2}-\d{2}) 结婚，对方是 (.+)", "relation", "married_to"),
            (r"(.+?) 于 (\d{4}-\d{2}-\d{2}) 帮助了 (.+?) 准备签证清单", "relation", "helped"),
            (r"(.+?) 在 (\d{4}-\d{2}-\d{2}) 签了 (.+)", "event", "signed"),
            (r"(.+?) 在 (\d{4}-\d{2}) 计划做 (.+)", "plan", "planned"),
        ]
        for pattern, atom_type, predicate in patterns:
            m = re.search(pattern, text)
            if not m:
                continue
            if predicate == "helped":
                subject, story_time, obj = m.group(1), m.group(2), m.group(3)
                obj = obj.strip("。.")
                statement = f"{subject} helped {obj} prepare a visa checklist."
            else:
                subject, story_time, obj = m.group(1), m.group(2), m.group(3)
                obj = obj.strip("。.")
                statement = text
            subject = subject.strip("。.")
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms):03d}",
                    atom_type=atom_type,
                    statement=statement,
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    mention_time=msg.created_at,
                    story_time=story_time,
                )
            )
        return atoms

    # ------------------------------------------------------------------
    # Temporal tree
    # ------------------------------------------------------------------

    def _build_temporal_tree(self) -> list[TreeBlock]:
        buckets: dict[tuple[str, str], list[str]] = {}
        for atom in self.atoms:
            day = atom.story_time[:10]
            month = day[:7]
            year = day[:4]
            line = f"- {day}: {atom.statement}"
            buckets.setdefault(("day", day), []).append(line)
            buckets.setdefault(("month", month), []).append(line)
            buckets.setdefault(("year", year), []).append(line)
        blocks = [
            TreeBlock(block_id=f"tree:{level}:{key}", level=level, key=key, lines=lines)
            for (level, key), lines in sorted(buckets.items())
        ]
        return blocks

    def _search_tree(self, query: str, offset: float = 0.0) -> list[Hit]:
        terms = self._terms(query)
        family = self.plan(query).family
        hits: list[Hit] = []
        for block in self.tree_blocks:
            content = f"{block.level.upper()} {block.key}\n" + "\n".join(block.lines)
            score = self._score(content, terms)
            if score <= 0:
                continue
            if family == "temporal" and block.level == "day":
                score += 1.0
            if family == "temporal" and block.level == "month":
                score += 0.3
            if family == "relational":
                score -= 0.8
            hits.append(Hit(source=block.block_id, layer="tree", score=round(score + offset, 3), content=content))
        return hits

    # ------------------------------------------------------------------
    # Relation graph
    # ------------------------------------------------------------------

    def _build_relation_graph(self) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen_entities: set[str] = set()
        ordered_events: list[tuple[str, str]] = []

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(Node(node_id=fact_id, node_type="fact", content=atom.statement, story_time=atom.story_time))
            event_id = f"event:{atom.atom_id}"
            if atom.atom_type in {"event", "relation", "plan"}:
                nodes.append(
                    Node(
                        node_id=event_id,
                        node_type="event",
                        content=f"{atom.subject} {atom.predicate} {atom.obj}",
                        story_time=atom.story_time,
                    )
                )
                edges.append(Edge(source_id=event_id, target_id=fact_id, relation_type="evidence_of"))
                ordered_events.append((event_id, atom.story_time))

            for entity in [atom.subject, atom.obj]:
                if not self._looks_like_entity(entity):
                    continue
                entity_id = f"entity:{entity}"
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    nodes.append(Node(node_id=entity_id, node_type="entity", content=f"name={entity}"))
                edges.append(Edge(source_id=entity_id, target_id=fact_id, relation_type="has_fact"))
                if atom.atom_type in {"event", "relation", "plan"}:
                    edges.append(Edge(source_id=event_id, target_id=entity_id, relation_type="involves"))

        ordered_events.sort(key=lambda item: item[1])
        for left, right in zip(ordered_events, ordered_events[1:]):
            edges.append(Edge(source_id=left[0], target_id=right[0], relation_type="temporal_next"))
        return nodes, edges

    def _search_graph(self, query: str, offset: float = 0.0) -> list[Hit]:
        terms = self._terms(query)
        family = self.plan(query).family
        q = query.lower()
        hits: list[Hit] = []
        for node in self.nodes:
            score = self._score(node.content + "\n" + node.node_id, terms)
            if score <= 0:
                continue
            if family == "relational" and node.node_type == "entity":
                score += 1.5
            elif family == "relational" and node.node_type == "event":
                score += 0.8
            elif family == "temporal_relational" and node.node_type in {"event", "entity"}:
                score += 0.8
            elif family == "temporal" and node.node_type == "event":
                score += 0.5

            if ("配偶" in query or "married" in q or "spouse" in q) and "married_to" in node.content:
                score += 2.2
            if ("谁帮助" in query or "helped" in q or "签证清单" in query) and "helped" in node.content:
                score += 2.0
            if ("哪个公司" in query or "which company" in q) and ("figma" in node.content.lower() or "left" in node.content.lower()):
                score += 1.6
            if (("计划" in query or "plan" in q) and ("之后" in query or "after" in q)) and node.node_type == "event":
                score += 1.2

            hits.append(Hit(source=node.node_id, layer=node.node_type, score=round(score + offset, 3), content=node.content))
        return hits

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_sort(hits: list[Hit]) -> list[Hit]:
        seen: set[str] = set()
        out: list[Hit] = []
        for hit in sorted(hits, key=lambda item: (-item.score, item.source)):
            if hit.source in seen:
                continue
            seen.add(hit.source)
            out.append(hit)
        return out

    @staticmethod
    def _infer_story_time(content: str, created_at: str) -> str:
        if m := re.search(r"(20\d{2}-\d{2}-\d{2})", content):
            return m.group(1)
        if m := re.search(r"(20\d{2}-\d{2})", content):
            return m.group(1) + "-01"
        return created_at[:10]

    @staticmethod
    def _terms(query: str) -> list[str]:
        return [term for term in re.findall(r"[A-Za-z0-9_-]+|[\u4e00-\u9fff]{2,}", query.lower()) if len(term) >= 2]

    @staticmethod
    def _score(text: str, terms: list[str]) -> float:
        hay = text.lower()
        score = 0.0
        for term in terms:
            if term in hay:
                score += 1.0
        return score

    @staticmethod
    def _looks_like_entity(value: str) -> bool:
        value = value.strip()
        if not value:
            return False
        if re.match(r"^\d", value):
            return False
        if len(value) == 1:
            return False
        return True


def build_demo_memory() -> TeachingDualBackboneMemory:
    mem = TeachingDualBackboneMemory()
    mem.append_message("user", "Gina 于 2023-01-12 加入了 Figma。", "2023-01-13T09:00:00Z", "Gina")
    mem.append_message("user", "Gina 于 2023-02-18 结婚，对方是 Alex。", "2023-02-19T11:00:00Z", "Gina")
    mem.append_message("user", "Nora 于 2023-03-23 帮助了 Gina 准备签证清单。", "2023-03-24T09:30:00Z", "Nora")
    mem.append_message("user", "Gina 于 2023-03-04 离开了 Figma。", "2023-03-05T10:00:00Z", "Gina")
    mem.append_message("user", "Gina 在 2023-04-03 签了 Lisbon lease。", "2023-04-04T08:40:00Z", "Gina")
    mem.append_message("user", "Gina 在 2023-04 计划做 design studio。", "2023-04-07T18:00:00Z", "Gina")
    mem.run_hot_path()
    mem.run_cold_path()
    return mem


def build_demo_output() -> dict[str, Any]:
    mem = build_demo_memory()
    queries = [
        "Gina 是什么时候加入 Figma 的？",
        "Gina 的配偶是谁？",
        "谁帮助 Gina 准备签证清单？",
        "Gina 在签了 Lisbon lease 之后计划做什么？",
    ]
    return {
        "messages": [asdict(msg) for msg in mem.messages],
        "readiness": asdict(mem.readiness),
        "atoms": [asdict(atom) for atom in mem.atoms],
        "tree_blocks": [asdict(block) for block in mem.tree_blocks],
        "graph_nodes": [asdict(node) for node in mem.nodes],
        "graph_edges": [asdict(edge) for edge in mem.edges],
        "queries": [mem.search(query) for query in queries],
    }


def render_html(payload: dict[str, Any]) -> str:
    qcards = []
    for item in payload["queries"]:
        plan = item["plan"]
        hits = item["hits"]
        hit_rows = "".join(
            f"<tr><td>{html.escape(hit['layer'])}</td><td>{hit['score']}</td><td>{html.escape(hit['content'])}</td></tr>"
            for hit in hits[:4]
        )
        qcards.append(
            f"""
            <section class="card">
              <h3>{html.escape(item['query'])}</h3>
              <p><b>family</b>: {html.escape(plan['family'])} &nbsp; <b>primary</b>: {html.escape(plan['primary_backbone'])}</p>
              <p class="muted">{html.escape(plan['reason'])}</p>
              <table>
                <thead><tr><th>layer</th><th>score</th><th>content</th></tr></thead>
                <tbody>{hit_rows}</tbody>
              </table>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Dual-Backbone Teaching</title>
  <style>
    :root{{--bg:#f5f7fb;--panel:#fff;--line:#dbe3ef;--text:#162033;--muted:#5c6c80;--blue:#2563eb;--green:#0f8f63;--shadow:0 10px 30px rgba(15,23,42,.06)}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1160px;margin:0 auto;padding:24px 18px 64px}} .hero,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:22px 24px}} .card{{padding:16px 18px;margin-top:14px}} .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
    h1,h2,h3{{margin:0 0 10px}} p,li{{line-height:1.7}} ul{{margin:8px 0 0 18px;padding:0}} .muted{{color:var(--muted)}}
    .tag{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700;margin-right:6px;margin-bottom:6px;background:#eaf1ff;color:var(--blue)}}
    .ok{{background:#e9f8f1;color:var(--green)}} code,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}} code{{background:#f3f6fb;border-radius:4px;padding:1px 5px;font-size:12px}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:8px;border-top:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-size:12px;color:var(--muted)}} pre{{overflow:auto;background:#f7f9fc;border:1px solid var(--line);border-radius:8px;padding:12px}}
    @media (max-width:900px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="tag">teaching nano</div><div class="tag">dual-backbone</div><div class="tag">single file</div>
      <h1>EchoMemory 最小教学版：tree + graph + readiness</h1>
      <p class="muted">这个版本专门用来解释方法，不追求复杂度。它把 stream、atoms、temporal tree、relation graph、planner、readiness 放在一个文件里。</p>
    </section>

    <section class="card">
      <h2>它对应大系统里的什么</h2>
      <div class="grid">
        <div><b>messages</b><br />append-only session stream</div>
        <div><b>atoms</b><br />hot-path extracted event / relation / plan units</div>
        <div><b>tree + graph</b><br />cold-path dual backbones</div>
      </div>
      <ul>
        <li><b>RAPTOR / MemoRAG</b> 对应 tree-first chronology navigation。</li>
        <li><b>HippoRAG / GraphReader</b> 对应 graph-first relation traversal。</li>
        <li><b>Mem0 / MemOS / LightMem</b> 对应 <code>messages_persisted -&gt; atoms_ready -&gt; tree_ready/graph_ready -&gt; qa_ready</code>。</li>
      </ul>
    </section>

    <section class="card">
      <h2>当前 readiness</h2>
      <pre>{html.escape(json.dumps(payload["readiness"], ensure_ascii=False, indent=2))}</pre>
    </section>

    <section class="card">
      <h2>4 个示例查询</h2>
      {"".join(qcards)}
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    payload = build_demo_output()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
