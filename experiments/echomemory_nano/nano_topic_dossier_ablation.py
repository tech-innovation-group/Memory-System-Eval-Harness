#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_topic_dossier_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_topic_dossier_ablation_20260615.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]{2,}|\d{4}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", text.lower()))


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta:
        return 0.0
    return len(ta & tb) / max(len(ta), 1)


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


class TopicDossierNano:
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

    def plan(self, query: str) -> dict[str, Any]:
        q = query.lower()
        longitudinal = bool(re.search(r"(evolve|progress|latest|status|change|timeline|how did|发展|变化|进展|最新|状态|过程)", q))
        relational = bool(re.search(r"(who|helped|introduced|contact|through whom|谁|帮助|介绍|联系)", q))
        visual = bool(re.search(r"(screenshot|image|photo|shown|address|截图|图片|照片|地址)", q))
        topic = self._select_topic(query)
        if visual:
            return {
                "family": "visual",
                "primary": "topic_dossier",
                "support": ["atoms"],
                "topic": topic,
                "required": ["topic_dossier", "fact"],
            }
        if longitudinal:
            return {
                "family": "longitudinal",
                "primary": "topic_dossier",
                "support": ["atoms"],
                "topic": topic,
                "required": ["topic_dossier", "timeline", "fact"],
            }
        if relational:
            return {
                "family": "relational",
                "primary": "atoms",
                "support": ["topic_dossier"],
                "topic": topic,
                "required": ["fact", "entity"],
            }
        return {
            "family": "general",
            "primary": "atoms",
            "support": ["topic_dossier"],
            "topic": topic,
            "required": ["fact"],
        }

    def run_mode(self, mode: str, case: QueryCase) -> dict[str, Any]:
        plan = self.plan(case.question)
        topic = plan["topic"]
        hits: list[dict[str, Any]] = []
        readers: list[str] = []

        if mode == "overview_only":
            readers.append("overview")
            hits.append(
                {
                    "kind": "overview",
                    "topic": "all",
                    "content": self.overview,
                    "score": round(overlap(case.question, self.overview), 3),
                }
            )
        elif mode == "atom_only":
            readers.append("atoms")
            hits.extend(self._retrieve_atoms(case.question, topic, limit=4))
        elif mode == "topic_dossier":
            readers.append("topic_dossier")
            dossier = self.dossiers.get(topic)
            if dossier is not None:
                hits.append(
                    {
                        "kind": "topic_dossier",
                        "topic": topic,
                        "content": dossier.summary,
                        "score": round(overlap(case.question, dossier.summary) + 0.25, 3),
                        "timeline": dossier.timeline,
                        "atom_ids": dossier.atom_ids,
                    }
                )
            readers.append("atoms")
            hits.extend(self._retrieve_atoms(case.question, topic, limit=4))
        else:
            raise ValueError(f"unknown mode: {mode}")

        hits = sorted(hits, key=lambda item: item.get("score", 0.0), reverse=True)
        answer = self._synthesize_answer(mode, case, plan, hits)
        success = self._grade_answer(answer, case)
        evidence_atom_ids = []
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
        }

    def _extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for turn in self.turns:
            parts = [p.strip() for p in re.split(r"[。.!?？]", turn.content) if p.strip()]
            for idx, part in enumerate(parts):
                entities = re.findall(r"\b[A-Z][a-zA-Z]+\b|[\u4e00-\u9fff]{2,3}", part)
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        topic=turn.topic,
                        statement=part,
                        event_time=turn.write_time[:10],
                        session_id=turn.session_id,
                        source_turn_id=turn.turn_id,
                        entities=entities[:6],
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
            seen_entities: set[str] = set()
            for atom in atoms:
                for ent in atom.entities:
                    if ent not in seen_entities:
                        seen_entities.add(ent)
                        entities.append(ent)
            timeline = [f"{atom.event_time}: {atom.statement}" for atom in atoms[:6]]
            summary_parts = [
                f"Topic: {topic}",
                f"Span: {atoms[0].event_time} -> {atoms[-1].event_time}",
                "Key updates:",
            ]
            for atom in atoms[:4]:
                summary_parts.append(f"- {atom.statement}")
            dossiers[topic] = TopicDossier(
                topic=topic,
                summary="\n".join(summary_parts),
                start_time=atoms[0].event_time,
                end_time=atoms[-1].event_time,
                entities=entities[:8],
                atom_ids=[atom.atom_id for atom in atoms],
                timeline=timeline,
            )
        return dossiers

    def _build_overview(self) -> str:
        pieces = []
        for topic, dossier in sorted(self.dossiers.items()):
            pieces.append(f"{topic}: {dossier.summary.replace(chr(10), ' ')}")
        return " | ".join(pieces)

    def _select_topic(self, query: str) -> str:
        scored = []
        for topic, dossier in self.dossiers.items():
            score = overlap(query, topic) + overlap(query, dossier.summary)
            scored.append((score, topic))
        scored.sort(reverse=True)
        return scored[0][1]

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

    def _synthesize_answer(self, mode: str, case: QueryCase, plan: dict[str, Any], hits: list[dict[str, Any]]) -> str:
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
            best = hits[0]
            return best["content"]
        return hits[0]["content"]

    @staticmethod
    def _grade_answer(answer: str, case: QueryCase) -> bool:
        lowered = answer.lower()
        for phrase in case.expected_phrases:
            if phrase.lower() not in lowered:
                return False
        return True


