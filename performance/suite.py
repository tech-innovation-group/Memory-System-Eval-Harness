"""通用套件能力：case 收敛、case → Profile 解析、单 case 与整套执行。

``apply_quick`` 做 quick 收敛（duration / barrier count 双 cap、
quick_commit_rpm 覆盖、sessions 压到 1）；``build_case_profile`` 把 case
翻译为 ``profile.Profile``（arrival 按任务映射 fixed_rps，params 带基础键
与 target 专属参数回调）；``summarize_case_records`` 把一批 RequestRecord
汇总为 case 契约摘要（``{"metrics": {...}}``，search/commit/fairness/
per_tenant），op 映射与锚点判定可参数化；``run_case`` 用 ``engine.Engine``
进程内执行单个 case（load_scene + Engine.run），把汇总结果经
``report.write_records`` 写成 case 目录下的 summary.json / records.csv，
Engine 异常记 ``ENV_ERROR`` 不中断套件；``run_suite`` 编排整套正式套件
（prepare → preflight → 灌种 → 逐 case → acceptance → suite.json），
case 选择/Profile 构造/单 case 执行/preflight/seed/acceptance 均可由
target 挂钩。target 可通过 ``summarize`` / ``write_evidence`` 挂自己的
汇总扩展（如 details/parameters）与证据 CSV。
"""

from __future__ import annotations

import json
import logging
import shutil
import statistics
import threading
from dataclasses import dataclass, field
from pathlib import Path

from performance.memory_leak import diagnose_runs
from typing import Any, Callable

from performance.engine import Engine, load_scene
from performance.profile import ArrivalSpec, LoadSpec, Profile, TargetSpec, TenantSpec
from performance.records import RequestRecord
from performance.report import write_records
from performance.stats import percentile
from performance.util import now_iso, run_command, scale_counts_to_cap

log = logging.getLogger(__name__)

# case 超时后调用 Engine.stop() 后等待 worker 退出的有界时长（秒）。
# stop 会关闭活跃连接中断在途请求，worker 正常场景在该窗口内退出；
# 确认窗口到期仍存活视为不可回收，run_case 持久化 TIMEOUT 产物后抛错
# 中止套件，绝不在旧 worker 存活时推进下一 case。
_STOP_CONFIRM_S = 30.0


def _seconds(stage_ms: float) -> float:
    return stage_ms / 1000.0


_FS_UNSAFE_CHARS = str.maketrans({c: "_" for c in '<>:"/\\|?*'})


def _fs_safe_label(label: str) -> str:
    """把 case 标签转为文件系统安全的目录名（Windows 不允许 ``:`` 等字符）。"""
    return label.translate(_FS_UNSAFE_CHARS)


def _rate_limited(record: RequestRecord) -> bool:
    """限流样本：http_4xx 且带 Retry-After 或 reason_code。"""
    return (
        record.status == "error"
        and record.error_type == "http_4xx"
        and (record.retry_after_s is not None or record.reason_code != "")
    )


@dataclass
class QuickSpec:
    """quick 收敛参数：时长上限、barrier 计数上限与是否保留灌种。"""

    duration_cap_s: float = 15.0
    barrier_count_cap: int = 32
    include_seed: bool = False


def apply_quick(case: dict, quick: QuickSpec) -> dict:
    """返回 quick 收敛后的 case 副本（原 case 不被修改）。"""
    result = dict(case)
    result["duration_s"] = min(float(case["duration_s"]), quick.duration_cap_s)
    if case.get("commit_barrier"):
        cap = quick.barrier_count_cap
        scenario_cap = int(case.get("quick_barrier_count_cap") or 0)
        if scenario_cap > 0:
            cap = min(cap, scenario_cap)
        result["commit_barrier_count"] = min(
            int(case.get("commit_barrier_count", 32)), cap
        )
        counts = case.get("commit_tenant_counts")
        if counts and result["commit_barrier_count"] < sum(int(v) for v in counts):
            result["commit_tenant_counts"] = scale_counts_to_cap(
                [int(v) for v in counts], result["commit_barrier_count"]
            )
    if case.get("quick_commit_rpm") is not None:
        result["commit_rpm"] = case["quick_commit_rpm"]
    result["sessions_per_tenant"] = 1
    return result


