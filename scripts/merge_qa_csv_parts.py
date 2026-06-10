#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge split QA CSV files into one CSV.")
    parser.add_argument("--inputs", nargs="+", required=True, help="Input CSV files in any order.")
    parser.add_argument("--output", required=True, help="Merged output CSV path.")
    parser.add_argument(
        "--order-from",
        default="",
        help="Optional reference CSV whose question_id order should be preserved.",
    )
    args = parser.parse_args()

    input_paths = [Path(item).expanduser().resolve() for item in args.inputs]
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    merged: dict[str, dict[str, str]] = {}
    fieldnames: list[str] | None = None
    for path in input_paths:
        rows = load_rows(path)
        if rows and fieldnames is None:
            fieldnames = list(rows[0].keys())
        for row in rows:
            qid = str(row.get("question_id") or "").strip()
            key = qid or f"__row_{len(merged)}"
            merged[key] = row

    if fieldnames is None:
        raise SystemExit("No rows found in input CSV files.")

    ordered_keys: list[str]
    if args.order_from:
        ref_rows = load_rows(Path(args.order_from).expanduser().resolve())
        seen = set()
        ordered_keys = []
        for row in ref_rows:
            qid = str(row.get("question_id") or "").strip()
            if qid and qid in merged and qid not in seen:
                ordered_keys.append(qid)
                seen.add(qid)
        for key in merged:
            if key not in seen:
                ordered_keys.append(key)
    else:
        ordered_keys = sorted(merged)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for key in ordered_keys:
            writer.writerow(merged[key])

    print(f"[merge] wrote {len(ordered_keys)} rows -> {output_path}")


if __name__ == "__main__":
    main()
