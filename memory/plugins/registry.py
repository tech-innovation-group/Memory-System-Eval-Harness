from __future__ import annotations

from typing import Any

from .contract import validate_plugin
from .echomemory import EchoMemoryPlugin
from .openviking import OpenVikingPlugin


_PLUGINS = {
    "openviking": OpenVikingPlugin(),
    "echomemory": EchoMemoryPlugin(),
}


def _with_contract(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload["kind"] = "memory_backend_plugin"
    payload["api_name"] = "backend"
    plugin_id = str(payload.get("id") or "")
    if plugin_id:
        payload["contract"] = validate_plugin(plugin_id, _PLUGINS[plugin_id]).public()
    return payload


def available_plugins() -> list[dict[str, Any]]:
    return [_with_contract(plugin.public_descriptor()) for plugin in _PLUGINS.values()]


def get_plugin(plugin_id: str):
    return _PLUGINS[plugin_id]


def plugin_contract(plugin_id: str) -> dict[str, Any]:
    return validate_plugin(plugin_id, _PLUGINS[plugin_id]).public()


available_memory_backends = available_plugins
get_memory_backend = get_plugin


__all__ = [
    "available_memory_backends",
    "available_plugins",
    "get_memory_backend",
    "get_plugin",
    "plugin_contract",
]
