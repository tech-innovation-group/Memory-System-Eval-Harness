#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def looks_like_echomem_root(path: Path) -> bool:
    return (
        (path / "packages" / "echomem" / "src").exists()
        or ((path / "echomem").exists() and (path / "pyproject.toml").exists())
    )


def default_echomem_root() -> Path:
    preferred_local_roots = [
        Path.home() / "Code" / "echomemory" / "echo_memory_v006",
        Path.home() / "Code" / "echomemory" / "echo_memory",
    ]
    candidates = [
        os.environ.get("ECHOMEM_ROOT"),
        os.environ.get("ECHOMEMORY_ROOT"),
        *preferred_local_roots,
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw)).expanduser()
        if looks_like_echomem_root(path):
            return path
    fallback = os.environ.get("ECHOMEM_ROOT") or preferred_local_roots[0]
    return Path(str(fallback)).expanduser()


DEFAULT_ECHOMEM_ROOT = default_echomem_root()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def ensure_echomem_imports(echomem_root: str | Path = DEFAULT_ECHOMEM_ROOT) -> Path:
    root = Path(echomem_root).expanduser().resolve()
    if (root / "packages" / "echomem" / "src").exists():
        for rel in ("packages/echofs/src", "packages/echomem/src"):
            path = str(root / rel)
            if path not in sys.path:
                sys.path.insert(0, path)
    elif (root / "echomem").exists():
        path = str(root)
        if path not in sys.path:
            sys.path.insert(0, path)
    return root


