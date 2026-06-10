from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import reports as report_service


RUN_LIST_CSV_SUMMARY_MAX_BYTES = 5 * 1024 * 1024
NATIVE_OPENVIKING_BASELINE_FILE = "native_openviking_baseline.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def agent_type_for(kind: str, payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    explicit = str(payload.get("agent_type") or "")
    if explicit:
        return explicit
    output_hint = str(payload.get("output_file") or payload.get("output") or "").lower()
    if "local_agent_results.csv" in output_hint or "/local_agent/" in output_hint:
        return "local_reference_agent"
    if kind == "local_agent":
        return "local_reference_agent"
    if kind == "openviking_qa":
        return "memorybench_agent"
    if kind == "echomemory_qa":
        return "echomemory_memory_qa"
    if kind == "openviking_generic_qa":
        return "openviking_generic_qa"
    if kind in {"openviking_qa_retry_failed", "openviking_qa_retry_missing"}:
        return "memorybench_agent"
    if kind == "openviking_import":
        return "openviking_commit_import"
    if kind == "echomemory_import":
        return "echomemory_commit_import"
    if kind == "judge":
        return "judge"
    return kind or "unknown"


def row_grade(row: dict[str, str]) -> str:
    return report_service.row_grade(row)


def _normalize_run_status(manifest: dict[str, Any], run_dir: Path, active_run_ids: set[str] | None = None) -> tuple[str, dict[str, Any]]:
    raw_status = str(manifest.get("status") or "").strip()
    run_id = str(manifest.get("id") or run_dir.name)
    if raw_status == "running" and active_run_ids is not None and run_id not in active_run_ids and str(run_dir) not in active_run_ids:
        return (
            "interrupted",
            {
                "manifest_status": raw_status,
                "stale_running": True,
                "recoverable": True,
                "status_reason": "manifest_running_without_active_task",
                "recovery_hint": "任务已不在当前服务活动列表中；可查看日志后补跑缺失题或重跑失败题。",
            },
        )
    return raw_status, {"manifest_status": raw_status, "stale_running": False, "recoverable": False}


def _external_status_from_formal_benchmark(run_dir: Path) -> tuple[str, dict[str, Any]]:
    status_path = run_dir.parent / "formal_benchmark_status.json"
    if not status_path.exists():
        return "", {}
    try:
        data = read_json(status_path)
    except Exception:
        return "", {}
    runs = data.get("runs") if isinstance(data, dict) else {}
    if not isinstance(runs, dict):
        return "", {}
    run_dir_text = str(run_dir)
    for key, record in runs.items():
        if not isinstance(record, dict) or str(record.get("run_dir") or "") != run_dir_text:
            continue
        status = str(record.get("status") or "").strip()
        if not status:
            return "", {}
        rows = int(record.get("rows") or 0)
        failed_rows = int(record.get("failed_rows") or 0)
        meta = {
            "external_status_source": str(status_path),
            "external_status_key": key,
            "external_rows": rows,
            "external_failed_rows": failed_rows,
            "external_completion_gate": record.get("completion_gate") or record.get("start_gate") or "",
        }
        if status.startswith("queued"):
            return "queued", meta
        if status in {"running", "waiting", "prepared_not_started", "queued_after_longmemeval_health_gate"}:
            return "running" if status == "running" else status, meta
        return status, meta
    return "", {}


def _csv_output_rank(path: Path) -> tuple[int, float]:
    name = path.name.lower()
    text = str(path).lower()
    derived_markers = [
        ".pending_judge",
        "pending_judge",
        "wrong_questions",
        "wrong_analysis",
        "wrong_memory",
        "analysis_report",
        "summary",
        "brief",
    ]
    canonical_names = {
        "openviking_memory_qa_results.csv",
        "echomemory_memory_qa_results.csv",
        "vikingbot_results.csv",
        "local_agent_results.csv",
        "distributed_results.csv",
        "chenmo_results.csv",
        "openviking_generic_qa_results.csv",
    }
    if name == "judge_snapshot_full81.csv":
        score = 120
    elif name.startswith("judge_snapshot_") and name.endswith(".csv"):
        score = 110
    elif name in canonical_names:
        score = 100
    elif name.endswith("_results.csv") or "results.csv" in name:
        score = 80
    else:
        score = 20
    if any(marker in text for marker in derived_markers):
        score -= 100
    return score, path.stat().st_mtime


