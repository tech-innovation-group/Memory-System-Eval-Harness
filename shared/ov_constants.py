"""Shared constants and helpers for OpenViking integration in CLI agent plugins.

Provides:
- OV env-var names and default URL
- Hook script content (auto-recall.mjs) for kimi_code and hermes
- Helpers to write OV files (hook script + config + mcp.json) to an ov_home
  directory, reading the user's actual agent config and injecting the OV hook

Each CLI agent plugin calls write_*_ov_files() in send_message() when ov_home
is configured. The hook script queries OV's recall API and injects an
<openviking-context> block into the agent's context.

Config injection strategy: the caller passes config_home (the path to the
user's actual agent config directory, e.g. ~/.kimi-code/ or ~/.hermes/).
write_*_ov_files() reads the real config file from there, injects the OV
hook section, and writes the modified copy to ov_home. The user's original
config is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

# ------------------------------------------------------------------ #
#  Constants                                                          #
# ------------------------------------------------------------------ #

OV_URL_DEFAULT = "http://127.0.0.1:19080"

# Env vars passed to the hook script (and to the agent subprocess).
OV_ENV_URL = "OPENVIKING_URL"
OV_ENV_API_KEY = "OPENVIKING_API_KEY"
OV_ENV_ACCOUNT = "OPENVIKING_ACCOUNT"
OV_ENV_USER = "OPENVIKING_USER"

# Env var set per send_message call to a unique file path. The hook script
# writes recall results (items, rendered, latency, error) to this path so
# the plugin can collect telemetry after the subprocess finishes.
OV_ENV_SIDECAR_PATH = "OV_SIDECAR_PATH"

# Env var name passed to the kimi CLI subprocess to point it at ov_home.
KIMI_CODE_HOME_ENV = "KIMI_CODE_HOME"
# Env var name passed to the hermes CLI subprocess to point it at ov_home.
HERMES_HOME_ENV = "HERMES_HOME"


# ------------------------------------------------------------------ #
#  Hook script: shared recall logic (extracted to avoid duplication)  #
# ------------------------------------------------------------------ #

_RECALL_CORE = """
import { writeFileSync } from 'node:fs';

const OV_URL = process.env.OPENVIKING_URL || 'http://127.0.0.1:19080';
const OV_API_KEY = process.env.OPENVIKING_API_KEY || '';
const OV_ACCOUNT = process.env.OPENVIKING_ACCOUNT || '';
const OV_USER = process.env.OPENVIKING_USER || '';

/**
 * Write recall telemetry to a sidecar JSON file when OV_SIDECAR_PATH is set.
 * The plugin creates a unique path per send_message call so that concurrent
 * workers never collide. All call paths (success, error, short-circuit)
 * invoke this so the plugin always finds a sidecar to read.
 */
function _writeSidecar(query, items, rendered, latencyMs, error) {
  const path = process.env.OV_SIDECAR_PATH;
  if (!path) return;
  try {
    writeFileSync(path, JSON.stringify({
      query, items, rendered, latency_ms: latencyMs, error,
    }));
  } catch {}
}

/**
 * Query OpenViking recall API and return rendered context string.
 * Returns empty string on error, empty results, or short queries.
 */
export async function recallContext(userPrompt) {
  if (!userPrompt || userPrompt.trim().length < 3) {
    _writeSidecar(userPrompt || '', [], '', 0, 'query_too_short');
    return '';
  }

  const headers = { 'Content-Type': 'application/json' };
  if (OV_API_KEY) headers['Authorization'] = `Bearer ${OV_API_KEY}`;
  if (OV_ACCOUNT) headers['X-OpenViking-Account'] = OV_ACCOUNT;
  if (OV_USER) headers['X-OpenViking-User'] = OV_USER;

  const body = JSON.stringify({
    query: userPrompt,
    quotas: { events: 6, entities: 6, preferences: 3, experiences: 0 },
    max_chars: 3000,
    min_score: 0.35,
    render: true,
  });

  const t0 = Date.now();
  try {
    const resp = await fetch(`${OV_URL}/api/v1/search/recall`, {
      method: 'POST',
      headers,
      body,
      signal: AbortSignal.timeout(8000),
    });
    const latencyMs = Date.now() - t0;
    if (!resp.ok) {
      _writeSidecar(userPrompt, [], '', latencyMs, `http_${resp.status}`);
      return '';
    }
    const data = await resp.json();
    const rendered = (data?.result?.rendered || '').trim();
    const items = data?.result?.entries || [];
    _writeSidecar(userPrompt, items, rendered, latencyMs, '');
    if (!rendered) return '';
    return `<openviking-context>\\n${rendered}\\n</openviking-context>`;
  } catch (e) {
    const latencyMs = Date.now() - t0;
    _writeSidecar(userPrompt, [], '', latencyMs, String(e?.message || e));
    return '';
  }
}
""".strip()


# ------------------------------------------------------------------ #
#  Kimi Code hook script (UserPromptSubmit)                           #
# ------------------------------------------------------------------ #
#
# Kimi Code UserPromptSubmit hook:
#   stdin:  {"hook_event_name":"UserPromptSubmit","session_id":"...",
#            "cwd":"...","prompt":[{"type":"text","text":"user input"}]}
#   stdout: plain text appended to the agent's context
#

KIMI_HOOK_SCRIPT = f"""\
#!/usr/bin/env node
/**
 * OpenViking auto-recall hook for Kimi Code (UserPromptSubmit).
 *
 * Reads the user prompt from stdin, queries OpenViking's recall API,
 * and outputs an <openviking-context> block as plain text. Kimi Code
 * appends stdout text to the conversation context.
 */

