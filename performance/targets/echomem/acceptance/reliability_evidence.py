"""Evidence-preserving public counts for recovery and tenant observability."""

from collections import Counter
import json
import math


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _complete_sum(values):
    return sum(values) if values and all(_number(v) for v in values) else None


def _all_observed(values):
    if any(v is False for v in values):
        return False
    return True if values and all(v is True for v in values) else None


def recovery_counts(value: dict) -> dict:
    if "samples" in value:
        samples = [recovery_counts(sample) for sample in value.get("samples", [])]
        expected = value.get("expected_samples", len(samples))
        expected = expected if isinstance(expected, int) and not isinstance(expected, bool) and expected >= 0 else None
        counts = Counter(sample["status"] for sample in samples)
        passed = counts["PASS"]
        status = ("FAIL" if counts["FAIL"] or value.get("status") == "FAIL" else
                  "PASS" if expected and expected == len(samples) == passed else "INCONCLUSIVE")
        result = {"status": status, "sample_count": len(samples), "expected_samples": expected,
                  "passed_samples": passed, "failed_samples": counts["FAIL"],
                  "inconclusive_samples": counts["INCONCLUSIVE"],
                  "unexecuted_samples": max(0, expected - len(samples)) if expected is not None else None,
                  "samples": samples,
                  "checks": [{"name": f"sample-{i+1}/{c['name']}", "status": c["status"]}
                             for i, sample in enumerate(samples) for c in sample["checks"]]}
        for field in ("accepted_202", "autonomous_completed", "same_archive"):
            result[field] = _all_observed([sample.get(field) for sample in samples])
        for field in ("expected_messages", "missing_messages", "elapsed_s"):
            result[field] = _complete_sum([sample.get(field) for sample in samples])
        result["reconciled_samples"] = sum(sample.get("missing_messages") is not None for sample in samples)
        result["known_missing_messages"] = sum(sample.get("missing_messages") or 0 for sample in samples)
        return result
    checks = value.get("checks", [])
    details = {}
    for check in checks:
        try:
            detail = json.loads(check.get("detail") or "{}")
            details[check["name"]] = detail if isinstance(detail, dict) else {}
        except (TypeError, ValueError):
            details[check["name"]] = {}
    operation = details.get("commit-recovery", {})
    messages = details.get("message-reconciliation", {})
    replay = details.get("idempotency-replay", {})
    expected = messages.get("expected_server_message_ids")
    missing = messages.get("missing_server_message_ids")
    expected_count = len(expected) if isinstance(expected, list) and expected else None
    missing_count = len(missing) if expected_count is not None and isinstance(missing, list) else None
    required = {"pending-before-kill", "commit-recovery", "message-reconciliation",
                "idempotency-replay", "cursor-reconciliation", "order-reconciliation"}
    names = [check.get("name") for check in checks]
    complete = (required <= set(names) and len(names) == len(set(names))
                and all(check.get("status") == "PASS" for check in checks)
                and operation.get("accepted_202") is True
                and operation.get("autonomous_recovery_observed") is True
                and expected_count is not None and missing_count == 0
                and replay.get("same_archive") is True)
    status = ("FAIL" if value.get("status") == "FAIL" or any(c.get("status") == "FAIL" for c in checks)
              else "PASS" if complete else "INCONCLUSIVE")
    return {"status": status, "checks": [{"name": c["name"], "status": c["status"]} for c in checks],
            "sample_count": int(bool(checks)), "expected_samples": int(bool(checks)),
            "passed_samples": int(status == "PASS"), "failed_samples": int(status == "FAIL"),
            "inconclusive_samples": int(bool(checks) and status == "INCONCLUSIVE"),
            "unexecuted_samples": 0, "reconciled_samples": int(missing_count is not None),
            "known_missing_messages": missing_count,
            "sample": value.get("sample"), "kill_delay_s": value.get("kill_delay_s"),
            "elapsed_s": value.get("elapsed_s") if _number(value.get("elapsed_s")) else None,
            "accepted_202": operation.get("accepted_202"),
            "autonomous_completed": operation.get("autonomous_recovery_observed"),
            "expected_messages": expected_count, "missing_messages": missing_count,
            "complete_sources": [s for s in messages.get("complete_sources", [])
                                 if s in {"history", "archive", "commit_cursor"}],
            "missing_required_checks": sorted(required - set(names)),
            "same_archive": replay.get("same_archive"), "replayed": replay.get("replayed")}


