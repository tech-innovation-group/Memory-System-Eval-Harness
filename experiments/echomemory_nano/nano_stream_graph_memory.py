#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    turn_id: str
    role: str
    text: str
    created_at: str = ""


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


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    source_atom_id: str = ""
    event_time: str = ""


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


class EchoMemoryNanoV2:
    """
    A slightly richer nano version for understanding the core EchoMemory ideas:

    1. append_turn          -> session stream
    2. extract_atoms        -> atomic facts/events/preferences
    3. build_memory_planes  -> fact/event/entity nodes + lightweight edges
    4. plan_query           -> a tiny recall planner
    5. search               -> route query to the right layers

    It is still intentionally tiny:
    - regex extraction instead of LLM extraction
    - lexical scoring instead of embeddings
    - no background workers, no persistence, no benchmark glue
    """

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.atoms: list[Atom] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []

    def append_turn(self, role: str, text: str, created_at: str = "") -> None:
        self.turns.append(
            Turn(
                turn_id=f"turn-{len(self.turns):03d}",
                role=role,
                text=text,
                created_at=created_at,
            )
        )

    def extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        patterns: list[tuple[str, str, str, str, str]] = [
            (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)", "event", "{0}", "visited", "{1}"),
            (r"([A-Z][a-z]+)\s+likes\s+(.+)", "preference", "{0}", "likes", "{1}"),
            (r"([A-Z][a-z]+)\s+lost\s+his job", "event", "{0}", "lost_job", "job"),
            (r"([A-Z][a-z]+)\s+got accepted.*internship", "event", "{0}", "accepted", "internship"),
            (r"([A-Z][a-z]+)'s ideal .* is (.+)", "fact", "{0}", "ideal", "{1}"),
            (r"([A-Z][a-z]+)\s+married\s+([A-Z][a-z]+)", "event", "{0}", "married", "{1}"),
            (r"([A-Z][a-z]+)\s+moved to\s+([A-Z][a-z]+)", "event", "{0}", "moved_to", "{1}"),
        ]

        for idx, turn in enumerate(self.turns):
            text = turn.text.strip()
            if not text:
                continue

            event_time = self._extract_date(text) or turn.created_at
            matched = False
            for pat, atom_type, subj_t, pred_t, obj_t in patterns:
                match = re.search(pat, text, re.I)
                if not match:
                    continue
                groups = match.groups()
                atoms.append(
                    Atom(
                        atom_id=f"atom-{idx:03d}-{len(atoms):02d}",
                        atom_type=atom_type,
                        subject=subj_t.format(*groups).strip(),
                        predicate=pred_t.format(*groups).strip(),
                        object=obj_t.format(*groups).strip(),
                        statement=text,
                        event_time=event_time,
                        created_at=turn.created_at,
                    )
                )
                matched = True
                break

            if not matched:
                atoms.append(
                    Atom(
                        atom_id=f"atom-{idx:03d}-{len(atoms):02d}",
                        atom_type="fact",
                        subject="unknown",
                        predicate="mentions",
                        object=text[:64],
                        statement=text,
                        event_time=event_time,
                        created_at=turn.created_at,
                    )
                )

        self.atoms = atoms
        return atoms

    def build_memory_planes(self) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen_entities: set[str] = set()
        event_node_ids: list[tuple[str, str]] = []

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(
                Node(
                    node_id=fact_id,
                    node_type="fact",
                    source_atom_id=atom.atom_id,
                    event_time=atom.event_time,
                    content=(
                        f"statement={atom.statement}\n"
                        f"subject={atom.subject}\n"
                        f"predicate={atom.predicate}\n"
                        f"object={atom.object}\n"
                        f"event_time={atom.event_time}\n"
                        f"created_at={atom.created_at}"
                    ).strip(),
                )
            )

            event_id = ""
            if atom.atom_type == "event" or atom.event_time:
                event_id = f"event:{atom.atom_id}"
                nodes.append(
                    Node(
                        node_id=event_id,
                        node_type="event",
                        source_atom_id=atom.atom_id,
                        event_time=atom.event_time,
                        content=(
                            f"statement={atom.statement}\n"
                            f"participants={atom.subject}, {atom.object}\n"
                            f"event_time={atom.event_time}"
                        ).strip(),
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
                event_node_ids.append((event_id, atom.event_time or ""))

            for ent in [atom.subject, atom.object]:
                if not self._looks_like_entity(ent):
                    continue
                entity_id = f"entity:{ent}"
                if ent not in seen_entities:
                    seen_entities.add(ent)
                    nodes.append(
                        Node(
                            node_id=entity_id,
                            node_type="entity",
                            source_atom_id=atom.atom_id,
                            content=f"name={ent}\nfirst_seen_in={atom.statement}",
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
                if event_id:
                    edges.append(
                        Edge(
                            edge_id=f"{event_id}:involves:{ent}",
                            source_id=event_id,
                            target_id=entity_id,
                            relation_type="involves",
                        )
                    )

        sorted_events = [item for item in event_node_ids if item[1]]
        sorted_events.sort(key=lambda item: item[1])
        for left, right in zip(sorted_events, sorted_events[1:]):
            edges.append(
                Edge(
                    edge_id=f"{left[0]}:temporal_next:{right[0]}",
                    source_id=left[0],
                    target_id=right[0],
                    relation_type="temporal_next",
                )
            )

        self.nodes = nodes
        self.edges = edges
        return nodes, edges

    def plan_query(self, query: str) -> QueryPlan:
        q = query.strip().lower()
        if re.search(r"\bwhen\b|什么时候|时间|日期|哪天|何时|后来|之前|之后", q, re.I):
            return QueryPlan(
                intent="temporal",
                target_layers=["event", "fact"],
                notes="Prefer event nodes because they carry explicit event_time.",
            )
        if re.search(r"\bwho\b|谁|关系|一起|共同|both|shared", q, re.I):
            return QueryPlan(
                intent="relational",
                target_layers=["entity", "event", "fact"],
                notes="Prefer entity/event paths because relation questions are rarely answerable from summaries alone.",
            )
        if re.search(r"\blike|ideal|prefer|favorite\b|喜欢|偏好|理想", q, re.I):
            return QueryPlan(
                intent="profile",
                target_layers=["fact", "entity"],
                notes="Preference/profile queries can usually stay on fact/entity layers.",
            )
        return QueryPlan(
            intent="general",
            target_layers=["fact", "event", "entity"],
            notes="Use broad retrieval when intent is unclear.",
        )

    def search(self, query: str, top_k: int = 5) -> dict[str, Any]:
        plan = self.plan_query(query)
        q_tokens = set(self._tokens(query))
        scored_nodes: list[tuple[float, Node]] = []
        edge_bonus: dict[str, float] = {}

        for edge in self.edges:
            if edge.relation_type in {"involves", "has_fact", "evidence_of", "temporal_next"}:
                edge_bonus[edge.source_id] = edge_bonus.get(edge.source_id, 0.0) + 0.15
                edge_bonus[edge.target_id] = edge_bonus.get(edge.target_id, 0.0) + 0.1

        for node in self.nodes:
            if node.node_type not in plan.target_layers:
                continue
            n_tokens = set(self._tokens(node.content))
            overlap = len(q_tokens & n_tokens)
            if overlap <= 0:
                continue

            score = float(overlap)
            score += edge_bonus.get(node.node_id, 0.0)
            if plan.intent == "temporal" and node.node_type == "event":
                score += 1.4
            if plan.intent == "relational" and node.node_type in {"entity", "event"}:
                score += 0.8
            if plan.intent == "profile" and node.node_type == "fact":
                score += 0.6
            scored_nodes.append((score, node))

        scored_nodes.sort(key=lambda item: item[0], reverse=True)
        return {
            "query": query,
            "plan": asdict(plan),
            "hits": [
                {
                    "score": round(score, 3),
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "event_time": node.event_time,
                    "content": node.content,
                }
                for score, node in scored_nodes[:top_k]
            ],
        }

    def dump(self) -> dict[str, Any]:
        return {
            "turns": [asdict(item) for item in self.turns],
            "atoms": [asdict(item) for item in self.atoms],
            "nodes": [asdict(item) for item in self.nodes],
            "edges": [asdict(item) for item in self.edges],
        }

    @staticmethod
    def _extract_date(text: str) -> str:
        match = re.search(r"\b(20\d{2}[-/]\d{2}[-/]\d{2})\b", text)
        return match.group(1).replace("/", "-") if match else ""

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", str(text).lower())

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
    mem = EchoMemoryNanoV2()
    mem.append_turn("user", "Jon lost his job as a banker on 2023-01-19 and decided to start a dance studio.", "2023-01-20")
    mem.append_turn("user", "Jon visited Paris on 2023-01-28 and said it was cool.", "2023-01-28")
    mem.append_turn("user", "Gina visited Rome on 2023-01-30 after her design interview.", "2023-01-30")
    mem.append_turn("user", "Jon's ideal dance studio is by the water, with natural light and Marley flooring.", "2023-02-03")
    mem.extract_atoms()
    mem.build_memory_planes()
    return {
        "memory_dump": mem.dump(),
        "temporal_query": mem.search("When did Jon lose his job?"),
        "relational_query": mem.search("Which city have both Gina and Jon visited?"),
        "profile_query": mem.search("What does Jon think the ideal dance studio should look like?"),
    }


if __name__ == "__main__":
    result = demo()
    out = Path(__file__).with_name("nano_stream_graph_demo_output.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
