#!/usr/bin/env python3
"""Conservative PR421 acceptance checks for formal stress-suite results.

The checks consume only recorded run summaries and request CSVs.  Missing
server evidence is never inferred from client timing, and capabilities that
require an unavailable EchoMem control plane are reported explicitly.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

PR421_LANES = {
    "recall_engine",
    "recall_intent_llm",
    "recall_query_embedding",
    "recall_rerank",
    "commit",
}

# Review status is kept separate from measured PR421 gates. A harness item
# can be resolved while the corresponding EchoMem capability is unavailable.
PR28_REVIEW_RESOLUTION = [
    {
        "item": "Commit barrier and tenant distributions",
        "status": "RESOLVED",
        "evidence": "commit-barrier; uniform/zipf/explicit distributions",
    },
    {
        "item": "Retry-After retry and retry audit",
        "status": "RESOLVED",
        "evidence": "commit_with_retry; commit_results.csv; summary.json",
    },
    {
        "item": "Server-observe boundary and telemetry completeness",
        "status": "RESOLVED",
        "evidence": "server-observe; server_* fields; metric coverage",
    },
    {
        "item": "Real multi-tenant isolation evidence",
        "status": "RESOLVED",
        "evidence": "independent tenant credentials; directed marker probes",
    },
    {
        "item": "Commit final completion",
        "status": "PARTIAL",
        "evidence": "terminal-state polling is present; cursor/message-set reconciliation is available when configured",
    },
    {
        "item": "Saturation discipline",
        "status": "PARTIAL",
        "evidence": "saturation and rejection contract are present; queue-full precondition and recovery check are absent",
    },
    {
        "item": "Reproducible EchoMem environment",
        "status": "PARTIAL",
        "evidence": "runner environment is reproducible; target resource/profile/MySQL topology is not owned by the harness",
    },
    {
        "item": "k6 toolchain",
        "status": "PARTIAL",
        "evidence": "real k6 script and Python reconciliation entrypoint are available; installation is deployment-owned",
    },
    {
        "item": "Fault injection and restart recovery",
        "status": "PARTIAL",
        "evidence": "real command/HTTP/Docker controls and PID/container SIGKILL recovery are available; deployment controls are required",
    },
    {
        "item": "Incident regression and full capacity ladder",
        # The capacity ladder is now in formal_suite. Incident-specific
        # controls still require an explicit deployment plan, so this is a
        # platform limitation rather than an EchoMem defect.
        "status": "PARTIAL",
        "evidence": "formal_suite capacity-2/4/8/16/32/64/128; incident controls require deployment plan",
    },
    {
        "item": "Persistence reconciliation judge",
        "status": "PARTIAL",
        "evidence": "cursor/message-set export adapter is available when EchoMem exposes the endpoint",
    },
]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _is_admission_rejection(row: dict[str, Any]) -> bool:
    """Recognize an explicit overload rejection, including legacy HTTP 400.

    EchoMem currently reports ``too many ... in flight`` as HTTP 400 on some
    paths.  That is still useful evidence of admission control, but it must
    remain distinguishable from the required 429/503 wire contract.
    """
    code = _number(row.get("status_code"))
    if code is not None and int(code) in {429, 503}:
        return True
    if str(row.get("error_class") or "").lower() == "admission_rejected":
        return True
    text = " ".join(
        str(row.get(key) or "")
        for key in ("reason_code", "error_detail", "error", "message")
    ).lower()
    return any(
        marker in text
        for marker in (
            "too many",
            "in flight",
            "rate limit",
            "queue full",
            "overload",
            "capacity",
        )
    )


def _has_retry_after(row: dict[str, Any]) -> bool:
    value = row.get("retry_after_s") or row.get("retry_after")
    return value not in (None, "")


def _result(
    name: str,
    status: str,
    *,
    target: Any = None,
    observed: Any = None,
    evidence: str = "",
    reason: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "target": target,
        "observed": observed,
        "evidence": evidence,
        "reason": reason,
    }


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return run.get("summary") or {}


def _runs_for(manifest: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
    return [
        run for run in manifest.get("runs") or []
        if str(run.get("scenario") or "") == scenario
    ]


def _report4_concurrency(scenario: str) -> int | None:
    marker = "-c"
    if marker not in scenario:
        return None
    try:
        return int(scenario.rsplit(marker, 1)[1])
    except (TypeError, ValueError):
        return None


def _search_success_rate(run: dict[str, Any]) -> float | None:
    return _number((_run_summary(run).get("metrics") or {}).get("search", {}).get("success_rate"))


def _metric_values(
    runs: list[dict[str, Any]],
    path: tuple[str, ...],
) -> list[float]:
    values: list[float] = []
    for run in runs:
        current: Any = _run_summary(run)
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        number = _number(current)
        if number is not None:
            values.append(number)
    return values


def _target_coverage(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = manifest.get("runs") or []
    coverages = [
        (_run_summary(run).get("details") or {}).get("pr421_metric_coverage")
        for run in runs
    ]
    coverages = [item for item in coverages if isinstance(item, dict)]
    if not coverages:
        return _result(
            "B7 lane/fan-out metrics",
            INCONCLUSIVE,
            evidence="details.pr421_metric_coverage",
            reason="没有采集到 PR421 B7 指标覆盖证据",
        )
    missing = sorted({
        str(key)
        for item in coverages
        for key in (item.get("missing") or [])
    })
    label_violations = [
        violation
        for item in coverages
        for violation in (item.get("bounded_label_violations") or [])
    ]
    lane_coverage = [
        item.get("lane_quartets") or {}
        for item in coverages
        if isinstance(item.get("lane_quartets"), dict)
    ]
    fanout_coverage = [
        item.get("fanout_engines") or {}
        for item in coverages
        if isinstance(item.get("fanout_engines"), dict)
    ]
    expected_lanes = PR421_LANES
    complete_lanes = sorted({
        str(lane)
        for coverage in lane_coverage
        for lane, quartet in coverage.items()
        if isinstance(quartet, dict)
        and all(bool(quartet.get(family)) for family in (
            "queued", "wait", "exec", "rejected"
        ))
    })
    complete_fanout_engines = sorted({
        str(engine)
        for coverage in fanout_coverage
        for engine, facts in coverage.items()
        if isinstance(facts, dict)
        and facts.get("exec") and facts.get("skipped")
    })
    # Legacy results used per-tenant labels. Keep them readable, but don't
    # require that forbidden shape for new PR421 evidence.
    legacy_tenants = sorted({
        str(tenant)
        for item in coverages
        for tenant, families in (item.get("per_tenant_quartets") or {}).items()
        if isinstance(families, dict)
        and all(bool(families.get(family)) for family in (
            "queued", "wait", "exec", "rejected"
        ))
    })
    present = sorted({
        str(key)
        for item in coverages
        for key, value in (item.get("present") or {}).items()
        if value
    })
    status = (
        PASS
        if (
            not missing
            and not label_violations
            and expected_lanes.issubset(set(complete_lanes))
            and complete_fanout_engines
        )
        or (
            not lane_coverage
            and not missing
            and not label_violations
            and len(legacy_tenants) >= 2
        )
        else INCONCLUSIVE
    )
    return _result(
        "B7 lane/fan-out metrics",
        status,
        target="6 metric families with bounded labels",
        observed={
            "present": present,
            "missing": missing,
            "bounded_label_violations": label_violations,
            "complete_lanes": complete_lanes,
            "missing_lanes": sorted(expected_lanes - set(complete_lanes)),
            "complete_fanout_engines": complete_fanout_engines,
            "legacy_tenants_with_complete_quartets": legacy_tenants,
        },
        evidence="details.pr421_metric_coverage",
        reason=(
            "全部指标族、预期 lane 四元组和 fan-out 均有服务端证据"
            if status == PASS
            else (
                "指标标签不符合 bounded-label 契约，不能证明服务端 lane/fan-out 行为"
                if label_violations and not missing
                else "指标族、预期 lane 四元组或 fan-out 覆盖不足，不能证明调度可观测"
            )
        ),
    )


def _search_success_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = [
        run for run in manifest.get("runs") or []
        if (
            str(run.get("scenario") or "") in {"mixed", "search-storm", "saturation"}
            or str(run.get("scenario") or "").startswith(("A-c", "C", "D-c"))
        )
    ]
    rates = _metric_values(runs, ("metrics", "search", "success_rate"))
    if not rates:
        return _result(
            "Search success rate",
            INCONCLUSIVE,
            target=0.999,
            evidence="metrics.search.success_rate",
            reason="没有足够的 Search 成功率数据",
        )
    observed = min(rates)
    target = 0.99 if any(
        str(run.get("scenario") or "").startswith(("A-c", "C", "D-c"))
        for run in runs
    ) else 0.999
    return _result(
        "Search success rate",
        PASS if observed >= target else FAIL,
        target=target,
        observed=observed,
        evidence="metrics.search.success_rate",
        reason="按所有选定压力场景的最差轮次判定",
    )


def _report6_quality_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Reject report(6) runs that hide empty or unverified retrievals."""
    runs = [
        run for run in manifest.get("runs") or []
        if str(run.get("scenario") or "").startswith(
            ("A@", "B@", "C", "D@")
        )
    ]
    if not runs:
        return _result(
            "report(6) Search quality assertion",
            INCONCLUSIVE,
            evidence="metrics.search.quality_failures",
            reason="没有 report(6) 场景结果",
        )
    search_metrics = [
        (_run_summary(run).get("metrics") or {}).get("search") or {}
        for run in runs
    ]
    asserted = sum(int(item.get("quality_asserted") or 0) for item in search_metrics)
    failures = sum(int(item.get("quality_failures") or 0) for item in search_metrics)
    seed_rows = [
        row
        for run in runs
        for row in ((_run_summary(run).get("details") or {}).get("quality_seed") or [])
    ]
    seed_failures = sum(not bool(row.get("completed")) for row in seed_rows)
    if not asserted:
        return _result(
            "report(6) Search quality assertion",
            INCONCLUSIVE,
            target="all measured Search requests have a deterministic marker assertion",
            observed={"quality_asserted": asserted, "seed_failures": seed_failures},
            evidence="metrics.search.quality_asserted; details.quality_seed",
            reason="Search 未启用确定性 marker 断言，不能证明空召回没有被假通过",
        )
    status = FAIL if failures or seed_failures else PASS
    return _result(
        "report(6) Search quality assertion",
        status,
        target="quality_failures=0 and seed_failures=0",
        observed={
            "quality_asserted": asserted,
            "quality_failures": failures,
            "seed_failures": seed_failures,
        },
        evidence="metrics.search.quality_failures; details.quality_seed",
        reason=(
            "所有 marker Search 均有真实召回证据"
            if status == PASS
            else "存在 seed 未完成或 marker 未召回，不能把 HTTP 200 当作检索成功"
        ),
    )


