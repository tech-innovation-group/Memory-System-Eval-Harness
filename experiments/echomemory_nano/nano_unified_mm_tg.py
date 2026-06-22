#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass
class Observation:
    obs_id: str
    obs_type: str  # text | image
    content: str
    created_at: str
    event_time: str = ""
    speaker: str = ""
    caption: str = ""
    ocr: str = ""
    tags: list[str] = field(default_factory=list)
    linked_subject: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    mention_time: str
    event_time: str
    source_obs_id: str
    time_confidence: float = 0.0


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    source_ref: str
    event_time: str = ""


@dataclass
class Edge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class ReadinessState:
    messages_persisted: bool = False
    atoms_ready: bool = False
    graph_ready: bool = False
    organized_ready: bool = False
    multimodal_ready: bool = False
    qa_ready: bool = False


@dataclass
class QueryPlan:
    intent: str
    target_layers: list[str]
    graph_first: bool = False
    prefer_event: bool = False
    prefer_fact: bool = False
    prefer_visual: bool = False
    notes: str = ""


@dataclass
class SearchHit:
    node_id: str
    node_type: str
    score: float
    event_time: str
    content: str
    matched_terms: list[str]


@dataclass
class SearchResult:
    query: str
    plan: QueryPlan
    readiness: ReadinessState
    allowed_to_answer: bool
    hits: list[SearchHit]
    answer_sketch: str = ""
    note: str = ""


