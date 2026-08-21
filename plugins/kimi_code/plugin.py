"""Kimi Code CLI agent plugin.

Wraps the `kimi` CLI (Kimi Code) in prompt mode. Each send_message()
call spawns a subprocess with `-p "<message>" --output-format stream-json`,
parses the JSONL output to extract the assistant response and the
kimi-generated session ID.

Session management: kimi-code generates its own session IDs. The plugin
maintains a mapping from the harness session_id to the real kimi session
ID. The first call to a new session runs without -S; subsequent calls
use -S <kimi_session_id> to continue the conversation.

When the harness passes an empty session_id, each call is independent:
no -S flag, no mapping stored. This is the correct behaviour for
benchmarks (e.g. LoCoMo QA) where every question should be answered in
isolation.

For non-empty session_id, a per-session lock serialises all send_message
calls so that concurrent workers cannot race on session creation or
issue overlapping subprocess calls to the same kimi session.

Note: --yolo and --auto cannot be combined with --prompt. In prompt mode
the agent runs with default permissions. For memory evaluation this is
sufficient since the agent only needs to answer questions.

OpenViking integration: when --kimi-ov-home is set, the plugin writes
OV config files (config.toml + hooks/auto-recall.mjs + mcp.json) to that
directory and sets KIMI_CODE_HOME to point there. A UserPromptSubmit hook
queries the OV recall API and injects an <openviking-context> block.
The --kimi-mcp-tools flag controls whether the OV MCP server is configured.
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
    KIMI_CODE_HOME_ENV,
    OV_ENV_ACCOUNT,
    OV_ENV_SIDECAR_PATH,
    OV_ENV_USER,
    OV_URL_DEFAULT,
    build_ov_env,
    write_kimi_ov_files,
)


class KimiCodePlugin(AgentPlugin):
    """Kimi Code CLI agent (subprocess-based).

    Invokes `kimi -p "<msg>" --output-format stream-json` per send_message.
    Session continuity via -S <kimi_session_id>. Thread-safe at the
    subprocess level.
    """

    descriptor = AgentDescriptor(
        id="kimi_code",
        name="Kimi Code",
        description="Kimi Code CLI agent invoked via subprocess in prompt mode.",
        capabilities=("cli_subprocess", "memory_injection"),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_qa_args(parser)
        g = parser.add_argument_group("Kimi Code")
        g.add_argument(
            "--kimi-binary",
            default=os.getenv("KIMI_BINARY", "kimi"),
            help="Path to the kimi CLI binary (default: kimi)",
        )
        g.add_argument(
            "--kimi-model",
            default=os.getenv("KIMI_MODEL", ""),
            help="Model alias to pass to -m (default: agent's built-in)",
        )
        g.add_argument(
            "--kimi-timeout-s",
            type=float,
            default=float(os.getenv("KIMI_TIMEOUT_S", "300")),
            help="Per-call subprocess timeout in seconds (default: 300)",
        )
        g.add_argument(
            "--kimi-workdir",
            default=os.getenv("KIMI_WORKDIR", ""),
            help="Working directory for kimi (default: current directory)",
        )
        # OpenViking integration
        gov = parser.add_argument_group("Kimi Code OpenViking")
        gov.add_argument(
            "--kimi-ov-home",
            default=os.getenv("KIMI_OV_HOME", ""),
            help="Directory for OV-enabled kimi config (sets KIMI_CODE_HOME)",
        )
        gov.add_argument(
            "--kimi-config-home",
            default="",
            help="Path to user's kimi-code config directory (e.g. ~/.kimi-code). "
                 "Required when --kimi-ov-home is set.",
        )
        gov.add_argument(
            "--kimi-mcp-tools",
            action="store_true",
            default=False,
            help="Enable OpenViking MCP server for kimi (requires --kimi-ov-home)",
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
            config.get("kimi_binary", "kimi"),
            config.get("kimi_timeout_s", 300),
        )
        self._model = config.get("kimi_model", "")
        self._workdir = config.get("kimi_workdir", "") or None
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))
        self.memory_client = create_memory_client(config)
        # Maps harness session_id -> kimi-generated session_id
        self._kimi_sessions: dict[str, str] = {}
        # Per-session locks for serialising concurrent access to the same
        # kimi session (non-empty session_id only).
        self._session_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # OpenViking integration
        self._ov_home = config.get("kimi_ov_home", "") or ""
        self._config_home = config.get("kimi_config_home", "") or ""
        self._ov_mcp_tools = bool(config.get("kimi_mcp_tools", False))
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
        return f"eval_kimi_{uuid.uuid4().hex[:12]}"

    def _parse_stream_json(
        self, stdout: str
    ) -> tuple[str, str | None, dict, int]:
        """Parse JSONL output from `kimi -p ... --output-format stream-json`.

        Returns ``(response_text, kimi_session_id, tool_audit, iterations)``.
        The session_id is extracted from the meta line; None if not found.

        Non-JSON lines are skipped. In stream-json mode, non-JSON lines are
        hook notifications (e.g. "UserPromptSubmit hook") or hook stdout
        (e.g. <openviking-context> blocks), not LLM response text.

        Additionally, kimi-code wraps the UserPromptSubmit hook's stdout in
        a JSON line with role=assistant. We filter out any assistant line
        whose content contains the <openviking-context> tag, which is the
        OV recall hook's output marker.

        Tool calls are parsed from assistant lines containing ``tool_calls``
        and paired with the subsequent ``role=tool`` result lines.
        Iterations counts assistant lines that carry visible content (not
        hook output).
        """
        text_parts: list[str] = []
        kimi_session_id: str | None = None
        tools_used: list[str] = []
        tool_calls_audit: list[dict] = []
        iterations = 0
        # Pending tool_call entries awaiting their tool-result lines, keyed
        # by tool_call_id so we can attach the result preview.
        pending: dict[str, dict] = {}

        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON line (hook output, debug, banner) -- skip.
                continue

            role = obj.get("role", "")
            if role == "assistant":
                content = obj.get("content", "")
                is_hook = content and "<openviking-context>" in content
                if content and not is_hook:
                    text_parts.append(content)
                    iterations += 1

                tcs = obj.get("tool_calls") or []
                for tc in tcs:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    tc_id = tc.get("id", "")
                    args_str = func.get("arguments", "")
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args = {"_raw": args_str}
                    entry = {
                        "name": name,
                        "arguments": args,
                        "result_preview": "",
                    }
                    tool_calls_audit.append(entry)
                    if tc_id:
                        pending[tc_id] = entry
                    if name and name not in tools_used:
                        tools_used.append(name)

            elif role == "tool":
                tool_call_id = obj.get("tool_call_id", "")
                content = obj.get("content", "")
                entry = pending.pop(tool_call_id, None)
                if entry is not None:
                    entry["result_preview"] = content[:500]

            elif role == "meta":
                if obj.get("type") == "session.resume_hint":
                    kimi_session_id = obj.get("session_id")

        tool_audit = {
            "schema_version": 1,
            "tools_used": tools_used,
            "tool_calls": tool_calls_audit,
        }

        return "\n".join(text_parts), kimi_session_id, tool_audit, iterations

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

    @staticmethod
    def _read_wire_jsonl_usage(
        kimi_session_id: str | None,
        kimi_code_home: str | None,
    ) -> tuple[int, int]:
        """Read token usage from kimi-code's internal wire.jsonl.

        Kimi writes ``session_index.jsonl`` at the kimi-code home root,
        mapping sessionId -> sessionDir. Each sessionDir contains
        ``agents/main/wire.jsonl`` with per-LLM-call usage records.

        Returns ``(prompt_tokens, completion_tokens)`` summed across all
        usage records in the session. Returns ``(0, 0)`` when the session
        or files cannot be found.
        """
        if not kimi_session_id or not kimi_code_home:
            return 0, 0

        index_path = Path(kimi_code_home) / "session_index.jsonl"
        if not index_path.exists():
            return 0, 0

        session_dir: str | None = None
        try:
            for line in index_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("sessionId") == kimi_session_id:
                    session_dir = entry.get("sessionDir")
                    break
        except OSError:
            return 0, 0

        if not session_dir:
            return 0, 0

        wire_path = Path(session_dir) / "agents" / "main" / "wire.jsonl"
        if not wire_path.exists():
            return 0, 0

        prompt_tokens = 0
        completion_tokens = 0
        try:
            for line in wire_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "usage.record":
                    continue
                usage = record.get("usage") or {}
                prompt_tokens += int(usage.get("inputOther", 0))
                prompt_tokens += int(usage.get("inputCacheRead", 0))
                completion_tokens += int(usage.get("output", 0))
        except OSError:
            return 0, 0

        return prompt_tokens, completion_tokens

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        """Get or create a lock for serialising access to a kimi session."""
        with self._locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

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

        args = ["-p", full_message, "--output-format", "stream-json"]
        if self._model:
            args += ["-m", self._model]

        # Copy the shared OV env per call so we can inject a unique
        # sidecar path for hook telemetry. When OV is not enabled,
        # env stays None (subprocess inherits the default environment).
        env = self._ov_env
        sidecar_path = ""
        if env is not None:
            env = dict(env)
            # Refresh account/user — memory_client identity may have been
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

        session_lock = (
            self._get_session_lock(session_id) if session_id else nullcontext()
        )
        with session_lock:
            kimi_sid = (
                self._kimi_sessions.get(session_id) if session_id else None
            )
            run_args = args + ["-S", kimi_sid] if kimi_sid else args

            result = self._runner.run(
                run_args, timeout_s=timeout_s, cwd=self._workdir, env=env,
            )

            if result.timed_out:
                _cleanup_sidecar(sidecar_path)
                return AgentResponse(
                    error=f"kimi timed out after {result.effective_timeout_s}s",
                    extra={
                        "elapsed_s": result.elapsed_s,
                        "timed_out": True,
                        "qa_profile": self.qa_profile,
                    },
                )

            if result.returncode != 0:
                _cleanup_sidecar(sidecar_path)
                return AgentResponse(
                    error=result.stderr.strip() or f"kimi exited with code {result.returncode}",
                    extra={
                        "elapsed_s": result.elapsed_s,
                        "returncode": result.returncode,
                        "qa_profile": self.qa_profile,
                    },
                )

            text, parsed_kimi_sid, tool_audit, iterations = (
                self._parse_stream_json(result.stdout)
            )

            # Store mapping only for non-empty session_id.
            if parsed_kimi_sid and session_id and session_id not in self._kimi_sessions:
                self._kimi_sessions[session_id] = parsed_kimi_sid

        # ---- Collect telemetry after subprocess completes ----

        effective_sid = parsed_kimi_sid or kimi_sid
        kimi_code_home = ""
        if env is not None:
            kimi_code_home = env.get(KIMI_CODE_HOME_ENV, "")
        if not kimi_code_home:
            kimi_code_home = os.environ.get(KIMI_CODE_HOME_ENV, "")

        prompt_tokens, completion_tokens = self._read_wire_jsonl_usage(
            effective_sid, kimi_code_home,
        )
        memory_items, retrieval_latency_s, retrieval_error = (
            self._read_hook_sidecar(sidecar_path)
        )

        _cleanup_sidecar(sidecar_path)

        return AgentResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            memory_items=memory_items,
            extra={
                "elapsed_s": result.elapsed_s,
                "kimi_session_id": effective_sid,
                "qa_profile": self.qa_profile,
                "tool_call_count": len(tool_audit.get("tool_calls", [])),
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

        When ov_home is configured, writes OV config files (config.toml,
        hook script, mcp.json) to ov_home and returns env dict with
        KIMI_CODE_HOME + OPENVIKING_* vars. Returns None when ov_home
        is not set (agent uses the user's default config).
        """
        if not self._ov_home:
            return None

        if not self._config_home:
            raise ValueError(
                "--kimi-config-home is required when --kimi-ov-home is set. "
                "Pass the path to your kimi-code config directory."
            )

        # Write config files to ov_home once during setup (not per call)
        # to avoid concurrent file-write races when multiple workers run
        # with empty session_id (e.g. locomo QA with concurrency > 1).
        resolved_home = write_kimi_ov_files(
            self._ov_home,
            mcp_tools=self._ov_mcp_tools,
            ov_url=self._ov_url,
            config_home=self._config_home,
        )

        env: dict[str, str] = {KIMI_CODE_HOME_ENV: resolved_home}
        # Fall back to the memory client's provisioned identity when --ov-account
        # / --ov-user are not explicitly set, so the recall hook queries the same
        # OV account that memories were imported under.
        account = self._ov_account or getattr(self.memory_client, "account", "")
        user = self._ov_user or getattr(self.memory_client, "user_id", "")
        env.update(build_ov_env(self._ov_url, self._ov_api_key, account, user))
        return env

    def getlog(self) -> str:
        return self._runner.get_logs_json()


def _cleanup_sidecar(sidecar_path: str) -> None:
    """Delete the sidecar file if it exists. Never raises."""
    if not sidecar_path:
        return
    try:
        Path(sidecar_path).unlink(missing_ok=True)
    except OSError:
        pass
