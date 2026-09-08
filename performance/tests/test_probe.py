"""Probe execution: contract loading, four-state aggregation, runner.

The capability probe is the reference migration: it runs against the
mock server and must classify /health as PASS, unconfigured optional
paths as INCONCLUSIVE, and the absent /metrics as NOT_IMPLEMENTED.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from performance.ctx import ConnectionRegistry, Ctx, ProbeCheck
from performance.probe import (
    ProbeError,
    ProbeRunner,
    load_probe,
    overall_status,
    summarize_probe,
)
from performance.profile import load_profile

PROBES_DIR = Path(__file__).resolve().parent.parent / "targets" / "echomem" / "probes"


def _profile(base_url: str, **params) -> object:
    return load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "params": params,
        }
    )


# -- contract -----------------------------------------------------------


def test_load_probe_contract():
    probe = load_probe(PROBES_DIR / "capability.py")
    assert probe.name == "capability"
    assert callable(probe.run)
    assert probe.exit_on == ("FAIL", "NOT_IMPLEMENTED", "INCONCLUSIVE")


def test_load_probe_missing_run_raises(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ProbeError):
        load_probe(bad)


def test_overall_status_priority():
    def check(status: str) -> ProbeCheck:
        return ProbeCheck(name="c", status=status)

    assert overall_status([check("PASS")]) == "PASS"
    assert overall_status([check("PASS"), check("NOT_IMPLEMENTED")]) == "NOT_IMPLEMENTED"
    assert overall_status([check("PASS"), check("INCONCLUSIVE")]) == "INCONCLUSIVE"
    assert overall_status([check("INCONCLUSIVE"), check("FAIL")]) == "FAIL"
    assert overall_status([]) == "PASS"


def test_check_requires_probe_ctx():
    ctx = Ctx(
        scene="s", worker_id=0, tenant_idx=0, headers={}, base_url="http://x",
        read_timeout_s=5, params={}, duration_s=1, stop=threading.Event(),
        record_fn=lambda r: None, seq_fn=lambda: 0,
        choose_fn=lambda items: None, phases=[],
        registry=ConnectionRegistry(),
    )
    with pytest.raises(RuntimeError):
        ctx.check("x", status="PASS")


def test_check_rejects_unknown_status():
    checks: list[ProbeCheck] = []
    ctx = Ctx(
        scene="s", worker_id=0, tenant_idx=0, headers={}, base_url="http://x",
        read_timeout_s=5, params={}, duration_s=1, stop=threading.Event(),
        record_fn=lambda r: None, seq_fn=lambda: 0,
        choose_fn=lambda items: None, phases=[], checks=checks,
        registry=ConnectionRegistry(),
    )
    with pytest.raises(ValueError):
        ctx.check("x", status="MAYBE")


# -- execution against the mock server ----------------------------------


def test_probe_capability_against_mock(server):
    _, _, base_url = server
    probe = load_probe(PROBES_DIR / "capability.py")
    profile = _profile(base_url)
    result = ProbeRunner(profile, probe).run()
    summary = summarize_probe(probe, result, profile)
    assert summary["probe"] == "capability"
    assert summary["base_url"] == base_url
    statuses = [c["status"] for c in summary["checks"]]
    assert "PASS" in statuses  # /health
    assert "NOT_IMPLEMENTED" in statuses  # /metrics -> 404
    assert "INCONCLUSIVE" in statuses  # unconfigured optional paths
    assert summary["status"] == "INCONCLUSIVE"  # inconclusive dominates
    assert summary["summary"]["total"] == len(summary["checks"])
    assert all(c["name"] for c in summary["checks"])


def test_probe_run_exception_records_fail(tmp_path, server):
    _, _, base_url = server
    path = tmp_path / "boom_probe.py"
    path.write_text("def run(ctx):\n    raise RuntimeError('boom')\n", encoding="utf-8")
    probe = load_probe(path)
    profile = _profile(base_url)
    result = ProbeRunner(profile, probe).run()
    assert result.checks == [
        ProbeCheck(name="probe", status="FAIL", reason="RuntimeError: boom")
    ]
    assert summarize_probe(probe, result, profile)["status"] == "FAIL"


def test_probe_exit_on_override(tmp_path, server):
    _, _, base_url = server
    path = tmp_path / "never_fail.py"
    path.write_text(
        "exit_on = ()\n"
        "def run(ctx):\n"
        "    ctx.check('x', status='INCONCLUSIVE', reason='no control configured')\n",
        encoding="utf-8",
    )
    probe = load_probe(path)
    assert probe.exit_on == ()
    profile = _profile(base_url)
    result = ProbeRunner(profile, probe).run()
    summary = summarize_probe(probe, result, profile)
    assert summary["status"] == "INCONCLUSIVE"
    assert summary["status"] not in probe.exit_on  # CLI exits 0