def build_case_profile(
    case: dict,
    *,
    scene_path: Path,
    base_url: str,
    tenant_count: int,
    auth_headers: dict,
    queries: list[str],
    quick: QuickSpec | None = None,
    extra_params: Callable[[dict[str, Any], dict], None] | None = None,
) -> Profile:
    """case → Profile：arrival 按任务固定 rps，params 带场景专属参数。

    quick 非 None 时先 ``apply_quick`` 收敛。worker 数取 case 显式
    ``search_workers``/``commit_workers``，否则按 rps 取整（至少 1）。
    ``scene_path`` 指向场景文件，``queries`` 是检索探测用 query 列表；
    ``extra_params`` 在基础 params 之后回调，用于注入场景专属参数
    （如 barrier/burst）。
    """
    if quick is not None:
        case = apply_quick(case, quick)
    scene = load_scene(scene_path)
    search_rps = float(case.get("search_rps") or 0.0)
    commit_rps = float(case.get("commit_rpm") or 0.0) / 60.0

    read_workers = case.get("search_workers") or 0
    if read_workers <= 0 and search_rps > 0:
        read_workers = max(1, round(search_rps))
    write_workers = case.get("commit_workers") or 0
    if write_workers <= 0 and commit_rps > 0:
        write_workers = max(1, round(commit_rps))
    if case.get("read_only") or "write" not in scene.tasks:
        write_workers = 0
        commit_rps = 0.0

    arrival: dict[str, ArrivalSpec] = {}
    mix: dict[str, int] = {}
    if "read" in scene.tasks:
        mix["read"] = max(1, read_workers)
        if search_rps > 0:
            arrival["read"] = ArrivalSpec(model="fixed_rps", rps=search_rps,
                scope=case.get("arrival_scope", "global"), start_s=float(case.get("search_start_s", 0)),
                end_s=case.get("arrival_end_s"))
    if "write" in scene.tasks:
        mix["write"] = max(1, write_workers) if write_workers > 0 else 0
        if commit_rps > 0:
            arrival["write"] = ArrivalSpec(model="fixed_rps", rps=commit_rps,
                scope=case.get("arrival_scope", "global"), start_s=float(case.get("commit_start_s", 0)),
                end_s=case.get("arrival_end_s"))

    params: dict[str, Any] = {
        "top_k": int(case.get("top_k", 5)),
        "messages_per_session": int(case.get("messages_per_session", 3)),
        "sessions_per_tenant": int(case.get("sessions_per_tenant", 1)),
        "queries": list(queries),
        "commit_poll_timeout_s": float(case.get("commit_poll_timeout_s", 600)),
    }
    if extra_params is not None:
        extra_params(params, case)

    return Profile(
        name=case["label"],
        target=TargetSpec(
            base_url=base_url.rstrip("/"),
            headers=dict(auth_headers),
            read_timeout_s=30.0,
        ),
        load=LoadSpec(
            workers=max(1, read_workers + write_workers),
            duration_s=float(case["duration_s"]),
            mix=mix or None,
            arrival=arrival,
        ),
        tenants=[TenantSpec(name=f"tenant-{index}") for index in range(tenant_count)],
        params=params,
    )


