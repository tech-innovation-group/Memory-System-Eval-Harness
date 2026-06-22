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
MM_PATH = ROOT / "nano_multimodal_temporal_graph.py"
OUT_JSON = ROOT / "nano_visual_memory_eval_results.json"
OUT_HTML = ROOT / "nano_visual_memory_eval_report.html"


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
    query: str
    expected_keywords: list[str]
    mode: str
    expected_visual_top1: bool = False
    note: str = ""


def esc(value: Any) -> str:
    return html.escape(str(value))


def setup_memory() -> Any:
    mod = load_module(MM_PATH, "echomemory_visual_memory_eval")
    mem = mod.EchoMemoryMultiModalNano()

    mem.append_text(
        "Gina arrived in Rome on 2023-01-30 for a design interview.",
        created_at="2023-02-01",
        event_time="2023-01-30",
    )
    mem.append_image(
        caption="Phone screenshot from Gina's arrival day showing Roma Termini station board.",
        ocr="Roma Termini 08:42 Platform 7",
        tags=["rome", "station", "arrival", "travel"],
        created_at="2023-02-01",
        event_time="2023-01-30",
        linked_subject="Gina",
    )

    mem.append_text(
        "Jon wanted the dance studio to look like a waterfront loft with natural light and Marley flooring.",
        created_at="2023-02-03",
        event_time="2023-02-03",
    )
    mem.append_image(
        caption="Moodboard screenshot for Jon's dream studio with sunlit windows and a waterfront interior.",
        ocr="waterfront loft natural light Marley floor",
        tags=["studio", "moodboard", "design", "waterfront"],
        created_at="2023-02-03",
        event_time="2023-02-03",
        linked_subject="Jon",
    )

    mem.append_text(
        "Alice reviewed a finance dashboard during the weekly planning check-in.",
        created_at="2023-02-04",
        event_time="2023-02-04",
    )
    mem.append_image(
        caption="Finance dashboard screenshot",
        ocr="Revenue 123; Margin 18%",
        tags=["finance", "dashboard", "metrics"],
        created_at="2023-02-04",
        event_time="2023-02-04",
        linked_subject="Alice",
    )

    mem.extract_atoms()
    mem.build_memory()
    return mem


def top1_type(result: dict[str, Any]) -> str:
    hits = result.get("hits", [])
    return str(hits[0].get("node_type", "")) if hits else "none"


def topk_text(result: dict[str, Any], top_k: int = 3) -> str:
    hits = result.get("hits", [])[:top_k]
    return "\n".join(str(hit.get("content", "")) for hit in hits)


