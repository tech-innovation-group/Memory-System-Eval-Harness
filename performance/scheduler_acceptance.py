#!/usr/bin/env python3
"""Evaluate the six 4U8G scheduler acceptance targets from real evidence.

This command intentionally separates the test-platform verdict from the
EchoMem capability verdict. Missing runtime controls are INCONCLUSIVE, never
silently treated as a pass.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"

# A small barrier is useful as a smoke test, but it is not a flood.  The
# priority objective must have enough accepted Commit arrivals to create
# meaningful contention before a low Search P95 can be called evidence.
MIN_PRIORITY_COMMIT_FLOOD = 32

PR421_LANES = {
    "recall_engine",
    "recall_intent_llm",
    "recall_query_embedding",
    "recall_rerank",
    "commit",
}

OBJECTIVE_OWNERS = {
    "DAU / 最大热用户容量": "测试平台 + 部署资源",
    "单租户故障隔离": "EchoMem/部署控制面 + 测试平台采集",
    "Commit/Search 公平性 Jain": "EchoMem 调度 + 测试平台负载",
    "Search 优先于 Commit": "EchoMem 调度 + 测试平台负载",
    "Commit kill-9 恢复与重放": "EchoMem 持久化 + 部署重启权限 + 测试平台对账",
    "分层/分租户调度可观测性": "EchoMem /metrics + 测试平台校验",
}
LEGACY_LANES = {
    "http_interactive",
    "http_background",
    "http_global",
    "tenant_rate_limit",
    "commit",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        # Some legacy server jobs wrote a literal "\\n" after the JSON object.
        # Accept that harmless artifact so old evidence remains auditable.
        if text.endswith("\\n"):
            text = text[:-2].rstrip()
        value = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _result(name: str, status: str, target: Any, observed: Any, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "target": target,
        "observed": observed,
        "reason": reason,
        "owner": OBJECTIVE_OWNERS.get(name, "测试平台"),
    }


def _jain(values: list[float]) -> float | None:
    values = [value for value in values if value >= 0]
    if len(values) < 2 or sum(value * value for value in values) <= 0:
        return None
    total = sum(values)
    return (total * total) / (len(values) * sum(value * value for value in values))


def _per_tenant_metrics(
    suite: dict[str, Any],
    selected_runs: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge one run's per-tenant metrics without mixing unlike workloads."""
    # This helper is used by the fairness gate. Search-priority is intentionally
    # excluded: its uneven Commit completion is the expected consequence of
    # protecting Search, not evidence of unfair tenant scheduling.
    candidate_scenarios = (
        "tenant-skew",
        "tenant-skew-bounded",
        "fairness-bounded",
        "mixed",
        "commit-barrier",
    )
    if selected_runs is None:
        runs_by_scenario = {
            scenario: [
                run for run in _suite_runs(suite)
                if str(run.get("scenario") or "") == scenario
                and str(run.get("status") or "") == "completed"
            ]
            for scenario in candidate_scenarios
        }
        selected_scenario = next(
            (
                scenario
                for scenario in candidate_scenarios
                if any(
                    isinstance(
                        (_run_summary(run).get("metrics") or {}).get("per_tenant"),
                        dict,
                    )
                    for run in runs_by_scenario[scenario]
                )
            ),
            "",
        )
        runs = runs_by_scenario[selected_scenario] if selected_scenario else []
    else:
        # The fairness gate has already selected one comparable workload.
        # Re-selecting from the whole suite here can pull in capacity-128
        # tenants and silently change the Jain denominator.
        runs = [
            run for run in selected_runs
            if str(run.get("status") or "") == "completed"
        ]
    merged: dict[str, dict[str, Any]] = {}
    for run in runs:
        metrics = _run_summary(run).get("metrics") or {}
        for tenant, data in (metrics.get("per_tenant") or {}).items():
            if not isinstance(data, dict):
                continue
            entry = merged.setdefault(
                str(tenant),
                {
                    "commit_submitted": 0,
                    "commit_completed": 0,
                    "commit_submitted_fallback": False,
                    "window_s": 0.0,
                    "search_p95_s": [],
                },
            )
            commit = data.get("commit") or {}
            submitted = commit.get("submitted")
            if submitted in (None, ""):
                # Older summaries exposed only completed counts.  Treat that
                # as a lower-bound submission count for compatibility, while
                # retaining the fallback marker so the report can disclose
                # that the raw arrival count was unavailable.
                submitted = commit.get("completed") or 0
                entry["commit_submitted_fallback"] = True
            entry["commit_submitted"] += int(submitted or 0)
            entry["commit_completed"] += int(commit.get("completed") or 0)
            run_window = run.get("duration_s")
            if run_window is None:
                run_window = _metric(_run_summary(run), "config", "duration_s")
            try:
                window = max(0.0, float(run_window))
            except (TypeError, ValueError):
                window = 0.0
            entry["window_s"] += window
            search = data.get("search") or {}
            search_latency = search.get("latency") or {}
            value = search_latency.get("p95_s")
            try:
                if float(value) > 0:
                    entry["search_p95_s"].append(float(value))
            except (TypeError, ValueError):
                pass
    for entry in merged.values():
        values = entry.pop("search_p95_s", [])
        entry["search_p95_s"] = (
            sum(values) / len(values) if values else None
        )
        entry["commit_throughput"] = (
            entry["commit_completed"] / entry["window_s"]
            if entry["window_s"] > 0
            else float(entry["commit_completed"])
        )
        entry["commit_throughput_source"] = (
            "completed_per_window" if entry["window_s"] > 0 else "completed_count_fallback"
        )
    return merged


def _usable_tenant_ids(suite: dict[str, Any]) -> list[str]:
    """Return the ordered identities that passed the current auth preflight."""
    auth = suite.get("auth_preflight")
    if not isinstance(auth, dict):
        return []
    return [
        str(item).strip()
        for item in auth.get("usable_tenant_ids") or []
        if str(item).strip()
    ]


