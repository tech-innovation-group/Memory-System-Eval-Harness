#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx")
REPO = ROOT / "Code/echomemory/echo_memory_v010"
BACKUP = ROOT / "Code/echomemory/echo_memory_backup_before_v010_20260615_005320"
RUN_DIR = ROOT / "locomo-eval-web/runs/echomemory_v010_subset20_baseline_20260615"
RESCUE_RUN_DIR = ROOT / "locomo-eval-web/runs/echomemory_v010_subset20_rescue_20260615"
QUICK5_QA_DIR = ROOT / "locomo-eval-web/runs/echomemory_v010_quick5_20260615/qa"
SMOKE_DIR = ROOT / "locomo-eval-web/runs/echomemory_v010_import_smoke_s1b_20260615/echomemory_import"
REPORT = ROOT / "locomo-eval-web/web/static/generated-reports/echomemory_v010_audit_report_latest.html"
FULL_PYTEST_LOG = ROOT / "locomo-eval-web/runs/echomemory_v010_pytest_full_20260615.log"
TARGET_PYTEST_LOG = ROOT / "locomo-eval-web/runs/echomemory_v010_pytest_targeted_20260615.log"
GAP_REPORT = ROOT / "locomo-eval-web/web/static/generated-reports/echomemory_v010_locomo_gap_analysis_20260615.html"
REFRESH_SCRIPT = ROOT / "locomo-eval-web/scripts/refresh_echomemory_v010_audit_report.sh"
REFRESH_LOG = ROOT / "locomo-eval-web/runs/echomemory_v010_audit_refresh.log"
LAUNCH_AGENT_LABEL = "com.locomo-eval.echomemory-v010-audit-refresh"
LAUNCH_AGENT_PLIST = ROOT / "Library/LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
RESCUE_SCREEN_NAME = "echomemory_v010_rescue"
UPSTREAM_REF = "e3a5220"

TRACKED_DIFF_FILES = [
    "pyproject.toml",
    "benchmarks/analyze_locomo_run.py",
    "benchmarks/echomem_locomo.yaml",
    "benchmarks/echomem_locomo_kimi.yaml",
    "benchmarks/monitor_memory_generation.py",
    "benchmarks/openview_locomo_eval_qa_session.py",
    "benchmarks/run_conv30_benchmark.sh",
    "benchmarks/run_locomo_flywheel.py",
]
NEW_FILES = [
    "benchmarks/bench_env.py",
    "echomem/contracts.py",
    "echomem/engine/__init__.py",
    "echomem/engine/base.py",
    "echomem/engine/manifest.py",
]


def sh(cmd: list[str] | str, *, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        check=check,
        text=True,
        capture_output=True,
        cwd=cwd or REPO,
    )


def git(*args: str, check: bool = True) -> str:
    cp = sh(["git", "-C", str(REPO), *args], check=check)
    return cp.stdout


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def read_text_tail(path: Path, *, max_chars: int = 10000) -> str:
    text = read_text(path)
    return text[-max_chars:] if len(text) > max_chars else text


def escape(text: str) -> str:
    return html.escape(text)


def summarize_pytest(log_text: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "summary": "未找到 pytest 摘要",
        "failed": [],
    }
    m = re.search(r"(\d+) failed, (\d+) passed, (\d+) skipped, (\d+) warning", log_text)
    if m:
        out["summary"] = f"{m.group(2)} passed, {m.group(1)} failed, {m.group(3)} skipped, {m.group(4)} warning"
    else:
        m = re.search(r"(\d+) passed, (\d+) warning", log_text)
        if m:
            out["summary"] = f"{m.group(1)} passed, {m.group(2)} warning"
        else:
            m = re.search(r"(\d+) passed", log_text)
            if m:
                out["summary"] = f"{m.group(1)} passed"
    failed = []
    for line in log_text.splitlines():
        if line.startswith("FAILED "):
            failed.append(line.removeprefix("FAILED ").strip())
    out["failed"] = failed
    return out


def current_git_state() -> dict[str, str]:
    return {
        "commit": git("rev-parse", "--short", "HEAD").strip(),
        "branch": git("branch", "--show-current").strip(),
        "status": git("status", "--short").strip(),
        "diff_stat": git("diff", "--stat").strip(),
    }


def upstream_file_text(path: str) -> str:
    cp = sh(["git", "-C", str(REPO), "show", f"{UPSTREAM_REF}:{path}"], check=False)
    return cp.stdout if cp.returncode == 0 else ""


def upstream_file_exists(path: str) -> bool:
    cp = sh(["git", "-C", str(REPO), "cat-file", "-e", f"{UPSTREAM_REF}:{path}"], check=False)
    return cp.returncode == 0


def redact_secrets(text: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_.-]{8,}", "sk-<redacted>", text)


def excerpt_matching(text: str, patterns: list[str]) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(p in line for p in patterns):
            out.append(f"{i}: {redact_secrets(line)}")
    return out


def collect_upstream_problem_evidence() -> dict[str, Any]:
    pyproject_upstream = upstream_file_text("pyproject.toml")
    yaml_upstream = upstream_file_text("benchmarks/echomem_locomo.yaml")
    shell_upstream = upstream_file_text("benchmarks/run_conv30_benchmark.sh")
    qa_script_upstream = upstream_file_text("benchmarks/openview_locomo_eval_qa_session.py")
    return {
        "missing_contracts": not upstream_file_exists("echomem/contracts.py"),
        "missing_engine_base": not upstream_file_exists("echomem/engine/base.py"),
        "missing_engine_manifest": not upstream_file_exists("echomem/engine/manifest.py"),
        "pyproject_has_http_extra": "[project.optional-dependencies]" in pyproject_upstream and "fastapi" in pyproject_upstream,
        "pyproject_has_requests_dev": "requests" in pyproject_upstream,
        "yaml_secret_lines": excerpt_matching(yaml_upstream, ["api_key:", "sk-"]),
        "shell_problem_lines": excerpt_matching(shell_upstream, ["/Users/su", "sk-", "8000", "31030"]),
        "qa_problem_lines": excerpt_matching(qa_script_upstream, ["/Users/su", "8000", "31030", "locomo_benchmark", "benchmark123"]),
    }


def diff_block_for(path: str) -> str:
    cp = sh(["git", "-C", str(REPO), "diff", "--", path], check=False)
    text = cp.stdout.strip()
    return text or f"# {path}\n# 无 diff 输出"


def new_file_block_for(path: str) -> str:
    abs_path = REPO / path
    text = read_text(abs_path)
    if not text:
        return f"# {path}\n# 文件不存在"
    return f"*** NEW FILE: {path} ***\n{text}"


