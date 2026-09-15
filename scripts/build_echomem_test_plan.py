#!/usr/bin/env python3
"""Build the current EchoMem M1-M6 executable test-plan HTML.

This command is read-only. It reads a public profile and optional redacted
summary evidence, then delegates HTML layout to the acceptance plan renderer.
It never sends HTTP requests and never loads a secret value into the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from performance.targets.echomem.acceptance.plan_report import write_test_plan_report


def _load_profile(path: Path, name: str | None) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    profiles = document.get("profiles") if isinstance(document, dict) else document
    if not isinstance(profiles, list):
        raise ValueError("profile JSON must contain a profiles list")
    if name:
        matches = [item for item in profiles if str(item.get("name")) == name]
    else:
        matches = profiles if len(profiles) == 1 else []
    if len(matches) != 1:
        raise ValueError("profile file must resolve to exactly one profile; use --profile-name")
    return dict(matches[0])


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _models(profile: dict[str, Any]) -> dict[str, str]:
    config_path = Path(str(profile.get("preflight_config") or ""))
    config = _read_json(config_path) or {}
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    llm = model.get("llm") if isinstance(model.get("llm"), dict) else {}
    embedding = model.get("embedding") if isinstance(model.get("embedding"), dict) else {}
    return {
        "llm": str(llm.get("model") or "按实际 preflight"),
        "embedding": str(
            embedding.get("model")
            or profile.get("required_embedding_model")
            or "按实际 preflight"
        ),
        "llm_endpoint": str(llm.get("api_base") or "按实际 preflight"),
        "embedding_endpoint": str(embedding.get("api_base") or "按实际 preflight"),
    }


def _resource_limits(profile: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, str]:
    resource = (evidence or {}).get("resource_evidence") or {}
    cpu = resource.get("cpus")
    memory = resource.get("memory_bytes")
    if cpu:
        cpu_text = f"{cpu:g} vCPU"
    elif profile.get("require_4u8g"):
        cpu_text = "4 vCPU（profile 要求，运行时仍需核验）"
    else:
        cpu_text = "宿主机默认/运行时核验"
    if memory:
        memory_text = f"{int(memory) / (1024 ** 3):g} GiB"
    elif profile.get("require_4u8g"):
        memory_text = "8 GiB（profile 要求，运行时仍需核验）"
    else:
        memory_text = "宿主机默认/运行时核验"
    return {"cpu": cpu_text, "memory": memory_text}


def _current_evidence(evidence_dir: Path | None) -> dict[str, Any]:
    if not evidence_dir:
        return {
            "summary": "没有传入历史证据目录；本页只展示当前方案。",
            "source": "未提供 --evidence-dir",
            "caveats": ["方案页不把计划值当作实测值。"],
        }
    summary_path = evidence_dir / "summary.json"
    summary = _read_json(summary_path)
    if not summary:
        return {
            "summary": "指定证据目录缺少可读 summary.json；不能从该目录推导 16/64 结果。",
            "source": str(summary_path),
            "caveats": ["请保留同一运行目录中的 summary.json、level-* measurement 和 Prometheus 窗口证据。"],
        }

    m1 = ((summary.get("metrics") or {}).get("M1") or {})
    rows = []
    for level in m1.get("levels") or []:
        search = level.get("search") or {}
        status_counts = search.get("http_status_counts") or {}
        breakdown = search.get("error_breakdown") or {}
        if breakdown:
            # transport_or_http_errors is already a combined field; do not add
            # it to http_non_200 a second time.
            http_errors = int(breakdown.get("http_non_200") or 0) + int(
                breakdown.get("transport_errors") or 0
            )
        else:
            http_errors = sum(
                int(value or 0) for key, value in status_counts.items() if str(key) != "200"
            ) + int(search.get("transport_errors") or 0)
        stage = (level.get("server_stage_timings") or {}).get("memory_profile") or {}
        rows.append({
            "concurrency": level.get("target_concurrency"),
            "peak_inflight": search.get("peak_inflight_requests")
                or level.get("peak_inflight_requests"),
            "stage_observations": stage.get("observations"),
            "stage_p95_ms": round(float(stage["p95_s"]) * 1000, 3)
                if stage.get("p95_s") is not None else None,
            "search_sent": search.get("sent"),
            "http_errors": http_errors,
            "fact_hits": search.get("fact_hits"),
            "quality_total": search.get("fact_hit_observations"),
            "search_p95_ms": round(float(search["p95_s"]) * 1000, 3)
                if search.get("p95_s") is not None else None,
            "status_counts": status_counts,
        })

    comparison = m1.get("memory_profile_comparison") or {}
    p95_16 = comparison.get("p95_16_s")
    p95_64 = comparison.get("p95_64_s")
    comparison_view = {
        "p95_16_ms": round(float(p95_16) * 1000, 3) if p95_16 is not None else None,
        "p95_64_ms": round(float(p95_64) * 1000, 3) if p95_64 is not None else None,
        "ratio": round(float(comparison["p95_amplification"]), 4)
            if comparison.get("p95_amplification") is not None else None,
        "ready": comparison.get("comparison_ready", False),
    }
    selected_metrics = [str(code) for code in summary.get("selected_metrics") or []]
    selected_label = "/".join(selected_metrics) if selected_metrics else "未声明指标范围"
    if rows and comparison_view["ready"]:
        summary_text = (
            f"已有 {selected_label} 证据目录，其中 M1 同时采到 C=16/C=64 的 memory_profile 阶段样本；"
            "这可以回答阶段 P95 的 64/16 比值，但不等于完整六项完成，也不等于服务容量边界。"
        )
    else:
        summary_text = (
            "已有目录没有同时具备带边界的 C=16/C=64 阶段样本；"
            "不能用端到端耗时或旧 HTML 数字代替 memory_profile 对比。"
        )
    caveats = [
        "阶段 P95 来自服务端 memory_profile 的真实窗口样本，不是 Search HTTP P95。",
        "若某档出现 429/503、质量失败或持续积压，必须保留在分母；阶段可比不代表该档业务质量通过。",
        "容器未 OOM/崩溃且积压最终排空时，只能记录拥塞/操作边界，不能直接写成硬件极限。",
        f"本证据目录声明的指标范围为 {selected_label}；未选择的指标不从其他历史报告回填。",
    ]
    return {
        "summary": summary_text,
        "source": str(summary_path),
        "rows": sorted(rows, key=lambda row: (row.get("concurrency") is None, row.get("concurrency") or 0)),
        "comparison": comparison_view,
        "caveats": caveats,
    }


def _parameter_audit(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a redacted EchoMem server-parameter audit for the plan report.

    The report is deliberately explicit about provenance: an explicit
    scheduling override, a resolved small-profile value, and a version default
    are different facts.  Numeric recommendations are diagnostic starting
    points for a C=64 experiment, not a promise that a 4U8G instance can
    sustain those values or that a provider account accepts them.
    """

    config_path = Path(str(profile.get("preflight_config") or ""))
    config = _read_json(config_path) or {}
    instance = config.get("instance_profile") if isinstance(config.get("instance_profile"), dict) else {}

    def nested(document: dict[str, Any], *keys: str) -> Any:
        value: Any = document
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
        return value

    def display(value: Any) -> str:
        if value is None:
            return "未配置"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    def number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed == parsed and abs(parsed) != float("inf") else None

    # These are the values observed in the 4U8G small-profile replica used for
    # the earlier rejection analysis.  They are comparison anchors only; the
    # resolved startup log remains authoritative for a specific EchoMem build.
    small_defaults: dict[str, Any] = {
        "http": 64,
        "admission": 8,
        "recall_max_inflight": 16,
        "tenant_qps": 10,
        "tenant_concurrency": 8,
        "model_max": 16,
        "llm_total": 4,
        "embed_total": 4,
        "recall_llm": 1,
        "recall_embed": 2,
        "commit_queue": 256,
        "commit_quota": 64,
        "commit_executor": 4,
        "commit_gate": 4,
        "cache_max": 16,
        "cache_overshoot": 4,
        "cache_hard_cap": 20,
    }

    def resolve(
        path: tuple[str, ...],
        profile_key: str | None,
        default_value: Any,
        default_source: str = "small profile 对照",
    ) -> tuple[Any, str]:
        explicit = nested(config, *path)
        if explicit is not None:
            return explicit, "profile preflight config"
        if profile_key and instance.get(profile_key) is not None:
            return instance[profile_key], "instance_profile"
        return default_value, default_source

    rows: list[dict[str, Any]] = []

    def add_row(
        layer: str,
        parameter: str,
        current: Any,
        source: str,
        baseline: Any,
        target: str,
        action: str,
        explanation: str,
    ) -> None:
        rows.append(
            {
                "layer": layer,
                "parameter": parameter,
                "current": display(current),
                "source": source,
                "small_default": display(baseline),
                "c64_start": target,
                "action": action,
                "explanation": explanation,
            }
        )

    http, http_source = resolve(("scheduling", "http", "max_workers"), "http_max_workers", small_defaults["http"])
    admission, admission_source = resolve(
        ("scheduling", "retrieval", "admission_permits"),
        "retrieval_admission_permits",
        small_defaults["admission"],
    )
    recall_max, recall_source = resolve(
        ("recall", "max_inflight"),
        None,
        small_defaults["recall_max_inflight"],
        "版本默认/需 instance_profile_resolved 核验",
    )
    tenant_qps, tenant_qps_source = resolve(
        ("scheduling", "tenant", "qps"), "tenant_qps", small_defaults["tenant_qps"]
    )
    tenant_concurrency, tenant_concurrency_source = resolve(
        ("scheduling", "tenant", "concurrency"),
        "tenant_concurrency",
        small_defaults["tenant_concurrency"],
    )
    recall_cap_disabled = number(recall_max) == 0
    recall_current = "0（显式关闭上限）" if recall_cap_disabled else recall_max
    recall_target = (
        "保持0（已关闭）；或改为64作为有界诊断"
        if recall_cap_disabled
        else "64（诊断可128）"
    )
    recall_action = (
        "无需再放开；必须核对环境变量覆盖"
        if recall_cap_disabled
        else "必须核对/当前为16时需放开"
    )
    recall_explanation = (
        "当前配置明确把外层 Recall 上限设为 0，按当前代码语义表示关闭该上限；仍需用启动日志和拒绝日志确认没有环境变量覆盖，实际瓶颈转而看 Retrieval admission、子阶段和 Provider。"
        if recall_cap_disabled
        else "即使 engine/query_embedding 等子阶段配置为 64，外层总闸门仍会把同时执行的 Recall 限在较小值；以 retrieval_admission_rejected 的 in_flight/max_inflight 为证据。"
    )

    add_row(
        "HTTP 入口",
        "scheduling.http.max_workers / instance_profile.http_max_workers",
        http,
        http_source,
        small_defaults["http"],
        "256（与 admission=64 配套）",
        "必须核对/通常需放开",
        "当前版本存在 HTTP 与 Retrieval 的 4:1 约束；若目标是让 64 个 Search 都进入 Retrieval，HTTP 入口至少要有 256 个工作名额。",
    )
    add_row(
        "Retrieval 入口",
        "scheduling.retrieval.admission_permits",
        admission,
        admission_source,
        small_defaults["admission"],
        "64",
        "必须核对/目标为64时需放开",
        "这是进入 Recall 的入口名额；它小于 64 时，C=64 客户端仍可发压，但部分请求会在服务端入口排队或被拒绝。",
    )
    add_row(
        "Recall 总闸门",
        "recall.max_inflight / ECHOMEM_RECALL_MAX_INFLIGHT",
        recall_current,
        recall_source,
        small_defaults["recall_max_inflight"],
        recall_target,
        recall_action,
        recall_explanation,
    )
    add_row(
        "租户配额",
        "scheduling.tenant.concurrency",
        tenant_concurrency,
        tenant_concurrency_source,
        small_defaults["tenant_concurrency"],
        "按拓扑：1T×64 为64；4T×16 为16；8T×8为8",
        "按测试拓扑设置",
        "它是单租户并发，不是总并发。若保持 4 个租户×16，应确保每租户至少 16；单租户×64 才需要单租户 64。",
    )
    add_row(
        "租户配额",
        "scheduling.tenant.qps",
        tenant_qps,
        tenant_qps_source,
        small_defaults["tenant_qps"],
        "建议起步128；按实际到达率核定",
        "按到达率设置",
        "QPS 是单位时间到达速率，不等于在途并发。提高它不会绕过模型 Provider 的 RPM/TPM 或并发上限。",
    )

    stage_defaults = {"engine": 8, "intent_llm": 8, "query_embedding": 8, "rerank": 8}
    stage_labels = {
        "engine": "检索引擎",
        "intent_llm": "意图 LLM",
        "query_embedding": "查询 Embedding",
        "rerank": "Rerank",
    }
    stage_values: dict[str, tuple[Any, Any, Any, str]] = {}
    for stage, label in stage_labels.items():
        stage_config = nested(config, "recall", "concurrency", stage)
        stage_config = stage_config if isinstance(stage_config, dict) else {}
        max_value = stage_config.get("max_concurrent", stage_defaults[stage])
        queue_value = stage_config.get("queue_capacity", 32)
        per_tenant_value = stage_config.get("max_queued_per_tenant", 8)
        stage_source = "profile preflight config" if stage_config else "版本默认/需日志核验"
        stage_values[stage] = (max_value, queue_value, per_tenant_value, stage_source)
        add_row(
            "Recall 子阶段",
            f"recall.concurrency.{stage} (max_concurrent / queue_capacity / max_queued_per_tenant)",
            f"{display(max_value)} / {display(queue_value)} / {display(per_tenant_value)}",
            stage_source,
            f"{stage_defaults[stage]} / 32 / 8",
            "max=64；queue=256；per-tenant 按拓扑（16或64）",
            "按实际路由放开",
            f"{label} 是否被每个 Search 使用要由 trace/stage 事件确认；不要因为 C=64 就盲目把没有走到的阶段全部扩大。",
        )

    model_max, model_max_source = resolve(("model", "max_concurrent"), None, small_defaults["model_max"])
    llm_total, llm_source = resolve(
        ("scheduling", "llm_gateway", "llm_max_concurrent"),
        "llm_max_concurrent",
        small_defaults["llm_total"],
    )
    embed_total, embed_source = resolve(
        ("scheduling", "llm_gateway", "embed_max_concurrent"),
        "embed_max_concurrent",
        small_defaults["embed_total"],
    )
    recall_llm, recall_llm_source = resolve(
        ("scheduling", "llm_gateway", "recall_llm_max_concurrent"),
        "recall_llm_max_concurrent",
        small_defaults["recall_llm"],
    )
    recall_embed, recall_embed_source = resolve(
        ("scheduling", "llm_gateway", "recall_embed_max_concurrent"),
        "recall_embed_max_concurrent",
        small_defaults["recall_embed"],
    )
    provider_llm, provider_llm_source = resolve(
        ("scheduling", "llm_gateway", "provider_budget_llm"),
        "provider_budget_llm",
        8,
    )
    provider_embed, provider_embed_source = resolve(
        ("scheduling", "llm_gateway", "provider_budget_embed"),
        "provider_budget_embed",
        9,
    )
    tenant_share, tenant_share_source = resolve(
        ("scheduling", "llm_gateway", "tenant_max_share_pct"),
        "llm_lane_tenant_max_share_pct",
        50,
    )
    thinking_type = nested(config, "model", "llm", "extra_params", "thinking", "type")
    thinking_source = "profile preflight config" if thinking_type is not None else "未在 profile config 声明"
    if thinking_type is None:
        thinking_type = "未声明（以实际请求/启动日志核验）"
    add_row(
        "模型网关",
        "model.max_concurrent",
        model_max,
        model_max_source,
        small_defaults["model_max"],
        "至少覆盖所有消费者之和；建议先64/128并观察",
        "必须核对有效值",
        "全局模型池仍可能覆盖 Recall 子池；不同版本对 small profile 的默认值可能不同，必须记录 instance_profile_resolved。",
    )
    add_row(
        "模型网关",
        "llm_gateway.llm_max_concurrent / recall_llm_max_concurrent",
        f"{display(llm_total)} / {display(recall_llm)}",
        f"{llm_source}; {recall_llm_source}",
        f"{small_defaults['llm_total']} / {small_defaults['recall_llm']}",
        "Recall LLM 起步64；总池至少覆盖 Recall、Commit、episode 之和",
        "按实际 fan-out 和 Provider 限额",
        "Recall LLM 子池不能大于总池可用份额；扩大本地池不会提高供应商账号限额，也不能把 429 归因成 EchoMem CPU 不够。",
    )
    add_row(
        "模型网关",
        "llm_gateway.embed_max_concurrent / recall_embed_max_concurrent",
        f"{display(embed_total)} / {display(recall_embed)}",
        f"{embed_source}; {recall_embed_source}",
        f"{small_defaults['embed_total']} / {small_defaults['recall_embed']}",
        "Recall Embedding 起步64；总池按所有消费者之和",
        "若每个 query 都需向量则优先核对",
        "Embedding 往往是 Recall 的必经路径；先确认 query-vector cache 命中率和真实模型调用数，再决定是否扩大。",
    )
    add_row(
        "Provider 预算",
        "llm_gateway.provider_budget_llm / provider_budget_embed",
        f"{display(provider_llm)} / {display(provider_embed)}",
        f"{provider_llm_source}; {provider_embed_source}",
        "8 / 9",
        "分别不小于所有消费者份额之和，并留出重试余量",
        "不可只改本地参数",
        "预算、RPM、TPM、账号并发和模型实际支持能力必须单独预检；服务端队列放大只会积压，不能制造供应商容量。",
    )
    add_row(
        "租户份额",
        "llm_gateway.tenant_max_share_pct / instance_profile.llm_lane_tenant_max_share_pct",
        tenant_share,
        tenant_share_source,
        "50%（small 对照）",
        "M2 等权场景保持有限份额；不要让单租户占满共享模型池",
        "公平性场景必须核对",
        "模型池的租户份额会影响 M2 的 Jain 和 M3 的单租户洪泛；必须把它和实际独立凭据、队列等待一起报告。",
    )
    add_row(
        "模型行为",
        "model.llm.extra_params.thinking.type",
        thinking_type,
        thinking_source,
        "按被测线上开关固定",
        "纯容量对比建议 disabled；开启时单独成组",
        "必须固定/不可混组",
        "thinking 会改变 LLM 生成时间和 token 消耗；同一份 C=16/C=64 对比必须使用相同开关，否则放大倍数不能归因于并发。",
    )

    fanout_executor, fanout_executor_source = resolve(
        ("scheduling", "fanout", "executor_workers"), "fanout_executor_workers", 0
    )
    fanout_engine, fanout_engine_source = resolve(
        ("scheduling", "fanout", "engine_max_inflight"), "fanout_engine_max_inflight", 0
    )
    add_row(
        "Fan-out 调度",
        "scheduling.fanout.executor_workers / engine_max_inflight",
        f"{display(fanout_executor)} / {display(fanout_engine)}",
        f"{fanout_executor_source}; {fanout_engine_source}",
        "0 / 0（profile-derived，需以启动日志解释）",
        "engine_max_inflight 起步64；executor 按 CPU 和 fan-out 调整",
        "按实际引擎数量设置",
        "每个 Recall 可能分叉多个引擎；engine_max_inflight 是每引擎/扇出限制，不等于 recall.max_inflight，也不应无条件设成 128。",
    )

    commit_queue, commit_queue_source = resolve(
        ("scheduling", "commit", "queue_max"), "commit_queue_max", small_defaults["commit_queue"]
    )
    commit_quota, commit_quota_source = resolve(
        ("scheduling", "commit", "tenant_quota"), "commit_tenant_quota", small_defaults["commit_quota"]
    )
    commit_executor, commit_executor_source = resolve(
        ("scheduling", "commit", "executor_workers"), "commit_executor_workers", small_defaults["commit_executor"]
    )
    commit_gate, commit_gate_source = resolve(
        ("scheduling", "commit", "gate_workers"), "commit_gate_workers", small_defaults["commit_gate"]
    )
    commit_tenant_inflight, commit_tenant_inflight_source = resolve(
        ("scheduling", "commit", "tenant_inflight_max"),
        "commit_tenant_inflight_max",
        0,
        "未配置/需以运行时语义核验",
    )
    commit_timeout, commit_timeout_source = resolve(
        ("commit_pipeline", "engine_timeout_seconds"),
        None,
        900,
        "版本默认/需启动日志核验",
    )
    add_row(
        "Commit 调度",
        "scheduling.commit.queue_max / tenant_quota",
        f"{display(commit_queue)} / {display(commit_quota)}",
        f"{commit_queue_source}; {commit_quota_source}",
        f"{small_defaults['commit_queue']} / {small_defaults['commit_quota']}",
        "queue=512；quota 按 M2/M3 拓扑（建议64或128）",
        "混合 Search+Commit 时核对",
        "队列变大只延后拒绝，不会提高完成吞吐；202 仍要轮询终态，不能以提交数代替完成数。",
    )
    add_row(
        "Commit 执行池",
        "scheduling.commit.executor_workers / gate_workers",
        f"{display(commit_executor)} / {display(commit_gate)}",
        f"{commit_executor_source}; {commit_gate_source}",
        f"{small_defaults['commit_executor']} / {small_defaults['commit_gate']}",
        "4+4 或5+3 起步；总数不超过 4 vCPU 的安全预算",
        "不要扩成64",
        "4U8G 下长 Commit 应保持有界执行池，并把 Search 与 Commit 隔离；把 worker 数直接放到 64 会制造 CPU 争用和更长尾延迟。",
    )
    add_row(
        "Commit 租户闸门",
        "scheduling.commit.tenant_inflight_max",
        commit_tenant_inflight,
        commit_tenant_inflight_source,
        "0（版本语义需确认）",
        "按每租户 Commit 强度设有界值；建议先4，不等于 Search C=64",
        "混合负载时核对",
        "该值限制单租户未完成 Commit 数；放大到64会让一个租户占满 CPU/模型资源，设为0的含义必须从目标版本启动日志确认，不能猜测。",
    )
    add_row(
        "Commit 生命周期",
        "commit_pipeline.engine_timeout_seconds",
        commit_timeout,
        commit_timeout_source,
        "900s（对照）",
        "保持足够长；按真实终态轮询，不设短 DDL",
        "不要缩短",
        "Commit 天然可能比 Search 长；短超时会把 pending 误记成失败，并破坏 M2/M3 的 overlap 和 M5 的恢复分母。",
    )

    cache_max, cache_max_source = resolve(
        ("scheduling", "tenant_cache", "max_cached_tenants"), "max_cached_tenants", small_defaults["cache_max"]
    )
    cache_overshoot, cache_overshoot_source = resolve(
        ("scheduling", "tenant_cache", "active_overshoot"), "tenant_cache_active_overshoot", small_defaults["cache_overshoot"]
    )
    cache_cap, cache_cap_source = resolve(
        ("scheduling", "tenant_cache", "hard_cap"), "tenant_cache_hard_cap", small_defaults["cache_hard_cap"]
    )
    add_row(
        "租户缓存",
        "tenant_cache.max_cached_tenants / active_overshoot / hard_cap",
        f"{display(cache_max)} / {display(cache_overshoot)} / {display(cache_cap)}",
        f"{cache_max_source}; {cache_overshoot_source}; {cache_cap_source}",
        f"{small_defaults['cache_max']} / {small_defaults['cache_overshoot']} / {small_defaults['cache_hard_cap']}",
        "保持内存预算；不因 C=64 自动改成64",
        "不要为并发数字盲改",
        "64 个在途请求不等于 64 个常驻热租户；缓存硬上限触发、淘汰和启动校验本身要作为容量证据记录。",
    )

    retrieval_deadline, retrieval_deadline_source = resolve(
        ("scheduling", "retrieval", "deadline_s"), "retrieval_deadline_s", 40
    )
    llm_deadline = nested(config, "model", "llm_call_deadline_seconds")
    embed_deadline = nested(config, "model", "embedding_call_deadline_seconds")
    if llm_deadline is None:
        llm_deadline = 120
    if embed_deadline is None:
        embed_deadline = 45
    add_row(
        "超时与熔断",
        "scheduling.retrieval.deadline_s / model.llm_call_deadline_seconds / model.embedding_call_deadline_seconds",
        f"{display(retrieval_deadline)}s / {display(llm_deadline)}s / {display(embed_deadline)}s",
        retrieval_deadline_source,
        "40s / 120s / 45s",
        "保持长 Commit/Recall 的真实等待；不要用短 deadline 截断",
        "不要通过缩短超时提速",
        "超时数量应区分 Provider 慢、服务端排队和客户端未发；缩短 deadline 只会把 pending 伪装成失败，破坏容量和恢复分母。",
    )

    chart: list[dict[str, Any]] = []

    def chart_value(label: str, value: Any, suffix: str) -> None:
        parsed = number(value)
        if parsed is not None:
            shown = display(value)
            chart.append({"label": label, "value": parsed, "display": f"{shown}{suffix}"})

    chart_value("HTTP workers · small 默认", small_defaults["http"], "")
    chart_value("HTTP workers · 当前", http, "")
    chart_value("HTTP workers · C=64 起点", 256, "")
    chart_value("Retrieval permits · small 默认", small_defaults["admission"], "")
    chart_value("Retrieval permits · 当前", admission, "")
    chart_value("Retrieval permits · C=64 起点", 64, "")
    chart_value("Recall max_inflight · small 默认", small_defaults["recall_max_inflight"], "")
    chart_value(
        "Recall max_inflight · 当前（0=关闭上限）" if recall_cap_disabled else "Recall max_inflight · 当前",
        recall_max,
        "",
    )
    chart_value("Recall max_inflight · C=64 起点", 64, "")

    recommended_config = {
        "scheduling": {
            "http": {"max_workers": 256},
            "retrieval": {"admission_permits": 64},
            "tenant": {"qps": 128, "concurrency": 64},
            "fanout": {"engine_max_inflight": 64},
            "commit": {
                "queue_max": 512,
                "tenant_quota": 64,
                "executor_workers": 4,
                "gate_workers": 4,
                "tenant_inflight_max": 4,
            },
            "llm_gateway": {
                "llm_max_concurrent": 128,
                "embed_max_concurrent": 128,
                "recall_llm_max_concurrent": 64,
                "recall_embed_max_concurrent": 64,
                "provider_budget_llm": "按所有消费者之和",
                "provider_budget_embed": "按所有消费者之和",
                "tenant_max_share_pct": 50,
            },
        },
        "recall": {
            "max_inflight": 64,
            "concurrency": {
                "engine": {"max_concurrent": 64, "queue_capacity": 256, "max_queued_per_tenant": 64},
                "intent_llm": {"max_concurrent": 64, "queue_capacity": 256, "max_queued_per_tenant": 64},
                "query_embedding": {"max_concurrent": 64, "queue_capacity": 256, "max_queued_per_tenant": 64},
                "rerank": {"max_concurrent": 64, "queue_capacity": 256, "max_queued_per_tenant": 64},
            },
        },
        "model": {"llm": {"extra_params": {"thinking": {"type": "disabled"}}}},
    }

    return {
        "scope": "仅 EchoMem 服务端参数；不包含客户端 worker、连接池或压测脚本参数。",
        "interpretation": "C=64 指总的同时在途 Search 请求。服务端参数放开后仍须以实际 peak_inflight、请求状态、阶段日志、Prometheus 和 Provider 返回码核验，不把配置数字当成实测吞吐。",
        "baseline_label": "4U8G small 默认/已核验 replica 对照",
        "tuning_label": "C=64 诊断调优起点（需要独立结果目录，不是生产保证）",
        "config_source": str(config_path) if config_path else "未提供 preflight_config",
        "rows": rows,
        "chart": chart,
        "topologies": [
            {"topology": "1 租户 × 64", "total_inflight": 64, "per_tenant": 64, "meaning": "单租户压力；需要该租户 concurrency 至少64"},
            {"topology": "4 租户 × 16", "total_inflight": 64, "per_tenant": 16, "meaning": "多租户等权；每租户独立 key/user/session"},
            {"topology": "8 租户 × 8", "total_inflight": 64, "per_tenant": 8, "meaning": "更多租户；总在途仍为64，不是8 QPS"},
        ],
        "rules": [
            "先看外层 Recall max_inflight 和 Retrieval admission，再看各 Recall 子阶段；只改子阶段不会解除外层拒绝。",
            "如果目标是 64 个 Search 同时进入 Retrieval，按当前 4:1 校验将 HTTP max_workers 配到至少 256，并保存启动后的 resolved 值。",
            "Query embedding、intent、rerank 是否需要同档放开，取决于真实路由和 trace；未走到的阶段不应为了表格数字盲目扩容。",
            "总 LLM/Embedding 池、Recall/episode 份额、provider budget 和外部账号限额必须整体核算；不能只把一个字段改大。",
            "默认基线与调优组分开运行、分开报告；默认配置下出现 429/503 是默认行为证据，不应被调优结果覆盖。",
            "每次修改后重启专用 Core，并记录 instance_profile_resolved、provider_budget_configured 和实际资源限制。",
        ],
        "do_not_change": [
            "不要把 tenant cache hard_cap 因为 C=64 直接改成64；先观察淘汰、内存和启动校验。",
            "不要把 Commit executor/gate 直接改成64；4U8G 先保持总执行槽在 CPU 可承受范围内。",
            "不要用缩短 retrieval/LLM/Embedding deadline 的方式消除 pending、排队或长 Commit。",
            "不要把本地队列放大后仍收到的 Provider 429、RPM/TPM 限流报告成 EchoMem 容量。",
        ],
        "references": [
            {
                "name": "历史 C=64 高限诊断快照",
                "source": "/Users/chx/pr33-deepseek-official-4k-c64-20260910/topology-64/deployment-parameters.json",
                "values": "HTTP=256；admission=64；recall_max_inflight=128；Recall stages=128/512/128；LLM/Embedding 总池=256；Commit=32+32",
                "meaning": "仅作为曾经使用过的参数对照；它不是 4U8G 的安全推荐，也不能替代本轮 resolved 配置和资源证据。",
            }
        ],
        "config_snippet": json.dumps(recommended_config, ensure_ascii=False, indent=2),
    }


