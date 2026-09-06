#!/usr/bin/env python3
"""Build an auditable HTML report for a PR29 six-metric run.

The report deliberately distinguishes:

* a scenario that was configured;
* a scenario that actually produced samples; and
* a metric that has enough evidence for PASS.

It never upgrades missing evidence to PASS and keeps links to the raw JSON/CSV
files next to the generated report.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

# Allow direct execution from the repository's ``scripts/`` directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from performance.scheduler_acceptance import evaluate as evaluate_scheduler_acceptance
from performance.objective_suite import _probe_plan, platform_objective_coverage


OBJECTIVES = (
    ("O1", "最大 DAU / 热用户量"),
    ("O2", "单租户故障隔离"),
    ("O3", "多租户公平性"),
    ("O4", "Search 优先级"),
    ("O5", "202 Commit 崩溃恢复"),
    ("O6", "四元组可观测性"),
)


def load(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def first_existing(*paths: Path) -> Path | None:
    """Return the first existing artifact path, keeping old result layouts usable."""
    for path in paths:
        if path.is_file():
            return path
    return None


def load_artifact(*paths: Path) -> tuple[dict[str, Any], Path | None]:
    path = first_existing(*paths)
    return (load(path), path) if path else ({}, None)


def load_formal_artifacts(
    formal: Path,
    extra_roots: tuple[Path, ...] = (),
) -> dict[str, dict[str, Any]]:
    """Load post-suite probes from current, legacy, or supplemental layouts.

    A six-metric run is often assembled from a long formal matrix plus a
    shorter recovery/contract supplement.  Both layouts are kept separate on
    disk, so the report must accept more than one evidence root without
    silently preferring a stale artifact.
    """
    roots: list[Path] = []
    for root in (formal, *extra_roots):
        root = root.expanduser().resolve()
        for candidate in (
            root,
            root.parent,
            root / "probes",
            root.parent / "probes",
            root / "formal",
            root / "formal" / "probes",
        ):
            if candidate not in roots:
                roots.append(candidate)
    probe_dirs = tuple(roots)
    artifacts: dict[str, dict[str, Any]] = {}
    for key, filename in (
        ("capability_probe", "capability-probe.json"),
        ("commit_recovery", "commit-recovery.json"),
        ("fault_suite", "fault-suite.json"),
        ("fault_isolation", "fault-isolation.json"),
        ("tenant_observability", "tenant-observability.json"),
        ("blackbox_contract_probe", "blackbox-contract-probe.json"),
        ("concurrent_commit", "concurrent-commit.json"),
        ("missing_cases", "missing-cases.json"),
        ("limit_failure_sweep", "limit-failure-sweep-summary.json"),
    ):
        candidates = [probe_dir / filename for probe_dir in probe_dirs]
        if key == "limit_failure_sweep":
            candidates.extend(
                probe_dir / "limit-failure-sweep" / "summary.json"
                for probe_dir in probe_dirs
            )
        if key == "fault_suite":
            candidates.extend(
                probe_dir / "fault-suite" / filename
                for probe_dir in probe_dirs
            )
        artifact, _artifact_path = load_artifact(*candidates)
        if artifact:
            artifacts[key] = artifact
    return artifacts


def find_run_artifact(formal: Path, scenario: str, filename: str) -> Path | None:
    """Find a normalized or timestamped artifact for one scenario."""
    case_root = formal / str(scenario) / "repeat-01" / "server-observe"
    direct = case_root / filename
    if direct.is_file():
        return direct
    matches = sorted(case_root.glob(f"run/*/{filename}"))
    return matches[-1] if matches else None


def resolve_run_dir(formal: Path, run: dict[str, Any], fallback: str) -> Path:
    """Resolve a run's real output directory.

    The merged matrix stores absolute ``output_dir`` values because individual
    cases may come from different runs.  Falling back to ``formal/<scenario>``
    is retained for old manifests that did not persist that field.
    """
    configured = str(run.get("output_dir") or run.get("run_dir") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            # Newer formal runs persist the case directory in ``output_dir``
            # and put timestamped runner files below ``run/<id>``.
            nested = sorted(
                child for child in candidate.glob("run/*")
                if child.is_dir()
            )
            if nested and not (candidate / "summary.json").is_file():
                return nested[-1].resolve()
            return candidate.resolve()
    key = str(run.get("scenario_key") or fallback).strip()
    source = str(run.get("source_scenario") or fallback).strip()
    candidates = (
        formal / key / "repeat-01" / "server-observe",
        formal / source / "repeat-01" / "server-observe",
        formal / fallback / "repeat-01" / "server-observe",
    )
    for candidate in candidates:
        if candidate.is_dir():
            nested = sorted(
                child for child in candidate.glob("run/*")
                if child.is_dir()
            )
            if nested and not (candidate / "summary.json").is_file():
                return nested[-1].resolve()
            return candidate.resolve()
    return candidates[0].resolve()


def first_runner_config(formal: Path) -> dict[str, Any]:
    """Read one non-secret runner config for report context."""
    matches = sorted(formal.glob("*/repeat-01/server-observe/run/*/config.json"))
    return load(matches[0]) if matches else {}


def esc(value: Any) -> str:
    return html.escape("-" if value in (None, "") else str(value))


def declared_observability_expectations(suite: dict[str, Any]) -> dict[str, list[str]]:
    """Resolve the PR421 observability contract for old and new manifests."""
    for key in ("observability_expectations", "observability"):
        declared = suite.get(key)
        if isinstance(declared, dict) and (
            declared.get("lanes") or declared.get("fanout_engines")
        ):
            return {
                "lanes": [
                    str(item).strip()
                    for item in declared.get("lanes") or []
                    if str(item).strip()
                ],
                "fanout_engines": [
                    str(item).strip()
                    for item in declared.get("fanout_engines") or []
                    if str(item).strip()
                ],
            }

    targets = suite.get("acceptance_targets") or {}
    if not isinstance(targets, dict):
        return {"lanes": [], "fanout_engines": []}
    # ``fanout_metric_families`` describes metric *families*, not engine label
    # values.  Do not invent labels such as ``recall`` or ``commit`` here:
    # actual engine labels must come from the effective EchoMem config or from
    # observed Prometheus samples.
    fanout_engines = targets.get("fanout_engines") or []
    return {
        "lanes": [
            str(item).strip()
            for item in targets.get("lane_values") or []
            if str(item).strip()
        ],
        "fanout_engines": [
            str(item).strip()
            for item in fanout_engines or []
            if str(item).strip()
        ],
    }


def _config_path_candidates(
    *,
    explicit: Path | None,
    root: Path,
    formal: Path,
    suite: dict[str, Any],
    recall_run: dict[str, Any],
) -> list[Path]:
    """Build non-secret candidates for the effective EchoMem config."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit.expanduser())
    env_path = os.environ.get("ECHOMEM_CONFIG_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    for value in (
        suite.get("preflight_config"),
        formal and load(formal / "suite.json").get("preflight_config"),
    ):
        if value:
            candidates.append(Path(str(value)).expanduser())
    candidates.extend(
        (
            root / "echomem-config-real-4u8g.json",
            root / "config.json",
            formal / "echomem-config-real-4u8g.json",
            formal / "config.json",
        )
    )
    if recall_run.get("config_path"):
        candidates.append(Path(str(recall_run["config_path"])))
    result: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in result and resolved.is_file():
            result.append(resolved)
    return result


def load_effective_echomem_config(
    *,
    explicit: Path | None,
    root: Path,
    formal: Path,
    suite: dict[str, Any],
    recall_run: dict[str, Any],
) -> tuple[dict[str, Any], Path | None]:
    """Load the actual EchoMem config when it is available.

    Runner ``config.json`` files intentionally contain load-generator options,
    not EchoMem's runtime module switches.  Prefer the preflight/runtime config
    and return its path so the report can prove which configuration was used.
    """
    for candidate in _config_path_candidates(
        explicit=explicit,
        root=root,
        formal=formal,
        suite=suite,
        recall_run=recall_run,
    ):
        payload = load(candidate)
        if isinstance(payload.get("model"), dict) or isinstance(
            payload.get("engine"), dict
        ):
            return payload, candidate
    return {}, None


