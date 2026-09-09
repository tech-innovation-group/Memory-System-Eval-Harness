"""Six-objective evidence contract. Missing evidence never means PASS."""

from __future__ import annotations

import csv
import html
import json
import math
from collections import Counter
from pathlib import Path

from performance.stats import percentile


def configure_profile(profile: dict, *, live: bool = True) -> dict:
    from performance.targets.echomem.probes.fault_isolation import validate_fault_config
    from performance.targets.echomem.orchestrator.suites import six_metric_cases
    if str(profile.get("name", "")).upper() != "4U8G":
        raise ValueError("six-metrics currently requires a 4U8G profile")
    tenant_config = json.loads(Path(profile["tenant_config"]).read_text(encoding="utf-8"))
    ids = [t.get("tenant_id") or t.get("id") for t in tenant_config.get("tenants", [])]
    ids = [str(t) for t in ids if t][:4]
    if len(ids) != 4 or len(set(ids)) != 4:
        raise ValueError("six-metrics needs at least four distinct configured tenants")
    base = str(profile.get("base_url", "")).rstrip("/")
    fault = {"enabled": True, "endpoint": base + "/api/inspect/test-control/fault",
             "token_env": "ECHOMEM_TEST_CONTROL_TOKEN", "samples": 100, "repeats": 3,
             "phase_duration_s": 60, "duration_s": 180, "search_rps_per_tenant": 2, "target_rps": 1,
             **profile.get("fault_isolation", {})}
    validate_fault_config(fault)
    if live:
        phase = float(fault["phase_duration_s"])
        rate = float(fault["search_rps_per_tenant"])
        target_rate = float(fault["target_rps"])
        samples = max(100, int(fault["samples"]))
        if (not all(math.isfinite(v) for v in (phase, rate, target_rate))
                or phase < 60 or rate <= 0 or target_rate <= 0 or phase * rate < samples
                or phase >= float(fault["duration_s"]) or int(fault["repeats"]) < 3):
            raise ValueError("Formal fault phases require >=60s, >=100 bystander samples, positive rates, >=3 repeats, and a longer fault TTL")
    catalog = six_metric_cases(profile.get("capacity_levels"))
    # Credential values are never included in the normalized profile or reports.
    from performance.targets.echomem.probes._client import load_tenant_specs
    if live:
        specs = load_tenant_specs(profile["tenant_config"])
        required = max(c["tenants"] for c in catalog)
        if len(specs) < required or len({s.auth_key for s in specs[:required]}) != required:
            raise ValueError(f"six-metrics needs {required} independent tenant credentials for the configured capacity levels")
        if not profile.get("preflight_config"):
            raise ValueError("six-metrics requires preflight_config from the deployed EchoMem configuration")
        recovery = profile.get("commit_recovery") or {}
        if recovery.get("container", profile.get("resource_container")) != profile.get("resource_container"):
            raise ValueError("commit_recovery.container must match resource_container")
        if recovery.get("allow_container_restart") is not True:
            raise ValueError("Set commit_recovery.allow_container_restart=true only for a dedicated test container")
    observable = {"enabled": True, "expected_tenants": ids,
                  "expected_lanes": ["recall_engine", "recall_intent_llm", "recall_query_embedding", "commit"],
                  "token_env": "ECHOMEM_TEST_CONTROL_TOKEN", **profile.get("tenant_observability", {})}
    return {**profile, "six_metrics": True, "capacity_levels": [c["tenants"] for c in catalog if c["label"].startswith("capacity-")], "seed_sessions": 1,
            "seed_messages": 1, "allow_partial_tenants": False,
            "quick_include_seed": True, "metrics_enabled": True,
            "fairness_expectations": {"tenant_ids": ids}, "fault_isolation": fault,
            "tenant_observability": observable,
            "commit_recovery": {"tenant": ids[0], "container": profile.get("resource_container", ""),
                                "messages": 12, "content_chars": 1000,
                                **profile.get("commit_recovery", {}), "require_accepted_202": True}}


def probe_detail(payload: dict, name: str) -> dict:
    for check in payload.get("checks", []):
        if check.get("name") == name:
            try:
                detail = json.loads(check.get("detail") or "{}")
            except (TypeError, ValueError):
                detail = {}
            return {**detail, "status": check.get("status", "INCONCLUSIVE"),
                    "reason": check.get("reason", "")}
    return payload if "checks" not in payload else {}


