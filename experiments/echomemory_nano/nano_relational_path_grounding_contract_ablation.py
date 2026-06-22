#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_relational_path_grounding_contract_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_relational_path_grounding_contract_ablation_20260615.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def coverage(required: list[str], hits: list[dict[str, Any]]) -> dict[str, Any]:
    present_layers = {
        str(hit.get("layer", "")).strip()
        for hit in hits
        if str(hit.get("layer", "")).strip()
    }
    has_path_grounding = any(
        any(str(edge).strip() for edge in (hit.get("path_edge_ids") or []))
        for hit in hits
    )
    matched: list[str] = []
    for item in required:
        if item == "path_grounding":
            if has_path_grounding:
                matched.append(item)
        elif item in present_layers:
            matched.append(item)
    missing = [item for item in required if item not in matched]
    return {
        "required": required,
        "matched": matched,
        "missing": missing,
        "contract_ok": not missing,
        "coverage_ratio": round(len(matched) / max(len(required), 1), 3),
        "has_path_grounding": has_path_grounding,
    }


def main() -> None:
    readers = {
        "graph_seed": [
            {
                "source": "graph://entity/Alice",
                "layer": "graph",
                "content": "Alice and Bob are both linked to Orchard Labs",
                "path_edge_ids": [],
                "why": "Graph seed hit without explicit path trace.",
            }
        ],
        "atom": [
            {
                "source": "atom://orchard_fact",
                "layer": "fact",
                "content": "Alice worked at Orchard Labs in 2026.",
                "path_edge_ids": [],
                "why": "Fact support, but still no explicit relation path.",
            }
        ],
        "graph_path": [
            {
                "source": "graph://relation/alice-orchard-bob",
                "layer": "graph",
                "content": "Alice knew Bob because both worked on the Orchard Labs launch team.",
                "path_edge_ids": ["entity:Alice->company:Orchard", "company:Orchard->entity:Bob"],
                "why": "Explicit graph path grounding the relation.",
            }
        ],
    }

    policies = {
        "graph_fact_only": {
            "required": ["graph", "fact"],
            "reader_order": ["graph_seed", "atom", "graph_path"],
        },
        "path_grounded_relational": {
            "required": ["graph", "fact", "path_grounding"],
            "reader_order": ["graph_seed", "atom", "graph_path"],
        },
    }

    payload: dict[str, Any] = {"policies": {}}
    for name, policy in policies.items():
        hits: list[dict[str, Any]] = []
        used_readers: list[str] = []
        snapshots: list[dict[str, Any]] = []
        for reader in policy["reader_order"]:
            hits.extend(readers[reader])
            used_readers.append(reader)
            state = coverage(policy["required"], hits)
            snapshots.append(
                {
                    "after_reader": reader,
                    "coverage": state,
                    "present_layers": sorted({hit["layer"] for hit in hits}),
                }
            )
            if state["contract_ok"]:
                break
        payload["policies"][name] = {
            "required": policy["required"],
            "used_readers": used_readers,
            "final_coverage": coverage(policy["required"], hits),
            "hits": hits,
            "snapshots": snapshots,
        }

    OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")


def render_html(payload: dict[str, Any]) -> str:
    rows = []
    details = []
    for name, result in payload["policies"].items():
        rows.append(
            f"""
            <tr>
              <td><code>{esc(name)}</code></td>
              <td><code>{esc(result['required'])}</code></td>
              <td><code>{esc(result['used_readers'])}</code></td>
              <td>{esc(result['final_coverage']['coverage_ratio'])}</td>
              <td>{esc(result['final_coverage']['contract_ok'])}</td>
              <td>{esc(result['final_coverage']['has_path_grounding'])}</td>
            </tr>
            """
        )
        snaps = []
        for snap in result["snapshots"]:
            snaps.append(
                f"<li>after <code>{esc(snap['after_reader'])}</code>: missing=<code>{esc(snap['coverage']['missing'])}</code>, path_grounding={esc(snap['coverage']['has_path_grounding'])}</li>"
            )
        details.append(
            f"""
            <section class="panel">
              <h2>{esc(name)}</h2>
              <p>required: <code>{esc(result['required'])}</code></p>
              <p>used readers: <code>{esc(result['used_readers'])}</code></p>
              <ul>{''.join(snaps)}</ul>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Relational Path-Grounding Contract Ablation</title>
  <style>
    :root{{--bg:#f5f7fb;--panel:#fff;--line:#dbe3ee;--text:#172233;--muted:#617186;--blue:#245cff;--code:#f3f6fb}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1080px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}}
    .hero{{padding:26px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    h1,h2{{margin:0 0 10px;line-height:1.28}} h1{{font-size:30px}} h2{{font-size:20px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}}
    th,td{{border:1px solid var(--line);padding:10px;vertical-align:top;text-align:left}}
    th{{background:#f4f7fd}}
    code{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:var(--code);border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px;word-break:break-all}}
    .callout{{border-left:4px solid var(--blue);background:#f4f8ff;padding:12px 14px;border-radius:6px;margin-top:10px}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Nano Relational Path-Grounding Contract Ablation</h1>
      <p class="muted">这个小实验只回答一个问题：关系题是不是拿到 <code>graph + fact</code> 就该停，还是应该继续要求显式的图路径证据。</p>
      <div class="callout">
        设计重点是泛化原则，不是刷数据集：<b>如果系统只有图命中和事实命中，却没有明确的路径级关系依据，它是否应该继续扩证据。</b>
      </div>
    </section>

    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead><tr><th>Policy</th><th>Required</th><th>Readers Used</th><th>Coverage</th><th>Contract OK</th><th>Has Path Grounding</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>

    {''.join(details)}
  </div>
</body>
</html>"""


if __name__ == "__main__":
    main()
