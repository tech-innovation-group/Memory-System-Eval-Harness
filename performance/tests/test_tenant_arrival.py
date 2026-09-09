"""Scheduling unit tests, not performance or service acceptance evidence."""

import threading

import pytest

from performance.engine import Engine, RateGate, SceneError, SceneModule
from performance.profile import ArrivalSpec, ProfileError, load_profile


def profile(**arrival):
    return load_profile({
        "name": "tenant-arrival",
        "target": {"base_url": "http://unused.invalid"},
        "tenants": [{"name": "a"}, {"name": "b"}],
        "load": {"workers": 2, "duration_s": 0.01,
                 "arrival": {"read": {"model": "fixed_rps", "rps": 1, **arrival}}},
    })


def scene():
    return SceneModule("test", "", {"read": lambda ctx: None}, None)


def test_defaults_preserve_global_rate():
    p = profile()
    assert p.load.arrival["read"] == ArrivalSpec("fixed_rps", 1)
    assert set(Engine(p, scene())._build_gates()) == {("read", None)}


def test_fast_tenant_cannot_claim_slow_tenant_slots(monkeypatch):
    monkeypatch.setattr("performance.engine.time.perf_counter", lambda: 1000)
    gates = Engine(profile(scope="per_tenant"), scene())._build_gates()
    fast, slow = gates[("read", 0)], gates[("read", 1)]
    for _ in range(10):
        fast.wait(0, threading.Event())
    assert fast._claimed == 10
    assert slow._claimed == 0
    slow.wait(0, threading.Event())
    assert slow._claimed == 1
    assert fast._claimed == 10


@pytest.mark.parametrize("model", ["fixed_rps", "poisson"])
def test_start_delay_is_interruptible(model, monkeypatch):
    monkeypatch.setattr("performance.engine.time.perf_counter", lambda: 100)
    waits = []

    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return True

    RateGate(ArrivalSpec(model, 1, start_s=30)).wait(100, Stop())
    assert waits == [30]


def test_end_boundary_does_not_start_new_arrivals(monkeypatch):
    monkeypatch.setattr("performance.engine.time.perf_counter", lambda: 100)
    gate = RateGate(ArrivalSpec("fixed_rps", 1, end_s=1))
    assert gate.wait(100, threading.Event()) == (0, 0)
    assert gate.wait(100, threading.Event()) is None


def test_late_worker_cannot_catch_up_after_end(monkeypatch):
    monkeypatch.setattr("performance.engine.time.perf_counter", lambda: 102)
    gate = RateGate(ArrivalSpec("fixed_rps", 1, end_s=1))
    assert gate.wait(100, threading.Event()) is None


def test_insufficient_tenant_workers_fail_before_start():
    p = profile(scope="per_tenant")
    p.load.workers = 1
    with pytest.raises(SceneError, match="one worker per tenant"):
        Engine(p, scene()).run()


def test_engine_assigns_distinct_gates_to_tenants(monkeypatch):
    p = profile(scope="per_tenant")
    p.load.workers = 4
    assigned = []
    engine = Engine(p, scene())
    monkeypatch.setattr(engine, "_worker_loop", lambda ctx, task, gate:
                        assigned.append((ctx.tenant_idx, gate)))
    engine.run()
    by_tenant = {index: {gate for tenant, gate in assigned if tenant == index}
                 for index in (0, 1)}
    assert len(assigned) == 4
    assert len(by_tenant[0]) == len(by_tenant[1]) == 1
    assert by_tenant[0].isdisjoint(by_tenant[1])


def test_per_tenant_weights_change_effective_rates():
    p = profile(scope="per_tenant", tenant_weights=[4, 1])
    gates = Engine(p, scene())._build_gates()
    assert gates[("read", 0)].arrival.rps == 4
    assert gates[("read", 1)].arrival.rps == 1


def test_per_tenant_weights_must_match_tenant_count():
    with pytest.raises(SceneError, match="weights"):
        Engine(profile(scope="per_tenant", tenant_weights=[1]), scene())._build_gates()


def test_engine_global_gate_shared_by_tenants(monkeypatch):
    engine = Engine(profile(), scene())
    gates = []
    monkeypatch.setattr(engine, "_worker_loop", lambda ctx, task, gate: gates.append(gate))
    engine.run()
    assert len(gates) == 2
    assert gates[0] is gates[1]


@pytest.mark.parametrize("options", [
    {"scope": "typo"}, {"start_s": -1},
    {"model": "none", "scope": "per_tenant"},
    {"model": "none", "start_s": 1},
])
def test_invalid_arrival_rejected(options):
    with pytest.raises(ProfileError):
        profile(**options)
