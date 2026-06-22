#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


OUT_DIR = Path("/Users/chx/locomo-eval-web/experiments/echomemory_search_planner_gap_probe_20260613")
OUT_JSON = OUT_DIR / "planner_gap_probe.json"
OUT_HTML = OUT_DIR / "planner_gap_probe.html"

SEARCH_SERVICE = Path("/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py")
TEST_INTENT = Path("/Users/chx/Code/echomemory/echo_memory_v006/tests/unit/service/test_search_intent.py")
TEST_GRAPH = Path("/Users/chx/Code/echomemory/echo_memory_v006/tests/unit/service/test_search_graph_integration.py")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_line_numbers(text: str, patterns: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        for pattern in patterns:
            if pattern in out:
                continue
            if pattern in line:
                out[pattern] = idx
    return out


def classify_current_behavior(query: str) -> dict[str, Any]:
    q = query.lower()
    visual = bool(re.search(r"截图|图片|照片|画面|ocr|screen|screenshot|image|photo|图里|图中|写着什么", query, re.I))
    force_l2 = bool(re.search(r"时间|顺序|排序|多久|多长|前后|之前|之后|当时|后来|时候|完成|是否|对比|区别|哪家|哪家公司|谁|去向|去了|加入了|哪年|哪月|哪天|什么时候|何时|排列|先后|期间|间隔", query, re.I))
    relation_word = bool(re.search(r"\bwho\b|\bwhich\b|\bboth\b|谁|哪个|哪些|共同|关系|联系|mentor|guide", query, re.I))
    graph_memory_types = visual
    graph_trigger = visual
    reason = []
    if visual:
        reason.append("visual_lookup/visual keywords directly allow graph/image_evidence path")
    if force_l2:
        reason.append("FORCE_L2 keywords force deeper retrieval, but not graph-first by themselves")
    if relation_word and not visual:
        reason.append("relation-style wording is visible, but current trigger still depends on intent memory_types or sparse L2")
    if not reason:
        reason.append("default path is still layered retrieval with graph as conditional add-on")
    return {
        "visual_query": visual,
        "force_l2": force_l2,
        "explicit_graph_memory_type": graph_memory_types,
        "likely_graph_prefetch": graph_trigger,
        "summary": "; ".join(reason),
    }


def classify_target_behavior(query: str) -> dict[str, Any]:
    temporal = bool(re.search(r"\bwhen\b|什么时候|何时|日期|时间|多久|多长|之前|之后|后来|哪年|哪月|哪天", query, re.I))
    visual = bool(re.search(r"截图|图片|照片|画面|ocr|screen|screenshot|image|photo|图里|图中|写着什么", query, re.I))
    relation = bool(re.search(r"\bwho\b|\bwhich\b|\bboth\b|谁|哪个|哪些|共同|关系|联系|mentor|guide", query, re.I))
    if visual:
        return {
            "target_mode": "graph-first visual",
            "why": "image_evidence should be the primary seed plane",
        }
    if temporal and relation:
        return {
            "target_mode": "graph-first temporal-relational",
            "why": "event nodes plus relation edges should be the default evidence backbone",
        }
    if temporal:
        return {
            "target_mode": "graph-first temporal",
            "why": "event nodes should outrank generic blocks/facts for story-time questions",
        }
    if relation:
        return {
            "target_mode": "graph-first relational",
            "why": "entity/event/fact chains are more faithful than flat lexical matches",
        }
    return {
        "target_mode": "hybrid",
        "why": "general/profile questions can remain mixed block/fact retrieval",
    }


def build_cases() -> list[dict[str, Any]]:
    cases = [
        {
            "case_id": "visual_ocr",
            "query": "截图里写着什么？",
            "expected": "image_evidence direct seed and visual diffusion",
        },
        {
            "case_id": "temporal_date",
            "query": "When did Gina lose her job?",
            "expected": "event-first retrieval",
        },
        {
            "case_id": "temporal_relation",
            "query": "Who married Alice and when?",
            "expected": "event + relation chain",
        },
        {
            "case_id": "list_relation",
            "query": "Which two people were involved in the Seattle wedding?",
            "expected": "entity/event relation path",
        },
        {
            "case_id": "plan_general",
            "query": "What does Gina plan to do after spring hiring season?",
            "expected": "block/fact hybrid is acceptable",
        },
    ]
    rows: list[dict[str, Any]] = []
    for case in cases:
        current = classify_current_behavior(case["query"])
        target = classify_target_behavior(case["query"])
        gap = "aligned" if (
            target["target_mode"].startswith("graph-first") and current["likely_graph_prefetch"]
            or target["target_mode"] == "hybrid"
        ) else "gap"
        if target["target_mode"].startswith("graph-first") and not current["likely_graph_prefetch"]:
            gap = "gap"
        elif target["target_mode"] == "hybrid":
            gap = "aligned"
        rows.append(
            {
                **case,
                "current": current,
                "target": target,
                "gap": gap,
            }
        )
    return rows


def render_html(data: dict[str, Any]) -> str:
    rows = "".join(
        f"""
        <tr>
          <td><code>{row['case_id']}</code><br>{row['query']}</td>
          <td>{row['expected']}</td>
          <td>
            visual={row['current']['visual_query']}<br>
            force_l2={row['current']['force_l2']}<br>
            graph_prefetch={row['current']['likely_graph_prefetch']}<br>
            <div class="small">{row['current']['summary']}</div>
          </td>
          <td>
            <b>{row['target']['target_mode']}</b><br>
            <div class="small">{row['target']['why']}</div>
          </td>
          <td><span class="badge {'ok' if row['gap']=='aligned' else 'bad'}">{row['gap']}</span></td>
        </tr>
        """
        for row in data["cases"]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Search Planner Gap Probe</title>
  <style>
    :root {{
      --bg:#f6f8fb; --panel:#fff; --text:#172033; --muted:#667085; --line:#dde4ee;
      --green:#067647; --green-soft:#ecfdf3; --red:#b42318; --red-soft:#fff1f3; --blue:#2457c5; --blue-soft:#eef4ff;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1200px;margin:0 auto;padding:28px 20px 48px}}
    .hero,.section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:0 10px 28px rgba(15,23,42,.08)}}
    .hero{{padding:26px 28px;margin-bottom:16px}}
    .section{{padding:20px 22px;margin-bottom:16px}}
    h1,h2{{margin:0 0 10px}}
    p{{margin:0 0 10px}}
    .stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:16px}}
    .stat{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:#fbfcff}}
    .stat .label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}}
    .stat .value{{font-size:22px;font-weight:700}}
    table{{width:100%;border-collapse:collapse;table-layout:fixed}}
    th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;word-break:break-word}}
    th{{background:#f8fafc;color:#344054;font-size:12px}}
    .badge{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700}}
    .badge.ok{{background:var(--green-soft);color:var(--green)}}
    .badge.bad{{background:var(--red-soft);color:var(--red)}}
    .small{{font-size:12px;color:var(--muted)}}
    code{{background:#f3f6fb;border-radius:6px;padding:2px 6px;font-size:12px}}
    .path{{display:inline-block;margin-top:6px;padding:4px 8px;border-radius:999px;background:var(--blue-soft);color:var(--blue);font-size:12px}}
    ul{{margin:8px 0 0;padding-left:18px}}
    @media (max-width: 960px) {{
      .stats{{grid-template-columns:1fr}}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Search Planner Gap Probe</h1>
      <p>
        这份 probe 不是跑完整 QA，而是专门分析主仓 <code>SearchService</code> 当前规则和理想的 graph-first planner 之间还有多大差距。
        核心问题是：<b>视觉题已经较成熟，但 temporal / relational query 是否也已经被当成 graph-first 问题处理？</b>
      </p>
      <div class="stats">
        <div class="stat"><span class="label">检查用例</span><span class="value">{data['summary']['total_cases']}</span></div>
        <div class="stat"><span class="label">当前已对齐</span><span class="value">{data['summary']['aligned_cases']}</span></div>
        <div class="stat"><span class="label">仍有 gap</span><span class="value">{data['summary']['gap_cases']}</span></div>
        <div class="stat"><span class="label">最明显成熟项</span><span class="value">visual_lookup</span></div>
      </div>
    </div>

    <div class="section">
      <h2>从代码读出来的事实</h2>
      <ul>
        <li>视觉题已有较明确主路径：<code>visual_lookup</code>、<code>image_evidence</code> seed、视觉 relation filter、对应单测都比较完整。</li>
        <li>时间题目前更像“强制进 L2”，还不等于“默认 graph-first”。</li>
        <li>关系题是否进图，目前仍较依赖 <code>intent.memory_types</code> 或 <code>L2 sparse</code> 条件，不像视觉题这么直接。</li>
      </ul>
      <div class="path">{data['evidence']['search_service_path']}</div>
      <div class="path">{data['evidence']['test_intent_path']}</div>
      <div class="path">{data['evidence']['test_graph_path']}</div>
    </div>

    <div class="section">
      <h2>代表性 query 诊断</h2>
      <table>
        <thead>
          <tr>
            <th style="width:22%">用例</th>
            <th style="width:18%">理想证据</th>
            <th style="width:25%">当前规则推断</th>
            <th style="width:23%">目标 planner</th>
            <th style="width:12%">结论</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>

    <div class="section">
      <h2>最重要结论</h2>
      <ul>
        <li><b>视觉 query 这条线已经比较像成熟 planner 行为。</b></li>
        <li><b>temporal / relational query 仍然是下一步最值得改的地方。</b> 现在它们更像“更深检索”，还不是“显式 graph-first routing”。</li>
        <li>所以论文里最诚实的说法应该是：EchoMemory 已经有 real-code planner skeleton，但 planner maturity 在不同 query family 上是不均衡的。</li>
      </ul>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    search_text = read(SEARCH_SERVICE)
    intent_text = read(TEST_INTENT)
    graph_text = read(TEST_GRAPH)

    line_map = extract_line_numbers(
        search_text,
        [
            "_FORCE_L2_KEYWORDS",
            "visual_query = intent.strategy == \"visual_lookup\"",
            "if self._graph_retriever is not None and (len(l2_items) < 5 or force_graph or visual_query):",
            "elif intent and intent.strategy == \"visual_lookup\":",
        ],
    )
    test_line_map = extract_line_numbers(
        graph_text,
        [
            "async def test_visual_query_prefers_image_evidence_seed(",
            "async def test_visual_diffusion_returns_image_content(",
        ],
    )
    intent_line_map = extract_line_numbers(
        intent_text,
        [
            "async def test_template_fast_path_visual_lookup(",
        ],
    )

    cases = build_cases()
    summary = {
        "total_cases": len(cases),
        "aligned_cases": sum(1 for row in cases if row["gap"] == "aligned"),
        "gap_cases": sum(1 for row in cases if row["gap"] == "gap"),
    }
    report = {
        "summary": summary,
        "cases": cases,
        "evidence": {
            "search_service_path": f"{SEARCH_SERVICE}:{line_map.get('_FORCE_L2_KEYWORDS', '?')}",
            "search_visual_trigger_line": line_map.get("visual_query = intent.strategy == \"visual_lookup\"", None),
            "search_graph_gate_line": line_map.get("if self._graph_retriever is not None and (len(l2_items) < 5 or force_graph or visual_query):", None),
            "search_visual_relation_filter_line": line_map.get("elif intent and intent.strategy == \"visual_lookup\":", None),
            "test_intent_path": f"{TEST_INTENT}:{intent_line_map.get('async def test_template_fast_path_visual_lookup(', '?')}",
            "test_graph_path": f"{TEST_GRAPH}:{test_line_map.get('async def test_visual_query_prefers_image_evidence_seed(', '?')}",
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
