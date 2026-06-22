#!/usr/bin/env python3
from __future__ import annotations

import html
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MM_PATH = ROOT / "nano_multimodal_temporal_graph.py"
OUT_JSON = ROOT / "nano_multimodal_contract_ablation_results.json"
OUT_HTML = ROOT / "nano_multimodal_contract_ablation_report.html"
PUBLIC_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_multimodal_contract_ablation_20260615.html")


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
    note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def setup_memory() -> Any:
    mod = load_module(MM_PATH, "echomemory_multimodal_contract_ablation")
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


def infer_required_layers(query: str) -> list[str]:
    q = query.lower().strip()
    if re.search(r"\b(whose|who|谁)\b", q):
        return ["image_evidence", "entity"]
    if re.search(r"\b(what time|几点|什么时候|when)\b", q):
        return ["image_evidence", "event"]
    if re.search(r"\b(look like|style|kind|什么样|理想)\b", q):
        return ["fact", "image_evidence"]
    if re.search(r"\b(evidence|proof|依据|证据)\b", q):
        return ["image_evidence", "fact", "event"]
    if re.search(r"\b(visible|shown|显示|看到|metrics)\b", q):
        return ["image_evidence", "fact"]
    return ["image_evidence", "fact"]


def unique_preserve(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("node_id", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def compute_coverage(hits: list[dict[str, Any]], required_layers: list[str]) -> dict[str, Any]:
    present = []
    seen = set()
    for hit in hits:
        node_type = str(hit.get("node_type", ""))
        if node_type and node_type not in seen:
            seen.add(node_type)
            present.append(node_type)
    present_set = set(present)
    matched = [layer for layer in required_layers if layer in present_set]
    missing = [layer for layer in required_layers if layer not in present_set]
    return {
        "required_layers": required_layers,
        "present_layers": present,
        "matched_layers": matched,
        "missing_layers": missing,
        "coverage_ratio": len(matched) / max(len(required_layers), 1),
        "contract_ok": not missing,
    }


def build_graph_indexes(mem: Any) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], dict[str, list[str]]]:
    node_by_id = {node.node_id: {"node_id": node.node_id, "node_type": node.node_type, "content": node.content, "event_time": node.event_time, "source_ref": node.source_ref} for node in mem.nodes}
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    for edge in mem.edges:
        outgoing.setdefault(edge.source_id, []).append(edge.target_id)
        incoming.setdefault(edge.target_id, []).append(edge.source_id)
    return node_by_id, outgoing, incoming


