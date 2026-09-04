"""Unit tests for backends/memory_types.py.

Covers CommitResult, SearchResult, MemoryClient Protocol, BaseHTTPMemoryClient
(retry/deadline/poll-commit template method), and NullMemoryClient.

Uses unittest.TestCase with mocked HTTP (no real servers, no network).
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from backends.memory_types import (
    BaseHTTPMemoryClient,
    CommitResult,
    MemoryClient,
    NullMemoryClient,
    SearchResult,
)


# ------------------------------------------------------------------ #
#  Test helpers                                                       #
# ------------------------------------------------------------------ #

class _ConcreteClient(BaseHTTPMemoryClient):
    """Minimal concrete subclass so the abstract base can be instantiated."""

    def _headers(self) -> dict[str, str]:
        return {}


def _fake_response(body: bytes) -> MagicMock:
    """Return a mock behaving like an http.client.HTTPResponse context manager."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    """Create a real HTTPError with *code* and optional response *body*."""
    return urllib.error.HTTPError(
        "http://test", code, "Error", {}, io.BytesIO(body),
    )


# ------------------------------------------------------------------ #
#  CommitResult                                                       #
# ------------------------------------------------------------------ #

class CommitResultTests(unittest.TestCase):
    def test_construction_with_all_fields(self) -> None:
        cr = CommitResult("s1", "a1", "completed", 1.5, 3, error="none")
        self.assertEqual("s1", cr.session_id)
        self.assertEqual("a1", cr.archive_id)
        self.assertEqual("completed", cr.status)
        self.assertEqual(1.5, cr.elapsed_s)
        self.assertEqual(3, cr.polls)
        self.assertEqual("none", cr.error)

    def test_default_error_is_empty_string(self) -> None:
        cr = CommitResult("s1", "a1", "completed", 0.0, 0)
        self.assertEqual("", cr.error)


# ------------------------------------------------------------------ #
#  SearchResult                                                       #
# ------------------------------------------------------------------ #

class SearchResultTests(unittest.TestCase):
    # -- from_dict: uri fallback chain --------------------------------

    def test_from_dict_uri_fallback_chain(self) -> None:
        cases = [
            ({"uri": "u"}, "u"),
            ({"evidence_uri": "eu"}, "eu"),
            ({"source_uri": "su"}, "su"),
            ({"path": "/p"}, "/p"),
            ({"id": "42"}, "42"),
            ({}, ""),
            # empty string falls through to next key
            ({"uri": "", "evidence_uri": "eu"}, "eu"),
            # first non-empty key wins
            ({"uri": "u", "id": "42"}, "u"),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(expected, SearchResult.from_dict(data).uri)

    # -- from_dict: score ---------------------------------------------

    def test_from_dict_score_float_conversion(self) -> None:
        for value, expected in [("0.95", 0.95), (1, 1.0), (0.5, 0.5), ("3", 3.0)]:
            with self.subTest(value=value):
                self.assertEqual(expected, SearchResult.from_dict({"score": value}).score)

    def test_from_dict_score_defaults_to_zero(self) -> None:
        self.assertEqual(0.0, SearchResult.from_dict({}).score)

    # -- from_dict: content fallback chain ----------------------------

    def test_from_dict_content_fallback_chain(self) -> None:
        cases = [
            ({"content": "c"}, "c"),
            ({"text": "t"}, "t"),
            ({"preview": "p"}, "p"),
            ({"abstract": "a"}, "a"),
            ({"overview": "o"}, "o"),
            ({"summary": "s"}, "s"),
            ({}, ""),
            ({"content": "", "text": "t"}, "t"),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(expected, SearchResult.from_dict(data).content)

    # -- from_dict: memory_type fallback chain ------------------------

    def test_from_dict_memory_type_fallback_chain(self) -> None:
        cases = [
            ({"memory_type": "mt"}, "mt"),
            ({"type": "t"}, "t"),
            ({"kind": "k"}, "k"),
            ({}, ""),
            ({"memory_type": "", "type": "t"}, "t"),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(expected, SearchResult.from_dict(data).memory_type)

    # -- from_dict: metadata ------------------------------------------

    def test_from_dict_metadata_stores_full_original_dict(self) -> None:
        data = {"uri": "u", "score": 0.9, "custom": "x", "nested": {"k": "v"}}
        sr = SearchResult.from_dict(data)
        self.assertEqual(data, sr.metadata)
        # metadata is a copy, not the same object
        self.assertIsNot(data, sr.metadata)

    # -- to_dict -------------------------------------------------------

    def test_to_dict_preserves_metadata_and_overrides_normalized(self) -> None:
        sr = SearchResult.from_dict({
            "uri": "echo://1",
            "score": "0.95",
            "content": "hello",
            "memory_type": "episode",
            "extra": "preserved",
        })
        d = sr.to_dict()
        self.assertEqual("echo://1", d["uri"])
        self.assertEqual(0.95, d["score"])
        self.assertEqual("hello", d["content"])
        self.assertEqual("episode", d["memory_type"])
        # original metadata key preserved
        self.assertEqual("preserved", d["extra"])
        # normalized score (float) overrides original string score
        self.assertIsInstance(d["score"], float)

    def test_to_dict_includes_all_metadata_keys(self) -> None:
        data = {"uri": "u", "custom_a": 1, "custom_b": [1, 2]}
        sr = SearchResult.from_dict(data)
        d = sr.to_dict()
        self.assertIn("custom_a", d)
        self.assertIn("custom_b", d)

    # -- round-trip ----------------------------------------------------

    def test_round_trip_preserves_key_data(self) -> None:
        original = {
            "uri": "echo://mem/1",
            "score": 0.9,
            "content": "hello world",
            "memory_type": "atom",
            "custom": "data",
        }
        sr = SearchResult.from_dict(original)
        result = sr.to_dict()
        self.assertEqual("echo://mem/1", result["uri"])
        self.assertEqual(0.9, result["score"])
        self.assertEqual("hello world", result["content"])
        self.assertEqual("atom", result["memory_type"])
        self.assertEqual("data", result["custom"])


# ------------------------------------------------------------------ #
#  MemoryClient Protocol                                              #
# ------------------------------------------------------------------ #

class MemoryClientProtocolTests(unittest.TestCase):
    def test_null_client_has_protocol_methods(self) -> None:
        client = NullMemoryClient()
        for method in ("search", "fs_read", "fs_list", "fs_glob"):
            with self.subTest(method=method):
                self.assertTrue(hasattr(client, method))
                self.assertTrue(callable(getattr(client, method)))

    def test_memory_client_protocol_has_expected_methods(self) -> None:
        for method in ("search", "fs_read", "fs_list", "fs_glob"):
            with self.subTest(method=method):
                self.assertTrue(hasattr(MemoryClient, method))


# ------------------------------------------------------------------ #
#  BaseHTTPMemoryClient.__init__                                      #
# ------------------------------------------------------------------ #

class BaseHTTPMemoryClientInitTests(unittest.TestCase):
    def test_base_url_rstrips_trailing_slash(self) -> None:
        self.assertEqual("http://test", _ConcreteClient("http://test/").base_url)

    def test_base_url_rstrips_multiple_trailing_slashes(self) -> None:
        self.assertEqual("http://test", _ConcreteClient("http://test///").base_url)

    def test_base_url_without_trailing_slash_unchanged(self) -> None:
        self.assertEqual("http://test", _ConcreteClient("http://test").base_url)

    def test_stores_all_parameters(self) -> None:
        client = _ConcreteClient(
            "http://test/",
            account="acc",
            user_id="uid",
            agent_id="aid",
            workspace="ws",
            timeout_s=30.0,
            max_retries=5,
            retry_backoff_s=0.5,
        )
        self.assertEqual("acc", client.account)
        self.assertEqual("uid", client.user_id)
        self.assertEqual("aid", client.agent_id)
        self.assertEqual("ws", client.workspace)
        self.assertEqual(30.0, client.timeout_s)
        self.assertEqual(5, client.max_retries)
        self.assertEqual(0.5, client.retry_backoff_s)

    def test_default_parameters(self) -> None:
        client = _ConcreteClient("http://test")
        self.assertEqual("default", client.account)
        self.assertEqual("default", client.user_id)
        self.assertEqual("default", client.agent_id)
        self.assertEqual("", client.workspace)
        self.assertEqual(60.0, client.timeout_s)
        self.assertEqual(3, client.max_retries)
        self.assertEqual(1.0, client.retry_backoff_s)


# ------------------------------------------------------------------ #
#  BaseHTTPMemoryClient._do_request                                   #
# ------------------------------------------------------------------ #

class BaseHTTPMemoryClientDoRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _ConcreteClient("http://test")
        self.req = urllib.request.Request("http://test/api", method="GET")

    def test_success_on_first_attempt(self) -> None:
        resp = _fake_response(b'{"key": "value"}')
        resp.status = 200
        with patch("urllib.request.urlopen", return_value=resp):
            result = self.client._do_request(self.req)
        self.assertEqual({"key": "value"}, result)

    def test_success_response_is_written_to_http_trace(self) -> None:
        resp = _fake_response(b'{"commit": {"status": "pending"}}')
        resp.status = 202
        with tempfile.TemporaryDirectory() as trace_dir, patch.dict(
            os.environ, {"ECHOMEM_HTTP_TRACE_DIR": trace_dir}
        ), patch("urllib.request.urlopen", return_value=resp):
            self.client._do_request(self.req)
            trace_path = os.path.join(trace_dir, "echomem_http_trace.jsonl")
            with open(trace_path, encoding="utf-8") as handle:
                record = json.loads(handle.readline())
        self.assertEqual("GET", record["method"])
        self.assertEqual(202, record["status"])
        self.assertEqual('{"commit": {"status": "pending"}}', record["response_body"])

    def test_http_error_response_is_written_to_http_trace(self) -> None:
        error = _http_error(401, b'{"error":"invalid_api_key"}')
        with tempfile.TemporaryDirectory() as trace_dir, patch.dict(
            os.environ, {"ECHOMEM_HTTP_TRACE_DIR": trace_dir}
        ), patch("urllib.request.urlopen", side_effect=error), patch("time.sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                self.client._do_request(self.req)
            trace_path = os.path.join(trace_dir, "echomem_http_trace.jsonl")
            with open(trace_path, encoding="utf-8") as handle:
                record = json.loads(handle.readline())
        self.assertEqual(401, record["status"])
        self.assertEqual('{"error":"invalid_api_key"}', record["response_body"])
        self.assertEqual("HTTPError", record["error"])

    def test_empty_response_body_returns_empty_dict(self) -> None:
        resp = _fake_response(b"")
        with patch("urllib.request.urlopen", return_value=resp):
            result = self.client._do_request(self.req)
        self.assertEqual({}, result)

    def test_5xx_error_retries_then_succeeds(self) -> None:
        err = _http_error(500, b"server error")
        resp = _fake_response(b'{"ok": true}')
        mock_open = MagicMock(side_effect=[err, resp])
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            result = self.client._do_request(self.req)
        self.assertEqual({"ok": True}, result)
        self.assertEqual(2, mock_open.call_count)

    def test_503_error_retries_then_succeeds(self) -> None:
        err = _http_error(503, b"unavailable")
        resp = _fake_response(b'{"ok": true}')
        mock_open = MagicMock(side_effect=[err, resp])
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            result = self.client._do_request(self.req)
        self.assertEqual({"ok": True}, result)

    def test_4xx_error_does_not_retry(self) -> None:
        err = _http_error(404, b"not found")
        mock_open = MagicMock(side_effect=err)
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.client._do_request(self.req)
        self.assertEqual(404, ctx.exception.code)
        self.assertEqual(1, mock_open.call_count)

    def test_400_error_does_not_retry(self) -> None:
        err = _http_error(400, b"bad request")
        mock_open = MagicMock(side_effect=err)
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.client._do_request(self.req)
        self.assertEqual(400, ctx.exception.code)
        self.assertEqual(1, mock_open.call_count)

    def test_network_error_retries_then_succeeds(self) -> None:
        err = urllib.error.URLError("connection refused")
        resp = _fake_response(b'{"ok": true}')
        mock_open = MagicMock(side_effect=[err, resp])
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            result = self.client._do_request(self.req)
        self.assertEqual({"ok": True}, result)
        self.assertEqual(2, mock_open.call_count)

    def test_deadline_exceeded_stops_retrying(self) -> None:
        err = urllib.error.URLError("refused")
        mock_open = MagicMock(side_effect=err)
        client = _ConcreteClient("http://test", timeout_s=1.0, max_retries=5)
        # monotonic calls: deadline calc(0), remaining-in-try(0), remaining-in-except(100)
        with (
            patch("urllib.request.urlopen", mock_open),
            patch("time.monotonic", side_effect=[0.0, 0.0, 100.0]),
            patch("time.sleep"),
        ):
            with self.assertRaises(TimeoutError):
                client._do_request(self.req)
        self.assertEqual(1, mock_open.call_count)

    def test_deadline_exceeded_during_5xx_retry(self) -> None:
        err = _http_error(500, b"err")
        mock_open = MagicMock(side_effect=err)
        client = _ConcreteClient("http://test", timeout_s=1.0, max_retries=5)
        with (
            patch("urllib.request.urlopen", mock_open),
            patch("time.monotonic", side_effect=[0.0, 0.0, 100.0]),
            patch("time.sleep"),
        ):
            with self.assertRaises(TimeoutError):
                client._do_request(self.req)
        self.assertEqual(1, mock_open.call_count)

    def test_max_retries_exhausted_raises_last_error(self) -> None:
        err = urllib.error.URLError("refused")
        mock_open = MagicMock(side_effect=err)
        client = _ConcreteClient("http://test", max_retries=2, retry_backoff_s=0.01)
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            with self.assertRaises(urllib.error.URLError):
                client._do_request(self.req)
        self.assertEqual(2, mock_open.call_count)

    def test_max_retries_zero_raises_runtime_error(self) -> None:
        err = urllib.error.URLError("refused")
        mock_open = MagicMock(side_effect=err)
        client = _ConcreteClient("http://test", max_retries=0)
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                client._do_request(self.req)
        self.assertEqual(0, mock_open.call_count)

    def test_5xx_exhausted_re_raises_http_error(self) -> None:
        err = _http_error(500, b"err")
        mock_open = MagicMock(side_effect=err)
        client = _ConcreteClient("http://test", max_retries=2, retry_backoff_s=0.01)
        with patch("urllib.request.urlopen", mock_open), patch("time.sleep"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                client._do_request(self.req)
        self.assertEqual(500, ctx.exception.code)
        self.assertEqual(2, mock_open.call_count)


# ------------------------------------------------------------------ #
#  BaseHTTPMemoryClient.poll_commit                                   #
# ------------------------------------------------------------------ #

class BaseHTTPMemoryClientPollCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _ConcreteClient("http://test")

    # -- empty archive_id ---------------------------------------------

    def test_empty_archive_id_returns_failed(self) -> None:
        result = self.client.poll_commit("s1", "", timeout_s=5.0)
        self.assertEqual("failed", result.status)
        self.assertIn("empty archive_id", result.error)
        self.assertEqual(0, result.polls)
        self.assertEqual("s1", result.session_id)
        self.assertEqual("", result.archive_id)

    # -- successful poll -----------------------------------------------

    def test_successful_poll_returns_completed(self) -> None:
        self.client._fetch_commit_status = lambda sid, aid: {"status": "completed"}  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.polls)
        self.assertEqual("", result.error)

    def test_done_status_treated_as_completed(self) -> None:
        self.client._fetch_commit_status = lambda sid, aid: {"status": "done"}  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)

    def test_success_status_treated_as_completed(self) -> None:
        self.client._fetch_commit_status = lambda sid, aid: {"status": "success"}  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)

    def test_completed_status_is_case_insensitive(self) -> None:
        self.client._fetch_commit_status = lambda sid, aid: {"status": "COMPLETED"}  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)

    # -- failed status -------------------------------------------------

    def test_failed_status_returns_failed(self) -> None:
        self.client._fetch_commit_status = lambda sid, aid: {"status": "failed", "error": "boom"}  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("failed", result.status)
        self.assertEqual("boom", result.error)
        self.assertEqual(1, result.polls)

    def test_error_status_treated_as_failed(self) -> None:
        self.client._fetch_commit_status = lambda sid, aid: {"status": "error"}  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("failed", result.status)
        # no "error" key in resp -> fallback to status string
        self.assertEqual("error", result.error)

    def test_failed_status_uses_extract_commit_error(self) -> None:
        resp = {"status": "failed", "error": "extraction failed"}
        self.client._fetch_commit_status = lambda sid, aid: resp  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("extraction failed", result.error)

    # -- timeout -------------------------------------------------------

    def test_timeout_returns_timeout_status(self) -> None:
        self.client._fetch_commit_status = lambda sid, aid: {"status": "pending"}  # type: ignore[method-assign]
        # monotonic: start(0), elapsed-iter1(0), elapsed-iter2(100)
        with patch("time.monotonic", side_effect=[0.0, 0.0, 100.0]), patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("timeout", result.status)
        self.assertEqual(2, result.polls)
        self.assertEqual("s1", result.session_id)
        self.assertEqual("a1", result.archive_id)

    # -- 4xx HTTPError terminal failure --------------------------------

    def test_4xx_httperror_is_terminal_failure(self) -> None:
        err = _http_error(404)
        self.client._fetch_commit_status = MagicMock(side_effect=err)  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("failed", result.status)
        self.assertIn("HTTP 404", result.error)
        self.assertEqual(1, result.polls)

    def test_403_httperror_is_terminal_failure(self) -> None:
        err = _http_error(403)
        self.client._fetch_commit_status = MagicMock(side_effect=err)  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("failed", result.status)
        self.assertIn("HTTP 403", result.error)

    # -- retriable 4xx HTTPError (408/409/425/429) ---------------------

    def test_409_httperror_is_retried(self) -> None:
        err = _http_error(409)
        calls: list[str] = []

        def fake_fetch(sid: str, aid: str) -> dict:
            calls.append(aid)
            if len(calls) == 1:
                raise err
            return {"status": "completed"}

        self.client._fetch_commit_status = fake_fetch  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)
        self.assertEqual(2, result.polls)

    def test_429_httperror_is_retried(self) -> None:
        err = _http_error(429)
        calls: list[int] = []

        def fake_fetch(sid: str, aid: str) -> dict:
            calls.append(1)
            if len(calls) == 1:
                raise err
            return {"status": "completed"}

        self.client._fetch_commit_status = fake_fetch  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)
        self.assertEqual(2, result.polls)

    # -- generic exception is retried ----------------------------------

    def test_generic_exception_is_retried(self) -> None:
        calls: list[int] = []

        def fake_fetch(sid: str, aid: str) -> dict:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient")
            return {"status": "completed"}

        self.client._fetch_commit_status = fake_fetch  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)
        self.assertEqual(2, result.polls)

    # -- timeout_s=0 waits indefinitely --------------------------------

    def test_timeout_zero_waits_indefinitely(self) -> None:
        calls: list[int] = []

        def fake_fetch(sid: str, aid: str) -> dict:
            calls.append(1)
            if len(calls) >= 3:
                return {"status": "completed"}
            return {"status": "pending"}

        self.client._fetch_commit_status = fake_fetch  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)
        self.assertEqual(3, result.polls)

    # -- poll counting --------------------------------------------------

    def test_poll_counting_pending_then_completed(self) -> None:
        calls: list[int] = []

        def fake_fetch(sid: str, aid: str) -> dict:
            calls.append(1)
            if len(calls) >= 4:
                return {"status": "completed"}
            return {"status": "processing"}

        self.client._fetch_commit_status = fake_fetch  # type: ignore[method-assign]
        with patch("time.sleep"):
            result = self.client.poll_commit("s1", "a1", timeout_s=0, poll_interval_s=0.01)
        self.assertEqual("completed", result.status)
        self.assertEqual(4, result.polls)
        self.assertEqual(4, len(calls))


# ------------------------------------------------------------------ #
#  BaseHTTPMemoryClient hook methods                                  #
# ------------------------------------------------------------------ #

class BaseHTTPMemoryClientHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _ConcreteClient("http://test")

    # -- _parse_commit_status ------------------------------------------

    def test_parse_commit_status_reads_status_key(self) -> None:
        self.assertEqual("completed", self.client._parse_commit_status({"status": "completed"}))

    def test_parse_commit_status_reads_stage_key(self) -> None:
        self.assertEqual("processing", self.client._parse_commit_status({"stage": "processing"}))

    def test_parse_commit_status_reads_state_key(self) -> None:
        self.assertEqual("running", self.client._parse_commit_status({"state": "running"}))

    def test_parse_commit_status_lowercases(self) -> None:
        self.assertEqual("completed", self.client._parse_commit_status({"status": "COMPLETED"}))

    def test_parse_commit_status_empty_when_no_keys(self) -> None:
        self.assertEqual("", self.client._parse_commit_status({}))

    def test_parse_commit_status_status_takes_priority_over_stage(self) -> None:
        resp = {"status": "completed", "stage": "processing"}
        self.assertEqual("completed", self.client._parse_commit_status(resp))

    # -- _commit_failed_statuses ---------------------------------------

    def test_commit_failed_statuses_returns_failed_and_error(self) -> None:
        self.assertEqual(("failed", "error"), self.client._commit_failed_statuses())

    # -- _extract_commit_error -----------------------------------------

    def test_extract_commit_error_reads_error_key(self) -> None:
        self.assertEqual("boom", self.client._extract_commit_error({"error": "boom"}, "failed"))

    def test_extract_commit_error_fallback_to_status(self) -> None:
        self.assertEqual("failed", self.client._extract_commit_error({}, "failed"))

    def test_extract_commit_error_fallback_when_error_empty(self) -> None:
        # resp.get("error", status) -> "" is falsy but get returns "" not default
        resp = {"error": ""}
        self.assertEqual("", self.client._extract_commit_error(resp, "failed"))


# ------------------------------------------------------------------ #
#  BaseHTTPMemoryClient context manager                               #
# ------------------------------------------------------------------ #

class BaseHTTPMemoryClientContextManagerTests(unittest.TestCase):
    def test_close_is_noop(self) -> None:
        client = _ConcreteClient("http://test")
        client.close()  # must not raise

    def test_enter_returns_self(self) -> None:
        client = _ConcreteClient("http://test")
        with client as ctx:
            self.assertIs(client, ctx)

    def test_exit_calls_close(self) -> None:
        client = _ConcreteClient("http://test")
        client.close = MagicMock()  # type: ignore[method-assign]
        client.__exit__(None, None, None)
        client.close.assert_called_once()

    def test_context_manager_closes_on_exit(self) -> None:
        client = _ConcreteClient("http://test")
        close_called = []
        original_close = client.close

        def tracking_close() -> None:
            close_called.append(True)
            original_close()

        client.close = tracking_close  # type: ignore[method-assign]
        with client:
            pass
        self.assertEqual([True], close_called)


# ------------------------------------------------------------------ #
#  NullMemoryClient                                                   #
# ------------------------------------------------------------------ #

class NullMemoryClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = NullMemoryClient()

    def test_health_returns_ok(self) -> None:
        self.assertEqual({"status": "ok"}, self.client.health())

    def test_open_session_returns_empty(self) -> None:
        self.assertEqual("", self.client.open_session("title"))

    def test_open_session_no_args(self) -> None:
        self.assertEqual("", self.client.open_session())

    def test_add_message_returns_empty_dict(self) -> None:
        self.assertEqual({}, self.client.add_message("s1", "user", "hello"))

    def test_add_message_accepts_arbitrary_kwargs(self) -> None:
        result = self.client.add_message("s1", "user", "hello", extra="val", num=42)
        self.assertEqual({}, result)

    def test_commit_session_returns_empty(self) -> None:
        self.assertEqual("", self.client.commit_session("s1"))

    def test_commit_session_with_keep_recent_count(self) -> None:
        self.assertEqual("", self.client.commit_session("s1", keep_recent_count=5))

    def test_poll_commit_returns_completed(self) -> None:
        result = self.client.poll_commit("s1", "a1")
        self.assertIsInstance(result, CommitResult)
        self.assertEqual("completed", result.status)
        self.assertEqual(0, result.polls)
        self.assertEqual("s1", result.session_id)
        self.assertEqual("a1", result.archive_id)

    def test_has_archives_returns_false(self) -> None:
        self.assertFalse(self.client.has_archives("s1"))

    def test_search_returns_empty_list(self) -> None:
        self.assertEqual([], self.client.search("query"))

    def test_search_with_all_params(self) -> None:
        result = self.client.search("q", top_k=5, session_id="s", agent_id="a", timeout_s=1.0)
        self.assertEqual([], result)

    def test_fs_read_returns_empty_string(self) -> None:
        self.assertEqual("", self.client.fs_read("echo://mem/1"))

    def test_fs_list_returns_empty_list(self) -> None:
        self.assertEqual([], self.client.fs_list("echo://mem/"))

    def test_fs_list_recursive_returns_empty_list(self) -> None:
        self.assertEqual([], self.client.fs_list("echo://mem/", recursive=True))

    def test_fs_glob_returns_empty_list(self) -> None:
        self.assertEqual([], self.client.fs_glob("echo://mem/*.md"))

    def test_close_is_noop(self) -> None:
        self.client.close()  # must not raise

    def test_context_manager_support(self) -> None:
        with NullMemoryClient() as client:
            self.assertIsInstance(client, NullMemoryClient)
            # still functional inside the context
            self.assertEqual([], client.search("q"))

    def test_context_manager_exit_does_not_raise(self) -> None:
        client = NullMemoryClient()
        client.__exit__(None, None, None)  # must not raise

    def test_default_class_attributes(self) -> None:
        self.assertEqual("default", NullMemoryClient.account)
        self.assertEqual("default", NullMemoryClient.user_id)
        self.assertEqual("default", NullMemoryClient.agent_id)
        self.assertEqual("", NullMemoryClient.auth_key)


# ------------------------------------------------------------------ #
#  Backend design intent (README)                                     #
# ------------------------------------------------------------------ #

class BackendDesignIntentTests(unittest.TestCase):
    def test_echomem_client_imports_from_memory_types(self) -> None:
        from backends.echomem.client import EchoMemClient
        self.assertTrue(issubclass(EchoMemClient, BaseHTTPMemoryClient))

    def test_openviking_client_imports_from_memory_types(self) -> None:
        from backends.openviking.client import OpenVikingClient
        self.assertTrue(issubclass(OpenVikingClient, BaseHTTPMemoryClient))

    def test_all_clients_implement_memory_client_protocol(self) -> None:
        from backends.echomem.client import EchoMemClient
        from backends.openviking.client import OpenVikingClient
        for cls in (EchoMemClient, OpenVikingClient, NullMemoryClient):
            for method in ("search", "fs_read", "fs_list", "fs_glob"):
                with self.subTest(cls=cls.__name__, method=method):
                    self.assertTrue(hasattr(cls, method))

    def test_base_http_provides_template_method(self) -> None:
        """poll_commit delegates to _fetch_commit_status (template method pattern)."""
        client = _ConcreteClient("http://test")
        calls: list[tuple[str, str]] = []

        def fake_fetch(sid: str, aid: str) -> dict:
            calls.append((sid, aid))
            return {"status": "completed"}

        client._fetch_commit_status = fake_fetch  # type: ignore[method-assign]
        with patch("time.sleep"):
            client.poll_commit("s1", "a1", timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual([("s1", "a1")], calls)

    def test_null_client_for_unsupported_plugins(self) -> None:
        """NullMemoryClient provides no-op implementations so plugin code
        can call memory_client.search(...) etc. without conditional branches."""
        client = NullMemoryClient()
        self.assertEqual([], client.search("q"))
        self.assertEqual("", client.fs_read("uri"))
        self.assertEqual([], client.fs_list("uri"))
        self.assertEqual([], client.fs_glob("pat"))

    def test_shared_base_avoids_code_duplication(self) -> None:
        """Both concrete clients inherit _do_request and poll_commit from the base."""
        from backends.echomem.client import EchoMemClient
        from backends.openviking.client import OpenVikingClient
        for cls in (EchoMemClient, OpenVikingClient):
            with self.subTest(cls=cls.__name__):
                # poll_commit and _do_request are inherited, not overridden
                self.assertIs(BaseHTTPMemoryClient.poll_commit, cls.poll_commit)
                self.assertIs(BaseHTTPMemoryClient._do_request, cls._do_request)


if __name__ == "__main__":
    unittest.main()
