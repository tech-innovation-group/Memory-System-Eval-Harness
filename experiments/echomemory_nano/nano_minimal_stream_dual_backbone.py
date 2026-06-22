#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


OUT_JSON = Path(
    "/Users/chx/locomo-eval-web/experiments/echomemory_nano/"
    "nano_minimal_stream_dual_backbone_output.json"
)


@dataclass
class Turn:
    role: str
    text: str
    created_at: str


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    obj: str
    statement: str
    created_at: str
    mention_time: str
    event_time: str


@dataclass
class TemporalBlock:
    key: str
    lines: list[str] = field(default_factory=list)


@dataclass
class GraphEdge:
    source: str
    relation: str
    target: str
    evidence: str


@dataclass
class Readiness:
    messages_persisted: bool = False
    atoms_ready: bool = False
    tree_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False


class MinimalStreamDualBackbone:
    """
    A smallest useful EchoMemory-style nano:

    stream -> atoms -> temporal tree + relation graph -> planner-routed retrieval.

    It keeps only six concepts:
    1. append-only stream
    2. atom extraction
    3. three-clock time (created / mention / event)
    4. temporal tree
    5. relation graph
    6. readiness gate + query planner
    """

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.atoms: list[Atom] = []
        self.tree: dict[str, TemporalBlock] = {}
        self.graph: list[GraphEdge] = []
        self.readiness = Readiness()

    def append_turn(self, role: str, text: str, created_at: str) -> None:
        self.turns.append(Turn(role=role, text=text.strip(), created_at=created_at))
        self.readiness.messages_persisted = True
        self.readiness.atoms_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    def run_hot_path(self) -> None:
        self.atoms = []
        for turn in self.turns:
            if turn.role != "user":
                continue
            self.atoms.extend(self._extract_atoms(turn))
        self.readiness.atoms_ready = True

    def run_cold_path(self) -> None:
        self.tree = {}
        self.graph = []
        for atom in self.atoms:
            month_key = (atom.event_time or atom.created_at)[:7]
            block = self.tree.setdefault(month_key, TemporalBlock(key=month_key))
            block.lines.append(f"{atom.event_time or atom.created_at}: {atom.statement}")

            self.graph.append(
                GraphEdge(
                    source=atom.subject,
                    relation=atom.predicate,
                    target=atom.obj,
                    evidence=atom.statement,
                )
            )
            self.graph.append(
                GraphEdge(
                    source=atom.subject,
                    relation="has_fact",
                    target=atom.statement,
                    evidence=atom.atom_id,
                )
            )
            if atom.event_time:
                self.graph.append(
                    GraphEdge(
                        source=atom.statement,
                        relation="happened_at",
                        target=atom.event_time,
                        evidence=atom.atom_id,
                    )
                )

        self.readiness.tree_ready = True
        self.readiness.graph_ready = True
        self.readiness.qa_ready = True

    def commit(self) -> None:
        self.run_hot_path()
        self.run_cold_path()

    def plan(self, query: str) -> dict[str, object]:
        if re.search(r"什么时候|哪天|日期|时间|昨天|上周|before|after|when|date|time", query, re.I):
            return {
                "family": "temporal",
                "primary": "tree",
                "support": "graph",
                "reason": "time-oriented query prefers chronology backbone",
            }
        if re.search(r"谁|关系|介绍|帮助|合作|married|relationship|who|helped", query, re.I):
            return {
                "family": "relational",
                "primary": "graph",
                "support": "tree",
                "reason": "relation-oriented query prefers graph backbone",
            }
        return {
            "family": "general",
            "primary": "mixed",
            "support": "mixed",
            "reason": "general factual query can use both backbones",
        }

    def search(self, query: str) -> dict[str, object]:
        plan = self.plan(query)
        if not self.readiness.qa_ready:
            return {
                "query": query,
                "plan": plan,
                "allowed_to_answer": False,
                "note": "memory is persisted but not QA-ready yet",
                "hits": [],
            }

        if plan["primary"] == "tree":
            hits = self._search_tree(query, bonus=0.08) + self._search_graph(query, bonus=0.02)
        elif plan["primary"] == "graph":
            hits = self._search_graph(query, bonus=0.08) + self._search_tree(query, bonus=0.02)
        else:
            hits = self._search_tree(query, bonus=0.04) + self._search_graph(query, bonus=0.04)
        hits.sort(key=lambda item: item["score"], reverse=True)
        return {
            "query": query,
            "plan": plan,
            "allowed_to_answer": True,
            "note": "answer-time evidence should come from the primary backbone first",
            "hits": hits[:6],
        }

    def _extract_atoms(self, turn: Turn) -> list[Atom]:
        text = turn.text
        atoms: list[Atom] = []

        patterns = [
            (r"([A-Z][a-z]+)\s+joined\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "joined"),
            (r"([A-Z][a-z]+)\s+left\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "left"),
            (r"([A-Z][a-z]+)\s+married\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "married_to"),
            (r"([A-Z][a-z]+)\s+helped\s+([A-Z][a-z]+)\s+with\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "helped_with"),
            (r"([A-Z][a-z]+)\s+moved\s+to\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "moved_to"),
        ]

        for pattern, atom_type, predicate in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            groups = match.groups()
            if predicate == "helped_with":
                subject, obj, detail, event_time = groups
                target = f"{obj}::{detail}"
            else:
                subject, target, event_time = groups
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms):03d}",
                    atom_type=atom_type,
                    subject=subject,
                    predicate=predicate,
                    obj=target,
                    statement=text,
                    created_at=turn.created_at,
                    mention_time=turn.created_at,
                    event_time=event_time,
                )
            )

        if not atoms:
            relative_time = self._resolve_relative_time(text, turn.created_at)
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(self.atoms):03d}",
                    atom_type="fact",
                    subject="unknown",
                    predicate="mentions",
                    obj=text[:48],
                    statement=text,
                    created_at=turn.created_at,
                    mention_time=turn.created_at,
                    event_time=relative_time,
                )
            )
        return atoms

    @staticmethod
    def _resolve_relative_time(text: str, created_at: str) -> str:
        anchor = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        lowered = text.lower()
        if "yesterday" in lowered:
            return (anchor - timedelta(days=1)).strftime("%Y-%m-%d")
        if "last week" in lowered:
            return (anchor - timedelta(days=7)).strftime("%Y-%m-%d")
        match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if match:
            return match.group(1)
        return anchor.strftime("%Y-%m-%d")

    @staticmethod
    def _score(query: str, content: str) -> float:
        q_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,4}|[a-zA-Z_]+|\d{4}-\d{2}-\d{2}", query.lower()))
        c_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,4}|[a-zA-Z_]+|\d{4}-\d{2}-\d{2}", content.lower()))
        if not q_tokens:
            return 0.0
        overlap = len(q_tokens & c_tokens)
        return overlap / max(1, len(q_tokens))

    def _search_tree(self, query: str, bonus: float = 0.0) -> list[dict[str, object]]:
        hits: list[dict[str, object]] = []
        for key, block in self.tree.items():
            content = "\n".join(block.lines)
            score = self._score(query, content) + bonus
            if score > bonus:
                hits.append(
                    {
                        "layer": "temporal_tree",
                        "source": key,
                        "score": round(score, 3),
                        "content": content,
                    }
                )
        return hits

    def _search_graph(self, query: str, bonus: float = 0.0) -> list[dict[str, object]]:
        hits: list[dict[str, object]] = []
        for edge in self.graph:
            content = f"{edge.source} --{edge.relation}--> {edge.target} | {edge.evidence}"
            score = self._score(query, content) + bonus
            if score > bonus:
                hits.append(
                    {
                        "layer": "relation_graph",
                        "source": edge.source,
                        "score": round(score, 3),
                        "content": content,
                    }
                )
        return hits


def demo() -> dict[str, object]:
    mem = MinimalStreamDualBackbone()
    mem.append_turn("user", "Gina joined Figma on 2025-03-12.", "2025-03-12T10:00:00Z")
    mem.append_turn("user", "Nora helped Gina with visa prep on 2025-04-08.", "2025-04-08T09:00:00Z")
    mem.append_turn("user", "Gina left Figma on 2025-05-01.", "2025-05-01T18:00:00Z")
    mem.append_turn("user", "Gina moved to Lisbon on 2025-05-03.", "2025-05-03T14:00:00Z")
    mem.commit()
    return {
        "readiness": asdict(mem.readiness),
        "turns": [asdict(t) for t in mem.turns],
        "atoms": [asdict(a) for a in mem.atoms],
        "temporal_tree": {k: asdict(v) for k, v in mem.tree.items()},
        "graph": [asdict(edge) for edge in mem.graph],
        "queries": [
            mem.search("When did Gina leave Figma?"),
            mem.search("Who helped Gina with visa prep?"),
            mem.search("What happened before Gina moved to Lisbon?"),
        ],
    }


if __name__ == "__main__":
    payload = demo()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_JSON}")
