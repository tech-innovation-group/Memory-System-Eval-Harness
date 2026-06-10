"""Memory backend plugin contract checks.

The web app talks to OpenViking and EchoMemory through this plugin boundary.
These checks keep required LoCoMo/chat methods explicit before a long run starts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


REQUIRED_CAPABILITIES: tuple[str, ...] = (
    "session_write",
    "commit_session",
    "relevant_memory",
    "import_integrity",
    "memory_browser",
    "locomo_task_build",
)

RECOMMENDED_CAPABILITIES: tuple[str, ...] = (
    "report_evidence",
)

REQUIRED_METHODS: tuple[str, ...] = (
    "normalize_config",
    "public_descriptor",
    "list_imported_memories",
    "import_integrity",
    "session_browser",
    "memory_timeline",
    "read_memory_file",
    "build_locomo_import_task",
    "build_locomo_qa_task",
)

OPTIONAL_METHODS_BY_BACKEND: dict[str, tuple[str, ...]] = {
    "openviking": (
        "probe",
        "discover_ports",
        "build_generic_qa_task",
        "agent_context",
        "agent_chat",
        "archive_chat",
    ),
    "echomemory": (
        "agent_context",
        "agent_chat",
        "archive_chat",
    ),
}


@dataclass(frozen=True)
class PluginContract:
    plugin_id: str
    status: str
    required_capabilities: tuple[str, ...]
    recommended_capabilities: tuple[str, ...]
    capabilities: tuple[str, ...]
    missing_required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    missing_recommended_capabilities: tuple[str, ...] = field(default_factory=tuple)
    required_methods: tuple[str, ...] = field(default_factory=tuple)
    missing_required_methods: tuple[str, ...] = field(default_factory=tuple)
    optional_methods: tuple[str, ...] = field(default_factory=tuple)
    missing_optional_methods: tuple[str, ...] = field(default_factory=tuple)

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["adapter_id"] = self.plugin_id
        payload["ok"] = self.status == "ok"
        return payload


def _capability_names(plugin: Any) -> tuple[str, ...]:
    descriptor = getattr(plugin, "descriptor", None)
    capabilities = getattr(descriptor, "capabilities", ()) or ()
    names: list[str] = []
    for item in capabilities:
        name = getattr(item, "name", "")
        if name:
            names.append(str(name))
    return tuple(names)


def _missing_methods(plugin: Any, methods: tuple[str, ...]) -> tuple[str, ...]:
    missing: list[str] = []
    for name in methods:
        if not callable(getattr(plugin, name, None)):
            missing.append(name)
    return tuple(missing)


def validate_plugin(plugin_id: str, plugin: Any) -> PluginContract:
    capabilities = _capability_names(plugin)
    cap_set = set(capabilities)
    required_missing = tuple(item for item in REQUIRED_CAPABILITIES if item not in cap_set)
    recommended_missing = tuple(item for item in RECOMMENDED_CAPABILITIES if item not in cap_set)
    required_method_missing = _missing_methods(plugin, REQUIRED_METHODS)
    optional_methods = OPTIONAL_METHODS_BY_BACKEND.get(plugin_id, ())
    optional_method_missing = _missing_methods(plugin, optional_methods)
    status = "ok" if not required_missing and not required_method_missing else "fail"
    if status == "ok" and (recommended_missing or optional_method_missing):
        status = "warn"
    return PluginContract(
        plugin_id=plugin_id,
        status=status,
        required_capabilities=REQUIRED_CAPABILITIES,
        recommended_capabilities=RECOMMENDED_CAPABILITIES,
        capabilities=capabilities,
        missing_required_capabilities=required_missing,
        missing_recommended_capabilities=recommended_missing,
        required_methods=REQUIRED_METHODS,
        missing_required_methods=required_method_missing,
        optional_methods=optional_methods,
        missing_optional_methods=optional_method_missing,
    )


__all__ = [
    "OPTIONAL_METHODS_BY_BACKEND",
    "PluginContract",
    "RECOMMENDED_CAPABILITIES",
    "REQUIRED_CAPABILITIES",
    "REQUIRED_METHODS",
    "validate_plugin",
]
