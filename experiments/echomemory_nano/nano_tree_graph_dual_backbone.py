#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone_output.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone_report.html")


@dataclass
class StreamObservation:
    obs_id: str
    obs_type: str
    content: str
    mention_time: str
    story_time: str = ""
    linked_subject: str = ""
    tags: tuple[str, ...] = ()


@dataclass
class Readiness:
    messages_persisted: bool = False
    atoms_ready: bool = False
    graph_ready: bool = False
    tree_ready: bool = False
    qa_ready: bool = False


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    subject: str
    predicate: str
    obj: str
    statement: str
    mention_time: str
    story_time: str


@dataclass
class GraphNode:
    node_id: str
    node_type: str
    content: str
    story_time: str = ""
    support: tuple[str, ...] = ()


@dataclass
class GraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class TreeBlock:
    block_id: str
    level: str
    key: str
    title: str
    content: str
    story_time: str = ""


@dataclass
class QueryPlan:
    family: str
    primary_backbone: str
    supporting_backbones: list[str]
    notes: str


@dataclass
class Hit:
    item_id: str
    layer: str
    score: float
    content: str
    provenance: str


@dataclass
class EvalCase:
    case_id: str
    query: str
    expected_keywords: list[str]
    expected_top_layers: list[str]
    note: str


