from types import SimpleNamespace

from performance.targets.echomem.probes import invalid_input


def test_negative_contract_probe_reports_all_cases(monkeypatch, tmp_path):
    monkeypatch.setattr(invalid_input, "load_tenant_specs", lambda *a, **k: [
        SimpleNamespace(auth_key="secret", tenant_id="tenant-a", agent_id="agent-a")
    ])
    def call(*args, **kwargs):
        return {"http_status": 401 if kwargs["headers"].get("X-Auth-Key") in (None, "invalid-stress-key") else 400,
                "elapsed_s": 0.01}
    monkeypatch.setattr(invalid_input, "_call", call)
    checks = []
    ctx = SimpleNamespace(
        params={"tenant_config": str(tmp_path / "tenants.json")},
        base_url="http://127.0.0.1:1",
        check=lambda name, **values: checks.append({"name": name, **values}),
    )
    invalid_input.run(ctx)
    assert checks[0]["status"] == "PASS"
    assert "15/15" in checks[0]["reason"]
