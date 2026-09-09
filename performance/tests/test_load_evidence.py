from copy import deepcopy

import pytest

from performance.targets.echomem.acceptance.load_evidence import (
    fairness_counts, load_counts, load_conclusions, priority_counts,
)
from performance.targets.echomem.acceptance.main_metric_report import contention_matrix_counts


def window():
    return {"sent": 40, "success": 40, "transport_or_http_errors": 0, "p95_s": 2.0}


def joint():
    overlap = window()
    return {"expected_identity_indices": [0, 1, 2, 3], "search_window_s": 60,
            "tenants": [{"identity_index": i, "commit_completed_in_search_window": 4,
                         "commit_rps": 999, "search": window()} for i in range(4)],
            "commit_jain": 0.1, "search_inverse_p95_jain": 0.1,
            "commit_planned": 32, "accepted_202": 32,
            "paired": [{"identity_index": i, "before": window(), "during": window(),
                        "p95_degradation_percent": 999} for i in range(4)],
            "overlap_protocol": "nonterminal-poll-v1",
            "overlap_search": overlap, "confirmed_overlap_search": overlap}


def test_equal_completions_recomputed_not_labelled_unfair():
    data = joint()
    counts = fairness_counts(data)
    assert counts["commit_jain"] == counts["search_inverse_p95_jain"] == 1
    assert counts["rows"][0]["commit_rps"] == 4 / 60
    conclusion = load_conclusions(load_counts(data))
    assert conclusion["M3"]["level"] == "各轮Commit完成数均匀"
    assert priority_counts(data)["pairs"][0]["p95_degradation_percent"] == 0


def test_zero_completion_retains_denominator_and_all_zero_is_undefined():
    data = joint()
    data["tenants"][-1]["commit_completed_in_search_window"] = 0
    assert fairness_counts(data)["commit_jain"] == pytest.approx(.75)
    for row in data["tenants"]:
        row["commit_completed_in_search_window"] = 0
    result = fairness_counts(data)
    assert result["all_commit_zero"] and result["commit_tenants"] == 4
    assert result["commit_jain"] is None
    assert result["search_inverse_p95_jain"] == 1
    assert "未定义" in load_conclusions(load_counts(data))["M3"]["level"]


@pytest.mark.parametrize("value", [None, -1, True, "4", float("nan"), float("inf"), .5])
def test_invalid_commit_count_is_not_zero_or_dropped(value):
    data = joint()
    data["tenants"][2]["commit_completed_in_search_window"] = value
    result = fairness_counts(data)
    assert result["expected_tenants"] == 4 and result["commit_tenants"] == 3
    assert result["commit_jain"] is None


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unexpected", "missing-window", "empty-search"])
def test_incomplete_tenant_contract_cannot_inherit_saved_jain(mutation):
    data = joint()
    if mutation == "missing":
        data["tenants"].pop()
    elif mutation == "duplicate":
        data["tenants"][3]["identity_index"] = 2
    elif mutation == "unexpected":
        data["tenants"].append({"identity_index": 9})
    elif mutation == "missing-window":
        data.pop("search_window_s")
    else:
        data["tenants"][3]["search"] = {}
    assert fairness_counts(data)["status"] == "INCONCLUSIVE"


@pytest.mark.parametrize("value", [None, 0, -1, True, "2", float("nan"), float("inf")])
def test_invalid_p95_cannot_yield_search_jain_or_pair(value):
    data = joint()
    data["tenants"][3]["search"]["p95_s"] = value
    data["paired"][3]["before"]["p95_s"] = value
    assert fairness_counts(data)["search_inverse_p95_jain"] is None
    assert priority_counts(data)["valid_pairs"] == 3
    assert priority_counts(data)["status"] == "INCONCLUSIVE"


def test_empty_or_failed_search_is_not_completion_normal():
    empty = load_conclusions(load_counts({}))
    assert empty["M3"]["status"] == empty["M4"]["status"] == "INCONCLUSIVE"
    data = joint()
    data["overlap_search"].update(success=0, transport_or_http_errors=40)
    result = load_conclusions(load_counts(data))["M4"]
    assert result["status"] == "MEASURED"
    assert "出现错误" in result["level"]
    assert "0/40" in result["evidence"]


