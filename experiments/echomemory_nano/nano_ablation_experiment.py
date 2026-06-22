#!/usr/bin/env python3
from __future__ import annotations

import html
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
V1_PATH = ROOT / "nano_temporal_graph.py"
V2_PATH = ROOT / "nano_stream_graph_memory.py"
OUT_JSON = ROOT / "nano_ablation_results.json"
OUT_HTML = ROOT / "nano_ablation_report.html"


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
    section: str
    query: str
    expected: str
    judge_note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def build_shared_turns() -> list[tuple[str, str]]:
    return [
        ("Jon lost his job as a banker on 2023-01-19 and decided to start a dance studio.", "2023-01-20"),
        ("Jon visited Paris on 2023-01-28 and said it was cool.", "2023-01-28"),
        ("Gina visited Rome on 2023-01-30 after her design interview.", "2023-02-01"),
        ("Jon's ideal dance studio is by the water, with natural light and Marley flooring.", "2023-02-03"),
    ]


def setup_memories() -> tuple[Any, Any]:
    mod_v1 = load_module(V1_PATH, "echomemory_nano_v1_ablation")
    mod_v2 = load_module(V2_PATH, "echomemory_nano_v2_ablation")
    mem_v1 = mod_v1.EchoMemoryNano()
    mem_v2 = mod_v2.EchoMemoryNanoV2()
    for text, created_at in build_shared_turns():
        mem_v1.append_turn("user", text, created_at)
        mem_v2.append_turn("user", text, created_at)
    mem_v1.extract_atoms()
    mem_v1.build_graph()
    mem_v2.extract_atoms()
    mem_v2.build_memory_planes()
    return mem_v1, mem_v2


def flat_search_v2(mem_v2: Any, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    q_tokens = set(mem_v2._tokens(query))
    scored: list[tuple[float, Any]] = []
    for node in mem_v2.nodes:
        n_tokens = set(mem_v2._tokens(node.content))
        overlap = len(q_tokens & n_tokens)
        if overlap <= 0:
            continue
        scored.append((float(overlap), node))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "score": round(score, 3),
            "node_id": node.node_id,
            "node_type": node.node_type,
            "event_time": node.event_time,
            "content": node.content,
        }
        for score, node in scored[:top_k]
    ]


def top1_contains_date(result: Any, expected_date: str) -> bool:
    if not result:
        return False
    if isinstance(result, dict):
        hits = result.get("hits", [])
    else:
        hits = result
    if not hits:
        return False
    top = hits[0]
    payload = f"{top.get('content', '')}\n{top.get('event_time', '')}"
    return expected_date in payload


def top1_is_type(result: Any, expected_type: str) -> bool:
    if isinstance(result, dict):
        hits = result.get("hits", [])
    else:
        hits = result
    return bool(hits) and str(hits[0].get("node_type", "")) == expected_type


def top3_contains_all(result: Any, keywords: list[str]) -> bool:
    if isinstance(result, dict):
        hits = result.get("hits", [])
    else:
        hits = result
    joined = "\n".join(str(item.get("content", "")) for item in hits[:3]).lower()
    return all(keyword.lower() in joined for keyword in keywords)


