#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nano_reference_impl_v17 import EchoMemoryNanoReferenceV17, Observation


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_unified_structure_ablation_20260617_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_unified_structure_ablation_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


@dataclass
class Case:
    case_id: str
    family: str
    query: str
    query_time: str
    expected_keywords: list[str]
    note: str


class WriteTimeCollapseNano(EchoMemoryNanoReferenceV17):
    """Weak variant: collapse story time to write time."""

    def append_text(self, *, role: str, content: str, write_time: str, topic_hint: str = "") -> None:
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role=role,
                modality="text",
                content=content.strip(),
                mention_time=write_time,
                write_time=write_time,
                story_time=write_time[:10],
                topic_hint=topic_hint.strip(),
            )
        )
        self._invalidate()

    def append_image(
        self,
        *,
        role: str,
        caption: str,
        ocr: str,
        write_time: str,
        topic_hint: str = "",
        linked_subject: str = "",
    ) -> None:
        content = "\n".join(x for x in [caption.strip(), ocr.strip()] if x)
        self.observations.append(
            Observation(
                obs_id=f"obs-{len(self.observations):03d}",
                role=role,
                modality="image",
                content=content,
                mention_time=write_time,
                write_time=write_time,
                story_time=write_time[:10],
                topic_hint=topic_hint.strip(),
                linked_subject=linked_subject.strip(),
                caption=caption.strip(),
                ocr=ocr.strip(),
            )
        )
        self._invalidate()

    def _extract_atoms(self):
        atoms = super()._extract_atoms()
        for atom in atoms:
            atom.story_time = atom.write_time[:10]
            atom.valid_from = atom.write_time[:10]
        return atoms


class NoDossierNano(EchoMemoryNanoReferenceV17):
    """Weak variant: remove the topic-centered middle layer."""

    def _build_dossiers(self) -> dict[str, Any]:
        return {}

    def build(self) -> None:
        self.atoms = self._extract_atoms()
        self._apply_state_lifecycle()
        self.readiness.atoms_ready = bool(self.atoms)
        self.dossiers = {}
        self.readiness.dossier_ready = False
        self.temporal_blocks = self._build_temporal_blocks()
        self.readiness.temporal_ready = bool(self.temporal_blocks)
        self.nodes, self.edges = self._build_graph()
        self.readiness.graph_ready = bool(self.nodes)
        self.readiness.qa_ready = (
            self.readiness.persisted
            and self.readiness.atoms_ready
            and self.readiness.temporal_ready
            and self.readiness.graph_ready
        )

    def plan(self, query: str):
        plan = super().plan(query)
        if plan.family == "longitudinal":
            return type(plan)(
                family=plan.family,
                primary_reader="atom",
                supporting_readers=["temporal_tree", "graph"],
                required_layers=["fact"],
                rationale="No topic dossier available; longitudinal questions fall back to flat atoms.",
            )
        return plan


class NoAnswerabilityGateNano(EchoMemoryNanoReferenceV17):
    """Weak variant: retrieval contract can complete, but candidate answer is not filtered."""

    def _answerability_ok(self, query: str, plan: Any, hits: list[Any], candidate: str) -> bool:
        return True


