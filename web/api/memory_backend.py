from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs


def handle_memory_backend_get(
    parsed,
    *,
    send_json,
    safe_path,
    load_defaults,
    normalize_memory_backend,
    plugin_service,
    backend_runtime_status,
    default_output_dir: Path,
    default_data: Path,
) -> bool:
    path = parsed.path
    if path not in {
        "/api/probe",
        "/api/discover-openviking",
        "/api/memory-imported",
        "/api/openviking-imported",
        "/api/memory-import-integrity",
        "/api/openviking-import-integrity",
        "/api/memory-sessions",
        "/api/openviking-sessions",
        "/api/memory-timeline",
        "/api/memory-file",
    }:
        return False

    qs = parse_qs(parsed.query)
    defaults = load_defaults()

    if path == "/api/probe":
        backend = normalize_memory_backend(qs.get("backend", ["openviking"])[0])
        host = qs.get("host", ["127.0.0.1"])[0] or "127.0.0.1"
        port = qs.get("port", ["19080"])[0] or "19080"
        api_key = qs.get("root_api_key", [""])[0]
        if backend == "openviking":
            data = plugin_service.probe("openviking", host, port, api_key)
            data["backend"] = "openviking"
            send_json(data)
            return True
        runtime = backend_runtime_status("echomemory", {
            "memoryBackend": "echomemory",
            "ovWorkspace": qs.get("workspace", [""])[0],
            "account": qs.get("account", ["default"])[0] or "default",
            "echomemRoot": qs.get("echomem_root", [""])[0] or qs.get("echomemRoot", [""])[0],
            "echomem_root": qs.get("echomem_root", [""])[0] or qs.get("echomemRoot", [""])[0],
            "memoryUserId": qs.get("user_id", ["default"])[0] or "default",
            "memoryAgentId": qs.get("agent_id", ["default"])[0] or "default",
        }, defaults)
        send_json({
            "ok": runtime.get("status") == "ok",
            "backend": "echomemory",
            "status": runtime.get("status") or "fail",
            "kind": runtime.get("kind") or "local-sdk",
            "url": runtime.get("root") or runtime.get("label") or "EchoMemory local SDK",
            "root": runtime.get("root") or "",
            "message": runtime.get("message") or "",
            "next_action": runtime.get("next_action") or "",
        })
        return True

    if path == "/api/discover-openviking":
        host = qs.get("host", ["127.0.0.1"])[0] or "127.0.0.1"
        api_key = qs.get("root_api_key", [""])[0]
        raw_ports = qs.get("ports", [""])[0]
        ports = [p for p in re.split(r"[,\s]+", raw_ports) if p] if raw_ports else None
        send_json(plugin_service.discover_ports("openviking", host, ports, api_key))
        return True

    workspace = safe_path(qs.get("workspace", [defaults.get("openviking_workspace") or defaults.get("workspace") or ""])[0])
    account = qs.get("account", [defaults.get("account") or "default"])[0] or "default"

    if path in {"/api/memory-imported", "/api/openviking-imported"}:
        sample = qs.get("sample", [""])[0]
        limit = int(qs.get("limit", ["80"])[0] or 80)
        backend = "openviking" if path == "/api/openviking-imported" else normalize_memory_backend(qs.get("backend", ["openviking"])[0])
        try:
            send_json(plugin_service.list_imported_memories(backend, workspace, account, default_output_dir, limit, sample))
        except Exception as exc:
            send_json({"error": str(exc)}, 400)
        return True

    if path in {"/api/memory-import-integrity", "/api/openviking-import-integrity"}:
        user_id = qs.get("user", [defaults.get("ov_user_id") or "default"])[0] or "default"
        sample = qs.get("sample", [""])[0]
        summary_text = qs.get("summary", [""])[0]
        summary_path = safe_path(summary_text) if summary_text else None
        backend = "openviking" if path == "/api/openviking-import-integrity" else normalize_memory_backend(qs.get("backend", ["openviking"])[0])
        try:
            send_json(plugin_service.import_integrity(backend, workspace, account, default_output_dir, default_data, sample, summary_path, user_id))
        except Exception as exc:
            send_json({"error": str(exc)}, 400)
        return True

    if path in {"/api/memory-sessions", "/api/openviking-sessions"}:
        sample = qs.get("sample", [""])[0]
        limit = int(qs.get("limit", ["120"])[0] or 120)
        backend = "openviking" if path == "/api/openviking-sessions" else normalize_memory_backend(qs.get("backend", ["openviking"])[0])
        try:
            send_json(plugin_service.session_browser(backend, workspace, account, sample, limit))
        except Exception as exc:
            send_json({"error": str(exc)}, 400)
        return True

    if path == "/api/memory-timeline":
        user_id = qs.get("user", [defaults.get("ov_user_id") or "default"])[0] or "default"
        query = qs.get("q", [""])[0]
        limit = int(qs.get("limit", ["200"])[0] or 200)
        backend = normalize_memory_backend(qs.get("backend", ["openviking"])[0])
        try:
            send_json(plugin_service.memory_timeline(backend, workspace, account, user_id, query, limit))
        except Exception as exc:
            send_json({"error": str(exc)}, 400)
        return True

    if path == "/api/memory-file":
        backend = normalize_memory_backend(qs.get("backend", ["openviking"])[0])
        file_path = safe_path(qs.get("path", [""])[0])
        try:
            send_json(plugin_service.read_memory_file(backend, file_path))
        except Exception as exc:
            send_json({"error": str(exc)}, 404)
        return True

    return False