def effective_observability_expectations(
    config: dict[str, Any],
    fallback: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Derive expected lane/engine labels from the effective runtime config.

    ``NOT_APPLICABLE`` belongs in the report for disabled optional stages; it
    must not become a false ``MISSING_EVIDENCE`` result.  Commit and the main
    recall engine are expected whenever the corresponding top-level pipeline
    exists.  Intent LLM, query embedding, rerank, and fan-out engines are
    driven by their actual config switches.
    """
    if not config:
        return {
            "lanes": list(fallback.get("lanes") or []),
            "fanout_engines": list(fallback.get("fanout_engines") or []),
        }

    lanes: list[str] = []
    commit_pipeline = config.get("commit_pipeline")
    if isinstance(commit_pipeline, dict) or isinstance(config.get("session"), dict):
        lanes.append("commit")

    recall = config.get("recall")
    recall = recall if isinstance(recall, dict) else {}
    layers = {
        str(item).strip().lower()
        for item in recall.get("intent_recognition_layers") or []
        if str(item).strip()
    }
    # Search's orchestration boundary is always part of a configured recall
    # service, even when individual optional stages are disabled.
    if recall or isinstance(config.get("index"), dict):
        lanes.append("recall_engine")
    if "llm" in layers or isinstance((recall.get("model") or {}).get("intent_llm"), dict):
        lanes.append("recall_intent_llm")
    if "semantic" in layers or isinstance(config.get("index"), dict):
        lanes.append("recall_query_embedding")
    rerank = (recall.get("model") or {}).get("rerank")
    if isinstance(rerank, dict) and bool(rerank.get("enabled")):
        lanes.append("recall_rerank")

    engine_config = config.get("engine")
    engine_config = engine_config if isinstance(engine_config, dict) else {}
    enabled_engines = [
        str(item).strip()
        for item in engine_config.get("enabled") or []
        if str(item).strip()
    ]
    recall_enabled = engine_config.get("recall_enabled")
    recall_enabled = recall_enabled if isinstance(recall_enabled, dict) else {}
    fanout_engines = [
        engine
        for engine in enabled_engines
        if bool(recall_enabled.get(engine, True))
    ]
    # Keep deterministic output and avoid duplicate labels from malformed
    # configs.  The report separately lists disabled configured engines.
    return {
        "lanes": list(dict.fromkeys(lanes)),
        "fanout_engines": list(dict.fromkeys(fanout_engines)),
    }


def disabled_observability_components(config: dict[str, Any]) -> list[str]:
    """Return optional recall components explicitly disabled by config."""
    if not config:
        return []
    disabled: list[str] = []
    recall = config.get("recall")
    recall = recall if isinstance(recall, dict) else {}
    rerank = (recall.get("model") or {}).get("rerank")
    if isinstance(rerank, dict) and not bool(rerank.get("enabled")):
        disabled.append("recall_rerank")
    engine = config.get("engine")
    engine = engine if isinstance(engine, dict) else {}
    recall_enabled = engine.get("recall_enabled")
    if isinstance(recall_enabled, dict):
        for engine_name, enabled in recall_enabled.items():
            if enabled is False:
                disabled.append(str(engine_name))
    return disabled


def link(path: Path, report: Path, label: str) -> str:
    if not path.is_file():
        return ""
    return (
        f"<li><a href='{html.escape(os.path.relpath(path, report.parent))}'>"
        f"{esc(label)}</a></li>"
    )


def status(value: Any) -> str:
    text = str(value or "INCONCLUSIVE").upper()
    css = {
        "PASS": "pass",
        "FAIL": "fail",
        "INCONCLUSIVE": "warn",
        "BLOCKED": "warn",
    }.get(text, "neutral")
    return f"<span class='badge {css}'>{esc(text)}</span>"


def metric_bar(value: float | None, maximum: float = 1.0, tone: str = "teal") -> str:
    if value is None or maximum <= 0:
        width = 0
    else:
        width = max(0.0, min(100.0, value / maximum * 100.0))
    return f"<span class='meter {tone}'><i style='width:{width:.1f}%'></i></span>"


def json_text(value: Any) -> str:
    if value in (None, "", {}, []):
        return "-"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "ok", "pass"}


def _percentile(values: list[float], fraction: float) -> float | None:
    """Linear-interpolated percentile used only when summary stats are absent."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _resolve_result_dir(path: Path) -> Path:
    """Accept a run directory or a wrapper directory containing one run."""
    candidate = path.expanduser().resolve()
    if (candidate / "summary.json").is_file():
        return candidate
    children = sorted(
        item.parent
        for item in candidate.glob("*/summary.json")
        if item.is_file()
    )
    return children[0] if len(children) == 1 else candidate


def load_recall_run(path: Path | None) -> dict[str, Any]:
    """Load a real-memory Search supplement without changing six-metric gates.

    ``run_stress`` writes authoritative aggregate values to ``summary.json`` and
    per-request evidence to ``requests.csv``.  The report uses the aggregate
    values when present and falls back to the CSV only for older result layouts.
    """
    if path is None:
        return {"available": False}

    run_dir = _resolve_result_dir(path)
    summary_path = run_dir / "summary.json"
    config_path = run_dir / "config.json"
    requests_path = run_dir / "requests.csv"
    metrics_path = run_dir / "metrics_samples.csv"
    report_path = run_dir / "report.html"
    summary = load(summary_path)
    config = load(config_path)
    rows: list[dict[str, str]] = []
    if requests_path.is_file():
        try:
            with requests_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            rows = []

    read_rows = [
        row
        for row in rows
        if str(row.get("op") or "").strip().lower() in {"read", "search"}
    ]
    ok_rows = [
        row
        for row in read_rows
        if str(row.get("status") or "").strip().lower() in {"ok", "completed", "200"}
        and _as_float(row.get("stage_ms")) is not None
    ]
    durations = [
        value
        for value in (_as_float(row.get("stage_ms")) for row in ok_rows)
        if value is not None
    ]
    quality = summary.get("search_quality") or {}
    gated = quality.get("gated_read_stats") or {}
    if not gated and durations:
        gated = {
            "count": len(durations),
            "avg_ms": statistics.mean(durations),
            "p50_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
            "p99_ms": _percentile(durations, 0.99),
            "max_ms": max(durations),
            "min_ms": min(durations),
        }

    def count_rows(predicate) -> int:
        return sum(1 for row in read_rows if predicate(row))

    real_recall_count = count_rows(
        lambda row: _as_bool(row.get("real_recall")) or _as_bool(row.get("recall_matched"))
    )
    if real_recall_count == 0 and quality.get("recall_total") is not None:
        real_recall_count = int(quality.get("recall_total") or 0)
    degraded_count = count_rows(lambda row: _as_bool(row.get("degraded")))
    if quality.get("degraded_total") is not None:
        degraded_count = int(quality.get("degraded_total") or 0)

    query_kind_counts: dict[str, int] = {}
    for row in read_rows:
        kind = str(row.get("query_kind") or "unknown")
        query_kind_counts[kind] = query_kind_counts.get(kind, 0) + 1
    if not query_kind_counts:
        query_kind_counts = {
            str(kind): int(item.get("count") or 0)
            for kind, item in (quality.get("query_kind_stats") or {}).items()
            if isinstance(item, dict)
        }

    query_groups: dict[str, list[dict[str, str]]] = {}
    for row in ok_rows:
        query = str(row.get("query") or "(未记录查询)")
        query_groups.setdefault(query, []).append(row)
    query_rows: list[dict[str, Any]] = []
    for query, group in query_groups.items():
        values = [
            value
            for value in (_as_float(row.get("stage_ms")) for row in group)
            if value is not None
        ]
        query_rows.append(
            {
                "query": query,
                "count": len(group),
                "avg_ms": statistics.mean(values) if values else None,
                "p95_ms": _percentile(values, 0.95),
                "hit_count": max(
                    (
                        _as_float(row.get("hit_count"))
                        for row in group
                        if _as_float(row.get("hit_count")) is not None
                    ),
                    default=None,
                ),
                "degraded": sum(1 for row in group if _as_bool(row.get("degraded"))),
            }
        )
    query_rows.sort(key=lambda item: str(item["query"]))

    stats = {
        "count": int(gated.get("count") or len(durations)),
        "avg_ms": _as_float(gated.get("avg_ms")),
        "p50_ms": _as_float(gated.get("p50_ms")),
        "p95_ms": _as_float(gated.get("p95_ms")),
        "p99_ms": _as_float(gated.get("p99_ms")),
        "max_ms": _as_float(gated.get("max_ms")),
        "min_ms": _as_float(gated.get("min_ms")),
        "real_recall_count": real_recall_count,
        "degraded_count": degraded_count,
        "error_count": int(quality.get("total") or len(read_rows)) - int(
            quality.get("recall_total") or len(ok_rows)
        ),
        "query_kind_counts": query_kind_counts,
        "query_rows": query_rows,
    }
    return {
        "available": summary_path.is_file(),
        "run_dir": run_dir,
        "summary_path": summary_path,
        "config_path": config_path,
        "requests_path": requests_path,
        "metrics_path": metrics_path,
        "report_path": report_path,
        "summary": summary,
        "config": config,
        "stats": stats,
    }


def _fmt_number(value: Any, digits: int = 3) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        type=Path,
        help="objective-suite result directory, or a direct formal-suite result directory",
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument(
        "--recall-run",
        type=Path,
        default=None,
        help="optional real-memory Search run directory to attach as a supplement",
    )
    parser.add_argument(
        "--supplemental-dir",
        type=Path,
        default=None,
        help="optional directory containing recovery/contract/capability probe artifacts",
    )
    parser.add_argument(
        "--echomem-config",
        type=Path,
        default=None,
        help="actual EchoMem runtime config.json used by the run (API keys are never rendered)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    output = (args.output or root / "pr29-six-metric-report.html").resolve()
    recall_run = load_recall_run(args.recall_run)
    supplemental_dir = (
        args.supplemental_dir.expanduser().resolve()
        if args.supplemental_dir
        else None
    )
    extra_roots = (supplemental_dir,) if supplemental_dir else ()
    suite = load(root / "objective-suite.json")
    direct_formal = not suite and (root / "suite.json").is_file()
    if direct_formal:
        formal = root
        formal_suite = load(formal / "suite.json")
        direct_expectations = declared_observability_expectations(formal_suite)
        formal_suite = {
            **formal_suite,
            "observability_expectations": direct_expectations,
        }
        # Do not silently lose recovery/capability evidence just because the
        # caller points at the formal directory.  A supplemental probe run is
        # merged here as well, so the six objectives use the strongest
        # available real-HTTP evidence instead of the formal matrix alone.
        formal_suite.update(load_formal_artifacts(formal, extra_roots))
        acceptance = evaluate_scheduler_acceptance(
            formal_suite,
            capability=formal_suite.get("capability_probe"),
            recovery=formal_suite.get("commit_recovery"),
            fault=(
                formal_suite.get("fault_suite")
                or formal_suite.get("fault_isolation")
            ),
            tenant_observability=formal_suite.get("tenant_observability"),
        )
        objective_name_to_id = {
            "DAU / 最大热用户容量": "O1",
            "单租户故障隔离": "O2",
            "Commit/Search 公平性 Jain": "O3",
            "Search 优先于 Commit": "O4",
            "Commit kill-9 恢复与重放": "O5",
            "分层/分租户调度可观测性": "O6",
        }
        objectives = {
            objective_name_to_id.get(str(item.get("name")), str(item.get("name"))): {
                **item,
                "id": objective_name_to_id.get(
                    str(item.get("name")), str(item.get("name"))
                ),
            }
            for item in acceptance.get("checks") or []
            if isinstance(item, dict)
        }
        suite = formal_suite
        profile = {
            "name": formal_suite.get("instance_profile") or "4U8G",
            "objectives": [],
            "platform_objective_coverage": [],
            "observability": direct_expectations,
        }
        formal_manifest = formal_suite
    else:
        profile = (suite.get("profiles") or [{}])[0]
        objectives = {
            str(item.get("id")): item
            for item in profile.get("objectives") or []
            if isinstance(item, dict)
        }
        formal = root / "4U8G" / "formal"
        formal_manifest = load(formal / "suite.json")
        probe_artifacts = load_formal_artifacts(formal, extra_roots)
        if probe_artifacts:
            formal_manifest = {
                **formal_manifest,
                **probe_artifacts,
                # Re-evaluation must use the same declared coverage contract
                # as the objective-suite run, otherwise report regeneration
                # can silently downgrade or change O6.
                "fairness_expectations": profile.get("fairness_expectations", {}),
                "observability_expectations": profile.get("observability", {}),
            }
            acceptance = evaluate_scheduler_acceptance(
                formal_manifest,
                capability=formal_manifest.get("capability_probe"),
                recovery=formal_manifest.get("commit_recovery"),
                fault=formal_manifest.get("fault_suite"),
                tenant_observability=formal_manifest.get("tenant_observability"),
            )
            objective_name_to_id = {
                "DAU / 最大热用户容量": "O1",
                "单租户故障隔离": "O2",
                "Commit/Search 公平性 Jain": "O3",
                "Search 优先于 Commit": "O4",
                "Commit kill-9 恢复与重放": "O5",
                "分层/分租户调度可观测性": "O6",
            }
            objectives = {
                objective_name_to_id.get(str(item.get("name")), str(item.get("name"))): {
                    **item,
                    "id": objective_name_to_id.get(
                        str(item.get("name")), str(item.get("name"))
                    ),
                }
                for item in acceptance.get("checks") or []
                if isinstance(item, dict)
            }
    # Re-evaluate with the effective EchoMem config.  The formal suite's
    # static acceptance target is intentionally broad for compatibility, but
    # disabled optional stages must not be reported as missing evidence.
    fallback_observability = profile.get("observability")
    if not isinstance(fallback_observability, dict):
        fallback_observability = declared_observability_expectations(formal_manifest)
    effective_config, effective_config_path = load_effective_echomem_config(
        explicit=args.echomem_config,
        root=root,
        formal=formal,
        suite=formal_manifest,
        recall_run=recall_run,
    )
    effective_expectations = effective_observability_expectations(
        effective_config,
        fallback_observability,
    )
    formal_manifest = {
        **formal_manifest,
        "observability_expectations": effective_expectations,
    }
    if isinstance(profile.get("fairness_expectations"), dict):
        formal_manifest["fairness_expectations"] = profile["fairness_expectations"]
    acceptance = evaluate_scheduler_acceptance(
        formal_manifest,
        capability=formal_manifest.get("capability_probe"),
        recovery=formal_manifest.get("commit_recovery"),
        fault=formal_manifest.get("fault_suite"),
        tenant_observability=formal_manifest.get("tenant_observability"),
    )
    objective_name_to_id = {
        "DAU / 最大热用户容量": "O1",
        "单租户故障隔离": "O2",
        "Commit/Search 公平性 Jain": "O3",
        "Search 优先于 Commit": "O4",
        "Commit kill-9 恢复与重放": "O5",
        "分层/分租户调度可观测性": "O6",
    }
    objectives = {
        objective_name_to_id.get(str(item.get("name")), str(item.get("name"))): {
            **item,
            "id": objective_name_to_id.get(
                str(item.get("name")), str(item.get("name"))
            ),
        }
        for item in acceptance.get("checks") or []
        if isinstance(item, dict)
    }
    effective_disabled = disabled_observability_components(effective_config)
    platform_coverage = profile.get("platform_objective_coverage") or []
    if not platform_coverage:
        probe_plan = profile.get("probe_plan")
        if not isinstance(probe_plan, list):
            probe_plan = _probe_plan(profile)
        platform_coverage = platform_objective_coverage(
            profile,
            formal_manifest,
            probe_plan,
            profile.get("coverage") or {},
        )
    auth = formal_manifest.get("auth_preflight") or {}
    scenarios = []
    manifest_runs = [
        item for item in formal_manifest.get("runs") or []
        if isinstance(item, dict)
    ]
    for index, run_record in enumerate(manifest_runs):
        name = str(
            run_record.get("source_scenario")
            or run_record.get("scenario")
            or run_record.get("scenario_key")
            or f"run-{index + 1}"
        )
        key = str(
            run_record.get("scenario_key")
            or run_record.get("scenario")
            or name
        )
        path = resolve_run_dir(formal, run_record, key)
        summary = (
            run_record.get("summary")
            if isinstance(run_record.get("summary"), dict)
            else load(path / "summary.json")
        )
        if not summary:
            summary = load(path / "summary.json")
        metrics = summary.get("metrics") or {}
        search = metrics.get("search") or {}
        commit = metrics.get("commit") or {}
        search_latency = search.get("latency") or {}
        commit_latency = commit.get("latency") or {}
        resources = summary.get("resources") or {}
        details = summary.get("details") or {}
        activity = details.get("user_activity") or {}
        errors = summary.get("errors") or {}
        scenarios.append(
            {
                "name": name,
                "key": key,
                "plan": str(
                    run_record.get("plan_source")
                    or (key.split("__", 1)[0] if "__" in key else "")
                    or "-"
                ),
                "run_dir": str(path),
                "summary": summary,
                "status": (
                    run_record.get("status")
                    or summary.get("status")
                    or "not-run"
                ),
                "search_submitted": search.get("submitted", 0),
                "search_succeeded": search.get("succeeded", 0),
                "search_success_rate": search.get("success_rate"),
                "commit_submitted": commit.get("submitted", 0),
                "commit_completed": commit.get("completed", 0),
                "commit_success_rate": commit.get("success_rate"),
                "search_p95": search_latency.get("p95_s"),
                "commit_p95": commit_latency.get("p95_s"),
                "per_tenant": metrics.get("per_tenant") or {},
                "same_window_overlap": details.get("same_window_overlap") or {},
                "activity": activity,
                "resources": resources,
                "search_quality": details.get("search_quality") or {},
                "metric_coverage": details.get("pr421_metric_coverage") or {},
                "duration_s": (
                    run_record.get("duration_s")
                    if run_record.get("duration_s") is not None
                    else summary.get("duration_s")
                ),
                "blocked_reason": run_record.get("blocked_reason") or "",
                "error_count": (
                    summary.get("error_count")
                    if summary.get("error_count") is not None
                    else sum(
                        int(value or 0)
                        for value in errors.values()
                        if isinstance(value, (int, float))
                    )
                ),
            }
        )
    # Preserve an auditable placeholder when a manifest has configured cases
    # but no run record at all.  This is intentionally not treated as evidence.
    configured_names = [
        str(item).strip()
        for item in (formal_manifest.get("scenarios") or [])
        if str(item).strip()
    ]
    observed_keys = {str(item["key"]) for item in scenarios}
    for name in configured_names:
        if name in observed_keys:
            continue
        scenarios.append(
            {
                "name": name.split("__", 1)[-1] if "__" in name else name,
                "key": name,
                "plan": name.split("__", 1)[0] if "__" in name else "-",
                "run_dir": "",
                "summary": {},
                "status": "not-run",
                "search_submitted": 0,
                "search_succeeded": 0,
                "search_success_rate": None,
                "commit_submitted": 0,
                "commit_completed": 0,
                "commit_success_rate": None,
                "search_p95": None,
                "commit_p95": None,
                "duration_s": None,
                "blocked_reason": "manifest 中已配置，但没有运行记录",
                "error_count": 0,
            }
        )

    def scenario_artifact(name: str, filename: str) -> Path | None:
        """Find an artifact using the run's persisted output_dir first."""
        for item in scenarios:
            if str(item.get("name")) != name and str(item.get("key")) != name:
                continue
            run_dir = str(item.get("run_dir") or "").strip()
            if run_dir:
                candidate = Path(run_dir) / filename
                if candidate.is_file():
                    return candidate
        return find_run_artifact(formal, name, filename)

    scenario_metric_links = "".join(
        link(
            Path(item["run_dir"]) / "metrics_samples.csv"
            if str(item.get("run_dir") or "").strip()
            and (Path(item["run_dir"]) / "metrics_samples.csv").is_file()
            else Path("__missing__"),
            output,
            f"{item['key']} metrics_samples.csv",
        )
        for item in scenarios
        if str(item.get("run_dir") or "").strip()
        and (Path(item["run_dir"]) / "metrics_samples.csv").is_file()
    )

    cards = "".join(
        f"<article class='card'><div class='id'>{esc(ident)}</div>"
        f"<h3>{esc(title)}</h3>{status(objectives.get(ident, {}).get('status'))}"
        f"<p>{esc(objectives.get(ident, {}).get('reason'))}</p></article>"
        for ident, title in OBJECTIVES
    )
    scenario_rows = "".join(
        "<tr>"
        f"<td><b>{esc(item['key'])}</b><br><span class='muted'>{esc(item['name'])}</span></td>"
        f"<td>{esc(item['plan'])}</td><td>{status(item['status'])}</td>"
        f"<td>{esc(item['search_submitted'])}/{esc(item['search_succeeded'])}"
        f"<br><span class='muted'>{esc(item['search_success_rate'])}</span></td>"
        f"<td>{esc(item['commit_submitted'])}/{esc(item['commit_completed'])}"
        f"<br><span class='muted'>{esc(item['commit_success_rate'])}</span></td>"
        f"<td>{esc(item['search_p95'])} s"
        f"<br><span class='muted'>Commit {esc(item['commit_p95'])} s</span></td>"
        f"<td>{esc(item['duration_s'])} s</td>"
        f"<td>{esc(item['error_count'])}"
        f"<br><span class='muted'>{esc(item['blocked_reason'])}</span></td></tr>"
        for item in scenarios
    )
    probe_paths = {
        key: Path(str(value.get("path")))
        for key, value in formal_manifest.items()
        if key in {
            "capability_probe",
            "commit_recovery",
            "fault_suite",
            "fault_isolation",
            "tenant_observability",
            "blackbox_contract_probe",
            "concurrent_commit",
            "missing_cases",
            "limit_failure_sweep",
        }
        and isinstance(value, dict)
        and value.get("path")
    }
    artifact_paths = (
        (root / "objective-suite.json", "objective-suite.json"),
        (formal / "suite.json", "4U8G/formal/suite.json"),
        (formal / "acceptance.json", "4U8G/formal/acceptance.json"),
        (
            first_existing(
                root / "4U8G" / "capability-probe.json",
                root / "capability-probe.json",
                root.parent / "capability-probe.json",
                formal / "probes" / "capability-probe.json",
                probe_paths.get("capability_probe", Path("__missing__")),
            ) or root / "__missing__",
            "capability-probe.json",
        ),
        (
            first_existing(
                root / "4U8G" / "blackbox-contract-probe.json",
                root / "blackbox-contract-probe.json",
                root.parent / "blackbox-contract-probe.json",
                formal / "probes" / "blackbox-contract-probe.json",
                probe_paths.get("blackbox_contract_probe", Path("__missing__")),
            ) or root / "__missing__",
            "blackbox-contract-probe.json",
        ),
        (
            first_existing(
                root / "4U8G" / "commit-recovery.json",
                root / "commit-recovery.json",
                root.parent / "commit-recovery.json",
                formal / "probes" / "commit-recovery.json",
                probe_paths.get("commit_recovery", Path("__missing__")),
            ) or root / "__missing__",
            "commit-recovery.json",
        ),
        (
            scenario_artifact("fairness-bounded", "search_results.csv")
            or Path("__missing__"),
            "fairness Search CSV",
        ),
        (
            scenario_artifact("fairness-bounded", "commit_results.csv")
            or Path("__missing__"),
            "fairness Commit CSV",
        ),
        (
            scenario_artifact("search-priority-blackbox", "search_results.csv")
            or Path("__missing__"),
            "priority Search CSV",
        ),
        (
            scenario_artifact("search-priority-blackbox", "commit_results.csv")
            or Path("__missing__"),
            "priority Commit CSV",
        ),
    )
    for key, label in (
        ("fault_isolation", "fault-isolation.json"),
        ("tenant_observability", "tenant-observability.json"),
        ("concurrent_commit", "concurrent-commit.json"),
        ("missing_cases", "missing-cases.json"),
        ("limit_failure_sweep", "limit-failure-sweep-summary.json"),
    ):
        artifact_paths += (
            (probe_paths.get(key, Path("__missing__")), label),
        )
    if recall_run.get("available"):
        artifact_paths += (
            (recall_run["summary_path"], "real-recall summary.json"),
            (recall_run["config_path"], "real-recall config.json"),
            (recall_run["requests_path"], "real-recall requests.csv"),
            (recall_run["metrics_path"], "real-recall metrics_samples.csv"),
            (recall_run["report_path"], "real-recall report.html"),
        )
    artifacts = "".join(link(path, output, label) for path, label in artifact_paths)
    configured = formal_manifest.get("scenarios") or []
    scenario_coverage = f"{len(manifest_runs)}/{len(configured)}"
    # Prefer the effective EchoMem runtime config over the load-generator
    # config.  The latter describes the test client and can otherwise make the
    # report show the wrong model or omit the service's module switches.
    model = effective_config
    if not model:
        model = load(root / "echomem-config-real-4u8g.json")
    if not model and direct_formal:
        model = first_runner_config(formal)
    if not model and direct_formal:
        candidates = sorted(formal.glob("*/repeat-01/server-observe/config.json"))
        if candidates:
            model = load(candidates[0])
    runner_config = first_runner_config(formal)
    if not runner_config:
        for item in scenarios:
            run_dir = str(item.get("run_dir") or "").strip()
            if not run_dir:
                continue
            candidate = Path(run_dir) / "config.json"
            if candidate.is_file():
                runner_config = load(candidate)
                break
    llm = ((model.get("model") or {}).get("llm") or {}).get("model", "-")
    embedding = ((model.get("model") or {}).get("embedding") or {}).get("model", "-")
    if llm == "-" and runner_config:
        llm = str(runner_config.get("llm_model") or runner_config.get("model") or "-")
    if embedding == "-" and runner_config:
        embedding = str(runner_config.get("embedding_model") or "-")
    runtime_recall = model.get("recall") if isinstance(model, dict) else {}
    runtime_recall = runtime_recall if isinstance(runtime_recall, dict) else {}
    runtime_layers = [
        str(item).strip()
        for item in runtime_recall.get("intent_recognition_layers") or []
        if str(item).strip()
    ]
    runtime_engine = model.get("engine") if isinstance(model, dict) else {}
    runtime_engine = runtime_engine if isinstance(runtime_engine, dict) else {}
    runtime_enabled_engines = [
        str(item).strip()
        for item in runtime_engine.get("enabled") or []
        if str(item).strip()
    ]
    runtime_disabled = effective_disabled
    runtime_config_source = str(effective_config_path or "-")
    observed_rows = "".join(
        f"<tr><td>{esc(ident)}</td><td>{status(item.get('status'))}</td>"
        f"<td>{esc(item.get('reason'))}</td>"
        f"<td><code>{esc(json_text(item.get('observed')))}</code></td></tr>"
        for ident, _title in OBJECTIVES
        for item in [objectives.get(ident, {})]
    )
    status_counts = {
        state: sum(
            str(objectives.get(ident, {}).get("status") or "INCONCLUSIVE").upper() == state
            for ident, _title in OBJECTIVES
        )
        for state in ("PASS", "FAIL", "INCONCLUSIVE")
    }
    status_legend = "".join(
        f"<div class='legend-item'><span class='dot {state.lower()}'></span>"
        f"<b>{count}</b> {esc(state)}</div>"
        for state, count in status_counts.items()
    )
    capacity_points = []
    for item in scenarios:
        name = str(item["name"])
        state = str(item["status"])
        if name.startswith("capacity-"):
            try:
                level = int(name.split("-", 1)[1])
            except ValueError:
                continue
            capacity_points.append((level, str(state).upper()))
    capacity_points.sort()
    capacity_chart = "".join(
        f"<div class='capacity-point'><span>{level}</span>"
        f"<i class='{'done' if state.upper() == 'COMPLETED' else 'blocked'}'></i></div>"
        for level, state in capacity_points
    )
    o3 = objectives.get("O3", {}).get("observed") or {}
    o4 = objectives.get("O4", {}).get("observed") or {}
    o1 = objectives.get("O1", {}).get("observed") or {}
    o3_jain = o3.get("jain")
    o4_ratio = o4.get("priority_ratio") or o4.get("degradation_ratio")
    o1_max = (
        o1.get("max_valid_active_user_count")
        or o1.get("max_completed_active_user_count")
        or o1.get("max_measured_active_user_count")
    )
    chart_max = max(
        (float(item["search_p95"]) for item in scenarios if item["search_p95"] is not None),
        default=0.0,
    )
    scenario_chart = "".join(
        f"<div class='chart-row'><span>{esc(item['key'])}</span>"
        f"<i style='width:{(float(item['search_p95']) / chart_max * 100.0) if chart_max else 0:.1f}%'></i>"
        f"<b>{esc(item['search_p95'])}s</b></div>"
        for item in scenarios
        if item["search_p95"] is not None
    )
    recall_stats = (recall_run.get("stats") or {}) if recall_run.get("available") else {}
    recall_config = recall_run.get("config") or {}
    recall_summary = recall_run.get("summary") or {}
    recall_query_rows = recall_stats.get("query_rows") or []
    recall_chart_max = max(
        (_as_float(item.get("avg_ms")) or 0.0 for item in recall_query_rows),
        default=0.0,
    )
    recall_query_chart = "".join(
        f"<div class='chart-row'><span>{esc(item.get('query'))}</span>"
        f"<i style='width:{((_as_float(item.get('avg_ms')) or 0.0) / recall_chart_max * 100.0) if recall_chart_max else 0:.1f}%'></i>"
        f"<b>{_fmt_number(item.get('avg_ms'))} ms</b></div>"
        for item in recall_query_rows
    )
    recall_count = int(recall_stats.get("count") or 0)
    recall_degraded_count = int(recall_stats.get("degraded_count") or 0)
    recall_degraded_rate = (
        recall_degraded_count / recall_count if recall_count else None
    )
    recall_query_kinds = recall_stats.get("query_kind_counts") or {}
    recall_section = (
        f"""<section><h2>真实记忆召回 Search 延迟</h2>
<div class="callout"><b>这组数据测的是什么：</b>先在 EchoMem 中准备可检索记忆，
再通过真实 HTTP Search 查询，只有成功返回且命中真实记忆的请求才纳入延迟统计。
它不是普通空查询，也不是把 429/失败请求的快速返回算进平均值。</div>
<div class="recall-dashboard">
<div class="viz"><div class="muted">平均延迟</div>
<div class="big-number">{_fmt_number(recall_stats.get("avg_ms"), 1)} ms</div>
<div class="muted">约 {_fmt_number((_as_float(recall_stats.get("avg_ms")) or 0.0) / 1000.0, 2)} 秒</div></div>
<div class="viz"><div class="muted">P50 / P95 / P99</div>
<div class="metric-line"><b>{_fmt_number(recall_stats.get("p50_ms"), 1)}</b><span> / </span>
<b>{_fmt_number(recall_stats.get("p95_ms"), 1)}</b><span> / </span>
<b>{_fmt_number(recall_stats.get("p99_ms"), 1)} ms</b></div>
<div class="muted">最小 {_fmt_number(recall_stats.get("min_ms"), 1)} ms ·
最大 {_fmt_number(recall_stats.get("max_ms"), 1)} ms</div></div>
<div class="viz"><div class="muted">真实命中</div>
<div class="big-number">{esc(recall_stats.get("real_recall_count"))}/{esc(recall_stats.get("count"))}</div>
<div class="muted">命中率 {_fmt_number(
            (recall_stats.get("real_recall_count") or 0) / recall_count * 100.0
            if recall_count else None, 1
        )}%</div></div>
</div>
<table><thead><tr><th>项目</th><th>结果</th><th>解释</th></tr></thead><tbody>
<tr><td>样本数</td><td>{esc(recall_stats.get("count"))}</td><td>有效、成功并纳入统计的真实召回 Search 请求</td></tr>
<tr><td>查询类型</td><td>{esc(", ".join(f"{key}={value}" for key, value in recall_query_kinds.items()))}</td><td>本专项为 recall-only</td></tr>
<tr><td>degraded 响应</td><td>{recall_degraded_count}/{recall_count}（{_fmt_number(recall_degraded_rate * 100.0 if recall_degraded_rate is not None else None, 1)}%）</td><td>服务返回了真实记忆，但完整 recall fan-out 存在降级标记；不能只看命中率判断链路健康</td></tr>
<tr><td>HTTP 错误 / 超时</td><td>{esc(recall_stats.get("error_count"))}</td><td>不纳入平均延迟分母，单独保留错误统计</td></tr>
</tbody></table>
<h3>不同查询的平均延迟</h3>
<div class="chart">{recall_query_chart or "<span class='muted'>没有逐查询统计</span>"}</div>
<p class="muted">来源运行：{esc(recall_run.get("run_dir"))}；
LLM：{esc(((recall_config.get("model") or {}).get("llm") or {}).get("model") or "-")}；
Embedding：{esc(((recall_config.get("model") or {}).get("embedding") or {}).get("model") or "-")}。
专项 summary 状态：{esc(recall_summary.get("status") or "-")}。</p>
</section>"""
        if recall_run.get("available")
        else ""
    )
    formal_metric_scene_count = sum(
        1
        for item in scenarios
        if str(item.get("run_dir") or "").strip()
        and (Path(item["run_dir"]) / "metrics_samples.csv").is_file()
    )
    # The real-recall supplement is an actual HTTP Search workload even
    # though it is not part of the formal scenario manifest.
    metric_scene_count = formal_metric_scene_count + (
        1 if recall_run.get("available") else 0
    )
    seed_warmup = formal_manifest.get("seed_warmup") or {}
    seed_status = str(seed_warmup.get("status") or "unknown")
    fault_configured = bool(
        (profile.get("fault_isolation") or {}).get("enabled")
        or profile.get("fault_plan")
    )
    capacity_completed = sorted(
        {
            item["name"]
            for item in scenarios
            if str(item["name"]).startswith("capacity-")
            and str(item["status"]).lower() == "completed"
        },
        key=lambda value: int(str(value).split("-", 1)[1]),
    )
    capacity_blocked = sorted(
        {
            item["name"]
            for item in scenarios
            if str(item["name"]).startswith("capacity-")
            and str(item["status"]).lower() == "blocked"
        },
        key=lambda value: int(str(value).split("-", 1)[1]),
    )
    capacity_action = (
        f"已完成档位：{', '.join(capacity_completed)}；"
        f"当前最高有效档：{capacity_completed[-1] if capacity_completed else '-'}；"
        "还需要更高一档真实 SLO 失败，才能报告最大边界。"
        if capacity_completed
        else "继续执行尚未完成的容量档位，并记录最后成功档位与下一档真实失败。"
    )
    module_rows = [
        (
            "认证 / 多租户",
            f"有效凭据 {auth.get('passed', 0)}/{auth.get('tenant_count', 0)}；"
            "正式矩阵使用独立凭据，不把同一个 key 伪装成多个租户。",
            "auth / tenant_config",
            "测试平台输入与部署凭据",
            "认证前提已满足；故障隔离仍需部署侧提供只作用于目标租户的故障控制。",
        ),
        (
            "容量阶梯",
            f"已完成 {', '.join(capacity_completed) or '无'}；"
            f"阻断 {', '.join(capacity_blocked) or '无'}，未形成最大容量失败边界。",
            "session / active-user / admission",
            "测试平台场景 / 4U8G 资源",
            capacity_action,
        ),
        (
            "检索 / Search",
            f"真实 Search 场景 {metric_scene_count} 个（正式场景 {formal_metric_scene_count} 个"
            f" + 真实召回专项 {'1' if recall_run.get('available') else '0'}）；"
            f"共享 seed 状态为 {seed_status}，"
            "当前不能把无记忆请求当作热缓存结果。",
            "recall / model / index",
            "测试数据准备与 EchoMem 检索链路",
            "先保证 seed Commit 完成并用 marker Search 验证命中，再判定热缓存准确率和优先级。",
        ),
        (
            "路由 / 调度",
            "公平窗口有 4 租户 Commit/Search 竞争；Priority 窗口有真实并发，但热记忆前提不足。",
            "router / scheduler / tenant_coordination",
            "测试平台负载 + EchoMem 调度",
            "保留到达/完成时间、在途 Commit 和 Search P95，避免只凭客户端返回判定优先级。",
        ),
        (
            "Commit / 持久化",
            "202、kill-9、重启、completed、history/archive/cursor 顺序对账均有证据。",
            "commit_pipeline / storage / control_store",
            "EchoMem 现有接口 + 测试平台恢复探针",
            "增加重复样本；同幂等键虽返回同 archive，但 replayed 标记为 false，需单独确认契约。",
        ),
        (
            "Metrics / 可观测性",
            f"/metrics 能力探针可访问；{metric_scene_count} 个真实 Search 场景有原始采样，"
            "但 lane/fan-out 覆盖仍需按实际负载补齐。",
            "observability / metrics / engine_state",
            "EchoMem /metrics 与测试平台触发负载",
            "用真实拒绝、等待和引擎 fan-out 负载触发每个指标，再采集完整四元组。",
        ),
        (
            "故障控制面",
            (
                "已配置故障控制计划，但仍需检查每个旁观租户前后 P95。"
                if fault_configured
                else "未配置真实单租户依赖故障控制 URL/命令，O2 无前后 P95 配对数据。"
            ),
            "fault control / deployment",
            "部署侧控制面 + 测试平台采集",
            "提供只作用于目标租户的 500/429/timeout/connection-refused 控制，并保留时间线。",
        ),
    ]
    module_html = "".join(
        f"<tr><td><b>{esc(module)}</b></td><td>{esc(evidence)}</td>"
        f"<td><code>{esc(config_path)}</code></td><td>{esc(owner)}</td>"
        f"<td><span class='{'issue-text' if module in {'容量阶梯', '检索 / Search', '路由 / 调度', 'Commit / 持久化', 'Metrics / 可观测性', '故障控制面'} and objectives.get({'容量阶梯': 'O1', '检索 / Search': 'O4', '路由 / 调度': 'O3', 'Commit / 持久化': 'O5', 'Metrics / 可观测性': 'O6', '故障控制面': 'O2'}.get(module, ''), {}).get('status') != 'PASS' else ''}'>{esc(action)}</span></td></tr>"
        for module, evidence, config_path, owner, action in module_rows
    )
    module_objective_ids = {
        "容量阶梯": "O1",
        "故障控制面": "O2",
        "路由 / 调度": "O3",
        "检索 / Search": "O4",
        "Commit / 持久化": "O5",
        "Metrics / 可观测性": "O6",
    }
    module_issue_items = "".join(
        f"<li><b>{esc(module)}</b>：<span class='issue-text'>{esc(action)}</span></li>"
        for module, _evidence, _config_path, _owner, action in module_rows
        if module in module_objective_ids
        and objectives.get(module_objective_ids[module], {}).get("status") != "PASS"
    )
    module_issue_summary = (
        f"<ul class='issue-list'>{module_issue_items}</ul>"
        if module_issue_items
        else "<p>当前六项指标均有足够证据，未发现待处理模块问题。</p>"
    )

    def scenario_item(source: str, plan: str = "pr421") -> dict[str, Any]:
        candidates = [
            item for item in scenarios
            if str(item.get("name")) == source
        ]
        planned = [
            item for item in candidates
            if not plan or str(item.get("plan")) == plan
        ]
        # Direct formal runs and older merged manifests may omit ``plan``.
        # Prefer a declared plan, but do not hide a valid source-name match.
        return (planned or candidates or [{}])[0]

    def pct(value: Any) -> str:
        number = _as_float(value)
        return "-" if number is None else f"{number * 100:.2f}%"

    capacity_detail_rows = []
    for item in sorted(
        (
            row for row in scenarios
            if str(row.get("name") or "").startswith("capacity-")
        ),
        key=lambda row: int(str(row["name"]).split("-", 1)[1])
        if str(row["name"]).split("-", 1)[1].isdigit()
        else 0,
    ):
        activity = item.get("activity") or {}
        hot = activity.get("hot_user_proxy") or {}
        resources = item.get("resources") or {}
        capacity_detail_rows.append(
            f"<tr><td>{esc(item['key'])}</td>"
            f"<td>{esc(item['name'])}</td>"
            f"<td>{esc(activity.get('active_user_count') or '-')}</td>"
            f"<td>{esc(hot.get('request_count') or '-')}</td>"
            f"<td>{esc(item.get('search_submitted'))}/{esc(item.get('search_succeeded'))}"
            f"<br><span class='muted'>{pct(item.get('search_success_rate'))}</span></td>"
            f"<td>{_fmt_number(item.get('search_p95'), 3)} s</td>"
            f"<td>{_fmt_number(resources.get('cpu_util_mean_percent'), 2)}%"
            f"<br><span class='muted'>RSS {_fmt_number(resources.get('rss_peak_mb'), 1)} MB</span></td>"
            f"<td>{status(item.get('status'))}</td></tr>"
        )
    capacity_detail_html = "".join(capacity_detail_rows) or (
        "<tr><td colspan='8'>没有容量场景证据</td></tr>"
    )

    fault_observed = objectives.get("O2", {}).get("observed") or {}
    fault_bystanders = fault_observed.get("bystander_tenants")
    if isinstance(fault_bystanders, dict) and fault_bystanders:
        fault_rows = "".join(
            f"<tr><td>{esc(tenant)}</td>"
            f"<td>{_fmt_number(sample.get('baseline_p95_s'), 3)} s</td>"
            f"<td>{_fmt_number(sample.get('fault_p95_s'), 3)} s</td>"
            f"<td>{pct(sample.get('degradation'))}</td>"
            f"<td>{esc(sample.get('error_rate') or '-')}</td></tr>"
            for tenant, sample in fault_bystanders.items()
            if isinstance(sample, dict)
        )
    else:
        fault_rows = (
            "<tr><td colspan='5'>没有真实单租户故障控制和旁观租户前后 P95 配对数据；"
            "不能用普通压力场景替代故障注入。</td></tr>"
        )

    fault_chart_rows = []
    if isinstance(fault_bystanders, dict):
        for tenant, sample in fault_bystanders.items():
            if not isinstance(sample, dict):
                continue
            baseline = _as_float(sample.get("baseline_p95_s"))
            fault_p95 = _as_float(sample.get("fault_p95_s"))
            if baseline is not None:
                fault_chart_rows.append((f"{tenant} 故障前", baseline, "teal"))
            if fault_p95 is not None:
                fault_chart_rows.append((f"{tenant} 故障期", fault_p95, "red"))
    fault_chart_max = max((value for _, value, _ in fault_chart_rows), default=0.0)
    fault_chart = "".join(
        f"<div class='chart-row'><span>{esc(label)}</span>"
        f"<i class='{tone}' style='width:{(value / fault_chart_max * 100.0) if fault_chart_max else 0:.1f}%'></i>"
        f"<b>{_fmt_number(value, 3)} s</b></div>"
        for label, value, tone in fault_chart_rows
    )

    fairness_items = [
        item for item in scenarios
        if str(item.get("name")) in {"fairness-steady", "fairness-bounded", "mixed"}
        and str(item.get("plan")) == "pr421"
    ]
    fairness_rows: list[str] = []
    for item in fairness_items:
        for tenant, tenant_data in sorted(
            (item.get("per_tenant") or {}).items(),
            key=lambda pair: str(pair[0]),
        ):
            if not isinstance(tenant_data, dict):
                continue
            tenant_commit = tenant_data.get("commit") or {}
            tenant_search = tenant_data.get("search") or {}
            fairness_rows.append(
                f"<tr><td>{esc(item['key'])}</td><td>{esc(tenant)}</td>"
                f"<td>{esc(tenant_commit.get('submitted') or 0)}</td>"
                f"<td>{esc(tenant_commit.get('completed') or 0)}</td>"
                f"<td>{_fmt_number(tenant_search.get('latency', {}).get('p95_s'), 3)} s</td>"
                f"<td>{esc(item.get('duration_s'))} s</td></tr>"
            )
    fairness_detail_html = "".join(fairness_rows) or (
        "<tr><td colspan='6'>没有逐租户公平性数据</td></tr>"
    )
    fairness_chart_rows = []
    fairness_observed_tenants = o3.get("tenants") if isinstance(o3, dict) else {}
    if isinstance(fairness_observed_tenants, dict):
        for tenant, values in sorted(
            fairness_observed_tenants.items(),
            key=lambda pair: str(pair[0]),
        ):
            if not isinstance(values, dict):
                continue
            throughput = _as_float(values.get("commit_throughput"))
            search_p95 = _as_float(values.get("search_p95_s"))
            if throughput is not None:
                fairness_chart_rows.append((f"{tenant} Commit", throughput, "teal"))
            if search_p95 is not None:
                fairness_chart_rows.append((f"{tenant} Search P95", search_p95, "amber"))
    fairness_chart_max = max((value for _, value, _ in fairness_chart_rows), default=0.0)
    fairness_chart = "".join(
        f"<div class='chart-row'><span>{esc(label)}</span>"
        f"<i class='{tone}' style='width:{(value / fairness_chart_max * 100.0) if fairness_chart_max else 0:.1f}%'></i>"
        f"<b>{_fmt_number(value, 3)}</b></div>"
        for label, value, tone in fairness_chart_rows
    )
    fairness_steady = scenario_item("fairness-steady")
    fairness_bounded = scenario_item("fairness-bounded")
    fairness_note = (
        "正式等权窗口必须在同一个固定时间窗内让每个租户都提交 Commit 并发送 Search；"
        "任何租户没有提交、没有 completed 或没有 Search P95，都必须保留在分母并将本项标为 "
        "INCONCLUSIVE，不能只对“有结果的租户”计算 Jain。"
    )

    baseline_item = scenario_item("baseline")
    priority_item = scenario_item("search-priority-blackbox")
    baseline_p95 = _as_float(baseline_item.get("search_p95"))
    priority_p95 = _as_float(priority_item.get("search_p95"))
    priority_ratio = (
        priority_p95 / baseline_p95
        if baseline_p95 and priority_p95 is not None
        else None
    )
    overlap = priority_item.get("same_window_overlap") or {}
    priority_detail = (
        f"基线 Search P95 {_fmt_number(baseline_p95, 3)} s；"
        f"洪泛场景 Search P95 {_fmt_number(priority_p95, 3)} s；"
        f"比值 {_fmt_number(priority_ratio, 3)}；"
        f"Commit 提交/完成 {esc(priority_item.get('commit_submitted'))}/"
        f"{esc(priority_item.get('commit_completed'))}；"
        f"同窗重叠 {esc(overlap.get('overlap_ms') or 0)} ms。"
    )
    priority_chart_rows = []
    if baseline_p95 is not None:
        priority_chart_rows.append(("无 Commit 洪泛基线", baseline_p95, "teal"))
    if priority_p95 is not None:
        priority_chart_rows.append(("Commit 洪泛期间", priority_p95, "amber"))
    priority_chart_max = max((value for _, value, _ in priority_chart_rows), default=0.0)
    priority_chart = "".join(
        f"<div class='chart-row'><span>{esc(label)}</span>"
        f"<i class='{tone}' style='width:{(value / priority_chart_max * 100.0) if priority_chart_max else 0:.1f}%'></i>"
        f"<b>{_fmt_number(value, 3)} s</b></div>"
        for label, value, tone in priority_chart_rows
    )
    recovery = formal_manifest.get("commit_recovery") or {}
    recovery_control = recovery.get("container_control") or {}
    recovery_terminal = recovery.get("commit_terminal") or []
    recovery_final_state = (
        recovery_terminal[-1].get("state")
        if recovery_terminal and isinstance(recovery_terminal[-1], dict)
        else "-"
    )
    recovery_detail_rows = "".join(
        f"<tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>"
        for label, value in (
            ("真实 HTTP", recovery.get("real_http")),
            ("202 已接受", recovery.get("accepted_202")),
            ("kill/restart 控制", recovery_control.get("control_backend") or "-"),
            ("服务已恢复", recovery.get("recovered")),
            ("Commit 最终状态", recovery_final_state),
            ("消息集合对账", recovery.get("message_set_reconciled")),
            ("顺序对账", recovery.get("order_reconciliation")),
            ("幂等重放同 archive", (recovery.get("idempotency_replay") or {}).get("same_archive")),
        )
    )
    capability = formal_manifest.get("capability_probe") or {}
    capability_checks = capability.get("checks") or []
    metric_check = next(
        (
            item for item in capability_checks
            if isinstance(item, dict) and item.get("name") == "Prometheus B7 metrics"
        ),
        {},
    )
    metric_present = metric_check.get("present") or {}
    metric_pass_count = sum(1 for value in metric_present.values() if bool(value))
    contract = formal_manifest.get("blackbox_contract_probe") or {}
    contract_summary = contract.get("summary") or {}
    coverage_lanes: dict[str, dict[str, bool]] = {}
    coverage_fanout: dict[str, dict[str, bool]] = {}
    for item in scenarios:
        metric_coverage = item.get("metric_coverage") or {}
        for lane, quartet in (metric_coverage.get("lane_quartets") or {}).items():
            if not isinstance(quartet, dict):
                continue
            current = coverage_lanes.setdefault(
                str(lane),
                {"queued": False, "wait": False, "exec": False, "rejected": False},
            )
            for key in current:
                current[key] = bool(current[key] or quartet.get(key))
        for engine, values in (metric_coverage.get("fanout_engines") or {}).items():
            if not isinstance(values, dict):
                continue
            current = coverage_fanout.setdefault(
                str(engine), {"exec": False, "skipped": False}
            )
            for key in current:
                current[key] = bool(current[key] or values.get(key))
    expected_lanes = {
        str(item).strip()
        for item in (effective_expectations.get("lanes") or [])
        if str(item).strip()
    }
    expected_fanout_engines = {
        str(item).strip()
        for item in (effective_expectations.get("fanout_engines") or [])
        if str(item).strip()
    }
    all_lanes = sorted(expected_lanes | set(coverage_lanes))
    o6_rows = "".join(
        f"<tr><td>{esc(lane)}</td>"
        f"<td>{'是' if lane in expected_lanes else '否'}</td>"
        f"<td>{'✓' if values.get('queued') else '—'}</td>"
        f"<td>{'✓' if values.get('wait') else '—'}</td>"
        f"<td>{'✓' if values.get('exec') else '—'}</td>"
        f"<td>{'✓' if values.get('rejected') else '—'}</td>"
        f"<td>{('OBSERVED' if all(values.get(key) for key in ('queued', 'wait', 'exec', 'rejected')) else 'MISSING_EVIDENCE') if lane in expected_lanes else 'OBSERVED_NOT_EXPECTED'}</td></tr>"
        for lane in all_lanes
        for values in [coverage_lanes.get(
            lane,
            {"queued": False, "wait": False, "exec": False, "rejected": False},
        )]
    ) or "<tr><td colspan='7'>没有场景级 lane 覆盖数据</td></tr>"
    all_fanout_engines = sorted(expected_fanout_engines | set(coverage_fanout))
    o6_fanout_rows = "".join(
        f"<tr><td>{esc(engine)}</td>"
        f"<td>{'是' if engine in expected_fanout_engines else '否'}</td>"
        f"<td>{'✓' if values.get('exec') else '—'}</td>"
        f"<td>{'✓' if values.get('skipped') else '—'}</td>"
        f"<td>{('OBSERVED' if values.get('exec') and values.get('skipped') else 'MISSING_EVIDENCE') if engine in expected_fanout_engines else 'OBSERVED_NOT_EXPECTED'}</td></tr>"
        for engine in all_fanout_engines
        for values in [coverage_fanout.get(
            engine,
            {"exec": False, "skipped": False},
        )]
    ) or "<tr><td colspan='5'>没有 engine fan-out 覆盖数据</td></tr>"

    tenant_snapshot = formal_manifest.get("tenant_observability") or {}
    tenant_snapshot_rows = "".join(
        f"<tr><td>{esc(item.get('tenant_id'))}</td><td>{esc(item.get('lane'))}</td>"
        f"<td>{esc(item.get('queued'))}</td>"
        f"<td>{_fmt_number(item.get('wait_seconds_total'), 3)} s</td>"
        f"<td>{_fmt_number(item.get('exec_seconds_total'), 3)} s</td>"
        f"<td>{esc(item.get('rejected_total'))}</td>"
        f"<td>{esc(item.get('accepted_total') or '-')}</td>"
        f"<td>{esc(item.get('completed_total') or '-')}</td></tr>"
        for item in tenant_snapshot.get("rows") or []
        if isinstance(item, dict)
    )
    tenant_snapshot_rows = tenant_snapshot_rows or (
        "<tr><td colspan='8'>没有逐租户快照数据；"
        "仅有 /metrics 端点存在不能证明每租户覆盖。</td></tr>"
    )
    recovery_iterations = recovery.get("iteration_results") or []
    if recovery_iterations:
        recovery_step_rows = []
        for index, item in enumerate(recovery_iterations, 1):
            control_ok = item.get("container_control_ok")
            terminal = item.get("commit_terminal") or []
            final = (
                terminal[-1].get("state")
                if terminal and isinstance(terminal[-1], dict)
                else "-"
            )
            replay = (item.get("idempotency_replay") or {}).get("same_archive")
            order = (item.get("order_reconciliation") or {}).get("status")
            recovery_step_rows.append(
                f"<tr><td>样本 {index}</td><td>{esc(item.get('accepted_202'))}</td>"
                f"<td>{esc(control_ok)}</td><td>{esc(item.get('recovered'))}</td>"
                f"<td>{esc(final)}</td><td>{esc(replay)}</td>"
                f"<td>{esc(order or '-')}</td><td>{status(item.get('status'))}</td></tr>"
            )
        recovery_iteration_html = "".join(recovery_step_rows)
    else:
        recovery_iteration_html = (
            "<tr><td colspan='8'>当前结果为单样本旧格式，未提供多次恢复统计。</td></tr>"
        )
    recovery_counts = (
        f"样本 {esc(recovery.get('attempted_iterations') or 1)}；"
        f"202 {esc(recovery.get('accepted_202_count') if recovery.get('accepted_202_count') is not None else recovery.get('accepted_202'))}；"
        f"恢复完成 {esc(recovery.get('recovered_completed_count') or '-')}；"
        f"重放对账 {esc(recovery.get('replay_verified_count') or '-')}；"
        f"顺序对账 {esc(recovery.get('order_verified_count') or '-')}"
    )

    evidence_rows = "".join(
        f"<tr><td>{esc(item['key'])}</td><td>{esc(item['plan'])}</td>"
        f"<td>{esc(item['run_dir'] or '-')}</td>"
        f"<td>{'有' if str(item.get('run_dir') or '').strip() and (Path(item['run_dir']) / 'summary.json').is_file() else '嵌入 suite'}</td>"
        f"<td>{'有' if str(item.get('run_dir') or '').strip() and (Path(item['run_dir']) / 'search_results.csv').is_file() else '—'}</td>"
        f"<td>{'有' if str(item.get('run_dir') or '').strip() and (Path(item['run_dir']) / 'commit_results.csv').is_file() else '—'}</td></tr>"
        for item in scenarios
    )
    detailed_metrics_html = f"""
<section><h2>六项指标详细数据</h2>
<p class="muted">以下每项都区分“已观测数据”和“是否足以做出完整结论”。没有数据的部分保留为 INCONCLUSIVE，不用推测补齐。</p>
<article class="metric-detail"><h3>O1 · 单实例 DAU / 热用户容量</h3>
<p>容量阶梯实际观察到活跃 session 和单 session 请求量；当前最高有效档为
{esc(o1.get('max_valid_active_user_count') or o1.get('max_measured_active_user_count') or '-')}
个活动用户代理，最高热用户代理请求量为
{esc(o1.get('max_measured_hot_user_requests') or '-')}。尚未出现更高档真实失败边界，
所以结论是“至少支持到该档”，不是“最大容量”。</p>
<table><thead><tr><th>场景键</th><th>档位</th><th>活动用户代理</th><th>热用户请求数</th><th>Search</th><th>Search P95</th><th>资源</th><th>状态</th></tr></thead>
<tbody>{capacity_detail_html}</tbody></table></article>

<article class="metric-detail"><h3>O2 · 单租户故障隔离</h3>
<p>{esc(objectives.get('O2', {}).get('reason') or '当前没有结论')}</p>
<div class="chart">{fault_chart or "<span class='muted'>没有故障前后 P95 配对数据</span>"}</div>
<table><thead><tr><th>旁观租户</th><th>故障前 P95</th><th>故障期 P95</th><th>劣化</th><th>错误率</th></tr></thead>
<tbody>{fault_rows}</tbody></table></article>

<article class="metric-detail"><h3>O3 · Commit 吞吐 / Search 延迟公平性 Jain</h3>
<p>比较对象是<strong>不同租户</strong>，不是同一租户的 Commit 和 Search。
Commit Jain 使用各租户完成吞吐；Search 延迟 Jain 使用各租户 P95 的倒数。
{esc(fairness_note)}</p>
<div class="chart">{fairness_chart or "<span class='muted'>没有同一窗口的逐租户吞吐/P95 数据</span>"}</div>
<div class="jain-pair"><div><span>Commit Jain</span><b>{_fmt_number(o3.get('commit_jain'), 4)}</b></div>
<div><span>Search 延迟 Jain</span><b>{_fmt_number(o3.get('search_latency_jain'), 4)}</b></div>
<div><span>验收取较小值</span><b>{_fmt_number(o3.get('jain'), 4)}</b></div></div>
<table><thead><tr><th>场景键</th><th>租户</th><th>Commit 提交</th><th>Commit 完成</th><th>Search P95</th><th>窗口</th></tr></thead>
<tbody>{fairness_detail_html}</tbody></table>
<p class="muted">当前验收状态：{status(objectives.get('O3', {}).get('status'))}；
Commit/Search 双维 Jain：{esc(o3.get('jain') or '-')}。</p></article>

<article class="metric-detail"><h3>O4 · Commit 洪泛时 Search 优先级</h3>
<p>{esc(priority_detail)}</p>
<div class="chart">{priority_chart or "<span class='muted'>没有基线和洪泛 P95</span>"}</div>
<p>当前验收状态：{status(objectives.get('O4', {}).get('status'))}。
虽然记录到了同窗重叠和 Search P95，但本轮只接受了
{esc(priority_item.get('commit_submitted') or 0)} 个 Commit，低于正式洪泛证据要求，
而且正式场景的 Search 质量断言没有命中真实记忆，因此不能宣称严格优先。</p></article>

<article class="metric-detail"><h3>O5 · 202 Commit 崩溃恢复 / 顺序 / 幂等</h3>
<p>这组补测使用真实 HTTP 和真实容器控制，不把 202 或 completed 单独当成持久化证明。</p>
<p class="metric-line">{recovery_counts}</p>
<table><thead><tr><th>样本</th><th>202</th><th>kill/restart</th><th>服务恢复</th><th>最终状态</th><th>同 archive</th><th>顺序对账</th><th>结论</th></tr></thead>
<tbody>{recovery_iteration_html}</tbody></table>
<table><tbody>{recovery_detail_rows or '<tr><td colspan="2">没有恢复探针数据</td></tr>'}</tbody></table>
<p>当前验收状态：{status(objectives.get('O5', {}).get('status'))}。
只有当所有独立样本都在 kill-9 前收到 202，且每个样本都完成恢复、消息/顺序/幂等对账时，才允许写成 100%。</p></article>

<article class="metric-detail"><h3>O6 · 每层 / 每租户四元组可观测性</h3>
<p>能力探针确认 Prometheus B7 指标接口可访问，当前端点级检查为
{esc(metric_pass_count)}/{esc(len(metric_present) or 0)}；契约探针
{esc(contract_summary.get('pass') or 0)}/{esc(contract_summary.get('total') or 0)}
个接口检查通过。但正式运行只采到了部分 lane，不能把端点存在当成“每层每租户都有真实变化数据”。</p>
<table><thead><tr><th>Lane</th><th>按配置应存在</th><th>queued</th><th>wait</th><th>exec</th><th>rejected</th><th>证据状态</th></tr></thead>
<tbody>{o6_rows}</tbody></table>
<h4>Engine fan-out</h4><table><thead><tr><th>Engine</th><th>按配置应存在</th><th>exec</th><th>skipped</th><th>证据状态</th></tr></thead>
<tbody>{o6_fanout_rows}</tbody></table>
<h4>逐租户四元组快照</h4>
<table><thead><tr><th>租户</th><th>Lane</th><th>排队深度</th><th>等待总时长</th><th>执行总时长</th><th>拒绝数</th><th>接受数</th><th>完成数</th></tr></thead>
<tbody>{tenant_snapshot_rows}</tbody></table>
<p>当前验收状态：{status(objectives.get('O6', {}).get('status'))}。</p></article>
</section>
<section><h2>原始运行证据索引</h2>
<p class="muted">场景 summary 由合并 suite 保存；存在独立 CSV 的地方直接链接在上方原始证据区。</p>
<table><thead><tr><th>场景键</th><th>计划</th><th>运行目录</th><th>summary</th><th>Search CSV</th><th>Commit CSV</th></tr></thead>
<tbody>{evidence_rows}</tbody></table></section>
"""

    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR29 4U8G 六项指标报告</title>
<style>
:root{{--ink:#172a35;--muted:#667983;--line:#d8e2e7;--bg:#f4f7f8;--teal:#176b87;--green:#147a61;--red:#b6423d;--amber:#9a6b00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1320px;margin:auto;padding:28px 20px 60px}}h1{{margin:0;font-size:28px}}h2{{margin:0 0 14px;font-size:20px}}h3{{margin:5px 0;font-size:15px}}
.sub,.muted{{color:var(--muted)}}section{{background:#fff;border:1px solid var(--line);padding:18px;margin:14px 0}}
.cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}}.card{{background:#fbfcfc;border-top:4px solid var(--amber);padding:12px;min-height:142px}}
.card p{{color:var(--muted);font-size:12px}}.id{{font-weight:800;color:var(--teal);font-size:12px}}
.badge{{display:inline-block;padding:2px 7px;border-radius:3px;font-weight:700;font-size:12px}}
.pass{{color:var(--green);background:#e5f4ee}}.fail{{color:var(--red);background:#fae9e7}}.warn{{color:var(--amber);background:#fff3d2}}.neutral{{color:var(--muted);background:#edf1f2}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f2f6f7;font-size:12px}}
.dashboard{{display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:12px}}.viz{{border:1px solid var(--line);padding:14px;background:#fbfcfc}}
.viz h3{{margin-top:0}}.legend-item{{display:inline-flex;align-items:center;margin:0 12px 8px 0;gap:5px;font-size:12px}}.dot{{width:10px;height:10px;border-radius:50%;display:inline-block}}.dot.pass{{background:var(--green)}}.dot.fail{{background:var(--red)}}.dot.inconclusive{{background:var(--amber)}}
.big-number{{font-size:28px;font-weight:800;color:var(--teal);margin:4px 0}}.meter{{display:block;height:9px;background:#e6edef;margin:7px 0 11px;border-radius:2px;overflow:hidden}}.meter i{{display:block;height:100%;background:var(--teal)}}.meter.green i{{background:var(--green)}}.meter.amber i{{background:var(--amber)}}.meter.red i{{background:var(--red)}}
.capacity{{display:flex;align-items:flex-end;gap:10px;height:72px;border-bottom:1px solid var(--line);padding:0 8px}}.capacity-point{{display:flex;flex-direction:column;align-items:center;gap:5px;color:var(--muted);font-size:11px}}.capacity-point i{{display:block;width:18px;height:42px;background:var(--amber);border-radius:2px 2px 0 0}}.capacity-point i.done{{background:var(--green)}}.capacity-point i.blocked{{background:#d8e2e7}}
.chart{{display:grid;gap:7px;max-width:880px}}.chart-row{{display:grid;grid-template-columns:190px minmax(80px,1fr) 70px;align-items:center;gap:8px;font-size:12px}}.chart-row i{{display:block;height:14px;background:var(--teal);border-radius:2px;min-width:2px}}.chart-row i.amber{{background:var(--amber)}}.chart-row i.red{{background:var(--red)}}.chart-row b{{font-size:12px;color:var(--muted)}}
.jain-pair{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}}.jain-pair div{{border:1px solid var(--line);background:#fbfcfc;padding:10px}}.jain-pair span{{display:block;color:var(--muted);font-size:12px}}.jain-pair b{{display:block;color:var(--teal);font-size:20px;margin-top:3px}}
.recall-dashboard{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:12px 0}}.metric-line{{font-size:20px;color:var(--teal);margin:6px 0}}
.issue-text{{color:var(--red);font-weight:650}}.issue-row td{{background:#fff7f6}}
.issue-list{{margin:8px 0;padding-left:22px}}.issue-list li{{margin:7px 0}}
.callout{{border-left:4px solid var(--teal);background:#eef7f9;padding:12px 14px}}ul{{padding-left:20px}}a{{color:var(--teal)}}
@media(max-width:1050px){{.cards{{grid-template-columns:repeat(2,1fr)}}.dashboard{{grid-template-columns:1fr}}.recall-dashboard{{grid-template-columns:1fr}}}}@media(max-width:520px){{.cards{{grid-template-columns:1fr}}.chart-row{{grid-template-columns:120px minmax(60px,1fr) 58px}}main{{padding:18px 10px}}}}
</style></head><body><main>
<h1>PR29 4U8G 六项指标真实黑盒测试</h1>
<p class="sub">生成时间：{esc(suite.get("created_at"))} · 场景覆盖：{scenario_coverage} · 默认不含 soak</p>
<div class="callout"><b>阅读规则：</b>配置了场景不等于已经完成测试；有 HTTP 返回不等于有记忆召回证据。
缺少故障控制、热记忆或完整指标样本时，保留为 INCONCLUSIVE。</div>
<section><h2>六项指标结论</h2><div class="cards">{cards}</div></section>
<section><h2>模块问题摘要</h2>
<p class="muted">红字表示当前不是 EchoMem 已被证明有 bug，而是该模块的真实验收证据仍不足或已出现失败；责任边界见下方明细。</p>
{module_issue_summary}</section>
<section><h2>测试平台覆盖审计</h2>
<p class="muted">这张表回答“平台有没有配置出数据的入口”，不把入口存在误当成指标通过。</p>
<table><thead><tr><th>指标</th><th>平台状态</th><th>已配置场景</th>
<th>已配置探针</th><th>缺口</th><th>责任边界</th></tr></thead><tbody>
{"".join(
    f"<tr><td><b>{esc(item.get('id'))}</b> {esc(item.get('name'))}</td>"
    f"<td>{esc(item.get('status'))}</td>"
    f"<td>{esc(', '.join(map(str, item.get('configured_scenarios') or [])) or '-')}</td>"
    f"<td>{esc(', '.join(map(str, item.get('configured_probes') or [])) or '-')}</td>"
    f"<td>{esc(', '.join(map(str, item.get('missing') or [])) or '-')}</td>"
    f"<td>{esc(item.get('owner') or '-')}</td></tr>"
    for item in platform_coverage if isinstance(item, dict)
)}
</tbody></table>
<div class="callout">平台状态为 <b>incomplete</b> 时，报告仍展示已有真实数据，
但不会自动把结果标成 PASS；需要区分“平台未配置”“部署没有控制/凭据”和“服务真实失败”。</div>
</section>
<section><h2>一眼看懂</h2><div class="dashboard">
<div class="viz"><h3>状态分布</h3><div>{status_legend}</div>
<p class="muted">PASS 只代表当前验收器已有充分证据；INCONCLUSIVE 代表还缺真实前提或样本。</p></div>
<div class="viz"><h3>已观测容量</h3><div class="big-number">{esc(o1_max or "-")} 个 session</div>
<div class="capacity">{capacity_chart or "<span class='muted'>无容量数据</span>"}</div>
<p class="muted">绿色为实际完成，灰色为未形成有效边界；不是业务 DAU。</p></div>
<div class="viz"><h3>关键数值</h3>
<div class="muted">公平性 Jain（取较小值）</div><b>{esc(o3_jain or "-")}</b>
{metric_bar(float(o3_jain) if o3_jain is not None else None, 1, "green")}
<div class="muted">Search 洪泛/基线比</div><b>{esc(o4_ratio or "-")}</b>
{metric_bar(float(o4_ratio) if o4_ratio is not None else None, 2, "amber")}</div>
</div></section>
<section><h2>Search P95 场景对比</h2>
<p class="muted">每根条代表一个已产生真实 Search 样本的场景；没有样本的阻断场景不参与比例缩放。</p>
<div class="chart">{scenario_chart or "<span class='muted'>暂无 Search P95 数据</span>"}</div></section>
{recall_section}
<section><h2>运行环境</h2><table>
<tr><th>项目</th><th>值</th></tr>
<tr><td>测试平台</td><td>Memory-System-Eval-Harness PR29</td></tr>
<tr><td>实例</td><td>4 vCPU / 8 GiB，4U8G</td></tr>
<tr><td>EchoMem 配置来源</td><td><code>{esc(runtime_config_source)}</code></td></tr>
<tr><td>LLM</td><td>{esc(llm)}（真实模型）</td></tr>
<tr><td>Embedding</td><td>{esc(embedding)}（真实模型）</td></tr>
<tr><td>Recall 意图层</td><td>{esc(", ".join(runtime_layers) or "-")}</td></tr>
<tr><td>启用 Engine</td><td>{esc(", ".join(runtime_enabled_engines) or "-")}</td></tr>
<tr><td>配置明确关闭</td><td>{esc(", ".join(runtime_disabled) or "无")}</td></tr>
<tr><td>独立凭据</td><td>{esc(auth.get("passed", 0))}/{esc(auth.get("tenant_count", 0))}</td></tr>
<tr><td>Search 配置</td><td>{esc(runner_config.get("search_query_profile") or "-")}；
recall 比例 {esc(runner_config.get("search_recall_ratio") or "-")}；
seed {esc(not runner_config.get("skip_seed", True))}</td></tr>
<tr><td>Commit 配置</td><td>轮询超时 {esc(runner_config.get("commit_poll_timeout_s") or "-")}s；
重试 {esc(runner_config.get("commit_retry_max") or "-")} 次；
barrier {esc(runner_config.get("commit_barrier"))}</td></tr>
</table></section>
<section><h2>场景明细</h2><p class="muted">Search 和 Commit 均按“提交数/成功或完成数”展示；成功率、P95、耗时和错误数直接来自场景 summary.json。场景键保留 PR397/PR421 前缀，避免同名场景混淆。</p>
<table><thead><tr><th>场景 / 方案</th><th>计划</th><th>运行状态</th><th>Search</th><th>Commit</th><th>延迟 P95</th><th>耗时</th><th>错误数</th></tr></thead>
<tbody>{scenario_rows or "<tr><td colspan='8'>没有场景结果</td></tr>"}</tbody></table></section>
<section><h2>六项指标原始观测</h2><p class="muted">这里保留验收器实际使用的 observed 字段，便于复核容量档位、Jain、恢复和指标覆盖。</p>
<table><thead><tr><th>指标</th><th>状态</th><th>判定说明</th><th>观测数据</th></tr></thead>
<tbody>{observed_rows}</tbody></table></section>
<section><h2>按 config.json 模块归因</h2>
<table><thead><tr><th>模块</th><th>当前真实证据</th><th>config.json 对应区域</th><th>归属</th><th>下一步</th></tr></thead>
<tbody>{module_html}</tbody></table>
<div class="callout"><b>归因原则：</b>没有真实故障控制、热记忆命中或指标样本时，只能说明测试前提/证据缺失；
不能直接写成 EchoMem 内部未实现。只有 HTTP 404 或真实服务行为明确违反契约时，才建议修改 EchoMem。</div></section>
<section><h2>EchoMem PR449 对接状态</h2>
<table><thead><tr><th>能力</th><th>代码/接口证据</th><th>本轮结论</th></tr></thead>
<tbody>
<tr><td>Commit 状态与 202 恢复</td><td>commit status、history、archive、cursor 对账路径已存在</td><td>{status("PASS")}</td></tr>
<tr><td>Search lane / fan-out 指标</td><td>/metrics 已定义 lane 四元组和 engine fan-out 指标</td><td>{status("INCONCLUSIVE")}：本轮服务器未采到完整实样本</td></tr>
<tr><td>单租户故障控制</td><td>PR449 代码未提供可直接由黑盒调用的故障注入控制面</td><td>{status("INCONCLUSIVE")}：需要部署侧提供控制接口</td></tr>
</tbody></table>
<p class="muted">本轮没有新增 PR449 提交：现有证据不足以证明 EchoMem 内部行为违反契约，先修复部署/测试前提后再复测。
O2 的故障控制优先建议由部署侧受限控制面提供，O6 则需要真实负载把每个 lane 的四元组触发出来。</p></section>
<section><h2>原始证据</h2><ul>{artifacts}</ul>
<p class="muted">各场景原始 Prometheus 采样：</p><ul>{scenario_metric_links or "<li>暂无 metrics_samples.csv</li>"}</ul>
<p class="muted">报告只引用运行目录内存在的文件，不写入 API key、密码或环境变量值。</p></section>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
