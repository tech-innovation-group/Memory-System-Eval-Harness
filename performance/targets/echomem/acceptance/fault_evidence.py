"""Evidence contracts for controlled faults, separate from latency SLOs."""

from __future__ import annotations

import json

from performance.targets.echomem.acceptance.load_evidence import count, number


def control_receipt(reply: dict, tenant: str, fault_type: str) -> dict:
    """Keep only public control facts, never the response body or tenant identity."""
    try:
        body = json.loads(reply.get("body", ""))
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    fault = body.get("fault")
    valid = reply.get("status") == "PASS" and body.get("enabled", body.get("status") == "enabled") is True
    active = bool(valid and isinstance(fault, dict) and fault.get("tenant_id") == tenant
                  and fault.get("fault_type") == fault_type and fault.get("active") is True
                  and (number(fault.get("remaining_s")) or 0) > 0)
    cleared = bool(valid and "fault" in body and fault is None)
    return {"http_status": reply.get("status_code"), "active_match": active,
            "cleared": cleared, "remaining_s": number(fault.get("remaining_s")) if active else None,
            "delay_ms": number(fault.get("delay_ms")) if active else None}


def public_control_evidence(value) -> dict:
    value = value if isinstance(value, dict) else {}
    result = {key: value.get(key) is True for key in ("window_covered",)}
    for key in ("elapsed_since_enable_s", "duration_s"):
        result[key] = number(value.get(key))
    for key in ("enabled", "at_end", "disabled"):
        receipt = value.get(key)
        receipt = receipt if isinstance(receipt, dict) else {}
        result[key] = {field: receipt.get(field) is True for field in ("active_match", "cleared")}
        result[key].update({field: number(receipt.get(field)) for field in ("http_status", "remaining_s", "delay_ms")})
    return result


def _summary(value):
    sent, success, errors = (count(value.get(k)) for k in ("sent", "success", "transport_or_http_errors"))
    p95 = number(value.get("p95_s"))
    not_sent = count(value.get("not_sent"))
    valid = bool(sent and success is not None and errors is not None and success + errors <= sent
                 and p95 is not None and p95 > 0)
    return {"sent": sent, "success": success, "errors": errors, "p95_s": p95,
            "not_sent": not_sent, "valid": valid, "strict": bool(valid and success == sent)}


def _pairs(rows, expected):
    grouped = {i: [] for i in expected}
    unexpected = 0
    for row in rows or []:
        i = row.get("identity_index")
        if type(i) is int and i in grouped:
            grouped[i].append(row)
        else:
            unexpected += 1
    return {i: found[0] if len(found) == 1 else {} for i, found in grouped.items()}, unexpected


