from __future__ import annotations

from nano_reference_impl_v15_write_governance_benchmark import run


def test_v15_write_governance_benchmark_regression() -> None:
    report = run()
    summary = report["summary"]
    assert summary["v14_correct"] == 0
    assert summary["v15_correct"] == 4
    assert len(summary["improved_cases"]) == 4
