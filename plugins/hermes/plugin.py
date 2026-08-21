"""Hermes CLI agent plugin.

Wraps the `hermes` CLI in non-interactive mode. Each send_message() call
spawns a subprocess, captures stdout (the agent's response), and
accumulates stderr as logs.

Uses ``hermes chat -q "<msg>" -Q --yolo --source tool`` for non-interactive
single-query execution. Hermes -Q prints the response text to stdout and
``session_id: <id>`` to stderr. Session continuity is maintained by mapping
the harness session_id to hermes's own session_id (captured from stderr)
and passing it via ``--resume`` on subsequent calls.

Telemetry (token usage, tool calls, iterations) is collected by running
``hermes sessions export - --session-id <id>`` after each chat call and
parsing the JSONL session dump.

OpenViking integration: when --hermes-ov-home is set, the plugin writes
OV config files (config.yaml + hooks/auto-recall.mjs) to that directory
and sets HERMES_HOME to point there. A pre_llm_call hook queries the OV
recall API and injects an <openviking-context> block. The --hermes-mcp-tools
flag controls whether the OV MCP server is configured in config.yaml.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import uuid
from contextlib import nullcontext
from pathlib import Path

from plugins.base import AgentDescriptor, AgentPlugin, AgentResponse
from backends.memory_args import add_memory_backend_args
from backends.memory_types import NullMemoryClient
from shared.cli_agent_runner import (
    CLIAgentRunner,
    create_memory_client,
    inject_memories_to_client,
)
from shared.eval_base import add_llm_args, add_qa_args
from shared.ov_constants import (
    HERMES_HOME_ENV,
    OV_ENV_ACCOUNT,
    OV_ENV_SIDECAR_PATH,
    OV_ENV_USER,
    OV_URL_DEFAULT,
    build_ov_env,
    write_hermes_ov_files,
)


class HermesPlugin(AgentPlugin):
    """Hermes CLI agent (subprocess-based).

    Invokes ``hermes chat -q "<msg>" -Q --yolo`` per send_message.
    Session continuity via ``--resume`` with hermes's own session_id
    (captured from ``-Q`` output). When the harness passes an empty
    session_id, each call is independent. For non-empty session_id,
    a per-session lock serialises concurrent workers so that
    overlapping subprocess calls to the same hermes session cannot occur.
    """

    descriptor = AgentDescriptor(
        id="hermes",
        name="Hermes Agent",
        description="Hermes CLI agent invoked via subprocess.",
        capabilities=("cli_subprocess", "memory_injection"),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_qa_args(parser)
        g = parser.add_argument_group("Hermes")
        g.add_argument(
            "--hermes-binary",
            default=os.getenv("HERMES_BINARY", "hermes"),
            help="Path to the hermes CLI binary (default: hermes)",
        )
        g.add_argument(
            "--hermes-timeout-s",
            type=float,
            default=float(os.getenv("HERMES_TIMEOUT_S", "300")),
            help="Per-call subprocess timeout in seconds (default: 300)",
        )
        g.add_argument(
            "--hermes-workdir",
            default=os.getenv("HERMES_WORKDIR", ""),
            help="Working directory for hermes (default: current directory)",
        )
        # OpenViking integration
        gov = parser.add_argument_group("Hermes OpenViking")
        gov.add_argument(
            "--hermes-ov-home",
            default=os.getenv("HERMES_OV_HOME", ""),
            help="Directory for OV-enabled hermes config (sets HERMES_HOME)",
        )
        gov.add_argument(
            "--hermes-config-home",
            default="",
            help="Path to user's hermes config directory (e.g. ~/.hermes). "
                 "Required when --hermes-ov-home is set.",
        )
        gov.add_argument(
            "--hermes-mcp-tools",
            action="store_true",
            default=False,
            help="Enable OpenViking MCP server for hermes (requires --hermes-ov-home)",
        )
        gov.add_argument(
            "--ov-url",
            default=os.getenv("OPENVIKING_URL", OV_URL_DEFAULT),
            help="OpenViking server URL (default: http://127.0.0.1:1933)",
        )
        gov.add_argument("--ov-api-key", default=os.getenv("OPENVIKING_API_KEY", ""),
                         help="OpenViking API key")
        gov.add_argument("--ov-account", default=os.getenv("OPENVIKING_ACCOUNT", ""),
                         help="OpenViking account ID")
        gov.add_argument("--ov-user", default=os.getenv("OPENVIKING_USER", ""),
                         help="OpenViking user ID")
        add_memory_backend_args(parser, with_backend_choice=True)

    def setup(self, config: dict) -> None:
        self._runner = CLIAgentRunner(
            config.get("hermes_binary", "hermes"),
            config.get("hermes_timeout_s", 300),
        )
        self._workdir = config.get("hermes_workdir", "") or None
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))
        self.memory_client = create_memory_client(config)
        # Map harness session_id -> hermes session_id for --resume continuity.
        self._session_map: dict[str, str] = {}
        # Per-session locks for serialising concurrent access to the same
        # hermes session (non-empty session_id only).
        self._session_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # OpenViking integration
        self._ov_home = config.get("hermes_ov_home", "") or ""
        self._config_home = config.get("hermes_config_home", "") or ""
        self._ov_mcp_tools = bool(config.get("hermes_mcp_tools", False))
        self._ov_url = config.get("ov_url", "") or OV_URL_DEFAULT
        self._ov_api_key = config.get("ov_api_key", "")
        self._ov_account = config.get("ov_account", "")
        self._ov_user = config.get("ov_user", "")
        # Write OV config files once during setup (not per send_message call)
        # to avoid concurrent file-write races when multiple workers run with
        # empty session_id (e.g. locomo QA with concurrency > 1).
        self._ov_env = self._build_ov_env()

    def inject_memories(
        self,
        memories: list[dict],
        *,
        backend: str = "",
        session_id: str = "",
    ) -> str:
        return inject_memories_to_client(
            self.memory_client,
            memories,
            session_id=session_id,
            commit_timeout_s=self._commit_timeout_s,
            commit_poll_interval_s=self._commit_poll_interval_s,
        )

    def create_session(self, title: str = "") -> str:
        return f"eval_hermes_{uuid.uuid4().hex[:12]}"

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        """Get or create a lock for serialising access to a hermes session."""
        with self._locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    # ------------------------------------------------------------------ #
    #  Telemetry helpers                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _read_hook_sidecar(
        sidecar_path: str,
    ) -> tuple[list[dict], float, str]:
        """Read recall telemetry from the OV hook sidecar JSON file.

        Returns ``(memory_items, retrieval_latency_s, retrieval_error)``.
        If the file does not exist or cannot be parsed, returns empty
        defaults so callers never crash on a missing sidecar.
        """
        if not sidecar_path:
            return [], 0.0, ""
        try:
            data = json.loads(
                Path(sidecar_path).read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return [], 0.0, ""

        items = data.get("items") or []
        latency_ms = data.get("latency_ms") or 0
        error = data.get("error") or ""
        return items, latency_ms / 1000.0, error

    def _read_session_export(
        self,
        hermes_session_id: str,
        env: dict[str, str] | None,
    ) -> tuple[int, int, list[dict], list[str], int]:
        """Export the hermes session and extract telemetry.

        Runs ``hermes sessions export - --session-id <id>`` to get a JSONL
        dump of the session, then parses it for token usage, tool calls,
        and iteration count.

        When *hermes_session_id* is empty (hermes -Q occasionally omits the
        ``session_id:`` line on stderr for long-running sessions), falls
        back to ``hermes sessions export -`` without ``--session-id`` to
        export the most recently created session.

        Returns ``(prompt_tokens, completion_tokens, tool_calls_audit,
        tools_used, iterations)``.  On any error returns zeros/empties
        so callers never crash.
        """
        if hermes_session_id:
            args = ["sessions", "export", "-", "--session-id", hermes_session_id]
        else:
            # Fallback: export the most recent session (best-effort when
            # hermes -Q didn't output session_id to stderr).
            args = ["sessions", "export", "-"]
        try:
            result = self._runner.run(
                args, timeout_s=30, cwd=self._workdir, env=env,
            )
        except Exception:
            return 0, 0, [], [], 1
        if result.returncode != 0:
            return 0, 0, [], [], 1

        stdout = result.stdout.strip()
        if not stdout:
            return 0, 0, [], [], 1

        # JSONL: first (and usually only) line is the session object.
        try:
            data = json.loads(stdout.splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            return 0, 0, [], [], 1

        prompt_tokens = data.get("input_tokens", 0) or 0
        completion_tokens = data.get("output_tokens", 0) or 0
        iterations = data.get("api_call_count", 1) or 1

        tool_calls_audit: list[dict] = []
        tools_used: list[str] = []
        for msg in data.get("messages", []):
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                raw_args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args)
                    except (json.JSONDecodeError, ValueError):
                        parsed_args = raw_args
                else:
                    parsed_args = raw_args
                tool_calls_audit.append({
                    "name": name,
                    "arguments": parsed_args,
                })
                if name and name not in tools_used:
                    tools_used.append(name)

        return prompt_tokens, completion_tokens, tool_calls_audit, tools_used, iterations

    def send_message(
        self,
        session_id: str,
        message: str,
        context_path: str = "/",
        *,
        extra: dict | None = None,
    ) -> AgentResponse:
        extra = extra or {}
        timeout_s = extra.get("question_timeout_s", self._runner.default_timeout_s)
        system_append = extra.get("system_prompt_append", "")

        full_message = f"{system_append}\n\n{message}" if system_append else message

        # Copy the shared OV env per call so we can inject a unique
        # sidecar path for hook telemetry.
        env = self._ov_env
        sidecar_path = ""
        if env is not None:
            env = dict(env)
            # Refresh account/user - memory_client identity may have been
            # updated after plugin init (e.g. --reuse-memory-from sets
            # the resumed tenant on memory_client post-construction).
            account = self._ov_account or getattr(self.memory_client, "account", "")
            user = self._ov_user or getattr(self.memory_client, "user_id", "")
            if account:
                env[OV_ENV_ACCOUNT] = account
            if user:
                env[OV_ENV_USER] = user
            sidecar_path = str(
                Path(self._ov_home) / f"recall_{uuid.uuid4().hex}.json"
            )
            env[OV_ENV_SIDECAR_PATH] = sidecar_path

        # Empty session_id: each call is independent (no --resume).
        # Non-empty session_id: serialise per-session to prevent concurrent
        # subprocess access to the same hermes session.
        session_lock = (
            self._get_session_lock(session_id) if session_id else nullcontext()
        )
        with session_lock:
            args = ["chat", "-q", full_message, "-Q", "--yolo", "--source", "tool"]
            # Resume the hermes session if we have a mapping for this
            # harness session_id (set on a previous call).
            hermes_sid = self._session_map.get(session_id, "") if session_id else ""
            if hermes_sid:
                args += ["--resume", hermes_sid]

            result = self._runner.run(
                args, timeout_s=timeout_s, cwd=self._workdir, env=env,
            )

            if result.timed_out:
                _cleanup_sidecar(sidecar_path)
                return AgentResponse(
                    error=f"hermes timed out after {result.effective_timeout_s}s",
                    extra={
                        "elapsed_s": result.elapsed_s,
                        "timed_out": True,
                        "qa_profile": self.qa_profile,
                    },
                )

            if result.returncode != 0:
                _cleanup_sidecar(sidecar_path)
                return AgentResponse(
                    error=result.stderr.strip() or f"hermes exited with code {result.returncode}",
                    extra={
                        "elapsed_s": result.elapsed_s,
                        "returncode": result.returncode,
                        "qa_profile": self.qa_profile,
                    },
                )

            # Hermes -Q prints the response text to stdout and
            # "session_id: <id>" to stderr.
            text, hermes_session_id = _parse_hermes_output(
                result.stdout.strip(), result.stderr,
            )

            # Remember the hermes session_id for future --resume calls.
            if hermes_session_id and session_id:
                self._session_map[session_id] = hermes_session_id

        # Collect recall telemetry from the OV hook sidecar.
        memory_items, retrieval_latency_s, retrieval_error = (
            self._read_hook_sidecar(sidecar_path)
        )
        _cleanup_sidecar(sidecar_path)

        # Collect tool/token telemetry from the hermes session export.
        (
            prompt_tokens,
            completion_tokens,
            tool_calls_audit,
            tools_used,
            iterations,
        ) = self._read_session_export(hermes_session_id, env)

        tool_audit = {
            "schema_version": 1,
            "tools_used": tools_used,
            "tool_calls": tool_calls_audit,
        }

        return AgentResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            memory_items=memory_items,
            extra={
                "elapsed_s": result.elapsed_s,
                "qa_profile": self.qa_profile,
                "tool_call_count": len(tool_calls_audit),
                "iterations": iterations,
                "retrieval_latency_s": retrieval_latency_s,
                "retrieval_error": retrieval_error,
                "trace": {
                    "tool_audit": tool_audit,
                },
            },
        )

    def _build_ov_env(self) -> dict[str, str] | None:
        """Build env vars for OpenViking integration.

        When ov_home is configured, writes OV config files (config.yaml,
        hook script) to ov_home and returns env dict with HERMES_HOME +
        OPENVIKING_* vars. Returns None when ov_home is not set (agent
        uses the user's default config).
        """
        if not self._ov_home:
            return None

        if not self._config_home:
            raise ValueError(
                "--hermes-config-home is required when --hermes-ov-home is set. "
                "Pass the path to your hermes config directory."
            )

        # Write config files to ov_home once during setup (not per call)
        # to avoid concurrent file-write races when multiple workers run
        # with empty session_id (e.g. locomo QA with concurrency > 1).
        resolved_home = write_hermes_ov_files(
            self._ov_home,
            mcp_tools=self._ov_mcp_tools,
            ov_url=self._ov_url,
            config_home=self._config_home,
        )

        env: dict[str, str] = {HERMES_HOME_ENV: resolved_home}
        # Fall back to the memory client's provisioned identity when --ov-account
        # / --ov-user are not explicitly set, so the recall hook queries the same
        # OV account that memories were imported under.
        account = self._ov_account or getattr(self.memory_client, "account", "")
        user = self._ov_user or getattr(self.memory_client, "user_id", "")
        env.update(build_ov_env(self._ov_url, self._ov_api_key, account, user))
        return env

    def getlog(self) -> str:
        return self._runner.get_logs_json()


def _parse_hermes_output(stdout: str, stderr: str = "") -> tuple[str, str]:
    """Parse hermes ``-Q`` output into ``(response_text, session_id)``.

    Hermes quiet mode prints the agent's response to **stdout** and
    ``session_id: <id>`` to **stderr**.  Returns ``(stdout, session_id)``.
    If the session_id line is absent, returns ``(stdout, "")``.
    """
    text = stdout
    source = stderr if stderr else stdout
    session_id = ""
    marker = "session_id:"
    idx = source.rfind(marker)
    if idx >= 0:
        session_id = source[idx + len(marker):].strip()
    return text, session_id


def _cleanup_sidecar(sidecar_path: str) -> None:
    """Delete the sidecar file if it exists. Never raises."""
    if not sidecar_path:
        return
    try:
        Path(sidecar_path).unlink(missing_ok=True)
    except OSError:
        pass