def _metric_m1() -> dict[str, Any]:
    return {
        "code": "M1",
        "name": "单实例容量、最大热用户和 DAU",
        "state": "计划",
        "reflects": "在固定 EchoMem 实例和真实模型下，单实例能持续承载多少活跃热用户；同时把可持续请求能力按明确业务假设换算成 read-heavy、balanced、write-heavy 三种等价 DAU。",
        "method": "按 C=1、C=8、C=16、C=64 四档执行闭环 Search；C=1 是单并发基线，其余档位逐步增加总在途请求。每个压测身份有自己的 user/session；C 值表示总的同时在途 HTTP 请求，不是租户数。Search 只使用提前注入并验证过的自然语言记忆召回问题，不混入 no-recall。每个请求结束后立即补发，实际 peak_inflight 必须从请求记录核验。",
        "boundary": "C=1/8/16/64 是同一套 memory_profile 阶段对比。4 租户×1 user×1 session 承载四档总在途请求，用于隔离 Search/Recall 放大，不能直接代替包含 Commit 的业务容量。最大容量还要看持续拒绝、崩溃、OOM 和停压后积压是否恢复。",
        "flow": [
            {"label": "真实 seed", "detail": "每租户独特自然语言事实"},
            {"label": "C=1", "detail": "独立基线"},
            {"label": "C=8", "detail": "闭环 Search"},
            {"label": "C=16", "detail": "闭环 Search"},
            {"label": "C=64", "detail": "闭环 Search"},
            {"label": "drain", "detail": "停压后观察积压"},
            {"label": "compare", "detail": "阶段 P95 64/16"},
        ],
        "chart_title": "计划并发形状（目标值，不是实测值）",
        "chart": [
            {"label": "C=1 基线", "value": 1, "display": "1 在途"},
            {"label": "C=8", "value": 8, "display": "8 在途"},
            {"label": "C=16", "value": 16, "display": "16 在途"},
            {"label": "C=64", "value": 64, "display": "64 在途"},
        ],
        "cases": [
            {
                "id": "m1-c1-baseline",
                "goal": "建立单并发 Search/模型阶段基线",
                "actors": "1 个独立租户；1 user；1 session；C=1",
                "load": "仅 memory-recall Search；1 个自然语言问题池",
                "window": "预热后短测；停压并确认 peak=1",
                "evidence": "请求记录、质量命中、HTTP/传输错误、阶段日志/Prom、资源采样",
            },
            {
                "id": "m1-c8",
                "goal": "观察低并发进入稳定区后的阶段和端到端变化",
                "actors": "与其他档位相同的 4 个独立租户；每租户 1 user/1 session；总 C=8",
                "load": "闭环 Search；同一已验证记忆和问题池",
                "window": "warmup -> measurement -> drain；保留计划/已发/未发",
                "evidence": "peak_inflight=8、Search P50/P95/P99、memory_profile 阶段分布、CPU/RSS",
            },
            {
                "id": "m1-c16",
                "goal": "测 16 在途下的端到端和 memory_profile 放大",
                "actors": "4 个独立租户；每租户 1 user/1 session；总 C=16",
                "load": "闭环 Search；每响应完成后同身份补发",
                "window": "warmup -> measurement -> drain；窗口边界独立保存",
                "evidence": "peak_inflight=16、Search 分母、memory_profile P50/P95/P99、CPU/RSS",
            },
            {
                "id": "m1-c64",
                "goal": "测 64 在途下的拥塞、阶段放大和资源变化",
                "actors": "与 C=16 相同的独立租户/用户/session 拓扑；总 C=64",
                "load": "相同 query/seed，只改变 target_concurrency",
                "window": "与 C=16 同口径；拒绝和未发请求不删除",
                "evidence": "peak_inflight=64、429/503 分类、阶段 P95、积压恢复、CPU/RSS",
            },
        ],
        "fields": [
            {"field": "target_concurrency / peak_inflight_requests", "meaning": "配置目标与实际同时在途请求", "denominator": "目标和实测分别报告；peak 未达到时不能称为该档已测", "source": "capacity measurement / records.csv"},
            {"field": "Search planned / sent / not_sent / HTTP status", "meaning": "发压是否真正送达，以及 429/503/超时/传输错误", "denominator": "所有计划请求；not_sent 不从计划分母消失", "source": "records.csv + level measurement"},
            {"field": "P50 / P95 / P99 / throughput", "meaning": "端到端 Search 延迟和严格成功吞吐", "denominator": "延迟有效样本；错误和质量失败另列但保留总分母", "source": "records.csv"},
            {"field": "recall hits / attempts / quality failures", "meaning": "真实预期事实是否被返回", "denominator": "所有已发 recall query；HTTP 200 但空召回仍是失败", "source": "semantic corpus assessor"},
            {"field": "memory_profile stage P50/P95/P99", "meaning": "记忆画像阶段自身耗时", "denominator": "同一运行窗口的真实日志或 Prom 增量样本", "source": "recall_stage_completed / Prom Histogram"},
            {"field": "CPU / RSS / backlog", "meaning": "资源压力和停压后的排空状态", "denominator": "每档采样时间窗；不把缺失采样写成 0", "source": "Docker/resource samples + recovery"},
        ],
        "formulas": [
            "memory_profile 放大 = memory_profile_P95(C=64) / memory_profile_P95(C=16)，C=8 作为中间档；只接受同一版本、同一拓扑、同一 seed、独立窗口的真实阶段样本。",
            "操作边界 = 最后可持续且停压后排空的档位；首个持续拒绝、超时、崩溃、OOM 或不排空档位单列。Provider 429/鉴权错误先归为外部模型错误，不自动写成硬件极限。",
            "DAU 换算 = sustainable_RPS × 86400 / 每用户每日请求数 / 峰均比。DAU 是业务模型换算，不是直接测得的用户数；Search 与 Commit 分别算，并给较小的保守值。",
            "配置的热用户数、租户数、客户端 worker 数和实际 peak_inflight 是四个不同字段，报告必须同时展示。",
        ],
        "gaps": [
            "旧复测 profile 可能只有 m1_concurrency_levels=[16,64]；当前默认方案补齐 C=1、C=8，并保存四档独立证据。",
            "若 C=64 主要返回 HTTP 503/429，阶段样本仍可比较，但业务容量/质量结论只能写拥塞现象和责任码。",
            "要宣布容量边界，至少需要相邻档位、停压排空和资源/容器状态；单次错误不能外推全部实例容量。",
        ],
        "modules": [
            {"module": "HTTP 入口 / Retrieval admission", "observe": "入口在途、RETRIEVAL_BUSY、HTTP_LANE_SATURATED、实际 peak", "improve": "把 admission 与客户端 target 分开记录；给拒绝原因、Retry-After 和每租户配额打点"},
            {"module": "Recall / memory_profile", "observe": "memory_profile、query_embedding、engine_execution 的 trace 阶段", "improve": "缓存画像与 query 向量；批量/异步画像加载；限制单请求 fan-out"},
            {"module": "LLM/Embedding gateway", "observe": "模型阶段耗时、429、队列等待、Provider budget", "improve": "按用途分池、批量 embedding、退避和预算隔离；不得用扩大本地队列掩盖供应商限额"},
            {"module": "测试平台发压器", "observe": "planned/sent/not_sent、generator_lag、peak_inflight", "improve": "闭环槽位独立计数；发压端饱和必须与服务拒绝分开"},
        ],
        "evidence": ["M1/concurrency/level-1-search-measurement.json", "M1/concurrency/level-8-search-measurement.json", "M1/concurrency/level-16-search-measurement.json", "M1/concurrency/level-64-search-measurement.json", "structured-stage-events.jsonl", "metrics_samples.csv"],
    }