def run_experiment() -> dict[str, Any]:
    mem_v1, mem_v2 = setup_memories()

    extraction_rows: list[dict[str, Any]] = []
    extraction_targets = [
        ("Jon job-loss event_time", mem_v1.atoms[0].event_time, mem_v2.atoms[0].event_time, "2023-01-19"),
        ("Gina Rome visit event_time", mem_v1.atoms[2].event_time, mem_v2.atoms[2].event_time, "2023-01-30"),
    ]
    for label, v1_value, v2_value, gold in extraction_targets:
        extraction_rows.append(
            {
                "label": label,
                "gold": gold,
                "v1": v1_value,
                "v2": v2_value,
                "v1_correct": v1_value == gold,
                "v2_correct": v2_value == gold,
            }
        )

    retrieval_cases = [
        EvalCase(
            case_id="temporal_q1",
            section="retrieval",
            query="When did Jon lose his job?",
            expected="top1 is event and contains 2023-01-19",
            judge_note="Time-sensitive query should prefer event evidence and surface the true event date, not the record date.",
        ),
        EvalCase(
            case_id="temporal_q2",
            section="retrieval",
            query="When did Gina visit Rome?",
            expected="top1 is event and contains 2023-01-30",
            judge_note="Another time-sensitive query where event nodes should outrank generic fact/entity hits.",
        ),
        EvalCase(
            case_id="profile_q1",
            section="retrieval",
            query="What does Jon think the ideal dance studio should look like?",
            expected="top1 type is fact; top3 contain water, natural light, Marley",
            judge_note="Profile/detail query should prioritize fact nodes over event-like wrappers.",
        ),
    ]

    retrieval_rows: list[dict[str, Any]] = []
    for case in retrieval_cases:
        flat = flat_search_v2(mem_v2, case.query)
        planned = mem_v2.search(case.query)
        if case.case_id == "temporal_q1":
            flat_ok = top1_is_type(flat, "event") and top1_contains_date(flat, "2023-01-19")
            planned_ok = top1_is_type(planned, "event") and top1_contains_date(planned, "2023-01-19")
        elif case.case_id == "temporal_q2":
            flat_ok = top1_is_type(flat, "event") and top1_contains_date(flat, "2023-01-30")
            planned_ok = top1_is_type(planned, "event") and top1_contains_date(planned, "2023-01-30")
        else:
            flat_ok = top1_is_type(flat, "fact") and top3_contains_all(flat, ["water", "natural light", "Marley"])
            planned_ok = top1_is_type(planned, "fact") and top3_contains_all(planned, ["water", "natural light", "Marley"])
        retrieval_rows.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "expected": case.expected,
                "judge_note": case.judge_note,
                "flat": flat,
                "planned": planned,
                "flat_ok": flat_ok,
                "planned_ok": planned_ok,
            }
        )

    summary = {
        "extraction_v1_correct": sum(1 for row in extraction_rows if row["v1_correct"]),
        "extraction_v2_correct": sum(1 for row in extraction_rows if row["v2_correct"]),
        "extraction_total": len(extraction_rows),
        "flat_retrieval_correct": sum(1 for row in retrieval_rows if row["flat_ok"]),
        "planned_retrieval_correct": sum(1 for row in retrieval_rows if row["planned_ok"]),
        "retrieval_total": len(retrieval_rows),
    }

    return {
        "scenario": {
            "turns": [{"text": text, "created_at": created_at} for text, created_at in build_shared_turns()]
        },
        "extraction_rows": extraction_rows,
        "retrieval_rows": retrieval_rows,
        "summary": summary,
    }


