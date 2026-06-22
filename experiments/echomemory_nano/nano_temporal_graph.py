#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    role: str
    text: str
    time: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    event_time: str = ""


@dataclass
class GraphNode:
    node_id: str
    node_type: str
    content: str
    source_atom_id: str = ""


class EchoMemoryNano:
    """
    一个极简版 EchoMemory，故意只保留四个动作：

    1. append_turn       把对话写进 session stream
    2. extract_atoms     从 turn 里抽 atom
    3. build_graph       把 atom 投影成 fact / event / entity 节点
    4. search            对节点做一个很朴素的 lexical retrieval

    目的不是拟真生产系统，而是让人 5 分钟看懂：
    session -> atoms -> temporal graph -> retrieval
    """

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.atoms: list[Atom] = []
        self.graph_nodes: list[GraphNode] = []

    def append_turn(self, role: str, text: str, time: str = "") -> None:
        self.turns.append(Turn(role=role, text=text, time=time))

    def extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for idx, turn in enumerate(self.turns):
            text = turn.text.strip()
            if not text:
                continue

            event_time = turn.time or self._extract_date(text)

            # 很简单的模式：X visited Y / X likes Y / X lost job / X accepted internship
            patterns = [
                (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)", "event", "{0}", "visited", "{1}"),
                (r"([A-Z][a-z]+)\s+likes\s+(.+)", "preference", "{0}", "likes", "{1}"),
                (r"([A-Z][a-z]+)\s+lost\s+his job", "event", "{0}", "lost_job", "job"),
                (r"([A-Z][a-z]+)\s+got accepted.*internship", "event", "{0}", "accepted", "internship"),
                (r"([A-Z][a-z]+)'s ideal .* is (.+)", "fact", "{0}", "ideal", "{1}"),
            ]

            matched = False
            for pat, atom_type, subj_t, pred_t, obj_t in patterns:
                m = re.search(pat, text, re.I)
                if not m:
                    continue
                groups = m.groups()
                subject = subj_t.format(*groups).strip()
                predicate = pred_t.format(*groups).strip()
                obj = obj_t.format(*groups).strip()
                atoms.append(
                    Atom(
                        atom_id=f"atom-{idx:03d}-{len(atoms):02d}",
                        atom_type=atom_type,
                        subject=subject,
                        predicate=predicate,
                        object=obj,
                        statement=text,
                        event_time=event_time,
                    )
                )
                matched = True
                break

            if not matched:
                # fallback：把整句当 fact
                atoms.append(
                    Atom(
                        atom_id=f"atom-{idx:03d}-{len(atoms):02d}",
                        atom_type="fact",
                        subject="unknown",
                        predicate="mentions",
                        object=text[:48],
                        statement=text,
                        event_time=event_time,
                    )
                )

        self.atoms = atoms
        return atoms

    def build_graph(self) -> list[GraphNode]:
        nodes: list[GraphNode] = []
        seen_entities: set[str] = set()

        for atom in self.atoms:
            # 1. fact node
            nodes.append(
                GraphNode(
                    node_id=f"fact:{atom.atom_id}",
                    node_type="fact",
                    source_atom_id=atom.atom_id,
                    content=(
                        f"{atom.statement}\n"
                        f"subject={atom.subject}\n"
                        f"predicate={atom.predicate}\n"
                        f"object={atom.object}\n"
                        f"event_time={atom.event_time}"
                    ).strip(),
                )
            )

            # 2. event node
            if atom.atom_type == "event" or atom.event_time:
                nodes.append(
                    GraphNode(
                        node_id=f"event:{atom.atom_id}",
                        node_type="event",
                        source_atom_id=atom.atom_id,
                        content=(
                            f"{atom.statement}\n"
                            f"event_time={atom.event_time}\n"
                            f"participants={atom.subject}, {atom.object}"
                        ).strip(),
                    )
                )

            # 3. entity nodes
            for ent in [atom.subject, atom.object]:
                if not self._looks_like_entity(ent):
                    continue
                if ent in seen_entities:
                    continue
                seen_entities.add(ent)
                nodes.append(
                    GraphNode(
                        node_id=f"entity:{ent}",
                        node_type="entity",
                        source_atom_id=atom.atom_id,
                        content=f"name={ent}\nfirst_seen_in={atom.statement}",
                    )
                )

        self.graph_nodes = nodes
        return nodes

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        scored: list[tuple[float, GraphNode]] = []
        q_tokens = set(self._tokens(query))
        temporal = bool(re.search(r"\bwhen\b|什么时候|时间|日期", query, re.I))

        for node in self.graph_nodes:
            n_tokens = set(self._tokens(node.content))
            overlap = len(q_tokens & n_tokens)
            score = float(overlap)

            if temporal and node.node_type == "event":
                score += 1.5
            elif temporal and node.node_type == "fact":
                score += 0.8

            if score > 0:
                scored.append((score, node))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "score": score,
                "node_id": node.node_id,
                "node_type": node.node_type,
                "content": node.content,
            }
            for score, node in scored[:top_k]
        ]

    def dump(self) -> dict[str, Any]:
        return {
            "turns": [asdict(item) for item in self.turns],
            "atoms": [asdict(item) for item in self.atoms],
            "graph_nodes": [asdict(item) for item in self.graph_nodes],
        }

    @staticmethod
    def _extract_date(text: str) -> str:
        m = re.search(r"\b(20\d{{2}}[-/]\d{{2}}[-/]\d{{2}})\b", text)
        return m.group(1) if m else ""

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
    mem = EchoMemoryNano()
    mem.append_turn("user", "Jon lost his job as a banker on 2023-01-19 and decided to start a dance studio.", "2023-01-20")
    mem.append_turn("user", "Jon visited Paris on 2023-01-28 and said it was cool.", "2023-01-28")
    mem.append_turn("user", "Gina has been to Rome once.", "2023-02-01")
    mem.append_turn("user", "Jon's ideal dance studio is by the water, with natural light and Marley flooring.", "2023-02-03")
    mem.extract_atoms()
    mem.build_graph()
    return {
        "memory_dump": mem.dump(),
        "search_when_job_lost": mem.search("When did Jon lose his job as a banker?"),
        "search_ideal_studio": mem.search("What does Jon think the ideal dance studio should look like?"),
        "search_both_city": mem.search("Which city have both Gina and Jon visited?"),
    }


if __name__ == "__main__":
    result = demo()
    out = Path(__file__).with_name("nano_demo_output.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
