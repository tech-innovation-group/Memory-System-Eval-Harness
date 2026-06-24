from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


DATASET_CANDIDATES = [
    Path(__file__).resolve().parents[3] / "dataset" / "full" / "locomo.json",
    Path(__file__).resolve().parents[3] / "dataset" / "locomo.json",
    Path(__file__).resolve().parents[3] / "dataset" / "locomo10.json",
    Path(__file__).resolve().parents[3] / "benchmark" / "locomo" / "data" / "locomo10.json",
    Path.cwd() / "dataset" / "full" / "locomo.json",
    Path.cwd() / "dataset" / "locomo.json",
    Path.cwd() / "dataset" / "locomo10.json",
]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_sample_filter(sample: Any) -> str:
    value = str(sample or "").strip()
    return "" if value.lower() in {"all", "*", "全部"} else value


def count_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def engine_root_candidates(account_root: Path) -> list[Path]:
    return [
        account_root / "engines" / "echo0_plugin",
    ]


def memory_root_candidates(account_root: Path) -> list[Path]:
    roots = [
        account_root / "engines" / "echo0_plugin" / "memory",
        account_root / "memory",
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in roots:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def preferred_memory_root(account_root: Path) -> Path:
    candidates = memory_root_candidates(account_root)
    for path in candidates:
        if path.exists() and count_files(path) > 0:
            return path
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def session_projection_dir_candidates(account_root: Path, session_id: str) -> list[Path]:
    roots = [
        account_root / "engines" / "echo0_plugin" / "sessions" / session_id,
        account_root / "sessions" / session_id,
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in roots:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def session_projection_file(account_root: Path, session_id: str, name: str) -> Path | None:
    for session_root in session_projection_dir_candidates(account_root, session_id):
        candidate = session_root / name
        if candidate.exists():
            return candidate
    return None


def engine_commit_status_payload(account_root: Path, session_id: str, archive_id: str = "archive_001") -> dict[str, Any]:
    for engine_root in engine_root_candidates(account_root):
        path = engine_root / "commits" / f"{session_id}__{archive_id}.status.json"
        if not path.exists():
            continue
        try:
            payload = read_json(path)
        except Exception:
            continue
        return payload if isinstance(payload, dict) else {}
    return {}


def structured_atom_entries(account_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    structured_root = preferred_memory_root(account_root) / ".structured"
    atom_dir = structured_root / "atoms"
    if atom_dir.exists():
        for path in sorted(atom_dir.glob("*.json")):
            try:
                data = read_json(path)
            except Exception:
                continue
            if isinstance(data, dict):
                rows.append(data)
    atom_json = structured_root / "atoms.json"
    if atom_json.exists():
        try:
            payload = read_json(atom_json)
        except Exception:
            payload = {}
        atoms = payload.get("atoms") if isinstance(payload, dict) else payload
        if isinstance(atoms, dict):
            rows.extend(item for item in atoms.values() if isinstance(item, dict))
        elif isinstance(atoms, list):
            rows.extend(item for item in atoms if isinstance(item, dict))
    return rows


def compact(text: Any, limit: int = 420) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def matched_probe_groups(text: str, groups: list[list[str]]) -> list[dict[str, Any]]:
    low = normalize_text(text)
    rows = []
    for group in groups:
        matched = [term for term in group if normalize_text(term) and normalize_text(term) in low]
        rows.append({"terms": group, "matched": matched, "ok": bool(matched)})
    return rows


def gap_probe_diagnosis(status: str) -> tuple[str, str, str]:
    mapping = {
        "pass": (
            "atom_extraction_ok",
            "原始 session、atoms 和派生 memory 都能对上这道题的核心事实。",
            "这类题的长期记忆抽取链路基本健康，可以继续看检索排序或回答阶段。",
        ),
        "partial": (
            "partial_atom_coverage",
            "长期记忆里只抽出了部分关键词，缺少回答这道题需要的完整事实。",
            "优先检查 atom 抽取提示词、分句和去重逻辑，避免关键成分被压缩掉。",
        ),
        "fact_only": (
            "derived_memory_without_atoms",
            "events / episodes 等派生记忆里有相关事实，但 atoms 层没有完整抽出来。",
            "说明 commit 后下游产物有信息，但基础原子层覆盖不足，建议优先修 atom flush。",
        ),
        "archive_only": (
            "not_extracted_to_long_memory",
            "原始 session/summary 里有答案线索，但长期记忆没有抽出来。",
            "这是抽取覆盖问题，先修导入/commit 完整性，再谈检索和回答。",
        ),
        "missing": (
            "source_and_memory_mismatch",
            "当前 workspace 里连原始 session 都没稳定命中这些事实，可能是 sample/session 对不上或导入不完整。",
            "先核对 workspace、account、sample 和导入 summary，再看抽取问题。",
        ),
    }
    return mapping.get(status, ("unknown", "状态未知。", "先检查 session、atoms 和 memory 根目录。"))


def load_locomo_dataset() -> list[dict[str, Any]]:
    for path in DATASET_CANDIDATES:
        if not path.exists():
            continue
        try:
            data = read_json(path)
        except Exception:
            continue
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def find_sample_in_dataset(dataset: list[dict[str, Any]], sample: str) -> dict[str, Any] | None:
    sample = str(sample or "").strip()
    if not sample:
        return None
    for index, item in enumerate(dataset):
        sample_id = str(item.get("sample_id") or f"sample_{index}")
        if sample_id == sample or str(index) == sample:
            return item
    return None


def session_text_bundle(session_dir: Path) -> tuple[str, str]:
    message_texts: list[str] = []
    summary_texts: list[str] = []
    messages_path = session_primary_messages_path(session_dir)
    if messages_path.exists():
        try:
            with messages_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if item.get("content"):
                        message_texts.append(str(item.get("content")))
                    for part in item.get("parts") or []:
                        if isinstance(part, dict) and part.get("text"):
                            message_texts.append(str(part.get("text")))
        except Exception:
            pass
    for path in session_summary_paths(session_dir):
        if path.exists():
            try:
                summary_texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    return normalize_text("\n".join(message_texts)), normalize_text("\n".join(summary_texts))


def expected_sample_terms(sample_record: dict[str, Any]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for index, qa in enumerate(sample_record.get("qa") or []):
        question_id = f"{sample_record.get('sample_id')}_qa{index}"
        answer = str(qa.get("answer") or "").strip()
        if not answer or len(answer) < 3:
            continue
        answer_terms = [part.strip() for part in re.split(r"[,;/]| and |，|、", answer) if len(part.strip()) >= 3]
        terms.append(
            {
                "question_id": question_id,
                "answer": answer,
                "question": str(qa.get("question") or ""),
                "terms": answer_terms[:4] or [answer],
            }
        )
    return terms


def sample_memory_texts(account_root: Path) -> tuple[str, str]:
    atom_texts: list[str] = []
    memory_texts: list[str] = []
    memory_root = preferred_memory_root(account_root)
    for item in structured_atom_entries(account_root):
        atom_texts.append(str(item.get("statement") or item.get("content") or ""))
    for pattern in [
        memory_root / "events",
        memory_root / "entities",
        memory_root / ".episodes" / "episodes",
        memory_root / "session",
    ]:
        if not pattern.exists():
            continue
        for path in sorted(pattern.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".json"}:
                continue
            try:
                memory_texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
    return normalize_text("\n".join(atom_texts)), normalize_text("\n".join(memory_texts))


def gold_atom_gap_probe(workspace: Path, account: str, sample: str) -> dict[str, Any]:
    dataset = load_locomo_dataset()
    sample_record = find_sample_in_dataset(dataset, sample)
    if not sample_record:
        return {"enabled": False, "sample_id": sample, "reason": "LoCoMo dataset sample not found for gold-vs-atom probe."}
    account_root = first_existing_root(workspace, account)
    atom_text, memory_text = sample_memory_texts(account_root)
    session_root = account_root / "sessions"
    session_text = ""
    session_summary_text = ""
    if session_root.exists():
        for session_dir in sorted(path for path in session_root.iterdir() if path.is_dir()):
            if sample and sample not in session_dir.name:
                meta = read_session_metadata(session_dir)
                title = session_title_from_meta(meta, session_dir.name)
                if not session_title_matches_sample(title, sample):
                    continue
            msg_text, sum_text = session_text_bundle(session_dir)
            session_text += "\n" + msg_text
            session_summary_text += "\n" + sum_text
    session_text = normalize_text(session_text)
    session_summary_text = normalize_text(session_summary_text)

    gap_items: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    session_hit_count = 0
    atom_hit_count = 0
    memory_hit_count = 0
    for item in expected_sample_terms(sample_record):
        raw_terms = [str(term).strip() for term in item.get("terms") or [] if str(term).strip()]
        terms = [normalize_text(term) for term in raw_terms if normalize_text(term)]
        if not terms or not raw_terms:
            continue
        groups = [[term] for term in raw_terms]
        session_groups = matched_probe_groups(f"{session_text}\n{session_summary_text}", groups)
        atom_groups = matched_probe_groups(atom_text, groups)
        memory_groups = matched_probe_groups(memory_text, groups)
        in_session = all(group["ok"] for group in session_groups)
        in_atom = all(group["ok"] for group in atom_groups)
        in_memory = all(group["ok"] for group in memory_groups)
        session_hit_count += int(in_session)
        atom_hit_count += int(in_atom)
        memory_hit_count += int(in_memory)
        evidence_total = len(groups)
        evidence_archive_hits = sum(1 for group in session_groups if group["ok"])
        evidence_atom_hits = sum(1 for group in atom_groups if group["ok"])
        evidence_memory_hits = sum(1 for group in memory_groups if group["ok"])
        if in_atom:
            status = "pass"
        elif in_memory:
            status = "fact_only"
        elif evidence_atom_hits > 0 or evidence_memory_hits > 0:
            status = "partial"
        elif in_session or evidence_archive_hits > 0:
            status = "archive_only"
        else:
            status = "missing"
        diagnosis, diagnosis_detail, recommended_action = gap_probe_diagnosis(status)
        results.append(
            {
                "question_id": item["question_id"],
                "question": item["question"],
                "gold": item["answer"],
                "status": status,
                "diagnosis": diagnosis,
                "diagnosis_detail": diagnosis_detail,
                "recommended_action": recommended_action,
                "expected_groups": groups,
                "memory_groups": atom_groups,
                "archive_groups": session_groups,
                "derived_memory_groups": memory_groups,
                "evidence_total": evidence_total,
                "evidence_memory_hits": evidence_atom_hits,
                "evidence_archive_hits": evidence_archive_hits,
                "memory_exact_complete": in_atom,
                "archive_exact_complete": in_session,
            }
        )
        if in_session and not in_atom:
            gap_items.append(
                {
                    "question_id": item["question_id"],
                    "question": item["question"],
                    "answer": item["answer"],
                    "terms": item["terms"],
                    "session_has_gold": True,
                    "atom_has_gold": False,
                    "memory_has_gold": in_memory,
                }
            )
    counts = {key: sum(1 for row in results if row["status"] == key) for key in ["pass", "partial", "fact_only", "archive_only", "missing"]}
    return {
        "enabled": True,
        "sample_id": sample,
        "session_hit_count": session_hit_count,
        "atom_hit_count": atom_hit_count,
        "memory_hit_count": memory_hit_count,
        "gap_count": len(gap_items),
        "gaps": gap_items[:30],
        "counts": counts,
        "results": results,
    }


def account_root_candidates(workspace: Path, account: str) -> list[Path]:
    account = account or "default"
    return [
        workspace / "tenants" / account,
        workspace / account / account,
        workspace / account,
        workspace,
    ]


def first_existing_root(workspace: Path, account: str) -> Path:
    for candidate in account_root_candidates(workspace, account):
        if (
            (candidate / "sessions").exists()
            or (candidate / "memory").exists()
            or (candidate / "engines" / "echo0_plugin" / "sessions").exists()
            or (candidate / "engines" / "echo0_plugin" / "memory").exists()
        ):
            return candidate
    for candidate in account_root_candidates(workspace, account):
        if candidate.exists():
            return candidate
    return account_root_candidates(workspace, account)[0]


def session_dir_candidates(workspace: Path, account: str) -> list[Path]:
    candidates: list[Path] = []
    for root in account_root_candidates(workspace, account):
        candidates.append(root / "sessions")
        candidates.append(root / "engines" / "echo0_plugin" / "sessions")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def session_dir_for(workspace: Path, account: str, session_id: str) -> Path:
    for root in session_dir_candidates(workspace, account):
        candidate = root / session_id
        if candidate.exists():
            return candidate
    return first_existing_root(workspace, account) / "sessions" / session_id


def session_id_matches_sample(session_id: str, sample: str) -> bool:
    if not sample:
        return True
    return sample in session_id or f"locomo-{sample}" in session_id


def session_summary_paths(session_dir: Path) -> list[Path]:
    candidates = [
        session_dir / "overview.md",
        session_dir / "abstract.md",
        session_dir / "current" / "overview.md",
        session_dir / "current" / "abstract.md",
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def session_metadata_path(session_dir: Path) -> Path:
    for candidate in (
        session_dir / "meta.json",
        session_dir / "current" / "session.json",
        session_dir / "session.json",
    ):
        if candidate.exists():
            return candidate
    return session_dir / "meta.json"


def read_session_metadata(session_dir: Path) -> dict[str, Any]:
    path = session_metadata_path(session_dir)
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def session_title_from_meta(meta: dict[str, Any], fallback: str = "") -> str:
    if not isinstance(meta, dict):
        return fallback
    title = meta.get("title")
    if not title and isinstance(meta.get("metadata"), dict):
        title = meta["metadata"].get("title")
    return str(title or fallback or "")


def session_title_matches_sample(title: str, sample: str) -> bool:
    if not sample:
        return True
    title_text = str(title or "")
    return sample in title_text or sample.replace("conv-", "conv_") in title_text


def session_message_paths(session_dir: Path) -> list[Path]:
    candidates: list[Path] = [
        session_dir / "messages.jsonl",
        session_dir / "current" / "messages.jsonl",
    ]
    history_root = session_dir / "history"
    if history_root.exists():
        candidates.extend(
            sorted(
                history_root.glob("archive_*/messages.jsonl"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            )
        )
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def session_archive_message_paths(session_dir: Path) -> list[Path]:
    history_root = session_dir / "history"
    if not history_root.exists():
        return []
    paths = sorted(
        history_root.glob("archive_*/messages.jsonl"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def session_primary_messages_path(session_dir: Path) -> Path:
    paths = session_message_paths(session_dir)
    return paths[0] if paths else (session_dir / "messages.jsonl")


def count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except Exception:
        return 0


def summary_matches_workspace(summary: dict[str, Any], workspace: Path) -> bool:
    raw = str(summary.get("workspace") or "").strip()
    if not raw:
        return True
    try:
        return Path(raw).expanduser().resolve() == workspace.expanduser().resolve()
    except Exception:
        return raw.rstrip("/") == str(workspace).rstrip("/")


def summary_matches_account(summary: dict[str, Any], account: str) -> bool:
    raw = str(summary.get("account") or "").strip()
    return not raw or raw == (account or "default")


def iter_import_summaries(output_dir: Path) -> list[Path]:
    patterns = [
        "echomemory_import_*/echomemory_import/echomemory_import_summary.json",
        "echomemory_import_*/echomemory_import_summary.json",
        "echomemory_*/import/echomemory_import_summary.json",
        "echomemory_import_script_*/echomemory_import_summary.json",
        "echomemory_*/echomemory_import_summary.json",
        "echomemory_*/echomemory_import/echomemory_import_summary.json",
        "*/echomemory_import_summary.json",
        "*/echomemory_import/echomemory_import_summary.json",
    ]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(output_dir.glob(pattern))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in sorted(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def sample_records(summary: dict[str, Any], sample: str) -> list[dict[str, Any]]:
    records = summary.get("records") or []
    if not sample:
        return list(records)
    return [record for record in records if str(record.get("sample_id") or "") == sample]


def session_ids_for_records(records: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for record in records:
        for session in record.get("session_records") or [record]:
            session_id = str(session.get("session_id") or "")
            if session_id:
                ids.add(session_id)
    return ids


def list_imported_memories(workspace: Path, account: str, output_dir: Path, limit: int = 80, sample: str = "") -> dict[str, Any]:
    account = account or "default"
    sample = normalize_sample_filter(sample)
    account_root = first_existing_root(workspace, account)
    matching_session_ids: set[str] = set()

    summaries: list[dict[str, Any]] = []
    seen_summary_keys: set[str] = set()
    for summary_path in iter_import_summaries(output_dir):
        try:
            summary = read_json(summary_path)
        except Exception:
            continue
        if not summary_matches_workspace(summary, workspace) or not summary_matches_account(summary, account):
            continue
        matched = sample_records(summary, sample)
        if sample and not matched:
            continue
        for record in matched or [{}]:
            record_key = str(record.get("sample_id") or record.get("session_id") or summary_path)
            if record_key in seen_summary_keys:
                continue
            seen_summary_keys.add(record_key)
            matching_session_ids.update(session_ids_for_records([record] if record else []))
            summaries.append(
                {
                    "summary_path": str(summary_path),
                    "run_dir": str(summary_path.parents[1]) if len(summary_path.parents) > 1 else str(summary_path.parent),
                    "sample_id": record.get("sample_id", ""),
                    "session_id": record.get("session_id", ""),
                    "integrity": record.get("integrity") or ("complete" if not summary.get("incomplete_samples") else "incomplete"),
                    "integrity_stage": record.get("integrity_stage") or summary.get("status") or "",
                    "submitted_messages": record.get("submitted_messages", summary.get("submitted_messages")),
                    "expected_messages": record.get("expected_messages", summary.get("expected_messages")),
                    "session_count": record.get("session_count"),
                    "memories_extracted": {
                        "total": sum(
                            int(((session.get("atom_flush") or {}).get("attempts") or [{}])[-1].get("atoms_added") or 0)
                            for session in (record.get("session_records") or [])
                        )
                    },
                    "updated_at": datetime.fromtimestamp(summary_path.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
            if len(summaries) >= limit:
                break
        if len(summaries) >= limit:
            break

    sessions: list[dict[str, Any]] = []
    seen_session_keys: set[str] = set()
    for root in session_dir_candidates(workspace, account):
        if not root.exists():
            continue
        for session_dir in sorted(root.glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not session_dir.is_dir():
                continue
            meta = read_session_metadata(session_dir)
            title = session_title_from_meta(meta, session_dir.name)
            session_key = title or session_dir.name
            if session_key in seen_session_keys:
                continue
            matches_sample = session_id_matches_sample(session_dir.name, sample) or session_title_matches_sample(title, sample)
            if matching_session_ids:
                matches_sample = session_dir.name in matching_session_ids
            if not matches_sample:
                continue
            seen_session_keys.add(session_key)
            archived_message_files = session_archive_message_paths(session_dir)
            has_committed_archive = bool(archived_message_files)
            if not has_committed_archive and session_dir.name not in matching_session_ids:
                # EchoMem_develop writes current/session.json and current/messages.jsonl
                # before commit. Only committed archive files count as imported memory.
                continue
            files = [p for p in session_dir.rglob("*") if p.is_file()]
            message_files = archived_message_files or session_message_paths(session_dir)
            message_file = message_files[0] if message_files else session_primary_messages_path(session_dir)
            meta_file = session_metadata_path(session_dir)
            stored_messages = count_jsonl_rows(message_file)
            sessions.append(
                {
                    "session_id": session_dir.name,
                    "session_key": session_key,
                    "path": str(session_dir),
                    "history_path": str(message_file) if message_file.exists() else "",
                    "history_files": len(archived_message_files) if archived_message_files else len(message_files),
                    "files": len(files),
                    "stored_messages": stored_messages,
                    "meta_path": str(meta_file) if meta_file.exists() else "",
                    "has_archive": has_committed_archive,
                    "updated_at": datetime.fromtimestamp(session_dir.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
            if len(sessions) >= limit:
                break
        if len(sessions) >= limit:
            break

    return {
        "backend": "echomemory",
        "workspace": str(workspace),
        "account": account,
        "sample": sample,
        "account_path": str(account_root),
        "memory_root": str(preferred_memory_root(account_root)),
        "sessions": sessions,
        "summaries": summaries,
    }


def latest_import_summary_for(workspace: Path, account: str, output_dir: Path, sample: str = "") -> Path | None:
    imported = list_imported_memories(workspace, account, output_dir, 120, sample)
    summaries = imported.get("summaries") or []
    if not summaries:
        return None
    path = summaries[0].get("summary_path") or ""
    return Path(path) if path else None


def add_check(checks: list[dict[str, Any]], name: str, ok: bool, message: str, level: str = "") -> None:
    checks.append({"name": name, "ok": ok, "level": level or ("ok" if ok else "fail"), "message": message})


def session_record_checks(workspace: Path, account: str, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    account_root = first_existing_root(workspace, account)
    totals = {
        "expected": 0,
        "submitted": 0,
        "pending": 0,
        "missing_sessions": 0,
        "empty_message_files": 0,
        "incomplete_commits": 0,
        "incomplete_atom_flush": 0,
    }
    for record in records:
        for session in record.get("session_records") or [record]:
            session_id = str(session.get("session_id") or "")
            if not session_id:
                continue
            expected = int(session.get("expected_messages") or 0)
            submitted = int(session.get("submitted_messages") or 0)
            pending = int(session.get("pending_message_count_after_commit") or 0)
            totals["expected"] += expected
            totals["submitted"] += submitted
            totals["pending"] += pending
            session_dir = session_dir_for(workspace, account, session_id)
            messages_path = session_primary_messages_path(session_dir)
            meta_path = session_metadata_path(session_dir)
            exists = session_dir.exists()
            if not exists:
                totals["missing_sessions"] += 1
            message_files = len(session_message_paths(session_dir))
            if exists and not message_files:
                totals["empty_message_files"] += 1
            commit_artifacts = session.get("commit_artifacts") or {}
            atom_flush = session.get("atom_flush") or {}
            engine_commit = engine_commit_status_payload(account_root, session_id)
            engine_completed = str(engine_commit.get("status") or "").lower() == "completed"
            commit_complete = bool(commit_artifacts.get("complete", session.get("archive_complete_after_commit")) or engine_completed)
            session_cursor = str(commit_artifacts.get("session_last_extracted_turn_id") or "")
            expected_cursor = str(commit_artifacts.get("expected_last_message_id") or session.get("last_added_message_id") or "")
            session_cursor_ok = bool(session_cursor) and (not expected_cursor or session_cursor == expected_cursor)
            atom_complete = bool(
                atom_flush.get("complete")
                or commit_artifacts.get("atom_last_extracted_turn_id_ok")
                or session_cursor_ok
                or engine_completed
            )
            if not commit_complete:
                totals["incomplete_commits"] += 1
            if not atom_complete:
                totals["incomplete_atom_flush"] += 1
            ok = exists and message_files > 0 and submitted == expected and commit_complete and atom_complete
            rows.append(
                {
                    "session_id": session_id,
                    "session_key": session.get("session_key") or record.get("sample_id") or "",
                    "expected_messages": expected,
                    "submitted_messages": submitted,
                    "pending_after_commit": pending,
                    "integrity": session.get("integrity") or record.get("integrity") or "",
                    "session_path": str(session_dir),
                    "history_path": str(messages_path),
                    "meta_path": str(meta_path) if meta_path.exists() else "",
                    "exists": exists,
                    "history_files": message_files,
                    "commit_complete": commit_complete,
                    "atom_flush_complete": atom_complete,
                    "ok": ok,
                }
            )
    return rows, totals


def current_session_snapshot(workspace: Path, account: str, sample: str = "") -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = first_existing_root(workspace, account)
    sample_text = normalize_sample_filter(sample)
    rows: list[dict[str, Any]] = []
    totals = {
        "expected": 0,
        "submitted": 0,
        "sessions": 0,
        "complete": 0,
        "incomplete": 0,
    }
    session_roots = [path for path in session_dir_candidates(workspace, account) if path.exists()]
    if not session_roots:
        return rows, totals
    seen_session_ids: set[str] = set()
    session_dirs: list[Path] = []
    for session_root in session_roots:
        for path in session_root.iterdir():
            if not path.is_dir():
                continue
            if path.name in seen_session_ids:
                continue
            seen_session_ids.add(path.name)
            session_dirs.append(path)
    for session_dir in sorted(session_dirs, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        meta_path = session_metadata_path(session_dir)
        meta = read_session_metadata(session_dir)
        title = session_title_from_meta(meta, session_dir.name)
        if sample_text and not session_title_matches_sample(title, sample_text) and not session_id_matches_sample(session_dir.name, sample_text):
            continue
        messages_path = session_primary_messages_path(session_dir)
        message_count = count_jsonl_rows(messages_path)
        expected_index = max(message_count - 1, -1)
        commit_index = meta.get("commit_index", meta.get("archive_index", -1))
        atom_index = meta.get("atom_pipeline_index", meta.get("atom_index", -1))
        commit_state_known = isinstance(commit_index, int) and commit_index >= 0
        session_cursor = str(meta.get("last_extracted_turn_id") or "")
        atom_state_known = (isinstance(atom_index, int) and atom_index >= 0) or bool(session_cursor)
        last_message_id = ""
        if messages_path.exists():
            try:
                with messages_path.open(encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.strip():
                            try:
                                last_message_id = json.loads(line).get("message_id") or last_message_id
                            except Exception:
                                continue
            except Exception:
                last_message_id = ""
        archive_json = session_dir / "history" / "archive_001" / "archive.json"
        archive_messages = session_dir / "history" / "archive_001" / "messages.jsonl"
        engine_commit = engine_commit_status_payload(root, session_dir.name)
        engine_completed = str(engine_commit.get("status") or "").lower() == "completed"
        archive_ok = archive_json.exists()
        projection_meta = session_projection_file(root, session_dir.name, "meta.json")
        projection_abstract = session_projection_file(root, session_dir.name, "abstract.md")
        projection_overview = session_projection_file(root, session_dir.name, "overview.md")
        projection_ready = bool(projection_meta and projection_abstract and projection_overview)
        commit_ok = archive_ok or engine_completed or projection_ready or (isinstance(commit_index, int) and commit_index >= expected_index)
        atom_ok = (
            (isinstance(atom_index, int) and atom_index >= expected_index)
            or (bool(session_cursor) and (not last_message_id or session_cursor == last_message_id))
            or engine_completed
            or projection_ready
        )
        ok = message_count > 0 and commit_ok and atom_ok
        totals["sessions"] += 1
        totals["submitted"] += message_count
        totals["expected"] += message_count
        totals["complete"] += int(ok)
        totals["incomplete"] += int(not ok)
        rows.append(
            {
                "session_id": session_dir.name,
                "session_key": title,
                "expected_messages": message_count,
                "submitted_messages": message_count,
                "pending_after_commit": 0,
                "integrity": "complete" if ok else "incomplete",
                "session_path": str(session_dir),
                "history_path": str(archive_messages if archive_messages.exists() else messages_path),
                "meta_path": str(meta_path) if meta_path.exists() else "",
                "exists": True,
                "history_files": 1 if (archive_messages.exists() or messages_path.exists()) else 0,
                "commit_complete": commit_ok,
                "atom_flush_complete": atom_ok,
                "commit_state_known": commit_state_known or archive_ok or engine_completed,
                "atom_state_known": atom_state_known or engine_completed,
                "ok": ok,
            }
        )
    return rows, totals


def import_integrity(
    workspace: Path,
    account: str,
    output_dir: Path,
    data_path: Path,
    sample: str = "",
    summary_path: Path | None = None,
    user_id: str = "default",
) -> dict[str, Any]:
    del user_id
    account = account or "default"
    sample = normalize_sample_filter(sample)
    summary_missing = False
    if not summary_path or not summary_path.exists():
        summary_path = latest_import_summary_for(workspace, account, output_dir, sample)
    if not summary_path or not summary_path.exists():
        summary_missing = True
        summary = {}
        records = []
    else:
        summary = read_json(summary_path)
        records = sample_records(summary, sample)
        if not records:
            records = summary.get("records") or []

    checks: list[dict[str, Any]] = []
    expected = sum(int(record.get("expected_messages") or 0) for record in records) or int(summary.get("expected_messages") or 0)
    submitted = sum(int(record.get("submitted_messages") or 0) for record in records) or int(summary.get("submitted_messages") or 0)
    summary_incomplete = sum(1 for record in records if str(record.get("integrity") or "").lower() != "complete")
    if not records:
        summary_incomplete = int(summary.get("incomplete_samples") or 0)
    summary_status = str(summary.get("status") or "")
    summary_running = summary_status == "ECHOMEMORY_IMPORT_RUNNING" or bool(summary.get("running"))
    if summary_missing:
        add_check(checks, "Summary 文件", False, "未找到匹配当前 workspace/account/sample 的 EchoMemory 导入 summary；改用 workspace 当前快照判定。", "warn")
    else:
        add_check(checks, "Summary 消息数", bool(expected and submitted == expected), f"submitted={submitted} / expected={expected}")
        add_check(
            checks,
            "Summary 完整性",
            summary_incomplete == 0 and summary_status.startswith("ECHOMEMORY_IMPORT") and not summary_running,
            f"status={summary_status or '-'} · incomplete_records={summary_incomplete}",
            "warn" if summary_running or summary_incomplete != 0 else "ok",
        )

    sessions, totals = session_record_checks(workspace, account, records)
    current_sessions, current_totals = current_session_snapshot(workspace, account, sample)
    snapshot_has_explicit_state = any(
        bool(item.get("commit_state_known") or item.get("atom_state_known"))
        for item in current_sessions
    )
    if current_sessions and snapshot_has_explicit_state:
        sessions = current_sessions
        totals["expected"] = current_totals["expected"]
        totals["submitted"] = current_totals["submitted"]
        totals["missing_sessions"] = 0
        totals["empty_message_files"] = sum(1 for item in current_sessions if not item.get("history_files"))
        totals["incomplete_commits"] = sum(1 for item in current_sessions if not item.get("commit_complete"))
        totals["incomplete_atom_flush"] = sum(1 for item in current_sessions if not item.get("atom_flush_complete"))
    if not expected:
        expected = totals["expected"]
    if not submitted:
        submitted = totals["submitted"]
    add_check(checks, "Session 目录", bool(sessions) and totals["missing_sessions"] == 0, f"sessions={len(sessions)} · missing={totals['missing_sessions']}")
    add_check(checks, "消息文件", bool(sessions) and totals["empty_message_files"] == 0, f"empty_message_files={totals['empty_message_files']}")
    add_check(checks, "Commit 完成", totals["incomplete_commits"] == 0, f"incomplete_commits={totals['incomplete_commits']}")
    add_check(checks, "Commit 后保留消息", True, f"pending/live_after_commit={totals['pending']}（EchoMemory 可保留最近消息；完整性以 summary 与 commit artifact 为准）")
    add_check(checks, "Session 消息汇总", bool(totals["expected"]) and totals["expected"] == totals["submitted"], f"submitted={totals['submitted']} / expected={totals['expected']}")

    account_root = first_existing_root(workspace, account)
    artifact_files = count_files(account_root)
    session_root = account_root / "sessions"
    memory_root = preferred_memory_root(account_root)
    engine_root = next((path for path in engine_root_candidates(account_root) if path.exists()), engine_root_candidates(account_root)[0])
    vector_root = engine_root / "vector_store"
    abstract_count = sum(1 for row in sessions if session_projection_file(account_root, str(row.get("session_id") or ""), "abstract.md"))
    overview_count = sum(1 for row in sessions if session_projection_file(account_root, str(row.get("session_id") or ""), "overview.md"))
    atom_count = len(structured_atom_entries(account_root)) or count_files(memory_root / ".structured" / "atoms")
    graph_count = count_files(memory_root / ".graph")
    episode_count = count_files(memory_root / ".episodes" / "episodes")
    vector_count = count_files(vector_root)
    overview_ready = bool(sessions) and overview_count == len(sessions)
    abstract_ready = bool(sessions) and abstract_count == len(sessions)
    retrieval_layers_ready = bool(atom_count > 0 and vector_count > 0 and abstract_ready and overview_ready)
    atom_flush_lagging_only = bool(
        totals["incomplete_atom_flush"] > 0
        and totals["incomplete_commits"] == 0
        and retrieval_layers_ready
    )
    add_check(
        checks,
        "Atom Flush 完成",
        totals["incomplete_atom_flush"] == 0,
        (
            f"incomplete_atom_flush={totals['incomplete_atom_flush']}（cursor 未完全追平，但 overview / atom / vector 已可用）"
            if atom_flush_lagging_only
            else f"incomplete_atom_flush={totals['incomplete_atom_flush']}"
        ),
        "warn" if atom_flush_lagging_only else "",
    )
    add_check(
        checks,
        "EchoMemory Artifact",
        account_root.exists() and artifact_files > 0,
        f"artifact_files={artifact_files} · account_root={account_root}",
        "ok" if account_root.exists() and artifact_files > 0 else "warn",
    )
    add_check(checks, "Session Abstract", abstract_count == len(sessions), f"abstract={abstract_count} / sessions={len(sessions)}", "ok" if abstract_count == len(sessions) else "warn")
    add_check(checks, "Session Overview", overview_count == len(sessions), f"overview={overview_count} / sessions={len(sessions)}", "ok" if overview_count == len(sessions) else "warn")
    add_check(checks, "Atoms", atom_count > 0, f"atom_files={atom_count}", "ok" if atom_count > 0 else "warn")
    add_check(checks, "Graph", graph_count > 0, f"graph_files={graph_count}", "ok" if graph_count > 0 else "warn")
    add_check(checks, "Episodes", episode_count > 0, f"episode_files={episode_count}", "ok" if episode_count > 0 else "warn")
    add_check(checks, "Vector Index", vector_count > 0, f"vector_files={vector_count} · vector_root={vector_root}", "ok" if vector_count > 0 else "warn")

    gold_probe = gold_atom_gap_probe(workspace, account, sample)
    if gold_probe.get("enabled"):
        gap_count = int(gold_probe.get("gap_count") or 0)
        add_check(
            checks,
            "Gold in session but missing from atoms",
            gap_count == 0,
            f"gap_count={gap_count} · session_hits={gold_probe.get('session_hit_count', 0)} · atom_hits={gold_probe.get('atom_hit_count', 0)}",
            "warn" if gap_count else "ok",
        )

    failed = [item for item in checks if item["level"] == "fail" or (item["ok"] is False and item["level"] != "warn")]
    warnings = [item for item in checks if item["level"] == "warn" and not item["ok"]]
    current_complete = bool(sessions) and totals["incomplete_commits"] == 0 and totals["incomplete_atom_flush"] == 0 and totals["expected"] == totals["submitted"] and retrieval_layers_ready
    if summary_running:
        status = "running"
    else:
        status = "complete" if current_complete and not failed else ("warning" if current_complete else ("warning" if not failed else "incomplete"))
    return {
        "backend": "echomemory",
        "memory_label": "EchoMemory",
        "status": status,
        "workspace": str(workspace),
        "account": account,
        "sample": sample,
        "summary_path": str(summary_path) if summary_path else "",
        "account_path": str(account_root),
        "session_root": str(first_existing_root(workspace, account) / "sessions"),
        "memory_root": str(memory_root),
        "expected_messages": expected,
        "submitted_messages": submitted,
        "session_count": len(sessions),
        "memory_files": artifact_files,
        "abstract_files": abstract_count,
        "overview_files": overview_count,
        "atom_files": atom_count,
        "graph_files": graph_count,
        "episode_files": episode_count,
        "vector_files": vector_count,
        "summary_extracted_memories": "",
        "checks": checks,
        "sessions": sessions,
        "evidence_probe": gold_probe,
    }


def memory_timeline(workspace: Path, account: str, user_id: str = "default", query: str = "", limit: int = 200) -> dict[str, Any]:
    del user_id
    account_root = first_existing_root(workspace, account)
    root = preferred_memory_root(account_root)
    q = query.strip().lower()
    rows: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in {".md", ".json", ".jsonl", ".txt", ".yaml", ".yml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        haystack = f"{path.name} {text}".lower()
        if q and q not in haystack:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        rows.append(
            {
                "date": "undated",
                "kind": rel.parts[0] if rel.parts else "artifact",
                "title": path.name,
                "uri": f"echomemory://{rel}",
                "path": str(path),
                "chars": len(text),
                "snippet": compact(text),
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
        if len(rows) >= limit:
            break
    return {"backend": "echomemory", "workspace": str(workspace), "account": account, "memory_root": str(root), "count": len(rows), "by_date": {}, "items": rows}


def session_browser(workspace: Path, account: str, sample: str = "", limit: int = 120) -> dict[str, Any]:
    imported = list_imported_memories(workspace, account, Path("__unused__"), limit, sample)
    return {"backend": "echomemory", "workspace": str(workspace), "account": account, "sample": normalize_sample_filter(sample), "sessions": imported.get("sessions") or []}


def read_memory_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"path": str(path), "name": path.name, "chars": len(text), "text": text}