def _canonical_tenant_key(raw: Any, suite: dict[str, Any]) -> str:
    """Map runner tenant indexes back to the current tenant identity.

    ``run_stress`` historically writes ``0``, ``1`` ... into per-tenant
    summaries.  Fairness expectations, fault probes, and observability
    snapshots use real tenant IDs.  Normalizing at the acceptance boundary
    keeps the denominator tied to the current auth-preflight result without
    treating a stale profile name as a missing tenant.
    """
    value = str(raw or "").strip()
    usable = _usable_tenant_ids(suite)
    if value.isdigit():
        index = int(value)
        if 0 <= index < len(usable):
            return usable[index]
    return value


def _canonicalize_tenant_metrics(
    metrics: dict[str, dict[str, Any]],
    suite: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw_tenant, data in metrics.items():
        tenant = _canonical_tenant_key(raw_tenant, suite)
        if not tenant or not isinstance(data, dict):
            continue
        # A malformed/legacy manifest can contain both an index and its
        # already-normalized ID. Merge rather than silently dropping either.
        current = normalized.setdefault(tenant, {})
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key] = {**current[key], **value}
            else:
                current[key] = value
    return normalized


def _fairness_expected_tenants(
    suite: dict[str, Any],
    selected_runs: list[dict[str, Any]],
    observed_tenants: set[str],
) -> set[str]:
    """Build the fairness denominator from the selected workload window.

    Static ``fairness_expectations.tenant_ids`` is only a fallback.  If the
    runner records numeric tenant indexes, use the number of tenants required
    by the selected scenario and map those indexes through auth preflight.
    This preserves tenants that produced zero rows instead of excluding them.
    """
    usable = _usable_tenant_ids(suite)
    expected_count = 0
    for run in selected_runs:
        config = run.get("scenario_config")
        if isinstance(config, dict):
            try:
                expected_count = max(expected_count, int(config.get("tenant_count") or 0))
            except (TypeError, ValueError):
                pass
        summary = _run_summary(run)
        activity = (summary.get("details") or {}).get("user_activity") or {}
        active = activity.get("active_users_by_tenant")
        if isinstance(active, dict):
            numeric = [int(key) for key in active if str(key).isdigit()]
            expected_count = max(expected_count, max(numeric, default=-1) + 1)

    declared = suite.get("fairness_expectations")
    declared_ids = (
        {
            str(item).strip()
            for item in declared.get("tenant_ids") or []
            if str(item).strip()
        }
        if isinstance(declared, dict)
        else set()
    )

    if expected_count and observed_tenants and all(
        tenant.isdigit() for tenant in observed_tenants
    ):
        return {
            _canonical_tenant_key(str(index), suite)
            for index in range(expected_count)
        }
    if expected_count and usable:
        return set(usable[:expected_count])
    if declared_ids:
        return declared_ids
    return set(observed_tenants)


def _suite_runs(suite: dict[str, Any]) -> list[dict[str, Any]]:
    runs = suite.get("runs")
    return [item for item in runs if isinstance(item, dict)] if isinstance(runs, list) else []


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    value = run.get("summary")
    return value if isinstance(value, dict) else {}


def _scenario_runs(suite: dict[str, Any], names: set[str]) -> list[dict[str, Any]]:
    return [run for run in _suite_runs(suite) if str(run.get("scenario") or "") in names]


def _metric(summary: dict[str, Any], *path: str) -> Any:
    current: Any = summary
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return current


