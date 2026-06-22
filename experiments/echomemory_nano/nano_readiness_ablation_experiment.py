#!/usr/bin/env python3
from __future__ import annotations

import html
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
NANO_PATH = ROOT / "nano_readiness_temporal_graph.py"
OUT_JSON = ROOT / "nano_readiness_ablation_results.json"
OUT_HTML = ROOT / "nano_readiness_ablation_report.html"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass
class EvalCase:
    case_id: str
    stage: str
    query: str
    expected: str
    judge_note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def build_turns() -> list[tuple[str, str, str]]:
    return [
        ("user", "Yesterday Jon lost his job at the bank.", "2025-05-10"),
        ("user", "Two days ago Gina visited Rome for a design fair.", "2025-05-15"),
        ("user", "Maya introduced Jon to Lena.", "2025-05-18"),
    ]


def setup_systems() -> dict[str, Any]:
    mod = load_module(NANO_PATH, "echomemory_nano_readiness_ablation")
    cls = mod.EchoMemoryReadinessTemporalNano
    systems = {
        "baseline": cls(
            temporal_normalize=False,
            graph_first=False,
            readiness_gate=False,
        ),
        "temporal_graph": cls(
            temporal_normalize=True,
            graph_first=True,
            readiness_gate=False,
        ),
        "full": cls(
            temporal_normalize=True,
            graph_first=True,
            readiness_gate=True,
        ),
    }
    for system in systems.values():
        for role, text, created_at in build_turns():
            system.append_turn(role, text, created_at)
        system.run_hot_path()
    return systems


def answer_ok(case_id: str, answer: dict[str, Any]) -> bool:
    if case_id == "readiness_pre_cold":
        return answer.get("status") == "not_ready"
    if case_id == "temporal_date_job":
        return answer.get("answer") == "2025-05-09"
    if case_id == "temporal_date_rome":
        return answer.get("answer") == "2025-05-13"
    if case_id == "temporal_chain_before_intro":
        top = "\n".join(str(hit.get("content", "")) for hit in answer.get("hits", [])[:2]).lower()
        return "gina visited rome" in top and "2025-05-13" in top
    if case_id == "relation_intro":
        payload = str(answer.get("answer", "")).lower() + "\n" + "\n".join(
            str(hit.get("content", "")).lower() for hit in answer.get("hits", [])[:2]
        )
        return "maya" in payload and "jon" in payload and "lena" in payload
    return False


def run_experiment() -> dict[str, Any]:
    systems = setup_systems()
    rows: list[dict[str, Any]] = []

    pre_case = EvalCase(
        case_id="readiness_pre_cold",
        stage="before_cold_path",
        query="When did Jon lose his job?",
        expected="status should be not_ready",
        judge_note="This checks whether a system treats persisted messages as immediately answerable, or waits until memory is QA-ready.",
    )
    for name, system in systems.items():
        answer = system.answer(pre_case.query)
        rows.append(
            {
                "case_id": pre_case.case_id,
                "stage": pre_case.stage,
                "system": name,
                "query": pre_case.query,
                "expected": pre_case.expected,
                "judge_note": pre_case.judge_note,
                "answer": answer,
                "ok": answer_ok(pre_case.case_id, answer),
            }
        )

    for system in systems.values():
        system.run_cold_path()

    cases = [
        EvalCase(
            case_id="temporal_date_job",
            stage="after_cold_path",
            query="When did Jon lose his job?",
            expected="2025-05-09",
            judge_note="Baseline tends to confuse write time with story time; improved systems should recover the resolved story date.",
        ),
        EvalCase(
            case_id="temporal_date_rome",
            stage="after_cold_path",
            query="When did Gina visit Rome?",
            expected="2025-05-13",
            judge_note="A second relative-time case to show this is not a one-off rule.",
        ),
        EvalCase(
            case_id="temporal_chain_before_intro",
            stage="after_cold_path",
            query="What happened before Maya introduced Jon to Lena?",
            expected="Gina visited Rome on 2025-05-13",
            judge_note="This is the graph-specific case: the answer should come from the temporal_next chain, not just token overlap.",
        ),
        EvalCase(
            case_id="relation_intro",
            stage="after_cold_path",
            query="Who introduced Jon to Lena?",
            expected="Maya",
            judge_note="A sanity check that the improved system still handles direct relation questions.",
        ),
    ]
    for case in cases:
        for name, system in systems.items():
            answer = system.answer(case.query)
            rows.append(
                {
                    "case_id": case.case_id,
                    "stage": case.stage,
                    "system": name,
                    "query": case.query,
                    "expected": case.expected,
                    "judge_note": case.judge_note,
                    "answer": answer,
                    "ok": answer_ok(case.case_id, answer),
                }
            )

    summary: dict[str, Any] = {}
    for name in systems:
        subset = [row for row in rows if row["system"] == name]
        summary[name] = {
            "correct": sum(1 for row in subset if row["ok"]),
            "total": len(subset),
        }

    return {
        "turns": [
            {"role": role, "text": text, "created_at": created_at}
            for role, text, created_at in build_turns()
        ],
        "rows": rows,
        "summary": summary,
    }


