#!/usr/bin/env python3
"""Measure bystander Search degradation during a real tenant fault.

The fault must be controlled by the deployment (HTTP endpoint or command).
This probe never fabricates a dependency failure and never treats a control
success without before/after Search samples as an isolation pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

try:
    from ._client import EchoMemHTTP, TenantSpec, load_tenant_specs
except ImportError:
    from _client import EchoMemHTTP, TenantSpec, load_tenant_specs

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], q: float = 0.95) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def control(
    config: dict[str, Any],
    *,
    action: str,
    target_tenant: str = "",
    timeout_s: float,
) -> dict[str, Any]:
    endpoint = str(config.get("endpoint") or "").strip()
    command = str(config.get("command") or "").strip()
    started = time.monotonic()
    try:
        if endpoint:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(
                    {
                        "action": action,
                        "target_tenant": target_tenant,
                        "tenant": target_tenant,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = response.read().decode("utf-8", errors="replace")[-4000:]
                return {
                    "status": PASS if 200 <= response.status < 300 else FAIL,
                    "backend": "http",
                    "status_code": response.status,
                    "body": body,
                    "elapsed_s": time.monotonic() - started,
                }
        if command:
            rendered = command.format(
                action=action,
                target_tenant=target_tenant,
                tenant=target_tenant,
            )
            completed = subprocess.run(
                rendered,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            return {
                "status": PASS if completed.returncode == 0 else FAIL,
                "backend": "command",
                "command": shlex.split(rendered),
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "elapsed_s": time.monotonic() - started,
            }
        return {
            "status": INCONCLUSIVE,
            "reason": "未配置真实故障控制 endpoint 或 command",
        }
    except urllib.error.HTTPError as exc:
        return {
            "status": INCONCLUSIVE if exc.code == 404 else FAIL,
            "backend": "http",
            "status_code": exc.code,
            "body": exc.read().decode("utf-8", errors="replace")[-4000:],
            "elapsed_s": time.monotonic() - started,
        }
    except (OSError, urllib.error.URLError, subprocess.TimeoutExpired) as exc:
        return {
            "status": FAIL,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": time.monotonic() - started,
        }


def sample_search(
    clients: dict[str, EchoMemHTTP],
    sessions: dict[str, str],
    *,
    count: int,
    workers: int,
    timeout_s: float,
    phase: str,
) -> dict[str, Any]:
    def one(tenant_id: str, index: int) -> dict[str, Any]:
        started = time.monotonic()
        response = clients[tenant_id].search(
            sessions[tenant_id],
            f"PR397 fault isolation {phase} {index}",
            timeout_s=timeout_s,
        )
        return {
            "tenant": tenant_id,
            "status_code": response.status_code,
            "elapsed_s": time.monotonic() - started,
            "error": response.error,
        }

    jobs = [
        (tenant_id, index)
        for index in range(max(1, count))
        for tenant_id in sessions
    ]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        rows = list(executor.map(lambda item: one(*item), jobs))
    by_tenant: dict[str, dict[str, Any]] = {}
    for tenant_id in sessions:
        selected = [row for row in rows if row["tenant"] == tenant_id]
        latencies = [
            float(row["elapsed_s"])
            for row in selected
            if isinstance(row.get("status_code"), int)
            and 200 <= row["status_code"] < 300
        ]
        by_tenant[tenant_id] = {
            "submitted": len(selected),
            "succeeded": len(latencies),
            "p95_s": percentile(latencies),
            "median_s": median(latencies) if latencies else None,
            "rows": selected,
        }
    return {"phase": phase, "by_tenant": by_tenant}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--target-tenant", required=True)
    parser.add_argument("--bystander-tenants", default="")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout-s", type=float, default=20)
    parser.add_argument("--control-timeout-s", type=float, default=30)
    parser.add_argument("--auth-header", default="X-Auth-Key")
    args = parser.parse_args()

    specs = load_tenant_specs(args.tenant_config)
    selected = {
        spec.tenant_id: spec
        for spec in specs
        if spec.tenant_id == args.target_tenant
        or spec.tenant_id in {
            item.strip() for item in args.bystander_tenants.split(",") if item.strip()
        }
    }
    bystanders = [
        tenant_id for tenant_id in selected
        if tenant_id != args.target_tenant
    ]
    result: dict[str, Any] = {
        "created_at": now(),
        "base_url": args.base_url,
        "target_tenant": args.target_tenant,
        "bystander_tenants": bystanders,
        "real_http": True,
        "mock_model": False,
    }
    if not bystanders or args.target_tenant not in selected:
        result.update({
            "status": INCONCLUSIVE,
            "reason": "故障租户或旁观租户配置不足，至少需要 1 个旁观租户",
        })
    else:
        clients = {
            tenant_id: EchoMemHTTP(
                args.base_url,
                spec.auth_key,
                tenant_id=spec.tenant_id,
                user_id=spec.user_id,
                account_id=spec.account_id,
                agent_id="pr397-fault-isolation",
                auth_header=args.auth_header,
            )
            for tenant_id, spec in selected.items()
        }
        sessions = {
            tenant_id: clients[tenant_id].open_session(
                tenant_id, f"pr397-fault-isolation-{tenant_id}"
            )[0]
            for tenant_id in selected
        }
        before = sample_search(
            clients, sessions, count=args.samples, workers=args.workers,
            timeout_s=args.timeout_s, phase="before",
        )
        enable = control(
            {"endpoint": args.endpoint, "command": args.command},
            action="enable",
            target_tenant=args.target_tenant,
            timeout_s=args.control_timeout_s,
        )
        during: dict[str, Any] = {}
        disable: dict[str, Any] = {
            "status": INCONCLUSIVE,
            "reason": "故障尚未启用，未执行恢复动作",
        }
        try:
            if enable.get("status") == PASS:
                during = sample_search(
                    clients, sessions, count=args.samples, workers=args.workers,
                    timeout_s=args.timeout_s, phase="during",
                )
        finally:
            # Always restore a real dependency after sampling, even when a
            # client-side error interrupts the observation phase.
            disable = control(
                {"endpoint": args.endpoint, "command": args.command},
                action="disable",
                target_tenant=args.target_tenant,
                timeout_s=args.control_timeout_s,
            )
        result.update({"before": before, "enable": enable, "during": during, "disable": disable})
        degradations: dict[str, float] = {}
        for tenant_id in bystanders:
            baseline = (before.get("by_tenant", {}).get(tenant_id) or {}).get("p95_s")
            degraded = (during.get("by_tenant", {}).get(tenant_id) or {}).get("p95_s")
            if baseline and degraded is not None:
                degradations[tenant_id] = (float(degraded) - float(baseline)) / float(baseline)
        result["bystander_p95_degradation"] = max(degradations.values(), default=None)
        result["degradation_by_tenant"] = degradations
        result["bystander_tenants"] = {
            tenant_id: {
                "baseline_p95_s": (
                    (before.get("by_tenant", {}).get(tenant_id) or {}).get("p95_s")
                ),
                "fault_p95_s": (
                    (during.get("by_tenant", {}).get(tenant_id) or {}).get("p95_s")
                ),
                "degradation": degradations.get(tenant_id),
                "baseline_submitted": (
                    (before.get("by_tenant", {}).get(tenant_id) or {}).get("submitted", 0)
                ),
                "fault_submitted": (
                    (during.get("by_tenant", {}).get(tenant_id) or {}).get("submitted", 0)
                ),
            }
            for tenant_id in bystanders
        }
        complete = (
            enable.get("status") == PASS
            and disable.get("status") == PASS
            and len(degradations) == len(bystanders)
        )
        if not complete:
            result["status"] = INCONCLUSIVE
            result["reason"] = "故障控制或旁观租户前后 Search P95 证据不完整"
        else:
            result["fault_recovered"] = True
            result["status"] = (
                PASS
                if result["bystander_p95_degradation"] <= 0.20
                else FAIL
            )
            result["reason"] = (
                "旁观租户 Search P95 劣化不超过 20%"
                if result["status"] == PASS
                else "至少一个旁观租户 Search P95 劣化超过 20%"
            )
    result["finished_at"] = now()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
