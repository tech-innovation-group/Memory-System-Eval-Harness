"""Adapter-facing type aliases for memory backends."""

from __future__ import annotations

from memory.plugins.base import MemoryPlugin
from memory.plugins.base import PluginCapability
from memory.plugins.base import PluginConfig
from memory.plugins.base import PluginDescriptor
from memory.plugins.base import PluginTaskSpec

AdapterCapability = PluginCapability
AdapterDescriptor = PluginDescriptor
AdapterConfig = PluginConfig
AdapterTaskSpec = PluginTaskSpec
MemoryBackendAdapter = MemoryPlugin

__all__ = [
    "AdapterCapability",
    "AdapterDescriptor",
    "AdapterConfig",
    "AdapterTaskSpec",
    "MemoryBackendAdapter",
]
