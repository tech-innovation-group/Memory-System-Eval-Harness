"""Unit tests for EchoMemClient -- mock-based, no real HTTP server.

Covers functional points NOT already tested in ``test_echomem_client.py``:
``_headers``, ``search``, ``fs_read``, ``health``, ``delete_current_identity``,
``commit_session``, ``commit_status``, ``has_archives``, ``_parse_commit_status``,
``_extract_commit_error``, ``open_session`` body structure, ``add_message``
optional fields, and the zero third-party dependency guarantee.

All tests monkey-patch ``client._post`` / ``client._get`` with fake functions
that return predetermined dicts -- no real HTTP server is used.
"""

from __future__ import annotations

import inspect
import unittest
from typing import Any

from backends.echomem import client as client_module
from backends.echomem.client import EchoMemClient
from backends.memory_types import SearchResult


# ------------------------------------------------------------------ #
#  _headers()                                                          #
# ------------------------------------------------------------------ #

class TestHeaders(unittest.TestCase):
    def test_with_auth_key_returns_content_type_and_auth_header(self) -> None:
        client = EchoMemClient(auth_key="secret123")
        h = client._headers()
        self.assertEqual("application/json", h["Content-Type"])
        self.assertEqual("secret123", h["X-Auth-Key"])

    def test_without_auth_key_omits_auth_header(self) -> None:
        client = EchoMemClient(auth_key="")
        h = client._headers()
        self.assertEqual("application/json", h["Content-Type"])
        self.assertNotIn("X-Auth-Key", h)


# ------------------------------------------------------------------ #
#  search()                                                            #
# ------------------------------------------------------------------ #

class TestSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(
            auth_key="k", account="a", user_id="u", agent_id="agent-x",
        )
        self.captured_body: dict[str, Any] = {}

    def _install_post(self, response: dict[str, Any]) -> None:
        def fake_post(path: str, body: dict | None = None, **_kw: Any) -> dict[str, Any]:
            self.captured_body.update(body or {})
            return response

        self.client._post = fake_post  # type: ignore[method-assign]

    def test_extracts_items_from_both_response_formats(self) -> None:
        cases = [
            ({"result": {"items": [
                {"uri": "mem://1", "score": 0.9, "content": "alpha"},
                {"uri": "mem://2", "score": 0.5, "content": "beta"},
            ]}}, 2),
            ({"items": [
                {"uri": "mem://3", "score": 0.8, "content": "gamma"},
            ]}, 1),
            ({"result": {"items": []}}, 0),
            ({"items": []}, 0),
        ]
        for resp, expected_len in cases:
            with self.subTest(resp=resp):
                self._install_post(resp)
                results = self.client.search("query", top_k=5)
                self.assertEqual(expected_len, len(results))

    def test_empty_result_returns_empty_list(self) -> None:
        for resp in ({"result": {"items": []}}, {"items": []}, {"result": {}}):
            with self.subTest(resp=resp):
                self._install_post(resp)
                self.assertEqual([], self.client.search("query"))

    def test_body_defaults_when_no_optional_args(self) -> None:
        self._install_post({"items": []})
        self.client.search("hello world", top_k=7)
        self.assertEqual("hello world", self.captured_body["query"])
        self.assertEqual("agent-x", self.captured_body["agent_id"])
        self.assertEqual(7, self.captured_body["limit"])
        self.assertFalse(self.captured_body["include_explain"])
        self.assertTrue(self.captured_body["include_debug"])
        self.assertNotIn("session_id", self.captured_body)

    def test_body_includes_session_id_when_provided(self) -> None:
        self._install_post({"items": []})
        self.client.search("q", session_id="sess-42")
        self.assertEqual("sess-42", self.captured_body["session_id"])

    def test_body_uses_explicit_agent_id_over_self(self) -> None:
        self._install_post({"items": []})
        self.client.search("q", agent_id="custom-agent")
        self.assertEqual("custom-agent", self.captured_body["agent_id"])

    def test_search_result_from_dict_conversion(self) -> None:
        self._install_post({"result": {"items": [
            {"uri": "u1", "score": "0.95", "content": "c1", "type": "episode"},
        ]}})
        results = self.client.search("q")
        r = results[0]
        self.assertIsInstance(r, SearchResult)
        self.assertEqual("u1", r.uri)
        self.assertAlmostEqual(0.95, r.score)
        self.assertEqual("c1", r.content)
        self.assertEqual("episode", r.memory_type)


# ------------------------------------------------------------------ #
#  fs_read()                                                           #
# ------------------------------------------------------------------ #