def _search_isolation_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    all_runs = manifest.get("runs") or []
    report4 = any(
        str(run.get("scenario") or "").startswith(("A-c", "C", "D-c"))
        for run in all_runs
    )
    if report4:
        baselines: dict[int, list[float]] = {}
        stressed: dict[int, list[float]] = {}
        invalid: list[str] = []
        for run in all_runs:
            scenario = str(run.get("scenario") or "")
            concurrency = _report4_concurrency(scenario)
            p95 = _number(
                ((_run_summary(run).get("metrics") or {}).get("search") or {})
                .get("latency", {})
                .get("p95_s")
            )
            if concurrency is None or p95 is None or p95 <= 0:
                continue
            if scenario.startswith("A-c"):
                if (_search_success_rate(run) or 0.0) < 0.99:
                    invalid.append(scenario)
                else:
                    baselines.setdefault(concurrency, []).append(p95)
            elif scenario.startswith(("C", "D-c")):
                stressed.setdefault(concurrency, []).append(p95)
        pairs = [
            (concurrency, max(values), max(stressed[concurrency]))
            for concurrency, values in baselines.items()
            if concurrency in stressed and values and stressed[concurrency]
        ]
        if invalid or not pairs:
            return _result(
                "Search P95 isolation ratio",
                INCONCLUSIVE,
                target=1.20,
                evidence="report4 A/C/D paired metrics.search",
                observed={"invalid_baselines": invalid, "paired_concurrency": [p[0] for p in pairs]},
                reason="report4 的 A 纯读基线必须先达到 99% Search 成功率，且按同一并发档配对",
            )
        ratios = [
            {"concurrency": concurrency, "ratio": stress_p95 / baseline_p95}
            for concurrency, baseline_p95, stress_p95 in pairs
        ]
        ratio = max(item["ratio"] for item in ratios)
        return _result(
            "Search P95 isolation ratio",
            PASS if ratio <= 1.20 else FAIL,
            target=1.20,
            observed={"worst_ratio": ratio, "by_concurrency": ratios},
            evidence="report4 A/C/D paired metrics.search.latency.p95_s",
            reason="只比较同一并发档且成功率至少 99% 的 A 基线与压力场景，按目标要求限制劣化不超过 20%",
        )

    baseline = _metric_values(
        _runs_for(manifest, "baseline"),
        ("metrics", "search", "latency", "p95_s"),
    )
    stressed = _metric_values(
        _runs_for(manifest, "mixed") + _runs_for(manifest, "search-storm"),
        ("metrics", "search", "latency", "p95_s"),
    )
    if not baseline or not stressed or min(baseline) <= 0:
        return _result(
            "Search P95 isolation ratio",
            INCONCLUSIVE,
            target=1.20,
            evidence="baseline/mixed/search-storm metrics.search.latency.p95_s",
            reason="缺少有效基线或压力场景的成功请求 P95",
        )
    ratio = max(stressed) / min(baseline)
    return _result(
        "Search P95 isolation ratio",
        PASS if ratio <= 1.20 else FAIL,
        target=1.20,
        observed=ratio,
        evidence={"baseline_p95_s": baseline, "stressed_p95_s": stressed},
        reason=(
            "当前按已记录轮次的最差压力 P95/最优基线 P95计算；"
            "仅比较成功请求，超时率由 Search success rate 单独判定"
        ),
    )


