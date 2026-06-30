from __future__ import annotations

import json
from pathlib import Path

from memory.datasets import generic_data_overview


def test_hotpotqa_overview_counts_context_supporting_facts_and_answer(tmp_path: Path) -> None:
    dataset_path = tmp_path / "hotpotqa.sample.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "_id": "q1",
                    "question": "Who directed the film?",
                    "answer": "Tim Burton",
                    "type": "bridge",
                    "level": "hard",
                    "supporting_facts": [["Ed Wood (film)", 0]],
                    "context": [
                        [
                            "Ed Wood (film)",
                            [
                                "Ed Wood is a 1994 American biographical comedy-drama film directed by Tim Burton."
                            ],
                        ]
                    ],
                },
                {
                    "_id": "q2",
                    "question": "Who starred in the film?",
                    "answer": "Johnny Depp",
                    "type": "comparison",
                    "level": "hard",
                    "supporting_facts": [],
                    "context": [],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    overview = generic_data_overview(dataset_path)

    assert overview["samples"] == 2
    assert overview["questions"] == 2
    assert overview["hotpotqa_context_present_count"] == 1
    assert overview["hotpotqa_supporting_facts_present_count"] == 1
    assert overview["hotpotqa_answer_present_count"] == 2
    assert overview["hotpotqa_supporting_facts_total"] == 1
