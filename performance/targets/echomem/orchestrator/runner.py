"""单 instance profile 的正式套件执行（echomem 侧薄包装）。

通用编排 ``performance.suite.run_suite`` 负责 prepare → preflight → 灌种
→ 逐 case → acceptance → suite.json/acceptance.json 的整体流程；本模块挂
echomem 的钩子：case 选择（``select_cases``）、case → Profile（含 quick
收敛与 barrier/burst 参数，``build_case_profile``）、单 case 执行
（``run_case``，summarize 包装 + commit/search 证据 CSV）、preflight /
seed / acceptance 求值。

``run_case`` 与 ``summarize_case_records`` 仍在本模块导出，供测试直接使用。
"""

from __future__ import annotations

import csv
import json
import time
import os
from collections import Counter
from pathlib import Path
from typing import Any

from performance.monitor import MetricsMonitor, write_metrics_csv
from performance.profile import Profile
from performance.records import RequestRecord
from performance.suite import (
    SeedContext,
    run_case as run_case_impl,
    run_suite as run_suite_impl,
    summarize_case_records as summarize_case_metrics,
)
from performance.targets.echomem.acceptance.evaluate import (
    evaluate_pr421_acceptance,
)
from performance.targets.echomem.acceptance.metrics import metric_coverage
from performance.targets.echomem.acceptance.observation import _commit_window_evidence
from performance.targets.echomem.acceptance.preflight import run_preflight
from performance.targets.echomem.acceptance.seed import (
    TenantPreparer,
    load_tenant_specs,
)
from performance.targets.echomem.orchestrator.suites import (
    QuickSpec,
    build_case_profile,
    select_cases,
)
from performance.targets.echomem.protocol import is_anchor_query

SCENES_DIR = Path(__file__).resolve().parent.parent / "scenes"


def summarize_case_records(records: list[RequestRecord]) -> dict:
    """records → suite per-run summary（通用 metrics + echomem 扩展）。

    通用层 ``suite.summarize_case_records`` 只产出 metrics；这里补
    details/parameters（identity_mode/quality_seed/延迟阈值），并用
    ``is_anchor_query`` 计算 quality_asserted。
    """
    summary = summarize_case_metrics(records, is_anchor=is_anchor_query)
    summary["details"] = {"identity_mode": "independent_auth_keys", "quality_seed": []}
    summary["parameters"] = {
        "commit_delay_threshold_s": 10.0,
        "search_delay_threshold_s": 2.5,
    }
    reads = [r for r in records if r.op == "read"]
    from performance.stats import percentile
    import statistics
    summary["details"]["query_classes"] = {}
    for query_type in ("recall", "no_recall", "unclassified"):
        rows = [r for r in reads if r.query_type == query_type]
        values = [r.stage_ms / 1000 for r in rows]
        summary["details"]["query_classes"][query_type] = {
            "submitted": len(rows),
            "quality_passed": sum(r.status == "ok" and r.quality_ok for r in rows),
            "degraded": sum(r.degraded for r in rows),
            "mean_s": statistics.mean(values) if values else None,
            "p95_s": percentile(values, 95) if values else None,
        }
    return summary


# ---------------------------------------------------------------------- #
#  单 case 执行与产物                                                    #
# ---------------------------------------------------------------------- #

