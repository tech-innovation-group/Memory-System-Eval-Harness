"""Memory tools exposed to the VikingBot agent."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backends.memory_types import MemoryClient, SearchResult


MEMORY_SEARCH_TOOL = "memory_search"
MEMORY_READ_TOOL = "memory_read_many"
MEMORY_LIST_TOOL = "memory_list"
MEMORY_GREP_TOOL = "memory_grep"
MEMORY_GLOB_TOOL = "memory_glob"
SESSIONS_ROOT = "echo://sessions"
SESSION_FILE = "current/messages.jsonl"


def _resource_search_result(
    item: dict[str, Any],
    path_title_map: dict[str, str] | None = None,
) -> SearchResult:
    """Convert a ``search_resources`` item into a SearchResult with title metadata.

    The resource dict carries a relative ``path`` (e.g. ``hotpotqa/<slug>-<hash>``);
    the corpus map (keyed without the ``user/`` prefix) resolves the title so the
    HotpotQA supporting-fact evaluation can match evidence by ``hotpotqa_title``.
    """
    path = str(item.get("path") or "")
    title = str(
        item.get("hotpotqa_title")
        or (path_title_map or {}).get(path)
        or ""
    ).strip()
    return SearchResult(
        uri=str(item.get("uri") or ""),
        score=float(item.get("score") or 0.0),
        content=str(item.get("text") or ""),
        metadata={"hotpotqa_title": title} if title else {},
    )


def tool_definitions(
    tool_set: str = "search_read",
    *,
    search_target_uri: bool = False,
) -> list[dict[str, Any]]:
    echo_native = tool_set == "vikingbot_echo_native"
    search_properties: dict[str, Any] = {
        "query": {"type": "string", "description": "The search query"},
    }
    if search_target_uri:
        search_properties["target_uri"] = {
            "type": "string",
            "description": (
                "Optional EchoMemory URI prefix to limit search scope. "
                "If omitted, search all available memory."
            ),
        }
    if echo_native:
        search_properties["min_score"] = {
            "type": "number",
            "description": "Minimum relevance score threshold",
            "default": 0.35,
        }
    search_description = (
        "Using query to search for resources (knowledge, files, memories, etc.) in EchoMemory. "
        "Result: Only URIs and summaries are included here. To view the full content, use memory_read_many tool. "
        "This operation performs semantic retrieval, not full character matching. "
        "Avoid duplicate calls with the same intent in the same turn, but do search again for a new user question or a follow-up that asks for a different remembered fact. "
        "For questions about the user's memory, profile, preferences, or personal facts, use this tool before concluding no relevant record exists."
        if echo_native
        else (
            "Using query to search EchoMemory long-term memories and supporting context. "
            "This operation performs semantic retrieval, not full character matching. "
            "Avoid duplicate calls with the same intent in the same turn, but search again "
            "when a follow-up asks for a different remembered fact."
        )
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": MEMORY_SEARCH_TOOL,
                "description": search_description,
                "parameters": {
                    "type": "object",
                    "properties": search_properties,
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": MEMORY_READ_TOOL,
                "description": (
                    "Read full content from multiple EchoMemory resources concurrently. "
                    "Returns complete content for all URIs with no truncation."
                    if echo_native
                    else (
                        "Read full content for up to 20 EchoMemory URIs through HTTP /fs/read. "
                        "For session URIs this resolves the corresponding messages.jsonl when available. "
                        "Use this for relevant summary or session results that need more detail."
                    )
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uris": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of EchoMemory URIs to read from.",
                        },
                    },
                    "required": ["uris"],
                },
            },
        },
    ]
    if tool_set not in {"vikingbot_native_safe", "vikingbot_echo_native"}:
        return tools
    tools.extend([
        {
            "type": "function",
            "function": {
                "name": MEMORY_LIST_TOOL,
                "description": (
                    "List resources in an EchoMemory folder path."
                    if echo_native
                    else (
                        "List EchoMemory items by URI prefix. In HTTP mode this "
                        "uses the public read-only /fs/ls API."
                    )
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": (
                                "Optional parent EchoMemory URI to list. "
                                "Defaults to all visible EchoMemory memory roots."
                                if echo_native
                                else "The parent EchoMemory URI prefix to list."
                            ),
                            **({"default": "echo://"} if echo_native else {}),
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Whether to list recursively",
                            "default": False,
                        },
                    },
                    "required": [] if echo_native else ["uri"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": MEMORY_GREP_TOOL,
                "description": (
                    "Search EchoMemory resources using a regex pattern (like grep)."
                    "Result: Only URIs and matching lines are included here. "
                    "To view the full content, use memory_read_many tool."
                    "Avoid duplicate calls with the same intent in the same turn."
                    if echo_native
                    else (
                        "Search EchoMemory session messages and committed messages "
                        "using regex patterns. In HTTP mode this uses only public "
                        "read-only /fs/glob and /fs/read APIs."
                    )
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": (
                                "Optional EchoMemory URI to search within. "
                                "Defaults to all visible EchoMemory memory roots."
                                if echo_native
                                else "The EchoMemory URI prefix to search within."
                            ),
                            **({"default": "echo://"} if echo_native else {}),
                        },
                        "pattern": {
                            "type": "string" if echo_native else "array",
                            **({} if echo_native else {"items": {"type": "string"}}),
                            "description": (
                                "Regex pattern or array of regex patterns "
                                "to search for"
                            ),
                        },
                        "case_insensitive": {
                            "type": "boolean",
                            "description": "Case-insensitive search",
                            "default": False,
                        },
                    },
                    "required": ["pattern"] if echo_native else ["uri", "pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": MEMORY_GLOB_TOOL,
                "description": (
                    "Find EchoMemory resources using glob patterns (like **/*.md, *.jsonl)."
                    "Result: Only URIs are included here. To view the full content, use memory_read_many tool."
                    if echo_native
                    else (
                        "Find EchoMemory item URIs using glob patterns. In HTTP "
                        "mode this uses the public read-only /fs/glob API."
                    )
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Glob pattern to match (e.g., **/*.md, *.jsonl)"
                                if echo_native
                                else "Glob pattern to match."
                            ),
                        },
                        "uri": {
                            "type": "string",
                            "description": (
                                "Optional EchoMemory URI prefix to search "
                                "within."
                            ),
                            "default": "",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
    ])
    return tools


def _search_payload_items(
    items: list[SearchResult],
    limit: int,
) -> list[SearchResult]:
    selected: list[SearchResult] = []
    seen: set[str] = set()
    for item in items:
        if not item.uri or item.uri in seen:
            continue
        seen.add(item.uri)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def search_payload(items: list[SearchResult], limit: int) -> str:
    memories: list[dict[str, Any]] = []
    for item in _search_payload_items(items, limit):
        memories.append({
            "index": len(memories) + 1,
            "uri": item.uri,
            "abstract": item.content[:700],
            "is_leaf": True,
            "score": round(item.score, 6),
        })
    return json.dumps(
        {"count": len(memories), "memories": memories, "resources": [], "skills": []},
        ensure_ascii=False,
        indent=2,
    )


def execute_tool(
    echomem: MemoryClient,
    name: str,
    arguments: dict[str, Any],
    cache: dict[str, SearchResult],
    *,
    top_k: int,
    tool_search_limit: int,
    tool_search_pool_multiplier: int,
    tool_min_score: float,
    timeout_s: float,
    tool_set: str = "",
    search_resources: bool = False,
    path_title_map: dict[str, str] | None = None,
) -> tuple[str, list[SearchResult]]:
    echo_native = tool_set == "vikingbot_echo_native"
    if name == MEMORY_SEARCH_TOOL:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return "No results found for empty query", []
        if search_resources:
            raw_items = echomem.search_resources(
                query,
                limit=max(top_k, tool_search_limit * tool_search_pool_multiplier),
                tags=["hotpotqa"],
                timeout_s=timeout_s,
            )
            items = [
                _resource_search_result(item, path_title_map)
                for item in raw_items
                if str(item.get("uri") or "").strip()
            ]
        else:
            items = echomem.search(
                query,
                top_k=max(top_k, tool_search_limit * tool_search_pool_multiplier),
                timeout_s=timeout_s,
            )
        requested_min_score = arguments.get("min_score")
        effective_min_score = tool_min_score
        if requested_min_score is not None:
            try:
                effective_min_score = float(requested_min_score)
            except (TypeError, ValueError):
                pass
        items = [
            item for item in items
            if item.score >= effective_min_score
        ]
        target_uri = str(arguments.get("target_uri") or "").strip()
        if target_uri.startswith("echo://"):
            items = [item for item in items if item.uri.startswith(target_uri)]
        for item in items:
            if item.uri:
                cache[item.uri] = item
        exposed_items = _search_payload_items(items, tool_search_limit)
        return search_payload(exposed_items, tool_search_limit), exposed_items
    if name == MEMORY_READ_TOOL:
        raw_uris = arguments.get("uris")
        uris = raw_uris if isinstance(raw_uris, list) else [raw_uris]
        uris = [str(uri or "").strip() for uri in uris if str(uri or "").strip()]
        if not echo_native:
            uris = uris[:20]
        if not uris:
            return "Error: No URIs provided.", []
        lines = [f"Multi-read results for {len(uris)} resources (level: read):"]

        def read_one(uri: str) -> list[str]:
            result_lines = [f"\n--- START OF {uri} ---"]
            content = ""
            read_uri = uri
            candidates: list[str] = []
            session_tail = _session_uri_tail(uri)
            cached_content = (
                cache.get(uri) or SearchResult(uri, 0.0)
            ).content
            if session_tail is not None:
                tail = session_tail
                if not tail:
                    result_lines.extend(
                        _session_candidates_message(
                            echomem,
                            "*",
                            timeout_s=timeout_s,
                            cached_content=cached_content,
                        )
                    )
                    result_lines.append(f"--- END OF {uri} ---")
                    return result_lines
                parts = tail.split("/", 1)
                session_id = parts[0]
                if len(parts) == 2 and parts[1]:
                    file_path = parts[1]
                    if file_path.startswith("current/"):
                        candidates = [
                            f"{SESSIONS_ROOT}/{session_id}/{file_path}"
                        ]
                    else:
                        candidates = [
                            f"{SESSIONS_ROOT}/{session_id}/current/{file_path}"
                        ]
                else:
                    matches = _matching_session_files(
                        echomem,
                        session_id + "*",
                        timeout_s=timeout_s,
                    )
                    exact_file = (
                        f"{SESSIONS_ROOT}/{session_id}/{SESSION_FILE}"
                    )
                    if exact_file in matches:
                        candidates = [exact_file]
                    elif matches:
                        result_lines.extend([
                            "A concrete EchoMemory session URI is required.",
                            "Matching session URIs:",
                            *matches,
                        ])
                        result_lines.append(f"--- END OF {uri} ---")
                        return result_lines
                    else:
                        result_lines.append(
                            "ERROR: No concrete EchoMemory session matched "
                            f"{SESSIONS_ROOT}/{session_id}*"
                        )
                        result_lines.append(f"--- END OF {uri} ---")
                        return result_lines
            elif uri.startswith("echo://engine/"):
                candidates = [uri]
            for candidate in candidates:
                try:
                    content = echomem.fs_read(candidate, timeout_s=timeout_s)
                except Exception:
                    continue
                if content:
                    read_uri = candidate
                    break
            if not content:
                content = cached_content
            if not content and session_tail is not None and "/" not in session_tail:
                result_lines.extend(
                    _session_candidates_message(
                        echomem,
                        session_tail + "*",
                        timeout_s=timeout_s,
                    )
                )
                result_lines.append(f"--- END OF {uri} ---")
                return result_lines
            if read_uri != uri:
                result_lines.append(f"[http_read_uri={read_uri}]")
            result_lines.append(content or f"ERROR: EchoMemory returned empty content for {uri}")
            result_lines.append(f"--- END OF {uri} ---")
            return result_lines

        if echo_native and len(uris) > 1:
            with ThreadPoolExecutor(max_workers=min(10, len(uris))) as pool:
                batches = list(pool.map(read_one, uris))
        else:
            batches = [read_one(uri) for uri in uris]
        for batch in batches:
            lines.extend(batch)
        return "\n".join(lines), []
    if name == MEMORY_LIST_TOOL:
        raw_uri = str(arguments.get("uri") or "").strip()
        session_tail = _session_uri_tail(raw_uri)
        if session_tail and "/" not in session_tail:
            matches = _matching_session_files(
                echomem,
                session_tail + "*",
                timeout_s=timeout_s,
            )
            session_dirs = [
                uri.removesuffix(f"/{SESSION_FILE}") for uri in matches
            ]
            if len(session_dirs) == 1:
                uri = session_dirs[0]
            elif session_dirs:
                return "\n".join([
                    "A concrete EchoMemory session URI is required.",
                    "Matching session URIs:",
                    *session_dirs,
                ]), []
            else:
                uri = _engine_uri(raw_uri)
        else:
            uri = _engine_uri(raw_uri)
        recursive = bool(arguments.get("recursive"))
        try:
            entries = echomem.fs_list(
                uri,
                recursive=recursive,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            return f"Error listing EchoMemory resources: {exc}", []
        rows = [
            {
                "name": str(entry.get("name") or ""),
                "size": int(entry.get("size") or 0),
                "uri": str(entry.get("uri") or ""),
                "isDir": str(entry.get("kind") or "") == "directory",
            }
            for entry in (entries if echo_native else entries[:80])
        ]
        return (
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
            if rows
            else f"No resources found at {uri}"
        ), []
    if name == MEMORY_GLOB_TOOL:
        pattern = str(arguments.get("pattern") or "*").strip() or "*"
        uri = str(arguments.get("uri") or "").strip()
        if uri:
            pattern = uri.rstrip("/") + "/" + pattern.lstrip("/")
        target = _engine_uri(pattern, leaf_pattern=f"*/{SESSION_FILE}")
        try:
            entries = echomem.fs_glob(target, timeout_s=timeout_s)
        except Exception as exc:
            return f"Error globbing EchoMemory resources: {exc}", []
        uris = [
            str(entry.get("uri") or "")
            for entry in entries
            if str(entry.get("uri") or "").strip()
        ]
        if not echo_native:
            uris = uris[:80]
        return (
            f"Found {len(uris)} files:\n" + "\n".join(uris)
            if uris
            else f"No files found for pattern: {target}"
        ), []
    if name == MEMORY_GREP_TOOL:
        return _grep_memory(
            echomem,
            arguments,
            timeout_s=timeout_s,
            native_contract=echo_native,
        ), []
    return f"Error executing {name}: unsupported tool", []


def _session_uri_tail(uri: str) -> str | None:
    if not uri.startswith("echo://"):
        return None
    marker = "/sessions"
    _head, separator, tail = uri.partition(marker)
    if not separator or (tail and not tail.startswith("/")):
        return None
    return tail.strip("/")


def _session_candidates_message(
    echomem: MemoryClient,
    session_pattern: str,
    *,
    timeout_s: float,
    cached_content: str = "",
) -> list[str]:
    if cached_content:
        return [cached_content]
    pattern = f"{SESSIONS_ROOT}/{session_pattern}/{SESSION_FILE}"
    try:
        matches = _matching_session_files(
            echomem,
            session_pattern,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        return [
            "ERROR: A concrete EchoMemory session or file URI is required.",
            f"Unable to list matching sessions: {exc}",
        ]
    if not matches:
        return [
            "ERROR: A concrete EchoMemory session or file URI is required.",
            f"No session file matched {pattern}",
        ]
    return [
        "A concrete EchoMemory session or file URI is required.",
        "Matching session URIs:",
        *matches,
    ]


def _matching_session_files(
    echomem: MemoryClient,
    session_pattern: str,
    *,
    timeout_s: float,
) -> list[str]:
    pattern = f"{SESSIONS_ROOT}/{session_pattern}/{SESSION_FILE}"
    entries = echomem.fs_glob(pattern, timeout_s=timeout_s)
    return list(dict.fromkeys(
        str(entry.get("uri") or "")
        for entry in entries
        if str(entry.get("uri") or "").strip()
    ))[:20]


def _engine_uri(value: str, *, leaf_pattern: str = "") -> str:
    raw = str(value or "").strip()
    sessions_root = SESSIONS_ROOT
    if raw.startswith("echo://engine/"):
        if not leaf_pattern or raw.endswith((".md", ".jsonl", "*")):
            return raw
        if raw.rstrip("/").endswith("/sessions"):
            return raw.rstrip("/") + "/" + leaf_pattern.lstrip("/")
        return raw.rstrip("/") + "/" + leaf_pattern.removeprefix("*/")
    if "/sessions" in raw:
        tail = raw.split("/sessions", 1)[1].strip("/")
        mapped = sessions_root + (f"/{tail}" if tail else "")
        if (
            not leaf_pattern
            or mapped.endswith((".md", ".jsonl", "*"))
            or "*" in tail
        ):
            return mapped
        if not tail:
            return mapped.rstrip("/") + "/" + leaf_pattern.lstrip("/")
        return mapped.rstrip("/") + "/" + leaf_pattern.removeprefix("*/")
    if not raw:
        return (
            sessions_root + f"/{leaf_pattern.lstrip('/')}"
            if leaf_pattern
            else sessions_root
        )
    return (
        sessions_root + f"/{leaf_pattern.lstrip('/')}"
        if leaf_pattern
        else sessions_root
    )


def _grep_memory(
    echomem: MemoryClient,
    arguments: dict[str, Any],
    *,
    timeout_s: float,
    native_contract: bool = False,
) -> str:
    raw_patterns = arguments.get("pattern")
    patterns = (
        raw_patterns if isinstance(raw_patterns, list) else [raw_patterns]
    )
    patterns = [
        str(pattern or "").strip()
        for pattern in patterns
        if str(pattern or "").strip()
    ]
    if not patterns:
        return "No matches found for patterns: ''"
    flags = re.I if arguments.get("case_insensitive") else 0
    uri = str(arguments.get("uri") or "").strip()
    session_tail = _session_uri_tail(uri)
    matched_files = (
        _matching_session_files(
            echomem,
            session_tail + "*",
            timeout_s=timeout_s,
        )
        if session_tail and "/" not in session_tail
        else []
    )
    if matched_files:
        uris = list(dict.fromkeys(matched_files))
    else:
        file_pattern = _engine_uri(uri, leaf_pattern=f"*/{SESSION_FILE}")
    if (
        not matched_files
        and file_pattern.endswith("/messages.jsonl")
        and "*" not in file_pattern
    ):
        uris = [file_pattern]
    elif not matched_files:
        if not file_pattern.endswith((".jsonl", "*")):
            file_pattern = file_pattern.rstrip("/") + f"/*/{SESSION_FILE}"
        entries: list[dict[str, Any]] = []
        try:
            entries.extend(
                echomem.fs_glob(file_pattern, timeout_s=timeout_s)
            )
        except Exception:
            pass
        uris = list(dict.fromkeys(
            str(entry.get("uri") or "")
            for entry in entries
            if str(entry.get("uri") or "").strip()
        ))
    output: list[str] = []
    total = 0
    source_uris = uris if native_contract else uris[:160]
    for item_uri in source_uris:
        try:
            content = echomem.fs_read(item_uri, timeout_s=timeout_s)
        except Exception:
            continue
        for pattern in patterns:
            try:
                regex = re.compile(pattern, flags)
            except re.error:
                regex = re.compile(re.escape(pattern), flags)
            for line_no, line in enumerate(content.splitlines(), 1):
                if not regex.search(line):
                    continue
                output.extend([
                    f"\nFILE {item_uri}",
                    f"Line {line_no} (pattern: {pattern!r}):",
                    line if native_contract else line[:600],
                ])
                total += 1
                if not native_contract and total >= 60:
                    break
            if not native_contract and total >= 60:
                break
        if not native_contract and total >= 60:
            break
    if not output:
        return "No matches found for patterns: " + ", ".join(
            repr(pattern) for pattern in patterns
        )
    return (
        f"Found {total} matches across {len(patterns)} patterns:\n"
        + "\n".join(output)
    )
