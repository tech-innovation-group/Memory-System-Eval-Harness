"""Profile loading, validation and environment expansion."""

from __future__ import annotations

import pytest

from performance.profile import (
    ArrivalSpec,
    LoadSpec,
    Profile,
    ProfileError,
    TargetSpec,
    expand_env_in,
    load_profile,
    with_name,
)


def test_default_profile():
    profile = load_profile(None)
    assert profile.name == "default"
    assert profile.load.workers == 8
    assert profile.load.duration_s == 60.0
    assert profile.tenants == []


def test_minimal_dict():
    profile = load_profile({"name": "p", "target": {"base_url": "http://x:1"}})
    assert profile.target.base_url == "http://x:1"
    assert profile.target.read_timeout_s == 30.0
    assert profile.load.workers == 8
    assert profile.load.mix is None


def test_full_dict():
    profile = load_profile(
        {
            "name": "p",
            "description": "d",
            "target": {
                "base_url": "http://x:1",
                "headers": {"X-Auth-Key": "k"},
                "read_timeout_s": 5,
            },
            "load": {
                "workers": 9,
                "duration_s": 120,
                "mix": {"read": 8, "write": 1},
                "arrival": {"read": {"model": "fixed_rps", "rps": 10, "ramp_s": 2}},
            },
            "tenants": [{"name": "t0", "headers": {"X-Auth-Key": "k0"}}],
            "params": {"top_k": 5, "nested": {"a": 1}},
        }
    )
    assert profile.description == "d"
    assert profile.target.headers == {"X-Auth-Key": "k"}
    assert profile.load.mix == {"read": 8, "write": 1}
    assert profile.load.arrival == {"read": ArrivalSpec("fixed_rps", 10.0, 2.0)}
    assert profile.tenants[0].headers == {"X-Auth-Key": "k0"}
    assert profile.params == {"top_k": 5, "nested": {"a": 1}}


def test_env_expansion(monkeypatch):
    monkeypatch.setenv("ECHOMEM_AUTH_KEY", "secret")
    profile = load_profile(
        {
            "name": "p",
            "target": {
                "base_url": "${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}",
                "headers": {"X-Auth-Key": "${ECHOMEM_AUTH_KEY:-}"},
            },
        }
    )
    assert profile.target.base_url == "http://127.0.0.1:8010"
    assert profile.target.headers["X-Auth-Key"] == "secret"


def test_env_expansion_default():
    profile = load_profile(
        {
            "name": "p",
            "target": {
                "base_url": "${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}",
            },
        }
    )
    assert profile.target.base_url == "http://127.0.0.1:8010"


def test_missing_name():
    with pytest.raises(ProfileError, match="name"):
        load_profile({"target": {"base_url": "http://x"}})


def test_missing_base_url():
    with pytest.raises(ProfileError, match="base_url"):
        load_profile({"name": "p", "target": {}})


def test_unknown_top_level_field():
    with pytest.raises(ProfileError, match="unknown profile fields"):
        load_profile({"name": "p", "target": {"base_url": "http://x"}, "bogus": 1})


def test_zero_workers():
    with pytest.raises(ProfileError, match="workers"):
        load_profile({"name": "p", "target": {"base_url": "http://x"}, "load": {"workers": 0}})


def test_bad_mix_weight():
    with pytest.raises(ProfileError, match="mix"):
        load_profile(
            {"name": "p", "target": {"base_url": "http://x"}, "load": {"mix": {"read": -1}}}
        )


def test_all_zero_mix():
    with pytest.raises(ProfileError, match="mix"):
        load_profile(
            {"name": "p", "target": {"base_url": "http://x"}, "load": {"mix": {"read": 0}}}
        )


def test_bad_arrival_model():
    with pytest.raises(ProfileError, match="model"):
        load_profile(
            {
                "name": "p",
                "target": {"base_url": "http://x"},
                "load": {"arrival": {"read": {"model": "burst"}}},
            }
        )


def test_fixed_rps_requires_rate():
    with pytest.raises(ProfileError, match="rps"):
        load_profile(
            {
                "name": "p",
                "target": {"base_url": "http://x"},
                "load": {"arrival": {"read": {"model": "fixed_rps"}}},
            }
        )


def test_per_tenant_arrival_weights_are_validated():
    profile = load_profile({
        "name": "p", "target": {"base_url": "http://x"},
        "load": {"arrival": {"read": {
            "model": "fixed_rps", "rps": 2, "scope": "per_tenant",
            "tenant_weights": [4, 2, 1],
        }}},
    })
    assert profile.load.arrival["read"].tenant_weights == (4.0, 2.0, 1.0)
    for weights in ([], [1, 0], [1, "bad"]):
        with pytest.raises(ProfileError, match="tenant_weights"):
            load_profile({
                "name": "p", "target": {"base_url": "http://x"},
                "load": {"arrival": {"read": {
                    "model": "fixed_rps", "rps": 2,
                    "scope": "per_tenant", "tenant_weights": weights,
                }}},
            })


def test_arrival_none_ignores_rate():
    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": "http://x"},
            "load": {"arrival": {"read": {"model": "none", "rps": 50}}},
        }
    )
    assert profile.load.arrival["read"] == ArrivalSpec("none", 0.0, 0.0)


def test_bad_tenants_entry():
    with pytest.raises(ProfileError, match="tenants"):
        load_profile(
            {"name": "p", "target": {"base_url": "http://x"}, "tenants": ["t0"]}
        )


def test_with_name_falls_back():
    profile = load_profile(None)
    assert with_name(profile, "scene_x").name == "scene_x"
    named = load_profile({"name": "p", "target": {"base_url": "http://x"}})
    assert with_name(named, "scene_x").name == "p"


def test_load_from_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOMEM_AUTH_KEY", "k")
    path = tmp_path / "p.yaml"
    path.write_text(
        "name: p\ntarget:\n  base_url: ${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}\n"
        "  headers:\n    X-Auth-Key: ${ECHOMEM_AUTH_KEY:-}\n",
        encoding="utf-8",
    )
    profile = load_profile(str(path))
    assert profile.name == "p"
    assert profile.target.headers["X-Auth-Key"] == "k"


def test_load_invalid_yaml(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(Exception):
        load_profile(str(path))


# -- expand_env_in（instance profile JSON 的递归展开） ----------------------


def test_expand_env_in_recursive(monkeypatch):
    monkeypatch.setenv("ECHOMEM_CONTAINER", "echomem-8u16g")
    monkeypatch.delenv("ECHOMEM_FAULT_CONTROL_URL", raising=False)
    data = {
        "name": "4U8G",
        "commit_recovery": {"container": "${ECHOMEM_CONTAINER:-echomem-4u8g}"},
        "fault_isolation": {"endpoint": "${ECHOMEM_FAULT_CONTROL_URL}"},
        "levels": ["${ECHOMEM_CONTAINER:-x}", "plain"],
        "kept": {"n": 3, "flag": True},
    }
    expanded = expand_env_in(data)
    assert expanded["commit_recovery"]["container"] == "echomem-8u16g"
    assert expanded["fault_isolation"]["endpoint"] == ""
    assert expanded["levels"] == ["echomem-8u16g", "plain"]
    assert expanded["kept"] == {"n": 3, "flag": True}
    # 原 dict 不被就地修改
    assert data["commit_recovery"]["container"] == "${ECHOMEM_CONTAINER:-echomem-4u8g}"
