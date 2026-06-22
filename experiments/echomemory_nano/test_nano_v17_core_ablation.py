from __future__ import annotations

from nano_v17_core_ablation import run_ablation


def test_v17_core_ablation_matches_expected_pattern() -> None:
    report = run_ablation()
    variants = {item["variant"]: item for item in report["variants"]}
    assert variants["full_v17"]["correct"] == 4
    assert variants["write_time_only"]["correct"] < variants["full_v17"]["correct"]
    assert variants["atom_only_no_dossier"]["correct"] < variants["full_v17"]["correct"]

    case_map = {}
    for variant in report["variants"]:
        case_map[variant["variant"]] = {row["case_id"]: row for row in variant["rows"]}

    assert case_map["write_time_only"]["temporal_retrospective"]["passed"] is False
    assert case_map["atom_only_no_dossier"]["longitudinal_visa"]["passed"] is False
    assert case_map["full_v17"]["longitudinal_visa"]["passed"] is True
