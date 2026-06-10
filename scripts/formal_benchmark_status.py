#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
DATASET_DIR = ROOT / "dataset"
EXTERNAL_DIR = ROOT / "external"

LONGMEM_RUN = RUNS_DIR / "formal_longmemeval_s_full_openviking_20260606_1530"
HOTPOT_RUN = RUNS_DIR / "formal_hotpotqa_distractor_full_openviking_20260606_1530"
TAU2_FORMAL_RUNS = {
    "airline": "formal_tau2_airline_base_gpt55_20260606",
    "retail": "formal_tau2_retail_base_gpt55_20260606",
    "telecom": "formal_tau2_telecom_base_gpt55_20260606",
    "banking_knowledge": "formal_tau2_banking_knowledge_bm25_gpt55_20260606",
}
TAU2_LEADERBOARD_DOMAINS = ["airline", "retail", "telecom", "banking_knowledge"]
TAU2_CURRENT_DOMAINS = TAU2_LEADERBOARD_DOMAINS
TAU2_SUPERVISOR_LOG = RUNS_DIR / "formal_tau2_supervisor_20260606.log"
TAU2_SUPERVISOR_STDOUT = RUNS_DIR / "formal_tau2_supervisor_20260606.stdout.log"


def read_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if rows:
        # Formal CSVs are appended while this status script reads them. If the
        # reader catches the final row mid-write, csv.DictReader returns a row
        # with missing tail status fields; dropping only that trailing row avoids
        # publishing a transient false strict failure.
        tail = rows[-1]
        if any(tail.get(field) in (None, "") for field in ("health_status", "model_status", "answer_status")):
            rows = rows[:-1]
    return rows


def count_json_items(path: Path) -> int | None:
    data = read_json(path)
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("data", "examples", "questions", "tasks"):
            if isinstance(data.get(key), list):
                return len(data[key])
    return None


def strict_failed_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("health_status") != "ok"
        or row.get("model_status") == "failed"
        or row.get("answer_status") == "failed"
        or bool((row.get("retrieval_error") or "").strip())
    ]


def counter(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(Counter(row.get(field, "") for row in rows))


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def seconds_since_mtime(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        return round(time.time() - path.stat().st_mtime, 1)
    except Exception:
        return None


def process_rows() -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,etime=,stat=,command="],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 5:
            continue
        pid, ppid, etime, stat, command = parts
        try:
            pid_int = int(pid)
            ppid_int = int(ppid)
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid_int,
                "ppid": ppid_int,
                "etime": etime,
                "stat": stat,
                "command": command,
            }
        )
    return rows


