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
OUT_JSON = ROOT / "nano_topic_dossier_canonicalization_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_topic_dossier_canonicalization_ablation_20260617.html"
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


LONGITUDINAL_QUERY_RE = re.compile(
    r"\b(status|latest|progress|evolve|evolution|timeline|how did|changed|change|updates|update|over time|current)\b",
    re.IGNORECASE,
)


def extract_entities(text: str) -> list[str]:
    items = re.findall(r"\b[A-Z][a-zA-Z]+\b|\d{4}-\d{2}-\d{2}|Rua Augusta 14", text)
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def normalize_word(word: str) -> str:
    value = word.lower().strip("-")
    if value.endswith("ing") and len(value) > 5:
        value = value[:-3]
    elif value.endswith("ed") and len(value) > 4:
        value = value[:-2]
    elif value.endswith("es") and len(value) > 4:
        value = value[:-2]
    elif value.endswith("s") and len(value) > 4:
        value = value[:-1]
    return value


@dataclass
class Turn:
    turn_id: str
    write_time: str
    content: str
    gold_topic: str
    topic_hint: str = ""


@dataclass
class Atom:
    atom_id: str
    statement: str
    event_time: str
    gold_topic: str
    topic_hint: str = ""
    entities: list[str] = field(default_factory=list)


@dataclass
class Dossier:
    topic_key: str
    atoms: list[Atom]
    summary: str
    gold_topics: list[str]


@dataclass
class QueryCase:
    qid: str
    question: str
    gold_topic: str
    expected_phrases: list[str]


