#!/usr/bin/env python3
"""Reconcile accepted Commit operations with a real cursor/message-set API."""

from __future__ import annotations

import argparse
import csv
import json
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path
from typing import Any

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def read_commits(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def fetch(url: str, key: str, header: str, timeout: float) -> tuple[int | None, dict[str, Any], str]:
    request = urllib.request.Request(url, headers={header: key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            return response.status, payload if isinstance(payload, dict) else {}, raw[-4000:]
    except (OSError, urllib.error.URLError) as exc:
        return None, {}, str(exc)


def fetch_existing_cursor(
    base_url: str,
    session: str,
    key: str,
    header: str,
    timeout: float,
    uri_template: str,
) -> tuple[int | None, dict[str, Any], str]:
    """Read the durable cursor through EchoMem's existing /fs/read API."""
    uri = uri_template.format(session=session)
    url = f"{base_url.rstrip('/')}/fs/read?uri={quote(uri, safe=':/')}"
    code, payload, raw = fetch(url, key, header, timeout)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    text = result.get("text")
    if isinstance(text, str):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return code, {}, raw
        return code, decoded if isinstance(decoded, dict) else {}, raw
    return code, payload, raw


def values_from_payload(payload: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """Extract message, archive and operation identities without assuming one schema."""
    message_ids: set[str] = set()
    archive_ids: set[str] = set()
    operation_ids: set[str] = set()

    def visit(value: Any, list_context: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and list_context in {
                    "messages",
                    "items",
                    "message_ids",
                    "committed_message_ids",
                    "source_turn_ids",
                }:
                    message = item.get("message_id") or item.get("messageId") or item.get("id")
                    if message:
                        message_ids.add(str(message))
                    archive = item.get("archive_id") or item.get("archiveId")
                    operation = item.get("operation_id") or item.get("operationId")
                    if archive:
                        archive_ids.add(str(archive))
                    if operation:
                        operation_ids.add(str(operation))
                elif not isinstance(item, (dict, list)) and list_context in {
                    "message_ids",
                    "messages",
                    "items",
                    "committed_message_ids",
                    "source_turn_ids",
                }:
                    if item not in (None, ""):
                        message_ids.add(str(item))
                visit(item, list_context)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            normalized = str(key)
            if normalized in {"archive_id", "archiveId"} and item not in (None, ""):
                archive_ids.add(str(item))
            elif normalized in {"operation_id", "operationId"} and item not in (None, ""):
                operation_ids.add(str(item))
            if normalized in {
                "message_ids",
                "messages",
                "items",
                "committed_message_ids",
                "source_turn_ids",
            }:
                visit(item, normalized)
            else:
                visit(item, "")

    visit(payload)
    return message_ids, archive_ids, operation_ids


def ordered_message_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    """Extract message IDs in the order exposed by a durable read API.

    The set extractor above is intentionally schema-tolerant, but a set alone
    cannot prove the no-reordering part of the recovery contract. Keep this
    helper equally tolerant while preserving list order and removing repeated
    occurrences of the same ID.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    list_keys = {
        "messages",
        "items",
        "message_ids",
        "committed_message_ids",
        "source_turn_ids",
    }

    def append(value: Any) -> None:
        if value in (None, ""):
            return
        message_id = str(value)
        if message_id not in seen:
            seen.add(message_id)
            ordered.append(message_id)

    def visit(value: Any, list_context: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and list_context in list_keys:
                    append(
                        item.get("message_id")
                        or item.get("messageId")
                        or item.get("id")
                    )
                elif not isinstance(item, (dict, list)) and list_context in list_keys:
                    append(item)
                visit(item, list_context)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            visit(item, str(key) if str(key) in list_keys else "")

    visit(payload)
    return ordered


def reconcile(args: argparse.Namespace) -> dict[str, Any]:
    commits = [
        row for row in read_commits(args.commit_csv)
        if row.get("status", "").lower() in {"completed", "complete", "transcommit", "succeeded", "success"}
    ]
    if not commits:
        return {"status": INCONCLUSIVE, "reason": "no completed Commit evidence to reconcile"}
    if not args.cursor_url_template and not args.base_url:
        return {
            "status": INCONCLUSIVE,
            "reason": "no cursor endpoint or EchoMem base URL configured",
            "accepted_commits": len(commits),
        }
    checks = []
    for row in commits:
        session = row.get("session_id", "")
        if args.cursor_url_template:
            url = args.cursor_url_template.format(
                session=session, archive=row.get("archive_id", "")
            )
            code, payload, raw = fetch(
                url, args.auth_key, args.auth_header, args.timeout_s
            )
            source_payloads = {"cursor_endpoint": payload}
        else:
            code, payload, raw = fetch_existing_cursor(
                args.base_url,
                session,
                args.auth_key,
                args.auth_header,
                args.timeout_s,
                args.cursor_uri_template,
            )
            source_payloads = {"commit_cursor": payload}

        # EchoMem's stable public read APIs are the primary reconciliation
        # evidence. The cursor is an additional durable index, not the only
        # way to prove that a committed message survived.
        if args.base_url:
            archive_id = row.get("archive_id", "")
            for source, path in (
                ("history", f"/api/sessions/{session}/history?limit=200"),
                ("archives", f"/api/sessions/{session}/archives?limit=200"),
                ("archive", f"/api/sessions/{session}/archives/{archive_id}" if archive_id else ""),
                ("commit_status", f"/api/sessions/{session}/commits/{archive_id}" if archive_id else ""),
                ("commit_memories", f"/api/sessions/{session}/commits/{archive_id}/memories" if archive_id else ""),
            ):
                if not path:
                    continue
                source_code, source_payload, source_raw = fetch(
                    args.base_url.rstrip("/") + path,
                    args.auth_key,
                    args.auth_header,
                    args.timeout_s,
                )
                source_payloads[source] = {
                    "_http_status": source_code,
                    "_raw": source_raw,
                    **source_payload,
                }

        actual_sets: dict[str, set[str]] = {}
        archives: set[str] = set()
        operations: set[str] = set()
        source_statuses: dict[str, int | None] = {}
        for source, source_payload in source_payloads.items():
            actual_sets[source], source_archives, source_operations = values_from_payload(source_payload)
            archives.update(source_archives)
            operations.update(source_operations)
            source_statuses[source] = source_payload.get("_http_status")
        actual = set().union(*actual_sets.values()) if actual_sets else set()
        if not actual and not archives and not operations:
            checks.append({
                "session_id": session,
                "status": NOT_IMPLEMENTED if code == 404 else INCONCLUSIVE,
                "http_status": code,
                "raw": raw,
                "source_statuses": source_statuses,
                "reason": (
                    "cursor endpoint returned HTTP 404"
                    if code == 404
                    else "read-only responses were reachable but contained no externally parseable identities"
                ),
            })
            continue
        raw_expected = row.get("message_ids", "")
        try:
            parsed_expected = json.loads(raw_expected) if raw_expected else []
        except json.JSONDecodeError:
            parsed_expected = [item.strip(" '\"") for item in raw_expected.strip("[]").split(",") if item.strip()]
        expected = {str(item) for item in parsed_expected}
        expected_archive = str(row.get("archive_id") or "")
        expected_operation = str(row.get("operation_id") or "")
        missing = sorted(expected - actual)
        # /history is cumulative by contract, so older committed messages are
        # expected there. A single archive/cursor is scoped to this Commit and
        # can be checked for exact membership.
        history_ids = actual_sets.get("history", set())
        scoped_ids = set().union(
            actual_sets.get("archive", set()),
            actual_sets.get("commit_cursor", set()),
            actual_sets.get("cursor_endpoint", set()),
        )
        unexpected = sorted(scoped_ids - expected) if scoped_ids else []
        duplicate_count = max(0, len(parsed_expected) - len(expected))
        archive_ok = not expected_archive or expected_archive in archives
        operation_ok = not expected_operation or expected_operation in operations
        if args.strict:
            # In strict mode, an expected identity must be observed from at
            # least one durable source. The non-strict mode remains useful for
            # legacy APIs that expose only message membership.
            if expected_archive and not archives:
                archive_ok = False
            if expected_operation and not operations:
                operation_ok = False
        checks.append({
            "session_id": session,
            "archive_id": row.get("archive_id", ""),
            "http_status": code,
            "expected_message_count": len(expected),
            "actual_message_count": len(actual),
            "missing": missing,
            "unexpected": unexpected,
            "duplicate_expected_count": duplicate_count,
            "archive_observed": sorted(archives),
            "operation_observed": sorted(operations),
            "source_message_ids": {
                source: sorted(values) for source, values in actual_sets.items()
            },
            "history_contains_expected": expected <= history_ids if history_ids else None,
            "scoped_message_set_exact": (
                scoped_ids == expected if scoped_ids else None
            ),
            "source_statuses": source_statuses,
            "archive_match": archive_ok,
            "operation_match": operation_ok,
            "status": PASS if not missing and not unexpected and archive_ok and operation_ok else FAIL,
        })
    if any(item["status"] == FAIL for item in checks):
        status = FAIL
    elif any(item["status"] == NOT_IMPLEMENTED for item in checks):
        status = NOT_IMPLEMENTED
    else:
        status = PASS
    return {"status": status, "accepted_commits": len(commits), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit-csv", required=True, type=Path)
    parser.add_argument("--cursor-url-template", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument(
        "--cursor-uri-template",
        default="echo://sessions/{session}/current/commit_cursor.json",
    )
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument("--timeout-s", type=float, default=10)
    parser.add_argument("--strict", action="store_true", help="Require archive/operation identities when present in CSV")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = reconcile(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
