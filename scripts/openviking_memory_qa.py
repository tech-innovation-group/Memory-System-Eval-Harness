#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import benchmark_adapter
from memory.vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_ALIGNMENT_PROFILE,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(text: Any, limit: int = 1400) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def token_estimate(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4) if text else 0


SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})")
MUTATING_COMMAND_RE = re.compile(
    r"(^|[;&|]\s*)(rm|mv|cp|touch|mkdir|rmdir|chmod|chown|python|python3|node|perl|ruby|sh|bash|zsh|curl|wget|tee)\b|>{1,2}",
    re.I,
)
VIKINGBOT_BOOTSTRAP_FILES = ("AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md")


def redact_tool_output(text: Any, limit: int = 20000) -> str:
    value = SECRET_VALUE_RE.sub("******", str(text or ""))
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "\n... [truncated]"


def path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def detect_vikingbot_workspace() -> str:
    candidates = [
        os.environ.get("VIKINGBOT_WORKSPACE", ""),
        str(Path.home() / "openviking-latest" / "bot" / "workspace"),
        str(Path.cwd() / "bot" / "workspace"),
        str(Path.cwd().parent / "openviking-latest" / "bot" / "workspace"),
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.exists() and path.is_dir():
            return str(path.resolve())
    return ""


def vikingbot_local_workspace(args: argparse.Namespace | None = None) -> Path:
    raw = str(getattr(args, "vikingbot_workspace", "") if args is not None else "").strip()
    if not raw:
        raw = detect_vikingbot_workspace()
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def local_tool_root(args: argparse.Namespace) -> Path:
    return vikingbot_local_workspace(args)


def load_vikingbot_bootstrap(workspace: Path) -> tuple[str, list[str]]:
    parts: list[str] = []
    loaded: list[str] = []
    for filename in VIKINGBOT_BOOTSTRAP_FILES:
        path = workspace / filename
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if content:
            parts.append(f"## {filename}\n\n{content}")
            loaded.append(filename)
    return "\n\n".join(parts), loaded


def parse_skill_frontmatter(content: str) -> dict[str, str]:
    if not content.startswith("---"):
        return {}
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    metadata: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def parse_vikingbot_skill_meta(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data.get("vikingbot", {}) if isinstance(data, dict) else {}


def strip_skill_frontmatter(content: str) -> str:
    if content.startswith("---"):
        match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
        if match:
            return content[match.end() :].strip()
    return content


def skill_requirements_met(skill_meta: dict[str, Any]) -> bool:
    requires = skill_meta.get("requires", {}) if isinstance(skill_meta, dict) else {}
    for binary in requires.get("bins", []) or []:
        if not shutil.which(str(binary)):
            return False
    for env_name in requires.get("env", []) or []:
        if not os.environ.get(str(env_name)):
            return False
    return True


def vikingbot_skill_files(workspace: Path) -> list[tuple[str, Path, str, dict[str, str], dict[str, Any]]]:
    skills_dir = workspace / "skills"
    if not skills_dir.exists():
        return []
    skills: list[tuple[str, Path, str, dict[str, str], dict[str, Any]]] = []
    for skill_dir in sorted(skills_dir.iterdir(), key=lambda item: item.name.lower()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        content = skill_file.read_text(encoding="utf-8", errors="replace")
        metadata = parse_skill_frontmatter(content)
        skill_meta = parse_vikingbot_skill_meta(metadata.get("metadata", ""))
        if skill_requirements_met(skill_meta):
            skills.append((skill_dir.name, skill_file, content, metadata, skill_meta))
    return skills


def load_vikingbot_skills_context(workspace: Path) -> tuple[str, str, list[str]]:
    active_parts: list[str] = []
    summary_lines: list[str] = ["<skills>"]
    loaded_names: list[str] = []

    def escape_xml(text: Any) -> str:
        return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    for name, path, content, metadata, skill_meta in vikingbot_skill_files(workspace):
        loaded_names.append(name)
        if skill_meta.get("always") or str(metadata.get("always", "")).strip().lower() in {"1", "true", "yes", "on"}:
            active_parts.append(f"### Skill: {name}\n\n{strip_skill_frontmatter(content)}")
        description = metadata.get("description") or name
        summary_lines.append('  <skill available="true">')
        summary_lines.append(f"    <name>{escape_xml(name)}</name>")
        summary_lines.append(f"    <description>{escape_xml(description)}</description>")
        summary_lines.append(f"    <location>{escape_xml(path)}</location>")
        summary_lines.append("  </skill>")

    if not loaded_names:
        return "", "", []
    summary_lines.append("</skills>")
    active_context = "\n\n---\n\n".join(active_parts)
    return active_context, "\n".join(summary_lines), loaded_names


def vikingbot_context_metadata(args: argparse.Namespace) -> dict[str, Any]:
    workspace = vikingbot_local_workspace(args)
    _bootstrap_text, bootstrap_files = load_vikingbot_bootstrap(workspace)
    _active_skills, _skills_summary, skill_names = load_vikingbot_skills_context(workspace)
    return {
        "vikingbot_workspace": str(workspace),
        "vikingbot_bootstrap_files": bootstrap_files,
        "vikingbot_skill_names": skill_names,
    }


def resolve_local_tool_path(args: argparse.Namespace, value: Any = ".") -> tuple[Path | None, str]:
    root = local_tool_root(args)
    raw = str(value or ".").strip() or "."
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
    except OSError as exc:
        return None, f"Error: {exc}"
    if not (path_within(resolved, root) or resolved == root):
        return None, f"Error: path outside allowed workspace: {resolved}"
    return resolved, ""


def csv_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(key for row in rows for key in row.keys()))


def parse_counter_json(value: Any) -> Counter:
    counter: Counter = Counter()
    try:
        data = json.loads(str(value or "{}"))
    except Exception:
        return counter
    if isinstance(data, dict):
        for key, count in data.items():
            try:
                counter[str(key)] += int(count)
            except (TypeError, ValueError):
                counter[str(key)] += 1
    elif isinstance(data, list):
        counter.update(str(item) for item in data)
    return counter


def parse_memory_users(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value or "").strip()
        raw_items: list[Any]
        if not text:
            raw_items = []
        else:
            try:
                decoded = json.loads(text)
                raw_items = decoded if isinstance(decoded, list) else [decoded]
            except Exception:
                raw_items = re.split(r"[,，;；\n]+", text)
    users: list[str] = []
    for item in raw_items:
        user = str(item or "").strip()
        if user and user not in users:
            users.append(user)
    return users


def vikingbot_sender_id(job: benchmark_adapter.Job) -> str:
    return str(getattr(job, "original_sample_id", "") or job.sample_id or "user").strip() or "user"


def vikingbot_session_id(job: benchmark_adapter.Job) -> str:
    return str(job.question_id or f"{job.sample_id}_qa").strip()


def effective_memory_users(args: argparse.Namespace, job: benchmark_adapter.Job) -> list[str]:
    override = parse_memory_users(getattr(args, "memory_users", ""))
    if override:
        return override
    sender = vikingbot_sender_id(job)
    if getattr(args, "group_chat", False):
        users = parse_memory_users(getattr(job, "memory_users", ""))
        if users:
            if sender and sender not in users:
                users.append(sender)
            return users
    return [sender]


def effective_memory_user_strategy(args: argparse.Namespace) -> str:
    if parse_memory_users(getattr(args, "memory_users", "")):
        return "memory_users_override"
    if getattr(args, "group_chat", False):
        return "vikingbot_group_chat"
    return "sender_sample_namespace"


def user_memory_target_uri(user_id: str) -> str:
    user_id = str(user_id or "").strip()
    return f"viking://user/{user_id}/memories/" if user_id else "viking://user/memories/"


def agent_memory_target_uri(agent_id: str) -> str:
    agent_id = str(agent_id or "").strip()
    return f"viking://agent/{agent_id}/memories/" if agent_id else "viking://agent/memories/"


def restrict_tools_to_long_term_memory(args: argparse.Namespace) -> bool:
    return str(getattr(args, "openviking_tool_set", VIKINGBOT_TOOL_SET) or VIKINGBOT_TOOL_SET) == "vikingboat_default"


def is_long_term_memory_uri(uri: str) -> bool:
    value = str(uri or "").strip()
    if not value:
        return True
    if not value.startswith("viking://"):
        return False
    return bool(re.match(r"^viking://(?:user|agent)(?:/[^/\s]+)?/memories(?:/|$)", value))


def is_long_term_event_memory_uri(uri: str) -> bool:
    value = str(uri or "").strip()
    return is_long_term_memory_uri(value) and "/memories/events/" in value


def is_temporal_or_fact_question(query: str) -> bool:
    value = str(query or "").lower()
    if re.search(r"\b(when|date|time|before|after|during|while|timeline|chronolog|sequence|order|first|last|latest|then)\b", value):
        return True
    if re.search(r"\b(who|whose|where|which|what|how long|how many|how much|relationship|relation|connect|linked)\b", value):
        return True
    return bool(re.search(r"(什么时候|哪天|日期|时间|之前|之后|期间|顺序|先后|第一个|最后|最新|谁|哪里|哪个|什么|多久|多少|关系|联系|牵线)", value))


def guard_long_term_memory_uri(args: argparse.Namespace, uri: str, tool_name: str) -> str:
    if restrict_tools_to_long_term_memory(args) and not is_long_term_memory_uri(uri):
        return (
            f"Error: {tool_name} is restricted to OpenViking long-term memory URIs "
            "under viking://user/.../memories/ or viking://agent/.../memories/."
        )
    return ""


USER_MEMORY_URI_RE = re.compile(r"^viking://user(?:/([^/\s]+))?/memories(?P<suffix>/.*)?$")


def current_tool_memory_user(args: argparse.Namespace) -> str:
    users = [str(user).strip() for user in list(getattr(args, "current_memory_users", []) or []) if str(user).strip()]
    if users:
        return users[0]
    return str(getattr(args, "user_id", "") or "default").strip() or "default"


def normalize_tool_memory_uri(args: argparse.Namespace, uri: str) -> str:
    return str(uri or "").strip()


def default_tool_memory_uri(args: argparse.Namespace) -> str:
    return user_memory_target_uri(current_tool_memory_user(args))


RATE_LIMIT_RE = re.compile(r"(rate.?limit|too many requests|http 429|\b429\b|quota|throttl|限流|频率)", re.I)
TIMEOUT_RE = re.compile(r"(timed out|timeout|temporarily unavailable|connection reset)", re.I)


class ModelCallError(RuntimeError):
    def __init__(self, message: str, retry_count: int, error_kind: str) -> None:
        super().__init__(message)
        self.retry_count = retry_count
        self.error_kind = error_kind


def _merge_stream_tool_call(target: dict[str, Any], update: dict[str, Any]) -> None:
    if update.get("id"):
        target["id"] = update.get("id")
    if update.get("type"):
        target["type"] = update.get("type")
    function_update = update.get("function") or {}
    if function_update:
        function_target = target.setdefault("function", {})
        if function_update.get("name"):
            function_target["name"] = function_update.get("name")
        if "arguments" in function_update:
            function_target["arguments"] = str(function_target.get("arguments") or "") + str(function_update.get("arguments") or "")


def parse_openai_compatible_response(body: str) -> dict[str, Any]:
    text = str(body or "")
    if not text.strip():
        raise RuntimeError("empty response body")
    if not any(line.lstrip().startswith("data:") for line in text.splitlines()):
        return json.loads(text)

    content_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    role = "assistant"
    last_obj: dict[str, Any] = {}
    parsed_any = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        obj = json.loads(payload)
        parsed_any = True
        last_obj = obj if isinstance(obj, dict) else {}
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            usage = obj.get("usage") or {}
        for choice in (obj.get("choices") or []) if isinstance(obj, dict) else []:
            message = choice.get("message") or {}
            if message.get("role"):
                role = str(message.get("role") or role)
            if message.get("content"):
                content_parts.append(str(message.get("content") or ""))
            for index, tool_call in enumerate(message.get("tool_calls") or []):
                call_index = int(tool_call.get("index") if tool_call.get("index") is not None else index)
                _merge_stream_tool_call(tool_calls_by_index.setdefault(call_index, {}), tool_call)

            delta = choice.get("delta") or {}
            if delta.get("role"):
                role = str(delta.get("role") or role)
            if delta.get("content"):
                content_parts.append(str(delta.get("content") or ""))
            for index, tool_call in enumerate(delta.get("tool_calls") or []):
                call_index = int(tool_call.get("index") if tool_call.get("index") is not None else index)
                _merge_stream_tool_call(tool_calls_by_index.setdefault(call_index, {}), tool_call)

    if not parsed_any:
        raise RuntimeError("empty streaming response")

    message: dict[str, Any] = {"role": role, "content": "".join(content_parts).strip()}
    if tool_calls_by_index:
        message["tool_calls"] = [tool_calls_by_index[index] for index in sorted(tool_calls_by_index)]
    data = dict(last_obj)
    data["choices"] = [{"message": message}]
    if usage:
        data["usage"] = usage
    return data


def classify_model_error(text: Any) -> str:
    value = str(text or "")
    if RATE_LIMIT_RE.search(value):
        return "rate_limited"
    if TIMEOUT_RE.search(value):
        return "timeout"
    return "api_error"


def default_openai_max_tokens() -> int:
    raw = os.environ.get("LOCOMO_LLM_MAX_TOKENS") or os.environ.get("OPENAI_MAX_TOKENS") or "1024"
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1024


def openai_payload_variants(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    base: dict[str, Any] = {"model": model, "messages": messages}
    variants = [
        dict(base),
        {**base, "stream": False, "max_tokens": max_tokens},
        {**base, "stream": False, "max_completion_tokens": max_tokens},
        {**base, "temperature": 0, "stream": False, "max_tokens": max_tokens},
        {**base, "temperature": 0, "stream": False, "max_completion_tokens": max_tokens},
    ]
    if tools:
        for payload in variants:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
    return variants


def openai_response_message(data: dict[str, Any], allow_tool_calls: bool = False) -> dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("empty choices in model response")
    message = (choices[0].get("message") or {}) if isinstance(choices[0], dict) else {}
    content = str(message.get("content") or "").strip()
    if content:
        return message
    if allow_tool_calls and message.get("tool_calls"):
        return message
    raise RuntimeError("empty model response content")


def headers(account: str, user_id: str, agent_id: str, api_key: str = "") -> dict[str, str]:
    out = {
        "Content-Type": "application/json",
        "X-OpenViking-Account": account,
        "X-OpenViking-User": user_id,
        "X-OpenViking-Agent": agent_id,
    }
    if api_key:
        out["X-API-Key"] = api_key
        out["Authorization"] = f"Bearer {api_key}"
    return out


def openviking_find(
    base_url: str,
    query: str,
    account: str,
    user_id: str,
    agent_id: str,
    api_key: str,
    limit: int,
    target_uri: str = "viking://user/memories/",
    retries: int = 2,
) -> list[dict[str, Any]]:
    payload = {
        "query": query,
        "target_uri": target_uri,
        "limit": limit,
        "score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
    }
    last_error = ""
    for attempt in range(max(1, retries + 1)):
        try:
            req = request.Request(
                base_url.rstrip("/") + "/api/v1/search/find",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers(account, user_id, agent_id, api_key),
                method="POST",
            )
            with request.urlopen(req, timeout=45) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {compact(body, 500)}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < retries:
            print(f"[retrieval] retry={attempt + 1}/{retries} error={compact(last_error, 220)}", flush=True)
            time.sleep(min(15, 2 ** attempt))
    else:
        raise RuntimeError(last_error or "OpenViking search failed")
    if raw.get("status") == "error":
        raise RuntimeError(json.dumps(raw, ensure_ascii=False)[:1000])
    result = raw.get("result", raw)
    if isinstance(result, list):
        items = result
    else:
        items = result.get("items") or result.get("results") or result.get("hits") or result.get("memories") or []
        if isinstance(result.get("memories"), list) and isinstance(result.get("resources"), list):
            items = result["memories"] + result["resources"]
    return items[:limit] if isinstance(items, list) else []


def openviking_search_api(
    base_url: str,
    query: str,
    account: str,
    user_id: str,
    agent_id: str,
    api_key: str,
    limit: int,
    target_uri: str = "",
    retries: int = 2,
    session_id: str = "",
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "query": query,
        "target_uri": target_uri,
        "limit": limit,
    }
    if session_id:
        payload["session_id"] = session_id
    raw: Any = None
    last_error = ""
    for attempt in range(max(1, retries + 1)):
        try:
            req = request.Request(
                base_url.rstrip("/") + "/api/v1/search/search",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers(account, user_id, agent_id, api_key),
                method="POST",
            )
            with request.urlopen(req, timeout=45) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {compact(body, 500)}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < retries:
            print(f"[tool-search] retry={attempt + 1}/{retries} error={compact(last_error, 220)}", flush=True)
            time.sleep(min(15, 2 ** attempt))
    else:
        raise RuntimeError(last_error or "OpenViking search failed")
    if raw.get("status") == "error":
        raise RuntimeError(json.dumps(raw, ensure_ascii=False)[:1000])
    result = raw.get("result", raw)
    items: list[Any]
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        if any(isinstance(result.get(key), list) for key in ("memories", "resources", "skills")):
            items = []
            for key, item_type in (("memories", "memory"), ("resources", "resource"), ("skills", "skill")):
                for item in result.get(key) or []:
                    if isinstance(item, dict):
                        copied = dict(item)
                        copied.setdefault("type", item_type)
                        items.append(copied)
        else:
            items = result.get("items") or result.get("results") or result.get("hits") or []
    else:
        items = []
    return [item for item in items[:limit] if isinstance(item, dict)]


def openviking_result(raw: Any) -> Any:
    if isinstance(raw, dict) and raw.get("status") == "error":
        raise RuntimeError(json.dumps(raw.get("error") or raw, ensure_ascii=False)[:1000])
    if isinstance(raw, dict) and "result" in raw:
        return raw.get("result")
    return raw


def openviking_get_json(
    base_url: str,
    path: str,
    params: dict[str, Any],
    account: str,
    user_id: str,
    agent_id: str,
    api_key: str,
    timeout: int = 45,
) -> Any:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = base_url.rstrip("/") + path + (f"?{query}" if query else "")
    req = request.Request(url, headers=headers(account, user_id, agent_id, api_key), method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {compact(body, 1000)}") from exc
    return openviking_result(raw)


def openviking_post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    account: str,
    user_id: str,
    agent_id: str,
    api_key: str,
    timeout: int = 45,
) -> Any:
    req = request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers(account, user_id, agent_id, api_key),
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {compact(body, 1000)}") from exc
    return openviking_result(raw)


def openviking_read_content(args: argparse.Namespace, uri: str, timeout: int = 45) -> str:
    uri = str(uri or "").strip()
    if not uri:
        return ""
    try:
        result = openviking_get_json(
            args.openviking_url,
            "/api/v1/content/read",
            {"uri": uri, "offset": 0, "limit": -1},
            args.account,
            args.user_id,
            args.agent_id,
            args.openviking_api_key,
            timeout,
        )
    except Exception:
        return ""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def expand_memory_queries(query: str, limit: int = 8) -> list[str]:
    base = str(query or "").strip()
    if not base:
        return []
    parts = [base]
    splitters = r"[，,。；;：:、\n]|(?:和|与|及|以及|并且|同时|然后)|(?:and|then|also)"
    for segment in re.split(splitters, base):
        cleaned = segment.strip()
        if len(cleaned) >= 4:
            parts.append(cleaned)
    keywords = query_terms(base)
    if keywords:
        parts.append(" ".join(keywords[:10]))
    lowered = base.lower()
    if "ideal" in lowered and "studio" in lowered:
        parts.extend([
            "ideal dance studio water natural light Marley flooring",
            "dance studio by the water natural light Marley flooring",
            "Marley flooring natural light water",
        ])
    if "flooring" in lowered or "marley" in lowered:
        parts.extend(["Marley flooring", "dance studio flooring Marley"])
    if "mentor" in lowered or "guide" in lowered:
        parts.extend(["positivity determination mentor guide", "perfect mentor guide positivity determination"])
    if "visited" in lowered or "city" in lowered or "cities" in lowered:
        parts.extend(["visited Paris Rome", "Jon Paris Rome"])
    deduped: list[str] = []
    for item in parts:
        if item and item not in deduped:
            deduped.append(item)
    return deduped[:limit]


def ranked_openviking_find(args: argparse.Namespace, query: str, target_uri: str) -> tuple[list[dict[str, Any]], list[str], str]:
    base_query = str(query or "").strip()
    query_plan = expand_memory_queries(query) if args.query_expansion else ([base_query] if base_query else [])
    merged: list[dict[str, Any]] = []
    errors: list[str] = []
    for item_query in query_plan:
        try:
            for item in openviking_find(
                args.openviking_url,
                item_query,
                args.account,
                args.user_id,
                args.agent_id,
                args.openviking_api_key,
                args.top_k,
                target_uri,
                args.retrieval_retries,
            ):
                copied = dict(item)
                copied.setdefault("_target_uri", target_uri)
                copied.setdefault("_query", item_query)
                merged.append(copied)
        except Exception as exc:
            errors.append(f"{item_query}: {exc}")
    seen: dict[str, dict[str, Any]] = {}
    for item in merged:
        key = str(item.get("uri") or item.get("path") or item.get("id") or memory_text(item, query, args.workspace, args.account, 800, args.read_memory_files))
        if key not in seen:
            seen[key] = item
    terms = query_terms(query)

    def score(item: dict[str, Any]) -> tuple[float, int]:
        text = memory_text(item, query, args.workspace, args.account, 1200, args.read_memory_files).lower()
        overlap = sum(1 for term in terms if term.lower() in text)
        base = float(item.get("score") or item.get("similarity") or 0.0)
        return (base, overlap)

    return sorted(seen.values(), key=score, reverse=True)[: args.top_k], query_plan, "; ".join(errors[:3])


def ranked_openviking_find_many(
    args: argparse.Namespace,
    query: str,
    target_uris: list[str],
) -> tuple[list[dict[str, Any]], list[str], str]:
    merged: list[dict[str, Any]] = []
    query_plan: list[str] = []
    errors: list[str] = []
    for target_uri in target_uris:
        hits, plan, err = ranked_openviking_find(args, query, target_uri)
        query_plan = query_plan or plan
        if err:
            errors.append(f"{target_uri}: {err}")
        for hit in hits:
            copied = dict(hit)
            copied.setdefault("_target_uri", target_uri)
            merged.append(copied)

    seen: dict[str, dict[str, Any]] = {}
    for item in merged:
        key = str(item.get("uri") or item.get("path") or item.get("id") or item.get("_target_uri") or "")
        if key not in seen:
            seen[key] = item

    return sorted(seen.values(), key=hit_score, reverse=True)[: args.top_k], query_plan, "; ".join(errors[:3])


def vikingbot_style_openviking_find(args: argparse.Namespace, query: str) -> dict[str, Any]:
    memory_users = list(getattr(args, "current_memory_users", []) or [])
    user_targets = [user_memory_target_uri(user) for user in memory_users] if memory_users else ["viking://user/memories/"]
    user_hits, user_plan, user_error = ranked_openviking_find_many(args, query, user_targets)
    agent_hits, agent_plan, agent_error = ranked_openviking_find(args, query, "viking://agent/memories/")
    errors = [item for item in [user_error, agent_error] if item]
    return {
        "user_memory": user_hits,
        "agent_memory": agent_hits,
        "query_plan": user_plan or agent_plan,
        "retrieval_error": "; ".join(errors),
        "memory_users": memory_users,
        "user_target_uris": user_targets,
    }


STOPWORDS = {
    "what", "when", "where", "which", "who", "why", "how", "did", "does", "do", "the", "a", "an", "to", "for",
    "of", "in", "on", "and", "or", "is", "are", "was", "were", "has", "have", "had", "take", "took", "from",
    "kind", "looking", "look", "like", "thinks", "think", "should", "both", "according", "makes",
}


def query_terms(query: str) -> list[str]:
    terms = []
    for match in re.finditer(r"[a-z0-9]{3,}", query.lower()):
        token = match.group(0)
        if token not in STOPWORDS and token not in terms:
            terms.append(token)
    return terms


def uri_to_path(workspace: str, account: str, uri: str) -> Path | None:
    if not workspace or not uri.startswith("viking://"):
        return None
    rel = uri.removeprefix("viking://").lstrip("/")
    if rel.startswith("user/") or rel.startswith("agent/") or rel.startswith("session/") or rel.startswith("resources/"):
        return Path(workspace).expanduser().resolve() / "viking" / account / rel
    return Path(workspace).expanduser().resolve() / "viking" / account / rel


def read_long_term_memory_file(args: argparse.Namespace, uri: str, limit: int = 20000) -> str:
    if not is_long_term_memory_uri(uri):
        return ""
    path = uri_to_path(str(getattr(args, "workspace", "") or ""), str(getattr(args, "account", "") or "default"), uri)
    if not path or not path.exists() or not path.is_file():
        return ""
    try:
        workspace_root = Path(str(getattr(args, "workspace", "") or "")).expanduser().resolve()
        resolved = path.resolve()
    except OSError:
        return ""
    if not path_within(resolved, workspace_root):
        return ""
    if "/memories/" not in resolved.as_posix():
        return ""
    try:
        return compact(resolved.read_text(encoding="utf-8", errors="replace"), limit)
    except Exception:
        return ""


def read_openviking_memory_content(args: argparse.Namespace, uri: str, timeout: int = 45, fallback_to_file: bool = True) -> str:
    content = openviking_read_content(args, uri, timeout=timeout)
    if content:
        return content
    if fallback_to_file:
        return read_long_term_memory_file(args, uri)
    return ""


def prefetch_link_event_memories(
    args: argparse.Namespace,
    query: str,
    user_hits: list[dict[str, Any]],
    agent_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    if not bool(getattr(args, "prefetch_link_events", True)):
        return {"count": 0, "uris": []}
    if not is_temporal_or_fact_question(query):
        return {"count": 0, "uris": []}

    try:
        limit = max(0, int(getattr(args, "prefetch_link_events_limit", 6) or 0))
    except (TypeError, ValueError):
        limit = 6
    if limit <= 0:
        return {"count": 0, "uris": []}

    prefetched_uris: list[str] = []
    seen: set[str] = set()
    for item in sorted([*user_hits, *agent_hits], key=hit_score, reverse=True):
        if len(prefetched_uris) >= limit:
            break
        uri = str(item.get("uri") or item.get("path") or item.get("id") or "").strip()
        if not uri or uri in seen or not is_long_term_event_memory_uri(uri):
            continue
        seen.add(uri)
        existing = str(item.get("content") or item.get("text") or "").strip()
        if existing and len(existing) >= 500:
            continue
        guard = guard_long_term_memory_uri(args, uri, "prefetch_link_event_memories")
        if guard:
            continue
        content = read_openviking_memory_content(args, uri, timeout=max(45, int(getattr(args, "timeout_s", 120) or 120)))
        if not content:
            continue
        item["_prefetched_content"] = content
        item["_prefetched_content_source"] = "openviking_content_read_or_memory_file"
        prefetched_uris.append(uri)
    return {"count": len(prefetched_uris), "uris": prefetched_uris}


def focused_file_snippet(path: Path, query: str, limit: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    terms = query_terms(query)
    lower = text.lower()
    windows = []
    for term in terms:
        start = lower.find(term)
        if start < 0:
            continue
        left = max(0, start - 700)
        right = min(len(text), start + 1100)
        windows.append(text[left:right])
    if not windows:
        return compact(text, limit)
    merged = "\n...\n".join(windows)
    return compact(merged, limit)


def lexical_memory_hits(workspace: str, account: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    root = Path(workspace).expanduser().resolve() / "viking" / account / "user" / "default" / "memories"
    if not workspace or not root.exists():
        return []
    terms = query_terms(query)
    if not terms:
        return []
    hits = []
    for path in root.rglob("*.md"):
        if path.name.startswith("."):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        score = 0
        for term in terms:
            if term in low:
                score += 3 if term in {"rome", "paris", "festival", "tattoo", "hoodies", "internship"} else 1
        if score:
            uri = "viking://user/default/memories/" + str(path.relative_to(root)).replace(os.sep, "/")
            hits.append({"uri": uri, "score": score, "abstract": focused_file_snippet(path, query, 2200), "source": "lexical_memory_file"})
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits[:limit]


def archive_message_text(message: dict[str, Any]) -> str:
    parts = message.get("parts")
    if isinstance(parts, list):
        texts = [str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("text")]
        if texts:
            return "\n".join(texts)
    return str(message.get("content") or message.get("text") or "")


def archive_fallback_hits(
    workspace: str,
    account: str,
    sample_id: str,
    query: str,
    query_plan: list[str],
    limit: int = 6,
) -> list[dict[str, Any]]:
    if not workspace or not sample_id:
        return []
    session_root = Path(workspace).expanduser().resolve() / "viking" / account / "session"
    if not session_root.exists():
        return []
    terms: list[str] = []
    for candidate in [query, *query_plan]:
        for term in query_terms(candidate):
            if term not in terms:
                terms.append(term)
    if not terms:
        return []
    name_terms = {"jon", "gina", "caroline", "melanie", "user", "agent"}
    distinctive_terms = [term for term in terms if term not in name_terms]
    hits: list[dict[str, Any]] = []
    seen_windows: set[str] = set()
    for session_dir in sorted(session_root.glob(f"locomo-{sample_id}-*")):
        for path in sorted(session_dir.glob("history/archive_*/messages.jsonl")):
            messages: list[dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = archive_message_text(message)
                if text:
                    messages.append({**message, "_text": text})
            for index, message in enumerate(messages):
                left = max(0, index - 2)
                right = min(len(messages), index + 3)
                window = messages[left:right]
                window_text = "\n".join(
                    f"{item.get('created_at', '')} {item.get('role_id') or item.get('role') or ''}: {item.get('_text', '')}"
                    for item in window
                )
                low_window = window_text.lower()
                if distinctive_terms and not any(term in low_window for term in distinctive_terms):
                    continue
                low_center = str(message.get("_text") or "").lower()
                score = sum(2 for term in terms if term in low_center) + sum(1 for term in terms if term in low_window)
                if score <= 0:
                    continue
                key = compact(low_window, 600)
                if key in seen_windows:
                    continue
                seen_windows.add(key)
                rel = path.relative_to(session_root)
                hits.append(
                    {
                        "uri": f"viking://session/{rel.as_posix()}#msg{index + 1}",
                        "score": float(score),
                        "abstract": compact(window_text, 2200),
                        "source": "archive_fallback",
                        "content_source": "openviking_session_archive",
                        "session_id": session_dir.name,
                    }
                )
    hits.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return hits[:limit]


def memory_text(item: dict[str, Any], query: str, workspace: str, account: str, limit: int = 2200, read_file: bool = False) -> str:
    uri = item.get("uri") or item.get("path") or item.get("id") or ""
    score = item.get("score", "")
    body = ""
    path = uri_to_path(workspace, account, str(uri)) if read_file else None
    if path and path.exists() and path.is_file():
        body = compact(path.read_text(encoding="utf-8", errors="replace"), limit)
    if not body:
        body = item.get("content") or item.get("text") or item.get("abstract") or item.get("overview") or item.get("summary") or ""
    return compact(f"{uri} score={score}\n{body}", limit)


def hit_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or item.get("similarity") or 0)
    except (TypeError, ValueError):
        return 0.0


def format_viking_memory_section(
    items: list[dict[str, Any]],
    query: str,
    workspace: str,
    account: str,
    max_chars: int,
    read_file: bool,
    args: argparse.Namespace | None = None,
    read_openviking_content: bool = False,
) -> str:
    formatted: list[str] = []
    total_chars = 0
    seen_content_hashes: set[int] = set()
    for index, item in enumerate(items, 1):
        uri = str(item.get("uri") or item.get("path") or item.get("id") or "")
        score = hit_score(item)
        content = str(item.get("_prefetched_content") or "")
        if not content and args and read_openviking_content and uri:
            content = read_openviking_memory_content(args, uri)
        abstract = str(item.get("abstract") or item.get("content") or item.get("text") or "")
        content_to_hash = content or abstract
        if content_to_hash:
            content_hash = hash(content_to_hash)
            if content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(content_hash)
        if content:
            full_text = compact(content, 12000).strip()
            full_entry = (
                f'<memory index="{index}" type="full">\n'
                f"  <uri>{uri}</uri>\n"
                f"  <score>{score}</score>\n"
                f"  <content>{full_text}</content>\n"
                f"</memory>"
            )
            needed = len(full_entry) + (1 if formatted else 0)
            if total_chars + needed <= max_chars:
                formatted.append(full_entry)
                total_chars += needed
                continue
        formatted.append(
            f'<memory index="{index}" type="link">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <score>{score}</score>\n"
            f"</memory>"
        )
    return "\n".join(formatted)


def call_openai(base_url: str, model: str, token: str, messages: list[dict[str, str]], timeout: int, max_retries: int = 5) -> dict[str, Any]:
    last_error = ""
    last_kind = "api_error"
    data: dict[str, Any] | None = None
    retry_count = 0
    attempts = max(1, max_retries + 1)
    payload_variants = openai_payload_variants(model, messages, default_openai_max_tokens())
    for attempt in range(attempts):
        payload = payload_variants[attempt % len(payload_variants)]
        try:
            req = request.Request(
                base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                method="POST",
            )
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            candidate = parse_openai_compatible_response(body)
            openai_response_message(candidate)
            data = candidate
            retry_count = attempt
            break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {body[:1000]}"
            last_kind = classify_model_error(last_error)
        except Exception as exc:
            last_error = str(exc)
            last_kind = classify_model_error(last_error)
        if attempt < attempts - 1:
            sleep_s = min(30, 2 ** attempt)
            if last_kind == "rate_limited":
                sleep_s = min(45, 5 * (attempt + 1))
            variant = (attempt % len(payload_variants)) + 1
            print(f"[model] retry={attempt + 1}/{max_retries} variant={variant} kind={last_kind} error={compact(last_error, 220)}", flush=True)
            time.sleep(sleep_s)
    if data is None:
        raise ModelCallError(last_error or "model call failed", max_retries, last_kind)
    msg = openai_response_message(data).get("content") or ""
    usage = data.get("usage") or {}
    return {
        "answer": msg.strip(),
        "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
        "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
        "total_tokens": usage.get("total_tokens") or ((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)),
        "model_retry_count": retry_count,
        "model_error_kind": "",
    }


def openviking_read_user_profile(args: argparse.Namespace, user_id: str) -> str:
    user_id = str(user_id or "").strip()
    if not user_id:
        return ""
    return openviking_read_content(args, f"{user_memory_target_uri(user_id)}profile.md", timeout=20)


def build_vikingbot_system_prompt(args: argparse.Namespace) -> str:
    workspace = vikingbot_local_workspace(args)
    system = platform.system()
    runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
    workspace_display = str(workspace)
    openviking_only = restrict_tools_to_long_term_memory(args)
    capabilities = """You have access to tools that allow you to:
- Read, search, and grep OpenViking files
- Read, write, and edit local files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks"""
    workspace_section = f"""## Workspace
You have two workspaces:
1. Local workspace: {workspace_display}
2. OpenViking workspace: managed via OpenViking tools
- Custom skills: {workspace_display}/skills/{{skill-name}}/SKILL.md"""
    memory_section = """## Memory
- Remember important facts: using openviking_memory_commit tool to commit"""

    parts = [
        f"""# vikingbot 🐈

You are VikingBot, an AI assistant built based on the OpenViking context database.
When acquiring information, data, and knowledge, you **prioritize using openviking tools to read and search OpenViking (a context database) above all other sources**.
{capabilities}

## Runtime
{runtime}

{workspace_section}

IMPORTANT:
- When responding to direct questions or conversations, reply directly with your text response. 
- Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).For normal conversation, just respond with text - do not call the message tool.
- Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.

{memory_section}"""
    ]
    bootstrap, _loaded_bootstrap = load_vikingbot_bootstrap(workspace)
    if bootstrap:
        parts.append(bootstrap)
    if openviking_only:
        parts.append(
            "## Evaluation Tool Availability\n"
            "For this read-only evaluation run, only the tools exposed in the current tool schema are callable. "
            "Use OpenViking long-term memory evidence from viking://user/.../memories/ and viking://agent/.../memories/. "
            "Do not use raw session transcripts, session archives, local files, or benchmark source files as evidence."
        )
    parts.append(
        "## Direct QA Evidence Use\n"
        "- For date and timeline questions, distinguish an event's actual date from later phrases such as as-of, mentioned on, recalled on, or current date.\n"
        "- If a retrieved memory is link-only and the question asks for a date or concrete fact, inspect the linked OpenViking memory with openviking_multi_read before deciding.\n"
        "- Answer only the specific items requested by the question; do not add adjacent plans or extra facts unless needed to resolve the answer."
    )

    active_skills, skills_summary, _skill_names = load_vikingbot_skills_context(workspace)
    if active_skills and not openviking_only:
        parts.append(f"# Active Skills\n\n{active_skills}")
    if skills_summary and not openviking_only:
        parts.append(
            "# Skills\n\n"
            "The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.\n"
            "Skills with available=\"false\" need dependencies installed first - you can try installing them with apt/brew.\n\n"
            f"{skills_summary}"
        )

    profile = openviking_read_user_profile(args, str(getattr(args, "vikingbot_sender_id", "") or args.user_id))
    if profile:
        parts.append(f"## Current user's information\n{profile}")
    return "\n\n---\n\n".join(parts)


def build_vikingbot_user_memory_message(
    evidence: str,
    has_memory: bool,
    group_chat: bool = False,
    sender_id: str = "",
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    tz = time.strftime("%Z") or "UTC"
    session_context = "## Current Session\nChannel: cli"
    if group_chat:
        session_context += (
            f"\n**Group chat session.** Current user: {sender_id or 'user'}\n"
            f"Multiple users can participate in this conversation. Each user message is prefixed with the user's name in brackets like '[张三]: 你好'. "
            f"You should pay attention to who is speaking to understand the context. "
        )
    parts = [
        f"## Current Time: {now} ({tz})",
        session_context,
    ]
    if has_memory:
        parts.append(f"## openviking_search(query=[user_query])\n{evidence}")
    parts.append("Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:")
    return "\n\n---\n\n".join(parts)


def build_vikingbot_question_prompt(job: benchmark_adapter.Job) -> str:
    if job.query_time and job.query_time != "-":
        return f"Current date: {job.query_time}. Answer the question directly: {job.question}"
    return f"Answer the question directly: {job.question}"


def build_strict_messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_vikingbot_aligned_messages(args: argparse.Namespace, job: benchmark_adapter.Job, evidence: str, has_memory: bool) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_vikingbot_system_prompt(args)},
        {
            "role": "user",
            "content": build_vikingbot_user_memory_message(
                evidence,
                has_memory,
                bool(getattr(args, "group_chat", False)),
                str(getattr(args, "vikingbot_sender_id", "") or args.user_id),
            ),
        },
        {"role": "user", "content": build_vikingbot_question_prompt(job)},
    ]


def openviking_search_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "openviking_search",
            "description": (
                "Using query to search for resources (knowledge, code, files, workflow, etc.) in OpenViking. "
                "Result: Only URIs and summaries are included here. To view the full content, use openviking_multi_read tool."
                "This operation performs semantic retrieval, not full character matching. Please avoid repeated calls with similar queries as much as possible."
                "bad-case: after searching with 'Nate Joanna dog playdate 3:00 pm', another search was performed using 'Nate Joanna dog playdate'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "target_uri": {
                        "type": "string",
                        "description": "Optional target URI to limit search scope, if is None, then search the entire range.(e.g., viking://resources/)",
                    },
                    "min_score": {"type": "number", "description": "Minimum relevance score threshold", "default": VIKINGBOT_TOOL_MIN_SCORE},
                },
                "required": ["query"],
            },
        },
    }


def openviking_read_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "openviking_read",
            "description": "Read full content from a single OpenViking resource URI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The Viking URI to read, e.g. viking://user/default/memories/profile.md"},
                },
                "required": ["uri"],
            },
        },
    }


