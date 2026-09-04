#!/usr/bin/env python3
"""Performance stress-test entry point for EchoMem.

Measures multi-tenant concurrent read/write performance of a live EchoMem
server: read throughput/latency, four-stage injection latency, mixed
read/write degradation, injection-burst interference ("injection blocks
retrieval"), and server CPU/RSS observed through its /metrics endpoint.

The target address is fully configurable (--echomem-url), so internet
deployments are supported; --auth-mode static covers pre-provisioned
identities on servers that do not allow self-service tenant creation.

用法:
  python performance/run_stress.py --quick
  python performance/run_stress.py --echomem-url http://192.168.1.10:8010
      --auth-mode static --auth-key XXX --tenant-id T --user-id U
      --tenants 1 --scenarios A,C,D --mix-ratios 8:1,4:1
  python performance/run_stress.py --tenants 8 --seed-source locomo
      --sample-filter conv-30 --cleanup-identities
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保能 import backends/shared/performance 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backends.echomem.client import EchoMemClient
from performance.loadgen import LoadGenerator, RequestRecord, SceneResult
from performance.metrics_calc import (
    FEATURE_LABELS,
    RSS_LEAK_SLOPE_MB_PER_MIN,
    commit_completion_latency,
    commit_durability,
    consistency_summary,
    degradation_factor,
    error_type_validation,
    evaluate_features,
    fault_injection_summary,
    hot_tenant_summary,
    injected_bytes_series,
    isolation_probe_summary,
    isolation_summary,
    reconcile_messages,
    retry_summary,
    rss_normalized_series,
    rss_trend_mb_per_min,
    saturation_summary,
    search_quality_summary,
    summarize_records,
    tenant_fairness,
)
from performance.monitor import (
    COMMIT_QUEUE_DEPTH,
    CPU_SECONDS,
    HTTP_INFLIGHT,
    PROCESS_THREADS,
    RECALL_ENGINE_CALLS,
    RESIDENT_MEMORY,
    MetricsMonitor,
    scene_resource_summary,
)
from performance.perf_mock_provider import (
    DEFAULT_FAULT_STAGES,
    MockProvider,
    run_fault_sequence,
)
from performance.perf_preflight import run_preflight
from performance.prepare import (
    build_search_query_pool,
    TenantPreparer,
    load_locomo_seed_batches,
    load_tenant_specs,
    verify_recall_query_pool,
)
from performance.report import (
    build_html,
    save_html,
    write_config,
    write_metrics_csv,
    write_requests_csv,
    write_summary,
)
from performance.scenarios import (
    SCENARIO_NAMES,
    SceneRun,
    expand_matrix,
    parse_concurrency_steps,
    parse_mix_ratios,
)

logger = logging.getLogger("performance.run_stress")

DEFAULT_MIX_RATIOS = "8:1,4:1,1:1"
DEFAULT_CONCURRENCY_STEPS = "1,4,16,64"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EchoMem performance stress test (multi-tenant read/write)"
    )
    g = parser.add_argument_group("Target")
    g.add_argument(
        "--echomem-url",
        default="http://127.0.0.1:8010",
        help="EchoMem base URL, IP:port configurable (外网部署: http://<ip>:<port>)",
    )
    g.add_argument(
        "--timeout-s",
        type=float,
        default=30.0,
        help="Per-request timeout (真实模型路径 search/commit 5-7s+；外网 RTT 大可调大)",
    )
    g.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Search top-k used by read load",
    )
    g.add_argument(
        "--commit-poll-timeout-s",
        type=float,
        default=600.0,
        help="Timeout for poll-until-commit-completed (真实模型 commit 平均 46s、P99 117s+，加排队)",
    )
    g = parser.add_argument_group("Identity")
    g.add_argument(
        "--auth-mode",
        choices=["provision", "static"],
        default="provision",
        help=(
            "provision=自助创建隔离租户; static=复用预置身份 "
            "(外网部署无自助创建权限时用 static)"
        ),
    )
    g.add_argument("--auth-key", default="", help="static 模式: 预置鉴权 key")
    g.add_argument("--tenant-id", default="", help="static 模式: 预置租户 id")
    g.add_argument("--user-id", default="", help="static 模式: 预置用户 id")
    g.add_argument("--agent-id", default="default", help="agent id")
    g.add_argument("--tenants", type=int, default=8, help="租户数 (static 模式固定为 1)")
    g = parser.add_argument_group("Data")
    g.add_argument(
        "--seed-source",
        choices=["synthetic", "locomo"],
        default="synthetic",
        help="种子数据源: synthetic=合成锚词消息(默认); locomo=真实对话(按 --sample-filter 从数据集读取)",
    )
    g.add_argument(
        "--dataset-path",
        default="",
        help="locomo 数据集路径 (默认 benchmarks/locomo/data/locomo10.json；仅 --seed-source locomo 生效)",
    )
    g.add_argument(
        "--sample-filter",
        default="conv-30",
        help="locomo 样本过滤器: 单个 sample_id / 逗号分隔多个 / all (默认 conv-30)",
    )
    g.add_argument("--seed-sessions-per-tenant", type=int, default=5, help="每租户种子 session 数 (locomo 源时由数据集会话数决定)")
    g.add_argument("--messages-per-session", type=int, default=10, help="每个 session 的消息条数 (写事务也用它)")
    g.add_argument(
        "--skip-seed",
        action="store_true",
        help="复用已有租户和记忆，只执行压测请求，不重复注入真实模型",
    )
    g.add_argument(
        "--search-queries",
        default=os.getenv("ECHOMEM_SEARCH_QUERIES", ""),
        help=(
            "兼容旧参数：逗号分隔的 Search 查询词。建议改用 "
            "--search-recall-queries 或 --search-no-recall-queries。"
        ),
    )
    g.add_argument(
        "--search-recall-queries",
        default=os.getenv("ECHOMEM_SEARCH_RECALL_QUERIES", ""),
        help=(
            "额外/复用记忆的 recall 查询词，逗号分隔。会在压测窗口前做真实 "
            "Search 验证，只有命中的 query 才进入 recall 流量池。"
        ),
    )
    g.add_argument(
        "--search-no-recall-queries",
        default=os.getenv("ECHOMEM_SEARCH_NO_RECALL_QUERIES", ""),
        help=(
            "日常但预期不命中既有记忆的查询词，逗号分隔；用于 mixed 或 "
            "no-recall-only 流量。未配置时使用内置日常查询池。"
        ),
    )
    g.add_argument(
        "--search-query-profile",
        choices=["mixed", "recall-only", "no-recall-only"],
        default=os.getenv("ECHOMEM_SEARCH_QUERY_PROFILE", "mixed"),
        help="Search 负载类型: mixed=召回+普通混合, recall-only=只测记忆召回, no-recall-only=只测普通用户查询",
    )
    g.add_argument(
        "--search-recall-ratio",
        type=float,
        default=float(os.getenv("ECHOMEM_SEARCH_RECALL_RATIO", "0.7")),
        help="mixed 模式下 recall 查询占比（0~1）",
    )
    g.add_argument(
        "--seed-recall-probe-limit",
        type=int,
        default=20,
        help=(
            "每租户在压测前最多验证多少条 recall 候选；验证不计入压测窗口，"
            "默认 20。0 表示不验证，recall 正确性只能标记 INCONCLUSIVE。"
        ),
    )
    g.add_argument(
        "--allow-unverified-search",
        action="store_true",
        help=(
            "无可验证记忆时仍执行真实 Search 负载；仅用于容量/调度/指标等黑盒观测，"
            "召回质量和热缓存结论会标记 INCONCLUSIVE"
        ),
    )
    g.add_argument(
        "--seed-concurrency",
        type=int,
        default=4,
        help="种子数据的租户级并发数（不计入压测窗口，默认 4）",
    )
    g.add_argument(
        "--barrier-prepare-concurrency",
        type=int,
        default=4,
        help="commit barrier 未提交 session 准备并发数（默认 4）",
    )
    g.add_argument(
        "--barrier-wave-size",
        type=int,
        default=32,
        help="barrier Commit 最大同时在途数（默认 32，避免 4U8G 一次性打满）",
    )
    g.add_argument(
        "--barrier-drain-timeout-s",
        type=float,
        default=10.0,
        help=(
            "barrier 压测窗口结束后用于收集 Commit 终态的最大等待时间；"
            "超出后记录 commit_timeout，不阻塞 Search 场景结束"
        ),
    )
    g = parser.add_argument_group("Load")
    g.add_argument(
        "--concurrency-steps",
        default=DEFAULT_CONCURRENCY_STEPS,
        help="每租户并发阶梯 (逗号分隔; 总并发=tenants*step)",
    )
    g.add_argument(
        "--scenarios",
        default="A,B,C,D",
        help=(
            "场景: A=纯读基线, B=纯写注入, C=读写混合, D=注入洪峰, "
            "F=故障注入(mock provider), S=饱和(commit barrier over reads), "
            "H=热租户偏斜(explicit barrier), K=容量(定速率混合), "
            "I=N×N 隔离探针 (逗号分隔, 可按需过滤如 A,D)"
        ),
    )
    g.add_argument(
        "--mix-ratios",
        default=DEFAULT_MIX_RATIOS,
        help="C 场景读:写比档位 (逗号分隔, READ:WRITE 格式)",
    )
    g.add_argument(
        "--burst-commits",
        type=int,
        default=32,
        help="D 场景洪峰写入的 commit 事务数",
    )
    g.add_argument(
        "--burst-window-s",
        type=float,
        default=10.0,
        help="D 场景洪峰目标窗口 (秒)",
    )
    g.add_argument(
        "--duration-s",
        type=float,
        default=60.0,
        help="每场景每并发档时长 (秒)",
    )
    g.add_argument(
        "--mode",
        choices=["max-throughput", "fixed-rps"],
        default="max-throughput",
        help="负载模式: 饱和打满 或 固定速率 (仅限读)",
    )
    g.add_argument("--rps", type=float, default=0.0, help="fixed-rps 模式的总读速率")
    g.add_argument(
        "--per-tenant-rps",
        type=float,
        default=0.0,
        help="固定每租户读速率；与 --rps 互斥，正式公平性场景使用",
    )
    g.add_argument(
        "--client-connection-error-abort-threshold",
        type=int,
        default=100,
        help=(
            "单场景累计本机 connection 错误达到该值后停止发压并标记 "
            "CLIENT_RESOURCE_EXHAUSTED；0=不熔断"
        ),
    )
    g.add_argument(
        "--commit-retry-max",
        type=int,
        default=0,
        help="commit 提交的可重试失败重试上限 (429+Retry-After/5xx/超时/连接; 0=不重试)",
    )
    g.add_argument(
        "--commit-retry-backoff-s",
        type=float,
        default=0.5,
        help="commit 提交重试的基础退避秒数 (429 优先用 Retry-After)",
    )
    g.add_argument(
        "--reconciliation-sessions",
        type=int,
        default=20,
        help="B 场景尾段消息对账的会话数上限 (默认 20)",
    )
    g = parser.add_argument_group("Formal load")
    g.add_argument(
        "--tenant-config",
        default="",
        help="租户凭据 JSON 路径（tenants 数组，每租户独立 auth_key；优先于 --auth-mode/--tenants）",
    )
    g.add_argument(
        "--commit-rpm",
        type=float,
        default=0.0,
        help="commit 固定速率（每分钟 commit 数；>0 时对写事务限速，K 场景用）",
    )
    g.add_argument(
        "--per-tenant-commit-rpm",
        type=float,
        default=0.0,
        help="固定每租户 Commit 速率；与 --commit-rpm 互斥，正式公平性场景使用",
    )
    g.add_argument(
        "--commit-barrier",
        action="store_true",
        help="启用 commit barrier 场景（S/H 的 commit 风暴）",
    )
    g.add_argument(
        "--commit-barrier-count",
        type=int,
        default=128,
        help="S 场景 commit barrier 的 commit 总数",
    )
    g.add_argument(
        "--commit-tenant-distribution",
        choices=["uniform", "zipf", "explicit"],
        default="uniform",
        help="commit barrier 的租户分布 (S 场景; H 固定 explicit)",
    )
    g.add_argument(
        "--commit-zipf-exponent",
        type=float,
        default=2.0,
        help="zipf 分布的指数 (rank 1..N 权重 1/rank^s)",
    )
    g.add_argument(
        "--commit-tenant-counts",
        default="",
        help="explicit 分布: 逗号分隔的每租户 commit 数（H 场景）",
    )
    g.add_argument(
        "--commit-barrier-waves",
        type=int,
        default=1,
        help="H 场景 commit barrier 波数",
    )
    g.add_argument(
        "--commit-barrier-cooldown-s",
        type=float,
        default=0.0,
        help="H 场景波间冷却秒数",
    )
    g.add_argument(
        "--isolation-markers-per-tenant",
        type=int,
        default=5,
        help="I 场景每租户 marker 数",
    )
    g = parser.add_argument_group("Observation")
    g.add_argument("--metrics-interval-s", type=float, default=2.0, help="/metrics 采样间隔 (秒)")
    g.add_argument("--cool-down-s", type=float, default=15.0, help="压测结束后的冷却采样时长 (秒)")
    g.add_argument("--no-metrics", action="store_true", help="不抓取 /metrics (外网未暴露时)")
    g.add_argument("--skip-health", action="store_true", help="跳过 /health 预检")
    g.add_argument("--degradation-threshold", type=float, default=2.0, help="P95 劣化倍数判定阈值")
    g = parser.add_argument_group("Run")
    g.add_argument(
        "--preflight-config",
        default="",
        help="engine 配置文件路径 (JSON); 传入则先过模型/配置预检门禁，失败即停",
    )
    g.add_argument(
        "--mock-provider-port",
        type=int,
        default=18090,
        help="F 场景故障注入 mock provider 的监听端口",
    )
    g.add_argument(
        "--quick",
        action="store_true",
        help="快速模式: 并发档 1,16 + 时长减为 1/4 (最短 5s)",
    )
    g.add_argument("--cleanup-identities", action="store_true", help="压测结束后删除 provision 租户（仅 provision 模式；static 模式拒绝）")
    g.add_argument("--out-dir", default="results", help="结果根目录")
    g.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    return parser


def _resolve_args(args: argparse.Namespace) -> dict[str, Any]:
    """Normalize CLI args; apply --quick overrides and basic validation."""
    scenarios = [part.strip().upper() for part in str(args.scenarios).split(",") if part.strip()]
    known = {"A", "B", "C", "D", "F", "S", "H", "K", "I"}
    unknown = [sid for sid in scenarios if sid not in known]
    if unknown:
        raise ValueError(f"--scenarios 未知场景: {', '.join(unknown)} (可选 A,B,C,D,F,S,H,K,I)")
    mix_ratios = parse_mix_ratios([part.strip() for part in str(args.mix_ratios).split(",")])
    concurrency_steps = parse_concurrency_steps(args.concurrency_steps)
    if args.quick:
        concurrency_steps = [step for step in concurrency_steps if step in (1, 16)]
        if not concurrency_steps:
            concurrency_steps = [1, 16]
        args.duration_s = max(5.0, args.duration_s / 4)
    if args.tenants < 1:
        raise ValueError("--tenants 必须 >= 1")
    if args.seed_concurrency < 1:
        raise ValueError("--seed-concurrency 必须 >= 1")
    if args.barrier_prepare_concurrency < 1:
        raise ValueError("--barrier-prepare-concurrency 必须 >= 1")
    if getattr(args, "barrier_wave_size", 32) < 1:
        raise ValueError("--barrier-wave-size 必须 >= 1")
    if getattr(args, "barrier_drain_timeout_s", 10.0) < 0:
        raise ValueError("--barrier-drain-timeout-s 必须 >= 0")
    commit_tenant_counts: list[int] | None = None
    if str(args.commit_tenant_counts or "").strip():
        try:
            commit_tenant_counts = [
                int(part.strip())
                for part in str(args.commit_tenant_counts).split(",")
                if part.strip()
            ]
        except ValueError as exc:
            raise ValueError(
                f"--commit-tenant-counts 需为逗号分隔的整数: {args.commit_tenant_counts}"
            ) from exc
        if not commit_tenant_counts or any(count < 0 for count in commit_tenant_counts):
            raise ValueError("--commit-tenant-counts 需为非负整数列表")
    tenant_specs: list[dict[str, Any]] | None = None
    if str(args.tenant_config or "").strip():
        try:
            tenant_specs = load_tenant_specs(args.tenant_config)
        except FileNotFoundError as exc:
            raise ValueError(f"--tenant-config 文件不存在: {args.tenant_config}") from exc
    seed_dataset_path: str | None = None
    if args.seed_source == "locomo" and not getattr(args, "skip_seed", False):
        dataset_path = (
            Path(args.dataset_path)
            if args.dataset_path
            else _PROJECT_ROOT / "benchmarks" / "locomo" / "data" / "locomo10.json"
        )
        if not dataset_path.is_file():
            raise ValueError(f"locomo 数据集不存在: {dataset_path}")
        if not str(args.sample_filter or "").strip():
            raise ValueError("--sample-filter 不能为空 (示例: conv-30 / conv-30,conv-41 / all)")
        seed_dataset_path = str(dataset_path)
    if args.auth_mode == "static" and args.cleanup_identities:
        raise ValueError(
            "--cleanup-identities 仅适用于 --auth-mode provision；"
            "static 模式复用预置身份，删除会连同生产租户数据一起清掉"
        )
    if args.burst_commits < 1:
        raise ValueError("--burst-commits 必须 >= 1")
    per_tenant_rps = float(getattr(args, "per_tenant_rps", 0.0) or 0.0)
    per_tenant_commit_rpm = float(
        getattr(args, "per_tenant_commit_rpm", 0.0) or 0.0
    )
    if args.mode == "fixed-rps" and args.rps <= 0:
        if per_tenant_rps <= 0:
            raise ValueError("--mode fixed-rps 需要 --rps 或 --per-tenant-rps > 0")
    if args.rps > 0 and args.mode == "max-throughput":
        raise ValueError("--rps 仅在 --mode fixed-rps 下生效")
    if args.rps > 0 and per_tenant_rps > 0:
        raise ValueError("--rps 与 --per-tenant-rps 互斥")
    if args.commit_rpm > 0 and per_tenant_commit_rpm > 0:
        raise ValueError("--commit-rpm 与 --per-tenant-commit-rpm 互斥")
    if per_tenant_rps < 0 or per_tenant_commit_rpm < 0:
        raise ValueError("每租户速率不能为负数")
    search_recall_ratio = float(getattr(args, "search_recall_ratio", 0.7))
    search_query_profile = str(
        getattr(args, "search_query_profile", "mixed") or "mixed"
    )
    if not 0.0 <= search_recall_ratio <= 1.0:
        raise ValueError("--search-recall-ratio 必须在 0~1 之间")
    if int(getattr(args, "seed_recall_probe_limit", 20)) < 0:
        raise ValueError("--seed-recall-probe-limit 必须 >= 0")
    if getattr(args, "client_connection_error_abort_threshold", 100) < 0:
        raise ValueError("--client-connection-error-abort-threshold 必须 >= 0")
    return {
        "scenario_ids": scenarios,
        "concurrency_steps": concurrency_steps,
        "mix_ratios": mix_ratios,
        "rps": args.rps if args.mode == "fixed-rps" else None,
        "per_tenant_rps": (
            per_tenant_rps
            if args.mode == "fixed-rps" and per_tenant_rps > 0
            else None
        ),
        "per_tenant_commit_rpm": (
            per_tenant_commit_rpm if per_tenant_commit_rpm > 0 else None
        ),
        "seed_dataset_path": seed_dataset_path,
        "commit_tenant_counts": commit_tenant_counts,
        "tenant_specs": tenant_specs,
        "effective_auth_mode": "tenant_config" if tenant_specs else args.auth_mode,
        "search_queries": [
            item.strip()
            for item in str(getattr(args, "search_queries", "") or "").split(",")
            if item.strip()
        ],
        "search_recall_queries": [
            item.strip()
            for item in str(getattr(args, "search_recall_queries", "") or "").split(",")
            if item.strip()
        ],
        "search_no_recall_queries": [
            item.strip()
            for item in str(getattr(args, "search_no_recall_queries", "") or "").split(",")
            if item.strip()
        ],
        "search_query_profile": search_query_profile,
        "search_recall_ratio": search_recall_ratio,
    }


def _probe_metrics(monitor: MetricsMonitor) -> dict[str, Any]:
    """One /metrics probe; returns availability info."""
    frame = monitor.sample()
    if frame is None:
        return {
            "metrics_available": False,
            "fetch_ok": monitor.fetch_ok,
            "fetch_failures": monitor.fetch_failures,
            "last_error": monitor.last_error,
        }
    return {
        "metrics_available": True,
        "fetch_ok": monitor.fetch_ok,
        "fetch_failures": monitor.fetch_failures,
        "series_count": len(frame.samples),
        "last_error": "",
    }


def _scene_metrics(
    scene: SceneRun,
    monitor: MetricsMonitor,
    t0: float,
    t1: float,
) -> dict[str, Any]:
    entry = scene_resource_summary(logger, monitor, t0, t1)
    return {**scene.to_dict(), "window_s": [round(t0, 3), round(t1, 3)], "resource": entry}


def _run_special_scene(
    gen: LoadGenerator,
    scene: SceneRun,
    tenants: list[Any],
    messages_per_session: int,
) -> SceneResult:
    """执行 S/H/K 特殊场景（commit barrier 家族），返回 SceneResult。

    S: 读线程打满 + 一次性 commit barrier（饱和）
    H: 多波 commit barrier（explicit 分布），波间 cooldown（热租户偏斜）
    K: 读+写线程按 --rps / --commit-rpm 固定速率（容量）
    """
    gen.reset_client_diagnostics()
    records: list[Any] = []
    started_wall = time.time()
    if scene.scene_id == "S":
        # Prepare real sessions before opening the measured contention window.
        # ``open``/``add`` may invoke the model-backed memory pipeline; doing
        # that inline with the Search window can consume the case timeout
        # before a single Commit has even arrived.
        prepared, barrier_records = gen.prepare_commit_barrier(
            scene, tenants, messages_per_session
        )
        stop = threading.Event()
        start_window = threading.Event()
        workers = max(1, len(tenants) * scene.per_tenant_conc)
        futures: list[Any] = []
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="perf-load"
        ) as pool:
            for index in range(workers):
                tenant = tenants[index % len(tenants)]
                futures.append(
                    pool.submit(
                        gen._read_loop,
                        stop,
                        tenant,
                        scene_key=scene.key,
                        step_conc=scene.per_tenant_conc,
                        start_event=start_window,
                    )
                )

            def run_commit_window() -> list[Any]:
                start_window.wait()
                return gen.commit_prepared_barrier(
                    scene,
                    prepared,
                    barrier_records,
                    poll_timeout_s=gen.barrier_drain_timeout_s,
                )

            # Keep Commit in a separate producer path. Both paths are released
            # together so the report can prove a real wall-clock overlap.
            barrier_pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="perf-commit-barrier"
            )
            try:
                barrier_future = barrier_pool.submit(run_commit_window)
                start_window.set()
                time.sleep(scene.duration_s)
                stop.set()
                wait(futures)
                barrier_records = barrier_future.result()
            finally:
                stop.set()
                barrier_pool.shutdown(wait=True)
        for future in futures:
            records.extend(future.result())
        records.extend(barrier_records)
    elif scene.scene_id == "H":
        waves = max(1, scene.barrier_waves)
        for wave in range(waves):
            records.extend(
                gen.run_commit_barrier(scene, tenants, messages_per_session)
            )
            if wave + 1 < waves and scene.barrier_cooldown_s > 0:
                time.sleep(scene.barrier_cooldown_s)
    else:  # K: 定速率混合（读 rps / 写 commit-rpm），复用常规 run_scene 混合路径
        result = gen.run_scene(scene, tenants, messages_per_session)
        records.extend(result.records)
    return SceneResult(
        scene_key=scene.key,
        records=records,
        wall_s=time.time() - started_wall,
    )


def _run_all_scenes(
    args: argparse.Namespace,
    resolved: dict[str, Any],
    generator: LoadGenerator,
    tenants: list[Any],
    monitor: MetricsMonitor,
) -> dict[str, Any]:
    """Execute the matrix; returns per-scene summaries + full records.

    Scene F (fault injection) and I (N×N isolation probe) are independent
    flows and are excluded from the concurrency matrix; they run separately
    in ``main``. S/H/K run inside the matrix via ``_run_special_scene``.
    """
    matrix_ids = [sid for sid in resolved["scenario_ids"] if sid not in ("F", "I")]
    # F/I are standalone probes. They must be runnable by themselves so a
    # short capability check does not end in an unrelated "empty matrix"
    # environment failure after the probe already produced evidence.
    if not matrix_ids:
        now = time.time()
        return {
            "records": [],
            "scenes": {},
            "scene_read_stats": {},
            "consistency": {"status": "skipped"},
            "reconciliation_data": [],
            "window_first_t0": now,
            "window_last_t1": now,
        }
    runs = expand_matrix(
        scenario_ids=matrix_ids,
        concurrency_steps=resolved["concurrency_steps"],
        mix_ratios=resolved["mix_ratios"],
        duration_s=args.duration_s,
        burst_commits=args.burst_commits,
        burst_window_s=args.burst_window_s,
        barrier_commits=args.commit_barrier_count,
        barrier_distribution=args.commit_tenant_distribution,
        barrier_zipf_exponent=args.commit_zipf_exponent,
        barrier_tenant_counts=resolved.get("commit_tenant_counts"),
        barrier_waves=args.commit_barrier_waves,
        barrier_cooldown_s=args.commit_barrier_cooldown_s,
    )
    if not runs:
        raise ValueError("场景矩阵为空: --scenarios 过滤后没有可运行的场景")
    logger.info("场景矩阵: %d 个运行单元", len(runs))

    all_records: list[Any] = []
    scenes: dict[str, Any] = {}
    scene_read_stats: dict[str, dict[str, Any]] = {}
    consistency = {"status": "skipped"}
    reconciliation_data: list[Any] = []
    first_t0: float | None = None
    last_t1: float | None = None

    for scene in runs:
        name = SCENARIO_NAMES[scene.scene_id]
        t0 = time.time()
        if first_t0 is None:
            first_t0 = t0
        logger.info("===> 场景 %s (%s) 并发/租户=%d 时长=%.0fs", scene.key, name, scene.per_tenant_conc, scene.duration_s)
        try:
            if scene.scene_id in ("S", "H", "K"):
                result: SceneResult = _run_special_scene(
                    generator, scene, tenants, args.messages_per_session
                )
            else:
                result = generator.run_scene(scene, tenants, args.messages_per_session)
        except BaseException as exc:  # noqa: BLE001 - 场景失败不中断矩阵，失败场景记入报告
            logger.exception("场景 %s 执行失败（继续后续场景）", scene.key)
            scenes[scene.key] = {
                "scene_id": scene.scene_id,
                "per_tenant_conc": scene.per_tenant_conc,
                "duration_s": scene.duration_s,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            continue
        t1 = time.time()
        last_t1 = t1
        wall = max(result.wall_s, 1e-6)
        per_op = summarize_records(result.records, wall_s=wall)

        entry = _scene_metrics(scene, monitor, t0, t1)
        entry["ops"] = per_op
        if result.burst_start_s is not None and result.burst_end_s is not None:
            entry["burst_window_s"] = [round(result.burst_start_s, 3), round(result.burst_end_s, 3)]
        scenes[scene.key] = entry
        all_records.extend(result.records)

        read = (per_op.get(scene.key) or {}).get("read") or {}
        scene_read_stats[scene.key] = read
        writes = sum(
            ((per_op.get(scene.key) or {}).get(op) or {}).get("count", 0)
            for op in ("open", "add", "commit_submit", "commit_done")
        )
        logger.info(
            "    完成: 读=%d qps=%.1f p95=%.1fms 写op=%d 错误=%d",
            read.get("count", 0),
            read.get("qps", 0) or 0,
            read.get("p95_ms", 0) or 0,
            writes,
            read.get("errors_total", 0),
        )
        # Reconcile each scene while its successful write candidates are still
        # scoped to that scene. This covers normal writes and barrier writes
        # (S/H), and prevents a later workload from hiding a gap.
        if generator._reconciliation_candidates:
            reconciliation_data.extend(
                generator.run_reconciliation(
                    tenants, max_sessions=args.reconciliation_sessions
                )
            )
        # B 场景尾段立即做写后读一致性检查。
        if scene.scene_id == "B":
            check_records = generator.run_consistency_checks(
                tenants,
                scene_key=scene.key,
                step_conc=scene.per_tenant_conc,
            )
            all_records.extend(check_records)
            consistency = consistency_summary(check_records)

    return {
        "records": all_records,
        "scenes": scenes,
        "scene_read_stats": scene_read_stats,
        "consistency": consistency,
        "reconciliation_data": reconciliation_data,
        "window_first_t0": first_t0 if first_t0 is not None else time.time(),
        "window_last_t1": last_t1 if last_t1 is not None else time.time(),
    }


def _build_degradation(
    scenes: dict[str, Any],
    scene_reads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Degradation of C/D read latency against the same-step A baseline."""
    degradation: dict[str, Any] = {}
    a_stats_by_conc: dict[str, dict[str, Any]] = {}
    for key, stats in scene_reads.items():
        if key.startswith("A@"):
            a_stats_by_conc[key.split("@")[1]] = stats
    fallback_baseline = next(iter(a_stats_by_conc.values()), None)
    for key, stats in scene_reads.items():
        if not key.startswith(("C:", "D@")):
            continue
        conc = key.rsplit("@", 1)[-1]
        baseline = a_stats_by_conc.get(conc, fallback_baseline)
        factors = degradation_factor(baseline, stats)
        if any(value is not None for value in factors.values()):
            degradation[f"{key}_vs_A@{conc}"] = factors
    return degradation


