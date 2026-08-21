"""EchoAgent plugin: wraps EchoAgentClient for dynamic evaluation.

Design intent: this plugin owns every CLI argument and all HTTP logic
related to the EchoAgent backend (login, session management, prefill
simulation, SSE streaming). The dynamic evaluation flow calls only
AgentPlugin methods; the benchmark flow accesses agent_plugin.client
for low-level EchoAgentClient methods that don't fit the step-based
interface.

Memory injection is handled by the memory plugin's client directly
(open_session / add_message / commit / poll_commit), not by this plugin.
QA goes through the full EchoAgent pipeline (prefill + SSE streaming).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from typing import Any

from plugins.base import AgentPlugin, AgentResponse, AgentDescriptor, TypingResult
from plugins.echo_agent.client import EchoAgentClient
from backends.echomem.client import EchoMemClient
from backends.openviking.client import OpenVikingClient
from backends.memory_args import add_memory_backend_args
from shared.eval_base import add_llm_args

logger = logging.getLogger("agent.echo_agent")


class EchoAgentPlugin(AgentPlugin):
    """EchoAgent plugin for dynamic evaluation.

    QA goes through the full EchoAgent pipeline (prefill + SSE streaming).
    Memory injection supports both echomem and openviking backends, selected
    via --memory-backend (default: echomem).
    """

    descriptor = AgentDescriptor(
        id="echo_agent",
        name="EchoAgent",
        description="EchoAgent full pipeline (prefill + SSE streaming) for dynamic evaluation.",
        capabilities=(
            "typing_simulation",
            "prefill_pipeline",
            "sse_streaming",
            "memory_injection",
        ),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_memory_backend_args(parser, with_backend_choice=True)
        g = parser.add_argument_group("EchoAgent")
        g.add_argument("--echoagent-url",
                       default=os.environ.get("ECHOAGENT_URL", "http://127.0.0.1:31020"),
                       help="EchoAgent 后端地址")
        g.add_argument("--username",
                       default=os.environ.get("ECHOAGENT_TEST_USERNAME", "test_user"),
                       help="EchoAgent 登录用户名")
        g.add_argument("--password",
                       default=os.environ.get("ECHOAGENT_TEST_PASSWORD", ""),
                       help="EchoAgent 登录密码")
        g.add_argument("--memory-engine-endpoint",
                       default=os.environ.get("GLOBAL_MEMORY_ENGINE_ENDPOINT", "http://127.0.0.1:31030"),
                       help="echoagent 插件地址 (31030)")

    def setup(self, config: dict) -> None:
        echoagent_url = config.get("echoagent_url", "http://127.0.0.1:31020")
        username = config.get("username", "test_user")
        password = config.get("password", "")
        self._memory_engine_endpoint = config.get(
            "memory_engine_endpoint",
            "http://127.0.0.1:31030",
        )
        # Login to EchoAgent
        self.client = EchoAgentClient(echoagent_url, username, password)
        print(f"登录 EchoAgent ({echoagent_url})...")
        self.client.login()
        logger.info("登录成功 (user=%s, uuid=%s)", username, self.client.user_uuid)

        # Dynamic eval QA goes through EchoAgent -> echoagent plugin, which
        # uses agent_id="echoagent". Injection must use the same agent_id.
        self._agent_id = config.get("agent_id", "")
        if not self._agent_id or self._agent_id == "default":
            self._agent_id = "echoagent"
        config["agent_id"] = self._agent_id

        # Resolve auth_key so injection uses the same identity as retrieval
        auth_key = config.get("echomem_auth_key", "")
        if not auth_key:
            try:
                auth_key = self.client.get_memory_auth_key(self._memory_engine_endpoint)
            except Exception as e:
                logger.warning("解析 auth_key 失败: %s — 注入将不携带身份", e)
                auth_key = ""
        self._auth_key = auth_key
        config["echomem_auth_key"] = auth_key
        logger.info("agent_id=%s, auth_key=%s", self._agent_id, "已设置" if auth_key else "未设置")

        # Create memory client for injection (echomem or openviking)
        self._memory_backend = config.get("memory_backend", "echomem")
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))

        if self._memory_backend == "openviking":
            self.memory_client = OpenVikingClient(
                base_url=config.get("echomem_url", "http://127.0.0.1:19080"),
                api_key=auth_key,
                account=config.get("account", "default"),
                user_id=config.get("user_id", "default"),
                agent_id=self._agent_id,
                workspace=config.get("workspace", ""),
                timeout_s=float(config.get("timeout_s", 60.0)),
                max_retries=int(config.get("max_retries", 3)),
            )
        else:
            self.memory_client = EchoMemClient(
                base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
                auth_key=auth_key,
                account=config.get("account", "default"),
                user_id=config.get("user_id", "default"),
                agent_id=self._agent_id,
                workspace=config.get("workspace", ""),
                timeout_s=float(config.get("timeout_s", 60.0)),
                max_retries=int(config.get("max_retries", 3)),
                log_access_key=config.get("echomem_log_access_key", ""),
            )

        # Typing state (reset per round)
        self._pending_turn_id = ""
        self._typing_committed = False
        self._typing_memory_items: list[dict] = []

    def create_session(self, title: str = "") -> str:
        return self.client.create_session(title, self._memory_engine_endpoint)

    @property
    def supports_typing_simulation(self) -> bool:
        return True

    def simulate_typing(
        self,
        session_id: str,
        context_path: str,
        text: str,
        speed_ms: int = 200,
        jitter_ms: int = 20,
    ) -> TypingResult | None:
        """Simulate typing to trigger prefill.

        speed_ms < 50: fast mode -- single tick + finalize, no per-char delay.
        """
        import random

        # Reset typing state for this round
        self._pending_turn_id = ""
        self._typing_committed = False
        self._typing_memory_items = []

        client_turn_id = uuid.uuid4().hex[:12]
        committed = False

        if speed_ms < 50:
            tick_result = self.client.prefetch_tick(
                session_id, context_path, client_turn_id, 1, text,
            )
            if tick_result is None:
                return None
            time.sleep(0.5)
            finalize_result = self.client.prefetch_finalize(
                session_id, context_path, client_turn_id, text,
            )
            if finalize_result is not None:
                fin_data = finalize_result.get("data", finalize_result)
                committed = bool(fin_data.get("accepted"))
            # Store state for send_message
            self._pending_turn_id = client_turn_id
            self._typing_committed = committed
            return TypingResult(committed=committed)

        for i in range(1, len(text) + 1):
            draft = text[:i]
            tick_result = self.client.prefetch_tick(
                session_id, context_path, client_turn_id, i, draft,
            )
            if tick_result is None:
                return None
            tick_data = tick_result.get("data", tick_result)
            if not tick_data.get("accepted") and i == 1:
                self._pending_turn_id = client_turn_id
                self._typing_committed = False
                return TypingResult(committed=False)
            delay = speed_ms + random.randint(-jitter_ms, jitter_ms)
            time.sleep(max(10, delay) / 1000.0)

        finalize_result = self.client.prefetch_finalize(
            session_id, context_path, client_turn_id, text,
        )
        memory_items: list[dict] = []
        if finalize_result is not None:
            fin_data = finalize_result.get("data", finalize_result)
            committed = bool(fin_data.get("accepted"))
            memory_items = fin_data.get("memoryItems") or []

        # Store state for send_message
        self._pending_turn_id = client_turn_id
        self._typing_committed = committed
        self._typing_memory_items = memory_items
        return TypingResult(committed=committed, memory_items=memory_items)

    def send_message(
        self, session_id: str, message: str, context_path: str = "/",
        *, extra: dict | None = None,
    ) -> AgentResponse:
        """Send message to EchoAgent and stream the reply.

        Uses the prefill client_turn_id from the last simulate_typing call
        (if any), then clears the typing state.

        In benchmark mode, session_id may be empty (the benchmark passes
        memory-session IDs, not EchoAgent session IDs). In that case, create
        a fresh EchoAgent session for each question so QA is independent.
        """
        # Capture and clear typing state
        pending_turn_id = self._pending_turn_id
        committed = self._typing_committed
        memory_items = list(self._typing_memory_items)
        self._pending_turn_id = ""
        self._typing_committed = False
        self._typing_memory_items = []

        try:
            if not session_id:
                session_id = self.client.create_session(
                    f"qa-{extra.get('question_id', '')}" if extra else "qa",
                    self._memory_engine_endpoint,
                )
            msg_result = self.client.send_message(
                session_id, context_path, message, pending_turn_id,
            )
            msg_data = msg_result.get("data", msg_result)
            if msg_data.get("error"):
                return AgentResponse(
                    error=f"send failed: {msg_data.get('error')} {msg_data.get('message', '')}",
                    prefetch_committed=committed,
                    memory_items=memory_items,
                    extra={"qa_profile": self.qa_profile},
                )

            # Extract seq for streaming
            messages_list = msg_data.get("messages") or []
            seq = 0
            for m in reversed(messages_list):
                if m.get("status") in ("generating", "completed"):
                    seq = m.get("seq", 0)
                    break
            if not seq and messages_list:
                seq = messages_list[-1].get("seq", 0)
            if not seq:
                seq = msg_data.get("latestContextSeq") or 0

            # Stream reply
            reply_result = self.client.stream_reply(session_id, context_path, seq)

        except Exception as exc:
            logger.error("发送/接收失败: %s", exc)
            return AgentResponse(
                error=str(exc),
                prefetch_committed=committed,
                memory_items=memory_items,
                extra={"qa_profile": self.qa_profile},
            )

        reply = reply_result.get("reply") or ""
        ttft = reply_result.get("ttft_ms")
        done = reply_result.get("done_event") or {}
        metrics = done.get("metrics", {})
        done_memory_items = done.get("memoryItems") or []

        # memory_items 优先级：prefill（typing）> done 事件
        final_memory_items = memory_items if memory_items else done_memory_items

        return AgentResponse(
            text=reply,
            ttft_ms=round(metrics.get("ttft_ms", ttft), 1)
            if metrics.get("ttft_ms", ttft) is not None else None,
            prompt_tokens=int(metrics.get("prompt_tokens") or 0)
            or int(done.get("promptTokens") or done.get("prompt_tokens") or 0),
            completion_tokens=int(metrics.get("completion_tokens") or 0),
            cached_tokens=int(metrics.get("cached_tokens") or 0)
            or int(done.get("cachedTokens") or done.get("cached_tokens") or 0),
            prefetch_committed=committed,
            memory_items=final_memory_items,
            error=reply_result.get("error"),
            extra={
                "qa_profile": self.qa_profile,
                "retrieval_error": metrics.get(
                    "retrieval_error", reply_result.get("error") or ""
                ),
                "elapsed_s": metrics.get("elapsed_ms", 0) / 1000,
                "retrieval_latency_s": metrics.get("retrieval_latency_ms", 0) / 1000,
                "llm_latency_s": metrics.get("llm_latency_ms", 0) / 1000,
                "tool_call_count": int(metrics.get("tool_call_count", 0)),
                "iterations": int(metrics.get("turn_iterations", 1)),
                "trace": {
                    "metrics": metrics,
                    "model": metrics.get("model_name"),
                    "finish_reason": metrics.get("finish_reason"),
                    "tool_audit": done.get("toolAudit"),
                },
            },
        )

    def inject_memories(
        self,
        memories: list[dict],
        *,
        backend: str = "",
        session_id: str = "",
    ) -> str:
        if not session_id:
            session_id = self.memory_client.open_session(title="inject")
        for mem in memories:
            text = str(mem.get("text") or "")
            if text:
                self.memory_client.add_message(
                    session_id,
                    "user",
                    text,
                    created_at=str(mem.get("time") or ""),
                )
        archive_id = self.memory_client.commit_session(session_id)
        commit = self.memory_client.poll_commit(
            session_id,
            archive_id,
            timeout_s=self._commit_timeout_s,
            poll_interval_s=self._commit_poll_interval_s,
        )
        if commit.status != "completed":
            raise RuntimeError(
                f"memory injection failed: status={commit.status} error={commit.error}"
            )
        return session_id

    def getlog(self) -> str:
        """Fetch backend logs and return as JSON string."""
        if self._memory_backend == "openviking":
            return json.dumps(self.memory_client.fetch_console_logs(), ensure_ascii=False, indent=2)
        # echomem: only logs of this run's tenant/user (injected memories + QA).
        try:
            logs = self.memory_client.fetch_logs(
                tenant_id=self.memory_client.account,
                user_id=self.memory_client.user_id,
            )
            return json.dumps(logs, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)

    def teardown(self) -> None:
        pass
