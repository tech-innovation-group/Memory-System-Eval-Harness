from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ACCOUNT = "default"
SECRET_MARKERS = ("key", "token", "password", "secret")
COUNTED_MEMORY_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml", ".csv"}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slug_account(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")
    return text or DEFAULT_ACCOUNT


def clean_workspace(home: str | Path, account: str, timestamp: str | None = None, backend: str = "openviking") -> str:
    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    backend_id = normalize_backend(str(backend or "openviking"))
    if backend_id == "echomemory":
        prefix = "echomem_workspace"
    else:
        prefix = "openviking_workspace"
    return str(Path(home).expanduser() / f"{prefix}_{slug_account(account)}_{stamp}")


def normalize_backend(value: str | None) -> str:
    text = str(value or "").strip().lower()
    return "echomemory" if text in {"echomem", "echomemory"} else "openviking"


def is_legacy_fixed_workspace(workspace: str | Path | None) -> bool:
    value = str(workspace or "").rstrip("/")
    name = Path(value).name.lower()
    retired_marker = "hi" + "go"
    return value.endswith("/openviking_workspace_new0420") or ("workspace" in name and retired_marker in name)


def normalize_workspace_config(defaults: dict[str, Any], account: str, config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config or {})
    normalized["memoryBackend"] = normalize_backend(str(normalized.get("memoryBackend") or defaults.get("memory_backend") or "openviking"))
    workspace = str(normalized.get("ovWorkspace") or normalized.get("memoryWorkspace") or "").strip()
    was_legacy = is_legacy_fixed_workspace(workspace)
    if not workspace or was_legacy:
        workspace = clean_workspace(defaults.get("home") or str(Path.home()), account, backend=normalized["memoryBackend"])
        normalized["ovWorkspace"] = workspace
        normalized["memoryWorkspace"] = workspace
        normalized["workspace_source"] = "migrated_legacy_fixed_workspace" if was_legacy else "generated_missing_workspace"
    elif not normalized.get("memoryWorkspace"):
        normalized["memoryWorkspace"] = workspace
    return normalized


def account_root(workspace: str | Path, account: str) -> Path:
    return Path(workspace).expanduser() / "viking" / slug_account(account)


def storage_root(workspace: str | Path, account: str, backend: str = "openviking") -> Path:
    workspace_path = Path(workspace).expanduser()
    account_id = slug_account(account)
    if normalize_backend(backend) == "echomemory":
        return workspace_path / account_id / account_id
    return workspace_path / "viking" / account_id


def prepare_account_workspace(workspace: str | Path, account: str, user_id: str = "default", agent_id: str = "default", backend: str = "openviking") -> dict[str, str]:
    backend = normalize_backend(backend)
    root = storage_root(workspace, account, backend)
    if backend == "echomemory":
        paths = {
            "workspace": str(Path(workspace).expanduser()),
            "storage_root": str(root),
            "account_root": str(root),
            "session_root": str(root / "sessions"),
            "user_root": str(root / "users" / user_id),
            "user_memories": str(root / "users" / user_id / "memories"),
            "agent_root": str(root / "agents" / agent_id),
            "agent_memories": str(root / "agents" / agent_id / "memories"),
        }
    else:
        paths = {
            "workspace": str(Path(workspace).expanduser()),
            "storage_root": str(root),
            "account_root": str(root),
            "session_root": str(root / "session"),
            "user_root": str(root / "user" / user_id),
            "user_memories": str(root / "user" / user_id / "memories"),
            "agent_root": str(root / "agent" / agent_id),
            "agent_memories": str(root / "agent" / agent_id / "memories"),
        }
    for value in paths.values():
        Path(value).mkdir(parents=True, exist_ok=True)
    return paths


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if any(marker in key.lower() for marker in SECRET_MARKERS):
            redacted[key] = "******" if value else ""
        elif isinstance(value, dict):
            redacted[key] = redact_config(value)
        else:
            redacted[key] = value
    return redacted


def sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if any(marker in key.lower() for marker in SECRET_MARKERS):
            continue
        if isinstance(value, dict):
            sanitized[key] = sanitize_config(value)
        else:
            sanitized[key] = value
    return sanitized


def default_account_config(defaults: dict[str, Any], account: str = DEFAULT_ACCOUNT, clean: bool = False) -> dict[str, Any]:
    host = str(defaults.get("server_host") or "127.0.0.1")
    port = str(defaults.get("server_port") or "19080")
    backend = normalize_backend(str(defaults.get("memory_backend") or "openviking"))
    workspace = str(defaults.get("openviking_workspace") or defaults.get("workspace") or "")
    if clean or not workspace or is_legacy_fixed_workspace(workspace):
        workspace = clean_workspace(defaults.get("home") or str(Path.home()), account, backend=backend)
    return {
        "memoryBackend": backend,
        "ovHost": host,
        "ovPort": port,
        "ovWorkspace": workspace,
        "memoryWorkspace": workspace,
        "judgeBaseUrl": str(defaults.get("judge_base_url") or ""),
        "judgeModel": str(defaults.get("judge_model") or "gpt-5.5"),
        "chatTopK": "",
    }


