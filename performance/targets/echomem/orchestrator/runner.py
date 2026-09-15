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
import math
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


def _planned_arrival_contract(profile: Profile) -> dict[str, Any]:
    """Return the effective open-loop arrival counts used by this case.

    The engine's fixed-rate gate stops slots at ``end_s``.  Repeating that
    calculation here makes the plan auditable without treating observed
    request counts as the plan or silently shrinking a workload.
    """
    duration = float(profile.load.duration_s)

    def count(rate: float, start: float, end: float | None) -> int:
        finish = duration if end is None else min(duration, float(end))
        if rate <= 0 or finish <= start:
            return 0
        return max(0, int(math.ceil((finish - start) * rate - 1e-9)))

    result: dict[str, Any] = {}
    tenant_count = len(profile.tenants)
    for task_name, spec in profile.load.arrival.items():
        start = float(spec.start_s)
        if spec.scope == "per_tenant":
            weights = list(spec.tenant_weights) if spec.tenant_weights else [1.0] * tenant_count
            by_tenant = {
                str(index): count(float(spec.rps) * float(weights[index]), start, spec.end_s)
                for index in range(tenant_count)
            }
            result[task_name] = {
                "scope": spec.scope,
                "rps": spec.rps,
                "start_s": start,
                "end_s": spec.end_s,
                "tenant_weights": weights,
                "planned_total": sum(by_tenant.values()),
                "planned_by_tenant": by_tenant,
            }
        else:
            result[task_name] = {
                "scope": spec.scope,
                "rps": spec.rps,
                "start_s": start,
                "end_s": spec.end_s,
                "tenant_weights": None,
                "planned_total": count(float(spec.rps), start, spec.end_s),
                "planned_by_tenant": None,
            }
    return result


def _record_value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _case_records(case_dir: Path) -> list[dict[str, str]]:
    path = case_dir / "records.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _commit_observation_counts(records: list[Any]) -> dict[str, Any]:
    """Summarise Commit submit/202/terminal evidence without dropping failures."""
    submits = [record for record in records if _record_value(record, "op") == "commit_submit"]
    accepted = [record for record in submits if str(_record_value(record, "http_status")) == "202"]
    completed = [record for record in records
                 if _record_value(record, "op") == "commit_done"
                 and _record_value(record, "commit_terminal_state") == "completed"]
    failed_terminal = [record for record in records
                       if _record_value(record, "op") == "commit_done"
                       and _record_value(record, "commit_terminal_state") in {"failed", "error"}]
    rejected = [record for record in submits
                if _number_value(_record_value(record, "http_status")) is not None
                and _number_value(_record_value(record, "http_status")) >= 400]
    terminal = len(completed) + len(failed_terminal)
    by_tenant: dict[str, dict[str, int]] = {}
    tenants = sorted({str(_record_value(record, "tenant_idx")) for record in submits})
    for tenant in tenants:
        tenant_submits = [record for record in submits if str(_record_value(record, "tenant_idx")) == tenant]
        tenant_accepted = [record for record in accepted if str(_record_value(record, "tenant_idx")) == tenant]
        tenant_completed = [record for record in completed if str(_record_value(record, "tenant_idx")) == tenant]
        tenant_failed = [record for record in failed_terminal if str(_record_value(record, "tenant_idx")) == tenant]
        by_tenant[str(tenant)] = {
            "submitted": len(tenant_submits),
            "accepted_202": len(tenant_accepted),
            "completed": len(tenant_completed),
            "failed_terminal": len(tenant_failed),
            "rejected": sum(
                _number_value(_record_value(record, "http_status")) is not None
                and _number_value(_record_value(record, "http_status")) >= 400
                for record in tenant_submits
            ),
            "pending_at_end": max(0, len(tenant_accepted) - len(tenant_completed) - len(tenant_failed)),
        }
    return {
        "submitted": len(submits),
        "accepted_202": len(accepted),
        "completed": len(completed),
        "failed_terminal": len(failed_terminal),
        "rejected": len(rejected),
        "pending_at_end": max(0, len(accepted) - terminal),
        "by_tenant": by_tenant,
    }


