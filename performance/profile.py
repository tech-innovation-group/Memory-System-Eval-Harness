"""Load profile: the configurable half of a load run.

A profile is YAML data describing the target, the load shape (worker
count, duration, per-task mix and arrival), optional tenant identities
and free-form ``params`` consumed by scenario code.  Scenario semantics
never live here — only numbers and knobs; the ``params`` section is the
single escape hatch for scenario-specific configuration.

``${ENV:-default}`` inside string values is expanded from environment
variables at load time.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_KNOWN_TOP_LEVEL = {"name", "description", "target", "load", "tenants", "params"}


@dataclass
class TargetSpec:
    base_url: str = "http://127.0.0.1:8010"
    headers: dict[str, str] = field(default_factory=dict)
    read_timeout_s: float = 30.0


@dataclass
class ArrivalSpec:
    model: str = "none"  # none | fixed_rps | poisson
    rps: float = 0.0
    ramp_s: float = 0.0
    scope: str = "global"  # global | per_tenant; rps applies to this scope
    start_s: float = 0.0
    end_s: float | None = None


@dataclass
class LoadSpec:
    workers: int = 8
    duration_s: float = 60.0
    mix: dict[str, int] | None = None  # task name -> weight; None = equal weight
    arrival: dict[str, ArrivalSpec] = field(default_factory=dict)  # per task name


@dataclass
class TenantSpec:
    name: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Profile:
    name: str
    description: str = ""
    target: TargetSpec = field(default_factory=TargetSpec)
    load: LoadSpec = field(default_factory=LoadSpec)
    tenants: list[TenantSpec] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


class ProfileError(ValueError):
    """Invalid or unloadable profile."""


def load_profile(source: str | dict[str, Any] | None = None) -> Profile:
    """Load and validate a profile from a YAML file, a dict, or defaults.

    ``None`` yields a default profile (localhost, 8 workers, 60s) whose
    name is filled in by the caller via :func:`with_name`.
    """
    if source is None:
        return Profile(name="default")
    data = source if isinstance(source, dict) else _load_yaml_file(source)
    _reject_unknown(data)
    return _build(data)


def with_name(profile: Profile, name: str) -> Profile:
    """Return a copy whose name falls back to ``name`` when unset."""
    if profile.name and profile.name != "default":
        return profile
    profile.name = name or "default"
    return profile


def _load_yaml_file(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML in profile {path}: {exc}") from exc
    except OSError as exc:
        raise ProfileError(f"cannot read profile {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"profile must be a YAML mapping: {path}")
    return data


def _reject_unknown(data: dict[str, Any]) -> None:
    unknown = [key for key in data if key not in _KNOWN_TOP_LEVEL]
    if unknown:
        raise ProfileError(f"unknown profile fields: {', '.join(sorted(unknown))}")


def _build(data: dict[str, Any]) -> Profile:
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProfileError("profile.name is required")
    target_raw = data.get("target") or {}
    if not isinstance(target_raw, dict):
        raise ProfileError("profile.target must be a mapping")
    base_url = _expand(str(target_raw.get("base_url") or "")).rstrip("/")
    if not base_url:
        raise ProfileError("profile.target.base_url is required")
    headers = _expand_headers(target_raw.get("headers"))
    read_timeout_s = _positive_float(target_raw.get("read_timeout_s"), 30.0, "target.read_timeout_s")

    load_raw = data.get("load") or {}
    if not isinstance(load_raw, dict):
        raise ProfileError("profile.load must be a mapping")
    workers = _positive_int(load_raw.get("workers"), 8, "load.workers")
    duration_s = _non_negative_float(load_raw.get("duration_s"), 60.0, "load.duration_s")
    mix = _parse_mix(load_raw.get("mix"))
    arrival = _parse_arrival(load_raw.get("arrival"))

    tenants = _parse_tenants(data.get("tenants"))
    params = data.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProfileError("profile.params must be a mapping")

    return Profile(
        name=name,
        description=str(data.get("description") or ""),
        target=TargetSpec(base_url=base_url, headers=headers, read_timeout_s=read_timeout_s),
        load=LoadSpec(workers=workers, duration_s=duration_s, mix=mix, arrival=arrival),
        tenants=tenants,
        params=params,
    )


def _parse_mix(raw: Any) -> dict[str, int] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        raise ProfileError("load.mix must be a non-empty mapping of task name -> weight")
    mix: dict[str, int] = {}
    for task_name, weight in raw.items():
        if not isinstance(task_name, str) or not task_name:
            raise ProfileError(f"load.mix has an empty task name: {raw}")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise ProfileError(f"load.mix['{task_name}'] must be a non-negative int")
        mix[task_name] = weight
    if sum(mix.values()) == 0:
        raise ProfileError("load.mix weights must not all be zero")
    return mix


def _parse_arrival(raw: Any) -> dict[str, ArrivalSpec]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ProfileError("load.arrival must be a mapping of task name -> spec")
    result: dict[str, ArrivalSpec] = {}
    for task_name, spec in raw.items():
        if not isinstance(task_name, str) or not task_name:
            raise ProfileError("load.arrival has an empty task name")
        if not isinstance(spec, dict):
            raise ProfileError(f"load.arrival['{task_name}'] must be a mapping")
        model = str(spec.get("model") or "none")
        if model not in ("none", "fixed_rps", "poisson"):
            raise ProfileError(
                f"load.arrival['{task_name}'].model must be none|fixed_rps|poisson, got {model}"
            )
        rps = _non_negative_float(spec.get("rps"), 0.0, "load.arrival.rps")
        ramp_s = _non_negative_float(spec.get("ramp_s"), 0.0, "load.arrival.ramp_s")
        scope = str(spec.get("scope", "global"))
        if scope not in ("global", "per_tenant"):
            raise ProfileError("load.arrival.scope must be global|per_tenant")
        start_s = _non_negative_float(spec.get("start_s"), 0.0, "load.arrival.start_s")
        end_s = (_non_negative_float(spec["end_s"], 0, "load.arrival.end_s")
                 if spec.get("end_s") is not None else None)
        if end_s is not None and end_s <= start_s:
            raise ProfileError("load.arrival.end_s must be greater than start_s")
        if model != "none" and rps <= 0:
            raise ProfileError(f"load.arrival['{task_name}'].rps must be > 0 for {model}")
        if model == "none":
            rps = 0.0
        if model == "none" and (scope != "global" or start_s or end_s is not None):
            raise ProfileError("load.arrival scope/start_s require an arrival model")
        result[task_name] = ArrivalSpec(
            model=model, rps=rps, ramp_s=ramp_s, scope=scope, start_s=start_s, end_s=end_s,
        )
    return result


def _parse_tenants(raw: Any) -> list[TenantSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProfileError("profile.tenants must be a list")
    tenants: list[TenantSpec] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ProfileError(f"profile.tenants[{index}] must be a mapping")
        tenants.append(
            TenantSpec(
                name=str(item.get("name") or ""),
                headers=_expand_headers(item.get("headers")),
            )
        )
    return tenants


def _expand_headers(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ProfileError("headers must be a mapping")
    return {str(key): _expand(str(value)) for key, value in raw.items()}


def _positive_float(raw: Any, default: float, field_name: str) -> float:
    if raw is None:
        return default
    value = float(raw)
    if value <= 0:
        raise ProfileError(f"profile.{field_name} must be > 0")
    return value


def _non_negative_float(raw: Any, default: float, field_name: str) -> float:
    if raw is None:
        return default
    value = float(raw)
    if value < 0:
        raise ProfileError(f"profile.{field_name} must be >= 0")
    return value


def _positive_int(raw: Any, default: int, field_name: str) -> int:
    if raw is None:
        return default
    value = int(raw)
    if value < 1:
        raise ProfileError(f"profile.{field_name} must be >= 1")
    return value


def _expand(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        env_name, default = match.group(1), match.group(2)
        if env_name in os.environ:
            return os.environ[env_name]
        return default if default is not None else ""

    return _ENV_PATTERN.sub(replace, value)


def expand_env_in(data: Any) -> Any:
    """Recursively expand ``${ENV:-default}`` placeholders in string values."""
    if isinstance(data, str):
        return _expand(data)
    if isinstance(data, dict):
        return {key: expand_env_in(value) for key, value in data.items()}
    if isinstance(data, list):
        return [expand_env_in(item) for item in data]
    return data
