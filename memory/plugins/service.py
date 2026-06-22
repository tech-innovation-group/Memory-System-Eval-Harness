from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import available_plugins, get_plugin, plugin_contract


class PluginMethodMissingError(NotImplementedError):
    pass


@dataclass(frozen=True)
class MemoryPluginService:
    """Facade used by the web/server layer.

    The server should depend on this service instead of reaching into concrete
    ``openviking`` / ``echomemory`` plugin implementations directly.
    """

    def available_backends(self) -> list[dict[str, Any]]:
        return available_plugins()

    def get_backend(self, plugin_id: str):
        return get_plugin(plugin_id)

    def backend_contract(self, plugin_id: str) -> dict[str, Any]:
        return plugin_contract(plugin_id)

    def invoke(self, plugin_id: str, method: str, *args: Any, **kwargs: Any) -> Any:
        backend = self.get_backend(plugin_id)
        fn = getattr(backend, method, None)
        if not callable(fn):
            raise PluginMethodMissingError(f"backend {plugin_id!r} does not implement {method}()")
        return fn(*args, **kwargs)

    def normalize_config(self, plugin_id: str, payload: dict[str, Any]) -> Any:
        return self.invoke(plugin_id, "normalize_config", payload)

    def list_imported_memories(self, plugin_id: str, workspace: Path, account: str, output_dir: Path, limit: int = 80, sample: str = "") -> dict[str, Any]:
        return self.invoke(plugin_id, "list_imported_memories", workspace, account, output_dir, limit, sample)

    def import_integrity(
        self,
        plugin_id: str,
        workspace: Path,
        account: str,
        output_dir: Path,
        data_path: Path,
        sample: str = "",
        summary_path: Path | None = None,
        user_id: str = "default",
    ) -> dict[str, Any]:
        return self.invoke(plugin_id, "import_integrity", workspace, account, output_dir, data_path, sample, summary_path, user_id)

    def session_browser(self, plugin_id: str, workspace: Path, account: str, sample: str = "", limit: int = 120) -> dict[str, Any]:
        return self.invoke(plugin_id, "session_browser", workspace, account, sample, limit)

    def memory_timeline(self, plugin_id: str, workspace: Path, account: str, user_id: str = "default", query: str = "", limit: int = 200) -> dict[str, Any]:
        return self.invoke(plugin_id, "memory_timeline", workspace, account, user_id, query, limit)

    def read_memory_file(self, plugin_id: str, path: Path) -> dict[str, Any]:
        return self.invoke(plugin_id, "read_memory_file", path)

    def agent_context(self, plugin_id: str, payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        return self.invoke(plugin_id, "agent_context", payload, defaults)

    def agent_chat(self, plugin_id: str, payload: dict[str, Any], defaults: dict[str, Any], config_path: Path) -> dict[str, Any]:
        return self.invoke(plugin_id, "agent_chat", payload, defaults, config_path)

    def archive_chat(self, plugin_id: str, payload: dict[str, Any], defaults: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        return self.invoke(plugin_id, "archive_chat", payload, defaults, output_dir)

    def probe(self, plugin_id: str, host: str, port: str, api_key: str = "") -> dict[str, Any]:
        return self.invoke(plugin_id, "probe", host, port, api_key)

    def discover_ports(self, plugin_id: str, host: str = "127.0.0.1", ports: list[str] | None = None, api_key: str = "") -> dict[str, Any]:
        return self.invoke(plugin_id, "discover_ports", host, ports, api_key)

    def workspace_for_run(self, plugin_id: str, payload: dict[str, Any], run_dir: Path, safe_path) -> Path | None:
        return self.invoke(plugin_id, "workspace_for_run", payload, run_dir, safe_path)

    def make_runtime_config(
        self,
        plugin_id: str,
        payload: dict[str, Any],
        run_dir: Path,
        base_config: Path,
        memory_templates_dir: Path,
    ) -> Path:
        return self.invoke(plugin_id, "make_runtime_config", payload, run_dir, base_config, memory_templates_dir)

    def restart_for_workspace(
        self,
        plugin_id: str,
        payload: dict[str, Any],
        run_dir: Path,
        config_path: Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self.invoke(plugin_id, "restart_for_workspace", payload, run_dir, config_path, **kwargs)

    def build_locomo_import_task(self, plugin_id: str, payload: dict[str, Any], run_dir: Path, root: Path, default_data: Path, safe_path):
        return self.invoke(plugin_id, "build_locomo_import_task", payload, run_dir, root, default_data, safe_path)

    def build_locomo_qa_task(
        self,
        plugin_id: str,
        payload: dict[str, Any],
        run_dir: Path,
        config: Path,
        root: Path,
        default_data: Path,
        defaults: dict[str, Any],
        safe_path,
        resolve_judge_token,
    ):
        return self.invoke(plugin_id, "build_locomo_qa_task", payload, run_dir, config, root, default_data, defaults, safe_path, resolve_judge_token)

    def build_generic_qa_task(
        self,
        plugin_id: str,
        payload: dict[str, Any],
        run_dir: Path,
        config: Path,
        root: Path,
        default_data: Path,
        defaults: dict[str, Any],
        safe_path,
        resolve_judge_token,
    ):
        return self.invoke(plugin_id, "build_generic_qa_task", payload, run_dir, config, root, default_data, defaults, safe_path, resolve_judge_token)


plugin_service = MemoryPluginService()


def available_backends() -> list[dict[str, Any]]:
    return plugin_service.available_backends()


def get_backend(plugin_id: str):
    return plugin_service.get_backend(plugin_id)


def backend_contract(plugin_id: str) -> dict[str, Any]:
    return plugin_service.backend_contract(plugin_id)


__all__ = [
    "MemoryPluginService",
    "PluginMethodMissingError",
    "available_backends",
    "backend_contract",
    "get_backend",
    "plugin_service",
]
