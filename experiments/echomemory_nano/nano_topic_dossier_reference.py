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
OUT_JSON = ROOT / "nano_topic_dossier_reference_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_topic_dossier_reference_20260616.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def shift_day(ymd: str, delta: int) -> str:
    dt = datetime.fromisoformat(ymd)
    return (dt + timedelta(days=delta)).strftime("%Y-%m-%d")


def normalize_date(text: str, anchor: str) -> str:
    explicit = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if explicit:
        return explicit.group(1)
    lowered = text.lower()
    if "yesterday" in lowered or "昨天" in text:
        return shift_day(anchor, -1)
    if "last week" in lowered or "上周" in text:
        return shift_day(anchor, -7)
    if "tomorrow" in lowered or "明天" in text:
        return shift_day(anchor, 1)
    return anchor


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]{2,}|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", text.lower()))


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta:
        return 0.0
    return len(ta & tb) / max(len(ta), 1)


@dataclass
class Observation:
    obs_id: str
    role: str
    modality: str
    content: str
    mention_time: str
    write_time: str
    event_time: str
    topic_hint: str = ""
    caption: str = ""
    ocr: str = ""


@dataclass
class Atom:
    atom_id: str
    atom_type: str
    topic: str
    statement: str
    event_time: str
    mention_time: str
    write_time: str
    source_obs_id: str
    entities: list[str] = field(default_factory=list)


@dataclass
class TopicDossier:
    topic: str
    summary: str
    start_time: str
    end_time: str
    atom_ids: list[str]
    entities: list[str]
    timeline: list[str]


@dataclass
class QueryPlan:
    family: str
    primary_reader: str
    supporting_readers: list[str]
    required_evidence: list[str]
    reason: str


@dataclass
class Readiness:
    persisted: bool = False
    atoms_ready: bool = False
    dossier_ready: bool = False
    tree_ready: bool = False
    graph_ready: bool = False
    qa_ready: bool = False