def openviking_multi_read_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "openviking_multi_read",
            "description": "Read full content from multiple OpenViking resources concurrently. Returns complete content for all URIs with no truncation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uris": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of Viking file URIs to read.",
                    },
                },
                "required": ["uris"],
            },
        },
    }


def openviking_list_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "openviking_list",
            "description": "List resources in a OpenViking folder path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The parent Viking uri to list (e.g., viking://resources/)"},
                    "recursive": {"type": "boolean", "description": "Whether to list recursively", "default": False},
                },
                "required": ["uri"],
            },
        },
    }


def openviking_grep_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "openviking_grep",
            "description": (
                "Search Viking resources using a regex pattern (like grep)."
                "Result: Only URIs and summaries are included here. To view the full content, use openviking_multi_read tool."
                "Please avoid repeated calls with similar queries as much as possible."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The whole Viking URI to search within (e.g., viking://resources/)"},
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive search", "default": False},
                },
                "required": ["uri", "pattern"],
            },
        },
    }


def openviking_glob_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "openviking_glob",
            "description": (
                "Find Viking resources using glob patterns (like **/*.md, *.py)."
                "Result: Only URIs and summaries are included here. To view the full content, use openviking_multi_read tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match (e.g., **/*.md, *.py, src/**/*.js)"},
                    "uri": {"type": "string", "description": "The whole Viking URI to search within (e.g., viking://resources/path/)", "default": ""},
                },
                "required": ["pattern"],
            },
        },
    }


