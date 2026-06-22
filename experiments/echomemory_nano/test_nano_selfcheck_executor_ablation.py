from __future__ import annotations

from nano_selfcheck_executor_ablation import run_ablation


def test_selfcheck_executor_outperforms_advisory() -> None:
    report = run_ablation()
    summary = report["summary"]

    assert summary["cases"] == 7
    assert summary["primary_direct_correct"] == summary["advisory_only_correct"]
    assert summary["advisory_gap_detected"] >= 5
    assert len(summary["advisory_detected_but_still_wrong"]) >= 5
    assert summary["executive_policy_correct"] > summary["advisory_only_correct"]
    assert "readiness_premature" in summary["executive_not_ready_cases"]
    assert "relational_should_abstain" in summary["executive_abstained_cases"]
