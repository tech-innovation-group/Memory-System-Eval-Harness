"""VikingBot prompt assembly migrated from the v2 workbench."""

from __future__ import annotations

import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from backends.memory_types import SearchResult

DEFAULT_BOOTSTRAP_FILES = ("SOUL.md", "TOOLS.md")


def build_question_prompt(question: str, question_time: str) -> str:
    if question_time.strip():
        return f"Current date: {question_time}. Answer the question directly: {question}"
    return f"Answer the question directly: {question}"


def load_bootstrap(
    workspace: Path,
    bootstrap_files: tuple[str, ...] = DEFAULT_BOOTSTRAP_FILES,
) -> str:
    parts: list[str] = []
    for filename in bootstrap_files:
        path = workspace / filename
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            parts.append(f"## {filename}\n\n{content}")
    return "\n\n---\n\n".join(parts)


def build_system_prompt(vikingbot_workspace: str = "") -> str:
    workspace = (
        Path(vikingbot_workspace).expanduser().resolve()
        if vikingbot_workspace.strip()
        else Path.cwd().resolve()
    )
    runtime = (
        f"{'macOS' if platform.system() == 'Darwin' else platform.system()} "
        f"{platform.machine()}, Python {platform.python_version()}"
    )
    capabilities = """You have access to tools that allow you to:
- Read, search, and grep OpenViking files
- Read, write, and edit local files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks"""
    parts = [f"""# vikingbot 🐈

You are VikingBot, an AI assistant built based on the OpenViking context database.
When acquiring information, data, and knowledge, you **prioritize using openviking tools to read and search OpenViking (a context database) above all other sources**.
{capabilities}

## Runtime
{runtime}

## Workspace
You have two workspaces:
1. Local workspace: {workspace}
2. OpenViking workspace: managed via OpenViking tools
- Custom skills: {workspace}/skills/{{skill-name}}/SKILL.md

IMPORTANT:
- When responding to direct questions or conversations, reply directly with your text response.
- Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).For normal conversation, just respond with text - do not call the message tool.
- Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.

## Memory
- Remember important facts: using openviking_memory_commit tool to commit"""]
    bootstrap = load_bootstrap(workspace)
    if bootstrap:
        parts.append(bootstrap)
    parts.append(
        "## Direct QA Evidence Use\n"
        "- For date and timeline questions, distinguish an event's actual date from later phrases such as as-of, mentioned on, recalled on, or current date.\n"
        "- If a retrieved memory is link-only and the question asks for a date or concrete fact, inspect the linked OpenViking memory with openviking_multi_read before deciding.\n"
        "- Answer only the specific items requested by the question; do not add adjacent plans or extra facts unless needed to resolve the answer."
    )
    return "\n\n---\n\n".join(parts)


def format_memory(items: list[SearchResult], max_chars: int) -> str:
    lines: list[str] = []
    used = 0
    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        content = item.content.strip()
        if content and content in seen:
            continue
        if content:
            seen.add(content)
        link = (
            f'<memory index="{index}" type="link">\n'
            f"  <uri>{item.uri}</uri>\n"
            f"  <score>{item.score:.3f}</score>\n"
            f"</memory>"
        )
        full = (
            f'<memory index="{index}" type="full">\n'
            f"  <uri>{item.uri}</uri>\n"
            f"  <score>{item.score:.3f}</score>\n"
            f"  <content>{content}</content>\n"
            f"</memory>"
        )
        candidate = full if content and used + len(full) + 1 <= max_chars else link
        needed = len(candidate) + (1 if lines else 0)
        if used + needed > max_chars:
            continue
        lines.append(candidate)
        used += needed
    return "\n".join(lines)


def build_messages(
    question: str,
    question_time: str,
    items: list[SearchResult],
    user_memory_budget_chars: int,
    agent_memory_budget_chars: int,
    vikingbot_workspace: str,
    qa_profile: str = "",
    system_prompt_append: str = "",
) -> list[dict[str, Any]]:
    def with_prompt_append(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prompt = system_prompt_append.strip()
        if not prompt:
            return messages
        updated = [dict(message) for message in messages]
        for message in updated:
            if message.get("role") == "system":
                message["content"] = (
                    f"{str(message.get('content') or '').rstrip()}"
                    f"\n\n---\n\n{prompt}"
                )
                return updated
        raise ValueError("QA prompt append requires a system message")

    if qa_profile == "vikingboat0411":
        from .vikingboat0411_prompting import build_vikingboat0411_messages

        return with_prompt_append(build_vikingboat0411_messages(
            question,
            question_time,
            items,
            user_memory_budget_chars,
            agent_memory_budget_chars,
        ))
    if qa_profile == "vikingboat0411-natural-no-tools":
        from .vikingboat0411_prompting import (
            build_vikingboat0411_natural_no_tools_messages,
        )

        return with_prompt_append(build_vikingboat0411_natural_no_tools_messages(
            question,
            question_time,
            items,
            user_memory_budget_chars,
            agent_memory_budget_chars,
        ))
    user_items: list[SearchResult] = []
    agent_items: list[SearchResult] = []
    for item in items:
        if "/agent/" in item.uri.lower() or item.memory_type.lower().startswith("agent"):
            agent_items.append(item)
        else:
            user_items.append(item)
    user_memory = format_memory(user_items, user_memory_budget_chars)
    agent_memory = format_memory(agent_items, agent_memory_budget_chars)
    now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    tz = time.strftime("%Z") or "UTC"
    evidence = (
        f"### user memories:\n{user_memory or '(none)'}\n\n"
        f"### agent memories:\n{agent_memory or '(none)'}"
    )
    memory_message = (
        f"## Current Time: {now} ({tz})\n\n---\n\n"
        "## Current Session\nChannel: cli\n\n---\n\n"
        f"## openviking_search(query=[user_query])\n{evidence}\n\n---\n\n"
        "Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:"
    )
    return with_prompt_append([
        {"role": "system", "content": build_system_prompt(vikingbot_workspace)},
        {"role": "user", "content": memory_message},
        {"role": "user", "content": build_question_prompt(question, question_time)},
    ])
