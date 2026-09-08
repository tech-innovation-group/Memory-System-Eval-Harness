import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from performance.ctx import Ctx, ConnectionRegistry
from performance.targets.echomem.probes.auth_preflight import key_fingerprint, run


def _probe_ctx(base_url: str, **params):
    checks = []
    ctx = Ctx(
        scene="auth_preflight",
        worker_id=0,
        tenant_idx=0,
        headers={},
        base_url=base_url,
        read_timeout_s=5,
        params=params,
        duration_s=0,
        stop=threading.Event(),
        record_fn=lambda r: None,
        seq_fn=lambda: 0,
        choose_fn=lambda items: None,
        phases=[],
        checks=checks,
        registry=ConnectionRegistry(),
    )
    return ctx, checks


def _transport_failed(checks) -> bool:
    # A per-tenant check that never reached the server reports http_status
    # null. Windows can abort a concurrent localhost connection
    # (WSAECONNABORTED 10053) while a ThreadingHTTPServer is under parallel
    # load; that is an environment flake, not a probe verdict.
    return any('"http_status": null' in check.detail for check in checks)


class AuthPreflightTests(unittest.TestCase):
    def test_key_fingerprint_is_fixed_length_and_not_plaintext(self):
        value = "test-secret-key"
        fingerprint = key_fingerprint(value)
        self.assertEqual(12, len(fingerprint))
        self.assertNotIn(value, fingerprint)

    def test_auth_preflight_records_real_http_status_without_secret(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                status = 200 if self.headers.get("X-Auth-Key") == "valid-key" else 401
                body = json.dumps(
                    {"status": "ok" if status == 200 else "invalid"}
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = Path(os.getenv("TMPDIR", "/tmp")) / (
            f"auth-preflight-test-{os.getpid()}.json"
        )
        try:
            config.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "ok", "auth_key_env": "OK_KEY"},
                            {"tenant_id": "bad", "auth_key_env": "BAD_KEY"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            old_values = {name: os.environ.get(name) for name in ("OK_KEY", "BAD_KEY")}
            os.environ["OK_KEY"] = "valid-key"
            os.environ["BAD_KEY"] = "invalid-key"
            try:
                checks = []
                for _attempt in range(3):
                    ctx, checks = _probe_ctx(
                        f"http://127.0.0.1:{server.server_port}",
                        tenant_config=str(config),
                        timeout_s=1,
                    )
                    run(ctx)
                    if not _transport_failed(checks):
                        break
            finally:
                for name, value in old_values.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
            by_name = {check.name: check for check in checks}
            self.assertEqual({"ok", "bad"}, set(by_name))
            self.assertEqual("PASS", by_name["ok"].status)
            self.assertEqual("FAIL", by_name["bad"].status)
            encoded = json.dumps([(check.reason, check.detail) for check in checks])
            self.assertNotIn("valid-key", encoded)
            self.assertNotIn("invalid-key", encoded)
        finally:
            config.unlink(missing_ok=True)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