def _build_isolation(
    records: list[Any],
    scenes: dict[str, Any],
    scene_reads: dict[str, dict[str, Any]],
    tenants: list[Any],
    *,
    degradation_threshold: float,
) -> dict[str, Any]:
    """D-scene burst-window read isolation (same/cross tenant) vs A baseline."""
    burst_tenant_idx = tenants[0].idx if tenants else 0
    result: dict[str, Any] = {}
    for scene_key, entry in scenes.items():
        if entry.get("scene_id") != "D":
            continue
        burst = entry.get("burst_window_s")
        if not burst:
            continue
        conc = entry.get("per_tenant_conc")
        a_stats = (scene_reads.get(f"A@{conc}") or {})
        result[scene_key] = isolation_summary(
            records,
            t0_ms=burst[0] * 1000,
            t1_ms=burst[1] * 1000,
            burst_tenant_idx=burst_tenant_idx,
            baseline_p95=a_stats.get("p95_ms"),
            degradation_threshold=degradation_threshold,
        )
    return result


def _build_signals(
    args: argparse.Namespace,
    scenes: dict[str, Any],
    scene_reads: dict[str, dict[str, Any]],
    degradation: dict[str, Any],
    monitor: MetricsMonitor,
    durability: dict[str, Any],
    fairness: dict[str, Any],
    resources: dict[str, Any],
) -> dict[str, Any]:
    """Signal set for the four EchoMem feature guarantees."""
    signals: list[str] = []
    for scene_key, entry in scenes.items():
        burst_window = entry.get("burst_window_s")
        if entry.get("scene_id") != "D" or not burst_window:
            continue
        t0, t1 = burst_window
        factor = (degradation.get(f"{scene_key}_vs_A@{entry['per_tenant_conc']}") or {}).get("p95")
        if factor is not None and factor >= args.degradation_threshold:
            signals.append(f"{scene_key}: 写洪峰窗口读 P95 劣化 {factor}x (阈值 {args.degradation_threshold}x)")
        engine_delta = monitor.counter_delta(RECALL_ENGINE_CALLS, t0 - 1, t1 + 1)
        if engine_delta is not None and engine_delta <= 0:
            signals.append(f"{scene_key}: 洪峰窗口 engine_calls 增量≈0 而延迟变化 → 疑似锁/排他竞争")
        elif engine_delta is not None:
            signals.append(f"{scene_key}: 洪峰窗口 engine_calls 增量={int(engine_delta)} → 疑似资源竞争")
        read = scene_reads.get(scene_key) or {}
        if read.get("errors_total", 0) > 0:
            signals.append(f"{scene_key}: 洪峰窗口读错误 {read['errors_total']} 次")
        inflight = (entry.get("resource") or {}).get("http_inflight_max")
        if inflight is not None and scene_key.startswith("D@"):
            conc = int(scene_key.rsplit("@", 1)[-1])
            total_workers = conc * args.tenants
            if inflight >= total_workers * 0.9:
                signals.append(f"{scene_key}: inflight 峰值 {int(inflight)} 接近总并发 {total_workers} → 请求堆积")

    # 特性 1: commit 异步 + 成功保证 + 不阻塞检索（读劣化信号已在上方）
    violations = durability.get("guarantee_violations", 0)
    if violations > 0:
        signals.append(
            f"commit 成功保证被违反: 202 已接受但最终失败 {violations} 个事务 "
            f"(accepted_done_failed={durability.get('accepted_done_failed')}, "
            f"accepted_done_other={durability.get('accepted_done_other')})"
        )
    rejected = durability.get("submit_rejected_total", 0)
    if rejected > 0:
        rejected_detail = durability.get("submit_rejected_breakdown", {})
        signals.append(f"commit 提交阶段被拒绝 {rejected} 次 (不重试, 分类={rejected_detail})")

    # 特性 2: 租户公平性
    for scene_key, fair in fairness.items():
        if fair.get("balanced") is False:
            signals.append(
                f"{scene_key}: 租户间读 P95 最大/最小 {fair.get('p95_max_min_ratio')}x "
                f"(≥3x) → 租户延迟不均衡，疑似单租户占满资源"
            )

    # 特性 3: 无内存泄漏（RSS 时间趋势）
    slope = (resources.get("rss_trend") or {}).get("slope_mb_per_min")
    trend = resources.get("rss_trend") or {}
    if (
        slope is not None
        and slope >= RSS_LEAK_SLOPE_MB_PER_MIN
        and float(trend.get("window_s") or 0.0) >= 60.0
    ):
        signals.append(
            f"疑似内存泄漏: RSS 上升斜率 {slope} MB/min "
            f"(阈值 {RSS_LEAK_SLOPE_MB_PER_MIN} MB/min)"
        )
    return {"signals_found": signals, "threshold": args.degradation_threshold}