class TopicDossierReferenceNano:
    """
    A minimal teaching implementation for the v13 story.

    This version is intentionally small and generic:
    - append-only stream
    - three-clock time
    - atom extraction
    - topic dossier middle layer
    - temporal tree + relation graph
    - contract-driven retrieval
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.atoms: list[Atom] = []
        self.dossiers: dict[str, TopicDossier] = {}
        self.temporal_tree: dict[str, list[str]] = {}
        self.graph: dict[str, list[dict[str, str]]] = {}
        self.readiness = Readiness()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def append_text(self, *, role: str, content: str, write_time: str, topic_hint: str = "") -> None:
        event_time = normalize_date(content, write_time[:10])
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role=role,
                modality="text",
                content=content.strip(),
                mention_time=write_time,
                write_time=write_time,
                event_time=event_time,
                topic_hint=topic_hint.strip(),
            )
        )
        self.readiness.persisted = True
        self.readiness.atoms_ready = False
        self.readiness.dossier_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    def append_image(self, *, role: str, caption: str, ocr: str, write_time: str, topic_hint: str = "") -> None:
        merged = "\n".join(x for x in [caption.strip(), ocr.strip()] if x)
        event_time = normalize_date(merged, write_time[:10])
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role=role,
                modality="image",
                content=merged,
                mention_time=write_time,
                write_time=write_time,
                event_time=event_time,
                topic_hint=topic_hint.strip(),
                caption=caption.strip(),
                ocr=ocr.strip(),
            )
        )
        self.readiness.persisted = True
        self.readiness.atoms_ready = False
        self.readiness.dossier_ready = False
        self.readiness.tree_ready = False
        self.readiness.graph_ready = False
        self.readiness.qa_ready = False

    # ------------------------------------------------------------------
    # Build path
    # ------------------------------------------------------------------

    def build(self) -> None:
        self.atoms = self._extract_atoms()
        self.readiness.atoms_ready = True
        self.dossiers = self._build_topic_dossiers()
        self.readiness.dossier_ready = bool(self.dossiers)
        self.temporal_tree = self._build_temporal_tree()
        self.readiness.tree_ready = bool(self.temporal_tree)
        self.graph = self._build_graph()
        self.readiness.graph_ready = bool(self.graph)
        self.readiness.qa_ready = (
            self.readiness.persisted
            and self.readiness.atoms_ready
            and self.readiness.dossier_ready
            and self.readiness.tree_ready
            and self.readiness.graph_ready
        )

    def _extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for obs in self.observations:
            topic = obs.topic_hint or self._infer_topic(obs.content)
            if obs.modality == "image":
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type="image_evidence",
                        topic=topic,
                        statement=obs.content,
                        event_time=obs.event_time,
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                        entities=self._extract_entities(obs.content),
                    )
                )
                continue

            for sent in self._split_sentences(obs.content):
                atom_type = self._classify_atom(sent)
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        atom_type=atom_type,
                        topic=topic,
                        statement=sent,
                        event_time=normalize_date(sent, obs.write_time[:10]),
                        mention_time=obs.mention_time,
                        write_time=obs.write_time,
                        source_obs_id=obs.obs_id,
                        entities=self._extract_entities(sent),
                    )
                )
        return atoms

    def _build_topic_dossiers(self) -> dict[str, TopicDossier]:
        grouped: dict[str, list[Atom]] = {}
        for atom in self.atoms:
            grouped.setdefault(atom.topic, []).append(atom)
        dossiers: dict[str, TopicDossier] = {}
        for topic, atoms in grouped.items():
            ordered = sorted(atoms, key=lambda x: (x.event_time, x.write_time, x.atom_id))
            entities: list[str] = []
            seen: set[str] = set()
            for atom in ordered:
                for ent in atom.entities:
                    if ent not in seen:
                        seen.add(ent)
                        entities.append(ent)
            summary_lines = [f"Topic: {topic}", f"Span: {ordered[0].event_time} -> {ordered[-1].event_time}", "Key updates:"]
            for atom in ordered[:5]:
                summary_lines.append(f"- {atom.statement}")
            dossiers[topic] = TopicDossier(
                topic=topic,
                summary="\n".join(summary_lines),
                start_time=ordered[0].event_time,
                end_time=ordered[-1].event_time,
                atom_ids=[a.atom_id for a in ordered],
                entities=entities[:10],
                timeline=[f"{a.event_time}: {a.statement}" for a in ordered[:8]],
            )
        return dossiers

    def _build_temporal_tree(self) -> dict[str, list[str]]:
        tree: dict[str, list[str]] = {}
        for atom in self.atoms:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", atom.event_time):
                continue
            y, m, d = atom.event_time.split("-")
            for key in [y, f"{y}-{m}", atom.event_time]:
                tree.setdefault(key, []).append(f"{atom.atom_id}:{atom.statement}")
        return tree

    def _build_graph(self) -> dict[str, list[dict[str, str]]]:
        graph: dict[str, list[dict[str, str]]] = {"nodes": [], "edges": []}
        for atom in self.atoms:
            graph["nodes"].append({"id": f"atom:{atom.atom_id}", "type": atom.atom_type, "topic": atom.topic})
            if atom.atom_type == "image_evidence":
                graph["nodes"].append({"id": f"image:{atom.atom_id}", "type": "image_evidence", "topic": atom.topic})
                graph["edges"].append({"source": f"image:{atom.atom_id}", "target": f"atom:{atom.atom_id}", "type": "evidence_of"})
            if atom.topic:
                graph["nodes"].append({"id": f"topic:{atom.topic}", "type": "topic"})
                graph["edges"].append({"source": f"topic:{atom.topic}", "target": f"atom:{atom.atom_id}", "type": "has_atom"})
            for ent in atom.entities:
                graph["nodes"].append({"id": f"entity:{ent}", "type": "entity"})
                graph["edges"].append({"source": f"entity:{ent}", "target": f"atom:{atom.atom_id}", "type": "mentions"})
        return graph

    # ------------------------------------------------------------------
    # Query path
    # ------------------------------------------------------------------

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"(latest|status|progress|evolve|evolution|how did|变化|进展|最新|状态|演化)", q):
            return QueryPlan(
                family="longitudinal",
                primary_reader="topic_dossier",
                supporting_readers=["temporal_tree", "graph"],
                required_evidence=["topic_dossier", "fact"],
                reason="longitudinal queries need a middle-layer topic object first",
            )
        if re.search(r"(when|yesterday|last week|before|after|时间|日期|昨天|上周|之前|之后)", q):
            return QueryPlan(
                family="temporal",
                primary_reader="temporal_tree",
                supporting_readers=["graph"],
                required_evidence=["temporal_tree", "event_time"],
                reason="time queries should start from chronology, not summary",
            )
        if re.search(r"(who|relationship|introduced|helped|contact|关系|介绍|帮助|联系)", q):
            return QueryPlan(
                family="relational",
                primary_reader="graph",
                supporting_readers=["temporal_tree", "topic_dossier"],
                required_evidence=["graph", "path_grounding"],
                reason="relation-heavy queries need graph path grounding",
            )
        if re.search(r"(photo|image|screenshot|图|截图|照片|图片|address|地址)", q):
            return QueryPlan(
                family="visual",
                primary_reader="graph",
                supporting_readers=["temporal_tree", "topic_dossier"],
                required_evidence=["image_evidence", "fact"],
                reason="visual queries need first-class image evidence",
            )
        return QueryPlan(
            family="general",
            primary_reader="topic_dossier",
            supporting_readers=["graph", "temporal_tree"],
            required_evidence=["fact"],
            reason="general queries can use the middle layer plus support",
        )

    def search(self, query: str, query_time: str) -> dict[str, Any]:
        plan = self.plan(query)
        if not self.readiness.qa_ready:
            return {
                "query": query,
                "plan": asdict(plan),
                "readiness": asdict(self.readiness),
                "allowed_to_answer": False,
                "note": "pipeline is not QA-ready",
                "hits": [],
            }
        hits = self._retrieve(plan, query, query_time)
        present = self._present_evidence(hits)
        missing = [x for x in plan.required_evidence if x not in present]
        if missing:
            for reader in plan.supporting_readers:
                hits.extend(self._retrieve_reader(reader, query, query_time, bonus=0.05))
            present = self._present_evidence(hits)
            missing = [x for x in plan.required_evidence if x not in present]
        return {
            "query": query,
            "plan": asdict(plan),
            "readiness": asdict(self.readiness),
            "allowed_to_answer": not missing,
            "missing": missing,
            "hits": sorted(hits, key=lambda x: x["score"], reverse=True),
        }

    # ------------------------------------------------------------------
    # Retrieval helpers
    # ------------------------------------------------------------------

    def _retrieve(self, plan: QueryPlan, query: str, query_time: str) -> list[dict[str, Any]]:
        if plan.primary_reader == "topic_dossier":
            return self._retrieve_topic_dossier(query)
        if plan.primary_reader == "temporal_tree":
            return self._retrieve_temporal_tree(query, query_time)
        if plan.primary_reader == "graph":
            return self._retrieve_graph(query)
        return self._retrieve_topic_dossier(query) + self._retrieve_graph(query) + self._retrieve_temporal_tree(query, query_time)

    def _retrieve_reader(self, reader: str, query: str, query_time: str, bonus: float) -> list[dict[str, Any]]:
        if reader == "topic_dossier":
            return self._retrieve_topic_dossier(query, bonus=bonus)
        if reader == "temporal_tree":
            return self._retrieve_temporal_tree(query, query_time, bonus=bonus)
        if reader == "graph":
            return self._retrieve_graph(query, bonus=bonus)
        return []

    def _retrieve_topic_dossier(self, query: str, bonus: float = 0.12) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for topic, dossier in self.dossiers.items():
            score = overlap(query, dossier.summary) + bonus
            hits.append({
                "reader": "topic_dossier",
                "topic": topic,
                "score": round(score, 3),
                "content": dossier.summary,
                "atom_ids": dossier.atom_ids,
                "timeline": dossier.timeline,
            })
        return sorted(hits, key=lambda x: x["score"], reverse=True)[:4]

    def _retrieve_temporal_tree(self, query: str, query_time: str, bonus: float = 0.08) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for key, lines in self.temporal_tree.items():
            score = overlap(query, key) + bonus + (0.02 if key == query_time[:10] else 0.0)
            hits.append({
                "reader": "temporal_tree",
                "topic": key,
                "score": round(score, 3),
                "content": "\n".join(lines[:4]),
            })
        return sorted(hits, key=lambda x: x["score"], reverse=True)[:4]

    def _retrieve_graph(self, query: str, bonus: float = 0.08) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for node in self.graph.get("nodes", []):
            score = overlap(query, node.get("id", "")) + bonus
            if node.get("type") == "entity":
                score += 0.03
            hits.append({
                "reader": "graph",
                "topic": node.get("topic", ""),
                "score": round(score, 3),
                "content": node["id"],
                "node_type": node.get("type", ""),
            })
        return sorted(hits, key=lambda x: x["score"], reverse=True)[:4]

    def _present_evidence(self, hits: list[dict[str, Any]]) -> set[str]:
        present: set[str] = set()
        for hit in hits:
            reader = hit.get("reader")
            if reader == "topic_dossier":
                present.add("topic_dossier")
                present.add("fact")
            elif reader == "temporal_tree":
                present.add("temporal_tree")
                if "20" in str(hit.get("content", "")):
                    present.add("event_time")
            elif reader == "graph":
                present.add("graph")
                present.add("fact")
                if "entity:" in str(hit.get("content", "")):
                    present.add("path_grounding")
                if hit.get("node_type") == "image_evidence":
                    present.add("image_evidence")
        return present

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------

    def _infer_topic(self, text: str) -> str:
        for pat in [
            r"topic[:：]\s*([A-Za-z0-9\u4e00-\u9fff _-]{2,40})",
            r"([A-Za-z0-9\u4e00-\u9fff _-]{2,40})\s*(evolved|changed|progress|status|最新|进展|状态)",
        ]:
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1).strip()
        return "general"

    def _classify_atom(self, text: str) -> str:
        if re.search(r"\b(signed|joined|left|met|moved|approved|delayed|launched)\b|昨天|上周|之前|之后|参加|离开|批准|推迟|启动", text, re.I):
            return "event"
        if re.search(r"\b(helped|introduced|works with|married|relationship|联系|介绍|帮助|关系)\b", text, re.I):
            return "relation"
        if re.search(r"\b(plan|intend|prepare|should|will)\b|计划|打算|准备", text, re.I):
            return "plan"
        return "fact"

    def _split_sentences(self, text: str) -> list[str]:
        parts = [p.strip() for p in re.split(r"[。.!?？]\s*", text) if p.strip()]
        return parts or ([text.strip()] if text.strip() else [])

    def _extract_entities(self, text: str) -> list[str]:
        ents = re.findall(r"\b[A-Z][a-zA-Z]+\b|[\u4e00-\u9fff]{2,4}", text)
        seen: list[str] = []
        for ent in ents:
            if ent not in seen:
                seen.append(ent)
        return seen[:8]


def demo() -> dict[str, Any]:
    nano = TopicDossierReferenceNano()
    nano.append_text(role="user", content="Topic: apartment lease We found an apartment on Rua Augusta 14.", write_time="2026-03-01T10:00:00+00:00", topic_hint="apartment lease")
    nano.append_text(role="user", content="The landlord asked for a signed lease draft.", write_time="2026-03-02T10:00:00+00:00", topic_hint="apartment lease")
    nano.append_image(role="user", caption="lease screenshot", ocr="Rua Augusta 14 move-in 2026-03-20", write_time="2026-03-05T10:00:00+00:00", topic_hint="apartment lease")
    nano.append_text(role="user", content="The landlord delayed the handover by one week, so the move-in moved to 2026-03-27.", write_time="2026-03-12T10:00:00+00:00", topic_hint="apartment lease")

    nano.append_text(role="user", content="Topic: visa process Maya started the visa paperwork and Nora helped collect the financial statements.", write_time="2026-03-02T11:00:00+00:00", topic_hint="visa process")
    nano.append_text(role="user", content="The consulate requested an extra residence document, so the visa process was delayed.", write_time="2026-03-18T11:00:00+00:00", topic_hint="visa process")
    nano.append_text(role="user", content="The visa was approved after Maya submitted the missing residence document.", write_time="2026-03-18T12:00:00+00:00", topic_hint="visa process")

    nano.build()
    cases = [
        ("q1", "How did the apartment lease situation evolve?", "2026-03-13T10:00:00+00:00"),
        ("q2", "What is the latest status of the visa process?", "2026-03-19T10:00:00+00:00"),
        ("q3", "What address was shown in the lease screenshot?", "2026-03-05T10:00:00+00:00"),
        ("q4", "Who helped collect the financial statements?", "2026-03-03T10:00:00+00:00"),
    ]
    outputs = []
    for qid, query, query_time in cases:
        outputs.append({"qid": qid, "query": query, "result": nano.search(query, query_time)})
    result = {
        "readiness": asdict(nano.readiness),
        "observations": [asdict(o) for o in nano.observations],
        "atoms": [asdict(a) for a in nano.atoms],
        "dossiers": {k: asdict(v) for k, v in nano.dossiers.items()},
        "temporal_tree": nano.temporal_tree,
        "graph": nano.graph,
        "cases": outputs,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    render_html(result)
    return result


def render_html(result: dict[str, Any]) -> None:
    def li(items: list[str]) -> str:
        return "".join(f"<li>{esc(x)}</li>" for x in items)

    rows = []
    for case in result["cases"]:
        res = case["result"]
        hits = res.get("hits", [])
        hit_html = "".join(
            f"<li><code>{esc(h.get('reader',''))}</code> [{esc(h.get('topic',''))}] score={esc(h.get('score',''))} {esc(h.get('content',''))}</li>"
            for h in hits
        )
        rows.append(
            f"""
            <section class='panel'>
              <h2>{esc(case['qid'])} · {esc(case['query'])}</h2>
              <p class='muted'>plan: {esc(res['plan']['family'])} | primary: {esc(res['plan']['primary_reader'])} | allowed: {esc(res['allowed_to_answer'])}</p>
              <div class='grid'>
                <div class='card'>
                  <h3>Readiness</h3>
                  <pre>{esc(json.dumps(res['readiness'], ensure_ascii=False, indent=2))}</pre>
                </div>
                <div class='card'>
                  <h3>Hits</h3>
                  <ul>{hit_html}</ul>
                </div>
              </div>
            </section>
            """
        )
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Topic Dossier Reference</title>
  <style>
    :root{{--bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#172235;--muted:#617186;--blue:#245cff;--code:#f4f7fb}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1180px;margin:0 auto;padding:24px 18px 56px}}
    .hero,.panel,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}}
    .hero{{padding:26px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.3}} h1{{font-size:28px}} h2{{font-size:20px}} h3{{font-size:16px}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}} ul,ol{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    pre{{white-space:pre-wrap;word-break:break-word;background:var(--code);border:1px solid #e0e7f1;border-radius:8px;padding:12px;margin:8px 0 0}}
    code{{background:var(--code);border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano Topic Dossier Reference</h1>
      <p class="muted">A minimal teaching version for the v13 topic_dossier story.</p>
      <ul>
        <li>append-only stream</li>
        <li>three-clock time</li>
        <li>topic dossier middle layer</li>
        <li>temporal tree + graph retrieval</li>
        <li>contract-driven second pass</li>
      </ul>
    </section>
    <section class="panel">
      <h2>1. Readiness</h2>
      <pre>{esc(json.dumps(result["readiness"], ensure_ascii=False, indent=2))}</pre>
    </section>
    <section class="panel">
      <h2>2. Topic dossiers</h2>
      <pre>{esc(json.dumps(result["dossiers"], ensure_ascii=False, indent=2))}</pre>
    </section>
    {''.join(rows)}
  </div>
</body>
</html>"""
    OUT_HTML.write_text(html_doc)


if __name__ == "__main__":
    demo()
