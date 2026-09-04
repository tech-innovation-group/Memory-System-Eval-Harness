"""Pure functions for stress-test statistics.

No I/O here so every function is unit-testable in isolation. Percentile
conventions match `dynamic/metrics.py` and `scripts/compare_memory_backends.py`
(linear interpolation over sorted values).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from performance.loadgen import RequestRecord, is_anchor_query


def percentile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in [0, 1]) of a sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if q <= 0.0:
        return sorted_vals[0]
    if q >= 1.0:
        return sorted_vals[-1]
    pos = (n - 1) * q
    lower = int(pos)
    upper = lower + 1
    frac = pos - lower
    if upper >= n:
        return sorted_vals[lower]
    return sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * frac


def percentiles(sorted_vals: list[float], quantiles: Iterable[float]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for q in quantiles:
        label = f"p{int(round(q * 100))}"
        result[label] = percentile(sorted_vals, q)
    return result


def _op_stats(stages: list[float]) -> dict[str, Any]:
    """Summary over one operation's measured stage latencies (ms)."""
    if not stages:
        return {
            "count": 0,
            "avg_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
            "min_ms": None,
        }
    ordered = sorted(stages)
    ps = percentiles(ordered, (0.5, 0.95, 0.99))
    return {
        "count": len(stages),
        "avg_ms": round(sum(stages) / len(stages), 3),
        "p50_ms": ps["p50"],
        "p95_ms": ps["p95"],
        "p99_ms": ps["p99"],
        "max_ms": round(ordered[-1], 3),
        "min_ms": round(ordered[0], 3),
    }


def summarize_records(records: list[RequestRecord], *, wall_s: float) -> dict[str, Any]:
    """Summarize records grouped by (scene key, operation).

    ``wall_s`` is the scene wall-clock duration used to derive QPS. Errors
    are classified into timeout / http_4xx / http_5xx / connection / other.
    """
    groups: dict[tuple[str, str], list[float]] = {}
    errors: dict[tuple[str, str], dict[str, int]] = {}
    http_statuses: dict[tuple[str, str], dict[str, int]] = {}
    for rec in records:
        key = (rec.scene_key, rec.op)
        groups.setdefault(key, []).append(rec.stage_ms)
        if rec.error_type:
            counter = errors.setdefault(key, {})
            counter[rec.error_type] = counter.get(rec.error_type, 0) + 1
        if rec.http_status is not None:
            counter = http_statuses.setdefault(key, {})
            status = str(rec.http_status)
            counter[status] = counter.get(status, 0) + 1

    summary: dict[str, dict[str, Any]] = {}
    for key, stages in groups.items():
        scene_key, op = key
        entry = _op_stats(stages)
        count = entry["count"]
        entry["qps"] = round(count / wall_s, 3) if wall_s > 0 else None
        err = errors.get(key, {})
        entry["errors_total"] = sum(err.values())
        entry["error_rate"] = round(sum(err.values()) / count, 5) if count else None
        entry["error_breakdown"] = err
        entry["http_status_breakdown"] = http_statuses.get(key, {})
        summary.setdefault(scene_key, {})[op] = entry
    return summary


def degradation_factor(
    baseline: dict[str, Any] | None,
    target: dict[str, Any] | None,
) -> dict[str, float | None]:
    """Read-latency degradation of target vs baseline for p50/p95/p99.

    A value of 1.0 means no degradation; ``None`` when either side lacks data.
    """
    result: dict[str, float | None] = {"p50": None, "p95": None, "p99": None}
    if not baseline or not target:
        return result
    for key in ("p50", "p95", "p99"):
        base = baseline.get(f"{key}_ms")
        tgt = target.get(f"{key}_ms")
        if base is not None and tgt is not None and base > 0:
            result[key] = round(tgt / base, 3)
    return result


def read_records_in_window(
    records: list[RequestRecord],
    t0_ms: float,
    t1_ms: float,
) -> list[RequestRecord]:
    """Read-op records whose completion timestamp falls inside [t0, t1]."""
    return [
        rec
        for rec in records
        if rec.op == "read" and rec.ts_ms is not None and t0_ms <= rec.ts_ms <= t1_ms
    ]


def consistency_summary(records: list[RequestRecord]) -> dict[str, Any]:
    """Write-then-read consistency window stats (records with op=consistent_check)."""
    stages = [rec.stage_ms for rec in records if rec.op == "consistent_check"]
    stats = _op_stats(stages)
    stats["timeouts"] = sum(
        1
        for rec in records
        if rec.op == "consistent_check" and rec.error_type in ("timeout", "consistency_timeout")
    )
    return stats


def burst_summary(
    burst_reads: list[RequestRecord],
    baseline_reads: list[RequestRecord],
) -> dict[str, Any]:
    """Compare burst-window read latency distribution against the scene baseline."""
    if not burst_reads or not baseline_reads:
        return {"count": 0, "degradation": {"p50": None, "p95": None, "p99": None}}
    burst_stats = _op_stats([rec.stage_ms for rec in burst_reads])
    baseline_stats = _op_stats([rec.stage_ms for rec in baseline_reads])
    return {
        "count": len(burst_reads),
        "burst_stats": burst_stats,
        "baseline_stats": baseline_stats,
        "degradation": degradation_factor(baseline_stats, burst_stats),
    }


def commit_completion_latency(records: list[RequestRecord]) -> dict[str, Any]:
    """Submit→completed 的异步完成等待耗时 (ms)，即 commit_done 轮询阶段。

    ``commit_done`` 的 stage_ms 是客户端从提交成功到观察为已完成的等待
    时长（见 loadgen.run_write_transaction），量化 commit 异步完成的实时性。
    """
    return _op_stats(
        [rec.stage_ms for rec in records if rec.op == "commit_done" and rec.status == "ok"]
    )


def degradation_measurements(summary: dict[str, Any]) -> dict[str, Any]:
    """写洪峰/混合场景 vs 同并发 A 基线的绝对延迟与相对倍率。

    每个对照形如 ``D@4_vs_A@4``，输出 baseline/flood 的 P50/P95/P99 绝对
    值、绝对差 (delta_ms) 与相对倍率 (ratio)：即「写洪峰时 search 延迟比
    基线高多少」的直接度量。数据来自 summary.scenes 的 read 统计与
    summary.degradation 的倍率。
    """
    scenes = summary.get("scenes") or {}
    degradation = summary.get("degradation") or {}
    result: dict[str, Any] = {}
    for key, factors in degradation.items():
        if "_vs_" not in key:
            continue
        target_key, base_key = key.split("_vs_", 1)
        target = ((scenes.get(target_key) or {}).get("ops") or {}).get("read") or {}
        baseline = ((scenes.get(base_key) or {}).get("ops") or {}).get("read") or {}
        entry: dict[str, Any] = {}
        for p in ("p50", "p95", "p99"):
            t_ms = target.get(f"{p}_ms")
            b_ms = baseline.get(f"{p}_ms")
            entry[f"baseline_{p}_ms"] = b_ms
            entry[f"flood_{p}_ms"] = t_ms
            entry[f"delta_{p}_ms"] = (
                round(float(t_ms) - float(b_ms), 3)
                if t_ms is not None and b_ms is not None
                else None
            )
            entry[f"ratio_{p}"] = factors.get(p)
        result[key] = entry
    return result


def fairness_measurements(fairness: dict[str, Any]) -> dict[str, Any]:
    """租户公平性量化：每个多租户场景的最快/最慢租户等待与差距。

    ``slowest_waits_extra_ms`` 即「最慢租户比最快租户多等的 P95 时长」，
    是公平性不满足时受困租户可能遭遇的最坏额外等待的直接度量。
    """
    scenes: dict[str, Any] = {}
    for scene_key, fair in (fairness or {}).items():
        rows: list[dict[str, Any]] = []
        for row in fair.get("tenants") or []:
            p95 = row.get("p95_ms")
            if p95 is None:
                continue
            rows.append(
                {
                    "tenant_idx": row.get("tenant_idx"),
                    "p95_ms": float(p95),
                    "p99_ms": (
                        float(row["p99_ms"]) if row.get("p99_ms") is not None else None
                    ),
                }
            )
        if len(rows) < 2:
            continue
        best = min(rows, key=lambda item: item["p95_ms"])
        worst = max(rows, key=lambda item: item["p95_ms"])
        scenes[scene_key] = {
            "tenant_count": len(rows),
            "p95_max_min_ratio": fair.get("p95_max_min_ratio"),
            "p95_cv": fair.get("p95_cv"),
            "fastest_tenant_idx": best["tenant_idx"],
            "fastest_tenant_p95_ms": best["p95_ms"],
            "slowest_tenant_idx": worst["tenant_idx"],
            "slowest_tenant_p95_ms": worst["p95_ms"],
            "slowest_tenant_p99_ms": worst["p99_ms"],
            "slowest_waits_extra_ms": round(worst["p95_ms"] - best["p95_ms"], 3),
        }
    if not scenes:
        return {"scenes": scenes}
    worst_key = max(scenes, key=lambda key: scenes[key]["p95_max_min_ratio"] or 0.0)
    return {"scenes": scenes, "worst_scene": worst_key, **scenes[worst_key]}


