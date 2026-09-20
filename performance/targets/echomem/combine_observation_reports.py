"""Combine persisted M1/M2/M3 observation summaries into one canonical report.

This command only reads completed observation artifacts. It never sends HTTP
requests and never changes the measured denominators. Separate metric runs are
kept visibly separate in the generated report, while their scenario timings and
metric-specific evidence are shown in one navigable page.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from performance.targets.echomem.acceptance.observation import write_observation_report


def _read_summary(path: Path) -> dict[str, Any]:
    payload = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict):
        raise ValueError(f"invalid observation summary: {path}")
    return payload


def _source_timing(label: str, summary: dict[str, Any]) -> dict[str, Any]:
    timing = summary.get("timing_evidence") or {}
    totals = timing.get("timing_totals") or {}
    return {
        "label": label,
        "path": summary.get("_source_path"),
        "planned_load_s": totals.get("planned_load_s") or 0,
        "actual_elapsed_s": totals.get("actual_elapsed_s") or 0,
        "drain_s": totals.get("drain_s") or 0,
        "scenario_count": totals.get("scenario_count") or len(timing.get("scenario_timings") or []),
        "scenarios": [
            {**row, "metric_source": label}
            for row in timing.get("scenario_timings") or []
        ],
    }


def _merge_m1(summaries: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    base = copy.deepcopy(summaries[0][1]["metrics"].get("M1") or {})
    levels: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen_levels: set[tuple[Any, ...]] = set()
    seen_rows: set[Any] = set()
    for label, summary in summaries:
        metric = summary["metrics"].get("M1") or {}
        for level in metric.get("levels") or []:
            key = tuple(level.get(name) for name in ("topology", "target_concurrency", "hot_users", "load_profile"))
            if key not in seen_levels:
                levels.append({**level, "metric_source": label})
                seen_levels.add(key)
        for row in metric.get("concurrency_rows") or []:
            key = row.get("target_concurrency")
            if key not in seen_rows:
                rows.append({**row, "metric_source": label})
                seen_rows.add(key)
    rows.sort(key=lambda row: (row.get("target_concurrency") is None, row.get("target_concurrency") or 0))
    levels.sort(key=lambda row: (row.get("target_concurrency") is None, row.get("target_concurrency") or 0))
    by_target = {row.get("target_concurrency"): row for row in rows}
    row1, row64 = by_target.get(1), by_target.get(64)
    p95_1 = row1.get("memory_profile_p95_s") if row1 else None
    p95_64 = row64.get("memory_profile_p95_s") if row64 else None
    base.update({
        "status": "MEASURED" if levels else "BLOCKED",
        "levels": levels,
        "concurrency_rows": rows,
        "highest_measured_hot_users": max((int(row.get("hot_users") or 0) for row in levels), default=None),
        "required_concurrency": max((int(summary["metrics"].get("M1", {}).get("required_concurrency") or 0)
                                      for _, summary in summaries), default=None) or None,
        "peak_inflight_requests": max((int(summary["metrics"].get("M1", {}).get("peak_inflight_requests") or 0)
                                        for _, summary in summaries), default=0),
        "expected_windows": sum(int(summary["metrics"].get("M1", {}).get("expected_windows") or 0)
                                 for _, summary in summaries),
        "measured_windows": len(levels),
        "memory_profile_comparison": {
            "baseline_concurrency": 1 if row1 else None,
            "comparison_concurrency": 64 if row64 else None,
            "p95_1_s": p95_1,
            "p95_64_s": p95_64,
            "p95_amplification": (
                p95_64 / p95_1
                if p95_1 and p95_64 is not None
                else None
            ),
            "comparison_ready": bool(p95_1 is not None and p95_64 is not None),
            "reason": "跨运行合并后的 C=1/C=64 memory_profile 样本"
                      if p95_1 is not None and p95_64 is not None else "缺少 C=1 或 C=64 档位的阶段样本",
        },
    })
    return base


def _merge_timing(summaries: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sources = [_source_timing(label, summary) for label, summary in summaries]
    scenario_timings = [row for source in sources for row in source["scenarios"]]
    preferred = next((summary for label, summary in summaries if label.startswith("M3")), summaries[-1][1])
    base_timing = copy.deepcopy(preferred.get("timing_evidence") or {})
    totals = {
        "scenario_count": sum(int(source["scenario_count"] or 0) for source in sources),
        "planned_load_s": sum(float(source["planned_load_s"] or 0) for source in sources),
        "actual_elapsed_s": sum(float(source["actual_elapsed_s"] or 0) for source in sources),
        "drain_s": sum(float(source["drain_s"] or 0) for source in sources),
        "note": "M1/M2/M3 来自独立运行目录；实际耗时为各场景实际耗时之和，不包含运行间隔。",
    }
    base_timing["timing_totals"] = totals
    base_timing["scenario_timings"] = scenario_timings
    base_timing["combined_sources"] = sources
    base_timing["trace_correlation"] = {
        "status": "PARTIAL" if any((summary.get("timing_evidence") or {}).get("trace_correlation", {}).get("status") == "PARTIAL"
                                     for _, summary in summaries) else "COLLECTED",
        "eligible_requests": sum(int(((summary.get("timing_evidence") or {}).get("trace_correlation") or {}).get("eligible_requests") or 0)
                                  for _, summary in summaries),
        "requests_with_trace": sum(int(((summary.get("timing_evidence") or {}).get("trace_correlation") or {}).get("requests_with_trace") or 0)
                                    for _, summary in summaries),
        "requests_linked_to_internal_stage": sum(int(((summary.get("timing_evidence") or {}).get("trace_correlation") or {}).get("requests_linked_to_internal_stage") or 0)
                                                  for _, summary in summaries),
        "requests_missing_trace": sum(int(((summary.get("timing_evidence") or {}).get("trace_correlation") or {}).get("requests_missing_trace") or 0)
                                       for _, summary in summaries),
        "traced_without_stage_log": sum(int(((summary.get("timing_evidence") or {}).get("trace_correlation") or {}).get("traced_without_stage_log") or 0)
                                         for _, summary in summaries),
    }
    return base_timing, sources


def combine(sources: dict[str, Path], output: Path) -> dict[str, Any]:
    loaded: list[tuple[str, dict[str, Any]]] = []
    for label, path in sources.items():
        summary = _read_summary(path)
        summary["_source_path"] = str(path)
        loaded.append((label, summary))
    if not loaded:
        raise ValueError("at least one source is required")
    model_names = {
        (row.get("kind"), row.get("model"))
        for _, summary in loaded
        for row in (summary.get("model_preflight") or {}).get("engines") or []
        if row.get("kind") in {"llm", "embedding"}
    }
    if len({name for _, name in model_names}) > 2:
        raise ValueError("source runs use different model identities")
    metrics: dict[str, Any] = {}
    m1_sources = [(label, summary) for label, summary in loaded if "M1" in summary.get("metrics", {})]
    if m1_sources:
        metrics["M1"] = _merge_m1(m1_sources)
    for code in ("M2", "M3"):
        source = next((summary for label, summary in loaded
                       if code in (summary.get("selected_metrics") or [])
                       and code in summary.get("metrics", {})), None)
        if source is not None:
            metrics[code] = copy.deepcopy(source["metrics"][code])
    timing, timing_sources = _merge_timing(loaded)
    base_summary = next((summary for label, summary in loaded if label.startswith("M3")), loaded[-1][1])
    base = copy.deepcopy(base_summary)
    base.update({
        "schema_version": 2,
        "instance_profile": "combined-M1-M2-M3",
        "selected_metrics": [code for code in ("M1", "M2", "M3") if code in metrics],
        "metrics": metrics,
        "status": "PARTIAL" if any(metric.get("status") in {"PARTIAL", "BLOCKED"} for metric in metrics.values()) else "MEASURED",
        "sampling_mode": "combined-independent-formal-runs",
        "timing_evidence": timing,
        "combined_evidence": {
            "status": "CROSS_RUN_COMBINED",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": timing_sources,
            "active_elapsed_s": timing["timing_totals"]["actual_elapsed_s"],
            "planned_load_s": timing["timing_totals"]["planned_load_s"],
            "drain_s": timing["timing_totals"]["drain_s"],
            "note": "不同指标独立运行后合并展示；不能解读为一次连续压测。",
        },
    })
    # The base summary comes from M3 so that its stage evidence is retained,
    # but these two fields must describe the combined report rather than the
    # last source run alone.
    categories = copy.deepcopy(base.get("issue_categories") or [])
    for item in categories:
        category = item.get("category")
        if category == "Admission/调度":
            item["evidence"] = {
                "M1": (metrics.get("M1") or {}).get("status"),
                "M2": (metrics.get("M2") or {}).get("status"),
                "M3": (metrics.get("M3") or {}).get("status"),
            }
        elif category == "Search/Recall":
            item["evidence"] = {
                "M1_windows": (metrics.get("M1") or {}).get("measured_windows"),
                "M2_windows": len((metrics.get("M2") or {}).get("fairness_windows") or []),
                "M3_windows": len((metrics.get("M3") or {}).get("windows") or []),
            }
        elif category == "测试平台/部署":
            item["note"] = "跨运行资源和模型预检见各来源报告；本页仅合并测量结果。"
    base["issue_categories"] = categories
    setup_rows = [summary.get("setup_evidence") or {} for _, summary in loaded]
    base["setup_evidence"] = {
        "seed_status": "SEE_SOURCE_RUNS",
        "seed_contract": "M1/M2/M3 各自使用来源运行中记录的固定样本；本页不重新注入或重跑。",
        "source_run_count": len(loaded),
        "source_runs_with_completed_seed": sum(row.get("seed_status") == "completed" for row in setup_rows),
        "load_cases_completed": sum(int(row.get("load_cases_completed") or 0) for row in setup_rows),
        "note": "租户、用户、会话和样本准备证据按来源运行保留，避免把单次 M3 的 4 个场景误报为全量场景。",
    }
    base["platform_provenance"] = {
        **(base.get("platform_provenance") or {}),
        "combined_source_commits": [
            {"label": label, "git_commit": (summary.get("platform_provenance") or {}).get("git_commit"),
             "python_source_sha256": (summary.get("platform_provenance") or {}).get("python_source_sha256")}
            for label, summary in loaded
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "combined-sources.json").write_text(json.dumps(timing_sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_observation_report(base, output / "report.html")
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1", type=Path, required=True)
    parser.add_argument("--m1-extra", type=Path)
    parser.add_argument("--m2", type=Path, required=True)
    parser.add_argument("--m3", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    sources = {"M1 1/8/16/64": args.m1, "M2 4/8租户": args.m2, "M3 基线/洪泛": args.m3}
    if args.m1_extra:
        sources["M1 C=128"] = args.m1_extra
    combine(sources, args.out)
    print(args.out / "report.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