@pytest.mark.parametrize("mutation", ["no-overlap", "31-accepted", "bad-baseline", "duplicate-pair"])
def test_inadequate_flood_or_baseline_is_inconclusive(mutation):
    data = joint()
    if mutation == "no-overlap":
        data["overlap_search"] = {}
    elif mutation == "31-accepted":
        data["accepted_202"] = 31
    elif mutation == "bad-baseline":
        data["paired"][0]["before"]["success"] = 0
    else:
        data["paired"][3]["identity_index"] = 2
    assert priority_counts(data)["status"] == "INCONCLUSIVE"


@pytest.mark.parametrize("mutation", ["legacy", "empty-confirmed", "exceeds-observed", "unknown-protocol"])
def test_observed_terminal_gap_cannot_prove_backlog(mutation):
    data = joint()
    if mutation == "legacy":
        data.pop("confirmed_overlap_search")
        data.pop("overlap_protocol")
    elif mutation == "empty-confirmed":
        data["confirmed_overlap_search"] = {"sent": 0, "success": 0}
    elif mutation == "exceeds-observed":
        data["confirmed_overlap_search"] = dict(window(), sent=41)
    else:
        data["overlap_protocol"] = "future-unverified"
    counts = priority_counts(data)
    assert counts["status"] == "INCONCLUSIVE"
    assert counts["observed_sent"] == 40


def test_reduction_keeps_confirmed_overlap_for_every_repeat():
    data = joint()
    reduced, _ = contention_matrix_counts({"samples": [{"repeat": 1, "M3_M4": data}]})
    assert load_counts(reduced)["priority_status"] == "MEASURED"
    assert priority_counts(reduced)["overlap_basis"] == "confirmed_nonterminal"


def test_rounds_are_not_pooled_to_hide_missing_evidence():
    first, last = joint(), joint()
    first["tenants"].pop()
    first["accepted_202"] = 31
    reduced, _ = contention_matrix_counts({"expected_samples": 2, "samples": [
        {"repeat": 1, "M3_M4": first}, {"repeat": 2, "M3_M4": last}]})
    result = load_counts(reduced)
    assert result["expected_repeats"] == result["observed_repeats"] == 2
    assert result["fairness_status"] == result["priority_status"] == "INCONCLUSIVE"
    assert result["rows"][1]["fairness"]["status"] == "MEASURED"
    assert result["rows"][0]["priority"]["flood_observed"] is False
    assert result["rows"][1]["priority"]["flood_observed"] is True


def test_unexecuted_or_duplicate_repeats_stay_in_denominator():
    data = joint()
    data["repeat_summaries"] = [dict(joint(), repeat=1)]
    data["expected_repeats"] = 2
    assert load_counts(data)["fairness_status"] == "INCONCLUSIVE"
    data["repeat_summaries"].append(deepcopy(data["repeat_summaries"][0]))
    assert load_counts(data)["priority_status"] == "INCONCLUSIVE"


def test_no_latency_threshold_is_an_acceptance_requirement():
    data = joint()
    for row in data["tenants"]:
        row["search"]["p95_s"] = 300
    data["overlap_search"]["p95_s"] = 300
    for row in data["paired"]:
        row["during"]["p95_s"] = 300
    result = load_counts(data)
    assert result["fairness_status"] == result["priority_status"] == "MEASURED"
    assert result["rows"][0]["priority"]["strict_server_scheduling_proven"] is False


def test_missing_round_totals_are_unknown_not_zero():
    first, last = joint(), joint()
    first.pop("accepted_202")
    reduced, _ = contention_matrix_counts({"expected_samples": 2, "samples": [
        {"repeat": 1, "M3_M4": first}, {"repeat": 2, "M3_M4": last}]})
    assert reduced["accepted_202"] is None
    assert reduced["completed_including_drain"] is None
    assert reduced["commit_planned"] == 64


def test_representative_round_uses_recomputed_not_cached_jain():
    first, last = joint(), joint()
    first["commit_jain"] = .1
    last["commit_jain"] = 1
    last["tenants"][3]["commit_completed_in_search_window"] = 0
    reduced, _ = contention_matrix_counts({"expected_samples": 2, "samples": [
        {"repeat": 1, "M3_M4": first}, {"repeat": 2, "M3_M4": last}]})
    assert fairness_counts(reduced)["commit_jain"] == pytest.approx(.75)


@pytest.mark.parametrize("contract", [[], [0], [0, 0], [True, 1], ["0", "1"]])
def test_invalid_tenant_contract_is_inconclusive(contract):
    data = joint()
    data["expected_identity_indices"] = contract
    assert fairness_counts(data)["status"] == priority_counts(data)["status"] == "INCONCLUSIVE"
