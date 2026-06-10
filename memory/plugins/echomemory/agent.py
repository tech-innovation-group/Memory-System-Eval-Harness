from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ... import llm
from ...vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from echomemory_common import DEFAULT_ECHOMEM_ROOT, context_item_to_dict, ctx, ensure_echomem_imports, write_echomem_config  # noqa: E402


DASHSCOPE_COMPAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ECHOMEMORY_VIKINGBOAT_TOOL_SET = "vikingboat_default"


def looks_like_echomem_root(path: Path) -> bool:
    return (
        ((path / "packages" / "echomem" / "src").exists() and (path / "packages" / "echofs" / "src").exists())
        or ((path / "echomem").exists() and (path / "pyproject.toml").exists())
    )


def discover_echomem_root(raw_root: str = "") -> Path:
    candidates = [
        raw_root,
        os.environ.get("ECHOMEM_ROOT"),
        os.environ.get("ECHOMEMORY_ROOT"),
        str(Path.home() / "Code" / "echomemory" / "echo_memory_v006"),
        str(Path.home() / "Code" / "echomemory" / "echo_memory"),
        str(DEFAULT_ECHOMEM_ROOT),
    ]
    seen: set[str] = set()
    fallback = Path(str(raw_root or DEFAULT_ECHOMEM_ROOT)).expanduser()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(str(candidate)).expanduser().resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if looks_like_echomem_root(path):
            return path
    return fallback.resolve()


def ensure_echomem_dependency_paths(root: Path) -> None:
    venv_lib = root / ".venv" / "lib"
    if not venv_lib.exists():
        return
    for site_packages in sorted(venv_lib.glob("python*/site-packages")):
        if site_packages.exists():
            path = str(site_packages)
            if path not in sys.path:
                sys.path.append(path)


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def config_vlm_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in (ROOT / "judge.conf", Path.home() / ".openviking" / "ov.conf"):
        vlm = read_json_object(path).get("vlm") or {}
        if isinstance(vlm, dict):
            rows.append(vlm)
    return rows


def first_text(*values: Any, default: str = "") -> str:
    for value in values:
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    return default


def normalize_base_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()


def config_base_url(config: dict[str, Any]) -> str:
    return str(config.get("api_base") or config.get("base_url") or "").strip()


def compatible_base(source_base: str, target_base: str) -> bool:
    source = normalize_base_url(source_base)
    target = normalize_base_url(target_base)
    return not source or not target or source == target


def token_for_base(vlm_configs: list[dict[str, Any]], base_url: str) -> str:
    for item in vlm_configs:
        key = str(item.get("api_key") or "").strip()
        if key and compatible_base(config_base_url(item), base_url):
            return key
    return first_text(*(item.get("api_key") for item in vlm_configs))


def resolve_model_env(payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, str]:
    vlm_configs = config_vlm_candidates()
    config_base = first_text(
        *(
            item.get("api_base") or item.get("base_url")
            for item in vlm_configs
            if any(marker in normalize_base_url(item.get("api_base") or item.get("base_url")) for marker in ("dashscope", "deepseek"))
        )
    )
    config_model = first_text(*(item.get("model") for item in vlm_configs if "deepseek" in str(item.get("model") or "").lower()))
    payload_base = first_text(payload.get("vlm_base_url"), payload.get("dashscope_base_url"), payload.get("echomem_chat_base_url"))
    base_url = first_text(
        payload_base,
        os.environ.get("ECHOMEM_CHAT_BASE_URL"),
        os.environ.get("DASHSCOPE_BASE_URL"),
        config_base,
        default=DASHSCOPE_COMPAT_BASE_URL,
    )
    config_key = token_for_base(vlm_configs, base_url)
    model = first_text(
        payload.get("vlm_model"),
        payload.get("echomem_chat_model"),
        payload.get("memory_inject_model"),
        os.environ.get("ECHOMEM_CHAT_MODEL"),
        payload.get("answer_model"),
        config_model,
        default="deepseek-v4-flash",
    )
    provider = first_text(payload.get("vlm_provider"), payload.get("echomem_chat_provider"), os.environ.get("ECHOMEM_CHAT_PROVIDER")).lower()
    if provider not in {"deepseek", "dashscope", "anthropic"}:
        provider = "deepseek" if "deepseek" in model.lower() or "dashscope" in base_url.lower() else "dashscope"
    token = first_text(
        payload.get("echomem_chat_api_key"),
        payload.get("echomem_api_key"),
        payload.get("dashscope_api_key"),
        payload.get("vlm_api_key"),
        os.environ.get("DASHSCOPE_API_KEY"),
        os.environ.get("ECHOMEM_API_KEY"),
        os.environ.get("ECHOMEM_CHAT_API_KEY"),
        config_key,
        payload.get("answer_token"),
        payload.get("judge_token"),
        payload.get("api_key"),
    )
    return {
        "DASHSCOPE_API_KEY": token,
        "ECHOMEM_CHAT_API_KEY": first_text(payload.get("echomem_chat_api_key"), token),
        "ECHOMEM_API_KEY": token,
        "DASHSCOPE_BASE_URL": base_url,
        "ECHOMEM_CHAT_BASE_URL": base_url,
        "ECHOMEM_CHAT_PROVIDER": provider,
        "ECHOMEM_CHAT_MODEL": model,
    }


