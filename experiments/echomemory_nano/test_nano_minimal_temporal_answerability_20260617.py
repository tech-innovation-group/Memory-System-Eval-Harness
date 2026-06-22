from __future__ import annotations

from nano_minimal_temporal_answerability_20260617 import (
    MinimalTemporalAnswerabilityNano,
    build_cases,
    evaluate,
)


def test_minimal_nano_summary_pattern() -> None:
    report = evaluate()
    summary = report["summary"]

    assert summary["flat_direct"]["correct"] == 1
    assert summary["temporal_only"]["correct"] == 4
    assert summary["full_family_aware"]["correct"] == 5


def test_temporal_arbitration_fixes_retrospective_mention() -> None:
    nano = MinimalTemporalAnswerabilityNano()
    cases = {case.case_id: case for case in build_cases()}

    flat = nano.run_case(cases["temporal_retro_mention"], "flat_direct")
    temporal = nano.run_case(cases["temporal_retro_mention"], "temporal_only")

    assert flat["answer"] == "2026-05-20"
    assert temporal["answer"] == "2026-05-13"


def test_family_aware_gate_fixes_relational_case() -> None:
    nano = MinimalTemporalAnswerabilityNano()
    cases = {case.case_id: case for case in build_cases()}

    temporal_only = nano.run_case(cases["relational_path_grounding"], "temporal_only")
    full = nano.run_case(cases["relational_path_grounding"], "full_family_aware")

    assert temporal_only["answer"] == "Figma"
    assert "path_grounding" in temporal_only["missing"]
    assert full["answer"] == "Maya"
    assert full["correct"] is True


def test_readiness_case_remains_blocked() -> None:
    nano = MinimalTemporalAnswerabilityNano()
    cases = {case.case_id: case for case in build_cases()}

    result = nano.run_case(cases["readiness_barrier"], "full_family_aware")
    assert result["answer"] == "not_ready"
    assert result["correct"] is True
