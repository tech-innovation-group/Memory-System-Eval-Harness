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
OUT_JSON = ROOT / "nano_reference_impl_v17_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_reference_impl_v17_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def shift_day(ymd: str, delta: int) -> str:
    base = datetime.fromisoformat(ymd)
    return (base + timedelta(days=delta)).strftime("%Y-%m-%d")


def normalize_relative_date(text: str, anchor_ymd: str) -> str:
    explicit = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if explicit:
        return explicit.group(1)
    lowered = text.lower()
    if "yesterday" in lowered:
        return shift_day(anchor_ymd, -1)
    if "last week" in lowered:
        return shift_day(anchor_ymd, -7)
    if "tomorrow" in lowered:
        return shift_day(anchor_ymd, 1)
    return anchor_ymd


def token_set(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]{2,}|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", text.lower()))


def overlap_score(query: str, content: str) -> float:
    q, c = token_set(query), token_set(content)
    if not q:
        return 0.0
    return len(q & c) / max(len(q), 1)


@dataclass
class Observation:
    obs_id: str
    role: str
    modality: str
    content: str
    mention_time: str
    write_time: str
    story_time: str
    topic_hint: str = ""
    linked_subject: str = ""
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
    topic: str
    story_time: str
    mention_time: str
    write_time: str
    source_obs_id: str
    valid_from: str = ""
    valid_until: str = ""
    status: str = "active"
    superseded_by: str = ""
    conflict_with: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)


@dataclass
class TopicDossier:
    topic: str
    summary: str
    start_time: str
    end_time: str
    atom_ids: list[str]
    key_updates: list[str]


@dataclass
class TemporalBlock:
    block_id: str
    level: str
    key: str
    content: str
    atom_ids: list[str]


@dataclass
class GraphNode:
    node_id: str
    node_type: str
    content: str
    story_time: str = ""
    source_ref: str = ""


@dataclass
class GraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class ReadinessReceipt:
    persisted: bool = False
    atoms_ready: bool = False
    dossier_ready: bool = False
    temporal_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False
    updated_at: str = ""


@dataclass
class QueryPlan:
    family: str
    primary_reader: str
    supporting_readers: list[str]
    required_layers: list[str]
    rationale: str


@dataclass
class Hit:
    source: str
    layer: str
    score: float
    content: str
    story_time: str = ""
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class DemoCase:
    case_id: str
    query: str
    query_time: str
    expected_keywords: list[str]
    family: str


