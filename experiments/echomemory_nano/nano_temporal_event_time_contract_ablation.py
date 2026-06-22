#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_temporal_event_time_contract_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_temporal_event_time_contract_ablation_20260615.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def coverage(required: list[str], hits: list[dict[str, Any]]) -> dict[str, Any]:
    present_layers = {str(hit.get("layer", "")).strip() for hit in hits if str(hit.get("layer", "")).strip()}
    has_event_time = any(str(hit.get("event_time", "")).strip() for hit in hits)
    matched: list[str] = []
    for item in required:
        if item == "event_time":
            if has_event_time:
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
        "has_event_time": has_event_time,
    }


def main() -> None:
    readers = {
        "tree": [
            {
                "source": "tree://2026-03-03",
                "layer": "temporal_tree",
                "content": "- 2026-03-03: Aria signed the Riverside lease.",
                "event_time": "",
                "why": "Chronology skeleton only.",
            }
        ],
        "graph": [
            {
                "source": "graph://event-sign-lease",
                "layer": "event",
                "content": "Aria signed Riverside lease",
                "event_time": "",
                "why": "Entity/event relation, but no explicit event-time field.",
            }
        ],
        "atom": [
            {
                "source": "atom://sign-lease",
                "layer": "event",
                "content": "Aria signed the Riverside lease [时间=2026-03-03]",
                "event_time": "2026-03-03",
                "why": "Explicit story-time grounding.",
            }
        ],
    }

    policies = {
        "layer_only_temporal": {
            "required": ["temporal_tree", "event"],
            "reader_order": ["tree", "graph", "atom"],
        },
        "event_time_temporal": {
            "required": ["temporal_tree", "event", "event_time"],
            "reader_order": ["tree", "graph", "atom"],
        },
    }

    results: dict[str, Any] = {"policies": {}}
    for policy_name, policy in policies.items():
        hits: list[dict[str, Any]] = []
        used_readers: list[str] = []
        snapshots: list[dict[str, Any]] = []
        for reader in policy["reader_order"]:
            hits.extend(readers[reader])
            used_readers.append(reader)
            status = coverage(policy["required"], hits)
            snapshots.append(
                {
                    "after_reader": reader,
                    "coverage": status,
                    "hit_layers": [hit["layer"] for hit in hits],
                }
            )
            if status["contract_ok"]:
                break
        results["policies"][policy_name] = {
            "required": policy["required"],
            "used_readers": used_readers,
            "final_coverage": coverage(policy["required"], hits),
            "hits": hits,
            "snapshots": snapshots,
        }

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(results), encoding="utf-8")


def render_html(results: dict[str, Any]) -> str:
    rows = []
    for name, result in results["policies"].items():
        rows.append(
            f"""
            <tr>
              <td><code>{esc(name)}</code></td>
              <td><code>{esc(result['required'])}</code></td>
              <td><code>{esc(result['used_readers'])}</code></td>
              <td>{esc(result['final_coverage']['coverage_ratio'])}</td>
              <td>{esc(result['final_coverage']['contract_ok'])}</td>
            </tr>
            """
        )

    detail_blocks = []
    for name, result in results["policies"].items():
        snaps = []
        for snap in result["snapshots"]:
            snaps.append(
                f"<li>after <code>{esc(snap['after_reader'])}</code>: coverage={esc(snap['coverage']['coverage_ratio'])}, missing=<code>{esc(snap['coverage']['missing'])}</code></li>"
            )
        detail_blocks.append(
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
  <title>EchoMemory Nano Temporal Event-Time Contract Ablation</title>
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
      <h1>Nano Temporal Contract Ablation</h1>
      <p class="muted">这个小实验只回答一个问题：时间题是不是只要拿到 <code>temporal_tree + event</code> 就够，还是应该继续要求显式的 <code>event_time</code> 证据。</p>
      <div class="callout">
        设计重点是泛化原则，不是刷数据集：<b>如果 graph/event hit 只有关系和摘要、却没有明确 story-time，那么系统是否应该继续补 atom 级时间证据。</b>
      </div>
    </section>

    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead><tr><th>Policy</th><th>Required</th><th>Readers Used</th><th>Coverage</th><th>Contract OK</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>

    {''.join(detail_blocks)}
  </div>
</body>
</html>"""


if __name__ == "__main__":
    main()
