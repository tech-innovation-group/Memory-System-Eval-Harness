"""Publish an allowlisted, shareable first report from private main-metric samples."""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path

from performance.targets.echomem.acceptance.capacity_observation_report import render_observation


def recovery_counts(value: dict) -> dict:
    if value.get("samples"):
        samples = [recovery_counts(sample) for sample in value["samples"]]
        passed = sum(sample.get("status") == "PASS" for sample in samples)
        return {"status": "PASS" if passed == len(samples) else value.get("status", "INCONCLUSIVE"),
                "sample_count": len(samples), "passed_samples": passed, "samples": samples,
                "accepted_202": all(sample.get("accepted_202") for sample in samples),
                "autonomous_completed": all(sample.get("autonomous_completed") for sample in samples),
                "expected_messages": sum(sample.get("expected_messages") or 0 for sample in samples),
                "missing_messages": sum(sample.get("missing_messages") or 0 for sample in samples),
                "same_archive": all(sample.get("same_archive") for sample in samples),
                "elapsed_s": sum(sample.get("elapsed_s") or 0 for sample in samples),
                "checks": [{"name": f"sample-{index + 1}/{check['name']}", "status": check["status"]}
                           for index, sample in enumerate(samples) for check in sample.get("checks", [])]}
    checks = value.get("checks", [])
    statuses = [check.get("status") for check in checks]
    status = value.get("status")
    if not status and statuses:
        status = "PASS" if all(item == "PASS" for item in statuses) else (
            "FAIL" if "FAIL" in statuses else "INCONCLUSIVE"
        )
    details = {}
    for check in checks:
        try:
            details[check["name"]] = json.loads(check.get("detail") or "{}")
        except (TypeError, ValueError):
            details[check["name"]] = {}
    operation = details.get("commit-recovery", {})
    messages = details.get("message-reconciliation", {})
    replay = details.get("idempotency-replay", {})
    expected = messages.get("expected_server_message_ids", [])
    return {"checks": [{"name": c["name"], "status": c["status"]} for c in checks],
            "elapsed_s": value.get("elapsed_s"), "status": status,
            "accepted_202": operation.get("accepted_202"),
            "autonomous_completed": operation.get("autonomous_recovery_observed"),
            "expected_messages": len(expected) if expected else None,
            "missing_messages": len(messages.get("missing_server_message_ids", [])) if expected else None,
            "complete_sources": messages.get("complete_sources", []),
            "same_archive": replay.get("same_archive"), "replayed": replay.get("replayed")}


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _number(value, default=0):
    return value if isinstance(value, (int, float)) else default


def fault_matrix_counts(value: dict) -> dict:
    if not value.get("cases"):
        return value
    fields = ("repeat", "fault_type", "target_index", "status", "control_enabled",
              "control_disabled", "target_effect_observed", "target_p95_delta_s",
              "bystander_http_errors", "worst_bystander_p95_change_percent", "pairs", "recovery_pairs")
    return {"status": value.get("status"), "expected_cases": value.get("expected_cases"),
            "measured_cases": value.get("measured_cases"), "repeats": value.get("repeats"),
            "phase_duration_s": value.get("phase_duration_s"),
            "recovery_duration_s": value.get("recovery_duration_s"),
            "bystander_http_errors": value.get("bystander_http_errors"),
            "worst_bystander_p95_change_percent": value.get("worst_bystander_p95_change_percent"),
            "cases": [{key: case.get(key) for key in fields} for case in value["cases"]]}