class TestFsRead(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(auth_key="k")
        self.captured: list[tuple[str, dict[str, Any]]] = []

    def _install_get(self, response: dict[str, Any]) -> None:
        def fake_get(path: str, query: dict | None = None, **_kw: Any) -> dict[str, Any]:
            self.captured.append((path, dict(query or {})))
            return response

        self.client._get = fake_get  # type: ignore[method-assign]

    def test_response_formats_and_fallback_chain(self) -> None:
        """content > text > result.content > result.text > ''."""
        cases: list[tuple[dict[str, Any], str]] = [
            ({"content": "hello"}, "hello"),
            ({"text": "world"}, "world"),
            ({"result": {"content": "nested"}}, "nested"),
            ({"result": {"text": "nested_t"}}, "nested_t"),
            ({}, ""),
            # multi-key priority: content beats text at same level
            ({"content": "a", "text": "b"}, "a"),
            ({"content": "", "text": "b"}, "b"),
            ({"text": "b", "result": {"content": "c"}}, "b"),
            ({"result": {"content": "c", "text": "d"}}, "c"),
            ({"result": {"text": "d"}}, "d"),
            ({"content": "", "text": "", "result": {}}, ""),
        ]
        for resp, expected in cases:
            with self.subTest(resp=resp):
                self._install_get(resp)
                self.assertEqual(expected, self.client.fs_read("echo://x"))

    def test_uri_passed_as_query_param(self) -> None:
        self._install_get({"content": "data"})
        self.client.fs_read("echo://doc/1")
        self.assertEqual([("/fs/read", {"uri": "echo://doc/1"})], self.captured)


# ------------------------------------------------------------------ #
#  health()                                                            #
# ------------------------------------------------------------------ #

class TestHealth(unittest.TestCase):
    def test_calls_health_endpoint_and_returns_response(self) -> None:
        client = EchoMemClient(auth_key="k")
        captured: list[str] = []

        def fake_get(path: str, query: dict | None = None, **_kw: Any) -> dict[str, Any]:
            captured.append(path)
            return {"status": "ok", "version": "1.2.3"}

        client._get = fake_get  # type: ignore[method-assign]
        result = client.health()
        self.assertEqual(["/health"], captured)
        self.assertEqual({"status": "ok", "version": "1.2.3"}, result)


class TestEpisodeEndpoints(unittest.TestCase):
    def test_runtime_calls_runtime_endpoint(self) -> None:
        client = EchoMemClient(auth_key="k")
        captured: list[str] = []

        def fake_get(path: str, query=None, **_kw):
            captured.append(path)
            return {"engines": []}

        client._get = fake_get  # type: ignore[method-assign]
        self.assertEqual({"engines": []}, client.runtime())
        self.assertEqual(["/runtime"], captured)

    def test_generate_episode_sends_post_without_body(self) -> None:
        client = EchoMemClient(auth_key="k")
        captured = {}

        def fake_request(req, **_kw):
            captured["method"] = req.get_method()
            captured["data"] = req.data
            captured["url"] = req.full_url
            captured["max_attempts"] = _kw.get("max_attempts")
            return {"status": "generated"}

        client._do_request = fake_request  # type: ignore[method-assign]
        self.assertEqual(
            {"status": "generated"},
            client.generate_episode(),
        )
        self.assertEqual("POST", captured["method"])
        self.assertIsNone(captured["data"])
        self.assertEqual(1, captured["max_attempts"])
        self.assertTrue(captured["url"].endswith("/api/cognitive/episode/generate"))


# ------------------------------------------------------------------ #
#  delete_current_identity()                                           #
# ------------------------------------------------------------------ #

class TestDeleteCurrentIdentity(unittest.TestCase):
    def test_success_does_not_raise(self) -> None:
        client = EchoMemClient(auth_key="k")
        client._post = lambda path, body=None, **kw: {"status": "deleted"}  # type: ignore
        client.delete_current_identity()  # should not raise

    def test_failure_raises_runtime_error(self) -> None:
        client = EchoMemClient(auth_key="k")
        client._post = lambda path, body=None, **kw: {"status": "pending"}  # type: ignore
        with self.assertRaises(RuntimeError):
            client.delete_current_identity()

    def test_calls_correct_endpoint_with_empty_body(self) -> None:
        client = EchoMemClient(auth_key="k")
        captured: list[tuple[str, dict | None]] = []

        def fake_post(path: str, body: dict | None = None, **_kw: Any) -> dict[str, Any]:
            captured.append((path, body))
            return {"status": "deleted"}

        client._post = fake_post  # type: ignore[method-assign]
        client.delete_current_identity()
        self.assertEqual([("/api/auth/account/delete", {})], captured)


# ------------------------------------------------------------------ #
#  commit_session()                                                    #
# ------------------------------------------------------------------ #

class TestCommitSession(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(auth_key="k")

    def _install_post(self, response: dict[str, Any]) -> None:
        self.client._post = lambda path, body=None, **kw: response  # type: ignore

    def test_extracts_archive_id_from_all_response_formats(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"archive_id": "arc-1"}, "arc-1"),
            ({"task_id": "task-1"}, "task-1"),
            ({"result": {"archive_id": "arc-2"}}, "arc-2"),
            ({"result": {"task_id": "task-2"}}, "task-2"),
            ({"id": "id-1"}, "id-1"),
        ]
        for resp, expected in cases:
            with self.subTest(resp=resp):
                self._install_post(resp)
                aid = self.client.commit_session("sess-1")
                self.assertEqual(expected, aid)


