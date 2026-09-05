#!/usr/bin/env python3
"""可重复的真实多租户 EchoMem 压测验收套件。

套件只负责编排：每个 case 由 run_stress 子进程执行，并保留逐请求 CSV 与
原始服务端遥测；套件在其上叠加场景/轮次元数据，并把 run_stress 原生产物
推导成验收求值器消费的契约摘要（summary.json / commit_results.csv /
search_results.csv）。只有每次运行都使用独立租户凭据时才允许做出上线结论。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import os
import signal
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 支持 ``python -m performance.formal_suite`` 与直接执行两种方式。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from performance.acceptance import (
    build_model_analysis_input,
    evaluate_pr421_acceptance,
)
from performance.perf_preflight import run_preflight
from performance.probes.auth_preflight import run as run_auth_preflight


# 正式运行复现在线客户端。压测端不施加 FIFO、优先级、lane 或租户公平调度。
POLICIES = ("server-observe",)

# A seed is useful for hot-cache/retrieval-quality observations, but it is
# not a prerequisite for measuring admission, capacity, fairness, recovery,
# or metrics. Keeping this dependency explicit prevents a slow real-model
# warm-up from blocking unrelated black-box scheduler evidence.
SEED_REQUIRED_SCENARIOS = {
    "baseline",
    "mixed",
    "commit-storm",
    "commit-barrier",
    "saturation",
    "tenant-skew",
    "fairness-bounded",
    "search-priority-blackbox",
    "search-storm",
}


def _scenario_requires_seed(case: dict[str, Any], scenario: str = "") -> bool:
    """Whether a scenario needs preloaded memory for its intended claim."""
    source = str(case.get("_source_scenario") or scenario or "").strip()
    return bool(
        case.get("seed_required")
        or source in SEED_REQUIRED_SCENARIOS
        or (not source and str(case.get("label") or "").strip() in SEED_REQUIRED_SCENARIOS)
    )


def _seed_dependency(case: dict[str, Any], scenario: str = "") -> dict[str, Any]:
    """Describe the seed dependency in every case manifest."""
    required = _scenario_requires_seed(case, scenario)
    return {
        "required_for_intended_claim": required,
        "purpose": (
            "hot-cache/retrieval evidence"
            if required
            else "not required; active session identity is enough for black-box load"
        ),
    }

# 正式套件的运行器。每个 case 作为独立子进程执行，产物落到 case 的 run/ 子目录。
RUNNER = Path(__file__).with_name("run_stress.py")

# Acceptance targets from EchoMem PR421. These are recorded in suite.json so
# every result carries the intended gate instead of relying on report prose.
PR421_ACCEPTANCE_TARGETS: dict[str, Any] = {
    "source": {
        "repository": "tech-innovation-group/EchoMem",
        "pull_request": 421,
        "commit": "4bafa33b46487ec451498d114b9bf6c784462f3e",
    },
    "search_p95_isolation_ratio_max": 1.20,
    "tenant_fairness_jain_min": 0.90,
    "accepted_commit_recovery_rate_min": 1.00,
    "rejection_response_required": ["status_code", "retry_after", "reason_code"],
    "lane_metric_families": [
        "echomem_lane_queued",
        "echomem_lane_wait_seconds",
        "echomem_lane_exec_seconds",
        "echomem_lane_rejected_total",
    ],
    "lane_values": [
        "recall_engine",
        "recall_intent_llm",
        "recall_query_embedding",
        "recall_rerank",
        "commit",
    ],
    "lane_label_contract": {
        "allowed_labels": ["lane"],
        "rejected_labels": ["tenant_id", "tenant"],
        "rejection_reason_label": "reason_code",
    },
    "fanout_metric_families": [
        "echomem_engine_fanout_exec_seconds",
        "echomem_engine_fanout_skipped_total",
    ],
    "saturation_search_rejection_rate_max": 0.05,
    "saturation_rejection_latency_max_s": 1.0,
    "hot_tenant_bystander_median_ratio_max": 1.50,
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "baseline": {
        "label": "单租户基线",
        "tenants": 1,
        "duration_s": 600,
        "search_rps": 2.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "mixed": {
        "label": "四租户均衡混合负载",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 8.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "fairness-steady": {
        "label": "四租户正式稳态公平性（每租户固定速率）",
        "tenants": 4,
        "duration_s": 300,
        "search_rps_per_tenant": 2.0,
        # Keep Search and Commit on the same steady-state window with an
        # explicit per-tenant rate. Without this field run_stress falls back
        # to zero Commit arrivals, making the Jain denominator meaningless.
        "commit_rpm_per_tenant": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
        "steady_state_fairness": True,
        "min_commit_submitted_per_tenant": 6,
    },
    "commit-storm": {
        "label": "Commit 压力",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 4.0,
        "commit_rpm": 10.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "commit-barrier": {
        "label": "160 Commit 屏障风暴（Zipf 热租户）",
        "tenants": 4,
        "duration_s": 60,
        "search_rps": 4.0,
        "commit_rpm": 0.0,
        "commit_barrier": True,
        "commit_barrier_count": 160,
        "commit_tenant_distribution": "zipf",
        "commit_zipf_exponent": 2.0,
        "quick_barrier_count_cap": 16,
        "sessions_per_tenant": 40,
        "messages_per_session": 3,
    },
    "saturation": {
        "label": "128 并发入口饱和",
        "tenants": 4,
        "duration_s": 60,
        "search_rps": 32.0,
        "commit_rpm": 0.0,
        "commit_barrier": True,
        "commit_barrier_count": 128,
        "commit_tenant_distribution": "uniform",
        "quick_barrier_count_cap": 16,
        "sessions_per_tenant": 32,
        "messages_per_session": 3,
    },
    "tenant-skew": {
        "label": "热租户 200 + 其他租户各 20",
        "tenants": 4,
        "duration_s": 120,
        "search_rps": 8.0,
        "commit_rpm": 0.0,
        "commit_barrier": True,
        "commit_barrier_count": 260,
        "commit_tenant_distribution": "explicit",
        "commit_tenant_counts": [200, 20, 20, 20],
        "sessions_per_tenant": 200,
        "messages_per_session": 3,
    },
    "capacity-16": {
        "label": "16 活跃用户容量阶梯（4 租户 × 4 session）",
        "tenants": 4,
        "capacity_active_users": 16,
        "active_sessions_per_tenant": 4,
        "duration_s": 300,
        "search_rps": 16.0,
        # Capacity is a Search-only measurement. Commit completion belongs
        # to the durability/priority scenarios and must not extend the
        # capacity case timeout.
        "commit_rpm": 0.0,
        "quick_commit_rpm": 0.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
    },
    "capacity-2": {
        "label": "2 活跃用户容量阶梯（2 租户 × 1 session）",
        "tenants": 2,
        "capacity_active_users": 2,
        "active_sessions_per_tenant": 1,
        "duration_s": 180,
        "search_rps": 2.0,
        "commit_rpm": 0.0,
        "quick_commit_rpm": 0.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
    },
    "capacity-4": {
        "label": "4 活跃用户容量阶梯（4 租户 × 1 session）",
        "tenants": 4,
        "capacity_active_users": 4,
        "active_sessions_per_tenant": 1,
        "duration_s": 180,
        "search_rps": 4.0,
        "commit_rpm": 0.0,
        "quick_commit_rpm": 0.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
    },
    "capacity-8": {
        "label": "8 活跃用户容量阶梯（4 租户 × 2 session）",
        "tenants": 4,
        "capacity_active_users": 8,
        "active_sessions_per_tenant": 2,
        "duration_s": 180,
        "search_rps": 8.0,
        "commit_rpm": 0.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
    },
    "capacity-32": {
        "label": "32 活跃用户容量阶梯（4 租户 × 8 session）",
        "tenants": 4,
        "capacity_active_users": 32,
        "active_sessions_per_tenant": 8,
        "duration_s": 300,
        "search_rps": 32.0,
        "commit_rpm": 0.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
    },
    "capacity-64": {
        "label": "64 活跃用户容量阶梯（4 租户 × 16 session）",
        "tenants": 4,
        "capacity_active_users": 64,
        "active_sessions_per_tenant": 16,
        "duration_s": 300,
        "search_rps": 64.0,
        "commit_rpm": 0.0,
        "quick_commit_rpm": 0.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
    },
    "capacity-128": {
        "label": "128 活跃用户容量阶梯（4 租户 × 32 session）",
        "tenants": 4,
        "capacity_active_users": 128,
        "active_sessions_per_tenant": 32,
        "duration_s": 300,
        "search_rps": 128.0,
        "commit_rpm": 0.0,
        "quick_commit_rpm": 0.0,
        "sessions_per_tenant": 2,
        "messages_per_session": 3,
        "search_query_profile": "no-recall-only",
    },
    "search-priority-blackbox": {
        "label": "Search/Commit 同时到达（服务端优先级黑盒）",
        "tenants": 4,
        "duration_s": 90,
        "search_rps": 16.0,
        "commit_rpm": 0.0,
        "search_workers": 32,
        "commit_workers": 32,
        "commit_barrier": True,
        "commit_barrier_count": 128,
        "commit_tenant_distribution": "uniform",
        # Keep quick mode bounded, but retain enough real Commit arrivals for
        # the strict Search-priority acceptance gate.
        "quick_barrier_count_cap": 32,
        "sessions_per_tenant": 32,
        "messages_per_session": 3,
        "blackbox_search_priority": True,
    },
    "search-storm": {
        "label": "Search 压力",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 20.0,
        "commit_rpm": 1.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "soak": {
        "label": "长稳态",
        "tenants": 4,
        "duration_s": 1800,
        "search_rps": 8.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
}


def report4_scenarios() -> dict[str, dict[str, Any]]:
    """Build report(4)'s A/B/C/D matrix with a valid read-only baseline."""
    scenarios: dict[str, dict[str, Any]] = {}
    for concurrency in (1, 4, 16):
        workers = 8 * concurrency
        suffix = f"c{concurrency}"
        common = {
            "tenants": 8,
            "duration_s": 60,
            "search_workers": workers,
            "commit_workers": workers,
            "sessions_per_tenant": max(2, concurrency),
            "messages_per_session": 3,
        }
        scenarios[f"A-{suffix}"] = {
            **common,
            "label": f"A 纯读基线 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "read_only": True,
        }
        scenarios[f"B-{suffix}"] = {
            **common,
            "label": f"B 纯写注入 / 每租户并发 {concurrency}",
            "search_rps": 0.0,
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": workers,
        }
        for ratio, search_factor in (("8-1", 8), ("4-1", 4), ("1-1", 1)):
            scenarios[f"C{ratio}-{suffix}"] = {
                **common,
                "label": f"C 读写 {ratio} / 每租户并发 {concurrency}",
                "search_rps": float(workers * search_factor),
                "commit_rpm": float(workers),
            }
        scenarios[f"D-{suffix}"] = {
            **common,
            "label": f"D 连续注入洪峰 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": workers,
            "commit_barrier_waves": 3,
            "commit_barrier_cooldown_s": 10.0,
        }
    return scenarios


def report6_scenarios() -> dict[str, dict[str, Any]]:
    """Build report(6)'s 8-tenant, 12-case A/B/C/D matrix.

    The runner's rates are global, while the report(6) concurrency is per
    tenant.  We therefore use eight tenant lanes and scale the global offered
    rate by the requested per-tenant concurrency.  Commit counts in C are
    rounded to whole requests per tenant, which is recorded in the manifest.
    """
    scenarios: dict[str, dict[str, Any]] = {}
    for concurrency in (1, 2):
        workers = 8 * concurrency
        common = {
            "tenants": 8,
            "duration_s": 60,
            "search_workers": workers,
            "commit_workers": workers,
            "sessions_per_tenant": 2,
            "messages_per_session": 10,
            "per_tenant_concurrency": concurrency,
        }
        suffix = f"@{concurrency}"
        scenarios[f"A{suffix}"] = {
            **common,
            "label": f"A 纯读基线 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "read_only": True,
        }
        scenarios[f"B{suffix}"] = {
            **common,
            "label": f"B 纯写注入 / 每租户并发 {concurrency}",
            "search_rps": 0.0,
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": workers,
        }
        for ratio, factor in (("8:1", 8), ("4:1", 4), ("1:1", 1)):
            # Search is a global arrival rate.  Commit is a per-tenant
            # requests/minute rate.  With eight tenants, this gives an exact
            # global read:write ratio over the one-minute scenario window:
            # (8 * concurrency * factor reads/s) : (8 * 60 * concurrency writes/min).
            commit_rpm = 60.0 * concurrency
            scenarios[f"C{ratio}{suffix}"] = {
                **common,
                "label": f"C 读写 {ratio} / 每租户并发 {concurrency}",
                "search_rps": float(workers * factor),
                "commit_rpm": commit_rpm,
            }
        scenarios[f"D{suffix}"] = {
            **common,
            "label": f"D 注入洪峰 / 每租户并发 {concurrency}",
            "search_rps": float(workers),
            "commit_rpm": 0.0,
            "commit_barrier": True,
            "commit_barrier_count": 32,
            "commit_barrier_waves": 1,
            "commit_barrier_cooldown_s": 0.0,
            "commit_burst_window_s": 10.0,
        }
    return scenarios


