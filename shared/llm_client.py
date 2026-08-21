"""LLM client for OpenAI-compatible chat completions (urllib, no third-party deps)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

logger = logging.getLogger("llm_client")


@dataclass
class LLMResponse:
    """Result of a single LLM call."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float
    error: str = ""
    retry_count: int = 0
    usage_observed: bool = False


@dataclass
class LLMToolResponse:
    """Result of an LLM call with tool definitions."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    retry_count: int = 0
    usage_observed: bool = False


class LLMClient:
    """Synchronous OpenAI-compatible chat completion client.

    Uses urllib so there are zero third-party dependencies.  Designed to be
    called from a ``ThreadPoolExecutor`` for concurrent QA.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "doubao-seed-2.0-pro",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout_s: float = 120.0,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_s: float | None = None,
        temperature: float | None = None,
        response_format: bool = False,
        thinking_disabled: bool = False,
        omit_max_tokens: bool = False,
    ) -> LLMResponse:
        """Call /v1/chat/completions and return the response.

        Args:
            messages: OpenAI-format message list ``[{role, content}, ...]``.
            temperature: sampling temperature override; ``None`` uses the
                client's configured value.
            response_format: request JSON mode (``response_format``
                ``{"type": "json_object"}``) when the provider supports it;
                forces structured JSON output from the model.
            thinking_disabled: request ``thinking: {"type": "disabled"}`` when
                the provider supports it.  Reasoning models otherwise spend the
                output budget on a chain-of-thought and truncate the answer.
            omit_max_tokens: do not send ``max_tokens`` at all, leaving the
                output budget to the provider's default.  Judge calls use this
                so a reasoning model can finish a long verdict instead of
                having it truncated by a small configured cap.

        Returns:
            LLMResponse with content, token usage, and timing.
        """
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": (
                self.temperature if temperature is None else temperature
            ),
        }
        if not omit_max_tokens:
            payload["max_tokens"] = self.max_tokens
        if response_format:
            payload["response_format"] = {"type": "json_object"}
        if thinking_disabled:
            payload["thinking"] = {"type": "disabled"}
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        start = time.monotonic()
        request_timeout = self.timeout_s if timeout_s is None else max(0.001, timeout_s)
        deadline = start + request_timeout
        last_err: str = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LLM deadline exceeded after {request_timeout:g}s")
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=min(self.timeout_s, remaining)) as resp:
                    raw = resp.read().decode("utf-8")
                    obj = json.loads(raw)
                    choice = obj.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    content = message.get("content") or ""
                    usage = obj.get("usage", {})
                    # An empty visible completion is not a success: it is a
                    # safety-filtered output (finish_reason="content_filter")
                    # or a thinking model that put its answer in
                    # ``reasoning_content``.  Raise so the retry loop recovers
                    # it instead of silently grading on nothing.
                    if not content.strip():
                        reasoning_content = message.get("reasoning_content") or ""
                        if reasoning_content.strip():
                            content = reasoning_content
                        else:
                            finish_reason = choice.get("finish_reason")
                            raise RuntimeError(
                                "empty completion"
                                + (
                                    f" (finish_reason={finish_reason!r})"
                                    if finish_reason
                                    else ""
                                )
                            )
                    elapsed = time.monotonic() - start
                    return LLMResponse(
                        content=content,
                        prompt_tokens=int(usage.get("prompt_tokens", 0)),
                        completion_tokens=int(usage.get("completion_tokens", 0)),
                        elapsed_s=elapsed,
                        retry_count=attempt - 1,
                        usage_observed=isinstance(usage, dict) and bool(usage),
                    )
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {body}"
                logger.warning("LLM call failed: %s (attempt %d/%d)", last_err, attempt, self.max_retries)
                if e.code >= 500 and attempt < self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        last_err = f"LLM deadline exceeded after {request_timeout:g}s"
                        break
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    return LLMResponse(
                        content="",
                        prompt_tokens=0,
                        completion_tokens=0,
                        elapsed_s=time.monotonic() - start,
                        error=last_err,
                        retry_count=attempt - 1,
                    )
            except Exception as e:
                last_err = str(e)
                logger.warning("LLM call error: %s (attempt %d/%d)", last_err, attempt, self.max_retries)
                if attempt < self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        last_err = f"LLM deadline exceeded after {request_timeout:g}s"
                        break
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    return LLMResponse(
                        content="",
                        prompt_tokens=0,
                        completion_tokens=0,
                        elapsed_s=time.monotonic() - start,
                        error=last_err,
                        retry_count=attempt - 1,
                    )
        return LLMResponse(
            content="",
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_s=time.monotonic() - start,
            error=last_err,
            retry_count=max(0, self.max_retries - 1),
        )

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float | None = None,
        tool_choice: str = "auto",
    ) -> LLMToolResponse:
        """Call /v1/chat/completions with tool definitions.

        Like ``chat()`` but includes ``tools`` and ``tool_choice`` in the
        payload and parses ``tool_calls`` from the response message.
        """
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        start = time.monotonic()
        request_timeout = self.timeout_s if timeout_s is None else max(0.001, timeout_s)
        deadline = start + request_timeout
        last_err: str = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LLM deadline exceeded after {request_timeout:g}s")
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=min(self.timeout_s, remaining)) as resp:
                    raw = resp.read().decode("utf-8")
                    obj = json.loads(raw)
                    choice = obj.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    content = message.get("content") or ""
                    tool_calls = message.get("tool_calls") or []
                    usage = obj.get("usage", {})
                    # An empty completion with no tool call is not a success:
                    # it is a safety-filtered output
                    # (finish_reason="content_filter") or a thinking model
                    # that put its answer in ``reasoning_content``.  Raise so
                    # the retry loop recovers it instead of silently recording
                    # an empty answer.
                    if not content.strip() and not tool_calls:
                        reasoning_content = message.get("reasoning_content") or ""
                        if reasoning_content.strip():
                            content = reasoning_content
                        else:
                            finish_reason = choice.get("finish_reason")
                            raise RuntimeError(
                                "empty completion"
                                + (
                                    f" (finish_reason={finish_reason!r})"
                                    if finish_reason
                                    else ""
                                )
                            )
                    elapsed = time.monotonic() - start
                    return LLMToolResponse(
                        content=content,
                        tool_calls=tool_calls,
                        prompt_tokens=int(usage.get("prompt_tokens", 0)),
                        completion_tokens=int(usage.get("completion_tokens", 0)),
                        elapsed_s=elapsed,
                        retry_count=attempt - 1,
                        usage_observed=isinstance(usage, dict) and bool(usage),
                    )
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {body}"
                logger.warning("LLM tool call failed: %s (attempt %d/%d)", last_err, attempt, self.max_retries)
                if e.code >= 500 and attempt < self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        last_err = f"LLM deadline exceeded after {request_timeout:g}s"
                        break
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    return LLMToolResponse(
                        error=last_err,
                        elapsed_s=time.monotonic() - start,
                        retry_count=attempt - 1,
                    )
            except Exception as e:
                last_err = str(e)
                logger.warning("LLM tool call error: %s (attempt %d/%d)", last_err, attempt, self.max_retries)
                if attempt < self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        last_err = f"LLM deadline exceeded after {request_timeout:g}s"
                        break
                    time.sleep(min(self.retry_backoff_s * attempt, remaining))
                else:
                    return LLMToolResponse(
                        error=last_err,
                        elapsed_s=time.monotonic() - start,
                        retry_count=attempt - 1,
                    )
        return LLMToolResponse(
            error=last_err,
            elapsed_s=time.monotonic() - start,
            retry_count=max(0, self.max_retries - 1),
        )

    def judge(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        thinking_disabled: bool = True,
    ) -> str:
        """Judge helper: force JSON, disable thinking, and cap no output.

        Judge calls request ``response_format`` JSON mode and turn off
        reasoning by default (``thinking: {"type": "disabled"}``) so the model
        returns a parseable verdict instead of spending the budget on a
        chain-of-thought.  ``max_tokens`` is deliberately not sent: a small
        configured cap would truncate a long verdict.

        ``thinking_disabled`` can be overridden by callers that want the
        model to reason before judging.
        """
        resp = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=True,
            thinking_disabled=thinking_disabled,
            omit_max_tokens=True,
        )
        if resp.error:
            raise RuntimeError(f"judge call failed: {resp.error}")
        if not resp.content.strip():
            raise RuntimeError("judge call returned an empty response")
        return resp.content


