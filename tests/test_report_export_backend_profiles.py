from __future__ import annotations

from pathlib import Path

from memory import report_export


def test_backend_display_name_uses_profile_mapping() -> None:
    assert report_export.backend_display_name("openviking") == "OpenViking"
    assert report_export.backend_display_name("echomemory") == "EchoMemory"
    assert report_export.backend_display_name("echomem") == "EchoMemory"
    assert report_export.backend_display_name("custom-backend") == "custom-backend"


def test_context_composition_openviking_uses_openviking_labels() -> None:
    summary = {"summary_json": {}}
    summary_json = {"openviking_tool_loop_enabled": "true", "openviking_tool_set": "native", "openviking_content_read_enabled": "true"}
    config = {
        "prompt_mode": "vikingboat_compat",
        "top_k": "30",
        "openviking_tool_loop": "false",
        "openviking_tool_set": "fallback",
        "read_openviking_content": "false",
    }

    items = dict(report_export.context_composition(summary, summary_json, config, "openviking"))

    assert items["OpenViking tool loop"] == "true"
    assert items["OpenViking tool set"] == "native"
    assert items["OpenViking content read"] == "true"
    assert items["Prompt mode"] == "vikingboat_compat"


def test_context_composition_echomemory_uses_memory_labels() -> None:
    summary = {"summary_json": {}}
    summary_json = {
        "memory_tool_loop_enabled": "true",
        "memory_tool_set": "memory-search",
        "memory_content_read_enabled": "true",
    }
    config = {
        "prompt_mode": "vikingboat_compat",
        "top_k": "20",
        "memory_tool_loop_enabled": "false",
        "memory_tool_set": "fallback",
        "memory_content_read_enabled": "false",
    }

    items = dict(report_export.context_composition(summary, summary_json, config, "echomemory"))

    assert items["Memory tool loop"] == "true"
    assert items["Memory tool set"] == "memory-search"
    assert items["Memory content read"] == "true"
    assert items["Prompt mode"] == "vikingboat_compat"


def test_context_composition_echomemory_accepts_legacy_openviking_fallback_keys() -> None:
    summary = {"summary_json": {}}
    summary_json = {
        "openviking_tool_loop_enabled": "true",
        "openviking_tool_set": "legacy-tools",
        "openviking_content_read_enabled": "true",
    }
    config = {
        "memory_tool_loop_enabled": "false",
        "memory_tool_set": "unused",
        "read_openviking_content": "false",
    }

    items = dict(report_export.context_composition(summary, summary_json, config, "echomemory"))

    assert items["Memory tool loop"] == "true"
    assert items["Memory tool set"] == "legacy-tools"
    assert items["Memory content read"] == "true"


def test_import_integrity_unavailable_uses_backend_display_name() -> None:
    echomemory = report_export.import_integrity_unavailable("echomemory", "missing workspace", workspace="/tmp/echo")
    openviking = report_export.import_integrity_unavailable("openviking", "missing workspace", workspace="/tmp/ov")

    assert echomemory["memory_label"] == "EchoMemory"
    assert echomemory["backend"] == "echomemory"
    assert echomemory["reason"] == "missing workspace"

    assert openviking["memory_label"] == "OpenViking"
    assert openviking["backend"] == "openviking"
    assert openviking["reason"] == "missing workspace"


def test_latest_import_integrity_reports_missing_openviking_workspace(tmp_path: Path) -> None:
    result = report_export.latest_import_integrity(
        tmp_path / "runs" / "example",
        {"account": "acct"},
        backend="openviking",
    )

    assert result["backend"] == "openviking"
    assert result["memory_label"] == "OpenViking"
    assert "缺少 workspace 配置" in result["reason"]


def test_latest_import_integrity_reports_missing_echomemory_workspace(tmp_path: Path) -> None:
    result = report_export.latest_import_integrity(
        tmp_path / "runs" / "example",
        {"account": "acct"},
        backend="echomemory",
        data_path=tmp_path / "dataset.json",
    )

    assert result["backend"] == "echomemory"
    assert result["memory_label"] == "EchoMemory"
    assert "缺少 workspace 配置" in result["reason"]


def test_report_backend_prefers_detected_backend_names() -> None:
    backend = report_export.report_backend(
        {"kind": "locomo_echomemory_qa"},
        {"backend": "echomemory"},
        {},
        {},
    )
    assert backend == "echomemory"

    backend = report_export.report_backend(
        {"kind": "openviking_qa"},
        {"backend": "openviking"},
        {},
        {},
    )
    assert backend == "openviking"


def test_backend_display_name_is_used_for_unknown_empty_backend() -> None:
    assert report_export.backend_display_name(None) == "未知后端"

