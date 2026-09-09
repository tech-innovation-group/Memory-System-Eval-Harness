"""Model/config preflight gate for acceptance stress runs.

Reads the engine configuration actually used, checks every ``api_key_env``
variable is set and non-empty, sends one minimal real request per
LLM/embedding endpoint and verifies the model name is accepted. Any failure
stops the run and is classified as an environment/dependency error, never
attributed to the code under test.

API keys are referenced by environment-variable name only; a SHA-256 digest
of the resolved configuration (endpoints and model names, no secrets) is
recorded for change detection before fault injection.

Config shape (JSON)::

    {"engines": [
        {"id": "atomic_engine", "kind": "llm", "api_key_env": "ARK_API_KEY",
         "api_base": "https://...", "model": "deepseek-v4-flash-0731"}
    ]}

A bare list of engine dicts is accepted too, as is the native EchoMem
nested config layout (providers stored below ``model``/``engine.configs``/
``recall.model`` rather than under a top-level ``engines`` array).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ("id", "kind", "api_base", "model")


def parse_engine_configs(path: str | Path) -> list[dict[str, Any]]:
    """Load and normalize flat or native EchoMem nested engine configs.

    Native configs store providers below ``model``, ``engine.configs`` and
    ``recall.model`` rather than under a top-level ``engines`` array.  Official
    runs must preflight that exact file, so discover provider-shaped mappings
    recursively while retaining the original flat ``engines`` format.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        entries = raw.get("engines")
        if entries:
            candidates = [
                (str(entry.get("id") or f"engine-{index}"), entry)
                for index, entry in enumerate(entries)
            ]
        else:
            candidates = []

            def visit(value: Any, path_parts: tuple[str, ...] = ()) -> None:
                if isinstance(value, dict):
                    # Optional model branches such as recall.model.rerank can
                    # remain in the native config while disabled.  A disabled
                    # branch is not part of the effective runtime and must not
                    # block a real-model preflight on a missing credential.
                    if value.get("enabled") is False:
                        return
                    if value.get("api_base") and value.get("model"):
                        candidates.append((
                            ".".join(path_parts) or "engine",
                            value,
                        ))
                    for key, child in value.items():
                        visit(child, (*path_parts, str(key)))
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        visit(child, (*path_parts, str(index)))

            visit(raw)
            if not candidates:
                candidates = [("engine", raw)]
    elif isinstance(raw, list):
        candidates = [
            (str(entry.get("id") or f"engine-{index}"), entry)
            for index, entry in enumerate(raw)
        ]
    else:
        raise ValueError("engine config 必须是 JSON 对象或对象数组")  # noqa: TRY004
    # Native configs may contain providers for optional branches that are not
    # active in the selected runtime. Preflighting an inactive provider can
    # stop an otherwise valid run (for example an intent LLM while the
    # configured intent backend is "rule").
    intent_backends: list[str] = []

    def collect_intent_backends(value: Any, path_parts: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            if (
                len(path_parts) >= 2
                and path_parts[-2:] == ("search", "intent")
                and value.get("backend")
            ):
                intent_backends.append(str(value["backend"]).lower())
            for key, child in value.items():
                collect_intent_backends(child, (*path_parts, str(key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect_intent_backends(child, (*path_parts, str(index)))

    collect_intent_backends(raw)
    intent_backend = next(
        (backend for backend in intent_backends if backend),
        "",
    )

    def is_active(candidate_id: str) -> bool:
        if candidate_id.lower().startswith("recall.model.intent_llm"):
            layers = (raw.get("recall") or {}).get("intent_recognition_layers") if isinstance(raw, dict) else None
            if isinstance(layers, list):
                return "llm" in layers
            return intent_backend in {"", "llm", "model", "openai_compatible"}
        return True

    engines: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate_id, entry in candidates:
        if not is_active(candidate_id):
            continue
        if not isinstance(entry, dict):
            raise ValueError("engine config 条目必须是 JSON 对象")  # noqa: TRY004
        # Native EchoMem configs also contain fake VLM and model-only
        # templates. They are not real provider endpoints and must not make
        # a real-model preflight fail.
        if not str(entry.get("api_base") or "").strip():
            continue
        if not str(entry.get("model") or "").strip():
            continue
        provider = str(entry.get("provider") or "").lower()
        if provider in {"fake", "mock"}:
            continue
        explicit_kind = str(entry.get("kind") or "").lower()
        kind = explicit_kind if explicit_kind in {"llm", "embedding", "rerank"} else ("embedding" if any(
            token in candidate_id.lower()
            for token in ("embedding", "vector", "query_embedding")
        ) else "llm")
        if not explicit_kind and "rerank" in candidate_id.lower():
            kind = "rerank"
        engine_id = str(entry.get("id") or candidate_id)
        api_key_env = str(entry.get("api_key_env") or "")
        api_base = str(entry["api_base"]).rstrip("/")
        model = str(entry["model"])
        extra_params = dict(entry.get("extra_params") or {})
        identity = (engine_id, kind, api_key_env,
                    api_base + "\0" + model + "\0" + json.dumps(extra_params, sort_keys=True))
        if identity in seen:
            continue
        seen.add(identity)
        engines.append(
            {
                "id": engine_id,
                "kind": kind,
                "api_key_env": api_key_env,
                "api_base": api_base,
                "model": model,
                "_api_key": str(entry.get("api_key") or ""),
                "extra_params": extra_params,
            }
        )
    if not engines and raw and isinstance(raw, (dict, list)):
        raise ValueError("engine config 未发现可预检的真实 provider endpoint")
    return engines


def config_digest(engines: list[dict[str, Any]]) -> str:
    """SHA-256 over endpoints/model names (never over API key values)."""
    public = [{k: v for k, v in e.items() if k != "_api_key"} for e in engines]
    canonical = json.dumps(public, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def check_env(engines: list[dict[str, Any]]) -> list[str]:
    """Every referenced ``api_key_env`` variable must exist and be non-empty."""
    errors: list[str] = []
    for engine in engines:
        env_name = engine["api_key_env"]
        if not env_name:
            continue
        if not os.environ.get(env_name, "").strip():
            errors.append(
                f"{engine['id']}: 环境变量 {env_name} 不存在或为空"
            )
    return errors


def probe_endpoint(engine: dict[str, Any], *, timeout_s: float = 20.0) -> dict[str, Any]:
    """One minimal real request against the engine endpoint.

    ``kind=llm`` probes ``{api_base}/chat/completions``; ``kind=embedding``
    probes ``{api_base}/embeddings``. A 2xx response means the model name is
    accepted; any HTTP error or timeout marks the engine as failed.
    """
    api_key = os.environ.get(engine["api_key_env"], "") if engine["api_key_env"] else engine.get("_api_key", "")
    if engine["kind"] == "embedding":
        path = "/embeddings"
        body: dict[str, Any] = {"model": engine["model"], "input": "ping"}
    elif engine["kind"] == "rerank":
        path = "/reranks"
        body = {"model": engine["model"], "query": "meeting location",
                "documents": ["The meeting is in room Cedar."],
                "instruct": "Find documents relevant to the query."}
    else:
        path = "/chat/completions"
        body = {**engine.get("extra_params", {}), **{
            "model": engine["model"],
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }}
    url = f"{engine['api_base']}{path}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
        if not valid_model_response(engine["kind"], payload,
                                    require_content="intent_llm" in engine["id"].lower()):
            raise ValueError("invalid model response")
        return {
            "id": engine["id"],
            "kind": engine["kind"],
            "api_base": engine["api_base"],
            "model": engine["model"],
            "model_supported": True,
            "status": "ok",
            "code": resp.status,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": "",
        }
    except urllib.error.HTTPError as exc:
        return {
            "id": engine["id"],
            "kind": engine["kind"],
            "api_base": engine["api_base"],
            "model": engine["model"],
            "model_supported": False,
            "status": "error",
            "code": exc.code,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": f"HTTP {exc.code}（模型 {engine['model']} 可能不被该 endpoint 支持）",
        }
    except (ValueError, UnicodeError):
        return {"id": engine["id"], "kind": engine["kind"], "api_base": engine["api_base"],
                "model": engine["model"], "model_supported": False, "status": "error",
                "code": 200, "elapsed_s": round(time.monotonic() - started, 3),
                "error": "Provider returned an invalid LLM/embedding payload; HTTP success is insufficient"}
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        return {
            "id": engine["id"],
            "kind": engine["kind"],
            "api_base": engine["api_base"],
            "model": engine["model"],
            "model_supported": False,
            "status": "error",
            "code": None,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": f"endpoint 不可达/超时: {type(exc).__name__}",
        }


def valid_model_response(kind: str, payload: Any, *, require_content: bool = False) -> bool:
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    if kind == "rerank":
        results = payload.get("results")
        return isinstance(results, list) and bool(results) and all(
            isinstance(row, dict) and row.get("index") == 0
            and type(row.get("relevance_score")) in (int, float)
            and math.isfinite(row["relevance_score"]) for row in results)
    if kind == "embedding":
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return False
        vector = data[0].get("embedding")
        return isinstance(vector, list) and bool(vector) and all(
            type(v) in (int, float) and math.isfinite(v) for v in vector)
    choices = payload.get("choices")
    return isinstance(choices, list) and bool(choices) and all(
        isinstance(c, dict) and isinstance(c.get("message"), dict)
        and any(isinstance(c["message"].get(k), str) and bool(c["message"][k])
                for k in (("content",) if require_content else ("content", "reasoning_content"))) for c in choices)


def run_preflight(
    config_path: str | Path,
    *,
    timeout_s: float = 20.0,
    retry_attempts: int = 3,
    retry_backoff_s: float = 1.0,
    required_kinds: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the full preflight gate; returns a structured, secret-free result.

    ``ok`` is True only when every engine passes environment and real-request
    checks. On failure the caller must stop the run and classify the result
    as an environment/dependency error. Transient transport failures (name
    resolution, timeouts, connection refused) are retried up to
    ``retry_attempts`` times with linear backoff; deterministic HTTP errors
    are never retried.
    """
    try:
        engines = parse_engine_configs(config_path)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "error": f"配置读取失败: {exc}",
            "engines_checked": 0,
            "engines": [],
            "digest": "",
        }
    env_errors = check_env(engines)
    missing = sorted(set(required_kinds) - {e["kind"] for e in engines})
    if not engines or missing or env_errors:
        return {"ok": False, "error": "; ".join(env_errors) or "Missing real provider kinds: " + ", ".join(missing or ["any"]),
                "engines_checked": 0, "engines": [], "digest": config_digest(engines), "probe_attempts": 0}
    attempts = max(1, int(retry_attempts))
    probes: list[dict[str, Any]] = []
    attempts_used = 0
    for attempt in range(1, attempts + 1):
        attempts_used = attempt
        probes = [probe_endpoint(engine, timeout_s=timeout_s) for engine in engines]
        failures = [entry for entry in probes if entry["status"] != "ok"]
        if not failures or not _retryable_probe_failure(failures[0]):
            break
        if attempt < attempts:
            time.sleep(max(0.0, float(retry_backoff_s)) * attempt)
    failures = [entry for entry in probes if entry["status"] != "ok"]
    if env_errors:
        return {
            "ok": False,
            "error": "; ".join(env_errors),
            "engines_checked": len(engines),
            "engines": probes,
            "digest": config_digest(engines),
            "probe_attempts": attempts_used,
        }
    if failures:
        return {
            "ok": False,
            "error": failures[0]["error"],
            "engines_checked": len(engines),
            "engines": probes,
            "digest": config_digest(engines),
            "probe_attempts": attempts_used,
        }
    return {
        "ok": True,
        "error": "",
        "engines_checked": len(engines),
        "engines": probes,
        "digest": config_digest(engines),
        "probe_attempts": attempts_used,
    }


def _retryable_probe_failure(entry: dict[str, Any]) -> bool:
    """Retry only transient transport failures, never deterministic HTTP errors."""
    if entry.get("code") is not None:
        return False
    error = str(entry.get("error") or "").lower()
    return any(
        marker in error
        for marker in (
            "nodename nor servname",
            "name or service not known",
            "temporary failure in name resolution",
            "timed out",
            "connection refused",
            "network is unreachable",
            "urlopen error",
        )
    )
