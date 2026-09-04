from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backends.echomem.client import EchoMemClient


class _Handler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict]] = []

    def log_message(self, *_args) -> None:
        return

    def _write(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.requests.append(("POST", self.path, payload))
        if self.path == "/api/sessions/open":
            self._write(200, {"scope": {"session_id": "session_test"}})
        else:
            self._write(200, {})

    def do_GET(self) -> None:
        self.requests.append(("GET", self.path, {}))
        self._write(400, {"error": "SessionError", "message": "Unknown session commit"})


class EchoMemClientTests(unittest.TestCase):
    def test_provisions_and_switches_to_isolated_identity(self) -> None:
        client = EchoMemClient(auth_key="old", account="old", user_id="old")
        responses = iter([
            {"tenant": {"tenant_id": "tenant_new"}},
            {"user": {"user_id": "user_new"}},
            {"auth_key": "ek_new"},
        ])
        paths: list[str] = []

        def fake_post(path, body=None, **kwargs):
            paths.append(path)
            return next(responses)

        client._post = fake_post  # type: ignore[method-assign]
        identity = client.provision_isolated_identity("evaluation")

        self.assertEqual({"tenant_id": "tenant_new", "user_id": "user_new"}, identity)
        self.assertEqual("ek_new", client.auth_key)
        self.assertEqual("tenant_new", client.account)
        self.assertEqual("user_new", client.user_id)
        self.assertEqual(
            [
                "/api/auth/tenants",
                "/api/auth/tenants/tenant_new/users",
                "/api/auth/tenants/tenant_new/users/user_new/key",
            ],
            paths,
        )

    def test_uses_local_tenant_bootstrap_key_for_user_and_key(self) -> None:
        client = EchoMemClient()
        observed_headers: list[dict[str, str]] = []
        responses = iter([
            {
                "tenant": {"tenant_id": "tenant_new"},
                "bootstrap_key": "bootstrap_new",
            },
            {"user": {"user_id": "user_new"}},
            {"auth_key": "ek_new"},
        ])

        def fake_post(path, body=None, **kwargs):
            observed_headers.append(client._headers())
            return next(responses)

        client._post = fake_post  # type: ignore[method-assign]
        client.provision_isolated_identity("evaluation")

        self.assertNotIn("X-EchoMem-Bootstrap-Key", observed_headers[0])
        self.assertEqual("bootstrap_new", observed_headers[1]["X-EchoMem-Bootstrap-Key"])
        self.assertEqual("bootstrap_new", observed_headers[2]["X-EchoMem-Bootstrap-Key"])
        self.assertEqual("", client.bootstrap_auth_key)

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _Handler.requests.clear()
        self.client = EchoMemClient(
            f"http://127.0.0.1:{self.server.server_port}",
            account="account-a",
            user_id="user-a",
            agent_id="agent-a",
            max_retries=1,
        )

    def test_sends_identity_and_message_metadata(self) -> None:
        session_id = self.client.open_session("title-a")
        self.client.add_message(
            session_id,
            "user",
            "hello",
            created_at="2023-01-19T13:30:00",
            role_id="Jon",
        )

        open_payload = _Handler.requests[0][2]
        message_payload = _Handler.requests[1][2]
        self.assertEqual("account-a", open_payload["metadata"]["account_id"])
        self.assertEqual("user-a", open_payload["metadata"]["user_id"])
        self.assertEqual("Jon", message_payload["metadata"]["role_id"])
        self.assertEqual("Jon", message_payload["name"])

    def test_http_400_is_terminal_commit_failure(self) -> None:
        result = self.client.poll_commit(
            "session_test",
            "archive_test",
            timeout_s=5,
            poll_interval_s=0.01,
        )
        self.assertEqual("failed", result.status)
        self.assertEqual(1, result.polls)
        self.assertIn("HTTP 400", result.error)

    def test_public_filesystem_list_and_glob_use_http_contract(self) -> None:
        calls: list[tuple[str, str, dict]] = []

        def fake_get(path, query=None, **_kwargs):
            calls.append(("GET", path, dict(query or {})))
            return {
                "result": {
                    "entries": [{
                        "name": "overview.md",
                        "uri": "echo://engine/session/overview.md",
                    }]
                }
            }

        def fake_post(path, body=None, **_kwargs):
            calls.append(("POST", path, dict(body or {})))
            return {
                "result": {
                    "entries": [{
                        "name": "messages.jsonl",
                        "uri": "echo://engine/session/messages.jsonl",
                    }]
                }
            }

        self.client._get = fake_get  # type: ignore[method-assign]
        self.client._post = fake_post  # type: ignore[method-assign]

        listed = self.client.fs_list("echo://engine/sessions")
        globbed = self.client.fs_glob(
            "echo://engine/sessions/*/messages.jsonl"
        )

        self.assertEqual("overview.md", listed[0]["name"])
        self.assertEqual("messages.jsonl", globbed[0]["name"])
        self.assertEqual([
            (
                "GET",
                "/fs/ls",
                {"uri": "echo://engine/sessions"},
            ),
            (
                "POST",
                "/fs/glob",
                {
                    "pattern": (
                        "echo://engine/sessions/*/messages.jsonl"
                    )
                },
            ),
        ], calls)


    def test_resource_client_methods_use_http_contract(self) -> None:
        calls: list[tuple[str, str, dict]] = []

        def fake_post(path, body=None, **_kwargs):
            calls.append(("POST", path, dict(body or {})))
            if path == "/api/resources/search":
                return {
                    "status": "ok",
                    "query": "q",
                    "results": [
                        {
                            "path": "user/hotpotqa/doc-x",
                            "domain": "user",
                            "chunk_index": 0,
                            "text": "chunk text",
                            "score": 0.91,
                            "source_uri": "echo://resources/user/hotpotqa/doc-x",
                            "rank": 1,
                        }
                    ],
                }
            if path == "/api/resources/reindex_all":
                return {"reindexed": 3, "degraded": 0, "errors": 0}
            return {"path": "user/hotpotqa/doc-x", "uri": "echo://resources/user/hotpotqa/doc-x", "status": "accepted"}

        def fake_get(path, query=None, **_kwargs):
            calls.append(("GET", path, dict(query or {})))
            return {"path": "user/hotpotqa/doc-x", "status": "indexed"}

        self.client._post = fake_post  # type: ignore[method-assign]
        self.client._get = fake_get  # type: ignore[method-assign]

        add_resp = self.client.add_resource(
            "hotpotqa/doc-x",
            "content",
            name="Doc X",
            tags=["hotpotqa"],
            metadata={"hotpotqa_title": "Doc X"},
        )
        results = self.client.search_resources(
            "query", limit=5, tags=["hotpotqa"], paths=["hotpotqa/"]
        )
        reindex = self.client.reindex_all_resources()
        status = self.client.resource_index_status("user/hotpotqa/doc-x")

        self.assertEqual("accepted", add_resp["status"])
        self.assertEqual("chunk text", results[0]["text"])
        self.assertEqual(0.91, results[0]["score"])
        self.assertEqual("user/hotpotqa/doc-x", results[0]["path"])
        self.assertEqual(3, reindex["reindexed"])
        self.assertEqual("indexed", status["status"])

        self.assertEqual([
            ("POST", "/api/resources", {
                "content": "content",
                "name": "Doc X",
                "content_type": "text/markdown",
                "tags": ["hotpotqa"],
                "metadata": {"hotpotqa_title": "Doc X"},
                "path": "hotpotqa/doc-x",
            }),
            ("POST", "/api/resources/search", {
                "query": "query",
                "limit": 5,
                "tags": ["hotpotqa"],
                "paths": ["hotpotqa/"],
            }),
            ("POST", "/api/resources/reindex_all", {}),
            ("GET", "/api/resources/index", {"path": "user/hotpotqa/doc-x"}),
        ], calls)

    def test_wait_for_resource_index_polls_until_done(self) -> None:
        statuses = iter([
            {"status": "running"},
            {"status": "running"},
            {"status": "completed", "detail": {"chunk_count": 3}},
        ])
        path_calls: list[str] = []

        def fake_get(path, query=None, **_kwargs):
            path_calls.append((query or {}).get("path", ""))
            return next(statuses)

        self.client._get = fake_get  # type: ignore[method-assign]
        result = self.client.wait_for_resource_index(
            ["user/hotpotqa/doc-x"],
            timeout_s=10,
            poll_interval_s=0.01,
        )
        self.assertEqual({"indexed": 1, "failed": {}}, result)
        self.assertEqual(["user/hotpotqa/doc-x"] * 3, path_calls)

    def test_wait_for_resource_index_reports_failures(self) -> None:
        def fake_get(path, query=None, **_kwargs):
            return {"status": "failed", "detail": {"error": "embed failed"}}

        self.client._get = fake_get  # type: ignore[method-assign]
        result = self.client.wait_for_resource_index(
            ["user/hotpotqa/doc-x", "user/hotpotqa/doc-y"],
            timeout_s=10,
            poll_interval_s=0.01,
        )
        self.assertEqual(0, result["indexed"])
        self.assertIn("user/hotpotqa/doc-x", result["failed"])

    def test_wait_for_resource_index_reports_progress(self) -> None:
        statuses = iter([
            {"status": "running"},
            {"status": "completed", "detail": {"chunk_count": 3}},
        ])

        def fake_get(path, query=None, **_kwargs):
            return next(statuses)

        self.client._get = fake_get  # type: ignore[method-assign]
        calls: list[tuple[int, int]] = []
        result = self.client.wait_for_resource_index(
            ["user/hotpotqa/doc-x"],
            timeout_s=10,
            poll_interval_s=0.01,
            progress=lambda done, total: calls.append((done, total)),
        )
        self.assertEqual({"indexed": 1, "failed": {}}, result)
        # pass 1: still running -> (0, 1); pass 2: terminal -> (1, 1)
        self.assertEqual([(0, 1), (1, 1)], calls)

    def test_wait_for_resource_index_times_out(self) -> None:
        def fake_get(path, query=None, **_kwargs):
            return {"status": "running"}

        self.client._get = fake_get  # type: ignore[method-assign]
        with self.assertRaises(TimeoutError):
            self.client.wait_for_resource_index(
                ["user/hotpotqa/doc-x"],
                timeout_s=0.05,
                poll_interval_s=0.01,
            )


if __name__ == "__main__":
    unittest.main()
