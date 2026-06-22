from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path


PROJECT_ROOT = Path("/Users/chx/Code/echomemory/echo_memory_v006")
OUTPUT_DIR = Path("/Users/chx/locomo-eval-web/experiments/echomemory_mm_real_smoke_20260613")
OUTPUT_JSON = OUTPUT_DIR / "mm_real_smoke_result.json"
OUTPUT_HTML = OUTPUT_DIR / "mm_real_smoke_report.html"


async def _create_config() -> str:
    schema_dir = PROJECT_ROOT / "configs" / "schemas"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            "tenant:\n"
            "  id: mm-smoke-tenant\n"
            "echofs:\n"
            "  backend: memory\n"
            f"schemas:\n  path: {schema_dir}\n"
            "memory:\n"
            "  pipeline:\n"
            "    mode: atom_first\n"
            "    auto_flush_on_message_persisted: true\n"
            "search:\n"
            "  intent:\n"
            "    enabled: false\n"
            "    llm_first: false\n"
            "    llm_fallback: false\n"
            "vector:\n"
            "  indexing_enabled: false\n"
            "graph:\n"
            "  decay:\n"
            "    enabled: false\n"
        )
        return f.name


def _esc(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def run() -> dict:
    import sys

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from echomem.runtime.bootstrap import open_runtime
    from echomem.utils.domain.context import RequestContext

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config_path = await _create_config()
    runtime = await open_runtime(config_path)
    ctx = RequestContext(
        account_id="mm-smoke",
        user_id="user-mm",
        agent_id="agent-mm",
    )

    try:
        session = await runtime.services.session.create_session("MM Smoke", ctx)
        session_id = session.session_id
        ctx = RequestContext(
            account_id="mm-smoke",
            user_id="user-mm",
            agent_id="agent-mm",
            session_id=session_id,
        )

        await runtime.services.session.add_message(
            session_id,
            "user",
            "这是我今天看到的仪表盘截图。",
            ctx,
            obs_type="image",
            resource_uri="echo://mm-smoke/resources/screenshot-001",
            mime="image/png",
            caption="Finance dashboard screenshot",
            ocr="Revenue 123; Margin 18%",
            linked_subject="Alice",
            tags=["dashboard", "finance", "ocr"],
        )
        await runtime.services.session.add_message(
            session_id,
            "user",
            "Alice reviewed the dashboard today.",
            ctx,
        )

        await asyncio.sleep(0.05)
        # Force pipeline in case async flush timing varies.
        if getattr(runtime.services, "atom_pipeline", None) is not None:
            await runtime.services.atom_pipeline.ingest_message(session_id, ctx)

        image_node = await runtime.services.graph_memory.get_node(
            "image_evidence:msg-000000000000", ctx
        )
        # We do not know the generated message id in advance; scan instead.
        all_nodes = await runtime.services.graph_memory.list_nodes("image_evidence", ctx)
        image_node = all_nodes[0] if all_nodes else None
        visual_edges = await runtime.services.graph_memory.list_edges(
            ctx, relation_type="visual_evidence_of"
        )

        graph_only_items = await runtime.services.search._search_graph(
            "截图里写着什么？",
            ctx,
            [],
            None,
        )
        search_result = await runtime.services.search.search(
            "截图里写着什么？",
            {},
            ctx,
        )
        items = []
        for item in search_result.items[:5]:
            items.append(
                {
                    "content": item.content,
                    "source_uri": item.source_uri,
                    "memory_type": item.memory_type,
                    "confidence": item.confidence,
                    "trace": item.trace,
                }
            )

        result = {
            "session_id": session_id,
            "image_node_found": image_node is not None,
            "image_node": image_node.to_dict() if image_node else None,
            "search_query": "截图里写着什么？",
            "graph_only_result_count": len(graph_only_items),
            "visual_evidence_edge_count": len(visual_edges),
            "visual_evidence_edges": [edge.to_dict() for edge in visual_edges[:5]],
            "graph_only_top_items": [
                {
                    "content": item.content,
                    "source_uri": item.source_uri,
                    "memory_type": item.memory_type,
                    "confidence": item.confidence,
                    "trace": item.trace,
                }
                for item in graph_only_items[:5]
            ],
            "search_result_count": len(search_result.items),
            "top_items": items,
            "top1_source_uri": items[0]["source_uri"] if items else "",
            "top1_memory_type": items[0]["memory_type"] if items else "",
            "top1_node_type": items[0].get("trace", {}).get("node_type", "") if items else "",
            "top1_strategy": items[0].get("trace", {}).get("strategy", "") if items else "",
            "ocr_visible_in_top1": ("Revenue 123" in items[0]["content"]) if items else False,
        }
        return result
    finally:
        await runtime.stop()
        Path(config_path).unlink(missing_ok=True)


def render_html(result: dict) -> str:
    top_rows = []
    for item in result.get("top_items", []):
        top_rows.append(
            "<tr>"
            f"<td>{_esc(item.get('memory_type', ''))}</td>"
            f"<td>{_esc(item.get('source_uri', ''))}</td>"
            f"<td>{_esc(item.get('trace', {}).get('node_type', ''))}</td>"
            f"<td>{item.get('confidence', 0.0):.3f}</td>"
            f"<td>{_esc(item.get('content', ''))}</td>"
            "</tr>"
        )
    top_rows_html = "\n".join(top_rows) or "<tr><td colspan='5'>No hits</td></tr>"

    node_block = "<p>No image node found.</p>"
    if result.get("image_node"):
        node_block = (
            "<pre>"
            + _esc(json.dumps(result["image_node"], ensure_ascii=False, indent=2))
            + "</pre>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory-MM Real Smoke</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #f4f7fb;
      color: #162033;
      font: 14px/1.68 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
    }}
    .page {{ max-width: 1100px; margin: 0 auto; }}
    .card {{
      background: #fff;
      border: 1px solid #d8e1ee;
      border-radius: 10px;
      padding: 20px 22px;
      margin-bottom: 16px;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.08);
    }}
    h1, h2 {{ margin: 0 0 12px; }}
    p {{ color: #5e697b; }}
    .kpis {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .kpi {{
      border: 1px solid #d8e1ee;
      background: #f9fbff;
      border-radius: 8px;
      padding: 12px 14px;
      min-width: 180px;
    }}
    .label {{ font-size: 12px; color: #5e697b; }}
    .value {{ font-size: 18px; font-weight: 700; color: #162033; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      border-top: 1px solid #d8e1ee;
      text-align: left;
      vertical-align: top;
      padding: 10px;
    }}
    th {{
      background: #fbfcff;
      color: #5e697b;
      font-size: 12px;
    }}
    pre {{
      background: #f3f6fb;
      border-radius: 8px;
      padding: 12px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    code {{ background: #f2f6fb; border-radius: 4px; padding: 1px 4px; }}
  </style>
</head>
<body>
  <div class="page">
    <div class="card">
      <h1>EchoMemory-MM Real Smoke</h1>
      <p>这个 smoke 不再是 unit test，也不是 nano。它直接走主代码 runtime：<code>image observation -&gt; atom_first pipeline -&gt; graph node -&gt; visual query search</code>。</p>
      <div class="kpis">
        <div class="kpi"><div class="label">image node found</div><div class="value">{_esc(result.get("image_node_found"))}</div></div>
        <div class="kpi"><div class="label">top1 memory type</div><div class="value">{_esc(result.get("top1_memory_type", ""))}</div></div>
        <div class="kpi"><div class="label">top1 node type</div><div class="value">{_esc(result.get("top1_node_type", ""))}</div></div>
        <div class="kpi"><div class="label">OCR visible in top1</div><div class="value">{_esc(result.get("ocr_visible_in_top1"))}</div></div>
      </div>
    </div>
    <div class="card">
      <h2>Image Node</h2>
      {node_block}
    </div>
    <div class="card">
      <h2>Top Search Hits</h2>
      <table>
        <thead>
          <tr>
            <th>memory_type</th>
            <th>source_uri</th>
            <th>node_type</th>
            <th>confidence</th>
            <th>content</th>
          </tr>
        </thead>
        <tbody>
          {top_rows_html}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    result = asyncio.run(run())
    OUTPUT_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    OUTPUT_HTML.write_text(render_html(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"wrote: {OUTPUT_JSON}")
    print(f"wrote: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