def _write_commit_results(case_dir: Path, records: list[RequestRecord]) -> None:
    """commit_results.csv：每条 commit_submit 一行，按 commit_done 对账状态。"""
    evidence = _commit_window_evidence([r.to_csv_row() for r in records])
    def key(r):
        return str(r.tenant_idx), r.session_id, r.archive_id

    observations = {}
    for r in records:
        if r.op == "commit_done":
            observations.setdefault(key(r), []).append(r)
    receipts = Counter(key(r) for r in records if r.op == "commit_submit"
                       and r.http_status == 202 and r.session_id and r.archive_id)
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.op != "commit_submit":
            continue
        terminal = evidence["terminals"].get(key(record))
        observed = observations.get(key(record), [])
        observation = observed[0] if len(observed) == 1 else None
        if record.http_status is not None and record.http_status >= 400:
            status = "rejected"
        elif not (record.http_status == 202 and record.session_id and record.archive_id):
            status = "unaccepted"
        elif receipts[key(record)] != 1 or len(observed) > 1:
            status = "ambiguous"
        elif terminal is not None:
            status = "completed" if terminal["status"] == "ok" else "failed"
        else:
            status = "unresolved"
        terminal_ms = terminal.get("completed_at_ms") if terminal else None
        done_ms = observation.stage_ms if terminal and observation else None
        accepted_latency = terminal_ms - record.accepted_at_ms if terminal_ms is not None else None
        total_ms = terminal_ms - (record.ts_ms - record.stage_ms) if terminal_ms is not None else None
        rows.append(
            {
                "tenant_idx": record.tenant_idx,
                "session_id": record.session_id,
                "archive_id": record.archive_id,
                "status": status,
                "submit_ms": round(record.stage_ms, 3),
                "done_ms": round(done_ms, 3) if done_ms is not None else "",
                "end_to_end_s": round(total_ms / 1000.0, 3) if total_ms is not None and total_ms >= 0 else "",
                "accepted_to_terminal_s": round(accepted_latency / 1000.0, 3) if accepted_latency is not None else "",
                "observation_status": observation.poll_outcome if observation else "unknown",
                "poll_count": observation.poll_count if observation else "",
                "poll_http_errors": observation.poll_http_errors if observation else "",
                "poll_evidence_version": observation.poll_evidence_version if observation else "",
            }
        )
    with (case_dir / "commit_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "tenant_idx", "session_id", "archive_id", "status",
                "submit_ms", "done_ms", "end_to_end_s",
                "accepted_to_terminal_s", "observation_status", "poll_count", "poll_http_errors", "poll_evidence_version",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_search_results(case_dir: Path, records: list[RequestRecord]) -> None:
    """search_results.csv：每条 read 一行，含质量字段与拒绝响应证据。"""
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.op != "read":
            continue
        status_code = record.http_status if record.http_status is not None else ""
        rows.append(
            {
                "query": record.query,
                "hit_count": record.hit_count,
                "quality_ok": record.quality_ok,
                "degraded": record.degraded,
                "status_code": status_code,
                "end_to_end_s": round(record.stage_ms / 1000.0, 3),
                "tenant": record.tenant_idx,
                "session_id": record.session_id,
                "retry_after_s": record.retry_after_s or "",
                "reason_code": record.reason_code,
            }
        )
    with (case_dir / "search_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "query", "hit_count", "quality_ok", "degraded", "status_code",
                "end_to_end_s", "tenant", "session_id", "retry_after_s", "reason_code",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_case_evidence(case_dir: Path, records: list[RequestRecord]) -> None:
    """commit/search 证据 CSV（echomem 专属，写在通用 summary/records 之后）。"""
    _write_commit_results(case_dir, records)
    _write_search_results(case_dir, records)


def run_case(
    case: dict,
    profile: Profile,
    *,
    case_dir: Path,
    timeout_s: float | None = None,
    collect_metrics: bool = True,
) -> dict:
    """执行单个 case（经通用层 run_case）：load_scene + Engine.run 并写产物。

    通用层负责执行与 summary.json/records.csv；这里挂上 echomem 的
    summarize 包装（metrics + details/parameters）与 commit/search 证据 CSV。
    ``collect_metrics`` 时用 ``MetricsMonitor`` 后台采样服务端 /metrics，
    写 metrics_samples.csv 并把 PR421 B7 覆盖证据挂到 summary.details。
    """
    monitor = None
    started = time.time()
    if collect_metrics and profile.target.base_url:
        monitor = MetricsMonitor(profile.target.base_url, interval_s=2.0, timeout_s=5.0)
        monitor.start()
    try:
        result = run_case_impl(
            case,
            profile,
            scene_path=SCENES_DIR / f"{case['scene']}.py",
            case_dir=case_dir,
            timeout_s=timeout_s,
            summarize=summarize_case_records,
            write_evidence=_write_case_evidence,
        )
    finally:
        if monitor is not None:
            monitor.stop()
    if monitor is not None:
        write_metrics_csv(case_dir, monitor)
        summary = result["summary"]
        details = summary.setdefault("details", {})
        details["pr421_metric_coverage"] = metric_coverage(monitor, started, time.time())
        # coverage 在通用层写盘 summary.json 之后才算出，必须同步写回，
        # 保证磁盘 summary 与内存一致（--resume / rebuild_report 都以
        # 磁盘 summary.json 为唯一数据源）。
    # Persist the effective, quick-capped workload, not credentials or query pools.
    contract = {"version": "echomem-case-v1", "tenant_count": len(profile.tenants),
                "query_mode": profile.params.get("query_mode", "recall")}
    if case.get("fairness_mode") == "independent-periodic-v1":
        contract.update({"fairness_mode": case["fairness_mode"],
                         "measurement_start_s": case["measurement_start_s"],
                         "measurement_end_s": case["measurement_end_s"],
                         "arrival": {name: {"scope": spec.scope, "rps": spec.rps,
                                            "start_s": spec.start_s, "end_s": spec.end_s,
                                            "tenant_weights": list(spec.tenant_weights)
                                            if spec.tenant_weights else None}
                                     for name, spec in profile.load.arrival.items()}})
    elif any(spec.tenant_weights for spec in profile.load.arrival.values()):
        contract.update({"heterogeneous_tenant_load": True,
                         "arrival": {name: {"scope": spec.scope, "rps": spec.rps,
                                            "start_s": spec.start_s, "end_s": spec.end_s,
                                            "tenant_weights": list(spec.tenant_weights)
                                            if spec.tenant_weights else None}
                                     for name, spec in profile.load.arrival.items()}})
    if case["scene"] == "scene_barrier":
        contract.update({name: profile.params.get(name) for name in (
            "barrier_count", "barrier_waves", "barrier_distribution", "commit_tenant_counts")})
    result["summary"]["measurement_contract"] = contract
    (case_dir / "summary.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


# ---------------------------------------------------------------------- #
#  suite 编排（钩子 + 薄包装）                                            #
# ---------------------------------------------------------------------- #

def _preflight_stage(config: str, *, strict: bool = False) -> dict:
    """preflight 阶段条目：配置缺失时给 NOT_RUN，否则运行并附加 config。"""
    if not config:
        return {
            "status": "NOT_RUN", "config": "", "engines_checked": 0,
            "engines": [], "digest": "",
        }
    result = run_preflight(config, timeout_s=30.0,
                           **({"required_kinds": ("llm", "embedding")} if strict else {}))
    return {**result, "config": config}


def _prepare_semantic_seed(base_url, tenant_config, max_tenants, seed_sessions, seed_messages, *,
                           reuse_seed=None, dataset_path="", sample_id="conv-30", session_key="session_1",
                           search_timeout_s=60):
    """Observation workloads share remembered facts, not a bare-marker routing gate."""
    import uuid
    from performance.suite import SeedPreparationError
    from performance.targets.echomem.acceptance.capacity_seed import (
        CapacityActor,
        prepare_actors,
        validate_cached_actors,
        validate_search_timeout,
    )
    from performance.targets.echomem.acceptance.semantic_corpus import (
        DEFAULT_LOCOMO_DATASET,
        build_locomo_session_corpus,
    )
    from performance.targets.echomem.probes._client import EchoMemHTTP

    validate_search_timeout(search_timeout_s)
    specs = load_tenant_specs(tenant_config, tenant_count=max_tenants)
    if len(specs) != max_tenants or len({s.auth_key for s in specs}) != max_tenants:
        raise RuntimeError("Semantic seed requires all independent tenant credentials")
    run_tag = uuid.uuid4().hex
    source_path = Path(dataset_path) if dataset_path else DEFAULT_LOCOMO_DATASET
    actors = [CapacityActor(index, 0, EchoMemHTTP(base_url, spec.auth_key,
                    tenant_id=spec.tenant_id, user_id=spec.user_id,
                    account_id=spec.account_id, agent_id=spec.agent_id),
                build_locomo_session_corpus(f"formal-recall-{run_tag}-{index}",
                    dataset_path=source_path, sample_id=sample_id, session_key=session_key))
              for index, spec in enumerate(specs)]
    if reuse_seed:
        from dataclasses import replace
        from performance.targets.echomem.acceptance.capacity_experiment import _load_actors
        cached, _ = _load_actors(Path(reuse_seed), base_url)
        incompatible = [a for a in cached if a.corpus.get("query_contract") != "locomo-single-session-evidence-v1"
                        or (a.corpus.get("source") or {}).get("sample_id") != sample_id
                        or (a.corpus.get("source") or {}).get("session_key") != session_key]
        if incompatible:
            raise RuntimeError("Semantic cache is not the configured LoCoMo single-session corpus")
        actors = []
        for spec in specs:
            matches = [a for a in cached if all(getattr(a.client, field) == getattr(spec, field)
                       for field in ("tenant_id", "user_id", "account_id", "agent_id", "auth_key"))]
            if len(matches) != 1:
                raise RuntimeError("Semantic cache must match each configured identity exactly once")
            actors.append(replace(matches[0], tenant_index=len(actors)))
        evidence = validate_cached_actors(actors, validation_queries=4, search_timeout_s=search_timeout_s)
    else:
        evidence = prepare_actors(actors, timeout_s=180, validation_queries=4, search_timeout_s=search_timeout_s)
    if evidence["healthy_actors"] != max_tenants:
        raise SeedPreparationError(f"Semantic seed validation failed: healthy={evidence['healthy_actors']}/{max_tenants}", evidence)
    contexts = [SeedContext(tenant_id=actor.client.tenant_id, auth_key=actor.client.auth_key,
                    agent_id=actor.client.agent_id, user_id=actor.client.user_id,
                    account_id=actor.client.account_id,
                    queries=[q["query"] for q in actor.corpus["recall_queries"]],
                    query_cases={q["query"]: q for q in actor.corpus["recall_queries"]}) for actor in actors]
    counts = [{"documents": len(a.corpus["documents"]), "facts": len(a.corpus["facts"]),
               "queries": len(a.corpus["recall_queries"])} for a in actors]
    def uniform_count(field):
        values = {row[field] for row in counts}
        return next(iter(values)) if len(values) == 1 else None

    return contexts, {"status": "completed", "tenant_count": max_tenants,
                      "identity_mode": "independent", "keys_independent": True,
                      "seed_contract": "fixed-fact-in-items", "seed_evidence": evidence,
                      "seed_source": "validated-cache" if reuse_seed else "locomo-single-session",
                      "corpus_source": {"dataset": source_path.name, "sample_id": sample_id,
                                        "session_key": session_key},
                      "probe_queries": {actor.client.tenant_id: actor.corpus["recall_queries"][0] for actor in actors},
                      "corpus_fingerprints": [actor.corpus["fingerprint"] for actor in actors],
                      "corpus_counts_by_tenant_index": counts,
                      "seed_documents_per_tenant": uniform_count("documents"),
                      "facts_per_tenant": uniform_count("facts"),
                      "query_variants_per_tenant": uniform_count("queries"),
                      "validated_queries_per_tenant": 4, "seed_search_timeout_s": search_timeout_s}


def _prepare_seed(
    base_url: str,
    tenant_config: str,
    max_tenants: int,
    seed_sessions: int,
    seed_messages: int,
) -> tuple[list[SeedContext], dict]:
    """灌种：解析租户规格 → TenantPreparer 打开真实 session，返回上下文与摘要。"""
    specs = load_tenant_specs(tenant_config, tenant_count=max_tenants)
    preparer = TenantPreparer(base_url, tenant_specs=specs)
    contexts = preparer.prepare(
        seed_sessions, seed_messages, commit_poll_timeout_s=600.0
    )
    if not preparer.keys_independent() or len(contexts) != max_tenants:
        raise RuntimeError("All requested tenants must have distinct, nonempty credentials")
    from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval

    visibility = []
    for ctx in contexts:
        query_cases = dict(getattr(ctx, "query_cases", {}) or {})
        if not query_cases:
            raise RuntimeError(f"Seed did not generate semantic recall cases: tenant={ctx.tenant_id}")
        for query, sample in query_cases.items():
            deadline = time.monotonic() + 60
            while True:
                response = ctx.client.search("", query, timeout_s=10)
                quality = assess_retrieval(response.payload, sample)
                # Visibility is a setup prerequisite; degradation remains a
                # measured quality failure rather than hiding all load evidence.
                if response.status_code == 200 and quality["matched_expected_fact"]:
                    visibility.append({"tenant_id": ctx.tenant_id, "query_id": sample["id"], "visible": True,
                                       "quality_ok": quality["quality_ok"], "degraded": quality["degraded"],
                                       "intent_rejected": quality["intent_rejected"],
                                       "degraded_reasons": quality["degraded_reasons"]})
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Seed fact not found: tenant={ctx.tenant_id} query_id={sample['id']}; "
                                       f"http_status={response.status_code}, hits={quality['hit_count']}, "
                                       f"intent_rejected={quality['intent_rejected']}, degraded={quality['degraded']}")
                time.sleep(1)
    return [
        SeedContext(
            tenant_id=ctx.tenant_id,
            auth_key=ctx.auth_key,
            queries=list(ctx.queries),
            agent_id=ctx.client.agent_id,
            user_id=ctx.client.user_id,
            account_id=ctx.client.account_id,
            query_cases=dict(ctx.query_cases),
        )
        for ctx in contexts
    ], {
        "status": "completed",
        "tenant_count": len(contexts),
        "identity_mode": preparer.identity_mode(),
        "seed_sessions_per_tenant": seed_sessions,
        "seed_messages_per_session": seed_messages,
        "visibility": visibility,
        "keys_independent": preparer.keys_independent(),
    }


def run_suite(
    profile: dict,
    *,
    suite_dir: Path,
    quick: QuickSpec | None = None,
    profile_name: str = "4u8g",
    base_url: str = "",
    timeout_s: float = 120.0,
    scenarios: list[str] | None = None,
    resume: bool = False,
) -> dict:
    """执行单个 instance profile 的正式套件（通用编排 + echomem 钩子）。

    profile = instance-profiles JSON 里的单个 profile dict。通用流程见
    ``performance.suite.run_suite``；这里传入 echomem 的 case 选择、Profile
    构造（auth_headers 恒为空，租户凭据由灌种上下文按 case 覆盖）、单 case
    执行、preflight/seed/acceptance 钩子。``metrics_enabled`` 控制 case 级
    服务端 /metrics 采样。``resume`` 为 True 时跳过已有 summary.json 的
    case，历史 run 合并进最终 suite.json（语义见通用层）。
    """
    metrics_enabled = bool(profile.get("metrics_enabled", True))
    observation_before = None
    if profile.get("six_metrics") or profile.get("six_metrics_observation"):
        from performance.targets.echomem.acceptance.readiness import check_readiness
        readiness = check_readiness(profile)
        if not readiness["ok"]:
            suite_dir.mkdir(parents=True, exist_ok=True)
            result = {"runs": [], "resource_preflight": {"status": "INCONCLUSIVE", "readiness": readiness},
                      "output_root": str(suite_dir), "instance_profile": profile.get("name")}
            (suite_dir / "suite.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result
        profile = {**profile, "resource_evidence": readiness["resource_evidence"]}
        observation = profile.get("tenant_observability", {})
        from performance.targets.echomem.probes.tenant_observability import collect
        observation_before = collect(
            base_url=base_url, endpoint=str(observation.get("endpoint", "")),
            token=os.environ.get(observation.get("token_env", "ECHOMEM_TEST_CONTROL_TOKEN"), ""),
            expected_tenants=list(observation.get("expected_tenants", [])),
            expected_lanes=list(observation.get("expected_lanes", [])), timeout_s=15,
        )

    def _run_case(case, case_profile, *, case_dir, timeout_s):
        return run_case(
            case, case_profile, case_dir=case_dir, timeout_s=timeout_s,
            collect_metrics=metrics_enabled,
        )

    def _select_cases(name, scenarios):
        if profile.get("six_metrics_observation"):
            from performance.targets.echomem.orchestrator.suites import six_metric_observation_cases
            catalog = six_metric_observation_cases(quick=quick is not None)
            if scenarios is None:
                return catalog
            selected = set(scenarios)
            return [case for case in catalog if case["label"] in selected]
        if profile.get("six_metrics"):
            from performance.targets.echomem.orchestrator.suites import six_metric_cases
            return six_metric_cases(profile.get("capacity_levels"))
        return select_cases(name, scenarios)

    from functools import partial
    semantic_seed = partial(
        _prepare_semantic_seed,
        reuse_seed=profile.get("semantic_seed_cache"),
        dataset_path=profile.get("semantic_seed_dataset", ""),
        sample_id=profile.get("semantic_seed_sample", "conv-30"),
        session_key=profile.get("semantic_seed_session", "session_1"),
        search_timeout_s=profile.get("seed_search_timeout_s", 60),
    )
    result = run_suite_impl(
        profile,
        suite_dir=suite_dir,
        profile_name=profile_name,
        base_url=base_url,
        timeout_s=timeout_s,
        scenarios=scenarios,
        quick=quick,
        resume=resume,
        select_cases=_select_cases,
        build_profile=lambda case, url, tenant_count, q: build_case_profile(
            case, base_url=url, tenant_count=tenant_count, auth_headers={}, quick=q
        ),
        run_case=_run_case,
        preflight=lambda config: _preflight_stage(
            config,
            strict=bool(profile.get("six_metrics") or profile.get("six_metrics_observation")),
        ),
        seed=(semantic_seed if profile.get("six_metrics_observation") else _prepare_seed),
        evaluate=evaluate_pr421_acceptance,
    )
    if profile.get("resource_evidence"):
        result["resource_evidence"] = profile["resource_evidence"]
    if profile.get("six_metrics") or profile.get("six_metrics_observation"):
        result["readiness"] = readiness
    if observation_before is not None:
        result["tenant_observability_before"] = observation_before
    return result
