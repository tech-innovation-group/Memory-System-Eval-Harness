from __future__ import annotations

from nano_topic_dossier_state_delta_ablation_20260617 import run


def test_state_delta_beats_timeline_only_on_state_queries() -> None:
    payload = run()
    rows = {row["variant"]: row for row in payload["summary"]}
    timeline = rows["timeline_only_dossier"]
    state_delta = rows["state_delta_dossier"]

    assert state_delta["correct"] > timeline["correct"]
    assert state_delta["correct"] == state_delta["total"]