class DualBackboneMemory:
    """
    Minimal research-oriented nano for explaining a dual-backbone memory:

    1. Stream observations as the source of truth.
    2. Temporal tree for chronology / coarse-to-fine recall.
    3. Relation graph for entity / event / evidence traversal.
    4. A planner that routes different query families differently.

    This is intentionally tiny but closer to a paper method than the earlier
    single-path demos.
    """

    def __init__(self) -> None:
        self.observations: list[StreamObservation] = []
        self.readiness = Readiness()
        self.atoms: list[Atom] = []
        self.graph_nodes: list[GraphNode] = []
        self.graph_edges: list[GraphEdge] = []
        self.tree_blocks: list[TreeBlock] = []

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def append_text(self, content: str, mention_time: str) -> None:
        story_time = self._infer_story_time(content, mention_time)
        self.observations.append(
            StreamObservation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="text",
                content=content.strip(),
                mention_time=mention_time,
                story_time=story_time,
            )
        )
        self._mark_dirty_after_append()

    def append_image(
        self,
        *,
        caption: str,
        ocr: str,
        mention_time: str,
        story_time: str,
        linked_subject: str,
        tags: list[str],
    ) -> None:
        payload = f"caption={caption}\nocr={ocr}"
        self.observations.append(
            StreamObservation(
                obs_id=f"obs-{len(self.observations):03d}",
                obs_type="image",
                content=payload,
                mention_time=mention_time,
                story_time=story_time,
                linked_subject=linked_subject,
                tags=tuple(tags),
            )
        )
        self._mark_dirty_after_append()

    def _mark_dirty_after_append(self) -> None:
        self.readiness.messages_persisted = True
        self.readiness.atoms_ready = False
        self.readiness.graph_ready = False
        self.readiness.tree_ready = False
        self.readiness.qa_ready = False

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def run_hot_path(self) -> None:
        self.atoms = []
        for obs in self.observations:
            if obs.obs_type != "text":
                continue
            self.atoms.extend(self._extract_atoms(obs))
        self.readiness.atoms_ready = True
        self.readiness.qa_ready = False

    def run_cold_path(self) -> None:
        self.graph_nodes = []
        self.graph_edges = []
        self.tree_blocks = []

        self._build_graph()
        self._build_temporal_tree()

        self.readiness.graph_ready = True
        self.readiness.tree_ready = True
        self.readiness.qa_ready = (
            self.readiness.messages_persisted
            and self.readiness.atoms_ready
            and self.readiness.graph_ready
            and self.readiness.tree_ready
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"\bwhen\b|\bdate\b|什么时候|哪天|日期|时间|前后|后来|之前|昨天|前天", q, re.I):
            return QueryPlan(
                family="temporal",
                primary_backbone="tree",
                supporting_backbones=["graph"],
                notes="Use temporal hierarchy first, then attach graph evidence.",
            )
        if re.search(
            r"\bwho\b|\bwhose\b|\brelationship\b|\bwhich company\b|\bwhich city\b|\bhelped\b|\bmarried to\b|"
            r"谁|关系|和.*什么关系|共同|哪个公司|哪个城市|谁帮助|谁帮|配偶|结婚对象",
            q,
            re.I,
        ):
            return QueryPlan(
                family="relational",
                primary_backbone="graph",
                supporting_backbones=["tree"],
                notes="Use entity/event graph first, then attach timeline support.",
            )
        if re.search(
            r"\bplan\b|\bafter\b|\bbefore\b|\bwhat happened after\b|\bwhat happened before\b|"
            r"计划|打算|之后要|后来准备|之后发生了什么|之前发生了什么|之后|之前",
            q,
            re.I,
        ):
            return QueryPlan(
                family="temporal_relational",
                primary_backbone="graph",
                supporting_backbones=["tree"],
                notes="Use event path plus timeline ordering to connect plan after event.",
            )
        if re.search(r"\bscreenshot\b|\bimage\b|\bphoto\b|截图|图片|照片|画面|ocr", q, re.I):
            return QueryPlan(
                family="visual",
                primary_backbone="graph",
                supporting_backbones=["tree"],
                notes="Image evidence is a first-class graph node.",
            )
        return QueryPlan(
            family="general",
            primary_backbone="tree",
            supporting_backbones=["graph"],
            notes="Default to summary blocks and supplement with graph evidence.",
        )

    def search(self, query: str) -> dict[str, Any]:
        plan = self.plan(query)
        allowed = self.readiness.qa_ready
        hits: list[Hit] = []

        if plan.primary_backbone == "tree":
            hits.extend(self._search_tree(query))
            hits.extend(self._search_graph(query, score_offset=0.08))
        else:
            hits.extend(self._search_graph(query))
            hits.extend(self._search_tree(query, score_offset=0.05))

        hits = self._dedup_and_sort(hits)

        return {
            "query": query,
            "plan": asdict(plan),
            "readiness": asdict(self.readiness),
            "allowed_to_answer": allowed,
            "hits": [asdict(hit) for hit in hits[:6]],
            "note": (
                "qa_ready false: retrieval is inspectable but should not be treated as final answer evidence."
                if not allowed else
                "dual-backbone retrieval ready"
            ),
        }

    # ------------------------------------------------------------------
    # Build graph
    # ------------------------------------------------------------------

    def _build_graph(self) -> None:
        seen_entities: set[str] = set()
        ordered_events: list[tuple[str, str]] = []
        atom_lookup: dict[str, Atom] = {}

        for atom in self.atoms:
            atom_lookup[atom.atom_id] = atom
            fact_id = f"fact:{atom.atom_id}"
            self.graph_nodes.append(
                GraphNode(
                    node_id=fact_id,
                    node_type="fact",
                    content=atom.statement,
                    story_time=atom.story_time,
                    support=(atom.atom_id,),
                )
            )

            if atom.atom_type in {"event", "relation", "plan"}:
                event_id = f"event:{atom.atom_id}"
                self.graph_nodes.append(
                    GraphNode(
                        node_id=event_id,
                        node_type="event",
                        content=f"{atom.subject} {atom.predicate} {atom.obj}",
                        story_time=atom.story_time,
                        support=(atom.atom_id,),
                    )
                )
                ordered_events.append((event_id, atom.story_time))
                self.graph_edges.append(
                    GraphEdge(
                        edge_id=f"{event_id}:evidence_of:{fact_id}",
                        source_id=event_id,
                        target_id=fact_id,
                        relation_type="evidence_of",
                    )
                )
            else:
                event_id = ""

            for ent in [atom.subject, atom.obj]:
                if not self._looks_like_entity(ent):
                    continue
                entity_id = f"entity:{ent}"
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    self.graph_nodes.append(
                        GraphNode(
                            node_id=entity_id,
                            node_type="entity",
                            content=f"name={ent}",
                            support=(ent,),
                        )
                    )
                self.graph_edges.append(
                    GraphEdge(
                        edge_id=f"{entity_id}:has_fact:{atom.atom_id}",
                        source_id=entity_id,
                        target_id=fact_id,
                        relation_type="has_fact",
                    )
                )
                if event_id:
                    self.graph_edges.append(
                        GraphEdge(
                            edge_id=f"{event_id}:involves:{ent}",
                            source_id=event_id,
                            target_id=entity_id,
                            relation_type="involves",
                        )
                    )

        ordered_events = [item for item in ordered_events if item[1]]
        ordered_events.sort(key=lambda item: item[1])
        for left, right in zip(ordered_events, ordered_events[1:]):
            self.graph_edges.append(
                GraphEdge(
                    edge_id=f"{left[0]}:temporal_next:{right[0]}",
                    source_id=left[0],
                    target_id=right[0],
                    relation_type="temporal_next",
                )
            )

        for obs in self.observations:
            if obs.obs_type != "image":
                continue
            image_id = f"image:{obs.obs_id}"
            self.graph_nodes.append(
                GraphNode(
                    node_id=image_id,
                    node_type="image_evidence",
                    content=obs.content,
                    story_time=obs.story_time,
                    support=(obs.obs_id,),
                )
            )
            if obs.linked_subject:
                entity_id = f"entity:{obs.linked_subject}"
                self.graph_edges.append(
                    GraphEdge(
                        edge_id=f"{image_id}:shows:{entity_id}",
                        source_id=image_id,
                        target_id=entity_id,
                        relation_type="shows",
                    )
                )

    # ------------------------------------------------------------------
    # Build tree
    # ------------------------------------------------------------------

    def _build_temporal_tree(self) -> None:
        day_buckets: dict[str, list[str]] = {}
        month_buckets: dict[str, list[str]] = {}
        year_buckets: dict[str, list[str]] = {}

        for atom in self.atoms:
            story_day = atom.story_time[:10] if atom.story_time else atom.mention_time[:10]
            story_month = story_day[:7]
            story_year = story_day[:4]
            line = f"- {story_day}: {atom.statement}"
            day_buckets.setdefault(story_day, []).append(line)
            month_buckets.setdefault(story_month, []).append(line)
            year_buckets.setdefault(story_year, []).append(line)

        for year_key, items in sorted(year_buckets.items()):
            self.tree_blocks.append(
                TreeBlock(
                    block_id=f"tree:year:{year_key}",
                    level="year",
                    key=year_key,
                    title=f"Year {year_key}",
                    content="\n".join(items[:12]),
                    story_time=year_key,
                )
            )
        for month_key, items in sorted(month_buckets.items()):
            self.tree_blocks.append(
                TreeBlock(
                    block_id=f"tree:month:{month_key}",
                    level="month",
                    key=month_key,
                    title=f"Month {month_key}",
                    content="\n".join(items[:12]),
                    story_time=month_key,
                )
            )
        for day_key, items in sorted(day_buckets.items()):
            self.tree_blocks.append(
                TreeBlock(
                    block_id=f"tree:day:{day_key}",
                    level="day",
                    key=day_key,
                    title=f"Day {day_key}",
                    content="\n".join(items[:10]),
                    story_time=day_key,
                )
            )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def _search_tree(self, query: str, score_offset: float = 0.0) -> list[Hit]:
        terms = self._terms(query)
        family = self.plan(query).family
        hits: list[Hit] = []
        for block in self.tree_blocks:
            score = self._lexical_score(block.title + "\n" + block.content, terms)
            if score <= 0:
                continue
            if block.level == "day" and family == "temporal":
                score += 1.0
            elif block.level == "month":
                score += 0.25
            if family == "relational":
                score -= 0.8
            hits.append(
                Hit(
                    item_id=block.block_id,
                    layer="tree",
                    score=round(score + score_offset, 3),
                    content=f"{block.title}\n{block.content}",
                    provenance=f"temporal_{block.level}",
                )
            )
        return hits

    def _search_graph(self, query: str, score_offset: float = 0.0) -> list[Hit]:
        terms = self._terms(query)
        family = self.plan(query).family
        hits: list[Hit] = []
        query_lower = query.lower()

        for node in self.graph_nodes:
            score = self._lexical_score(node.content + "\n" + node.node_id, terms)
            if score <= 0 and node.node_type != "image_evidence":
                continue

            if family == "relational":
                if node.node_type == "entity":
                    score += 1.6
                elif node.node_type == "event":
                    score += 1.0
                elif node.node_type == "fact":
                    score += 0.2
            elif family == "temporal":
                if node.node_type == "event":
                    score += 0.8
            elif family == "visual":
                if node.node_type == "image_evidence":
                    score += 1.1
            elif family == "temporal_relational":
                if node.node_type in {"event", "entity"}:
                    score += 0.7

            hits.append(
                Hit(
                    item_id=node.node_id,
                    layer=node.node_type,
                    score=round(score + score_offset, 3),
                    content=node.content,
                    provenance="graph_node",
                )
            )

        top_seed_ids = {hit.item_id for hit in sorted(hits, key=lambda item: -item.score)[:3]}
        node_map = {node.node_id: node for node in self.graph_nodes}
        outgoing_temporal: dict[str, list[str]] = {}
        incoming_temporal: dict[str, list[str]] = {}
        for edge in self.graph_edges:
            if edge.relation_type != "temporal_next":
                continue
            outgoing_temporal.setdefault(edge.source_id, []).append(edge.target_id)
            incoming_temporal.setdefault(edge.target_id, []).append(edge.source_id)
        for edge in self.graph_edges:
            if edge.source_id not in top_seed_ids and edge.target_id not in top_seed_ids:
                continue
            for node_id in (edge.source_id, edge.target_id):
                node = node_map.get(node_id)
                if node is None:
                    continue
                bonus = 0.45
                if family == "visual" and node.node_type == "image_evidence":
                    bonus += 0.5
                if family == "temporal" and edge.relation_type == "temporal_next":
                    bonus += 0.35
                if family in {"relational", "temporal_relational"} and edge.relation_type in {"involves", "shows"}:
                    bonus += 0.3
                    if node.node_type == "entity":
                        bonus += 0.55
                if family == "relational" and edge.relation_type == "has_fact" and node.node_type == "entity":
                    bonus += 0.65
                hits.append(
                    Hit(
                        item_id=node.node_id,
                        layer=node.node_type,
                        score=round(bonus + score_offset, 3),
                        content=f"{node.content}\n[path:{edge.relation_type}]",
                        provenance=f"path:{edge.relation_type}",
                    )
                )

        if family == "temporal_relational":
            for seed_id in list(top_seed_ids):
                if "after" in query_lower or "之后" in query_lower:
                    for target_id in outgoing_temporal.get(seed_id, []):
                        node = node_map.get(target_id)
                        if node is None:
                            continue
                        hits.append(
                            Hit(
                                item_id=node.node_id,
                                layer=node.node_type,
                                score=1.55,
                                content=f"{node.content}\n[path:temporal_next_after]",
                                provenance="path:temporal_next_after",
                            )
                        )
                if "before" in query_lower or "之前" in query_lower:
                    for source_id in incoming_temporal.get(seed_id, []):
                        node = node_map.get(source_id)
                        if node is None:
                            continue
                        hits.append(
                            Hit(
                                item_id=node.node_id,
                                layer=node.node_type,
                                score=1.55,
                                content=f"{node.content}\n[path:temporal_next_before]",
                                provenance="path:temporal_next_before",
                            )
                        )
        return hits

    @staticmethod
    def _dedup_and_sort(hits: list[Hit]) -> list[Hit]:
        merged: dict[str, Hit] = {}
        for hit in hits:
            prev = merged.get(hit.item_id)
            if prev is None or hit.score > prev.score:
                merged[hit.item_id] = hit
        return sorted(merged.values(), key=lambda item: (-item.score, item.item_id))

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_atoms(self, obs: StreamObservation) -> list[Atom]:
        text = obs.content
        patterns = [
            (r"([A-Z][a-z]+)\s+joined\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "joined", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+met\s+([A-Z][a-z]+).+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "met", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+married\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "{0}", "married", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+left\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "left", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+helped\s+([A-Z][a-z]+)\s+(.+)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "{0}", "helped", "{1}", "{3}"),
            (r"([A-Z][a-z]+)\s+signed\s+(.+)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "signed", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+plans to\s+(.+)", "plan", "{0}", "plans", "{1}", ""),
            (r"([A-Z][a-z]+)\s+planned to\s+(.+)", "plan", "{0}", "planned", "{1}", ""),
            (r"([A-Z][a-z]+)\s+visited\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "{0}", "visited", "{1}", "{2}"),
        ]
        atoms: list[Atom] = []
        for pat, atom_type, s_t, p_t, o_t, t_t in patterns:
            m = re.search(pat, text, re.I)
            if not m:
                continue
            groups = m.groups()
            story_time = t_t.format(*groups).strip() if t_t else obs.story_time
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms):03d}",
                    atom_type=atom_type,
                    subject=s_t.format(*groups).strip(),
                    predicate=p_t.format(*groups).strip(),
                    obj=o_t.format(*groups).strip(),
                    statement=text,
                    mention_time=obs.mention_time,
                    story_time=story_time or obs.story_time,
                )
            )
            return atoms

        atoms.append(
            Atom(
                atom_id=f"atom-{len(self.atoms):03d}",
                atom_type="fact",
                subject="unknown",
                predicate="mentions",
                obj=text[:40],
                statement=text,
                mention_time=obs.mention_time,
                story_time=obs.story_time,
            )
        )
        return atoms

    @staticmethod
    def _infer_story_time(text: str, mention_time: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        return m.group(1) if m else mention_time[:10]

    @staticmethod
    def _looks_like_entity(value: str) -> bool:
        raw = str(value or "").strip()
        if not raw or raw == "unknown":
            return False
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return False
        return bool(re.match(r"[A-Z][a-z]+$", raw))

    @staticmethod
    def _terms(text: str) -> list[str]:
        lowered = text.lower()
        terms = re.findall(r"[a-z]{2,}|[\u4e00-\u9fa5]{1,}", lowered)
        return [term for term in terms if term not in {"the", "did", "what", "was", "who", "when"}]

    @staticmethod
    def _lexical_score(text: str, terms: list[str]) -> float:
        hay = text.lower()
        return float(sum(1 for term in terms if term in hay))


def build_demo_memory() -> DualBackboneMemory:
    mem = DualBackboneMemory()
    mem.append_text("Gina joined Figma on 2023-01-12.", "2023-01-13T09:00:00Z")
    mem.append_text("Gina married Alex on 2023-02-18.", "2023-02-19T11:00:00Z")
    mem.append_text("Gina left Figma on 2023-03-04.", "2023-03-05T10:00:00Z")
    mem.append_text("Gina plans to move to Lisbon after leaving Figma.", "2023-03-10T14:00:00Z")
    mem.append_text("Gina visited Lisbon on 2023-03-21.", "2023-03-22T08:00:00Z")
    mem.append_image(
        caption="Phone screenshot from Lisbon arrival day.",
        ocr="Lisbon Santa Apolonia Platform 4 08:42",
        mention_time="2023-03-22T08:01:00Z",
        story_time="2023-03-21",
        linked_subject="Gina",
        tags=["lisbon", "station", "arrival"],
    )
    mem.run_hot_path()
    mem.run_cold_path()
    return mem


def evaluate(mem: DualBackboneMemory) -> dict[str, Any]:
    cases = [
        EvalCase(
            case_id="temporal_join",
            query="When did Gina join Figma?",
            expected_keywords=["2023-01-12", "joined Figma"],
            expected_top_layers=["tree"],
            note="Temporal queries should benefit from a temporal hierarchy rather than flat facts only.",
        ),
        EvalCase(
            case_id="relation_spouse",
            query="Who is Gina married to?",
            expected_keywords=["Alex", "married"],
            expected_top_layers=["entity", "event"],
            note="Relational queries should enter through graph entities and event links.",
        ),
        EvalCase(
            case_id="plan_after_event",
            query="What did Gina plan after leaving Figma?",
            expected_keywords=["move to Lisbon", "left Figma"],
            expected_top_layers=["event"],
            note="Plan-after-event requires both temporal and relational support.",
        ),
        EvalCase(
            case_id="visual_arrival",
            query="What was visible in Gina's arrival screenshot?",
            expected_keywords=["Lisbon Santa Apolonia", "08:42"],
            expected_top_layers=["image_evidence"],
            note="Visual queries should see image evidence as a first-class memory object.",
        ),
    ]

    results: list[dict[str, Any]] = []
    for case in cases:
        search = mem.search(case.query)
        top_layer = search["hits"][0]["layer"] if search["hits"] else "none"
        blob = "\n".join(hit["content"] for hit in search["hits"][:4]).lower()
        keyword_ok = all(keyword.lower() in blob for keyword in case.expected_keywords)
        results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "expected_keywords": case.expected_keywords,
                "expected_top_layers": case.expected_top_layers,
                "note": case.note,
                "result": search,
                "top_layer": top_layer,
                "keyword_ok": keyword_ok,
                "top_layer_ok": top_layer in case.expected_top_layers,
                "overall_ok": keyword_ok and top_layer in case.expected_top_layers,
            }
        )

    return {
        "summary": {
            "total_cases": len(results),
            "passed": sum(1 for item in results if item["overall_ok"]),
            "qa_ready": mem.readiness.qa_ready,
            "atoms": len(mem.atoms),
            "graph_nodes": len(mem.graph_nodes),
            "graph_edges": len(mem.graph_edges),
            "tree_blocks": len(mem.tree_blocks),
        },
        "cases": results,
    }