def collect_change_blocks() -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for path in TRACKED_DIFF_FILES:
        blocks.append((path, diff_block_for(path)))
    for path in NEW_FILES:
        blocks.append((path, new_file_block_for(path)))
    return blocks


def matching_process_lines(*markers: str) -> list[str]:
    cp = sh(["ps", "-ax", "-o", "pid=,command="], check=False, cwd=ROOT)
    lines = []
    for raw in (cp.stdout or "").splitlines():
        if all(marker in raw for marker in markers):
            lines.append(" ".join(raw.split()))
    return lines[:5]


def launch_agent_status() -> dict[str, Any]:
    service = f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"
    cp = sh(["launchctl", "print", service], check=False, cwd=ROOT)
    loaded = cp.returncode == 0
    important = []
    if loaded:
        for line in cp.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith(("path =", "state =", "program =", "pid =", "last exit code =")):
                important.append(stripped)
    return {
        "label": LAUNCH_AGENT_LABEL,
        "plist_path": str(LAUNCH_AGENT_PLIST),
        "script_path": str(REFRESH_SCRIPT),
        "exists": LAUNCH_AGENT_PLIST.exists(),
        "loaded": loaded,
        "refresh_log_exists": REFRESH_LOG.exists(),
        "refresh_log_path": str(REFRESH_LOG),
        "status_excerpt": "\n".join(important[:8]),
        "error_excerpt": (cp.stderr or "").strip()[:400],
    }


def screen_session_running(name: str) -> bool:
    if not name:
        return False
    cp = sh(["screen", "-ls"], check=False, cwd=ROOT)
    return f".{name}" in (cp.stdout or "")


def extract_traceback(log_text: str) -> str:
    start = log_text.rfind("Traceback (most recent call last):")
    if start >= 0:
        return "\n".join(log_text[start:].splitlines()[:24])
    start = log_text.rfind("HardTimeoutError:")
    if start >= 0:
        return "\n".join(log_text[start:].splitlines()[:10])
    return ""


def summarize_live_log(log_text: str) -> dict[str, Any]:
    last_import: dict[str, Any] = {}
    last_verify: dict[str, Any] = {}
    last_llm: dict[str, Any] = {}
    last_ts = ""

    for line in log_text.splitlines():
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+", line)
        if m:
            last_ts = m.group(1)
        m = re.search(r"\[import\] session=([^\s]+) label=([^\s]+) expected_messages=(\d+)", line)
        if m:
            last_import = {
                "session_id": m.group(1),
                "label": m.group(2),
                "expected_messages": int(m.group(3)),
            }
        m = re.search(r"\[verify\] ([^\s]+) added_total=(\d+)/(\d+)", line)
        if m:
            last_verify = {
                "label": m.group(1),
                "added_total": int(m.group(2)),
                "expected_total": int(m.group(3)),
            }
        m = re.search(r"call_site=([a-z_]+).*session=(sess-[0-9a-f]+)", line)
        if m:
            last_llm = {
                "call_site": m.group(1),
                "session_id": m.group(2),
            }

    return {
        "last_import": last_import,
        "last_verify": last_verify,
        "last_llm": last_llm,
        "last_timestamp": last_ts,
    }


def structured_memory_probe(memory_root: str) -> dict[str, Any]:
    if not memory_root:
        return {
            "structured_atoms_count": 0,
            "episodes_count": 0,
            "entity_pages_count": 0,
        }
    root = Path(memory_root)
    atoms_bundle = root / "memory/.structured/atoms.json"
    atoms_count = 0
    if atoms_bundle.exists():
        payload = read_json(atoms_bundle)
        if isinstance(payload, dict):
            bundled_atoms = payload.get("atoms")
            if isinstance(bundled_atoms, dict):
                atoms_count = len(bundled_atoms)
            elif isinstance(bundled_atoms, list):
                atoms_count = len(bundled_atoms)
        elif isinstance(payload, list):
            atoms_count = len(payload)
    episodes_count = len(list((root / "memory/.episodes/episodes").glob("*.json")))
    entity_pages_count = len(list((root / "memory/entities").glob("**/*.md")))
    return {
        "structured_atoms_count": atoms_count,
        "episodes_count": episodes_count,
        "entity_pages_count": entity_pages_count,
    }


def load_accuracy(run_dir: Path) -> dict[str, Any]:
    judge_path = run_dir / "echomemory_qa/judge_summary.json"
    if not judge_path.exists():
        return {}
    summary = read_json(judge_path)
    correct = summary.get("correct")
    total = summary.get("total")
    accuracy = summary.get("accuracy")
    if accuracy is None and isinstance(correct, (int, float)) and isinstance(total, (int, float)) and total:
        accuracy = correct / total
    return {
        "path": str(judge_path),
        "correct": correct,
        "total": total,
        "accuracy": accuracy,
    }


def load_accuracy_from_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    summary = read_json(path)
    correct = summary.get("correct")
    total = summary.get("total") or summary.get("count")
    accuracy = summary.get("accuracy")
    if accuracy is None and isinstance(correct, (int, float)) and isinstance(total, (int, float)) and total:
        accuracy = correct / total
    return {
        "path": str(path),
        "correct": correct,
        "total": total,
        "accuracy": accuracy,
    }


def diagnose_quick5_wrong_answer(question: str, gold: str, response: str) -> str:
    q = (question or "").strip().lower()
    r = (response or "").strip().lower()
    if q.startswith("when") and ("exact date isn't specified" in r or "before " in r):
        return "时间粒度被 summary 抹平，检索拿到了相关事实，但回答只能退化成“某日期之前”而不是精确月份/日期。"
    if "both have in common" in q:
        return "共同点题被高频主题词带偏，模型抓住了“都喜欢舞蹈”，却没有把 gold 需要的“都失业并开始创业”提升为主结论。"
    if gold and gold.lower() not in r:
        return "检索结果已有相关线索，但回答阶段没有稳定提炼出 gold 所需的主事实。"
    return "当前样本需要人工复核。"


