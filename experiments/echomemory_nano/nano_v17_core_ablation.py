#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nano_reference_impl_v17 import EchoMemoryNanoReferenceV17, Observation


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_v17_core_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_v17_core_ablation_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


@dataclass
class EvalCase:
    case_id: str
    family: str
    query: str
    query_time: str
    expected_keywords: list[str]
    note: str


class WriteTimeOnlyNano(EchoMemoryNanoReferenceV17):
    """Weak variant that intentionally collapses story_time to write_time."""

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


class AtomOnlyNoDossierNano(EchoMemoryNanoReferenceV17):
    """Weak variant that disables the topic-dossier middle layer."""

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


def build_shared_data(mem: EchoMemoryNanoReferenceV17) -> EchoMemoryNanoReferenceV17:
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


def build_cases() -> list[EvalCase]:
    return [
        EvalCase(
            "temporal_retrospective",
            "temporal",
            "When did the consulate request one more financial statement?",
            "2026-04-10",
            ["2026-04-08"],
            "Retrospective mention should use story time, not write time.",
        ),
        EvalCase(
            "longitudinal_visa",
            "longitudinal",
            "How did the visa process change over time?",
            "2026-04-10",
            ["2026-03-02", "2026-04-08"],
            "Needs a longitudinal middle layer to surface multiple updates coherently.",
        ),
        EvalCase(
            "longitudinal_latest_pref",
            "longitudinal",
            "What is Maya's latest preference now?",
            "2026-04-10",
            ["2026-04-10", "coffee"],
            "Latest-state question should still use longitudinal evidence correctly.",
        ),
        EvalCase(
            "visual_address",
            "visual",
            "What address was shown in the lease screenshot?",
            "2026-03-20",
            ["Rua Augusta 14"],
            "Visual grounding should work in every variant that retains image evidence.",
        ),
    ]


def score_variant(name: str, mem: EchoMemoryNanoReferenceV17, cases: list[EvalCase]) -> dict[str, Any]:
    rows = []
    for case in cases:
        result = mem.retrieve(case.query, case.query_time)
        answer = str(result["answer"])
        passed = all(keyword.lower() in answer.lower() for keyword in case.expected_keywords)
        rows.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "answer": answer,
                "passed": passed,
                "contract_ok": result["contract_ok"],
                "second_pass_sources": result["second_pass_sources"],
                "present_layers": result["present_layers"],
                "missing_layers": result["missing_layers"],
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


def run_ablation() -> dict[str, Any]:
    cases = build_cases()
    full = score_variant("full_v17", build_shared_data(EchoMemoryNanoReferenceV17()), cases)
    write_only = score_variant("write_time_only", build_shared_data(WriteTimeOnlyNano()), cases)
    no_dossier = score_variant("atom_only_no_dossier", build_shared_data(AtomOnlyNoDossierNano()), cases)
    return {
        "summary": {
            "variants": {
                full["variant"]: f"{full['correct']}/{full['total']}",
                write_only["variant"]: f"{write_only['correct']}/{write_only['total']}",
                no_dossier["variant"]: f"{no_dossier['correct']}/{no_dossier['total']}",
            }
        },
        "cases": [case.__dict__ for case in cases],
        "variants": [full, write_only, no_dossier],
    }


def render_html(report: dict[str, Any]) -> str:
    variant_rows = []
    for variant in report["variants"]:
        variant_rows.append(
            "<tr>"
            f"<td>{esc(variant['variant'])}</td>"
            f"<td>{esc(variant['correct'])}/{esc(variant['total'])}</td>"
            f"<td>{esc(variant['qa_ready'])}</td>"
            f"<td>{esc(variant['dossier_count'])}</td>"
            "</tr>"
        )
    case_sections = []
    for case in report["cases"]:
        rows = []
        for variant in report["variants"]:
            row = next(item for item in variant["rows"] if item["case_id"] == case["case_id"])
            rows.append(
                "<tr>"
                f"<td>{esc(variant['variant'])}</td>"
                f"<td>{esc(row['answer'])}</td>"
                f"<td>{esc(', '.join(row['present_layers']))}</td>"
                f"<td>{esc(', '.join(row['second_pass_sources']) or '-')}</td>"
                f"<td>{'PASS' if row['passed'] else 'FAIL'}</td>"
                "</tr>"
            )
        case_sections.append(
            f"""
            <section class="panel">
              <h2>{esc(case['case_id'])}</h2>
              <p><b>Query:</b> {esc(case['query'])}</p>
              <p><b>Why it matters:</b> {esc(case['note'])}</p>
              <table>
                <thead><tr><th>variant</th><th>answer</th><th>present layers</th><th>second pass</th><th>result</th></tr></thead>
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
  <title>EchoMemory nano v17 core ablation</title>
  <style>
    body{{margin:0;background:#f5f7fb;color:#182333;font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1180px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:#fff;border:1px solid #dde5ef;border-radius:12px;box-shadow:0 14px 34px rgba(18,32,51,.08)}}
    .hero{{padding:28px;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .panel{{padding:18px;margin-bottom:16px}}
    h1,h2{{margin:0 0 10px;line-height:1.28}} h1{{font-size:30px}} h2{{font-size:20px}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{border:1px solid #dde5ef;padding:10px;text-align:left;vertical-align:top}} th{{background:#f4f7fd}}
    .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}} .chip{{padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;background:#f8fbff;border:1px solid #cad7ee;color:#29446b}}
    .callout{{padding:12px 14px;border-left:4px solid #245cff;background:#f4f8ff;border-radius:8px;margin-top:10px}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory nano v17 core ablation</h1>
      <p>这组实验专门验证两件最核心的事：<b>three-clock 时间建模</b> 和 <b>topic dossier 中层</b>。它不是 benchmark hack，而是针对结构本身做的机制 ablation。</p>
      <div class="chips">
        <span class="chip">full_v17</span>
        <span class="chip">write_time_only</span>
        <span class="chip">atom_only_no_dossier</span>
      </div>
      <div class="callout">
        预期：<b>write_time_only</b> 应该在 retrospective 时间题上掉分，<b>atom_only_no_dossier</b> 应该在 longitudinal 题上掉分，而 <b>full_v17</b> 要同时稳住两类。
      </div>
    </section>
    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead><tr><th>variant</th><th>score</th><th>qa_ready</th><th>dossier_count</th></tr></thead>
        <tbody>{''.join(variant_rows)}</tbody>
      </table>
    </section>
    {''.join(case_sections)}
  </div>
</body>
</html>"""


def main() -> None:
    report = run_ablation()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
