#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_family_aware_readiness_ablation_results.json"
OUT_HTML = ROOT / "nano_family_aware_readiness_ablation_report.html"
PUBLIC_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_family_aware_readiness_ablation_20260617.html"
)


@dataclass
class Readiness:
    messages_persisted: bool = False
    atoms_ready: bool = False
    graph_ready: bool = False
    organized_ready: bool = False
    episode_ready: bool = False
    qa_ready: bool = False


@dataclass(frozen=True)
class QueryPlan:
    family: str
    required_layers: tuple[str, ...]
    primary_reader: str


@dataclass(frozen=True)
class Case:
    case_id: str
    stage: str
    query: str
    family: str
    expected_answer: str
    note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


class FamilyAwareReadinessNano:
    """
    A compact nano model for the readiness-policy question:

    - strict: answer only after every major stage is complete
    - core_global: answer after atoms+graph, regardless of query family
    - family_aware: answer after atoms+graph for core families, but require
      organized/episode readiness when the query contract depends on them
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.readiness = Readiness()
        self.atoms: list[dict[str, Any]] = []
        self.graph_hits: list[dict[str, Any]] = []
        self.organized_hits: list[dict[str, Any]] = []

    def ingest(self) -> None:
        self.readiness.messages_persisted = True
        self.atoms = [
            {
                "layer": "fact",
                "content": "Maya introduced Jon to Lena on 2025-05-18.",
            },
            {
                "layer": "fact",
                "content": "Gina planned to move to Lisbon after leaving Figma.",
            },
        ]
        self.graph_hits = [
            {
                "layer": "graph",
                "content": "Maya --introduced--> Jon and Maya --introduced--> Lena",
            }
        ]
        self.readiness.atoms_ready = True
        self.readiness.graph_ready = True
        self.readiness.qa_ready = self._base_qa_ready()

    def run_heavy_stages(self) -> None:
        self.organized_hits = [
            {
                "layer": "topic_dossier",
                "content": "Move to Lisbon dossier: after leaving Figma, Gina planned the move, signed a lease, and prepared the relocation.",
            }
        ]
        self.readiness.organized_ready = True
        self.readiness.episode_ready = True
        self.readiness.qa_ready = self._base_qa_ready()

    def plan(self, query: str, family: str) -> QueryPlan:
        if family == "relational":
            return QueryPlan(
                family=family,
                required_layers=("graph", "fact"),
                primary_reader="graph",
            )
        if family == "longitudinal":
            return QueryPlan(
                family=family,
                required_layers=("topic_dossier", "fact"),
                primary_reader="topic_dossier",
            )
        return QueryPlan(family=family, required_layers=("fact",), primary_reader="fact")

    def answer(self, query: str, family: str) -> dict[str, Any]:
        plan = self.plan(query, family)
        allowed, reason = self._can_answer(plan)
        if not allowed:
            return {
                "status": "not_ready",
                "answer": "unknown",
                "reason": reason,
                "plan": asdict(plan),
                "readiness": asdict(self.readiness),
                "hits": [],
            }

        if family == "relational":
            hits = self.graph_hits + self.atoms[:1]
            answer = "Maya"
        elif family == "longitudinal":
            hits = self.organized_hits + self.atoms[1:2]
            if self.organized_hits:
                answer = "Gina planned to move to Lisbon after leaving Figma."
            else:
                answer = "Gina planned something after leaving Figma."
        else:
            hits = self.atoms[:1]
            answer = self.atoms[0]["content"]
        return {
            "status": "ready",
            "answer": answer,
            "reason": reason,
            "plan": asdict(plan),
            "readiness": asdict(self.readiness),
            "hits": hits,
        }

    def _base_qa_ready(self) -> bool:
        if self.mode == "strict":
            return (
                self.readiness.messages_persisted
                and self.readiness.atoms_ready
                and self.readiness.graph_ready
                and self.readiness.organized_ready
                and self.readiness.episode_ready
            )
        if self.mode in {"core_global", "family_aware"}:
            return (
                self.readiness.messages_persisted
                and self.readiness.atoms_ready
                and self.readiness.graph_ready
            )
        return False

    def _can_answer(self, plan: QueryPlan) -> tuple[bool, str]:
        if self.mode == "strict":
            return self.readiness.qa_ready, "strict barrier"

        if self.mode == "core_global":
            return self.readiness.qa_ready, "core-global barrier"

        if self.mode == "family_aware":
            if not self.readiness.qa_ready:
                return False, "core stages incomplete"
            if "topic_dossier" in plan.required_layers and not self.readiness.organized_ready:
                return False, "organized stage required for this family"
            if "episode" in plan.required_layers and not self.readiness.episode_ready:
                return False, "episode stage required for this family"
            return True, "family-aware barrier"

        return False, "unknown mode"


def build_cases() -> list[Case]:
    return [
        Case(
            case_id="pre_relation",
            stage="before_heavy_stages",
            query="Who introduced Jon to Lena?",
            family="relational",
            expected_answer="Maya",
            note="A core graph+fact query should be answerable once atoms and graph are ready.",
        ),
        Case(
            case_id="pre_longitudinal",
            stage="before_heavy_stages",
            query="What was Gina's latest Lisbon move plan after leaving Figma?",
            family="longitudinal",
            expected_answer="unknown",
            note="A topic-dossier query should wait for organized memory instead of being globally unlocked.",
        ),
        Case(
            case_id="post_relation",
            stage="after_heavy_stages",
            query="Who introduced Jon to Lena?",
            family="relational",
            expected_answer="Maya",
            note="After full consolidation, every mode should answer correctly.",
        ),
        Case(
            case_id="post_longitudinal",
            stage="after_heavy_stages",
            query="What was Gina's latest Lisbon move plan after leaving Figma?",
            family="longitudinal",
            expected_answer="Gina planned to move to Lisbon after leaving Figma.",
            note="Once dossier/organized layers are ready, the longitudinal query should open.",
        ),
    ]


def is_correct(case: Case, answer: dict[str, Any]) -> bool:
    if case.expected_answer == "unknown":
        return answer["status"] == "not_ready"
    return answer["answer"] == case.expected_answer


def run_ablation() -> dict[str, Any]:
    systems = {
        "strict": FamilyAwareReadinessNano("strict"),
        "core_global": FamilyAwareReadinessNano("core_global"),
        "family_aware": FamilyAwareReadinessNano("family_aware"),
    }
    for system in systems.values():
        system.ingest()

    cases = build_cases()
    rows: list[dict[str, Any]] = []
    summary = {name: {"correct": 0, "total": 0} for name in systems}

    for case in cases:
        if case.stage == "after_heavy_stages":
            for system in systems.values():
                if not system.readiness.organized_ready:
                    system.run_heavy_stages()
        for name, system in systems.items():
            answer = system.answer(case.query, case.family)
            ok = is_correct(case, answer)
            summary[name]["correct"] += int(ok)
            summary[name]["total"] += 1
            rows.append(
                {
                    "case_id": case.case_id,
                    "stage": case.stage,
                    "system": name,
                    "query": case.query,
                    "family": case.family,
                    "expected_answer": case.expected_answer,
                    "answer": answer,
                    "ok": ok,
                    "note": case.note,
                }
            )

    return {"summary": summary, "rows": rows}


def render_html(report: dict[str, Any]) -> str:
    summary_cards = "".join(
        f"""
        <div class="metric">
          <div class="label">{esc(name)}</div>
          <div class="value">{info['correct']}/{info['total']}</div>
        </div>
        """
        for name, info in report["summary"].items()
    )
    rows_html = "".join(
        f"""
        <tr>
          <td>{esc(row['stage'])}</td>
          <td><code>{esc(row['case_id'])}</code></td>
          <td>{esc(row['system'])}</td>
          <td>{esc(row['family'])}</td>
          <td>{esc(row['query'])}</td>
          <td>{esc(row['answer']['status'])}</td>
          <td>{esc(row['answer']['answer'])}</td>
          <td>{'pass' if row['ok'] else 'fail'}</td>
        </tr>
        <tr class="detail">
          <td colspan="8">
            <div class="mini">reason={esc(row['answer']['reason'])}</div>
            <div class="mini">plan={esc(row['answer']['plan'])}</div>
            <div class="mini">readiness={esc(row['answer']['readiness'])}</div>
            <div class="note">{esc(row['note'])}</div>
          </td>
        </tr>
        """
        for row in report["rows"]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Family-Aware Readiness Ablation</title>
  <style>
    :root {{
      --bg:#f6f7fb; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#e5e7eb; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; line-height:1.6; }}
    .wrap {{ width:min(1200px, calc(100vw - 32px)); margin:24px auto 48px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:20px 22px; margin-bottom:16px; }}
    h1,h2 {{ margin:0 0 12px; }}
    p {{ margin:8px 0; }}
    .muted,.mini,.note {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:12px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:#fafafa; }}
    .metric .label {{ font-size:12px; color:var(--muted); text-transform:uppercase; }}
    .metric .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
    .callout {{ background:#eff6ff; border-left:4px solid var(--blue); border-radius:8px; padding:12px 14px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; text-transform:uppercase; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="panel">
      <h1>Nano Family-Aware Readiness Ablation</h1>
      <p class="muted">
        这个实验对应主仓里的 <code>qa_readiness_mode=strict|core</code>，但再往前推一步：
        <b>readiness 不该只是全局开关，还应该和 query family 需要的 memory plane 对齐。</b>
      </p>
      <div class="callout">
        结论预期是：<code>strict</code> 太保守，<code>core_global</code> 太粗，<code>family_aware</code> 更像一个真正的 systems policy。
      </div>
      <div class="metrics">{summary_cards}</div>
    </section>

    <section class="panel">
      <h2>Results</h2>
      <table>
        <thead>
          <tr>
            <th>Stage</th>
            <th>Case</th>
            <th>System</th>
            <th>Family</th>
            <th>Query</th>
            <th>Status</th>
            <th>Answer</th>
            <th>Judge</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    report = run_ablation()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(report)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    PUBLIC_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
