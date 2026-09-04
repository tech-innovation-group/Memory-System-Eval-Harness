from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.runtime_config import (
    apply_cli_runtime_overrides,
    prepare_runtime_environment,
    validate_real_model_config,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_cli_runtime_options_override_environment_for_preflight(self) -> None:
        with patch.dict(os.environ, {"ECHOMEM_BASE_URL": "http://old"}, clear=True):
            apply_cli_runtime_overrides([
                "--echomem-url=http://new",
                "--llm-base-url", "https://model.test/v1",
                "--llm-api-key", "secret",
            ])

            self.assertEqual("http://new", os.environ["ECHOMEM_BASE_URL"])
            self.assertEqual("https://model.test/v1", os.environ["LLM_BASE_URL"])
            self.assertEqual("secret", os.environ["LLM_API_KEY"])

    def test_discovers_workspace_model_and_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (root / ".env").write_text(
                f"ECHOMEM_WORKSPACE={workspace}\nECHOMEM_BASE_URL=http://127.0.0.1:9999\n",
                encoding="utf-8",
            )
            (workspace / "config.json").write_text(
                json.dumps({
                    "model": {
                        "llm": {
                            "api_base": "https://example.test/v1",
                            "model": "test-model",
                            "api_key": "model-secret",
                        }
                    }
                }),
                encoding="utf-8",
            )
            (workspace / ".echomem_http_auth_keys.json").write_text(
                json.dumps({"keys": [{"key": "ek_test"}]}),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                prepare_runtime_environment(root)
                self.assertEqual("https://example.test/v1", os.environ["LLM_BASE_URL"])
                self.assertEqual("test-model", os.environ["LLM_MODEL"])
                self.assertEqual("model-secret", os.environ["LLM_API_KEY"])
                self.assertEqual("ek_test", os.environ["ECHOMEM_AUTH_KEY"])
                self.assertEqual("model-secret", os.environ["JUDGE_TOKEN"])

    def test_rejects_fake_llm_and_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({
                    "model": {
                        "llm": {"provider": "fake", "model": "fake-llm"},
                        "embedding": {"provider": "fake", "model": "fake-embedding"},
                    }
                }),
                encoding="utf-8",
            )
            errors = validate_real_model_config(config_path)
            self.assertGreaterEqual(len(errors), 2)
            self.assertTrue(any("model.llm uses fake" in error for error in errors))
            self.assertTrue(any("model.embedding uses fake" in error for error in errors))

    def test_accepts_dashscope_real_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({
                    "model": {
                        "llm": {
                            "provider": "openai_compatible",
                            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                            "api_key_env": "DASHSCOPE_API_KEY",
                            "model": "deepseek-v4-flash-0731",
                        },
                        "embedding": {
                            "provider": "openai_compatible",
                            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                            "api_key_env": "DASHSCOPE_API_KEY",
                            "model": "text-embedding-v3",
                            "dimensions": 1024,
                        },
                    }
                }),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}, clear=True):
                self.assertEqual(
                    [],
                    validate_real_model_config(
                        config_path,
                        expected_embedding_dimensions=1024,
                    ),
                )

    def test_rejects_unexpected_embedding_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({
                    "model": {
                        "llm": {
                            "provider": "openai_compatible",
                            "api_base": "https://example.test/v1",
                            "model": "llm",
                        },
                        "embedding": {
                            "provider": "openai_compatible",
                            "api_base": "https://example.test/v1",
                            "model": "embedding",
                            "dimensions": 768,
                        },
                    }
                }),
                encoding="utf-8",
            )
            errors = validate_real_model_config(
                config_path,
                expected_embedding_dimensions=1024,
            )
            self.assertTrue(any("dimensions must be 1024" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
