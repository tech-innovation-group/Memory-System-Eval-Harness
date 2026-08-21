"""EchoAgent live plugin: test external EchoAgent deployment without typing simulation.

QA goes through the full EchoAgent pipeline (login, create session, send message,
SSE streaming) but does NOT simulate typing or trigger the prefill pipeline.
Memory injection is handled by the memory client directly (open_session /
add_message / commit / poll_commit), identical to echo_agent.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

from plugins.base import AgentPlugin, AgentResponse, AgentDescriptor
from plugins.echo_agent.client import EchoAgentClient
from backends.echomem.client import EchoMemClient
from backends.openviking.client import OpenVikingClient
from backends.memory_args import add_memory_backend_args
from shared.eval_base import add_llm_args

logger = logging.getLogger("agent.echoagent_live")


class EchoAgentLivePlugin(AgentPlugin):
    """EchoAgent live plugin for testing external deployments.

    QA goes through the full EchoAgent pipeline (SSE streaming) without typing
    simulation. Memory injection supports both echomem and openviking backends.
    """

    descriptor = AgentDescriptor(
        id="echoagent_live",
        name="EchoAgent Live",
        description="EchoAgent 外网部署评测插件（无打字模拟）。",
        capabilities=(
            "sse_streaming",
            "memory_injection",
        ),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_memory_backend_args(parser, with_backend_choice=True)
        g = parser.add_argument_group("EchoAgent Live")
        g.add_argument("--echoagent-url",
                       default=os.environ.get("ECHOAGENT_URL", "https://echo-agent.online"),
                       help="EchoAgent 后端地址")
        g.add_argument("--echoagent-api-prefix",
                       default=os.environ.get("ECHOAGENT_API_PREFIX", "/api"),
                       help="EchoAgent API 前缀（外网反代 /api -> /v1，本地直连 /v1）")
        g.add_argument("--username",
                       default=os.environ.get("ECHOAGENT_TEST_USERNAME", "test_user"),
                       help="EchoAgent 登录用户名")
        g.add_argument("--password",
                       default=os.environ.get("ECHOAGENT_TEST_PASSWORD", ""),
                       help="EchoAgent 登录密码")
        g.add_argument("--memory-engine-endpoint",
                       default=os.environ.get("GLOBAL_MEMORY_ENGINE_ENDPOINT",
                                              "http://8.134.127.8:31030"),
                       help="echoagent 插件地址 (31030)")

    def setup(self, config: dict) -> None:
        echoagent_url = config.get("echoagent_url", "https://echo-agent.online")
        api_prefix = config.get("echoagent_api_prefix", "/api")
        username = config.get("username", "test_user")
        password = config.get("password", "")
        self._memory_engine_endpoint = config.get(
            "memory_engine_endpoint",
            "http://8.134.127.8:31030",
        )
        # Login to EchoAgent
        self.client = EchoAgentClient(echoagent_url, username, password, api_prefix=api_prefix)
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
                logger.warning("解析 auth_key 失败: %s - 注入将不携带身份", e)
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
                base_url=config.get("echomem_url", "http://8.134.127.8:19080"),
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
                base_url=config.get("echomem_url", "http://8.134.127.8:8010"),
                auth_key=auth_key,
                account=config.get("account", "default"),
                user_id=config.get("user_id", "default"),
                agent_id=self._agent_id,
                workspace=config.get("workspace", ""),
                timeout_s=float(config.get("timeout_s", 60.0)),
                max_retries=int(config.get("max_retries", 3)),
                log_access_key=config.get("echomem_log_access_key", ""),
            )

    def create_session(self, title: str = "") -> str:
        return self.client.create_session(title, self._memory_engine_endpoint)

    def send_message(
        self, session_id: str, message: str, context_path: str = "/",
        *, extra: dict | None = None,
    ) -> AgentResponse:
        """Send message to EchoAgent and stream the reply (no prefill).

        In benchmark mode, session_id may be empty (the benchmark passes
        memory-session IDs, not EchoAgent session IDs). In that case, create
        a fresh EchoAgent session for each question so QA is independent.
        """
        try:
            if not session_id:
                session_id = self.client.create_session(
                    f"qa-{extra.get('question_id', '')}" if extra else "qa",
                    self._memory_engine_endpoint,
                )
            msg_result = self.client.send_message(
                session_id, context_path, message,
            )
            msg_data = msg_result.get("data", msg_result)
            if msg_data.get("error"):
                return AgentResponse(
                    error=f"send failed: {msg_data.get('error')} {msg_data.get('message', '')}",
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
                extra={"qa_profile": self.qa_profile},
            )

        reply = reply_result.get("reply") or ""
        ttft = reply_result.get("ttft_ms")
        done = reply_result.get("done_event") or {}
        metrics = done.get("metrics", {})
        done_memory_items = done.get("memoryItems") or []

        return AgentResponse(
            text=reply,
            ttft_ms=round(metrics.get("ttft_ms", ttft), 1)
            if metrics.get("ttft_ms", ttft) is not None else None,
            prompt_tokens=int(metrics.get("prompt_tokens") or 0)
            or int(done.get("promptTokens") or done.get("prompt_tokens") or 0),
            completion_tokens=int(metrics.get("completion_tokens") or 0),
            cached_tokens=int(metrics.get("cached_tokens") or 0)
            or int(done.get("cachedTokens") or done.get("cached_tokens") or 0),
            prefetch_committed=False,  # echoagent_live 不支持 prefill
            memory_items=done_memory_items,
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