def _build_resource_totals(
    monitor: MetricsMonitor,
    t0: float,
    t1: float,
    settle_t: float,
) -> dict[str, Any]:
    """Whole-run CPU/RSS/queue resource summary (missing series -> None)."""
    rss_series = monitor.gauge_series(RESIDENT_MEMORY, t0, t1)
    baseline_frame = monitor._frame_at_or_before(t0)
    settle_frame = monitor._frame_at_or_before(settle_t)
    baseline = None
    if baseline_frame is not None:
        try:
            baseline = max(value for _, value in baseline_frame.samples.get(RESIDENT_MEMORY, []))
        except ValueError:
            baseline = None
    peak = max((value for _, value in rss_series), default=None)
    settled = None
    if settle_frame is not None:
        try:
            settled = max(value for _, value in settle_frame.samples.get(RESIDENT_MEMORY, []))
        except ValueError:
            settled = None

    def mb(value: float | None) -> float | None:
        return round(value / 1024 / 1024, 2) if value is not None else None

    wall = t1 - t0
    cpu_series = monitor.cpu_utilization_series(t0, t1)
    cpu_delta = monitor.counter_delta(CPU_SECONDS, t0, t1)
    cpu_mean = round(cpu_delta / wall * 100, 2) if cpu_delta is not None and wall > 0 else None
    rss_peak = peak if peak is not None else baseline

    return {
        "cpu_util_mean_percent": cpu_mean,
        "rss_baseline_mb": mb(baseline),
        "rss_peak_mb": mb(rss_peak),
        "rss_settled_mb": mb(settled),
        "rss_unsettled_mb": (
            mb(settled - baseline) if settled is not None and baseline is not None else None
        ),
        "rss_trend": rss_trend_mb_per_min(rss_series),
        "threads_max": monitor.gauge_max(PROCESS_THREADS, t0, t1),
        "commit_queue_max": monitor.gauge_max(COMMIT_QUEUE_DEPTH, t0, t1),
        "http_inflight_max": monitor.gauge_max(HTTP_INFLIGHT, t0, t1),
        "cpu_util_max_percent": (
            round(max(value for _, value in cpu_series), 2) if cpu_series else None
        ),
        "metrics_frames": len(monitor.frames),
    }


