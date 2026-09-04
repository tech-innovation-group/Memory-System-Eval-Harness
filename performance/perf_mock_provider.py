"""Fault-injection mock provider for stress runs (scene F).

The mock stands in for the server's external LLM/embedding HTTP endpoint so
faults can be injected without modifying the server: point the engine's
``api_base`` at this mock and toggle behaviors. Behaviors:

  ok          -> HTTP 200 with a fixed success payload
  error500    -> HTTP 500
  hang        -> never respond (blocks past the client timeout)
  rate_limit  -> HTTP 429 with a ``Retry-After`` header
  restore     -> back to ok

The fault sequence runner probes the mock per stage and classifies each
outcome (ok / http_4xx / http_5xx / timeout / connection), producing the
"controllable fault semantics" evidence reported separately from real
capacity measurements.

CLI: ``python perf_mock_provider.py --port 18090`` starts a server whose
behavior is switched via ``set_behavior`` (library use) only; the CLI serves
as a live target for an EchoMem engine whose ``api_base`` points at it.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

OK_PAYLOAD = {"choices": [{"message": {"content": "ok"}}], "object": "chat.completion"}

BEHAVIORS = ("ok", "error500", "hang", "rate_limit", "restore")

DEFAULT_FAULT_STAGES: list[dict[str, Any]] = [
    {"stage": "baseline", "behavior": "ok", "expected_error_type": "", "requests": 5},
    {"stage": "half-500", "behavior": "error500", "expected_error_type": "http_5xx", "requests": 5},
    {"stage": "hang", "behavior": "hang", "expected_error_type": "timeout", "requests": 2},
    {"stage": "recover", "behavior": "restore", "expected_error_type": "", "requests": 3, "recovered": True},
    {"stage": "rate-limit", "behavior": "rate_limit", "expected_error_type": "http_4xx", "requests": 5},
    {"stage": "recover-2", "behavior": "restore", "expected_error_type": "", "requests": 3, "recovered": True},
]


class _MockHandler(BaseHTTPRequestHandler):
    """Serves any path per the provider's current behavior."""

    def _respond(self) -> None:
        provider = self.server.provider  # type: ignore[attr-defined]
        behavior = provider.behavior
        if behavior in ("ok", "restore"):
            body = json.dumps(OK_PAYLOAD).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif behavior == "error500":
            self.send_error(500, "injected 500")
        elif behavior == "rate_limit":
            self.send_response(429)
            self.send_header("Retry-After", str(provider.retry_after_s))
            body = b'{"error": {"message": "rate limited", "type": "rate_limit"}}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:  # hang: block past the client timeout
            time.sleep(provider.hang_s)
            body = b'{"choices": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        self._respond()

    def do_GET(self) -> None:  # noqa: N802
        self._respond()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # keep the fault sequence output clean


class MockProvider:
    """Local HTTP endpoint whose behavior is switchable for fault injection."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18090,
        *,
        hang_s: float = 30.0,
        retry_after_s: int = 1,
    ) -> None:
        self.host = host
        self.port = port
        self.hang_s = hang_s
        self.retry_after_s = retry_after_s
        self.behavior = "ok"
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self.host, self.port), _MockHandler)
        if self.port == 0:
            self.port = int(httpd.server_address[1])
        httpd.provider = self  # type: ignore[attr-defined]
        self._httpd = httpd
        import threading

        self._thread = threading.Thread(
            target=httpd.serve_forever, name="perf-mock-provider", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def set_behavior(self, behavior: str) -> None:
        if behavior not in BEHAVIORS:
            raise ValueError(f"unknown behavior '{behavior}' (可选 {', '.join(BEHAVIORS)})")
        self.behavior = behavior


def probe(
    url: str,
    *,
    method: str = "POST",
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """One request against the mock; classifies the outcome.

    Returns status/error_type/code/retry_after/elapsed_s. Timeouts and
    connection errors are classified separately from HTTP errors.
    """
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"model": "mock", "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read()
        return {
            "status": "ok",
            "error_type": "",
            "code": 200,
            "retry_after": "",
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    except urllib.error.HTTPError as exc:
        retry_after = ""
        if exc.headers is not None:
            retry_after = str(exc.headers.get("Retry-After", "") or "")
        return {
            "status": "error",
            "error_type": "http_4xx" if exc.code < 500 else "http_5xx",
            "code": exc.code,
            "retry_after": retry_after,
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        error_type = "timeout" if isinstance(reason, TimeoutError) else "connection"
        return {
            "status": "error",
            "error_type": error_type,
            "code": None,
            "retry_after": "",
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    except (TimeoutError, OSError) as exc:
        return {
            "status": "error",
            "error_type": "timeout",
            "code": None,
            "retry_after": "",
            "elapsed_s": round(time.monotonic() - started, 3),
        }


def run_fault_sequence(
    provider: MockProvider,
    *,
    stages: list[dict[str, Any]] | None = None,
    timeout_s: float = 5.0,
) -> list[dict[str, Any]]:
    """Execute the fault stage sequence against the mock.

    Each stage switches the behavior, probes it ``requests`` times and
    records the observed error type. ``recovered`` marks stages that must
    show a successful request after a fault.
    """
    results: list[dict[str, Any]] = []
    for stage in stages or DEFAULT_FAULT_STAGES:
        provider.set_behavior(stage["behavior"])
        outcomes = [probe(provider.url, timeout_s=timeout_s) for _ in range(stage["requests"])]
        error_types = [outcome["error_type"] for outcome in outcomes]
        observed = _majority(error_types)
        results.append(
            {
                "stage": stage["stage"],
                "behavior": stage["behavior"],
                "expected_error_type": stage["expected_error_type"],
                "observed_error_type": observed,
                "requests": len(outcomes),
                "hang": stage["behavior"] == "hang",
                "recovered": bool(stage.get("recovered", False)),
                "outcomes": outcomes,
            }
        )
    return results


def _majority(values: list[str]) -> str:
    """Most frequent value; ties break toward the first occurrence."""
    if not values:
        return ""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    best, best_count = "", -1
    for value in values:
        if counts[value] > best_count:
            best, best_count = value, counts[value]
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="EchoMem fault-injection mock provider")
    parser.add_argument("--port", type=int, default=18090)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--hang-s", type=float, default=30.0)
    parser.add_argument("--retry-after-s", type=int, default=1)
    args = parser.parse_args()

    provider = MockProvider(
        args.host, args.port, hang_s=args.hang_s, retry_after_s=args.retry_after_s
    )
    provider.start()
    print(f"mock provider listening on {provider.url} (behavior={provider.behavior})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        provider.stop()


if __name__ == "__main__":
    main()