def _capacity(suite: dict[str, Any]) -> dict[str, Any]:
    def scenario_name(run: dict[str, Any]) -> str:
        """Use the canonical source name while accepting namespaced manifests."""
        return str(
            run.get("source_scenario")
            or run.get("scenario")
            or ""
        ).strip()

    names = {
        scenario_name(run)
        for run in _suite_runs(suite)
        if scenario_name(run).startswith("capacity-")
    }
    levels = sorted(
        (int(name.split("-", 1)[1]) for name in names if name.split("-", 1)[1].isdigit()),
    )
    target = "4U8G 实际生效配置 + 至少一档 DAU/热租户容量数据"
    if not levels:
        return _result(
            "DAU / 最大热用户容量",
            INCONCLUSIVE,
            target,
            {"capacity_levels": []},
            "未找到 capacity-* 场景，不能估计 DAU 或热租户容量",
        )
    profile = suite.get("instance_profile") or suite.get("config", {}).get("instance_profile")
    completed_levels = sorted(
        int(scenario_name(run).split("-", 1)[1])
        for run in _suite_runs(suite)
        if scenario_name(run).startswith("capacity-")
        and str(run.get("status") or "") == "completed"
        and scenario_name(run).split("-", 1)[1].isdigit()
    )
    valid_levels = []
    invalid_levels = []
    timeout_levels = []
    hot_user_candidates: list[int] = []
    activity_by_level: dict[str, dict[str, Any]] = {}
    capacity_targets: dict[str, int] = {}
    activity_missing_levels: list[int] = []
    for run in _suite_runs(suite):
        scenario = scenario_name(run)
        metrics = (_run_summary(run).get("metrics") or {})
        if scenario.startswith("capacity-"):
            level_text = scenario.split("-", 1)[1]
            if not level_text.isdigit():
                continue
            level = int(level_text)
            scenario_config = run.get("scenario_config")
            if isinstance(scenario_config, dict):
                try:
                    capacity_targets[str(level)] = int(
                        scenario_config.get("capacity_active_users") or level
                    )
                except (TypeError, ValueError):
                    capacity_targets[str(level)] = level
            activity = (_run_summary(run).get("details") or {}).get("user_activity")
            if isinstance(activity, dict):
                activity_by_level[str(level)] = activity
            active_users = (
                int(activity.get("active_user_count") or 0)
                if isinstance(activity, dict)
                else 0
            )
            hot_requests = (
                int((activity.get("hot_user_proxy") or {}).get("request_count") or 0)
                if isinstance(activity, dict)
                else 0
            )
            activity_observed = active_users > 0 and hot_requests > 0
            if str(run.get("status")) != "completed":
                # A higher capacity point can fail in two different ways:
                # the bounded case timeout fires, or the real service emits
                # enough HTTP/transport errors for run_stress to exit non-zero.
                # Both are valid boundary evidence only after Search actually
                # reached the target. Setup failures and empty runner crashes
                # must remain outside the capacity calculation.
                search = metrics.get("search") or {}
                search_submitted = int(search.get("submitted") or 0)
                if search_submitted > 0:
                    if str(run.get("status") or "").upper() == "TIMEOUT":
                        timeout_levels.append(level)
                    else:
                        try:
                            below_slo = float(search.get("success_rate") or 0.0) < 0.99
                        except (TypeError, ValueError):
                            below_slo = True
                        if below_slo:
                            invalid_levels.append(level)
                continue
            search = metrics.get("search") or {}
            # Capacity is the active-user/Search boundary.  Commit flooding
            # has its own O5 gate and must not invalidate an otherwise healthy
            # Search capacity point.
            if (
                int(search.get("submitted") or 0) > 0
                and float(search.get("success_rate") or 0) >= 0.99
            ):
                if activity_observed:
                    valid_levels.append(level)
                else:
                    # A successful HTTP workload without distinct user
                    # identity evidence cannot support a DAU/hot-user claim.
                    activity_missing_levels.append(level)
            else:
                # A completed but unsuccessful higher capacity point is the
                # boundary evidence needed to claim the previous point as a
                # maximum.  A successful point by itself only proves a lower
                # bound.
                invalid_levels.append(level)
        elif scenario == "tenant-skew" and str(run.get("status")) == "completed":
            submitted = [
                int((data.get("commit") or {}).get("submitted") or 0)
                for data in (metrics.get("per_tenant") or {}).values()
                if isinstance(data, dict)
            ]
            if submitted:
                hot_user_candidates.append(max(submitted))

    # A formal capacity ladder measures distinct active sessions.  The
    # separate real admission sweep measures the next useful boundary when a
    # formal point is still healthy.  Treat it as boundary evidence only when
    # the sweep has real request rows and an observed error/rejection/transport
    # failure at a higher offered level; a configured level alone is not a
    # failure boundary.
    boundary_levels: list[int] = []
    sweep = suite.get("limit_failure_sweep")
    sweep_levels = (
        sweep.get("level_results")
        if isinstance(sweep, dict)
        else []
    )
    sweep_boundary_levels: list[int] = []
    sweep_valid_levels: list[int] = []
    if isinstance(sweep_levels, list):
        for item in sweep_levels:
            if not isinstance(item, dict):
                continue
            try:
                level = int(item.get("level"))
            except (TypeError, ValueError):
                continue
            submitted = int(item.get("submitted") or 0)
            success_rate = item.get("success_rate")
            has_boundary = bool(item.get("boundary_evidence"))
            if submitted <= 0:
                continue
            if (
                isinstance(success_rate, (int, float))
                and float(success_rate) >= 0.99
                and not has_boundary
            ):
                sweep_valid_levels.append(level)
            elif has_boundary:
                sweep_boundary_levels.append(level)
    # Do not merge admission-sweep levels into the formal capacity ladder.
    # ``limit_failure_sweep`` measures concurrent requests/workers and does
    # not establish a distinct active-user or DAU identity.  It remains useful
    # evidence for O4/O6 and for diagnosing admission limits, but must never
    # turn a formal DAU result into PASS.
    max_valid_level = max(valid_levels, default=None)
    boundary_levels = [
        level for level in invalid_levels
        if max_valid_level is not None and level > max_valid_level
    ]
    boundary_levels.extend(
        level for level in timeout_levels
        if max_valid_level is not None and level > max_valid_level
    )
    # Keep sweep failures in their own field.  A sweep boundary is not a
    # formal active-user failure boundary and therefore cannot satisfy the O1
    # PASS gate.
    has_capacity_boundary = bool(
        profile and max_valid_level is not None and boundary_levels
    )
    status = PASS if has_capacity_boundary else INCONCLUSIVE
    return _result(
        "DAU / 最大热用户容量",
        status,
        target,
        {
            "capacity_levels": levels,
            "completed_capacity_levels": completed_levels,
            "valid_capacity_levels": sorted(set(valid_levels)),
            "invalid_capacity_levels": sorted(set(invalid_levels)),
            "timeout_capacity_levels": sorted(set(timeout_levels)),
            "activity_missing_levels": sorted(set(activity_missing_levels)),
            "capacity_boundary_levels": sorted(set(boundary_levels)),
            "formal_capacity_boundary_levels": sorted(set(boundary_levels)),
            "admission_sweep_valid_levels": sorted(set(sweep_valid_levels)),
            "admission_sweep_boundary_levels": sorted(set(sweep_boundary_levels)),
            "admission_sweep": sweep if isinstance(sweep, dict) else {},
            "capacity_target_active_users": {
                key: capacity_targets[key]
                for key in sorted(capacity_targets, key=int)
            },
            "max_valid_capacity_level": max_valid_level,
            "max_valid_active_user_count": (
                capacity_targets.get(str(max_valid_level), max_valid_level)
                if max_valid_level is not None
                else None
            ),
            "max_hot_user_proxy": max(hot_user_candidates, default=None),
            "user_activity_by_capacity_level": activity_by_level,
            "max_measured_active_user_count": max(
                (
                    int(item.get("active_user_count") or 0)
                    for item in activity_by_level.values()
                ),
                default=None,
            ),
            "max_measured_hot_user_requests": max(
                (
                    int(((item.get("hot_user_proxy") or {}).get("request_count") or 0))
                    for item in activity_by_level.values()
                ),
                default=None,
            ),
            "instance_profile": profile,
        },
        (
            "已记录实际规格、有效容量阶梯和更高一档失败边界；数值是压测代理上限，不直接等于业务 DAU"
            if status == PASS
            else (
                "只有成功容量档位，缺少更高一档真实失败/超时边界；目前只能报告容量下界"
                if valid_levels
                else (
                    "有成功 Search，但缺少 active_user_count/hot_user_proxy 证据；"
                    "不能把租户请求数当作 DAU/热用户量"
                    if activity_missing_levels
                    else "有容量场景，但没有实际完成的有效容量级别"
                )
            )
        ),
    )


