"""Confirm an M1 hot-user boundary with fresh identities and three repeats."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import time

from performance.targets.echomem.acceptance.capacity_experiment import _private_actors, _write
from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.capacity_seed import prepare_actors, provision_actors
from performance.targets.echomem.acceptance.capacity_statistics import evaluate_level
from performance.targets.echomem.probes.docker_inspect import resource_sample, restart_container


def _shifted_rows(measurements: list[dict]) -> list[dict]:
    rows, offset = [], 0.0
    timing = ("scheduled_s", "start_s", "end_s", "accepted_at_s")
    for measurement in measurements:
        for source in measurement["rows"]:
            row = dict(source)
            if row.get("op") == "commit_done":
                row["completed_in_window"] = bool(row.get("success") and row.get("end_s", float("inf")) <= measurement["duration_s"])
            for key in timing:
                if row.get(key) is not None:
                    row[key] += offset
            rows.append(row)
        offset += measurement["duration_s"] + 10
    return rows


def _aggregate(measurements: list[dict]) -> dict:
    first = measurements[0]
    return {**{key: first.get(key) for key in ("mixed", "identity_count", "tenant_count",
             "per_user_search_rps", "per_user_message_rate_per_min",
             "per_user_commit_interval_s", "recall_query_fraction",
             "request_timeout_s", "commit_deadline_s")},
            "duration_s": sum(row["duration_s"] for row in measurements),
            "elapsed_with_drain_s": sum(row["elapsed_with_drain_s"] for row in measurements),
            "planned_search": sum(row["planned_search"] for row in measurements),
            "rows": _shifted_rows(measurements), "repeat_count": len(measurements),
            "arrival_process": "independent-seeded-poisson"}


def estimate_dau(mixed: dict, *, searches_per_day: int = 50,
                 commits_per_day_range: tuple[int, int] = (40, 60),
                 peak_factors: tuple[int, ...] = (1, 3, 5)) -> list[dict]:
    search_rps = mixed["effective_search_rps"]
    commit = mixed["commit"]
    completion_rps = commit.get("completed_rps", commit["completed"] / mixed["duration_s"] if mixed["duration_s"] else 0)
    interval = mixed.get("per_user_commit_interval_s") or 300
    offered_commit_rps = mixed.get("identity_count", 0) / interval
    # A finite window may contain an extra boundary event (for example at 30s
    # and 330s in a 450s window).  Do not call that transient density a stable
    # capacity; cap it at the declared per-user steady-state schedule.
    commit_capacity_rps = min(completion_rps, offered_commit_rps)
    rows = []
    for factor in peak_factors:
        search_dau = search_rps * 86400 / searches_per_day / factor
        for commits_per_day in commits_per_day_range:
            commit_dau = commit_capacity_rps * 86400 / commits_per_day / factor
            rows.append({"searches_per_user_day": searches_per_day,
                         "commits_per_user_day": commits_per_day,
                         "search_peak_factor": factor, "commit_peak_factor": factor,
                         "search_limited_dau": search_dau,
                         "commit_limited_dau": commit_dau,
                         "observed_completion_rps": completion_rps,
                         "steady_commit_capacity_rps": commit_capacity_rps,
                         "conservative_dau": min(search_dau, commit_dau)})
    return rows


def _finalize(suite: dict) -> dict:
    if suite.get("assessment_mode", "observe") == "observe":
        measured = [r for r in suite["levels"] if r["status"] == "MEASURED"]
        suite.update(status="MEASURED" if len(measured) == len(suite["levels"]) else "PARTIAL",
                     max_hot_users=None, dau=None, evidence_complete=False, current=None,
                     performance_requirements_applied=False,
                     highest_measured_hot_users=max((r["hot_users"] for r in measured), default=None),
                     boundary={"status": "NOT_ESTABLISHED", "reason": "observation-without-performance-thresholds"})
        return suite
    passed = [row for row in suite["levels"] if row["status"] == "PASS"]
    failed = [row for row in suite["levels"] if row["status"] == "FAIL"]
    highest = max((row["hot_users"] for row in passed), default=None)
    first_fail = min((row["hot_users"] for row in failed
                      if highest is None or row["hot_users"] > highest), default=None)
    if highest is not None and first_fail is not None:
        suite.update(status="PASS", evidence_complete=True, max_hot_users=highest,
                     boundary={"status": "CONFIRMED", "highest_pass": highest,
                               "first_fail": first_fail,
                               "evidence": "three-fresh-identity-repeats"})
        winning = next(row for row in passed if row["hot_users"] == highest)
        suite["dau"] = {"status": "CONDITIONAL_ESTIMATE",
                        "basis": "PR397 standard user: 50 search/day, 40-60 commit/day",
                        "estimates": estimate_dau(winning["mixed_aggregate"])}
    elif highest is None and failed and min(row["hot_users"] for row in failed) == 1:
        suite.update(status="FAIL", evidence_complete=True, max_hot_users=0,
                     boundary={"status": "CONFIRMED", "highest_pass": 0, "first_fail": 1,
                               "evidence": "three-fresh-identity-repeats"},
                     dau={"status": "ZERO_UNDER_LOCKED_SLO", "estimates": []})
    else:
        suite.update(status="INCONCLUSIVE", evidence_complete=False, max_hot_users=None,
                     boundary={"status": "UNCONFIRMED", "highest_pass": highest,
                               "first_fail": first_fail}, dau=None)
    suite["current"] = None
    return suite


def recompute_confirmation(output: Path, *, assessment_mode: str = "observe") -> dict:
    """Rebuild derived conclusions from immutable raw measurement files."""
    source = json.loads((output / "report.json").read_text(encoding="utf-8"))
    suite = {key: value for key, value in source.items() if key not in
             ("levels", "status", "boundary", "max_hot_users", "dau",
              "evidence_complete", "current")}
    suite.update(status="RUNNING", levels=[], max_hot_users=None, dau=None,
                 assessment_mode=assessment_mode,
                 evidence_complete=False, current=None, derived_from_raw=True,
                 derivation="capacity_statistics.evaluate_level")
    topology = suite["topology"]
    fixed_tenants = source.get("fixed_tenants", 4)
    for level in suite["levels_requested"]:
        tenant_count = level if topology == "cross-tenant" else fixed_tenants
        users_per_tenant = 1 if topology == "cross-tenant" else level
        result = {"level": level, "hot_users": tenant_count * users_per_tenant,
                  "tenant_count": tenant_count, "users_per_tenant": users_per_tenant,
                  "repeats": []}
        pure_measurements, mixed_measurements = [], []
        for repeat in range(suite["repeats"]):
            root = output / f"level-{level}-repeat-{repeat+1:02d}"
            seed_path = root / "seed-evidence.json"
            pure_path = root / "pure-measurement.json"
            mixed_path = root / "mixed-measurement.json"
            row = {"repeat": repeat + 1,
                   "seed_status": (json.loads(seed_path.read_text(encoding="utf-8")).get("status")
                                   if seed_path.exists() else "MISSING")}
            if row["seed_status"] != "PASS" or not pure_path.exists() or not mixed_path.exists():
                row.update(status="BLOCKED", reason="missing-or-invalid-raw-evidence")
                result["repeats"].append(row)
                continue
            pure = json.loads(pure_path.read_text(encoding="utf-8"))
            mixed = json.loads(mixed_path.read_text(encoding="utf-8"))
            pure_result = evaluate_level(pure, assessment_mode=assessment_mode)
            mixed_result = evaluate_level(mixed, assessment_mode=assessment_mode)
            row.update(status="MEASURED" if assessment_mode == "observe" else
                       "PASS" if pure_result["slo_observed"] and mixed_result["slo_observed"] else "FAIL",
                       pure=pure_result, mixed=mixed_result,
                       pure_resource_samples=_json_list_size(root / "pure-measurement-resources.json"),
                       mixed_resource_samples=_json_list_size(root / "mixed-measurement-resources.json"))
            result["repeats"].append(row)
            pure_measurements.append(pure)
            mixed_measurements.append(mixed)
        if len(pure_measurements) == suite["repeats"] and len(mixed_measurements) == suite["repeats"]:
            result["pure_aggregate"] = evaluate_level(_aggregate(pure_measurements), confirmation=True,
                                                       assessment_mode=assessment_mode)
            result["mixed_aggregate"] = evaluate_level(_aggregate(mixed_measurements), confirmation=True,
                                                        assessment_mode=assessment_mode)
            statuses = (result["pure_aggregate"]["status"], result["mixed_aggregate"]["status"])
            result["status"] = "MEASURED" if statuses == ("MEASURED", "MEASURED") else \
                "PASS" if statuses == ("PASS", "PASS") else \
                "FAIL" if "FAIL" in statuses else "INCONCLUSIVE"
        else:
            result["status"] = "BLOCKED"
        suite["levels"].append(result)
    return _finalize(suite)


def _json_list_size(path: Path) -> int:
    if not path.exists():
        return 0
    value = json.loads(path.read_text(encoding="utf-8"))
    return len(value) if isinstance(value, list) else 0


def run_confirmation(*, base_url: str, output: Path, topology: str,
                     levels: list[int], fixed_tenants: int = 4, repeats: int = 3,
                     pure_duration_s: float = 180, mixed_duration_s: float = 450,
                     warmup_s: float = 30, q: float = 1, memory_scale: int = 1,
                     manifest: dict | None = None, target_container: str = "",
                     assessment_mode: str = "observe") -> dict:
    if output.exists():
        raise FileExistsError(f"Refuse to overwrite M1 confirmation: {output}")
    output.mkdir(parents=True, mode=0o700)
    if repeats < 3 or len(levels) not in (1, 2) or levels != sorted(set(levels)):
        raise ValueError("confirmation needs >=3 repeats and one/two ascending boundary levels")
    suite = {"status": "RUNNING", "topology": topology, "levels_requested": levels,
             "assessment_mode": assessment_mode,
             "fixed_tenants": fixed_tenants,
             "repeats": repeats, "pure_duration_s": pure_duration_s,
             "mixed_duration_s": mixed_duration_s, "warmup_s": warmup_s,
             "per_user_search_rps": q, "memory_scale": memory_scale,
             "manifest": manifest or {}, "levels": [], "max_hot_users": None,
             "dau": None, "evidence_complete": False, "current": None}
    _write(output / "report.json", suite)

    def progress(stage: str, level: int, repeat: int, *, elapsed_s: float = 0,
                 duration_s: float | None = None) -> None:
        suite["current"] = {"stage": stage, "level": level, "repeat": repeat,
                            "elapsed_s": elapsed_s, "duration_s": duration_s,
                            "updated_at_unix_s": time.time()}
        _write(output / "report.json", suite)

    def measured(actors, destination: Path, *, stage: str, level: int, repeat: int, **kwargs):
        samples, stop = [], threading.Event()
        origin = time.monotonic()

        def collect():
            while not stop.is_set():
                started = time.monotonic()
                try:
                    row = resource_sample(target_container) if target_container else {}
                except Exception as exc:
                    row = {"error_class": type(exc).__name__}
                row.update(at_s=time.monotonic() - origin, collection_s=time.monotonic() - started)
                samples.append(row)
                _write(destination.with_name(destination.stem + "-resources.json"), samples)
                progress(stage, level, repeat, elapsed_s=row["at_s"],
                         duration_s=kwargs.get("duration_s"))
                stop.wait(5)

        thread = threading.Thread(target=collect, daemon=True)
        thread.start()
        try:
            result = measure(actors, **kwargs)
        finally:
            stop.set()
            thread.join()
        return result, samples

    for level in levels:
        tenant_count = level if topology == "cross-tenant" else fixed_tenants
        users_per_tenant = 1 if topology == "cross-tenant" else level
        hot_users = tenant_count * users_per_tenant
        level_result = {"level": level, "hot_users": hot_users, "tenant_count": tenant_count,
                        "users_per_tenant": users_per_tenant, "repeats": []}
        suite["levels"].append(level_result)
        _write(output / "report.json", suite)
        pure_measurements, mixed_measurements = [], []
        for repeat in range(repeats):
            repeat_root = output / f"level-{level}-repeat-{repeat+1:02d}"
            repeat_root.mkdir(mode=0o700)
            progress("provision-and-seed", level, repeat + 1)
            actors = provision_actors(base_url, tenant_count, users_per_tenant,
                                      memory_scale=memory_scale, seed=42)
            _write(repeat_root / "identities.private.json", _private_actors(actors), private=True)
            seed = prepare_actors(actors)
            _write(repeat_root / "seed-evidence.json", seed)
            _write(repeat_root / "identities.private.json", _private_actors(actors), private=True)
            repeat_result = {"repeat": repeat + 1, "seed_status": seed["status"]}
            if seed["status"] != "PASS":
                repeat_result.update(status="BLOCKED", reason="semantic-seed")
                level_result["repeats"].append(repeat_result)
                _write(output / "report.json", suite)
                continue
            if target_container:
                progress("restart-target", level, repeat + 1)
                restart = restart_container(target_container,
                    base_url.rstrip("/") + "/api/v1/system/ready")
                repeat_result["restart_before_measurement"] = restart
                if restart["status"] != "ready":
                    repeat_result.update(status="BLOCKED", reason="restart-readiness")
                    level_result["repeats"].append(repeat_result)
                    _write(output / "report.json", suite)
                    continue
            progress("pure-warmup", level, repeat + 1, duration_s=warmup_s)
            _write(repeat_root / "pure-warmup.json",
                   measure(actors, duration_s=warmup_s, q=q, seed=5100 + repeat))
            pure, pure_resources = measured(actors, repeat_root / "pure-measurement.json",
                stage="pure-measurement", level=level, repeat=repeat + 1,
                duration_s=pure_duration_s, q=q, seed=5200 + repeat)
            _write(repeat_root / "pure-measurement.json", pure)
            pure_result = evaluate_level(pure, assessment_mode=assessment_mode)
            progress("mixed-warmup", level, repeat + 1, duration_s=warmup_s)
            _write(repeat_root / "mixed-warmup.json",
                   measure(actors, duration_s=warmup_s, q=q, seed=5300 + repeat))
            mixed, mixed_resources = measured(actors, repeat_root / "mixed-measurement.json",
                stage="mixed-measurement", level=level, repeat=repeat + 1,
                duration_s=mixed_duration_s, q=q, mixed=True, seed=5400 + repeat)
            _write(repeat_root / "mixed-measurement.json", mixed)
            mixed_result = evaluate_level(mixed, assessment_mode=assessment_mode)
            repeat_result.update(status="MEASURED" if assessment_mode == "observe" else
                                 "PASS" if pure_result["slo_observed"] and
                                 mixed_result["slo_observed"] else "FAIL",
                                 pure=pure_result, mixed=mixed_result,
                                 pure_resource_samples=len(pure_resources),
                                 mixed_resource_samples=len(mixed_resources))
            level_result["repeats"].append(repeat_result)
            pure_measurements.append(pure)
            mixed_measurements.append(mixed)
            _write(output / "report.json", suite)
        if len(pure_measurements) == repeats and len(mixed_measurements) == repeats:
            pure_aggregate = evaluate_level(_aggregate(pure_measurements), confirmation=True,
                                            assessment_mode=assessment_mode)
            mixed_aggregate = evaluate_level(_aggregate(mixed_measurements), confirmation=True,
                                             assessment_mode=assessment_mode)
            level_result.update(pure_aggregate=pure_aggregate, mixed_aggregate=mixed_aggregate,
                                status="MEASURED" if pure_aggregate["status"] == mixed_aggregate["status"] == "MEASURED" else
                                "PASS" if pure_aggregate["status"] == "PASS" and
                                mixed_aggregate["status"] == "PASS" else
                                "FAIL" if pure_aggregate["status"] == "FAIL" or
                                mixed_aggregate["status"] == "FAIL" else "INCONCLUSIVE")
        else:
            level_result["status"] = "BLOCKED"
        _write(output / "report.json", suite)
        if assessment_mode == "slo" and hot_users == 1 and level_result["status"] == "FAIL":
            suite["early_stop"] = {
                "reason": "lowest-load-level-failed-locked-slo",
                "skipped_levels": [candidate for candidate in levels if candidate > level],
            }
            break
    _finalize(suite)
    _write(output / "report.json", suite)
    return suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topology", choices=("cross-tenant", "within-tenant"))
    parser.add_argument("--levels")
    parser.add_argument("--fixed-tenants", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--pure-duration-s", type=float, default=180)
    parser.add_argument("--mixed-duration-s", type=float, default=450)
    parser.add_argument("--warmup-s", type=float, default=30)
    parser.add_argument("--memory-scale", type=int, choices=(1, 10), default=1)
    parser.add_argument("--manifest-json", default="{}")
    parser.add_argument("--target-container", default="")
    parser.add_argument("--assessment-mode", choices=("observe", "slo"), default="observe")
    parser.add_argument("--recompute", action="store_true",
                        help="rebuild report.recomputed.json from existing raw files")
    args = parser.parse_args()
    if args.recompute:
        result = recompute_confirmation(args.output, assessment_mode=args.assessment_mode)
        _write(args.output / "report.recomputed.json", result)
    else:
        if not args.base_url or not args.topology or not args.levels:
            parser.error("--base-url, --topology and --levels are required unless --recompute is used")
        result = run_confirmation(base_url=args.base_url, output=args.output,
            topology=args.topology, levels=[int(item) for item in args.levels.split(",")],
            fixed_tenants=args.fixed_tenants, repeats=args.repeats,
            pure_duration_s=args.pure_duration_s, mixed_duration_s=args.mixed_duration_s,
            warmup_s=args.warmup_s, memory_scale=args.memory_scale,
            manifest=json.loads(args.manifest_json), target_container=args.target_container,
            assessment_mode=args.assessment_mode)
    print(json.dumps({"status": result["status"], "boundary": result.get("boundary"),
                      "max_hot_users": result["max_hot_users"], "dau": result["dau"]}))


if __name__ == "__main__":
    main()
