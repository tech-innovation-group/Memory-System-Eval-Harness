"""Compatibility wrapper for the plugin-first memory backend registry."""

from __future__ import annotations

from typing import Any

from memory.plugins.registry import available_plugins
from memory.plugins.registry import get_plugin
from memory.plugins.registry import plugin_contract


def available_adapters() -> list[dict[str, Any]]:
    return available_plugins()


def get_adapter(adapter_id: str) -> Any:
    return get_plugin(adapter_id)


def adapter_contract(adapter_id: str) -> dict[str, Any]:
    return plugin_contract(adapter_id)


__all__ = ["adapter_contract", "available_adapters", "get_adapter"]