def complete_scenarios() -> dict[str, dict[str, Any]]:
    """Combine the PR397/report(6) and PR421 scenario catalogs."""
    combined: dict[str, dict[str, Any]] = {}
    combined.update(report6_scenarios())
    combined.update(SCENARIOS)
    return combined


SCENARIO_PROFILES = {
    "pr421": SCENARIOS,
    "report4": report4_scenarios(),
    "report6": report6_scenarios(),
    "complete": complete_scenarios(),
}

# A bounded single-instance profile for the actual 4U8G deployment.  The
# machine profile is independent from the tenant count: 4U8G can exercise
# the eight-tenant PR397 matrix when the deployment has eight independent
# credentials.  Keep soak opt-in, but do not silently drop PR397 from the
# routine black-box run.
FOUR_U8G_SCENARIOS = dict(report6_scenarios())
FOUR_U8G_SCENARIOS.update({
    name: SCENARIOS[name]
    for name in (
        "baseline",
        "mixed",
        "fairness-steady",
        "commit-barrier",
        "saturation",
        "tenant-skew",
        "search-priority-blackbox",
        "capacity-2",
        "capacity-4",
        "capacity-8",
        "capacity-16",
        "capacity-32",
        "capacity-64",
        "capacity-128",
    )
})
for _capacity_name in (
    "capacity-2",
    "capacity-4",
    "capacity-8",
    "capacity-16",
    "capacity-32",
    "capacity-64",
    "capacity-128",
):
    # Capacity is a Search-only measurement in quick mode.  Keep this
    # override on the bounded 4U8G catalog as well as the base catalog;
    # otherwise the profile replacement loses the explicit zero rate and
    # run_stress falls back to its default Commit rate.
    FOUR_U8G_SCENARIOS[_capacity_name]["quick_commit_rpm"] = 0.0
FOUR_U8G_SCENARIOS["fairness-bounded"] = {
    "label": "四租户均衡 Commit/Search 公平性（短窗口）",
    "tenants": 4,
    "duration_s": 30,
    "search_rps": 8.0,
    "commit_rpm": 0.0,
    "commit_barrier": True,
    "commit_barrier_count": 32,
    "commit_tenant_distribution": "uniform",
    "sessions_per_tenant": 8,
    "messages_per_session": 1,
    "fairness_bounded": True,
    # Fairness needs enough arrivals for every tenant; use the same minimum
    # bounded sample as the strict priority gate.
    # Two Commit arrivals per tenant are enough for a bounded Jain sample and
    # keep this real-model smoke case inside the one-hour budget.
    "quick_barrier_count_cap": 8,
    # Real-model completion can exceed the generic 10-second barrier drain.
    # This is still a bounded case-specific window, not a soak test.
    "quick_barrier_drain_timeout_s": 90.0,
}
SCENARIO_PROFILES["4u8g"] = FOUR_U8G_SCENARIOS

# The historical ``4u8g`` profile is kept for compatibility with existing
# quick commands.  This explicit profile runs both source plans in full:
# PR397/report(6) has 12 cases and the PR421 4U8G catalog has 27 cases.
# Names are namespaced because the two plans intentionally reuse some case
# names; the original scenario name is retained for acceptance evaluation.
FOUR_U8G_FULL_SCENARIOS: dict[str, dict[str, Any]] = {}
for _source_prefix, _catalog in (
    ("pr397", report6_scenarios()),
    ("pr421", FOUR_U8G_SCENARIOS),
):
    for _name, _case in _catalog.items():
        _full_name = f"{_source_prefix}__{_name}"
        FOUR_U8G_FULL_SCENARIOS[_full_name] = {
            **_case,
            "_plan_source": _source_prefix,
            "_source_scenario": _name,
        }
