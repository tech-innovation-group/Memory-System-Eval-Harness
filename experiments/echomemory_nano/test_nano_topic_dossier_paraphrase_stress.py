from __future__ import annotations

from nano_topic_dossier_paraphrase_stress import run


def test_topic_dossier_paraphrase_stress_trend() -> None:
    payload = run()
    rows = {row["mode"]: row for row in payload["summary"]}

    baseline = rows["naive_no_hint + lexical"]
    grouping_only = rows["canonicalized_no_hint + lexical"]
    full = rows["canonicalized_no_hint + longitudinal"]

    assert baseline["correct"] <= grouping_only["correct"] <= full["correct"]
