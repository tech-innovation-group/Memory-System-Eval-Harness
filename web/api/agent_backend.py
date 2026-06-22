from __future__ import annotations

from typing import Any


def handle_agent_backend_post(
    path: str,
    payload: dict[str, Any],
    *,
    send_json,
    load_defaults,
    default_config,
    default_output_dir,
    plugin_service,
    agent_backend_from_payload,
    unsupported_agent_backend,
) -> bool:
    if path not in {"/api/agent/chat", "/api/agent/context", "/api/agent/archive"}:
        return False

    backend = agent_backend_from_payload(payload)
    defaults = load_defaults()

    try:
        if path == "/api/agent/chat":
            result = plugin_service.agent_chat(backend, payload, defaults, default_config)
            if "error" in result:
                send_json(result, status=500)
            else:
                send_json(result)
            return True

        if path == "/api/agent/context":
            result = plugin_service.agent_context(backend, payload, defaults)
            send_json(result)
            return True

        if path == "/api/agent/archive":
            result = plugin_service.archive_chat(backend, payload, defaults, default_output_dir)
            send_json(result)
            return True
    except NotImplementedError:
        send_json(unsupported_agent_backend(backend, {
            "/api/agent/chat": "agent_chat",
            "/api/agent/context": "agent_context",
            "/api/agent/archive": "archive_chat",
        }[path]), status=501)
        return True
    except Exception as exc:
        send_json({"error": str(exc)}, status=500)
        return True

    return False
