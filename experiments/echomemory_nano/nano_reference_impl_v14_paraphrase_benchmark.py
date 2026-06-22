#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nano_reference_impl_v14 import EchoMemoryNanoReferenceV14, QueryPlan


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_reference_impl_v14_paraphrase_benchmark_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_reference_v14_paraphrase_benchmark_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


@dataclass
class QueryVariant:
    qid: str
    family: str
    query: str
    query_time: str
    expected_keywords: list[str]


class BaselineReferenceV14(EchoMemoryNanoReferenceV14):
    """Original, narrower query-family cues kept for comparison."""

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if re.search(r"\b(when|yesterday|last week|before|after|date|time)\b", q):
            return QueryPlan("temporal", "temporal_tree", ["graph", "atom"], ["temporal_tree", "event", "event_time"], "Chronology-heavy query.")
        if re.search(r"\b(who|through whom|introduced|helped|connected|relationship)\b", q):
            return QueryPlan("relational", "graph", ["atom", "temporal_tree"], ["graph", "fact", "path_grounding"], "Relation-heavy query.")
        if re.search(r"\b(status|latest|progress|evolve|evolution|timeline|how did)\b", q):
            return QueryPlan("longitudinal", "topic_dossier", ["atom", "temporal_tree"], ["topic_dossier", "fact"], "Cross-session topic evolution query.")
        if re.search(r"\b(image|photo|screenshot|shown|visible|look like|ocr)\b", q):
            return QueryPlan("visual", "graph", ["atom"], ["image_evidence", "fact"], "Visual evidence query.")
        if re.search(r"\b(can you answer|ready|qa ready|answer now)\b", q):
            return QueryPlan("readiness", "readiness", [], ["readiness"], "Lifecycle / answerability query.")
        return QueryPlan("general", "atom", ["topic_dossier"], ["fact"], "General factual query.")

    def _answer(self, query: str, plan: QueryPlan, hits: list[Any], missing: list[str]) -> str:
        if plan.family == "readiness":
            return "ready" if self.readiness.qa_ready else "not ready"
        if missing:
            return "unknown"
        top = hits[0] if hits else None
        if top is None:
            return "unknown"
        if plan.family == "temporal":
            for hit in hits:
                if hit.trace.get("event_time"):
                    return str(hit.trace["event_time"])
                if hit.event_time:
                    return hit.event_time
            return "unknown"
        if plan.family == "relational":
            for hit in hits:
                ents = self._extract_entities(hit.content)
                if len(ents) >= 2:
                    return ", ".join(ents[:2])
            return top.content
        if plan.family == "longitudinal":
            timeline = top.trace.get("timeline", [])
            return "\n".join(timeline[:3]) if timeline else top.content
        if plan.family == "visual":
            return top.content.split("\n")[0]
        return top.content


def build_demo_memory(cls: type[EchoMemoryNanoReferenceV14]) -> EchoMemoryNanoReferenceV14:
    mem = cls()
    mem.append_text(
        role="user",
        write_time="2026-03-01T09:00:00Z",
        topic_hint="apartment_lease",
        content="Maya found an apartment on Rua Augusta 14 on 2026-03-01.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-05T11:00:00Z",
        topic_hint="apartment_lease",
        content="Maya showed the lease screenshot with move-in date 2026-03-20.",
    )
    mem.append_image(
        role="user",
        write_time="2026-03-05T11:05:00Z",
        topic_hint="apartment_lease",
        linked_subject="Maya",
        caption="Lease contract screenshot",
        ocr="Rua Augusta 14 move-in 2026-03-20",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-12T14:00:00Z",
        topic_hint="apartment_lease",
        content="The landlord delayed the handover and the move-in shifted to 2026-03-27.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-02T10:00:00Z",
        topic_hint="visa_process",
        content="Maya started the visa paperwork and Nora helped Maya prepare the financial statement on 2026-03-02.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-18T15:00:00Z",
        topic_hint="visa_process",
        content="Maya approved the visa process after the consulate received the residence document on 2026-03-18.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-03T09:00:00Z",
        topic_hint="product_launch",
        content="Lena started the beta launch plan for 2026-04-10.",
    )
    mem.append_text(
        role="user",
        write_time="2026-03-19T18:00:00Z",
        topic_hint="product_launch",
        content="Lena confirmed the launch date moved to 2026-04-24 after the payment bug fix.",
    )
    mem.build()
    return mem


