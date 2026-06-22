from __future__ import annotations

from nano_topic_dossier_dual_ablation import run


def test_dual_ablation_shows_both_axes_matter() -> None:
    payload = run()
    rows = {row["mode"]: row for row in payload["summary"]}

    baseline = rows["naive_no_hint + lexical"]
    selection_only = rows["naive_no_hint + longitudinal"]
    grouping_only = rows["canonicalized_no_hint + lexical"]
    full = rows["canonicalized_no_hint + longitudinal"]

    assert baseline["correct"] <= selection_only["correct"]
    assert baseline["correct"] <= grouping_only["correct"]
    assert full["correct"] >= grouping_only["correct"]
    assert full["correct"] >= selection_only["correct"]
