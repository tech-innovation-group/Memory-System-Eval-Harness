#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"succeeded", "failed", "done", "cancelled", "canceled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously summarize a running benchmark CSV into a small JSON file.")
    parser.add_argument("--csv", required=True, help="Benchmark CSV path")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--manifest", default="", help="Optional manifest.json path for terminal status detection")
    parser.add_argument("--interval", type=float, default=10.0, help="Polling interval in seconds")
    parser.add_argument("--idle-exit-rounds", type=int, default=12, help="Stop after this many unchanged rounds once manifest is terminal")
    return parser.parse_args()


def safe_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def compute_summary(csv_path: Path) -> dict[str, Any]:
    rows = 0
    sums = {
        "memory_injection_time_s": 0.0,
        "memory_settle_wait_elapsed_s": 0.0,
        "qa_time_s": 0.0,
        "end_to_end_time_s": 0.0,
    }
    counts = {key: 0 for key in sums}
    last_question_id = ""
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows += 1
                last_question_id = str(row.get("question_id") or "")
                for key in sums:
                    value = safe_float(row.get(key))
                    if value is None:
                        continue
                    sums[key] += value
                    counts[key] += 1
    def avg(key: str) -> float | None:
        count = counts[key]
        return round(sums[key] / count, 4) if count else None
    def total(key: str) -> float | None:
        return round(sums[key], 4) if counts[key] else None
    return {
        "rows": rows,
        "last_question_id": last_question_id,
        "total_memory_injection_time_s": total("memory_injection_time_s"),
        "avg_memory_injection_time_s": avg("memory_injection_time_s"),
        "total_memory_settle_wait_time_s": total("memory_settle_wait_elapsed_s"),
        "avg_memory_settle_wait_time_s": avg("memory_settle_wait_elapsed_s"),
        "total_qa_time_s": total("qa_time_s"),
        "avg_qa_time_s": avg("qa_time_s"),
        "total_end_to_end_time_s": total("end_to_end_time_s"),
        "avg_end_to_end_time_s": avg("end_to_end_time_s"),
    }


def manifest_status(manifest_path: Path | None) -> str:
    if not manifest_path or not manifest_path.exists():
        return ""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("status") or "").strip().lower()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else None
    unchanged_rounds = 0
    previous_rows = -1
    while True:
        summary = compute_summary(csv_path)
        status = manifest_status(manifest_path)
        payload = {
            **summary,
            "status": status or "running",
            "csv_path": str(csv_path),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        write_json_atomic(out_path, payload)
        rows = int(summary.get("rows") or 0)
        unchanged_rounds = unchanged_rounds + 1 if rows == previous_rows else 0
        previous_rows = rows
        if status in TERMINAL_STATUSES and unchanged_rounds >= max(1, int(args.idle_exit_rounds)):
            return 0
        time.sleep(max(1.0, float(args.interval or 10.0)))


if __name__ == "__main__":
    raise SystemExit(main())
