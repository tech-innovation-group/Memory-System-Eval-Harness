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
from typing import Any


def looks_like_echomem_root(path: Path) -> bool:
    return (
        (path / "packages" / "echomem" / "src").exists()
        or ((path / "echomem").exists() and (path / "pyproject.toml").exists())
    )


def default_echomem_root() -> Path:
    candidates = [
        os.environ.get("ECHOMEM_ROOT"),
        os.environ.get("ECHOMEMORY_ROOT"),
        Path.home() / "Code" / "echomemory" / "echo_memory_v010",
        Path.home() / "Code" / "echomemory" / "echo_memory",
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


def echomem_account_roots(workspace: str | Path, account: str) -> list[Path]:
    workspace_path = Path(workspace).expanduser().resolve()
    candidates = [
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
    for root in echomem_account_roots(workspace, account):
        token_dir = root / "metrics" / "llm_tokens"
        if not token_dir.exists():
            continue
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