def _fault_isolation(suite: dict[str, Any], fault: dict[str, Any]) -> dict[str, Any]:
    evidence = fault.get("tenant_fault_isolation") if isinstance(fault, dict) else None
    if not isinstance(evidence, dict):
        evidence = suite.get("tenant_fault_isolation")
    if not isinstance(evidence, dict):
        return _result(
            "单租户故障隔离",
            INCONCLUSIVE,
            "故障租户之外的租户 Search P95 劣化 <= 20%",
            {},
            "没有按租户注入故障并对旁观租户做前后 P95 配对的真实证据",
        )
    degradation = evidence.get("bystander_p95_degradation")
    bystander = evidence.get("bystander_tenants")
    if not isinstance(bystander, dict) or not bystander:
        return _result(
            "单租户故障隔离",
            INCONCLUSIVE,
            0.20,
            evidence,
            "缺少逐旁观租户的 baseline_p95、fault_p95 和 degradation 记录",
        )
    incomplete = []
    for tenant, sample in bystander.items():
        if not isinstance(sample, dict):
            incomplete.append(str(tenant))
            continue
        required = ("baseline_p95_s", "fault_p95_s", "degradation")
        if any(sample.get(key) in (None, "") for key in required):
            incomplete.append(str(tenant))
    if incomplete:
        return _result(
            "单租户故障隔离",
            INCONCLUSIVE,
            0.20,
            {"incomplete_tenants": incomplete, "evidence": evidence},
            "逐旁观租户证据不完整，不能只用聚合最大值判定隔离",
        )
    try:
        value = float(degradation)
    except (TypeError, ValueError):
        value = None
    if value is None:
        return _result(
            "单租户故障隔离",
            INCONCLUSIVE,
            0.20,
            evidence,
            "故障隔离结果缺少旁观租户 Search P95 劣化值",
        )
    return _result(
        "单租户故障隔离",
        PASS if value <= 0.20 and evidence.get("fault_recovered") else FAIL,
        0.20,
        evidence,
        "旁观租户劣化不超过 20% 且故障已恢复"
        if value <= 0.20 and evidence.get("fault_recovered")
        else "旁观租户劣化超过 20% 或故障未恢复",
    )