def _metric_m2() -> dict[str, Any]:
    return {
        "code": "M2",
        "name": "同档位多租户公平性",
        "state": "计划",
        "reflects": "在多个真实、互相独立的租户同时使用 Search 和 Commit 时，是否每个同档位租户都获得接近等权的完成机会；它看的是不同租户之间，不是同一租户的多个 session。",
        "method": "分别运行 4 租户和 8 租户。每个租户使用独立凭据、独立 user、自己的 Search session，并用自己的写 session 执行 open -> add x4 -> Commit -> 轮询终态。所有租户使用相同的 offered Search/Commit 速率和测量窗口；Commit 完成吞吐与 Search P95 按租户单独计算。",
        "boundary": "M2 的等权 Jain 只能用独立租户、同档位、同一测量窗口数据。M3 的 8:4:2:1 / 1:2:4:8 是异构负载，用来观察权重和吵闹邻居，不能混入 M2 等权指数。",
        "flow": [
            {"label": "4 tenants", "detail": "独立 key / session"},
            {"label": "equal arrival", "detail": "同速率到达"},
            {"label": "Search + Commit", "detail": "同窗并行"},
            {"label": "terminal poll", "detail": "202 不算完成"},
            {"label": "Jain", "detail": "逐租户计算"},
        ],
        "chart_title": "计划公平性档位（目标租户数）",
        "chart": [
            {"label": "4T 等权", "value": 4, "display": "4 独立租户"},
            {"label": "8T 等权", "value": 8, "display": "8 独立租户"},
        ],
        "cases": [
            {
                "id": "m2-fairness-4t",
                "goal": "4 个同档位租户的公平性",
                "actors": "4 个不同 tenant key；每租户独立 user；Commit 独立 session",
                "load": "每租户 Search 1 RPS；Commit 计划 2 RPM；open/add/commit/poll",
                "window": "约 30s 预热；[30,300) 测量；停压后最多 180s 排空",
                "evidence": "每租户 offered/arrivals/completions、Search P95、两个 Jain、零完成租户",
            },
            {
                "id": "m2-fairness-8t",
                "goal": "扩展到 8 个同档位租户的公平性",
                "actors": "8 个不同 tenant key；不允许复用同一个 key 冒充租户",
                "load": "与 4T 相同的每租户 Search/Commit 计划",
                "window": "与 4T 相同的测量和 tail 口径",
                "evidence": "8 个租户逐租户完整分母；缺失租户不填 0",
            },
        ],
        "fields": [
            {"field": "tenant identity", "meaning": "租户是否由不同凭据真实隔离", "denominator": "4T/8T 预期租户数；重复 key 不计为独立租户", "source": "tenant manifest + preflight"},
            {"field": "Search offered/arrivals/P95", "meaning": "每租户实际到达与尾延迟", "denominator": "固定测量窗口内已发 Search；未发另列", "source": "records.csv"},
            {"field": "Commit planned/202/completed/pending", "meaning": "受理和终态完成分开", "denominator": "计划事务；202 只进入 accepted，不进入 completed", "source": "commit evidence + status polls"},
            {"field": "Jain_commit", "meaning": "各租户完成 Commit/s 的等权公平", "denominator": "x_i=每租户窗口内 completed/s；零完成租户保留", "source": "derived from per-tenant window"},
            {"field": "Jain_search", "meaning": "各租户 Search P95 倒数的等权公平", "denominator": "y_i=1/P95_i；缺失/非法 P95 不伪造为 0", "source": "derived from per-tenant window"},
        ],
        "formulas": [
            "Jain(x) = (sum(x_i)^2) / (n * sum(x_i^2))；n 是实际应测的独立租户数，零完成租户仍在分母。",
            "Commit 公平使用完成吞吐，不使用提交数或 202 数；Search 公平使用每租户 P95 的倒数，数值越大表示延迟越公平。",
            "等权窗口只包含同一测量时间段；窗口外排空单列，不能用 tail 完成量回填窗口内吞吐。",
            "所有租户都为 0 完成时 Commit Jain 没有业务意义，报告写明未定义和原因，不写成 1。",
        ],
        "gaps": [
            "必须拿到 4/8 个不同凭据及每租户完整记录；同一个 API key 多租户只能测并发，不能证明公平。",
            "客户端计划速率不等于实际到达速率；若 worker/轮询拖慢发送，保留 arrival gap 并降低证据状态。",
            "M2 不证明内部严格出队顺序，只回答结果层面的吞吐/延迟公平。",
        ],
        "modules": [
            {"module": "租户鉴权 / 配额", "observe": "每租户 key、tenant_id、quota/reject", "improve": "启动前做凭据唯一性校验；配额按租户显示而非共享全局池"},
            {"module": "Commit 调度", "observe": "per-tenant arrival、queue、completed、window", "improve": "独立租户配额和加权公平队列；轮询不占用 Search worker"},
            {"module": "Search admission", "observe": "每租户 P95、queue wait、429/503", "improve": "租户级 admission 与全局池分层，避免一个租户耗尽共享槽位"},
        ],
        "evidence": ["m2-fairness-4t/records.csv", "m2-fairness-8t/records.csv", "m2-fairness-*/summary.json", "tenant manifest (redacted)"],
    }


