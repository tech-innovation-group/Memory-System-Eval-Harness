#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_selfcheck_executor_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_selfcheck_executor_ablation_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


@dataclass(frozen=True)
class Readiness:
    messages_persisted: bool = True
    atoms_ready: bool = True
    graph_ready: bool = True
    organized_ready: bool = True
    qa_ready: bool = True


@dataclass(frozen=True)
class Hit:
    source: str
    layer: str
    score: float
    content: str
    answer_hint: str
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    case_id: str
    family: str
    query: str
    required_layers: tuple[str, ...]
    expected_answer: str
    note: str
    readiness: Readiness
    primary_hits: tuple[Hit, ...]
    supporting_reads: dict[str, tuple[Hit, ...]]


def present_layers(hits: list[Hit]) -> set[str]:
    present = {hit.layer for hit in hits}
    for hit in hits:
        if hit.layer == "graph":
            present.add("event")
            if hit.trace.get("path_grounding"):
                present.add("path_grounding")
        if hit.layer == "fact":
            if hit.trace.get("event_time"):
                present.add("event_time")
        if hit.layer == "image_evidence":
            present.add("graph")
            present.add("event")
        if hit.layer == "readiness":
            present.add("readiness")
    return present


def reader_for_missing(layer: str) -> str | None:
    mapping = {
        "event": "graph",
        "graph": "graph",
        "path_grounding": "graph",
        "fact": "atom",
        "event_time": "atom",
        "topic_dossier": "dossier",
        "image_evidence": "graph",
        "readiness": "readiness",
    }
    return mapping.get(layer)


def dedup_sort(hits: list[Hit]) -> list[Hit]:
    best: dict[str, Hit] = {}
    for hit in hits:
        prev = best.get(hit.source)
        if prev is None or hit.score > prev.score:
            best[hit.source] = hit
    return sorted(best.values(), key=lambda item: (-item.score, item.source))


def direct_answer(hits: list[Hit]) -> str:
    if not hits:
        return "unknown"
    return hits[0].answer_hint


def executive_answer(case: Case, hits: list[Hit], missing: list[str]) -> str:
    if not case.readiness.qa_ready:
        return "not_ready"
    if missing:
        return "unknown"
    return direct_answer(hits)


def run_primary_direct(case: Case) -> dict[str, Any]:
    hits = dedup_sort(list(case.primary_hits))
    present = present_layers(hits)
    missing = [layer for layer in case.required_layers if layer not in present]
    answer = direct_answer(hits)
    return {
        "mode": "primary_direct",
        "answer": answer,
        "correct": answer == case.expected_answer,
        "present_layers": sorted(present),
        "missing_layers": missing,
        "used_readers": ["primary"],
        "note": "Answer directly from primary hits without any review.",
        "hits": [asdict(hit) for hit in hits],
    }


def run_advisory_only(case: Case) -> dict[str, Any]:
    hits = dedup_sort(list(case.primary_hits))
    present = present_layers(hits)
    missing = [layer for layer in case.required_layers if layer not in present]
    answer = direct_answer(hits)
    return {
        "mode": "advisory_only",
        "answer": answer,
        "correct": answer == case.expected_answer,
        "present_layers": sorted(present),
        "missing_layers": missing,
        "used_readers": ["primary"],
        "note": (
            "Self-check diagnoses the gap but does not change the answer path."
            if missing or not case.readiness.qa_ready
            else "Primary evidence already satisfies the contract."
        ),
        "hits": [asdict(hit) for hit in hits],
    }