class TypedAnswerabilityGateNano(EchoMemoryNanoReferenceV17):
    """Stronger generic gate: enforce lightweight answer-type compatibility."""

    COMPANY_MARKERS = ("inc", "corp", "ltd", "llc", "company", "group", "studio", "technologies", "tech", "labs")
    PLACE_MARKERS = ("street", "road", "avenue", "city", "lisbon", "district")

    @staticmethod
    def _looks_like_person(text: str) -> bool:
        value = str(text or "").strip()
        parts = [part for part in value.split() if part]
        if not parts:
            return False
        return all(part[:1].isupper() and part[1:].islower() for part in parts if part.isalpha())

    def _answerability_ok(self, query: str, plan: Any, hits: list[Any], candidate: str) -> bool:
        lowered_query = query.lower()
        lowered_candidate = str(candidate).lower()
        candidate_entities = self._extract_entities(str(candidate))

        if getattr(plan, "family", "") in {"temporal", "longitudinal", "state"}:
            return True

        query_entities = self._extract_entities(query)
        if query_entities:
            joined = "\n".join(getattr(hit, "content", "") for hit in hits[:6])
            if not any(entity in joined for entity in query_entities):
                return False

        if "which company" in lowered_query:
            if any(marker in lowered_candidate for marker in self.COMPANY_MARKERS):
                return True
            if any(self._looks_like_person(entity) for entity in candidate_entities):
                return False
            return False

        if "which city" in lowered_query or "what city" in lowered_query:
            if any(marker in lowered_candidate for marker in self.PLACE_MARKERS):
                return True
            if any(self._looks_like_person(entity) for entity in candidate_entities):
                return False

        if "who" in lowered_query and "company" not in lowered_query and "city" not in lowered_query:
            if any(marker in lowered_candidate for marker in self.COMPANY_MARKERS):
                return False

        return True

    def _answer(self, query: str, query_time: str, plan: Any, hits: list[Any], missing: list[str]) -> str:
        candidate = super()._answer(query, query_time, plan, hits, missing)
        if candidate in {"unknown", "unknown_conflict", "ready", "not_ready"}:
            return candidate
        if not self._answerability_ok(query, plan, hits, candidate):
            return "unknown"
        return candidate


def build_memory(mem: EchoMemoryNanoReferenceV17) -> EchoMemoryNanoReferenceV17:
    mem.append_text(
        role="user",
        content="Maya started the visa paperwork on 2026-03-02. Nora helped Maya with the visa checklist on 2026-03-03.",
        write_time="2026-03-03T10:00:00",
        topic_hint="visa_process",
    )
    mem.append_text(
        role="user",
        content="Yesterday the consulate requested one more financial statement.",
        write_time="2026-04-09T10:00:00",
        topic_hint="visa_process",
    )
    mem.append_text(
        role="user",
        content="Maya prefers tea.",
        write_time="2026-03-04T10:00:00",
        topic_hint="daily_preferences",
    )
    mem.append_text(
        role="user",
        content="Maya prefers coffee.",
        write_time="2026-04-10T08:00:00",
        topic_hint="daily_preferences",
    )
    mem.append_text(
        role="user",
        content="Jon met Lena at the Figma meetup. Maya introduced Jon to Lena that evening.",
        write_time="2026-04-12T18:00:00",
        topic_hint="social_graph",
    )
    mem.append_image(
        role="user",
        caption="Lease renewal screenshot",
        ocr="Rua Augusta 14, Lisbon",
        write_time="2026-03-12T09:00:00",
        topic_hint="housing",
        linked_subject="Maya",
    )
    mem.append_text(
        role="user",
        content="Kai's badge number is B-441.",
        write_time="2026-04-14T08:00:00",
        topic_hint="work_access",
    )
    mem.append_text(
        role="user",
        content="Kai's badge number is B-772.",
        write_time="2026-04-14T09:00:00",
        topic_hint="work_access",
    )
    mem.build()
    return mem


def build_cases() -> list[Case]:
    return [
        Case(
            "temporal_story_time",
            "temporal",
            "When did the consulate request one more financial statement?",
            "2026-04-10",
            ["2026-04-08"],
            "Relative-time mention should resolve to story time rather than write time.",
        ),
        Case(
            "longitudinal_latest_state",
            "longitudinal",
            "What is Maya's latest preference now?",
            "2026-04-10",
            ["2026-04-10", "coffee"],
            "Latest-state query should benefit from the topic-centered middle layer.",
        ),
        Case(
            "longitudinal_process_evolution",
            "longitudinal",
            "How did the visa process change over time?",
            "2026-04-10",
            ["2026-03-02", "2026-04-08"],
            "Cross-session process evolution should surface early and late updates together.",
        ),
        Case(
            "relational_intro_path",
            "relational",
            "Who introduced Jon to Lena?",
            "2026-04-13",
            ["Maya"],
            "Relation-heavy question should prefer path-grounded graph evidence over nearby co-occurrence.",
        ),
        Case(
            "answerability_type_mismatch",
            "relational",
            "Which company helped Maya with the visa checklist?",
            "2026-03-20",
            ["unknown"],
            "Without answerability discipline, the system may answer with a person even when the query requires a company.",
        ),
        Case(
            "state_conflict",
            "state",
            "What is Kai's badge number?",
            "2026-04-14",
            ["unknown_conflict"],
            "Without answerability discipline, the system may over-answer conflicted state.",
        ),
    ]


