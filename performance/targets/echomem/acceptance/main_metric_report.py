"""Publish an allowlisted, shareable first report from private main-metric samples."""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path

from performance.targets.echomem.acceptance.capacity_observation_report import render_observation
from performance.targets.echomem.acceptance.route_path_report import render_route_paths
from performance.targets.echomem.acceptance.provenance import render_platform_provenance
from performance.targets.echomem.acceptance.reliability_evidence import observability_counts, recovery_counts
from performance.targets.echomem.acceptance.load_evidence import count, number, fairness_counts, load_counts, load_conclusions, priority_counts
from performance.targets.echomem.acceptance.fault_evidence import matrix_counts, public_control_evidence


def _ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator else None


def _number(value, default=0):
    return value if isinstance(value, (int, float)) else default


def _observed(value):
    if value is None:
        return "未采集"
    if isinstance(value, bool):
        return "是" if value else "否"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _counts(value):
    if not value:
        return "无"
    return ", ".join(f"{key}: {count}" for key, count in sorted(value.items()))


def fault_matrix_counts(value: dict) -> dict:
    if not value.get("cases"):
        return value
    fields = ("repeat", "fault_type", "target_index", "status", "control_enabled",
              "control_disabled", "target_effect_observed", "target_p95_delta_s",
              "bystander_http_errors", "worst_bystander_p95_change_percent", "pairs", "recovery_pairs", "control_evidence")
    return {"status": value.get("status"), "expected_cases": value.get("expected_cases"),
            "measured_cases": value.get("measured_cases"), "repeats": value.get("repeats"),
            "phase_duration_s": value.get("phase_duration_s"),
            "recovery_duration_s": value.get("recovery_duration_s"),
            "delay_ms": value.get("delay_ms"), "verification": matrix_counts(value),
            "protocol_version": value.get("protocol_version"), "baseline_scope": value.get("baseline_scope"),
            "read_worker_isolation": value.get("read_worker_isolation"),
            "bystander_http_errors": value.get("bystander_http_errors"),
            "worst_bystander_p95_change_percent": value.get("worst_bystander_p95_change_percent"),
            "cases": [{**{key: case.get(key) for key in fields},
                       "control_evidence": public_control_evidence(case.get("control_evidence"))}
                      for case in value["cases"]]}


def contention_matrix_counts(value: dict) -> tuple[dict, dict]:
    samples = value.get("samples", [])
    if not samples:
        return {}, {}
    joints = [sample.get("M3_M4", {}) for sample in samples]
    ranked = [(fairness_counts(joint)["commit_jain"], joint) for joint in joints]
    ranked = [(score, joint) for score, joint in ranked if score is not None]
    representative = dict(min(ranked, key=lambda row: row[0])[1] if ranked else joints[-1])

    def total(field):
        values = [count(joint.get(field)) for joint in joints]
        return sum(values) if all(v is not None for v in values) else None
    representative.update(
        repeat_count=len(samples),
        expected_repeats=value.get("expected_samples", len(samples)),
        repeat_summaries=[{"repeat": sample.get("repeat"),
                           "expected_identity_indices": joint.get("expected_identity_indices", [0, 1, 2, 3]),
                           "minimum_flood_commits": joint.get("minimum_flood_commits", 32),
                           "search_window_s": joint.get("search_window_s"),
                           "tenants": joint.get("tenants", []),
                           "commit_jain": joint.get("commit_jain"),
                           "search_inverse_p95_jain": joint.get("search_inverse_p95_jain"),
                           "commit_planned": joint.get("commit_planned"),
                           "accepted_202": joint.get("accepted_202"),
                           "commit_outcomes": joint.get("commit_outcomes", {}),
                           "submission_schedule": joint.get("submission_schedule"),
                           "completed_including_drain": joint.get("completed_including_drain"),
                           "unresolved_after_observation": joint.get("unresolved_after_observation"),
                           "overlap_search": joint.get("overlap_search") or {},
                           "overlap_protocol": joint.get("overlap_protocol"),
                           "confirmed_overlap_search": joint.get("confirmed_overlap_search"),
                           "overlap_evidence": joint.get("overlap_evidence"),
                           "paired": joint.get("paired", [])}
                          for sample, joint in zip(samples, joints)],
        commit_jain_values=[joint.get("commit_jain") for joint in joints],
        search_inverse_p95_jain_values=[joint.get("search_inverse_p95_jain") for joint in joints],
        commit_planned=total("commit_planned"),
        accepted_202=total("accepted_202"),
        completed_including_drain=total("completed_including_drain"),
        unresolved_after_observation=total("unresolved_after_observation"),
    )
    observations = [sample.get("M6", {}) for sample in samples]
    latest = dict(observations[-1])
    latest["repeat_observations"] = observations
    latest["expected_repeats"] = value.get("expected_samples", len(samples))
    return representative, latest