def quick5_status() -> dict[str, Any]:
    summary_path = QUICK5_QA_DIR / "summary.json"
    judge_path = QUICK5_QA_DIR / "judge_summary.json"
    csv_path = QUICK5_QA_DIR / "echomemory_memory_qa_results.csv"
    summary = read_json(summary_path)
    accuracy = load_accuracy_from_file(judge_path)
    wrong_items: list[str] = []
    wrong_details: list[dict[str, Any]] = []
    if csv_path.exists():
        try:
            import csv

            with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                for row in csv.DictReader(handle):
                    if str(row.get("result") or "").upper() == "WRONG":
                        qid = str(row.get("question_id") or "").strip()
                        question = compact(row.get("question") or "", 120)
                        gold = compact(row.get("answer") or "", 120)
                        response = compact(row.get("response") or "", 220)
                        wrong_items.append(f"{qid}: {question}")
                        wrong_details.append(
                            {
                                "question_id": qid,
                                "question": question,
                                "gold": gold,
                                "response": response,
                                "retrieval_count": row.get("retrieval_count"),
                                "memory_hit_count": row.get("memory_hit_count"),
                                "diagnosis": diagnose_quick5_wrong_answer(
                                    str(row.get("question") or ""),
                                    str(row.get("answer") or ""),
                                    str(row.get("response") or ""),
                                ),
                            }
                        )
        except Exception:
            wrong_items = []
            wrong_details = []
    return {
        "exists": summary_path.exists() or judge_path.exists() or csv_path.exists(),
        "summary_path": str(summary_path),
        "judge_path": str(judge_path),
        "csv_path": str(csv_path),
        "summary": summary,
        "accuracy": accuracy,
        "wrong_items": wrong_items[:5],
        "wrong_details": wrong_details[:5],
    }


def rescue_auto_eval_status() -> dict[str, Any]:
    path = RESCUE_RUN_DIR / "echomemory_qa/auto_eval_status.json"
    data = read_json(path)
    if not data:
        return {"exists": False, "path": str(path)}
    settle = data.get("memory_settle") or {}
    return {
        "exists": True,
        "path": str(path),
        **data,
        "stable_hits": data.get("stable_hits", settle.get("stable_hits")),
        "required_stable_hits": data.get("required_stable_hits", settle.get("required_stable_hits")),
        "snapshot": data.get("snapshot") or settle.get("snapshot") or {},
        "previous_snapshot": data.get("previous_snapshot") or settle.get("previous_snapshot") or {},
        "memory_settle_ready": settle.get("ready"),
        "memory_settle_timed_out": settle.get("timed_out"),
    }


def run_status(run_dir: Path, *, screen_name: str = "") -> dict[str, Any]:
    manifest_path = run_dir / "subset20_manifest.json"
    log_path = run_dir / "subset20_import.log"
    summary_path = run_dir / "echomemory_import/echomemory_import_summary.json"
    manifest = read_json(manifest_path)
    summary = read_json(summary_path)
    log_text = read_text_tail(log_path, max_chars=300000)
    live_log = summarize_live_log(log_text)
    process_lines = matching_process_lines("echomemory_locomo_import.py", str(run_dir))
    screen_running = screen_session_running(screen_name)
    process_running = bool(process_lines) or screen_running
    accuracy = load_accuracy(run_dir)
    if not summary:
        return {
            "exists": run_dir.exists(),
            "status": (
                "not_started"
                if not run_dir.exists()
                else ("running_no_summary_yet" if process_running else "summary_missing")
            ),
            "running": process_running,
            "process_lines": process_lines,
            "screen_running": screen_running,
            "manifest_path": str(manifest_path),
            "summary_path": str(summary_path),
            "log_path": str(log_path),
            "accuracy": accuracy,
        }

    record = (summary.get("records") or [{}])[0] if summary.get("records") else {}
    sessions = record.get("session_records") or []
    latest = sessions[-1] if sessions else {}
    latest_artifacts = (latest.get("commit_artifacts") or {}).get("memory_artifacts") or {}
    latest_memory_root = str(latest_artifacts.get("memory_root") or "")
    probe = structured_memory_probe(latest_memory_root)
    latest_summary_label = str(latest.get("session_key") or "")
    latest_import_label = str((live_log.get("last_import") or {}).get("label") or "")
    live_summary_drift = bool(
        process_running and latest_import_label and latest_summary_label and latest_import_label != latest_summary_label
    )
    raw_status = str(summary.get("status") or "")
    derived_status = raw_status
    status_note = str(summary.get("status_explanation") or "")
    if raw_status == "ECHOMEMORY_IMPORT_RUNNING" and not process_running:
        if "HardTimeoutError:" in log_text:
            derived_status = "ECHOMEMORY_IMPORT_FAILED_TIMEOUT"
            status_note = "导入摘要仍写着 RUNNING，但真实进程已退出；subset20 在 atom pipeline 导入阶段触发 600 秒硬超时。"
        elif "Traceback (most recent call last):" in log_text:
            derived_status = "ECHOMEMORY_IMPORT_FAILED"
            status_note = "导入摘要仍写着 RUNNING，但真实进程已退出；日志尾部存在未处理异常。"
        else:
            derived_status = "ECHOMEMORY_IMPORT_STOPPED"
            status_note = "导入摘要仍写着 RUNNING，但当前未检测到对应进程，状态可能已经陈旧。"

    elapsed_values = []
    for session in sessions:
        atom_flush = session.get("atom_flush") or {}
        value = atom_flush.get("elapsed_s")
        if isinstance(value, (int, float)):
            elapsed_values.append(float(value))
    return {
        "exists": True,
        "status": derived_status,
        "status_raw": raw_status,
        "status_note": status_note,
        "running": process_running,
        "process_lines": process_lines,
        "screen_running": screen_running,
        "warnings": summary.get("warnings") or [],
        "session_count": record.get("session_count"),
        "original_session_count": record.get("original_session_count"),
        "attempted_sessions": len(sessions),
        "complete_sessions": sum(1 for session in sessions if session.get("integrity") == "complete"),
        "latest_session_id": latest.get("session_id"),
        "latest_elapsed_s": ((latest.get("atom_flush") or {}).get("elapsed_s")) if latest else None,
        "max_elapsed_s": max(elapsed_values) if elapsed_values else None,
        "summary_path": str(summary_path),
        "log_path": str(log_path),
        "manifest_path": str(manifest_path),
        "memory_root": latest_memory_root,
        "structured_atoms_count": probe["structured_atoms_count"],
        "episodes_count": probe["episodes_count"],
        "entity_pages_count": probe["entity_pages_count"],
        "reported_atoms_count": latest_artifacts.get("atoms_count"),
        "reported_vector_items": latest_artifacts.get("vector_items"),
        "artifact_counter_mismatch": bool(
            probe["structured_atoms_count"] and not int(latest_artifacts.get("atoms_count") or 0)
        ),
        "error_excerpt": extract_traceback(log_text),
        "accuracy": accuracy,
        "workspace": manifest.get("workspace"),
        "account": manifest.get("account"),
        "latest_summary_label": latest_summary_label,
        "live_import_label": latest_import_label,
        "live_import_session_id": str((live_log.get("last_import") or {}).get("session_id") or ""),
        "live_verify_label": str((live_log.get("last_verify") or {}).get("label") or ""),
        "live_verify_added_total": (live_log.get("last_verify") or {}).get("added_total"),
        "live_verify_expected_total": (live_log.get("last_verify") or {}).get("expected_total"),
        "live_call_site": str((live_log.get("last_llm") or {}).get("call_site") or ""),
        "live_call_session_id": str((live_log.get("last_llm") or {}).get("session_id") or ""),
        "live_log_timestamp": str(live_log.get("last_timestamp") or ""),
        "live_summary_drift": live_summary_drift,
    }