def run_record(run_dir: Path, active_run_ids: set[str] | None = None) -> dict[str, Any] | None:
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = read_json(manifest_path)
        except Exception:
            manifest = {}
    else:
        manifest = {}

    output_file = manifest.get("output_file") or ""
    if not output_file:
        csvs = sorted(
            [p for p in run_dir.rglob("*.csv") if p.name != "input.csv"],
            key=_csv_output_rank,
            reverse=True,
        )
        output_file = str(csvs[0]) if csvs else ""

    summary = manifest.get("summary") or {}
    output_path = Path(output_file) if output_file else None
    if output_path and output_path.exists() and output_path.suffix.lower() == ".json":
        json_summary = report_service.parse_json_run_summary(output_path)
        if json_summary:
            summary = json_summary
    if output_path and output_path.exists() and output_path.suffix.lower() == ".csv":
        try:
            csv_size = output_path.stat().st_size
        except OSError:
            csv_size = 0
        if csv_size <= RUN_LIST_CSV_SUMMARY_MAX_BYTES:
            csv_summary = report_service.parse_csv_summary(output_path)
            summary = {**summary, **csv_summary} if summary else csv_summary
        else:
            summary = {
                **summary,
                "summary_skipped": True,
                "summary_skip_reason": f"CSV is {round(csv_size / 1024 / 1024, 1)} MB; open run detail/report for full metrics.",
            }

    try:
        stat = run_dir.stat()
    except OSError:
        return None

    created_at = manifest.get("created_at")
    if not created_at:
        match = re.search(r"_(\d{8})_(\d{6})_", run_dir.name)
        if match:
            created_at = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").isoformat(timespec="seconds")
        else:
            created_at = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")

    status_value, status_meta = _normalize_run_status(manifest, run_dir, active_run_ids)
    external_status, external_meta = _external_status_from_formal_benchmark(run_dir)
    if external_status:
        status_value = external_status
        status_meta = {**status_meta, **external_meta}

    duration_s = manifest.get("duration_s")
    if status_value == "running":
        started_at = manifest.get("started_at") or created_at
        try:
            started_dt = datetime.fromisoformat(str(started_at))
            duration_s = round(max(0.0, (datetime.now() - started_dt).total_seconds()), 1)
        except Exception:
            pass
    elif status_meta.get("stale_running"):
        end_candidates = [run_dir]
        for key in ["output_file", "log_file"]:
            value = manifest.get(key)
            if value:
                end_candidates.append(Path(str(value)))
        mtimes = []
        for candidate in end_candidates:
            try:
                if candidate.exists():
                    mtimes.append(candidate.stat().st_mtime)
            except OSError:
                continue
        started_at = manifest.get("started_at") or created_at
        try:
            started_dt = datetime.fromisoformat(str(started_at))
            end_ts = max(mtimes) if mtimes else run_dir.stat().st_mtime
            duration_s = round(max(0.0, end_ts - started_dt.timestamp()), 1)
        except Exception:
            pass

    config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    command = manifest.get("command") if isinstance(manifest.get("command"), list) else []
    account = str(config.get("account") or _command_option(command, "account") or "").strip()
    workspace = str(config.get("workspace") or config.get("openviking_workspace") or "").strip()
    dataset_path = str(
        config.get("dataset")
        or config.get("data")
        or config.get("dataset_path")
        or _command_option(command, "dataset")
        or _command_option(command, "data")
        or ""
    ).strip()
    sample = str(config.get("sample") or _command_option(command, "sample") or "").strip()
    dataset_format = _dataset_format_from_run(manifest, config, command, summary, output_file, run_dir)

    return {
        "id": manifest.get("id") or run_dir.name,
        "name": manifest.get("name") or run_dir.name,
        "agent_type": manifest.get("agent_type")
        or agent_type_for(
            manifest.get("kind") or run_dir.name.split("_", 1)[0],
            {**(manifest.get("config") or {}), "output_file": output_file},
        ),
        "experiment_version": manifest.get("experiment_version") or "",
        "experiment_tags": manifest.get("experiment_tags") or "",
        "config_hash": manifest.get("config_hash") or "",
        "kind": manifest.get("kind") or run_dir.name.split("_", 1)[0],
        "dataset_format": dataset_format,
        "dataset_path": dataset_path,
        "sample": sample,
        "session_start": config.get("session_start") or _command_option(command, "session-start"),
        "session_end": config.get("session_end") or _command_option(command, "session-end"),
        "status": status_value or ("succeeded" if summary else "unknown"),
        **status_meta,
        "created_at": created_at,
        "duration_s": duration_s,
        "output_file": output_file,
        "log_file": manifest.get("log_file") or str(run_dir / "run.log"),
        "manifest_file": str(manifest_path) if manifest_path.exists() else "",
        "summary": summary,
        "run_dir": str(run_dir),
        "account": account,
        "workspace": workspace,
    }


