"""Execute one reproducible M1 hot-user exploration topology."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import time

from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.capacity_seed import CapacityActor, prepare_actors, provision_actors
from performance.targets.echomem.probes._client import EchoMemHTTP
from performance.targets.echomem.acceptance.capacity_statistics import evaluate_level
from performance.targets.echomem.acceptance.capacity_recovery import lifecycle, observe_recovery
from performance.targets.echomem.probes.docker_inspect import inspect_container, resource_sample


def _write(path: Path, value, *, private: bool = False) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        temporary.chmod(0o600)
    temporary.replace(path)


def _private_actors(actors: list) -> list[dict]:
    return [{"tenant_index": actor.tenant_index, "user_index": actor.user_index,
             "client": {key: getattr(actor.client, key) for key in
                        ("base_url", "auth_key", "tenant_id", "user_id", "account_id", "agent_id")},
             "corpus": actor.corpus, "write_session": actor.write_session} for actor in actors]


def _load_actors(directory: Path, base_url: str) -> tuple[list, dict]:
    private = directory / "identities.private.json"
    if private.stat().st_mode & 0o077:
        raise ValueError("Reused identity file must be owner-only (0600)")
    values = json.loads(private.read_text(encoding="utf-8"))
    actors = []
    for row in values:
        client = dict(row["client"])
        client["base_url"] = base_url
        actors.append(CapacityActor(row["tenant_index"], row["user_index"],
                                   EchoMemHTTP(**client), row["corpus"], row["write_session"]))
    seed = json.loads((directory / "seed-evidence.json").read_text(encoding="utf-8"))
    return actors, seed


def run_exploration(*, base_url: str, output: Path, topology: str, levels: list[int],
                    fixed_tenants: int = 4, memory_scale: int = 1,
                    warmup_s: float = 30, duration_s: float = 60, q: float = 1,
                    target_container: str = "", manifest: dict | None = None,
                    assessment_mode: str = "observe", load_profile: str = "pure",
                    reuse_seed: Path | None = None, seed_validation_queries: int = 40,
                    recovery_timeout_s: float = 300, request_timeout_s: float = 10) -> dict:
    if output.exists():
        raise FileExistsError(f"Refuse to overwrite M1 evidence directory: {output}")
    output.mkdir(parents=True, mode=0o700)
    if not levels or levels != sorted(set(levels)) or min(levels) < 1:
        raise ValueError("levels must be unique ascending positive integers")
    if topology not in {"cross-tenant", "within-tenant"}:
        raise ValueError("topology must be cross-tenant or within-tenant")
    if assessment_mode not in {"observe", "slo"} or load_profile not in {"pure", "mixed", "both"}:
        raise ValueError("Invalid assessment mode or load profile")
    tenants = max(levels) if topology == "cross-tenant" else fixed_tenants
    users = 1 if topology == "cross-tenant" else max(levels)
    if target_container:
        state = inspect_container(target_container)
        if not state.get("State", {}).get("Running"):
            raise RuntimeError("Target container is not running")
    report = {"status": "PREPARING", "topology": topology, "levels_requested": levels,
              "assessment_mode": assessment_mode, "load_profile": load_profile,
              "recovery_timeout_s": recovery_timeout_s, "request_timeout_s": request_timeout_s,
              "fixed_tenants": fixed_tenants if topology == "within-tenant" else None,
              "memory_scale": memory_scale, "warmup_s": warmup_s, "duration_s": duration_s,
              "per_user_search_rps": q, "manifest": manifest or {}, "seed": {}, "levels": [],
              "boundary": {"status": "UNMEASURED", "highest_pass": None, "first_fail": None},
              "max_hot_users": None, "dau": None}
    _write(output / "report.json", report)
    actors, reused_seed = (_load_actors(reuse_seed, base_url) if reuse_seed else
                           (provision_actors(base_url, tenants, users, memory_scale=memory_scale), None))
    extension = []
    if len(actors) < tenants * users and reused_seed is not None and topology == "cross-tenant":
        if len({a.tenant_index for a in actors}) != len(actors) or any(a.user_index for a in actors):
            raise ValueError("Cross-tenant extension requires one user per tenant")
        extension = provision_actors(base_url, tenants - len(actors), 1, memory_scale=memory_scale,
                                     tenant_offset=max(a.tenant_index for a in actors) + 1)
        actors.extend(extension)
    if len(actors) != tenants * users:
        raise ValueError("Seed identities do not match the requested maximum topology")
    _write(output / "identities.private.json", _private_actors(actors), private=True)

    progress: dict[str, dict] = {}
    progress_lock = threading.Lock()

    def checkpoint(row):
        key = f"{row['tenant_index']}:{row['user_index']}"
        with progress_lock:
            progress[key] = row
            _write(output / "seed-progress.json", progress)

    seeded = reused_seed if reused_seed is not None else prepare_actors(
        actors, checkpoint=checkpoint, validation_queries=seed_validation_queries)
    if extension:
        extension_seed = prepare_actors(extension, checkpoint=checkpoint,
                                        validation_queries=seed_validation_queries)
        seeded = {"actors": [*seeded.get("actors", []), *extension_seed["actors"]],
                  "status": "PASS" if seeded["status"] == extension_seed["status"] == "PASS" else "INCONCLUSIVE",
                  "actor_count": len(actors), "new_actor_count": len(extension),
                  "raw_credentials_exported": False}
    report["seed_reused"] = reused_seed is not None
    report["seed"] = seeded
    _write(output / "seed-evidence.json", seeded)
    _write(output / "identities.private.json", _private_actors(actors), private=True)
    if seeded["status"] != "PASS" and assessment_mode == "slo":
        report.update(status="BLOCKED", phase="semantic-seed")
        _write(output / "report.json", report)
        return report

    resources, phase = [], {"name": "idle", "level": None}
    stop = threading.Event()

    def collect_resources():
        while not stop.is_set():
            started = time.monotonic()
            sample_phase = dict(phase)
            try:
                row = resource_sample(target_container) if target_container else {}
            except Exception as exc:
                row = {"error_class": type(exc).__name__}
            row.update(at_epoch_s=time.time(), phase=sample_phase["name"], level=sample_phase["level"],
                       collection_s=time.monotonic() - started)
            resources.append(row)
            _write(output / "resources.json", resources)
            stop.wait(5)

    thread = threading.Thread(target=collect_resources, daemon=True)
    thread.start()
    try:
        for level_index, level in enumerate(levels):
            if topology == "cross-tenant":
                selected = [actor for actor in actors if actor.tenant_index < level]
                tenant_count, user_count = level, 1
            else:
                selected = [actor for actor in actors if actor.user_index < level]
                tenant_count, user_count = fixed_tenants, level
            hot_users = len(selected)
            if target_container and not inspect_container(target_container).get("State", {}).get("Running"):
                report["stop_reason"] = "target-container-stopped"
                break
            profiles = (False, True) if load_profile == "both" else (load_profile == "mixed",)
            for mixed in profiles:
                label = "mixed" if mixed else "pure"
                before = lifecycle(target_container)
                phase.update(name=label + "-warmup", level=level)
                report.update(status="RUNNING", current=dict(phase))
                _write(output / "report.json", report)
                warmup = measure(selected, duration_s=warmup_s, q=q, seed=4200 + level_index,
                                  request_timeout_s=request_timeout_s)
                _write(output / f"level-{level}-{label}-warmup.json", warmup)
                phase.update(name=label + "-measurement", level=level)
                report["current"] = dict(phase)
                _write(output / "report.json", report)
                measurement = measure(selected, duration_s=duration_s, q=q, mixed=mixed, seed=4300 + level_index,
                                       request_timeout_s=request_timeout_s)
                _write(output / f"level-{level}-{label}-measurement.json", measurement)
                result = evaluate_level(measurement, assessment_mode=assessment_mode)
                result.update(level=level, topology=topology, tenant_count=tenant_count,
                              users_per_tenant=user_count, hot_users=hot_users, memory_scale=memory_scale,
                              measurement_file=f"level-{level}-{label}-measurement.json")
                report["levels"].append(result)
                result["resources"] = [r for r in resources if r.get("level") == level
                                       and r.get("phase") == label + "-measurement"]
                if assessment_mode == "observe":
                    phase.update(name="recovery-observation", level=level)
                    report["current"] = dict(phase)
                    _write(output / "report.json", report)
                    recovery = observe_recovery(selected, measurement, container=target_container,
                        before=before, timeout_s=recovery_timeout_s,
                        checkpoint=lambda value: _write(output / f"level-{level}-{label}-recovery.json", value))
                    result["recovery"] = recovery
                    _write(output / f"level-{level}-{label}-recovery.json", recovery)
                    if recovery["status"] == "BOUNDARY_OBSERVED":
                        report["stop_reason"] = recovery["reason"]
                        report["operational_boundary"] = {"hot_users": hot_users, "load_profile": label,
                                                           "evidence": recovery}
                _write(output / "report.json", report)
                if report.get("stop_reason"):
                    break
            passes = [row for row in report["levels"] if row["status"] == "PASS"]
            failures = [row for row in report["levels"] if row["status"] == "FAIL"]
            report["boundary"] = {"status": "EXPLORING",
                                  "highest_pass": max((row["hot_users"] for row in passes), default=None),
                                  "first_fail": min((row["hot_users"] for row in failures), default=None)}
            report.update(status="RUNNING", phase="capacity-exploration")
            _write(output / "report.json", report)
            if report.get("stop_reason") or assessment_mode == "slo" and result["status"] == "FAIL":
                break
    finally:
        stop.set()
        thread.join()
    if assessment_mode == "observe":
        observed = [r for r in report["levels"] if r["search"]["sent"]]
        report.update(status="BOUNDARY_OBSERVED" if report.get("operational_boundary") else
                      "PARTIAL" if report.get("stop_reason") else "MEASURED",
                      phase="capacity-observation-complete", current=None, resources=resources,
                      highest_measured_hot_users=max((r["hot_users"] for r in observed), default=None),
                      max_hot_users=None, dau=None, performance_requirements_applied=False,
                      boundary={"status": "NOT_ESTABLISHED", "reason": "observation-without-performance-thresholds"})
        _write(output / "report.json", report)
        return report
    highest = report["boundary"]["highest_pass"]
    failed = report["boundary"]["first_fail"]
    if highest is not None and failed is not None and highest < failed:
        boundary_status, status = "CANDIDATE", "INCONCLUSIVE"
    elif highest is not None:
        boundary_status, status = "LOWER_BOUND", "INCONCLUSIVE"
    else:
        boundary_status, status = "NO_PASS", "BLOCKED"
    report["boundary"]["status"] = boundary_status
    report.update(status=status, phase="capacity-exploration-complete", resources=resources,
                  evidence_complete=False, max_hot_users=None, dau=None)
    _write(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topology", choices=("cross-tenant", "within-tenant"), required=True)
    parser.add_argument("--levels", default="1,2,4,8,12,16")
    parser.add_argument("--fixed-tenants", type=int, default=4)
    parser.add_argument("--memory-scale", type=int, choices=(1, 10), default=1)
    parser.add_argument("--warmup-s", type=float, default=30)
    parser.add_argument("--duration-s", type=float, default=60)
    parser.add_argument("--per-user-search-rps", type=float, default=1)
    parser.add_argument("--target-container", default="")
    parser.add_argument("--manifest-json", default="{}")
    parser.add_argument("--assessment-mode", choices=("observe", "slo"), default="observe")
    parser.add_argument("--load-profile", choices=("pure", "mixed", "both"), default="pure")
    parser.add_argument("--reuse-seed", type=Path,
                        help="server-local previous run containing owner-only identities.private.json")
    parser.add_argument("--seed-validation-queries", type=int, default=40)
    parser.add_argument("--recovery-timeout-s", type=float, default=300)
    parser.add_argument("--request-timeout-s", type=float, default=10)
    args = parser.parse_args()
    result = run_exploration(base_url=args.base_url, output=args.output, topology=args.topology,
        levels=[int(item) for item in args.levels.split(",")], fixed_tenants=args.fixed_tenants,
        memory_scale=args.memory_scale, warmup_s=args.warmup_s, duration_s=args.duration_s,
        q=args.per_user_search_rps, target_container=args.target_container,
        manifest=json.loads(args.manifest_json), assessment_mode=args.assessment_mode,
        load_profile=args.load_profile, reuse_seed=args.reuse_seed,
        seed_validation_queries=args.seed_validation_queries, recovery_timeout_s=args.recovery_timeout_s,
        request_timeout_s=args.request_timeout_s)
    print(json.dumps({"status": result["status"], "phase": result["phase"],
                      "boundary": result["boundary"], "levels": [{"level": row["level"],
                      "hot_users": row["hot_users"], "status": row["status"]}
                      for row in result["levels"]]}))


if __name__ == "__main__":
    main()