def smoke_status() -> dict[str, Any]:
    summary = read_json(SMOKE_DIR / "echomemory_import_summary.json")
    if not summary:
        return {}
    record = (summary.get("records") or [{}])[0] if summary.get("records") else {}
    session = (record.get("session_records") or [{}])[0] if record.get("session_records") else {}
    return {
        "status": summary.get("status"),
        "qa_ready_samples": summary.get("qa_ready_samples"),
        "session_count": record.get("session_count"),
        "flush_elapsed_s": ((session.get("atom_flush") or {}).get("elapsed_s")),
        "summary_path": str(SMOKE_DIR / "echomemory_import_summary.json"),
    }


def current_problem_bullets(full_pytest: dict[str, Any], baseline: dict[str, Any], gap_link: str, rescue_auto: dict[str, Any]) -> list[str]:
    bullets = []
    bullets.append("完整 pytest 仍未通过：当前日志显示 1566 passed / 26 failed / 1 skipped / 1 warning，失败集中在外部依赖型 e2e、episode merge/project、SearchService 排序与短文本读取路径。")
    if baseline:
        bullets.append(
            f"官方 frozen subset20 基线没有正常收尾：当前只完成 {baseline.get('complete_sessions')}/{baseline.get('original_session_count')} 个 session，最后在 {baseline.get('latest_session_id') or '未知 session'} 上触发导入超时。"
        )
    bullets.append("LoCoMo 导入吞吐偏慢：已完成 session 的 atom flush 大多在 109-124 秒，最长一次已经逼近 300 秒，说明 0.1.0 在真实长对话导入上有明显性能压力。")
    bullets.append("完成态信号不稳定：import summary 多次出现 “commit task status=pending, but strict QA-ready artifacts are already complete”，commit 返回态与可答题态没有完全对齐。")
    if baseline.get("live_summary_drift"):
        bullets.append(
            f"live 进度与摘要快照存在偏移：日志已经跑到 {baseline.get('live_import_label')} / {baseline.get('live_call_site')}，但 summary 最新完成记录仍停在 {baseline.get('latest_summary_label')}。"
        )
    if rescue_auto.get("exists") and rescue_auto.get("stage") == "waiting_async_memory_settle":
        stable_hits = rescue_auto.get("stable_hits")
        required_hits = rescue_auto.get("required_stable_hits")
        required_hits_text = required_hits if required_hits is not None else "-"
        snapshot = rescue_auto.get("snapshot") or {}
        bullets.append(
            f"subset20 的 wait_and_eval 还存在稳定性卡住问题：auto_eval_status 长时间停在 waiting_async_memory_settle，stable_hits={stable_hits}/{required_hits_text}，但 snapshot 仍把 complete_sessions 记成 {snapshot.get('complete_sessions')}。"
        )
    elif rescue_auto.get("exists") and rescue_auto.get("stage") == "running_repair" and rescue_auto.get("memory_settle_timed_out"):
        stable_hits = rescue_auto.get("stable_hits")
        required_hits = rescue_auto.get("required_stable_hits")
        required_hits_text = required_hits if required_hits is not None else "-"
        snapshot = rescue_auto.get("snapshot") or {}
        bullets.append(
            "subset20 的 wait_and_eval 已经从“等待稳定”升级成“等待超时后自动修复”："
            f"memory_settle timed_out=True，stable_hits={stable_hits}/{required_hits_text}，"
            f"但 snapshot 仍把 complete_sessions 记成 {snapshot.get('complete_sessions')}、vector_count 记成 {snapshot.get('vector_count')}。"
        )
    bullets.append("日志里出现 `Episode projection failed` 和 graph self-loop 拒绝，说明 episode/graph 这两层在真实数据上还会冒出边界错误。")
    bullets.append("0.1.0 的 atom 持久化布局已经变成 `.structured/atoms.json`，旧评测脚本如果继续按 `.structured/atoms/*.json` 统计，会把已落盘的 atom 误记成 0。")
    bullets.append(f"更多 LoCoMo 结构问题说明：{gap_link}")
    return bullets


