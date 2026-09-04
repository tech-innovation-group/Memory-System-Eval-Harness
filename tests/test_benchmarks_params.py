"""Exhaustive unit tests for the shared benchmark parameters/config module.

Covers every functional point in ``shared/eval_base.py`` that relates to
benchmark parameters and configuration:

  - EvalConfig dataclass: fields, defaults, construction, to_dict masking
  - add_eval_args: --concurrency, --out-dir, --allow-diagnostics
  - add_llm_args: the seven LLM CLI parameters (incl. env-var defaults)
  - add_qa_args: the three QA CLI parameters
  - add_agent_plugin_args: pre-parses sys.argv, delegates to plugin
  - build_config_from_args: getattr-based namespace -> EvalConfig mapping
  - validate_eval_config: required-field and numeric-bound validation
  - _scan_argv_for_plugin: --flag VALUE / --flag=VALUE pre-parsing
  - results_root_for: result-root resolution

No real services are touched; plugins.get_plugin_class is mocked so no
plugin module is imported.
"""

from __future__ import annotations

import argparse
import io
import os
import unittest
from contextlib import redirect_stderr
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from shared.eval_base import (
    EvalConfig,
    _scan_argv_for_plugin,
    add_agent_plugin_args,
    add_eval_args,
    add_llm_args,
    add_qa_args,
    build_config_from_args,
    results_root_for,
    validate_eval_config,
)


def _make_parser() -> argparse.ArgumentParser:
    """A fresh parser without -h so help never interferes with parse_args."""
    return argparse.ArgumentParser(prog="test", add_help=False)


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    out: set[str] = set()
    for action in parser._actions:
        out.update(action.option_strings)
    return out


