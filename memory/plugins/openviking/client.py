from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def headers(account: str = "default", user_id: str = "default", agent_id: str = "default", api_key: str = "") -> dict[str, str]:
    value = {
        "Content-Type": "application/json",
        "X-OpenViking-Account": account or "default",
        "X-OpenViking-User": user_id or "default",
        "X-OpenViking-Agent": agent_id or "default",
    }
    if api_key:
        value["X-API-Key"] = api_key
        value["Authorization"] = f"Bearer {api_key}"
    return value


def request(
    base_url: str,
    api_key: str,
    account: str,
    user_id: str,
    agent_id: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(base_url.rstrip("/") + path, data=body, headers=headers(account, user_id, agent_id, api_key), method=method)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenViking HTTP {exc.code} {path}: {detail[:1000]}") from exc
    except URLError as exc:
        raise RuntimeError(f"cannot connect OpenViking {base_url}: {exc}") from exc
    data = json.loads(text) if text else {}
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(json.dumps(data.get("error") or data, ensure_ascii=False)[:1000])
    inner = data.get("result") if isinstance(data, dict) else None
    return inner if isinstance(inner, dict) else data


def wait_commit_task(
    base_url: str,
    api_key: str,
    account: str,
    user_id: str,
    agent_id: str,
    task_id: str,
    timeout_s: int,
) -> dict[str, Any]:
    deadline = time.time() + max(1, timeout_s)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = request(base_url, api_key, account, user_id, agent_id, "GET", f"/api/v1/tasks/{task_id}")
        status = str(last.get("status") or "").lower()
        if status in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(2)
    last["status"] = last.get("status") or "timeout"
    return last


def find(
    base_url: str,
    query: str,
    account: str,
    user_id: str,
    agent_id: str,
    api_key: str = "",
    limit: int = 30,
    target_uri: str = "viking://user/memories/",
    score_threshold: float = 0.1,
) -> dict[str, Any]:
    payload = {
        "query": query,
        "target_uri": target_uri,
        "limit": limit,
        "score_threshold": score_threshold,
    }
    req = Request(
        f"{base_url.rstrip('/')}/api/v1/search/find",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers(account, user_id, agent_id, api_key),
        method="POST",
    )
    with urlopen(req, timeout=20) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    if raw.get("status") == "error":
        raise RuntimeError(json.dumps(raw, ensure_ascii=False)[:1000])
    result = raw.get("result", raw)
    if isinstance(result, list):
        items = result
    else:
        items = (
            result.get("items")
            or result.get("results")
            or result.get("hits")
            or result.get("memories")
            or result.get("resources")
            or []
        )
        if isinstance(result.get("memories"), list) and isinstance(result.get("resources"), list):
            items = result["memories"] + result["resources"]
    return {"raw": result, "items": items[:limit] if isinstance(items, list) else []}


def build_paths(workspace: Path, account: str, user_id: str, agent_id: str, session_id: str, path_source: str) -> dict[str, str]:
    workspace_path = workspace.expanduser()
    account_dir = workspace_path / "viking" / account
    return {
        "workspace": str(workspace_path),
        "viking_dir": str(workspace_path / "viking"),
        "account_dir": str(account_dir),
        "session_dir": str(account_dir / "session" / session_id),
        "user_dir": str(account_dir / "user" / user_id),
        "user_memories_dir": str(account_dir / "user" / user_id / "memories"),
        "agent_dir": str(account_dir / "agent" / agent_id),
        "agent_memories_dir": str(account_dir / "agent" / agent_id / "memories"),
        "path_source": path_source,
    }
