from __future__ import annotations

from nano_family_aware_readiness_ablation import run_ablation


def test_family_aware_readiness_beats_strict_and_core_global() -> None:
    report = run_ablation()
    summary = report["summary"]
    assert summary["strict"]["correct"] == 3
    assert summary["core_global"]["correct"] == 3
    assert summary["family_aware"]["correct"] == 4
