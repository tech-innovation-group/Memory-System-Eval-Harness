#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_whiteboard_echomemory_output.json")


@dataclass
class Message:
    role: str
    text: str
    created_at: str


@dataclass
class Atom:
    kind: str
    subject: str
    predicate: str
    obj: str
    story_time: str
    mention_time: str
    statement: str


@dataclass
class TreeBlock:
    key: str
    lines: list[str] = field(default_factory=list)


@dataclass
class GraphEdge:
    source: str
    relation: str
    target: str


class WhiteboardEchoMemory:
    """
    The shortest "explain it on a whiteboard" version of EchoMemory.

    It keeps only five ideas:
    1. append-only messages
    2. extract atoms from user messages
    3. build a temporal tree
    4. build a relation graph
    5. route queries to tree-first or graph-first
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.atoms: list[Atom] = []
        self.tree: dict[str, TreeBlock] = {}
        self.graph_edges: list[GraphEdge] = []

    def append(self, role: str, text: str, created_at: str) -> None:
        self.messages.append(Message(role=role, text=text.strip(), created_at=created_at))

    def commit(self) -> None:
        self.atoms = []
        self.tree = {}
        self.graph_edges = []
        for msg in self.messages:
            if msg.role != "user":
                continue
            self.atoms.extend(self._extract_atoms(msg))
        self._build_tree()
        self._build_graph()

    def plan(self, query: str) -> str:
        if re.search(r"什么时候|哪天|日期|时间|before|after|之前|之后", query, re.I):
            return "tree-first"
        if re.search(r"谁|关系|介绍|帮|合作|married|relationship|who", query, re.I):
            return "graph-first"
        return "mixed"

    def search(self, query: str) -> dict:
        plan = self.plan(query)
        if plan == "tree-first":
            hits = self._search_tree(query) + self._search_graph(query, bonus=0.05)
        elif plan == "graph-first":
            hits = self._search_graph(query) + self._search_tree(query, bonus=0.05)
        else:
            hits = self._search_tree(query, bonus=0.02) + self._search_graph(query, bonus=0.02)
        hits.sort(key=lambda x: x["score"], reverse=True)
        return {"query": query, "plan": plan, "hits": hits[:5]}

    def _extract_atoms(self, msg: Message) -> list[Atom]:
        atoms: list[Atom] = []
        patterns = [
            (r"(.+?)于(\d{4}-\d{2}-\d{2})加入了(.+)", "event", "joined"),
            (r"(.+?)于(\d{4}-\d{2}-\d{2})离开了(.+)", "event", "left"),
            (r"(.+?)于(\d{4}-\d{2}-\d{2})和(.+?)结婚", "relation", "married_to"),
            (r"(.+?)于(\d{4}-\d{2}-\d{2})帮助了(.+?)准备签证", "relation", "helped"),
            (r"(.+?)在(\d{4}-\d{2}-\d{2})签了(.+)", "event", "signed"),
        ]
        for pattern, kind, predicate in patterns:
            m = re.search(pattern, msg.text)
            if not m:
                continue
            if predicate == "married_to":
                subject, story_time, obj = m.group(1), m.group(2), m.group(3)
            else:
                subject, story_time, obj = m.group(1), m.group(2), m.group(3)
            atoms.append(
                Atom(
                    kind=kind,
                    subject=subject.strip("。 "),
                    predicate=predicate,
                    obj=obj.strip("。 "),
                    story_time=story_time,
                    mention_time=msg.created_at,
                    statement=msg.text,
                )
            )
        return atoms

    def _build_tree(self) -> None:
        for atom in self.atoms:
            month_key = atom.story_time[:7]
            block = self.tree.setdefault(month_key, TreeBlock(key=month_key))
            block.lines.append(f"{atom.story_time}: {atom.statement}")

    def _build_graph(self) -> None:
        for atom in self.atoms:
            self.graph_edges.append(GraphEdge(source=atom.subject, relation=atom.predicate, target=atom.obj))
            self.graph_edges.append(GraphEdge(source=atom.subject, relation="has_fact", target=atom.statement))
            if atom.kind == "event":
                self.graph_edges.append(GraphEdge(source=atom.statement, relation="happened_at", target=atom.story_time))

    def _search_tree(self, query: str, bonus: float = 0.0) -> list[dict]:
        hits: list[dict] = []
        for key, block in self.tree.items():
            content = "\n".join(block.lines)
            score = self._overlap_score(query, content) + bonus
            if score > 0:
                hits.append({"layer": "tree", "source": key, "score": round(score, 3), "content": content})
        return hits

    def _search_graph(self, query: str, bonus: float = 0.0) -> list[dict]:
        hits: list[dict] = []
        for edge in self.graph_edges:
            content = f"{edge.source} --{edge.relation}--> {edge.target}"
            score = self._overlap_score(query, content) + bonus
            if score > 0:
                hits.append({"layer": "graph", "source": edge.source, "score": round(score, 3), "content": content})
        return hits

    @staticmethod
    def _overlap_score(query: str, content: str) -> float:
        q_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,4}|[a-zA-Z]+|\d{4}-\d{2}-\d{2}", query.lower()))
        c_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,4}|[a-zA-Z]+|\d{4}-\d{2}-\d{2}", content.lower()))
        if not q_tokens:
            return 0.0
        overlap = len(q_tokens & c_tokens)
        return overlap / max(1, len(q_tokens))


def demo() -> dict:
    mem = WhiteboardEchoMemory()
    mem.append("user", "Gina于2025-03-12加入了Figma。", "2025-03-12T10:00:00Z")
    mem.append("user", "Nora于2025-04-08帮助了Gina准备签证。", "2025-04-08T09:00:00Z")
    mem.append("user", "Gina于2025-05-01离开了Figma。", "2025-05-01T18:00:00Z")
    mem.append("user", "Gina在2025-05-03签了Lisbon lease。", "2025-05-03T14:00:00Z")
    mem.commit()
    return {
        "messages": [asdict(m) for m in mem.messages],
        "atoms": [asdict(a) for a in mem.atoms],
        "tree": {k: asdict(v) for k, v in mem.tree.items()},
        "graph_edges": [asdict(e) for e in mem.graph_edges],
        "queries": [
            mem.search("Gina什么时候离开Figma？"),
            mem.search("谁帮助了Gina准备签证？"),
            mem.search("Gina签租约之前发生了什么？"),
        ],
    }


if __name__ == "__main__":
    data = demo()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_JSON}")
