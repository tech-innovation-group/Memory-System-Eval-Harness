#!/usr/bin/env python3
"""Run the six 4U8G EchoMem target checks from one profile-aware entry point.

The suite orchestrates existing real-HTTP probes. It does not mock the target
service and does not change EchoMem code. Missing deployment controls are
reported as INCONCLUSIVE instead of being silently skipped.
"""

from __future__ import annotations

import argparse
import csv
import errno
import fcntl
import html
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from .scheduler_acceptance import evaluate as evaluate_scheduler_acceptance
except ImportError:
    from scheduler_acceptance import evaluate as evaluate_scheduler_acceptance

OBJECTIVES = [
    ("O1", "单实例最大用户量 / 热用户量"),
    ("O2", "单租户故障下 Search P95 劣化 <= 20%"),
    ("O3", "多租户公平性 Jain >= 0.9"),
    ("O4", "Commit 洪泛时 Search P95 <= 5s"),
    ("O5", "202 Commit 崩溃恢复后 100% 重放且不丢序"),
    ("O6", "每层每租户四元组可观测指标"),
]
INCONCLUSIVE = "INCONCLUSIVE"
PASS = "PASS"
FAIL = "FAIL"

# The quick matrix is intended to return actionable black-box observations
# on a real model within a bounded window.  The report4/report6 A/B/D cases
# duplicate the same read/write/barrier signals and are too expensive when
# every real Commit is polled to completion.  Full acceptance still uses the
# complete catalog; quick mode is explicitly a smoke/diagnostic run.
QUICK_SCENARIOS = (
    "baseline,fairness-bounded,search-priority-blackbox,"
    "saturation,tenant-skew,capacity-2,capacity-4,capacity-8,"
    "capacity-16,capacity-32"
)

PLATFORM_OBJECTIVE_REQUIREMENTS = {
    "O1": {
        "name": "单实例最大用户量 / 热用户量",
        "scenarios": {
            "capacity-2",
            "capacity-4",
            "capacity-8",
            "capacity-16",
            "capacity-32",
        },
        "probes": set(),
        "owner": "测试平台 + 部署资源",
    },
    "O2": {
        "name": "单租户故障下 Search P95 劣化",
        "scenarios": set(),
        "probes": {"fault_isolation", "fault_suite"},
        "owner": "部署故障控制 + 测试平台采集",
    },
    "O3": {
        "name": "多租户 Commit/Search 公平性 Jain",
        "scenarios": {"fairness-steady", "fairness-bounded"},
        "scenario_mode": "any",
        "probes": set(),
        "owner": "测试平台负载 + 独立租户凭据",
    },
    "O4": {
        "name": "Commit 洪泛时 Search 优先级",
        "scenarios": {"search-priority-blackbox"},
        "probes": set(),
        "owner": "测试平台负载 + EchoMem 调度",
    },
    "O5": {
        "name": "202 Commit 崩溃恢复与重放",
        "scenarios": set(),
        "probes": {"commit_recovery", "blackbox_probe"},
        "owner": "EchoMem 持久化 + 测试平台重启/对账",
    },
    "O6": {
        "name": "每层/每租户四元组可观测性",
        "scenarios": {
            "baseline",
            "fairness-steady",
            "fairness-bounded",
            "search-priority-blackbox",
        },
        "scenario_mode": "all_except_fairness",
        "probes": {"capability_probe"},
        "owner": "EchoMem /metrics + 测试平台校验",
    },
}


def _formal_profile_name(profile_name: str, *, quick: bool) -> str:
    """Select the scenario catalog for one objective-suite profile."""
    if str(profile_name).upper() == "4U8G":
        return "4u8g" if quick else "4u8g-full"
    return "complete"


def _formal_scenario_filter(profile_name: str, scenarios: str, *, quick: bool) -> str:
    """Translate public objective names to the formal catalog names.

    The full 4U8G catalog namespaces overlapping PR397/PR421 cases. The
    objective-suite API intentionally exposes the source scenario name, so a
    targeted run such as ``fairness-steady`` must be translated before it is
    passed to ``formal_suite``.
    """
    if not scenarios or quick or str(profile_name).upper() != "4U8G":
        return scenarios
    return ",".join(
        item if "__" in item else f"pr421__{item}"
        for item in (part.strip() for part in scenarios.split(","))
        if item
    )


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_profiles(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else payload
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles config must contain a non-empty profiles list")
    def expand(value: Any) -> Any:
        if isinstance(value, str):
            # Support both ${NAME} and ${NAME:-fallback} for deployment-only
            # values such as the current EchoMem container name.
            import re

            def replace(match: re.Match[str]) -> str:
                name = match.group(1)
                fallback = match.group(2)
                return os.environ.get(name, fallback or match.group(0))

            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}", replace, value)
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            return {str(key): expand(item) for key, item in value.items()}
        return value

    return [
        expand(item)
        for item in profiles
        if isinstance(item, dict) and item.get("name")
    ]


def load_env_file(path: Path) -> dict[str, str]:
    """Load simple KEY=VALUE exports for nested real-model subprocesses.

    The objective suite launches formal_suite and its probes as child
    processes.  Server deployments commonly keep model credentials in a
    Docker env file, so accepting that file here prevents the preflight from
    seeing a different environment from EchoMem itself.  Values are never
    written to reports or included in command output.
    """
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_" for char in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _append_quick_seed_options(
    command: list[str],
    *,
    include_seed: bool,
) -> list[str]:
    """Keep one real-model warm-up shared by the bounded scenario matrix."""
    if include_seed:
        command += ["--seed-sessions-per-tenant", "1"]
    else:
        command += ["--skip-seed", "--seed-sessions-per-tenant", "0"]
    return command


