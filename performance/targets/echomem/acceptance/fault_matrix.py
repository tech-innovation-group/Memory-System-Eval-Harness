"""Run the full four-tenant reject/delay isolation matrix against real HTTP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from performance.targets.echomem.acceptance.capacity_experiment import _load_actors, _write
from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.main_metric_samples import comparison
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
    if not 0 <= delay_ms <= 30000 or q <= 0:
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
                or report.get("delay_ms") != delay_ms):
            raise ValueError("Resume parameters do not match existing fault matrix")
    else:
        output.mkdir(parents=True, mode=0o700)
        report = {"status": "RUNNING", "expected_cases": repeats * 8, "repeats": repeats,
                  "phase_duration_s": phase_duration_s, "recovery_duration_s": recovery_duration_s,
                  "search_rps_per_tenant": q, "delay_ms": delay_ms,
                  "seed_status": seed.get("status"), "cases": [], "current": None,
                  "performance_requirements_applied": False}
        _write(output / "report.json", report, private=True)
    endpoint = base_url.rstrip("/") + "/api/inspect/test-control/fault"
    completed_keys = {(case.get("repeat"), case.get("fault_type"), case.get("target_index"))
                      for case in report.get("cases", [])}

    try:
        for repeat in range(1, repeats + 1):
            for fault_type in ("reject", "delay"):
                pending_targets = [index for index in range(4)
                                   if (repeat, fault_type, index) not in completed_keys]
                if not pending_targets:
                    continue
                report["current"] = {"repeat": repeat, "fault_type": fault_type, "phase": "baseline"}
                _write(output / "report.json", report, private=True)
                baseline = measure(actors, duration_s=phase_duration_s, q=q,
                                   seed=61000 + repeat * 100 + (fault_type == "delay") * 10)
                _write(output / f"r{repeat}-{fault_type}-baseline.json", baseline, private=True)
                for target_index in pending_targets:
                    actor = actors[target_index]
                    label = f"r{repeat}-{fault_type}-T{target_index + 1}"
                    report["current"] = {"repeat": repeat, "fault_type": fault_type,
                                         "target_index": target_index, "phase": "fault"}
                    _write(output / "report.json", report, private=True)
                    enabled = control({"endpoint": endpoint}, action="enable",
                        target_tenant=actor.client.tenant_id, timeout_s=10, token=token,
                        fault_type=fault_type, delay_ms=delay_ms,
                        duration_s=phase_duration_s + 30)
                    during = None
                    disabled = {"status": "NOT_RUN"}
                    try:
                        if enabled.get("status") == "PASS":
                            during = measure(actors, duration_s=phase_duration_s, q=q,
                                             seed=61000 + repeat * 100 + (fault_type == "delay") * 10)
                            _write(output / f"{label}-during.json", during, private=True)
                    finally:
                        disabled = control({"endpoint": endpoint}, action="disable",
                            target_tenant=actor.client.tenant_id, timeout_s=10, token=token)
                    report["current"]["phase"] = "recovery"
                    _write(output / "report.json", report, private=True)
                    recovery = measure(actors, duration_s=recovery_duration_s, q=q,
                                       seed=62000 + repeat * 100 + target_index)
                    _write(output / f"{label}-recovery.json", recovery, private=True)
                    if during is None:
                        case = {"repeat": repeat, "fault_type": fault_type,
                                "target_index": target_index, "status": "INCONCLUSIVE",
                                "control_enabled": enabled.get("status"),
                                "control_disabled": disabled.get("status")}
                    else:
                        pairs = comparison(baseline, during, 4)
                        recovered = comparison(baseline, recovery, 4)
                        target = pairs[target_index]
                        target_p95_delta_s = (
                            target["during"]["p95_s"] - target["before"]["p95_s"]
                            if target["during"].get("p95_s") is not None
                            and target["before"].get("p95_s") is not None else None
                        )
                        effect = (target["during"]["transport_or_http_errors"] > 0
                                  if fault_type == "reject" else
                                  target_p95_delta_s is not None
                                  and target_p95_delta_s >= max(.05, delay_ms / 2000))
                        bystanders = [row for row in pairs if row["identity_index"] != target_index]
                        case = {"repeat": repeat, "fault_type": fault_type,
                                "target_index": target_index,
                                "status": "MEASURED" if effect and disabled.get("status") == "PASS" else "INCONCLUSIVE",
                                "control_enabled": enabled.get("status"),
                                "control_disabled": disabled.get("status"),
                                "target_effect_observed": effect,
                                "target_p95_delta_s": target_p95_delta_s, "pairs": pairs,
                                "recovery_pairs": recovered,
                                "bystander_http_errors": sum(row["during"]["transport_or_http_errors"] for row in bystanders),
                                "worst_bystander_p95_change_percent": max(
                                    (row["p95_degradation_percent"] for row in bystanders
                                     if row.get("p95_degradation_percent") is not None), default=None)}
                    report["cases"].append(case)
                    _write(output / "report.json", report, private=True)
    finally:
        for actor in actors:
            control({"endpoint": endpoint}, action="disable", target_tenant=actor.client.tenant_id,
                    timeout_s=10, token=token)
    measured = [case for case in report["cases"] if case["status"] == "MEASURED"]
    report.update(status="MEASURED" if len(measured) == report["expected_cases"] else "INCONCLUSIVE",
                  current=None, measured_cases=len(measured),
                  bystander_http_errors=sum(case.get("bystander_http_errors", 0) for case in measured),
                  worst_bystander_p95_change_percent=max(
                      (case.get("worst_bystander_p95_change_percent") for case in measured
                       if case.get("worst_bystander_p95_change_percent") is not None), default=None),
                  finished_at_unix_s=time.time())
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
