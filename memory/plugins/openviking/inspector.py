from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def compact(text: Any, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."


def count_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def normalize_sample_filter(sample: Any) -> str:
    value = str(sample or "").strip()
    return "" if value.lower() in {"all", "*", "全部"} else value


LOCOMO_EVIDENCE_PROBES: dict[str, list[dict[str, Any]]] = {
    "conv-30": [
        {"question_id": "conv-30_qa0", "groups": [["2023-01-19", "19 January"], ["banker"]]},
        {"question_id": "conv-30_qa5", "groups": [["water"], ["natural light"], ["Marley"]]},
        {"question_id": "conv-30_qa29", "groups": [["Paris"], ["Rome"]]},
        {"question_id": "conv-30_qa31", "groups": [["2023-01-19", "lost his job"], ["2023-06-20", "grand opening", "opening night"]]},
        {"question_id": "conv-30_qa39", "groups": [["Gina"], ["Contemporary"]]},
        {"question_id": "conv-30_qa40", "groups": [["Jon"], ["Contemporary"]]},
        {"question_id": "conv-30_qa46", "groups": [["Marley"]]},
        {"question_id": "conv-30_qa78", "groups": [["positivity"], ["determination"]]},
    ]
}


def list_imported_memories(workspace: Path, account: str, output_dir: Path, limit: int = 80, sample: str = "") -> dict[str, Any]:
    account = account or "default"
    sample = normalize_sample_filter(sample)
    root = workspace / "viking" / account
    session_root = root / "session"
    sessions: list[dict[str, Any]] = []
    if session_root.exists():
        for session_dir in sorted(session_root.glob("locomo-*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if sample and not session_dir.name.startswith(f"locomo-{sample}-"):
                continue
            history = session_dir / "history"
            history_files = [p for p in history.rglob("*") if p.is_file()] if history.exists() else []
            sessions.append(
                {
                    "session_id": session_dir.name,
                    "path": str(session_dir),
                    "history_path": str(history),
                    "history_files": len(history_files),
                    "updated_at": datetime.fromtimestamp(session_dir.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
            if len(sessions) >= limit:
                break
    session_ids = {item["session_id"] for item in sessions}

    summaries: list[dict[str, Any]] = []
    pattern = "openviking_import_*/openviking_import/openviking_import_summary.json"
    for summary_path in sorted(output_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            manifest_path = summary_path.parents[1] / "manifest.json"
            manifest = read_json(manifest_path) if manifest_path.exists() else {}
            manifest_config = manifest.get("config") or {}
            manifest_workspace = manifest_config.get("workspace")
            if manifest_workspace:
                try:
                    if Path(manifest_workspace).expanduser().resolve() != workspace.resolve():
                        continue
                except Exception:
                    continue
            summary = read_json(summary_path)
            record = (summary.get("records") or [{}])[0]
            if not manifest_workspace and record.get("session_id") not in session_ids:
                continue
            if sample and record.get("sample_id") != sample:
                continue
            record_account = (((record.get("create_response") or {}).get("user") or {}).get("account_id") or "")
            if record_account and record_account != account:
                continue
            summaries.append(
                {
                    "summary_path": str(summary_path),
                    "run_dir": str(summary_path.parents[1]),
                    "sample_id": record.get("sample_id", ""),
                    "session_id": record.get("session_id", ""),
                    "integrity": record.get("integrity", ""),
                    "submitted_messages": summary.get("submitted_messages"),
                    "expected_messages": summary.get("expected_messages"),
                    "memories_extracted": (record.get("session_after_commit") or {}).get("memories_extracted") or {},
                    "updated_at": datetime.fromtimestamp(summary_path.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
            if len(summaries) >= limit:
                break
        except Exception:
            continue

    return {
        "workspace": str(workspace),
        "account": account,
        "sample": sample,
        "account_path": str(root),
        "sessions": sessions,
        "summaries": summaries,
    }


def latest_import_summary_for(workspace: Path, account: str, output_dir: Path, sample: str = "") -> Path | None:
    imported = list_imported_memories(workspace, account, output_dir, 120, sample)
    summaries = imported.get("summaries") or []
    if not summaries:
        return None
    first = summaries[0]
    path = first.get("summary_path") or ""
    return Path(path) if path else None


def norm_for_probe(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def load_locomo_sample_for_probe(data_path: Path, sample_id: str) -> dict[str, Any] | None:
    try:
        data = read_json(data_path)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    for sample in data:
        if str(sample.get("sample_id") or "") == sample_id:
            return sample
    return None


def locomo_question_by_id(sample: dict[str, Any], sample_id: str, question_id: str) -> dict[str, Any] | None:
    prefix = f"{sample_id}_qa"
    if not question_id.startswith(prefix):
        return None
    try:
        index = int(question_id.removeprefix(prefix))
    except ValueError:
        return None
    qa = sample.get("qa") or []
    if index < 0 or index >= len(qa):
        return None
    return qa[index]


def locomo_evidence_text(sample: dict[str, Any], evidence: str) -> str:
    conv = sample.get("conversation") or {}
    try:
        day, offset = str(evidence).split(":")
        session_key = f"session_{int(day[1:])}"
        item = conv[session_key][int(offset) - 1]
    except Exception:
        return ""
    parts = [str(item.get("text") or "")]
    if item.get("blip_caption"):
        parts.append(f"image: {item['blip_caption']}")
    if item.get("query"):
        parts.append(f"query: {item['query']}")
    return " ".join(part for part in parts if part).strip()


def matched_probe_groups(text: str, groups: list[list[str]]) -> list[dict[str, Any]]:
    low = norm_for_probe(text)
    rows = []
    for group in groups:
        matched = [term for term in group if norm_for_probe(term) and norm_for_probe(term) in low]
        rows.append({"terms": group, "matched": matched, "ok": bool(matched)})
    return rows


def evidence_probe_diagnosis(status: str) -> tuple[str, str, str]:
    mapping = {
        "pass": (
            "storage_and_extraction_ok",
            "原始 session、长期 memory 和 gold evidence 都能对上。",
            "可以直接进入 QA/Judge；如果回答仍错，优先看回答 prompt 或 judge。",
        ),
        "partial": (
            "extraction_loss",
            "OpenViking archive 里有原始对话，但长期 memory 只保留了部分 evidence。",
            "重导入前优先检查抽取 prompt/schema；让 commit_session 保留完整属性、地点、材质、日期和说话人。",
        ),
        "fact_only": (
            "summarized_memory",
            "长期 memory 里有关键词或事实，但 gold evidence 原文没有完整保留。",
            "QA 可能答对，但可解释性弱；建议在抽取侧保留可回答 benchmark 的具体短语。",
        ),
        "archive_only": (
            "not_extracted_to_memory",
            "原始 archive 有证据，长期 memory 没抽出来。",
            "这是抽取覆盖问题，不是导入丢消息；需要修改 OpenViking extraction guidance 后重新 commit。",
        ),
        "missing": (
            "storage_or_dataset_mismatch",
            "archive 和长期 memory 都没找到该证据。",
            "先检查是否导错 conversation、workspace/account 是否一致，再检查导入 summary 和 session history。",
        ),
    }
    return mapping.get(status, ("unknown", "状态未知。", "先查看 archive、memory root 和 summary。"))


def import_evidence_probe(workspace: Path, account: str, user_id: str, sample_id: str, data_path: Path) -> dict[str, Any]:
    sample = load_locomo_sample_for_probe(data_path, sample_id)
    if not sample:
        return {"enabled": False, "sample_id": sample_id, "reason": "no matching LoCoMo sample found"}
    probes = LOCOMO_EVIDENCE_PROBES.get(sample_id)
    if not probes:
        return {"enabled": False, "sample_id": sample_id, "reason": "no predefined probe cases for this sample"}
    account_root = workspace / "viking" / account
    memory_root = account_root / "user" / user_id / "memories"
    session_root = account_root / "session"
    memory_text_parts: list[str] = []
    if memory_root.exists():
        for path in memory_root.rglob("*.md"):
            if path.is_file() and not path.name.startswith("."):
                try:
                    memory_text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    pass
    archive_text_parts: list[str] = []
    if session_root.exists():
        for path in session_root.glob(f"locomo-{sample_id}-*/history/archive_001/messages.jsonl"):
            try:
                archive_text_parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    memory_text = "\n".join(memory_text_parts)
    archive_text = "\n".join(archive_text_parts)
    results = []
    for probe in probes:
        question_id = str(probe.get("question_id") or "")
        qa = locomo_question_by_id(sample, sample_id, question_id)
        if not qa:
            continue
        groups = probe.get("groups") or []
        memory_groups = matched_probe_groups(memory_text, groups)
        archive_groups = matched_probe_groups(archive_text, groups)
        evidence = [str(item) for item in qa.get("evidence") or []]
        evidence_texts = [locomo_evidence_text(sample, item) for item in evidence]
        evidence_memory_hits = sum(1 for text in evidence_texts if norm_for_probe(text) and norm_for_probe(text) in norm_for_probe(memory_text))
        evidence_archive_hits = sum(1 for text in evidence_texts if norm_for_probe(text) and norm_for_probe(text) in norm_for_probe(archive_text))
        memory_ok = all(item["ok"] for item in memory_groups)
        archive_ok = all(item["ok"] for item in archive_groups)
        exact_evidence_ok = not evidence_texts or evidence_memory_hits == len(evidence_texts)
        archive_exact_ok = not evidence_texts or evidence_archive_hits == len(evidence_texts)
        if memory_ok and exact_evidence_ok:
            status = "pass"
        elif evidence_memory_hits > 0:
            status = "partial"
        elif memory_ok:
            status = "fact_only"
        elif archive_ok or archive_exact_ok:
            status = "archive_only"
        else:
            status = "missing"
        diagnosis, diagnosis_detail, recommended_action = evidence_probe_diagnosis(status)
        results.append(
            {
                "question_id": question_id,
                "question": qa.get("question"),
                "gold": qa.get("answer"),
                "category": qa.get("category"),
                "status": status,
                "diagnosis": diagnosis,
                "diagnosis_detail": diagnosis_detail,
                "recommended_action": recommended_action,
                "expected_groups": groups,
                "memory_groups": memory_groups,
                "archive_groups": archive_groups,
                "evidence_total": len(evidence_texts),
                "evidence_memory_hits": evidence_memory_hits,
                "evidence_archive_hits": evidence_archive_hits,
                "memory_exact_complete": exact_evidence_ok,
                "archive_exact_complete": archive_exact_ok,
            }
        )
    counts = {key: sum(1 for item in results if item["status"] == key) for key in ["pass", "partial", "fact_only", "archive_only", "missing"]}
    return {
        "enabled": True,
        "sample_id": sample_id,
        "memory_root": str(memory_root),
        "session_root": str(session_root),
        "counts": counts,
        "results": results,
    }


def import_integrity(
    workspace: Path,
    account: str,
    output_dir: Path,
    data_path: Path,
    sample: str = "",
    summary_path: Path | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    account = account or "default"
    sample = normalize_sample_filter(sample)
    if not summary_path or not summary_path.exists():
        summary_path = latest_import_summary_for(workspace, account, output_dir, sample)
    if not summary_path or not summary_path.exists():
        raise FileNotFoundError("没有找到匹配当前 workspace/account/sample 的导入 summary")

    summary = read_json(summary_path)
    records = summary.get("records") or []
    account_root = workspace / "viking" / account
    session_root = account_root / "session"
    memory_root = account_root / "user" / user_id / "memories"
    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, message: str, level: str = "") -> None:
        checks.append({"name": name, "ok": ok, "level": level or ("ok" if ok else "fail"), "message": message})

    expected = int(summary.get("expected_messages") or 0)
    submitted = int(summary.get("submitted_messages") or 0)
    incomplete = int(summary.get("incomplete_samples") or 0)
    add_check("Summary 消息数", bool(expected and submitted == expected), f"submitted={submitted} / expected={expected}")
    add_check(
        "Summary 完整性",
        incomplete == 0 and str(summary.get("status") or "").endswith("_DONE"),
        f"status={summary.get('status') or '-'} · incomplete_samples={incomplete}",
    )

    session_checks: list[dict[str, Any]] = []
    total_expected = 0
    total_submitted = 0
    total_pending = 0
    missing_sessions = 0
    empty_history = 0
    for record in records:
        session_records = record.get("session_records") or [record]
        for session in session_records:
            session_id = session.get("session_id") or ""
            if not session_id:
                continue
            expected_messages = int(session.get("expected_messages") or 0)
            submitted_messages = int(session.get("submitted_messages") or 0)
            pending = int(session.get("pending_message_count_after_commit") or 0)
            total_expected += expected_messages
            total_submitted += submitted_messages
            total_pending += pending
            session_dir = session_root / session_id
            history_dir = session_dir / "history"
            history_files = count_files(history_dir)
            exists = session_dir.exists()
            if not exists:
                missing_sessions += 1
            if exists and history_files == 0:
                empty_history += 1
            session_checks.append(
                {
                    "session_id": session_id,
                    "session_key": session.get("session_key") or "",
                    "expected_messages": expected_messages,
                    "submitted_messages": submitted_messages,
                    "pending_after_commit": pending,
                    "integrity": session.get("integrity") or "",
                    "session_path": str(session_dir),
                    "history_path": str(history_dir),
                    "exists": exists,
                    "history_files": history_files,
                    "ok": exists and history_files > 0 and submitted_messages == expected_messages and pending == 0,
                }
            )

    add_check("Session 目录", bool(session_checks) and missing_sessions == 0, f"sessions={len(session_checks)} · missing={missing_sessions}")
    add_check("History 落盘", bool(session_checks) and empty_history == 0, f"empty_history_sessions={empty_history}")
    add_check("Commit 后待处理", total_pending == 0, f"pending_after_commit={total_pending}")
    add_check("Session 消息汇总", bool(total_expected) and total_expected == total_submitted, f"submitted={total_submitted} / expected={total_expected}")

    memory_files = count_files(memory_root)
    extracted_total = 0
    for record in records:
        extracted = ((record.get("session_after_commit") or {}).get("memories_extracted") or {})
        try:
            extracted_total += int(extracted.get("total") or 0)
        except Exception:
            pass
    if extracted_total == 0:
        for record in records:
            for session in record.get("session_records") or []:
                extracted = ((session.get("session_after_commit") or {}).get("memories_extracted") or {})
                try:
                    extracted_total += int(extracted.get("total") or 0)
                except Exception:
                    pass
    memory_ok = memory_root.exists() and memory_files > 0
    add_check(
        "Memory 文件",
        memory_ok,
        f"memory_files={memory_files} · summary_extracted={extracted_total}",
        "ok" if memory_ok else "warn",
    )

    failed = [item for item in checks if item["level"] == "fail" or item["ok"] is False and item["level"] != "warn"]
    warnings = [item for item in checks if item["level"] == "warn" and not item["ok"]]
    evidence_probe = import_evidence_probe(workspace, account, user_id, sample, data_path) if sample else {"enabled": False}
    if evidence_probe.get("enabled"):
        missing = int((evidence_probe.get("counts") or {}).get("missing") or 0)
        partial = int((evidence_probe.get("counts") or {}).get("partial") or 0)
        fact_only = int((evidence_probe.get("counts") or {}).get("fact_only") or 0)
        archive_only = int((evidence_probe.get("counts") or {}).get("archive_only") or 0)
        add_check(
            "LoCoMo Evidence Probe",
            missing == 0 and archive_only == 0 and fact_only == 0 and partial == 0,
            f"pass={(evidence_probe.get('counts') or {}).get('pass', 0)} · partial={partial} · fact_only={fact_only} · archive_only={archive_only} · missing={missing}",
            "warn" if missing or archive_only or fact_only or partial else "ok",
        )
        warnings = [item for item in checks if item["level"] == "warn" and not item["ok"]]
    status = "complete" if not failed and not warnings else ("warning" if not failed else "incomplete")
    return {
        "status": status,
        "workspace": str(workspace),
        "account": account,
        "sample": sample,
        "summary_path": str(summary_path),
        "account_path": str(account_root),
        "session_root": str(session_root),
        "memory_root": str(memory_root),
        "expected_messages": expected,
        "submitted_messages": submitted,
        "session_count": len(session_checks),
        "memory_files": memory_files,
        "summary_extracted_memories": extracted_total,
        "checks": checks,
        "sessions": session_checks,
        "evidence_probe": evidence_probe,
    }


def session_browser(workspace: Path, account: str, sample: str = "", limit: int = 120) -> dict[str, Any]:
    account = account or "default"
    sample = normalize_sample_filter(sample)
    root = workspace / "viking" / account
    session_root = root / "session"
    rows: list[dict[str, Any]] = []
    if session_root.exists():
        for session_dir in sorted(session_root.glob("locomo-*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if sample and not session_dir.name.startswith(f"locomo-{sample}-"):
                continue
            files = [p for p in session_dir.rglob("*") if p.is_file()]
            history_files = [p for p in files if "/history/" in str(p)]
            archive_files = [p for p in history_files if "archive" in p.name]
            rows.append(
                {
                    "session_id": session_dir.name,
                    "path": str(session_dir),
                    "history_path": str(session_dir / "history"),
                    "history_files": len(history_files),
                    "archive_files": len(archive_files),
                    "files": len(files),
                    "updated_at": datetime.fromtimestamp(session_dir.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
            if len(rows) >= limit:
                break
    return {"workspace": str(workspace), "account": account, "sample": sample, "sessions": rows}


def memory_kind_for_path(path: Path, memory_root: Path) -> str:
    try:
        rel = path.relative_to(memory_root)
    except ValueError:
        return "memory"
    parts = rel.parts
    return parts[0] if parts else "memory"


def memory_timeline(workspace: Path, account: str, user_id: str = "default", query: str = "", limit: int = 200) -> dict[str, Any]:
    account = account or "default"
    memory_root = workspace / "viking" / account / "user" / user_id / "memories"
    rows: list[dict[str, Any]] = []
    q = query.strip().lower()
    if memory_root.exists():
        for path in memory_root.rglob("*.md"):
            if path.name.startswith("."):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            haystack = f"{path.name} {text}".lower()
            if q and q not in haystack:
                continue
            date = ""
            match = re.search(r"/events/(\d{4})/(\d{2})/(\d{2})/", str(path))
            if match:
                date = "-".join(match.groups())
            first_title = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), path.stem)
            uri = "viking://user/default/memories/" + str(path.relative_to(memory_root)).replace(os.sep, "/")
            rows.append(
                {
                    "date": date or "undated",
                    "kind": memory_kind_for_path(path, memory_root),
                    "title": first_title[:160],
                    "uri": uri,
                    "path": str(path),
                    "chars": len(text),
                    "snippet": compact(text, 420),
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
    rows.sort(key=lambda item: (item["date"] == "undated", item["date"], item["path"]), reverse=False)
    by_date: dict[str, int] = {}
    for row in rows:
        by_date[row["date"]] = by_date.get(row["date"], 0) + 1
    return {
        "workspace": str(workspace),
        "account": account,
        "memory_root": str(memory_root),
        "count": len(rows),
        "by_date": by_date,
        "items": rows[:limit],
    }


def read_memory_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"path": str(path), "name": path.name, "chars": len(text), "text": text}
