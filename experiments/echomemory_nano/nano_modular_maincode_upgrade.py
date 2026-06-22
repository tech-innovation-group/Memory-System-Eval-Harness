#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_modular_maincode_upgrade_output.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_modular_maincode_upgrade_20260614.html")


@dataclass
class Message:
    message_id: str
    role: str
    content: str
    created_at: str
    story_time: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    obj: str
    statement: str
    mention_time: str
    story_time: str


@dataclass
class TreeBlock:
    block_id: str
    level: str
    key: str
    content: str
    derived_from: list[str] = field(default_factory=list)


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    story_time: str = ""


@dataclass
class Edge:
    edge_id: str
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
class QueryIntent:
    label: str
    rationale: str


@dataclass
class QueryPlan:
    family: str
    primary_reader: str
    supporting_readers: list[str]
    must_have_layers: list[str]
    answer_rule: str


@dataclass
class Hit:
    source: str
    layer: str
    score: float
    content: str
    story_time: str = ""


@dataclass
class AnswerDecision:
    answer: str
    confidence: float
    should_answer: bool
    note: str
    expanded_support: bool


class SessionStream:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.readiness = Readiness()

    def append(self, role: str, content: str, created_at: str) -> None:
        self.messages.append(
            Message(
                message_id=f"msg-{len(self.messages):03d}",
                role=role,
                content=content.strip(),
                created_at=created_at,
                story_time=infer_story_time(content, created_at),
            )
        )
        self.readiness.messages_persisted = True
        self.readiness.atoms_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False


class AtomExtractor:
    def extract(self, messages: list[Message]) -> list[Atom]:
        atoms: list[Atom] = []
        patterns = [
            (r"Gina joined Figma on (\d{4}-\d{2}-\d{2})", "event", "Gina", "joined", "Figma"),
            (r"Gina left Figma on (\d{4}-\d{2}-\d{2})", "event", "Gina", "left", "Figma"),
            (r"Gina married Alex on (\d{4}-\d{2}-\d{2})", "relation", "Gina", "married_to", "Alex"),
            (r"Nora helped Gina prepare a Lisbon visa checklist on (\d{4}-\d{2}-\d{2})", "relation", "Nora", "helped", "Gina"),
            (r"Gina signed a Lisbon lease on (\d{4}-\d{2}-\d{2})", "event", "Gina", "signed", "Lisbon lease"),
            (r"Gina plans to move to Lisbon after leaving Figma", "plan", "Gina", "plans_after", "leave Figma"),
            (r"Screenshot of lease contract showed Rua Augusta 14 Lisbon Lease Agreement", "visual_fact", "lease_screenshot", "shows", "Rua Augusta 14"),
            (r"Arrival photo showed Santa Apolonia Platform 4", "visual_fact", "arrival_photo", "shows", "Santa Apolonia Platform 4"),
        ]
        for msg in messages:
            if msg.role != "user":
                continue
            matched = False
            for pattern, atom_type, subject, predicate, obj in patterns:
                m = re.search(pattern, msg.content)
                if not m:
                    continue
                story_time = m.group(1) if m.groups() else msg.story_time
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=atom_type,
                        subject=subject,
                        predicate=predicate,
                        obj=obj,
                        statement=msg.content,
                        mention_time=msg.created_at,
                        story_time=story_time,
                    )
                )
                matched = True
                break
            if not matched:
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type="fact",
                        subject="unknown",
                        predicate="mentions",
                        obj=msg.content[:48],
                        statement=msg.content,
                        mention_time=msg.created_at,
                        story_time=msg.story_time,
                    )
                )
        return atoms


