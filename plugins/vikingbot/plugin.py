"""Plugin entrypoint for the VikingBot agent.

VikingBot is a tool-call agent that searches memory via OpenAI-compatible
tool definitions, iterates until it finds the answer, and returns a
sanitized final response. It is the default agent for the LoCoMo benchmark.

VikingBot creates its own MemoryClient and LLMClient in setup().
send_message() is a thin wrapper around answer_one_vikingbot_question()
that adapts the QAResult into an AgentResponse.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from benchmarks.locomo.profiles import (
    profile_settings,
    VIKINGBOAT_0411_PROFILE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
)
from plugins.base import AgentDescriptor, AgentResponse, AgentPlugin
from backends.echomem.client import EchoMemClient
from backends.openviking.client import OpenVikingClient
from backends.memory_args import add_memory_backend_args
from shared.eval_base import add_llm_args, add_qa_args
from shared.llm_client import LLMClient
from shared.resource_rag import (
    RESOURCE_SYSTEM_PROMPT,
    build_retrieval_items,
    format_chunk_section,
)

from .runtime import answer_one_vikingbot_question

_DEFAULT_WORKSPACE = Path(__file__).resolve().parent / "bootstrap"
logger = logging.getLogger("eval.vikingbot")

# The tool-loop profile (vikingboat0411) is a personal-assistant persona and
# produces explanatory prose; HotpotQA scores answers with F1/EM, so the
# documents+tools path pins the final answer to the exact-fact style that the
# single-shot RAG path gets from RESOURCE_SYSTEM_PROMPT.
_BENCHMARK_ANSWER_APPEND = (
    "For benchmark evaluation, answer with only the exact answer: exact "
    "names, dates, and values when present. Do not explain, justify, or add "
    "context."
)


class VikingBotPlugin(AgentPlugin):
    """VikingBot: iterative tool-call agent with EchoMemory tools."""

    descriptor = AgentDescriptor(
        id="vikingbot",
        name="VikingBot",
        description="VikingBot prompt, memory tools, and iterative tool-call runtime.",
        capabilities=(
            "memory_search",
            "memory_read_many",
            "memory_list",
            "memory_grep",
            "memory_glob",
            "openai_tool_loop",
            "workspace_bootstrap",
        ),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_qa_args(parser)
        add_memory_backend_args(parser, with_backend_choice=True)
        g = parser.add_argument_group("VikingBot")
        g.add_argument(
            "--vikingbot-workspace",
            default=os.getenv("VIKINGBOT_WORKSPACE", str(_DEFAULT_WORKSPACE)),
            help="Workspace supplying the SOUL.md and TOOLS.md bootstrap",
        )
        g.add_argument("--tool-search-limit", type=int, default=None)
        g.add_argument("--user-memory-budget-chars", type=int, default=None)
        g.add_argument("--agent-memory-budget-chars", type=int, default=None)
        g.add_argument("--max-iterations", type=int, default=None)
        g.add_argument("--initial-min-score", type=float, default=None)
        g.add_argument("--tool-min-score", type=float, default=None)
        g.add_argument("--tool-search-pool-multiplier", type=int, default=None)
        g.add_argument(
            "--tool-set",
            choices=[
                "search_read",
                "vikingbot_native_safe",
                "vikingbot_echo_native",
            ],
            default=None,
        )
        g.add_argument(
            "--tools",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Expose the profile's memory tools to the answer model; "
                "--no-tools keeps the same profile prompt and initial memory "
                "injection but performs a single model turn"
            ),
        )

    def setup(self, config: dict) -> None:
        self._memory_backend = config.get("memory_backend", "echomem")
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))

        auth_key = config.get("echomem_auth_key", "")

        if self._memory_backend == "openviking":
            self.memory_client = OpenVikingClient(
                base_url=config.get("echomem_url", "http://127.0.0.1:19080"),
                api_key=auth_key,
                account=config.get("account", "default"),
                user_id=config.get("user_id", "default"),
                agent_id=config.get("agent_id", "default"),
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
                agent_id=config.get("agent_id", "default"),
                workspace=config.get("workspace", ""),
                timeout_s=float(config.get("timeout_s", 60.0)),
                max_retries=int(config.get("max_retries", 3)),
                log_access_key=config.get("echomem_log_access_key", ""),
            )

        # Identity isolation
        benchmark_name = config.get("benchmark_name", "")
        run_id = config.get("run_id", "")
        resume_qa = bool(config.get("resume_qa", ""))

        if benchmark_name and run_id and not resume_qa:
            label = f"eval-{benchmark_name}-{run_id}"[:120]
            self.memory_client.provision_isolated_identity(label)

        # LLM client for the tool-call loop
        self._llm = LLMClient(
            base_url=config.get("llm_base_url", ""),
            api_key=config.get("llm_api_key", ""),
            model=config.get("llm_model", "doubao-seed-2.0-pro"),
            temperature=config.get("llm_temperature", 0.7),
            max_tokens=config.get("llm_max_tokens", 2048),
            timeout_s=config.get("llm_timeout_s", 120.0),
            max_retries=config.get("llm_retries", 3),
        )

        # Resolve profile defaults: CLI args override profile settings.
        qa_profile = config.get("qa_profile")
        tools_enabled = config.get("tools", True)
        if not qa_profile:
            qa_profile = (
                VIKINGBOAT_0411_PROFILE
                if tools_enabled
                else VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE
            )
        if qa_profile in (
            VIKINGBOAT_0411_PROFILE,
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        ):
            try:
                defaults = profile_settings(qa_profile)
            except ValueError:
                defaults = {}
        else:
            defaults = {}

        def _resolve(name, cast):
            cli_val = config.get(name)
            if cli_val is not None:
                return cast(cli_val)
            if name in defaults:
                return cast(defaults[name])
            return None

        self._qa_profile = qa_profile
        self._tools_enabled = config.get("tools", True)
        self._top_k = _resolve("top_k", int) or 10
        self._tool_search_limit = _resolve("tool_search_limit", int)
        self._user_memory_budget_chars = _resolve("user_memory_budget_chars", int)
        self._agent_memory_budget_chars = _resolve("agent_memory_budget_chars", int)
        self._max_iterations = _resolve("max_iterations", int)
        self._initial_min_score = _resolve("initial_min_score", float)
        self._tool_min_score = _resolve("tool_min_score", float)
        self._tool_search_pool_multiplier = _resolve("tool_search_pool_multiplier", int)
        self._tool_set = _resolve("tool_set", str)
        self._vikingbot_workspace = config.get("vikingbot_workspace", "")
        _qt = config.get("question_timeout_s")
        self._question_timeout_s = float(_qt if _qt is not None else defaults.get("question_timeout_s", 120.0))
        self._answer_temperature = _resolve("answer_temperature", float)
        self._omit_answer_temperature = _resolve("omit_answer_temperature", bool)
        self._initial_retrieval_query_mode = _resolve("initial_retrieval_query_mode", str)
        self._tool_query_dedup_scope = _resolve("tool_query_dedup_scope", str)
        self._retrieval_uri_dedup = _resolve("retrieval_uri_dedup", bool)
        self._search_tool_target_uri_schema = _resolve("search_tool_target_uri_schema", bool)
        self._documents_mode = (
            str(config.get("import_mode") or "").strip().lower() == "documents"
        )
        self.path_title_map: dict[str, str] = {}
        self._docs_memory_budget_chars = int(config.get("memory_budget_chars", 8000))

    @property
    def qa_profile(self) -> str:
        return self._qa_profile

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

    def create_session(self, title: str = "") -> str:
        return self.memory_client.open_session(title=title or "qa")

    def _send_documents(
        self,
        message: str,
        extra: dict | None = None,
    ) -> AgentResponse:
        """Document mode: retrieve the shared corpus and answer in one LLM call.

        Used when the benchmark imports the HotpotQA passages as document
        resources (``--import-mode documents``). The corpus lives in the
        backend's resources space (EchoMem ``/api/resources`` or OpenViking
        ``viking://user/resources/``); no tool loop or session is involved.
        """
        extra = extra or {}
        start = time.monotonic()
        deadline = start + self._question_timeout_s if self._question_timeout_s > 0 else None

        def remaining() -> float | None:
            if deadline is None:
                return None
            return max(0.001, deadline - time.monotonic())

        retrieval_items: list[dict[str, Any]] = []
        retrieval_latency_s = 0.0
        retrieval_error = ""
        try:
            t0 = time.monotonic()
            results = self.memory_client.search_resources(
                message,
                limit=self._top_k,
                tags=["hotpotqa"],
                timeout_s=remaining(),
            )
            retrieval_latency_s = time.monotonic() - t0
            retrieval_items = build_retrieval_items(results, self.path_title_map)
        except Exception as exc:
            retrieval_error = f"{type(exc).__name__}: {exc}"
            logger.warning("resource search failed: %s", retrieval_error)

        question_time = str(extra.get("question_time") or "")
        time_context = f"Current date: {question_time}.\n\n" if question_time.strip() else ""
        chunks = format_chunk_section(retrieval_items, self._docs_memory_budget_chars)
        if chunks:
            user_content = (
                f"{time_context}Retrieved documents:\n\n{chunks}\n\n"
                f"Question: {message}"
            )
        else:
            user_content = f"{time_context}Question: {message}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": RESOURCE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        resp = self._llm.chat(messages, timeout_s=remaining())
        elapsed = time.monotonic() - start
        trace: dict[str, Any] = {
            "schema_version": 1,
            "agent": "vikingbot",
            "qa_profile": "vikingbot_docs",
            "question_id": str(extra.get("question_id") or ""),
            "sample_id": str(extra.get("sample_id") or ""),
            "category": str(extra.get("category") or ""),
            "question": message,
            "gold_answer": str(extra.get("answer") or ""),
            "question_time": question_time,
            "settings": {
                "top_k": self._top_k,
                "memory_budget_chars": self._docs_memory_budget_chars,
                "question_timeout_s": self._question_timeout_s,
            },
            "model_request": {
                "base_url": self._llm.base_url.rstrip("/"),
                "model": self._llm.model,
                "max_tokens": self._llm.max_tokens,
            },
            "initial_retrieval": {
                "query": message,
                "items": retrieval_items,
                "error": retrieval_error,
                "latency_ms": round(retrieval_latency_s * 1000, 3),
            },
            "initial_messages": json.loads(json.dumps(messages, ensure_ascii=False)),
            "iterations": [],
            "tool_audit": {
                "schema_version": 1,
                "tools_used": [],
                "tool_calls": [],
                "discovered_files": [],
                "read_files": [],
            },
            "tool_protocol": {"names": [], "sha256": ""},
            "final_response": resp.content,
            "answer_sanitized": False,
        }
        return AgentResponse(
            text=resp.content,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            memory_items=retrieval_items,
            error=resp.error or None,
            extra={
                "qa_profile": "vikingbot_docs",
                "elapsed_s": elapsed,
                "retrieval_latency_s": retrieval_latency_s,
                "llm_latency_s": elapsed,
                "retrieval_error": retrieval_error,
                "iterations": 1,
                "trace": trace,
            },
        )

    def _answer_kwargs(self, message: str, extra: dict) -> dict[str, Any]:
        """Build the ``answer_one_vikingbot_question`` kwargs from plugin state."""
        kwargs: dict[str, Any] = dict(
            question_id=extra.get("question_id", ""),
            question=extra.get("question", message),
            answer=extra.get("answer", ""),
            question_time=extra.get("question_time", ""),
            sample_id=extra.get("sample_id", ""),
            category=extra.get("category", ""),
        )

        # Agent configuration from setup(); None values omitted so the
        # runtime's own defaults apply.
        config_fields: dict[str, Any] = {
            "top_k": self._top_k,
            "tool_search_limit": self._tool_search_limit,
            "tool_search_pool_multiplier": self._tool_search_pool_multiplier,
            "initial_min_score": self._initial_min_score,
            "tool_min_score": self._tool_min_score,
            "tool_set": self._tool_set,
            "user_memory_budget_chars": self._user_memory_budget_chars,
            "agent_memory_budget_chars": self._agent_memory_budget_chars,
            "max_iterations": self._max_iterations,
            "question_timeout_s": self._question_timeout_s,
            "vikingbot_workspace": self._vikingbot_workspace,
            "qa_profile": self._qa_profile,
            "answer_temperature": self._answer_temperature,
            "omit_answer_temperature": self._omit_answer_temperature,
            "initial_retrieval_query_mode": self._initial_retrieval_query_mode,
            "tool_query_dedup_scope": self._tool_query_dedup_scope,
            "retrieval_uri_dedup": self._retrieval_uri_dedup,
            "search_tool_target_uri_schema": self._search_tool_target_uri_schema,
            "tools_enabled": self._tools_enabled,
            "system_prompt_append": extra.get("system_prompt_append", ""),
            "system_prompt_append_sha256": extra.get("system_prompt_append_sha256", ""),
            "system_prompt_append_source": extra.get("system_prompt_append_source", ""),
        }
        for key, val in config_fields.items():
            if val is not None:
                kwargs[key] = val
        return kwargs

    def _answer_documents_with_tools(
        self,
        message: str,
        extra: dict,
    ) -> AgentResponse:
        """Document mode with the tool loop: multi-round resource retrieval.

        Reuses ``answer_one_vikingbot_question`` with ``search_resources_mode``
        so the agent can iteratively search/read the shared corpus; the full
        tool-call chain lands in the trace/tool_audit.  The context budget
        comes from the documents mode ``memory_budget_chars`` instead of the
        profile's session budgets.
        """
        kwargs = self._answer_kwargs(message, extra)
        kwargs["tools_enabled"] = True
        kwargs["search_resources_mode"] = True
        kwargs["path_title_map"] = self.path_title_map
        kwargs["user_memory_budget_chars"] = self._docs_memory_budget_chars
        kwargs["agent_memory_budget_chars"] = self._docs_memory_budget_chars
        existing_append = (extra.get("system_prompt_append") or "").strip()
        kwargs["system_prompt_append"] = "\n\n".join(
            part for part in (existing_append, _BENCHMARK_ANSWER_APPEND) if part
        )
        qa = answer_one_vikingbot_question(self.memory_client, self._llm, **kwargs)
        return AgentResponse(
            text=qa.response,
            prompt_tokens=qa.prompt_tokens,
            completion_tokens=qa.completion_tokens,
            memory_items=qa.retrieval_items,
            error=qa.llm_error or None,
            extra={
                "elapsed_s": qa.elapsed_s,
                "tool_call_count": qa.tool_call_count,
                "iterations": qa.iterations,
                "qa_profile": qa.qa_profile,
                "retrieval_latency_s": qa.retrieval_latency_s,
                "llm_latency_s": qa.llm_latency_s,
                "retrieval_error": qa.retrieval_error,
                "trace": qa.trace,
            },
        )

    def send_message(
        self,
        session_id: str,
        message: str,
        context_path: str = "/",
        *,
        extra: dict | None = None,
    ) -> AgentResponse:
        """Send a question and receive the agent's response.

        *extra* carries benchmark context (question_id, question, answer,
        question_time, sample_id, category, system_prompt_append).  All
        agent-level configuration is read from instance attributes set
        in setup().  In document mode (``--import-mode documents``) the
        shared document corpus is retrieved; with tools enabled this runs
        the multi-round tool loop over the corpus, otherwise a single-shot
        RAG call.
        """
        extra = extra or {}
        if self._documents_mode:
            if self._tools_enabled:
                return self._answer_documents_with_tools(message, extra)
            return self._send_documents(message, extra)

        existing_append = (extra.get("system_prompt_append") or "").strip()
        kwargs = self._answer_kwargs(message, extra)
        kwargs["system_prompt_append"] = "\n\n".join(
            part for part in (existing_append, _BENCHMARK_ANSWER_APPEND) if part
        )
        qa = answer_one_vikingbot_question(
            self.memory_client,
            self._llm,
            **kwargs,
        )

        return AgentResponse(
            text=qa.response,
            prompt_tokens=qa.prompt_tokens,
            completion_tokens=qa.completion_tokens,
            memory_items=qa.retrieval_items,
            error=qa.llm_error or None,
            extra={
                "elapsed_s": qa.elapsed_s,
                "tool_call_count": qa.tool_call_count,
                "iterations": qa.iterations,
                "qa_profile": qa.qa_profile,
                "retrieval_latency_s": qa.retrieval_latency_s,
                "llm_latency_s": qa.llm_latency_s,
                "retrieval_error": qa.retrieval_error,
                "trace": qa.trace,
            },
        )

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
