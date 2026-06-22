from __future__ import annotations

from pathlib import Path

from memory.backend_profiles import (
    ECHOMEMORY_PROFILE,
    OPENVIKING_PROFILE,
    backend_profile,
    normalize_backend_id,
)


def test_normalize_backend_id_maps_aliases() -> None:
    assert normalize_backend_id("openviking") == "openviking"
    assert normalize_backend_id("echomemory") == "echomemory"
    assert normalize_backend_id("echomem") == "echomemory"
    assert normalize_backend_id("something-unknown") == "openviking"
    assert normalize_backend_id("") == "openviking"


def test_backend_profile_returns_expected_profiles() -> None:
    assert backend_profile("openviking") is OPENVIKING_PROFILE
    assert backend_profile("echomemory") is ECHOMEMORY_PROFILE
    assert backend_profile("echomem") is ECHOMEMORY_PROFILE


def test_clean_workspace_uses_backend_specific_prefixes() -> None:
    stamp = "20260622_120000"
    openviking_workspace = OPENVIKING_PROFILE.clean_workspace("/tmp", "alpha team", stamp)
    echomemory_workspace = ECHOMEMORY_PROFILE.clean_workspace("/tmp", "alpha team", stamp)

    assert openviking_workspace.endswith("/openviking_workspace_alpha-team_20260622_120000")
    assert echomemory_workspace.endswith("/echomem_workspace_alpha-team_20260622_120000")


def test_openviking_storage_root_layout() -> None:
    root = OPENVIKING_PROFILE.storage_root("/tmp/workspace", "acct.demo")
    assert root == Path("/tmp/workspace/viking/acct.demo")


def test_echomemory_storage_root_layout() -> None:
    root = ECHOMEMORY_PROFILE.storage_root("/tmp/workspace", "acct.demo")
    assert root == Path("/tmp/workspace/acct.demo/acct.demo")


def test_openviking_prepare_paths_uses_singular_directories() -> None:
    paths = OPENVIKING_PROFILE.prepare_paths("/tmp/workspace", "acct", user_id="u1", agent_id="a1")

    assert paths["storage_root"] == "/tmp/workspace/viking/acct"
    assert paths["session_root"] == "/tmp/workspace/viking/acct/session"
    assert paths["user_root"] == "/tmp/workspace/viking/acct/user/u1"
    assert paths["user_memories"] == "/tmp/workspace/viking/acct/user/u1/memories"
    assert paths["agent_root"] == "/tmp/workspace/viking/acct/agent/a1"
    assert paths["agent_memories"] == "/tmp/workspace/viking/acct/agent/a1/memories"


def test_echomemory_prepare_paths_uses_plural_directories() -> None:
    paths = ECHOMEMORY_PROFILE.prepare_paths("/tmp/workspace", "acct", user_id="u1", agent_id="a1")

    assert paths["storage_root"] == "/tmp/workspace/acct/acct"
    assert paths["session_root"] == "/tmp/workspace/acct/acct/sessions"
    assert paths["user_root"] == "/tmp/workspace/acct/acct/users/u1"
    assert paths["user_memories"] == "/tmp/workspace/acct/acct/users/u1/memories"
    assert paths["agent_root"] == "/tmp/workspace/acct/acct/agents/a1"
    assert paths["agent_memories"] == "/tmp/workspace/acct/acct/agents/a1/memories"


def test_memory_root_helpers_for_openviking_and_echomemory() -> None:
    openviking_root = Path("/tmp/workspace/viking/acct")
    echomemory_root = Path("/tmp/workspace/acct/acct")

    assert OPENVIKING_PROFILE.memory_root(openviking_root, "u1") == openviking_root / "user" / "u1" / "memories"
    assert OPENVIKING_PROFILE.atom_root(openviking_root, "u1") is None

    assert ECHOMEMORY_PROFILE.memory_root(echomemory_root, "u1") == echomemory_root / "memory"
    assert ECHOMEMORY_PROFILE.atom_root(echomemory_root, "u1") == echomemory_root / "memory" / ".structured" / "atoms"


def test_report_related_profile_fields_are_stable() -> None:
    assert OPENVIKING_PROFILE.backend_url_label == "OpenViking URL"
    assert OPENVIKING_PROFILE.tool_loop_label == "OpenViking tool loop"
    assert OPENVIKING_PROFILE.tool_set_label == "OpenViking tool set"
    assert OPENVIKING_PROFILE.content_read_label == "OpenViking content read"

    assert ECHOMEMORY_PROFILE.backend_url_label == "EchoMemory SDK Root"
    assert ECHOMEMORY_PROFILE.tool_loop_label == "Memory tool loop"
    assert ECHOMEMORY_PROFILE.tool_set_label == "Memory tool set"
    assert ECHOMEMORY_PROFILE.content_read_label == "Memory content read"


def test_runtime_labels_and_workspace_layouts_are_stable() -> None:
    assert OPENVIKING_PROFILE.runtime_label == "OpenViking 服务"
    assert OPENVIKING_PROFILE.workspace_layout == "workspace/viking/<account>"

    assert ECHOMEMORY_PROFILE.runtime_label == "EchoMemory 本地 SDK"
    assert ECHOMEMORY_PROFILE.workspace_layout == "workspace/<account>/<account>"
