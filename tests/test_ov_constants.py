"""Unit tests for shared.ov_constants.

Tests the OV config file generation (write_kimi_ov_files / write_hermes_ov_files),
TOML hook stripping, and env var builder.
Uses tempfile for file I/O.

Run: python -m unittest tests.test_ov_constants -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from shared.ov_constants import (
    KIMI_CODE_HOME_ENV,
    HERMES_HOME_ENV,
    OV_URL_DEFAULT,
    OV_ENV_URL,
    OV_ENV_API_KEY,
    OV_ENV_ACCOUNT,
    OV_ENV_USER,
    _strip_ov_hooks_from_toml,
    build_ov_env,
    write_kimi_ov_files,
    write_hermes_ov_files,
)


# Minimal kimi config.toml for testing (mimics real structure).
_KIMI_CONFIG_TOML = """\
default_model = "test-model"
default_permission_mode = "auto"

[providers.test]
type = "openai"
base_url = "https://example.com/v1"
api_key = "sk-test-key"

[[permission.rules]]
decision = "allow"
pattern = "Read"

[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "node /some/other-hook.mjs"
timeout = 5
"""

# Minimal hermes config.yaml for testing.
_HERMES_CONFIG_YAML = """\
model:
  default: test-model
  provider: custom
  base_url: https://example.com/v1
  api_key: sk-test-key
toolsets:
- hermes-cli
hooks: {}
hooks_auto_accept: false
"""


# ------------------------------------------------------------------ #
#  build_ov_env                                                       #
# ------------------------------------------------------------------ #

class BuildOvEnvTests(unittest.TestCase):

    def test_all_fields(self):
        env = build_ov_env("http://ov:1933", "sk-key", "acct", "usr")
        self.assertEqual("http://ov:1933", env[OV_ENV_URL])
        self.assertEqual("sk-key", env[OV_ENV_API_KEY])
        self.assertEqual("acct", env[OV_ENV_ACCOUNT])
        self.assertEqual("usr", env[OV_ENV_USER])

    def test_empty_fields_omitted(self):
        env = build_ov_env("http://ov:1933", "", "", "")
        self.assertIn(OV_ENV_URL, env)
        self.assertNotIn(OV_ENV_API_KEY, env)
        self.assertNotIn(OV_ENV_ACCOUNT, env)
        self.assertNotIn(OV_ENV_USER, env)

    def test_empty_url_omitted(self):
        env = build_ov_env("", "key", "acct", "usr")
        self.assertNotIn(OV_ENV_URL, env)
        self.assertIn(OV_ENV_API_KEY, env)

    def test_all_empty_returns_empty_dict(self):
        env = build_ov_env("", "", "", "")
        self.assertEqual({}, env)


# ------------------------------------------------------------------ #
#  _strip_ov_hooks_from_toml                                          #
# ------------------------------------------------------------------ #

class StripOvHooksFromTomlTests(unittest.TestCase):

    def test_removes_hooks_block_with_auto_recall(self):
        toml = """\
key = "value"

[[hooks]]
event = "UserPromptSubmit"
command = "node /ov/hooks/auto-recall.mjs"
timeout = 10
"""
        result = _strip_ov_hooks_from_toml(toml)
        self.assertNotIn("auto-recall.mjs", result)
        self.assertIn('key = "value"', result)

    def test_preserves_non_ov_hooks(self):
        toml = """\
[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "node /some/other-hook.mjs"
timeout = 5

[[hooks]]
event = "UserPromptSubmit"
command = "node /ov/hooks/auto-recall.mjs"
timeout = 10
"""
        result = _strip_ov_hooks_from_toml(toml)
        self.assertNotIn("auto-recall.mjs", result)
        self.assertIn("other-hook.mjs", result)
        self.assertIn("PreToolUse", result)

    def test_no_hooks_returns_unchanged(self):
        toml = 'key = "value"\n'
        result = _strip_ov_hooks_from_toml(toml)
        self.assertEqual(toml, result)

    def test_multiple_ov_hooks_all_removed(self):
        toml = """\
[[hooks]]
event = "UserPromptSubmit"
command = "node /ov1/auto-recall.mjs"
timeout = 10

[[hooks]]
event = "PostToolUse"
command = "node /ov2/auto-recall.mjs"
timeout = 5
"""
        result = _strip_ov_hooks_from_toml(toml)
        self.assertNotIn("auto-recall.mjs", result)

    def test_empty_string(self):
        self.assertEqual("", _strip_ov_hooks_from_toml(""))


# ------------------------------------------------------------------ #
#  write_kimi_ov_files                                                #
# ------------------------------------------------------------------ #

class WriteKimiOvFilesTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ov_kimi_test_")
        self._ov_home = os.path.join(self._tmpdir, "ov_home")
        self._user_config_dir = os.path.join(self._tmpdir, "user_config")
        os.makedirs(self._user_config_dir)
        with open(os.path.join(self._user_config_dir, "config.toml"), "w",
                  encoding="utf-8") as f:
            f.write(_KIMI_CONFIG_TOML)

    def test_creates_config_toml(self):
        result = write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        config_path = os.path.join(self._ov_home, "config.toml")
        self.assertTrue(os.path.exists(config_path))
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("UserPromptSubmit", content)
        self.assertIn("auto-recall.mjs", content)

    def test_preserves_user_config_content(self):
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        with open(os.path.join(self._ov_home, "config.toml"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("test-model", content)
        self.assertIn("sk-test-key", content)
        self.assertIn("example.com", content)

    def test_preserves_non_ov_hooks(self):
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        with open(os.path.join(self._ov_home, "config.toml"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("other-hook.mjs", content)
        self.assertIn("PreToolUse", content)

    def test_creates_hook_script(self):
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        hook_path = os.path.join(self._ov_home, "hooks", "auto-recall.mjs")
        self.assertTrue(os.path.exists(hook_path))
        with open(hook_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("recallContext", content)
        self.assertIn("openviking-context", content)

    def test_mcp_on_writes_ov_server(self):
        write_kimi_ov_files(
            self._ov_home, mcp_tools=True, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        mcp_path = os.path.join(self._ov_home, "mcp.json")
        with open(mcp_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("openviking", data["mcpServers"])
        self.assertEqual("http://ov:1933/mcp",
                         data["mcpServers"]["openviking"]["url"])

    def test_mcp_off_writes_empty_servers(self):
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        mcp_path = os.path.join(self._ov_home, "mcp.json")
        with open(mcp_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual({}, data["mcpServers"])

    def test_returns_resolved_path(self):
        result = write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(os.path.exists(result))

    def test_hook_path_uses_forward_slashes(self):
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        config_path = os.path.join(self._ov_home, "config.toml")
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("hooks/auto-recall.mjs", content)

    def test_idempotent_overwrite(self):
        # Write with MCP on
        write_kimi_ov_files(
            self._ov_home, mcp_tools=True, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        # Write again with MCP off
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        mcp_path = os.path.join(self._ov_home, "mcp.json")
        with open(mcp_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual({}, data["mcpServers"])

    def test_idempotent_no_duplicate_ov_hooks(self):
        # Write once
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        # Write again
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        with open(os.path.join(self._ov_home, "config.toml"), encoding="utf-8") as f:
            content = f.read()
        # Should have exactly one auto-recall.mjs reference
        self.assertEqual(1, content.count("auto-recall.mjs"))

    def test_raises_when_config_not_found(self):
        bad_dir = os.path.join(self._tmpdir, "nonexistent")
        with self.assertRaises(FileNotFoundError) as ctx:
            write_kimi_ov_files(
                self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
                config_home=bad_dir,
            )
        self.assertIn("config.toml", str(ctx.exception))

    def test_raises_when_ov_home_has_spaces(self):
        spaced_dir = os.path.join(self._tmpdir, "dir with spaces")
        with self.assertRaises(ValueError) as ctx:
            write_kimi_ov_files(
                spaced_dir, mcp_tools=False, ov_url="http://ov:1933",
                config_home=self._user_config_dir,
            )
        self.assertIn("spaces", str(ctx.exception))


# ------------------------------------------------------------------ #
#  write_hermes_ov_files                                              #
# ------------------------------------------------------------------ #

class WriteHermesOvFilesTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ov_hermes_test_")
        self._ov_home = os.path.join(self._tmpdir, "ov_home")
        self._user_config_dir = os.path.join(self._tmpdir, "user_config")
        os.makedirs(self._user_config_dir)
        with open(os.path.join(self._user_config_dir, "config.yaml"), "w",
                  encoding="utf-8") as f:
            f.write(_HERMES_CONFIG_YAML)

    def test_creates_config_yaml(self):
        result = write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        config_path = os.path.join(self._ov_home, "config.yaml")
        self.assertTrue(os.path.exists(config_path))
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pre_llm_call", content)
        self.assertIn("auto-recall.mjs", content)
        self.assertIn("hooks_auto_accept: true", content)

    def test_preserves_user_config_content(self):
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        with open(os.path.join(self._ov_home, "config.yaml"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("test-model", content)
        self.assertIn("sk-test-key", content)
        self.assertIn("hermes-cli", content)

    def test_creates_hook_script(self):
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        hook_path = os.path.join(self._ov_home, "hooks", "auto-recall.mjs")
        self.assertTrue(os.path.exists(hook_path))
        with open(hook_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("recallContext", content)
        self.assertIn("openviking-context", content)
        self.assertIn("JSON.stringify", content)

    def test_mcp_on_includes_mcp_servers(self):
        write_hermes_ov_files(
            self._ov_home, mcp_tools=True, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        config_path = os.path.join(self._ov_home, "config.yaml")
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("mcp_servers:", content)
        self.assertIn("openviking:", content)
        self.assertIn("http://ov:1933/mcp", content)

    def test_mcp_off_excludes_mcp_servers(self):
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        config_path = os.path.join(self._ov_home, "config.yaml")
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("mcp_servers:", content)

    def test_returns_resolved_path(self):
        result = write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(os.path.exists(result))

    def test_hook_path_uses_forward_slashes(self):
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        config_path = os.path.join(self._ov_home, "config.yaml")
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("hooks/auto-recall.mjs", content)

    def test_idempotent_overwrite(self):
        # Write with MCP on
        write_hermes_ov_files(
            self._ov_home, mcp_tools=True, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        # Write again with MCP off
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        config_path = os.path.join(self._ov_home, "config.yaml")
        with open(config_path, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("mcp_servers:", content)

    def test_idempotent_no_duplicate_ov_hooks(self):
        # Write once
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        # Write again
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        with open(os.path.join(self._ov_home, "config.yaml"), encoding="utf-8") as f:
            content = f.read()
        # Should have exactly one auto-recall.mjs reference
        self.assertEqual(1, content.count("auto-recall.mjs"))

    def test_raises_when_config_not_found(self):
        bad_dir = os.path.join(self._tmpdir, "nonexistent")
        with self.assertRaises(FileNotFoundError) as ctx:
            write_hermes_ov_files(
                self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
                config_home=bad_dir,
            )
        self.assertIn("config.yaml", str(ctx.exception))

    def test_raises_when_ov_home_has_spaces(self):
        spaced_dir = os.path.join(self._tmpdir, "dir with spaces")
        with self.assertRaises(ValueError) as ctx:
            write_hermes_ov_files(
                spaced_dir, mcp_tools=False, ov_url="http://ov:1933",
                config_home=self._user_config_dir,
            )
        self.assertIn("spaces", str(ctx.exception))

    def test_sets_hooks_auto_accept_true(self):
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        import yaml
        with open(os.path.join(self._ov_home, "config.yaml"), encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.assertTrue(config["hooks_auto_accept"])


# ------------------------------------------------------------------ #
#  Constants                                                          #
# ------------------------------------------------------------------ #

class OVConstantsTests(unittest.TestCase):

    def test_default_url(self):
        self.assertEqual("http://127.0.0.1:19080", OV_URL_DEFAULT)

    def test_env_var_names(self):
        self.assertEqual("KIMI_CODE_HOME", KIMI_CODE_HOME_ENV)
        self.assertEqual("HERMES_HOME", HERMES_HOME_ENV)
        self.assertEqual("OPENVIKING_URL", OV_ENV_URL)
        self.assertEqual("OPENVIKING_API_KEY", OV_ENV_API_KEY)
        self.assertEqual("OPENVIKING_ACCOUNT", OV_ENV_ACCOUNT)
        self.assertEqual("OPENVIKING_USER", OV_ENV_USER)

    def test_sidecar_path_env_var(self):
        from shared.ov_constants import OV_ENV_SIDECAR_PATH
        self.assertEqual("OV_SIDECAR_PATH", OV_ENV_SIDECAR_PATH)


# ------------------------------------------------------------------ #
#  Hook script sidecar content                                        #
# ------------------------------------------------------------------ #

class HookScriptSidecarTests(unittest.TestCase):
    """Verify the hook scripts contain sidecar-writing logic."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ov_sidecar_test_")
        self._ov_home = os.path.join(self._tmpdir, "ov_home")
        self._user_config_dir = os.path.join(self._tmpdir, "user_config")
        os.makedirs(self._user_config_dir)
        with open(os.path.join(self._user_config_dir, "config.toml"), "w",
                  encoding="utf-8") as f:
            f.write(_KIMI_CONFIG_TOML)
        with open(os.path.join(self._user_config_dir, "config.yaml"), "w",
                  encoding="utf-8") as f:
            f.write(_HERMES_CONFIG_YAML)

    def test_kimi_hook_contains_write_sidecar(self):
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        hook_path = os.path.join(self._ov_home, "hooks", "auto-recall.mjs")
        with open(hook_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("writeFileSync", content)
        self.assertIn("_writeSidecar", content)
        self.assertIn("OV_SIDECAR_PATH", content)

    def test_hermes_hook_contains_write_sidecar(self):
        write_hermes_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        hook_path = os.path.join(self._ov_home, "hooks", "auto-recall.mjs")
        with open(hook_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("writeFileSync", content)
        self.assertIn("_writeSidecar", content)
        self.assertIn("OV_SIDECAR_PATH", content)

    def test_kimi_hook_captures_entries(self):
        """Hook script reads result.entries (not result.items) from OV API."""
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        hook_path = os.path.join(self._ov_home, "hooks", "auto-recall.mjs")
        with open(hook_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("entries", content)

    def test_kimi_hook_no_read_file_sync(self):
        """The old unused readFileSync import should be gone."""
        write_kimi_ov_files(
            self._ov_home, mcp_tools=False, ov_url="http://ov:1933",
            config_home=self._user_config_dir,
        )
        hook_path = os.path.join(self._ov_home, "hooks", "auto-recall.mjs")
        with open(hook_path, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("readFileSync", content)


if __name__ == "__main__":
    unittest.main()
