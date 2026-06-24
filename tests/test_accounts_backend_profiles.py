from __future__ import annotations

from pathlib import Path

from memory import accounts


def test_prepare_account_workspace_creates_openviking_layout(tmp_path: Path) -> None:
    paths = accounts.prepare_account_workspace(tmp_path, "acct", user_id="alice", agent_id="bot", backend="openviking")

    assert Path(paths["storage_root"]) == tmp_path / "viking" / "acct"
    assert Path(paths["session_root"]).is_dir()
    assert Path(paths["user_root"]) == tmp_path / "viking" / "acct" / "user" / "alice"
    assert Path(paths["user_memories"]).is_dir()
    assert Path(paths["agent_root"]) == tmp_path / "viking" / "acct" / "agent" / "bot"
    assert Path(paths["agent_memories"]).is_dir()


def test_prepare_account_workspace_creates_echomemory_layout(tmp_path: Path) -> None:
    paths = accounts.prepare_account_workspace(tmp_path, "acct", user_id="alice", agent_id="bot", backend="echomemory")

    assert Path(paths["storage_root"]) == tmp_path / "tenants" / "acct"
    assert Path(paths["session_root"]).is_dir()
    assert Path(paths["user_root"]) == tmp_path / "tenants" / "acct" / "users" / "alice"
    assert Path(paths["user_memories"]).is_dir()
    assert Path(paths["agent_root"]) == tmp_path / "tenants" / "acct" / "agents" / "bot"
    assert Path(paths["agent_memories"]).is_dir()


def test_clean_workspace_delegates_backend_specific_prefixes(tmp_path: Path) -> None:
    openviking_workspace = accounts.clean_workspace(tmp_path, "acct", timestamp="20260622_120000", backend="openviking")
    echomemory_workspace = accounts.clean_workspace(tmp_path, "acct", timestamp="20260622_120000", backend="echomemory")

    assert openviking_workspace.endswith("openviking_workspace_acct_20260622_120000")
    assert echomemory_workspace.endswith("echomem_workspace_acct_20260622_120000")


def test_account_view_reports_openviking_isolation(tmp_path: Path) -> None:
    workspace = tmp_path / "ov"
    accounts.prepare_account_workspace(workspace, "acct", backend="openviking")
    record = {
        "id": "acct",
        "created_at": "2026-06-22T10:00:00",
        "updated_at": "2026-06-22T10:00:00",
        "config": {
            "memoryBackend": "openviking",
            "ovWorkspace": str(workspace),
            "memoryWorkspace": str(workspace),
        },
    }

    view = accounts.account_view(record, include_secrets=False, include_counts=False)
    isolation = view["isolation"]

    assert isolation["backend"] == "openviking"
    assert isolation["storage_root"] == str(workspace / "viking" / "acct")
    assert isolation["viking_root"] == str(workspace / "viking" / "acct")
    assert isolation["workspace_exists"] is True
    assert isolation["session_root_exists"] is True
    assert isolation["user_root_exists"] is True
    assert isolation["agent_root_exists"] is True


def test_account_view_reports_echomemory_isolation(tmp_path: Path) -> None:
    workspace = tmp_path / "echo"
    accounts.prepare_account_workspace(workspace, "acct", backend="echomemory")
    record = {
        "id": "acct",
        "created_at": "2026-06-22T10:00:00",
        "updated_at": "2026-06-22T10:00:00",
        "config": {
            "memoryBackend": "echomemory",
            "ovWorkspace": str(workspace),
            "memoryWorkspace": str(workspace),
        },
    }

    view = accounts.account_view(record, include_secrets=False, include_counts=False)
    isolation = view["isolation"]

    assert isolation["backend"] == "echomemory"
    assert isolation["storage_root"] == str(workspace / "tenants" / "acct")
    assert isolation["viking_root"] == ""
    assert isolation["workspace_exists"] is True
    assert isolation["session_root_exists"] is True
    assert isolation["user_root_exists"] is True
    assert isolation["agent_root_exists"] is True


def test_public_state_marks_shared_workspace(tmp_path: Path) -> None:
    state_path = tmp_path / "accounts.json"
    shared_workspace = str(tmp_path / "shared")
    defaults = {"home": str(tmp_path), "account": "default", "memory_backend": "openviking"}
    state = {
        "active_account": "a1",
        "accounts": {
            "a1": {
                "id": "a1",
                "created_at": "2026-06-22T10:00:00",
                "updated_at": "2026-06-22T10:00:00",
                "config": {
                    "memoryBackend": "openviking",
                    "ovWorkspace": shared_workspace,
                    "memoryWorkspace": shared_workspace,
                },
            },
            "a2": {
                "id": "a2",
                "created_at": "2026-06-22T10:00:00",
                "updated_at": "2026-06-22T10:00:00",
                "config": {
                    "memoryBackend": "echomemory",
                    "ovWorkspace": shared_workspace,
                    "memoryWorkspace": shared_workspace,
                },
            },
        },
    }
    accounts.write_state(state_path, state)

    public = accounts.public_state(state_path, defaults)
    rows = {row["id"]: row for row in public["accounts"]}

    assert rows["a1"]["isolation"]["status"] == "shared_workspace"
    assert rows["a2"]["isolation"]["status"] == "shared_workspace"
    assert rows["a1"]["isolation"]["shared_with"] == ["a2"]
    assert rows["a2"]["isolation"]["shared_with"] == ["a1"]


def test_storage_root_normalizes_echomem_alias(tmp_path: Path) -> None:
    root = accounts.storage_root(tmp_path, "acct", "echomem")
    assert root == tmp_path / "tenants" / "acct"


def test_resolve_workspace_root_accepts_openviking_account_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "ov"
    account_root = workspace / "viking" / "acct"

    assert accounts.resolve_workspace_root(account_root, "acct", "openviking") == str(workspace)


def test_resolve_workspace_root_accepts_echomemory_account_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "echo"
    account_root = workspace / "tenants" / "acct"

    assert accounts.resolve_workspace_root(account_root, "acct", "echomemory") == str(workspace)