def _fairness_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = (
        _runs_for(manifest, "tenant-skew")
        + _runs_for(manifest, "tenant-skew-bounded")
        + _runs_for(manifest, "fairness-bounded")
        + _runs_for(manifest, "mixed")
    )
    values: list[float] = []
    evidence: list[dict[str, Any]] = []
    for run in runs:
        fairness = (_run_summary(run).get("metrics") or {}).get("fairness") or {}
        completed = fairness.get("commit_completed_per_tenant") or {}
        rates = [_number(value) for value in completed.values()]
        rates = [value for value in rates if value is not None and value >= 0]
        if len(rates) < 2 or sum(value * value for value in rates) == 0:
            continue
        total = sum(rates)
        jain = total * total / (len(rates) * sum(value * value for value in rates))
        values.append(jain)
        evidence.append({"commit_completed_per_tenant": completed, "jain": jain})
    if not values:
        return _result(
            "Tenant fairness (Jain)",
            INCONCLUSIVE,
            target=0.90,
            evidence="metrics.fairness.commit_completed_per_tenant",
            reason="没有至少两个租户的有效 Commit 完成吞吐样本",
        )
    observed = min(values)
    return _result(
        "Tenant fairness (Jain)",
        PASS if observed >= 0.90 else FAIL,
        target=0.90,
        observed=observed,
        evidence=evidence,
        reason="按逐租户 Commit 完成吞吐计算并取最差轮次；延迟公平性另行展示",
    )


