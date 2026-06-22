#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_tg_output.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_tg_report.html")


@dataclass
class Observation:
    obs_id: str
    role: str
    text: str
    mention_time: str
    event_time: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    obj: str
    statement: str
    mention_time: str
    event_time: str


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    event_time: str = ""


@dataclass
class Edge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class Plan:
    intent: str
    query_family: str
    graph_first: bool
    preferred_node_types: list[str]
    retrieval_steps: list[str]
    answer_rule: str


@dataclass
class Hit:
    item_id: str
    item_type: str
    score: float
    content: str
    provenance: str


class MemoryBuilder:
    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.blocks: dict[str, str] = {}

    def append(self, text: str, mention_time: str) -> None:
        event_time = self._infer_event_time(text, mention_time)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role="user",
                text=text.strip(),
                mention_time=mention_time,
                event_time=event_time,
            )
        )

    def build(self) -> None:
        self._extract_atoms()
        self._build_nodes()
        self._build_blocks()

    def _extract_atoms(self) -> None:
        self.atoms = []
        patterns = [
            (r"([A-Z][a-z]+)\s+lost his job on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "lost_job", "job", "{1}"),
            (r"([A-Z][a-z]+)\s+married\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "{0}", "married", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+plans to\s+(.+)", "plan", "{0}", "plans", "{1}", ""),
            (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "visited", "{1}", "{2}"),
        ]
        for obs in self.observations:
            matched = False
            for pat, atom_type, subj_t, pred_t, obj_t, evt_t in patterns:
                m = re.search(pat, obs.text, re.I)
                if not m:
                    continue
                groups = m.groups()
                event_time = evt_t.format(*groups).strip() if evt_t else obs.event_time
                self.atoms.append(
                    Atom(
                        atom_id=f"atom-{len(self.atoms):03d}",
                        atom_type=atom_type,
                        subject=subj_t.format(*groups).strip(),
                        predicate=pred_t.format(*groups).strip(),
                        obj=obj_t.format(*groups).strip(),
                        statement=obs.text,
                        mention_time=obs.mention_time,
                        event_time=event_time or obs.event_time,
                    )
                )
                matched = True
                break
            if not matched:
                self.atoms.append(
                    Atom(
                        atom_id=f"atom-{len(self.atoms):03d}",
                        atom_type="fact",
                        subject="unknown",
                        predicate="mentions",
                        obj=obs.text[:40],
                        statement=obs.text,
                        mention_time=obs.mention_time,
                        event_time=obs.event_time,
                    )
                )

    def _build_nodes(self) -> None:
        self.nodes = []
        self.edges = []
        seen_entities: set[str] = set()
        event_order: list[tuple[str, str]] = []

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            self.nodes.append(Node(fact_id, "fact", atom.statement, atom.event_time))
            event_id = f"event:{atom.atom_id}"
            if atom.atom_type in {"event", "relation", "plan"}:
                self.nodes.append(Node(event_id, "event", f"{atom.subject} {atom.predicate} {atom.obj}", atom.event_time))
                self.edges.append(Edge(f"{event_id}:evidence_of:{fact_id}", event_id, fact_id, "evidence_of"))
                event_order.append((event_id, atom.event_time))
            for ent in [atom.subject, atom.obj]:
                if not self._looks_like_entity(ent):
                    continue
                ent_id = f"entity:{ent}"
                if ent_id not in seen_entities:
                    seen_entities.add(ent_id)
                    self.nodes.append(Node(ent_id, "entity", f"name={ent}"))
                self.edges.append(Edge(f"{ent_id}:has_fact:{atom.atom_id}", ent_id, fact_id, "has_fact"))
                if atom.atom_type in {"event", "relation", "plan"}:
                    self.edges.append(Edge(f"{event_id}:involves:{ent}", event_id, ent_id, "involves"))

        event_order = [item for item in event_order if item[1]]
        event_order.sort(key=lambda item: item[1])
        for left, right in zip(event_order, event_order[1:]):
            self.edges.append(Edge(f"{left[0]}:temporal_next:{right[0]}", left[0], right[0], "temporal_next"))

    def _build_blocks(self) -> None:
        timelines = []
        plans = []
        for atom in self.atoms:
            if atom.atom_type == "plan":
                plans.append(f"- {atom.subject} plans to {atom.obj}")
            else:
                timelines.append(f"- {atom.event_time or atom.mention_time}: {atom.statement}")
        self.blocks = {
            "timeline": "\n".join(timelines),
            "plans": "\n".join(plans),
        }

    @staticmethod
    def _infer_event_time(text: str, fallback: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        return m.group(1) if m else fallback[:10]

    @staticmethod
    def _looks_like_entity(value: str) -> bool:
        raw = str(value or "").strip()
        if not raw or raw == "unknown":
            return False
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return False
        return bool(re.match(r"[A-Z][a-z]+$", raw))


class Planner:
    def plan(self, query: str) -> Plan:
        q = query.lower()
        if re.search(r"\bwhen\b|什么时候|哪天|日期|时间|多久|前后|之后|之前", q, re.I):
            if re.search(r"\bwho\b|\bwhich\b|谁|哪个|哪些", q, re.I):
                return Plan(
                    intent="temporal_relational",
                    query_family="temporal+relation",
                    graph_first=True,
                    preferred_node_types=["event", "entity", "fact"],
                    retrieval_steps=["seed_event_nodes", "expand_relation_edges", "attach_fact_support"],
                    answer_rule="Prefer event answer, enrich with relation participants.",
                )
            return Plan(
                intent="temporal",
                query_family="temporal",
                graph_first=True,
                preferred_node_types=["event", "fact"],
                retrieval_steps=["seed_event_nodes", "attach_fact_support"],
                answer_rule="Use event-time first, fact text second.",
            )
        if re.search(r"\bwho\b|\bwhich\b|\bboth\b|谁|哪个|哪些|关系|共同", q, re.I):
            return Plan(
                intent="relational",
                query_family="relation",
                graph_first=True,
                preferred_node_types=["entity", "event", "fact"],
                retrieval_steps=["seed_entities", "expand_involves_edges", "attach_fact_support"],
                answer_rule="Use relation path rather than flat lexical evidence.",
            )
        if re.search(r"计划|plan|打算|未来", q, re.I):
            return Plan(
                intent="plan",
                query_family="plan",
                graph_first=False,
                preferred_node_types=["block", "event", "fact"],
                retrieval_steps=["read_plan_block", "fallback_event"],
                answer_rule="Blocks are acceptable primary evidence for plans.",
            )
        return Plan(
            intent="general",
            query_family="general",
            graph_first=False,
            preferred_node_types=["fact", "block"],
            retrieval_steps=["flat_retrieval"],
            answer_rule="Use default mixed retrieval.",
        )


class Retriever:
    def __init__(self, mem: MemoryBuilder) -> None:
        self.mem = mem

    def retrieve(self, query: str, plan: Plan) -> list[Hit]:
        if plan.graph_first:
            return self._graph_first(query, plan)
        return self._hybrid(query, plan)

    def _graph_first(self, query: str, plan: Plan) -> list[Hit]:
        terms = self._terms(query)
        hits: list[Hit] = []
        for node in self.mem.nodes:
            if node.node_type not in set(plan.preferred_node_types):
                continue
            score = self._score(node.content, terms)
            if score <= 0:
                continue
            if plan.intent.startswith("temporal") and node.node_type == "event":
                score += 1.0
            if "relation" in plan.intent and node.node_type == "entity":
                score += 0.8
            hits.append(Hit(node.node_id, node.node_type, score, node.content, "graph"))

        if plan.intent == "relational":
            fact_like = [hit for hit in hits if hit.item_type in {"fact", "event"}]
            entity_mentions: dict[str, float] = {}
            for hit in fact_like[:4]:
                text = hit.content
                for ent in re.findall(r"\b[A-Z][a-z]+\b", text):
                    entity_mentions[ent] = max(entity_mentions.get(ent, 0.0), hit.score + 0.9)
            for ent, score in entity_mentions.items():
                hits.append(Hit(f"entity:{ent}", "entity", score, f"name={ent}", "graph_relation_expansion"))

        hits.sort(key=lambda item: (-item.score, item.item_id))
        dedup: dict[str, Hit] = {}
        for hit in hits:
            prev = dedup.get(hit.item_id)
            if prev is None or hit.score > prev.score:
                dedup[hit.item_id] = hit
        final_hits = sorted(dedup.values(), key=lambda item: (-item.score, item.item_id))
        return final_hits[:6]

    def _hybrid(self, query: str, plan: Plan) -> list[Hit]:
        terms = self._terms(query)
        hits: list[Hit] = []
        for block_name, content in self.mem.blocks.items():
            score = self._score(content, terms)
            if score > 0:
                hits.append(Hit(f"block:{block_name}", "block", score + 0.3, content, "block"))
        for node in self.mem.nodes:
            if node.node_type not in {"fact", "event"}:
                continue
            score = self._score(node.content, terms)
            if score > 0:
                hits.append(Hit(node.node_id, node.node_type, score, node.content, "flat"))
        hits.sort(key=lambda item: (-item.score, item.item_id))
        return hits[:6]

    @staticmethod
    def _terms(query: str) -> list[str]:
        q = query.lower()
        return [t for t in re.findall(r"[a-z]{2,}|[\u4e00-\u9fa5]{1,}", q) if t not in {"what", "does", "did", "the"}]

    @staticmethod
    def _score(content: str, terms: list[str]) -> float:
        hay = content.lower()
        return float(sum(1 for term in terms if term in hay))


class EvidenceComposer:
    def compose(self, plan: Plan, hits: list[Hit]) -> str:
        if not hits:
            return "unknown"
        top = hits[0]
        if plan.intent == "temporal":
            return f"Top event evidence: {top.content}"
        if plan.intent == "temporal_relational":
            return f"Event+relation chain: {top.content}"
        if plan.intent == "relational":
            return f"Relation evidence path starts from: {top.content}"
        if plan.intent == "plan":
            return f"Plan block says: {top.content.splitlines()[0] if top.content else ''}"
        return f"Top evidence: {top.content}"


def build_demo() -> dict[str, Any]:
    mem = MemoryBuilder()
    mem.append("Gina visited Rome on 2023-01-30 after leaving Milan.", "2023-02-01T09:00:00Z")
    mem.append("Gina lost his job on 2023-02-02 and started searching for design roles.", "2023-02-03T10:00:00Z")
    mem.append("Jon married Alice on 2023-03-12 in Seattle.", "2023-03-13T11:00:00Z")
    mem.append("Gina plans to move to Lisbon after the spring hiring season.", "2023-02-05T08:00:00Z")
    mem.build()

    planner = Planner()
    retriever = Retriever(mem)
    composer = EvidenceComposer()

    queries = [
        "When did Gina lose her job?",
        "Who married Alice and when?",
        "Which two people were involved in the Seattle wedding?",
        "What does Gina plan to do after spring hiring season?",
    ]

    runs = []
    for query in queries:
        plan = planner.plan(query)
        hits = retriever.retrieve(query, plan)
        runs.append(
            {
                "query": query,
                "plan": asdict(plan),
                "hits": [asdict(hit) for hit in hits],
                "answer_sketch": composer.compose(plan, hits),
            }
        )

    return {
        "observations": [asdict(obs) for obs in mem.observations],
        "atoms": [asdict(atom) for atom in mem.atoms],
        "nodes": [asdict(node) for node in mem.nodes],
        "edges": [asdict(edge) for edge in mem.edges],
        "blocks": mem.blocks,
        "runs": runs,
    }


def render_html(data: dict[str, Any]) -> str:
    runs_html = "".join(
        "<div class='case'>"
        f"<h3>{html.escape(run['query'])}</h3>"
        f"<p><b>Intent:</b> {html.escape(run['plan']['intent'])} · <b>family:</b> {html.escape(run['plan']['query_family'])} · <b>graph_first:</b> {html.escape(str(run['plan']['graph_first']))}</p>"
        f"<p><b>Steps:</b> {html.escape(' -> '.join(run['plan']['retrieval_steps']))}</p>"
        f"<p><b>Answer rule:</b> {html.escape(run['plan']['answer_rule'])}</p>"
        "<ul>" + "".join(
            f"<li><code>{html.escape(hit['item_id'])}</code> · {html.escape(hit['item_type'])} · score={html.escape(str(hit['score']))}<br>{html.escape(hit['content'][:200])}</li>"
            for hit in run["hits"][:4]
        ) + "</ul>"
        f"<p class='note'>{html.escape(run['answer_sketch'])}</p>"
        "</div>"
        for run in data["runs"]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Explicit Planner</title>
  <style>
    :root {{
      --bg:#f6f7fb;--panel:#fff;--text:#172033;--muted:#667085;--line:#dde4ee;--blue:#175cd3;--blue-soft:#eff4ff;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 60px}}
    .hero,.section,.case{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero{{padding:28px 30px 22px}}
    .section{{padding:20px 22px;margin-top:18px}}
    .case{{padding:18px 20px;margin-top:16px}}
    h1,h2,h3{{margin:0 0 10px}}
    p{{margin:0 0 10px}}
    ul{{margin:8px 0 0;padding-left:18px}}
    code{{background:#f3f6fb;border-radius:6px;padding:2px 6px;font-size:12px}}
    .note{{margin-top:12px;padding:10px 12px;border-left:3px solid var(--blue);background:var(--blue-soft)}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Nano: Explicit Planner / Retriever / Composer</h1>
      <p>
        这个 nano 的目的不是提高分数，而是把主仓里现在混在 <code>SearchService</code> 里的几件事拆开：
        <b>Planner</b> 决定 query family，<b>Retriever</b> 决定 graph-first 还是 hybrid，<b>EvidenceComposer</b> 决定怎样把命中的证据组合成回答。
      </p>
    </div>
    <div class="section">
      <h2>为什么要拆</h2>
      <ul>
        <li>便于做 clean ablation：到底是 planner 错，还是 retrieval 错，还是 answer composition 错。</li>
        <li>便于把 temporal / relational / visual query 分别设计成不同的证据路径。</li>
        <li>便于把它画成论文方法图，而不是一大团 SearchService 逻辑。</li>
      </ul>
    </div>
    {runs_html}
  </div>
</body>
</html>
"""


def main() -> None:
    data = build_demo()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML), "runs": len(data["runs"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
