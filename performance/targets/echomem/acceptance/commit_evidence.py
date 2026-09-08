"""Allowlisted Commit outcomes; response bodies and identities never leave the runner."""

from collections import Counter

from performance.targets.echomem.acceptance.load_evidence import count, number
from performance.targets.echomem.probes._client import PUBLIC_HTTP_ERROR_CODES


PUBLIC_REASONS = PUBLIC_HTTP_ERROR_CODES | frozenset({
    "OVERLOADED", "RATE_LIMITED", "ADMISSION_REJECTED", "TOO_MANY_REQUESTS",
})
STATES = frozenset({"pending", "queued", "running", "processing", "in_progress",
                    "awaiting_engines", "completed", "failed", "error"})


def receipt(result) -> dict:
    payload = result.payload if isinstance(result.payload, dict) else {}
    candidates = [getattr(result, "reason_code", ""), payload.get("error")]
    for obj in (payload, payload.get("error"), payload.get("detail")):
        if isinstance(obj, dict):
            candidates.extend(obj.get(k) for k in ("reason_code", "error_code", "code"))
    supplied = [v for v in candidates if isinstance(v, str) and v]
    known = next((v for v in supplied if v in PUBLIC_REASONS), None)
    status = count(result.status_code)
    return {"http_status": status if status is not None and 100 <= status <= 599 else None,
            "reason_code": known or ("UNRECOGNIZED" if supplied else "NOT_RECORDED"),
            "reason_code_present": bool(supplied),
            "retry_after_s": number(getattr(result, "retry_after_s", None)),
            "response_elapsed_s": number(result.elapsed_s)}


def commit_outcomes(rows: list[dict]) -> dict:
    statuses, reasons, poll_statuses = Counter(), Counter(), Counter()
    accepted = rejected = missing_archive = transport = unexpected = 0
    known_rejection_reasons = poll_errors = poll_observations = completed = failed = unresolved = 0
    for row in rows:
        status = count(row.get("http_status"))
        status = status if status is not None and 100 <= status <= 599 else None
        statuses[str(status) if status is not None else "NOT_RECORDED"] += 1
        admitted = row.get("accepted_202") is True
        accepted += admitted
        rejected += status is not None and status >= 400
        missing_archive += status == 202 and not admitted
        transport += status is None
        unexpected += status is not None and status < 400 and status != 202
        if status is not None and status >= 400:
            reason = row.get("reason_code")
            known = isinstance(reason, str) and reason in PUBLIC_REASONS
            label = reason if known else "UNRECOGNIZED" if reason not in (None, "", "NOT_RECORDED") else "NOT_RECORDED"
            reasons[label] += 1
            known_rejection_reasons += known
        completed += admitted and row.get("completed") is True
        failed += admitted and row.get("state") in {"failed", "error"}
        unresolved += admitted and not row.get("terminal_at")
        polls = row.get("polls")
        if isinstance(polls, list):
            poll_observations += 1
            for poll in polls:
                code = count(poll.get("http_status"))
                label = str(code) if code is not None and 100 <= code <= 599 else "NOT_RECORDED"
                poll_statuses[label] += 1
                poll_errors += code != 200
    return {"recorded_submissions": len(rows), "accepted_202": accepted,
            "http_rejected": rejected, "http_status_counts": dict(statuses),
            "missing_archive_on_202": missing_archive, "transport_or_unrecorded": transport,
            "unexpected_non202_success": unexpected,
            "rejection_reason_counts": dict(reasons), "known_rejection_reasons": known_rejection_reasons,
            "unknown_rejection_reasons": rejected - known_rejection_reasons,
            "completed": completed, "failed": failed, "unresolved": unresolved,
            "poll_history_submissions": poll_observations,
            "poll_http_status_counts": dict(poll_statuses),
            "poll_http_errors": poll_errors if poll_observations == accepted else None}
