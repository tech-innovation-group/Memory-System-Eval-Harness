from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable

from echomemory_qa_common import (
    ECHOMEMORY_VIKINGBOAT_TOOL_SET,
    MEMORY_GLOB_TOOL_NAME,
    MEMORY_GREP_TOOL_NAME,
    MEMORY_LIST_TOOL_NAME,
    MEMORY_MULTI_READ_TOOL_NAME,
    MEMORY_SEARCH_TOOL_NAME,
    compact,
)
def memory_uri(item: dict[str, Any]) -> str:
    return str(item.get("uri") or item.get("path") or item.get("id") or "")


def _strip_raw_turn_metadata(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    speaker_match = None
    for match in re.finditer(r"\[(?!turn=|session_date=|turn_time=|time_expression=|current=)([^\]=]{1,40})\]", cleaned):
        speaker_match = match
    if speaker_match:
        tail = cleaned[speaker_match.end() :].strip()
        tail = re.sub(r"^[A-Z]\d+:\d+:\s*", "", tail).strip()
        if tail:
            return tail
    cleaned = re.sub(r"^(?:session_date=[^\[]+\s*)+", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"(?:\[(?:turn|session_date|turn_time|created_at|speaker)=[^\]]+\]\s*)+", "", cleaned, flags=re.I).strip()
    return cleaned


def _strip_session_summary_metadata(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    kept: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.lower().startswith("## session metadata"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _strip_event_memory_metadata(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    statement_match = re.search(r"\bstatement:\s*(.+?)(?:\s+-\s+event_time:|\s+<!--|$)", cleaned, flags=re.I | re.S)
    if statement_match:
        statement = " ".join(statement_match.group(1).split()).strip(" -")
        if statement:
            return statement
    cleaned = re.sub(r"^[^<\n]*?\b(?:event_id|statement):\s*", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\s*<!--[\s\S]*$", "", cleaned).strip()
    return cleaned


def sanitize_memory_content(item: dict[str, Any], text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    raw = str(item.get("memory_type") or item.get("type") or "memory").strip().lower()
    uri = str(item.get("uri") or item.get("path") or item.get("id") or "").strip().lower()
    memory_type = raw or "memory"
    if uri.endswith("/overview.md") or uri.endswith("/abstract.md") or uri.endswith("/summary"):
        memory_type = "session_summary"
    elif "messages.jsonl#turn=" in uri:
        memory_type = "raw_turn"
    elif "event" in raw or "/events/" in uri:
        memory_type = "event_memory"
    elif raw == "segment_memory":
        memory_type = "segment_memory"
    if memory_type in {"raw_turn", "segment_memory"}:
        cleaned = _strip_raw_turn_metadata(cleaned)
    elif memory_type == "session_summary":
        cleaned = _strip_session_summary_metadata(cleaned)
    elif memory_type == "event_memory":
        cleaned = _strip_event_memory_metadata(cleaned)
    cleaned = re.sub(r"\s*<!--[\s\S]*$", "", cleaned).strip()
    return cleaned


def memory_content(item: dict[str, Any]) -> str:
    raw = str(
        item.get("content")
        or item.get("text")
        or item.get("abstract")
        or item.get("overview")
        or item.get("summary")
        or item.get("preview")
        or ""
    )
    return sanitize_memory_content(item, raw)


def log_retrieved_memory_preview(
    job: Any,
    hits: list[dict[str, Any]],
    *,
    question_no: int | None = None,
    max_items: int = 5,
    preview_chars: int = 220,
    hit_score_fn: Callable[[dict[str, Any]], float],
    memory_type_fn: Callable[[dict[str, Any]], str],
) -> None:
    prefix = f"[memory] q{question_no}" if question_no else "[memory]"
    print(
        f"{prefix} {job.question_id} retrieved={len(hits)} "
        f"sample={job.sample_id}",
        flush=True,
    )
    for index, item in enumerate(sorted(hits, key=hit_score_fn, reverse=True)[:max_items], 1):
        uri = compact(memory_uri(item), 180)
        memory_type = memory_type_fn(item)
        score = hit_score_fn(item)
        preview = compact(memory_content(item), preview_chars)
        print(
            f"[memory]   #{index} score={score:.3f} type={memory_type} uri={uri}",
            flush=True,
        )
        if preview:
            print(f"[memory]      {preview}", flush=True)


def log_retrieval_resolution(
    job: Any,
    *,
    question_no: int | None = None,
    initial_hits: int,
    tool_search_hits: int,
    tool_read_calls: int,
    effective_hits: int,
) -> None:
    prefix = f"[memory] q{question_no}" if question_no else "[memory]"
    print(
        f"{prefix} {job.question_id} initial_hits={initial_hits} "
        f"tool_search_hits={tool_search_hits} tool_reads={tool_read_calls} "
        f"effective_hits={effective_hits}",
        flush=True,
    )


def cache_memory_items(cache: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> None:
    for item in items:
        uri = memory_uri(item)
        if uri and uri not in cache:
            cache[uri] = item


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


def search_result_kind(item: dict[str, Any]) -> str:
    raw = str(item.get("memory_type") or item.get("type") or item.get("backend") or "").lower()
    uri = memory_uri(item).lower()
    if "skill" in raw or "/skills/" in uri:
        return "skills"
    if "resource" in raw or "/resources/" in uri:
        return "resources"
    return "memories"


def echomemory_search_payload(
    items: list[dict[str, Any]],
    limit: int,
    *,
    hit_score_fn: Callable[[dict[str, Any]], float],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {"memories": [], "resources": [], "skills": []}
    emitted = 0
    seen_uris: set[str] = set()
    for item in items:
        score = hit_score_fn(item)
        uri = memory_uri(item)
        if not uri or uri in seen_uris:
            continue
        seen_uris.add(uri)
        emitted += 1
        grouped[search_result_kind(item)].append(
            {
                "index": emitted,
                "uri": uri,
                "abstract": compact(memory_content(item), 700),
                "is_leaf": True,
                "score": round(score, 6),
            }
        )
        if emitted >= limit:
            break
    return {"count": emitted, **grouped}


async def execute_echomemory_search_tool(
    args: argparse.Namespace,
    sdk: Any,
    tool_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    *,
    retrieve_fn: Callable[[argparse.Namespace, Any, str], Any],
    hit_score_fn: Callable[[dict[str, Any]], float],
) -> tuple[str, str, int]:
    query = str(tool_args.get("query") or "").strip()
    if not query:
        return "No results found for empty query", "", 0
    tool_query_args = argparse.Namespace(**vars(args))
    # Keep the native Top-K boundary by default. A larger pool is an explicit
    # adapter experiment, not part of the strict black-box benchmark profile.
    tool_query_args.top_k = max(
        int(args.top_k),
        int(args.tool_search_limit)
        * max(1, int(getattr(args, "tool_search_pool_multiplier", 4) or 4)),
    )
    hits, retrieval_error, _timing = await retrieve_fn(tool_query_args, sdk, query)
    cache_memory_items(cache, hits)
    payload = echomemory_search_payload(hits, int(args.tool_search_limit), hit_score_fn=hit_score_fn)
    if payload["count"] == 0:
        return f"No results found for query: {query}", retrieval_error, 0
    return json.dumps(payload, ensure_ascii=False, indent=2), retrieval_error, int(payload["count"])


def execute_echomemory_multi_read_tool(
    tool_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> str:
    raw_uris = tool_args.get("uris")
    if isinstance(raw_uris, str):
        uris = [raw_uris]
    elif isinstance(raw_uris, list):
        uris = [str(uri) for uri in raw_uris if str(uri or "").strip()]
    else:
        uris = []
    if not uris:
        return "Error: No URIs provided."
    lines = [f"Multi-read results for {len(uris)} resources (level: read):"]
    for uri in uris[:20]:
        lines.append(f"\n--- START OF {uri} ---")
        item = cache.get(uri)
        content = memory_content(item or {})
        lines.append(content if content else f"ERROR: Error reading from EchoMemory memory item: empty content for {uri}")
        lines.append(f"--- END OF {uri} ---")
    if len(uris) > 20:
        lines.append(f"\nSkipped {len(uris) - 20} URIs to keep the tool result bounded.")
    return "\n".join(lines)


async def execute_echomemory_http_multi_read_tool(
    sdk: Any,
    tool_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> tuple[str, str, int]:
    raw_uris = tool_args.get("uris")
    if isinstance(raw_uris, str):
        uris = [raw_uris]
    elif isinstance(raw_uris, list):
        uris = [str(uri) for uri in raw_uris if str(uri or "").strip()]
    else:
        uris = []
    if not uris:
        return "Error: No URIs provided.", "", 0

    lines = [f"Multi-read results for {len(uris)} resources (level: read):"]
    errors: list[str] = []
    read_count = 0
    for uri in uris[:20]:
        lines.append(f"\n--- START OF {uri} ---")
        content = ""
        read_uri = uri
        read_candidates = [uri]
        if uri.startswith("echo://") and "/sessions/" in uri:
            session_tail = uri.split("/sessions/", 1)[1].strip("/")
            session_id = session_tail.split("/", 1)[0]
            source = str((cache.get(uri) or {}).get("source") or "echo0_plugin").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", source):
                source = "echo0_plugin"
            read_candidates = [
                f"echo://sessions/{session_id}/current/messages.jsonl",
                # f"echo://engine/{source}/sessions/{session_id}/overview.md",
                # f"echo://engine/{source}/sessions/{session_id}/abstract.md",
            ]
        candidate_errors: list[str] = []
        for candidate_uri in read_candidates:
            try:
                payload = await sdk.fs_read(candidate_uri)
                candidate_content = str((payload or {}).get("content") or "").strip()
                if candidate_content:
                    content = candidate_content
                    read_uri = candidate_uri
                    read_count += 1
                    # For session messages.jsonl, parse and keep only created_at + content
                    if read_uri.endswith("/messages.jsonl"):
                        formatted_lines: list[str] = []
                        for line in candidate_content.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                                created_at = msg.get("created_at", "")
                                text = msg.get("content", "")
                                if text:
                                    formatted_lines.append(f"[{created_at}] {text}")
                            except Exception:
                                formatted_lines.append(line)
                        content = "\n".join(formatted_lines)
                    break
            except Exception as exc:
                candidate_errors.append(f"{candidate_uri}: {exc}")
        if not content and candidate_errors:
            errors.extend(candidate_errors)
        if not content:
            content = memory_content(cache.get(uri) or {})
        if read_uri != uri:
            lines.append(f"[http_read_uri={read_uri}]")
        lines.append(content if content else f"ERROR: EchoMemory returned empty content for {uri}")
        lines.append(f"--- END OF {uri} ---")
    if len(uris) > 20:
        lines.append(f"\nSkipped {len(uris) - 20} URIs to keep the tool result bounded.")
    return "\n".join(lines), "; ".join(errors[:5]), read_count


def execute_echomemory_list_tool(cache: dict[str, dict[str, Any]], uri: str = "", recursive: bool = False) -> str:
    prefix = str(uri or "").strip()
    rows = []
    for item in sorted(cache.values(), key=lambda value: memory_uri(value)):
        item_uri = memory_uri(item)
        if prefix and not item_uri.startswith(prefix):
            continue
        rows.append(
            str(
                {
                    "name": Path(item_uri.rstrip("/")).name or item_uri,
                    "size": len(memory_content(item)),
                    "uri": item_uri,
                    "isDir": False,
                }
            )
        )
        if not recursive and len(rows) >= 30:
            break
    return "\n".join(rows) if rows else f"No resources found at {prefix or 'cached EchoMemory results'}"


def execute_echomemory_grep_tool(tool_args: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str:
    raw_patterns = tool_args.get("pattern")
    patterns = raw_patterns if isinstance(raw_patterns, list) else [raw_patterns]
    patterns = [str(pattern or "").strip() for pattern in patterns if str(pattern or "").strip()]
    uri_prefix = str(tool_args.get("uri") or "").strip()
    flags = re.I if tool_args.get("case_insensitive") else 0
    if not patterns:
        return "No matches found for patterns: ''"
    results: list[str] = []
    total = 0
    for item in cache.values():
        item_uri = memory_uri(item)
        if uri_prefix and not item_uri.startswith(uri_prefix):
            continue
        content = memory_content(item)
        for pattern in patterns:
            try:
                regex = re.compile(pattern, flags)
            except re.error:
                regex = re.compile(re.escape(pattern), flags)
            for line_no, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    if not results or results[-1] != f"\n📄 {item_uri}":
                        results.append(f"\n📄 {item_uri}")
                    results.append(f"   Line {line_no} (pattern: '{pattern}'):")
                    results.append(f"   {line[:600]}")
                    total += 1
                    if total >= 60:
                        return f"Found {total} matches across {len(patterns)} patterns:\n" + "\n".join(results)
    if not results:
        return "No matches found for patterns: " + ", ".join(f"'{pattern}'" for pattern in patterns)
    return f"Found {total} matches across {len(patterns)} patterns:\n" + "\n".join(results)


def execute_echomemory_glob_tool(tool_args: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str:
    pattern = str(tool_args.get("pattern") or "*").strip() or "*"
    uri_prefix = str(tool_args.get("uri") or "").strip()
    regex = re.escape(pattern).replace("\\*\\*", ".*").replace("\\*", "[^/]*")
    compiled = re.compile(f"^{regex}$")
    matches = []
    for item in sorted(cache.values(), key=lambda value: memory_uri(value)):
        item_uri = memory_uri(item)
        if uri_prefix and not item_uri.startswith(uri_prefix):
            continue
        name = item_uri.split("://", 1)[-1]
        if compiled.search(name) or compiled.search(Path(item_uri).name):
            matches.append(item_uri)
        if len(matches) >= 80:
            break
    if not matches:
        return f"No files found for pattern: {pattern}"
    return "Found " + str(len(matches)) + " files:\n" + "\n".join(f"📄 {uri}" for uri in matches)


def http_engine_uri(value: str, *, leaf_pattern: str = "") -> str:
    raw = str(value or "").strip()
    engine_sessions = "echo://engine/echo0_plugin/sessions"
    if raw.startswith("echo://engine/"):
        if not leaf_pattern or raw.endswith((".md", "*")):
            return raw
        if raw.rstrip("/").endswith("/sessions"):
            return raw.rstrip("/") + "/" + leaf_pattern.lstrip("/")
        return raw.rstrip("/") + "/" + leaf_pattern.removeprefix("*/").lstrip("/")
    if "/sessions" in raw:
        session_tail = raw.split("/sessions", 1)[1].strip("/")
        if not session_tail:
            suffix = f"/{leaf_pattern.lstrip('/')}" if leaf_pattern else ""
            return engine_sessions + suffix
        mapped = engine_sessions + "/" + session_tail
        if not leaf_pattern or mapped.endswith((".md", "*")) or "*" in session_tail:
            return mapped
        return mapped.rstrip("/") + "/" + leaf_pattern.removeprefix("*/").lstrip("/")
    if not raw:
        suffix = f"/{leaf_pattern.lstrip('/')}" if leaf_pattern else ""
        return engine_sessions + suffix
    return raw


async def execute_echomemory_http_list_tool(
    sdk: Any,
    uri: str = "",
    recursive: bool = False,
) -> tuple[str, str, int]:
    target = http_engine_uri(uri)
    try:
        if recursive:
            pattern = target.rstrip("/") + "/**"
            entries = await sdk.fs_glob(pattern)
        else:
            entries = await sdk.fs_list(target)
    except Exception as exc:
        return f"Error listing EchoMemory resources: {exc}", str(exc), 0
    rows = []
    for entry in entries[:80]:
        rows.append(
            str(
                {
                    "name": str(entry.get("name") or ""),
                    "size": int(entry.get("size") or 0),
                    "uri": str(entry.get("uri") or ""),
                    "isDir": str(entry.get("kind") or "") == "directory",
                }
            )
        )
    return "\n".join(rows) if rows else f"No resources found at {target}", "", len(rows)


async def execute_echomemory_http_glob_tool(
    sdk: Any,
    tool_args: dict[str, Any],
) -> tuple[str, str, int]:
    pattern = str(tool_args.get("pattern") or "*").strip() or "*"
    uri_prefix = str(tool_args.get("uri") or "").strip()
    if uri_prefix:
        pattern = uri_prefix.rstrip("/") + "/" + pattern.lstrip("/")
    target = http_engine_uri(pattern, leaf_pattern="*/overview.md")
    try:
        entries = await sdk.fs_glob(target)
    except Exception as exc:
        return f"Error globbing EchoMemory resources: {exc}", str(exc), 0
    uris = [str(entry.get("uri") or "") for entry in entries if str(entry.get("uri") or "").strip()]
    if not uris:
        return f"No files found for pattern: {target}", "", 0
    return "Found " + str(len(uris)) + " files:\n" + "\n".join(f"📄 {uri}" for uri in uris[:80]), "", len(uris)


async def execute_echomemory_http_grep_tool(
    sdk: Any,
    tool_args: dict[str, Any],
) -> tuple[str, str, int]:
    raw_patterns = tool_args.get("pattern")
    patterns = raw_patterns if isinstance(raw_patterns, list) else [raw_patterns]
    patterns = [str(pattern or "").strip() for pattern in patterns if str(pattern or "").strip()]
    if not patterns:
        return "No matches found for patterns: ''", "", 0
    flags = re.I if tool_args.get("case_insensitive") else 0
    uri_prefix = str(tool_args.get("uri") or "").strip()
    overview_pattern = http_engine_uri(uri_prefix, leaf_pattern="*/overview.md")
    if overview_pattern.endswith("/overview.md") and "*" not in overview_pattern:
        entries = [
            {"uri": overview_pattern},
            {"uri": overview_pattern.removesuffix("/overview.md") + "/messages.jsonl"},
        ]
    else:
        if not overview_pattern.endswith((".md", "*")):
            overview_pattern = overview_pattern.rstrip("/") + "/*/overview.md"
        message_pattern = overview_pattern.removesuffix("/overview.md") + "/messages.jsonl"
        entries = []
        glob_errors: list[str] = []
        for pattern in (overview_pattern, message_pattern):
            try:
                entries.extend(await sdk.fs_glob(pattern))
            except Exception as exc:
                glob_errors.append(f"{pattern}: {exc}")
        if not entries and glob_errors:
            return f"Error globbing EchoMemory resources: {'; '.join(glob_errors)}", "; ".join(glob_errors), 0
    results: list[str] = []
    errors: list[str] = []
    total = 0
    seen_uris: set[str] = set()
    for entry in entries[:160]:
        item_uri = str(entry.get("uri") or "").strip()
        if not item_uri or item_uri in seen_uris:
            continue
        seen_uris.add(item_uri)
        try:
            payload = await sdk.fs_read(item_uri)
            content = str((payload or {}).get("content") or "")
        except Exception as exc:
            errors.append(f"{item_uri}: {exc}")
            continue
        for pattern in patterns:
            try:
                regex = re.compile(pattern, flags)
            except re.error:
                regex = re.compile(re.escape(pattern), flags)
            for line_no, line in enumerate(content.splitlines(), 1):
                if not regex.search(line):
                    continue
                if not results or results[-1] != f"\n📄 {item_uri}":
                    results.append(f"\n📄 {item_uri}")
                results.append(f"   Line {line_no} (pattern: '{pattern}'):")
                results.append(f"   {line[:600]}")
                total += 1
                if total >= 60:
                    return f"Found {total} matches across {len(patterns)} patterns:\n" + "\n".join(results), "; ".join(errors[:5]), total
    if not results:
        return "No matches found for patterns: " + ", ".join(f"'{pattern}'" for pattern in patterns), "; ".join(errors[:5]), 0
    return f"Found {total} matches across {len(patterns)} patterns:\n" + "\n".join(results), "; ".join(errors[:5]), total


def echomemory_search_tool_definition(args: argparse.Namespace | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "query": {"type": "string", "description": "The search query"},
    }
    if bool(getattr(args, "search_tool_target_uri_schema", False)):
        properties["target_uri"] = {
            "type": "string",
            "description": "Optional EchoMemory URI prefix to limit search scope. If omitted, search all available memory.",
        }
    return {
        "type": "function",
        "function": {
            "name": MEMORY_SEARCH_TOOL_NAME,
            "description": (
                "Using query to search EchoMemory long-term memories and supporting context. "
                "This operation performs semantic retrieval, not full character matching. "
                "Avoid duplicate calls with the same intent in the same turn, but search again "
                "when a follow-up asks for a different remembered fact."
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["query"],
            },
        },
    }


def echomemory_multi_read_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_MULTI_READ_TOOL_NAME,
            "description": (
                "Read full content for up to 20 EchoMemory URIs through HTTP /fs/read. "
                "For session URIs this resolves the corresponding overview.md when available. "
                "Use this for relevant summary or session results that need more detail."
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
    }


def echomemory_list_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_LIST_TOOL_NAME,
            "description": "List EchoMemory items by URI prefix. In HTTP mode this uses the public read-only /fs/ls API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The parent EchoMemory URI prefix to list."},
                    "recursive": {"type": "boolean", "description": "Whether to list recursively", "default": False},
                },
                "required": ["uri"],
            },
        },
    }


def echomemory_grep_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_GREP_TOOL_NAME,
            "description": (
                "Search EchoMemory session overview and committed messages using regex patterns. "
                "In HTTP mode this uses only public read-only /fs/glob and /fs/read APIs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The EchoMemory URI prefix to search within."},
                    "pattern": {"type": "array", "items": {"type": "string"}, "description": "Regex pattern or array of regex patterns to search for"},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive search", "default": False},
                },
                "required": ["uri", "pattern"],
            },
        },
    }


def echomemory_glob_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_GLOB_TOOL_NAME,
            "description": "Find EchoMemory item URIs using glob patterns. In HTTP mode this uses the public read-only /fs/glob API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match."},
                    "uri": {"type": "string", "description": "Optional EchoMemory URI prefix to search within.", "default": ""},
                },
                "required": ["pattern"],
            },
        },
    }