def run_executive_policy(case: Case) -> dict[str, Any]:
    if not case.readiness.qa_ready:
        return {
            "mode": "executive_policy",
            "answer": "not_ready",
            "correct": case.expected_answer == "not_ready",
            "present_layers": [],
            "missing_layers": list(case.required_layers),
            "used_readers": ["readiness_gate"],
            "note": "Readiness gate blocks answering before the required memory planes are answer-ready.",
            "hits": [],
        }

    hits = dedup_sort(list(case.primary_hits))
    used_readers = ["primary"]
    present = present_layers(hits)
    missing = [layer for layer in case.required_layers if layer not in present]

    for layer in list(missing):
        reader = reader_for_missing(layer)
        if reader is None or reader in used_readers:
            continue
        extra_hits = list(case.supporting_reads.get(reader, ()))
        if not extra_hits:
            continue
        used_readers.append(reader)
        hits = dedup_sort(hits + extra_hits)
        present = present_layers(hits)
        missing = [need for need in case.required_layers if need not in present]
        if not missing:
            break

    answer = executive_answer(case, hits, missing)
    return {
        "mode": "executive_policy",
        "answer": answer,
        "correct": answer == case.expected_answer,
        "present_layers": sorted(present),
        "missing_layers": missing,
        "used_readers": used_readers,
        "note": (
            "Targeted re-read completes the contract."
            if answer not in {"unknown", "not_ready"}
            else "Policy refuses to answer after review because the contract remains incomplete."
        ),
        "hits": [asdict(hit) for hit in hits],
    }


