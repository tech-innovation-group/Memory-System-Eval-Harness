"""Formal-scenario matrix (suites.py) unit tests."""

from __future__ import annotations

import pytest

from performance.engine import load_scene
from performance.profile import ArrivalSpec
from performance.targets.echomem.orchestrator.suites import (
    QUICK_SCENARIOS,
    SCENES_DIR,
    QuickSpec,
    apply_quick,
    build_case_profile,
    complete_cases,
    four_u8g_cases,
    select_cases,
)

REQUIRED_FIELDS = (
    "label", "scene", "tenants", "duration_s", "search_rps", "commit_rpm",
    "sessions_per_tenant", "messages_per_session",
)


def _by_label(cases):
    return {case["label"]: case for case in cases}


def _all_cases():
    return complete_cases() + four_u8g_cases()


# -- catalog shape ------------------------------------------------------


def test_case_counts():
    assert len(complete_cases()) == 26
    assert len(four_u8g_cases()) == 22


def test_labels_unique():
    for cases in (complete_cases(), four_u8g_cases()):
        labels = [case["label"] for case in cases]
        assert len(labels) == len(set(labels))


def test_quick_scenarios_in_four_u8g():
    four_u8g = {case["label"] for case in four_u8g_cases()}
    for label in QUICK_SCENARIOS.split(","):
        assert label in four_u8g


def test_required_fields_present():
    for case in _all_cases():
        for field in REQUIRED_FIELDS:
            assert field in case, f"{case['label']} missing {field}"


def test_scenes_loadable():
    for case in _all_cases():
        load_scene(SCENES_DIR / f"{case['scene']}.py")


def test_four_u8g_derives_from_complete_plus_fairness():
    complete = _by_label(complete_cases())
    four_u8g = _by_label(four_u8g_cases())
    assert "fairness-bounded" not in complete
    for label, case in four_u8g.items():
        if label == "fairness-bounded":
            continue
        assert label in complete
        for field in ("scene", "tenants", "duration_s", "search_rps"):
            assert case[field] == complete[label][field]


def test_four_u8g_capacity_quick_commit_rpm_zero():
    cases = _by_label(four_u8g_cases())
    for label in ("capacity-2", "capacity-4", "capacity-8"):
        assert cases[label]["quick_commit_rpm"] == 0.0


# -- select_cases -------------------------------------------------------


def test_select_cases_default():
    assert [c["label"] for c in select_cases("4u8g", None)] == [
        c["label"] for c in four_u8g_cases()
    ]
    assert [c["label"] for c in select_cases("complete", None)] == [
        c["label"] for c in complete_cases()
    ]


def test_select_cases_unknown_profile():
    with pytest.raises(ValueError, match="unknown profile"):
        select_cases("bogus", None)


def test_select_cases_filter_preserves_order():
    selected = select_cases("4u8g", ["saturation", "baseline", "capacity-8"])
    assert [c["label"] for c in selected] == [
        "saturation", "baseline", "capacity-8",
    ]


def test_select_cases_unknown_scenario():
    with pytest.raises(ValueError, match="unknown scenarios"):
        select_cases("4u8g", ["baseline", "nope"])


# -- apply_quick --------------------------------------------------------


def test_apply_quick_returns_copy():
    case = _by_label(complete_cases())["tenant-skew"]
    quick = apply_quick(case, QuickSpec())
    assert case["commit_barrier_count"] == 260
    assert case["commit_tenant_counts"] == [200, 20, 20, 20]
    assert quick["commit_barrier_count"] == 32


def test_apply_quick_duration_and_sessions_cap():
    case = _by_label(complete_cases())["soak"]
    quick = apply_quick(case, QuickSpec(duration_cap_s=15.0))
    assert quick["duration_s"] == 15.0
    assert quick["sessions_per_tenant"] == 1