def _metric_m3() -> dict[str, Any]:
    return {
        "code": "M3",
        "name": "Commit 洪泛下 Search 优先级",
        "state": "计划",
        "reflects": "当后台 Commit 持续或突发洪泛时，交互式、带真实记忆召回的 Search 是否仍保持可用，及其 P95/P99、质量和错误如何变化。",
        "method": "先用同一批已验证热记忆测 Search baseline，再只增加后台 Commit。依次运行均匀洪泛、单租户洪泛和异构租户负载。每个 Commit 都由真实 open/add/Commit 产生，202 后按原 session/archive 轮询；只有 Search 时间戳落在已确认未终态 Commit 的重叠窗口内，才计入 flood overlap。",
        "boundary": "M3 测的是 Search 受到后台写入的实际影响；M2 测同档位租户公平。黑盒延迟下降/上升不能单独证明服务内部严格优先级，必须结合 recall queue wait、调度日志和阶段事件。",
        "flow": [
            {"label": "hot Search baseline", "detail": "无 Commit"},
            {"label": "uniform flood", "detail": "所有租户写入"},
            {"label": "single-tenant flood", "detail": "一个吵闹租户"},
            {"label": "heterogeneous", "detail": "不同权重"},
            {"label": "overlap compare", "detail": "只算确认重叠"},
        ],
        "chart_title": "计划洪泛场景（Commit barrier 数）",
        "chart": [
            {"label": "baseline", "value": 0, "display": "0 个未完成 Commit"},
            {"label": "uniform", "value": 64, "display": "64 个计划 Commit"},
            {"label": "single tenant", "value": 64, "display": "64 个计划 Commit"},
            {"label": "heterogeneous", "value": 16, "display": "4 租户权重"},
        ],
        "chart_color": "red",
        "cases": [
            {
                "id": "m3-baseline",
                "goal": "建立无后台写入的热召回基线",
                "actors": "4 个独立租户；各自预注入记忆和 recall query",
                "load": "只发 Search；不含 no-recall；query/tenant/worker 固定",
                "window": "与 flood 使用相同 Search 窗口和 seed",
                "evidence": "逐租户 Search P95/P99、质量、错误、recall stages",
            },
            {
                "id": "m3-flood-uniform",
                "goal": "观察所有租户一起 Commit 洪泛时的 Search",
                "actors": "4 个独立租户；Commit 均匀分配",
                "load": "Search 持续；在窗口中提交 barrier_count（默认 64）个 Commit",
                "window": "记录每个 Commit accepted/non-terminal 区间；只算 overlap Search",
                "evidence": "overlap Search、Commit 计划/202/完成/pending、基线对比",
            },
            {
                "id": "m3-flood-single-tenant",
                "goal": "识别单一吵闹租户对旁观租户的串扰",
                "actors": "T1 承担全部 Commit；T2-T4 继续各自 Search",
                "load": "与 uniform 相同的 Search；Commit 只发给 T1",
                "window": "按真实未终态区间对账，不把提交时刻当完成时刻",
                "evidence": "T1/T2/T3/T4 各自 P95/P99、错误、质量和 overlap",
            },
            {
                "id": "m3-heterogeneous-tenants",
                "goal": "模拟读多写少、均衡、写多读少的真实租户",
                "actors": "4 个独立租户；Search 权重 8:4:2:1；Commit 权重 1:2:4:8",
                "load": "独立 per-tenant arrival plan；展示计划和实际到达",
                "window": "同一测量窗口；窗口外 Commit 排空另列",
                "evidence": "逐租户权重、到达、Search P95/质量、Commit 完成量",
            },
        ],
        "fields": [
            {"field": "baseline vs overlap P95/P99", "meaning": "洪泛重叠期间 Search 尾延迟变化", "denominator": "只纳入确认存在未终态 Commit 的时间重叠样本；其余 Search 仍保留为非 overlap", "source": "records.csv + commit timeline"},
            {"field": "degradation", "meaning": "相对基线的 P95 劣化", "denominator": "同租户/同 query 类/同窗口口径；基线为 0 时不能除法", "source": "derived comparison"},
            {"field": "Commit lifecycle", "meaning": "后台压力是否真的形成", "denominator": "planned、202、rejected、completed、non-terminal 全部保留", "source": "commit evidence"},
            {"field": "tenant weights / actual arrivals", "meaning": "异构流量是否按计划发出", "denominator": "每租户计划与实际到达都要展示；不能只显示配置权重", "source": "arrival plan + records.csv"},
            {"field": "recall quality / stage queue", "meaning": "Search 变慢是否伴随质量/阶段排队变化", "denominator": "所有已发 recall Search；错误、空召回和降级不删除", "source": "semantic assessor + stage logs/Prom"},
        ],
        "formulas": [
            "劣化百分比 = (P95_overlap - P95_baseline) / P95_baseline × 100%；基线与 overlap 必须来自同一版本、同一租户和同一 query 池。",
            "确认 overlap = Search start_at 位于 Commit accepted_at 至最后一次成功非终态/终态观测区间；只看提交计划时刻不算 overlap 证据。",
            "严格优先级只能在服务端有 queue/dequeue/dispatch 序列时确认；客户端 P95 只能说明用户可见影响。",
            "异构场景权重用于模拟真实租户差异，不用于 M2 的等权 Jain；若客户端未按权重实际到达，报告为计划/实际不一致。",
        ],
        "gaps": [
            "至少需要 baseline、uniform、single-tenant、heterogeneous 四个独立场景；不能拿单一 503 结果宣称严格优先。",
            "Commit 的 202、终态和未完成区间必须真实对账；不能用短 deadline 把长 Commit 截成失败。",
            "Search 仍只用 memory-recall query；本轮明确不加入 no-recall，避免把意图拒绝混入召回压力。",
        ],
        "modules": [
            {"module": "路由 / 意图 / Recall admission", "observe": "query_embedding、intent、recall queue wait、入口拒绝", "improve": "交互 Search 独立 admission；按 query 复杂度限流和缓存"},
            {"module": "Commit scheduler / gate", "observe": "Commit queue、accepted-to-terminal、Search/Commit overlap", "improve": "Search 保留高优先级 lane；后台写入使用可抢占或有界队列"},
            {"module": "Atomic engine", "observe": "extraction、contradiction、persistence、vector publication 阶段", "improve": "拆分执行池和预算；长 Commit 不占满 Recall/HTTP worker"},
            {"module": "Provider gateway", "observe": "LLM/Embedding TTFB、queue wait、429/timeout", "improve": "按 Search/Commit 分池、批量 embedding、显式退避与预算"},
        ],
        "evidence": ["m3-baseline/records.csv", "m3-flood-uniform/records.csv", "m3-flood-single-tenant/records.csv", "m3-heterogeneous-tenants/records.csv", "structured-stage-events.jsonl"],
    }