def score_variant(name: str, mem: EchoMemoryNanoReferenceV17, cases: list[Case]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        result = mem.retrieve(case.query, case.query_time)
        answer = str(result["answer"])
        passed = all(keyword.lower() in answer.lower() for keyword in case.expected_keywords)
        rows.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "query": case.query,
                "answer": answer,
                "passed": passed,
                "contract_ok": bool(result["contract_ok"]),
                "present_layers": list(result["present_layers"]),
                "missing_layers": list(result["missing_layers"]),
                "second_pass_sources": list(result["second_pass_sources"]),
                "note": case.note,
            }
        )
    return {
        "variant": name,
        "correct": sum(1 for row in rows if row["passed"]),
        "total": len(rows),
        "rows": rows,
        "qa_ready": mem.readiness.qa_ready,
        "dossier_count": len(mem.dossiers),
    }


def run() -> dict[str, Any]:
    cases = build_cases()
    variants = [
        ("legacy_v17", build_memory(EchoMemoryNanoReferenceV17())),
        ("typed_answerability_gate", build_memory(TypedAnswerabilityGateNano())),
        ("write_time_collapse", build_memory(WriteTimeCollapseNano())),
        ("no_topic_dossier", build_memory(NoDossierNano())),
        ("no_answerability_gate", build_memory(NoAnswerabilityGateNano())),
    ]
    reports = [score_variant(name, mem, cases) for name, mem in variants]
    return {"cases": [case.__dict__ for case in cases], "variants": reports}


def render_html(report: dict[str, Any]) -> str:
    summary_rows = []
    for variant in report["variants"]:
        summary_rows.append(
            "<tr>"
            f"<td>{esc(variant['variant'])}</td>"
            f"<td>{variant['correct']}/{variant['total']}</td>"
            f"<td>{esc(variant['qa_ready'])}</td>"
            f"<td>{esc(variant['dossier_count'])}</td>"
            "</tr>"
        )

    detail_blocks = []
    for variant in report["variants"]:
        rows = []
        for row in variant["rows"]:
            rows.append(
                "<tr>"
                f"<td>{esc(row['case_id'])}</td>"
                f"<td>{esc(row['family'])}</td>"
                f"<td>{esc(row['answer'])}</td>"
                f"<td>{esc(', '.join(row['present_layers']))}</td>"
                f"<td>{esc(', '.join(row['second_pass_sources']))}</td>"
                f"<td>{'ok' if row['passed'] else 'fail'}</td>"
                "</tr>"
            )
        detail_blocks.append(
            f"""
            <section class="panel">
              <h2>{esc(variant['variant'])}</h2>
              <table>
                <thead><tr><th>case</th><th>family</th><th>answer</th><th>present layers</th><th>second pass</th><th>result</th></tr></thead>
                <tbody>{''.join(rows)}</tbody>
              </table>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Unified Structure Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1220px; margin:0 auto; padding:28px 20px 54px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px 24px; margin-bottom:16px; }}
    h1,h2 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    p {{ margin:0 0 10px; }}
    .muted {{ color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; }}
    code {{ background:#f3f6fb; border:1px solid #e5eaf2; border-radius:6px; padding:1px 4px; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Unified Structure Ablation</h1>
      <p class="muted">
        这个统一实验把四个结构改动放到同一套 memory 上比较：<code>three-clock time</code>、<code>topic dossier</code>、<code>graph path</code> 和 <code>answerability gate</code>。
        它不是为了某个 benchmark 调规则，而是直接看拿掉这些结构后会坏哪类题。
      </p>
    </section>

    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead><tr><th>variant</th><th>score</th><th>qa_ready</th><th>dossier_count</th></tr></thead>
        <tbody>{''.join(summary_rows)}</tbody>
      </table>
    </section>

    {''.join(detail_blocks)}
  </div>
</body>
</html>
"""


def main() -> None:
    report = run()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
