from __future__ import annotations

from nano_typed_path_constraint_ablation import run_ablation


def test_typed_path_constraints_fix_shared_neighbor_failures() -> None:
    report = run_ablation()
    summary = report["summary"]
    assert summary["cases"] == 3
    assert summary["typed_correct"] == 3
    assert summary["naive_correct"] < summary["typed_correct"]
    assert set(summary["typed_fixed_cases"]) == {
        "introducer_maya_leo",
        "referrer_aria_ivy",
        "connector_elena_chen",
    }