# ------------------------------------------------------------------ #
#  commit_status()                                                     #
# ------------------------------------------------------------------ #

class TestCommitStatus(unittest.TestCase):
    def test_calls_correct_endpoint_and_returns_raw_response(self) -> None:
        client = EchoMemClient(auth_key="k")
        captured: list[str] = []
        raw = {"status": "completed", "progress": 100}

        def fake_get(path: str, query: dict | None = None, **_kw: Any) -> dict[str, Any]:
            captured.append(path)
            return raw

        client._get = fake_get  # type: ignore[method-assign]
        result = client.commit_status("sess-1", "arc-1")
        self.assertEqual(["/api/sessions/sess-1/commits/arc-1"], captured)
        self.assertEqual(raw, result)


# ------------------------------------------------------------------ #
#  has_archives()                                                      #
# ------------------------------------------------------------------ #

class TestHasArchives(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(auth_key="k")

    def _install_get(self, response: dict[str, Any]) -> None:
        self.client._get = lambda path, query=None, **kw: response  # type: ignore

    def test_response_variants(self) -> None:
        cases: list[tuple[dict[str, Any], bool]] = [
            ({"archives": [{"id": "a1"}]}, True),
            ({"commits": [{"id": "c1"}]}, True),
            ({"archives": []}, False),
            ({"commits": []}, False),
            ({}, False),
            ({"other": "data"}, False),
        ]
        for resp, expected in cases:
            with self.subTest(resp=resp):
                self._install_get(resp)
                self.assertEqual(expected, self.client.has_archives("sess-1"))


# ------------------------------------------------------------------ #
#  _parse_commit_status()  -- nested dict handling                    #
# ------------------------------------------------------------------ #

class TestParseCommitStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(auth_key="k")

    def test_status_variants(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"status": "Completed"}, "completed"),
            ({"status": {"status": "completed"}}, "completed"),
            ({"status": {"stage": "processing"}}, "processing"),
            ({"status": {"state": "done"}}, "done"),
            ({"stage": "Processing"}, "processing"),
            ({"state": "Done"}, "done"),
            ({}, ""),
        ]
        for resp, expected in cases:
            with self.subTest(resp=resp):
                self.assertEqual(expected, self.client._parse_commit_status(resp))


# ------------------------------------------------------------------ #
#  _extract_commit_error()  -- nested dict handling                   #
# ------------------------------------------------------------------ #