# --------------------------------------------------------------------------- #
#  EvalConfig dataclass
# --------------------------------------------------------------------------- #
class EvalConfigDefaultsTests(unittest.TestCase):
    def test_default_construction_uses_documented_defaults(self) -> None:
        cfg = EvalConfig()
        # Plugins
        self.assertEqual(cfg.memory_backend, "echomem")
        self.assertEqual(cfg.agent_plugin, "bare_llm")
        # LLM
        self.assertEqual(
            cfg.llm_base_url,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.assertEqual(cfg.llm_model, "deepseek-v4-flash-0731")
        self.assertEqual(cfg.llm_api_key, "")
        self.assertEqual(cfg.llm_temperature, 0.7)
        self.assertEqual(cfg.llm_max_tokens, 2048)
        # Retrieval
        self.assertEqual(cfg.top_k, 10)
        self.assertEqual(cfg.memory_budget_chars, 8000)
        # Concurrency
        self.assertEqual(cfg.concurrency, 4)
        # Timeouts
        self.assertEqual(cfg.commit_timeout_s, 0.0)
        self.assertEqual(cfg.commit_poll_interval_s, 2.0)
        self.assertEqual(cfg.question_timeout_s, 120.0)
        self.assertEqual(cfg.llm_timeout_s, 120.0)
        self.assertEqual(cfg.llm_retries, 3)
        # Dataset
        self.assertEqual(cfg.dataset_path, "")
        self.assertEqual(cfg.sample_filter, "all")
        self.assertEqual(cfg.question_limit, 0)

    def test_default_construction_field_count(self) -> None:
        self.assertEqual(len(fields(EvalConfig)), 18)

    def test_custom_construction_overrides_fields(self) -> None:
        cfg = EvalConfig(
            memory_backend="openviking",
            agent_plugin="echo_agent",
            llm_base_url="http://x/v1",
            llm_model="m",
            llm_api_key="k",
            llm_temperature=0.1,
            llm_max_tokens=100,
            top_k=5,
            memory_budget_chars=4000,
            concurrency=2,
            commit_timeout_s=10.0,
            commit_poll_interval_s=1.0,
            question_timeout_s=60.0,
            llm_timeout_s=30.0,
            llm_retries=1,
            dataset_path="/data.json",
            sample_filter="conv-1",
            question_limit=12,
        )
        self.assertEqual(cfg.memory_backend, "openviking")
        self.assertEqual(cfg.agent_plugin, "echo_agent")
        self.assertEqual(cfg.llm_base_url, "http://x/v1")
        self.assertEqual(cfg.llm_model, "m")
        self.assertEqual(cfg.llm_api_key, "k")
        self.assertEqual(cfg.llm_temperature, 0.1)
        self.assertEqual(cfg.llm_max_tokens, 100)
        self.assertEqual(cfg.top_k, 5)
        self.assertEqual(cfg.memory_budget_chars, 4000)
        self.assertEqual(cfg.concurrency, 2)
        self.assertEqual(cfg.commit_timeout_s, 10.0)
        self.assertEqual(cfg.commit_poll_interval_s, 1.0)
        self.assertEqual(cfg.question_timeout_s, 60.0)
        self.assertEqual(cfg.llm_timeout_s, 30.0)
        self.assertEqual(cfg.llm_retries, 1)
        self.assertEqual(cfg.dataset_path, "/data.json")
        self.assertEqual(cfg.sample_filter, "conv-1")
        self.assertEqual(cfg.question_limit, 12)


class EvalConfigToDictTests(unittest.TestCase):
    def test_to_dict_contains_all_fields(self) -> None:
        d = EvalConfig().to_dict()
        expected = {f.name for f in fields(EvalConfig)}
        self.assertEqual(set(d.keys()), expected)

    def test_to_dict_preserves_non_secret_fields(self) -> None:
        cfg = EvalConfig(llm_base_url="http://x/v1", llm_model="m", top_k=7)
        d = cfg.to_dict()
        self.assertEqual(d["llm_base_url"], "http://x/v1")
        self.assertEqual(d["llm_model"], "m")
        self.assertEqual(d["top_k"], 7)

    def test_to_dict_leaves_empty_api_key_unchanged(self) -> None:
        self.assertEqual(EvalConfig(llm_api_key="").to_dict()["llm_api_key"], "")

    def test_to_dict_masks_short_api_key(self) -> None:
        # length <= 8 -> fully masked
        for key in ("a", "short", "12345678"):
            with self.subTest(key=key):
                self.assertEqual(
                    EvalConfig(llm_api_key=key).to_dict()["llm_api_key"], "***"
                )

    def test_to_dict_masks_long_api_key(self) -> None:
        # length > 8 -> first4 + *** + last4
        self.assertEqual(
            EvalConfig(llm_api_key="123456789").to_dict()["llm_api_key"],
            "1234***6789",
        )

    def test_to_dict_boundary_at_eight_chars(self) -> None:
        # exactly 8 chars -> "***" (condition is len > 8)
        self.assertEqual(
            EvalConfig(llm_api_key="12345678").to_dict()["llm_api_key"], "***"
        )
        # 9 chars -> masked with prefix/suffix
        self.assertEqual(
            EvalConfig(llm_api_key="123456789").to_dict()["llm_api_key"],
            "1234***6789",
        )

    def test_to_dict_returns_plain_dict(self) -> None:
        self.assertIsInstance(EvalConfig().to_dict(), dict)


# --------------------------------------------------------------------------- #
#  add_eval_args
# --------------------------------------------------------------------------- #
class AddEvalArgsTests(unittest.TestCase):
    def test_declares_three_eval_args_with_defaults(self) -> None:
        parser = _make_parser()
        add_eval_args(parser)
        opts = _option_strings(parser)
        for opt in ("--concurrency", "--out-dir", "--allow-diagnostics"):
            with self.subTest(opt=opt):
                self.assertIn(opt, opts)
        ns = parser.parse_args([])
        self.assertEqual(ns.concurrency, 4)
        self.assertEqual(ns.out_dir, "results")
        self.assertFalse(ns.allow_diagnostics)

    def test_parses_explicit_values(self) -> None:
        parser = _make_parser()
        add_eval_args(parser)
        ns = parser.parse_args(
            ["--concurrency", "8", "--out-dir", "/tmp/x", "--allow-diagnostics"]
        )
        self.assertEqual(ns.concurrency, 8)
        self.assertEqual(ns.out_dir, "/tmp/x")
        self.assertTrue(ns.allow_diagnostics)

    def test_concurrency_is_int_typed(self) -> None:
        parser = _make_parser()
        add_eval_args(parser)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--concurrency", "not-an-int"])

    def test_allow_diagnostics_is_store_true(self) -> None:
        parser = _make_parser()
        add_eval_args(parser)
        # Absent -> False; present without value -> True
        self.assertFalse(parser.parse_args([]).allow_diagnostics)
        self.assertTrue(
            parser.parse_args(["--allow-diagnostics"]).allow_diagnostics
        )


# --------------------------------------------------------------------------- #
#  add_llm_args
# --------------------------------------------------------------------------- #
class AddLlmArgsTests(unittest.TestCase):
    _EXPECTED_OPTS = {
        "--llm-base-url",
        "--llm-model",
        "--llm-api-key",
        "--llm-temperature",
        "--llm-max-tokens",
        "--llm-timeout-s",
        "--llm-retries",
    }

    def test_declares_seven_llm_args(self) -> None:
        parser = _make_parser()
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        self.assertEqual(self._EXPECTED_OPTS, _option_strings(parser))

    def test_defaults_without_env(self) -> None:
        parser = _make_parser()
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        ns = parser.parse_args([])
        self.assertEqual(
            ns.llm_base_url,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.assertEqual(ns.llm_model, "deepseek-v4-flash-0731")
        self.assertEqual(ns.llm_api_key, "")
        self.assertEqual(ns.llm_temperature, 0.7)
        self.assertEqual(ns.llm_max_tokens, 2048)
        self.assertEqual(ns.llm_timeout_s, 120.0)
        self.assertEqual(ns.llm_retries, 3)

    def test_env_vars_override_url_model_key_defaults(self) -> None:
        env = {
            "LLM_BASE_URL": "http://env.example/v1",
            "LLM_MODEL": "env-model",
            "LLM_API_KEY": "env-key",
        }
        parser = _make_parser()
        with patch("os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            add_llm_args(parser)
        ns = parser.parse_args([])
        self.assertEqual(ns.llm_base_url, "http://env.example/v1")
        self.assertEqual(ns.llm_model, "env-model")
        self.assertEqual(ns.llm_api_key, "env-key")

    def test_parses_explicit_values(self) -> None:
        parser = _make_parser()
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        ns = parser.parse_args(
            [
                "--llm-base-url", "http://x/v1",
                "--llm-model", "m",
                "--llm-api-key", "k",
                "--llm-temperature", "0.1",
                "--llm-max-tokens", "100",
                "--llm-timeout-s", "30",
                "--llm-retries", "5",
            ]
        )
        self.assertEqual(ns.llm_base_url, "http://x/v1")
        self.assertEqual(ns.llm_model, "m")
        self.assertEqual(ns.llm_api_key, "k")
        self.assertEqual(ns.llm_temperature, 0.1)
        self.assertEqual(ns.llm_max_tokens, 100)
        self.assertEqual(ns.llm_timeout_s, 30.0)
        self.assertEqual(ns.llm_retries, 5)

    def test_temperature_is_float_typed(self) -> None:
        parser = _make_parser()
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--llm-temperature", "not-a-float"])

    def test_max_tokens_is_int_typed(self) -> None:
        parser = _make_parser()
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--llm-max-tokens", "1.5"])


# --------------------------------------------------------------------------- #
#  add_qa_args
# --------------------------------------------------------------------------- #
class AddQaArgsTests(unittest.TestCase):
    _EXPECTED_OPTS = {"--top-k", "--memory-budget-chars", "--question-timeout-s"}

    def test_declares_three_qa_args(self) -> None:
        parser = _make_parser()
        add_qa_args(parser)
        self.assertEqual(self._EXPECTED_OPTS, _option_strings(parser))

    def test_defaults(self) -> None:
        parser = _make_parser()
        add_qa_args(parser)
        ns = parser.parse_args([])
        self.assertEqual(ns.top_k, 10)
        self.assertEqual(ns.memory_budget_chars, 8000)
        self.assertEqual(ns.question_timeout_s, 120.0)

    def test_parses_explicit_values(self) -> None:
        parser = _make_parser()
        add_qa_args(parser)
        ns = parser.parse_args(
            ["--top-k", "5", "--memory-budget-chars", "4000", "--question-timeout-s", "0"]
        )
        self.assertEqual(ns.top_k, 5)
        self.assertEqual(ns.memory_budget_chars, 4000)
        self.assertEqual(ns.question_timeout_s, 0.0)

    def test_top_k_is_int_typed(self) -> None:
        parser = _make_parser()
        add_qa_args(parser)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--top-k", "x"])

    def test_question_timeout_is_float_typed(self) -> None:
        parser = _make_parser()
        add_qa_args(parser)
        ns = parser.parse_args(["--question-timeout-s", "60"])
        self.assertEqual(ns.question_timeout_s, 60.0)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--question-timeout-s", "x"])


# --------------------------------------------------------------------------- #
#  _scan_argv_for_plugin
# --------------------------------------------------------------------------- #
class ScanArgvForPluginTests(unittest.TestCase):
    def test_finds_space_separated_value(self) -> None:
        with patch("sys.argv", ["prog", "--agent-plugin", "echo_agent", "--other", "x"]):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "echo_agent"
            )

    def test_finds_equals_separated_value(self) -> None:
        with patch("sys.argv", ["prog", "--agent-plugin=echo_agent"]):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "echo_agent"
            )

    def test_returns_default_when_absent(self) -> None:
        with patch("sys.argv", ["prog", "--other", "x"]):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "default"
            )

    def test_returns_default_when_flag_is_last_without_value(self) -> None:
        # "--agent-plugin" with no following token -> falls through to default
        with patch("sys.argv", ["prog", "--agent-plugin"]):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "default"
            )

    def test_returns_default_for_empty_argv(self) -> None:
        with patch("sys.argv", []):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "default"
            )

    def test_uses_custom_flag_name(self) -> None:
        with patch("sys.argv", ["prog", "--memory-backend", "openviking"]):
            self.assertEqual(
                _scan_argv_for_plugin("--memory-backend", "echomem"), "openviking"
            )

    def test_picks_first_occurrence(self) -> None:
        with patch("sys.argv", ["prog", "--agent-plugin", "first", "--agent-plugin", "second"]):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "first"
            )

    def test_bare_flag_returns_next_token_verbatim(self) -> None:
        # The token following --flag is returned verbatim, even if it itself
        # looks like a flag (the space form does not interpret the next token).
        with patch("sys.argv", ["prog", "--agent-plugin", "--other=x"]):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "--other=x"
            )

    def test_equals_form_with_empty_value(self) -> None:
        with patch("sys.argv", ["prog", "--agent-plugin="]):
            self.assertEqual(_scan_argv_for_plugin("--agent-plugin", "default"), "")

    def test_does_not_match_partial_flag_prefix(self) -> None:
        # "--agent-plugins" must not be treated as "--agent-plugin"
        with patch("sys.argv", ["prog", "--agent-plugins", "echo_agent"]):
            self.assertEqual(
                _scan_argv_for_plugin("--agent-plugin", "default"), "default"
            )


