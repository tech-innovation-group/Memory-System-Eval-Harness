"""Backward-compatible memory adapter API.

New code should import from ``memory.plugins``. This package keeps legacy
reports and scripts that still use ``memory.adapters`` working.
"""

from .base import (
    AdapterCapability,
    AdapterConfig,
    AdapterDescriptor,
    AdapterTaskSpec,
    MemoryBackendAdapter,
)
from .registry import adapter_contract, available_adapters, get_adapter

__all__ = [
    "AdapterCapability",
    "AdapterConfig",
    "AdapterDescriptor",
    "AdapterTaskSpec",
    "MemoryBackendAdapter",
    "adapter_contract",
    "available_adapters",
    "get_adapter",
]
