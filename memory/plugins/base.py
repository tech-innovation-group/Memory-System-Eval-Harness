from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class PluginCapability:
    name: str
    description: str


@dataclass(frozen=True)
class PluginDescriptor:
    id: str
    name: str
    status: str
    description: str
    config_scope: str = "account"
    capabilities: tuple[PluginCapability, ...] = field(default_factory=tuple)

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = [asdict(item) for item in self.capabilities]
        return payload


@dataclass
class PluginConfig:
    account: str = "default"
    workspace: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginTaskSpec:
    command: list[str]
    output_file: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryPlugin(Protocol):
    descriptor: PluginDescriptor

    def normalize_config(self, payload: dict[str, Any]) -> PluginConfig:
        ...

    def public_descriptor(self) -> dict[str, Any]:
        ...

    def list_imported_memories(self, workspace: Path, account: str, output_dir: Path, limit: int = 80, sample: str = "") -> dict[str, Any]:
        ...

    def import_integrity(
        self,
        workspace: Path,
        account: str,
        output_dir: Path,
        data_path: Path,
        sample: str = "",
        summary_path: Path | None = None,
        user_id: str = "default",
    ) -> dict[str, Any]:
        ...

    def session_browser(self, workspace: Path, account: str, sample: str = "", limit: int = 120) -> dict[str, Any]:
        ...

    def memory_timeline(self, workspace: Path, account: str, user_id: str = "default", query: str = "", limit: int = 200) -> dict[str, Any]:
        ...

    def read_memory_file(self, path: Path) -> dict[str, Any]:
        ...

    def build_locomo_import_task(self, payload: dict[str, Any], run_dir: Path, root: Path, default_data: Path, safe_path) -> PluginTaskSpec:
        ...

    def build_locomo_qa_task(
        self,
        payload: dict[str, Any],
        run_dir: Path,
        config: Path,
        root: Path,
        default_data: Path,
        defaults: dict[str, Any],
        safe_path,
        resolve_judge_token,
    ) -> PluginTaskSpec:
        ...
