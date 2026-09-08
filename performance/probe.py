"""Probe execution: one-shot assertion checks with four-state outcomes.

A probe is a Python module exporting ``run(ctx)``; it performs directed
checks (injection -> verify -> cleanup expressed in ordinary Python) and
records each assertion via ``ctx.check`` with one of PASS / FAIL /
NOT_IMPLEMENTED / INCONCLUSIVE.  The runner executes it exactly once,
aggregates the checks, and reports.

This is the second execution model next to load scenes: scenes sustain
traffic and measure latency, probes verify behaviour once.  Probes live
under ``targets/<system>/probes/`` and are system-specific; this module
is fully generic (it knows nothing about EchoMem).

``run_configured_probe`` drives a single configured probe as a subprocess
(write a temp YAML profile -> probe CLI -> read the artifact back); the
orchestrators under ``targets/<system>/orchestrator/`` map their profile
sections to these calls.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

import yaml

# 支持从任意位置运行（`cd performance && python probe.py` 或项目根 `python -m performance.probe`）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from performance.ctx import ConnectionRegistry, Ctx, PROBE_STATUSES, ProbeCheck
from performance.dispatch import dispatch
from performance.profile import Profile
from performance.records import RequestRecord
from performance.util import read_json, run_command

# Statuses that make the probe exit non-zero, unless the probe overrides
# ``exit_on`` (e.g. a data-collection probe never fails, a recovery probe
# treats INCONCLUSIVE as a non-failure).
DEFAULT_EXIT_ON = ("FAIL", "NOT_IMPLEMENTED", "INCONCLUSIVE")


@dataclass
class ProbeModule:
    """A loaded probe file: the run function and exit-code policy."""

    name: str
    description: str
    run: Callable[[Ctx], None]
    exit_on: tuple[str, ...] = DEFAULT_EXIT_ON


class ProbeError(ValueError):
    """A probe file is missing or does not export a valid ``run`` function."""


def load_probe(path: str | Path) -> ProbeModule:
    """Import a probe file and extract its contract.

    A probe must export ``run(ctx)`` (executed exactly once).  Optional
    ``exit_on`` is a tuple of statuses that count as failure for the exit
    code (default: everything except PASS).
    """
    probe_path = Path(path)
    if not probe_path.is_file():
        raise ProbeError(f"probe file not found: {probe_path}")
    name = probe_path.stem
    spec = importlib.util.spec_from_file_location(name, probe_path)
    if spec is None or spec.loader is None:
        raise ProbeError(f"cannot load probe module: {probe_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ProbeError(f"probe module failed to import: {probe_path}: {exc}") from exc

    run = getattr(module, "run", None)
    if not callable(run):
        raise ProbeError(f"probe {name}: must export a callable `run(ctx)`")
    exit_on = tuple(getattr(module, "exit_on", DEFAULT_EXIT_ON))
    if not all(status in PROBE_STATUSES for status in exit_on):
        raise ProbeError(f"probe {name}: exit_on must contain only probe statuses")
    description = (module.__doc__ or "").strip().splitlines()[0] if module.__doc__ else ""
    return ProbeModule(name=name, description=description, run=run, exit_on=exit_on)


def overall_status(checks: list[ProbeCheck]) -> str:
    """Aggregate checks into one status.

    Any FAIL fails; otherwise any INCONCLUSIVE; otherwise any
    NOT_IMPLEMENTED; otherwise PASS.  Mirrors the original probes'
    aggregation (a probe must never claim PASS when it could not observe).
    """
    statuses = [check.status for check in checks]
    if "FAIL" in statuses:
        return "FAIL"
    if "INCONCLUSIVE" in statuses:
        return "INCONCLUSIVE"
    if "NOT_IMPLEMENTED" in statuses:
        return "NOT_IMPLEMENTED"
    return "PASS"


@dataclass
class ProbeResult:
    checks: list[ProbeCheck]
    records: list[RequestRecord]
    started_at: float
    finished_at: float

    @property
    def elapsed_s(self) -> float:
        return self.finished_at - self.started_at


class ProbeRunner:
    """Executes one probe module once and collects its observations."""

    def __init__(self, profile: Profile, probe: ProbeModule):
        self.profile = profile
        self.probe = probe

    def run(self) -> ProbeResult:
        started_at = time.perf_counter()
        checks: list[ProbeCheck] = []
        records: list[RequestRecord] = []
        seq = itertools.count()
        data_cursor = 0
        connections = ConnectionRegistry()

        def choose(items: list[Any]) -> Any:
            nonlocal data_cursor
            if not items:
                return None
            index = data_cursor
            data_cursor += 1
            return items[index % len(items)]

        ctx = Ctx(
            scene=self.probe.name,
            worker_id=-1,
            tenant_idx=0,
            headers=self.profile.target.headers,
            base_url=self.profile.target.base_url,
            read_timeout_s=self.profile.target.read_timeout_s,
            params=self.profile.params,
            duration_s=0.0,
            stop=threading.Event(),
            record_fn=records.append,
            seq_fn=lambda: next(seq),
            choose_fn=choose,
            phases=[],
            checks=checks,
            tenant_count=len(self.profile.tenants) or 1,
            registry=connections,
        )
        try:
            self.probe.run(ctx)
        except Exception as exc:
            checks.append(
                ProbeCheck(
                    name="probe",
                    status="FAIL",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            # 探针与 Engine 同等对待：结束后关闭并注销本次运行的全部连接。
            connections.close_all()
        return ProbeResult(
            checks=checks, records=records,
            started_at=started_at, finished_at=time.perf_counter(),
        )


def summarize_probe(probe: ProbeModule, result: ProbeResult,
                    profile: Profile) -> dict[str, Any]:
    """Aggregate a probe run into the JSON report structure.

    Keeps the original probes' schema (``status`` / ``created_at`` /
    ``base_url`` / ``real_http`` / ``mock_model`` / ``checks`` /
    ``summary``) so downstream consumers keep working; ``probe`` names
    the executed probe.
    """
    checks = [asdict(check) for check in result.checks]
    statuses = [check["status"] for check in checks]
    return {
        "status": overall_status(result.checks),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": profile.target.base_url,
        "real_http": True,
        "mock_model": False,
        "probe": probe.name,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "pass": statuses.count("PASS"),
            "fail": statuses.count("FAIL"),
            "inconclusive": statuses.count("INCONCLUSIVE"),
            "not_implemented": statuses.count("NOT_IMPLEMENTED"),
        },
    }


def print_probe_summary(summary: dict[str, Any], stream: TextIO = sys.stdout) -> None:
    """Render the probe summary as console lines."""
    s = summary["summary"]
    print(
        f"probe: {summary['probe']}  status={summary['status']}  "
        f"pass={s['pass']} fail={s['fail']} inconclusive={s['inconclusive']} "
        f"not_implemented={s['not_implemented']}  base={summary['base_url']}",
        file=stream,
    )
    for check in summary["checks"]:
        elapsed = f"{check['elapsed_s']:.3f}s" if check["elapsed_s"] is not None else "-"
        print(f"  {check['name']}: {check['status']:<16} ({elapsed}) {check['reason']}",
              file=stream)


def write_probe_output(out_path: Path, summary: dict[str, Any]) -> None:
    """Write the probe report JSON to ``out_path``."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_probe_profile(base_url: str, params: dict[str, Any]) -> Path:
    """把探针 profile 物化为临时 YAML 供探针 CLI 使用。

    ``base_url`` 落在 ``target.base_url``；探针参数保留原始类型。调用方负责
    在运行结束后删除返回的临时文件。
    """
    fd, path = tempfile.mkstemp(prefix="objective-probe-", suffix=".yaml", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                {
                    "name": "objective-probe",
                    "target": {"base_url": base_url},
                    "params": params,
                },
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
    except Exception:
        os.unlink(path)
        raise
    return Path(path)


def probe_command(
    probes_dir: Path,
    scene: str,
    profile_path: Path,
    out_path: Path,
) -> list[str]:
    """构造探针 CLI 子进程命令（``--target general probe``）。"""
    return [
        sys.executable,
        "-m",
        "performance",
        "--target",
        "general",
        "probe",
        "--scene",
        str(probes_dir / scene),
        "--profile",
        str(profile_path),
        "--out",
        str(out_path),
    ]


def preserve_probe_status(
    execution: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """探针级 INCONCLUSIVE/NOT_IMPLEMENTED 不被子进程失败掩盖。"""
    if (
        execution.get("status") == "FAIL"
        and payload.get("status") in {"INCONCLUSIVE", "NOT_IMPLEMENTED"}
    ):
        execution["status"] = payload["status"]
    return execution


def run_configured_probe(
    params: dict[str, Any],
    *,
    probes_dir: Path,
    scene: str,
    output: Path,
    base_url: str,
    timeout_s: float,
    redact_values: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """以子进程方式运行单个配置探针，返回 (payload, execution)。

    写临时 YAML profile → ``probe_command`` 子进程 → 删除临时文件 → 读回
    产物并 ``preserve_probe_status``。payload 为空 dict 表示没有产物；
    调用方负责把 payload 映射到自己的制品键。
    """
    profile_path = write_probe_profile(base_url, params)
    try:
        execution = run_command(
            probe_command(probes_dir, scene, profile_path, output),
            timeout_s=timeout_s,
            redact_values=redact_values,
        )
    finally:
        profile_path.unlink(missing_ok=True)
    payload = read_json(output)
    preserve_probe_status(execution, payload)
    return payload, execution


def main(argv: list[str] | None = None) -> int:
    """CLI: forward ``--target <system>`` to the target's orchestration entry.

    Probe execution itself is driven by the target's ``main.py`` (e.g.
    ``python probe.py --target echomem --profiles ...``).
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    target_result = dispatch(argv)
    if target_result is not None:
        return target_result
    print("error: probe.py requires --target <system>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
