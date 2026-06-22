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
OUT_JSON = ROOT / "nano_reference_impl_v16_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_reference_impl_v16_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def shift_day(ymd: str, delta: int) -> str:
    dt = datetime.fromisoformat(ymd)
    return (dt + timedelta(days=delta)).strftime("%Y-%m-%d")


def normalize_date(text: str, anchor_ymd: str) -> str:
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


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]{2,}|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", text.lower()))


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta:
        return 0.0
    return len(ta & tb) / max(len(ta), 1)


@dataclass
class Observation:
    obs_id: str
    role: str
    modality: str
    content: str
    mention_time: str
    write_time: str
    event_time: str
    topic_hint: str = ""
    linked_subject: str = ""
    caption: str = ""
    ocr: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    topic: str
    subject: str
    predicate: str
    obj: str
    statement: str
    event_time: str
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
    timeline: list[str]
    atom_ids: list[str]
    entities: list[str]


@dataclass
class TreeBlock:
    block_id: str
    level: str
    key: str
    content: str
    atom_ids: list[str]


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
    required_layers: list[str]
    reason: str


@dataclass
class Readiness:
    persisted: bool = False
    atoms_ready: bool = False
    dossier_ready: bool = False
    tree_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False


@dataclass
class DemoCase:
    case_id: str
    query: str
    query_time: str
    expected_keywords: list[str]
    family: str