def render_report(data: dict[str, Any]) -> str:
    rows = data["rows"]
    summary = data["summary"]
    turns = data["turns"]

    summary_cards = "".join(
        f"""
        <div class="metric">
          <div class="label">{esc(name)}</div>
          <div class="value">{esc(info['correct'])}/{esc(info['total'])}</div>
        </div>
        """
        for name, info in summary.items()
    )

    turns_html = "".join(
        f"<li><code>{esc(turn['created_at'])}</code> · {esc(turn['text'])}</li>"
        for turn in turns
    )

    rows_html = "".join(
        f"""
        <tr>
          <td>{esc(row['stage'])}</td>
          <td><code>{esc(row['case_id'])}</code></td>
          <td>{esc(row['system'])}</td>
          <td>{esc(row['query'])}</td>
          <td>{esc(row['expected'])}</td>
          <td><span class="badge {'ok' if row['ok'] else 'bad'}">{'pass' if row['ok'] else 'fail'}</span></td>
          <td>{esc(row['answer'].get('status'))}</td>
          <td>{esc(row['answer'].get('answer'))}</td>
        </tr>
        <tr class="detail">
          <td colspan="8">
            <div class="note">{esc(row['judge_note'])}</div>
            <div class="mini">plan={esc(row['answer'].get('plan'))}</div>
            <div class="mini">readiness={esc(row['answer'].get('readiness'))}</div>
            <div class="mini">top_hits={esc(row['answer'].get('hits', [])[:2])}</div>
          </td>
        </tr>
        """
        for row in rows
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Readiness Temporal Ablation</title>
  <style>
    :root {{
      --bg:#f6f7fb; --panel:#fff; --text:#1f2937; --muted:#667085; --line:#e5e7eb;
      --blue:#2563eb; --green:#047857; --red:#b42318; --amber:#b45309;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:var(--bg); color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; line-height:1.6;
    }}
    .wrap {{ width:min(1200px, calc(100vw - 32px)); margin:24px auto 48px; }}
    .panel {{
      background:var(--panel); border:1px solid var(--line); border-radius:10px;
      padding:20px 22px; margin-bottom:16px;
    }}
    h1,h2,h3 {{ margin:0 0 12px; }}
    p {{ margin:8px 0; }}
    .muted,.mini,.note {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:12px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:#fafafa; }}
    .metric .label {{ font-size:12px; color:var(--muted); text-transform:uppercase; }}
    .metric .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
    .callout {{
      background:#eff6ff; border-left:4px solid var(--blue); border-radius:8px; padding:12px 14px;
    }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th,td {{ border-top:1px solid var(--line); padding:10px; text-align:left; vertical-align:top; }}
    th {{ background:#fafafa; color:var(--muted); font-size:12px; border-top:0; }}
    .badge {{
      display:inline-block; border-radius:999px; padding:3px 8px; font-size:12px; font-weight:600;
      color:#fff;
    }}
    .badge.ok {{ background:var(--green); }}
    .badge.bad {{ background:var(--red); }}
    .detail td {{ background:#fcfcfd; }}
    code {{ background:#f4f4f5; padding:2px 5px; border-radius:5px; font-size:12px; }}
    @media (max-width: 900px) {{
      .metrics {{ grid-template-columns:1fr; }}
      .wrap {{ width:min(100vw - 20px, 1200px); }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="panel">
      <h1>EchoMemory Nano Ablation: Readiness + Temporal Normalize + Graph-First</h1>
      <p class="muted">
        这个 nano 实验不追求复杂，而是专门对准 EchoMemory 当前最关键的三个系统问题：
        记忆是否 QA-ready、相对时间是否被解析成 story time、以及 graph 是否真的改变时间链问题的检索。
      </p>
      <div class="callout">
        这里对比三套系统：
        <strong>baseline</strong>（无时间归一、无 graph-first、无 readiness gate）、
        <strong>temporal_graph</strong>（有时间归一 + graph-first）、
        <strong>full</strong>（再加 readiness gate）。
      </div>
      <div class="metrics">{summary_cards}</div>
    </section>

    <section class="panel">
      <h2>测试场景</h2>
      <ul>{turns_html}</ul>
    </section>

    <section class="panel">
      <h2>为什么这组实验重要</h2>
      <ul>
        <li><strong>relative time：</strong> “Yesterday / Two days ago” 会暴露 write time 和 story time 混淆。</li>
        <li><strong>temporal chain：</strong> “What happened before ...” 会暴露 graph 的 <code>temporal_next</code> 是否只是摆设。</li>
        <li><strong>readiness：</strong> 如果 messages 已写入，但 graph / organized 尚未构建，系统是否还会贸然回答。</li>
      </ul>
    </section>

    <section class="panel">
      <h2>逐题结果</h2>
      <table>
        <thead>
          <tr>
            <th>阶段</th>
            <th>Case</th>
            <th>系统</th>
            <th>问题</th>
            <th>期望</th>
            <th>结果</th>
            <th>Status</th>
            <th>Answer</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>可写进论文的结论</h2>
      <ul>
        <li><strong>readiness gate</strong> 不是“更保守的 UI”，而是一个系统正确性约束：写入成功不等于 QA-ready。</li>
        <li><strong>temporal normalize</strong> 直接改善 relative-time 问题，不是只靠后端 LLM 猜时间。</li>
        <li><strong>graph-first retrieval</strong> 在时间链问题上能产生与 flat lexical retrieval 不同的行为，这一点是可以做 ablation 的。</li>
      </ul>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    data = run_experiment()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_report(data), encoding="utf-8")
    print(OUT_JSON)
    print(OUT_HTML)


if __name__ == "__main__":
    main()