def contains_any_keyword(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def judge_case(case: EvalCase, text_only: dict[str, Any], multimodal: dict[str, Any]) -> dict[str, Any]:
    text_blob = topk_text(text_only)
    mm_blob = topk_text(multimodal)
    text_has = contains_any_keyword(text_blob, case.expected_keywords)
    mm_has = contains_any_keyword(mm_blob, case.expected_keywords)
    mm_top1_visual = top1_type(multimodal) == "image_evidence"

    if case.mode == "ocr_only":
      text_ok = not text_has
      mm_ok = mm_has and mm_top1_visual
    elif case.mode == "visual_anchor":
      text_ok = True
      mm_ok = mm_has and mm_top1_visual
    elif case.mode == "hybrid_support":
      text_ok = text_has
      mm_ok = mm_has and any(hit.get("node_type") == "image_evidence" for hit in multimodal.get("hits", [])[:5])
    else:
      text_ok = text_has
      mm_ok = mm_has

    return {
        "case_id": case.case_id,
        "query": case.query,
        "mode": case.mode,
        "expected_keywords": case.expected_keywords,
        "note": case.note,
        "text_only": text_only,
        "multimodal": multimodal,
        "text_only_ok": text_ok,
        "multimodal_ok": mm_ok,
        "mm_top1_visual": mm_top1_visual,
    }


def run_eval() -> dict[str, Any]:
    mem = setup_memory()
    cases = [
        EvalCase(
            case_id="ocr_timestamp",
            query="What time was visible in Gina's screenshot when she arrived?",
            expected_keywords=["08:42"],
            mode="ocr_only",
            note="The timestamp exists in OCR but not in the textual event fact.",
        ),
        EvalCase(
            case_id="station_board_city",
            query="Which city appears in Gina's screenshot from her trip?",
            expected_keywords=["Roma Termini", "Rome"],
            mode="visual_anchor",
            note="The query should anchor on the screenshot node itself rather than only on event text.",
        ),
        EvalCase(
            case_id="design_reference",
            query="What kind of studio look did Jon want?",
            expected_keywords=["waterfront", "natural light", "Marley"],
            mode="hybrid_support",
            note="Text can answer this, but multimodal memory should also surface moodboard evidence.",
        ),
        EvalCase(
            case_id="dashboard_metrics",
            query="What metrics were visible in Alice's dashboard screenshot?",
            expected_keywords=["Revenue 123", "Margin 18%"],
            mode="ocr_only",
            note="This is a second OCR-only case to avoid overfitting to one example.",
        ),
        EvalCase(
            case_id="dashboard_owner",
            query="Whose screenshot showed Revenue 123 and Margin 18%?",
            expected_keywords=["Alice"],
            mode="visual_anchor",
            note="The visual node is tied to Alice via linked_subject and should be preferred as evidence.",
        ),
        EvalCase(
            case_id="trip_evidence_link",
            query="What evidence do we have from Gina's arrival day?",
            expected_keywords=["Roma Termini", "08:42", "station board"],
            mode="hybrid_support",
            note="This checks whether multimodal retrieval can bring in the screenshot as supporting evidence for an event-day question.",
        ),
    ]

    rows = []
    for case in cases:
        text_only = mem.search(case.query, text_only=True)
        multimodal = mem.search(case.query, text_only=False)
        rows.append(judge_case(case, text_only, multimodal))

    summary = {
        "text_only_correct": sum(1 for row in rows if row["text_only_ok"]),
        "multimodal_correct": sum(1 for row in rows if row["multimodal_ok"]),
        "total_cases": len(rows),
        "visual_top1_cases": sum(1 for row in rows if row["mm_top1_visual"]),
        "ocr_only_cases": sum(1 for row in rows if row["mode"] == "ocr_only"),
    }

    return {
        "scenario": mem.dump(),
        "rows": rows,
        "summary": summary,
    }


def render_report(data: dict[str, Any]) -> str:
    summary = data["summary"]
    rows = data["rows"]

    def hit_list(items: list[dict[str, Any]]) -> str:
        if not items:
            return "<li>-</li>"
        return "".join(
            f"<li><code>{esc(item.get('node_id'))}</code> · {esc(item.get('node_type'))} · score={esc(item.get('score'))}<br>{esc(str(item.get('content', ''))[:220])}</li>"
            for item in items[:3]
        )

    cases_html = "".join(
        "<div class='case'>"
        f"<h3>{esc(row['case_id'])}</h3>"
        f"<p><b>Query:</b> {esc(row['query'])}</p>"
        f"<p><b>Mode:</b> {esc(row['mode'])}</p>"
        f"<p class='muted'>{esc(row['note'])}</p>"
        "<div class='grid two'>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['text_only_ok'] else 'bad'}'>text-only {'pass' if row['text_only_ok'] else 'fail'}</div>"
        "<ul>" + hit_list(row["text_only"].get("hits", [])) + "</ul>"
        "</div>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['multimodal_ok'] else 'bad'}'>multimodal {'pass' if row['multimodal_ok'] else 'fail'}</div>"
        f"<p class='muted'>intent={esc(row['multimodal'].get('plan', {}).get('intent', '-'))} · layers={esc(','.join(row['multimodal'].get('plan', {}).get('target_layers', [])))} · top1_visual={esc(row['mm_top1_visual'])}</p>"
        "<ul>" + hit_list(row["multimodal"].get("hits", [])) + "</ul>"
        "</div>"
        "</div>"
        "</div>"
        for row in rows
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Visual Memory Eval Harness</title>
  <style>
    :root {{
      --bg:#f6f7fb;--panel:#fff;--text:#172033;--muted:#667085;--line:#dde4ee;
      --green:#067647;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fff1f3;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .wrap{{max-width:1200px;margin:0 auto;padding:28px 20px 70px}}
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
    .muted{{color:var(--muted)}}
    .badge{{display:inline-block;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700}}
    .ok{{background:var(--green-soft);color:var(--green)}}
    .bad{{background:var(--red-soft);color:var(--red)}}
    .kpi{{display:grid;gap:12px;grid-template-columns:repeat(5,minmax(0,1fr));margin-top:14px}}
    .kpi .item{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
    .kpi .num{{font-size:24px;font-weight:800}}
    .case{{padding-top:14px;border-top:1px solid var(--line);margin-top:14px}}
    @media (max-width:960px){{.grid.two,.kpi{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Visual Memory Eval Harness</h1>
      <p class="muted">
        This is a small but more structured evaluation harness for the multimodal nano line. It is still a toy evaluation, but it is more systematic than a single smoke or a single ablation table.
      </p>
      <div class="kpi">
        <div class="item"><div class="num">{summary['text_only_correct']}/{summary['total_cases']}</div><div class="muted">text-only</div></div>
        <div class="item"><div class="num">{summary['multimodal_correct']}/{summary['total_cases']}</div><div class="muted">multimodal</div></div>
        <div class="item"><div class="num">{summary['visual_top1_cases']}/{summary['total_cases']}</div><div class="muted">visual top1</div></div>
        <div class="item"><div class="num">{summary['ocr_only_cases']}</div><div class="muted">ocr-only cases</div></div>
        <div class="item"><div class="num">{summary['total_cases']}</div><div class="muted">total cases</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>Interpretation</h2>
      <ul>
        <li>OCR-only questions should fail under text-only retrieval and succeed under multimodal retrieval.</li>
        <li>Visual-anchor questions should make <code>image_evidence</code> the top retrieval path.</li>
        <li>Hybrid-support questions should keep textual answers while surfacing visual evidence as support.</li>
      </ul>
    </section>

    <section class="panel">
      <h2>Cases</h2>
      {cases_html}
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    result = run_eval()
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_report(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"wrote: {OUT_JSON}")
    print(f"wrote: {OUT_HTML}")


if __name__ == "__main__":
    main()

