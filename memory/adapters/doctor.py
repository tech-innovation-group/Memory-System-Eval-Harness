"""Adapter doctor for the memory backend delivery boundary."""

from __future__ import annotations

from typing import Any

from memory.plugins.service import available_backends


EXPECTED_BACKENDS = ("echomemory", "openviking")


def summarize_backend(item: dict[str, Any]) -> dict[str, Any]:
    contract = item.get("contract") or {}
    missing_required = list(contract.get("missing_required_capabilities") or []) + list(contract.get("missing_required_methods") or [])
    missing_recommended = list(contract.get("missing_recommended_capabilities") or []) + list(contract.get("missing_optional_methods") or [])
    return {
        "id": item.get("id") or "",
        "name": item.get("name") or item.get("id") or "",
        "status": item.get("status") or "",
        "contract_status": contract.get("status") or "unknown",
        "contract_ok": bool(contract.get("ok")),
        "capabilities": [cap.get("name") for cap in (item.get("capabilities") or []) if cap.get("name")],
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "required_capabilities": contract.get("required_capabilities") or [],
        "required_methods": contract.get("required_methods") or [],
        "optional_methods": contract.get("optional_methods") or [],
    }


def build_report() -> dict[str, Any]:
    backends = available_backends()
    rows = [summarize_backend(item) for item in backends]
    ids = {str(item.get("id") or "") for item in rows}
    expected = set(EXPECTED_BACKENDS)
    unexpected = sorted(ids - expected)
    missing = sorted(expected - ids)
    failed = [item for item in rows if item.get("contract_status") == "fail" or item.get("missing_required")]
    warned = [item for item in rows if item.get("contract_status") == "warn" or item.get("missing_recommended")]
    status = "fail" if missing or unexpected or failed else ("warn" if warned else "ok")
    return {
        "status": status,
        "expected_backends": sorted(expected),
        "registered_backends": sorted(ids),
        "missing_backends": missing,
        "unexpected_backends": unexpected,
        "failed_backends": [item["id"] for item in failed],
        "warned_backends": [item["id"] for item in warned],
        "backends": rows,
        "safe_to_share": True,
        "secrets_included": False,
        "summary": text_report_from_parts(status, sorted(expected), sorted(ids), missing, unexpected, rows),
        "markdown": markdown_report_from_parts(status, sorted(expected), sorted(ids), missing, unexpected, rows),
    }


def text_report_from_parts(
    status: str,
    expected_backends: list[str],
    registered_backends: list[str],
    missing_backends: list[str],
    unexpected_backends: list[str],
    backends: list[dict[str, Any]],
) -> str:
    lines = [
        "Memory Backend Adapter Doctor",
        f"Status: {status}",
        f"Expected: {', '.join(expected_backends)}",
        f"Registered: {', '.join(registered_backends) or '-'}",
    ]
    if missing_backends:
        lines.append(f"Missing: {', '.join(missing_backends)}")
    if unexpected_backends:
        lines.append(f"Unexpected: {', '.join(unexpected_backends)}")
    lines.append("")
    for item in backends:
        lines.append(f"- {item['id']} ({item['name']}): contract={item['contract_status']} status={item['status']}")
        lines.append(f"  capabilities: {len(item['capabilities'])}")
        lines.append(f"  missing required: {', '.join(item['missing_required']) or 'none'}")
        lines.append(f"  missing recommended: {', '.join(item['missing_recommended']) or 'none'}")
    lines.append("")
    lines.append("Safe to share: yes. API keys and runtime secrets are not included.")
    return "\n".join(lines)


def markdown_report_from_parts(
    status: str,
    expected_backends: list[str],
    registered_backends: list[str],
    missing_backends: list[str],
    unexpected_backends: list[str],
    backends: list[dict[str, Any]],
) -> str:
    lines = [
        "# Memory Backend Adapter Doctor",
        "",
        f"- Status: `{status}`",
        f"- Expected backends: `{', '.join(expected_backends)}`",
        f"- Registered backends: `{', '.join(registered_backends) or '-'}`",
        f"- Missing backends: `{', '.join(missing_backends) or 'none'}`",
        f"- Unexpected backends: `{', '.join(unexpected_backends) or 'none'}`",
        "- Safe to share: yes, no API keys or runtime secrets are included.",
        "",
        "| Backend | Runtime Status | Contract | Missing Required | Missing Recommended |",
        "|---|---:|---:|---|---|",
    ]
    for item in backends:
        lines.append(
            "| {id} | {status} | {contract} | {missing_required} | {missing_recommended} |".format(
                id=item["id"],
                status=item["status"] or "-",
                contract=item["contract_status"],
                missing_required=", ".join(item["missing_required"]) or "none",
                missing_recommended=", ".join(item["missing_recommended"]) or "none",
            )
        )
    return "\n".join(lines)


def text_report(report: dict[str, Any]) -> str:
    return str(report.get("summary") or "")


def markdown_report(report: dict[str, Any]) -> str:
    return str(report.get("markdown") or "")


__all__ = [
    "EXPECTED_BACKENDS",
    "build_report",
    "markdown_report",
    "summarize_backend",
    "text_report",
]
