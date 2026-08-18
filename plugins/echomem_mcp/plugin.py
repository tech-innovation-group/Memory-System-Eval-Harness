"""EchoMem MCP agent plugin: LLM uses MCP tools to retrieve memories.

The LLM is given EchoMem's MCP tools (memory_query, read, list, glob) as
OpenAI function-calling definitions.  It decides when to search memory and
which URIs to read, mimicking how a real agent would interact with a
memory system through the MCP protocol.

Two configurable parameters control behavior:
- --tool-calling / --no-tool-calling: enable LLM tool calling via MCP
- --search-in-tools / --no-search-in-tools: include memory_query in tool defs

Initial memory pre-fetch is always performed through EchoMem MCP
``memory_query``.  The plugin intentionally does not call EchoMem's HTTP
retrieval API for QA retrieval.

When the benchmark runs with ``--import-mode documents``, QA switches to
the document mode: the shared document corpus is retrieved via
``/api/resources/search`` and answered with a single LLM call (no MCP).

Memory injection is handled before QA starts.  The MCP server must be running
(EchoMem config ``mcp.enabled=true``) for non-document modes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from plugins.base import AgentDescriptor, AgentPlugin, AgentResponse
from plugins.echomem_mcp.mcp_client import McpClient
from plugins.echomem_mcp.runtime import (
    _NO_TOOLS_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    configured_tools,
)
from shared.resource_rag import (
    RESOURCE_SYSTEM_PROMPT,
    build_retrieval_items,
    format_chunk_section,
)
from backends.echomem.client import EchoMemClient
from backends.memory_types import SearchResult
from shared.eval_base import add_llm_args, add_qa_args
from shared.llm_client import LLMClient
from backends.memory_args import add_memory_backend_args
from backends.memory_format import format_memory_section

logger = logging.getLogger("eval.echomem_mcp")


def _is_agent_memory(item: SearchResult) -> bool:
    uri = str(getattr(item, "uri", "") or "").lower()
    memory_type = str(getattr(item, "memory_type", "") or "").lower()
    return "/agent/" in uri or memory_type.startswith("agent")


def _format_items(items: list[SearchResult], budget_chars: int) -> str:
    sections: list[str] = []
    total = 0
    for i, item in enumerate(items, 1):
        content = str(item.content or "")
        full_block = f"[{i}] (score: {item.score:.2f}) uri: {item.uri}\n{content}"
        uri_block = f"[{i}] (score: {item.score:.2f}) uri: {item.uri}"
        separator = 2 if sections else 0
        if not budget_chars or total + separator + len(full_block) <= budget_chars:
            sections.append(full_block)
            total += separator + len(full_block)
            continue
        if total + separator + len(uri_block) <= budget_chars:
            sections.append(uri_block)
            total += separator + len(uri_block)
    return "\n\n".join(sections)


def format_split_memory_section(
    items: list[SearchResult],
    *,
    user_memory_budget_chars: int,
    agent_memory_budget_chars: int,
) -> str:
    if not items:
        return ""

    user_items: list[SearchResult] = []
    agent_items: list[SearchResult] = []
    for item in items:
        if _is_agent_memory(item):
            agent_items.append(item)
        else:
            user_items.append(item)

    sections: list[str] = []
    user_memory = _format_items(user_items, user_memory_budget_chars)
    agent_memory = _format_items(agent_items, agent_memory_budget_chars)
    if user_memory:
        sections.append(f"### user memories:\n\n{user_memory}")
    if agent_memory:
        sections.append(f"### agent memories:\n\n{agent_memory}")
    return "\n\n".join(sections)


class EchoMemMCPPlugin(AgentPlugin):
    """Agent that uses EchoMem MCP tools for memory retrieval.

    Behavior is controlled by two flags (both default to True):
    - tool_calling: whether to present tools to the LLM
    - search_in_tools: whether memory_query is in the tool list

    The platform-side pre-fetch before each LLM turn always uses MCP
    memory_query.
    """

    descriptor = AgentDescriptor(
        id="echomem_mcp",
        name="EchoMem MCP Agent",
        description="LLM agent that retrieves memories via EchoMem MCP tools.",
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_llm_args(parser)
        add_qa_args(parser)
        # EchoMem MCP benefits from a wider initial candidate set than the
        # shared QA default while leaving other plugins unchanged.
        for action in parser._actions:
            if "--top-k" in getattr(action, "option_strings", []):
                action.default = 25
                break
        add_memory_backend_args(parser)
        g = parser.add_argument_group("echomem-mcp")
        g.add_argument(
            "--mcp-url",
            default="http://127.0.0.1:8001",
            help="EchoMem MCP server URL (default: http://127.0.0.1:8001)",
        )
        g.add_argument(
            "--mcp-auth-key",
            default="",
            help="X-Auth-Key for MCP server (falls back to --echomem-auth-key if empty)",
        )
        g.add_argument(
            "--mcp-max-iterations",
            type=int,
            default=50,
            help="Maximum tool-call iterations per question (default: 50)",
        )
        g.add_argument(
            "--tool-calling",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Enable LLM tool calling via MCP (default: enabled)",
        )
        g.add_argument(
            "--search-in-tools",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Include memory_query in tool definitions (default: enabled)",
        )
        g.add_argument(
            "--mcp-read-mode",
            choices=["disabled", "allow", "require"],
            default="allow",
            help=(
                "Transcript read policy: disabled removes read from MCP tools; "
                "allow and require preserve the read tool; require is retained "
                "as a compatibility alias without extra prompt rules"
            ),
        )
        g.add_argument(
            "--user-memory-budget-chars",
            type=int,
            default=4000,
            help="Max chars of retrieved user memories to inject (default: 4000)",
        )
        g.add_argument(
            "--agent-memory-budget-chars",
            type=int,
            default=2000,
            help="Max chars of retrieved agent memories to inject (default: 2000)",
        )

    def setup(self, config: dict) -> None:
        self._mcp_url = config.get("mcp_url", "http://127.0.0.1:8001")
        self._auth_key = config.get("mcp_auth_key", "") or config.get("echomem_auth_key", "")
        self._max_iterations = config.get("mcp_max_iterations", 50)
        self._tool_calling = config.get("tool_calling", True)
        self._search_in_tools = config.get("search_in_tools", True)
        # Kept as an internal/backward-compatible config switch for unit tests
        # and older config files. It is intentionally not exposed as a CLI
        # option; QA retrieval should use MCP by default.
        self._manual_search = config.get("manual_search", True)
        self._initial_search_via_mcp = True
        self._mcp_read_mode = config.get("mcp_read_mode", "allow")
        self._top_k = config.get("top_k", 25)
        self._memory_budget_chars = config.get("memory_budget_chars", 8000)
        self._user_memory_budget_chars = config.get("user_memory_budget_chars", 4000)
        self._agent_memory_budget_chars = config.get("agent_memory_budget_chars", 2000)
        self._question_timeout_s = float(config.get("question_timeout_s", 120.0))
        self._documents_mode = (
            str(config.get("import_mode") or "").strip().lower() == "documents"
        )
        self.path_title_map: dict[str, str] = {}

        # Create LLM client
        self._llm = LLMClient(
            base_url=config.get("llm_base_url", ""),
            api_key=config.get("llm_api_key", ""),
            model=config.get("llm_model", "doubao-seed-2.0-pro"),
            temperature=config.get("llm_temperature", 0.7),
            max_tokens=config.get("llm_max_tokens", 2048),
            timeout_s=config.get("llm_timeout_s", 120.0),
            max_retries=config.get("llm_retries", 3),
        )

        # Create EchoMemClient for memory injection
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))

        self.memory_client = EchoMemClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
            auth_key=config.get("echomem_auth_key", ""),
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

    def inject_memories(
        self,
        memories: list[dict],
        *,
        backend: str = "echomem",
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
        self._session_count = getattr(self, "_session_count", 0) + 1
        return f"echomem_mcp_session_{self._session_count}"

    def _send_documents(
        self,
        message: str,
        extra: dict | None = None,
    ) -> AgentResponse:
        """Document mode: retrieve the shared corpus and answer in one LLM call.

        Used when the benchmark imports the HotpotQA passages as document
        resources (``--import-mode documents``). The corpus is stored in
        EchoMem's resource engine and searched via ``/api/resources/search``;
        no MCP tools or sessions are involved.
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
        chunks = format_chunk_section(retrieval_items, self._memory_budget_chars)
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
            "agent": "echomem_mcp",
            "qa_profile": "echomem_mcp_documents",
            "question_id": str(extra.get("question_id") or ""),
            "sample_id": str(extra.get("sample_id") or ""),
            "category": str(extra.get("category") or ""),
            "question": message,
            "gold_answer": str(extra.get("answer") or ""),
            "question_time": question_time,
            "settings": {
                "top_k": self._top_k,
                "memory_budget_chars": self._memory_budget_chars,
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
                "qa_profile": "echomem_mcp_documents",
                "elapsed_s": elapsed,
                "retrieval_latency_s": retrieval_latency_s,
                "llm_latency_s": elapsed,
                "retrieval_error": retrieval_error,
                "iterations": 1,
                "trace": trace,
            },
        )

    def _documents_search_tool(
        self,
        query: str,
        timeout_s: float | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Resource-backed ``memory_search``: query the corpus, return text + items."""
        if not query.strip():
            return "No results found for empty query", []
        try:
            results = self.memory_client.search_resources(
                query,
                limit=self._top_k,
                tags=["hotpotqa"],
                timeout_s=timeout_s,
            )
            items = build_retrieval_items(results, self.path_title_map)
        except Exception as exc:
            return f"Search failed: {exc}", []
        if not items:
            return "No results found.", []
        lines = [
            f"[{index}] ({item.get('hotpotqa_title') or item.get('uri') or 'resource'}) "
            f"{item.get('content') or ''}"
            for index, item in enumerate(items, 1)
        ]
        return "\n\n".join(lines), items

    def _documents_read_tool(
        self,
        uris: list[str],
        timeout_s: float | None,
    ) -> str:
        """Resource-backed ``memory_read_many``: read resource content by URI."""
        if not uris:
            return "Error: No URIs provided."
        blocks = []
        for uri in uris[:20]:
            try:
                content = self.memory_client.fs_read(uri, timeout_s=timeout_s)
            except Exception as exc:
                content = f"Error reading {uri}: {exc}"
            blocks.append(f"--- {uri} ---\n{content}")
        return "\n\n".join(blocks)

    def _send_documents_with_tools(
        self,
        message: str,
        extra: dict | None = None,
    ) -> AgentResponse:
        """Document mode with the tool loop: multi-round resource retrieval.

        Reuses ``LLMClient.chat_with_tools`` with resource-backed
        ``memory_search`` / ``memory_read_many`` tools so the agent can
        iteratively search/read the shared corpus; the full tool-call chain
        lands in the trace/tool_audit.
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
        chunks = format_chunk_section(retrieval_items, self._memory_budget_chars)
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

        tools: list[dict[str, Any]] = [
            {"type": "function", "function": {
                "name": "memory_search",
                "description": "Search the HotpotQA document corpus for resources relevant to a query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query"},
                    },
                    "required": ["query"],
                },
            }},
            {"type": "function", "function": {
                "name": "memory_read_many",
                "description": "Read full content from multiple document resources by URI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uris": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Resource URIs to read",
                        },
                    },
                    "required": ["uris"],
                },
            }},
        ]
        tool_protocol_payload = json.dumps(
            tools,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        trace: dict[str, Any] = {
            "schema_version": 1,
            "agent": "echomem_mcp",
            "qa_profile": "echomem_mcp_documents",
            "question_id": str(extra.get("question_id") or ""),
            "sample_id": str(extra.get("sample_id") or ""),
            "category": str(extra.get("category") or ""),
            "question": message,
            "gold_answer": str(extra.get("answer") or ""),
            "question_time": question_time,
            "settings": {
                "top_k": self._top_k,
                "memory_budget_chars": self._memory_budget_chars,
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
            "tool_protocol": {
                "names": [str(t["function"]["name"]) for t in tools],
                "sha256": hashlib.sha256(tool_protocol_payload).hexdigest(),
            },
            "final_response": "",
            "answer_sanitized": False,
        }

        prompt_tokens = 0
        completion_tokens = 0
        tool_call_count = 0
        iterations = 0
        response_text = ""
        llm_error = ""
        for iteration in range(1, self._max_iterations + 1):
            iterations = iteration
            rem = remaining()
            if rem is not None and rem <= 0:
                llm_error = f"question deadline exceeded after {self._question_timeout_s:g}s"
                break
            resp = self._llm.chat_with_tools(messages, tools, timeout_s=rem)
            prompt_tokens += resp.prompt_tokens
            completion_tokens += resp.completion_tokens
            if resp.error:
                llm_error = resp.error
                break
            iteration_trace: dict[str, Any] = {
                "iteration": iteration,
                "model_message": {
                    "role": "assistant",
                    "content": resp.content,
                    "tool_calls": resp.tool_calls,
                },
                "prompt_tokens": resp.prompt_tokens,
                "completion_tokens": resp.completion_tokens,
                "tool_calls": [],
            }
            trace["iterations"].append(iteration_trace)
            if not resp.tool_calls:
                response_text = resp.content
                break
            messages.append({
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": resp.tool_calls,
            })
            for tool_call in resp.tool_calls:
                function = tool_call.get("function") or {}
                name = str(function.get("name") or "")
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else dict(raw_arguments)
                    )
                except Exception:
                    arguments = {}
                tool_call_count += 1
                if name == "memory_search":
                    tool_text, tool_items = self._documents_search_tool(
                        str(arguments.get("query") or ""),
                        remaining(),
                    )
                    retrieval_items.extend(tool_items)
                elif name == "memory_read_many":
                    uris = [
                        str(uri)
                        for uri in (arguments.get("uris") or [])
                        if str(uri or "").strip()
                    ]
                    tool_text = self._documents_read_tool(uris, remaining())
                else:
                    tool_text = f"Unknown tool: {name}"
                record: dict[str, Any] = {
                    "iteration": iteration,
                    "call_id": str(tool_call.get("id") or ""),
                    "name": name,
                    "arguments": arguments,
                    "duplicate_skipped": False,
                    "backend_operations": [],
                }
                iteration_trace["tool_calls"].append(record)
                if name not in trace["tool_audit"]["tools_used"]:
                    trace["tool_audit"]["tools_used"].append(name)
                trace["tool_audit"]["tool_calls"].append(record)
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(tool_call.get("id") or ""),
                    "content": tool_text,
                })

        trace["final_response"] = response_text
        trace["answer_sanitized"] = False
        if llm_error:
            trace["error"] = llm_error
        elapsed = time.monotonic() - start
        return AgentResponse(
            text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            memory_items=retrieval_items,
            error=llm_error or None,
            extra={
                "qa_profile": "echomem_mcp_documents",
                "elapsed_s": elapsed,
                "tool_call_count": tool_call_count,
                "iterations": iterations,
                "retrieval_latency_s": retrieval_latency_s,
                "llm_latency_s": elapsed,
                "retrieval_error": retrieval_error,
                "trace": trace,
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
        extra = extra or {}
        if self._documents_mode:
            if self._tool_calling:
                return self._send_documents_with_tools(message, extra)
            return self._send_documents(message, extra)
        question_time = extra.get("question_time", "")
        start = time.monotonic()
        deadline = start + self._question_timeout_s if self._question_timeout_s > 0 else None

        def remaining() -> float | None:
            if deadline is None:
                return None
            return max(0.001, deadline - time.monotonic())

        # Build messages
        time_context = f"Current date: {question_time}.\n\n" if str(question_time).strip() else ""
        system_prompt = _SYSTEM_PROMPT if self._tool_calling else _NO_TOOLS_SYSTEM_PROMPT
        prompt_append = str(extra.get("system_prompt_append") or "").strip()
        if prompt_append:
            system_prompt += "\n\nAdditional evaluation instructions:\n" + prompt_append
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{time_context}{message}"},
        ]

        retrieval_items: list[dict[str, Any]] = []
        retrieval_latency_s = 0.0
        retrieval_error = ""
        initial_search_via_mcp = False
        mcp: McpClient | None = None

        initial_search_enabled = bool(getattr(self, "_initial_search_via_mcp", True))
        if self._manual_search and initial_search_enabled:
            try:
                mcp = McpClient(
                    self._mcp_url,
                    auth_key=self._auth_key or self.memory_client.auth_key,
                )
                mcp.initialize(timeout_s=remaining())
                initial_search_via_mcp = True
            except Exception as e:
                retrieval_error = f"{type(e).__name__}: {e}"
                logger.warning("Initial MCP search initialize failed: %s", retrieval_error)
                mcp = None

        # Phase A: Platform pre-fetch search through MCP memory_query.
        if self._manual_search:
            try:
                t0 = time.monotonic()
                if mcp is not None:
                    raw = mcp.call_tool(
                        "memory_query",
                        {"query": message, "limit": self._top_k},
                        timeout_s=remaining(),
                    )
                    payload = json.loads(raw) if raw else {}
                    raw_items = payload.get("items", []) if isinstance(payload, dict) else []
                    results = [
                        SearchResult.from_dict(item)
                        for item in raw_items
                        if isinstance(item, dict)
                    ]
                else:
                    results = []
                retrieval_latency_s = time.monotonic() - t0
                memory_text = format_split_memory_section(
                    results,
                    user_memory_budget_chars=self._user_memory_budget_chars,
                    agent_memory_budget_chars=self._agent_memory_budget_chars,
                )
                if not memory_text:
                    memory_text = format_memory_section(results, self._memory_budget_chars)
                if memory_text:
                    messages.insert(1, {"role": "user", "content": memory_text})
                retrieval_items = [r.to_dict() for r in results]
            except Exception as e:
                retrieval_error = f"{type(e).__name__}: {e}"
                logger.warning("Manual search failed: %s", retrieval_error)

        # Phase B: Build tool list
        if self._tool_calling:
            tools = configured_tools(self._mcp_read_mode)
            if not self._search_in_tools:
                tools = [
                    t for t in tools
                    if t["function"]["name"] != "memory_query"
                ]
        else:
            tools = []
        allowed_tool_names = {
            str(tool["function"]["name"])
            for tool in tools
        }

        # Phase C: Tool-call loop or single call
        tool_call_count = 0
        iterations = 0
        total_prompt = 0
        total_completion = 0
        response_text = ""
        llm_error = ""
        tool_audit: dict[str, Any] = {
            "schema_version": 1,
            "mcp_read_mode": self._mcp_read_mode,
            "tools_used": [],
            "tool_calls": [],
            "messages_jsonl_reads": [],
        }

        if tools:
            try:
                mcp = McpClient(
                    self._mcp_url,
                    auth_key=self._auth_key or self.memory_client.auth_key,
                )
                mcp.initialize(timeout_s=remaining())
            except Exception as e:
                logger.warning("MCP initialize failed: %s", e)
                mcp = None

            if mcp is not None:
                try:
                    for iteration in range(1, self._max_iterations + 1):
                        iterations = iteration
                        rem = remaining()
                        if rem is not None and rem <= 0:
                            llm_error = f"question deadline exceeded after {self._question_timeout_s:g}s"
                            break

                        resp = self._llm.chat_with_tools(messages, tools, timeout_s=rem)
                        total_prompt += resp.prompt_tokens
                        total_completion += resp.completion_tokens

                        if resp.error:
                            llm_error = resp.error
                            break

                        if not resp.tool_calls:
                            response_text = resp.content
                            break

                        messages.append({
                            "role": "assistant",
                            "content": resp.content or "",
                            "tool_calls": resp.tool_calls,
                        })

                        for tc in resp.tool_calls:
                            func = tc.get("function", {})
                            name = func.get("name", "")
                            try:
                                args = json.loads(func.get("arguments", "{}"))
                            except json.JSONDecodeError:
                                args = {}

                            if name not in allowed_tool_names:
                                result_text = (
                                    f"Tool '{name}' is not available in this "
                                    "evaluation mode. Use only the tools in "
                                    "the supplied schema."
                                )
                                logger.warning(
                                    "Rejected unavailable tool call: %s", name
                                )
                                tool_audit["tool_calls"].append({
                                    "name": name,
                                    "arguments": args,
                                    "is_messages_jsonl_read": False,
                                    "error": "tool_not_exposed",
                                })
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc.get("id", ""),
                                    "content": result_text,
                                })
                                continue

                            try:
                                result_text = mcp.call_tool(name, args, timeout_s=remaining())
                                tool_call_count += 1
                                is_messages_read = (
                                    name == "read"
                                    and (
                                        "messages.jsonl" in str(args.get("uris") or "")
                                        or "messages.jsonl" in result_text
                                    )
                                )
                                tool_audit["tool_calls"].append({
                                    "name": name,
                                    "arguments": args,
                                    "is_messages_jsonl_read": is_messages_read,
                                    "result_preview": result_text[:500],
                                })
                                if name not in tool_audit["tools_used"]:
                                    tool_audit["tools_used"].append(name)
                                if is_messages_read:
                                    tool_audit["messages_jsonl_reads"].append({
                                        "uris": args.get("uris", ""),
                                        "result_preview": result_text[:500],
                                    })
                                if name == "memory_query":
                                    retrieval_items.append({
                                        "tool": name,
                                        "query": args.get("query", ""),
                                        "result": result_text[:2000],
                                    })
                            except Exception as e:
                                result_text = f"Error calling {name}: {e}"
                                logger.warning("Tool %s failed: %s", name, e)
                                tool_audit["tool_calls"].append({
                                    "name": name,
                                    "arguments": args,
                                    "is_messages_jsonl_read": False,
                                    "error": str(e)[:500],
                                })

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": result_text,
                            })
                    else:
                        # Max iterations - force final answer without tools
                        messages.append({
                            "role": "user",
                            "content": "You have reached the tool-use iteration limit. Do not call any more tools. Answer the question directly now.",
                        })
                        resp = self._llm.chat_with_tools(messages, [], timeout_s=remaining())
                        total_prompt += resp.prompt_tokens
                        total_completion += resp.completion_tokens
                        response_text = resp.content
                        llm_error = resp.error or ""
                finally:
                    try:
                        mcp.close()
                    except Exception:
                        pass
            else:
                # MCP init failed - fall back to single LLM call
                resp = self._llm.chat(messages, timeout_s=remaining())
                total_prompt = resp.prompt_tokens
                total_completion = resp.completion_tokens
                response_text = resp.content
                llm_error = resp.error or ""
        else:
            # No tool calling - single LLM call
            resp = self._llm.chat(messages, timeout_s=remaining())
            total_prompt = resp.prompt_tokens
            total_completion = resp.completion_tokens
            response_text = resp.content
            llm_error = resp.error or ""

        if mcp is not None and not tools:
            mcp.close()

        elapsed = time.monotonic() - start
        return AgentResponse(
            text=response_text,
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            memory_items=retrieval_items,
            error=llm_error or None,
            extra={
                "tool_call_count": tool_call_count,
                "iterations": iterations,
                "qa_profile": "echomem_mcp",
                "elapsed_s": elapsed,
                "retrieval_latency_s": retrieval_latency_s,
                "llm_latency_s": elapsed,
                "retrieval_error": retrieval_error,
                "initial_search_via_mcp": initial_search_via_mcp,
                "trace": {
                    "tool_audit": tool_audit,
                    "mcp_read_mode": self._mcp_read_mode,
                    "initial_search_via_mcp": initial_search_via_mcp,
                    "tools": [
                        t["function"]["name"]
                        for t in tools
                    ],
                },
            },
        )

    def getlog(self) -> str:
        """Fetch EchoMem backend logs scoped to this run's tenant/user.

        Returns the injected-memory logs plus the QA logs of this run
        (both live under the run's tenant/user). Falls back to an error
        object so log collection never breaks the evaluation run.
        """
        try:
            logs = self.memory_client.fetch_logs(
                tenant_id=self.memory_client.account,
                user_id=self.memory_client.user_id,
            )
            return json.dumps(logs, ensure_ascii=False, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)
