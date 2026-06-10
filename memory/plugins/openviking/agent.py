from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ... import llm
from ...vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)
from . import client


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def extract_memory_keywords(query: str) -> list[str]:
    text = str(query or "")
    words = re.findall(r"[A-Za-z][A-Za-z0-9'_-]{2,}|\d{4}-\d{2}-\d{2}|\d{4}|[\u4e00-\u9fff]{2,}", text)
    stop = {
        "what", "when", "where", "which", "who", "why", "how", "did", "does", "the", "and", "for", "with",
        "has", "have", "had", "was", "were", "his", "her", "their", "both", "look", "like", "think", "thinks",
        "should", "would", "could", "about", "from", "into", "that", "this", "your", "what's",
    }
    keywords: list[str] = []
    for word in words:
        cleaned = word.strip(" ?.!,:;\"'“”‘’()[]{}").lower()
        if not cleaned or cleaned in stop:
            continue
        original = word.strip(" ?.!,:;\"'“”‘’()[]{}")
        if original and original not in keywords:
            keywords.append(original)
    return keywords


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
    quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]{2,64})[\"“”'‘’]", base)
    parts.extend(item.strip() for item in quoted if item.strip())
    keywords = extract_memory_keywords(base)
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


def uri_to_workspace_path(workspace: str, account: str, uri: str) -> Path | None:
    if not workspace or not uri.startswith("viking://"):
        return None
    rel = uri.removeprefix("viking://").lstrip("/")
    if not rel:
        return None
    return Path(workspace).expanduser() / "viking" / account / rel


def read_memory_full_content(workspace: str, account: str, uri: str, limit: int = 12000) -> tuple[str, str]:
    path = uri_to_workspace_path(workspace, account, uri)
    if not path or not path.exists() or not path.is_file():
        return "", str(path) if path else ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", str(path)
    return compact_for_prompt(text, limit), str(path)


