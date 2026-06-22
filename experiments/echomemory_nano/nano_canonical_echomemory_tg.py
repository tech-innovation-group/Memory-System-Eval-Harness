#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    turn_id: str
    role: str
    text: str
    created_at: str


@dataclass
class ReadinessState:
    messages_persisted: bool = False
    atoms_ready: bool = False
    graph_ready: bool = False
    organized_ready: bool = False
    qa_ready: bool = False


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    object: str
    statement: str
    mention_time: str
    event_time_start: str = ""
    event_time_end: str = ""
    time_confidence: float = 0.0


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    source_atom_id: str
    event_time: str = ""


@dataclass
class TemporalBlock:
    block_id: str
    level: str
    key: str
    content: str
    derived_from: list[str] = field(default_factory=list)


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
    graph_first: bool = False
    prefer_event: bool = False
    prefer_fact: bool = False
    notes: str = ""


@dataclass
class SearchHit:
    source: str
    layer: str
    score: float
    content: str


@dataclass
class SearchResult:
    query: str
    plan: QueryPlan
    readiness: ReadinessState
    allowed_to_answer: bool
    hits: list[SearchHit] = field(default_factory=list)
    answer_sketch: str = ""
    note: str = ""


class CanonicalEchoMemoryTG:
    """
    一个“最像当前 EchoMemory 主结构”的单文件 nano。

    它故意只保留 4 个核心问题：
    1. append-only message stream
    2. atom extraction with story-time normalization
    3. graph/organized projection
    4. query-planned retrieval with readiness gating

    这个脚本的目标不是高精度，而是帮助理解真实系统里：
    - 为什么要把 created_at 和 event_time 分开
    - 为什么 graph 不是装饰，而是 temporal / relation query 的主路径之一
    - 为什么 qa_ready 是系统正确性约束，而不是 UI 小开关
    """

    def __init__(self, *, enable_story_time: bool = True, enable_graph_first: bool = True) -> None:
        self.enable_story_time = enable_story_time
        self.enable_graph_first = enable_graph_first
        self.turns: list[Turn] = []
        self.atoms: list[Atom] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.temporal_blocks: list[TemporalBlock] = []
        self.organized_summaries: list[dict[str, str]] = []
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
        self.readiness.atoms_ready = False
        self.readiness.graph_ready = False
        self.readiness.organized_ready = False
        self.readiness.qa_ready = False

    def run_hot_path(self) -> None:
        self.atoms = []
        for turn in self.turns:
            if turn.role != "user":
                continue
            self.atoms.extend(self._extract_atoms(turn))
        self.readiness.atoms_ready = True
        self.readiness.qa_ready = False

    def run_cold_path(self) -> None:
        self.nodes = []
        self.edges = []
        self.temporal_blocks = []
        self.organized_summaries = []

        seen_entities: set[str] = set()
        event_nodes: list[Node] = []

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            fact_node = Node(
                node_id=fact_id,
                node_type="fact",
                source_atom_id=atom.atom_id,
                event_time=atom.event_time_start,
                content=(
                    f"statement={atom.statement}\n"
                    f"subject={atom.subject}\n"
                    f"predicate={atom.predicate}\n"
                    f"object={atom.object}\n"
                    f"mention_time={atom.mention_time}\n"
                    f"event_time={atom.event_time_start}\n"
                    f"time_confidence={atom.time_confidence:.2f}"
                ),
            )
            self.nodes.append(fact_node)

            if atom.atom_type in {"event", "relation"} or atom.event_time_start:
                event_id = f"event:{atom.atom_id}"
                event_node = Node(
                    node_id=event_id,
                    node_type="event",
                    source_atom_id=atom.atom_id,
                    event_time=atom.event_time_start,
                    content=(
                        f"{atom.subject} / {atom.predicate} / {atom.object}\n"
                        f"statement={atom.statement}\n"
                        f"event_time={atom.event_time_start}"
                    ),
                )
                self.nodes.append(event_node)
                event_nodes.append(event_node)
                self.edges.append(
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
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    self.nodes.append(
                        Node(
                            node_id=entity_id,
                            node_type="entity",
                            source_atom_id=atom.atom_id,
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

        event_nodes.sort(key=lambda n: n.event_time or "9999")
        for left, right in zip(event_nodes, event_nodes[1:]):
            self.edges.append(
                Edge(
                    edge_id=f"{left.node_id}:temporal_next:{right.node_id}",
                    source_id=left.node_id,
                    target_id=right.node_id,
                    relation_type="temporal_next",
                )
            )

        self._build_organized_summaries()
        self._build_temporal_blocks()
        self.readiness.graph_ready = True
        self.readiness.organized_ready = True
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
        if re.search(
            r"\bwhen\b|\bdate\b|\bhow long\b|\byesterday\b|\btoday\b|\blast week\b|\blast month\b|\brecently\b|\blately\b|"
            r"什么时候|哪天|时间|日期|多久|前后|后来|之前|昨天|前天|上周|上个月|最近",
            q,
            re.I,
        ):
            return QueryPlan(
                intent="temporal",
                target_layers=["temporal_tree", "event", "fact", "organized"],
                graph_first=self.enable_graph_first,
                prefer_event=True,
                notes="Temporal question: prefer anchored temporal tree and story-time carrying event nodes.",
            )
        if re.search(r"\bwho\b|\brelationship\b|\bconnect\b|谁|关系|联系|牵线|共同|相关", q, re.I):
            return QueryPlan(
                intent="relational",
                target_layers=["entity", "event", "fact", "organized"],
                graph_first=self.enable_graph_first,
                prefer_event=True,
                notes="Relational question: prefer entity/event graph route.",
            )
        if re.search(r"\bwhat\b|\bwhich\b|什么|哪些|哪家|哪一个", q, re.I):
            return QueryPlan(
                intent="factual",
                target_layers=["fact", "organized", "event"],
                prefer_fact=True,
                notes="General factual question: start from fact layer.",
            )
        return QueryPlan(
            intent="general",
            target_layers=["organized", "fact", "event"],
            notes="Fallback route.",
        )

    def search(self, query: str, *, query_time: str = "") -> SearchResult:
        plan = self.plan_query(query)
        readiness_snapshot = ReadinessState(**asdict(self.readiness))
        if not self.readiness.qa_ready:
            return SearchResult(
                query=query,
                plan=plan,
                readiness=readiness_snapshot,
                allowed_to_answer=False,
                note="qa_ready=false: messages are persisted, but memory is not fully consolidated yet.",
            )

        hits: list[SearchHit] = []
        query_terms = self._tokenize(query)

        if plan.graph_first:
            for layer in plan.target_layers:
                hits.extend(self._search_layer(layer, query_terms, query=query, query_time=query_time))
        else:
            hits.extend(self._search_layer("temporal_tree", query_terms, query=query, query_time=query_time))
            hits.extend(self._search_layer("fact", query_terms, query=query, query_time=query_time))
            hits.extend(self._search_layer("organized", query_terms, query=query, query_time=query_time))
            hits.extend(self._search_layer("event", query_terms, query=query, query_time=query_time))

        hits.sort(key=lambda h: h.score, reverse=True)
        hits = hits[:8]
        answer_sketch = self._answer_sketch(query, hits)
        return SearchResult(
            query=query,
            plan=plan,
            readiness=readiness_snapshot,
            allowed_to_answer=True,
            hits=hits,
            answer_sketch=answer_sketch,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_atoms(self, turn: Turn) -> list[Atom]:
        text = turn.text.strip()
        atoms: list[Atom] = []
        story_time = self._resolve_story_time(text, turn.created_at) if self.enable_story_time else turn.created_at[:10]

        pair_match = re.search(
            r"([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)\s+both lost their jobs and later started businesses",
            text,
            re.I,
        )
        if pair_match:
            person_a, person_b = pair_match.groups()
            return [
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms):03d}",
                    atom_type="relation",
                    subject=person_a,
                    predicate="shared_experience_with",
                    object=person_b,
                    statement=text,
                    mention_time=turn.created_at[:10],
                    event_time_start=story_time,
                    event_time_end=story_time,
                    time_confidence=0.95,
                ),
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms) + 1:03d}",
                    atom_type="relation",
                    subject=person_a,
                    predicate="shared_business_transition_with",
                    object=person_b,
                    statement=text,
                    mention_time=turn.created_at[:10],
                    event_time_start=story_time,
                    event_time_end=story_time,
                    time_confidence=0.95,
                ),
            ]

        patterns: list[tuple[str, str, str, str, str]] = [
            (r"([A-Z][a-z]+)\s+lost .* job .* yesterday", "event", "{0}", "lost_job", "job"),
            (r"([A-Z][a-z]+)\s+opened .* studio", "event", "{0}", "opened", "studio"),
            (r"([A-Z][a-z]+)\s+started learning marketing and analytics tools", "event", "{0}", "started_learning", "marketing_and_analytics_tools"),
            (r"([A-Z][a-z]+)\s+started expanding .* social media presence", "event", "{0}", "started_expanding_social_media", "studio_social_media"),
            (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)", "event", "{0}", "visited", "{1}"),
        ]

        for pattern, atom_type, subj_t, pred_t, obj_t in patterns:
            m = re.search(pattern, text, re.I)
            if not m:
                continue
            groups = m.groups()
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms):03d}",
                    atom_type=atom_type,
                    subject=subj_t.format(*groups),
                    predicate=pred_t.format(*groups),
                    object=obj_t.format(*groups),
                    statement=text,
                    mention_time=turn.created_at[:10],
                    event_time_start=story_time,
                    event_time_end=story_time,
                    time_confidence=0.95 if story_time else 0.30,
                )
            )

        if atoms:
            return atoms

        fallback_story_time = self._resolve_story_time(text, turn.created_at) if self.enable_story_time else turn.created_at[:10]
        return [
            Atom(
                atom_id=f"atom-{len(self.atoms):03d}",
                atom_type="fact",
                subject="unknown",
                predicate="mentions",
                object=text[:48],
                statement=text,
                mention_time=turn.created_at[:10],
                event_time_start=fallback_story_time,
                event_time_end=fallback_story_time,
                time_confidence=0.20 if fallback_story_time else 0.05,
            )
        ]

    def _resolve_story_time(self, text: str, created_at: str) -> str:
        anchor = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        lowered = text.lower()
        if "yesterday" in lowered or "昨天" in text:
            return (anchor - timedelta(days=1)).date().isoformat()
        if "two days ago" in lowered or "前天" in text:
            return (anchor - timedelta(days=2)).date().isoformat()
        if "last week" in lowered or "上周" in text:
            return (anchor - timedelta(days=7)).date().isoformat()
        month_match = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", lowered)
        if month_match:
            month_map = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }
            month = month_map[month_match.group(1)]
            return f"{anchor.year:04d}-{month:02d}"
        iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        if iso_match:
            return iso_match.group(1)
        return created_at[:10]

    def _build_organized_summaries(self) -> None:
        by_subject: dict[str, list[Atom]] = {}
        for atom in self.atoms:
            by_subject.setdefault(atom.subject, []).append(atom)
        summaries: list[dict[str, str]] = []
        for subject, items in by_subject.items():
            ordered = sorted(items, key=lambda a: a.event_time_start or a.mention_time)
            line = "; ".join(
                f"{a.event_time_start}: {a.subject} {a.predicate} {a.object}" for a in ordered[:4]
            )
            summaries.append(
                {
                    "summary_id": f"org:{subject}",
                    "subject": subject,
                    "content": line,
                }
            )
        self.organized_summaries = summaries

    def _build_temporal_blocks(self) -> None:
        buckets: dict[tuple[str, str], list[Atom]] = {}
        for atom in self.atoms:
            event_time = atom.event_time_start.strip()
            if not event_time:
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}$", event_time):
                yyyy, mm, dd = event_time.split("-")
            elif re.match(r"^\d{4}-\d{2}$", event_time):
                yyyy, mm = event_time.split("-")
                dd = "01"
            elif re.match(r"^\d{4}$", event_time):
                yyyy = event_time
                mm = "01"
                dd = "01"
            else:
                continue
            keys = {
                ("year", yyyy),
                ("month", f"{yyyy}-{mm}"),
                ("day", f"{yyyy}-{mm}-{dd}"),
            }
            for bucket in keys:
                buckets.setdefault(bucket, []).append(atom)

        blocks: list[TemporalBlock] = []
        for (level, key), atoms in sorted(buckets.items()):
            ordered = sorted(atoms, key=lambda a: a.event_time_start or a.mention_time)
            lines = [f"- {a.event_time_start}: {a.statement}" for a in ordered[:8]]
            blocks.append(
                TemporalBlock(
                    block_id=f"{level}:{key}",
                    level=level,
                    key=key,
                    content="\n".join(lines),
                    derived_from=[a.atom_id for a in ordered[:8]],
                )
            )
        self.temporal_blocks = blocks

    def _search_layer(self, layer: str, query_terms: set[str], *, query: str = "", query_time: str = "") -> list[SearchHit]:
        hits: list[SearchHit] = []
        if layer == "temporal_tree":
            keys = self._extract_temporal_keys(query, query_time=query_time)
            preferred_ids = {f"{level}:{key}" for level, values in keys.items() for key in values}
            for block in self.temporal_blocks:
                score = 0.0
                if block.block_id in preferred_ids:
                    score += 1.0 if block.level == "day" else 0.8 if block.level == "month" else 0.65
                score += self._overlap_score(query_terms, block.content)
                if score > 0:
                    hits.append(
                        SearchHit(
                            source=block.block_id,
                            layer="temporal_tree",
                            score=score,
                            content=f"level={block.level}\nkey={block.key}\n{block.content}",
                        )
                    )
        elif layer == "fact":
            for node in self.nodes:
                if node.node_type != "fact":
                    continue
                score = self._overlap_score(query_terms, node.content)
                if score > 0:
                    hits.append(SearchHit(node.node_id, "fact", score, node.content))
        elif layer == "event":
            for node in self.nodes:
                if node.node_type != "event":
                    continue
                score = self._overlap_score(query_terms, node.content) + (0.15 if node.event_time else 0.0)
                if score > 0:
                    hits.append(SearchHit(node.node_id, "event", score, node.content))
        elif layer == "entity":
            for node in self.nodes:
                if node.node_type != "entity":
                    continue
                score = self._overlap_score(query_terms, node.content)
                if score > 0:
                    hits.append(SearchHit(node.node_id, "entity", score, node.content))
        elif layer == "organized":
            for item in self.organized_summaries:
                score = self._overlap_score(query_terms, item["content"])
                if score > 0:
                    hits.append(SearchHit(item["summary_id"], "organized", score, item["content"]))
        return hits

    def _answer_sketch(self, query: str, hits: list[SearchHit]) -> str:
        if not hits:
            return "No strong evidence retrieved."
        top = hits[0].content.replace("\n", " | ")
        return f"Use top evidence: {top}"

    @staticmethod
    def _looks_like_entity(text: str) -> bool:
        if not text:
            return False
        return bool(re.match(r"^[A-Z][a-zA-Z_]+$", text))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        words = re.findall(r"[A-Za-z_]+|[\u4e00-\u9fff]{2,}", text.lower())
        return {w for w in words if len(w) > 1}

    @staticmethod
    def _overlap_score(query_terms: set[str], content: str) -> float:
        content_terms = CanonicalEchoMemoryTG._tokenize(content)
        if not query_terms or not content_terms:
            return 0.0
        overlap = len(query_terms & content_terms)
        return overlap / max(len(query_terms), 1)

    @staticmethod
    def _parse_anchor_time(raw: str) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    @classmethod
    def _extract_temporal_keys(cls, query: str, *, query_time: str = "") -> dict[str, set[str]]:
        years: set[str] = set(re.findall(r"\b(20\d{2})\b", query))
        months: set[str] = set()
        days: set[str] = set()

        for y, m, d in re.findall(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", query):
            yyyy = y
            mm = f"{int(m):02d}"
            dd = f"{int(d):02d}"
            years.add(yyyy)
            months.add(f"{yyyy}-{mm}")
            days.add(f"{yyyy}-{mm}-{dd}")

        for y, m in re.findall(r"\b(20\d{2})[-/](\d{1,2})\b", query):
            yyyy = y
            mm = f"{int(m):02d}"
            years.add(yyyy)
            months.add(f"{yyyy}-{mm}")

        anchor = cls._parse_anchor_time(query_time)
        if anchor is not None:
            today = anchor.replace(hour=0, minute=0, second=0, microsecond=0)

            def _add_day(day_dt: datetime) -> None:
                yyyy = f"{day_dt.year:04d}"
                mm = f"{day_dt.month:02d}"
                dd = f"{day_dt.day:02d}"
                years.add(yyyy)
                months.add(f"{yyyy}-{mm}")
                days.add(f"{yyyy}-{mm}-{dd}")

            query_low = query.lower()
            if "yesterday" in query_low or "昨天" in query:
                _add_day(today - timedelta(days=1))
            if "two days ago" in query_low or "前天" in query:
                _add_day(today - timedelta(days=2))
            if "today" in query_low or "今天" in query:
                _add_day(today)
            if "last week" in query_low or "上周" in query:
                week_anchor = today - timedelta(days=7)
                years.add(f"{week_anchor.year:04d}")
                months.add(f"{week_anchor.year:04d}-{week_anchor.month:02d}")

        return {"year": years, "month": months, "day": days}


def build_demo() -> dict[str, Any]:
    sys = CanonicalEchoMemoryTG(enable_story_time=True, enable_graph_first=True)

    sys.append_turn("user", "Jon lost his banker job yesterday and decided to start a studio.", "2023-01-20T09:00:00+00:00")
    sys.append_turn("user", "Jon opened his studio in April 2023 after months of preparation.", "2023-04-20T09:00:00+00:00")
    sys.append_turn("user", "Jon started expanding his studio social media presence in April 2023.", "2023-04-25T09:00:00+00:00")
    sys.append_turn("user", "Jon started learning marketing and analytics tools in July 2023.", "2023-07-10T09:00:00+00:00")
    sys.append_turn("user", "Jon and Gina both lost their jobs and later started businesses.", "2023-07-23T09:00:00+00:00")
    sys.append_turn("user", "Jon visited Rome on 2023-06-19.", "2023-06-20T09:00:00+00:00")

    pre_ready = sys.search("When did Jon start learning marketing and analytics tools?", query_time="2023-07-11T09:00:00+00:00")

    sys.run_hot_path()
    after_hot = sys.search("When did Jon start learning marketing and analytics tools?", query_time="2023-07-11T09:00:00+00:00")

    sys.run_cold_path()
    q1 = sys.search("When did Jon start learning marketing and analytics tools?", query_time="2023-07-11T09:00:00+00:00")
    q2 = sys.search("What did Jon and Gina both have in common?", query_time="2023-07-24T09:00:00+00:00")
    q3 = sys.search("When was Jon in Rome?", query_time="2023-06-21T09:00:00+00:00")
    q4 = sys.search("What happened yesterday?", query_time="2023-01-21T09:00:00+00:00")

    return {
        "system_name": "CanonicalEchoMemoryTG",
        "what_this_demo_shows": [
            "append-only message stream",
            "story-time normalization",
            "query-time anchored temporal retrieval",
            "temporal_tree + graph-backed retrieval",
            "qa_ready gating",
            "graph-first retrieval for temporal/relational questions",
        ],
        "readiness_before_hot": asdict(pre_ready.readiness),
        "readiness_after_hot": asdict(after_hot.readiness),
        "readiness_after_cold": asdict(sys.readiness),
        "pre_ready_result": asdict(pre_ready),
        "after_hot_result": asdict(after_hot),
        "queries_after_cold": [
            asdict(q1),
            asdict(q2),
            asdict(q3),
            asdict(q4),
        ],
        "atoms": [asdict(a) for a in sys.atoms],
        "nodes": [asdict(n) for n in sys.nodes],
        "edges": [asdict(e) for e in sys.edges],
        "temporal_blocks": [asdict(b) for b in sys.temporal_blocks],
        "organized_summaries": sys.organized_summaries,
    }


def main() -> None:
    data = build_demo()
    out = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg_output.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