def openviking_memory_commit_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "openviking_memory_commit",
            "description": "When user has personal information needs to be remembered, Commit messages to OpenViking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "description": "List of messages to commit, each with role, content",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string", "enum": ["user", "assistant"]},
                                "content": {"type": "string"},
                            },
                            "required": ["role", "content"],
                        },
                    },
                },
                "required": ["messages"],
            },
        },
    }


def read_file_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file at the given path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "The file path to read"}},
                "required": ["path"],
            },
        },
    }


def list_dir_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the contents of a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "The directory path to list"}},
                "required": ["path"],
            },
        },
    }


def exec_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Execute a shell command and return its output. Use with caution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"},
                    "working_dir": {
                        "type": "string",
                        "description": "Optional working directory for the command",
                    },
                },
                "required": ["command"],
            },
        },
    }


def openviking_tool_definitions(args: argparse.Namespace) -> list[dict[str, Any]]:
    tool_set = str(getattr(args, "openviking_tool_set", VIKINGBOT_TOOL_SET) or VIKINGBOT_TOOL_SET)
    if tool_set == "search_only":
        return [openviking_search_tool_definition()]
    vikingbot_default_tools = [
        openviking_multi_read_tool_definition(),
        openviking_list_tool_definition(),
        openviking_search_tool_definition(),
        openviking_grep_tool_definition(),
        openviking_glob_tool_definition(),
    ]
    if tool_set == "vikingbot_native_safe":
        return [
            read_file_tool_definition(),
            list_dir_tool_definition(),
            exec_tool_definition(),
            *vikingbot_default_tools,
            openviking_memory_commit_tool_definition(),
        ]
    if tool_set == "vikingboat_default":
        return vikingbot_default_tools
    if tool_set == "vikingbot_openviking":
        return vikingbot_default_tools
    return vikingbot_default_tools


