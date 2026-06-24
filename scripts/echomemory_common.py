#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def detect_echomem_layout(path: Path) -> str:
    if (path / "packages" / "echomem" / "src").exists() and (path / "packages" / "echofs" / "src").exists():
        return "old-packages"
    if (path / "src" / "echomem").exists() and (path / "src" / "echo0").exists() and (path / "pyproject.toml").exists():
        return "develop-src"
    if (path / "echomem").exists() and (path / "pyproject.toml").exists():
        return "legacy-flat"
    return "unknown"


def looks_like_echomem_root(path: Path) -> bool:
    return detect_echomem_layout(path) != "unknown"


def default_echomem_root() -> Path:
    candidates = [
        os.environ.get("ECHOMEM_ROOT"),
        os.environ.get("ECHOMEMORY_ROOT"),
        Path.home() / "Code" / "echomemory" / "EchoMem_develop",
        Path.home() / "Code" / "echomemory" / "echo_memory_v010",
        Path.home() / "Code" / "echomemory" / "echo_memory",
        Path.cwd() / "EchoMem_develop",
        Path.cwd().parent / "EchoMem_develop",
        Path.cwd() / "echo_memory_v010",
        Path.cwd().parent / "echo_memory_v010",
        Path.cwd() / "echo_memory",
        Path.cwd().parent / "echo_memory",
        Path.cwd() / "echo_memory_v007_tag",
        Path.cwd() / "echo_memory_v007",
        Path.cwd().parent / "echo_memory_v007_tag",
        Path.cwd().parent / "echo_memory_v007",
        Path.home() / "Code" / "echomemory" / "echo_memory_v006",
        Path.home() / "Code" / "echomemory" / "echo_memory_v007_tag",
        Path.home() / "Code" / "echomemory" / "echo_memory_v007",
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(str(raw)).expanduser()
        if looks_like_echomem_root(path):
            return path
    fallback = (
        os.environ.get("ECHOMEM_ROOT")
        or os.environ.get("ECHOMEMORY_ROOT")
        or (Path.home() / "Code" / "echomemory" / "echo_memory")
    )
    return Path(str(fallback)).expanduser()


DEFAULT_ECHOMEM_ROOT = default_echomem_root()
ECHOMEM_IMPORT_CALL_SITES = {
    "atom_extraction",
    "memory_extraction",
    "extraction_intent",
    "graph_arbitration",
    "entity_merge",
    "overview_generation",
    "abstract_generation",
    "vector_indexing",
    "experience_extraction",
}
ECHOMEM_QA_CALL_SITES = {
    "search_intent",
}


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
    layout = detect_echomem_layout(root)
    if layout == "old-packages":
        for rel in ("packages/echofs/src", "packages/echomem/src"):
            path = str(root / rel)
            if path not in sys.path:
                sys.path.insert(0, path)
    elif layout == "develop-src":
        path = str(root / "src")
        if path not in sys.path:
            sys.path.insert(0, path)
    elif layout == "legacy-flat":
        path = str(root)
        if path not in sys.path:
            sys.path.insert(0, path)
    return root


def echomem_account_roots(workspace: str | Path, account: str) -> list[Path]:
    workspace_path = Path(workspace).expanduser().resolve()
    candidates = [
        workspace_path / "tenants" / account,
        workspace_path / account / "default",
        workspace_path / account / account,
        workspace_path / account,
        workspace_path,
    ]
    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            roots.append(candidate)
    return roots


def load_workspace_token_rows(workspace: str | Path, account: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    token_dirs: list[Path] = []
    seen_dirs: set[str] = set()
    for root in echomem_account_roots(workspace, account):
        candidates = [root / "metrics" / "llm_tokens"]
        engines_root = root / "engines"
        if engines_root.exists():
            candidates.extend(sorted(engines_root.glob("*/metrics/llm_tokens")))
        for token_dir in candidates:
            key = str(token_dir)
            if key in seen_dirs or not token_dir.exists():
                continue
            seen_dirs.add(key)
            token_dirs.append(token_dir)
    for token_dir in token_dirs:
        for path in sorted(token_dir.glob("*.jsonl")):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                timestamp = row.get("timestamp")
                if timestamp:
                    try:
                        row["_timestamp"] = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                    except Exception:
                        pass
                rows.append(row)
    rows.sort(key=lambda item: str(item.get("timestamp") or ""))
    return rows


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def summarize_token_rows(
    rows: list[dict[str, Any]],
    *,
    include_call_sites: set[str] | None = None,
    exclude_call_sites: set[str] | None = None,
    start_time: datetime | str | None = None,
    end_time: datetime | str | None = None,
) -> dict[str, Any]:
    by_call_site: dict[str, dict[str, int]] = {}
    start_dt = _coerce_datetime(start_time)
    end_dt = _coerce_datetime(end_time)
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_calls = 0
    total_latency_ms = 0.0
    max_latency_ms = 0.0
    for row in rows:
        row_ts = row.get("_timestamp")
        if not isinstance(row_ts, datetime):
            row_ts = _coerce_datetime(row.get("timestamp"))
        if (start_dt or end_dt) and not isinstance(row_ts, datetime):
            continue
        if isinstance(row_ts, datetime):
            if start_dt is not None and row_ts < start_dt:
                continue
            if end_dt is not None and row_ts > end_dt:
                continue
        call_site = str(row.get("call_site") or "unknown")
        if include_call_sites is not None and call_site not in include_call_sites:
            continue
        if exclude_call_sites is not None and call_site in exclude_call_sites:
            continue
        input_tokens = int(row.get("input_tokens") or 0)
        output_tokens = int(row.get("output_tokens") or 0)
        row_total_tokens = int(row.get("total_tokens") or (input_tokens + output_tokens))
        try:
            row_latency_ms = float(row.get("latency_ms") or 0.0)
        except Exception:
            row_latency_ms = 0.0
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_tokens += row_total_tokens
        total_calls += 1
        total_latency_ms += row_latency_ms
        max_latency_ms = max(max_latency_ms, row_latency_ms)
        bucket = by_call_site.setdefault(
            call_site,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "call_count": 0,
                "total_latency_ms": 0.0,
                "avg_latency_ms": 0.0,
                "max_latency_ms": 0.0,
            },
        )
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += row_total_tokens
        bucket["call_count"] += 1
        bucket["total_latency_ms"] += row_latency_ms
        bucket["max_latency_ms"] = max(float(bucket.get("max_latency_ms") or 0.0), row_latency_ms)
    for bucket in by_call_site.values():
        call_count = max(int(bucket.get("call_count") or 0), 1)
        bucket["total_latency_ms"] = round(float(bucket.get("total_latency_ms") or 0.0), 1)
        bucket["avg_latency_ms"] = round(float(bucket["total_latency_ms"]) / call_count, 1)
        bucket["max_latency_ms"] = round(float(bucket.get("max_latency_ms") or 0.0), 1)
    return {
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "call_count": total_calls,
        "total_latency_ms": round(total_latency_ms, 1),
        "avg_latency_ms": round(total_latency_ms / total_calls, 1) if total_calls else 0.0,
        "max_latency_ms": round(max_latency_ms, 1),
        "by_call_site": by_call_site,
    }


def workspace_token_usage_summary(
    workspace: str | Path,
    account: str,
    *,
    start_time: datetime | str | None = None,
    end_time: datetime | str | None = None,
) -> dict[str, Any]:
    rows = load_workspace_token_rows(workspace, account)
    overall = summarize_token_rows(rows, start_time=start_time, end_time=end_time)
    import_usage = summarize_token_rows(
        rows,
        exclude_call_sites=ECHOMEM_QA_CALL_SITES,
        start_time=start_time,
        end_time=end_time,
    )
    qa_internal = summarize_token_rows(
        rows,
        include_call_sites=ECHOMEM_QA_CALL_SITES,
        start_time=start_time,
        end_time=end_time,
    )
    embedding_usage = overall["by_call_site"].get("embedding", {})
    return {
        "llm_log_rows": len(rows),
        "llm_input_tokens": overall["total_input_tokens"],
        "llm_output_tokens": overall["total_output_tokens"],
        "llm_total_tokens": overall["total_tokens"],
        "llm_call_count": overall["call_count"],
        "llm_total_latency_ms": overall["total_latency_ms"],
        "llm_avg_latency_ms": overall["avg_latency_ms"],
        "llm_max_latency_ms": overall["max_latency_ms"],
        "import_llm_prompt_tokens": import_usage["total_input_tokens"],
        "import_llm_completion_tokens": import_usage["total_output_tokens"],
        "import_llm_total_tokens": import_usage["total_tokens"],
        "import_llm_total_latency_ms": import_usage["total_latency_ms"],
        "import_llm_avg_latency_ms": import_usage["avg_latency_ms"],
        "import_llm_max_latency_ms": import_usage["max_latency_ms"],
        "import_embedding_total_tokens": int(embedding_usage.get("total_tokens") or 0),
        "import_total_tokens": import_usage["total_tokens"],
        "search_intent_total_tokens": qa_internal["total_tokens"],
        "search_intent_call_count": qa_internal["call_count"],
        "search_intent_total_latency_ms": qa_internal["total_latency_ms"],
        "search_intent_avg_latency_ms": qa_internal["avg_latency_ms"],
        "search_intent_max_latency_ms": qa_internal["max_latency_ms"],
        "embedding_total_tokens": int(embedding_usage.get("total_tokens") or 0),
        "embedding_call_count": int(embedding_usage.get("call_count") or 0),
        "call_sites": overall["by_call_site"],
    }


def _optional_yaml_load(text: str) -> Any:
    try:
        import yaml
    except Exception:
        return None
    try:
        return yaml.safe_load(text)
    except Exception:
        return None


def load_echomem_config_file(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser()
    if not config_path.exists():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8-sig")
    except Exception:
        return {}
    stripped = text.lstrip()
    if not stripped:
        return {}
    loaded: Any = None
    if config_path.suffix.lower() == ".json" or stripped[:1] in {"{", "["}:
        try:
            loaded = json.loads(text)
        except Exception:
            loaded = None
    if loaded is None:
        loaded = _optional_yaml_load(text)
    return loaded if isinstance(loaded, dict) else {}


def _write_legacy_echomem_config(
    out_dir: Path,
    account: str,
    workspace: str,
    echomem_root: Path,
    fallback_to_mock: bool = False,
) -> Path:
    root = echomem_root
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
    ingest_mode = (os.environ.get("ECHOMEM_INGEST_MODE") or "full").strip().lower() or "full"
    if ingest_mode not in {"full", "fast", "minimal"}:
        ingest_mode = "full"
    default_sync_atoms_to_graph = True
    default_run_organized_projection = True
    default_run_episode_projection = False
    if ingest_mode == "fast":
        default_run_organized_projection = False
        default_run_episode_projection = False
    elif ingest_mode == "minimal":
        default_sync_atoms_to_graph = False
        default_run_organized_projection = False
        default_run_episode_projection = False

    sync_atoms_to_graph_bool = _env_bool("ECHOMEM_SYNC_ATOMS_TO_GRAPH", default_sync_atoms_to_graph)
    run_organized_projection_bool = _env_bool("ECHOMEM_RUN_ORGANIZED_PROJECTION", default_run_organized_projection)
    run_episode_projection_bool = _env_bool("ECHOMEM_RUN_EPISODE_PROJECTION", default_run_episode_projection)
    index_atoms_to_vector_store_bool = _env_bool("ECHOMEM_INDEX_ATOMS_TO_VECTOR_STORE", True)
    session_generate_abstract_bool = _env_bool("ECHOMEM_SESSION_GENERATE_ABSTRACT", True)
    session_segment_enabled_bool = _env_bool("ECHOMEM_SESSION_SEGMENT_ENABLED", True)
    sync_atoms_to_graph = _yaml_bool(sync_atoms_to_graph_bool)
    run_organized_projection = _yaml_bool(run_organized_projection_bool)
    run_episode_projection = _yaml_bool(run_episode_projection_bool)
    index_atoms_to_vector_store = _yaml_bool(index_atoms_to_vector_store_bool)
    session_generate_abstract = _yaml_bool(session_generate_abstract_bool)
    session_segment_enabled = _yaml_bool(session_segment_enabled_bool)
    session_segment_window_size = int(os.environ.get("ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE") or 4)
    session_segment_stride = int(os.environ.get("ECHOMEM_SESSION_SEGMENT_STRIDE") or session_segment_window_size)
    session_segment_max_chars = int(os.environ.get("ECHOMEM_SESSION_SEGMENT_MAX_CHARS") or 1400)
    dashscope_base_url = (os.environ.get("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    chat_base_url = (os.environ.get("ECHOMEM_CHAT_BASE_URL") or dashscope_base_url).strip()
    anthropic_base_url = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.minimaxi.com/anthropic").strip()
    chat_provider = (os.environ.get("ECHOMEM_CHAT_PROVIDER") or "deepseek").strip()
    chat_model = (os.environ.get("ECHOMEM_CHAT_MODEL") or "deepseek-v4-flash").strip()
    # For benchmark QA runs, search-intent LLM planning is usually the largest
    # extra QA-side token sink. Default to rule-only intent routing here and
    # let env vars opt back into LLM planning when explicitly needed.
    search_intent_llm_first_bool = _env_bool("ECHOMEM_SEARCH_INTENT_LLM_FIRST", False)
    search_intent_llm_fallback_bool = _env_bool("ECHOMEM_SEARCH_INTENT_LLM_FALLBACK", False)
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
    online_mode: "{ingest_mode}"
    atom_window_size: {int(os.environ.get("ECHOMEM_ATOM_WINDOW_SIZE") or os.environ.get("ECHOMEM_EXTRACTION_TRIGGER_WINDOW_TURNS") or 8)}
    atom_max_tokens: {int(os.environ.get("ECHOMEM_ATOM_MAX_TOKENS") or os.environ.get("ECHOMEM_EXTRACTION_TRIGGER_PENDING_TOKENS") or 1536)}
    atom_prompt_profile: "{os.environ.get("ECHOMEM_ATOM_PROMPT_PROFILE") or "compact"}"
    atom_turn_limit: {int(os.environ.get("ECHOMEM_ATOM_TURN_LIMIT") or 8)}
    sync_atoms_to_graph: {sync_atoms_to_graph}
    run_organized_projection: {run_organized_projection}
    run_episode_projection: {run_episode_projection}
    index_atoms_to_vector_store: {index_atoms_to_vector_store}
session:
  generate_abstract: {session_generate_abstract}
  segment:
    enabled: {session_segment_enabled}
    window_size: {session_segment_window_size}
    stride: {session_segment_stride}
    max_chars: {session_segment_max_chars}
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


def _normalize_recall_routers(value: Any, default: tuple[str, ...]) -> list[str]:
    if value is None:
        items = list(default)
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        items = [str(item).strip() for item in value]
    routers: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        routers.append(item)
    return routers or list(default)


def _write_develop_echomem_config(
    out_dir: Path,
    account: str,
    user_id: str,
    workspace: str,
    fallback_to_mock: bool = False,
    recall_routers: Any = None,
) -> Path:
    config_path = out_dir / "echomem.config.json"
    dashscope_base_url = (os.environ.get("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    embedding_model = (os.environ.get("ECHOMEM_EMBEDDING_MODEL") or "text-embedding-v3").strip()
    embedding_token = str(os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ECHOMEM_API_KEY") or "").strip()
    chat_base_url = (os.environ.get("ECHOMEM_CHAT_BASE_URL") or dashscope_base_url).strip()
    chat_provider = str(os.environ.get("ECHOMEM_CHAT_PROVIDER") or "deepseek").strip().lower() or "deepseek"
    if chat_provider not in {"deepseek", "dashscope", "anthropic", "kimi", "openai"}:
        chat_provider = "deepseek"
    chat_model = (os.environ.get("ECHOMEM_CHAT_MODEL") or "deepseek-v4-flash").strip()
    chat_token = str(os.environ.get("ECHOMEM_CHAT_API_KEY") or embedding_token).strip()
    llm_provider = "fake" if fallback_to_mock else "openai_compatible"
    embedding_provider = "fake" if fallback_to_mock else "openai_compatible"
    recall_routers = _normalize_recall_routers(
        recall_routers if recall_routers is not None else os.environ.get("ECHOMEM_DEVELOP_RECALL_ROUTERS"),
        default=("template-2", "llm"),
    )
    payload = {
        "workspace_version": 1,
        "runtime": {
            "mode": "local",
            "log_level": str(os.environ.get("ECHOMEM_RUNTIME_LOG_LEVEL") or "INFO"),
        },
        "storage": {"filesystem": "local"},
        "auth": {
            "mode": "local",
            "default_tenant_id": account or "default",
            "default_user_id": user_id or "default",
        },
        "model": {
            "llm": {
                "provider": llm_provider,
                "api_base": chat_base_url if llm_provider != "fake" else "",
                "api_key": chat_token if llm_provider != "fake" else "",
                "model": chat_model if llm_provider != "fake" else "fake-llm",
            },
            "embedding": {
                "provider": embedding_provider,
                "api_base": dashscope_base_url if embedding_provider != "fake" else "",
                "api_key": embedding_token if embedding_provider != "fake" else "",
                "model": embedding_model if embedding_provider != "fake" else "fake-embedding",
                "dimensions": int(os.environ.get("ECHOMEM_EMBEDDING_DIM") or 1024),
            },
            "vlm": {
                "provider": "fake",
                "api_base": "",
                "api_key": "",
                "model": "fake-vlm",
            },
            "max_concurrent": int(os.environ.get("ECHOMEM_MODEL_MAX_CONCURRENT") or 4),
        },
        "index": {
            "text": str(os.environ.get("ECHOMEM_DEVELOP_TEXT_INDEX") or "sqlite_fts5"),
            "vector": str(os.environ.get("ECHOMEM_DEVELOP_VECTOR_INDEX") or "sqlite_numpy"),
        },
        "engine": {
            "enabled": ["echo0_plugin"],
            "configs": {
                "echo0_plugin": {
                    "observability": {
                        "token_logging": True,
                        "token_collector": str(os.environ.get("ECHOMEM_TOKEN_COLLECTOR") or "fs"),
                    },
                    "enabled_memory_types": [
                        "profile",
                        "preferences",
                        "entities",
                        "events",
                        "tools",
                        "user_lesson",
                        "user_insight",
                        "user_pattern",
                        "user_error_review",
                        "agent_lesson",
                        "agent_insight",
                        "agent_pattern",
                        "agent_error_review",
                    ],
                    "vector": {
                        "enabled": True,
                        "backend": "hnswlib",
                        "dim": int(os.environ.get("ECHOMEM_EMBEDDING_DIM") or 1024),
                    },
                    "search": {
                        "intent": {
                            "backend": str(os.environ.get("ECHOMEM_SEARCH_INTENT_BACKEND") or "rule"),
                            "llm_first": _env_bool("ECHOMEM_SEARCH_INTENT_LLM_FIRST", False),
                            "llm_fallback": _env_bool("ECHOMEM_SEARCH_INTENT_LLM_FALLBACK", False),
                            "model_alias": "chat",
                        },
                        "fusion": {
                            "max_results": int(os.environ.get("ECHOMEM_DEVELOP_FUSION_MAX_RESULTS") or 12),
                        },
                    },
                    "extractor": {
                        "ingest_roles": ["user", "assistant"],
                        "intent": {
                            "backend": "rule",
                            "model_alias": "intent-classifier",
                        },
                        "extraction": {
                            "backend": str(os.environ.get("ECHOMEM_DEVELOP_EXTRACTION_BACKEND") or "hybrid"),
                            "model_alias": "chat",
                        },
                    },
                    "models": {
                        "fallback_to_mock": bool(fallback_to_mock),
                        "aliases": {
                            "embedding": {
                                "provider": "dashscope",
                                "model_id": embedding_model,
                                "temperature": 0.0,
                                "max_tokens": 8192,
                            },
                            "chat": {
                                "provider": chat_provider,
                                "model_id": chat_model,
                                "temperature": 0.0,
                                "max_tokens": 8192,
                            },
                            "intent-classifier": {
                                "provider": chat_provider,
                                "model_id": chat_model,
                                "temperature": 0.0,
                                "max_tokens": 2048,
                            },
                        },
                        "providers": {
                            "dashscope": {
                                "api_key": embedding_token,
                                "base_url": dashscope_base_url,
                                "default_timeout": 60,
                            },
                            chat_provider: {
                                "api_key": chat_token,
                                "base_url": chat_base_url,
                                "default_timeout": 120,
                            },
                        },
                    },
                }
            },
        },
        "commit_pipeline": {
            "engine_timeout_seconds": int(os.environ.get("ECHOMEM_COMMIT_ENGINE_TIMEOUT_SECONDS") or 1800),
        },
        "recall": {
            "routers": recall_routers,
            "include_explain": False,
        },
        "mcp": {"enabled": False},
        "echoagent": {"enabled": False},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_name(f".{config_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(config_path)
    return config_path


def write_echomem_config(
    out_dir: Path,
    account: str,
    workspace: str,
    echomem_root: str | Path = DEFAULT_ECHOMEM_ROOT,
    fallback_to_mock: bool = False,
    user_id: str = "default",
    recall_routers: Any = None,
) -> Path:
    root = Path(echomem_root).expanduser().resolve()
    if detect_echomem_layout(root) == "develop-src":
        return _write_develop_echomem_config(
            out_dir,
            account,
            user_id,
            workspace,
            fallback_to_mock=fallback_to_mock,
            recall_routers=recall_routers,
        )
    return _write_legacy_echomem_config(
        out_dir,
        account,
        workspace,
        root,
        fallback_to_mock=fallback_to_mock,
    )


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


class EchoMemDevelopCompatSDK:
    def __init__(
        self,
        client: Any,
        *,
        account: str,
        user_id: str,
        agent_id: str,
        workspace: str | Path,
        runtime: Any = None,
        client_core: Any = None,
    ) -> None:
        self._client = client
        self._account = account or "default"
        self._user_id = user_id or "default"
        self._agent_id = agent_id or "default"
        self._workspace = Path(workspace).expanduser().resolve()
        self._runtime = runtime
        self._client_core = client_core
        self._compat_layout = "develop-src"

    def _ctx(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    def _ctx_value(self, ctx: dict[str, Any] | None, key: str, default: str = "") -> str:
        if isinstance(ctx, dict):
            value = str(ctx.get(key) or "").strip()
            if value:
                return value
        return default

    async def create_session(self, title: str = "", ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        session = await self._client.open_session(
            agent_id=self._ctx_value(ctx, "agent_id", self._agent_id),
            session_id=self._ctx_value(ctx, "session_id", "") or None,
            metadata={
                "title": title,
                "account_id": self._ctx_value(ctx, "account_id", self._account),
                "user_id": self._ctx_value(ctx, "user_id", self._user_id),
            },
        )
        return {
            "session_id": str(getattr(session, "session_id", "") or ""),
            "agent_id": str(getattr(session, "agent_id", "") or ""),
            "user_id": str(getattr(session, "user_id", "") or ""),
            "title": title,
        }

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        ctx: dict[str, Any] | None = None,
        created_at: str = "",
        role_id: str = "",
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if created_at:
            metadata["created_at"] = created_at
        if role_id:
            metadata["role_id"] = role_id
        message = await self._client.add_message(
            session_id,
            role=role,
            content=content,
            name=role_id or None,
            metadata=metadata or None,
        )
        return {
            "message_id": str(getattr(message, "id", "") or ""),
            "role": str(getattr(message, "role", "") or role),
            "content": str(getattr(message, "content", "") or content),
        }

    async def commit_session(
        self,
        session_id: str,
        *,
        ctx: dict[str, Any] | None = None,
        keep_recent_count: int = 0,
    ) -> Any:
        result = await self._client.commit(
            session_id,
            metadata={"keep_recent_count": int(keep_recent_count or 0)},
        )
        archive_id = str(getattr(result, "archive_id", "") or "")
        status = str(getattr(result, "status", "") or "pending")
        task_id = archive_id or f"commit-{session_id}"
        return SimpleNamespace(task_id=task_id, archive_id=archive_id, status=status)

    async def commit_status(
        self,
        session_id: str,
        archive_id: str,
        *,
        ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            state = await self._client.commit_status(session_id, archive_id)
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        fallback = self._commit_status_fallback(session_id, archive_id)
        if fallback and self._prefer_commit_status_fallback(state, fallback):
            merged = dict(state)
            merged.update(fallback)
            return merged
        return state

    def _commit_status_fallback(self, session_id: str, archive_id: str) -> dict[str, Any]:
        commit_key = f"{session_id}__{archive_id}"
        for root in echomem_account_roots(self._workspace, self._account):
            path = root / "engines" / "echo0_plugin" / "commits" / f"{commit_key}.status.json"
            data = load_echomem_config_file(path)
            if not data:
                continue
            data.setdefault("session_id", session_id)
            data.setdefault("archive_id", archive_id)
            data.setdefault("commit_id", str(data.get("commit_id") or archive_id))
            data.setdefault("accepted_at", str(data.get("updated_at") or ""))
            data.setdefault("committed_at", str(data.get("updated_at") or ""))
            data.setdefault("stage_detail", dict(data.get("detail") or {}))
            return data
        return {}

    @staticmethod
    def _prefer_commit_status_fallback(primary: dict[str, Any], fallback: dict[str, Any]) -> bool:
        def _terminal(value: Any) -> bool:
            return str(value or "").strip().lower() in {"completed", "failed"}

        primary_status = str(primary.get("status") or "").strip().lower()
        primary_stage = str(primary.get("stage") or "").strip().lower()
        fallback_status = str(fallback.get("status") or "").strip().lower()
        fallback_stage = str(fallback.get("stage") or "").strip().lower()
        if _terminal(fallback_status) or _terminal(fallback_stage):
            return not (_terminal(primary_status) or _terminal(primary_stage))
        return not primary

    async def get_history(self, session_id: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        messages = await self._client.get_history(session_id, limit=200)
        rows: list[dict[str, Any]] = []
        for item in messages:
            metadata = dict(getattr(item, "metadata", {}) or {})
            rows.append(
                {
                    "message_id": str(getattr(item, "id", "") or ""),
                    "role": str(getattr(item, "role", "") or ""),
                    "content": str(getattr(item, "content", "") or ""),
                    "name": str(getattr(item, "name", "") or ""),
                    "created_at": str(getattr(item, "created_at", "") or metadata.get("created_at") or ""),
                    "role_id": str(metadata.get("role_id") or getattr(item, "name", "") or ""),
                    "metadata": metadata,
                }
            )
        return rows

    async def search(self, query: str, *, ctx: dict[str, Any] | None = None, budget: dict[str, Any] | None = None) -> Any:
        limit = 8
        if isinstance(budget, dict):
            try:
                limit = max(1, int(budget.get("max_results") or limit))
            except Exception:
                limit = 8
        items = await self._client.search(
            query,
            agent_id=self._ctx_value(ctx, "agent_id", self._agent_id),
            session_id=self._ctx_value(ctx, "session_id", "") or None,
            limit=limit,
            include_explain=False,
            include_debug=False,
        )
        return SimpleNamespace(items=items)

    async def fs_read(self, uri: str, *, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = str(uri or "").strip()
        if raw.startswith("/"):
            raw = f"echo://{self._ctx_value(ctx, 'account_id', self._account)}/{raw.lstrip('/')}"
        elif not raw.startswith("echo://"):
            raw = f"echo://{self._ctx_value(ctx, 'account_id', self._account)}/{raw.lstrip('/')}"
        return {"content": await self._client.fs_read(raw)}

    async def close(self) -> None:
        await self._client.close()


async def open_echomem_sdk(
    *,
    echomem_root: str | Path,
    workspace: str,
    account: str,
    user_id: str,
    agent_id: str,
    config_path: str | Path,
) -> tuple[Any, Any | None, str]:
    root = ensure_echomem_imports(echomem_root)
    layout = detect_echomem_layout(root)
    print(
        f"[sdk-open] root={root} layout={layout} workspace={workspace} account={account} "
        f"user_id={user_id} agent_id={agent_id} config={config_path}",
        flush=True,
    )
    if layout == "develop-src":
        from echomem.entrypoints.client.local.async_client import AsyncEchoMemLocalClient

        # The develop local runtime auto-commits after a char threshold by
        # default. For benchmark import/QA we want one explicit archive per
        # commit so the harness can wait on the exact archive it triggered.
        os.environ["ECHOMEM_AUTO_COMMIT_THRESHOLD"] = "0"
        override = load_echomem_config_file(config_path)
        print("[sdk-open] using develop AsyncEchoMemLocalClient compat path", flush=True)
        client = AsyncEchoMemLocalClient(workspace=workspace, config=override or None)
        client_core = getattr(client, "_core", None)
        runtime = getattr(client_core, "_runtime", None)
        sdk = EchoMemDevelopCompatSDK(
            client,
            account=account,
            user_id=user_id,
            agent_id=agent_id,
            workspace=workspace,
            runtime=runtime,
            client_core=client_core,
        )
        print("[sdk-open] develop compat sdk ready", flush=True)
        return sdk, None, layout
    try:
        from echomem.protocol.local_sdk.sdk import EchoMemSDK
        from echomem.runtime.runtime import open_runtime
    except ModuleNotFoundError:
        from echomem.entrypoints.plugins.echoagent.sdk import EchoMemSDK
        from echomem.runtime.bootstrap import open_runtime
    print("[sdk-open] using local sdk/runtime path", flush=True)
    runtime = await open_runtime(str(config_path))
    sdk = EchoMemSDK(runtime)
    print("[sdk-open] local sdk/runtime ready", flush=True)
    return sdk, runtime, layout


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
        "uri": data.get("source_uri") or data.get("source") or data.get("uri") or data.get("path") or "",
        "score": data.get("confidence") or data.get("score") or 0.0,
        "content": data.get("content") or data.get("text") or "",
        "memory_type": data.get("memory_type") or data.get("kind") or "",
        "evidence_uri": data.get("evidence_uri") or "",
        "path": data.get("path") or "",
        "trace": data.get("trace") or {},
        "backend": "echomemory",
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