def _commit_completion_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = (
        _runs_for(manifest, "commit-barrier")
        + _runs_for(manifest, "tenant-skew")
        + [
            run for run in manifest.get("runs") or []
            if str(run.get("scenario") or "").startswith(("B-c", "C", "D-c"))
        ]
    )
    rates = _metric_values(runs, ("metrics", "commit", "success_rate"))
    if not rates:
        return _result(
            "Accepted Commit eventual completion",
            INCONCLUSIVE,
            target=1.0,
            evidence="metrics.commit.success_rate",
            reason="没有 Commit 最终状态数据",
        )
    observed = min(rates)
    return _result(
        "Accepted Commit eventual completion",
        PASS if observed >= 1.0 else FAIL,
        target=1.0,
        observed=observed,
        evidence="metrics.commit.success_rate",
        reason=(
            "当前按 runner 最终状态统计；尚未包含 cursor/消息集合对账"
        ),
    )


def _rejection_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = _runs_for(manifest, "saturation")
    rejected = total = 0
    rejected_latencies: list[float] = []
    response_fields_complete = True
    retry_after_complete = True
    reason_code_complete = True
    wire_status_complete = True
    status_breakdown: dict[str, int] = {}
    evidence_sources: list[str] = []
    for run in runs:
        output_dir = Path(run.get("output_dir") or "")
        rows = _read_csv(output_dir / "search_results.csv")
        evidence_sources.append(str(output_dir / "search_results.csv"))
        for row in rows:
            code = _number(row.get("status_code"))
            if code is None:
                continue
            total += 1
            if _is_admission_rejection(row):
                rejected += 1
                status_key = str(int(code))
                status_breakdown[status_key] = status_breakdown.get(status_key, 0) + 1
                if int(code) not in {429, 503}:
                    wire_status_complete = False
                latency = _number(row.get("end_to_end_s")) or _number(row.get("elapsed_s"))
                if latency is not None:
                    rejected_latencies.append(latency)
                # A rejection is only contract-complete when both the
                # retry hint and the server-provided reason are present.
                if not _has_retry_after(row):
                    retry_after_complete = False
                if not row.get("reason_code"):
                    reason_code_complete = False
                if not _has_retry_after(row) or not row.get("reason_code"):
                    response_fields_complete = False
    sweep = manifest.get("limit_failure_sweep")
    if isinstance(sweep, dict):
        sweep_path = Path(str(sweep.get("requests_path") or ""))
        rows = _read_csv(sweep_path)
        if rows:
            evidence_sources.append(str(sweep_path))
        for row in rows:
            code = _number(row.get("status_code"))
            if code is None:
                continue
            total += 1
            if _is_admission_rejection(row):
                rejected += 1
                status_key = str(int(code))
                status_breakdown[status_key] = status_breakdown.get(status_key, 0) + 1
                if int(code) not in {429, 503}:
                    wire_status_complete = False
                latency = _number(row.get("elapsed_s"))
                if latency is not None:
                    rejected_latencies.append(latency)
                if not _has_retry_after(row):
                    retry_after_complete = False
                if not row.get("reason_code"):
                    reason_code_complete = False
                if not _has_retry_after(row) or not row.get("reason_code"):
                    response_fields_complete = False
    if not total:
        return _result(
            "Saturation rejection rate",
            INCONCLUSIVE,
            target={"rate_max": 0.05, "latency_max_s": 1.0},
            evidence=evidence_sources or "search_results.csv",
            reason="saturation/真实限流阶梯没有有效响应",
        )
    if not rejected:
        return _result(
            "Saturation rejection rate",
            INCONCLUSIVE,
            target={"rate_max": 0.05, "latency_max_s": 1.0},
            observed={"rejection_rate": 0.0, "responses": total},
            evidence=evidence_sources or "search_results.csv",
            reason="没有实际 429/503 拒绝样本，无法验证 PR421 拒绝响应契约",
        )
    rate = rejected / total
    max_latency = max(rejected_latencies) if rejected_latencies else None
    observed = {
        "rejection_rate": rate,
        "max_rejection_latency_s": max_latency,
        "rejected": rejected,
        "status_breakdown": status_breakdown,
        "wire_status_complete": wire_status_complete,
        "retry_after_complete": retry_after_complete,
        "reason_code_complete": reason_code_complete,
        "response_fields_complete": response_fields_complete,
    }
    if not response_fields_complete or not wire_status_complete:
        status = FAIL
        problems = []
        if not wire_status_complete:
            problems.append("admission 拒绝使用了 HTTP 400 而不是 429/503")
        if not response_fields_complete:
            problems.append("缺少 Retry-After 或 reason_code")
        reason = "；".join(problems)
    elif rate > 0.05 or (max_latency is not None and max_latency > 1.0):
        status = FAIL
        reason = "拒绝率或拒绝响应耗时超过 PR421 门槛"
    else:
        status = PASS
        reason = "拒绝率和拒绝响应耗时均未超过门槛"
    return _result(
        "Saturation rejection rate",
        status,
        target={"rate_max": 0.05, "latency_max_s": 1.0},
        observed=observed,
        evidence=evidence_sources or "search_results.csv",
        reason=reason,
    )


