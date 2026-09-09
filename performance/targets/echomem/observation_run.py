"""Run the EchoMem M1-M6 observation suite and publish one report."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from performance.targets.echomem.acceptance.capacity_experiment import run_exploration
from performance.targets.echomem.acceptance.observation import (
    METRIC_NAMES,
    evaluate_observation,
    write_observation_report,
)
from performance.targets.echomem.acceptance.readiness import check_readiness
from performance.targets.echomem.acceptance.provenance import platform_snapshot
from performance.targets.echomem.acceptance.preflight import run_preflight
from performance.targets.echomem.acceptance.stage_observability import (
    collect_container_stage_events,
)
from performance.targets.echomem.main import _resolve_profile, load_profiles
from performance.targets.echomem.orchestrator.probes import run_configured_probes
from performance.targets.echomem.orchestrator.runner import run_suite
from performance.targets.echomem.orchestrator.suites import QuickSpec
from performance.targets.echomem.probes._client import load_tenant_specs
from performance.targets.echomem.probes.docker_inspect import inspect_container
from performance.targets.echomem.probes.tenant_observability import expected_lanes_from_config
from performance.targets.echomem.probes.tenant_observability import collect as collect_tenant_observability
from performance.util import acquire_output_lock, load_env_file, read_json


class PublishedObservationError(RuntimeError):
    """A failed phase whose partial evidence has already been published."""

    def __init__(self, message: str, result: dict[str, Any]):
        super().__init__(message)
        self.result = result


DEFAULT_M1_LEVELS = [1, 2, 4, 8, 16, 32]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metrics(value: str) -> list[str]:
    selected = [item.strip().upper() for item in value.split(",") if item.strip()]
    unknown = [item for item in selected if item not in METRIC_NAMES]
    if unknown or not selected:
        raise ValueError("metrics must be a comma-separated subset of M1,M2,M3,M4,M5,M6")
    return list(dict.fromkeys(selected))


def _git_commit() -> str | None:
    return platform_snapshot()["git_commit"]


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name", "base_url", "resource_container", "capacity_levels",
        "require_4u8g",
        "m1_tenant_levels", "m1_user_levels", "dau_scenarios",
        "required_concurrency", "required_embedding_model",
        "require_stage_observability",
        "preflight_config", "tenant_config",
    }
    return {key: profile.get(key) for key in allowed if profile.get(key) not in (None, "")}


def _combine_csv(suite: dict[str, Any], output: Path, filename: str) -> None:
    sources = []
    for run in suite.get("runs", []):
        path = Path(str(run.get("output_dir") or "")) / filename
        if path.is_file():
            sources.append((str(run.get("scenario") or ""), path))
    destination = output / filename
    fieldnames: list[str] = ["scenario"]
    rows = []
    for scenario, path in sources:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                row = {"scenario": scenario, **row}
                rows.append(row)
                for key in row:
                    if key not in fieldnames:
                        fieldnames.append(key)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _m1_levels(profile: dict[str, Any], key: str, defaults: list[int]) -> list[int]:
    configured = profile.get(key)
    return defaults if configured is None else [int(value) for value in configured]


def _validate_m1_resume(report: dict[str, Any], expected: dict[str, Any]) -> None:
    mismatches = [
        key for key, value in expected.items()
        if report.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "M1 resume configuration differs for " + ", ".join(mismatches)
        )


def _collect_observation(profile: dict[str, Any], token: str) -> dict[str, Any]:
    observation = profile.get("tenant_observability") or {}
    result = collect_tenant_observability(
        base_url=profile["base_url"], endpoint=str(observation.get("endpoint", "")),
        token=token, expected_tenants=list(observation.get("expected_tenants", [])),
        expected_lanes=list(observation.get("expected_lanes", [])), timeout_s=15,
    )
    container = str(profile.get("resource_container") or "")
    if container and not (result.get("boot_id") or result.get("process_started_at")):
        try:
            state = inspect_container(container).get("State") or {}
            if state.get("StartedAt"):
                result["process_started_at"] = state["StartedAt"]
                result["process_id"] = state.get("Pid")
                result["process_identity_source"] = "container-state"
        except Exception as exc:
            result["process_identity_error"] = type(exc).__name__
    result["observed_at_s"] = time.monotonic()
    return result


def _run_m1_profiles(profile: dict[str, Any], args: argparse.Namespace, output: Path) -> list[dict]:
    reports = []
    levels_by_topology = {
        "cross-tenant": _m1_levels(profile, "m1_tenant_levels", [1, 2] if args.quick else DEFAULT_M1_LEVELS),
        "within-tenant": _m1_levels(profile, "m1_user_levels", [1, 2] if args.quick else DEFAULT_M1_LEVELS),
    }
    for topology, levels in levels_by_topology.items():
        target = output / "M1" / topology
        report_path = target / "report.json"
        duration_s = 15 if args.quick else float(profile.get("m1_duration_s", 300))
        warmup_s = 5 if args.quick else 30
        expected_resume = {"topology": topology, "levels_requested": levels,
            "assessment_mode": "observe", "load_profile": "all", "warmup_s": warmup_s,
            "duration_s": duration_s, "per_user_search_rps": float(profile.get("m1_search_rps_per_user", 1))}
        if args.resume and report_path.is_file():
            resumed_report = read_json(report_path)
            _validate_m1_resume(resumed_report, expected_resume)
            reports.append(resumed_report)
            continue
        reports.append(run_exploration(base_url=profile["base_url"], output=target,
            topology=topology, levels=levels, fixed_tenants=4, warmup_s=warmup_s,
            duration_s=duration_s, q=float(profile.get("m1_search_rps_per_user", 1)),
            target_container=str(profile.get("resource_container") or ""),
            manifest={"resource_evidence": profile["resource_evidence"]},
            assessment_mode="observe", load_profile="all",
            seed_validation_queries=4 if args.quick else 40,
            recovery_timeout_s=30 if args.quick else 300, persist_private_identities=False))
    return reports


def _sample_observability(stop, collect, samples, errors, output: Path) -> None:
    try:
        while not stop.is_set():
            try:
                sample = collect()
            except Exception as exc:
                errors.append(type(exc).__name__)
                sample = {"status": "FAIL", "observed_at_s": time.monotonic(),
                          "reason": type(exc).__name__, "rows": []}
            samples.append(sample)
            (output / "tenant-observability-samples.json").write_text(
                json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stop.wait(2)
    except Exception as exc:
        # Exception text can contain a protected endpoint or credentials.
        errors.append(type(exc).__name__)


def _validate_stage_observability_config(
    profile: dict[str, Any], selected: list[str]
) -> None:
    if not profile.get("require_stage_observability") or not set(selected) & {"M1", "M2", "M3"}:
        return
    document = read_json(Path(profile["preflight_config"]))
    runtime = document.get("runtime") if isinstance(document.get("runtime"), dict) else {}
    logging = document.get("logging") if isinstance(document.get("logging"), dict) else {}
    if str(runtime.get("log_level") or "").upper() != "DEBUG":
        raise ValueError("M1-M3 stage observability requires runtime.log_level=DEBUG")
    if str(logging.get("format") or "").lower() != "json":
        raise ValueError("M1-M3 stage observability requires logging.format=json")
    if not profile.get("resource_container"):
        raise ValueError("M1-M3 stage observability requires resource_container for bounded Docker log collection")


def _configure(profile: dict[str, Any], selected: list[str], *, quick: bool) -> dict[str, Any]:
    needs_m6_behaviors = "M6" in selected
    m6_only = set(selected) == {"M6"}
    needs_fault = "M4" in selected or needs_m6_behaviors
    readiness = check_readiness({
        **profile,
        "fault_isolation": {**(profile.get("fault_isolation") or {}), "enabled": needs_fault},
        "tenant_observability": {**(profile.get("tenant_observability") or {}),
                                  "enabled": needs_m6_behaviors},
    })
    if not readiness.get("ok"):
        raise RuntimeError(json.dumps(readiness, ensure_ascii=False))
    if not profile.get("preflight_config"):
        raise ValueError("preflight_config is required for real LLM and embedding verification")
    _validate_stage_observability_config(profile, selected)
    model_preflight = run_preflight(
        profile["preflight_config"], required_kinds=("llm", "embedding")
    )
    if not model_preflight.get("ok"):
        raise RuntimeError(json.dumps(model_preflight, ensure_ascii=False))
    required_embedding = str(profile.get("required_embedding_model") or "").strip()
    observed_embeddings = {
        str(engine.get("model") or "") for engine in model_preflight.get("engines", [])
        if engine.get("kind") == "embedding" and engine.get("status") == "ok"
    }
    if required_embedding and required_embedding not in observed_embeddings:
        raise ValueError(
            f"required embedding model {required_embedding!r} was not verified; "
            f"observed {sorted(observed_embeddings)!r}"
        )
    required_concurrency = int(profile.get("required_concurrency") or 0)
    if "M1" in selected and not quick and required_concurrency > 0:
        configured_levels = [
            *_m1_levels(profile, "m1_tenant_levels", DEFAULT_M1_LEVELS),
            *_m1_levels(profile, "m1_user_levels", DEFAULT_M1_LEVELS),
        ]
        if max(configured_levels, default=0) < required_concurrency:
            raise ValueError(
                f"M1 levels stop below required_concurrency={required_concurrency}; "
                "increase m1_tenant_levels or m1_user_levels"
            )
    tenant_document = read_json(Path(profile["tenant_config"]))
    configured_tenants = tenant_document.get("tenants", [])
    for tenant in configured_tenants:
        if tenant.get("auth_key"):
            raise ValueError("Observation runs forbid auth_key in tenant files; use auth_key_env")
        env_name = str(tenant.get("auth_key_env") or "")
        if not env_name or not os.environ.get(env_name, ""):
            raise ValueError("Every observation tenant requires a non-empty auth_key_env")
    specs = load_tenant_specs(profile["tenant_config"])
    required = 8 if "M2" in selected else 4
    if len(specs) < required or len({spec.auth_key for spec in specs[:required]}) != required:
        raise ValueError(f"{required} independently authenticated tenants are required")
    tenant_ids = [spec.tenant_id for spec in specs[:required]]
    lanes = expected_lanes_from_config(profile["preflight_config"])
    if "M6" in selected and not lanes:
        raise ValueError("No effective scheduler lanes could be derived from preflight_config")
    base_url = str(profile.get("base_url") or "").rstrip("/")
    phase = 15 if quick or m6_only else 60
    fault = {
        "enabled": "M4" in selected or needs_m6_behaviors,
        "endpoint": base_url + "/api/inspect/test-control/fault",
        "token_env": "ECHOMEM_TEST_CONTROL_TOKEN",
        "samples": 10 if quick else 100,
        "repeats": 3,
        "phase_duration_s": phase,
        "duration_s": min(300, phase * 3),
        "search_rps_per_tenant": 2,
        "target_rps": 1,
        **(profile.get("fault_isolation") or {}),
        "observation_only": True,
        "behavior_case_only": m6_only,
    }
    recovery = {
        "enabled": "M5" in selected or needs_m6_behaviors,
        "tenant": tenant_ids[0],
        "container": profile.get("resource_container", ""),
        "messages": 4 if m6_only else 12, "content_chars": 1000,
        "samples": 1 if m6_only else 3, "require_accepted_202": True,
        "expected_container_id": readiness["resource_evidence"].get("container_id"),
        "expected_image_id": readiness["resource_evidence"].get("image_id"),
        **(profile.get("commit_recovery") or {}),
    }
    if ("M5" in selected or needs_m6_behaviors) and recovery.get("allow_container_restart") is not True:
        raise ValueError("M5/M6 RESET requires commit_recovery.allow_container_restart=true for the dedicated target container")
    return {
        **profile,
        "six_metrics": False,
        "six_metrics_observation": True,
        "resource_evidence": readiness["resource_evidence"],
        "readiness": readiness,
        "model_preflight": model_preflight,
        "seed_sessions": 1,
        "seed_messages": 1,
        "allow_partial_tenants": False,
        "metrics_enabled": True,
        "invalid_input": {"enabled": True, "token_env": "ECHOMEM_TEST_CONTROL_TOKEN",
                          **(profile.get("invalid_input") or {})},
        "fault_isolation": fault if ("M4" in selected or needs_m6_behaviors) else {"enabled": False},
        "tenant_observability": {
            **(profile.get("tenant_observability") or {}),
            "enabled": "M6" in selected,
            "expected_tenants": tenant_ids,
            "expected_lanes": lanes,
            "token_env": "ECHOMEM_TEST_CONTROL_TOKEN",
        },
        "commit_recovery": recovery if ("M5" in selected or needs_m6_behaviors) else None,
        "fairness_expectations": {"tenant_ids": tenant_ids[:4]},
    }


def run(args: argparse.Namespace, *, output_lock=None) -> dict[str, Any]:
    if args.env_file:
        os.environ.update(load_env_file(args.env_file.expanduser().resolve()))
    profiles = load_profiles(args.profiles)
    profile_name = args.profile
    if profile_name is None:
        if len(profiles) != 1:
            raise ValueError("--profile is required when the profile file contains more than one profile")
        profile_name = str(profiles[0].get("name") or "")
    matches = [item for item in profiles if str(item.get("name")) == profile_name]
    if len(matches) != 1:
        raise ValueError(f"profile {profile_name!r} was not found exactly once")
    selected = _metrics(args.metrics)
    m6_only = set(selected) == {"M6"}
    profile = _configure(
        _resolve_profile(matches[0], args.profiles), selected, quick=args.quick
    )
    output = args.out_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock = output_lock if output_lock is not None else acquire_output_lock(output)
    started_at = _now()
    provenance = platform_snapshot()
    (output / "execution-manifest.json").write_text(json.dumps({
        "schema_version": 2,
        "metric_numbering": "capacity-fairness-priority-isolation-recovery-observability-v2",
        "started_at": started_at, "finished_at": None,
        "git_commit": provenance["git_commit"], "platform_provenance": provenance, "selected_metrics": selected,
        "sampling_mode": "quick-non-complete" if args.quick else "full",
        "soak_enabled": False, "execution_status": "PARTIAL",
        "real_http_required": True, "real_llm_required": True,
        "real_embedding_required": True,
        "credentials_source": "environment variables only",
        "profile": _public_profile(profile),
        "model_preflight": profile.get("model_preflight"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        m1_reports = []
        observation = profile.get("tenant_observability") or {}
        observation_samples: list[dict[str, Any]] = []
        observation_errors: list[str] = []
        observation_monitor: dict[str, Any] = {"errors": observation_errors,
            "max_sampling_gap_s": observation.get("max_sampling_gap_s", 20)}
        sampler_stop = threading.Event()
        sampler = None
        token = os.environ.get(str(observation.get("token_env") or "ECHOMEM_TEST_CONTROL_TOKEN"), "")
        observation_before: dict[str, Any] = {}
        if observation.get("enabled") and token:
            observation_before = _collect_observation(profile, token)
            (output / "tenant-observability-before.json").write_text(
                json.dumps(observation_before, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            observation_monitor["window_start_s"] = time.monotonic()
            sampler = threading.Thread(target=_sample_observability,
                args=(sampler_stop, lambda: _collect_observation(profile, token),
                      observation_samples, observation_errors, output), daemon=True)
            sampler.start()

        suite = {"runs": [], "output_root": str(output), "instance_profile": profile["name"],
                 "resource_evidence": profile["resource_evidence"], "readiness": profile["readiness"]}

        def refresh_stage_observability() -> None:
            evidence = collect_container_stage_events(
                str(profile.get("resource_container") or ""),
                since=started_at,
                output=output / "structured-stage-events.jsonl",
            )
            evidence.pop("events", None)
            suite["stage_observability"] = evidence

        def publish_stage(pending, error=None):
            if error is not None and sampler is not None:
                sampler_stop.set()
                sampler.join(timeout=20)
            if sampler is not None:
                observation_monitor["window_end_s"] = time.monotonic()
            suite["tenant_observability_samples"] = list(observation_samples)
            suite["tenant_observability_monitor"] = dict(observation_monitor)
            refresh_stage_observability()
            result = evaluate_observation(suite, profile, m1_reports, quick=args.quick, selected_metrics=selected)
            result.update(platform_provenance=provenance, checkpoint=error is None, pending_metrics=pending)
            if "capacity_start_readiness" in suite:
                result["capacity_start_readiness"] = suite["capacity_start_readiness"]
            if error is not None:
                result.update(status="EXECUTION_ERROR", error_class=type(error).__name__)
                manifest_path = output / "execution-manifest.json"
                manifest = read_json(manifest_path)
                manifest.update(execution_status="EXECUTION_ERROR", finished_at=_now(), error_class=type(error).__name__)
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (output / "suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _combine_csv(suite, output, "records.csv")
            _combine_csv(suite, output, "metrics_samples.csv")
            write_observation_report(result, output / "report.html")
            return result

        if "M1" in selected:
            publish_stage(selected)
            try:
                suite["capacity_start_readiness"] = check_readiness(profile)
                if not suite["capacity_start_readiness"].get("ok"):
                    raise RuntimeError("capacity_control_preflight_failed")
                m1_reports = _run_m1_profiles(profile, args, output)
                suite["m1"] = {"reports": [{"topology": report.get("topology"), "status": report.get("status"),
                    "path": str(output / "M1" / str(report.get("topology")) / "report.json")} for report in m1_reports]}
                if any((level.get("recovery") or {}).get("state") not in {"RECOVERED", "NO_BOUNDARY_OBSERVED"}
                       for report in m1_reports for level in report.get("levels", [])):
                    raise RuntimeError("capacity_recovery_unproven_before_next_metric")
                if not check_readiness(profile).get("ok"):
                    raise RuntimeError("post_capacity_readiness_failed")
            except Exception as exc:
                result = publish_stage(selected, exc)
                raise PublishedObservationError(str(exc), result) from exc
            publish_stage([code for code in selected if code != "M1"])

        load_metrics = [name for name in selected if name in {"M2", "M3"}]
        scenarios = []
        if "M2" in load_metrics:
            scenarios.extend(("m2-fairness-4t", "m2-fairness-8t"))
        if "M3" in load_metrics:
            scenarios.extend(("m3-baseline", "m3-flood-uniform", "m3-flood-single-tenant",
                              "m3-heterogeneous-tenants"))
        if "M4" in selected and "m3-baseline" not in scenarios:
            scenarios.append("m3-baseline")
        if "M6" in selected:
            for dependency in ("m3-baseline", "m3-flood-uniform"):
                if dependency not in scenarios:
                    scenarios.append(dependency)
        if scenarios:
            quick_spec = (
                QuickSpec(
                    duration_cap_s=45,
                    barrier_count_cap=8,
                    include_seed=True,
                    commit_poll_timeout_cap_s=45 if m6_only else None,
                )
                if args.quick or m6_only else None
            )
            try:
                suite = run_suite(
                    profile, suite_dir=output, quick=quick_spec,
                    profile_name="six-metrics-observation", base_url=profile["base_url"],
                    timeout_s=args.timeout_s, scenarios=scenarios, resume=args.resume,
                )
            except Exception as exc:
                result = publish_stage([code for code in selected if code != "M1"], exc)
                raise PublishedObservationError(str(exc), result) from exc
        else:
            suite = {"runs": [], "output_root": str(output),
                     "instance_profile": profile["name"],
                     "resource_evidence": profile["resource_evidence"],
                     "readiness": profile["readiness"]}

        suite["m1"] = {
            "reports": [{"topology": report.get("topology"), "status": report.get("status"),
                "path": str(output / "M1" / str(report.get("topology")) / "report.json")}
                for report in m1_reports]
        }
        visibility = (suite.get("seed") or {}).get("visibility", [])
        probe_queries = (suite.get("seed") or {}).get("probe_queries")
        if probe_queries and isinstance(profile.get("fault_isolation"), dict):
            profile["fault_isolation"] = {**profile["fault_isolation"], "queries": probe_queries}
        elif visibility and isinstance(profile.get("fault_isolation"), dict):
            profile["fault_isolation"] = {
                **profile["fault_isolation"],
                "queries": {row["tenant_id"]: row["marker"] for row in visibility},
            }
        tenant_config = read_json(Path(profile["tenant_config"]))
        pending = [code for code in selected if code in {"M4", "M5", "M6"}]
        early_report = None
        if scenarios and pending:
            early_report = evaluate_observation(suite, profile, m1_reports, quick=args.quick, selected_metrics=selected)
            early_report.update(platform_provenance=provenance, checkpoint=True, pending_metrics=pending)
            (output / "suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (output / "summary.json").write_text(json.dumps(early_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _combine_csv(suite, output, "records.csv")
            _combine_csv(suite, output, "metrics_samples.csv")
            write_observation_report(early_report, output / "report.html")
        try:
            probes, commands = run_configured_probes(
                profile, base_url=profile["base_url"], suite_dir=output,
                auth_headers={}, tenant_config=tenant_config, quick=args.quick,
                timeout_s=args.timeout_s,
            )
        except Exception as exc:
            if early_report is None:
                raise
            early_report.update(status="EXECUTION_ERROR", checkpoint=False, error_class=type(exc).__name__)
            (output / "summary.json").write_text(json.dumps(early_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_observation_report(early_report, output / "report.html")
            manifest_path = output / "execution-manifest.json"
            manifest = read_json(manifest_path)
            manifest.update(execution_status="EXECUTION_ERROR", finished_at=_now(), error_class=type(exc).__name__)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise PublishedObservationError(str(exc), early_report) from exc
        suite = {**suite, **probes}
        if observation.get("enabled"):
            suite["tenant_observability_before"] = observation_before
        def snapshot_observability(*, stop: bool):
            if sampler is not None:
                if stop:
                    sampler_stop.set()
                    sampler.join(timeout=20)
                    if sampler.is_alive():
                        observation_errors.append("SamplerStopTimeout")
                observation_monitor["window_end_s"] = time.monotonic()
            if observation.get("enabled"):
                after_all = _collect_observation(profile, token)
                suite["tenant_observability_after_all"] = after_all
                (output / "tenant-observability-after-all.json").write_text(
                    json.dumps(after_all, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            suite["tenant_observability_samples"] = list(observation_samples)
            suite["tenant_observability_monitor"] = {**observation_monitor, "errors": list(observation_errors)}

        snapshot_observability(stop=True)
        refresh_stage_observability()
        (output / "suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _combine_csv(suite, output, "records.csv")
        _combine_csv(suite, output, "metrics_samples.csv")
        result = evaluate_observation(
            suite, profile, m1_reports, quick=args.quick,
            selected_metrics=selected,
        )
        result["platform_provenance"] = provenance
        (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": 1, "started_at": started_at, "finished_at": _now(),
            "git_commit": provenance["git_commit"], "platform_provenance": provenance, "selected_metrics": selected,
            "sampling_mode": result["sampling_mode"], "soak_enabled": False,
            "real_http_required": True, "real_llm_required": True,
            "real_embedding_required": True,
            "credentials_source": "environment variables; tenant config contains env names only",
            "execution_status": result["status"],
            "profile": _public_profile(profile),
            "model_preflight": profile.get("model_preflight"),
            "probe_executions": commands,
        }
        (output / "execution-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_observation_report(result, output / "report.html")
        return result
    finally:
        if "sampler_stop" in locals():
            sampler_stop.set()
        if "sampler" in locals() and sampler is not None and sampler.is_alive():
            sampler.join(timeout=20)
        if output_lock is None:
            lock.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--profile", help="profile name; optional when the file contains exactly one profile")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--metrics", default="M1,M2,M3,M4,M5,M6")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=7200)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    interrupted = False
    output = args.out_dir.expanduser().resolve()
    lock = None
    try:
        output.mkdir(parents=True, exist_ok=True)
        lock = acquire_output_lock(output)
        if (output / "summary.json").exists() and not args.resume:
            print("Output already contains results; use a new directory or --resume.", file=sys.stderr)
            return 2
        result = run(args, output_lock=lock)
    except PublishedObservationError as exc:
        result = exc.result
    except (ValueError, RuntimeError, OSError, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, KeyboardInterrupt)
        # A contender must never publish an error over the owning run's evidence.
        if lock is None or (output / "summary.json").exists():
            print(f"{type(exc).__name__}: {exc}; existing output left unchanged.", file=sys.stderr)
            return 130 if interrupted else 2
        try:
            selected = _metrics(args.metrics)
        except ValueError:
            selected = []
        status = ("PARTIAL" if interrupted else "BLOCKED"
                  if isinstance(exc, (ValueError, RuntimeError)) else "EXECUTION_ERROR")
        blockers = []
        model_preflight = {}
        if isinstance(exc, RuntimeError):
            try:
                detail = json.loads(str(exc))
                blockers = [check for check in detail.get("checks", [])
                            if check.get("status") == "BLOCKED"]
                if isinstance(detail.get("engines"), list):
                    model_preflight = detail
            except (TypeError, ValueError):
                blockers = []
        concise_reason = (
            f"环境预检阻塞：{', '.join(str(item.get('name')) for item in blockers)}"
            if blockers else f"{type(exc).__name__}: {exc}"
        )
        result = {
            "schema_version": 1, "assessment": "observation-only",
            "instance_profile": args.profile or "local",
            "performance_thresholds_applied": False,
            "sampling_mode": "quick-non-complete" if args.quick else "full",
            "status": status, "selected_metrics": selected,
            "model_preflight": model_preflight,
            "allowed_statuses": ["MEASURED", "PARTIAL", "BLOCKED", "EXECUTION_ERROR"],
            "metrics": {
                code: {"status": status if code in selected else "BLOCKED",
                       "reason": concise_reason if code in selected else "本次命令未选择该指标"}
                for code in METRIC_NAMES
            },
        }
        (output / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output / "execution-manifest.json").write_text(json.dumps({
            "schema_version": 1, "started_at": _now(), "finished_at": _now(),
            "git_commit": _git_commit(), "selected_metrics": selected,
            "soak_enabled": False, "execution_status": status,
            "error_class": type(exc).__name__, "error": str(exc),
            "blockers": blockers, "model_preflight": model_preflight,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_observation_report(result, output / "report.html")
    finally:
        if lock is not None:
            lock.close()
    print(args.out_dir.expanduser().resolve() / "report.html")
    if interrupted:
        return 130
    return 0 if result["status"] not in {"BLOCKED", "EXECUTION_ERROR"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
