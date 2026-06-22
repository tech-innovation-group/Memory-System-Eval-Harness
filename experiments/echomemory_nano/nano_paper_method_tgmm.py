#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Observation:
    obs_id: str
    obs_type: str
    content: str
    observed_at: str
    committed_at: str
    speaker: str = ""
    event_time: str = ""
    caption: str = ""
    ocr: str = ""
    linked_subject: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    observed_at: str
    committed_at: str
    event_time_start: str
    event_time_end: str
    source_obs_id: str
    salience: str = "medium"


@dataclass
class MemoryBlock:
    block_id: str
    block_type: str
    title: str
    content: str
    source_refs: list[str]
    event_time_start: str = ""
    event_time_end: str = ""


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    source_ref: str
    observed_at: str = ""
    committed_at: str = ""
    event_time_start: str = ""
    event_time_end: str = ""


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
    blocks_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False


@dataclass
class QueryPlan:
    intent: str
    layers: list[str]
    graph_first: bool
    note: str


@dataclass
class Hit:
    item_id: str
    layer: str
    score: float
    content: str
    evidence_time: str = ""


class NanoPaperMethodTGMM:
    """
    A compact prototype aligned with the proposed paper path:
    stream -> atoms -> typed blocks -> temporal graph -> planned retrieval.
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.blocks: list[MemoryBlock] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.readiness = Readiness()

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def append_text(
        self,
        text: str,
        *,
        observed_at: str,
        committed_at: str,
        speaker: str = "user",
    ) -> None:
        event_time = self._infer_event_time(text, observed_at)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="text",
                content=text.strip(),
                observed_at=observed_at,
                committed_at=committed_at,
                speaker=speaker,
                event_time=event_time,
            )
        )
        self._invalidate()
        self.readiness.messages_persisted = True

    def append_image(
        self,
        *,
        caption: str,
        ocr: str,
        observed_at: str,
        committed_at: str,
        linked_subject: str = "",
        tags: list[str] | None = None,
        event_time: str = "",
    ) -> None:
        effective_event_time = event_time or self._infer_event_time(f"{caption} {ocr}", observed_at)
        content = "\n".join(
            p for p in [caption.strip(), ocr.strip(), ", ".join(tags or [])] if p
        )
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="image",
                content=content,
                observed_at=observed_at,
                committed_at=committed_at,
                event_time=effective_event_time,
                caption=caption.strip(),
                ocr=ocr.strip(),
                linked_subject=linked_subject.strip(),
                tags=list(tags or []),
            )
        )
        self._invalidate()
        self.readiness.messages_persisted = True

    def project(self) -> None:
        self._extract_atoms()
        self._build_blocks()
        self._build_graph()
        self.readiness.atoms_ready = True
        self.readiness.blocks_ready = True
        self.readiness.graph_ready = True
        self.readiness.qa_ready = True

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _extract_atoms(self) -> None:
        self.atoms = []
        for obs in self.observations:
            if obs.obs_type != "text":
                continue
            text = obs.content
            patterns = [
                (r"([A-Z][a-z]+)\s+lost his job on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "lost_job", "job", "{1}"),
                (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "visited", "{1}", "{2}"),
                (r"([A-Z][a-z]+)\s+plans to\s+(.+)", "plan", "{0}", "plans", "{1}", ""),
                (r"([A-Z][a-z]+)\s+likes\s+(.+)", "preference", "{0}", "likes", "{1}", ""),
                (r"([A-Z][a-z]+)\s+married\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "married", "{1}", "{2}"),
            ]
            matched = False
            for pat, atom_type, subj_t, pred_t, obj_t, evt_t in patterns:
                m = re.search(pat, text, re.I)
                if not m:
                    continue
                g = m.groups()
                evt = evt_t.format(*g).strip() if evt_t else obs.event_time
                self.atoms.append(
                    Atom(
                        atom_id=f"atom-{len(self.atoms):03d}",
                        atom_type=atom_type,
                        subject=subj_t.format(*g).strip(),
                        predicate=pred_t.format(*g).strip(),
                        object=obj_t.format(*g).strip(),
                        statement=text,
                        observed_at=obs.observed_at,
                        committed_at=obs.committed_at,
                        event_time_start=evt or obs.event_time,
                        event_time_end=evt or obs.event_time,
                        source_obs_id=obs.obs_id,
                        salience="high" if atom_type in {"event", "plan"} else "medium",
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
                        object=text[:40],
                        statement=text,
                        observed_at=obs.observed_at,
                        committed_at=obs.committed_at,
                        event_time_start=obs.event_time,
                        event_time_end=obs.event_time,
                        source_obs_id=obs.obs_id,
                        salience="low",
                    )
                )

    def _build_blocks(self) -> None:
        self.blocks = []
        profile_bits: list[str] = []
        timeline_bits: list[str] = []
        plan_bits: list[str] = []
        profile_refs: list[str] = []
        timeline_refs: list[str] = []
        plan_refs: list[str] = []

        for atom in self.atoms:
            if atom.atom_type == "preference":
                profile_bits.append(f"- {atom.subject} likes {atom.object}")
                profile_refs.append(atom.atom_id)
            elif atom.atom_type == "plan":
                plan_bits.append(f"- {atom.subject} plans to {atom.object}")
                plan_refs.append(atom.atom_id)
            else:
                timeline_bits.append(f"- {atom.event_time_start or atom.observed_at}: {atom.statement}")
                timeline_refs.append(atom.atom_id)

        if profile_bits:
            self.blocks.append(
                MemoryBlock(
                    block_id="block-profile-main",
                    block_type="profile",
                    title="Stable profile",
                    content="\n".join(profile_bits),
                    source_refs=profile_refs,
                )
            )
        if timeline_bits:
            self.blocks.append(
                MemoryBlock(
                    block_id="block-timeline-main",
                    block_type="timeline",
                    title="Event timeline",
                    content="\n".join(timeline_bits),
                    source_refs=timeline_refs,
                )
            )
        if plan_bits:
            self.blocks.append(
                MemoryBlock(
                    block_id="block-plan-main",
                    block_type="plan",
                    title="Active plans",
                    content="\n".join(plan_bits),
                    source_refs=plan_refs,
                )
            )

    def _build_graph(self) -> None:
        self.nodes = []
        self.edges = []
        seen_entities: set[str] = set()
        events: list[tuple[str, str]] = []

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            self.nodes.append(
                Node(
                    node_id=fact_id,
                    node_type="fact",
                    source_ref=atom.source_obs_id,
                    observed_at=atom.observed_at,
                    committed_at=atom.committed_at,
                    event_time_start=atom.event_time_start,
                    event_time_end=atom.event_time_end,
                    content=(
                        f"statement={atom.statement}\n"
                        f"subject={atom.subject}\n"
                        f"predicate={atom.predicate}\n"
                        f"object={atom.object}"
                    ),
                )
            )

            if atom.atom_type in {"event", "plan"} or atom.event_time_start:
                event_id = f"event:{atom.atom_id}"
                self.nodes.append(
                    Node(
                        node_id=event_id,
                        node_type="event",
                        source_ref=atom.source_obs_id,
                        observed_at=atom.observed_at,
                        committed_at=atom.committed_at,
                        event_time_start=atom.event_time_start,
                        event_time_end=atom.event_time_end,
                        content=f"{atom.subject} {atom.predicate} {atom.object}",
                    )
                )
                self.edges.append(
                    Edge(
                        edge_id=f"{event_id}:evidence_of:{fact_id}",
                        source_id=event_id,
                        target_id=fact_id,
                        relation_type="evidence_of",
                    )
                )
                events.append((event_id, atom.event_time_start))
            else:
                event_id = ""

            for ent in [atom.subject, atom.object]:
                if not self._looks_like_entity(ent):
                    continue
                entity_id = f"entity:{ent}"
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    self.nodes.append(
                        Node(
                            node_id=entity_id,
                            node_type="entity",
                            source_ref=atom.source_obs_id,
                            content=f"name={ent}",
                        )
                    )
                self.edges.append(
                    Edge(
                        edge_id=f"{entity_id}:has_fact:{atom.atom_id}",
                        source_id=entity_id,
                        target_id=fact_id,
                        relation_type="has_fact",
                    )
                )
                if event_id:
                    self.edges.append(
                        Edge(
                            edge_id=f"{event_id}:involves:{ent}",
                            source_id=event_id,
                            target_id=entity_id,
                            relation_type="involves",
                        )
                    )

        for obs in self.observations:
            if obs.obs_type != "image":
                continue
            image_id = f"image:{obs.obs_id}"
            self.nodes.append(
                Node(
                    node_id=image_id,
                    node_type="image_evidence",
                    source_ref=obs.obs_id,
                    observed_at=obs.observed_at,
                    committed_at=obs.committed_at,
                    event_time_start=obs.event_time,
                    event_time_end=obs.event_time,
                    content=f"caption={obs.caption}\nocr={obs.ocr}\ntags={','.join(obs.tags)}",
                )
            )
            if obs.linked_subject:
                self.edges.append(
                    Edge(
                        edge_id=f"{image_id}:shows:{obs.linked_subject}",
                        source_id=image_id,
                        target_id=f"entity:{obs.linked_subject}",
                        relation_type="shows",
                    )
                )

        events = [e for e in events if e[1]]
        events.sort(key=lambda x: x[1])
        for left, right in zip(events, events[1:]):
            self.edges.append(
                Edge(
                    edge_id=f"{left[0]}:temporal_next:{right[0]}",
                    source_id=left[0],
                    target_id=right[0],
                    relation_type="temporal_next",
                )
            )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def plan_query(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"截图|图片|ocr|图里|写着什么|screen|image|screenshot", q, re.I):
            return QueryPlan("visual", ["image_evidence", "event", "block"], True, "Visual evidence first.")
        if re.search(r"什么时候|哪天|when|date|later|before|之后|之前|昨天", q, re.I):
            return QueryPlan("temporal", ["event", "fact", "block"], True, "Event nodes carry event time.")
        if re.search(r"计划|plan|打算|未来", q, re.I):
            return QueryPlan("plan", ["block", "event", "fact"], True, "Plan block summarizes future actions.")
        if re.search(r"喜欢|偏好|like|prefer|favorite", q, re.I):
            return QueryPlan("profile", ["block", "fact", "entity"], False, "Profile block is efficient for stable preferences.")
        return QueryPlan("general", ["fact", "block", "event"], False, "Fallback.")

    def search(self, query: str) -> dict:
        plan = self.plan_query(query)
        if not self.readiness.qa_ready:
            return {
                "query": query,
                "plan": asdict(plan),
                "readiness": asdict(self.readiness),
                "allowed_to_answer": False,
                "hits": [],
                "note": "Not QA-ready yet.",
            }

        hits: list[Hit] = []
        terms = self._query_terms(query)

        if "block" in plan.layers:
            for block in self.blocks:
                score = self._lexical_score(block.title + "\n" + block.content, terms)
                if score > 0:
                    hits.append(Hit(block.block_id, "block", score + 0.3, block.content))

        if plan.graph_first:
            for node in self.nodes:
                if node.node_type not in set(plan.layers):
                    continue
                score = self._lexical_score(node.content, terms)
                if score <= 0:
                    continue
                if plan.intent == "temporal" and node.node_type == "event":
                    score += 1.0
                if plan.intent == "visual" and node.node_type == "image_evidence":
                    score += 1.2
                hits.append(Hit(node.node_id, node.node_type, score, node.content, node.event_time_start))
        else:
            for node in self.nodes:
                if node.node_type not in {"fact", "entity"}:
                    continue
                score = self._lexical_score(node.content, terms)
                if score > 0:
                    hits.append(Hit(node.node_id, node.node_type, score, node.content, node.event_time_start))

        hits.sort(key=lambda h: (-h.score, h.item_id))
        return {
            "query": query,
            "plan": asdict(plan),
            "readiness": asdict(self.readiness),
            "allowed_to_answer": True,
            "hits": [asdict(h) for h in hits[:5]],
            "note": "Graph-first on temporal/visual; blocks help profile/plan questions.",
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _invalidate(self) -> None:
        self.readiness.atoms_ready = False
        self.readiness.blocks_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    @staticmethod
    def _infer_event_time(text: str, fallback: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if m:
            return m.group(1)
        if re.search(r"yesterday|昨天", text, re.I) and len(fallback) >= 10:
            return fallback[:10]
        return fallback[:10] if fallback else ""

    @staticmethod
    def _looks_like_entity(text: str) -> bool:
        t = text.strip()
        if not t or t == "unknown":
            return False
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            return False
        return bool(re.match(r"[A-Z][a-z]+$", t))

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        lowered = query.lower()
        return [t for t in re.findall(r"[a-z]{2,}|[\u4e00-\u9fa5]{1,}", lowered) if t not in {"the", "did", "what"}]

    @staticmethod
    def _lexical_score(text: str, terms: list[str]) -> float:
        hay = text.lower()
        return float(sum(1 for term in terms if term in hay))


def build_demo() -> tuple[NanoPaperMethodTGMM, dict]:
    sys = NanoPaperMethodTGMM()
    sys.append_text(
        "Jon lost his job on 2023-01-19 and decided to open a dance studio.",
        observed_at="2023-01-28T09:00:00Z",
        committed_at="2023-01-28T09:00:05Z",
    )
    sys.append_text(
        "Gina visited Rome on 2023-01-30 after her interview.",
        observed_at="2023-02-02T10:00:00Z",
        committed_at="2023-02-02T10:00:06Z",
    )
    sys.append_text(
        "Jon plans to call three investors next week.",
        observed_at="2023-02-03T12:00:00Z",
        committed_at="2023-02-03T12:00:05Z",
    )
    sys.append_text(
        "Gina likes jazz and museums.",
        observed_at="2023-02-03T12:30:00Z",
        committed_at="2023-02-03T12:30:05Z",
    )
    sys.append_image(
        caption="Finance dashboard screenshot",
        ocr="Revenue 123; Margin 18%",
        tags=["finance", "dashboard"],
        linked_subject="Jon",
        observed_at="2023-02-04T08:00:00Z",
        committed_at="2023-02-04T08:00:02Z",
        event_time="2023-02-04",
    )
    sys.project()

    queries = [
        "When did Jon lose his job?",
        "What does Gina like?",
        "What does Jon plan to do?",
        "What does the screenshot say?",
    ]
    results = [sys.search(q) for q in queries]

    return sys, {
        "readiness": asdict(sys.readiness),
        "counts": {
            "observations": len(sys.observations),
            "atoms": len(sys.atoms),
            "blocks": len(sys.blocks),
            "nodes": len(sys.nodes),
            "edges": len(sys.edges),
        },
        "queries": results,
    }


if __name__ == "__main__":
    system, payload = build_demo()
    out = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm_output.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
