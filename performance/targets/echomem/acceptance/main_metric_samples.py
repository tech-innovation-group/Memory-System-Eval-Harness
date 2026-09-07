"""Short real-service M2-M6 samples; observations, not full-matrix acceptance."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import uuid

from performance.probe import ProbeModule, ProbeRunner
from performance.targets.echomem.acceptance.capacity_experiment import _load_actors, _write
from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.capacity_statistics import evaluate_level, search_summary
from performance.targets.echomem.acceptance.six_metrics import jain
from performance.targets.echomem.probes import commit_recovery
from performance.targets.echomem.probes._client import extract_archive, status_from
from performance.targets.echomem.probes.docker_inspect import inspect_container
from performance.targets.echomem.probes.fault_isolation import control
from performance.targets.echomem.probes.tenant_observability import collect


def comparison(before: dict, during: dict, identities: int) -> list[dict]:
    rows = []
    for i in range(identities):
        pair = [search_summary([r for r in run["rows"] if r.get("op") == "read"
                                and r.get("identity_index") == i]) for run in (before, during)]
        b, d = (r["p95_s"] for r in pair)
        rows.append({"identity_index": i, "before": pair[0], "during": pair[1],
                     "p95_degradation_percent": (d / b - 1) * 100 if b and d is not None else None})
    return rows


def flood(actors: list, sessions: list[tuple], *, delay_s: float = 5,
          commit_timeout_s: float = 180) -> list[dict]:
    time.sleep(delay_s)

    def submit(item):
        index, sid = item
        client = actors[index].client
        row = {"identity_index": index, "submit_at": time.monotonic()}
        try:
            result = client.commit(sid, idempotency_key="main-sample-" + uuid.uuid4().hex)
            archive = extract_archive(result.payload)
            row.update(http_status=result.status_code, accepted_202=result.status_code == 202 and bool(archive),
                       accepted_at=time.monotonic())
            if not row["accepted_202"]:
                return row
            deadline = time.monotonic() + commit_timeout_s
            while time.monotonic() < deadline:
                response = client.request("GET", f"/api/sessions/{sid}/commits/{archive}",
                    timeout_s=min(10, max(.01, deadline - time.monotonic())), operation="commit_poll")
                state = status_from(response.payload)
                row["state"] = state
                if response.status_code == 200 and state in {"completed", "failed", "error"}:
                    row.update(completed=state == "completed", terminal_at=time.monotonic())
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            row["observed_until"] = time.monotonic()
        except Exception as exc:
            row["error_class"] = type(exc).__name__
        return row

    with ThreadPoolExecutor(max_workers=32) as pool:
        return list(pool.map(submit, sessions))


def summarize_flood(baseline: dict, loaded: dict, commits: list[dict], identities: int) -> dict:
    start = loaded["started_at_monotonic_s"]
    end = start + loaded["duration_s"]
    accepted = [c for c in commits if c.get("accepted_202")]
    intervals = [(c["accepted_at"] - start,
                  c.get("terminal_at", c.get("observed_until", end)) - start) for c in accepted]
    # A Search belongs to the real overlap window only when its request start
    # falls between a server-accepted Commit and that Commit's observed
    # terminal time. Merely finishing after a Commit was accepted would
    # over-count slow requests that actually started before the flood.
    overlap = [r for r in loaded["rows"] if r.get("op") == "read" and r.get("sent")
               and any(a <= r["start_s"] <= b for a, b in intervals)]
    tenants = []
    for i in range(identities):
        completed = sum(c.get("completed", False) and c.get("terminal_at", end + 1) <= end
                        for c in commits if c["identity_index"] == i)
        selected = [r for r in loaded["rows"] if r.get("op") == "read" and r.get("identity_index") == i]
        tenants.append({"identity_index": i, "commit_completed_in_search_window": completed,
                        "commit_rps": completed / loaded["duration_s"], "search": search_summary(selected)})
    commit_rates = [t["commit_rps"] for t in tenants]
    latencies = [t["search"]["p95_s"] for t in tenants]
    return {"status": "MEASURED", "performance_requirements_applied": False,
            "commit_planned": len(commits), "accepted_202": len(accepted),
            "completed_including_drain": sum(bool(c.get("completed")) for c in commits),
            "unresolved_after_observation": sum(
                c.get("accepted_202", False) and not c.get("terminal_at") for c in commits
            ),
            "search_window_s": loaded["duration_s"], "tenants": tenants,
            "commit_jain": jain(commit_rates),
            "search_inverse_p95_jain": jain([1 / p for p in latencies]) if all(latencies) else None,
            "paired": comparison(baseline, loaded, identities), "overlap_search": search_summary(overlap),
            "strict_server_scheduling_proven": False,
            "scope": "one equal-load window; completion throughput excludes post-window drain"}


def run(*, base_url: str, seed_directory: Path, output: Path, container: str,
        expected_lanes: list[str], token_env: str = "ECHOMEM_TEST_CONTROL_TOKEN",
        duration_s: float = 60, q: float = 1, allow_container_restart: bool = False,
        fault_target_index: int = 0, fault_type: str = "reject", fault_delay_ms: int = 1000,
        commits_per_tenant: int = 8, commit_timeout_s: float = 180,
        recovery_kill_delay_s: float = .2, recovery_messages: int = 8) -> dict:
    if output.exists():
        raise FileExistsError("Refuse to overwrite main-metric evidence")
    if duration_s < 15 or duration_s > 120 or q <= 0:
        raise ValueError("Short sample duration must be 15..120 seconds and q must be positive")
    if fault_target_index not in range(4) or fault_type not in {"reject", "delay"}:
        raise ValueError("fault target must be T1..T4 and type must be reject or delay")
    if not 1 <= commits_per_tenant <= 64 or not 10 <= commit_timeout_s <= 600:
        raise ValueError("commits_per_tenant must be 1..64 and commit_timeout_s 10..600")
    if not 1 <= recovery_messages <= 100 or recovery_kill_delay_s < 0:
        raise ValueError("invalid recovery sample settings")
    state = inspect_container(container)
    if not state.get("State", {}).get("Running") or state["HostConfig"].get("NanoCpus") != 4_000_000_000 or state["HostConfig"].get("Memory") != 8_589_934_592:
        raise ValueError("A running, dedicated 4CPU/8GiB target is required")
    actors, seed = _load_actors(seed_directory, base_url)
    actors = actors[:4]
    if len(actors) != 4 or len({a.client.tenant_id for a in actors}) != 4:
        raise ValueError("Four independently authenticated seeded tenants required")
    if len({a.client.auth_key for a in actors}) != 4 or any(not a.client.auth_key for a in actors):
        raise ValueError("Independent non-empty authentication keys required")
    output.mkdir(parents=True, mode=0o700)
    token = os.environ.get(token_env, "")
    report = {"status": "RUNNING", "scope": "main samples, not full six-metric acceptance",
              "duration_s": duration_s, "per_tenant_search_rps": q,
              "real_models_required": True, "performance_requirements_applied": False,
              "seed_status": seed.get("status"), "metrics": {}}

    def save(name, value):
        _write(output / (name + ".json"), value, private=True)

    def phase(name):
        report["current"] = name
        save("report", report)

    def snapshot():
        if not token:
            return {"status": "INCONCLUSIVE", "reason": "test-control-token-unavailable"}
        return collect(base_url=base_url, endpoint="", token=token,
                       expected_tenants=[a.client.tenant_id for a in actors],
                       expected_lanes=expected_lanes, timeout_s=10)

    phase("observability-before")
    observability_before = snapshot()
    save("observability-before", observability_before)
    phase("fault-baseline")
    baseline = measure(actors, duration_s=duration_s, q=q, seed=31001)
    save("fault-baseline", baseline)
    endpoint = base_url.rstrip("/") + "/api/inspect/test-control/fault"
    target = actors[fault_target_index]
    if token:
        phase("fault-during")
        fault_started = time.monotonic()
        cleanup_failed = False
        enabled = control({"endpoint": endpoint}, action="enable", target_tenant=target.client.tenant_id,
                          timeout_s=10, token=token, fault_type=fault_type,
                          delay_ms=fault_delay_ms, duration_s=duration_s + 30)
        save("fault-enabled", enabled)
        try:
            if enabled.get("status") == "PASS":
                during = measure(actors, duration_s=duration_s, q=q, seed=31001)
                save("fault-during", during)
                pairs = comparison(baseline, during, 4)
                target_errors = pairs[fault_target_index]["during"]["transport_or_http_errors"]
                fault_effect = target_errors if fault_type == "reject" else pairs[fault_target_index].get("p95_degradation_percent")
                report["metrics"]["M2"] = {"status": "MEASURED" if fault_effect else "INCONCLUSIVE",
                    "target_index": fault_target_index, "fault_type": fault_type,
                    "fault_delay_ms": fault_delay_ms if fault_type == "delay" else None,
                    "pairs": pairs, "scope": "one target and one fault type; three bystanders",
                    "target_http_errors": target_errors,
                    "fault_window_covered": time.monotonic() - fault_started < duration_s + 30,
                    "baseline_strict_valid": baseline.get("rows") is not None and
                        all(r.get("success") for r in baseline["rows"] if r.get("op") == "read")}
            else:
                report["metrics"]["M2"] = {"status": "INCONCLUSIVE", "reason": "fault-control-not-connected"}
        finally:
            disabled = control({"endpoint": endpoint}, action="disable", target_tenant=target.client.tenant_id,
                               timeout_s=10, token=token)
            save("fault-disabled", disabled)
            if disabled.get("status") != "PASS":
                report.update(status="INCONCLUSIVE", current="fault-cleanup-failed")
                save("report", report)
                cleanup_failed = True
        if cleanup_failed:
            return report
    else:
        report["metrics"]["M2"] = {"status": "INCONCLUSIVE", "reason": "test-control-token-unavailable"}

    phase("prepare-equal-commits")
    sessions = []
    for turn in range(commits_per_tenant):
        for index, actor in enumerate(actors):
            sid, _ = actor.client.open_session(actor.client.tenant_id, "equal-commit-sample")
            result = actor.client.add_message(sid, uuid.uuid4().hex, actor.corpus["documents"][turn % 5], retry_rate_limit=True)
            if result.status_code not in (200, 201):
                raise RuntimeError("Commit setup rejected; no performance conclusion")
            sessions.append((index, sid))
    phase("priority-baseline")
    baseline = measure(actors, duration_s=duration_s, q=q, seed=31002)
    save("priority-baseline", baseline)
    phase("priority-and-fairness")
    samples = []
    monitor_stop = threading.Event()

    def monitor():
        while not monitor_stop.is_set():
            samples.append(snapshot())
            save("observability-samples", samples)
            monitor_stop.wait(2)

    monitoring = threading.Thread(target=monitor, daemon=True)
    monitoring.start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            committing = pool.submit(flood, actors, sessions, commit_timeout_s=commit_timeout_s)
            loaded = measure(actors, duration_s=duration_s, q=q, seed=31002)
            save("priority-loaded", loaded)
            commits = committing.result()
    finally:
        monitor_stop.set()
        monitoring.join()
    save("flood-commits", commits)
    summary = summarize_flood(baseline, loaded, commits, 4)
    report["metrics"]["M3_M4"] = summary
    phase("observability-after")
    after = snapshot()
    save("observability-after", after)
    report["metrics"]["M6"] = after
    after["sample_count"] = len(samples)
    before_rows = {(r["tenant_id"], r["lane"]): r for r in observability_before.get("rows", [])}
    for row in after.get("rows", []):
        queued = [r["queued"] for s in samples for r in s.get("rows", [])
                  if r.get("tenant_id") == row["tenant_id"] and r.get("lane") == row["lane"]
                  and isinstance(r.get("queued"), (int, float))]
        row["queued_peak_during_load"] = max(queued, default=None)
        previous = before_rows.get((row["tenant_id"], row["lane"]), {})
        row["accepted_delta"] = (row["accepted_total"] - previous["accepted_total"]
            if isinstance(row.get("accepted_total"), (int, float)) and
               isinstance(previous.get("accepted_total"), (int, float)) else None)

    if allow_container_restart and not summary["unresolved_after_observation"]:
        phase("real-202-crash-recovery")
        config = output / "tenants.private.json"
        _write(config, {"tenants": [{key: getattr(a.client, key) for key in
               ("tenant_id", "user_id", "auth_key", "account_id", "agent_id")} for a in actors]}, private=True)
        profile = SimpleNamespace(target=SimpleNamespace(base_url=base_url, headers={}, read_timeout_s=10),
            tenants=actors, params={"container": container, "tenant_config": str(config),
            "tenant": actors[0].client.tenant_id, "messages": recovery_messages, "content_chars": 1000,
            "require_accepted_202": True, "kill_delay_s": recovery_kill_delay_s,
            "recovery_timeout_s": max(180, commit_timeout_s)})
        result = ProbeRunner(profile, ProbeModule("commit-recovery", "", commit_recovery.run, ())).run()
        recovery = {"checks": [asdict(c) for c in result.checks], "elapsed_s": result.elapsed_s}
        save("commit-recovery", recovery)
        report["metrics"]["M5"] = recovery
    else:
        report["metrics"]["M5"] = {"status": "INCONCLUSIVE", "reason":
            "flood-commits-unresolved-after-observation"
            if summary["unresolved_after_observation"]
            else "container-restart-not-authorized"}
    report.update(status="MEASURED", current=None)
    save("report", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--seed-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--expected-lanes", required=True)
    parser.add_argument("--allow-container-restart", action="store_true")
    parser.add_argument("--duration-s", type=float, default=60)
    parser.add_argument("--fault-target-index", type=int, default=0)
    parser.add_argument("--fault-type", choices=("reject", "delay"), default="reject")
    parser.add_argument("--fault-delay-ms", type=int, default=1000)
    parser.add_argument("--commits-per-tenant", type=int, default=8)
    parser.add_argument("--commit-timeout-s", type=float, default=180)
    parser.add_argument("--recovery-kill-delay-s", type=float, default=.2)
    parser.add_argument("--recovery-messages", type=int, default=8)
    args = parser.parse_args()
    result = run(base_url=args.base_url, seed_directory=args.seed_directory, output=args.output,
                 container=args.container, expected_lanes=args.expected_lanes.split(","),
                 allow_container_restart=args.allow_container_restart, duration_s=args.duration_s,
                 fault_target_index=args.fault_target_index, fault_type=args.fault_type,
                 fault_delay_ms=args.fault_delay_ms, commits_per_tenant=args.commits_per_tenant,
                 commit_timeout_s=args.commit_timeout_s,
                 recovery_kill_delay_s=args.recovery_kill_delay_s,
                 recovery_messages=args.recovery_messages)
    print(json.dumps({"status": result["status"], "metrics": list(result["metrics"])}))


if __name__ == "__main__":
    main()
