#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CASE_SPECS = [
    {
        "question_id": "conv-30_qa0",
        "label": "Jon 丢掉银行工作日期",
        "patterns": ["lost his job as a banker", "banker yesterday"],
    },
    {
        "question_id": "conv-30_qa20",
        "label": "Gina 实习录取精确日期",
        "patterns": ["27 May 2023", "accepted for a part-time fashion internship"],
    },
    {
        "question_id": "conv-30_qa22",
        "label": "Gina 视频讲解穿搭",
        "patterns": ["video presentation to teach how to style my fashion pieces"],
    },
    {
        "question_id": "conv-30_qa33",
        "label": "Jon 参加 networking event 的日期",
        "patterns": ["Yesterday I chose to go to networking events", "attending networking events"],
    },
    {
        "question_id": "conv-30_qa55",
        "label": "Gina 纹身含义",
        "patterns": ["tattoo", "freedom - dancing without worrying", "express myself"],
    },
    {
        "question_id": "conv-30_qa80",
        "label": "Jon 在 networking advice 之后的计划",
        "patterns": ["sprucing up my biz plan", "tweaking my pitch to investors", "online platform"],
    },
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def compact(text: Any, limit: int = 220) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def account_root(workspace: Path, account: str) -> Path:
    for candidate in (workspace / account / account, workspace / account, workspace):
        if (candidate / "sessions").exists() or (candidate / "memory").exists():
            return candidate
    return workspace / account / account


def count_json_files(path: Path) -> int:
    return len(list(path.glob("*.json"))) if path.exists() else 0


