"""OpenAI-compatible tool-call runtime for the VikingBot agent."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backends.memory_types import MemoryClient, SearchResult
from shared.llm_client import LLMClient
from shared.qa import QAResult

from .prompting import build_messages, build_question_prompt
from .tools import (
    MEMORY_SEARCH_TOOL,
    _resource_search_result,
    execute_tool,
    tool_definitions,
)


class _AuditedMemoryClient:
    """Record backend operations performed by one agent tool call."""

    def __init__(self, client: MemoryClient):
        self._client = client
        self.operations: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    @staticmethod
    def _entry_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "uri": str(entry.get("uri") or ""),
                "name": str(entry.get("name") or ""),
                "is_dir": (
                    bool(entry.get("isDir"))
                    or str(entry.get("kind") or "") == "directory"
                ),
                "size": int(entry.get("size") or 0),
            }
            for entry in entries
            if isinstance(entry, dict)
        ]

    def search(self, query: str, *args: Any, **kwargs: Any) -> list[SearchResult]:
        operation: dict[str, Any] = {
            "operation": "search",
            "query": query,
            "status": "attempted",
        }
        self.operations.append(operation)
        try:
            results = self._client.search(query, *args, **kwargs)
        except Exception as exc:
            operation.update(status="error", error=str(exc))
            raise
        operation.update(
            status="ok",
            result_count=len(results),
            result_uris=list(dict.fromkeys(
                item.uri for item in results if item.uri
            )),
        )
        return results

    def fs_read(self, uri: str, *args: Any, **kwargs: Any) -> str:
        operation: dict[str, Any] = {
            "operation": "fs_read",
            "uri": uri,
            "status": "attempted",
        }
        self.operations.append(operation)
        try:
            content = self._client.fs_read(uri, *args, **kwargs)
        except Exception as exc:
            operation.update(status="error", error=str(exc))
            raise
        operation.update(
            status="ok",
            content_chars=len(content),
            empty=not bool(content),
        )
        return content

    def fs_list(
        self,
        uri: str,
        *args: Any,
        recursive: bool = False,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        operation: dict[str, Any] = {
            "operation": "fs_list",
            "uri": uri,
            "recursive": recursive,
            "status": "attempted",
        }
        self.operations.append(operation)
        try:
            entries = self._client.fs_list(
                uri,
                *args,
                recursive=recursive,
                **kwargs,
            )
        except Exception as exc:
            operation.update(status="error", error=str(exc))
            raise
        operation.update(
            status="ok",
            entry_count=len(entries),
            entries=self._entry_rows(entries),
        )
        return entries

    def fs_glob(
        self,
        pattern: str,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        operation: dict[str, Any] = {
            "operation": "fs_glob",
            "pattern": pattern,
            "status": "attempted",
        }
        self.operations.append(operation)
        try:
            entries = self._client.fs_glob(pattern, *args, **kwargs)
        except Exception as exc:
            operation.update(status="error", error=str(exc))
            raise
        operation.update(
            status="ok",
            entry_count=len(entries),
            entries=self._entry_rows(entries),
        )
        return entries


def _append_tool_audit(
    audit: dict[str, Any],
    *,
    iteration: int,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    duplicate_skipped: bool,
    operations: list[dict[str, Any]],
) -> None:
    audit["tool_calls"].append({
        "iteration": iteration,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "duplicate_skipped": duplicate_skipped,
        "backend_operations": operations,
    })
    if name not in audit["tools_used"]:
        audit["tools_used"].append(name)

    def add_file(bucket: str, uri: str, operation: str) -> None:
        if not uri:
            return
        rows = audit[bucket]
        row = next((item for item in rows if item["uri"] == uri), None)
        if row is None:
            row = {"uri": uri, "tools": [], "call_ids": [], "operations": []}
            rows.append(row)
        if name not in row["tools"]:
            row["tools"].append(name)
        if call_id not in row["call_ids"]:
            row["call_ids"].append(call_id)
        if operation not in row["operations"]:
            row["operations"].append(operation)

    for operation in operations:
        operation_name = str(operation.get("operation") or "")
        if operation_name == "fs_read" and operation.get("status") == "ok":
            add_file("read_files", str(operation.get("uri") or ""), operation_name)
        if operation_name not in {"fs_list", "fs_glob"}:
            continue
        for entry in operation.get("entries") or []:
            if not entry.get("is_dir"):
                add_file(
                    "discovered_files",
                    str(entry.get("uri") or ""),
                    operation_name,
                )


def chat_with_tools(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout_s: float,
    *,
    omit_temperature: bool = True,
    answer_temperature: float = 0.7,
) -> tuple[dict[str, Any], int, int, int, float, bool, dict[str, Any]]:
    url = f"{llm.base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": llm.model,
        "messages": messages,
        "stream": False,
        "max_tokens": llm.max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if not omit_temperature:
        payload["temperature"] = answer_temperature
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_sha256 = hashlib.sha256(data).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {llm.api_key}",
        "User-Agent": "Mozilla/5.0 (compatible; MemoryBenchmarkWorkbench/2.0)",
    }
    last_error = ""
    started = time.monotonic()
    for attempt in range(0, llm.max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=max(0.001, timeout_s)) as response:
                obj = json.loads(response.read().decode("utf-8"))
            choices = obj.get("choices") or []
            if not choices:
                raise RuntimeError("empty choices in model response")
            choice = choices[0] if isinstance(choices[0], dict) else {}
            message = dict(choice.get("message") or {})
            if not str(message.get("content") or "").strip() and not message.get("tool_calls"):
                raise RuntimeError("empty model response content")
            usage = obj.get("usage") or {}
            response_metadata = {
                "request_sha256": request_sha256,
                "request_model": llm.model,
                "response_id": str(obj.get("id") or ""),
                "response_model": str(obj.get("model") or ""),
                "system_fingerprint": str(obj.get("system_fingerprint") or ""),
                "created": obj.get("created"),
                "finish_reason": str(choice.get("finish_reason") or ""),
            }
            return (
                message,
                int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                attempt,
                time.monotonic() - started,
                isinstance(usage, dict) and bool(usage),
                response_metadata,
            )
        except Exception as exc:
            last_error = str(exc)
            if attempt < llm.max_retries:
                time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(last_error or "model call failed")


def _retrieval_rows(items: list[SearchResult]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in items]


def _unique_retrieval_items(
    items: list[SearchResult],
) -> list[SearchResult]:
    unique: list[SearchResult] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        native_uri = str(item.metadata.get("source_uri") or item.uri)
        key = (native_uri, item.content)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def answer_one_vikingbot_question(
    echomem: MemoryClient,
    llm: LLMClient,
    *,
    question_id: str,
    question: str,
    answer: str,
    question_time: str = "",
    top_k: int = 25,
    tool_search_limit: int = 25,
    tool_search_pool_multiplier: int = 4,
    initial_min_score: float = 0.0,
    tool_min_score: float = 0.0,
    tool_set: str = "search_read",
    user_memory_budget_chars: int = 4000,
    agent_memory_budget_chars: int = 2000,
    max_iterations: int = 50,
    question_timeout_s: float = 600.0,
    vikingbot_workspace: str = "",
    qa_profile: str = "vikingbot",
    profile_source: dict[str, Any] | None = None,
    sample_id: str = "",
    category: str = "",
    answer_temperature: float = 0.7,
    omit_answer_temperature: bool = True,
    initial_retrieval_query_mode: str = "question_only",
    tool_query_dedup_scope: str = "question",
    retrieval_uri_dedup: bool = True,
    search_tool_target_uri_schema: bool = False,
    tools_enabled: bool = True,
    system_prompt_append: str = "",
    system_prompt_append_sha256: str = "",
    system_prompt_append_source: str = "",
    search_resources_mode: bool = False,
    path_title_map: dict[str, str] | None = None,
) -> QAResult:
    started = time.monotonic()
    deadline = started + question_timeout_s if question_timeout_s > 0 else None

    def remaining() -> float:
        if deadline is None:
            return llm.timeout_s
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError(f"question deadline exceeded after {question_timeout_s:g}s")
        return max(0.001, value)

    items: list[SearchResult] = []
    retrieval_error = ""
    retrieval_latency_s = 0.0
    retrieval_started = time.monotonic()
    initial_query = (
        build_question_prompt(question, question_time)
        if initial_retrieval_query_mode == "vikingbot_prompt"
        else question
    )
    try:
        if search_resources_mode:
            raw_items = echomem.search_resources(
                initial_query,
                limit=top_k,
                tags=["hotpotqa"],
                timeout_s=remaining(),
            )
            items = [
                _resource_search_result(item, path_title_map)
                for item in raw_items
                if str(item.get("uri") or "").strip()
            ]
        else:
            items = echomem.search(initial_query, top_k=top_k, timeout_s=remaining())
        items = [
            item for item in items
            if item.score >= initial_min_score
        ]
        if retrieval_uri_dedup:
            deduped: list[SearchResult] = []
            seen_uris: set[str] = set()
            for item in items:
                if item.uri and item.uri in seen_uris:
                    continue
                if item.uri:
                    seen_uris.add(item.uri)
                deduped.append(item)
            items = deduped
    except Exception as exc:
        retrieval_error = str(exc)
    retrieval_latency_s += time.monotonic() - retrieval_started
    cache = {item.uri: item for item in items if item.uri}
    orchestration_started = time.monotonic()
    messages = build_messages(
        question,
        question_time,
        items,
        user_memory_budget_chars,
        agent_memory_budget_chars,
        vikingbot_workspace,
        qa_profile,
        system_prompt_append,
    )
    trace: dict[str, Any] = {
        "schema_version": 1,
        "agent": "vikingbot",
        "qa_profile": qa_profile,
        "profile_source": dict(profile_source or {}),
        "question_id": question_id,
        "sample_id": sample_id,
        "category": category,
        "question": question,
        "gold_answer": answer,
        "question_time": question_time,
        "settings": {
            "top_k": top_k,
            "tool_search_limit": tool_search_limit,
            "tool_search_pool_multiplier": tool_search_pool_multiplier,
            "initial_min_score": initial_min_score,
            "tool_min_score": tool_min_score,
            "tool_set": tool_set,
            "user_memory_budget_chars": user_memory_budget_chars,
            "agent_memory_budget_chars": agent_memory_budget_chars,
            "max_iterations": max_iterations,
            "question_timeout_s": question_timeout_s,
            "answer_temperature": answer_temperature,
            "omit_answer_temperature": omit_answer_temperature,
            "initial_retrieval_query_mode": initial_retrieval_query_mode,
            "tool_query_dedup_scope": tool_query_dedup_scope,
            "retrieval_uri_dedup": retrieval_uri_dedup,
            "search_tool_target_uri_schema": (
                search_tool_target_uri_schema
            ),
            "tools_enabled": tools_enabled,
            "search_resources_mode": search_resources_mode,
            "system_prompt_append_sha256": system_prompt_append_sha256,
            "system_prompt_append_source": system_prompt_append_source,
        },
        "model_request": {
            "base_url": llm.base_url.rstrip("/"),
            "model": llm.model,
            "max_tokens": llm.max_tokens,
            "temperature_mode": (
                "provider_default"
                if omit_answer_temperature
                else "explicit"
            ),
            "temperature": (
                None
                if omit_answer_temperature
                else answer_temperature
            ),
        },
        "initial_retrieval": {
            "query": initial_query,
            "items": _retrieval_rows(items),
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
    }
    orchestration_latency_s = time.monotonic() - orchestration_started
    tools = (
        tool_definitions(
            tool_set,
            search_target_uri=search_tool_target_uri_schema,
        )
        if tools_enabled
        else []
    )
    tool_protocol_payload = json.dumps(
        tools,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    trace["tool_protocol"] = {
        "names": [
            str(item.get("function", {}).get("name") or "")
            for item in tools
        ],
        "sha256": hashlib.sha256(tool_protocol_payload).hexdigest(),
    }
    prompt_tokens = 0
    completion_tokens = 0
    tool_retrieval_items: list[SearchResult] = []
    seen_search_queries: set[str] = set()
    tool_call_count = 0
    iterations = 0
    llm_latency_s = 0.0
    model_retry_count: int | None = 0
    model_usage_observed = False
    try:
        for iterations in range(1, max_iterations + 1):
            turn_search_queries = (
                seen_search_queries
                if tool_query_dedup_scope == "question"
                else (
                    set()
                    if tool_query_dedup_scope == "turn"
                    else None
                )
            )
            if omit_answer_temperature:
                chat_result = chat_with_tools(
                    llm,
                    messages,
                    tools,
                    remaining(),
                )
            else:
                chat_result = chat_with_tools(
                    llm,
                    messages,
                    tools,
                    remaining(),
                    omit_temperature=False,
                    answer_temperature=answer_temperature,
                )
            message, prompt_count, completion_count = chat_result[:3]
            if len(chat_result) >= 5:
                assert model_retry_count is not None
                model_retry_count += int(chat_result[3] or 0)
                llm_latency_s += float(chat_result[4] or 0.0)
            else:
                model_retry_count = None
            if len(chat_result) >= 6:
                model_usage_observed = (
                    model_usage_observed or bool(chat_result[5])
                )
            elif prompt_count or completion_count:
                model_usage_observed = True
            prompt_tokens += prompt_count
            completion_tokens += completion_count
            tool_calls = list(message.get("tool_calls") or [])
            iteration_trace: dict[str, Any] = {
                "iteration": iterations,
                "model_message": message,
                "prompt_tokens": prompt_count,
                "completion_tokens": completion_count,
                "tool_calls": [],
            }
            if len(chat_result) >= 7 and isinstance(chat_result[6], dict):
                iteration_trace["model_response"] = dict(chat_result[6])
            trace["iterations"].append(iteration_trace)
            if not tool_calls:
                raw_response = str(message.get("content") or "").strip()
                final_response = raw_response
                trace["raw_response"] = raw_response
                trace["final_response"] = final_response
                trace["answer_sanitized"] = final_response != raw_response
                return QAResult(
                    question_id=question_id,
                    question=question,
                    answer=answer,
                    response=final_response,
                    retrieval_items=_retrieval_rows(
                        _unique_retrieval_items([
                            *items,
                            *tool_retrieval_items,
                        ])
                    ),
                    retrieval_error=retrieval_error,
                    elapsed_s=time.monotonic() - started,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    tool_call_count=tool_call_count,
                    iterations=iterations,
                    qa_profile=qa_profile,
                    sample_id=sample_id,
                    category=category,
                    retrieval_latency_s=retrieval_latency_s,
                    orchestration_latency_s=orchestration_latency_s,
                    llm_latency_s=llm_latency_s,
                    model_retry_count=model_retry_count,
                    model_usage_observed=model_usage_observed,
                    trace=trace,
                )
            messages.append({
                "role": "assistant",
                "content": message.get("content") or " ",
                "tool_calls": tool_calls,
            })
            prepared_calls: list[dict[str, Any]] = []
            for tool_call in tool_calls:
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
                    arguments = {"query": str(raw_arguments)}
                normalized_query = ""
                if name == MEMORY_SEARCH_TOOL:
                    normalized_query = re.sub(
                        r"\s+",
                        " ",
                        str(arguments.get("query") or "").strip().lower(),
                    )
                duplicate_skipped = bool(
                    normalized_query
                    and turn_search_queries is not None
                    and normalized_query in turn_search_queries
                )
                if (
                    normalized_query
                    and turn_search_queries is not None
                    and not duplicate_skipped
                ):
                    turn_search_queries.add(normalized_query)
                prepared_calls.append({
                    "tool_call": tool_call,
                    "name": name,
                    "arguments": arguments,
                    "duplicate_skipped": duplicate_skipped,
                })

            def run_prepared_tool(
                prepared: dict[str, Any],
            ) -> tuple[str, list[SearchResult], float, list[dict[str, Any]]]:
                if prepared["duplicate_skipped"]:
                    return (
                        "Duplicate search skipped. Reformulate the query around a different "
                        "entity, event phrase, date clue, quote, object, or relation.",
                        [],
                        0.0,
                        [],
                    )
                tool_started = time.monotonic()
                audited_client = _AuditedMemoryClient(echomem)
                result_text, new_items = execute_tool(
                    audited_client,
                    str(prepared["name"]),
                    dict(prepared["arguments"]),
                    cache,
                    top_k=top_k,
                    tool_search_limit=tool_search_limit,
                    tool_search_pool_multiplier=tool_search_pool_multiplier,
                    tool_min_score=tool_min_score,
                    timeout_s=remaining(),
                    tool_set=tool_set,
                    search_resources=search_resources_mode,
                    path_title_map=path_title_map,
                )
                return (
                    result_text,
                    new_items,
                    time.monotonic() - tool_started,
                    audited_client.operations,
                )

            if len(prepared_calls) > 1:
                with ThreadPoolExecutor(
                    max_workers=len(prepared_calls)
                ) as tool_pool:
                    tool_results = list(
                        tool_pool.map(run_prepared_tool, prepared_calls)
                    )
            else:
                tool_results = [
                    run_prepared_tool(prepared)
                    for prepared in prepared_calls
                ]

            tool_batch_latency_s = max(
                (
                    result[2]
                    for prepared, result in zip(
                        prepared_calls,
                        tool_results,
                        strict=True,
                    )
                    if not prepared["duplicate_skipped"]
                ),
                default=0.0,
            )
            retrieval_latency_s += tool_batch_latency_s
            iteration_trace["tool_batch_latency_ms"] = round(
                tool_batch_latency_s * 1000,
                3,
            )
            for prepared, tool_result in zip(
                prepared_calls,
                tool_results,
                strict=True,
            ):
                tool_call = prepared["tool_call"]
                name = str(prepared["name"])
                arguments = dict(prepared["arguments"])
                result_text, new_items, tool_elapsed_s, backend_operations = (
                    tool_result
                )
                tool_call_count += 1
                tool_retrieval_items.extend(new_items)
                call_id = tool_call.get("id") or f"tool_{tool_call_count}"
                iteration_trace["tool_calls"].append({
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "duplicate_skipped": prepared["duplicate_skipped"],
                    "result": result_text,
                    "retrieval_items": _retrieval_rows(new_items),
                    "latency_ms": round(tool_elapsed_s * 1000, 3),
                    "backend_operations": backend_operations,
                })
                _append_tool_audit(
                    trace["tool_audit"],
                    iteration=iterations,
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                    duplicate_skipped=prepared["duplicate_skipped"],
                    operations=backend_operations,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": result_text,
                })
            messages.append({
                "role": "user",
                "content": "Reflect on the results and decide next steps.",
            })
        messages.append({
            "role": "user",
            "content": (
                "Tool-use iteration limit reached. Do not call any more tools. "
                "Answer the user's original request directly using only the "
                "conversation, tool calls, and tool results already available above. "
                "If the gathered information is incomplete, explain the best-known "
                "answer and clearly note what remains uncertain."
            ),
        })
        if omit_answer_temperature:
            chat_result = chat_with_tools(
                llm,
                messages,
                [],
                remaining(),
            )
        else:
            chat_result = chat_with_tools(
                llm,
                messages,
                [],
                remaining(),
                omit_temperature=False,
                answer_temperature=answer_temperature,
            )
        message, prompt_count, completion_count = chat_result[:3]
        if len(chat_result) >= 5:
            assert model_retry_count is not None
            model_retry_count += int(chat_result[3] or 0)
            llm_latency_s += float(chat_result[4] or 0.0)
        else:
            model_retry_count = None
        if len(chat_result) >= 6:
            model_usage_observed = (
                model_usage_observed or bool(chat_result[5])
            )
        elif prompt_count or completion_count:
            model_usage_observed = True
        prompt_tokens += prompt_count
        completion_tokens += completion_count
        raw_response = str(message.get("content") or "").strip()
        final_response = raw_response
        trace["forced_final_answer"] = {
            "model_message": message,
            "prompt_tokens": prompt_count,
            "completion_tokens": completion_count,
            "model_response": (
                dict(chat_result[6])
                if len(chat_result) >= 7 and isinstance(chat_result[6], dict)
                else {}
            ),
        }
        trace["raw_response"] = raw_response
        trace["final_response"] = final_response
        trace["answer_sanitized"] = final_response != raw_response
        return QAResult(
            question_id=question_id,
            question=question,
            answer=answer,
            response=final_response,
            retrieval_items=_retrieval_rows(
                _unique_retrieval_items([
                    *items,
                    *tool_retrieval_items,
                ])
            ),
            retrieval_error=retrieval_error,
            elapsed_s=time.monotonic() - started,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tool_call_count=tool_call_count,
            iterations=iterations,
            qa_profile=qa_profile,
            sample_id=sample_id,
            category=category,
            retrieval_latency_s=retrieval_latency_s,
            orchestration_latency_s=orchestration_latency_s,
            llm_latency_s=llm_latency_s,
            model_retry_count=model_retry_count,
            model_usage_observed=model_usage_observed,
            trace=trace,
        )
    except Exception as exc:
        trace["error"] = str(exc)
        trace["raw_response"] = ""
        trace["final_response"] = ""
        return QAResult(
            question_id=question_id,
            question=question,
            answer=answer,
            response="",
            retrieval_items=_retrieval_rows(
                _unique_retrieval_items([
                    *items,
                    *tool_retrieval_items,
                ])
            ),
            retrieval_error=retrieval_error,
            llm_error=str(exc),
            elapsed_s=time.monotonic() - started,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tool_call_count=tool_call_count,
            iterations=iterations,
            qa_profile=qa_profile,
            sample_id=sample_id,
            category=category,
            retrieval_latency_s=retrieval_latency_s,
            orchestration_latency_s=orchestration_latency_s,
            llm_latency_s=llm_latency_s,
            model_retry_count=model_retry_count,
            model_usage_observed=model_usage_observed,
            trace=trace,
        )
