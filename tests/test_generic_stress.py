from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from stress.generic.runner import (
    check_assertions,
    json_path,
    load_config,
    main,
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
        elif self.path.startswith("/api/items/"):
            body = b'{"id":"item-1"}'
            self.send_response(200)
        else:
            body = b'{"error":"missing"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class GenericStressTests(unittest.TestCase):
    def test_json_path_and_assertions(self):
        payload = {"status": "ok", "items": [{"id": 3}]}
        self.assertEqual(3, json_path(payload, "$.items[0].id"))
        self.assertEqual([], check_assertions(payload, [{"path": "$.status", "equals": "ok"}]))
        self.assertTrue(check_assertions(payload, [{"path": "$.status", "equals": "bad"}]))

    def test_config_requires_target_and_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps({"target": {"base_url": "http://x"}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_real_http_smoke_writes_artifacts(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                config = {
                    "target": {"base_url": f"http://127.0.0.1:{server.server_port}"},
                    "requests": {
                        "health": {"path": "/health", "assertions": [{"path": "$.status", "equals": "ok"}]},
                        "read": {"path": "/api/items/1", "assertions": [{"path": "$.id", "exists": True}]},
                    },
                    "healthcheck": {"request": "health"},
                    "scenarios": [{"name": "smoke", "requests": ["read"], "total_requests": 3, "concurrency": 2}],
                }
                config_path = Path(directory) / "config.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                self.assertEqual(0, main(["--config", str(config_path), "--out-dir", directory]))
                self.assertTrue((Path(directory) / "summary.json").exists())
                self.assertTrue((Path(directory) / "requests.csv").exists())
                self.assertTrue((Path(directory) / "report.html").exists())
        finally:
            server.shutdown()
            thread.join(timeout=2)