def run_command(
    command: list[str],
    *,
    timeout_s: float,
    redact_values: set[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    redact_values = redact_values or set()
    child_env = dict(env) if env is not None else None
    if child_env is not None:
        # Nested probes are launched with ``-m``. Make the Harness root
        # explicit so they work regardless of the caller's working directory.
        project_root = str(Path(__file__).resolve().parent.parent)
        existing_pythonpath = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = (
            project_root
            if not existing_pythonpath
            else project_root + os.pathsep + existing_pythonpath
        )

    def safe_command() -> list[str]:
        return [
            "***configured***" if item in redact_values else item
            for item in command
        ]

    if timeout_s <= 0:
        return {
            "status": "TIMEOUT",
            "returncode": 124,
            "command": safe_command(),
            "stdout": "",
            "stderr": "objective suite wall-clock budget exhausted\n",
            "elapsed_s": 0.0,
        }
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
        env=child_env,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        return {
            "status": "PASS" if process.returncode == 0 else "FAIL",
            "returncode": process.returncode,
            "command": safe_command(),
            "stdout": (stdout or "")[-12000:],
            "stderr": (stderr or "")[-12000:],
            "elapsed_s": (datetime.now(timezone.utc) - started).total_seconds(),
        }
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
        return {
            "status": "TIMEOUT",
            "returncode": 124,
            "command": safe_command(),
            "stdout": (stdout or str(exc.stdout or ""))[-12000:],
            "stderr": (
                (stderr or str(exc.stderr or ""))
                + f"\nobjective suite child timeout after {timeout_s:.1f}s\n"
            )[-12000:],
            "elapsed_s": (datetime.now(timezone.utc) - started).total_seconds(),
        }


def _remaining_budget(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _bounded_timeout(requested_s: float, deadline: float | None) -> float:
    remaining = _remaining_budget(deadline)
    if remaining is None:
        return max(0.0, float(requested_s))
    return max(0.0, min(float(requested_s), remaining))


def _service_available(base_url: str, *, timeout_s: float) -> tuple[bool, str]:
    if timeout_s <= 0:
        return False, "objective suite wall-clock budget exhausted"
    try:
        request = urllib.request.Request(
            base_url.rstrip("/") + "/health",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=min(3.0, timeout_s)) as response:
            if 200 <= response.status < 300:
                return True, ""
            return False, f"health returned HTTP {response.status}"
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _probe_budget_skip(
    profile: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    keys = (
        "capability_probe",
        "blackbox_probe",
        "missing_cases",
        "concurrent_commit",
        "fault_isolation",
        "limit_failure_sweep",
        "commit_recovery",
        "fault_plan",
    )
    skipped: dict[str, Any] = {}
    for key in keys:
        configured = profile.get(key)
        enabled = (
            bool(configured)
            if key == "fault_plan"
            else isinstance(configured, dict) and configured.get("enabled", True)
        )
        if enabled:
            skipped[key] = {
                "status": "TIMEOUT" if "budget" in reason else "ENVIRONMENT_ERROR",
                "reason": reason,
            }
    return skipped


PROBE_CONFIG_KEYS = (
    "capability_probe",
    "blackbox_probe",
    "missing_cases",
    "concurrent_commit",
    "fault_isolation",
    "limit_failure_sweep",
    "commit_recovery",
    "fault_plan",
)


def _probe_plan(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe configured post-suite probes before they are executed.

    The plan is persisted in ``objective-suite.json`` so a partial run still
    explains which six-metric evidence was requested and which controls were
    absent.  This is intentionally configuration-driven; the harness never
    invents a fault or restart control.
    """
    plan: list[dict[str, Any]] = []
    labels = {
        "capability_probe": ("能力探针", "O6"),
        "blackbox_probe": ("黑盒契约探针", "O5"),
        "missing_cases": ("PR397 一致性探针", "O5"),
        "concurrent_commit": ("并发 Commit 探针", "O3"),
        "fault_isolation": ("单租户故障隔离探针", "O2"),
        "limit_failure_sweep": ("限流阶梯探针", "O1/O4/O6"),
        "commit_recovery": ("Commit kill-9 恢复探针", "O5"),
        "fault_plan": ("故障套件", "O2/O5"),
    }
    for key in PROBE_CONFIG_KEYS:
        configured = profile.get(key)
        automatic = key == "blackbox_probe"
        if automatic:
            enabled = True
        elif key == "fault_plan":
            enabled = bool(configured)
        else:
            enabled = isinstance(configured, dict) and configured.get("enabled", True)
        label, objectives = labels[key]
        plan.append({
            "name": key,
            "label": label,
            "objectives": objectives,
            "configured": enabled,
            "status": "scheduled" if enabled else "not_configured",
            "reason": (
                "将由 objective suite 在 formal 场景后自动执行"
                if automatic
                else "将由 objective suite 在 formal 场景后执行"
                if enabled
                else "profile 未配置真实控制或探针"
            ),
        })
    return plan


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _preserve_probe_status(
    execution: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Keep probe-level INCONCLUSIVE/NOT_IMPLEMENTED distinct from process failure."""
    if (
        execution.get("status") == "FAIL"
        and payload.get("status") in {"INCONCLUSIVE", "NOT_IMPLEMENTED"}
    ):
        execution["status"] = payload["status"]
    return execution


def _first_completed_commit_csv(formal_root: Path) -> tuple[Path, str] | None:
    candidates = sorted(formal_root.glob("**/commit_results.csv"))
    for path in candidates:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            continue
        for row in rows:
            if (
                str(row.get("status") or "").lower()
                in {"completed", "complete", "success", "succeeded"}
            ):
                return path, str(row.get("tenant") or "")
    return None


def _first_completed_commit_evidence(
    formal_root: Path,
) -> tuple[Path, dict[str, str]] | None:
    """Return the first completed Commit row for probe session binding."""
    for path in sorted(formal_root.glob("**/commit_results.csv")):
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            continue
        for row in rows:
            if str(row.get("status") or "").lower() in {
                "completed", "complete", "success", "succeeded",
            }:
                return path, row
    return None


def _resolve_auth_key(
    tenant_config: Path,
    tenant_selector: str = "",
) -> tuple[str, str]:
    """Resolve credentials for the tenant that produced the evidence.

    ``commit_results.csv`` historically stored either a zero-based tenant
    index or the tenant id.  Falling back to the first configured key makes a
    valid session look like an HTTP 400 when the completed row belongs to a
    different tenant, so selection must follow the evidence row.
    """
    try:
        payload = read_json(tenant_config)
        entries = payload.get("tenants") or []
        if not isinstance(entries, list) or not entries:
            return "", ""
        item: dict[str, Any] | None = None
        selector = str(tenant_selector or "").strip()
        if selector.isdigit():
            index = int(selector)
            if 0 <= index < len(entries) and isinstance(entries[index], dict):
                item = entries[index]
        if item is None:
            for candidate in entries:
                if (
                    isinstance(candidate, dict)
                    and str(candidate.get("tenant_id") or candidate.get("id") or "").strip()
                    == selector
                ):
                    item = candidate
                    break
        if item is None and not selector and isinstance(entries[0], dict):
            item = entries[0]
        if item is None:
            return "", ""
        direct = str(item.get("auth_key") or "")
        env_name = str(item.get("auth_key_env") or "")
        return direct or os.getenv(env_name, ""), env_name
    except (OSError, IndexError, TypeError):
        return "", ""


def _resolve_tenant_id(tenant_config: Path, requested: str) -> str:
    """Use the configured tenant, or the first real tenant when a profile is stale."""
    requested = str(requested or "").strip()
    payload = read_json(tenant_config)
    entries = payload.get("tenants") or []
    if not isinstance(entries, list):
        return requested
    for item in entries:
        if not isinstance(item, dict):
            continue
        tenant_id = str(
            item.get("tenant_id") or item.get("id") or item.get("user_id") or ""
        ).strip()
        if tenant_id == requested:
            return tenant_id
    for item in entries:
        if isinstance(item, dict):
            tenant_id = str(
                item.get("tenant_id") or item.get("id") or item.get("user_id") or ""
            ).strip()
            if tenant_id:
                return tenant_id
    return requested


def _resolve_profile_path(value: str, profiles_path: Path) -> str:
    """Resolve deployment paths without duplicating the profile directory.

    Example manifests live under ``performance/`` but historically used both
    ``tenants.json`` and ``performance/tenants.json``. Resolving only beside
    the manifest turns the latter into ``performance/performance/...``. Try
    the path beside the manifest first, then the repository root, and keep the
    original relative value as a final fallback for diagnostics.
    """
    if not value:
        return ""
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    candidates = [
        profiles_path.parent / path,
        profiles_path.parent.parent / path,
        _PROJECT_ROOT / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return str((profiles_path.parent / path).resolve())


def _materialize_fault_plan(
    plan_path: Path,
    *,
    base_url: str,
    output_path: Path,
) -> Path:
    """Resolve the selected service address in a run-local fault plan."""
    payload = read_json(plan_path)

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("${BASE_URL}", base_url.rstrip("/"))
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {str(key): replace(item) for key, item in value.items()}
        return value

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(replace(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def _add_option(command: list[str], flag: str, value: Any) -> None:
    if value not in (None, ""):
        command.extend([flag, str(value)])


def _run_limit_failure_sweep(
    profile: dict[str, Any],
    *,
    profile_dir: Path,
    profiles_path: Path,
    formal_root: Path,
    timeout_s: float,
    quick: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run an optional real admission sweep after the bounded suite.

    The formal ``saturation`` case measures Search while Commit is busy.  It
    does not guarantee a queue-full response, so it cannot prove the
    429/503/Retry-After/reason_code contract by itself.  This separate sweep
    intentionally drives the public endpoints at explicit worker levels and
    keeps the raw rows for audit.
    """
    config = profile.get("limit_failure_sweep")
    if not isinstance(config, dict):
        return {}, {
            "limit_failure_sweep": {
                "status": "INCONCLUSIVE",
                "reason": "profile 未配置真实限流阶梯",
            }
        }

    tenant_value = str(profile.get("tenant_config") or "")
    tenant_path = Path(_resolve_profile_path(tenant_value, profiles_path))
    output = profile_dir / "limit-failure-sweep"
    output.mkdir(parents=True, exist_ok=True)
    levels = str(config.get("levels") or "16,64,128,256")
    search_count = config.get("search_count")
    open_count = config.get("open_count")
    commit_count = config.get("commit_count")
    workers = config.get("workers")
    probe_timeout_s = config.get("timeout_s") or 8.0
    if quick:
        # Quick objective runs must remain diagnostic and bounded.  The
        # formal suite already has a small wall-clock cap, but a configured
        # admission sweep can otherwise reintroduce hundreds of requests per
        # wave and make the whole run appear stuck.
        levels = "4,16"
        search_count = min(int(search_count or 16), 16)
        open_count = min(int(open_count or 8), 8)
        commit_count = min(int(commit_count or 8), 8)
        workers = min(int(workers or 16), 16)
        probe_timeout_s = min(float(probe_timeout_s), 5.0)
    command = [
        sys.executable,
        "-m",
        "performance.probes.limit_failure_sweep",
        "--base-url",
        str(profile.get("base_url") or "http://127.0.0.1:8010"),
        "--tenant-config",
        str(tenant_path),
        "--out-dir",
        str(output),
        "--levels",
        levels,
        "--timeout-s",
        str(probe_timeout_s),
        "--auth-header",
        str(profile.get("auth_header") or "X-Auth-Key"),
    ]
    session_root = str(config.get("session_root") or "").strip()
    if session_root:
        resolved_session_root = _resolve_profile_path(session_root, profiles_path)
        command += ["--session-root", resolved_session_root]
    else:
        # New sessions make the sweep independent of whichever formal case
        # happened to finish first and avoid cross-run session contamination.
        command += ["--session-root", str(formal_root), "--create-sessions"]
    for key, flag in (
        ("search_count", "--search-count"),
        ("open_count", "--open-count"),
        ("commit_count", "--commit-count"),
        ("workers", "--workers"),
    ):
        value = {
            "search_count": search_count,
            "open_count": open_count,
            "commit_count": commit_count,
            "workers": workers,
        }.get(key)
        _add_option(command, flag, value)
    execution = run_command(command, timeout_s=min(timeout_s, 180 if quick else 1800))
    commands = {"limit_failure_sweep": execution}
    summary_path = output / "summary.json"
    payload = read_json(summary_path)
    if not payload:
        return {}, commands
    return {
        "limit_failure_sweep": {
            **payload,
            "path": str(summary_path),
            "requests_path": str(output / "requests.csv"),
        }
    }, commands


def _run_configured_probes(
    profile: dict[str, Any],
    *,
    profile_dir: Path,
    profiles_path: Path,
    formal_root: Path,
    timeout_s: float,
    quick: bool = False,
    deadline: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run explicitly configured real probes and return artifacts/metadata."""
    artifacts: dict[str, Any] = {}
    commands: dict[str, Any] = {}
    base_url = str(profile.get("base_url") or "http://127.0.0.1:8010")
    auth_header = str(profile.get("auth_header") or "X-Auth-Key")
    probe_budget = _bounded_timeout(timeout_s, deadline)
    available, availability_error = _service_available(
        base_url,
        timeout_s=probe_budget,
    )
    if not available:
        reason = f"目标 EchoMem 在探针开始前不可达：{availability_error}"
        commands.update(_probe_budget_skip(profile, reason=reason))
        commands["service_preflight"] = {
            "status": (
                "TIMEOUT"
                if "budget" in availability_error
                else "ENVIRONMENT_ERROR"
            ),
            "reason": reason,
        }
        return artifacts, commands
    tenant_path = Path(
        _resolve_profile_path(str(profile.get("tenant_config") or ""), profiles_path)
    )
    commit_artifact = _first_completed_commit_csv(formal_root)
    commit_csv = commit_artifact[0] if commit_artifact else None
    tenant_index = commit_artifact[1] if commit_artifact else ""
    commit_evidence = _first_completed_commit_evidence(formal_root)
    evidence_row = commit_evidence[1] if commit_evidence else {}
    auth_key, auth_key_env = _resolve_auth_key(tenant_path, tenant_index)
    redact = {auth_key} if auth_key else set()

    capability = profile.get("capability_probe")
    if isinstance(capability, dict):
        output = profile_dir / "capability-probe.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.capability_probe",
            "--base-url",
            base_url,
            "--out",
            str(output),
        ]
        if auth_key:
            command += ["--auth-key", auth_key]
        elif auth_key_env:
            command += ["--auth-key-env", auth_key_env]
        command += ["--auth-header", auth_header]
        for key, flag in (
            ("session_id", "--session-id"),
            ("health_path", "--health-path"),
            ("metrics_path", "--metrics-path"),
            ("cursor_path", "--cursor-path"),
            ("cursor_uri_template", "--cursor-uri-template"),
            ("operation_path", "--operation-path"),
            ("conflict_path", "--conflict-path"),
            ("ttl_path", "--ttl-path"),
            ("engine_path", "--engine-path"),
            ("fault_path", "--fault-path"),
            ("timeout_s", "--timeout-s"),
        ):
            _add_option(command, flag, capability.get(key))
        _add_option(
            command,
            "--auth-header",
            str(profile.get("auth_header") or "X-Auth-Key"),
        )
        # Bind cursor probing to a session created by this run rather than a
        # stale session hard-coded in a shared deployment profile.
        if evidence_row.get("session_id") and not capability.get("session_id"):
            command += ["--session-id", str(evidence_row["session_id"])]
        execution = run_command(
            command,
            timeout_s=_bounded_timeout(min(timeout_s, 180), deadline),
            redact_values=redact,
        )
        commands["capability_probe"] = execution
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["capability_probe"] = {**payload, "path": str(output)}

    if commit_csv and tenant_path.is_file():
        output = profile_dir / "blackbox-contract-probe.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.blackbox_contract_probe",
            "--base-url",
            base_url,
            "--commit-csv",
            str(commit_csv),
            "--tenant",
            tenant_index,
            "--out",
            str(output),
        ]
        if auth_key:
            command += ["--auth-key", auth_key]
        elif auth_key_env:
            command += ["--auth-key-env", auth_key_env]
        command += ["--auth-header", auth_header]
        execution = run_command(
            command,
            timeout_s=_bounded_timeout(min(timeout_s, 180), deadline),
            redact_values=redact,
        )
        commands["blackbox_probe"] = execution
        payload = read_json(output)
        if payload:
            artifacts["blackbox_contract_probe"] = {**payload, "path": str(output)}
    else:
        commands["blackbox_probe"] = {
            "status": "INCONCLUSIVE",
            "reason": (
                "本轮没有完成 Commit 或租户配置不存在，"
                "无法从真实 session 启动黑盒契约探测"
            ),
        }

    # PR397 also ships standalone black-box probes. Wire them into the common
    # entry point so "configured" means "actually executed", while keeping
    # their tenant/count bounds in the profile for quick server runs.
    missing = profile.get("missing_cases")
    if isinstance(missing, dict) and missing.get("enabled", True):
        output = profile_dir / "missing-cases.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.missing_cases",
            "--base-url", base_url,
            "--tenant-config", str(tenant_path),
            "--out", str(output),
        ]
        for key, flag in (
            ("max_tenants", "--max-tenants"),
            ("auth_header", "--auth-header"),
            ("commit_timeout_s", "--commit-timeout-s"),
            ("search_timeout_s", "--search-timeout-s"),
            ("visibility_timeout_s", "--visibility-timeout-s"),
        ):
            _add_option(command, flag, missing.get(key))
        command += ["--auth-header", auth_header]
        execution = run_command(
            command,
            timeout_s=_bounded_timeout(
                min(timeout_s, 300 if quick else 900),
                deadline,
            ),
            redact_values=redact,
        )
        commands["missing_cases"] = execution
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["missing_cases"] = {**payload, "path": str(output)}

    concurrent = profile.get("concurrent_commit")
    if isinstance(concurrent, dict) and concurrent.get("enabled", True):
        output = profile_dir / "concurrent-commit.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.concurrent_commit_cases",
            "--base-url", base_url,
            "--tenant-config", str(tenant_path),
            "--out", str(output),
        ]
        for key, flag in (
            ("concurrency", "--concurrency"),
            ("timeout_s", "--timeout-s"),
            ("auth_header", "--auth-header"),
        ):
            _add_option(command, flag, concurrent.get(key))
        command += ["--auth-header", auth_header]
        execution = run_command(
            command,
            timeout_s=_bounded_timeout(
                min(timeout_s, 300 if quick else 900),
                deadline,
            ),
            redact_values=redact,
        )
        commands["concurrent_commit"] = execution
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["concurrent_commit"] = {**payload, "path": str(output)}

    fault_isolation = profile.get("fault_isolation")
    if isinstance(fault_isolation, dict) and fault_isolation.get("enabled", True):
        output = profile_dir / "fault-isolation.json"
        command = [
            sys.executable,
            "-m",
            "performance.probes.fault_isolation_probe",
            "--base-url", base_url,
            "--tenant-config", str(tenant_path),
            "--target-tenant", str(fault_isolation.get("target_tenant") or ""),
            "--bystander-tenants", str(fault_isolation.get("bystander_tenants") or ""),
            "--out", str(output),
        ]
        for key, flag in (
            ("endpoint", "--endpoint"),
            ("command", "--command"),
            ("samples", "--samples"),
            ("workers", "--workers"),
            ("timeout_s", "--timeout-s"),
            ("control_timeout_s", "--control-timeout-s"),
            ("auth_header", "--auth-header"),
        ):
            _add_option(command, flag, fault_isolation.get(key))
        command += ["--auth-header", auth_header]
        execution = run_command(
            command,
            timeout_s=_bounded_timeout(
                min(timeout_s, 600 if quick else 1800),
                deadline,
            ),
            redact_values=redact,
        )
        commands["fault_isolation"] = execution
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["fault_isolation"] = {**payload, "path": str(output)}

    if _remaining_budget(deadline) is not None and _remaining_budget(deadline) <= 0:
        commands["limit_failure_sweep"] = {
            "status": "TIMEOUT",
            "reason": "objective suite wall-clock budget exhausted",
        }
    else:
        sweep_artifacts, sweep_commands = _run_limit_failure_sweep(
            profile,
            profile_dir=profile_dir,
            profiles_path=profiles_path,
            formal_root=formal_root,
            timeout_s=_bounded_timeout(timeout_s, deadline),
            quick=quick,
        )
        artifacts.update(sweep_artifacts)
        commands.update(sweep_commands)

    recovery = profile.get("commit_recovery")
    if isinstance(recovery, dict) and tenant_path.is_file():
        output = profile_dir / "commit-recovery.json"
        recovery_tenant = _resolve_tenant_id(
            tenant_path, str(recovery.get("tenant") or "")
        )
        command = [
            sys.executable,
            "-m",
            "performance.probes.commit_recovery_probe",
            "--base-url",
            base_url,
            "--tenant-config",
            str(tenant_path),
            "--out",
            str(output),
        ]
        if recovery.get("container"):
            command += ["--container", str(recovery["container"])]
        if recovery.get("pid"):
            command += ["--pid", str(recovery["pid"])]
        if recovery.get("restart_command"):
            command += ["--restart-command", str(recovery["restart_command"])]
        if recovery_tenant:
            command += ["--tenant", recovery_tenant]
        for key, flag in (
            ("kill_delay_s", "--kill-delay-s"),
            ("messages", "--messages"),
            ("content_chars", "--content-chars"),
            ("recovery_timeout_s", "--recovery-timeout-s"),
            ("poll_s", "--poll-s"),
            ("accepted_wait_s", "--accepted-wait-s"),
        ):
            _add_option(command, flag, recovery.get(key))
        command += ["--auth-header", auth_header]
        if recovery.get("require_accepted_202"):
            command.append("--require-accepted-202")
        execution = run_command(
            command,
            timeout_s=_bounded_timeout(min(timeout_s, 900), deadline),
            redact_values=redact,
        )
        commands["commit_recovery"] = execution
        payload = read_json(output)
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["commit_recovery"] = {**payload, "path": str(output)}

    fault_plan_value = profile.get("fault_plan")
    if fault_plan_value:
        plan_path = Path(_resolve_profile_path(str(fault_plan_value), profiles_path))
        if not plan_path.is_file():
            commands["fault_suite"] = {
                "status": "INCONCLUSIVE",
                "reason": (
                    "fault_plan 不存在，未启动故障套件；"
                    f"期望路径：{plan_path}"
                ),
            }
            return artifacts, commands
        plan_path = _materialize_fault_plan(
            plan_path,
            base_url=base_url,
            output_path=profile_dir / "fault-plan.resolved.json",
        )
        output_dir = profile_dir / "fault-suite"
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "performance.probes.fault_suite",
            "--plan",
            str(plan_path),
            "--out-dir",
            str(output_dir),
            "--base-url",
            base_url,
        ]
        if auth_key:
            command += ["--auth-key", auth_key]
        if commit_csv:
            command += ["--commit-csv", str(commit_csv)]
        command += ["--auth-header", auth_header]
        execution = run_command(
            command,
            timeout_s=_bounded_timeout(min(timeout_s, 900), deadline),
            redact_values=redact,
        )
        commands["fault_suite"] = execution
        payload = read_json(output_dir / "fault-suite.json")
        _preserve_probe_status(execution, payload)
        if payload:
            artifacts["fault_suite"] = {
                **payload,
                "path": str(output_dir / "fault-suite.json"),
            }

    return artifacts, commands


def _formal_run_counts(suite: dict[str, Any]) -> tuple[int, int]:
    """Return (completed, submitted) counts without equating the two.

    A run can emit requests and still end in TIMEOUT, ENV_ERROR, or blocked
    state. Profile-level evidence for the optional multi-spec diagnostic must
    count only explicit completed runs, while submitted is retained as a
    diagnostic volume.
    """
    completed = 0
    submitted = 0
    for item in suite.get("runs") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        if not isinstance(summary, dict):
            continue
        metrics = summary.get("metrics")
        if not isinstance(metrics, dict):
            continue
        search = metrics.get("search")
        commit = metrics.get("commit")
        search = search if isinstance(search, dict) else {}
        commit = commit if isinstance(commit, dict) else {}
        if int(search.get("submitted") or 0) <= 0 and int(commit.get("submitted") or 0) <= 0:
            continue
        submitted += 1
        if str(item.get("status") or "").lower() == "completed":
            completed += 1
    return completed, submitted


def _formal_submitted_operations(run: dict[str, Any]) -> int:
    """Count real operations recorded by one formal-suite run."""
    summary = run.get("summary")
    if not isinstance(summary, dict):
        return 0
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    total = 0
    for operation in ("search", "commit"):
        item = metrics.get(operation)
        if isinstance(item, dict):
            try:
                total += max(0, int(item.get("submitted") or 0))
            except (TypeError, ValueError):
                continue
    return total


def _formal_coverage(suite: dict[str, Any]) -> dict[str, Any]:
    """Summarize formal-suite coverage without treating placeholders as data.

    ``formal_suite`` may materialize a TIMEOUT/BLOCKED record for every
    remaining case so that a partial run is auditable.  Those records are
    useful evidence, but they do not prove that the case actually ran.
    Coverage therefore has two dimensions:

    * manifest coverage: one record exists for every configured case;
    * evidence coverage: the record completed and contains at least one
      submitted real HTTP operation.
    """
    configured = [
        str(item).strip()
        for item in (suite.get("scenarios") or [])
        if str(item).strip()
    ]
    repeats = max(1, int(suite.get("repeats") or 1))
    policies = [
        str(item).strip()
        for item in (suite.get("policies") or ["server-observe"])
        if str(item).strip()
    ]
    expected = len(configured) * repeats * max(1, len(policies))
    runs = [item for item in (suite.get("runs") or []) if isinstance(item, dict)]
    actual = len(runs)
    completed = sum(
        1 for item in runs if str(item.get("status") or "").lower() == "completed"
    )
    evidence_runs = sum(
        1
        for item in runs
        if (
            str(item.get("status") or "").lower() == "completed"
            and _formal_submitted_operations(item) > 0
        )
    )
    empty_completed_runs = completed - evidence_runs
    status_counts: dict[str, int] = {}
    for item in runs:
        status = str(item.get("status") or "NO_SUMMARY").upper()
        status_counts[status] = status_counts.get(status, 0) + 1
    observed_keys = {
        str(item.get("scenario_key") or item.get("scenario") or "")
        for item in runs
    }
    expected_keys = set(configured)
    missing = sorted(expected_keys - observed_keys)
    evidence_missing = sorted(
        str(item.get("scenario_key") or item.get("scenario") or "")
        for item in runs
        if (
            str(item.get("status") or "").lower() != "completed"
            or _formal_submitted_operations(item) <= 0
        )
    )
    return {
        "expected_runs": expected,
        "manifest_runs": actual,
        "completed_runs": completed,
        "evidence_runs": evidence_runs,
        "empty_completed_runs": empty_completed_runs,
        "status_counts": status_counts,
        "failed_runs": sum(
            status_counts.get(status, 0)
            for status in ("FAIL", "HARNESS_ERROR", "NO_SUMMARY")
        ),
        "timeout_runs": status_counts.get("TIMEOUT", 0),
        "blocked_runs": status_counts.get("BLOCKED", 0),
        "missing_scenarios": missing,
        "evidence_missing_scenarios": evidence_missing,
        "status": (
            "complete"
            if expected > 0 and actual >= expected and evidence_runs >= expected
            else "partial"
        ),
    }


def platform_objective_coverage(
    profile: dict[str, Any],
    suite: dict[str, Any],
    probe_plan: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> list[dict[str, Any]]:
    """Report whether the platform is configured to produce each objective.

    This is deliberately separate from the runtime PASS/FAIL verdict.  A
    configured scenario can still be blocked, and an absent fault/restart
    control is a deployment gap rather than evidence that EchoMem failed.
    """
    configured_probes = {
        str(item.get("name"))
        for item in probe_plan
        if isinstance(item, dict) and item.get("configured")
    }
    run_names = {
        str(item.get("source_scenario") or item.get("scenario") or "")
        for item in suite.get("runs") or []
        if isinstance(item, dict)
    }
    results: list[dict[str, Any]] = []
    for objective_id, requirement in PLATFORM_OBJECTIVE_REQUIREMENTS.items():
        scenarios = sorted(set(requirement["scenarios"]) & run_names)
        missing_scenarios = sorted(set(requirement["scenarios"]) - run_names)
        scenario_mode = requirement.get("scenario_mode")
        if scenario_mode == "any":
            missing_scenarios = (
                [] if scenarios else sorted(requirement["scenarios"])
            )
        elif scenario_mode == "all_except_fairness":
            has_fairness = bool(
                {"fairness-steady", "fairness-bounded"} & run_names
            )
            missing_scenarios = sorted(
                {
                    item
                    for item in requirement["scenarios"]
                    if item not in run_names
                    and item not in {"fairness-steady", "fairness-bounded"}
                }
            )
            if not has_fairness:
                missing_scenarios.append("fairness-steady|fairness-bounded")
        probes = sorted(set(requirement["probes"]) & configured_probes)
        missing_probes = sorted(set(requirement["probes"]) - configured_probes)
        missing = [
            *(f"scenario:{item}" for item in missing_scenarios),
            *(f"probe:{item}" for item in missing_probes),
        ]
        if objective_id == "O1":
            evidence_missing = coverage.get("evidence_missing_scenarios") or []
            missing.extend(
                f"evidence:{item}"
                for item in evidence_missing
                if str(item).startswith("capacity-")
            )
        status = "configured" if not missing else "incomplete"
        results.append(
            {
                "id": objective_id,
                "name": requirement["name"],
                "status": status,
                "owner": requirement["owner"],
                "required_scenarios": sorted(requirement["scenarios"]),
                "configured_scenarios": scenarios,
                "required_probes": sorted(requirement["probes"]),
                "configured_probes": probes,
                "missing": sorted(set(missing)),
            }
        )
    return results


def acceptance_by_name(suite: dict[str, Any]) -> dict[str, dict[str, Any]]:
    acceptance = suite.get("acceptance") or {}
    return {
        str(item.get("name")): item
        for item in acceptance.get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }


def objective_statuses(
    suite: dict[str, Any],
    *,
    recovery_configured: bool,
    metrics_configured: bool,
) -> list[dict[str, Any]]:
    checks = acceptance_by_name(suite)
    blackbox = suite.get("blackbox_contract_probe") or {}
    recovery = suite.get("commit_recovery") or {}
    fault_suite = suite.get("fault_suite") or {}
    capability = suite.get("capability_probe") or {}

    recovery_for_scheduler = dict(recovery)
    message_reconciliation = recovery_for_scheduler.get("message_reconciliation")
    if (
        "message_set_reconciled" not in recovery_for_scheduler
        and isinstance(message_reconciliation, dict)
    ):
        recovery_for_scheduler["message_set_reconciled"] = (
            str(message_reconciliation.get("status") or "") == PASS
        )
    if (
        "replay_verified" not in recovery_for_scheduler
        and isinstance(recovery_for_scheduler.get("idempotency_reconciliation"), dict)
    ):
        recovery_for_scheduler["replay_verified"] = (
            str(
                recovery_for_scheduler["idempotency_reconciliation"].get("status") or ""
            )
            == PASS
        )
    strict_acceptance = evaluate_scheduler_acceptance(
        {
            **suite,
            # Keep the standalone fault-isolation artifact on the stable
            # contract consumed by the six-objective evaluator.
            "tenant_fault_isolation": (
                suite.get("tenant_fault_isolation")
                if isinstance(suite.get("tenant_fault_isolation"), dict)
                else suite.get("fault_isolation")
            ),
        },
        capability=capability,
        recovery=recovery_for_scheduler,
        fault=fault_suite,
    )
    strict_by_name = {
        str(item.get("name")): item
        for item in strict_acceptance.get("checks") or []
        if isinstance(item, dict) and item.get("name")
    }

    def strict(name: str, fallback: str = INCONCLUSIVE) -> dict[str, Any]:
        item = strict_by_name.get(name)
        return item if isinstance(item, dict) else {"status": fallback}

    def status(name: str, fallback: str = "INCONCLUSIVE") -> str:
        return str((checks.get(name) or {}).get("status") or fallback)

    def fault_case_statuses() -> list[str]:
        return [
            str((case.get("execution") or {}).get("result", {}).get("status") or INCONCLUSIVE)
            for case in fault_suite.get("cases") or []
            if isinstance(case, dict)
        ]

    fault_cases = fault_case_statuses()
    fault_has_search_observation = any(
        isinstance(case, dict)
        and (
            "search_p95" in case
            or "search" in case
            or "observer" in case
            or "isolation_ratio" in case
        )
        for case in fault_suite.get("cases") or []
    )
    recovery_reconcile = recovery.get("message_reconciliation") or {}
    cursor_reconcile = recovery.get("cursor_reconciliation") or {}
    idempotency_reconcile = recovery.get("idempotency_reconciliation") or {}
    recovery_statuses = [
        str(recovery.get("status") or INCONCLUSIVE),
        str(recovery_reconcile.get("status") or INCONCLUSIVE),
        str(cursor_reconcile.get("status") or INCONCLUSIVE),
        str(idempotency_reconcile.get("status") or INCONCLUSIVE),
    ]

    # O1 is a capacity ladder. It is a measured upper bound for active test
    # identities, not a product DAU forecast.
    capacity = [
        run for run in suite.get("runs") or []
        if str(run.get("scenario") or "").startswith("capacity-")
    ]
    def strict_observed(name: str) -> Any:
        return strict(name).get("observed", {})

    return [
        {
            "id": "O1",
            "name": OBJECTIVES[0][1],
            "status": strict("DAU / 最大热用户容量")["status"],
            "reason": strict("DAU / 最大热用户容量").get("reason", ""),
            "observed": strict_observed("DAU / 最大热用户容量"),
            "owner": strict("DAU / 最大热用户容量").get("owner"),
            "evidence": "scheduler_acceptance: DAU / 最大热用户容量",
        },
        {
            "id": "O2",
            "name": OBJECTIVES[1][1],
            "status": strict("单租户故障隔离")["status"],
            "reason": strict("单租户故障隔离").get("reason", ""),
            "observed": strict_observed("单租户故障隔离"),
            "owner": strict("单租户故障隔离").get("owner"),
            "evidence": "scheduler_acceptance: 单租户故障隔离",
        },
        {
            "id": "O3",
            "name": OBJECTIVES[2][1],
            "status": strict("Commit/Search 公平性 Jain")["status"],
            "reason": strict("Commit/Search 公平性 Jain").get("reason", ""),
            "observed": strict_observed("Commit/Search 公平性 Jain"),
            "owner": strict("Commit/Search 公平性 Jain").get("owner"),
            "evidence": "scheduler_acceptance: Commit/Search 公平性 Jain",
        },
        {
            "id": "O4",
            "name": OBJECTIVES[3][1],
            "status": strict("Search 优先于 Commit")["status"],
            "reason": strict("Search 优先于 Commit").get("reason", ""),
            "observed": strict_observed("Search 优先于 Commit"),
            "owner": strict("Search 优先于 Commit").get("owner"),
            "evidence": "scheduler_acceptance: Search 优先于 Commit",
        },
        {
            "id": "O5",
            "name": OBJECTIVES[4][1],
            "status": strict("Commit kill-9 恢复与重放")["status"],
            "reason": strict("Commit kill-9 恢复与重放").get("reason", ""),
            "observed": strict_observed("Commit kill-9 恢复与重放"),
            "owner": strict("Commit kill-9 恢复与重放").get("owner"),
            "evidence": "scheduler_acceptance: Commit kill-9 恢复与重放",
        },
        {
            "id": "O6",
            "name": OBJECTIVES[5][1],
            "status": strict("分层/分租户调度可观测性")["status"],
            "reason": strict("分层/分租户调度可观测性").get("reason", ""),
            "observed": strict_observed("分层/分租户调度可观测性"),
            "owner": strict("分层/分租户调度可观测性").get("owner"),
            "evidence": "scheduler_acceptance: 分层/分租户调度可观测性",
        },
    ]


def render_report(result: dict[str, Any], path: Path) -> None:
    def target_text(objective: dict[str, Any]) -> str:
        target = objective.get("target")
        if target not in (None, ""):
            return str(target)
        names = {
            "O1": "成功容量档位 + 更高一档真实失败/超时边界",
            "O2": "旁观租户 Search P95 劣化 <= 20%",
            "O3": "Commit Jain 与 Search 延迟 Jain 的较小值 >= 0.90",
            "O4": "洪泛窗口 Search P95 <= 5s，且相对基线劣化 <= 2x",
            "O5": "HTTP 202 + kill/restart + 消息集合/cursor/幂等对账全部通过",
            "O6": "每个实际 lane 具备 queued/wait/exec/rejected 四元组",
        }
        return names.get(str(objective.get("id") or ""), "见验收器")

    rows = []
    for profile in result.get("profiles") or []:
        for objective in profile.get("objectives") or []:
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(profile.get('name')))}</td>"
                f"<td>{html.escape(str(objective.get('id')))} "
                f"{html.escape(str(objective.get('name')))}</td>"
                f"<td class='{html.escape(str(objective.get('status')).lower())}'>"
                f"{html.escape(str(objective.get('status')))}</td>"
                f"<td>{html.escape(target_text(objective))}</td>"
                f"<td>{html.escape(str(objective.get('reason')))}"
                f"<br><code>{html.escape(json.dumps(objective.get('observed', {}), ensure_ascii=False, sort_keys=True))}</code></td>"
                f"<td>{html.escape(str(objective.get('owner') or '测试平台'))}</td>"
                f"<td><code>{html.escape(str(objective.get('evidence')))}</code></td>"
                "</tr>"
            )
    details = []
    for profile in result.get("profiles") or []:
        details.append(f"<h3>{html.escape(str(profile.get('name')))}</h3>")
        probe_plan = profile.get("probe_plan") or []
        if isinstance(probe_plan, list) and probe_plan:
            configured_count = sum(
                1
                for item in probe_plan
                if isinstance(item, dict) and item.get("configured")
            )
            details.append(
                "<details><summary>补测计划："
                f"{html.escape(str(configured_count))}/"
                f"{html.escape(str(len(probe_plan)))} 项已配置</summary>"
                "<table><thead><tr><th>探针</th><th>目标</th><th>状态</th>"
                "<th>说明</th></tr></thead><tbody>"
            )
            for item in probe_plan:
                if not isinstance(item, dict):
                    continue
                details.append(
                    "<tr>"
                    f"<td>{html.escape(str(item.get('label') or item.get('name')))}</td>"
                    f"<td>{html.escape(str(item.get('objectives') or '-'))}</td>"
                    f"<td>{html.escape(str(item.get('status') or '-'))}</td>"
                    f"<td>{html.escape(str(item.get('reason') or '-'))}</td>"
                    "</tr>"
                )
            details.append(
                "</tbody></table>"
                f"<p class='muted'>预留补测时间："
                f"{html.escape(str(profile.get('probe_budget_reserved_s', 0)))} 秒</p>"
                "</details>"
            )
        coverage = profile.get("coverage") or {}
        missing = coverage.get("missing_scenarios") or []
        platform_coverage = profile.get("platform_objective_coverage") or []
        if isinstance(platform_coverage, list) and platform_coverage:
            details.append(
                "<details open><summary>测试平台六项指标覆盖审计</summary>"
                "<table><thead><tr><th>指标</th><th>平台状态</th>"
                "<th>已配置场景</th><th>已配置探针</th><th>缺口</th>"
                "<th>责任边界</th></tr></thead><tbody>"
            )
            for item in platform_coverage:
                if not isinstance(item, dict):
                    continue
                missing_items = item.get("missing") or []
                details.append(
                    "<tr>"
                    f"<td>{html.escape(str(item.get('id') or ''))} "
                    f"{html.escape(str(item.get('name') or ''))}</td>"
                    f"<td>{html.escape(str(item.get('status') or ''))}</td>"
                    f"<td>{html.escape(', '.join(map(str, item.get('configured_scenarios') or [])) or '-')}</td>"
                    f"<td>{html.escape(', '.join(map(str, item.get('configured_probes') or [])) or '-')}</td>"
                    f"<td>{html.escape(', '.join(map(str, missing_items)) or '-')}</td>"
                    f"<td>{html.escape(str(item.get('owner') or '-'))}</td>"
                    "</tr>"
                )
            details.append(
                "</tbody></table>"
                "<p class='muted'>平台状态只表示“是否具备产出该指标的测试入口”；"
                "最终 PASS/FAIL/INCONCLUSIVE 仍以真实运行证据为准。</p></details>"
            )
        details.append(
            "<p><strong>场景覆盖：</strong>"
            f"{html.escape(str(coverage.get('manifest_runs', 0)))}/"
            f"{html.escape(str(coverage.get('expected_runs', 0)))} 个结果，"
            f"状态 <strong>{html.escape(str(coverage.get('status', 'unknown')))}</strong>"
            f"；完成 {html.escape(str(coverage.get('completed_runs', 0)))}，"
            f"真实证据 {html.escape(str(coverage.get('evidence_runs', 0)))}，"
            f"空完成记录 {html.escape(str(coverage.get('empty_completed_runs', 0)))}，"
            f"超时 {html.escape(str(coverage.get('timeout_runs', 0)))}，"
            f"阻断 {html.escape(str(coverage.get('blocked_runs', 0)))}"
            + (
                "；缺失："
                + html.escape(", ".join(str(item) for item in missing))
                if missing
                else ""
            )
            + (
                "；证据缺失："
                + html.escape(", ".join(str(item) for item in coverage.get("evidence_missing_scenarios") or []))
                if coverage.get("evidence_missing_scenarios")
                else ""
            )
            + "</p>"
        )
        for key, label in (
            ("capability_probe", "能力探针"),
            ("blackbox_contract_probe", "黑盒契约探针"),
            ("missing_cases", "PR397 黑盒一致性探针"),
            ("concurrent_commit", "并发 Commit 探针"),
            ("fault_isolation", "单租户故障隔离探针"),
            ("limit_failure_sweep", "真实限流阶梯"),
            ("commit_recovery", "Commit 崩溃恢复探针"),
            ("fault_suite", "故障套件"),
        ):
            payload = profile.get(key)
            if not isinstance(payload, dict):
                continue
            checks_detail = payload.get("checks") or payload.get("cases") or []
            details.append(
                f"<details><summary>{label}："
                f"<strong>{html.escape(str(payload.get('status', '未返回')))}</strong>"
                "</summary>"
            )
            if payload.get("reason"):
                details.append(f"<p>{html.escape(str(payload['reason']))}</p>")
            if isinstance(checks_detail, list) and checks_detail:
                details.append(
                    "<table><thead><tr><th>检查项</th><th>状态</th><th>HTTP/耗时</th>"
                    "<th>说明</th></tr></thead><tbody>"
                )
                for item in checks_detail:
                    item = item if isinstance(item, dict) else {}
                    execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
                    nested = execution.get("result") if isinstance(execution.get("result"), dict) else {}
                    details.append(
                        "<tr>"
                        f"<td>{html.escape(str(item.get('name') or item.get('kind') or 'case'))}</td>"
                        f"<td>{html.escape(str(item.get('status') or nested.get('status') or ''))}</td>"
                        f"<td>{html.escape(str(item.get('http_status') or item.get('elapsed_s') or ''))}</td>"
                        f"<td>{html.escape(str(item.get('reason') or nested.get('reason') or ''))}</td>"
                        "</tr>"
                    )
                details.append("</tbody></table>")
            details.append(
                f"<p class='muted'>制品：<code>{html.escape(str(payload.get('path', '')))}</code></p></details>"
            )
    optional_profiles = result.get("instance_profiles")
    if isinstance(optional_profiles, list):
        details.append(
            "<details><summary>附加诊断：多规格实例对比（不计入六项总体判定）</summary>"
            "<table><thead><tr><th>规格</th><th>状态</th><th>覆盖</th>"
            "<th>完成场景</th><th>提交场景</th></tr></thead><tbody>"
        )
        for item in optional_profiles:
            if not isinstance(item, dict):
                continue
            details.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('name') or ''))}</td>"
                f"<td>{html.escape(str(item.get('status') or ''))}</td>"
                f"<td>{html.escape(str(item.get('coverage_status') or 'unknown'))} "
                f"({html.escape(str(item.get('completed_runs') or 0))}/"
                f"{html.escape(str(item.get('expected_runs') or 0))})</td>"
                f"<td>{html.escape(str(item.get('completed_runs') or 0))}</td>"
                f"<td>{html.escape(str(item.get('submitted_runs') or 0))}</td>"
                "</tr>"
            )
        details.append(
            "</tbody></table><p class='muted'>多规格需要至少两种规格都实际完成同一组场景，"
            "本轮 4U8G 单规格不会因此被判定为失败。</p></details>"
        )
    doc = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 六项 4U8G 目标自动化验收</title>