class TemporalTreeProjector:
    def project(self, atoms: list[Atom]) -> list[TreeBlock]:
        buckets: dict[tuple[str, str], list[Atom]] = {}
        for atom in atoms:
            if not atom.story_time:
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}$", atom.story_time):
                yyyy, mm, dd = atom.story_time.split("-")
            else:
                continue
            for level, key in [
                ("year", yyyy),
                ("month", f"{yyyy}-{mm}"),
                ("day", f"{yyyy}-{mm}-{dd}"),
            ]:
                buckets.setdefault((level, key), []).append(atom)
        blocks: list[TreeBlock] = []
        for (level, key), items in sorted(buckets.items()):
            ordered = sorted(items, key=lambda a: a.story_time)
            blocks.append(
                TreeBlock(
                    block_id=f"{level}:{key}",
                    level=level,
                    key=key,
                    content="\n".join(f"- {a.story_time}: {a.statement}" for a in ordered[:6]),
                    derived_from=[a.atom_id for a in ordered[:6]],
                )
            )
        return blocks


class GraphProjector:
    def project(self, atoms: list[Atom]) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        entity_seen: set[str] = set()
        timed_events: list[tuple[str, str]] = []
        for atom in atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(Node(fact_id, "fact", atom.statement, atom.story_time))
            if atom.atom_type in {"event", "relation", "plan", "visual_fact"}:
                node_type = "image_evidence" if atom.atom_type == "visual_fact" else "event"
                event_id = f"{node_type}:{atom.atom_id}"
                nodes.append(Node(event_id, node_type, f"{atom.subject} {atom.predicate} {atom.obj}", atom.story_time))
                edges.append(Edge(f"{event_id}:evidence_of:{fact_id}", event_id, fact_id, "evidence_of"))
                if atom.story_time:
                    timed_events.append((event_id, atom.story_time))
            else:
                event_id = ""
            for ent in [atom.subject, atom.obj]:
                if not looks_like_entity(ent):
                    continue
                ent_id = f"entity:{ent}"
                if ent_id not in entity_seen:
                    entity_seen.add(ent_id)
                    nodes.append(Node(ent_id, "entity", f"name={ent}"))
                edges.append(Edge(f"{ent_id}:has_fact:{atom.atom_id}", ent_id, fact_id, "has_fact"))
                if event_id:
                    relation = "visual_evidence_of" if event_id.startswith("image_evidence:") else "involves"
                    edges.append(Edge(f"{event_id}:{relation}:{ent}", event_id, ent_id, relation))
        timed_events.sort(key=lambda x: x[1])
        for left, right in zip(timed_events, timed_events[1:]):
            edges.append(Edge(f"{left[0]}:temporal_next:{right[0]}", left[0], right[0], "temporal_next"))
        return nodes, edges


class IntentClassifier:
    def classify(self, query: str) -> QueryIntent:
        q = query.lower()
        if re.search(r"photo|image|screenshot|ocr|图|图片|截图|照片", q):
            return QueryIntent("visual", "Visual terms detected.")
        if re.search(r"\bafter\b|\bbefore\b|之后|之前|后来|计划|打算", q):
            return QueryIntent("temporal_relational", "Ordering / plan words detected.")
        if re.search(r"\bwhen\b|\bdate\b|什么时候|哪天|时间|日期", q):
            return QueryIntent("temporal", "Temporal words detected.")
        if re.search(r"\bwho\b|\bwhich\b|\brelationship\b|谁|关系|谁帮|哪个公司", q):
            return QueryIntent("relational", "Relation / entity words detected.")
        return QueryIntent("general", "Fallback classification.")


class QueryPlanner:
    def plan(self, query: str, intent: QueryIntent) -> QueryPlan:
        if intent.label == "visual":
            return QueryPlan(
                family="visual",
                primary_reader="graph",
                supporting_readers=["tree", "atom"],
                must_have_layers=["image_evidence", "fact"],
                answer_rule="Prefer image evidence anchored by fact support.",
            )
        if intent.label == "temporal_relational":
            return QueryPlan(
                family="temporal_relational",
                primary_reader="graph",
                supporting_readers=["tree", "atom"],
                must_have_layers=["event", "fact"],
                answer_rule="Need ordering or plan relation, then add chronology support.",
            )
        if intent.label == "temporal":
            return QueryPlan(
                family="temporal",
                primary_reader="tree",
                supporting_readers=["graph", "atom"],
                must_have_layers=["temporal_tree", "event"],
                answer_rule="Prefer chronology blocks; use event/fact as support.",
            )
        if intent.label == "relational":
            return QueryPlan(
                family="relational",
                primary_reader="graph",
                supporting_readers=["atom", "tree"],
                must_have_layers=["entity", "fact"],
                answer_rule="Prefer entity/event traversal with fact grounding.",
            )
        return QueryPlan(
            family="general",
            primary_reader="atom",
            supporting_readers=["tree", "graph"],
            must_have_layers=["fact"],
            answer_rule="Use atom/fact evidence first.",
        )


