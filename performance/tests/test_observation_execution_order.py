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


def test_cli_does_not_replace_published_partial_result(tmp_path, monkeypatch):
    args, _ = setup_run(tmp_path, monkeypatch)
    original_evaluate = module.evaluate_observation
    def evaluate(*a, **k):
        result = original_evaluate(*a, **k)
        result["metrics"]["M3"]["retained_evidence"] = [1, 2, 3]
        return result
    monkeypatch.setattr(module, "evaluate_observation", evaluate)
    def capacity(*a):
        raise RuntimeError("unit capacity failure")
    monkeypatch.setattr(module, "_run_m1_profiles", capacity)
    code = module.main(["--profiles", str(args.profiles), "--profile", "4U8G",
                        "--out-dir", str(args.out_dir), "--metrics", "M1,M3"])
    assert code == 2
    summary = json.loads((args.out_dir / "summary.json").read_text())
    assert summary["metrics"]["M3"]["retained_evidence"] == [1, 2, 3]
    assert summary["pending_metrics"] == ["M1"]
    assert "运行中断" in (args.out_dir / "report.html").read_text()
    manifest = json.loads((args.out_dir / "execution-manifest.json").read_text())
    assert manifest["execution_status"] == "EXECUTION_ERROR"
    assert manifest["finished_at"] and manifest["platform_provenance"]["git_commit"] == "unit-test"


def test_uncleared_fault_prevents_capacity_without_losing_checkpoint(tmp_path, monkeypatch):
    args, events = setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "check_readiness", lambda p: {"ok": False})
    monkeypatch.setattr(module, "_run_m1_profiles", lambda *a: pytest.fail("capacity must not run"))
    with pytest.raises(RuntimeError, match="capacity_control_preflight_failed"):
        module.run(args)
    data = json.loads((args.out_dir / "summary.json").read_text())
    assert data["capacity_start_readiness"]["ok"] is False
    assert events == ["bounded-suite", "probes"]


@pytest.mark.parametrize("existing_report", [False, True])
def test_competing_cli_never_publishes_into_locked_output(tmp_path, monkeypatch, existing_report):
    args, events = setup_run(tmp_path, monkeypatch)
    args.out_dir.mkdir()
    if existing_report:
        (args.out_dir / "summary.json").write_text('{"owner": "original"}')
        (args.out_dir / "report.html").write_text("original report")
    lock = module.acquire_output_lock(args.out_dir)
    before = {p.name: p.read_bytes() for p in args.out_dir.iterdir()}
    try:
        code = module.main(["--profiles", str(args.profiles), "--profile", "4U8G",
                            "--out-dir", str(args.out_dir), "--metrics", "M3", "--resume"])
        assert code == 2
        assert events == []
        assert {p.name: p.read_bytes() for p in args.out_dir.iterdir()} == before
    finally:
        lock.close()


def test_existing_results_require_explicit_resume(tmp_path, monkeypatch):
    args, events = setup_run(tmp_path, monkeypatch)
    args.out_dir.mkdir()
    summary = args.out_dir / "summary.json"
    summary.write_text('{"retained": true}')
    code = module.main(["--profiles", str(args.profiles), "--out-dir", str(args.out_dir)])
    assert code == 2
    assert events == []
    assert summary.read_text() == '{"retained": true}'


def test_failure_report_written_while_lock_owned(tmp_path, monkeypatch):
    args, _ = setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_configure", lambda *a, **k: (_ for _ in ()).throw(ValueError("invalid profile")))
    render = module.write_observation_report

    def guarded_render(result, path):
        with pytest.raises(RuntimeError, match="already locked"):
            module.acquire_output_lock(path.parent)
        return render(result, path)

    monkeypatch.setattr(module, "write_observation_report", guarded_render)
    assert module.main(["--profiles", str(args.profiles), "--out-dir", str(args.out_dir)]) == 2
    assert json.loads((args.out_dir / "summary.json").read_text())["status"] == "BLOCKED"
    module.acquire_output_lock(args.out_dir).close()


@pytest.mark.parametrize("fail_capacity", [False, True])
def test_observability_stays_live_through_capacity(tmp_path, monkeypatch, fail_capacity):
    args, _ = setup_run(tmp_path, monkeypatch)
    profiles = json.loads(args.profiles.read_text())
    profiles["profiles"][0]["tenant_observability"] = {"enabled": True}
    args.profiles.write_text(json.dumps(profiles))
    monkeypatch.setenv("ECHOMEM_TEST_CONTROL_TOKEN", "unit-only")
    monkeypatch.setattr(module, "_collect_observation", lambda *a: {"status": "PASS"})
    threads = []

    class Sampler:
        def __init__(self, target, args, **kwargs):
            self.stop = args[0]
            threads.append(self)

        def start(self):
            pass

        def join(self, **kwargs):
            assert self.stop.is_set()

        def is_alive(self):
            return not self.stop.is_set()

    monkeypatch.setattr(module.threading, "Thread", Sampler)

    def capacity(*a):
        assert len(threads) == 1 and not threads[0].stop.is_set()
        if fail_capacity:
            raise RuntimeError("capacity interrupted")
        return []

    monkeypatch.setattr(module, "_run_m1_profiles", capacity)
    if fail_capacity:
        with pytest.raises(RuntimeError, match="capacity interrupted"):
            module.run(args)
    else:
        module.run(args)
    assert threads[0].stop.is_set()
    suite = json.loads((args.out_dir / "suite.json").read_text())
    monitor = suite["tenant_observability_monitor"]
    assert monitor["window_end_s"] >= monitor["window_start_s"]
