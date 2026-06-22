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
OUT_JSON = ROOT / "nano_generalizable_stream_graph_contract_results.json"
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_generalizable_stream_graph_contract_20260615.html")


def iso_date(text: str) -> str:
    return text[:10]


def shift_days(ymd: str, delta: int) -> str:
    dt = datetime.fromisoformat(ymd)
    return (dt + timedelta(days=delta)).strftime("%Y-%m-%d")


def esc(value: Any) -> str:
    return html.escape(str(value))


@dataclass
class Observation:
    obs_id: str
    modality: str
    content: str
    mention_time: str
    write_time: str
    event_time: str = ""
    caption: str = ""
    ocr: str = ""
    tags: list[str] = field(default_factory=list)


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
class TreeBlock:
    block_id: str
    level: str
    key: str
    content: str
    source_refs: list[str]


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    event_time: str = ""
    source_ref: str = ""


@dataclass
class Edge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class Hit:
    source: str
    layer: str
    score: float
    content: str
    event_time: str = ""
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryPlan:
    family: str
    primary_reader: str
    supporting_readers: list[str]
    must_have_layers: list[str]
    reason: str


@dataclass
class EvalCase:
    case_id: str
    query: str
    query_time: str
    expected_keywords: list[str]
    note: str


class NanoGeneralizableMemory:
    """
    A compact, generalized memory prototype for paper-method teaching.

    It intentionally combines four ideas that are currently split across the
    main EchoMemory stack and many local nano ablations:

    1. three-clock time: event_time / mention_time / write_time
    2. dual backbone: temporal tree + relation graph
    3. evidence contract: planned required layers
    4. contract-driven second pass: add the missing reader, not just "more text"
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.blocks: list[TreeBlock] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def append_text(self, text: str, mention_time: str) -> None:
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                modality="text",
                content=text.strip(),
                mention_time=mention_time,
                write_time=mention_time,
                event_time=self._infer_event_time(text, mention_time),
            )
        )

    def append_image(self, *, caption: str, ocr: str, mention_time: str, tags: list[str] | None = None) -> None:
        merged = "\n".join(x for x in [caption.strip(), ocr.strip()] if x)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                modality="image",
                content=merged,
                caption=caption.strip(),
                ocr=ocr.strip(),
                mention_time=mention_time,
                write_time=mention_time,
                event_time=self._infer_event_time(f"{caption} {ocr}", mention_time),
                tags=list(tags or []),
            )
        )

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> None:
        self.atoms = self._extract_atoms()
        self.blocks = self._build_temporal_tree()
        self.nodes, self.edges = self._build_graph()

    def _extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for obs in self.observations:
            if obs.modality == "image":
                image_subject = self._infer_image_subject(obs)
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type="image_evidence",
                        subject=image_subject,
                        predicate="shows",
                        obj=(obs.ocr or obs.caption or obs.content)[:80],
                        statement=obs.content,
                        event_time=obs.event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                    )
                )
                continue

            text = obs.content
            matched = False
            patterns = [
                (r"([A-Z][a-z]+)\s+signed\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "signed", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+joined\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "joined", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+left\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "left", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+helped\s+([A-Z][a-z]+)\s+prepare\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "{0}", "helped", "{1}", "{3}"),
                (r"([A-Z][a-z]+)\s+planned\s+to\s+(.+?)\s+after\s+leaving\s+(.+)", "plan", "{0}", "planned_after_leaving", "{1}", ""),
                (r"([A-Z][a-z]+)\s+presented\s+the\s+(.+?)\s+yesterday", "event", "{0}", "presented", "{1}", ""),
                (r"([A-Z][a-z]+)\s+had\s+the\s+(.+?)\s+last week", "event", "{0}", "had", "{1}", ""),
            ]
            for pattern, atom_type, subj_t, pred_t, obj_t, evt_t in patterns:
                m = re.search(pattern, text, re.I)
                if not m:
                    continue
                g = m.groups()
                event_time = evt_t.format(*g).strip() if evt_t else obs.event_time
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=atom_type,
                        subject=subj_t.format(*g).strip(),
                        predicate=pred_t.format(*g).strip(),
                        obj=obj_t.format(*g).strip(" ."),
                        statement=text,
                        event_time=event_time or obs.event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
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
                        obj=text[:64],
                        statement=text,
                        event_time=obs.event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                    )
                )
        return atoms

    def _build_temporal_tree(self) -> list[TreeBlock]:
        buckets: dict[tuple[str, str], list[Atom]] = {}
        for atom in self.atoms:
            if not atom.event_time or not re.match(r"^\d{4}-\d{2}-\d{2}$", atom.event_time):
                continue
            yyyy, mm, dd = atom.event_time.split("-")
            for level, key in [
                ("year", yyyy),
                ("month", f"{yyyy}-{mm}"),
                ("day", atom.event_time),
            ]:
                buckets.setdefault((level, key), []).append(atom)
        blocks: list[TreeBlock] = []
        for (level, key), items in sorted(buckets.items()):
            ordered = sorted(items, key=lambda x: x.event_time)
            blocks.append(
                TreeBlock(
                    block_id=f"{level}:{key}",
                    level=level,
                    key=key,
                    content="\n".join(f"- {item.event_time}: {item.statement}" for item in ordered),
                    source_refs=[item.atom_id for item in ordered],
                )
            )
        return blocks

    def _build_graph(self) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen_entities: set[str] = set()
        timed_event_nodes: list[tuple[str, str]] = []
        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(
                Node(
                    node_id=fact_id,
                    node_type="fact",
                    content=atom.statement,
                    event_time=atom.event_time,
                    source_ref=atom.source_obs_id,
                )
            )

            event_like = atom.atom_type in {"event", "relation", "plan", "image_evidence"}
            event_node_id = ""
            if event_like:
                node_type = "image_evidence" if atom.atom_type == "image_evidence" else "event"
                event_node_id = f"{node_type}:{atom.atom_id}"
                nodes.append(
                    Node(
                        node_id=event_node_id,
                        node_type=node_type,
                        content=f"{atom.subject} {atom.predicate} {atom.obj}".strip(),
                        event_time=atom.event_time,
                        source_ref=atom.source_obs_id,
                    )
                )
                edges.append(Edge(f"{event_node_id}:evidence_of:{fact_id}", event_node_id, fact_id, "evidence_of"))
                if atom.event_time:
                    timed_event_nodes.append((event_node_id, atom.event_time))

            for entity in [atom.subject, atom.obj]:
                if not self._looks_like_entity(entity):
                    continue
                ent_id = f"entity:{entity}"
                if ent_id not in seen_entities:
                    seen_entities.add(ent_id)
                    nodes.append(Node(ent_id, "entity", f"name={entity}", source_ref=atom.source_obs_id))
                edges.append(Edge(f"{ent_id}:has_fact:{atom.atom_id}", ent_id, fact_id, "has_fact"))
                if event_node_id:
                    rel = "visual_evidence_of" if event_node_id.startswith("image_evidence:") else "involves"
                    edges.append(Edge(f"{event_node_id}:{rel}:{entity}", event_node_id, ent_id, rel))

        timed_event_nodes.sort(key=lambda x: x[1])
        for left, right in zip(timed_event_nodes, timed_event_nodes[1:]):
            edges.append(Edge(f"{left[0]}:temporal_next:{right[0]}", left[0], right[0], "temporal_next"))
        return nodes, edges

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"photo|image|screenshot|ocr|address|shown|showed|图|图片|截图|照片", q):
            return QueryPlan(
                family="visual",
                primary_reader="graph",
                supporting_readers=["atom", "tree"],
                must_have_layers=["image_evidence", "fact"],
                reason="Visual questions need image evidence grounded by linked facts.",
            )
        if re.search(r"\bafter\b|\bbefore\b|之后|之前|后来|计划|打算", q):
            return QueryPlan(
                family="temporal_relational",
                primary_reader="graph",
                supporting_readers=["tree", "atom"],
                must_have_layers=["event", "fact", "temporal_tree"],
                reason="Order-sensitive questions need relation/event structure plus chronology support.",
            )
        if re.search(r"\bwhen\b|\byesterday\b|\blast week\b|\bdate\b|什么时候|哪天|日期|时间|昨天|上周", q):
            return QueryPlan(
                family="temporal",
                primary_reader="tree",
                supporting_readers=["graph", "atom"],
                must_have_layers=["temporal_tree", "event"],
                reason="Chronology questions should enter via time blocks, then add event support.",
            )
        if re.search(r"\bwho\b|\bwhich company\b|\brelationship\b|谁|关系|哪个公司|谁帮", q):
            return QueryPlan(
                family="relational",
                primary_reader="graph",
                supporting_readers=["atom", "tree"],
                must_have_layers=["entity", "fact", "path_grounding"],
                reason="Relation questions need entity/event traversal, fact grounding, and explicit graph-path support.",
            )
        return QueryPlan(
            family="general",
            primary_reader="atom",
            supporting_readers=["tree", "graph"],
            must_have_layers=["fact"],
            reason="Fallback to fact-oriented retrieval.",
        )

    def search_flat_text(self, query: str) -> dict[str, Any]:
        hits: list[Hit] = []
        for obs in self.observations:
            score = self._lexical_score(query, obs.content)
            if score > 0:
                hits.append(Hit(obs.obs_id, "flat_text", round(score, 3), obs.content, obs.event_time))
        hits.sort(key=lambda x: (x.score, x.event_time), reverse=True)
        return {
            "mode": "flat_text",
            "query": query,
            "hits": [asdict(h) for h in hits[:8]],
        }

    def search_primary_only(self, query: str) -> dict[str, Any]:
        plan = self.plan(query)
        hits = self._read(plan.primary_reader, query, query_time="")
        return self._pack("primary_only", query, plan, hits, [])

    def search_contract_aware(self, query: str, query_time: str) -> dict[str, Any]:
        plan = self.plan(query)
        hits = self._read(plan.primary_reader, query, query_time=query_time)
        coverage = self._coverage(plan, hits)
        used_sources: list[str] = []
        groups = [hits]
        for missing in coverage["missing_layers"]:
            reader = self._reader_for_missing(missing)
            if reader is None or reader == plan.primary_reader or reader in used_sources:
                continue
            groups.append(self._read(reader, query, query_time=query_time))
            used_sources.append(reader)
        merged = self._merge(groups)
        return self._pack("contract_aware", query, plan, merged, used_sources)

    def _pack(self, mode: str, query: str, plan: QueryPlan, hits: list[Hit], second_pass_sources: list[str]) -> dict[str, Any]:
        coverage = self._coverage(plan, hits)
        return {
            "mode": mode,
            "query": query,
            "plan": asdict(plan),
            "coverage": coverage,
            "second_pass_sources": second_pass_sources,
            "hits": [asdict(h) for h in hits[:8]],
        }

    def _coverage(self, plan: QueryPlan, hits: list[Hit]) -> dict[str, Any]:
        present = []
        seen = set()
        has_path_grounding = False
        for hit in hits[:8]:
            layer = hit.layer
            if layer not in seen:
                seen.add(layer)
                present.append(layer)
            path_edge_ids = hit.trace.get("path_edge_ids") or []
            if isinstance(path_edge_ids, list) and any(str(edge).strip() for edge in path_edge_ids):
                has_path_grounding = True
        matched = []
        for layer in plan.must_have_layers:
            if layer == "path_grounding":
                if has_path_grounding:
                    matched.append(layer)
            elif layer in seen:
                matched.append(layer)
        missing = [layer for layer in plan.must_have_layers if layer not in matched]
        ratio = len(matched) / max(len(plan.must_have_layers), 1)
        return {
            "required_layers": plan.must_have_layers,
            "present_layers": present,
            "matched_layers": matched,
            "missing_layers": missing,
            "coverage_ratio": round(ratio, 3),
            "contract_ok": ratio >= 1.0,
            "has_path_grounding": has_path_grounding,
        }

    def _reader_for_missing(self, layer: str) -> str | None:
        if layer == "temporal_tree":
            return "tree"
        if layer in {"event", "entity", "image_evidence"}:
            return "graph"
        if layer == "path_grounding":
            return "graph"
        if layer == "fact":
            return "atom"
        return None

    def _read(self, reader: str, query: str, query_time: str) -> list[Hit]:
        if reader == "tree":
            return self._search_tree(query, query_time=query_time)
        if reader == "graph":
            return self._search_graph(query, query_time=query_time)
        return self._search_atom(query, query_time=query_time)

    def _search_tree(self, query: str, query_time: str) -> list[Hit]:
        q = query.lower()
        preferred_dates = self._preferred_dates(q, query_time)
        hits: list[Hit] = []
        for block in self.blocks:
            score = self._lexical_score(query, block.content)
            if block.key in preferred_dates:
                score += 1.5 if block.level == "day" else 0.75
            if "when" in q or "yesterday" in q or "last week" in q:
                if block.level == "day":
                    score += 0.6
            if score > 0:
                hits.append(
                    Hit(
                        source=block.block_id,
                        layer="temporal_tree",
                        score=round(score, 3),
                        content=block.content,
                        event_time=block.key,
                        trace={"reader": "tree"},
                    )
                )
        hits.sort(key=lambda x: (x.score, x.event_time), reverse=True)
        return hits[:8]

    def _search_graph(self, query: str, query_time: str) -> list[Hit]:
        q = query.lower()
        hits: list[Hit] = []
        for node in self.nodes:
            score = self._lexical_score(query, node.content)
            if node.node_type == "entity" and re.search(r"\bwho\b|谁|关系|who helped", q):
                score += 1.0
            if node.node_type == "event" and re.search(r"\bafter\b|\bbefore\b|\bwhen\b|昨天|上周", q):
                score += 1.0
            if node.node_type == "image_evidence" and re.search(r"image|photo|screenshot|address|shown|showed|截图|照片", q):
                score += 2.0
            if score > 0:
                trace = {"reader": "graph"}
                if re.search(r"\bwho\b|\bwhich company\b|\brelationship\b|谁|关系|哪个公司|谁帮", q):
                    path_edge_ids = self._path_trace_for_node(node.node_id)
                    if path_edge_ids:
                        trace["path_edge_ids"] = path_edge_ids
                hits.append(
                    Hit(
                        source=node.node_id,
                        layer=node.node_type,
                        score=round(score, 3),
                        content=node.content,
                        event_time=node.event_time,
                        trace=trace,
                    )
                )
        hits.sort(key=lambda x: (x.score, x.event_time), reverse=True)
        return hits[:8]

    def _search_atom(self, query: str, query_time: str) -> list[Hit]:
        hits: list[Hit] = []
        for atom in self.atoms:
            score = self._lexical_score(query, atom.statement)
            if re.search(r"\bwhen\b|昨天|上周", query.lower()) and atom.event_time:
                score += 0.5
            if score > 0:
                hits.append(
                    Hit(
                        source=atom.atom_id,
                        layer="fact",
                        score=round(score, 3),
                        content=atom.statement,
                        event_time=atom.event_time,
                        trace={"reader": "atom"},
                    )
                )
        hits.sort(key=lambda x: (x.score, x.event_time), reverse=True)
        return hits[:8]

    def _merge(self, groups: list[list[Hit]]) -> list[Hit]:
        merged: dict[tuple[str, str], Hit] = {}
        for group in groups:
            for hit in group:
                key = (hit.source, hit.layer)
                old = merged.get(key)
                if old is None or hit.score > old.score:
                    merged[key] = hit
        return sorted(merged.values(), key=lambda x: (x.score, x.event_time), reverse=True)

    def _path_trace_for_node(self, node_id: str) -> list[str]:
        direct = [edge.edge_id for edge in self.edges if edge.source_id == node_id or edge.target_id == node_id]
        if direct:
            return direct[:4]
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_event_time(self, text: str, mention_time: str) -> str:
        direct = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        if direct:
            return direct.group(1)
        mention_day = iso_date(mention_time)
        lower = text.lower()
        if "yesterday" in lower:
            return shift_days(mention_day, -1)
        if "two days ago" in lower:
            return shift_days(mention_day, -2)
        if "last week" in lower:
            return shift_days(mention_day, -7)
        return mention_day

    def _preferred_dates(self, query: str, query_time: str) -> set[str]:
        if not query_time:
            return set()
        anchor = iso_date(query_time)
        if "yesterday" in query:
            return {shift_days(anchor, -1)}
        if "last week" in query:
            return {shift_days(anchor, -7)}
        return set()

    def _infer_image_subject(self, obs: Observation) -> str:
        lower = (obs.caption + " " + obs.ocr).lower()
        if "lease" in lower:
            return "lease_screenshot"
        if "badge" in lower:
            return "conference_badge"
        if "ticket" in lower:
            return "travel_ticket"
        return "image_observation"

    @staticmethod
    def _looks_like_entity(text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        if re.match(r"^\d", text):
            return False
        if len(text) <= 2:
            return False
        return True

    @staticmethod
    def _lexical_score(query: str, content: str) -> float:
        q_terms = NanoGeneralizableMemory._terms(query)
        c = content.lower()
        score = 0.0
        for term in q_terms:
            if term and term in c:
                score += 1.0
        if re.search(r"\b20\d{2}-\d{2}-\d{2}\b", c) and re.search(r"\bwhen\b|日期|时间|哪天", query.lower()):
            score += 0.6
        return score

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [x for x in re.findall(r"[a-zA-Z]+(?:\-[a-zA-Z]+)?|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{2,}", text.lower()) if x not in {"the", "did", "what", "when", "who", "after", "before"}]


def build_system() -> NanoGeneralizableMemory:
    mem = NanoGeneralizableMemory()
    mem.append_text("Maya signed the Riverside lease on 2026-03-03, and I am only mentioning it now after travel.", "2026-03-10T09:00:00Z")
    mem.append_text("Leo joined Orchard Labs on 2026-02-14.", "2026-02-15T11:00:00Z")
    mem.append_text("Leo left Northlight Studio on 2026-01-20.", "2026-01-21T12:00:00Z")
    mem.append_text("Nora helped Maya prepare the visa checklist on 2026-04-02.", "2026-04-03T09:00:00Z")
    mem.append_text("Maya planned to relocate to Lisbon after leaving Northlight Studio.", "2026-04-04T09:00:00Z")
    mem.append_text("Maya presented the keynote deck yesterday.", "2026-05-10T08:00:00Z")
    mem.append_text("Maya had the investor board review last week.", "2026-05-18T09:00:00Z")
    mem.append_image(
        caption="Lease document screenshot",
        ocr="Riverside Lease Agreement Rua Augusta 14 Lisbon",
        mention_time="2026-03-10T09:05:00Z",
        tags=["lease", "address", "lisbon"],
    )
    mem.build()
    return mem


def build_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="retrospective_sign_date",
            query="When did Maya sign the Riverside lease?",
            query_time="2026-03-10T10:00:00Z",
            expected_keywords=["2026-03-03", "signed the Riverside lease"],
            note="Retrospective mention should preserve true event time instead of collapsing to write time.",
        ),
        EvalCase(
            case_id="relation_helper",
            query="Who helped Maya prepare the visa checklist?",
            query_time="2026-04-05T09:00:00Z",
            expected_keywords=["Nora", "visa checklist"],
            note="Relation question should prefer graph/entity evidence over flat lexical scanning.",
        ),
        EvalCase(
            case_id="plan_after_leaving",
            query="What did Maya plan to do after leaving Northlight Studio?",
            query_time="2026-04-05T09:00:00Z",
            expected_keywords=["planned", "relocate to Lisbon"],
            note="Temporal-relational question should expose both event/plan and chronology support.",
        ),
        EvalCase(
            case_id="yesterday_keynote",
            query="What happened yesterday about the keynote?",
            query_time="2026-05-11T08:30:00Z",
            expected_keywords=["2026-05-09", "keynote"],
            note="Relative-time query should resolve against query anchor, not only mention time.",
        ),
        EvalCase(
            case_id="last_week_board",
            query="What happened last week in the board review?",
            query_time="2026-05-18T20:00:00Z",
            expected_keywords=["2026-05-11", "board review"],
            note="Week-scale chronology retrieval should hit temporal tree support.",
        ),
        EvalCase(
            case_id="visual_address",
            query="What address was shown in the lease screenshot?",
            query_time="2026-03-10T10:00:00Z",
            expected_keywords=["Rua Augusta 14", "Lease Agreement"],
            note="Visual query should use image evidence as a first-class memory object.",
        ),
    ]


def keywords_ok(hits: list[dict[str, Any]], expected_keywords: list[str]) -> bool:
    blob = "\n".join(str(hit.get("content", "")) for hit in hits[:8]).lower()
    return all(keyword.lower() in blob for keyword in expected_keywords)


def evaluate() -> dict[str, Any]:
    mem = build_system()
    cases = build_cases()
    results: list[dict[str, Any]] = []
    summary = {
        "cases": len(cases),
        "flat_text_ok": 0,
        "primary_only_ok": 0,
        "contract_aware_ok": 0,
        "contract_improves_over_primary": [],
        "primary_improves_over_flat": [],
    }

    for case in cases:
        flat_run = mem.search_flat_text(case.query)
        primary_run = mem.search_primary_only(case.query)
        contract_run = mem.search_contract_aware(case.query, case.query_time)

        flat_ok = keywords_ok(flat_run["hits"], case.expected_keywords)
        primary_ok = keywords_ok(primary_run["hits"], case.expected_keywords) and primary_run["coverage"]["contract_ok"]
        contract_ok = keywords_ok(contract_run["hits"], case.expected_keywords) and contract_run["coverage"]["contract_ok"]

        summary["flat_text_ok"] += int(flat_ok)
        summary["primary_only_ok"] += int(primary_ok)
        summary["contract_aware_ok"] += int(contract_ok)
        if (not flat_ok) and primary_ok:
            summary["primary_improves_over_flat"].append(case.case_id)
        if (not primary_ok) and contract_ok:
            summary["contract_improves_over_primary"].append(case.case_id)

        results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "query_time": case.query_time,
                "expected_keywords": case.expected_keywords,
                "note": case.note,
                "flat_text": {**flat_run, "ok": flat_ok},
                "primary_only": {**primary_run, "ok": primary_ok},
                "contract_aware": {**contract_run, "ok": contract_ok},
            }
        )

    return {
        "observations": [asdict(x) for x in mem.observations],
        "atoms": [asdict(x) for x in mem.atoms],
        "blocks": [asdict(x) for x in mem.blocks],
        "nodes": [asdict(x) for x in mem.nodes],
        "edges": [asdict(x) for x in mem.edges],
        "summary": summary,
        "results": results,
    }


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = []
    for item in payload["results"]:
        rows.append(
            f"""
            <tr>
              <td>{esc(item['case_id'])}</td>
              <td>{esc(item['query'])}<br><span class="muted">{esc(item['query_time'])}</span></td>
              <td>{'yes' if item['flat_text']['ok'] else 'no'}</td>
              <td>{'yes' if item['primary_only']['ok'] else 'no'}<br><span class="muted">coverage={esc(item['primary_only']['coverage']['coverage_ratio'])}</span></td>
              <td>{'yes' if item['contract_aware']['ok'] else 'no'}<br><span class="muted">coverage={esc(item['contract_aware']['coverage']['coverage_ratio'])}</span></td>
              <td>{esc(', '.join(item['contract_aware']['second_pass_sources']) or '-')}</td>
              <td>{esc(item['note'])}</td>
            </tr>
            """
        )

    sample_case = payload["results"][0]

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano: Generalizable Stream + Graph + Contract</title>
  <style>
    :root {{
      --bg: #f5f7fb; --panel: #fff; --text: #18212f; --muted: #5b6678; --line: #dde4ee;
      --blue: #2f6fed; --green: #14804a; --amber: #b7791f; --soft-blue: #eef4ff; --shadow: 0 10px 28px rgba(24,33,47,.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height: 1.65; }}
    .wrap {{ max-width: 1220px; margin: 0 auto; padding: 28px 20px 72px; }}
    .hero {{ background: linear-gradient(135deg,#18212f 0%,#22467f 58%,#2f6fed 100%); color: #fff; border-radius: 18px; padding: 28px 30px; box-shadow: var(--shadow); margin-bottom: 18px; }}
    .hero h1 {{ margin: 0 0 10px; font-size: 30px; line-height: 1.18; }}
    .hero p {{ margin: 0; max-width: 920px; color: rgba(255,255,255,.9); }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 20px 22px; box-shadow: var(--shadow); margin-bottom: 18px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .pill {{ display:inline-block; padding:6px 10px; border-radius:999px; background:var(--soft-blue); color:var(--blue); font-size:12px; font-weight:600; margin-right:8px; margin-top:8px; }}
    .muted {{ color: var(--muted); font-size: 12px; }}
    h2 {{ margin: 0 0 12px; font-size: 22px; }}
    h3 {{ margin: 18px 0 10px; font-size: 17px; }}
    ul {{ margin: 10px 0; padding-left: 20px; }}
    li {{ margin: 6px 0; }}
    table {{ width:100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f7fb; font-size: 13px; }}
    code, pre {{ font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; }}
    pre {{ background: #f7f9fc; border:1px solid var(--line); border-radius:10px; padding:12px 14px; overflow:auto; white-space:pre-wrap; }}
    .kpis {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; margin-top: 12px; }}
    .kpi {{ border:1px solid var(--line); border-radius: 12px; padding: 14px; background: #fff; }}
    .kpi .label {{ display:block; font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
    .kpi .value {{ font-size: 28px; font-weight: 700; }}
    @media (max-width: 920px) {{ .grid, .kpis {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano: Generalizable Stream + Graph + Contract</h1>
      <p>
        这不是给 LoCoMo 或某个固定 benchmark 打补丁的脚本，而是一份更接近论文方法原型的 nano 实现：
        把 <b>three-clock time</b>、<b>dual-backbone retrieval</b>、<b>evidence contract</b> 和
        <b>contract-driven second pass</b> 放进同一个最小系统里，看它们为什么是泛化改进。
      </p>
      <div>
        <span class="pill">stream memory</span>
        <span class="pill">temporal tree</span>
        <span class="pill">relation graph</span>
        <span class="pill">image evidence</span>
        <span class="pill">contract-aware retrieval</span>
      </div>
    </section>

    <section class="panel">
      <h2>为什么要补这版 nano</h2>
      <p>
        过去的 nano 大多各测一个点，比如 three-clock、dual-backbone、type-aware second pass。
        这版把它们揉在一起，是为了更贴近近两年 30 篇 memory / graph / agent retrieval 论文给出的共同方向：
      </p>
      <ul>
        <li><b>RAPTOR / TiMem</b>：需要时间结构，不是只有平铺 fact。</li>
        <li><b>GraphReader / HippoRAG / Zep</b>：关系题要把图当读路径，而不是仅当存储副产物。</li>
        <li><b>Self-RAG / AgentIR</b>：检索要能自检并补对 reader，不是只会“再多搜一点”。</li>
        <li><b>LongMemEval / LoCoMo</b>：真正难的是时间、关系、多跳、相对时间，而不是单条事实存没存进去。</li>
        <li><b>CVPR 路线</b>：图像/截图证据要成为一等记忆对象，而不是纯文本附件。</li>
      </ul>
    </section>

    <section class="panel">
      <h2>实验摘要</h2>
      <div class="kpis">
        <div class="kpi"><span class="label">Flat Text</span><span class="value">{summary['flat_text_ok']} / {summary['cases']}</span></div>
        <div class="kpi"><span class="label">Primary Only</span><span class="value">{summary['primary_only_ok']} / {summary['cases']}</span></div>
        <div class="kpi"><span class="label">Contract Aware</span><span class="value">{summary['contract_aware_ok']} / {summary['cases']}</span></div>
      </div>
      <p class="muted">
        这里的 “ok” 不是只看关键词命中，还要求证据契约覆盖完整，即 coverage ratio = 1.0。
      </p>
    </section>

    <section class="panel">
      <h2>结果表</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query</th>
            <th>Flat Text</th>
            <th>Primary Only</th>
            <th>Contract Aware</th>
            <th>Second Pass</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>这个原型做对了什么</h2>
        <ul>
          <li>把 <code>event_time</code>、<code>mention_time</code>、<code>write_time</code> 明确拆开。</li>
          <li>时间题优先走 tree，关系题优先走 graph，视觉题优先走 image evidence。</li>
          <li>当 primary reader 的证据不完整时，不是一律补 graph，而是根据缺的层补 tree / graph / atom。</li>
          <li>视觉证据不再只是 OCR 字符串，而是独立的 <code>image_evidence</code> 节点。</li>
        </ul>
      </div>
      <div class="panel">
        <h2>它刻意没做什么</h2>
        <ul>
          <li>没有 LLM 抽取，只有通用事件/关系 regex。</li>
          <li>没有 embedding 或 learned reranker。</li>
          <li>没有多 session、cursor、异步 worker。</li>
          <li>没有任何 LoCoMo 特化实体名或关键词 hardcode。</li>
        </ul>
      </div>
    </section>

    <section class="panel">
      <h2>样例：第一题的 contract-aware 输出</h2>
      <pre>{esc(json.dumps(sample_case['contract_aware'], ensure_ascii=False, indent=2))}</pre>
    </section>

    <section class="panel">
      <h2>这版 nano 对主系统的映射</h2>
      <ul>
        <li><code>append_text / append_image</code> ~= session stream / observation ingest</li>
        <li><code>_extract_atoms</code> ~= raw atom extraction</li>
        <li><code>_build_temporal_tree</code> ~= organized projection / temporal hierarchy</li>
        <li><code>_build_graph</code> ~= graph sync / entity-event-fact layer</li>
        <li><code>plan + _coverage + reader_for_missing</code> ~= planner + evidence contract + self-check second pass</li>
      </ul>
      <p>
        如果下一步要继续往 CVPR 风格方法写作推进，这份 nano 最适合当“method intuition page”。
        它的价值不在 benchmark 分数，而在于它把 30 篇文献里最反复出现的 4 个结构性原则，收束成了一份读得懂的最小系统。
      </p>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    payload = evaluate()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps({
        "out_json": str(OUT_JSON),
        "out_html": str(OUT_HTML),
        "summary": payload["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