class UnifiedEchoMemoryNano:
    """
    A single-file teaching prototype for the current EchoMemory research story.

    It intentionally unifies:
    1. append-only text/image stream
    2. story-time vs mention-time separation
    3. readiness-aware memory visibility
    4. temporal-graph retrieval
    5. image_evidence as first-class memory objects

    This is not a production implementation. It is a compact conceptual model.
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.organized: list[dict[str, Any]] = []
        self.readiness = ReadinessState()

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def append_text(self, text: str, *, created_at: str, speaker: str = "user") -> None:
        event_time, confidence = self._infer_story_time(text, created_at)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="text",
                content=text.strip(),
                created_at=created_at,
                event_time=event_time,
                speaker=speaker,
            )
        )
        self._reset_readiness()
        self.readiness.messages_persisted = True

    def append_image(
        self,
        *,
        caption: str,
        created_at: str,
        ocr: str = "",
        tags: list[str] | None = None,
        linked_subject: str = "",
        event_time: str = "",
    ) -> None:
        if not event_time:
            event_time, _ = self._infer_story_time(f"{caption}\n{ocr}".strip(), created_at)
        content = "\n".join(part for part in [caption.strip(), ocr.strip(), ", ".join(tags or [])] if part)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="image",
                content=content,
                created_at=created_at,
                event_time=event_time,
                caption=caption.strip(),
                ocr=ocr.strip(),
                tags=list(tags or []),
                linked_subject=linked_subject.strip(),
            )
        )
        self._reset_readiness()
        self.readiness.messages_persisted = True

    def run_hot_path(self) -> None:
        self.atoms = []
        for obs in self.observations:
            if obs.obs_type != "text":
                continue
            self.atoms.extend(self._extract_atoms(obs))
        self.readiness.atoms_ready = True

    def run_cold_path(self) -> None:
        self.nodes = []
        self.edges = []
        self.organized = []

        seen_entities: set[str] = set()
        event_node_ids: list[tuple[str, str]] = []

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            self.nodes.append(
                Node(
                    node_id=fact_id,
                    node_type="fact",
                    source_ref=atom.source_obs_id,
                    event_time=atom.event_time,
                    content=(
                        f"statement={atom.statement}\n"
                        f"subject={atom.subject}\n"
                        f"predicate={atom.predicate}\n"
                        f"object={atom.object}\n"
                        f"mention_time={atom.mention_time}\n"
                        f"event_time={atom.event_time}\n"
                        f"time_confidence={atom.time_confidence:.2f}"
                    ),
                )
            )

            event_id = f"event:{atom.atom_id}"
            self.nodes.append(
                Node(
                    node_id=event_id,
                    node_type="event",
                    source_ref=atom.source_obs_id,
                    event_time=atom.event_time,
                    content=(
                        f"{atom.subject} / {atom.predicate} / {atom.object}\n"
                        f"statement={atom.statement}\n"
                        f"event_time={atom.event_time}"
                    ),
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
            event_node_ids.append((event_id, atom.event_time))

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
                    event_time=obs.event_time,
                    content=(
                        f"caption={obs.caption}\n"
                        f"ocr={obs.ocr}\n"
                        f"tags={', '.join(obs.tags)}\n"
                        f"linked_subject={obs.linked_subject}\n"
                        f"event_time={obs.event_time}\n"
                        f"created_at={obs.created_at}"
                    ),
                )
            )
            if obs.linked_subject:
                entity_id = f"entity:{obs.linked_subject}"
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    self.nodes.append(
                        Node(
                            node_id=entity_id,
                            node_type="entity",
                            source_ref=obs.obs_id,
                            content=f"name={obs.linked_subject}",
                        )
                    )
                self.edges.append(
                    Edge(
                        edge_id=f"{image_id}:shows:{entity_id}",
                        source_id=image_id,
                        target_id=entity_id,
                        relation_type="shows",
                    )
                )
            # Link image evidence to temporal-nearest event
            candidate_event = self._best_event_for_image(obs)
            if candidate_event:
                self.edges.append(
                    Edge(
                        edge_id=f"{image_id}:supports_event:{candidate_event}",
                        source_id=image_id,
                        target_id=candidate_event,
                        relation_type="supports_event",
                    )
                )

        event_node_ids = [item for item in event_node_ids if item[1]]
        event_node_ids.sort(key=lambda item: item[1])
        for left, right in zip(event_node_ids, event_node_ids[1:]):
            self.edges.append(
                Edge(
                    edge_id=f"{left[0]}:temporal_next:{right[0]}",
                    source_id=left[0],
                    target_id=right[0],
                    relation_type="temporal_next",
                )
            )

        self._build_organized_memory()
        self.readiness.graph_ready = True
        self.readiness.organized_ready = True
        self.readiness.multimodal_ready = any(obs.obs_type == "image" for obs in self.observations)
        self.readiness.qa_ready = (
            self.readiness.messages_persisted
            and self.readiness.atoms_ready
            and self.readiness.graph_ready
            and self.readiness.organized_ready
        )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def plan_query(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"screenshot|image|ocr|screen|visible|截图|图片|界面|屏幕|显示|看见|车站牌|海报", q, re.I):
            return QueryPlan(
                intent="visual",
                target_layers=["image_evidence", "event", "entity"],
                graph_first=True,
                prefer_visual=True,
                notes="Visual question: image evidence should be treated as first-class memory.",
            )
        if re.search(r"\bwhen\b|\bdate\b|\bhow long\b|什么时候|哪天|时间|日期|多久|前后|后来|之前|昨天|前天", q, re.I):
            return QueryPlan(
                intent="temporal",
                target_layers=["event", "fact", "organized"],
                graph_first=True,
                prefer_event=True,
                notes="Temporal question: prefer event nodes that preserve story time.",
            )
        if re.search(r"\bwho\b|\brelationship\b|\btogether\b|共同|谁|关系|一起|都有|共同点", q, re.I):
            return QueryPlan(
                intent="relational",
                target_layers=["entity", "event", "fact"],
                graph_first=True,
                notes="Relational question: traverse entity/event paths before flat summaries.",
            )
        if re.search(r"\blike\b|\bideal\b|\bprefer\b|\bstyle\b|喜欢|偏好|理想|风格|想要|希望", q, re.I):
            return QueryPlan(
                intent="profile",
                target_layers=["fact", "image_evidence", "organized"],
                prefer_fact=True,
                prefer_visual=True,
                notes="Profile or style question: text facts first, visual moodboard second.",
            )
        return QueryPlan(
            intent="general",
            target_layers=["fact", "organized", "event"],
            notes="Default factual path.",
        )

    def search(self, query: str) -> SearchResult:
        plan = self.plan_query(query)
        readiness_snapshot = replace(self.readiness)
        if not self.readiness.qa_ready:
            return SearchResult(
                query=query,
                plan=plan,
                readiness=readiness_snapshot,
                allowed_to_answer=False,
                hits=[],
                note="qa_ready=false: memory is persisted but not fully consolidated.",
            )

        terms = self._query_terms(query)
        candidate_layers = set(plan.target_layers)
        hits: list[SearchHit] = []

        for node in self.nodes:
            if node.node_type not in candidate_layers:
                continue
            score, matched = self._score_node(query, terms, node, plan)
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    score=score,
                    event_time=node.event_time,
                    content=node.content,
                    matched_terms=matched,
                )
            )

        if plan.graph_first:
            hits = self._apply_graph_boost(hits, plan)

        hits.sort(key=lambda h: (h.score, h.event_time, h.node_id), reverse=True)
        hits = hits[:8]
        answer = ""
        if hits:
            top = hits[0]
            answer = f"Use top evidence: {top.node_id} | {top.content.replace(chr(10), ' | ')}"

        return SearchResult(
            query=query,
            plan=plan,
            readiness=readiness_snapshot,
            allowed_to_answer=True,
            hits=hits,
            answer_sketch=answer,
        )

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_atoms(self, obs: Observation) -> list[Atom]:
        text = obs.content.strip()
        story_time, confidence = self._infer_story_time(text, obs.created_at)
        atoms: list[Atom] = []

        rules: list[tuple[str, str, str, str, str]] = [
            (r"([A-Z][a-z]+)\s+arrived in\s+([A-Z][a-z]+)", "event", "{0}", "arrived_in", "{1}"),
            (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)", "event", "{0}", "visited", "{1}"),
            (r"([A-Z][a-z]+)\s+opened\s+(his|her)\s+([a-zA-Z_ ]+)", "event", "{0}", "opened", "{2}"),
            (r"([A-Z][a-z]+)\s+started learning\s+([a-zA-Z_ ]+)", "event", "{0}", "started_learning", "{1}"),
            (r"([A-Z][a-z]+)\s+wanted.*to look like\s+(.+)", "fact", "{0}", "wanted_style", "{1}"),
            (r"([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)\s+both\s+lost their jobs and later started businesses", "relation", "{0}", "shared_experience_with", "{1}"),
            (r"([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)\s+both\s+lost their jobs and later started businesses", "relation", "{0}", "shared_business_transition_with", "{1}"),
        ]

        for idx, rule in enumerate(rules):
            pattern, atom_type, subj_t, pred_t, obj_t = rule
            match = re.search(pattern, text, re.I)
            if not match:
                continue
            groups = match.groups()
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms):03d}",
                    atom_type=atom_type,
                    subject=subj_t.format(*groups).strip(),
                    predicate=pred_t.format(*groups).strip(),
                    object=obj_t.format(*groups).strip(),
                    statement=text,
                    mention_time=obs.created_at,
                    event_time=story_time,
                    source_obs_id=obs.obs_id,
                    time_confidence=confidence,
                )
            )
            # Allow paired relation atoms from same sentence.
            if atom_type != "relation":
                break

        if atoms:
            return atoms

        return [
            Atom(
                atom_id=f"atom-{len(self.atoms):03d}",
                atom_type="fact",
                subject="unknown",
                predicate="mentions",
                object=text[:48],
                statement=text,
                mention_time=obs.created_at,
                event_time=story_time,
                source_obs_id=obs.obs_id,
                time_confidence=confidence,
            )
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reset_readiness(self) -> None:
        self.readiness.atoms_ready = False
        self.readiness.graph_ready = False
        self.readiness.organized_ready = False
        self.readiness.multimodal_ready = False
        self.readiness.qa_ready = False

    def _best_event_for_image(self, obs: Observation) -> str:
        candidates = [node for node in self.nodes if node.node_type == "event"]
        if not candidates:
            return ""
        if obs.linked_subject:
            for edge in self.edges:
                if edge.relation_type != "involves":
                    continue
                if edge.target_id == f"entity:{obs.linked_subject}":
                    event_node = edge.source_id
                    event = next((n for n in candidates if n.node_id == event_node), None)
                    if event and event.event_time == obs.event_time:
                        return event.node_id
        for event in candidates:
            if event.event_time == obs.event_time:
                return event.node_id
        return candidates[0].node_id

    def _build_organized_memory(self) -> None:
        grouped: dict[str, list[Node]] = {}
        for node in self.nodes:
            if node.node_type != "event":
                continue
            content = node.content.split("\n", 1)[0]
            subject = content.split(" / ", 1)[0] if " / " in content else "unknown"
            grouped.setdefault(subject, []).append(node)

        self.organized = []
        for subject, items in sorted(grouped.items()):
            ordered = sorted(items, key=lambda n: n.event_time or "9999")
            summary = "; ".join(f"{n.event_time}: {n.content.splitlines()[0]}" for n in ordered[:6])
            self.organized.append(
                {
                    "summary_id": f"org:{subject}",
                    "subject": subject,
                    "content": summary,
                }
            )
            self.nodes.append(
                Node(
                    node_id=f"organized:{subject}",
                    node_type="organized",
                    source_ref="derived",
                    content=summary,
                    event_time=ordered[-1].event_time if ordered else "",
                )
            )

    def _infer_story_time(self, text: str, created_at: str) -> tuple[str, float]:
        if match := re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text):
            return match.group(1), 0.95
        if match := re.search(r"\b(20\d{2}-\d{2})\b", text):
            return match.group(1), 0.9
        if "yesterday" in text.lower() or "昨天" in text:
            return self._shift_date(created_at, -1), 0.7
        if "the day before yesterday" in text.lower() or "前天" in text:
            return self._shift_date(created_at, -2), 0.65
        if match := re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b", text, re.I):
            month_map = {
                "january": "01", "february": "02", "march": "03", "april": "04",
                "may": "05", "june": "06", "july": "07", "august": "08",
                "september": "09", "october": "10", "november": "11", "december": "12",
            }
            month = month_map[match.group(1).lower()]
            return f"{match.group(2)}-{month}", 0.85
        return created_at, 0.2

    @staticmethod
    def _shift_date(date_text: str, delta_days: int) -> str:
        from datetime import datetime, timedelta
        dt = datetime.strptime(date_text, "%Y-%m-%d")
        return (dt + timedelta(days=delta_days)).strftime("%Y-%m-%d")

    @staticmethod
    def _looks_like_entity(text: str) -> bool:
        value = text.strip()
        if not value:
            return False
        if len(value) <= 1:
            return False
        if value.lower() in {"job", "businesses", "studio_social_media"}:
            return True
        if value[0].isupper():
            return True
        return bool(re.search(r"[A-Z][a-z]+", value))

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        lowered = query.lower()
        terms = re.findall(r"[a-z0-9:]+", lowered)
        terms += re.findall(r"[\u4e00-\u9fff]{1,4}", lowered)
        return [t for t in terms if t]

    def _score_node(self, query: str, terms: list[str], node: Node, plan: QueryPlan) -> tuple[float, list[str]]:
        content = node.content.lower()
        matched = [term for term in terms if term in content]
        if not matched:
            return 0.0, []
        score = float(len(matched))

        if plan.prefer_event and node.node_type == "event":
            score += 2.0
        if plan.prefer_fact and node.node_type == "fact":
            score += 1.8
        if plan.prefer_visual and node.node_type == "image_evidence":
            score += 2.4
        if plan.intent == "relational" and node.node_type in {"entity", "event"}:
            score += 1.4
        if plan.intent == "profile" and node.node_type == "image_evidence":
            score += 0.9
        if node.event_time and re.search(r"20\d{2}", query):
            if node.event_time in query:
                score += 0.6
        return score, matched

    def _apply_graph_boost(self, hits: list[SearchHit], plan: QueryPlan) -> list[SearchHit]:
        hit_map = {h.node_id: h for h in hits}
        adjacency: dict[str, list[str]] = {}
        for edge in self.edges:
            adjacency.setdefault(edge.source_id, []).append(edge.target_id)
            adjacency.setdefault(edge.target_id, []).append(edge.source_id)

        for hit in hits:
            neighbors = adjacency.get(hit.node_id, [])
            structural_bonus = 0.0
            for neighbor in neighbors:
                if neighbor in hit_map:
                    structural_bonus += 0.15
            if plan.intent == "visual" and hit.node_type == "image_evidence":
                structural_bonus += 0.35
            if plan.intent == "temporal" and hit.node_type == "event":
                structural_bonus += 0.25
            if plan.intent == "relational" and hit.node_type in {"entity", "event"}:
                structural_bonus += 0.2
            hit.score += structural_bonus
        return hits


def build_demo() -> dict[str, Any]:
    mem = UnifiedEchoMemoryNano()
    mem.append_text(
        "Jon lost his banker job yesterday and decided to start a studio.",
        created_at="2023-01-20",
    )
    mem.append_text(
        "Jon opened his studio in April 2023 after months of preparation.",
        created_at="2023-04-18",
    )
    mem.append_text(
        "Jon started expanding his studio social media presence in April 2023.",
        created_at="2023-04-22",
    )
    mem.append_text(
        "Jon visited Rome on 2023-06-19.",
        created_at="2023-06-20",
    )
    mem.append_text(
        "Jon started learning marketing and analytics tools in July 2023.",
        created_at="2023-07-10",
    )
    mem.append_text(
        "Jon and Gina both lost their jobs and later started businesses.",
        created_at="2023-07-23",
    )
    mem.append_text(
        "Jon wanted the dance studio to look like a waterfront loft with natural light and Marley flooring.",
        created_at="2023-07-25",
    )
    mem.append_image(
        caption="Phone screenshot from Gina's arrival day showing Roma Termini station board.",
        ocr="Roma Termini 08:42 Platform 7",
        tags=["rome", "station", "arrival", "travel"],
        linked_subject="Gina",
        created_at="2023-07-24",
        event_time="2023-07-23",
    )
    mem.append_image(
        caption="Moodboard screenshot for Jon's dream studio with sunlit windows and a waterfront interior.",
        ocr="waterfront loft natural light Marley floor",
        tags=["studio", "moodboard", "design", "waterfront"],
        linked_subject="Jon",
        created_at="2023-07-25",
        event_time="2023-07-25",
    )

    pre_ready = mem.search("When did Jon start learning marketing and analytics tools?")
    mem.run_hot_path()
    after_hot = mem.search("When did Jon start learning marketing and analytics tools?")
    mem.run_cold_path()

    queries = [
        "When did Jon start learning marketing and analytics tools?",
        "What did Jon and Gina both have in common?",
        "What time was visible in Gina's screenshot?",
        "What did Jon want the studio to look like?",
    ]
    results = [mem.search(q) for q in queries]

    return {
        "system_name": "UnifiedEchoMemoryNano",
        "research_claim": [
            "text and image evidence should share one temporal memory graph",
            "story time must be separated from mention/write time",
            "qa_ready should gate answering",
            "planner-guided graph retrieval improves temporal/relational/visual evidence selection",
        ],
        "readiness_before_hot": asdict(pre_ready.readiness),
        "pre_ready_result": asdict(pre_ready),
        "after_hot_result": asdict(after_hot),
        "readiness_after_cold": asdict(mem.readiness),
        "final_results": [asdict(item) for item in results],
        "atoms": [asdict(atom) for atom in mem.atoms],
        "nodes": [asdict(node) for node in mem.nodes],
        "edges": [asdict(edge) for edge in mem.edges],
        "organized": mem.organized,
    }


def main() -> None:
    demo = build_demo()
    out_path = Path(__file__).with_name("nano_unified_mm_tg_output.json")
    out_path.write_text(json.dumps(demo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
