"""M1 capacity evidence: delivery, class/identity SLO, boundaries and DAU."""

from __future__ import annotations

import math
import random
from collections import Counter

from performance.stats import percentile


def wilson(success: int, count: int) -> list[float] | None:
    if not count:
        return None
    z = 1.959963984540054
    p = success / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count**2)) / denominator
    return [max(0, center - half), min(1, center + half)]


def block_p95_interval(rows: list[dict], *, seed: int = 42, repeats: int = 300) -> list[float] | None:
    blocks = {}
    for row in rows:
        if row.get("sent") and row.get("elapsed_s") is not None:
            blocks.setdefault(int(row.get("start_s", 0) // 10), []).append(row["elapsed_s"])
    if len(blocks) < 5:
        return None
    groups = list(blocks.values())
    rng = random.Random(seed)
    values = [percentile([v for _ in groups for v in rng.choice(groups)], 95) for _ in range(repeats)]
    return [percentile(values, 2.5), percentile(values, 97.5)]


def search_summary(rows: list[dict]) -> dict:
    sent = [r for r in rows if r.get("sent")]
    successful = [r for r in sent if r.get("success")]
    latency = [r["elapsed_s"] for r in sent if r.get("elapsed_s") is not None]
    atomic = [engine.get("duration_seconds") for row in sent for engine in row.get("engine_results", [])
              if engine.get("engine_id") == "atomic_engine" and engine.get("duration_seconds") is not None]
    engine_values: dict[str, list[float]] = {}
    residual = []
    for row in sent:
        durations = []
        for engine in row.get("engine_results", []):
            duration = engine.get("duration_seconds")
            if engine.get("engine_id") and duration is not None:
                engine_values.setdefault(engine["engine_id"], []).append(duration)
                durations.append(duration)
        if row.get("elapsed_s") is not None:
            residual.append(max(0, row["elapsed_s"] - sum(durations)))
    engine_timings = {engine: {"observations": len(values),
                               "mean_s": sum(values) / len(values),
                               "p95_s": percentile(values, 95), "max_s": max(values)}
                      for engine, values in sorted(engine_values.items())}
    codes = Counter(str(row.get("http_status")) for row in sent)
    reasons = Counter(str(reason) for row in sent for reason in row.get("degraded_reasons", []))
    fact_observed = [row for row in sent if row.get("matched_expected_fact") is not None]
    hit_observed = [row for row in sent if row.get("hit_count") is not None]
    atomic_fact_rows = [row for row in sent if row.get("atomic_fact_hit") is not None]
    concurrency = [(r["start_s"], 1) for r in sent if "start_s" in r and "end_s" in r]
    concurrency.extend((r["end_s"], -1) for r in sent if "start_s" in r and "end_s" in r)
    outstanding, peak = 0, 0
    for _, change in sorted(concurrency):
        outstanding += change
        peak = max(peak, outstanding)
    return {"planned": len(rows), "sent": len(sent), "success": len(successful),
            "not_sent": len(rows) - len(sent), "delivery_rate": len(sent) / len(rows) if rows else None,
            "quality_rate": len(successful) / len(sent) if sent else None,
            "quality_wilson_95": wilson(len(successful), len(sent)),
            "errors": len(sent) - len(successful), "degraded": sum(bool(r.get("degraded")) for r in sent),
            "http_status_counts": dict(codes), "degraded_reason_counts": dict(reasons),
            "http_reason_counts": dict(Counter(r["reason_code"] for r in sent if r.get("reason_code"))),
            "transport_or_http_errors": sum(row.get("http_status") != 200 for row in sent),
            "fact_hit_observations": len(fact_observed),
            "fact_hits": sum(bool(row["matched_expected_fact"]) for row in fact_observed),
            "result_count_observations": len(hit_observed),
            "empty_results": sum(row["hit_count"] == 0 for row in hit_observed),
            "nonempty_results": sum(row["hit_count"] > 0 for row in hit_observed),
            "atomic_fact_observations": len(atomic_fact_rows),
            "atomic_fact_hits": sum(bool(r["atomic_fact_hit"]) for r in atomic_fact_rows),
            "peak_inflight_requests": peak if concurrency else None,
            "timeout_censored": sum(bool(r.get("timeout_censored")) for r in sent),
            "mean_s": sum(latency) / len(latency) if latency else None,
            "p50_s": percentile(latency, 50), "p95_s": percentile(latency, 95), "p99_s": percentile(latency, 99),
            "success_p95_s": percentile([r["elapsed_s"] for r in successful], 95),
            "atomic_p95_s": percentile(atomic, 95), "atomic_observations": len(atomic),
            "engine_timings": engine_timings,
            # This is endpoint time minus reported engine durations.  It
            # includes routing, model calls, serialization and unreported
            # work, so it is a diagnostic residual rather than a direct LLM
            # timer.
            "unattributed_residual_p95_s": percentile(residual, 95),
            "p95_block_bootstrap_95": block_p95_interval(sent),
            "generator_lag_p95_s": percentile([r.get("generator_lag_s", 0) for r in sent], 95)}


def evaluate_level(measurement: dict, *, confirmation: bool = False,
                   assessment_mode: str = "observe") -> dict:
    if assessment_mode not in {"observe", "slo"}:
        raise ValueError("assessment_mode must be observe or slo")
    reads = [r for r in measurement["rows"] if r["op"] == "read"]
    classes = ("recall", "no_recall") if measurement["mixed"] else ("recall",)
    cells = []
    for identity in range(measurement["identity_count"]):
        for kind in classes:
            selected = [r for r in reads if r["identity_index"] == identity and r.get("query_type") == kind]
            summary = search_summary(selected)
            summary.update(identity_index=identity, query_type=kind)
            cells.append(summary)
    total = search_summary(reads)
    enough = all(c["sent"] >= (100 if confirmation else 20) for c in cells)
    delivered = total["delivery_rate"] is not None and total["delivery_rate"] >= .95
    healthy = all(c["quality_rate"] is not None and c["quality_rate"] >= .99
                  and c["p95_s"] is not None and c["p95_s"] < 2.5
                  and (c["query_type"] == "no_recall" or
                       c["atomic_p95_s"] is not None and c["atomic_p95_s"] < 2)
                  for c in cells)
    submissions = [r for r in measurement["rows"] if r["op"] == "commit_submit"]
    accepted = [r for r in submissions if r.get("accepted_202")]
    done = [r for r in measurement["rows"] if r["op"] == "commit_done"]
    successful = [r for r in done if r.get("success")]
    commit = {"submitted": len(submissions), "accepted_202": len(accepted), "completed": len(successful),
              "completion_rate": len(successful) / len(accepted) if accepted else None,
              "p95_s": percentile([r["elapsed_s"] for r in done], 95),
              "unfinished_or_failed": len(accepted) - len(successful)}
    backlog = []
    for at in range(0, math.ceil(measurement["duration_s"]) + 1, 10):
        outstanding = sum(r.get("accepted_at_s", float("inf")) <= at for r in accepted) - sum(r.get("end_s", float("inf")) <= at for r in done)
        backlog.append({"at_s": at, "pending": max(0, outstanding)})
    tail = backlog[-max(3, len(backlog) // 3):]
    growing = len(tail) >= 3 and all(b["pending"] > a["pending"] for a, b in zip(tail, tail[1:]))
    commit["backlog"] = backlog
    commit["tail_continuously_growing"] = growing
    commit["not_accepted"] = len(submissions) - len(accepted)
    receipts = measurement.get("commit_receipts")
    commit["unique_accepted_tasks"] = (len({(r["session_id"], r["archive_id"]) for r in receipts})
                                       if receipts is not None else None)
    commit["completed_in_window"] = sum(r.get("completed_in_window", r.get("end_s", float("inf")) <= measurement["duration_s"])
                                        for r in successful)
    commit["completed_rps"] = commit["completed_in_window"] / measurement["duration_s"]
    commit["completion_deadline_s"] = measurement.get("commit_deadline_s")
    commit["terminal_status_counts"] = dict(Counter(str(r.get("status")) for r in done))
    common = {"assessment_mode": assessment_mode, "confirmation": confirmation,
              "search": total, "cells": cells, "commit": commit,
              "effective_search_rps": total["success"] / measurement["duration_s"],
              "sent_search_rps": total["sent"] / measurement["duration_s"],
              "duration_s": measurement["duration_s"], "identity_count": measurement["identity_count"],
              "tenant_count": measurement["tenant_count"], "mixed": measurement["mixed"],
              "per_user_search_rps": measurement.get("per_user_search_rps"),
              "per_user_commit_interval_s": measurement.get("per_user_commit_interval_s"),
              "elapsed_with_drain_s": measurement.get("elapsed_with_drain_s")}
    if assessment_mode == "observe":
        common.update(status="MEASURED" if total["sent"] else "NO_DATA",
                      all_identities_and_classes_sampled=all(c["sent"] > 0 for c in cells),
                      performance_requirements_applied=False)
        return common
    if measurement["mixed"]:
        covered = {r["identity_index"] for r in accepted}
        enough = enough and len(covered) == measurement["identity_count"] and measurement["duration_s"] >= 300
        healthy = healthy and commit["completion_rate"] is not None and commit["completion_rate"] >= .99 and commit["p95_s"] <= 180 and not growing
    uncertainty = confirmation and any(
        c["p95_block_bootstrap_95"] is None or c["quality_wilson_95"] is None
        or c["p95_block_bootstrap_95"][0] < 2.5 <= c["p95_block_bootstrap_95"][1]
        or c["quality_wilson_95"][0] < .99 <= c["quality_wilson_95"][1] for c in cells)
    status = "INCONCLUSIVE" if not enough or not delivered or uncertainty else "PASS" if healthy else "FAIL"
    return {**common, "status": status, "all_identities_and_classes_sampled": enough,
            "generator_delivery_valid": delivered, "slo_observed": healthy,
            "observed_service_violations": total["errors"],
            "statistical_boundary_uncertain": uncertainty}