def _metric_m4() -> dict[str, Any]:
    return {
        "code": "M4",
        "name": "单租户故障隔离",
        "state": "计划",
        "reflects": "任意一个被测租户出现请求级 delay/reject 时，其余租户 Search P95 和错误是否保持稳定，以及故障撤销后是否恢复。",
        "method": "对 4 个候选目标租户轮流注入 reject 和 delay，各重复 3 轮。每轮按 before -> during -> clear -> after 采集；目标租户和旁观租户都保留请求记录，但劣化分母只包含旁观租户。",
        "boundary": "本指标只覆盖测试控制面能注入的请求级租户故障，不外推模型断网、数据库损坏或所有租户故障。控制接口返回 200 不是故障生效证据，必须看到目标租户真实响应/延迟变化。",
        "flow": [
            {"label": "before", "detail": "旁观基线"},
            {"label": "inject", "detail": "reject / delay"},
            {"label": "during", "detail": "旁观 Search"},
            {"label": "clear", "detail": "撤销回执"},
            {"label": "after", "detail": "恢复观察"},
        ],
        "chart_title": "计划故障矩阵",
        "chart": [
            {"label": "reject", "value": 12, "display": "4 目标 × 3 轮"},
            {"label": "delay", "value": 12, "display": "4 目标 × 3 轮"},
        ],
        "chart_color": "amber",
        "cases": [
            {"id": "m4-reject-r1..r3", "goal": "目标租户拒绝隔离", "actors": "每轮 1 target + 3 bystanders；4 个 target 轮换", "load": "旁观者持续 memory-recall Search", "window": "before/during/after；3 repeats", "evidence": "控制回执、目标真实响应、旁观 P95/错误"},
            {"id": "m4-delay-r1..r3", "goal": "目标租户延迟隔离", "actors": "同上；独立租户凭据", "load": "目标 delay_ms；旁观者同速率 Search", "window": "delay TTL 内完成 during；撤销后再采样", "evidence": "延迟实际生效、旁观劣化、恢复数据"},
        ],
        "fields": [
            {"field": "fault target/type/receipt", "meaning": "谁被注入、哪种故障、控制面是否确认", "denominator": "24 个用例；每个 before/during/after 都要有", "source": "fault evidence"},
            {"field": "bystander P95 before/during/after", "meaning": "旁观租户可见劣化", "denominator": "目标租户排除；每个旁观者单列", "source": "records.csv"},
            {"field": "target exercised evidence", "meaning": "故障真的作用于请求", "denominator": "控制回执 + 目标响应/延迟变化，两者缺一不可", "source": "fault response + workload"},
        ],
        "formulas": ["旁观租户劣化 = (during_P95 - before_P95) / before_P95；按租户和重复分别算，整体只作汇总。", "缺控制回执、目标未受影响或旁观窗口缺失时直接写明阻塞原因，不用模糊状态覆盖。"],
        "gaps": ["需要专用测试容器和测试 Token；不能指向共享/生产实例。", "控制接口覆盖的是请求级故障，不等于底层存储或 Provider 故障。"],
        "modules": [
            {"module": "Tenant isolation / admission", "observe": "目标与旁观 tenant_id、配额、lane", "improve": "按租户隔离连接、队列和 worker；拒绝原因可诊断"},
            {"module": "Fault control", "observe": "注入/撤销回执和生效时间", "improve": "按 operation 细分 fault scope，并返回可对账的 fault_id"},
        ],
        "evidence": ["fault-isolation-*.json", "before/during/after records.csv", "tenant-observability-samples.json"],
    }


