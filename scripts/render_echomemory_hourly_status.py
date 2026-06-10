#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.plugins.echomemory.inspector import (  # noqa: E402
    add_check,
    count_files,
    current_session_snapshot,
    first_existing_root,
    gold_atom_gap_probe,
    import_integrity,
)


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def compact(text: Any, limit: int = 220) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def latest_file(directory: Path, pattern: str) -> Path | None:
    candidates = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return candidates[0] if candidates else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_ref_for_root(root: Path | None) -> str:
    if not root or not root.exists():
        return "-"
    try:
        tag = subprocess.check_output(
            ["git", "-C", str(root), "describe", "--tags", "--always"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        sha = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return f"{tag} ({sha})"
    except Exception:
        return "-"


def has_local_venv(root: Path | None) -> bool:
    if not root or not root.exists():
        return False
    return (root / ".venv" / "bin" / "python").exists()


def find_latest_matching_qa_summary(runs_dir: Path, workspace: Path, account: str, sample: str) -> tuple[Path | None, dict[str, Any] | None]:
    candidates = sorted(runs_dir.glob("*/echomemory_qa/summary.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for path in candidates:
        try:
            summary = read_json(path)
        except Exception:
            continue
        if str(Path(summary.get("workspace") or "").expanduser()) != str(workspace):
            continue
        if str(summary.get("account") or "") != account:
            continue
        if sample and str(summary.get("sample") or "") != sample:
            continue
        return path, summary
    return None, None


def find_adjacent_judge_summary(summary_path: Path | None) -> tuple[Path | None, dict[str, Any] | None]:
    if not summary_path:
        return None, None
    qa_dir = summary_path.parent
    for name in ("judge_summary.json", "summary.judge.json"):
        path = qa_dir / name
        if path.exists():
            try:
                return path, read_json(path)
            except Exception:
                continue
    return None, None


def find_adjacent_wrong_analysis(summary_path: Path | None) -> tuple[Path | None, dict[str, Any] | None]:
    if not summary_path:
        return None, None
    qa_dir = summary_path.parent
    for name in ("echomemory_memory_qa_results.wrong_analysis.json", "wrong_analysis.json"):
        path = qa_dir / name
        if path.exists():
            try:
                return path, read_json(path)
            except Exception:
                continue
    return None, None


def recent_matching_judged_runs(runs_dir: Path, workspace: Path, account: str, sample: str, limit: int = 2) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    candidates = sorted(runs_dir.glob("*/echomemory_qa/summary.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for path in candidates:
        try:
            summary = read_json(path)
        except Exception:
            continue
        if str(Path(summary.get("workspace") or "").expanduser()) != str(workspace):
            continue
        if str(summary.get("account") or "") != account:
            continue
        if sample and str(summary.get("sample") or "") != sample:
            continue
        judge_path, judge = find_adjacent_judge_summary(path)
        if not judge_path or not judge:
            continue
        items.append(
            {
                "summary_path": str(path),
                "judge_path": str(judge_path),
                "accuracy": float(judge.get("accuracy") or 0),
                "correct": int(judge.get("correct") or 0),
                "count": int(judge.get("count") or 0),
                "run_dir": str(path.parents[1]),
            }
        )
        if len(items) >= limit:
            break
    return items


def latest_preflight_probe(runs_dir: Path) -> tuple[Path | None, dict[str, Any] | None]:
    candidates = sorted(
        runs_dir.glob("echomemory_*preflight_probe*/echomemory_import/echomemory_model_preflight.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    latest_ok: tuple[Path | None, dict[str, Any] | None] = (None, None)
    for path in candidates:
        try:
            data = read_json(path)
        except Exception:
            continue
        if str(data.get("status") or "").lower() == "fail":
            return path, data
        if latest_ok[0] is None:
            latest_ok = (path, data)
    return latest_ok


def latest_live_import(runs_dir: Path) -> dict[str, Any] | None:
    candidates = sorted(
        runs_dir.glob("echomemory_v006_full_conv30_import_*/import.log"),
        key=lambda p: (p.parent.name, p.stat().st_mtime if p.exists() else 0),
        reverse=True,
    )
    for log_path in candidates:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        out_dir = log_path.parent / "echomemory_import"
        preflight_path = out_dir / "echomemory_model_preflight.json"
        preflight = read_json(preflight_path) if preflight_path.exists() else {}
        commit_count = text.count("[commit] ")
        flush_count = text.count("[flush] ")
        warning_count = text.count("[warning] ")
        llm_count = text.count("echomem.observability.gateway")
        session_lines = re.findall(r"\[import\] session=([^\s]+) label=([^\s]+)", text)
        sessions_seen = len(session_lines)
        latest_session_id = session_lines[-1][0] if session_lines else ""
        latest_label = session_lines[-1][1] if session_lines else ""
        latest_llm = ""
        llm_matches = re.findall(r"call_site=([a-zA-Z0-9_/-]+).*?latency=([0-9.]+)ms", text)
        if llm_matches:
            latest_llm = f"{llm_matches[-1][0]} / {llm_matches[-1][1]}ms"
        return {
            "log_path": str(log_path),
            "run_dir": str(log_path.parent),
            "out_dir": str(out_dir),
            "preflight_path": str(preflight_path) if preflight_path.exists() else "",
            "preflight_ok": bool(preflight.get("embedding", {}).get("ok")) and bool(preflight.get("chat", {}).get("ok")),
            "sessions_seen": sessions_seen,
            "commit_count": commit_count,
            "flush_count": flush_count,
            "warning_count": warning_count,
            "llm_count": llm_count,
            "latest_session_id": latest_session_id,
            "latest_label": latest_label,
            "latest_llm": latest_llm,
            "tail": compact("\n".join(text.splitlines()[-12:]), 1600),
        }
    return None


def latest_auto_eval(runs_dir: Path) -> dict[str, Any] | None:
    candidates = sorted(
        runs_dir.glob("echomemory_v006_full_conv30_autoeval_*/echomemory_qa/auto_eval_status.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        try:
            data = read_json(path)
        except Exception:
            continue
        data["status_path"] = str(path)
        return data
    return None


def runtime_root_from_live_import(live_import: dict[str, Any] | None) -> Path | None:
    runtime_path = Path(str((live_import or {}).get("out_dir") or "")) / "echomem.runtime.yaml"
    if not runtime_path.exists():
        return None
    try:
        text = runtime_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    match = re.search(r'^\s*path:\s*"([^"]+/configs/schemas)"\s*$', text, re.M)
    if not match:
        return None
    schemas_path = Path(match.group(1)).expanduser()
    try:
        return schemas_path.parents[1]
    except Exception:
        return None


def load_sample_expected_counts(dataset_path: Path, sample: str) -> tuple[int, int]:
    try:
        data = json.loads(dataset_path.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0
    if not isinstance(data, list):
        return 0, 0
    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("sample_id") or "") != str(sample or ""):
            continue
        conversation = item.get("conversation") or {}
        if not isinstance(conversation, dict):
            return 0, 0
        session_keys = [key for key, value in conversation.items() if key.startswith("session_") and isinstance(value, list)]
        expected_sessions = len(session_keys)
        expected_messages = sum(len(conversation.get(key) or []) for key in session_keys)
        return expected_sessions, expected_messages
    return 0, 0


def build_live_integrity(
    workspace: Path,
    account: str,
    dataset_path: Path,
    sample: str,
    live_import: dict[str, Any] | None,
) -> dict[str, Any]:
    sessions, totals = current_session_snapshot(workspace, account, sample)
    account_root = first_existing_root(workspace, account)
    expected_sessions, expected_messages = load_sample_expected_counts(dataset_path, sample)
    empty_message_files = sum(1 for row in sessions if not row.get("history_files"))
    incomplete_commits = sum(1 for row in sessions if not row.get("commit_complete"))
    incomplete_atom_flush = sum(1 for row in sessions if not row.get("atom_flush_complete"))
    pending_after_commit = sum(int(row.get("pending_after_commit") or 0) for row in sessions)
    legacy_index_lag_rows: list[str] = []
    for row in sessions:
        try:
            meta = read_json(Path(str(row.get("meta_path") or "")))
        except Exception:
            meta = {}
        atom_index = int(meta.get("atom_pipeline_index")) if str(meta.get("atom_pipeline_index") or "").strip() not in {"", "None"} else -1
        cursor = str(meta.get("last_extracted_turn_id") or "")
        title = str(meta.get("title") or row.get("session_key") or row.get("session_id") or "")
        if cursor and atom_index < 0:
            legacy_index_lag_rows.append(title or str(row.get("session_id") or ""))
    artifact_files = count_files(account_root)
    memory_root = account_root / "memory"
    vector_root = account_root.parent / "system" / "vector_index"
    abstract_count = sum(1 for row in sessions if Path(str(row.get("session_path") or "")).joinpath("abstract.md").exists())
    overview_count = sum(1 for row in sessions if Path(str(row.get("session_path") or "")).joinpath("overview.md").exists())
    atom_count = count_files(memory_root / ".structured" / "atoms")
    graph_count = count_files(memory_root / ".graph")
    episode_count = count_files(memory_root / ".episodes" / "episodes")
    vector_count = count_files(vector_root)
    retrieval_layers_ready = bool(atom_count > 0 and vector_count > 0 and abstract_count > 0 and overview_count > 0)
    checks: list[dict[str, Any]] = []

    add_check(
        checks,
        "Live Import Summary",
        True,
        "当前 full run 尚未产出 echomemory_import_summary.json；本页改为直接读取 live workspace / import.log。",
        "warn",
    )
    add_check(
        checks,
        "Session 目录",
        bool(sessions),
        f"sessions={len(sessions)} / expected={expected_sessions or '?'}",
    )
    add_check(checks, "消息文件", bool(sessions) and empty_message_files == 0, f"empty_message_files={empty_message_files}")
    add_check(checks, "Commit 完成", incomplete_commits == 0, f"incomplete_commits={incomplete_commits}")
    add_check(checks, "Commit 后保留消息", True, f"pending/live_after_commit={pending_after_commit}（live run 允许异步收尾）")
    add_check(
        checks,
        "Session 消息汇总",
        totals["submitted"] > 0,
        f"submitted={totals['submitted']} / expected_total={expected_messages or '?'}",
        "warn" if expected_messages and totals["submitted"] < expected_messages else "ok",
    )
    add_check(
        checks,
        "Atom Flush 完成",
        incomplete_atom_flush == 0,
        (
            f"incomplete_atom_flush={incomplete_atom_flush}（live import 中，cursor 仍可能继续追平）"
            if incomplete_atom_flush > 0
            else "incomplete_atom_flush=0"
        ),
        "warn" if incomplete_atom_flush > 0 and retrieval_layers_ready else "",
    )
    if legacy_index_lag_rows:
        add_check(
            checks,
            "Legacy atom_pipeline_index 滞后",
            False,
            f"sessions={len(legacy_index_lag_rows)} · examples={', '.join(legacy_index_lag_rows[:3])}",
            "warn",
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
    current_complete = bool(expected_sessions and len(sessions) >= expected_sessions and incomplete_commits == 0 and retrieval_layers_ready)
    status = "complete" if current_complete and not failed else ("running" if live_import else "warning")
    return {
        "backend": "echomemory",
        "memory_label": "EchoMemory",
        "status": status,
        "workspace": str(workspace),
        "account": account,
        "sample": sample,
        "summary_path": "(live import; summary not generated yet)",
        "account_path": str(account_root),
        "session_root": str(account_root / "sessions"),
        "memory_root": str(account_root),
        "expected_messages": expected_messages or totals["submitted"],
        "submitted_messages": totals["submitted"],
        "expected_sessions": expected_sessions or len(sessions),
        "session_count": len(sessions),
        "checks": checks,
        "evidence_probe": gold_probe,
    }


def check_label(check: dict[str, Any]) -> str:
    if check.get("ok"):
        return "OK"
    if check.get("level") == "warn":
        return "WARN"
    return "FAIL"


def check_class(check: dict[str, Any]) -> str:
    if check.get("ok"):
        return "ok"
    if check.get("level") == "warn":
        return "warn"
    return "bad"


def parse_count(message: str, key: str) -> int:
    match = re.search(rf"{re.escape(key)}=(\d+)", str(message or ""))
    return int(match.group(1)) if match else 0


def build_code_findings(
    integrity: dict[str, Any],
    import_summary: dict[str, Any] | None,
    echomem_root: Path | None,
    qa_summary: dict[str, Any] | None,
    wrong_analysis: dict[str, Any] | None,
    preflight_probe: dict[str, Any] | None,
    preflight_probe_path: Path | None,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    checks_by_name = {str(item.get("name") or ""): item for item in (integrity.get("checks") or [])}
    commit_check = checks_by_name.get("Commit 完成") or {}
    atom_check = checks_by_name.get("Atom Flush 完成") or {}
    commit_missing = parse_count(str(commit_check.get("message") or ""), "incomplete_commits")
    atom_missing = parse_count(str(atom_check.get("message") or ""), "incomplete_atom_flush")
    probe = integrity.get("evidence_probe") or {}
    gap_count = int(probe.get("gap_count") or 0)
    import_summary = import_summary or {}
    qa_summary = qa_summary or {}
    summary_json = qa_summary.get("summary_json") if isinstance(qa_summary.get("summary_json"), dict) else {}
    wrong_analysis = wrong_analysis or {}
    preflight_probe = preflight_probe or {}
    failure_attribution = wrong_analysis.get("failure_attribution") if isinstance(wrong_analysis.get("failure_attribution"), dict) else {}
    mode_counts = failure_attribution.get("mode_counts") if isinstance(failure_attribution.get("mode_counts"), dict) else {}

    if echomem_root and not has_local_venv(echomem_root):
        findings.append(
            {
                "level": "warn",
                "title": "v0.0.6 工作树没有独立 .venv，平台可用性仍依赖外部 Python 环境",
                "symptom": f"missing_venv={echomem_root / '.venv'}",
                "code": "start.sh:10-16；scripts/echomemory_common.py；memory/plugins/echomemory/tasks.py",
                "analysis": "当前平台虽然已优先指向 echo_memory_v006，但 v006 仓本身没有单独依赖环境。实操上只能借旧仓 .venv 或外部 python 环境跑，这会让“换了代码仓但运行环境没换”变得更隐蔽。",
            }
        )

    if commit_missing or atom_missing:
        findings.append(
            {
                "level": "bad",
                "title": "导入链还没完全 flush 到长期记忆终态",
                "symptom": f"incomplete_commits={commit_missing}；incomplete_atom_flush={atom_missing}",
                "code": "scripts/echomemory_locomo_import.py:565-579, 679-706；memory/plugins/echomemory/inspector.py:640-697",
                "analysis": "导入脚本已经把 session 标成 complete/partial/incomplete，不会再把 retrieval_ready 误当成一切完成。现在小时页看到的 incomplete_atom_flush=19 不是展示问题，而是 atom cursor 真的没走到期望终点。",
            }
        )

    try:
        first_record = ((import_summary.get("records") or [])[0].get("session_records") or [])[0]
    except Exception:
        first_record = {}
    atom_flush = first_record.get("atom_flush") or {}
    atom_index = int(atom_flush.get("atom_pipeline_index") or -1)
    expected_atom_index = int(atom_flush.get("expected_atom_pipeline_index") or -1)
    timed_out_attempts = [item for item in (atom_flush.get("attempts") or []) if item.get("timed_out")]
    if timed_out_attempts and atom_index == 9 and expected_atom_index > atom_index:
        findings.append(
            {
                "level": "warn",
                "title": "已复现历史中间态：atom cursor 曾停在第 10 条，形态上很像阈值 flush 抢先跑了一批",
                "symptom": f"atom_pipeline_index={atom_index}；expected={expected_atom_index}；timed_out_attempts={len(timed_out_attempts)}",
                "code": "echomem/workers/organized_projector/message_adapter.py:24-27, 113-161；scripts/echomemory_locomo_import.py:249-318",
                "analysis": "smoke3 最早留下的 last_extracted_turn_id 正好落在第 10 条消息，而 MessagePersistedAdapter 的 _FLUSH_THRESHOLD 默认就是 10。这个中间态后来已经通过手动补跑 ingest_message 从 9 推到 27，说明后端并不是抽不出来，主要问题还是 flush 等待时间和超时后的重试策略。",
            }
        )

    if gap_count:
        findings.append(
            {
                "level": "warn",
                "title": "gold 在 session 有，但 atoms 没完整抽出来",
                "symptom": f"gap_count={gap_count}；session_hits={probe.get('session_hit_count', 0)}；atom_hits={probe.get('atom_hit_count', 0)}",
                "code": "memory/plugins/echomemory/inspector.py:184-274, 682-690",
                "analysis": "专项 probe 会同时扫描原始 session、atoms 和派生 memory。当前大量题目落在 archive_only / partial，说明问题主要还在抽取层，不是纯回答层。qa5 少掉 'by the water' 就是这一类。",
            }
        )

    if summary_json:
        local_messages = bool(summary_json.get("local_messages"))
        local_session_summaries = bool(summary_json.get("local_session_summaries"))
        local_atoms = bool(summary_json.get("local_atoms"))
        raw_turn_fallback = bool(summary_json.get("raw_turn_fallback"))
        if local_session_summaries and local_atoms and not local_messages and not raw_turn_fallback:
            findings.append(
                {
                    "level": "warn",
                    "title": "QA 默认不读原始 messages，抽取漏事实时没有原文兜底",
                    "symptom": "local_session_summaries=true；local_atoms=true；local_messages=false；raw_turn_fallback=false",
                    "code": "scripts/echomemory_memory_qa.py:691-717, 720-760, 763-810, 946-963, 2208-2210",
                    "analysis": "这和当前策略是一致的：不靠原始对话作弊。但副作用也很直接，session summary / atom 一旦漏掉关键短语，回答阶段拿不到它。qa5、qa23 都是在 summary 更泛、atoms 又没补齐时被带偏。",
                }
            )

    if int(mode_counts.get("list_aggregation_error") or 0) or int(mode_counts.get("semantic_mismatch") or 0):
        findings.append(
            {
                "level": "warn",
                "title": "answer refinement 会收窄答案，但还不会稳定补齐跨 session 列表",
                "symptom": (
                    f"list_aggregation_error={int(mode_counts.get('list_aggregation_error') or 0)}；"
                    f"semantic_mismatch={int(mode_counts.get('semantic_mismatch') or 0)}"
                ),
                "code": "scripts/echomemory_memory_qa.py:1623-1731, 1869-1880",
                "analysis": "这轮新补丁已经生效，答案 token 变多，且 answer_refined=true。但它更擅长把答案缩小，不擅长自动补齐分散在多条记忆里的清单项，所以 qa24 仍漏 fair / dance competition，qa66 仍把 classes/workshops 一起带了出来。",
            }
        )

    if int(mode_counts.get("evidence_mismatch") or 0):
        findings.append(
            {
                "level": "warn",
                "title": "session summary 在检索里权重较高，泛化摘要会压过缺失事实",
                "symptom": f"evidence_mismatch={int(mode_counts.get('evidence_mismatch') or 0)}",
                "code": "scripts/echomemory_memory_qa.py:691-717, 720-760, 1595-1617",
                "analysis": "当前本地检索会先从 session summary 和 atoms 组装 prompt。若 summary 里是 downtown / ad campaign 这种泛化说法，而 atoms 层又没把更细事实写出来，最终答案只会围着这个更宽泛的摘要收窄。",
            }
        )

    if preflight_probe_path and str(preflight_probe.get("status") or "").lower() == "fail":
        findings.append(
            {
                "level": "ok",
                "title": "CLI 导入现在会在写消息前拦住无效 provider key",
                "symptom": f"preflight_file={preflight_probe_path}",
                "code": "scripts/echomemory_locomo_import.py:79-220, 951-974",
                "analysis": "这轮新增了 embedding/chat 双预检。坏 key 不会再先写 session、再在 overview/abstract/atom 阶段一起 401，而是直接返回 ECHOMEMORY_IMPORT_PREFLIGHT_FAILED，并把细节写入 echomemory_model_preflight.json。",
            }
        )

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a current EchoMemory hourly issue/bug status HTML.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--sample", default="conv-30")
    parser.add_argument("--runs-dir", default=str(ROOT / "runs"))
    dataset_candidates = [
        ROOT / "dataset" / "full" / "locomo.json",
        ROOT / "dataset" / "locomo.json",
        ROOT / "dataset" / "locomo10.json",
    ]
    dataset_default = next((path for path in dataset_candidates if path.exists()), dataset_candidates[-1])
    parser.add_argument("--dataset", default=str(dataset_default))
    parser.add_argument("--generated-dir", default=str(ROOT / "web" / "static" / "generated-reports"))
    parser.add_argument("--out-html", default=str(ROOT / "web" / "static" / "generated-reports" / "echomemory_hourly_issue_status_latest.html"))
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    runs_dir = Path(args.runs_dir).expanduser().resolve()
    dataset = Path(args.dataset).expanduser().resolve()
    generated_dir = Path(args.generated_dir).expanduser().resolve()
    out_html = Path(args.out_html).expanduser().resolve()

    live_import = latest_live_import(runs_dir)
    auto_eval = latest_auto_eval(runs_dir)
    try:
        integrity = import_integrity(workspace, args.account, runs_dir, dataset, sample=args.sample)
        summary_path = Path(str(integrity.get("summary_path") or "")).expanduser()
        summary_json = read_json(summary_path) if summary_path.exists() else {}
        if live_import and (
            str(summary_json.get("status") or "") == "ECHOMEMORY_IMPORT_RUNNING"
            or bool(summary_json.get("running"))
        ):
            integrity = build_live_integrity(workspace, args.account, dataset, args.sample, live_import)
            integrity["summary_path"] = f"{summary_path} (RUNNING snapshot present; live workspace preferred)"
    except FileNotFoundError:
        integrity = build_live_integrity(workspace, args.account, dataset, args.sample, live_import)
        summary_path = Path()
        summary_json = {}
    probe = integrity.get("evidence_probe") or {}
    checks = integrity.get("checks") or []
    echomem_root = Path(str(summary_json.get("echomem_root") or "")).expanduser() if summary_json else runtime_root_from_live_import(live_import)
    echomem_ref = git_ref_for_root(echomem_root)
    echomem_has_venv = has_local_venv(echomem_root)

    latest_code_issues = latest_file(generated_dir, "echomemory_v006*.html") or latest_file(generated_dir, "echomemory_v005*.html")
    latest_system_issues = latest_file(generated_dir, "echomemory_other_system_issues_and_fixes_*.html")
    latest_diag = latest_file(generated_dir, "echomemory_conv30_diagnostic_*.html")
    latest_status = latest_file(generated_dir, "echomemory_conv30_status_*.html")
    preflight_probe_path, preflight_probe = latest_preflight_probe(runs_dir)

    qa_summary_path, qa_summary = find_latest_matching_qa_summary(runs_dir, workspace, args.account, args.sample)
    judge_summary_path, judge_summary = find_adjacent_judge_summary(qa_summary_path)
    wrong_analysis_path, wrong_analysis = find_adjacent_wrong_analysis(qa_summary_path)

    fail_count = sum(1 for item in checks if not item.get("ok") and item.get("level") != "warn")
    warn_count = sum(1 for item in checks if not item.get("ok") and item.get("level") == "warn")
    probe_counts = probe.get("counts") or {}
    refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code_findings = build_code_findings(integrity, summary_json, echomem_root, qa_summary, wrong_analysis, preflight_probe, preflight_probe_path)
    recent_judged = recent_matching_judged_runs(runs_dir, workspace, args.account, args.sample, limit=2)
    checks_by_name = {str(item.get("name") or ""): item for item in checks}

    key_findings = [
        f"完整性状态：{integrity.get('status') or '-'}；硬失败 {fail_count} 项，提醒 {warn_count} 项。",
        f"消息写入：{integrity.get('submitted_messages', 0)}/{integrity.get('expected_messages', 0)}；session={integrity.get('session_count', 0)}。",
        f"证据探针：pass={probe_counts.get('pass', 0)} / partial={probe_counts.get('partial', 0)} / archive_only={probe_counts.get('archive_only', 0)} / missing={probe_counts.get('missing', 0)}。",
    ]
    if echomem_root:
        key_findings.append(f"当前 EchoMemory 代码：{echomem_root}；版本={echomem_ref}。")
        if not echomem_has_venv:
            key_findings.append("当前代码仓没有独立 .venv；平台运行仍可能借旧仓或外部 Python 环境。")
    if preflight_probe_path and str((preflight_probe or {}).get("status") or "").lower() == "fail":
        key_findings.append(f"CLI 导入脚本现在会先做 embedding/chat 预检；最近一次坏 key 样本已在导入前被 fail-fast 拦住：{preflight_probe_path}。")
    if live_import:
        key_findings.append(
            "当前还有一轮 v0.0.6 full conv-30 导入在后台推进："
            f"sessions_seen={live_import.get('sessions_seen', 0)}，commit={live_import.get('commit_count', 0)}，"
            f"flush={live_import.get('flush_count', 0)}，log={live_import.get('log_path') or '-'}。"
        )
    if auto_eval:
        key_findings.append(
            f"导入完成后的自动 QA/Judge 已挂起：stage={auto_eval.get('stage') or '-'}；status={auto_eval.get('status_path') or '-'}。"
        )
    if judge_summary:
        key_findings.append(
            f"最近一轮 QA/Judge：{judge_summary.get('correct', 0)}/{judge_summary.get('count', 0)}，准确率 {(float(judge_summary.get('accuracy') or 0) * 100):.2f}%。"
        )
    else:
        key_findings.append("最近一轮 QA/Judge：当前没有匹配到可直接引用的 judge_summary.json。")
    try:
        first_record = ((summary_json.get("records") or [])[0].get("session_records") or [])[0]
    except Exception:
        first_record = {}
    if first_record:
        historical_atom_incomplete = not bool((first_record.get("atom_flush") or {}).get("complete"))
        current_atom_ok = bool((checks_by_name.get("Atom Flush 完成") or {}).get("ok"))
        if historical_atom_incomplete and current_atom_ok:
            key_findings.append("同一 workspace 已验证可以把 atom cursor 从 9 补到 27；当前更像 flush 等待/重试策略问题，不是 atom 抽取能力缺失。")
    if len(recent_judged) >= 2:
        latest = recent_judged[0]
        previous = recent_judged[1]
        key_findings.append(
            "最近两轮同配置波动："
            f"最新 {(latest['accuracy'] * 100):.2f}%（{latest['correct']}/{latest['count']}），"
            f"上一轮 {(previous['accuracy'] * 100):.2f}%（{previous['correct']}/{previous['count']}）。"
        )

    commit_check = checks_by_name.get("Commit 完成") or {}
    atom_flush_check = checks_by_name.get("Atom Flush 完成") or {}
    graph_check = checks_by_name.get("Graph") or {}
    atoms_check = checks_by_name.get("Atoms") or {}
    vector_check = checks_by_name.get("Vector Index") or {}
    interpretation_cards: list[str] = []
    if commit_check.get("ok"):
        interpretation_cards.append(
            """
            <article class="card ok">
              <strong>commit 已保住</strong>
              <p>当前 smoke/workspace 中，session 的 <code>commit_index</code> 已经不再回滚。如果这里是 OK，说明这轮导入至少没有再出现最早那种 <code>commit_index=-1</code>、<code>pending_tokens</code> 被旧 meta 覆盖回去的问题。</p>
            </article>
            """
        )
    if atom_flush_check.get("level") == "warn":
        interpretation_cards.append(
            f"""
            <article class="card warn">
              <strong>atom flush 仍是异步尾巴</strong>
              <p>{esc(atom_flush_check.get("message"))}。这通常表示 <code>overview / atom / graph / vector</code> 已经出来了，但 cursor 还没有完全追到最后一条消息。对当前阶段来说，它更接近“异步收尾未完全对齐”，不是“核心记忆完全不可用”。</p>
            </article>
            """
        )
    if atoms_check.get("ok") and graph_check.get("ok") and vector_check.get("ok"):
        interpretation_cards.append(
            """
            <article class="card ok">
              <strong>优先关注的记忆层已落盘</strong>
              <p>当前更有价值的 <code>overview / atom / graph</code> 以及向量索引已经存在。这和当前的判断口径一致：先看这些层是否保住关键事实，不把 <code>episodes</code> 当成本轮主指标。</p>
            </article>
            """
        )
    if not interpretation_cards:
        interpretation_cards.append(
            """
            <article class="card ok">
              <strong>暂无新增运行解读</strong>
              <p>这一轮没有从完整性检查里提取到比常规状态表更强的新信号。</p>
            </article>
            """
        )

    issue_rows = []
    for item in checks:
        if item.get("ok") and item.get("level") != "warn":
            continue
        issue_rows.append(
            f"""
            <tr>
              <td><span class="pill {check_class(item)}">{check_label(item)}</span></td>
              <td>{esc(item.get("name"))}</td>
              <td>{esc(item.get("message"))}</td>
            </tr>
            """
        )

    probe_rows = []
    for item in (probe.get("results") or [])[:20]:
        missing = "；".join(
            " / ".join(group.get("terms") or [])
            for group in (item.get("memory_groups") or [])
            if not group.get("ok")
        ) or "-"
        probe_rows.append(
            f"""
            <tr>
              <td>{esc(item.get("question_id"))}</td>
              <td>{esc(item.get("status"))}</td>
              <td>{esc(compact(item.get("question"), 120))}</td>
              <td>{esc(missing)}</td>
              <td>{esc(item.get("diagnosis_detail"))}</td>
            </tr>
            """
        )

    check_cards = []
    for item in checks:
        check_cards.append(
            f"""
            <article class="card {check_class(item)}">
              <strong>{esc(check_label(item))} · {esc(item.get("name"))}</strong>
              <p>{esc(item.get("message"))}</p>
            </article>
            """
        )

    report_links = []
    for label, path in [
        ("最新代码问题与修复", latest_code_issues),
        ("外围系统问题与修复", latest_system_issues),
        ("conv-30 诊断", latest_diag),
        ("conv-30 状态", latest_status),
    ]:
        if path:
            report_links.append(f"<li><strong>{esc(label)}</strong>：<code>{esc(path)}</code></li>")
    if live_import:
        report_links.append(f"<li><strong>当前 full conv-30 导入日志</strong>：<code>{esc(live_import.get('log_path'))}</code></li>")
    if auto_eval:
        report_links.append(f"<li><strong>自动 QA/Judge 状态</strong>：<code>{esc(auto_eval.get('status_path'))}</code></li>")

    qa_bits = []
    if qa_summary_path and qa_summary:
        qa_bits.append(f"<li>QA summary：<code>{esc(qa_summary_path)}</code></li>")
        qa_bits.append(f"<li>QA 题数：<strong>{esc(qa_summary.get('count', 0))}</strong>；avg retrieval={esc(qa_summary.get('avg_retrieval_count', 0))}；avg iteration={esc(qa_summary.get('avg_iteration', 0))}</li>")
    if judge_summary_path and judge_summary:
        qa_bits.append(f"<li>Judge summary：<code>{esc(judge_summary_path)}</code></li>")
        qa_bits.append(f"<li>Judge 准确率：<strong>{(float(judge_summary.get('accuracy') or 0) * 100):.2f}%</strong>（{esc(judge_summary.get('correct', 0))}/{esc(judge_summary.get('count', 0))}）</li>")
    if wrong_analysis_path and wrong_analysis:
        qa_bits.append(f"<li>Wrong analysis：<code>{esc(wrong_analysis_path)}</code></li>")

    code_rows = []
    for item in code_findings:
        code_rows.append(
            f"""
            <article class="card {esc(item.get('level'))}">
              <strong>{esc(item.get('title'))}</strong>
              <p><strong>现象：</strong>{esc(item.get('symptom'))}</p>
              <p><strong>代码位置：</strong><code>{esc(item.get('code'))}</code></p>
              <p><strong>分析：</strong>{esc(item.get('analysis'))}</p>
            </article>
            """
        )

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory 每小时问题状态</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #16202a;
      --muted: #667085;
      --line: #d7deea;
      --ok-bg: #ecfdf3;
      --ok-fg: #067647;
      --warn-bg: #fff7ed;
      --warn-fg: #b54708;
      --bad-bg: #fef3f2;
      --bad-fg: #b42318;
      --shadow: 0 12px 30px rgba(16, 24, 40, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,"PingFang SC","Microsoft YaHei",sans-serif; }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 24px 18px 48px; }}
    section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; box-shadow: var(--shadow); padding: 20px 22px; margin-bottom: 16px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    h1 {{ font-size: 30px; line-height: 1.16; }}
    h2 {{ font-size: 20px; }}
    p, li {{ margin: 0; }}
    ul {{ padding-left: 18px; }}
    .muted {{ color: var(--muted); }}
    .hero {{ display: grid; grid-template-columns: 1.3fr .7fr; gap: 14px; align-items: start; }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .metric {{ border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; background: #fbfcff; }}
    .metric .k {{ font-size: 24px; font-weight: 800; }}
    .metric .t {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
    .pill {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 700; }}
    .pill.ok {{ background: var(--ok-bg); color: var(--ok-fg); }}
    .pill.warn {{ background: var(--warn-bg); color: var(--warn-fg); }}
    .pill.bad {{ background: var(--bad-bg); color: var(--bad-fg); }}
    .cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; background: #fbfcff; }}
    .card.ok {{ border-color: #b7ebc6; background: #f6fff8; }}
    .card.warn {{ border-color: #fedf89; background: #fffaf0; }}
    .card.bad {{ border-color: #fecdca; background: #fff7f7; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; vertical-align: top; border-top: 1px solid var(--line); padding: 10px 8px; }}
    thead th {{ border-top: none; color: var(--muted); font-size: 12px; }}
    code {{ font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size: 12px; background: #f5f6f8; padding: 2px 6px; border-radius: 6px; }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  </style>
</head>
<body>
<main>
  <section>
    <div class="hero">
      <div>
        <h1>EchoMemory 每小时问题状态</h1>
        <p class="muted">刷新时间：{esc(refreshed_at)} · 当前目标仍是持续修 EchoMemory 当前版本的问题，并把 LoCoMo conv-30 在不依赖原始对话兜底的前提下推进到 60% 以上。</p>
        <ul>
          {''.join(f'<li>{esc(item)}</li>' for item in key_findings)}
        </ul>
      </div>
      <div class="metrics">
        <div class="metric"><div class="t">完整性状态</div><div class="k">{esc(integrity.get('status'))}</div></div>
        <div class="metric"><div class="t">硬失败项</div><div class="k">{fail_count}</div></div>
        <div class="metric"><div class="t">Probe 缺失</div><div class="k">{probe_counts.get('missing', 0)}</div></div>
        <div class="metric"><div class="t">Probe 仅原文</div><div class="k">{probe_counts.get('archive_only', 0)}</div></div>
      </div>
    </div>
  </section>

  <section>
    <h2>当前路径</h2>
    <ul>
      <li>workspace：<code>{esc(workspace)}</code></li>
      <li>account：<code>{esc(args.account)}</code></li>
      <li>sample：<code>{esc(args.sample)}</code></li>
      <li>integrity summary：<code>{esc(integrity.get('summary_path'))}</code></li>
      <li>memory root：<code>{esc(integrity.get('memory_root'))}</code></li>
    </ul>
  </section>

  <section>
    <h2>当前后台导入</h2>
    <div class="cards">
      {(
        f'''
        <article class="card warn">
          <strong>v0.0.6 full conv-30 正在跑</strong>
          <p><strong>log：</strong><code>{esc(live_import.get("log_path"))}</code></p>
          <p><strong>已见 session：</strong>{esc(str(live_import.get("sessions_seen", 0)))} / 19；<strong>commit：</strong>{esc(str(live_import.get("commit_count", 0)))}；<strong>flush：</strong>{esc(str(live_import.get("flush_count", 0)))}</p>
          <p><strong>最近 session：</strong>{esc(live_import.get("latest_label") or live_import.get("latest_session_id") or "-")}</p>
          <p><strong>最近模型调用：</strong>{esc(live_import.get("latest_llm") or "-")}；<strong>LLM 调用累计：</strong>{esc(str(live_import.get("llm_count", 0)))}</p>
          <p><strong>预检：</strong>{'ok' if live_import.get('preflight_ok') else 'unknown'}；<strong>warning：</strong>{esc(str(live_import.get("warning_count", 0)))}</p>
          <p class="muted">{esc(live_import.get("tail") or "")}</p>
        </article>
        '''
      ) if live_import else '<article class="card ok"><strong>暂无后台 full conv-30 导入</strong><p>当前没有检测到新的 v0.0.6 full conv-30 import.log。</p></article>'}
    </div>
  </section>

  <section>
    <h2>本轮运行解读</h2>
    <div class="cards">
      {''.join(interpretation_cards)}
    </div>
  </section>

  <section>
    <h2>本轮解读口径</h2>
    <div class="cards">
      <article class="card warn">
        <strong>atom 生成是异步的</strong>
        <p>消息开始触发 <code>commit</code> 之后，<code>overview / atom / graph</code> 仍可能继续生成一段时间。LLM 调用有延迟，所以导入后不能立刻把 commit 返回当成“长期记忆已经全部可用”。</p>
      </article>
      <article class="card warn">
        <strong>当前先看 overview / atom / graph</strong>
        <p><code>episodes</code> 现在更像占位层，不是本轮判断效果的重点。评估 EchoMemory 是否真的有价值，优先看 overview、atom 和 graph 有没有完整落盘、有没有保住关键事实。</p>
      </article>
      <article class="card ok">
        <strong>当前页更偏后端与产物完整性</strong>
        <p>这里主要回答“有没有导进去、有没有抽出来、卡在代码哪一层”，不是直接替代最终 QA/Judge 准确率报告。</p>
      </article>
    </div>
  </section>

  <section>
    <h2>问题卡片</h2>
    <div class="cards">
      {''.join(check_cards)}
    </div>
  </section>

  <section>
    <h2>结合代码分析的当前结论</h2>
    <p class="muted">这里把“现象”直接对应到代码位置，方便每小时刷新时不只是看状态，还能知道应该去哪段逻辑继续追。</p>
    <div class="cards">
      {''.join(code_rows) or '<article class="card ok"><strong>暂无新的代码层问题</strong><p>当前没有从完整性或 wrong analysis 中提取到新的代码热点。</p></article>'}
    </div>
  </section>

  <section>
    <div class="split">
      <div>
        <h2>当前重点问题</h2>
        <table>
          <thead><tr><th>状态</th><th>项目</th><th>说明</th></tr></thead>
          <tbody>
            {''.join(issue_rows) or '<tr><td colspan="3">暂无未通过项。</td></tr>'}
          </tbody>
        </table>
      </div>
      <div>
        <h2>相关报告</h2>
        <ul>
          {''.join(report_links) or '<li>暂无已有报告。</li>'}
          {''.join(qa_bits) or '<li>暂无匹配到最近一轮 QA/Judge 摘要。</li>'}
        </ul>
      </div>
    </div>
  </section>

  <section>
    <h2>Gold vs Atom / Memory 探针</h2>
    <p class="muted">这里不是看“模型答得像不像”，而是看 gold 事实有没有真正进入长期记忆，以及卡在哪一层。</p>
    <table>
      <thead>
        <tr><th>question_id</th><th>状态</th><th>问题</th><th>缺失项</th><th>诊断</th></tr>
      </thead>
      <tbody>
        {''.join(probe_rows) or '<tr><td colspan="5">当前没有 probe 结果。</td></tr>'}
      </tbody>
    </table>
  </section>
</main>
</body>
</html>
"""

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html_text, encoding="utf-8")
    print(out_html)


if __name__ == "__main__":
    main()