def render_html(memory: DualBackboneMemory, evaluation: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value))

    summary = evaluation["summary"]
    rows = []
    for case in evaluation["cases"]:
        rows.append(
            f"""
            <tr>
              <td>{esc(case['case_id'])}</td>
              <td>{esc(case['query'])}</td>
              <td>{esc(', '.join(case['expected_top_layers']))}</td>
              <td>{esc(case['top_layer'])}</td>
              <td>{'yes' if case['keyword_ok'] else 'no'}</td>
              <td>{'yes' if case['overall_ok'] else 'no'}</td>
              <td>{esc(case['note'])}</td>
            </tr>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Dual Backbone Report</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#5f6b7a; --line:#dde4ee;
      --blue:#2563eb; --blue-soft:#eaf2ff; --green:#0f9f6e; --green-soft:#eafaf4;
      --amber:#c77b00; --amber-soft:#fff7e8;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.65 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif; }}
    .wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 48px; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:20px 22px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 10px; }}
    p {{ margin:0 0 10px; }}
    ul {{ margin:8px 0 0; padding-left:18px; }}
    li {{ margin:4px 0; }}
    .kpis {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi {{ border:1px solid var(--line); border-radius:10px; padding:12px 14px; background:#fbfcff; }}
    .label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
    .value {{ font-size:22px; font-weight:700; }}
    .badge {{ display:inline-block; padding:3px 9px; border-radius:999px; font-size:12px; font-weight:700; }}
    .ok {{ background:var(--green-soft); color:var(--green); }}
    .warn {{ background:var(--amber-soft); color:var(--amber); }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    code, pre {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    code {{ background:#f3f6fb; border-radius:4px; padding:1px 5px; }}
    pre {{ background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:12px; overflow:auto; font-size:12px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    @media (max-width:960px) {{ .grid,.kpis {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Nano: Stream + Temporal Tree + Relation Graph</h1>
      <p>
        This nano is designed to explain one concrete upgrade direction for EchoMemory:
        instead of treating graph memory as a sparse sidecar, use a <b>dual-backbone memory</b>
        with a <b>temporal tree</b> for chronology and a <b>relation graph</b> for entity / event / image evidence traversal.
      </p>
      <div class="kpis">
        <div class="kpi"><span class="label">Cases</span><span class="value">{esc(summary['total_cases'])}</span></div>
        <div class="kpi"><span class="label">Passed</span><span class="value">{esc(summary['passed'])}</span></div>
        <div class="kpi"><span class="label">Atoms</span><span class="value">{esc(summary['atoms'])}</span></div>
        <div class="kpi"><span class="label">Graph Nodes</span><span class="value">{esc(summary['graph_nodes'])}</span></div>
        <div class="kpi"><span class="label">Tree Blocks</span><span class="value">{esc(summary['tree_blocks'])}</span></div>
      </div>
    </div>

    <div class="section">
      <h2>What this nano tries to demonstrate</h2>
      <div class="grid">
        <div>
          <ul>
            <li>Temporal queries should not rely only on flat fact matching.</li>
            <li>Relational queries should enter through graph entities and event links.</li>
            <li>Visual evidence should be a first-class memory object, not extra prompt text.</li>
            <li><code>messages_persisted</code> is weaker than <code>qa_ready</code>.</li>
          </ul>
        </div>
        <div>
          <ul>
            <li>Tree backbone: coarse-to-fine chronology blocks.</li>
            <li>Graph backbone: entity / event / fact / image nodes and path expansion.</li>
            <li>Planner: route by query family instead of one universal retrieval mode.</li>
            <li>Output: inspectable hits plus readiness state.</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Evaluation</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query</th>
            <th>Expected Top</th>
            <th>Actual Top</th>
            <th>Keywords</th>
            <th>Pass</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>

    <div class="section">
      <h2>Readiness</h2>
      <p><span class="badge {'ok' if memory.readiness.qa_ready else 'warn'}">{'qa_ready' if memory.readiness.qa_ready else 'not qa_ready'}</span></p>
      <pre>{esc(json.dumps(asdict(memory.readiness), ensure_ascii=False, indent=2))}</pre>
    </div>

    <div class="section">
      <h2>Sample Search Output</h2>
      <pre>{esc(json.dumps(evaluation['cases'][0]['result'], ensure_ascii=False, indent=2))}</pre>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    memory = build_demo_memory()
    evaluation = evaluate(memory)
    payload = {
        "observations": [asdict(obs) for obs in memory.observations],
        "atoms": [asdict(atom) for atom in memory.atoms],
        "graph_nodes": [asdict(node) for node in memory.graph_nodes],
        "graph_edges": [asdict(edge) for edge in memory.graph_edges],
        "tree_blocks": [asdict(block) for block in memory.tree_blocks],
        "evaluation": evaluation,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(memory, evaluation), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