def render() -> str:
    git_state = current_git_state()
    full_pytest = summarize_pytest(read_text(FULL_PYTEST_LOG))
    targeted_pytest = summarize_pytest(read_text(TARGET_PYTEST_LOG))
    baseline = run_status(RUN_DIR)
    rescue = run_status(RESCUE_RUN_DIR, screen_name=RESCUE_SCREEN_NAME)
    rescue_auto = rescue_auto_eval_status()
    quick5 = quick5_status()
    smoke = smoke_status()
    upstream = collect_upstream_problem_evidence()
    changes = collect_change_blocks()
    launchd = launch_agent_status()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rescue_required_hits_text = rescue_auto.get("required_stable_hits")
    if rescue_required_hits_text is None:
        rescue_required_hits_text = "-"

    original_problem_rows = [
        (
            "上游 tag 缺少兼容导入路径",
            "tests/contract 依赖的 echomem.contracts / echomem.engine.* 在上游树里不存在。",
            f"contracts.py 缺失={upstream['missing_contracts']}；engine/base.py 缺失={upstream['missing_engine_base']}；engine/manifest.py 缺失={upstream['missing_engine_manifest']}",
        ),
        (
            "benchmark 配置内联真实 secret",
            "上游 benchmark YAML 和 shell 直接写了 sk- 开头的 token，不适合公开仓库复现。",
            "<br>".join(escape(x) for x in upstream["yaml_secret_lines"][:6] + upstream["shell_problem_lines"][:6]),
        ),
        (
            "benchmark 脚本写死作者机器路径",
            "上游脚本直接依赖 /Users/su/... 路径，别人拉仓后无法直接运行。",
            "<br>".join(escape(x) for x in (upstream["shell_problem_lines"] + upstream["qa_problem_lines"])[-8:]),
        ),
        (
            "端口与文档不一致",
            "README/注释提到 31030，但评测脚本把 engine endpoint 指向 localhost:8000。",
            "<br>".join(escape(x) for x in upstream["qa_problem_lines"] if "8000" in x or "31030" in x),
        ),
        (
            "上游 pyproject 没把 HTTP 本地测试路径配全",
            "原始 pyproject 没有 http extra，dev 里也没有 requests。",
            f"pyproject_has_http_extra={upstream['pyproject_has_http_extra']}；pyproject_has_requests_dev={upstream['pyproject_has_requests_dev']}",
        ),
    ]

    current_problem_rows = [
        ("完整 pytest 未绿", str(full_pytest["summary"])),
        (
            "frozen subset20 导入中途退出",
            f"status={baseline.get('status')}；complete_sessions={baseline.get('complete_sessions')}/{baseline.get('original_session_count')}；最新异常见导入日志尾部。",
        ),
        (
            "LoCoMo 导入过慢",
            f"已完成 session 的 atom flush 最高约 {baseline.get('max_elapsed_s')} 秒；官方 frozen 协议 600 秒硬超时已被打穿。",
        ),
        (
            "commit/QA-ready 状态有偏差",
            "import summary 反复提示 commit task pending，但严格 QA-ready artifacts 已经齐全，commit 返回态与可用态不完全一致。",
        ),
        (
            "结构计数与真实落盘不一致",
            f"官方 baseline 真实 atoms.json 中已有 {baseline.get('structured_atoms_count')} 个 atom，但旧摘要统计 atoms_count={baseline.get('reported_atoms_count')}。",
        ),
        (
            "真实数据下的边界错误",
            "导入日志已出现 Episode projection failed 与 self-loop edge 拒绝，说明 episode/graph 边界逻辑还不稳。",
        ),
        (
            "subset20 QA 前稳定性检查卡住",
            (
                f"auto_eval_status={rescue_auto.get('stage')}；stable_hits={rescue_auto.get('stable_hits')}/"
                f"{rescue_required_hits_text}；snapshot.complete_sessions="
                f"{(rescue_auto.get('snapshot') or {}).get('complete_sessions')}"
            ) if rescue_auto.get("exists") else "尚未产出 auto_eval_status.json",
        ),
    ]
    problem_bullets = current_problem_bullets(full_pytest, baseline, GAP_REPORT.as_uri(), rescue_auto)

    blocks_html = []
    for path, content in changes:
        blocks_html.append(
            f"<details class='codebox'><summary>{escape(path)}</summary><pre>{escape(redact_secrets(content))}</pre></details>"
        )

    failed_tests = full_pytest["failed"][:26]
    failed_list = "".join(f"<li><code>{escape(name)}</code></li>" for name in failed_tests)
    problem_list = "".join(f"<li>{escape(item)}</li>" for item in problem_bullets[:-1]) + f"<li><a href='{GAP_REPORT.as_uri()}'>LoCoMo 结构问题补充页</a></li>"
    status_text = git_state["status"] or "(clean)"
    diff_stat = git_state["diff_stat"] or "(no diff)"
    refresh_note = "本地 launchd 已加载，每 2 小时会真正重生成 HTML。" if launchd["loaded"] else "HTML 页面会自动刷新，但本地重生成任务还没加载完成。"
    baseline_accuracy = baseline.get("accuracy") or {}
    rescue_accuracy = rescue.get("accuracy") or {}
    quick5_accuracy = quick5.get("accuracy") or {}
    quick5_accuracy_text = None
    if quick5_accuracy:
        quick5_accuracy_text = (
            f"{quick5_accuracy.get('correct')}/{quick5_accuracy.get('total')} "
            f"({float(quick5_accuracy.get('accuracy') or 0) * 100:.2f}%)"
        )
    quick5_wrong_detail_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('question_id') or ''))}</code></td>"
        f"<td>{escape(str(item.get('question') or ''))}</td>"
        f"<td>{escape(str(item.get('gold') or ''))}</td>"
        f"<td>{escape(str(item.get('response') or ''))}</td>"
        f"<td>{escape(str(item.get('diagnosis') or ''))}</td>"
        "</tr>"
        for item in (quick5.get("wrong_details") or [])
    )
    rescue_block = ""
    if rescue.get("exists"):
        rescue_live_note = ""
        if rescue.get("live_summary_drift"):
            rescue_live_note = (
                f"注意：summary 最新完成记录仍停在 <code>{escape(str(rescue.get('latest_summary_label') or '-'))}</code>，"
                f"但实时日志已进入 <code>{escape(str(rescue.get('live_import_label') or '-'))}</code> / "
                f"<code>{escape(str(rescue.get('live_call_site') or '-'))}</code>。"
            )
        elif rescue.get("live_import_label"):
            rescue_live_note = (
                f"实时日志最近看到 <code>{escape(str(rescue.get('live_import_label')))}</code>，"
                f"阶段 <code>{escape(str(rescue.get('live_call_site') or '-'))}</code>，"
                f"时间 <code>{escape(str(rescue.get('live_log_timestamp') or '-'))}</code>。"
            )
        rescue_block = f"""
      <div class="callout {'good' if rescue_accuracy else 'warn'}">
        <strong>工程补跑（rescue）</strong>
        <div>run_dir=<code>{escape(str(RESCUE_RUN_DIR))}</code></div>
        <div>status=<code>{escape(str(rescue.get('status')))}</code>，running=<code>{escape(str(rescue.get('running')))}</code>，complete_sessions=<code>{escape(str(rescue.get('complete_sessions')))}</code>/<code>{escape(str(rescue.get('original_session_count')))}</code></div>
        <div>screen_running=<code>{escape(str(rescue.get('screen_running')))}</code>，attempted_sessions=<code>{escape(str(rescue.get('attempted_sessions')))}</code></div>
        <div>{rescue_live_note or '当前还没有提取到足够的实时进度摘要。'}</div>
        <div>auto_eval_stage=<code>{escape(str(rescue_auto.get('stage') or '-'))}</code>，stable_hits=<code>{escape(str(rescue_auto.get('stable_hits') or '-'))}</code>/<code>{escape(str(rescue_required_hits_text))}</code>，timed_out=<code>{escape(str(rescue_auto.get('memory_settle_timed_out')))}</code></div>
        <div>{"当前已产出准确率：" + escape(f"{rescue_accuracy.get('correct')}/{rescue_accuracy.get('total')} ({(float(rescue_accuracy.get('accuracy') or 0) * 100):.2f}%)") if rescue_accuracy else "当前还没有 rescue 准确率文件。导入/QA 继续推进后，这里会自动刷新。"}</div>
      </div>
"""

    quick5_block = ""
    if quick5.get("exists"):
        quick5_wrong = ""
        if quick5.get("wrong_items"):
            quick5_wrong = "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in quick5["wrong_items"]) + "</ul>"
        quick5_block = f"""
      <div class="callout {'good' if quick5_accuracy else 'warn'}">
        <strong>同仓 quick5 smoke（已完成）</strong>
        <div>qa_dir=<code>{escape(str(QUICK5_QA_DIR))}</code></div>
        <div>{"准确率：<code>" + escape(quick5_accuracy_text or "") + "</code>" if quick5_accuracy_text else "尚未产出 judge_summary.json"}</div>
        <div>summary：<a href="{Path(quick5.get('summary_path','')).as_uri() if quick5.get('summary_path') else '#'}">summary.json</a>，judge：<a href="{Path(quick5.get('judge_path','')).as_uri() if quick5.get('judge_path') else '#'}">judge_summary.json</a>，csv：<a href="{Path(quick5.get('csv_path','')).as_uri() if quick5.get('csv_path') else '#'}">echomemory_memory_qa_results.csv</a></div>
        <div>检索/回答健康度：retrieval_ok=<code>{escape(str((quick5.get('summary') or {}).get('retrieval_ok_count')))}</code>，model_ok=<code>{escape(str((quick5.get('summary') or {}).get('model_ok_count')))}</code>，answer_ok=<code>{escape(str((quick5.get('summary') or {}).get('answer_ok_count')))}</code>，avg_retrieval_count=<code>{escape(str((quick5.get('summary') or {}).get('avg_retrieval_count')))}</code></div>
        {quick5_wrong}
      </div>
"""

    error_box = ""
    if baseline.get("error_excerpt"):
        error_box = f"""
      <div class="callout bad">
        <strong>官方 frozen baseline 异常尾部</strong>
        <pre>{escape(redact_secrets(baseline['error_excerpt']))}</pre>
      </div>
"""

    mismatch_box = ""
    if baseline.get("artifact_counter_mismatch"):
        mismatch_box = f"""
      <div class="callout warn">
        <strong>记忆计数存在旧版统计偏差</strong>
        <div>官方 baseline 的最新 memory root 为 <code>{escape(str(baseline.get('memory_root') or ''))}</code>。</div>
        <div>这里真实已经有 <code>{escape(str(baseline.get('structured_atoms_count')))}</code> 个结构化 atom、<code>{escape(str(baseline.get('episodes_count')))}</code> 个 episode、<code>{escape(str(baseline.get('entity_pages_count')))}</code> 个实体页面，但旧摘要统计仍写成 <code>atoms_count={escape(str(baseline.get('reported_atoms_count')))}</code>。</div>
      </div>
"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta http-equiv="refresh" content="7200">
  <title>EchoMemory 0.1.0 审计报告</title>
  <style>
    :root {{
      color-scheme: light;
      --bg:#f5f5f7; --card:#fff; --text:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7;
      --blue:#0071e3; --orange:#b15b00; --red:#b42318; --green:#11845b;
      --shadow:0 10px 30px rgba(0,0,0,.08); --radius:18px;
      --mono:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace;
      --sans:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Microsoft YaHei",sans-serif;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:var(--sans); line-height:1.68; }}
    .page {{ width:min(100%, 980px); margin:0 auto; padding:18px 14px 72px; }}
    .hero,.card {{ background:var(--card); border:1px solid rgba(210,210,215,.8); border-radius:var(--radius); box-shadow:var(--shadow); }}
    .hero {{ padding:22px 18px; margin-bottom:16px; }}
    .card {{ padding:18px 16px; margin-bottom:14px; }}
    h1,h2,h3 {{ margin:0 0 10px; line-height:1.24; letter-spacing:0; }}
    h1 {{ font-size:1.86rem; }} h2 {{ font-size:1.22rem; }} h3 {{ font-size:1.02rem; }}
    p {{ margin:0 0 12px; }} .muted {{ color:var(--muted); }}
    .pillrow {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }}
    .pill {{ display:inline-flex; align-items:center; padding:6px 10px; border-radius:999px; background:#f0f7ff; color:#004a99; border:1px solid rgba(0,113,227,.16); font-size:.9rem; }}
    .stats {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px; }}
    .stat {{ border:1px solid var(--line); border-radius:14px; background:#fbfbfd; padding:12px; }}
    .stat .num {{ font-size:1.35rem; font-weight:700; margin-bottom:4px; }}
    .split {{ display:grid; grid-template-columns:1fr; gap:14px; }}
    .callout {{ border-left:4px solid var(--blue); border-radius:12px; background:#f5f9ff; padding:12px; margin:12px 0; }}
    .warn {{ border-left-color:var(--orange); background:#fff7ed; }}
    .bad {{ border-left-color:var(--red); background:#fff5f5; }}
    .good {{ border-left-color:var(--green); background:#f2fbf7; }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:.95rem; }}
    th,td {{ text-align:left; vertical-align:top; padding:10px; border-bottom:1px solid rgba(210,210,215,.8); }}
    th {{ background:#fafafc; font-weight:700; }}
    tr:last-child td {{ border-bottom:0; }}
    code {{ font-family:var(--mono); background:#f6f6f8; border-radius:8px; padding:.15em .38em; font-size:.92em; word-break:break-word; }}
    pre {{ margin:10px 0 0; padding:12px; background:#111214; color:#f5f5f7; border-radius:14px; overflow:auto; white-space:pre-wrap; word-break:break-word; font-family:var(--mono); font-size:.87rem; line-height:1.55; }}
    ul {{ margin:0; padding-left:1.1rem; }} li {{ margin-bottom:8px; }}
    a {{ color:var(--blue); text-decoration:none; }}
    .codebox {{ margin-top:10px; border:1px solid var(--line); border-radius:14px; background:#fcfcfd; padding:10px 12px; }}
    .footer {{ margin-top:18px; color:var(--muted); font-size:.92rem; text-align:center; }}
    @media (min-width:860px) {{
      .split {{ grid-template-columns:1.05fr .95fr; }}
      .stats {{ grid-template-columns:repeat(4,minmax(0,1fr)); }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory 0.1.0 拉取 / 备份 / 审计 / 跑分报告</h1>
      <p class="muted">报告生成时间：{escape(now)}。页面每 2 小时自动刷新；{escape(refresh_note)}</p>
      <p>这份报告把 4 件事放在一起：<strong>上游原始代码的问题</strong>、<strong>本地为了可复现做的修改</strong>、<strong>当前 0.1.0 的测试与准确率状态</strong>、以及<strong>可直接转给别人的 diff 证据</strong>。</p>
      <div class="pillrow">
        <span class="pill">clone: {escape(str(REPO))}</span>
        <span class="pill">backup: {escape(str(BACKUP))}</span>
        <span class="pill">upstream tag: {UPSTREAM_REF}</span>
        <span class="pill">branch: {escape(git_state['branch'])}</span>
      </div>
    </section>

    <section class="split">
      <div class="card">
        <h2>拉取与备份</h2>
        <table>
          <tr><th>项目</th><th>状态</th></tr>
          <tr><td>上游 tag</td><td><code>{UPSTREAM_REF}</code> / 当前 HEAD <code>{escape(git_state['commit'])}</code></td></tr>
          <tr><td>当前工作分支</td><td><code>{escape(git_state['branch'])}</code></td></tr>
          <tr><td>旧仓备份</td><td><code>{escape(str(BACKUP))}</code></td></tr>
          <tr><td>新 clone</td><td><code>{escape(str(REPO))}</code></td></tr>
        </table>
        <div class="callout good">
          <strong>当前 git 状态</strong>
          <pre>{escape(status_text)}</pre>
        </div>
      </div>

      <div class="card">
        <h2>验证概览</h2>
        <div class="stats">
          <div class="stat"><div class="num">{escape(str(targeted_pytest['summary']))}</div><div>定向回归</div></div>
          <div class="stat"><div class="num">{escape(str(full_pytest['summary']))}</div><div>完整 pytest</div></div>
          <div class="stat"><div class="num">{baseline.get('complete_sessions','-')}/{baseline.get('original_session_count','-')}</div><div>frozen subset20</div></div>
          <div class="stat"><div class="num">{escape(quick5_accuracy_text) if quick5_accuracy_text else smoke.get('qa_ready_samples','-')}</div><div>{'quick5 准确率' if quick5_accuracy else '单 session smoke 可答题样本'}</div></div>
        </div>
        <div class="callout warn">
          <strong>准确率结论</strong>
          <div>0.1.0 的 frozen subset20 基线目前<strong>没有正常收尾</strong>。导入摘要表面上还写着 <code>{escape(str(baseline.get('status_raw') or baseline.get('status')))}</code>，但真实进程已经退出，当前最直接的问题是 LoCoMo 导入链路在 atom pipeline 阶段被 600 秒硬超时打断。</div>
          <div>{'不过，基于同一份 0.1.0 rescue 导入完成后的工作空间，我已经额外跑出一组 quick5 smoke：<code>' + escape(quick5_accuracy_text or '') + '</code>。' if quick5_accuracy_text else '目前还没有额外 smoke 准确率。'}</div>
          <div>作为历史对照，完整 conv-30 的旧版链路结果是 44/81 = 54.32%，详细结构诊断见 <a href="{GAP_REPORT.as_uri()}">LoCoMo 不足点分析页</a>。</div>
        </div>
        <div class="callout {'good' if launchd['loaded'] else 'warn'}">
          <strong>2 小时自动刷新</strong>
          <div>LaunchAgent：<code>{escape(launchd['label'])}</code> / loaded=<code>{escape(str(launchd['loaded']))}</code></div>
          <div>plist：<code>{escape(launchd['plist_path'])}</code></div>
          <div>script：<code>{escape(launchd['script_path'])}</code></div>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>必须单独指出：上游原始代码存在的问题</h2>
      <p>这一节只写“原始代码就有的问题”，方便你直接告诉别人。证据都来自 <code>{UPSTREAM_REF}</code> 这棵树本身，不依赖我后面的本地修改。</p>
      <table>
        <thead><tr><th>原始问题</th><th>为什么算问题</th><th>证据</th></tr></thead>
        <tbody>
          {''.join(f"<tr><td>{escape(a)}</td><td>{escape(b)}</td><td>{c}</td></tr>" for a,b,c in original_problem_rows)}
        </tbody>
      </table>
      <div class="callout bad">
        <strong>一句话总结</strong>
        <div>上游 0.1.0 最大的“可复现性问题”不是算法本身，而是：<strong>缺兼容导入、benchmark 写死作者环境、配置里直接塞 secret、脚本端口不统一、HTTP 本地测试路径没配齐</strong>。</div>
      </div>
    </section>

    <section class="card">
      <h2>我本地做了哪些修改，为什么改</h2>
      <table>
        <thead><tr><th>改动</th><th>目的</th></tr></thead>
        <tbody>
          <tr><td><code>echomem/contracts.py</code> + <code>echomem/engine/*</code></td><td>补回 contract tests 依赖的旧导入路径，避免上游仓在测试收集阶段就炸掉。</td></tr>
          <tr><td><code>pyproject.toml</code></td><td>补 <code>http</code> extra 和 <code>requests</code> dev 依赖，让 HTTP 协议测试可直接安装运行。</td></tr>
          <tr><td><code>benchmarks/bench_env.py</code></td><td>把机器路径、端口、数据根目录、账户等集中成环境变量入口。</td></tr>
          <tr><td><code>benchmarks/*.yaml</code> / <code>run_conv30_benchmark.sh</code></td><td>去掉内联 secret，改成环境变量占位，并把 8000/31030 这类端口混乱收敛掉。</td></tr>
          <tr><td><code>openview_locomo_eval_qa_session.py</code> 等 benchmark 脚本</td><td>用统一 env 配置替代 <code>/Users/su/...</code> 与固定账户名密码。</td></tr>
          <tr><td>评测辅助脚本</td><td>补了 0.1.0 新 atom 布局统计、失败态识别和自动刷新，让 HTML 能看见“真问题”而不是被旧计数和 stale running 误导。</td></tr>
        </tbody>
      </table>
      <div class="callout good">
        <strong>改动后的定向验证</strong>
        <div><a href="{TARGET_PYTEST_LOG.as_uri()}">pytest targeted log</a> 显示：<code>{escape(str(targeted_pytest['summary']))}</code>。</div>
        <div>同时当前环境已能导入 <code>fastapi 0.137.0</code>、<code>uvicorn 0.49.0</code>、<code>requests 2.34.2</code>。</div>
      </div>
      <div class="callout">
        <strong>diff 统计</strong>
        <pre>{escape(diff_stat)}</pre>
      </div>
    </section>

    <section class="card">
      <h2>当前 0.1.0 还存在的问题</h2>
      <ul>
        {problem_list}
      </ul>
      <table>
        <thead><tr><th>当前问题</th><th>现状</th></tr></thead>
        <tbody>
          {''.join(f"<tr><td>{escape(a)}</td><td>{escape(b)}</td></tr>" for a,b in current_problem_rows)}
        </tbody>
      </table>
      <h3>完整 pytest 失败清单（摘要）</h3>
      <ul>{failed_list}</ul>
      <div class="callout warn">
        <strong>怎么理解这些失败</strong>
        <div>这里混了三类问题：1) 需要额外外部服务的 e2e 测试；2) episode memory / merge 边界逻辑当前不稳；3) SearchService 的排序、短文本不读盘约束与现实现状不一致。</div>
      </div>
      {error_box}
      {mismatch_box}
    </section>

    <section class="card">
      <h2>准确率测试进度</h2>
      <table>
        <thead><tr><th>项目</th><th>当前状态</th></tr></thead>
        <tbody>
          <tr><td>单 session smoke import</td><td>status=<code>{escape(str(smoke.get('status')))}</code>，qa_ready_samples=<code>{escape(str(smoke.get('qa_ready_samples')))}</code>，flush_elapsed_s=<code>{escape(str(smoke.get('flush_elapsed_s')))}</code></td></tr>
          <tr><td>同仓 quick5 smoke</td><td>{"<code>" + escape(quick5_accuracy_text or "") + "</code>" if quick5_accuracy_text else "尚未产出 judge_summary.json"}</td></tr>
          <tr><td>subset20 官方 frozen 基线</td><td>status=<code>{escape(str(baseline.get('status')))}</code>（raw=<code>{escape(str(baseline.get('status_raw')))}</code>），running=<code>{escape(str(baseline.get('running')))}</code>，complete_sessions=<code>{escape(str(baseline.get('complete_sessions')))}</code>/<code>{escape(str(baseline.get('original_session_count')))}</code></td></tr>
          <tr><td>当前最新 session</td><td><code>{escape(str(baseline.get('latest_session_id')))}</code>，最近一次 atom flush 约 <code>{escape(str(baseline.get('latest_elapsed_s')))}</code> 秒，历史最大约 <code>{escape(str(baseline.get('max_elapsed_s')))}</code> 秒</td></tr>
          <tr><td>官方 frozen 准确率</td><td>{"<code>" + escape(str(baseline_accuracy.get('correct'))) + "/" + escape(str(baseline_accuracy.get('total'))) + f" ({float(baseline_accuracy.get('accuracy') or 0) * 100:.2f}%)</code>" if baseline_accuracy else "尚未产出 judge_summary.json"}</td></tr>
          <tr><td>导入摘要</td><td><a href="{Path(baseline.get('summary_path','')).as_uri() if baseline.get('summary_path') else '#'}">echomemory_import_summary.json</a></td></tr>
          <tr><td>导入日志</td><td><a href="{Path(baseline.get('log_path','')).as_uri() if baseline.get('log_path') else '#'}">subset20_import.log</a></td></tr>
        </tbody>
      </table>
      {quick5_block}
      <div class="callout warn">
        <strong>当前该怎么读这组结果</strong>
        <div>官方 frozen baseline 这次更像是“结构与完成性审计”而不是最终准确率结果。它已经证明 0.1.0 在 LoCoMo 真实导入链路上存在明显的超时与状态漂移问题；等 rescue 补跑完成后，这个页面会自动补上新的准确率文件。</div>
        <div>同时，subset20 的 rescue 链路目前又暴露出一个新的评测问题：<code>echomemory_wait_and_eval.py</code> 的稳定性等待已经超时，并自动转入 <code>running_repair</code>；但快照里的 <code>complete_sessions</code> / <code>vector_count</code> 仍明显落后于导入完成态。这和同一工作空间下 quick5 能正常完成形成了直接对照。</div>
      </div>
      {rescue_block}
    </section>

    <section class="card">
      <h2>LoCoMo 结果暴露出的答题短板</h2>
      <table>
        <thead><tr><th>题目</th><th>问题</th><th>金标</th><th>模型回答</th><th>暴露出的短板</th></tr></thead>
        <tbody>
          {quick5_wrong_detail_rows or '<tr><td colspan="5">当前 quick5 没有可展开的错题明细。</td></tr>'}
        </tbody>
      </table>
      <div class="callout warn">
        <strong>怎么理解这两类错题</strong>
        <div>这批 quick5 的一个关键信号是：<code>retrieval_ok_count=5</code>、<code>model_ok_count=5</code>、<code>answer_ok_count=5</code>，说明问题不只是“完全没检索到”。至少在当前样本里，更明显的是两类能力短板：</div>
        <div>1) <strong>时间粒度丢失</strong>：session summary 经常把精确时间压成 “Before ... / 2023-04 / unknown earlier date”，导致时间题容易退化成模糊区间。</div>
        <div>2) <strong>共性/抽象题被主题词带偏</strong>：检索命中了很多“shared passion for dance”一类高频摘要，但没有稳定把“都失业并创业”这类更贴近 gold 的抽象共性顶到最前。</div>
      </div>
    </section>

    <section class="card">
      <h2>修改代码输出</h2>
      <p>下面直接放这次主要代码修改的 diff / 新文件内容，方便你转发给别人看“到底改了什么”。为了可读性，我故意跳过了 <code>uv.lock</code> 这类机械变化。</p>
      {''.join(blocks_html)}
    </section>

    <section class="card">
      <h2>相关文件</h2>
      <ul>
        <li><a href="{FULL_PYTEST_LOG.as_uri()}">完整 pytest 日志</a></li>
        <li><a href="{TARGET_PYTEST_LOG.as_uri()}">定向 pytest 日志</a></li>
        <li><a href="{GAP_REPORT.as_uri()}">LoCoMo 结构性问题补充分析</a></li>
        <li><a href="{REPORT.as_uri()}">本报告稳定地址</a></li>
        <li><a href="{REFRESH_LOG.as_uri() if REFRESH_LOG.exists() else '#'}">自动刷新日志</a></li>
      </ul>
    </section>

    <div class="footer">自动生成脚本：/Users/chx/locomo-eval-web/scripts/generate_echomemory_v010_audit_report.py</div>
  </div>
</body>
</html>
"""


def main() -> None:
    REPORT.write_text(render(), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
