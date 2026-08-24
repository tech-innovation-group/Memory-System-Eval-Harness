#!/usr/bin/env python3
"""Extract a unified, multi-dimensional metric snapshot from any eval result folder.

Format-agnostic: auto-detects locomo / dynamic / hotpotqa / longmemeval result
folders produced by Memory-System-Eval-Harness, and degrades to a generic
"whatever summary.json exposes" mode when known artifacts are missing.

The script is deliberately deterministic and read-only: it computes numbers and
lists *observations* (noticed facts with heuristic flags). Turning observations
into root-cause analysis and improvement recommendations is the skill's job
(the agent reasons about them), not this script's.

Usage:
  python analyze_eval_results.py <result_dir> [<result_dir> ...] [--json] [--report]
  python analyze_eval_results.py <results_parent> --latest          # pick newest run per arg
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from pathlib import Path

# Heuristic thresholds that turn a number into a *suggested* observation.
# These are interpretation hints for the agent, NOT hard conclusions: the skill
# must re-check against raw artifacts before asserting a problem.
ACCURACY_WARN = 0.80
TOKENS_PER_CORRECT_WARN = 2000.0
LONG_TAIL_RATIO_WARN = 5.0
EMPTY_RETRIEVAL_WARN = 0.10
RETRIEVAL_FRACTION_WARN = 0.5
MEMORY_UTILIZATION_WARN = 7.0
MEMORY_QUALITY_WARN = 80.0


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def percentiles(values: list[float], ps: list[float]) -> dict[str, float | None]:
    if not values:
        return {f"p{p}": None for p in ps}
    vals = sorted(values)
    n = len(vals)
    out = {}
    for p in ps:
        idx = min(n - 1, max(0, int(round(p / 100.0 * (n - 1)))))
        out[f"p{p}"] = round(vals[idx], 4)
    return out


def num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def has(path: Path) -> bool:
    return path.is_file()


def detect_type(folder: Path) -> str:
    """Detect result schema by the artifacts present (most specific first)."""
    if has(folder / "dynamic_results.json"):
        return "dynamic"
    if has(folder / "eval_results.csv"):
        # hotpotqa -> answer/support/joint columns; longmemeval -> per_type.
        summary = _safe_summary(folder)
        if summary and summary.get("benchmark") == "longmemeval":
            return "longmemeval"
        if summary and summary.get("benchmark") == "hotpotqa":
            return "hotpotqa"
        return "eval_csv"
    if has(folder / "qa_results.csv") and has(folder / "judge_results.csv"):
        return "locomo"
    if has(folder / "summary.json"):
        return "generic"
    return "unknown"


def _safe_summary(folder: Path) -> dict:
    p = folder / "summary.json"
    if not has(p):
        return {}
    try:
        return read_json(p)
    except Exception:
        return {}


def _stats_for(vals: list[float]) -> dict:
    if not vals:
        return {"count": 0, "avg": None, "p50": None, "p95": None, "p99": None, "max": None}
    return {
        "count": len(vals),
        "avg": round(statistics.mean(vals), 4),
        "max": round(max(vals), 4),
        **percentiles(vals, [50, 95, 99]),
    }


def _merge_stat(a: dict, b: dict) -> dict:
    """Prefer a non-empty stats dict; else b."""
    return a if a.get("count") else b


# --------------------------------------------------------------------------- #
# Common extraction (any folder with summary.json)
# --------------------------------------------------------------------------- #

def extract_common(folder: Path) -> dict:
    summary = _safe_summary(folder)
    config = {}
    cfg_path = folder / "config.json"
    if has(cfg_path):
        try:
            config = read_json(cfg_path)
        except Exception:
            pass
    cfg = config.get("config", config) if isinstance(config, dict) else {}
    agent_opts = (config.get("agent_options") or {}) if isinstance(config, dict) else {}
    if not isinstance(agent_opts, dict):
        agent_opts = {}

    identity = summary.get("memory_identity") or {}
    provenance = summary.get("memory_provenance") or {}
    sb = summary.get("strict_blackbox") or {}
    sb_metrics = sb.get("metrics") or {}

    start = summary.get("run_started_at")
    finish = summary.get("run_finished_at") or summary.get("finished_at")
    wall = None
    if start and finish:
        from datetime import datetime

        fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
        try:
            t0 = datetime.strptime(start, fmt)
            t1 = datetime.strptime(finish, fmt)
            wall = round((t1 - t0).total_seconds(), 1)
        except ValueError:
            wall = None

    return {
        "benchmark": summary.get("benchmark"),
        "status": summary.get("status"),
        "backend": cfg.get("memory_backend"),
        "plugin": cfg.get("agent_plugin") or agent_opts.get("agent_plugin"),
        "dataset": cfg.get("dataset_path") or summary.get("dataset"),
        "model": cfg.get("llm_model") or summary.get("llm_model"),
        "top_k": cfg.get("top_k") or agent_opts.get("top_k"),
        "memory_budget_chars": cfg.get("memory_budget_chars") or agent_opts.get("memory_budget_chars"),
        "question_limit": cfg.get("question_limit") or summary.get("question_limit"),
        "run_window": {
            "started_at": start,
            "finished_at": finish,
            "wall_clock_s": wall,
        },
        "session_mode": summary.get("session_mode"),
        "retrieval_scope": summary.get("retrieval_scope"),
        "memory_identity": {
            "mode": identity.get("mode"),
            # auth_key deliberately NOT exported here (secret hygiene).
        },
        "memory_provenance": {
            "expected_session_count": provenance.get("expected_session_count"),
            "actual_session_count": provenance.get("actual_session_count"),
        },
        "import": {
            "import_ok": summary.get("import_ok"),
            "import_total": summary.get("import_total"),
            "incomplete_imports": summary.get("incomplete_imports"),
        },
        "qa_count": summary.get("qa_count") or summary.get("total_questions"),
        "unavailable_common": {
            "internal_memory_injection_tokens": sb_metrics.get("internal_memory_injection_tokens"),
            "initial_memory_import_time_s": sb_metrics.get("initial_memory_import_time_s"),
        },
    }


# --------------------------------------------------------------------------- #
# Dimensions
# --------------------------------------------------------------------------- #

def extract_tokens(folder: Path, common: dict) -> dict:
    summary = _safe_summary(folder)
    sb_metrics = (summary.get("strict_blackbox") or {}).get("metrics") or {}
    tokens = {}

    if sb_metrics:
        tokens["answer_prompt_tokens"] = sb_metrics.get("answer_prompt_tokens")
        tokens["answer_completion_tokens"] = sb_metrics.get("answer_completion_tokens")
        tokens["answer_total_tokens"] = sb_metrics.get("answer_total_tokens")
        tokens["judge_prompt_tokens"] = sb_metrics.get("judge_prompt_tokens")
        tokens["judge_completion_tokens"] = sb_metrics.get("judge_completion_tokens")
        tokens["judge_total_tokens"] = sb_metrics.get("judge_total_tokens")
        tokens["visible_model_total_tokens"] = sb_metrics.get("visible_model_total_tokens")
        tokens["tokens_per_correct"] = sb_metrics.get("tokens_per_correct")

    qa = folder / "qa_results.csv"
    if has(qa):
        rows = read_csv(qa)
        prompts = [num(r.get("prompt_tokens")) for r in rows]
        completions = [num(r.get("completion_tokens")) for r in rows]
        prompts = [v for v in prompts if v is not None]
        completions = [v for v in completions if v is not None]
        if prompts and "answer_prompt_tokens" not in tokens:
            tokens["answer_prompt_tokens"] = _stats_for(prompts)
        if completions and "answer_completion_tokens" not in tokens:
            tokens["answer_completion_tokens"] = _stats_for(completions)

    dr = folder / "dynamic_results.json"
    if has(dr):
        data = read_json(dr)
        rounds = data.get("rounds") or []
        prompts = [num(r.get("prompt_tokens")) for r in rounds]
        completions = [num(r.get("completion_tokens")) for r in rounds]
        cached = [num(r.get("cached_tokens")) for r in rounds]
        prompts = [v for v in prompts if v is not None]
        completions = [v for v in completions if v is not None]
        cached = [v for v in cached if v is not None]
        if prompts:
            tokens["prompt_tokens_per_round"] = _stats_for(prompts)
        if completions:
            tokens["completion_tokens_per_round"] = _stats_for(completions)
        if cached:
            tokens["cached_tokens_per_round"] = _stats_for(cached)
        tokens["ttft_ms_per_round"] = _stats_for(
            [v for v in (num(r.get("ttft_ms")) for r in rounds) if v is not None]
        )

    if not tokens:
        return {"unavailable": "no token usage data (summary / qa_results / dynamic_results)"}
    return tokens


def _latency_from_csv(folder: Path, column: str) -> dict:
    qa = folder / "qa_results.csv"
    if not has(qa):
        return {}
    rows = read_csv(qa)
    vals = [num(r.get(column)) for r in rows]
    vals = [v / 1000.0 if v is not None and (column.endswith("_ms") or column.endswith("ms")) else v for v in vals if v is not None]
    return _stats_for(vals)


def extract_retrieval_latency(folder: Path, common: dict) -> dict:
    result = {}
    qa = folder / "qa_results.csv"
    if has(qa):
        stats = _latency_from_csv(folder, "retrieval_latency_ms")
        if stats.get("count"):
            result["per_question_s"] = stats

    dr = folder / "dynamic_results.json"
    if has(dr):
        rounds = (read_json(dr).get("rounds") or [])
        vals = [num(r.get("retrieval_latency_s")) for r in rounds]
        vals = [v for v in vals if v is not None]
        if vals:
            result["per_round_s"] = _stats_for(vals)

    if not result:
        return {"unavailable": "no retrieval latency data (qa_results.retrieval_latency_ms / dynamic_results.retrieval_latency_s)"}
    return result


def extract_injection_latency(folder: Path, common: dict) -> dict:
    result = {}
    imp = folder / "import_results.csv"
    if has(imp):
        rows = read_csv(imp)
        elapsed = [num(r.get("elapsed_s")) for r in rows]
        elapsed = [v for v in elapsed if v is not None]
        if elapsed:
            result["per_session_import_s"] = _stats_for(elapsed)
            result["total_import_s"] = round(sum(elapsed), 1)
            ok = sum(1 for r in rows if r.get("status") == "completed")
            result["sessions_ok"] = ok
            result["sessions_total"] = len(rows)

    bl = folder / "backend_logs.json"
    if has(bl):
        try:
            data = read_json(bl)
        except Exception:
            data = {}
        items = data.get("items") or []
        durations = {}
        for it in items:
            ev = it.get("event")
            dm = num(it.get("duration_ms"))
            if ev and dm is not None:
                durations.setdefault(ev, []).append(dm)
        if durations:
            result["backend_commit_stages_ms"] = {
                ev: _stats_for(vals) for ev, vals in sorted(durations.items())
                if ev in (
                    "commit_completed",
                    "memory_extraction_completed",
                    "memory_extraction_started",
                    "atomic_pipeline_completed",
                    "atomic_macro_stage_completed",
                    "http_request_completed",
                )
            }

    if not result:
        return {"unavailable": "no injection latency data (import_results.csv / backend_logs.json)"}
    return result


# --------------------------------------------------------------------------- #
# Backend logs (backend_logs.json): EchoMem internal stages / provider usage
# --------------------------------------------------------------------------- #

def _avg_macro_stage_timings(records: list[dict]) -> dict[str, dict]:
    """Average atomic_pipeline macro-stage timings (ms) across records."""
    per_stage: dict[str, list[float]] = {}
    for rec in records:
        for stage, value in (rec.get("macro_stage_timings_ms") or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            per_stage.setdefault(stage, []).append(number)
    return {
        stage: _stats_for(vals) for stage, vals in sorted(per_stage.items())
    }


def _sum_provider_diagnostics(records: list[dict]) -> dict[str, float]:
    """Sum numeric provider_diagnostics fields across atomic_pipeline records."""
    totals: dict[str, float] = {}
    for rec in records:
        diagnostics = rec.get("provider_diagnostics") or {}
        for key, value in diagnostics.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            totals[key] = totals.get(key, 0.0) + number
    return {key: round(value, 1) for key, value in sorted(totals.items())}


def extract_backend(folder: Path, common: dict) -> dict:
    """Extract EchoMem backend metrics from backend_logs.json.

    backend_logs.json is the getlog() snapshot of the EchoMem service: it
    carries per-event durations, the atomic extraction pipeline breakdown
    (macro stages + provider token/embedding diagnostics), per-route HTTP
    latency, index diagnostics, and log-window pagination.
    """
    path = folder / "backend_logs.json"
    if not has(path):
        return {"unavailable": "no backend_logs.json"}
    try:
        data = read_json(path)
    except Exception:
        return {"unavailable": "backend_logs.json is not valid JSON"}
    items = data.get("items") or []
    result: dict[str, Any] = {}

    page = data.get("page") or {}
    diagnostics = data.get("diagnostics") or {}
    total_matched = page.get("total_matched")
    returned = page.get("returned")
    has_more = page.get("has_more")
    result["page"] = {
        "total_matched": total_matched,
        "returned": returned,
        "has_more": bool(has_more),
        "complete": bool(
            items
            and not has_more
            and not diagnostics.get("truncated")
            and (
                total_matched is None
                or len(items) == int(total_matched)
            )
        ),
    }
    if diagnostics:
        result["index_diagnostics"] = {
            "files_indexed": diagnostics.get("files_indexed"),
            "records_indexed": diagnostics.get("records_indexed"),
            "records_missing_user_id": diagnostics.get("records_missing_user_id"),
            "parse_errors": diagnostics.get("parse_errors"),
            "partial_lines_skipped": diagnostics.get("partial_lines_skipped"),
            "index_rebuilt": diagnostics.get("index_rebuilt"),
        }

    atomic = [
        item for item in items
        if item.get("event") == "atomic_pipeline_completed"
    ]
    if atomic:
        outcome_reasons: dict[str, int] = {}
        for rec in atomic:
            reason = str(rec.get("outcome_reason") or "unknown")
            outcome_reasons[reason] = outcome_reasons.get(reason, 0) + 1
        result["atomic_pipeline"] = {
            "commit_count": len(atomic),
            "outcome_reasons": outcome_reasons,
            "max_stage_error_count": max(
                int(rec.get("stage_error_count") or 0) for rec in atomic
            ),
            "macro_stage_avg_ms": _avg_macro_stage_timings(atomic),
            "provider_diagnostics_total": _sum_provider_diagnostics(atomic),
        }

    http_items = [
        item for item in items
        if item.get("event") == "http_request_completed"
    ]
    if http_items:
        by_route: dict[tuple[str, str], list[float]] = {}
        status_counts: dict[tuple[str, str], dict[str, int]] = {}
        for rec in http_items:
            method = str(rec.get("method") or "?")
            route = str(rec.get("route") or "?")
            duration = num(rec.get("duration_ms"))
            if duration is None:
                continue
            key = (method, route)
            by_route.setdefault(key, []).append(duration)
            status = str(rec.get("status_code") or "?")
            counter = status_counts.setdefault(key, {})
            counter[status] = counter.get(status, 0) + 1
        result["http_routes"] = {
            f"{method} {route}": {
                **_stats_for(values),
                "status_codes": status_counts.get((method, route)),
            }
            for (method, route), values in sorted(
                by_route.items(), key=lambda kv: len(kv[1]), reverse=True
            )
        }

    if not result:
        return {"unavailable": "backend_logs.json contains no records"}
    return result


def _evidence_items(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        raw = r.get("retrieval_items_json") or ""
        if not raw:
            continue
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(items, list):
            out.extend(items)
    return out


def extract_retrieval_precision(folder: Path, common: dict) -> dict:
    summary = _safe_summary(folder)
    result = {}
    if summary.get("accuracy") is not None:
        result["accuracy"] = summary.get("accuracy")
        result["judge_correct"] = summary.get("judge_correct")
        result["judge_wrong"] = summary.get("judge_wrong")
        result["judge_graded"] = summary.get("judge_graded")

    diag = folder / "diagnosis.json"
    if has(diag):
        try:
            d = read_json(diag)
        except Exception:
            d = {}
        if d.get("failure_breakdown"):
            result["failure_modes"] = d["failure_breakdown"]
        if d.get("category_breakdown"):
            result["category_breakdown"] = d["category_breakdown"]
        if d.get("retrieval_coverage") is not None:
            result["retrieval_coverage"] = d["retrieval_coverage"]

    ev = folder / "eval_results.csv"
    if has(ev):
        rows = read_csv(ev)
        for metric in ("answer_em", "answer_f1", "support_em", "support_f1", "joint_em", "joint_f1"):
            vals = [num(r.get(metric)) for r in rows]
            vals = [v for v in vals if v is not None]
            if vals:
                result[metric] = round(statistics.mean(vals), 4)

    qr = folder / "quality_report.json"
    if has(qr):
        try:
            q = read_json(qr)
        except Exception:
            q = {}
        qs = q.get("summary") or {}
        if qs.get("avg_quality_score") is not None:
            result["avg_quality_score"] = qs["avg_quality_score"]
            result["avg_dimension_scores"] = qs.get("avg_dimension_scores")
        results = q.get("results") or []
        if results:
            result["quality_results_count"] = len(results)
            result["hallucination_detected_count"] = sum(
                1 for r in results if r.get("hallucination_detected")
            )
            result["task_completed_count"] = sum(1 for r in results if r.get("task_completed"))

    if not result:
        return {"unavailable": "no precision data (summary.accuracy / diagnosis / eval_results / quality_report)"}
    return result


def extract_memory_count(folder: Path, common: dict) -> dict:
    result = {}
    qa = folder / "qa_results.csv"
    if has(qa):
        rows = read_csv(qa)
        counts = [num(r.get("retrieval_count")) for r in rows]
        counts = [v for v in counts if v is not None]
        if counts:
            result["retrieval_count_per_question"] = _stats_for(counts)
        items = _evidence_items(rows)
        if items:
            result["evidence_items_total"] = len(items)
            result["evidence_items_per_question"] = round(len(items) / max(len(rows), 1), 2)
            scores = [num(i.get("score")) for i in items]
            scores = [v for v in scores if v is not None]
            if scores:
                result["evidence_score"] = _stats_for(scores)
            types = {}
            for i in items:
                t = i.get("memory_type") or "unknown"
                types[t] = types.get(t, 0) + 1
            if types:
                result["evidence_memory_types"] = types

    qr = folder / "quality_report.json"
    if has(qr):
        try:
            q = read_json(qr)
        except Exception:
            q = {}
        qs = q.get("summary") or {}
        if qs.get("total_recalled_memories") is not None:
            result["total_recalled_memories"] = qs["total_recalled_memories"]
        results = q.get("results") or []
        recalls = [num(r.get("recalled_memories_count")) for r in results]
        recalls = [v for v in recalls if v is not None]
        if recalls:
            result["recalled_memories_per_round"] = _stats_for(recalls)
        grounds = [num(r.get("ground_facts_count")) for r in results]
        grounds = [v for v in grounds if v is not None]
        if grounds:
            result["ground_facts_per_round"] = _stats_for(grounds)

    dr = folder / "dynamic_results.json"
    if has(dr):
        rounds = (read_json(dr).get("rounds") or [])
        prefetch = sum(1 for r in rounds if r.get("prefetch_committed"))
        if rounds:
            result["prefetch_committed_count"] = prefetch

    if not result:
        return {"unavailable": "no memory-count data (qa_results.retrieval_count / quality_report / dynamic_results)"}
    return result


def extract_quality(folder: Path, common: dict) -> dict:
    result = {}
    dr = folder / "dynamic_results.json"
    if has(dr):
        data = read_json(dr)
        rounds = data.get("rounds") or []
        result["total_rounds"] = len(rounds)
        tc = [num(r.get("tool_call_count")) for r in rounds]
        tc = [v for v in tc if v is not None]
        if tc:
            result["tool_call_count_per_round"] = _stats_for(tc)
        it = [num(r.get("iterations")) for r in rounds]
        it = [v for v in it if v is not None]
        if it:
            result["iterations_per_round"] = _stats_for(it)
        llm = [num(r.get("llm_latency_s")) for r in rounds]
        llm = [v for v in llm if v is not None]
        if llm:
            result["llm_latency_per_round_s"] = _stats_for(llm)
        elapsed = [num(r.get("elapsed_s")) for r in rounds]
        elapsed = [v for v in elapsed if v is not None]
        if elapsed:
            result["elapsed_per_round_s"] = _stats_for(elapsed)

    qr = folder / "quality_report.json"
    if has(qr):
        try:
            q = read_json(qr)
        except Exception:
            q = {}
        results = q.get("results") or []
        strengths = [s for r in results for s in (r.get("strengths") or [])]
        weaknesses = [w for r in results for w in (r.get("weaknesses") or [])]
        if strengths:
            result["strength_notes_count"] = len(strengths)
        if weaknesses:
            result["weakness_notes_count"] = len(weaknesses)
            result["weakness_notes"] = weaknesses[:20]
        ttft = [num(r.get("ttft_ms")) for r in results]
        ttft = [v for v in ttft if v is not None]
        if ttft:
            result["ttft_ms_per_round"] = _stats_for(ttft)
        else:
            result["ttft_unavailable_reason"] = "no TTFT values (this plugin does not measure typing-time prefill)"

    if not result:
        return {"unavailable": "no dynamic/quality data"}
    return result


def extract_health(folder: Path, common: dict) -> dict:
    summary = _safe_summary(folder)
    sb = summary.get("strict_blackbox") or {}
    sb_metrics = sb.get("metrics") or {}
    result = {}
    if sb_metrics:
        for key in (
            "request_success_rate",
            "empty_retrieval_rate",
            "failure_rate",
            "submission_rate",
            "retry_rate",
            "qa_errors",
        ):
            if key in sb_metrics:
                result[key] = sb_metrics[key]
    if summary.get("qa_errors") is not None:
        result["qa_errors"] = summary["qa_errors"]
    if summary.get("retrieval_errors") is not None:
        result["retrieval_errors"] = summary["retrieval_errors"]
    if summary.get("judge_errors") is not None:
        result["judge_errors"] = summary["judge_errors"]

    qa = folder / "qa_results.csv"
    if has(qa):
        rows = read_csv(qa)
        if rows:
            result["qa_rows"] = len(rows)
            ok_status = sum(1 for r in rows if r.get("health_status") == "ok")
            result["health_ok_rows"] = ok_status

    if not result:
        return {"unavailable": "no health data"}
    return result


# --------------------------------------------------------------------------- #
# Observations (heuristic flags; the agent must verify against raw artifacts)
# --------------------------------------------------------------------------- #

def build_observations(folder: Path, common: dict, dims: dict) -> list[dict]:
    obs = []
    summary = _safe_summary(folder)

    tokens = dims.get("tokens") or {}
    tpc = tokens.get("tokens_per_correct")
    if tpc is not None and tpc > TOKENS_PER_CORRECT_WARN:
        obs.append(
            {
                "dimension": "tokens",
                "flag": "high_cost",
                "message": f"tokens_per_correct = {tpc:.1f} (> {TOKENS_PER_CORRECT_WARN:.0f}); per-correct-answer token cost is high.",
                "verify_hint": "Break down prompt vs completion; check injected-memory size vs top_k; check extraction/arbitration LLM calls if backend logs expose token counts.",
            }
        )

    prec = dims.get("retrieval_precision") or {}
    acc = prec.get("accuracy")
    if acc is not None and acc < ACCURACY_WARN:
        obs.append(
            {
                "dimension": "retrieval_precision",
                "flag": "low_accuracy",
                "message": f"accuracy = {acc*100:.2f}% (< {ACCURACY_WARN*100:.0f}%).",
                "verify_hint": "Read diagnosis.json failure_modes and retrieval_traces.jsonl to root-cause (temporal_reasoning / evidence_mismatch / evidence_unused / ...).",
            }
        )
    qscore = prec.get("avg_quality_score")
    if qscore is not None and qscore < MEMORY_QUALITY_WARN:
        obs.append(
            {
                "dimension": "retrieval_precision",
                "flag": "low_quality",
                "message": f"avg quality score = {qscore:.1f} (< {MEMORY_QUALITY_WARN:.0f}).",
                "verify_hint": "Inspect quality_report.json dimension scores and per-round weaknesses.",
            }
        )

    rl = dims.get("retrieval_latency") or {}
    for key, bucket in (("per_question_s", "per question"), ("per_round_s", "per round")):
        stats = rl.get(key)
        if not stats or not stats.get("count"):
            continue
        p50, p95 = stats.get("p50"), stats.get("p95")
        if p50 and p95 and p95 > LONG_TAIL_RATIO_WARN * p50:
            obs.append(
                {
                    "dimension": "retrieval_latency",
                    "flag": "long_tail",
                    "message": f"retrieval latency {bucket}: p95/p50 = {p95/p50:.1f}x (p50={p50}s, p95={p95}s).",
                    "verify_hint": "Find which questions have the worst retrieval latency in qa_results.csv; check for slow paths (multi-recall, rerank, cold embedding).",
                }
            )

    inj = dims.get("injection_latency") or {}
    stages = inj.get("backend_commit_stages_ms") or {}
    ext = stages.get("memory_extraction_completed") or stages.get("commit_completed")
    if ext and ext.get("count"):
        obs.append(
            {
                "dimension": "injection_latency",
                "flag": "info",
                "message": f"backend extraction/commit avg = {ext['avg']/1000:.1f}s per session (n={ext['count']}).",
                "verify_hint": "This is usually dominated by LLM extraction; check atomic_pipeline_completed model_token_counts if available in backend logs.",
            }
        )

    mc = dims.get("memory_count") or {}
    rcp = mc.get("retrieval_count_per_question")
    if rcp and rcp.get("count"):
        obs.append(
            {
                "dimension": "memory_count",
                "flag": "info",
                "message": f"avg retrieval_count per question = {rcp['avg']:.1f} (top_k={common.get('top_k')}).",
                "verify_hint": "Compare against top_k and memory_budget_chars; too many low-score items dilute precision and cost tokens.",
            }
        )

    health = dims.get("health") or {}
    er = health.get("empty_retrieval_rate")
    if er is not None and er > EMPTY_RETRIEVAL_WARN:
        obs.append(
            {
                "dimension": "health",
                "flag": "empty_retrieval",
                "message": f"empty retrieval rate = {er*100:.2f}% (> {EMPTY_RETRIEVAL_WARN*100:.0f}%).",
                "verify_hint": "Check which questions retrieved nothing; inspect retrieval_items_json / retrieval_traces.",
            }
        )
    if summary.get("status") and summary.get("status") != "completed":
        obs.append(
            {
                "dimension": "health",
                "flag": "incomplete",
                "message": f"run status = {summary.get('status')} (not completed).",
                "verify_hint": "Read run.log; some metrics may be partial.",
            }
        )

    backend = dims.get("backend") or {}
    if "unavailable" not in backend:
        atomic = backend.get("atomic_pipeline") or {}
        reasons = atomic.get("outcome_reasons") or {}
        gaps = reasons.get("completed_with_extraction_gaps", 0)
        if gaps:
            obs.append(
                {
                    "dimension": "backend",
                    "flag": "extraction_gaps",
                    "message": (
                        f"atomic extraction completed_with_extraction_gaps "
                        f"in {gaps}/{atomic.get('commit_count', 0)} commits."
                    ),
                    "verify_hint": "Read backend_logs atomic_pipeline_completed outcome_reason and which macro stage failed/aborted; correlate with memory_missing failures in D4.",
                }
            )
        provider = atomic.get("provider_diagnostics_total") or {}
        extraction_calls = provider.get("llm_atom_extraction_calls", 0)
        repair_calls = provider.get("llm_atom_extraction_repair_calls", 0)
        if extraction_calls and repair_calls / extraction_calls > 0.3:
            obs.append(
                {
                    "dimension": "backend",
                    "flag": "high_repair",
                    "message": (
                        f"extraction repair calls = {repair_calls:.0f} "
                        f"vs extraction calls = {extraction_calls:.0f} "
                        f"({repair_calls / extraction_calls:.0%})."
                    ),
                    "verify_hint": "Repair re-parses failed extractions and costs extra LLM tokens; check extraction prompt quality (R7).",
                }
            )
        logical_texts = provider.get("embedding_logical_texts", 0)
        cache_hits = provider.get("embedding_cache_hit_texts", 0)
        if logical_texts and cache_hits / logical_texts < 0.7:
            obs.append(
                {
                    "dimension": "backend",
                    "flag": "low_embedding_cache",
                    "message": (
                        f"embedding cache hit rate = {cache_hits / logical_texts:.0%} "
                        f"({cache_hits:.0f}/{logical_texts:.0f} texts)."
                    ),
                    "verify_hint": "Low dedup/cache reuse increases embedding provider calls and injection latency.",
                }
            )
        diag = backend.get("index_diagnostics") or {}
        missing = diag.get("records_missing_user_id")
        records = diag.get("records_indexed")
        if missing and records and missing / records > 0.5:
            obs.append(
                {
                    "dimension": "backend",
                    "flag": "missing_user_id",
                    "message": (
                        f"{missing}/{records} log records missing user_id "
                        f"({missing / records:.0%})."
                    ),
                    "verify_hint": "Check identity isolation / log attribution for the tenant; high share may indicate records from other tenants leaking into this run's log window.",
                }
            )
    return obs


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def analyze_folder(folder: Path) -> dict:
    common = extract_common(folder)
    dims = {
        "tokens": extract_tokens(folder, common),
        "retrieval_latency": extract_retrieval_latency(folder, common),
        "injection_latency": extract_injection_latency(folder, common),
        "retrieval_precision": extract_retrieval_precision(folder, common),
        "memory_count": extract_memory_count(folder, common),
        "quality": extract_quality(folder, common),
        "health": extract_health(folder, common),
        "backend": extract_backend(folder, common),
    }
    observations = build_observations(folder, common, dims)
    artifacts = sorted(p.name for p in folder.iterdir() if p.is_file())
    return {
        "folder": str(folder),
        "type": detect_type(folder),
        "common": common,
        "dimensions": dims,
        "observations": observations,
        "artifacts_present": artifacts,
    }


def latest_run(path: Path) -> Path:
    """If path looks like a results-parent, return its newest run subdir."""
    if has(path / "summary.json"):
        return path
    candidates = [p for p in path.iterdir() if p.is_dir() and has(p / "summary.json")]
    if not candidates:
        raise SystemExit(f"no result run found under {path}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def render_report(results: list[dict]) -> str:
    lines = []
    for r in results:
        c = r["common"]
        d = r["dimensions"]
        lines.append(f"## {r['folder']}  (type={r['type']}, backend={c.get('backend')}, plugin={c.get('plugin')})")
        lines.append("")
        lines.append("| 维度 | 关键值 |")
        lines.append("|---|---|")
        lines.append(f"| benchmark | {c.get('benchmark')} |")
        lines.append(f"| status | {c.get('status')} |")
        acc = (d.get("retrieval_precision") or {}).get("accuracy")
        lines.append(f"| accuracy | {acc if acc is None else f'{acc*100:.2f}%'} |")
        qs = (d.get("retrieval_precision") or {}).get("avg_quality_score")
        lines.append(f"| quality | {qs} |")
        tpc = (d.get("tokens") or {}).get("tokens_per_correct")
        lines.append(f"| tokens_per_correct | {tpc} |")
        rl = (d.get("retrieval_latency") or {}).get("per_question_s") or (d.get("retrieval_latency") or {}).get("per_round_s")
        lines.append(f"| retrieval latency avg/p95 | {rl.get('avg') if rl else None} / {rl.get('p95') if rl else None} s |")
        lines.append("")
        lines.append("### observations")
        for o in r["observations"]:
            lines.append(f"- [{o['flag']}] ({o['dimension']}) {o['message']}")
        lines.append("")
        for name, dim in d.items():
            if isinstance(dim, dict) and dim and "unavailable" not in dim:
                lines.append(f"### {name}")
                lines.append("```json")
                lines.append(json.dumps(dim, ensure_ascii=False, indent=1))
                lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", nargs="+", help="result dir(s), or results-parent with --latest")
    parser.add_argument("--latest", action="store_true", help="treat each arg as a results-parent, use newest run")
    parser.add_argument("--report", action="store_true", help="render a markdown report instead of JSON")
    parser.add_argument("--out", help="write output to file instead of stdout")
    args = parser.parse_args()

    paths = [Path(p) for p in args.folders]
    paths = [latest_run(p) if args.latest else p for p in paths]
    results = [analyze_folder(p) for p in paths]

    if args.report:
        out = render_report(results)
    else:
        out = json.dumps(results[0] if len(results) == 1 else {"runs": results}, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(args.out)
    else:
        print(out)


if __name__ == "__main__":
    main()