def build_cases() -> list[Case]:
    return [
        Case(
            case_id="temporal_mention_vs_event",
            family="temporal",
            query="When did the consulate request one more financial statement?",
            required_layers=("temporal_tree", "event", "event_time"),
            expected_answer="2026-04-08",
            note="Primary chronology text mentions the event on 2026-04-09, but the true story time is yesterday = 2026-04-08.",
            readiness=Readiness(),
            primary_hits=(
                Hit(
                    "tree:2026-04-09",
                    "temporal_tree",
                    0.93,
                    "2026-04-09 note: Maya said the consulate requested one more financial statement yesterday.",
                    "2026-04-09",
                ),
            ),
            supporting_reads={
                "graph": (
                    Hit(
                        "graph:event:consulate_request",
                        "graph",
                        0.88,
                        "Event node for consulate request.",
                        "2026-04-08",
                    ),
                ),
                "atom": (
                    Hit(
                        "atom:consulate_request",
                        "fact",
                        0.99,
                        "Consulate requested one more financial statement. event_time=2026-04-08",
                        "2026-04-08",
                        {"event_time": "2026-04-08"},
                    ),
                ),
            },
        ),
        Case(
            case_id="relational_untyped_bridge",
            family="relational",
            query="Who introduced Jon to Lena?",
            required_layers=("graph", "fact", "path_grounding"),
            expected_answer="Maya",
            note="A shared-neighbor graph hit points to Figma, but typed path grounding plus fact support recovers Maya as the introducer.",
            readiness=Readiness(),
            primary_hits=(
                Hit(
                    "graph:shared_neighbor:figma",
                    "graph",
                    0.92,
                    "Figma is connected to Jon and Lena through multiple collaboration edges.",
                    "Figma",
                ),
            ),
            supporting_reads={
                "graph": (
                    Hit(
                        "graph:path:maya_intro",
                        "graph",
                        0.98,
                        "Maya -> introduced -> Jon ; Maya -> introduced -> Lena",
                        "Maya",
                        {"path_grounding": True},
                    ),
                ),
                "atom": (
                    Hit(
                        "atom:maya_intro",
                        "fact",
                        0.97,
                        "Maya introduced Jon to Lena during the spring launch dinner.",
                        "Maya",
                    ),
                ),
            },
        ),
        Case(
            case_id="longitudinal_fragment",
            family="longitudinal",
            query="How did the partnership review evolve over time?",
            required_layers=("topic_dossier", "fact"),
            expected_answer="2026-04-12 kickoff; 2026-05-02 budget revision; 2026-05-20 partner approval.",
            note="A single flat fact gives only the latest fragment; dossier expansion restores the cross-session evolution chain.",
            readiness=Readiness(),
            primary_hits=(
                Hit(
                    "atom:budget_revision",
                    "fact",
                    0.94,
                    "2026-05-02: Board requested one more budget revision.",
                    "Budget revision on 2026-05-02.",
                ),
            ),
            supporting_reads={
                "dossier": (
                    Hit(
                        "dossier:partnership_review",
                        "topic_dossier",
                        0.99,
                        "Topic: partnership_review\n- 2026-04-12 kickoff\n- 2026-05-02 budget revision\n- 2026-05-20 partner approval",
                        "2026-04-12 kickoff; 2026-05-02 budget revision; 2026-05-20 partner approval.",
                    ),
                ),
            },
        ),
        Case(
            case_id="visual_ocr_ambiguous",
            family="visual",
            query="What address was shown in the lease screenshot?",
            required_layers=("image_evidence", "fact"),
            expected_answer="Rua Augusta 14",
            note="Raw OCR contains multiple numeric strings; fact grounding linked to the lease resolves the correct address.",
            readiness=Readiness(),
            primary_hits=(
                Hit(
                    "image:lease_page_2",
                    "image_evidence",
                    0.95,
                    "OCR: Rua Augusta 41 ; ref code 14 ; tenant Maya ; lease screenshot page 2",
                    "Rua Augusta 41",
                ),
            ),
            supporting_reads={
                "atom": (
                    Hit(
                        "atom:lease_address",
                        "fact",
                        0.98,
                        "Maya signed the lease for Rua Augusta 14 in Lisbon.",
                        "Rua Augusta 14",
                    ),
                ),
            },
        ),
        Case(
            case_id="readiness_premature",
            family="readiness_sensitive",
            query="Can the system answer the latest relocation question now?",
            required_layers=("readiness", "topic_dossier"),
            expected_answer="not_ready",
            note="Persisted facts are present, but organized memory is not answer-ready yet.",
            readiness=Readiness(organized_ready=False, qa_ready=False),
            primary_hits=(
                Hit(
                    "atom:relocation_latest",
                    "fact",
                    0.91,
                    "2026-06-01: Partner approval happened for the relocation plan.",
                    "Partner approval happened.",
                ),
            ),
            supporting_reads={
                "readiness": (
                    Hit(
                        "receipt:session",
                        "readiness",
                        1.0,
                        "qa_ready=false; organized_ready=false",
                        "not_ready",
                    ),
                ),
                "dossier": (
                    Hit(
                        "dossier:relocation",
                        "topic_dossier",
                        0.96,
                        "Topic: relocation\n- 2026-05-11 lease signed\n- 2026-06-01 partner approval",
                        "2026-05-11 lease signed; 2026-06-01 partner approval.",
                    ),
                ),
            },
        ),
        Case(
            case_id="relational_should_abstain",
            family="relational",
            query="Who connected Omar to the investor?",
            required_layers=("graph", "fact", "path_grounding"),
            expected_answer="unknown",
            note="Co-occurrence suggests Sasha, but no typed introduction path or grounding fact exists, so the policy should abstain.",
            readiness=Readiness(),
            primary_hits=(
                Hit(
                    "graph:cooccur:sasha",
                    "graph",
                    0.93,
                    "Sasha appears in the same meeting cluster as Omar and the investor.",
                    "Sasha",
                ),
            ),
            supporting_reads={
                "graph": (
                    Hit(
                        "graph:meeting_cluster",
                        "graph",
                        0.89,
                        "Meeting cluster: Omar, Sasha, investor, strategy review.",
                        "Sasha",
                    ),
                ),
                "atom": (
                    Hit(
                        "atom:sasha_meeting",
                        "fact",
                        0.87,
                        "Sasha attended the same strategy review meeting as Omar.",
                        "Sasha",
                    ),
                ),
            },
        ),
        Case(
            case_id="control_direct_fact",
            family="state",
            query="What is Maya's current drink preference?",
            required_layers=("fact",),
            expected_answer="Maya prefers coffee.",
            note="Control case: when the primary fact already satisfies the contract, all modes should agree.",
            readiness=Readiness(),
            primary_hits=(
                Hit(
                    "atom:maya_pref",
                    "fact",
                    0.97,
                    "Maya prefers coffee.",
                    "Maya prefers coffee.",
                ),
            ),
            supporting_reads={},
        ),
    ]


