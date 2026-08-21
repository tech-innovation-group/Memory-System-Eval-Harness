"""Shared formatting helpers for document-resource RAG retrieval.

Used by the document mode (``--import-mode documents``) of the HotpotQA
benchmark: the retrieved resource chunks are mapped to evidence items with a
``hotpotqa_title`` (for supporting-fact evaluation) and formatted into a
bounded prompt section. Shared by the vikingbot and echomem_mcp plugins so
the logic has a single source of truth.
"""

from __future__ import annotations

from typing import Any

RESOURCE_SYSTEM_PROMPT = (
    "You are answering a question using the retrieved documents below. "
    "Base your answer only on these documents. If the documents do not "
    "contain the information, answer with exactly 'noanswer'. "
    "Answer with only the exact answer: a single word or short phrase, "
    "with no explanation, preamble, or sentences. For yes/no questions, "
    "answer with exactly 'yes' or 'no'. Preserve exact names, dates, "
    "and values when present."
)

_RESOURCE_DOMAINS = ("user", "agent", "mem")


def _strip_domain(path: str) -> str:
    """Strip the top-level resource domain prefix (e.g. ``user/``)."""
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[0] in _RESOURCE_DOMAINS:
        return parts[1]
    return path


def format_chunk_section(
    items: list[dict[str, Any]],
    budget_chars: int = 0,
) -> str:
    """Format retrieved resource chunks into a bounded prompt section.

    The first item is always included so a tight budget cannot drop all
    context; subsequent items are skipped once the budget is exhausted.
    """
    sections: list[str] = []
    total = 0
    for i, item in enumerate(items, 1):
        title = str(item.get("hotpotqa_title") or "").strip()
        text = str(item.get("content") or "")
        header = f"[{i}] (score: {float(item.get('score') or 0.0):.2f})"
        if title:
            header += f" title: {title}"
        block = f"{header}\n{text}"
        separator = 2 if sections else 0
        if (
            not budget_chars
            or total + separator + len(block) <= budget_chars
            or not sections
        ):
            sections.append(block)
            total += separator + len(block)
    return "\n\n".join(sections)


def build_retrieval_items(
    results: list[dict[str, Any]],
    path_title_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Map resource search results to evidence items with a hotpotqa_title.

    Search results carry the full resource path (``user/hotpotqa/...``)
    while the corpus map from import is keyed without the ``user/`` prefix;
    both forms are tried so the title resolves.
    """
    path_title_map = path_title_map or {}
    items: list[dict[str, Any]] = []
    for result in results:
        path = str(result.get("path") or "")
        title = str(
            result.get("hotpotqa_title")
            or path_title_map.get(path)
            or path_title_map.get(_strip_domain(path))
            or ""
        ).strip()
        items.append({
            "uri": str(result.get("source_uri") or ""),
            "path": path,
            "score": float(result.get("score") or 0.0),
            "content": str(result.get("text") or ""),
            "hotpotqa_title": title,
            "chunk_index": result.get("chunk_index"),
        })
    return items