def expand_missing_layers(
    mem: Any,
    *,
    query: str,
    hits: list[dict[str, Any]],
    missing_layers: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    node_by_id, outgoing, incoming = build_graph_indexes(mem)
    extra_ids: list[str] = []
    sources: list[str] = []

    hit_ids = [str(hit.get("node_id", "")) for hit in hits]

    if "entity" in missing_layers:
        for hit_id in hit_ids:
            if not hit_id.startswith("image:"):
                continue
            for target_id in outgoing.get(hit_id, []):
                node = node_by_id.get(target_id)
                if node and node["node_type"] == "entity":
                    extra_ids.append(target_id)
        if extra_ids:
            sources.append("image_to_entity")

    if "fact" in missing_layers:
        for hit_id in hit_ids:
            if hit_id.startswith("image:"):
                for target_id in outgoing.get(hit_id, []):
                    node = node_by_id.get(target_id)
                    if node and node["node_type"] == "fact":
                        extra_ids.append(target_id)
            elif hit_id.startswith("event:"):
                for target_id in outgoing.get(hit_id, []):
                    node = node_by_id.get(target_id)
                    if node and node["node_type"] == "fact":
                        extra_ids.append(target_id)
        if extra_ids:
            sources.append("linked_fact")

    if "event" in missing_layers:
        for hit_id in hit_ids:
            if hit_id.startswith("image:"):
                for target_id in outgoing.get(hit_id, []):
                    node = node_by_id.get(target_id)
                    if node and node["node_type"] == "event":
                        extra_ids.append(target_id)
            elif hit_id.startswith("fact:"):
                for source_id in incoming.get(hit_id, []):
                    node = node_by_id.get(source_id)
                    if node and node["node_type"] == "event":
                        extra_ids.append(source_id)
        if extra_ids:
            sources.append("linked_event")

    if "image_evidence" in missing_layers:
        for hit_id in hit_ids:
            for source_id in incoming.get(hit_id, []):
                node = node_by_id.get(source_id)
                if node and node["node_type"] == "image_evidence":
                    extra_ids.append(source_id)
        if extra_ids:
            sources.append("reverse_image_link")

    extras = []
    for node_id in unique_preserve([{"node_id": node_id} for node_id in extra_ids]):
        node = node_by_id.get(node_id["node_id"])
        if node:
            extras.append(
                {
                    "score": 0.75,
                    "node_id": node["node_id"],
                    "node_type": node["node_type"],
                    "event_time": node.get("event_time", ""),
                    "content": node["content"],
                    "second_pass": True,
                }
            )
    return extras, sources


def contains_keywords(hits: list[dict[str, Any]], keywords: list[str], top_k: int = 5) -> bool:
    blob = "\n".join(str(hit.get("content", "")) for hit in hits[:top_k]).lower()
    return all(keyword.lower() in blob for keyword in keywords)


def run_case(mem: Any, case: EvalCase) -> dict[str, Any]:
    one_pass = mem.search(case.query, text_only=False)
    required_layers = infer_required_layers(case.query)
    one_pass_hits = list(one_pass.get("hits", [])[:3])
    one_pass_cov = compute_coverage(one_pass_hits, required_layers)

    extras, second_pass_sources = expand_missing_layers(
        mem,
        query=case.query,
        hits=one_pass_hits,
        missing_layers=one_pass_cov["missing_layers"],
    )
    contract_hits = unique_preserve(one_pass_hits + extras)
    contract_cov = compute_coverage(contract_hits, required_layers)

    return {
        "case_id": case.case_id,
        "query": case.query,
        "note": case.note,
        "expected_keywords": case.expected_keywords,
        "required_layers": required_layers,
        "one_pass": {
            "plan": one_pass.get("plan", {}),
            "hits": one_pass_hits,
            "coverage": one_pass_cov,
            "keyword_ok": contains_keywords(one_pass_hits, case.expected_keywords),
        },
        "contract_aware": {
            "hits": contract_hits,
            "coverage": contract_cov,
            "keyword_ok": contains_keywords(contract_hits, case.expected_keywords),
            "second_pass_sources": second_pass_sources,
        },
    }


def run_experiment() -> dict[str, Any]:
    mem = setup_memory()
    cases = [
        EvalCase(
            case_id="arrival_timestamp",
            query="What time was visible in Gina's screenshot when she arrived?",
            expected_keywords=["08:42"],
            note="A visual-temporal query should keep image evidence while also anchoring to the linked event.",
        ),
        EvalCase(
            case_id="dashboard_owner",
            query="Whose screenshot showed Revenue 123 and Margin 18%?",
            expected_keywords=["Alice"],
            note="A visual-identification query should not stop at OCR-bearing image hits; it should also recover the owner entity.",
        ),
        EvalCase(
            case_id="studio_style_reference",
            query="What kind of studio look did Jon want?",
            expected_keywords=["waterfront", "natural light"],
            note="A style question is answerable from text, but a multimodal memory system should surface the moodboard as supporting evidence.",
        ),
        EvalCase(
            case_id="arrival_day_evidence",
            query="What evidence do we have from Gina's arrival day?",
            expected_keywords=["Roma Termini", "08:42"],
            note="Evidence-oriented questions should recover both the screenshot node and the linked event/fact context.",
        ),
        EvalCase(
            case_id="dashboard_metrics",
            query="What metrics were visible in Alice's dashboard screenshot?",
            expected_keywords=["Revenue 123", "Margin 18%"],
            note="An OCR-bearing dashboard query should still retain fact-like grounding instead of returning an isolated image hit only.",
        ),
    ]

    results = [run_case(mem, case) for case in cases]
    summary = {
        "cases": len(results),
        "one_pass_contract_ok": sum(1 for row in results if row["one_pass"]["coverage"]["contract_ok"]),
        "contract_aware_contract_ok": sum(1 for row in results if row["contract_aware"]["coverage"]["contract_ok"]),
        "one_pass_keyword_ok": sum(1 for row in results if row["one_pass"]["keyword_ok"]),
        "contract_aware_keyword_ok": sum(1 for row in results if row["contract_aware"]["keyword_ok"]),
        "improved_cases": [
            row["case_id"]
            for row in results
            if (not row["one_pass"]["coverage"]["contract_ok"]) and row["contract_aware"]["coverage"]["contract_ok"]
        ],
    }
    return {
        "scenario": mem.dump(),
        "summary": summary,
        "results": results,
    }


def render_report(data: dict[str, Any]) -> str:
    summary = data["summary"]
    rows = data["results"]

    def hit_list(items: list[dict[str, Any]]) -> str:
        if not items:
            return "<li>-</li>"
        return "".join(
            f"<li><code>{esc(item.get('node_id'))}</code> · {esc(item.get('node_type'))} · score={esc(item.get('score'))}"
            f"{' · second-pass' if item.get('second_pass') else ''}<br>{esc(str(item.get('content', ''))[:220])}</li>"
            for item in items[:5]
        )

    def coverage_box(cov: dict[str, Any]) -> str:
        return (
            f"<p class='muted'>required={esc(', '.join(cov['required_layers']))} · "
            f"present={esc(', '.join(cov['present_layers']))} · "
            f"coverage={esc(cov['coverage_ratio'])} · "
            f"contract_ok={esc(cov['contract_ok'])}</p>"
        )

    cases_html = "".join(
        "<div class='case'>"
        f"<h3>{esc(row['case_id'])}</h3>"
        f"<p><b>Query:</b> {esc(row['query'])}</p>"
        f"<p class='muted'>{esc(row['note'])}</p>"
        "<div class='grid two'>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['one_pass']['coverage']['contract_ok'] else 'bad'}'>one-pass {'contract ok' if row['one_pass']['coverage']['contract_ok'] else 'contract incomplete'}</div>"
        + coverage_box(row["one_pass"]["coverage"])
        + "<ul>" + hit_list(row["one_pass"]["hits"]) + "</ul>"
        "</div>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['contract_aware']['coverage']['contract_ok'] else 'bad'}'>contract-aware {'contract ok' if row['contract_aware']['coverage']['contract_ok'] else 'contract incomplete'}</div>"
        + coverage_box(row["contract_aware"]["coverage"])
        + f"<p class='muted'>second_pass_sources={esc(', '.join(row['contract_aware']['second_pass_sources']) or '-')}</p>"
        + "<ul>" + hit_list(row["contract_aware"]["hits"]) + "</ul>"
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
  <title>EchoMemory Multimodal Contract Ablation</title>
  <style>
    :root {{
      --bg:#f6f7fb;--panel:#fff;--text:#172033;--muted:#667085;--line:#dde4ee;
      --green:#067647;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fff1f3;--blue:#2457c5;--blue-soft:#eef4ff;
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
    .tag{{display:inline-block;border-radius:999px;padding:4px 10px;font-size:12px;font-weight:700;background:var(--blue-soft);color:var(--blue);margin-right:8px}}
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
      <div class="tag">multimodal</div>
      <div class="tag">contract ablation</div>
      <div class="tag">nano</div>
      <h1>EchoMemory Multimodal Contract Ablation</h1>
      <p class="muted">
        This experiment asks a narrower question than the older visual demos: for image-grounded memory questions, is keyword relevance enough, or does the system also need the
        right evidence family combination such as <code>image_evidence + entity</code>, <code>image_evidence + fact</code>, or <code>image_evidence + event</code>?
      </p>
      <div class="kpi">
        <div class="item"><div class="num">{summary['one_pass_contract_ok']}/{summary['cases']}</div><div class="muted">one-pass contract-complete</div></div>
        <div class="item"><div class="num">{summary['contract_aware_contract_ok']}/{summary['cases']}</div><div class="muted">contract-aware contract-complete</div></div>
        <div class="item"><div class="num">{summary['one_pass_keyword_ok']}/{summary['cases']}</div><div class="muted">one-pass keyword ok</div></div>
        <div class="item"><div class="num">{summary['contract_aware_keyword_ok']}/{summary['cases']}</div><div class="muted">contract-aware keyword ok</div></div>
        <div class="item"><div class="num">{len(summary['improved_cases'])}</div><div class="muted">improved cases</div></div>
      </div>
      <p class="muted">
        Improved cases: <code>{esc(', '.join(summary['improved_cases']) or '-')}</code>
      </p>
    </section>
    <section class="panel">
      <h2>Main takeaway</h2>
      <p>
        The important distinction here is the same one now appearing in the real EchoMemory stack: a multimodal system can already look relevant while still being structurally incomplete.
        A screenshot hit without owner/entity grounding, or a style answer without the moodboard evidence, is still a weaker memory behavior than a contract-complete answer path.
      </p>
    </section>
    <section class="panel">
      <h2>Per-case results</h2>
      {cases_html}
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    data = run_experiment()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_report(data)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    PUBLIC_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML), "public_html": str(PUBLIC_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