def list_runs(
    output_dir: Path,
    limit: int = 40,
    query: str = "",
    status: str = "all",
    active_run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not output_dir.exists():
        return []
    dirs = [p for p in output_dir.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    records = []
    query_l = query.strip().lower()
    for path in dirs:
        if query_l and query_l not in path.name.lower():
            manifest_path = path / "manifest.json"
            manifest_text = ""
            if manifest_path.exists():
                try:
                    manifest = read_json(manifest_path)
                    manifest_text = " ".join(
                        str(manifest.get(key, ""))
                        for key in ["id", "name", "kind", "status", "output_file", "run_dir"]
                    ).lower()
                except Exception:
                    manifest_text = ""
            if query_l not in manifest_text:
                continue
        record = run_record(path, active_run_ids)
        if not record:
            continue
        haystack = " ".join(str(record.get(key, "")) for key in ["id", "name", "kind", "status", "output_file", "run_dir"]).lower()
        if query_l and query_l not in haystack:
            continue
        if status not in ("", "all") and record.get("status") != status:
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return records


def _command_has(command: Any, option: str) -> bool:
    return isinstance(command, list) and f"--{option}" in [str(item) for item in command]


def _command_option(command: Any, option: str) -> str:
    if not isinstance(command, list):
        return ""
    values = [str(item) for item in command]
    flag = f"--{option}"
    if flag not in values:
        return ""
    index = values.index(flag)
    if index + 1 >= len(values) or values[index + 1].startswith("--"):
        return ""
    return values[index + 1]


def _dataset_format_from_run(
    manifest: dict[str, Any],
    config: dict[str, Any],
    command: list[Any],
    summary: dict[str, Any],
    output_file: str,
    run_dir: Path,
) -> str:
    summary_json = summary.get("summary_json") if isinstance(summary.get("summary_json"), dict) else {}
    explicit = (
        manifest.get("dataset_format")
        or config.get("dataset_format")
        or summary.get("dataset_format")
        or (summary_json or {}).get("dataset_format")
        or _command_option(command, "format")
    )
    if explicit:
        return str(explicit).strip().lower()
    haystack = f"{manifest.get('name') or ''} {manifest.get('id') or ''} {manifest.get('kind') or ''} {output_file} {run_dir}".lower()
    if "longmem" in haystack:
        return "longmemeval"
    if "hotpot" in haystack:
        return "hotpotqa"
    if "proagent" in haystack:
        return "proagentbench"
    if "tau2" in haystack or "tau-bench" in haystack:
        return "tau2bench"
    if "evolvingevents" in haystack or "evolving_events" in haystack:
        return "evolvingevents"
    if "chenmo" in haystack or "陈默" in haystack:
        return "chenmo"
    if "locomo" in haystack or "openviking_qa" in haystack or "echomemory_qa" in haystack:
        return "locomo"
    return ""


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _summary_metric(summary: dict[str, Any], summary_json: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = summary.get(key)
        if value not in (None, ""):
            return value
        value = summary_json.get(key)
        if value not in (None, ""):
            return value
    return ""


def _native_top_k_label(value: Any, prompt_mode: Any) -> Any:
    if str(prompt_mode or "").strip() == "native_vikingbot_cli":
        return "native_vikingbot_internal"
    return value


def _compact_count_map(value: Any, limit: int = 3) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    items = sorted(value.items(), key=lambda item: (-int(item[1] or 0), str(item[0])))
    return " · ".join(f"{key}:{count}" for key, count in items[:limit])


def run_compare_row(run_dir: Path) -> dict[str, Any]:
    detail = run_detail(run_dir)
    if not detail:
        raise FileNotFoundError(str(run_dir))
    record = detail.get("record") or {}
    manifest = detail.get("manifest") or {}
    snapshot = {}
    snapshot_path = run_dir / "config_snapshot.json"
    if snapshot_path.exists():
        try:
            data = read_json(snapshot_path)
            if isinstance(data, dict):
                snapshot = data
        except Exception:
            snapshot = {}
    config: dict[str, Any] = {}
    if isinstance(snapshot.get("config"), dict):
        config.update(snapshot["config"])
    if isinstance(manifest.get("config"), dict):
        config.update(manifest["config"])
    command = manifest.get("command") or snapshot.get("command") or []
    config_sources = [
        "manifest" if manifest.get("config") or manifest.get("command") else "",
        "config snapshot" if snapshot.get("config") or snapshot.get("command") else "",
    ]
    summary = record.get("summary") or {}
    summary_json = summary.get("summary_json") or {}
    query_expansion = _summary_metric(summary, summary_json, "query_expansion_enabled")
    lexical_fallback = _summary_metric(summary, summary_json, "lexical_fallback_enabled")
    archive_fallback = _summary_metric(summary, summary_json, "archive_fallback_enabled")
    memory_file_read = _summary_metric(summary, summary_json, "memory_file_read_enabled")
    prompt_mode = _summary_metric(summary, summary_json, "prompt_mode")
    openviking_tool_loop = _summary_metric(summary, summary_json, "openviking_tool_loop_enabled")
    openviking_tool_set = _summary_metric(summary, summary_json, "openviking_tool_set")
    openviking_content_read = _summary_metric(summary, summary_json, "openviking_content_read_enabled")
    vikingboat_profile = _summary_metric(summary, summary_json, "vikingboat_alignment_profile")
    alignment_backend_route = _summary_metric(summary, summary_json, "alignment_backend_route", "backend_route")
    vikingbot_prompt_aligned = _summary_metric(summary, summary_json, "vikingbot_prompt_aligned")
    group_chat = _summary_metric(summary, summary_json, "group_chat")
    memory_user_strategy = _summary_metric(summary, summary_json, "memory_user_strategy")
    identity_mode = _summary_metric(summary, summary_json, "vikingbot_identity_mode")
    vikingbot_channel = _summary_metric(summary, summary_json, "vikingbot_channel")
    initial_agent_memory = _summary_metric(summary, summary_json, "initial_agent_memory_enabled")
    raw_turn_fallback = _summary_metric(summary, summary_json, "raw_turn_fallback")
    initial_search_limit = _summary_metric(summary, summary_json, "initial_search_limit")
    initial_score_threshold = _summary_metric(summary, summary_json, "initial_score_threshold")
    tool_search_limit = _summary_metric(summary, summary_json, "tool_search_limit")
    tool_min_score = _summary_metric(summary, summary_json, "tool_min_score")
    effective_prompt_mode = _first_value(config.get("prompt_mode"), _command_option(command, "prompt-mode"), prompt_mode)
    effective_top_k = _native_top_k_label(
        _first_value(config.get("top_k"), _command_option(command, "top-k"), config.get("chatTopK")),
        effective_prompt_mode,
    )
    return {
        "id": record.get("id") or run_dir.name,
        "name": record.get("name") or run_dir.name,
        "kind": record.get("kind") or "-",
        "status": record.get("status") or "-",
        "created_at": record.get("created_at") or "-",
        "duration_s": record.get("duration_s"),
        "agent_type": record.get("agent_type") or agent_type_for(str(record.get("kind") or ""), record),
        "rows": summary.get("rows") or summary_json.get("count"),
        "graded": summary.get("graded") or summary_json.get("graded"),
        "accuracy": summary.get("official_score") if summary.get("official_score") is not None else summary.get("accuracy"),
        "formal_accuracy": summary.get("accuracy"),
        "official_metric": summary.get("official_metric") or summary_json.get("official_metric"),
        "official_score": summary.get("official_score") if summary.get("official_score") is not None else summary_json.get("official_score"),
        "official_metric_scope": summary.get("official_metric_scope") or summary_json.get("official_metric_scope"),
        "dataset_format": record.get("dataset_format")
        or _first_value(config.get("dataset_format"), _command_option(command, "format"), summary.get("dataset_format"), summary_json.get("dataset_format")),
        "exact": summary.get("exact_match_reference") if summary.get("exact_match_reference") is not None else summary_json.get("exact_match_rate"),
        "correct": summary.get("correct") if summary.get("correct") is not None else summary_json.get("correct"),
        "wrong": summary.get("wrong") if summary.get("wrong") is not None else summary_json.get("wrong"),
        "pending": (summary.get("result_counts") or {}).get("UNSCORED"),
        "answer_model": _first_value(config.get("answer_model"), _command_option(command, "answer-model"), config.get("model"), config.get("judge_model"), _command_option(command, "model")),
        "judge_model": _first_value(config.get("judge_model"), _command_option(command, "judge-model"), _command_option(command, "model")),
        "embedding_model": _first_value(config.get("embedding_model"), config.get("embed_model"), config.get("vlm_model")),
        "top_k": effective_top_k,
        "prompt_mode": effective_prompt_mode,
        "vikingboat_alignment_profile": vikingboat_profile,
        "alignment_backend_route": alignment_backend_route,
        "vikingbot_prompt_aligned": vikingbot_prompt_aligned,
        "group_chat": _first_value(config.get("group_chat"), _command_option(command, "group-chat"), group_chat),
        "memory_user_strategy": memory_user_strategy,
        "vikingbot_identity_mode": identity_mode,
        "vikingbot_channel": vikingbot_channel,
        "initial_agent_memory": initial_agent_memory,
        "raw_turn_fallback": raw_turn_fallback,
        "initial_search_limit": _first_value(config.get("initial_search_limit"), _command_option(command, "initial-search-limit"), initial_search_limit),
        "initial_score_threshold": _first_value(config.get("initial_score_threshold"), _command_option(command, "initial-score-threshold"), initial_score_threshold),
        "tool_search_limit": _first_value(config.get("tool_search_limit"), _command_option(command, "tool-search-limit"), tool_search_limit),
        "tool_min_score": _first_value(config.get("tool_min_score"), _command_option(command, "tool-min-score"), tool_min_score),
        "openviking_tool_set": _first_value(config.get("openviking_tool_set"), _command_option(command, "openviking-tool-set"), openviking_tool_set),
        "openviking_tool_loop": openviking_tool_loop if openviking_tool_loop != "" else (
            False if _command_has(command, "no-openviking-tool-loop") else True if _command_has(command, "openviking-tool-loop") else config.get("openviking_tool_loop")
        ),
        "openviking_content_read": openviking_content_read if openviking_content_read != "" else (
            False if _command_has(command, "no-read-openviking-content") else True if _command_has(command, "read-openviking-content") else config.get("read_openviking_content")
        ),
        "max_iterations": _first_value(config.get("max_iterations"), _command_option(command, "max-iterations"), _summary_metric(summary, summary_json, "max_iterations")),
        "account": _first_value(config.get("account"), _command_option(command, "account")),
        "sample": _first_value(config.get("sample"), _command_option(command, "sample")),
        "questions": _first_value(config.get("questions"), _command_option(command, "questions")),
        "retrieval_mode": _first_value(_summary_metric(summary, summary_json, "retrieval_mode"), "-"),
        "query_expansion": query_expansion if query_expansion != "" else (False if _command_has(command, "no-query-expansion") else ""),
        "lexical_fallback": lexical_fallback if lexical_fallback != "" else (False if _command_has(command, "no-lexical-fallback") else ""),
        "archive_fallback": archive_fallback if archive_fallback != "" else (False if _command_has(command, "no-archive-fallback") else ""),
        "memory_file_read": memory_file_read if memory_file_read != "" else (False if _command_has(command, "no-read-memory-files") else ""),
        "memory_hits": _summary_metric(summary, summary_json, "memory_hit_total"),
        "avg_memory_hits": _summary_metric(summary, summary_json, "avg_memory_hit_count", "avg_retrieval_count"),
        "avg_iteration": _summary_metric(summary, summary_json, "avg_iteration"),
        "tool_call_rows": _summary_metric(summary, summary_json, "tool_call_rows"),
        "tool_call_total": _summary_metric(summary, summary_json, "tool_call_total"),
        "tool_name_counts": _compact_count_map(summary.get("tool_name_counts") or summary_json.get("tool_name_counts")),
        "retrieval_tokens": _summary_metric(summary, summary_json, "retrieval_tokens_est_total", "retrieval_tokens_est"),
        "answer_tokens": _summary_metric(summary, summary_json, "answer_total_tokens"),
        "injection_tokens": _summary_metric(summary, summary_json, "total_injection_tokens_est"),
        "health": _compact_count_map(summary.get("health_counts") or summary_json.get("health_counts")),
        "config_source": " + ".join(item for item in config_sources if item) or "旧 run 缺配置",
        "output_file": record.get("output_file") or "",
        "run_dir": str(run_dir),
    }


def native_openviking_baseline_path(output_dir: Path) -> Path:
    return output_dir / NATIVE_OPENVIKING_BASELINE_FILE


def _native_openviking_score(row: dict[str, Any]) -> int:
    kind = str(row.get("kind") or "").lower()
    agent_type = str(row.get("agent_type") or "").lower()
    if kind in {"judge", "stats", "openviking_import", "echomemory_import", "adapter"} or agent_type == "judge":
        return -100
    text = " ".join(
        str(row.get(key) or "")
        for key in ("id", "name", "kind", "agent_type", "prompt_mode", "output_file", "run_dir")
    ).lower()
    score = 0
    if "native_vikingbot_cli" in text:
        score += 100
    if "native_vikingbot" in text:
        score += 60
    if "vikingbot_results.csv" in text:
        score += 35
    if "openviking_memory_qa_results.csv" in text:
        score += 25
    if "openviking_qa" in text:
        score += 20
    if "aligned" in text or "custom_agent" in text:
        score -= 35
    if "echomemory" in text or "local_agent" in text:
        score -= 80
    dataset_format = str(row.get("dataset_format") or "").lower()
    if dataset_format == "locomo":
        score += 15
    if row.get("accuracy") is not None:
        score += 10
    return score


def _row_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _native_openviking_candidate_key(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    rows = _row_int(row.get("rows"))
    graded = _row_int(row.get("graded"))
    has_score = 1 if row.get("accuracy") is not None else 0
    score = int(row.get("native_openviking_match_score") or 0)
    return (has_score, rows, graded, score, str(row.get("created_at") or ""))


def native_openviking_candidates(output_dir: Path, limit: int = 12) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for record in list_runs(output_dir, 10000, "", "all"):
        run_dir_text = str(record.get("run_dir") or "")
        if not run_dir_text:
            continue
        try:
            row = run_compare_row(Path(run_dir_text))
        except Exception:
            continue
        score = _native_openviking_score(row)
        if score <= 0:
            continue
        row["native_openviking_match_score"] = score
        candidates.append(row)
    candidates.sort(key=_native_openviking_candidate_key, reverse=True)
    return candidates[:limit]


def load_native_openviking_baseline(output_dir: Path) -> dict[str, Any]:
    path = native_openviking_baseline_path(output_dir)
    if not path.exists():
        candidates = native_openviking_candidates(output_dir, 5)
        return {
            "configured": False,
            "path": str(path),
            "baseline": None,
            "candidates": candidates,
        }
    data = read_json(path)
    baseline = data.get("baseline") if isinstance(data, dict) else None
    if not isinstance(baseline, dict):
        baseline = None
    return {
        "configured": bool(baseline),
        "path": str(path),
        "baseline": baseline,
        "candidates": [] if baseline else native_openviking_candidates(output_dir, 5),
        "updated_at": data.get("updated_at") if isinstance(data, dict) else "",
    }


def pin_native_openviking_baseline(
    output_dir: Path,
    run_dir: Path | None = None,
    auto: bool = False,
    note: str = "",
) -> dict[str, Any]:
    candidates = native_openviking_candidates(output_dir, 12)
    row: dict[str, Any] | None = None
    if auto:
        all_candidates = native_openviking_candidates(output_dir, 10000)
        row = all_candidates[0] if all_candidates else None
    elif run_dir is not None:
        row = run_compare_row(run_dir)
        row["native_openviking_match_score"] = _native_openviking_score(row)
    if not row:
        raise FileNotFoundError("native OpenViking baseline candidate not found")
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "baseline": row,
    }
    path = native_openviking_baseline_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "configured": True,
        "path": str(path),
        "baseline": row,
        "candidates": candidates,
        "updated_at": payload["updated_at"],
    }


def compare_run_dirs(run_dirs: list[Path]) -> dict[str, Any]:
    rows = [run_compare_row(path) for path in run_dirs]
    baseline = next((row for row in rows if row.get("accuracy") is not None), None)
    for row in rows:
        row["delta_vs_first"] = (
            row["accuracy"] - baseline["accuracy"]
            if baseline and row.get("accuracy") is not None and baseline.get("accuracy") is not None
            else None
        )
    return {"runs": rows, "count": len(rows), "baseline": baseline.get("id") if baseline else ""}


def csv_preview(path: Path, limit: int = 20) -> dict[str, Any]:
    if not path.exists():
        return {"error": "file not found", "rows": [], "fieldnames": []}
    rows = []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for index, row in enumerate(reader):
            if index >= limit:
                break
            rows.append(row)
    return {"path": str(path), "fieldnames": fieldnames, "rows": rows}


def qa_diagnostics(path: Path, dataset_path: Path | None = None, sample: str = "all") -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8", errors="replace")))
    summary = report_service.parse_csv_summary(path)
    question_rows: dict[str, list[dict[str, str]]] = {}
    retryable_rows: list[dict[str, str]] = []
    for row in rows:
        qid = str(row.get("question_id") or "").strip()
        if qid:
            question_rows.setdefault(qid, []).append(row)
        if report_service.retryable_qa_failure(row):
            retryable_rows.append(row)

    retryable_failed_question_ids: list[str] = []
    duplicate_question_ids: list[str] = []
    for qid, group in question_rows.items():
        if report_service.retryable_qa_failure(group[-1]) and qid not in retryable_failed_question_ids:
            retryable_failed_question_ids.append(qid)
        if len(group) > 1:
            duplicate_question_ids.append(qid)

    diagnostics: dict[str, Any] = {
        "input": str(path),
        "rows": len(rows),
        "summary": summary,
        "unique_question_ids": len(question_rows),
        "duplicate_question_ids_count": len(duplicate_question_ids),
        "duplicate_question_ids": duplicate_question_ids,
        "retryable_failed_rows": len(retryable_rows),
        "retryable_failed_questions": len(retryable_failed_question_ids),
        "retryable_failed_question_ids": retryable_failed_question_ids,
        "retryable_failed_examples": [
            {
                "question_id": row.get("question_id", ""),
                "sample_id": row.get("sample_id", ""),
                "category": row.get("category", ""),
                "question": row.get("question", ""),
                "health_status": row.get("health_status", ""),
                "model_status": row.get("model_status", ""),
                "answer_status": row.get("answer_status", ""),
                "retrieval_status": row.get("retrieval_status", ""),
            }
            for row in retryable_rows[:12]
        ],
    }

    if dataset_path and dataset_path.exists():
        from . import datasets as dataset_service

        question_data = dataset_service.benchmark_questions(dataset_path, sample, limit=500000)
        expected_questions = question_data.get("questions") or []
        expected_ids = [str(row.get("question_id") or "").strip() for row in expected_questions if str(row.get("question_id") or "").strip()]
        expected_set = set(expected_ids)
        actual_set = set(question_rows)
        missing_ids = [qid for qid in expected_ids if qid not in actual_set]
        diagnostics.update(
            {
                "dataset": str(dataset_path),
                "sample": sample,
                "expected_questions": len(expected_ids),
                "missing_questions_count": len(missing_ids),
                "missing_question_ids": missing_ids,
                "missing_examples": [
                    {
                        "question_id": row.get("question_id", ""),
                        "sample_id": row.get("sample_id", ""),
                        "category": row.get("category", ""),
                        "question": row.get("question", ""),
                        "answer": row.get("answer", ""),
                    }
                    for row in expected_questions
                    if str(row.get("question_id") or "").strip() in set(missing_ids[:12])
                ],
                "unexpected_question_ids_count": len(actual_set - expected_set),
                "unexpected_question_ids": sorted(actual_set - expected_set)[:50],
            }
        )
    return diagnostics


def _pending_row_matches(
    row: dict[str, Any],
    category: str = "",
    query: str = "",
    min_tokens: int | None = None,
    max_tokens: int | None = None,
) -> bool:
    if category and str(row.get("category") or "") != category:
        return False
    query_l = query.strip().lower()
    if query_l:
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ["question_id", "sample_id", "question", "answer", "response", "category"]
        ).lower()
        if query_l not in haystack:
            return False
    try:
        token_value = int(float(row.get("injection_tokens_est") or 0))
    except ValueError:
        token_value = 0
    if min_tokens is not None and token_value < min_tokens:
        return False
    if max_tokens is not None and token_value > max_tokens:
        return False
    return True


def csv_pending_preview(
    path: Path,
    limit: int = 20,
    category: str = "",
    query: str = "",
    min_tokens: int | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return {"error": "file not found", "rows": [], "fieldnames": [], "total_pending": 0}
    rows = []
    total_pending = 0
    total_matched = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for index, row in enumerate(reader):
            if row_grade(row) != "UNSCORED":
                continue
            total_pending += 1
            if not _pending_row_matches(row, category, query, min_tokens, max_tokens):
                continue
            total_matched += 1
            if len(rows) < limit:
                item = dict(row)
                item["_row_index"] = index
                rows.append(item)
    return {
        "path": str(path),
        "fieldnames": fieldnames,
        "rows": rows,
        "total_pending": total_pending,
        "total_matched": total_matched,
        "filters": {
            "category": category,
            "query": query,
            "min_tokens": min_tokens,
            "max_tokens": max_tokens,
        },
        "limit": limit,
    }


def export_pending_csv(
    path: Path,
    category: str = "",
    query: str = "",
    min_tokens: int | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    pending_rows = []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            if row_grade(row) == "UNSCORED" and _pending_row_matches(row, category, query, min_tokens, max_tokens):
                pending_rows.append(row)
    suffix = ".pending_judge.filtered.csv" if any([category, query, min_tokens is not None, max_tokens is not None]) else ".pending_judge.csv"
    out_path = path.with_name(path.stem + suffix)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(pending_rows)
    return {
        "input": str(path),
        "output": str(out_path),
        "rows": len(pending_rows),
        "filters": {
            "category": category,
            "query": query,
            "min_tokens": min_tokens,
            "max_tokens": max_tokens,
        },
    }


def ensure_judge_columns(path: Path) -> None:
    if not path.exists() or path.suffix.lower() != ".csv":
        return
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    needed = [name for name in ["result", "reasoning"] if name not in fieldnames]
    if not needed:
        return
    fieldnames.extend(needed)
    for row in rows:
        for name in needed:
            row.setdefault(name, "")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def tail_file(path: Path, limit: int = 12000) -> dict[str, Any]:
    empty = {
        "path": str(path),
        "exists": False,
        "text": "",
        "rate_limit_hits": [],
        "rate_limit_count": 0,
        "model_api_error_hits": [],
        "model_api_error_count": 0,
        "retrieval_retry_hits": [],
        "retrieval_retry_count": 0,
        "embedding_timeout_hits": [],
        "embedding_timeout_count": 0,
        "embedding_circuit_breaker_hits": [],
        "embedding_circuit_breaker_count": 0,
        "generic_failure_hits": [],
        "generic_failure_count": 0,
    }
    if not path.exists():
        return empty
    data = path.read_bytes()
    text = data[-limit:].decode("utf-8", errors="replace")
    full_text = data.decode("utf-8", errors="replace")
    hits = []
    model_api_error_hits = []
    retrieval_retry_hits = []
    embedding_timeout_hits = []
    embedding_circuit_breaker_hits = []
    generic_failure_hits = []
    patterns = [
        r"rate limit",
        r"http\s*429",
        r"\bstatus(?:_code)?[=:]\s*429\b",
        r"\b429\b.*(?:too many requests|rate limit|quota|throttle|限流|请求过多)",
        r"(?:too many requests|rate limit|quota|throttle|限流|请求过多).*\b429\b",
        r"too many requests",
        r"quota",
        r"throttle",
        r"限流",
        r"请求过多",
    ]
    for line in full_text.splitlines():
        if any(re.search(pattern, line, re.I) for pattern in patterns):
            hits.append(line[-500:])
        if re.search(r"\[model\]\s+retry=.*\bkind=(api_error|timeout|rate_limited)\b|authentication failed|autherror|401", line, re.I):
            model_api_error_hits.append(line[-500:])
        if re.search(r"\[retrieval\]\s+retry=", line, re.I):
            retrieval_retry_hits.append(line[-500:])
        if re.search(
            r"(embedding slow call|failed to generate embedding|query embedding failed|request timed out|re-enqueued embedding|re-enqueueing messages|_scan_tree hit max_files|results may be incomplete)",
            line,
            re.I,
        ):
            embedding_timeout_hits.append(line[-500:])
        if re.search(r"(circuit breaker tripped|embedding circuit breaker is open)", line, re.I):
            embedding_circuit_breaker_hits.append(line[-500:])
        lowered = line.lower()
        benign_error_line = (
            '"error": null' in lowered
            or '"error": ""' in lowered
            or "'error': none" in lowered
            or "[model] retry=" in lowered
            or "[retrieval] retry=" in lowered
            or "no errors file found" in lowered
        )
        if not benign_error_line and re.search(
            r"(traceback|exception|fatal|\berror\b|\bfailed\b|status=failed|returncode[=:\s]+[1-9])",
            line,
            re.I,
        ):
            generic_failure_hits.append(line[-500:])
    return {
        "path": str(path),
        "exists": True,
        "bytes": len(data),
        "text": text,
        "rate_limit_hits": hits[-20:],
        "rate_limit_count": len(hits),
        "model_api_error_hits": model_api_error_hits[-20:],
        "model_api_error_count": len(model_api_error_hits),
        "retrieval_retry_hits": retrieval_retry_hits[-20:],
        "retrieval_retry_count": len(retrieval_retry_hits),
        "embedding_timeout_hits": embedding_timeout_hits[-20:],
        "embedding_timeout_count": len(embedding_timeout_hits),
        "embedding_circuit_breaker_hits": embedding_circuit_breaker_hits[-20:],
        "embedding_circuit_breaker_count": len(embedding_circuit_breaker_hits),
        "generic_failure_hits": generic_failure_hits[-20:],
        "generic_failure_count": len(generic_failure_hits),
    }


def relevant_memory(run_dir: Path, limit: int = 20) -> dict[str, Any]:
    items = []
    for path in sorted(run_dir.rglob("*.recall.json")):
        try:
            data = read_json(path)
        except Exception:
            continue
        q_match = re.search(r"q(\d+)\.recall\.json$", path.name)
        question_no = int(q_match.group(1)) if q_match else None
        for selected in data.get("selected") or []:
            items.append(
                {
                    "question_no": question_no,
                    "file": str(path),
                    "uri": selected.get("uri"),
                    "score": selected.get("score"),
                    "tokens": selected.get("tokens"),
                    "chars": selected.get("chars"),
                }
            )
    items.sort(key=lambda item: (item.get("question_no") or 0, -(item.get("score") or 0)))
    return {"run_dir": str(run_dir), "count": len(items), "items": items[:limit]}


def run_detail(run_dir: Path, active_run_ids: set[str] | None = None) -> dict[str, Any] | None:
    record = run_record(run_dir, active_run_ids)
    if not record:
        return None
    manifest = {}
    manifest_path = Path(record.get("manifest_file") or run_dir / "manifest.json")
    if manifest_path.exists():
        try:
            manifest = read_json(manifest_path)
        except Exception as exc:
            manifest = {"error": str(exc)}
    output = Path(record["output_file"]) if record.get("output_file") else None
    preview = csv_preview(output, 12) if output else {"rows": [], "fieldnames": []}
    log_tail = ""
    log_path = Path(record.get("log_file") or "")
    if log_path.exists():
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]

    def artifact_info(path_value: str | Path | None, expect_dir: bool = False) -> dict[str, Any]:
        path = Path(path_value) if path_value else None
        exists = path.is_dir() if expect_dir and path else path.exists() if path else False
        return {"path": str(path) if path else "", "exists": bool(exists)}

    output_dir = output.parent if output else run_dir
    return {
        "record": record,
        "manifest": manifest,
        "preview": preview,
        "log_tail": log_tail,
        "artifact_status": {
            "output_file": artifact_info(record.get("output_file") or ""),
            "run_dir": artifact_info(record.get("run_dir") or run_dir, expect_dir=True),
            "log_file": artifact_info(record.get("log_file") or ""),
            "manifest_file": artifact_info(record.get("manifest_file") or ""),
            "config_snapshot": artifact_info(run_dir / "config_snapshot.json"),
            "report": artifact_info(run_dir / "report.md"),
            "report_html": artifact_info(run_dir / "report.html"),
            "graph_report_html": artifact_info(run_dir / "graph_report.html"),
            "summary": artifact_info(output_dir / "summary.json"),
            "longmemeval_official_summary": artifact_info(output_dir / "longmemeval_official_summary.json"),
            "hotpotqa_answer_summary": artifact_info(output_dir / "hotpotqa_answer_summary.json"),
        },
    }
