from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


BASIC_CONTENT_KEYS = ("content", "abstract", "overview", "text", "body", "summary")
BASIC_URI_KEYS = ("uri", "source_uri", "path", "evidence_uri", "file")
SCORE_KEYS = ("score", "confidence", "similarity")
ECHOMEM_REQUIRED_KEYS = ("content", "uri", "score", "memory_type", "evidence_uri", "trace")


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(str(value).strip())


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if _is_non_empty(value):
            return value
    return ""


def _score_value(item: dict[str, Any]) -> Any:
    for key in SCORE_KEYS:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return ""


def _score_ok(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _status_from_counts(required_failures: int, warnings: int) -> str:
    if required_failures:
        return "fail"
    if warnings:
        return "warn"
    return "ok"


def _sample_ref(row: dict[str, str], row_index: int) -> dict[str, Any]:
    return {
        "row": row_index + 1,
        "sample_id": row.get("sample_id") or "",
        "question_id": row.get("question_id") or "",
        "question": row.get("question") or "",
    }


def _safe_json_list(raw: str) -> tuple[list[Any], str]:
    if not str(raw or "").strip():
        return [], ""
    try:
        data = json.loads(raw)
    except Exception as exc:
        return [], str(exc)
    if not isinstance(data, list):
        return [], "relevant_memory is not a JSON list"
    return data, ""


def _detect_backend(rows: list[dict[str, str]], explicit: str = "") -> str:
    text = explicit.strip().lower()
    if text in {"openviking", "echomemory", "echomem"}:
        return "echomemory" if text == "echomem" else text
    blob = " ".join(
        str(row.get(key) or "")
        for row in rows[:20]
        for key in ("backend", "eval_engine", "memory_uri", "prompt_mode", "relevant_memory")
    ).lower()
    if "echomemory" in blob or "echo://" in blob or "backend=\"echomemory\"" in blob:
        return "echomemory"
    if "openviking" in blob or "viking://" in blob:
        return "openviking"
    return "unknown"


def _check_basic_item(item: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = []
    if not _is_non_empty(_first_value(item, BASIC_CONTENT_KEYS)):
        missing.append("content_or_abstract")
    if not _is_non_empty(_first_value(item, BASIC_URI_KEYS)):
        missing.append("uri_or_path")
    if not _score_ok(_score_value(item)):
        missing.append("score")
    return not missing, missing


def _check_echomem_item(item: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = []
    content_value = item.get("content")
    uri_value = item.get("uri") or item.get("source_uri") or item.get("path")
    score_value = item.get("score") if item.get("score") not in (None, "") else item.get("confidence")
    normalized = {
        "content": content_value,
        "uri": uri_value,
        "score": score_value,
        "memory_type": item.get("memory_type"),
        "evidence_uri": item.get("evidence_uri"),
        "trace": item.get("trace"),
    }
    for key in ECHOMEM_REQUIRED_KEYS:
        value = normalized.get(key)
        if key == "score":
            ok = _score_ok(value)
        elif key == "trace":
            ok = isinstance(value, dict)
        else:
            ok = _is_non_empty(value)
        if not ok:
            missing.append(key)
    return not missing, missing


def validate_evidence_csv(path: Path, *, backend: str = "", limit: int = 5000, sample_limit: int = 12) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    if limit > 0:
        rows = rows[:limit]

    detected_backend = _detect_backend(rows, backend)
    required_failures = 0
    warnings = 0
    rows_with_relevant_memory = 0
    rows_with_empty_relevant_memory = 0
    parse_error_rows = 0
    total_items = 0
    basic_valid_items = 0
    echomem_valid_items = 0
    item_type_counts: Counter[str] = Counter()
    missing_basic: Counter[str] = Counter()
    missing_echomem: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    parse_examples: list[dict[str, Any]] = []
    empty_examples: list[dict[str, Any]] = []

    for row_index, row in enumerate(rows):
        raw = row.get("relevant_memory") or ""
        items, parse_error = _safe_json_list(raw)
        if parse_error:
            parse_error_rows += 1
            if len(parse_examples) < sample_limit:
                parse_examples.append({**_sample_ref(row, row_index), "error": parse_error})
            continue
        if not items:
            rows_with_empty_relevant_memory += 1
            if len(empty_examples) < sample_limit:
                empty_examples.append(_sample_ref(row, row_index))
            continue
        rows_with_relevant_memory += 1
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                missing_basic["item_not_object"] += 1
                if len(examples) < sample_limit:
                    examples.append({**_sample_ref(row, row_index), "item_index": item_index, "issue": "item_not_object"})
                continue
            total_items += 1
            item_type_counts[str(item.get("backend") or item.get("context_type") or item.get("memory_type") or "memory")] += 1
            basic_ok, basic_missing = _check_basic_item(item)
            if basic_ok:
                basic_valid_items += 1
            else:
                missing_basic.update(basic_missing)
                if len(examples) < sample_limit:
                    examples.append({
                        **_sample_ref(row, row_index),
                        "item_index": item_index,
                        "issue": "basic_missing",
                        "missing": basic_missing,
                        "keys": sorted(item.keys()),
                    })
            echomem_ok, echomem_missing = _check_echomem_item(item)
            if echomem_ok:
                echomem_valid_items += 1
            elif detected_backend == "echomemory":
                missing_echomem.update(echomem_missing)
                if len(examples) < sample_limit:
                    examples.append({
                        **_sample_ref(row, row_index),
                        "item_index": item_index,
                        "issue": "echomem_missing",
                        "missing": echomem_missing,
                        "keys": sorted(item.keys()),
                    })

    if parse_error_rows:
        required_failures += parse_error_rows
    if total_items == 0:
        required_failures += 1
    if total_items and basic_valid_items < total_items:
        warnings += total_items - basic_valid_items
    if detected_backend == "echomemory" and total_items and echomem_valid_items < total_items:
        required_failures += total_items - echomem_valid_items
    if rows and rows_with_empty_relevant_memory:
        warnings += rows_with_empty_relevant_memory

    checks = [
        {
            "id": "relevant_memory_json",
            "title": "relevant_memory JSON",
            "status": "ok" if not parse_error_rows else "fail",
            "severity": "required",
            "detail": "所有 relevant_memory 字段都能解析为 JSON list。" if not parse_error_rows else f"{parse_error_rows} 行 relevant_memory 解析失败。",
            "evidence": parse_examples,
        },
        {
            "id": "report_consumable_items",
            "title": "报告可消费字段",
            "status": "ok" if total_items and basic_valid_items == total_items else ("warn" if total_items else "fail"),
            "severity": "required" if not total_items else "recommended",
            "detail": f"{basic_valid_items}/{total_items} 条 evidence 具备 content/abstract、uri/path 和 score。",
            "evidence": dict(missing_basic),
        },
        {
            "id": "non_empty_retrieval",
            "title": "非空召回",
            "status": "ok" if rows and rows_with_relevant_memory == len(rows) else ("warn" if rows_with_relevant_memory else "fail"),
            "severity": "recommended" if rows_with_relevant_memory else "required",
            "detail": f"{rows_with_relevant_memory}/{len(rows)} 行有非空 relevant_memory。",
            "evidence": empty_examples,
        },
    ]
    if detected_backend == "echomemory":
        checks.append(
            {
                "id": "echomem_strict_shape",
                "title": "EchoMemory 严格 evidence 契约",
                "status": "ok" if total_items and echomem_valid_items == total_items else "fail",
                "severity": "required",
                "detail": f"{echomem_valid_items}/{total_items} 条 evidence 满足 content/uri/score/memory_type/evidence_uri/trace。",
                "evidence": dict(missing_echomem),
            }
        )
    else:
        checks.append(
            {
                "id": "echomem_strict_shape",
                "title": "EchoMemory 严格 evidence 契约",
                "status": "ok",
                "severity": "info",
                "detail": f"当前识别为 {detected_backend}，不强制 EchoMemory 字段；OpenViking evidence 可使用 abstract/uri/score。",
                "evidence": {},
            }
        )

    status = _status_from_counts(
        len([item for item in checks if item["severity"] == "required" and item["status"] == "fail"]),
        len([item for item in checks if item["status"] == "warn"]),
    )
    return {
        "status": status,
        "path": str(path),
        "backend": detected_backend,
        "rows": len(rows),
        "rows_with_relevant_memory": rows_with_relevant_memory,
        "rows_with_empty_relevant_memory": rows_with_empty_relevant_memory,
        "parse_error_rows": parse_error_rows,
        "total_items": total_items,
        "basic_valid_items": basic_valid_items,
        "echomem_valid_items": echomem_valid_items,
        "item_type_counts": dict(item_type_counts),
        "missing_basic": dict(missing_basic),
        "missing_echomem": dict(missing_echomem),
        "checks": checks,
        "examples": examples,
        "summary": "\n".join(
            [
                "Evidence Contract",
                f"- Status: {status}",
                f"- Backend: {detected_backend}",
                f"- Rows: {len(rows)}",
                f"- Non-empty relevant_memory rows: {rows_with_relevant_memory}",
                f"- Evidence items: {total_items}",
                f"- Report-consumable items: {basic_valid_items}",
                f"- EchoMemory strict items: {echomem_valid_items}" if detected_backend == "echomemory" else "- EchoMemory strict items: not required for this backend",
            ]
        ),
    }
