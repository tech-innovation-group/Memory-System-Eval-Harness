"""Tests for the acceptance seed module (migrated from performance/prepare.py)."""

from __future__ import annotations

import http.server
import json
import re
import threading
import urllib.parse
from pathlib import Path

import pytest

from performance.targets.echomem.acceptance.seed import (
    TenantPreparer,
    _seed_session_flow,
    load_locomo_seed_batches,
    load_tenant_specs,
    seed_tenant,
    seed_tenant_from_conversations,
)
from performance.targets.echomem.probes._client import (
    EchoMemHTTP,
    TenantSpec,
)
from performance.tests.conftest import MockState

# -- inline servers (provision / flaky-add mocks live here, not conftest) --

def _start_server(handler: type[http.server.BaseHTTPRequestHandler]):
    # Default listen backlog (5) refuses connections when the full suite
    # saturates the GIL; raise it before construction so server_bind's
    # listen() uses it (mirrors conftest).
    http.server.ThreadingHTTPServer.request_queue_size = 128
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _make_flaky_add_handler(counters: dict, fail_first_adds: int = 1):
    """Session endpoints whose first ``fail_first_adds`` add calls return 500."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if re.fullmatch(r"/api/sessions/([^/]+)/commits/([^/]+)", path):
                return self._send(200, {"status": "completed"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/sessions/open":
                counters["opens"] += 1
                return self._send(200, {"session_id": f"s{counters['opens']}"})
            if re.fullmatch(r"/api/sessions/([^/]+)/messages", path):
                counters["adds"] += 1
                if counters["adds"] <= fail_first_adds:
                    counters["rejected"] += 1
                    return self._send(500, {"error": "add boom"})
                return self._send(200, {"message_id": f"m{counters['adds']}"})
            if re.fullmatch(r"/api/sessions/([^/]+)/commit", path):
                return self._send(200, {"archive_id": "a1"})
            return self._send(404, {"error": "not found"})

    return Handler


def _make_provision_handler(counters: dict):
    """Auth + session endpoints for the provision identity mode."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if re.fullmatch(r"/api/sessions/([^/]+)/commits/([^/]+)", path):
                return self._send(200, {"status": "completed"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/auth/tenants":
                counters["tenants"] += 1
                n = counters["tenants"]
                return self._send(200, {
                    "tenant": {"tenant_id": f"pt{n}"},
                    "bootstrap_key": f"bk{n}",
                })
            if re.fullmatch(r"/api/auth/tenants/([^/]+)/users", path):
                counters["users"] += 1
                if self.headers.get("X-EchoMem-Bootstrap-Key"):
                    counters["bootstrap_seen"] += 1
                return self._send(200, {"user": {"user_id": f"pu{counters['users']}"}})
            if re.fullmatch(r"/api/auth/tenants/([^/]+)/users/([^/]+)/key", path):
                counters["keys"] += 1
                if self.headers.get("X-EchoMem-Bootstrap-Key"):
                    counters["bootstrap_seen"] += 1
                return self._send(200, {"auth_key": f"pk{counters['keys']}"})
            if path == "/api/auth/account/delete":
                counters["deletes"] += 1
                return self._send(200, {"status": "deleted"})
            if path == "/api/sessions/open":
                return self._send(200, {"session_id": f"s{counters['tenants']}"})
            if re.fullmatch(r"/api/sessions/([^/]+)/messages", path):
                return self._send(200, {"message_id": "m1"})
            if re.fullmatch(r"/api/sessions/([^/]+)/commit", path):
                return self._send(200, {"archive_id": "a1"})
            return self._send(404, {"error": "not found"})

    return Handler


def _write_json(tmp_path, name: str, data) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# -- load_locomo_seed_batches -------------------------------------------


def _fake_load_dataset(plans_by_filter):
    def fake(path, sample_filter="all"):
        if sample_filter in ("", "all"):
            plans = [plan for plans in plans_by_filter.values() for plan in plans]
        else:
            plans = plans_by_filter.get(sample_filter, [])
        return [], plans

    return fake


PLAN_30 = {
    "sample_id": "conv-30",
    "session_batches": [
        {"messages": [
            {"role_id": "Gina", "content": "Hello there."},
            {"role_id": "Jon", "content": "Hi Gina."},
        ]},
    ],
}

PLAN_41 = {
    "sample_id": "conv-41",
    "session_batches": [
        {"messages": [
            {"role_id": "Alice", "content": "Good morning!"},
            {"role_id": "Bob", "content": "Morning Alice."},
            {"role_id": "Bob", "content": ""},
        ]},
    ],
}


def test_load_locomo_seed_batches_single(monkeypatch, tmp_path):
    path = _write_json(tmp_path, "data.json", [])
    monkeypatch.setattr(
        "benchmarks.locomo.dataset.load_dataset",
        _fake_load_dataset({"conv-30": [PLAN_30], "conv-41": [PLAN_41]}),
    )
    batches = load_locomo_seed_batches(path, sample_filter="conv-30")
    assert batches == [
        [
            {"role": "user", "content": "Hello there."},
            {"role": "assistant", "content": "Hi Gina."},
        ],
    ]


def test_load_locomo_seed_batches_comma_separated(monkeypatch, tmp_path):
    path = _write_json(tmp_path, "data.json", [])
    monkeypatch.setattr(
        "benchmarks.locomo.dataset.load_dataset",
        _fake_load_dataset({"conv-30": [PLAN_30], "conv-41": [PLAN_41]}),
    )
    batches = load_locomo_seed_batches(path, sample_filter="conv-30,conv-41")
    assert batches == [
        [
            {"role": "user", "content": "Hello there."},
            {"role": "assistant", "content": "Hi Gina."},
        ],
        [
            {"role": "user", "content": "Good morning!"},
            {"role": "assistant", "content": "Morning Alice."},
        ],
    ]


def test_load_locomo_seed_batches_all(monkeypatch, tmp_path):
    path = _write_json(tmp_path, "data.json", [])
    monkeypatch.setattr(
        "benchmarks.locomo.dataset.load_dataset",
        _fake_load_dataset({"conv-30": [PLAN_30], "conv-41": [PLAN_41]}),
    )
    batches = load_locomo_seed_batches(path, sample_filter="all")
    assert len(batches) == 2


def test_load_locomo_seed_batches_no_match_raises(monkeypatch, tmp_path):
    path = _write_json(tmp_path, "data.json", [])
    monkeypatch.setattr(
        "benchmarks.locomo.dataset.load_dataset",
        _fake_load_dataset({"conv-30": [PLAN_30]}),
    )
    with pytest.raises(ValueError, match="conv-99"):
        load_locomo_seed_batches(path, sample_filter="conv-99")


def test_load_locomo_seed_batches_empty_filter_raises(tmp_path):
    path = _write_json(tmp_path, "data.json", [])
    with pytest.raises(ValueError, match="sample_filter"):
        load_locomo_seed_batches(path, sample_filter="")


# -- load_tenant_specs (reused from probes._client) ----------------------


def test_load_tenant_specs_reuse(tmp_path, monkeypatch):
    path = _write_json(tmp_path, "tenants.json", {"tenants": [
        {"tenant_id": "t1", "auth_key": "inline", "user_id": "u1", "agent_id": "ag1"},
    ]})
    specs = load_tenant_specs(path)
    assert len(specs) == 1
    assert specs[0].tenant_id == "t1"
    assert specs[0].auth_key == "inline"
    assert specs[0].user_id == "u1"
    assert specs[0].agent_id == "ag1"

    env_path = _write_json(tmp_path, "tenants_env.json", {"tenants": [
        {"tenant_id": "t2", "auth_key_env": "SEED_TEST_AUTH_KEY"},
    ]})
    monkeypatch.setenv("SEED_TEST_AUTH_KEY", "from-env")
    assert load_tenant_specs(env_path)[0].auth_key == "from-env"

    missing_path = _write_json(tmp_path, "tenants_missing.json", {"tenants": [
        {"tenant_id": "t3", "auth_key_env": "SEED_TEST_MISSING_KEY"},
    ]})
    monkeypatch.delenv("SEED_TEST_MISSING_KEY", raising=False)
    with pytest.raises(ValueError, match="SEED_TEST_MISSING_KEY"):
        load_tenant_specs(missing_path)

    empty_path = _write_json(tmp_path, "tenants_empty.json", {"tenants": []})
    with pytest.raises(ValueError):
        load_tenant_specs(empty_path)


# -- seed_tenant / seed_tenant_from_conversations ------------------------


def test_seed_tenant_against_mock(server):
    _, _, base_url = server
    client = EchoMemHTTP(base_url, "k1", timeout_s=5.0, tenant_id="t1", user_id="u1")
    context = seed_tenant(
        client, idx=0, sessions=2, messages_per_session=2,
        commit_poll_timeout_s=10, poll_interval_s=0.01,
    )
    assert context.idx == 0
    assert context.tenant_id == "t1"
    assert context.user_id == "u1"
    assert context.auth_key == "k1"
    assert context.client is client
    assert context.seed_sessions == 2
    assert context.seed_messages == 8
    assert all("PERFANCHOR" not in query for query in context.queries)
    assert context.queries[:4] == list(context.query_cases)
    assert len(context.query_cases) == 4
    assert all(case["aliases"][0].startswith("PERFANCHOR-0-") for case in context.query_cases.values())
    assert len(context.queries) == 8  # 4 natural recall questions + 4 marker-free user fragments

    info = context.to_dict()
    assert info["idx"] == 0
    assert info["tenant_id"] == "t1"
    assert info["user_id"] == "u1"
    assert info["auth_key_configured"] is True
    assert info["queries"] == 8
    assert info["query_cases"] == 4
    assert info["seed_sessions"] == 2
    assert info["seed_messages"] == 8
    assert info["seed_elapsed_s"] >= 0.0


def test_seed_tenant_from_conversations(mock_server):
    httpd, _, base_url = mock_server(MockState(pending_attempts=0))
    try:
        client = EchoMemHTTP(base_url, "k", timeout_s=5.0, tenant_id="t1", user_id="u1")
        batches = [
            [
                {"role": "user", "content": "Can you remind me about the meeting?"},
                {"role": "assistant", "content": "Sure, at 3pm on Thursday."},
            ],
            [
                {"role": "user", "content": "What about the report deadline?"},
                {"role": "assistant", "content": "Friday next week."},
            ],
        ]
        context = seed_tenant_from_conversations(
            client, idx=1, batches=batches,
            commit_poll_timeout_s=10, poll_interval_s=0.01,
        )
        assert context.idx == 1
        assert context.seed_sessions == 2
        assert context.seed_messages == 4
        # query pool is built only from user messages, split on punctuation
        assert context.queries == [
            "Can you remind me about the meeting",
            "What about the report deadline",
        ]
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- TenantPreparer ------------------------------------------------------


def test_preparer_validation():
    with pytest.raises(ValueError, match="tenants"):
        TenantPreparer("http://x", tenants=0)
    with pytest.raises(ValueError, match="seed_concurrency"):
        TenantPreparer("http://x", seed_concurrency=0)
    with pytest.raises(ValueError, match="static"):
        TenantPreparer("http://x", auth_mode="static", tenants=2)
    TenantPreparer("http://x", auth_mode="static", tenants=1)


def test_preparer_identity_mode():
    assert TenantPreparer("http://x").identity_mode() == "provision"
    assert (
        TenantPreparer("http://x", auth_mode="static", tenants=1).identity_mode()
        == "static"
    )
    specs = [TenantSpec("t1", "k1")]
    assert TenantPreparer("http://x", tenant_specs=specs).identity_mode() == "tenant_config"


def test_preparer_keys_independent():
    assert TenantPreparer("http://x").keys_independent() is True
    unique = [TenantSpec("t1", "k1"), TenantSpec("t2", "k2")]
    assert TenantPreparer("http://x", tenant_specs=unique).keys_independent() is True
    duplicate = [TenantSpec("t1", "same"), TenantSpec("t2", "same")]
    assert TenantPreparer("http://x", tenant_specs=duplicate).keys_independent() is False
    empty = [TenantSpec("t1", "k1"), TenantSpec("t2", "")]
    assert TenantPreparer("http://x", tenant_specs=empty).keys_independent() is False


def test_preparer_static_single_tenant(mock_server):
    httpd, _, base_url = mock_server(MockState(pending_attempts=0))
    try:
        preparer = TenantPreparer(
            base_url,
            auth_mode="static",
            auth_key="static-key",
            tenant_id="st1",
            user_id="su1",
            tenants=1,
        )
        contexts = preparer.prepare(
            seed_sessions=1, messages_per_session=1, commit_poll_timeout_s=10
        )
        assert len(contexts) == 1
        assert contexts[0].tenant_id == "st1"
        assert contexts[0].user_id == "su1"
        assert contexts[0].auth_key == "static-key"
        assert contexts[0].seed_sessions == 1
        assert contexts[0].seed_messages == 2
        preparer.cleanup()  # static provisions nothing -> no-op
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_preparer_tenant_config_multi_tenant(mock_server):
    httpd, _, base_url = mock_server(MockState(pending_attempts=0))
    try:
        specs = [
            TenantSpec("t1", "k1", user_id="u1", account_id="t1", agent_id="default"),
            TenantSpec("t2", "k2", user_id="u2", account_id="t2", agent_id="default"),
        ]
        preparer = TenantPreparer(
            base_url, tenants=1, tenant_specs=specs, seed_concurrency=2
        )
        assert preparer.identity_mode() == "tenant_config"
        assert preparer.keys_independent() is True
        contexts = preparer.prepare(
            seed_sessions=1, messages_per_session=1, commit_poll_timeout_s=10
        )
        assert [c.tenant_id for c in contexts] == ["t1", "t2"]
        assert [c.user_id for c in contexts] == ["u1", "u2"]
        assert [c.auth_key for c in contexts] == ["k1", "k2"]
        preparer.cleanup()  # config credentials are not owned by the preparer
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_preparer_provision_mode():
    counters = {"tenants": 0, "users": 0, "keys": 0, "deletes": 0, "bootstrap_seen": 0}
    httpd = _start_server(_make_provision_handler(counters))
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        preparer = TenantPreparer(
            base_url, auth_mode="provision", tenants=2, label_prefix="perf"
        )
        assert preparer.identity_mode() == "provision"
        assert preparer.keys_independent() is True
        contexts = preparer.prepare(
            seed_sessions=1, messages_per_session=1, commit_poll_timeout_s=10
        )
        assert [c.tenant_id for c in contexts] == ["pt1", "pt2"]
        assert [c.user_id for c in contexts] == ["pu1", "pu2"]
        assert [c.auth_key for c in contexts] == ["pk1", "pk2"]
        assert counters["tenants"] == 2
        assert counters["users"] == 2
        assert counters["keys"] == 2
        # every user/key provisioning call carried the bootstrap key header
        assert counters["bootstrap_seen"] == 4
        preparer.cleanup()
        assert counters["deletes"] == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- _seed_session_flow re-pour on failure -------------------------------


def test_seed_session_flow_repours_after_add_failure():
    counters = {"opens": 0, "adds": 0, "rejected": 0}
    httpd = _start_server(_make_flaky_add_handler(counters, fail_first_adds=1))
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        client = EchoMemHTTP(base_url, "k", timeout_s=5.0, tenant_id="t1", user_id="u1")
        messages = [
            ("user", "hello one"),
            ("assistant", "reply one"),
            ("user", "hello two"),
            ("assistant", "reply two"),
        ]
        texts = _seed_session_flow(
            client, idx=0, session_idx=0, messages=messages,
            commit_poll_timeout_s=10, poll_interval_s=0.01,
        )
        assert texts == ["hello one", "reply one", "hello two", "reply two"]
        assert counters["rejected"] == 1  # first add of the first pour failed
        assert counters["opens"] == 2  # whole session re-poured once
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_seed_session_flow_raises_after_two_failures():
    counters = {"opens": 0, "adds": 0, "rejected": 0}
    httpd = _start_server(_make_flaky_add_handler(counters, fail_first_adds=10))
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        client = EchoMemHTTP(base_url, "k", timeout_s=5.0, tenant_id="t1", user_id="u1")
        messages = [("user", "hello one"), ("assistant", "reply one")]
        with pytest.raises(RuntimeError, match="seed session failed"):
            _seed_session_flow(
                client, idx=0, session_idx=0, messages=messages,
                commit_poll_timeout_s=10, poll_interval_s=0.01,
            )
        assert counters["opens"] == 2  # both pours attempted
        assert counters["rejected"] == 2
    finally:
        httpd.shutdown()
        httpd.server_close()
