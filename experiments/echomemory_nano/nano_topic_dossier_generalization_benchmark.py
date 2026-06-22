#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_topic_dossier_generalization_benchmark_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_topic_dossier_generalization_benchmark_20260616.html"
)


def esc(v: Any) -> str:
    return html.escape(str(v))


def tok(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]{2,}|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", text.lower()))


def overlap(a: str, b: str) -> float:
    ta, tb = tok(a), tok(b)
    if not ta:
        return 0.0
    return len(ta & tb) / max(len(ta), 1)


def top_score(query: str, content: str, boost: float = 0.0) -> float:
    return round(overlap(query, content) + boost, 3)


@dataclass
class Turn:
    turn_id: str
    session_id: str
    write_time: str
    role: str
    topic: str
    content: str


@dataclass
class Atom:
    atom_id: str
    topic: str
    statement: str
    event_time: str
    session_id: str
    source_turn_id: str
    entities: list[str] = field(default_factory=list)


@dataclass
class TopicDossier:
    topic: str
    summary: str
    start_time: str
    end_time: str
    entities: list[str]
    atom_ids: list[str]
    timeline: list[str]


@dataclass
class QueryCase:
    qid: str
    question: str
    family: str
    expected_topic: str
    expected_phrases: list[str]
    expected_atom_ids: list[str]