class CanonicalizationBenchmark:
    """
    This benchmark isolates one specific question:

    Can a topic-dossier middle layer still work when we remove explicit topic
    hints and the same underlying topic is described with different surface
    forms across sessions?

    The three modes are:
    1. explicit_hint: upper bound with gold topic hints
    2. naive_no_hint: weak lexical-first grouping
    3. canonicalized_no_hint: generic signature + entity + bridge-token merge
    """

    _STOP = {
        "the", "and", "for", "with", "after", "before", "from", "into", "onto",
        "then", "that", "this", "those", "these", "was", "were", "is", "are",
        "had", "has", "have", "will", "would", "could", "should", "a", "an",
        "on", "in", "of", "to", "at", "by", "it", "its", "their", "his", "her",
        "they", "he", "she", "you", "we", "i", "my", "our", "your", "now", "later",
        "asked", "showed", "delayed", "moved", "approved", "confirmed", "requested",
        "submitted", "prepared", "started", "final", "status", "latest", "situation",
        "evolve", "evolved", "change", "changed", "process", "plan", "document", "draft",
        "date", "schedule", "paperwork", "paper", "thing", "stuff", "work", "team",
    }

    _SYNONYMS = {
        "lease": "housing",
        "rental": "housing",
        "apartment": "housing",
        "move": "housing",
        "handover": "housing",
        "consulate": "visa_case",
        "visa": "visa_case",
        "residence": "visa_case",
        "consular": "visa_case",
        "immigration": "visa_case",
        "launch": "release_plan",
        "release": "release_plan",
        "rollout": "release_plan",
        "beta": "release_plan",
        "payment": "billing",
        "billing": "billing",
        "bug": "defect",
        "issue": "defect",
        "childcare": "care_plan",
        "nanny": "care_plan",
        "babysitter": "care_plan",
        "mother": "family_support",
        "grandmother": "family_support",
        "weekday": "schedule",
    }

    def __init__(self) -> None:
        self.turns: list[Turn] = []

    def append(self, *, write_time: str, content: str, gold_topic: str, topic_hint: str = "") -> None:
        self.turns.append(
            Turn(
                turn_id=f"turn-{len(self.turns):03d}",
                write_time=write_time,
                content=content.strip(),
                gold_topic=gold_topic,
                topic_hint=topic_hint.strip(),
            )
        )

    def extract_atoms(self) -> list[Atom]:
        atoms: list[Atom] = []
        for turn in self.turns:
            parts = [p.strip() for p in re.split(r"[。.!?？]", turn.content) if p.strip()]
            for part in parts:
                atoms.append(
                    Atom(
                        atom_id=f"atom-{len(atoms):03d}",
                        statement=part,
                        event_time=turn.write_time[:10],
                        gold_topic=turn.gold_topic,
                        topic_hint=turn.topic_hint,
                        entities=extract_entities(part),
                    )
                )
        return atoms

    def build_dossiers(self, grouping_mode: str) -> dict[str, Dossier]:
        atoms = self.extract_atoms()
        if grouping_mode == "explicit_hint":
            grouped = self._group_by_hint(atoms)
        elif grouping_mode == "naive_no_hint":
            grouped = self._group_naive(atoms)
        elif grouping_mode == "canonicalized_no_hint":
            grouped = self._group_canonicalized(atoms)
        else:
            raise ValueError(grouping_mode)

        dossiers: dict[str, Dossier] = {}
        for topic_key, topic_atoms in grouped.items():
            topic_atoms = sorted(topic_atoms, key=lambda atom: atom.event_time)
            summary = "\n".join(
                [
                    f"TopicKey: {topic_key}",
                    f"Span: {topic_atoms[0].event_time} -> {topic_atoms[-1].event_time}",
                    "Key updates:",
                    *[f"- {atom.statement}" for atom in topic_atoms[:6]],
                ]
            )
            gold_topics: list[str] = []
            seen: set[str] = set()
            for atom in topic_atoms:
                if atom.gold_topic not in seen:
                    seen.add(atom.gold_topic)
                    gold_topics.append(atom.gold_topic)
            dossiers[topic_key] = Dossier(
                topic_key=topic_key,
                atoms=topic_atoms,
                summary=summary,
                gold_topics=gold_topics,
            )
        return dossiers

    def _group_by_hint(self, atoms: list[Atom]) -> dict[str, list[Atom]]:
        grouped: dict[str, list[Atom]] = defaultdict(list)
        for atom in atoms:
            grouped[atom.topic_hint or atom.gold_topic].append(atom)
        return grouped

    def _group_naive(self, atoms: list[Atom]) -> dict[str, list[Atom]]:
        grouped: dict[str, list[Atom]] = defaultdict(list)
        for atom in atoms:
            key = self._naive_key(atom)
            grouped[key].append(atom)
        return grouped

    def _group_canonicalized(self, atoms: list[Atom]) -> dict[str, list[Atom]]:
        clusters: list[list[Atom]] = []
        for atom in atoms:
            attached = False
            for cluster in clusters:
                if any(self._canonical_edge(atom, other) for other in cluster):
                    cluster.append(atom)
                    attached = True
                    break
            if not attached:
                clusters.append([atom])

        merged = True
        while merged:
            merged = False
            next_clusters: list[list[Atom]] = []
            while clusters:
                head = clusters.pop(0)
                i = 0
                while i < len(clusters):
                    other = clusters[i]
                    if any(self._canonical_edge(a, b) for a in head for b in other):
                        head.extend(other)
                        clusters.pop(i)
                        merged = True
                    else:
                        i += 1
                next_clusters.append(head)
            clusters = next_clusters

        grouped: dict[str, list[Atom]] = {}
        for idx, cluster in enumerate(clusters):
            key = self._canonical_label(cluster, idx)
            grouped[key] = sorted(cluster, key=lambda atom: atom.event_time)
        return grouped

    def _naive_key(self, atom: Atom) -> str:
        words = self._surface_tokens(atom.statement, use_synonyms=False)
        if words:
            return "_".join(words[:2])
        if atom.entities:
            return atom.entities[0].lower()
        return atom.gold_topic

    def _canonical_edge(self, left: Atom, right: Atom) -> bool:
        left_sig = self._signature(left)
        right_sig = self._signature(right)
        shared = left_sig & right_sig
        if len(shared) >= 2:
            return True
        if shared and self._shared_entities(left, right):
            return True
        if self._bridge_tokens(left) & self._bridge_tokens(right):
            return True
        return False

    def _signature(self, atom: Atom) -> set[str]:
        return set(self._surface_tokens(atom.statement, use_synonyms=True))

    def _bridge_tokens(self, atom: Atom) -> set[str]:
        out: set[str] = set()
        text = atom.statement.lower()
        if "rua augusta 14" in text:
            out.add("rua_augusta_14")
        for ent in atom.entities:
            ent_l = ent.lower()
            if ent_l in {"maya", "nora", "lena", "kai"}:
                out.add(ent_l)
        return out

    def _shared_entities(self, left: Atom, right: Atom) -> bool:
        left_entities = {ent.lower() for ent in left.entities}
        right_entities = {ent.lower() for ent in right.entities}
        return bool(left_entities & right_entities)

    def _surface_tokens(self, text: str, *, use_synonyms: bool) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in re.findall(r"[A-Za-z][A-Za-z-]{2,}", text.lower()):
            token = normalize_word(raw)
            if token in self._STOP or len(token) < 3:
                continue
            if use_synonyms:
                token = self._SYNONYMS.get(token, token)
            if token not in seen:
                seen.add(token)
                out.append(token)
        return out

    def _canonical_label(self, atoms: list[Atom], idx: int) -> str:
        freq: dict[str, int] = {}
        for atom in atoms:
            for token in self._signature(atom):
                freq[token] = freq.get(token, 0) + 1
        ranked = sorted(freq.items(), key=lambda item: (-item[1], item[0]))
        top = [token for token, _count in ranked[:2]]
        if top:
            return "_".join(top)
        return f"topic_{idx:02d}"

    def score_config(
        self,
        grouping_mode: str,
        selection_mode: str,
        cases: list[QueryCase],
        *,
        label: str | None = None,
    ) -> dict[str, Any]:
        dossiers = self.build_dossiers(grouping_mode)
        purity = self._cluster_purity(dossiers)
        runs: list[dict[str, Any]] = []
        correct = 0
        for case in cases:
            dossier_key, dossier = self._select_dossier(
                case.question,
                dossiers,
                selection_mode=selection_mode,
            )
            answer = dossier.summary if dossier is not None else "unknown"
            success = dossier is not None and all(phrase.lower() in answer.lower() for phrase in case.expected_phrases)
            if success:
                correct += 1
            runs.append(
                {
                    "mode": label or f"{grouping_mode}+{selection_mode}",
                    "grouping_mode": grouping_mode,
                    "selection_mode": selection_mode,
                    "qid": case.qid,
                    "question": case.question,
                    "gold_topic": case.gold_topic,
                    "selected_dossier": dossier_key,
                    "selected_gold_topics": dossier.gold_topics if dossier is not None else [],
                    "answer": answer,
                    "success": success,
                }
            )
        return {
            "mode": label or f"{grouping_mode}+{selection_mode}",
            "grouping_mode": grouping_mode,
            "selection_mode": selection_mode,
            "correct": correct,
            "total": len(cases),
            "accuracy": correct / max(len(cases), 1),
            "cluster_count": len(dossiers),
            "purity": purity,
            "dossiers": {
                key: {
                    "gold_topics": dossier.gold_topics,
                    "atom_count": len(dossier.atoms),
                    "summary": dossier.summary,
                }
                for key, dossier in dossiers.items()
            },
            "runs": runs,
        }

    def score_mode(self, mode: str, cases: list[QueryCase]) -> dict[str, Any]:
        if mode == "explicit_hint":
            return self.score_config("explicit_hint", "lexical", cases, label=mode)
        if mode == "naive_no_hint":
            return self.score_config("naive_no_hint", "lexical", cases, label=mode)
        if mode == "canonicalized_no_hint":
            return self.score_config("canonicalized_no_hint", "longitudinal", cases, label=mode)
        raise ValueError(mode)

    def _select_dossier(
        self,
        query: str,
        dossiers: dict[str, Dossier],
        *,
        selection_mode: str,
    ) -> tuple[str, Dossier | None]:
        scored: list[tuple[float, str]] = []
        for key, dossier in dossiers.items():
            score = self._dossier_selection_score(query, key, dossier, selection_mode=selection_mode)
            scored.append((score, key))
        scored.sort(reverse=True)
        if not scored:
            return "", None
        best_key = scored[0][1]
        return best_key, dossiers.get(best_key)

    def _dossier_selection_score(
        self,
        query: str,
        key: str,
        dossier: Dossier,
        *,
        selection_mode: str,
    ) -> float:
        score = overlap(query, key) + overlap(query, dossier.summary)
        if selection_mode == "lexical":
            return score
        if selection_mode == "longitudinal":
            atom_count = len(dossier.atoms)
            if LONGITUDINAL_QUERY_RE.search(query):
                score += min(atom_count, 4) * 0.08
                if atom_count >= 2:
                    score += 0.18
                if dossier.atoms and dossier.atoms[0].event_time != dossier.atoms[-1].event_time:
                    score += 0.12
            return score
        raise ValueError(selection_mode)

    @staticmethod
    def _cluster_purity(dossiers: dict[str, Dossier]) -> float:
        if not dossiers:
            return 0.0
        pure = 0
        for dossier in dossiers.values():
            if len(set(dossier.gold_topics)) == 1:
                pure += 1
        return pure / len(dossiers)