def _fairness(suite: dict[str, Any]) -> dict[str, Any]:
    acceptance = suite.get("acceptance") if isinstance(suite.get("acceptance"), dict) else {}
    checks = acceptance.get("checks") if isinstance(acceptance.get("checks"), list) else []
    check = next((item for item in checks if item.get("name") == "Tenant fairness (Jain)"), None)
    # A legacy acceptance record only contains Commit throughput. It cannot
    # satisfy the current two-dimensional Commit + Search target.
    observed_check = check.get("observed") if isinstance(check, dict) else None
    has_two_dimensional_check = (
        isinstance(observed_check, dict)
        and observed_check.get("commit_jain") is not None
        and observed_check.get("search_latency_jain") is not None
    )
    if not has_two_dimensional_check:
        # Fairness is a within-workload statistic. Never add completion
        # counts from capacity, priority, and skew scenarios together: their
        # denominators and admission windows differ. The priority workload is
        # deliberately excluded because it tests Search admission ahead of
        # Commit, not equal tenant service.
        runs_by_scenario: dict[str, list[dict[str, Any]]] = {}
        for run in _suite_runs(suite):
            runs_by_scenario.setdefault(str(run.get("scenario") or ""), []).append(run)
        candidate_scenarios = [
            "fairness-steady",
            "tenant-skew",
            "tenant-skew-bounded",
            "fairness-bounded",
            "mixed",
            "commit-barrier",
            "commit-storm",
        ]
        # Prefer the workload with the broadest tenant coverage.  A bounded
        # priority smoke can legitimately finish only two barrier Commits;
        # selecting it merely because it appears first would make fairness
        # depend on the quick-mode matrix ordering rather than the evidence.
        candidates: list[tuple[int, int, str]] = []
        for priority, scenario in enumerate(candidate_scenarios):
            tenant_ids: set[str] = set()
            for run in runs_by_scenario.get(scenario, []):
                fairness = (_run_summary(run).get("metrics") or {}).get("fairness")
                per_tenant = (
                    fairness.get("commit_completed_per_tenant")
                    if isinstance(fairness, dict)
                    else None
                )
                if not isinstance(per_tenant, dict):
                    continue
                tenant_ids.update(str(key) for key in per_tenant)
            if tenant_ids:
                candidates.append(
                    (len(tenant_ids), -priority, scenario)
                )
        # ``fairness-steady`` is the only scenario whose offered load is
        # explicitly equal per tenant. Prefer it whenever it has evidence;
        # a skew/barrier workload is useful for isolation and hot-tenant
        # behavior, but is not a valid denominator for the fairness claim.
        steady_candidate = next(
            (
                item for item in candidates
                if item[2] == "fairness-steady"
            ),
            None,
        )
        selected_scenario = (
            steady_candidate[2]
            if steady_candidate is not None
            else max(candidates, key=lambda item: (item[0], item[1]))[2]
            if candidates
            else ""
        )
        selected_runs = (
            runs_by_scenario.get(selected_scenario, [])
            if selected_scenario
            else _suite_runs(suite)
        )
        counts: dict[str, int] = {}
        run_count = 0
        for run in selected_runs:
            metrics = _run_summary(run).get("metrics")
            fairness = metrics.get("fairness") if isinstance(metrics, dict) else {}
            per_tenant = (
                fairness.get("commit_completed_per_tenant")
                if isinstance(fairness, dict)
                else None
            )
            if not isinstance(per_tenant, dict):
                continue
            run_count += 1
            for tenant, value in per_tenant.items():
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                counts[str(tenant)] = counts.get(str(tenant), 0) + max(0, count)
        # Search and Commit must come from the same workload. Otherwise a
        # partial priority/barrier run can contribute zero Commit completions
        # to a different scenario's Search latency denominator.
        scoped_suite = dict(suite)
        scoped_suite["runs"] = selected_runs
        tenant_metrics = _canonicalize_tenant_metrics(
            _per_tenant_metrics(scoped_suite, selected_runs),
            suite,
        )
        # A Jain score over only the tenants that happened to finish a
        # Commit is misleading: a tenant with no submitted/observed work has
        # silently disappeared from the denominator.  Fairness requires one
        # comparable workload window covering every tenant that participated
        # in the selected scenario.
        observed_tenants: set[str] = set(tenant_metrics)
        for run in selected_runs:
            summary = _run_summary(run)
            metrics = summary.get("metrics") or {}
            per_tenant = metrics.get("per_tenant") or {}
            if isinstance(per_tenant, dict):
                for tenant, data in per_tenant.items():
                    if not isinstance(data, dict):
                        continue
                    tenant_id = _canonical_tenant_key(tenant, suite)
                    observed_tenants.add(tenant_id)
            activity = (summary.get("details") or {}).get("user_activity") or {}
            active_by_tenant = activity.get("active_users_by_tenant")
            if isinstance(active_by_tenant, dict):
                observed_tenants.update(
                    _canonical_tenant_key(item, suite)
                    for item in active_by_tenant
                )
        expected_tenants = _fairness_expected_tenants(
            suite,
            selected_runs,
            observed_tenants,
        )
        missing_tenants = sorted(expected_tenants - set(tenant_metrics))
        incomplete_tenants = sorted(
            tenant
            for tenant in expected_tenants
            if tenant in tenant_metrics
            and (
                int(tenant_metrics[tenant].get("commit_submitted") or 0) <= 0
                or tenant_metrics[tenant].get("search_p95_s") is None
            )
        )
        commit_values = [
            float(item["commit_throughput"])
            for item in tenant_metrics.values()
            if item.get("commit_throughput") is not None
        ]
        search_values = [
            1.0 / float(item["search_p95_s"])
            for item in tenant_metrics.values()
            if item.get("search_p95_s") and float(item["search_p95_s"]) > 0
        ]
        commit_jain = _jain(commit_values)
        search_jain = _jain(search_values)
        if (
            commit_jain is not None
            and search_jain is not None
            and len(tenant_metrics) >= 2
            and len(commit_values) >= 2
            and len(search_values) >= 2
            and not missing_tenants
            and not incomplete_tenants
        ):
            combined = min(commit_jain, search_jain)
            return _result(
                "Commit/Search 公平性 Jain",
                PASS if combined >= 0.90 else FAIL,
                0.90,
                {
                    "commit_jain": round(commit_jain, 4),
                    "search_latency_jain": round(search_jain, 4),
                    "jain": round(combined, 4),
                    "tenants": tenant_metrics,
                    "scenario": selected_scenario or "mixed",
                    "expected_tenants": sorted(expected_tenants),
                },
                "同时按 Commit 完成吞吐和 Search P95 的倒数计算 Jain，取两者较小值",
            )
        return _result(
            "Commit/Search 公平性 Jain",
            INCONCLUSIVE,
            0.90,
            {
                "completed_per_tenant": counts,
                "runs_with_fairness": run_count,
                "scenario": selected_scenario or "mixed",
                "expected_tenants": sorted(expected_tenants),
                "observed_tenants": sorted(observed_tenants),
                "missing_tenants": missing_tenants,
                "incomplete_tenants": incomplete_tenants,
            },
            (
                "部分租户缺少同一负载窗口的 Commit 提交或 Search P95，"
                "不能把缺失租户排除后计算公平性"
                if missing_tenants or incomplete_tenants
                else "需要至少两个租户同时具备 Commit 完成吞吐和 Search P95，才能计算双维公平性"
            ),
        )
    observed = check.get("observed")
    if has_two_dimensional_check:
        return _result(
            "Commit/Search 公平性 Jain",
            check.get("status", INCONCLUSIVE),
            0.90,
            observed,
            check.get("reason", "同时按 Commit 吞吐和 Search 延迟计算公平性"),
        )
    try:
        value = float(observed)
    except (TypeError, ValueError):
        value = None
    return _result(
        "Commit/Search 公平性 Jain",
        check.get("status", INCONCLUSIVE)
        if value is not None
        and isinstance(check.get("observed"), dict)
        and check["observed"].get("commit_jain") is not None
        and check["observed"].get("search_latency_jain") is not None
        else INCONCLUSIVE,
        0.90,
        observed,
        check.get("reason", "按逐租户完成吞吐计算"),
    )


