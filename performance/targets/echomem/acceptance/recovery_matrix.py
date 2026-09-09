"""Run repeatable real kill-9 Commit recovery samples with different timings."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from performance.probe import ProbeModule, ProbeRunner
from performance.targets.echomem.acceptance.capacity_experiment import _load_actors, _write
from performance.targets.echomem.probes import commit_recovery
from performance.targets.echomem.probes.docker_inspect import inspect_container


def run(*, base_url: str, seed_directory: Path, output: Path, container: str,
        kill_delays_s: list[float], message_counts: list[int],
        recovery_timeout_s: float = 240) -> dict:
    if output.exists():
        raise FileExistsError("Refuse to overwrite recovery-matrix evidence")
    if not kill_delays_s or len(kill_delays_s) != len(message_counts):
        raise ValueError("kill delays and message counts must be non-empty and have equal length")
    if any(delay < 0 for delay in kill_delays_s) or any(not 1 <= count <= 100 for count in message_counts):
        raise ValueError("invalid recovery matrix values")
    state = inspect_container(container)
    if (not state.get("State", {}).get("Running")
            or state["HostConfig"].get("NanoCpus") != 4_000_000_000
            or state["HostConfig"].get("Memory") != 8_589_934_592):
        raise ValueError("A running dedicated 4CPU/8GiB target is required")
    actors, _ = _load_actors(seed_directory, base_url)
    actor = actors[0]
    output.mkdir(parents=True, mode=0o700)
    config = output / "tenant.private.json"
    _write(config, {"tenants": [{key: getattr(actor.client, key) for key in
           ("tenant_id", "user_id", "auth_key", "account_id", "agent_id")}]}, private=True)
    report = {"status": "RUNNING", "expected_samples": len(kill_delays_s),
              "performance_requirements_applied": False, "samples": [], "current": None}
    _write(output / "report.json", report, private=True)
    for index, (delay, messages) in enumerate(zip(kill_delays_s, message_counts), 1):
        report["current"] = {"sample": index, "kill_delay_s": delay, "messages": messages}
        _write(output / "report.json", report, private=True)
        profile = SimpleNamespace(
            target=SimpleNamespace(base_url=base_url, headers={}, read_timeout_s=10),
            tenants=actors,
            params={"container": container, "tenant_config": str(config),
                    "tenant": actor.client.tenant_id, "messages": messages,
                    "content_chars": 1000, "require_accepted_202": True,
                    "kill_delay_s": delay, "recovery_timeout_s": recovery_timeout_s},
        )
        result = ProbeRunner(profile, ProbeModule("commit-recovery", "", commit_recovery.run, ())).run()
        checks = [asdict(check) for check in result.checks]
        sample = {"sample": index, "kill_delay_s": delay, "messages": messages,
                  "elapsed_s": result.elapsed_s, "checks": checks,
                  "status": "PASS" if checks and all(check["status"] == "PASS" for check in checks)
                  else "FAIL" if any(check["status"] == "FAIL" for check in checks)
                  else "INCONCLUSIVE"}
        report["samples"].append(sample)
        _write(output / f"sample-{index:02d}.json", sample, private=True)
        _write(output / "report.json", report, private=True)
    passed = sum(sample["status"] == "PASS" for sample in report["samples"])
    report.update(status="PASS" if passed == report["expected_samples"] else
                  "FAIL" if any(sample["status"] == "FAIL" for sample in report["samples"])
                  else "INCONCLUSIVE", current=None, passed_samples=passed)
    _write(output / "report.json", report, private=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--seed-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--kill-delays-s", default="0.0,0.2,1.0")
    parser.add_argument("--message-counts", default="8,12,20")
    parser.add_argument("--recovery-timeout-s", type=float, default=240)
    args = parser.parse_args()
    result = run(base_url=args.base_url, seed_directory=args.seed_directory,
                 output=args.output, container=args.container,
                 kill_delays_s=[float(value) for value in args.kill_delays_s.split(",")],
                 message_counts=[int(value) for value in args.message_counts.split(",")],
                 recovery_timeout_s=args.recovery_timeout_s)
    print(json.dumps({"status": result["status"], "passed": result["passed_samples"],
                      "total": result["expected_samples"]}))


if __name__ == "__main__":
    main()
