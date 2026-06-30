from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BACKEND = "openviking"


def _slug_account(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")
    return text or "default"


@dataclass(frozen=True)
class BackendProfile:
    id: str
    display_name: str
    workspace_prefix: str
    workspace_layout: str
    runtime_label: str
    backend_url_label: str
    tool_loop_label: str
    tool_set_label: str
    content_read_label: str
    backend_note: str
    import_integrity_note: str
    backend_url_config_keys: tuple[str, ...]
    tool_loop_summary_keys: tuple[str, ...]
    tool_loop_config_keys: tuple[str, ...]
    tool_set_summary_keys: tuple[str, ...]
    tool_set_config_keys: tuple[str, ...]
    content_read_summary_keys: tuple[str, ...]
    content_read_config_keys: tuple[str, ...]
    session_dir_name: str
    user_container_name: str
    agent_container_name: str
    memory_root_segments: tuple[str, ...]
    atom_root_segments: tuple[str, ...]

    def clean_workspace(self, home: str | Path, account: str, timestamp: str) -> str:
        return str(Path(home).expanduser() / f"{self.workspace_prefix}_{_slug_account(account)}_{timestamp}")

    def storage_root(self, workspace: str | Path, account: str) -> Path:
        workspace_path = Path(workspace).expanduser()
        account_id = _slug_account(account)
        if self.id == "echomemory":
            primary_candidates = [
                workspace_path / "tenants" / account_id,
                workspace_path / account_id / account_id,
                workspace_path / account_id,
            ]
            for candidate in primary_candidates:
                if (candidate / "memory").exists() or (candidate / "sessions").exists():
                    return candidate
            if (workspace_path / "memory").exists() or (workspace_path / "sessions").exists():
                return workspace_path
            for candidate in primary_candidates:
                if candidate.exists():
                    return candidate
            return primary_candidates[0]
        return workspace_path / "viking" / account_id

    def session_root(self, account_root: Path) -> Path:
        return account_root / self.session_dir_name

    def user_root(self, account_root: Path, user_id: str = "default") -> Path:
        return account_root / self.user_container_name / user_id

    def user_memories(self, account_root: Path, user_id: str = "default") -> Path:
        return self.user_root(account_root, user_id) / "memories"

    def agent_root(self, account_root: Path, agent_id: str = "default") -> Path:
        return account_root / self.agent_container_name / agent_id

    def agent_memories(self, account_root: Path, agent_id: str = "default") -> Path:
        return self.agent_root(account_root, agent_id) / "memories"

    def memory_root(self, account_root: Path, user_id: str = "default") -> Path | None:
        if not self.memory_root_segments:
            return None
        resolved: list[str] = []
        for item in self.memory_root_segments:
            if item == "{user_id}":
                resolved.append(user_id)
            else:
                resolved.append(item)
        return account_root.joinpath(*resolved)

    def atom_root(self, account_root: Path, user_id: str = "default") -> Path | None:
        if not self.atom_root_segments:
            return None
        resolved: list[str] = []
        for item in self.atom_root_segments:
            if item == "{user_id}":
                resolved.append(user_id)
            else:
                resolved.append(item)
        return account_root.joinpath(*resolved)

    def prepare_paths(self, workspace: str | Path, account: str, user_id: str = "default", agent_id: str = "default") -> dict[str, str]:
        workspace_path = Path(workspace).expanduser()
        account_root = self.storage_root(workspace_path, account)
        paths = {
            "workspace": str(workspace_path),
            "storage_root": str(account_root),
            "account_root": str(account_root),
            "session_root": str(self.session_root(account_root)),
            "user_root": str(self.user_root(account_root, user_id)),
            "user_memories": str(self.user_memories(account_root, user_id)),
            "agent_root": str(self.agent_root(account_root, agent_id)),
            "agent_memories": str(self.agent_memories(account_root, agent_id)),
        }
        return paths


OPENVIKING_PROFILE = BackendProfile(
    id="openviking",
    display_name="OpenViking",
    workspace_prefix="openviking_workspace",
    workspace_layout="workspace/viking/<account>",
    runtime_label="OpenViking 服务",
    backend_url_label="OpenViking URL",
    tool_loop_label="OpenViking tool loop",
    tool_set_label="OpenViking tool set",
    content_read_label="OpenViking content read",
    backend_note="本次使用 OpenViking 作为记忆后端，Agent 调用 OpenViking 兼容工具读取 user/agent memory。",
    import_integrity_note="自动匹配同一 workspace/account 的最近一次 OpenViking import，检查 LoCoMo conversation 是否完整提交并 commit。",
    backend_url_config_keys=("openviking_url", "server_url"),
    tool_loop_summary_keys=("openviking_tool_loop_enabled",),
    tool_loop_config_keys=("openviking_tool_loop",),
    tool_set_summary_keys=("openviking_tool_set",),
    tool_set_config_keys=("openviking_tool_set",),
    content_read_summary_keys=("openviking_content_read_enabled",),
    content_read_config_keys=("read_openviking_content",),
    session_dir_name="session",
    user_container_name="user",
    agent_container_name="agent",
    memory_root_segments=("user", "{user_id}", "memories"),
    atom_root_segments=(),
)


ECHOMEMORY_PROFILE = BackendProfile(
    id="echomemory",
    display_name="EchoMemory",
    workspace_prefix="echomem",
    workspace_layout="workspace/tenants/<account> (compatible with workspace/<account>/<account>)",
    runtime_label="EchoMemory 本地 SDK",
    backend_url_label="EchoMemory SDK Root",
    tool_loop_label="Memory tool loop",
    tool_set_label="Memory tool set",
    content_read_label="Memory content read",
    backend_note="本次使用 EchoMemory 作为记忆后端，Agent 调用 memory_search / memory_read_many 等 memory_* 工具读取 EchoMemory 记忆；不是 OpenViking 记忆结果。",
    import_integrity_note="自动匹配同一 workspace/account 的最近一次 EchoMemory import，检查 LoCoMo conversation 是否完整写入并可检索。",
    backend_url_config_keys=("echomem_root", "echomemory_root"),
    tool_loop_summary_keys=("memory_tool_loop_enabled", "openviking_tool_loop_enabled"),
    tool_loop_config_keys=("memory_tool_loop_enabled", "openviking_tool_loop"),
    tool_set_summary_keys=("memory_tool_set", "openviking_tool_set"),
    tool_set_config_keys=("memory_tool_set", "openviking_tool_set"),
    content_read_summary_keys=("memory_content_read_enabled", "openviking_content_read_enabled"),
    content_read_config_keys=("memory_content_read_enabled", "read_openviking_content"),
    session_dir_name="sessions",
    user_container_name="users",
    agent_container_name="agents",
    memory_root_segments=("memory",),
    atom_root_segments=("memory", ".structured", "atoms"),
)


BACKEND_PROFILES = {
    OPENVIKING_PROFILE.id: OPENVIKING_PROFILE,
    ECHOMEMORY_PROFILE.id: ECHOMEMORY_PROFILE,
}


def normalize_backend_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"echomem", "echomemory"}:
        return "echomemory"
    return "openviking"


def backend_profile(value: Any) -> BackendProfile:
    return BACKEND_PROFILES[normalize_backend_id(value)]