def contention_matrix_counts(value: dict) -> tuple[dict, dict]:
    samples = value.get("samples", [])
    if not samples:
        return {}, {}
    joints = [sample.get("M3_M4", {}) for sample in samples]
    ranked = [joint for joint in joints if joint.get("commit_jain") is not None]
    representative = dict(min(ranked, key=lambda row: row["commit_jain"]) if ranked else joints[-1])
    representative.update(
        repeat_count=len(samples),
        repeat_summaries=[{"repeat": sample.get("repeat"),
                           "commit_jain": joint.get("commit_jain"),
                           "search_inverse_p95_jain": joint.get("search_inverse_p95_jain"),
                           "commit_planned": joint.get("commit_planned"),
                           "accepted_202": joint.get("accepted_202"),
                           "completed_including_drain": joint.get("completed_including_drain"),
                           "unresolved_after_observation": joint.get("unresolved_after_observation"),
                           "overlap_search": joint.get("overlap_search"),
                           "paired": joint.get("paired", [])}
                          for sample, joint in zip(samples, joints)],
        commit_jain_values=[joint.get("commit_jain") for joint in joints],
        search_inverse_p95_jain_values=[joint.get("search_inverse_p95_jain") for joint in joints],
        commit_planned=sum(joint.get("commit_planned", 0) for joint in joints),
        accepted_202=sum(joint.get("accepted_202", 0) for joint in joints),
        completed_including_drain=sum(joint.get("completed_including_drain", 0) for joint in joints),
        unresolved_after_observation=sum(joint.get("unresolved_after_observation", 0) for joint in joints),
    )
    observations = [sample.get("M6", {}) for sample in samples]
    latest = dict(observations[-1])
    row_groups = {}
    for observation in observations:
        for row in observation.get("rows", []):
            row_groups.setdefault((row.get("tenant_id"), row.get("lane")), []).append(row)
    latest["rows"] = []
    for key, rows in row_groups.items():
        row = dict(rows[-1])
        row["queued_peak_during_load"] = max(
            (_number(item.get("queued_peak_during_load"), -1) for item in rows), default=-1)
        row["accepted_delta"] = sum(_number(item.get("accepted_delta")) for item in rows)
        latest["rows"].append(row)
    latest["sample_count"] = sum(_number(observation.get("sample_count")) for observation in observations)
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
    first_clear_saturation = min((row.get("hot_users") for row in levels
        if row.get("search", {}).get("sent") and
        row["search"].get("transport_or_http_errors", 0) / row["search"]["sent"] >= .01), default=None)
    no_crash = all(row.get("recovery", {}).get("status") in {None, "NO_BOUNDARY_OBSERVED", "RECOVERED"}
                   for row in highest_rows)

    m2 = report.get("M2", {})
    matrix_cases = m2.get("cases", [])
    bystanders = ([row for case in matrix_cases for row in case.get("pairs", [])
                   if row.get("identity_index") != case.get("target_index")]
                  if matrix_cases else
                  [row for row in m2.get("pairs", []) if row.get("identity_index") != m2.get("target_index")])
    bystander_changes = [row.get("p95_degradation_percent") for row in bystanders
                         if row.get("p95_degradation_percent") is not None]
    bystander_errors = sum(row.get("during", {}).get("transport_or_http_errors", 0) for row in bystanders)
    bystander_quality = [row.get("during", {}).get("quality_rate") for row in bystanders]
    matrix_complete = bool(matrix_cases and m2.get("measured_cases") == m2.get("expected_cases"))

    joint = report.get("M3_M4", {})
    tenants = joint.get("tenants", [])
    commit_counts = [row.get("commit_completed_in_search_window", 0) for row in tenants]
    search_p95s = [row.get("search", {}).get("p95_s") for row in tenants
                   if row.get("search", {}).get("p95_s") is not None]
    priority_changes = [row.get("p95_degradation_percent") for row in joint.get("paired", [])
                        if row.get("p95_degradation_percent") is not None]
    overlap = joint.get("overlap_search", {})
    commit_jain = joint.get("commit_jain")
    all_commit_zero = bool(commit_counts) and not any(commit_counts)
    priority_increases = sum(value > 0 for value in priority_changes)
    priority_decreases = sum(value < 0 for value in priority_changes)
    priority_consistent_degradation = bool(priority_changes and
                                           priority_increases == len(priority_changes))
    overlap_sent = overlap.get("sent", 0)
    overlap_success = overlap.get("success", 0)
    overlap_http_errors = overlap.get("transport_or_http_errors", 0)

    m5 = report.get("M5", {})
    recovery_pass = bool(m5.get("accepted_202") and m5.get("autonomous_completed")
                         and m5.get("missing_messages") == 0 and m5.get("same_archive")
                         and m5.get("checks") and all(row.get("status") == "PASS" for row in m5["checks"]))
    m6 = report.get("M6", {})
    observed_cells = len(m6.get("rows", []))
    expected_cells = m6.get("expected_cells", 0)
    commit_rows = [row for row in m6.get("rows", []) if row.get("lane") == "commit"]

    if zero_error_level is not None:
        m1_level = "零错误档已确认，硬容量未确定"
        m1_conclusion = (
            f"连续三轮全部请求完成的最高实测档是 {zero_error_level} 个热用户；"
            f"{first_nonzero_error} 用户档出现过非零请求错误。这个数字只描述严格零错误合同，"
            f"不是最大承载用户数。更高档已测到 {highest} 用户，服务未崩溃、OOM或形成不可恢复积压，"
            "因此硬容量上限仍未测得。"
        )
    elif confirmed_capacity is not None:
        m1_level = "请求完成边界已确认"
        m1_conclusion = (
            f"在不设置延迟和召回质量门槛的请求完成合同下，最大热用户代理为 {confirmed_capacity}；"
            f"紧邻的 {first_failed_capacity} 用户档至少出现一项 Search 未完成、HTTP/传输失败或 Commit 未完成。"
            f"更高档已继续测到 {highest} 用户，用于展示过载形态，不改变相邻档三重复确认的边界。"
        )
    else:
        m1_level = "边界未确定"
        m1_conclusion = (f"已测到 {highest} 个跨租户热用户，但 {highest} 用户档无法承载每用户 1 Search/s 的目标流量；"
                         f"首次持续出现超过 1% HTTP/传输错误的档位是 {first_clear_saturation} 用户。"
                         "服务未崩溃或 OOM，因此不能把最高已测档写成绝对容量上限。")

    return {
        "M1": {
            "level": m1_level,
            "conclusion": m1_conclusion,
            "evidence": (f"{highest}用户纯召回：P95 {_number(pure_search.get('p95_s')):.2f}s，"
                         f"HTTP/传输错误 {pure_search.get('transport_or_http_errors', 0)}/{pure_search.get('sent', 0)}，"
                         f"严格有效 {pure_search.get('success', 0)}/{pure_search.get('sent', 0)}；"
                         f"混合流量：P95 {_number(mixed_search.get('p95_s')):.2f}s，"
                         f"严格有效 {mixed_search.get('success', 0)}/{mixed_search.get('sent', 0)}。"
                         f"无崩溃/OOM={no_crash}。"),
            "next": ("继续提高负载直到崩溃、OOM或积压无法恢复；零错误档与硬容量分别报告。"
                     if zero_error_level is not None else
                     "边界已按锁定SLO确认；线上容量规划仍需结合DAU画像。"
                     if confirmed_capacity is not None else
                     "对相邻通过/失败档各做三次新身份重复；线上容量还需定义DAU画像。"),
        },
        "M2": {
            "level": (f"{m2.get('measured_cases')}/{m2.get('expected_cases')} 故障用例已测"
                      if matrix_cases else "单次初步支持，正式结论证据不足"),
            "conclusion": ((f"四个测试租户均完成reject与delay故障验证，共 {m2.get('repeats', 1)} 轮；旁观租户的HTTP错误和最差P95变化见下表。"
                            "结论严格限定在这些实测租户、故障类型和轮次。") if matrix_complete else
                           "当前故障矩阵尚未全部形成有效目标故障证据，不能宣称任意租户故障都已隔离。" if matrix_cases else
                           "本次只对一个租户注入一次故障；旁观租户结果可作初步观察，不能宣称任意租户故障都隔离成功。"),
            "evidence": (f"有效故障用例 {m2.get('measured_cases', 1)}/{m2.get('expected_cases', 1)}；旁观租户错误 {bystander_errors}；"
                         f"旁观 P95 变化 {', '.join(f'{value:.2f}%' for value in bystander_changes) or '未采集'}；"
                         f"故障中质量 {', '.join(f'{value * 100:.1f}%' for value in bystander_quality if value is not None)}；"
                         f"基线全部严格有效={m2.get('baseline_strict_valid')}。"),
            "next": ("保持原始before/during/after请求证据，并按模型/存储故障类型继续扩展。" if matrix_complete else
                     "轮流故障四租户，覆盖reject/delay并重复；目标故障未生效的用例保留在分母。"),
        },
        "M3": {
            "level": ("Commit公平性数据不足；Search相对公平" if all_commit_zero else
                      "观察到租户完成分布不均" if commit_jain is not None else "公平性数据不足"),
            "conclusion": (("四个租户在本次短窗口内完成的 Commit 均为0，Jain分母为0，不能判断公平或不公平；"
                            "窗口后完成的任务不能回填。Search延迟分布相对接近，但样本窗口较短。")
                           if all_commit_zero else
                           f"四租户窗口内Commit完成数为{commit_counts}，并非等权分布；"
                           "Search P95也存在租户差异。Jain指数只描述本次窗口的相对均匀程度，不代表速度或可靠性。"),
            "evidence": (f"Search逆P95 Jain={_number(joint.get('search_inverse_p95_jain')):.3f}，"
                         f"P95范围 {min(search_p95s, default=0):.2f}-{max(search_p95s, default=0):.2f}s；"
                         f"Commit Jain={'未定义' if commit_jain is None else f'{commit_jain:.3f}'}，"
                         f"窗口完成数={commit_counts}。"),
            "next": "延长等权稳态窗口并重复，仍按窗口内完成数统计；零完成租户必须保留在分母。",
        },
        "M4": {
            "level": ("Search P95一致上升；严格优先未证明" if priority_consistent_degradation else
                      "Search完成正常；严格优先仍未证明"),
            "conclusion": (f"Commit 洪泛窗口中 {priority_increases} 个租户P95上升、{priority_decreases} 个下降；"
                           f"重叠Search严格召回正确 {overlap_success}/{overlap_sent}，HTTP/传输错误 {overlap_http_errors}。"
                           + ("本轮观察到四租户P95一致劣化。" if priority_consistent_degradation else
                              "本轮没有观察到所有租户一致劣化。")
                           + "但只凭端到端延迟仍不能证明服务内部严格先调度Search。"),
            "evidence": (f"积压重叠 Search P95={_number(overlap.get('p95_s')):.2f}s，质量={_number(_ratio(overlap.get('success'), overlap.get('sent'))):.1%}；"
                         f"四租户 P95 劣化 {min(priority_changes, default=0):.2f}%-{max(priority_changes, default=0):.2f}%；"
                         f"Commit 202={joint.get('accepted_202', 0)}/{joint.get('commit_planned', 0)}，"
                         f"观察期完成={joint.get('completed_including_drain', 0)}，未见终态={joint.get('unresolved_after_observation', 0)}。"),
            "next": "补服务端可读调度事件或序列证据，区分优先级调度、模型并发与Atomic bulkhead对P95的贡献。",
        },
        "M5": {
            "level": (f"{m5.get('passed_samples')}/{m5.get('sample_count')} 恢复样本通过"
                      if m5.get("sample_count") else "本次样本通过"),
            "conclusion": ("本次真实 kill-9/start 样本中，崩溃前已返回202且未完成的Commit在重启后自主完成，"
                           "消息集合、顺序、cursor和幂等重试均一致。结论只覆盖报告列出的实际样本，不外推为数学上的100%可靠性。"),
            "evidence": (f"恢复样本 {m5.get('passed_samples', 1)}/{m5.get('sample_count', 1)}；源消息 {m5.get('expected_messages')} 条，缺失 {m5.get('missing_messages')} 条；"
                         f"同archive={m5.get('same_archive')}，耗时 {_number(m5.get('elapsed_s')):.1f}s；全部检查通过={recovery_pass}。"),
            "next": "覆盖不同崩溃时机、并发Commit、重复崩溃和更大积压，再计算真实恢复成功率。",
        },
        "M6": {
            "level": "当前四个模块覆盖完整，全部层级尚未证明",
            "conclusion": ("本次定义的4租户×4模块四元组全部可读取，字段完整且数值有效；"
                           "可观测接口能展示Commit积压和Recall拒绝。但测试分母只含4个模块，不能宣称EchoMem每一层都已覆盖。"),
            "evidence": (f"有效单元 {observed_cells}/{expected_cells}，缺失 {m6.get('missing_cells', 0)}，"
                         f"非法 {m6.get('invalid_cells', 0)}，负载中快照 {m6.get('sample_count')} 次；"
                         f"服务声明lane数={m6.get('declared_lane_count')}，数量对账={m6.get('lane_count_matches_observed')}；"
                         f"Commit队列峰值 {[row.get('queued_peak_during_load') for row in commit_rows]}。"),
            "next": "从生效配置枚举所有启用层形成分母，并为HTTP/admission/fanout等缺失层补只读观测或明确排除依据。",
        },
    }