def derive_conclusions(report: dict) -> dict[str, dict]:
    """Turn measurements into explicit, bounded conclusions without inventing SLOs."""
    m1 = report.get("M1", {})
    levels = m1.get("levels", [])
    boundary = m1.get("boundary") or {}
    confirmed_capacity = m1.get("max_hot_users") if boundary.get("status") == "CONFIRMED" else None
    zero_error_level = (m1.get("zero_error_max_hot_users")
                        if boundary.get("status") == "ZERO_ERROR_CONFIRMED" else None)
    first_failed_capacity = boundary.get("first_fail")
    first_nonzero_error = boundary.get("first_nonzero_error")
    highest = max((row.get("hot_users", 0) for row in levels), default=None)
    highest_rows = [row for row in levels if row.get("hot_users") == highest]
    highest_pure = next((row for row in highest_rows if not row.get("mixed")), {})
    highest_mixed = next((row for row in highest_rows if row.get("mixed")), {})
    pure_search = highest_pure.get("search", {})
    mixed_search = highest_mixed.get("search", {})
    recovery_states = [row.get("recovery", {}).get("status") for row in highest_rows]
    observed_boundary = m1.get("operational_boundary") or {}
    boundary_detail = observed_boundary.get("evidence") or {}
    boundary_observed = boundary_detail.get("status") == "BOUNDARY_OBSERVED"
    no_boundary = (bool(recovery_states) and
                   all(state in {"NO_BOUNDARY_OBSERVED", "RECOVERED"} for state in recovery_states))

    m2 = report.get("M2", {})
    matrix_cases = m2.get("cases", [])
    bystanders = ([row for case in matrix_cases for row in case.get("pairs", [])
                   if row.get("identity_index") != case.get("target_index")]
                  if matrix_cases else
                  [row for row in m2.get("pairs", []) if row.get("identity_index") != m2.get("target_index")])
    bystander_changes = [row.get("p95_degradation_percent") for row in bystanders
                         if row.get("p95_degradation_percent") is not None]
    bystander_quality = [row.get("during", {}).get("quality_rate") for row in bystanders]
    known_quality = [value for value in bystander_quality if value is not None]
    fault_evidence = matrix_counts(m2)
    matrix_complete = fault_evidence["status"] == "MEASURED"

    joint = report.get("M3_M4", {})
    load_conclusion = load_conclusions(load_counts(joint))

    m5 = report.get("M5", {})
    recovery_pass = bool(m5.get("status") == "PASS" and m5.get("accepted_202") and m5.get("autonomous_completed")
                         and m5.get("missing_messages") == 0 and m5.get("same_archive")
                         and m5.get("checks") and all(row.get("status") == "PASS" for row in m5["checks"]))
    m6 = report.get("M6", {})
    observed_cells = len(m6.get("rows", []))
    expected_cells = m6.get("expected_cells", 0)
    commit_rows = [row for row in m6.get("rows", []) if row.get("lane") == "commit"]
    m5_passed = m5.get("passed_samples", int(recovery_pass))
    m5_total = m5.get("expected_samples", m5.get("sample_count", 1 if m5.get("checks") else 0))
    recovery_level = (f"{m5_passed}/{m5_total} 恢复样本通过" if recovery_pass else
                      "恢复检查出现失败" if m5.get("status") == "FAIL" else "恢复证据不足")
    recovery_conclusion = ("已完成的样本通过202受理、崩溃前pending、原任务自主恢复、消息集合、顺序、cursor和幂等检查。"
                          if recovery_pass else
                          "至少一项恢复检查失败，具体项目与样本见下表；不能据此宣称恢复可靠。"
                          if m5.get("status") == "FAIL" else
                          "恢复样本或关键检查不完整；尚不能确认已返回202的任务在崩溃后完整重放。")
    observability_pass = bool(m6.get("snapshot_status", m6.get("status")) == "PASS" and expected_cells
                             and m6.get("valid_cells") == expected_cells)
    timelines = [sample.get("timeline", {}) for sample in m6.get("repeat_observations") or [m6]]
    timeline_pass = m6.get("status") == "PASS" and observability_pass and all(t.get("status") == "PASS" for t in timelines)
    timeline_failed = any(t.get("status") == "FAIL" for t in timelines)
    timeline_incomplete = any(t.get("status") == "INCONCLUSIVE" and t.get("snapshot_count") for t in timelines)

    if boundary_observed:
        m1_level = "已观察到运行边界"
        m1_conclusion = (
            f"在 H={_observed(observed_boundary.get('hot_users'))} 观察到 {_observed(boundary_detail.get('reason'))}；"
            f"恢复观察窗口 {_observed(boundary_detail.get('recovery_window_s'))} 秒。"
            "这是本次条件下的运行故障证据，需结合相邻档与重复测试确定可承载范围，不能外推永久无法恢复。"
        )
    elif zero_error_level is not None:
        m1_level = "零错误档已确认，硬容量未确定"
        m1_conclusion = (
            f"连续三轮全部请求完成的最高实测档是 {zero_error_level} 个热用户；"
            f"{first_nonzero_error} 用户档出现过非零请求错误。这个数字只描述严格零错误合同，"
            f"不是最大承载用户数。更高档已测到 {_observed(highest)} 用户，"
            "尚未提供已核验的运行故障边界，因此硬容量上限仍未测得。"
        )
    elif confirmed_capacity is not None:
        m1_level = "历史请求完成合同边界，硬容量未确定"
        m1_conclusion = (
            f"历史请求完成合同记录的热用户档为 {confirmed_capacity}；"
            f"紧邻的 {first_failed_capacity} 用户档至少出现一项 Search 未完成、HTTP/传输失败或 Commit 未完成。"
            f"更高档已继续测到 {_observed(highest)} 用户；首个请求错误不能确定崩溃/OOM/不可恢复积压意义上的硬容量。"
        )
    else:
        m1_level = "边界未确定"
        m1_conclusion = ((f"最高记录档为 {_observed(highest)} 个热用户。" if highest is not None else
                          "尚未采集到有效热用户档位。") +
                         "未提供已核验的崩溃、OOM或停止发压后不可恢复积压证据，不能把最高已测档写成绝对容量上限。")

    return {
        "M1": {
            "level": m1_level,
            "conclusion": m1_conclusion,
            "evidence": (f"{_observed(highest)}用户纯召回：P95 {_observed(pure_search.get('p95_s'))}s，"
                         f"HTTP/传输错误 {_observed(pure_search.get('transport_or_http_errors'))}/{_observed(pure_search.get('sent'))}，"
                         f"严格有效 {_observed(pure_search.get('success'))}/{_observed(pure_search.get('sent'))}；"
                         f"混合流量：P95 {_observed(mixed_search.get('p95_s'))}s，"
                         f"严格有效 {_observed(mixed_search.get('success'))}/{_observed(mixed_search.get('sent'))}。"
                         f"最高档恢复观测未见运行边界={_observed(True if no_boundary else None)}。"),
            "next": ("复测运行故障档与相邻档，核验生命周期和积压恢复，再结合明确的DAU画像换算。"
                     if boundary_observed else
                     "继续提高负载直到真实运行边界；错误、质量与硬容量分别报告，DAU只能按明确画像条件换算。"),
        },
        "M2": {
            "status": fault_evidence["status"],
            "level": (f"已记录 {fault_evidence['recorded_cases']} 个用例；完整核验 {fault_evidence['measured_cases']}/{_observed(fault_evidence['expected_cases'])}"
                      if matrix_cases else "单次初步支持，正式结论证据不足"),
            "conclusion": ((f"四个测试租户均完成reject与delay故障验证，共 {m2.get('repeats', 1)} 轮；旁观租户的HTTP错误和最差P95变化见下表。"
                            "结论严格限定在这些实测租户、故障类型和轮次。") if matrix_complete else
                           "当前故障矩阵尚未全部形成有效目标故障证据，不能宣称任意租户故障都已隔离。" if matrix_cases else
                           "本次只对一个租户注入一次故障；旁观租户结果可作初步观察，不能宣称任意租户故障都隔离成功。"),
            "evidence": (f"已记录用例 {fault_evidence['recorded_cases']}，证据完整 {fault_evidence['measured_cases']}/{_observed(fault_evidence['expected_cases'])}；"
                         f"已知旁观HTTP错误 {fault_evidence['known_bystander_http_errors']}，全矩阵HTTP错误 {_observed(fault_evidence['bystander_http_errors'])}；"
                         f"旁观 P95 变化范围 {_observed(min(bystander_changes, default=None))}% 至 {_observed(max(bystander_changes, default=None))}%；"
                         f"已观测质量范围 {_observed(min(known_quality, default=None))} 至 {_observed(max(known_quality, default=None))}；"
                         "完整逐租户数值见下表；证据不足不等于未执行，也不等于服务故障。"),
            "next": ("保持原始before/during/after请求证据，并按模型/存储故障类型继续扩展。" if matrix_complete else
                     "补足目标原因码、匹配的控制回执和故障窗口覆盖；每目标单独测基线与恢复。未生效和失败用例仍保留在分母。"),
        },
        "M3": load_conclusion["M3"],
        "M4": load_conclusion["M4"],
        "M5": {
            "level": recovery_level,
            "conclusion": recovery_conclusion + "结论仅覆盖实际样本，不外推为所有崩溃时机的100%保证。",
            "evidence": (f"通过样本 {m5_passed}/{m5_total}；源消息 {_observed(m5.get('expected_messages'))}，缺失消息 {_observed(m5.get('missing_messages'))}；"
                         f"同archive={_observed(m5.get('same_archive'))}，耗时(s)={_observed(m5.get('elapsed_s'))}；全部检查通过={_observed(recovery_pass)}。"),
            "next": "覆盖不同崩溃时机、并发Commit、重复崩溃和更大积压，再计算真实恢复成功率。",
        },
        "M6": {
            "level": ("过程采样核验通过" if timeline_pass else "过程观测出现异常" if timeline_failed else
                      "过程采样证据不完整" if timeline_incomplete else
                      "末次快照覆盖完整，过程待核验" if observability_pass else "四元组覆盖证据不足"),
            "conclusion": (("负载前、中、后的已采样快照字段完整，采样间隔满足配置且累计计数未见回退。"
                            if timeline_pass else
                            f"本轮 {m6.get('tenant_count')} 租户×{len(m6.get('expected_lanes', []))} 模块的末次快照字段合法且唯一。"
                            if observability_pass else
                            "预期单元、字段合法性或逐轮验证尚未全部通过；已有行数不能直接当作有效覆盖数。")
                           + "过程采样次数不等于每次都覆盖完整；覆盖范围以声明的模块为准，不能直接推断EchoMem内部每一层均已覆盖。"),
            "evidence": (f"有效单元 {m6.get('valid_cells')}/{expected_cells}，原始行 {observed_cells}，缺失 {m6.get('missing_cells')}，"
                         f"非法 {m6.get('invalid_cells')}，重复 {m6.get('duplicate_cells')}，负载中快照 {m6.get('sample_count')} 次；"
                         f"服务声明lane数={m6.get('declared_lane_count')}，数量对账={m6.get('lane_count_matches_observed')}；"
                         f"过程核验通过轮次 {sum(t.get('status') == 'PASS' for t in timelines)}/{len(timelines)}；"
                         f"Commit队列峰值 {[row.get('queued_peak_during_load') for row in commit_rows]}。"),
            "next": "从生效配置枚举所有启用层形成分母，并为HTTP/admission/fanout等缺失层补只读观测或明确排除依据。",
        },
    }