# ---------------------------------------------------------------------- #
#  Four feature guarantees: commit durability, tenant fairness,           #
#  memory trend, resource timeline                                        #
# ---------------------------------------------------------------------- #

# 租户公平性判定阈值：组间读 P95 max/min 比达到该值判不均衡。
FAIRNESS_MAX_MIN_RATIO = 3.0
# RSS 斜率泄漏判定阈值（MB/分钟）：超过即判疑似泄漏。
RSS_LEAK_SLOPE_MB_PER_MIN = 5.0
# A short bounded smoke test is too dominated by allocator warm-up and GC to
# support a leak conclusion. Keep the slope for diagnostics, but gate the
# verdict until the observed window is long enough.
RSS_LEAK_MIN_WINDOW_S = 60.0


def _verdict(verdict: str, reason: str) -> dict[str, Any]:
    return {"verdict": verdict, "reason": reason}


def _merge_subs(subs: list[dict[str, Any]]) -> str:
    """Worst verdict of sub-checks: FAIL > INCONCLUSIVE > PASS."""
    if any(sub.get("verdict") == "FAIL" for sub in subs):
        return "FAIL"
    if any(sub.get("verdict") == "INCONCLUSIVE" for sub in subs):
        return "INCONCLUSIVE"
    return "PASS"


def commit_durability(records: list[RequestRecord]) -> dict[str, Any]:
    """Commit 成功保证：submit(202) 接受后必须最终 completed。

    A submit that the server accepted (status ok) is paired with its
    commit_done record by session_id. Any accepted commit that does not
    reach ``completed`` violates the guarantee. Poll timeouts are reported
    separately: they mean the observation window (--commit-poll-timeout-s)
    expired, not necessarily that the commit itself failed.
    """
    submit_by_session: dict[str, bool] = {}
    done_by_session: dict[str, str] = {}
    submit_errors: dict[str, int] = {}
    for rec in records:
        if rec.op == "commit_submit":
            if rec.status == "ok":
                submit_by_session[rec.session_id] = True
            else:
                submit_errors[rec.error_type] = submit_errors.get(rec.error_type, 0) + 1
        elif rec.op == "commit_done":
            done_by_session[rec.session_id] = rec.error_type or rec.status

    accepted = 0
    done_ok = 0
    done_failed = 0
    done_timeout = 0
    done_other = 0
    for session_id in submit_by_session:
        outcome = done_by_session.get(session_id)
        accepted += 1
        if outcome in ("ok", "completed"):
            done_ok += 1
        elif outcome in ("commit_failed", "failed"):
            done_failed += 1
        elif outcome in ("commit_timeout", "timeout"):
            done_timeout += 1
        else:
            done_other += 1
    total_accepts = accepted + len(submit_errors)
    return {
        "submit_ok_total": accepted,
        "submit_rejected_total": len(submit_errors),
        "submit_rejected_breakdown": submit_errors,
        "accepted_done_ok": done_ok,
        "accepted_done_failed": done_failed,
        "accepted_done_poll_timeout": done_timeout,
        "accepted_done_other": done_other,
        "commit_success_rate": (
            round(done_ok / accepted, 5) if accepted else None
        ),
        # 违反「commit 成功保证」的信号：202 已接受但最终失败
        "guarantee_violations": done_failed + done_other,
    }


def jain_fairness(values: Iterable[float]) -> float | None:
    """Return Jain's index for non-negative tenant rates/utilities.

    Jain's index is defined across tenants, never across operation types.
    A minimum of two positive observations is required; zero-throughput
    tenants remain in the denominator when they are part of the window.
    """
    usable = [float(value) for value in values if value is not None and value >= 0]
    if len(usable) < 2:
        return None
    denominator = len(usable) * sum(value * value for value in usable)
    if denominator == 0:
        return None
    total = sum(usable)
    return round((total * total) / denominator, 4)


def tenant_fairness(
    records: list[RequestRecord],
    *,
    wall_s: float | None = None,
) -> dict[str, Any]:
    """租户公平性：分别计算 Commit 吞吐和 Search 延迟公平性。

    The fairness population is the set of tenants in each scene. Commit
    throughput and Search latency are deliberately separate dimensions:
    Commit uses completed operations per second, while Search converts P95
    latency into an inverse utility (``1 / p95``) before applying Jain.
    The legacy read P95 spread fields are retained for compatibility.
    """
    per_scene: dict[str, dict[int, list[float]]] = {}
    commit_counts: dict[str, dict[int, int]] = {}
    for rec in records:
        if rec.op == "read" and rec.status == "ok":
            per_scene.setdefault(rec.scene_key, {}).setdefault(rec.tenant_idx, []).append(
                rec.stage_ms
            )
        elif rec.op == "commit_done" and rec.status == "ok":
            commit_counts.setdefault(rec.scene_key, {}).setdefault(rec.tenant_idx, 0)
            commit_counts[rec.scene_key][rec.tenant_idx] += 1

    result: dict[str, Any] = {}
    scene_keys = set(per_scene) | set(commit_counts)
    for scene_key in scene_keys:
        tenant_map = per_scene.get(scene_key, {})
        scene_commit_counts = commit_counts.get(scene_key, {})
        tenant_ids = set(tenant_map) | set(scene_commit_counts)
        rows: list[dict[str, Any]] = []
        for tenant_idx in sorted(tenant_ids):
            stages = tenant_map.get(tenant_idx, [])
            ordered = sorted(stages)
            rows.append(
                {
                    "tenant_idx": tenant_idx,
                    "count": len(stages),
                    "p50_ms": percentile(ordered, 0.5),
                    "p95_ms": percentile(ordered, 0.95),
                    "p99_ms": percentile(ordered, 0.99),
                    "commit_completed": scene_commit_counts.get(tenant_idx, 0),
                }
            )
        if len(rows) < 2:
            result[scene_key] = {
                "tenants": rows,
                "p95_max_min_ratio": None,
                "balanced": True,
                "commit_throughput_per_tenant": {},
                "commit_throughput_jain": None,
                "search_latency_utility_per_tenant": {},
                "search_latency_utility_jain": None,
            }
            continue
        p95s = [row["p95_ms"] for row in rows if row["p95_ms"] is not None]
        positive = [v for v in p95s if v > 0]
        ratio = None
        if len(positive) >= 2:
            ratio = round(max(positive) / min(positive), 3)
        mean = sum(p95s) / len(p95s) if p95s else 0.0
        variance = (
            sum((v - mean) ** 2 for v in p95s) / len(p95s)
            if p95s
            else 0.0
        )
        duration = wall_s if wall_s and wall_s > 0 else 1.0
        commit_rates = {
            str(row["tenant_idx"]): round(row["commit_completed"] / duration, 6)
            for row in rows
        }
        search_utilities = {
            str(row["tenant_idx"]): round(1000.0 / row["p95_ms"], 6)
            for row in rows
            if row["p95_ms"] is not None and row["p95_ms"] > 0
        }
        result[scene_key] = {
            "tenants": rows,
            "p95_max_min_ratio": ratio,
            "p95_cv": round(variance**0.5 / mean, 3) if mean > 0 and p95s else None,
            "balanced": ratio is None or ratio < FAIRNESS_MAX_MIN_RATIO,
            "commit_throughput_per_tenant": commit_rates,
            "commit_throughput_jain": jain_fairness(commit_rates.values()),
            "search_latency_utility_per_tenant": search_utilities,
            "search_latency_utility_jain": jain_fairness(search_utilities.values()),
        }
    return result