def benchmark_cases() -> list[QueryVariant]:
    return [
        QueryVariant("temporal_1", "temporal", "When did Maya start the visa paperwork?", "2026-03-20", ["2026-03-02"]),
        QueryVariant("temporal_2", "temporal", "On what date did Maya begin the visa paperwork?", "2026-03-20", ["2026-03-02"]),
        QueryVariant("temporal_3", "temporal", "What date marks the start of Maya's visa paperwork?", "2026-03-20", ["2026-03-02"]),
        QueryVariant("rel_1", "relational", "Who helped Maya with the visa paperwork?", "2026-03-20", ["Nora"]),
        QueryVariant("rel_2", "relational", "Through whom did Maya get help for the visa paperwork?", "2026-03-20", ["Nora"]),
        QueryVariant("rel_3", "relational", "Who assisted Maya on the visa paperwork?", "2026-03-20", ["Nora"]),
        QueryVariant("long_1", "longitudinal", "How did the apartment lease situation evolve?", "2026-03-20", ["2026-03-27"]),
        QueryVariant("long_2", "longitudinal", "What changed over time in the apartment lease situation?", "2026-03-20", ["2026-03-27"]),
        QueryVariant("long_3", "longitudinal", "Give the timeline of the apartment lease updates.", "2026-03-20", ["Rua Augusta 14", "2026-03-27"]),
        QueryVariant("vis_1", "visual", "What was shown in the lease screenshot?", "2026-03-20", ["Rua Augusta 14"]),
        QueryVariant("vis_2", "visual", "What appears in the lease contract image?", "2026-03-20", ["Rua Augusta 14"]),
        QueryVariant("vis_3", "visual", "Which address is visible in the lease screenshot?", "2026-03-20", ["Rua Augusta 14"]),
        QueryVariant("ready_1", "readiness", "Can you answer now?", "2026-03-20", ["ready"]),
        QueryVariant("ready_2", "readiness", "Is the memory ready for QA now?", "2026-03-20", ["ready"]),
        QueryVariant("ready_3", "readiness", "Can the system answer at this point?", "2026-03-20", ["ready"]),
    ]


def grade(answer: str, expected_keywords: list[str]) -> bool:
    lowered = answer.lower()
    return all(keyword.lower() in lowered for keyword in expected_keywords)