class TopicDossierGeneralizationBenchmark:
    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.atoms: list[Atom] = []
        self.dossiers: dict[str, TopicDossier] = {}
        self.overview: str = ""

    def append(self, *, session_id: str, write_time: str, role: str, topic: str, content: str) -> None:
        self.turns.append(
            Turn(
                turn_id=f"turn-{len(self.turns):03d}",
                session_id=session_id,
                write_time=write_time,
                role=role,
                topic=topic,
                content=content.strip(),
            )
        )

    def build(self) -> None:
        self.atoms = self._extract_atoms()
        self.dossiers = self._build_dossiers()
        self.overview = self._build_overview()

    def _extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for turn in self.turns:
            parts = [p.strip() for p in re.split(r"[。.!?？]", turn.content) if p.strip()]
            for part in parts:
                ents = re.findall(r"\b[A-Z][a-zA-Z]+\b|[\u4e00-\u9fff]{2,4}", part)
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        topic=turn.topic,
                        statement=part,
                        event_time=turn.write_time[:10],
                        session_id=turn.session_id,
                        source_turn_id=turn.turn_id,
                        entities=ents[:8],
                    )
                )
        return atoms

    def _build_dossiers(self) -> dict[str, TopicDossier]:
        grouped: dict[str, list[Atom]] = defaultdict(list)
        for atom in self.atoms:
            grouped[atom.topic].append(atom)
        dossiers: dict[str, TopicDossier] = {}
        for topic, atoms in grouped.items():
            atoms = sorted(atoms, key=lambda x: x.event_time)
            entities: list[str] = []
            seen: set[str] = set()
            for atom in atoms:
                for ent in atom.entities:
                    if ent not in seen:
                        seen.add(ent)
                        entities.append(ent)
            timeline = [f"{a.event_time}: {a.statement}" for a in atoms[:6]]
            summary = "\n".join(
                [
                    f"Topic: {topic}",
                    f"Span: {atoms[0].event_time} -> {atoms[-1].event_time}",
                    "Key updates:",
                    *[f"- {a.statement}" for a in atoms[:5]],
                ]
            )
            dossiers[topic] = TopicDossier(
                topic=topic,
                summary=summary,
                start_time=atoms[0].event_time,
                end_time=atoms[-1].event_time,
                entities=entities[:8],
                atom_ids=[a.atom_id for a in atoms],
                timeline=timeline,
            )
        return dossiers

    def _build_overview(self) -> str:
        return " | ".join(
            f"{topic}: {dossier.summary.replace(chr(10), ' ')}"
            for topic, dossier in sorted(self.dossiers.items())
        )

    def plan(self, query: str) -> dict[str, Any]:
        q = query.lower()
        longitudinal = bool(re.search(r"(evolve|progress|latest|status|change|timeline|how did|发展|变化|进展|最新|状态|过程)", q))
        relational = bool(re.search(r"(who|helped|introduced|contact|through whom|谁|帮助|介绍|联系)", q))
        visual = bool(re.search(r"(screenshot|image|photo|shown|address|截图|图片|照片|地址)", q))
        if visual:
            return {"family": "visual", "primary": "topic_dossier", "support": ["atoms"], "required": ["topic_dossier", "fact"]}
        if longitudinal:
            return {"family": "longitudinal", "primary": "topic_dossier", "support": ["atoms"], "required": ["topic_dossier", "timeline", "fact"]}
        if relational:
            return {"family": "relational", "primary": "atoms", "support": ["topic_dossier"], "required": ["fact", "entity"]}
        return {"family": "general", "primary": "atoms", "support": ["topic_dossier"], "required": ["fact"]}

    def run_mode(self, mode: str, case: QueryCase) -> dict[str, Any]:
        plan = self.plan(case.question)
        topic = self._select_topic(case.question)
        plan["topic"] = topic
        hits: list[dict[str, Any]] = []
        readers: list[str] = []

        if mode == "overview_only":
            readers.append("overview")
            hits.append({"kind": "overview", "topic": "all", "content": self.overview, "score": top_score(case.question, self.overview)})
        elif mode == "atom_only":
            readers.append("atoms")
            hits.extend(self._retrieve_atoms(case.question, topic, limit=5))
        elif mode == "topic_dossier":
            readers.append("topic_dossier")
            dossier = self.dossiers.get(topic)
            if dossier is not None:
                hits.append(
                    {
                        "kind": "topic_dossier",
                        "topic": topic,
                        "content": dossier.summary,
                        "score": top_score(case.question, dossier.summary, boost=0.25),
                        "timeline": dossier.timeline,
                        "atom_ids": dossier.atom_ids,
                    }
                )
            readers.append("atoms")
            hits.extend(self._retrieve_atoms(case.question, topic, limit=4))
        elif mode == "contract_topic_dossier":
            readers.append("topic_dossier")
            dossier = self.dossiers.get(topic)
            if dossier is not None:
                hits.append(
                    {
                        "kind": "topic_dossier",
                        "topic": topic,
                        "content": dossier.summary,
                        "score": top_score(case.question, dossier.summary, boost=0.25),
                        "timeline": dossier.timeline,
                        "atom_ids": dossier.atom_ids,
                    }
                )
            readers.append("atoms")
            atom_hits = self._retrieve_atoms(case.question, topic, limit=5)
            hits.extend(atom_hits)
            if plan["family"] == "longitudinal" and "topic_dossier" not in self._present(hits):
                dossier = self.dossiers.get(topic)
                if dossier is not None:
                    hits.append(
                        {
                            "kind": "topic_dossier",
                            "topic": topic,
                            "content": dossier.summary,
                            "score": top_score(case.question, dossier.summary, boost=0.35),
                            "timeline": dossier.timeline,
                            "atom_ids": dossier.atom_ids,
                        }
                    )
        else:
            raise ValueError(mode)

        hits = sorted(hits, key=lambda x: x.get("score", 0.0), reverse=True)
        answer = self._synthesize(mode, plan, case, hits)
        success = self._grade(answer, case)
        evidence_atom_ids: list[str] = []
        for hit in hits:
            if hit["kind"] == "atom":
                evidence_atom_ids.append(hit["atom_id"])
            elif hit["kind"] == "topic_dossier":
                evidence_atom_ids.extend(hit.get("atom_ids", []))
        evidence_atom_ids = list(dict.fromkeys(evidence_atom_ids))

        return {
            "qid": case.qid,
            "question": case.question,
            "plan": plan,
            "mode": mode,
            "readers": readers,
            "answer": answer,
            "success": success,
            "hits": hits,
            "covered_atoms": sorted(set(evidence_atom_ids) & set(case.expected_atom_ids)),
            "missing_atoms": sorted(set(case.expected_atom_ids) - set(evidence_atom_ids)),
            "present_evidence": sorted(self._present(hits)),
        }

    def _present(self, hits: list[dict[str, Any]]) -> set[str]:
        present: set[str] = set()
        for hit in hits:
            if hit["kind"] == "topic_dossier":
                present |= {"topic_dossier", "fact"}
            elif hit["kind"] == "atom":
                present.add("fact")
                if hit.get("topic"):
                    present.add("entity")
                    present.add("timeline")
            elif hit["kind"] == "overview":
                present.add("fact")
        return present

    def _synthesize(self, mode: str, plan: dict[str, Any], case: QueryCase, hits: list[dict[str, Any]]) -> str:
        if not hits:
            return "unknown"
        if mode == "overview_only":
            return hits[0]["content"][:220]
        if plan["family"] == "longitudinal":
            dossier_hit = next((h for h in hits if h["kind"] == "topic_dossier"), None)
            atom_hits = [h for h in hits if h["kind"] == "atom"][:3]
            if dossier_hit is None:
                return "unknown"
            lines = [f"{plan['topic']} evolved as follows:"]
            for item in dossier_hit.get("timeline", [])[:3]:
                lines.append(f"- {item}")
            for item in atom_hits:
                line = f"- {item['event_time']}: {item['content']}"
                if line not in lines:
                    lines.append(line)
            return "\n".join(lines)
        if plan["family"] == "visual":
            atom_hit = next((h for h in hits if "address" in h["content"].lower() or "地址" in h["content"]), hits[0])
            return atom_hit["content"]
        if plan["family"] == "relational":
            return hits[0]["content"]
        return hits[0]["content"]

    @staticmethod
    def _grade(answer: str, case: QueryCase) -> bool:
        lowered = answer.lower()
        return all(phrase.lower() in lowered for phrase in case.expected_phrases)

    def _select_topic(self, query: str) -> str:
        scored = []
        for topic, dossier in self.dossiers.items():
            score = overlap(query, topic) + overlap(query, dossier.summary)
            scored.append((score, topic))
        scored.sort(reverse=True)
        return scored[0][1] if scored else "general"

    def _retrieve_atoms(self, query: str, topic: str, limit: int) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for atom in self.atoms:
            topic_bonus = 0.18 if atom.topic == topic else 0.0
            score = overlap(query, atom.statement) + topic_bonus
            if score <= 0:
                continue
            hits.append(
                {
                    "kind": "atom",
                    "atom_id": atom.atom_id,
                    "topic": atom.topic,
                    "content": atom.statement,
                    "score": round(score, 3),
                    "event_time": atom.event_time,
                }
            )
        hits.sort(key=lambda item: item["score"], reverse=True)
        return hits[:limit]