def observability_counts(value: dict) -> dict:
    tenants = list(dict.fromkeys(value.get("expected_tenants", [])))
    lanes = list(dict.fromkeys(value.get("expected_lanes", [])))
    labels = {tenant: f"T{i+1}" for i, tenant in enumerate(tenants)}
    fields = ("queued", "wait_seconds_total", "exec_seconds_total", "rejected_total",
              "accepted_total", "completed_total", "failed_total", "accepted_delta", "queued_peak_during_load")
    required = fields[:7]
    rows = value.get("rows", [])
    grouped = {}
    for row in rows:
        grouped.setdefault((row.get("tenant_id"), row.get("lane")), []).append(row)
    missing, invalid, valid, duplicates = [], [], [], []
    reported_bad = {(item.get("tenant_id"), item.get("lane"))
                    for field in ("missing", "invalid", "duplicate_rows")
                    for item in value.get(field, [])}
    for tenant in tenants:
        for lane in lanes:
            cell = {"tenant": labels[tenant], "lane": lane}
            entries = grouped.get((tenant, lane), [])
            if not entries or any(field not in entries[0] for field in required):
                missing.append(cell)
                continue
            if len(entries) != 1:
                duplicates.append(cell)
                continue
            bad = [field for field in required if not _number(entries[0][field])
                   or field in {"queued", "rejected_total", "accepted_total", "completed_total", "failed_total"}
                   and entries[0][field] % 1 != 0]
            if bad:
                invalid.append({**cell, "fields": bad})
            elif (tenant, lane) not in reported_bad:
                valid.append(cell)
    # Retain validation failures detected before normalized rows were produced.
    missing_count = max(len(missing), len(value.get("missing", [])))
    invalid_count = max(len(invalid), len(value.get("invalid", [])))
    duplicate_count = max(len(duplicates), len(value.get("duplicate_rows", [])))
    complete = bool(tenants and lanes and not missing_count and not invalid_count and not duplicate_count
                    and value.get("status") == "PASS"
                    and value.get("lane_count_matches_observed") is True
                    and value.get("expected_lanes_match_observed") is True)
    public = {"status": "PASS" if complete else "FAIL" if value.get("status") == "FAIL" else "INCONCLUSIVE",
              "tenant_count": len(tenants), "expected_cells": len(tenants)*len(lanes), "expected_lanes": lanes,
              "valid_cells": len(valid), "missing_cells": missing_count,
              "invalid_cells": invalid_count, "duplicate_cells": duplicate_count,
              "missing_details": missing, "invalid_details": invalid, "duplicate_details": duplicates,
              "sample_count": value.get("sample_count"), "declared_lane_count": value.get("lane_count"),
              "lane_count_matches_observed": value.get("lane_count_matches_observed"),
              "expected_lanes_match_observed": value.get("expected_lanes_match_observed"),
              "coverage_scope": value.get("coverage_scope"), "observed_lanes": value.get("observed_lanes", []),
              "rows": [{"tenant": labels.get(row.get("tenant_id"), "unknown"), "lane": row.get("lane"),
                        **{field: row.get(field) if _number(row.get(field)) else None for field in fields}}
                       for row in rows]}
    public["snapshot_status"] = public["status"]
    if "process_observations" in value:
        from performance.targets.echomem.acceptance.observability_timeline import timeline_counts
        public["timeline"] = timeline_counts(value["process_observations"])
        if public["status"] == "PASS" and public["timeline"]["status"] != "PASS":
            public["status"] = public["timeline"]["status"]
    if "repeat_observations" in value:
        repeats = [observability_counts(item) for item in value["repeat_observations"]]
        expected = value.get("expected_repeats", len(repeats))
        public.update(repeat_observations=repeats, expected_repeats=expected,
                      passed_repeats=sum(r["status"] == "PASS" for r in repeats))
        public["status"] = ("FAIL" if any(r["status"] == "FAIL" for r in repeats) else
                            "PASS" if public["status"] == "PASS" and complete and repeats and len(repeats) == expected
                            and all(r["status"] == "PASS" for r in repeats) else "INCONCLUSIVE")
    return public
