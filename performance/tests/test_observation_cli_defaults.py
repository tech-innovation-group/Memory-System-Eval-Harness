"""CLI defaults for the current EchoMem observation entry point."""

from pathlib import Path

from performance.targets.echomem.observation_run import build_parser


def test_observation_defaults_to_first_three_metrics() -> None:
    args = build_parser().parse_args(["--profiles", "profile.json", "--out-dir", "results"])
    assert args.metrics == "M1,M2,M3"


def test_full_wrapper_explicitly_selects_all_six_metrics() -> None:
    wrapper = Path("performance/targets/echomem/run_six_metrics.sh").read_text(encoding="utf-8")
    assert "full)\n    command+=(--metrics M1,M2,M3,M4,M5,M6)" in wrapper
