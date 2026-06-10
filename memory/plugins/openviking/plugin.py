from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import PluginCapability, PluginConfig, PluginDescriptor
from . import agent
from . import inspector
from . import runtime
from . import tasks


class OpenVikingPlugin:
    descriptor = PluginDescriptor(
        id="openviking",
        name="OpenViking",
        status="active",
        description="OpenViking commit_session, relevant-memory search, evidence capture and account-scoped workspace isolation.",
        capabilities=(
            PluginCapability("session_write", "Create sessions and append messages before commit_session."),
            PluginCapability("commit_session", "Trigger OpenViking memory extraction and indexing."),
            PluginCapability("relevant_memory", "Retrieve evidence for chat and benchmark QA."),
            PluginCapability("report_evidence", "Expose answer, token usage, context and evidence for HTML reports."),
            PluginCapability("import_integrity", "Verify imported LoCoMo sessions, archives and memory files."),
            PluginCapability("memory_browser", "List account-scoped OpenViking sessions and memory timeline items."),
            PluginCapability("runtime_probe", "Detect OpenViking service health and available local ports."),
            PluginCapability("agent_workbench", "Build readonly chat context, retrieve relevant memory and manually commit chat sessions."),
            PluginCapability("locomo_task_build", "Build LoCoMo OpenViking import and strict memory QA task commands."),
        ),
    )

    def normalize_config(self, payload: dict[str, Any]) -> PluginConfig:
        host = str(payload.get("host") or "127.0.0.1").strip()
        port = str(payload.get("port") or "19080").strip()
        return PluginConfig(
            account=str(payload.get("account") or "default").strip() or "default",
            workspace=str(payload.get("workspace") or "").strip(),
            base_url=str(payload.get("server_url") or f"http://{host}:{port}").strip(),
            api_key=str(payload.get("root_api_key") or "").strip(),
            model=str(payload.get("vlm_model") or payload.get("judge_model") or "").strip(),
            extra={
                "user_id": str(payload.get("ov_user_id") or "default"),
                "agent_id": str(payload.get("ov_agent_id") or "default"),
                "workspace_mode": str(payload.get("workspace_mode") or "manual"),
            },
        )

    def public_descriptor(self) -> dict[str, Any]:
        return self.descriptor.public()

    def probe(self, host: str, port: str, api_key: str = "") -> dict[str, Any]:
        return runtime.probe(host, port, api_key)

    def discover_ports(self, host: str = "127.0.0.1", ports: list[str] | None = None, api_key: str = "") -> dict[str, Any]:
        return runtime.discover_ports(host, ports, api_key)

    def workspace_for_run(self, payload: dict[str, Any], run_dir: Path, safe_path) -> Path | None:
        return runtime.workspace_for_run(payload, run_dir, safe_path)

    def make_runtime_config(
        self,
        payload: dict[str, Any],
        run_dir: Path,
        base_config: Path,
        memory_templates_dir: Path,
    ) -> Path:
        return runtime.make_runtime_config(payload, run_dir, base_config, memory_templates_dir)

    def restart_for_workspace(
        self,
        payload: dict[str, Any],
        run_dir: Path,
        config_path: Path,
        *,
        safe_path,
        openviking_python: Path,
        memory_templates_dir: Path,
    ) -> dict[str, Any]:
        return runtime.restart_for_workspace(
            payload,
            run_dir,
            config_path,
            safe_path=safe_path,
            openviking_python=openviking_python,
            memory_templates_dir=memory_templates_dir,
        )

    def list_imported_memories(self, workspace: Path, account: str, output_dir: Path, limit: int = 80, sample: str = "") -> dict[str, Any]:
        return inspector.list_imported_memories(workspace, account, output_dir, limit, sample)

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
        return inspector.import_integrity(workspace, account, output_dir, data_path, sample, summary_path, user_id)

    def session_browser(self, workspace: Path, account: str, sample: str = "", limit: int = 120) -> dict[str, Any]:
        return inspector.session_browser(workspace, account, sample, limit)

    def memory_timeline(self, workspace: Path, account: str, user_id: str = "default", query: str = "", limit: int = 200) -> dict[str, Any]:
        return inspector.memory_timeline(workspace, account, user_id, query, limit)

    def read_memory_file(self, path: Path) -> dict[str, Any]:
        return inspector.read_memory_file(path)

    def agent_context(self, payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        return agent.build_context_preview(payload, defaults)

    def agent_chat(self, payload: dict[str, Any], defaults: dict[str, Any], config_path: Path) -> dict[str, Any]:
        return agent.chat(payload, defaults, config_path)

    def archive_chat(self, payload: dict[str, Any], defaults: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        return agent.archive_chat(payload, defaults, output_dir)

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
    ):
        return tasks.build_openviking_qa_command(payload, run_dir, config, root, default_data, defaults, safe_path, resolve_judge_token)

    def build_locomo_import_task(self, payload: dict[str, Any], run_dir: Path, root: Path, default_data: Path, safe_path):
        return tasks.build_openviking_import_command(payload, run_dir, root, default_data, safe_path)

    def build_generic_qa_task(
        self,
        payload: dict[str, Any],
        run_dir: Path,
        config: Path,
        root: Path,
        default_data: Path,
        defaults: dict[str, Any],
        safe_path,
        resolve_judge_token,
    ):
        return tasks.build_openviking_generic_qa_command(payload, run_dir, config, root, default_data, defaults, safe_path, resolve_judge_token)
