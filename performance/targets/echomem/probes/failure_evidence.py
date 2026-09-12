"""Secret-free failure classifications and stable references for black-box evidence."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def reference(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:24] if value else ""


def public_label(value: Any) -> str:
    text = str(value or "")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}", text) and not text.lower().startswith(("sk-", "bearer", "token", "key-")):
        return text
    return ""


def failure_evidence(payload: Any) -> dict[str, Any]:
    """Never export free-form errors, prompts, response content, or credentials."""
    if not isinstance(payload, dict):
        payload = {"error": str(payload or "")}
    # The session status endpoint wraps its terminal details in {"status": {...}}.
    if isinstance(payload.get("status"), dict):
        payload = {**payload, **payload["status"]}
    error = payload.get("error") or payload.get("error_message") or payload.get("exception") or ""
    text = json.dumps(error, ensure_ascii=False) if not isinstance(error, str) else error
    if not text:
        text = str(payload.get("message") or payload.get("msg") or "")
    lower = text.lower()
    patterns = {
        "PROVIDER_FREE_QUOTA_EXHAUSTED": ("insufficientfreequota", "free quota", "free tier quota"),
        "PROVIDER_BALANCE": ("arrearage", "insufficient balance", "account is in good standing"),
        "PROVIDER_QUOTA": ("insufficient_quota", "insufficient quota", "quota exceeded", "quota exhausted"),
        "PROVIDER_RATE_LIMIT": ("ratelimitexceeded", "rate_limit_exceeded", "rate limit", "too many requests"),
        "PROVIDER_AUTH": ("invalid_api_key", "invalidapikey", "incorrect api key", "authentication failed"),
        "CONTEXT_LENGTH": ("context_length_exceeded", "maximum context", "input length", "context length"),
        "TIMEOUT": ("timed out", "timeout"),
        "CIRCUIT_OPEN": ("circuit_open", "circuit open"),
        "BULKHEAD": ("bulkhead",),
    }
    categories = [name for name, terms in patterns.items() if any(term in lower for term in terms)]
    codes = []
    if isinstance(error, dict):
        for key in ("code", "type", "error_code"):
            label = public_label(error.get(key))
            if label:
                codes.append(label)
    for match in re.finditer(r"[\"'](?:code|type)[\"']\s*:\s*[\"']([A-Za-z][A-Za-z0-9_.:-]{0,79})[\"']", text):
        label = public_label(match.group(1))
        if label:
            codes.append(label)
    statuses = [int(x) for x in re.findall(r"(?:HTTP(?: Error)?|Error code:)\s*(\d{3})\b", text)]
    return {"error_present": bool(error), "categories": categories,
            "provider_codes": sorted(set(codes)), "upstream_http_statuses": sorted(set(statuses)),
            "error_signature": reference(text) if error else "",
            "error_type": public_label(payload.get("error_type")),
            "fault_domain": public_label(payload.get("fault_domain")),
            "stage": public_label(payload.get("stage")),
            "trace_ref": reference(payload.get("trace_id") or payload.get("request_id"))}
