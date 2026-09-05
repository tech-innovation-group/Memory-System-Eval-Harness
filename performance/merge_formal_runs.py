#!/usr/bin/env python3
"""Merge supplemental formal-suite runs into a canonical suite manifest.

This is intended for resumable 4U8G runs: a first invocation can finish the
early matrix and a later invocation can rerun only environment-error cases.
The supplemental runs replace matching ``pr421__<scenario>`` entries while
the original manifest, paths, and all non-replaced cases remain auditable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from performance.acceptance import build_model_analysis_input, evaluate_pr421_acceptance
from performance.formal_data_report import render


def _submitted_operations(run: dict[str, Any]) -> int:
    """Count real request samples recorded by a formal-suite run."""
    summary = run.get("summary")
    if not isinstance(summary, dict):
        return 0
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    total = 0
    for operation in ("search", "commit"):
        item = metrics.get(operation)
        if not isinstance(item, dict):
            continue
        try:
            total += max(0, int(item.get("submitted") or 0))
        except (TypeError, ValueError):
            continue
    return total


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _namespaced_key(run: dict[str, Any], default_prefix: str = "pr421") -> str:
    key = str(run.get("scenario_key") or "").strip()
    if key and "__" in key:
        return key
    scenario = str(run.get("source_scenario") or run.get("scenario") or "").strip()
    return f"{default_prefix}__{scenario or key}" if (scenario or key) else ""


def _raw_scenario_key(run: dict[str, Any]) -> str:
    """Return the un-namespaced scenario name used by legacy manifests."""
    return str(
        run.get("source_scenario")
        or run.get("scenario")
        or run.get("scenario_key")
        or ""
    ).strip()


def merge_manifests(base_path: Path, supplement_path: Path, output_dir: Path) -> Path:
    base = _read(base_path)
    supplement = _read(supplement_path)
    base_runs = list(base.get("runs") or [])
    supplemental_runs = list(supplement.get("runs") or [])

    replacements: dict[str, dict[str, Any]] = {}
    for run in supplemental_runs:
        key = _namespaced_key(run)
        if not key:
            continue
        item = dict(run)
        item["scenario_key"] = key
        item.setdefault("source_scenario", key.split("__", 1)[-1])
        item.setdefault("plan_source", key.split("__", 1)[0])
        item["scenario"] = item.get("source_scenario") or item.get("scenario")
        replacements[key] = item
        raw_key = _raw_scenario_key(run)
        if raw_key:
            replacements[raw_key] = item

    merged_runs: list[dict[str, Any]] = []
    replaced: list[str] = []
    for run in base_runs:
        key = _namespaced_key(run)
        raw_key = _raw_scenario_key(run)
        replacement = replacements.get(key) or replacements.get(raw_key)
        if replacement is not None:
            merged_runs.append(replacement)
            replaced.append(key)
        else:
            merged_runs.append(run)

    base["runs"] = merged_runs
    base["supplemental_merge"] = {
        "base_manifest": str(base_path.resolve()),
        "supplement_manifest": str(supplement_path.resolve()),
        "replaced_scenario_keys": replaced,
        "supplement_run_count": len(supplemental_runs),
    }
    expected = len(base.get("scenarios") or []) * int(base.get("repeats") or 1) * len(
        base.get("policies") or ["server-observe"]
    )
    statuses = [str(run.get("status") or "NO_SUMMARY").upper() for run in merged_runs]
    completed_run_count = sum(status == "COMPLETED" for status in statuses)
    evidence_run_count = sum(
        status == "COMPLETED" and _submitted_operations(run) > 0
        for status, run in zip(statuses, merged_runs)
    )
    acceptance = evaluate_pr421_acceptance(base)
    base["acceptance"] = acceptance
    base["finalization"] = {
        "status": "completed",
        "finished_at": base.get("finished_at") or supplement.get("finished_at"),
        "reason": "",
        "run_count": len(merged_runs),
        "expected_run_count": expected,
        "completed_run_count": completed_run_count,
        "evidence_run_count": evidence_run_count,
        "empty_completed_run_count": completed_run_count - evidence_run_count,
        "failed_run_count": sum(
            status in {"FAIL", "HARNESS_ERROR", "NO_SUMMARY"} for status in statuses
        ),
        "timeout_run_count": sum(status == "TIMEOUT" for status in statuses),
        "blocked_run_count": sum(status in {"BLOCKED", "ENVIRONMENT_ERROR"} for status in statuses),
        "coverage_status": (
            "complete"
            if len(merged_runs) >= expected and evidence_run_count >= expected
            else "partial"
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "suite.json"
    manifest_path.write_text(
        json.dumps(base, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "acceptance.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "model_analysis_input.json").write_text(
        json.dumps(build_model_analysis_input(base, acceptance), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    render(manifest_path, output_dir / "suite.html")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--supplement", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(merge_manifests(args.base, args.supplement, args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
