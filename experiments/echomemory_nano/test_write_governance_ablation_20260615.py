from __future__ import annotations

from write_governance_ablation_20260615 import (
    AppendOnlyMemory,
    GovernedVersionedMemory,
    WriteTimeLatestMemory,
    build_cases,
    run_variant,
)


def test_write_governance_variants_regression() -> None:
    cases = build_cases()
    append_only = run_variant(AppendOnlyMemory, cases)
    write_time_latest = run_variant(WriteTimeLatestMemory, cases)
    governed_versioned = run_variant(GovernedVersionedMemory, cases)

    assert append_only["passed"] == 0
    assert write_time_latest["passed"] == 2
    assert governed_versioned["passed"] == 5
