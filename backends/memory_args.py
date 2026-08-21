"""Shared CLI argument declarations for memory backends.

Agent plugins that support memory injection call add_memory_backend_args()
in their add_arguments() to declare connection parameters and commit
timeouts. Plugins that support multiple backends pass with_backend_choice=True
to add --memory-backend.
"""

from __future__ import annotations

import argparse
import os


def add_memory_backend_args(parser: argparse.ArgumentParser, *, with_backend_choice: bool = False) -> None:
    """Declare memory backend connection args on *parser*.

    Called by agent plugins that support memory injection. The args cover
    both EchoMem and OpenViking connection parameters (they share the same
    CLI flag names for simplicity).

    Parameters
    ----------
    with_backend_choice:
        If True, add --memory-backend (choices: echomem/openviking, default:
        echomem). Used by plugins that support multiple backends (echo_agent,
        vikingbot). Single-backend plugins pass False.
    """
    g = parser.add_argument_group("Memory Backend")
    if with_backend_choice:
        g.add_argument(
            "--memory-backend",
            choices=["echomem", "openviking"],
            default="echomem",
            help="Memory backend for injection",
        )
    g.add_argument(
        "--echomem-url",
        default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"),
        help="Memory backend HTTP base URL",
    )
    g.add_argument("--echomem-auth-key", default=os.getenv("ECHOMEM_AUTH_KEY", ""), help="Memory backend auth key / API key")
    g.add_argument(
        "--echomem-log-access-key",
        default=os.getenv("ECHOMEM_LOG_ACCESS_KEY", ""),
        help="Privileged log query access key (EchoMem /api/logs, x_auth_key mode)",
    )
    g.add_argument("--account", default=os.getenv("ECHOMEM_ACCOUNT", "default"))
    g.add_argument("--user-id", default=os.getenv("ECHOMEM_USER_ID", "default"))
    g.add_argument("--agent-id", default=os.getenv("ECHOMEM_AGENT_ID", "default"))
    g.add_argument("--workspace", default=os.getenv("ECHOMEM_WORKSPACE", ""), help="Memory backend workspace path")
    g.add_argument(
        "--commit-timeout-s",
        type=float,
        default=0.0,
        help="Commit poll timeout in seconds (0 = wait indefinitely)",
    )
    g.add_argument(
        "--commit-poll-interval-s",
        type=float,
        default=2.0,
        help="Seconds between commit status polls",
    )
    g.add_argument(
        "--timeout-s",
        type=float,
        default=60.0,
        help="HTTP request timeout for memory backend calls",
    )
    g.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum retries for memory backend HTTP calls",
    )

