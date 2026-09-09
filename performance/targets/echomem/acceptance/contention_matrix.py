"""Repeat M3 fairness, M4 priority and M6 observability under real Commit floods."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

from performance.targets.echomem.acceptance.capacity_experiment import _load_actors, _write
from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.main_metric_samples import flood, summarize_flood
from performance.targets.echomem.probes.docker_inspect import inspect_container
from performance.targets.echomem.probes.tenant_observability import collect


def run(*, base_url: str, seed_directory: Path, output: Path, container: str,
        expected_lanes: list[str], repeats: int = 3, duration_s: float = 60,
        q: float = 1, commits_per_tenant: int = 8, commit_timeout_s: float = 180,
        commit_submit_rps: float = 0,
        token_env: str = "ECHOMEM_TEST_CONTROL_TOKEN", max_sampling_gap_s: float = 15) -> dict:
    if output.exists():
        raise FileExistsError("Refuse to overwrite contention-matrix evidence")
    if repeats < 1 or not 15 <= duration_s <= 120 or q <= 0:
        raise ValueError("invalid repeat, duration or Search rate")
    if not 1 <= commits_per_tenant <= 64 or not 10 <= commit_timeout_s <= 600:
        raise ValueError("invalid Commit matrix settings")
    if type(commit_submit_rps) not in (int, float) or not math.isfinite(commit_submit_rps) or commit_submit_rps < 0:
        raise ValueError("Commit submission rate must be finite and nonnegative")
    if commit_submit_rps and 5 + (4 * commits_per_tenant - 1) / commit_submit_rps >= duration_s:
        raise ValueError("The planned Commit submissions must fit inside the Search window")
    if not math.isfinite(max_sampling_gap_s) or max_sampling_gap_s <= 0:
        raise ValueError("max sampling gap must be positive and finite")
    state = inspect_container(container)
    if (not state.get("State", {}).get("Running")
            or state["HostConfig"].get("NanoCpus") != 4_000_000_000
            or state["HostConfig"].get("Memory") != 8_589_934_592):
        raise ValueError("A running dedicated 4CPU/8GiB target is required")
    actors, seed = _load_actors(seed_directory, base_url)
    actors = actors[:4]
    if len(actors) != 4 or len({a.client.tenant_id for a in actors}) != 4:
        raise ValueError("Four independently authenticated seeded tenants are required")
    if len({a.client.auth_key for a in actors}) != 4 or any(not a.client.auth_key for a in actors):
        raise ValueError("Four independent non-empty authentication keys are required")
    token = os.environ.get(token_env, "")
    if not token:
        raise ValueError("Tenant observability token is required")
    output.mkdir(parents=True, mode=0o700)
    tenants = [actor.client.tenant_id for actor in actors]

    def snapshot():
        observed_at = time.monotonic()
        result = collect(base_url=base_url, endpoint="", token=token,
                         expected_tenants=tenants, expected_lanes=expected_lanes, timeout_s=10)
        result["observed_at_s"] = observed_at
        return result

    report = {"status": "RUNNING", "expected_samples": repeats, "samples": [],
              "duration_s": duration_s, "search_rps_per_tenant": q,
              "commits_per_tenant": commits_per_tenant,
              "commit_submit_rps": commit_submit_rps,
              "commit_timeout_s": commit_timeout_s, "seed_status": seed.get("status"),
              "performance_requirements_applied": False, "current": None}
    _write(output / "report.json", report, private=True)
    for repeat in range(1, repeats + 1):
        root = output / f"repeat-{repeat:02d}"
        root.mkdir(mode=0o700)
        report["current"] = {"repeat": repeat, "phase": "prepare-commits"}
        _write(output / "report.json", report, private=True)
        sessions = []
        for turn in range(commits_per_tenant):
            for index, actor in enumerate(actors):
                sid, _ = actor.client.open_session(actor.client.tenant_id, "contention-matrix")
                added = actor.client.add_message(sid, uuid.uuid4().hex,
                                                  actor.corpus["documents"][turn % 5],
                                                  retry_rate_limit=True)
                if added.status_code not in (200, 201):
                    raise RuntimeError("Commit setup rejected; no contention conclusion")
                sessions.append((index, sid))
        before = snapshot()
        _write(root / "observability-before.json", before, private=True)
        report["current"]["phase"] = "search-baseline"
        _write(output / "report.json", report, private=True)
        baseline = measure(actors, duration_s=duration_s, q=q, seed=71000 + repeat)
        _write(root / "search-baseline.json", baseline, private=True)
        observations = []
        monitor_errors = []
        stopped = threading.Event()

        def monitor():
            try:
                while not stopped.is_set():
                    observations.append(snapshot())
                    _write(root / "observability-during.json", observations, private=True)
                    stopped.wait(2)
            except Exception as exc:
                monitor_errors.append(type(exc).__name__)

        report["current"]["phase"] = "commit-flood-and-search"
        _write(output / "report.json", report, private=True)
        thread = threading.Thread(target=monitor, daemon=True)
        window_start = time.monotonic()
        thread.start()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                committing = pool.submit(flood, actors, sessions,
                                         commit_timeout_s=commit_timeout_s, commit_submit_rps=commit_submit_rps)
                loaded = measure(actors, duration_s=duration_s, q=q, seed=71000 + repeat)
                commits_rows = committing.result()
        finally:
            stopped.set()
            thread.join()
        window_end = time.monotonic()
        _write(root / "search-loaded.json", loaded, private=True)
        _write(root / "commits.json", commits_rows, private=True)
        after = snapshot()
        _write(root / "observability-after.json", after, private=True)
        before_rows = {(row["tenant_id"], row["lane"]): row for row in before.get("rows", [])}
        for row in after.get("rows", []):
            queue = [item.get("queued") for sample in observations for item in sample.get("rows", [])
                     if item.get("tenant_id") == row["tenant_id"] and item.get("lane") == row["lane"]
                     and isinstance(item.get("queued"), (int, float))]
            row["queued_peak_during_load"] = max(queue, default=None)
            prior = before_rows.get((row["tenant_id"], row["lane"]), {})
            row["accepted_delta"] = (row.get("accepted_total") - prior.get("accepted_total")
                if isinstance(row.get("accepted_total"), (int, float))
                and isinstance(prior.get("accepted_total"), (int, float)) else None)
        after["sample_count"] = len(observations)
        after["process_observations"] = {
            "expected_tenants": tenants, "expected_lanes": expected_lanes,
            "before": before, "during": observations, "after": dict(after),
            "window_start_s": window_start, "window_end_s": window_end,
            "max_sampling_gap_s": max_sampling_gap_s, "monitor_errors": monitor_errors,
        }
        sample = {"repeat": repeat, "M3_M4": summarize_flood(
                      baseline, loaded, commits_rows, 4), "M6": after}
        sample["M3_M4"]["submission_schedule"] = {
            "mode": "paced" if commit_submit_rps else "burst", "requested_rps": commit_submit_rps,
            "planned_span_s": (len(sessions) - 1) / commit_submit_rps if commit_submit_rps else 0,
            "submission_workers": 32, "poll_workers": len(sessions), "poll_interval_s": 1,
            "commit_timeout_s": commit_timeout_s}
        report["samples"].append(sample)
        _write(root / "summary.json", sample, private=True)
        _write(output / "report.json", report, private=True)
    report.update(status="MEASURED" if len(report["samples"]) == repeats else "INCONCLUSIVE",
                  current=None, measured_samples=len(report["samples"]),
                  commit_jain_values=[sample["M3_M4"].get("commit_jain") for sample in report["samples"]],
                  search_jain_values=[sample["M3_M4"].get("search_inverse_p95_jain") for sample in report["samples"]])
    _write(output / "report.json", report, private=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--seed-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--expected-lanes", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--duration-s", type=float, default=60)
    parser.add_argument("--search-rps", type=float, default=1)
    parser.add_argument("--commits-per-tenant", type=int, default=8)
    parser.add_argument("--commit-timeout-s", type=float, default=180)
    parser.add_argument("--commit-submit-rps", type=float, default=0,
                        help="Total Commit submissions/s across tenants; 0 keeps the burst workload")
    parser.add_argument("--max-sampling-gap-s", type=float, default=15,
                        help="Monitoring coverage limit, not a Search latency SLO")
    args = parser.parse_args()
    result = run(base_url=args.base_url, seed_directory=args.seed_directory,
                 output=args.output, container=args.container,
                 expected_lanes=args.expected_lanes.split(","), repeats=args.repeats,
                 duration_s=args.duration_s, q=args.search_rps,
                 commits_per_tenant=args.commits_per_tenant,
                 commit_timeout_s=args.commit_timeout_s, commit_submit_rps=args.commit_submit_rps,
                 max_sampling_gap_s=args.max_sampling_gap_s)
    print(json.dumps({"status": result["status"], "samples": result["measured_samples"]}))


if __name__ == "__main__":
    main()
