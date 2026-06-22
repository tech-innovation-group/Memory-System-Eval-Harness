from __future__ import annotations

from nano_reference_impl_v15_cross_family_benchmark import run


def test_v15_cross_family_benchmark_regression() -> None:
    report = run()
    summary = report["summary"]
    assert summary["v14_correct"] == 4
    assert summary["v15_correct"] == 7
    assert len(summary["improved_cases"]) == 3
