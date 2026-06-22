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
OUT_JSON = ROOT / "nano_multimodal_ablation_results.json"
OUT_HTML = ROOT / "nano_multimodal_ablation_report.html"


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
    expected: str
    judge_note: str
    expected_top1_type: str | None = None
    expected_keyword: str | None = None


def esc(value: Any) -> str:
    return html.escape(str(value))


def setup_memory() -> Any:
    mod = load_module(MM_PATH, "echomemory_multimodal_nano_ablation")
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
    mem.extract_atoms()
    mem.build_memory()
    return mem


def top1_type(result: dict[str, Any]) -> str:
    hits = result.get("hits", [])
    return str(hits[0].get("node_type", "")) if hits else "none"


def topk_contains_keyword(result: dict[str, Any], keyword: str, top_k: int = 3) -> bool:
    hits = result.get("hits", [])[:top_k]
    joined = "\n".join(str(hit.get("content", "")) for hit in hits)
    return keyword.lower() in joined.lower()


def run_experiment() -> dict[str, Any]:
    mem = setup_memory()
    cases = [
        EvalCase(
            case_id="visual_city_q1",
            query="Which city appears in Gina's screenshot from her trip?",
            expected="top1 should be image_evidence in multimodal mode",
            judge_note="A visual-location query should prioritize the screenshot node itself, not only textual event descriptions.",
            expected_top1_type="image_evidence",
            expected_keyword="Roma Termini",
        ),
        EvalCase(
            case_id="visual_time_q2",
            query="What time was visible in Gina's screenshot when she arrived?",
            expected="multimodal retrieval should surface OCR with 08:42; text-only should miss it",
            judge_note="This is the core visual-memory case: the answer lives in OCR, not in the textual fact node.",
            expected_top1_type="image_evidence",
            expected_keyword="08:42",
        ),
        EvalCase(
            case_id="style_q3",
            query="What did Jon want the studio to look like?",
            expected="text-only can answer, but multimodal should also surface the moodboard screenshot",
            judge_note="This case checks that multimodal memory complements text for style/preference questions rather than replacing it.",
            expected_keyword="waterfront",
        ),
    ]

    rows: list[dict[str, Any]] = []
    for case in cases:
        text_only = mem.search(case.query, text_only=True)
        multimodal = mem.search(case.query, text_only=False)

        text_only_ok = True
        multimodal_ok = True
        delta_note = ""

        if case.case_id == "visual_city_q1":
            text_only_ok = top1_type(text_only) != "image_evidence"
            multimodal_ok = (
                top1_type(multimodal) == "image_evidence"
                and topk_contains_keyword(multimodal, case.expected_keyword or "")
            )
            delta_note = "multimodal path should route to screenshot evidence"
        elif case.case_id == "visual_time_q2":
            text_only_ok = not topk_contains_keyword(text_only, case.expected_keyword or "")
            multimodal_ok = (
                top1_type(multimodal) == "image_evidence"
                and topk_contains_keyword(multimodal, case.expected_keyword or "")
            )
            delta_note = "OCR-only timestamp should appear only in multimodal retrieval"
        else:
            text_only_ok = topk_contains_keyword(text_only, case.expected_keyword or "")
            multimodal_ok = (
                topk_contains_keyword(multimodal, case.expected_keyword or "")
                and any(hit.get("node_type") == "image_evidence" for hit in multimodal.get("hits", [])[:5])
            )
            delta_note = "multimodal path should preserve textual answer while also surfacing moodboard evidence"

        rows.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "expected": case.expected,
                "judge_note": case.judge_note,
                "delta_note": delta_note,
                "text_only": text_only,
                "multimodal": multimodal,
                "text_only_ok": text_only_ok,
                "multimodal_ok": multimodal_ok,
            }
        )

    summary = {
        "text_only_correct": sum(1 for row in rows if row["text_only_ok"]),
        "multimodal_correct": sum(1 for row in rows if row["multimodal_ok"]),
        "total_cases": len(rows),
        "visual_only_gain_cases": sum(
            1
            for row in rows
            if (not topk_contains_keyword(row["text_only"], "08:42") and topk_contains_keyword(row["multimodal"], "08:42"))
            or (top1_type(row["text_only"]) != "image_evidence" and top1_type(row["multimodal"]) == "image_evidence")
        ),
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
            f"<li><code>{esc(item.get('node_id'))}</code> · {esc(item.get('node_type'))} · score={esc(item.get('score'))}<br>{esc(str(item.get('content', ''))[:200])}</li>"
            for item in items[:3]
        )

    cases_html = "".join(
        "<div class='case'>"
        f"<h3>{esc(row['case_id'])}</h3>"
        f"<p><b>Query:</b> {esc(row['query'])}</p>"
        f"<p><b>Expected:</b> {esc(row['expected'])}</p>"
        f"<p class='muted'>{esc(row['judge_note'])}</p>"
        f"<p class='muted'><b>Delta:</b> {esc(row['delta_note'])}</p>"
        "<div class='grid two'>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['text_only_ok'] else 'bad'}'>text-only {'pass' if row['text_only_ok'] else 'fail'}</div>"
        "<ul>" + hit_list(row["text_only"].get("hits", [])) + "</ul>"
        "</div>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['multimodal_ok'] else 'bad'}'>multimodal {'pass' if row['multimodal_ok'] else 'fail'}</div>"
        f"<p class='muted'>intent={esc(row['multimodal'].get('plan', {}).get('intent', '-'))} · layers={esc(','.join(row['multimodal'].get('plan', {}).get('target_layers', [])))}</p>"
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
  <title>EchoMemory Multimodal Nano Ablation</title>
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
      <h1>EchoMemory Multimodal Nano Ablation</h1>
      <p class="muted">
        This is a toy multimodal ablation for the CVPR-oriented branch. It compares text-only retrieval against multimodal retrieval on the same memory graph and focuses on one narrow question:
        when does image evidence become necessary instead of merely helpful?
      </p>
      <div class="kpi">
        <div class="item"><div class="num">{summary['text_only_correct']}/{summary['total_cases']}</div><div class="muted">text-only</div></div>
        <div class="item"><div class="num">{summary['multimodal_correct']}/{summary['total_cases']}</div><div class="muted">multimodal</div></div>
        <div class="item"><div class="num">{summary['visual_only_gain_cases']}</div><div class="muted">visual gain cases</div></div>
        <div class="item"><div class="num">{summary['total_cases']}</div><div class="muted">total cases</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>Interpretation</h2>
      <p class="muted">
        The core expected pattern is not that multimodal always beats text-only on every query. Instead:
      </p>
      <ul>
        <li>for screenshot-specific questions, multimodal retrieval should surface <code>image_evidence</code> nodes</li>
        <li>for OCR-only answers, text-only retrieval should fail while multimodal retrieval succeeds</li>
        <li>for style or preference questions, multimodal memory should complement text rather than replace it</li>
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
    data = run_experiment()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_report(data), encoding="utf-8")
    print(OUT_JSON)
    print(OUT_HTML)


if __name__ == "__main__":
    main()
