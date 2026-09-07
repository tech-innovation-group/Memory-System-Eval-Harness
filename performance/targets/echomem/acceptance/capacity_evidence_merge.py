"""Merge immutable M1 reports into one explicitly bounded publication input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _snapshots(report: dict, source: Path) -> list[dict]:
    rows = []
    for level in report.get("levels", []):
        hot_users = level.get("hot_users", level.get("identity_count"))
        if level.get("search"):
            rows.append({**level, "hot_users": hot_users, "evidence_source": str(source)})
            continue
        aggregates = [("pure_aggregate", False), ("mixed_aggregate", True)]
        emitted = False
        for key, mixed in aggregates:
            if level.get(key):
                rows.append({**level[key], "hot_users": hot_users, "mixed": mixed,
                             "evidence_source": str(source)})
                emitted = True
        if emitted:
            continue
        for repeat in level.get("repeats", []):
            for key, mixed in (("pure", False), ("mixed", True)):
                if repeat.get(key):
                    rows.append({**repeat[key], "hot_users": hot_users, "mixed": mixed,
                                 "repeat": repeat.get("repeat"),
                                 "evidence_source": str(source)})
    return rows


def merge(paths: list[Path], *, zero_error_level: int | None,
          first_nonzero_error: int | None) -> dict:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    levels = [row for report, path in zip(reports, paths) for row in _snapshots(report, path)]
    highest = max((row.get("hot_users", 0) for row in levels), default=None)
    boundary = {"status": "NOT_ESTABLISHED", "reason": "no-hard-failure-observed",
                "highest_tested_hot_users": highest}
    result = {
        "status": "MEASURED", "assessment_mode": "observe", "levels": levels,
        "max_hot_users": None, "dau": None, "hard_boundary": boundary,
        "operational_boundary": boundary, "highest_measured_hot_users": highest,
        "performance_requirements_applied": False,
        "evidence_sources": [str(path) for path in paths],
    }
    if zero_error_level is not None and first_nonzero_error is not None:
        result.update(
            zero_error_max_hot_users=zero_error_level,
            first_nonzero_error_hot_users=first_nonzero_error,
            boundary={"status": "ZERO_ERROR_CONFIRMED",
                      "highest_zero_error": zero_error_level,
                      "first_nonzero_error": first_nonzero_error,
                      "evidence": "three-fresh-identity-repeats",
                      "not_a_capacity_maximum": True},
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zero-error-level", type=int)
    parser.add_argument("--first-nonzero-error", type=int)
    args = parser.parse_args()
    value = merge(args.input, zero_error_level=args.zero_error_level,
                  first_nonzero_error=args.first_nonzero_error)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"levels": len(value["levels"]),
                      "highest_tested_hot_users": value["highest_measured_hot_users"]}))


if __name__ == "__main__":
    main()