def _priority(suite: dict[str, Any]) -> dict[str, Any]:
    runs = _scenario_runs(suite, {"search-priority-blackbox"})
    if not runs:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            "Search P95 <= 5s 且有同到达窗口证据",
            {},
            "未运行 search-priority-blackbox 场景",
        )
    completed_runs = [run for run in runs if str(run.get("status") or "") == "completed"]
    if not completed_runs:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            "Search P95 <= 5s 且有同到达窗口证据",
            {"runs": len(runs), "completed_runs": 0},
            "场景存在但没有已完成的真实运行结果",
        )
    seed_statuses = {
        str(
            run.get("seed_evidence_status")
            or ((_run_summary(run).get("details") or {}).get("search_evidence_status"))
            or ""
        ).lower()
        for run in completed_runs
    }
    if "inconclusive" in seed_statuses:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            "热缓存 Search P95 <= 5s 且有同到达窗口证据",
            {
                "runs": len(completed_runs),
                "seed_evidence_statuses": sorted(seed_statuses),
            },
            "场景已产生真实请求，但共享记忆 seed 未完成，不能把无记忆负载的结果当作热缓存优先级结论",
        )
    quality_asserted = [
        _metric(_run_summary(run), "metrics", "search", "quality_asserted")
        for run in completed_runs
    ]
    if not any(
        isinstance(value, (int, float)) and int(value) > 0
        for value in quality_asserted
    ):
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            "热缓存 Search P95 <= 5s 且至少有一条真实召回质量断言",
            {
                "runs": len(completed_runs),
                "quality_asserted": quality_asserted,
            },
            "没有真实召回命中断言；只能说明接口可用，不能证明热缓存 Search 优先级",
        )
    overlap_evidence = [
        ((_run_summary(run).get("details") or {}).get("same_window_overlap"))
        for run in completed_runs
    ]
    if not overlap_evidence or any(
        not isinstance(item, dict) or not bool(item.get("overlap_proven"))
        for item in overlap_evidence
    ):
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            "Search P95 <= 5s 且 Search/Commit 提交时间窗口真实重叠",
            {
                "runs": len(completed_runs),
                "same_window_overlap": overlap_evidence,
            },
            "缺少 Search 与 Commit 同时到达的时间窗口证据",
        )
    p95_values = [
        _metric(_run_summary(run), "metrics", "search", "latency", "p95_s")
        for run in completed_runs
    ]
    baseline_values = []
    for run in completed_runs:
        details = _run_summary(run).get("details") or {}
        baseline = details.get("baseline_search_p95_s")
        if baseline is None:
            baseline = details.get("baseline", {}).get("search_p95_s")
        if isinstance(baseline, (int, float)) and baseline > 0:
            baseline_values.append(float(baseline))
    # Formal suites commonly execute a separate baseline case before the
    # contention case. Use only completed, successful baseline evidence from
    # that same suite; never compare against an unrelated historical run.
    if not baseline_values:
        baseline_runs = [
            run for run in _suite_runs(suite)
            if str(run.get("scenario") or "") in {"baseline", "A", "A@1"}
            and str(run.get("status") or "") == "completed"
        ]
        for run in baseline_runs:
            summary = _run_summary(run)
            search = _metric(summary, "metrics", "search") or {}
            if (
                float(search.get("success_rate") or 0.0) >= 0.99
                and isinstance(
                    _metric(summary, "metrics", "search", "latency", "p95_s"),
                    (int, float),
                )
                and float(_metric(summary, "metrics", "search", "latency", "p95_s")) > 0
            ):
                baseline_values.append(
                    float(_metric(summary, "metrics", "search", "latency", "p95_s"))
                )
    if not baseline_values:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            "有无 Commit 洪泛基线和劣化比例，且 Search P95 <= 5s",
            {"runs": len(completed_runs)},
            "缺少同配置、无洪泛 Search 基线 P95，不能仅凭绝对 P95 证明优先级",
        )
    commit_counts = [
        _metric(_run_summary(run), "metrics", "commit", "submitted")
        for run in completed_runs
    ]
    commit_counts = [
        int(value) for value in commit_counts
        if isinstance(value, (int, float)) and value > 0
    ]
    if not commit_counts:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            5.0,
            {"runs": len(completed_runs), "commit_submitted": 0},
            "场景存在但没有 Commit 洪泛样本，不能证明 Search 优先级",
        )
    commit_submitted = sum(commit_counts)
    if commit_submitted < MIN_PRIORITY_COMMIT_FLOOD:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            5.0,
            {
                "runs": len(completed_runs),
                "commit_submitted": commit_submitted,
                "minimum_commit_flood": MIN_PRIORITY_COMMIT_FLOOD,
            },
            (
                "Commit 到达量不足以构成洪泛："
                f"{commit_submitted} < {MIN_PRIORITY_COMMIT_FLOOD}；"
                "只能报告 Search P95，不能验收严格优先级"
            ),
        )
    values = [float(value) for value in p95_values if isinstance(value, (int, float))]
    if not values:
        return _result(
            "Search 优先于 Commit",
            INCONCLUSIVE,
            5.0,
            {"runs": len(completed_runs)},
            "场景运行了，但没有 Search P95 数据",
        )
    worst = max(values)
    baseline = min(baseline_values)
    degradation = worst / baseline if baseline > 0 else None
    max_degradation = 2.0
    return _result(
        "Search 优先于 Commit",
        PASS if worst <= 5.0 and degradation <= max_degradation else FAIL,
        {"search_p95_max_s": 5.0, "degradation_max_ratio": max_degradation},
        {
            "worst_search_p95_s": worst,
            "baseline_search_p95_s": baseline,
            "degradation_ratio": round(degradation, 4),
            "commit_submitted": commit_submitted,
            "minimum_commit_flood": MIN_PRIORITY_COMMIT_FLOOD,
        },
        (
            "同到达窗口 Search P95 在 5 秒内且相对基线劣化不超过 2x"
            if worst <= 5.0 and degradation <= max_degradation
            else "Search P95 超过 5 秒或相对基线劣化超过 2x"
        ),
    )


