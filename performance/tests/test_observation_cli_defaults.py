"""CLI defaults for the current EchoMem observation entry point."""

from pathlib import Path

import pytest

from performance.targets.echomem.observation_run import build_parser


def test_observation_defaults_to_first_three_metrics() -> None:
    args = build_parser().parse_args(["--profiles", "profile.json", "--out-dir", "results"])
    assert args.metrics == "M1,M2,M3"


def test_full_wrapper_explicitly_selects_all_six_metrics() -> None:
    wrapper = Path("performance/targets/echomem/run_six_metrics.sh").read_text(encoding="utf-8")
    assert "full)\n    command+=(--metrics M1,M2,M3,M4,M5,M6)" in wrapper


def test_observation_rejects_quick_mode() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "--profiles", "profile.json", "--out-dir", "results", "--quick",
        ])


def test_wrapper_does_not_offer_quick_mode() -> None:
    wrapper = Path("performance/targets/echomem/run_six_metrics.sh").read_text(encoding="utf-8")
    assert "{quick|" not in wrapper
    assert "--quick" not in wrapper


def test_formal_profile_example_continues_after_congestion() -> None:
    import json

    profile_path = Path("performance/targets/echomem/docs/six-metrics.profile.example.json")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["profiles"][0]["m1_continue_after_congestion"] is True