def run_ablation() -> dict[str, Any]:
    cases = build_cases()
    rows: list[dict[str, Any]] = []
    summary = {
        "cases": len(cases),
        "primary_direct_correct": 0,
        "advisory_only_correct": 0,
        "executive_policy_correct": 0,
        "advisory_gap_detected": 0,
        "advisory_detected_but_still_wrong": [],
        "executive_fixed_cases": [],
        "executive_abstained_cases": [],
        "executive_not_ready_cases": [],
    }

    for case in cases:
        direct = run_primary_direct(case)
        advisory = run_advisory_only(case)
        executive = run_executive_policy(case)

        summary["primary_direct_correct"] += int(direct["correct"])
        summary["advisory_only_correct"] += int(advisory["correct"])
        summary["executive_policy_correct"] += int(executive["correct"])

        advisory_detected_gap = bool(advisory["missing_layers"]) or not case.readiness.qa_ready
        summary["advisory_gap_detected"] += int(advisory_detected_gap)
        if advisory_detected_gap and not advisory["correct"]:
            summary["advisory_detected_but_still_wrong"].append(case.case_id)

        if (not direct["correct"] or not advisory["correct"]) and executive["correct"]:
            summary["executive_fixed_cases"].append(case.case_id)
        if executive["answer"] == "unknown":
            summary["executive_abstained_cases"].append(case.case_id)
        if executive["answer"] == "not_ready":
            summary["executive_not_ready_cases"].append(case.case_id)

        rows.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "query": case.query,
                "expected_answer": case.expected_answer,
                "note": case.note,
                "required_layers": list(case.required_layers),
                "readiness": asdict(case.readiness),
                "primary_direct": direct,
                "advisory_only": advisory,
                "executive_policy": executive,
            }
        )

    return {"summary": summary, "rows": rows}


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    cards = []
    for row in report["rows"]:
        cards.append(
            f"""
            <section class="case">
              <div class="case-head">
                <div>
                  <div class="pill">{esc(row['family'])}</div>
                  <h3>{esc(row['case_id'])}</h3>
                  <p class="muted">{esc(row['query'])}</p>
                </div>
                <div class="expect">
                  <div class="label">Expected</div>
                  <div class="value">{esc(row['expected_answer'])}</div>
                </div>
              </div>
              <p class="note">{esc(row['note'])}</p>
              <p class="meta"><b>Required layers:</b> {esc(', '.join(row['required_layers']))}</p>
              <div class="grid">
                {render_mode_box('Primary Direct', row['primary_direct'])}
                {render_mode_box('Advisory Only', row['advisory_only'])}
                {render_mode_box('Executive Policy', row['executive_policy'])}
              </div>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Self-Check Executor Ablation</title>
  <style>
    :root{{--bg:#f5f7fb;--panel:#fff;--line:#dde5ef;--text:#182333;--muted:#607286;--blue:#245cff;--blue-soft:#eef4ff;--green:#0f8c60;--green-soft:#eefaf4;--amber:#a86400;--amber-soft:#fff7ea;--red:#c33c35;--red-soft:#fff3f2;--shadow:0 14px 34px rgba(18,32,51,.08);}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1260px;margin:0 auto;padding:28px 18px 60px}}
    .hero,.panel,.case,.mode{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:28px 30px;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .panel{{padding:18px 20px;margin-bottom:16px}} .case{{padding:18px 20px;margin-bottom:16px}} .mode{{padding:14px}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.22}} h1{{font-size:30px}} h2{{font-size:20px}} h3{{font-size:16px}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}} .note{{color:#334155}} .meta{{font-size:13px;color:#475569}}
    .chips,.kpis,.grid{{display:grid;gap:12px}} .chips{{grid-template-columns:repeat(4,minmax(0,1fr));margin-top:14px}} .kpis{{grid-template-columns:repeat(4,minmax(0,1fr));margin-top:14px}} .grid{{grid-template-columns:repeat(3,minmax(0,1fr));margin-top:12px}}
    .chip,.pill{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;background:var(--blue-soft);color:var(--blue);border:1px solid #d6e2ff}}
    .kpi{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcff}} .kpi .v{{font-size:24px;font-weight:800;line-height:1.1}} .kpi .k{{font-size:12px;color:var(--muted);margin-bottom:4px}}
    .good{{background:var(--green-soft);color:var(--green)}} .bad{{background:var(--red-soft);color:var(--red)}} .warn{{background:var(--amber-soft);color:var(--amber)}}
    .case-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}} .expect{{text-align:right}} .label{{font-size:12px;color:var(--muted)}} .value{{font-weight:700}}
    .mode h4{{margin:0 0 8px;font-size:14px}} .status{{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;font-weight:700;margin-bottom:8px}}
    .status.ok{{background:var(--green-soft);color:var(--green)}} .status.no{{background:var(--red-soft);color:var(--red)}}
    .mono{{font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f7f9fc;border:1px solid var(--line);border-radius:10px;padding:10px 12px;white-space:pre-wrap;overflow-wrap:anywhere}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:5px 0}} code{{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f3f6fb;border:1px solid #dfe7f1;border-radius:4px;padding:1px 5px}}
    @media (max-width:1000px){{.chips,.kpis,.grid{{grid-template-columns:1fr}} .case-head{{display:block}} .expect{{text-align:left;margin-top:8px}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <div class="pill">Nano</div>
      <div class="pill">Self-check</div>
      <div class="pill">Executor</div>
      <h1>EchoMemory Nano Self-Check Executor Ablation</h1>
      <p class="muted">
        这个实验只回答一个机制问题：<b>self-check 只是“会提醒问题”，和 self-check 真正接管 answer-time policy，是不是两回事？</b>
        我们比较三种模式：<code>primary_direct</code>、<code>advisory_only</code>、<code>executive_policy</code>。
      </p>
      <div class="kpis">
        <div class="kpi"><div class="k">Cases</div><div class="v">{summary['cases']}</div><div class="muted">temporal / relational / longitudinal / visual / readiness / abstain</div></div>
        <div class="kpi"><div class="k">Primary</div><div class="v">{summary['primary_direct_correct']}/{summary['cases']}</div><div class="muted">不看契约，直接回答</div></div>
        <div class="kpi"><div class="k">Advisory</div><div class="v">{summary['advisory_only_correct']}/{summary['cases']}</div><div class="muted">会诊断，但不改变决策</div></div>
        <div class="kpi"><div class="k">Executive</div><div class="v">{summary['executive_policy_correct']}/{summary['cases']}</div><div class="muted">会补检索、会拒答、会 readiness gate</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>为什么这条证据重要</h2>
      <ul>
        <li><b>LongMemEval / Self-RAG / Mem-T</b> 一类工作都在暗示同一个事实：memory 系统不只是“搜到没”，还要决定“现在能不能答”。</li>
        <li>如果 <code>self_check</code> 只输出诊断日志，却不改变回答路径，那它更像一个解释器，不像一个 policy。</li>
        <li>这个实验故意不用任何数据集关键词，只用 query family + required layers + supporting readers 的通用机制。</li>
      </ul>
      <p class="meta">
        <b>Summary:</b> advisory 模式在 {summary['advisory_gap_detected']}/{summary['cases']} 个 case 里识别出了缺口，
        但仍然在这些 case 中继续错误回答；executive 模式则修复或正确拦截了
        {len(summary['executive_fixed_cases'])} 个问题 case。
      </p>
      <div class="mono">advisory_detected_but_still_wrong = {json.dumps(summary['advisory_detected_but_still_wrong'], ensure_ascii=False)}
executive_fixed_cases = {json.dumps(summary['executive_fixed_cases'], ensure_ascii=False)}
executive_abstained_cases = {json.dumps(summary['executive_abstained_cases'], ensure_ascii=False)}
executive_not_ready_cases = {json.dumps(summary['executive_not_ready_cases'], ensure_ascii=False)}</div>
    </section>

    {''.join(cards)}
  </div>
</body>
</html>"""


def render_mode_box(title: str, result: dict[str, Any]) -> str:
    status = "ok" if result["correct"] else "no"
    hits = result.get("hits", [])[:3]
    hit_lines = []
    for hit in hits:
        hit_lines.append(
            f"- {hit['layer']} · {hit['source']} · answer_hint={hit['answer_hint']}"
        )
    return f"""
      <div class="mode">
        <div class="status {status}">{'Correct' if result['correct'] else 'Wrong'}</div>
        <h4>{esc(title)}</h4>
        <p><b>Answer:</b> {esc(result['answer'])}</p>
        <p><b>Missing:</b> {esc(', '.join(result['missing_layers']) or '-')}</p>
        <p><b>Readers:</b> {esc(', '.join(result['used_readers']))}</p>
        <p class="meta">{esc(result['note'])}</p>
        <div class="mono">{esc(chr(10).join(hit_lines) or '-')}</div>
      </div>
    """


def main() -> None:
    report = run_ablation()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