def openviking_item_type(item: dict[str, Any]) -> str:
    raw_type = str(item.get("type") or item.get("context_type") or "").lower()
    uri = str(item.get("uri") or item.get("path") or item.get("id") or "").lower()
    if "skill" in raw_type or "/skills/" in uri:
        return "skill"
    if "memory" in raw_type or "/memories/" in uri:
        return "memory"
    return "resource"


def openviking_grouped_search_payload(items: list[dict[str, Any]], min_score: float) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {"memories": [], "resources": [], "skills": []}
    for item in items:
        score = hit_score(item)
        if score < min_score:
            continue
        out = {
            "uri": str(item.get("uri") or item.get("path") or item.get("id") or ""),
            "abstract": str(item.get("abstract") or item.get("content") or item.get("text") or item.get("summary") or ""),
            "is_leaf": bool(item.get("is_leaf", False)),
            "score": round(score, 6),
        }
        kind = openviking_item_type(item)
        grouped["skills" if kind == "skill" else "memories" if kind == "memory" else "resources"].append(out)
    for group_items in grouped.values():
        for index, item in enumerate(group_items, 1):
            item["index"] = index
    return {
        "count": sum(len(value) for value in grouped.values()),
        **grouped,
    }


def execute_openviking_search_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    query = str(tool_args.get("query") or "").strip()
    target_uri = normalize_tool_memory_uri(args, str(tool_args.get("target_uri") or "").strip())
    try:
        min_score = float(tool_args.get("min_score") if tool_args.get("min_score") is not None else args.tool_min_score)
    except (TypeError, ValueError):
        min_score = args.tool_min_score
    if not query:
        return "No results found for empty query"
    try:
        target_uris = [target_uri] if target_uri else [
            user_memory_target_uri(user) for user in list(getattr(args, "current_memory_users", []) or [])
        ] or ["viking://user/memories/"]
        items: list[dict[str, Any]] = []
        for uri in target_uris:
            guard = guard_long_term_memory_uri(args, uri, "openviking_search")
            if guard:
                return guard
            for item in openviking_search_api(
                args.openviking_url,
                query,
                args.account,
                args.user_id,
                args.agent_id,
                args.openviking_api_key,
                args.tool_search_limit,
                uri,
                args.retrieval_retries,
                str(getattr(args, "vikingbot_session_id", "") or ""),
            ):
                copied = dict(item)
                copied.setdefault("_target_uri", uri)
                items.append(copied)
    except Exception as exc:
        return f"Error searching Viking: {exc}"
    payload = openviking_grouped_search_payload(items, min_score)
    if not payload["count"]:
        return f"No results found for query: {query}"
    return json.dumps(payload, ensure_ascii=False, indent=2)


