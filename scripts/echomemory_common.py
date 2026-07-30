#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
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


def echomem_engine_id(default: str = "echo0_plugin") -> str:
    value = str(os.environ.get("ECHOMEM_ENGINE_ID") or default).strip()
    return value or default


def echomem_engine_candidates(preferred: str | None = None) -> tuple[str, ...]:
    primary = str(preferred or echomem_engine_id()).strip() or "echo0_plugin"
    items: list[str] = []
    for item in (primary, "echo0_plugin", "graph_engine"):
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return tuple(items)


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


def load_workspace_trace_token_rows(workspace: str | Path, account: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trace_files: list[Path] = []
    seen_files: set[str] = set()
    for root in echomem_account_roots(workspace, account):
        candidates = sorted((root / "traces").glob("*.jsonl"))
        engines_root = root / "engines"
        if engines_root.exists():
            for engine_dir in sorted(engines_root.glob("*")):
                candidates.extend(sorted((engine_dir / "traces").glob("*.jsonl")))
        for trace_file in candidates:
            key = str(trace_file)
            if key in seen_files or not trace_file.exists():
                continue
            seen_files.add(key)
            trace_files.append(trace_file)
    for trace_file in trace_files:
        try:
            text = trace_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if str(payload.get("stage") or "") != "completed":
                continue
            detail = payload.get("detail") or {}
            if not isinstance(detail, dict):
                detail = {}
            created_at = payload.get("created_at") or payload.get("timestamp")
            total_tokens = detail.get("llm_tokens_used")
            if total_tokens is None:
                total_tokens = detail.get("total_tokens")
            try:
                total_tokens_value = int(total_tokens or 0)
            except Exception:
                total_tokens_value = 0
            row = {
                "timestamp": created_at,
                "call_site": str(detail.get("call_site") or "trace_completed"),
                "input_tokens": int(detail.get("input_tokens") or 0),
                "output_tokens": int(detail.get("output_tokens") or 0),
                "total_tokens": total_tokens_value,
                "latency_ms": float(detail.get("latency_ms") or 0.0),
                "_source": "trace_completed",
                "_trace_path": str(trace_file),
            }
            if created_at:
                try:
                    row["_timestamp"] = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
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
        try:
            local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        except Exception:
            local_tz = timezone.utc
        return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
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
    row_source = "metrics_jsonl"
    if not rows:
        rows = load_workspace_trace_token_rows(workspace, account)
        row_source = "trace_completed_fallback" if rows else "none"
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
        "llm_log_source": row_source,
        "llm_log_rows": overall["call_count"],
        "llm_loaded_rows": len(rows),
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
    fallback_to_mock_embedding_only: bool = False,
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
    mock_text = "true" if (fallback_to_mock or fallback_to_mock_embedding_only) else "false"
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
    fallback_to_mock_embedding_only: bool = False,
    recall_routers: Any = None,
) -> Path:
    config_path = out_dir / "echomem.config.json"
    engine_id = echomem_engine_id()
    dashscope_base_url = (os.environ.get("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    embedding_model = (os.environ.get("ECHOMEM_EMBEDDING_MODEL") or "text-embedding-v3").strip()
    embedding_token = str(os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ECHOMEM_API_KEY") or "").strip()
    chat_base_url = (os.environ.get("ECHOMEM_CHAT_BASE_URL") or dashscope_base_url).strip()
    chat_provider = str(os.environ.get("ECHOMEM_CHAT_PROVIDER") or "deepseek").strip().lower() or "deepseek"
    if chat_provider not in {"deepseek", "dashscope", "anthropic", "kimi", "openai"}:
        chat_provider = "deepseek"
    chat_model = (os.environ.get("ECHOMEM_CHAT_MODEL") or "deepseek-v4-flash").strip()
    chat_token = str(os.environ.get("ECHOMEM_CHAT_API_KEY") or embedding_token).strip()
    neo4j_uri = str(os.environ.get("ECHOMEM_NEO4J_URI") or "bolt://127.0.0.1:7687").strip()
    neo4j_username = str(os.environ.get("ECHOMEM_NEO4J_USERNAME") or "neo4j").strip()
    neo4j_password = str(os.environ.get("ECHOMEM_NEO4J_PASSWORD") or "").strip()
    neo4j_database = str(os.environ.get("ECHOMEM_NEO4J_DATABASE") or "neo4j").strip()
    graph_enabled = _env_bool("ECHOMEM_GRAPH_ENABLED", bool(neo4j_password))
    llm_provider = "fake" if fallback_to_mock else "openai_compatible"
    embedding_provider = "fake" if (fallback_to_mock or fallback_to_mock_embedding_only) else "openai_compatible"
    recall_routers = _normalize_recall_routers(
        recall_routers if recall_routers is not None else os.environ.get("ECHOMEM_DEVELOP_RECALL_ROUTERS"),
        default=("template-2", "llm"),
    )
    text_scan_max_files = int(os.environ.get("ECHOMEM_TEXT_SCAN_MAX_FILES") or 5000)
    text_scan_timeout_seconds = float(os.environ.get("ECHOMEM_TEXT_SCAN_TIMEOUT_SECONDS") or 10)
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
            "enabled": [engine_id],
            "configs": {
                engine_id: {
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
                    "graph": {
                        "enabled": graph_enabled,
                        "backend": "neo4j",
                        "neo4j": {
                            "uri": neo4j_uri,
                            "username": neo4j_username,
                            "password": neo4j_password,
                            "database": neo4j_database,
                            "auto_create_schema": True,
                            "pool": {
                                "max_size": int(
                                    os.environ.get("ECHOMEM_NEO4J_POOL_MAX_SIZE") or 100
                                ),
                                "max_connection_lifetime": int(
                                    os.environ.get("ECHOMEM_NEO4J_MAX_CONNECTION_LIFETIME")
                                    or 3600
                                ),
                            },
                            "retry": {
                                "max_attempts": int(
                                    os.environ.get("ECHOMEM_NEO4J_RETRY_MAX_ATTEMPTS") or 3
                                ),
                            },
                        },
                    },
                    "search": {
                        "text_scan": {
                            "max_files": text_scan_max_files,
                            "timeout_seconds": text_scan_timeout_seconds,
                        },
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
                        "fallback_to_mock_embedding_only": bool(fallback_to_mock_embedding_only),
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
    fallback_to_mock_embedding_only: bool = False,
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
            fallback_to_mock_embedding_only=fallback_to_mock_embedding_only,
            recall_routers=recall_routers,
        )
    return _write_legacy_echomem_config(
        out_dir,
        account,
        workspace,
        root,
        fallback_to_mock=fallback_to_mock,
        fallback_to_mock_embedding_only=fallback_to_mock_embedding_only,
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


def echomem_transport_mode(base_url: str = "", explicit_mode: str = "") -> str:
    mode = str(explicit_mode or "").strip().lower()
    if mode in {"http", "local"}:
        return mode
    if str(base_url or "").strip():
        return "http"
    return "local"


def _http_cache_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".echomem_http_auth_keys.json"


def _http_cache_key(base_url: str, account: str, user_id: str) -> str:
    raw = "\n".join([
        str(base_url or "").strip().rstrip("/"),
        str(account or "default").strip() or "default",
        str(user_id or "default").strip() or "default",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_http_auth_cache(workspace: str | Path) -> dict[str, Any]:
    path = _http_cache_path(workspace)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_http_auth_cache(workspace: str | Path, data: dict[str, Any]) -> None:
    path = _http_cache_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


def _load_workspace_auth_jsonl(workspace: str | Path, name: str) -> list[dict[str, Any]]:
    path = Path(workspace).expanduser().resolve() / "auth" / f"{name}.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if isinstance(data, dict):
                    records.append(data)
    except Exception:
        return []
    return records


def _resolve_existing_workspace_identity(
    workspace: str | Path,
    account: str,
    user_id: str,
) -> tuple[str, str] | None:
    tenants = _load_workspace_auth_jsonl(workspace, "tenants")
    users = _load_workspace_auth_jsonl(workspace, "users")
    account_text = str(account or "").strip()
    user_text = str(user_id or "").strip()
    if not account_text or not user_text:
        return None

    def _active(record: dict[str, Any]) -> bool:
        return str(record.get("status") or "active").strip().lower() == "active"

    exact_tenant_ids = [
        str(record.get("tenant_id") or "").strip()
        for record in tenants
        if _active(record) and str(record.get("tenant_id") or "").strip() == account_text
    ]
    named_tenant_ids = [
        str(record.get("tenant_id") or "").strip()
        for record in tenants
        if _active(record)
        and str(record.get("tenant_id") or "").strip()
        and str(record.get("name") or "").strip() == account_text
    ]
    candidate_tenant_ids = exact_tenant_ids + [tenant_id for tenant_id in named_tenant_ids if tenant_id not in exact_tenant_ids]
    active_user_keys = {
        (
            str(record.get("tenant_id") or "").strip(),
            str(record.get("user_id") or "").strip(),
        )
        for record in users
        if _active(record)
    }
    for tenant_id in candidate_tenant_ids:
        if (tenant_id, user_text) in active_user_keys:
            return tenant_id, user_text
    return None


def _http_json_request(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
    auth_key: str = "",
) -> dict[str, Any]:
    url = f"{str(base_url).strip().rstrip('/')}{path}"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if auth_key:
        headers["X-Auth-Key"] = auth_key
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_s or 60.0))) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = ""
        if raw:
            try:
                body_data = json.loads(raw.decode("utf-8", errors="replace"))
                detail = str(body_data.get("message") or body_data.get("error") or "")
            except Exception:
                detail = raw.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"EchoMemory HTTP {exc.code} for {path}") from exc
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def ensure_echomem_http_auth_key(
    *,
    base_url: str,
    auth_key: str = "",
    account: str,
    user_id: str,
    workspace: str | Path,
    timeout_s: float = 60.0,
) -> tuple[str, dict[str, Any]]:
    explicit_key = str(auth_key or "").strip()
    if explicit_key:
        return explicit_key, {"source": "explicit", "auth_key_present": True}
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    if not normalized_base_url:
        raise ValueError("EchoMemory HTTP auth requires a base_url")
    account_text = str(account or "default").strip() or "default"
    user_text = str(user_id or "default").strip() or "default"
    cache = _load_http_auth_cache(workspace)
    cache_entries = cache.setdefault("entries", {})
    key = _http_cache_key(normalized_base_url, account_text, user_text)
    preferred_identity = _resolve_existing_workspace_identity(workspace, account_text, user_text)
    cached = cache_entries.get(key) if isinstance(cache_entries, dict) else None
    if isinstance(cached, dict) and str(cached.get("auth_key") or "").strip():
        cached_tenant_id = str(cached.get("tenant_id") or "").strip()
        cached_user_id = str(cached.get("api_user_id") or "").strip()
        if not preferred_identity or (cached_tenant_id, cached_user_id) == preferred_identity:
            return str(cached["auth_key"]).strip(), {
                "source": "cache",
                "auth_key_present": True,
                "tenant_id": cached_tenant_id,
                "user_id": cached_user_id,
                "cache_path": str(_http_cache_path(workspace)),
            }

    if preferred_identity:
        tenant_id, api_user_id = preferred_identity
        key_payload = _http_json_request(
            normalized_base_url,
            "POST",
            f"/api/auth/tenants/{urllib.parse.quote(tenant_id, safe='')}/users/{urllib.parse.quote(api_user_id, safe='')}/key",
            payload={},
            timeout_s=timeout_s,
        )
        issued_key = str(key_payload.get("auth_key") or "").strip()
        if not issued_key:
            raise RuntimeError("EchoMemory HTTP auth did not return auth_key")
        cache_entries[key] = {
            "base_url": normalized_base_url,
            "account": account_text,
            "requested_user_id": user_text,
            "tenant_id": tenant_id,
            "api_user_id": api_user_id,
            "auth_key": issued_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_http_auth_cache(workspace, cache)
        return issued_key, {
            "source": "workspace_existing",
            "auth_key_present": True,
            "tenant_id": tenant_id,
            "user_id": api_user_id,
            "cache_path": str(_http_cache_path(workspace)),
        }

    tenant_payload = _http_json_request(
        normalized_base_url,
        "POST",
        "/api/auth/tenants",
        payload={"name": account_text},
        timeout_s=timeout_s,
    )
    tenant = tenant_payload.get("tenant") or {}
    tenant_id = str(tenant.get("tenant_id") or "").strip()
    if not tenant_id:
        raise RuntimeError("EchoMemory HTTP auth did not return tenant_id")
    user_payload = _http_json_request(
        normalized_base_url,
        "POST",
        f"/api/auth/tenants/{urllib.parse.quote(tenant_id, safe='')}/users",
        payload={},
        timeout_s=timeout_s,
    )
    user = user_payload.get("user") or {}
    api_user_id = str(user.get("user_id") or "").strip()
    if not api_user_id:
        raise RuntimeError("EchoMemory HTTP auth did not return user_id")
    key_payload = _http_json_request(
        normalized_base_url,
        "POST",
        f"/api/auth/tenants/{urllib.parse.quote(tenant_id, safe='')}/users/{urllib.parse.quote(api_user_id, safe='')}/key",
        payload={},
        timeout_s=timeout_s,
    )
    issued_key = str(key_payload.get("auth_key") or "").strip()
    if not issued_key:
        raise RuntimeError("EchoMemory HTTP auth did not return auth_key")
    cache_entries[key] = {
        "base_url": normalized_base_url,
        "account": account_text,
        "requested_user_id": user_text,
        "tenant_id": tenant_id,
        "api_user_id": api_user_id,
        "auth_key": issued_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_http_auth_cache(workspace, cache)
    return issued_key, {
        "source": "created",
        "auth_key_present": True,
        "tenant_id": tenant_id,
        "user_id": api_user_id,
        "cache_path": str(_http_cache_path(workspace)),
    }


class EchoMemHTTPCompatSDK:
    def __init__(
        self,
        *,
        base_url: str,
        auth_key: str = "",
        account: str,
        user_id: str,
        agent_id: str,
        workspace: str | Path,
        timeout_s: float = 60.0,
        auto_auth: bool = True,
    ) -> None:
        self._base_url = str(base_url).strip().rstrip("/")
        self._account = account or "default"
        self._user_id = user_id or "default"
        self._agent_id = agent_id or "default"
        self._workspace = Path(workspace).expanduser().resolve()
        self._timeout_s = max(1.0, float(timeout_s or 60.0))
        self._request_counts: dict[str, int] = {}
        explicit_auth_key = str(auth_key or "").strip()
        if explicit_auth_key or auto_auth:
            self._auth_key, self._auth_info = ensure_echomem_http_auth_key(
                base_url=self._base_url,
                auth_key=explicit_auth_key,
                account=self._account,
                user_id=self._user_id,
                workspace=self._workspace,
                timeout_s=self._timeout_s,
            )
        else:
            self._auth_key = ""
            self._auth_info = {
                "source": "anonymous",
                "auth_key_present": False,
                "user_id": self._user_id,
            }
        self._compat_layout = "http"

    def _ctx(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    def _ctx_value(self, ctx: dict[str, Any] | None, key: str, default: str = "") -> str:
        if isinstance(ctx, dict):
            value = str(ctx.get(key) or "").strip()
            if value:
                return value
        return default

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self._auth_key:
            headers["X-Auth-Key"] = self._auth_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = path if path.startswith("http://") or path.startswith("https://") else f"{self._base_url}{path}"
        audit_path = urllib.parse.urlsplit(url).path or "/"
        audit_key = f"{method.upper()} {audit_path}"
        self._request_counts[audit_key] = self._request_counts.get(audit_key, 0) + 1
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"
        body = None
        headers = self._headers()
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            detail = ""
            if raw:
                try:
                    detail = str(json.loads(raw.decode("utf-8", errors="replace")).get("error") or "")
                except Exception:
                    detail = raw.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"EchoMemory HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"EchoMemory HTTP unavailable at {self._base_url}: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"EchoMemory HTTP returned non-JSON for {path}") from exc
        return data if isinstance(data, dict) else {}

    async def _request_async(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._request, method, path, payload=payload, params=params)

    async def create_session(self, title: str = "", ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = self._ctx_value(ctx, "session_id", "")
        data = await self._request_async(
            "POST",
            "/api/sessions/open",
            payload={
                "agent_id": self._ctx_value(ctx, "agent_id", self._agent_id),
                "session_id": session_id or None,
                "metadata": {
                    "title": title,
                    "account_id": self._ctx_value(ctx, "account_id", self._account),
                    "user_id": self._ctx_value(ctx, "user_id", self._user_id),
                },
            },
        )
        scope = data.get("scope") or {}
        return {
            "session_id": str(scope.get("session_id") or session_id or ""),
            "agent_id": self._ctx_value(ctx, "agent_id", self._agent_id),
            "user_id": self._ctx_value(ctx, "user_id", self._user_id),
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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # EchoMem accepts created_at as a top-level field. Avoid putting duplicate
        # speaker/time fields into metadata; caller-supplied metadata should hold
        # application-specific provenance such as dia_id / session_key.
        payload: dict[str, Any] = {
            "role": role,
            "content": content,
            "name": role_id or None,
            "created_at": created_at or None,
        }
        if metadata:
            payload["metadata"] = metadata
        data = await self._request_async(
            "POST",
            f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/messages",
            payload=payload,
        )
        message = data.get("message") or {}
        return {
            "message_id": str(message.get("id") or ""),
            "role": str(message.get("role") or role),
            "content": str(message.get("content") or content),
        }

    async def commit_session(
        self,
        session_id: str,
        *,
        ctx: dict[str, Any] | None = None,
        keep_recent_count: int = 0,
    ) -> Any:
        data = await self._request_async(
            "POST",
            f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/commit",
            payload={"metadata": {"keep_recent_count": int(keep_recent_count or 0)}},
        )
        result = data.get("result") or {}
        archive_id = str(result.get("archive_id") or "")
        status = str(result.get("status") or "pending")
        task_id = str(result.get("commit_id") or archive_id or f"commit-{session_id}")
        return SimpleNamespace(task_id=task_id, archive_id=archive_id, status=status)

    async def commit_status(
        self,
        session_id: str,
        archive_id: str,
        *,
        ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            payload = await self._request_async(
                "GET",
                f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/commits/{urllib.parse.quote(archive_id, safe='')}",
            )
        except Exception:
            payload = {}
        state = payload.get("status") if isinstance(payload, dict) else {}
        if not isinstance(state, dict):
            state = {}
        return state

    async def get_history(self, session_id: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        data = await self._request_async(
            "GET",
            f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/history?limit=200",
        )
        history_payload = data.get("history") or {}
        history = history_payload.get("messages") if isinstance(history_payload, dict) else history_payload
        if not isinstance(history, list):
            history = []
        rows: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            metadata = dict(item.get("metadata") or {})
            rows.append(
                {
                    "message_id": str(item.get("id") or ""),
                    "role": str(item.get("role") or ""),
                    "content": str(item.get("content") or ""),
                    "name": str(item.get("name") or ""),
                    "created_at": str(item.get("created_at") or metadata.get("created_at") or ""),
                    "role_id": str(metadata.get("role_id") or item.get("name") or ""),
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
        data = await self._request_async(
            "POST",
            "/api/retrieval/search",
            payload={
                "query": query,
                "agent_id": self._ctx_value(ctx, "agent_id", self._agent_id),
                "session_id": self._ctx_value(ctx, "session_id", "") or None,
                "limit": limit,
                "include_explain": False,
                "include_debug": True,
            },
        )
        result = data.get("result") or {}
        items = result.get("items") or []
        return SimpleNamespace(items=list(items) if isinstance(items, list) else [])

    async def fs_read(self, uri: str, *, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = str(uri or "").strip()
        if not raw:
            return {"content": ""}
        if raw.startswith("echo://"):
            account_prefix = f"echo://{self._account}/"
            if raw.startswith(account_prefix):
                raw = f"echo://{raw[len(account_prefix):]}"
        else:
            raw = f"echo://{raw.lstrip('/')}"
        data = await self._request_async("GET", "/fs/read", params={"uri": raw})
        return {"content": str((data.get("result") or {}).get("text") or "")}

    async def fs_list(self, uri: str, *, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raw = str(uri or "").strip()
        if not raw:
            return []
        data = await self._request_async("GET", "/fs/ls", params={"uri": raw})
        entries = (data.get("result") or {}).get("entries") or []
        return [dict(entry) for entry in entries if isinstance(entry, dict)]

    async def fs_glob(self, pattern: str, *, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raw = str(pattern or "").strip()
        if not raw:
            return []
        data = await self._request_async("POST", "/fs/glob", payload={"pattern": raw})
        entries = (data.get("result") or {}).get("entries") or []
        return [dict(entry) for entry in entries if isinstance(entry, dict)]

    def transport_audit(self) -> dict[str, Any]:
        return {
            "transport": "http",
            "base_url": self._base_url,
            "request_counts": dict(sorted(self._request_counts.items())),
            "local_workspace_evidence_reads": 0,
            "platform_neo4j_queries": 0,
        }

    async def close(self) -> None:
        return None


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

    def _tenant_context(self, ctx: dict[str, Any] | None = None) -> Any | None:
        runtime = self._runtime
        if runtime is None:
            return None
        account_id = self._ctx_value(ctx, "account_id", self._account)
        user_id = self._ctx_value(ctx, "user_id", self._user_id)
        try:
            from echomem.req_coordinator.interfaces.entities.tenant import TenantContext

            return TenantContext(tenant_id=account_id, user_id=user_id)
        except Exception:
            return None

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
            for engine_id in echomem_engine_candidates():
                path = root / "engines" / engine_id / "commits" / f"{commit_key}.status.json"
                data = load_echomem_config_file(path)
                if not data:
                    continue
                data.setdefault("session_id", session_id)
                data.setdefault("archive_id", archive_id)
                data.setdefault("commit_id", str(data.get("commit_id") or archive_id))
                data.setdefault("accepted_at", str(data.get("updated_at") or ""))
                data.setdefault("committed_at", str(data.get("updated_at") or ""))
                data.setdefault("stage_detail", dict(data.get("detail") or {}))
                data.setdefault("engine_id", engine_id)
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
        tenant = self._tenant_context(ctx)
        if tenant is not None and self._client_core is not None:
            def _search_with_tenant() -> Any:
                return self._client_core._retrieval_service.retrieve(
                    __import__("echomem.memrouter.recall.interfaces.entities", fromlist=["RetrievalReq"]).RetrievalReq(
                        query=query,
                        user_id=self._ctx_value(ctx, "user_id", self._user_id),
                        agent_id=self._ctx_value(ctx, "agent_id", self._agent_id),
                        session_id=self._ctx_value(ctx, "session_id", "") or None,
                        limit=limit,
                        include_explain=False,
                        include_debug=False,
                    ),
                    tenant=tenant,
                )

            result = await asyncio.to_thread(_search_with_tenant)
            if inspect.isawaitable(result):
                result = await result
            items = list(getattr(result, "items", []) or [])
        else:
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
        if not raw:
            return {"content": ""}
        account = self._ctx_value(ctx, "account_id", self._account)
        if raw.startswith("/"):
            raw = f"echo://{raw.lstrip('/')}"
        elif not raw.startswith("echo://"):
            raw = f"echo://{raw.lstrip('/')}"

        account_prefix = f"echo://{account}/"
        if raw.startswith(account_prefix):
            raw = f"echo://{raw[len(account_prefix):]}"

        tail = raw[len("echo://") :] if raw.startswith("echo://") else raw
        parts = tail.split("/")
        if parts and parts[0] == "sessions" and len(parts) >= 3:
            resolved_uri = (
                f"echo://engine/{echomem_engine_id()}/"
                + "/".join(parts)
            )
            return {
                "content": await self._client.fs_read(resolved_uri),
                "resolved_uri": resolved_uri,
            }

        if parts and parts[0] == "memory" and len(parts) >= 3:
            filename = parts[-1]
            parent = "/".join(parts[:-1])
            pattern = (
                f"echo://engine/{echomem_engine_id()}/{parent}/**/{filename}"
            )
            matches = await self._client.fs_glob(pattern)
            if not matches:
                return {"content": ""}
            resolved_uri = str(getattr(matches[0], "uri", "") or "")
            return {
                "content": await self._client.fs_read(resolved_uri),
                "resolved_uri": resolved_uri,
            }

        # Search results use a session URI as provenance for inline atomic
        # content. The URI names a directory rather than a readable memory
        # file, so preserve the inline atom instead of replacing it with the
        # entire source transcript.
        if parts and parts[0] == "sessions" and len(parts) == 2:
            return {"content": ""}

        return {"content": await self._client.fs_read(raw), "resolved_uri": raw}

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
    base_url: str = "",
    auth_key: str = "",
    transport_mode: str = "",
    http_timeout_s: float = 60.0,
    http_auto_auth: bool = True,
) -> tuple[Any, Any | None, str]:
    normalized_transport = echomem_transport_mode(base_url, transport_mode)
    if normalized_transport == "http":
        normalized_base_url = str(base_url or "").strip().rstrip("/")
        if not normalized_base_url:
            raise ValueError("EchoMemory HTTP mode requires --echomem-base-url")
        sdk = EchoMemHTTPCompatSDK(
            base_url=normalized_base_url,
            auth_key=auth_key,
            account=account,
            user_id=user_id,
            agent_id=agent_id,
            workspace=workspace,
            timeout_s=http_timeout_s,
            auto_auth=http_auto_auth,
        )
        print(
            f"[sdk-open] transport=http base_url={normalized_base_url} workspace={workspace} "
            f"account={account} user_id={user_id} agent_id={agent_id} "
            f"auth_source={sdk._auth_info.get('source', 'unknown')}",
            flush=True,
        )
        return sdk, None, "http"
    root = ensure_echomem_imports(echomem_root)
    layout = detect_echomem_layout(root)
    print(
        f"[sdk-open] root={root} layout={layout} workspace={workspace} account={account} "
        f"user_id={user_id} agent_id={agent_id} config={config_path}",
        flush=True,
    )
    if layout == "develop-src":
        from echomem.entrypoints.client.local.async_client import AsyncEchoMemLocalClient

        # SessionService commits when accumulated chars are greater than or
        # equal to this threshold, so zero means "commit every message", not
        # "disabled". Benchmarks explicitly commit each source session once.
        os.environ.setdefault("ECHOMEM_AUTO_COMMIT_THRESHOLD", str(2**63 - 1))
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
        "uri": (
            data.get("source_uri")
            or data.get("evidence_uri")
            or data.get("uri")
            or data.get("path")
            or data.get("source")
            or ""
        ),
        "score": data.get("confidence") or data.get("score") or 0.0,
        "content": data.get("content") or data.get("text") or "",
        "memory_type": data.get("memory_type") or data.get("kind") or "",
        "evidence_uri": data.get("evidence_uri") or "",
        "source": data.get("source") or "",
        "path": data.get("path") or "",
        "trace": data.get("trace") or {},
        "backend": "echomemory",
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