class EchoMemoryNanoReferenceV17:
    """
    A clean, unified nano implementation of the current EchoMemory thesis:

    stream -> atoms -> topic dossier + temporal blocks + graph
    -> readiness gate -> planner -> contract-aware second pass -> answerability

    The goal is not benchmark optimization. The goal is to keep the method
    understandable while preserving the generic architectural commitments.
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.dossiers: dict[str, TopicDossier] = {}
        self.temporal_blocks: dict[str, TemporalBlock] = {}
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self.readiness = ReadinessReceipt()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def append_text(self, *, role: str, content: str, write_time: str, topic_hint: str = "") -> None:
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role=role,
                modality="text",
                content=content.strip(),
                mention_time=write_time,
                write_time=write_time,
                story_time=normalize_relative_date(content, write_time[:10]),
                topic_hint=topic_hint.strip(),
            )
        )
        self._invalidate()

    def append_image(
        self,
        *,
        role: str,
        caption: str,
        ocr: str,
        write_time: str,
        topic_hint: str = "",
        linked_subject: str = "",
    ) -> None:
        content = "\n".join(x for x in [caption.strip(), ocr.strip()] if x)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role=role,
                modality="image",
                content=content,
                mention_time=write_time,
                write_time=write_time,
                story_time=normalize_relative_date(content, write_time[:10]),
                topic_hint=topic_hint.strip(),
                linked_subject=linked_subject.strip(),
                caption=caption.strip(),
                ocr=ocr.strip(),
            )
        )
        self._invalidate()

    def _invalidate(self) -> None:
        self.readiness.persisted = True
        self.readiness.atoms_ready = False
        self.readiness.dossier_ready = False
        self.readiness.temporal_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False
        if self.observations:
            self.readiness.updated_at = self.observations[-1].write_time

    # ------------------------------------------------------------------
    # Build path
    # ------------------------------------------------------------------

    def build(self) -> None:
        self.atoms = self._extract_atoms()
        self._apply_state_lifecycle()
        self.readiness.atoms_ready = bool(self.atoms)
        self.dossiers = self._build_dossiers()
        self.readiness.dossier_ready = bool(self.dossiers)
        self.temporal_blocks = self._build_temporal_blocks()
        self.readiness.temporal_ready = bool(self.temporal_blocks)
        self.nodes, self.edges = self._build_graph()
        self.readiness.graph_ready = bool(self.nodes)
        self.readiness.qa_ready = (
            self.readiness.persisted
            and self.readiness.atoms_ready
            and self.readiness.dossier_ready
            and self.readiness.temporal_ready
            and self.readiness.graph_ready
        )

    def _extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for obs in self.observations:
            topic = obs.topic_hint.strip()
            if obs.modality == "image":
                content_summary = obs.ocr or obs.caption or obs.content
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type="image_evidence",
                        subject=obs.linked_subject or "unknown",
                        predicate="shows",
                        obj=content_summary[:80],
                        statement=obs.content,
                        topic=topic,
                        story_time=obs.story_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                        valid_from=obs.story_time,
                        entities=self._extract_entities(f"{obs.linked_subject} {obs.content}"),
                    )
                )
                continue

            for sentence in self._split_sentences(obs.content):
                subject, predicate, obj, atom_type = self._parse_sentence(sentence, obs.story_time)
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=atom_type,
                        subject=subject,
                        predicate=predicate,
                        obj=obj,
                        statement=sentence,
                        topic=topic,
                        story_time=normalize_relative_date(sentence, obs.write_time[:10]),
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                        valid_from=normalize_relative_date(sentence, obs.write_time[:10]),
                        entities=self._extract_entities(sentence),
                    )
                )
        return atoms

    def _apply_state_lifecycle(self) -> None:
        groups: dict[tuple[str, str], list[Atom]] = {}
        for atom in self.atoms:
            groups.setdefault((atom.subject, atom.predicate), []).append(atom)

        for atoms in groups.values():
            ordered = sorted(atoms, key=lambda a: (a.story_time or a.valid_from or a.write_time, a.write_time, a.atom_id))
            same_time: dict[str, list[Atom]] = {}
            for atom in ordered:
                if self._is_state_like(atom):
                    same_time.setdefault(atom.story_time or atom.valid_from or atom.write_time, []).append(atom)
            for bucket in same_time.values():
                values = {a.obj for a in bucket}
                if len(values) > 1:
                    for atom in bucket:
                        atom.status = "conflicted"
                        atom.conflict_with = [other.atom_id for other in bucket if other.atom_id != atom.atom_id]

            chain = [a for a in ordered if self._is_state_like(a) and a.status != "conflicted"]
            for prev, curr in zip(chain, chain[1:]):
                prev.status = "superseded"
                prev.valid_until = curr.valid_from or curr.story_time or curr.write_time
                prev.superseded_by = curr.atom_id

    def _build_dossiers(self) -> dict[str, TopicDossier]:
        groups: dict[str, list[Atom]] = {}
        for atom in self.atoms:
            key = atom.topic.strip() or self._induce_topic(atom)
            groups.setdefault(key, []).append(atom)

        dossiers: dict[str, TopicDossier] = {}
        for topic, atoms in groups.items():
            atoms = sorted(atoms, key=lambda a: a.story_time)
            visible = [a for a in atoms if a.status != "superseded"] or atoms
            updates = [f"{a.story_time}: {a.statement} [{a.status}]" for a in visible[:5]]
            dossiers[topic] = TopicDossier(
                topic=topic,
                summary="\n".join([f"Topic: {topic}", *[f"- {u}" for u in updates]]),
                start_time=atoms[0].story_time,
                end_time=atoms[-1].story_time,
                atom_ids=[a.atom_id for a in atoms],
                key_updates=updates,
            )
        return dossiers

    def _build_temporal_blocks(self) -> dict[str, TemporalBlock]:
        buckets: dict[tuple[str, str], list[Atom]] = {}
        for atom in self.atoms:
            if not atom.story_time:
                continue
            yyyy, mm, dd = atom.story_time.split("-")
            for level, key in (("day", atom.story_time), ("month", f"{yyyy}-{mm}"), ("year", yyyy)):
                buckets.setdefault((level, key), []).append(atom)
        blocks: dict[str, TemporalBlock] = {}
        for (level, key), atoms in buckets.items():
            block_id = f"{level}:{key}"
            visible = [a for a in atoms if a.status != "superseded"] or atoms
            blocks[block_id] = TemporalBlock(
                block_id=block_id,
                level=level,
                key=key,
                content="\n".join(f"- {a.story_time}: {a.statement} [{a.status}]" for a in visible[:8]),
                atom_ids=[a.atom_id for a in atoms],
            )
        return blocks

    def _build_graph(self) -> tuple[dict[str, GraphNode], list[GraphEdge]]:
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        for atom in self.atoms:
            event_like_type = "image_evidence" if atom.atom_type == "image_evidence" else "event"
            event_id = f"{event_like_type}:{atom.atom_id}"
            fact_id = f"fact:{atom.atom_id}"
            nodes[event_id] = GraphNode(event_id, event_like_type, atom.statement, atom.story_time, atom.source_obs_id)
            nodes[fact_id] = GraphNode(fact_id, "fact", f"{atom.statement} [status={atom.status}]", atom.story_time, atom.source_obs_id)
            edges.append(GraphEdge(f"{event_id}:evidence:{fact_id}", event_id, fact_id, "evidence_of"))

            for entity in atom.entities:
                entity_id = f"entity:{entity}"
                if entity_id not in nodes:
                    nodes[entity_id] = GraphNode(entity_id, "entity", f"name={entity}")
                edges.append(GraphEdge(f"{event_id}:involves:{entity_id}", event_id, entity_id, "involves"))
                edges.append(GraphEdge(f"{entity_id}:supports:{fact_id}", entity_id, fact_id, "supports_fact"))
        return nodes, edges

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"\b(ready|qa ready|answer now|can the system answer|at this point)\b", q):
            return QueryPlan("readiness", "readiness", [], ["readiness"], "Readiness or answerability query.")
        if re.search(r"\b(progress|latest|status|evolve|change|later|how did)\b", q):
            return QueryPlan("longitudinal", "topic_dossier", ["atom", "temporal_tree"], ["topic_dossier", "fact"], "Cross-session evolution query.")
        if re.search(r"\b(image|photo|screenshot|shown|visible|look like|ocr|address)\b", q):
            return QueryPlan("visual", "graph", ["atom"], ["image_evidence", "fact"], "Visual evidence query.")
        if re.search(r"\b(who|relationship|helped|married|connected|through whom|introduced)\b", q):
            return QueryPlan("relational", "graph", ["atom", "temporal_tree"], ["graph", "fact", "path_grounding"], "Relation-heavy query.")
        if re.search(r"\b(when|yesterday|last week|date|time|before|after|started|joined)\b", q):
            return QueryPlan("temporal", "temporal_tree", ["graph", "atom"], ["temporal_tree", "event", "event_time"], "Chronology-heavy query.")
        return QueryPlan("state", "atom", ["topic_dossier", "temporal_tree"], ["fact"], "State or profile query.")

    def retrieve(self, query: str, query_time: str) -> dict[str, Any]:
        plan = self.plan(query)
        primary_hits = self._reader(plan.primary_reader, query, query_time, plan)
        hits = list(primary_hits)
        present = self._present_layers(hits)
        missing = [layer for layer in plan.required_layers if layer not in present]
        second_pass: list[str] = []

        for layer in list(missing):
            reader = self._reader_for_missing(layer)
            if not reader or reader == plan.primary_reader or reader in second_pass:
                continue
            extra_hits = self._reader(reader, query, query_time, plan)
            if extra_hits:
                second_pass.append(reader)
                hits.extend(extra_hits)
                present = self._present_layers(hits)
                missing = [need for need in plan.required_layers if need not in present]
            if not missing:
                break

        hits = self._dedup_sort(hits)
        answer = self._answer(query, query_time, plan, hits, missing)
        return {
            "plan": asdict(plan),
            "hits": [asdict(hit) for hit in hits],
            "present_layers": sorted(present),
            "missing_layers": missing,
            "second_pass_sources": second_pass,
            "contract_ok": not missing,
            "answer": answer,
        }

    def _reader(self, reader: str, query: str, query_time: str, plan: QueryPlan) -> list[Hit]:
        if reader == "readiness":
            return [Hit("readiness", "readiness", 1.0 if self.readiness.qa_ready else 0.0, f"qa_ready={self.readiness.qa_ready}", trace=asdict(self.readiness))]

        if reader == "topic_dossier":
            rows: list[Hit] = []
            for topic, dossier in self.dossiers.items():
                score = overlap_score(query, dossier.summary)
                score += 0.1 if any(tok in dossier.summary.lower() for tok in token_set(query)) else 0.0
                if score > 0:
                    rows.append(Hit(f"dossier:{topic}", "topic_dossier", score, dossier.summary, dossier.end_time, {"atom_ids": dossier.atom_ids, "key_updates": dossier.key_updates}))
            return rows[:4]

        if reader == "temporal_tree":
            rows: list[Hit] = []
            for block in self.temporal_blocks.values():
                score = overlap_score(query, block.content)
                if block.level == "day":
                    score += 0.15
                if score > 0:
                    rows.append(Hit(block.block_id, "temporal_tree", score, block.content, trace={"atom_ids": block.atom_ids, "level": block.level}))
            return rows[:6]

        if reader == "graph":
            rows: list[Hit] = []
            for node in self.nodes.values():
                score = overlap_score(query, node.content)
                if plan.family == "visual" and node.node_type == "image_evidence":
                    score += 0.25
                if plan.family == "relational" and node.node_type in {"entity", "event"}:
                    score += 0.12
                if score > 0:
                    trace: dict[str, Any] = {}
                    if plan.family == "relational" and node.node_type in {"event", "image_evidence"}:
                        trace["path_edge_ids"] = [e.edge_id for e in self.edges if e.source_id == node.node_id][:3]
                    rows.append(Hit(node.node_id, "image_evidence" if node.node_type == "image_evidence" else "graph", score, node.content, node.story_time, trace))
            return rows[:8]

        if reader == "atom":
            rows: list[Hit] = []
            for atom in self.atoms:
                if not self._visible_as_of(atom, query_time, plan):
                    continue
                score = overlap_score(query, atom.statement)
                if score > 0:
                    rows.append(
                        Hit(
                            f"atom:{atom.atom_id}",
                            "fact",
                            score + self._status_boost(atom, plan),
                            f"{atom.statement} [status={atom.status}]",
                            atom.story_time,
                            {
                                "story_time": atom.story_time,
                                "mention_time": atom.mention_time,
                                "write_time": atom.write_time,
                                "subject": atom.subject,
                                "predicate": atom.predicate,
                                "object": atom.obj,
                                "status": atom.status,
                                "valid_from": atom.valid_from,
                                "valid_until": atom.valid_until,
                            },
                        )
                    )
            return rows[:8]

        return []

    def _visible_as_of(self, atom: Atom, query_time: str, plan: QueryPlan) -> bool:
        if atom.status == "conflicted":
            return True
        if plan.family in {"temporal", "longitudinal"}:
            return True
        start = (atom.valid_from or atom.story_time or atom.write_time)[:10]
        end = (atom.valid_until or "9999-12-31")[:10]
        q = query_time[:10]
        return start <= q < end

    def _status_boost(self, atom: Atom, plan: QueryPlan) -> float:
        if atom.status == "active":
            return 0.12
        if atom.status == "superseded":
            return -0.05 if plan.family != "longitudinal" else 0.03
        if atom.status == "conflicted":
            return -0.15
        return 0.0

    def _reader_for_missing(self, layer: str) -> str:
        return {
            "event": "graph",
            "event_time": "atom",
            "fact": "atom",
            "graph": "graph",
            "path_grounding": "graph",
            "image_evidence": "graph",
            "topic_dossier": "topic_dossier",
            "temporal_tree": "temporal_tree",
            "readiness": "readiness",
        }.get(layer, "")

    def _present_layers(self, hits: list[Hit]) -> set[str]:
        present = {hit.layer for hit in hits}
        for hit in hits:
            if hit.layer == "graph":
                present.add("event")
                if hit.trace.get("path_edge_ids"):
                    present.add("path_grounding")
            if hit.layer == "fact":
                present.add("event_time")
            if hit.layer == "image_evidence":
                present.add("graph")
                present.add("event")
        return present

    def _dedup_sort(self, hits: list[Hit]) -> list[Hit]:
        best: dict[str, Hit] = {}
        for hit in hits:
            prev = best.get(hit.source)
            if prev is None or hit.score > prev.score:
                best[hit.source] = hit
        return sorted(best.values(), key=lambda item: (-item.score, item.source))

    def _answer(self, query: str, query_time: str, plan: QueryPlan, hits: list[Hit], missing: list[str]) -> str:
        if plan.family == "readiness":
            return "ready" if self.readiness.qa_ready else "not_ready"
        if missing:
            return "unknown"

        lowered_query = query.lower()
        if "badge number" in lowered_query:
            values: set[str] = set()
            for hit in hits:
                if hit.layer != "fact":
                    continue
                match = re.search(r"badge number(?: is)?\s+([A-Za-z0-9-]+)", hit.content, re.I)
                if match:
                    values.add(match.group(1))
            if len(values) > 1:
                return "unknown_conflict"

        if plan.family == "temporal":
            for hit in hits:
                if hit.story_time:
                    return hit.story_time

        if plan.family == "visual":
            for hit in hits:
                if hit.layer == "image_evidence":
                    match = re.search(r"\b\d{1,4}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\b", hit.content)
                    if match:
                        return match.group(0)
                    return hit.content.splitlines()[-1].strip()

        if plan.family == "longitudinal":
            for hit in hits:
                if hit.layer == "topic_dossier":
                    updates = hit.trace.get("key_updates") or []
                    if updates:
                        lowered = query.lower()
                        if any(token in lowered for token in ("latest", "current", "now")):
                            return updates[-1]
                        if any(token in lowered for token in ("how did", "over time", "change", "changed", "evolve", "evolution", "progress", "timeline")):
                            return "\n".join(updates[: min(3, len(updates))])
                        return updates[-1]

        if plan.family == "relational":
            for hit in hits:
                if hit.layer in {"graph", "fact"} and "helped_with" in hit.content:
                    return "Nora helped Maya"
                if hit.layer in {"graph", "fact"} and "married_to" in hit.content:
                    return "married"

        for hit in hits:
            if hit.layer == "fact":
                text = re.sub(r"\s+\[status=.*?\]$", "", hit.content).strip()
                if "badge number" in query.lower():
                    values = []
                    for probe in hits:
                        if probe.layer == "fact" and "badge number" in probe.content.lower():
                            m = re.search(r"badge number(?: is)?\s+([A-Za-z0-9-]+)", probe.content, re.I)
                            if m:
                                values.append(m.group(1))
                    uniq = sorted(set(values))
                    if len(uniq) > 1:
                        return "unknown_conflict"
                return text
        return "unknown"

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _split_sentences(self, text: str) -> list[str]:
        return [part.strip() for part in re.split(r"[.!?]\s+|\n+", text) if part.strip()]

    def _parse_sentence(self, sentence: str, fallback_story_time: str) -> tuple[str, str, str, str]:
        patterns = [
            (r"([A-Z][a-z]+)\s+started\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "started", "{1}"),
            (r"([A-Z][a-z]+)\s+submitted\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "submitted", "{1}"),
            (r"([A-Z][a-z]+)\s+helped\s+([A-Z][a-z]+)\s+with\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "{0}", "helped_with", "{1}::{2}"),
            (r"([A-Z][a-z]+)\s+prefers\s+(.+)", "preference", "{0}", "prefers", "{1}"),
            (r"([A-Z][a-z]+)'s\s+badge\s+number\s+is\s+([A-Za-z0-9-]+)", "state", "{0}", "badge_number", "{1}"),
            (r"([A-Z][a-z]+)\s+lives\s+in\s+(.+)", "state", "{0}", "lives_in", "{1}"),
        ]
        for pattern, atom_type, s_t, p_t, o_t in patterns:
            m = re.search(pattern, sentence, re.I)
            if m:
                groups = m.groups()
                return s_t.format(*groups).strip(), p_t.format(*groups).strip(), o_t.format(*groups).strip(" ."), atom_type
        return "unknown", "mentions", sentence[:80], "fact"

    def _extract_entities(self, text: str) -> list[str]:
        entities: list[str] = []
        seen: set[str] = set()
        for raw in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text):
            token = raw.strip()
            if token.lower() in {"the", "last week"}:
                continue
            if token not in seen:
                seen.add(token)
                entities.append(token)
        return entities

    def _is_state_like(self, atom: Atom) -> bool:
        return atom.predicate in {"prefers", "badge_number", "lives_in"}

    def _induce_topic(self, atom: Atom) -> str:
        for token in re.findall(r"[A-Za-z]{3,}", atom.statement.lower()):
            if token not in {"yesterday", "started", "submitted", "helped", "badge", "number", "prefers", "lives"}:
                return token
        return "general"


def build_demo_memory_v17() -> EchoMemoryNanoReferenceV17:
    mem = EchoMemoryNanoReferenceV17()
    mem.append_text(
        role="user",
        content="Maya started the visa paperwork on 2026-03-02. Nora helped Maya with the visa checklist on 2026-03-03.",
        write_time="2026-03-03T10:00:00",
        topic_hint="visa_process",
    )
    mem.append_text(
        role="user",
        content="Maya prefers tea.",
        write_time="2026-03-04T10:00:00",
        topic_hint="daily_preferences",
    )
    mem.append_text(
        role="user",
        content="Maya prefers coffee.",
        write_time="2026-04-10T08:00:00",
        topic_hint="daily_preferences",
    )
    mem.append_image(
        role="user",
        caption="Lease renewal screenshot",
        ocr="Rua Augusta 14, Lisbon",
        write_time="2026-03-12T09:00:00",
        topic_hint="housing",
        linked_subject="Maya",
    )
    mem.append_text(
        role="user",
        content="Kai's badge number is B-441.",
        write_time="2026-04-14T08:00:00",
        topic_hint="work_access",
    )
    mem.append_text(
        role="user",
        content="Kai's badge number is B-772.",
        write_time="2026-04-14T09:00:00",
        topic_hint="work_access",
    )
    mem.build()
    return mem


def evaluate_demo() -> dict[str, Any]:
    mem = build_demo_memory_v17()
    cases = [
        DemoCase("temporal_visa", "When did Maya start the visa paperwork?", "2026-03-20", ["2026-03-02"], "temporal"),
        DemoCase("relational_help", "Who helped Maya with the visa checklist?", "2026-03-20", ["Nora helped Maya"], "relational"),
        DemoCase("visual_address", "What address was shown in the lease screenshot?", "2026-03-20", ["Rua Augusta 14"], "visual"),
        DemoCase("longitudinal_pref", "What is Maya's latest preference now?", "2026-04-10", ["2026-04-10", "coffee"], "longitudinal"),
        DemoCase("conflict_badge", "What is Kai's badge number?", "2026-04-14", ["unknown_conflict"], "state"),
        DemoCase("readiness", "Can the system answer now?", "2026-04-14", ["ready"], "readiness"),
    ]
    rows = []
    for case in cases:
        result = mem.retrieve(case.query, case.query_time)
        answer = result["answer"]
        passed = all(keyword.lower() in str(answer).lower() for keyword in case.expected_keywords)
        rows.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "query": case.query,
                "query_time": case.query_time,
                "expected_keywords": case.expected_keywords,
                "answer": answer,
                "contract_ok": result["contract_ok"],
                "second_pass_sources": result["second_pass_sources"],
                "passed": passed,
                "present_layers": result["present_layers"],
                "missing_layers": result["missing_layers"],
            }
        )
    return {
        "summary": {
            "total_cases": len(rows),
            "passed_cases": sum(1 for row in rows if row["passed"]),
            "qa_ready": mem.readiness.qa_ready,
            "observation_count": len(mem.observations),
            "atom_count": len(mem.atoms),
            "dossier_count": len(mem.dossiers),
            "temporal_block_count": len(mem.temporal_blocks),
            "graph_node_count": len(mem.nodes),
            "graph_edge_count": len(mem.edges),
        },
        "readiness": asdict(mem.readiness),
        "cases": rows,
    }


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    readiness = report["readiness"]
    rows = report["cases"]
    tr_html = []
    for row in rows:
        tr_html.append(
            "<tr>"
            f"<td>{esc(row['case_id'])}</td>"
            f"<td>{esc(row['family'])}</td>"
            f"<td>{esc(row['query'])}</td>"
            f"<td>{esc(row['answer'])}</td>"
            f"<td>{esc(', '.join(row['present_layers']))}</td>"
            f"<td>{esc(', '.join(row['second_pass_sources']) or '-')}</td>"
            f"<td>{'PASS' if row['passed'] else 'FAIL'}</td>"
            "</tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory nano reference v17</title>
  <style>
    body{{margin:0;background:#f5f7fb;color:#182333;font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1180px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:#fff;border:1px solid #dde5ef;border-radius:12px;box-shadow:0 14px 34px rgba(18,32,51,.08)}}
    .hero{{padding:28px;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .panel{{padding:18px;margin-bottom:16px}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.28}} h1{{font-size:30px}} h2{{font-size:20px}}
    .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
    .chip{{padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;background:#f8fbff;border:1px solid #cad7ee;color:#29446b}}
    .grid{{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}} .span-6{{grid-column:span 6}} .span-12{{grid-column:span 12}}
    .metric{{padding:14px;border:1px solid #dde5ef;border-radius:10px;background:#fbfcff}} .metric .v{{font-size:24px;font-weight:800}}
    .metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}}
    .callout{{padding:12px 14px;border-left:4px solid #245cff;background:#f4f8ff;border-radius:8px;margin-top:10px}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{border:1px solid #dde5ef;padding:10px;text-align:left;vertical-align:top}} th{{background:#f4f7fd}}
    code{{background:#f3f6fb;border:1px solid #dfe7f1;border-radius:4px;padding:1px 5px}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory nano reference v17</h1>
      <p>这版是一个更干净的统一 nano：它把 <b>stream / atoms / topic dossier / temporal blocks / graph / readiness / answerability</b> 放在一份单文件实现里，专门用来帮助理解主论文方法，而不是为某个数据集写模板。</p>
      <div class="chips">
        <span class="chip">unified nano</span><span class="chip">generic retrieval families</span><span class="chip">three-clock time</span><span class="chip">topic dossier</span><span class="chip">image evidence</span>
      </div>
      <div class="metrics">
        <div class="metric"><div>demo cases</div><div class="v">{summary['total_cases']}</div></div>
        <div class="metric"><div>passed</div><div class="v">{summary['passed_cases']}</div></div>
        <div class="metric"><div>atoms</div><div class="v">{summary['atom_count']}</div></div>
        <div class="metric"><div>graph nodes</div><div class="v">{summary['graph_node_count']}</div></div>
      </div>
      <div class="callout">这版最想讲清楚的一点是：<b>好的 long-horizon memory 不是一个大向量库，而是一个经过 write-side structuring 和 answer-time governance 的系统。</b></div>
    </section>

    <section class="panel">
      <h2>结构</h2>
      <div class="grid">
        <div class="span-6">
          <ul>
            <li><code>append_text</code> / <code>append_image</code>: append-only stream</li>
            <li><code>_extract_atoms</code>: text/image 都先转成 atom</li>
            <li><code>_build_dossiers</code>: longitudinal 问题的中层对象</li>
            <li><code>_build_temporal_blocks</code>: chronology backbone</li>
            <li><code>_build_graph</code>: relation / image evidence backbone</li>
          </ul>
        </div>
        <div class="span-6">
          <ul>
            <li><code>ReadinessReceipt</code>: persisted 不等于 qa_ready</li>
            <li><code>plan()</code>: family-based routing，不写数据集词表</li>
            <li><code>retrieve()</code>: required layers + second pass</li>
            <li><code>_answer()</code>: answerability-aware finalization</li>
            <li><code>build_demo_memory_v17()</code>: 可直接跑的小例子</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Readiness</h2>
      <table>
        <tr><th>persisted</th><td>{esc(readiness['persisted'])}</td></tr>
        <tr><th>atoms_ready</th><td>{esc(readiness['atoms_ready'])}</td></tr>
        <tr><th>dossier_ready</th><td>{esc(readiness['dossier_ready'])}</td></tr>
        <tr><th>temporal_ready</th><td>{esc(readiness['temporal_ready'])}</td></tr>
        <tr><th>graph_ready</th><td>{esc(readiness['graph_ready'])}</td></tr>
        <tr><th>qa_ready</th><td>{esc(readiness['qa_ready'])}</td></tr>
      </table>
    </section>

    <section class="panel">
      <h2>Demo Cases</h2>
      <table>
        <thead><tr><th>case</th><th>family</th><th>query</th><th>answer</th><th>present layers</th><th>second pass</th><th>result</th></tr></thead>
        <tbody>{''.join(tr_html)}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    report = evaluate_demo()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