def summarize_case_records(
    records: list[RequestRecord],
    *,
    search_op: str = "read",
    commit_submit_op: str = "commit_submit",
    commit_done_op: str = "commit_done",
    is_anchor: Callable[[str], bool] | None = None,
) -> dict:
    """records → case 契约摘要（只含 ``metrics``）。

    search = ``search_op`` 记录，commit = ``commit_submit_op`` /
    ``commit_done_op`` 记录；per_tenant 按 tenant_idx 分组；延迟统一为秒
    （stage_ms/1000）。``is_anchor`` 为 None 时 quality_asserted 记 0。
    """
    reads = [r for r in records if r.op == search_op]
    ok_reads = [r for r in reads if r.status == "ok"]
    read_latencies = [_seconds(r.stage_ms) for r in ok_reads]

    submits = [r for r in records if r.op == commit_submit_op]
    dones = [r for r in records if r.op == commit_done_op]
    ok_dones = [r for r in dones if r.status == "ok"]

    completed_by_tenant: dict[str, int] = {}
    for record in ok_dones:
        key = str(record.tenant_idx)
        completed_by_tenant[key] = completed_by_tenant.get(key, 0) + 1

    def _pct(values: list[float], p: float) -> float | None:
        value = percentile(values, p)
        return round(value, 3) if value is not None else None

    search = {
        "submitted": len(reads),
        "succeeded": len(ok_reads),
        "errors": len(reads) - len(ok_reads),
        "success_rate": (len(ok_reads) / len(reads)) if reads else None,
        "rate_limited_count": sum(1 for r in reads if _rate_limited(r)),
        "quality_asserted": (
            sum(1 for r in reads if is_anchor(r.query)) if is_anchor is not None else 0
        ),
        "quality_failures": sum(1 for r in reads if not r.quality_ok),
        "latency": {
            "mean_s": (
                round(statistics.mean(read_latencies), 3) if read_latencies else None
            ),
            "p50_s": _pct(read_latencies, 50),
            "p95_s": _pct(read_latencies, 95),
            "p99_s": _pct(read_latencies, 99),
        },
    }
    submitted = len(submits)
    completed = len(ok_dones)
    commit = {
        "submitted": submitted,
        "completed": completed,
        "failed": sum(1 for r in dones if r.status != "ok")
        + sum(1 for r in submits if r.status != "ok"),
        "success_rate": (completed / submitted) if submitted else None,
        "rate_limited_count": sum(1 for r in submits if _rate_limited(r)),
    }

    per_tenant: dict[str, dict[str, Any]] = {}
    for tenant_idx in sorted({str(r.tenant_idx) for r in records}):
        tenant_reads = [r for r in ok_reads if str(r.tenant_idx) == tenant_idx]
        tenant_submits = [
            r for r in submits if r.status == "ok" and str(r.tenant_idx) == tenant_idx
        ]
        tenant_dones = [
            _seconds(r.stage_ms) for r in ok_dones if str(r.tenant_idx) == tenant_idx
        ]
        if not tenant_reads and not tenant_submits and not tenant_dones:
            continue
        entry: dict[str, Any] = {}
        if tenant_submits or tenant_dones:
            entry["commit"] = {
                "submitted": len(tenant_submits),
                "completed": len(tenant_dones),
            }
            if tenant_dones:
                entry["commit"]["completion"] = {"p50_s": _pct(tenant_dones, 50)}
        if tenant_reads:
            entry["search"] = {
                "submitted": sum(1 for r in reads if str(r.tenant_idx) == tenant_idx),
                "succeeded": len(tenant_reads),
                "latency": {
                    "p50_s": _pct([_seconds(r.stage_ms) for r in tenant_reads], 50),
                    "p95_s": _pct([_seconds(r.stage_ms) for r in tenant_reads], 95),
                },
            }
        per_tenant[tenant_idx] = entry

    return {
        "metrics": {
            "search": search,
            "commit": commit,
            "fairness": {"commit_completed_per_tenant": completed_by_tenant},
            "per_tenant": per_tenant,
        },
    }


