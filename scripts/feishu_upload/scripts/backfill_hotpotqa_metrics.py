#!/usr/bin/env python3
"""Backfill strict_blackbox.metrics into hotpotqa result directories.

hotpotqa 的 summary.json 不写 strict_blackbox.metrics（locomo 才有）。本脚本读取
每个结果目录的 qa_results.csv，按 locomo 同款公式聚合延迟/token/健康度指标，
写回 summary.json 的 strict_blackbox.metrics 块，使结果文件自包含、与 locomo 对齐。

不重跑注入记忆和 QA，纯离线计算。幂等：已存在 strict_blackbox.metrics 的目录跳过。

Usage:
    python backfill_hotpotqa_metrics.py <result_dir> [<result_dir> ...]
    python backfill_hotpotqa_metrics.py --all    # benchmarks/hotpotqa/results 下全部目录
"""

import json
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# extract_eval_result 在 import 时会把 stdout 切到 UTF-8
from extract_eval_result import _hotpotqa_metrics, _load_qa_results  # noqa: E402


def _round_deep(value):
    """Round all floats to 4 decimals (locomo stores metrics rounded to 4)."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: _round_deep(item) for key, item in value.items()}
    return value


def backfill(result_dir: str) -> str:
    summary_path = os.path.join(result_dir, "summary.json")
    if not os.path.isfile(summary_path):
        return "no summary.json"
    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)

    blackbox = summary.get("strict_blackbox") or {}
    if blackbox.get("metrics"):
        return "already has strict_blackbox.metrics (skip)"
    if summary.get("benchmark") != "hotpotqa":
        return "not hotpotqa (skip)"
    rows = _load_qa_results(result_dir)
    if not rows:
        return "no qa_results.csv (skip)"

    summary["strict_blackbox"] = blackbox
    blackbox["metrics"] = _round_deep(_hotpotqa_metrics(rows, summary))
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return "backfilled"


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    if "--all" in args:
        base = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "..", "benchmarks", "hotpotqa", "results",
        )
        dirs = sorted(
            os.path.join(base, name)
            for name in os.listdir(base)
            if os.path.isdir(os.path.join(base, name))
        )
    else:
        dirs = args

    for result_dir in dirs:
        if not os.path.isdir(result_dir):
            print(f"SKIP  {result_dir} (not a directory)", file=sys.stderr)
            continue
        print(f"{os.path.basename(result_dir)}: {backfill(result_dir)}")


if __name__ == "__main__":
    main()
