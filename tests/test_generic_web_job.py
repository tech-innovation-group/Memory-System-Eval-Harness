from __future__ import annotations

import os
import unittest


os.environ.setdefault("SESSION_SECRET", "generic-test-secret")

try:
    import docker  # noqa: F401
except ModuleNotFoundError:
    docker = None

if docker is not None:
    from deploy.web_app_server import validate_generic_stress_config


def valid_config() -> dict:
    return {
        "target": {"name": "test-api", "base_url": "http://127.0.0.1:8080"},
        "requests": {
            "health": {
                "method": "GET",
                "path": "/health",
                "expected_status": 200,
            },
            "read": {
                "method": "GET",
                "path": "/items/{sequence}",
                "expected_status": 200,
            },
        },
        "healthcheck": {"request": "health"},
        "scenarios": [
            {
                "name": "smoke",
                "requests": ["read"],
                "total_requests": 2,
                "concurrency": 1,
            }
        ],
    }


@unittest.skipUnless(docker is not None, "Web service tests require the docker Python package")
class GenericWebJobValidationTests(unittest.TestCase):
    def test_accepts_real_http_config(self):
        self.assertEqual(valid_config(), validate_generic_stress_config(valid_config()))

    def test_rejects_non_http_target(self):
        config = valid_config()
        config["target"]["base_url"] = "file:///tmp/target"
        with self.assertRaisesRegex(ValueError, "http"):
            validate_generic_stress_config(config)

    def test_rejects_response_capture_on_server(self):
        config = valid_config()
        config["target"]["capture_response_body"] = True
        with self.assertRaisesRegex(ValueError, "响应正文"):
            validate_generic_stress_config(config)

    def test_rejects_missing_request_template(self):
        config = valid_config()
        config["scenarios"][0]["requests"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "不存在"):
            validate_generic_stress_config(config)

    def test_rejects_excessive_concurrency(self):
        config = valid_config()
        config["scenarios"][0]["concurrency"] = 257
        with self.assertRaisesRegex(ValueError, "并发"):
            validate_generic_stress_config(config)