def _metric_m5() -> dict[str, Any]:
    return {
        "code": "M5",
        "name": "已返回 202 的 Commit 崩溃恢复、顺序与幂等",
        "state": "计划",
        "reflects": "Commit 已被服务接受但尚未终态时发生进程/容器崩溃，重启后原任务能否自主完成，消息是否不丢、不重复、不乱序，重复幂等键是否仍指向原任务。",
        "method": "每个样本写入带顺序号的真实消息，使用唯一幂等键提交；确认 HTTP 202 且状态仍 pending/running 后，在专用容器中 kill-9。重启后只轮询原 archive，待自主终态后再用相同幂等键重试，并读取 history/archive/cursor 对账。",
        "boundary": "202 只代表受理，不代表完成；重新创建替代 Commit 不能证明恢复。一次或少量样本只能说明这些故障窗口的观察结果，不能数学外推任意崩溃时机。",
        "flow": [
            {"label": "write ordered msgs", "detail": "记录 message id"},
            {"label": "202 pending", "detail": "保存 archive"},
            {"label": "kill-9", "detail": "专用容器"},
            {"label": "restart", "detail": "保持原任务"},
            {"label": "poll original", "detail": "自主终态"},
            {"label": "replay + reconcile", "detail": "幂等/顺序"},
        ],
        "chart_title": "恢复样本漏斗（计划数）",
        "chart": [
            {"label": "计划样本", "value": 3, "display": "3"},
            {"label": "已收 202", "value": 3, "display": "3（目标）"},
            {"label": "kill 时未终态", "value": 3, "display": "3（目标）"},
            {"label": "自主恢复", "value": 3, "display": "逐样本核验"},
        ],
        "chart_color": "amber",
        "cases": [
            {"id": "m5-recovery-1..3", "goal": "3 个独立崩溃恢复样本", "actors": "专用容器；一个测试租户/独立写 session", "load": "12 条有序消息；唯一幂等键；Commit -> 202 -> poll", "window": "202 后未终态立即 kill；重启后最长 180s 观察", "evidence": "每步时间线、原 archive、history/archive/cursor 对账"},
        ],
        "fields": [
            {"field": "planned / accepted_202 / unfinished_at_kill", "meaning": "恢复样本分母和 kill 时状态", "denominator": "所有计划样本；未收 202 也保留", "source": "recovery evidence"},
            {"field": "autonomous terminal status", "meaning": "原任务是否自己完成/失败", "denominator": "只轮询原 archive；不以替代任务完成填充", "source": "status poll"},
            {"field": "message set/order/cursor", "meaning": "消息集合、顺序和游标一致性", "denominator": "每个样本逐消息对账；缺少读取接口就是缺证据", "source": "history/archive/cursor"},
            {"field": "idempotency replay", "meaning": "同 key 重试是否复用原任务且不重复", "denominator": "自主终态后重试；匹配结果布尔化展示，不泄露 key", "source": "replay response"},
        ],
        "formulas": ["恢复通过样本必须同时满足：202、kill 时未终态、原任务自主终态、消息集合一致、顺序一致、cursor 一致、幂等重试一致。", "缺少任一对账接口时写明具体缺失，不把 completed 单字段当作 100% 重放。"],
        "gaps": ["必须获得专用容器 kill/restart 授权；共享服务不执行。", "服务若没有稳定 archive/idempotency/cursor 契约，只能输出恢复观察和缺失证据，不能宣称通过。"],
        "modules": [
            {"module": "Commit persistence / recovery", "observe": "accepted task、archive、recovery scan、terminal state", "improve": "先持久化幂等 receipt 再返回 202；启动扫描可恢复任务并记录代际"},
            {"module": "Atomic engine / cursor", "observe": "原子写入、vector publication、cursor advance 的阶段事件", "improve": "事务性游标推进和可重放 outbox；消息顺序使用单调序列"},
        ],
        "evidence": ["commit-recovery-*.json", "history/archive/cursor snapshots", "container lifecycle evidence"],
    }


def _metric_m6() -> dict[str, Any]:
    return {
        "code": "M6",
        "name": "每层、每租户的排队/等待/执行/拒绝四元组",
        "state": "计划",
        "reflects": "服务是否能按 tenant × lane 持续解释请求在哪里排队、等了多久、执行多久以及被拒绝多少，而不是只给一个全局 QPS。",
        "method": "在 M1-M5 运行期间持续采集受保护 tenant-observability 快照，并配合 /metrics、DEBUG JSON 日志和容器代际。覆盖 NORMAL、QUEUE、REJECT、RESET 四种状态；按实际启用 lane 生成固定 expected cells，逐帧校验缺失、重复、非法值和计数回退。",
        "boundary": "M6 是过程覆盖指标，不是某次最后快照有字段就算完成。/metrics 全局直方图只能补充，不能替代 tenant-observability 的逐租户矩阵。",
        "flow": [
            {"label": "expected matrix", "detail": "tenant × enabled lane"},
            {"label": "NORMAL", "detail": "持续快照"},
            {"label": "QUEUE/REJECT", "detail": "制造压力"},
            {"label": "RESET", "detail": "重启代际"},
            {"label": "validate", "detail": "缺失/回退/重复"},
        ],
        "chart_title": "矩阵规模示意（以 4T×2 lane 为例）",
        "chart": [
            {"label": "4 tenants", "value": 4, "display": "4"},
            {"label": "2 enabled lanes", "value": 2, "display": "2"},
            {"label": "expected cells", "value": 8, "display": "4×2"},
        ],
        "chart_color": "teal",
        "cases": [
            {"id": "m6-normal", "goal": "正常执行覆盖", "actors": "所有启用租户 × 所有生效 lane", "load": "低压 Search/Commit", "window": "before/during/after 快照", "evidence": "每 cell 的 queued/wait/exec/rejected"},
            {"id": "m6-queue-reject", "goal": "排队与拒绝覆盖", "actors": "同一租户和旁观租户", "load": "复用 M2/M3 压力；不另造无关负载", "window": "压力窗口内按固定间隔采样", "evidence": "queue depth、累计 wait/exec、rejected 增量"},
            {"id": "m6-reset", "goal": "重启代际与计数重置", "actors": "M5 专用容器", "load": "重启前后持续采集", "window": "按 process/container identity 分段", "evidence": "reset/restart identity、采样空档和回退"},
        ],
        "fields": [
            {"field": "queued", "meaning": "当前/快照时队列深度", "denominator": "每个 expected tenant×lane cell", "source": "tenant-observability endpoint"},
            {"field": "wait_seconds_total", "meaning": "累计排队等待时长", "denominator": "相邻有效快照做增量；重启代际分开", "source": "tenant-observability + Prom"},
            {"field": "exec_seconds_total", "meaning": "累计执行时长", "denominator": "相邻有效快照做增量；不由 HTTP 延迟减法得到", "source": "tenant-observability + stage logs"},
            {"field": "rejected_total", "meaning": "累计拒绝数", "denominator": "相邻有效快照做增量；计数回退要解释", "source": "tenant-observability + Prom"},
        ],
        "formulas": ["expected_cells = unique_active_tenants × effective_enabled_lanes；缺一 cell 不补零。", "累计值只在同一进程代际内做窗口增量；重启后重新分段，不能把回退误算为负请求。", "阶段耗时必须来自 recall_stage_completed、recall_engine_completed、memory_extraction_completed、atomic_pipeline_completed 或 Prom Histogram；禁止端到端相减。"],
        "gaps": ["需要受保护观测接口和准确 expected_lanes；接口缺失时 M6 明确阻塞。", "采样间隔和重启期间空档必须展示；最后一帧完整不能覆盖全过程缺失。"],
        "modules": [
            {"module": "Observability contract", "observe": "tenant×lane 四元组、采样时间、代际", "improve": "统一 schema、单调计数和 reset reason；为每层提供 tenant labels"},
            {"module": "Recall/Commit instrumentation", "observe": "stage queue wait、execution、rejection", "improve": "所有入口使用同一 trace_id；日志和 Prom 标签对齐"},
        ],
        "evidence": ["tenant-observability-samples.json", "metrics_samples.csv", "structured-stage-events.jsonl", "execution-manifest.json"],
    }