class TemporalTreeReader:
    def __init__(self, blocks: list[TreeBlock]) -> None:
        self.blocks = blocks

    def read(self, query: str) -> list[Hit]:
        preferred = extract_temporal_keys(query)
        hits: list[Hit] = []
        for block in self.blocks:
            score = overlap_score(query, block.content)
            if block.block_id in preferred:
                score += 0.85 if block.level == "day" else 0.65
            if score > 0:
                hits.append(Hit(block.block_id, "temporal_tree", score, block.content, block.key))
        return hits


class GraphReader:
    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self.nodes = nodes
        self.edges = edges

    def read(self, query: str) -> list[Hit]:
        hits: list[Hit] = []
        for node in self.nodes:
            score = overlap_score(query, node.content)
            if has_action_overlap(query, node.content):
                score += 0.18
            if score > 0:
                if node.node_type == "image_evidence":
                    score += 0.2
                elif node.node_type == "event":
                    score += 0.1
                hits.append(Hit(node.node_id, node.node_type, score, node.content, node.story_time))
        return hits


class AtomReader:
    def __init__(self, atoms: list[Atom]) -> None:
        self.atoms = atoms

    def read(self, query: str) -> list[Hit]:
        hits: list[Hit] = []
        for atom in self.atoms:
            score = overlap_score(query, atom.statement)
            if has_action_overlap(query, atom.statement):
                score += 0.18
            if score > 0:
                hits.append(Hit(f"atom:{atom.atom_id}", "fact", score, atom.statement, atom.story_time))
        return hits


class EvidenceFusion:
    def merge(self, groups: list[list[Hit]]) -> list[Hit]:
        all_hits: list[Hit] = []
        seen: set[tuple[str, str]] = set()
        for group in groups:
            for hit in group:
                key = (hit.source, hit.layer)
                if key in seen:
                    continue
                seen.add(key)
                all_hits.append(hit)
        all_hits.sort(key=lambda h: h.score, reverse=True)
        return all_hits


class SelfCheckPolicy:
    def assess(self, plan: QueryPlan, hits: list[Hit]) -> tuple[bool, float, str]:
        layers = {h.layer for h in hits[:6]}
        matched = len([layer for layer in plan.must_have_layers if layer in layers])
        confidence = matched / max(len(plan.must_have_layers), 1)
        if confidence >= 1.0:
            return False, confidence, "Primary evidence shape already sufficient."
        return True, confidence, "Primary evidence shape is incomplete; expand supporting readers."


