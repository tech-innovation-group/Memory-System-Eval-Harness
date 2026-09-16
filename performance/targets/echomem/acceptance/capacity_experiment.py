"""Execute one reproducible M1 hot-user exploration topology."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
import threading
import time
import urllib.request

from performance.targets.echomem.acceptance.capacity_load import measure
from performance.targets.echomem.acceptance.provenance import platform_snapshot
from performance.targets.echomem.acceptance.capacity_seed import CapacityActor, prepare_actors, provision_actors
from performance.targets.echomem.probes._client import EchoMemHTTP
from performance.targets.echomem.acceptance.capacity_statistics import evaluate_level, detect_congestion
from performance.targets.echomem.acceptance.capacity_recovery import lifecycle, observe_recovery
from performance.targets.echomem.probes.docker_inspect import inspect_container, resource_sample


_PROM_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)
_PROM_LABEL = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:\\.|[^"\\])*)"')
_PROM_HISTOGRAMS = (
    "echomem_memrouter_planning_duration_seconds",
    "echomem_memrouter_stage_duration_seconds",
    "echomem_memrouter_stage_queue_wait_seconds",
    "echomem_recall_duration_seconds",
    "echomem_router_embedding_duration_seconds",
    "echomem_engine_model_duration_seconds",
    "echomem_engine_model_ttfb_seconds",
)


def _metrics_snapshot(base_url: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    """Read one cumulative Prometheus snapshot without exposing response data."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/metrics", timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace")
    except Exception:
        return {}
    result = {}
    for line in text.splitlines():
        match = _PROM_SAMPLE.match(line.strip())
        if not match:
            continue
        try:
            value = float(match.group("value"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        labels = {
            key: raw.replace(r'\\"', '"').replace(r"\\\\", "\\")
            for key, raw in _PROM_LABEL.findall(match.group("labels") or "")
        }
        result[(match.group("name"), tuple(sorted(labels.items())))] = value
    return result


def _histogram_delta(
    before: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    after: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    *, stage: str = "memory_profile",
) -> dict:
    """Summarize a bounded stage histogram window from real cumulative samples."""
    prefix = "echomem_memrouter_stage_duration_seconds"
    buckets = {}
    count = total = 0.0
    for (name, labels), value in after.items():
        label_map = dict(labels)
        if label_map.get("stage") != stage:
            continue
        old = before.get((name, labels), 0.0)
        delta = max(0.0, value - old)
        if name == f"{prefix}_bucket":
            upper = label_map.get("le")
            if upper not in (None, "+Inf"):
                try:
                    buckets[float(upper)] = buckets.get(float(upper), 0.0) + delta
                except ValueError:
                    pass
        elif name == f"{prefix}_count":
            count += delta
        elif name == f"{prefix}_sum":
            total += delta
    def quantile(q):
        if not buckets or count <= 0:
            return None
        target = count * q
        previous_bound, previous_count = 0.0, 0.0
        for bound in sorted(buckets):
            current = buckets[bound]
            if current >= target:
                if current == previous_count:
                    return bound
                ratio = (target - previous_count) / (current - previous_count)
                return previous_bound + (bound - previous_bound) * ratio
            previous_bound, previous_count = bound, current
        return max(buckets)
    return {
        "stage": stage,
        "observations": int(count),
        "mean_s": total / count if count else None,
        "p50_s": quantile(.50),
        "p95_s": quantile(.95),
        "p99_s": quantile(.99),
        "source": "prometheus histogram delta",
        "sampled": bool(count),
    }


def _histogram_deltas(
    before: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    after: dict[tuple[str, tuple[tuple[str, str], ...]], float],
) -> list[dict]:
    """Return independent Prometheus histogram windows for one load phase.

    The service exports cumulative histograms.  Grouping by the non-``le``
    labels keeps engine/stage dimensions intact and prevents a single global
    number from hiding which module handled the request.
    """
    grouped = {}
    for (name, labels), current in after.items():
        base = next((item for item in _PROM_HISTOGRAMS if name.startswith(item + "_")), None)
        if base is None:
            continue
        label_map = dict(labels)
        key = (base, tuple(sorted((k, v) for k, v in labels if k != "le")))
        target = grouped.setdefault(key, {"buckets": {}, "count": 0.0, "sum": 0.0})
        previous = before.get((name, labels), 0.0)
        delta = current - previous if current >= previous else current
        if name.endswith("_bucket"):
            upper = label_map.get("le")
            if upper not in (None, "+Inf"):
                try:
                    target["buckets"][float(upper)] = delta
                except ValueError:
                    pass
        elif name.endswith("_count"):
            target["count"] = delta
        elif name.endswith("_sum"):
            target["sum"] = delta

    def quantile(buckets: dict[float, float], count: float, q: float):
        if count <= 0 or not buckets:
            return None
        target = count * q
        lower_bound, lower_count = 0.0, 0.0
        for bound, cumulative in sorted(buckets.items()):
            if cumulative >= target:
                if cumulative <= lower_count:
                    return bound
                fraction = (target - lower_count) / (cumulative - lower_count)
                return lower_bound + (bound - lower_bound) * fraction
            lower_bound, lower_count = bound, cumulative
        return None

    rows = []
    for (metric, label_items), values in sorted(grouped.items()):
        count = values["count"]
        if count <= 0:
            continue
        labels = dict(label_items)
        row = {
            "metric": metric,
            "labels": labels,
            "observations": int(count),
            "mean_s": values["sum"] / count if count else None,
            "p50_s": quantile(values["buckets"], count, .50),
            "p95_s": quantile(values["buckets"], count, .95),
            "p99_s": quantile(values["buckets"], count, .99),
            "source": "prometheus histogram delta",
        }
        row["module"] = metric.removeprefix("echomem_")
        if labels:
            row["module"] += " [" + ", ".join(f"{k}={v}" for k, v in label_items) + "]"
        rows.append(row)
    return rows


def _write(path: Path, value, *, private: bool = False) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        temporary.chmod(0o600)
    temporary.replace(path)


def _private_actors(actors: list) -> list[dict]:
    return [{"tenant_index": actor.tenant_index, "user_index": actor.user_index,
             "client": {key: getattr(actor.client, key) for key in
                        ("base_url", "auth_key", "tenant_id", "user_id", "account_id", "agent_id")},
             "corpus": actor.corpus, "write_session": actor.write_session} for actor in actors]


def _load_actors(directory: Path, base_url: str) -> tuple[list, dict]:
    private = directory / "identities.private.json"
    if private.stat().st_mode & 0o077:
        raise ValueError("Reused identity file must be owner-only (0600)")
    values = json.loads(private.read_text(encoding="utf-8"))
    actors = []
    for row in values:
        client = dict(row["client"])
        client["base_url"] = base_url
        actors.append(CapacityActor(row["tenant_index"], row["user_index"],
                                   EchoMemHTTP(**client), row["corpus"], row["write_session"]))
    seed = json.loads((directory / "seed-evidence.json").read_text(encoding="utf-8"))
    return actors, seed


def _select_reused_actors(actors: list, *, topology: str, tenants: int, users: int) -> list:
    """Select an exact coordinate subset from a larger validated seed cache."""
    by_coordinate = {}
    for actor in actors:
        coordinate = (actor.tenant_index, actor.user_index)
        if coordinate in by_coordinate:
            raise ValueError(f"Reused seed contains duplicate actor coordinate: {coordinate}")
        by_coordinate[coordinate] = actor
    wanted = (
        [(tenant, 0) for tenant in range(tenants)]
        if topology == "cross-tenant"
        else [(tenant, user) for tenant in range(tenants) for user in range(users)]
    )
    missing = [coordinate for coordinate in wanted if coordinate not in by_coordinate]
    if missing:
        raise ValueError(f"Reused seed is missing required actor coordinates: {missing[:5]}")
    return [by_coordinate[coordinate] for coordinate in wanted]


def _validate_reused_sessions(actors: list, *, max_checks: int = 2) -> dict:
    """Check a small identity sample before spending any load window.

    A reused seed can contain valid coordinates but expired credentials or
    sessions.  Opening a fresh session is a cheap authenticated preflight and
    also gives the sampled Commit-capable actors a current writable session.
    If a seed omitted sessions entirely, every such actor is checked because
    otherwise a later Commit would fail after the load window starts.
    """
    if not actors:
        return {"status": "BLOCKED", "reason": "no_reused_actors", "checks": []}
    sample = actors[:max(1, min(int(max_checks), len(actors)))]
    for actor in actors:
        if not actor.write_session and actor not in sample:
            sample.append(actor)
    checks = []
    for actor in sample:
        row = {"tenant_index": actor.tenant_index, "user_index": actor.user_index}
        try:
            session_id, _ = actor.client.open_session(
                actor.client.tenant_id, "capacity-reuse-preflight", retry_rate_limit=False
            )
            actor.write_session = session_id
            row.update(status="PASS", session_ref="fresh-session")
        except Exception as exc:
            row.update(status="BLOCKED", error_class=type(exc).__name__)
        checks.append(row)
    failed = [row for row in checks if row["status"] != "PASS"]
    return {
        "status": "BLOCKED" if failed else "PASS",
        "checks": checks,
        "checked_actors": len(checks),
        "failed_checks": len(failed),
        "reason": "reused-seed-auth-or-session-invalid" if failed else None,
    }


def run_exploration(*, base_url: str, output: Path, topology: str, levels: list[int],
                    fixed_tenants: int = 4, memory_scale: int = 1,
                    warmup_s: float = 30, duration_s: float = 60, q: float = 1,
                    target_container: str = "", manifest: dict | None = None,
                    assessment_mode: str = "observe", load_profile: str = "search",
                    reuse_seed: Path | None = None, seed_validation_queries: int = 40,
                    recovery_timeout_s: float = 300, request_timeout_s: float = 60,
                    persist_private_identities: bool = True, search_workers: int | None = None,
                    seed_profile: str = "standard", seed_workers: int | None = None,
                    seed_timeout_s: float = 600, seed_full_session: bool = False,
                    retry_failed: int = 1, dataset_path: str | None = None,
                    sample_id: str = "conv-30", session_key: str = "session_1",
                    session_keys: list[str] | None = None,
                    max_questions: int | None = None,
                    query_count: int | None = None,
                    fragment_seed_file: str | None = None,
                    search_schedule: str = "poisson",
                    rewrite_queries: bool = False,
                    continue_after_congestion: bool = False,
                    docker_context: str = "",
                    reuse_preflight_checks: int = 2) -> dict:
    if output.exists():
        raise FileExistsError(f"Refuse to overwrite M1 evidence directory: {output}")
    output.mkdir(parents=True, mode=0o700)
    if not levels or levels != sorted(set(levels)) or min(levels) < 1:
        raise ValueError("levels must be unique ascending positive integers")
    if topology not in {"cross-tenant", "within-tenant", "concurrency"}:
        raise ValueError("topology must be cross-tenant, within-tenant or concurrency")
    if docker_context:
        # Resource sampling and restart controls must inspect the same daemon
        # that owns the target container.
        import os
        os.environ["DOCKER_CONTEXT"] = str(docker_context)
        os.environ["ECHOMEM_DOCKER_CONTEXT"] = str(docker_context)
    aliases = {"pure": "search", "both": "search,mixed"}
    load_profile = aliases.get(load_profile, load_profile)
    allowed_profiles = {"search", "commit", "mixed", "hotspot", "search,mixed", "all"}
    if assessment_mode not in {"observe", "completion", "slo"} or load_profile not in allowed_profiles:
        raise ValueError("Invalid assessment mode or load profile")
    if (isinstance(seed_timeout_s, bool) or not isinstance(seed_timeout_s, (int, float))
            or not math.isfinite(seed_timeout_s) or seed_timeout_s <= 0):
        raise ValueError("seed_timeout_s must be finite and positive")
    if seed_full_session and max_questions is not None:
        raise ValueError("seed_full_session cannot be combined with seed max_questions")
    if seed_full_session and seed_profile != "locomo-single-session":
        raise ValueError("seed_full_session requires locomo-single-session")
    tenants = max(levels) if topology == "cross-tenant" else fixed_tenants
    users = 1 if topology in {"cross-tenant", "concurrency"} else max(levels)
    if target_container:
        state = inspect_container(target_container)
        if not state.get("State", {}).get("Running"):
            raise RuntimeError("Target container is not running")
    report = {"status": "PARTIAL" if assessment_mode == "observe" else "PREPARING",
              "topology": topology, "levels_requested": levels,
              "assessment_mode": assessment_mode, "load_profile": load_profile,
              "recovery_timeout_s": recovery_timeout_s, "request_timeout_s": request_timeout_s,
              "search_workers_requested": search_workers,
              "search_schedule": search_schedule,
              "query_rewrite": "deterministic-context-variants" if rewrite_queries else "disabled",
              "continue_after_congestion": continue_after_congestion,
              "reuse_preflight_checks": reuse_preflight_checks,
              "docker_context": docker_context or None,
              "fixed_tenants": fixed_tenants if topology == "within-tenant" else None,
              "memory_scale": memory_scale, "warmup_s": warmup_s, "duration_s": duration_s,
              "per_user_search_rps": q, "seed_profile": seed_profile,
              "seed_workers": seed_workers, "seed_timeout_s": seed_timeout_s,
              "seed_memory_policy": "full-session" if seed_full_session else (
                  "full-session" if max_questions is None and seed_profile == "locomo-single-session"
                  else "bounded-session"),
              "manifest": {**(manifest or {}),
                  "platform_provenance": platform_snapshot()}, "seed": {}, "levels": [],
              "boundary": {"status": "UNMEASURED", "highest_pass": None, "first_fail": None},
              "max_hot_users": None, "dau": None, "operational_anomalies": []}
    _write(output / "report.json", report)
    actors, reused_seed = (_load_actors(reuse_seed, base_url) if reuse_seed else
                           (provision_actors(base_url, tenants, users,
                                             memory_scale=memory_scale,
                                             corpus_mode=seed_profile,
                                             dataset_path=dataset_path,
                                             sample_id=sample_id,
                                             session_key=session_key,
                                             session_keys=session_keys,
                                             max_questions=max_questions,
                                             query_count=query_count,
                                             fragment_seed_file=fragment_seed_file), None))
    if reused_seed is not None:
        reused_actor_count = len(actors) if reused_seed is not None else None
    extension = []
    if len(actors) < tenants * users and reused_seed is not None and topology == "cross-tenant":
        if len({a.tenant_index for a in actors}) != len(actors) or any(a.user_index for a in actors):
            raise ValueError("Cross-tenant extension requires one user per tenant")
        extension = provision_actors(base_url, tenants - len(actors), 1, memory_scale=memory_scale,
                                     tenant_offset=max(a.tenant_index for a in actors) + 1,
                                     corpus_mode=seed_profile,
                                     dataset_path=dataset_path,
                                     sample_id=sample_id,
                                     session_key=session_key,
                                     session_keys=session_keys,
                                     max_questions=max_questions,
                                     query_count=query_count,
                                     fragment_seed_file=fragment_seed_file)
        actors.extend(extension)
    if reused_seed is not None:
        actors = _select_reused_actors(
            actors, topology=topology, tenants=tenants, users=users
        )
        preflight = _validate_reused_sessions(
            actors, max_checks=reuse_preflight_checks
        )
        report["reuse_seed_preflight"] = preflight
        if preflight["status"] != "PASS":
            report.update(
                status="BLOCKED", phase="reuse-seed-preflight",
                stop_reason=preflight["reason"], seed_reused=True,
                seed_status="REUSED_PRECHECK_BLOCKED",
                seed_actor_count=len(actors), current=None,
            )
            _write(output / "report.json", report)
            return report
    if len(actors) != tenants * users:
        raise ValueError("Seed identities do not match the requested maximum topology")
    if persist_private_identities:
        _write(output / "identities.private.json", _private_actors(actors), private=True)

    progress: dict[str, dict] = {}
    progress_lock = threading.Lock()

    def checkpoint(row):
        key = f"{row['tenant_index']}:{row['user_index']}"
        with progress_lock:
            progress[key] = row
            _write(output / "seed-progress.json", progress)

    if reused_seed is not None:
        seeded = {
            **reused_seed,
            "source_actor_count": reused_actor_count,
            "actor_count": len(actors),
            "actors": list(reused_seed.get("actors") or [])[:len(actors)],
        }
    else:
        seeded = prepare_actors(
            actors, checkpoint=checkpoint, validation_queries=seed_validation_queries,
            timeout_s=seed_timeout_s, workers=seed_workers, retry_failed=retry_failed)
    if extension:
        extension_seed = prepare_actors(extension, checkpoint=checkpoint,
                                        validation_queries=seed_validation_queries,
                                        timeout_s=seed_timeout_s, workers=seed_workers)
        seeded = {"actors": [*seeded.get("actors", []), *extension_seed["actors"]],
                  "status": "PASS" if seeded["status"] == extension_seed["status"] == "PASS" else "INCONCLUSIVE",
                  "actor_count": len(actors), "new_actor_count": len(extension),
                  "raw_credentials_exported": False}
    report["seed_reused"] = reused_seed is not None
    report["seed_status"] = seeded.get("status")
    report["seed_actor_count"] = len(actors)
    report["seed_source"] = "reused-seed" if reused_seed is not None else "new-seed"
    seed_rows = seeded.get("actors") if isinstance(seeded, dict) else []
    if isinstance(seed_rows, list):
        seeded["memory_policy"] = report["seed_memory_policy"]
        seeded["session_count"] = sum(
            1 for row in seed_rows
            if isinstance(row, dict) and (row.get("corpus_source") or {}).get("session_key")
        )
        seeded["total_documents"] = sum(
            int(row.get("input_documents") or 0) for row in seed_rows if isinstance(row, dict)
        )
        seeded["total_input_characters"] = sum(
            int(row.get("input_characters") or 0) for row in seed_rows if isinstance(row, dict)
        )
    report["seed"] = seeded
    if persist_private_identities:
        _write(output / "identities.private.json", _private_actors(actors), private=True)
    # Keep failed seed rows in the evidence denominator, but never send their
    # missing session/recall state into a load window. A partial seed is useful
    # when independent tenants are healthy; only zero healthy actors blocks M1.
    healthy_coordinates = {
        (int(row.get("tenant_index")), int(row.get("user_index", 0)))
        for row in seed_rows
        if isinstance(row, dict) and row.get("status") == "PASS"
    }
    # A PASS result with no actor rows is accepted for lightweight callers that
    # supply their own prepared actor state (and keeps the load phase testable).
    # Real seed preparation always emits one row per actor.
    has_seed_rows = bool(seed_rows)
    failed_seed_count = len(actors) - len(healthy_coordinates) if has_seed_rows else 0
    if failed_seed_count:
        actors = [actor for actor in actors
                  if (actor.tenant_index, actor.user_index) in healthy_coordinates]
        seeded["status"] = "PARTIAL"
        seeded["failed_actor_count"] = failed_seed_count
        seeded["healthy_actor_count"] = len(actors)
        report["seed_status"] = "PARTIAL"
        report["seed_failed_actor_count"] = failed_seed_count
        report["seed_healthy_actor_count"] = len(actors)
    if not actors:
        report.update(status="BLOCKED", phase="semantic-seed",
                      stop_reason="semantic-seed-no-healthy-actors")
        report["seed"] = seeded
        _write(output / "seed-evidence.json", seeded)
        _write(output / "report.json", report)
        return report

    # Write after partial-seed accounting so live and final evidence expose the
    # same denominator and healthy actor counts.
    report["seed"] = seeded
    _write(output / "seed-evidence.json", seeded)

    resources, phase = [], {"name": "idle", "level": None}
    stop = threading.Event()

    def collect_resources():
        while not stop.is_set():
            started = time.monotonic()
            sample_phase = dict(phase)
            try:
                row = resource_sample(target_container) if target_container else {}
            except Exception as exc:
                row = {"error_class": type(exc).__name__}
            row.update(at_epoch_s=time.time(), phase=sample_phase["name"], level=sample_phase["level"],
                       collection_s=time.monotonic() - started)
            resources.append(row)
            _write(output / "resources.json", resources)
            stop.wait(5)

    thread = threading.Thread(target=collect_resources, daemon=True)
    thread.start()
    try:
        for level_index, level in enumerate(levels):
            if topology == "cross-tenant":
                selected = [actor for actor in actors if actor.tenant_index < level]
                tenant_count, user_count = level, 1
                level_q = q
                level_workers = search_workers
                target_concurrency = None
            elif topology == "concurrency":
                selected = list(actors)
                tenant_count, user_count = fixed_tenants, 1
                target_concurrency = level
                level_workers = level
                level_q = max(q, level / max(1, len(selected)))
            else:
                selected = [actor for actor in actors if actor.user_index < level]
                tenant_count, user_count = fixed_tenants, level
                level_q = q
                level_workers = search_workers
                target_concurrency = None
            hot_users = len(selected)
            if target_container and not inspect_container(target_container).get("State", {}).get("Running"):
                report["stop_reason"] = "target-container-stopped"
                break
            modes = {
                "search,mixed": ("search", "mixed"),
                "all": ("search", "commit", "mixed", "hotspot"),
            }.get(load_profile, (load_profile,))
            for mode in modes:
                label = mode
                before = lifecycle(target_container)
                phase.update(name=label + "-warmup", level=level)
                report.update(status="PARTIAL" if assessment_mode == "observe" else "RUNNING",
                              current=dict(phase))
                _write(output / "report.json", report)
                warmup = measure(
                    selected, duration_s=warmup_s, q=level_q,
                    seed=4200 + level_index, request_timeout_s=request_timeout_s,
                    search_workers=level_workers, target_concurrency=target_concurrency,
                    load_mode=mode,
                    search_schedule=search_schedule, rewrite_queries=rewrite_queries,
                )
                _write(output / f"level-{level}-{label}-warmup.json", warmup)
                phase.update(name=label + "-measurement", level=level)
                report["current"] = dict(phase)
                _write(output / "report.json", report)
                metrics_before = _metrics_snapshot(base_url)
                measurement = measure(
                    selected, duration_s=duration_s, q=level_q,
                    seed=4300 + level_index, request_timeout_s=request_timeout_s,
                    search_workers=level_workers, target_concurrency=target_concurrency,
                    load_mode=mode,
                    search_schedule=search_schedule, rewrite_queries=rewrite_queries,
                )
                metrics_after = _metrics_snapshot(base_url)
                _write(output / f"level-{level}-{label}-measurement.json", measurement)
                result = evaluate_level(measurement, assessment_mode=assessment_mode)
                result.update(level=level, topology=topology, tenant_count=tenant_count,
                              users_per_tenant=user_count, hot_users=hot_users, memory_scale=memory_scale,
                              target_concurrency=target_concurrency,
                              search_workers_requested=level_workers,
                              offered_search_rps=level_q * len(selected),
                              server_stage_timings={
                                  "memory_profile": _histogram_delta(metrics_before, metrics_after),
                                  "prometheus_histograms": _histogram_deltas(metrics_before, metrics_after),
                              },
                              measurement_file=f"level-{level}-{label}-measurement.json")
                report["levels"].append(result)
                result["resources"] = [r for r in resources if r.get("level") == level
                                       and r.get("phase") == label + "-measurement"]
                if assessment_mode == "observe":
                    phase.update(name="recovery-observation", level=level)
                    report["current"] = dict(phase)
                    _write(output / "report.json", report)
                    recovery = observe_recovery(selected, measurement, container=target_container,
                        before=before, timeout_s=recovery_timeout_s,
                        checkpoint=lambda value: _write(output / f"level-{level}-{label}-recovery.json", value))
                    recovery["state"] = recovery.pop("status", "UNKNOWN")
                    result["recovery"] = recovery
                    congestion = detect_congestion(measurement)
                    result["congestion"] = congestion
                    if congestion["observed"]:
                        anomaly = {
                            "hot_users": hot_users, "load_profile": label,
                            "kind": "congestion", "evidence": congestion,
                            "recovered_after_load": recovery["state"],
                            "continued_after_detection": continue_after_congestion,
                        }
                        report.setdefault("operational_anomalies", []).append(anomaly)
                        report.setdefault("operational_boundary", anomaly)
                        if not continue_after_congestion:
                            report["stop_reason"] = "sustained-service-congestion"
                    _write(output / f"level-{level}-{label}-recovery.json", recovery)
                    if recovery["state"] == "BOUNDARY_OBSERVED":
                        report["stop_reason"] = recovery["reason"]
                        report["operational_boundary"] = {"hot_users": hot_users, "load_profile": label,
                                                           "evidence": recovery}
                _write(output / "report.json", report)
                if report.get("stop_reason"):
                    break
            passes = [row for row in report["levels"] if row["status"] == "PASS"]
            failures = [row for row in report["levels"] if row["status"] == "FAIL"]
            report["boundary"] = {"status": "EXPLORING",
                                  "highest_pass": max((row["hot_users"] for row in passes), default=None),
                                  "first_fail": min((row["hot_users"] for row in failures), default=None)}
            report.update(status="PARTIAL" if assessment_mode == "observe" else "RUNNING",
                          phase="capacity-exploration")
            _write(output / "report.json", report)
            if report.get("stop_reason") or assessment_mode == "slo" and result["status"] == "FAIL":
                break
    finally:
        stop.set()
        thread.join()
    if assessment_mode == "observe":
        observed = [r for r in report["levels"] if r["search"]["sent"]]
        report.update(status="PARTIAL" if report.get("stop_reason") else "MEASURED",
                      phase="capacity-observation-complete", current=None, resources=resources,
                      highest_measured_hot_users=max((r["hot_users"] for r in observed), default=None),
                      max_hot_users=None, dau=None, performance_requirements_applied=False,
                      boundary=({"status": "CONGESTION_OBSERVED",
                                 "reason": "sustained-service-congestion",
                                 "first_congested_hot_users": report["operational_boundary"]["hot_users"]}
                                if report.get("stop_reason") == "sustained-service-congestion"
                                else ({"status": "CONGESTION_OBSERVED_CONTINUED",
                                       "reason": "sustained-service-congestion",
                                       "first_congested_hot_users": report["operational_boundary"]["hot_users"],
                                       "levels_completed_after_detection": len(observed)}
                                      if report.get("operational_boundary")
                                      else {"status": "NOT_ESTABLISHED", "reason": "observation-without-performance-thresholds"})))
        _write(output / "report.json", report)
        return report
    highest = report["boundary"]["highest_pass"]
    failed = report["boundary"]["first_fail"]
    if highest is not None and failed is not None and highest < failed:
        boundary_status, status = "CANDIDATE", "INCONCLUSIVE"
    elif highest is not None:
        boundary_status, status = "LOWER_BOUND", "INCONCLUSIVE"
    else:
        boundary_status, status = "NO_PASS", "BLOCKED"
    report["boundary"]["status"] = boundary_status
    report.update(status=status, phase="capacity-exploration-complete", resources=resources,
                  evidence_complete=False, max_hot_users=None, dau=None)
    _write(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topology", choices=("cross-tenant", "within-tenant", "concurrency"), required=True)
    parser.add_argument("--levels", default="1,2,4,8,12,16")
    parser.add_argument("--fixed-tenants", type=int, default=4)
    parser.add_argument("--memory-scale", type=int, choices=(1, 10), default=1)
    parser.add_argument("--warmup-s", type=float, default=30)
    parser.add_argument("--duration-s", type=float, default=60)
    parser.add_argument("--per-user-search-rps", type=float, default=1)
    parser.add_argument("--target-container", default="")
    parser.add_argument("--docker-context", default="",
                        help="Docker context containing the target container")
    parser.add_argument("--manifest-json", default="{}")
    parser.add_argument("--assessment-mode", choices=("observe", "completion", "slo"), default="observe")
    parser.add_argument(
        "--load-profile",
        choices=("search", "commit", "mixed", "hotspot", "all", "pure", "both"),
        default="search",
        help="M1 load shape; pure/both are retained as legacy aliases",
    )
    parser.add_argument("--reuse-seed", type=Path,
                        help="server-local previous run containing owner-only identities.private.json")
    parser.add_argument("--reuse-preflight-checks", type=int, default=2,
                        help="Maximum reused identities authenticated before load windows")
    parser.add_argument("--seed-validation-queries", type=int, default=40)
    parser.add_argument("--recovery-timeout-s", type=float, default=300)
    parser.add_argument("--request-timeout-s", type=float, default=60)
    parser.add_argument("--search-workers", type=int, help="Explicit client Search worker count; independent of service limits")
    parser.add_argument("--seed-timeout-s", type=float, default=600,
                        help="Per-tenant Commit terminal wait; full real sessions need a long timeout")
    parser.add_argument("--full-session", action="store_true",
                        help="Require the selected LoCoMo session to be injected in full")
    parser.add_argument("--dataset-path", default="", help="LoCoMo dataset path for locomo-single-session seeds")
    parser.add_argument("--sample-id", default="conv-30")
    parser.add_argument("--session-key", default="session_1")
    parser.add_argument("--session-keys", default="",
                        help="Comma-separated per-tenant LoCoMo sessions; repeats deterministically if shorter than tenants")
    parser.add_argument("--seed-max-questions", type=int,
                        help="Keep only the first N real recall questions and their evidence documents per tenant")
    parser.add_argument("--seed-query-count", type=int,
                        help="Keep the full session memory but use only the first N real recall questions for Search")
    parser.add_argument("--search-schedule", choices=("poisson", "fixed-interval"), default="poisson",
                        help="Search arrival schedule; fixed-interval emits one request per interval per tenant")
    parser.add_argument("--rewrite-queries", action="store_true",
                        help="Rotate deterministic natural-language context variants of recall questions")
    args = parser.parse_args()
    result = run_exploration(base_url=args.base_url, output=args.output, topology=args.topology,
        levels=[int(item) for item in args.levels.split(",")], fixed_tenants=args.fixed_tenants,
        memory_scale=args.memory_scale, warmup_s=args.warmup_s, duration_s=args.duration_s,
        q=args.per_user_search_rps, target_container=args.target_container,
        manifest=json.loads(args.manifest_json), assessment_mode=args.assessment_mode,
        load_profile=args.load_profile, reuse_seed=args.reuse_seed,
        seed_validation_queries=args.seed_validation_queries, recovery_timeout_s=args.recovery_timeout_s,
        request_timeout_s=args.request_timeout_s, search_workers=args.search_workers,
        seed_timeout_s=args.seed_timeout_s, seed_full_session=args.full_session,
        dataset_path=args.dataset_path, sample_id=args.sample_id, session_key=args.session_key,
        session_keys=[item.strip() for item in args.session_keys.split(",") if item.strip()] or None,
        max_questions=args.seed_max_questions, query_count=args.seed_query_count,
        search_schedule=args.search_schedule, rewrite_queries=args.rewrite_queries,
        docker_context=args.docker_context,
        reuse_preflight_checks=args.reuse_preflight_checks)
    print(json.dumps({"status": result["status"], "phase": result["phase"],
                      "boundary": result["boundary"], "levels": [{"level": row["level"],
                      "hot_users": row["hot_users"], "status": row["status"]}
                      for row in result["levels"]]}))


if __name__ == "__main__":
    main()
