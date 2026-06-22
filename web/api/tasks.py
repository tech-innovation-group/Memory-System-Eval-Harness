from __future__ import annotations

import re
from typing import Any


_TASK_STOP_RE = re.compile(r"^/api/tasks/([^/]+)/stop$")


def handle_task_post(
    path: str,
    payload: dict[str, Any],
    *,
    send_json,
    create_task,
    validate_payload,
    stop_all_tasks,
    stop_task_by_id,
    duplicate_error_cls,
    conflict_error_cls,
) -> bool:
    if path == "/api/tasks":
        try:
            task = create_task(payload.get("kind", "local_agent"), payload)
            send_json(task.public(), 201)
        except (duplicate_error_cls, conflict_error_cls) as exc:
            send_json({"error": str(exc), "task": exc.task.public()}, 409)
        except Exception as exc:
            send_json({"error": str(exc)}, 400)
        return True

    if path == "/api/validate":
        try:
            send_json(validate_payload(payload))
        except Exception as exc:
            send_json({"error": str(exc)}, 400)
        return True

    if path == "/api/tasks/stop-all":
        send_json(stop_all_tasks())
        return True

    match = _TASK_STOP_RE.match(path)
    if match:
        task_id = match.group(1)
        response, status = stop_task_by_id(task_id)
        send_json(response, status)
        return True

    return False