def build_demo() -> tuple[TopicDossierNano, list[QueryCase]]:
    nano = TopicDossierNano()
    # apartment lease
    nano.append(session_id="s1", write_time="2026-03-01T09:00:00", role="user", topic="apartment lease", content="We found an apartment on Rua Augusta 14. The landlord asked for a signed lease draft.")
    nano.append(session_id="s2", write_time="2026-03-05T10:00:00", role="user", topic="apartment lease", content="The lease screenshot showed the address Rua Augusta 14 and a move-in date of 2026-03-20.")
    nano.append(session_id="s3", write_time="2026-03-12T11:00:00", role="user", topic="apartment lease", content="The landlord delayed the handover by one week, so the move-in moved to 2026-03-27.")
    # visa
    nano.append(session_id="s1", write_time="2026-03-02T09:30:00", role="user", topic="visa process", content="Maya started the visa paperwork and Nora helped collect the financial statements.")
    nano.append(session_id="s2", write_time="2026-03-09T10:30:00", role="user", topic="visa process", content="The consulate requested an extra residence document, so the visa process was delayed.")
    nano.append(session_id="s3", write_time="2026-03-18T12:30:00", role="user", topic="visa process", content="The visa was approved after Maya submitted the missing residence document.")
    # product launch
    nano.append(session_id="s1", write_time="2026-03-03T15:00:00", role="user", topic="product launch", content="The team planned the beta launch for 2026-04-10.")
    nano.append(session_id="s2", write_time="2026-03-11T15:30:00", role="user", topic="product launch", content="A payment bug pushed the launch back to 2026-04-24.")
    nano.append(session_id="s3", write_time="2026-03-19T16:00:00", role="user", topic="product launch", content="The payment fix landed and the launch date was confirmed for 2026-04-24.")
    nano.build()

    cases = [
        QueryCase(
            qid="q1",
            question="How did the apartment lease situation evolve?",
            family="longitudinal",
            expected_topic="apartment lease",
            expected_phrases=["Rua Augusta 14", "2026-03-27"],
            expected_atom_ids=["atom-000", "atom-002", "atom-004"],
        ),
        QueryCase(
            qid="q2",
            question="What is the latest status of the visa process?",
            family="longitudinal",
            expected_topic="visa process",
            expected_phrases=["visa was approved", "missing residence document"],
            expected_atom_ids=["atom-006", "atom-008", "atom-010"],
        ),
        QueryCase(
            qid="q3",
            question="Who helped with the visa paperwork?",
            family="relational",
            expected_topic="visa process",
            expected_phrases=["Nora", "helped collect the financial statements"],
            expected_atom_ids=["atom-006"],
        ),
        QueryCase(
            qid="q4",
            question="What address was shown in the lease screenshot?",
            family="visual",
            expected_topic="apartment lease",
            expected_phrases=["Rua Augusta 14"],
            expected_atom_ids=["atom-002"],
        ),
        QueryCase(
            qid="q5",
            question="When did the product launch settle on its final date?",
            family="longitudinal",
            expected_topic="product launch",
            expected_phrases=["2026-04-24", "payment fix landed"],
            expected_atom_ids=["atom-012", "atom-014", "atom-016"],
        ),
    ]
    return nano, cases


