"""Shared fixtures: an in-process mock EchoMem HTTP server."""

from __future__ import annotations

import http.server
import itertools
import json
import re
import threading
import time
import urllib.parse

import pytest


class MockState:
    def __init__(
        self,
        *,
        search_empty: bool = False,
        search_degraded: bool = False,
        always_pending: bool = False,
        pending_attempts: int = 2,
        poll_fail_after: int = 0,
        delay_s: float = 0.0,
        fail_open: bool = False,
        fail_add: bool = False,
        fail_commit: bool = False,
        commit_status: int = 202,
        commit_missing_archive: bool = False,
        poll_http_status: int = 200,
    ):
        self.search_empty = search_empty
        self.search_degraded = search_degraded
        self.always_pending = always_pending
        self.pending_attempts = pending_attempts
        self.poll_fail_after = poll_fail_after
        self.delay_s = delay_s
        self.fail_open = fail_open
        self.fail_add = fail_add
        self.fail_commit = fail_commit
        self.commit_status = commit_status
        self.commit_missing_archive = commit_missing_archive
        self.poll_http_status = poll_http_status
        self.metrics_text: str | None = None
        self.sessions = itertools.count(1)
        self.messages = itertools.count(1)
        self.archives = itertools.count(1)
        self.poll_counts: dict[tuple[str, str], int] = {}
        self.search_queries: list[str] = []
        self.search_agent_ids: list[str] = []
        self.semantic_markers: dict[tuple[str, str], str] = {}
        self.connections = 0


def _make_handler(state: MockState):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code: int, payload: dict) -> None:
            if state.delay_s:
                time.sleep(state.delay_s)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _count_connection(self):
            if not getattr(self, "_conn_counted", False):
                self._conn_counted = True
                state.connections += 1

        def do_GET(self):
            self._count_connection()
            path = urllib.parse.urlparse(self.path).path
            if path == "/health":
                return self._send(200, {"ok": True})
            if path == "/metrics":
                if state.metrics_text is not None:
                    body = state.metrics_text.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                return self._send(404, {"error": "not found"})
            match = re.fullmatch(r"/api/sessions/([^/]+)/commits/([^/]+)", path)
            if match:
                if state.poll_http_status != 200:
                    return self._send(state.poll_http_status, {"error": "poll unavailable"})
                key = (match.group(1), match.group(2))
                state.poll_counts[key] = state.poll_counts.get(key, 0) + 1
                if state.poll_fail_after and state.poll_counts[key] > state.poll_fail_after:
                    return self._send(200, {"status": "failed", "error": "boom"})
                if state.always_pending or state.poll_counts[key] <= state.pending_attempts:
                    return self._send(200, {"status": "pending"})
                return self._send(200, {"status": "completed"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            self._count_connection()
            path = urllib.parse.urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                body = {}
            if path == "/api/sessions/open":
                if state.fail_open:
                    return self._send(500, {"error": "open boom"})
                return self._send(200, {"session_id": f"s{next(state.sessions)}"})
            if re.fullmatch(r"/api/sessions/([^/]+)/messages", path):
                if state.fail_add:
                    return self._send(500, {"error": "add boom"})
                content = str(body.get("content") or "")
                subject = re.search(r"第\d+批第\d+条事项", content)
                marker = re.search(r"PERFANCHOR-[A-Za-z0-9-]+", content)
                if subject and marker:
                    state.semantic_markers[(self.headers.get("X-Auth-Key", ""), subject.group(0))] = marker.group(0)
                return self._send(200, {"message_id": f"m{next(state.messages)}"})
            if re.fullmatch(r"/api/sessions/([^/]+)/commit", path):
                if state.fail_commit:
                    return self._send(500, {"error": "commit boom"})
                return self._send(state.commit_status, {} if state.commit_missing_archive else
                                  {"archive_id": f"a{next(state.archives)}"})
            if path == "/api/retrieval/search":
                query = body.get("query", "")
                state.search_queries.append(query)
                state.search_agent_ids.append(str(body.get("agent_id", "")))
                result: dict = {}
                if not state.search_empty:
                    auth_key = self.headers.get("X-Auth-Key", "")
                    recalled = next((marker for (key, subject), marker in state.semantic_markers.items()
                                     if key == auth_key and subject in query), query)
                    result["items"] = [{"text": f"recalled {recalled}"}]
                    result["explain"] = {"tokens": 1}
                if state.search_degraded:
                    result["status"] = "degraded"
                    result["degraded_reasons"] = ["saturated"]
                return self._send(200, {"result": result})
            if path == "/api/fail500":
                return self._send(500, {"error": "boom"})
            if path == "/api/fail404":
                return self._send(404, {"error": "missing"})
            return self._send(404, {"error": "not found"})

    return Handler


@pytest.fixture
def mock_server():
    started: list[http.server.ThreadingHTTPServer] = []

    def _start(state: MockState | None = None):
        state = state or MockState()
        # Default listen backlog (5) refuses connections when the full suite
        # saturates the GIL; raise it before construction so server_bind's
        # listen() uses it.
        http.server.ThreadingHTTPServer.request_queue_size = 128
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append(httpd)
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        return httpd, state, base_url

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def server(mock_server):
    httpd, state, base_url = mock_server()
    return httpd, state, base_url