def derive_module_recommendations(report: dict) -> list[dict]:
    """Map verified observations to investigations, not unproven code defects."""
    conclusions = derive_conclusions(report)
    load = load_counts(report.get("M3_M4", {}))
    fault = matrix_counts(report.get("M2", {}))
    levels = report.get("M1", {}).get("levels", [])
    highest = max((number(row.get("hot_users")) for row in levels
                   if number(row.get("hot_users")) is not None), default=None)
    highest_errors = [count(row.get("search", {}).get("transport_or_http_errors"))
                      for row in levels if row.get("hot_users") == highest]
    errors_complete = bool(highest_errors and all(v is not None for v in highest_errors))
    joints = report.get("M3_M4", {}).get("repeat_summaries") or [report.get("M3_M4", {})]
    lane_rejections = [count((joint.get("commit_outcomes") or {}).get(
        "rejection_reason_counts", {}).get("HTTP_LANE_SATURATED")) for joint in joints]
    known_lane_rejections = sum(value for value in lane_rejections if value is not None)
    atomic = [number(row.get("search", {}).get("atomic_p95_s"))
              for joint in joints for row in joint.get("tenants", [])]
    atomic = [v for v in atomic if v is not None]
    timing_evidence = (
        f"已记录的租户窗口 Atomic P95 范围 {_observed(min(atomic, default=None))}"
        f" 至 {_observed(max(atomic, default=None))} 秒；窗口数 {len(atomic)}。"
        "这是各窗口的分位数范围，不是合并请求的P95，也不是模型独立耗时。"
    )
    m2_judgment = (
        "故障矩阵证据不完整，尚不能确认错误或延迟隔离。"
        if fault["status"] != "MEASURED" else
        "本轮有效故障矩阵的旁观租户未见HTTP/传输错误；P95变化按用例报告，不能外推为任意故障均隔离。"
        if fault["bystander_http_errors"] == 0 else
        "本轮故障矩阵中旁观租户出现HTTP/传输错误；需用配对基线、原因码和重试复测区分服务与外部依赖。"
    )
    return [
        {"priority": "P0", "module": "Admission 与容量保护", "metrics": "M1 / M4",
         "evidence": (f"最高记录档 H={_observed(highest)}；该档最多HTTP/传输错误 "
                      f"{_observed(max(highest_errors) if errors_complete else None)}。"
                      + (f"已记录HTTP_LANE_SATURATED拒绝 {known_lane_rejections} 次。" if known_lane_rejections else "")
                      + conclusions["M4"]["evidence"]),
         "judgment": ("回执确认部分Commit在HTTP入口通道饱和时被拒绝，不能算成已受理任务执行失败。"
                      "该原因码不解释通道为什么被占满，也不能排除外部依赖间接延长占用；应继续核对入口容量和持有时长。"
                      if known_lane_rejections else
                      "HTTP失败或202未受理不能单独证明Admission正在保护服务；必须区分限流、传输故障和外部依赖。"),
         "change": "先按reason_code、lane、配额与retry_after定位拒绝来源；只有确认Search与Commit争用配额时，再评估独立预算。",
         "verify": "逐档报告实际发送、202受理、完成与积压恢复；继续升档至真实运行边界，不把首个非零错误档当最大容量。"},
        {"priority": "P0", "module": "租户公平调度", "metrics": "M3 / M4",
         "evidence": conclusions["M3"]["evidence"],
         "judgment": conclusions["M3"]["conclusion"],
         "change": ("先核对等权输入和同窗完成分布；若重复稳态仍不均，定位在途配额与租户出队顺序，再评估轮询或DRR。"
                    if load["fairness_status"] == "MEASURED" else
                    "先补所有预期租户的同窗计数和Search样本；缺数据不能当作调度器不公平。"),
         "verify": "固定独立租户等权输入，至少重复3轮稳态负载；记录每租户完成、等待和Jain，不用排空后的完成数回填。"},
        {"priority": "P0", "module": "路由与意图模型", "metrics": "M1 / M4",
         "evidence": timing_evidence,
         "judgment": "快速路径与意图LLM路径应分别观察；现有端到端P95不能定位模型、排队或编排各自耗时，更不能证明开启thinking。",
         "change": "补同一请求的路由选择、排队、意图LLM、embedding、fanout和merge计时；先确认调用配置和实际路径，再决定缓存、超时预算或快速路径改动。",
         "verify": "对同一问题集按executed_layers拆分样本数、P50/P95/P99和召回质量；并发阶段不简单相加，不用两组P95相减归因。"},
        {"priority": "P1", "module": "原子引擎 Atomic Engine", "metrics": "M1 / M4",
         "evidence": timing_evidence,
         "judgment": ("已有引擎计时，但不足以确认或排除其为瓶颈；需要同请求关联及质量证据。"
                      if atomic else "未采集到有效Atomic阶段计时，不能认定引擎快或慢。"),
         "change": "先检查embedding、索引读取、候选合并与Commit索引写入的时序和锁等待；有争用证据后再调整线程池、隔离或索引锁。",
         "verify": "在相同记忆规模和问题分布下比较空闲/洪泛的逐请求引擎耗时、端到端耗时及召回质量，保留失败请求。"},
        {"priority": "P1", "module": "租户故障隔离", "metrics": "M2",
         "evidence": conclusions["M2"]["evidence"],
         "judgment": m2_judgment,
         "change": "先确认故障控制回执、目标效应和旁观请求独立发送；若多轮仍有串扰，再检查共享worker、permit、provider连接和重试预算。",
         "verify": "每目标独立测before/during/after，轮流注入reject/delay；保留未生效用例、漏发与全部错误，扩展真实慢依赖故障。"},
        {"priority": "P1", "module": "Commit 持久化与恢复", "metrics": "M5",
         "evidence": conclusions["M5"]["evidence"],
         "judgment": conclusions["M5"]["conclusion"],
         "change": "若任务、幂等或顺序对账失败，检查202之前的持久化、恢复扫描与终态原子写入；已有样本通过不等于必须改动实现。",
         "verify": "覆盖入队后、执行中、落archive前后、并发积压与连续崩溃；逐个原任务对账history/archive/cursor，禁止重提任务掩盖失败。"},
        {"priority": "P1", "module": "可观测性", "metrics": "M6",
         "evidence": conclusions["M6"]["evidence"],
         "judgment": conclusions["M6"]["conclusion"],
         "change": "将生效配置中的启用层与接口声明核对；先补平台采样与分母，确实缺失的服务层再补只读四元组，不假定某层已实现或缺失。",
         "verify": "逐轮逐帧核验tenant×lane字段、采样间隔和进程身份；缺失、负数、重复和同进程计数回退单列，末次完整不代表全过程完整。"},
    ]


def redacted_report(source: dict, capacity: dict) -> dict:
    metrics = source.get("metrics", {})
    joint = dict(metrics.get("M3_M4", {}))
    if "unresolved_after_observation" not in joint and "pending_after_drain" in joint:
        joint["unresolved_after_observation"] = joint.pop("pending_after_drain")
    m6 = observability_counts(metrics.get("M6", {}))
    levels = []
    for level in capacity.get("levels", []):
        if "search" in level:
            levels.append(level)
        else:
            for key in ("pure_aggregate", "mixed_aggregate"):
                if level.get(key):
                    levels.append({**level[key], "hot_users": level["hot_users"]})
    dau_values = [row.get("conservative_dau") for row in
                  (capacity.get("dau") or {}).get("estimates", [])
                  if row.get("conservative_dau") is not None]
    report = {"status": "INITIAL_OBSERVATIONS", "performance_requirements_applied": False,
            "publication": {"redacted": True, "raw_requests_exported": False, "private_identities_exported": False},
            "platform_base_pr": 31, "current_phase": source.get("current"),
            "duration_s": source.get("duration_s"), "per_tenant_search_rps": source.get("per_tenant_search_rps"),
            "environment": capacity.get("manifest", {}),
            "M1": {"levels": levels, "max_hot_users": capacity.get("max_hot_users"),
                   "zero_error_max_hot_users": capacity.get("zero_error_max_hot_users"),
                   "first_nonzero_error_hot_users": capacity.get("first_nonzero_error_hot_users"),
                   "max_dau": max(dau_values, default=None), "dau": capacity.get("dau"),
                   "boundary": capacity.get("boundary"),
                   "operational_boundary": capacity.get("operational_boundary")},
            "M2": fault_matrix_counts(metrics.get("M2", {})), "M3_M4": joint,
            "M5": recovery_counts(metrics.get("M5", {})), "M6": m6}
    report["load_evidence"] = load_counts(joint)
    report["conclusions"] = derive_conclusions(report)
    report["module_recommendations"] = derive_module_recommendations(report)
    return report