def _hot_tenant_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = _runs_for(manifest, "tenant-skew") + _runs_for(manifest, "tenant-skew-bounded")
    ratios: list[float] = []
    evidence: list[dict[str, Any]] = []
    for run in runs:
        metrics = _run_summary(run).get("metrics") or {}
        per_tenant = metrics.get("per_tenant") or {}
        if len(per_tenant) < 4:
            continue
        hot = max(
            per_tenant,
            key=lambda tenant: int(
                ((per_tenant[tenant].get("commit") or {}).get("submitted")) or 0
            ),
        )
        bystander_p50 = []
        for tenant, data in per_tenant.items():
            if tenant == hot:
                continue
            p50 = _number(
                ((data.get("commit") or {}).get("completion") or {}).get("p50_s")
            )
            if p50 is not None and p50 > 0:
                bystander_p50.append(p50)
        if len(bystander_p50) >= 2:
            ratio = max(bystander_p50) / min(bystander_p50)
            ratios.append(ratio)
            evidence.append({"hot_tenant": hot, "bystander_p50_s": bystander_p50})
    if not ratios:
        return _result(
            "Hot-tenant bystander fairness",
            INCONCLUSIVE,
            target=1.50,
            evidence="metrics.per_tenant.*.commit.completion.p50_s",
            reason="没有足够的热租户和旁观租户完成样本",
        )
    observed = max(ratios)
    return _result(
        "Hot-tenant bystander fairness",
        PASS if observed <= 1.50 else FAIL,
        target=1.50,
        observed=observed,
        evidence=evidence,
        reason="比较旁观租户 Commit P50 的最大/最小比值",
    )