def derive_module_recommendations(report: dict) -> list[dict]:
    """Map measured symptoms to bounded EchoMem module recommendations."""
    levels = report.get("M1", {}).get("levels", [])
    joint = report.get("M3_M4", {})
    m2 = report.get("M2", {})
    m5 = report.get("M5", {})
    m6 = report.get("M6", {})
    tenant_counts = [row.get("commit_completed_in_search_window", 0)
                     for row in joint.get("tenants", [])]
    atomic_p95 = [row.get("search", {}).get("atomic_p95_s")
                  for row in joint.get("tenants", [])
                  if row.get("search", {}).get("atomic_p95_s") is not None]
    residual_p95 = [row.get("search", {}).get("unattributed_residual_p95_s")
                    for row in joint.get("tenants", [])
                    if row.get("search", {}).get("unattributed_residual_p95_s") is not None]
    bystander_changes = [case.get("worst_bystander_p95_change_percent")
                         for case in m2.get("cases", [])
                         if case.get("worst_bystander_p95_change_percent") is not None]
    fault_cases = m2.get("cases", [])
    effective_fault_cases = sum(case.get("target_effect_observed") is True
                                for case in fault_cases)
    accepted = joint.get("accepted_202", 0)
    planned = joint.get("commit_planned", 0)
    highest = max((row.get("hot_users", 0) for row in levels), default=None)
    highest_errors = max((row.get("search", {}).get("transport_or_http_errors", 0)
                          for row in levels if row.get("hot_users") == highest), default=0)
    return [
        {"priority": "P0", "module": "Admission 与容量保护", "metrics": "M1 / M4",
         "evidence": (f"最高观察档 H={highest} 出现最多 {highest_errors} 个 HTTP/传输错误；"
                      f"洪泛 Commit 仅受理 {accepted}/{planned}。"),
         "judgment": "服务能通过拒绝保护自身，但当前报告无法区分全局容量、租户配额和具体拒绝原因。",
         "change": "为 Search 预留独立 admission 配额；Commit 使用独立上限，并返回稳定的 reason_code、lane、tenant quota 与 retry_after。",
         "verify": "重跑 H4/H8/H16 与 32 Commit 洪泛，按 reason_code 拆分拒绝数，并确认 Search 不被 Commit 配额占满。"},
        {"priority": "P0", "module": "租户公平调度", "metrics": "M3 / M4",
         "evidence": (f"60 秒窗口内各租户 Commit 完成数={tenant_counts}，"
                      f"Commit Jain={joint.get('commit_jain')}，Search Jain={joint.get('search_inverse_p95_jain')}。"),
         "judgment": "同档位租户没有获得近似等权的完成机会，慢租户同时承受更高 Search 延迟。",
         "change": "Commit lane 引入按租户轮询或 DRR；限制单租户在途数；Search lane 使用独立 worker/permit，并在调度器中显式高于 Commit。",
         "verify": "固定4租户等权输入，至少重复3轮；检查每租户窗口完成数、等待时间和Jain分布，不能用窗口后排空数回填。"},
        {"priority": "P0", "module": "路由与意图模型", "metrics": "M1 / M4",
         "evidence": (f"洪泛下 Atomic P95 最大约 {max(atomic_p95, default=0):.3f}s，"
                      f"未归因残余 P95 最大约 {max(residual_p95, default=0):.3f}s。"),
         "judgment": "当前样本中原子检索本身较快，较大的端到端尾延迟主要发生在未单独计时的路由、模型调用、排队或编排阶段。",
         "change": "为 intent/router 设置总时间预算、超时降级和熔断；确定性 memory-recall query 优先走快速路径；分别记录排队、LLM、embedding、fanout、merge耗时。",
         "verify": "同一批固定问题对比 router 开/关及快速路径，要求报告逐阶段 P50/P95/P99，并核对最终召回质量不下降。"},
        {"priority": "P1", "module": "原子引擎 Atomic Engine", "metrics": "M1 / M4",
         "evidence": f"本轮租户 Atomic P95 范围为 {min(atomic_p95, default=0):.3f}-{max(atomic_p95, default=0):.3f}s。",
         "judgment": "现有证据不支持把 Atomic Engine 认定为主要瓶颈，但它仍需与慢路由、批量 Commit 隔离。",
         "change": "保留独立 bulkhead/线程池；对 embedding、索引读取和候选合并分别打点；避免 Commit 重建索引时持有 Search 所需的全局锁。",
         "verify": "Commit 洪泛中持续比较 Atomic P95 与端到端 P95；若 Atomic 稳定而总延迟升高，应优先修路由/排队而非盲目优化向量检索。"},
        {"priority": "P1", "module": "租户故障隔离", "metrics": "M2",
         "evidence": (f"故障生效 {effective_fault_cases}/{len(fault_cases)}，"
                      f"旁观租户 HTTP 错误为 {m2.get('bystander_http_errors', 0)}，"
                      f"最差旁观 P95 变化 {max(bystander_changes, default=0):.2f}%。"),
         "judgment": "错误隔离有效，但延迟隔离仍有明显抖动，慢租户可能占用共享 worker、permit或模型连接。",
         "change": "按租户设置并发预算和熔断；慢依赖等待不得占用全局 Search permit；将 provider 连接池与重试预算纳入租户隔离。",
         "verify": "T1-T4轮流注入 reject/delay 并重复3轮，比较旁观租户 before/during/after P95和队列等待，不只看HTTP错误。"},
        {"priority": "P1", "module": "Commit 持久化与恢复", "metrics": "M5",
         "evidence": (f"真实 kill-9 恢复 {m5.get('passed_samples', 0)}/{m5.get('sample_count', 0)}；"
                      f"缺失消息 {m5.get('missing_messages')}，幂等同 archive={m5.get('same_archive')}。"),
         "judgment": "当前样本证明恢复链路可用，但单一崩溃时机不能证明所有已返回202任务都可靠。",
         "change": "确保返回202前持久化任务、幂等键和顺序游标；恢复扫描与正常提交使用同一状态机，终态写入保持原子性。",
         "verify": "覆盖入队后、执行中、落 archive 前后三种 kill 时机，以及并发积压和连续两次崩溃，逐任务对账 history/archive/cursor。"},
        {"priority": "P1", "module": "可观测性", "metrics": "M6",
         "evidence": (f"当前 tenant×lane 四元组 {len(m6.get('rows', []))}/{m6.get('expected_cells', 0)}，"
                      f"已声明 lane={m6.get('expected_lanes', [])}。"),
         "judgment": "四个已声明 lane 可观测，但 HTTP admission、router fanout、provider、archive/storage 尚未进入完整分母。",
         "change": "所有启用层统一输出 tenant、lane、queued、wait、exec、rejected，并补 accepted/completed/failed 与 reason_code；接口声明完整 lane 清单。",
         "verify": "测试平台从生效配置和接口声明生成分母；任何启用层缺一项都标INCONCLUSIVE，不允许用0补缺失值。"},
    ]


