"""Unit tests for the shared document-resource RAG helpers.

Tests the deterministic retriever+generator plumbing (resource search result
mapping and chunk formatting) used by the vikingbot / echomem_mcp document
mode, without any HTTP, MCP, or LLM calls.
"""

from __future__ import annotations

import unittest

from shared.resource_rag import (
    build_retrieval_items,
    format_chunk_section,
)


class TestBuildRetrievalItems(unittest.TestCase):
    def test_maps_path_to_title(self):
        results = [
            {
                "path": "user/hotpotqa/doc-abc",
                "text": "chunk text",
                "score": 0.9,
                "source_uri": "echo://resources/user/hotpotqa/doc-abc",
                "chunk_index": 1,
            }
        ]
        items = build_retrieval_items(results, {"hotpotqa/doc-abc": "Doc Title"})
        self.assertEqual(1, len(items))
        self.assertEqual("Doc Title", items[0]["hotpotqa_title"])
        self.assertEqual("chunk text", items[0]["content"])
        self.assertEqual(0.9, items[0]["score"])

    def test_prefers_explicit_title_metadata(self):
        results = [{
            "path": "user/hotpotqa/doc-abc",
            "text": "t",
            "score": 0.1,
            "hotpotqa_title": "Explicit",
        }]
        items = build_retrieval_items(results, {})
        self.assertEqual("Explicit", items[0]["hotpotqa_title"])

    def test_empty_map_yields_empty_title(self):
        items = build_retrieval_items(
            [{"path": "user/hotpotqa/doc-x", "text": "t", "score": 0.2}],
            {},
        )
        self.assertEqual("", items[0]["hotpotqa_title"])


class TestFormatChunkSection(unittest.TestCase):
    def test_formats_header_with_title_and_score(self):
        items = [{
            "hotpotqa_title": "Doc A",
            "content": "body",
            "score": 0.87,
        }]
        text = format_chunk_section(items)
        self.assertIn("[1] (score: 0.87) title: Doc A", text)
        self.assertIn("body", text)

    def test_respects_budget(self):
        items = [
            {"hotpotqa_title": "A", "content": "x" * 100, "score": 0.5},
            {"hotpotqa_title": "B", "content": "y" * 100, "score": 0.4},
        ]
        text = format_chunk_section(items, budget_chars=50)
        self.assertIn("A", text)
        self.assertNotIn("title: B", text)

    def test_empty(self):
        self.assertEqual("", format_chunk_section([]))


if __name__ == "__main__":
    unittest.main()