def _recovery(recovery: dict[str, Any]) -> dict[str, Any]:
    if not recovery:
        return _result(
            "Commit kill-9 恢复与重放",
            INCONCLUSIVE,
            "202 接受的 Commit 恢复后 100% 完成并通过消息对账",
            {},
            "未提供 kill-9/restart 真实控制结果",
        )
    status = str(recovery.get("status") or INCONCLUSIVE)
    replay_rate = recovery.get("replay_rate")
    if replay_rate is None:
        replay_rate = recovery.get("recovered_commit_rate")
    # Multi-iteration probes expose an explicit denominator.  Do not fall
    # back to a single aggregate status when only some 202 samples were
    # actually exercised.
    aggregated_iterations = recovery.get("attempted_iterations") is not None
    attempted_iterations = recovery.get("attempted_iterations")
    accepted_202_count = recovery.get("accepted_202_count")
    all_conditions_pass_count = recovery.get("all_conditions_pass_count")
    if aggregated_iterations:
        try:
            attempted_iterations = int(attempted_iterations or 0)
        except (TypeError, ValueError):
            attempted_iterations = 0
        try:
            accepted_202_count = int(accepted_202_count or 0)
        except (TypeError, ValueError):
            accepted_202_count = 0
        try:
            all_conditions_pass_count = int(all_conditions_pass_count or 0)
        except (TypeError, ValueError):
            all_conditions_pass_count = 0
        if replay_rate is None and accepted_202_count:
            replay_rate = all_conditions_pass_count / accepted_202_count
    try:
        rate = float(replay_rate)
    except (TypeError, ValueError):
        rate = None
    cursor = recovery.get("cursor_reconciliation")
    cursor_proven = isinstance(cursor, dict) and str(cursor.get("status")) == PASS
    message_reconciliation = recovery.get("message_reconciliation")
    message_set_proven = bool(
        recovery.get("message_set_reconciled")
        or (
            isinstance(message_reconciliation, dict)
            and str(message_reconciliation.get("status") or "") == PASS
        )
    )
    order_reconciliation = recovery.get("order_reconciliation")
    order_proven = (
        isinstance(order_reconciliation, dict)
        and str(order_reconciliation.get("status") or "") == PASS
    )
    idempotency = recovery.get("idempotency_reconciliation")
    idempotency_status = (
        str(idempotency.get("status") or INCONCLUSIVE)
        if isinstance(idempotency, dict)
        else INCONCLUSIVE
    )
    idempotency_replay = recovery.get("idempotency_replay")
    same_archive = bool(
        isinstance(idempotency_replay, dict)
        and idempotency_replay.get("same_archive") is True
    )
    accepted_202 = recovery.get("accepted_202") is True
    terminal = recovery.get("commit_terminal")
    final_state = (
        terminal[-1].get("state")
        if isinstance(terminal, list) and terminal and isinstance(terminal[-1], dict)
        else None
    )
    # ``replayed=true`` is an optional response annotation.  Some EchoMem
    # versions safely deduplicate the same idempotency key and return the
    # original archive without setting that annotation.  The black-box
    # durability target is proven by 202 + restart + completed terminal state
    # + durable message/cursor/order reconciliation + same archive on replay.
    replay_evidence = same_archive or idempotency_status == PASS
    single_sample_passed = (
        accepted_202
        and status != FAIL
        and (rate is None or rate >= 1.0)
        and recovery.get("recovered") is not False
        and final_state == "completed"
        and cursor_proven
        and message_set_proven
        and order_proven
        and replay_evidence
    )
    aggregate_passed = (
        aggregated_iterations
        and attempted_iterations > 0
        and accepted_202_count == attempted_iterations
        and accepted_202_count > 0
        and all_conditions_pass_count == accepted_202_count
        and rate is not None
        and rate >= 1.0
    )
    passed = aggregate_passed or (
        not aggregated_iterations and single_sample_passed
    )
    if idempotency_status == FAIL and not same_archive:
        verdict = FAIL
        reason = "服务恢复但同一幂等键没有返回原 archive，幂等重放失败"
    elif passed:
        verdict = PASS
        reason = (
            (
                f"{accepted_202_count}/{attempted_iterations} 个独立样本均收到 202；"
                "全部完成恢复、消息集合/cursor/顺序对账和幂等重放"
                if aggregated_iterations
                else
                "收到 202；服务恢复；Commit completed；消息集合、cursor、顺序对账通过；"
                "同一幂等键仍返回原 archive"
            )
        )
    elif aggregated_iterations and accepted_202_count < attempted_iterations:
        verdict = INCONCLUSIVE
        reason = (
            f"只有 {accepted_202_count}/{attempted_iterations} 个样本收到 202，"
            "恢复统计前提不完整"
        )
    elif status in {FAIL, INCONCLUSIVE}:
        verdict = status
        reason = "202、恢复、Commit 终态、消息/cursor/顺序对账或幂等重放证据不完整/失败"
    else:
        verdict = INCONCLUSIVE
        reason = "202、恢复、Commit 终态、消息/cursor/顺序对账或幂等重放证据不完整/失败"
    observed = dict(recovery)
    observed["accepted_202_required"] = True
    observed["aggregated_iterations"] = aggregated_iterations
    if aggregated_iterations:
        observed["accepted_202_count"] = accepted_202_count
        observed["attempted_iterations"] = attempted_iterations
        observed["all_conditions_pass_count"] = all_conditions_pass_count
    observed["final_state"] = final_state
    observed["same_archive_on_idempotency_replay"] = same_archive
    observed["replayed_flag_observed"] = (
        idempotency_replay.get("replayed")
        if isinstance(idempotency_replay, dict)
        else None
    )
    observed["replayed_flag_is_optional_annotation"] = True
    return _result(
        "Commit kill-9 恢复与重放",
        verdict,
        1.0,
        observed,
        reason,
    )