{_RECALL_CORE}

async function main() {{
  let input;
  try {{
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  }} catch {{
    process.stdout.write('');
    return;
  }}

  const promptArr = input.prompt || [];
  const userPrompt = promptArr.map(p => p.text || '').join('\\n').trim();

  const context = await recallContext(userPrompt);
  process.stdout.write(context);
}}

main();
"""


# ------------------------------------------------------------------ #
#  Hermes hook script (pre_llm_call)                                  #
# ------------------------------------------------------------------ #
#
# Hermes pre_llm_call hook:
#   stdin:  {"hook_event_name":"pre_llm_call","session_id":"...",
#            "cwd":"...","extra":{{"user_message":"user input", ...}}}}
#   stdout: {{"context":"<text>"}} - context is appended to the user message
#

HERMES_HOOK_SCRIPT = f"""\
#!/usr/bin/env node
/**
 * OpenViking auto-recall hook for Hermes (pre_llm_call).
 *
 * Reads the user prompt from stdin, queries OpenViking's recall API,
 * and outputs {{"context":"<openviking-context>...</openviking-context>"}} JSON.
 * Hermes appends the context string to the current turn's user message.
 */

{_RECALL_CORE}

async function main() {{
  let input;
  try {{
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  }} catch {{
    process.stdout.write(JSON.stringify({{}}));
    return;
  }}

  const userPrompt = (input.extra?.user_message || '').trim();

  const context = await recallContext(userPrompt);
  process.stdout.write(JSON.stringify({{ context }}));
}}

