from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..backend_profiles import backend_profile


def looks_like_echomem_root(path: Path) -> bool:
    return (
        ((path / "packages" / "echomem" / "src").exists() and (path / "packages" / "echofs" / "src").exists())
        or ((path / "src" / "echomem").exists() and (path / "src" / "echo0").exists() and (path / "pyproject.toml").exists())
        or ((path / "echomem").exists() and (path / "pyproject.toml").exists())
    )


@dataclass(frozen=True)
class RuntimeStatusContext:
    repo_root: Path
    first_existing_path: Callable[[list[Path], Path], Path]
    resolve_openviking_embedding_config: Callable[[], dict[str, str]]
    resolve_openviking_vlm_config: Callable[[], dict[str, str]]
    plugin_service: Any


def discover_echomem_roots(config: dict[str, Any], *, context: RuntimeStatusContext) -> list[dict[str, Any]]:
    repo_root = context.repo_root
    cwd_root_develop = (Path.cwd() / "EchoMem_develop").expanduser().resolve()
    cwd_root_v010 = (Path.cwd() / "echo_memory_v010").expanduser().resolve()
    cwd_root_main = (Path.cwd() / "echo_memory").expanduser().resolve()
    cwd_root_tag = (Path.cwd() / "echo_memory_v007_tag").expanduser().resolve()
    cwd_root = (Path.cwd() / "echo_memory_v007").expanduser().resolve()
    repo_root_develop = (repo_root.parent / "EchoMem_develop").expanduser().resolve()
    repo_root_v010 = (repo_root.parent / "echo_memory_v010").expanduser().resolve()
    repo_root_main = (repo_root.parent / "echo_memory").expanduser().resolve()
    repo_root_tag = (repo_root.parent / "echo_memory_v007_tag").expanduser().resolve()
    repo_root_legacy = (repo_root.parent / "echo_memory_v007").expanduser().resolve()
    home_root_develop = (Path.home() / "Code" / "echomemory" / "EchoMem_develop").expanduser().resolve()
    home_root_v010 = (Path.home() / "Code" / "echomemory" / "echo_memory_v010").expanduser().resolve()
    home_root_main = (Path.home() / "Code" / "echomemory" / "echo_memory").expanduser().resolve()
    home_root_tag = (Path.home() / "Code" / "echomemory" / "echo_memory_v007_tag").expanduser().resolve()
    home_root = (Path.home() / "Code" / "echomemory" / "echo_memory_v007").expanduser().resolve()
    preferred_default = context.first_existing_path(
        [
            home_root_develop,
            cwd_root_develop,
            repo_root_develop,
            home_root_v010,
            cwd_root_v010,
            repo_root_v010,
            home_root_main,
            cwd_root_main,
            repo_root_main,
            cwd_root_tag,
            cwd_root,
            repo_root_tag,
            repo_root_legacy,
            home_root_tag,
            home_root,
        ],
        home_root_main,
    )
    raw_candidates = [
        config.get("echomemRoot"),
        config.get("echomem_root"),
        os.environ.get("ECHOMEM_ROOT"),
        os.environ.get("ECHOMEMORY_ROOT"),
        home_root_develop,
        repo_root_develop,
        cwd_root_develop,
        home_root_v010,
        repo_root_v010,
        cwd_root_v010,
        home_root_main,
        repo_root_main,
        cwd_root_main,
        home_root,
        home_root_tag,
        repo_root_legacy,
        repo_root_tag,
        cwd_root_tag,
        cwd_root,
    ]
    explicit_keys = {
        str(Path(str(value)).expanduser().resolve())
        for value in [config.get("echomemRoot"), config.get("echomem_root"), os.environ.get("ECHOMEM_ROOT"), os.environ.get("ECHOMEMORY_ROOT")]
        if value
    }
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for raw in raw_candidates:
        if not raw:
            continue
        path = Path(str(raw)).expanduser().resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "path": key,
                "exists": path.exists(),
                "sdk_layout": looks_like_echomem_root(path),
                "default": key == str(preferred_default),
                "explicit": key in explicit_keys,
            }
        )
    return rows


def echomem_git_info(root: str | Path) -> dict[str, Any]:
    path = Path(str(root)).expanduser()
    required_tag = "version_0.1.0"
    is_develop_layout = (path / "src" / "echomem").exists() and (path / "src" / "echo0").exists() and (path / "pyproject.toml").exists()
    if not path.exists():
        return {"tag": "", "commit": "", "describe": "", "required_tag": required_tag, "version_ok": False}

    def run_git(args: list[str]) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    tag = run_git(["describe", "--tags", "--exact-match"])
    describe = run_git(["describe", "--tags", "--always", "--dirty"])
    commit = run_git(["rev-parse", "HEAD"])
    return {
        "tag": tag,
        "commit": commit,
        "short_commit": commit[:12] if commit else "",
        "describe": describe,
        "required_tag": "version_0.1.0 or EchoMem_develop" if is_develop_layout else required_tag,
        "version_ok": is_develop_layout or tag == required_tag or describe == required_tag,
        "layout": "develop-src" if is_develop_layout else "",
    }