def _observability(
    capability: dict[str, Any],
    suite: dict[str, Any],
    tenant_observability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = capability.get("checks") if isinstance(capability.get("checks"), list) else []
    metric = next((item for item in checks if item.get("name") == "Prometheus B7 metrics"), None)
    required = {"lane_queued", "lane_wait", "lane_exec", "lane_rejected"}
    present_map = (metric or {}).get("present") or {}
    present = {
        str(name) for name, available in present_map.items() if bool(available)
    }
    missing = sorted(required - present)
    coverage = [
        _metric(_run_summary(run), "details", "pr421_metric_coverage")
        for run in _suite_runs(suite)
    ]
    coverage = [item for item in coverage if isinstance(item, dict)]
    lane_quartets: dict[str, dict[str, bool]] = {}
    fanout_engines: dict[str, dict[str, bool]] = {}
    legacy_tenant_lanes: dict[str, list[str]] = {}
    for item in coverage:
        for lane, quartet in (item.get("lane_quartets") or {}).items():
            if isinstance(quartet, dict):
                existing = lane_quartets.setdefault(
                    str(lane),
                    {
                        "queued": False,
                        "wait": False,
                        "exec": False,
                        "rejected": False,
                    },
                )
                # Coverage is accumulated across scenario samples. Do not
                # let a later, partial sample erase evidence observed earlier.
                for key in ("queued", "wait", "exec", "rejected"):
                    existing[key] = bool(existing[key] or quartet.get(key))
        for engine, observations in (item.get("fanout_engines") or {}).items():
            if isinstance(observations, dict):
                existing = fanout_engines.setdefault(
                    str(engine),
                    {"exec": False, "skipped": False},
                )
                for key in ("exec", "skipped"):
                    existing[key] = bool(existing[key] or observations.get(key))
        for tenant, data in (item.get("per_tenant_quartets") or {}).items():
            if not isinstance(data, dict):
                continue
            lane_map = data.get("per_lane") or {}
            if isinstance(lane_map, dict):
                legacy_tenant_lanes[str(tenant)] = sorted(
                    lane for lane, quartet in lane_map.items()
                    if isinstance(quartet, dict)
                    and all(bool(quartet.get(key)) for key in (
                        "queued", "wait", "exec", "rejected"
                    ))
                )
            elif all(bool(data.get(key)) for key in (
                "queued", "wait", "exec", "rejected"
            )):
                legacy_tenant_lanes[str(tenant)] = list(data.get("lanes") or [])
    complete_lanes = sorted(
        lane for lane, quartet in lane_quartets.items()
        if all(bool(quartet.get(key)) for key in (
            "queued", "wait", "exec", "rejected"
        ))
    )
    # The active EchoMem configuration determines which scheduler lanes are
    # real for this run. Disabled optional engines must not be reported as
    # missing, while a run with no lane observations remains inconclusive.
    expectations = suite.get("observability_expectations")
    declared_lanes = (
        expectations.get("lanes")
        if isinstance(expectations, dict)
        else None
    )
    expected_lanes = {
        str(item).strip()
        for item in declared_lanes or []
        if str(item).strip()
    } or set(lane_quartets)
    declared_engines = (
        expectations.get("fanout_engines")
        if isinstance(expectations, dict)
        else None
    )
    expected_fanout_engines = {
        str(item).strip()
        for item in declared_engines or []
        if str(item).strip()
    }
    missing_lanes = sorted(expected_lanes - set(complete_lanes))
    complete_fanout_engines = sorted(
        engine for engine, observations in fanout_engines.items()
        if observations.get("exec") and observations.get("skipped")
    )
    missing_fanout_engines = sorted(
        expected_fanout_engines - set(complete_fanout_engines)
    )
    snapshot_required = isinstance(tenant_observability, dict)
    snapshot_missing = (
        list(tenant_observability.get("missing") or [])
        if isinstance(tenant_observability, dict)
        else []
    )
    snapshot_status = (
        str(tenant_observability.get("status") or INCONCLUSIVE)
        if isinstance(tenant_observability, dict)
        else None
    )
    snapshot_complete = (
        snapshot_status == PASS
        and not snapshot_missing
        and bool((tenant_observability or {}).get("rows"))
    )
    if (missing or not expected_lanes) and not coverage:
        return _result(
            "分层/分租户调度可观测性",
            INCONCLUSIVE,
            "每个实际 lane 有 queued/wait/exec/rejected 四元组，且有 fan-out 证据",
            {
                "missing": missing,
                "expected_lanes": sorted(expected_lanes),
                "expected_fanout_engines": sorted(expected_fanout_engines),
                "missing_lanes": sorted(expected_lanes),
                "missing_fanout_engines": sorted(expected_fanout_engines),
                "tenant_observability": tenant_observability,
            },
            (
                "没有采到任何实际 lane 的 Prometheus B7 覆盖证据"
                if not expected_lanes
                else "没有完整的 Prometheus B7 指标覆盖证据"
            ),
        )
    observed = {
        "missing": missing,
        "coverage_samples": len(coverage),
        "lane_quartets": lane_quartets,
        "complete_lanes": complete_lanes,
        "missing_lanes": missing_lanes,
        "fanout_engines": fanout_engines,
        "complete_fanout_engines": complete_fanout_engines,
        "expected_lanes": sorted(expected_lanes),
        "expected_fanout_engines": sorted(expected_fanout_engines),
        "missing_fanout_engines": missing_fanout_engines,
        "legacy_tenant_lanes": legacy_tenant_lanes,
        "tenant_observability": tenant_observability,
        "tenant_observability_required": snapshot_required,
        "tenant_observability_complete": snapshot_complete,
    }
    legacy_complete_tenants = [
        tenant for tenant, lanes in legacy_tenant_lanes.items()
        if LEGACY_LANES.issubset(set(lanes))
    ]
    complete = (
        not missing
        and not missing_lanes
        and bool(complete_fanout_engines)
        and not missing_fanout_engines
        and (not snapshot_required or snapshot_complete)
    ) or (
        # Accept old artifacts only when they explicitly contain the legacy
        # per-tenant evidence and no bounded-label violation was recorded.
        not lane_quartets
        and not missing
        and bool(legacy_complete_tenants)
        and not any(item.get("bounded_label_violations") for item in coverage)
        and (not snapshot_required or snapshot_complete)
    )
    observed["legacy_complete_tenants"] = legacy_complete_tenants
    return _result(
        "分层/分租户调度可观测性",
        PASS if complete else INCONCLUSIVE,
        "每个预期 lane 都有 queued/wait/exec/rejected 四元组，且至少一个 engine 有 fan-out 证据",
        observed,
        (
            "全部预期 lane 和 fan-out 指标均有真实服务端证据"
            if complete
            else "指标族、预期 lane 四元组或 fan-out 覆盖不足，不能完整验收"
        ),
    )


def evaluate(
    suite: dict[str, Any],
    *,
    capability: dict[str, Any] | None = None,
    recovery: dict[str, Any] | None = None,
    fault: dict[str, Any] | None = None,
    tenant_observability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = [
        _capacity(suite),
        _fault_isolation(suite, fault or {}),
        _fairness(suite),
        _priority(suite),
        _recovery(recovery or {}),
        _observability(capability or {}, suite, tenant_observability),
    ]
    statuses = [item["status"] for item in checks]
    overall = FAIL if FAIL in statuses else INCONCLUSIVE if INCONCLUSIVE in statuses else PASS
    return {
        "version": "scheduler-acceptance-v2",
        "created_at": _now(),
        "overall": overall,
        "checks": checks,
        "evidence": {
            "suite": suite.get("created_at") or suite.get("out_dir"),
            "capability": bool(capability),
            "recovery": bool(recovery),
            "fault": bool(fault),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the six 4U8G scheduler targets")
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--capability", type=Path)
    parser.add_argument("--recovery", type=Path)
    parser.add_argument("--fault", type=Path)
    parser.add_argument("--tenant-observability", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate(
        _load(args.suite),
        capability=_load(args.capability),
        recovery=_load(args.recovery),
        fault=_load(args.fault),
        tenant_observability=_load(
            Path(args.tenant_observability)
        ) if args.tenant_observability else None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall": result["overall"], "checks": result["checks"]}, ensure_ascii=False))
    return 0 if result["overall"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