def build_demo() -> tuple[TopicDossierGeneralizationBenchmark, list[QueryCase]]:
    bench = TopicDossierGeneralizationBenchmark()
    # apartment lease
    bench.append(session_id="s1", write_time="2026-03-01T09:00:00", role="user", topic="apartment lease", content="We found an apartment on Rua Augusta 14. The landlord asked for a signed lease draft.")
    bench.append(session_id="s2", write_time="2026-03-05T10:00:00", role="user", topic="apartment lease", content="The lease screenshot showed the address Rua Augusta 14 and a move-in date of 2026-03-20.")
    bench.append(session_id="s3", write_time="2026-03-12T11:00:00", role="user", topic="apartment lease", content="The landlord delayed the handover by one week, so the move-in moved to 2026-03-27.")
    # visa
    bench.append(session_id="s1", write_time="2026-03-02T09:30:00", role="user", topic="visa process", content="Maya started the visa paperwork and Nora helped collect the financial statements.")
    bench.append(session_id="s2", write_time="2026-03-09T10:30:00", role="user", topic="visa process", content="The consulate requested an extra residence document, so the visa process was delayed.")
    bench.append(session_id="s3", write_time="2026-03-18T12:30:00", role="user", topic="visa process", content="The visa was approved after Maya submitted the missing residence document.")
    # product launch
    bench.append(session_id="s1", write_time="2026-03-03T15:00:00", role="user", topic="product launch", content="The team planned the beta launch for 2026-04-10.")
    bench.append(session_id="s2", write_time="2026-03-11T15:30:00", role="user", topic="product launch", content="A payment bug pushed the launch back to 2026-04-24.")
    bench.append(session_id="s3", write_time="2026-03-19T16:00:00", role="user", topic="product launch", content="The payment fix landed and the launch date was confirmed for 2026-04-24.")
    # parenting / family
    bench.append(session_id="s1", write_time="2026-03-04T09:15:00", role="user", topic="family support", content="My mother moved in to help with childcare after the baby was born.")
    bench.append(session_id="s2", write_time="2026-03-10T09:15:00", role="user", topic="family support", content="The childcare plan shifted to include a nanny and my mother on weekdays.")
    bench.append(session_id="s3", write_time="2026-03-21T09:15:00", role="user", topic="family support", content="We kept the childcare plan because the work schedule became more intense.")
    bench.build()

    cases = [
        QueryCase("q1", "How did the apartment lease situation evolve?", "longitudinal", "apartment lease", ["Rua Augusta 14", "2026-03-27"], ["atom-000", "atom-002", "atom-004"]),
        QueryCase("q2", "What is the latest status of the visa process?", "longitudinal", "visa process", ["visa was approved", "missing residence document"], ["atom-006", "atom-008", "atom-010"]),
        QueryCase("q3", "Who helped with the visa paperwork?", "relational", "visa process", ["Nora", "helped collect the financial statements"], ["atom-006"]),
        QueryCase("q4", "What address was shown in the lease screenshot?", "visual", "apartment lease", ["Rua Augusta 14"], ["atom-002"]),
        QueryCase("q5", "When did the product launch settle on its final date?", "longitudinal", "product launch", ["2026-04-24", "payment fix landed"], ["atom-012", "atom-014", "atom-016"]),
        QueryCase("q6", "How did the childcare plan evolve?", "longitudinal", "family support", ["nanny", "weekday"], ["atom-018", "atom-020", "atom-022"]),
    ]
    return bench, cases


