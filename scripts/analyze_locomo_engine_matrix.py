#!/usr/bin/env python3
"""Summarize a same-injection LoCoMo EchoMem engine matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path


VARIANTS = ("full", "atomic-only", "atomic-base")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def retrieval_count(row: dict[str, str]) -> int:
    try:
        items = json.loads(row.get("retrieval_items_json") or "[]")
    except json.JSONDecodeError:
        return 0
    return len(items) if isinstance(items, list) else 0


def result_dir(root: Path, prefix: str, variant: str) -> Path:
    candidates = sorted((root / f"{prefix}-{variant}").glob("*/summary.json"))
    if not candidates:
        raise FileNotFoundError(f"missing completed result for {variant}")
    return candidates[-1].parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--prefix", default="locomo-engine-matrix")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    runs = {v: result_dir(args.results_root, args.prefix, v) for v in VARIANTS}
    payload: dict = {"runs": {}, "same_memory": {}, "pairwise": {}}
    session_maps = {}
    verdicts = {}
    counts = {}
    for variant, directory in runs.items():
        summary = read_json(directory / "summary.json")
        qa_rows = read_rows(directory / "qa_results.csv")
        judge_rows = {
            row["question_id"]: row
            for row in read_rows(directory / "judge_results.csv")
        }
        imports = read_rows(directory / "import_results.csv")
        session_maps[variant] = [
            (r.get("sample_id"), r.get("session_key"), r.get("session_id"))
            for r in imports
        ]
        counts[variant] = {
            r["question_id"]: retrieval_count(r) for r in qa_rows
        }
        verdicts[variant] = {
            qid: str(row.get("verdict") or "")
            for qid, row in judge_rows.items()
        }
        values = list(counts[variant].values())
        payload["runs"][variant] = {
            "directory": str(directory),
            "accuracy": summary.get("accuracy"),
            "correct": summary.get("judge_correct"),
            "wrong": summary.get("judge_wrong"),
            "judge_errors": summary.get("judge_errors"),
            "import_ok": summary.get("import_ok"),
            "import_total": summary.get("import_total"),
            "memory_identity": summary.get("memory_identity"),
            "retrieval_count": {
                "average": statistics.mean(values),
                "p50": statistics.median(values),
                "min": min(values),
                "max": max(values),
                "empty": sum(value == 0 for value in values),
            },
        }

    payload["same_memory"] = {
        "session_mapping_equal": len({repr(v) for v in session_maps.values()}) == 1,
        "session_mapping_sha256": {
            variant: hashlib.sha256(
                repr(session_maps[variant]).encode()
            ).hexdigest()
            for variant in VARIANTS
        },
        "identity_equal": len({
            json.dumps(
                payload["runs"][variant]["memory_identity"],
                sort_keys=True,
            )
            for variant in VARIANTS
        }) == 1,
    }

    base = "full"
    for variant in ("atomic-only", "atomic-base"):
        deltas = [
            counts[variant][qid] - counts[base][qid]
            for qid in counts[base]
        ]
        changed = [delta for delta in deltas if delta]
        transitions = []
        for qid in verdicts[base]:
            left = verdicts[base].get(qid, "")
            right = verdicts[variant].get(qid, "")
            if left != right:
                transitions.append({
                    "question_id": qid,
                    "from": left,
                    "to": right,
                    "full_retrieval_count": counts[base].get(qid),
                    "variant_retrieval_count": counts[variant].get(qid),
                })
        payload["pairwise"][variant] = {
            "retrieval_count_changed": len(changed),
            "retrieval_count_delta_average": statistics.mean(deltas),
            "retrieval_count_delta_min": min(deltas),
            "retrieval_count_delta_max": max(deltas),
            "verdict_changed": len(transitions),
            "verdict_transitions": transitions,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