def records(run: dict) -> list[dict]:
    path = Path(run.get("output_dir", "")) / "records.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def number(row: dict, field: str, default: float = 0) -> float:
    try:
        value = float(row.get(field, default))
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def truth(value) -> bool:
    return value is True or str(value).lower() == "true"


def search_stats(rows: list[dict]) -> dict:
    reads = [r for r in rows if r.get("op") == "read"]
    latencies = [number(r, "stage_ms") / 1000 for r in reads]
    valid = [r for r in reads if r.get("status") == "ok"
             and truth(r.get("quality_ok")) and not truth(r.get("degraded"))]
    return {"submitted": len(reads), "valid": len(valid),
            "http_errors": sum(r.get("status") != "ok" for r in reads),
            "degraded": sum(truth(r.get("degraded")) for r in reads),
            "quality_rate": len(valid) / len(reads) if reads else None,
            "mean_s": sum(latencies) / len(latencies) if latencies else None,
            "p50_s": percentile(latencies, 50),
            "p95_s": percentile(latencies, 95), "p99_s": percentile(latencies, 99)}


def jain(values: list[float]) -> float | None:
    denominator = len(values) * sum(v * v for v in values)
    return sum(values) ** 2 / denominator if denominator else None


def module_issues(data: dict[str, list[dict]], suite: dict) -> list[dict]:
    """Group observed symptoms without promoting attribution guesses to facts."""
    counts = Counter()
    for rows in data.values():
        for row in rows:
            if row.get("op") == "read":
                try:
                    reasons = json.loads(row.get("degraded_reasons") or "[]")
                except (TypeError, ValueError):
                    reasons = []
                unique_reasons = set(r for r in reasons if isinstance(r, str)) if isinstance(reasons, list) else set()
                for reason in unique_reasons:
                    module = "Recall 路由 / 引擎配置" if reason.startswith("engine_not_enabled:") else "Recall / 依赖（待定位）"
                    counts[(module, reason, "核对路由选择与实际启用引擎；保留降级，不计为质量通过")] += 1
                if row.get("status") != "ok":
                    code = str(row.get("http_status") or row.get("error_type") or "unknown")
                    module = "鉴权 / 测试身份" if code in {"401", "403"} else "HTTP / Admission（待定位）"
                    counts[(module, f"Search 请求错误: {code}", "结合状态码与服务证据区分鉴权、拒绝、超时；不剔除失败样本")] += 1
                elif not truth(row.get("quality_ok")) and not truth(row.get("degraded")):
                    counts[("Recall / 样本断言", "未通过内容质量断言", "核对该租户的预期标记、空召回或无召回样本误召回")] += 1
            elif row.get("op") == "commit_done" and row.get("status") != "ok":
                counts[("Commit / 后台处理", str(row.get("error_type") or "commit terminal error"),
                        "用原任务状态与持久化内容区分处理失败、超时及读取错误")] += 1
    issues = [{"module": module, "symptom": symptom, "count": count, "next_action": action}
              for (module, symptom, action), count in sorted(counts.items())]
    readiness = suite.get("readiness") or (suite.get("resource_preflight") or {}).get("readiness") or {}
    for check in readiness.get("checks", []):
        if check.get("status") != "PASS":
            issues.append({"module": check.get("owner"), "symptom": check.get("name"),
                           "count": 1, "next_action": check.get("next_action")})
    for check in (suite.get("commit_recovery") or {}).get("checks", []):
        if check.get("status") != "PASS":
            issues.append({"module": "Commit 恢复 / 对账", "symptom": check.get("reason", check.get("name")),
                           "count": 1, "next_action": "依据原任务自主恢复、集合、顺序和幂等证据分别判定，缺证据不推定服务缺陷"})
    snapshot = probe_detail(suite.get("tenant_observability", {}), "tenant-observability")
    if snapshot.get("missing") or snapshot.get("invalid"):
        issues.append({"module": "可观测 / 工作负载覆盖", "symptom": "预期租户或层的指标缺失/非法",
                       "count": len(snapshot.get("missing", [])) + len(snapshot.get("invalid", [])),
                       "next_action": "区分未触发的启用路径和接口未暴露字段；不能仅凭全局指标存在而通过"})
    return issues


