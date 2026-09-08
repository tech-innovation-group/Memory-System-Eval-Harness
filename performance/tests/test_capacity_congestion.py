from performance.targets.echomem.acceptance.capacity_statistics import detect_congestion


def window(at, rejected, **overrides):
    return [{"op": "read", "sent": True, "start_s": at + i / 100,
             "http_status": 429 if i < rejected else 200, **overrides} for i in range(20)]


def test_sustained_rejections_stop_without_waiting_for_crash():
    result = detect_congestion({"rows": window(0, 2) + window(10, 2)})
    assert result["observed"]
    assert result["windows"][0]["pressure_errors"] == 2


def test_quality_failure_is_not_congestion():
    assert not detect_congestion({"rows": window(0, 0, success=False) + window(10, 0, success=False)})["observed"]


def test_isolated_or_nonconsecutive_spikes_do_not_stop():
    assert not detect_congestion({"rows": window(0, 5)})["observed"]
    assert not detect_congestion({"rows": window(0, 5) + window(20, 5)})["observed"]


def test_authentication_and_generator_failures_are_not_capacity():
    for extra in ({"http_status": 401}, {"sent": False}):
        assert not detect_congestion({"rows": window(0, 20, **extra) + window(10, 20, **extra)})["observed"]


def test_repeated_timeouts_count_as_pressure():
    assert detect_congestion({"rows": window(0, 0, timeout_censored=True) + window(10, 0, timeout_censored=True)})["observed"]


def test_exploration_stops_at_congestion_even_when_it_recovers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from performance.targets.echomem.acceptance import capacity_experiment as module
    actors = [SimpleNamespace(tenant_index=i, user_index=0, client=SimpleNamespace(
        base_url="unit", auth_key="unit", tenant_id=f"t{i}", user_id=f"u{i}",
        account_id=f"t{i}", agent_id="unit"), corpus={}, write_session="s") for i in range(4)]
    monkeypatch.setattr(module, "provision_actors", lambda *a, **k: actors)
    monkeypatch.setattr(module, "prepare_actors", lambda *a, **k: {"status": "PASS", "actors": []})
    monkeypatch.setattr(module, "observe_recovery", lambda *a, **k: {"status": "RECOVERED", "reason": "drained"})
    def measure(selected, **kwargs):
        rows = window(0, 2) + window(10, 2)
        return {"rows": [{**r, "identity_index": 0, "query_type": "recall", "success": r["http_status"] == 200,
                           "elapsed_s": 1} for r in rows], "mixed": False,
                "duration_s": 20, "identity_count": len(selected), "tenant_count": len(selected)}
    monkeypatch.setattr(module, "measure", measure)
    result = module.run_exploration(base_url="unit", output=tmp_path / "result",
        topology="cross-tenant", levels=[1, 2, 4], warmup_s=1, duration_s=20)
    assert len(result["levels"]) == 1
    assert result["boundary"]["status"] == "CONGESTION_OBSERVED"
    assert result["operational_boundary"]["recovered_after_load"] == "RECOVERED"
    assert result["max_hot_users"] is None