def render(results: dict[str, Any]) -> str:
    mode_rows = []
    for mode_name, mode_result in results["modes"].items():
        mode_rows.append(
            f"<tr><td>{esc(mode_name)}</td><td>{mode_result['correct']}/{mode_result['total']}</td><td>{mode_result['accuracy']:.2%}</td><td>{esc(mode_result['summary'])}</td></tr>"
        )

    case_sections = []
    for case in results["cases"]:
        cards = []
        for run in case["runs"]:
            hits_html = "".join(
                f"<li><code>{esc(hit['kind'])}</code> [{esc(hit.get('topic',''))}] score={hit.get('score',0)} {esc(hit['content'])}</li>"
                for hit in run["hits"][:5]
            )
            cards.append(
                "<div class='mode-card'>"
                f"<h4>{esc(run['mode'])} {'✅' if run['success'] else '❌'}</h4>"
                f"<p><b>Readers:</b> {esc(', '.join(run['readers']))}</p>"
                f"<p><b>Present:</b> {esc(', '.join(run['present_evidence'])) or 'none'}</p>"
                f"<p><b>Answer:</b><br>{esc(run['answer'])}</p>"
                f"<p><b>Covered atoms:</b> {esc(', '.join(run['covered_atoms']) or 'none')}</p>"
                f"<p><b>Missing atoms:</b> {esc(', '.join(run['missing_atoms']) or 'none')}</p>"
                f"<ul>{hits_html}</ul>"
                "</div>"
            )
        case_sections.append(
            "<section class='panel'>"
            f"<h3>{esc(case['qid'])} · {esc(case['question'])}</h3>"
            f"<p class='muted'>Expected topic: {esc(case['expected_topic'])} | Family: {esc(case['family'])}</p>"
            f"<div class='mode-grid'>{''.join(cards)}</div>"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Topic Dossier Generalization Benchmark</title>
  <style>
    :root {{ --bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#172233;--muted:#617186;--blue:#245cff;--green:#10895f;--amber:#9a6200; }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1180px;margin:0 auto;padding:24px 18px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}}
    .hero{{padding:26px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    h1,h2,h3,h4{{margin:0 0 10px;line-height:1.3}} h1{{font-size:28px}} h2{{font-size:20px}} h3{{font-size:17px}} h4{{font-size:15px}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}} ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{border:1px solid var(--line);padding:10px;vertical-align:top;text-align:left}} th{{background:#f4f7fd}}
    code{{background:#f3f6fb;border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px}}
    .callout{{border-left:4px solid var(--blue);background:#f4f8ff;padding:12px 14px;border-radius:8px;margin-top:12px}}
    .mode-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}} .mode-card{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcff}}
    @media (max-width:980px){{.mode-grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Topic Dossier Generalization Benchmark</h1>
      <p class="muted">
        A broader generic benchmark for comparing overview, atoms, topic dossiers, and contract-aware topic dossiers.
        No dataset-specific keywords are used in the retrieval logic.
      </p>
      <div class="callout">
        This benchmark adds a new topic (<b>family support</b>) and compares four modes:
        <code>overview_only</code>, <code>atom_only</code>, <code>topic_dossier</code>, and <code>contract_topic_dossier</code>.
      </div>
    </section>

    <section class="panel">
      <h2>1. Mode summary</h2>
      <table>
        <thead><tr><th>Mode</th><th>Correct</th><th>Accuracy</th><th>Interpretation</th></tr></thead>
        <tbody>{''.join(mode_rows)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>2. Architectural takeaway</h2>
      <ul>
        <li><code>overview_only</code> is broad and tends to blend topics.</li>
        <li><code>atom_only</code> is precise but fragmentary.</li>
        <li><code>topic_dossier</code> improves longitudinal coherence.</li>
        <li><code>contract_topic_dossier</code> is the most explicit about evidence completeness.</li>
      </ul>
    </section>

    {''.join(case_sections)}
  </div>
</body>
</html>"""


def main() -> None:
    bench, cases = build_demo()
    modes = {m: [] for m in ("overview_only", "atom_only", "topic_dossier", "contract_topic_dossier")}
    case_rows = []
    for case in cases:
        runs = []
        for mode in modes:
            run = bench.run_mode(mode, case)
            modes[mode].append(run)
            runs.append(run)
        case_rows.append({"qid": case.qid, "question": case.question, "family": case.family, "expected_topic": case.expected_topic, "runs": runs})

    summary = {}
    for mode, runs in modes.items():
        correct = sum(1 for r in runs if r["success"])
        summary[mode] = {
            "correct": correct,
            "total": len(runs),
            "accuracy": correct / max(len(runs), 1),
            "summary": {
                "overview_only": "broad summary baseline",
                "atom_only": "fragmentary atom baseline",
                "topic_dossier": "topic-centered middle layer",
                "contract_topic_dossier": "topic dossier plus explicit contract-aware fallback",
            }[mode],
        }

    result = {"modes": summary, "cases": case_rows, "readiness": {"topics": sorted(bench.dossiers.keys()), "atoms": len(bench.atoms)}}
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    OUT_HTML.write_text(render(result))


if __name__ == "__main__":
    main()
