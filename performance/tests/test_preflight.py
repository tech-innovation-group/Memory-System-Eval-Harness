"""Preflight gate: config parsing, digest, env checks, endpoint probes.

Uses a test-local inline ``http.server`` to simulate the OpenAI-shaped
``/chat/completions`` and ``/embeddings`` endpoints; ``conftest.py`` is
intentionally not touched.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import urllib.parse

import pytest

from performance.targets.echomem.acceptance.preflight import (
    REQUIRED_FIELDS,
    check_env,
    config_digest,
    parse_engine_configs,
    probe_endpoint,
    run_preflight,
)

# -- inline mock of the OpenAI-shaped probe endpoints ---------------------


class _ProbeState:
    def __init__(self):
        self.requests: list[dict] = []
        self.fail_model = False  # respond 404 to simulate an unsupported model


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        state = self.server.probe_state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        path = urllib.parse.urlparse(self.path).path
        state.requests.append({
            "path": path,
            "body": body,
            "auth": self.headers.get("Authorization"),
        })
        if state.fail_model:
            self._send(404, {"error": "model not found"})
            return
        if path in {"/chat/completions", "/embeddings"}:
            self._send(200, {"data": [{"embedding": [0.1, 0.2]}]} if path == "/embeddings"
                       else {"choices": [{"message": {"content": "pong"}}]})
        else:
            self._send(404, {"error": "not found"})

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ProbeServer(http.server.ThreadingHTTPServer):
    def __init__(self, state: _ProbeState):
        super().__init__(("127.0.0.1", 0), _ProbeHandler)
        self.probe_state = state


@pytest.fixture
def probe_server():
    servers: list[_ProbeServer] = []

    def _start(state: _ProbeState | None = None):
        state = state or _ProbeState()
        httpd = _ProbeServer(state)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append(httpd)
        return httpd, state, f"http://127.0.0.1:{httpd.server_port}"

    yield _start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


# -- parse_engine_configs --------------------------------------------------


def test_parse_flat_engines(tmp_path):
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({
        "engines": [
            {"id": "a", "kind": "llm", "api_key_env": "K1", "api_base": "https://a/", "model": "m1"},
            {"id": "b", "kind": "embedding", "api_key_env": "K2", "api_base": "https://b", "model": "m2"},
        ]
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert [e["id"] for e in engines] == ["a", "b"]
    assert engines[0]["kind"] == "llm"
    assert engines[0]["api_base"] == "https://a"  # trailing slash stripped
    assert engines[1]["kind"] == "embedding"


def test_parse_bare_list(tmp_path):
    path = tmp_path / "engines.json"
    path.write_text(json.dumps([
        {"id": "a", "kind": "llm", "api_base": "https://a", "model": "m1"},
        {"api_base": "https://x", "model": "m2"},  # no id -> engine-1
    ]), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert [e["id"] for e in engines] == ["a", "engine-1"]


def test_parse_nested_native_config(tmp_path):
    path = tmp_path / "native.json"
    path.write_text(json.dumps({
        "model": {
            "provider": {"api_key_env": "K", "api_base": "https://llm", "model": "m1"},
            "engine": {"configs": {
                "embedding": {"enabled": True, "api_base": "https://emb", "model": "emb-1"},
                "rerank": {"enabled": False, "api_base": "https://rerank", "model": "r1"},
            }},
        },
        "recall": {"model": {"api_base": "https://llm", "model": "m1"}},
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    ids = {e["id"] for e in engines}
    # disabled rerank branch must not appear; providers discovered recursively
    assert ids == {"model.provider", "model.engine.configs.embedding", "recall.model"}
    by_id = {e["id"]: e for e in engines}
    assert by_id["model.engine.configs.embedding"]["kind"] == "embedding"
    assert by_id["model.provider"]["api_base"] == "https://llm"


def test_parse_skips_empty_api_base_and_model(tmp_path):
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({
        "engines": [
            {"id": "no-base", "kind": "llm", "api_base": "", "model": "m1"},
            {"id": "no-model", "kind": "llm", "api_base": "https://x", "model": "  "},
            {"id": "on", "kind": "llm", "api_base": "https://on", "model": "m2"},
        ]
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert [e["id"] for e in engines] == ["on"]


def test_parse_intent_llm_skipped_when_intent_backend_not_llm(tmp_path):
    # An intent LLM provider is inactive when search.intent.backend is a
    # non-LLM backend (e.g. "rule"); preflighting it would stop a valid run.
    path = tmp_path / "native.json"
    path.write_text(json.dumps({
        "recall": {
            "search": {"intent": {"backend": "rule"}},
            "model": {
                "intent_llm": {"api_key_env": "K", "api_base": "https://intent", "model": "i1"},
                "llm": {"api_key_env": "K", "api_base": "https://llm", "model": "m1"},
            },
        },
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    ids = [e["id"] for e in engines]
    assert "recall.model.intent_llm" not in ids
    assert ids == ["recall.model.llm"]


def test_parse_intent_llm_kept_when_intent_backend_llm(tmp_path):
    path = tmp_path / "native.json"
    path.write_text(json.dumps({
        "recall": {
            "search": {"intent": {"backend": "llm"}},
            "model": {
                "intent_llm": {"api_key_env": "K", "api_base": "https://intent", "model": "i1"},
            },
        },
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert [e["id"] for e in engines] == ["recall.model.intent_llm"]


def test_parse_flat_keeps_enabled_false_entry(tmp_path):
    # ``enabled:false`` filtering applies to the native nested layout (see
    # test_parse_nested_native_config); the flat engines format does not
    # treat ``enabled`` as a filter field.
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({
        "engines": [
            {"id": "off", "enabled": False, "api_base": "https://off", "model": "m1"},
        ]
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert [e["id"] for e in engines] == ["off"]


def test_parse_skips_fake_and_mock_providers(tmp_path):
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({
        "engines": [
            {"id": "fake", "provider": "fake", "api_base": "https://fake", "model": "m1"},
            {"id": "mock", "provider": "mock", "api_base": "https://mock", "model": "m2"},
            {"id": "real", "provider": "ark", "api_base": "https://real", "model": "m3"},
        ]
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert [e["id"] for e in engines] == ["real"]


def test_parse_dedupes_identical_entries(tmp_path):
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({
        "engines": [
            {"id": "a", "kind": "llm", "api_key_env": "K", "api_base": "https://a", "model": "m1"},
            {"id": "a", "kind": "llm", "api_key_env": "K", "api_base": "https://a", "model": "m1"},
            {"id": "b", "kind": "llm", "api_key_env": "K", "api_base": "https://a", "model": "m1"},
        ]
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert [e["id"] for e in engines] == ["a", "b"]


def test_parse_kind_inferred_from_id(tmp_path):
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({
        "engines": [
            {"id": "query_embedding", "api_base": "https://a", "model": "m1"},
            {"id": "plain-llm", "api_base": "https://b", "model": "m2"},
        ]
    }), encoding="utf-8")
    engines = parse_engine_configs(path)
    assert engines[0]["kind"] == "embedding"
    assert engines[1]["kind"] == "llm"


def test_parse_no_real_endpoint_raises(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"engines": [{"id": "x", "api_base": "", "model": ""}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        parse_engine_configs(path)


def test_parse_invalid_root_raises(tmp_path):
    path = tmp_path / "scalar.json"
    path.write_text(json.dumps("nope"), encoding="utf-8")
    with pytest.raises(ValueError):
        parse_engine_configs(path)


def test_required_fields_contract():
    assert REQUIRED_FIELDS == ("id", "kind", "api_base", "model")


# -- config_digest ---------------------------------------------------------


def test_config_digest_stable_and_secret_free(monkeypatch):
    engines = [
        {"id": "a", "kind": "llm", "api_key_env": "MY_KEY",
         "api_base": "https://a", "model": "m1"},
    ]
    monkeypatch.setenv("MY_KEY", "secret-value-1")
    first = config_digest(engines)
    monkeypatch.setenv("MY_KEY", "secret-value-2")
    assert config_digest(engines) == first  # secret values never enter the digest
    assert len(first) == 64  # SHA-256 hex


def test_config_digest_sensitive_to_model_and_endpoint():
    base = [{"id": "a", "kind": "llm", "api_key_env": "K",
             "api_base": "https://a", "model": "m1"}]
    other_model = [dict(base[0], model="m2")]
    other_base = [dict(base[0], api_base="https://b")]
    assert config_digest(base) != config_digest(other_model)
    assert config_digest(base) != config_digest(other_base)
    assert config_digest(base) == config_digest([dict(base[0])])


# -- check_env -------------------------------------------------------------


def test_check_env_missing(monkeypatch):
    monkeypatch.delenv("K_MISSING", raising=False)
    engines = [
        {"id": "a", "kind": "llm", "api_key_env": "K_MISSING",
         "api_base": "https://a", "model": "m1"},
    ]
    errors = check_env(engines)
    assert len(errors) == 1
    assert "K_MISSING" in errors[0]


def test_check_env_empty(monkeypatch):
    monkeypatch.setenv("K_EMPTY", "   ")
    errors = check_env([{"id": "a", "api_key_env": "K_EMPTY"}])
    assert errors and "K_EMPTY" in errors[0]


def test_check_env_present_and_unreferenced(monkeypatch):
    monkeypatch.setenv("K_OK", "secret")
    engines = [
        {"id": "a", "kind": "llm", "api_key_env": "K_OK",
         "api_base": "https://a", "model": "m1"},
        {"id": "b", "kind": "llm", "api_key_env": "",
         "api_base": "https://b", "model": "m2"},  # no env referenced
    ]
    assert check_env(engines) == []


# -- probe_endpoint --------------------------------------------------------


def _engine(kind: str, base_url: str, *, env: str = "PROBE_KEY") -> dict:
    return {
        "id": "probe",
        "kind": kind,
        "api_key_env": env,
        "api_base": base_url,
        "model": "probe-model",
    }


def test_probe_endpoint_llm_ok(probe_server, monkeypatch):
    _, state, base_url = probe_server()
    monkeypatch.setenv("PROBE_KEY", "secret-key")
    result = probe_endpoint(_engine("llm", base_url), timeout_s=5.0)
    assert result["status"] == "ok"
    assert result["model_supported"] is True
    assert result["code"] == 200
    assert result["error"] == ""
    assert result["elapsed_s"] >= 0
    assert len(state.requests) == 1
    request = state.requests[0]
    assert request["path"] == "/chat/completions"
    assert request["body"]["model"] == "probe-model"
    assert request["body"]["messages"] == [{"role": "user", "content": "ping"}]
    assert request["body"]["max_tokens"] == 1
    assert request["auth"] == "Bearer secret-key"


def test_probe_endpoint_embedding_ok(probe_server, monkeypatch):
    _, state, base_url = probe_server()
    monkeypatch.setenv("PROBE_KEY", "secret-key")
    result = probe_endpoint(_engine("embedding", base_url), timeout_s=5.0)
    assert result["status"] == "ok"
    assert result["model_supported"] is True
    request = state.requests[0]
    assert request["path"] == "/embeddings"
    assert request["body"]["input"] == "ping"


def test_probe_endpoint_http_error_means_unsupported(probe_server, monkeypatch):
    _, state, base_url = probe_server(_ProbeState())
    state.fail_model = True
    monkeypatch.setenv("PROBE_KEY", "secret-key")
    result = probe_endpoint(_engine("llm", base_url), timeout_s=5.0)
    assert result["status"] == "error"
    assert result["model_supported"] is False
    assert result["code"] == 404
    assert "可能不被该 endpoint 支持" in result["error"]


def test_probe_endpoint_unreachable():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    result = probe_endpoint(_engine("llm", f"http://127.0.0.1:{port}", env=""), timeout_s=0.5)
    assert result["status"] == "error"
    assert result["model_supported"] is False
    assert result["code"] is None
    assert "endpoint 不可达" in result["error"]


# -- run_preflight ---------------------------------------------------------


def _write_config(tmp_path, engines: list[dict]) -> str:
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({"engines": engines}), encoding="utf-8")
    return str(path)


def test_run_preflight_ok(probe_server, tmp_path, monkeypatch):
    _, _, base_url = probe_server()
    monkeypatch.setenv("PROBE_KEY", "secret-key")
    config = _write_config(tmp_path, [_engine("llm", base_url)])
    result = run_preflight(config, timeout_s=5.0)
    assert result["ok"] is True
    assert result["error"] == ""
    assert result["engines_checked"] == 1
    assert result["engines"][0]["status"] == "ok"
    assert len(result["digest"]) == 64


def test_run_preflight_env_failure(probe_server, tmp_path, monkeypatch):
    _, _, base_url = probe_server()
    monkeypatch.delenv("PROBE_KEY", raising=False)
    config = _write_config(tmp_path, [_engine("llm", base_url)])
    result = run_preflight(config, timeout_s=5.0)
    assert result["ok"] is False
    assert "PROBE_KEY" in result["error"]
    assert result["engines_checked"] == 0
    assert result["probe_attempts"] == 0
    assert result["digest"]


def test_run_preflight_probe_failure(probe_server, tmp_path, monkeypatch):
    _, state, base_url = probe_server()
    state.fail_model = True
    monkeypatch.setenv("PROBE_KEY", "secret-key")
    config = _write_config(tmp_path, [_engine("llm", base_url)])
    result = run_preflight(config, timeout_s=5.0)
    assert result["ok"] is False
    assert "可能不被该 endpoint 支持" in result["error"]
    assert result["engines_checked"] == 1
    # deterministic HTTP errors are never retried
    assert result["probe_attempts"] == 1
    assert len(state.requests) == 1


def test_run_preflight_transient_failure_retried_then_ok(tmp_path, monkeypatch):
    from performance.targets.echomem.acceptance import preflight as preflight_mod

    attempts: list[int] = []

    def flaky_probe(engine: dict, *, timeout_s: float = 20.0) -> dict:
        attempts.append(1)
        if len(attempts) < 3:
            return {
                "id": engine["id"],
                "kind": engine["kind"],
                "api_base": engine["api_base"],
                "model": engine["model"],
                "model_supported": False,
                "status": "error",
                "code": None,
                "elapsed_s": 0.0,
                "error": "urlopen error [Errno -2] Name or service not known",
            }
        return {
            "id": engine["id"],
            "kind": engine["kind"],
            "api_base": engine["api_base"],
            "model": engine["model"],
            "model_supported": True,
            "status": "ok",
            "code": 200,
            "elapsed_s": 0.0,
            "error": "",
        }

    monkeypatch.setattr(preflight_mod, "probe_endpoint", flaky_probe)
    monkeypatch.setenv("PROBE_KEY", "secret-key")
    config = _write_config(tmp_path, [_engine("llm", "https://x")])
    result = run_preflight(config, timeout_s=5.0, retry_backoff_s=0.0)
    assert result["ok"] is True
    assert result["probe_attempts"] == 3
    assert len(attempts) == 3


def test_run_preflight_config_read_failure(tmp_path):
    result = run_preflight(tmp_path / "missing.json", timeout_s=5.0)
    assert result["ok"] is False
    assert "配置读取失败" in result["error"]
    assert result["engines_checked"] == 0
    assert result["engines"] == []
    assert result["digest"] == ""
