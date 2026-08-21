"""Shared subprocess runner for local CLI agent plugins.

Provides CLIAgentRunner (subprocess execution + log collection) and two
helper functions (create_memory_client, inject_memories_to_client) that
are shared across CLI agent plugins (claude_code, codex, openclaw, hermes).

All plugins use CLIAgentRunner.run() to invoke the agent CLI, capture
stdout/stderr, and accumulate logs for getlog(). Each plugin parses the
agent-specific output format itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backends.memory_types import MemoryClient, NullMemoryClient


# ------------------------------------------------------------------ #
#  Run result                                                         #
# ------------------------------------------------------------------ #

@dataclass
class CLIRunResult:
    """Result of a single CLIAgentRunner.run() call."""

    stdout: str
    stderr: str
    returncode: int
    elapsed_s: float
    timed_out: bool = False
    effective_timeout_s: float = 0.0


# ------------------------------------------------------------------ #
#  Runner                                                             #
# ------------------------------------------------------------------ #

class CLIAgentRunner:
    """Subprocess runner for local CLI agents.

    Thread-safe: each run() call is an independent subprocess, and the
    internal log list is protected by a lock. Multiple threads can call
    run() concurrently on the same runner instance.

    stderr is truncated to 2000 characters per entry to prevent log
    explosion from verbose agents.
    """

    _STDERR_TRUNCATE = 2000

    def __init__(self, binary: str, default_timeout_s: float = 300):
        self.binary = binary
        self.default_timeout_s = default_timeout_s
        self._logs: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def run(
        self,
        args: list[str],
        *,
        timeout_s: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CLIRunResult:
        """Execute the agent CLI, capture output, and record a log entry.

        Returns a CLIRunResult. Does not raise on non-zero exit or timeout;
        the caller inspects returncode / timed_out.
        """
        cmd = [self.binary, *args]
        effective_timeout = timeout_s if timeout_s else self.default_timeout_s
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                cwd=cwd,
                env={**os.environ, **env} if env else None,
            )
            elapsed = time.monotonic() - t0
            self._record(cmd, result, elapsed)
            return CLIRunResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                returncode=result.returncode,
                elapsed_s=elapsed,
                effective_timeout_s=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - t0
            self._record(cmd, None, elapsed, timed_out=True)
            return CLIRunResult(
                stdout="",
                stderr=str(exc),
                returncode=-1,
                elapsed_s=elapsed,
                timed_out=True,
                effective_timeout_s=effective_timeout,
            )

    def get_logs_json(self) -> str:
        """Return accumulated logs as a JSON string (for plugin.getlog())."""
        with self._lock:
            return json.dumps(self._logs, indent=2, ensure_ascii=False)

    def _record(
        self,
        cmd: list[str],
        result: subprocess.CompletedProcess | None,
        elapsed: float,
        *,
        timed_out: bool = False,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "command": cmd,
            "elapsed_s": round(elapsed, 3),
            "timed_out": timed_out,
        }
        if result is not None:
            entry["returncode"] = result.returncode
            entry["stdout_len"] = len(result.stdout or "")
            entry["stderr"] = (result.stderr or "")[: self._STDERR_TRUNCATE]
        with self._lock:
            self._logs.append(entry)


# ------------------------------------------------------------------ #
#  Memory client helpers                                              #
# ------------------------------------------------------------------ #

def create_memory_client(config: dict) -> MemoryClient:
    """Create a MemoryClient from --memory-backend config.

    Returns NullMemoryClient when no backend is configured. When a backend
    is selected, creates EchoMemClient or OpenVikingClient with the same
    parameter pattern used by vikingbot and echo_agent plugins.

    For benchmark runs (benchmark_name + run_id set, not resume), calls
    provision_isolated_identity for identity isolation.
    """
    backend = config.get("memory_backend", "")
    if not backend:
        return NullMemoryClient()

    auth_key = config.get("echomem_auth_key", "")
    account = config.get("account", "default")
    user_id = config.get("user_id", "default")
    agent_id = config.get("agent_id", "default")
    workspace = config.get("workspace", "")
    timeout_s = float(config.get("timeout_s", 60.0))
    max_retries = int(config.get("max_retries", 3))

    if backend == "openviking":
        from backends.openviking.client import OpenVikingClient

        client = OpenVikingClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:19080"),
            api_key=auth_key,
            account=account,
            user_id=user_id,
            agent_id=agent_id,
            workspace=workspace,
            timeout_s=timeout_s,
            max_retries=max_retries,
        )
    elif backend == "echomem":
        from backends.echomem.client import EchoMemClient

        client = EchoMemClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
            auth_key=auth_key,
            account=account,
            user_id=user_id,
            agent_id=agent_id,
            workspace=workspace,
            timeout_s=timeout_s,
            max_retries=max_retries,
            log_access_key=config.get("echomem_log_access_key", ""),
        )
    else:
        return NullMemoryClient()

    # Identity isolation for benchmark runs (same pattern as vikingbot).
    benchmark_name = config.get("benchmark_name", "")
    run_id = config.get("run_id", "")
    resume_qa = bool(config.get("resume_qa", ""))
    if benchmark_name and run_id and not resume_qa:
        label = f"eval-{benchmark_name}-{run_id}"[:120]
        client.provision_isolated_identity(label)

    return client


def inject_memories_to_client(
    client: MemoryClient,
    memories: list[dict],
    *,
    session_id: str = "",
    commit_timeout_s: float = 0.0,
    commit_poll_interval_s: float = 2.0,
) -> str:
    """Inject memories into a MemoryClient.

    No-op for NullMemoryClient (returns session_id unchanged). For real
    clients: open_session -> add_message x N -> commit_session -> poll_commit.

    Raises RuntimeError if commit does not reach 'completed' status.
    """
    if isinstance(client, NullMemoryClient):
        return session_id

    sid = session_id or client.open_session(title="inject")
    for mem in memories:
        text = str(mem.get("text") or "")
        if text:
            client.add_message(
                sid,
                "user",
                text,
                created_at=str(mem.get("time") or ""),
            )
    archive_id = client.commit_session(sid)
    commit = client.poll_commit(
        sid,
        archive_id,
        timeout_s=commit_timeout_s,
        poll_interval_s=commit_poll_interval_s,
    )
    if commit.status != "completed":
        raise RuntimeError(
            f"memory injection failed: status={commit.status} error={commit.error}"
        )
    return sid
