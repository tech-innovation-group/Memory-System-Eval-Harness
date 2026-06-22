#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    turn_id: str
    role: str
    text: str
    created_at: str


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    story_time: str = ""
    created_at: str = ""


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    source_atom_id: str
    story_time: str = ""


@dataclass
class Edge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class ReadinessState:
    messages_persisted: bool = False
    atoms_extracted: bool = False
    graph_built: bool = False
    qa_ready: bool = False


@dataclass
class QueryPlan:
    intent: str
    target_layers: list[str]
    notes: str = ""


@dataclass
class SearchResult:
    query: str
    plan: QueryPlan
    readiness: ReadinessState
    status: str
    hits: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""


class EchoMemoryReadinessTemporalNano:
    """
    一个更贴近 EchoMemory 当前痛点的 nano 版本：

    - hot path: append -> extract atoms
    - cold path: build graph / event / entity memory
    - readiness gate: QA 只有在 graph ready 后才放行
    - temporal normalize: 把 yesterday / two days ago / 上周五 这类词解析成 story time
    - graph-first retrieval: 时间题 / 关系题优先走 event / entity / relation

    这个文件不是为了拟真生产系统，而是为了直观解释：
    为什么 “写入成功” 不等于 “QA ready”，以及为什么时间和图结构应该是主流程。
    """

    def __init__(
        self,
        *,
        temporal_normalize: bool,
        graph_first: bool,
        readiness_gate: bool,
    ) -> None:
        self.temporal_normalize = temporal_normalize
        self.graph_first = graph_first
        self.readiness_gate = readiness_gate
        self.turns: list[Turn] = []
        self.atoms: list[Atom] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.readiness = ReadinessState()

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def append_turn(self, role: str, text: str, created_at: str) -> None:
        self.turns.append(
            Turn(
                turn_id=f"turn-{len(self.turns):03d}",
                role=role,
                text=text,
                created_at=created_at,
            )
        )
        self.readiness.messages_persisted = True
        self.readiness.qa_ready = False

    def run_hot_path(self) -> None:
        self.atoms = []
        for idx, turn in enumerate(self.turns):
            self.atoms.extend(self._extract_turn_atoms(turn, idx))
        self.readiness.atoms_extracted = True
        self.readiness.qa_ready = False

    def run_cold_path(self) -> None:
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen_entities: set[str] = set()

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(
                Node(
                    node_id=fact_id,
                    node_type="fact",
                    source_atom_id=atom.atom_id,
                    story_time=atom.story_time,
                    content=(
                        f"statement={atom.statement}\n"
                        f"subject={atom.subject}\n"
                        f"predicate={atom.predicate}\n"
                        f"object={atom.object}\n"
                        f"story_time={atom.story_time}\n"
                        f"created_at={atom.created_at}"
                    ),
                )
            )

            if atom.atom_type in {"event", "relation"} or atom.story_time:
                event_id = f"event:{atom.atom_id}"
                nodes.append(
                    Node(
                        node_id=event_id,
                        node_type="event",
                        source_atom_id=atom.atom_id,
                        story_time=atom.story_time,
                        content=(
                            f"statement={atom.statement}\n"
                            f"story_time={atom.story_time}\n"
                            f"participants={atom.subject}, {atom.object}"
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
            else:
                event_id = ""

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
                            story_time=atom.story_time,
                            content=f"name={ent}\nfirst_seen={atom.statement}",
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

            if atom.atom_type == "relation":
                relation_id = f"relation:{atom.atom_id}"
                nodes.append(
                    Node(
                        node_id=relation_id,
                        node_type="relation",
                        source_atom_id=atom.atom_id,
                        story_time=atom.story_time,
                        content=(
                            f"relation={atom.subject} {atom.predicate} {atom.object}\n"
                            f"statement={atom.statement}\n"
                            f"story_time={atom.story_time}"
                        ),
                    )
                )
                if self._looks_like_entity(atom.subject) and self._looks_like_entity(atom.object):
                    edges.append(
                        Edge(
                            edge_id=f"entity:{atom.subject}:relation:{atom.atom_id}",
                            source_id=f"entity:{atom.subject}",
                            target_id=relation_id,
                            relation_type="participates_in_relation",
                        )
                    )
                    edges.append(
                        Edge(
                            edge_id=f"entity:{atom.object}:relation:{atom.atom_id}",
                            source_id=f"entity:{atom.object}",
                            target_id=relation_id,
                            relation_type="participates_in_relation",
                        )
                    )

        event_nodes = [node for node in nodes if node.node_type == "event" and node.story_time]
        event_nodes.sort(key=lambda node: node.story_time)
        for left, right in zip(event_nodes, event_nodes[1:]):
            edges.append(
                Edge(
                    edge_id=f"{left.node_id}:temporal_next:{right.node_id}",
                    source_id=left.node_id,
                    target_id=right.node_id,
                    relation_type="temporal_next",
                )
            )

        self.nodes = nodes
        self.edges = edges
        self.readiness.graph_built = True
        self.readiness.qa_ready = True

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def plan_query(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"\bwhen\b|\bbefore\b|\bafter\b|什么时候|哪天|何时|时间|日期|昨天|前天|后来|之前|之后", q, re.I):
            return QueryPlan(
                intent="temporal",
                target_layers=["event", "fact"],
                notes="Prefer story-time carrying event nodes.",
            )
        if re.search(r"\bwho\b|谁|介绍|关系|一起|共同|联系", q, re.I):
            return QueryPlan(
                intent="relational",
                target_layers=["relation", "entity", "event", "fact"],
                notes="Prefer relation/entity path for people-company linkage questions.",
            )
        return QueryPlan(
            intent="general",
            target_layers=["fact", "event", "entity"],
            notes="Fallback retrieval order.",
        )

    def search(self, query: str) -> SearchResult:
        plan = self.plan_query(query)

        if self.readiness_gate and not self.readiness.qa_ready:
            return SearchResult(
                query=query,
                plan=plan,
                readiness=self.readiness,
                status="not_ready",
                note="Memory is persisted but not QA-ready yet; cold consolidation has not finished.",
            )

        if self.graph_first and self.nodes:
            hits = self._graph_search(query, plan)
            return SearchResult(
                query=query,
                plan=plan,
                readiness=self.readiness,
                status="ok",
                hits=hits,
            )

        hits = self._atom_flat_search(query)
        return SearchResult(
            query=query,
            plan=plan,
            readiness=self.readiness,
            status="ok",
            hits=hits,
        )

    def answer(self, query: str) -> dict[str, Any]:
        result = self.search(query)
        answer = "unknown"
        if result.status == "not_ready":
            answer = "not_ready"
        elif result.hits:
            top = result.hits[0]
            if result.plan.intent == "temporal":
                answer = top.get("story_time") or top.get("created_at") or "unknown"
            elif result.plan.intent == "relational":
                content = str(top.get("content", ""))
                m = re.search(r"relation=(.+)", content)
                answer = m.group(1).strip() if m else content.split("\n", 1)[0]
            else:
                answer = str(top.get("content", "")).split("\n", 1)[0]
        return {
            "query": query,
            "status": result.status,
            "answer": answer,
            "hits": result.hits,
            "plan": asdict(result.plan),
            "readiness": asdict(result.readiness),
            "note": result.note,
        }

    def dump(self) -> dict[str, Any]:
        return {
            "config": {
                "temporal_normalize": self.temporal_normalize,
                "graph_first": self.graph_first,
                "readiness_gate": self.readiness_gate,
            },
            "readiness": asdict(self.readiness),
            "turns": [asdict(turn) for turn in self.turns],
            "atoms": [asdict(atom) for atom in self.atoms],
            "nodes": [asdict(node) for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_turn_atoms(self, turn: Turn, idx: int) -> list[Atom]:
        text = turn.text.strip()
        if not text:
            return []
        story_time = self._resolve_story_time(text, turn.created_at)

        patterns: list[tuple[str, str, str, str, str]] = [
            (r"([A-Z][a-z]+)\s+lost\s+his job", "event", "{0}", "lost_job", "job"),
            (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)", "event", "{0}", "visited", "{1}"),
            (r"([A-Z][a-z]+)\s+met\s+([A-Z][a-z]+)", "event", "{0}", "met", "{1}"),
            (r"([A-Z][a-z]+)\s+introduced\s+([A-Z][a-z]+)\s+to\s+([A-Z][a-z]+)", "relation", "{0}", "introduced_to", "{1}->{2}"),
            (r"([A-Z][a-z]+)\s+joined\s+([A-Z][a-z]+)", "event", "{0}", "joined", "{1}"),
        ]

        for local_idx, (pat, atom_type, subj_t, pred_t, obj_t) in enumerate(patterns):
            match = re.search(pat, text, re.I)
            if not match:
                continue
            groups = match.groups()
            return [
                Atom(
                    atom_id=f"atom-{idx:03d}-{local_idx:02d}",
                    atom_type=atom_type,
                    subject=subj_t.format(*groups).strip(),
                    predicate=pred_t.format(*groups).strip(),
                    object=obj_t.format(*groups).strip(),
                    statement=text,
                    story_time=story_time,
                    created_at=turn.created_at,
                )
            ]

        return [
            Atom(
                atom_id=f"atom-{idx:03d}-99",
                atom_type="fact",
                subject="unknown",
                predicate="mentions",
                object=text[:80],
                statement=text,
                story_time=story_time,
                created_at=turn.created_at,
            )
        ]

    def _resolve_story_time(self, text: str, created_at: str) -> str:
        explicit = self._extract_iso_date(text)
        if explicit:
            return explicit
        if not self.temporal_normalize:
            return created_at

        created = self._parse_date(created_at)
        lowered = text.lower()

        if "yesterday" in lowered or "昨天" in text:
            return (created - timedelta(days=1)).isoformat()
        if "two days ago" in lowered or "前天" in text:
            return (created - timedelta(days=2)).isoformat()
        if "last week" in lowered or "上周" in text:
            return (created - timedelta(days=7)).isoformat()
        if "today" in lowered or "今天" in text:
            return created.isoformat()
        return created_at

    def _atom_flat_search(self, query: str) -> list[dict[str, Any]]:
        q_tokens = set(self._tokens(query))
        scored: list[tuple[float, Atom]] = []
        for atom in self.atoms:
            payload = (
                f"{atom.statement}\n{atom.subject}\n{atom.predicate}\n"
                f"{atom.object}\n{atom.story_time}\n{atom.created_at}"
            )
            overlap = len(q_tokens & set(self._tokens(payload)))
            if overlap <= 0:
                continue
            scored.append((float(overlap), atom))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "score": round(score, 3),
                "node_id": atom.atom_id,
                "node_type": atom.atom_type,
                "story_time": atom.story_time,
                "created_at": atom.created_at,
                "content": atom.statement,
            }
            for score, atom in scored[:5]
        ]

    def _graph_search(self, query: str, plan: QueryPlan) -> list[dict[str, Any]]:
        temporal_chain_hits = self._graph_temporal_neighbor_search(query, plan)
        if temporal_chain_hits:
            return temporal_chain_hits

        q_tokens = set(self._tokens(query))
        priority = {layer: idx for idx, layer in enumerate(plan.target_layers)}
        scored: list[tuple[int, float, Node]] = []
        for node in self.nodes:
            overlap = len(q_tokens & set(self._tokens(node.content)))
            if overlap <= 0:
                continue
            layer_rank = priority.get(node.node_type, 9)
            bonus = 0.0
            if plan.intent == "temporal" and node.node_type == "event":
                bonus += 2.0
            if plan.intent == "relational" and node.node_type in {"relation", "entity"}:
                bonus += 2.0
            score = float(overlap) + bonus
            scored.append((layer_rank, -score, node))
        scored.sort(key=lambda item: (item[0], item[1], item[2].node_id))
        return [
            {
                "score": round(-neg_score, 3),
                "node_id": node.node_id,
                "node_type": node.node_type,
                "story_time": node.story_time,
                "created_at": self._created_at_for_node(node),
                "content": node.content,
            }
            for _, neg_score, node in scored[:5]
        ]

    def _graph_temporal_neighbor_search(self, query: str, plan: QueryPlan) -> list[dict[str, Any]]:
        if plan.intent != "temporal":
            return []
        lowered = query.lower()
        want_before = "before" in lowered or "之前" in query
        want_after = "after" in lowered or "之后" in query or "later" in lowered
        if not (want_before or want_after):
            return []

        anchor = self._find_anchor_event(query)
        if anchor is None:
            return []

        target_edge_type = "temporal_next"
        if want_before:
            for edge in self.edges:
                if edge.relation_type != target_edge_type:
                    continue
                if edge.target_id == anchor.node_id:
                    previous = self._node_by_id(edge.source_id)
                    if previous is not None:
                        return [self._hit_from_node(previous, score=5.0)]
        if want_after:
            for edge in self.edges:
                if edge.relation_type != target_edge_type:
                    continue
                if edge.source_id == anchor.node_id:
                    nxt = self._node_by_id(edge.target_id)
                    if nxt is not None:
                        return [self._hit_from_node(nxt, score=5.0)]
        return []

    def _find_anchor_event(self, query: str) -> Node | None:
        q_tokens = set(self._tokens(query)) - {
            "before", "after", "what", "happened", "did", "when",
            "who", "the", "a", "an", "之前", "之后", "发生", "什么",
        }
        best: tuple[int, Node] | None = None
        for node in self.nodes:
            if node.node_type != "event":
                continue
            overlap = len(q_tokens & set(self._tokens(node.content)))
            if overlap <= 0:
                continue
            if best is None or overlap > best[0]:
                best = (overlap, node)
        return best[1] if best else None

    def _node_by_id(self, node_id: str) -> Node | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def _hit_from_node(self, node: Node, *, score: float) -> dict[str, Any]:
        return {
            "score": round(score, 3),
            "node_id": node.node_id,
            "node_type": node.node_type,
            "story_time": node.story_time,
            "created_at": self._created_at_for_node(node),
            "content": node.content,
        }

    def _created_at_for_node(self, node: Node) -> str:
        atom_id = node.source_atom_id
        for atom in self.atoms:
            if atom.atom_id == atom_id:
                return atom.created_at
        return ""

    @staticmethod
    def _extract_iso_date(text: str) -> str:
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        return match.group(1) if match else ""

    @staticmethod
    def _parse_date(raw: str) -> date:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", str(text).lower())

    @staticmethod
    def _looks_like_entity(value: str) -> bool:
        raw = str(value or "").strip()
        if not raw or len(raw) <= 2:
            return False
        if raw.replace("-", "").replace("/", "").isdigit():
            return False
        return True


def demo() -> dict[str, Any]:
    systems = {
        "baseline": EchoMemoryReadinessTemporalNano(
            temporal_normalize=False,
            graph_first=False,
            readiness_gate=False,
        ),
        "improved": EchoMemoryReadinessTemporalNano(
            temporal_normalize=True,
            graph_first=True,
            readiness_gate=True,
        ),
    }

    turns = [
        ("user", "Yesterday Jon lost his job at the bank.", "2025-05-10"),
        ("user", "Two days ago Gina visited Rome for a design fair.", "2025-05-15"),
        ("user", "Maya introduced Jon to Lena.", "2025-05-18"),
    ]

    for system in systems.values():
        for role, text, created_at in turns:
            system.append_turn(role, text, created_at)
        system.run_hot_path()

    before_cold = systems["improved"].answer("When did Jon lose his job?")
    for system in systems.values():
        system.run_cold_path()

    queries = [
        "When did Jon lose his job?",
        "When did Gina visit Rome?",
        "Who introduced Jon to Lena?",
    ]
    after = {
        name: {query: system.answer(query) for query in queries}
        for name, system in systems.items()
    }

    return {
        "turns": [
            {"role": role, "text": text, "created_at": created_at}
            for role, text, created_at in turns
        ],
        "before_cold_path": before_cold,
        "systems": {name: system.dump() for name, system in systems.items()},
        "answers_after_cold_path": after,
    }


if __name__ == "__main__":
    result = demo()
    out = Path(__file__).with_name("nano_readiness_temporal_graph_demo_output.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
