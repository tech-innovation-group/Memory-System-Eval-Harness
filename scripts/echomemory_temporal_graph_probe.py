#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProbeCase:
    question_id: str
    title: str
    question: str
    answer: str


DEFAULT_PROBE_IDS = (
    "conv-30_qa0",
    "conv-30_qa5",
    "conv-30_qa8",
    "conv-30_qa9",
    "conv-30_qa20",
    "conv-30_qa31",
    "conv-30_qa33",
    "conv-30_qa78",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def compact(text: Any, limit: int = 260) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def query_tokens(query: str) -> list[str]:
    lowered = norm(query)
    parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", lowered)
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "do", "did", "does", "what",
        "when", "where", "why", "how", "which", "who", "both", "have", "has", "had",
        "jon", "gina", "jean", "john",
    }
    return [part for part in parts if part not in stop]


def lexical_score(query: str, text: str) -> float:
    q = set(query_tokens(query))
    if not q:
        return 0.0
    t = set(query_tokens(text))
    overlap = len(q & t)
    return overlap / max(1.0, math.sqrt(len(q)))


def answer_support_score(answer: str, text: str) -> float:
    a = norm(answer)
    t = norm(text)
    if not a:
        return 0.0
    score = 0.0
    if a in t:
        score += 1.0
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", a)
    if years and all(y in t for y in years):
        score += 0.8
    words = [w for w in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", a) if len(w) > 1]
    if words:
        hit = sum(1 for w in words if w in t)
        score += hit / len(words)
    return score


def is_temporal_query(query: str) -> bool:
    return bool(re.search(r"\bwhen\b|什么时候|何时|日期|时间|多久|多长|visited|which city", str(query or ""), re.I))


def is_relation_or_list_query(query: str) -> bool:
    return bool(re.search(r"\bwhich\b|\bwho\b|\bboth\b|哪个|哪些|谁|共同|mentor|guide|city", str(query or ""), re.I))


def looks_like_entity(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if len(raw) <= 2:
        return False
    digits_removed = raw.replace(".", "").replace("-", "").replace("/", "")
    if digits_removed.isdigit():
        return False
    return True


def is_event_atom(atom: dict[str, Any]) -> bool:
    return (
        str(atom.get("atom_type") or "") == "event"
        or str(atom.get("state_kind") or "") == "event"
        or bool(atom.get("event_time"))
    )


def build_fact_node(atom: dict[str, Any]) -> dict[str, Any]:
    atom_id = str(atom.get("atom_id") or "")
    statement = str(atom.get("statement") or "")
    subject = str(atom.get("subject") or "")
    predicate = str(atom.get("predicate") or "")
    obj = str(atom.get("object") or "")
    event_time = str(atom.get("event_time") or "")
    valid_from = str(atom.get("valid_from") or "")
    valid_until = str(atom.get("valid_until") or "")
    return {
        "node_id": f"fact:{atom_id}",
        "node_type": "fact",
        "source_atom_id": atom_id,
        "summary": statement,
        "content": "\n".join(
            part for part in [
                statement,
                f"subject={subject}" if subject else "",
                f"predicate={predicate}" if predicate else "",
                f"object={obj}" if obj else "",
                f"event_time={event_time}" if event_time else "",
                f"valid_from={valid_from}" if valid_from else "",
                f"valid_until={valid_until}" if valid_until else "",
            ] if part
        ),
        "properties": {
            "statement": statement,
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "event_time": event_time,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "atom_type": str(atom.get("atom_type") or ""),
        },
    }


def build_event_node(atom: dict[str, Any]) -> dict[str, Any] | None:
    if not is_event_atom(atom):
        return None
    atom_id = str(atom.get("atom_id") or "")
    statement = str(atom.get("statement") or "")
    subject = str(atom.get("subject") or "")
    obj = str(atom.get("object") or "")
    event_time = str(atom.get("event_time") or "")
    participants = [part for part in [subject, obj if looks_like_entity(obj) else ""] if part]
    return {
        "node_id": f"event:{atom_id}",
        "node_type": "event",
        "source_atom_id": atom_id,
        "summary": statement,
        "content": "\n".join(
            part for part in [
                statement,
                f"event_time={event_time}" if event_time else "",
                f"participants={', '.join(participants)}" if participants else "",
            ] if part
        ),
        "properties": {
            "statement": statement,
            "event_time": event_time,
            "participants": participants,
        },
    }


def build_entity_nodes(atom: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    subject = str(atom.get("subject") or "")
    obj = str(atom.get("object") or "")
    candidates = []
    if looks_like_entity(subject):
        candidates.append(subject)
    if looks_like_entity(obj):
        candidates.append(obj)
    for name in candidates:
        out.append(
            {
                "node_id": f"entity:{name}",
                "node_type": "entity",
                "source_atom_id": str(atom.get("atom_id") or ""),
                "summary": name,
                "content": "\n".join(
                    part for part in [
                        f"name={name}",
                        compact(str(atom.get("statement") or ""), 120),
                        f"related_predicate={atom.get('predicate')}" if atom.get("predicate") else "",
                    ] if part
                ),
                "properties": {"name": name},
            }
        )
    return out


def load_atoms(account_root: Path) -> list[dict[str, Any]]:
    atoms_dir = account_root / "memory/.structured/atoms"
    atoms: list[dict[str, Any]] = []
    for path in sorted(atoms_dir.glob("*.json")):
        try:
            data = read_json(path)
        except Exception:
            continue
        if isinstance(data, dict):
            atoms.append(data)
    return atoms


def load_existing_graph_nodes(account_root: Path) -> list[dict[str, Any]]:
    node_root = account_root / "memory/.graph/nodes"
    nodes: list[dict[str, Any]] = []
    if not node_root.exists():
        return nodes
    for path in sorted(node_root.rglob("*.json")):
        try:
            data = read_json(path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        props = data.get("properties") or {}
        node_type = str(data.get("node_type") or path.parent.name or "")
        if node_type not in {"atom", "entity", "episode", "fact", "event"}:
            continue
        content = "\n".join(
            part for part in [
                str(data.get("summary_hint") or ""),
                *(f"{k}={v}" for k, v in props.items() if v not in (None, "", [], {})),
            ] if part
        )
        nodes.append(
            {
                "node_id": str(data.get("node_id") or ""),
                "node_type": node_type,
                "content": content,
                "summary": str(data.get("summary_hint") or ""),
                "properties": props,
            }
        )
    return nodes


def build_temporal_graph_from_atoms(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for atom in atoms:
        fact = build_fact_node(atom)
        by_id[fact["node_id"]] = fact
        event = build_event_node(atom)
        if event is not None:
            by_id[event["node_id"]] = event
        for entity in build_entity_nodes(atom):
            node_id = entity["node_id"]
            if node_id in by_id:
                existing = by_id[node_id]
                snippets = existing.setdefault("_entity_snippets", [])
                snippet = entity["content"]
                if snippet not in snippets and len(snippets) < 3:
                    snippets.append(snippet)
                    existing["content"] = "\n".join(snippets)
            else:
                entity["_entity_snippets"] = [entity["content"]]
                by_id[node_id] = entity
    return list(by_id.values())


def score_nodes(query: str, answer: str, nodes: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    temporal = is_temporal_query(query)
    relational = is_relation_or_list_query(query)
    for node in nodes:
        content = str(node.get("content") or "")
        if not content:
            continue
        lscore = lexical_score(query, content)
        ascore = answer_support_score(answer, content)
        bonus = 0.0
        node_type = str(node.get("node_type") or "")
        if temporal:
            if node_type == "event":
                bonus += 0.30
            elif node_type == "fact":
                bonus += 0.18
            elif node_type == "entity":
                bonus -= 0.10
        elif relational:
            if node_type == "fact":
                bonus += 0.14
            elif node_type == "entity":
                bonus += 0.04
        else:
            if node_type == "fact":
                bonus += 0.06
            elif node_type == "event":
                bonus += 0.04
        if node_type == "entity" and ascore < 0.35:
            bonus -= 0.10
        length_penalty = min(0.12, max(0.0, (len(content) - 220) / 2400.0))
        score = lscore * 0.95 + ascore * 1.25 + bonus - length_penalty
        if score <= 0:
            continue
        scored.append(
            {
                "node_id": node.get("node_id"),
                "node_type": node_type,
                "score": round(score, 4),
                "lexical_score": round(lscore, 4),
                "answer_support_score": round(ascore, 4),
                "content": compact(content, 420),
            }
        )
    scored.sort(key=lambda item: (item["score"], item["answer_support_score"], item["lexical_score"]), reverse=True)
    return scored[:top_k]


def load_conv30_questions(dataset_path: Path, sample_id: str, probe_ids: tuple[str, ...]) -> list[ProbeCase]:
    data = read_json(dataset_path)
    sample = None
    for item in data:
        if str(item.get("sample_id")) == sample_id:
            sample = item
            break
    if sample is None:
        raise ValueError(f"sample not found: {sample_id}")
    qa_rows = sample.get("qa") or []
    qmap: dict[str, ProbeCase] = {}
    for idx, qa in enumerate(qa_rows):
        qid = f"{sample_id}_qa{idx}"
        qmap[qid] = ProbeCase(
            question_id=qid,
            title=qid,
            question=str(qa.get("question") or ""),
            answer=str(qa.get("answer") or ""),
        )
    return [qmap[qid] for qid in probe_ids if qid in qmap]


def summarize_hits(hits: list[dict[str, Any]]) -> dict[str, Any]:
    node_types = [str(item.get("node_type") or "") for item in hits]
    counts = Counter(node_types)
    return {
        "top_hit_type": node_types[0] if node_types else "",
        "node_type_counts": dict(counts),
        "contains_fact": counts.get("fact", 0) > 0,
        "contains_event": counts.get("event", 0) > 0,
        "contains_entity": counts.get("entity", 0) > 0,
        "best_fact_rank": next((idx + 1 for idx, item in enumerate(hits) if item.get("node_type") == "fact"), 0),
        "best_event_rank": next((idx + 1 for idx, item in enumerate(hits) if item.get("node_type") == "event"), 0),
        "best_entity_rank": next((idx + 1 for idx, item in enumerate(hits) if item.get("node_type") == "entity"), 0),
    }


def render_html(report: dict[str, Any], out_path: Path) -> None:
    rows = []
    for item in report["cases"]:
        old_summary = item["old_graph_summary"]
        new_summary = item["temporal_graph_summary"]
        rows.append(
            f"""
            <tr>
              <td><code>{item['question_id']}</code><br>{item['question']}</td>
              <td>{item['answer']}</td>
              <td>
                top type: <b>{old_summary['top_hit_type'] or '-'}</b><br>
                types: <code>{json.dumps(old_summary['node_type_counts'], ensure_ascii=False)}</code><br>
                top hit: <div class="small">{item['old_graph_hits'][0]['content'] if item['old_graph_hits'] else 'none'}</div>
              </td>
              <td>
                top type: <b>{new_summary['top_hit_type'] or '-'}</b><br>
                types: <code>{json.dumps(new_summary['node_type_counts'], ensure_ascii=False)}</code><br>
                top hit: <div class="small">{item['temporal_graph_hits'][0]['content'] if item['temporal_graph_hits'] else 'none'}</div>
              </td>
            </tr>
            """
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Temporal Graph Prototype Probe</title>
  <style>
    :root {{
      --bg:#f6f8fb; --panel:#fff; --text:#172033; --muted:#667085; --line:#dde4ee;
      --blue:#2457c5; --blue-soft:#eef4ff; --green:#067647; --green-soft:#ecfdf3; --amber:#b54708; --amber-soft:#fff7ed;
    }}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,"PingFang SC","Microsoft YaHei",sans-serif}}
    header{{background:var(--panel);border-bottom:1px solid var(--line);padding:30px 38px 22px}}
    main{{max-width:1240px;margin:0 auto;padding:22px 20px 42px}}
    h1{{margin:0;font-size:30px;line-height:1.2}} h2{{margin:0 0 12px;font-size:22px}}
    p{{margin:8px 0}} .section{{margin-top:18px;padding:18px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}}
    .grid{{display:grid;gap:14px}} .grid.two{{grid-template-columns:repeat(2,minmax(0,1fr))}}
    .card{{padding:14px;border:1px solid var(--line);border-radius:8px;background:#fbfcff}}
    .callout{{margin-top:12px;padding:12px 14px;border-left:4px solid var(--blue);border-radius:8px;background:var(--blue-soft)}}
    .ok{{border-left-color:var(--green);background:var(--green-soft)}} .warn{{border-left-color:var(--amber);background:var(--amber-soft)}}
    table{{width:100%;border-collapse:collapse;table-layout:fixed}} th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;word-break:break-word}}
    th{{background:#f8fafc;color:#344054;font-size:12px}} tr:last-child td{{border-bottom:0}}
    code{{background:#f8fafc;border:1px solid var(--line);border-radius:6px;padding:2px 5px}}
    .small{{font-size:12px;color:var(--muted)}}
    @media (max-width:960px){{header{{padding:22px 16px 16px}} main{{padding:14px 10px 34px}} .grid.two{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <header>
    <h1>EchoMemory Temporal Graph Prototype Probe</h1>
    <p style="margin-top:10px;color:#667085;max-width:980px;">
      这份 probe 不依赖平台是否已经切到 temporal-graph 代码根，而是直接基于现有 conv-30 atom 产物离线重建
      <code>fact / event / entity</code> 节点，用 8 道代表性问题观察新图结构是否更像“应该被检索到的证据层”。
    </p>
  </header>
  <main>
    <section class="section">
      <h2>结果概览</h2>
      <div class="grid two">
        <div class="card">
          <b>旧 graph 节点类型</b><br>
          <code>{json.dumps(report['old_graph_counts'], ensure_ascii=False)}</code>
        </div>
        <div class="card">
          <b>离线重建 temporal graph 节点类型</b><br>
          <code>{json.dumps(report['temporal_graph_counts'], ensure_ascii=False)}</code>
        </div>
      </div>
      <div class="callout ok">
        在这轮离线重建里，我们不再只有 <code>atom / entity / episode</code>，
        而是显式得到 <code>fact / event / entity</code> 三类可检索节点。
      </div>
      <div class="callout warn">
        这还是 evidence-level probe，不是完整 QA/Judge。它回答的是：
        <b>新图结构有没有生成、会不会对 temporal / relation / list 问题提供更像样的 top evidence</b>。
      </div>
    </section>

    <section class="section">
      <h2>Probe 题对照</h2>
      <table>
        <thead>
          <tr>
            <th style="width:24%">问题</th>
            <th style="width:12%">gold</th>
            <th style="width:32%">旧 graph top evidence</th>
            <th style="width:32%">temporal graph top evidence</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>

    <section class="section">
      <h2>结论</h2>
      <ul>
        <li>如果离线重建后的 top evidence 中，<code>event</code> 在时间题里显著增多，说明 Temporal Fact Graph 的方向是对的。</li>
        <li>如果 <code>entity</code> 在 list / city / relation 题里更容易进入前列，说明 event-centric / entity-centric 检索值得继续做。</li>
        <li>真正的下一步不是继续写概念，而是把平台默认 root 切到新代码根，再跑正式 QA/Judge。</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline probe for EchoMemory temporal graph prototype.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--dataset", default="/Users/chx/locomo-eval-web/dataset/locomo10.json")
    parser.add_argument("--sample", default="conv-30")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-html", required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    account = args.account
    account_roots = [workspace / account / account, workspace / account, workspace]
    account_root = next((root for root in account_roots if (root / "memory/.structured/atoms").exists()), None)
    if account_root is None:
        raise SystemExit(f"atom root not found under workspace={workspace} account={account}")

    atoms = load_atoms(account_root)
    old_graph = load_existing_graph_nodes(account_root)
    temporal_graph = build_temporal_graph_from_atoms(atoms)
    probes = load_conv30_questions(Path(args.dataset), args.sample, DEFAULT_PROBE_IDS)

    cases: list[dict[str, Any]] = []
    for probe in probes:
        old_hits = score_nodes(probe.question, probe.answer, old_graph, args.top_k)
        new_hits = score_nodes(probe.question, probe.answer, temporal_graph, args.top_k)
        cases.append(
            {
                "question_id": probe.question_id,
                "title": probe.title,
                "question": probe.question,
                "answer": probe.answer,
                "old_graph_hits": old_hits,
                "temporal_graph_hits": new_hits,
                "old_graph_summary": summarize_hits(old_hits),
                "temporal_graph_summary": summarize_hits(new_hits),
            }
        )

    report = {
        "workspace": str(workspace),
        "account": account,
        "account_root": str(account_root),
        "atom_count": len(atoms),
        "old_graph_counts": dict(Counter(str(item.get("node_type") or "") for item in old_graph)),
        "temporal_graph_counts": dict(Counter(str(item.get("node_type") or "") for item in temporal_graph)),
        "cases": cases,
    }

    out_json = Path(args.out_json).expanduser().resolve()
    out_html = Path(args.out_html).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(report, out_html)
    print(str(out_json))
    print(str(out_html))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