class TestExtractCommitError(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(auth_key="k")

    def test_error_variants(self) -> None:
        cases: list[tuple[dict[str, Any], str, str]] = [
            # (resp, status_param, expected)
            ({"status": {"error": "boom"}}, "failed", "boom"),
            ({"status": {"status": "completed"}}, "failed", "failed"),
            ({"status": "failed", "error": "oops"}, "failed", "oops"),
            ({"status": "failed"}, "failed", "failed"),
        ]
        for resp, status, expected in cases:
            with self.subTest(resp=resp):
                self.assertEqual(
                    expected, self.client._extract_commit_error(resp, status),
                )


# ------------------------------------------------------------------ #
#  open_session()  -- body structure & response parsing               #
# ------------------------------------------------------------------ #

class TestOpenSession(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(
            auth_key="k",
            account="acct-1",
            user_id="user-1",
            agent_id="agent-1",
            workspace="",
        )
        self.captured_body: dict[str, Any] = {}

    def _install_post(self, response: dict[str, Any]) -> None:
        def fake_post(path: str, body: dict | None = None, **_kw: Any) -> dict[str, Any]:
            self.captured_body.update(body or {})
            return response

        self.client._post = fake_post  # type: ignore[method-assign]

    # -- body structure ------------------------------------------------

    def test_body_contains_agent_id(self) -> None:
        self._install_post({"session_id": "s1"})
        self.client.open_session("title")
        self.assertEqual("agent-1", self.captured_body["agent_id"])

    def test_body_contains_metadata_with_title_account_user(self) -> None:
        self._install_post({"session_id": "s1"})
        self.client.open_session("My Title")
        meta = self.captured_body["metadata"]
        self.assertEqual("My Title", meta["title"])
        self.assertEqual("acct-1", meta["account_id"])
        self.assertEqual("user-1", meta["user_id"])

    def test_body_contains_title_when_non_empty(self) -> None:
        self._install_post({"session_id": "s1"})
        self.client.open_session("Hello")
        self.assertEqual("Hello", self.captured_body["title"])

    def test_body_omits_title_when_empty(self) -> None:
        self._install_post({"session_id": "s1"})
        self.client.open_session("")
        self.assertNotIn("title", self.captured_body)

    def test_body_contains_workspace_when_set(self) -> None:
        client = EchoMemClient(auth_key="k", workspace="ws-1")
        captured: dict[str, Any] = {}

        def fake_post(path: str, body: dict | None = None, **_kw: Any) -> dict[str, Any]:
            captured.update(body or {})
            return {"session_id": "s1"}

        client._post = fake_post  # type: ignore[method-assign]
        client.open_session("t")
        self.assertEqual("ws-1", captured["workspace"])

    def test_body_omits_workspace_when_empty(self) -> None:
        self._install_post({"session_id": "s1"})
        self.client.open_session("t")
        self.assertNotIn("workspace", self.captured_body)

    # -- response parsing ----------------------------------------------

    def test_response_session_id_top_level(self) -> None:
        self._install_post({"session_id": "sid-1"})
        self.assertEqual("sid-1", self.client.open_session("t"))

    def test_response_id_fallback(self) -> None:
        self._install_post({"id": "sid-2"})
        self.assertEqual("sid-2", self.client.open_session("t"))

    def test_response_scope_session_id(self) -> None:
        self._install_post({"scope": {"session_id": "sid-3"}})
        self.assertEqual("sid-3", self.client.open_session("t"))

    def test_response_no_id_raises_runtime_error(self) -> None:
        self._install_post({"foo": "bar"})
        with self.assertRaises(RuntimeError):
            self.client.open_session("t")


# ------------------------------------------------------------------ #
#  add_message()  -- optional fields                                  #
# ------------------------------------------------------------------ #

class TestAddMessage(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(auth_key="k")
        self.captured_body: dict[str, Any] = {}

    def _install_post(self, response: dict[str, Any] | None = None) -> None:
        def fake_post(path: str, body: dict | None = None, **_kw: Any) -> dict[str, Any]:
            self.captured_body.update(body or {})
            return response or {}

        self.client._post = fake_post  # type: ignore[method-assign]

    def test_minimal_call_has_no_metadata(self) -> None:
        self._install_post()
        self.client.add_message("sess-1", "user", "hello")
        self.assertEqual("user", self.captured_body["role"])
        self.assertEqual("hello", self.captured_body["content"])
        self.assertNotIn("metadata", self.captured_body)
        self.assertNotIn("created_at", self.captured_body)
        self.assertNotIn("role_id", self.captured_body)
        self.assertNotIn("name", self.captured_body)

    def test_with_created_at_adds_to_body_and_metadata(self) -> None:
        self._install_post()
        self.client.add_message(
            "sess-1", "user", "hi", created_at="2024-01-01T00:00:00",
        )
        self.assertEqual("2024-01-01T00:00:00", self.captured_body["created_at"])
        self.assertEqual(
            "2024-01-01T00:00:00", self.captured_body["metadata"]["created_at"],
        )

    def test_with_role_id_adds_to_body_and_metadata(self) -> None:
        self._install_post()
        self.client.add_message("sess-1", "user", "hi", role_id="Alice")
        self.assertEqual("Alice", self.captured_body["role_id"])
        self.assertEqual("Alice", self.captured_body["name"])
        self.assertEqual("Alice", self.captured_body["metadata"]["role_id"])

    def test_with_both_optional_fields_metadata_has_both(self) -> None:
        self._install_post()
        self.client.add_message(
            "sess-1", "assistant", "response",
            created_at="2024-01-01T12:00:00", role_id="Bob",
        )
        meta = self.captured_body["metadata"]
        self.assertIn("created_at", meta)
        self.assertIn("role_id", meta)
        self.assertEqual("2024-01-01T12:00:00", meta["created_at"])
        self.assertEqual("Bob", meta["role_id"])


# ------------------------------------------------------------------ #
#  fetch_logs()                                                        #
# ------------------------------------------------------------------ #

class TestFetchLogs(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EchoMemClient(
            auth_key="k", account="tenant-1", user_id="user-1",
        )
        self.captured: dict[str, Any] = {}

    def _install(self, responses: list[dict[str, Any]]) -> None:
        iterator = iter(responses)

        def fake_do_request(req: Any) -> dict[str, Any]:
            self.captured = {
                "url": req.full_url,
                "headers": {k: v for k, v in req.header_items()},
            }
            return next(iterator)

        self.client._do_request = fake_do_request  # type: ignore[method-assign]

    def test_requires_at_least_one_filter(self) -> None:
        with self.assertRaises(ValueError):
            self.client.fetch_logs()

    def test_sends_only_nonempty_filters(self) -> None:
        self._install([
            {"result": {"items": [], "page": {"has_more": False}}},
        ])
        self.client.fetch_logs(tenant_id="t1", user_id="u1")
        self.assertIn("/api/logs", self.captured["url"])
        self.assertIn("tenant_id=t1", self.captured["url"])
        self.assertIn("user_id=u1", self.captured["url"])
        self.assertIn("limit=200", self.captured["url"])
        self.assertIn("offset=0", self.captured["url"])
        self.assertNotIn("event=", self.captured["url"])
        self.assertNotIn("route=", self.captured["url"])
        self.assertNotIn("request_id=", self.captured["url"])

    def test_sends_optional_filters_when_provided(self) -> None:
        self._install([
            {"result": {"items": [], "page": {"has_more": False}}},
        ])
        self.client.fetch_logs(
            tenant_id="t", request_id="req-1", event="commit",
            route="/api/sessions/open", since="2026-01-01T00:00:00Z", limit=50,
        )
        self.assertIn("request_id=req-1", self.captured["url"])
        self.assertIn("event=commit", self.captured["url"])
        self.assertIn("route=%2Fapi%2Fsessions%2Fopen", self.captured["url"])
        self.assertIn("since=2026-01-01T00%3A00%3A00Z", self.captured["url"])
        self.assertIn("limit=50", self.captured["url"])

    def test_sends_x_log_access_key_when_configured(self) -> None:
        client = EchoMemClient(auth_key="k", log_access_key="la-key")
        captured: dict[str, Any] = {}

        def fake_do_request(req: Any) -> dict[str, Any]:
            captured["headers"] = {
                k.lower(): v for k, v in req.header_items()
            }
            return {"result": {"items": [], "page": {"has_more": False}}}

        client._do_request = fake_do_request  # type: ignore[method-assign]
        client.fetch_logs(tenant_id="t")
        self.assertEqual("la-key", captured["headers"].get("x-log-access-key"))
        self.assertEqual("k", captured["headers"].get("x-auth-key"))

    def test_aggregates_pages_until_has_more_false(self) -> None:
        self._install([
            {"result": {"items": [{"ts": "a"}, {"ts": "b"}],
                        "page": {"has_more": True}}},
            {"result": {"items": [{"ts": "c"}],
                        "page": {"has_more": False}}},
        ])
        result = self.client.fetch_logs(tenant_id="t")
        self.assertEqual(3, len(result["items"]))
        self.assertEqual([{"ts": "a"}, {"ts": "b"}, {"ts": "c"}], result["items"])

    def test_stops_when_page_returns_no_items(self) -> None:
        self._install([
            {"result": {"items": [], "page": {"has_more": True}}},
        ])
        result = self.client.fetch_logs(tenant_id="t")
        self.assertEqual([], result["items"])


# ------------------------------------------------------------------ #
#  Zero third-party dependencies                                      #
# ------------------------------------------------------------------ #

class TestModuleImports(unittest.TestCase):
    def test_only_stdlib_and_backends_imports(self) -> None:
        """The client module must have zero third-party dependencies."""
        source = inspect.getsource(client_module)
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        allowed = (
            "from __future__",
            "import logging",
            "import time",
            "import urllib",
            "from typing",
            "from backends.memory_types",
        )
        self.assertTrue(import_lines, "expected at least one import line")
        for line in import_lines:
            self.assertTrue(
                line.startswith(allowed),
                f"Unexpected third-party import: {line!r}",
            )


if __name__ == "__main__":
    unittest.main()