def evaluate_pr421_acceptance(manifest: dict[str, Any]) -> dict[str, Any]:
    """Evaluate measurable PR421 gates and list unavailable gates."""
    checks = [
        _target_coverage(manifest),
        _search_isolation_gate(manifest),
        _search_success_gate(manifest),
        _report6_quality_gate(manifest),
        _fairness_gate(manifest),
        _commit_completion_gate(manifest),
        _rejection_gate(manifest),
        _hot_tenant_gate(manifest),
    ]
    fault_suite = manifest.get("fault_suite") or {}
    fault_summary = fault_suite.get("summary") or {}
    fault_cases = fault_suite.get("cases") or []

    def artifact_gate(name: str, case_kind: str, default_reason: str) -> dict[str, Any]:
        matching = [
            case for case in fault_cases
            if case.get("kind") == case_kind
        ]
        result = (matching[0].get("execution") or {}).get("result") if matching else None
        status = str((result or {}).get("status") or INCONCLUSIVE)
        return _result(
            name,
            status if status in {PASS, FAIL, INCONCLUSIVE, NOT_IMPLEMENTED} else INCONCLUSIVE,
            evidence=fault_suite.get("path", "fault-suite.json"),
            reason=(result or {}).get("reason") or default_reason,
            observed=(result or {}).get("summary") or (result or {}).get("recovered"),
        )

    capability_checks = {
        str(item.get("name")): item
        for item in (manifest.get("capability_probe") or {}).get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }

    def capability_gate(name: str, fallback: str, reason: str) -> dict[str, Any]:
        observed = capability_checks.get(name)
        if not observed:
            return _result(name, fallback, evidence="capability-probe.json", reason=reason)
        return _result(
            name,
            str(observed.get("status") or fallback),
            evidence="capability-probe.json",
            reason=str(observed.get("reason") or reason),
            observed={
                "http_status": observed.get("http_status"),
                "error": observed.get("error"),
            },
        )

    unavailable = [
        capability_gate(
            "cursor/message-set",
            INCONCLUSIVE,
            "未配置 cursor 对账计划或 EchoMem 未提供真实消息集合接口",
        ),
        artifact_gate(
            "Kill-9 local/cluster recovery",
            "kill-9-recovery",
            "未配置真实 PID/container 和重启命令",
        ),
        capability_gate(
            "fault control",
            INCONCLUSIVE,
            "未配置真实依赖故障控制接口或命令",
        ),
    ]
    all_checks = checks + unavailable
    statuses = {item["status"] for item in all_checks}
    overall = (
        FAIL if FAIL in statuses
        else INCONCLUSIVE if statuses & {INCONCLUSIVE, NOT_IMPLEMENTED}
        else PASS
    )
    return {
        "version": "pr421-acceptance-v1",
        "overall": overall,
        "checks": all_checks,
        "pr28_review_resolution": PR28_REVIEW_RESOLUTION,
        "review": {
            "reasonable_targets": [
                "Search P95 隔离度应排除超时样本并单列错误率",
                "Jain 公平指数要求至少两个独立认证租户",
                "拒绝率必须和拒绝响应耗时、Retry-After、reason_code 一起验收",
                "Commit 恢复率必须基于最终状态，不把轮询窗口内未知状态算成功",
            ],
            "missing_or_weak_targets": [
                "PR421 的 deadline_exhausted=0 过于绝对，应同时看跨租户影响和错误率",
                "128 并发饱和需增加队列打满前置与降载后恢复检查",
                "Commit 100% 完成率还必须增加 cursor/消息集合对账",
                "热租户指标应固定旁观租户样本和持续未服务时间口径",
                "四档 profile 需要记录实际生效资源，而不是只记录配置值",
            ],
        },
    }


def build_model_analysis_input(
    manifest: dict[str, Any],
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    """Create a bounded, secret-free context for an external LLM diagnosis."""
    return {
        "task": "Analyze EchoMem PR421 stress acceptance results",
        "rules": [
            "Use only supplied evidence; do not invent server behavior.",
            "Distinguish FAIL, INCONCLUSIVE, and NOT_IMPLEMENTED.",
            "Do not claim client-side queueing proves EchoMem server scheduling.",
            "Prioritize data-loss, cross-tenant leakage, recovery, and saturation failures.",
        ],
        "run_context": {
            "base_url": manifest.get("base_url"),
            "scenarios": manifest.get("scenarios") or [],
            "repeats": manifest.get("repeats"),
            "client_admission_enabled": manifest.get("client_admission_enabled"),
            "server_observation_mode": manifest.get("server_observation_mode"),
        },
        "acceptance": acceptance,
        "requested_output": [
            "one-paragraph executive conclusion",
            "failed or inconclusive gates with evidence",
            "most likely root cause and confidence",
            "next diagnostic action",
            "whether EchoMem code, deployment, or test-platform code needs change",
        ],
    }
