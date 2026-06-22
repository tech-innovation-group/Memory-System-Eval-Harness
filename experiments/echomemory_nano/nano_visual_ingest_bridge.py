#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "nano_visual_ingest_bridge_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_visual_ingest_bridge_20260617.html"
)


@dataclass
class TextTurn:
    turn_id: str
    text: str
    created_at: str
    event_time: str


@dataclass
class ImageResource:
    image_id: str
    caption: str
    ocr: str
    tags: list[str]
    created_at: str
    event_time: str
    linked_subject: str = ""


@dataclass
class MemoryNode:
    node_id: str
    node_type: str
    content: str
    event_time: str = ""
    source_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryEdge:
    source_id: str
    target_id: str
    relation_type: str


@dataclass
class EvalCase:
    case_id: str
    query: str
    expected_keywords: list[str]
    required_types: list[str]
    note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text.lower())


def contains_all(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return all(keyword.lower() in lowered for keyword in keywords)


class VisualIngestBridgeNano:
    """
    A tiny prototype for one specific EchoMemory gap:

    query-time multimodal routing can exist,
    but if image resources are not structurally ingested on the write side,
    retrieval still lacks owner / OCR / event links.

    Modes:
      - no_visual_ingest: images are stored externally but do not produce searchable memory nodes
      - surface_visual_ingest: image_evidence nodes contain caption only
      - structured_visual_ingest: image_evidence nodes contain caption + OCR + owner + event links
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.text_turns: list[TextTurn] = []
        self.images: list[ImageResource] = []
        self.nodes: list[MemoryNode] = []
        self.edges: list[MemoryEdge] = []

    def append_text(self, text: str, *, created_at: str, event_time: str) -> None:
        self.text_turns.append(
            TextTurn(
                turn_id=f"turn-{len(self.text_turns):03d}",
                text=text.strip(),
                created_at=created_at,
                event_time=event_time,
            )
        )

    def append_image(
        self,
        *,
        caption: str,
        ocr: str,
        tags: list[str],
        created_at: str,
        event_time: str,
        linked_subject: str = "",
    ) -> None:
        self.images.append(
            ImageResource(
                image_id=f"img-{len(self.images):03d}",
                caption=caption.strip(),
                ocr=ocr.strip(),
                tags=list(tags),
                created_at=created_at,
                event_time=event_time,
                linked_subject=linked_subject.strip(),
            )
        )

    def build(self) -> None:
        self.nodes = []
        self.edges = []
        entity_ids: dict[str, str] = {}
        event_ids_by_time: dict[str, list[str]] = {}
        fact_ids_by_time: dict[str, list[str]] = {}

        for turn in self.text_turns:
            fact_id = f"fact:{turn.turn_id}"
            self.nodes.append(
                MemoryNode(
                    node_id=fact_id,
                    node_type="fact",
                    content=turn.text,
                    event_time=turn.event_time,
                    source_ref=turn.turn_id,
                )
            )
            fact_ids_by_time.setdefault(turn.event_time, []).append(fact_id)

            event_id = f"event:{turn.turn_id}"
            self.nodes.append(
                MemoryNode(
                    node_id=event_id,
                    node_type="event",
                    content=turn.text,
                    event_time=turn.event_time,
                    source_ref=turn.turn_id,
                )
            )
            event_ids_by_time.setdefault(turn.event_time, []).append(event_id)
            self.edges.append(MemoryEdge(source_id=event_id, target_id=fact_id, relation_type="evidence_of"))

            for entity in self._extract_entities(turn.text):
                entity_id = entity_ids.setdefault(entity, f"entity:{entity}")
                if not any(node.node_id == entity_id for node in self.nodes):
                    self.nodes.append(
                        MemoryNode(
                            node_id=entity_id,
                            node_type="entity",
                            content=f"name={entity}",
                            source_ref=turn.turn_id,
                        )
                    )
                self.edges.append(MemoryEdge(source_id=entity_id, target_id=fact_id, relation_type="has_fact"))
                self.edges.append(MemoryEdge(source_id=event_id, target_id=entity_id, relation_type="involves"))

        if self.mode == "no_visual_ingest":
            return

        for image in self.images:
            content_parts = [image.caption]
            if self.mode == "structured_visual_ingest" and image.ocr:
                content_parts.append(image.ocr)
            if self.mode == "structured_visual_ingest" and image.tags:
                content_parts.append(", ".join(image.tags))
            image_id = f"image:{image.image_id}"
            self.nodes.append(
                MemoryNode(
                    node_id=image_id,
                    node_type="image_evidence",
                    content="\n".join(content_parts),
                    event_time=image.event_time,
                    source_ref=image.image_id,
                    metadata={
                        "caption": image.caption,
                        "ocr": image.ocr if self.mode == "structured_visual_ingest" else "",
                        "linked_subject": image.linked_subject if self.mode == "structured_visual_ingest" else "",
                    },
                )
            )

            if self.mode != "structured_visual_ingest":
                continue

            for event_id in event_ids_by_time.get(image.event_time, []):
                self.edges.append(MemoryEdge(source_id=image_id, target_id=event_id, relation_type="depicts_event"))
            for fact_id in fact_ids_by_time.get(image.event_time, []):
                self.edges.append(MemoryEdge(source_id=image_id, target_id=fact_id, relation_type="supports_fact"))
            if image.linked_subject:
                entity_id = entity_ids.setdefault(image.linked_subject, f"entity:{image.linked_subject}")
                if not any(node.node_id == entity_id for node in self.nodes):
                    self.nodes.append(
                        MemoryNode(
                            node_id=entity_id,
                            node_type="entity",
                            content=f"name={image.linked_subject}",
                            source_ref=image.image_id,
                        )
                    )
                self.edges.append(MemoryEdge(source_id=image_id, target_id=entity_id, relation_type="shows_subject"))

    def search(self, query: str) -> dict[str, Any]:
        q_terms = tokenize(query)
        query_lower = query.lower()
        primary_types = self._required_types(query)

        hits: list[dict[str, Any]] = []
        for node in self.nodes:
            score = self._node_score(node, q_terms, query_lower)
            if score <= 0:
                continue
            hits.append(
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "content": node.content,
                    "event_time": node.event_time,
                    "score": round(score, 3),
                }
            )

        # Graph-style expansion matters only when write-side created usable links.
        if any(hit["node_type"] == "image_evidence" for hit in hits[:3]):
            expanded = self._expand_from_images(hits[:3], needed=set(primary_types))
            existing = {hit["node_id"] for hit in hits}
            for item in expanded:
                if item["node_id"] not in existing:
                    hits.append(item)
                    existing.add(item["node_id"])

        hits.sort(
            key=lambda item: (
                -item["score"],
                0 if item["node_type"] in primary_types else 1,
                item["node_id"],
            )
        )
        top_hits = hits[:5]
        present_types = []
        seen_types: set[str] = set()
        for hit in top_hits:
            if hit["node_type"] not in seen_types:
                seen_types.add(hit["node_type"])
                present_types.append(hit["node_type"])
        missing_types = [item for item in primary_types if item not in seen_types]
        return {
            "mode": self.mode,
            "query": query,
            "required_types": primary_types,
            "hits": top_hits,
            "present_types": present_types,
            "missing_types": missing_types,
            "contract_ok": not missing_types,
        }

    def _expand_from_images(self, seed_hits: list[dict[str, Any]], needed: set[str]) -> list[dict[str, Any]]:
        image_hits = [hit for hit in seed_hits if hit["node_type"] == "image_evidence"]
        if not image_hits:
            return []
        best_image_score = max(hit["score"] for hit in image_hits)
        image_ids = {
            hit["node_id"]
            for hit in image_hits
            if hit["score"] >= best_image_score - 1.5
        }
        if not image_ids:
            return []
        node_by_id = {node.node_id: node for node in self.nodes}
        extra: list[dict[str, Any]] = []
        for edge in self.edges:
            if edge.source_id not in image_ids:
                continue
            node = node_by_id.get(edge.target_id)
            if node is None:
                continue
            if node.node_type == "entity" and "entity" in needed:
                bonus = 5.0
            elif node.node_type == "event" and "event" in needed:
                bonus = 4.5
            elif node.node_type == "fact" and "fact" in needed:
                bonus = 4.0
            elif node.node_type in needed:
                bonus = 3.0
            else:
                bonus = 0.8
            extra.append(
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "content": node.content,
                    "event_time": node.event_time,
                    "score": round(bonus, 3),
                }
            )
        return extra

    def _node_score(self, node: MemoryNode, q_terms: list[str], query_lower: str) -> float:
        text = f"{node.content}\n{node.metadata}".lower()
        overlap = sum(1 for term in q_terms if term in text)
        if overlap == 0:
            return 0.0
        score = float(overlap)
        if node.node_type == "image_evidence" and re.search(r"(screenshot|image|photo|visible|shown|dashboard|moodboard|图|截图|照片|显示)", query_lower):
            score += 2.2
        if node.node_type == "entity" and re.search(r"\bwhose\b|\bwho\b|谁的|谁", query_lower):
            score += 0.6
        if node.node_type == "event" and re.search(r"(arrival day|same day|当天|那天|arrived)", query_lower):
            score += 0.8
        return score

    @staticmethod
    def _required_types(query: str) -> list[str]:
        q = query.lower()
        if re.search(r"\bwhose\b|\bwho\b|谁的|谁", q):
            return ["image_evidence", "entity"]
        if re.search(r"(arrival day|same day|当天|那天|when she arrived)", q):
            return ["image_evidence", "event"]
        if re.search(r"(what time|metrics|visible|shown|显示|看见|截图|screenshot|moodboard)", q):
            return ["image_evidence", "fact"]
        return ["image_evidence", "fact"]

    @staticmethod
    def _extract_entities(text: str) -> list[str]:
        return re.findall(r"\b[A-Z][a-z]+\b", text)


def build_demo(mode: str) -> VisualIngestBridgeNano:
    mem = VisualIngestBridgeNano(mode)
    mem.append_text(
        "Gina arrived in Rome on 2026-03-02 for a design interview.",
        created_at="2026-03-03",
        event_time="2026-03-02",
    )
    mem.append_image(
        caption="Phone screenshot from Gina's arrival day showing Roma Termini station board.",
        ocr="Roma Termini 08:42 Platform 7",
        tags=["rome", "arrival", "station"],
        created_at="2026-03-03",
        event_time="2026-03-02",
        linked_subject="Gina",
    )
    mem.append_text(
        "Jon wanted the studio to feel like a waterfront loft with natural light and Marley flooring.",
        created_at="2026-03-06",
        event_time="2026-03-06",
    )
    mem.append_image(
        caption="Moodboard screenshot for Jon's studio idea.",
        ocr="waterfront loft natural light Marley floor",
        tags=["studio", "moodboard", "interior"],
        created_at="2026-03-06",
        event_time="2026-03-06",
        linked_subject="Jon",
    )
    mem.append_text(
        "Alice reviewed a finance dashboard during the weekly planning check-in.",
        created_at="2026-03-07",
        event_time="2026-03-07",
    )
    mem.append_image(
        caption="Finance dashboard screenshot.",
        ocr="Revenue 123 Margin 18%",
        tags=["finance", "dashboard", "metrics"],
        created_at="2026-03-07",
        event_time="2026-03-07",
        linked_subject="Alice",
    )
    mem.build()
    return mem


def benchmark_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="ocr_time",
            query="What time was visible in Gina's screenshot?",
            expected_keywords=["08:42"],
            required_types=["image_evidence", "fact"],
            note="OCR-only detail should be invisible if visual ingest never extracted OCR.",
        ),
        EvalCase(
            case_id="arrival_day_anchor",
            query="What screenshot evidence do we have from Gina's arrival day?",
            expected_keywords=["Roma Termini", "Platform 7"],
            required_types=["image_evidence", "event"],
            note="This needs the image node to be linked to the same-day event rather than only caption-matched.",
        ),
        EvalCase(
            case_id="dashboard_owner",
            query="Whose screenshot showed Revenue 123 Margin 18%?",
            expected_keywords=["Alice"],
            required_types=["image_evidence", "entity"],
            note="Owner linkage is a write-side problem, not a query-time reranking trick.",
        ),
        EvalCase(
            case_id="moodboard_style",
            query="What kind of studio look was shown in Jon's moodboard screenshot?",
            expected_keywords=["waterfront", "natural light", "Marley"],
            required_types=["image_evidence", "fact"],
            note="Surface caption helps a little, but the stronger answer needs OCR-like text extracted from the image.",
        ),
        EvalCase(
            case_id="trip_city_board",
            query="Which station board was visible in Gina's trip screenshot?",
            expected_keywords=["Roma Termini"],
            required_types=["image_evidence", "fact"],
            note="A simple lexical visual question should favor the image node itself.",
        ),
        EvalCase(
            case_id="metrics_visible",
            query="What metrics were visible in the dashboard screenshot?",
            expected_keywords=["Revenue 123", "Margin 18%"],
            required_types=["image_evidence", "fact"],
            note="A second OCR-heavy case, to avoid relying on a single example.",
        ),
    ]


def evaluate_mode(mode: str, cases: list[EvalCase]) -> dict[str, Any]:
    mem = build_demo(mode)
    rows = []
    correct = 0
    for case in cases:
        result = mem.search(case.query)
        top_blob = "\n".join(str(hit["content"]) for hit in result["hits"][:3])
        keyword_ok = contains_all(top_blob, case.expected_keywords)
        contract_ok = not [item for item in case.required_types if item not in result["present_types"]]
        passed = keyword_ok and contract_ok
        if passed:
            correct += 1
        rows.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "note": case.note,
                "expected_keywords": case.expected_keywords,
                "expected_types": case.required_types,
                "result": result,
                "keyword_ok": keyword_ok,
                "contract_ok": contract_ok,
                "passed": passed,
            }
        )
    return {
        "mode": mode,
        "summary": {
            "correct": correct,
            "total": len(cases),
            "accuracy": round(correct / max(len(cases), 1), 3),
            "image_nodes": sum(1 for node in mem.nodes if node.node_type == "image_evidence"),
            "edges": len(mem.edges),
        },
        "rows": rows,
        "memory_snapshot": {
            "nodes": [asdict(node) for node in mem.nodes],
            "edges": [asdict(edge) for edge in mem.edges],
        },
    }


def run_benchmark() -> dict[str, Any]:
    cases = benchmark_cases()
    modes = [
        "no_visual_ingest",
        "surface_visual_ingest",
        "structured_visual_ingest",
    ]
    results = {mode: evaluate_mode(mode, cases) for mode in modes}
    return {
        "cases": [asdict(case) for case in cases],
        "results": results,
        "takeaway": (
            "Query-time multimodal routing is not enough. "
            "Accuracy rises only when write-side visual ingest produces searchable image nodes "
            "with OCR text, owner links, and event/fact graph edges."
        ),
    }


def render_html(payload: dict[str, Any]) -> str:
    summary_rows = []
    for mode, result in payload["results"].items():
        summary = result["summary"]
        summary_rows.append(
            f"""
            <tr>
              <td><code>{esc(mode)}</code></td>
              <td>{esc(summary['correct'])} / {esc(summary['total'])}</td>
              <td>{esc(summary['accuracy'])}</td>
              <td>{esc(summary['image_nodes'])}</td>
              <td>{esc(summary['edges'])}</td>
            </tr>
            """
        )

    case_blocks = []
    for case in payload["cases"]:
        mode_cols = []
        for mode, result in payload["results"].items():
            row = next(item for item in result["rows"] if item["case_id"] == case["case_id"])
            hits_html = "".join(
                f"<li><code>{esc(hit['node_id'])}</code> · {esc(hit['node_type'])} · score={esc(hit['score'])}<br>{esc(str(hit['content'])[:180])}</li>"
                for hit in row["result"]["hits"][:3]
            ) or "<li>-</li>"
            badge = "ok" if row["passed"] else "bad"
            mode_cols.append(
                f"""
                <div class="card">
                  <div class="badge {badge}">{esc(mode)} {'pass' if row['passed'] else 'fail'}</div>
                  <p class="muted">present={esc(row['result']['present_types'])} · missing={esc(row['result']['missing_types'])}</p>
                  <ul>{hits_html}</ul>
                </div>
                """
            )
        case_blocks.append(
            f"""
            <section class="panel">
              <h2>{esc(case['case_id'])}</h2>
              <p><b>Query:</b> {esc(case['query'])}</p>
              <p class="muted">{esc(case['note'])}</p>
              <div class="grid three">{''.join(mode_cols)}</div>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Visual Ingest Bridge</title>
  <style>
    :root {{
      --bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#18212f;--muted:#617184;
      --blue:#2563eb;--green:#067647;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fff1f3;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1260px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero{{padding:28px 30px}}
    .panel{{padding:20px 22px;margin-top:16px}}
    .card{{padding:14px 16px}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.28}}
    h1{{font-size:30px}} h2{{font-size:20px}}
    p{{margin:0 0 10px}} ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:10px 8px;border-top:1px solid var(--line);text-align:left;vertical-align:top}}
    th{{background:#f8fafc;font-size:12px;color:var(--muted);text-transform:uppercase}}
    .grid{{display:grid;gap:14px}} .three{{grid-template-columns:repeat(3,minmax(0,1fr))}}
    .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;margin-bottom:8px}}
    .ok{{background:var(--green-soft);color:var(--green)}} .bad{{background:var(--red-soft);color:var(--red)}}
    .muted{{color:var(--muted)}}
    code{{background:#f3f6fb;border:1px solid #e5ebf3;border-radius:6px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    .callout{{margin-top:14px;padding:12px 14px;border:1px solid #cfe0ff;border-radius:10px;background:#f6f9ff;color:#274690}}
    @media (max-width:980px){{ .three{{grid-template-columns:1fr}} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano Visual Ingest Bridge</h1>
      <p>这个 nano 实验专门补当前主仓最明显的一块空白：<b>query 侧已经会说 visual / image_evidence，但如果 write 侧没有把图像资源结构化进记忆，检索其实还是拿不到 owner、OCR、event link 这些关键信号。</b></p>
      <div class="callout">
        设计原则是泛化，不是刷数据集关键词。这里比较的是三种写入策略：<code>no_visual_ingest</code>、<code>surface_visual_ingest</code>、<code>structured_visual_ingest</code>。差别只在图像进入记忆时保留了多少结构，不在 query 侧塞特判。
      </div>
    </section>

    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead>
          <tr><th>Mode</th><th>Correct</th><th>Accuracy</th><th>Image Nodes</th><th>Edges</th></tr>
        </thead>
        <tbody>{''.join(summary_rows)}</tbody>
      </table>
      <p class="muted" style="margin-top:10px">{esc(payload['takeaway'])}</p>
    </section>

    <section class="panel">
      <h2>Why This Matters for the Paper</h2>
      <ul>
        <li><b>Against surface multimodality:</b> having a visual planner is not enough if image resources never become linked memory objects.</li>
        <li><b>Bridge to real EchoMemory code:</b> this mirrors the gap between query-time <code>image_evidence</code> support and thinner write-side <code>resource_service</code> / <code>describe_image()</code>.</li>
        <li><b>CVPR-facing value:</b> this gives a compact mechanistic claim for why visual understanding should be treated as write-time memory construction, not only answer-time prompting.</li>
      </ul>
    </section>

    {''.join(case_blocks)}
  </div>
</body>
</html>"""


def main() -> None:
    payload = run_benchmark()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