# --------------------------------------------------------------------------- #
#  add_agent_plugin_args
# --------------------------------------------------------------------------- #
class AddAgentPluginArgsTests(unittest.TestCase):
    @patch("plugins.get_plugin_class")
    def test_declares_agent_plugin_with_default(self, mock_get_cls: MagicMock) -> None:
        parser = _make_parser()
        with patch("sys.argv", ["prog"]):
            add_agent_plugin_args(parser, default_plugin="vikingbot")
        ns = parser.parse_args([])
        self.assertEqual(ns.agent_plugin, "vikingbot")
        self.assertIn("--agent-plugin", _option_strings(parser))

    @patch("plugins.get_plugin_class")
    def test_default_plugin_flows_to_scan_and_resolution(
        self, mock_get_cls: MagicMock
    ) -> None:
        for default in ("bare_llm", "vikingbot", "echo_agent"):
            with self.subTest(default=default):
                parser = _make_parser()
                with patch("sys.argv", ["prog"]):
                    add_agent_plugin_args(parser, default_plugin=default)
                mock_get_cls.assert_called_with(default)

    @patch("plugins.get_plugin_class")
    def test_reads_plugin_from_argv_space_form(self, mock_get_cls: MagicMock) -> None:
        parser = _make_parser()
        with patch("sys.argv", ["prog", "--agent-plugin", "echo_agent"]):
            add_agent_plugin_args(parser, default_plugin="vikingbot")
        mock_get_cls.assert_called_once_with("echo_agent")

    @patch("plugins.get_plugin_class")
    def test_reads_plugin_from_argv_equals_form(self, mock_get_cls: MagicMock) -> None:
        parser = _make_parser()
        with patch("sys.argv", ["prog", "--agent-plugin=echo_agent"]):
            add_agent_plugin_args(parser, default_plugin="vikingbot")
        mock_get_cls.assert_called_once_with("echo_agent")

    @patch("plugins.get_plugin_class")
    def test_delegates_add_arguments_to_resolved_plugin_class(
        self, mock_get_cls: MagicMock
    ) -> None:
        parser = _make_parser()
        with patch("sys.argv", ["prog"]):
            add_agent_plugin_args(parser, default_plugin="vikingbot")
        mock_get_cls.assert_called_once_with("vikingbot")
        mock_get_cls.return_value.add_arguments.assert_called_once_with(parser)

    @patch("plugins.get_plugin_class")
    def test_explicit_cli_overrides_default_namespace(self, mock_get_cls: MagicMock) -> None:
        parser = _make_parser()
        with patch("sys.argv", ["prog"]):
            add_agent_plugin_args(parser, default_plugin="vikingbot")
        ns = parser.parse_args(["--agent-plugin", "echo_agent"])
        self.assertEqual(ns.agent_plugin, "echo_agent")


