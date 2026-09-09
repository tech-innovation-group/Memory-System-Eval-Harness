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
        if row.get("sent") and _valid_duration(row.get("elapsed_s")):
            blocks.setdefault(int(row.get("start_s", 0) // 10), []).append(row["elapsed_s"])
    if len(blocks) < 5:
        return None
    groups = list(blocks.values())
    rng = random.Random(seed)
    values = [percentile([v for _ in groups for v in rng.choice(groups)], 95) for _ in range(repeats)]
    return [percentile(values, 2.5), percentile(values, 97.5)]


def _valid_duration(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _status_code(row: dict) -> int | None:
    value = row.get("http_status")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_ADMISSION_REASONS = {
    "retrieval_inflight_full", "tenant_concurrency_exceeded",
    "http_ingress_saturated", "http_lane_saturated", "tenant_rate_limited",
    "retrieval_busy", "commit_queue_full",
}


def _failure_domain(row: dict, status: int | None) -> str | None:
    if row.get("success"):
        return None
    if row.get("provider_error_code") or row.get("provider_failure_kind"):
        return "model_provider"
    if status is None:
        return ("network_transport" if row.get("transport_error_type") or row.get("error")
                or row.get("timeout_censored") else "unknown")
    if status in {401, 403}:
        return "echomem_auth"
    reason = str(row.get("reason_code") or "").strip().lower()
    if status in {429, 503, 504} and reason in _ADMISSION_REASONS:
        return "echomem_admission"
    if status != 200:
        return "echomem_http_unattributed"
    degraded_reasons = [str(value).lower() for value in row.get("degraded_reasons", [])]
    if row.get("degraded") and any("atomic_engine" in value for value in degraded_reasons):
        return "atomic_engine"
    if row.get("degraded"):
        return "routing_or_recall_orchestration"
    return "recall_quality"


def error_breakdown(sent: list[dict]) -> dict:
    """Build a complete, mutually exclusive Search outcome denominator."""
    status_counts = Counter()
    reason_counts = Counter()
    transport_types = Counter()
    failure_domains = Counter()
    provider_codes = Counter()
    partition = Counter()
    http_4xx = http_5xx = http_other = 0
    auth = rate_limited = timeout_censored = 0
    degraded_200 = quality_failed_200 = 0

    for row in sent:
        status = _status_code(row)
        success = bool(row.get("success"))
        if status is not None:
            status_counts[str(status)] += 1
        if row.get("reason_code"):
            reason_counts[str(row["reason_code"])] += 1
        domain = _failure_domain(row, status)
        if domain:
            failure_domains[domain] += 1
        if row.get("provider_error_code"):
            provider_codes[str(row["provider_error_code"])] += 1
        if row.get("timeout_censored"):
            timeout_censored += 1

        if success:
            partition["strict_success"] += 1
        elif status == 200:
            partition["http_200_quality_failure"] += 1
            quality_failed_200 += 1
            degraded_200 += bool(row.get("degraded"))
        elif status is None and (
            row.get("transport_error_type") or row.get("error") or row.get("timeout_censored")
        ):
            partition["transport_error"] += 1
            kind = str(row.get("transport_error_type") or row.get("error") or
                       "unknown_transport_error")
            transport_types[kind] += 1
        elif status is None:
            partition["unclassified"] += 1
        else:
            partition["http_non_200"] += 1

        if status is not None and status != 200:
            if 400 <= status < 500:
                http_4xx += 1
            elif 500 <= status < 600:
                http_5xx += 1
            else:
                http_other += 1
            auth += status in {401, 403}
            rate_limited += status == 429

    transport_errors = partition["transport_error"]
    http_non_200 = partition["http_non_200"]
    partition_total = sum(partition.values())
    return {
        "denominator_sent": len(sent),
        "outcome_partition": dict(partition),
        "partition_total": partition_total,
        "partition_complete": partition_total == len(sent),
        "http_status_counts": dict(status_counts),
        "http_non_200": http_non_200,
        "http_4xx": http_4xx,
        "http_5xx": http_5xx,
        "http_other_non_200": http_other,
        "authentication_or_permission_http": auth,
        "rate_limited_http_429": rate_limited,
        "transport_errors": transport_errors,
        "transport_error_types": dict(transport_types),
        "unknown_transport_errors": transport_types.get("unknown_transport_error", 0),
        "timeout_censored": timeout_censored,
        "http_200_quality_failures": quality_failed_200,
        "http_200_degraded": degraded_200,
        "http_200_non_degraded_quality_failures": quality_failed_200 - degraded_200,
        "reason_code_counts": dict(reason_counts),
        "failure_domain_counts": dict(failure_domains),
        "failure_domain_total": sum(failure_domains.values()),
        "failure_domain_matches_failed_requests": (
            sum(failure_domains.values()) == len(sent) - sum(bool(row.get("success")) for row in sent)
        ),
        "root_cause_attribution_complete": not (
            failure_domains.get("unknown") or failure_domains.get("echomem_http_unattributed")
        ),
        "provider_evidence_available": any(
            "provider_error_code" in row or "provider_failure_kind" in row for row in sent
        ),
        "provider_failure_count": failure_domains.get("model_provider", 0),
        "provider_error_code_counts": dict(provider_codes),
        "unclassified_failures": partition["unclassified"],
    }


def qps_baseline(levels: list[dict]) -> dict:
    """Derive observed QPS breakpoints without inventing missing root-cause evidence."""
    groups: dict[float, list[dict]] = {}
    peak_strict_rps = None
    for level in levels:
        search = level.get("search") or {}
        identities = level.get("identity_count", level.get("hot_users"))
        per_user = level.get("per_user_search_rps")
        if isinstance(identities, (int, float)) and isinstance(per_user, (int, float)):
            nominal = float(identities * per_user)
        elif isinstance(level.get("nominal_qps"), (int, float)):
            nominal = float(level["nominal_qps"])
        elif isinstance(level.get("hot_users"), (int, float)):
            nominal = float(level["hot_users"])
        else:
            continue
        groups.setdefault(nominal, []).append(level)
        value = level.get("effective_search_rps")
        if isinstance(value, (int, float)):
            peak_strict_rps = value if peak_strict_rps is None else max(peak_strict_rps, value)

    rows = []
    for nominal, group in sorted(groups.items()):
        searches = [level.get("search") or {} for level in group]
        request_errors = sum(int(search.get("transport_or_http_errors") or 0) for search in searches)
        degraded = sum(int(search.get("degraded") or 0) for search in searches)
        strict_failures = sum(int(search.get("errors") or 0) for search in searches)
        provider_failures = sum(int((search.get("error_breakdown") or {}).get(
            "provider_failure_count") or 0) for search in searches)
        provider_evidence_runs = sum(bool((search.get("error_breakdown") or {}).get(
            "provider_evidence_available")) for search in searches)
        rows.append({
            "nominal_qps": nominal, "runs": len(group), "request_errors": request_errors,
            "degraded": degraded, "strict_failures": strict_failures,
            "all_runs_strict": strict_failures == 0,
            "provider_failures": provider_failures,
            "provider_evidence_runs": provider_evidence_runs,
        })

    def first(field):
        return next((row["nominal_qps"] for row in rows if row[field] > 0), None)

    stable = [row["nominal_qps"] for row in rows if row["all_runs_strict"]]
    provider_failures = sum(row["provider_failures"] for row in rows)
    provider_evidence_runs = sum(row["provider_evidence_runs"] for row in rows)
    return {
        "highest_all_runs_strict_qps": max(stable, default=None),
        "first_strict_failure_qps": first("strict_failures"),
        "first_degraded_qps": first("degraded"),
        "first_request_error_qps": first("request_errors"),
        "peak_strict_success_rps": peak_strict_rps,
        "provider_failure_count": provider_failures,
        "provider_evidence_available": provider_evidence_runs > 0,
        "provider_verdict": ("OBSERVED" if provider_failures else
                             "NOT_OBSERVED" if provider_evidence_runs else "NOT_MEASURED"),
        "rows": rows,
    }


def detect_congestion(measurement: dict, *, window_s: float = 10,
                      minimum_requests: int = 20, rejection_ratio: float = .10) -> dict:
    """Stop on sustained service pressure, not recall quality or generator lag."""
    if window_s <= 0 or minimum_requests < 1 or not 0 < rejection_ratio <= 1:
        raise ValueError("Invalid congestion observation parameters")
    buckets = {}
    for row in measurement.get("rows", []):
        if row.get("op") not in {"read", "commit_submit"} or not row.get("sent"):
            continue
        start = row.get("start_s")
        if not _valid_duration(start):
            continue
        bucket = buckets.setdefault(int(start // window_s), {"requests": 0, "pressure_errors": 0})
        bucket["requests"] += 1
        bucket["pressure_errors"] += str(row.get("http_status")) in {"429", "503", "504"} or bool(row.get("timeout_censored"))
    windows = []
    previous = None
    sustained = False
    for index, counts in sorted(buckets.items()):
        ratio = counts["pressure_errors"] / counts["requests"]
        congested = counts["requests"] >= minimum_requests and ratio >= rejection_ratio
        sustained |= congested and previous == index - 1
        previous = index if congested else None
        windows.append({"window_index": index, **counts, "pressure_error_ratio": ratio,
                        "congested": congested})
    return {"observed": sustained, "kind": "sustained-rejection-or-timeout",
            "window_s": window_s, "minimum_requests": minimum_requests,
            "rejection_ratio": rejection_ratio, "required_consecutive_windows": 2,
            "windows": windows}


def search_summary(rows: list[dict]) -> dict:
    sent = [r for r in rows if r.get("sent")]
    successful = [r for r in sent if r.get("success")]
    latency = [r["elapsed_s"] for r in sent if _valid_duration(r.get("elapsed_s"))]
    atomic = [engine.get("duration_seconds") for row in sent for engine in row.get("engine_results", [])
              if engine.get("engine_id") == "atomic_engine" and _valid_duration(engine.get("duration_seconds"))]
    engine_values: dict[str, list[float]] = {}
    for row in sent:
        for engine in row.get("engine_results", []):
            duration = engine.get("duration_seconds")
            if engine.get("engine_id") and _valid_duration(duration):
                engine_values.setdefault(engine["engine_id"], []).append(duration)
    engine_timings = {engine: {"observations": len(values),
                               "mean_s": sum(values) / len(values),
                               "p95_s": percentile(values, 95), "max_s": max(values)}
                      for engine, values in sorted(engine_values.items())}
    failures = error_breakdown(sent)
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
    route_groups: dict[str, list[dict]] = {
        "fast_path": [],
        "intent_llm": [],
        "unobserved": [],
    }
    for row in sent:
        layers = row.get("executed_layers")
        if (not isinstance(layers, list) or not layers
                or any(not isinstance(layer, str) for layer in layers)):
            route = "unobserved"
        elif "llm" in layers:
            route = "intent_llm"
        elif set(layers) <= {"rule", "semantic"}:
            route = "fast_path"
        else:
            route = "unobserved"
        route_groups[route].append(row)
    route_path_timings = {}
    for route, selected in route_groups.items():
        values = [row["elapsed_s"] for row in selected if _valid_duration(row.get("elapsed_s"))]
        route_path_timings[route] = {
            "observations": len(selected),
            "latency_observations": len(values),
            "latency_missing_or_invalid": len(selected) - len(values),
            "fraction_of_sent": len(selected) / len(sent) if sent else None,
            "success": sum(bool(row.get("success")) for row in selected),
            "errors": sum(not row.get("success") for row in selected),
            "degraded": sum(bool(row.get("degraded")) for row in selected),
            "transport_or_http_errors": sum(row.get("http_status") != 200 for row in selected),
            "mean_s": sum(values) / len(values) if values else None,
            "p50_s": percentile(values, 50),
            "p95_s": percentile(values, 95),
            "min_s": min(values) if values else None,
            "max_s": max(values) if values else None,
        }
    return {"planned": len(rows), "sent": len(sent), "success": len(successful),
            "not_sent": len(rows) - len(sent), "delivery_rate": len(sent) / len(rows) if rows else None,
            "quality_rate": len(successful) / len(sent) if sent else None,
            "quality_wilson_95": wilson(len(successful), len(sent)),
            "errors": len(sent) - len(successful), "degraded": sum(bool(r.get("degraded")) for r in sent),
            "http_status_counts": failures["http_status_counts"],
            "degraded_reason_counts": dict(reasons),
            "http_reason_counts": failures["reason_code_counts"],
            "transport_or_http_errors": failures["http_non_200"] + failures["transport_errors"],
            "error_breakdown": failures,
            "fact_hit_observations": len(fact_observed),
            "fact_hits": sum(bool(row["matched_expected_fact"]) for row in fact_observed),
            "result_count_observations": len(hit_observed),
            "empty_results": sum(row["hit_count"] == 0 for row in hit_observed),
            "nonempty_results": sum(row["hit_count"] > 0 for row in hit_observed),
            "atomic_fact_observations": len(atomic_fact_rows),
            "atomic_fact_hits": sum(bool(r["atomic_fact_hit"]) for r in atomic_fact_rows),
            "peak_inflight_requests": peak if concurrency else None,
            "timeout_censored": sum(bool(r.get("timeout_censored")) for r in sent),
            "latency_observations": len(latency),
            "latency_missing_or_invalid": len(sent) - len(latency),
            "mean_s": sum(latency) / len(latency) if latency else None,
            "p50_s": percentile(latency, 50), "p95_s": percentile(latency, 95), "p99_s": percentile(latency, 99),
            "success_p95_s": percentile([r["elapsed_s"] for r in successful
                                         if _valid_duration(r.get("elapsed_s"))], 95),
            "atomic_p95_s": percentile(atomic, 95), "atomic_observations": len(atomic),
            "engine_timings": engine_timings,
            "route_path_timings": route_path_timings,
            # Kept null for old readers; parallel stage times cannot be subtracted.
            "unattributed_residual_p95_s": None,
            "p95_block_bootstrap_95": block_p95_interval(sent),
            "generator_lag_p95_s": percentile([r.get("generator_lag_s", 0) for r in sent], 95)}


def evaluate_level(measurement: dict, *, confirmation: bool = False,
                   assessment_mode: str = "observe") -> dict:
    if assessment_mode not in {"observe", "completion", "slo"}:
        raise ValueError("assessment_mode must be observe, completion or slo")
    reads = [r for r in measurement["rows"] if r["op"] == "read"]
    load_mode = measurement.get("load_mode") or (
        "mixed" if measurement["mixed"] else "search"
    )
    classes = (() if load_mode == "commit" else
               ("recall", "no_recall") if measurement["mixed"] else ("recall",))
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
    concurrency_by_repeat = []
    repeat_indexes = sorted({row.get("repeat_index", 0) for row in submissions + done}) or [0]
    for repeat_index in repeat_indexes:
        repeat_accepted = [row for row in accepted if row.get("repeat_index", 0) == repeat_index]
        repeat_done = [row for row in done if row.get("repeat_index", 0) == repeat_index]
        repeat_submissions = [row for row in submissions if row.get("repeat_index", 0) == repeat_index]
        in_flight_events = [
            (row["accepted_at_s"], 1) for row in repeat_accepted if row.get("accepted_at_s") is not None]
        in_flight_events += [
            (row["end_s"], -1) for row in repeat_done if row.get("end_s") is not None]
        current_in_flight = peak_in_flight = 0
        for _, delta in sorted(in_flight_events, key=lambda item: (item[0], item[1])):
            current_in_flight = max(0, current_in_flight + delta)
            peak_in_flight = max(peak_in_flight, current_in_flight)
        submission_times = [row.get("start_s") for row in repeat_submissions
                            if row.get("start_s") is not None]
        concurrency_by_repeat.append({
            "repeat": repeat_index or 1,
            "peak_in_flight": peak_in_flight,
            "submission_window_s": (max(submission_times) - min(submission_times)
                                    if len(submission_times) > 1 else 0 if submission_times else None),
        })
    commit = {"submitted": len(submissions), "accepted_202": len(accepted), "completed": len(successful),
              "completion_rate": len(successful) / len(accepted) if accepted else None,
              "p95_s": percentile([r["elapsed_s"] for r in done], 95),
              "unfinished_or_failed": len(accepted) - len(successful),
              "peak_in_flight": max((row["peak_in_flight"] for row in concurrency_by_repeat), default=0),
              "submission_window_s": max((row["submission_window_s"] for row in concurrency_by_repeat
                                           if row["submission_window_s"] is not None), default=None),
              "concurrency_by_repeat": concurrency_by_repeat}
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
              "load_mode": load_mode,
              "per_user_search_rps": measurement.get("per_user_search_rps"),
              "per_user_commit_interval_s": measurement.get("per_user_commit_interval_s"),
              "elapsed_with_drain_s": measurement.get("elapsed_with_drain_s")}
    if assessment_mode == "observe":
        operation_rows = [
            row for row in measurement["rows"]
            if row.get("op") in {"read", "add", "commit_submit"}
        ]
        sent_operations = sum(bool(row.get("sent")) for row in operation_rows)
        common.update(status="MEASURED" if sent_operations else "PARTIAL",
                      all_identities_and_classes_sampled=(
                          all(c["sent"] > 0 for c in cells)
                          if cells else len({r.get("identity_index") for r in submissions})
                          == measurement["identity_count"]
                      ),
                      performance_requirements_applied=False)
        return common
    if assessment_mode == "completion":
        searches_completed = bool(
            total["planned"]
            and total["sent"] == total["planned"]
            and total["transport_or_http_errors"] == 0
        )
        commits_completed = bool(
            not measurement["mixed"]
            or submissions
            and len(accepted) == len(submissions)
            and len(successful) == len(accepted)
            and not growing
        )
        common.update(
            status="PASS" if searches_completed and commits_completed else "FAIL",
            all_identities_and_classes_sampled=all(c["sent"] > 0 for c in cells),
            generator_delivery_valid=total["sent"] == total["planned"],
            completion_contract={
                "search_all_scheduled_sent": total["sent"] == total["planned"],
                "search_http_or_transport_errors": total["transport_or_http_errors"],
                "commit_all_accepted": not measurement["mixed"] or len(accepted) == len(submissions),
                "commit_all_terminal_completed": not measurement["mixed"] or len(successful) == len(accepted),
                "commit_backlog_recovered": not measurement["mixed"] or not growing,
            },
            performance_requirements_applied=False,
            latency_threshold_applied=False,
            quality_threshold_applied=False,
        )
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
