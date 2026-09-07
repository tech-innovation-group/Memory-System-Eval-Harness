"""objective-suite CLI：多 instance profile 的正式验收编排入口（EchoMem target）。

读取 instance-profiles JSON，逐 profile 执行正式套件（``run_suite``，进程内
Engine + 灌种 + acceptance 求值）与探针编排（``run_configured_probes``），
汇总七项目标 O1-O7，写 objective-suite.json 并渲染 objective-suite.html。
``--skip-run`` 只读已有 suite.json 生成报告，不重新发送压测请求。

调用方式：``python run.py --target echomem``（由 performance 顶层分发）。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from performance.targets.echomem.acceptance.objectives import (
    INCONCLUSIVE,
    OBJECTIVES,
    PASS,
    objective_statuses,
)
from performance.targets.echomem.orchestrator.probes import run_configured_probes
from performance.targets.echomem.orchestrator.report import (
    write_objective_suite_html,
)
from performance.targets.echomem.orchestrator.runner import run_suite
from performance.targets.echomem.orchestrator.suites import (
    QUICK_SCENARIOS,
    QuickSpec,
)
from performance.profile import expand_env_in
from performance.util import (
    acquire_output_lock,
    load_env_file,
    now_iso,
    read_json,
    resolve_relative_to,
)

__all__ = [
    "build_parser",
    "load_profiles",
    "main",
]


def load_profiles(path: Path) -> list[dict[str, Any]]:
    """读取 {"profiles":[...]} 或裸 list，过滤非 dict/无名条目。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else payload
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles config must contain a non-empty profiles list")
    return [item for item in profiles if isinstance(item, dict) and item.get("name")]


def _formal_run_counts(suite: dict[str, Any]) -> tuple[int, int]:
    """返回 (completed, submitted) 计数，两者不等价。

    一次 run 可以发请求但以 TIMEOUT/ENV_ERROR 结束；O2 的 profile 级证据只
    统计显式完成的 run，submitted 仅作为诊断量保留。
    """
    completed = 0
    submitted = 0
    for item in suite.get("runs") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        if not isinstance(summary, dict):
            continue
        metrics = summary.get("metrics")
        if not isinstance(metrics, dict):
            continue
        search = metrics.get("search")
        commit = metrics.get("commit")
        search = search if isinstance(search, dict) else {}
        commit = commit if isinstance(commit, dict) else {}
        if int(search.get("submitted") or 0) <= 0 and int(commit.get("submitted") or 0) <= 0:
            continue
        submitted += 1
        if str(item.get("status") or "").lower() == "completed":
            completed += 1
    return completed, submitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echomem-acceptance",
        description="Run EchoMem objective acceptance suite",
    )
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--profile", default="", help="只运行一个 profile；默认运行全部")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--quick", action="store_true", help="bounded smoke matrix")
    parser.add_argument("--six-metrics", action="store_true",
                        help="4U8G six-metric acceptance with strict evidence gates")
    parser.add_argument("--check-only", action="store_true",
                        help="six-metrics preparation check; no seeding, fault injection or container restart")
    parser.add_argument("--scenarios", default="", help="覆盖场景列表，逗号分隔")
    parser.add_argument("--quick-duration-cap-s", type=float, default=30.0)
    parser.add_argument("--quick-case-timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--quick-barrier-count-cap",
        type=int,
        default=32,
        help="quick 模式的 barrier Commit 上限，默认 32",
    )
    parser.add_argument(
        "--quick-include-seed",
        action="store_true",
        help="quick 保留配置的灌种会话数；默认只缩小会话数，不跳过灌种",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "跳过结果目录中已完成的场景（case 目录有 summary.json），"
            "从第一个未完成场景继续；历史已完成的 run 合并进最终报告"
        ),
    )
    parser.add_argument("--timeout-s", type=float, default=7200.0)
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="只根据已有 suite.json 生成总报告",
    )
    parser.add_argument(
        "--suite-path",
        type=Path,
        default=None,
        help="配合 --skip-run 读取已有 formal suite.json；不重新发送压测请求",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=(
            "加载 KEY=VALUE 环境文件供探针使用；"
            "适合服务器 Docker env 文件，密钥不会写入报告"
        ),
    )
    return parser


def _resolve_profile(profile: dict[str, Any], profiles_path: Path) -> dict[str, Any]:
    """把 profile 引用的文件路径解析为绝对路径（相对清单目录），并展开
    ``${ENV:-default}`` 占位符（instance profile 是 JSON，不经 load_profile）。"""
    profiles_dir = profiles_path.expanduser().resolve().parent
    profile = expand_env_in(profile)
    return {
        **profile,
        "tenant_config": resolve_relative_to(
            str(profile.get("tenant_config") or ""), profiles_dir
        ),
        "preflight_config": resolve_relative_to(
            str(profile.get("preflight_config") or ""), profiles_dir
        ),
        "fault_plan": resolve_relative_to(
            str(profile.get("fault_plan") or ""), profiles_dir
        ),
    }


