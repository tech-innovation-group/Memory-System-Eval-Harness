from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _config_vlm(config_path: Path | None) -> dict[str, Any]:
    if not config_path:
        return {}
    try:
        return _read_json(config_path).get("vlm", {})
    except Exception:
        return {}


def _vlm_config_candidates(config_path: Path | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths = [
        config_path,
        Path.cwd() / "judge.conf",
        Path.home() / ".openviking" / "ov.conf",
    ]
    for path in paths:
        if not path:
            continue
        try:
            resolved = Path(path).expanduser().resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        vlm = _config_vlm(resolved)
        if isinstance(vlm, dict) and vlm:
            candidates.append(vlm)
    return candidates


def _normalize_base_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _config_base_url(config: dict[str, Any]) -> str:
    return str(config.get("api_base") or config.get("base_url") or "").strip()


def _base_matches(config_base: str, target_base: str) -> bool:
    source = _normalize_base_url(config_base)
    target = _normalize_base_url(target_base)
    return bool(source and target and source == target)


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _env_token_for_base(base_url: str) -> str:
    normalized = _normalize_base_url(base_url)
    if any(marker in normalized for marker in ("dashscope", "aliyun")):
        return _first_text(
            os.environ.get("DASHSCOPE_API_KEY"),
            os.environ.get("ECHOMEM_API_KEY"),
            os.environ.get("ECHOMEM_CHAT_API_KEY"),
            os.environ.get("MEMORY_INJECT_TOKEN"),
        )
    return _first_text(
        os.environ.get("JUDGE_TOKEN"),
        os.environ.get("ANSWER_TOKEN"),
        os.environ.get("LOCOMO_ANSWER_TOKEN"),
        os.environ.get("OPENAI_API_KEY"),
    )


def _config_token_for_base(configs: list[dict[str, Any]], base_url: str) -> str:
    for item in configs:
        token = str(item.get("api_key") or item.get("token") or "").strip()
        if token and _base_matches(_config_base_url(item), base_url):
            return token
    return ""


def _fallback_config_token(configs: list[dict[str, Any]]) -> str:
    for item in configs:
        token = str(item.get("api_key") or item.get("token") or "").strip()
        if token:
            return token
    return ""


def _response_snippet(raw: str) -> str:
    return " ".join((raw or "").strip().split())[:220]


def _parse_sse_chat_response(raw: str, service_name: str) -> dict[str, Any] | None:
    if not raw.lstrip().startswith("data:"):
        return None
    content_parts: list[str] = []
    model = ""
    usage: dict[str, Any] = {}
    saw_chunk = False
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            snippet = _response_snippet(payload)
            return {"error": f"{service_name} 流式响应包含非 JSON chunk；响应片段：{snippet}"}
        saw_chunk = True
        if isinstance(chunk, dict):
            if chunk.get("model"):
                model = str(chunk.get("model") or "")
            if isinstance(chunk.get("usage"), dict):
                usage = chunk.get("usage") or {}
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                message = choice.get("message") or {}
                piece = ""
                if isinstance(delta, dict):
                    piece = delta.get("content") or ""
                if not piece and isinstance(message, dict):
                    piece = message.get("content") or ""
                if piece:
                    content_parts.append(str(piece))
    if not saw_chunk:
        return {"error": f"{service_name} 返回空流式响应；请稍后重试。"}
    return {
        "choices": [{"message": {"content": "".join(content_parts)}}],
        "usage": usage,
        "model": model,
    }


def _read_json_response(response: Any, service_name: str) -> dict[str, Any]:
    raw = response.read().decode("utf-8", "replace")
    if not raw.strip():
        return {"error": f"{service_name} 返回空响应；请检查 base_url、model，或稍后重试。"}
    sse = _parse_sse_chat_response(raw, service_name)
    if sse is not None:
        return sse
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        snippet = _response_snippet(raw)
        suffix = f"；响应片段：{snippet}" if snippet else ""
        return {"error": f"{service_name} 返回非 JSON 响应；请检查 base_url 是否指向 OpenAI-compatible chat/completions 服务{suffix}"}
    if not isinstance(result, dict):
        return {"error": f"{service_name} 返回 JSON 结构异常；期望 object。"}
    return result


def _http_error_message(exc: HTTPError, service_name: str) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or str(error)
            else:
                message = parsed.get("message") or str(error or "")
            if message:
                return f"{service_name} HTTP {exc.code}: {message}"
    snippet = _response_snippet(raw)
    suffix = f"；响应片段：{snippet}" if snippet else ""
    return f"{service_name} HTTP {exc.code}: {exc.reason}{suffix}"


def openai_chat(
    messages: list[dict[str, Any]],
    model: str = "gpt-4",
    temperature: float = 0.7,
    api_key: str | None = None,
    base_url: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    configs = _vlm_config_candidates(config_path)
    primary_vlm = configs[0] if configs else {}
    resolved_base_url = (
        base_url
        or os.environ.get("JUDGE_BASE_URL")
        or str(primary_vlm.get("api_base") or primary_vlm.get("base_url") or "")
        or "https://api.openai.com/v1"
    )
    token = (
        api_key
        or _env_token_for_base(resolved_base_url)
        or _config_token_for_base(configs, resolved_base_url)
        or _fallback_config_token(configs)
    )
    if not token:
        return {"error": f"missing model API key for {resolved_base_url}"}
    try:
        req = Request(
            f"{resolved_base_url.rstrip('/')}/chat/completions",
            json.dumps({"model": model, "messages": messages, "temperature": temperature}).encode("utf-8"),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urlopen(req, timeout=60) as response:
            result = _read_json_response(response, "模型服务")
        if "error" in result:
            return result
        usage = result.get("usage") or {}
        choices = result.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return {"error": "模型服务返回缺少 choices；请检查模型名称或上游服务兼容性。"}
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            return {"error": "模型服务返回缺少 message.content；请检查模型名称或上游服务兼容性。"}
        if not str(content).strip():
            finish_reason = choices[0].get("finish_reason")
            suffix = f" finish_reason={finish_reason}" if finish_reason else ""
            return {"error": f"模型服务返回了空 message.content；请检查 Agent 模型是否支持 chat/completions、是否需要关闭流式/思考输出，或稍后重试。{suffix}"}
        return {
            "answer": content,
            "tokens": {
                "prompt": usage.get("prompt_tokens"),
                "completion": usage.get("completion_tokens"),
                "total": usage.get("total_tokens"),
            },
            "model": result.get("model") or model,
        }
    except HTTPError as exc:
        return {"error": _http_error_message(exc, "模型服务")}
    except URLError as exc:
        return {"error": f"模型服务连接失败：{exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


def claude_chat(messages: list[dict[str, Any]], model: str = "claude-opus-4", temperature: float = 0.7) -> dict[str, Any]:
    base_url = os.environ.get("CLAUDE_BASE_URL", "https://api.anthropic.com")
    api_key = os.environ.get("CLAUDE_API_KEY", "")
    if not api_key:
        return {"error": "missing Claude API key"}
    try:
        system_messages = [m["content"] for m in messages if m.get("role") == "system"]
        user_messages = [m for m in messages if m.get("role") != "system"]
        data: dict[str, Any] = {
            "model": model,
            "messages": user_messages,
            "max_tokens": 4096,
            "temperature": temperature,
        }
        if system_messages:
            data["system"] = "\n\n".join(system_messages)
        req = Request(
            f"{base_url.rstrip('/')}/v1/messages",
            json.dumps(data).encode("utf-8"),
            {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urlopen(req, timeout=60) as response:
            result = _read_json_response(response, "Claude 服务")
        if "error" in result:
            return result
        usage = result.get("usage") or {}
        prompt_tokens = usage.get("input_tokens")
        completion_tokens = usage.get("output_tokens")
        content_items = result.get("content") or []
        if not content_items or not isinstance(content_items[0], dict):
            return {"error": "Claude 服务返回缺少 content；请检查模型名称或上游服务兼容性。"}
        return {
            "answer": content_items[0].get("text") or "",
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": (prompt_tokens or 0) + (completion_tokens or 0),
            },
            "model": result.get("model") or model,
        }
    except HTTPError as exc:
        return {"error": _http_error_message(exc, "Claude 服务")}
    except URLError as exc:
        return {"error": f"Claude 服务连接失败：{exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}
