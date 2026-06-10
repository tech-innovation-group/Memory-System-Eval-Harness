from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _truncate(text: Any, limit: int = 56) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def _infer_dataset_name(graph_dir: Path) -> str:
    parts = list(graph_dir.parts)
    if "memory" in parts:
        idx = parts.index("memory")
        if idx >= 1:
            return parts[idx - 1]
    return graph_dir.parent.name or graph_dir.name


def _build_directory_breakdown(graph_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for top_dir in sorted(p for p in graph_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        top_files = list(top_dir.rglob("*.json"))
        rows.append({"path": top_dir.name, "level": 0, "file_count": len(top_files)})
        for child in sorted(p for p in top_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            child_files = list(child.rglob("*.json"))
            rows.append({"path": f"{top_dir.name}/{child.name}", "level": 1, "file_count": len(child_files)})
    return rows


def _layout_positions(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    if not nodes:
        return {}

    type_centers = {
        "episode": (0.0, -0.95),
        "entity": (-1.0, 0.55),
        "atom": (1.0, 0.55),
    }
    nodes_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_type[node["type"]].append(node)

    positions: dict[str, list[float]] = {}
    for node_type, bucket in nodes_by_type.items():
        center_x, center_y = type_centers.get(node_type, (0.0, 0.0))
        radius = 0.32 if node_type == "episode" else 0.62
        for idx, node in enumerate(bucket):
            angle = (2.0 * math.pi * idx) / max(len(bucket), 1)
            wobble = 0.08 * ((idx % 5) - 2)
            positions[node["id"]] = [
                center_x + (radius + wobble) * math.cos(angle),
                center_y + (radius + wobble) * math.sin(angle),
            ]

    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1

    for _ in range(90):
        deltas = {node["id"]: [0.0, 0.0] for node in nodes}
        node_ids = [node["id"] for node in nodes]

        for i, src in enumerate(node_ids):
            sx, sy = positions[src]
            for dst in node_ids[i + 1 :]:
                dx, dy = positions[dst]
                vx = sx - dx
                vy = sy - dy
                dist2 = vx * vx + vy * vy + 0.003
                force = 0.004 / dist2
                fx = vx * force
                fy = vy * force
                deltas[src][0] += fx
                deltas[src][1] += fy
                deltas[dst][0] -= fx
                deltas[dst][1] -= fy

        for edge in edges:
            src = edge["source"]
            dst = edge["target"]
            sx, sy = positions[src]
            dx, dy = positions[dst]
            vx = dx - sx
            vy = dy - sy
            dist = math.sqrt(vx * vx + vy * vy) + 0.001
            target = 0.28 if edge["relation"] == "contains" else 0.18
            force = 0.012 * (dist - target)
            fx = (vx / dist) * force
            fy = (vy / dist) * force
            deltas[src][0] += fx
            deltas[src][1] += fy
            deltas[dst][0] -= fx
            deltas[dst][1] -= fy

        for node in nodes:
            node_id = node["id"]
            x, y = positions[node_id]
            dx, dy = deltas[node_id]
            grav_x, grav_y = type_centers.get(node["type"], (0.0, 0.0))
            degree_boost = min(degree.get(node_id, 0), 12) / 12.0
            x += dx * 0.82 + (grav_x - x) * (0.018 - degree_boost * 0.004)
            y += dy * 0.82 + (grav_y - y) * (0.018 - degree_boost * 0.004)
            positions[node_id] = [max(-1.9, min(1.9, x)), max(-1.55, min(1.55, y))]

    return {node_id: {"x": xy[0], "y": xy[1]} for node_id, xy in positions.items()}


def build_graph_dataset(graph_dir: Path) -> dict[str, Any]:
    node_files = sorted(graph_dir.glob("nodes/*/*.json"))
    edge_files = sorted(graph_dir.glob("edges/*/*.json"))
    adjacency_files = sorted(
        p for p in graph_dir.glob("adjacency/*.json")
        if p.name != ".echofs_meta"
    )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_map: dict[str, dict[str, Any]] = {}
    relation_counter: Counter[str] = Counter()
    node_type_counter: Counter[str] = Counter()
    in_degree: Counter[str] = Counter()
    out_degree: Counter[str] = Counter()
    adjacency_map: dict[str, dict[str, Any]] = {}
    raw_node_ids: set[str] = set()
    missing_endpoint_rows: list[dict[str, Any]] = []
    missing_endpoint_type_counts: Counter[str] = Counter()
    missing_endpoint_relation_counts: Counter[str] = Counter()

    def ensure_stub_node(node_id: str) -> None:
        if node_id in node_map:
            return
        prefix, _, raw_name = node_id.partition(":")
        node_type = prefix if prefix else "unknown"
        summary = raw_name or node_id
        stub = {
            "id": node_id,
            "type": node_type,
            "label": _truncate(summary, 24),
            "summary": summary,
            "salience": None,
            "temporal_scope": None,
            "backing_plane": "stub",
            "backing_ref": raw_name or node_id,
            "properties": {"name": summary, "stub": True},
            "raw": {"node_id": node_id, "node_type": node_type, "stub": True},
        }
        nodes.append(stub)
        node_map[node_id] = stub
        node_type_counter[node_type] += 1

    for path in node_files:
        payload = _read_json(path)
        node_id = payload.get("node_id", path.stem)
        node_type = payload.get("node_type", path.parent.name)
        props = payload.get("properties") or {}
        summary_hint = payload.get("summary_hint") or ""
        if node_type == "atom":
            label = _truncate(props.get("statement") or summary_hint or node_id.replace("atom:", ""), 30)
        elif node_type == "entity":
            label = _truncate(props.get("name") or summary_hint or node_id.replace("entity:", ""), 24)
        elif node_type == "episode":
            label = _truncate(summary_hint or node_id.replace("episode:", ""), 24)
        else:
            label = _truncate(summary_hint or node_id, 24)
        node = {
            "id": node_id,
            "type": node_type,
            "label": label,
            "summary": summary_hint or label,
            "salience": payload.get("salience"),
            "temporal_scope": payload.get("temporal_scope"),
            "backing_plane": payload.get("backing_plane"),
            "backing_ref": payload.get("backing_ref"),
            "properties": props,
            "raw": payload,
        }
        nodes.append(node)
        node_map[node_id] = node
        raw_node_ids.add(node_id)
        node_type_counter[node_type] += 1

    for path in edge_files:
        payload = _read_json(path)
        relation = payload.get("relation_type", path.parent.name)
        source = payload.get("source_id")
        target = payload.get("target_id")
        if not source or not target:
            continue
        for endpoint, endpoint_role in ((source, "source"), (target, "target")):
            if endpoint not in raw_node_ids:
                missing_endpoint_rows.append(
                    {
                        "endpoint": endpoint,
                        "relation": relation,
                        "edge_id": payload.get("edge_id", path.stem),
                        "role": endpoint_role,
                    }
                )
                missing_endpoint_type_counts[endpoint.split(":", 1)[0] if ":" in endpoint else endpoint] += 1
                missing_endpoint_relation_counts[relation] += 1
        ensure_stub_node(source)
        ensure_stub_node(target)
        edge = {
            "id": payload.get("edge_id", path.stem),
            "source": source,
            "target": target,
            "relation": relation,
            "weight": payload.get("weight", 1.0),
            "support_count": payload.get("support_count", 1),
            "raw": payload,
        }
        edges.append(edge)
        relation_counter[relation] += 1
        out_degree[source] += 1
        in_degree[target] += 1

    for path in adjacency_files:
        adjacency_map[unquote(path.stem)] = _read_json(path)

    degree_counter: Counter[str] = Counter()
    for edge in edges:
        degree_counter[edge["source"]] += 1
        degree_counter[edge["target"]] += 1

    for node in nodes:
        node["degree"] = degree_counter.get(node["id"], 0)
        node["in_degree"] = in_degree.get(node["id"], 0)
        node["out_degree"] = out_degree.get(node["id"], 0)
        node["adjacency"] = adjacency_map.get(node["id"], {"incoming": [], "outgoing": []})

    positions = _layout_positions(nodes, edges)
    for node in nodes:
        node["position"] = positions.get(node["id"], {"x": 0.0, "y": 0.0})

    top_nodes = sorted(nodes, key=lambda item: (-item["degree"], item["label"]))[:20]
    unique_missing_nodes = sorted({row["endpoint"] for row in missing_endpoint_rows})
    stub_node_count = sum(1 for node in nodes if node.get("backing_plane") == "stub")

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "adjacency_count": len(adjacency_files),
        "node_types": dict(node_type_counter),
        "relations": dict(relation_counter.most_common()),
        "avg_degree": round((sum(degree_counter.values()) / len(nodes)) if nodes else 0.0, 2),
        "max_degree": max((node["degree"] for node in nodes), default=0),
    }
    integrity = {
        "raw_node_file_count": len(node_files),
        "raw_edge_file_count": len(edge_files),
        "raw_adjacency_file_count": len(adjacency_files),
        "stub_node_count": stub_node_count,
        "missing_endpoint_count": len(missing_endpoint_rows),
        "unique_missing_node_count": len(unique_missing_nodes),
        "missing_node_ids": unique_missing_nodes[:200],
        "missing_endpoint_type_counts": dict(missing_endpoint_type_counts),
        "missing_endpoint_relation_counts": dict(missing_endpoint_relation_counts),
        "missing_endpoint_examples": missing_endpoint_rows[:40],
        "has_structural_gaps": bool(missing_endpoint_rows),
    }

    return {
        "name": _infer_dataset_name(graph_dir),
        "path": str(graph_dir),
        "stats": stats,
        "integrity": integrity,
        "nodes": nodes,
        "edges": edges,
        "top_nodes": [
            {
                "id": node["id"],
                "label": node["label"],
                "type": node["type"],
                "degree": node["degree"],
                "summary": _truncate(node["summary"], 48),
            }
            for node in top_nodes
        ],
        "directory_breakdown": _build_directory_breakdown(graph_dir),
        "sample_node": nodes[0] if nodes else None,
        "sample_edge": edges[0] if edges else None,
    }


def render_graph_report_html(
    graph_dir: Path,
    *,
    run_title: str = "",
    run_dir: str = "",
) -> str:
    dataset = build_graph_dataset(graph_dir)
    data_json = json.dumps(
        {
            "dataset": dataset,
            "meta": {
                "run_title": run_title,
                "run_dir": run_dir,
                "graph_dir": str(graph_dir),
            },
        },
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory 图诊断</title>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #121933;
      --panel-2: #162040;
      --text: #e8edf8;
      --muted: #94a3c7;
      --border: rgba(255,255,255,0.08);
      --entity: #22c55e;
      --atom: #f59e0b;
      --episode: #a78bfa;
      --warn: #f59e0b;
      --danger: #fb7185;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(34,197,94,0.08), transparent 24%),
        radial-gradient(circle at top right, rgba(125,211,252,0.10), transparent 24%),
        var(--bg);
      color: var(--text);
    }}
    .page {{
      max-width: 1540px;
      margin: 0 auto;
      padding: 24px;
    }}
    .hero {{
      padding: 20px 0 12px;
    }}
    h1, h2, h3 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 30px; line-height: 1.1; }}
    h2 {{ font-size: 18px; }}
    p {{ color: var(--muted); line-height: 1.6; }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 12px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.03);
      border-radius: 999px;
      color: var(--muted);
      font-size: 13px;
    }}
    .banner {{
      margin: 18px 0;
      padding: 14px 16px;
      border-radius: 10px;
      border: 1px solid rgba(251, 113, 133, 0.3);
      background: rgba(251, 113, 133, 0.10);
      color: #ffd6de;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 420px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }}
    .panel {{
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
      border-radius: 10px;
      padding: 16px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.18);
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }}
    .stat {{
      padding: 14px;
      border-radius: 10px;
      background: var(--panel-2);
      border: 1px solid var(--border);
      min-height: 94px;
    }}
    .stat .k {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 10px;
    }}
    .stat .v {{
      font-size: 28px;
      line-height: 1;
      font-weight: 700;
    }}
    .grid-2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-top: 16px;
    }}
    .subpanel {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      background: rgba(255,255,255,0.02);
    }}
    .subpanel h3 {{
      font-size: 15px;
      margin-bottom: 10px;
    }}
    .table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .table th, .table td {{
      text-align: left;
      padding: 8px 10px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      vertical-align: top;
    }}
    .table th {{
      color: var(--muted);
      font-weight: 600;
    }}
    .type-tag, .rel-tag {{
      display: inline-flex;
      align-items: center;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.03);
    }}
    .type-entity {{ color: var(--entity); }}
    .type-atom {{ color: var(--atom); }}
    .type-episode {{ color: var(--episode); }}
    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }}
    .toggle {{
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: rgba(255,255,255,0.03);
      color: var(--text);
      cursor: pointer;
      font-size: 13px;
      user-select: none;
    }}
    .toggle.active {{
      background: rgba(125,211,252,0.12);
      border-color: rgba(125,211,252,0.38);
    }}
    .canvas-wrap {{
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)),
        #0b1328;
      min-height: 760px;
      position: relative;
    }}
    canvas {{
      display: block;
      width: 100%;
      height: 760px;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      padding: 12px 14px;
      border-top: 1px solid var(--border);
      background: rgba(0,0,0,0.16);
      color: var(--muted);
      font-size: 12px;
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 6px;
      vertical-align: middle;
    }}
    .entity {{ background: var(--entity); }}
    .atom {{ background: var(--atom); }}
    .episode {{ background: var(--episode); }}
    .tooltip {{
      position: absolute;
      pointer-events: none;
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: rgba(4, 9, 22, 0.92);
      color: var(--text);
      font-size: 12px;
      line-height: 1.5;
      display: none;
      max-width: 320px;
      z-index: 5;
    }}
    .list {{
      display: grid;
      gap: 8px;
      max-height: 360px;
      overflow: auto;
    }}
    .list-item {{
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: rgba(255,255,255,0.02);
      cursor: pointer;
    }}
    .list-item:hover, .list-item.active {{
      background: rgba(125,211,252,0.10);
      border-color: rgba(125,211,252,0.28);
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
      color: #c9d4f5;
    }}
    .muted {{
      color: var(--muted);
    }}
    .path {{
      color: #d6ddf6;
      word-break: break-all;
      font-size: 12px;
    }}
    @media (max-width: 1200px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid-2 {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 720px) {{
      .page {{ padding: 16px; }}
      .stats {{ grid-template-columns: 1fr; }}
      canvas {{ height: 560px; }}
      .canvas-wrap {{ min-height: 560px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory 图诊断报告</h1>
      <p>
        这页从当前 run 对应的 <code>.graph</code> 目录生成，既保留图结构浏览能力，也额外标出原始图文件里的结构缺口。
        如果这里出现 stub 节点或大量缺失 entity 端点，说明底层图同步还不完全闭合。
      </p>
      <div class="chips">
        <span class="chip">Run: <code id="runTitle"></code></span>
        <span class="chip">Graph Root: <code id="graphRoot"></code></span>
      </div>
    </section>

    <div id="integrityBanner" class="banner" hidden></div>

    <div class="layout">
      <div>
        <div class="panel">
          <h2>筛选与目录</h2>
          <div style="margin-top: 12px;">
            <label for="searchBox">搜索节点</label>
            <input id="searchBox" placeholder="比如 Gina / 现代舞 / atom-08c..." />
          </div>
          <div style="margin-top: 10px;">
            <label>节点类型过滤</label>
            <div class="filter-row">
              <button class="toggle active" data-type="episode">episode</button>
              <button class="toggle active" data-type="entity">entity</button>
              <button class="toggle active" data-type="atom">atom</button>
            </div>
          </div>
          <div style="margin-top: 16px;">
            <div class="muted" style="font-size:12px; margin-bottom:6px;">图目录路径</div>
            <div id="datasetPath" class="path"></div>
          </div>
        </div>

        <div class="panel" style="margin-top: 18px;">
          <h2>目录剖面</h2>
          <p class="muted" style="margin: 8px 0 12px;">按子目录统计文件数，快速看图存储实际落盘情况。</p>
          <div id="directoryBreakdown"></div>
        </div>

        <div class="panel" style="margin-top: 18px;">
          <h2>高连接节点</h2>
          <p class="muted" style="margin: 8px 0 12px;">这些通常是当前记忆图里最中心的实体、事实或 episode。</p>
          <div id="topNodes" class="list"></div>
        </div>

        <div class="panel" style="margin-top: 18px;">
          <h2>结构缺口</h2>
          <p class="muted" style="margin: 8px 0 12px;">这里列的是原始节点文件里缺失，但被边引用到的端点。</p>
          <div id="missingEndpointTable"></div>
        </div>
      </div>

      <div>
        <div class="panel">
          <h2>图结构概览</h2>
          <div id="statCards" class="stats"></div>
          <div class="grid-2">
            <div class="subpanel">
              <h3>边类型分布</h3>
              <div id="relationTable"></div>
            </div>
            <div class="subpanel">
              <h3>节点详情</h3>
              <div id="nodeDetails" class="muted">点击右下图中的节点，或点击左侧高连接节点列表。</div>
            </div>
          </div>
        </div>

        <div class="panel" style="margin-top: 18px;">
          <h2>节点关系图</h2>
          <p class="muted" style="margin: 8px 0 12px;">
            颜色区分 <code>episode / entity / atom</code>。点击节点后会高亮一跳邻居，并在上方显示原始 JSON 摘要。
          </p>
          <div class="canvas-wrap">
            <canvas id="graphCanvas"></canvas>
            <div id="tooltip" class="tooltip"></div>
          </div>
          <div class="legend">
            <span><span class="dot episode"></span>episode</span>
            <span><span class="dot entity"></span>entity</span>
            <span><span class="dot atom"></span>atom</span>
            <span><span class="dot" style="background:#94a3b8;"></span>contains</span>
            <span><span class="dot" style="background:#38bdf8;"></span>about</span>
            <span><span class="dot" style="background:#fb7185;"></span>其它关系</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const payload = {data_json};
    const dataset = payload.dataset;
    const meta = payload.meta || {{}};

    const colors = {{
      episode: "#a78bfa",
      entity: "#22c55e",
      atom: "#f59e0b",
      edgeDefault: "rgba(148, 163, 184, 0.22)",
      about: "rgba(56, 189, 248, 0.45)",
      contains: "rgba(148, 163, 184, 0.34)",
      other: "rgba(251, 113, 133, 0.42)",
    }};

    const state = {{
      selectedNodeId: null,
      search: "",
      activeTypes: new Set(["episode", "entity", "atom"]),
      hoverNodeId: null,
      rendered: null,
    }};

    const searchBox = document.getElementById("searchBox");
    const nodeDetails = document.getElementById("nodeDetails");
    const topNodesContainer = document.getElementById("topNodes");
    const directoryBreakdown = document.getElementById("directoryBreakdown");
    const relationTable = document.getElementById("relationTable");
    const statCards = document.getElementById("statCards");
    const datasetPath = document.getElementById("datasetPath");
    const missingEndpointTable = document.getElementById("missingEndpointTable");
    const integrityBanner = document.getElementById("integrityBanner");
    const canvas = document.getElementById("graphCanvas");
    const tooltip = document.getElementById("tooltip");
    const ctx = canvas.getContext("2d");

    document.getElementById("runTitle").textContent = meta.run_title || "-";
    document.getElementById("graphRoot").textContent = meta.graph_dir || dataset.path || "-";

    function esc(text) {{
      return String(text ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }}

    function setCanvasSize() {{
      const ratio = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * ratio;
      canvas.height = rect.height * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }}

    function nodeMatches(node) {{
      if (!state.activeTypes.has(node.type)) return false;
      if (!state.search) return true;
      const haystack = `${{node.id}} ${{node.label}} ${{node.summary}} ${{JSON.stringify(node.properties)}}`.toLowerCase();
      return haystack.includes(state.search);
    }}

    function currentNeighborSet() {{
      if (!state.selectedNodeId) return new Set();
      const node = dataset.nodes.find(item => item.id === state.selectedNodeId);
      if (!node) return new Set();
      const set = new Set([node.id]);
      (node.adjacency.outgoing || []).forEach(item => set.add(item.peer));
      (node.adjacency.incoming || []).forEach(item => set.add(item.peer));
      return set;
    }}

    function bindControls() {{
      searchBox.addEventListener("input", () => {{
        state.search = searchBox.value.trim().toLowerCase();
        renderTopNodes();
        renderGraph();
      }});
      document.querySelectorAll(".toggle").forEach(button => {{
        button.addEventListener("click", () => {{
          const value = button.dataset.type;
          if (state.activeTypes.has(value)) {{
            state.activeTypes.delete(value);
          }} else {{
            state.activeTypes.add(value);
          }}
          button.classList.toggle("active", state.activeTypes.has(value));
          if (state.selectedNodeId) {{
            const node = dataset.nodes.find(n => n.id === state.selectedNodeId);
            if (node && !state.activeTypes.has(node.type)) {{
              state.selectedNodeId = null;
            }}
          }}
          renderTopNodes();
          renderGraph();
        }});
      }});
    }}

    function renderBanner() {{
      const integrity = dataset.integrity || {{}};
      if (!integrity.has_structural_gaps) {{
        integrityBanner.hidden = true;
        return;
      }}
      integrityBanner.hidden = false;
      integrityBanner.innerHTML = `
        <strong>检测到图结构缺口</strong><br>
        原始图文件里有 <code>${{integrity.missing_endpoint_count}}</code> 个边端点引用了不存在的节点，
        涉及 <code>${{integrity.unique_missing_node_count}}</code> 个唯一节点；
        可视化为了保持图可读性，自动补了 <code>${{integrity.stub_node_count}}</code> 个 stub 节点。
      `;
    }}

    function renderStats() {{
      const stats = dataset.stats || {{}};
      const integrity = dataset.integrity || {{}};
      statCards.innerHTML = `
        <div class="stat"><div class="k">渲染节点数</div><div class="v">${{stats.node_count || 0}}</div></div>
        <div class="stat"><div class="k">渲染边数</div><div class="v">${{stats.edge_count || 0}}</div></div>
        <div class="stat"><div class="k">Stub 节点</div><div class="v">${{integrity.stub_node_count || 0}}</div></div>
        <div class="stat"><div class="k">缺失端点</div><div class="v">${{integrity.missing_endpoint_count || 0}}</div></div>
      `;
    }}

    function renderDirectoryBreakdown() {{
      const rows = (dataset.directory_breakdown || []).map(item => `
        <tr>
          <td>${{item.level === 1 ? "&nbsp;&nbsp;&nbsp;&nbsp;↳ " : ""}}${{esc(item.path)}}</td>
          <td>${{item.file_count}}</td>
        </tr>
      `).join("");
      directoryBreakdown.innerHTML = `
        <table class="table">
          <thead><tr><th>路径</th><th>JSON 文件数</th></tr></thead>
          <tbody>${{rows}}</tbody>
        </table>
      `;
      datasetPath.textContent = dataset.path || "-";
    }}

    function renderRelations() {{
      const rows = Object.entries((dataset.stats || {{}}).relations || {{}})
        .sort((a, b) => b[1] - a[1])
        .map(([rel, count]) => `
          <tr>
            <td><span class="rel-tag">${{esc(rel)}}</span></td>
            <td>${{count}}</td>
          </tr>
        `).join("");
      relationTable.innerHTML = `
        <table class="table">
          <thead><tr><th>relation_type</th><th>条数</th></tr></thead>
          <tbody>${{rows}}</tbody>
        </table>
      `;
    }}

    function renderMissingEndpoints() {{
      const integrity = dataset.integrity || {{}};
      const rows = (integrity.missing_endpoint_examples || []).map(item => `
        <tr>
          <td>${{esc(item.endpoint)}}</td>
          <td>${{esc(item.role)}}</td>
          <td>${{esc(item.relation)}}</td>
        </tr>
      `).join("");
      missingEndpointTable.innerHTML = integrity.has_structural_gaps ? `
        <table class="table">
          <thead><tr><th>缺失节点</th><th>端点角色</th><th>关系</th></tr></thead>
          <tbody>${{rows}}</tbody>
        </table>
      ` : "<p class='muted'>没有发现缺失端点，原始图文件的节点和边是闭合的。</p>";
    }}

    function renderTopNodes() {{
      const filtered = dataset.nodes
        .filter(nodeMatches)
        .sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label))
        .slice(0, 24);
      topNodesContainer.innerHTML = filtered.map(node => `
        <div class="list-item ${{state.selectedNodeId === node.id ? "active" : ""}}" data-id="${{esc(node.id)}}">
          <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start;">
            <div>
              <div><span class="type-tag type-${{esc(node.type)}}">${{esc(node.type)}}</span></div>
              <div style="margin-top:8px; font-weight:600;">${{esc(node.label)}}</div>
              <div class="muted" style="font-size:12px; margin-top:6px;">${{esc(node.summary)}}</div>
            </div>
            <div style="text-align:right;">
              <div class="muted" style="font-size:11px;">degree</div>
              <div style="font-size:22px; font-weight:700;">${{node.degree}}</div>
            </div>
          </div>
        </div>
      `).join("");
      topNodesContainer.querySelectorAll(".list-item").forEach(el => {{
        el.addEventListener("click", () => {{
          state.selectedNodeId = el.dataset.id;
          renderNodeDetails();
          renderTopNodes();
          renderGraph();
        }});
      }});
    }}

    function renderNodeDetails() {{
      const node = dataset.nodes.find(item => item.id === state.selectedNodeId);
      if (!node) {{
        nodeDetails.innerHTML = '<div class="muted">点击右下图中的节点，或点击左侧高连接节点列表。</div>';
        return;
      }}
      const outgoing = (node.adjacency.outgoing || []).slice(0, 20).map(item =>
        `<tr><td>${{esc(item.rel_type)}}</td><td>${{esc(item.peer)}}</td></tr>`
      ).join("");
      const incoming = (node.adjacency.incoming || []).slice(0, 20).map(item =>
        `<tr><td>${{esc(item.rel_type)}}</td><td>${{esc(item.peer)}}</td></tr>`
      ).join("");
      nodeDetails.innerHTML = `
        <div style="display:grid; gap:12px;">
          <div>
            <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
              <span class="type-tag type-${{esc(node.type)}}">${{esc(node.type)}}</span>
              <strong>${{esc(node.label)}}</strong>
            </div>
            <div class="muted" style="margin-top:8px;">${{esc(node.id)}}</div>
            <div class="muted" style="margin-top:6px;">summary: ${{esc(node.summary)}}</div>
            <div class="muted" style="margin-top:6px;">degree: ${{node.degree}} | in: ${{node.in_degree}} | out: ${{node.out_degree}}</div>
          </div>
          <div>
            <div style="font-weight:600; margin-bottom:6px;">properties</div>
            <pre>${{esc(JSON.stringify(node.properties, null, 2))}}</pre>
          </div>
          <div class="grid-2" style="margin-top:0;">
            <div class="subpanel">
              <h3>outgoing</h3>
              <table class="table"><thead><tr><th>关系</th><th>peer</th></tr></thead><tbody>${{outgoing || '<tr><td colspan="2" class="muted">无</td></tr>'}}</tbody></table>
            </div>
            <div class="subpanel">
              <h3>incoming</h3>
              <table class="table"><thead><tr><th>关系</th><th>peer</th></tr></thead><tbody>${{incoming || '<tr><td colspan="2" class="muted">无</td></tr>'}}</tbody></table>
            </div>
          </div>
        </div>
      `;
    }}

    function getRenderedGraph() {{
      const rect = canvas.getBoundingClientRect();
      const width = rect.width;
      const height = rect.height;
      const neighbors = currentNeighborSet();
      const visibleNodes = dataset.nodes.filter(node => state.activeTypes.has(node.type) && nodeMatches(node));
      const visibleNodeIds = new Set(visibleNodes.map(node => node.id));
      const nodes = visibleNodes.map(node => {{
        const px = ((node.position.x + 2.0) / 4.0) * (width - 60) + 30;
        const py = ((node.position.y + 1.7) / 3.4) * (height - 60) + 30;
        return {{...node, px, py}};
      }});
      const byId = new Map(nodes.map(node => [node.id, node]));
      const edges = dataset.edges
        .filter(edge => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
        .map(edge => ({{
          ...edge,
          sourceNode: byId.get(edge.source),
          targetNode: byId.get(edge.target),
          highlighted: neighbors.size ? (neighbors.has(edge.source) && neighbors.has(edge.target)) : false,
        }}))
        .filter(edge => edge.sourceNode && edge.targetNode);
      return {{nodes, edges}};
    }}

    function edgeColor(rel, highlighted) {{
      let base = colors.edgeDefault;
      if (rel === "about") base = colors.about;
      else if (rel === "contains") base = colors.contains;
      else if (rel) base = colors.other;
      if (!highlighted && state.selectedNodeId) {{
        return base.replace(/0\\.[0-9]+\\)/, "0.10)");
      }}
      return base;
    }}

    function nodeRadius(node) {{
      const degreeBoost = Math.min(node.degree || 0, 12);
      if (node.type === "episode") return 11 + degreeBoost * 0.35;
      if (node.type === "entity") return 7 + degreeBoost * 0.28;
      return 6 + degreeBoost * 0.22;
    }}

    function renderGraph() {{
      setCanvasSize();
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const rendered = getRenderedGraph();
      state.rendered = rendered;
      const neighbors = currentNeighborSet();
      for (const edge of rendered.edges) {{
        const {{sourceNode, targetNode}} = edge;
        ctx.beginPath();
        ctx.moveTo(sourceNode.px, sourceNode.py);
        ctx.lineTo(targetNode.px, targetNode.py);
        ctx.lineWidth = edge.highlighted ? 2.1 : 1.0;
        ctx.strokeStyle = edgeColor(edge.relation, edge.highlighted);
        ctx.stroke();
      }}
      for (const node of rendered.nodes) {{
        const selected = node.id === state.selectedNodeId;
        const hovered = node.id === state.hoverNodeId;
        const connected = !neighbors.size || neighbors.has(node.id);
        const radius = nodeRadius(node) + (selected ? 2.4 : hovered ? 1.2 : 0);
        ctx.beginPath();
        ctx.arc(node.px, node.py, radius, 0, Math.PI * 2);
        ctx.fillStyle = colors[node.type] || "#cbd5e1";
        ctx.globalAlpha = connected ? 1 : 0.33;
        ctx.fill();
        ctx.globalAlpha = 1;
        if (selected || hovered) {{
          ctx.lineWidth = selected ? 2.5 : 1.5;
          ctx.strokeStyle = "#ffffff";
          ctx.stroke();
        }}
      }}
    }}

    function updateTooltip(x, y, node) {{
      if (!node) {{
        tooltip.style.display = "none";
        return;
      }}
      tooltip.style.display = "block";
      tooltip.style.left = `${{x + 16}}px`;
      tooltip.style.top = `${{y + 16}}px`;
      tooltip.innerHTML = `
        <strong>${{esc(node.label)}}</strong><br>
        <span class="muted">${{esc(node.id)}}</span><br>
        degree=${{node.degree}} | in=${{node.in_degree}} | out=${{node.out_degree}}
      `;
    }}

    function findNodeAt(x, y) {{
      if (!state.rendered) return null;
      for (const node of state.rendered.nodes) {{
        const dx = x - node.px;
        const dy = y - node.py;
        const radius = nodeRadius(node) + 3;
        if (dx * dx + dy * dy <= radius * radius) return node;
      }}
      return null;
    }}

    canvas.addEventListener("mousemove", (event) => {{
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const node = findNodeAt(x, y);
      state.hoverNodeId = node ? node.id : null;
      renderGraph();
      updateTooltip(x, y, node);
    }});

    canvas.addEventListener("mouseleave", () => {{
      state.hoverNodeId = null;
      tooltip.style.display = "none";
      renderGraph();
    }});

    canvas.addEventListener("click", (event) => {{
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const node = findNodeAt(x, y);
      state.selectedNodeId = node ? node.id : null;
      renderNodeDetails();
      renderTopNodes();
      renderGraph();
    }});

    function renderAll() {{
      renderBanner();
      renderStats();
      renderDirectoryBreakdown();
      renderRelations();
      renderMissingEndpoints();
      renderTopNodes();
      renderNodeDetails();
      renderGraph();
    }}

    bindControls();
    renderAll();
    window.addEventListener("resize", () => renderGraph());
  </script>
</body>
</html>
"""

