"""正式场景矩阵（case 定义）与 echomem 侧 case → Profile 包装。

case 是正式验收矩阵的最小单元：label 唯一、携带场景文件名（scenes/ 下，
不含 .py）、负载参数（tenants/duration_s/search_rps/commit_rpm/sessions/
messages）与 barrier/burst 字段。``complete_cases`` 是报告(6) 12 例 +
场景集 14 例的全集（26 例），``four_u8g_cases`` 是 4U8G bounded 目录
（22 例，capacity-2/4/8 强置 quick_commit_rpm=0，另含 fairness-bounded）；
两者都是显式常量列表，不做动态叉乘生成。

quick 收敛（``QuickSpec``/``apply_quick``）与 case → Profile 的通用骨架
（``build_case_profile``）在通用套件层 ``performance.suite``；本模块按
echomem 约定填充场景目录、默认 query 列表与 barrier/burst 参数回调，
并保留场景矩阵常量与 case 选择。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from performance.profile import Profile
from performance.suite import (
    QuickSpec,
    apply_quick as apply_quick,
    build_case_profile as _build_case_profile_general,
)
from performance.targets.echomem.protocol import DEFAULT_QUERIES

SCENES_DIR = Path(__file__).resolve().parent.parent / "scenes"

# quick 模式的推荐场景子集（供 CLI 使用；suite 本身跑全量 case + 收敛）。
QUICK_SCENARIOS = (
    "baseline,fairness-bounded,search-priority-blackbox,"
    "saturation,capacity-2,capacity-4,capacity-8"
)


def _case(**fields: Any) -> dict[str, Any]:
    case = {
        "commit_rpm": None,
        "sessions_per_tenant": 1,
        "messages_per_session": 3,
        "commit_barrier": False,
        "commit_barrier_count": 32,
        "commit_tenant_distribution": "uniform",
        "commit_zipf_exponent": 2.0,
        "commit_tenant_counts": None,
        "commit_barrier_waves": 1,
        "commit_barrier_cooldown_s": 0.0,
        "commit_burst_window_s": None,
        "quick_barrier_count_cap": 0,
        "quick_commit_rpm": None,
        "search_workers": None,
        "commit_workers": None,
        "per_tenant_concurrency": None,
        "read_only": False,
        "blackbox_search_priority": False,
        "fairness_bounded": False,
    }
    case.update(fields)
    return case


# -- 报告(6) 12 例 + PR421 SCENARIOS 14 例 = 26 例 ----------------------- #

_COMPLETE_CASES: list[dict[str, Any]] = [
    # report(6) @1（concurrency=1，workers=8）
    _case(
        label="A@1",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=8.0,
        commit_rpm=0.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=8,
        commit_workers=8,
        per_tenant_concurrency=1,
        read_only=True,
    ),
    _case(
        label="B@1",
        scene="scene_barrier",
        tenants=8,
        duration_s=60,
        search_rps=0.0,
        commit_rpm=0.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=8,
        commit_workers=8,
        per_tenant_concurrency=1,
        commit_barrier=True,
        commit_barrier_count=8,
    ),
    _case(
        label="C8:1@1",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=64.0,
        commit_rpm=60.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=8,
        commit_workers=8,
        per_tenant_concurrency=1,
    ),
    _case(
        label="C4:1@1",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=32.0,
        commit_rpm=60.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=8,
        commit_workers=8,
        per_tenant_concurrency=1,
    ),
    _case(
        label="C1:1@1",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=8.0,
        commit_rpm=60.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=8,
        commit_workers=8,
        per_tenant_concurrency=1,
    ),
    _case(
        label="D@1",
        scene="scene_d_burst",
        tenants=8,
        duration_s=60,
        search_rps=8.0,
        commit_rpm=0.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=8,
        commit_workers=8,
        per_tenant_concurrency=1,
        commit_barrier=True,
        commit_barrier_count=32,
        commit_barrier_waves=1,
        commit_barrier_cooldown_s=0.0,
        commit_burst_window_s=10.0,
    ),
    # report(6) @2（concurrency=2，workers=16）
    _case(
        label="A@2",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=16.0,
        commit_rpm=0.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=16,
        commit_workers=16,
        per_tenant_concurrency=2,
        read_only=True,
    ),
    _case(
        label="B@2",
        scene="scene_barrier",
        tenants=8,
        duration_s=60,
        search_rps=0.0,
        commit_rpm=0.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=16,
        commit_workers=16,
        per_tenant_concurrency=2,
        commit_barrier=True,
        commit_barrier_count=16,
    ),
    _case(
        label="C8:1@2",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=128.0,
        commit_rpm=120.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=16,
        commit_workers=16,
        per_tenant_concurrency=2,
    ),
    _case(
        label="C4:1@2",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=64.0,
        commit_rpm=120.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=16,
        commit_workers=16,
        per_tenant_concurrency=2,
    ),
    _case(
        label="C1:1@2",
        scene="scene_capacity",
        tenants=8,
        duration_s=60,
        search_rps=16.0,
        commit_rpm=120.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=16,
        commit_workers=16,
        per_tenant_concurrency=2,
    ),
    _case(
        label="D@2",
        scene="scene_d_burst",
        tenants=8,
        duration_s=60,
        search_rps=16.0,
        commit_rpm=0.0,
        sessions_per_tenant=2,
        messages_per_session=10,
        search_workers=16,
        commit_workers=16,
        per_tenant_concurrency=2,
        commit_barrier=True,
        commit_barrier_count=32,
        commit_barrier_waves=1,
        commit_barrier_cooldown_s=0.0,
        commit_burst_window_s=10.0,
    ),
    # PR421 SCENARIOS
    _case(
        label="baseline",
        scene="scene_capacity",
        tenants=1,
        duration_s=600,
        search_rps=2.0,
        commit_rpm=2.0,
        sessions_per_tenant=4,
        messages_per_session=3,
    ),
    _case(
        label="mixed",
        scene="scene_capacity",
        tenants=4,
        duration_s=600,
        search_rps=8.0,
        commit_rpm=2.0,
        sessions_per_tenant=4,
        messages_per_session=3,
    ),
    _case(
        label="commit-storm",
        scene="scene_capacity",
        tenants=4,
        duration_s=600,
        search_rps=4.0,
        commit_rpm=10.0,
        sessions_per_tenant=4,
        messages_per_session=3,
    ),
    _case(
        label="commit-barrier",
        scene="scene_barrier",
        tenants=4,
        duration_s=60,
        search_rps=4.0,
        commit_rpm=0.0,
        sessions_per_tenant=40,
        messages_per_session=3,
        commit_barrier=True,
        commit_barrier_count=160,
        commit_tenant_distribution="zipf",
        commit_zipf_exponent=2.0,
        quick_barrier_count_cap=16,
    ),
    _case(
        label="saturation",
        scene="scene_barrier",
        tenants=4,
        duration_s=60,
        search_rps=32.0,
        commit_rpm=0.0,
        sessions_per_tenant=32,
        messages_per_session=3,
        commit_barrier=True,
        commit_barrier_count=128,
        commit_tenant_distribution="uniform",
        quick_barrier_count_cap=16,
    ),
    _case(
        label="tenant-skew",
        scene="scene_barrier",
        tenants=4,
        duration_s=120,
        search_rps=8.0,
        commit_rpm=0.0,
        sessions_per_tenant=200,
        messages_per_session=3,
        commit_barrier=True,
        commit_barrier_count=260,
        commit_tenant_distribution="explicit",
        commit_tenant_counts=[200, 20, 20, 20],
    ),
    _case(
        label="capacity-16",
        scene="scene_capacity",
        tenants=16,
        duration_s=300,
        search_rps=16.0,
        commit_rpm=2.0,
        sessions_per_tenant=2,
        messages_per_session=3,
        quick_commit_rpm=0.0,
    ),
    _case(
        label="capacity-2",
        scene="scene_capacity",
        tenants=2,
        duration_s=180,
        search_rps=2.0,
        commit_rpm=2.0,
        sessions_per_tenant=2,
        messages_per_session=3,
        quick_commit_rpm=0.0,
    ),
    _case(
        label="capacity-4",
        scene="scene_capacity",
        tenants=4,
        duration_s=180,
        search_rps=4.0,
        commit_rpm=2.0,
        sessions_per_tenant=2,
        messages_per_session=3,
        quick_commit_rpm=0.0,
    ),
    _case(
        label="capacity-8",
        scene="scene_capacity",
        tenants=8,
        duration_s=180,
        search_rps=8.0,
        commit_rpm=2.0,
        sessions_per_tenant=2,
        messages_per_session=3,
    ),
    _case(
        label="capacity-32",
        scene="scene_capacity",
        tenants=32,
        duration_s=300,
        search_rps=32.0,
        commit_rpm=2.0,
        sessions_per_tenant=2,
        messages_per_session=3,
    ),
    _case(
        label="search-priority-blackbox",
        scene="scene_barrier",
        tenants=4,
        duration_s=90,
        search_rps=16.0,
        commit_rpm=0.0,
        sessions_per_tenant=32,
        messages_per_session=3,
        search_workers=32,
        commit_workers=32,
        commit_barrier=True,
        commit_barrier_count=128,
        commit_tenant_distribution="uniform",
        quick_barrier_count_cap=32,
        blackbox_search_priority=True,
    ),
    _case(
        label="search-storm",
        scene="scene_capacity",
        tenants=4,
        duration_s=600,
        search_rps=20.0,
        commit_rpm=1.0,
        sessions_per_tenant=4,
        messages_per_session=3,
    ),
    _case(
        label="soak",
        scene="scene_capacity",
        tenants=4,
        duration_s=1800,
        search_rps=8.0,
        commit_rpm=2.0,
        sessions_per_tenant=4,
        messages_per_session=3,
    ),
]

# bounded 4U8G 目录：report6 12 例 + SCENARIOS 9 例 + fairness-bounded。
_FOUR_U8G_LABELS = (
    "A@1", "B@1", "C8:1@1", "C4:1@1", "C1:1@1", "D@1",
    "A@2", "B@2", "C8:1@2", "C4:1@2", "C1:1@2", "D@2",
    "baseline", "mixed", "commit-barrier", "saturation", "tenant-skew",
    "search-priority-blackbox", "capacity-2", "capacity-4", "capacity-8",
)

_FAIRNESS_BOUNDED_CASE = _case(
    label="fairness-bounded",
    scene="scene_barrier",
    tenants=4,
    duration_s=30,
    search_rps=8.0,
    commit_rpm=0.0,
    sessions_per_tenant=8,
    messages_per_session=1,
    commit_barrier=True,
    commit_barrier_count=32,
    commit_tenant_distribution="uniform",
    quick_barrier_count_cap=32,
    fairness_bounded=True,
)


def complete_cases() -> list[dict]:
    """26 个正式 case（报告(6) 12 例 + 场景集 14 例）。"""
    return list(_COMPLETE_CASES)


def four_u8g_cases() -> list[dict]:
    """22 个 bounded case（capacity-2/4/8 强置 quick_commit_rpm=0）。"""
    by_label = {case["label"]: case for case in _COMPLETE_CASES}
    cases = [dict(by_label[label]) for label in _FOUR_U8G_LABELS]
    for case in cases:
        if case["label"] in ("capacity-2", "capacity-4", "capacity-8"):
            case["quick_commit_rpm"] = 0.0
    cases.append(dict(_FAIRNESS_BOUNDED_CASE))
    return cases


def select_cases(profile_name: str, scenarios: list[str] | None) -> list[dict]:
    """按 profile 选 case；``scenarios`` 非空时按 label 过滤并保留顺序。"""
    if profile_name == "six-metrics":
        catalog = six_metric_cases()
    elif profile_name == "4u8g":
        catalog = four_u8g_cases()
    elif profile_name == "complete":
        catalog = complete_cases()
    else:
        raise ValueError(f"unknown profile: {profile_name}")
    if scenarios is None:
        return catalog
    by_label = {case["label"]: case for case in catalog}
    unknown = [item for item in scenarios if item not in by_label]
    if unknown:
        raise ValueError(f"unknown scenarios: {', '.join(unknown)}")
    return [by_label[item] for item in scenarios]


def six_metric_cases(capacity_levels: list[int] | None = None) -> list[dict]:
    levels = capacity_levels if capacity_levels is not None else [2, 4, 8, 16, 32]
    if (not isinstance(levels, list) or len(levels) < 2
            or any(type(n) is not int or n < 2 for n in levels)
            or levels != sorted(set(levels))):
        raise ValueError("capacity_levels must contain at least two increasing integer levels >= 2")
    cases = [
        _case(label="recall-baseline", scene="scene_capacity", tenants=4,
              duration_s=60, search_rps=16, search_workers=32, commit_rpm=0, read_only=True),
        _case(label="query-mixed", scene="scene_capacity", tenants=4,
              duration_s=60, search_rps=8, commit_rpm=0, read_only=True,
              query_mode="mixed"),
        {**_FAIRNESS_BOUNDED_CASE, "duration_s": 120},
        {**next(c for c in _COMPLETE_CASES if c["label"] == "search-priority-blackbox"),
         "duration_s": 120, "sessions_per_tenant": 1, "commit_barrier_count": 32,
         "barrier_at_s": 15},
    ]
    for level in levels:
        cases.append(_case(label=f"capacity-{level}", scene="scene_capacity",
                           tenants=level, duration_s=60, search_rps=level,
                           search_workers=level * 2, commit_rpm=0, read_only=True))
    return cases


def six_metric_observation_cases(*, quick: bool = False) -> list[dict]:
    """Observation-only M3/M4 matrix.

    M1 is executed by the T x U capacity runner and M2/M5/M6 are probes. The
    cases here therefore contain only the paired Search/Commit windows needed
    for fairness and flood observations. No case encodes a performance gate.
    """
    duration = 15 if quick else 300
    barrier = 8 if quick else 64
    common = {
        "duration_s": duration,
        "search_rps": 8.0,
        "search_workers": 64,
        "commit_workers": 64,
        "sessions_per_tenant": 2,
        "messages_per_session": 4,
    }
    return [
        _case(
            label="m3-fairness-4t", scene="scene_c_mixed", tenants=4,
            commit_rpm=20.0 if quick else 2.0, commit_barrier=False,
            arrival_scope="per_tenant", commit_start_s=3 if quick else 30,
            arrival_end_s=duration, measurement_start_s=3 if quick else 30,
            measurement_end_s=duration, fairness_mode="independent-periodic-v1",
            **{**common, "search_rps": 1.0, "duration_s": duration + (30 if quick else 180)},
        ),
        _case(
            label="m3-fairness-8t", scene="scene_c_mixed", tenants=8,
            commit_rpm=20.0 if quick else 2.0, commit_barrier=False,
            arrival_scope="per_tenant", commit_start_s=3 if quick else 30,
            arrival_end_s=duration, measurement_start_s=3 if quick else 30,
            measurement_end_s=duration, fairness_mode="independent-periodic-v1",
            **{**common, "search_rps": 1.0, "duration_s": duration + (30 if quick else 180)},
        ),
        _case(
            label="m4-baseline", scene="scene_capacity", tenants=4,
            commit_rpm=0.0, read_only=True, **common,
        ),
        _case(
            label="m4-flood-uniform", scene="scene_barrier", tenants=4,
            barrier_prepare_before_commit=True,
            commit_rpm=0.0, commit_barrier=True,
            commit_barrier_count=barrier,
            commit_tenant_distribution="uniform", barrier_at_s=3 if quick else 30,
            blackbox_search_priority=True, **common,
        ),
        _case(
            label="m4-flood-single-tenant", scene="scene_barrier", tenants=4,
            barrier_prepare_before_commit=True,
            commit_rpm=0.0, commit_barrier=True,
            commit_barrier_count=barrier,
            commit_tenant_distribution="explicit",
            commit_tenant_counts=[barrier, 0, 0, 0],
            barrier_at_s=3 if quick else 30,
            blackbox_search_priority=True, **common,
        ),
    ]


def build_case_profile(
    case: dict,
    *,
    base_url: str,
    tenant_count: int,
    auth_headers: dict,
    quick: QuickSpec | None = None,
) -> Profile:
    """case → Profile：场景目录/默认 query 按 echomem 约定填充。

    quick 非 None 时先 ``apply_quick`` 收敛；barrier/burst 参数经
    ``_apply_barrier_params`` 注入 params。通用骨架见
    ``performance.suite.build_case_profile``。
    """
    return _build_case_profile_general(
        case,
        scene_path=SCENES_DIR / f"{case['scene']}.py",
        base_url=base_url,
        tenant_count=tenant_count,
        auth_headers=auth_headers,
        queries=list(DEFAULT_QUERIES),
        quick=quick,
        extra_params=_apply_barrier_params,
    )


def _apply_barrier_params(params: dict[str, Any], case: dict) -> None:
    """按场景把 barrier/burst 字段翻译进 params（与 case['scene'] 一致）。"""
    scene_name = case["scene"]
    params["query_mode"] = case.get("query_mode", "recall")
    params["commit_poll_timeout_s"] = case.get("commit_poll_timeout_s", 180)
    if scene_name == "scene_d_burst":
        params["burst_commits"] = int(case.get("commit_barrier_count", 32))
        params["burst_window_s"] = float(case.get("commit_burst_window_s", 10.0))
        params["burst_max_workers"] = 8
        return
    if scene_name == "scene_burst_waves":
        params["burst_commits"] = int(case.get("commit_barrier_count", 32))
        params["burst_window_s"] = float(case.get("commit_burst_window_s", 10.0))
        params["burst_waves"] = int(case.get("commit_barrier_waves", 1))
        params["burst_cooldown_s"] = float(case.get("commit_barrier_cooldown_s", 0.0))
        params["burst_max_workers"] = 8
        return
    if scene_name == "scene_barrier":
        barrier_count = int(case.get("commit_barrier_count", 32))
        params.update(
            {
                "barrier_count": barrier_count,
                "barrier_prepare_before_commit": bool(case.get("barrier_prepare_before_commit", False)),
                "barrier_at_s": float(case.get("barrier_at_s", 0)),
                "barrier_distribution": str(
                    case.get("commit_tenant_distribution", "uniform")
                ),
                "barrier_zipf_exponent": float(case.get("commit_zipf_exponent", 2.0)),
                "barrier_waves": int(case.get("commit_barrier_waves", 1)),
                "barrier_cooldown_s": float(case.get("commit_barrier_cooldown_s", 0.0)),
                "barrier_max_workers": min(barrier_count, 32),
            }
        )
        if case.get("commit_tenant_counts"):
            params["commit_tenant_counts"] = [
                int(value) for value in case["commit_tenant_counts"]
            ]
        if (
            case.get("fairness_bounded")
            and case.get("commit_tenant_distribution") == "uniform"
        ):
            params["floor_to_tenants"] = True