def echomemory_tool_definitions(
    args: argparse.Namespace,
    *,
    normalize_tool_set_fn: Callable[..., str],
) -> list[dict[str, Any]]:
    tool_set = normalize_tool_set_fn(
        getattr(args, "tool_set", ""),
        vikingboat_compat=bool(getattr(args, "vikingboat_compat", False)),
    )
    search_tool = echomemory_search_tool_definition(args)
    if tool_set == "search_only":
        return [search_tool]
    if tool_set == ECHOMEMORY_VIKINGBOAT_TOOL_SET:
        return [
            search_tool,
            echomemory_multi_read_tool_definition(),
            echomemory_list_tool_definition(),
            echomemory_grep_tool_definition(),
            echomemory_glob_tool_definition(),
        ]
    return [search_tool, echomemory_multi_read_tool_definition()]


async def execute_echomemory_tool(
    args: argparse.Namespace,
    sdk: Any,
    name: str,
    parsed_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    *,
    retrieve_fn: Callable[[argparse.Namespace, Any, str], Any],
    hit_score_fn: Callable[[dict[str, Any]], float],
) -> tuple[str, str, int]:
    if name == MEMORY_SEARCH_TOOL_NAME:
        return await execute_echomemory_search_tool(
            args,
            sdk,
            parsed_args,
            cache,
            retrieve_fn=retrieve_fn,
            hit_score_fn=hit_score_fn,
        )
    if name == MEMORY_MULTI_READ_TOOL_NAME:
        if getattr(sdk, "_compat_layout", "") == "http":
            return await execute_echomemory_http_multi_read_tool(sdk, parsed_args, cache)
        return execute_echomemory_multi_read_tool(parsed_args, cache), "", 0
    if name == MEMORY_LIST_TOOL_NAME:
        if getattr(sdk, "_compat_layout", "") == "http":
            return await execute_echomemory_http_list_tool(
                sdk,
                str(parsed_args.get("uri") or ""),
                bool(parsed_args.get("recursive")),
            )
        return execute_echomemory_list_tool(cache, str(parsed_args.get("uri") or ""), bool(parsed_args.get("recursive"))), "", 0
    if name == MEMORY_GREP_TOOL_NAME:
        if getattr(sdk, "_compat_layout", "") == "http":
            return await execute_echomemory_http_grep_tool(sdk, parsed_args)
        return execute_echomemory_grep_tool(parsed_args, cache), "", 0
    if name == MEMORY_GLOB_TOOL_NAME:
        if getattr(sdk, "_compat_layout", "") == "http":
            return await execute_echomemory_http_glob_tool(sdk, parsed_args)
        return execute_echomemory_glob_tool(parsed_args, cache), "", 0
    return f"Error executing {name}: unsupported tool", "", 0


def search_payload_uris(result_text: str, limit: int) -> list[str]:
    try:
        payload = json.loads(result_text)
    except Exception:
        return []
    uris: list[str] = []
    for group_name in ("memories", "resources", "skills"):
        group = payload.get(group_name) if isinstance(payload, dict) else None
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "").strip()
            if uri and uri not in uris:
                uris.append(uri)
            if len(uris) >= limit:
                return uris
    return uris
