"""Compatibility wrapper for the plugin-first memory backend contract."""

from __future__ import annotations

from typing import Any

from memory.plugins.contract import (
    OPTIONAL_METHODS_BY_BACKEND,
    RECOMMENDED_CAPABILITIES,
    REQUIRED_CAPABILITIES,
    REQUIRED_METHODS,
    PluginContract as AdapterContract,
    validate_plugin,
)


def validate_adapter(adapter_id: str, adapter: Any) -> AdapterContract:
    return validate_plugin(adapter_id, adapter)


__all__ = [
    "AdapterContract",
    "OPTIONAL_METHODS_BY_BACKEND",
    "RECOMMENDED_CAPABILITIES",
    "REQUIRED_CAPABILITIES",
    "REQUIRED_METHODS",
    "validate_adapter",
]