def _plan(profile_path: Path, profile: dict[str, Any], evidence_dir: Path | None) -> dict[str, Any]:
    evidence = _read_json(evidence_dir / "summary.json") if evidence_dir else None
    models = _models(profile)
    resource_source = evidence or {}
    if evidence:
        nested_m1 = ((evidence.get("metrics") or {}).get("M1") or {})
        if nested_m1.get("resource_evidence"):
            resource_source = {**evidence, "resource_evidence": nested_m1["resource_evidence"]}
    resource = _resource_limits(profile, resource_source)
    levels = profile.get("m1_concurrency_levels") or [1, 8, 16, 64]
    level_text = "、".join(f"C={value}" for value in levels)
    topologies = profile.get("m1_topologies")
    current = _current_evidence(evidence_dir)
    current_comparison = current.get("comparison") or {}
    if current_comparison.get("ready"):
        evidence_line = (
            f"已有快照显示 memory_profile P95 C=16 {current_comparison.get('p95_16_ms')}ms、"
            f"C=64 {current_comparison.get('p95_64_ms')}ms，阶段比值 {current_comparison.get('ratio')}x；"
            "这不是完整六项结论。"
        )
    else:
        evidence_line = "当前没有可审计的 16/64 阶段比值；必须保存两个有边界的运行窗口。"

    state_rows = [
        {
            "item": "M1 并发档位是否已接到脚本",
            "value": f"当前 profile: topology={topologies}; levels={levels}",
            "meaning": "concurrency topology 使用 target_concurrency 闭环发压；实际 peak 仍要从 measurement 核验。",
        },
        {
            "item": "C=1 基线",
            "value": "当前 profile 已配置" if 1 in levels else "当前 profile 未配置；正式方案要求补齐",
            "meaning": "C=1 用于单并发端到端/模型阶段基线，与 C=8/16/64 的总在途含义一致。",
        },
        {
            "item": "M2/M3 编排",
            "value": "同一 observation_run 按场景顺序执行，分别计窗",
            "meaning": "M2 4T/8T 等权；M3 baseline/uniform/single/heterogeneous；不把不同窗口合并。",
        },
        {
            "item": "Search 样本",
            "value": "只用已预注入、已验证的 memory-recall query",
            "meaning": "本轮不加入 no-recall；质量、空召回、降级和错误仍进分母。",
        },
        {
            "item": "模型名一致性",
            "value": f"当前 profile 要求 {profile.get('required_embedding_model') or models.get('embedding')}",
            "meaning": "必须以实际 preflight 的模型名为准；不同模型的延迟/限流证据不能混用。",
        },
        {
            "item": "Commit 语义",
            "value": "open -> add -> Commit -> poll terminal",
            "meaning": "202 是 accepted，不是 completed；窗口外 pending 另列，不能短 deadline 截断。",
        },
        {
            "item": "Soak",
            "value": "关闭",
            "meaning": "本方案不运行 7 小时长稳态；需要时另开明确的 soak 任务。",
        },
        {
            "item": "已有证据",
            "value": evidence_line,
            "meaning": "历史/当前证据只用于状态说明，不回填缺失的 M2-M6。",
        },
    ]

    profile_view = {
        "name": profile.get("name"),
        "source": str(profile_path),
        "base_url": profile.get("base_url"),
        "resource_container": profile.get("resource_container"),
        "resource_limits": resource,
        "models": models,
        "m1_topologies": topologies,
        "m1_concurrency_levels": levels,
    }

    workflow = [
        {"label": "Discover", "detail": "仓库、分支、版本、容器"},
        {"label": "Configure", "detail": "profile / 独立租户 / DEBUG JSON"},
        {"label": "Preflight", "detail": "真实 LLM + Embedding"},
        {"label": "Seed once", "detail": "自然语言事实 + 召回校验"},
        {"label": "M1", "detail": f"{level_text} 闭环 Search"},
        {"label": "M2", "detail": "4T / 8T Jain"},
        {"label": "M3", "detail": "baseline + 3 flood"},
        {"label": "M4-M6", "detail": "隔离 / 恢复 / 观测"},
        {"label": "Explain", "detail": "HTML + 原始分母"},
    ]
    schedule = [
        {"phase": "A. 预检", "scope": "EchoMem ready、真实模型、凭据唯一性、配置指纹、资源容器", "duration": "约 1-5 分钟", "output": "execution-manifest.json / preflight", "gate": "模型和服务都 READY；否则立即写明阻塞"},
        {"phase": "B. 一次性 seed", "scope": "每租户注入独特自然语言事实，open/add/Commit/终态轮询，再用 recall query 验证", "duration": "按真实 Commit；可能数分钟以上", "output": "seed-evidence.json / seed-progress.json", "gate": "目标租户的事实命中可对账；失败不隐藏"},
        {"phase": "C. M1", "scope": f"{level_text}；每档 warmup、measurement、drain", "duration": "短测约 5-20 分钟；正式时按 profile", "output": "M1/*/level-*、records.csv、resources", "gate": "每档检查 peak、计划/已发、错误、质量和积压"},
        {"phase": "D. M2", "scope": "4T 和 8T 等权 Search + Commit，窗口内完成吞吐/Jain", "duration": "约 10-20 分钟，取决于 Commit 排空", "output": "m2-fairness-*/summary.json", "gate": "每租户有独立凭据和完整窗口"},
        {"phase": "E. M3", "scope": "Search baseline、均匀洪泛、单租户洪泛、异构权重", "duration": "约 20-40 分钟，取决于 202 后终态", "output": "m3-*/summary.json + overlap timeline", "gate": "只对确认未终态重叠区间计算优先级影响"},
        {"phase": "F. M4-M6", "scope": "full 模式才执行故障注入、kill/restart 和全程 tenant×lane 采样", "duration": "可明显长于 M1-M3", "output": "fault/recovery/observability 原始证据", "gate": "专用容器和测试控制明确授权；缺接口直接报告原因"},
        {"phase": "G. 发布", "scope": "重算分母、阶段耗时、API 台账、错误归因、HTML", "duration": "约数十秒", "output": "report.html + summary/suite/CSV", "gate": "report.html 与结构化证据一致"},
    ]

    execution_matrix = {
        "rule": (
            "本方案只定义一套完整测试流程，固定执行 M1-M6。每个指标拆成独立执行单元，"
            "报告必须逐单元标记已完成、部分完成或尚未执行；不能用 M1-M3 的结果代替 M4-M6。"
        ),
        "rows": [
            {
                "unit": "M1-A",
                "metric": "M1",
                "scenario": "跨租户用户容量 1/2/4/8/16/32，必要时 64/128",
                "status": "PARTIAL",
                "evidence": "已复测到 32 用户；16/32 有 Atomic Engine 质量降级，未观察到崩溃/OOM/不可排空",
                "next_step": "继续升档并重复边界档，确定服务边界与质量边界",
            },
            {
                "unit": "M1-B",
                "metric": "M1",
                "scenario": "同租户多用户 1/2/4/8/16/32",
                "status": "BLOCKED",
                "evidence": "复用身份在 1/2/4/8 用户档全部 401 UNAUTHENTICATED",
                "next_step": "修复同租户用户凭据/Session 绑定后重跑",
            },
            {
                "unit": "M1-C",
                "metric": "M1",
                "scenario": "固定 4 租户的 C=1/8/16/64 并发基准",
                "status": "MEASURED",
                "evidence": "已完成 C=1/8/16/64，C=64 P95 约 30.27 秒",
                "next_step": "与用户容量档合并判断，不把并发数当用户数",
            },
            {
                "unit": "M2-A",
                "metric": "M2",
                "scenario": "4 租户等权 Search + Commit 公平性",
                "status": "PARTIAL",
                "evidence": "Search Jain 已计算；服务率存在错误/超时/空召回",
                "next_step": "健康基线和 Commit 窗口完整后重复",
            },
            {
                "unit": "M2-B",
                "metric": "M2",
                "scenario": "8 租户等权 Search + Commit 公平性",
                "status": "PARTIAL",
                "evidence": "Search Jain 已计算；窗口内无 Commit 完成，Commit Jain 无定义",
                "next_step": "延长窗口或降低 Commit 强度，保证窗口内有完成样本",
            },
            {
                "unit": "M3-A",
                "metric": "M3",
                "scenario": "无 Commit 健康 Search baseline",
                "status": "PARTIAL",
                "evidence": "基线自身有超时、错误和空召回，健康门禁失败",
                "next_step": "先修复 Search/模型/认证，再作为洪泛对照",
            },
            {
                "unit": "M3-B",
                "metric": "M3",
                "scenario": "均匀 Commit 洪泛",
                "status": "MEASURED",
                "evidence": "已采集确认重叠窗口、P95、503、超时和空召回",
                "next_step": "必须基于健康 baseline 重复才能解释劣化",
            },
            {
                "unit": "M3-C",
                "metric": "M3",
                "scenario": "单租户 Commit 洪泛",
                "status": "PARTIAL",
                "evidence": "已执行；停压截止仍有 3 个 Commit 未确认终态",
                "next_step": "补齐排空并在健康 baseline 下重复",
            },
            {
                "unit": "M3-D",
                "metric": "M3",
                "scenario": "异构 Search/Commit 权重",
                "status": "PARTIAL",
                "evidence": "已定义权重，需检查完整租户级窗口证据",
                "next_step": "补齐每租户权重、服务率和重叠对账",
            },
            {
                "unit": "M4",
                "metric": "M4",
                "scenario": "单租户故障注入与旁观租户恢复",
                "status": "NOT_EXECUTED",
                "evidence": "本轮未选择/未执行",
                "next_step": "启用专用 fault-control，执行 before/during/after",
            },
            {
                "unit": "M5",
                "metric": "M5",
                "scenario": "202 Commit 崩溃恢复、顺序和幂等",
                "status": "NOT_EXECUTED",
                "evidence": "本轮未选择/未执行",
                "next_step": "专用容器 kill/restart，保留原 archive 对账",
            },
            {
                "unit": "M6",
                "metric": "M6",
                "scenario": "tenant × lane 四元组和 RESET 代际",
                "status": "NOT_EXECUTED",
                "evidence": "本轮未选择/未执行",
                "next_step": "开启 tenant-observability，持续采样 NORMAL/QUEUE/REJECT/RESET",
            },
        ],
        "common_fields": [
            "实际 target/peak in-flight、HTTP 状态、传输错误、429/503、超时和未发请求",
            "认证/租户/session 前置条件与真实 memory-recall 非空比例；不能把 401 当容量失败",
            "阶段 P50/P95/P99、queue wait、模型/Provider 返回码、CPU/RSS和停压后排空",
            "计划、已发、成功、质量失败、pending、窗口外完成和原始证据路径",
        ],
        "order": [
            "预检、资源和真实模型验证；一次性准备并验证记忆 seed。",
            "M1-A/M1-B/M1-C：用户容量、同租户用户、并发基准。",
            "M2-A/M2-B：4/8 租户公平性，Commit 和 Search 分开统计。",
            "M3-A/M3-B/M3-C/M3-D：baseline、均匀/单租户/异构洪泛。",
            "M4：故障隔离；M5：崩溃恢复；M6：全过程四元组观测。",
            "生成单一完整 report.html；未执行项保留 NOT_EXECUTED，部分项保留缺口和分母。",
        ],
    }

    commands = [
        {
            "title": "只生成本方案 HTML（不发请求）",
            "when": "本命令已经由测试平台脚本调用；可在任何时候重建方案页。",
            "command": (
                "cd /Users/chx/.tmp/Memory-System-Eval-Harness-live-20260914-copy\n"
                ".venv/bin/python scripts/build_echomem_test_plan.py \\\n"
                "  --profile /path/to/m1m2m3.profile.json \\\n"
                "  --evidence-dir /path/to/previous-run \\\n"
                "  --out-dir results/echomem-plan"
            ),
        },
        {
            "title": "按选择运行指标（短窗口定位）",
            "when": "用户显式选择指标；需要快速定位时使用 quick 参数。它只缩短窗口和阶梯，不改变指标口径。",
            "command": (
                "cd /path/to/Memory-System-Eval-Harness\n"
                "performance/targets/echomem/run_six_metrics.sh quick \\\n"
                "  .local-stress/m1m2m3.profile.json \\\n"
                "  results/m1m2m3-quick \\\n"
                "  .local-stress/test.env"
            ),
        },
        {
            "title": "按选择运行指标（正式窗口）",
            "when": "用户显式选择 M1、M2、M3 或其组合；profile 必须包含对应指标所需的用户/租户、真实模型和观测配置。",
            "command": (
                "cd /path/to/Memory-System-Eval-Harness\n"
                ".venv/bin/python -m performance.targets.echomem.observation_run \\\n"
                "  --profiles .local-stress/m1m2m3.profile.json \\\n"
                "  --metrics M1,M2,M3 \\\n"
                "  --env-file .local-stress/test.env \\\n"
                "  --out-dir results/m1m2m3-$(date +%Y%m%d-%H%M%S)"
            ),
        },
        {
            "title": "完整六项：M1-M6",
            "when": "只指向专用测试部署；会执行故障注入和容器重启，Soak 仍关闭。",
            "command": (
                "cd /path/to/Memory-System-Eval-Harness\n"
                "performance/targets/echomem/run_six_metrics.sh full \\\n"
                "  .local-stress/m1m2m3.profile.json \\\n"
                "  results/six-metrics-$(date +%Y%m%d-%H%M%S) \\\n"
                "  .local-stress/test.env"
            ),
        },
        {
            "title": "中断后续跑",
            "when": "只在同一 profile、同一输出目录且版本/配置没有变化时使用 --resume。",
            "command": (
                ".venv/bin/python -m performance.targets.echomem.observation_run \\\n"
                "  --profiles .local-stress/m1m2m3.profile.json \\\n"
                "  --env-file .local-stress/test.env \\\n"
                "  --out-dir results/six-metrics-existing \\\n"
                "  --resume"
            ),
        },
    ]

    contracts = [
        {
            "title": "真实请求与独立租户",
            "description": "所有正式结论必须来自真实 EchoMem、真实 LLM/Embedding 和独立租户凭据；不能用 fake 或同一 key 冒充多租户。",
            "rows": [
                {"item": "Search", "value": "HTTP 200 还要检查预期事实；空召回、降级、错误保留"},
                {"item": "Commit", "value": "202=accepted；必须轮询 completed/failed，pending 不删除"},
                {"item": "tenant", "value": "每个租户独立 auth key/user/session；报告不输出 key"},
            ],
        },
        {
            "title": "阶段耗时与 trace 关联",
            "description": "端到端 HTTP latency、模型耗时和内部阶段耗时分开展示，不能用相减推算。",
            "rows": [
                {"item": "Search/Recall logs", "value": "recall_stage_completed、recall_engine_completed、dashscope_rerank_operation、http_request_completed"},
                {"item": "Commit/Atomic logs", "value": "memory_extraction_completed、atomic_pipeline_completed 及 macro_stage_timings_ms"},
                {"item": "Prometheus", "value": "memrouter planning/stage/queue、recall、router embedding、engine model duration/TTFB"},
                {"item": "join key", "value": "按脱敏 trace_id 关联；没有真实样本才写不可观测并给原因"},
            ],
        },
        {
            "title": "错误和分母",
            "description": "每个计划项都保留计划、已发、未发、HTTP/传输错误、质量失败、202、终态和 pending。",
            "rows": [
                {"item": "external provider", "value": "鉴权、余额、429、模型 timeout 单独归因"},
                {"item": "EchoMem", "value": "admission、queue、bulkhead、routing、atomic engine 分开"},
                {"item": "harness", "value": "generator_saturated/not_sent 不能伪装成服务拒绝"},
            ],
        },
        {
            "title": "API 和边界审计",
            "description": "在正式报告后附接口调用台账与负向契约探针，不把探针请求混入性能分母。",
            "rows": [
                {"item": "runtime ledger", "value": "ready、open、add、search、commit submit/status、history/archive/cursor、metrics"},
                {"item": "invalid input", "value": "缺认证、畸形 JSON、缺字段、非法类型、不存在资源、无 token 控制接口"},
                {"item": "payload boundary", "value": "真实自然语言短/中/长样本；超长边界独立记录，不用随机字符替代"},
            ],
        },
        {
            "title": "服务端配置与客户端发压分离",
            "description": "EchoMem 的 worker、queue、model concurrency 和 provider budget 只作为被测解释证据；测试平台不能读取它们后偷偷降载。",
            "rows": [
                {"item": "client", "value": "target_concurrency、search_workers、actual_peak 独立记录"},
                {"item": "server", "value": "admission、lane、model pool、tenant quota 原值和拒绝原因保留"},
                {"item": "comparison", "value": "默认配置和调优配置必须分目录，不能混成一个容量结论"},
            ],
        },
    ]

    improvements = [
            {"module": "路由 / Recall", "observed_or_risk": "intent/query_embedding/engine 阶段可能串行或排队，C=64 时尾延迟和空召回放大", "change": "按 query 类型和复杂度做路由；缓存 memory_profile/query vector；限制 fan-out；为每阶段暴露 queue wait", "benefit": "降低 Search 长尾并区分路由、Embedding 和引擎责任", "rerun": "同一 seed 重跑 C=1/8/16/64，比较阶段 P95 和质量分母"},
        {"module": "调度 / Admission", "observed_or_risk": "HTTP/Recall/tenant lane 满时出现 429/503，客户端可能误判为模型慢", "change": "Search/Commit 分离 admission 和 worker；租户级配额；返回结构化 reason_code 与 Retry-After", "benefit": "减少吵闹邻居串扰，保留可诊断拒绝", "rerun": "M2 4/8T + M3 uniform/single flood，核对 per-tenant queue/reject"},
        {"module": "LLM / Embedding 网关", "observed_or_risk": "模型 TTFB、供应商限流和本地并发池混在一起，扩大队列不能提高 provider 限额", "change": "Search/Commit 用途分池；批量 embedding；预算、重试和退避独立；记录 provider code/TTFB", "benefit": "把外部限流与 EchoMem 容量分开，减少无效等待", "rerun": "预检 + 负载窗口 Prom/JSON 交叉校验，比较 provider 与内部 queue wait"},
        {"module": "Commit / Atomic Engine", "observed_or_risk": "长 Commit 占用执行槽，202 后 pending 时间长，可能干扰 Search", "change": "accepted receipt 先持久化；Commit 分阶段有界池；Search 高优先级；恢复扫描和 cursor 原子化", "benefit": "M3 更可控，M5 可验证不丢序/不重复", "rerun": "M3 overlap + M5 3 个 202 pending kill/restart 样本"},
        {"module": "租户隔离 / 控制面", "observed_or_risk": "请求级故障或单租户高负载可能耗尽共享连接/队列", "change": "连接、admission、worker 和缓存按租户隔离；fault scope 支持 operation 维度", "benefit": "旁观租户 P95 更稳定，故障影响更容易定位", "rerun": "M4 24 用例，逐旁观租户 before/during/after"},
        {"module": "可观测性", "observed_or_risk": "只有全局指标或末次快照时，无法解释哪层/哪个租户在等", "change": "统一 trace_id、tenant_id、lane、generation；输出 queued/wait/exec/rejected 四元组和 reset reason", "benefit": "M6 过程证据完整，阶段耗时无需猜测", "rerun": "全程采样并校验 expected tenant×lane 矩阵、代际和计数回退"},
    ]

    artifacts = [
        {"file": "report.html", "purpose": "结论先行的可视化方案/结果入口；实时运行时持续更新", "required": "必需"},
        {"file": "summary.json", "purpose": "六项状态、分母、公式输出", "required": "必需"},
        {"file": "suite.json", "purpose": "场景、探针、运行顺序和原始引用", "required": "必需"},
        {"file": "records.csv", "purpose": "逐请求 Search/Commit 状态、耗时、质量和错误", "required": "必需"},
        {"file": "metrics_samples.csv", "purpose": "CPU、RSS、Prometheus/资源窗口", "required": "必需"},
        {"file": "structured-stage-events.jsonl", "purpose": "脱敏 trace 关联的 EchoMem 阶段事件", "required": "M1-M3 必需"},
        {"file": "execution-manifest.json", "purpose": "EchoMem/平台版本、模型、配置指纹、容器和资源", "required": "必需"},
        {"file": "fault/recovery/tenant-observability 原始 JSON", "purpose": "M4/M5/M6 的专属证据", "required": "full 必需"},
    ]

    delivery_checks = [
        "确认 report.html 是本次 OUTPUT/report.html，且更新时间与 summary.json/suite.json 对齐。",
        "确认 EchoMem 和测试平台 branch/commit、配置指纹、模型名、Endpoint、容器和 CPU/内存已记录。",
        "确认 C=1、C=8、C=16、C=64 的 target 与 actual peak 分开；未达到目标的请求列为未达档，不补成目标并发。",
        "确认 M2 使用 4/8 个不同凭据；M3 四种场景和 Commit overlap 时间线存在。",
        "确认错误、429/503、超时、质量失败、未发和 pending 都还在分母；没有通过筛选隐藏。",
        "确认阶段 P50/P95/P99 和 queue wait 来自真实日志/Prom 窗口；没有用端到端相减制造模块耗时。",
        "确认未执行项写出具体原因；不使用单独的 INCONCLUSIVE 作为解释。",
        "确认没有 API key、租户 key、密码或测试控制 token 出现在 profile、日志、HTML 或 Git。",
    ]

    return {
        "title": "EchoMem M1-M6 完整压测方案与执行进度",
        "status": "PLAN_READY_WITH_GAPS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_conclusion": (
            "当前方案只有一套完整测试流程，按 M1-A 到 M6 的执行单元拆开记录，并把并发档位明确定义为总在途 Search 请求。"
            "正式前三项应先用同一批已验证记忆完成 M1 的 C=1、C=8、C=16、C=64，再分别执行 M2 的 4/8 租户等权公平和 "
            "M3 的 baseline/均匀洪泛/单租户洪泛/异构负载；M4-M6 只在 full 模式执行。"
            f"当前 profile 的 concurrency 档位为 {levels}；每档保持独立窗口和真实分母，"
            "并把实际 peak 与目标并发分开呈现。"
        ),
        "scope": (
            "本页是完整测试方案和当前执行进度，不是新的性能结果。完整命令固定执行 M1-M6；每个执行单元单独保留证据和状态。"
            "Search 只使用 memory-recall query 和提前注入的真实自然语言记忆，不测试 no-recall；Soak 默认关闭。"
            "所有时长为计划窗口，Commit 的真实终态可能超过测量窗口，pending/排空继续保留。"
        ),
        "profile": profile_view,
        "execution_state": {"rows": state_rows},
        "execution_matrix": execution_matrix,
        "current_evidence": current,
        "concurrency_parameters": _parameter_audit(profile),
        "workflow": workflow,
        "schedule": schedule,
        "metrics": [_metric_m1(), _metric_m2(), _metric_m3(), _metric_m4(), _metric_m5(), _metric_m6()],
        "contracts": contracts,
        "commands": [commands[0], commands[3], commands[4]],
        "artifacts": artifacts,
        "delivery_checks": delivery_checks,
        "improvements": improvements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True, help="JSON profile file")
    parser.add_argument("--profile-name", default=None, help="profile name when JSON contains several")
    parser.add_argument("--evidence-dir", type=Path, default=None, help="optional prior run directory")
    parser.add_argument("--out-dir", type=Path, required=True, help="directory receiving plan.json and report.html")
    args = parser.parse_args()
    profile_path = args.profile.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    profile = _load_profile(profile_path, args.profile_name)
    plan = _plan(profile_path, profile, args.evidence_dir.expanduser().resolve() if args.evidence_dir else None)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_test_plan_report(plan, out_dir / "report.html")
    print(out_dir / "report.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