SCENARIO_PROFILES["4u8g-full"] = FOUR_U8G_FULL_SCENARIOS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tenants(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tenants = payload.get("tenants") if isinstance(payload, dict) else payload
    if not isinstance(tenants, list) or not tenants:
        raise ValueError("tenant config must contain a non-empty tenants list")
    return tenants


def write_subset(path: Path, tenants: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"tenants": tenants}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _seed_anchor_queries(tenant_count: int) -> str:
    """Return deterministic queries for synthetic data reused across cases."""
    return ",".join(
        f"PERFANCHOR-{index}-0-0" for index in range(max(0, tenant_count))
    )


def _build_seed_warmup_command(
    args: argparse.Namespace,
    config_path: Path,
    output: Path,
    tenant_count: int,
    seed_commit_timeout_s: float | None = None,
) -> list[str]:
    """Build one real-model seed run shared by quick scheduler cases."""
    effective_seed_timeout = (
        float(seed_commit_timeout_s)
        if seed_commit_timeout_s is not None
        else max(180.0, float(getattr(args, "commit_timeout_s", 0.0) or 0.0))
    )
    command = [
        sys.executable,
        str(RUNNER),
        "--echomem-url",
        args.base_url,
        "--tenants",
        str(tenant_count),
        "--duration-s",
        "1",
        "--concurrency-steps",
        "1",
        "--out-dir",
        str(output),
        "--seed-sessions-per-tenant",
        "1",
        "--messages-per-session",
        "3",
        "--commit-poll-timeout-s",
        str(effective_seed_timeout),
        "--commit-retry-max",
        str(args.commit_max_attempts),
        "--commit-retry-backoff-s",
        str(args.commit_retry_backoff_s),
        "--seed-concurrency",
        str(getattr(args, "seed_concurrency", 2)),
        "--tenant-config",
        str(config_path),
        "--scenarios",
        "K",
    ]
    if args.preflight_config:
        command += ["--preflight-config", args.preflight_config]
    if args.no_server_metrics:
        command += ["--no-metrics"]
    return command


def _usable_tenants(
    tenants: list[dict[str, Any]],
    auth_preflight: dict[str, Any],
) -> list[dict[str, Any]]:
    """Keep only credentials that passed the real HTTP preflight."""
    usable_ids = {
        str(item).strip()
        for item in auth_preflight.get("usable_tenant_ids") or []
        if str(item).strip()
    }
    return [
        tenant for tenant in tenants
        if str(
            tenant.get("tenant_id")
            or tenant.get("id")
            or tenant.get("user_id")
            or ""
        ).strip() in usable_ids
    ]


def _identity_is_independent(tenants: list[dict[str, Any]]) -> bool:
    """所有租户都能解析出非空 auth_key 且彼此不同，才算独立认证。"""
    keys: list[str] = []
    for tenant in tenants:
        key = str(tenant.get("auth_key") or "").strip()
        if not key:
            env_name = str(tenant.get("auth_key_env") or "").strip()
            key = os.environ.get(env_name, "").strip() if env_name else ""
        if not key:
            return False
        keys.append(key)
    return len(set(keys)) == len(keys)


def _auth_mode_validation_error(
    *, local_auth: bool, required_tenants: int
) -> str | None:
    """Reject a shared local identity before launching any child runner."""
    if local_auth and required_tenants > 1:
        return (
            "选定场景需要多租户，但 --local-auth 只提供一个本地身份；"
            "请使用可用的独立 tenant-config，或仅选择 tenants=1 的场景"
        )
    return None


def run_case_process(
    command: list[str],
    *,
    timeout_s: float,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run one case with a bounded wall-clock budget.

    A runner can contain its own per-request retries, so limiting only the
    workload duration does not bound the total case duration.  Start a new
    process group so a timed-out barrier cannot leave worker children behind.
    """
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=max(1.0, timeout_s))
        return (
            subprocess.CompletedProcess(
                command, process.returncode, stdout, stderr
            ),
            False,
        )
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
        stdout = stdout or ""
        stderr = stderr or ""
        stderr += (
            f"\nformal_suite: case wall-clock timeout after "
            f"{timeout_s:.1f}s\n"
        )
        if exc.stderr:
            stderr = f"{exc.stderr}\n{stderr}"
        return (
            subprocess.CompletedProcess(command, 124, stdout, stderr),
            True,
        )


def wait_for_service(
    base_url: str,
    *,
    timeout_s: float,
    poll_s: float = 2.0,
) -> tuple[bool, str]:
    """Wait for the target to become healthy before starting a case.

    A previous case may intentionally restart the service (for example after
    a burst or a deployment supervisor action).  Launching the next runner
    immediately turns that expected recovery window into a misleading
    harness/environment failure, so the suite records the wait separately.
    """
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_error = ""
    while True:
        try:
            request = urllib.request.Request(
                base_url.rstrip("/") + "/health",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5.0) as response:
                if 200 <= response.status < 300:
                    return True, ""
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            return False, last_error
        time.sleep(max(0.1, poll_s))


def _remaining_budget(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _budget_timeout(
    requested_s: float,
    deadline: float | None,
    *,
    minimum_s: float = 0.1,
) -> float:
    requested = max(0.0, float(requested_s))
    remaining = _remaining_budget(deadline)
    if remaining is None:
        return max(minimum_s, requested)
    return max(0.0, min(requested, remaining))


def _budget_exhausted_run(
    scenario: str,
    repetition: int,
    policy: str,
    case: dict[str, Any],
    output: Path,
    max_wall_clock_s: float,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    reason = "测试平台总 wall-clock 预算已耗尽，未启动该场景；这不是 EchoMem 业务失败"
    summary = {
        "status": "TIMEOUT",
        "metrics": {},
        "details": {
            "owner": "测试平台",
            "reason": reason,
            "max_wall_clock_s": max_wall_clock_s,
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "scenario": scenario,
        "scenario_label": case["label"],
        "scenario_config": {
            "tenant_count": int(case.get("tenants") or 1),
            "capacity_active_users": case.get("capacity_active_users"),
            "active_sessions_per_tenant": int(
                case.get("active_sessions_per_tenant") or 1
            ),
        },
        "repetition": repetition,
        "policy": policy,
        "status": "TIMEOUT",
        "runner_returncode": 124,
        "duration_s": 0.0,
        "case_timeout_s": 0.0,
        "runner_timeout": True,
        "output_dir": str(output.resolve()),
        "summary": summary,
        "failure_evidence": {"phase": "suite_deadline", "reason": reason},
    }


def _read_requests_csv(path: Path) -> list[dict[str, str]]:
    """读取逐请求 CSV；文件缺失时返回空列表。"""
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _submitted_operations(run: dict[str, Any]) -> int:
    """Count operations with real HTTP request records in a case summary."""
    summary = run.get("summary")
    if not isinstance(summary, dict):
        return 0
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    total = 0
    for operation in ("search", "commit"):
        item = metrics.get(operation)
        if not isinstance(item, dict):
            continue
        try:
            total += max(0, int(item.get("submitted") or 0))
        except (TypeError, ValueError):
            continue
    return total


def _resolve_run_dir(run_dir: Path) -> Path:
    """定位 run_stress 实际产物目录。

    run_stress 会在 ``--out-dir`` 下再创建时间戳子目录，产物实际落在该
    子目录内；直接构造的产物（如测试夹具）则落在 ``run_dir`` 自身。两者
    都能被解析到同一份 summary.json 所在目录。
    """
    if (run_dir / "summary.json").is_file():
        return run_dir
    if not run_dir.is_dir():
        # A runner can fail before creating its output directory. Keep the
        # case auditable and let the caller record NO_SUMMARY/ENV_ERROR
        # instead of aborting the whole multi-case suite.
        return run_dir
    children = [
        child for child in run_dir.iterdir()
        if child.is_dir() and (child / "summary.json").is_file()
    ]
    if len(children) == 1:
        return children[0]
    if len(children) > 1:
        # A resumed or externally copied runner can leave more than one
        # timestamp directory. Pick the newest completed summary instead of
        # silently treating a valid run as NO_SUMMARY.
        return max(
            children,
            key=lambda child: (child / "summary.json").stat().st_mtime,
        )
    return run_dir


def _ms_to_s(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number / 1000.0


def _bounded_label_violations(metrics_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """从 lane 指标样本提取违反 bounded-label 契约的 tenant 标签。"""
    violations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in metrics_rows:
        metric = str(row.get("metric") or "")
        if not metric.startswith("echomem_lane_"):
            continue
        try:
            labels = json.loads(row.get("labels") or "{}")
        except json.JSONDecodeError:
            labels = {}
        if not isinstance(labels, dict):
            continue
        for label_key in ("tenant_id", "tenant"):
            value = labels.get(label_key)
            if value is None:
                continue
            key = (metric, label_key, str(value))
            if key not in seen:
                seen.add(key)
                violations.append(
                    {"metric": metric, "label": label_key, "value": str(value)}
                )
            break
        if len(violations) >= 5:
            break
    return violations


def _scale_explicit_tenant_counts(
    counts: list[int],
    total_cap: int,
) -> list[int]:
    """Scale an explicit distribution without exceeding a bounded barrier."""
    if total_cap <= 0 or sum(counts) <= total_cap:
        return counts
    if not counts:
        return []
    if total_cap < len(counts):
        return [1 if index < total_cap else 0 for index in range(len(counts))]
    total = sum(counts)
    scaled = [max(1, (count * total_cap) // total) for count in counts]
    while sum(scaled) > total_cap:
        index = max(
            (idx for idx, value in enumerate(scaled) if value > 1),
            key=lambda idx: (scaled[idx], -idx),
            default=None,
        )
        if index is None:
            break
        scaled[index] -= 1
    fractions = [
        (count * total_cap / total) - ((count * total_cap) // total)
        for count in counts
    ]
    while sum(scaled) < total_cap:
        index = max(range(len(counts)), key=lambda idx: (fractions[idx], -idx))
        scaled[index] += 1
        fractions[index] = -1.0
    return scaled


def _build_case_command(
    args: argparse.Namespace,
    case: dict[str, Any],
    config_path: Path,
    output: Path,
    duration_s: float,
    barrier_count_cap: int = 0,
) -> list[str]:
    """把 stress case 字典映射为 run_stress CLI 参数。

    barrier 场景按「洪峰窗口 / 多波 / 其余分布」映射到 D / H / S；无 barrier
    的定速率场景映射到 K。``blackbox_search_priority`` 等仅记录在 manifest
    的字段不映射 CLI。
    """
    per_tenant_conc = int(case.get("per_tenant_concurrency") or 1)
    # Barrier 场景会在 run_stress 内部另外准备精确数量的未提交会话。
    # ``sessions_per_tenant`` 只用于 warm-up；将它设置成 barrier 总数会在
    # 正式压测前额外提交数百个真实模型请求，并可能耗尽 case timeout。
    seed_sessions = int(
        getattr(args, "seed_sessions_per_tenant", None)
        if getattr(args, "seed_sessions_per_tenant", None) is not None
        else case.get("sessions_per_tenant", 5)
    )
    if getattr(args, "quick_mode", False):
        # Quick mode still needs real memory for hot-cache/search evidence,
        # but repeating the full seed matrix makes every case spend most of
        # its wall clock waiting for model-backed Commit extraction. One
        # warm-up session is enough to establish a non-empty tenant while
        # keeping the measured workload inside the case timeout.
        seed_sessions = min(seed_sessions, 1)
    elif case.get("commit_barrier"):
        seed_sessions = min(seed_sessions, 4)
    cmd = [
        sys.executable,
        str(RUNNER),
        "--echomem-url",
        args.base_url,
        "--tenants",
        str(case["tenants"]),
        "--duration-s",
        str(duration_s),
        "--concurrency-steps",
        str(per_tenant_conc),
        "--out-dir",
        str(output / "run"),
        "--auth-header",
        str(getattr(args, "auth_header", "X-Auth-Key")),
        "--seed-sessions-per-tenant",
        str(seed_sessions),
        "--active-sessions-per-tenant",
        str(
            int(
                getattr(args, "active_sessions_per_tenant", None)
                or case.get("active_sessions_per_tenant")
                or 1
            )
        ),
        "--messages-per-session",
        str(case.get("messages_per_session", 10)),
        "--commit-poll-timeout-s",
        str(args.commit_timeout_s),
        "--commit-retry-max",
        str(args.commit_max_attempts),
        "--commit-retry-backoff-s",
        str(args.commit_retry_backoff_s),
        "--barrier-prepare-concurrency",
        "4",
        "--barrier-wave-size",
        str(getattr(args, "barrier_wave_size", 32)),
        "--barrier-drain-timeout-s",
        str(
            (
                case.get(
                    "quick_barrier_drain_timeout_s",
                    getattr(args, "barrier_drain_timeout_s", 10.0),
                )
                if getattr(args, "quick_mode", False)
                else getattr(args, "barrier_drain_timeout_s", 10.0)
            )
        ),
    ]
    if getattr(args, "local_auth_mode", False):
        # EchoMem local auth resolves the configured default identity when no
        # X-Auth-Key is sent. The local workspace has no key registry, so
        # passing a synthetic tenant-config key would make every request 401.
        cmd += [
            "--auth-mode",
            "static",
            "--tenant-id",
            str(getattr(args, "local_tenant_id", "local")),
            "--user-id",
            str(getattr(args, "local_user_id", "local_user")),
        ]
    else:
        cmd += ["--tenant-config", str(config_path)]
    if getattr(args, "skip_seed", False) or getattr(args, "reuse_existing_data", False):
        cmd += ["--skip-seed"]
        # Keep real Search load running when seed evidence is unavailable.
        # run_stress records hot-memory evidence as INCONCLUSIVE.
        cmd += ["--allow-unverified-search"]
    if case.get("search_rps"):
        cmd += ["--mode", "fixed-rps", "--rps", str(case["search_rps"])]
    if case.get("search_rps_per_tenant"):
        cmd += [
            "--mode",
            "fixed-rps",
            "--per-tenant-rps",
            str(case["search_rps_per_tenant"]),
        ]
    commit_rpm = case.get("commit_rpm")
    if getattr(args, "quick_mode", False) and "quick_commit_rpm" in case:
        commit_rpm = case["quick_commit_rpm"]
    # ``0`` is meaningful in capacity cases: it disables background
    # Commit generation so the capacity probe measures Search only. Passing
    # no flag would make run_stress fall back to its non-zero default.
    if commit_rpm is not None:
        cmd += ["--commit-rpm", str(commit_rpm)]
    if case.get("commit_rpm_per_tenant"):
        cmd += [
            "--per-tenant-commit-rpm",
            str(case["commit_rpm_per_tenant"]),
        ]
    if args.preflight_config:
        cmd += ["--preflight-config", args.preflight_config]
    if getattr(args, "search_queries", ""):
        cmd += ["--search-queries", str(args.search_queries)]
    if case.get("search_query_profile"):
        cmd += ["--search-query-profile", str(case["search_query_profile"])]
    if args.no_server_metrics:
        cmd += ["--no-metrics"]
    if case.get("commit_barrier"):
        barrier_count = int(case.get("commit_barrier_count", 32))
        effective_barrier_cap = barrier_count_cap
        if getattr(args, "quick_mode", False):
            scenario_cap = int(case.get("quick_barrier_count_cap") or 0)
            if scenario_cap > 0:
                effective_barrier_cap = min(
                    effective_barrier_cap or scenario_cap,
                    scenario_cap,
                )
        if effective_barrier_cap > 0:
            barrier_count = min(barrier_count, effective_barrier_cap)
            # A bounded fairness sample must touch every selected tenant.
            # Merely truncating a uniform barrier can leave tail tenants with
            # zero Commit completions and turn sample-size reduction into a
            # false fairness failure.
            if (
                case.get("fairness_bounded")
                and case.get("commit_tenant_distribution") == "uniform"
            ):
                tenant_count = max(1, int(case.get("tenants") or 1))
                barrier_count = max(
                    barrier_count,
                    min(
                        tenant_count,
                        int(case.get("commit_barrier_count", 0)),
                    )
                )
        # Explicit tenant distributions are a single barrier with a custom
        # tenant allocation. Keep it on S so run_stress receives the explicit
        # counts; H is reserved for multi-wave barriers.
        if case.get("commit_tenant_distribution") == "explicit":
            cmd += [
                "--scenarios", "S",
                "--commit-barrier",
                "--commit-barrier-count", str(barrier_count),
                "--commit-tenant-distribution", "explicit",
                "--commit-tenant-counts",
                ",".join(
                    map(
                        str,
                        _scale_explicit_tenant_counts(
                            list(map(int, case.get("commit_tenant_counts") or [])),
                            effective_barrier_cap,
                        ),
                    )
                ),
                "--commit-barrier-waves",
                str(case.get("commit_barrier_waves", 1) or 1),
                "--commit-barrier-cooldown-s",
                str(case.get("commit_barrier_cooldown_s", 0.0)),
            ]
        # 洪峰窗口（report6 D：waves 为 1 且存在 burst 窗口）→ D 场景。
        elif case.get("commit_burst_window_s") and not (case.get("commit_barrier_waves") or 1) > 1:
            cmd += [
                "--scenarios", "D",
                "--burst-commits", str(barrier_count),
                "--burst-window-s", str(case["commit_burst_window_s"]),
            ]
        # 多波（report4 D：waves > 1）→ H 场景。
        elif (case.get("commit_barrier_waves") or 1) > 1:
            cmd += [
                "--scenarios", "H", "--commit-barrier",
                "--commit-barrier-count", str(barrier_count),
                "--commit-barrier-waves", str(case["commit_barrier_waves"]),
                "--commit-barrier-cooldown-s", str(case.get("commit_barrier_cooldown_s", 0.0)),
            ]
        # 其余 barrier（并发读 + 一次性 barrier）→ S 场景。
        else:
            cmd += [
                "--scenarios", "S", "--commit-barrier",
                "--commit-barrier-count", str(barrier_count),
                "--commit-tenant-distribution", str(case.get("commit_tenant_distribution", "uniform")),
            ]
            if case.get("commit_zipf_exponent"):
                cmd += ["--commit-zipf-exponent", str(case["commit_zipf_exponent"])]
            if case.get("commit_tenant_counts"):
                counts = list(map(int, case["commit_tenant_counts"]))
                if effective_barrier_cap > 0:
                    counts = _scale_explicit_tenant_counts(
                        counts, effective_barrier_cap
                    )
                cmd += ["--commit-tenant-counts", ",".join(map(str, counts))]
    else:
        cmd += ["--scenarios", "K"]
    return cmd


def _derive_case_summary(run_dir: Path, identity_independent: bool) -> dict[str, Any]:
    """把 run_stress 原生产物推导成 stress 契约摘要。

    契约字段（metrics.search / metrics.commit / metrics.fairness /
    metrics.per_tenant / details.*）供 acceptance 求值器与 data report
    消费；run_stress 原始 summary.json / requests.csv 保留在 run 目录内不动。
    """
    run_dir = _resolve_run_dir(run_dir)
    summary_path = run_dir / "summary.json"
    native: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            native = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            native = {}
    rows = _read_requests_csv(run_dir / "requests.csv")
    reads = [row for row in rows if row.get("op") == "read"]
    ok_reads = [row for row in reads if row.get("status") == "ok"]
    commit_submits = [row for row in rows if row.get("op") == "commit_submit"]
    ok_commits = [row for row in commit_submits if row.get("status") == "ok"]
    commit_dones = [row for row in rows if row.get("op") == "commit_done"]
    ok_dones = [row for row in commit_dones if row.get("status") == "ok"]
    timeout_dones = [
        row
        for row in commit_dones
        if row.get("status") == "error"
        and row.get("error_type") == "commit_timeout"
    ]
    fail_dones = [
        row
        for row in commit_dones
        if row.get("status") == "error"
        and row.get("error_type") != "commit_timeout"
    ]

    read_latencies = [
        value for value in (_ms_to_s(row.get("stage_ms")) for row in ok_reads)
        if value is not None
    ]
    search_quality = native.get("search_quality") or {}
    native_durability = native.get("commit_durability") or {}
    native_fairness = native.get("tenant_fairness") or {}

    submitted = len(reads)
    succeeded = len(ok_reads)
    commit_submitted = len(commit_submits)
    completed = len(ok_dones)
    failed = len(fail_dones)
    commit_success_rate = native_durability.get("commit_success_rate")
    if commit_success_rate is None:
        commit_success_rate = completed / commit_submitted if commit_submitted else None

    # A tenant is not a user. Count distinct session identities from measured
    # requests and expose a separate hot-user proxy for capacity reporting.
    session_ops: Counter[tuple[str, str]] = Counter()
    sessions_by_tenant: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        tenant = str(row.get("tenant_idx") or "").strip()
        session = str(row.get("session_id") or "").strip()
        if not tenant or not session:
            continue
        session_ops[(tenant, session)] += 1
        sessions_by_tenant[tenant].add(session)
    active_users_by_tenant = {
        tenant: len(sessions)
        for tenant, sessions in sorted(sessions_by_tenant.items())
    }
    hot_user_identity, hot_user_requests = (
        max(session_ops.items(), key=lambda item: (item[1], item[0]))
        if session_ops
        else (None, 0)
    )

    completed_by_tenant: dict[str, int] = {}
    for row in ok_dones:
        tenant_idx = str(row.get("tenant_idx") or "")
        if tenant_idx:
            completed_by_tenant[tenant_idx] = completed_by_tenant.get(tenant_idx, 0) + 1

    per_tenant: dict[str, dict[str, Any]] = {}
    for tenant_idx in sorted({str(row.get("tenant_idx") or "") for row in rows}):
        if not tenant_idx:
            continue
        tenant_reads = [
            value for value in (
                _ms_to_s(row.get("stage_ms"))
                for row in reads
                if str(row.get("tenant_idx") or "") == tenant_idx
                and row.get("status") == "ok"
            )
            if value is not None
        ]
        tenant_ok_commits = [
            row for row in ok_commits if str(row.get("tenant_idx") or "") == tenant_idx
        ]
        tenant_done_stages = [
            value for value in (
                _ms_to_s(row.get("stage_ms"))
                for row in ok_dones
                if str(row.get("tenant_idx") or "") == tenant_idx
            )
            if value is not None
        ]
        if not tenant_ok_commits and not tenant_done_stages and not tenant_reads:
            continue
        commit_entry: dict[str, Any] = {}
        if tenant_ok_commits:
            commit_entry["submitted"] = len(tenant_ok_commits)
            commit_entry["completed"] = sum(
                1
                for row in ok_dones
                if str(row.get("tenant_idx") or "") == tenant_idx
            )
        if tenant_done_stages:
            commit_entry["completion"] = {
                "p50_s": round(percentile(tenant_done_stages, 50), 3)
            }
        search_entry: dict[str, Any] = {}
        if tenant_reads:
            search_entry = {
                "submitted": sum(
                    1
                    for row in reads
                    if str(row.get("tenant_idx") or "") == tenant_idx
                ),
                "succeeded": len(tenant_reads),
                "latency": {
                    "p50_s": round(percentile(tenant_reads, 50), 3),
                    "p95_s": round(percentile(tenant_reads, 95), 3),
                },
            }
        per_tenant[tenant_idx] = {
            "commit": commit_entry,
            "search": search_entry,
        }

    details: dict[str, Any] = {
        "identity_mode": "independent_auth_keys" if identity_independent else "shared",
        "quality_seed": [],
        "native_status": native.get("status"),
        "search_evidence_status": (
            (native.get("data_scale") or {}).get("search_evidence_status")
            if isinstance(native.get("data_scale"), dict)
            else None
        ),
        "query_profile": (
            (native.get("data_scale") or {}).get("query_profile")
            if isinstance(native.get("data_scale"), dict)
            else None
        ),
        "user_activity": {
            "active_user_count": sum(active_users_by_tenant.values()),
            "active_users_by_tenant": active_users_by_tenant,
            "hot_user_proxy": {
                "tenant_idx": hot_user_identity[0] if hot_user_identity else None,
                "session_id": hot_user_identity[1] if hot_user_identity else None,
                "request_count": hot_user_requests,
            },
            "definition": (
                "active user = distinct (tenant_idx, session_id) observed in this run; "
                "hot user proxy = maximum measured operations for one such session"
            ),
        },
    }
    # A priority result is only valid when Search and Commit shared a real
    # wall-clock window. Preserve that proof in the derived summary instead
    # of letting the acceptance layer infer it from a scenario name.
    def _timestamp_ms(row: dict[str, str]) -> float | None:
        try:
            value = float(row.get("ts_ms") or "")
        except (TypeError, ValueError):
            return None
        return value if value == value else None

    def _request_interval(row: dict[str, str]) -> tuple[float, float] | None:
        end = _timestamp_ms(row)
        if end is None:
            return None
        try:
            start = float(row.get("start_ts_ms") or "")
        except (TypeError, ValueError):
            start = end - max(0.0, float(row.get("stage_ms") or 0.0))
        if start != start:
            return None
        return min(start, end), max(start, end)

    search_intervals = [
        interval for interval in (_request_interval(row) for row in reads)
        if interval is not None
    ]
    commit_intervals = [
        interval
        for interval in (_request_interval(row) for row in commit_submits)
        if interval is not None
    ]
    if search_intervals and commit_intervals:
        search_start = min(interval[0] for interval in search_intervals)
        search_end = max(interval[1] for interval in search_intervals)
        commit_start = min(interval[0] for interval in commit_intervals)
        commit_end = max(interval[1] for interval in commit_intervals)
        overlap_ms = max(0.0, min(search_end, commit_end) - max(search_start, commit_start))
        details["same_window_overlap"] = {
            "search_window_ms": [search_start, search_end],
            "commit_submit_window_ms": [commit_start, commit_end],
            "overlap_ms": round(overlap_ms, 3),
            "overlap_proven": overlap_ms > 0,
            "basis": "request start/end intervals",
        }
    for key in (
        "degradation",
        "isolation",
        "search_quality",
        "commit_durability",
        "reconciliation",
        "resources",
    ):
        if isinstance(native.get(key), dict):
            details[key] = native[key]
    metrics_path = run_dir / "metrics_samples.csv"
    if metrics_path.is_file():
        metrics_rows = _read_requests_csv(metrics_path)
        if metrics_rows:
            families = (
                tuple(PR421_ACCEPTANCE_TARGETS["lane_metric_families"])
                + tuple(PR421_ACCEPTANCE_TARGETS["fanout_metric_families"])
            )
            observed = {
                str(row.get("metric") or "")
                for row in metrics_rows
                if row.get("metric")
            }
            # Prometheus histograms are exported as *_bucket, *_count and
            # *_sum samples. Treat any of those samples as evidence that the
            # corresponding metric family exists.
            for family in families:
                if any(
                    name == family
                    or name.startswith(f"{family}_")
                    for name in observed
                ):
                    observed.add(family)
            details["pr421_metric_coverage"] = {
                "present": {family: True for family in families if family in observed},
                "missing": [family for family in families if family not in observed],
                "bounded_label_violations": _bounded_label_violations(metrics_rows),
            }
            # PR421 deliberately forbids tenant/request labels on scheduler
            # metrics. Build coverage by the bounded ``lane`` label instead
            # of requiring a per-tenant quartet.
            lane_quartets: dict[str, dict[str, bool]] = {}
            fanout_engines: dict[str, dict[str, bool]] = {}
            for row in metrics_rows:
                metric_name = str(row.get("metric") or "")
                family = next(
                    (
                        candidate
                        for candidate in families
                        if metric_name == candidate
                        or metric_name.startswith(f"{candidate}_")
                    ),
                    None,
                )
                if family is None:
                    continue
                try:
                    labels = json.loads(row.get("labels") or "{}")
                except json.JSONDecodeError:
                    labels = {}
                if not isinstance(labels, dict):
                    continue
                if family in PR421_ACCEPTANCE_TARGETS["lane_metric_families"]:
                    lane = labels.get("lane")
                    if lane in (None, ""):
                        continue
                    short = {
                        "echomem_lane_queued": "queued",
                        "echomem_lane_wait_seconds": "wait",
                        "echomem_lane_exec_seconds": "exec",
                        "echomem_lane_rejected_total": "rejected",
                    }[family]
                    lane_quartets.setdefault(
                        str(lane),
                        {
                            "queued": False,
                            "wait": False,
                            "exec": False,
                            "rejected": False,
                        },
                    )[short] = True
                elif family in PR421_ACCEPTANCE_TARGETS["fanout_metric_families"]:
                    engine = labels.get("engine")
                    if engine in (None, ""):
                        continue
                    short = (
                        "exec"
                        if family == "echomem_engine_fanout_exec_seconds"
                        else "skipped"
                    )
                    fanout_engines.setdefault(
                        str(engine), {"exec": False, "skipped": False}
                    )[short] = True
            details["pr421_metric_coverage"]["lane_quartets"] = lane_quartets
            details["pr421_metric_coverage"]["fanout_engines"] = fanout_engines

    def _percentile(values: list[float], p: float) -> float | None:
        value = percentile(values, p)
        return round(value, 3) if value is not None else None

    return {
        "status": "completed" if str(native.get("status") or "") == "completed" else "NO_SUMMARY",
        "metrics": {
            "search": {
                "submitted": submitted,
                "succeeded": succeeded,
                "errors": submitted - succeeded,
                "success_rate": (succeeded / submitted) if submitted else None,
                "latency": {
                    "mean_s": (
                        round(statistics.mean(read_latencies), 3) if read_latencies else None
                    ),
                    "p50_s": _percentile(read_latencies, 50),
                    "p95_s": _percentile(read_latencies, 95),
                    "p99_s": _percentile(read_latencies, 99),
                },
                "rate_limited_count": sum(
                    1
                    for row in reads
                    if str(row.get("http_status") or "").strip() == "429"
                ),
                "quality_asserted": int(search_quality.get("anchor_total") or 0),
                "quality_failures": int(search_quality.get("quality_failures") or 0),
            },
            "commit": {
                "submitted": commit_submitted,
                "completed": completed,
                "failed": failed,
                "timeout": len(timeout_dones),
                "success_rate": commit_success_rate,
                "rate_limited_count": sum(
                    1
                    for row in commit_submits
                    if str(row.get("http_status") or "").strip() == "429"
                ),
            },
            "fairness": {
                "commit_completed_per_tenant": completed_by_tenant,
                "by_scene": {
                    str(scene): {
                        "commit_throughput_per_tenant": value.get(
                            "commit_throughput_per_tenant", {}
                        ),
                        "commit_throughput_jain": value.get(
                            "commit_throughput_jain"
                        ),
                        "search_latency_utility_per_tenant": value.get(
                            "search_latency_utility_per_tenant", {}
                        ),
                        "search_latency_utility_jain": value.get(
                            "search_latency_utility_jain"
                        ),
                        "search_p95_max_min_ratio": value.get(
                            "p95_max_min_ratio"
                        ),
                    }
                    for scene, value in native_fairness.items()
                    if isinstance(value, dict)
                },
            },
            "per_tenant": per_tenant,
        },
        "details": details,
        "parameters": {
            "commit_delay_threshold_s": 10.0,
            "search_delay_threshold_s": 2.5,
        },
    }


def _write_case_csvs(output: Path, rows: list[dict[str, str]]) -> None:
    """把 run_stress 逐请求记录归一化为套件契约的两个 CSV。"""
    done_by_session: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("op") == "commit_done" and row.get("session_id"):
            done_by_session.setdefault(row["session_id"], row)

    commit_rows: list[dict[str, str]] = []
    for row in rows:
        if row.get("op") != "commit_submit":
            continue
        session_id = row.get("session_id") or ""
        done = done_by_session.get(session_id)
        if done is not None and done.get("status") == "ok":
            status = "completed"
            end_to_end = _ms_to_s(done.get("stage_ms"))
        elif done is not None and done.get("error_type") == "commit_timeout":
            status = "timeout"
            end_to_end = _ms_to_s(done.get("stage_ms"))
        elif done is not None:
            status = "failed"
            end_to_end = _ms_to_s(done.get("stage_ms"))
        else:
            status = "submitted"
            end_to_end = _ms_to_s(row.get("stage_ms"))
        commit_rows.append(
            {
                "tenant": row.get("tenant_idx") or "",
                "session_id": session_id,
                "archive_id": row.get("archive_id") or "",
                "status": status,
                "end_to_end_s": f"{end_to_end:.3f}" if end_to_end is not None else "",
                "queue_wait_s": "",
                "admission_wait_s": "",
                "admission_queue_depth": "",
                # Legacy result bundles predate per-request correlation.
                # Retain the session id only as an explicit fallback so their
                # CSV remains joinable; new runner output always uses the
                # true HTTP X-Request-ID.
                "request_id": row.get("request_id") or session_id,
                "http_status": row.get("http_status") or "",
                "error_type": row.get("error_type") or "",
                "retry_count": row.get("retry_count") or "0",
                "retried": row.get("retried") or "false",
                "retry_total_wait_ms": row.get("retry_total_wait_ms") or "0",
                "retry_after_s": row.get("retry_after_s") or "",
                "reason_code": row.get("reason_code") or "",
            }
        )
    with (output / "commit_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tenant", "session_id", "status", "end_to_end_s",
                "archive_id",
                "queue_wait_s", "admission_wait_s", "admission_queue_depth", "request_id",
                "http_status", "error_type", "retry_count", "retried",
                "retry_total_wait_ms", "retry_after_s", "reason_code",
            ],
        )
        writer.writeheader()
        writer.writerows(commit_rows)

    search_rows: list[dict[str, str]] = []
    for row in rows:
        if row.get("op") != "read":
            continue
        if row.get("status") == "ok":
            status_code = "200"
        else:
            # Preserve the actual HTTP code. A transport timeout/connection
            # failure is intentionally left as 0, not mislabeled as 429/500.
            raw_status = str(row.get("http_status") or "").strip()
            status_code = raw_status if raw_status.isdigit() else "0"
        service_s = _ms_to_s(row.get("stage_ms"))
        search_rows.append(
            {
                "tenant": row.get("tenant_idx") or "",
                "session_id": row.get("session_id") or "",
                "status_code": status_code,
                "service_s": f"{service_s:.3f}" if service_s is not None else "",
                "end_to_end_s": f"{service_s:.3f}" if service_s is not None else "",
                "queue_wait_s": "",
                "request_id": row.get("request_id") or row.get("session_id") or "",
                "error_type": row.get("error_type") or "",
                "error_class": row.get("error_class") or "",
                "error_detail": row.get("error_detail") or "",
                "retry_after_s": row.get("retry_after_s") or "",
                "reason_code": row.get("reason_code") or "",
                "query_kind": row.get("query_kind") or "",
                "query": row.get("query") or "",
                "hit_count": row.get("hit_count") or "0",
                "real_recall": row.get("real_recall") or "false",
                "degraded": row.get("degraded") or "false",
                "quality_ok": row.get("quality_ok") or "true",
            }
        )
    with (output / "search_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tenant", "session_id", "status_code", "service_s",
                "end_to_end_s", "queue_wait_s", "request_id",
                "error_type", "error_class", "error_detail",
                "retry_after_s", "reason_code", "query_kind", "query",
                "hit_count", "real_recall", "degraded", "quality_ok",
            ],
        )
        writer.writeheader()
        writer.writerows(search_rows)


def _preserve_run_artifacts(output: Path, run_dir: Path) -> None:
    """Copy raw request and Prometheus timelines beside normalized artifacts."""
    for filename in ("metrics_samples.csv", "requests.csv"):
        source = run_dir / filename
        if source.is_file():
            shutil.copyfile(source, output / filename)


def run_case(
    runner: Path,
    case_root: Path,
    scenario: str,
    repetition: int,
    policy: str,
    config_path: Path,
    args: argparse.Namespace,
    case: dict[str, Any],
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    started_at = now_iso()
    output = case_root / scenario / f"repeat-{repetition:02d}" / policy
    output.mkdir(parents=True, exist_ok=True)
    recovery_timeout_s = float(
        getattr(args, "inter_case_recovery_timeout_s", 0.0) or 0.0
    )
    if recovery_timeout_s > 0:
        recovery_timeout_s = _budget_timeout(
            recovery_timeout_s,
            deadline,
            minimum_s=0.0,
        )
        if recovery_timeout_s <= 0:
            return _budget_exhausted_run(
                scenario,
                repetition,
                policy,
                case,
                output,
                float(getattr(args, "max_wall_clock_s", 0.0) or 0.0),
            )
        healthy, health_error = wait_for_service(
            args.base_url,
            timeout_s=recovery_timeout_s,
            poll_s=float(getattr(args, "health_poll_s", 2.0) or 2.0),
        )
        (output / "pre_case_health.json").write_text(
            json.dumps(
                {
                    "healthy": healthy,
                    "timeout_s": recovery_timeout_s,
                    "error": health_error,
                    "checked_at": now_iso(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not healthy:
            return {
                "scenario": scenario,
                "scenario_label": case["label"],
                "repetition": repetition,
                "policy": policy,
                "status": "ENV_ERROR",
                "started_at": started_at,
                "finished_at": now_iso(),
                "runner_returncode": 125,
                "duration_s": float(case["duration_s"]),
                "case_timeout_s": float(args.case_timeout_s or 0),
                "output_dir": str(output.resolve()),
                "summary": {},
                "failure_evidence": {
                    "phase": "pre_case_health",
                    "error": health_error,
                    "recovery_timeout_s": recovery_timeout_s,
                },
            }
    duration_s = case["duration_s"]
    if args.duration_cap_s > 0:
        duration_s = min(float(duration_s), args.duration_cap_s)
    case_timeout_s = (
        args.case_timeout_s
        if args.case_timeout_s > 0
        else duration_s + max(
            60.0,
            float(args.commit_timeout_s),
            120.0 if case.get("commit_barrier") and not getattr(args, "barrier_count_cap", 0) else 0.0,
        )
    )
    case_timeout_s = _budget_timeout(case_timeout_s, deadline, minimum_s=0.0)
    if case_timeout_s <= 0:
        return _budget_exhausted_run(
            scenario,
            repetition,
            policy,
            case,
            output,
            float(getattr(args, "max_wall_clock_s", 0.0) or 0.0),
        )
    barrier_count_cap = int(getattr(args, "barrier_count_cap", 0) or 0)
    command = _build_case_command(
        args, case, config_path, output, duration_s, barrier_count_cap
    )
    (output / "command.json").write_text(
        json.dumps(
            {
                "argv": command,
                "effective_auth_mode": (
                    "local_auth" if getattr(args, "local_auth_mode", False)
                    else "tenant_config"
                ),
                "tenant_config": str(config_path),
                "base_url": args.base_url,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.reset_command:
        try:
            completed_reset = subprocess.run(
                args.reset_command,
                shell=True,
                text=True,
                capture_output=True,
                timeout=_budget_timeout(case_timeout_s, deadline, minimum_s=0.1),
            )
        except subprocess.TimeoutExpired:
            return _budget_exhausted_run(
                scenario,
                repetition,
                policy,
                case,
                output,
                float(getattr(args, "max_wall_clock_s", 0.0) or 0.0),
            )
        (output / "reset.stdout.log").write_text(
            completed_reset.stdout, encoding="utf-8"
        )
        (output / "reset.stderr.log").write_text(
            completed_reset.stderr, encoding="utf-8"
        )
        if completed_reset.returncode != 0:
            return {
                "scenario": scenario,
                "scenario_label": case["label"],
                "repetition": repetition,
                "policy": policy,
                "status": "RESET_FAILED",
                "started_at": started_at,
                "finished_at": now_iso(),
                "runner_returncode": completed_reset.returncode,
                "duration_s": duration_s,
                "case_timeout_s": case_timeout_s,
                "barrier_count_cap": barrier_count_cap,
                "output_dir": str(output.resolve()),
                "summary": {},
            }
    completed, timed_out = run_case_process(
        command,
        timeout_s=_budget_timeout(case_timeout_s, deadline, minimum_s=0.1),
    )
    (output / "suite_runner.stdout.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    (output / "suite_runner.stderr.log").write_text(
        completed.stderr, encoding="utf-8"
    )
    run_dir = _resolve_run_dir(output / "run")
    rows = _read_requests_csv(run_dir / "requests.csv")
    _write_case_csvs(output, rows)
    _preserve_run_artifacts(output, run_dir)
    derived = _derive_case_summary(run_dir, args.identity_independent)
    if timed_out:
        derived["status"] = "TIMEOUT"
    elif completed.returncode != 0:
        derived["status"] = "NO_SUMMARY"
    if completed.returncode != 0:
        derived["failure_evidence"] = {
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-8000:],
            "rows_written": len(rows),
            "run_dir": str(run_dir.resolve()),
        }
    (output / "summary.json").write_text(
        json.dumps(derived, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "scenario": scenario,
        "scenario_label": case["label"],
        "repetition": repetition,
        "policy": policy,
        "status": (
            "TIMEOUT" if timed_out
            else "ENV_ERROR" if completed.returncode != 0 and not rows
            else "FAIL" if completed.returncode != 0
            else derived["status"]
        ),
        "runner_returncode": completed.returncode,
        "started_at": started_at,
        "finished_at": now_iso(),
        "duration_s": duration_s,
        "case_timeout_s": case_timeout_s,
        "barrier_count_cap": barrier_count_cap,
        "runner_timeout": timed_out,
        "output_dir": str(output.resolve()),
        "summary": derived,
    }


def _retryable_case_status(status: Any) -> bool:
    """Whether a case can be retried before the target produced real rows."""
    return str(status or "").upper() in {
        "ENV_ERROR",
        "NO_SUMMARY",
        "HARNESS_ERROR",
    }


def _archive_case_attempt(output: Path, attempt: int) -> Path | None:
    """Preserve a failed attempt before retrying the same case directory."""
    if not output.exists():
        return None
    archived = output.with_name(f"attempt-{attempt:02d}")
    if archived.exists():
        shutil.rmtree(archived)
    output.rename(archived)
    return archived


def fmt_seconds(value: Any) -> str:
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return "-"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def csv_values(path: Path, field: str) -> list[float]:
    if not path.is_file():
        return []
    values: list[float] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                value = float(row.get(field) or "")
            except (TypeError, ValueError):
                continue
            if value >= 0:
                values.append(value)
    return values


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def aggregate_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault(
            (str(run.get("scenario")), str(run.get("policy"))), []
        ).append(run)
    aggregates = []
    for (scenario, policy), group in sorted(groups.items()):
        commit_values: list[float] = []
        search_values: list[float] = []
        commit_submitted = commit_completed = commit_failed = 0
        search_submitted = search_succeeded = search_errors = 0
        commit_delayed = search_delayed = rate_limited = 0
        tenant_rows: dict[str, dict[str, list[float] | int]] = {}
        for run in group:
            summary = run.get("summary") or {}
            metrics = summary.get("metrics") or {}
            commit = metrics.get("commit") or {}
            search = metrics.get("search") or {}
            commit_submitted += int(commit.get("submitted") or 0)
            commit_completed += int(commit.get("completed") or 0)
            commit_failed += int(commit.get("failed") or 0)
            search_submitted += int(search.get("submitted") or 0)
            search_succeeded += int(search.get("succeeded") or 0)
            search_errors += int(search.get("errors") or 0)
            commit_delayed += int(commit.get("delayed_count") or 0)
            search_delayed += int(search.get("delayed_count") or 0)
            rate_limited += int(commit.get("rate_limited_count") or 0)
            rate_limited += int(search.get("rate_limited_count") or 0)
            out_dir = Path(run.get("output_dir", ""))
            commit_values.extend(csv_values(out_dir / "commit_results.csv", "end_to_end_s"))
            search_values.extend(csv_values(out_dir / "search_results.csv", "service_s"))
            # Per-run means are insufficient for a cross-run percentile. Use
            # raw request rows so a busy run cannot be underweighted.
            for row in read_rows(out_dir / "commit_results.csv"):
                tenant = str(row.get("tenant") or "-")
                target = tenant_rows.setdefault(
                    tenant,
                    {
                        "commit": [],
                        "search": [],
                        "commit_completed": 0,
                        "commit_submitted": 0,
                        "commit_delayed": 0,
                        "search_succeeded": 0,
                        "search_submitted": 0,
                        "search_delayed": 0,
                    },
                )
                target["commit_submitted"] += 1
                try:
                    commit_duration = float(
                        row.get("end_to_end_s") or row.get("elapsed_s") or 0
                    )
                except (TypeError, ValueError):
                    commit_duration = 0.0
                if str(row.get("status") or "") in {
                    "completed", "complete", "transcommit", "succeeded", "success"
                }:
                    target["commit"].append(commit_duration)
                    target["commit_completed"] += 1
                if commit_duration >= float(
                    (summary.get("parameters") or {}).get(
                        "commit_delay_threshold_s", 10.0
                    )
                ):
                    target["commit_delayed"] += 1
            for row in read_rows(out_dir / "search_results.csv"):
                tenant = str(row.get("tenant") or "-")
                target = tenant_rows.setdefault(
                    tenant,
                    {
                        "commit": [],
                        "search": [],
                        "commit_completed": 0,
                        "commit_submitted": 0,
                        "commit_delayed": 0,
                        "search_succeeded": 0,
                        "search_submitted": 0,
                        "search_delayed": 0,
                    },
                )
                target["search_submitted"] += 1
                try:
                    code = int(float(row.get("status_code") or 0))
                except (TypeError, ValueError):
                    code = 0
                if 200 <= code < 300:
                    target["search_succeeded"] += 1
                    try:
                        search_duration = float(
                            row.get("service_s") or row.get("elapsed_s") or 0
                        )
                        target["search"].append(search_duration)
                    except (TypeError, ValueError):
                        search_duration = 0.0
                    if search_duration >= float(
                        (summary.get("parameters") or {}).get(
                            "search_delay_threshold_s", 2.5
                        )
                    ):
                        target["search_delayed"] += 1
        aggregates.append(
            {
                "scenario": scenario,
                "policy": policy,
                "repetitions": len(group),
                "commit_submitted": commit_submitted,
                "commit_completed": commit_completed,
                "commit_failed": commit_failed,
                "commit_mean": statistics.mean(commit_values) if commit_values else None,
                "commit_p50": percentile(commit_values, 50),
                "commit_p90": percentile(commit_values, 90),
                "commit_p95": percentile(commit_values, 95),
                "commit_p99": percentile(commit_values, 99),
                "commit_max": max(commit_values) if commit_values else None,
                "search_submitted": search_submitted,
                "search_succeeded": search_succeeded,
                "search_errors": search_errors,
                "search_mean": statistics.mean(search_values) if search_values else None,
                "search_p50": percentile(search_values, 50),
                "search_p90": percentile(search_values, 90),
                "search_p95": percentile(search_values, 95),
                "search_p99": percentile(search_values, 99),
                "search_max": max(search_values) if search_values else None,
                "commit_delayed": commit_delayed,
                "search_delayed": search_delayed,
                "rate_limited": rate_limited,
                "tenant_rows": tenant_rows,
            }
        )
    return aggregates


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write a manifest without leaving a truncated JSON file after interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_suite_checkpoint(
    root: Path,
    manifest: dict[str, Any],
    *,
    expected_run_count: int,
    status: str = "running",
    reason: str = "",
) -> None:
    """Persist a resumable, secret-free snapshot after each observable step."""
    checkpoint = {
        "status": status,
        "updated_at": now_iso(),
        "reason": reason,
        "completed_run_count": len(manifest.get("runs") or []),
        "expected_run_count": expected_run_count,
        "manifest": "suite.json",
    }
    manifest["checkpoint"] = checkpoint
    # Keep the report useful while the matrix is still running.  The preview
    # uses exactly the same acceptance evaluator as finalization, but remains
    # explicitly labeled as partial evidence by the renderer.
    try:
        manifest["acceptance"] = evaluate_pr421_acceptance(manifest)
    except Exception as exc:  # noqa: BLE001 - checkpoint must never stop a run
        manifest["acceptance_preview_error"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "updated_at": now_iso(),
        }
    _write_json_atomic(root / "suite.json", manifest)
    _write_json_atomic(root / "checkpoint.json", checkpoint)
    # Keep a browsable partial report available while a long matrix is
    # running.  This intentionally does not evaluate a final suite status.
    try:
        try:
            from .formal_data_report import render as render_data_report
        except ImportError:
            try:
                from performance.formal_data_report import render as render_data_report
            except ImportError:
                from formal_data_report import render as render_data_report
        render_data_report(root / "suite.json", root / "suite.html")
    except Exception as exc:  # noqa: BLE001 - checkpoint must never stop a run
        _write_json_atomic(
            root / "checkpoint-render-error.json",
            {"error": f"{type(exc).__name__}: {exc}", "updated_at": now_iso()},
        )


def _finalize_suite_outputs(
    root: Path,
    manifest: dict[str, Any],
    args: argparse.Namespace,
    tenant_path: Path,
    scenario_names: list[str],
    *,
    final_status: str = "completed",
    final_reason: str = "",
) -> str:
    """Always materialize machine-readable and HTML output, including partial runs."""
    acceptance = evaluate_pr421_acceptance(manifest)
    manifest["acceptance"] = acceptance
    expected_run_count = len(scenario_names) * args.repeats * len(POLICIES)
    statuses = [
        str(run.get("status") or "NO_SUMMARY").upper()
        for run in manifest.get("runs") or []
    ]
    manifest_run_count = len(statuses)
    completed_run_count = sum(status == "COMPLETED" for status in statuses)
    evidence_run_count = sum(
        status == "COMPLETED" and _submitted_operations(run) > 0
        for status, run in zip(statuses, manifest.get("runs") or [])
    )
    empty_completed_run_count = completed_run_count - evidence_run_count
    manifest["finalization"] = {
        "status": final_status,
        "finished_at": now_iso(),
        "reason": final_reason,
        "run_count": manifest_run_count,
        "expected_run_count": expected_run_count,
        "completed_run_count": completed_run_count,
        "evidence_run_count": evidence_run_count,
        "empty_completed_run_count": empty_completed_run_count,
        "failed_run_count": sum(status in {"FAIL", "HARNESS_ERROR", "NO_SUMMARY"} for status in statuses),
        "timeout_run_count": sum(status == "TIMEOUT" for status in statuses),
        "blocked_run_count": sum(status == "BLOCKED" for status in statuses),
        "coverage_status": (
            "complete"
            if (
                manifest_run_count >= expected_run_count
                and evidence_run_count >= expected_run_count
            )
            else "partial"
        ),
    }
    _write_json_atomic(root / "suite.json", manifest)
    _write_json_atomic(root / "acceptance.json", acceptance)
    _write_json_atomic(
        root / "model_analysis_input.json",
        build_model_analysis_input(manifest, acceptance),
    )

    report_path = root / "suite.html"
    try:
        from .formal_data_report import render as render_data_report
    except ImportError:
        try:
            from performance.formal_data_report import render as render_data_report
        except ImportError:
            from formal_data_report import render as render_data_report
    render_data_report(root / "suite.json", report_path)

    statuses = [
        str(run.get("status") or "NO_SUMMARY")
        for run in manifest.get("runs") or []
    ]
    has_environment_error = any(
        status in {
            "ENVIRONMENT_ERROR",
            "ENV_ERROR",
            "RESET_FAILED",
            "NO_SUMMARY",
            "TIMEOUT",
            "FAIL",
            "HARNESS_ERROR",
        }
        for status in statuses
    )
    if has_environment_error or acceptance["overall"] == "FAIL":
        overall = "FAIL"
    elif (
        any(status in {"INCONCLUSIVE", "NOT_IMPLEMENTED", "BLOCKED", "blocked"}
            for status in statuses)
        or acceptance["overall"] in {"INCONCLUSIVE", "NOT_IMPLEMENTED"}
        or final_status != "completed"
    ):
        overall = "INCONCLUSIVE"
    else:
        overall = "PASS"

    suite_summary = {
        "status": overall,
        "test_type": "formal_stress_suite",
        "base_url": args.base_url,
        "created_at": manifest.get("created_at"),
        "finished_at": now_iso(),
        "parameters": {
            "tenant_config": str(tenant_path),
            "profile": args.profile,
            "instance_profile": args.instance_profile,
            "plan_sources": (
                ["PR397/report(6)", "PR421"]
                if args.profile in {"4u8g", "4u8g-full", "complete"}
                else ["PR397/report(6)"] if args.profile == "report6"
                else ["PR421"] if args.profile == "pr421"
                else ["report(4)"]
            ),
            "scenarios": scenario_names,
            "repeats": args.repeats,
            "policies": list(POLICIES),
            "commit_timeout_s": args.commit_timeout_s,
            "max_wall_clock_s": args.max_wall_clock_s,
            "barrier_wave_size": args.barrier_wave_size,
            "barrier_drain_timeout_s": args.barrier_drain_timeout_s,
            "skip_seed": args.skip_seed,
            "seed_sessions_per_tenant_override": args.seed_sessions_per_tenant,
            "seed_concurrency": args.seed_concurrency,
            "seed_commit_timeout_s": args.seed_commit_timeout_s,
        },
        "details": {
            "run_count": manifest_run_count,
            "expected_run_count": expected_run_count,
            "completed_run_count": completed_run_count,
            "coverage_status": manifest["finalization"]["coverage_status"],
            "plan_sources": manifest.get("plan_sources") or {},
            "failed_runs": sum(status == "FAIL" for status in statuses),
            "timeout_runs": sum(status == "TIMEOUT" for status in statuses),
            "blocked_runs": sum(
                status in {"BLOCKED", "blocked"} for status in statuses
            ),
            "inconclusive_runs": sum(
                status in {"INCONCLUSIVE", "NOT_IMPLEMENTED", "BLOCKED", "blocked"}
                for status in statuses
            ),
            "environment_errors": sum(
                status in {
                    "ENVIRONMENT_ERROR",
                    "ENV_ERROR",
                    "RESET_FAILED",
                    "NO_SUMMARY",
                    "TIMEOUT",
                    "HARNESS_ERROR",
                }
                for status in statuses
            ),
            "suite_report": "suite.html",
            "suite_manifest": "suite.json",
            "acceptance_report": "acceptance.json",
            "model_analysis_input": "model_analysis_input.json",
            "acceptance_overall": acceptance["overall"],
            "finalization_status": final_status,
        },
        "aggregates": aggregate_runs(manifest.get("runs") or []),
        "acceptance": acceptance,
    }
    _write_json_atomic(root / "summary.json", suite_summary)
    _write_suite_checkpoint(
        root,
        manifest,
        expected_run_count=len(scenario_names) * args.repeats * len(POLICIES),
        status=final_status,
        reason=manifest.get("finalization", {}).get("reason", ""),
    )
    return overall


def main() -> int:
    parser = argparse.ArgumentParser(description="Run formal real multi-tenant stress suite")
    parser.add_argument("--base-url", default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--tenant-config", required=True)
    parser.add_argument(
        "--local-auth",
        action="store_true",
        help="Use the single local identity from config.json instead of tenant credentials.",
    )
    parser.add_argument("--out-dir", default="")
    parser.add_argument(
        "--profile",
        choices=tuple(SCENARIO_PROFILES),
        default="pr421",
        help=(
            "Scenario profile; report6 is the PR397/report(6) matrix, pr421 "
            "is the PR421 acceptance suite, 4u8g is the compatibility bounded "
            "single-instance run, 4u8g-full runs all 12 PR397 and 27 PR421 "
            "cases, and complete runs both catalogs."
        ),
    )
    parser.add_argument(
        "--instance-profile",
        default="",
        help="实际生效的机器规格名称，例如 4U8G / 8U16G",
    )
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--duration-cap-s",
        type=float,
        default=0.0,
        help="Optional diagnostic cap for each scenario duration; 0 keeps scenario defaults.",
    )
    parser.add_argument(
        "--case-timeout-s",
        type=float,
        default=0.0,
        help=(
            "Wall-clock timeout for one scenario case, including setup and "
            "retries; 0 derives it from workload duration and commit timeout."
        ),
    )
    parser.add_argument(
        "--allow-shared-identity",
        action="store_true",
        help="Allow an exploratory shared credential; isolation/fairness remain inconclusive.",
    )
    parser.add_argument(
        "--allow-partial-tenants",
        action="store_true",
        help=(
            "鉴权预检部分成功时继续执行可用租户范围内的场景；"
            "需要更多租户的场景会记录为 blocked，不伪造多租户结论"
        ),
    )
    parser.add_argument("--commit-timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--seed-commit-timeout-s",
        type=float,
        default=180.0,
        help="seed warmup 使用的真实 Commit 终态等待上限，默认 180 秒",
    )
    parser.add_argument("--commit-max-attempts", type=int, default=3)
    parser.add_argument("--commit-retry-backoff-s", type=float, default=2.0)
    parser.add_argument(
        "--seed-concurrency",
        type=int,
        default=2,
        help="正式 seed warmup 的租户级并发数；默认 2，避免真实 Commit 相互堆积",
    )
    parser.add_argument(
        "--barrier-wave-size",
        type=int,
        default=32,
        help="barrier Commit 最大同时在途数，默认 32",
    )
    parser.add_argument(
        "--barrier-drain-timeout-s",
        type=float,
        default=600.0,
        help="barrier Search 窗口结束后收集 Commit 终态的最大等待时间；正式模式默认 600 秒",
    )
    parser.add_argument(
        "--max-wall-clock-s",
        type=float,
        default=21600.0,
        help="整轮正式套件最大 wall-clock 时间；默认 6 小时，超时后不再启动新场景",
    )
    parser.add_argument(
        "--case-retries",
        type=int,
        default=2,
        help=(
            "场景发生无业务样本的环境错误时的重试次数；只重试 "
            "ENV_ERROR/NO_SUMMARY/HARNESS_ERROR，默认 2"
        ),
    )
    parser.add_argument(
        "--case-retry-backoff-s",
        type=float,
        default=5.0,
        help="场景环境错误重试之间的等待秒数，默认 5 秒",
    )
    parser.add_argument(
        "--barrier-count-cap",
        type=int,
        default=0,
        help=(
            "显式限制每个 barrier 场景的 Commit 数；仅用于 bounded/quick "
            "诊断，0 表示使用方案原始数量"
        ),
    )
    parser.add_argument("--reset-command", default="", help="Optional command run before every case")
    parser.add_argument("--no-server-metrics", action="store_true")
    parser.add_argument(
        "--search-queries",
        default=os.getenv("ECHOMEM_SEARCH_QUERIES", ""),
        help="skip-seed 时使用已有记忆的真实查询词，逗号分隔；未提供则记录 fallback",
    )
    parser.add_argument(
        "--inter-case-recovery-timeout-s",
        type=float,
        default=90.0,
        help="每个场景开始前等待 EchoMem /health 恢复的最长时间",
    )
    parser.add_argument(
        "--health-poll-s",
        type=float,
        default=2.0,
        help="场景间健康检查轮询间隔",
    )
    parser.add_argument(
        "--reuse-existing-data",
        action="store_true",
        help="复用 tenant-config 对应租户的已有记忆，不重复注入真实模型",
    )
    parser.add_argument(
        "--no-seed-reuse",
        action="store_true",
        help="每个场景独立灌入最小真实 session，保留 active-user 证据",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help=(
            "正式套件别名：复用已有租户和记忆，只执行调度/延迟/指标压测；"
            "不用于证明记忆质量"
        ),
    )
    parser.add_argument(
        "--quick-mode",
        action="store_true",
        help=(
            "启用 bounded quick 覆盖：默认每场景最多 15s、每个 barrier "
            "最多 8 个 Commit、单轮；例如容量阶梯不发送后台 Commit"
        ),
    )
    parser.add_argument(
        "--quick-seed-tenant-cap",
        type=int,
        default=4,
        help=(
            "quick 模式真实灌种的租户上限；容量/调度场景仍可使用更多租户，"
            "但未灌种租户只能用于黑盒调度和容量证据，不能宣称有热记忆"
        ),
    )
    parser.add_argument(
        "--seed-sessions-per-tenant",
        type=int,
        default=None,
        help=(
            "覆盖各场景的灌种 session 数；0 表示不灌种。"
            "正式记忆质量测试建议保留默认场景值"
        ),
    )
    parser.add_argument(
        "--active-sessions-per-tenant",
        type=int,
        default=None,
        help=(
            "无 seed 的容量场景为每租户创建的真实空 session 数；"
            "用于把 active session 作为活跃用户压测代理"
        ),
    )
    parser.add_argument(
        "--preflight-config",
        default=os.getenv("ECHOMEM_CONFIG", ""),
        help="EchoMem config.json to validate before the suite starts",
    )
    # Compatibility options used by the Web/Feishu orchestrator.  The formal
    # suite derives worker counts from each scenario, but accepting these
    # legacy knobs keeps an already deployed API from failing before case 1.
    parser.add_argument("--commit-workers", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--search-workers", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--auth-header", default="X-Auth-Key", help=argparse.SUPPRESS)
    parser.add_argument("--pid", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.quick_mode:
        # Quick mode is intended to produce actionable evidence quickly.  Do
        # not leave the scenario's 10-minute/30-minute defaults in effect.
        args.duration_cap_s = min(args.duration_cap_s or 15.0, 15.0)
        # A small barrier is only a smoke test and cannot establish O5.
        # Keep quick mode bounded, but preserve the minimum real Commit flood
        # required by scheduler_acceptance.
        args.barrier_count_cap = min(args.barrier_count_cap or 32, 32)
        args.repeats = 1

    scenario_catalog = SCENARIO_PROFILES[args.profile]
    default_scenarios = (
        "baseline,mixed,commit-storm,commit-barrier,saturation,tenant-skew,"
        "search-priority-blackbox,search-storm"
        if args.profile == "pr421"
        else ",".join(
            name for name in FOUR_U8G_SCENARIOS
            if name != "soak"
        )
        if args.profile == "4u8g"
        else ",".join(
            name for name in FOUR_U8G_FULL_SCENARIOS
            if name != "pr421__soak"
        )
        if args.profile == "4u8g-full"
        else ",".join(
            name for name in scenario_catalog
            if not (args.profile == "complete" and name == "soak")
        )
    )
    scenario_names = [
        item.strip()
        for item in (args.scenarios or default_scenarios).split(",")
        if item.strip()
    ]
    unknown = [item for item in scenario_names if item not in scenario_catalog]
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    if args.case_timeout_s < 0:
        parser.error("--case-timeout-s must not be negative")
    if args.max_wall_clock_s <= 0:
        parser.error("--max-wall-clock-s must be > 0")
    if args.case_retries < 0:
        parser.error("--case-retries must be >= 0")
    if args.case_retry_backoff_s < 0:
        parser.error("--case-retry-backoff-s must be >= 0")
    if args.seed_commit_timeout_s <= 0:
        parser.error("--seed-commit-timeout-s must be > 0")
    if args.seed_concurrency < 1:
        parser.error("--seed-concurrency must be >= 1")
    if args.barrier_wave_size < 1:
        parser.error("--barrier-wave-size must be >= 1")
    if args.barrier_drain_timeout_s < 0:
        parser.error("--barrier-drain-timeout-s must be >= 0")
    if args.inter_case_recovery_timeout_s < 0:
        parser.error("--inter-case-recovery-timeout-s must not be negative")
    if args.health_poll_s <= 0:
        parser.error("--health-poll-s must be > 0")
    if args.seed_sessions_per_tenant is not None and args.seed_sessions_per_tenant < 0:
        parser.error("--seed-sessions-per-tenant must be >= 0")
    if (
        args.active_sessions_per_tenant is not None
        and args.active_sessions_per_tenant < 1
    ):
        parser.error("--active-sessions-per-tenant must be >= 1")
    if args.quick_seed_tenant_cap < 1:
        parser.error("--quick-seed-tenant-cap must be >= 1")
    if args.profile in {"report6", "4u8g", "4u8g-full", "complete"} and not args.preflight_config:
        parser.error(
            f"--profile {args.profile} requires --preflight-config with the actual EchoMem config.json"
        )
    suite_deadline = time.monotonic() + args.max_wall_clock_s
    preflight_result: dict[str, Any] | None = None
    if args.preflight_config:
        preflight_result = run_preflight(
            args.preflight_config,
            timeout_s=_budget_timeout(30.0, suite_deadline, minimum_s=0.0),
        )
        if not preflight_result["ok"]:
            parser.error(
                f"real-model preflight failed: {preflight_result['error']}"
            )

    tenant_path = Path(args.tenant_config).expanduser().resolve()
    all_tenants = load_tenants(tenant_path)
    required_tenants = max(scenario_catalog[name]["tenants"] for name in scenario_names)
    auth_mode_error = _auth_mode_validation_error(
        local_auth=args.local_auth,
        required_tenants=required_tenants,
    )
    if auth_mode_error:
        parser.error(auth_mode_error)
    if len(all_tenants) < required_tenants:
        parser.error(
            f"tenant config has {len(all_tenants)} tenants, but selected scenarios require {required_tenants}"
        )
    auth_preflight_result: dict[str, Any] = {
        "status": "SKIPPED",
        "reason": "local auth mode does not use tenant credentials",
    }
    if not args.local_auth:
        auth_preflight_result = run_auth_preflight(
            args.base_url,
            tenant_path,
            timeout_s=_budget_timeout(5.0, suite_deadline, minimum_s=0.0),
            tenant_count=required_tenants,
            auth_header=args.auth_header,
        )
        if (
            auth_preflight_result["status"] != "PASS"
            and not args.allow_partial_tenants
        ):
            root = Path(
                args.out_dir
                or f"results/performance/formal_auth_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            root.mkdir(parents=True, exist_ok=True)
            (root / "auth-preflight.json").write_text(
                json.dumps(auth_preflight_result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                "AUTH_PREFLIGHT_FAILED "
                f"status={auth_preflight_result['status']} "
                f"passed={auth_preflight_result['passed']} "
                f"failed={auth_preflight_result['failed']} "
                f"out={root}"
            )
            return 3
    if args.allow_partial_tenants and not args.local_auth:
        all_tenants = _usable_tenants(all_tenants, auth_preflight_result)
        if not all_tenants:
            print("AUTH_PREFLIGHT_FAILED no usable tenants", flush=True)
            return 3
    args.identity_independent = _identity_is_independent(all_tenants)
    try:
        runtime_config = json.loads(
            Path(args.preflight_config).expanduser().read_text(encoding="utf-8")
        ) if args.preflight_config else {}
    except (OSError, json.JSONDecodeError):
        runtime_config = {}
    auth_config = runtime_config.get("auth") if isinstance(runtime_config, dict) else {}
    # Do not infer the wire authentication mode from config.json. A deployment
    # may keep a local workspace config while exposing API-key identities.
    args.local_auth_mode = bool(args.local_auth)
    args.effective_auth_mode = "local_auth" if args.local_auth else "tenant_config"
    args.local_tenant_id = (
        str(auth_config.get("default_tenant_id") or "local")
        if isinstance(auth_config, dict)
        else "local"
    )
    args.local_user_id = (
        str(auth_config.get("default_user_id") or "local_user")
        if isinstance(auth_config, dict)
        else "local_user"
    )
    root = Path(args.out_dir or f"results/performance/formal_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    root.mkdir(parents=True, exist_ok=True)
    runner = RUNNER
    config_dir = root / "_tenant_configs"
    config_dir.mkdir(exist_ok=True)
    config_paths: dict[int, Path] = {}
    for count in sorted({scenario_catalog[name]["tenants"] for name in scenario_names}):
        config_paths[count] = config_dir / f"tenants-{count}.json"
        write_subset(config_paths[count], all_tenants[:count])

    manifest: dict[str, Any] = {
        "created_at": now_iso(),
        "base_url": args.base_url,
        "profile": args.profile,
        "instance_profile": args.instance_profile,
        "plan_sources": {
            "pr397": {
                "name": "EchoMem PR397 / report(6) 故障发现与真实多租户压测方案",
                "included": args.profile in {"report6", "4u8g", "4u8g-full", "complete"},
                "scenario_count": (
                    len(report6_scenarios())
                    if args.profile in {"report6", "4u8g", "4u8g-full", "complete"}
                    else 0
                ),
                "scenarios": (
                    sorted(report6_scenarios())
                    if args.profile in {"report6", "4u8g", "complete"}
                    else sorted(
                        name.removeprefix("pr397__")
                        for name in FOUR_U8G_FULL_SCENARIOS
                        if name.startswith("pr397__")
                    )
                    if args.profile == "4u8g-full"
                    else []
                ),
            },
            "pr421": {
                "name": "EchoMem PR421 可量化验收与调度指标方案",
                "included": args.profile in {"pr421", "4u8g", "4u8g-full", "complete"},
                "scenario_count": (
                    len(FOUR_U8G_SCENARIOS)
                    if args.profile == "4u8g-full"
                    else len(scenario_catalog)
                    if args.profile in {"pr421", "4u8g"}
                    else len(SCENARIOS)
                    if args.profile == "complete"
                    else 0
                ),
                "scenarios": (
                    sorted(
                        name.removeprefix("pr421__")
                        for name in FOUR_U8G_FULL_SCENARIOS
                        if name.startswith("pr421__")
                    )
                    if args.profile == "4u8g-full"
                    else sorted(scenario_catalog)
                    if args.profile in {"pr421", "4u8g"}
                    else sorted(SCENARIOS)
                    if args.profile == "complete"
                    else []
                ),
                "acceptance_targets_recorded": True,
            },
        },
        "tenant_config": str(tenant_path),
        "allow_shared_identity": args.allow_shared_identity,
        "output_root": str(root.resolve()),
        "scenarios": scenario_names,
        "repeats": args.repeats,
        "duration_cap_s": args.duration_cap_s,
        "case_timeout_s": args.case_timeout_s,
        "max_wall_clock_s": args.max_wall_clock_s,
        "seed_commit_timeout_s": args.seed_commit_timeout_s,
        "policies": list(POLICIES),
        "acceptance_targets": PR421_ACCEPTANCE_TARGETS,
        "preflight_config": (
            str(Path(args.preflight_config).expanduser().resolve())
            if args.preflight_config
            else ""
        ),
        "preflight": (
            {
                **preflight_result,
                "config": str(Path(args.preflight_config).expanduser().resolve()),
            }
            if preflight_result is not None
            else {"status": "NOT_RUN", "config": "", "engines_checked": 0, "engines": [], "digest": ""}
        ),
        "auth_preflight": auth_preflight_result,
        "allow_partial_tenants": args.allow_partial_tenants,
        "usable_tenant_count": len(all_tenants),
        "reset_command": args.reset_command,
        "reuse_existing_data": args.reuse_existing_data,
        "skip_seed": args.skip_seed,
        "seed_sessions_per_tenant_override": args.seed_sessions_per_tenant,
        "search_queries_configured": bool(args.search_queries),
        "inter_case_recovery_timeout_s": args.inter_case_recovery_timeout_s,
        "client_admission_enabled": False,
        "server_observation_mode": True,
        "runs": [],
    }
    expected_run_count = len(scenario_names) * args.repeats * len(POLICIES)

    def checkpoint(status: str = "running", reason: str = "") -> None:
        _write_suite_checkpoint(
            root,
            manifest,
            expected_run_count=expected_run_count,
            status=status,
            reason=reason,
        )

    signal_state = {"finalized": False}

    def handle_interrupt(signum: int, _frame: Any) -> None:
        """Persist a readable partial report before honoring SIGINT/SIGTERM."""
        if signal_state["finalized"]:
            raise KeyboardInterrupt
        signal_state["finalized"] = True
        reason = f"收到信号 {signum}，套件在当前场景停止；未运行场景保留为缺失证据"
        checkpoint("interrupted", reason)
        try:
            _finalize_suite_outputs(
                root,
                manifest,
                args,
                tenant_path,
                scenario_names,
        final_status="interrupted",
                final_reason=reason,
            )
        finally:
            raise KeyboardInterrupt

    previous_sigint = signal.signal(signal.SIGINT, handle_interrupt)
    previous_sigterm = signal.signal(signal.SIGTERM, handle_interrupt)
    checkpoint()
    # 用确定性顺序执行，便于重跑对比；服务端重置钩子负责固定数据/索引边界。
    max_seed_count = max(
        int(scenario_catalog[name]["tenants"]) for name in scenario_names
    )
    seed_scenario_names = [
        name
        for name in scenario_names
        if _scenario_requires_seed(scenario_catalog[name], name)
    ]
    # Capacity-only scenarios may need 16/32 active identities, but they do
    # not need 16/32 real-model Commit extractions. Warm up only the largest
    # scenario whose intended claim actually depends on preloaded memory.
    seed_tenant_count = max(
        (
            int(scenario_catalog[name]["tenants"])
            for name in seed_scenario_names
        ),
        default=0,
    )
    auto_reuse_seed = (
        bool(args.quick_mode or args.profile in {"4u8g", "4u8g-full", "complete", "report6"})
        and not bool(args.reuse_existing_data)
        and not bool(args.skip_seed)
        and not bool(args.no_seed_reuse)
    )
    # ``--reuse-existing-data`` means that the configured tenants already have
    # usable memory and may be reused. ``--skip-seed`` is different: it
    # explicitly requests empty-session black-box traffic and must never be
    # converted into a seed reuse decision.
    seed_ready = bool(args.reuse_existing_data and not args.skip_seed)
    seed_warmup_failed = False
    if auto_reuse_seed:
        if args.quick_mode:
            seed_tenant_count = min(seed_tenant_count, args.quick_seed_tenant_cap)
        if seed_tenant_count <= 0:
            # The selected matrix contains only capacity/other black-box
            # scenarios. Do not spend real-model resources on an unused seed.
            seed_ready = False
            manifest["seed_warmup"] = {
                "status": "not_required",
                "tenant_count": 0,
                "requested_tenant_count": max_seed_count,
                "seed_scenarios": [],
                "reason": "selected scenarios do not require preloaded memory",
            }
            checkpoint("seed_not_required", "no selected scenario requires seed")
        else:
            if seed_tenant_count not in config_paths:
                config_paths[seed_tenant_count] = config_dir / f"tenants-{seed_tenant_count}.json"
                write_subset(config_paths[seed_tenant_count], all_tenants[:seed_tenant_count])
            warmup_output = root / "_seed_warmup"
            warmup_started_at = now_iso()
            warmup_timeout_s = max(
                180.0,
                float(args.case_timeout_s or 0.0),
                float(args.seed_commit_timeout_s) * 2.0,
            )
            warmup_command = _build_seed_warmup_command(
                args,
                config_paths[seed_tenant_count],
                warmup_output / "run",
                seed_tenant_count,
                args.seed_commit_timeout_s,
            )
            warmup_output.mkdir(parents=True, exist_ok=True)
            warmup_timeout_s = _budget_timeout(warmup_timeout_s, suite_deadline, minimum_s=0.0)
            if warmup_timeout_s <= 0:
                seed_ready = False
                warmup_completed = subprocess.CompletedProcess(
                    warmup_command,
                    124,
                    "",
                    "formal_suite: suite wall-clock budget exhausted before seed warmup\n",
                )
                warmup_timed_out = True
            else:
                warmup_completed, warmup_timed_out = run_case_process(
                    warmup_command,
                    timeout_s=warmup_timeout_s,
                )
            warmup_finished_at = now_iso()
            (warmup_output / "stdout.log").write_text(
                warmup_completed.stdout, encoding="utf-8"
            )
            (warmup_output / "stderr.log").write_text(
                warmup_completed.stderr, encoding="utf-8"
            )
            warmup_run_dir = _resolve_run_dir(warmup_output / "run")
            warmup_summary = {}
            if (warmup_run_dir / "summary.json").is_file():
                try:
                    warmup_summary = json.loads(
                        (warmup_run_dir / "summary.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    warmup_summary = {}
            seed_ready = (
                not warmup_timed_out
                and warmup_completed.returncode == 0
                and str(warmup_summary.get("status") or "") == "completed"
            )
            seed_warmup_failed = not seed_ready
            manifest["seed_warmup"] = {
                "status": "completed" if seed_ready else "failed",
                "started_at": warmup_started_at,
                "finished_at": warmup_finished_at,
                "timeout_s": warmup_timeout_s,
                "seed_commit_timeout_s": args.seed_commit_timeout_s,
                "tenant_count": seed_tenant_count,
                "requested_tenant_count": max_seed_count,
                "tenant_cap_applied": seed_tenant_count < max_seed_count,
                "seed_scenarios": seed_scenario_names,
                "command": warmup_command,
                "returncode": warmup_completed.returncode,
                "timeout": warmup_timed_out,
                "output_dir": str(warmup_output.resolve()),
                "summary": warmup_summary,
            }
            if seed_ready:
                args.reuse_existing_data = True
                args.search_queries = _seed_anchor_queries(seed_tenant_count)
            else:
                manifest["seed_warmup"]["failure_reason"] = (
                    "共享真实模型 seed warmup 未完成；相关场景继续执行但只能将记忆质量/"
                    "热缓存结论标为 INCONCLUSIVE，容量/调度类场景不应被连带阻断"
                )
            checkpoint(
                "seed_ready" if seed_ready else "seed_failed",
                "seed warmup completed" if seed_ready else "seed warmup failed",
            )
    budget_exhausted = False
    total_runs = len(scenario_names) * args.repeats * len(POLICIES)
    for scenario_index, scenario in enumerate(scenario_names):
        case = scenario_catalog[scenario]
        if _remaining_budget(suite_deadline) <= 0:
            budget_exhausted = True
            for remaining_scenario in scenario_names[scenario_index:]:
                remaining_case = scenario_catalog[remaining_scenario]
                for repetition in range(1, args.repeats + 1):
                    for remaining_policy in POLICIES:
                        output = root / remaining_scenario / f"repeat-{repetition:02d}" / remaining_policy
                        manifest["runs"].append(
                            _budget_exhausted_run(
                                remaining_scenario,
                                repetition,
                                remaining_policy,
                                remaining_case,
                                output,
                                args.max_wall_clock_s,
                            )
                        )
            break
        if case["tenants"] > len(all_tenants):
            reason = (
                f"需要 {case['tenants']} 个通过鉴权的租户，当前只有 "
                f"{len(all_tenants)} 个可用租户；未发送请求，不能据此判断 EchoMem 功能失败"
            )
            for repetition in range(1, args.repeats + 1):
                for policy in POLICIES:
                    manifest["runs"].append({
                        "scenario": scenario,
                        "repetition": repetition,
                        "policy": policy,
                        "status": "blocked",
                        "blocked_reason": reason,
                        "required_tenants": case["tenants"],
                        "usable_tenants": len(all_tenants),
                    })
            print(
                f"FORMAL_BLOCKED scenario={scenario} "
                f"required_tenants={case['tenants']} usable_tenants={len(all_tenants)}",
                flush=True,
            )
            continue
        for repetition in range(1, args.repeats + 1):
            for policy in POLICIES:
                if _remaining_budget(suite_deadline) <= 0:
                    budget_exhausted = True
                    for remaining_scenario in scenario_names[scenario_index:]:
                        remaining_case = scenario_catalog[remaining_scenario]
                        first_repetition = repetition if remaining_scenario == scenario else 1
                        for remaining_repetition in range(first_repetition, args.repeats + 1):
                            for remaining_policy in POLICIES:
                                output = root / remaining_scenario / f"repeat-{remaining_repetition:02d}" / remaining_policy
                                manifest["runs"].append(
                                    _budget_exhausted_run(
                                        remaining_scenario,
                                        remaining_repetition,
                                        remaining_policy,
                                        remaining_case,
                                        output,
                                        args.max_wall_clock_s,
                                    )
                                )
                    break
                # A bounded suite pays the real-model seed cost once per
                # tenant envelope, not once per scenario. If warm-up failed,
                # run the black-box case with seed disabled so capacity,
                # scheduling, recovery, and metrics evidence is still
                # collected. The run manifest records the missing hot-memory
                # precondition instead of turning the case into a zero-row
                # BLOCKED placeholder.
                requires_seed = _scenario_requires_seed(case, scenario)
                case_reuses_seed = seed_ready
                previous_reuse = bool(args.reuse_existing_data)
                previous_queries = str(args.search_queries)
                previous_skip_seed = bool(args.skip_seed)
                if case_reuses_seed:
                    args.reuse_existing_data = True
                    args.search_queries = _seed_anchor_queries(seed_tenant_count)
                    args.skip_seed = False
                elif seed_warmup_failed:
                    args.reuse_existing_data = False
                    args.skip_seed = True
                elif args.skip_seed:
                    args.reuse_existing_data = False
                    args.skip_seed = True
                elif not requires_seed:
                    # Capacity, scheduling and metrics cases do not need
                    # model-backed memory preparation. Explicitly disable
                    # per-case seed work so a black-box case cannot spend
                    # its timeout on an irrelevant Commit warm-up.
                    args.reuse_existing_data = False
                    args.skip_seed = True
                completed_runs = len(manifest["runs"])
                total_runs = len(scenario_names) * args.repeats * len(POLICIES)
                print(
                    f"FORMAL_PROGRESS {completed_runs}/{total_runs} "
                    f"scenario={scenario} repeat={repetition} policy={policy}",
                    flush=True,
                )
                case_output = (
                    root / scenario / f"repeat-{repetition:02d}" / policy
                )
                attempts: list[dict[str, Any]] = []
                run: dict[str, Any]
                for attempt in range(1, args.case_retries + 2):
                    if attempt > 1:
                        remaining = _remaining_budget(suite_deadline)
                        if remaining is not None and remaining <= 0:
                            break
                        if args.case_retry_backoff_s > 0:
                            time.sleep(
                                min(
                                    args.case_retry_backoff_s,
                                    max(0.0, remaining or args.case_retry_backoff_s),
                                )
                            )
                        archived = _archive_case_attempt(case_output, attempt - 1)
                        if archived is not None:
                            print(
                                f"FORMAL_RETRY_ARCHIVED scenario={scenario} "
                                f"attempt={attempt - 1} path={archived}",
                                flush=True,
                            )
                    try:
                        run = run_case(
                            runner,
                            root,
                            scenario,
                            repetition,
                            policy,
                            config_paths[case["tenants"]],
                            args,
                            case,
                            deadline=suite_deadline,
                        )
                    except BaseException as exc:  # noqa: BLE001
                        # A single probe must not discard the rest of the matrix.
                        # Preserve the exception and let the bounded retry
                        # policy decide whether another attempt is useful.
                        run = {
                            "scenario": scenario,
                            "scenario_label": case["label"],
                            "repetition": repetition,
                            "policy": policy,
                            "status": "HARNESS_ERROR",
                            "runner_returncode": None,
                            "duration_s": float(case["duration_s"]),
                            "case_timeout_s": float(args.case_timeout_s or 0),
                            "output_dir": str(case_output.resolve()),
                            "summary": {},
                            "failure_evidence": {
                                "exception_type": type(exc).__name__,
                                "exception": str(exc),
                            },
                        }
                        print(
                            f"FORMAL_CASE_ERROR scenario={scenario} "
                            f"repeat={repetition} policy={policy} "
                            f"attempt={attempt} "
                            f"error={type(exc).__name__}: {exc}",
                            flush=True,
                        )
                    attempts.append(
                        {
                            "attempt": attempt,
                            "status": run.get("status"),
                            "runner_returncode": run.get("runner_returncode"),
                            "output_dir": run.get("output_dir"),
                            "failure_evidence": run.get("failure_evidence", {}),
                        }
                    )
                    if not _retryable_case_status(run.get("status")):
                        break
                    if attempt >= args.case_retries + 1:
                        break
                    print(
                        f"FORMAL_CASE_RETRY scenario={scenario} "
                        f"repeat={repetition} policy={policy} "
                        f"attempt={attempt}/{args.case_retries + 1} "
                        f"status={run.get('status')}",
                        flush=True,
                    )
                if attempts:
                    run["attempts"] = attempts
                    run["attempt_count"] = len(attempts)
                    run["retry_count"] = max(0, len(attempts) - 1)
                run["scenario_key"] = scenario
                run["source_scenario"] = case.get("_source_scenario", scenario)
                run["plan_source"] = case.get("_plan_source", "")
                # Acceptance gates consume the canonical scenario name while
                # ``scenario_key`` keeps duplicate PR397/PR421 artifacts
                # separately addressable on disk.
                run["scenario"] = case.get("_source_scenario", scenario)
                run["seed_reused"] = case_reuses_seed
                run["seed_status"] = (
                    "completed"
                    if case_reuses_seed
                    else "failed"
                    if seed_warmup_failed
                    else "not_requested"
                )
                run["seed_dependency"] = _seed_dependency(case, scenario)
                run["seed_tenant_count"] = (
                    seed_tenant_count if case_reuses_seed else 0
                )
                run["seed_evidence_status"] = (
                    "complete"
                    if case_reuses_seed
                    else "inconclusive"
                    if requires_seed
                    else "not_required"
                )
                if seed_warmup_failed:
                    run["seed_warning"] = (
                        "shared seed warm-up failed; this run still collected real "
                        "black-box requests, but hot-cache/retrieval-quality claims "
                        "are INCONCLUSIVE"
                    )
                run["effective_commit_timeout_s"] = float(args.commit_timeout_s)
                manifest["runs"].append(run)
                print(
                    f"FORMAL_PROGRESS {len(manifest['runs'])}/{total_runs} "
                    f"scenario={scenario} repeat={repetition} policy={policy} "
                    f"status={run.get('status')} "
                    f"seed_reused={str(case_reuses_seed).lower()}",
                    flush=True,
                )
                args.reuse_existing_data = previous_reuse
                args.search_queries = previous_queries
                args.skip_seed = previous_skip_seed
                checkpoint()
            if budget_exhausted:
                break
        if budget_exhausted:
            break

    overall = _finalize_suite_outputs(
        root,
        manifest,
        args,
        tenant_path,
        scenario_names,
        final_status="completed",
    )
    report_path = root / "suite.html"
    signal.signal(signal.SIGINT, previous_sigint)
    signal.signal(signal.SIGTERM, previous_sigterm)
    print(report_path)
    return 0 if overall in {"PASS", "INCONCLUSIVE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