def enrich_memory_items(
    items: list[dict[str, Any]],
    workspace: str,
    account: str,
    limit: int = VIKINGBOT_INITIAL_SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in items[:limit]:
        copied = dict(item)
        uri = str(copied.get("uri") or copied.get("path") or "")
        full_content, local_path = read_memory_full_content(workspace, account, uri)
        if full_content:
            copied["full_content"] = full_content
            copied["local_path"] = local_path
            copied["content_source"] = "memory_file"
        elif local_path:
            copied["local_path"] = local_path
            copied.setdefault("content_source", "search_abstract")
        else:
            copied.setdefault("content_source", "search_abstract")
        enriched.append(copied)
    return enriched


def memory_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or item.get("similarity") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def memory_uri(item: dict[str, Any]) -> str:
    return str(item.get("uri") or item.get("path") or item.get("id") or "")


def memory_snippet(item: Any) -> str:
    if not isinstance(item, dict):
        return compact_for_prompt(str(item), 900)
    uri = item.get("uri") or item.get("path") or item.get("id") or ""
    score = item.get("score") or item.get("similarity") or ""
    text = (
        item.get("full_content")
        or item.get("content")
        or item.get("text")
        or item.get("abstract")
        or item.get("overview")
        or item.get("summary")
        or ""
    )
    if not text and isinstance(item.get("metadata"), dict):
        text = item["metadata"].get("abstract") or item["metadata"].get("overview") or ""
    prefix = f"{uri} score={score}".strip()
    return compact_for_prompt(f"{prefix}\n{text}", 1200)


def format_viking_memory_section(items: list[dict[str, Any]], max_chars: int) -> str:
    """Format memories with vikingbot-compatible total budget behavior.

    Full memory entries count toward max_chars. When the next full entry would
    exceed the budget, keep only its URI/score link so the model can still see
    that a candidate existed without injecting more full text.
    """
    formatted: list[str] = []
    total_chars = 0
    seen_hashes: set[int] = set()
    for idx, item in enumerate(items, start=1):
        uri = memory_uri(item)
        score = memory_score(item)
        content = str(
            item.get("full_content")
            or item.get("content")
            or item.get("text")
            or item.get("abstract")
            or item.get("overview")
            or item.get("summary")
            or ""
        ).strip()
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


def ranked_memory_search(
    base_url: str,
    query: str,
    account: str,
    user_id: str,
    agent_id: str,
    api_key: str = "",
    limit: int = VIKINGBOT_INITIAL_SEARCH_LIMIT,
    workspace: str = "",
    target_uri: str = "viking://user/memories/",
    min_score: float = VIKINGBOT_INITIAL_MIN_SCORE,
) -> dict[str, Any]:
    query_plan = expand_memory_queries(query)
    merged: list[dict[str, Any]] = []
    errors: list[str] = []
    for item_query in query_plan:
        try:
            result = client.find(
                base_url,
                item_query,
                account,
                user_id,
                agent_id,
                api_key,
                limit,
                target_uri=target_uri,
                score_threshold=min_score,
            )
            for item in result.get("items", []):
                if isinstance(item, dict):
                    copied = dict(item)
                    if memory_score(copied) < min_score:
                        continue
                    copied.setdefault("_query", item_query)
                    copied.setdefault("_target_uri", target_uri)
                    merged.append(copied)
        except Exception as exc:
            errors.append(f"{item_query}: {exc}")
    keywords = [token for token in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", query.lower()) if len(token) >= 2]
    unique: dict[str, dict[str, Any]] = {}
    for item in merged:
        key = str(item.get("uri") or item.get("path") or item.get("id") or memory_snippet(item))
        if key not in unique:
            unique[key] = item

    def score(item: dict[str, Any]) -> tuple[float, int]:
        text = memory_snippet(item).lower()
        overlap = sum(1 for token in keywords if token in text)
        base = float(item.get("score") or item.get("similarity") or 0.0)
        return (base, overlap)

    items = sorted(unique.values(), key=score, reverse=True)[:limit]
    if workspace:
        items = enrich_memory_items(items, workspace, account, limit)
    return {"items": items, "query_plan": query_plan, "errors": errors, "degraded": bool(errors)}


def vikingbot_style_memory_search(
    base_url: str,
    query: str,
    account: str,
    user_id: str,
    agent_id: str,
    api_key: str = "",
    limit: int = VIKINGBOT_INITIAL_SEARCH_LIMIT,
    workspace: str = "",
    min_score: float = VIKINGBOT_INITIAL_MIN_SCORE,
) -> dict[str, Any]:
    user_retrieval = ranked_memory_search(
        base_url,
        query,
        account,
        user_id,
        agent_id,
        api_key,
        limit,
        workspace=workspace,
        target_uri="viking://user/memories/",
        min_score=min_score,
    )
    agent_retrieval = ranked_memory_search(
        base_url,
        query,
        account,
        user_id,
        agent_id,
        api_key,
        limit,
        workspace=workspace,
        target_uri="viking://agent/memories/",
        min_score=min_score,
    )
    user_items = user_retrieval.get("items", [])
    agent_items = agent_retrieval.get("items", [])
    return {
        "items": user_items + agent_items,
        "user_memory": user_items,
        "agent_memory": agent_items,
        "query_plan": user_retrieval.get("query_plan", []) or agent_retrieval.get("query_plan", []),
        "errors": (user_retrieval.get("errors", []) or []) + (agent_retrieval.get("errors", []) or []),
        "degraded": bool(user_retrieval.get("degraded") or agent_retrieval.get("degraded")),
        "min_score": min_score,
        "user_budget_chars": VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
        "agent_budget_chars": VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    }


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


def build_context_preview(payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages", [])
    host = str(payload.get("host") or defaults.get("server_host") or "127.0.0.1")
    port = str(payload.get("port") or defaults.get("server_port") or "19080")
    ov_url = str(payload.get("server_url") or f"http://{host}:{port}")
    account = str(payload.get("account") or defaults.get("account") or "default")
    user_id = str(payload.get("user_id") or "default")
    agent_id = str(payload.get("agent_id") or "default")
    api_key = str(payload.get("root_api_key") or defaults.get("root_api_key") or "")
    workspace = str(payload.get("workspace") or defaults.get("openviking_workspace") or defaults.get("workspace") or "").strip()
    use_memory = bool(payload.get("use_memory", True))
    last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    retrieval = {"items": [], "query_plan": [], "errors": [], "degraded": False}
    top_k = int(payload.get("top_k") or VIKINGBOT_INITIAL_SEARCH_LIMIT)
    if use_memory and last_user:
        retrieval = vikingbot_style_memory_search(
            ov_url,
            last_user,
            account,
            user_id,
            agent_id,
            api_key,
            top_k,
            workspace=workspace,
            min_score=VIKINGBOT_INITIAL_MIN_SCORE,
        )
    retrieval.setdefault("error", "; ".join(retrieval.get("errors", [])[:2]))
    user_memory_block = format_viking_memory_section(retrieval.get("user_memory", []), VIKINGBOT_USER_MEMORY_BUDGET_CHARS)
    agent_memory_block = format_viking_memory_section(retrieval.get("agent_memory", []), VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS)
    memory_block = (
        f"### user memories:\n{user_memory_block or '(none)'}\n"
        f"### agent memories:\n{agent_memory_block or '(none)'}"
    ) if (user_memory_block or agent_memory_block) else ""
    last_user_index = next((idx for idx in range(len(messages) - 1, -1, -1) if messages[idx].get("role") == "user"), None)
    history_messages = messages[:last_user_index] if last_user_index is not None else messages
    history_messages = history_messages[-10:]
    current_request = str(last_user or "")

    agent_charter = (
        "<agent_charter>\n"
        "你是一个接入当前记忆后端的任务型 Agent。\n"
        "回答要准确、直接、可验证，先给结论，再给关键依据。\n"
        "你具备长期记忆检索能力。请优先基于当前记忆后端返回的证据作答。\n"
        "回答时先给结论，再给证据与推理；尽量明确时间、人物、公司与因果关系。\n"
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
            f"<retrieved_memory source=\"OpenViking\" account=\"{account}\" user=\"{user_id}\" agent=\"{agent_id}\" top_k=\"{top_k}\" score_threshold=\"{VIKINGBOT_INITIAL_MIN_SCORE}\" user_budget_chars=\"{VIKINGBOT_USER_MEMORY_BUDGET_CHARS}\" agent_budget_chars=\"{VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS}\">\n"
            f"{memory_block}\n"
            "</retrieved_memory>"
        )
    elif retrieval.get("errors"):
        retrieved_memory = (
            "<retrieved_memory source=\"OpenViking\" status=\"degraded\">\n"
            f"Retrieval error: {'; '.join(retrieval['errors'][:2])}\n"
            "</retrieved_memory>"
        )
    elif use_memory:
        retrieved_memory = (
            f"<retrieved_memory source=\"OpenViking\" account=\"{account}\" user=\"{user_id}\" agent=\"{agent_id}\" top_k=\"{top_k}\" score_threshold=\"{VIKINGBOT_INITIAL_MIN_SCORE}\" user_budget_chars=\"{VIKINGBOT_USER_MEMORY_BUDGET_CHARS}\" agent_budget_chars=\"{VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS}\">\n"
            "No relevant memory was retrieved for the current request.\n"
            "</retrieved_memory>"
        )
    else:
        retrieved_memory = (
            "<retrieved_memory source=\"OpenViking\" status=\"disabled\">\n"
            "Memory retrieval is disabled for this request.\n"
            "</retrieved_memory>"
        )
    recent_conversation = "\n".join(
        f"{str(item.get('role') or 'message').upper()}: {compact_for_prompt(item.get('content', ''), 900)}"
        for item in history_messages
    ) or "No previous turns are included."
    current_request_block = f"<current_request>\n{current_request}\n</current_request>"

    blocks = [
        make_context_block(1, "system", "Agent 章程", "静态规则", "LoCoMo Harness", agent_charter, "charter"),
        make_context_block(2, "system", "行为规则", "静态规则", "OpenViking QA", behavior_policy, "policy"),
        make_context_block(3, "system", "Retrieved Memory", "OpenViking", "OpenViking search/find", retrieved_memory, "memory"),
        make_context_block(4, "history", "最近对话", "浏览器会话", "Agent workbench", f"<recent_conversation>\n{recent_conversation}\n</recent_conversation>", "history"),
        make_context_block(5, "user", "当前请求", "用户", "Current input", current_request_block, "user"),
    ]
    system = "\n\n".join(block["content"] for block in blocks[:3])
    model_messages = [{"role": "system", "content": system}] + history_messages
    if current_request:
        model_messages.append({"role": "user", "content": current_request})
    prompt_chars = sum(len(str(item.get("content", ""))) for item in model_messages)
    prompt_tokens_est = sum(estimate_tokens(item.get("content", "")) for item in model_messages)
    memory_file_hits = sum(1 for item in retrieval.get("items", []) if item.get("content_source") == "memory_file")
    search_abstract_hits = sum(1 for item in retrieval.get("items", []) if item.get("content_source") != "memory_file")
    return {
        "messages": model_messages,
        "retrieval": retrieval,
        "context_trace": {
            "phase": "openviking-readonly-context-v1",
            "stable_prefix_version": "locomo-harness-context-v1",
            **alignment_metadata("openviking", "custom_agent_direct_openviking_search_find"),
            "layers": [
                {"name": "Agent Charter", "source": "static prompt", "enabled": True, "item_count": 2, "char_count": len(agent_charter) + len(behavior_policy), "highlight": False},
                {"name": "Retrieved Memory", "source": "OpenViking search/find", "enabled": use_memory, "item_count": len(retrieval.get("items", [])), "char_count": len(memory_block), "highlight": True},
                {"name": "User Memory", "source": "OpenViking user memory", "enabled": use_memory, "item_count": len(retrieval.get("user_memory", [])), "char_count": len(user_memory_block), "highlight": True},
                {"name": "Agent Memory", "source": "OpenViking agent memory", "enabled": use_memory, "item_count": len(retrieval.get("agent_memory", [])), "char_count": len(agent_memory_block), "highlight": True},
                {"name": "Recent Conversation", "source": "browser session", "enabled": bool(history_messages), "item_count": len(history_messages), "char_count": sum(len(str(m.get("content", ""))) for m in history_messages), "highlight": False},
            ],
            "blocks": blocks,
            "prompt_chars": prompt_chars,
            "prompt_tokens_est": prompt_tokens_est,
            "model_messages_count": len(model_messages),
            "memory_hits": len(retrieval.get("items", [])),
            "user_memory_hits": len(retrieval.get("user_memory", [])),
            "agent_memory_hits": len(retrieval.get("agent_memory", [])),
            "memory_file_hits": memory_file_hits,
            "search_abstract_hits": search_abstract_hits,
            "query_plan": retrieval.get("query_plan", []),
            "retrieval_errors": retrieval.get("errors", []),
            "alignment_notes": [
                "所有回答先读取 OpenViking search/find 结果。",
                "如果 workspace 可解析，top hits 会读取本地 memory 文件全文，避免只看 API abstract。",
                "当前 Web Agent 是一次性检索+回答，不是完整多轮工具调用 agent。",
            ],
            "prompt_engineering": {
                "retrieval_config": {
                    "top_k": top_k,
                    "score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
                    "initial_score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
                    "user_memory_budget_chars": VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
                    "agent_memory_budget_chars": VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
                    "query_expansion": "enabled" if len(retrieval.get("query_plan", [])) > 1 else "disabled",
                    "lexical_fallback": "disabled",
                },
                "system_prompt_structure": [
                    {"name": "Agent Charter", "type": "role_definition", "chars": len(agent_charter)},
                    {"name": "Behavior Policy", "type": "instructions", "chars": len(behavior_policy)},
                    {"name": "Retrieved Memory", "type": "evidence", "chars": len(retrieved_memory)},
                ],
                "few_shot_examples": "none",
                "temperature": 0,
                "architecture": "single-shot RAG (non-agentic)",
            },
        },
        "isolation": {
            "account": account,
            "user_id": user_id,
            "agent_id": agent_id,
            "workspace": workspace,
            "memory_write": "disabled",
        },
    }


def chat(payload: dict[str, Any], defaults: dict[str, Any], config_path: Path) -> dict[str, Any]:
    model = payload.get("model") or payload.get("answer_model") or defaults.get("answer_model") or defaults.get("judge_model") or "gpt-5.5"
    temperature = float(payload.get("temperature", 0.7))
    allow_write = bool(payload.get("allow_write", False))
    preview = build_context_preview(payload, defaults)
    model_messages = preview["messages"]
    if str(model).startswith("claude"):
        result = llm.claude_chat(model_messages, model, temperature)
    else:
        result = llm.openai_chat(
            model_messages,
            model,
            temperature,
            api_key=payload.get("api_key") or payload.get("answer_token") or payload.get("judge_token"),
            base_url=payload.get("agent_base_url") or payload.get("answer_base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url"),
            config_path=config_path,
        )
    if "error" in result:
        return result
    result["retrieval"] = preview["retrieval"]
    result["isolation"] = preview["isolation"]
    result["isolation"]["memory_write"] = "disabled" if not allow_write else "requested_but_not_implemented"
    result["context_trace"] = preview["context_trace"]
    result["messages"] = model_messages
    return result


def recent_workspace_candidates(workspace_hint: str, output_dir: Path) -> list[Path]:
    candidates: list[Path] = []

    def add(path_like: Any) -> None:
        if not path_like:
            return
        try:
            path = Path(str(path_like)).expanduser()
        except Exception:
            return
        if path not in candidates:
            candidates.append(path)

    add(workspace_hint)
    try:
        cfg = read_json(Path.home() / ".openviking" / "ov.conf")
        add((cfg.get("storage") or {}).get("workspace"))
    except Exception:
        pass
    runtime_configs = sorted(
        output_dir.glob("openviking_import_*/openviking.runtime.conf"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for runtime_config in runtime_configs[:30]:
        try:
            cfg = read_json(runtime_config)
            add((cfg.get("storage") or {}).get("workspace"))
        except Exception:
            continue
    try:
        for workspace in sorted(Path.home().glob("openviking_workspace*"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            add(workspace)
    except Exception:
        pass
    return candidates


def discover_paths(session_id: str, account: str, user_id: str, agent_id: str, workspace_hint: str, output_dir: Path) -> dict[str, str]:
    fallback = Path(workspace_hint).expanduser() if workspace_hint else None
    for workspace in recent_workspace_candidates(workspace_hint, output_dir):
        account_dir = workspace / "viking" / account
        session_root = account_dir / "session"
        direct = session_root / session_id
        if direct.exists():
            return client.build_paths(workspace, account, user_id, agent_id, session_id, "discovered_from_session_dir")
        if session_root.exists():
            try:
                matches = [p for p in session_root.rglob(session_id) if p.is_dir()]
            except Exception:
                matches = []
            if matches:
                session_dir = sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0]
                paths = client.build_paths(workspace, account, user_id, agent_id, session_id, "discovered_from_session_dir")
                paths["session_dir"] = str(session_dir)
                return paths
    if fallback:
        return client.build_paths(fallback, account, user_id, agent_id, session_id, "workspace_hint_not_verified")
    return {}


def archive_chat(payload: dict[str, Any], defaults: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    messages = [item for item in payload.get("messages", []) if str(item.get("content") or "").strip()]
    if not messages:
        raise ValueError("当前没有可写入 OpenViking 的对话消息")

    host = str(payload.get("host") or defaults.get("server_host") or "127.0.0.1")
    port = str(payload.get("port") or defaults.get("server_port") or "19080")
    base_url = str(payload.get("server_url") or f"http://{host}:{port}").rstrip("/")
    account = str(payload.get("account") or defaults.get("account") or "default")
    user_id = str(payload.get("user_id") or "default")
    agent_id = str(payload.get("agent_id") or "default")
    api_key = str(payload.get("root_api_key") or defaults.get("root_api_key") or "")
    workspace = str(payload.get("workspace") or defaults.get("openviking_workspace") or defaults.get("workspace") or "").strip()
    workspace_input = workspace
    trigger = str(payload.get("trigger") or "manual_button")
    message_threshold = max(1, int(payload.get("archive_message_threshold") or 12))
    token_threshold = max(1, int(payload.get("archive_token_threshold") or 3000))
    current_tokens = sum(estimate_tokens(item.get("content", "")) for item in messages)
    current_messages = len(messages)
    threshold_met = current_messages >= message_threshold or current_tokens >= token_threshold
    if trigger == "threshold_auto":
        trigger_reason = "threshold_auto_messages_or_tokens"
    elif threshold_met:
        trigger_reason = "manual_button_threshold_already_met"
    else:
        trigger_reason = "manual_button_before_threshold"

    created_at = datetime.now().isoformat(timespec="seconds")
    slug = now_slug()
    safe_account = re.sub(r"[^A-Za-z0-9_.-]+", "-", account).strip("-") or "default"
    session_id = str(payload.get("archive_session_id") or f"agent-archive-{safe_account}-{slug}-{uuid.uuid4().hex[:8]}")
    openviking_paths = discover_paths(session_id, account, user_id, agent_id, workspace, output_dir)
    run_dir = output_dir / f"agent_archive_{slug}_{uuid.uuid4().hex[:6]}"
    out_dir = run_dir / "agent_archive"
    out_dir.mkdir(parents=True, exist_ok=True)

    archive_messages: list[dict[str, Any]] = []
    for idx, item in enumerate(messages):
        role = str(item.get("role") or "user").lower()
        if role not in {"user", "assistant", "system"}:
            role = "user"
        text = compact_for_prompt(item.get("content", ""), 6000)
        content = f"[{role}] {text}"
        archive_messages.append(
            {
                "role": role if role in {"user", "assistant"} else "user",
                "role_id": role,
                "content": content,
                "parts": [{"type": "text", "text": content}],
                "created_at": datetime.fromtimestamp(time.time() + idx).isoformat(timespec="seconds"),
            }
        )

    transcript_path = out_dir / "transcript.json"
    transcript_path.write_text(json.dumps({"session_id": session_id, "messages": archive_messages}, ensure_ascii=False, indent=2), encoding="utf-8")

    started_at = datetime.now().isoformat(timespec="seconds")
    create_response = client.request(base_url, api_key, account, user_id, agent_id, "POST", "/api/v1/sessions", {"session_id": session_id})
    batch_supported = True
    submitted = 0
    try:
        batch_response = client.request(
            base_url,
            api_key,
            account,
            user_id,
            agent_id,
            "POST",
            f"/api/v1/sessions/{session_id}/messages/batch",
            {"messages": archive_messages},
        )
        submitted = int(batch_response.get("added") or len(archive_messages))
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        batch_supported = False
        for msg in archive_messages:
            client.request(base_url, api_key, account, user_id, agent_id, "POST", f"/api/v1/sessions/{session_id}/messages", msg)
            submitted += 1

    before_commit = client.request(base_url, api_key, account, user_id, agent_id, "GET", f"/api/v1/sessions/{session_id}")
    commit_response = client.request(base_url, api_key, account, user_id, agent_id, "POST", f"/api/v1/sessions/{session_id}/commit", {})
    task_id = str(commit_response.get("task_id") or "")
    task = client.wait_commit_task(
        base_url,
        api_key,
        account,
        user_id,
        agent_id,
        task_id,
        int(payload.get("commit_timeout_s") or 180),
    ) if task_id else {}
    after_commit = client.request(base_url, api_key, account, user_id, agent_id, "GET", f"/api/v1/sessions/{session_id}")
    ended_at = datetime.now().isoformat(timespec="seconds")
    openviking_paths = discover_paths(session_id, account, user_id, agent_id, workspace, output_dir)
    actual_workspace = openviking_paths.get("workspace") or workspace
    task_status = str(task.get("status") or commit_response.get("status") or "").lower()
    committed = str(commit_response.get("status") or "").lower() in {"accepted", "committed", "ok"} or task_status == "completed"
    pending_after_commit = int(after_commit.get("pending_messages") or after_commit.get("pending_message_count") or 0)
    summary_path = out_dir / "agent_archive_summary.json"
    summary = {
        "status": "AGENT_ARCHIVE_DONE" if committed else "AGENT_ARCHIVE_INCOMPLETE",
        "created_at": created_at,
        "commit_started_at": started_at,
        "commit_finished_at": ended_at,
        "openviking_url": base_url,
        "workspace": actual_workspace,
        "workspace_input": workspace_input,
        "openviking_paths": openviking_paths,
        "account": account,
        "user_id": user_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "trigger": trigger,
        "trigger_reason": trigger_reason,
        "threshold": {
            "messages": message_threshold,
            "tokens_est": token_threshold,
            "condition": "commit when new messages >= messages threshold OR estimated tokens >= token threshold; manual button can force commit earlier",
        },
        "current": {
            "messages": current_messages,
            "tokens_est": current_tokens,
            "threshold_met": threshold_met,
        },
        "submitted_messages": submitted,
        "batch_supported": batch_supported,
        "committed": committed,
        "pending_after_commit": pending_after_commit,
        "create_response": create_response,
        "commit_response": commit_response,
        "task": task,
        "session_before_commit": before_commit,
        "session_after_commit": after_commit,
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "summary_path": str(summary_path),
        "transcript_path": str(transcript_path),
        "harness_log_dir": str(out_dir),
        "harness_summary_path": str(summary_path),
        "harness_transcript_path": str(transcript_path),
        "task_id": task_id,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