_T = TypeVar("_T")


def chat_with_repair(
    llm,
    system_prompt: str,
    user_prompt: str,
    *,
    repair_prompt: str,
    parse: Callable[[str], _T],
    attempts: int = 3,
    retry_temperature: float = 0.3,
    retry_backoff_s: float = 1.0,
    response_format: bool = True,
    thinking_disabled: bool = True,
    omit_max_tokens: bool = True,
) -> _T:
    """Call ``llm.chat`` and parse the completion, retrying on bad output.

    Small judge models intermittently return empty/filtered completions or
    malformed text.  Instead of repeating the identical deterministic request
    (temperature 0 reproduces the same failure), retries append
    ``repair_prompt`` to the user message and raise the temperature above 0.

    ``parse`` decides whether a completion is usable and must raise on
    unusable output (empty, ambiguous, or malformed).  Returns the first
    successful parse; raises the last transport/parse error after
    ``attempts`` tries.

    This is a judge helper, so by default it forces JSON mode
    (``response_format``), disables reasoning (``thinking_disabled``), and
    sends no ``max_tokens`` cap (``omit_max_tokens``) — see ``LLMClient.chat``.
    Callers parsing plain non-JSON output (e.g. a yes/no verdict) pass
    ``response_format=False``.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        response = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        user_prompt if attempt == 1
                        else user_prompt + repair_prompt
                    ),
                },
            ],
            temperature=(retry_temperature if attempt > 1 else None),
            response_format=response_format,
            thinking_disabled=thinking_disabled,
            omit_max_tokens=omit_max_tokens,
        )
        if response.error:
            last_error = RuntimeError(response.error)
        else:
            try:
                return parse(response.content)
            except Exception as exc:
                last_error = exc
        if attempt < attempts:
            time.sleep(min(retry_backoff_s * attempt, 4))
    assert last_error is not None
    raise last_error
