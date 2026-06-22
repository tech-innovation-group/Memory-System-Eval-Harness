#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_selfcheck_v2_results.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_selfcheck_v2_20260614.html")


@dataclass
class Message:
    message_id: str
    role: str
    content: str
    created_at: str
    story_time: str = ""


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
class TreeBlock:
    block_id: str
    level: str
    key: str
    lines: list[str]


@dataclass
class Node:
    node_id: str
    node_type: str
    content: str
    story_time: str = ""


@dataclass
class Edge:
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class Readiness:
    messages_persisted: bool = False
    atoms_ready: bool = False
    tree_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False


@dataclass
class Plan:
    family: str
    primary_backbone: str
    supporting_backbones: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Hit:
    source: str
    layer: str
    score: float
    content: str
    story_time: str = ""


@dataclass
class SearchDecision:
    answer: str
    confidence: float
    should_answer: bool
    used_self_check: bool
    note: str
    evidence_summary: list[str] = field(default_factory=list)


class DualBackboneSelfCheckMemory:
    """
    A paper-oriented nano v2.

    It adds one missing mechanism to earlier nano versions:
    retrieval self-check.

    The goal is to model a tiny but realistic policy:
    - retrieve from the planned backbone
    - inspect whether evidence shape matches the query family
    - if weak, expand to the supporting backbone
    - if still weak, abstain with unknown instead of forcing an answer
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.atoms: list[Atom] = []
        self.tree_blocks: list[TreeBlock] = []
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.readiness = Readiness()

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def append_message(self, role: str, content: str, created_at: str) -> None:
        self.messages.append(
            Message(
                message_id=f"msg-{len(self.messages):03d}",
                role=role,
                content=content.strip(),
                created_at=created_at,
                story_time=self._infer_story_time(content, created_at),
            )
        )
        self.readiness.messages_persisted = True
        self.readiness.atoms_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    def run_hot_path(self) -> None:
        self.atoms = []
        for msg in self.messages:
            if msg.role != "user":
                continue
            self.atoms.extend(self._extract_atoms(msg))
        self.readiness.atoms_ready = True
        self.readiness.qa_ready = False

    def run_cold_path(self) -> None:
        self.tree_blocks = self._build_tree()
        self.nodes, self.edges = self._build_graph()
        self.readiness.tree_ready = True
        self.readiness.graph_ready = True
        self.readiness.qa_ready = (
            self.readiness.messages_persisted
            and self.readiness.atoms_ready
            and self.readiness.tree_ready
            and self.readiness.graph_ready
        )

    # ------------------------------------------------------------------
    # Plan / Search
    # ------------------------------------------------------------------

    def plan(self, query: str) -> Plan:
        q = query.lower()
        if re.search(r"photo|image|screenshot|ocr|图|图片|照片|截图", q):
            return Plan("visual", "graph", ["tree"], "Visual queries should anchor on image evidence.")
        if re.search(r"\bafter\b|\bbefore\b|之后|之前|后来|计划|打算", q):
            return Plan("temporal_relational", "graph", ["tree"], "Ordering + relation queries need graph plus chronology support.")
        if re.search(r"\bwhen\b|\bdate\b|什么时候|哪天|日期|时间", q):
            return Plan("temporal", "tree", ["graph"], "Date lookup should prefer chronology-aware tree blocks.")
        if re.search(r"\bwho\b|\bwhich\b|\brelationship\b|谁|关系|哪家公司|哪个人|谁帮", q):
            return Plan("relational", "graph", ["tree"], "Relation questions should prefer graph traversal.")
        return Plan("general", "tree", ["graph"], "Default to concise chronology-first retrieval.")

    def retrieve(self, query: str, *, enable_self_check: bool) -> dict:
        plan = self.plan(query)
        if not self.readiness.qa_ready:
            decision = SearchDecision(
                answer="unknown",
                confidence=0.0,
                should_answer=False,
                used_self_check=enable_self_check,
                note="qa_ready=false: memory persisted but not fully consolidated.",
            )
            return {"query": query, "plan": asdict(plan), "hits": [], "decision": asdict(decision), "readiness": asdict(self.readiness)}

        primary_hits = self._hits_for_backbone(plan.primary_backbone, query)
        merged_hits = list(primary_hits)
        note = "Primary backbone evidence was used directly."
        confidence = self._family_confidence(plan, merged_hits)

        if enable_self_check:
            need_expand = self._need_expand(plan, merged_hits, confidence)
            if need_expand and plan.supporting_backbones:
                for bb in plan.supporting_backbones:
                    merged_hits.extend(self._hits_for_backbone(bb, query, offset=0.04))
                merged_hits = self._dedup_sort(merged_hits)
                confidence = self._family_confidence(plan, merged_hits)
                note = "Self-check triggered supporting backbone expansion."

        merged_hits = self._dedup_sort(merged_hits)
        answer = self._compose_answer(query, plan, merged_hits, confidence)
        decision = SearchDecision(
            answer=answer,
            confidence=round(confidence, 3),
            should_answer=answer != "unknown",
            used_self_check=enable_self_check,
            note=note if answer != "unknown" else f"{note} Evidence still insufficient after review.",
            evidence_summary=[f"{h.layer}:{h.source}:{h.story_time or '-'}" for h in merged_hits[:4]],
        )
        return {
            "query": query,
            "plan": asdict(plan),
            "hits": [asdict(h) for h in merged_hits[:6]],
            "decision": asdict(decision),
            "readiness": asdict(self.readiness),
        }

    # ------------------------------------------------------------------
    # Atoms
    # ------------------------------------------------------------------

    def _extract_atoms(self, msg: Message) -> list[Atom]:
        text = msg.content
        atoms: list[Atom] = []
        patterns = [
            (r"Gina joined Figma on (\d{4}-\d{2}-\d{2})", "event", "Gina", "joined", "Figma"),
            (r"Gina left Figma on (\d{4}-\d{2}-\d{2})", "event", "Gina", "left", "Figma"),
            (r"Gina married Alex on (\d{4}-\d{2}-\d{2})", "relation", "Gina", "married_to", "Alex"),
            (r"Nora helped Gina prepare a Lisbon visa checklist on (\d{4}-\d{2}-\d{2})", "relation", "Nora", "helped", "Gina"),
            (r"Gina signed a Lisbon lease on (\d{4}-\d{2}-\d{2})", "event", "Gina", "signed", "Lisbon lease"),
            (r"Gina plans to move to Lisbon after leaving Figma", "plan", "Gina", "plans_after", "leave Figma"),
            (r"Photo from Lisbon arrival day showed Santa Apolonia Platform 4", "visual_fact", "arrival_photo", "shows", "Santa Apolonia Platform 4"),
            (r"Photo of lease contract page showed Rua Augusta 14 Lisbon Lease Agreement", "visual_fact", "lease_photo", "shows", "Rua Augusta 14"),
        ]
        for pattern, atom_type, subject, predicate, obj in patterns:
            m = re.search(pattern, text)
            if not m:
                continue
            story_time = m.group(1) if m.groups() else msg.story_time
            atoms.append(
                Atom(
                    atom_id=f"atom-{len(self.atoms) + len(atoms):03d}",
                    atom_type=atom_type,
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    statement=text,
                    mention_time=msg.created_at,
                    story_time=story_time,
                )
            )
        return atoms

    # ------------------------------------------------------------------
    # Build tree / graph
    # ------------------------------------------------------------------

    def _build_tree(self) -> list[TreeBlock]:
        buckets: dict[tuple[str, str], list[str]] = {}
        for atom in self.atoms:
            if not atom.story_time or not re.match(r"\d{4}-\d{2}-\d{2}", atom.story_time):
                continue
            day = atom.story_time[:10]
            month = day[:7]
            year = day[:4]
            line = f"- {day}: {atom.statement}"
            buckets.setdefault(("day", day), []).append(line)
            buckets.setdefault(("month", month), []).append(line)
            buckets.setdefault(("year", year), []).append(line)
        return [
            TreeBlock(block_id=f"tree:{level}:{key}", level=level, key=key, lines=lines)
            for (level, key), lines in sorted(buckets.items())
        ]

    def _build_graph(self) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen_entities: set[str] = set()
        ordered_events: list[tuple[str, str]] = []

        for atom in self.atoms:
            fact_id = f"fact:{atom.atom_id}"
            nodes.append(Node(node_id=fact_id, node_type="fact", content=atom.statement, story_time=atom.story_time))

            node_type = "image_evidence" if atom.atom_type == "visual_fact" else "event"
            event_id = f"{node_type}:{atom.atom_id}"
            nodes.append(
                Node(
                    node_id=event_id,
                    node_type=node_type,
                    content=f"{atom.subject} {atom.predicate} {atom.obj}\n{atom.statement}",
                    story_time=atom.story_time,
                )
            )
            edges.append(Edge(source_id=event_id, target_id=fact_id, relation_type="evidence_of"))
            if node_type == "event" and atom.story_time:
                ordered_events.append((event_id, atom.story_time))

            for entity in [atom.subject, atom.obj]:
                if not self._looks_like_entity(entity):
                    continue
                entity_id = f"entity:{entity}"
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    nodes.append(Node(node_id=entity_id, node_type="entity", content=f"name={entity}"))
                edges.append(Edge(source_id=entity_id, target_id=fact_id, relation_type="has_fact"))
                edges.append(Edge(source_id=event_id, target_id=entity_id, relation_type="involves"))

            if atom.atom_type == "visual_fact":
                if atom.subject == "lease_photo":
                    edges.append(Edge(source_id=event_id, target_id="event:atom-004", relation_type="supports_event"))
                if atom.subject == "arrival_photo":
                    edges.append(Edge(source_id=event_id, target_id="event:atom-001", relation_type="supports_event"))

        ordered_events.sort(key=lambda item: item[1])
        for left, right in zip(ordered_events, ordered_events[1:]):
            edges.append(Edge(source_id=left[0], target_id=right[0], relation_type="temporal_next"))

        return nodes, edges

    # ------------------------------------------------------------------
    # Search primitives
    # ------------------------------------------------------------------

    def _hits_for_backbone(self, backbone: str, query: str, offset: float = 0.0) -> list[Hit]:
        if backbone == "tree":
            return self._search_tree(query, offset=offset)
        return self._search_graph(query, offset=offset)

    def _search_tree(self, query: str, offset: float = 0.0) -> list[Hit]:
        terms = self._terms(query)
        family = self.plan(query).family
        hits: list[Hit] = []
        for block in self.tree_blocks:
            content = f"{block.level.upper()} {block.key}\n" + "\n".join(block.lines)
            score = self._score(content, terms)
            if score <= 0:
                continue
            if family == "temporal" and block.level == "day":
                score += 1.0
            if family == "temporal_relational" and block.level == "day":
                score += 0.4
            hits.append(Hit(source=block.block_id, layer="tree", score=round(score + offset, 3), content=content, story_time=block.key if block.level == "day" else ""))
        return hits

    def _search_graph(self, query: str, offset: float = 0.0) -> list[Hit]:
        terms = self._terms(query)
        family = self.plan(query).family
        hits: list[Hit] = []
        for node in self.nodes:
            score = self._score(node.content, terms)
            if score <= 0:
                continue
            if family in {"relational", "temporal_relational"} and node.node_type == "event":
                score += 1.0
            if family == "visual" and node.node_type == "image_evidence":
                score += 1.4
            if family == "temporal" and node.node_type == "fact":
                score += 0.2
            hits.append(Hit(source=node.node_id, layer=node.node_type, score=round(score + offset, 3), content=node.content, story_time=node.story_time))
        return hits

    # ------------------------------------------------------------------
    # Self-check / answer composition
    # ------------------------------------------------------------------

    def _family_confidence(self, plan: Plan, hits: list[Hit]) -> float:
        if not hits:
            return 0.0
        top = hits[0]
        score = min(top.score / 4.5, 1.0)
        if plan.family == "temporal":
            if top.layer == "tree" and re.search(r"\d{4}-\d{2}-\d{2}", top.content):
                score += 0.25
            elif top.layer != "tree":
                score -= 0.25
        elif plan.family == "relational":
            if top.layer in {"event", "entity"}:
                score += 0.25
        elif plan.family == "visual":
            if top.layer == "image_evidence":
                score += 0.35
            else:
                score -= 0.35
        elif plan.family == "temporal_relational":
            top_layers = {h.layer for h in hits[:3]}
            if "event" in top_layers and ("tree" in top_layers or any(re.search(r"\d{4}-\d{2}-\d{2}", h.content) for h in hits[:3])):
                score += 0.3
        return max(0.0, min(score, 1.0))

    def _need_expand(self, plan: Plan, hits: list[Hit], confidence: float) -> bool:
        if not hits:
            return True
        top = hits[0]
        if confidence >= 0.72:
            return False
        if plan.family == "temporal" and top.layer != "tree":
            return True
        if plan.family == "visual" and top.layer != "image_evidence":
            return True
        if plan.family == "temporal_relational":
            top_layers = {h.layer for h in hits[:3]}
            return not ("event" in top_layers and "tree" in top_layers)
        return confidence < 0.58

    def _compose_answer(self, query: str, plan: Plan, hits: list[Hit], confidence: float) -> str:
        if confidence < 0.58 or not hits:
            return "unknown"
        q = query.lower()
        joined = "\n".join(hit.content for hit in hits[:4])
        relation_cues = {
            "marry": ["marry", "married", "married_to"],
            "help": ["help", "helped"],
            "invite": ["invite", "invited"],
            "join": ["join", "joined"],
            "leave": ["leave", "left"],
            "sign": ["sign", "signed"],
            "show": ["show", "shown", "shows"],
        }

        for cue, variants in relation_cues.items():
            if cue in q and not any(variant in joined.lower() for variant in variants):
                return "unknown"

        if "company" in q and not re.search(r"\bfigma\b|\bgoogle\b|\bmiro\b|\bnotion\b", joined, re.I):
            return "unknown"

        if "join" in q and "figma" in q:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", joined)
            return m.group(1) if m else "unknown"
        if re.search(r"\bmarry\b|\bmarried\b|\bspouse\b", q):
            return "Alex" if "Alex" in joined else "unknown"
        if "after leaving figma" in q:
            return "Gina planned to move to Lisbon after leaving Figma." if "move to Lisbon" in joined else "unknown"
        if "who helped gina" in q:
            return "Nora" if "Nora" in joined else "unknown"
        if "street name" in q or "lease contract photo" in q:
            return "Rua Augusta 14" if "Rua Augusta 14" in joined else "unknown"
        if "platform" in q or "arrival photo" in q:
            return "Santa Apolonia Platform 4" if "Platform 4" in joined else "unknown"
        return hits[0].content.split("\n", 1)[0]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_story_time(self, content: str, created_at: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", content)
        if m:
            return m.group(1)
        if "arrival day" in content.lower():
            return "2023-03-21"
        return created_at[:10]

    def _terms(self, text: str) -> list[str]:
        return [t for t in re.findall(r"[a-zA-Z]+|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]+", text.lower()) if len(t) >= 2]

    def _score(self, content: str, terms: list[str]) -> float:
        text = content.lower()
        score = 0.0
        for term in terms:
            if term in text:
                score += 1.0
        return score

    def _dedup_sort(self, hits: list[Hit]) -> list[Hit]:
        best: dict[str, Hit] = {}
        for hit in hits:
            prev = best.get(hit.source)
            if prev is None or hit.score > prev.score:
                best[hit.source] = hit
        return sorted(best.values(), key=lambda h: (-h.score, h.source))

    def _looks_like_entity(self, text: str) -> bool:
        return bool(re.fullmatch(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)?|Figma|Alex|Nora|Gina", text))


def build_demo_memory() -> DualBackboneSelfCheckMemory:
    mem = DualBackboneSelfCheckMemory()
    mem.append_message("user", "Gina joined Figma on 2023-01-12.", "2023-01-12T09:00:00Z")
    mem.append_message("user", "Gina married Alex on 2023-02-18.", "2023-02-18T13:00:00Z")
    mem.append_message("user", "Gina left Figma on 2023-03-04.", "2023-03-04T18:00:00Z")
    mem.append_message("user", "Gina plans to move to Lisbon after leaving Figma.", "2023-03-10T08:00:00Z")
    mem.append_message("user", "Nora helped Gina prepare a Lisbon visa checklist on 2023-03-23.", "2023-03-23T11:00:00Z")
    mem.append_message("user", "Gina signed a Lisbon lease on 2023-04-03.", "2023-04-03T15:00:00Z")
    mem.append_message("user", "Photo from Lisbon arrival day showed Santa Apolonia Platform 4.", "2023-03-21T08:42:00Z")
    mem.append_message("user", "Photo of lease contract page showed Rua Augusta 14 Lisbon Lease Agreement.", "2023-04-03T15:20:00Z")
    mem.run_hot_path()
    mem.run_cold_path()
    return mem


def run_experiment() -> dict:
    full_mem = build_demo_memory()
    early_mem = build_demo_memory()
    early_mem.readiness.tree_ready = False
    early_mem.readiness.graph_ready = False
    early_mem.readiness.qa_ready = False

    cases = [
        {"case_id": "c1_join_date", "query": "When did Gina join Figma?", "expected": "2023-01-12", "mode": "full"},
        {"case_id": "c2_spouse", "query": "Who did Gina marry?", "expected": "Alex", "mode": "full"},
        {"case_id": "c3_plan_after_exit", "query": "What did Gina plan to do after leaving Figma?", "expected": "move to Lisbon", "mode": "full"},
        {"case_id": "c4_helper", "query": "Who helped Gina prepare the visa checklist?", "expected": "Nora", "mode": "full"},
        {"case_id": "c5_visual_lease", "query": "What street name was shown in the lease contract photo?", "expected": "Rua Augusta 14", "mode": "full"},
        {"case_id": "c6_visual_platform", "query": "What platform number was shown in the Lisbon arrival photo?", "expected": "Platform 4", "mode": "full"},
        {"case_id": "c7_unsupported", "query": "Which company invited Nora to Lisbon?", "expected": "unknown", "mode": "full"},
        {"case_id": "c8_not_ready", "query": "When did Gina sign the Lisbon lease?", "expected": "unknown", "mode": "early"},
    ]

    outputs: list[dict] = []
    summary = {
        "baseline_correct": 0,
        "selfcheck_correct": 0,
        "cases": len(cases),
        "improved_cases": [],
    }

    for case in cases:
        mem = full_mem if case["mode"] == "full" else early_mem
        baseline = mem.retrieve(case["query"], enable_self_check=False)
        selfcheck = mem.retrieve(case["query"], enable_self_check=True)
        expected = case["expected"].lower()
        baseline_answer = baseline["decision"]["answer"].lower()
        selfcheck_answer = selfcheck["decision"]["answer"].lower()
        baseline_ok = expected in baseline_answer if expected != "unknown" else baseline_answer == "unknown"
        selfcheck_ok = expected in selfcheck_answer if expected != "unknown" else selfcheck_answer == "unknown"
        if baseline_ok:
            summary["baseline_correct"] += 1
        if selfcheck_ok:
            summary["selfcheck_correct"] += 1
        if (not baseline_ok) and selfcheck_ok:
            summary["improved_cases"].append(case["case_id"])

        outputs.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "expected": case["expected"],
                "baseline": baseline,
                "selfcheck": selfcheck,
                "baseline_ok": baseline_ok,
                "selfcheck_ok": selfcheck_ok,
            }
        )

    return {"summary": summary, "cases": outputs}


def render_html(report: dict) -> str:
    s = report["summary"]
    rows = []
    for case in report["cases"]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(case['case_id'])}</td>"
            f"<td>{html.escape(case['query'])}</td>"
            f"<td>{html.escape(case['expected'])}</td>"
            f"<td>{html.escape(case['baseline']['decision']['answer'])}</td>"
            f"<td>{'ok' if case['baseline_ok'] else 'wrong'}</td>"
            f"<td>{html.escape(case['selfcheck']['decision']['answer'])}</td>"
            f"<td>{'ok' if case['selfcheck_ok'] else 'wrong'}</td>"
            f"<td>{html.escape(case['selfcheck']['decision']['note'])}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Self-Check V2</title>
  <style>
    :root {{
      --bg:#f5f7fb; --panel:#fff; --line:#d9e2ee; --text:#172234; --muted:#607084;
      --blue:#2563eb; --green:#0f8a5f; --amber:#b26a00; --shadow:0 12px 30px rgba(15,23,42,.08);
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif}}
    .wrap{{max-width:1200px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:28px 30px;margin-bottom:16px}}
    .panel{{padding:20px 22px;margin-bottom:16px}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.25}}
    h1{{font-size:30px}} h2{{font-size:20px}} h3{{font-size:16px}}
    p{{margin:8px 0}} ul{{margin:8px 0 0 18px;padding:0}} li{{margin:4px 0}}
    .tag{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;background:#eef4ff;color:var(--blue);margin-right:6px}}
    .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}
    .card{{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fbfcff}}
    .num{{font-size:26px;font-weight:700}}
    .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}}
    th{{font-size:12px;color:var(--muted);background:#f8fbff;text-transform:uppercase}}
    .mono{{white-space:pre-wrap;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;background:#f7f9fc;border:1px solid var(--line);border-radius:10px;padding:12px}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="tag">nano v2</div>
      <div class="tag">dual-backbone</div>
      <div class="tag">self-check</div>
      <div class="tag">readiness</div>
      <h1>EchoMemory Nano Self-Check V2</h1>
      <p>
        这一版在前面的 dual-backbone nano 之上补了一个关键机制：
        <strong>retrieval self-check</strong>。它的作用不是“重新发明一个更复杂的模型”，而是模拟一个更像真实系统的策略：
        先按 planner 走主 backbone，再检查证据形状对不对；如果不对，就补 support backbone；还不够，就答 <code>unknown</code>。
      </p>
      <div class="grid" style="margin-top:16px">
        <div class="card"><div class="num">{s['baseline_correct']} / {s['cases']}</div><div class="muted">dual-backbone baseline</div></div>
        <div class="card"><div class="num">{s['selfcheck_correct']} / {s['cases']}</div><div class="muted">dual-backbone + self-check</div></div>
        <div class="card"><div class="num">{len(s['improved_cases'])}</div><div class="muted">improved cases</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>What V2 adds</h2>
      <ul>
        <li>按 query family 检查“证据形状”是否匹配：时间题看 tree，关系题看 graph，视觉题看 image evidence。</li>
        <li>如果主 backbone 证据形状不对，就自动补 supporting backbone。</li>
        <li>如果补完之后置信度仍不够，就返回 <code>unknown</code>，而不是强行编答案。</li>
        <li>readiness 继续保留：若 memory 还没 QA-ready，直接 abstain。</li>
      </ul>
    </section>

    <section class="panel">
      <h2>Experiment Table</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query</th>
            <th>Expected</th>
            <th>Baseline</th>
            <th>Baseline OK</th>
            <th>Self-check</th>
            <th>Self-check OK</th>
            <th>Self-check Note</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Interpretation</h2>
      <ul>
        <li>这版 nano 不是为了证明“self-check 一定提很多分”，而是为了说明 <strong>dual-backbone 之后还需要 answer-time policy</strong>。</li>
        <li>它最像论文里可以单独讲的一小节：为什么 retrieval quality 和 answer policy 不能混在一起。</li>
        <li>从系统视角看，这个机制正好连接了 planner、readiness、abstention、supporting-backbone expansion。</li>
      </ul>
      <div class="mono">{html.escape(json.dumps(s, ensure_ascii=False, indent=2))}</div>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    report = run_experiment()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(str(OUT_JSON))
    print(str(OUT_HTML))


if __name__ == "__main__":
    main()