class SearchOrchestrator:
    def __init__(
        self,
        stream: SessionStream,
        atoms: list[Atom],
        tree_blocks: list[TreeBlock],
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        self.stream = stream
        self.intent_classifier = IntentClassifier()
        self.planner = QueryPlanner()
        self.tree_reader = TemporalTreeReader(tree_blocks)
        self.graph_reader = GraphReader(nodes, edges)
        self.atom_reader = AtomReader(atoms)
        self.fusion = EvidenceFusion()
        self.self_check = SelfCheckPolicy()

    def search(self, query: str) -> dict[str, Any]:
        intent = self.intent_classifier.classify(query)
        plan = self.planner.plan(query, intent)
        readiness = asdict(self.stream.readiness)
        if not self.stream.readiness.qa_ready:
            decision = AnswerDecision("unknown", 0.0, False, "qa_ready=false", False)
            return {
                "query": query,
                "intent": asdict(intent),
                "plan": asdict(plan),
                "hits": [],
                "decision": asdict(decision),
                "readiness": readiness,
            }

        primary_hits = self._read(plan.primary_reader, query)
        merged = self.fusion.merge([primary_hits])
        need_expand, confidence, note = self.self_check.assess(plan, merged)
        expanded = False
        if need_expand:
            support_groups = [self._read(reader, query) for reader in plan.supporting_readers]
            merged = self.fusion.merge([primary_hits] + support_groups)
            expanded = True
            _, confidence, note = self.self_check.assess(plan, merged)
        answer = compose_answer(query, plan, merged, confidence)
        decision = AnswerDecision(answer, round(confidence, 3), answer != "unknown", note, expanded)
        return {
            "query": query,
            "intent": asdict(intent),
            "plan": asdict(plan),
            "hits": [asdict(h) for h in merged[:6]],
            "decision": asdict(decision),
            "readiness": readiness,
        }

    def _read(self, reader_name: str, query: str) -> list[Hit]:
        if reader_name == "tree":
            return self.tree_reader.read(query)
        if reader_name == "graph":
            return self.graph_reader.read(query)
        return self.atom_reader.read(query)


def infer_story_time(text: str, created_at: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    if "yesterday" in text.lower():
        return created_at[:10]
    return created_at[:10]


def looks_like_entity(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return False
    return bool(re.match(r"[A-Z][A-Za-z0-9_ ]+$", raw))


def overlap_score(query: str, content: str) -> float:
    q_terms = normalized_terms(query)
    c_terms = normalized_terms(content)
    if not q_terms or not c_terms:
        return 0.0
    return len(q_terms & c_terms) / max(len(q_terms), 1)


def normalized_terms(text: str) -> set[str]:
    raw = set(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", text.lower()))
    aliases = {
        "left": "leave",
        "leaving": "leave",
        "joined": "join",
        "married": "marry",
        "helped": "help",
        "signed": "sign",
        "showed": "show",
        "plans_after": "plan",
        "moving": "move",
    }
    return {aliases.get(token, token) for token in raw}


def has_action_overlap(query: str, content: str) -> bool:
    action_terms = {"leave", "join", "marry", "help", "sign", "show", "move", "plan"}
    q_actions = normalized_terms(query) & action_terms
    c_actions = normalized_terms(content) & action_terms
    return bool(q_actions & c_actions)


def extract_temporal_keys(query: str) -> set[str]:
    keys: set[str] = set()
    for y, m, d in re.findall(r"(20\d{2})-(\d{2})-(\d{2})", query):
        keys.update({f"year:{y}", f"month:{y}-{m}", f"day:{y}-{m}-{d}"})
    return keys


def compose_answer(query: str, plan: QueryPlan, hits: list[Hit], confidence: float) -> str:
    if confidence < 0.5 or not hits:
        return "unknown"
    top = select_best_hit(query, plan, hits)
    if plan.family == "temporal":
        return f"Likely time evidence: {top.story_time or top.content[:80]}"
    if plan.family == "relational":
        return f"Likely relation evidence: {top.content[:100]}"
    if plan.family == "visual":
        return f"Likely visual evidence: {top.content[:100]}"
    if plan.family == "temporal_relational":
        return f"Likely ordered relation evidence: {top.content[:100]}"
    return f"Likely fact evidence: {top.content[:100]}"


def select_best_hit(query: str, plan: QueryPlan, hits: list[Hit]) -> Hit:
    q_terms = normalized_terms(query)
    best = hits[0]
    best_score = -1.0
    for hit in hits:
        score = hit.score
        content_terms = normalized_terms(hit.content)
        if plan.family == "temporal":
            if hit.layer == "event":
                score += 0.22
            if hit.layer == "temporal_tree":
                score += 0.16
            if "plan" in content_terms and "plan" not in q_terms:
                score -= 0.42
            if has_action_overlap(query, hit.content):
                score += 0.12
        elif plan.family == "relational":
            if hit.layer in {"entity", "event", "fact"}:
                score += 0.08
        elif plan.family == "visual":
            if hit.layer == "image_evidence":
                score += 0.3
        if score > best_score:
            best = hit
            best_score = score
    return best


def build_demo() -> dict[str, Any]:
    stream = SessionStream()
    stream.append("user", "Gina joined Figma on 2024-01-05.", "2024-01-06T09:00:00+00:00")
    stream.append("user", "Gina married Alex on 2024-03-10.", "2024-03-11T09:00:00+00:00")
    stream.append("user", "Nora helped Gina prepare a Lisbon visa checklist on 2024-04-02.", "2024-04-03T09:00:00+00:00")
    stream.append("user", "Gina signed a Lisbon lease on 2024-05-08.", "2024-05-09T09:00:00+00:00")
    stream.append("user", "Gina left Figma on 2024-06-01.", "2024-06-02T09:00:00+00:00")
    stream.append("user", "Gina plans to move to Lisbon after leaving Figma.", "2024-06-03T09:00:00+00:00")
    stream.append("user", "Screenshot of lease contract showed Rua Augusta 14 Lisbon Lease Agreement.", "2024-05-09T11:00:00+00:00")
    stream.append("user", "Arrival photo showed Santa Apolonia Platform 4.", "2024-06-10T08:00:00+00:00")

    pre = SearchOrchestrator(stream, [], [], [], []).search("When did Gina leave Figma?")
    extractor = AtomExtractor()
    atoms = extractor.extract(stream.messages)
    stream.readiness.atoms_ready = True
    mid = SearchOrchestrator(stream, atoms, [], [], []).search("Who helped Gina with the Lisbon visa checklist?")
    tree_blocks = TemporalTreeProjector().project(atoms)
    nodes, edges = GraphProjector().project(atoms)
    stream.readiness.tree_ready = True
    stream.readiness.graph_ready = True
    stream.readiness.qa_ready = True
    search = SearchOrchestrator(stream, atoms, tree_blocks, nodes, edges)
    queries = [
        "When did Gina leave Figma?",
        "Who helped Gina with the Lisbon visa checklist?",
        "What did Gina plan to do after leaving Figma?",
        "What did the screenshot of the lease contract show?",
    ]
    results = [search.search(q) for q in queries]
    return {
        "system_name": "ModularMainCodeUpgradeNano",
        "why_this_exists": [
            "show how main code can be split into planner/readers/fusion/self-check",
            "make dual-backbone routing more concrete than a static diagram",
            "bridge current main code to future refactor without changing production code yet",
        ],
        "modules": [
            "SessionStream",
            "AtomExtractor",
            "TemporalTreeProjector",
            "GraphProjector",
            "IntentClassifier",
            "QueryPlanner",
            "TemporalTreeReader",
            "GraphReader",
            "AtomReader",
            "EvidenceFusion",
            "SelfCheckPolicy",
            "SearchOrchestrator",
        ],
        "pre_ready_result": pre,
        "after_hot_before_cold_result": mid,
        "after_cold_results": results,
        "atoms": [asdict(a) for a in atoms],
        "tree_blocks": [asdict(b) for b in tree_blocks],
        "nodes": [asdict(n) for n in nodes],
        "edges": [asdict(e) for e in edges],
    }


def render_html(data: dict[str, Any]) -> str:
    queries = data["after_cold_results"]
    cards = []
    for item in queries:
        hits = "".join(
            f"<li><b>{html.escape(h['layer'])}</b> · {html.escape(h['source'])} · score={h['score']:.2f}<br>{html.escape(h['content'][:120])}</li>"
            for h in item["hits"][:4]
        )
        cards.append(
            f"""
            <div class="card">
              <div class="pill">{html.escape(item['plan']['family'])}</div>
              <h3>{html.escape(item['query'])}</h3>
              <p><b>Primary:</b> {html.escape(item['plan']['primary_reader'])}</p>
              <p><b>Supporting:</b> {html.escape(', '.join(item['plan']['supporting_readers']))}</p>
              <p><b>Decision:</b> {html.escape(item['decision']['answer'])}</p>
              <p><b>Note:</b> {html.escape(item['decision']['note'])}</p>
              <ul>{hits}</ul>
            </div>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Modular Main-Code Upgrade</title>
  <style>
    :root{{--bg:#f4f7fb;--panel:#fff;--line:#d9e3ef;--text:#142033;--muted:#627387;--blue:#2563eb;--blue-soft:#eef4ff;--shadow:0 14px 34px rgba(15,23,42,.08);}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif;}}
    .wrap{{max-width:1240px;margin:0 auto;padding:28px 18px 72px}} .hero,.panel,.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}}
    .hero{{padding:30px 32px}} .panel{{padding:22px 24px;margin-top:16px}} .grid{{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}} .span-12{{grid-column:span 12}} .span-6{{grid-column:span 6}} .span-4{{grid-column:span 4}}
    h1,h2,h3{{margin:0;letter-spacing:0}} h1{{font-size:32px;line-height:1.16;margin-top:8px}} h2{{font-size:21px;margin-bottom:12px}} h3{{font-size:16px;margin-bottom:8px}} p{{margin:8px 0}} ul{{margin:8px 0 0 18px;padding:0}} li{{margin:5px 0}}
    .tag,.pill{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;margin-right:6px;margin-bottom:6px}} .tag,.pill{{background:var(--blue-soft);color:var(--blue)}} .muted{{color:var(--muted)}}
    .note{{margin-top:14px;padding:14px 16px;border-left:4px solid #b8ccff;background:#f8fbff;border-radius:10px}} .mono{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f7f9fc;border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}} th{{background:#f8fbff;color:#475569;font-size:12px;text-transform:uppercase}} tr:last-child td{{border-bottom:none}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}} .span-6,.span-4{{grid-column:span 12}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="tag">Nano</div><div class="tag">Modular</div><div class="tag">Main-code upgrade</div>
      <h1>EchoMemory Modular Main-Code Upgrade Nano</h1>
      <p class="muted">这份 nano 不再只是解释 tree 或 graph，而是直接模拟未来主代码拆层后的形态：<b>IntentClassifier / QueryPlanner / Readers / EvidenceFusion / SelfCheckPolicy / SearchOrchestrator</b>。</p>
      <div class="note"><b>它回答的问题：</b>如果以后真的要把 <code>SearchService</code> 拆开，最小但合理的结构会长什么样。</div>
    </section>
    <section class="panel">
      <h2>1. 模块分层</h2>
      <div class="mono">SessionStream
  -> AtomExtractor
  -> TemporalTreeProjector
  -> GraphProjector

Query
  -> IntentClassifier
  -> QueryPlanner
  -> Primary Reader
  -> Supporting Readers
  -> EvidenceFusion
  -> SelfCheckPolicy
  -> AnswerDecision</div>
    </section>
    <section class="panel">
      <h2>2. 为什么这比之前的 nano 更接近真实重构</h2>
      <table>
        <thead><tr><th>能力</th><th>之前的教学 nano</th><th>这个 modular nano</th></tr></thead>
        <tbody>
          <tr><td>query family 识别</td><td>通常内嵌在一个类里</td><td>单独拆成 <code>IntentClassifier</code></td></tr>
          <tr><td>routing</td><td>plan 和 search 常耦合</td><td>显式拆成 <code>QueryPlanner</code></td></tr>
          <tr><td>reader 角色</td><td>tree/graph 搜索常混在一起</td><td>拆成 <code>TemporalTreeReader / GraphReader / AtomReader</code></td></tr>
          <tr><td>fusion</td><td>多在 retrieval 内部做</td><td>单独变成 <code>EvidenceFusion</code></td></tr>
          <tr><td>answer-time policy</td><td>通常隐含</td><td>单独变成 <code>SelfCheckPolicy</code></td></tr>
        </tbody>
      </table>
    </section>
    <section class="panel">
      <h2>3. 查询示例</h2>
      <div class="grid">{''.join(cards)}</div>
    </section>
    <section class="panel">
      <h2>4. 它对应真实代码哪里</h2>
      <div class="mono">真实代码锚点：
- session_service.py
- atom_first_pipeline.py
- raw_atom_extractor.py
- graph/sync.py
- search_service.py

这个 nano 的意义不是替代它们，而是把未来更合理的边界画出来。</div>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    data = build_demo()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
