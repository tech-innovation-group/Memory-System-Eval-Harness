"""Publish a redacted M1 HTML/JSON report from server-side raw evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from performance.targets.echomem.acceptance.capacity_confirmation import recompute_confirmation
from performance.targets.echomem.acceptance.capacity_report import render
from performance.targets.echomem.acceptance.capacity_statistics import evaluate_level


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def seed_summary(root: Path) -> dict:
    paths = [path for path in sorted(root.glob("level-*-repeat-*/seed-evidence.json"))
             if (path.parent / "pure-measurement.json").exists()
             and (path.parent / "mixed-measurement.json").exists()]
    actors = [actor for path in paths for actor in _read(path).get("actors", [])]
    queries = [query for actor in actors for query in actor.get("queries", [])]
    return {
        "evidence_files": len(paths),
        "actors": len(actors),
        "queries": len(queries),
        "strict_valid": sum(bool(query.get("success")) for query in queries),
        "fact_hits": sum(bool(query.get("matched_expected_fact")) for query in queries),
        "degraded": sum(bool(query.get("degraded")) for query in queries),
        "empty": sum(query.get("hit_count") == 0 for query in queries),
        "input_documents": sum(actor.get("input_documents", 0) for actor in actors),
        "input_characters": sum(actor.get("input_characters", 0) for actor in actors),
        "all_actors_passed": bool(actors) and all(actor.get("status") == "PASS" for actor in actors),
    }


def redacted_resources(root: Path, *, max_points: int = 180) -> tuple[list[dict], dict]:
    rows = []
    for path in sorted(root.glob("level-*-repeat-*/*-resources.json")):
        for row in _read(path):
            rows.append({key: row.get(key) for key in
                         ("cpu_percent_one_core_100", "rss_bytes", "working_set_bytes", "pids")})
    step = max(1, len(rows) // max_points)
    sampled = rows[::step][:max_points]
    summary = {
        "samples": len(rows),
        "cpu_peak_percent_one_core_100": max((row.get("cpu_percent_one_core_100") or 0
                                              for row in rows), default=None),
        "rss_peak_bytes": max((row.get("rss_bytes") or 0 for row in rows), default=None),
        "working_set_peak_bytes": max((row.get("working_set_bytes") or 0 for row in rows), default=None),
        "pids_peak": max((row.get("pids") or 0 for row in rows), default=None),
    }
    return sampled, summary


def publish(*, confirmation: Path | None, output: Path, exploration: Path | None = None,
            calibration: Path | None = None, preflight: Path | None = None,
            observations: list[Path] | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    if confirmation:
        confirmation_mode = _read(confirmation / "report.json").get("assessment_mode", "observe")
        report = recompute_confirmation(confirmation, assessment_mode=confirmation_mode)
    else:
        report = {"assessment_mode": "observe", "levels": [], "manifest": {},
                  "max_hot_users": None, "dau": None, "status": "MEASURED",
                  "performance_requirements_applied": False}
    report["publication"] = {
        "redacted": True,
        "raw_requests_exported": False,
        "private_identities_exported": False,
        "derivation_source": "server-side immutable raw measurement files",
    }
    if confirmation:
        report["seed_summary"] = seed_summary(confirmation)
        report["resources"], report["resources_summary"] = redacted_resources(confirmation)
    for root in observations or []:
        value = _read(root / "report.json")
        report["active_current"] = value.get("current")
        report["manifest"] = value.get("manifest", {})
        report["seed_reused"] = value.get("seed_reused")
        actors = value.get("seed", {}).get("actors", [])
        queries = [q for actor in actors for q in actor.get("queries", [])]
        report["seed_summary"] = {"actors": len(actors), "queries": len(queries),
                                  "strict_valid": sum(bool(q.get("success")) for q in queries),
                                  "healthy_actors": sum(a.get("status") == "PASS" for a in actors)}
        report["latest_run_status"] = value.get("status")
        for source in value.get("levels", []):
            raw_path = root / source.get("measurement_file", f"level-{source['level']}-measurement.json")
            summary = evaluate_level(_read(raw_path))
            summary.update({key: source.get(key) for key in
                            ("hot_users", "users_per_tenant", "topology", "memory_scale", "level")})
            summary["platform_base_pr"] = value.get("manifest", {}).get("platform_base_pr")
            summary["platform_base_commit"] = value.get("manifest", {}).get("platform_base_commit")
            sample_rows = source.get("resources")
            if sample_rows is None:
                all_resources = _read(root / "resources.json") if (root / "resources.json").exists() else []
                label = "mixed-measurement" if summary["mixed"] else "pure-measurement"
                sample_rows = [r for r in all_resources if r.get("level") == source["level"]
                               and r.get("phase") == label]
            summary["resource_summary"] = {
                "samples": len(sample_rows),
                "cpu_peak_percent_one_core_100": max((r["cpu_percent_one_core_100"] for r in sample_rows
                                                       if r.get("cpu_percent_one_core_100") is not None), default=None),
                "rss_peak_bytes": max((r["rss_bytes"] for r in sample_rows if r.get("rss_bytes") is not None), default=None),
                "working_set_peak_bytes": max((r["working_set_bytes"] for r in sample_rows
                                                 if r.get("working_set_bytes") is not None), default=None),
            }
            if source.get("recovery"):
                summary["recovery"] = source["recovery"]
            report["levels"].append(summary)
        if value.get("operational_boundary"):
            report["operational_boundary"] = value["operational_boundary"]
    if not confirmation or report.get("assessment_mode") == "observe":
        report.update(assessment_mode="observe", max_hot_users=None, dau=None,
                      boundary={"status": "NOT_ESTABLISHED"}, performance_requirements_applied=False)
    if report.get("operational_boundary") and report.get("boundary", {}).get("status") != "CONFIRMED":
        report["boundary"] = {"status": "OBSERVED_FAILURE_STAGE",
                              "hot_users": report["operational_boundary"]["hot_users"]}
    samples = [l.get("resource_summary", {}) for l in report["levels"] if "search" in l]
    previous = report.get("resources_summary", {})
    report["resources_summary"] = {
        "samples": previous.get("samples", 0) + sum(r.get("samples", 0) for r in samples),
        **{key: max((r[key] for r in [previous, *samples] if r.get(key) is not None), default=None)
           for key in ("cpu_peak_percent_one_core_100", "rss_peak_bytes", "working_set_peak_bytes")},
    }
    if exploration:
        value = _read(exploration / "report.json")
        report["exploration"] = {key: value.get(key) for key in
                                 ("status", "topology", "levels", "boundary")}
    if calibration:
        value = _read(calibration)
        report["query_calibration"] = {
            "status": value.get("status"),
            "candidates": value.get("candidates", []),
        }
    if preflight:
        value = _read(preflight)
        report["providers"] = [{key: engine.get(key) for key in
                                ("kind", "model", "status", "code", "elapsed_s")}
                               for engine in value.get("engines", [])]
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.html").write_text(render(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--observation", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exploration", type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--preflight", type=Path)
    args = parser.parse_args()
    if not args.confirmation and not args.observation:
        parser.error("Provide --confirmation and/or --observation")
    result = publish(confirmation=args.confirmation, output=args.output,
                     exploration=args.exploration, calibration=args.calibration,
                     preflight=args.preflight, observations=args.observation)
    print(json.dumps({"status": result["status"], "boundary": result.get("boundary"),
                      "redacted": True}))


if __name__ == "__main__":
    main()
