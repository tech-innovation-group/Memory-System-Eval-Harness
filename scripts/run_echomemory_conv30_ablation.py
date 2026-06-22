#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_RUN = ROOT / "runs" / "echomemory_v010_conv30_eval_20260615_123200"
DEFAULT_ECHOMEM_ROOT = Path("/Users/chx/Code/echomemory/echo_memory_v010")
DEFAULT_OV_CONF = Path("/Users/chx/.openviking/ov.conf")


ABLATIONS: list[dict[str, Any]] = [
    {
        "slug": "baseline_vikingboat_lite",
        "label": "Baseline",
        "description": "vikingboat_lite + tool loop + prefetch + overview enrichment",
        "prompt_mode": "vikingboat_lite",
        "tool_loop": True,
        "prefetch": True,
        "overview_enrichment": True,
    },
    {
        "slug": "no_prefetch",
        "label": "No Prefetch",
        "description": "baseline - initial tool prefetch",
        "prompt_mode": "vikingboat_lite",
        "tool_loop": True,
        "prefetch": False,
        "overview_enrichment": True,
    },
    {
        "slug": "no_tool_loop",
        "label": "No Tool Loop",
        "description": "baseline - tool loop",
        "prompt_mode": "vikingboat_lite",
        "tool_loop": False,
        "prefetch": True,
        "overview_enrichment": True,
    },
    {
        "slug": "one_shot_no_overview",
        "label": "One Shot / No Overview",
        "description": "one_shot + no prefetch + no tool loop + no overview enrichment",
        "prompt_mode": "one_shot",
        "tool_loop": False,
        "prefetch": False,
        "overview_enrichment": False,
    },
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_cmd(cmd: list[str], *, env: dict[str, str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True)


def load_model_config(path: Path) -> dict[str, str]:
    data = read_json(path)
    vlm = data.get("vlm") or {}
    return {
        "api_key": str(vlm.get("api_key") or "").strip(),
        "base_url": str(vlm.get("api_base") or "").strip(),
        "model": str(vlm.get("model") or "").strip(),
    }


def count_correct(csv_path: Path) -> tuple[int, int]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    total = len(rows)
    correct = sum(1 for row in rows if str(row.get("result") or "").strip().upper() == "CORRECT")
    return correct, total


def reuse_existing_run(base_run_dir: Path, out_dir: Path) -> dict[str, Any]:
    csv_path = base_run_dir / "qa_merged" / "echomemory_memory_qa_results.csv"
    judge_path = base_run_dir / "qa_merged" / "judge_summary.json"
    shard_summary = read_json(base_run_dir / "qa_shard1" / "summary.json")
    judge_summary = read_json(judge_path)
    correct, total = count_correct(csv_path)
    return {
        "slug": "baseline_vikingboat_lite",
        "label": "Baseline",
        "description": "reused existing formal run: vikingboat_lite + tool loop + prefetch + overview enrichment",
        "run_dir": str(base_run_dir),
        "correct": correct,
        "total": total,
        "summary": {
            "prompt_mode": shard_summary.get("prompt_mode"),
            "memory_tool_loop_enabled": shard_summary.get("memory_tool_loop_enabled"),
            "initial_tool_prefetch_enabled": shard_summary.get("initial_tool_prefetch_enabled"),
            "search_overview_enrichment_enabled": True,
        },
        "judge_summary": judge_summary,
    }


def render_html(out_dir: Path, manifest: dict[str, Any]) -> Path:
    rows = manifest["runs"]
    table_rows = []
    for row in rows:
        judge = row.get("judge_summary") or {}
        summary = row.get("summary") or {}
        table_rows.append(
            f"""
            <tr>
              <td>{row['label']}</td>
              <td>{row['description']}</td>
              <td>{judge.get('correct', 0)}/{judge.get('count', 0)}</td>
              <td>{float(judge.get('accuracy') or 0) * 100:.2f}%</td>
              <td>{summary.get('prompt_mode', '-')}</td>
              <td>{summary.get('memory_tool_loop_enabled', '-')}</td>
              <td>{summary.get('initial_tool_prefetch_enabled', '-')}</td>
              <td>{summary.get('search_overview_enrichment_enabled', '-')}</td>
              <td><code>{row['run_dir']}</code></td>
            </tr>
            """
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EchoMemory conv30 Ablation</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin: 0; background: #ffffff; color: #111827; }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 28px 24px 40px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    p {{ margin: 6px 0; line-height: 1.6; }}
    .muted {{ color: #6b7280; font-size: 14px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; background: #fff; margin: 18px 0; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    code {{ font: 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; word-break: break-all; }}
  </style>
</head>
<body>
  <main>
    <h1>EchoMemory conv-30 对照消融</h1>
    <p class="muted">输出目录：<code>{out_dir}</code></p>
    <div class="card">
      <p>基线 run：<code>{manifest['base_run_dir']}</code></p>
      <p>复用 workspace：<code>{manifest['workspace']}</code></p>
      <p>复用 account：<code>{manifest['account']}</code></p>
      <p>Judge 对齐：<code>{manifest['judge_alignment']}</code></p>
    </div>
    <table>
      <thead>
        <tr>
          <th>实验</th>
          <th>说明</th>
          <th>Correct</th>
          <th>Accuracy</th>
          <th>Prompt</th>
          <th>Tool Loop</th>
          <th>Prefetch</th>
          <th>Overview Enrichment</th>
          <th>产物目录</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows)}
      </tbody>
    </table>
  </main>
</body>
</html>
"""
    html_path = out_dir / "ablation_report.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EchoMemory conv-30 ablation experiments against an existing imported workspace.")
    parser.add_argument("--base-run-dir", default=str(DEFAULT_BASE_RUN))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="conv-30")
    parser.add_argument("--dataset", default=str(ROOT / "dataset" / "locomo10.json"))
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--ov-conf", default=str(DEFAULT_OV_CONF))
    parser.add_argument("--python-bin", default=str(DEFAULT_ECHOMEM_ROOT / ".venv" / "bin" / "python"))
    parser.add_argument("--model-retries", type=int, default=2)
    parser.add_argument("--timeout-s", type=int, default=90)
    parser.add_argument("--dashscope-api-key", default="", help="Override DASHSCOPE_API_KEY used for embeddings.")
    parser.add_argument("--chat-api-key", default="", help="Override ECHOMEM_CHAT_API_KEY and answer token used for chat.")
    parser.add_argument("--answer-base-url", default="", help="Override answer/judge base URL.")
    parser.add_argument("--answer-model", default="", help="Override answer/judge model.")
    parser.add_argument("--reuse-baseline", action="store_true", help="Reuse the existing base run as baseline instead of re-running it.")
    args = parser.parse_args()

    base_run_dir = Path(args.base_run_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_judge = read_json(base_run_dir / "qa_merged" / "judge_summary.json")
    shard_summary = read_json(base_run_dir / "qa_shard1" / "summary.json")
    model_cfg = load_model_config(Path(args.ov_conf).expanduser().resolve())
    if not all(model_cfg.values()):
        raise SystemExit("ov.conf missing api_key/base_url/model")

    env = dict(os.environ)
    dashscope_api_key = str(args.dashscope_api_key or model_cfg["api_key"]).strip()
    chat_api_key = str(args.chat_api_key or model_cfg["api_key"]).strip()
    answer_base_url = str(args.answer_base_url or model_cfg["base_url"]).strip()
    answer_model = str(args.answer_model or model_cfg["model"]).strip()
    env["DASHSCOPE_API_KEY"] = dashscope_api_key
    env["ECHOMEM_CHAT_API_KEY"] = chat_api_key

    manifest: dict[str, Any] = {
        "base_run_dir": str(base_run_dir),
        "workspace": shard_summary["workspace"],
        "account": shard_summary["account"],
        "judge_alignment": merged_judge.get("judge_alignment"),
        "runs": [],
    }

    specs = list(ABLATIONS)
    if args.reuse_baseline:
        manifest["runs"].append(reuse_existing_run(base_run_dir, out_dir))
        specs = [spec for spec in specs if spec["slug"] != "baseline_vikingboat_lite"]

    for spec in specs:
        run_dir = out_dir / spec["slug"]
        qa_dir = run_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        qa_cmd = [
            args.python_bin,
            str(ROOT / "scripts" / "echomemory_memory_qa.py"),
            "--dataset",
            str(Path(args.dataset).expanduser().resolve()),
            "--out-dir",
            str(qa_dir),
            "--sample",
            args.sample,
            "--echomem-root",
            str(Path(args.echomem_root).expanduser().resolve()),
            "--echomem-config",
            str(base_run_dir / "qa_shard1" / "echomem.runtime.yaml"),
            "--workspace",
            str(shard_summary["workspace"]),
            "--account",
            str(shard_summary["account"]),
            "--user-id",
            "default",
            "--agent-id",
            "default",
            "--prompt-mode",
            spec["prompt_mode"],
            "--answer-base-url",
            answer_base_url,
            "--answer-model",
            answer_model,
            "--answer-token",
            chat_api_key,
            "--model-retries",
            str(args.model_retries),
            "--timeout-s",
            str(args.timeout_s),
        ]
        qa_cmd.append("--vikingboat-tool-loop" if spec["tool_loop"] else "--no-vikingboat-tool-loop")
        qa_cmd.append("--initial-tool-prefetch" if spec["prefetch"] else "--no-initial-tool-prefetch")
        qa_cmd.append("--search-overview-enrichment" if spec["overview_enrichment"] else "--no-search-overview-enrichment")
        run_cmd(qa_cmd, env=env, cwd=ROOT)

        judge_cmd = [
            args.python_bin,
            str(ROOT / "scripts" / "local_judge.py"),
            "--input",
            str(qa_dir / "echomemory_memory_qa_results.csv"),
            "--base-url",
            answer_base_url,
            "--model",
            answer_model,
            "--token",
            chat_api_key,
            "--parallel",
            "10",
            "--timeout-s",
            str(args.timeout_s),
            "--retries",
            str(args.model_retries),
        ]
        run_cmd(judge_cmd, env=env, cwd=ROOT)

        judge_summary = read_json(qa_dir / "judge_summary.json")
        summary = read_json(qa_dir / "summary.json")
        correct, total = count_correct(qa_dir / "echomemory_memory_qa_results.csv")
        manifest["runs"].append(
            {
                "slug": spec["slug"],
                "label": spec["label"],
                "description": spec["description"],
                "run_dir": str(run_dir),
                "correct": correct,
                "total": total,
                "summary": summary,
                "judge_summary": judge_summary,
            }
        )

    manifest_path = out_dir / "ablation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = render_html(out_dir, manifest)
    print(json.dumps({"manifest": str(manifest_path), "html": str(html_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