def active_tau2_processes(run_name: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in process_rows():
        command = str(row.get("command") or "")
        if run_name not in command:
            continue
        if not is_tau2_run_command(command.split()):
            continue
        matches.append({key: row[key] for key in ("pid", "ppid", "etime", "stat")})
    return matches


def is_tau2_run_command(args: list[str]) -> bool:
    if not args:
        return False
    names = [Path(arg).name for arg in args]
    for index, name in enumerate(names):
        if index > 2:
            break
        if name == "tau2" and index + 1 < len(args) and args[index + 1] == "run":
            return True
        if name in {"python", "python3", "python3.12"} and index + 2 < len(args):
            if args[index + 1] == "-m" and args[index + 2] in {"tau2", "tau2.cli"} and "run" in args[index + 3:index + 5]:
                return True
    return False


def active_supervisor_processes() -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in process_rows():
        command = str(row.get("command") or "")
        if "scripts/supervise_formal_tau2.py" not in command and "/supervise_formal_tau2.py" not in command:
            continue
        if "py_compile" in command or "-m py_compile" in command:
            continue
        matches.append({key: row[key] for key in ("pid", "ppid", "etime", "stat")})
    return matches


def generic_run_status(
    name: str,
    run_dir: Path,
    expected_rows: int | None,
    official_summary_name: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    csv_path = run_dir / "openviking_generic_qa_results.csv"
    summary_path = run_dir / "summary.json"
    rows = read_csv_rows(csv_path)
    previous = previous or {}
    previous_rows = int(previous.get("rows") or 0)
    previous_status = str(previous.get("status") or "")
    transient_partial_read = bool(
        rows
        and previous_rows > len(rows)
        and previous_status in {"running", "completed"}
        and not summary_path.exists()
    )
    if transient_partial_read:
        # The formal runs append CSV rows while the UI/status refresher reads them.
        # A read can land mid-write and produce a short snapshot; never let the
        # published status move backwards because of that transient.
        rows_count_for_status = previous_rows
    else:
        rows_count_for_status = len(rows)
    failed = strict_failed_rows(rows)
    summary = read_json(summary_path, {}) or {}
    official_path = run_dir / official_summary_name
    official_summary = read_json(official_path, {}) or {}
    hotpot_summary = read_json(run_dir / "hotpotqa_answer_summary.json", {}) or {}
    complete_by_rows = expected_rows is not None and rows_count_for_status >= expected_rows
    summary_exists = summary_path.exists()
    official_exists = official_path.exists()
    if name == "hotpotqa":
        metric_exists = (run_dir / "hotpotqa_answer_summary.json").exists()
        completed = complete_by_rows and summary_exists and metric_exists and not failed
    elif name == "longmemeval":
        completed = complete_by_rows and summary_exists and official_exists and not failed
    else:
        completed = complete_by_rows and summary_exists and not failed
    if completed:
        status = "completed"
    elif rows_count_for_status:
        status = "running"
    elif csv_path.exists():
        status = "prepared_not_started"
    else:
        status = "not_started"
    latest_row = rows[-1] if rows else {}
    result: dict[str, Any] = {
        "status": status,
        "manager": "launchd" if (run_dir / "launchd.log").exists() else "",
        "run_dir": str(run_dir),
        "csv": str(csv_path),
        "exists": csv_path.exists(),
        "expected_rows": expected_rows,
        "rows": rows_count_for_status,
        "progress_pct": round((rows_count_for_status / expected_rows) * 100, 2) if expected_rows else None,
        "failed_rows": len(failed),
        "failed_question_ids": [row.get("question_id") or row.get("sample_id") or "" for row in failed[:20]],
        "health_counts": counter(rows, "health_status"),
        "model_counts": counter(rows, "model_status"),
        "answer_counts": counter(rows, "answer_status"),
        "last_question_id": latest_row.get("question_id") or "",
        "last_response": (latest_row.get("response") or "")[:160],
        "csv_mtime": file_info(csv_path).get("mtime"),
        "transient_partial_read": transient_partial_read,
        "summary_exists": summary_path.exists(),
        "summary": {
            key: summary.get(key)
            for key in (
                "status",
                "rows",
                "graded",
                "correct",
                "wrong",
                "accuracy",
                "official_metric",
                "official_score",
                "official_metric_scope",
                "answer_model",
                "judge_model",
                "model_ok_count",
                "model_failed_count",
                "answer_empty_or_unknown_count",
                "retrieval_error_rows",
            )
            if key in summary
        },
        "artifacts": {
            "csv": file_info(csv_path),
            "summary": file_info(summary_path),
            "official_summary": file_info(official_path),
            "hotpotqa_answer_summary": file_info(run_dir / "hotpotqa_answer_summary.json"),
            "launchd_log": file_info(run_dir / "launchd.log"),
        },
    }
    if official_summary:
        result["official_summary"] = {
            key: official_summary.get(key)
            for key in (
                "status",
                "graded",
                "correct",
                "wrong",
                "overall_accuracy",
                "task_averaged_accuracy",
                "abstention_accuracy",
                "judge_model",
                "judge_error_count",
            )
            if key in official_summary
        }
    if hotpot_summary:
        result["hotpotqa_answer_summary"] = {
            key: hotpot_summary.get(key)
            for key in ("status", "count", "answer_em", "answer_f1", "exact_match", "f1")
            if key in hotpot_summary
        }
    return result


def reward_value(sim: dict[str, Any]) -> float | None:
    reward_info = sim.get("reward_info")
    if isinstance(reward_info, dict) and reward_info.get("reward") is not None:
        try:
            return float(reward_info["reward"])
        except (TypeError, ValueError):
            return None
    if sim.get("reward") is not None:
        try:
            return float(sim["reward"])
        except (TypeError, ValueError):
            return None
    return None


def is_infra_error(sim: dict[str, Any]) -> bool:
    reason = str(sim.get("termination_reason") or "").lower()
    return "infrastructure" in reason


def tau2_result_metrics(result: dict[str, Any], expected: int | None = None) -> dict[str, Any]:
    sims = result.get("simulations") if isinstance(result, dict) else None
    tasks = result.get("tasks") if isinstance(result, dict) else None
    info = result.get("info") if isinstance(result, dict) else {}
    if not isinstance(sims, list):
        sims = []
    if not isinstance(tasks, list):
        tasks = []
    evaluated = [sim for sim in sims if isinstance(sim, dict) and not is_infra_error(sim)]
    rewards = [value for value in (reward_value(sim) for sim in evaluated) if value is not None]
    success_count = sum(1 for value in rewards if math.isclose(value, 1.0, rel_tol=0, abs_tol=1e-6))
    unique_tasks = sorted({str(sim.get("task_id")) for sim in evaluated if sim.get("task_id") is not None})
    expected_sims = expected
    num_trials = None
    if isinstance(info, dict):
        num_trials = info.get("num_trials")
    try:
        num_trials = int(num_trials)
    except (TypeError, ValueError):
        num_trials = 1
    if expected_sims is None and tasks:
        expected_sims = len(tasks) * max(1, num_trials)
    avg_reward = (sum(rewards) / len(rewards)) if rewards else None
    pass1 = (success_count / len(rewards)) if rewards else None
    return {
        "simulation_count": len(sims),
        "evaluated_simulations": len(evaluated),
        "expected_simulations": expected_sims,
        "progress_pct": round((len(evaluated) / expected_sims) * 100, 2) if expected_sims else None,
        "total_tasks": len(tasks) or None,
        "completed_tasks": len(unique_tasks),
        "avg_reward": avg_reward,
        "pass_hat_1": pass1,
        "success_count": success_count,
        "infra_error_count": len(sims) - len(evaluated),
        "last_task_id": str(evaluated[-1].get("task_id") or "") if evaluated else "",
        "last_reward": reward_value(evaluated[-1]) if evaluated else None,
        "termination_counts": dict(Counter(str(sim.get("termination_reason") or "") for sim in sims if isinstance(sim, dict))),
    }


def parse_tau2_run_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status_line": "", "active_running": []}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return {"status_line": "", "active_running": []}
    status_line = ""
    for line in reversed(lines):
        if line.startswith("Status: "):
            status_line = line
            break
    active_running: list[dict[str, Any]] = []
    if status_line:
        match = re.search(r"\b(?P<count>\d+)\s+running:\s+(?P<items>.+)$", status_line)
        if match:
            for raw in re.findall(r"(?P<task>[A-Za-z0-9_.-]+)\((?P<seconds>\d+)s(?:\s+(?P<retry>R\d+))?\)", match.group("items")):
                task_id, seconds, retry = raw
                active_running.append(
                    {
                        "task_id": task_id,
                        "elapsed_seconds": int(seconds),
                        "retry": retry,
                    }
                )
    return {
        "status_line": status_line,
        "active_running": active_running,
    }


def tau2_stall_warning(metrics: dict[str, Any], log_progress: dict[str, Any], result_path: Path, timeout_seconds: int = 900) -> dict[str, Any]:
    active = log_progress.get("active_running") or []
    max_elapsed = max((int(item.get("elapsed_seconds") or 0) for item in active), default=0)
    result_age = seconds_since_mtime(result_path)
    stalled = bool(max_elapsed > timeout_seconds and (result_age is None or result_age > timeout_seconds))
    return {
        "status": "timeout_exceeded" if stalled else "ok",
        "timeout_seconds": timeout_seconds,
        "max_active_elapsed_seconds": max_elapsed,
        "result_seconds_since_mtime": result_age,
        "message": (
            f"Active tau2 simulation has exceeded {timeout_seconds}s and results.json has not advanced for {result_age}s; supervisor may need the stale tau2 run to exit before auto-resume."
            if stalled
            else ""
        ),
    }


def tau2_run_status(domain: str, run_name: str, expected: int | None) -> dict[str, Any]:
    tau_root = EXTERNAL_DIR / "tau2-bench"
    run_dir = tau_root / "data" / "simulations" / run_name
    result_path = run_dir / "results.json"
    run_log = run_dir / "run.log"
    result = read_json(result_path, {}) or {}
    metrics = tau2_result_metrics(result, expected)
    active_processes = active_tau2_processes(run_name)
    log_progress = parse_tau2_run_log(run_log)
    stall_warning = tau2_stall_warning(metrics, log_progress, result_path)
    complete = bool(expected and metrics["evaluated_simulations"] >= expected and metrics["infra_error_count"] == 0)
    if complete:
        status = "completed"
    elif active_processes:
        status = "running"
    elif result_path.exists() and metrics["evaluated_simulations"]:
        status = "running_or_partial"
    elif run_dir.exists():
        status = "prepared_or_running"
    else:
        status = "not_started"
    return {
        "domain": domain,
        "status": status,
        "run_name": run_name,
        "run_dir": str(run_dir),
        "result_path": str(result_path),
        "active_processes": active_processes,
        "active_pids": [item["pid"] for item in active_processes],
        "artifacts": {
            "results": file_info(result_path),
            "run_log": file_info(run_log),
        },
        "log_progress": log_progress,
        "stall_warning": stall_warning,
        **metrics,
    }


def tau2_status() -> dict[str, Any]:
    tau_root = EXTERNAL_DIR / "tau2-bench"
    domains_dir = tau_root / "data" / "tau2" / "domains"
    domains: dict[str, Any] = {}
    for domain in ("airline", "retail", "telecom", "banking_knowledge"):
        task_path = domains_dir / domain / "tasks.json"
        split_path = domains_dir / domain / "split_tasks.json"
        splits = read_json(split_path, {}) or {}
        domains[domain] = {
            "tasks_path": str(task_path),
            "tasks": count_json_items(task_path),
            "splits": {key: len(value) if isinstance(value, list) else value for key, value in splits.items()},
        }
    simulation_dir = tau_root / "data" / "simulations"
    formal_runs = []
    if simulation_dir.exists():
        for result_path in sorted(simulation_dir.glob("formal_tau2_*/results.json")):
            result = read_json(result_path, {}) or {}
            sims = result.get("simulations")
            formal_runs.append(
                {
                    "path": str(result_path),
                    "run_dir": str(result_path.parent),
                    "simulation_count": len(sims) if isinstance(sims, list) else None,
                    "mtime": datetime.fromtimestamp(result_path.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
    formal_by_domain = {
        domain: tau2_run_status(
            domain,
            run_name,
            domains.get(domain, {}).get("splits", {}).get("base") or domains.get(domain, {}).get("tasks"),
        )
        for domain, run_name in TAU2_FORMAL_RUNS.items()
    }
    expected_total = sum(int(item.get("expected_simulations") or 0) for item in formal_by_domain.values())
    simulation_total = sum(int(item.get("simulation_count") or 0) for item in formal_by_domain.values())
    evaluated_total = sum(int(item.get("evaluated_simulations") or 0) for item in formal_by_domain.values())
    infra_error_total = sum(int(item.get("infra_error_count") or 0) for item in formal_by_domain.values())
    weighted_reward_num = sum(
        float(item["avg_reward"]) * int(item.get("evaluated_simulations") or 0)
        for item in formal_by_domain.values()
        if item.get("avg_reward") is not None
    )
    success_total = sum(int(item.get("success_count") or 0) for item in formal_by_domain.values())
    supervisor_processes = active_supervisor_processes()
    return {
        "status": "supervised_running" if supervisor_processes else ("official_runner_available_not_started" if not formal_runs else "official_runner_outputs_present"),
        "root": str(tau_root),
        "runner": "tau2 run",
        "supervisor": {
            "script": str(ROOT / "scripts" / "supervise_formal_tau2.py"),
            "active_processes": supervisor_processes,
            "active_pids": [item["pid"] for item in supervisor_processes],
            "log": file_info(TAU2_SUPERVISOR_LOG),
            "stdout_log": file_info(TAU2_SUPERVISOR_STDOUT),
        },
        "official_metric": "Pass^1 / avg_reward from official tau2 metrics",
        "current_scope": "4 official domains, 1 trial each, text-mode official runner; banking_knowledge uses bm25 retrieval",
        "leaderboard_scope": "official tau2 Overall appears when airline, retail, telecom and banking_knowledge all have Pass^1; submission guidance recommends 4+ trials for stable Pass^k",
        "base_domains": TAU2_CURRENT_DOMAINS,
        "leaderboard_domains": TAU2_LEADERBOARD_DOMAINS,
        "domains": domains,
        "formal_runs": formal_runs,
        "formal_by_domain": formal_by_domain,
        "formal_total": {
            "expected_simulations": expected_total,
            "simulation_count": simulation_total,
            "evaluated_simulations": evaluated_total,
            "infra_error_count": infra_error_total,
            "progress_pct": round((evaluated_total / expected_total) * 100, 2) if expected_total else None,
            "avg_reward": (weighted_reward_num / evaluated_total) if evaluated_total else None,
            "pass_hat_1": (success_total / evaluated_total) if evaluated_total else None,
            "success_count": success_total,
        },
        "recommended_full_commands": [
            "uv run tau2 run --domain airline --task-split-name base --agent-llm openai/gpt-5.5 --user-llm openai/gpt-5.5 --num-trials 1 --save-to formal_tau2_airline_openviking_20260606 --auto-resume",
            "uv run tau2 run --domain retail --task-split-name base --agent-llm openai/gpt-5.5 --user-llm openai/gpt-5.5 --num-trials 1 --save-to formal_tau2_retail_openviking_20260606 --auto-resume",
            "uv run tau2 run --domain telecom --task-split-name base --agent-llm openai/gpt-5.5 --user-llm openai/gpt-5.5 --num-trials 1 --save-to formal_tau2_telecom_openviking_20260606 --auto-resume",
            "uv run tau2 run --domain banking_knowledge --retrieval-config bm25 --agent-llm openai/gpt-5.5 --user-llm openai/gpt-5.5 --num-trials 1 --save-to formal_tau2_banking_knowledge_bm25_openviking_20260606 --auto-resume",
        ],
        "leaderboard_full_command_template": [
            "uv run tau2 run --domain airline --task-split-name base --num-trials 4 --save-to <airline_4trials> --auto-resume",
            "uv run tau2 run --domain retail --task-split-name base --num-trials 4 --save-to <retail_4trials> --auto-resume",
            "uv run tau2 run --domain telecom --task-split-name base --num-trials 4 --save-to <telecom_4trials> --auto-resume",
            "uv run tau2 run --domain banking_knowledge --retrieval-config bm25 --num-trials 4 --save-to <banking_4trials> --auto-resume",
            "uv run tau2 submit prepare data/simulations/<airline_4trials> data/simulations/<retail_4trials> data/simulations/<telecom_4trials> data/simulations/<banking_4trials> --output <submission_dir>",
        ],
        "notes": [
            "Text-mode official runner is local; it uses LiteLLM and saves results under external/tau2-bench/data/simulations.",
            "The OpenViking generic QA page is not a tau2 official score.",
            "Current supervised run is a 1-trial 4-domain progress run, not the full 4-trial leaderboard submission.",
            "Run all four domains to completion, then run 4+ trials before claiming a stable official tau2 leaderboard-comparable overall score.",
        ],
    }


def load_sota_registry() -> dict[str, Any]:
    data = read_json(DATASET_DIR / "sota_registry.json", {}) or {}
    entries = {}
    for item in data.get("benchmarks", []):
        key = item.get("dataset_format")
        if key:
            entries[key] = item
    return entries


def build_status(previous_status_path: Path | None = None) -> dict[str, Any]:
    previous_status = read_json(previous_status_path, {}) if previous_status_path else {}
    previous_runs = previous_status.get("runs") if isinstance(previous_status, dict) else {}
    if not isinstance(previous_runs, dict):
        previous_runs = {}
    longmem_expected = count_json_items(DATASET_DIR / "full" / "longmemeval_s_cleaned.json")
    hotpot_expected = count_json_items(DATASET_DIR / "full" / "hotpotqa_dev_distractor.json")
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "objective": "full real-LLM benchmark scores for additional datasets plus SOTA comparison",
        "full_datasets_available_locally": {
            "longmemeval": {
                "path": str(DATASET_DIR / "full" / "longmemeval_s_cleaned.json"),
                "rows": longmem_expected,
                "official_style_metric": "overall_accuracy",
            },
            "hotpotqa": {
                "path": str(DATASET_DIR / "full" / "hotpotqa_dev_distractor.json"),
                "rows": hotpot_expected,
                "local_metric": "answer_f1",
                "official_leaderboard_metric": "joint_f1",
            },
        },
        "runs": {
            "longmemeval": generic_run_status(
                "longmemeval",
                LONGMEM_RUN,
                longmem_expected,
                "longmemeval_official_summary.json",
                previous_runs.get("longmemeval") if isinstance(previous_runs.get("longmemeval"), dict) else None,
            ),
            "hotpotqa": generic_run_status(
                "hotpotqa",
                HOTPOT_RUN,
                hotpot_expected,
                "hotpotqa_answer_summary.json",
                previous_runs.get("hotpotqa") if isinstance(previous_runs.get("hotpotqa"), dict) else None,
            ),
        },
        "tau2bench": tau2_status(),
        "unsupported_full_scores": {
            "evolvingevents": "only bundled sample present; focused local/web audit did not identify an authoritative official full dataset or scorer",
            "proagentbench": "only bundled sample present locally; public HF full asset exists but is large imagefolder/SQLite/screenshot data and the official multimodal runner/metrics are not wired",
        },
        "sota_registry": str(DATASET_DIR / "sota_registry.json"),
        "notes": [
            "Do not report sample/probe scores as complete benchmark scores.",
            "LongMemEval full run uses real OpenViking retrieval and real LLM answer model; official-style LLM evaluator has completed.",
            "HotpotQA full local score will be answer-only EM/F1, not official joint F1 until supporting-fact predictions are implemented.",
            "tau2-bench must use the official tau2 runner for Pass^1/reward; generic OpenViking QA is not comparable.",
            "The current tau2 run is official-runner progress, but not yet a full leaderboard submission because banking_knowledge and 4-trial Pass^k are not complete.",
            "ProAgentBench full data is not the local 2-row JSON smoke sample; the public HF asset is imagefolder/SQLite/screenshot-based and requires a separate multimodal/proactive-agent runner.",
            "EvolvingEvents remains blocked because no authoritative official full dataset/scorer has been identified in the local tree or focused web audit.",
        ],
    }


def pct(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return str(value)


def tau2_active_note(item: dict[str, Any]) -> str:
    if not item.get("active_pids"):
        return ""
    progress = item.get("log_progress") or {}
    active = progress.get("active_running") or []
    if not active:
        return ""
    parts = []
    for entry in active:
        seconds = entry.get("elapsed_seconds")
        elapsed = f"{int(seconds)}s" if isinstance(seconds, (int, float)) else "-"
        retry = entry.get("retry") or ""
        suffix = f" {retry}" if retry else ""
        parts.append(f"{entry.get('task_id', '-')}: {elapsed}{suffix}")
    note = "running " + ", ".join(parts)
    stall = item.get("stall_warning") or {}
    if stall.get("status") == "timeout_exceeded":
        note += f"; WARNING timeout_exceeded result_age={stall.get('result_seconds_since_mtime', '-')}s"
    return note


def tau2_is_base_complete(tau: dict[str, Any]) -> bool:
    total = tau.get("formal_total") or {}
    expected = int(total.get("expected_simulations") or 0)
    evaluated = int(total.get("evaluated_simulations") or 0)
    infra_errors = int(total.get("infra_error_count") or 0)
    domains = tau.get("formal_by_domain") or {}
    base_domains = tau.get("base_domains") or []
    return bool(
        expected
        and evaluated >= expected
        and infra_errors == 0
        and base_domains
        and all((domains.get(domain) or {}).get("status") == "completed" for domain in base_domains)
    )


def render_report(status: dict[str, Any]) -> str:
    sota = load_sota_registry()
    lines = [
        "# Formal benchmark status",
        "",
        f"- Updated: `{status['updated_at']}`",
        f"- Objective: {status['objective']}",
        "",
        "## Current real-LLM runs",
        "",
        "| Dataset | Status | Rows | Strict failures | Official/local metric | Score | Reference / SOTA | Comparable? |",
        "|---|---:|---:|---:|---|---:|---|---|",
    ]
    longmem = status["runs"]["longmemeval"]
    longmem_off = longmem.get("official_summary") or {}
    longmem_sota = (sota.get("longmemeval") or {}).get("sota") or {}
    lines.append(
        "| LongMemEval-S | "
        f"{longmem['status']} | {longmem['rows']}/{longmem['expected_rows']} | {longmem['failed_rows']} | "
        f"overall_accuracy | {pct(longmem_off.get('overall_accuracy') or longmem.get('summary', {}).get('official_score'))} | "
        f"paper/reference baseline: {longmem_sota.get('model', '-')} {pct(longmem_sota.get('score'))} | cautious; official-style judge, not a same-runner memory-backend leaderboard |"
    )
    hotpot = status["runs"]["hotpotqa"]
    hotpot_metric = hotpot.get("hotpotqa_answer_summary") or {}
    hotpot_sota = (sota.get("hotpotqa") or {}).get("sota") or {}
    hotpot_score = hotpot_metric.get("answer_f1") or hotpot_metric.get("f1")
    lines.append(
        "| HotpotQA distractor dev | "
        f"{hotpot['status']} | {hotpot['rows']}/{hotpot['expected_rows']} | {hotpot['failed_rows']} | "
        f"answer_f1 | {pct(hotpot_score)} | {hotpot_sota.get('model', '-')} {pct(hotpot_sota.get('score'))} joint_f1 | no, answer-only until support facts |"
    )
    tau = status["tau2bench"]
    tau_sota = (sota.get("tau2bench") or {}).get("sota") or {}
    tau_total = tau.get("formal_total") or {}
    base_counts = ", ".join(
        f"{domain}:{tau['domains'][domain]['splits'].get('base', tau['domains'][domain]['tasks'])}"
        for domain in tau["base_domains"]
    )
    tau_score = tau_total.get("pass_hat_1") if tau_total.get("pass_hat_1") is not None else tau_total.get("avg_reward")
    tau_rows = f"{tau_total.get('evaluated_simulations', 0)}/{tau_total.get('expected_simulations', '-')}"
    tau_score_text = pct(tau_score) if tau2_is_base_complete(tau) else f"intermediate {pct(tau_score)}"
    tau_comparable = (
        "1-trial four-domain complete; still not full leaderboard without 4+ trials"
        if tau2_is_base_complete(tau)
        else "not final; official runner progress only"
    )
    leaderboard_scope = tau.get("leaderboard_scope") or "official leaderboard scope not recorded"
    lines.append(
        "| tau2-bench | "
        f"{tau['status']} | {tau_rows} evaluated base ({base_counts}) | - | Pass^1/reward | {pct(tau_score)} | {tau_sota.get('source', 'live leaderboard')} | official runner; comparable only after four domains and 4+ trials |"
    )
    lines[-1] = (
        "| tau2-bench | "
        f"{tau['status']} | {tau_rows} evaluated base ({base_counts}) | - | Pass^1/reward | {tau_score_text} | "
        f"{tau_sota.get('source', 'live leaderboard')} | {tau_comparable}; generic QA is not valid for tau2 |"
    )
    lines.append("")
    lines.append(f"> tau2 scope note: current run is `{tau.get('current_scope', '-')}`. Full leaderboard comparison still requires: {leaderboard_scope}.")
    tau_domains = tau.get("formal_by_domain") or {}
    if tau_domains:
        lines.extend(["", "## tau2-bench official runner detail", ""])
        lines.append("| Domain | Status | Sims | Evaluated | Infra errors | Pass^1 | Avg reward | Last task | Results |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|---|")
        for domain in tau["base_domains"]:
            item = tau_domains.get(domain) or {}
            sims_text = f"{item.get('simulation_count', 0)}/{item.get('expected_simulations', '-')}"
            evaluated_text = f"{item.get('evaluated_simulations', 0)}/{item.get('expected_simulations', '-')}"
            lines.append(
                f"| {domain} | {item.get('status', '-')} | {sims_text} | {evaluated_text} | {item.get('infra_error_count', 0)} | "
                f"{pct(item.get('pass_hat_1'))} | {pct(item.get('avg_reward'))} | "
                f"{item.get('last_task_id') or '-'}{(' · ' + tau2_active_note(item)) if tau2_active_note(item) else ''} | `{item.get('result_path', '-')}` |"
            )
    lines.extend(
        [
            "",
            "## Not formal yet",
            "",
            f"- EvolvingEvents: {status['unsupported_full_scores']['evolvingevents']}.",
            f"- proAgentBench: {status['unsupported_full_scores']['proagentbench']}; official metrics are proactive timing/intention metrics, not QA accuracy.",
            "",
            "## Evidence",
            "",
            f"- LongMemEval run dir: `{longmem['run_dir']}`",
            f"- HotpotQA run dir: `{hotpot['run_dir']}`",
            f"- tau2 runner root: `{tau['root']}`",
            "",
            "## Notes",
            "",
        ]
    )
    lines.extend(f"- {note}" for note in status["notes"])
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-out", default=str(RUNS_DIR / "formal_benchmark_status.json"))
    parser.add_argument("--report-out", default=str(RUNS_DIR / "formal_benchmark_report.md"))
    args = parser.parse_args()
    status_path = Path(args.status_out)
    status = build_status(status_path)
    report_path = Path(args.report_out)
    write_json(status_path, status)
    report_path.write_text(render_report(status), encoding="utf-8")
    print(json.dumps({"status": str(status_path), "report": str(report_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
