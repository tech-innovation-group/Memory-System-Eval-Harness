"""Read-only control-plane gates before expensive six-metric workloads."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from performance.targets.echomem.probes.docker_inspect import inspect_container


def recall_admission_evidence(config: dict, target_environment: list[str]) -> dict:
    """Inspect declared target inputs; never infer an unobserved version's default."""
    recall = config.get("recall") or {}
    recall = recall if isinstance(recall, dict) else {}
    value = recall.get("max_inflight")
    source = "config_file" if value is not None else "implicit_default"
    for entry in target_environment:
        name, separator, raw = entry.partition("=")
        if separator and name == "ECHOMEM_RECALL_MAX_INFLIGHT" and raw:
            value, source = raw, "target_container_environment"
    if value is None:
        return {"status": "UNPINNED", "source": source, "declared_max_inflight": None,
                "runtime_verified": False,
                "note": "Verify the target version default or retrieval_admission_rejected logs; stage concurrency does not override recall.max_inflight."}
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError
        limit = int(value)
        if limit < 0:
            raise ValueError
    except (TypeError, ValueError):
        return {"status": "INVALID", "source": source, "declared_max_inflight": None,
                "runtime_verified": False}
    return {"status": "CAP_DISABLED" if limit == 0 else "DECLARED",
            "source": source, "declared_max_inflight": limit, "runtime_verified": False,
            "note": "Declared input only; confirm config mount, restart and runtime evidence. No client load was lowered."}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get(url: str, token: str = "") -> tuple[int | None, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-EchoMem-Test-Token"] = token
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        # Do not forward the protected control token through redirects.
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(1024 * 1024)
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = {}
            return response.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(1024 * 1024))
        except (OSError, ValueError, UnicodeError):
            payload = {}
        finally:
            exc.close()
        return exc.code, payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return None, {}


def check_readiness(profile: dict) -> dict:
    """Only inspect Docker and GET APIs. Never export their raw responses."""
    checks = []
    resource = {}
    inspected = {}

    def record(name, ok, owner, action, **observed):
        checks.append({"name": name, "status": "PASS" if ok else "BLOCKED",
                       "owner": owner, "next_action": action, **observed})

    container = str(profile.get("resource_container") or "")
    require_4u8g = profile.get("require_4u8g", True) is not False
    try:
        inspected = inspect_container(container)
        limits = inspected["HostConfig"]
        cpus = float(limits.get("NanoCpus", 0)) / 1e9
        if not cpus and limits.get("CpuPeriod", 0) > 0:
            cpus = limits.get("CpuQuota", 0) / limits["CpuPeriod"]
        resource = {"container": container, "container_id": inspected.get("Id"),
                    "image_id": inspected.get("Image"), "cpus": cpus,
                    "memory_bytes": limits.get("Memory"),
                    "running": inspected.get("State", {}).get("Running") is True,
                    "resource_policy": "fixed-4u8g" if require_4u8g else "host-default"}
        resource_ok = resource["running"]
        action = "Start the dedicated target container and verify Docker access."
        if require_4u8g:
            resource_ok = resource_ok and cpus == 4 and resource["memory_bytes"] == 8 * 1024**3
            action = "Start the dedicated target with --cpus=4 --memory=8g; verify Docker access."
        record("resource-container", resource_ok, "deployment", action, **resource)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        record("resource-container", False, "deployment", "Cannot inspect the target container; check its name and Docker access.")

    config_path = profile.get("preflight_config")
    if config_path:
        try:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("native config required")
            admission = recall_admission_evidence(config, (inspected.get("Config") or {}).get("Env") or [])
        except (OSError, ValueError):
            admission = {"status": "UNAVAILABLE", "runtime_verified": False}
        checks.append({"name": "recall-entry-limit", "status": "INFO", "advisory": True,
                       "owner": "EchoMem / deployment", "evidence": admission,
                       "next_action": "Inspect the independent outer Recall cap before capacity testing; this advisory never reduces offered load."})

    base = str(profile.get("base_url") or "").rstrip("/")
    parsed = urlsplit(base)
    valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    record("target-url", valid_url, "configuration", "Use an HTTP(S) base_url without embedded credentials or query parameters.")
    if not valid_url:
        return {"ok": False, "checks": checks, "resource_evidence": resource}
    code, payload = _get(base + "/api/v1/system/ready")
    raw_checks = payload.get("checks")
    raw_checks = raw_checks if isinstance(raw_checks, dict) else {}
    component_checks = {
        name: value for name, value in raw_checks.items()
        if name in {"runtime", "filesystem", "engine_registry", "auth", "model", "commit_admission"}
        and isinstance(value, str)
        and value in {"ok", "error", "misconfigured", "saturated", "closing", "recovering"}
    }
    record("ready", code == 200 and bool(payload), "EchoMem / deployment",
           "Check the target readiness response and service startup.", http_status=code,
           component_checks=component_checks)
    code, _ = _get(base + "/metrics")
    record("metrics", code == 200, "EchoMem / deployment",
           "Expose the target Prometheus endpoint to this runner.", http_status=code)
    for section, path in (("fault_isolation", "/api/inspect/test-control/fault"),
                          ("tenant_observability", "/api/inspect/tenant-observability")):
        params = profile.get(section) or {}
        if params.get("enabled") is False:
            record(section, True, "not selected", "No action required.", skipped=True)
            continue
        token = os.environ.get(str(params.get("token_env") or "ECHOMEM_TEST_CONTROL_TOKEN"), "")
        endpoint = str(params.get("endpoint") or base + path)
        target = urlsplit(endpoint)
        same_origin = (target.scheme, target.netloc) == (parsed.scheme, parsed.netloc)
        if not token or not same_origin:
            record(section, False, "deployment / configuration",
                   "Enable the protected API, pass the same token to the runner, and keep its endpoint on the target origin.")
            continue
        code, payload = _get(endpoint, token)
        schema_ok = isinstance(payload.get("faults"), dict) if section == "fault_isolation" else isinstance(payload.get("rows"), list)
        clean = not payload.get("faults") if section == "fault_isolation" else True
        record(section, code == 200 and schema_ok and clean, "EchoMem / deployment",
               "Verify protected API availability/token and clear prior test faults; 404 alone does not prove missing implementation.",
               http_status=code, schema_valid=schema_ok, no_active_faults=clean)
    return {"ok": all(c["status"] == "PASS" for c in checks if not c.get("advisory")),
            "checks": checks, "resource_evidence": resource}