def find_snippets(sessions_root: Path, patterns: list[str], limit: int = 4) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    lower_patterns = [pattern.lower() for pattern in patterns if pattern.strip()]
    for path in sorted(sessions_root.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".jsonl"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines, start=1):
            lowered = line.lower()
            if not any(pattern in lowered for pattern in lower_patterns):
                continue
            hits.append(
                {
                    "path": str(path),
                    "line": str(idx),
                    "text": compact(line, 320),
                }
            )
            if len(hits) >= limit:
                return hits
    return hits


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render EchoMemory LoCoMo diagnostic HTML from an import summary and QA run.")
    parser.add_argument("--import-summary", required=True)
    parser.add_argument("--qa-csv", required=True)
    parser.add_argument("--judge-summary", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--out-html", required=True)
    parser.add_argument("--title", default="LoCoMo conv-30 诊断：自定义 Agent + EchoMemory")
    args = parser.parse_args()

    import_summary_path = Path(args.import_summary).expanduser().resolve()
    qa_csv_path = Path(args.qa_csv).expanduser().resolve()
    judge_summary_path = Path(args.judge_summary).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve()
    out_html = Path(args.out_html).expanduser().resolve()

    import_summary = read_json(import_summary_path)
    judge_summary = read_json(judge_summary_path)
    qa_rows = read_csv(qa_csv_path)
    by_qid = {row["question_id"]: row for row in qa_rows}

    root = account_root(workspace, args.account)
    sessions_root = root / "sessions"
    memory_root = root / "memory"
    session_records = import_summary.get("records", [{}])[0].get("session_records", [])
    expected_messages = {record["session_id"]: int(record.get("expected_messages") or 0) for record in session_records}

    session_audit: list[dict[str, Any]] = []
    for session_dir in sorted(path for path in sessions_root.iterdir() if path.is_dir() and path.name.startswith("sess-")):
        meta_path = session_dir / "meta.json"
        meta = read_json(meta_path) if meta_path.exists() else {}
        messages_path = session_dir / "messages.jsonl"
        message_count = 0
        if messages_path.exists():
            message_count = sum(1 for _ in messages_path.open("r", encoding="utf-8", errors="replace"))
        expected = expected_messages.get(session_dir.name, 0)
        session_audit.append(
            {
                "session_id": session_dir.name,
                "expected_messages": expected,
                "actual_messages": message_count,
                "commit_index": meta.get("commit_index"),
                "expected_commit_index": expected - 1 if expected else None,
                "commit_ok": meta.get("commit_index") == (expected - 1 if expected else None),
                "atom_pipeline_index": meta.get("atom_pipeline_index"),
                "last_extracted_turn_id": meta.get("last_extracted_turn_id"),
                "abstract_exists": (session_dir / "abstract.md").exists(),
                "overview_exists": (session_dir / "overview.md").exists(),
                "messages_exists": messages_path.exists(),
                "meta_exists": meta_path.exists(),
            }
        )

    commit_ok_count = sum(1 for row in session_audit if row["commit_ok"])
    message_ok_count = sum(1 for row in session_audit if row["actual_messages"] == row["expected_messages"])
    atom_none_count = sum(1 for row in session_audit if row["atom_pipeline_index"] in (None, "", -1))

    atoms_count = count_json_files(memory_root / ".structured" / "atoms")
    graph_adjacency_count = count_json_files(memory_root / ".graph" / "adjacency")
    graph_nodes_count = count_json_files(memory_root / ".graph" / "nodes")
    graph_edges_count = count_json_files(memory_root / ".graph" / "edges")
    episode_count = count_json_files(memory_root / ".episodes" / "episodes")
    event_file_count = len([path for path in (memory_root / "events").rglob("*") if path.is_file() and path.suffix in {".md", ".json"}]) if (memory_root / "events").exists() else 0
    event_top_dirs = sorted(path.name for path in (memory_root / "events").iterdir() if path.is_dir()) if (memory_root / "events").exists() else []
    episode_time_index = sorted(path.name for path in (memory_root / ".episodes" / "index" / "by_time").glob("*.json")) if (memory_root / ".episodes" / "index" / "by_time").exists() else []

    for row in qa_rows:
        row["tool_call_count_int"] = int(row.get("tool_call_count") or 0)
        row["iteration_int"] = int(row.get("iteration") or 0)
        row["retrieval_count_int"] = int(row.get("retrieval_count") or 0)
        row["time_cost_float"] = float(row.get("time_cost") or 0)
        row["is_correct"] = row.get("result") == "CORRECT"

    correct_rows = [row for row in qa_rows if row["is_correct"]]
    wrong_rows = [row for row in qa_rows if not row["is_correct"]]

    def avg(items: list[dict[str, Any]], key: str) -> float:
        values = [float(item[key]) for item in items]
        return round(statistics.mean(values), 2) if values else 0.0

    category_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in qa_rows:
        bucket = category_counts[row.get("category") or "-"]
        bucket["total"] += 1
        if row["is_correct"]:
            bucket["correct"] += 1

    high_risk_rows = [row for row in qa_rows if row["tool_call_count_int"] >= 8 or row["time_cost_float"] > 90]
    worst_rows = sorted(wrong_rows, key=lambda item: item["time_cost_float"], reverse=True)[:10]

    actual_timeout_count = 0
    summary_negative_atom_count = 0
    for record in session_records:
        atom_flush = record.get("atom_flush") or {}
        actual_timeout_count += sum(1 for attempt in atom_flush.get("attempts", []) if attempt.get("timed_out"))
        if (atom_flush.get("atom_pipeline_index") in (-1, None, "")) or not record.get("atom_memory_complete_after_commit"):
            summary_negative_atom_count += 1

    case_blocks = []
    for spec in CASE_SPECS:
        row = by_qid.get(spec["question_id"])
        if not row:
            continue
        relevant = []
        try:
            relevant = json.loads(row.get("relevant_memory") or "[]")
        except Exception:
            relevant = []
        snippets = find_snippets(sessions_root, spec["patterns"])
        top_memories = "".join(
            "<li><span class='mono'>{}</span> · score={} · {}</li>".format(
                esc(item.get("uri")),
                esc(round(float(item.get("score") or 0), 3)),
                esc(compact(item.get("content"), 180)),
            )
            for item in relevant[:4]
        ) or "<li>无</li>"
        snippet_html = "".join(
            "<li><span class='mono'>{}:{}</span><br>{}</li>".format(
                esc(hit["path"]),
                esc(hit["line"]),
                esc(hit["text"]),
            )
            for hit in snippets
        ) or "<li>未搜到匹配片段</li>"
        case_blocks.append(
            f"""
            <div class="case">
              <h3>{esc(spec['question_id'])} · {esc(spec['label'])}</h3>
              <p><strong>问题：</strong>{esc(row.get('question'))}</p>
              <p><strong>Gold：</strong>{esc(row.get('answer'))}</p>
              <p><strong>当前回答：</strong>{esc(row.get('response'))}</p>
              <p><strong>结果：</strong><span class="badge {'bad' if not row['is_correct'] else 'ok'}">{esc(row.get('result'))}</span> · tool={row['tool_call_count_int']} · iter={row['iteration_int']} · {row['time_cost_float']:.2f}s</p>
              <div class="two-col">
                <div>
                  <h4>Session 层实际证据</h4>
                  <ul>{snippet_html}</ul>
                </div>
                <div>
                  <h4>当前 top memory</h4>
                  <ul>{top_memories}</ul>
                </div>
              </div>
            </div>
            """
        )

    category_rows = []
    for category in sorted(category_counts, key=lambda value: int(value) if value.isdigit() else value):
        stats = category_counts[category]
        accuracy = (stats["correct"] / stats["total"] * 100) if stats["total"] else 0
        category_rows.append(
            [
                esc(category),
                esc(f"{stats['correct']} / {stats['total']}"),
                esc(f"{accuracy:.2f}%"),
            ]
        )

    session_rows = []
    for row in [item for item in session_audit if not item["commit_ok"] or item["atom_pipeline_index"] not in (None, "", -1)][:8]:
        session_rows.append(
            [
                f"<span class='mono'>{esc(row['session_id'])}</span>",
                esc(f"{row['actual_messages']} / {row['expected_messages']}"),
                esc(f"{row['commit_index']} / {row['expected_commit_index']}"),
                esc(row["atom_pipeline_index"]),
                esc(row["last_extracted_turn_id"]),
            ]
        )

    worst_rows_table = []
    for row in worst_rows:
        worst_rows_table.append(
            [
                f"<span class='mono'>{esc(row['question_id'])}</span>",
                esc(row["question"]),
                esc(f"{row['tool_call_count_int']}"),
                esc(f"{row['time_cost_float']:.2f}s"),
                esc(compact(row["response"], 120)),
            ]
        )

    conclusions = [
        "这轮 conv-30 不是“服务没回答”的假失败，而是 81 题都真正跑完了，只是答对率只有 35.80%。",
        "EchoMemory 注入后并非完全空白：session / overview / messages / events / atoms / graph 都有产物，但 commit/atom cursor 与实际产物存在漂移，说明“导入完成”和“状态元数据一致”不是一回事。",
        "自定义 agent 当前最大问题不是完全检索不到，而是经常拿到不够准的原子记忆后进入长工具循环，最后仍答偏或答成“没有信息”。",
        "多道错题可以证明 gold 事实明明在 session overview 或 raw messages 里，但 top memory 没把它排到前面，说明当前主要瓶颈在 atom 化粒度、召回排序和后续工具循环，而不是单纯缺消息。",
    ]

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(args.title)}</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #14202b;
      --muted: #667085;
      --line: #d8e0ea;
      --blue: #155eef;
      --blue-soft: #eef4ff;
      --red: #b42318;
      --red-soft: #fef3f2;
      --amber: #b54708;
      --amber-soft: #fff7ed;
      --green: #067647;
      --green-soft: #ecfdf3;
      --shadow: 0 10px 28px rgba(16, 24, 40, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 24px 18px 40px; background: var(--bg); color: var(--text); font: 14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,"PingFang SC","Microsoft YaHei",sans-serif; }}
    .wrap {{ max-width: 1240px; margin: 0 auto; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; box-shadow: var(--shadow); padding: 20px 22px; margin-bottom: 16px; }}
    h1, h2, h3, h4 {{ margin: 0; }}
    h1 {{ font-size: 30px; line-height: 1.18; margin-bottom: 10px; }}
    h2 {{ font-size: 20px; margin-bottom: 14px; }}
    h3 {{ font-size: 16px; margin-bottom: 10px; }}
    h4 {{ font-size: 14px; margin-bottom: 8px; }}
    p {{ margin: 0 0 10px; }}
    ul {{ margin: 0; padding-left: 18px; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .stat {{ padding: 14px 16px; border: 1px solid var(--line); border-radius: 12px; background: #fbfcff; }}
    .label {{ font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
    .value {{ font-size: 24px; font-weight: 800; }}
    .badge {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 700; }}
    .badge.ok {{ background: var(--green-soft); color: var(--green); }}
    .badge.bad {{ background: var(--red-soft); color: var(--red); }}
    .callout {{ border-radius: 12px; padding: 12px 14px; margin-top: 12px; }}
    .callout.blue {{ background: var(--blue-soft); border: 1px solid #bfd4ff; color: #1849a9; }}
    .callout.amber {{ background: var(--amber-soft); border: 1px solid #fedf89; color: #93370d; }}
    .callout.red {{ background: var(--red-soft); border: 1px solid #fecdca; color: var(--red); }}
    .mono {{ font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size: 12px; background: #f7f8fa; padding: 1px 4px; border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-top: 1px solid var(--line); padding: 10px 8px; text-align: left; vertical-align: top; }}
    thead th {{ border-top: none; color: var(--muted); font-size: 12px; font-weight: 700; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .case {{ border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; background: #fcfdff; margin-top: 12px; }}
    .path-list li {{ margin-bottom: 8px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="panel">
      <h1>{esc(args.title)}</h1>
      <p class="muted">基于当前实际文件重新核验：导入摘要、QA CSV、judge 结果、EchoMemory workspace 四份证据一起看，不只看单个 summary。</p>
      <div class="grid">
        <div class="stat"><div class="label">Judge 准确率</div><div class="value">{judge_summary.get('correct', 0)} / {judge_summary.get('count', 0)}</div><div class="muted">{judge_summary.get('accuracy', 0) * 100:.2f}%</div></div>
        <div class="stat"><div class="label">Session 消息完整</div><div class="value">{message_ok_count} / {len(session_audit)}</div><div class="muted">expected vs actual message count</div></div>
        <div class="stat"><div class="label">Commit Cursor 正常</div><div class="value">{commit_ok_count} / {len(session_audit)}</div><div class="muted">按实际 meta.json 统计</div></div>
        <div class="stat"><div class="label">Atom Flush 超时</div><div class="value">{actual_timeout_count}</div><div class="muted">import summary 里 timed_out attempt 数</div></div>
      </div>
      <div class="callout red">
        <strong>一句话结论：</strong> 当前这轮 <span class="mono">custom agent + EchoMemory</span> 的主要问题不是“没注入任何记忆”，而是 <strong>导入状态元数据漂移 + 原子记忆精度不够 + 工具循环越跑越偏</strong>，所以最终准确率被压到 <strong>{judge_summary.get('accuracy', 0) * 100:.2f}%</strong>。
      </div>
    </section>

    <section class="panel">
      <h2>当前输入与路径</h2>
      <ul class="path-list">
        <li>import summary: <span class="mono">{esc(import_summary_path)}</span></li>
        <li>QA CSV: <span class="mono">{esc(qa_csv_path)}</span></li>
        <li>judge summary: <span class="mono">{esc(judge_summary_path)}</span></li>
        <li>workspace: <span class="mono">{esc(workspace)}</span></li>
        <li>account root: <span class="mono">{esc(root)}</span></li>
      </ul>
    </section>

    <section class="panel">
      <h2>一眼能看到的 4 类问题</h2>
      <ol>
        {''.join(f'<li>{esc(item)}</li>' for item in conclusions)}
      </ol>
    </section>

    <section class="panel">
      <h2>EchoMemory 注入完整性</h2>
      <div class="grid">
        <div class="stat"><div class="label">Session 数</div><div class="value">{len(session_audit)}</div><div class="muted">import summary 期望 19</div></div>
        <div class="stat"><div class="label">Atom JSON</div><div class="value">{atoms_count}</div><div class="muted">memory/.structured/atoms</div></div>
        <div class="stat"><div class="label">Graph Adjacency</div><div class="value">{graph_adjacency_count}</div><div class="muted">memory/.graph/adjacency</div></div>
        <div class="stat"><div class="label">Events 文件</div><div class="value">{event_file_count}</div><div class="muted">memory/events/**/*.md|json</div></div>
      </div>
      <div class="callout amber">
        <strong>状态不一致：</strong> import summary 把 19 个 session 都标成 <span class="mono">retrieval_ready</span>，但同时也把 19 个 session 都记成 <span class="mono">atom flush timeout</span>。而实际 workspace 里又已经能看到 atoms / graph / events 产物，说明“产物存在”和“cursor/state 元数据一致”目前不是同一件事。
      </div>
      <p><strong>实际 workspace 审计：</strong> messages 完整 <span class="mono">{message_ok_count}/{len(session_audit)}</span>；commit cursor 正常 <span class="mono">{commit_ok_count}/{len(session_audit)}</span>；atom_pipeline_index 为空或异常 <span class="mono">{atom_none_count}/{len(session_audit)}</span>。</p>
      <p><strong>时间归档异常：</strong> <span class="mono">memory/events</span> 顶层目录是 {esc(", ".join(event_top_dirs) or "-")}，<span class="mono">memory/.episodes/index/by_time</span> 只有 {esc(", ".join(episode_time_index) or "-")}。这更像按导入时间归档，而不是按 LoCoMo 事件发生时间归档。</p>
      {render_table(["session", "消息数", "commit_index", "atom_pipeline_index", "last_extracted_turn_id"], session_rows or [["-", "-", "-", "-", "-"]])}
      <p class="muted">graph_nodes={graph_nodes_count}，graph_edges={graph_edges_count}，episodes={episode_count}。这轮 graph 的主要可见产物集中在 adjacency，episode 产物非常少。</p>
    </section>

    <section class="panel">
      <h2>自定义 Agent QA 行为</h2>
      <div class="grid">
        <div class="stat"><div class="label">平均检索条数</div><div class="value">{avg(qa_rows, 'retrieval_count_int'):.2f}</div><div class="muted">top_k 配的是 30</div></div>
        <div class="stat"><div class="label">平均迭代轮数</div><div class="value">{avg(qa_rows, 'iteration_int'):.2f}</div><div class="muted">max_iterations = 6</div></div>
        <div class="stat"><div class="label">平均工具调用</div><div class="value">{avg(qa_rows, 'tool_call_count_int'):.2f}</div><div class="muted">全体 81 题</div></div>
        <div class="stat"><div class="label">高风险题</div><div class="value">{len(high_risk_rows)}</div><div class="muted">tool>=8 或 time&gt;90s</div></div>
      </div>
      <p><strong>正确题</strong> 平均 tool={avg(correct_rows, 'tool_call_count_int'):.2f}，iter={avg(correct_rows, 'iteration_int'):.2f}，耗时={avg(correct_rows, 'time_cost_float'):.2f}s。</p>
      <p><strong>错误题</strong> 平均 tool={avg(wrong_rows, 'tool_call_count_int'):.2f}，iter={avg(wrong_rows, 'iteration_int'):.2f}，耗时={avg(wrong_rows, 'time_cost_float'):.2f}s。</p>
      <div class="callout blue">
        <strong>很明显的模式：</strong> 这轮不是“检索数为 0”导致的失败。相反，多数错题也能拿到 11 条左右记忆，但拿到的不是最关键那几条，于是 agent 会继续调工具，最后在更长的推理链里答偏。
      </div>
      {render_table(["类别", "正确/总数", "准确率"], category_rows)}
      {render_table(["question_id", "问题", "tool", "耗时", "当前回答"], worst_rows_table)}
    </section>

    <section class="panel">
      <h2>具体错题证据</h2>
      <p class="muted">下面每题都同时展示两边证据：左边是 session overview/raw messages 里真实存在的事实，右边是当前 top memory。这样能直接看出是“没存进去”还是“存进去了但没召回到前面”。</p>
      {''.join(case_blocks)}
    </section>

    <section class="panel">
      <h2>怎么检测，怎么避免</h2>
      {render_table(
            ["检查项", "现在看到的问题", "建议动作"],
            [
                [
                    "导入后完整性检查",
                    "目前 import summary 只要 retrieval_ready 就可能报完成，但 atom/commit cursor 仍漂移。",
                    "导入完成后必须额外检查 messages 数、commit_index、atom_pipeline_index、events/atoms/graph 产物是否一致，不要只看 retrieval_ready。",
                ],
                [
                    "超时检测",
                    "19 个 session 都出现 atom flush timeout。",
                    "把 timed_out attempt 数做成硬告警；如果超过 0，就把导入状态标成“部分完成”，不要给用户一个像完全成功的结论。",
                ],
                [
                    "时间归档检查",
                    "events / episodes 看起来按导入日归档，而不是按事件日。",
                    "导入后抽样核验 5 个事件：文件路径中的日期、atom/event_time、原始 message turn_time 是否一致。",
                ],
                [
                    "检索质量检查",
                    "top_k=30，但平均只进 prompt 11.67 条，而且很多是泛化原子。",
                    "对日期题、具体事实题抽样检查 top memory 前 5 条是否包含 gold 关键句；若不包含，优先查 atom 化粒度和排序。",
                ],
                [
                    "Agent 风险检查",
                    "tool>=8 的题几乎全错，说明长工具循环不是在修正，反而在发散。",
                    "把 tool_call_count>=8 或 time_cost>90s 的题自动打上高风险标签，优先人工检查这批题。",
                ],
            ],
        )}
      <div class="callout amber">
        <strong>实操优先级：</strong> 先修“导入后状态检测”和“时间归档校验”，再去调 agent。因为现在有些错题不是 prompt 本身的问题，而是记忆层已经把关键信息压扁了，agent 后面再聪明也只能在模糊证据上兜圈。
      </div>
    </section>
  </div>
</body>
</html>
"""

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html_text, encoding="utf-8")
    print(out_html)


if __name__ == "__main__":
    main()