def _scenario_list(text: str) -> list[str] | None:
    """逗号分隔文本 → label 列表；空文本返回 None（跑 profile 全量）。"""
    items = [item.strip() for item in text.split(",") if item.strip()]
    return items or None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.six_metrics and args.quick:
        parser.error("--six-metrics cannot use quick-mode evidence")
    if args.six_metrics and args.resume:
        parser.error("six-metrics requires a fresh output directory until resume provenance is verified")
    if args.check_only and (not args.six_metrics or args.skip_run or args.scenarios):
        parser.error("--check-only requires --six-metrics and cannot use --skip-run/--scenarios")
    if args.six_metrics and args.scenarios:
        parser.error("--six-metrics cannot omit scenarios; use --quick for diagnostic subsets")

    child_env = dict(os.environ)
    if args.env_file is not None:
        try:
            child_env.update(load_env_file(args.env_file.expanduser().resolve()))
        except OSError as exc:
            parser.error(f"无法读取 --env-file: {exc}")
    os.environ.update(child_env)

    try:
        profiles = load_profiles(args.profiles)
    except ValueError as exc:
        parser.error(str(exc))
    if args.profile:
        profiles = [item for item in profiles if str(item["name"]) == args.profile]
    if not profiles:
        parser.error("没有匹配的 profile")

    if args.six_metrics and not args.skip_run and args.out_dir.exists() and any(args.out_dir.iterdir()):
        parser.error("six-metrics requires a new or empty output directory; existing evidence was not changed")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_lock = None
    if not args.skip_run:
        try:
            output_lock = acquire_output_lock(args.out_dir)
        except RuntimeError as exc:
            parser.error(str(exc))

    output_profiles: list[dict[str, Any]] = []
    try:
        for profile in profiles:
            name = str(profile["name"])
            suite_dir = args.out_dir / name
            suite_dir.mkdir(parents=True, exist_ok=True)
            profile = _resolve_profile(profile, args.profiles)
            if args.six_metrics:
                from performance.targets.echomem.acceptance.six_metrics import configure_profile
                try:
                    profile = configure_profile(profile, live=not args.skip_run)
                except (ValueError, OSError, KeyError) as exc:
                    parser.error(str(exc))
            if args.check_only:
                from performance.targets.echomem.acceptance.readiness import check_readiness
                from performance.targets.echomem.acceptance.preflight import run_preflight
                from performance.targets.echomem.orchestrator.suites import six_metric_cases
                readiness = check_readiness(profile)
                models = run_preflight(profile["preflight_config"], required_kinds=("llm", "embedding")) if readiness["ok"] else {"ok": False, "status": "NOT_RUN"}
                plan = six_metric_cases(profile.get("capacity_levels"))
                result = {"readiness": readiness, "models": models, "cases": plan,
                          "fault_cases": 4 * 2 * int(profile["fault_isolation"].get("repeats", 3)),
                          "ok": readiness["ok"] and models["ok"],
                          "load_window_seconds": sum(c["duration_s"] for c in plan),
                          "note": "Preparation only; not six-metric acceptance. No seed, fault or restart executed."}
                (suite_dir / "readiness.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                output_profiles.append(result)
                print(suite_dir / "readiness.json")
                continue
            command_result: dict[str, Any] = {}
            suite_path = suite_dir / "suite.json"

            if not args.skip_run:
                scenarios = (
                    _scenario_list(args.scenarios)
                    if args.scenarios
                    else (_scenario_list(QUICK_SCENARIOS) if args.quick else None)
                )
                quick = (
                    QuickSpec(
                        duration_cap_s=args.quick_duration_cap_s,
                        barrier_count_cap=args.quick_barrier_count_cap,
                        include_seed=args.quick_include_seed,
                    )
                    if args.quick
                    else None
                )
                # quick 在 4U8G 上用 bounded 目录；完整目录含长时报告/容量
                # case，适合正式验收但会让诊断运行看起来卡住。
                profile_name = "4u8g" if args.quick and name.upper() == "4U8G" else "complete"
                if args.six_metrics:
                    profile_name = "six-metrics"
                case_timeout = args.quick_case_timeout_s if args.quick else args.timeout_s
                suite = run_suite(
                    profile,
                    suite_dir=suite_dir,
                    quick=quick,
                    profile_name=profile_name,
                    base_url=str(profile.get("base_url") or ""),
                    timeout_s=case_timeout,
                    scenarios=scenarios,
                    resume=args.resume,
                )
            else:
                configured_suite = str(
                    args.suite_path
                    if args.suite_path is not None
                    else profile.get("suite_path") or profile.get("suite") or ""
                ).strip()
                if configured_suite:
                    suite_path = Path(configured_suite).expanduser().resolve()
                    command_result["run"] = {
                        "status": "PASS",
                        "mode": "read-only-audit",
                        "reason": "只读取已有 suite.json，不重新发送压测请求",
                    }
                suite = read_json(suite_path)

            tenant_config = (
                read_json(Path(profile["tenant_config"])) if profile["tenant_config"] else {}
            )
            if args.skip_run or (args.six_metrics and (
                not suite.get("resource_evidence") or not (suite.get("preflight") or {}).get("ok")
            )):
                probe_artifacts, probe_commands = {}, []
            else:
                visibility = (suite.get("seed") or {}).get("visibility", [])
                if visibility and isinstance(profile.get("fault_isolation"), dict):
                    profile["fault_isolation"] = {
                        **profile["fault_isolation"],
                        "queries": {r["tenant_id"]: r["marker"] for r in visibility},
                    }
                probe_artifacts, probe_commands = run_configured_probes(
                    profile,
                    base_url=str(profile.get("base_url") or ""),
                    suite_dir=suite_dir,
                    auth_headers={},
                    tenant_config=tenant_config,
                    quick=args.quick,
                    timeout_s=args.timeout_s,
                )
            suite = {**suite, **probe_artifacts}
            if not args.skip_run:
                suite_path.write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if args.six_metrics:
                from performance.targets.echomem.acceptance.six_metrics import evaluate_six, write_report
                six = evaluate_six(suite, profile)
                (suite_dir / "six-metrics.json").write_text(
                    json.dumps(six, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                write_report(six, suite_dir / "six-metrics.html")
            command_result["probes"] = probe_commands

            completed_runs, submitted_runs = _formal_run_counts(suite)
            profile_execution_status = (
                "completed"
                if completed_runs > 0
                else str(command_result.get("run", {}).get("status") or "not_run")
            )
            output_profiles.append({
                **profile,
                "name": name,
                "suite": str(suite_path),
                "profile_execution_status": profile_execution_status,
                "completed_runs": completed_runs,
                "submitted_runs": submitted_runs,
                "probe_artifacts": probe_artifacts,
                "six_metric_status": six["status"] if args.six_metrics else None,
                "memory_leak": suite.get("memory_leak"),
                **probe_artifacts,
                "command": command_result,
                "objectives": objective_statuses({
                    **suite,
                    "profile_name": name,
                    "instance_profiles": [{
                        "name": name,
                        "status": profile_execution_status,
                        "completed_runs": completed_runs,
                    }],
                }),
            })
    finally:
        if output_lock is not None:
            output_lock.close()

    if args.check_only:
        return 0 if all(p["ok"] for p in output_profiles) else 2

    completed_profile_records = [
        {
            "name": str(profile.get("name") or ""),
            "status": str(profile.get("profile_execution_status") or ""),
            "completed_runs": int(profile.get("completed_runs") or 0),
            "submitted_runs": int(profile.get("submitted_runs") or 0),
        }
        for profile in output_profiles
    ]
    completed_profile_count = sum(
        1
        for item in completed_profile_records
        if item["status"] == "completed" and item["completed_runs"] > 0
    )
    for profile in output_profiles:
        for objective in profile.get("objectives") or []:
            if objective.get("id") == "O2":
                objective["status"] = PASS if completed_profile_count >= 2 else INCONCLUSIVE
                objective["reason"] = (
                    "至少两种规格均有真实完成场景，可比较调度与 config"
                    if completed_profile_count >= 2
                    else "当前只完成单一规格或没有真实场景结果；仅有 profile 配置不能证明多规格调度"
                )
                objective["evidence"] = completed_profile_records
    result = {
        "created_at": now_iso(),
        "profiles": output_profiles,
        "objectives": OBJECTIVES,
        "instance_profiles": completed_profile_records,
        "multi_spec_completed_count": completed_profile_count,
    }
    (args.out_dir / "objective-suite.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_objective_suite_html(result, args.out_dir / "objective-suite.html")
    print(args.out_dir / "objective-suite.html")
    if args.six_metrics and any(p.get("six_metric_status") != PASS for p in output_profiles):
        return 1 if any(p.get("six_metric_status") == "FAIL" for p in output_profiles) else 2
    return 0 if output_profiles else 2