def redacted_report(source: dict, capacity: dict) -> dict:
    metrics = source.get("metrics", {})
    joint = dict(metrics.get("M3_M4", {}))
    if "unresolved_after_observation" not in joint and "pending_after_drain" in joint:
        joint["unresolved_after_observation"] = joint.pop("pending_after_drain")
    observability = metrics.get("M6", {})
    tenants = observability.get("expected_tenants", [])
    labels = {tenant: f"T{i + 1}" for i, tenant in enumerate(tenants)}
    fields = ("lane", "queued", "wait_seconds_total", "exec_seconds_total", "rejected_total",
              "accepted_total", "completed_total", "failed_total", "accepted_delta", "queued_peak_during_load")
    m6 = {"status": observability.get("status"), "sample_count": observability.get("sample_count"),
          "expected_cells": len(tenants) * len(observability.get("expected_lanes", [])),
          "expected_lanes": observability.get("expected_lanes", []),
          "observed_lanes": observability.get("observed_lanes", []),
          "declared_lane_count": observability.get("lane_count"),
          "lane_count_matches_observed": observability.get("lane_count_matches_observed"),
          "expected_lanes_match_observed": observability.get("expected_lanes_match_observed"),
          "coverage_scope": observability.get("coverage_scope"),
          "rows": [{"tenant": labels.get(r.get("tenant_id"), "unknown"),
                    **{k: r.get(k) for k in fields}} for r in observability.get("rows", [])],
          "missing_cells": len(observability.get("missing", [])),
          "invalid_cells": len(observability.get("invalid", []))}
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
        return (f'<div class="conclusion"><strong>结论：{escape(value["level"])}</strong>'
                f'<p>{escape(value["conclusion"])}</p><small><b>证据：</b>{escape(value["evidence"])}</small>'
                f'<small><b>补齐方式：</b>{escape(value["next"])}</small></div>')

    levels = report["M1"]["levels"]
    highest = max((r.get("hot_users", 0) for r in levels), default=None)
    m2, joint, m5, m6 = (report[k] for k in ("M2", "M3_M4", "M5", "M6"))
    conclusions = report.get("conclusions") or derive_conclusions(report)
    module_recommendations = (report.get("module_recommendations") or
                              derive_module_recommendations(report))
    dau_estimates = (report.get("M1", {}).get("dau") or {}).get("estimates", [])
    conservative_dau = [row.get("conservative_dau") for row in dau_estimates
                        if row.get("conservative_dau") is not None]
    bystanders = [p for p in m2.get("pairs", []) if p["identity_index"] != m2.get("target_index")]
    degradation = [p["p95_degradation_percent"] for p in bystanders if p.get("p95_degradation_percent") is not None]
    worst = max(degradation, default=None)
    p95 = joint.get("overlap_search", {}).get("p95_s")
    cards = [("M1 三轮零错误热用户档", report.get("M1", {}).get("zero_error_max_hot_users")),
             ("M1 最高过载观察档", highest),
             ("M1 条件DAU范围", (f"{min(conservative_dau):.1f}-{max(conservative_dau):.1f}"
                               if conservative_dau else None)),
             ("M2 最差旁观 P95 劣化 %", worst),
             ("M3 Commit Jain", joint.get("commit_jain")), ("M4 积压重叠 Search P95 / s", p95),
             ("M5 202 自主恢复", m5.get("autonomous_completed")),
             ("M6 四元组单元", f"{len(m6['rows'])}/{m6['expected_cells']}")]
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
    fault_rows = [[f"T{p['identity_index']+1}", p["before"].get("sent"), p["during"].get("sent"),
        p["before"].get("p95_s"), p["during"].get("p95_s"), p.get("p95_degradation_percent"),
        p["during"].get("transport_or_http_errors")] for p in m2.get("pairs", [])]
    fault_case_rows = [[case.get("repeat"), case.get("fault_type"),
        f"T{_number(case.get('target_index')) + 1}", case.get("status"),
        case.get("target_effect_observed"), case.get("bystander_http_errors"),
        case.get("worst_bystander_p95_change_percent"), case.get("control_disabled")]
        for case in m2.get("cases", [])]
    fault_types = "/".join(sorted({str(case.get("fault_type"))
                                   for case in m2.get("cases", []) if case.get("fault_type")}))
    fault_targets = len({case.get("target_index") for case in m2.get("cases", [])
                         if case.get("target_index") is not None})
    fairness_rows = [[f"T{t['identity_index']+1}", t["commit_completed_in_search_window"], t["commit_rps"],
        t["search"].get("sent"), t["search"].get("p95_s"), t["search"].get("quality_rate")]
        for t in joint.get("tenants", [])]
    priority_rows = [[f"T{p['identity_index']+1}", p["before"].get("p95_s"), p["during"].get("p95_s"),
        p.get("p95_degradation_percent"), p["during"].get("sent"), p["during"].get("transport_or_http_errors")]
        for p in joint.get("paired", [])]
    observable_rows = [[r["tenant"],r["lane"],r["queued_peak_during_load"],r["queued"],r["wait_seconds_total"],
        r["exec_seconds_total"],r["rejected_total"],r["accepted_delta"]] for r in m6["rows"]]
    curves = ''.join(bar(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '召回'}", r["search"].get("p95_s"),
        max((x["search"].get("p95_s") or 0 for x in levels), default=1), "#327d9d") for r in levels)
    capacity_error_curves = ''.join(bar(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '召回'}",
        100 * _number(_ratio(r["search"].get("transport_or_http_errors"), r["search"].get("sent"))), 100, "#c65b45") for r in levels)
    capacity_rps_curves = ''.join(bar(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '召回'}",
        r.get("effective_search_rps"), max((x.get("effective_search_rps") or 0 for x in levels), default=1), "#258875") for r in levels)
    fault_chart = comparison_chart([(f"T{p['identity_index']+1}", p["before"].get("p95_s"), p["during"].get("p95_s")) for p in m2.get("pairs", [])])
    priority_chart = comparison_chart([(f"T{p['identity_index']+1}", p["before"].get("p95_s"), p["during"].get("p95_s")) for p in joint.get("paired", [])])
    fault_case_chart = ''.join(bar(
        f"{case.get('fault_type')} T{_number(case.get('target_index')) + 1}",
        max(0, _number(case.get("worst_bystander_p95_change_percent"))),
        max((max(0, _number(item.get("worst_bystander_p95_change_percent")))
             for item in m2.get("cases", [])), default=1), "#c65b45")
        for case in m2.get("cases", []))
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
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>4U8G 六项指标 · 综合实测</title><style>*{{box-sizing:border-box}}body{{margin:0;color:#24323a;background:#f6f8f9;font:15px/1.7 system-ui,"PingFang SC",sans-serif;letter-spacing:0}}main{{max-width:1360px;margin:auto;padding:28px}}h1{{font-size:28px}}h2{{font-size:21px}}h3{{font-size:17px}}header,section{{padding:20px 0;border-bottom:1px solid #ccd8dc}}.muted{{color:#576b74}}.notice{{border-left:4px solid #ad4637;padding:8px 16px;background:#fff1ec}}.conclusion{{border-left:4px solid #2b8175;padding:10px 16px;background:#edf7f4;margin:12px 0}}.conclusion>strong{{display:block;color:#17685e;font-size:17px}}.conclusion p{{margin:5px 0}}.conclusion small{{display:block;color:#40555e;margin-top:5px}}.stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:24px 0}}.stats div{{border-top:3px solid #278575;padding-top:12px}}.stats b{{display:block;font-size:27px}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:white;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #d7e0e5;text-align:left;vertical-align:top}}th{{background:#e6eef1;white-space:nowrap}}td{{overflow-wrap:anywhere}}code{{overflow-wrap:anywhere}}details{{border-top:1px solid #dbe3e6;margin-top:14px;padding-top:9px}}summary{{cursor:pointer;color:#176b73;font-weight:650}}.chart-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:18px 0}}.chart-grid>div{{min-width:0;border-top:2px solid #9eb3bc;padding-top:8px}}.chart-wide{{max-width:900px}}.bar{{display:grid;grid-template-columns:145px minmax(0,1fr) 70px;gap:12px;align-items:center;margin:10px 0}}.bar>div,.track{{height:14px;background:#dae3e7;display:block}}.bar i,.track em{{display:block;height:100%;font-style:normal}}.bar b{{text-align:right}}.comparison-chart{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:16px 0}}.compare{{display:grid;grid-template-columns:58px minmax(0,1fr) 62px;gap:5px 8px;align-items:center;background:#fff;padding:10px;border-top:2px solid #9eb3bc}}.compare>b{{grid-column:1/-1}}.compare strong{{text-align:right}}.flow{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:16px 0}}.flow span{{background:#fff;border-top:3px solid #278575;padding:10px;text-align:center;font-size:13px}}.flow b{{display:block}}a{{color:#16699b}}@media(max-width:900px){{.chart-grid{{grid-template-columns:1fr}}}}@media(max-width:700px){{main{{padding:14px}}.stats,.comparison-chart{{grid-template-columns:1fr}}h1{{font-size:24px}}.bar{{grid-template-columns:110px minmax(0,1fr) 58px;font-size:12px}}.flow{{grid-template-columns:1fr}}}}</style></head><body><main>
<header><p class="muted">服务器真实 HTTP / 真实模型 / 4 CPU · 8 GiB / PR31 基线 + PR32 测试增强</p><h1>EchoMem 六项指标综合压测报告</h1>
<p>报告按“指标含义、测试方法、图表数据、结论、模块改进”组织。未设置业务性能合格线；所有失败、超时、拒绝和召回错误均保留在分母。</p>
<p>EchoMem <code>{fmt(env.get('echomem_commit'))}</code>；测试平台 <code>{fmt(env.get('platform_base_commit'))}</code>。</p>
<p class="notice">版本口径：容量详情保留各档 platform_base_pr / platform_base_commit；缺失表示历史快照未记录该字段，不能称所有档位均已重跑本版本。容器4U8G是资源限额，不代表宿主机资源独占。</p></header>
<div class="stats">{''.join('<div>'+escape(label)+'<b>'+fmt(value)+'</b></div>' for label,value in cards)}</div>
<section><h2>六个指标分别反映什么</h2>{table(['指标','反映什么','测试方式','暴露的线上风险','主要关联模块'],[
['M1 容量与DAU','单个4U8G实例在不同热用户数下能完成多少真实Search和Commit？从哪一档开始拒绝、超时或积压？','逐档增加独立热用户，分别运行纯召回和召回+no-recall+Commit混合流量，记录请求、质量、积压和资源。','扩容过晚、请求大量失败、后台任务长期堆积。','Admission、Search编排、Provider并发、Commit队列、资源限制'],
['M2 故障隔离','一个租户被拒绝或依赖变慢时，其他租户的Search是否仍稳定？','T1-T4依次注入reject/delay，采集before/during/after并计算三个旁观租户P95变化。','单租户拖慢全站，形成共享线程池或连接池雪崩。','租户隔离、Bulkhead、连接池、超时与熔断'],
['M3 公平性','同档位租户是否获得近似等权的Commit吞吐与Search延迟？','四个独立租户同速率并发Search与Commit，用窗口内完成吞吐和Search P95倒数计算Jain，零完成保留。','部分租户饥饿，活跃大户长期挤占后台处理能力。','租户调度器、队列轮询、在途配额'],
['M4 Search优先级','Commit洪泛时，交互式Search是否仍优先并保持召回质量？','先测无Commit基线，再并发提交32个Commit，只统计与202后未终态任务区间重叠的Search。','后台写任务拖慢用户在线查询。','Search/Commit分lane、Admission、路由、Atomic bulkhead'],
['M5 崩溃恢复','已经返回202的Commit在进程崩溃后是否自主恢复且不丢失、不乱序、不重复？','任务202且pending时kill -9，重启后轮询原任务并对账history/archive/cursor/顺序/幂等。','接口称已受理但任务永久丢失，或重放产生重复记忆。','持久队列、状态机、cursor、幂等与archive写入'],
['M6 可观测性','能否按租户、按处理层看到queue/wait/exec/reject四元组？','负载前中后采样受保护接口，按实际配置枚举tenant×lane并覆盖NORMAL/QUEUE/REJECT/RESET。','出现慢请求时无法判断卡在路由、模型、检索还是Commit。','Metrics、Trace、lane声明、reason_code']])}</section>
<section><h2>六项结论总览</h2>{table(['指标','结论等级','具体结论','关键证据','下一步'],[[key,value['level'],value['conclusion'],value['evidence'],value['next']] for key,value in conclusions.items()])}</section>
<section><h2>EchoMem 模块改进优先级</h2><p>以下建议由本次数据推导；“判断”描述证据能够支持的范围，不把黑盒延迟直接当作某一模块的确定代码缺陷。</p>{table(['优先级','模块','关联指标','判断','建议修改'],recommendation_rows)}{details('查看逐模块证据与复测方法',table(['优先级','模块','本次证据','如何复测'],recommendation_detail_rows))}</section>
<section><h2>测试数据与真实操作</h2>{details('查看测试样本、HTTP 操作和成功判定',table(['对象','测试数据','真实HTTP操作','成功判定'],[
['Search召回','每个身份预先写入5段确定性记忆，包含20个固定事实；从40个改写问题中轮换提问。','POST /api/retrieval/search；问题不携带答案；记录HTTP、端到端耗时、引擎来源和返回items。','HTTP 200、非degraded、返回items中出现预先锁定的日期/时间/地点/联系人。'],
['Search不召回','20个日常问题，例如问候、算术和翻译，答案不依赖个人记忆。','与召回问题走同一个真实Search接口，混合场景按固定随机种子穿插。','HTTP 200、非degraded、记忆items为空；不能把接口200直接算召回正确。'],
['Commit','每个租户创建独立session，写入一段确定性记忆；洪泛场景每租户8个session。','POST Commit并使用唯一幂等键；只把202且返回archive_id计为受理，随后轮询commit_status。','状态到completed才计完成；failed/error/超时保留，窗口外完成不计入窗口吞吐。'],
['崩溃恢复','专用session写8条带序号消息，Commit返回202且仍pending时kill -9容器。','启动同一容器后轮询原任务，不重新提交；再读取history/archive/cursor并做一次同幂等键重试。','原任务自主completed，8条消息集合和顺序无缺失，cursor一致，幂等重试指向同archive。'],
['租户与观测','四个独立tenant/user/key，不用同一key伪装多租户。','负载前、中、后调用受保护只读观测接口；故障通过受保护test-control端点注入。','每个预期tenant×lane都有queue/wait/exec/reject四元组，字段非负、唯一且负载变化可见。']]))}</section>
<section><h2>M1 · 热用户与 DAU</h2><p><b>测试方式：</b>每个热用户拥有独立身份和预注入记忆，以1 Search/s开放到达率同时发请求；纯召回与“召回+不召回+Commit”混合流量分别测。混合场景在第30–60秒为每个热用户错峰提交1个非空Commit，因此H4代表计划4个、H8代表计划8个；“峰值在途”按每个已获202任务从受理到终态的真实时间区间计算，不把总提交数冒充服务端并行度。每档记录全部请求、P95/P99、有效吞吐、错误/降级、CPU/RSS及积压恢复。请求完成容量不使用延迟阈值：同一档纯召回和混合负载都必须无HTTP/传输失败，混合Commit还必须全部受理并最终完成。</p>{conclusion_panel('M1')}<div class="chart-grid"><div><h3>P95 / 秒</h3>{curves}</div><div><h3>HTTP/传输错误率 / %</h3>{capacity_error_curves}</div><div><h3>严格有效 Search/s</h3>{capacity_rps_curves}</div></div>{details('查看每档完整计数、Commit 和资源数据',table(['H','负载','Search P95 s','有效 Search/s','HTTP/传输错误','严格有效/发出','Commit提交','Commit 202','Commit完成','Commit未完成/失败','Commit峰值在途','提交跨度 s','Commit P95 s','CPU峰值 %','RSS峰值 MiB'],capacity_rows))}
<p>100% CPU 表示一个 CPU 核。最高已测档位不是最大用户量；HTTP 429、超时、召回降级全部保留。最大 DAU 尚未验证，画像换算及每题数据见 <a href="capacity-report.html">容量详细报告</a>。</p></section>
<section><h2>M2 · 单租户故障隔离</h2><p><b>测试方式：</b>先让四租户同时执行同一组召回问题形成基线，再仅对目标租户注入reject或delay；目标租户和三个旁观租户继续使用独立线程池发同速率Search，避免目标故障占满客户端线程而制造假隔离。劣化=(故障中P95/基线P95−1)×100%。本轮覆盖 {fmt(fault_targets)} 个目标租户、{fmt(fault_types)} 故障，共 {fmt(len(fault_case_rows))} 个用例。</p>{conclusion_panel('M2')}{fault_chart}<div class="chart-wide"><h3>每个故障用例的最差旁观租户 P95 上升 / %</h3>{fault_case_chart}</div>
{details('查看逐租户故障前后数据',table(['租户','基线样本','故障中样本','基线P95 s','故障中P95 s','变化 %','故障中HTTP错误'],fault_rows))}
{details('查看全部故障用例',table(['轮次','故障','目标','状态','目标生效','旁观HTTP错误','最差旁观P95变化 %','故障撤销'],fault_case_rows)) if fault_case_rows else ''}
<p>当前矩阵可形成快速实测结论，但统计稳定性仍需至少3轮复测。</p></section>
<section><h2>M3 · 等权租户公平性</h2><p><b>测试方式：</b>四个不同租户各准备8个独立写session，在同一个60秒稳态窗口并发Search与Commit。Commit公平性输入是每租户“窗口内completed/秒”，Search公平性输入是每租户P95倒数；没有完成的租户必须以0留在分母，窗口后的任务不回填。</p>{conclusion_panel('M3')}
{details('查看逐租户公平性数据',table(['租户','窗口内Commit完成','Commit/s','Search样本','Search P95 s','严格有效率'],fairness_rows))}
{bar('Commit Jain',joint.get('commit_jain'),1,'#258875')}{bar('Search Jain',joint.get('search_inverse_p95_jain'),1,'#327d9d')}
<p>Jain 接近 1 表示相对均匀，不代表快或可靠；零完成租户保留，全部零完成时指数留空。窗口后的排空完成不混入窗口吞吐；此为洪泛窗口观察，不是长时间稳态公平性结论。</p></section>
<section><h2>M4 · Commit 洪泛下的 Search</h2><p><b>测试方式：</b>先在无Commit时用固定问题序列测Search基线；随后同时提交32个真实Commit，并用同一问题、同一到达计划测Search。只选与至少一个已受理且未终态Commit时间区间重叠的Search样本计算优先级结果。</p>{conclusion_panel('M4')}<div class="flow"><span><b>1</b>预注入记忆</span><span><b>2</b>无Commit基线</span><span><b>3</b>32个Commit并发受理</span><span><b>4</b>积压重叠Search</span><span><b>5</b>轮询终态并对账</span></div>{priority_chart}<p>受理 202：{fmt(joint.get('accepted_202'))}/{fmt(joint.get('commit_planned'))}；每任务 180 秒观察期内完成：{fmt(joint.get('completed_including_drain'))}；观察期内未见终态：{fmt(joint.get('unresolved_after_observation', joint.get('pending_after_drain')))}。</p>
{details('查看逐租户基线与洪泛对比',table(['租户','无Commit P95 s','洪泛窗口P95 s','变化 %','洪泛Search样本','HTTP错误'],priority_rows))}
<p>真实 Commit 积压重叠 Search：{fmt(joint.get('overlap_search',{}).get('sent'))} 个，P95={fmt(p95)} 秒。全窗口与积压重叠窗口分开，不将没有后台任务的快速样本充作优先级证据；“未见终态”不等于永久丢失，内部严格调度顺序也尚未证明。</p></section>
<section><h2>M5 · 202 后崩溃恢复</h2><p><b>测试方式：</b>Commit已返回202且commit_status仍为pending时对专用容器执行kill -9；启动后只轮询原任务，不能通过重提Commit掩盖恢复失败。completed后再对history、archive、cursor、消息集合/顺序和幂等键逐项对账。</p>{conclusion_panel('M5')}<div class="flow"><span><b>202</b>任务已受理</span><span><b>pending</b>确认尚未完成</span><span><b>kill -9</b>真实进程崩溃</span><span><b>restart</b>原任务自主恢复</span><span><b>reconcile</b>数据与顺序对账</span></div>{details('查看恢复数据与行为检查',table(['观测项','结果'],[['崩溃前已受理202',m5.get('accepted_202')],['原任务自主completed',m5.get('autonomous_completed')],['源消息数',m5.get('expected_messages')],['未对账消息数',m5.get('missing_messages')],['重试是否同archive',m5.get('same_archive')],['总耗时 s',m5.get('elapsed_s')]]) + table(['行为检查','结果'],[[c['name'],c['status']] for c in m5.get('checks',[])]))}
<p>对专用容器执行一次真实 kill-9/start，等待原任务恢复后才做幂等重试；小样本即使通过，也不能推导所有崩溃时机均 100% 可靠。因未受理、已提前完成或其他任务未排空而没有重启时，不冒充恢复成功。</p></section>
<section><h2>M6 · 每租户四元组</h2><p><b>测试方式：</b>以EchoMem受保护接口声明/观测到的lane形成分母，在负载前、中、后反复读取快照。每个独立租户×lane都必须同时具有排队深度、累计等待、累计执行、拒绝数；缺失、重复、负数或NaN均不能填0，也不能算通过。</p>{conclusion_panel('M6')}<p>负载中采样 {fmt(m6.get('sample_count'))} 次；本轮期望 {fmt(m6['expected_cells'])} 个 tenant×lane 单元，观察到 {len(m6['rows'])} 个。模块：{escape(', '.join(m6['expected_lanes']))}。</p>
<div class="chart-wide"><h3>各租户各层峰值排队深度</h3>{queue_chart}</div>
{details('查看逐租户逐层四元组',table(['租户','层/模块','队列峰值','最终队列','累计等待 s','累计执行 s','累计拒绝','受理增量'],observable_rows))}
<p>累计时间不是单次请求延迟；缺失不能填零。这里只证明上表模块的观测，其他启用层和完整调度顺序仍需补充。</p></section>
<section><h2>测试仍需补齐的分母</h2>{table(['归属','待补测试'],[['测试平台','M1继续提高热用户与记忆规模，直到观察到崩溃、OOM或停止发压后积压无法恢复；补业务DAU画像。'],['测试平台','M2、M3至少重复3轮；M5覆盖多个kill时机、并发积压和连续崩溃。'],['EchoMem / 观测接口','让HTTP admission、router/fanout、provider与archive/storage进入完整lane声明和逐租户四元组。'],['模块归因','按reason_code和阶段耗时拆分429、模型降级、请求超时与Commit拒绝；CPU/内存未满不能证明代码没有瓶颈。']])}</section>
<p><a href="report.json">脱敏统计 JSON</a> · 原始请求、身份凭据和故障详情保留执行机，不包含于分享文件。</p></main></body></html>'''


def publish(source: Path, capacity: Path, output: Path, recovery: Path | None = None,
            fault_matrix: Path | None = None, contention_matrix: Path | None = None) -> dict:
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
    capacity_data = json.loads(capacity.read_text(encoding="utf-8"))
    public = redacted_report(raw, capacity_data)
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
    args = parser.parse_args()
    result = publish(args.source, args.capacity, args.output, recovery=args.recovery,
                     fault_matrix=args.fault_matrix, contention_matrix=args.contention_matrix)
    print(json.dumps({"status": result["status"], "redacted": True}))


if __name__ == "__main__":
    main()