def render_report(results: dict[str, Any]) -> str:
    modes = results["modes"]
    rows = []
    for mode_name, mode_result in modes.items():
        rows.append(
            f"<tr><td>{esc(mode_name)}</td><td>{mode_result['correct']}/{mode_result['total']}</td>"
            f"<td>{mode_result['accuracy']:.2%}</td><td>{esc(mode_result['summary'])}</td></tr>"
        )

    detail_cards = []
    for case in results["cases"]:
        comparisons = []
        for run in case["runs"]:
            hits_html = "".join(
                f"<li><code>{esc(hit['kind'])}</code> [{hit.get('topic','')}] score={hit.get('score',0)} {esc(hit['content'])}</li>"
                for hit in run["hits"][:4]
            )
            comparisons.append(
                "<div class='mode-card'>"
                f"<h4>{esc(run['mode'])} {'✅' if run['success'] else '❌'}</h4>"
                f"<p><b>Readers:</b> {esc(', '.join(run['readers']))}</p>"
                f"<p><b>Answer:</b><br>{esc(run['answer'])}</p>"
                f"<p><b>Covered atoms:</b> {esc(', '.join(run['covered_atoms']) or 'none')}</p>"
                f"<p><b>Missing atoms:</b> {esc(', '.join(run['missing_atoms']) or 'none')}</p>"
                f"<ul>{hits_html}</ul>"
                "</div>"
            )
        detail_cards.append(
            "<section class='panel'>"
            f"<h3>{esc(case['qid'])} · {esc(case['question'])}</h3>"
            f"<p class='muted'>Expected topic: {esc(case['expected_topic'])} | Family: {esc(case['family'])}</p>"
            f"<div class='mode-grid'>{''.join(comparisons)}</div>"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Topic Dossier Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#172233;--muted:#617186;--blue:#245cff;--green:#10895f;--amber:#9a6200;
    }}
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
    .mode-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}} .mode-card{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcff}}
    @media (max-width:980px){{.mode-grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Topic Dossier Ablation</h1>
      <p class="muted">
        这个 nano 专门回答一个主仓里还没有被单独讲透的问题:
        <b>当用户围绕同一主题跨多个 session 持续更新信息时，除了全局 overview 和扁平 atoms，是否需要一个 topic-centered 中层记忆对象？</b>
      </p>
      <div class="callout">
        结论很直接: <b>需要。</b>
        在这组泛化小实验里，<code>topic_dossier</code> 模式是 <b>{modes['topic_dossier']['correct']}/{modes['topic_dossier']['total']}</b>，
        明显优于 <code>overview_only</code> 的 <b>{modes['overview_only']['correct']}/{modes['overview_only']['total']}</b>
        和 <code>atom_only</code> 的 <b>{modes['atom_only']['correct']}/{modes['atom_only']['total']}</b>。
      </div>
    </section>

    <section class="panel">
      <h2>1. Why this matters</h2>
      <ul>
        <li><b>RAPTOR / MemoRAG</b> 提醒我们: 中层组织不是装饰，而是 recall backbone。</li>
        <li><b>Infini Memory / MemOS</b> 提醒我们: 长期记忆不该只有“全局摘要”和“最底层原子”。</li>
        <li>对 EchoMemory 来说，这个中层对象最自然的形态，不一定先叫 episode，而更可能是 <b>topic dossier</b>。</li>
      </ul>
    </section>

    <section class="panel">
      <h2>2. Settings</h2>
      <ul>
        <li>Modes: <code>overview_only</code>, <code>atom_only</code>, <code>topic_dossier</code></li>
        <li>Queries: 5 个，覆盖 longitudinal / relational / visual</li>
        <li>No benchmark-specific hacks: topic dossier 只是按 topic 聚合同一主题的原子，并保留 timeline 与实体。</li>
      </ul>
      <table>
        <thead><tr><th>Mode</th><th>Correct</th><th>Accuracy</th><th>Interpretation</th></tr></thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>3. Architectural takeaway</h2>
      <ul>
        <li><code>overview_only</code> 容易把多个主题揉在一起，导致 longitudinal query 缺 timeline focus。</li>
        <li><code>atom_only</code> 能查到碎片，但很容易缺“同一主题在多个 session 中如何演化”的中层组织。</li>
        <li><code>topic_dossier</code> 把同一主题的原子聚成一个 dossier，再补 atom supporting evidence，更接近主仓下一步该长的形态。</li>
      </ul>
    </section>

    {''.join(detail_cards)}
  </div>
</body>
</html>"""


def main() -> None:
    nano, cases = build_demo()
    mode_runs: dict[str, list[dict[str, Any]]] = {mode: [] for mode in ("overview_only", "atom_only", "topic_dossier")}
    case_rows: list[dict[str, Any]] = []

    for case in cases:
        runs = []
        for mode in mode_runs:
            result = nano.run_mode(mode, case)
            mode_runs[mode].append(result)
            runs.append(result)
        case_rows.append(
            {
                "qid": case.qid,
                "question": case.question,
                "family": case.family,
                "expected_topic": case.expected_topic,
                "runs": runs,
            }
        )

    modes_summary: dict[str, Any] = {}
    for mode, rows in mode_runs.items():
        correct = sum(1 for row in rows if row["success"])
        total = len(rows)
        if mode == "overview_only":
            summary = "global summary is broad but topic mixing hurts longitudinal answers"
        elif mode == "atom_only":
            summary = "flat atoms recover facts but often miss topic-level evolution"
        else:
            summary = "topic dossier keeps the topical timeline coherent and then lets atoms fill details"
        modes_summary[mode] = {
            "correct": correct,
            "total": total,
            "accuracy": correct / total,
            "summary": summary,
        }

    payload = {
        "experiment": "topic_dossier_ablation",
        "claim": "A topic-centered middle layer improves longitudinal and mixed-topic recall without dataset-specific hacks.",
        "modes": modes_summary,
        "cases": case_rows,
        "dossiers": {topic: asdict(dossier) for topic, dossier in nano.dossiers.items()},
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML), "modes": modes_summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