def _verdict_measurement_summary(feature_key: str, meas: dict[str, Any]) -> str:
    """终端一行提示：每特性结论背后的关键量化值。"""
    if feature_key == "commit_guarantee":
        dur = meas.get("durability") or {}
        prec = meas.get("retrieval_precedence") or {}
        parts = []
        if dur.get("commit_success_rate") is not None:
            parts.append(f"成功率 {dur['commit_success_rate']:.2%}")
        latency = (dur.get("completion_latency_ms") or {}).get("p95_ms")
        if latency is not None:
            parts.append(f"commit 完成 P95 {latency}ms")
        if prec.get("worst_p95_ratio") is not None:
            parts.append(f"洪峰读 P95 劣化 {prec['worst_p95_ratio']}x")
        return "，".join(parts)
    if feature_key == "tenant_fairness":
        if meas.get("slowest_tenant_p95_ms") is None:
            return "单租户/无租户分组数据"
        return (
            f"最慢租户 P95 {meas['slowest_tenant_p95_ms']}ms，比最快多等 "
            f"{meas.get('slowest_waits_extra_ms')}ms（max/min {meas.get('p95_max_min_ratio')}x）"
        )
    if feature_key == "memory_leak":
        if meas.get("slope_mb_per_min") is None:
            return "采样不足"
        return (
            f"RSS 斜率 {meas['slope_mb_per_min']} MB/min"
            f"（预计每小时 {meas.get('projected_growth_mb_per_hour')} MB）"
        )
    if feature_key == "resource_timeline":
        return (
            f"CPU 均值 {meas.get('cpu_util_mean_percent')}% / 峰值 "
            f"{meas.get('cpu_util_max_percent')}%，RSS 峰值 {meas.get('rss_peak_mb')}MB"
        )
    return ""


