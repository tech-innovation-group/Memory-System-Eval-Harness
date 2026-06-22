from __future__ import annotations

from nano_visual_ingest_bridge import benchmark_cases, evaluate_mode


def test_structured_visual_ingest_beats_surface_and_none() -> None:
    cases = benchmark_cases()
    no_visual = evaluate_mode("no_visual_ingest", cases)
    surface = evaluate_mode("surface_visual_ingest", cases)
    structured = evaluate_mode("structured_visual_ingest", cases)

    assert no_visual["summary"]["correct"] < surface["summary"]["correct"] < structured["summary"]["correct"]
    assert structured["summary"]["correct"] == structured["summary"]["total"]


def test_structured_mode_links_dashboard_owner() -> None:
    cases = benchmark_cases()
    structured = evaluate_mode("structured_visual_ingest", cases)
    owner_row = next(row for row in structured["rows"] if row["case_id"] == "dashboard_owner")

    assert owner_row["passed"] is True
    assert owner_row["result"]["hits"][0]["node_type"] == "image_evidence"
    assert any(hit["node_id"] == "entity:Alice" for hit in owner_row["result"]["hits"])