def backend_runtime_status(
    backend: str,
    config: dict[str, Any],
    defaults: dict[str, Any],
    *,
    context: RuntimeStatusContext,
) -> dict[str, Any]:
    profile = backend_profile(backend)
    if profile.id == "openviking":
        host = str(config.get("ovHost") or defaults.get("server_host") or "127.0.0.1").strip() or "127.0.0.1"
        default_port = str(defaults.get("server_port") or "19080")
        port = str(config.get("ovPort") or default_port).strip() or default_port
        api_key = str(config.get("root_api_key") or defaults.get("root_api_key") or "").strip()
        try:
            probe = context.plugin_service.probe(backend, host, port, api_key)
        except Exception as exc:
            probe = {"ok": False, "error": str(exc)}
        return {
            "status": "ok" if probe.get("ok") else "warn",
            "kind": "service",
            "label": profile.runtime_label,
            "url": f"http://{host}:{port}",
            "probe": probe,
        }

    candidates = discover_echomem_roots(config, context=context)
    explicit_candidate = next((item for item in candidates if item.get("explicit")), None)
    explicit = explicit_candidate if explicit_candidate and explicit_candidate.get("sdk_layout") else None
    discovered = explicit or next((item for item in candidates if item.get("sdk_layout")), None)
    selected = explicit_candidate or discovered or {}
    root = str(selected.get("path") or "")
    root_exists = bool(selected.get("exists"))
    sdk_ready = bool(selected.get("sdk_layout"))
    explicit_ready = bool(explicit)
    default_ready = bool(not explicit_candidate and discovered and discovered.get("default"))
    configured_root_ready = explicit_ready or default_ready
    required_tag = "version_0.1.0"
    source = echomem_git_info(root) if root else {"version_ok": False, "required_tag": required_tag}
    version_ready = bool(source.get("version_ok"))
    embedding_config = context.resolve_openviking_embedding_config()
    openviking_vlm = context.resolve_openviking_vlm_config()
    embedding_token = str(
        config.get("dashscope_api_key")
        or config.get("echomem_api_key")
        or config.get("echomemEmbeddingApiKey")
        or config.get("embedding_api_key")
        or config.get("memory_token")
        or config.get("vlm_api_key")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("ECHOMEM_API_KEY")
        or embedding_config.get("api_key")
        or openviking_vlm.get("api_key")
        or ""
    ).strip()
    chat_token = str(
        config.get("echomem_chat_api_key")
        or config.get("echomemChatApiKey")
        or config.get("vlm_api_key")
        or config.get("answer_token")
        or config.get("judge_token")
        or config.get("memory_token")
        or os.environ.get("ECHOMEM_CHAT_API_KEY")
        or openviking_vlm.get("api_key")
        or embedding_token
        or ""
    ).strip()
    embedding_ready = bool(config.get("echomemTokenSet") or config.get("echomemEmbeddingTokenSet") or embedding_token or chat_token)
    chat_ready = bool(config.get("echomemChatTokenSet") or chat_token or embedding_token)
    status = "ok" if configured_root_ready and version_ready and embedding_ready and chat_ready else ("warn" if sdk_ready else "fail")
    if explicit_candidate and not root_exists:
        message = f"显式指定的 EchoMemory 根目录不存在：{root}"
    elif explicit_candidate and not sdk_ready:
        message = f"显式指定的目录不是 EchoMemory SDK 根目录：{root}"
    elif not sdk_ready:
        message = "未找到 EchoMemory SDK 目录；需要设置 ECHOMEM_ROOT。"
    elif not configured_root_ready:
        message = f"已发现 EchoMemory 目录，建议显式设置 ECHOMEM_ROOT={root}。"
    elif not version_ready:
        found = source.get("describe") or source.get("tag") or source.get("short_commit") or "unknown"
        message = f"EchoMemory 源码版本不是 {required_tag}；当前检测到 {found}。"
    elif not embedding_ready or not chat_ready:
        missing = []
        if not embedding_ready:
            missing.append("embedding token")
        if not chat_ready:
            missing.append("chat token")
        message = "缺少 " + "、".join(missing) + "；可在 .env.local 中配置。"
    else:
        accepted_target = source.get("required_tag") or required_tag
        message = f"EchoMemory SDK {accepted_target}、embedding/chat token 均已检测到。"
    return {
        "status": status,
        "kind": "local-sdk",
        "label": profile.runtime_label,
        "root": root,
        "source": source,
        "root_exists": root_exists,
        "explicit_root": explicit_ready,
        "default_root": default_ready,
        "sdk_layout": sdk_ready,
        "version_ok": version_ready,
        "embedding_token_set": embedding_ready,
        "chat_token_set": chat_ready,
        "candidates": candidates[:6],
        "message": message,
        "next_action": "在 .env.local 中设置 ECHOMEM_ROOT、DASHSCOPE_API_KEY、ECHOMEM_CHAT_API_KEY 后重启服务。" if status != "ok" else "",
    }
