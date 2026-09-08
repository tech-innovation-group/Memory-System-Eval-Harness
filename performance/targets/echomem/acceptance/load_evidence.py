"""Recompute load observations without converting missing tenants into successes."""

from __future__ import annotations

import math

from performance.targets.echomem.acceptance.six_metrics import jain


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def count(value):
    value = number(value)
    return int(value) if value is not None and value == int(value) else None


def _contract(joint):
    # Historical main_metric_samples required four independently seeded tenants.
    expected = joint.get("expected_identity_indices", [0, 1, 2, 3])
    valid = (isinstance(expected, list) and len(expected) >= 2
             and all(type(i) is int and i >= 0 for i in expected)
             and len(set(expected)) == len(expected))
    return expected if valid else []


def _indexed(rows, expected):
    grouped = {i: [] for i in expected}
    unexpected = 0
    for row in rows:
        index = row.get("identity_index") if isinstance(row, dict) else None
        if type(index) is int and index in grouped:
            grouped[index].append(row)
        else:
            unexpected += 1
    return grouped, unexpected


def _search(summary):
    summary = summary if isinstance(summary, dict) else {}
    sent, success, errors = (count(summary.get(k)) for k in
                             ("sent", "success", "transport_or_http_errors"))
    p95 = number(summary.get("p95_s"))
    valid = bool(sent and success is not None and errors is not None
                 and success + errors <= sent and p95 is not None and p95 > 0)
    return valid, sent, success, errors, p95


def fairness_counts(joint: dict) -> dict:
    expected = _contract(joint)
    grouped, unexpected = _indexed(joint.get("tenants", []), expected)
    window = number(joint.get("search_window_s"))
    rows = []
    for index, candidates in grouped.items():
        row = candidates[0] if len(candidates) == 1 else {}
        completed = count(row.get("commit_completed_in_search_window"))
        valid_search, sent, success, errors, p95 = _search(row.get("search", {}))
        rows.append({"identity_index": index, "completed": completed,
                     "commit_rps": completed / window if completed is not None and window else None,
                     "search_p95_s": p95 if valid_search else None,
                     "search_sent": sent, "search_success": success, "search_http_errors": errors,
                     "unique": len(candidates) == 1})
    complete_commit = bool(expected and not unexpected and window
                           and all(r["unique"] and r["completed"] is not None for r in rows))
    complete_search = bool(expected and not unexpected
                           and all(r["unique"] and r["search_p95_s"] is not None for r in rows))
    commit_jain = jain([r["commit_rps"] for r in rows]) if complete_commit else None
    search_jain = jain([1 / r["search_p95_s"] for r in rows]) if complete_search else None
    return {"status": "MEASURED" if commit_jain is not None and search_jain is not None else "INCONCLUSIVE",
            "expected_tenants": len(expected), "rows": rows, "unexpected_rows": unexpected, "window_s": window,
            "commit_tenants": sum(r["unique"] and r["completed"] is not None for r in rows),
            "search_tenants": sum(r["unique"] and r["search_p95_s"] is not None for r in rows),
            "commit_jain": commit_jain, "search_inverse_p95_jain": search_jain,
            "all_commit_zero": complete_commit and all(r["completed"] == 0 for r in rows),
            "equal_completions": complete_commit and len({r["completed"] for r in rows}) == 1}


def priority_counts(joint: dict) -> dict:
    expected = _contract(joint)
    grouped, unexpected = _indexed(joint.get("paired", []), expected)
    pairs = []
    for index, candidates in grouped.items():
        row = candidates[0] if len(candidates) == 1 else {}
        before, during = _search(row.get("before", {})), _search(row.get("during", {}))
        valid = before[0] and during[0]
        pairs.append({"identity_index": index, "valid": valid,
                      "baseline_strict_valid": bool(before[0] and before[1] == before[2]),
                      "before_p95_s": before[4], "during_p95_s": during[4],
                      "during_sent": during[1], "during_http_errors": during[3],
                      "p95_degradation_percent": (during[4] / before[4] - 1) * 100 if valid else None})
    valid, sent, success, errors, p95 = _search(joint.get("overlap_search", {}))
    planned, accepted = count(joint.get("commit_planned")), count(joint.get("accepted_202"))
    minimum = count(joint.get("minimum_flood_commits", 32))
    flood = bool(minimum and planned is not None and accepted is not None and planned >= accepted >= minimum)
    complete = bool(expected and not unexpected and flood and valid
                    and all(p["valid"] and p["baseline_strict_valid"] for p in pairs))
    reasons = []
    if not flood:
        reasons.append("实际202受理未达到锁定洪泛数量，或计数缺失/矛盾")
    if not valid:
        reasons.append("积压重叠Search样本或计时不完整")
    if not expected or unexpected or not all(p["valid"] for p in pairs):
        reasons.append("预期租户配对缺失、重复或计时不完整")
    if any(not p["baseline_strict_valid"] for p in pairs):
        reasons.append("基线未证明全部严格召回有效")
    return {"status": "MEASURED" if complete else "INCONCLUSIVE",
            "expected_tenants": len(expected), "valid_pairs": sum(p["valid"] for p in pairs),
            "pairs": pairs, "flood_observed": flood, "minimum_flood_commits": minimum,
            "commit_planned": planned, "accepted_202": accepted, "incomplete_reasons": reasons,
            "sent": sent, "success": success, "transport_or_http_errors": errors, "p95_s": p95,
            "all_overlap_strict_valid": bool(valid and sent == success),
            "strict_server_scheduling_proven": False}


