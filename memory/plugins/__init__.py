"""Pluggable memory backend registry and facade."""

from .base import MemoryPlugin, PluginCapability, PluginConfig, PluginDescriptor, PluginTaskSpec
from .registry import available_plugins, get_plugin
from .service import available_backends, backend_contract, get_backend, plugin_service

__all__ = [
    "MemoryPlugin",
    "PluginCapability",
    "PluginConfig",
    "PluginDescriptor",
    "PluginTaskSpec",
    "available_backends",
    "available_plugins",
    "backend_contract",
    "get_backend",
    "get_plugin",
    "plugin_service",
]