def case_counts(case: dict, *, delay_ms=None) -> dict:
    # fault_matrix.run is intentionally a fixed four-tenant matrix.
    expected = [0, 1, 2, 3]
    pairs, unexpected = _pairs(case.get("pairs", []), expected)
    recovery, unexpected_recovery = _pairs(case.get("recovery_pairs", []), expected)
    target = case.get("target_index")
    target_valid = type(target) is int and target in expected
    mode = case.get("fault_type")
    rows = []
    for i in expected:
        before = _summary(pairs[i].get("before", {}))
        during = _summary(pairs[i].get("during", {}))
        after = _summary(recovery[i].get("during", {}))
        paired = before["valid"] and during["valid"]
        recovered = before["valid"] and after["valid"]
        rows.append({"identity_index": i, "target": i == target if target_valid else False,
                     "before": before, "during": during, "after": after,
                     "pair_valid": paired, "recovery_valid": recovered,
                     "p95_degradation_percent": (during["p95_s"] / before["p95_s"] - 1) * 100 if paired else None,
                     "recovery_p95_degradation_percent": (after["p95_s"] / before["p95_s"] - 1) * 100 if recovered else None})
    target_row = rows[target] if target_valid else None
    reason_counts = pairs[target].get("during", {}).get("http_reason_counts", {}) if target_valid else {}
    injected = count(reason_counts.get("TEST_FAULT_INJECTED", 0))
    effect = False
    if target_row and target_row["pair_valid"] and target_row["before"]["strict"]:
        if mode == "reject":
            effect = bool(injected and injected <= target_row["during"]["errors"])
        elif mode == "delay" and number(delay_ms):
            effect = target_row["during"]["p95_s"] - target_row["before"]["p95_s"] >= max(.05, delay_ms / 2000)
    receipts = public_control_evidence(case.get("control_evidence"))
    start, end, cleared = (receipts.get(k, {}) for k in ("enabled", "at_end", "disabled"))
    control = bool(start.get("active_match") is True and end.get("active_match") is True
                   and cleared.get("cleared") is True and receipts.get("window_covered") is True)
    if mode == "delay":
        control = control and number(delay_ms) is not None and start.get("delay_ms") == end.get("delay_ms") == delay_ms
    reasons = []
    if not target_valid or mode not in {"reject", "delay"}:
        reasons.append("目标或故障类型不符合四租户合同")
    if not control:
        reasons.append("缺少匹配目标的生效/窗口覆盖/撤销回执")
    if not effect:
        reasons.append("未证实目标故障效应：reject需明确原因码，delay需配置匹配及延迟增量")
    if unexpected or not all(r["pair_valid"] and r["before"]["strict"] for r in rows):
        reasons.append("基线或故障中租户配对不完整，或基线召回不符")
    if unexpected_recovery or not all(r["recovery_valid"] for r in rows):
        reasons.append("撤销后的恢复窗口样本不完整")
    if any(r[phase]["not_sent"] != 0 for r in rows for phase in ("before", "during", "after")):
        reasons.append("发压器漏发或缺少未发送计数，无法确认同速率负载")
    bystanders = [r for r in rows if not r["target"]] if target_valid else []
    known_errors = sum(r["during"]["errors"] for r in bystanders if r["during"]["errors"] is not None)
    full_errors = bool(len(bystanders) == 3 and not unexpected and all(r["during"]["errors"] is not None for r in bystanders))
    deltas = [r["p95_degradation_percent"] for r in bystanders if r["p95_degradation_percent"] is not None]
    return {"status": "MEASURED" if not reasons else "INCONCLUSIVE", "repeat": case.get("repeat"),
            "target_index": target if target_valid else None, "fault_type": mode if mode in {"reject", "delay"} else None,
            "target_effect_observed": effect, "injected_rejections": injected, "control_verified": control,
            "incomplete_reasons": reasons, "rows": rows, "known_bystander_http_errors": known_errors,
            "bystander_http_errors": known_errors if full_errors else None,
            "bystander_pairs": sum(r["pair_valid"] for r in bystanders),
            "recovery_pairs": sum(r["recovery_valid"] for r in rows),
            "worst_bystander_p95_change_percent": max(deltas, default=None)}


def matrix_counts(matrix: dict) -> dict:
    repeats = count(matrix.get("repeats"))
    cases = [case_counts(c, delay_ms=matrix.get("delay_ms")) for c in matrix.get("cases", [])]
    expected = repeats * 8 if repeats else None
    keys = [(c["repeat"], c["fault_type"], c["target_index"]) for c in cases]
    planned = {(r, mode, t) for r in range(1, (repeats or 0) + 1) for mode in ("reject", "delay") for t in range(4)}
    valid_keys = [k for k in keys if type(k[0]) is int and k in planned]
    unique = set(valid_keys)
    complete = bool(expected and matrix.get("expected_cases") == expected and len(keys) == len(unique) == expected)
    measured = sum(c["status"] == "MEASURED" and keys[i] in unique and keys.count(keys[i]) == 1 for i, c in enumerate(cases))
    errors = [c["bystander_http_errors"] for c in cases]
    return {"status": "MEASURED" if complete and measured == expected else "INCONCLUSIVE",
            "expected_cases": expected, "recorded_cases": len(cases), "measured_cases": measured,
            "unexecuted_cases": len(planned - unique) if expected else None,
            "duplicate_cases": len(valid_keys) - len(unique), "unexpected_cases": len(keys) - len(valid_keys),
            "cases": cases, "known_bystander_http_errors": sum(c["known_bystander_http_errors"] for c in cases),
            "bystander_http_errors": sum(errors) if complete and all(e is not None for e in errors) else None,
            "worst_bystander_p95_change_percent": max((c["worst_bystander_p95_change_percent"] for c in cases
                                                       if c["worst_bystander_p95_change_percent"] is not None), default=None)}
