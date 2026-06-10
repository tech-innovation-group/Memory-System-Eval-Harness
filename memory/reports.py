from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _add_int_field(row: dict[str, str], key: str) -> int | None:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _bump(counter: dict[str, int], value: str | None, fallback: str = "unknown") -> None:
    label = str(value or "").strip() or fallback
    counter[label] = counter.get(label, 0) + 1


def _first_non_empty(values: set[str]) -> str:
    return sorted(value for value in values if value)[0] if values else ""


def _merge_json_counter(target: dict[str, int], raw: str | None) -> None:
    try:
        data = json.loads(str(raw or "{}"))
    except Exception:
        return
    if isinstance(data, dict):
        for key, value in data.items():
            try:
                count = int(value)
            except (TypeError, ValueError):
                count = 1
            target[str(key)] = target.get(str(key), 0) + count
    elif isinstance(data, list):
        for item in data:
            key = str(item)
            target[key] = target.get(key, 0) + 1


RETRYABLE_FAILURE_HEALTH = {"api_error", "timeout", "rate_limited", "retrieval_empty", "retrieval_error"}


def retrieval_failed(row: dict[str, str]) -> bool:
    status = str(row.get("retrieval_status") or "").strip().lower()
    return status not in {"", "ok"}


def retryable_qa_failure(row: dict[str, str]) -> bool:
    return (
        str(row.get("model_status") or "").strip().lower() == "failed"
        or str(row.get("answer_status") or "").strip().lower() == "failed"
        or retrieval_failed(row)
        or str(row.get("health_status") or "").strip().lower() in RETRYABLE_FAILURE_HEALTH
        or bool(str(row.get("retrieval_error") or "").strip())
    )


def row_grade(row: dict[str, str]) -> str:
    raw = (row.get("result") or row.get("simple_grade") or row.get("simple_match") or "").upper()
    if raw in {"MATCH", "CORRECT"}:
        return "CORRECT"
    if raw == "WRONG":
        return "WRONG"
    return "UNSCORED"


