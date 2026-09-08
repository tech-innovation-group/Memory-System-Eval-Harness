"""Probe orchestration (probes.py) unit tests.

Runs the probe subprocesses against the in-process mock EchoMem server;
probe scenes live under ``performance/targets/echomem/probes``.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

from performance.targets.echomem.orchestrator.probes import (
    _resolve_auth_key,
    _resolve_tenant_id,
    run_configured_probes,
)
from performance.targets.echomem.probes.fault_isolation import control


def _tenant_config(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tenants(tmp_path) -> Path:
    path = tmp_path / "tenants.json"
    path.write_text(
        json.dumps({"tenants": [{"tenant_id": "t1", "auth_key": "k1"}]}),
        encoding="utf-8",
    )
    return path


def _probe_scenes(commands) -> list[str]:
    """commands 里实际运行过的探针场景文件名。"""
    scenes = []
    for entry in commands:
        argv = entry.get("command")
        if not isinstance(argv, list) or "--scene" not in argv:
            continue
        scenes.append(Path(argv[argv.index("--scene") + 1]).name)
    return scenes


# -- 凭据/租户解析（dict 形态） ----------------------------------------


def test_resolve_auth_key_from_dict():
    config = {"tenants": [{"tenant_id": "t1", "auth_key": "k1"}]}
    assert _resolve_auth_key(config, "") == ("k1", "")
    assert _resolve_auth_key(config, "0") == ("k1", "")
    assert _resolve_auth_key(config, "t1") == ("k1", "")
    assert _resolve_auth_key(config, "9") == ("", "")
    assert _resolve_auth_key(config, "t2") == ("", "")
    assert _resolve_auth_key({}, "0") == ("", "")


def test_resolve_auth_key_env_fallback():
    config = {
        "tenants": [{"tenant_id": "t1", "auth_key_env": "TEST_ECHOMEM_AUTH_KEY"}]
    }
    assert _resolve_auth_key(config, "")[1] == "TEST_ECHOMEM_AUTH_KEY"


def test_resolve_tenant_id_from_dict():
    config = {"tenants": [{"tenant_id": "t1"}, {"id": "t2"}]}
    assert _resolve_tenant_id(config, "t2") == "t2"
    assert _resolve_tenant_id(config, "missing") == "t1"
    assert _resolve_tenant_id({}, "x") == "x"


# -- capability 探针 -----------------------------------------------------


def test_capability_probe_runs_against_mock(server, tmp_path):
    _, _, base_url = server
    tenants_path = _tenants(tmp_path)
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {
        "name": "p",
        "base_url": base_url,
        "tenant_config": str(tenants_path),
        "capability_probe": {"health_path": "/health", "metrics_path": "/metrics"},
    }
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=_tenant_config(tenants_path),
        quick=False,
    )
    payload = artifacts["capability_probe"]
    assert payload["path"] == str(suite_dir / "capability-probe.json")
    assert (suite_dir / "capability-probe.json").is_file()
    assert payload["probe"] == "capability"
    assert payload["status"] == "INCONCLUSIVE"  # /metrics NOT_IMPLEMENTED + 未配置路径
    runs = [c for c in commands if isinstance(c.get("command"), list)]
    assert len(runs) == 1
    argv = runs[0]["command"]
    assert Path(argv[argv.index("--scene") + 1]).name == "capability.py"
    # INCONCLUSIVE 由 payload 保留，不被子进程失败掩盖
    assert runs[0]["status"] == "INCONCLUSIVE"
    # blackbox 缺 commit 证据 + sweep 未配置 → 两条 INCONCLUSIVE 标记
    markers = [c for c in commands if c.get("status") == "INCONCLUSIVE" and "command" not in c]
    assert len(markers) == 2


def test_capability_probe_cursor_uri_template(server, tmp_path):
    """cursor_uri_template 经 /fs/read 探测持久 cursor（mock 404 → NOT_IMPLEMENTED）。"""
    _, _, base_url = server
    tenants_path = _tenants(tmp_path)
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {
        "name": "p",
        "base_url": base_url,
        "tenant_config": str(tenants_path),
        "capability_probe": {
            "health_path": "/health",
            "metrics_path": "/metrics",
            "session_id": "s1",
            "cursor_uri_template": "echo://sessions/{session}/current/commit_cursor.json",
        },
    }
    artifacts, _ = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=_tenant_config(tenants_path),
        quick=False,
    )
    payload = artifacts["capability_probe"]
    cursor_checks = [
        check for check in payload["checks"] if check["name"] == "cursor/message-set"
    ]
    assert cursor_checks, "cursor/message-set check should exist"
    check = cursor_checks[0]
    assert check["status"] == "NOT_IMPLEMENTED"  # mock 无 /fs/read → 404
    assert check["reason"] == "endpoint returned HTTP 404"
    assert "echo://sessions/s1/current/commit_cursor.json" in check["detail"]
    assert '"document_keys"' in check["detail"]


def test_unconfigured_probes_not_run(server, tmp_path):
    _, _, base_url = server
    tenants_path = _tenants(tmp_path)
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {"name": "p", "base_url": base_url, "tenant_config": str(tenants_path)}
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=_tenant_config(tenants_path),
        quick=False,
    )
    assert artifacts == {}
    assert _probe_scenes(commands) == []
    for key in (
        "capability_probe", "blackbox_contract_probe", "missing_cases",
        "concurrent_commit", "fault_isolation", "limit_failure_sweep",
        "commit_recovery", "fault_suite",
    ):
        assert key not in artifacts
    # blackbox 无 commit 证据 + limit_failure_sweep 未配置 → INCONCLUSIVE 标记
    assert [c["status"] for c in commands] == ["INCONCLUSIVE", "INCONCLUSIVE"]


def test_missing_precondition_inconclusive(server, tmp_path):
    _, _, base_url = server
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {"name": "p", "base_url": base_url}
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config={},
        quick=False,
    )
    # 无 tenant 配置 + 无已完成 Commit → blackbox 记 INCONCLUSIVE，无制品
    assert "blackbox_contract_probe" not in artifacts
    assert any(
        c.get("status") == "INCONCLUSIVE" and "commit" in c.get("reason", "").lower()
        for c in commands
    )


# -- fault_plan ${BASE_URL} 替换 -----------------------------------------


def test_fault_plan_base_url_replacement(server, tmp_path):
    _, _, base_url = server
    tenants_path = _tenants(tmp_path)
    plan_path = tmp_path / "fault-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "faults": [
                    {
                        "kind": "llm-500",
                        "endpoint": "${BASE_URL}/fault/llm-500",
                        "action": "enable",
                        "timeout_s": 1,
                    }
                ],
                "recovery": {
                    "health_url": "${BASE_URL}/health",
                    "container": "c",
                    "wait_s": 0,
                    "poll_s": 1,
                },
                "cursor": {
                    "uri_template": "echo://sessions/{session}/current/commit_cursor.json"
                },
            }
        ),
        encoding="utf-8",
    )
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    profile = {
        "name": "p",
        "base_url": base_url,
        "tenant_config": str(tenants_path),
        "fault_plan": str(plan_path),
    }
    artifacts, commands = run_configured_probes(
        profile,
        base_url=base_url,
        suite_dir=suite_dir,
        auth_headers={},
        tenant_config=_tenant_config(tenants_path),
        quick=True,
    )
    resolved = suite_dir / "fault-plan.resolved.json"
    assert resolved.is_file()
    text = resolved.read_text(encoding="utf-8")
    assert "${BASE_URL}" not in text
    data = json.loads(text)
    assert data["faults"][0]["endpoint"] == f"{base_url}/fault/llm-500"
    assert data["recovery"]["health_url"] == f"{base_url}/health"
    # fault_suite 探针已运行并产出制品
    assert "fault_suite" in artifacts
    assert artifacts["fault_suite"]["path"] == str(
        suite_dir / "fault-suite" / "fault-suite.json"
    )
    assert _probe_scenes(commands) == ["fault_suite.py"]


# -- fault_isolation 故障控制 --------------------------------------------


def test_fault_isolation_control_command_injects_target_tenant(tmp_path):
    out = tmp_path / "ctrl.txt"
    result = control(
        {"endpoint": "", "command": f"echo {{action}} {{target_tenant}} {{tenant}} > {out}"},
        action="enable",
        target_tenant="t1",
        timeout_s=10,
    )
    assert result["status"] == "PASS"
    assert result["backend"] == "command"
    assert out.read_text(encoding="utf-8").strip() == "enable t1 t1"


def test_fault_isolation_control_http_injects_target_tenant(tmp_path):
    captured: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            captured["token"] = self.headers.get("X-EchoMem-Test-Token")
            length = int(self.headers.get("Content-Length") or 0)
            captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{httpd.server_port}/fault"
        result = control(
            {"endpoint": endpoint, "command": ""},
            action="disable",
            target_tenant="t1",
            timeout_s=10,
            token="test-only-token",
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert result["status"] == "PASS"
    assert result["status_code"] == 200
    assert captured["token"] == "test-only-token"
    assert captured["body"] == {
        "action": "disable",
        "target_tenant": "t1",
        "tenant": "t1",
        "tenant_id": "t1",
        "fault_type": "reject",
        "duration_s": 300,
        "delay_ms": 1000,
    }


def test_m6_behavior_only_runs_one_real_fault_case(monkeypatch, tmp_path):
    from performance.targets.echomem.orchestrator import probes as module

    tenants = {
        "tenants": [
            {"tenant_id": f"t{index}", "auth_key": f"k{index}"}
            for index in range(1, 5)
        ]
    }
    tenant_path = tmp_path / "tenants.json"
    tenant_path.write_text(json.dumps(tenants), encoding="utf-8")
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    calls = []

    def fake_probe(params, **kwargs):
        calls.append({"params": params, "scene": kwargs["scene"]})
        return {"checks": [{"name": "fault-isolation"}]}, {"status": "PASS"}

    monkeypatch.setattr(module, "run_configured_probe", fake_probe)
    monkeypatch.setenv("ECHOMEM_TEST_CONTROL_TOKEN", "test-only-token")
    profile = {
        "tenant_config": str(tenant_path),
        "six_metrics_observation": True,
        "fairness_expectations": {"tenant_ids": ["t1", "t2", "t3", "t4"]},
        "fault_isolation": {
            "enabled": True,
            "behavior_case_only": True,
            "queries": ["where is the marker"],
            "token_env": "ECHOMEM_TEST_CONTROL_TOKEN",
        },
    }
    artifacts, _ = run_configured_probes(
        profile, base_url="http://test.invalid", suite_dir=suite_dir,
        auth_headers={}, tenant_config=tenants, quick=False,
    )
    fault_calls = [call for call in calls if call["scene"] == "fault_isolation.py"]
    assert len(fault_calls) == 1
    assert fault_calls[0]["params"]["target_tenant"] == "t1"
    assert fault_calls[0]["params"]["fault_type"] == "reject"
    assert artifacts["fault_isolation"]["expected_cases"] == 1
