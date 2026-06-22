#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Observation:
    obs_id: str
    obs_type: str  # text | image
    content: str
    created_at: str = ""
    event_time: str = ""
    speaker: str = ""
    caption: str = ""
    ocr: str = ""
    tags: list[str] | None = None
    linked_subject: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    event_time: str = ""
    created_at: str = ""
    source_obs_id: str = ""


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
class QueryPlan:
    intent: str
    target_layers: list[str]
    notes: str = ""


class EchoMemoryMultiModalNano:
    """
    一个故意做小的 multimodal temporal-graph memory。

    目标不是拟真生产系统，而是帮助理解：
    1. 文本事实怎么进图
    2. 截图/图像证据怎么成为一等记忆对象
    3. 为什么 visual query 不能只靠 text memory
    4. 为什么 CVPR 版本需要 image evidence nodes
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []

    def append_text(
        self,
        text: str,
        *,
        created_at: str = "",
        event_time: str = "",
        speaker: str = "user",
    ) -> None:
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="text",
                content=text,
                created_at=created_at,
                event_time=event_time or self._extract_date(text) or created_at,
                speaker=speaker,
            )
        )

    def append_image(
        self,
        *,
        caption: str,
        ocr: str = "",
        tags: list[str] | None = None,
        created_at: str = "",
        event_time: str = "",
        linked_subject: str = "",
    ) -> None:
        content = "\n".join(
            part for part in [caption.strip(), ocr.strip(), ", ".join(tags or [])] if part
        )
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="image",
                content=content,
                caption=caption,
                ocr=ocr,
                tags=tags or [],
                created_at=created_at,
                event_time=event_time or self._extract_date(caption) or created_at,
                linked_subject=linked_subject,
            )
        )

    def extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        patterns: list[tuple[str, str, str, str, str]] = [
            (r"([A-Z][a-z]+)\s+arrived in\s+([A-Z][a-z]+)", "event", "{0}", "arrived_in", "{1}"),
            (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)", "event", "{0}", "visited", "{1}"),
            (r"([A-Z][a-z]+)\s+wanted .* to look like (.+)", "fact", "{0}", "wanted_style", "{1}"),
            (r"([A-Z][a-z]+)\s+compared .* on\s+(20\d{2}-\d{2}-\d{2})", "event", "{0}", "compared_locations", "locations"),
        ]

        for obs in self.observations:
            if obs.obs_type != "text":
                continue
            matched = False
            for pat, atom_type, subj_t, pred_t, obj_t in patterns:
                m = re.search(pat, obs.content, re.I)
                if not m:
                    continue
                groups = m.groups()
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=atom_type,
                        subject=subj_t.format(*groups).strip(),
                        predicate=pred_t.format(*groups).strip(),
                        object=obj_t.format(*groups).strip(),
                        statement=obs.content.strip(),
                        event_time=obs.event_time,
                        created_at=obs.created_at,
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
                        object=obs.content[:64],
                        statement=obs.content.strip(),
                        event_time=obs.event_time,
                        created_at=obs.created_at,
                        source_obs_id=obs.obs_id,
                    )
                )
        self.atoms = atoms
        return atoms

    def build_memory(self) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen_entities: set[str] = set()

        # Text-derived fact / event / entity nodes.
        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(
                Node(
                    node_id=fact_id,
                    node_type="fact",
                    event_time=atom.event_time,
                    source_ref=atom.source_obs_id,
                    content=(
                        f"statement={atom.statement}\n"
                        f"subject={atom.subject}\n"
                        f"predicate={atom.predicate}\n"
                        f"object={atom.object}\n"
                        f"event_time={atom.event_time}\n"
                        f"created_at={atom.created_at}"
                    ),
                )
            )

            event_id = f"event:{atom.atom_id}"
            nodes.append(
                Node(
                    node_id=event_id,
                    node_type="event",
                    event_time=atom.event_time,
                    source_ref=atom.source_obs_id,
                    content=(
                        f"statement={atom.statement}\n"
                        f"participants={atom.subject}, {atom.object}\n"
                        f"event_time={atom.event_time}"
                    ),
                )
            )
            edges.append(
                Edge(
                    edge_id=f"{event_id}:evidence_of:{fact_id}",
                    source_id=event_id,
                    target_id=fact_id,
                    relation_type="evidence_of",
                )
            )

            for entity in [atom.subject, atom.object]:
                if not self._looks_like_entity(entity):
                    continue
                entity_id = f"entity:{entity}"
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    nodes.append(
                        Node(
                            node_id=entity_id,
                            node_type="entity",
                            source_ref=atom.source_obs_id,
                            content=f"name={entity}\nfirst_seen_in={atom.statement}",
                        )
                    )
                edges.append(
                    Edge(
                        edge_id=f"{entity_id}:has_fact:{atom.atom_id}",
                        source_id=entity_id,
                        target_id=fact_id,
                        relation_type="has_fact",
                    )
                )
                edges.append(
                    Edge(
                        edge_id=f"{event_id}:involves:{entity}",
                        source_id=event_id,
                        target_id=entity_id,
                        relation_type="involves",
                    )
                )

        # Image evidence nodes.
        for obs in self.observations:
            if obs.obs_type != "image":
                continue
            image_id = f"image:{obs.obs_id}"
            nodes.append(
                Node(
                    node_id=image_id,
                    node_type="image_evidence",
                    event_time=obs.event_time,
                    source_ref=obs.obs_id,
                    content=(
                        f"caption={obs.caption}\n"
                        f"ocr={obs.ocr}\n"
                        f"tags={', '.join(obs.tags or [])}\n"
                        f"linked_subject={obs.linked_subject}\n"
                        f"event_time={obs.event_time}\n"
                        f"created_at={obs.created_at}"
                    ),
                )
            )

            if obs.linked_subject:
                subject_id = f"entity:{obs.linked_subject}"
                if subject_id not in seen_entities:
                    seen_entities.add(subject_id)
                    nodes.append(
                        Node(
                            node_id=subject_id,
                            node_type="entity",
                            source_ref=obs.obs_id,
                            content=f"name={obs.linked_subject}\nfirst_seen_in={obs.caption}",
                        )
                    )
                edges.append(
                    Edge(
                        edge_id=f"{image_id}:shows:{obs.linked_subject}",
                        source_id=image_id,
                        target_id=subject_id,
                        relation_type="shows",
                    )
                )

            # Link image evidence to nearby events by shared subject/time.
            matched_atom = self._best_matching_atom_for_image(obs)
            if matched_atom is not None:
                event_id = f"event:{matched_atom.atom_id}"
                fact_id = f"fact:{matched_atom.atom_id}"
                edges.append(
                    Edge(
                        edge_id=f"{image_id}:supports:{event_id}",
                        source_id=image_id,
                        target_id=event_id,
                        relation_type="supports_event",
                    )
                )
                edges.append(
                    Edge(
                        edge_id=f"{image_id}:evidence_of:{fact_id}",
                        source_id=image_id,
                        target_id=fact_id,
                        relation_type="visual_evidence_of",
                    )
                )

        self.nodes = nodes
        self.edges = edges
        return nodes, edges

    def plan_query(self, query: str) -> QueryPlan:
        q = query.lower().strip()
        if re.search(r"photo|image|screenshot|screen|截图|照片|图片|ocr|visible|看见|显示", q, re.I):
            return QueryPlan(
                intent="visual",
                target_layers=["image_evidence", "event", "entity"],
                notes="Visual query should prioritize image evidence nodes and OCR-bearing content.",
            )
        if re.search(r"when|time|what time|什么时候|几点|时间|日期|哪天", q, re.I):
            return QueryPlan(
                intent="temporal",
                target_layers=["event", "image_evidence", "fact"],
                notes="Temporal query should prefer event nodes, but visual timestamps are valid secondary evidence.",
            )
        if re.search(r"look like|style|ideal|wanted|理想|什么样", q, re.I):
            return QueryPlan(
                intent="profile",
                target_layers=["fact", "image_evidence"],
                notes="Preference or style queries can use both textual fact nodes and visual moodboard evidence.",
            )
        return QueryPlan(
            intent="general",
            target_layers=["fact", "event", "entity", "image_evidence"],
            notes="Fallback mixed retrieval.",
        )

    def search(self, query: str, *, text_only: bool = False, top_k: int = 5) -> dict[str, Any]:
        plan = self.plan_query(query)
        scored: list[tuple[float, Node]] = []
        q_tokens = set(self._tokens(query))

        for node in self.nodes:
            if text_only and node.node_type == "image_evidence":
                continue
            n_tokens = set(self._tokens(node.content))
            overlap = len(q_tokens & n_tokens)
            if overlap <= 0:
                continue
            score = float(overlap)

            if node.node_type in plan.target_layers:
                score += 2.0
            if plan.intent == "visual" and node.node_type == "image_evidence":
                score += 2.0
            if plan.intent == "temporal" and node.node_type == "event":
                score += 1.0
            if "ocr" in query.lower() and "ocr=" in node.content.lower():
                score += 1.5

            scored.append((score, node))

        scored.sort(key=lambda item: item[0], reverse=True)
        hits = [
            {
                "score": round(score, 3),
                "node_id": node.node_id,
                "node_type": node.node_type,
                "event_time": node.event_time,
                "content": node.content,
            }
            for score, node in scored[:top_k]
        ]
        return {
            "query": query,
            "text_only": text_only,
            "plan": asdict(plan),
            "hits": hits,
        }

    def dump(self) -> dict[str, Any]:
        return {
            "observations": [asdict(obs) for obs in self.observations],
            "atoms": [asdict(atom) for atom in self.atoms],
            "nodes": [asdict(node) for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
        }

    def _best_matching_atom_for_image(self, obs: Observation) -> Atom | None:
        for atom in self.atoms:
            if obs.linked_subject and atom.subject != obs.linked_subject:
                continue
            if obs.event_time and atom.event_time and obs.event_time == atom.event_time:
                return atom
        for atom in self.atoms:
            if obs.linked_subject and atom.subject == obs.linked_subject:
                return atom
        return None

    @staticmethod
    def _extract_date(text: str) -> str:
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        return match.group(1) if match else ""

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", str(text).lower())

    @staticmethod
    def _looks_like_entity(value: str) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        if len(raw) <= 2:
            return False
        if raw.replace("-", "").replace("/", "").isdigit():
            return False
        return True


def demo() -> dict[str, Any]:
    mem = EchoMemoryMultiModalNano()
    mem.append_text(
        "Gina arrived in Rome on 2023-01-30 for a design interview.",
        created_at="2023-02-01",
        event_time="2023-01-30",
    )
    mem.append_image(
        caption="Phone screenshot from Gina's arrival day showing Roma Termini station board.",
        ocr="Roma Termini 08:42 Platform 7",
        tags=["rome", "station", "arrival", "travel"],
        created_at="2023-02-01",
        event_time="2023-01-30",
        linked_subject="Gina",
    )
    mem.append_text(
        "Jon wanted the dance studio to look like a waterfront loft with natural light and Marley flooring.",
        created_at="2023-02-03",
        event_time="2023-02-03",
    )
    mem.append_image(
        caption="Moodboard screenshot for Jon's dream studio with sunlit windows and a waterfront interior.",
        ocr="waterfront loft natural light Marley floor",
        tags=["studio", "moodboard", "design", "waterfront"],
        created_at="2023-02-03",
        event_time="2023-02-03",
        linked_subject="Jon",
    )
    mem.extract_atoms()
    mem.build_memory()

    visual_q1_text_only = mem.search(
        "Which city appears in Gina's screenshot from her trip?",
        text_only=True,
    )
    visual_q1_multi = mem.search(
        "Which city appears in Gina's screenshot from her trip?",
        text_only=False,
    )
    visual_q2_text_only = mem.search(
        "What time was visible in Gina's screenshot when she arrived?",
        text_only=True,
    )
    visual_q2_multi = mem.search(
        "What time was visible in Gina's screenshot when she arrived?",
        text_only=False,
    )
    style_q_multi = mem.search(
        "What did Jon want the studio to look like?",
        text_only=False,
    )

    summary = {
        "visual_city_query_text_only_top1_type": (
            visual_q1_text_only["hits"][0]["node_type"] if visual_q1_text_only["hits"] else "none"
        ),
        "visual_city_query_multimodal_top1_type": (
            visual_q1_multi["hits"][0]["node_type"] if visual_q1_multi["hits"] else "none"
        ),
        "visual_time_query_text_only_has_ocr": any(
            "08:42" in hit["content"] for hit in visual_q2_text_only["hits"]
        ),
        "visual_time_query_multimodal_has_ocr": any(
            "08:42" in hit["content"] for hit in visual_q2_multi["hits"]
        ),
    }

    return {
        "memory_dump": mem.dump(),
        "queries": {
            "visual_city_text_only": visual_q1_text_only,
            "visual_city_multimodal": visual_q1_multi,
            "visual_time_text_only": visual_q2_text_only,
            "visual_time_multimodal": visual_q2_multi,
            "style_multimodal": style_q_multi,
        },
        "summary": summary,
    }


if __name__ == "__main__":
    result = demo()
    out = Path(__file__).with_name("nano_multimodal_demo_output.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