def run_case(
    case: dict,
    profile: Profile,
    *,
    scene_path: Path,
    case_dir: Path,
    timeout_s: float | None = None,
    summarize: Callable[[list[RequestRecord]], dict] | None = None,
    write_evidence: Callable[[Path, list[RequestRecord]], None] | None = None,
) -> dict:
    """执行单个 case：load_scene + Engine.run，写产物并返回 run dict。

    Engine 异常记 ``ENV_ERROR``；``timeout_s`` 用守护线程包裹 Engine.run，
    超时记 ``TIMEOUT``、调用 ``Engine.stop()`` 并有界确认 worker 退出。
    确认窗口（``_STOP_CONFIRM_S``）到期后线程仍存活时，先持久化真实的
    ``status`` / ``runner_timeout``（供 resume 只复用明确完成的 case），
    再抛 ``RuntimeError`` 中止套件——绝不在旧 worker 存活时推进下一 case。
    ``summarize`` 缺省为通用 ``summarize_case_records``；
    ``write_evidence`` 在 summary.json/records.csv 之后写 target 专属
    证据文件。
    """
    scene = load_scene(scene_path)
    runner_timeout = False
    status = "completed"
    stubborn = False
    records: list[RequestRecord] = []
    run_result = None
    try:
        if timeout_s and timeout_s > 0:
            holder: dict[str, Any] = {}
            engine = Engine(profile, scene)

            def _execute() -> None:
                holder["result"] = engine.run()

            thread = threading.Thread(target=_execute, daemon=True)
            thread.start()
            thread.join(timeout_s)
            if thread.is_alive():
                runner_timeout = True
                status = "TIMEOUT"
                engine.stop()
                thread.join(_STOP_CONFIRM_S)
                if thread.is_alive():
                    stubborn = True
                    log.error(
                        "case %s 超时后 worker 在确认窗口内未退出，"
                        "持久化 TIMEOUT 产物后中止套件",
                        case["label"],
                    )
            # A stopped run still owns real evidence; timeout changes status,
            # not the denominator. Never read a result while its thread is live.
            if not thread.is_alive():
                run_result = holder["result"]
                records = run_result.records
        else:
            run_result = Engine(profile, scene).run()
            records = run_result.records
    except Exception:
        status = "ENV_ERROR"
    summarize_fn = summarize or summarize_case_records
    summary = summarize_fn(records)
    summary["status"] = status
    summary["runner_timeout"] = runner_timeout
    summary["run_clock"] = {"started_wall_ms": getattr(run_result, "started_wall_ms", None),
                            "load_duration_s": profile.load.duration_s}
    write_records(case_dir, records, summary)
    if write_evidence is not None:
        write_evidence(case_dir, records)
    if stubborn:
        raise RuntimeError(
            f"case {case['label']} 超时后 worker 仍存活，拒绝推进下一 case"
        )
    return {
        "scenario": case["label"],
        "scenario_label": case["label"],
        "scene": case["scene"],
        "repetition": 1,
        "policy": "server-observe",
        "status": status,
        "duration_s": float(profile.load.duration_s),
        "case_timeout_s": float(timeout_s or 0),
        "runner_timeout": runner_timeout,
        "output_dir": str(case_dir.resolve()),
        "summary": summary,
    }


@dataclass
class SeedContext:
    """灌种结果里单个租户的上下文：身份、凭据与检索 query 列表。"""

    tenant_id: str
    auth_key: str
    queries: list[str]
    agent_id: str = "default"
    user_id: str = "default"
    account_id: str = "default"
    query_cases: dict[str, dict] = field(default_factory=dict)


class SeedPreparationError(RuntimeError):
    """A seed failure with deliberately public, credential-free evidence."""

    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.public_evidence = evidence


def _run_prepare_command(command: str) -> dict:
    """prepare_command 经 bash -lc 执行；Windows 无 bash 时降级为 NOT_RUN。"""
    if shutil.which("bash") is None:
        return {
            "status": "NOT_RUN",
            "command": command,
            "reason": "bash not available (Windows); command not executed",
        }
    result = run_command(["bash", "-lc", command], timeout_s=1800.0)
    return {
        "status": "ok" if result["status"] == "PASS" else "INCONCLUSIVE",
        "command": command,
        "returncode": result["returncode"],
        "stdout_tail": result["stdout"][-2000:],
        "stderr_tail": result["stderr"][-4000:],
    }


