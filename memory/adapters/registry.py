"""Compatibility wrapper for the plugin-first memory backend facade."""

from __future__ import annotations

from typing import Any

from memory.plugins.service import available_backends
from memory.plugins.service import backend_contract
from memory.plugins.service import get_backend


def available_adapters() -> list[dict[str, Any]]:
    return available_backends()


def get_adapter(adapter_id: str) -> Any:
    return get_backend(adapter_id)


def adapter_contract(adapter_id: str) -> dict[str, Any]:
    return backend_contract(adapter_id)


__all__ = ["adapter_contract", "available_adapters", "get_adapter"]