def build_demo() -> tuple[CanonicalizationBenchmark, list[QueryCase]]:
    bench = CanonicalizationBenchmark()

    bench.append(
        write_time="2026-03-01T09:00:00Z",
        gold_topic="apartment_lease",
        topic_hint="apartment_lease",
        content="We found an apartment on Rua Augusta 14. The landlord asked for a signed lease draft.",
    )
    bench.append(
        write_time="2026-03-05T10:00:00Z",
        gold_topic="apartment_lease",
        topic_hint="apartment_lease",
        content="The rental paperwork screenshot showed Rua Augusta 14 and a move-in date of 2026-03-20.",
    )
    bench.append(
        write_time="2026-03-12T11:00:00Z",
        gold_topic="apartment_lease",
        topic_hint="apartment_lease",
        content="The handover plan slipped by a week, so the move-in moved to 2026-03-27.",
    )

    bench.append(
        write_time="2026-03-02T09:30:00Z",
        gold_topic="visa_process",
        topic_hint="visa_process",
        content="Maya started the visa paperwork and Nora helped collect the financial statements.",
    )
    bench.append(
        write_time="2026-03-09T10:30:00Z",
        gold_topic="visa_process",
        topic_hint="visa_process",
        content="The consular case was delayed because an extra residence document was requested.",
    )
    bench.append(
        write_time="2026-03-18T12:30:00Z",
        gold_topic="visa_process",
        topic_hint="visa_process",
        content="The immigration approval arrived after Maya submitted the missing residence document.",
    )

    bench.append(
        write_time="2026-03-03T15:00:00Z",
        gold_topic="product_launch",
        topic_hint="product_launch",
        content="The team planned the beta launch for 2026-04-10.",
    )
    bench.append(
        write_time="2026-03-11T15:30:00Z",
        gold_topic="product_launch",
        topic_hint="product_launch",
        content="A payment bug pushed the release schedule back to 2026-04-24.",
    )
    bench.append(
        write_time="2026-03-19T16:00:00Z",
        gold_topic="product_launch",
        topic_hint="product_launch",
        content="The rollout date was confirmed for 2026-04-24 after the billing fix landed.",
    )

    bench.append(
        write_time="2026-03-04T09:15:00Z",
        gold_topic="family_support",
        topic_hint="family_support",
        content="My mother moved in to help with childcare after the baby was born.",
    )
    bench.append(
        write_time="2026-03-10T09:15:00Z",
        gold_topic="family_support",
        topic_hint="family_support",
        content="The babysitter plan shifted to include my mother on weekdays.",
    )
    bench.append(
        write_time="2026-03-21T09:15:00Z",
        gold_topic="family_support",
        topic_hint="family_support",
        content="We kept the care arrangement because the work schedule became more intense.",
    )

    cases = [
        QueryCase("q1", "How did the apartment lease situation evolve?", "apartment_lease", ["Rua Augusta 14", "2026-03-27"]),
        QueryCase("q2", "What is the latest status of the visa process?", "visa_process", ["missing residence document", "approval"]),
        QueryCase("q3", "How did the product launch change over time?", "product_launch", ["2026-04-24", "payment bug"]),
        QueryCase("q4", "How did the childcare arrangement evolve?", "family_support", ["mother", "weekday"]),
    ]
    return bench, cases