def evaluate_six(suite: dict, profile: dict) -> dict:
    runs = {r.get("scenario"): r for r in suite.get("runs", [])}
    data = {name: records(run) for name, run in runs.items()}
    settings = profile.get("six_metric_gates", {})
    minimum = max(100, int(settings.get("min_search_samples", 100)))
    checks = []

    def add(code, name, status, owner, reason, observed):
        checks.append({"id": code, "name": name, "status": status,
                       "owner": owner, "reason": reason, "observed": observed})

    capacities = []
    for name, rows in data.items():
        if not name.startswith("capacity-"):
            continue
        stats = search_stats(rows)
        level = int(name.split("-")[1])
        active = {r.get("tenant_idx") for r in rows if r.get("op") == "read"}
        valid = (stats["submitted"] >= minimum and len(active) == level
                 and stats["quality_rate"] >= .99
                 and stats["p95_s"] <= float(settings.get("capacity_p95_s", 5)))
        capacities.append({"level": level, "observed_identities": len(active),
                           "generator_coverage": len(active) == level,
                           "meets_slo": valid, **stats})
    successes = [c["level"] for c in capacities if c["meets_slo"]]
    lower = max(successes, default=None)
    boundary = [c["level"] for c in capacities if lower and c["level"] > lower
                and c["generator_coverage"] and c["submitted"] >= minimum and not c["meets_slo"]]
    dau_model = profile.get("dau_model", {})
    requests_per_day = number(dau_model, "requests_per_user_per_day")
    peak_factor = number(dau_model, "peak_to_average_ratio")
    sustainable_rps = min(
        (c["submitted"] / float(runs[f'capacity-{c["level"]}'].get("duration_s") or 60)
         for c in capacities if c["level"] == lower), default=0)
    dau = sustainable_rps * 86400 / requests_per_day / peak_factor if requests_per_day > 0 and peak_factor >= 1 else None
    resource = suite.get("resource_evidence") or {}
    resource_verified = number(resource, "cpus") == 4 and number(resource, "memory_bytes") == 8 * 1024 ** 3
    capacity_complete = bool(lower and boundary and resource_verified)
    add("M1", "活跃用户 / 热用户容量", "PASS" if capacity_complete and dau is not None else "INCONCLUSIVE", "测试平台 / 负载模型",
        "每个压测身份持续请求，作为热用户代理；DAU 按声明的日均请求量和峰值系数估算，非实测日活。",
        {"capacity_levels": capacities, "active_lower_bound": lower,
         "failure_boundary": boundary, "estimated_dau": dau, "dau_model": dau_model,
         "hot_identity_lower_bound": lower, "boundary_confirmed": capacity_complete,
         "resource_verified": resource_verified,
         "resource_evidence": suite.get("resource_evidence")})

    fault = suite.get("fault_isolation", {})
    fault_cases = []
    for payload in fault.get("cases", []):
        detail = probe_detail(payload, "fault-isolation")
        controls = []
        for control_name in ("fault-control-enable", "fault-control-disable"):
            control = probe_detail(payload, control_name)
            if control:
                controls.append({"name": control_name, "status": control.get("status"),
                                 "http_status": control.get("status_code"),
                                 "returncode": control.get("returncode"),
                                 "invalid_duration_contract": control.get("invalid_duration_contract")})
        fault_cases.append({**detail, "target_tenant": payload.get("target_tenant") or detail.get("target_tenant"),
                            "fault_type": payload.get("fault_type"), "repetition": payload.get("repetition"),
                            "control_checks": controls})
    expected_tenants = (profile.get("fairness_expectations") or {}).get("tenant_ids", [])
    repeats = int((profile.get("fault_isolation") or {}).get("repeats", 3))
    expected_matrix = {(t, kind, repeat) for t in expected_tenants for kind in ("reject", "delay")
                       for repeat in range(1, repeats + 1)}
    observed_matrix = {(f.get("target_tenant"), f.get("fault_type"), f.get("repetition")) for f in fault_cases}
    expected_fault_cases = len(expected_matrix) if expected_matrix else 24
    fault_complete = (len(set(expected_tenants)) == 4 and repeats > 0
                      and observed_matrix == expected_matrix
                      and len(fault_cases) == expected_fault_cases and all(
        f.get("fault_observed") and f.get("samples_per_tenant", 0) >= minimum
        and f.get("before") and f.get("during") and f.get("after") for f in fault_cases))
    fault_complete = fault_complete and all(f.get("baseline_healthy") for f in fault_cases)
    fault_status = "INCONCLUSIVE" if not fault_complete else "PASS" if all(
        f.get("status") == "PASS" and f.get("fault_recovered") for f in fault_cases) else "FAIL"
    add("M2", "单租户故障隔离", fault_status,
        "故障控制 / Recall", "轮流对每个租户注入拒绝/延迟，多轮保留全部错误和前中后采样。",
        {"expected_cases": expected_fault_cases, "completed_cases": len(fault_cases),
         "control_enable_failed_cases": sum(any(c["name"] == "fault-control-enable" and c["status"] != "PASS"
                                                  for c in f["control_checks"]) for f in fault_cases),
         "missing_cases": [list(key) for key in sorted(expected_matrix - observed_matrix)],
         "unique_observed_cases": len(observed_matrix), "cases": fault_cases})

    fair = data.get("fairness-bounded", [])
    if fair:
        window_start = min(number(r, "ts_ms") - number(r, "stage_ms") for r in fair)
        window_end = window_start + float(runs["fairness-bounded"].get("duration_s") or 120) * 1000
        fair = [r for r in fair if number(r, "ts_ms") <= window_end]
    tenants = range(4)
    tenant_rows = []
    fair_window_s = float(runs.get("fairness-bounded", {}).get("duration_s") or 120)
    for t in tenants:
        rows = [r for r in fair if str(r.get("tenant_idx")) == str(t)]
        submits = [r for r in rows if r.get("op") == "commit_submit"]
        done = [r for r in rows if r.get("op") == "commit_done" and r.get("status") == "ok"]
        tenant_rows.append({"tenant_index": t,
                            "tenant_id": expected_tenants[t] if t < len(expected_tenants) else str(t),
                            "commit_submitted": len(submits),
                            "commit_completed": len(done), "window_s": fair_window_s,
                            "commit_completed_per_s": len(done) / fair_window_s, **search_stats(rows)})
    comparable = all(t["submitted"] >= minimum and t["commit_submitted"] > 0
                     and t["p95_s"] and t["quality_rate"] >= .99 for t in tenant_rows)
    comparable = comparable and len({t["commit_submitted"] for t in tenant_rows}) == 1
    cj = jain([t["commit_completed"] for t in tenant_rows])
    sj = jain([1 / t["p95_s"] if t["p95_s"] else 0 for t in tenant_rows])
    verdict = "INCONCLUSIVE" if not comparable else "PASS" if cj is not None and sj is not None and min(cj, sj) >= .9 else "FAIL"
    add("M3", "同档位租户公平性", verdict, "调度 / 测试负载",
        "只使用同一等权负载窗口；零完成租户保留在分母。",
        {"tenants": tenant_rows, "commit_jain": cj, "search_inverse_p95_jain": sj,
         "equal_window_s": fair_window_s, "expected_tenants": 4})

    flood = data.get("search-priority-blackbox", [])
    baseline = search_stats(data.get("recall-baseline", []))
    stats = search_stats(flood)
    def commit_key(row):
        return row.get("tenant_idx"), row.get("session_id"), row.get("archive_id")

    accepted_by_key = {}
    for row in flood:
        if (row.get("op") == "commit_submit" and number(row, "http_status") == 202
                and row.get("session_id") and row.get("archive_id")):
            accepted_by_key.setdefault(commit_key(row), row)
    accepted = list(accepted_by_key.values())
    done = {commit_key(r): r for r in flood if r.get("op") == "commit_done"}
    overlap = []
    for r in flood:
        if r.get("op") != "read":
            continue
        at = number(r, "ts_ms") - number(r, "stage_ms")
        if any(number(c, "ts_ms") <= at <= number(done.get(commit_key(c), {}), "ts_ms", float("inf"))
               for c in accepted):
            overlap.append(r)
    overlap_stats = search_stats(overlap)
    priority_tenants = []
    for tenant in range(4):
        before = search_stats([r for r in data.get("recall-baseline", []) if str(r.get("tenant_idx")) == str(tenant)])
        during = search_stats([r for r in overlap if str(r.get("tenant_idx")) == str(tenant)])
        tenant_ratio = during["p95_s"] / before["p95_s"] if before["p95_s"] and during["p95_s"] else None
        priority_tenants.append({"tenant_index": tenant, "baseline": before, "overlap": during,
                                 "p95_ratio": tenant_ratio})
    enough = (baseline["submitted"] >= minimum and len(accepted) >= 32
              and overlap_stats["submitted"] >= minimum
              and all(t["baseline"]["submitted"] >= minimum and t["overlap"]["submitted"] >= minimum
                      for t in priority_tenants))
    quality = enough and baseline["quality_rate"] >= .99 and overlap_stats["quality_rate"] >= .99
    ratio = overlap_stats["p95_s"] / baseline["p95_s"] if baseline["p95_s"] and overlap_stats["p95_s"] else None
    tenant_slo = all(t["baseline"]["quality_rate"] is not None and t["baseline"]["quality_rate"] >= .99
                     and t["overlap"]["quality_rate"] is not None and t["overlap"]["quality_rate"] >= .99
                     and t["overlap"]["p95_s"] is not None and t["overlap"]["p95_s"] <= 5
                     and t["p95_ratio"] is not None and t["p95_ratio"] <= 1.2 for t in priority_tenants)
    verdict = "INCONCLUSIVE" if not enough else "PASS" if quality and tenant_slo and overlap_stats["p95_s"] <= 5 and ratio <= 1.2 else "FAIL"
    add("M4", "Commit 洪泛下 Search 服务保障", verdict, "Admission / Recall / Commit",
        "测量并发积压窗口的 Search SLO；黑盒延迟本身不能证明内部严格调度顺序。",
        {"baseline": baseline, "flood": stats, "overlap": overlap_stats,
         "tenants": priority_tenants, "accepted_202": len(accepted), "p95_ratio": ratio,
         "strict_internal_order_proven": False})

    recovery = suite.get("commit_recovery", {})
    required = ("commit-recovery", "pending-before-kill", "message-reconciliation",
                "cursor-reconciliation", "order-reconciliation", "idempotency-replay")
    recovery_checks = {c.get("name"): c.get("status") for c in recovery.get("checks", [])}
    recovery_detail = probe_detail(recovery, "commit-recovery")
    recovery_complete = all(recovery_checks.get(n) == "PASS" for n in required) and recovery_detail.get("autonomous_recovery_observed") is True
    verdict = "PASS" if recovery_complete else "FAIL" if "FAIL" in recovery_checks.values() else "INCONCLUSIVE"
    add("M5", "202 Commit 崩溃恢复", verdict, "Commit 持久化 / 恢复",
        "保留受理、崩溃、恢复、消息集合和顺序证据；样本通过率不外推所有请求。",
        {**recovery, "required_checks": list(required),
         "passed_checks": sum(recovery_checks.get(n) == "PASS" for n in required),
         "check_denominator": len(required),
         "autonomous_recovery_observed": recovery_detail.get("autonomous_recovery_observed"),
         "accepted_samples": int(recovery_detail.get("accepted_202") is True)})

    snapshot = probe_detail(suite.get("tenant_observability", {}), "tenant-observability")
    before = suite.get("tenant_observability_before", {})
    initial = {(r.get("tenant_id"), r.get("lane")): r for r in before.get("rows", [])}
    activity = []
    for row in snapshot.get("rows", []):
        key = (row.get("tenant_id"), row.get("lane"))
        prior = initial.get(key, {})
        delta = {field: number(row, field) - number(prior, field)
                 for field in ("accepted_total", "completed_total", "failed_total", "rejected_total", "wait_seconds_total", "exec_seconds_total")}
        counters_valid = all(not isinstance(r.get(field), bool) and number(r, field, -1) >= 0
                             for r in ([row, prior] if prior else [row]) for field in delta)
        activity.append({"tenant_id": key[0], "lane": key[1], **delta,
                         "counters_valid": counters_valid,
                         "activity_proven": counters_valid and delta["accepted_total"] > 0 and all(v >= 0 for v in delta.values())})
    observable = (snapshot.get("status") == "PASS" and before.get("http_status") == 200
                  and activity and all(row["activity_proven"] for row in activity))
    add("M6", "每层每租户四元组", "PASS" if observable else "INCONCLUSIVE", "可观测性 / 测试控制面",
        "校验预期 tenant × lane 及本次负载前后计数增量，历史累积值不能替代实际执行证据。",
        {"snapshot": snapshot, "activity": activity, "baseline_http_status": before.get("http_status")})
    return {"profile": profile.get("name"), "checks": checks,
            "module_issues": module_issues(data, suite),
            "suite_path": str(Path(suite.get("output_root", ".")) / "suite.json"),
            "preflight": suite.get("preflight"), "seed": suite.get("seed"),
            "resource_preflight": suite.get("resource_preflight"),
            "query_mix": search_stats(data.get("query-mixed", [])),
            "query_classes": {kind: search_stats([r for r in data.get("query-mixed", [])
                                                    if r.get("query_type") == kind])
                              for kind in ("recall", "no_recall", "unclassified")},
            "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL" if any(c["status"] == "FAIL" for c in checks) else "INCONCLUSIVE"}