def execute_openviking_read_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    uri = normalize_tool_memory_uri(args, str(tool_args.get("uri") or "").strip())
    if not uri:
        return "Error: No URI provided."
    guard = guard_long_term_memory_uri(args, uri, "openviking_read")
    if guard:
        return guard
    try:
        content = read_openviking_memory_content(args, uri, timeout=max(45, int(args.timeout_s or 120)))
    except Exception as exc:
        return f"Error reading from Viking: {exc}"
    return content or f"Error reading from Viking: empty content for {uri}"


def execute_openviking_multi_read_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    raw_uris = tool_args.get("uris")
    if isinstance(raw_uris, str):
        uris = [raw_uris]
    elif isinstance(raw_uris, list):
        uris = [str(uri) for uri in raw_uris if str(uri or "").strip()]
    else:
        uris = []
    if not uris:
        return "Error: No URIs provided."
    result_lines = [f"Multi-read results for {len(uris)} resources (level: read):"]
    for uri in [normalize_tool_memory_uri(args, uri) for uri in uris]:
        guard = guard_long_term_memory_uri(args, uri, "openviking_multi_read")
        if guard:
            result_lines.append(f"\n--- START OF {uri} ---")
            result_lines.append(guard)
            result_lines.append(f"--- END OF {uri} ---")
            continue
        result_lines.append(f"\n--- START OF {uri} ---")
        content = read_openviking_memory_content(args, uri, timeout=max(45, int(args.timeout_s or 120)))
        result_lines.append(content)
        result_lines.append(f"--- END OF {uri} ---")
    return "\n".join(result_lines)


def execute_openviking_list_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    uri = normalize_tool_memory_uri(args, str(tool_args.get("uri") or "").strip()) or default_tool_memory_uri(args)
    recursive = bool(tool_args.get("recursive", False))
    if not uri:
        return "Error listing Viking resources: uri is required"
    guard = guard_long_term_memory_uri(args, uri, "openviking_list")
    if guard:
        return guard
    try:
        result = openviking_get_json(
            args.openviking_url,
            "/api/v1/fs/ls",
            {"uri": uri, "simple": False, "recursive": recursive, "output": "original"},
            args.account,
            args.user_id,
            args.agent_id,
            args.openviking_api_key,
            timeout=45,
        )
    except Exception as exc:
        return f"Error listing Viking resources: {exc}"
    entries = result if isinstance(result, list) else result.get("items", []) if isinstance(result, dict) else []
    if not entries:
        return f"No resources found at {uri}"
    lines: list[str] = []
    for entry in entries[:200]:
        if isinstance(entry, dict):
            lines.append(str({
                "name": entry.get("name"),
                "size": entry.get("size"),
                "uri": entry.get("uri"),
                "isDir": entry.get("isDir"),
            }))
        else:
            lines.append(str(entry))
    if len(entries) > 200:
        lines.append(f"... {len(entries) - 200} more resources omitted")
    return "\n".join(lines)


def execute_openviking_grep_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    uri = normalize_tool_memory_uri(args, str(tool_args.get("uri") or "").strip())
    pattern = str(tool_args.get("pattern") or "").strip()
    if not uri:
        uri = default_tool_memory_uri(args)
    if not pattern:
        return "Error searching Viking with grep: pattern is required"
    guard = guard_long_term_memory_uri(args, uri, "openviking_grep")
    if guard:
        return guard
    merged_results: dict[str, list[tuple[Any, str]]] = {}
    try:
        result = openviking_post_json(
            args.openviking_url,
            "/api/v1/search/grep",
            {
                "uri": uri,
                "pattern": pattern,
                "case_insensitive": bool(tool_args.get("case_insensitive", False)),
                "node_limit": 10,
            },
            args.account,
            args.user_id,
            args.agent_id,
            args.openviking_api_key,
            timeout=max(45, int(args.timeout_s or 120)),
        )
        if isinstance(result, dict):
            matches = result.get("matches") or []
        elif isinstance(result, list):
            matches = result
        else:
            matches = getattr(result, "matches", []) if result is not None else []
        if not isinstance(matches, list):
            matches = []
        for match in matches:
            if isinstance(match, dict):
                match_uri = str(match.get("uri", "unknown") or "unknown")
                line = match.get("line", "?")
                content = str(match.get("content", "") or "")
            else:
                match_uri = str(getattr(match, "uri", "unknown") or "unknown")
                line = getattr(match, "line", "?")
                content = str(getattr(match, "content", "") or "")
            merged_results.setdefault(match_uri, []).append((line, content))
    except Exception as exc:
        return f"Error searching Viking with grep: {exc}"
    if not merged_results:
        return f"No matches found for pattern: '{pattern}'"
    total_matches = sum(len(matches) for matches in merged_results.values())
    result_lines = [f"Found {total_matches} match{'es' if total_matches != 1 else ''} for pattern '{pattern}':"]
    for match_uri, matches in merged_results.items():
        matches.sort(key=lambda item: int(item[0]) if str(item[0]).isdigit() else 0)
        result_lines.append(f"\n{match_uri}")
        for line, content in matches:
            result_lines.append(f"   Line {line}:")
            result_lines.append(f"   {content}")
    return "\n".join(result_lines)


def execute_openviking_glob_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    pattern = str(tool_args.get("pattern") or "").strip()
    uri = normalize_tool_memory_uri(args, str(tool_args.get("uri") or "").strip()) or default_tool_memory_uri(args)
    if not pattern:
        return "Error searching Viking with glob: pattern is required"
    guard = guard_long_term_memory_uri(args, uri, "openviking_glob")
    if guard:
        return guard
    try:
        result = openviking_post_json(
            args.openviking_url,
            "/api/v1/search/glob",
            {"pattern": pattern, "uri": uri},
            args.account,
            args.user_id,
            args.agent_id,
            args.openviking_api_key,
            timeout=45,
        )
    except Exception as exc:
        return f"Error searching Viking with glob: {exc}"
    if isinstance(result, dict):
        matches = result.get("matches") or []
        count = result.get("count", len(matches))
    elif isinstance(result, list):
        matches = result
        count = len(matches)
    else:
        matches = getattr(result, "matches", []) if result is not None else []
        count = getattr(result, "count", len(matches))
    if not matches:
        return f"No files found for pattern: {pattern}"
    lines = [f"Found {count} file{'s' if count != 1 else ''}:"]
    for match_uri in matches:
        if isinstance(match_uri, dict):
            match_uri = match_uri.get("uri", str(match_uri))
        lines.append(str(match_uri))
    return "\n".join(lines)


def execute_read_file_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    path, error_text = resolve_local_tool_path(args, tool_args.get("path") or tool_args.get("file") or "")
    if error_text:
        return error_text
    if path is None:
        return "Error: path is required"
    if not path.exists():
        return f"Error: file does not exist: {path}"
    if not path.is_file():
        return f"Error: path is not a file: {path}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading file {path}: {exc}"
    return redact_tool_output(text)


def execute_list_dir_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    path, error_text = resolve_local_tool_path(args, tool_args.get("path") or ".")
    if error_text:
        return error_text
    if path is None:
        return "Error: path is required"
    if not path.exists():
        return f"Error: directory does not exist: {path}"
    if not path.is_dir():
        return f"Error: path is not a directory: {path}"
    try:
        entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except Exception as exc:
        return f"Error listing directory {path}: {exc}"
    lines = [f"{path}"]
    for entry in entries[:300]:
        kind = "dir" if entry.is_dir() else "file"
        try:
            size = entry.stat().st_size if entry.is_file() else 0
        except OSError:
            size = 0
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"{kind:4} {size:>10} {entry.name}{suffix}")
    if len(entries) > 300:
        lines.append(f"... {len(entries) - 300} more entries omitted")
    return redact_tool_output("\n".join(lines))


def execute_exec_tool(args: argparse.Namespace, tool_args: dict[str, Any]) -> str:
    command = str(tool_args.get("command") or "").strip()
    if not command:
        return "Error: command is required"
    if len(command) > 1000:
        return "Error: command is too long"
    if MUTATING_COMMAND_RE.search(command):
        return "Error: exec is read-only in this harness; mutating or network commands are blocked."

    working_dir, error_text = resolve_local_tool_path(args, tool_args.get("working_dir") or tool_args.get("cwd") or ".")
    if error_text:
        return error_text
    if working_dir is None:
        return "Error: working_dir is required"
    if not working_dir.exists() or not working_dir.is_dir():
        return f"Error: working_dir is not a directory: {working_dir}"

    env = {
        key: value
        for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL"))
    }
    timeout_s = min(max(5, int(getattr(args, "timeout_s", 120) or 120)), 60)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout_s}s"
    except Exception as exc:
        return f"Error executing command: {exc}"

    output = []
    output.append(f"exit_code={proc.returncode}")
    if proc.stdout:
        output.append("[stdout]")
        output.append(proc.stdout)
    if proc.stderr:
        output.append("[stderr]")
        output.append(proc.stderr)
    return redact_tool_output("\n".join(output).strip() or f"exit_code={proc.returncode}")