def test_apply_quick_barrier_count_double_min():
    cases = _by_label(complete_cases())
    # scenario cap 16 beats the global cap 32
    assert apply_quick(cases["commit-barrier"], QuickSpec())["commit_barrier_count"] == 16
    assert apply_quick(cases["saturation"], QuickSpec())["commit_barrier_count"] == 16
    # scenario cap 32 == global cap 32
    assert apply_quick(cases["search-priority-blackbox"], QuickSpec())["commit_barrier_count"] == 32


def test_apply_quick_explicit_tenant_counts_scaled():
    case = _by_label(complete_cases())["tenant-skew"]
    quick = apply_quick(case, QuickSpec())
    assert quick["commit_barrier_count"] == 32
    assert quick["commit_tenant_counts"] == [25, 3, 2, 2]
    assert sum(quick["commit_tenant_counts"]) == 32


def test_apply_quick_commit_rpm_override():
    case = _by_label(complete_cases())["capacity-2"]
    quick = apply_quick(case, QuickSpec())
    assert quick["commit_rpm"] == 0.0


def test_apply_quick_can_bound_commit_observation_without_changing_default():
    case = {**_by_label(complete_cases())["search-priority-blackbox"],
            "commit_poll_timeout_s": 180}
    assert apply_quick(case, QuickSpec())["commit_poll_timeout_s"] == 180
    bounded = apply_quick(case, QuickSpec(commit_poll_timeout_cap_s=45))
    assert bounded["commit_poll_timeout_s"] == 45


def test_apply_quick_uniform_barrier_untouched():
    case = _by_label(complete_cases())["saturation"]
    quick = apply_quick(case, QuickSpec())
    assert quick["commit_tenant_distribution"] == "uniform"
    assert quick["commit_tenant_counts"] is None


# -- build_case_profile -------------------------------------------------


def _profile(case, *, quick=None):
    return build_case_profile(
        case,
        base_url="http://127.0.0.1:8010/",
        tenant_count=1,
        auth_headers={"X-Auth-Key": "k1"},
        quick=quick,
    )


def test_build_baseline():
    case = _by_label(complete_cases())["baseline"]
    profile = _profile(case)
    assert profile.target.base_url == "http://127.0.0.1:8010"
    assert profile.target.headers == {"X-Auth-Key": "k1"}
    assert len(profile.tenants) == 1
    assert profile.load.mix == {"read": 2, "write": 1}
    assert profile.load.arrival == {
        "read": ArrivalSpec(model="fixed_rps", rps=2.0),
        "write": ArrivalSpec(model="fixed_rps", rps=2.0 / 60.0),
    }


def test_build_saturation():
    case = _by_label(complete_cases())["saturation"]
    profile = _profile(case)
    assert profile.load.mix == {"read": 32}
    assert profile.load.arrival["read"] == ArrivalSpec(model="fixed_rps", rps=32.0)
    assert "write" not in profile.load.arrival
    params = profile.params
    assert params["barrier_count"] == 128
    assert params["barrier_distribution"] == "uniform"
    assert params["barrier_max_workers"] == 32


def test_build_d_burst():
    case = _by_label(complete_cases())["D@1"]
    profile = _profile(case)
    assert profile.params["burst_commits"] == 32
    assert profile.params["burst_window_s"] == 10.0
    assert "write" not in profile.load.arrival


def test_build_commit_barrier_quick():
    case = _by_label(complete_cases())["commit-barrier"]
    profile = _profile(case, quick=QuickSpec())
    assert profile.load.duration_s == 15.0
    assert profile.params["barrier_count"] == 16


def test_build_fairness_bounded_floor():
    case = _by_label(four_u8g_cases())["fairness-bounded"]
    profile = _profile(case, quick=QuickSpec())
    assert profile.params["floor_to_tenants"] is True
    assert profile.params["barrier_distribution"] == "uniform"


def test_build_capacity_2_quick_reads_only():
    case = _by_label(four_u8g_cases())["capacity-2"]
    profile = _profile(case, quick=QuickSpec())
    assert "write" not in profile.load.arrival
    assert profile.load.mix == {"read": 2, "write": 0}
