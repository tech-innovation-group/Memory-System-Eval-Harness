"""EchoAgent backend HTTP client (moved from dynamic/run_eval.py).

All calls have graceful failure: prefetch endpoints return None on 404,
seq conflicts retry up to 3 times.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger("echo_agent_client")


def _encode_context_path(context_path: str) -> str:
    return quote(context_path, safe="")


class EchoAgentClient:
    """EchoAgent backend HTTP client, all calls have fallback."""

    def __init__(self, base_url: str, username: str, password: str,
                 api_prefix: str = "/v1"):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix.rstrip("/")
        self.username = username
        self.password = password
        self.token: str = ""
        self.user_uuid: str = ""
        self._context_seq: dict[str, int] = {}

    def _headers(self, json_content: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body or {}).encode("utf-8") if body else None
        req = Request(url, data=data, headers=self._headers(), method=method)
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}

    def login(self) -> None:
        """Login and obtain JWT token."""
        body = {"username": self.username, "password": self.password}
        data = json.dumps(body).encode("utf-8")
        req = Request(
            f"{self.base_url}{self.api_prefix}/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8", "replace"))
        self.token = result.get("access_token") or ""
        if not self.token:
            for cookie_header in resp.headers.get_all("Set-Cookie") or []:
                if "access_token=" in cookie_header:
                    self.token = cookie_header.split("access_token=")[1].split(";")[0]
        if not self.token:
            raise RuntimeError(f"登录成功但未获取 token: {list(result.keys())}")
        user_info = result.get("user") or {}
        self.user_uuid = user_info.get("id") or ""

    def get_memory_auth_key(self, memory_engine_endpoint: str) -> str:
        """Resolve the auth_key that EchoAgent uses for memory retrieval.

        EchoAgent's backend sends the logged-in user's UUID to the echoagent
        plugin (31030), which maps it to an auth_key via echoagent_registry.json.
        Injection must use the same auth_key, otherwise memories are stored
        under one tenant and retrieved under another.
        """
        body = {"mode": "credential", "userId": self.user_uuid or "anonymous"}
        data = json.dumps(body).encode("utf-8")
        req = Request(
            memory_engine_endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8", "replace"))
        auth_key = result.get("result", {}).get("authKey", "")
        if not auth_key:
            raise RuntimeError(f"credential 接口未返回 authKey: {result}")
        return auth_key

    def create_session(self, title: str = "", memory_engine_endpoint: str = "") -> str:
        """Create a session, try to enable memory engine (ignore failure)."""
        result = self._request("POST", f"{self.api_prefix}/sessions", {"title": title or f"test-{uuid.uuid4().hex[:8]}"})
        session_id = result.get("data", result).get("id") or result.get("id", "")
        if session_id and memory_engine_endpoint:
            try:
                self._request("POST", f"{self.api_prefix}/sessions/{session_id}/memory-engine/test",
                              {"endpoint": memory_engine_endpoint})
                self._request("PUT", f"{self.api_prefix}/sessions/{session_id}/memory-engine",
                              {"enabled": True, "endpoint": memory_engine_endpoint})
            except Exception as exc:
                logging.warning("启用记忆引擎失败 (session %s): %s", session_id, exc)
        return session_id

    def prefetch_tick(self, session_id: str, context_path: str, client_turn_id: str,
                      revision: int, draft_text: str) -> dict[str, Any] | None:
        """Typing simulation tick. Returns None if endpoint absent."""
        path = f"{self.api_prefix}/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/prefetch/tick"
        try:
            return self._request("POST", path, {
                "clientTurnId": client_turn_id,
                "revision": revision,
                "draftText": draft_text,
            })
        except HTTPError as e:
            if e.code == 404:
                logging.debug("prefetch/tick 不存在 (404), 跳过打字模拟")
                return None
            raise
        except Exception as e:
            logging.debug("prefetch/tick 失败: %s", e)
            return None

    def prefetch_finalize(self, session_id: str, context_path: str, client_turn_id: str,
                          full_content: str) -> dict[str, Any] | None:
        """Finalize typing simulation. Returns None if endpoint absent."""
        path = f"{self.api_prefix}/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/prefetch/finalize"
        try:
            return self._request("POST", path, {
                "clientTurnId": client_turn_id,
                "fullContent": full_content,
            })
        except HTTPError as e:
            if e.code == 404:
                logging.debug("prefetch/finalize 不存在 (404), 跳过")
                return None
            raise
        except Exception as e:
            logging.debug("prefetch/finalize 失败: %s", e)
            return None

    def send_message(self, session_id: str, context_path: str, content: str,
                     prefetch_client_turn_id: str = "") -> dict[str, Any]:
        """Send a message, auto-retry on seq conflicts."""
        key = f"{session_id}:{context_path}"
        after_seq = self._context_seq.get(key, 0)
        path = f"{self.api_prefix}/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/messages"
        result = {}
        for attempt in range(3):
            body: dict[str, Any] = {"content": content, "afterSeq": after_seq}
            if prefetch_client_turn_id:
                body["prefetchClientTurnId"] = prefetch_client_turn_id
            result = self._request("POST", path, body)
            data = result.get("data", result)
            server_seq = data.get("latestContextSeq")
            if isinstance(server_seq, int):
                self._context_seq[key] = server_seq
            if data.get("error") in ("CONTEXT_SEQ_OUTDATED", "SEQ_OUTDATED") and isinstance(server_seq, int):
                after_seq = server_seq
                continue
            return result
        return result

    def stream_reply(self, session_id: str, context_path: str, seq: int,
                     timeout: float = 300) -> dict[str, Any]:
        """Read SSE stream, return {reply, ttft_ms, done_event}."""
        url = (f"{self.base_url}{self.api_prefix}/sessions/{session_id}/context-paths/"
               f"{_encode_context_path(context_path)}/streaming?seq={seq}")
        headers = self._headers(json_content=False)
        headers["Accept"] = "text/event-stream"
        headers["Last-Event-ID"] = "-1"
        req = Request(url, headers=headers)

        reply_parts: list[str] = []
        ttft_ms: float | None = None
        send_time = time.monotonic()
        done_event: dict[str, Any] = {}

        with urlopen(req, timeout=timeout) as resp:
            raw_buffer = b""
            text_buffer = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                raw_buffer += chunk
                try:
                    text = raw_buffer.decode("utf-8")
                    raw_buffer = b""
                except UnicodeDecodeError:
                    text = raw_buffer[:-3].decode("utf-8", errors="replace")
                    raw_buffer = raw_buffer[-3:]
                text_buffer += text
                while "\n\n" in text_buffer:
                    event_block, text_buffer = text_buffer.split("\n\n", 1)
                    event_type = ""
                    data_lines: list[str] = []
                    for line in event_block.splitlines():
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):])
                    event_data = "\n".join(data_lines)
                    if not event_data:
                        continue
                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        continue
                    if event_type in ("create", "append"):
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        fragment = data.get("fragment") or data.get("content") or ""
                        if isinstance(fragment, dict):
                            reply_parts.append(fragment.get("content") or "")
                        else:
                            reply_parts.append(str(fragment))
                    elif event_type == "done":
                        done_event = data if isinstance(data, dict) else {}
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}
                    elif event_type == "error":
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms,
                                "error": str(data), "done_event": {}}
        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}

    def get_last_request(self, session_id: str, context_path: str = "/") -> dict[str, Any]:
        try:
            path = (f"{self.api_prefix}/sessions/{session_id}/primary-model/last-request"
                    f"?contextPath={_encode_context_path(context_path)}")
            return self._request("GET", path)
        except Exception:
            return {}
