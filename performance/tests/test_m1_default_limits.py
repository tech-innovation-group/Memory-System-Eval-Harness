from performance.targets.echomem.observation_run import (
    DEFAULT_M1_CONCURRENCY_LEVELS,
    DEFAULT_M1_LEVELS,
    _m1_levels,
)


def test_m1_defaults_stop_at_32() -> None:
    assert DEFAULT_M1_LEVELS == [1, 2, 4, 8, 16, 32]


def test_m1_profile_can_explicitly_extend_to_64_and_128() -> None:
    profile = {"m1_tenant_levels": [1, 2, 4, 8, 16, 32, 64, 128]}
    assert _m1_levels(profile, "m1_tenant_levels", DEFAULT_M1_LEVELS)[-2:] == [64, 128]


def test_default_m1_concurrency_levels_are_one_eight_sixteen_sixty_four() -> None:
    assert DEFAULT_M1_CONCURRENCY_LEVELS == [1, 8, 16, 64]