def _number_value(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _planned_commit_contract(case: dict[str, Any], profile: Profile) -> dict[str, Any]:
    """Calculate planned Commit work for periodic and barrier cases."""
    if case.get("scene") == "scene_barrier":
        count = int(profile.params.get("barrier_count", 0))
        waves = int(profile.params.get("barrier_waves", 1))
        from performance.targets.echomem._barrier import barrier_tenant_counts

        per_wave = barrier_tenant_counts(
            count,
            len(profile.tenants),
            distribution=str(profile.params.get("barrier_distribution", "uniform")),
            zipf_exponent=float(profile.params.get("barrier_zipf_exponent", 2.0)),
            explicit=profile.params.get("commit_tenant_counts"),
        )
        planned_by_tenant = {
            str(tenant): value * waves for tenant, value in per_wave.items()
        }
        return {
            "planned_total": count * waves,
            "planned_by_tenant": planned_by_tenant,
            "source": "commit_barrier",
        }
    write = _planned_arrival_contract(profile).get("write") or {}
    return {
        "planned_total": write.get("planned_total", 0),
        "planned_by_tenant": write.get("planned_by_tenant"),
        "source": "periodic_arrival",
    }


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
            "recall_served": sum(r.status == "ok" and (r.recall_served is not False) and r.hit_count > 0 for r in rows),
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
    arrival_contract = _planned_arrival_contract(profile)
    commit_plan = _planned_commit_contract(case, profile)
    commit_counts = _commit_observation_counts(_case_records(case_dir))
    contract = {
        "version": "echomem-case-v2",
        "case_label": case["label"],
        "tenant_count": len(profile.tenants),
        "duration_s": profile.load.duration_s,
        "query_mode": profile.params.get("query_mode", "recall"),
        "commit_payload_profile": profile.params.get("commit_payload_profile", "standard"),
        "arrival": arrival_contract,
        "planned_search_count": (arrival_contract.get("read") or {}).get("planned_total", 0),
        "planned_commit_count": commit_plan["planned_total"],
        "planned_commit_by_tenant": commit_plan["planned_by_tenant"],
        "planned_commit_source": commit_plan["source"],
        "actual_commit": commit_counts,
    }
    if case.get("fairness_mode") == "independent-periodic-v1":
        contract.update({"fairness_mode": case["fairness_mode"],
                         "measurement_start_s": case["measurement_start_s"],
                         "measurement_end_s": case["measurement_end_s"]})
    if any(spec.tenant_weights for spec in profile.load.arrival.values()):
        contract["heterogeneous_tenant_load"] = True
    if case["scene"] == "scene_barrier":
        contract.update({name: profile.params.get(name) for name in (
            "barrier_count", "barrier_waves", "barrier_distribution",
            "barrier_prepare_before_commit", "barrier_prepare_at_s",
            "barrier_max_workers", "commit_tenant_counts")})
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
                           search_timeout_s=60, corpus_mode="locomo-single-session",
                           validation_queries=4, validation_query_ids=None,
                           seed_workers=None, identity_cache=None):
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
        build_fixed_fact_corpus,
        build_locomo_session_corpus,
    )
    from performance.targets.echomem.probes._client import EchoMemHTTP

    validate_search_timeout(search_timeout_s)
    run_tag = uuid.uuid4().hex
    source_path = Path(dataset_path) if dataset_path else DEFAULT_LOCOMO_DATASET
    if corpus_mode not in {"locomo-single-session", "fixed-natural-fact"}:
        raise ValueError("corpus_mode must be locomo-single-session or fixed-natural-fact")
    actors = []
    seed_source = corpus_mode
    if identity_cache:
        from dataclasses import replace
        from performance.targets.echomem.acceptance.capacity_experiment import _load_actors
        cached, _ = _load_actors(Path(identity_cache), base_url)
        if len(cached) < max_tenants:
            raise RuntimeError(
                f"Semantic identity cache has {len(cached)} actors; {max_tenants} required"
            )
        selected = cached[:max_tenants]
        if any(not actor.write_session for actor in selected):
            raise RuntimeError("Semantic identity cache has no live write session for every actor")
        actors = [replace(actor, tenant_index=index, user_index=0)
                  for index, actor in enumerate(selected)]
        seed_source = "validated-identity-cache"
    else:
        specs = load_tenant_specs(tenant_config, tenant_count=max_tenants)
        if len(specs) != max_tenants or len({s.auth_key for s in specs}) != max_tenants:
            raise RuntimeError("Semantic seed requires all independent tenant credentials")
        for index, spec in enumerate(specs):
            identity = f"formal-recall-{run_tag}-{index}"
            corpus = (
                build_fixed_fact_corpus(identity)
                if corpus_mode == "fixed-natural-fact"
                else build_locomo_session_corpus(
                    identity, dataset_path=source_path, sample_id=sample_id,
                    session_key=session_key,
                )
            )
            actors.append(CapacityActor(
                index, 0,
                EchoMemHTTP(base_url, spec.auth_key, tenant_id=spec.tenant_id,
                            user_id=spec.user_id, account_id=spec.account_id,
                            agent_id=spec.agent_id),
                corpus,
            ))
    if identity_cache:
        evidence = validate_cached_actors(
            actors, validation_queries=validation_queries,
            search_timeout_s=search_timeout_s,
            validation_query_ids=validation_query_ids,
        )
    elif reuse_seed:
        from dataclasses import replace
        from performance.targets.echomem.acceptance.capacity_experiment import _load_actors
        cached, _ = _load_actors(Path(reuse_seed), base_url)
        seed_source = "validated-cache"
        expected_contract = ("fixed-natural-fact-v1" if corpus_mode == "fixed-natural-fact"
                             else "locomo-single-session-evidence-v1")
        incompatible = [a for a in cached if (
            a.corpus.get("query_contract") != expected_contract
            or (corpus_mode != "fixed-natural-fact" and (
                (a.corpus.get("source") or {}).get("sample_id") != sample_id
                or (a.corpus.get("source") or {}).get("session_key") != session_key
            ))
        )]
        if incompatible:
            raise RuntimeError("Semantic cache is not the configured seed corpus")
        actors = []
        for spec in specs:
            matches = [a for a in cached if all(getattr(a.client, field) == getattr(spec, field)
                       for field in ("tenant_id", "user_id", "account_id", "agent_id", "auth_key"))]
            if len(matches) != 1:
                raise RuntimeError("Semantic cache must match each configured identity exactly once")
            actors.append(replace(matches[0], tenant_index=len(actors)))
        evidence = validate_cached_actors(
            actors, validation_queries=validation_queries,
            search_timeout_s=search_timeout_s,
            validation_query_ids=validation_query_ids,
        )
    else:
        evidence = prepare_actors(
            actors, timeout_s=180, validation_queries=validation_queries,
            search_timeout_s=search_timeout_s, workers=seed_workers,
        )
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
                      "seed_source": seed_source,
                      "corpus_source": {"dataset": source_path.name, "sample_id": sample_id,
                                        "session_key": session_key},
                      "probe_queries": {actor.client.tenant_id: actor.corpus["recall_queries"][0] for actor in actors},
                      "corpus_fingerprints": [actor.corpus["fingerprint"] for actor in actors],
                      "corpus_counts_by_tenant_index": counts,
                      "seed_documents_per_tenant": uniform_count("documents"),
                      "facts_per_tenant": uniform_count("facts"),
                      "query_variants_per_tenant": uniform_count("queries"),
                      "validated_queries_per_tenant": validation_queries,
                      "seed_search_timeout_s": search_timeout_s,
                      "seed_workers": seed_workers}


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
            catalog = six_metric_observation_cases(
                quick=quick is not None,
                duration_s=profile.get("m2m3_duration_s"),
                tail_s=profile.get("m2m3_tail_s"),
                m2_commit_rpm=profile.get("m2_commit_rpm"),
                m3_barrier_count=profile.get("m3_barrier_count"),
                search_workers=profile.get("m2m3_search_workers"),
            )
            if scenarios is None:
                return catalog
            selected = set(scenarios)
            return [case for case in catalog if case["label"] in selected]
        if profile.get("six_metrics"):
            from performance.targets.echomem.orchestrator.suites import six_metric_cases
            return six_metric_cases(profile.get("capacity_levels"))
        return select_cases(name, scenarios)

    from functools import partial
    seed_keywords = {
        "reuse_seed": profile.get("semantic_seed_cache"),
        "dataset_path": profile.get("semantic_seed_dataset", ""),
        "sample_id": profile.get("semantic_seed_sample", "conv-30"),
        "session_key": profile.get("semantic_seed_session", "session_1"),
        "search_timeout_s": profile.get("seed_search_timeout_s", 60),
    }
    identity_cache = profile.get("semantic_seed_identity_cache")
    if identity_cache:
        seed_keywords["identity_cache"] = identity_cache
    if "semantic_seed_mode" in profile:
        seed_keywords["corpus_mode"] = profile["semantic_seed_mode"]
    if "semantic_seed_validation_queries" in profile:
        seed_keywords["validation_queries"] = int(profile["semantic_seed_validation_queries"])
    if "semantic_seed_validation_query_ids" in profile:
        seed_keywords["validation_query_ids"] = [
            str(item) for item in (profile["semantic_seed_validation_query_ids"] or [])
        ]
    if "semantic_seed_workers" in profile:
        seed_keywords["seed_workers"] = profile["semantic_seed_workers"]
    semantic_seed = partial(_prepare_semantic_seed, **seed_keywords)
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