def _finalize_suite(manifest: dict, suite_dir: Path) -> dict:
    """写 suite.json / acceptance.json 并返回 manifest（acceptance 缺省 NOT_RUN）。"""
    if "acceptance" not in manifest:
        manifest["acceptance"] = {"status": "NOT_RUN", "reason": "no evaluator"}
    runs = manifest.get("runs") or []
    if runs:
        manifest["memory_leak"] = diagnose_runs(runs)
    (suite_dir / "suite.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (suite_dir / "acceptance.json").write_text(
        json.dumps(manifest["acceptance"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _load_completed_run(
    case: dict, case_dir: Path, timeout_s: float | None
) -> dict | None:
    """从已有 case 目录重建 run dict；非明确完成时返回 None。

    resume 模式用它跳过已完成的 case：run 的状态信息只在 suite.json 里，
    case 目录唯一可靠信号是 run_case 收尾写下的 summary.json（含 metrics
    契约摘要与持久化的 ``status`` / ``runner_timeout``）。仅当状态为
    ``completed`` 且未超时（``runner_timeout`` 非真）时才重建 run 并入
    manifest；TIMEOUT / ENV_ERROR 的产物一律视为未完成，resume 重跑该
    case。重建字段与 ``run_case`` 的返回结构一致，保证 O1-O7 求值证据
    完整。
    """
    summary_path = case_dir / "summary.json"
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(summary, dict):
        return None
    if summary.get("status") != "completed" or summary.get("runner_timeout"):
        return None
    return {
        "scenario": case["label"],
        "scenario_label": case["label"],
        "scene": case["scene"],
        "repetition": 1,
        "policy": "server-observe",
        "status": "completed",
        "duration_s": float(case.get("duration_s") or 0),
        "case_timeout_s": float(timeout_s or 0),
        "runner_timeout": False,
        "output_dir": str(case_dir.resolve()),
        "summary": summary,
    }


def run_suite(
    profile: dict,
    *,
    suite_dir: Path,
    profile_name: str,
    base_url: str = "",
    timeout_s: float = 120.0,
    scenarios: list[str] | None = None,
    quick: QuickSpec | None = None,
    resume: bool = False,
    select_cases: Callable[[str, list[str] | None], list[dict]],
    build_profile: Callable[[dict, str, int, QuickSpec | None], Profile],
    run_case: Callable[[dict, Profile, Path, float | None], dict],
    preflight: Callable[[str], dict] | None = None,
    seed: Callable[[str, str, int, int, int], tuple[list[SeedContext], dict]]
    | None = None,
    evaluate: Callable[[dict], dict] | None = None,
) -> dict:
    """执行单个 instance profile 的正式套件，返回 manifest。

    profile = instance-profiles JSON 里的单个 profile dict（name/base_url/
    tenant_config/preflight_config/allow_partial_tenants/quick_include_seed/
    prepare_command 等）。``select_cases``/``build_profile``/``run_case``
    必填，分别提供 case 选择、case → Profile（含 quick 收敛）与单 case
    执行；``preflight`` 收 preflight 配置路径（为空返回 NOT_RUN 条目，非空
    且 ``ok`` 非真时提前返回）；``seed`` 收 (base_url, tenant_config,
    max_tenants, sessions, messages) 返回 (contexts, seed 摘要)；``evaluate``
    收 manifest 返回 acceptance 摘要。各阶段失败按 prepare/preflight/seed
    段记录并提前返回（仍写 suite.json / acceptance.json）。

    ``resume`` 为 True 时跳过已完成的 case（case 目录的 summary.json
    标记 ``status=completed`` 且 ``runner_timeout=false``），把其历史 run
    重建后合并进 manifest，从第一个未完成的 case 继续执行；suite.json
    因此同时包含历史与本次的 runs。超时/异常的 case 产物不会被跳过。
    """
    suite_dir = Path(suite_dir)
    suite_dir.mkdir(parents=True, exist_ok=True)
    base_url = (base_url or str(profile.get("base_url") or "")).rstrip("/")
    manifest: dict[str, Any] = {
        "created_at": now_iso(),
        "base_url": base_url,
        "profile": profile_name,
        "instance_profile": str(profile.get("name") or ""),
        "tenant_config": str(profile.get("tenant_config") or ""),
        "preflight_config": str(profile.get("preflight_config") or ""),
        "allow_partial_tenants": bool(profile.get("allow_partial_tenants")),
        "metrics_enabled": bool(profile.get("metrics_enabled", True)),
        "resource_profile": profile.get("resource_profile") or {},
        "output_root": str(suite_dir.resolve()),
        "scenarios": [],
        "repeats": 1,
        "policies": ["server-observe"],
        "duration_cap_s": quick.duration_cap_s if quick else 0.0,
        "server_observation_mode": True,
        "client_admission_enabled": False,
        "probe_artifacts": {},
        "runs": [],
    }
    cases = select_cases(profile_name, scenarios)
    manifest["scenarios"] = [case["label"] for case in cases]

    def progress(stage: str, scenario: str = "") -> None:
        payload = {"updated_at": now_iso(), "stage": stage, "current_scenario": scenario,
                   "planned_cases": len(cases), "finished_cases": len(manifest["runs"])}
        temporary = suite_dir / "progress.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(suite_dir / "progress.json")

    def finish() -> dict:
        """写盘收尾：acceptance 缺失时先经 evaluate 钩子求值。"""
        if evaluate is not None and "acceptance" not in manifest:
            manifest["acceptance"] = evaluate(manifest)
        progress("finished")
        return _finalize_suite(manifest, suite_dir)

    prepare_command = profile.get("prepare_command")
    if prepare_command:
        progress("prepare")
        prepare = _run_prepare_command(str(prepare_command))
        manifest["prepare"] = prepare
        if prepare["status"] != "ok":
            return finish()

    preflight_config = str(profile.get("preflight_config") or "")
    progress("preflight")
    if preflight is not None:
        manifest["preflight"] = preflight(preflight_config)
        if preflight_config and not manifest["preflight"].get("ok"):
            return finish()
    else:
        manifest["preflight"] = {"status": "NOT_RUN", "config": preflight_config}

    contexts = None
    seed_sessions = int(
        profile.get("seed_sessions")
        or max(int(case.get("sessions_per_tenant", 1)) for case in cases)
    )
    include_seed = bool(profile.get("quick_include_seed"))
    if quick is not None and not include_seed and not quick.include_seed:
        seed_sessions = min(seed_sessions, 1)
    seed_messages = int(
        profile.get("seed_messages")
        or max(int(case.get("messages_per_session", 3)) for case in cases)
    )
    tenant_config = profile.get("tenant_config")
    if tenant_config and seed is not None:
        progress("seed")
        try:
            contexts, seed_summary = seed(
                base_url,
                str(tenant_config),
                max(int(case["tenants"]) for case in cases),
                seed_sessions,
                seed_messages,
            )
            manifest["seed"] = seed_summary
        except Exception as exc:
            manifest["seed"] = {"status": "ENV_ERROR", "error": str(exc)}
            if isinstance(exc, SeedPreparationError):
                manifest["seed"]["evidence"] = exc.public_evidence
            return finish()
    else:
        manifest["seed"] = {"status": "skipped", "reason": "no tenant_config"}

    for case in cases:
        progress("load", str(case["label"]))
        case_dir = suite_dir / _fs_safe_label(case["label"])
        if resume:
            completed_run = _load_completed_run(case, case_dir, timeout_s)
            if completed_run is not None:
                manifest["runs"].append(completed_run)
                continue
        tenant_count = case["tenants"]
        case_profile = build_profile(case, base_url, tenant_count, quick)
        if contexts:
            usable = contexts[:tenant_count] if tenant_count > 0 else contexts
            case_profile.tenants = [
                TenantSpec(
                    name=ctx.tenant_id or f"tenant-{index}",
                    headers={"X-Auth-Key": ctx.auth_key} if ctx.auth_key else {},
                )
                for index, ctx in enumerate(usable)
            ]
            case_profile.params["queries"] = [
                query for ctx in usable for query in ctx.queries
            ]
            case_profile.params["tenant_query_pools"] = {
                str(index): list(ctx.queries) for index, ctx in enumerate(usable)
            }
            case_profile.params["tenant_query_cases"] = {
                str(index): dict(ctx.query_cases) for index, ctx in enumerate(usable)
            }
            case_profile.params["tenant_identities"] = {
                str(index): {"agent_id": ctx.agent_id, "user_id": ctx.user_id,
                             "account_id": ctx.account_id, "tenant_id": ctx.tenant_id}
                for index, ctx in enumerate(usable)
            }
        run = run_case(
            case, case_profile,
            case_dir=case_dir, timeout_s=timeout_s,
        )
        manifest["runs"].append(run)
        progress("load", str(case["label"]))

    if evaluate is not None:
        manifest["acceptance"] = evaluate(manifest)
    return finish()