def render_report(data: dict[str, Any]) -> str:
    summary = data["summary"]
    extraction_rows = data["extraction_rows"]
    retrieval_rows = data["retrieval_rows"]

    def hit_list(items: list[dict[str, Any]]) -> str:
        if not items:
            return "<li>-</li>"
        return "".join(
            f"<li><code>{esc(item.get('node_id'))}</code> · {esc(item.get('node_type'))} · score={esc(item.get('score'))}<br>{esc(str(item.get('content', ''))[:180])}</li>"
            for item in items[:3]
        )

    extraction_html = "".join(
        "<tr>"
        f"<td>{esc(row['label'])}</td>"
        f"<td><code>{esc(row['gold'])}</code></td>"
        f"<td><code>{esc(row['v1'])}</code>{' ✅' if row['v1_correct'] else ' ❌'}</td>"
        f"<td><code>{esc(row['v2'])}</code>{' ✅' if row['v2_correct'] else ' ❌'}</td>"
        "</tr>"
        for row in extraction_rows
    )

    retrieval_html = "".join(
        "<div class='case'>"
        f"<h3>{esc(row['case_id'])}</h3>"
        f"<p><b>Query:</b> {esc(row['query'])}</p>"
        f"<p><b>Expected:</b> {esc(row['expected'])}</p>"
        f"<p class='muted'>{esc(row['judge_note'])}</p>"
        "<div class='grid two'>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['flat_ok'] else 'bad'}'>flat retrieval {'pass' if row['flat_ok'] else 'fail'}</div>"
        "<ul>" + hit_list(row["flat"]) + "</ul>"
        "</div>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['planned_ok'] else 'bad'}'>planned retrieval {'pass' if row['planned_ok'] else 'fail'}</div>"
        f"<p class='muted'>intent={esc(row['planned'].get('plan', {}).get('intent', '-'))} · layers={esc(','.join(row['planned'].get('plan', {}).get('target_layers', [])))}</p>"
        "<ul>" + hit_list(row["planned"].get("hits", [])) + "</ul>"
        "</div>"
        "</div>"
        "</div>"
        for row in retrieval_rows
    )

    turns_html = "".join(
        f"<li><code>{esc(item['created_at'])}</code> · {esc(item['text'])}</li>"
        for item in data["scenario"]["turns"]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Ablation</title>
  <style>
    :root {{
      --bg:#f6f7fb;--panel:#fff;--text:#172033;--muted:#667085;--line:#dde4ee;
      --blue:#2457c5;--blue-soft:#eef4ff;--green:#067647;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fff1f3;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 70px}}
    .hero,.panel,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero{{padding:28px 30px 24px}}
    .panel{{padding:20px 22px;margin-top:18px}}
    .grid{{display:grid;gap:16px}}
    .grid.two{{grid-template-columns:repeat(2,minmax(0,1fr))}}
    .card{{padding:14px 16px}}
    h1,h2,h3{{margin:0 0 10px}}
    h1{{font-size:28px}}
    p{{margin:8px 0}}
    ul{{margin:8px 0 0 18px;padding:0}}
    li{{margin:6px 0}}
    code{{background:#f8fafc;border:1px solid var(--line);border-radius:6px;padding:1px 5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}}
    th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
    th{{background:#f8fafc}}
    .muted{{color:var(--muted)}}
    .badge{{display:inline-block;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700}}
    .ok{{background:var(--green-soft);color:var(--green)}}
    .bad{{background:var(--red-soft);color:var(--red)}}
    .kpi{{display:grid;gap:12px;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:14px}}
    .kpi .item{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
    .kpi .num{{font-size:24px;font-weight:800}}
    .case{{padding-top:14px;border-top:1px solid var(--line);margin-top:14px}}
    @media (max-width:960px){{.grid.two,.kpi{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano Ablation</h1>
      <p class="muted">
        This is a toy experiment for understanding the architectural direction, not a paper-grade benchmark result.
        It answers two narrower questions:
        1) does nano v2 preserve event time better than nano v1;
        2) on the same v2 memory graph, is planned retrieval better than flat lexical retrieval for evidence selection.
      </p>
      <div class="kpi">
        <div class="item"><div class="num">{summary['extraction_v1_correct']}/{summary['extraction_total']}</div><div class="muted">v1 extraction</div></div>
        <div class="item"><div class="num">{summary['extraction_v2_correct']}/{summary['extraction_total']}</div><div class="muted">v2 extraction</div></div>
        <div class="item"><div class="num">{summary['flat_retrieval_correct']}/{summary['retrieval_total']}</div><div class="muted">flat retrieval</div></div>
        <div class="item"><div class="num">{summary['planned_retrieval_correct']}/{summary['retrieval_total']}</div><div class="muted">planned retrieval</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>Scenario</h2>
      <ul>{turns_html}</ul>
    </section>

    <section class="panel">
      <h2>Part A. Extraction sanity</h2>
      <p class="muted">
        Here the gold signal is the real event date in the sentence, not the logging date.
        This mirrors a real long-memory failure mode: confusing <code>event_time</code> with <code>created_at</code>.
      </p>
      <table>
        <thead><tr><th>Target</th><th>Gold</th><th>nano v1</th><th>nano v2</th></tr></thead>
        <tbody>{extraction_html}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Part B. Retrieval routing ablation</h2>
      <p class="muted">
        Both settings use the same v2 graph nodes. The only difference is whether retrieval first plans which layers to prioritize.
      </p>
      {retrieval_html}
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