def run_system(name: str, mem: EchoMemoryNanoReferenceV14, cases: list[QueryVariant]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    family_stats: dict[str, dict[str, int]] = {}
    for case in cases:
        result = mem.retrieve(case.query, case.query_time)
        answer = result["answer"]
        answer_ok = grade(answer, case.expected_keywords)
        family_ok = result["plan"]["family"] == case.family
        contract_ok = bool(result["contract_ok"])
        stats = family_stats.setdefault(case.family, {"count": 0, "answer_ok": 0, "family_ok": 0, "contract_ok": 0})
        stats["count"] += 1
        stats["answer_ok"] += int(answer_ok)
        stats["family_ok"] += int(family_ok)
        stats["contract_ok"] += int(contract_ok)
        rows.append(
            {
                "qid": case.qid,
                "expected_family": case.family,
                "query": case.query,
                "expected_keywords": case.expected_keywords,
                "predicted_family": result["plan"]["family"],
                "primary_reader": result["plan"]["primary_reader"],
                "contract_ok": contract_ok,
                "answer": answer,
                "answer_ok": answer_ok,
                "family_ok": family_ok,
            }
        )
    overall = {
        "count": len(cases),
        "answer_ok": sum(int(row["answer_ok"]) for row in rows),
        "family_ok": sum(int(row["family_ok"]) for row in rows),
        "contract_ok": sum(int(row["contract_ok"]) for row in rows),
    }
    return {"name": name, "overall": overall, "family_stats": family_stats, "rows": rows}


def render_html(payload: dict[str, Any]) -> str:
    systems = payload["systems"]
    summary_rows = []
    for system in systems:
        overall = system["overall"]
        summary_rows.append(
            f"<tr><td>{esc(system['name'])}</td><td>{overall['answer_ok']} / {overall['count']}</td><td>{overall['family_ok']} / {overall['count']}</td><td>{overall['contract_ok']} / {overall['count']}</td></tr>"
        )

    detail_sections = []
    for system in systems:
        family_rows = []
        for family, stats in system["family_stats"].items():
            family_rows.append(
                f"<tr><td>{esc(family)}</td><td>{stats['answer_ok']} / {stats['count']}</td><td>{stats['family_ok']} / {stats['count']}</td><td>{stats['contract_ok']} / {stats['count']}</td></tr>"
            )
        case_rows = []
        for row in system["rows"]:
            case_rows.append(
                "<tr>"
                f"<td>{esc(row['qid'])}</td>"
                f"<td>{esc(row['query'])}</td>"
                f"<td>{esc(row['expected_family'])}</td>"
                f"<td>{esc(row['predicted_family'])}</td>"
                f"<td>{esc(row['primary_reader'])}</td>"
                f"<td>{'yes' if row['contract_ok'] else 'no'}</td>"
                f"<td>{'yes' if row['family_ok'] else 'no'}</td>"
                f"<td>{'yes' if row['answer_ok'] else 'no'}</td>"
                f"<td>{esc(row['answer'])}</td>"
                "</tr>"
            )
        detail_sections.append(
            f"""
            <section class="section">
              <h2>{esc(system['name'])}</h2>
              <table>
                <thead><tr><th>Family</th><th>Answer OK</th><th>Family OK</th><th>Contract OK</th></tr></thead>
                <tbody>{''.join(family_rows)}</tbody>
              </table>
              <div style="height:12px"></div>
              <table>
                <thead><tr><th>ID</th><th>Query</th><th>Expected Family</th><th>Predicted Family</th><th>Primary</th><th>Contract</th><th>Family</th><th>Answer</th><th>Output</th></tr></thead>
                <tbody>{''.join(case_rows)}</tbody>
              </table>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Reference v14 Paraphrase Benchmark</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#d9e2ec; --text:#18212f; --muted:#617184; --blue:#2563eb; --green:#0f766e;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    .wrap {{ max-width:1200px; margin:0 auto; padding:28px 18px 48px; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:22px; margin-bottom:16px; }}
    h1,h2 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }} h2 {{ font-size:20px; }}
    p {{ margin:0 0 10px; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    code {{ background:#f3f6fb; border:1px solid #e5ebf3; border-radius:6px; padding:1px 5px; font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    .tag {{ display:inline-block; padding:4px 10px; border-radius:999px; background:#eef4ff; color:var(--blue); font-size:12px; font-weight:600; margin-right:8px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ text-align:left; vertical-align:top; padding:10px 8px; border-top:1px solid var(--line); }}
    th {{ font-size:12px; color:var(--muted); background:#fbfcfe; }}
    .good {{ color:var(--green); font-weight:700; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano Reference v14 Paraphrase Benchmark</h1>
      <p>
        这不是 benchmark accuracy 报告，而是一份更窄、更干净的泛化性证据：
        看同一个结构化 memory 系统在 <b>query-family paraphrase</b> 下是否还能保持
        family routing、contract completion 和 answer correctness。
      </p>
      <div style="margin-top:12px;">
        <span class="tag">generic</span>
        <span class="tag">no dataset keyword hacks</span>
        <span class="tag">paraphrase robustness</span>
        <span class="tag">query family</span>
      </div>
    </section>

    <section class="section">
      <h2>结论</h2>
      <ul>
        <li>Baseline 版在某些 paraphrase 上并不是检索不到，而是 <b>query family 识别窄</b>，导致进错 primary backbone。</li>
        <li>改进版没有加任何数据集实体词表，只是更泛化地扩展了 longitudinal / visual / readiness 的 family cue，并让视觉答案更偏向 OCR-bearing line。</li>
        <li>这更接近论文真正要说的点：<b>泛化性改进应该是结构性的 family routing 改进，而不是 benchmark-specific keyword hacks。</b></li>
      </ul>
      <table>
        <thead><tr><th>System</th><th>Answer OK</th><th>Family OK</th><th>Contract OK</th></tr></thead>
        <tbody>{''.join(summary_rows)}</tbody>
      </table>
    </section>

    {''.join(detail_sections)}
  </div>
</body>
</html>
"""


def main() -> None:
    cases = benchmark_cases()
    baseline = build_demo_memory(BaselineReferenceV14)
    improved = build_demo_memory(EchoMemoryNanoReferenceV14)
    payload = {
        "note": "Paraphrase robustness benchmark for query-family routing. No dataset-specific entity list or benchmark keyword table is used.",
        "systems": [
            run_system("baseline_reference_v14", baseline, cases),
            run_system("improved_reference_v14", improved, cases),
        ],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