def _print_terminal_summary(
    scenes: dict[str, Any],
    degradation: dict[str, Any],
    signals: dict[str, Any],
    resources: dict[str, Any],
    durability: dict[str, Any],
    fairness: dict[str, Any],
    verdicts: dict[str, Any],
) -> None:
    print()
    print("=" * 72)
    print("性能压测摘要")
    print("=" * 72)
    for key in sorted(scenes):
        ops = scenes[key].get("ops", {})
        read = ((ops.get(key) or {}).get("read") or {})
        commit = ((ops.get(key) or {}).get("commit_done") or {})
        print(
            f"  {key:<14} 读 {read.get('count', 0):>6} 次  "
            f"QPS {read.get('qps', 0) or 0:>7.1f}  "
            f"P50 {read.get('p50_ms') or 0:>7.1f}  "
            f"P95 {read.get('p95_ms') or 0:>7.1f}  "
            f"P99 {read.get('p99_ms') or 0:>7.1f} ms  "
            f"错 {read.get('errors_total', 0)}  "
            f"| 提交完成 {commit.get('count', 0)}  错误率 {commit.get('error_rate', 0) or 0:.2%}"
        )
    if degradation:
        print("  劣化倍数 (相对同并发档 A 基线):")
        for key, factors in degradation.items():
            print(
                f"    {key:<22} P50 {factors['p50']}  P95 {factors['p95']}  P99 {factors['p99']}"
            )
    print("  特性检查:")
    print(
        f"    [1] commit 持久性: 接受 {durability.get('submit_ok_total')} 个, "
        f"最终完成 {durability.get('accepted_done_ok')} 个, "
        f"接受后失败 {durability.get('guarantee_violations')} 个, "
        f"提交阶段拒绝 {durability.get('submit_rejected_total')} 次 (不重试)"
    )
    unbalanced = [
        (key, fair.get("p95_max_min_ratio"))
        for key, fair in fairness.items()
        if fair.get("balanced") is False
    ]
    if unbalanced:
        detail = "; ".join(f"{key}={ratio}x" for key, ratio in unbalanced)
        print(f"    [2] 租户公平性: 不均衡场景 {detail}")
    else:
        scene_with_ratio = next(
            ((key, fair.get("p95_max_min_ratio")) for key, fair in fairness.items() if fair.get("p95_max_min_ratio") is not None),
            (None, None),
        )
        ratio_text = f" (最大 max/min P95 {scene_with_ratio[1]}x)" if scene_with_ratio[1] is not None else ""
        print(f"    [2] 租户公平性: 均衡{ratio_text}")
    slope = (resources.get("rss_trend") or {}).get("slope_mb_per_min")
    slope_text = f"{slope} MB/min" if slope is not None else "不可判定(采样不足)"
    print(
        f"    [3] 内存趋势: RSS 斜率 {slope_text}, "
        f"峰值 {resources.get('rss_peak_mb')}MB, 冷却后未回落 {resources.get('rss_unsettled_mb')}MB"
    )
    print(
        f"    [4] 资源时间线: CPU/RSS/线程/队列 → {_PROJECT_ROOT / 'performance' / 'results'} (report.html)"
    )
    print("  特性结论:")
    verdict_labels = {"PASS": "通过", "FAIL": "不通过", "INCONCLUSIVE": "数据不足"}
    for feature_key, label in FEATURE_LABELS.items():
        entry = verdicts["features"].get(feature_key) or {}
        detail = _verdict_measurement_summary(
            feature_key, entry.get("measurements") or {}
        )
        suffix = f"  [{detail}]" if detail else ""
        print(
            f"    {label}: {verdict_labels.get(entry.get('verdict'), entry.get('verdict'))}{suffix}"
        )
    print(f"    总体结论: {verdict_labels.get(verdicts.get('overall'), verdicts.get('overall'))}")
    if signals["signals_found"]:
        print("  阻塞/干扰信号:")
        for signal in signals["signals_found"]:
            print(f"    - {signal}")
    print(
        f"  资源: CPU 均值 {resources.get('cpu_util_mean_percent')}%  "
        f"RSS 基线 {resources.get('rss_baseline_mb')}MB "
        f"峰值 {resources.get('rss_peak_mb')}MB "
        f"未回落 {resources.get('rss_unsettled_mb')}MB"
    )