class EchoMemoryNanoReferenceV15:
    """
    A single-file reference implementation for the current paper story.

    It is intentionally generic and keeps only the minimum moving pieces:
    1. append-only observations
    2. three-clock time
    3. atom extraction
    4. topic dossier middle layer
    5. temporal tree
    6. relation graph with image evidence nodes
    7. readiness gate
    8. contract-aware second pass
    9. governed state lifecycle (active / superseded / conflicted / historical)
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.dossiers: dict[str, TopicDossier] = {}
        self.temporal_tree: dict[str, TreeBlock] = {}
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.readiness = Readiness()

    # ------------------------------------------------------------------
    # Ingest
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
                event_time=normalize_date(content, write_time[:10]),
                topic_hint=topic_hint.strip(),
            )
        )
        self._invalidate_downstream()

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
        merged = "\n".join(x for x in [caption.strip(), ocr.strip()] if x)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role=role,
                modality="image",
                content=merged,
                mention_time=write_time,
                write_time=write_time,
                event_time=normalize_date(merged, write_time[:10]),
                topic_hint=topic_hint.strip(),
                linked_subject=linked_subject.strip(),
                caption=caption.strip(),
                ocr=ocr.strip(),
            )
        )
        self._invalidate_downstream()

    def _invalidate_downstream(self) -> None:
        self.readiness.persisted = True
        self.readiness.atoms_ready = False
        self.readiness.dossier_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> None:
        self.atoms = self._extract_atoms()
        self._apply_state_lifecycle()
        self.readiness.atoms_ready = bool(self.atoms)
        self.dossiers = self._build_dossiers()
        self.readiness.dossier_ready = bool(self.dossiers)
        self.temporal_tree = self._build_temporal_tree()
        self.readiness.tree_ready = bool(self.temporal_tree)
        self.nodes, self.edges = self._build_graph()
        self.readiness.graph_ready = bool(self.nodes)
        self.readiness.qa_ready = (
            self.readiness.persisted
            and self.readiness.atoms_ready
            and self.readiness.dossier_ready
            and self.readiness.tree_ready
            and self.readiness.graph_ready
        )

    def _extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for obs in self.observations:
            topic = (obs.topic_hint or "").strip()
            if obs.modality == "image":
                entities = self._extract_entities(f"{obs.linked_subject} {obs.content}".strip())
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type="image_evidence",
                        topic=topic,
                        subject=obs.linked_subject or "unknown",
                        predicate="shows",
                        obj=(obs.ocr or obs.caption or obs.content)[:80],
                        statement=obs.content,
                        event_time=obs.event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                        valid_from=obs.event_time,
                        entities=entities,
                    )
                )
                continue

            for sent in self._split_sentences(obs.content):
                subject, predicate, obj = self._parse_triplet(sent)
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=self._classify_atom(sent),
                        topic=topic,
                        subject=subject,
                        predicate=predicate,
                        obj=obj,
                        statement=sent,
                        event_time=normalize_date(sent, obs.write_time[:10]),
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                        valid_from=normalize_date(sent, obs.write_time[:10]),
                        entities=self._extract_entities(sent),
                    )
                )
        return atoms

    def _apply_state_lifecycle(self) -> None:
        keyed: dict[tuple[str, str], list[Atom]] = {}
        for atom in self.atoms:
            keyed.setdefault((atom.subject, atom.predicate), []).append(atom)

        for group in keyed.values():
            ordered = sorted(
                group,
                key=lambda a: (
                    a.event_time or a.valid_from or a.write_time,
                    a.write_time,
                    a.atom_id,
                ),
            )
            for atom in ordered:
                if not atom.valid_from:
                    atom.valid_from = atom.event_time or atom.write_time
                atom.valid_until = atom.valid_until or ""
                atom.status = atom.status or "active"

            seen_same_time: dict[str, list[Atom]] = {}
            for atom in ordered:
                if not self._is_state_like(atom):
                    continue
                seen_same_time.setdefault(atom.event_time or atom.valid_from or atom.write_time, []).append(atom)

            for same_time_atoms in seen_same_time.values():
                objs = {a.obj for a in same_time_atoms}
                if len(objs) <= 1:
                    continue
                for atom in same_time_atoms:
                    atom.status = "conflicted"
                    atom.conflict_with = [
                        other.atom_id for other in same_time_atoms if other.atom_id != atom.atom_id
                    ]

            active_chain = [
                atom for atom in ordered
                if self._is_state_like(atom) and atom.status != "conflicted"
            ]
            for prev, curr in zip(active_chain, active_chain[1:]):
                prev.status = "superseded"
                prev.valid_until = curr.valid_from or curr.event_time or curr.write_time
                prev.superseded_by = curr.atom_id

    def _is_state_like(self, atom: Atom) -> bool:
        if atom.predicate in {"lives_in", "prefers", "works_at", "budget", "badge_number"}:
            return True
        lowered = atom.statement.lower()
        if atom.atom_type in {"image_evidence", "relation"}:
            return False
        return bool(
            re.search(
                r"\b(lives|moved|date moved|move-in|budget|prefers|approved|status|launch date|shifted|confirmed)\b",
                lowered,
            )
        )

    def _build_dossiers(self) -> dict[str, TopicDossier]:
        grouped: dict[str, list[Atom]] = {}
        unassigned: list[Atom] = []
        for atom in self.atoms:
            topic = str(atom.topic or "").strip()
            if topic:
                grouped.setdefault(topic, []).append(atom)
            else:
                unassigned.append(atom)
        if unassigned:
            for topic, cluster_atoms in self._induce_topic_groups(unassigned).items():
                grouped.setdefault(topic, []).extend(cluster_atoms)
        dossiers: dict[str, TopicDossier] = {}
        for topic, atoms in grouped.items():
            atoms = sorted(atoms, key=lambda a: a.event_time)
            visible_atoms = [a for a in atoms if a.status != "superseded"] or atoms
            timeline = [
                f"{a.event_time}: {a.statement} [{a.status}]"
                for a in visible_atoms[:6]
            ]
            entity_list: list[str] = []
            seen: set[str] = set()
            for atom in atoms:
                for ent in atom.entities:
                    if ent not in seen:
                        seen.add(ent)
                        entity_list.append(ent)
            summary = "\n".join(
                [
                    f"Topic: {topic}",
                    f"Span: {atoms[0].event_time} -> {atoms[-1].event_time}",
                    "Key updates:",
                    *[f"- {a.statement} [{a.status}]" for a in visible_atoms[:5]],
                ]
            )
            dossiers[topic] = TopicDossier(
                topic=topic,
                summary=summary,
                start_time=atoms[0].event_time,
                end_time=atoms[-1].event_time,
                timeline=timeline,
                atom_ids=[a.atom_id for a in atoms],
                entities=entity_list[:8],
            )
        return dossiers

    def _induce_topic_groups(self, atoms: list[Atom]) -> dict[str, list[Atom]]:
        clusters: list[list[Atom]] = []
        for atom in atoms:
            attached = False
            for cluster in clusters:
                if any(self._topic_edge(atom, other) for other in cluster):
                    cluster.append(atom)
                    attached = True
                    break
            if not attached:
                clusters.append([atom])

        merged = True
        while merged:
            merged = False
            next_clusters: list[list[Atom]] = []
            while clusters:
                head = clusters.pop(0)
                i = 0
                while i < len(clusters):
                    other = clusters[i]
                    if any(self._topic_edge(a, b) for a in head for b in other):
                        head.extend(other)
                        clusters.pop(i)
                        merged = True
                    else:
                        i += 1
                next_clusters.append(head)
            clusters = next_clusters

        out: dict[str, list[Atom]] = {}
        for idx, cluster in enumerate(clusters):
            topic = self._cluster_topic_label(cluster, idx)
            out[topic] = sorted(cluster, key=lambda a: a.event_time)
        return out

    def _topic_edge(self, left: Atom, right: Atom) -> bool:
        left_sig = self._topic_signature(left)
        right_sig = self._topic_signature(right)
        shared = left_sig & right_sig
        if shared:
            return True
        left_entities = {e.lower() for e in left.entities}
        right_entities = {e.lower() for e in right.entities}
        shared_entities = left_entities & right_entities
        if shared_entities and (left_sig & right_sig):
            return True
        return False

    def _cluster_topic_label(self, atoms: list[Atom], idx: int) -> str:
        freq: dict[str, int] = {}
        for atom in atoms:
            for tok in self._topic_signature(atom):
                freq[tok] = freq.get(tok, 0) + 1
        ranked = sorted(freq.items(), key=lambda item: (-item[1], item[0]))
        top = [token for token, _count in ranked[:2]]
        if top:
            return "_".join(top)
        entities = [e.lower() for atom in atoms for e in atom.entities]
        if entities:
            return f"{entities[0]}_topic"
        return f"topic_{idx:02d}"

    def _topic_signature(self, atom: Atom) -> set[str]:
        blocked_names = {
            ent.lower()
            for ent in atom.entities
            if self._looks_like_person_entity(ent)
        }
        for raw in (atom.subject, atom.obj):
            value = str(raw or "").strip()
            if self._looks_like_person_entity(value):
                blocked_names.add(value.lower())

        parts = [str(atom.statement or "").strip()]
        obj = str(atom.obj or "").strip()
        if obj and not self._looks_like_person_entity(obj):
            parts.append(obj)
        text = " ".join(part for part in parts if part)
        return set(self._topic_tokens(text, blocked=blocked_names))

    def _topic_tokens(self, text: str, blocked: set[str] | None = None) -> list[str]:
        stop = {
            "the", "and", "for", "with", "after", "before", "from", "into", "onto",
            "then", "that", "this", "those", "these", "was", "were", "is", "are",
            "had", "has", "have", "will", "would", "could", "should", "a", "an",
            "on", "in", "of", "to", "at", "by", "it", "its", "their", "his", "her",
            "they", "he", "she", "you", "we", "i", "my", "our", "your", "now",
            "found", "showed", "started", "approved", "confirmed", "delayed",
            "shifted", "received", "prepare", "prepared", "moved", "move", "date",
            "plan", "process", "screenshot", "contract", "paperwork", "financial",
            "statement", "document", "situation", "thing", "stuff", "show", "shown",
        }
        blocked = {token.lower() for token in (blocked or set())}
        out: list[str] = []
        seen: set[str] = set()
        for raw in re.findall(r"[A-Za-z][A-Za-z-]{2,}", text.lower()):
            token = raw.strip("-")
            if token.endswith("ing") and len(token) > 5:
                token = token[:-3]
            elif token.endswith("ed") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("es") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            if token in stop or token in blocked or token.isdigit() or len(token) < 3:
                continue
            if token not in seen:
                seen.add(token)
                out.append(token)
        return out

    def _build_temporal_tree(self) -> dict[str, TreeBlock]:
        buckets: dict[tuple[str, str], list[Atom]] = {}
        for atom in self.atoms:
            day = atom.event_time[:10]
            month = atom.event_time[:7]
            year = atom.event_time[:4]
            for level, key in (("day", day), ("month", month), ("year", year)):
                buckets.setdefault((level, key), []).append(atom)
        blocks: dict[str, TreeBlock] = {}
        for (level, key), atoms in buckets.items():
            block_id = f"{level}:{key}"
            lines = [
                f"- {a.event_time}: {a.statement} [{a.status}]"
                for a in sorted(atoms, key=lambda a: a.event_time)[:10]
                if a.status != "superseded"
            ]
            if not lines:
                lines = [
                    f"- {a.event_time}: {a.statement} [{a.status}]"
                    for a in sorted(atoms, key=lambda a: a.event_time)[:10]
                ]
            blocks[block_id] = TreeBlock(
                block_id=block_id,
                level=level,
                key=key,
                content="\n".join(lines),
                atom_ids=[a.atom_id for a in atoms],
            )
        return blocks

    def _build_graph(self) -> tuple[dict[str, Node], list[Edge]]:
        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        for atom in self.atoms:
            node_type = "image_evidence" if atom.atom_type == "image_evidence" else "event"
            event_id = f"{node_type}:{atom.atom_id}"
            nodes[event_id] = Node(
                node_id=event_id,
                node_type=node_type,
                content=atom.statement,
                event_time=atom.event_time,
                source_ref=atom.source_obs_id,
            )
            fact_id = f"fact:{atom.atom_id}"
            nodes[fact_id] = Node(
                node_id=fact_id,
                node_type="fact",
                content=atom.statement,
                event_time=atom.event_time,
                source_ref=atom.source_obs_id,
            )
            nodes[fact_id].content = f"{atom.statement} [status={atom.status}]"
            edges.append(Edge(f"{event_id}:evidence:{fact_id}", event_id, fact_id, "evidence_of"))

            for ent in atom.entities:
                entity_id = f"entity:{ent}"
                if entity_id not in nodes:
                    nodes[entity_id] = Node(entity_id, "entity", f"name={ent}")
                edges.append(Edge(f"{event_id}:involves:{entity_id}", event_id, entity_id, "involves"))
                edges.append(Edge(f"{entity_id}:supports:{fact_id}", entity_id, fact_id, "supports"))
        return nodes, edges

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"\b(can you answer|ready|qa ready|answer now|system answer|at this point|available now)\b", q):
            return QueryPlan("readiness", "readiness", [], ["readiness"], "Lifecycle / answerability query.")
        if re.search(r"\b(status|latest|progress|evolve|evolution|timeline|how did|changed|change|updates|update|over time)\b", q):
            return QueryPlan("longitudinal", "topic_dossier", ["atom", "temporal_tree"], ["topic_dossier", "fact"], "Cross-session topic evolution query.")
        if re.search(r"\b(image|photo|screenshot|shown|visible|look like|ocr|appears|appear|address|contract image)\b", q):
            return QueryPlan("visual", "graph", ["atom"], ["image_evidence", "fact"], "Visual evidence query.")
        if re.search(r"\b(who|whom|which company|which person|introduced|helped|connected|relationship|assist|assisted|invited|married|worked with|contacted|through who|through whom)\b", q):
            return QueryPlan("relational", "graph", ["atom", "temporal_tree"], ["graph", "fact", "path_grounding"], "Relation-heavy query.")
        if re.search(r"\b(where does|where did|what does|what did|what is|which is|current|as of|preference|budget|badge number|lives in|works at|move-in date|launch date)\b", q):
            return QueryPlan("state", "atom", ["temporal_tree", "topic_dossier"], ["fact"], "State / as-of query.")
        if re.search(r"\b(when|yesterday|last week|before|after|date|time|begin|began|start|started)\b", q):
            return QueryPlan("temporal", "temporal_tree", ["graph", "atom"], ["temporal_tree", "event", "event_time"], "Chronology-heavy query.")
        return QueryPlan("general", "atom", ["topic_dossier"], ["fact"], "General factual query.")

    def retrieve(self, query: str, query_time: str) -> dict[str, Any]:
        plan = self.plan(query)
        primary = self._reader(plan.primary_reader, query, query_time, plan)
        present = self._present_layers(primary)
        missing = [layer for layer in plan.required_layers if layer not in present]
        second_pass_sources: list[str] = []
        hits = list(primary)
        for missing_layer in list(missing):
            reader = self._reader_for_missing(missing_layer)
            if not reader or reader == plan.primary_reader or reader in second_pass_sources:
                continue
            extra = self._reader(reader, query, query_time, plan)
            if extra:
                second_pass_sources.append(reader)
                hits.extend(extra)
                present = self._present_layers(hits)
                missing = [layer for layer in plan.required_layers if layer not in present]
            if not missing:
                break
        hits = self._dedup_sort(hits)
        answer = self._answer(query, query_time, plan, hits, missing)
        return {
            "plan": asdict(plan),
            "hits": [asdict(h) for h in hits],
            "present_layers": sorted(present),
            "missing_layers": missing,
            "contract_ok": not missing,
            "second_pass_sources": second_pass_sources,
            "answer": answer,
        }

    def _reader(self, reader: str, query: str, query_time: str, plan: QueryPlan) -> list[Hit]:
        if reader == "readiness":
            return [
                Hit(
                    source="readiness",
                    layer="readiness",
                    score=1.0 if self.readiness.qa_ready else 0.0,
                    content=f"qa_ready={self.readiness.qa_ready}",
                    trace=asdict(self.readiness),
                )
            ]
        if reader == "topic_dossier":
            rows: list[Hit] = []
            for topic, dossier in self.dossiers.items():
                score = self._topic_dossier_score(query, dossier, plan)
                rows.append(
                    Hit(
                        source=f"dossier:{topic}",
                        layer="topic_dossier",
                        score=score,
                        content=dossier.summary,
                        event_time=dossier.end_time,
                        trace={"timeline": dossier.timeline, "atom_ids": dossier.atom_ids},
                    )
                )
            return rows[:4]
        if reader == "temporal_tree":
            rows: list[Hit] = []
            for block_id, block in self.temporal_tree.items():
                score = overlap(query, block.content)
                if block.level == "day":
                    score += 0.2
                if score > 0:
                    rows.append(Hit(block_id, "temporal_tree", score, block.content, trace={"atom_ids": block.atom_ids}))
            return rows[:6]
        if reader == "graph":
            rows: list[Hit] = []
            for node in self.nodes.values():
                score = overlap(query, node.content)
                if plan.family == "relational" and node.node_type in {"entity", "event"}:
                    score += 0.2
                if plan.family == "visual" and node.node_type == "image_evidence":
                    score += 0.3
                if score > 0:
                    trace: dict[str, Any] = {}
                    if plan.family == "relational" and node.node_type == "event":
                        trace["path_edge_ids"] = [e.edge_id for e in self.edges if e.source_id == node.node_id][:2]
                    rows.append(Hit(node.node_id, node.node_type if node.node_type == "image_evidence" else "graph", score, node.content, node.event_time, trace))
            return rows[:8]
        if reader == "atom":
            rows: list[Hit] = []
            for atom in self.atoms:
                if not self._atom_visible_as_of(atom, query_time, plan):
                    continue
                score = overlap(query, atom.statement)
                if score > 0:
                    trace = {
                        "event_time": atom.event_time,
                        "subject": atom.subject,
                        "predicate": atom.predicate,
                        "status": atom.status,
                        "object": atom.obj,
                        "valid_from": atom.valid_from,
                        "valid_until": atom.valid_until,
                        "superseded_by": atom.superseded_by,
                        "conflict_with": list(atom.conflict_with),
                    }
                    rows.append(
                        Hit(
                            f"atom:{atom.atom_id}",
                            "fact",
                            score + self._status_boost(atom, plan, query_time),
                            f"{atom.statement} [status={atom.status}]",
                            atom.event_time,
                            trace,
                        )
                    )
            return rows[:8]
        return []

    def _atom_visible_as_of(self, atom: Atom, query_time: str, plan: QueryPlan) -> bool:
        if atom.status == "conflicted":
            return True
        if plan.family == "longitudinal":
            return True
        start = atom.valid_from or atom.event_time or atom.write_time
        end = atom.valid_until or "9999-12-31"
        q = query_time[:10]
        if start[:10] <= q < end[:10]:
            return True
        if atom.status not in {"superseded", "historical"}:
            return False
        return self._is_as_of_query(plan, q)

    def _is_as_of_query(self, plan: QueryPlan, query_time: str) -> bool:
        return plan.family in {"temporal", "longitudinal"}

    def _status_boost(self, atom: Atom, plan: QueryPlan, query_time: str) -> float:
        if atom.status == "active":
            return 0.12
        if atom.status == "historical":
            return 0.02
        if atom.status == "superseded":
            return -0.05
        if atom.status == "conflicted":
            return -0.15
        return 0.0

    def _reader_for_missing(self, layer: str) -> str:
        return {
            "event": "graph",
            "event_time": "atom",
            "fact": "atom",
            "path_grounding": "graph",
            "image_evidence": "graph",
            "topic_dossier": "topic_dossier",
            "temporal_tree": "temporal_tree",
            "readiness": "readiness",
        }.get(layer, "")

    def _present_layers(self, hits: list[Hit]) -> set[str]:
        present: set[str] = set()
        for hit in hits:
            if hit.layer == "topic_dossier":
                present.add("topic_dossier")
            if hit.layer == "temporal_tree":
                present.add("temporal_tree")
            if hit.layer == "readiness":
                present.add("readiness")
            if hit.layer in {"graph", "entity"}:
                present.add("graph")
            if hit.layer == "image_evidence":
                present.add("image_evidence")
                present.add("graph")
            if hit.layer == "fact":
                present.add("fact")
                if hit.trace.get("event_time"):
                    present.add("event_time")
                if hit.trace.get("subject") or hit.trace.get("predicate"):
                    present.add("event")
            if hit.trace.get("path_edge_ids"):
                present.add("path_grounding")
        return present

    def _dedup_sort(self, hits: list[Hit]) -> list[Hit]:
        seen: set[str] = set()
        uniq: list[Hit] = []
        for hit in sorted(hits, key=lambda h: h.score, reverse=True):
            key = f"{hit.source}:{hit.layer}"
            if key in seen:
                continue
            seen.add(key)
            uniq.append(hit)
        return uniq[:10]

    def _answer(self, query: str, query_time: str, plan: QueryPlan, hits: list[Hit], missing: list[str]) -> str:
        if plan.family == "readiness":
            return "ready" if self.readiness.qa_ready else "not ready"
        if missing:
            return "unknown"
        if plan.family == "state":
            return self._answer_state_query(query, hits, query_time)
        if any(
            hit.trace.get("status") == "conflicted" and overlap(query, hit.content) > 0
            for hit in hits
        ):
            return "unknown_conflict"
        top = hits[0] if hits else None
        if top is None:
            return "unknown"
        candidate = ""
        if plan.family == "temporal":
            for hit in hits:
                if hit.trace.get("event_time"):
                    candidate = str(hit.trace["event_time"])
                    break
                if hit.event_time:
                    candidate = hit.event_time
                    break
            if not candidate:
                return "unknown"
        elif plan.family == "relational":
            for hit in hits:
                ents = self._extract_entities(hit.content)
                if len(ents) >= 2:
                    candidate = ", ".join(ents[:2])
                    break
            if not candidate:
                candidate = top.content
        elif plan.family == "longitudinal":
            timeline = top.trace.get("timeline", [])
            if timeline:
                if len(timeline) <= 4:
                    chosen = timeline
                else:
                    chosen = [timeline[0], *timeline[-3:]]
                candidate = "\n".join(chosen)
            else:
                candidate = top.content
        elif plan.family == "visual":
            image_hits = [hit for hit in hits if hit.layer == "image_evidence"]
            target = image_hits[0].content if image_hits else top.content
            candidate = self._best_visual_line(target)
        else:
            candidate = top.content
        if not self._answerability_ok(query, plan, hits, candidate):
            return "unknown"
        return candidate

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _split_sentences(self, text: str) -> list[str]:
        return [s.strip() for s in re.split(r"[.!?。！？]", text) if s.strip()]

    def _topic_dossier_score(self, query: str, dossier: TopicDossier, plan: QueryPlan) -> float:
        score = overlap(query, dossier.summary) + 0.3
        if plan.family == "longitudinal":
            timeline_len = len(dossier.timeline)
            score += min(timeline_len, 4) * 0.08
            if timeline_len >= 2:
                score += 0.18
            if dossier.start_time and dossier.end_time and dossier.start_time != dossier.end_time:
                score += 0.12
        return score

    def _infer_topic(self, text: str) -> str:
        parts = self._topic_tokens(text)
        if parts:
            return "_".join(parts[:2])
        ents = self._extract_entities(text)
        if ents:
            return ents[0].lower()
        return "general_topic"

    def _parse_triplet(self, sent: str) -> tuple[str, str, str]:
        patterns = [
            (r"([A-Z][a-z]+)\s+lives\s+in\s+(.+)", "lives_in"),
            (r"([A-Z][a-z]+)\s+moved\s+to\s+(.+)", "lives_in"),
            (r"([A-Z][a-z]+)\s+prefers\s+(.+)", "prefers"),
            (r"([A-Z][a-z]+)\s+preferred\s+(.+)", "prefers"),
            (r"([A-Z][a-z]+\s[A-Z][a-z]+|[A-Z][a-z]+)\s+works\s+at\s+(.+)", "works_at"),
            (r"([A-Z][a-z]+\s[A-Z][a-z]+|[A-Z][a-z]+)\s+badge\s+number\s+(?:is\s+)?([A-Za-z0-9-]+)", "badge_number"),
            (r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\s+budget\s+is\s+([A-Za-z0-9-]+)", "budget"),
            (r"(Project\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\s+budget\s+is\s+([A-Za-z0-9-]+)", "budget"),
            (r"([A-Z][a-z]+)\s+found\s+an\s+(.+)", "found"),
            (r"([A-Z][a-z]+)\s+helped\s+([A-Z][a-z]+)\s+(.+)", "helped"),
            (r"([A-Z][a-z]+)\s+started\s+the\s+(.+)", "started"),
            (r"([A-Z][a-z]+)\s+approved\s+the\s+(.+)", "approved"),
            (r"([A-Z][a-z]+)\s+confirmed\s+the\s+(.+)", "confirmed"),
            (r"([A-Z][a-z]+)\s+showed\s+the\s+(.+)", "showed"),
            (r"([A-Z][a-z]+)\s+moved\s+in\s+to\s+(.+)", "moved_to"),
        ]
        for pattern, predicate in patterns:
            m = re.search(pattern, sent)
            if not m:
                continue
            groups = m.groups()
            if predicate == "helped":
                return groups[0], predicate, groups[1]
            return groups[0], predicate, groups[-1].strip()
        ents = self._extract_entities(sent)
        return (ents[0] if ents else "unknown", "mentions", sent[:40])

    def _classify_atom(self, sent: str) -> str:
        lowered = sent.lower()
        if any(
            phrase in lowered
            for phrase in ("lives in", "moved to", "prefers", "preferred", "works at", "budget is", "badge number")
        ):
            return "state"
        if any(word in lowered for word in ("helped", "introduced", "connected")):
            return "relation"
        if any(word in lowered for word in ("planned", "decided", "status", "progress")):
            return "plan"
        if re.search(r"\b(20\d{2}-\d{2}-\d{2}|yesterday|last week)\b", lowered):
            return "event"
        return "fact"

    def _extract_entities(self, text: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        blocked = {
            "What", "Who", "When", "Where", "Why", "How", "Which", "Can",
            "Do", "Does", "Did", "Is", "Are", "Was", "Were",
        }
        for token in re.findall(r"\b[A-Z][a-zA-Z]+\b", text):
            if token in blocked:
                continue
            if token not in seen:
                seen.add(token)
                out.append(token)
        return out

    def _looks_like_person_entity(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        if re.fullmatch(r"[A-Z][a-z]+", value):
            return True
        if re.fullmatch(r"[A-Z][a-z]+\s[A-Z][a-z]+", value):
            return True
        return False

    def _answer_state_query(self, query: str, hits: list[Hit], query_time: str) -> str:
        target = self._state_query_target(query)
        candidates = [hit for hit in hits if hit.layer == "fact"]
        if target is not None:
            subject, predicate = target
            candidates = [
                hit for hit in candidates
                if hit.trace.get("subject") == subject and hit.trace.get("predicate") == predicate
            ] or candidates
        if any(hit.trace.get("status") == "conflicted" for hit in candidates):
            return "unknown_conflict"
        active = [
            hit for hit in candidates
            if hit.trace.get("status") == "active"
        ]
        pool = active or candidates
        if not pool:
            return "unknown"
        best = max(
            pool,
            key=lambda hit: (
                str(hit.trace.get("valid_from", "")),
                hit.score,
            ),
        )
        obj = self._state_object_from_content(best.content, best.trace)
        return obj or "unknown"

    def _state_query_target(self, query: str) -> tuple[str, str] | None:
        patterns = [
            (r"where does ([A-Z][a-z]+) live", "lives_in"),
            (r"where did ([A-Z][a-z]+) live", "lives_in"),
            (r"what does ([A-Z][a-z]+) prefer", "prefers"),
            (r"what did ([A-Z][a-z]+) prefer", "prefers"),
            (r"what is ([A-Z][a-z]+(?:\s[A-Z][a-z]+)*) budget", "budget"),
            (r"what is ([A-Z][a-z]+)(?:'s)? badge number", "badge_number"),
            (r"where does ([A-Z][a-z]+(?:\s[A-Z][a-z]+)*) work", "works_at"),
        ]
        for pattern, predicate in patterns:
            m = re.search(pattern, query)
            if m:
                return (m.group(1), predicate)
        return None

    def _state_object_from_content(self, content: str, trace: dict[str, Any]) -> str:
        for pattern in (
            r"\b(?:lives in|moved to)\s+(.+?)\s*(?:on\s+\d{4}-\d{2}-\d{2})?(?:\s*\[status=.*)?$",
            r"\b(?:prefers|preferred)\s+(.+?)\s*(?:on\s+\d{4}-\d{2}-\d{2})?(?:\s*\[status=.*)?$",
            r"\bbudget is\s+([A-Za-z0-9-]+)",
            r"\bbadge number\s+(?:is\s+)?([A-Za-z0-9-]+)",
            r"\bworks at\s+(.+?)\s*(?:on\s+\d{4}-\d{2}-\d{2})?(?:\s*\[status=.*)?$",
        ):
            m = re.search(pattern, content, re.IGNORECASE)
            if m:
                return m.group(1).strip().rstrip(".")
        return str(trace.get("object", "") or "").strip()

    def _answerability_ok(self, query: str, plan: QueryPlan, hits: list[Hit], candidate: str) -> bool:
        joined = "\n".join(hit.content for hit in hits[:6])
        query_entities = self._extract_entities(query)
        if query_entities:
            present = sum(1 for ent in query_entities if ent in joined)
            if present == 0:
                return False

        q = query.lower()
        cue_groups = {
            "invite": ("invite", "invited"),
            "help": ("help", "helped", "assist", "assisted"),
            "introduce": ("introduce", "introduced", "connect", "connected"),
            "marry": ("marry", "married"),
            "join": ("join", "joined"),
            "leave": ("leave", "left"),
            "sign": ("sign", "signed"),
            "approve": ("approve", "approved"),
            "show": ("show", "showed", "shown", "shows"),
            "move": ("move", "moved", "move-in"),
        }
        for cue, variants in cue_groups.items():
            if plan.family == "visual" and cue == "show":
                continue
            if cue in q and not any(variant in joined.lower() for variant in variants):
                return False

        if "which company" in q:
            if not re.search(r"\b(company|inc|corp|labs|group|studio|technologies|tech)\b", candidate, re.IGNORECASE):
                return False
        if plan.family == "state":
            if any(hit.trace.get("status") == "conflicted" for hit in hits):
                return False
            return candidate not in {"", "unknown", "unknown_conflict"}
        if any(hit.trace.get("status") == "conflicted" for hit in hits):
            return False
        if plan.family == "relational" and not any(hit.trace.get("path_edge_ids") for hit in hits):
            graph_like = any(hit.layer in {"graph", "image_evidence"} for hit in hits)
            fact_like = any(hit.layer == "fact" for hit in hits)
            if not (graph_like and fact_like):
                return False
        return True

    def _best_visual_line(self, content: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return content.strip()
        scored: list[tuple[int, str]] = []
        for line in lines:
            score = 0
            if any(ch.isdigit() for ch in line):
                score += 2
            if len(line.split()) >= 3:
                score += 1
            if re.search(r"\b(address|street|road|avenue|contract|move-in)\b", line.lower()):
                score += 1
            scored.append((score, line))
        scored.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        return scored[0][1]


def build_demo_memory(*, use_topic_hints: bool = True) -> EchoMemoryNanoReferenceV15:
    mem = EchoMemoryNanoReferenceV15()
    mem.append_text(
        role="user",
        write_time="2026-03-01T09:00:00Z",
        topic_hint="apartment_lease" if use_topic_hints else "",
        content="Maya found an apartment on Rua Augusta 14 on 2026-03-01.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-05T11:00:00Z",
        topic_hint="apartment_lease" if use_topic_hints else "",
        content="Maya showed the lease screenshot with move-in date 2026-03-20.",
    )
    mem.append_image(
        role="user",
        write_time="2026-03-05T11:05:00Z",
        topic_hint="apartment_lease" if use_topic_hints else "",
        linked_subject="Maya",
        caption="Lease contract screenshot",
        ocr="Rua Augusta 14 move-in 2026-03-20",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-12T14:00:00Z",
        topic_hint="apartment_lease" if use_topic_hints else "",
        content="The landlord delayed the handover and the move-in shifted to 2026-03-27.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-02T10:00:00Z",
        topic_hint="visa_process" if use_topic_hints else "",
        content="Maya started the visa paperwork and Nora helped Maya prepare the financial statement on 2026-03-02.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-18T15:00:00Z",
        topic_hint="visa_process" if use_topic_hints else "",
        content="Maya approved the visa process after the consulate received the residence document on 2026-03-18.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-03T09:00:00Z",
        topic_hint="product_launch" if use_topic_hints else "",
        content="Lena started the beta launch plan for 2026-04-10.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-19T18:00:00Z",
        topic_hint="product_launch" if use_topic_hints else "",
        content="Lena confirmed the launch date moved to 2026-04-24 after the payment bug fix.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-22T09:00:00Z",
        topic_hint="apartment_lease" if use_topic_hints else "",
        content="Maya signed a revised lease and the move-in date changed again to 2026-03-30.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-23T10:00:00Z",
        topic_hint="team_preferences" if use_topic_hints else "",
        content="Nora prefers tea on 2026-03-23.",
    )
    mem.append_text(
        role="user",
        write_time="2026-04-08T10:00:00Z",
        topic_hint="team_preferences" if use_topic_hints else "",
        content="Nora prefers coffee on 2026-04-08.",
    )
    mem.append_text(
        role="user",
        write_time="2026-04-09T09:00:00Z",
        topic_hint="team_preferences" if use_topic_hints else "",
        content="Earlier notes confirm Nora prefers tea on 2026-03-23.",
    )
    mem.append_text(
        role="user",
        write_time="2026-04-12T14:00:00Z",
        topic_hint="access_control" if use_topic_hints else "",
        content="Kai badge number 3142 on 2026-04-12.",
    )
    mem.append_text(
        role="user",
        write_time="2026-04-13T10:00:00Z",
        topic_hint="access_control" if use_topic_hints else "",
        content="Kai badge number 3147 on 2026-04-12.",
    )
    mem.build()
    return mem


def demo_cases() -> list[DemoCase]:
    return [
        DemoCase("temporal_1", "When did Maya start the visa paperwork?", "2026-03-20", ["2026-03-02"], "temporal"),
        DemoCase("relational_1", "Who helped Maya with the visa paperwork?", "2026-03-20", ["Nora", "Maya"], "relational"),
        DemoCase("longitudinal_1", "How did the apartment lease situation evolve?", "2026-04-01", ["Rua Augusta 14", "2026-03-30"], "longitudinal"),
        DemoCase("visual_1", "What was shown in the lease screenshot?", "2026-03-20", ["Rua Augusta 14"], "visual"),
        DemoCase("state_1", "What does Nora prefer now?", "2026-04-10", ["coffee"], "general"),
        DemoCase("state_2", "What did Nora prefer before the switch?", "2026-04-10", ["tea"], "temporal"),
        DemoCase("conflict_1", "What is Kai's badge number?", "2026-04-14", ["unknown_conflict"], "general"),
        DemoCase("readiness_1", "Can you answer now?", "2026-03-20", ["ready"], "readiness"),
    ]


def render_html(payload: dict[str, Any]) -> str:
    case_cards = []
    for row in payload["cases"]:
        hits_html = "".join(
            f"<li><code>{esc(hit['source'])}</code> · {esc(hit['layer'])} · score={hit['score']:.3f}<br>{esc(hit['content'])}</li>"
            for hit in row["result"]["hits"][:5]
        )
        case_cards.append(
            f"""
            <div class="card">
              <h3>{esc(row['case_id'])}</h3>
              <p><b>Query:</b> {esc(row['query'])}</p>
              <p><b>Family:</b> {esc(row['family'])}</p>
              <p><b>Plan:</b> {esc(row['result']['plan']['primary_reader'])} -> {esc(', '.join(row['result']['plan']['supporting_readers']))}</p>
              <p><b>Answer:</b> {esc(row['result']['answer'])}</p>
              <p><b>Contract:</b> {'ok' if row['result']['contract_ok'] else 'missing ' + ', '.join(row['result']['missing_layers'])}</p>
              <ul>{hits_html}</ul>
            </div>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Reference v16</title>
  <style>
    :root{{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184;
      --blue:#2563eb; --soft:#eef4ff; --shadow:0 10px 28px rgba(15,23,42,.08);
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:28px 18px 48px}}
    .hero,.section,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:26px;margin-bottom:16px}}
    .section{{padding:20px 22px;margin-bottom:16px}}
    .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
    .card{{padding:14px 16px;box-shadow:none}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.25}}
    h1{{font-size:30px}} h2{{font-size:20px}} h3{{font-size:16px}}
    p{{margin:0 0 10px}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    code{{background:#f3f6fb;border:1px solid #e4ebf5;border-radius:6px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    .tag{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;background:var(--soft);color:var(--blue);margin-right:8px}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:10px 8px;border-top:1px solid var(--line);text-align:left;vertical-align:top}}
    th{{background:#fbfcfe;color:var(--muted);font-size:12px;text-transform:uppercase}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Nano Reference v16</h1>
      <p>这不是 benchmark 脚本，而是一份单文件参考实现。目标是让人快速看懂 EchoMemory-MM 最关键的最小骨架：三时钟、topic dossier、中层时间树、关系图、readiness gate、contract-aware second pass，以及 state lifecycle 治理。</p>
      <div style="margin-top:12px;">
        <span class="tag">single-file reference</span>
        <span class="tag">generic</span>
        <span class="tag">no dataset hacks</span>
        <span class="tag">three-clock</span>
        <span class="tag">topic dossier</span>
        <span class="tag">state lifecycle</span>
      </div>
    </div>
    <div class="section">
      <h2>1. What this file keeps</h2>
      <ul>
        <li>append-only observations</li>
        <li>event_time / mention_time / write_time split</li>
        <li>atom extraction</li>
        <li>topic dossier middle layer</li>
        <li>temporal tree</li>
        <li>relation graph with image_evidence nodes</li>
        <li>readiness gate</li>
        <li>contract-aware second pass</li>
        <li>version chain + superseded / conflicted states</li>
      </ul>
    </div>
    <div class="section">
      <h2>2. Memory summary</h2>
      <table>
        <thead><tr><th>Observation count</th><th>Atom count</th><th>Dossiers</th><th>Tree blocks</th><th>Graph nodes</th><th>QA ready</th></tr></thead>
        <tbody><tr><td>{payload['summary']['observations']}</td><td>{payload['summary']['atoms']}</td><td>{payload['summary']['dossiers']}</td><td>{payload['summary']['tree_blocks']}</td><td>{payload['summary']['graph_nodes']}</td><td>{payload['summary']['qa_ready']}</td></tr></tbody>
      </table>
    </div>
    <div class="section">
      <h2>3. Demo cases</h2>
      <div class="grid">
        {''.join(case_cards)}
      </div>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    mem = build_demo_memory()
    rows = []
    for case in demo_cases():
        result = mem.retrieve(case.query, case.query_time)
        rows.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "family": case.family,
                "expected_keywords": case.expected_keywords,
                "result": result,
            }
        )
    payload = {
        "summary": {
            "observations": len(mem.observations),
            "atoms": len(mem.atoms),
            "dossiers": len(mem.dossiers),
            "tree_blocks": len(mem.temporal_tree),
            "graph_nodes": len(mem.nodes),
            "qa_ready": mem.readiness.qa_ready,
        },
        "readiness": asdict(mem.readiness),
        "cases": rows,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
