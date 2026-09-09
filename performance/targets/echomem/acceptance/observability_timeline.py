"""Audit every monitoring snapshot against a fixed tenant/lane contract."""

import math
from datetime import datetime


def _time(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _epoch(snapshot):
    if snapshot.get("boot_id"):
        return ("boot", snapshot["boot_id"])
    if snapshot.get("process_started_at"):
        return ("process", snapshot.get("process_id"), snapshot["process_started_at"])
    return None


def _snapshot_time(snapshot):
    if _time(snapshot.get("observed_at_s")):
        return snapshot["observed_at_s"], "monotonic"
    try:
        value = datetime.fromisoformat(snapshot.get("created_at", "").replace("Z", "+00:00"))
        if value.tzinfo is not None:
            return value.timestamp(), "wall_clock"
    except (TypeError, ValueError, AttributeError):
        pass
    return None, None


def timeline_counts(evidence: dict, *, allow_restarts: bool = False) -> dict:
    from performance.targets.echomem.acceptance.reliability_evidence import observability_counts

    tenants = evidence.get("expected_tenants", [])
    lanes = evidence.get("expected_lanes", [])
    contract_valid = all(isinstance(values, list) and bool(values)
                         and all(isinstance(v, str) and bool(v.strip()) for v in values)
                         and len(set(values)) == len(values) for values in (tenants, lanes))
    if not contract_valid:
        tenants, lanes = [], []
    frames = [("before", evidence.get("before"))]
    frames += [("during", item) for item in evidence.get("during", [])]
    frames += [("after", evidence.get("after"))]
    counters = ("wait_seconds_total", "exec_seconds_total", "rejected_total",
                "accepted_total", "completed_total", "failed_total")
    snapshots, regressions, restarts = [], [], []
    previous = None
    comparisons = 0
    for index, (phase, raw) in enumerate(frames):
        raw = raw if isinstance(raw, dict) else {}
        # The expected denominator belongs to the experiment, not to each response.
        locked = {key: value for key, value in raw.items()
                  if key not in {"process_observations", "repeat_observations"}}
        locked.update(expected_tenants=tenants, expected_lanes=lanes)
        public = observability_counts(locked)
        observed_at, clock = _snapshot_time(raw)
        snapshots.append({"index": index, "phase": phase, "status": public["status"],
                          "expected_cells": public["expected_cells"], "valid_cells": public["valid_cells"],
                          "missing_cells": public["missing_cells"], "invalid_cells": public["invalid_cells"],
                          "duplicate_cells": public["duplicate_cells"],
                          "observed_at_s": observed_at, "clock": clock,
                          "missing_details": public["missing_details"],
                          "invalid_details": public["invalid_details"]})
        if public["status"] != "PASS":
            previous = None
            continue
        current = {(r["tenant"], r["lane"]): r for r in public["rows"]}
        epoch = _epoch(raw)
        if previous:
            old_index, old_epoch, old_rows, old_time, old_clock = previous
            if observed_at is None or old_time is None or clock != old_clock or observed_at <= old_time:
                previous = index, epoch, current, observed_at, clock
                continue
            restarted = epoch is not None and old_epoch is not None and epoch != old_epoch
            if restarted:
                restarts.append({"before_index": old_index, "after_index": index})
            for key, row in current.items():
                prior = old_rows.get(key, {})
                for field in counters:
                    before, after = prior.get(field), row.get(field)
                    if not _time(before) or not _time(after):
                        continue
                    comparisons += 1
                    if after < before:
                        regressions.append({"tenant": key[0], "lane": key[1], "counter": field,
                                            "before_index": old_index, "after_index": index,
                                            "before": before, "after": after,
                                            "classification": "restart" if restarted else
                                            "same_process" if epoch is not None and epoch == old_epoch else
                                            "process_identity_unknown"})
        previous = index, epoch, current, observed_at, clock
    during = [s for s in snapshots if s["phase"] == "during"]
    start, end = evidence.get("window_start_s"), evidence.get("window_end_s")
    limit = evidence.get("max_sampling_gap_s")
    times = [s["observed_at_s"] for s in during]
    clocks = {s["clock"] for s in during}
    interval_valid = bool(times and all(_time(t) for t in times)
                          and len(clocks) == 1 and None not in clocks
                          and all(a < b for a,b in zip(times, times[1:])))
    internal_gaps = [b-a for a,b in zip(times,times[1:])] if interval_valid else []
    time_valid = bool(interval_valid and clocks == {"monotonic"} and _time(start) and _time(end)
                      and start <= times[0] <= times[-1] <= end
                      and all(a < b for a, b in zip(times, times[1:])))
    gaps = [b-a for a, b in zip([start, *times], [*times, end])] if time_valid else []
    gap_valid = bool(time_valid and _time(limit) and limit > 0)
    exceeded = sum(gap > limit for gap in gaps) if gap_valid else None
    coverage_complete = bool(tenants and lanes and during
                             and all(s["status"] == "PASS" for s in snapshots))
    unexplained = any(r["classification"] == "same_process" for r in regressions)
    identities_complete = all(_epoch(raw) is not None for _, raw in frames if isinstance(raw, dict))
    explained_restarts = bool(allow_restarts and identities_complete
                              and all(r["classification"] == "restart" for r in regressions))
    status = ("FAIL" if unexplained or any(s["status"] == "FAIL" for s in snapshots) else
              "PASS" if coverage_complete and gap_valid and exceeded == 0 and comparisons
              and ((not regressions and not restarts) or explained_restarts)
              and (not allow_restarts or identities_complete)
              and not evidence.get("monitor_errors") else "INCONCLUSIVE")
    return {"status": status, "contract_valid": contract_valid,
            "snapshot_count": len(snapshots), "during_count": len(during),
            "passed_snapshots": sum(s["status"] == "PASS" for s in snapshots),
            "snapshots": snapshots, "counter_comparisons": comparisons,
            "counter_regressions": regressions, "restart_observations": restarts,
            "process_identity_observations": sum(_epoch(raw) is not None for _,raw in frames if isinstance(raw,dict)),
            "max_gap_s": max(gaps) if gaps else None,
            "max_internal_gap_s": max(internal_gaps) if internal_gaps else None,
            "sampling_clock": next(iter(clocks)) if len(clocks) == 1 else None,
            "max_sampling_gap_s": limit if _time(limit) else None, "gaps_exceeded": exceeded,
            "sampling_times_valid": time_valid,
            "monitor_failed": bool(evidence.get("monitor_errors")),
            "restarts_allowed": allow_restarts,
            "process_identities_complete": identities_complete,
            "scope": "sampled snapshots only; activity between samples is not fully observed"}