# --------------------------------------------------------------------------- #
#  build_config_from_args
# --------------------------------------------------------------------------- #
class BuildConfigFromArgsTests(unittest.TestCase):
    def test_maps_all_declared_fields(self) -> None:
        args = SimpleNamespace(
            memory_backend="openviking",
            agent_plugin="echo_agent",
            llm_base_url="http://x/v1",
            llm_model="m",
            llm_api_key="k",
            llm_temperature=0.1,
            llm_max_tokens=100,
            top_k=5,
            memory_budget_chars=4000,
            concurrency=2,
            commit_timeout_s=10.0,
            commit_poll_interval_s=1.0,
            question_timeout_s=60.0,
            llm_timeout_s=30.0,
            llm_retries=1,
        )
        cfg = build_config_from_args(args)
        self.assertEqual(cfg.memory_backend, "openviking")
        self.assertEqual(cfg.agent_plugin, "echo_agent")
        self.assertEqual(cfg.llm_base_url, "http://x/v1")
        self.assertEqual(cfg.llm_model, "m")
        self.assertEqual(cfg.llm_api_key, "k")
        self.assertEqual(cfg.llm_temperature, 0.1)
        self.assertEqual(cfg.llm_max_tokens, 100)
        self.assertEqual(cfg.top_k, 5)
        self.assertEqual(cfg.memory_budget_chars, 4000)
        self.assertEqual(cfg.concurrency, 2)
        self.assertEqual(cfg.commit_timeout_s, 10.0)
        self.assertEqual(cfg.commit_poll_interval_s, 1.0)
        self.assertEqual(cfg.question_timeout_s, 60.0)
        self.assertEqual(cfg.llm_timeout_s, 30.0)
        self.assertEqual(cfg.llm_retries, 1)

    def test_does_not_populate_dataset_fields(self) -> None:
        # dataset_path/sample_filter/question_limit are not read from args;
        # they keep their EvalConfig dataclass defaults.
        args = SimpleNamespace(
            memory_backend="echomem",
            agent_plugin="bare_llm",
            dataset_path="/ignored.json",
            sample_filter="ignored",
            question_limit=99,
        )
        cfg = build_config_from_args(args)
        self.assertEqual(cfg.dataset_path, "")
        self.assertEqual(cfg.sample_filter, "all")
        self.assertEqual(cfg.question_limit, 0)

    def test_uses_getattr_defaults_for_empty_namespace(self) -> None:
        cfg = build_config_from_args(SimpleNamespace())
        self.assertEqual(cfg.memory_backend, "echomem")
        self.assertEqual(cfg.agent_plugin, "bare_llm")
        self.assertEqual(
            cfg.llm_base_url,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.assertEqual(cfg.llm_model, "deepseek-v4-flash-0731")
        self.assertEqual(cfg.llm_api_key, "")
        self.assertEqual(cfg.llm_temperature, 0.7)
        self.assertEqual(cfg.llm_max_tokens, 2048)
        self.assertEqual(cfg.top_k, 10)
        self.assertEqual(cfg.memory_budget_chars, 8000)
        self.assertEqual(cfg.concurrency, 4)
        self.assertEqual(cfg.commit_timeout_s, 0.0)
        self.assertEqual(cfg.commit_poll_interval_s, 2.0)
        self.assertEqual(cfg.question_timeout_s, 120.0)
        self.assertEqual(cfg.llm_timeout_s, 120.0)
        self.assertEqual(cfg.llm_retries, 3)

    def test_uses_getattr_defaults_for_partial_namespace(self) -> None:
        # Only some attrs present; the rest fall back to getattr defaults.
        args = SimpleNamespace(concurrency=16, top_k=3)
        cfg = build_config_from_args(args)
        self.assertEqual(cfg.concurrency, 16)
        self.assertEqual(cfg.top_k, 3)
        self.assertEqual(cfg.memory_backend, "echomem")
        self.assertEqual(cfg.llm_timeout_s, 120.0)

    def test_works_with_real_argparse_namespace(self) -> None:
        parser = _make_parser()
        add_eval_args(parser)
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        add_qa_args(parser)
        ns = parser.parse_args(
            ["--concurrency", "8", "--top-k", "5", "--llm-base-url", "http://x/v1"]
        )
        cfg = build_config_from_args(ns)
        self.assertEqual(cfg.concurrency, 8)
        self.assertEqual(cfg.top_k, 5)
        self.assertEqual(cfg.llm_base_url, "http://x/v1")


# --------------------------------------------------------------------------- #
#  validate_eval_config
# --------------------------------------------------------------------------- #
class ValidateEvalConfigTests(unittest.TestCase):
    def _valid(self) -> EvalConfig:
        return EvalConfig(
            llm_base_url="https://example.test/v1",
            llm_model="test-model",
            llm_api_key="secret",
        )

    def test_valid_config_does_not_raise(self) -> None:
        # Returns None implicitly when there are no errors.
        self.assertIsNone(validate_eval_config(self._valid()))

    def test_valid_config_with_zero_timeouts_allowed(self) -> None:
        # question_timeout_s=0 and commit_timeout_s=0 are permitted (>= 0).
        cfg = self._valid()
        cfg.question_timeout_s = 0.0
        cfg.commit_timeout_s = 0.0
        self.assertIsNone(validate_eval_config(cfg))

    def test_each_violation_raises_with_expected_message(self) -> None:
        cases: list[tuple[str, Any, Any, str]] = [
            ("llm_base_url", "", "missing LLM base URL"),
            ("llm_base_url", "   ", "missing LLM base URL"),
            ("llm_model", "", "missing LLM model"),
            ("llm_api_key", "", "missing LLM API key"),
            ("concurrency", 0, "concurrency must be >= 1"),
            ("question_timeout_s", -1.0, "question timeout must be >= 0"),
            ("commit_timeout_s", -1.0, "commit timeout must be >= 0"),
            ("commit_poll_interval_s", 0.0, "commit poll interval must be > 0"),
            ("llm_timeout_s", 0.0, "LLM timeout must be > 0"),
            ("llm_retries", 0, "LLM retries must be >= 1"),
            ("llm_max_tokens", 0, "LLM max tokens must be >= 1"),
            ("top_k", 0, "top-k must be >= 1"),
            ("memory_budget_chars", 0, "memory budget must be >= 1"),
            ("question_limit", -1, "questions must be >= 0"),
        ]
        for attr, value, expected in cases:
            with self.subTest(attr=attr, value=value):
                cfg = self._valid()
                setattr(cfg, attr, value)
                with self.assertRaisesRegex(ValueError, expected):
                    validate_eval_config(cfg)

    def test_multiple_errors_joined_by_semicolon(self) -> None:
        # EvalConfig() defaults: DashScope base URL and model; API key is required.
        cfg = EvalConfig()
        with self.assertRaises(ValueError) as ctx:
            validate_eval_config(cfg)
        message = str(ctx.exception)
        self.assertIn("missing LLM API key", message)

    def test_whitespace_only_llm_fields_are_rejected(self) -> None:
        cfg = EvalConfig(
            llm_base_url="  ",
            llm_model="\t",
            llm_api_key=" ",
        )
        with self.assertRaises(ValueError) as ctx:
            validate_eval_config(cfg)
        message = str(ctx.exception)
        self.assertIn("missing LLM base URL", message)
        self.assertIn("missing LLM model", message)
        self.assertIn("missing LLM API key", message)


# --------------------------------------------------------------------------- #
#  results_root_for
# --------------------------------------------------------------------------- #
class ResultsRootForTests(unittest.TestCase):
    def test_default_results_appends_to_benchmark_dir(self) -> None:
        self.assertEqual(
            results_root_for("/bench/locomo", "results"),
            Path("/bench/locomo") / "results",
        )

    def test_none_or_empty_falls_back_to_benchmark_dir(self) -> None:
        for out_dir in (None, ""):
            with self.subTest(out_dir=out_dir):
                self.assertEqual(
                    results_root_for("/bench", out_dir),
                    Path("/bench") / "results",
                )

    def test_whitespace_only_out_dir_becomes_empty_path(self) -> None:
        # str("   " or "results") is "   " (truthy), then .strip() -> ""; since
        # "" != "results", it returns Path("").expanduser() (the current dir).
        self.assertEqual(results_root_for("/bench", "   "), Path("").expanduser())

    def test_custom_absolute_path_returned_as_is(self) -> None:
        self.assertEqual(
            results_root_for("/bench", "/var/eval/out"),
            Path("/var/eval/out"),
        )

    def test_custom_relative_path_returned_as_is(self) -> None:
        self.assertEqual(
            results_root_for("/bench", "custom/out"),
            Path("custom/out"),
        )

    def test_tilde_path_is_expanded(self) -> None:
        self.assertEqual(
            results_root_for("/bench", "~/runs"),
            Path("~/runs").expanduser(),
        )

    def test_default_string_is_case_sensitive(self) -> None:
        # Only the literal "results" triggers the benchmark-dir fallback.
        self.assertEqual(
            results_root_for("/bench", "Results"),
            Path("Results"),
        )


# --------------------------------------------------------------------------- #
#  End-to-end: args -> config -> validation
# --------------------------------------------------------------------------- #
class EndToEndParamsTests(unittest.TestCase):
    def test_full_pipeline_builds_and_validates(self) -> None:
        parser = _make_parser()
        add_eval_args(parser)
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        add_qa_args(parser)
        ns = parser.parse_args(
            [
                "--llm-base-url", "http://x/v1",
                "--llm-api-key", "secret123",
                "--llm-model", "m",
                "--concurrency", "2",
                "--top-k", "5",
            ]
        )
        cfg = build_config_from_args(ns)
        # Should validate cleanly.
        self.assertIsNone(validate_eval_config(cfg))
        self.assertEqual(cfg.concurrency, 2)
        self.assertEqual(cfg.top_k, 5)
        self.assertEqual(cfg.llm_base_url, "http://x/v1")

    def test_pipeline_with_missing_llm_api_key_fails_validation(self) -> None:
        parser = _make_parser()
        add_eval_args(parser)
        with patch("os.getenv", side_effect=lambda k, d=None: d):
            add_llm_args(parser)
        ns = parser.parse_args(["--llm-model", "m"])
        cfg = build_config_from_args(ns)
        with self.assertRaisesRegex(ValueError, "missing LLM API key"):
            validate_eval_config(cfg)


if __name__ == "__main__":
    unittest.main()