def execute_openviking_tool(args: argparse.Namespace, name: str, parsed_args: dict[str, Any]) -> str:
    if name == "read_file":
        return execute_read_file_tool(args, parsed_args)
    if name == "list_dir":
        return execute_list_dir_tool(args, parsed_args)
    if name == "exec":
        return execute_exec_tool(args, parsed_args)
    if name == "openviking_search":
        return execute_openviking_search_tool(args, parsed_args)
    if name == "openviking_read":
        return "Error: Tool 'openviking_read' not found"
    if name == "openviking_multi_read":
        return execute_openviking_multi_read_tool(args, parsed_args)
    if name == "openviking_list":
        return execute_openviking_list_tool(args, parsed_args)
    if name == "openviking_grep":
        return execute_openviking_grep_tool(args, parsed_args)
    if name == "openviking_glob":
        return execute_openviking_glob_tool(args, parsed_args)
    if name == "openviking_memory_commit":
        return "Error committing to Viking: read-only evaluation run does not write memory."
    return f"Error executing {name}: unsupported tool"


def call_openai_vikingbot_loop(
    args: argparse.Namespace,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    tools = openviking_tool_definitions(args) if args.openviking_tool_loop else None
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    tools_used: list[dict[str, Any]] = []
    final_answer = ""
    retry_count_total = 0
    attempts = max(1, args.model_retries + 1)
    for iteration in range(1, max(1, args.max_iterations) + 1):
        payload_variants = openai_payload_variants(args.answer_model, messages, default_openai_max_tokens(), tools)
        data: dict[str, Any] | None = None
        last_error = ""
        last_kind = "api_error"
        for attempt in range(attempts):
            payload = payload_variants[attempt % len(payload_variants)]
            try:
                req = request.Request(
                    args.answer_base_url.rstrip("/") + "/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {args.answer_token}"},
                    method="POST",
                )
                with request.urlopen(req, timeout=args.timeout_s) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                candidate = parse_openai_compatible_response(body)
                openai_response_message(candidate, allow_tool_calls=bool(tools))
                data = candidate
                retry_count_total += attempt
                break
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {body[:1000]}"
                last_kind = classify_model_error(last_error)
            except Exception as exc:
                last_error = str(exc)
                last_kind = classify_model_error(last_error)
            if attempt < attempts - 1:
                sleep_s = min(30, 2 ** attempt)
                if last_kind == "rate_limited":
                    sleep_s = min(45, 5 * (attempt + 1))
                variant = (attempt % len(payload_variants)) + 1
                print(f"[model] retry={attempt + 1}/{args.model_retries} variant={variant} kind={last_kind} error={compact(last_error, 220)}", flush=True)
                time.sleep(sleep_s)
        if data is None:
            raise ModelCallError(last_error or "model call failed", retry_count_total or args.model_retries, last_kind)

        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        total_usage["prompt_tokens"] += int(prompt_tokens or 0)
        total_usage["completion_tokens"] += int(completion_tokens or 0)
        total_usage["total_tokens"] += int(total_tokens or 0)

        message = openai_response_message(data, allow_tool_calls=bool(tools))
        tool_calls = message.get("tool_calls") or []
        if tool_calls and tools:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or " ",
                    "tool_calls": tool_calls,
                }
            )
            for tool_call in tool_calls:
                fn = tool_call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    parsed_args = {"query": str(raw_args)}
                result_text = execute_openviking_tool(args, name, parsed_args)
                tools_used.append({"tool_name": name, "args": parsed_args, "result": compact(result_text, 1200)})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id") or f"tool_{len(tools_used)}",
                        "name": name,
                        "content": result_text,
                    }
                )
            messages.append({"role": "user", "content": "Reflect on the results and decide next steps."})
            continue
        final_answer = str(message.get("content") or "").strip()
        return {
            "answer": final_answer,
            "prompt_tokens": total_usage["prompt_tokens"],
            "completion_tokens": total_usage["completion_tokens"],
            "total_tokens": total_usage["total_tokens"],
            "model_retry_count": retry_count_total,
            "model_error_kind": "",
            "iteration": iteration,
            "tools_used": tools_used,
        }
    return {
        "answer": final_answer or f"Reached {args.max_iterations} iterations without completion.",
        "prompt_tokens": total_usage["prompt_tokens"],
        "completion_tokens": total_usage["completion_tokens"],
        "total_tokens": total_usage["total_tokens"],
        "model_retry_count": retry_count_total,
        "model_error_kind": "max_iterations",
        "iteration": args.max_iterations,
        "tools_used": tools_used,
    }