def parse_csv_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    result_counts: dict[str, int] = {}
    simple_counts: dict[str, int] = {}
    categories: dict[str, dict[str, int]] = {}
    samples: dict[str, dict[str, int]] = {}
    api_errors = 0
    total_time = 0.0
    time_count = 0
    total_injection_tokens = 0
    injection_token_count = 0
    total_archive_evidence = 0
    archive_evidence_count = 0
    total_memory_evidence = 0
    memory_evidence_count = 0
    total_retrieval_tokens = 0
    retrieval_token_count = 0
    total_answer_tokens = 0
    answer_token_count = 0
    health_counts: dict[str, int] = {}
    retrieval_status_counts: dict[str, int] = {}
    answer_status_counts: dict[str, int] = {}
    model_status_counts: dict[str, int] = {}
    model_error_counts: dict[str, int] = {}
    retrieval_error_rows = 0
    unknown_response_rows = 0
    empty_response_rows = 0
    answer_failed_rows = 0
    retryable_failed_rows = 0
    retryable_failed_question_ids: list[str] = []
    rows_with_model_retries = 0
    model_retry_total = 0
    model_rate_limited_rows = 0
    iteration_total = 0
    iteration_count = 0
    tool_call_total = 0
    tool_call_rows = 0
    tool_name_counts: dict[str, int] = {}
    prompt_modes: set[str] = set()
    vikingbot_prompt_values: set[str] = set()
    group_chat_values: set[str] = set()
    memory_user_strategy_values: set[str] = set()
    initial_agent_memory_values: set[str] = set()
    vikingbot_identity_mode_values: set[str] = set()
    vikingbot_channel_values: set[str] = set()
    vikingbot_workspace_values: set[str] = set()
    vikingbot_bootstrap_values: set[str] = set()
    vikingbot_skill_values: set[str] = set()
    tool_loop_values: set[str] = set()
    tool_set_values: set[str] = set()
    content_read_values: set[str] = set()
    max_iterations_values: set[str] = set()

    for row in rows:
        raw_result = (row.get("result") or "").upper()
        raw_simple = (row.get("simple_grade") or row.get("simple_match") or "").upper()
        if raw_simple in {"MATCH", "CORRECT", "TRUE", "1"}:
            simple_result = "CORRECT"
        elif raw_simple == "WRONG":
            simple_result = "WRONG"
        else:
            simple_result = "UNSCORED"
        simple_counts[simple_result] = simple_counts.get(simple_result, 0) + 1
        if raw_result in {"MATCH", "CORRECT"}:
            result = "CORRECT"
        elif raw_result == "WRONG":
            result = "WRONG"
        else:
            result = "UNSCORED"
        result_counts[result] = result_counts.get(result, 0) + 1
        if str(row.get("reasoning", "")).startswith("[API ERROR]"):
            api_errors += 1
        cat = str(row.get("category", ""))
        categories.setdefault(cat, {"CORRECT": 0, "WRONG": 0, "UNSCORED": 0})
        categories[cat][result if result in ("CORRECT", "WRONG") else "UNSCORED"] += 1
        sample = str(row.get("sample_id", ""))
        samples.setdefault(sample, {"CORRECT": 0, "WRONG": 0, "UNSCORED": 0})
        samples[sample][result if result in ("CORRECT", "WRONG") else "UNSCORED"] += 1
        try:
            total_time += float(row.get("time_cost") or 0)
            time_count += 1
        except ValueError:
            pass
        try:
            total_injection_tokens += int(float(row.get("injection_tokens_est") or 0))
            injection_token_count += 1
        except ValueError:
            pass
        archive_evidence = _add_int_field(row, "archive_fallback_count")
        if archive_evidence is not None:
            total_archive_evidence += archive_evidence
            archive_evidence_count += 1
        memory_evidence = _add_int_field(row, "memory_hit_count")
        if memory_evidence is not None:
            total_memory_evidence += memory_evidence
            memory_evidence_count += 1
        retrieval_tokens = _add_int_field(row, "retrieval_tokens_est")
        if retrieval_tokens is not None:
            total_retrieval_tokens += retrieval_tokens
            retrieval_token_count += 1
        answer_tokens = _add_int_field(row, "answer_total_tokens")
        if answer_tokens is not None:
            total_answer_tokens += answer_tokens
            answer_token_count += 1
        _bump(health_counts, row.get("health_status"))
        _bump(retrieval_status_counts, row.get("retrieval_status"))
        _bump(answer_status_counts, row.get("answer_status"))
        _bump(model_status_counts, row.get("model_status"))
        answer_status = str(row.get("answer_status") or "").strip().lower()
        response_text = str(row.get("response") or "").strip().lower()
        model_retry_count = _add_int_field(row, "model_retry_count") or 0
        if model_retry_count > 0:
            rows_with_model_retries += 1
            model_retry_total += model_retry_count
        iteration = _add_int_field(row, "iteration")
        if iteration is not None:
            iteration_total += iteration
            iteration_count += 1
        tool_call_count = _add_int_field(row, "tool_call_count") or 0
        tool_call_total += tool_call_count
        if tool_call_count > 0:
            tool_call_rows += 1
        _merge_json_counter(tool_name_counts, row.get("tool_call_name_counts"))
        if row.get("prompt_mode"):
            prompt_modes.add(str(row.get("prompt_mode") or ""))
        if row.get("vikingbot_prompt_aligned"):
            vikingbot_prompt_values.add(str(row.get("vikingbot_prompt_aligned") or ""))
        if row.get("group_chat"):
            group_chat_values.add(str(row.get("group_chat") or ""))
        if row.get("memory_user_strategy"):
            memory_user_strategy_values.add(str(row.get("memory_user_strategy") or ""))
        if row.get("initial_agent_memory_enabled"):
            initial_agent_memory_values.add(str(row.get("initial_agent_memory_enabled") or ""))
        if row.get("vikingbot_identity_mode"):
            vikingbot_identity_mode_values.add(str(row.get("vikingbot_identity_mode") or ""))
        if row.get("vikingbot_channel"):
            vikingbot_channel_values.add(str(row.get("vikingbot_channel") or ""))
        if row.get("vikingbot_workspace"):
            vikingbot_workspace_values.add(str(row.get("vikingbot_workspace") or ""))
        if row.get("vikingbot_bootstrap_files"):
            vikingbot_bootstrap_values.add(str(row.get("vikingbot_bootstrap_files") or ""))
        if row.get("vikingbot_skill_names"):
            vikingbot_skill_values.add(str(row.get("vikingbot_skill_names") or ""))
        if row.get("openviking_tool_loop_enabled"):
            tool_loop_values.add(str(row.get("openviking_tool_loop_enabled") or ""))
        if row.get("openviking_tool_set"):
            tool_set_values.add(str(row.get("openviking_tool_set") or ""))
        if row.get("openviking_content_read_enabled"):
            content_read_values.add(str(row.get("openviking_content_read_enabled") or ""))
        if row.get("max_iterations"):
            max_iterations_values.add(str(row.get("max_iterations") or ""))
        if str(row.get("model_error_kind") or "").strip().lower() == "rate_limited" or str(row.get("health_status") or "").strip().lower() == "rate_limited":
            model_rate_limited_rows += 1
        if answer_status == "failed":
            answer_failed_rows += 1
        if response_text == "":
            empty_response_rows += 1
        elif response_text in {"unknown", "not specified", "not mentioned", "no specific information"}:
            unknown_response_rows += 1
        if row.get("model_error_kind"):
            _bump(model_error_counts, row.get("model_error_kind"))
        if str(row.get("retrieval_error") or "").strip():
            retrieval_error_rows += 1
        if retryable_qa_failure(row):
            retryable_failed_rows += 1
            qid = str(row.get("question_id") or "").strip()
            if qid and qid not in retryable_failed_question_ids:
                retryable_failed_question_ids.append(qid)

    total = len(rows)
    correct = result_counts.get("CORRECT", 0)
    wrong = result_counts.get("WRONG", 0)
    graded = correct + wrong
    simple_correct = simple_counts.get("CORRECT", 0)
    simple_wrong = simple_counts.get("WRONG", 0)
    simple_graded = simple_correct + simple_wrong
    summary_path = path.with_name("summary.json")
    summary_json = read_json(summary_path) if summary_path.exists() else None
    return {
        "rows": total,
        "graded": graded,
        "correct": correct,
        "wrong": wrong,
        "accuracy": correct / graded if graded else None,
        "result_counts": result_counts,
        "simple_counts": simple_counts,
        "simple_graded": simple_graded,
        "simple_correct": simple_correct,
        "simple_accuracy": simple_correct / simple_graded if simple_graded else None,
        "exact_match_reference": simple_correct / total if total else None,
        "api_errors": api_errors,
        "avg_time": total_time / time_count if time_count else None,
        "total_injection_tokens_est": total_injection_tokens,
        "avg_injection_tokens_est": round(total_injection_tokens / injection_token_count, 1) if injection_token_count else None,
        "archive_fallback_total": total_archive_evidence,
        "avg_archive_fallback_count": round(total_archive_evidence / archive_evidence_count, 2) if archive_evidence_count else None,
        "memory_hit_total": total_memory_evidence,
        "avg_memory_hit_count": round(total_memory_evidence / memory_evidence_count, 2) if memory_evidence_count else None,
        "retrieval_tokens_est_total": total_retrieval_tokens,
        "avg_retrieval_tokens_est": round(total_retrieval_tokens / retrieval_token_count, 1) if retrieval_token_count else None,
        "answer_total_tokens": total_answer_tokens,
        "avg_answer_total_tokens": round(total_answer_tokens / answer_token_count, 1) if answer_token_count else None,
        "health_counts": health_counts,
        "retrieval_status_counts": retrieval_status_counts,
        "answer_status_counts": answer_status_counts,
        "model_status_counts": model_status_counts,
        "model_error_counts": model_error_counts,
        "model_ok_count": model_status_counts.get("ok", 0),
        "model_failed_count": model_status_counts.get("failed", 0),
        "rows_with_model_retries": rows_with_model_retries,
        "model_retry_total": model_retry_total,
        "model_rate_limited_count": model_rate_limited_rows,
        "iteration_total": iteration_total,
        "avg_iteration": round(iteration_total / iteration_count, 2) if iteration_count else None,
        "tool_call_total": tool_call_total,
        "tool_call_rows": tool_call_rows,
        "tool_name_counts": tool_name_counts,
        "prompt_mode": _first_non_empty(prompt_modes),
        "vikingbot_prompt_aligned": _first_non_empty(vikingbot_prompt_values),
        "group_chat": _first_non_empty(group_chat_values),
        "memory_user_strategy": _first_non_empty(memory_user_strategy_values),
        "initial_agent_memory_enabled": _first_non_empty(initial_agent_memory_values),
        "vikingbot_identity_mode": _first_non_empty(vikingbot_identity_mode_values),
        "vikingbot_channel": _first_non_empty(vikingbot_channel_values),
        "vikingbot_workspace": _first_non_empty(vikingbot_workspace_values),
        "vikingbot_bootstrap_files": _first_non_empty(vikingbot_bootstrap_values),
        "vikingbot_skill_names": _first_non_empty(vikingbot_skill_values),
        "openviking_tool_loop_enabled": _first_non_empty(tool_loop_values),
        "openviking_tool_set": _first_non_empty(tool_set_values),
        "openviking_content_read_enabled": _first_non_empty(content_read_values),
        "max_iterations": _first_non_empty(max_iterations_values),
        "answer_failed_count": answer_failed_rows,
        "answer_empty_or_unknown_count": answer_status_counts.get("empty_or_unknown", 0),
        "unknown_response_count": unknown_response_rows,
        "empty_response_count": empty_response_rows,
        "retrieval_ok_count": retrieval_status_counts.get("ok", 0),
        "retrieval_error_rows": retrieval_error_rows,
        "retryable_failed_rows": retryable_failed_rows,
        "retryable_failed_questions": len(retryable_failed_question_ids),
        "retryable_failed_question_ids": retryable_failed_question_ids,
        "categories": categories,
        "samples": samples,
        "summary_json": summary_json,
    }


