"""Formal scenes (barrier / burst-waves / capacity) and the N×N probe.

These verify the formal scene semantics on the generic engine:
per-tenant distribution, wave structure and op topology, plus the N×N
isolation probe's four-state contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from performance.engine import Engine, load_scene
from performance.probe import load_probe, ProbeRunner, summarize_probe
from performance.profile import Profile, load_profile
from performance.targets.echomem._barrier import barrier_tenant_counts
from performance.tests.conftest import MockState

SCENES_DIR = Path(__file__).resolve().parent.parent / "targets" / "echomem" / "scenes"
PROBES_DIR = Path(__file__).resolve().parent.parent / "targets" / "echomem" / "probes"


def _profile(
    base_url: str,
    *,
    workers: int,
    duration_s: float,
    tenant_count: int = 1,
    **params,
) -> Profile:
    return load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": workers, "duration_s": duration_s},
            "tenants": [{"name": f"t{i}"} for i in range(tenant_count)],
            "params": {"queries": ["q1", "q2"], "messages_per_session": 2, **params},
        }
    )


# -- barrier distribution helper ----------------------------------------


def test_barrier_uniform_remainder():
    assert barrier_tenant_counts(5, 2, distribution="uniform") == {0: 3, 1: 2}
    assert barrier_tenant_counts(4, 4, distribution="uniform") == {0: 1, 1: 1, 2: 1, 3: 1}


def test_barrier_zipf_sums_and_skews():
    counts = barrier_tenant_counts(260, 4, distribution="zipf", zipf_exponent=2.0)
    assert sum(counts.values()) == 260
    # rank 1 dominates a 1/rank^2 distribution
    assert counts[0] > counts[1] > counts[2] > counts[3]


def test_barrier_explicit():
    counts = barrier_tenant_counts(260, 4, distribution="explicit",
                                   explicit=[200, 20, 20, 20])
    assert counts == {0: 200, 1: 20, 2: 20, 3: 20}


def test_barrier_explicit_mismatch():
    with pytest.raises(ValueError, match="explicit"):
        barrier_tenant_counts(260, 4, distribution="explicit", explicit=[200, 20])


# -- scene contracts -----------------------------------------------------


def test_formal_scene_contracts():
    barrier = load_scene(SCENES_DIR / "scene_barrier.py")
    waves = load_scene(SCENES_DIR / "scene_burst_waves.py")
    capacity = load_scene(SCENES_DIR / "scene_capacity.py")
    assert list(barrier.tasks) == ["read"]
    assert barrier.schedule is not None
    assert list(waves.tasks) == ["read"]
    assert waves.schedule is not None
    assert sorted(capacity.tasks) == ["read", "write"]
    assert capacity.schedule is None


# -- scene: commit barrier ----------------------------------------------


def test_scene_barrier_uniform_per_tenant(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_barrier.py")
    profile = _profile(base_url, workers=2, duration_s=2.0, tenant_count=2,
                       barrier_count=6, barrier_max_workers=4)
    result = Engine(profile, scene).run()
    reads = [r for r in result.records if r.op == "read"]
    assert reads
    barrier_submits = [r for r in result.records
                       if r.extra == "barrier" and r.op == "commit_submit"]
    assert len(barrier_submits) == 6
    assert all(r.worker_id == -1 for r in barrier_submits)
    per_tenant = {}
    for rec in barrier_submits:
        per_tenant[rec.tenant_idx] = per_tenant.get(rec.tenant_idx, 0) + 1
    assert per_tenant == {0: 3, 1: 3}
    # every barrier transaction reaches commit_done
    done = [r for r in result.records
            if r.extra == "barrier" and r.op == "commit_done"]
    assert len(done) == 6
    assert all(r.status == "ok" for r in done)


def test_scene_barrier_explicit_distribution(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_barrier.py")
    profile = _profile(base_url, workers=1, duration_s=2.5, tenant_count=3,
                       barrier_count=120, barrier_max_workers=6,
                       barrier_distribution="explicit",
                       commit_tenant_counts=[100, 10, 10])
    result = Engine(profile, scene).run()
    submits = [r for r in result.records
               if r.extra == "barrier" and r.op == "commit_submit"]
    per_tenant = {}
    for rec in submits:
        per_tenant[rec.tenant_idx] = per_tenant.get(rec.tenant_idx, 0) + 1
    assert per_tenant == {0: 100, 1: 10, 2: 10}


def test_scene_barrier_single_writer_retains_bystander_search(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_barrier.py")
    profile = _profile(base_url, workers=4, duration_s=2.0, tenant_count=4,
                       barrier_count=8, barrier_max_workers=4,
                       barrier_distribution="explicit", commit_tenant_counts=[8, 0, 0, 0])
    result = Engine(profile, scene).run()
    submits = [r for r in result.records if r.extra == "barrier" and r.op == "commit_submit"]
    assert len(submits) == 8
    assert {r.tenant_idx for r in submits} == {0}
    assert {r.tenant_idx for r in result.records if r.op == "read"} == {0, 1, 2, 3}


def test_explicit_barrier_rejects_negative_counts():
    with pytest.raises(ValueError, match="non-negative"):
        barrier_tenant_counts(8, 4, distribution="explicit", explicit=[9, -1, 0, 0])


@pytest.mark.parametrize("fail_first", [False, True])
def test_prepared_barrier_submits_only_after_all_adds(server, monkeypatch, fail_first):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_barrier.py")
    if fail_first:
        namespace = scene.schedule.__globals__
        original = namespace["prepare_commit_session"]
        attempts = []
        def prepare(ctx):
            attempts.append(1)
            return None if len(attempts) == 1 else original(ctx)
        monkeypatch.setitem(namespace, "prepare_commit_session", prepare)
    profile = _profile(base_url, workers=4, duration_s=3, tenant_count=4,
                       barrier_count=8, barrier_max_workers=4,
                       barrier_prepare_before_commit=True,
                       barrier_distribution="explicit", commit_tenant_counts=[8, 0, 0, 0])
    result = Engine(profile, scene).run()
    adds = [r for r in result.records if r.op == "add"]
    submits = [r for r in result.records if r.op == "commit_submit"]
    assert len(submits) == (7 if fail_first else 8)
    assert sum(r.op == "commit_preparation_failed" for r in result.records) == int(fail_first)
    assert adds and max(r.ts_ms for r in adds) <= min(r.ts_ms - r.stage_ms for r in submits)
    assert {r.tenant_idx for r in submits} == {0}
    assert {r.tenant_idx for r in result.records if r.op == "read"} == {0, 1, 2, 3}


def test_scene_barrier_floor_to_tenants(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_barrier.py")
    profile = _profile(base_url, workers=1, duration_s=2.0, tenant_count=4,
                       barrier_count=2, barrier_max_workers=4,
                       floor_to_tenants=True)
    result = Engine(profile, scene).run()
    submits = [r for r in result.records
               if r.extra == "barrier" and r.op == "commit_submit"]
    # floored to tenant count so every tenant gets at least one commit
    assert len(submits) == 4
    assert sorted(r.tenant_idx for r in submits) == [0, 1, 2, 3]


def test_scene_barrier_multi_wave(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_barrier.py")
    profile = _profile(base_url, workers=1, duration_s=3.0, tenant_count=2,
                       barrier_count=2, barrier_max_workers=2,
                       barrier_waves=2, barrier_cooldown_s=0.2)
    result = Engine(profile, scene).run()
    submits = [r for r in result.records
               if r.extra == "barrier" and r.op == "commit_submit"]
    assert len(submits) == 4  # 2 waves x 2 commits


# -- scene: multi-wave burst (D) ----------------------------------------


def test_scene_burst_waves(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_burst_waves.py")
    profile = _profile(base_url, workers=2, duration_s=3.0,
                       burst_commits=2, burst_window_s=0.2,
                       burst_waves=2, burst_cooldown_s=0.2)
    result = Engine(profile, scene).run()
    reads = [r for r in result.records if r.op == "read"]
    assert reads
    burst = [r for r in result.records if r.extra == "burst"]
    submits = [r for r in burst if r.op == "commit_submit"]
    assert len(submits) == 4  # 2 waves x 2 commits
    assert all(r.worker_id == -1 for r in burst)
    # D bursts stay on the first tenant
    assert all(r.tenant_idx == 0 for r in burst)
    ops = {r.op for r in burst}
    assert {"open", "add", "commit_submit", "commit_done"} <= ops


# -- scene: capacity (K) ------------------------------------------------


def test_scene_capacity_mixed(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_capacity.py")
    profile = _profile(base_url, workers=2, duration_s=2.0)
    profile.load.mix = {"read": 1, "write": 1}
    result = Engine(profile, scene).run()
    reads = [r for r in result.records if r.op == "read"]
    writes = [r for r in result.records if r.op == "open"]
    assert reads
    assert writes


# -- N×N isolation probe ------------------------------------------------


def test_nxn_isolation_probe_contract():
    probe = load_probe(PROBES_DIR / "nxn_isolation.py")
    assert callable(probe.run)


def test_nxn_isolation_probe_no_config(server):
    _, _, base_url = server
    probe = load_probe(PROBES_DIR / "nxn_isolation.py")
    profile = load_profile(
        {"name": "p", "target": {"base_url": base_url, "read_timeout_s": 5},
         "params": {}}
    )
    result = ProbeRunner(profile, probe).run()
    summary = summarize_probe(probe, result, profile)
    assert summary["status"] == "INCONCLUSIVE"
    assert summary["checks"][0]["name"] == "nxn_isolation"


def test_nxn_isolation_probe_runs_against_mock(server, tmp_path):
    _, _, base_url = server
    config = tmp_path / "tenants.json"
    config.write_text(
        json.dumps(
            {
                "tenants": [
                    {"tenant_id": "t1", "auth_key": "k1"},
                    {"tenant_id": "t2", "auth_key": "k2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    probe = load_probe(PROBES_DIR / "nxn_isolation.py")
    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "params": {"tenant_config": str(config), "markers_per_tenant": 1},
        }
    )
    result = ProbeRunner(profile, probe).run()
    summary = summarize_probe(probe, result, profile)
    assert summary["status"] in ("PASS", "FAIL", "INCONCLUSIVE", "NOT_IMPLEMENTED")
    names = {check["name"] for check in summary["checks"]}
    assert "nxn_isolation" in names
    # Reader checks are always emitted; writer checks are failure-only
    # (see test_nxn_isolation_probe_writer_failure for that path).
    assert "nxn_reader:0" in names
    assert "nxn_reader:1" in names


def test_nxn_isolation_probe_writer_failure(mock_server, tmp_path):
    """A writer that cannot open a session yields INCONCLUSIVE per-writer checks."""
    _, _, base_url = mock_server(MockState(fail_open=True))
    config = tmp_path / "tenants.json"
    config.write_text(
        json.dumps(
            {
                "tenants": [
                    {"tenant_id": "t1", "auth_key": "k1"},
                    {"tenant_id": "t2", "auth_key": "k2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    probe = load_probe(PROBES_DIR / "nxn_isolation.py")
    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "params": {"tenant_config": str(config), "markers_per_tenant": 1},
        }
    )
    result = ProbeRunner(profile, probe).run()
    summary = summarize_probe(probe, result, profile)
    assert summary["status"] == "INCONCLUSIVE"
    names = {check["name"] for check in summary["checks"]}
    assert "nxn_writer:0" in names
    assert "nxn_writer:1" in names
    assert "nxn_isolation" in names
    writer_checks = {
        check["status"]
        for check in summary["checks"]
        if check["name"].startswith("nxn_writer:")
    }
    assert writer_checks == {"INCONCLUSIVE"}