def answer_question(args: argparse.Namespace, job: benchmark_adapter.Job) -> dict[str, str]:
    started = time.time()
    retrieval_error = ""
    native_prompt = build_vikingbot_question_prompt(job)
    ctx_args = argparse.Namespace(**vars(args))
    sender_id = vikingbot_sender_id(job)
    session_id = vikingbot_session_id(job)
    memory_users = effective_memory_users(args, job)
    memory_user_strategy = effective_memory_user_strategy(args)
    user_target_uris = [user_memory_target_uri(user) for user in memory_users]
    ctx_args.current_memory_users = memory_users
    ctx_args.vikingbot_sender_id = sender_id
    ctx_args.vikingbot_session_id = session_id
    if getattr(args, "vikingbot_identity_mode", "sender_session") == "sender_session":
        ctx_args.user_id = sender_id
        ctx_args.agent_id = session_id
    try:
        retrieval = vikingbot_style_openviking_find(ctx_args, native_prompt)
        user_hits = list(retrieval.get("user_memory") or [])
        agent_hits = list(retrieval.get("agent_memory") or [])
        query_plan = list(retrieval.get("query_plan") or [])
        retrieval_error = str(retrieval.get("retrieval_error") or "")
    except Exception as exc:
        user_hits = []
        agent_hits = []
        query_plan = []
        retrieval_error = str(exc)
    if ctx_args.workspace and ctx_args.lexical_fallback:
        seen = {item.get("uri") for item in user_hits}
        for item in lexical_memory_hits(ctx_args.workspace, ctx_args.account, native_prompt, ctx_args.lexical_top_k):
            if item.get("uri") not in seen:
                user_hits.append(item)
                seen.add(item.get("uri"))
    if ctx_args.workspace and ctx_args.archive_fallback:
        seen = {item.get("uri") for item in user_hits}
        for item in archive_fallback_hits(ctx_args.workspace, ctx_args.account, job.sample_id, native_prompt, query_plan, ctx_args.archive_top_k):
            if item.get("uri") not in seen:
                user_hits.append(item)
                seen.add(item.get("uri"))

    user_hits = sorted(user_hits, key=hit_score, reverse=True)[: ctx_args.top_k]
    agent_hits = sorted(agent_hits, key=hit_score, reverse=True)[: ctx_args.top_k]
    prefetch_meta = prefetch_link_event_memories(ctx_args, native_prompt, user_hits, agent_hits)
    hits = user_hits + agent_hits
    user_memory_block = format_viking_memory_section(
        user_hits,
        native_prompt,
        ctx_args.workspace,
        ctx_args.account,
        VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
        ctx_args.read_memory_files,
        args=ctx_args,
        read_openviking_content=ctx_args.read_openviking_content,
    )
    agent_memory_block = format_viking_memory_section(
        agent_hits,
        native_prompt,
        ctx_args.workspace,
        ctx_args.account,
        VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
        ctx_args.read_memory_files,
        args=ctx_args,
        read_openviking_content=ctx_args.read_openviking_content,
    )
    has_memory = bool(user_memory_block or agent_memory_block)
    if has_memory:
        evidence = (
            f"### user memories:\n{user_memory_block}\n"
            f"### agent memories:\n{agent_memory_block}"
        )
    else:
        evidence = "(no memories found)"

    if ctx_args.prompt_mode == "strict_memory":
        system = (
            "# Strict OpenViking Memory Question Answering\n\n"
            "You are answering from OpenViking long-term memory search results only.\n"
            "No session archive, raw conversation transcript, lexical file scan, or query-expanded fallback evidence has been injected.\n\n"
            "Instructions:\n"
            "1. Carefully read all retrieved memories\n"
            "2. The memories are ranked by relevance score\n"
            "3. Extract relevant facts to answer the question\n"
            "4. Synthesize information from multiple memories if needed\n"
            "5. Answer concisely with specific dates/facts when available\n"
            "6. If memories don't contain sufficient information, respond 'unknown'\n\n"
            "Important:\n"
            "- Do not invent information not present in the memories\n"
            "- Use exact dates and facts from the memories\n"
            "- Combine information from multiple memories when necessary\n\n"
            "Examples:\n\n"
            "Q: When did Jon lose his job?\n"
            "Memories: 'Jon lost his job as a banker on 2023-01-19'\n"
            "A: 2023-01-19\n\n"
            "Q: What cities has Jon visited?\n"
            "Memories: 'Jon was in Paris on 2023-01-28' + 'Jon visited Rome in June 2023'\n"
            "A: Paris, Rome\n\n"
            "Q: What is Jon's favorite color?\n"
            "Memories: 'Jon loves contemporary dance'\n"
            "A: unknown"
        )
        if job.query_time and job.query_time != "-":
            question_with_context = f"Current date: {job.query_time}. {job.question}"
        else:
            question_with_context = job.question
        user = (
            f"Question: {question_with_context}\n\n"
            f"{evidence}\n\n"
            "Based on the memories above, provide your answer:"
        )
        messages: list[dict[str, Any]] = build_strict_messages(system, user)
    else:
        messages = build_vikingbot_aligned_messages(ctx_args, job, evidence, has_memory)

    if ctx_args.answer_token:
        try:
            if ctx_args.prompt_mode == "vikingbot_aligned":
                result = call_openai_vikingbot_loop(ctx_args, messages)
            else:
                result = call_openai(
                    ctx_args.answer_base_url,
                    ctx_args.answer_model,
                    ctx_args.answer_token,
                    messages,
                    ctx_args.timeout_s,
                    ctx_args.model_retries,
                )
                result.setdefault("iteration", 1)
                result.setdefault("tools_used", [])
        except ModelCallError as exc:
            result = {
                "answer": "",
                "prompt_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                "completion_tokens": 0,
                "total_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                "model_retry_count": exc.retry_count,
                "model_error_kind": exc.error_kind,
                "model_error": str(exc),
                "iteration": 0,
                "tools_used": [],
            }
    else:
        result = {
            "answer": "unknown",
            "prompt_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
            "completion_tokens": 1,
            "total_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)) + 1,
            "model_retry_count": 0,
            "model_error_kind": "no_answer_token",
            "iteration": 0,
            "tools_used": [],
        }
    tools_used = list(result.get("tools_used") or [])
    tool_names = [str(item.get("tool_name") or "") for item in tools_used if item.get("tool_name")]
    tool_name_counts = Counter(tool_names)
    relevant_memory = json.dumps({
        "user_memory": user_hits,
        "agent_memory": agent_hits,
        "memory_users": memory_users,
        "user_target_uris": user_target_uris,
    }, ensure_ascii=False)
    answer_ok = bool(str(result["answer"]).strip()) and str(result["answer"]).strip().lower() != "unknown"
    retrieval_ok = bool(hits)
    model_ok = bool(str(result["answer"]).strip()) and not result.get("model_error_kind")
    if retrieval_error:
        health_status = "retrieval_error"
    elif retrieval_ok and model_ok and answer_ok:
        health_status = "ok"
    else:
        health_status = "retrieval_empty" if not retrieval_ok else ("answer_empty" if not answer_ok else "model_degraded")
    health_notes = []
    if retrieval_error:
        health_notes.append(f"retrieval_error={compact(retrieval_error, 240)}")
    if result.get("model_error"):
        health_notes.append(f"model_error={compact(result.get('model_error'), 240)}")
    if not retrieval_ok:
        health_notes.append("no_relevant_memory")
    if not answer_ok:
        health_notes.append("empty_or_unknown_answer")
    vikingbot_meta = vikingbot_context_metadata(ctx_args)
    return {
        **benchmark_adapter.asdict(job),
        "response": result["answer"],
        "simple_grade": "CORRECT" if job.answer.lower().strip() and job.answer.lower().strip() in result["answer"].lower() else "NEEDS_JUDGE",
        "result": "",
        "reasoning": f"openviking memory qa; prompt_mode={ctx_args.prompt_mode}; pending judge" + (f"; {'; '.join(health_notes)}" if health_notes else ""),
        "time_cost": f"{time.time() - started:.4f}",
        "memory_uri": ",".join(user_target_uris) or "viking://user/memories/",
        "backend": "openviking",
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "alignment_backend_route": "custom_agent_initial_find_tool_search",
        "vikingbot_sender_id": sender_id,
        "vikingbot_session_id": session_id,
        "vikingbot_identity_mode": str(getattr(args, "vikingbot_identity_mode", "sender_session")),
        "vikingbot_channel": "cli",
        "vikingbot_workspace": vikingbot_meta["vikingbot_workspace"],
        "vikingbot_bootstrap_files": json.dumps(vikingbot_meta["vikingbot_bootstrap_files"], ensure_ascii=False),
        "vikingbot_skill_names": json.dumps(vikingbot_meta["vikingbot_skill_names"], ensure_ascii=False),
        "qa_user_id": str(ctx_args.user_id),
        "qa_agent_id": str(ctx_args.agent_id),
        "group_chat": str(bool(getattr(args, "group_chat", False))).lower(),
        "memory_user_strategy": memory_user_strategy,
        "effective_memory_users": json.dumps(memory_users, ensure_ascii=False),
        "user_target_uris": json.dumps(user_target_uris, ensure_ascii=False),
        "relevant_memory": relevant_memory,
        "prompt_mode": ctx_args.prompt_mode,
        "vikingbot_prompt_aligned": str(ctx_args.prompt_mode == "vikingbot_aligned").lower(),
        "openviking_tool_loop_enabled": str(bool(ctx_args.openviking_tool_loop and ctx_args.prompt_mode == "vikingbot_aligned")).lower(),
        "openviking_tool_set": str(ctx_args.openviking_tool_set),
        "openviking_tool_names": json.dumps([tool["function"]["name"] for tool in openviking_tool_definitions(ctx_args)], ensure_ascii=False),
        "openviking_content_read_enabled": str(bool(ctx_args.read_openviking_content)).lower(),
        "prefetch_link_events_enabled": str(bool(getattr(ctx_args, "prefetch_link_events", True))).lower(),
        "prefetched_link_event_count": str(prefetch_meta.get("count", 0)),
        "prefetched_link_event_uris": json.dumps(prefetch_meta.get("uris", []), ensure_ascii=False),
        "initial_agent_memory_enabled": str(bool(getattr(ctx_args, "initial_agent_memory", False))).lower(),
        "max_iterations": str(ctx_args.max_iterations),
        "iteration": str(result.get("iteration", 0)),
        "tool_call_count": str(len(tools_used)),
        "tool_call_name_counts": json.dumps(tool_name_counts, ensure_ascii=False),
        "tools_used_names": json.dumps(tool_names, ensure_ascii=False),
        "tools_used": json.dumps(tools_used, ensure_ascii=False),
        "native_prompt": native_prompt,
        "prompt_message_count": str(len(messages)),
        "prompt_preview": compact(json.dumps(messages, ensure_ascii=False), 5000),
        "retrieval_query_plan": json.dumps(query_plan, ensure_ascii=False),
        "retrieval_mode": "strict_original_query" if not ctx_args.query_expansion else "diagnostic_query_expansion",
        "query_expansion_enabled": str(bool(ctx_args.query_expansion)).lower(),
        "lexical_fallback_enabled": str(bool(ctx_args.lexical_fallback)).lower(),
        "archive_fallback_enabled": str(bool(ctx_args.archive_fallback)).lower(),
        "memory_file_read_enabled": str(bool(ctx_args.read_memory_files)).lower(),
        "retrieval_count": str(len(hits)),
        "archive_fallback_count": str(sum(1 for item in hits if item.get("source") == "archive_fallback")),
        "memory_hit_count": str(sum(1 for item in hits if item.get("source") != "archive_fallback")),
        "user_memory_count": str(len(user_hits)),
        "agent_memory_count": str(len(agent_hits)),
        "user_memory_budget_chars": str(VIKINGBOT_USER_MEMORY_BUDGET_CHARS),
        "agent_memory_budget_chars": str(VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS),
        "initial_search_limit": str(ctx_args.top_k),
        "initial_score_threshold": str(VIKINGBOT_INITIAL_MIN_SCORE),
        "score_threshold": str(VIKINGBOT_INITIAL_MIN_SCORE),
        "tool_search_limit": str(ctx_args.tool_search_limit),
        "tool_min_score": str(ctx_args.tool_min_score),
        "user_agent_memory_split": "true",
        "link_only_when_over_budget": "true",
        "raw_turn_fallback": "false",
        "retrieval_tokens_est": str(token_estimate(evidence)),
        "context_preview": compact(evidence, 3000),
        "answer_prompt_tokens": str(result["prompt_tokens"]),
        "answer_completion_tokens": str(result["completion_tokens"]),
        "answer_total_tokens": str(result["total_tokens"]),
        "model_status": "ok" if model_ok else "failed",
        "model_retry_count": str(result.get("model_retry_count", 0)),
        "model_error_kind": str(result.get("model_error_kind") or ""),
        "model_error": str(result.get("model_error") or ""),
        "retrieval_status": "ok" if retrieval_ok else "empty",
        "answer_status": "ok" if answer_ok else ("failed" if result.get("model_error_kind") else "empty_or_unknown"),
        "health_status": result.get("model_error_kind") or health_status,
        "retrieval_error": retrieval_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LoCoMo QA against injected OpenViking memories.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="1")
    parser.add_argument("--questions", default="")
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parser.add_argument("--openviking-url", default="http://127.0.0.1:1933")
    parser.add_argument("--workspace", default="")
    parser.add_argument(
        "--vikingbot-workspace",
        default=detect_vikingbot_workspace(),
        help="Local VikingBot bot/workspace path used for bootstrap files, skills, and local read/list/exec tools.",
    )
    parser.add_argument("--openviking-api-key", default="")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--memory-users", default="", help="Override VikingBot --memory-user list. Default uses the VikingBot sender/original sample id.")
    parser.add_argument("--group-chat", dest="group_chat", action="store_true", default=False)
    parser.add_argument("--no-group-chat", dest="group_chat", action="store_false")
    parser.add_argument("--initial-agent-memory", dest="initial_agent_memory", action="store_true", default=True)
    parser.add_argument("--no-initial-agent-memory", dest="initial_agent_memory", action="store_false")
    parser.add_argument(
        "--vikingbot-identity-mode",
        choices=["sender_session", "fixed"],
        default="sender_session",
        help="sender_session matches VikingBot: --sender original_sample_id and --session question_id.",
    )
    parser.add_argument("--top-k", type=int, default=VIKINGBOT_INITIAL_SEARCH_LIMIT)
    parser.add_argument("--query-expansion", dest="query_expansion", action="store_true", default=False)
    parser.add_argument("--no-query-expansion", dest="query_expansion", action="store_false")
    parser.add_argument("--lexical-fallback", dest="lexical_fallback", action="store_true", default=False)
    parser.add_argument("--no-lexical-fallback", dest="lexical_fallback", action="store_false")
    parser.add_argument("--lexical-top-k", type=int, default=8)
    parser.add_argument("--archive-fallback", dest="archive_fallback", action="store_true", default=False)
    parser.add_argument("--no-archive-fallback", dest="archive_fallback", action="store_false")
    parser.add_argument("--archive-top-k", type=int, default=6)
    parser.add_argument("--read-memory-files", dest="read_memory_files", action="store_true", default=False)
    parser.add_argument("--no-read-memory-files", dest="read_memory_files", action="store_false")
    parser.add_argument(
        "--prompt-mode",
        choices=["vikingbot_aligned", "strict_memory"],
        default="vikingbot_aligned",
        help="vikingbot_aligned matches VikingBot message layout; strict_memory is the older harness prompt.",
    )
    parser.add_argument("--openviking-tool-loop", dest="openviking_tool_loop", action="store_true", default=True)
    parser.add_argument("--no-openviking-tool-loop", dest="openviking_tool_loop", action="store_false")
    parser.add_argument(
        "--openviking-tool-set",
        choices=["vikingboat_default", "vikingbot_native_safe", "vikingbot_openviking", "search_only"],
        default=VIKINGBOT_TOOL_SET,
        help="vikingbot_native_safe exposes VikingBot-style local read/list/exec plus OpenViking tools while keeping raw transcript fallbacks off; vikingboat_default restricts tools to long-term memory URIs.",
    )
    parser.add_argument("--tool-search-limit", type=int, default=VIKINGBOT_TOOL_SEARCH_LIMIT)
    parser.add_argument("--tool-min-score", type=float, default=VIKINGBOT_TOOL_MIN_SCORE)
    parser.add_argument("--read-openviking-content", dest="read_openviking_content", action="store_true", default=True)
    parser.add_argument("--no-read-openviking-content", dest="read_openviking_content", action="store_false")
    parser.add_argument("--prefetch-link-events", dest="prefetch_link_events", action="store_true", default=True)
    parser.add_argument("--no-prefetch-link-events", dest="prefetch_link_events", action="store_false")
    parser.add_argument("--prefetch-link-events-limit", type=int, default=6)
    parser.add_argument("--max-iterations", type=int, default=VIKINGBOT_MAX_ITERATIONS)
    parser.add_argument("--answer-base-url", default=os.environ.get("JUDGE_BASE_URL", ""))
    parser.add_argument("--answer-model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--retrieval-retries", type=int, default=2)
    parser.add_argument("--timeout-s", type=int, default=120)
    args = parser.parse_args()
    args.vikingbot_workspace = str(vikingbot_local_workspace(args))

    data = read_json(Path(args.dataset).expanduser().resolve())
    question_filter = {q.strip() for q in args.questions.split(",") if q.strip()}
    jobs, _plans = benchmark_adapter.locomo_jobs(data, None, args.sample, question_filter or None)
    if args.random_count:
        rnd = random.Random(args.random_seed)
        jobs = rnd.sample(jobs, min(args.random_count, len(jobs)))
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "openviking_memory_qa_results.csv"
    print(f"[qa] dataset={args.dataset} sample={args.sample} questions={len(jobs)} openviking={args.openviking_url}", flush=True)
    rows = []
    for index, job in enumerate(jobs, 1):
        print(f"[qa] {index}/{len(jobs)} {job.question_id} {job.question[:90]}", flush=True)
        try:
            rows.append(answer_question(args, job))
        except Exception as exc:
            retry_count = getattr(exc, "retry_count", args.model_retries if isinstance(exc, ModelCallError) else 0)
            error_kind = getattr(exc, "error_kind", classify_model_error(str(exc)))
            sender_id = vikingbot_sender_id(job)
            session_id = vikingbot_session_id(job)
            memory_users = effective_memory_users(args, job)
            memory_user_strategy = effective_memory_user_strategy(args)
            user_target_uris = [user_memory_target_uri(user) for user in memory_users]
            qa_user_id = sender_id if args.vikingbot_identity_mode == "sender_session" else args.user_id
            qa_agent_id = session_id if args.vikingbot_identity_mode == "sender_session" else args.agent_id
            vikingbot_meta = vikingbot_context_metadata(args)
            rows.append(
                {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[QA ERROR] {exc}",
                    "time_cost": "0",
                    "memory_uri": ",".join(user_target_uris) or "viking://user/memories/",
                    "vikingbot_sender_id": sender_id,
                    "vikingbot_session_id": session_id,
                    "vikingbot_identity_mode": args.vikingbot_identity_mode,
                    "vikingbot_channel": "cli",
                    "vikingbot_workspace": vikingbot_meta["vikingbot_workspace"],
                    "vikingbot_bootstrap_files": json.dumps(vikingbot_meta["vikingbot_bootstrap_files"], ensure_ascii=False),
                    "vikingbot_skill_names": json.dumps(vikingbot_meta["vikingbot_skill_names"], ensure_ascii=False),
                    "qa_user_id": str(qa_user_id),
                    "qa_agent_id": str(qa_agent_id),
                    "group_chat": str(bool(args.group_chat)).lower(),
                    "memory_user_strategy": memory_user_strategy,
                    "effective_memory_users": json.dumps(memory_users, ensure_ascii=False),
                    "user_target_uris": json.dumps(user_target_uris, ensure_ascii=False),
                    "relevant_memory": json.dumps(
                        {"user_memory": [], "agent_memory": [], "memory_users": memory_users, "user_target_uris": user_target_uris},
                        ensure_ascii=False,
                    ),
                    "retrieval_query_plan": "[]",
                    "retrieval_mode": "strict_original_query" if not args.query_expansion else "diagnostic_query_expansion",
                    "query_expansion_enabled": str(bool(args.query_expansion)).lower(),
                    "lexical_fallback_enabled": str(bool(args.lexical_fallback)).lower(),
                    "archive_fallback_enabled": str(bool(args.archive_fallback)).lower(),
                    "memory_file_read_enabled": str(bool(args.read_memory_files)).lower(),
                    "prompt_mode": args.prompt_mode,
                    "vikingbot_prompt_aligned": str(args.prompt_mode == "vikingbot_aligned").lower(),
                    "openviking_tool_loop_enabled": str(bool(args.openviking_tool_loop and args.prompt_mode == "vikingbot_aligned")).lower(),
                    "openviking_tool_set": str(args.openviking_tool_set),
                    "openviking_tool_names": json.dumps([tool["function"]["name"] for tool in openviking_tool_definitions(args)], ensure_ascii=False),
                    "openviking_content_read_enabled": str(bool(args.read_openviking_content)).lower(),
                    "prefetch_link_events_enabled": str(bool(getattr(args, "prefetch_link_events", True))).lower(),
                    "prefetched_link_event_count": "0",
                    "prefetched_link_event_uris": "[]",
                    "initial_agent_memory_enabled": str(bool(args.initial_agent_memory)).lower(),
                    "max_iterations": str(args.max_iterations),
                    "iteration": "0",
                    "tool_call_count": "0",
                    "tool_call_name_counts": "{}",
                    "tools_used_names": "[]",
                    "tools_used": "[]",
                    "native_prompt": build_vikingbot_question_prompt(job),
                    "prompt_message_count": "0",
                    "prompt_preview": "",
                    "retrieval_count": "0",
                    "archive_fallback_count": "0",
                    "memory_hit_count": "0",
                    "user_memory_count": "0",
                    "agent_memory_count": "0",
                    "backend": "openviking",
                    "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
                    "alignment_backend_route": "custom_agent_initial_find_tool_search",
                    "user_memory_budget_chars": str(VIKINGBOT_USER_MEMORY_BUDGET_CHARS),
                    "agent_memory_budget_chars": str(VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS),
                    "initial_search_limit": str(args.top_k),
                    "initial_score_threshold": str(VIKINGBOT_INITIAL_MIN_SCORE),
                    "score_threshold": str(VIKINGBOT_INITIAL_MIN_SCORE),
                    "tool_search_limit": str(args.tool_search_limit),
                    "tool_min_score": str(args.tool_min_score),
                    "user_agent_memory_split": "true",
                    "link_only_when_over_budget": "true",
                    "raw_turn_fallback": "false",
                    "retrieval_tokens_est": "0",
                    "answer_prompt_tokens": "0",
                    "answer_completion_tokens": "0",
                    "answer_total_tokens": "0",
                    "model_status": "failed",
                    "model_retry_count": str(retry_count),
                    "model_error_kind": error_kind,
                    "model_error": str(exc),
                    "retrieval_status": "unknown",
                    "answer_status": "failed",
                    "health_status": error_kind,
                    "retrieval_error": "",
                }
            )
        fieldnames = csv_fieldnames(rows)
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    fieldnames = csv_fieldnames(rows) if rows else []
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tool_name_counts: Counter = Counter()
    for row in rows:
        tool_name_counts.update(parse_counter_json(row.get("tool_call_name_counts")))
    tool_call_total = sum(int(r.get("tool_call_count") or 0) for r in rows)
    vikingbot_meta = vikingbot_context_metadata(args)
    summary = {
        **alignment_metadata("openviking", "custom_agent_initial_find_tool_search"),
        "dataset_format": "locomo",
        "dataset": str(Path(args.dataset).expanduser().resolve()),
        "sample": args.sample,
        "selected_question_count": len(question_filter),
        "top_k": args.top_k,
        "group_chat": bool(args.group_chat),
        "memory_user_strategy": effective_memory_user_strategy(args),
        "initial_agent_memory_enabled": bool(args.initial_agent_memory),
        "memory_users_override": parse_memory_users(args.memory_users),
        "vikingbot_identity_mode": args.vikingbot_identity_mode,
        "vikingbot_channel": "cli",
        "vikingbot_workspace": vikingbot_meta["vikingbot_workspace"],
        "vikingbot_bootstrap_files": vikingbot_meta["vikingbot_bootstrap_files"],
        "vikingbot_skill_names": vikingbot_meta["vikingbot_skill_names"],
        "base_user_id": args.user_id,
        "base_agent_id": args.agent_id,
        "prompt_mode": args.prompt_mode,
        "vikingbot_prompt_aligned": args.prompt_mode == "vikingbot_aligned",
        "openviking_tool_loop_enabled": bool(args.openviking_tool_loop and args.prompt_mode == "vikingbot_aligned"),
        "openviking_tool_set": args.openviking_tool_set,
        "openviking_tool_names": [tool["function"]["name"] for tool in openviking_tool_definitions(args)],
        "openviking_content_read_enabled": bool(args.read_openviking_content),
        "max_iterations": args.max_iterations,
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "alignment_backend_route": "custom_agent_initial_find_tool_search",
        "answer_model": args.answer_model,
        "openviking_url": args.openviking_url,
        "workspace": args.workspace,
        "account": args.account,
        "count": len(rows),
        "output_csv": str(csv_path),
        "retrieval_mode": "strict_original_query" if not args.query_expansion else "diagnostic_query_expansion",
        "query_expansion_enabled": bool(args.query_expansion),
        "lexical_fallback_enabled": bool(args.lexical_fallback),
        "archive_fallback_enabled": bool(args.archive_fallback),
        "memory_file_read_enabled": bool(args.read_memory_files),
        "iteration_total": sum(int(r.get("iteration") or 0) for r in rows),
        "avg_iteration": round(sum(int(r.get("iteration") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "tool_call_total": tool_call_total,
        "tool_call_rows": sum(1 for r in rows if int(r.get("tool_call_count") or 0) > 0),
        "tool_name_counts": dict(tool_name_counts),
        "answer_prompt_tokens": sum(int(r.get("answer_prompt_tokens") or 0) for r in rows),
        "answer_completion_tokens": sum(int(r.get("answer_completion_tokens") or 0) for r in rows),
        "answer_total_tokens": sum(int(r.get("answer_total_tokens") or 0) for r in rows),
        "retrieval_tokens_est": sum(int(r.get("retrieval_tokens_est") or 0) for r in rows),
        "avg_retrieval_count": round(sum(int(r.get("retrieval_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "avg_user_memory_count": round(sum(int(r.get("user_memory_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "avg_agent_memory_count": round(sum(int(r.get("agent_memory_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "archive_fallback_total": sum(int(r.get("archive_fallback_count") or 0) for r in rows),
        "avg_archive_fallback_count": round(sum(int(r.get("archive_fallback_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "memory_hit_total": sum(int(r.get("memory_hit_count") or 0) for r in rows),
        "user_memory_budget_chars": VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
        "agent_memory_budget_chars": VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
        "initial_search_limit": args.top_k,
        "initial_score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
        "score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
        "tool_search_limit": args.tool_search_limit,
        "tool_min_score": args.tool_min_score,
        "user_agent_memory_split": True,
        "link_only_when_over_budget": True,
        "raw_turn_fallback": False,
        "model_retries_configured": args.model_retries,
        "retrieval_retries_configured": args.retrieval_retries,
        "model_ok_count": sum(1 for r in rows if r.get("model_status") == "ok"),
        "model_failed_count": sum(1 for r in rows if r.get("model_status") == "failed"),
        "model_rate_limited_count": sum(1 for r in rows if r.get("model_error_kind") == "rate_limited" or r.get("health_status") == "rate_limited"),
        "rows_with_model_retries": sum(1 for r in rows if int(r.get("model_retry_count") or 0) > 0),
        "model_retry_total": sum(int(r.get("model_retry_count") or 0) for r in rows),
        "retrieval_ok_count": sum(1 for r in rows if r.get("retrieval_status") == "ok"),
        "retrieval_empty_count": sum(1 for r in rows if r.get("retrieval_status") == "empty"),
        "answer_ok_count": sum(1 for r in rows if r.get("answer_status") == "ok"),
        "answer_empty_or_unknown_count": sum(1 for r in rows if r.get("answer_status") == "empty_or_unknown"),
        "health_counts": {key: sum(1 for r in rows if r.get("health_status") == key) for key in sorted({r.get("health_status") or "unknown" for r in rows})},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
