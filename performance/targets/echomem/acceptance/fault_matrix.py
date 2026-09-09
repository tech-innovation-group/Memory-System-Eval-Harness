"""Run the full four-tenant reject/delay isolation matrix against real HTTP."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import time

from performance.targets.echomem.acceptance.capacity_experiment import _load_actors, _write
from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.main_metric_samples import comparison
from performance.targets.echomem.acceptance.fault_evidence import case_counts, control_receipt, matrix_counts
from performance.targets.echomem.probes.docker_inspect import inspect_container
from performance.targets.echomem.probes.fault_isolation import control


def run(*, base_url: str, seed_directory: Path, output: Path, container: str,
        repeats: int = 3, phase_duration_s: float = 60, recovery_duration_s: float = 30,
        q: float = 1, delay_ms: int = 1000,
        token_env: str = "ECHOMEM_TEST_CONTROL_TOKEN", resume: bool = False) -> dict:
    if output.exists() and not resume:
        raise FileExistsError("Refuse to overwrite fault-matrix evidence")
    if repeats < 1 or not 15 <= phase_duration_s <= 120 or not 15 <= recovery_duration_s <= 120:
        raise ValueError("repeats must be positive and phase durations must be 15..120 seconds")
    if type(delay_ms) is not int or not 1 <= delay_ms <= 30000 or not math.isfinite(q) or q <= 0:
        raise ValueError("invalid delay or Search rate")
    state = inspect_container(container)
    if (not state.get("State", {}).get("Running")
            or state["HostConfig"].get("NanoCpus") != 4_000_000_000
            or state["HostConfig"].get("Memory") != 8_589_934_592):
        raise ValueError("A running dedicated 4CPU/8GiB target is required")
    actors, seed = _load_actors(seed_directory, base_url)
    actors = actors[:4]
    if len(actors) != 4 or len({a.client.tenant_id for a in actors}) != 4:
        raise ValueError("Four independent seeded tenants are required")
    token = os.environ.get(token_env, "")
    if not token:
        raise ValueError("Test-control token is required")
    if output.exists():
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        if (report.get("repeats") != repeats or report.get("phase_duration_s") != phase_duration_s
                or report.get("recovery_duration_s") != recovery_duration_s
                or report.get("delay_ms") != delay_ms or report.get("search_rps_per_tenant") != q
                or report.get("base_url") != base_url or report.get("container") != container
                or report.get("seed_directory") != str(seed_directory.resolve())):
            raise ValueError("Resume parameters do not match existing fault matrix")
    else:
        output.mkdir(parents=True, mode=0o700)
        report = {"status": "RUNNING", "expected_cases": repeats * 8, "repeats": repeats,
                  "protocol_version": 2, "baseline_scope": "per_target", "read_worker_isolation": "per_identity",
                  "phase_duration_s": phase_duration_s, "recovery_duration_s": recovery_duration_s,
                  "search_rps_per_tenant": q, "delay_ms": delay_ms,
                  "base_url": base_url, "container": container, "seed_directory": str(seed_directory.resolve()),
                  "seed_status": seed.get("status"), "cases": [], "current": None,
                  "performance_requirements_applied": False}
        _write(output / "report.json", report, private=True)
    endpoint = base_url.rstrip("/") + "/api/inspect/test-control/fault"
    completed_keys = {(case.get("repeat"), case.get("fault_type"), case.get("target_index"))
                      for case in report.get("cases", [])}

    try:
        for actor in actors:
            reply = control({"endpoint": endpoint}, action="disable", target_tenant=actor.client.tenant_id,
                            timeout_s=10, token=token)
            if not control_receipt(reply, actor.client.tenant_id, "reject")["cleared"]:
                raise RuntimeError("Cannot verify a fault-free baseline; no workload started")
        for repeat in range(1, repeats + 1):
            for fault_type in ("reject", "delay"):
                pending_targets = [index for index in range(4)
                                   if (repeat, fault_type, index) not in completed_keys]
                if not pending_targets:
                    continue
                for target_index in pending_targets:
                    actor = actors[target_index]
                    label = f"r{repeat}-{fault_type}-T{target_index + 1}"
                    attempt = output / label / f"attempt-{time.time_ns()}"
                    attempt.mkdir(parents=True, mode=0o700)
                    report["current"] = {"repeat": repeat, "fault_type": fault_type,
                                         "target_index": target_index, "phase": "baseline"}
                    _write(output / "report.json", report, private=True)
                    schedule_seed = 61000 + repeat * 100 + (fault_type == "delay") * 10 + target_index
                    baseline = measure(actors, duration_s=phase_duration_s, q=q, seed=schedule_seed,
                                       isolate_read_workers=True)
                    _write(attempt / "baseline.json", baseline, private=True)
                    report["current"]["phase"] = "fault"
                    _write(output / "report.json", report, private=True)
                    enable_started = time.monotonic()
                    enabled = control({"endpoint": endpoint}, action="enable",
                        target_tenant=actor.client.tenant_id, timeout_s=10, token=token,
                        fault_type=fault_type, delay_ms=delay_ms,
                        duration_s=phase_duration_s + 30)
                    during = None
                    at_end = {}
                    fault_elapsed = None
                    disabled = {"status": "NOT_RUN"}
                    try:
                        if control_receipt(enabled, actor.client.tenant_id, fault_type)["active_match"]:
                            during = measure(actors, duration_s=phase_duration_s, q=q,
                                             seed=schedule_seed, isolate_read_workers=True)
                            fault_elapsed = time.monotonic() - enable_started
                            _write(attempt / "during.json", during, private=True)
                            at_end = control({"endpoint": endpoint}, action="status",
                                target_tenant=actor.client.tenant_id, timeout_s=10, token=token)
                    finally:
                        disabled = control({"endpoint": endpoint}, action="disable",
                            target_tenant=actor.client.tenant_id, timeout_s=10, token=token)
                    report["current"]["phase"] = "recovery"
                    _write(output / "report.json", report, private=True)
                    cleared = control_receipt(disabled, actor.client.tenant_id, fault_type)["cleared"]
                    recovery = (measure(actors, duration_s=recovery_duration_s, q=q, seed=schedule_seed,
                                        isolate_read_workers=True)
                                if cleared else {"rows": []})
                    _write(attempt / "recovery.json", recovery, private=True)
                    if during is None:
                        case = {"repeat": repeat, "fault_type": fault_type,
                                "target_index": target_index, "status": "INCONCLUSIVE",
                                "control_enabled": enabled.get("status"),
                                "control_disabled": disabled.get("status")}
                    else:
                        pairs = comparison(baseline, during, 4)
                        recovered = comparison(baseline, recovery, 4)
                        case = {"repeat": repeat, "fault_type": fault_type,
                                "target_index": target_index,
                                "control_enabled": enabled.get("status"),
                                "control_disabled": disabled.get("status"),
                                "pairs": pairs, "recovery_pairs": recovered}
                    case["artifact_directory"] = str(attempt.relative_to(output))
                    case["control_evidence"] = {
                        "enabled": control_receipt(enabled, actor.client.tenant_id, fault_type),
                        "at_end": control_receipt(at_end, actor.client.tenant_id, fault_type),
                        "disabled": control_receipt(disabled, actor.client.tenant_id, fault_type),
                        "window_covered": fault_elapsed is not None and fault_elapsed < phase_duration_s + 30,
                        "elapsed_since_enable_s": fault_elapsed, "duration_s": phase_duration_s + 30}
                    evidence = case_counts(case, delay_ms=delay_ms)
                    case.update({key: evidence[key] for key in ("status", "target_effect_observed",
                                "bystander_http_errors", "worst_bystander_p95_change_percent")})
                    case["verification"] = evidence
                    report["cases"].append(case)
                    _write(output / "report.json", report, private=True)
                    if not cleared:
                        raise RuntimeError("Fault removal was not verified; refusing to contaminate the next case")
    except Exception as exc:
        report.update(status="EXECUTION_ERROR", error_class=type(exc).__name__)
        _write(output / "report.json", report, private=True)
        raise
    finally:
        for actor in actors:
            control({"endpoint": endpoint}, action="disable", target_tenant=actor.client.tenant_id,
                    timeout_s=10, token=token)
    verification = matrix_counts(report)
    report.update({key: verification[key] for key in ("status", "measured_cases", "bystander_http_errors",
                  "known_bystander_http_errors", "worst_bystander_p95_change_percent")})
    report.update(current=None, verification=verification, finished_at_unix_s=time.time())
    _write(output / "report.json", report, private=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--seed-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--phase-duration-s", type=float, default=60)
    parser.add_argument("--recovery-duration-s", type=float, default=30)
    parser.add_argument("--search-rps", type=float, default=1)
    parser.add_argument("--delay-ms", type=int, default=1000)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run(base_url=args.base_url, seed_directory=args.seed_directory,
                 output=args.output, container=args.container, repeats=args.repeats,
                 phase_duration_s=args.phase_duration_s,
                 recovery_duration_s=args.recovery_duration_s,
                 q=args.search_rps, delay_ms=args.delay_ms, resume=args.resume)
    print(json.dumps({"status": result["status"], "measured_cases": result["measured_cases"],
                      "expected_cases": result["expected_cases"]}))


if __name__ == "__main__":
    main()
