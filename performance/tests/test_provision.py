from __future__ import annotations

import json
import stat

from performance.targets.echomem import provision


class _FakeClient:
    counter = 0

    def __init__(self, *_args, **_kwargs):
        pass

    def provision_isolated_identity(self, _label):
        type(self).counter += 1
        index = type(self).counter
        return {
            "tenant_id": f"tenant-{index}",
            "user_id": f"user-{index}",
            "account_id": f"tenant-{index}",
            "agent_id": "default",
            "auth_key": f"secret-{index}",
        }

    def open_session(self, *_args, **_kwargs):
        return {"session_id": "verified"}


def test_provision_can_keep_credentials_out_of_tenant_json(tmp_path, monkeypatch):
    _FakeClient.counter = 0
    monkeypatch.setattr(provision, "EchoMemHTTP", _FakeClient)
    tenants = tmp_path / "tenants.json"
    env_file = tmp_path / "test.env"

    assert provision.main([
        "--base-url", "http://example.test", "--count", "2",
        "--out", str(tenants), "--env-file", str(env_file),
    ]) == 0

    payload = json.loads(tenants.read_text(encoding="utf-8"))
    assert [row["auth_key_env"] for row in payload["tenants"]] == [
        "ECHOMEM_TENANT_1_KEY", "ECHOMEM_TENANT_2_KEY",
    ]
    assert "secret-" not in tenants.read_text(encoding="utf-8")
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "ECHOMEM_TENANT_1_KEY=secret-1", "ECHOMEM_TENANT_2_KEY=secret-2",
    ]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_provision_keeps_legacy_inline_mode_without_env_file(tmp_path, monkeypatch):
    _FakeClient.counter = 0
    monkeypatch.setattr(provision, "EchoMemHTTP", _FakeClient)
    tenants = tmp_path / "tenants.json"

    assert provision.main([
        "--base-url", "http://example.test", "--count", "1", "--out", str(tenants),
    ]) == 0

    assert json.loads(tenants.read_text(encoding="utf-8"))["tenants"][0]["auth_key"] == "secret-1"