<style>
body{{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17212b;background:#f5f7f8;margin:0}}
main{{max-width:1280px;margin:auto;padding:28px 18px 56px}}section{{background:#fff;border:1px solid #dfe6ea;padding:18px;margin-top:14px}}
h1{{margin:0 0 6px;font-size:25px}}.muted{{color:#687784}}table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid #e7ecef;padding:9px;text-align:left;vertical-align:top}}th{{background:#f7f9fa}}
.pass{{color:#197c62;font-weight:700}}.fail,.timeout{{color:#b6403b;font-weight:700}}.inconclusive{{color:#9a6a00;font-weight:700}}
code{{background:#f0f3f5;padding:2px 4px}}.scroll{{overflow:auto}}
</style><main>
<section><h1>EchoMem 六项 4U8G 目标自动化验收</h1>
<div class="muted">生成时间：{html.escape(result.get("created_at", ""))} · 真实 HTTP：是 · mock 模型：否</div>
<p>报告只依据实际运行证据判定；缺少部署控制或服务端指标时标记为 INCONCLUSIVE，不推断为通过。</p></section>
<section class="scroll"><h2>逐 profile 目标状态</h2>
<table><thead><tr><th>Profile</th><th>目标</th><th>状态</th><th>判定目标</th><th>说明</th><th>归属</th><th>证据</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></section>
<section class="scroll"><h2>探针与黑盒证据明细</h2>
<p class="muted">这里显示真实 HTTP 探针实际检查到的内容。没有真实输入、控制能力或服务端观测时，状态保持 INCONCLUSIVE。</p>
{"".join(details)}</section>
</main></html>"""
    path.write_text(doc, encoding="utf-8")


def _acquire_output_lock(out_dir: Path):
    """Prevent two objective jobs from writing the same evidence tree."""
    lock_path = out_dir / ".objective-suite.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise RuntimeError(
                f"objective output directory is already locked: {out_dir}"
            ) from exc
        raise
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EchoMem objective acceptance suite")
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--profile", default="", help="只运行一个 profile；默认运行全部")
    parser.add_argument(
        "--base-url",
        default="",
        help="覆盖 profile 中的 EchoMem 地址（仅当前运行生效）",
    )
    parser.add_argument(
        "--preflight-config",
        default="",
        help="覆盖 profile 中的实际 EchoMem config.json（仅当前运行生效）",
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--quick", action="store_true", help="bounded smoke matrix")
    parser.add_argument(
        "--full",
        action="store_true",
        help="强制完整模式；4U8G 运行 PR397 12 个 + PR421 25 个场景，不得与 --quick 同时使用",
    )
    parser.add_argument("--scenarios", default="", help="覆盖场景列表，逗号分隔")
    parser.add_argument("--quick-duration-cap-s", type=float, default=30.0)
    parser.add_argument("--quick-case-timeout-s", type=float, default=180.0)
    parser.add_argument(
        "--quick-barrier-count-cap",
        type=int,
        default=32,
        help=(
            "quick 模式的 barrier Commit 上限，默认 32；低于该值不能验收严格优先级。"
            "完整套件请显式传 --barrier-count-cap 0"
        ),
    )
    parser.add_argument(
        "--quick-commit-timeout-s",
        type=float,
        default=180.0,
        help="quick 模式单个真实 Commit 的最终状态等待上限",
    )
    parser.add_argument(
        "--quick-seed-commit-timeout-s",
        type=float,
        default=180.0,
        help="quick 模式共享 seed warmup 的真实 Commit 等待上限",
    )
    parser.add_argument("--quick-commit-max-attempts", type=int, default=1)
    parser.add_argument("--quick-commit-retry-backoff-s", type=float, default=0.0)
    parser.add_argument(
        "--quick-barrier-wave-size",
        type=int,
        default=4,
        help="quick 模式每波最多并发的 Commit 数，降低队列堆积",
    )
    parser.add_argument(
        "--quick-include-seed",
        action="store_true",
        help="quick 默认跳过真实模型灌种；打开后保留灌种",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=14400.0,
        help="objective suite 外层超时；正式 4U8G 默认给足完整场景和探针预算",
    )
    parser.add_argument(
        "--max-wall-clock-s",
        type=float,
        default=10800.0,
        help="所有 profile 与探针共享的最大 wall-clock 时间；默认 10800 秒",
    )
    parser.add_argument(
        "--probe-budget-s",
        type=float,
        default=900.0,
        help=(
            "为 formal 完成后的能力/故障/恢复补测预留的 wall-clock 时间；"
            "设为 0 表示不预留，默认 900 秒"
        ),
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="只根据已有 suite.json 生成总报告",
    )
    parser.add_argument(
        "--gaps-only",
        action="store_true",
        help=(
            "只运行配置的黑盒补测探针并复用已有 suite.json；"
            "不重新执行 formal suite，需配合 --suite-path 或 profile.suite_path"
        ),
    )
    parser.add_argument(
        "--suite-path",
        type=Path,
        default=None,
        help="配合 --skip-run 读取已有 formal suite.json；不重新发送压测请求",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=(
            "加载 KEY=VALUE 环境文件供 formal suite/探针使用；"
            "适合服务器 Docker env 文件，密钥不会写入报告"
        ),
    )
    parser.add_argument(
        "--skip-prepare",
        action="store_true",
        help=(
            "跳过 profile.prepare_command；适用于 runner 容器已固定到目标实例，"
            "避免执行仅宿主机可用的切换命令"
        ),
    )
    args = parser.parse_args()
    if args.full and args.quick:
        parser.error("--full 与 --quick 不能同时使用")
    if args.full and args.scenarios:
        parser.error("--full 不能与 --scenarios 同时使用；请使用 profile 的完整场景目录")
    if args.full:
        args.quick = False
    if args.gaps_only:
        args.skip_run = True
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be > 0")
    if args.max_wall_clock_s <= 0:
        parser.error("--max-wall-clock-s must be > 0")
    if args.probe_budget_s < 0:
        parser.error("--probe-budget-s must be >= 0")
    suite_deadline = time.monotonic() + min(args.timeout_s, args.max_wall_clock_s)

    child_env = dict(os.environ)
    if args.env_file is not None:
        try:
            child_env.update(load_env_file(args.env_file.expanduser().resolve()))
        except OSError as exc:
            parser.error(f"无法读取 --env-file: {exc}")
    # Keep the same environment for every nested runner and probe.  The
    # target service may already have these variables, but the harness
    # subprocesses must independently pass the real-model preflight.
    os.environ.update(child_env)

    profiles = load_profiles(args.profiles)
    if args.profile:
        profiles = [item for item in profiles if str(item["name"]) == args.profile]
    if not profiles:
        parser.error("没有匹配的 profile")
    # A deployment command must be able to bind the harness to the same
    # address/configuration used by EchoMem without editing a shared example
    # profile. This prevents stale local templates from being used silently.
    for profile in profiles:
        if args.base_url:
            profile["base_url"] = args.base_url
        if args.preflight_config:
            profile["preflight_config"] = args.preflight_config
    if args.gaps_only and args.suite_path is None and not any(
        str(item.get("suite_path") or item.get("suite") or "").strip()
        for item in profiles
    ):
        parser.error("--gaps-only 需要 --suite-path 或 profile.suite_path")

    configured_probe_profiles = [
        profile for profile in profiles
        if any(item["configured"] for item in _probe_plan(profile))
    ]
    # Reserve time only when a profile actually asks for post-suite probes.
    # A pure formal run should keep the full wall-clock budget available.
    probe_reserve_s = (
        min(args.probe_budget_s, args.max_wall_clock_s)
        if configured_probe_profiles and not args.skip_run
        else 0.0
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_lock = None
    if not args.skip_run:
        try:
            output_lock = _acquire_output_lock(args.out_dir)
        except RuntimeError as exc:
            parser.error(str(exc))
    output_profiles: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(profile["name"])
        profile_dir = args.out_dir / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        command_result: dict[str, Any] = {}
        suite_path = profile_dir / "suite.json"
        probe_plan = _probe_plan(profile)

        if not args.skip_run:
            prepare = str(profile.get("prepare_command") or "").strip()
            if prepare and not args.skip_prepare:
                command_result["prepare"] = run_command(
                    ["bash", "-lc", prepare],
                    timeout_s=_bounded_timeout(
                        min(args.timeout_s, 900),
                        suite_deadline,
                    ),
                    env=child_env,
                )
                if command_result["prepare"]["status"] != "PASS":
                    output_profiles.append({
                        **profile,
                        "name": name,
                        "command": command_result,
                        "objectives": objective_statuses(
                            {}, recovery_configured=False, metrics_configured=False
                        ),
                    })
                    continue
            elif prepare and args.skip_prepare:
                command_result["prepare"] = {
                    "status": "SKIPPED",
                    "reason": "命令由 --skip-prepare 跳过；目标实例由部署环境固定",
                    "command": ["bash", "-lc", prepare],
                }
            tenant_config = _resolve_profile_path(
                str(profile.get("tenant_config") or ""), args.profiles
            )
            preflight_config = _resolve_profile_path(
                str(profile.get("preflight_config") or ""), args.profiles
            )
            if not tenant_config or not preflight_config:
                command_result["run"] = {
                    "status": "INCONCLUSIVE",
                    "reason": "profile 缺少 tenant_config 或 preflight_config",
                }
            else:
                scenarios = args.scenarios or (QUICK_SCENARIOS if args.quick else "")
                formal_scenarios = _formal_scenario_filter(
                    name,
                    scenarios,
                    quick=args.quick,
                )
                command = [
                    sys.executable,
                    "-m",
                    "performance.formal_suite",
                    "--base-url",
                    str(profile.get("base_url") or "http://127.0.0.1:8010"),
                    "--tenant-config",
                    tenant_config,
                    "--preflight-config",
                    preflight_config,
                    "--profile",
                    # Quick 4U8G uses the bounded catalog. A normal 4U8G
                    # run must use the explicit 37-case catalog: PR397 has
                    # 12 scenarios and PR421 has 25. The legacy ``complete``
                    # profile is a smaller historical catalog.
                    _formal_profile_name(name, quick=args.quick),
                    "--instance-profile",
                    name,
                    "--auth-header",
                    str(profile.get("auth_header") or "X-Auth-Key"),
                    "--repeats",
                    "1",
                    "--commit-timeout-s",
                    str(args.quick_commit_timeout_s if args.quick else 600.0),
                    "--seed-commit-timeout-s",
                    str(args.quick_seed_commit_timeout_s if args.quick else 600.0),
                    "--commit-max-attempts",
                    str(args.quick_commit_max_attempts if args.quick else 3),
                    "--commit-retry-backoff-s",
                    str(args.quick_commit_retry_backoff_s if args.quick else 2.0),
                    "--seed-concurrency",
                    str(4 if args.quick else 2),
                    "--barrier-wave-size",
                    str(args.quick_barrier_wave_size if args.quick else 32),
                    "--barrier-drain-timeout-s",
                    str(10.0 if args.quick else 600.0),
                    "--out-dir",
                    str(profile_dir / "formal"),
                ]
                preflight_payload = read_json(Path(preflight_config))
                auth_config = (
                    preflight_payload.get("auth")
                    if isinstance(preflight_payload, dict)
                    else {}
                )
                # The EchoMem config's internal auth mode is not the same as
                # the wire authentication mode exposed by the deployment.
                # For example, a service may load a local config while its
                # HTTP gateway still requires independent X-API-Key tenant
                # credentials.  Only an explicit profile/CLI setting may
                # select --local-auth; never infer it from config.json.
                if formal_scenarios:
                    command += [
                        "--scenarios", formal_scenarios,
                        "--duration-cap-s", str(args.quick_duration_cap_s),
                        "--case-timeout-s", str(args.quick_case_timeout_s),
                        "--barrier-count-cap", str(args.quick_barrier_count_cap),
                    ]
                    if args.quick:
                        # Keep the real-model warm-up bounded even when the
                        # selected quick matrix contains 16/32-tenant
                        # capacity probes.  Unseeded tenants still remain
                        # valid for scheduler/capacity evidence, but the
                        # report must retain that limitation explicitly.
                        command += ["--quick-seed-tenant-cap", "4"]
                    if args.quick:
                        command += ["--quick-mode"]
                include_quick_seed = bool(
                    args.quick_include_seed
                    or profile.get("quick_include_seed")
                )
                if args.quick and not include_quick_seed:
                    _append_quick_seed_options(command, include_seed=False)
                elif args.quick:
                    # Seed once for hot-cache evidence; reuse it across cases.
                    _append_quick_seed_options(command, include_seed=True)
                if bool(profile.get("allow_partial_tenants")):
                    command += ["--allow-partial-tenants"]
                remaining_before_formal = _bounded_timeout(
                    args.timeout_s,
                    suite_deadline,
                )
                remaining_for_formal = max(
                    0.0,
                    remaining_before_formal - probe_reserve_s,
                )
                if remaining_for_formal <= 0:
                    command_result["run"] = {
                        "status": "TIMEOUT",
                        "reason": (
                            "测试平台为 post-suite 探针预留了全部剩余预算；"
                            "formal suite 未启动"
                        ),
                    }
                    formal_root = profile_dir / "formal"
                    candidates = []
                else:
                    command += [
                        "--max-wall-clock-s",
                        str(remaining_for_formal),
                    ]
                    command_result["run"] = run_command(
                        command,
                        timeout_s=remaining_for_formal,
                        env=child_env,
                    )
                    formal_root = profile_dir / "formal"
                    candidates = []
                    if (formal_root / "suite.json").is_file():
                        candidates.append(formal_root / "suite.json")
                    candidates.extend(sorted(formal_root.glob("*/suite.json")))
                if candidates:
                    suite_path = candidates[-1]
        else:
            configured_suite = str(
                (
                    args.suite_path
                    if args.suite_path is not None
                    else profile.get("suite_path") or profile.get("suite") or ""
                )
            ).strip()
            if configured_suite:
                suite_path = Path(configured_suite).expanduser().resolve()
                command_result["run"] = {
                    "status": "PASS",
                    "mode": "read-only-audit",
                    "reason": "只读取已有 suite.json，不重新发送压测请求",
                }

        suite = read_json(suite_path)
        formal_root = (
            suite_path.parent
            if args.skip_run and suite_path.is_file()
            else profile_dir / "formal"
        )
        probe_artifacts, probe_commands = _run_configured_probes(
            profile,
            profile_dir=profile_dir,
            profiles_path=args.profiles,
            formal_root=formal_root,
            timeout_s=_bounded_timeout(args.timeout_s, suite_deadline),
            quick=args.quick,
            deadline=suite_deadline,
        )
        probe_command_names = {
            "fault_plan": "fault_suite",
        }
        for item in probe_plan:
            if not isinstance(item, dict) or not item.get("configured"):
                continue
            probe_name = str(item.get("name") or "")
            command_name = probe_command_names.get(probe_name, probe_name)
            execution = probe_commands.get(command_name)
            artifact = probe_artifacts.get(command_name)
            if isinstance(artifact, dict) and artifact.get("status"):
                item["status"] = str(artifact["status"])
            elif isinstance(execution, dict) and execution.get("status"):
                item["status"] = str(execution["status"])
            elif probe_name == "blackbox_probe":
                item["status"] = "not_run"
                item["reason"] = "本轮没有完成 Commit 证据，未启动自动黑盒契约探针"
            else:
                item["status"] = "not_run"
                item["reason"] = "未生成该探针的执行记录"
        suite = {**suite, **probe_artifacts}
        command_result.update(probe_commands)

        completed_runs, submitted_runs = _formal_run_counts(suite)
        coverage = _formal_coverage(suite)
        platform_coverage = platform_objective_coverage(
            profile,
            suite,
            probe_plan,
            coverage,
        )
        profile_execution_status = (
            "completed"
            if coverage["status"] == "complete"
            else str(command_result.get("run", {}).get("status") or "not_run")
        )
        output_profiles.append({
            **profile,
            "name": name,
            "suite": str(suite_path),
            "profile_execution_status": profile_execution_status,
            "completed_runs": completed_runs,
            "submitted_runs": submitted_runs,
            "coverage": coverage,
            "platform_objective_coverage": platform_coverage,
            "probe_plan": probe_plan,
            "probe_budget_reserved_s": probe_reserve_s,
            **probe_artifacts,
            "command": command_result,
            "objectives": objective_statuses(
                {
                    **suite,
                    "profile_name": name,
                    "fairness_expectations": profile.get("fairness_expectations", {}),
                    "observability_expectations": profile.get("observability", {}),
                    "instance_profiles": [{
                        "name": name,
                        "status": profile_execution_status,
                        "completed_runs": completed_runs,
                        "coverage": coverage,
                    }],
                },
                recovery_configured=bool(profile.get("commit_recovery")),
                metrics_configured=bool(profile.get("metrics_enabled", True)),
            ),
        })

    completed_profile_records = [
        {
            "name": str(profile.get("name") or ""),
            "status": str(profile.get("profile_execution_status") or ""),
            "completed_runs": int(profile.get("completed_runs") or 0),
            "submitted_runs": int(profile.get("submitted_runs") or 0),
            "expected_runs": int(
                (profile.get("coverage") or {}).get("expected_runs") or 0
            ),
            "coverage_status": str(
                (profile.get("coverage") or {}).get("status") or "unknown"
            ),
        }
        for profile in output_profiles
    ]
    completed_profile_count = sum(
        1
        for item in completed_profile_records
        if item["status"] == "completed" and item["completed_runs"] > 0
    )
    result = {
        "created_at": now(),
        "wall_clock_budget_s": args.max_wall_clock_s,
        "probe_budget_reserved_s": probe_reserve_s,
        "profiles": output_profiles,
        "objectives": OBJECTIVES,
        "instance_profiles": completed_profile_records,
        "multi_spec_completed_count": completed_profile_count,
    }
    (args.out_dir / "objective-suite.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    render_report(result, args.out_dir / "objective-suite.html")
    print(args.out_dir / "objective-suite.html")
    return 0 if output_profiles and all(
        (item.get("coverage") or {}).get("status") == "complete"
        or args.skip_run
        for item in output_profiles
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