def render(report: dict) -> str:
    def fmt(value):
        if value is None:
            return "未采集"
        if isinstance(value, bool):
            return "是" if value else "否"
        return f"{value:.3f}" if isinstance(value, float) else escape(str(value))

    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{escape(h)}</th>' for h in headers) + '</tr></thead><tbody>' + (
            ''.join('<tr>' + ''.join(f'<td>{fmt(v)}</td>' for v in row) + '</tr>' for row in rows)
            or f'<tr><td colspan="{len(headers)}">尚未采集到数据</td></tr>') + '</tbody></table></div>'

    def details(label, content):
        return f'<details><summary>{escape(label)}</summary>{content}</details>'

    def bar(label, value, maximum, color):
        width = 100 * max(0, value or 0) / max(maximum, .001)
        return f'<div class="bar"><span>{escape(label)}</span><div><i style="width:{min(100,width):.2f}%;background:{color}"></i></div><b>{fmt(value)}</b></div>'

    def comparison_chart(rows, *, before_key="before", during_key="during"):
        maximum = max((value for _, before, during in rows for value in (before, during)
                       if isinstance(value, (int, float))), default=1) or 1
        items = []
        for label, before, during in rows:
            items.append(f'<div class="compare"><b>{escape(label)}</b><span>基线</span>'
                         f'<i class="track"><em style="width:{100 * _number(before) / maximum:.2f}%;background:#3686a0"></em></i><strong>{fmt(before)}</strong>'
                         f'<span>负载中</span><i class="track"><em style="width:{100 * _number(during) / maximum:.2f}%;background:#c65b45"></em></i><strong>{fmt(during)}</strong></div>')
        return '<div class="comparison-chart">' + ''.join(items) + '</div>'

    def conclusion_panel(key):
        value = conclusions[key]
        status = value.get("status", report.get(key, {}).get("status"))
        color, background = (("#a33432", "#fff0ef") if status == "FAIL" else
                             ("#886012", "#fff7df") if status in {"INCONCLUSIVE", "BLOCKED"} else
                             ("#17685e", "#edf7f4") if status == "PASS" else ("#40555e", "#edf1f3"))
        return (f'<div class="conclusion" style="border-color:{color};background:{background}">'
                f'<strong style="color:{color}">结论：{escape(value["level"])}</strong>'
                f'<p>{escape(value["conclusion"])}</p><small><b>证据：</b>{escape(value["evidence"])}</small>'
                f'<small><b>补齐方式：</b>{escape(value["next"])}</small></div>')

    levels = report["M1"]["levels"]
    highest = max((r.get("hot_users", 0) for r in levels), default=None)
    m2, joint, m5, m6 = (report[k] for k in ("M2", "M3_M4", "M5", "M6"))
    conclusions = derive_conclusions(report)
    load_evidence = load_counts(joint)
    fairness = fairness_counts(joint)
    fault_evidence = matrix_counts(m2)
    module_recommendations = derive_module_recommendations(report)
    dau_estimates = (report.get("M1", {}).get("dau") or {}).get("estimates", [])
    conservative_dau = [row.get("conservative_dau") for row in dau_estimates
                        if row.get("conservative_dau") is not None]
    bystanders = [p for p in m2.get("pairs", []) if p["identity_index"] != m2.get("target_index")]
    degradation = [p["p95_degradation_percent"] for p in bystanders if p.get("p95_degradation_percent") is not None]
    worst = (fault_evidence["worst_bystander_p95_change_percent"] if m2.get("cases")
             else max(degradation, default=None))
    representative_priority = priority_counts(joint)
    p95 = representative_priority["p95_s"]
    cards = [("M1 三轮零错误热用户档", report.get("M1", {}).get("zero_error_max_hot_users")),
             ("M1 最高过载观察档", highest),
             ("M1 条件DAU范围", (f"{min(conservative_dau):.1f}-{max(conservative_dau):.1f}"
                               if conservative_dau else None)),
             ("M2 最差旁观 P95 劣化 %", worst),
             ("M3 代表轮Commit Jain", fairness.get("commit_jain")),
             ("M4 确认积压 Search P95 / s" if representative_priority["overlap_basis"] == "confirmed_nonterminal"
              else "M4 仅观察区间 Search P95 / s", p95),
             ("M5 202 自主恢复", m5.get("autonomous_completed")),
             ("M6 有效四元组单元", f"{m6.get('valid_cells', '未采集')}/{m6['expected_cells']}")]
    capacity_rows = [[r.get("hot_users"), "混合" if r.get("mixed") else "纯召回", r["search"].get("p95_s"),
        r.get("effective_search_rps"), r["search"].get("transport_or_http_errors"),
        f"{r['search']['success']}/{r['search']['sent']}",
        r.get("commit", {}).get("submitted", 0), r.get("commit", {}).get("accepted_202", 0),
        r.get("commit", {}).get("completed", 0), r.get("commit", {}).get("unfinished_or_failed", 0),
        r.get("commit", {}).get("peak_in_flight", 0), r.get("commit", {}).get("submission_window_s"),
        r.get("commit", {}).get("p95_s"),
        r.get("resource_summary", {}).get("cpu_peak_percent_one_core_100"),
        (r.get("resource_summary", {}).get("rss_peak_bytes") or 0) / 1048576
        if r.get("resource_summary", {}).get("rss_peak_bytes") is not None else None] for r in levels]
    capacity_error_rows = []
    for row in levels:
        search = row["search"]
        detail = search.get("error_breakdown") or {}
        partition = detail.get("outcome_partition") or {}
        capacity_error_rows.append([
            row.get("hot_users"), "混合" if row.get("mixed") else "纯召回",
            detail.get("denominator_sent", search.get("sent")),
            partition.get("strict_success", search.get("success")),
            detail.get("http_200_quality_failures"), detail.get("http_200_degraded"),
            detail.get("http_non_200"), detail.get("http_4xx"),
            detail.get("authentication_or_permission_http"), detail.get("rate_limited_http_429"),
            detail.get("http_5xx"), detail.get("transport_errors"),
            detail.get("timeout_censored", search.get("timeout_censored")),
            _counts(detail.get("transport_error_types")),
            _counts(detail.get("reason_code_counts") or search.get("http_reason_counts")),
            detail.get("unclassified_failures"),
            "是" if detail.get("partition_complete") else "旧数据未保留完整分区",
        ])
    fault_rows = [[f"T{p['identity_index']+1}", p["before"].get("sent"), p["during"].get("sent"),
        p["before"].get("p95_s"), p["during"].get("p95_s"), p.get("p95_degradation_percent"),
        p["during"].get("transport_or_http_errors")] for p in m2.get("pairs", [])]
    fault_case_rows = [[case.get("repeat"), {"reject": "主动拒绝", "delay": "延迟"}.get(case.get("fault_type")),
        f"T{case['target_index'] + 1}" if case["target_index"] is not None else None,
        "已观测" if case["status"] == "MEASURED" else "证据不足", case["control_verified"], case["target_effect_observed"], case["injected_rejections"],
        case["bystander_http_errors"], case["worst_bystander_p95_change_percent"],
        "；".join(case["incomplete_reasons"]) or "无"] for case in fault_evidence["cases"]]
    fault_detail_rows = [[case["repeat"], case["fault_type"],
        f"T{case['target_index'] + 1}" if case["target_index"] is not None else None,
        f"T{row['identity_index'] + 1}", "目标" if row["target"] else "旁观",
        row["before"]["sent"], row["during"]["sent"], row["after"]["sent"],
        row["before"]["not_sent"], row["during"]["not_sent"], row["after"]["not_sent"],
        row["before"]["p95_s"], row["during"]["p95_s"], row["after"]["p95_s"],
        row["p95_degradation_percent"], row["recovery_p95_degradation_percent"],
        row["during"]["errors"], row["after"]["errors"]]
        for case in fault_evidence["cases"] for row in case["rows"]]
    fairness_rows = [[f"T{t['identity_index']+1}", t["completed"], t["commit_rps"],
        t["search_sent"], t["search_p95_s"], _ratio(t["search_success"], t["search_sent"])]
        for t in fairness["rows"]]
    priority_rows = [[f"T{p['identity_index']+1}", p["before_p95_s"], p["during_p95_s"],
        p["p95_degradation_percent"], p["during_sent"], p["during_http_errors"]]
        for p in priority_counts(joint)["pairs"]]
    commit_outcome_rows = []
    backlog_depth_rows = []
    submission_schedule_rows = []
    for i, repeat in enumerate(joint.get("repeat_summaries") or [joint], 1):
        outcomes = repeat.get("commit_outcomes") or {}
        schedule = repeat.get("submission_schedule") or {}
        submission_schedule_rows.append([repeat.get("repeat", i), schedule.get("mode"),
            schedule.get("requested_rps"), schedule.get("planned_span_s"),
            outcomes.get("observed_submission_span_s"), outcomes.get("submission_timing_samples"),
            outcomes.get("submission_lag_max_s"), outcomes.get("poll_start_lag_max_s"),
            schedule.get("commit_timeout_s")])
        backlog = repeat.get("overlap_evidence") or {}
        backlog_depth_rows.append([repeat.get("repeat", i),
            backlog.get("accepted_with_nonterminal_poll"), backlog.get("confirmed_search"),
            backlog.get("uncertain_search"), backlog.get("min_confirmed_inflight"),
            backlog.get("max_confirmed_inflight")])
        commit_outcome_rows.append([
            repeat.get("repeat", i), outcomes.get("recorded_submissions"),
            outcomes.get("accepted_202"), outcomes.get("http_rejected"),
            outcomes.get("http_status_counts"), outcomes.get("rejection_reason_counts"),
            outcomes.get("unknown_rejection_reasons"), outcomes.get("missing_archive_on_202"),
            outcomes.get("completed"), outcomes.get("failed"), outcomes.get("unresolved"),
            outcomes.get("poll_http_errors")])
    observable_rows = [[r["tenant"],r["lane"],r["queued_peak_during_load"],r["queued"],r["wait_seconds_total"],
        r["exec_seconds_total"],r["rejected_total"],r["accepted_delta"]] for r in m6["rows"]]
    recovery_sample_rows = [[i+1, sample.get("kill_delay_s"), sample.get("status"),
        sample.get("accepted_202"), sample.get("autonomous_completed"), sample.get("expected_messages"),
        sample.get("missing_messages"), sample.get("same_archive"), sample.get("elapsed_s")]
        for i, sample in enumerate(m5.get("samples", []))]
    observation_repeat_rows = [[i+1, sample.get("status"), sample.get("expected_cells"),
        sample.get("valid_cells"), sample.get("missing_cells"), sample.get("invalid_cells"),
        sample.get("duplicate_cells"), sample.get("sample_count")]
        for i, sample in enumerate(m6.get("repeat_observations", []))]
    timeline_rows, frame_rows, regression_rows = [], [], []
    for i, sample in enumerate(m6.get("repeat_observations") or [m6], 1):
        timeline = sample.get("timeline", {})
        timeline_rows.append([i, timeline.get("status"), timeline.get("snapshot_count"),
            timeline.get("passed_snapshots"), timeline.get("during_count"), timeline.get("max_gap_s"),
            timeline.get("max_internal_gap_s"),
            timeline.get("max_sampling_gap_s"), timeline.get("gaps_exceeded"),
            timeline.get("counter_comparisons"), timeline.get("process_identity_observations"),
            len(timeline.get("restart_observations", [])) if timeline else None])
        for frame in timeline.get("snapshots", []):
            frame_rows.append([i, frame["index"], frame["phase"], frame["status"], frame["valid_cells"],
                frame["expected_cells"], frame["missing_cells"], frame["invalid_cells"], frame["duplicate_cells"],
                ', '.join(f"{cell['tenant']}/{cell['lane']}" for cell in frame.get('missing_details', []))])
        for event in timeline.get("counter_regressions", []):
            regression_rows.append([i,event["tenant"],event["lane"],event["counter"],event["before_index"],
                event["after_index"],event["before"],event["after"],event["classification"]])
    curves = ''.join(bar(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '召回'}", r["search"].get("p95_s"),
        max((x["search"].get("p95_s") or 0 for x in levels), default=1), "#327d9d") for r in levels)
    capacity_error_curves = ''.join(bar(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '召回'}",
        100 * _number(_ratio(r["search"].get("transport_or_http_errors"), r["search"].get("sent"))), 100, "#c65b45") for r in levels)
    capacity_rps_curves = ''.join(bar(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '召回'}",
        r.get("effective_search_rps"), max((x.get("effective_search_rps") or 0 for x in levels), default=1), "#258875") for r in levels)
    fault_chart = comparison_chart([(f"T{p['identity_index']+1}", p["before"].get("p95_s"), p["during"].get("p95_s")) for p in m2.get("pairs", [])])
    worst_pairs = []
    for case in fault_evidence["cases"]:
        observed = [row for row in case["rows"] if not row["target"] and row["p95_degradation_percent"] is not None]
        if observed and case["target_index"] is not None:
            row = max(observed, key=lambda r: r["p95_degradation_percent"])
            label = f"{case['fault_type']} T{case['target_index'] + 1} / 旁观 T{row['identity_index'] + 1}"
            worst_pairs.append((label, row["before"]["p95_s"], row["during"]["p95_s"]))
    fault_chart += comparison_chart(worst_pairs)
    priority_chart = comparison_chart([(f"T{p['identity_index']+1}", p["before"].get("p95_s"), p["during"].get("p95_s")) for p in joint.get("paired", [])])
    queue_chart = ''.join(bar(
        f"{row.get('tenant')} {row.get('lane')}", row.get("queued_peak_during_load"),
        max((_number(item.get("queued_peak_during_load")) for item in m6.get("rows", [])), default=1),
        "#6f6aa8") for row in m6.get("rows", []) if row.get("queued_peak_during_load") is not None)
    recommendation_rows = [[item[key] for key in
        ("priority", "module", "metrics", "judgment", "change")]
        for item in module_recommendations]
    recommendation_detail_rows = [[item[key] for key in
        ("priority", "module", "evidence", "verify")]
        for item in module_recommendations]
    env = report.get("environment", {})
    capacity_paths = [(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '纯召回'}",
                       r["search"]) for r in levels]
    fault_paths = []
    for case in m2.get("cases") or [m2]:
        context = f"轮次 {case.get('repeat', 1)} {case.get('fault_type', '故障')}"
        for pair in case.get("pairs", []):
            for phase, label in (("before", "基线"), ("during", "故障中")):
                fault_paths.append((f"{context} T{pair['identity_index']+1} {label}", pair.get(phase, {})))
    fairness_paths = [(f"T{r['identity_index']+1} 公平性窗口", r.get("search", {}))
                      for r in joint.get("tenants", [])]
    priority_paths = []
    for repeat in joint.get("repeat_summaries") or [joint]:
        for pair in repeat.get("paired", []):
            for phase, label in (("before", "基线"), ("during", "洪泛中")):
                priority_paths.append((f"轮次 {repeat.get('repeat', 1)} T{pair['identity_index']+1} {label}",
                                       pair.get(phase, {})))
        priority_paths.append((f"轮次 {repeat.get('repeat', 1)} 观察区间", repeat.get("overlap_search", {})))
        if repeat.get("confirmed_overlap_search"):
            priority_paths.append((f"轮次 {repeat.get('repeat', 1)} 非终态确认区间", repeat["confirmed_overlap_search"]))
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>4U8G 六项指标 · 综合实测</title><style>*{{box-sizing:border-box}}body{{margin:0;color:#24323a;background:#f6f8f9;font:15px/1.7 system-ui,"PingFang SC",sans-serif;letter-spacing:0}}main{{max-width:1360px;margin:auto;padding:28px}}h1{{font-size:28px}}h2{{font-size:21px}}h3{{font-size:17px}}header,section{{padding:20px 0;border-bottom:1px solid #ccd8dc}}.muted{{color:#576b74}}.notice{{border-left:4px solid #ad4637;padding:8px 16px;background:#fff1ec}}.conclusion{{border-left:4px solid #2b8175;padding:10px 16px;background:#edf7f4;margin:12px 0}}.conclusion>strong{{display:block;color:#17685e;font-size:17px}}.conclusion p{{margin:5px 0}}.conclusion small{{display:block;color:#40555e;margin-top:5px}}.stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:24px 0}}.stats div{{border-top:3px solid #278575;padding-top:12px}}.stats b{{display:block;font-size:27px}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:white;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #d7e0e5;text-align:left;vertical-align:top}}th{{background:#e6eef1;white-space:nowrap}}td{{overflow-wrap:anywhere}}code{{overflow-wrap:anywhere}}details{{border-top:1px solid #dbe3e6;margin-top:14px;padding-top:9px}}summary{{cursor:pointer;color:#176b73;font-weight:650}}.chart-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:18px 0}}.chart-grid>div{{min-width:0;border-top:2px solid #9eb3bc;padding-top:8px}}.chart-wide{{max-width:900px}}.bar{{display:grid;grid-template-columns:145px minmax(0,1fr) 70px;gap:12px;align-items:center;margin:10px 0}}.bar>div,.track{{height:14px;background:#dae3e7;display:block}}.bar i,.track em{{display:block;height:100%;font-style:normal}}.bar b{{text-align:right}}.comparison-chart{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:16px 0}}.compare{{display:grid;grid-template-columns:58px minmax(0,1fr) 62px;gap:5px 8px;align-items:center;background:#fff;padding:10px;border-top:2px solid #9eb3bc}}.compare>b{{grid-column:1/-1}}.compare strong{{text-align:right}}.flow{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:16px 0}}.flow span{{background:#fff;border-top:3px solid #278575;padding:10px;text-align:center;font-size:13px}}.flow b{{display:block}}a{{color:#16699b}}@media(max-width:900px){{.chart-grid{{grid-template-columns:1fr}}}}@media(max-width:700px){{main{{padding:14px}}.stats,.comparison-chart{{grid-template-columns:1fr}}h1{{font-size:24px}}.bar{{grid-template-columns:110px minmax(0,1fr) 58px;font-size:12px}}.flow{{grid-template-columns:1fr}}}}</style></head><body><main>
<header><p class="muted">服务器真实 HTTP / 真实模型 / 4 CPU · 8 GiB / PR31 基线 + PR32 测试增强</p><h1>EchoMem 六项指标综合压测报告</h1>
<p>报告按“指标含义、测试方法、图表数据、结论、模块改进”组织。未设置业务性能合格线；所有失败、超时、拒绝和召回错误均保留在分母。</p>
{'<p class="notice">多批次汇总：各指标来自分别选定的测试批次，不是六项同时复测。不同批次的配置、基线、请求率和采样方法需分别核对，数值变化不等于EchoMem性能提升。</p>' if report.get('publication', {}).get('separate_fault_matrix') or report.get('publication', {}).get('metric_sources') else ''}
{''.join('<p><b>' + escape(str(row.get('metrics', ''))) + '：</b>' + escape(str(row.get('description', ''))) + '</p>' for row in report.get('publication', {}).get('metric_sources', []))}
<p>EchoMem <code>{fmt(env.get('echomem_commit'))}</code>；声明的测试平台基础版本 <code>{fmt(env.get('platform_base_commit'))}</code>（不等于执行版本）。</p>
{render_platform_provenance(env.get('platform_provenance'))}
<p class="notice">版本口径：容量详情保留各档 platform_base_pr / platform_base_commit；缺失表示历史快照未记录该字段，不能称所有档位均已重跑本版本。容器4U8G是资源限额，不代表宿主机资源独占。</p></header>
<div class="stats">{''.join('<div>'+escape(label)+'<b>'+fmt(value)+'</b></div>' for label,value in cards)}</div>
<section><h2>六项结论总览</h2>{table(['指标','结论等级','具体结论','关键证据','下一步'],[[key,value['level'],value['conclusion'],value['evidence'],value['next']] for key,value in conclusions.items()])}</section>
<section><h2>六个指标分别反映什么</h2>{table(['指标','反映什么','测试方式','暴露的线上风险','主要关联模块'],[
['M1 容量与DAU','单个4U8G实例在不同热用户数下能完成多少真实Search和Commit？从哪一档开始拒绝、超时或积压？','逐档增加独立热用户，分别运行纯召回和召回+no-recall+Commit混合流量，记录请求、质量、积压和资源。','扩容过晚、请求大量失败、后台任务长期堆积。','Admission、Search编排、Provider并发、Commit队列、资源限制'],
['M2 故障隔离','一个租户被拒绝或依赖变慢时，其他租户的Search是否仍稳定？','T1-T4依次注入reject/delay，采集before/during/after并计算三个旁观租户P95变化。','单租户拖慢全站，形成共享线程池或连接池雪崩。','租户隔离、Bulkhead、连接池、超时与熔断'],
['M3 公平性','同档位租户是否获得近似等权的Commit吞吐与Search延迟？','四个独立租户同速率并发Search与Commit，用窗口内完成吞吐和Search P95倒数计算Jain，零完成保留。','部分租户饥饿，活跃大户长期挤占后台处理能力。','租户调度器、队列轮询、在途配额'],
['M4 Search优先级','Commit洪泛时，交互式Search是否仍优先并保持召回质量？','先测无Commit基线，再并发提交32个Commit，只统计与202后未终态任务区间重叠的Search。','后台写任务拖慢用户在线查询。','Search/Commit分lane、Admission、路由、Atomic bulkhead'],
['M5 崩溃恢复','已经返回202的Commit在进程崩溃后是否自主恢复且不丢失、不乱序、不重复？','任务202且pending时kill -9，重启后轮询原任务并对账history/archive/cursor/顺序/幂等。','接口称已受理但任务永久丢失，或重放产生重复记忆。','持久队列、状态机、cursor、幂等与archive写入'],
['M6 可观测性','能否按租户、按处理层看到queue/wait/exec/reject四元组？','负载前中后采样受保护接口，按实际配置枚举tenant×lane并覆盖NORMAL/QUEUE/REJECT/RESET。','出现慢请求时无法判断卡在路由、模型、检索还是Commit。','Metrics、Trace、lane声明、reason_code']])}</section>

<section><h2>EchoMem 模块改进优先级</h2><p>以下建议由本次数据推导；“判断”描述证据能够支持的范围，不把黑盒延迟直接当作某一模块的确定代码缺陷。</p>{table(['优先级','模块','关联指标','判断','建议修改'],recommendation_rows)}{details('查看逐模块证据与复测方法',table(['优先级','模块','本次证据','如何复测'],recommendation_detail_rows))}</section>
<section><h2>测试数据与真实操作</h2>{details('查看测试样本、HTTP 操作和成功判定',table(['对象','测试数据','真实HTTP操作','成功判定'],[
['Search召回','每个身份预先写入5段确定性记忆，包含20个固定事实；从40个改写问题中轮换提问。','POST /api/retrieval/search；问题不携带答案；记录HTTP、端到端耗时、引擎来源和返回items。','HTTP 200、非degraded、返回items中出现预先锁定的日期/时间/地点/联系人。'],
['Search不召回','20个日常问题，例如问候、算术和翻译，答案不依赖个人记忆。','与召回问题走同一个真实Search接口，混合场景按固定随机种子穿插。','HTTP 200、非degraded、记忆items为空；不能把接口200直接算召回正确。'],
['Commit','每个租户创建独立session，写入一段确定性记忆；洪泛场景每租户8个session。','POST Commit并使用唯一幂等键；只把202且返回archive_id计为受理，随后轮询commit_status。','状态到completed才计完成；failed/error/超时保留，窗口外完成不计入窗口吞吐。'],
['崩溃恢复','专用session写带序号消息，实际消息数见M5逐样本表；Commit返回202且仍pending时kill -9容器。','启动同一容器后轮询原任务，不重新提交；再读取history/archive/cursor并做一次同幂等键重试。','原任务自主completed，全部源消息集合和顺序无缺失，cursor一致，幂等重试指向同archive。'],
['租户与观测','四个独立tenant/user/key，不用同一key伪装多租户。','负载前、中、后调用受保护只读观测接口；故障通过受保护test-control端点注入。','每个预期tenant×lane都有queue/wait/exec/reject四元组，字段非负、唯一且负载变化可见。']]))}</section>
<section><h2>M1 · 热用户与 DAU</h2><p><b>测试方式：</b>每个热用户拥有独立身份和预注入记忆，以1 Search/s开放到达率同时发请求；纯召回与“召回+不召回+Commit”混合流量分别测。混合场景在第30–60秒为每个热用户错峰提交1个非空Commit，因此H4代表计划4个、H8代表计划8个；“峰值在途”按每个已获202任务从受理到终态的真实时间区间计算，不把总提交数冒充服务端并行度。每档记录全部请求、P95/P99、有效吞吐、错误/降级、CPU/RSS及积压恢复。分别展示每档请求完成与召回质量，不设性能合格线；历史零错误档仅作参考。继续升档并在停压后观察恢复，只有服务无法完成请求、崩溃、OOM或积压在明确观察窗口内无法恢复，才记录运行边界。</p>{conclusion_panel('M1')}<div class="chart-grid"><div><h3>P95 / 秒</h3>{curves}</div><div><h3>HTTP/传输错误率 / %</h3>{capacity_error_curves}</div><div><h3>严格有效 Search/s</h3>{capacity_rps_curves}</div></div>{details('查看每档完整计数、Commit 和资源数据',table(['H','负载','Search P95 s','有效 Search/s','HTTP/传输错误','严格有效/发出','Commit提交','Commit 202','Commit完成','Commit未完成/失败','Commit峰值在途','提交跨度 s','Commit P95 s','CPU峰值 %','RSS峰值 MiB'],capacity_rows))}
{details('查看 Search 错误完整拆分',table(['H','负载','已发出分母','严格成功','HTTP 200质量失败','其中降级','HTTP非200','4xx','401/403','429','5xx','传输错误','超时','传输类型','EchoMem reason_code','未分类','分母对账'],capacity_error_rows))}
{render_route_paths(capacity_paths)}
<p>错误拆分将每个已发请求唯一归入严格成功、HTTP 200质量失败、HTTP非200、传输错误或未分类；分母对账为“是”时类别总和等于已发请求数。401/403不能在缺少provider原因码或日志时直接归因为模型API Key。100% CPU 表示一个 CPU 核。最高已测档位不是最大用户量；最大 DAU 尚未验证，画像换算及每题数据见 <a href="capacity-report.html">容量详细报告</a>。</p></section>
<section><h2>M2 · 单租户故障隔离</h2><p><b>测试方式：</b>四租户同时执行召回问题；每个目标重新测故障前基线，随后只对该目标注入reject或delay，再撤销并测恢复窗口。前中后使用同一问题/到达随机种子，目标和旁观租户按同速率发Search。reject需明确TEST_FAULT_INJECTED原因码，delay需匹配配置和目标延迟增量；同时核验目标控制回执与故障窗口有效期。劣化=(故障中P95/基线P95−1)×100%，不设置性能通过阈值。旧结果不会补造缺失的原因码与控制回执。</p>{conclusion_panel('M2')}
{'<p class="notice">本页历史输入未声明每目标独立基线。上面是本轮修正后的脚本方案，不代表旧数据已经按新方案复测。</p>' if m2.get('cases') and m2.get('baseline_scope') != 'per_target' else ''}{fault_chart}
{table(['租户','基线样本','故障中样本','基线P95 s','故障中P95 s','变化 %','故障中HTTP错误'],fault_rows) if fault_rows else ''}
{render_route_paths(fault_paths)}
{table(['轮次','故障','目标','核验状态','控制证据完整','目标效应已证实','明确注入拒绝数','旁观HTTP错误','最差旁观P95变化 %','不足依据'],fault_case_rows) if fault_case_rows else ''}
<details><summary>逐用例、逐租户的基线 / 故障中 / 恢复数据</summary>{table(['轮次','故障','目标','请求租户','角色','基线样本','故障中样本','恢复样本','基线漏发','故障中漏发','恢复漏发','基线P95 s','故障中P95 s','恢复P95 s','故障变化 %','恢复变化 %','故障中HTTP错误','恢复HTTP错误'],fault_detail_rows)}</details>
<p>预期用例 {fmt(fault_evidence['expected_cases'])}；已记录 {fmt(fault_evidence['recorded_cases'])}；完整核验 {fmt(fault_evidence['measured_cases'])}；未执行 {fmt(fault_evidence['unexecuted_cases'])}；重复 {fmt(fault_evidence['duplicate_cases'])}。所有已知旁观HTTP错误 {fmt(fault_evidence['known_bystander_http_errors'])}，未核验完整的用例也计入已知错误。</p>
<p>基线全部严格有效：{fmt(m2.get('baseline_strict_valid'))}；故障窗口覆盖：{fmt(m2.get('fault_window_covered'))}。结论仅覆盖上表实际生效的目标租户、故障类型与轮次，不外推为任意租户与任意慢依赖故障均已验证。</p></section>
<section><h2>M3 · 等权租户公平性</h2><p><b>测试方式：</b>四个不同租户各准备8个独立写session，在同一个60秒洪泛观察窗口并发Search与Commit。Commit公平性输入是每租户“窗口内completed/秒”，Search公平性输入是每租户P95倒数；没有完成的租户必须以0留在分母，窗口后的任务不回填。</p>{conclusion_panel('M3')}
{details('查看逐租户公平性数据',table(['租户','窗口内Commit完成','Commit/s','Search样本','Search P95 s','严格有效率'],fairness_rows))}
{render_route_paths(fairness_paths)}
{bar('代表轮 Commit Jain',fairness.get('commit_jain'),1,'#258875')}{bar('代表轮 Search Jain',fairness.get('search_inverse_p95_jain'),1,'#327d9d')}
{table(['轮次','证据状态','窗口 s','预期租户','Commit计数覆盖','Search覆盖','重算Commit Jain','重算Search逆P95 Jain'],[[r['repeat'],r['fairness']['status'],r['fairness']['window_s'],r['fairness']['expected_tenants'],r['fairness']['commit_tenants'],r['fairness']['search_tenants'],r['fairness']['commit_jain'],r['fairness']['search_inverse_p95_jain']] for r in load_evidence['rows']])}
<p>Jain 接近 1 表示相对均匀，不代表快或可靠；零完成租户保留，全部零完成时指数留空。窗口后的排空完成不混入窗口吞吐；此为洪泛窗口观察，不是长时间稳态公平性结论。</p></section>
<section><h2>M4 · Commit 洪泛下的 Search</h2><p><b>测试方式：</b>先在无Commit时用固定问题序列测Search基线；随后并发提交真实Commit，并用同一问题、同一到达计划测Search。选取开始于Commit受理之后、首次观测终态（或结束观察）之前的Search样本。轮询间隔内的实际完成时刻未知，该区间是客户端观察范围，不能证明服务内部严格调度顺序。</p>{conclusion_panel('M4')}<div class="flow"><span><b>1</b>预注入记忆</span><span><b>2</b>无Commit基线</span><span><b>3</b>并发提交，分别记录受理与拒绝</span><span><b>4</b>观察区间内Search</span><span><b>5</b>轮询原任务终态</span></div>{priority_chart}<p>受理 202：{fmt(joint.get('accepted_202'))}/{fmt(joint.get('commit_planned'))}；各任务配置的观察期限内完成：{fmt(joint.get('completed_including_drain'))}；观察期内未见终态：{fmt(joint.get('unresolved_after_observation', joint.get('pending_after_drain')))}。</p>
<h3>Commit 受理、拒绝与终态</h3>
{table(['轮次','提交模式','计划总提交/s','计划提交跨度 s','实际提交跨度 s','有效提交计时','最大提交延迟 s','最大轮询启动延迟 s','原任务观察上限 s'],submission_schedule_rows)}
<p>burst是突发提交，paced是按固定总速率提交独立任务；提交与状态轮询使用不同线程池。提交延迟按实际开始减去计划开始计算，包含压测端调度等待，不归因于EchoMem。名义提交速率不等于服务吞吐；拒绝不自动补发。</p>
{table(['轮次','已记录提交','受理202','HTTP拒绝','HTTP状态分布','拒绝原因分布','原因未明确','202缺archive','completed','failed/error','未终态','轮询HTTP错误'],commit_outcome_rows)}
<p>每个Commit只提交一次；拒绝不自动重提，避免隐藏过载。503不自动归因于限流或API key；原因码仅保留已识别公共枚举。状态轮询可在原期限内继续，但全部轮询错误保留。旧数据未记录的字段显示未采集。</p>
<h3>积压区间的证据强度</h3>
{table(['轮次','验收采用口径','观察区间Search','非终态确认Search','观察区间P95 s','非终态确认P95 s'],[[r['repeat'],r['priority']['overlap_basis'],r['priority']['observed_sent'],r['priority']['confirmed_sent'],r['priority']['observed_p95_s'],r['priority']['confirmed_p95_s']] for r in load_evidence['rows']])}
<p>观察区间截止首次看到终态或结束观察；确认区间截止最后一次成功非终态轮询的请求开始时间。只有确认区间内样本用于完整M4证据，最后轮询空档仍保留统计但不当成已确认积压。此证据依赖原任务终态不回退，不代表Search整段执行时间都与Commit重叠，也不证明内部严格调度。</p>
{table(['轮次','有非终态确认的Commit','确认Search','轮询空档不确定Search','Search开始时最少确认在途','Search开始时最多确认在途'],backlog_depth_rows)}
<p>在途数是这些Search请求开始时、可由后续非终态轮询确认的任务数下界，不是服务队列深度，也不是全轮瞬时并发峰值。32次累计受理不代表始终有32个Commit同时运行。</p>
{details('查看逐租户基线与洪泛对比',table(['租户','无Commit P95 s','洪泛窗口P95 s','变化 %','洪泛Search样本','HTTP错误'],priority_rows))}
{table(['轮次','证据状态','有效配对','预期租户','Commit计划','实际202受理','最低洪泛受理','重叠Search发出','严格有效','HTTP/传输错误','重叠P95 s'],[[r['repeat'],r['priority']['status'],r['priority']['valid_pairs'],r['priority']['expected_tenants'],r['priority']['commit_planned'],r['priority']['accepted_202'],r['priority']['minimum_flood_commits'],r['priority']['sent'],r['priority']['success'],r['priority']['transport_or_http_errors'],r['priority']['p95_s']] for r in load_evidence['rows']])}
{render_route_paths(priority_paths)}
<p>代表轮所用区间：{fmt(representative_priority['overlap_basis'])}，Search {fmt(representative_priority['sent'])} 个，P95={fmt(p95)} 秒。全窗口、观察区间与非终态确认区间分别展示；历史仅观察区间的数据不充作完整积压证据。“未见终态”不等于永久丢失，内部严格调度顺序也尚未证明。</p></section>
<section><h2>M5 · 202 后崩溃恢复</h2><p><b>测试方式：</b>Commit已返回202且commit_status仍为pending时对专用容器执行kill -9；启动后只轮询原任务，不能通过重提Commit掩盖恢复失败。completed后再对history、archive、cursor、消息集合/顺序和幂等键逐项对账。</p>{conclusion_panel('M5')}<div class="flow"><span><b>202</b>任务已受理</span><span><b>pending</b>确认尚未完成</span><span><b>kill -9</b>真实进程崩溃</span><span><b>restart</b>原任务自主恢复</span><span><b>reconcile</b>数据与顺序对账</span></div>{table(['观测项','结果'],[['崩溃前已受理202',m5.get('accepted_202')],['原任务自主completed',m5.get('autonomous_completed')],['源消息数',m5.get('expected_messages')],['未对账消息数',m5.get('missing_messages')],['重试是否同archive',m5.get('same_archive')],['总耗时 s',m5.get('elapsed_s')]])}
{table(['行为检查','结果'],[[c['name'],c['status']] for c in m5.get('checks',[])])}
{table(['样本','kill 延迟 s','状态','受理202','自主completed','源消息数','缺失消息','同archive','耗时 s'],recovery_sample_rows) if recovery_sample_rows else ''}
<p>计划样本 {fmt(m5.get('expected_samples'))}；已执行 {fmt(m5.get('sample_count'))}；通过 {fmt(m5.get('passed_samples'))}；失败 {fmt(m5.get('failed_samples'))}；证据不足 {fmt(m5.get('inconclusive_samples'))}；未执行 {fmt(m5.get('unexecuted_samples'))}。有消息对账的样本 {fmt(m5.get('reconciled_samples'))}，已知缺失消息 {fmt(m5.get('known_missing_messages'))}。部分样本缺少对账时，总缺失数显示未采集。</p>
<p>逐样本记录真实 kill-9/start 和恢复对账；小样本即使通过，也不能推导所有崩溃时机均 100% 可靠。因未受理、已提前完成或其他任务未排空而没有重启时，按证据不足展示。</p></section>
<section><h2>M6 · 每租户四元组</h2><p><b>测试方式：</b>以EchoMem受保护接口声明/观测到的lane形成分母，在负载前、中、后反复读取快照。每个独立租户×lane都必须同时具有排队深度、累计等待、累计执行、拒绝数；缺失、重复、负数或NaN均不能填0，也不能算通过。</p>{conclusion_panel('M6')}<p>负载中采样 {fmt(m6.get('sample_count'))} 次；本轮期望 {fmt(m6['expected_cells'])} 个 tenant×lane 单元，观察到 {len(m6['rows'])} 个。模块：{escape(', '.join(m6['expected_lanes']))}。</p>
<div class="chart-wide"><h3>各租户各层峰值排队深度</h3>{queue_chart}</div>
{details('查看逐租户逐层四元组',table(['租户','层/模块','队列峰值','最终队列','累计等待 s','累计执行 s','累计拒绝','受理增量'],observable_rows))}
{table(['轮次','状态','预期单元','有效单元','缺失','非法','重复','负载中快照'],observation_repeat_rows) if observation_repeat_rows else ''}
<p>多轮测试的上表字段来自最后一轮快照；各轮缺失和失败在轮次表分别保留，不拼接不同轮次的行来宣称单轮完整。</p>
<h3>负载过程核验</h3>
{table(['轮次','过程状态','快照数','通过快照','负载中快照','含窗口边界最大间隔 s','相邻采样最大间隔 s','配置上限 s','超限间隔','计数比较次数','有进程身份的快照','观察到重启'],timeline_rows)}
<p>采样间隔上限约束监控完整性，不是服务性能门槛。排队深度可以下降；累计等待、执行、拒绝及任务计数出现下降时单独定位。采样点之间的全部变化仍不能由快照证明。</p>
<p>旧证据缺少监控窗口的单调时钟边界时，只报告已记录采样之间的间隔，不能把首尾未观测时间补为零。指标缺失可能来自首次使用后才创建，需要结合服务代码确认。</p>
<details><summary>逐次快照核验</summary>{table(['轮次','序号','阶段','状态','有效单元','预期单元','缺失','非法','重复','缺失租户/模块'],frame_rows)}</details>
{table(['轮次','租户','模块','累计计数','前序号','后序号','之前','之后','分类'],regression_rows) if regression_rows else ''}
<p>累计时间不是单次请求延迟；缺失不能填零。这里只证明上表模块的观测，其他启用层和完整调度顺序仍需补充。</p></section>
<section><h2>测试仍需补齐的分母</h2>{table(['归属','待补测试'],[['测试平台','M1继续提高热用户与记忆规模，直到观察到崩溃、OOM或停止发压后积压无法恢复；补业务DAU画像。'],['测试平台','M2、M3至少重复3轮；M5覆盖多个kill时机、并发积压和连续崩溃。'],['EchoMem / 观测接口','让HTTP admission、router/fanout、provider与archive/storage进入完整lane声明和逐租户四元组。'],['模块归因','按reason_code和阶段耗时拆分429、模型降级、请求超时与Commit拒绝；CPU/内存未满不能证明代码没有瓶颈。']])}</section>
<p><a href="report.json">脱敏统计 JSON</a> · 原始请求、身份凭据和故障详情保留执行机，不包含于分享文件。</p></main></body></html>'''


def publish(source: Path, capacity: Path, output: Path, recovery: Path | None = None,
            fault_matrix: Path | None = None, contention_matrix: Path | None = None,
            observability_repeats: list[Path] | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if recovery:
        raw.setdefault("metrics", {})["M5"] = json.loads(recovery.read_text(encoding="utf-8"))
    if fault_matrix:
        raw.setdefault("metrics", {})["M2"] = json.loads(fault_matrix.read_text(encoding="utf-8"))
    if contention_matrix:
        contention, observability = contention_matrix_counts(
            json.loads(contention_matrix.read_text(encoding="utf-8")))
        raw.setdefault("metrics", {})["M3_M4"] = contention
        raw.setdefault("metrics", {})["M6"] = observability
    if observability_repeats:
        observation = raw.get("metrics", {}).get("M6", {})
        repeats = observation.get("repeat_observations") or [observation]
        if len(repeats) != len(observability_repeats):
            raise ValueError("Supply one snapshot directory per recorded observability repeat, in order")
        for repeat, directory in zip(repeats, observability_repeats):
            frames = {phase: json.loads((directory / f"observability-{phase}.json").read_text(encoding="utf-8"))
                      for phase in ("before", "during", "after")}
            for phase in ("before", "after"):
                if any(frames[phase].get(key) != repeat.get(key) for key in ("expected_tenants", "expected_lanes")):
                    raise ValueError("Snapshot tenant/lane contract does not match the selected repeat")
            if repeat.get("generated_at") is not None and repeat["generated_at"] != frames["after"].get("generated_at"):
                raise ValueError("Snapshot final timestamp does not match the selected repeat")
            repeat["process_observations"] = {
                **repeat.get("process_observations", {}), **frames,
                "expected_tenants": repeat.get("expected_tenants", []),
                "expected_lanes": repeat.get("expected_lanes", []),
            }
        observation["process_observations"] = repeats[-1]["process_observations"]
    capacity_data = json.loads(capacity.read_text(encoding="utf-8"))
    public = redacted_report(raw, capacity_data)
    public["publication"]["separate_fault_matrix"] = fault_matrix is not None
    (output / "report.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.html").write_text(render(public), encoding="utf-8")
    (output / "capacity-report.html").write_text(render_observation(capacity_data).replace('href="report.json"', 'href="capacity-report.json"'), encoding="utf-8")
    (output / "capacity-report.json").write_text(json.dumps(capacity_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--capacity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, help="Optional separate crash sample; original run remains immutable")
    parser.add_argument("--fault-matrix", type=Path, help="Optional full T1-T4 reject/delay matrix")
    parser.add_argument("--contention-matrix", type=Path, help="Optional repeated M3/M4/M6 matrix")
    parser.add_argument("--observability-repeat-dir", type=Path, action="append",
                        help="Private before/during/after snapshot directory for each repeat, in order")
    args = parser.parse_args()
    result = publish(args.source, args.capacity, args.output, recovery=args.recovery,
                     fault_matrix=args.fault_matrix, contention_matrix=args.contention_matrix,
                     observability_repeats=args.observability_repeat_dir)
    print(json.dumps({"status": result["status"], "redacted": True}))


if __name__ == "__main__":
    main()