def write_echomem_config(
    out_dir: Path,
    account: str,
    workspace: str,
    echomem_root: str | Path = DEFAULT_ECHOMEM_ROOT,
    fallback_to_mock: bool = False,
) -> Path:
    root = Path(echomem_root).expanduser().resolve()
    base_path = Path(workspace).expanduser().resolve() / account
    base_path.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "echomem.runtime.yaml"
    schema_src = root / "configs" / "schemas"
    schema_dst = out_dir / "configs" / "schemas"
    if schema_src.exists() and not schema_dst.exists():
        schema_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(schema_src, schema_dst)
    keywords_src = root / "configs" / "extractors" / "intent_keywords.yaml"
    keywords_dst = out_dir / "configs" / "extractors" / "intent_keywords.yaml"
    if keywords_src.exists() and not keywords_dst.exists():
        keywords_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(keywords_src, keywords_dst)
    mock_text = "true" if fallback_to_mock else "false"
    # For benchmark imports we want the richer memory layers by default so
    # sessions do not stop at vectors/atoms only. These can still be turned off
    # explicitly via env when a minimal runtime is desired.
    #
    # Keep atom extraction aligned with EchoMemory v0.0.6's own extractor
    # defaults. The upstream RawAtomExtractor uses window_size=8; forcing a
    # much smaller window here produces many more overlapping atom-extraction
    # calls during LoCoMo imports and can leave session-level flushes looking
    # stalled even though overview/abstract already finished.
    sync_atoms_to_graph_bool = _env_bool("ECHOMEM_SYNC_ATOMS_TO_GRAPH", True)
    run_organized_projection_bool = _env_bool("ECHOMEM_RUN_ORGANIZED_PROJECTION", True)
    run_episode_projection_bool = _env_bool("ECHOMEM_RUN_EPISODE_PROJECTION", False)
    sync_atoms_to_graph = _yaml_bool(sync_atoms_to_graph_bool)
    run_organized_projection = _yaml_bool(run_organized_projection_bool)
    run_episode_projection = _yaml_bool(run_episode_projection_bool)
    dashscope_base_url = (os.environ.get("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    chat_base_url = (os.environ.get("ECHOMEM_CHAT_BASE_URL") or dashscope_base_url).strip()
    anthropic_base_url = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.minimaxi.com/anthropic").strip()
    chat_provider = (os.environ.get("ECHOMEM_CHAT_PROVIDER") or "deepseek").strip()
    chat_model = (os.environ.get("ECHOMEM_CHAT_MODEL") or "deepseek-v4-flash").strip()
    search_intent_llm_first_bool = _env_bool("ECHOMEM_SEARCH_INTENT_LLM_FIRST", True)
    search_intent_llm_fallback_bool = _env_bool("ECHOMEM_SEARCH_INTENT_LLM_FALLBACK", True)
    search_intent_llm_first = _yaml_bool(search_intent_llm_first_bool)
    search_intent_llm_fallback = _yaml_bool(search_intent_llm_fallback_bool)
    search_intent_backend = (os.environ.get("ECHOMEM_SEARCH_INTENT_BACKEND") or "").strip().lower()
    if search_intent_backend not in {"llm", "rule"}:
        search_intent_backend = "llm" if search_intent_llm_first_bool else "rule"
    content = f"""tenant:
  id: "{account}"
echofs:
  backend: "file"
  base_path: "{base_path}"
schemas:
  path: "{root / "configs/schemas"}"
models:
  fallback_to_mock: {mock_text}
  aliases:
    embedding:
      provider: dashscope
      model_id: text-embedding-v3
      temperature: 0.0
      max_tokens: 8192
    chat:
      provider: "{chat_provider}"
      model_id: "{chat_model}"
      temperature: 0.0
      max_tokens: 8192
      max_concurrent: 4
    intent-classifier:
      provider: "{chat_provider}"
      model_id: "{chat_model}"
      temperature: 0.0
      max_tokens: 2048
      max_concurrent: 4
  providers:
    dashscope:
      api_key: "${{DASHSCOPE_API_KEY}}"
      base_url: "{dashscope_base_url}"
      default_timeout: 60
    deepseek:
      api_key: "${{ECHOMEM_CHAT_API_KEY}}"
      base_url: "{chat_base_url}"
      default_timeout: 120
    anthropic:
      api_key: "${{ANTHROPIC_AUTH_TOKEN}}"
      base_url: "{anthropic_base_url}"
      default_timeout: 120
vector:
  backend: hnswlib
  dim: 1024
  indexing_enabled: true
search:
  text_scan:
    max_files: {int(os.environ.get("ECHOMEM_TEXT_SCAN_MAX_FILES") or 5000)}
    timeout_seconds: {float(os.environ.get("ECHOMEM_TEXT_SCAN_TIMEOUT_SECONDS") or 10)}
  intent:
    llm_first: {search_intent_llm_first}
    llm_fallback: {search_intent_llm_fallback}
    backend: "{search_intent_backend}"
    model_alias: "chat"
    temperature: 0.0
    max_tokens: 256
    fallback_to_rule: true
  fusion:
    max_results: 25
    strategy_boost: 0.18
memory:
  pipeline:
    mode: atom_first
    auto_flush_on_message_persisted: false
    atom_window_size: {int(os.environ.get("ECHOMEM_ATOM_WINDOW_SIZE") or 8)}
    atom_max_tokens: {int(os.environ.get("ECHOMEM_ATOM_MAX_TOKENS") or 1536)}
    atom_prompt_profile: "{os.environ.get("ECHOMEM_ATOM_PROMPT_PROFILE") or "compact"}"
    atom_turn_limit: {int(os.environ.get("ECHOMEM_ATOM_TURN_LIMIT") or 8)}
    sync_atoms_to_graph: {sync_atoms_to_graph}
    run_organized_projection: {run_organized_projection}
    run_episode_projection: {run_episode_projection}
    index_atoms_to_vector_store: true
extractor:
  session_committed_adapter:
    enabled: false
  intent:
    backend: "rule"
    model_alias: "intent-classifier"
    temperature: 0.0
    max_tokens: 2048
    fallback_to_rule: true
    keywords_path: "{root / "configs/extractors/intent_keywords.yaml"}"
  extraction:
    backend: "hybrid"
    model_alias: "chat"
    temperature: 0.0
    max_tokens: 8192
  registry:
    switchable: false
  scanner:
    enabled: false
    interval_seconds: 300
    batch_size: 10
    committed_only: true
agent_auth:
  enabled: true
  anonymous_fallback: true
logging:
  level: "INFO"
  format: "json"
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_name(f".{config_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(config_path)
    return config_path


def ctx(account: str, user_id: str, agent_id: str = "", session_id: str = "") -> dict[str, str]:
    return {
        "account_id": account or "default",
        "user_id": user_id or "default",
        "agent_id": agent_id or "default",
        "session_id": session_id or "",
    }


def sdk_ctx_kwargs(sdk: Any, account: str, user_id: str, agent_id: str = "", session_id: str = "") -> dict[str, str]:
    """Build ctx kwargs compatible with EchoMemory SDK versions with/without agent_id."""
    data = ctx(account, user_id, agent_id, session_id)
    try:
        sdk._ctx(**data)
        return data
    except TypeError as exc:
        if "agent_id" not in str(exc):
            raise
        data.pop("agent_id", None)
        return data


def context_item_to_dict(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        data = asdict(item)
    elif isinstance(item, dict):
        data = dict(item)
    else:
        data = {
            "content": getattr(item, "content", ""),
            "source_uri": getattr(item, "source_uri", ""),
            "memory_type": getattr(item, "memory_type", ""),
            "confidence": getattr(item, "confidence", 0.0),
            "evidence_uri": getattr(item, "evidence_uri", ""),
            "path": getattr(item, "path", ""),
            "trace": getattr(item, "trace", {}),
        }
    return {
        "uri": data.get("source_uri") or data.get("uri") or data.get("path") or "",
        "score": data.get("confidence") or data.get("score") or 0.0,
        "content": data.get("content") or "",
        "memory_type": data.get("memory_type") or "",
        "evidence_uri": data.get("evidence_uri") or "",
        "path": data.get("path") or "",
        "trace": data.get("trace") or {},
        "backend": "echomemory",
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
