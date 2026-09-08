"""Orchestrator unit fixtures do not perform model or service requests."""
import argparse
import json

import pytest

from performance.targets.echomem import observation_run as module


def setup_run(tmp_path, monkeypatch):
    tenants = tmp_path / "tenants.json"
    tenants.write_text('{"tenants": []}')
    profile = {"name": "4U8G", "base_url": "http://unused.invalid",
               "tenant_config": str(tenants), "resource_evidence": {}, "readiness": {}}
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"profiles": [profile]}))
    monkeypatch.setattr(module, "_configure", lambda p, *a, **k: p)
    monkeypatch.setattr(module, "check_readiness", lambda p: {"ok": True})
    monkeypatch.setattr(module, "platform_snapshot", lambda: {"git_commit": "unit-test"})
    events = []

    def suite(*args, **kwargs):
        events.append("bounded-suite")
        return {"runs": []}

    def probes(*args, **kwargs):
        events.append("probes")
        return {}, []

    monkeypatch.setattr(module, "run_suite", suite)
    monkeypatch.setattr(module, "run_configured_probes", probes)
    args = argparse.Namespace(profiles=profiles, profile="4U8G", env_file=None,
        out_dir=tmp_path / "out", metrics="M1,M3", quick=False, resume=False, timeout_s=600)
    return args, events


def test_publish_checkpoint_before_capacity(tmp_path, monkeypatch):
    args, events = setup_run(tmp_path, monkeypatch)

    def capacity(profile, options, output):
        events.append("capacity")
        data = json.loads((output / "summary.json").read_text())
        assert data["checkpoint"] is True
        assert data["pending_metrics"] == ["M1"]
        assert "阶段性结果，尚未完成" in (output / "report.html").read_text()
        assert (output / "records.csv").is_file()
        assert (output / "metrics_samples.csv").is_file()
        return []

    monkeypatch.setattr(module, "_run_m1_profiles", capacity)
    result = module.run(args)
    assert events == ["bounded-suite", "probes", "capacity"]
    assert not result.get("checkpoint")


def test_capacity_error_keeps_earlier_report_and_marks_interruption(tmp_path, monkeypatch):
    args, events = setup_run(tmp_path, monkeypatch)

    def capacity(*args):
        raise RuntimeError("unit injected failure")

    monkeypatch.setattr(module, "_run_m1_profiles", capacity)
    with pytest.raises(RuntimeError, match="unit injected failure"):
        module.run(args)
    data = json.loads((args.out_dir / "summary.json").read_text())
    assert data["status"] == "EXECUTION_ERROR"
    assert data["checkpoint"] is False
    assert data["pending_metrics"] == ["M1"]
    assert "运行中断" in (args.out_dir / "report.html").read_text()
    assert events == ["bounded-suite", "probes"]


def test_uncleared_fault_prevents_capacity_without_losing_checkpoint(tmp_path, monkeypatch):
    args, events = setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "check_readiness", lambda p: {"ok": False})
    monkeypatch.setattr(module, "_run_m1_profiles", lambda *a: pytest.fail("capacity must not run"))
    with pytest.raises(RuntimeError, match="capacity_control_preflight_failed"):
        module.run(args)
    data = json.loads((args.out_dir / "summary.json").read_text())
    assert data["capacity_start_readiness"]["ok"] is False
    assert events == ["bounded-suite", "probes"]