def load_counts(joint: dict) -> dict:
    samples = joint.get("repeat_summaries")
    samples = samples if isinstance(samples, list) else [joint]
    expected = count(joint.get("expected_repeats", len(samples)))
    rows = [{"repeat": sample.get("repeat", i + 1),
             "fairness": fairness_counts(sample), "priority": priority_counts(sample)}
            for i, sample in enumerate(samples)]
    complete = bool(expected and len(rows) == expected
                    and len({str(row["repeat"]) for row in rows}) == len(rows))
    return {"expected_repeats": expected, "observed_repeats": len(rows), "rows": rows,
            "fairness_status": "MEASURED" if complete and all(r["fairness"]["status"] == "MEASURED" for r in rows) else "INCONCLUSIVE",
            "priority_status": "MEASURED" if complete and all(r["priority"]["status"] == "MEASURED" for r in rows) else "INCONCLUSIVE"}


def load_conclusions(evidence: dict) -> dict:
    def show(value):
        return "未采集/未定义" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)

    fair = [r["fairness"] for r in evidence["rows"]]
    priorities = [r["priority"] for r in evidence["rows"]]
    fair_complete = evidence["fairness_status"] == "MEASURED"
    all_zero = bool(fair and all(f["all_commit_zero"] for f in fair))
    equal = bool(fair and all(f["equal_completions"] for f in fair))
    priority_complete = evidence["priority_status"] == "MEASURED"
    all_valid = bool(priorities and all(p["all_overlap_strict_valid"] for p in priorities))
    return {
        "M3": {
            "status": evidence["fairness_status"],
            "level": ("Commit均未完成，公平性未定义" if all_zero else "公平性证据不完整" if not fair_complete else
                      "各轮Commit完成数均匀" if equal else "观察到租户完成分布不均"),
            "conclusion": ("所有已测轮次的窗口内Commit完成数均为0，Jain分母为0，不代表公平或不公平。" if all_zero else
                           "至少一轮缺少租户计数、有效Search样本或观察窗口，不能依靠已保存的Jain汇总值判断公平性。" if not fair_complete else
                           "按每轮完整租户分母重新计算Commit吞吐和Search逆P95的Jain，不合并各轮完成数。"
                           + ("本次各轮租户Commit完成数相等。" if equal else "本次至少一轮租户Commit完成数不相等。"))
                          + "Jain只反映相对均匀程度，不代表快、可靠或已通过性能要求；短洪泛窗口不等于长期稳态。",
            "evidence": f"轮次 {evidence['observed_repeats']}/{show(evidence['expected_repeats'])}；" + "；".join(
                f"轮{r['repeat']}：Commit覆盖 {f['commit_tenants']}/{f['expected_tenants']}，"
                f"Search覆盖 {f['search_tenants']}/{f['expected_tenants']}，"
                f"完成数={[row['completed'] for row in f['rows']]}，"
                f"Commit Jain={show(f['commit_jain'])}，Search逆P95 Jain={show(f['search_inverse_p95_jain'])}"
                for r, f in zip(evidence["rows"], fair)),
            "next": "补齐各轮所有预期租户的同窗数据；零完成保留为0，未采集保留为空，再重复等权稳态负载。",
        },
        "M4": {
            "status": evidence["priority_status"],
            "level": ("优先级证据不完整；严格优先仍未证明" if not priority_complete else
                      "重叠Search全部严格有效；严格优先仍未证明" if all_valid else
                      "重叠Search出现错误或召回不符；严格优先仍未证明"),
            "conclusion": ("至少一轮缺少有效基线配对、足量真实202受理或积压重叠Search，不能写为Search完成正常。" if not priority_complete else
                           "每轮分别核验无Commit基线、真实202受理量和Commit在途期间的Search。"
                           + ("已观测的重叠Search均严格有效。" if all_valid else "重叠请求中存在HTTP/传输错误或严格召回不符，所有请求均保留在分母。"))
                          + "P95变化仅描述本次负载影响，端到端时延不能证明内部严格先调度Search；不人为设置性能通过阈值。",
            "evidence": f"轮次 {evidence['observed_repeats']}/{show(evidence['expected_repeats'])}；" + "；".join(
                f"轮{r['repeat']}：配对 {p['valid_pairs']}/{p['expected_tenants']}，"
                f"Commit受理 {show(p['accepted_202'])}/{show(p['commit_planned'])}，最低洪泛受理 {show(p['minimum_flood_commits'])}，"
                f"重叠严格有效 {show(p['success'])}/{show(p['sent'])}，HTTP/传输错误 {show(p['transport_or_http_errors'])}，"
                f"P95={show(p['p95_s'])}s，逐租户P95变化(%)={','.join(show(pair['p95_degradation_percent']) for pair in p['pairs'])}；"
                f"不足依据={'、'.join(p['incomplete_reasons']) or '无'}"
                for r, p in zip(evidence["rows"], priorities)),
            "next": "补齐每轮预热基线、锁定的洪泛受理量及重叠样本；内部严格优先另需服务端调度序列证据。",
        },
    }