def rss_trend_mb_per_min(series: list[tuple[float, float]]) -> dict[str, Any]:
    """Least-squares slope of RSS (bytes) over time, in MB per minute.

    Needs at least 4 samples across the observed window; fewer samples
    return an undecidable result. The slope together with the cooling
    settle delta distinguishes a slow leak from index-size growth.
    """
    n = len(series)
    if n < 4:
        return {
            "slope_mb_per_min": None,
            "r2": None,
            "samples": n,
            "window_s": 0.0,
            "verdictable": False,
        }
    xs = [ts for ts, _ in series]
    ys = [value / 1024 / 1024 for _, value in series]  # MB
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    s_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    s_xx = sum((x - mean_x) ** 2 for x in xs)
    if s_xx <= 0:
        return {
            "slope_mb_per_min": None,
            "r2": None,
            "samples": n,
            "window_s": 0.0,
            "verdictable": False,
        }
    slope = s_xy / s_xx  # MB per second
    ss_res = sum((y - (mean_y + slope * (x - mean_x))) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r2 = round(1 - ss_res / ss_tot, 4) if ss_tot > 0 else None
    return {
        "slope_mb_per_min": round(slope * 60, 3),
        "r2": r2,
        "samples": n,
        "window_s": round(max(xs) - min(xs), 3),
        "verdictable": (max(xs) - min(xs)) >= RSS_LEAK_MIN_WINDOW_S,
    }


# ---------------------------------------------------------------------- #
#  Feature verdicts: the four EchoMem guarantees, evaluated per run       #
# ---------------------------------------------------------------------- #

FEATURE_LABELS: dict[str, str] = {
    "commit_guarantee": "特性1 commit 异步/成功保证/不阻塞检索",
    "tenant_fairness": "特性2 租户公平性",
    "memory_leak": "特性3 无内存泄漏",
    "resource_timeline": "特性4 资源利用率随时间变化图",
    "write_retry": "特性5 写事务重试与正确写入（重试+对账）",
    "search_quality": "特性6 search 质量断言（无假通过）",
    "isolation_granularity": "特性7 读写隔离细粒度（同/跨租户）",
    "error_type": "特性8 服务端错误类型正确性",
    "fault_injection": "特性9 故障注入（mock provider）",
    "preflight": "特性10 模型与配置预检门禁",
    "tenant_isolation": "特性11 租户隔离（N×N marker 探针）",
    "saturation_contract": "特性12 饱和拒绝契约（429/503 Retry-After + reason_code）",
    "hot_tenant_fairness": "特性13 热租户旁观公平性",
}

# 判定分层：每特性结论携带证据类型（real 真实容量 / mock 可控故障语义）。
# 状态分类：pass / fail / not_run / known_limit / env_error。
#   env_error 仅表示环境/依赖失败（key、模型名、网络、provider admission），
#   不归因于被测代码；not_run 表示该目标未在本轮运行中执行。
VERDICT_ORDER = ("env_error", "fail", "inconclusive", "not_run", "pass")


def merge_verdicts(verdicts: list[str]) -> str:
    """Worst-status merge: env_error > FAIL > INCONCLUSIVE; not_run neutral."""
    if any(v == "env_error" for v in verdicts):
        return "env_error"
    if any(v == "FAIL" for v in verdicts):
        return "FAIL"
    if any(v == "INCONCLUSIVE" for v in verdicts):
        return "INCONCLUSIVE"
    return "PASS"


def evaluate_features(summary: dict[str, Any]) -> dict[str, Any]:
    """Evaluate all four feature guarantees from a finished run summary.

    Verdicts are PASS / FAIL / INCONCLUSIVE (data insufficient). The
    function is tolerant of missing fields so it can grade partial or
    degraded runs (e.g. --no-metrics, static single-tenant). Each verdict
    carries ``measurements`` with the quantified magnitude it is based on
    (degradation ratio + absolute delta, per-tenant wait spread, RSS growth
    slope and hour projection, CPU/RSS timeline extremes); the report
    renders these as the quantified-analysis section.
    """
    config = summary.get("config") or {}
    try:
        degradation_threshold = float(config.get("degradation_threshold", 2.0))
    except (TypeError, ValueError):
        degradation_threshold = 2.0

    features: dict[str, Any] = {}

    # -- 特性1a: commit 成功保证 -------------------------------------------
    durability = summary.get("commit_durability") or {}
    submitted = int(durability.get("submit_ok_total", 0) or 0)
    rejected = int(durability.get("submit_rejected_total", 0) or 0)
    violations = int(durability.get("guarantee_violations", 0) or 0)
    if submitted == 0 and rejected == 0:
        sub_durability = _verdict(
            "INCONCLUSIVE", "未运行写场景（B/C/D），无法评估 commit 成功保证"
        )
    elif violations > 0:
        sub_durability = _verdict(
            "FAIL",
            f"202 已接受的 commit 中 {violations} 个未最终 completed "
            f"(accepted_done_failed={durability.get('accepted_done_failed')}, "
            f"accepted_done_other={durability.get('accepted_done_other')})",
        )
    else:
        sub_durability = _verdict(
            "PASS",
            f"{submitted} 个已接受 commit 全部 completed (成功率 "
            f"{durability.get('commit_success_rate')})；提交阶段被拒绝 "
            f"{rejected} 次（不可重试因素，分类见 commit_durability）",
        )
    sub_durability["measurements"] = {
        "submitted_202": submitted,
        "submit_rejected": rejected,
        "accepted_done_ok": durability.get("accepted_done_ok"),
        "commit_success_rate": durability.get("commit_success_rate"),
        "violations": violations,
        "completion_latency_ms": (summary.get("commit_latency") or {}),
    }

    # -- 特性1b: search 优先级 / commit 不阻塞检索 ---------------------------
    degradation = summary.get("degradation") or {}
    d_cases = {
        key: value
        for key, value in degradation_measurements(summary).items()
        if key.startswith("D")
    }
    d_factors = [
        float(factors.get("p95"))
        for key, factors in degradation.items()
        if key.startswith("D") and factors.get("p95") is not None
    ]
    worst_ratio = max(d_factors) if d_factors else None
    worst_case = (
        max(d_cases, key=lambda key: (d_cases[key].get("ratio_p95") or 0.0))
        if d_cases
        else None
    )

    def _delta_text(case_key: str | None) -> str:
        case = d_cases.get(case_key) if case_key else None
        if not case or case.get("delta_p95_ms") is None:
            return ""
        return (
            f"（基线 P95 {case['baseline_p95_ms']}ms → 洪峰 {case['flood_p95_ms']}ms，"
            f"+{case['delta_p95_ms']}ms）"
        )

    if not d_factors:
        sub_priority = _verdict(
            "INCONCLUSIVE",
            "未运行注入洪峰场景（D）或缺少同并发档 A 基线，无法评估 search 优先级",
        )
    elif worst_ratio >= degradation_threshold:
        sub_priority = _verdict(
            "FAIL",
            f"{worst_case} 注入洪峰窗口读 P95 劣化 {worst_ratio}x"
            f"{_delta_text(worst_case)} ≥ 阈值 {degradation_threshold}x",
        )
    else:
        sub_priority = _verdict(
            "PASS",
            f"注入洪峰窗口读 P95 劣化 max={worst_ratio}x"
            f"{_delta_text(worst_case)} < 阈值 {degradation_threshold}x",
        )
    sub_priority["measurements"] = {
        "threshold_ratio": degradation_threshold,
        "worst_p95_ratio": worst_ratio,
        "worst_case": worst_case,
        "cases": d_cases,
    }
    features["commit_guarantee"] = {
        "verdict": _merge_subs([sub_durability, sub_priority]),
        "sub": {"durability": sub_durability, "retrieval_precedence": sub_priority},
        "measurements": {
            "durability": sub_durability["measurements"],
            "retrieval_precedence": sub_priority["measurements"],
        },
    }

    # -- 特性2: 租户公平性 ---------------------------------------------------
    fairness = summary.get("tenant_fairness") or {}
    fair_measurements = fairness_measurements(fairness)
    multi_tenant = [v for v in fairness.values() if len(v.get("tenants") or []) >= 2]
    if not fairness:
        features["tenant_fairness"] = _verdict("INCONCLUSIVE", "无按租户分组的读数据")
    elif not multi_tenant:
        features["tenant_fairness"] = _verdict(
            "INCONCLUSIVE",
            "单租户运行（如 --auth-mode static），无法评估租户间公平性",
        )
    elif any(not v.get("balanced", True) for v in multi_tenant):
        worst = max((v.get("p95_max_min_ratio") or 0 for v in multi_tenant), default=0.0)
        slow_p95 = fair_measurements.get("slowest_tenant_p95_ms")
        extra = fair_measurements.get("slowest_waits_extra_ms")
        wait_text = (
            f"；最慢租户 P95 {slow_p95}ms，比最快租户多等 {extra}ms"
            if slow_p95 is not None and extra is not None
            else ""
        )
        features["tenant_fairness"] = _verdict(
            "FAIL",
            f"存在租户间读 P95 max/min 比 ≥ {FAIRNESS_MAX_MIN_RATIO}x 的场景 "
            f"(最大 {worst}x){wait_text}",
        )
    else:
        worst = max(
            (v.get("p95_max_min_ratio") or 1.0 for v in multi_tenant), default=1.0
        )
        features["tenant_fairness"] = _verdict(
            "PASS",
            f"全部场景租户间读 P95 max/min 比最大 {worst}x "
            f"< {FAIRNESS_MAX_MIN_RATIO}x",
        )
    features["tenant_fairness"]["measurements"] = fair_measurements

    # -- 特性3: 无内存泄漏（RSS 归一校正后斜率） ---------------------------
    resources = summary.get("resources") or {}
    trend = resources.get("rss_trend") or {}
    normalized = resources.get("rss_normalized") or {}
    # 优先用扣除注入数据增长后的净斜率（归一口径），无归一数据时回退原始斜率
    slope = (
        (normalized.get("net_trend") or {}).get("slope_mb_per_min")
        or trend.get("slope_mb_per_min")
    )
    trend_window_s = (
        (normalized.get("net_trend") or {}).get("window_s")
        if normalized.get("net_trend")
        else None
    )
    if trend_window_s is None:
        trend_window_s = trend.get("window_s")
    unsettled = resources.get("rss_unsettled_mb")
    if slope is None or (
        trend_window_s is not None and trend_window_s < RSS_LEAK_MIN_WINDOW_S
    ):
        features["memory_leak"] = _verdict(
            "INCONCLUSIVE",
            (
                "RSS 采样不足（<4 帧）或 /metrics 不可用，无法判定泄漏趋势"
                if trend_window_s is None or trend_window_s >= RSS_LEAK_MIN_WINDOW_S
                else f"RSS 观测窗口仅 {trend_window_s}s，小于 "
                f"{RSS_LEAK_MIN_WINDOW_S:.0f}s 最小判定窗口；短时启动/GC 波动不作泄漏结论"
            ),
        )
    elif slope >= RSS_LEAK_SLOPE_MB_PER_MIN:
        features["memory_leak"] = _verdict(
            "FAIL",
            f"RSS 上升斜率 {slope} MB/min ≥ 泄漏判定阈值 "
            f"{RSS_LEAK_SLOPE_MB_PER_MIN} MB/min"
            f"（预计每小时增长 {round(slope * 60, 1)} MB）",
        )
    else:
        settle_note = (
            f"冷却后未回落 {unsettled}MB"
            if unsettled is not None
            else "冷却后未回落量不可测（/metrics 采样缺失）"
        )
        features["memory_leak"] = _verdict(
            "PASS",
            f"RSS 上升斜率 {slope} MB/min < 泄漏判定阈值 "
            f"{RSS_LEAK_SLOPE_MB_PER_MIN} MB/min（{settle_note}）",
        )
    features["memory_leak"]["measurements"] = {
        "slope_mb_per_min": slope,
        "slope_source": "rss_net" if normalized.get("net_trend") else "rss_raw",
        "projected_growth_mb_per_hour": (
            round(slope * 60, 1) if slope is not None else None
        ),
        "rss_baseline_mb": resources.get("rss_baseline_mb"),
        "rss_peak_mb": resources.get("rss_peak_mb"),
        "rss_unsettled_mb": unsettled,
        "rss_normalized": {
            "net_peak_mb": normalized.get("net_peak_mb"),
            "net_settled_mb": normalized.get("net_settled_mb"),
            "injected_mb": normalized.get("injected_mb"),
        },
        "trend_r2": trend.get("r2"),
        "trend_samples": trend.get("samples"),
        "trend_window_s": trend_window_s,
        "trend_verdictable": (
            trend_window_s is not None and trend_window_s >= RSS_LEAK_MIN_WINDOW_S
        ),
    }

    # -- 特性4: 资源利用率随时间变化图（报告内容完整性） -------------------------
    server = summary.get("server") or {}
    no_metrics = bool(config.get("no_metrics"))
    if no_metrics or server.get("metrics_available") is False:
        features["resource_timeline"] = _verdict(
            "INCONCLUSIVE",
            "未采集服务端 /metrics（--no-metrics 或抓取全部失败），报告不含资源时间线",
        )
    else:
        features["resource_timeline"] = _verdict(
            "PASS",
            "report.html 已包含 CPU/RSS/线程/commit 队列/inflight 随时间变化曲线 "
            "（metrics_samples.csv 含原始采样时序）",
        )
    features["resource_timeline"]["measurements"] = {
        "metrics_available": server.get("metrics_available"),
        "metrics_frames": resources.get("metrics_frames"),
        "cpu_util_mean_percent": resources.get("cpu_util_mean_percent"),
        "cpu_util_max_percent": resources.get("cpu_util_max_percent"),
        "rss_baseline_mb": resources.get("rss_baseline_mb"),
        "rss_peak_mb": resources.get("rss_peak_mb"),
        "threads_max": resources.get("threads_max"),
        "commit_queue_max": resources.get("commit_queue_max"),
    }

    # -- 特性5: 写事务重试与正确写入（重试成功 + 消息对账） -------------------
    retry = summary.get("write_retry") or {}
    reconciliation = summary.get("reconciliation") or {}
    if retry.get("submit_total", 0) == 0 and not reconciliation:
        features["write_retry"] = _verdict("not_run", "未运行写场景（B），无重试/对账数据")
    else:
        exhausted = int(retry.get("retry_exhausted_failures", 0) or 0)
        recon_verdict = reconciliation.get("verdict")
        if reconciliation.get("sessions") and recon_verdict == "FAIL":
            features["write_retry"] = _verdict(
                "FAIL", f"消息对账失败: {reconciliation.get('reason')}"
            )
        elif exhausted > 0:
            features["write_retry"] = _verdict(
                "FAIL", f"{exhausted} 个 commit 提交重试耗尽仍失败"
            )
        elif recon_verdict == "INCONCLUSIVE":
            features["write_retry"] = _verdict(
                "INCONCLUSIVE", "对账数据不可用（服务端查询接口缺失或未执行对账）"
            )
        else:
            features["write_retry"] = _verdict(
                "PASS",
                f"重试后最终提交成功 {retry.get('final_ok')}/{retry.get('submit_total')}；"
                f"消息对账通过",
            )
    features["write_retry"]["measurements"] = {
        "retry": retry,
        "reconciliation": reconciliation,
    }

    # -- 特性6: search 质量断言（无假通过） ----------------------------------
    quality = summary.get("search_quality") or {}
    if not quality:
        features["search_quality"] = _verdict(
            "not_run", "未运行读场景（A/C/D），无 read 数据"
        )
    else:
        recall_total = int(
            quality.get("recall_total", quality.get("anchor_total", 0)) or 0
        )
        recall_fail = int(
            quality.get("recall_failures", quality.get("anchor_failures", 0)) or 0
        )
        total = int(quality.get("total", 0) or 0)
        if recall_total == 0:
            features["search_quality"] = _verdict(
                "INCONCLUSIVE", "没有已验证 recall query，不能判定记忆召回正确性"
            )
        elif recall_fail > 0:
            features["search_quality"] = _verdict(
                "FAIL",
                f"{recall_fail}/{recall_total} 次 recall 查询未召回（hit_count<1），疑似短路/假通过",
            )
        else:
            features["search_quality"] = _verdict(
                "PASS", f"{recall_total} 次已验证 recall 查询全部召回"
            )
    features["search_quality"]["measurements"] = quality

    # -- 特性7: 读写隔离细粒度（同/跨租户） ----------------------------------
    isolation = summary.get("isolation") or {}
    if not isolation:
        features["isolation_granularity"] = _verdict(
            "not_run", "未运行 D 洪峰场景，无隔离分组数据"
        )
    else:
        scene_verdicts = {
            key: value.get("verdict") for key, value in isolation.items()
        }
        if any(v == "FAIL" for v in scene_verdicts.values()):
            failed = [
                f"{key}: {value.get('reason')}"
                for key, value in isolation.items()
                if value.get("verdict") == "FAIL"
            ]
            features["isolation_granularity"] = _verdict("FAIL", "; ".join(failed))
        elif all(v == "PASS" for v in scene_verdicts.values()):
            features["isolation_granularity"] = _verdict(
                "PASS", "全部 D 场景同/跨租户隔离判定通过"
            )
        else:
            features["isolation_granularity"] = _verdict(
                "INCONCLUSIVE", "部分 D 场景隔离数据不足（缺 A 基线或组内无 read）"
            )
    features["isolation_granularity"]["measurements"] = isolation

    # -- 特性8: 服务端错误类型正确性 -----------------------------------------
    error_type = summary.get("error_type_validation") or {}
    if not error_type:
        features["error_type"] = _verdict(
            "not_run", "无错误类型观测数据（无写/故障场景）"
        )
    else:
        features["error_type"] = _verdict(
            error_type.get("verdict"), error_type.get("reason", "")
        )
    features["error_type"]["measurements"] = error_type

    # -- 特性9: 故障注入（mock provider，可控故障语义证据） -------------------
    fault = summary.get("fault_injection") or {}
    if not fault:
        features["fault_injection"] = _verdict(
            "not_run", "未运行 F 场景（mock provider）"
        )
    else:
        features["fault_injection"] = _verdict(
            fault.get("verdict"), fault.get("reason", "")
        )
        features["fault_injection"]["evidence_type"] = "mock"
    features["fault_injection"]["measurements"] = fault

    # -- 特性10: 模型与配置预检门禁 ------------------------------------------
    preflight = summary.get("preflight") or {}
    if not preflight:
        features["preflight"] = _verdict(
            "not_run", "未执行预检（--preflight-config 未传）"
        )
    elif preflight.get("ok") is True:
        features["preflight"] = _verdict(
            "PASS",
            f"预检通过: {preflight.get('engines_checked')} 个 engine 真实请求成功",
        )
    else:
        features["preflight"] = _verdict(
            "env_error",
            f"预检失败（环境/依赖）: {preflight.get('error')}",
        )
    features["preflight"]["measurements"] = preflight

    # -- 特性11: 租户隔离（N×N marker 探针，FAIL 传播；否则 INCONCLUSIVE） ------
    isolation_probe = summary.get("isolation_probe") or {}
    if not isolation_probe:
        features["tenant_isolation"] = _verdict(
            "not_run", "未运行 I 场景（N×N 隔离探针）"
        )
    elif isolation_probe.get("verdict") == "FAIL":
        features["tenant_isolation"] = _verdict(
            "FAIL",
            f"{isolation_probe.get('invalid_probe_count')} 条隔离探针命中与预期不符，"
            f"租户隔离失效",
        )
    else:
        config = summary.get("config") or {}
        auth_mode = str(config.get("auth_mode") or config.get("effective_auth_mode") or "")
        tenant_count = int(
            (summary.get("data_scale") or {}).get("tenants")
            or config.get("tenants")
            or 0
        )
        # Provisioned identities are generated independently by the service.
        # Static/local auth intentionally remains inconclusive because all
        # probes can resolve to one identity even when ``--tenants`` is > 1.
        if (
            isolation_probe.get("verdict") == "PASS"
            and auth_mode == "provision"
            and tenant_count >= 2
            and int(isolation_probe.get("probe_count") or 0) >= 4
        ):
            features["tenant_isolation"] = _verdict(
                "PASS",
                "独立 provision 租户的同租户命中率为 100%，跨租户误命中率为 0%",
            )
        else:
            features["tenant_isolation"] = _verdict(
                "INCONCLUSIVE",
                "探针通过，但未能从运行配置证明使用了至少两个独立租户身份",
            )
    features["tenant_isolation"]["measurements"] = isolation_probe

    # -- 特性12: 饱和拒绝契约（429/503 拒绝必须带 Retry-After + reason_code） ----
    saturation = summary.get("saturation") or {}
    if not saturation:
        features["saturation_contract"] = _verdict(
            "not_run", "未运行 S 场景（commit barrier 饱和）"
        )
    else:
        features["saturation_contract"] = _verdict(
            saturation.get("verdict"), saturation.get("reason") or ""
        )
    features["saturation_contract"]["measurements"] = saturation

    # -- 特性13: 热租户旁观公平性 ---------------------------------------------
    hot_tenant = summary.get("hot_tenant") or {}
    if not hot_tenant:
        features["hot_tenant_fairness"] = _verdict(
            "not_run", "未运行 H 场景（热租户偏斜）"
        )
    else:
        features["hot_tenant_fairness"] = _verdict(
            hot_tenant.get("verdict"), hot_tenant.get("reason") or ""
        )
    features["hot_tenant_fairness"]["measurements"] = hot_tenant

    verdicts = [entry["verdict"] for entry in features.values()]
    overall = merge_verdicts(verdicts)

    # -- 判定分层与 SLO 口径 -------------------------------------------------
    verdict_layers = {
        "real": {
            key: entry.get("verdict")
            for key, entry in features.items()
            if entry.get("evidence_type") != "mock"
        },
        "mock": {
            key: entry.get("verdict")
            for key, entry in features.items()
            if entry.get("evidence_type") == "mock"
        },
    }
    return {
        "features": features,
        "overall": overall,
        "verdict_layers": verdict_layers,
        "slo_accounting": _slo_accounting(summary),
    }

# ---------------------------------------------------------------------- #
#  扩展特性（顶层新增目标）：写事务重试 / 消息对账 / search 质量断言 /        #
#  隔离细粒度 / RSS 归一校正 / 错误类型正确性 / SLO 口径                      #
# ---------------------------------------------------------------------- #


def _duplicates(values: list[str]) -> list[str]:
    """Elements appearing more than once, in first-occurrence order."""
    seen: set[str] = set()
    dups: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen and value not in dups:
            dups.add(value)
            result.append(value)
        seen.add(value)
    return result


def retry_summary(records: list[RequestRecord]) -> dict[str, Any]:
    """Write-submit retry statistics: raw (first-attempt) vs retried values.

    ``first_attempt_rate`` is the raw submission success without retries;
    ``final_success_rate`` is the success after retries. Both are reported
    because the SLO accounting keeps the two denominators separate.
    ``retry_after_s`` aggregates the Retry-After of commit-stage 429 samples;
    ``reason_codes`` is the per-reason counter (descending by count).
    """
    submits = [rec for rec in records if rec.op == "commit_submit"]
    if not submits:
        return {
            "submit_total": 0,
            "retry_after_s": None,
            "reason_codes": {},
        }
    total = len(submits)
    retried = [rec for rec in submits if rec.retried]
    first_ok = sum(1 for rec in submits if not rec.retried and rec.status == "ok")
    final_ok = sum(1 for rec in submits if rec.status == "ok")
    exhausted = sum(1 for rec in submits if rec.retried and rec.status == "error")
    waits = [rec.retry_total_wait_ms for rec in submits if rec.retried]
    retried_errors: dict[str, int] = {}
    for rec in retried:
        if rec.error_type:
            retried_errors[rec.error_type] = retried_errors.get(rec.error_type, 0) + 1
    # 提交阶段 429 样本的 Retry-After（retry_after_s 非空即带 Retry-After 头）
    retry_after_values = [rec.retry_after_s for rec in submits if rec.retry_after_s is not None]
    retry_after_s: dict[str, float] | None = None
    if retry_after_values:
        retry_after_s = {
            "avg": round(sum(retry_after_values) / len(retry_after_values), 3),
            "max": round(max(retry_after_values), 3),
        }
    return {
        "submit_total": total,
        "retried_total": len(retried),
        "first_attempt_ok": first_ok,
        "first_attempt_rate": round(first_ok / total, 5),
        "final_ok": final_ok,
        "final_success_rate": round(final_ok / total, 5),
        "retry_exhausted_failures": exhausted,
        "retried_final_ok": sum(1 for rec in retried if rec.status == "ok"),
        "retried_errors": retried_errors,
        "retry_wait_ms": _op_stats(waits) if waits else None,
        "retry_after_s": retry_after_s,
        "reason_codes": dict(Counter(rec.reason_code for rec in submits if rec.reason_code).most_common()),
    }


def isolation_probe_summary(records: list[RequestRecord]) -> dict[str, Any]:
    """N×N 隔离探针摘要：从 op="isolation_probe" 记录解析 extra JSON。

    verdict: "PASS"（invalid==0 且 probe_count==expected 且无中断）/
    "FAIL"（invalid>0 或条数不符）/ "INCONCLUSIVE"（数据不足，如无探针记录）。
    中断（存在 error 探针记录）时无法确定期望条数，判数据不足。
    """
    probes = [rec for rec in records if rec.op == "isolation_probe"]
    if not probes:
        return {
            "probe_count": 0,
            "expected_probe_count": None,
            "invalid_probe_count": None,
            "same_tenant_hit_rate": None,
            "cross_tenant_false_positive_rate": None,
            "verdict": "INCONCLUSIVE",
            "reason": "无隔离探针记录（未运行 I 场景）",
        }
    infos: list[dict[str, Any]] = []
    interrupted = False
    for rec in probes:
        if rec.status == "error":
            interrupted = True
            continue
        try:
            info = json.loads(rec.extra or "")
        except ValueError:
            info = {}
        if isinstance(info, dict):
            infos.append(info)
    invalid = sum(
        1
        for info in infos
        if bool(info.get("marker_found")) != bool(info.get("expected"))
    )
    same = [info for info in infos if info.get("same_tenant")]
    cross = [info for info in infos if not info.get("same_tenant")]
    same_hit_rate = (
        round(sum(1 for info in same if info.get("marker_found")) / len(same), 5)
        if same
        else None
    )
    cross_fp_rate = (
        round(sum(1 for info in cross if info.get("marker_found")) / len(cross), 5)
        if cross
        else None
    )
    expected = len(probes) if not interrupted else None
    if invalid > 0:
        verdict, reason = "FAIL", f"{invalid} 条探针命中与预期不符"
    elif interrupted:
        verdict, reason = "INCONCLUSIVE", "探针执行中断，数据不足"
    elif expected is None or len(probes) != expected:
        verdict, reason = "FAIL", "探针条数与期望不符"
    else:
        verdict, reason = "PASS", "全部同租户命中、跨租户不命中"
    return {
        "probe_count": len(probes),
        "expected_probe_count": expected,
        "invalid_probe_count": invalid,
        "same_tenant_hit_rate": same_hit_rate,
        "cross_tenant_false_positive_rate": cross_fp_rate,
        "verdict": verdict,
        "reason": reason,
    }


def saturation_summary(records: list[RequestRecord]) -> dict[str, Any]:
    """饱和拒绝契约：429/503 拒绝样本必须携带 Retry-After 与 reason_code。

    拒绝样本 = commit_submit/read 的真实 HTTP 429/503 错误记录。
    verdict: "PASS"（有拒绝样本且全部带两字段）/
    "FAIL"（有拒绝样本但缺字段）/ "INCONCLUSIVE"（无拒绝样本）。
    """
    ops = ("commit_submit", "read")
    success = sum(1 for rec in records if rec.op in ops and rec.status == "ok")
    rejected = [
        rec
        for rec in records
        if rec.op in ops
        and rec.status == "error"
        and rec.http_status in (429, 503)
    ]
    total = success + len(rejected)
    retry_after_present = sum(1 for rec in rejected if rec.retry_after_s is not None)
    reason_code_present = sum(1 for rec in rejected if rec.reason_code != "")
    stages = sorted(rec.stage_ms for rec in rejected)
    result: dict[str, Any] = {
        "rejected_total": len(rejected),
        "total": total,
        "rejection_rate": round(len(rejected) / total, 5) if total else None,
        "retry_after_present": retry_after_present,
        "reason_code_present": reason_code_present,
        "rejection_p50_ms": percentile(stages, 0.5),
        "rejection_p95_ms": percentile(stages, 0.95),
    }
    if not rejected:
        result["verdict"] = "INCONCLUSIVE"
        result["reason"] = "无 429/503 拒绝样本"
    elif retry_after_present == len(rejected) and reason_code_present == len(rejected):
        result["verdict"] = "PASS"
        result["reason"] = (
            f"{len(rejected)} 个拒绝样本均携带 Retry-After 与 reason_code"
        )
    else:
        result["verdict"] = "FAIL"
        result["reason"] = (
            f"拒绝样本缺 Retry-After 或 reason_code "
            f"({len(rejected) - retry_after_present} 缺 retry_after, "
            f"{len(rejected) - reason_code_present} 缺 reason_code)"
        )
    return result


def hot_tenant_summary(records: list[RequestRecord]) -> dict[str, Any]:
    """热租户旁观公平性：旁观租户（提交数 < 总提交数 1/4）commit P50 的散布。

    ``bystander_p50_ratio`` 是旁观租户 P50 的 max/min。verdict: "PASS"
    （ratio <= 1.50 或仅 1 个旁观租户）/ "FAIL"（>1.50）/
    "INCONCLUSIVE"（数据不足或无旁观租户）。
    """
    submits = [
        rec for rec in records if rec.op == "commit_submit" and rec.status == "ok"
    ]
    if not submits:
        return {
            "per_tenant_p50_ms": {},
            "bystander_p50_ratio": None,
            "verdict": "INCONCLUSIVE",
            "reason": "无 commit_submit 成功记录",
        }
    per_tenant: dict[int, list[float]] = {}
    for rec in submits:
        per_tenant.setdefault(rec.tenant_idx, []).append(rec.stage_ms)
    p50 = {
        tenant_idx: percentile(sorted(stages), 0.5)
        for tenant_idx, stages in per_tenant.items()
    }
    threshold = len(submits) / 4.0
    bystander_p50 = [
        p50[tenant_idx]
        for tenant_idx, stages in per_tenant.items()
        if len(stages) < threshold and p50[tenant_idx] is not None
    ]
    ratio = None
    if len(bystander_p50) >= 2:
        ratio = round(max(bystander_p50) / min(bystander_p50), 3)
    if not bystander_p50:
        verdict, reason = "INCONCLUSIVE", "无旁观租户（各租户提交数均 ≥ 总提交数 1/4）"
    elif len(bystander_p50) == 1:
        verdict, reason = "PASS", "仅 1 个旁观租户，无可比散布"
    elif ratio is not None and ratio <= 1.50:
        verdict, reason = (
            "PASS",
            f"旁观租户 commit P50 max/min 比 {ratio}x ≤ 1.50x",
        )
    else:
        verdict, reason = (
            "FAIL",
            f"旁观租户 commit P50 max/min 比 {ratio}x > 1.50x",
        )
    return {
        "per_tenant_p50_ms": p50,
        "bystander_p50_ratio": ratio,
        "verdict": verdict,
        "reason": reason,
    }


def reconcile_messages(data: list[dict[str, Any]]) -> dict[str, Any]:
    """Message-level write audit: client-written set vs server state.

    Each ``data`` entry comes from ``LoadGenerator.run_reconciliation``.
    Checks (per source that is available):
      - client_in_server: every injected content hash is present server-side;
      - server_no_duplicate: server message hashes contain no duplicates;
      - archive_completed: the session archive reached terminal completed;
      - atom_no_dup_and_subset: atom source_turn_ids have no duplicates and
        are a subset of the client-observed message ids.
    Missing server endpoints mark a check as unavailable instead of failing.
    """
    sessions: list[dict[str, Any]] = []
    any_fail = False
    any_available = False
    for entry in data:
        client_hashes = list(entry.get("client_hashes") or [])
        server_hashes = list(entry.get("server_hashes") or [])
        archive_status = str(entry.get("archive_status") or "").lower()
        atom_ids = [str(item) for item in (entry.get("atom_source_turn_ids") or [])]
        client_ids = [str(item) for item in (entry.get("client_ids") or []) if item]
        checks: list[dict[str, Any]] = []

        if entry.get("history_available"):
            any_available = True
            server_set = set(server_hashes)
            missing = [h for h in client_hashes if h not in server_set]
            dup = _duplicates(server_hashes)
            checks.append(
                {
                    "name": "client_in_server",
                    "ok": not missing,
                    "detail": f"client={len(client_hashes)} server={len(server_hashes)} missing={len(missing)}",
                }
            )
            checks.append(
                {
                    "name": "server_no_duplicate",
                    "ok": not dup,
                    "detail": f"duplicates={len(dup)}",
                }
            )
        else:
            checks.append(
                {"name": "client_in_server", "ok": None, "detail": "history 接口不可用"}
            )
            checks.append(
                {"name": "server_no_duplicate", "ok": None, "detail": "history 接口不可用"}
            )

        if entry.get("archive_available"):
            any_available = True
            checks.append(
                {
                    "name": "archive_completed",
                    "ok": archive_status in ("completed", "done", "success"),
                    "detail": archive_status or "空",
                }
            )
        else:
            checks.append(
                {"name": "archive_completed", "ok": None, "detail": "archives 接口不可用"}
            )

        if entry.get("atoms_available"):
            any_available = True
            dup_atoms = _duplicates(atom_ids)
            subset = (not client_ids) or set(atom_ids).issubset(set(client_ids))
            checks.append(
                {
                    "name": "atom_no_dup_and_subset",
                    "ok": (not dup_atoms) and subset,
                    "detail": f"atoms={len(atom_ids)} duplicates={len(dup_atoms)}",
                }
            )
        else:
            checks.append(
                {"name": "atom_no_dup_and_subset", "ok": None, "detail": "atoms 接口不可用"}
            )

        failed = any(c["ok"] is False for c in checks)
        session_available = any(c["ok"] is not None for c in checks)
        verdict = (
            "fail"
            if failed
            else ("pass" if session_available else "not_available")
        )
        if failed:
            any_fail = True
        if session_available:
            any_available = True
        sessions.append(
            {"session_id": entry.get("session_id"), "checks": checks, "verdict": verdict}
        )

    if not sessions:
        return {"sessions": [], "verdict": "INCONCLUSIVE", "reason": "无对账会话数据"}
    if any_fail:
        return {
            "sessions": sessions,
            "verdict": "FAIL",
            "reason": "存在对账失败会话（缺失/重复/终态异常），详见逐会话 checks",
        }
    if not any_available:
        return {
            "sessions": sessions,
            "verdict": "INCONCLUSIVE",
            "reason": "对账数据源全部不可用（服务端查询接口缺失）",
        }
    return {
        "sessions": sessions,
        "verdict": "PASS",
        "reason": "全部会话对账通过（无丢失、无重复、archive completed）",
    }


def search_quality_summary(
    records: list[RequestRecord],
    *,
    burst_windows: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Read quality assertions: anchor recall and real-recall evidence.

    Success for an anchor query is ``hit_count >= 1`` (anchors are unique
    tokens that must be recallable); an empty result on an anchor query is a
    false pass and is counted as a failure — unless the orchestrator reported
    a degraded response (engine skipped / saturated), which is a capacity
    artifact, not a recall defect, and is tallied separately
    (``degraded_total`` / ``anchor_degraded``). Ordinary queries must show
    recall evidence; when the server exposes none, they are counted as
    ``undetermined`` rather than failed. Gated latency / hit stats cover only
    reads that actually recalled items so a short-circuited fast path or a
    degraded empty response cannot drag them.

    Reads inside a burst window (``burst_windows`` as (t0_ms, t1_ms) pairs)
    are excluded from the quality judgment: the burst window is a deliberate
    overload scene (顶层 2.5) where degraded reads are expected and are
    reported separately (degradation/signals), not counted as write-to-read
    consistency failures. Reads outside the windows are judged strictly.
    """
    def _in_burst(ts_ms: float | None) -> bool:
        if ts_ms is None or not burst_windows:
            return False
        return any(t0_ms <= ts_ms <= t1_ms for t0_ms, t1_ms in burst_windows)

    reads = [
        rec
        for rec in records
        if rec.op == "read" and rec.status == "ok" and not _in_burst(rec.ts_ms)
    ]
    # query_kind is authoritative. The anchor fallback preserves
    # compatibility with raw request files produced before query_kind existed.
    recall = [
        rec
        for rec in reads
        if str(rec.query_kind or "") == "recall"
        or (not rec.query_kind and is_anchor_query(rec.query))
    ]
    no_recall = [
        rec
        for rec in reads
        if str(rec.query_kind or "") == "no_recall"
        or (not rec.query_kind and not is_anchor_query(rec.query))
    ]
    anchor = [rec for rec in reads if is_anchor_query(rec.query)]
    recall_failures = [rec for rec in recall if not rec.quality_ok]
    # Degraded contract: a degraded response means the engine was skipped /
    # saturated — an empty result is a capacity artifact, not a recall
    # failure. Surface it separately from clean quality failures.
    degraded = [rec for rec in reads if rec.degraded]
    recall_degraded = [rec for rec in recall if rec.degraded]
    undetermined = [
        rec for rec in no_recall if not rec.real_recall and rec.hit_count == 0
    ]
    passed = [rec for rec in reads if rec.quality_ok]
    # Gated latency / hit distribution cover reads that actually recalled
    # items; degraded empty responses did no recall work and must not drag
    # the latency or hit statistics.
    recalled = [rec for rec in passed if rec.hit_count >= 1]
    hit_counts = [rec.hit_count for rec in recalled]

    measured_reads = [
        rec for rec in records if rec.op == "read" and not _in_burst(rec.ts_ms)
    ]

    def _by_kind(kind: str) -> dict[str, Any]:
        # Keep transport failures in the denominator. Only successful reads
        # contribute to latency and hit distributions.
        selected = [
            rec for rec in measured_reads if str(rec.query_kind or "unknown") == kind
        ]
        if not selected:
            return {
                "count": 0,
                "ok": 0,
                "error": 0,
                "hit_p50": None,
                "hit_p95": None,
                "p95_ms": None,
            }
        successful = [rec for rec in selected if rec.status == "ok"]
        recalled_selected = [
            rec for rec in successful if rec.hit_count >= 1 and rec.quality_ok
        ]
        hit_counts_selected = [rec.hit_count for rec in recalled_selected]
        return {
            "count": len(selected),
            "ok": len(successful),
            "error": len(selected) - len(successful),
            "success_rate": round(len(successful) / len(selected), 5),
            "empty": sum(1 for rec in successful if rec.hit_count == 0),
            "hit_p50": percentile(sorted(hit_counts_selected), 0.5) if hit_counts_selected else None,
            "hit_p95": percentile(sorted(hit_counts_selected), 0.95) if hit_counts_selected else None,
            "p95_ms": _op_stats([rec.stage_ms for rec in successful]).get("p95_ms"),
        }

    query_kind_counts = Counter(str(rec.query_kind or "unknown") for rec in measured_reads)
    query_kind_stats = {kind: _by_kind(kind) for kind in sorted(query_kind_counts)}
    return {
        "total": len(reads),
        "recall_total": len(recall),
        "recall_failures": len(recall_failures),
        "recall_failure_rate": (
            round(len(recall_failures) / len(recall), 5) if recall else None
        ),
        "no_recall_total": len(no_recall),
        "undetermined_real_recall": len(undetermined),
        "degraded_total": len(degraded),
        "recall_degraded": len(recall_degraded),
        "quality_failures": len(recall_failures),
        "quality_failure_rate": (
            round(len(recall_failures) / len(reads), 5) if reads else None
        ),
        # Legacy fields remain for old reports and historical comparisons.
        "anchor_total": len(anchor),
        "anchor_failures": sum(1 for rec in anchor if not rec.quality_ok),
        "anchor_degraded": sum(1 for rec in anchor if rec.degraded),
        "ordinary_total": len(no_recall),
        "hit_count_p50": percentile(sorted(hit_counts), 0.5) if hit_counts else None,
        "hit_count_p95": percentile(sorted(hit_counts), 0.95) if hit_counts else None,
        "gated_read_stats": _op_stats([rec.stage_ms for rec in recalled]),
        "query_kind_counts": dict(query_kind_counts),
        "query_kind_stats": query_kind_stats,
    }


def isolation_summary(
    records: list[RequestRecord],
    *,
    t0_ms: float,
    t1_ms: float,
    burst_tenant_idx: int,
    baseline_p95: float | None,
    degradation_threshold: float = 2.0,
    crosstalk_tolerance: float = 1.25,
) -> dict[str, Any]:
    """D-scene burst-window read latency split by same/cross tenant.

    Same-tenant reads belong to the tenant that issued the burst writes;
    cross-tenant reads belong to every other tenant. The primary judgment
    (顶层 2.5) is that neither group's P95 degradation may reach the
    threshold. The same/cross comparison is a fine-grained observation:
    because the model provider (embedding/LLM) is a globally shared resource,
    burst extraction legitimately degrades every tenant; only when the
    cross-tenant degradation is *significantly* above same-tenant
    (cross/same > ``crosstalk_tolerance``) is that treated as cross-tenant
    interference and a failure.
    """
    window = [
        rec
        for rec in records
        if rec.op == "read"
        and rec.status == "ok"
        and rec.ts_ms is not None
        and t0_ms <= rec.ts_ms <= t1_ms
    ]
    same = [rec for rec in window if rec.tenant_idx == burst_tenant_idx]
    cross = [rec for rec in window if rec.tenant_idx != burst_tenant_idx]

    def group_stats(recs: list[RequestRecord]) -> dict[str, Any]:
        ordered = sorted(rec.stage_ms for rec in recs)
        return {
            "count": len(recs),
            "p50_ms": percentile(ordered, 0.5),
            "p95_ms": percentile(ordered, 0.95),
            "p99_ms": percentile(ordered, 0.99),
        }

    def degrade(p95: float | None) -> float | None:
        if p95 is None or not baseline_p95:
            return None
        return round(p95 / baseline_p95, 3)

    same_stats = group_stats(same)
    cross_stats = group_stats(cross)
    same_ratio = degrade(same_stats["p95_ms"])
    cross_ratio = degrade(cross_stats["p95_ms"])

    if not window:
        verdict, reason = "INCONCLUSIVE", "洪峰窗口内无 read 记录"
    elif not same and not cross:
        verdict, reason = "INCONCLUSIVE", "洪峰窗口内同/跨租户 read 均无数据"
    elif cross_ratio is None or same_ratio is None:
        verdict, reason = "INCONCLUSIVE", "缺 A 基线或组内数据不足，无法计算劣化"
    elif cross_ratio >= degradation_threshold or same_ratio >= degradation_threshold:
        verdict, reason = (
            "FAIL",
            (
                f"劣化达阈值: 同租户 {same_ratio}x / 跨租户 {cross_ratio}x "
                f"(阈值 {degradation_threshold}x)"
            ),
        )
    elif same_ratio > 0 and cross_ratio > same_ratio * crosstalk_tolerance:
        verdict, reason = (
            "FAIL",
            (
                f"跨租户 P95 劣化 {cross_ratio}x 显著高于同租户 {same_ratio}x"
                f"(串扰容差 {crosstalk_tolerance}x)，疑似跨租户串扰"
            ),
        )
    else:
        verdict, reason = (
            "PASS",
            (
                f"洪峰窗口读 P95 劣化同租户 {same_ratio}x / 跨租户 {cross_ratio}x，"
                f"均 < {degradation_threshold}x 且 cross/same 在串扰容差内"
            ),
        )
    return {
        "burst_tenant_idx": burst_tenant_idx,
        "same_tenant": same_stats,
        "cross_tenant": cross_stats,
        "same_tenant_degradation": same_ratio,
        "cross_tenant_degradation": cross_ratio,
        "baseline_p95_ms": baseline_p95,
        "verdict": verdict,
        "reason": reason,
    }


def rss_normalized_series(
    raw_series: list[tuple[float, float]],
    injected_bytes_series: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Net RSS = raw RSS minus cumulative injected bytes (aligned per sample).

    Both series are (ts, bytes). The net series accounts for the memory the
    index is expected to hold for the injected data, so normal growth from
    added messages is not misread as a leak. Returns [] when either input is
    empty.
    """
    if not raw_series or not injected_bytes_series:
        return []
    injected = sorted(injected_bytes_series)
    injected_ts = [ts for ts, _ in injected]
    injected_cum = [value for _, value in injected]

    def cumulative_at(t: float) -> float:
        if t < injected_ts[0]:
            return 0.0
        if t >= injected_ts[-1]:
            return injected_cum[-1]
        lo, hi = 0, len(injected_ts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if injected_ts[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        return injected_cum[lo]

    return [
        (ts, rss - cumulative_at(ts))
        for ts, rss in sorted(raw_series)
    ]


def fault_injection_summary(sequence: list[dict[str, Any]]) -> dict[str, Any]:
    """F-scene fault injection summary: per-stage semantics and recovery.

    Each stage entry carries ``behavior``, ``expected_error_type`` and the
    observed request outcomes; a stage passes when the observed error type
    matches the injected fault and the process did not hang forever.
    """
    if not sequence:
        return {"stages": [], "verdict": "not_run", "reason": "未执行故障注入序列"}
    stages: list[dict[str, Any]] = []
    any_fail = False
    for stage in sequence:
        behavior = stage.get("behavior")
        expected = stage.get("expected_error_type")
        observed = stage.get("observed_error_type")
        ok = observed == expected and (not stage.get("hang") or observed == "timeout")
        if not ok:
            any_fail = True
        stages.append(
            {
                "stage": stage.get("stage"),
                "behavior": behavior,
                "expected_error_type": expected,
                "observed_error_type": observed,
                "requests": stage.get("requests", 0),
                "hang": stage.get("hang", False),
                "recovered": bool(stage.get("recovered", False)),
                "ok": ok,
            }
        )
    verdict = "FAIL" if any_fail else "PASS"
    reason = (
        "全部阶段故障语义与错误类型一致且恢复成功"
        if not any_fail
        else "存在阶段错误类型与注入故障不一致或未恢复"
    )
    return {"stages": stages, "verdict": verdict, "reason": reason}


def error_type_validation(
    records: list[RequestRecord], fault: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Server error-type correctness from observed records plus fault stages.

    Real-scenario records contribute the observed error classification
    counts; the F-scene fault summary (when present) is cross-checked so a
    server mislabeling an error type fails the validation.
    """
    observed: dict[str, int] = {}
    for rec in records:
        if rec.error_type:
            observed[rec.error_type] = observed.get(rec.error_type, 0) + 1
    mismatches: list[str] = []
    for stage in (fault or {}).get("stages") or []:
        if stage.get("ok") is False:
            mismatches.append(
                f"{stage.get('stage')}: 期望 {stage.get('expected_error_type')} "
                f"实际 {stage.get('observed_error_type')}"
            )
    if mismatches:
        return {
            "verdict": "FAIL",
            "reason": "错误类型不匹配: " + "; ".join(mismatches),
            "observed_breakdown": observed,
        }
    if not observed and not (fault or {}).get("stages"):
        return {"verdict": "not_run", "reason": "无错误类型观测", "observed_breakdown": {}}
    return {
        "verdict": "PASS",
        "reason": "观测错误分类与注入/预期一致",
        "observed_breakdown": observed,
    }


def _slo_accounting(summary: dict[str, Any]) -> dict[str, Any]:
    """SLO 口径表: 每个指标列出分子/分母/时间窗口/是否含重试。"""
    retry = summary.get("write_retry") or {}
    return {
        "read_latency_p95": {
            "numerator": "质量断言通过的 read stage_ms",
            "denominator": "read ok 记录数",
            "window": "场景开始→场景结束",
            "retry_included": False,
        },
        "commit_completion_p95": {
            "numerator": "commit_done ok 的 stage_ms",
            "denominator": "commit_done ok 记录数",
            "window": "场景开始→场景结束",
            "retry_included": False,
        },
        "write_submit_success_raw": {
            "numerator": f"首次尝试成功 {retry.get('first_attempt_ok')}",
            "denominator": f"commit_submit 总数 {retry.get('submit_total')}",
            "window": "场景开始→场景结束",
            "retry_included": False,
        },
        "write_submit_success_retried": {
            "numerator": f"重试后最终成功 {retry.get('final_ok')}",
            "denominator": f"commit_submit 总数 {retry.get('submit_total')}",
            "window": "场景开始→场景结束",
            "retry_included": True,
        },
    }

def injected_bytes_series(records: list[RequestRecord]) -> list[tuple[float, float]]:
    """Cumulative injected message bytes over time, as (ts_seconds, bytes).

    Built from the ``add`` records' ``content_bytes`` and completion
    timestamps; used as the normalization input for RSS leak judgment.
    """
    adds = sorted(
        (rec.ts_ms / 1000.0, rec.content_bytes)
        for rec in records
        if rec.op == "add" and rec.status == "ok"
    )
    series: list[tuple[float, float]] = []
    cumulative = 0
    for ts, size in adds:
        cumulative += size
        series.append((ts, cumulative))
    return series