def parse_json_run_summary(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        return {}
    if data.get("mode") == "chenmo" and isinstance(data.get("summary"), dict):
        summary = data.get("summary") or {}
        results = data.get("results") or []
        passed = int(summary.get("passed") or 0)
        failed = int(summary.get("failed") or 0)
        total = int(summary.get("total") or len(results) or 0)
        by_section = summary.get("by_section") if isinstance(summary.get("by_section"), dict) else {}
        result_counts = {"CORRECT": passed, "WRONG": failed}
        categories: dict[str, dict[str, int]] = {}
        for section, item in by_section.items():
            if not isinstance(item, dict):
                continue
            section_total = int(item.get("total") or 0)
            section_passed = int(item.get("passed") or 0)
            categories[str(section)] = {
                "CORRECT": section_passed,
                "WRONG": max(0, section_total - section_passed),
                "UNSCORED": 0,
            }
        return {
            "summary_type": "chenmo_eval",
            "dataset_format": "chenmo",
            "status": "CHENMO_EVAL_DONE",
            "rows": total,
            "count": total,
            "graded": total,
            "correct": passed,
            "wrong": failed,
            "accuracy": passed / total if total else None,
            "pass_rate": summary.get("pass_rate"),
            "avg_score": round(sum(float((row.get("judgment") or {}).get("score") or 0) for row in results) / total, 4) if total else None,
            "result_counts": result_counts,
            "categories": categories,
            "question_count": data.get("question_count") or total,
            "turn_count": data.get("turn_count"),
            "answer_model": data.get("answer_model"),
            "embedding_model": data.get("embedding_model"),
            "engine": data.get("engine"),
            "scenario_path": data.get("scenario_path"),
            "summary_json": {
                "count": total,
                "correct": passed,
                "wrong": failed,
                "graded": total,
                "accuracy": passed / total if total else None,
                "dataset_format": "chenmo",
                "status": "CHENMO_EVAL_DONE",
                "by_section": by_section,
                "output_json": str(path),
            },
        }
    if data.get("status") in {"OPENVIKING_IMPORT_DONE", "OPENVIKING_IMPORT_INCOMPLETE"} or data.get("records"):
        records = data.get("records") or []
        extracted = 0
        session_count = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            sessions = record.get("session_records") or []
            session_count += len(sessions)
            for session in sessions:
                after = session.get("session_after_commit") or {}
                memories = after.get("memories_extracted") or {}
                extracted += int(memories.get("total") or 0)
        return {
            "summary_type": "openviking_import",
            "status": data.get("status"),
            "samples": data.get("samples"),
            "complete_samples": data.get("complete_samples"),
            "incomplete_samples": data.get("incomplete_samples"),
            "expected_messages": data.get("expected_messages"),
            "submitted_messages": data.get("submitted_messages"),
            "session_count": session_count,
            "extracted_memories": extracted,
            "rows": records[0].get("expected_messages") if len(records) == 1 and isinstance(records[0], dict) else data.get("expected_messages"),
            "graded": None,
            "accuracy": None,
            "result_counts": {},
        }
    return {}


def compare_runs(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for record in records:
        summary = record.get("summary") or {}
        summary_json = summary.get("summary_json") or {}
        accuracy = summary.get("accuracy")
        rows.append(
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "kind": record.get("kind"),
                "status": record.get("status"),
                "score": accuracy,
                "exact_match_reference": summary.get("exact_match_reference") or summary_json.get("exact_match_rate"),
                "simple_correct": summary.get("simple_correct") or summary_json.get("exact_match_count"),
                "rows": summary.get("rows") or summary_json.get("count"),
                "pending": (summary.get("result_counts") or {}).get("UNSCORED"),
                "recall_tokens_avg": summary_json.get("recall_tokens_est_avg"),
                "selected_memories_avg": summary_json.get("selected_memories_avg"),
                "output_file": record.get("output_file"),
            }
        )
    baseline = next((row for row in rows if row.get("score") is not None), None)
    for row in rows:
        row["delta_vs_first"] = (
            row["score"] - baseline["score"]
            if baseline and row.get("score") is not None and baseline.get("score") is not None
            else None
        )
    return {"runs": rows}


def compare_csv_rows(base_path: Path, candidate_path: Path) -> dict[str, Any]:
    def key_for(row: dict[str, str], index: int) -> str:
        return "|".join(
            [
                row.get("sample_id") or row.get("conversation_id") or "",
                row.get("question_id") or row.get("qi") or row.get("question_index") or str(index),
                row.get("question") or "",
            ]
        )

    base_rows = list(csv.DictReader(base_path.open(newline="", encoding="utf-8", errors="replace")))
    candidate_rows = list(csv.DictReader(candidate_path.open(newline="", encoding="utf-8", errors="replace")))
    base = {key_for(row, i): row for i, row in enumerate(base_rows)}
    candidate = {key_for(row, i): row for i, row in enumerate(candidate_rows)}
    keys = sorted(set(base) | set(candidate))
    changes = []
    improved = regressed = changed = added = removed = 0
    transitions: dict[str, int] = {}
    category_transitions: dict[str, dict[str, int]] = {}
    for key in keys:
        before = base.get(key)
        after = candidate.get(key)
        if before is None:
            added += 1
            changes.append({"type": "added", "key": key, "before": None, "after": after})
            continue
        if after is None:
            removed += 1
            changes.append({"type": "removed", "key": key, "before": before, "after": None})
            continue
        b_grade = row_grade(before)
        a_grade = row_grade(after)
        if b_grade == a_grade:
            continue
        changed += 1
        transition = f"{b_grade}->{a_grade}"
        transitions[transition] = transitions.get(transition, 0) + 1
        category = str((after or before or {}).get("category") or "-")
        category_transitions.setdefault(category, {})
        category_transitions[category][transition] = category_transitions[category].get(transition, 0) + 1
        if b_grade != "CORRECT" and a_grade == "CORRECT":
            improved += 1
            kind = "improved"
        elif b_grade == "CORRECT" and a_grade != "CORRECT":
            regressed += 1
            kind = "regressed"
        else:
            kind = "changed"
        changes.append({"type": kind, "key": key, "before": before, "after": after})
    return {
        "base": str(base_path),
        "candidate": str(candidate_path),
        "base_rows": len(base_rows),
        "candidate_rows": len(candidate_rows),
        "shared_rows": len(set(base) & set(candidate)),
        "changed": changed,
        "improved": improved,
        "regressed": regressed,
        "added": added,
        "removed": removed,
        "transitions": transitions,
        "category_transitions": category_transitions,
        "changes": changes[:80],
    }


def _json_items(raw: str | None) -> list[Any]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("memories", "items", "results", "relevant_memory"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return [data]
    return []


def _clip_text(value: Any, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _row_response(row: dict[str, str]) -> str:
    return str(row.get("response") or row.get("prediction") or row.get("model_response") or "").strip()


def _response_is_unknown(response: str) -> bool:
    text = re.sub(r"\s+", " ", response.strip().lower())
    if not text:
        return True
    exact = {
        "unknown",
        "not specified",
        "not mentioned",
        "no specific information",
        "no specific info",
        "i don't know",
        "i do not know",
        "cannot determine",
        "can't determine",
    }
    if text in exact:
        return True
    return any(
        phrase in text
        for phrase in [
            "not specified in the memory",
            "not mentioned in the memory",
            "not provided in the memories",
            "no evidence in the memories",
            "i could not find",
            "i can't find",
            "i cannot find",
            "there is no information",
        ]
    )


def _row_evidence_count(row: dict[str, str]) -> int:
    counts = [
        _add_int_field(row, "retrieval_count"),
        _add_int_field(row, "memory_hit_count"),
        _add_int_field(row, "user_memory_count"),
        _add_int_field(row, "agent_memory_count"),
    ]
    explicit = max((value for value in counts if value is not None), default=0)
    items = _json_items(row.get("relevant_memory"))
    preview = str(row.get("context_preview") or row.get("prompt_preview") or "")
    preview_count = len(re.findall(r"<memory\b|memory index=", preview, flags=re.IGNORECASE))
    return max(explicit, len(items), preview_count)


def _row_evidence_snippet(row: dict[str, str]) -> dict[str, Any]:
    items = _json_items(row.get("relevant_memory"))
    if items:
        item = items[0]
        if isinstance(item, dict):
            return {
                "uri": item.get("uri") or item.get("source_uri") or item.get("path") or item.get("evidence_uri") or "",
                "score": item.get("score") or item.get("confidence") or "",
                "content": _clip_text(item.get("content") or item.get("abstract") or item.get("text") or item.get("overview") or "", 360),
            }
        return {"uri": "", "score": "", "content": _clip_text(item, 360)}
    preview = str(row.get("context_preview") or "")
    return {"uri": "", "score": "", "content": _clip_text(preview, 360)}


def _question_kind(row: dict[str, str]) -> str:
    question = str(row.get("question") or "").lower()
    category = str(row.get("category") or "").strip()
    if category == "2" or question.startswith("when") or re.search(r"\b(month|year|week|day|date|how long|how many months|how many weeks)\b", question):
        return "time"
    if any(item in question for item in ["how many", "which", "what books", "what events", "what activities", "what pets", "what places", "what are"]):
        return "list"
    return "fact"


def failure_attribution(row: dict[str, str]) -> dict[str, Any]:
    """Classify one QA row into an actionable evaluation failure bucket."""
    grade = row_grade(row)
    response = _row_response(row)
    response_lower = response.lower()
    reasoning = str(row.get("reasoning") or "").lower()
    question_kind = _question_kind(row)
    evidence_count = _row_evidence_count(row)
    has_evidence = evidence_count > 0
    retrieval_status = str(row.get("retrieval_status") or "").strip().lower()
    retrieval_error = str(row.get("retrieval_error") or "").strip()
    model_status = str(row.get("model_status") or "").strip().lower()
    answer_status = str(row.get("answer_status") or "").strip().lower()
    model_error_kind = str(row.get("model_error_kind") or "").strip().lower()
    health_status = str(row.get("health_status") or "").strip().lower()
    is_unknown = _response_is_unknown(response)

    base = {
        "grade": grade,
        "question_kind": question_kind,
        "evidence_count": evidence_count,
        "has_evidence": has_evidence,
        "retryable": False,
        "owner": "agent",
        "severity": "warn",
    }
    if grade == "CORRECT":
        return {
            **base,
            "mode": "correct",
            "label": "已正确",
            "severity": "ok",
            "owner": "none",
            "reason": "Judge 或精确匹配已判定正确。",
        }
    if model_status == "failed" or answer_status == "failed" or model_error_kind or health_status in {"api_error", "timeout", "rate_limited"}:
        return {
            **base,
            "mode": "model_api_error",
            "label": "模型/API 异常",
            "severity": "bad",
            "owner": "model",
            "retryable": True,
            "reason": row.get("model_error") or row.get("model_error_kind") or row.get("health_status") or "模型调用失败或被限流。",
        }
    if retrieval_error or (retrieval_status and retrieval_status != "ok"):
        return {
            **base,
            "mode": "retrieval_error",
            "label": "检索异常",
            "severity": "bad",
            "owner": "retrieval",
            "retryable": True,
            "reason": retrieval_error or row.get("retrieval_status") or "检索阶段返回非 ok 状态。",
        }
    if is_unknown and has_evidence:
        return {
            **base,
            "mode": "unknown_with_evidence",
            "label": "有证据但回答 Unknown",
            "owner": "agent_prompt",
            "reason": "Relevant memory 非空，但回答仍然回避或 unknown，优先检查回答 prompt、证据排序和证据使用规则。",
        }
    if is_unknown or not has_evidence:
        return {
            **base,
            "mode": "no_relevant_memory",
            "label": "未召回可用记忆",
            "owner": "retrieval",
            "retryable": True,
            "reason": "没有可用 relevant memory，或回答明确表示未找到信息。",
        }
    if grade == "UNSCORED":
        return {
            **base,
            "mode": "pending_judge",
            "label": "待 Judge",
            "owner": "judge",
            "reason": "QA 已产生结果但还没有正式 Judge 结果。",
        }
    if question_kind == "time":
        return {
            **base,
            "mode": "time_reasoning_error",
            "label": "时间题推理错误",
            "owner": "context_engineering",
            "reason": "时间类问题已召回证据但日期/相对时间没有对齐。",
        }
    if question_kind == "list":
        return {
            **base,
            "mode": "list_aggregation_error",
            "label": "列表/聚合遗漏",
            "owner": "context_engineering",
            "reason": "问题需要合并多条记忆，当前回答遗漏或聚合不完整。",
        }
    if any(item in reasoning for item in ["does not mention", "omits", "different", "instead", "contradict"]):
        return {
            **base,
            "mode": "evidence_mismatch",
            "label": "证据与答案不一致",
            "owner": "retrieval",
            "reason": "Judge 认为回答与标准答案存在事实差异或遗漏，需要检查证据是否命中正确事实。",
        }
    if response_lower.startswith("[") or "[api error]" in reasoning or "[parse error]" in reasoning:
        return {
            **base,
            "mode": "model_api_error",
            "label": "模型/API 异常",
            "severity": "bad",
            "owner": "model",
            "retryable": True,
            "reason": "回答或判分输出包含 API/解析错误。",
        }
    return {
        **base,
        "mode": "semantic_mismatch",
        "label": "语义错配或幻觉",
        "owner": "agent",
        "reason": "已召回证据但最终回答与标准答案不一致。",
    }


def failure_mode(row: dict[str, str]) -> str:
    return str(failure_attribution(row).get("label") or "未知问题")


def attribution_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    severity_counts: dict[str, int] = {}
    owner_counts: dict[str, int] = {}
    question_kind_counts: dict[str, int] = {}
    retryable_rows = 0
    correct_rows = 0
    problem_rows = 0
    for index, row in enumerate(rows):
        item = failure_attribution(row)
        severity_counts[item["severity"]] = severity_counts.get(item["severity"], 0) + 1
        owner_counts[item["owner"]] = owner_counts.get(item["owner"], 0) + 1
        question_kind_counts[item["question_kind"]] = question_kind_counts.get(item["question_kind"], 0) + 1
        if item["mode"] == "correct":
            correct_rows += 1
            continue
        problem_rows += 1
        if item.get("retryable"):
            retryable_rows += 1
        bucket = buckets.setdefault(
            item["mode"],
            {
                "mode": item["mode"],
                "label": item["label"],
                "severity": item["severity"],
                "owner": item["owner"],
                "retryable": bool(item.get("retryable")),
                "reason": item["reason"],
                "count": 0,
                "examples": [],
            },
        )
        bucket["count"] += 1
        if len(bucket["examples"]) < 5:
            bucket["examples"].append(
                {
                    "row_index": index,
                    "sample_id": row.get("sample_id") or row.get("conversation_id") or "",
                    "question_id": row.get("question_id") or row.get("native_question_id") or "",
                    "category": row.get("category", ""),
                    "question": row.get("question", ""),
                    "gold": row.get("answer", ""),
                    "response": response if (response := _row_response(row)) else "",
                    "reasoning": _clip_text(row.get("reasoning", ""), 420),
                    "evidence_count": item["evidence_count"],
                    "evidence": _row_evidence_snippet(row),
                }
            )
    ordered = sorted(
        buckets.values(),
        key=lambda item: (0 if item["severity"] == "bad" else 1 if item["severity"] == "warn" else 2, -int(item["count"])),
    )
    action_items = []
    mode_counts = {item["mode"]: item["count"] for item in ordered}
    if mode_counts.get("model_api_error"):
        action_items.append("先重跑模型/API 异常题，限流或超时不应计入真实准确率。")
    if mode_counts.get("retrieval_error") or mode_counts.get("no_relevant_memory"):
        action_items.append("检查导入完整性、workspace/account 是否一致，再看 top-k 与检索 query。")
    if mode_counts.get("unknown_with_evidence"):
        action_items.append("有证据但 Unknown 通常是 agent prompt 或上下文排序问题，优先查看题目详情里的 evidence。")
    if mode_counts.get("pending_judge"):
        action_items.append("运行 Judge 或单独重跑 Judge，避免把待判分当成 0% 准确率。")
    if mode_counts.get("time_reasoning_error"):
        action_items.append("时间题需要保留 current date、event time 和相对时间原文，建议单独回归。")
    return {
        "schema_version": 2,
        "total": len(rows),
        "correct_rows": correct_rows,
        "problem_rows": problem_rows,
        "retryable_rows": retryable_rows,
        "severity_counts": severity_counts,
        "owner_counts": owner_counts,
        "question_kind_counts": question_kind_counts,
        "mode_counts": mode_counts,
        "buckets": ordered,
        "action_items": action_items,
    }


def cluster_failures(rows: list[dict[str, str]]) -> dict[str, Any]:
    clusters: dict[str, dict[str, Any]] = {}
    for row in rows:
        grade = (row.get("result") or row.get("simple_grade") or row.get("simple_match") or "").upper()
        if grade in {"MATCH", "CORRECT"}:
            continue
        mode = failure_mode(row)
        cat = str(row.get("category") or "unknown")
        key = f"{mode} / C{cat}"
        cluster = clusters.setdefault(
            key,
            {"label": key, "mode": mode, "category": cat, "count": 0, "examples": [], "sample_ids": {}},
        )
        cluster["count"] += 1
        sample_id = row.get("sample_id") or row.get("conversation_id") or ""
        if sample_id:
            cluster["sample_ids"][sample_id] = cluster["sample_ids"].get(sample_id, 0) + 1
        if len(cluster["examples"]) < 4:
            cluster["examples"].append(
                {
                    "sample_id": sample_id,
                    "question": row.get("question", ""),
                    "expected": row.get("expected") or row.get("answer") or "",
                    "actual": row.get("response") or row.get("prediction") or row.get("answer") or "",
                }
            )
    ordered = sorted(clusters.values(), key=lambda item: item["count"], reverse=True)
    for cluster in ordered:
        cluster["top_samples"] = sorted(cluster.pop("sample_ids").items(), key=lambda item: item[1], reverse=True)[:5]
    return {"clusters": ordered, "cluster_count": len(ordered)}


def analyze_wrong_answers(csv_path: Path, out_path: Path | None = None) -> dict[str, Any]:
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    wrong = [row for row in rows if (row.get("result") or row.get("simple_grade") or row.get("simple_match") or "").upper() == "WRONG"]
    unresolved = [
        row
        for row in rows
        if (row.get("result") or row.get("simple_grade") or row.get("simple_match") or "").upper() in {"UNSCORED", "NEEDS_JUDGE", ""}
    ]
    modes: dict[str, int] = {}
    examples: dict[str, list[dict[str, str]]] = {}
    for row in wrong:
        mode = failure_mode(row)
        modes[mode] = modes.get(mode, 0) + 1
        examples.setdefault(mode, [])
        if len(examples[mode]) < 5:
            examples[mode].append(
                {
                    "sample_id": row.get("sample_id", ""),
                    "question_index": row.get("question_index", ""),
                    "category": row.get("category", ""),
                    "question": row.get("question", ""),
                    "gold": row.get("answer", ""),
                    "response": row.get("response", ""),
                    "reasoning": row.get("reasoning", ""),
                }
            )
    result = {
        "total": len(rows),
        "wrong": len(wrong),
        "unresolved": len(unresolved),
        "modes": modes,
        "examples": examples,
        "failure_attribution": attribution_summary(rows),
        "failure_clusters": cluster_failures(rows),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    brief_path = csv_path.with_name("wrong_questions_brief.csv")
    if wrong:
        with brief_path.open("w", encoding="utf-8", newline="") as f:
            fieldnames = ["sample_id", "question_id", "category", "question", "answer", "response", "reasoning"]
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in wrong:
                writer.writerow({name: row.get(name, "") for name in fieldnames})
        result["wrong_questions_brief"] = str(brief_path)
    if out_path:
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def wrong_clusters_for_csv(csv_path: Path) -> dict[str, Any]:
    analysis_path = csv_path.with_suffix(".wrong_analysis.json")
    if analysis_path.exists():
        analysis = read_json(analysis_path)
        if not isinstance(analysis, dict) or "failure_attribution" not in analysis:
            analysis = analyze_wrong_answers(csv_path, analysis_path)
    else:
        analysis = analyze_wrong_answers(csv_path, analysis_path)
    return {"input": str(csv_path), "analysis_path": str(analysis_path), "analysis": analysis}