def plugin_config_view(config: dict[str, Any]) -> dict[str, Any]:
    host = str(config.get("ovHost") or "127.0.0.1").strip() or "127.0.0.1"
    port = str(config.get("ovPort") or "19080").strip() or "19080"
    workspace = str(config.get("ovWorkspace") or config.get("memoryWorkspace") or "").strip()
    return {
        "active_backend": normalize_backend(str(config.get("memoryBackend") or "openviking")),
        "openviking": {
            "server_url": f"http://{host}:{port}",
            "workspace": workspace,
            "account_scoped": True,
        },
        "echomemory": {
            "workspace": workspace,
            "base_url": "local-sdk",
            "account_scoped": True,
        },
        "models": {
            "judge_base_url": str(config.get("judgeBaseUrl") or ""),
            "judge_model": str(config.get("judgeModel") or ""),
        },
    }


def empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "active_account": DEFAULT_ACCOUNT, "accounts": {}}


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    data.setdefault("schema_version", 1)
    data.setdefault("active_account", DEFAULT_ACCOUNT)
    data.setdefault("accounts", {})
    if not isinstance(data["accounts"], dict):
        data["accounts"] = {}
    return data


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def ensure_default(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    state = read_state(path)
    accounts = state.setdefault("accounts", {})
    changed = False
    if DEFAULT_ACCOUNT not in accounts:
        stamp = now_iso()
        accounts[DEFAULT_ACCOUNT] = {
            "id": DEFAULT_ACCOUNT,
            "created_at": stamp,
            "updated_at": stamp,
            "config": default_account_config(defaults, DEFAULT_ACCOUNT, clean=False),
        }
        state["active_account"] = state.get("active_account") or DEFAULT_ACCOUNT
        changed = True
    for account_id, record in accounts.items():
        config = record.get("config") or {}
        normalized = normalize_workspace_config(defaults, account_id, config)
        if normalized != config:
            record["config"] = normalized
            record["updated_at"] = now_iso()
            changed = True
    if changed:
        write_state(path, state)
    return state


def public_account(record: dict[str, Any]) -> dict[str, Any]:
    config = sanitize_config(record.get("config") or {})
    account_id = record.get("id") or DEFAULT_ACCOUNT
    workspace = str(config.get("ovWorkspace") or config.get("memoryWorkspace") or "").strip()
    backend = normalize_backend(str(config.get("memoryBackend") or "openviking"))
    workspace_path = Path(workspace).expanduser() if workspace else None
    root = storage_root(workspace_path, account_id, backend) if workspace_path else None
    if backend == "echomemory":
        user_root = root / "users" / "default" if root else None
        agent_root = root / "agents" / "default" if root else None
        session_root = root / "sessions" if root else None
        memory_root = root / "memory" if root else None
        atom_root = root / "memory" / ".structured" / "atoms" if root else None
    else:
        user_root = root / "user" / "default" if root else None
        agent_root = root / "agent" / "default" if root else None
        session_root = root / "session" if root else None
        memory_root = root / "user" / "default" / "memories" if root else None
        atom_root = None
    def count_files(path: Path | None, limit: int = 200000) -> int:
        if not path or not path.exists():
            return 0
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file() and (not item.suffix or item.suffix.lower() in COUNTED_MEMORY_SUFFIXES):
                    total += 1
                    if total >= limit:
                        break
        except Exception:
            return 0
        return total

    storage_files = count_files(root)
    session_files = count_files(session_root)
    memory_files = count_files(memory_root)
    atom_files = count_files(atom_root)
    retrievable_memory_files = atom_files + memory_files
    return {
        "id": account_id,
        "created_at": record.get("created_at") or "",
        "updated_at": record.get("updated_at") or "",
        "config": redact_config(config),
        "plugin_configs": plugin_config_view(config),
        "isolation": {
            "backend": backend,
            "workspace": workspace,
            "storage_root": str(root) if root else "",
            "viking_root": str(root) if backend == "openviking" and root else "",
            "workspace_exists": bool(workspace_path and workspace_path.exists()),
            "account_root_exists": bool(root and root.exists()),
            "session_root_exists": bool(session_root and session_root.exists()),
            "user_root_exists": bool(user_root and user_root.exists()),
            "agent_root_exists": bool(agent_root and agent_root.exists()),
            "file_count": storage_files,
            "session_file_count": session_files,
            "memory_file_count": memory_files,
            "atom_file_count": atom_files,
            "retrievable_memory_file_count": retrievable_memory_files,
            "empty": retrievable_memory_files == 0,
            "status": "pending",
        },
    }


def public_state(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    state = ensure_default(path, defaults)
    accounts = state.get("accounts") or {}
    for record in accounts.values():
        config = sanitize_config(record.get("config") or {})
        account_id = record.get("id") or DEFAULT_ACCOUNT
        workspace = str(config.get("ovWorkspace") or config.get("memoryWorkspace") or "").strip()
        backend = str(config.get("memoryBackend") or "openviking")
        if workspace:
            prepare_account_workspace(workspace, account_id, backend=backend)
    rows = [public_account(accounts[key]) for key in sorted(accounts)]
    workspace_counts = Counter((row.get("isolation") or {}).get("workspace") or "" for row in rows)
    workspace_accounts: dict[str, list[str]] = {}
    for row in rows:
        workspace = (row.get("isolation") or {}).get("workspace") or ""
        if workspace:
            workspace_accounts.setdefault(workspace, []).append(str(row.get("id") or ""))
    for row in rows:
        isolation = row.get("isolation") or {}
        workspace = isolation.get("workspace") or ""
        if not workspace:
            status = "missing_workspace"
        elif workspace_counts[workspace] > 1:
            status = "shared_workspace"
        else:
            status = "isolated_workspace"
        isolation["status"] = status
        isolation["shared_with"] = [item for item in workspace_accounts.get(workspace, []) if item != row.get("id")]
        row["isolation"] = isolation
    return {
        "state_file": str(path),
        "active_account": state.get("active_account") or DEFAULT_ACCOUNT,
        "accounts": rows,
    }


def create_account(path: Path, defaults: dict[str, Any], account: str, inherit_from: str = "", config: dict[str, Any] | None = None) -> dict[str, Any]:
    state = ensure_default(path, defaults)
    accounts = state.setdefault("accounts", {})
    account_id = slug_account(account)
    inherited = dict((accounts.get(inherit_from) or {}).get("config") or {})
    incoming = {k: v for k, v in inherited.items() if k not in {"ovWorkspace", "memoryWorkspace"}}
    incoming.update(sanitize_config(config or {}))
    base_defaults = dict(defaults)
    if incoming.get("memoryBackend"):
        base_defaults["memory_backend"] = incoming["memoryBackend"]
    base = default_account_config(base_defaults, account_id, clean=True)
    base.update(incoming)
    base = normalize_workspace_config(defaults, account_id, base)
    workspace = str(base.get("ovWorkspace") or "").strip()
    if not workspace:
        workspace = clean_workspace(defaults.get("home") or str(Path.home()), account_id, backend=str(base.get("memoryBackend") or defaults.get("memory_backend") or "openviking"))
        base["ovWorkspace"] = workspace
    if not base.get("memoryWorkspace"):
        base["memoryWorkspace"] = workspace
    if workspace:
        prepare_account_workspace(workspace, account_id, backend=str(base.get("memoryBackend") or "openviking"))
    stamp = now_iso()
    existing = accounts.get(account_id) or {}
    accounts[account_id] = {
        "id": account_id,
        "created_at": existing.get("created_at") or stamp,
        "updated_at": stamp,
        "config": base,
    }
    state["active_account"] = account_id
    write_state(path, state)
    return public_state(path, defaults)


def delete_account(path: Path, defaults: dict[str, Any], account: str) -> dict[str, Any]:
    state = ensure_default(path, defaults)
    account_id = slug_account(account)
    if account_id == DEFAULT_ACCOUNT:
        raise ValueError("default account cannot be deleted")
    state.setdefault("accounts", {}).pop(account_id, None)
    if state.get("active_account") == account_id:
        state["active_account"] = DEFAULT_ACCOUNT
    write_state(path, state)
    return public_state(path, defaults)


def update_config(path: Path, defaults: dict[str, Any], account: str, config: dict[str, Any]) -> dict[str, Any]:
    state = ensure_default(path, defaults)
    account_id = slug_account(account)
    accounts = state.setdefault("accounts", {})
    if account_id not in accounts:
        create_account(path, defaults, account_id, config=config)
        state = read_state(path)
        accounts = state.setdefault("accounts", {})
    current = accounts[account_id]
    current["config"] = {**(current.get("config") or {}), **sanitize_config(config or {})}
    current["config"] = normalize_workspace_config(defaults, account_id, current["config"])
    current["updated_at"] = now_iso()
    workspace = str(current["config"].get("ovWorkspace") or current["config"].get("memoryWorkspace") or "").strip()
    if workspace:
        prepare_account_workspace(workspace, account_id, backend=str(current["config"].get("memoryBackend") or "openviking"))
    state["active_account"] = account_id
    write_state(path, state)
    return public_state(path, defaults)
