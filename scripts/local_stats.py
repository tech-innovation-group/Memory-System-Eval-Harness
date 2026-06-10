#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize local memory benchmark CSV results.")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    path = Path(args.input).expanduser().resolve()
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    correct = sum(1 for r in rows if (r.get("result") or r.get("simple_grade") or "").upper() in {"CORRECT", "MATCH"})
    wrong = sum(1 for r in rows if (r.get("result") or "").upper() == "WRONG")
    graded = correct + wrong
    pending = len(rows) - graded
    tokens = sum(int(float(r.get("injection_tokens_est") or 0)) for r in rows)
    summary = {
        "rows": len(rows),
        "graded": graded,
        "correct": correct,
        "wrong": wrong,
        "pending": pending,
        "accuracy": correct / graded if graded else None,
        "total_injection_tokens_est": tokens,
        "avg_injection_tokens_est": round(tokens / len(rows), 1) if rows else None,
    }
    out = path.parent / "summary.txt"
    lines = [
        "=== Local Result Statistics ===",
        f"Rows: {summary['rows']}",
        f"Graded: {summary['graded']}",
        f"Correct: {summary['correct']}",
        f"Wrong: {summary['wrong']}",
        f"Pending Judge: {summary['pending']}",
        f"Accuracy: {summary['accuracy'] * 100:.2f}%" if summary["accuracy"] is not None else "Accuracy: pending judge",
        f"Total context tokens est: {summary['total_injection_tokens_est']}",
        f"Avg context tokens est: {summary['avg_injection_tokens_est']}",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
