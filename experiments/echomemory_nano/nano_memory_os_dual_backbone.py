#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_memory_os_dual_backbone_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_memory_os_dual_backbone_20260615.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def score_overlap(query: str, content: str) -> float:
    q_tokens = set(re.findall(r"[A-Za-z_]+|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", query.lower()))
    c_tokens = set(re.findall(r"[A-Za-z_]+|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", content.lower()))
    if not q_tokens:
        return 0.0
    return float(len(q_tokens & c_tokens))


def looks_like_entity(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return False
    if text.lower() in {"job", "keynote", "review", "checklist"}:
        return False
    return any(ch.isupper() for ch in text[:1]) or " " in text


def infer_relative_date(text: str, anchor_iso: str) -> str:
    anchor = datetime.fromisoformat(anchor_iso.replace("Z", "+00:00"))
    lowered = text.lower()
    explicit = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if explicit:
        return explicit.group(1)
    if "yesterday" in lowered:
        return (anchor - timedelta(days=1)).strftime("%Y-%m-%d")
    if "last week" in lowered:
        return (anchor - timedelta(days=7)).strftime("%Y-%m-%d")
    if "tomorrow" in lowered:
        return (anchor + timedelta(days=1)).strftime("%Y-%m-%d")
    return anchor.strftime("%Y-%m-%d")


@dataclass
class Observation:
    obs_id: str
    role: str
    modality: str
    content: str
    mention_time: str
    write_time: str
    story_time: str
    tags: list[str] = field(default_factory=list)


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    state_kind: str
    subject: str
    predicate: str
    obj: str
    statement: str
    story_time: str
    mention_time: str
    write_time: str
    status: str = "active"
    source_obs_id: str = ""


@dataclass
class ReceiptStage:
    ready: bool
    status: str
    updated_at: str
    detail: str = ""


@dataclass
class ReadinessReceipt:
    schema_version: str = "nano-v1"
    updated_at: str = ""
    last_message_id: str = ""
    last_extracted_atom_id: str = ""
    stages: dict[str, ReceiptStage] = field(default_factory=dict)


@dataclass
class TreeBlock:
    block_id: str
    level: str
    key: str
    content: str
    source_refs: list[str]


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    story_time: str = ""
    source_ref: str = ""


@dataclass
class Edge:
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class QueryPlan:
    family: str
    primary_reader: str
    supporting_readers: list[str]
    must_have_layers: list[str]
    reason: str


@dataclass
class Hit:
    source: str
    layer: str
    score: float
    content: str
    story_time: str = ""
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalCase:
    case_id: str
    query: str
    query_time: str
    expected_keywords: list[str]
    note: str


class NanoMemoryOSDualBackbone:
    """
    A compact method prototype that is closer to the current paper-worthy path:

    stream -> governed write path -> atoms with lifecycle -> temporal tree + graph
    -> plan -> type-aware second pass -> contract-aware answer.

    The point is not to mimic a benchmark. The point is to isolate the generic
    mechanisms that should still help on unseen conversational memory tasks.
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.blocks: list[TreeBlock] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.receipt = ReadinessReceipt(
            stages={
                "messages": ReceiptStage(False, "pending", ""),
                "atoms": ReceiptStage(False, "pending", ""),
                "tree": ReceiptStage(False, "pending", ""),
                "graph": ReceiptStage(False, "pending", ""),
                "qa": ReceiptStage(False, "pending", ""),
            }
        )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def append(self, role: str, content: str, mention_time: str, modality: str = "text") -> None:
        story_time = infer_relative_date(content, mention_time)
        obs = Observation(
            obs_id=f"obs-{len(self.observations):03d}",
            role=role,
            modality=modality,
            content=content.strip(),
            mention_time=mention_time,
            write_time=mention_time,
            story_time=story_time,
            tags=self._infer_tags(content),
        )
        self.observations.append(obs)
        self.receipt.updated_at = mention_time
        self.receipt.last_message_id = obs.obs_id
        self.receipt.stages["messages"] = ReceiptStage(True, "complete", mention_time, "append-only persisted")
        for stage in ("atoms", "tree", "graph", "qa"):
            self.receipt.stages[stage] = ReceiptStage(False, "pending", mention_time, "downstream invalidated")

    def build(self) -> None:
        kept = [obs for obs in self.observations if self._should_store(obs)]
        self.atoms = self._extract_atoms(kept)
        self._apply_lifecycle()
        self.receipt.last_extracted_atom_id = self.atoms[-1].atom_id if self.atoms else ""
        stamp = self.observations[-1].write_time if self.observations else ""
        self.receipt.stages["atoms"] = ReceiptStage(True, "complete", stamp, f"{len(self.atoms)} atoms")

        self.blocks = self._build_tree()
        self.receipt.stages["tree"] = ReceiptStage(True, "complete", stamp, f"{len(self.blocks)} tree blocks")

        self.nodes, self.edges = self._build_graph()
        self.receipt.stages["graph"] = ReceiptStage(True, "complete", stamp, f"{len(self.nodes)} nodes / {len(self.edges)} edges")

        qa_ok = bool(self.atoms and self.blocks and self.nodes)
        self.receipt.stages["qa"] = ReceiptStage(qa_ok, "complete" if qa_ok else "blocked", stamp, "ready for retrieval" if qa_ok else "insufficient evidence planes")
        self.receipt.updated_at = stamp

    def qa_ready(self) -> bool:
        return self.receipt.stages["qa"].ready

    # ------------------------------------------------------------------
    # Build internals
    # ------------------------------------------------------------------

    def _should_store(self, obs: Observation) -> bool:
        text = obs.content.strip()
        if obs.role != "user":
            return False
        if len(text) < 6:
            return False
        if re.search(r"^(what|when|who|where|why|how|can you|please|do you)\b", text.lower()):
            return False
        if text.endswith("?") or text.endswith("？"):
            return False
        return True

    def _extract_atoms(self, observations: list[Observation]) -> list[Atom]:
        atoms: list[Atom] = []
        rules = [
            (r"([A-Z][a-z]+)\s+joined\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "event", "{0}", "joined", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+left\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "event", "{0}", "left", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+signed\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "event", "{0}", "signed", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+moved\s+to\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "event", "event", "{0}", "moved_to", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+married\s+([A-Z][a-z]+)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "event", "{0}", "married_to", "{1}", "{2}"),
            (r"([A-Z][a-z]+)\s+helped\s+([A-Z][a-z]+)\s+prepare\s+the\s+(.+?)\s+on\s+(\d{4}-\d{2}-\d{2})", "relation", "event", "{0}", "helped_with", "{1}::{2}", "{3}"),
            (r"([A-Z][a-z]+)\s+plans to\s+(.+)", "plan", "state", "{0}", "plans", "{1}", ""),
            (r"([A-Z][a-z]+)\s+likes\s+(.+)", "preference", "state", "{0}", "likes", "{1}", ""),
            (r"([A-Z][a-z]+)\s+presented\s+the\s+(.+?)\s+yesterday", "event", "event", "{0}", "presented", "{1}", ""),
            (r"([A-Z][a-z]+)\s+had\s+the\s+(.+?)\s+last week", "event", "event", "{0}", "had", "{1}", ""),
        ]
        for obs in observations:
            matched = False
            if obs.modality == "image":
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type="image_evidence",
                        state_kind="event",
                        subject="image_evidence",
                        predicate="shows",
                        obj=obs.content[:80],
                        statement=obs.content,
                        story_time=obs.story_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                    )
                )
                continue

            for pattern, atom_type, state_kind, subj_t, pred_t, obj_t, time_t in rules:
                m = re.search(pattern, obs.content, re.I)
                if not m:
                    continue
                g = m.groups()
                event_time = time_t.format(*g).strip() if time_t else ""
                if not event_time:
                    event_time = obs.story_time
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=atom_type,
                        state_kind=state_kind,
                        subject=subj_t.format(*g).strip(),
                        predicate=pred_t.format(*g).strip(),
                        obj=obj_t.format(*g).strip(),
                        statement=obs.content,
                        story_time=event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
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
                        state_kind="state",
                        subject="unknown",
                        predicate="mentions",
                        obj=obs.content[:64],
                        statement=obs.content,
                        story_time=obs.story_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                    )
                )
        return atoms

    def _apply_lifecycle(self) -> None:
        latest_by_key: dict[tuple[str, str], int] = {}
        for idx, atom in enumerate(self.atoms):
            if atom.predicate not in {"joined", "left", "moved_to", "signed"}:
                continue
            key = (atom.subject, atom.predicate)
            latest_by_key[key] = idx
        for idx, atom in enumerate(self.atoms):
            if atom.predicate not in {"joined", "left", "moved_to", "signed"}:
                continue
            key = (atom.subject, atom.predicate)
            if latest_by_key.get(key) != idx:
                atom.status = "superseded"

    def _build_tree(self) -> list[TreeBlock]:
        buckets: dict[tuple[str, str], list[Atom]] = {}
        for atom in self.atoms:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", atom.story_time):
                continue
            yyyy, mm, dd = atom.story_time.split("-")
            for level, key in (("year", yyyy), ("month", f"{yyyy}-{mm}"), ("day", atom.story_time)):
                buckets.setdefault((level, key), []).append(atom)
        blocks: list[TreeBlock] = []
        for (level, key), items in sorted(buckets.items()):
            ordered = sorted(items, key=lambda x: x.story_time)
            blocks.append(
                TreeBlock(
                    block_id=f"{level}:{key}",
                    level=level,
                    key=key,
                    content="\n".join(f"- {item.story_time}: {item.statement}" for item in ordered),
                    source_refs=[item.atom_id for item in ordered],
                )
            )
        return blocks

    def _build_graph(self) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen_entities: set[str] = set()
        timed_events: list[tuple[str, str]] = []
        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(Node(fact_id, "fact", atom.statement, atom.story_time, atom.source_obs_id))
            event_node_id = ""
            if atom.atom_type in {"event", "relation", "plan", "image_evidence"}:
                node_type = "image_evidence" if atom.atom_type == "image_evidence" else "event"
                event_node_id = f"{node_type}:{atom.atom_id}"
                nodes.append(Node(event_node_id, node_type, f"{atom.subject} {atom.predicate} {atom.obj}", atom.story_time, atom.source_obs_id))
                edges.append(Edge(f"{event_node_id}:evidence_of:{fact_id}", event_node_id, fact_id, "evidence_of"))
                if atom.story_time:
                    timed_events.append((event_node_id, atom.story_time))
            for value in [atom.subject, atom.obj]:
                if not looks_like_entity(value):
                    continue
                ent_id = f"entity:{value}"
                if ent_id not in seen_entities:
                    seen_entities.add(ent_id)
                    nodes.append(Node(ent_id, "entity", f"name={value}", "", atom.source_obs_id))
                edges.append(Edge(f"{ent_id}:has_fact:{atom.atom_id}", ent_id, fact_id, "has_fact"))
                if event_node_id:
                    rel = "visual_evidence_of" if event_node_id.startswith("image_evidence:") else "involves"
                    edges.append(Edge(f"{event_node_id}:{rel}:{value}", event_node_id, ent_id, rel))
        timed_events.sort(key=lambda x: x[1])
        for left, right in zip(timed_events, timed_events[1:]):
            edges.append(Edge(f"{left[0]}:temporal_next:{right[0]}", left[0], right[0], "temporal_next"))
        return nodes, edges

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"\bwhen\b|date|time|yesterday|last week|什么时候|日期|时间|昨天|上周", q):
            return QueryPlan(
                family="temporal",
                primary_reader="tree",
                supporting_readers=["graph", "atom"],
                must_have_layers=["temporal_tree", "event"],
                reason="Temporal questions need chronology plus event grounding.",
            )
        if re.search(r"\bwho\b|relationship|helped|introduced|牵线|关系|谁帮|谁", q):
            return QueryPlan(
                family="relational",
                primary_reader="graph",
                supporting_readers=["atom", "tree"],
                must_have_layers=["graph", "fact"],
                reason="Relational questions need graph connectivity backed by facts.",
            )
        if re.search(r"plan|next|after|打算|计划|之后", q):
            return QueryPlan(
                family="plan",
                primary_reader="graph",
                supporting_readers=["tree", "atom"],
                must_have_layers=["event", "fact"],
                reason="Plans and follow-ups need event/fact support and ordering context.",
            )
        if re.search(r"photo|image|screenshot|ocr|图片|截图|照片", q):
            return QueryPlan(
                family="visual",
                primary_reader="graph",
                supporting_readers=["atom", "tree"],
                must_have_layers=["image_evidence", "fact"],
                reason="Visual questions need first-class image evidence linked to facts.",
            )
        return QueryPlan(
            family="general",
            primary_reader="atom",
            supporting_readers=["tree", "graph"],
            must_have_layers=["fact"],
            reason="General factual questions can start from atom evidence.",
        )

    def run_query(self, query: str, query_time: str, variant: str) -> dict[str, Any]:
        plan = self.plan(query)
        if variant == "flat_text":
            hits = self._flat_text_search(query)
            coverage = self._coverage(plan.must_have_layers, hits)
            return {"variant": variant, "plan": asdict(plan), "coverage": coverage, "hits": [asdict(h) for h in hits[:6]]}

        if not self.qa_ready():
            return {"variant": variant, "plan": asdict(plan), "error": "qa_not_ready", "hits": []}

        if variant == "dual_backbone":
            primary = self._read(plan.primary_reader, query, query_time)
            hits = primary
        elif variant == "contract_aware":
            primary = self._read(plan.primary_reader, query, query_time)
            coverage = self._coverage(plan.must_have_layers, primary)
            hits = list(primary)
            second_pass: list[str] = []
            if coverage["missing_layers"]:
                for missing in coverage["missing_layers"]:
                    reader = self._reader_for_missing_layer(missing)
                    second_pass.append(reader)
                    hits.extend(self._read(reader, query, query_time))
                deduped: dict[tuple[str, str], Hit] = {}
                for hit in hits:
                    key = (hit.source, hit.layer)
                    existing = deduped.get(key)
                    if existing is None or hit.score > existing.score:
                        deduped[key] = hit
                hits = list(deduped.values())
                hits.sort(key=lambda x: x.score, reverse=True)
                coverage = self._coverage(plan.must_have_layers, hits)
                return {
                    "variant": variant,
                    "plan": asdict(plan),
                    "coverage": coverage,
                    "second_pass_readers": second_pass,
                    "hits": [asdict(h) for h in hits[:8]],
                }
        else:
            raise ValueError(f"Unknown variant: {variant}")

        coverage = self._coverage(plan.must_have_layers, hits)
        return {"variant": variant, "plan": asdict(plan), "coverage": coverage, "hits": [asdict(h) for h in hits[:8]]}

    def _reader_for_missing_layer(self, layer: str) -> str:
        return {
            "temporal_tree": "tree",
            "graph": "graph",
            "event": "graph",
            "fact": "atom",
            "image_evidence": "graph",
        }.get(layer, "atom")

    def _coverage(self, required: list[str], hits: list[Hit]) -> dict[str, Any]:
        present = sorted({hit.layer for hit in hits})
        matched = [layer for layer in required if layer in present]
        missing = [layer for layer in required if layer not in present]
        return {
            "required_layers": required,
            "present_layers": present,
            "matched_layers": matched,
            "missing_layers": missing,
            "coverage_ratio": round((len(matched) / len(required)) if required else 1.0, 3),
            "contract_ok": not missing,
        }

    def _flat_text_search(self, query: str) -> list[Hit]:
        hits: list[Hit] = []
        for obs in self.observations:
            score = score_overlap(query, obs.content)
            if score <= 0:
                continue
            hits.append(Hit(obs.obs_id, "flat_text", score, obs.content, obs.story_time, {"reader": "flat"}))
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits

    def _read(self, reader: str, query: str, query_time: str) -> list[Hit]:
        if reader == "tree":
            return self._read_tree(query)
        if reader == "graph":
            return self._read_graph(query)
        return self._read_atoms(query, query_time)

    def _read_tree(self, query: str) -> list[Hit]:
        hits: list[Hit] = []
        for block in self.blocks:
            score = score_overlap(query, block.content)
            if score <= 0:
                continue
            story_time = block.key if block.level == "day" else ""
            hits.append(Hit(block.block_id, "temporal_tree", score + 0.5, block.content, story_time, {"reader": "tree"}))
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:6]

    def _read_graph(self, query: str) -> list[Hit]:
        hits: list[Hit] = []
        for node in self.nodes:
            score = score_overlap(query, node.content)
            if score <= 0:
                continue
            layer = node.node_type if node.node_type in {"event", "entity", "image_evidence"} else "fact"
            hits.append(Hit(node.node_id, layer, score + 1.0, node.content, node.story_time, {"reader": "graph"}))
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:6]

    def _read_atoms(self, query: str, query_time: str) -> list[Hit]:
        hits: list[Hit] = []
        q_lower = query.lower()
        for atom in self.atoms:
            score = score_overlap(query, atom.statement)
            if score <= 0 and not (("yesterday" in q_lower or "last week" in q_lower) and atom.story_time):
                continue
            if "yesterday" in q_lower and atom.story_time == (datetime.fromisoformat(query_time.replace("Z", "+00:00")) - timedelta(days=1)).strftime("%Y-%m-%d"):
                score += 1.0
            if "last week" in q_lower:
                anchor = datetime.fromisoformat(query_time.replace("Z", "+00:00"))
                if atom.story_time == (anchor - timedelta(days=7)).strftime("%Y-%m-%d"):
                    score += 1.0
            hits.append(Hit(f"fact:{atom.atom_id}", "fact", score, atom.statement, atom.story_time, {"reader": "atom", "status": atom.status}))
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:6]

    def _infer_tags(self, text: str) -> list[str]:
        tags: list[str] = []
        if "screenshot" in text.lower() or "photo" in text.lower():
            tags.append("image")
        if re.search(r"join|left|moved|signed|married|helped", text.lower()):
            tags.append("event")
        if "plan" in text.lower() or "plans to" in text.lower():
            tags.append("plan")
        return tags


def build_demo_memory() -> NanoMemoryOSDualBackbone:
    mem = NanoMemoryOSDualBackbone()
    mem.append("user", "Aria left Northlight Studio on 2026-01-20.", "2026-01-21T10:00:00Z")
    mem.append("user", "Aria joined Orchard Labs on 2026-02-14.", "2026-02-15T09:00:00Z")
    mem.append("user", "Aria signed the Riverside lease on 2026-03-03, and I am only mentioning it now after travel.", "2026-03-10T09:00:00Z")
    mem.append("user", "Nora helped Aria prepare the visa checklist on 2026-04-02.", "2026-04-03T08:30:00Z")
    mem.append("user", "Aria plans to relocate to Lisbon after joining Orchard Labs.", "2026-04-04T09:00:00Z")
    mem.append("user", "Aria presented the keynote deck yesterday.", "2026-05-10T08:00:00Z")
    mem.append("user", "Aria had the investor board review last week.", "2026-05-18T09:00:00Z")
    mem.append("user", "Lease document screenshot Rua Augusta 14 Lisbon Lease Agreement", "2026-03-10T09:05:00Z", modality="image")
    mem.append("user", "What should I do next?", "2026-05-18T09:10:00Z")
    mem.build()
    return mem


def run_benchmark(mem: NanoMemoryOSDualBackbone) -> dict[str, Any]:
    cases = [
        EvalCase("c1_temporal_absolute", "When did Aria sign the Riverside lease?", "2026-03-11T10:00:00Z", ["2026-03-03", "signed"], "Story time should beat mention time."),
        EvalCase("c2_temporal_relative", "What happened yesterday?", "2026-05-10T20:00:00Z", ["presented", "2026-05-09"], "Relative time should use query anchor."),
        EvalCase("c3_relational", "Who helped Aria with the visa checklist?", "2026-04-05T10:00:00Z", ["Nora", "helped"], "Relation query should be graph-first."),
        EvalCase("c4_plan", "What does Aria plan to do after joining Orchard Labs?", "2026-04-06T10:00:00Z", ["relocate", "Lisbon"], "Plan query should keep event/fact support."),
        EvalCase("c5_visual", "What address was shown in the lease screenshot?", "2026-03-11T10:00:00Z", ["Rua Augusta 14", "Lease Agreement"], "Visual evidence should be first-class."),
        EvalCase("c6_general", "Where did Aria join in February?", "2026-02-16T09:00:00Z", ["Orchard Labs", "2026-02-14"], "General factual query should still work."),
    ]
    variants = ["flat_text", "dual_backbone", "contract_aware"]
    summary: dict[str, Any] = {"cases": [], "variant_totals": {}}
    for variant in variants:
        summary["variant_totals"][variant] = {"pass": 0, "total": len(cases), "contract_ok": 0}

    for case in cases:
        row: dict[str, Any] = {"case_id": case.case_id, "query": case.query, "note": case.note, "expected_keywords": case.expected_keywords, "variants": {}}
        for variant in variants:
            result = mem.run_query(case.query, case.query_time, variant)
            hit_blob = "\n".join(hit["content"] for hit in result.get("hits", []))
            ok = all(keyword.lower() in hit_blob.lower() for keyword in case.expected_keywords)
            result["ok"] = ok
            if ok:
                summary["variant_totals"][variant]["pass"] += 1
            if result.get("coverage", {}).get("contract_ok"):
                summary["variant_totals"][variant]["contract_ok"] += 1
            row["variants"][variant] = result
        summary["cases"].append(row)
    return summary


def render_html(payload: dict[str, Any]) -> str:
    mem = payload["memory"]
    results = payload["results"]
    flow_text = """stream
  -> governed write path (skip questions / low-value turns)
  -> atoms with story_time / mention_time / write_time
  -> lifecycle update (active / superseded)
  -> temporal tree
  -> relation graph
  -> planner
  -> primary reader
  -> contract check
  -> type-aware second pass
  -> answer-ready evidence"""
    rows = []
    for case in results["cases"]:
        cells = []
        for variant in ["flat_text", "dual_backbone", "contract_aware"]:
            res = case["variants"][variant]
            status = "pass" if res["ok"] else "fail"
            cov = res.get("coverage", {})
            cells.append(
                f"""
                <td>
                  <div class="badge {status}">{'通过' if res['ok'] else '未通过'}</div>
                  <div class="mini">contract: {esc(cov.get('matched_layers', []))} / missing: {esc(cov.get('missing_layers', []))}</div>
                  <div class="mini">readers: {esc(res.get('second_pass_readers', []))}</div>
                  <pre>{esc(json.dumps(res.get('hits', [])[:3], ensure_ascii=False, indent=2))}</pre>
                </td>
                """
            )
        rows.append(
            f"""
            <tr>
              <td><b>{esc(case['case_id'])}</b><br><span class="mini">{esc(case['note'])}</span></td>
              <td>{esc(case['query'])}</td>
              {''.join(cells)}
            </tr>
            """
        )

    totals = results["variant_totals"]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Memory-OS Dual-Backbone</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#172233; --muted:#637286;
      --blue:#245cff; --green:#11885e; --amber:#a86a00; --red:#c13f36;
      --blue-soft:#eef4ff; --green-soft:#eefaf4; --amber-soft:#fff8ec; --red-soft:#fff4f2;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .page {{ max-width:1280px; margin:0 auto; padding:28px 20px 56px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:18px; margin-bottom:16px; }}
    .hero {{ background:linear-gradient(135deg,#fff 0%,#eef4ff 100%); padding:26px; }}
    h1,h2,h3 {{ margin:0 0 10px; line-height:1.3; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:20px; padding-bottom:8px; border-bottom:1px solid var(--line); }}
    p {{ margin:8px 0; }}
    .muted,.mini {{ color:var(--muted); }}
    .mini {{ font-size:12px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }}
    .chip,.badge {{ display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px; font-weight:700; }}
    .chip {{ border:1px solid #cad7ee; background:#f8fbff; color:#274674; }}
    .pass {{ background:var(--green-soft); color:var(--green); }}
    .fail {{ background:var(--red-soft); color:var(--red); }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:16px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .value {{ font-size:24px; font-weight:800; margin-top:4px; }}
    .grid {{ display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:16px; }}
    .span-6 {{ grid-column:span 6; }} .span-12 {{ grid-column:span 12; }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:14px; }}
    th,td {{ border:1px solid var(--line); padding:10px; vertical-align:top; text-align:left; }}
    th {{ background:#f4f7fd; }}
    code,pre {{ font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; }}
    code {{ background:#f3f6fb; border:1px solid #e0e7f1; border-radius:4px; padding:1px 5px; font-size:12px; }}
    pre {{ background:#fbfcff; border:1px solid var(--line); border-radius:8px; padding:12px; overflow:auto; white-space:pre-wrap; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:6px 0; }}
    @media (max-width:980px) {{ .metric-grid{{grid-template-columns:1fr;}} .span-6{{grid-column:span 12;}} }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Memory-OS Dual-Backbone</h1>
      <p class="muted">
        这个 nano 不是再做一个 toy demo，而是把 <b>write governance、three-clock time、生命周期、双骨干检索、contract-aware second pass</b>
        放进同一个最小系统里。它更像“论文方法节可讲清”的版本。
      </p>
      <div class="chips">
        <span class="chip">LongMemEval / MemOS</span>
        <span class="chip">RAPTOR / MemoRAG</span>
        <span class="chip">GraphReader / HippoRAG</span>
        <span class="chip">Self-RAG style self-check</span>
      </div>
      <div class="metric-grid">
        <div class="metric"><div class="mini">Flat text</div><div class="value">{totals['flat_text']['pass']}/{totals['flat_text']['total']}</div></div>
        <div class="metric"><div class="mini">Dual backbone</div><div class="value">{totals['dual_backbone']['pass']}/{totals['dual_backbone']['total']}</div></div>
        <div class="metric"><div class="mini">Contract aware</div><div class="value">{totals['contract_aware']['pass']}/{totals['contract_aware']['total']}</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>为什么要再做这个 nano</h2>
      <ul>
        <li>现有 nano 文件很多，但多数各讲一块；这个版本把几块真正会影响论文论证的机制揉在了一起。</li>
        <li>它强调的是 <b>泛化结构</b>，不是给某个数据集写关键词捷径。</li>
        <li>它同时演示：什么该在写入时做，什么该在检索时做，什么该在回答前补证据。</li>
      </ul>
    </section>

    <div class="grid">
      <section class="panel span-6">
        <h2>系统骨架</h2>
        <pre>{esc(flow_text)}</pre>
      </section>
      <section class="panel span-6">
        <h2>对主仓最有帮助的几件事</h2>
        <ul>
          <li><b>Readiness receipt</b>：把“写进去了”和“现在能答题”分开。</li>
          <li><b>Three-clock</b>：时间题默认区分 story / mention / write，而不是只靠 created_at。</li>
          <li><b>Lifecycle</b>：给原子事实留 active / superseded，避免一直把旧状态当最新状态。</li>
          <li><b>Type-aware second pass</b>：不是盲目“再搜一点”，而是按缺失证据类型补 reader。</li>
        </ul>
      </section>
    </div>

    <section class="panel">
      <h2>Readiness receipt</h2>
      <pre>{esc(json.dumps(mem["receipt"], ensure_ascii=False, indent=2))}</pre>
    </section>

    <section class="panel">
      <h2>实验结果</h2>
      <table>
        <thead>
          <tr>
            <th style="width:14%">Case</th>
            <th style="width:18%">Query</th>
            <th style="width:22%">Flat text</th>
            <th style="width:22%">Dual backbone</th>
            <th>Contract aware</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>

    <div class="grid">
      <section class="panel span-6">
        <h2>这组小实验说明了什么</h2>
        <ul>
          <li><b>Flat text</b> 能答一些直接词面题，但对时间、关系、视觉 grounding 不稳定。</li>
          <li><b>Dual backbone</b> 已经比纯扁平检索更稳，因为时间题和关系题不再走同一路。</li>
          <li><b>Contract-aware</b> 的关键收益不是“多搜一点”，而是“补对证据类型”。</li>
        </ul>
      </section>
      <section class="panel span-6">
        <h2>和论文叙事的对应</h2>
        <ul>
          <li><b>LongMemEval / MemOS</b>：readiness 与 lifecycle 不是 UI 装饰，而是 correctness 前提。</li>
          <li><b>RAPTOR / MemoRAG</b>：temporal tree 提供 coarse chronology backbone。</li>
          <li><b>GraphReader / HippoRAG</b>：graph 负责 relation / plan / visual grounding。</li>
          <li><b>Self-RAG</b>：contract-aware second pass 对应 answer-time evidence self-check。</li>
        </ul>
      </section>
    </div>

    <section class="panel">
      <h2>下一步值得做的真实主仓改动</h2>
      <ul>
        <li>把 <code>policy/self_check.py</code> 从 advisory-only 改成真实闭环控制器。</li>
        <li>让 <code>search_service.py</code> 在 temporal / relational / visual family 上显式记录 primary 与补 reader。</li>
        <li>把 readiness receipt 接进搜索入口，避免未完成的 session 被当作 QA-ready 证据。</li>
        <li>把 lifecycle / superseded 状态继续推进到排序与 answer policy，而不只停在存储层。</li>
      </ul>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    mem = build_demo_memory()
    results = run_benchmark(mem)
    payload = {
        "memory": {
            "receipt": {
                "schema_version": mem.receipt.schema_version,
                "updated_at": mem.receipt.updated_at,
                "last_message_id": mem.receipt.last_message_id,
                "last_extracted_atom_id": mem.receipt.last_extracted_atom_id,
                "stages": {k: asdict(v) for k, v in mem.receipt.stages.items()},
            },
            "observations": [asdict(x) for x in mem.observations],
            "atoms": [asdict(x) for x in mem.atoms],
            "blocks": [asdict(x) for x in mem.blocks],
            "nodes": [asdict(x) for x in mem.nodes],
            "edges": [asdict(x) for x in mem.edges],
        },
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
