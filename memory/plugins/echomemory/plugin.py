from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..base import PluginCapability, PluginConfig, PluginDescriptor
from . import agent
from . import inspector
from . import tasks


class EchoMemoryPlugin:
    descriptor = PluginDescriptor(
        id="echomemory",
        name="EchoMemory",
        status="experimental",
        description="EchoMemory local SDK backend with account-scoped EchoFS storage, LoCoMo import and memory QA.",
        capabilities=(
            PluginCapability("session_write", "Create EchoMemory sessions and append messages."),
            PluginCapability("commit_session", "Trigger EchoMemory memory extraction and indexing."),
            PluginCapability("relevant_memory", "Retrieve EchoMemory context items for chat and benchmark QA."),
            PluginCapability("report_evidence", "Expose answer, context and evidence for HTML reports."),
            PluginCapability("import_integrity", "Verify EchoMemory LoCoMo sessions, commit artifacts and stored messages."),
            PluginCapability("memory_browser", "List account-scoped EchoMemory sessions and stored artifacts."),
            PluginCapability("agent_workbench", "Build readonly chat context, retrieve relevant memory and manually commit chat sessions."),
            PluginCapability("locomo_task_build", "Build LoCoMo EchoMemory import and strict memory QA task commands."),
        ),
    )

    def normalize_config(self, payload: dict[str, Any]) -> PluginConfig:
        return PluginConfig(
            account=str(payload.get("account") or "default").strip() or "default",
            workspace=str(payload.get("workspace") or payload.get("echomemory_workspace") or "").strip(),
            base_url="local-sdk",
            api_key="",
            model=str(payload.get("answer_model") or payload.get("judge_model") or "").strip(),
            extra={
                "echomem_root": tasks.echomem_root_value(payload),
                "user_id": str(payload.get("user_id") or payload.get("em_user_id") or "default"),
                "agent_id": str(payload.get("agent_id") or payload.get("em_agent_id") or "default"),
            },
        )

    def public_descriptor(self) -> dict[str, Any]:
        return self.descriptor.public()

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

    def build_locomo_import_task(self, payload: dict[str, Any], run_dir: Path, root: Path, default_data: Path, safe_path):
        return tasks.build_echomemory_import_command(payload, run_dir, root, default_data, safe_path)

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
        return tasks.build_echomemory_qa_command(payload, run_dir, config, root, default_data, defaults, safe_path, resolve_judge_token)