main();
"""


# ------------------------------------------------------------------ #
#  TOML hook injection (kimi-code)                                    #
# ------------------------------------------------------------------ #

def _strip_ov_hooks_from_toml(text: str) -> str:
    """Remove [[hooks]] blocks that reference auto-recall.mjs.

    Scans line-by-line for [[hooks]] section blocks. A block extends from
    the [[hooks]] header until the next section header ([...] or [[...]])
    or end of text. Blocks containing 'auto-recall.mjs' are dropped;
    all other hooks are preserved.
    """
    lines = text.split("\n")
    output: list[str] = []
    in_hooks_block = False
    block_has_ov = False
    block_lines: list[str] = []

    def _flush() -> None:
        nonlocal in_hooks_block, block_has_ov, block_lines
        if in_hooks_block and not block_has_ov:
            output.extend(block_lines)
        in_hooks_block = False
        block_has_ov = False
        block_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[["):
            _flush()
            if stripped == "[[hooks]]":
                in_hooks_block = True
                block_lines = [line]
            else:
                output.append(line)
        elif stripped.startswith("[") and not stripped.startswith("[["):
            _flush()
            output.append(line)
        elif in_hooks_block:
            block_lines.append(line)
            if "auto-recall.mjs" in line:
                block_has_ov = True
        else:
            output.append(line)

    _flush()
    return "\n".join(output)


# ------------------------------------------------------------------ #
#  MCP server config (kimi-code uses a separate mcp.json)             #
# ------------------------------------------------------------------ #

def _kimi_mcp_json(ov_url: str) -> str:
    """Generate mcp.json content for kimi-code with OV MCP server."""
    return json.dumps({
        "mcpServers": {
            "openviking": {
                "type": "http",
                "url": f"{ov_url}/mcp",
            }
        }
    }, indent=2)


def _kimi_mcp_json_empty() -> str:
    """Generate empty mcp.json content (MCP disabled)."""
    return json.dumps({"mcpServers": {}})


# ------------------------------------------------------------------ #
#  OV env vars builder                                                #
# ------------------------------------------------------------------ #

def build_ov_env(
    ov_url: str,
    ov_api_key: str,
    ov_account: str,
    ov_user: str,
) -> dict[str, str]:
    """Build the OPENVIKING_* env vars dict for the subprocess."""
    env: dict[str, str] = {}
    if ov_url:
        env[OV_ENV_URL] = ov_url
    if ov_api_key:
        env[OV_ENV_API_KEY] = ov_api_key
    if ov_account:
        env[OV_ENV_ACCOUNT] = ov_account
    if ov_user:
        env[OV_ENV_USER] = ov_user
    return env


# ------------------------------------------------------------------ #
#  Write OV files to ov_home                                          #
# ------------------------------------------------------------------ #

def write_kimi_ov_files(
    ov_home: str,
    *,
    mcp_tools: bool,
    ov_url: str,
    config_home: str,
) -> str:
    """Write kimi-code OV config files to ov_home.

    Reads the user's actual config.toml from config_home, strips any
    existing OV hooks, appends the OV UserPromptSubmit hook, and writes
    the modified config to ov_home/config.toml.

    Creates:
      {ov_home}/hooks/auto-recall.mjs  - hook script
      {ov_home}/config.toml            - kimi config with OV hook injected
      {ov_home}/mcp.json               - MCP config (OV server or empty)

    Returns the absolute path to ov_home (for KIMI_CODE_HOME env var).

    Raises FileNotFoundError if the user's config.toml does not exist.
    """
    user_config = Path(config_home) / "config.toml"
    if not user_config.exists():
        raise FileNotFoundError(
            f"Kimi Code config not found at {user_config}. "
            f"Pass --kimi-config-home with the correct directory."
        )
    config_text = user_config.read_text(encoding="utf-8")

    # Strip existing OV hooks (idempotency for repeated runs)
    config_text = _strip_ov_hooks_from_toml(config_text)

    # Prepare ov_home directory
    ov_home_path = Path(ov_home).resolve()
    if " " in str(ov_home_path):
        raise ValueError(
            f"ov_home path must not contain spaces: {ov_home_path}. "
            f"Kimi Code hook command parsing breaks on spaces in the path. "
            f"Use a no-space directory like D:/ov_eval/kimi."
        )
    hooks_dir = ov_home_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Write hook script
    (hooks_dir / "auto-recall.mjs").write_text(KIMI_HOOK_SCRIPT, encoding="utf-8")

    # Append OV hook to config
    hook_dir_str = str(hooks_dir).replace("\\", "/")
    ov_hook = (
        f'\n[[hooks]]\n'
        f'event = "UserPromptSubmit"\n'
        f'command = "node {hook_dir_str}/auto-recall.mjs"\n'
        f'timeout = 10\n'
    )
    config_text = config_text.rstrip() + "\n" + ov_hook
    (ov_home_path / "config.toml").write_text(config_text, encoding="utf-8")

    # Write mcp.json
    if mcp_tools:
        mcp_content = _kimi_mcp_json(ov_url)
    else:
        mcp_content = _kimi_mcp_json_empty()
    (ov_home_path / "mcp.json").write_text(mcp_content, encoding="utf-8")

    return str(ov_home_path)


def write_hermes_ov_files(
    ov_home: str,
    *,
    mcp_tools: bool,
    ov_url: str,
    config_home: str,
) -> str:
    """Write hermes OV config files to ov_home.

    Reads the user's actual config.yaml from config_home, injects the
    OV pre_llm_call hook, sets hooks_auto_accept: true, and optionally
    adds the OV MCP server.

    Creates:
      {ov_home}/hooks/auto-recall.mjs  - hook script
      {ov_home}/config.yaml            - hermes config with OV hook injected

    Returns the absolute path to ov_home (for HERMES_HOME env var).

    Raises FileNotFoundError if the user's config.yaml does not exist.
    """
    import yaml

    user_config = Path(config_home) / "config.yaml"
    if not user_config.exists():
        raise FileNotFoundError(
            f"Hermes config not found at {user_config}. "
            f"Pass --hermes-config-home with the correct directory."
        )

    with open(user_config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Prepare ov_home directory
    ov_home_path = Path(ov_home).resolve()
    if " " in str(ov_home_path):
        raise ValueError(
            f"ov_home path must not contain spaces: {ov_home_path}. "
            f"Hermes hook command parsing breaks on spaces in the path. "
            f"Use a no-space directory like D:/ov_eval/hermes."
        )
    hooks_dir = ov_home_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Write hook script
    (hooks_dir / "auto-recall.mjs").write_text(HERMES_HOOK_SCRIPT, encoding="utf-8")

    # Modify hooks section
    hook_dir_str = str(hooks_dir).replace("\\", "/")
    hook_command = f"node {hook_dir_str}/auto-recall.mjs"

    if not config:
        config = {}
    if config.get("hooks") is None:
        config["hooks"] = {}
    hooks = config["hooks"]

    if hooks.get("pre_llm_call") is None:
        hooks["pre_llm_call"] = []
    pre_llm_call = hooks["pre_llm_call"]

    # Remove existing OV hooks (idempotency for repeated runs)
    pre_llm_call = [
        h for h in pre_llm_call
        if "auto-recall.mjs" not in str(h.get("command", ""))
    ]

    # Add OV hook
    pre_llm_call.append({
        "command": hook_command,
        "timeout": 10,
    })
    hooks["pre_llm_call"] = pre_llm_call

    # Ensure headless mode triggers hooks
    config["hooks_auto_accept"] = True

    # MCP servers
    if mcp_tools:
        if config.get("mcp_servers") is None:
            config["mcp_servers"] = {}
        config["mcp_servers"]["openviking"] = {
            "url": f"{ov_url}/mcp",
            "enabled": True,
            "timeout": 120,
        }
    else:
        if config.get("mcp_servers") and "openviking" in config["mcp_servers"]:
            del config["mcp_servers"]["openviking"]

    # Write config
    with open(ov_home_path / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False,
                  allow_unicode=True)

    return str(ov_home_path)
