from __future__ import annotations

from pathlib import Path

from memory.services.runtime_status import RuntimeStatusContext, backend_runtime_status


class FakePluginService:
    def __init__(self, probe_result):
        self.probe_result = probe_result
        self.calls = []

    def probe(self, backend: str, host: str, port: str, api_key: str = ""):
        self.calls.append((backend, host, port, api_key))
        return dict(self.probe_result)


def make_context(tmp_path: Path, plugin_service) -> RuntimeStatusContext:
    return RuntimeStatusContext(
        repo_root=tmp_path,
        first_existing_path=lambda candidates, fallback: fallback,
        resolve_openviking_embedding_config=lambda: {},
        resolve_openviking_vlm_config=lambda: {},
        plugin_service=plugin_service,
    )


def test_openviking_runtime_status_uses_profile_label_and_url(tmp_path: Path) -> None:
    plugin_service = FakePluginService({"ok": True})
    context = make_context(tmp_path, plugin_service)

    result = backend_runtime_status(
        "openviking",
        {"ovHost": "127.0.0.1", "ovPort": "19123", "root_api_key": "secret"},
        {"server_host": "127.0.0.1", "server_port": "19080"},
        context=context,
    )

    assert result["status"] == "ok"
    assert result["label"] == "OpenViking 服务"
    assert result["url"] == "http://127.0.0.1:19123"
    assert plugin_service.calls == [("openviking", "127.0.0.1", "19123", "secret")]


def test_openviking_runtime_status_warns_when_probe_fails(tmp_path: Path) -> None:
    plugin_service = FakePluginService({"ok": False, "error": "connection refused"})
    context = make_context(tmp_path, plugin_service)

    result = backend_runtime_status(
        "openviking",
        {},
        {"server_host": "127.0.0.1", "server_port": "19080"},
        context=context,
    )

    assert result["status"] == "warn"
    assert result["label"] == "OpenViking 服务"
    assert result["probe"]["error"] == "connection refused"


def test_echomemory_runtime_status_uses_profile_label_when_root_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHOMEM_ROOT", raising=False)
    monkeypatch.delenv("ECHOMEMORY_ROOT", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ECHOMEM_API_KEY", raising=False)
    monkeypatch.delenv("ECHOMEM_CHAT_API_KEY", raising=False)

    plugin_service = FakePluginService({"ok": False})
    context = make_context(tmp_path, plugin_service)

    result = backend_runtime_status(
        "echomemory",
        {},
        {},
        context=context,
    )

    assert result["label"] == "EchoMemory 本地 SDK"
    assert result["kind"] == "local-sdk"
    assert result["status"] in {"fail", "warn"}
    assert "EchoMemory SDK" in result["message"] or "ECHOMEM_ROOT" in result["message"]


def test_echomemory_runtime_status_reports_ok_with_detected_sdk_and_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "echo_memory"
    (root / "packages" / "echomem" / "src").mkdir(parents=True)
    (root / "packages" / "echofs" / "src").mkdir(parents=True)
    monkeypatch.setenv("ECHOMEM_ROOT", str(root))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "embed-token")
    monkeypatch.setenv("ECHOMEM_CHAT_API_KEY", "chat-token")

    plugin_service = FakePluginService({"ok": False})
    context = make_context(tmp_path, plugin_service)

    result = backend_runtime_status(
        "echomemory",
        {},
        {},
        context=context,
    )

    assert result["label"] == "EchoMemory 本地 SDK"
    assert result["root"] == str(root)
    assert result["sdk_layout"] is True
    assert result["embedding_token_set"] is True
    assert result["chat_token_set"] is True