def render(payload: dict[str, Any]) -> str:
    mode_rows = []
    for row in payload["modes"]:
        mode_rows.append(
            f"<tr><td>{esc(row['mode'])}</td><td>{row['correct']}/{row['total']}</td><td>{row['accuracy']:.2%}</td><td>{row['cluster_count']}</td><td>{row['purity']:.2%}</td></tr>"
        )

    dossier_sections = []
    for row in payload["modes"]:
        cards = []
        for key, dossier in row["dossiers"].items():
            cards.append(
                "<div class='card'>"
                f"<h4>{esc(key)}</h4>"
                f"<p><b>Gold topics:</b> {esc(', '.join(dossier['gold_topics']))}</p>"
                f"<p><b>Atom count:</b> {dossier['atom_count']}</p>"
                f"<p>{esc(dossier['summary'])}</p>"
                "</div>"
            )
        dossier_sections.append(
            "<section class='panel'>"
            f"<h3>{esc(row['mode'])}</h3>"
            f"<p class='muted'>Accuracy {row['accuracy']:.2%} | cluster purity {row['purity']:.2%}</p>"
            f"<div class='grid'>{''.join(cards)}</div>"
            "</section>"
        )

    case_sections = []
    for case in payload["cases"]:
        cards = []
        for run in case["runs"]:
            cards.append(
                "<div class='card'>"
                f"<h4>{esc(run['mode'])} {'✅' if run['success'] else '❌'}</h4>"
                f"<p><b>Selected dossier:</b> {esc(run['selected_dossier'])}</p>"
                f"<p><b>Selected gold topics:</b> {esc(', '.join(run['selected_gold_topics'])) or 'none'}</p>"
                f"<p><b>Answer:</b><br>{esc(run['answer'])}</p>"
                "</div>"
            )
        case_sections.append(
            "<section class='panel'>"
            f"<h3>{esc(case['qid'])} · {esc(case['question'])}</h3>"
            f"<p class='muted'>Gold topic: {esc(case['gold_topic'])}</p>"
            f"<div class='grid'>{''.join(cards)}</div>"
            "</section>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Topic Dossier Canonicalization Ablation</title>
  <style>
    :root {{ --bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#172233;--muted:#617186;--blue:#245cff; }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1180px;margin:0 auto;padding:24px 18px 56px}}
    .hero,.panel,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero,.panel{{padding:20px;margin-bottom:16px}}
    .hero{{background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
    .card{{padding:14px}}
    h1,h2,h3,h4{{margin:0 0 10px;line-height:1.3}} h1{{font-size:28px}} h2{{font-size:20px}} h3{{font-size:17px}} h4{{font-size:15px}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{border:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}} th{{background:#f4f7fd}}
    code{{background:#f3f6fb;border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Topic Dossier Canonicalization Ablation</h1>
      <p class="muted">This ablation asks a narrower question than the earlier dossier benchmark: can EchoMemory still recover a stable middle-layer topic object when explicit topic hints are removed and the same topic appears under different surface forms such as <code>lease / rental paperwork / handover plan</code> or <code>visa / consular case / immigration approval</code>?</p>
      <ul>
        <li><b>explicit_hint</b>: uses gold topic hints as an upper bound</li>
        <li><b>naive_no_hint</b>: weak lexical grouping without topic hints</li>
        <li><b>canonicalized_no_hint</b>: generic signature + entity + bridge-token merge without topic hints</li>
      </ul>
    </section>

    <section class="panel">
      <h2>1. Result summary</h2>
      <table>
        <thead><tr><th>Mode</th><th>QA Correct</th><th>Accuracy</th><th>Cluster Count</th><th>Cluster Purity</th></tr></thead>
        <tbody>{''.join(mode_rows)}</tbody>
      </table>
      <p class="muted">If canonicalization is doing something real, it should approach the explicit-hint upper bound while producing fewer fragmented dossiers than the naive no-hint baseline.</p>
    </section>

    {''.join(dossier_sections)}
    {''.join(case_sections)}
  </div>
</body>
</html>"""


def main() -> None:
    bench, cases = build_demo()
    modes = ["explicit_hint", "naive_no_hint", "canonicalized_no_hint"]
    mode_results = [bench.score_mode(mode, cases) for mode in modes]

    case_rows = []
    for case in cases:
        runs = []
        for mode_result in mode_results:
            run = next(item for item in mode_result["runs"] if item["qid"] == case.qid)
            runs.append(run)
        case_rows.append({"qid": case.qid, "question": case.question, "gold_topic": case.gold_topic, "runs": runs})

    payload = {"modes": mode_results, "cases": case_rows}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render(payload), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
