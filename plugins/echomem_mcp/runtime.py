"""MCP tool definitions and system prompt for the echomem_mcp agent plugin.

The LLM is given EchoMem's MCP tools as OpenAI function-calling definitions.
It decides when to search memory and which URIs to read.  Each tool call is
forwarded to the MCP server via ``McpClient`` (in the plugin's send_message).
"""

from __future__ import annotations

from typing import Any

# -- MCP tool definitions (OpenAI function-calling format) ----------------

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_query",
            "description": (
                "Search long-term memory for information relevant to the query. "
                "Returns ranked results with scores and source URIs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default 8).",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read the full content of one or more memory items by URI. "
                "Pass a single echo:// URI or a comma-separated list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uris": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "One or more exact echo:// URIs. Use this `uris` "
                            "array field, never a singular `uri` field. For "
                            "conversation evidence, pass the concrete "
                            "`.../current/messages.jsonl` URI."
                        ),
                    },
                },
                "required": ["uris"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list",
            "description": "List memory entries under a URI prefix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {
                        "type": "string",
                        "description": "URI prefix to list entries under.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Recurse into sub-directories (default false).",
                        "default": False,
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum recursion depth (default 3).",
                        "default": 3,
                    },
                },
                "required": ["uri"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find memory URIs matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. echo://resources/**/*.md",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to EchoMem long-term memory "
    "through the MCP tools provided in this request. "
    "If context is insufficient, use the available EchoMem MCP tools or "
    "memory context to find more information. "
    "Answer the question directly. "
    "Answer with only the exact answer: a single word or short phrase, "
    "with no explanation, preamble, or sentences. For yes/no questions, "
    "answer with exactly 'yes' or 'no'."
)

_NO_TOOLS_SYSTEM_PROMPT = (
    "You are a helpful assistant answering a question from the memory excerpts "
    "included in the conversation. Answer concisely and directly from those "
    "excerpts. Prioritize the supplied EchoMem memory evidence over general "
    "knowledge or unsupported inference. "
    "Preserve exact names, dates, order, and values when the memory provides them. "
    "Do not emit tool calls, function-call markup, XML tool tags, "
    "or a plan to search. Use the available memory to answer as helpfully as "
    "possible. "
    "Answer with only the exact answer: a single word or short phrase, "
    "with no explanation, preamble, or sentences. For yes/no questions, "
    "answer with exactly 'yes' or 'no'."
)


def configured_tools(read_mode: str = "allow") -> list[dict[str, Any]]:
    """Return the MCP tool contract for the requested transcript policy."""
    mode = str(read_mode or "allow").strip().lower()
    if mode not in {"disabled", "allow", "require"}:
        raise ValueError(f"unsupported MCP read mode: {read_mode}")
    if mode == "disabled":
        return [
            tool
            for tool in MCP_TOOLS
            if tool["function"]["name"] != "read"
        ]
    return list(MCP_TOOLS)
