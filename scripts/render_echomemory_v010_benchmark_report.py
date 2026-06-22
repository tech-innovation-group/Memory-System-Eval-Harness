#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated-reports" / "echomemory_v010_longmemeval_evolvingevents_20260615.html"

LONGMEM_RUN = ROOT / "runs" / "echomemory_generic_qa_20260615_170910_14cee6"
EVOLVING_RUN = ROOT / "runs" / "echomemory_generic_qa_20260615_171127_b1592a"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def pct(value: object) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return "-"


def status_card(title: str, body: str, accent: str = "") -> str:
    return f"""
      <section class="card {accent}">
        <h3>{esc(title)}</h3>
        {body}
      </section>
    """


def render_longmem(longmem_manifest: dict, rows: list[dict[str, str]]) -> str:
    latest = rows[-1] if rows else {}
    row_html = "".join(
        f"<tr><td>{esc(r.get('question_id'))}</td><td>{esc(r.get('question'))}</td><td>{esc(r.get('response'))}</td><td>{esc(r.get('import_status'))}</td></tr>"
        for r in rows
    )
    return status_card(
        "LongMemEval 10题实跑",
        f"""
        <p>运行：<code>{esc(longmem_manifest.get("id"))}</code></p>
        <p>状态：<strong>{esc(longmem_manifest.get("status"))}</strong> · 已出题 <strong>{len(rows)}/10</strong></p>
        <p>最新：<code>{esc(latest.get("question_id"))}</code> - {esc(latest.get("response"))}</p>
        <p class="small">当前每条样本都在走真实 EchoMemory 导入与后台抽取；日志里能看到多次 <code>Rate limited by openai</code>，所以整体节奏偏慢。</p>
        <table>
          <thead><tr><th>题目</th><th>问题</th><th>回答</th><th>导入状态</th></tr></thead>
          <tbody>{row_html}</tbody>
        </table>
        """,
        "warn" if longmem_manifest.get("status") == "running" else "good",
    )


def render_evolving(ev_summary: dict) -> str:
    judge = (ev_summary.get("judge") or {}).get("summary") or {}
    return status_card(
        "EvolvingEvents sample",
        f"""
        <p>运行：<code>{esc(ev_summary.get("namespace") or EVOLVING_RUN.name)}</code></p>
        <p>结果：<strong>{esc(ev_summary.get("correct"))}/{esc(ev_summary.get("rows"))}</strong> · <strong>{pct(ev_summary.get("accuracy"))}</strong></p>
        <p>Judge：<strong>{esc(judge.get("accuracy"))}</strong> · <code>{esc(judge.get("judge_model"))}</code></p>
        <p class="small">仓内目前只注册了 sample；full 版本还缺 `dataset/full/evolvingevents.json`。</p>
        """,
        "good",
    )


def render() -> str:
    longmem_manifest = read_json(LONGMEM_RUN / "manifest.json")
    longmem_rows = read_csv(LONGMEM_RUN / "echomemory_generic_qa" / "echomemory_generic_qa_results.csv")
    evolving_summary = read_json(EVOLVING_RUN / "echomemory_generic_qa" / "summary.json")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EchoMemory 0.1.0 LongMemEval / EvolvingEvents 实测进展</title>
  <style>
    :root {{ --bg:#f4f7fb; --panel:#fff; --line:#d9e1ea; --ink:#1c2430; --muted:#5b6675; --blue:#225b96; --good:#116b47; --warn:#a35d08; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
    header {{ padding:28px 36px; background:#10253a; color:#fff; }}
    main {{ max-width:1200px; margin:0 auto; padding:22px 36px 40px; }}
    h1 {{ margin:0 0 6px; font-size:28px; }}
    h2 {{ margin:26px 0 10px; font-size:20px; }}
    h3 {{ margin:0 0 10px; font-size:16px; }}
    p {{ margin:6px 0; }}
    code {{ font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; word-break:break-all; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .good {{ border-left:4px solid var(--good); }}
    .warn {{ border-left:4px solid var(--warn); }}
    .note {{ background:#eef5fc; border-left:4px solid var(--blue); padding:10px 12px; border-radius:6px; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; margin-top:12px; }}
    th,td {{ border-bottom:1px solid var(--line); padding:10px 11px; text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#edf3f8; color:#31465a; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    tr:last-child td {{ border-bottom:0; }}
    .small {{ color:var(--muted); font-size:13px; }}
    @media (max-width:900px) {{ header,main {{ padding-left:16px; padding-right:16px; }} .grid {{ grid-template-columns:1fr; }} table {{ display:block; overflow-x:auto; }} }}
  </style>
</head>
<body>
  <header>
    <div class="small">生成时间：{esc(datetime.now().isoformat(timespec="seconds"))}</div>
    <h1>EchoMemory 0.1.0 LongMemEval / EvolvingEvents 实测进展</h1>
    <p class="small">当前进展先报给你。EvolvingEvents 已完成，LongMemEval 10 题正在继续跑。</p>
  </header>
  <main>
    <section class="note">
      <p>平台侧已确认：`19182` 这套新进程能接住 EchoMemory 0.1.0 的 generic QA 任务；`19181` 还是旧进程，之前会报 `unknown task kind`。</p>
    </section>
    <h2>当前结果</h2>
    <div class="grid">
      {render_longmem(longmem_manifest, longmem_rows)}
      {render_evolving(evolving_summary)}
    </div>
    <h2>LongMemEval 已完成题目</h2>
    <table>
      <thead><tr><th>ID</th><th>问题</th><th>回答</th><th>导入状态</th></tr></thead>
      <tbody>
        {''.join(f"<tr><td>{esc(r.get('question_id'))}</td><td>{esc(r.get('question'))}</td><td>{esc(r.get('response'))}</td><td>{esc(r.get('import_status'))}</td></tr>" for r in longmem_rows)}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(json.dumps({"out": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