def _write_failure_report(
    out_dir: Path,
    args: argparse.Namespace,
    resolved: dict[str, Any],
    error: BaseException,
    *,
    monitor: MetricsMonitor | None = None,
    server_info: dict[str, Any] | None = None,
    partial: dict[str, Any] | None = None,
) -> None:
    """压测失败兜底：仍生成一份失败报告，写入失败原因与已收集的部分数据。

    调用时机：预检 / prepare / 场景矩阵 / 故障注入 / 汇总 / 写文件任一阶段
    抛出未处理异常时。报告 status=failed，顶部渲染失败横幅，已收集的场景
    数据与请求记录照常写入，便于人工核查「失败发生在哪、损失了什么」。
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    scenes = (partial or {}).get("scenes") or {}
    records = (partial or {}).get("records") or []
    summary: dict[str, Any] = {
        "generator": "performance/run_stress.py",
        "status": "failed",
        "error": f"{type(error).__name__}: {error}",
        "started_at": now,
        "finished_at": now,
        "config": {
            **vars(args),
            "scenario_ids": resolved["scenario_ids"],
            "concurrency_steps": resolved["concurrency_steps"],
            "mix_ratios": [f"{r}:{w}" for r, w in resolved["mix_ratios"]],
        },
        "server": server_info or {},
        "scenes": scenes,
        "signals": {"signals_found": [], "threshold": args.degradation_threshold},
        "feature_verdicts": {"features": {}, "overall": "env_error"},
    }
    try:
        write_config(out_dir, summary["config"])
        if records:
            write_requests_csv(out_dir, records)
        write_summary(out_dir, summary)
        save_html(out_dir, build_html(summary, {}))
    except BaseException as write_exc:  # noqa: BLE001 - 兜底写入自身失败也要可见
        logger.exception("失败报告写入异常: %s", write_exc)
    logger.error("压测失败，失败报告已生成: %s", out_dir)


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    resolved = _resolve_args(args)

    out_root = Path(args.out_dir)
    if not out_root.is_absolute():
        out_root = _PROJECT_ROOT / "performance" / out_root
    out_dir = out_root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("结果目录: %s", out_dir)

    monitor = MetricsMonitor(
        args.echomem_url,
        interval_s=args.metrics_interval_s,
        timeout_s=5.0,
    )
    server_info: dict[str, Any] = {
        "base_url": args.echomem_url,
        "metrics_requested": not args.no_metrics,
    }
    all_data: dict[str, Any] | None = None
    try:
        # -- 模型/配置预检门禁（失败即停，归类环境/依赖失败） ---------------------
        preflight_result: dict[str, Any] | None = None
        if args.preflight_config:
            logger.info("预检门禁: %s", args.preflight_config)
            preflight_result = run_preflight(
                args.preflight_config, timeout_s=max(1.0, args.timeout_s)
            )
            if not preflight_result["ok"]:
                raise RuntimeError(
                    f"预检门禁失败（环境/依赖，非被测代码）: {preflight_result['error']}"
                )

        if not args.no_metrics:
            monitor.start()

        if not args.skip_health:
            probe = EchoMemClient(args.echomem_url, timeout_s=args.timeout_s, max_retries=0)
            health = probe.health()
            logger.info("预检 /health: %s", health)
        if not args.no_metrics:
            server_info.update(_probe_metrics(monitor))

        preparer = TenantPreparer(
            args.echomem_url,
            auth_mode=args.auth_mode,
            auth_key=args.auth_key,
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            agent_id=args.agent_id,
            tenants=args.tenants,
            timeout_s=args.timeout_s,
            label_prefix="perf",
            tenant_specs=resolved.get("tenant_specs"),
            seed_concurrency=args.seed_concurrency,
        )
        try:
            tenants = preparer.prepare(
                0 if args.skip_seed else args.seed_sessions_per_tenant,
                args.messages_per_session,
                args.commit_poll_timeout_s,
                locomo_batches=(
                    load_locomo_seed_batches(
                        resolved["seed_dataset_path"], args.sample_filter
                    )
                    if args.seed_source == "locomo" and not args.skip_seed
                    else None
                ),
            )
            legacy_queries = list(resolved.get("search_queries") or [])
            configured_recall = list(resolved.get("search_recall_queries") or [])
            configured_no_recall = list(resolved.get("search_no_recall_queries") or [])
            fallback_queries = legacy_queries or ["hello"]
            for tenant in tenants:
                recall_candidates = list(
                    configured_recall
                    or tenant.recall_queries
                    or tenant.queries
                    or legacy_queries
                )
                no_recall_queries = list(
                    configured_no_recall
                    or tenant.no_recall_queries
                    or fallback_queries
                )
                if args.skip_seed or getattr(args, "reuse_existing_data", False):
                    if not recall_candidates:
                        recall_candidates = list(fallback_queries)
                    if not no_recall_queries:
                        no_recall_queries = list(fallback_queries)
                recall_queries, recall_probe = verify_recall_query_pool(
                    tenant.client,
                    recall_candidates,
                    top_k=args.top_k,
                    timeout_s=args.timeout_s,
                    limit=args.seed_recall_probe_limit,
                    expected_terms_by_query=tenant.recall_expected_terms,
                )
                tenant.recall_probe = recall_probe
                requires_recall = args.search_query_profile == "recall-only" or (
                    args.search_query_profile == "mixed"
                    and args.search_recall_ratio > 0
                )
                if requires_recall and not recall_queries:
                    if not args.allow_unverified_search:
                        raise RuntimeError(
                            "没有可验证命中的 recall query；已完成 Commit 但无法证明记忆可召回。"
                            f" tenant={tenant.idx} probe={recall_probe}"
                        )
                    logger.warning(
                        "tenant_idx=%d 无可验证记忆，降级为 unverified Search 负载；"
                        " recall/热缓存结论将为 INCONCLUSIVE probe=%s",
                        tenant.idx,
                        recall_probe,
                    )
                queries, query_kinds = build_search_query_pool(
                    recall_queries=recall_queries,
                    no_recall_queries=no_recall_queries,
                    profile=args.search_query_profile,
                    recall_ratio=args.search_recall_ratio,
                )
                if not queries:
                    queries = list(fallback_queries)
                    query_kinds = ["fallback"] * len(queries)
                tenant.queries = queries
                tenant.query_kinds = query_kinds
                logger.info(
                    "tenant_idx=%d search pool profile=%s recall=%d/%d verified "
                    "no_recall=%d total=%d",
                    tenant.idx,
                    args.search_query_profile,
                    len(recall_queries),
                    len(recall_candidates),
                    len(no_recall_queries),
                    len(queries),
                )
        except BaseException:
            # prepare 阶段失败也要清掉已 provision 的租户（seed 中途失败时
            # preparer 仍持有它们的 client）；清完再向上抛。
            if args.cleanup_identities:
                logger.info("prepare 失败，清理已 provision 的压测租户...")
                preparer.cleanup()
            raise
        if not args.no_metrics:
            monitor.sample()  # RSS baseline frame right after seeding

        generator = LoadGenerator(
            top_k=args.top_k,
            timeout_s=args.timeout_s,
            commit_poll_timeout_s=args.commit_poll_timeout_s,
            rps=resolved["rps"] or None,
            per_tenant_rps=resolved.get("per_tenant_rps"),
            commit_rpm=args.commit_rpm,
            per_tenant_commit_rpm=(
                args.per_tenant_commit_rpm
                if args.per_tenant_commit_rpm > 0
                else None
            ),
            commit_retry_max=args.commit_retry_max,
            commit_retry_backoff_s=args.commit_retry_backoff_s,
            barrier_prepare_concurrency=args.barrier_prepare_concurrency,
            barrier_wave_size=args.barrier_wave_size,
            barrier_drain_timeout_s=args.barrier_drain_timeout_s,
            client_connection_error_abort_threshold=(
                args.client_connection_error_abort_threshold
            ),
        )

        try:
            try:
                # -- 场景 I: N×N 隔离探针（矩阵之前的一次性步骤，同 F 的独立流） ---
                isolation_probe_records: list[Any] = []
                if "I" in resolved["scenario_ids"]:
                    logger.info("===> 场景 I N×N 隔离探针（矩阵之前执行）")
                    isolation_probe_records, isolation_probe_run = (
                        generator.run_nxn_isolation_probe(
                            tenants,
                            markers_per_tenant=args.isolation_markers_per_tenant,
                        )
                    )
                    logger.info("    I 探针摘要: %s", isolation_probe_run)

                all_data = _run_all_scenes(args, resolved, generator, tenants, monitor)
                if isolation_probe_records:
                    all_data["records"] = isolation_probe_records + all_data["records"]

                # -- 场景 F: 故障注入（mock provider，独立流程，不并入并发矩阵） -----
                fault_result: dict[str, Any] | None = None
                if "F" in resolved["scenario_ids"]:
                    logger.info("===> 场景 F 故障注入（mock provider，只改配置不改服务端）")
                    provider = MockProvider(port=args.mock_provider_port)
                    provider.start()
                    try:
                        sequence = run_fault_sequence(
                            provider,
                            timeout_s=min(max(args.timeout_s, 0.5), 10.0),
                        )
                    finally:
                        provider.stop()
                    fault_result = fault_injection_summary(sequence)

                cool_down_until = time.time() + args.cool_down_s
                logger.info("冷却观测 %.0fs（观测内存回落与 commit 队列排空）", args.cool_down_s)
                while time.time() < cool_down_until:
                    if not args.no_metrics:
                        monitor.sample()
                    time.sleep(min(1.0, max(0.0, cool_down_until - time.time())))
                settle_t = time.time()
            finally:
                if not args.no_metrics:
                    monitor.stop()

            t_first = all_data["window_first_t0"]
            t_last = all_data["window_last_t1"]
            resources = _build_resource_totals(monitor, t_first, t_last, settle_t)
            # RSS 归一校正：扣除按注入消息字节归一估计的索引增长（正常增长不计为泄漏）
            injected = injected_bytes_series(all_data["records"])
            net_rss = (
                rss_normalized_series(
                    monitor.gauge_series(RESIDENT_MEMORY, t_first, t_last), injected
                )
                if not args.no_metrics
                else []
            )
            total_injected_bytes = injected[-1][1] if injected else 0
            raw_settled_mb = resources.get("rss_settled_mb")
            resources["rss_normalized"] = {
                "injected_mb": round(total_injected_bytes / 1024 / 1024, 2),
                "net_trend": rss_trend_mb_per_min(net_rss),
                "net_peak_mb": (
                    round(max((value for _, value in net_rss), default=0) / 1024 / 1024, 2)
                    if net_rss
                    else None
                ),
                "net_settled_mb": (
                    round(raw_settled_mb - total_injected_bytes / 1024 / 1024, 2)
                    if raw_settled_mb is not None
                    else None
                ),
            }
            isolation = _build_isolation(
                all_data["records"],
                all_data["scenes"],
                all_data["scene_read_stats"],
                tenants,
                degradation_threshold=args.degradation_threshold,
            )
            degradation = _build_degradation(all_data["scenes"], all_data["scene_read_stats"])
            durability = commit_durability(all_data["records"])
            fairness = tenant_fairness(
                all_data["records"],
                wall_s=max(0.0, t_last - t_first),
            )
            signals = _build_signals(
                args,
                all_data["scenes"],
                all_data["scene_read_stats"],
                degradation,
                monitor,
                durability,
                fairness,
                resources,
            )

            def _redact(value: str) -> str:
                return f"***configured*** ({len(value)} chars)" if value else ""

            summary: dict[str, Any] = {
                "generator": "performance/run_stress.py",
                "status": "completed",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "config": {
                    **vars(args),
                    "auth_key": _redact(args.auth_key),
                    "effective_auth_mode": preparer.identity_mode(),
                    "tenant_config_count": len(resolved.get("tenant_specs") or []),
                    "scenario_ids": resolved["scenario_ids"],
                    "per_tenant_rps": resolved.get("per_tenant_rps"),
                    "per_tenant_commit_rpm": (
                        args.per_tenant_commit_rpm
                        if args.per_tenant_commit_rpm > 0
                        else None
                    ),
                    "concurrency_steps": resolved["concurrency_steps"],
                    "mix_ratios": [f"{r}:{w}" for r, w in resolved["mix_ratios"]],
                },
                "data_scale": {
                    "tenants": len(tenants),
                    "sessions_per_tenant": args.seed_sessions_per_tenant,
                    "skip_seed": args.skip_seed,
                    "messages_per_session": args.messages_per_session,
                    "queries_per_tenant": [len(tenant.queries) for tenant in tenants],
                    "query_source": (
                        "seeded"
                        if not (args.skip_seed or getattr(args, "reuse_existing_data", False))
                        else (
                            "configured_fallback"
                            if resolved.get("search_queries")
                            else "default_fallback"
                        )
                    ),
                    "query_profile": args.search_query_profile,
                    "search_recall_ratio": args.search_recall_ratio,
                    "search_evidence_status": (
                        "inconclusive_unverified"
                        if args.allow_unverified_search
                        and not any(
                            tenant.recall_probe.get("verified", 0)
                            for tenant in tenants
                        )
                        else "verified_or_not_applicable"
                    ),
                    "tenant_details": [tenant.to_dict() for tenant in tenants],
                },
                "server": server_info,
                "scenes": {
                    key: entry for key, entry in all_data["scenes"].items()
                },
                "degradation": degradation,
                "signals": signals,
                "consistency": all_data["consistency"],
                "resources": resources,
                "commit_durability": durability,
                "tenant_fairness": fairness,
                "commit_latency": commit_completion_latency(all_data["records"]),
                "write_retry": retry_summary(all_data["records"]),
                "saturation": saturation_summary(all_data["records"]),
                "hot_tenant": hot_tenant_summary(all_data["records"]),
                "isolation_probe": isolation_probe_summary(all_data["records"]),
                "reconciliation": reconcile_messages(all_data["reconciliation_data"]),
                "search_quality": search_quality_summary(
                    all_data["records"],
                    burst_windows=[
                        (entry["burst_window_s"][0] * 1000, entry["burst_window_s"][1] * 1000)
                        for entry in all_data["scenes"].values()
                        if entry.get("scene_id") == "D" and entry.get("burst_window_s")
                    ],
                ),
                "isolation": isolation,
                "error_type_validation": error_type_validation(
                    all_data["records"], fault_result
                ),
                "fault_injection": fault_result,
                "preflight": preflight_result,
                "client_diagnostics": generator.client_diagnostics(),
            }
            summary["feature_verdicts"] = evaluate_features(summary)

            write_config(out_dir, summary["config"])
            write_requests_csv(out_dir, all_data["records"])
            if not args.no_metrics:
                write_metrics_csv(out_dir, monitor)
            else:
                write_metrics_csv(out_dir, MetricsMonitor(args.echomem_url))  # empty frames
            write_summary(out_dir, summary)

            chart_series = {
                "rss_mb": [
                    (ts, round(value / 1024 / 1024, 2))
                    for ts, value in monitor.gauge_series(RESIDENT_MEMORY, t_first, t_last)
                ],
                "threads": monitor.gauge_series(PROCESS_THREADS, t_first, t_last),
                "commit_queue": monitor.gauge_series(COMMIT_QUEUE_DEPTH, t_first, t_last),
                "inflight": monitor.gauge_series(HTTP_INFLIGHT, t_first, t_last),
                "cpu_percent": monitor.cpu_utilization_series(t_first, t_last),
            }
            save_html(out_dir, build_html(summary, chart_series))
            _print_terminal_summary(
                all_data["scenes"],
                degradation,
                signals,
                resources,
                durability,
                fairness,
                summary["feature_verdicts"],
            )
        finally:
            # 压测租户清理：prepare/场景/报告任一阶段异常也执行
            # （preparer 记录全部已 provision 的租户，seed 中途失败也会被清；
            #  static 模式复用预置身份，组合已在参数校验拒绝）
            if args.cleanup_identities:
                logger.info("清理压测租户（--cleanup-identities）...")
                preparer.cleanup()
    except BaseException as exc:  # noqa: BLE001 - 压测失败也生成报告（含失败原因与已收集部分）
        if not args.no_metrics:
            monitor.stop()
        partial = None
        if all_data is not None:
            partial = {
                "scenes": all_data.get("scenes") or {},
                "records": all_data.get("records") or [],
            }
        _write_failure_report(
            out_dir,
            args,
            resolved,
            exc,
            server_info=server_info,
            partial=partial,
        )
        raise
    logger.info("完成! 结果目录: %s", out_dir)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(2)