def write_report(result: dict, path: Path) -> None:
    esc = html.escape
    sections = []

    def table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
        if not rows:
            return '<p class="INCONCLUSIVE">暂无有效样本</p>'
        head = ''.join(f'<th>{esc(label)}</th>' for _, label in columns)
        body = ''.join('<tr>' + ''.join(
            f'<td>{esc(str(row.get(key))) if row.get(key) is not None else "—"}</td>'
            for key, _ in columns) + '</tr>' for row in rows)
        return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    setup = {key: result.get(key) for key in ("resource_preflight", "preflight", "seed") if result.get(key)}
    setup_html = '<section><h2>准备阶段与环境证据</h2><pre>' + esc(json.dumps(setup, ensure_ascii=False, indent=2)) + '</pre></section>'
    mixed_html = '<section><h2>混合查询分组数据</h2>' + table(
        [{"kind": kind, **values} for kind, values in result.get("query_classes", {}).items()],
        [("kind", "查询类型"), ("submitted", "全部样本"), ("valid", "质量通过"),
         ("http_errors", "请求错误"), ("degraded", "降级"), ("mean_s", "平均秒"),
         ("p50_s", "P50 秒"), ("p95_s", "P95 秒"), ("p99_s", "P99 秒")]) + '</section>'
    issue_html = '<section><h2>模块问题与下一步</h2><p>这是症状归类，不是未经验证的根因结论；同一请求可能包含多个降级原因。</p>' + table(
        result.get("module_issues", []), [("module", "模块"), ("symptom", "观测到的问题"),
                                         ("count", "次数/缺口"), ("next_action", "排查或修改方向")]) + '</section>'
    diagnostic_html = ''
    if result.get("diagnostics"):
        diagnostic_html = '<section><h2>真实 Search 诊断样本（不替代正式压测）</h2>' + table(
            result["diagnostics"], [("query", "查询"), ("elapsed_s", "耗时秒"),
                                    ("item_count", "召回条数"), ("marker_found", "标记命中"),
                                    ("status", "服务状态"), ("reasons", "降级原因")]) + '</section>'

    for check in result["checks"]:
        observed = esc(json.dumps(check["observed"], ensure_ascii=False, indent=2))
        values = check["observed"]
        visual = ""
        if check["id"] == "M1":
            visual = table(values.get("capacity_levels", []), [
                ("level", "配置身份数"), ("observed_identities", "实际身份数"),
                ("submitted", "Search 分母"), ("quality_rate", "质量成功率"),
                ("p95_s", "P95 秒"), ("meets_slo", "有效档位")])
        elif check["id"] == "M2":
            rows = []
            for index, case in enumerate(values.get("cases", [])):
                for tenant, degradation in case.get("degradation_by_tenant", {}).items():
                    rows.append({"case": index + 1, "target": case.get("target_tenant"),
                                 "tenant": tenant, "degradation_percent": round(degradation * 100, 2),
                                 "status": case.get("status")})
            visual = table(rows, [("case", "轮次"), ("target", "故障租户"), ("tenant", "旁观租户"),
                                  ("degradation_percent", "P95 劣化 %"), ("status", "判定")])
        elif check["id"] == "M3":
            visual = table(values.get("tenants", []), [("tenant_index", "租户"), ("commit_submitted", "Commit 提交"),
                ("commit_completed", "Commit 完成"), ("commit_completed_per_s", "Commit 完成/秒"),
                ("window_s", "等权窗口秒"), ("submitted", "Search 样本"), ("p95_s", "P95 秒")])
            for field, label in (("commit_jain", "Commit Jain"), ("search_inverse_p95_jain", "Search Jain")):
                if values.get(field) is not None:
                    score = values[field]
                    visual += f'<p>{label} <meter min="0" max="1" low="0.9" high="1" optimum="1" value="{score}"></meter> {score:.4f}</p>'
        elif check["id"] == "M4":
            visual = table([{"phase": phase, **values.get(key, {})} for key, phase in
                            (("baseline", "无 Commit 基线"), ("flood", "洪泛全窗口"), ("overlap", "实际积压重叠窗口"))],
                           [("phase", "阶段"), ("submitted", "样本数"), ("valid", "有效召回"),
                            ("mean_s", "平均秒"), ("p95_s", "P95 秒")])
            visual += table([{"tenant_index": t["tenant_index"], "baseline_samples": t["baseline"]["submitted"],
                              "baseline_p95": t["baseline"]["p95_s"], "overlap_samples": t["overlap"]["submitted"],
                              "overlap_p95": t["overlap"]["p95_s"], "ratio": t["p95_ratio"]}
                             for t in values.get("tenants", [])],
                            [("tenant_index", "租户"), ("baseline_samples", "基线样本"),
                             ("baseline_p95", "基线 P95 秒"), ("overlap_samples", "重叠样本"),
                             ("overlap_p95", "重叠 P95 秒"), ("ratio", "劣化倍数")])
        elif check["id"] == "M5":
            visual = table(values.get("checks", []), [("name", "检查项"), ("status", "结果"), ("reason", "依据")])
        elif check["id"] == "M6":
            visual = table(values.get("activity", []), [("tenant_id", "租户"), ("lane", "层"),
                ("accepted_total", "本次受理"), ("wait_seconds_total", "本次等待秒"),
                ("exec_seconds_total", "本次执行秒"), ("rejected_total", "本次拒绝"), ("activity_proven", "本次执行证据")])
        sections.append(f'<section id="{check["id"]}"><h2>{check["id"]} · {esc(check["name"])}</h2>'
                        f'<p class="{check["status"]}">{check["status"]}</p><p>{esc(check["reason"])}</p>'
                        f'<p>责任模块：{esc(check["owner"])}</p>{visual}<details><summary>原始指标与完整分母</summary><pre>{observed}</pre></details></section>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>EchoMem 六项指标验收</title><style>body{font:16px/1.65 system-ui;margin:0;color:#20262d;background:#fff}main{max-width:1100px;margin:auto;padding:28px}section{border-top:1px solid #d7dde0;padding:20px 0}h1{font-size:28px}h2{font-size:21px}.PASS{color:#167449}.FAIL{color:#b42318}.INCONCLUSIVE{color:#875d00}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f6;padding:16px}summary{cursor:pointer}a{color:#146ba0}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;margin:16px 0;font-size:14px}th,td{text-align:left;border-bottom:1px solid #dee3e8;padding:9px;overflow-wrap:anywhere}th{background:#f2f5f7}meter{width:180px;max-width:50%}@media(max-width:600px){main{padding:16px}h1{font-size:24px}}</style><main>'
                    f'<h1>EchoMem · {esc(str(result.get("profile")))} 六项指标</h1><p>整体状态：{esc(result["status"])}</p>'
                    f'<p>{esc(str(result.get("run_phase", "")))}</p>'
                    '<p>仅依据本次真实请求与服务端证据判定；缺失值显示为 null，不计为成功。</p>'
                    + issue_html + diagnostic_html + setup_html + ''.join(sections) + mixed_html + '</main></html>', encoding="utf-8")