def apply_echomem_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        if value:
            os.environ[key] = str(value)


def compact_for_prompt(text: Any, limit: int = 1200) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def estimate_tokens(text: Any) -> int:
    value = str(text or "")
    if not value:
        return 0
    ascii_chars = sum(1 for ch in value if ord(ch) < 128)
    other_chars = len(value) - ascii_chars
    return max(1, int(ascii_chars / 4 + other_chars / 1.6))


def now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("EchoMemory agent adapter cannot run inside an active event loop")


def payload_value(payload: dict[str, Any], defaults: dict[str, Any], *names: str, default: str = "") -> str:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value).strip()
        value = defaults.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return default


def resolve_settings(payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    account = payload_value(payload, defaults, "account", default="default") or "default"
    workspace = (
        str(payload.get("workspace") or payload.get("echomemory_workspace") or "").strip()
        or str(defaults.get("echomemory_workspace") or defaults.get("echomem_workspace") or "").strip()
        or os.environ.get("ECHOMEM_WORKSPACE")
        or str(ROOT / ".tmp" / "echomem_workspace")
    )
    raw_root = payload_value(
        payload,
        defaults,
        "echomem_root",
        "echomemRoot",
        default=os.environ.get("ECHOMEM_ROOT") or str(DEFAULT_ECHOMEM_ROOT),
    )
    raw_tool_set = str(payload.get("tool_set") or defaults.get("tool_set") or VIKINGBOT_TOOL_SET)
    normalized_tool_set = ECHOMEMORY_VIKINGBOAT_TOOL_SET if raw_tool_set == VIKINGBOT_TOOL_SET else raw_tool_set
    return {
        "account": account,
        "workspace": workspace,
        "echomem_root": str(discover_echomem_root(raw_root)),
        "user_id": payload_value(payload, defaults, "user_id", "em_user_id", "ov_user_id", default="default") or "default",
        "agent_id": payload_value(payload, defaults, "agent_id", "em_agent_id", "ov_agent_id", default="default") or "default",
        "top_k": max(1, int(payload.get("top_k") or VIKINGBOT_INITIAL_SEARCH_LIMIT)),
        "retrieval_mode": str(payload.get("retrieval_mode") or "both"),
        "runtime_dir": Path(payload.get("runtime_dir") or ROOT / ".tmp" / "echomemory_agent").expanduser(),
        "model_env": resolve_model_env(payload, defaults),
        "vikingboat_tool_loop": str(payload.get("vikingboat_tool_loop", defaults.get("vikingboat_tool_loop", "true"))).strip().lower() not in {"0", "false", "no", "off"},
        "tool_set": normalized_tool_set,
        "tool_search_limit": max(1, int(payload.get("tool_search_limit") or defaults.get("tool_search_limit") or VIKINGBOT_TOOL_SEARCH_LIMIT)),
        "tool_min_score": float(payload.get("tool_min_score") or defaults.get("tool_min_score") or VIKINGBOT_TOOL_MIN_SCORE),
        "tool_log_chars": max(200, int(payload.get("tool_log_chars") or defaults.get("tool_log_chars") or 1200)),
        "max_iterations": max(1, int(payload.get("max_iterations") or defaults.get("max_iterations") or VIKINGBOT_MAX_ITERATIONS)),
        "timeout_s": max(10, int(payload.get("timeout_s") or defaults.get("timeout_s") or 120)),
        "model_retries": max(0, int(payload.get("model_retries") or defaults.get("model_retries") or 5)),
    }


def memory_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def memory_uri(item: dict[str, Any]) -> str:
    return str(item.get("uri") or item.get("path") or item.get("source_uri") or item.get("id") or "")


def memory_content(item: dict[str, Any]) -> str:
    return str(
        item.get("content")
        or item.get("text")
        or item.get("abstract")
        or item.get("overview")
        or item.get("summary")
        or ""
    )


def split_user_agent_hits(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    user_hits: list[dict[str, Any]] = []
    agent_hits: list[dict[str, Any]] = []
    for item in items:
        uri = memory_uri(item).lower()
        memory_type = str(item.get("memory_type") or item.get("type") or "").lower()
        owner = str(item.get("owner") or item.get("scope") or "").lower()
        if "/agent/" in uri or memory_type.startswith("agent") or owner == "agent":
            agent_hits.append(item)
        else:
            user_hits.append(item)
    return user_hits, agent_hits


def format_memory_section(items: list[dict[str, Any]], max_chars: int) -> str:
    formatted: list[str] = []
    total_chars = 0
    seen_hashes: set[int] = set()
    for idx, item in enumerate(items, start=1):
        uri = memory_uri(item)
        score = memory_score(item)
        content = memory_content(item).strip()
        if content:
            content_hash = hash(content)
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            full = (
                f'<memory index="{idx}" type="full">\n'
                f"  <uri>{uri}</uri>\n"
                f"  <score>{score}</score>\n"
                f"  <content>{content}</content>\n"
                f"</memory>"
            )
            needed = len(full) + (1 if formatted else 0)
            if total_chars + needed <= max_chars:
                formatted.append(full)
                total_chars += needed
                continue
        formatted.append(
            f'<memory index="{idx}" type="link">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <score>{score}</score>\n"
            f"</memory>"
        )
    return "\n".join(formatted)


def make_context_block(index: int, role: str, title: str, tag: str, source: str, content: str, kind: str = "") -> dict[str, Any]:
    return {
        "index": index,
        "id": re.sub(r"[^a-z0-9]+", "_", f"{role}_{title}".lower()).strip("_") or f"block_{index}",
        "role": role,
        "title": title,
        "tag": tag,
        "source": source,
        "kind": kind or role,
        "content": content,
        "char_count": len(content),
        "tokens_est": estimate_tokens(content),
    }


async def open_echomem(settings: dict[str, Any]):
    root = ensure_echomem_imports(discover_echomem_root(settings["echomem_root"]))
    ensure_echomem_dependency_paths(root)
    apply_echomem_env(settings.get("model_env") or {})
    try:
        from echomem.protocol.local_sdk.sdk import EchoMemSDK
        from echomem.runtime.runtime import open_runtime
    except ModuleNotFoundError:
        try:
            from echomem.entrypoints.plugins.echoagent.sdk import EchoMemSDK
            from echomem.runtime.bootstrap import open_runtime
        except ModuleNotFoundError as exc:
            expected = [
                str(root / "packages" / "echomem" / "src"),
                str(root / "packages" / "echofs" / "src"),
                str(root / "echomem"),
            ]
            raise ModuleNotFoundError(
                f"{exc}; echomem_root={root}; expected SDK paths={expected}; sdk_layout={looks_like_echomem_root(root)}"
            ) from exc

    safe_account = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(settings["account"] or "default")).strip("-") or "default"
    workspace_hash = hashlib.sha1(str(settings["workspace"]).encode("utf-8")).hexdigest()[:10]
    runtime_dir = Path(settings["runtime_dir"]).expanduser() / safe_account / workspace_hash
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = write_echomem_config(runtime_dir, settings["account"], settings["workspace"], root)
    runtime = await open_runtime(str(config_path))
    return EchoMemSDK(runtime), root, config_path


def normalize_context_item(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        data = asdict(item)
    elif isinstance(item, dict):
        data = dict(item)
    else:
        return context_item_to_dict(item)
    normalized = context_item_to_dict(data)
    normalized.setdefault("backend", "echomemory")
    return normalized


async def retrieve(settings: dict[str, Any], query: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    root = ""
    config_path = ""
    try:
        sdk, root_path, runtime_config = await open_echomem(settings)
        root = str(root_path)
        config_path = str(runtime_config)
    except Exception as exc:
        return {
            "items": [],
            "user_memory": [],
            "agent_memory": [],
            "query_plan": [query] if query else [],
            "errors": [f"EchoMemory SDK unavailable: {exc}"],
            "degraded": True,
            "echomem_root": root,
            "echomem_config": config_path,
        }

    context = ctx(settings["account"], settings["user_id"], settings["agent_id"])
    mode = settings["retrieval_mode"]
    if not query:
        return {"items": [], "user_memory": [], "agent_memory": [], "query_plan": [], "errors": [], "degraded": False}
    try:
        if mode in {"find", "both"}:
            found = await sdk.find(query, ctx=context)
            items.extend(normalize_context_item(item) for item in found)
    except Exception as exc:
        errors.append(f"find: {exc}")
    try:
        if mode in {"search", "both"}:
            result = await sdk.search(query, ctx=context, budget={"max_results": settings["top_k"]})
            items.extend(normalize_context_item(item) for item in getattr(result, "items", []))
    except Exception as exc:
        errors.append(f"search: {exc}")

    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        key = f"{memory_uri(item)}::{compact_for_prompt(memory_content(item), 160)}"
        if key not in seen:
            seen[key] = item
    ranked = sorted(seen.values(), key=memory_score, reverse=True)
    filtered = [item for item in ranked if memory_score(item) >= VIKINGBOT_INITIAL_MIN_SCORE]
    limited = filtered[: settings["top_k"]]
    user_items, agent_items = split_user_agent_hits(limited)
    return {
        "items": limited,
        "user_memory": user_items,
        "agent_memory": agent_items,
        "query_plan": [query],
        "errors": errors,
        "degraded": bool(errors),
        "min_score": VIKINGBOT_INITIAL_MIN_SCORE,
        "user_budget_chars": VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
        "agent_budget_chars": VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
        "echomem_root": root,
        "echomem_config": config_path,
    }


async def build_context_preview_async(payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    settings = resolve_settings(payload, defaults)
    messages = [item for item in payload.get("messages", []) if isinstance(item, dict)]
    use_memory = bool(payload.get("use_memory", True))
    last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    retrieval = {"items": [], "user_memory": [], "agent_memory": [], "query_plan": [], "errors": [], "degraded": False}
    if use_memory and last_user:
        retrieval = await retrieve(settings, str(last_user))
    retrieval.setdefault("error", "; ".join(retrieval.get("errors", [])[:2]))

    user_memory_block = format_memory_section(retrieval.get("user_memory", []), VIKINGBOT_USER_MEMORY_BUDGET_CHARS)
    agent_memory_block = format_memory_section(retrieval.get("agent_memory", []), VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS)
    memory_block = (
        f"### user memories:\n{user_memory_block or '(none)'}\n"
        f"### agent memories:\n{agent_memory_block or '(none)'}"
    ) if (user_memory_block or agent_memory_block) else ""

    agent_charter = (
        "<agent_charter>\n"
        "你是一个接入当前记忆后端的任务型 Agent。\n"
        "回答要准确、直接、可验证，先给结论，再给关键依据。\n"
        "你具备长期记忆检索能力。请优先基于当前记忆后端返回的证据作答。\n"
        "回答时尽量明确时间、人物、公司与因果关系。\n"
        "</agent_charter>"
    )
    behavior_policy = (
        "<behavior_policy>\n"
        "不要编造事实；证据不足时说明不确定点，但先给最可能结论。\n"
        "若存在多个候选答案，给出排序和你选择的依据。\n"
        "若检索信息冲突，优先更具体、时间更明确、来源更直接的证据。\n"
        "默认使用中文，表达简洁明确，避免空泛免责声明。\n"
        "</behavior_policy>"
    )
    if memory_block:
        retrieved_memory = (
            f"<retrieved_memory source=\"EchoMemory\" account=\"{settings['account']}\" user=\"{settings['user_id']}\" agent=\"{settings['agent_id']}\" top_k=\"{settings['top_k']}\" score_threshold=\"{VIKINGBOT_INITIAL_MIN_SCORE}\" user_budget_chars=\"{VIKINGBOT_USER_MEMORY_BUDGET_CHARS}\" agent_budget_chars=\"{VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS}\">\n"
            f"{memory_block}\n"
            "</retrieved_memory>"
        )
    elif retrieval.get("errors"):
        retrieved_memory = (
            "<retrieved_memory source=\"EchoMemory\" status=\"degraded\">\n"
            f"Retrieval error: {'; '.join(retrieval['errors'][:2])}\n"
            "</retrieved_memory>"
        )
    elif use_memory:
        retrieved_memory = (
            f"<retrieved_memory source=\"EchoMemory\" account=\"{settings['account']}\" user=\"{settings['user_id']}\" agent=\"{settings['agent_id']}\" top_k=\"{settings['top_k']}\" score_threshold=\"{VIKINGBOT_INITIAL_MIN_SCORE}\">\n"
            "No relevant memory was retrieved for the current request.\n"
            "</retrieved_memory>"
        )
    else:
        retrieved_memory = "<retrieved_memory source=\"EchoMemory\" status=\"disabled\">\nMemory retrieval is disabled for this request.\n</retrieved_memory>"

    last_user_index = next((idx for idx in range(len(messages) - 1, -1, -1) if messages[idx].get("role") == "user"), None)
    history_messages = messages[:last_user_index] if last_user_index is not None else messages
    history_messages = history_messages[-10:]
    recent_conversation = "\n".join(
        f"{str(item.get('role') or 'message').upper()}: {compact_for_prompt(item.get('content', ''), 900)}"
        for item in history_messages
    ) or "No previous turns are included."
    current_request = str(last_user or "")
    blocks = [
        make_context_block(1, "system", "Agent 章程", "静态规则", "LoCoMo Harness", agent_charter, "charter"),
        make_context_block(2, "system", "行为规则", "静态规则", "EchoMemory QA", behavior_policy, "policy"),
        make_context_block(3, "system", "Retrieved Memory", "EchoMemory", "EchoMemory find/search", retrieved_memory, "memory"),
        make_context_block(4, "history", "最近对话", "浏览器会话", "Agent workbench", f"<recent_conversation>\n{recent_conversation}\n</recent_conversation>", "history"),
        make_context_block(5, "user", "当前请求", "用户", "Current input", f"<current_request>\n{current_request}\n</current_request>", "user"),
    ]
    system = "\n\n".join(block["content"] for block in blocks[:3])
    model_messages = [{"role": "system", "content": system}] + history_messages
    if current_request:
        model_messages.append({"role": "user", "content": current_request})
    prompt_chars = sum(len(str(item.get("content", ""))) for item in model_messages)
    prompt_tokens_est = sum(estimate_tokens(item.get("content", "")) for item in model_messages)
    return {
        "backend": "echomemory",
        "messages": model_messages,
        "retrieval": retrieval,
        "context_trace": {
            "phase": "echomemory-readonly-context-v1",
            "stable_prefix_version": "locomo-harness-context-v1",
            **alignment_metadata("echomemory", "custom_agent_echomemory_sdk_find_search"),
            "layers": [
                {"name": "Agent Charter", "source": "static prompt", "enabled": True, "item_count": 2, "char_count": len(agent_charter) + len(behavior_policy), "highlight": False},
                {"name": "Retrieved Memory", "source": "EchoMemory find/search", "enabled": use_memory, "item_count": len(retrieval.get("items", [])), "char_count": len(memory_block), "highlight": True},
                {"name": "User Memory", "source": "EchoMemory user memory", "enabled": use_memory, "item_count": len(retrieval.get("user_memory", [])), "char_count": len(user_memory_block), "highlight": True},
                {"name": "Agent Memory", "source": "EchoMemory agent memory", "enabled": use_memory, "item_count": len(retrieval.get("agent_memory", [])), "char_count": len(agent_memory_block), "highlight": True},
                {"name": "Recent Conversation", "source": "browser session", "enabled": bool(history_messages), "item_count": len(history_messages), "char_count": sum(len(str(m.get("content", ""))) for m in history_messages), "highlight": False},
            ],
            "blocks": blocks,
            "prompt_chars": prompt_chars,
            "prompt_tokens_est": prompt_tokens_est,
            "model_messages_count": len(model_messages),
            "memory_hits": len(retrieval.get("items", [])),
            "user_memory_hits": len(retrieval.get("user_memory", [])),
            "agent_memory_hits": len(retrieval.get("agent_memory", [])),
            "query_plan": retrieval.get("query_plan", []),
            "retrieval_errors": retrieval.get("errors", []),
            "alignment_notes": [
                "对话工作台通过 EchoMemory local SDK 执行 find/search。",
                "发送消息默认只读；点击保存记忆才会 create_session/add_message/commit_session。",
                (
                    "当前 Web Agent 已接入 EchoMemory 工具循环，可在回答过程中继续 search/read。"
                    if settings["vikingboat_tool_loop"]
                    else "当前 Web Agent 是一次性检索+回答，不是完整多轮工具调用 agent。"
                ),
            ],
            "prompt_engineering": {
                "retrieval_config": {
                    "top_k": settings["top_k"],
                    "score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
                    "user_memory_budget_chars": VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
                    "agent_memory_budget_chars": VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
                    "retrieval_mode": settings["retrieval_mode"],
                    "lexical_fallback": "disabled",
                },
                "system_prompt_structure": [
                    {"name": "Agent Charter", "type": "role_definition", "chars": len(agent_charter)},
                    {"name": "Behavior Policy", "type": "instructions", "chars": len(behavior_policy)},
                    {"name": "Retrieved Memory", "type": "evidence", "chars": len(retrieved_memory)},
                ],
                "few_shot_examples": "none",
                "temperature": 0,
                "architecture": "tool-loop RAG (EchoMemory VikingBoat-lite)" if settings["vikingboat_tool_loop"] else "single-shot RAG (non-agentic)",
            },
        },
        "isolation": {
            "backend": "echomemory",
            "account": settings["account"],
            "user_id": settings["user_id"],
            "agent_id": settings["agent_id"],
            "workspace": settings["workspace"],
            "echomem_root": settings["echomem_root"],
            "memory_write": "disabled",
        },
}


def build_context_preview(payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return run_async(build_context_preview_async(payload, defaults))


def build_tool_loop_args(payload: dict[str, Any], defaults: dict[str, Any], settings: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        answer_model=payload.get("model") or payload.get("answer_model") or defaults.get("answer_model") or defaults.get("judge_model") or "gpt-5.5",
        answer_base_url=payload.get("agent_base_url") or payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or "",
        answer_token=payload.get("api_key") or payload.get("answer_token") or payload.get("judge_token") or defaults.get("answer_token") or defaults.get("judge_token") or defaults.get("api_key") or "",
        timeout_s=settings["timeout_s"],
        model_retries=settings["model_retries"],
        max_iterations=settings["max_iterations"],
        vikingboat_tool_loop=settings["vikingboat_tool_loop"],
        tool_set=settings["tool_set"],
        tool_search_limit=settings["tool_search_limit"],
        tool_min_score=settings["tool_min_score"],
        tool_log_chars=settings["tool_log_chars"],
        top_k=settings["top_k"],
        retrieval_mode=settings["retrieval_mode"],
    )


async def chat_async(payload: dict[str, Any], defaults: dict[str, Any], config_path: Path) -> dict[str, Any]:
    model = payload.get("model") or payload.get("answer_model") or defaults.get("answer_model") or defaults.get("judge_model") or "gpt-5.5"
    temperature = float(payload.get("temperature", 0.2))
    settings = resolve_settings(payload, defaults)
    preview = await build_context_preview_async(payload, defaults)
    result: dict[str, Any] | None = None
    tool_loop_error = ""
    if settings["vikingboat_tool_loop"]:
        try:
            from echomemory_memory_qa import cache_memory_items as qa_cache_memory_items  # noqa: WPS433
            from echomemory_memory_qa import call_echomemory_vikingboat_lite_loop  # noqa: WPS433

            loop_args = build_tool_loop_args(payload, defaults, settings)
            sdk, _, _ = await open_echomem(settings)
            tool_cache: dict[str, dict[str, Any]] = {}
            qa_cache_memory_items(tool_cache, list(preview.get("retrieval", {}).get("items", []) or []))
            loop_result = await call_echomemory_vikingboat_lite_loop(loop_args, sdk, list(preview["messages"]), tool_cache)
            answer = str(loop_result.get("answer") or "").strip()
            if answer:
                result = {
                    "answer": answer,
                    "prompt_tokens": loop_result.get("prompt_tokens") or 0,
                    "completion_tokens": loop_result.get("completion_tokens") or 0,
                    "total_tokens": loop_result.get("total_tokens") or 0,
                    "tool_loop": True,
                    "iteration": loop_result.get("iteration") or 0,
                    "tools_used": loop_result.get("tools_used") or [],
                    "tool_retrieval_error": loop_result.get("tool_retrieval_error") or "",
                    "model_retry_count": loop_result.get("model_retry_count") or 0,
                    "model_error_kind": loop_result.get("model_error_kind") or "",
                }
            else:
                tool_loop_error = str(loop_result.get("model_error_kind") or "empty_tool_loop_answer")
        except Exception as exc:
            tool_loop_error = str(exc)
    if result is None:
        result = llm.openai_chat(
            preview["messages"],
            model,
            temperature,
            api_key=payload.get("api_key") or payload.get("answer_token") or payload.get("judge_token"),
            base_url=payload.get("agent_base_url") or payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url"),
            config_path=config_path,
        )
    if "error" in result:
        result["backend"] = "echomemory"
        result["retrieval"] = preview["retrieval"]
        result["context_trace"] = preview["context_trace"]
        result["isolation"] = preview["isolation"]
        if tool_loop_error:
            result["tool_loop_error"] = tool_loop_error
        return result
    result["backend"] = "echomemory"
    result["retrieval"] = preview["retrieval"]
    result["context_trace"] = preview["context_trace"]
    result["isolation"] = preview["isolation"]
    result["messages"] = preview["messages"]
    if tool_loop_error:
        result["tool_loop_error"] = tool_loop_error
    return result


def chat(payload: dict[str, Any], defaults: dict[str, Any], config_path: Path) -> dict[str, Any]:
    return run_async(chat_async(payload, defaults, config_path))


def session_paths(workspace: str, account: str, user_id: str, agent_id: str, session_id: str) -> dict[str, str]:
    workspace_path = Path(workspace).expanduser().resolve()
    candidates = [
        workspace_path / account / account,
        workspace_path / account,
        workspace_path,
    ]
    account_dir = next((path for path in candidates if (path / "sessions").exists()), candidates[0])
    session_dir = account_dir / "sessions" / session_id
    return {
        "workspace": str(workspace_path),
        "account_dir": str(account_dir),
        "session_dir": str(session_dir),
        "sessions_dir": str(account_dir / "sessions"),
        "memory_root": str(account_dir / "memory"),
        "atoms_dir": str(account_dir / "memory" / ".structured" / "atoms"),
        "users_dir": str(account_dir / "users"),
        "agents_dir": str(account_dir / "agents"),
        "user_dir": str(account_dir / "users" / user_id),
        "agent_dir": str(account_dir / "agents" / agent_id),
        "path_source": "echomemory_workspace_account",
    }


def count_atom_files(paths: dict[str, str]) -> int:
    atoms_dir = Path(paths.get("atoms_dir") or "")
    if not atoms_dir.exists():
        return 0
    try:
        return sum(1 for item in atoms_dir.glob("*.json") if item.is_file())
    except Exception:
        return 0


def public_task(task: Any) -> dict[str, Any]:
    if is_dataclass(task):
        return asdict(task)
    if isinstance(task, dict):
        return dict(task)
    return {
        "task_id": str(getattr(task, "task_id", "") or ""),
        "status": str(getattr(task, "status", "") or ""),
    }


async def archive_chat_async(payload: dict[str, Any], defaults: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    messages = [item for item in payload.get("messages", []) if str(item.get("content") or "").strip()]
    if not messages:
        raise ValueError("当前没有可写入 EchoMemory 的对话消息")
    settings = resolve_settings(payload, defaults)
    trigger = str(payload.get("trigger") or "manual_button")
    message_threshold = max(1, int(payload.get("archive_message_threshold") or 12))
    token_threshold = max(1, int(payload.get("archive_token_threshold") or 3000))
    current_tokens = sum(estimate_tokens(item.get("content", "")) for item in messages)
    threshold_met = len(messages) >= message_threshold or current_tokens >= token_threshold
    trigger_reason = (
        "threshold_auto_messages_or_tokens" if trigger == "threshold_auto"
        else "manual_button_threshold_already_met" if threshold_met
        else "manual_button_before_threshold"
    )
    slug = now_slug()
    safe_account = re.sub(r"[^A-Za-z0-9_.-]+", "-", settings["account"]).strip("-") or "default"
    session_id = str(payload.get("archive_session_id") or f"agent-archive-{safe_account}-{slug}-{uuid.uuid4().hex[:8]}")
    run_dir = output_dir / f"agent_archive_{slug}_{uuid.uuid4().hex[:6]}"
    out_dir = run_dir / "agent_archive"
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / "transcript.json"

    archive_messages: list[dict[str, Any]] = []
    for idx, item in enumerate(messages):
        role = str(item.get("role") or "user").lower()
        if role not in {"user", "assistant", "system"}:
            role = "user"
        content = compact_for_prompt(item.get("content", ""), 6000)
        archive_messages.append(
            {
                "role": role if role in {"user", "assistant"} else "user",
                "role_id": role,
                "content": content,
                "created_at": datetime.fromtimestamp(time.time() + idx).isoformat(timespec="seconds"),
            }
        )
    transcript_path.write_text(json.dumps({"session_id": session_id, "messages": archive_messages}, ensure_ascii=False, indent=2), encoding="utf-8")

    sdk, root, runtime_config = await open_echomem({**settings, "runtime_dir": out_dir})
    context = ctx(settings["account"], settings["user_id"], settings["agent_id"], session_id)
    created_at = datetime.now().isoformat(timespec="seconds")
    created = await sdk.create_session(title=session_id, ctx=context)
    actual_session_id = str(created.get("session_id") or session_id)
    submitted = 0
    last_message_id = ""
    for msg in archive_messages:
        added = await sdk.add_message(
            actual_session_id,
            msg["role"],
            msg["content"],
            ctx=context,
            created_at=msg["created_at"],
            role_id=msg["role_id"],
        )
        last_message_id = str(added.get("message_id") or last_message_id)
        submitted += 1
    before = await sdk.get_history(actual_session_id, ctx=context)
    started = datetime.now().isoformat(timespec="seconds")
    task = await sdk.commit_session(actual_session_id, ctx=context, keep_recent_count=0)
    after = await sdk.get_history(actual_session_id, ctx=context)
    finished = datetime.now().isoformat(timespec="seconds")
    paths = session_paths(settings["workspace"], settings["account"], settings["user_id"], settings["agent_id"], actual_session_id)
    atoms_count = count_atom_files(paths)
    committed = bool(actual_session_id) and submitted == len(archive_messages)
    summary_path = out_dir / "agent_archive_summary.json"
    summary = {
        "status": "ECHOMEMORY_AGENT_ARCHIVE_DONE" if committed else "ECHOMEMORY_AGENT_ARCHIVE_INCOMPLETE",
        "backend": "echomemory",
        "created_at": created_at,
        "commit_started_at": started,
        "commit_finished_at": finished,
        "workspace": settings["workspace"],
        "workspace_input": settings["workspace"],
        "account": settings["account"],
        "user_id": settings["user_id"],
        "agent_id": settings["agent_id"],
        "echomem_root": str(root),
        "echomem_config": str(runtime_config),
        "echomemory_paths": paths,
        "session_id": actual_session_id,
        "trigger": trigger,
        "trigger_reason": trigger_reason,
        "threshold": {
            "messages": message_threshold,
            "tokens_est": token_threshold,
            "condition": "manual button can commit before suggested message/token thresholds",
        },
        "current": {
            "messages": len(archive_messages),
            "tokens_est": current_tokens,
            "threshold_met": threshold_met,
        },
        "submitted_messages": submitted,
        "pending_after_commit": 0,
        "committed": committed,
        "last_added_message_id": last_message_id,
        "create_response": created,
        "commit_response": public_task(task),
        "session_before_commit": {"message_count": len(before)},
        "session_after_commit": {"message_count": len(after), "memories_extracted": {"total": atoms_count}},
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "summary_path": str(summary_path),
        "transcript_path": str(transcript_path),
        "harness_log_dir": str(out_dir),
        "harness_summary_path": str(summary_path),
        "harness_transcript_path": str(transcript_path),
        "task_id": str(getattr(task, "task_id", "") or ""),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def archive_chat(payload: dict[str, Any], defaults: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    return run_async(archive_chat_async(payload, defaults, output_dir))
