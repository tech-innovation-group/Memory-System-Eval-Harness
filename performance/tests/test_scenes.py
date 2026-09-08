"""The four EchoMem scenes load and run against a mock server.

These verify the lossless translation of ``performance/``'s A/B/C/D
scenarios: same task topology, same transaction sequence, same recorded
ops and same burst semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from performance.engine import Engine, load_scene
from performance.profile import Profile, load_profile

SCENES_DIR = Path(__file__).resolve().parent.parent / "targets" / "echomem" / "scenes"


def _profile(base_url: str, *, workers: int, duration_s: float, **params) -> Profile:
    return load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": workers, "duration_s": duration_s},
            "params": {"queries": ["q1", "q2"], **params},
        }
    )


# -- contract -----------------------------------------------------------


def test_scene_contracts():
    a = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    b = load_scene(SCENES_DIR / "scene_b_write_injection.py")
    c = load_scene(SCENES_DIR / "scene_c_mixed.py")
    d = load_scene(SCENES_DIR / "scene_d_burst.py")
    assert list(a.tasks) == ["read"]
    assert list(b.tasks) == ["write"]
    assert sorted(c.tasks) == ["read", "write"]
    assert list(d.tasks) == ["read"]
    assert a.schedule is None
    assert b.schedule is None
    assert c.schedule is None
    assert d.schedule is not None


def test_all_target_scenes_load():
    """Every scene under targets/<system>/scenes must satisfy the contract.

    This is the per-system extension contract: a new target only has to
    drop scenes into its own ``scenes/`` directory and they must load.
    """
    targets_root = Path(__file__).resolve().parent.parent / "targets"
    scenes = [
        path
        for system_dir in targets_root.iterdir()
        if system_dir.is_dir()
        for path in (system_dir / "scenes").glob("*.py")
        if not path.name.startswith("_")
    ]
    assert scenes, "no scenes found under targets/<system>/scenes"
    for path in scenes:
        scene = load_scene(path)
        assert scene.tasks, f"{path}: scene must export at least one task"


# -- scene A: pure read -------------------------------------------------


def test_scene_a_pure_read(server):
    _, state, base_url = server
    scene = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    profile = _profile(base_url, workers=3, duration_s=2.0)
    result = Engine(profile, scene).run()
    reads = [r for r in result.records if r.op == "read"]
    assert reads
    assert all(r.status == "ok" for r in reads)
    assert all(r.query in ("q1", "q2") for r in reads)
    assert state.search_queries


def test_scene_a_anchor_quality_rule(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    profile = _profile(base_url, workers=1, duration_s=1.5,
                       queries=["PERFANCHOR-0-0-0", "plain"])
    result = Engine(profile, scene).run()
    anchor_hits = [r for r in result.records if r.op == "read" and r.query.startswith("PERFANCHOR")]
    assert anchor_hits
    # clean empty search would set quality_ok=False, but mock returns items
    assert all(r.quality_ok for r in anchor_hits)
    assert all(r.real_recall for r in anchor_hits)


def test_scene_a_empty_search_quality_failure(mock_server):
    from performance.tests.conftest import MockState

    httpd, state, base_url = mock_server(MockState(search_empty=True))
    scene = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    profile = _profile(base_url, workers=1, duration_s=1.5,
                       queries=["PERFANCHOR-0-0-0"])
    result = Engine(profile, scene).run()
    anchor_hits = [r for r in result.records if r.op == "read"]
    assert anchor_hits
    assert all(r.quality_ok is False for r in anchor_hits)
    assert all(r.hit_count == 0 for r in anchor_hits)


# -- scene B: pure write ------------------------------------------------


def test_scene_b_write_transaction(server):
    _, state, base_url = server
    scene = load_scene(SCENES_DIR / "scene_b_write_injection.py")
    profile = _profile(base_url, workers=1, duration_s=2.0, messages_per_session=3)
    result = Engine(profile, scene).run()
    ops = [r.op for r in result.records]
    assert "open" in ops
    assert ops.count("add") >= 3
    assert "commit_submit" in ops
    assert "commit_done" in ops
    completed = [r for r in result.records if r.op == "commit_done"]
    assert completed
    assert any(r.commit_terminal_state == "completed" for r in completed)
    assert all(r.status == "ok" or (r.poll_outcome == "stopped" and r.terminal_at_ms is None)
               for r in completed)
    # anchor messages carry the PERFTAIL token and content hashes are recorded
    adds = [r for r in result.records if r.op == "add"]
    assert any(r.content_hash for r in adds)


def test_scene_b_failed_open_stops_transaction(mock_server):
    from performance.tests.conftest import MockState

    httpd, state, base_url = mock_server(MockState(fail_open=True))
    scene = load_scene(SCENES_DIR / "scene_b_write_injection.py")
    profile = _profile(base_url, workers=1, duration_s=1.5, messages_per_session=2)
    result = Engine(profile, scene).run()
    opens = [r for r in result.records if r.op == "open"]
    assert opens
    assert all(r.status == "error" for r in opens)
    assert all(r.error_type == "http_5xx" for r in opens)
    # no add/commit records may follow a failed open
    assert not any(r.op == "add" for r in result.records)


def test_scene_b_commit_poll_failure(mock_server):
    from performance.tests.conftest import MockState

    httpd, state, base_url = mock_server(MockState(poll_fail_after=1))
    scene = load_scene(SCENES_DIR / "scene_b_write_injection.py")
    profile = _profile(base_url, workers=1, duration_s=2.0, messages_per_session=2)
    result = Engine(profile, scene).run()
    done = [r for r in result.records if r.op == "commit_done"]
    assert done
    assert all(r.status == "error" for r in done)
    assert any(r.commit_terminal_state == "failed" for r in done)
    assert all(r.error_type == "commit_failed" or
               (r.error_type == "commit_stopped" and r.terminal_at_ms is None) for r in done)


# -- scene C: mixed -----------------------------------------------------


def test_scene_c_mixed(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_c_mixed.py")
    profile = _profile(base_url, workers=3, duration_s=2.0)
    profile.load.mix = {"read": 2, "write": 1}
    result = Engine(profile, scene).run()
    reads = [r for r in result.records if r.op == "read"]
    writes = [r for r in result.records if r.op == "open"]
    assert reads
    assert writes
    read_workers = {r.worker_id for r in reads}
    write_workers = {r.worker_id for r in writes}
    assert len(read_workers) == 2
    assert len(write_workers) == 1
    assert read_workers.isdisjoint(write_workers)


# -- scene D: burst -----------------------------------------------------


def test_scene_d_burst(server):
    _, state, base_url = server
    scene = load_scene(SCENES_DIR / "scene_d_burst.py")
    profile = _profile(base_url, workers=2, duration_s=2.5,
                       burst_commits=2, burst_window_s=0.2, messages_per_session=2)
    result = Engine(profile, scene).run()
    reads = [r for r in result.records if r.op == "read"]
    assert reads
    burst = [r for r in result.records if r.extra == "burst"]
    assert burst
    assert all(r.worker_id == -1 for r in burst)
    burst_ops = {r.op for r in burst}
    assert {"open", "add", "commit_submit", "commit_done"} <= burst_ops
