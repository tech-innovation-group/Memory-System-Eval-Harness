from __future__ import annotations

import io
import base64
import csv
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import docker
from docker.errors import DockerException, ImageNotFound
import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for


DATA_DIR = Path(os.getenv("WEB_DATA_DIR", "/data"))
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/results"))
# Docker is reached through the host socket. A bind source must therefore be
# a host path, not the Web container's /results mount point.
DOCKER_RESULTS_DIR = Path(
    os.getenv(
        "HOST_RESULTS_DIR",
        "/opt/memory-eval-harness/results"
        if str(RESULTS_DIR) == "/results"
        else str(RESULTS_DIR),
    )
)
RESULT_ARCHIVE_DIR = Path(
    os.getenv("RESULT_ARCHIVE_DIR", str(RESULTS_DIR / "_archives"))
)
RESULT_RETENTION_DAYS = int(os.getenv("RESULT_RETENTION_DAYS", "3"))
RESULT_CLEANUP_INTERVAL_S = int(
    os.getenv("RESULT_CLEANUP_INTERVAL_S", "3600")
)
SOURCE_ROOT = Path(os.getenv("SOURCE_ROOT", "/opt/memory-eval-sources"))
CACHE_ROOT = Path(os.getenv("SOURCE_CACHE_ROOT", str(SOURCE_ROOT / "_cache")))
ECHOMEM_WORKSPACE_CACHE = Path(
    os.getenv("ECHOMEM_WORKSPACE_CACHE", "/opt/memory-eval-web/cache")
)


def echomem_job_cache(job_id: str) -> Path:
    """Return a cache directory isolated to one evaluation task."""
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(job_id)).strip("_")
    return ECHOMEM_WORKSPACE_CACHE / "jobs" / (safe_id or "unknown")


def prepare_echomem_job_cache(job_id: str) -> Path:
    """Create an isolated workspace cache, reusing only immutable embeddings."""
    job_cache = echomem_job_cache(job_id)
    # job_cache is mounted at /workspace/cache inside EchoMem.
    recall_cache = job_cache / "recall"
    recall_cache.mkdir(parents=True, exist_ok=True)
    shared_recall_cache = ECHOMEM_WORKSPACE_CACHE / "recall"
    # Reuse immutable vector warm-ups only; memory state remains task-local.
    for cache_name in ("semantic_embeddings.json", "template_embeddings.json"):
        shared_cache = shared_recall_cache / cache_name
        task_cache = recall_cache / cache_name
        if (
            shared_cache.is_file()
            and shared_cache.stat().st_size > 0
            and not task_cache.exists()
        ):
            shutil.copy2(shared_cache, task_cache)
    task_embedding_cache = recall_cache / "semantic_embeddings.json"
    if task_embedding_cache.is_file():
        # EchoMem validates the cache against the configured embedding model.
        # Older server caches may contain the same vectors but stale metadata.
        try:
            payload = json.loads(task_embedding_cache.read_text(encoding="utf-8"))
            identity = {
                "model": os.getenv("DEFAULT_EMBEDDING_MODEL", "text-embedding-v3"),
                "dimensions": 1024,
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    identity, ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
            ).hexdigest()
            if (
                isinstance(payload, dict)
                and isinstance(payload.get("entries"), dict)
                and payload.get("fingerprint") != fingerprint
            ):
                payload["fingerprint"] = fingerprint
                task_embedding_cache.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return job_cache
ECHOMEM_REPO = os.getenv(
    "ECHOMEM_REPO",
    "https://github.com/tech-innovation-group/EchoMem.git",
)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
RUN_UID = os.getenv("RUN_UID", "1000")
RUN_GID = os.getenv("RUN_GID", "1000")
IMAGE = os.getenv("EVAL_IMAGE", "memory-eval-runner:local")
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
FEISHU_ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY", "").strip()
FEISHU_BITABLE_APP_TOKEN = os.getenv("FEISHU_BITABLE_APP_TOKEN", "").strip()
FEISHU_BITABLE_TABLE_ID = os.getenv("FEISHU_BITABLE_TABLE_ID", "").strip()
FEISHU_UPLOAD_USER = os.getenv("FEISHU_UPLOAD_USER", "").strip()
FEISHU_UPLOAD_NOTE = os.getenv("FEISHU_UPLOAD_NOTE", "").strip()
DEFAULT_LLM_BASE_URL = os.getenv("DEFAULT_LLM_BASE_URL", "")
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "")
DEFAULT_LLM_API_KEY = os.getenv("DEFAULT_LLM_API_KEY", "")
DEFAULT_EMBEDDING_API_KEY = os.getenv(
    "DEFAULT_EMBEDDING_API_KEY",
    "",
).strip()
if not DEFAULT_EMBEDDING_API_KEY:
    _embedding_key_file = Path(
        os.getenv("EMBEDDING_API_KEY_FILE", "/data/embedding_api_key")
    )
    try:
        DEFAULT_EMBEDDING_API_KEY = _embedding_key_file.read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        DEFAULT_EMBEDDING_API_KEY = ""
DEFAULT_EMBEDDING_BASE_URL = os.getenv(
    "DEFAULT_EMBEDDING_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
).strip()
HARNESS_API_BASE = os.getenv("HARNESS_API_BASE", "http://127.0.0.1:3082").rstrip("/")
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()
# Keep the deterministic Feishu command path as the default. Harness can be
# enabled later without changing the PR/develop job implementation.
HARNESS_ENABLED = os.getenv("HARNESS_ENABLED", "0").lower() not in {"0", "false", "no"}
ECHOMEM_IMAGE_PREFIX = os.getenv(
    "ECHOMEM_IMAGE_PREFIX",
    "memory-eval-echomem",
)
ECHOMEM_CONFIG_REVISION = (
    os.getenv("ECHOMEM_CONFIG_REVISION", "11") + "-mcp-required"
)
ECHOMEM_CONFIG_MODEL = os.getenv(
    "ECHOMEM_CONFIG_MODEL",
    DEFAULT_LLM_MODEL or "deepseek-v4-flash-0731",
).strip()
ECHOMEM_BUILD_TIMEOUT_S = int(os.getenv("ECHOMEM_BUILD_TIMEOUT_S", "1800"))
PIP_INDEX_URL = os.getenv(
    "PIP_INDEX_URL",
    "https://mirrors.aliyun.com/pypi/simple",
)
ECHOMEM_HTTP_PORT = int(os.getenv("ECHOMEM_HTTP_PORT", "18160"))
ECHOMEM_MCP_PORT = int(os.getenv("ECHOMEM_MCP_PORT", "18161"))
ECHOMEM_WORKSPACE = os.getenv("ECHOMEM_WORKSPACE", "/workspace").rstrip("/") or "/workspace"
ECHOMEM_AUTO_COMMIT_THRESHOLD = os.getenv(
    "ECHOMEM_AUTO_COMMIT_THRESHOLD",
    "20000",
).strip()
ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE = os.getenv(
    "ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE",
    "0.7",
).strip()
ECHOMEM_HEALTH_TIMEOUT_S = int(os.getenv("ECHOMEM_HEALTH_TIMEOUT_S", "300"))
ECHOMEM_HEALTH_REQUEST_TIMEOUT_S = float(
    os.getenv("ECHOMEM_HEALTH_REQUEST_TIMEOUT_S", "3")
)
ALLOWED_HOSTS = {
    item.strip()
    for item in os.getenv(
        "ALLOWED_HOSTS",
        "127.0.0.1,localhost",
    ).split(",")
    if item.strip()
}
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
public_host = urlparse(PUBLIC_BASE_URL).hostname
if public_host:
    # PUBLIC_BASE_URL is the operator's externally reachable address. Include
    # its hostname automatically so Feishu callbacks do not receive HTTP 400
    # merely because ALLOWED_HOSTS was left at its localhost default.
    ALLOWED_HOSTS.add(public_host)
MAX_JOBS = 50

if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET must be configured")

app = Flask(__name__)
app.secret_key = SESSION_SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_PATH = DATA_DIR / "jobs.json"
STARTUP_INCIDENTS_PATH = Path(
    os.getenv(
        "STARTUP_INCIDENTS_PATH",
        str(DATA_DIR / "skills" / "echomem-eval-startup" / "incidents.jsonl"),
    )
)
LOCK = threading.Lock()
JOB_QUEUE: queue.Queue[str] = queue.Queue()
SECRETS: dict[str, dict[str, str]] = {}
PHASE_LABELS = {
    "queued": "等待执行",
    "prepare": "准备 EchoMem 代码",
    "conflict": "存在合并冲突",
    "import": "导入记忆",
    "qa": "QA",
    "judge": "Judge",
    "completed": "已完成",
    "failed": "执行失败",
}
FEISHU_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0}
FEISHU_EVENT_IDS: set[str] = set()
FEISHU_LOCK = threading.Lock()
FEISHU_EVENT_LOG_PATH = DATA_DIR / "feishu-events.jsonl"
HARNESS_SESSION_MAP_PATH = DATA_DIR / "harness-feishu-sessions.json"
HARNESS_SESSION_LOCK = threading.Lock()


@app.before_request
def protect_internal_bridge() -> None:
    if not BRIDGE_TOKEN or not request.path.startswith("/api/bridge/"):
        return
    supplied = request.headers.get("X-Bridge-Token", "")
    if not secrets.compare_digest(supplied, BRIDGE_TOKEN):
        abort(401, "bridge authentication required")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_echo_config(
    config_path: Path,
) -> tuple[bytes, list[str]]:
    """Read the checkout config without changing its bytes or fields."""
    try:
        config_bytes = config_path.read_bytes()
        config = json.loads(config_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"无法读取或解析 EchoMem 配置 {config_path}: {exc}"
        ) from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"EchoMem 配置必须是 JSON 对象: {config_path}")

    api_key_envs: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "api_key_env" and isinstance(child, str) and child.strip():
                    env_name = child.strip()
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                        raise RuntimeError(
                            f"配置中的 api_key_env 不是合法环境变量名: {env_name}"
                        )
                    api_key_envs.add(env_name)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(config)
    return config_bytes, sorted(api_key_envs)


def enable_eval_mcp(config_bytes: bytes) -> bytes:
    """Enable MCP for the task-local EchoMem runtime config."""
    try:
        config = json.loads(config_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法解析 EchoMem 运行配置: {exc}") from exc
    mcp = config.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
        config["mcp"] = mcp
    mcp["enabled"] = True
    mcp["host"] = "0.0.0.0"
    mcp["port"] = 8001
    return json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")


def patch_echomem_config_model(config_path: Path, job_id: str) -> int:
    """Patch task-local model endpoints without mutating the source cache."""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 EchoMem 配置副本 {config_path}: {exc}") from exc
    llm_base_url = DEFAULT_LLM_BASE_URL.strip()
    llm_model = ECHOMEM_CONFIG_MODEL
    embedding_model = os.getenv("DEFAULT_EMBEDDING_MODEL", "text-embedding-v3").strip()
    changed = 0

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        nonlocal changed
        if isinstance(value, dict):
            provider = str(value.get("provider") or "").strip().lower()
            model = value.get("model")
            has_model_endpoint = isinstance(model, str) and (
                "api_base" in value or "api_key_env" in value
            )
            is_rerank = "rerank" in ".".join(path).lower()
            if has_model_endpoint and provider != "fake" and not is_rerank:
                is_embedding = (
                    "embedding" in ".".join(path).lower()
                    or "embedding" in model.lower()
                )
                target_model = embedding_model if is_embedding else llm_model
                # DeepSeek provides the chat model used by QA/Judge, while
                # text-embedding-v3 must keep its own embedding endpoint.
                target_base_url = (
                    DEFAULT_EMBEDDING_BASE_URL if is_embedding else llm_base_url
                )
                if target_base_url and value.get("api_base") != target_base_url:
                    value["api_base"] = target_base_url
                    changed += 1
                if target_model and value.get("model") != target_model:
                    value["model"] = target_model
                    changed += 1
            for key, child in value.items():
                visit(child, path + (str(key),))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + (str(index),))

    visit(config)
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    append_job_log(
        job_id,
        "已自动覆盖 EchoMem 任务模型配置: "
        f"llm={llm_model}, embedding={embedding_model}, "
        f"base_url={'configured' if llm_base_url else 'source-default'}, "
        f"changed_fields={changed}",
    )
    return changed


def read_jobs() -> list[dict[str, Any]]:
    if not JOBS_PATH.exists():
        return []
    try:
        return json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def write_jobs(jobs: list[dict[str, Any]]) -> None:
    temp = JOBS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(JOBS_PATH)


def active_job_count(jobs: list[dict[str, Any]]) -> int:
    """Count only jobs that still occupy queue/worker capacity."""
    return sum(
        1
        for job in jobs
        if str(job.get("status") or "").lower() in {"queued", "running"}
    )


def read_harness_session_map() -> dict[str, str]:
    if not HARNESS_SESSION_MAP_PATH.exists():
        return {}
    try:
        value = json.loads(HARNESS_SESSION_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_harness_session_map(value: dict[str, str]) -> None:
    temp = HARNESS_SESSION_MAP_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(HARNESS_SESSION_MAP_PATH)


def harness_rpc(method: str, payload: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    response = requests.post(
        f"{HARNESS_API_BASE}/api/{method}",
        headers={"Content-Type": "application/json"},
        json={
            "type": "client-request",
            "rpcId": uuid.uuid4().hex,
            "method": method,
            "payload": payload,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    result = body.get("result") or {}
    if not result.get("ok"):
        error = result.get("error") or {}
        raise RuntimeError(str(error.get("message") or f"Harness {method} 失败"))
    return result.get("value") or {}


def harness_session_for_chat(chat_id: str) -> str:
    with HARNESS_SESSION_LOCK:
        mapping = read_harness_session_map()
        existing = str(mapping.get(chat_id) or "")
        if existing:
            return existing
        created = harness_rpc("session.create", {})
        session_id = str(created.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("Harness 未返回 sessionId")
        mapping[chat_id] = session_id
        write_harness_session_map(mapping)
        return session_id


def harness_chat_for_session(session_id: str) -> str:
    with HARNESS_SESSION_LOCK:
        mapping = read_harness_session_map()
        for chat_id, value in mapping.items():
            if value == session_id:
                return chat_id
    return ""


def harness_history(session_id: str) -> list[dict[str, Any]]:
    value = harness_rpc(
        "session.history",
        {"sessionId": session_id, "maxMessages": 50},
        timeout=30,
    )
    events = value.get("events") or []
    return [item.get("event", item) for item in events if isinstance(item, dict)]


def assistant_text_from_event(event: dict[str, Any]) -> str:
    if event.get("type") != "assistant/message":
        return ""
    data = event.get("data") or {}
    message = data.get("message") or data
    content = message.get("content") or []
    parts = [
        str(block.get("text"))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]
    return "".join(parts).strip()


def harness_prompt_and_reply(chat_id: str, text: str) -> None:
    try:
        session_id = harness_session_for_chat(chat_id)
        previous = harness_history(session_id)
        previous_seq = max((int(event.get("seq", -1)) for event in previous), default=-1)
        harness_rpc(
            "session.prompt",
            {
                "sessionId": session_id,
                "mode": "queue",
                "content": [{
                    "type": "text",
                    "text": (
                        f"[Feishu context: chat_id={chat_id}; "
                        f"harness_session_id={session_id}]\n"
                        "Use the memory-eval tools for any task-related request. "
                        "The EchoMem PR source is read-only test input. Never modify "
                        "EchoMem files, apply patches, commit, install packages into "
                        "the source tree, or claim that a PR was fixed. If the exact "
                        "PR code cannot run, report the failure and evidence directly. "
                        "For '刚才/上一次/那个任务', call get_latest_memory_eval_status "
                        "or list_memory_eval_tasks first. Never invent a job id, status, "
                        "accuracy, or failure reason; only report tool results. "
                        "When a task is abnormal, call inspect_memory_eval and "
                        "diagnose_memory_eval before recommending an action. "
                        "Use recover_memory_eval only with an allowlisted action; "
                        "never claim that an arbitrary shell command was executed.\n"
                        f"{text}"
                    ),
                }],
            },
            timeout=30,
        )
        deadline = time.time() + float(os.getenv("HARNESS_REPLY_TIMEOUT_S", "180"))
        while time.time() < deadline:
            time.sleep(1.5)
            for event in harness_history(session_id):
                if int(event.get("seq", -1)) <= previous_seq:
                    continue
                answer = assistant_text_from_event(event)
                if answer:
                    send_feishu_text(chat_id, answer[:1800])
                    return
        send_feishu_text(chat_id, "Harness 已接收消息，但暂时没有生成完整回复，请稍后查询。")
    except Exception as exc:
        app.logger.exception("Harness Feishu bridge failed")
        try:
            send_feishu_text(chat_id, f"Harness 处理失败：{str(exc)[:500]}")
        except Exception:
            app.logger.exception("failed to report Harness error to Feishu")


def update_job(job_id: str, **updates: Any) -> dict[str, Any] | None:
    with LOCK:
        jobs = read_jobs()
        for job in jobs:
            if job["id"] == job_id:
                job.update(updates)
                write_jobs(jobs)
                return job
    return None


def get_job(job_id: str) -> dict[str, Any] | None:
    with LOCK:
        return next((job for job in read_jobs() if job["id"] == job_id), None)


def default_progress(phase: str = "queued") -> dict[str, Any]:
    return {
        "phase": phase,
        "label": PHASE_LABELS.get(phase, phase),
        "current": 0,
        "total": 0,
        "percent": 0,
        "last_log": "",
        "updated_at": now(),
    }


def _set_progress(
    progress: dict[str, Any],
    *,
    phase: str | None = None,
    current: int | None = None,
    total: int | None = None,
    last_log: str | None = None,
) -> bool:
    changed = False
    if phase and progress.get("phase") != phase:
        progress["phase"] = phase
        progress["label"] = PHASE_LABELS.get(phase, phase)
        progress["current"] = 0
        progress["total"] = 0
        changed = True
    if current is not None and current != progress.get("current"):
        progress["current"] = max(0, current)
        changed = True
    if total is not None and total != progress.get("total"):
        progress["total"] = max(0, total)
        changed = True
    if last_log:
        text = last_log.strip()
        if len(text) > 240:
            text = text[-240:]
        if text != progress.get("last_log"):
            progress["last_log"] = text
            changed = True
    total_value = int(progress.get("total") or 0)
    current_value = int(progress.get("current") or 0)
    percent = min(100, round(current_value * 100 / total_value)) if total_value else 0
    if percent != progress.get("percent"):
        progress["percent"] = percent
        changed = True
    if changed:
        progress["updated_at"] = now()
    return changed


def update_progress_from_line(job_id: str, line: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    progress = dict(job.get("progress") or default_progress("import"))
    changed = _set_progress(progress, last_log=line)

    if "阶段 1:" in line:
        changed |= _set_progress(progress, phase="import")
    match = re.search(r"conv-\d+:\s*.*?(\d+)/(\d+)", line)
    if match:
        changed |= _set_progress(
            progress,
            phase="import",
            current=int(match.group(1)),
            total=int(match.group(2)),
        )
    match = re.search(r"导入完成:\s*(\d+)/(\d+)", line)
    if match:
        changed |= _set_progress(
            progress,
            phase="import",
            current=int(match.group(1)),
            total=int(match.group(2)),
        )
    match = re.search(r"/session_(\d+):\s*completed", line)
    if match:
        changed |= _set_progress(
            progress,
            phase="import",
            current=max(int(progress.get("current") or 0), int(match.group(1))),
        )

    match = re.search(r"阶段 2:\s*QA\s*\(共\s*(\d+)\s*题", line)
    if match:
        changed |= _set_progress(progress, phase="qa", current=0, total=int(match.group(1)))
    match = re.search(r"QA (?:checkpoint|latest checkpoint saved):\s*(\d+)/(\d+)", line)
    if match:
        changed |= _set_progress(
            progress,
            phase="qa",
            current=int(match.group(1)),
            total=int(match.group(2)),
        )

    match = re.search(r"阶段 3:\s*Judge\s*\(共\s*(\d+)\s*题", line)
    if match:
        changed |= _set_progress(progress, phase="judge", current=0, total=int(match.group(1)))
    match = re.search(r"Judge (?:checkpoint|latest checkpoint saved):\s*(\d+)/(\d+)", line)
    if match:
        changed |= _set_progress(
            progress,
            phase="judge",
            current=int(match.group(1)),
            total=int(match.group(2)),
        )
    if "Judge 完成:" in line:
        match = re.search(r"Judge 完成:\s*(\d+)\s+CORRECT,\s*(\d+)\s+WRONG", line)
        if match:
            changed |= _set_progress(
                progress,
                phase="judge",
                current=int(match.group(1)) + int(match.group(2)),
            )
    if "评测完成!" in line:
        changed |= _set_progress(progress, phase="completed", current=1, total=1)

    if changed:
        update_job(job_id, progress=progress, message=progress["label"])


def valid_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("端口必须是整数") from exc
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间")
    return port


def require_access():
    return True


def eval_command(job: dict[str, Any], secret_values: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    http_port = job["echomem_http_port"]
    mcp_port = job["mcp_port"]
    command = [
        "python",
        "/app/benchmarks/locomo/run_eval.py",
        "--agent-plugin",
        "echomem_mcp",
        "--echomem-url",
        f"http://127.0.0.1:{http_port}",
        "--mcp-url",
        f"http://127.0.0.1:{mcp_port}",
        "--sample",
        "conv-30",
        "--no-tool-calling",
        "--no-search-in-tools",
        "--mcp-read-mode",
        "disabled",
        "--concurrency",
        str(job["qa_concurrency"]),
        "--judge-concurrency",
        str(job["judge_concurrency"]),
        "--top-k",
        "25",
        "--memory-budget-chars",
        "8000",
        "--user-memory-budget-chars",
        "4000",
        "--agent-memory-budget-chars",
        "2000",
        "--llm-temperature",
        "0.7",
        "--question-timeout-s",
        "600",
        "--llm-timeout-s",
        "600",
        "--llm-retries",
        "3",
        "--out-dir",
        f"/app/results/{job['id']}",
    ]
    environment = {
        "LLM_BASE_URL": secret_values["llm_base_url"],
        "LLM_MODEL": secret_values["llm_model"],
        "LLM_API_KEY": secret_values["llm_api_key"],
        "PYTHONPATH": "/work/EchoMem/src",
        "ECHOMEM_HTTP_TRACE_DIR": (
            f"/app/results/{job['id']}/echomem_http_trace"
        ),
    }
    provisioning_key = str(job.get("echomem_provisioning_auth_key") or "")
    if provisioning_key:
        environment["ECHOMEM_PROVISIONING_AUTH_KEY"] = provisioning_key
    return command, environment


def source_label(source_ref: str, pr_number: int | None = None) -> str:
    if source_ref == "develop":
        return "develop"
    if source_ref == "commit":
        return "commit"
    return f"PR {pr_number}" if pr_number is not None else source_ref


def dependency_fingerprint(source_dir: Path) -> str:
    """Hash dependency declarations, excluding application source files."""
    names = (
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
    )
    digest = hashlib.sha256()
    found = False
    for name in names:
        path = source_dir / name
        if not path.is_file():
            continue
        found = True
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if not found:
        raise RuntimeError(
            "EchoMem checkout 没有可识别的依赖声明文件，无法安全复用依赖镜像"
        )
    return digest.hexdigest()[:16]


def append_job_log(job_id: str, line: str) -> None:
    log_path = RESULTS_DIR / job_id / "container.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")
    update_progress_from_line(job_id, line)


def append_startup_incident(job_id: str, diagnosis: dict[str, Any]) -> None:
    """Persist a bounded, secret-free failure record for future operators."""
    job = get_job(job_id) or {}
    progress = job.get("progress") or {}
    record = {
        "recorded_at": now(),
        "job_id": job_id,
        "source": job.get("source_label") or job.get("source_ref"),
        "develop_commit": str(job.get("develop_commit_sha") or "")[:12],
        "pr_head": str(job.get("pr_head_sha") or "")[:12],
        "merge_commit": str(job.get("merge_commit_sha") or "")[:12],
        "phase": progress.get("phase"),
        "message": str(job.get("message") or "")[:1000],
        "category": diagnosis.get("category"),
        "needs_echomem_change": diagnosis.get("needs_echomem_change"),
        "retryable": diagnosis.get("retryable"),
        "allowed_actions": diagnosis.get("allowed_actions") or [],
        "reason": str(diagnosis.get("reason") or "")[:1000],
        "config_errors": diagnosis.get("config_errors") or [],
        "result_files": diagnosis.get("result_files") or [],
    }
    try:
        STARTUP_INCIDENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STARTUP_INCIDENTS_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        app.logger.exception("failed to persist startup incident for %s", job_id)


def run_checked(
    args: list[str],
    *,
    cwd: Path | None = None,
    job_id: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        output.append(line)
        if job_id:
            append_job_log(job_id, line)
    return_code = process.wait()
    if return_code:
        raise RuntimeError(
            f"命令失败({return_code}): {' '.join(args)}\n"
            + "\n".join(output[-20:])
        )
    return subprocess.CompletedProcess(args, return_code, "\n".join(output), "")


def prepare_echomem_source(job: dict[str, Any], secret_values: dict[str, str]) -> dict[str, Any]:
    """Prepare a dependency-cached image and an isolated source checkout."""
    source_ref = job["source_ref"]
    pr_number = job.get("pr_number")
    source_dir = SOURCE_ROOT / job["id"] / "source"
    build_dir = SOURCE_ROOT / job["id"] / "build"
    if source_dir.exists():
        shutil.rmtree(source_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    update_job(job["id"], progress=default_progress("prepare"), message="准备 EchoMem 代码")

    # Refresh develop metadata on every task. PR jobs are evaluated from
    # GitHub's generated merge ref, so the tested source is the PR as it would
    # be merged into the current develop branch.
    github_headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if GITHUB_TOKEN:
        github_headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        develop_commit_response = requests.get(
            "https://api.github.com/repos/tech-innovation-group/EchoMem/commits/develop",
            headers=github_headers,
            timeout=30,
        )
        if develop_commit_response.status_code == 404 and not GITHUB_TOKEN:
            raise RuntimeError(
                "EchoMem 仓库需要 GitHub 访问权限。请在 model.env 配置只读 GITHUB_TOKEN"
            )
        develop_commit_response.raise_for_status()
        develop_commit_payload = develop_commit_response.json()
        develop_commit_sha = str(develop_commit_payload.get("sha") or "unknown")
    except (requests.RequestException, RuntimeError) as exc:
        cached_develop_sources = [
            path
            for path in (CACHE_ROOT / "develop").glob("*")
            if path.is_dir()
            and re.fullmatch(r"[0-9a-fA-F]{7,64}", path.name)
            and (path / "pyproject.toml").is_file()
        ]
        if source_ref != "develop" or not cached_develop_sources:
            raise
        cached_develop = max(cached_develop_sources, key=lambda path: path.stat().st_mtime)
        develop_commit_sha = cached_develop.name
        append_job_log(
            job["id"],
            f"GitHub develop 检查失败，复用已缓存版本: {develop_commit_sha[:12]} ({exc})",
        )
    update_job(job["id"], develop_commit_sha=develop_commit_sha)
    append_job_log(job["id"], f"develop 已自动刷新: {develop_commit_sha[:12]}")

    fixed_commit_sha = str(job.get("fixed_commit_sha") or "").strip()
    if fixed_commit_sha:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", fixed_commit_sha):
            raise RuntimeError("固定 commit 必须是完整 40 位 SHA")
        archive_url = (
            ECHOMEM_REPO.removesuffix(".git")
            + f"/archive/{fixed_commit_sha}.tar.gz"
        )
        commit_sha = fixed_commit_sha
        merge_commit_sha = ""
        pr_head_sha = ""
        update_job(
            job["id"],
            develop_commit_sha=fixed_commit_sha,
            commit_sha=fixed_commit_sha,
            merge_status="fixed_commit",
            message=f"使用固定 commit: {fixed_commit_sha[:12]}",
        )
        append_job_log(
            job["id"],
            f"固定 commit 评测: {fixed_commit_sha}",
        )
    elif source_ref == "develop":
        archive_url = ECHOMEM_REPO.removesuffix(".git") + "/archive/refs/heads/develop.tar.gz"
        commit_sha = develop_commit_sha
        merge_commit_sha = ""
        pr_head_sha = ""
    else:
        commit_url = (
            f"https://api.github.com/repos/tech-innovation-group/EchoMem/pulls/{pr_number}"
        )
        merge_resolved = False
        for merge_attempt in range(1, 4):
            # GitHub can briefly report ``unknown`` while it computes the
            # merge ref. Once a concrete PR payload is returned, its
            # ``base.sha`` and ``pull/<number>/merge`` archive form one
            # coherent snapshot. Lock that snapshot for this task: develop is
            # allowed to move after the task starts and must not invalidate a
            # reproducible PR evaluation.
            commit_data = requests.get(
                commit_url,
                params={"_eval_ts": str(time.time_ns())},
                headers=github_headers,
                timeout=30,
            )
            commit_data.raise_for_status()
            commit_payload = commit_data.json()
            pr_state = str(commit_payload.get("state") or "").strip().lower()
            if pr_state != "open":
                raise RuntimeError(
                    f"PR {pr_number} 当前状态为 {pr_state or 'unknown'}，只测试开放 PR"
                )
            mergeable = commit_payload.get("mergeable")
            mergeable_state = str(commit_payload.get("mergeable_state") or "")
            if mergeable is None and mergeable_state in {"unknown", "unstable"}:
                time.sleep(2)
                continue
            base = commit_payload.get("base") or {}
            head = commit_payload.get("head") or {}
            base_ref = str(base.get("ref") or "")
            base_sha = str(base.get("sha") or "")
            pr_head_sha = str(head.get("sha") or "")
            if base_ref != "develop":
                raise RuntimeError(
                    f"PR {pr_number} 的目标分支是 {base_ref or '-'}，不是 develop，已停止测试"
                )
            if base_sha and base_sha != develop_commit_sha:
                append_job_log(
                    job["id"],
                    f"检测到 develop 在合并检查期间更新: "
                    f"{develop_commit_sha[:12]} -> {base_sha[:12]}；"
                    "锁定 GitHub 当前 PR merge snapshot，不再重试",
                )
                # The merge API payload is the authoritative base for the
                # archive URL used below. Persist it so the result clearly
                # states which develop commit was actually tested.
                develop_commit_sha = base_sha
                update_job(
                    job["id"],
                    develop_commit_sha=develop_commit_sha,
                    message=(
                        f"已锁定 PR merge 基线 {develop_commit_sha[:12]}"
                    ),
                )
            if mergeable is False or mergeable_state == "dirty":
                update_job(
                    job["id"],
                    merge_status="conflict",
                    pr_head_sha=pr_head_sha,
                    merge_base_sha=base_sha,
                    message=f"PR {pr_number} 与最新 develop 存在合并冲突，未开始评测",
                )
                raise RuntimeError(
                    f"PR {pr_number} 与最新 develop 存在合并冲突，未开始评测"
                )
            # GitHub may report mergeable=true with mergeable_state=unstable
            # when required checks are pending/failing. That is not a merge
            # conflict, so the generated PR merge ref remains testable.
            if mergeable is None:
                continue
            merge_commit_sha = str(commit_payload.get("merge_commit_sha") or "")
            if not re.fullmatch(r"[0-9a-fA-F]{7,64}", merge_commit_sha):
                merge_commit_sha = hashlib.sha256(
                    f"{base_sha}:{pr_head_sha}".encode("utf-8")
                ).hexdigest()
            archive_url = (
                ECHOMEM_REPO.removesuffix(".git")
                + f"/archive/refs/pull/{pr_number}/merge.tar.gz"
            )
            commit_sha = merge_commit_sha
            update_job(
                job["id"],
                pr_head_sha=pr_head_sha,
                merge_base_sha=base_sha,
                merge_commit_sha=merge_commit_sha,
                merge_status="clean",
                message=f"PR {pr_number} 与 develop 无冲突，准备合并结果代码",
            )
            append_job_log(
                job["id"],
                f"PR 合并检查通过: base={base_sha[:12]} head={pr_head_sha[:12]} merge={merge_commit_sha[:12]}",
            )
            merge_resolved = True
            break
        if not merge_resolved:
            raise RuntimeError(
                f"GitHub 暂时无法稳定判断 PR {pr_number} 是否可合并，平台已自动重试 3 次"
            )
    cache_namespace = "develop" if source_ref == "develop" else "pr-merge"
    cache_key = commit_sha if re.fullmatch(r"[0-9a-fA-F]{7,64}", commit_sha) else f"{source_ref}-{pr_number or 'unknown'}"
    cached_source = CACHE_ROOT / cache_namespace / cache_key
    if cached_source.is_dir() and (cached_source / "pyproject.toml").is_file():
        shutil.copytree(cached_source, source_dir)
        append_job_log(
            job["id"],
            f"合并后源码缓存命中: {source_label(source_ref, pr_number)} {commit_sha[:12]}",
        )
    else:
        archive = requests.get(
            archive_url,
            headers=github_headers,
            timeout=120,
        )
        if archive.status_code == 404 and not GITHUB_TOKEN:
            raise RuntimeError(
                "EchoMem 源码下载返回 404：服务器没有访问私有仓库的权限，请配置 GITHUB_TOKEN"
            )
        archive.raise_for_status()
        cached_source.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = cached_source.with_name(cached_source.name + ".tmp")
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz") as tar:
            tar.extractall(staging_dir)
        extracted = next(path for path in staging_dir.iterdir() if path.is_dir())
        extracted.rename(cached_source)
        shutil.copytree(cached_source, source_dir)
        append_job_log(
            job["id"],
            f"合并后源码下载完成并缓存: {source_label(source_ref, pr_number)} {commit_sha[:12]}",
        )
    # Source preparation and image preparation are cached independently. A
    # repeated evaluation still creates a new task and runs QA again, while
    # the dependency image is reused across source commits with the same
    # dependency declarations.
    image_suffix = dependency_fingerprint(source_dir)
    image_name = (
        f"{ECHOMEM_IMAGE_PREFIX}:deps-{image_suffix}"
        f"-cfg{ECHOMEM_CONFIG_REVISION}"
    )
    config_example_path = source_dir / "configs" / "config.example.json"
    if not config_example_path.is_file():
        raise RuntimeError(
            "被测 EchoMem 代码缺少 configs/config.example.json，"
            "不会回退到测试平台配置模板"
        )
    # Patch a task-owned runtime copy. The EchoMem checkout remains byte-for-byte
    # identical to the downloaded develop/PR merge snapshot.
    runtime_config_path = DOCKER_RESULTS_DIR / job["id"] / "echomem.config.json"
    runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_example_path, runtime_config_path)
    patch_echomem_config_model(runtime_config_path, job["id"])
    config_bytes, api_key_envs = read_echo_config(runtime_config_path)
    # The benchmark always performs its first memory_query through MCP,
    # including no-tool-calling mode. This is a task-local override and does
    # not modify the checked-out EchoMem source.
    config_bytes = enable_eval_mcp(config_bytes)
    # The dependency image is intentionally cached by dependency fingerprint.
    # Keep the
    # task-local endpoint/model configuration outside that image and mount it
    # at runtime, otherwise an old cached image can silently keep stale model
    # URLs (for example an old embedding endpoint).
    # The Web process talks to the host Docker daemon.  Use the host-visible
    # result path for bind sources, even when RESULTS_DIR is a container path.
    runtime_config_path.write_bytes(config_bytes)
    update_job(
        job["id"],
        config_source=str(config_example_path),
        config_api_key_envs=api_key_envs,
        config_exact=True,
    )
    client = docker.from_env()
    image_cached = not bool(job.get("force_rebuild"))
    try:
        if image_cached:
            client.images.get(image_name)
    except ImageNotFound:
        image_cached = False
    update_job(
        job["id"],
        cache_key=cache_key,
        image=image_name,
        image_cached=image_cached,
        image_temporary=False,
        force_rebuild=bool(job.get("force_rebuild")),
    )
    if image_cached:
        append_job_log(job["id"], f"EchoMem 镜像缓存命中: {image_name}")
        update_job(
            job["id"],
            commit_sha=commit_sha,
            source_dir=str(source_dir),
            image=image_name,
            image_cached=True,
            image_temporary=False,
            message=(
                "代码和依赖镜像准备完成（依赖镜像缓存命中）: "
                f"{commit_sha[:12]}"
            ),
        )
        return {
            "image": image_name,
            "commit_sha": commit_sha,
            "source_dir": source_dir,
            "api_key_envs": api_key_envs,
            "config_path": runtime_config_path,
        }
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "config.json").write_bytes(config_bytes)
    update_job(
        job["id"],
        config_source=str(config_example_path),
        config_api_key_envs=sorted(api_key_envs),
        config_exact=True,
    )
    (build_dir / "Dockerfile").write_text(
        """FROM python:3.11
WORKDIR /opt/echomem
ENV PIP_INDEX_URL=%s \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY source/ /opt/echomem/
RUN python -m pip install --no-cache-dir .
COPY source/configs/ /usr/local/lib/python3.11/configs/
COPY config.json %s/config.json
EXPOSE 8010 8001
ENV PYTHONPATH=/opt/echomem/src
CMD ["python", "-m", "echomem.entrypoints.cli", "server", "--host", "0.0.0.0", "--port", "8010", "--workspace", "%s"]
""" % (PIP_INDEX_URL, ECHOMEM_WORKSPACE, ECHOMEM_WORKSPACE),
        encoding="utf-8",
    )
    shutil.copytree(source_dir, build_dir / "source")
    update_job(job["id"], message=f"构建 EchoMem 镜像 ({source_label(source_ref, pr_number)})")
    update_job(job["id"], message=f"构建 EchoMem 镜像（依赖安装中）")
    build_started = time.monotonic()
    build_stream = client.api.build(
        path=str(build_dir),
        tag=image_name,
        rm=True,
        forcerm=True,
        decode=True,
    )
    build_error = ""
    for event in build_stream:
        if time.monotonic() - build_started > ECHOMEM_BUILD_TIMEOUT_S:
            raise TimeoutError(
                f"EchoMem 镜像构建超过 {ECHOMEM_BUILD_TIMEOUT_S} 秒，已终止"
            )
        if not isinstance(event, dict):
            continue
        if event.get("errorDetail"):
            build_error = str(
                (event.get("errorDetail") or {}).get("message")
                or event.get("error")
                or "Docker build failed"
            )
            append_job_log(job["id"], f"EchoMem 镜像构建失败: {build_error}")
            raise RuntimeError(build_error)
        text = str(event.get("stream") or event.get("status") or "").strip()
        if text:
            append_job_log(job["id"], f"[镜像] {text}")
    if build_error:
        raise RuntimeError(build_error)
    update_job(
        job["id"],
        commit_sha=commit_sha,
        source_dir=str(source_dir),
        image=image_name,
        image_cached=False,
        image_temporary=False,
        message=f"代码准备完成: {commit_sha[:12]}",
    )
    return {
        "image": image_name,
        "commit_sha": commit_sha,
        "source_dir": source_dir,
        "api_key_envs": api_key_envs,
        "config_path": runtime_config_path,
    }


def wait_for_http(url: str, *, timeout_s: int = 300, job_id: str | None = None) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=3)
            if response.ok:
                return
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        if job_id:
            update_job(job_id, message=f"等待 EchoMem 启动: {last_error[:160]}")
        time.sleep(2)
    raise RuntimeError(f"EchoMem 健康检查超时: {url} ({last_error})")


def capture_echomem_diagnostics(container, job_id: str, stage: str) -> list[str]:
    """Persist inspect/log output before a failed EchoMem container is removed."""
    result_dir = RESULTS_DIR / job_id
    result_dir.mkdir(parents=True, exist_ok=True)
    suffix = re.sub(r"[^a-zA-Z0-9_.-]+", "_", stage).strip("_") or "health"
    paths: list[str] = []
    try:
        inspect_path = result_dir / f"echomem.inspect.{suffix}.json"
        inspect_path.write_text(
            json.dumps(container.attrs, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths.append(str(inspect_path))
    except Exception:
        app.logger.exception("failed to save EchoMem docker inspect for %s", job_id)
    try:
        logs = container.logs(tail=300, timestamps=True)
        if isinstance(logs, bytes):
            logs = logs.decode("utf-8", errors="replace")
        logs_path = result_dir / f"echomem.logs.{suffix}.txt"
        logs_path.write_text(str(logs), encoding="utf-8")
        paths.append(str(logs_path))
    except Exception:
        app.logger.exception("failed to save EchoMem docker logs for %s", job_id)
    for path in paths:
        append_job_log(job_id, f"EchoMem 诊断已保存: {path}")
    return paths


def _container_health(container) -> tuple[bool, str]:
    """Check EchoMem from inside its own network namespace first."""
    try:
        result = container.exec_run(
            [
                "python",
                "-c",
                (
                    "import urllib.request; "
                    "r=urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3); "
                    "print(r.status)"
                ),
            ],
        )
        output = result.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if int(result.exit_code or 0) == 0:
            return True, str(output).strip()
        return False, str(output).strip()[-300:]
    except Exception as exc:
        return False, str(exc)[-300:]


def _mcp_probe(base_url: str) -> tuple[bool, str]:
    """Complete an MCP handshake and tools/list before QA starts."""
    url = f"{base_url.rstrip('/')}/mcp"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        # EchoMem validates the HTTP Host header. When the Web worker reaches
        # the container through its bridge IP, requests would otherwise send
        # that IP as Host and the MCP server returns 421.
        "Host": "127.0.0.1:8001",
    }

    def post(payload: dict[str, Any], session_id: str = "") -> tuple[Any, str]:
        request_headers = dict(headers)
        if session_id:
            request_headers["Mcp-Session-Id"] = session_id
            request_headers["mcp-protocol-version"] = "2025-06-18"
        response = requests.post(
            url,
            headers=request_headers,
            json=payload,
            timeout=5,
        )
        response.raise_for_status()
        session = (
            response.headers.get("Mcp-Session-Id")
            or response.headers.get("mcp-session-id")
            or session_id
        )
        if payload.get("method", "").startswith("notifications/"):
            return None, session
        data = next(
            (
                line[5:].strip()
                for line in response.text.splitlines()
                if line.strip().startswith("data:")
            ),
            "",
        )
        if not data:
            raise RuntimeError(f"MCP response has no SSE data: {response.text[:200]}")
        return json.loads(data), session

    result, session_id = post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "memory-eval-web-probe", "version": "1"},
            },
        }
    )
    if not session_id:
        raise RuntimeError("MCP initialize returned no session id")
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
    listed, _ = post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        session_id,
    )
    if isinstance(listed, dict) and listed.get("error"):
        raise RuntimeError(f"MCP tools/list failed: {listed['error']}")
    return True, "handshake/tools-list ok"


def wait_for_echomem(
    container,
    echo_ip: str,
    job_id: str,
    mcp_port: int = ECHOMEM_MCP_PORT,
) -> None:
    """Wait for EchoMem, restarting the container once after a failed window."""
    last_error = ""
    for attempt in range(2):
        deadline = time.monotonic() + max(1, ECHOMEM_HEALTH_TIMEOUT_S)
        while time.monotonic() < deadline:
            try:
                container.reload()
                status = str(container.status or "")
                current_network = (
                    container.attrs.get("NetworkSettings", {})
                    .get("Networks", {})
                    .get("bridge", {})
                )
                echo_ip = str(current_network.get("IPAddress") or echo_ip)
                if status not in {"running", "restarting"}:
                    last_error = f"容器状态为 {status or 'unknown'}"
                    break
            except Exception as exc:
                last_error = str(exc)

            internal_ok, internal_detail = _container_health(container)
            if internal_ok:
                try:
                    _, mcp_detail = _mcp_probe(f"http://{echo_ip}:8001")
                    append_job_log(
                        job_id,
                        "EchoMem HTTP/MCP 健康检查通过: "
                        f"127.0.0.1:8010/health ({internal_detail}); "
                        f"127.0.0.1:{mcp_port}/mcp ({mcp_detail})",
                    )
                    return
                except Exception as exc:
                    last_error = f"MCP 检查失败: {str(exc)[:300]}"
                    append_job_log(job_id, last_error)
                    update_job(
                        job_id,
                        message=f"EchoMem HTTP 已就绪，等待 MCP: {last_error[:160]}",
                    )
            else:
                last_error = f"容器内检查失败: {internal_detail}"
                append_job_log(job_id, last_error)

            # The Web worker is normally on the same Docker bridge, so use
            # the container IP as a secondary check after the local probe.
            try:
                response = requests.get(
                    f"http://{echo_ip}:8010/health",
                    timeout=ECHOMEM_HEALTH_REQUEST_TIMEOUT_S,
                )
                if response.ok:
                    append_job_log(
                        job_id,
                        f"EchoMem 网络健康检查通过: {echo_ip}:8010/health",
                    )
                    return
                last_error = f"网络检查 HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)
            update_job(
                job_id,
                message=(
                    f"等待 EchoMem 启动（第 {attempt + 1}/2 次）: "
                    f"{last_error[:160]}"
                ),
            )
            time.sleep(2)

        capture_echomem_diagnostics(container, job_id, f"health-attempt-{attempt + 1}")
        if attempt == 0:
            update_job(
                job_id,
                message="EchoMem 健康检查超时，正在自动重启容器",
            )
            append_job_log(job_id, "EchoMem 健康检查失败，自动重启容器（1/1）")
            try:
                container.restart(timeout=20)
            except Exception as exc:
                capture_echomem_diagnostics(container, job_id, "restart-failed")
                raise RuntimeError(f"EchoMem 自动重启失败: {exc}") from exc
            time.sleep(2)
            continue
        else:
            capture_echomem_diagnostics(container, job_id, "health-final")
    raise RuntimeError(
        "EchoMem 健康检查超时（每次尝试 300 秒，已自动重启 1 次）: "
        f"{last_error[:400]}"
    )


def source_eval_environment(secret_values: dict[str, str]) -> dict[str, str]:
    return {
        "LLM_BASE_URL": secret_values["llm_base_url"],
        "LLM_MODEL": secret_values["llm_model"],
        "LLM_API_KEY": secret_values["llm_api_key"],
    }


def result_summary(job_id: str) -> dict[str, Any]:
    result_dir = RESULTS_DIR / job_id
    candidates = list(result_dir.rglob("summary.json")) if result_dir.exists() else []
    if not candidates:
        return {}
    try:
        summary = json.loads(candidates[0].read_text(encoding="utf-8"))
        judge_path = next(result_dir.rglob("judge_results.csv"), None)
        if judge_path:
            with judge_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            correct = sum(
                str(row.get("verdict") or "").upper() == "CORRECT"
                for row in rows
            )
            wrong = sum(
                str(row.get("verdict") or "").upper() == "WRONG"
                for row in rows
            )
            errors = len(rows) - correct - wrong
            summary["judge_correct"] = correct
            summary["judge_wrong"] = wrong
            summary["judge_errors"] = errors
            summary["judge_graded"] = correct + wrong
            summary["judge_denominator"] = len(rows)
            summary["accuracy"] = (
                round(correct / len(rows), 4) if rows else 0.0
            )
            summary["judge_error_question_ids"] = [
                str(row.get("question_id") or "")
                for row in rows
                if str(row.get("judge_error") or "").strip()
                or str(row.get("verdict") or "").upper() == "ERROR"
            ]
        return summary
    except (OSError, json.JSONDecodeError):
        return {}


def _latest_result_csv(result_dir: Path, name: str) -> Path | None:
    candidates = list(result_dir.rglob(name))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def evaluation_details(job_id: str) -> dict[str, Any]:
    """Read the latest QA/Judge checkpoint rows for live task inspection."""
    result_dir = RESULTS_DIR / job_id
    qa_path = _latest_result_csv(result_dir, "qa_results.csv")
    qa_checkpoint = _latest_result_csv(result_dir, "qa_results.checkpoint.csv")
    judge_path = _latest_result_csv(result_dir, "judge_results.csv")
    judge_checkpoint = _latest_result_csv(result_dir, "judge_results.checkpoint.csv")
    qa_path = max(
        [path for path in (qa_path, qa_checkpoint) if path],
        key=lambda path: path.stat().st_mtime_ns,
        default=None,
    )
    judge_path = max(
        [path for path in (judge_path, judge_checkpoint) if path],
        key=lambda path: path.stat().st_mtime_ns,
        default=None,
    )

    def read_rows(path: Path | None) -> list[dict[str, str]]:
        if not path:
            return []
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))
        except (OSError, csv.Error, UnicodeError):
            return []

    qa_rows = read_rows(qa_path)
    judge_rows = read_rows(judge_path)
    judges = {
        str(row.get("question_id") or ""): row
        for row in judge_rows
        if row.get("question_id")
    }
    rows: list[dict[str, Any]] = []
    for qa in qa_rows:
        question_id = str(qa.get("question_id") or "")
        judge = judges.get(question_id) or {}
        rows.append(
            {
                "question_id": question_id,
                "question": qa.get("question", ""),
                "gold_answer": qa.get("answer", ""),
                "response": qa.get("response", ""),
                "qa_status": qa.get("answer_status")
                or qa.get("model_status")
                or ("error" if qa.get("llm_error") else "ok"),
                "qa_error": qa.get("llm_error") or qa.get("retrieval_error") or "",
                "judge_verdict": judge.get("verdict", ""),
                "judge_reasoning": judge.get("reasoning", ""),
                "judge_error": judge.get("judge_error", ""),
            }
        )
    for judge in judge_rows:
        question_id = str(judge.get("question_id") or "")
        if question_id and not any(row["question_id"] == question_id for row in rows):
            rows.append(
                {
                    "question_id": question_id,
                    "question": judge.get("question", ""),
                    "gold_answer": judge.get("answer", ""),
                    "response": judge.get("response", ""),
                    "qa_status": "已完成",
                    "qa_error": "",
                    "judge_verdict": judge.get("verdict", ""),
                    "judge_reasoning": judge.get("reasoning", ""),
                    "judge_error": judge.get("judge_error", ""),
                }
            )
    return {
        "rows": rows,
        "qa_count": len(qa_rows),
        "judge_count": len(judge_rows),
        "qa_source": qa_path.name if qa_path else "",
        "judge_source": judge_path.name if judge_path else "",
    }


def format_result(job: dict[str, Any]) -> str:
    summary = result_summary(job["id"]) or job.get("summary") or {}
    correct = summary.get("judge_correct")
    denominator = (
        summary.get("judge_denominator")
        or summary.get("total_questions")
        or summary.get("judge_graded")
    )
    accuracy = None
    if correct is not None and denominator:
        accuracy = correct / denominator
    if accuracy is None:
        accuracy = summary.get("accuracy")
    if accuracy is None:
        return f"任务 {job['id']} 已结束，但没有找到 summary.json。"
    percent = float(accuracy) * 100 if float(accuracy) <= 1 else float(accuracy)
    count = f" ({correct}/{denominator})" if correct is not None and denominator else ""
    errors = int(summary.get("judge_errors") or 0)
    error_ids = summary.get("judge_error_question_ids") or []
    error_text = (
        f"\nJudge 异常：{errors} 题（按错题计入分母）"
        f"\n异常题目：{', '.join(error_ids) or '-'}"
        if errors
        else ""
    )
    pr_number = job.get("pr_number")
    code_source = "分支: develop"
    if job.get("source_ref") == "pr" and pr_number is not None:
        code_source += f" · PR {pr_number}"
    return (
        f"测试完成\nLoCoMo / conv-30\n{code_source}\n"
        f"commit: {str(job.get('commit_sha', ''))[:12] or '-'}\n"
        f"准确率: {percent:.2f}%{count}{error_text}\n任务 ID: {job['id']}"
    )


def job_detail_url(job_id: str) -> str:
    base_url = PUBLIC_BASE_URL or "http://127.0.0.1:8081"
    return f"{base_url}/jobs/{job_id}"


def latest_chat_job(chat_id: str) -> dict[str, Any] | None:
    jobs = [job for job in read_jobs() if job.get("feishu_chat_id") == chat_id]
    return jobs[-1] if jobs else None


def compact_job(job: dict[str, Any]) -> dict[str, Any]:
    progress = job.get("progress") or {}
    summary = result_summary(job["id"]) or job.get("summary") or {}
    result_dir = RESULTS_DIR / job["id"]
    log_text = ""
    log_path = result_dir / "container.log"
    if log_path.is_file():
        try:
            log_text = "\n".join(
                log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            )
        except OSError:
            pass
    compact = {
        "id": job.get("id"),
        "status": job.get("status"),
        "message": job.get("message"),
        "source": job.get("source_label"),
        "commit": str(job.get("commit_sha") or "")[:12],
        "develop_baseline": str(job.get("develop_commit_sha") or "")[:12],
        "pr_head": str(job.get("pr_head_sha") or "")[:12],
        "merge_commit": str(job.get("merge_commit_sha") or "")[:12],
        "merge_status": job.get("merge_status"),
        "cache_key": job.get("cache_key"),
        "image": job.get("image"),
        "image_cached": bool(job.get("image_cached")),
        "failure_analysis": job.get("failure_analysis"),
        "failure_diagnosis": job.get("failure_diagnosis"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "progress": {
            "phase": progress.get("label"),
            "current": progress.get("current", 0),
            "total": progress.get("total") or None,
            "percent": progress.get("percent", 0),
            "last_log": progress.get("last_log", ""),
        },
        "summary": summary,
        "recent_log": log_text,
    }
    if job.get("id"):
        compact["detail_url"] = job_detail_url(str(job["id"]))
    return compact


def job_observability(job_id: str, *, log_lines: int = 120) -> dict[str, Any]:
    """Return bounded, safe evidence for the Harness diagnostic agent."""
    job = get_job(job_id)
    if not job:
        raise KeyError(job_id)
    result_dir = RESULTS_DIR / job_id
    platform_logs: list[str] = []
    docker_logs: list[str] = []
    container_log = result_dir / "container.log"
    if container_log.is_file():
        try:
            platform_logs.extend(
                container_log.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            )
        except OSError:
            pass
    echo_logs = sorted(result_dir.glob("echomem.logs.*.txt"))
    for path in echo_logs[-3:]:
        try:
            docker_logs.extend(
                f"[{path.name}] {line}"
                for line in path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            )
        except OSError:
            continue
    inspect_files = sorted(result_dir.glob("echomem.inspect.*.json"))
    inspect_summaries: list[dict[str, Any]] = []
    for path in inspect_files[-3:]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            state = payload.get("State") or {}
            network = payload.get("NetworkSettings") or {}
            config = payload.get("Config") or {}
            host_config = payload.get("HostConfig") or {}
            entrypoint = config.get("Entrypoint") or []
            command = config.get("Cmd") or []
            inspect_summaries.append(
                {
                    "file": path.name,
                    "status": state.get("Status"),
                    "exit_code": state.get("ExitCode"),
                    "oom_killed": bool(state.get("OOMKilled")),
                    "error": state.get("Error"),
                    "started_at": state.get("StartedAt"),
                    "finished_at": state.get("FinishedAt"),
                    "health": (state.get("Health") or {}).get("Status"),
                    "ports": network.get("Ports"),
                    "entrypoint": entrypoint,
                    "command": command,
                    "restart_count": payload.get("RestartCount"),
                    "memory_limit": host_config.get("Memory"),
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    files = []
    if result_dir.is_dir():
        files = sorted(
            str(path.relative_to(result_dir))
            for path in result_dir.rglob("*")
            if path.is_file()
        )[-100:]
    runtime = extract_runtime_evidence(
        platform_logs=platform_logs,
        docker_logs=docker_logs,
        inspect_summaries=inspect_summaries,
    )
    return {
        "job": compact_job(job),
        "logs": platform_logs[-max(10, min(log_lines, 240)):],
        "platform_logs": platform_logs[-max(10, min(log_lines, 240)):],
        "docker_logs": docker_logs[-max(10, min(log_lines, 240)):],
        "docker_inspect": inspect_summaries,
        "runtime": runtime,
        "result_files": files,
    }


def extract_runtime_evidence(
    *,
    platform_logs: list[str],
    docker_logs: list[str],
    inspect_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract stable signals that are easy for a model to misread."""
    all_logs = "\n".join(platform_logs + docker_logs)
    lowered = all_logs.lower()
    latest_inspect = inspect_summaries[-1] if inspect_summaries else {}
    config_error_patterns = (
        r"hostip",
        r"hostport",
        r"invalid .*config",
        r"configuration error",
        r"config(?:uration)? .*error",
        r"jsondecodeerror",
        r"validationerror",
        r"missing (?:required )?(?:environment|config)",
        r"invalid argument",
        r"bad request",
    )
    config_errors = [
        line[-500:]
        for line in (platform_logs + docker_logs)
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in config_error_patterns)
    ][-12:]
    error_markers = (
        "traceback",
        "error",
        "exception",
        "fatal",
        "failed",
        "no module named",
        "modulenotfounderror",
        "cannot import",
        "permission denied",
        "address already in use",
    )
    concrete_error_lines = [
        line[-800:]
        for line in docker_logs
        if any(marker in line.lower() for marker in error_markers)
    ][-20:]
    command = {
        "entrypoint": latest_inspect.get("entrypoint") or [],
        "cmd": latest_inspect.get("command") or [],
    }
    exit_code = latest_inspect.get("exit_code")
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    return {
        "exit_code": exit_code,
        "oom_killed": bool(latest_inspect.get("oom_killed")),
        "container_status": latest_inspect.get("status") or "",
        "health_status": latest_inspect.get("health") or "",
        "restart_count": latest_inspect.get("restart_count"),
        "memory_limit": latest_inspect.get("memory_limit"),
        "startup_command": command,
        "docker_error": latest_inspect.get("error") or "",
        "config_errors": config_errors,
        "concrete_error_lines": concrete_error_lines,
        "docker_logs_present": bool(docker_logs),
        "signals": {
            "module_not_found": "modulenotfounderror" in lowered or "no module named" in lowered,
            "model_auth_or_rate_limit": any(
                marker in lowered
                for marker in ("401 unauthorized", "403 forbidden", "invalid api key", "rate limit", "429")
            ),
            "docker_daemon": any(
                marker in lowered
                for marker in ("docker daemon", "cannot connect to the docker", "no space left on device")
            ),
            "timeout": "timeout" in lowered or "timed out" in lowered,
        },
    }


def format_runtime_evidence(runtime: dict[str, Any]) -> str:
    """Make container exit evidence directly visible to operators."""
    command = runtime.get("startup_command") or {}
    entrypoint = json.dumps(command.get("entrypoint") or [], ensure_ascii=False)
    cmd = json.dumps(command.get("cmd") or [], ensure_ascii=False)
    exit_code = runtime.get("exit_code")
    if exit_code == 137:
        exit_meaning = "通常表示 SIGKILL/OOM"
    elif exit_code == 127:
        exit_meaning = "通常表示启动命令或依赖不存在"
    elif exit_code == 126:
        exit_meaning = "通常表示启动命令不可执行"
    elif exit_code in {1, 2}:
        exit_meaning = "进程自身返回错误，需结合 docker logs 判断"
    else:
        exit_meaning = "无明确退出码解释"
    lines = [
        f"退出码: {exit_code if exit_code is not None else '未知'}（{exit_meaning}）",
        f"OOMKilled: {'是' if runtime.get('oom_killed') else '否'}",
        f"容器状态: {runtime.get('container_status') or '未知'}",
        f"启动命令: entrypoint={entrypoint}, cmd={cmd}",
    ]
    if runtime.get("docker_error"):
        lines.append(f"Docker Error: {str(runtime['docker_error'])[:800]}")
    concrete = runtime.get("concrete_error_lines") or []
    if concrete:
        lines.append("docker logs 关键错误:\n" + "\n".join(concrete[-6:]))
    elif not runtime.get("docker_logs_present"):
        lines.append("docker logs 关键错误: 未采集到容器输出，当前证据不足")
    if runtime.get("config_errors"):
        lines.append("配置错误:\n" + "\n".join(runtime["config_errors"][-4:]))
    return "\n".join(lines)


def classify_job_failure(job_id: str) -> dict[str, Any]:
    """Classify common failures before asking the LLM for an explanation."""
    evidence = job_observability(job_id, log_lines=180)
    job = evidence["job"]
    runtime = evidence["runtime"]
    text = "\n".join(
        [
            str(job.get("message") or ""),
            str(job.get("failure_analysis") or ""),
            *[str(line) for line in evidence["platform_logs"][-100:]],
            *[str(line) for line in evidence["docker_logs"][-100:]],
        ]
    ).lower()
    merge_status = str(job.get("merge_status") or "").lower()
    explicit_conflict = (
        merge_status == "conflict"
        or "与最新 develop 存在合并冲突，未开始评测" in text
        or "与 develop 存在合并冲突，未开始评测" in text
        or "pr merge conflict" in text
    )
    if explicit_conflict:
        category = "git_conflict"
        category_label = "Git 合并冲突"
        reason = "PR 与当前 develop 无法无冲突合并，未进入有效评测。"
        retryable = False
        actions = ["resolve_conflict", "resubmit"]
    elif runtime["oom_killed"] or runtime["signals"]["docker_daemon"] or runtime["docker_error"]:
        category = "docker_or_server"
        category_label = "Docker/服务器问题"
        reason = "容器被 OOM 杀死，或 Docker/宿主机报告了基础设施错误。"
        retryable = True
        actions = ["retry"]
    elif runtime["config_errors"] or any(
        marker in text
        for marker in (
            "hostip",
            "hostport",
            "invalid json",
            "invalid argument",
            "bad request",
            "端口绑定",
        )
    ):
        category = "test_platform_config"
        category_label = "测试平台配置问题"
        reason = "任务编排、Docker 参数或测试平台配置格式错误，不应归因于 EchoMem。"
        retryable = True
        actions = ["retry"]
    elif runtime["signals"]["module_not_found"] or any(
        marker in text
        for marker in (
            "could not find a version",
            "pip install",
            "dependency",
            "依赖",
        )
    ):
        category = "dependency"
        category_label = "依赖问题"
        reason = "运行环境缺少依赖或依赖安装失败；需要确认 PR 声明和镜像构建过程。"
        retryable = True
        actions = ["rebuild_retry", "retry"]
    elif runtime["signals"]["model_auth_or_rate_limit"] or any(
        marker in text
        for marker in (
            "api key",
            "authentication",
            "unauthorized",
            "model provider",
            "模型服务",
            "llm_error",
        )
    ):
        category = "model_service"
        category_label = "模型服务问题"
        reason = "模型服务鉴权、限流、接口或上游请求失败。"
        retryable = True
        actions = ["retry"]
    elif any(
        marker in text
        for marker in (
            "health check",
            "健康检查",
            "connection refused",
            "connecttimeouterror",
            "did not become healthy",
            "端口",
        )
    ):
        # A running container with EchoMem traceback/config errors is a
        # candidate code problem; a plain network timeout is infrastructure.
        if (
            "traceback" in text
            or "cannot import" in text
            or "attributeerror" in text
            or "typeerror" in text
        ):
            category = "echomem_code"
            category_label = "EchoMem 代码问题"
            reason = "EchoMem docker logs 出现自身 traceback/代码异常，容器未能正常启动。"
            retryable = False
            actions = ["report"]
        else:
            category = "docker_or_server"
            category_label = "Docker/服务器问题"
            reason = (
                "容器已退出但没有发现 EchoMem traceback；"
                "优先依据退出码、Docker Error 和 docker logs 排查，"
                "当前不能认定需要修改 EchoMem。"
            )
            retryable = True
            actions = ["restart_echomem", "retry"]
    elif any(
        marker in text
        for marker in (
            "no module named",
            "modulenotfounderror",
            "could not find a version",
            "pip install",
            "依赖",
            "docker build",
        )
    ):
        category = "dependency"
        category_label = "依赖问题"
        reason = "镜像或运行环境缺少依赖，需检查依赖声明和构建日志。"
        retryable = True
        actions = ["rebuild_retry", "retry"]
    elif any(
        marker in text
        for marker in (
            "judge",
            "judge_error",
            "评测进程退出异常",
            "llm_error",
            "rate limit",
            "429",
            "timeout",
        )
    ):
        category = "model_service"
        category_label = "模型服务问题"
        reason = "QA/Judge 请求出现模型调用错误或超时。"
        retryable = True
        actions = ["retry"]
    else:
        category = "test_platform_config"
        category_label = "测试平台配置问题"
        reason = "无法从现有证据确认 EchoMem 代码问题，先按测试平台运行异常反馈。"
        retryable = True
        actions = ["retry"]
    needs_change = category == "echomem_code"
    return {
        "category": category,
        "category_label": category_label,
        "reason": reason,
        "needs_echomem_change": needs_change,
        "needs_echomem_change_text": "是" if needs_change else "否",
        "retryable": retryable,
        "allowed_actions": actions,
        "evidence": evidence,
    }


def llm_failure_diagnosis(job_id: str) -> str:
    diagnosis = classify_job_failure(job_id)
    job = diagnosis["evidence"]["job"]
    error_message = str(job.get("message") or "")
    model_analysis = analyze_failure_with_llm(job_id, error_message)
    conclusion = (
        f"分类：{diagnosis['category_label']}\n"
        f"是否需要修改 EchoMem：{diagnosis['needs_echomem_change_text']}\n"
        f"依据：{diagnosis['reason']}\n"
        f"是否可重试：{'是' if diagnosis['retryable'] else '否'}\n"
        f"建议动作：{', '.join(diagnosis['allowed_actions'])}"
    )
    return f"{conclusion}\n模型分析：{model_analysis}" if model_analysis else conclusion


def build_failure_diagnosis(job_id: str, error_message: str) -> dict[str, Any]:
    """Build a persisted, structured diagnosis from bounded runtime evidence."""
    classified = classify_job_failure(job_id)
    evidence = classified["evidence"]
    model_analysis = analyze_failure_with_llm(job_id, error_message)
    text = (
        f"分类：{classified['category_label']}\n"
        f"是否需要修改 EchoMem：{classified['needs_echomem_change_text']}\n"
        f"依据：{classified['reason']}\n"
        f"运行证据：\n{format_runtime_evidence(evidence['runtime'])}\n"
        f"是否可重试：{'是' if classified['retryable'] else '否'}\n"
        f"建议动作：{', '.join(classified['allowed_actions'])}"
    )
    if model_analysis:
        text += f"\n模型分析：{model_analysis}"
    return {
        "category": classified["category"],
        "category_label": classified["category_label"],
        "needs_echomem_change": classified["needs_echomem_change"],
        "needs_echomem_change_text": classified["needs_echomem_change_text"],
        "reason": classified["reason"],
        "retryable": classified["retryable"],
        "allowed_actions": classified["allowed_actions"],
        "model_analysis": model_analysis,
        "runtime": evidence["runtime"],
        "docker_inspect": evidence["docker_inspect"],
        "platform_logs": evidence["platform_logs"],
        "docker_logs": evidence["docker_logs"],
        "result_files": evidence["result_files"],
        "config_errors": evidence["runtime"]["config_errors"],
        "text": text,
    }


def job_context(chat_id: str, question: str) -> str:
    all_jobs = read_jobs()
    chat_jobs = [job for job in all_jobs if job.get("feishu_chat_id") == chat_id]
    # Include the requested task if a message contains an explicit ID, even if
    # the task was created by another chat or before the current chat session.
    ids = re.findall(r"\b([a-f0-9]{12})\b", question.lower())
    selected: list[dict[str, Any]] = []
    for job_id in ids:
        job = get_job(job_id)
        if job:
            selected.append(job)
    for job in reversed(chat_jobs):
        if job not in selected:
            selected.append(job)
    if not selected:
        selected = list(reversed(all_jobs[-5:]))
    return json.dumps(
        {
            "current_time": now(),
            "user_question": question,
            "tasks": [compact_job(job) for job in selected[:5]],
        },
        ensure_ascii=False,
        indent=2,
    )


def fallback_assistant_reply(question: str, job: dict[str, Any] | None) -> str:
    if not job:
        return "当前群聊还没有找到评测任务。可以发送：测试 develop，或测试 PR 227。"
    progress = job.get("progress") or {}
    status = job.get("status")
    prefix = f"任务 {job['id']}（{job.get('source_label', 'EchoMem')}）"
    if status == "completed":
        return prefix + "\n" + format_result(job)
    if status == "failed":
        return f"{prefix}\n测试失败\n原因：{job.get('message', '未知错误')}"
    return (
        f"{prefix}\n状态：{job.get('message', status)}\n"
        f"阶段：{progress.get('label', '-')}"
        f" {progress.get('current', 0)}/{progress.get('total') or '?'}"
        f"（{progress.get('percent', 0)}%）\n"
        f"最近日志：{progress.get('last_log') or '暂无'}"
    )


def format_job_status(job: dict[str, Any]) -> str:
    progress = job.get("progress") or {}
    status = str(job.get("status") or "unknown")
    phase = progress.get("label") or "-"
    current = progress.get("current", 0)
    total = progress.get("total") or "?"
    percent = progress.get("percent", 0)
    lines = [
        f"任务 {job['id']}",
        f"状态：{job.get('message') or status}",
        f"阶段：{phase} {current}/{total}（{percent}%）",
        f"代码：{job.get('source_label') or '-'}",
        f"commit：{str(job.get('commit_sha') or '-')[:12]}",
    ]
    if job.get("image"):
        lines.append(
            "镜像："
            + ("缓存命中" if job.get("image_cached") else "本次新构建")
            + f"（{job.get('image')}）"
        )
    if status == "completed":
        return format_result(job)
    if status in {"failed", "interrupted"}:
        lines.append(f"原因：{job.get('message') or '未知错误'}")
    else:
        lines.append("最终准确率：尚未完成")
    return "\n".join(lines)


def answer_feishu_question(chat_id: str, question: str) -> str:
    job = latest_chat_job(chat_id)
    context = job_context(chat_id, question)
    if not DEFAULT_LLM_BASE_URL or not DEFAULT_LLM_MODEL or not DEFAULT_LLM_API_KEY:
        return fallback_assistant_reply(question, job)
    prompt = (
        "你是一个接入了 Memory Eval 任务系统的中文智能助手，像正常聊天助手一样理解用户。"
        "你可以回答评测进度、解释异常、解释准确率、说明如何发起 develop 或 PR 测试，"
        "也可以进行简短的普通对话。必须只依据任务 JSON 中的事实，不要把不同任务的信息混在一起。"
        "如果用户想发起测试但消息没有被系统识别为明确命令，请告诉用户正确格式："
        "“测试 develop”或“测试 PR 227”；不要假装已经创建任务。"
        "如果任务仍在运行，明确说明尚无最终准确率；如果任务失败，区分构建失败、容器失败、"
        "评测失败和服务重启中断。回答使用简体中文，表达自然，不必固定行数。"
        "\n\n用户问题："
        + question
        + "\n\n任务上下文：\n"
        + context
    )
    try:
        response = requests.post(
            DEFAULT_LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {DEFAULT_LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_LLM_MODEL,
                "temperature": 0.1,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是 Memory Eval 的智能任务助手。任务数据是唯一事实来源；"
                            "不能臆测、不能把旧任务错误归因给新任务。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        answer = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if answer:
            return answer[:1800]
    except Exception:
        app.logger.exception("Feishu assistant LLM request failed")
    return fallback_assistant_reply(question, job)


def feishu_access_token() -> str:
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return ""
    with FEISHU_LOCK:
        if FEISHU_TOKEN_CACHE["token"] and FEISHU_TOKEN_CACHE["expires_at"] > time.time() + 60:
            return str(FEISHU_TOKEN_CACHE["token"])
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        token = str(data.get("tenant_access_token", ""))
        if not token:
            raise RuntimeError(f"飞书 token 获取失败: {data.get('msg', 'unknown error')}")
        FEISHU_TOKEN_CACHE.update(
            token=token,
            expires_at=time.time() + int(data.get("expire", 7200)),
        )
        return token


def send_feishu_text(chat_id: str, text: str) -> None:
    token = feishu_access_token()
    if not token or not chat_id:
        app.logger.error(
            "cannot send Feishu text: token_configured=%s chat_id_present=%s text=%s",
            bool(token),
            bool(chat_id),
            text[:160],
        )
        return
    response = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"飞书文本消息发送失败: {payload.get('msg', 'unknown error')}")


def result_upload_path(job_id: str) -> Path:
    """Create and retain the artifact uploaded to the Feishu group."""
    result_dir = RESULTS_DIR / job_id
    RESULT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    package_path = RESULT_ARCHIVE_DIR / f"locomo-result-{job_id}.zip"
    if package_path.is_file() and package_path.stat().st_size > 0:
        return package_path
    if not result_dir.is_dir():
        raise RuntimeError(f"任务结果目录不存在，可能已按保留策略清理: {job_id}")
    temp_path = package_path.with_suffix(".zip.tmp")
    temp_path.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in result_dir.rglob("*"):
                if not path.is_file() or path == package_path:
                    continue
                archive.write(path, path.relative_to(result_dir))
        temp_path.replace(package_path)
    finally:
        temp_path.unlink(missing_ok=True)
    # Feishu's IM file upload API limits files to 30 MB.
    if package_path.stat().st_size <= 30 * 1024 * 1024:
        return package_path
    package_path.unlink(missing_ok=True)
    summary_path = result_dir / "summary.json"
    if not summary_path.is_file():
        raise RuntimeError("结果文件超过飞书 30 MB 限制，且没有 summary.json 可上传")
    return summary_path


def result_artifact_path(job_id: str, kind: str) -> Path:
    """Build a small, focused download package for memory inspection."""
    result_dir = RESULTS_DIR / job_id
    if not result_dir.is_dir():
        raise RuntimeError(f"任务结果目录不存在，可能已按保留策略清理: {job_id}")
    run_dirs = sorted(
        {
            path.parent
            for path in result_dir.rglob("summary.json")
            if path.is_file()
        },
        key=lambda path: path.stat().st_mtime,
    )
    source_dir = run_dirs[-1] if run_dirs else result_dir
    names = {
        "injected-memory": [
            "injected_memories.jsonl",
            "import_results.csv",
            "memory_provenance.json",
        ],
        "retrieved-memory": [
            "retrieval_traces.jsonl",
            "qa_results.csv",
            "tool_audits.jsonl",
            "tool_audits.json",
        ],
    }.get(kind)
    if names is None:
        raise ValueError(f"不支持的结果类型: {kind}")

    RESULT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    package_path = RESULT_ARCHIVE_DIR / f"locomo-{kind}-{job_id}.zip"
    if package_path.is_file() and package_path.stat().st_size > 0:
        return package_path
    temp_path = package_path.with_suffix(".zip.tmp")
    temp_path.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                path = source_dir / name
                if path.is_file():
                    archive.write(path, path.relative_to(result_dir))
            if kind == "retrieved-memory":
                trace_dir = source_dir / "agent_traces"
                if trace_dir.is_dir():
                    for path in trace_dir.rglob("*"):
                        if path.is_file():
                            archive.write(path, path.relative_to(result_dir))
        temp_path.replace(package_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return package_path


def cleanup_old_result_files() -> None:
    """Remove finished task files older than the configured retention period."""
    if RESULT_RETENTION_DAYS < 1:
        return
    cutoff = time.time() - RESULT_RETENTION_DAYS * 24 * 60 * 60
    jobs = {
        str(job.get("id")): job
        for job in read_jobs()
        if job.get("id")
    }
    removed = 0
    job_cache_root = ECHOMEM_WORKSPACE_CACHE / "jobs"
    if job_cache_root.is_dir():
        for path in job_cache_root.iterdir():
            if not path.is_dir():
                continue
            job = jobs.get(path.name)
            if not job or job.get("status") in {"queued", "running"}:
                continue
            timestamp = str(job.get("finished_at") or job.get("created_at") or "")
            try:
                job_time = datetime.fromisoformat(timestamp).timestamp()
            except (TypeError, ValueError, OverflowError):
                job_time = path.stat().st_mtime
            if job_time < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    for path in RESULTS_DIR.iterdir():
        if path.name == RESULT_ARCHIVE_DIR.name or not path.is_dir():
            continue
        job = jobs.get(path.name)
        if not job or job.get("status") in {"queued", "running"}:
            continue
        timestamp = str(job.get("finished_at") or job.get("created_at") or "")
        try:
            job_time = datetime.fromisoformat(timestamp).timestamp()
        except (TypeError, ValueError, OverflowError):
            job_time = path.stat().st_mtime
        if job_time >= cutoff:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1

    if RESULT_ARCHIVE_DIR.is_dir():
        for path in RESULT_ARCHIVE_DIR.iterdir():
            if not path.is_file() or path.stat().st_mtime >= cutoff:
                continue
            job_id = path.name.removeprefix("locomo-result-").removesuffix(".zip")
            job = jobs.get(job_id)
            if job and job.get("status") in {"queued", "running"}:
                continue
            path.unlink(missing_ok=True)
            removed += 1
    # Keep the task index aligned with the three-day artifact retention policy.
    # Active jobs are never removed, even if their timestamps are malformed.
    expired_ids: set[str] = set()
    for job in jobs.values():
        if job.get("status") in {"queued", "running"}:
            continue
        timestamp = str(job.get("finished_at") or job.get("created_at") or "")
        try:
            job_time = datetime.fromisoformat(timestamp).timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
        if job_time < cutoff:
            expired_ids.add(str(job["id"]))
    if expired_ids:
        with LOCK:
            current_jobs = read_jobs()
            kept_jobs = [
                job for job in current_jobs
                if str(job.get("id") or "") not in expired_ids
                or job.get("status") in {"queued", "running"}
            ]
            if len(kept_jobs) != len(current_jobs):
                write_jobs(kept_jobs)
                for job_id in expired_ids:
                    SECRETS.pop(job_id, None)
                removed += len(current_jobs) - len(kept_jobs)
    if removed:
        app.logger.info(
            "result retention cleanup removed %d files/directories/tasks older than %d days",
            removed,
            RESULT_RETENTION_DAYS,
        )


def result_cleanup_worker() -> None:
    while True:
        try:
            cleanup_old_result_files()
        except Exception:
            app.logger.exception("result retention cleanup failed")
        time.sleep(max(60, RESULT_CLEANUP_INTERVAL_S))


def upload_feishu_file(chat_id: str, path: Path) -> str:
    token = feishu_access_token()
    if not token or not chat_id:
        return ""
    if path.stat().st_size > 30 * 1024 * 1024:
        raise RuntimeError("结果文件超过飞书 30 MB 限制")
    response = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/files",
        headers={"Authorization": f"Bearer {token}"},
        data={"file_type": "stream", "file_name": path.name},
        files={"file": (path.name, path.open("rb"), "application/octet-stream")},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"飞书文件上传失败: {payload.get('msg', 'unknown error')}")
    file_key = str((payload.get("data") or {}).get("file_key", ""))
    if not file_key:
        raise RuntimeError("飞书文件上传成功但未返回 file_key")
    send_feishu_file(chat_id, file_key, path.name)
    return file_key


def _safe_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _failure_count(failure_breakdown: Any, mode_name: str) -> int:
    if not isinstance(failure_breakdown, list):
        return 0
    for item in failure_breakdown:
        if isinstance(item, dict) and item.get("mode") == mode_name:
            return int(item.get("count") or 0)
    return 0


def _iso_to_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def _evaluation_result_dir(job_id: str) -> Path:
    result_dir = RESULTS_DIR / job_id
    if (result_dir / "summary.json").is_file() and (result_dir / "config.json").is_file():
        return result_dir
    candidates = [
        path.parent
        for path in result_dir.rglob("summary.json")
        if (path.parent / "config.json").is_file()
    ] if result_dir.is_dir() else []
    if not candidates:
        raise RuntimeError("结果目录缺少 summary.json 或 config.json")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def feishu_bitable_fields(job_id: str) -> dict[str, Any]:
    """Extract the same fields as scripts/feishu_upload from a task result."""
    result_dir = _evaluation_result_dir(job_id)
    summary_path = result_dir / "summary.json"
    config_path = result_dir / "config.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cfg = config.get("config") or {}
    agent_opts = summary.get("agent_options") or {}
    metrics = _safe_get(summary, "strict_blackbox", "metrics", default={}) or {}
    categories = metrics.get("categories") or {}
    diagnosis = summary.get("diagnosis") or {}
    run_id = result_dir.name
    job = get_job(job_id) or {}
    source_label = str(job.get("source_label") or "EchoMem")
    develop_commit = str(job.get("develop_commit_sha") or "").strip()
    annotation = source_label
    if develop_commit:
        annotation += f"｜develop commit: {develop_commit}"
    fields: dict[str, Any] = {
        "运行ID": run_id,
        "运行时间": _iso_to_ms(summary.get("run_started_at")),
        "标注": annotation,
        "Benchmark": summary.get("benchmark"),
        "样本过滤器": summary.get("sample_filter"),
        "记忆后端": cfg.get("memory_backend"),
        "Agent插件": cfg.get("agent_plugin"),
        "QA Profile": summary.get("qa_profile"),
        "LLM模型": cfg.get("llm_model"),
        "温度": agent_opts.get("llm_temperature", cfg.get("llm_temperature")),
        "MaxTokens": cfg.get("llm_max_tokens"),
        "TopK": summary.get("top_k", cfg.get("top_k")),
        "记忆预算字符": summary.get(
            "memory_budget_chars", cfg.get("memory_budget_chars")
        ),
        "并发数": summary.get("qa_parallelism", cfg.get("concurrency")),
        "工具调用启用": bool(summary["tools_enabled"])
        if "tools_enabled" in summary
        else None,
        "运行状态": summary.get("status"),
        "总问题数": summary.get("total_questions"),
        "正确数": summary.get("judge_correct"),
        "错误数": summary.get("judge_wrong"),
        "准确率": summary.get("accuracy"),
        "Cat1准确率": _safe_get(categories, "1", "accuracy"),
        "Cat2准确率": _safe_get(categories, "2", "accuracy"),
        "Cat4准确率": _safe_get(categories, "4", "accuracy"),
        "检索覆盖率": diagnosis.get("retrieval_coverage"),
        "平均耗时s": summary.get("avg_qa_elapsed_s"),
        "E2E平均s": _safe_get(metrics, "end_to_end_s", "avg"),
        "E2E_P50_s": _safe_get(metrics, "end_to_end_s", "p50"),
        "E2E_P95_s": _safe_get(metrics, "end_to_end_s", "p95"),
        "E2E_P99_s": _safe_get(metrics, "end_to_end_s", "p99"),
        "检索延迟平均s": _safe_get(metrics, "retrieval_latency_s", "avg"),
        "检索延迟P95_s": _safe_get(metrics, "retrieval_latency_s", "p95"),
        "批次耗时s": metrics.get("batch_wall_clock_s"),
        "QA吞吐QPS": metrics.get("qa_throughput_qps"),
        "总PromptTokens": summary.get("total_prompt_tokens"),
        "总CompletionTokens": summary.get("total_completion_tokens"),
        "可见模型总Tokens": metrics.get("visible_model_total_tokens"),
        "每正确答案Tokens": metrics.get("tokens_per_correct"),
        "AnswerTokens平均": _safe_get(metrics, "answer_total_tokens", "avg"),
        "AnswerTokens_P95": _safe_get(metrics, "answer_total_tokens", "p95"),
        "JudgeTokens平均": _safe_get(metrics, "judge_total_tokens", "avg"),
        "请求成功率": metrics.get("request_success_rate"),
        "失败率": metrics.get("failure_rate"),
        "空检索率": metrics.get("empty_retrieval_rate"),
        "工具调用总数": summary.get("tool_call_total"),
        "平均迭代轮数": summary.get("avg_iterations"),
        "失败_证据未用": _failure_count(
            diagnosis.get("failure_breakdown"), "evidence_unused"
        ),
        "失败_时序推理": _failure_count(
            diagnosis.get("failure_breakdown"), "temporal_reasoning"
        ),
        "失败_证据不匹配": _failure_count(
            diagnosis.get("failure_breakdown"), "evidence_mismatch"
        ),
        "记忆来源": summary.get("memory_source"),
        "记忆复用来源": _safe_get(summary, "memory_reuse", "source"),
        "上传人": FEISHU_UPLOAD_USER,
        "备注": FEISHU_UPLOAD_NOTE,
    }
    return {
        key: round(value, 4) if isinstance(value, float) else value
        for key, value in fields.items()
        if value is not None
    }


def upload_feishu_bitable_result(job_id: str, artifact_path: Path) -> dict[str, str]:
    """Upload a result artifact and upsert its run record in Feishu Bitable."""
    if not FEISHU_BITABLE_APP_TOKEN or not FEISHU_BITABLE_TABLE_ID:
        return {"status": "disabled"}
    if not FEISHU_UPLOAD_USER:
        raise RuntimeError("未配置 FEISHU_UPLOAD_USER")
    token = feishu_access_token()
    upload_response = requests.post(
        "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "file_name": artifact_path.name,
            "parent_type": "bitable_image",
            "parent_node": FEISHU_BITABLE_APP_TOKEN,
            "size": str(artifact_path.stat().st_size),
        },
        files={
            "file": (
                artifact_path.name,
                artifact_path.open("rb"),
                "application/zip",
            )
        },
        timeout=180,
    )
    upload_response.raise_for_status()
    upload_payload = upload_response.json()
    if upload_payload.get("code", 0) != 0:
        raise RuntimeError(
            f"飞书素材上传失败: {upload_payload.get('msg', 'unknown error')}"
        )
    file_token = str((upload_payload.get("data") or {}).get("file_token") or "")
    if not file_token:
        raise RuntimeError("飞书素材上传成功但未返回 file_token")

    fields = feishu_bitable_fields(job_id)
    fields["附件"] = [{"file_token": file_token}]
    base = (
        "https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records"
    )
    search_response = requests.get(
        base,
        params={"filter": f'CurrentValue.[运行ID]="{job_id}"'},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    search_response.raise_for_status()
    items = (search_response.json().get("data") or {}).get("items") or []
    if items:
        record_id = str(items[0].get("record_id") or "")
        response = requests.put(
            f"{base}/{record_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"fields": fields},
            timeout=30,
        )
        action = "updated"
    else:
        response = requests.post(
            base,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"fields": fields},
            timeout=30,
        )
        record_id = str((response.json().get("data") or {}).get("record", {}).get("record_id") or "")
        action = "created"
    response.raise_for_status()
    payload = response.json()
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"飞书多维表格写入失败: {payload.get('msg', 'unknown error')}")
    return {"status": action, "file_token": file_token, "record_id": record_id}


def send_feishu_file(chat_id: str, file_key: str, file_name: str) -> None:
    token = feishu_access_token()
    response = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps(
                {"file_key": file_key, "file_name": file_name},
                ensure_ascii=False,
            ),
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"飞书文件消息发送失败: {payload.get('msg', 'unknown error')}")


def analyze_failure_with_llm(job_id: str, error_message: str) -> str:
    """Produce a short operator-facing diagnosis without changing job status."""
    if not DEFAULT_LLM_BASE_URL or not DEFAULT_LLM_MODEL or not DEFAULT_LLM_API_KEY:
        return ""
    evidence = job_observability(job_id, log_lines=180)
    runtime = evidence["runtime"]
    inspect_json = json.dumps(
        evidence["docker_inspect"],
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    platform_log = "\n".join(evidence["platform_logs"][-120:])
    docker_log = "\n".join(evidence["docker_logs"][-180:])
    prompt = (
        "你是 Memory Eval 的故障分析助手。只根据下面证据分析，不要编造准确率、"
        "不存在的日志或未执行的修复。请区分：EchoMem 代码问题、测试平台配置问题、"
        "依赖问题、模型服务问题、Docker/服务器问题。必须明确回答“是否需要修改 "
        "EchoMem”：是/否；只有明确看到 EchoMem 自身 traceback、代码异常或其配置"
        "解析错误，才能回答“是”。如果只是 Docker、平台参数、依赖、模型服务或"
        "网络问题，回答“否”。若证据不足，也回答“否”，并说明需要补充什么证据。"
        "输出 150 字以内，包含：分类、根因、是否需要修改 EchoMem、建议动作。\n\n"
        f"任务错误：{error_message}\n"
        f"运行证据摘要：\n{format_runtime_evidence(runtime)}\n"
        f"平台提取字段：{json.dumps(runtime, ensure_ascii=False, default=str)}\n"
        f"Docker inspect：\n{inspect_json}\n"
        f"测试平台日志：\n{platform_log[-14000:]}\n"
        f"EchoMem docker logs：\n{docker_log[-18000:]}"
    )
    try:
        response = requests.post(
            DEFAULT_LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {DEFAULT_LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_LLM_MODEL,
                "temperature": 0,
                "max_tokens": 300,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你只输出简短、准确、可执行的运维诊断。"
                            "不要建议修改 EchoMem，除非证据明确来自 EchoMem 自身代码。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get(
            "content"
        )
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
        return str(content or "").strip()[:1200]
    except Exception:
        app.logger.exception("failure analysis failed for job %s", job_id)
        return ""


def notify_feishu_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job or not job.get("feishu_chat_id"):
        return
    try:
        if job.get("status") == "completed":
            text = format_result(job) + f"\n详情：{job_detail_url(job_id)}"
            send_feishu_text(str(job["feishu_chat_id"]), text)
            artifact_path = result_upload_path(job_id)
            file_key = upload_feishu_file(
                str(job["feishu_chat_id"]), artifact_path
            )
            update_job(
                job_id,
                feishu_result_file=artifact_path.name,
                feishu_file_key=file_key,
            )
            try:
                bitable_result = upload_feishu_bitable_result(job_id, artifact_path)
                if bitable_result.get("status") != "disabled":
                    update_job(
                        job_id,
                        feishu_bitable_status=bitable_result.get("status"),
                        feishu_bitable_file_token=bitable_result.get("file_token"),
                        feishu_bitable_record_id=bitable_result.get("record_id"),
                        feishu_bitable_error="",
                    )
            except Exception as exc:
                update_job(job_id, feishu_bitable_error=str(exc)[:500])
                app.logger.exception("failed to upload Bitable result for job %s", job_id)
            return
        elif job.get("status") == "conflict":
            text = (
                f"无法开始测试\n代码: {job.get('source_label', '-')}\n任务 ID: {job_id}\n"
                f"原因: {job.get('message', '-')}\n"
                "请先解决 PR 与 develop 的冲突，再重新提交测试。\n"
                f"详情：{job_detail_url(job_id)}"
            )
        elif job.get("status") == "failed":
            text = (
                f"测试失败\n代码: {job.get('source_label', '-')}\n任务 ID: {job_id}\n"
                f"原因: {job.get('message', '-')}\n详情：{job_detail_url(job_id)}"
            )
            if job.get("failure_analysis"):
                text += f"\n异常分析: {job['failure_analysis']}"
        else:
            progress = job.get("progress") or {}
            text = (
                f"任务 {job_id}\n{job.get('message', '-')}\n"
                f"{progress.get('label', '')} "
                f"{progress.get('current', 0)}/{progress.get('total') or '?'}\n"
                f"详情：{job_detail_url(job_id)}"
            )
        send_feishu_text(str(job["feishu_chat_id"]), text)
    except Exception:
        app.logger.exception("failed to notify Feishu for job %s", job_id)
        try:
            send_feishu_text(
                str(job["feishu_chat_id"]),
                f"测试结果文件上传失败\n任务 ID: {job_id}\n"
                "准确率文字结果已发送，请稍后使用“结果 <任务ID>”重试。",
            )
        except Exception:
            app.logger.exception("failed to report Feishu upload error for job %s", job_id)


def parse_feishu_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    try:
        payload = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        payload = {}
    text = str(payload.get("text", content)).strip()
    text = re.sub(r"<at\b[^>]*>.*?</at>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^@\S+\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def is_all_members_mention(message: dict[str, Any]) -> bool:
    """Ignore Feishu broadcast mentions such as @所有人."""
    mentions = message.get("mentions") or []
    if isinstance(mentions, list):
        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            values = [
                mention.get("key"),
                mention.get("name"),
                mention.get("id"),
                (mention.get("id") or {}).get("user_id")
                if isinstance(mention.get("id"), dict)
                else "",
            ]
            if any(
                str(value or "").strip().lower() in {"@_all", "all", "所有人", "@所有人"}
                or "所有人" in str(value or "")
                for value in values
            ):
                return True
    content = message.get("content", "")
    raw_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    # ``\b?`` is invalid because a zero-width boundary cannot be quantified.
    # Match the broadcast token without swallowing ordinary @bot mentions.
    return bool(
        re.search(r"@(?:所有人|all)(?![A-Za-z0-9_])", raw_text, flags=re.IGNORECASE)
    )


def decrypt_feishu_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Decrypt Feishu event envelopes when an Encrypt Key is configured."""
    encrypted = payload.get("encrypt")
    if not encrypted:
        return payload
    if not FEISHU_ENCRYPT_KEY:
        app.logger.error(
            "received encrypted Feishu event but FEISHU_ENCRYPT_KEY is not configured"
        )
        raise RuntimeError("服务器缺少 FEISHU_ENCRYPT_KEY，无法处理加密飞书事件")
    try:
        key = hashlib.sha256(FEISHU_ENCRYPT_KEY.encode("utf-8")).digest()
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-K",
                key.hex(),
                "-iv",
                key[:16].hex(),
                "-a",
                "-A",
            ],
            input=str(encrypted).encode("ascii"),
            capture_output=True,
            check=True,
            timeout=5,
        )
        plaintext = result.stdout
        decoded = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        app.logger.exception("failed to decrypt Feishu event envelope")
        raise RuntimeError("飞书事件解密失败，请检查 FEISHU_ENCRYPT_KEY") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("飞书解密后的事件不是 JSON 对象")
    return decoded


def parse_test_command(text: str) -> tuple[str, int | None] | None:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    normalized = re.sub(r"[，。！？!?,:：]+$", "", normalized).strip()
    # Natural variants such as "更新 develop 后合并 PR340" still mean:
    # refresh the current develop baseline, then evaluate the PR merge result.
    natural_pr = re.search(
        r"(?:pr|pull request|pull)\s*#?\s*(\d+)",
        normalized,
    )
    mentions_develop_update = bool(
        re.search(
            r"(?:更新|刷新|拉取|同步|最新).{0,20}develop"
            r"|develop.{0,20}(?:更新|刷新|拉取|同步|最新)",
            normalized,
        )
    )
    mentions_merge_or_test = bool(
        re.search(r"(?:合并|测试|评测|跑|执行)", normalized)
    )
    if natural_pr and mentions_develop_update and mentions_merge_or_test:
        return "pr", int(natural_pr.group(1))
    if re.fullmatch(
        r"(?:test|测试|帮我测试|测试代码|测试一下)\s*develop(?:\s*代码)?",
        normalized,
    ):
        return "develop", None
    match = re.fullmatch(
        r"(?:test|测试|帮我测试|测试代码|测试一下)\s*"
        r"(?:pr|pull request|pull)\s*#?\s*(\d+)(?:\s*代码)?",
        normalized,
    )
    if match:
        return "pr", int(match.group(1))
    return None


def parse_test_command_with_llm(text: str) -> tuple[str, int | None] | None:
    """Understand natural-language test requests without letting the model act."""
    if not DEFAULT_LLM_BASE_URL or not DEFAULT_LLM_MODEL or not DEFAULT_LLM_API_KEY:
        return None
    prompt = (
        "判断用户是否想发起 EchoMem 记忆评测任务。只返回一个 JSON 对象，"
        "不得输出 Markdown 或解释。格式必须是："
        '{"intent":"test|other","source_ref":"develop|pr|null","pr_number":整数或null}。'
        "只有明确要求测试 develop 分支时 source_ref 才是 develop；"
        "只有明确要求测试某个 PR 且能识别 PR 编号时 source_ref 才是 pr。"
        "询问进度、结果、错误、准确率、普通聊天都返回 intent=other。"
        f"\n用户消息：{text[:1000]}"
    )
    try:
        response = requests.post(
            DEFAULT_LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {DEFAULT_LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_LLM_MODEL,
                "temperature": 0,
                "max_tokens": 160,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是严格的意图分类器，只输出合法 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=8,
        )
        response.raise_for_status()
        raw_content = (
            ((response.json().get("choices") or [{}])[0].get("message") or {}).get(
                "content"
            )
            or ""
        )
        if isinstance(raw_content, list):
            content = "".join(
                str(item.get("text") or "")
                for item in raw_content
                if isinstance(item, dict)
            ).strip()
        else:
            content = str(raw_content).strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        json_match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not json_match:
            return None
        payload = json.loads(json_match.group(0))
        if payload.get("intent") != "test":
            return None
        source_ref = str(payload.get("source_ref") or "").strip().lower()
        if source_ref == "develop":
            return "develop", None
        if source_ref == "pr":
            pr_number = int(payload.get("pr_number"))
            if pr_number > 0:
                return "pr", pr_number
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, requests.RequestException):
        app.logger.warning("LLM test-command parsing failed", exc_info=True)
    return None


def append_feishu_event_log(
    *,
    event_id: str,
    event_type: str,
    chat_id: str,
    text: str,
    outcome: str,
    job_id: str = "",
) -> None:
    """Persist a redacted callback trace so missing replies are diagnosable."""
    record = {
        "received_at": now(),
        "event_id": event_id,
        "event_type": event_type,
        "chat_id": chat_id,
        "text": text[:500],
        "outcome": outcome,
        "job_id": job_id,
    }
    try:
        FEISHU_EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FEISHU_EVENT_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        app.logger.exception("failed to persist Feishu callback trace")


def feishu_message_from_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Accept both current and legacy Feishu event envelope shapes."""
    event = payload.get("event") or {}
    if not isinstance(event, dict):
        event = {}
    message = event.get("message") or payload.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    chat_id = (
        message.get("chat_id")
        or event.get("chat_id")
        or event.get("open_chat_id")
        or payload.get("chat_id")
        or ""
    )
    return message, str(chat_id)


def enqueue_source_job(
    *,
    source_ref: str,
    pr_number: int | None,
    fixed_commit_sha: str = "",
    chat_id: str = "",
    force_rebuild: bool = False,
) -> dict[str, Any]:
    if not DEFAULT_LLM_BASE_URL or not DEFAULT_LLM_MODEL or not DEFAULT_LLM_API_KEY:
        raise ValueError("服务器尚未配置 DEFAULT_LLM_BASE_URL/DEFAULT_LLM_MODEL/DEFAULT_LLM_API_KEY")
    job_id = uuid.uuid4().hex[:12]
    label = source_label(source_ref, pr_number)
    job = {
        "id": job_id,
        "created_at": now(),
        "started_at": None,
        "finished_at": None,
        "status": "queued",
        "test_type": "full",
        "source_ref": source_ref,
        "pr_number": pr_number,
        "fixed_commit_sha": fixed_commit_sha,
        "source_label": label,
        "commit_sha": None,
        "echomem_http_port": ECHOMEM_HTTP_PORT,
        "mcp_port": ECHOMEM_MCP_PORT,
        "qa_concurrency": 1,
        "judge_concurrency": 1,
        "message": "等待执行",
        "feishu_chat_id": chat_id,
        "force_rebuild": force_rebuild,
        "progress": default_progress("queued"),
    }
    SECRETS[job_id] = {
        "llm_base_url": DEFAULT_LLM_BASE_URL,
        "llm_model": DEFAULT_LLM_MODEL,
        "embedding_model": os.getenv("DEFAULT_EMBEDDING_MODEL", "text-embedding-v3"),
        "llm_api_key": DEFAULT_LLM_API_KEY,
    }
    with LOCK:
        jobs = read_jobs()
        if active_job_count(jobs) >= MAX_JOBS:
            raise ValueError("任务列表已满")
        jobs.append(job)
        write_jobs(jobs)
    JOB_QUEUE.put(job_id)
    return job


@app.post("/api/bridge/jobs")
def bridge_create_job():
    payload = request.get_json(silent=True) or {}
    source_ref = str(payload.get("source_ref") or "").strip().lower()
    pr_number = payload.get("pr_number")
    fixed_commit_sha = str(
        payload.get("fixed_commit_sha") or payload.get("commit_sha") or ""
    ).strip()
    if source_ref not in {"develop", "pr", "commit"}:
        abort(400, "source_ref 必须是 develop、pr 或 commit")
    if source_ref == "commit":
        if not re.fullmatch(r"[0-9a-fA-F]{40}", fixed_commit_sha):
            abort(400, "commit 评测必须提供完整 40 位 commit SHA")
        pr_number = None
    if source_ref == "pr":
        try:
            pr_number = int(pr_number)
        except (TypeError, ValueError):
            abort(400, "pr_number 必须是整数")
        if pr_number <= 0:
            abort(400, "pr_number 必须大于 0")
    else:
        pr_number = None
    chat_id = str(payload.get("chat_id") or "")
    harness_session_id = str(payload.get("harness_session_id") or "")
    if not chat_id and harness_session_id:
        chat_id = harness_chat_for_session(harness_session_id)
    try:
        job = enqueue_source_job(
            source_ref=source_ref,
            pr_number=pr_number,
            fixed_commit_sha=fixed_commit_sha,
            chat_id=chat_id,
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"message": str(exc)}), 400
    return jsonify(compact_job(job)), 202


@app.get("/api/bridge/tools")
def bridge_tools():
    """Machine-readable tool contract for the conversational Harness layer."""
    return jsonify({
        "version": 1,
        "tools": [
            {
                "name": "create_memory_eval",
                "description": "Create a single-concurrency LoCoMo evaluation for EchoMem develop or a PR.",
                "method": "POST",
                "path": "/api/bridge/jobs",
                "input": {
                    "source_ref": {
                        "type": "string",
                        "enum": ["develop", "pr", "commit"],
                    },
                    "pr_number": {"type": "integer", "optional": True},
                    "fixed_commit_sha": {
                        "type": "string",
                        "optional": True,
                        "description": "Required when source_ref=commit",
                    },
                    "chat_id": {"type": "string", "optional": True},
                    "harness_session_id": {"type": "string", "optional": True},
                },
            },
            {
                "name": "get_memory_eval_status",
                "description": "Read live phase, logs, summary, warnings, and result metadata for one task.",
                "method": "GET",
                "path": "/api/bridge/jobs/{job_id}/status",
            },
            {
                "name": "get_memory_eval_result",
                "description": "Read the final result and stable detail URL for one task.",
                "method": "GET",
                "path": "/api/bridge/jobs/{job_id}/result",
            },
            {
                "name": "inspect_memory_eval",
                "description": "Read bounded live logs, Docker diagnostics, result files, and task metadata.",
                "method": "GET",
                "path": "/api/bridge/jobs/{job_id}/diagnosis",
            },
            {
                "name": "diagnose_memory_eval",
                "description": "Classify a task failure and ask the configured LLM for an operator-facing root-cause analysis.",
                "method": "GET",
                "path": "/api/bridge/jobs/{job_id}/diagnosis",
            },
            {
                "name": "recover_memory_eval",
                "description": (
                    "Perform one allowlisted operational action on the unchanged PR "
                    "input: retry, rebuild_retry, or restart_echomem. This never "
                    "modifies EchoMem source or fixes the PR."
                ),
                "method": "POST",
                "path": "/api/bridge/jobs/{job_id}/recover",
                "input": {
                    "action": {
                        "type": "string",
                        "enum": ["retry", "rebuild_retry", "restart_echomem"],
                    },
                },
            },
            {
                "name": "retry_memory_eval",
                "description": "Requeue a terminal develop or PR evaluation with the same source and chat.",
                "method": "POST",
                "path": "/api/bridge/jobs/{job_id}/retry",
            },
            {
                "name": "list_memory_eval_tasks",
                "description": "List recent tasks for a Feishu chat or Harness session.",
                "method": "GET",
                "path": "/api/bridge/jobs",
            },
            {
                "name": "get_latest_memory_eval_status",
                "description": "Read the newest task for the current Feishu chat or Harness session.",
                "method": "GET",
                "path": "/api/bridge/jobs/latest",
            },
        ],
    })


@app.get("/api/bridge/jobs")
def bridge_list_jobs():
    chat_id = str(request.args.get("chat_id") or "")
    session_id = str(request.args.get("harness_session_id") or "")
    if not chat_id and session_id:
        chat_id = harness_chat_for_session(session_id)
    jobs = read_jobs()
    if chat_id:
        jobs = [job for job in jobs if str(job.get("feishu_chat_id") or "") == chat_id]
    return jsonify({
        "jobs": [compact_job(job) for job in jobs[-20:]],
    })


@app.get("/api/bridge/jobs/latest")
def bridge_latest_job():
    chat_id = str(request.args.get("chat_id") or "")
    session_id = str(request.args.get("harness_session_id") or "")
    if not chat_id and session_id:
        chat_id = harness_chat_for_session(session_id)
    jobs = read_jobs()
    if chat_id:
        jobs = [job for job in jobs if str(job.get("feishu_chat_id") or "") == chat_id]
    if not jobs:
        return jsonify({"message": "当前会话没有评测任务"}), 404
    return jsonify(compact_job(jobs[-1]))


@app.get("/api/bridge/jobs/<job_id>/status")
def bridge_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"message": "找不到任务"}), 404
    return jsonify(compact_job(job))


@app.get("/api/bridge/jobs/<job_id>/result")
def bridge_job_result(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"message": "找不到任务"}), 404
    result = compact_job(job)
    result["result_url"] = url_for("job_detail", job_id=job_id, _external=True)
    return jsonify(result)


@app.get("/api/bridge/jobs/<job_id>/diagnosis")
def bridge_job_diagnosis(job_id: str):
    """Return bounded evidence plus a model-assisted diagnosis."""
    try:
        job = get_job(job_id)
        if not job:
            return jsonify({"message": "找不到任务"}), 404
        diagnosis = build_failure_diagnosis(
            job_id,
            str(job.get("message") or "用户请求诊断"),
        )
    except KeyError:
        return jsonify({"message": "找不到任务"}), 404
    try:
        analysis = diagnosis["text"]
    except Exception as exc:
        app.logger.exception("failed to diagnose job %s", job_id)
        analysis = f"诊断模型调用失败：{str(exc)[:400]}"
    diagnosis["analysis"] = analysis
    return jsonify(diagnosis)


def recover_memory_eval(job_id: str, action: str) -> dict[str, Any]:
    """Perform only explicitly allowlisted recovery operations."""
    action = action.strip().lower()
    if action not in {"retry", "rebuild_retry", "restart_echomem"}:
        raise ValueError("不支持的恢复动作")
    job = get_job(job_id)
    if not job:
        raise KeyError(job_id)

    if action == "restart_echomem":
        if job.get("status") != "running":
            raise ValueError("只有运行中的任务可以重启 EchoMem")
        client = docker.from_env()
        containers = client.containers.list(
            all=True,
            filters={"label": f"memory-eval.job={job_id}"},
        )
        echo_containers = [
            container
            for container in containers
            if (container.labels or {}).get("memory-eval.role") == "echomem"
        ]
        if not echo_containers:
            raise RuntimeError("当前任务没有找到 EchoMem 容器")
        echo_containers[0].restart(timeout=20)
        append_job_log(job_id, "Harness 请求重启 EchoMem 容器")
        update_job(job_id, message="Harness 已重启 EchoMem，等待健康检查")
        return {"job": compact_job(get_job(job_id) or job), "action": action}

    if job.get("status") in {"queued", "running"}:
        raise ValueError("任务仍在执行中，不能重复提交")
    source_ref = str(job.get("source_ref") or "")
    if source_ref not in {"develop", "pr", "commit"}:
        raise ValueError("该任务不是可恢复的 develop/PR 任务")
    retried = enqueue_source_job(
        source_ref=source_ref,
        pr_number=job.get("pr_number"),
        fixed_commit_sha=str(job.get("fixed_commit_sha") or ""),
        chat_id=str(job.get("feishu_chat_id") or ""),
        force_rebuild=action == "rebuild_retry",
    )
    return {
        "retried_from": job_id,
        "action": action,
        "job": compact_job(retried),
    }


@app.post("/api/bridge/jobs/<job_id>/recover")
def bridge_recover_job(job_id: str):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "retry")
    try:
        return jsonify(recover_memory_eval(job_id, action)), 202
    except KeyError:
        return jsonify({"message": "找不到任务"}), 404
    except (ValueError, RuntimeError, DockerException) as exc:
        return jsonify({"message": str(exc)}), 400


@app.post("/api/bridge/jobs/<job_id>/retry")
def bridge_retry_job(job_id: str):
    try:
        result = recover_memory_eval(job_id, "retry")
    except KeyError:
        return jsonify({"message": "找不到任务"}), 404
    except (ValueError, RuntimeError) as exc:
        return jsonify({"message": str(exc)}), 400
    return jsonify(result), 202


def monitor_container(job_id: str, container, log_path: Path) -> None:
    try:
        with log_path.open("ab") as log_file:
            for chunk in container.logs(stream=True, follow=True):
                log_file.write(chunk)
                log_file.flush()
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    update_progress_from_line(job_id, line)
        result = container.wait()
        exit_code = int(result.get("StatusCode", 1))
        status = "completed" if exit_code == 0 else "failed"
        progress = default_progress("completed" if exit_code == 0 else "failed")
        update_job(
            job_id,
            status=status,
            finished_at=now(),
            exit_code=exit_code,
            message="完成" if exit_code == 0 else "评测进程退出异常",
            progress=progress,
        )
    except Exception as exc:
        update_job(job_id, status="failed", finished_at=now(), message=f"运行错误: {exc}")
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def cleanup_stale_task_containers(
    client,
    *,
    keep_job_id: str = "",
) -> None:
    """Release containers owned by terminal jobs without touching live work."""
    jobs_by_id = {
        str(job.get("id") or ""): job
        for job in read_jobs()
        if str(job.get("id") or "")
    }
    live_statuses = {"queued", "running"}
    for container in client.containers.list(
        all=True,
        filters={"label": "memory-eval.role=echomem"},
    ):
        labels = container.labels or {}
        task_id = str(labels.get("memory-eval.job") or "")
        if keep_job_id and task_id == keep_job_id:
            continue
        # A second Web worker can briefly coexist during a restart. Never
        # delete a container belonging to a job that the authoritative job
        # index still considers live; reattach/recovery owns those containers.
        if jobs_by_id.get(task_id, {}).get("status") in live_statuses:
            continue
        try:
            container.remove(force=True)
            app.logger.warning(
                "removed stale EchoMem container %s for job %s",
                container.name,
                task_id or "unknown",
            )
        except Exception:
            app.logger.exception(
                "failed to remove stale EchoMem container %s",
                container.name,
            )


def run_source_job(job_id: str, secret_values: dict[str, str]) -> None:
    job = get_job(job_id)
    if not job:
        return
    echo_container = None
    eval_container = None
    try:
        update_job(
            job_id,
            status="running",
            started_at=job.get("started_at") or now(),
            message="准备 EchoMem 代码",
            progress=default_progress("prepare"),
        )
        prepared = prepare_echomem_source(job, secret_values)
        client = docker.from_env()
        cleanup_stale_task_containers(client, keep_job_id=job_id)
        job_cache = prepare_echomem_job_cache(job_id)
        echo_environment = {}
        for env_name in prepared.get("api_key_envs", []):
            env_name_upper = env_name.upper()
            if "EMBEDDING" in env_name_upper or "RERANK" in env_name_upper:
                echo_environment[env_name] = DEFAULT_EMBEDDING_API_KEY
            else:
                echo_environment[env_name] = secret_values["llm_api_key"]
        echo_environment["ECHOMEM_AUTO_COMMIT_THRESHOLD"] = (
            ECHOMEM_AUTO_COMMIT_THRESHOLD
        )
        echo_environment["ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE"] = (
            ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE
        )
        echo_environment["PYTHONPATH"] = "/opt/echomem/src"
        # EchoMem's local auth API requires a registry provisioning capability
        # for creating isolated tenants/users. Keep this per-task secret only
        # in the two containers; it is never persisted in the job record.
        registry_master_key = secrets.token_bytes(32)
        echo_environment["ECHOMEM_REGISTRY_MASTER_KEY"] = base64.b64encode(
            registry_master_key
        ).decode("ascii")
        provisioning_key = hmac.new(
            registry_master_key,
            b"echomem.registry-provisioning.v1",
            "sha256",
        ).hexdigest()
        echo_container = client.containers.run(
            prepared["image"],
            detach=True,
            name=f"memory-eval-echomem-{job_id}",
            ports={
                "8010/tcp": ("127.0.0.1", ECHOMEM_HTTP_PORT),
                "8001/tcp": ("127.0.0.1", ECHOMEM_MCP_PORT),
            },
            environment=echo_environment,
            volumes={
                str(prepared["source_dir"]): {
                    "bind": "/opt/echomem",
                    "mode": "ro",
                },
                str(job_cache): {
                    "bind": f"{ECHOMEM_WORKSPACE}/cache",
                    "mode": "rw",
                },
                str(prepared["config_path"]): {
                    "bind": f"{ECHOMEM_WORKSPACE}/config.json",
                    "mode": "ro",
                },
            },
            labels={"memory-eval.job": job_id, "memory-eval.role": "echomem"},
        )
        echo_container.reload()
        echo_network = (
            echo_container.attrs.get("NetworkSettings", {})
            .get("Networks", {})
            .get("bridge", {})
        )
        echo_ip = str(echo_network.get("IPAddress") or "")
        if not echo_ip:
            raise RuntimeError("无法获取 EchoMem 容器的 Docker 内网地址")
        update_job(
            job_id,
            status="running",
            started_at=job.get("started_at") or now(),
            message="启动 EchoMem 服务",
            progress=default_progress("prepare"),
        )
        # Probe inside EchoMem first, then fall back to the bridge IP.
        wait_for_echomem(echo_container, echo_ip, job_id)
        update_job(
            job_id,
            message="EchoMem 已启动，开始导入记忆",
            echomem_http_port=ECHOMEM_HTTP_PORT,
            mcp_port=ECHOMEM_MCP_PORT,
            progress=default_progress("import"),
        )
        command, environment = eval_command(
            {
                **job,
                "echomem_http_port": ECHOMEM_HTTP_PORT,
                "mcp_port": ECHOMEM_MCP_PORT,
                "echomem_provisioning_auth_key": provisioning_key,
            },
            secret_values,
        )
        log_path = RESULTS_DIR / job_id / "container.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # The eval container runs as RUN_UID/RUN_GID, while the Web worker
        # creates this directory as root. Make the mounted result directory
        # writable before starting the evaluator.
        for path in [log_path.parent, *log_path.parent.rglob("*")]:
            try:
                os.chown(path, int(RUN_UID), int(RUN_GID))
                if path.is_dir():
                    os.chmod(path, 0o777)
                else:
                    os.chmod(path, 0o666)
            except OSError:
                pass
        eval_container = client.containers.run(
            IMAGE,
            command=command,
            environment=environment,
            network_mode="host",
            user=f"{RUN_UID}:{RUN_GID}",
            volumes={
                str(DOCKER_RESULTS_DIR): {
                    "bind": "/app/results",
                    "mode": "rw",
                }
            },
            detach=True,
            remove=False,
            labels={"memory-eval.job": job_id, "memory-eval.role": "eval"},
        )
        with log_path.open("ab") as log_file:
            for chunk in eval_container.logs(stream=True, follow=True):
                log_file.write(chunk)
                log_file.flush()
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    update_progress_from_line(job_id, line)
        exit_code = int(eval_container.wait().get("StatusCode", 1))
        summary = result_summary(job_id)
        status = "completed" if exit_code == 0 else "failed"
        if exit_code == 0:
            progress = default_progress("completed")
        else:
            # Keep the last observed import/QA/Judge counters so a failed job
            # remains diagnosable instead of misleadingly showing 0%.
            previous_progress = dict((get_job(job_id) or {}).get("progress") or {})
            progress = default_progress("failed")
            for key in ("current", "total", "percent", "last_log"):
                if key in previous_progress:
                    progress[key] = previous_progress[key]
            progress["updated_at"] = now()
        update_job(
            job_id,
            status=status,
            finished_at=now(),
            exit_code=exit_code,
            summary=summary,
            message="完成" if exit_code == 0 else "评测进程退出异常",
            progress=progress,
        )
        if status == "failed":
            diagnosis = build_failure_diagnosis(
                job_id, "评测进程退出异常"
            )
            update_job(
                job_id,
                failure_analysis=diagnosis["text"],
                failure_diagnosis=diagnosis,
            )
            append_startup_incident(job_id, diagnosis)
        notify_feishu_job(job_id)
    except Exception as exc:
        current = get_job(job_id) or {}
        if current.get("merge_status") == "conflict":
            update_job(
                job_id,
                status="conflict",
                finished_at=now(),
                message=current.get("message")
                or f"PR {job.get('pr_number')} 与 develop 存在合并冲突，未开始评测",
                failure_analysis="",
                progress=default_progress("conflict"),
            )
            notify_feishu_job(job_id)
            return
        error_message = f"运行错误: {str(exc)[:500]}"
        diagnosis = build_failure_diagnosis(job_id, error_message)
        update_job(
            job_id,
            status="failed",
            finished_at=now(),
            message=error_message,
            failure_analysis=diagnosis["text"],
            failure_diagnosis=diagnosis,
            progress=default_progress("failed"),
        )
        append_startup_incident(job_id, diagnosis)
        notify_feishu_job(job_id)
    finally:
        if echo_container is not None:
            try:
                current = get_job(job_id) or {}
                if current.get("status") in {"failed", "interrupted"}:
                    capture_echomem_diagnostics(
                        echo_container,
                        job_id,
                        "final",
                    )
            except Exception:
                app.logger.exception(
                    "failed to capture final EchoMem diagnostics for %s",
                    job_id,
                )
        try:
            cleanup_stale_task_containers(docker.from_env())
        except Exception:
            app.logger.exception("failed to clean stale task containers")
        for container in (eval_container, echo_container):
            if container is None:
                continue
            try:
                container.remove(force=True)
            except Exception:
                pass
        current_job = get_job(job_id) or {}
        # Commit-specific EchoMem images are deliberately retained so later
        # evaluations of the same revision can reuse dependency installation.
        # Cleanup can be handled separately by an image retention policy.
        shutil.rmtree(SOURCE_ROOT / job_id, ignore_errors=True)


def run_job(job_id: str) -> None:
    job = get_job(job_id)
    secret_values = SECRETS.pop(job_id, None)
    if not secret_values and job and job.get("source_ref"):
        # Source jobs use server-side defaults, so they can resume after the
        # Web container restarts and its in-memory secret cache is lost.
        if DEFAULT_LLM_BASE_URL and DEFAULT_LLM_MODEL and DEFAULT_LLM_API_KEY:
            secret_values = {
                "llm_base_url": DEFAULT_LLM_BASE_URL,
                "llm_model": DEFAULT_LLM_MODEL,
                "embedding_model": os.getenv(
                    "DEFAULT_EMBEDDING_MODEL", "text-embedding-v3"
                ),
                "llm_api_key": DEFAULT_LLM_API_KEY,
            }
    if not job or not secret_values:
        update_job(job_id, status="interrupted", finished_at=now(), message="任务服务重启或密钥已过期")
        return

    if job.get("source_ref"):
        run_source_job(job_id, secret_values)
        return

    update_job(
        job_id,
        status="running",
        started_at=now(),
        message="正在运行",
        progress=default_progress("import"),
    )
    log_path = RESULTS_DIR / job_id / "container.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    os.chown(log_path.parent, int(RUN_UID), int(RUN_GID))
    try:
        client = docker.from_env()
        command, environment = eval_command(job, secret_values)
        container = client.containers.run(
            IMAGE,
            command=command,
            environment=environment,
            network_mode="host",
            user=f"{RUN_UID}:{RUN_GID}",
            volumes={
                str(DOCKER_RESULTS_DIR): {
                    "bind": "/app/results",
                    "mode": "rw",
                }
            },
            detach=True,
            remove=False,
            labels={"memory-eval.job": job_id},
        )
        monitor_container(job_id, container, log_path)
    except DockerException as exc:
        update_job(job_id, status="failed", finished_at=now(), message=f"Docker 错误: {exc}")
    except Exception as exc:
        update_job(job_id, status="failed", finished_at=now(), message=f"运行错误: {exc}")


def worker() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        try:
            run_job(job_id)
        finally:
            JOB_QUEUE.task_done()


def reattach_running_jobs() -> None:
    try:
        client = docker.from_env()
    except DockerException:
        return
    for job in read_jobs():
        if job.get("status") != "running":
            continue
        job_id = job["id"]
        containers = client.containers.list(
            all=True,
            filters={"label": f"memory-eval.job={job_id}"},
        )
        if job.get("source_ref"):
            echo_containers = [
                container
                for container in containers
                if (container.labels or {}).get("memory-eval.role") == "echomem"
            ]
            eval_containers = [
                container
                for container in containers
                if (container.labels or {}).get("memory-eval.role") == "eval"
            ]
            # A source task is resumable only when both sides of the
            # evaluation pair still exist. During startup there is a small
            # window between creating EchoMem and creating the evaluator.
            # Never delete a live task container from this recovery path:
            # an older Web worker may still be creating the other half.
            if not echo_containers or not eval_containers:
                app.logger.warning(
                    "leaving incomplete live task containers in place during "
                    "recovery: job=%s echo=%d eval=%d",
                    job_id,
                    len(echo_containers),
                    len(eval_containers),
                )
                continue
            containers = eval_containers
        if not containers:
            if job.get("source_ref"):
                # A source job may be between source preparation and creation
                # of its Docker containers when the Web process restarts.
                # Requeue it instead of losing the run.
                update_job(
                    job_id,
                    status="queued",
                    message="服务重启，准备阶段任务已自动重新排队",
                    progress=default_progress("prepare"),
                )
                JOB_QUEUE.put(job_id)
            else:
                update_job(
                    job_id,
                    status="interrupted",
                    finished_at=now(),
                    message="服务重启时未找到评测容器",
                )
            continue
        log_path = RESULTS_DIR / job_id / "container.log"
        threading.Thread(
            target=monitor_container,
            args=(job_id, containers[0], log_path),
            daemon=True,
            name=f"monitor-{job_id}",
        ).start()


@app.before_request
def reject_untrusted_hosts():
    host = request.host.split(":", 1)[0]
    if host not in ALLOWED_HOSTS:
        app.logger.warning("rejected request with untrusted Host: %s", host)
        abort(400)


@app.get("/")
def index():
    if not require_access():
        return redirect(url_for("login"))
    with LOCK:
        jobs = read_jobs()
    return render_template(
        "index.html",
        jobs=jobs[-20:][::-1],
        queued=JOB_QUEUE.qsize(),
        config_llm_base_url=DEFAULT_LLM_BASE_URL,
        config_llm_model=DEFAULT_LLM_MODEL,
        config_embedding_model=os.getenv(
            "DEFAULT_EMBEDDING_MODEL", "text-embedding-v3"
        ),
        config_echomem_http_port=ECHOMEM_HTTP_PORT,
        config_mcp_port=ECHOMEM_MCP_PORT,
    )


@app.get("/compare")
def compare():
    # Keep older Web images compatible with the newer navigation template.
    # The comparison UI is optional and must not affect task execution.
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    session["access_granted"] = True
    return redirect(url_for("index"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.post("/jobs")
def create_job():
    if not require_access():
        abort(403)
    with LOCK:
        if active_job_count(read_jobs()) >= MAX_JOBS:
            abort(429, "任务列表已满，请先清理历史任务")
    try:
        http_port = valid_port(request.form.get("echomem_http_port", "18140"))
        mcp_port = valid_port(request.form.get("mcp_port", "18141"))
        qa_concurrency = int(request.form.get("qa_concurrency", "2"))
        judge_concurrency = int(request.form.get("judge_concurrency", "2"))
    except ValueError:
        abort(400, "提交参数不正确")
    if qa_concurrency not in {1, 2, 4} or judge_concurrency not in {1, 2, 4}:
        abort(400, "并发数不正确")

    base_url = request.form.get("llm_base_url", "").strip()
    model = request.form.get("llm_model", "").strip()
    api_key = request.form.get("llm_api_key", "").strip()
    if not base_url or not model or not api_key:
        abort(400, "请完整填写模型配置")
    if not re.match(r"^https?://", base_url):
        abort(400, "模型地址必须以 http:// 或 https:// 开头")

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "created_at": now(),
        "started_at": None,
        "finished_at": None,
        "status": "queued",
        "test_type": request.form.get("test_type", "full"),
        "echomem_http_port": http_port,
        "mcp_port": mcp_port,
        "qa_concurrency": qa_concurrency,
        "judge_concurrency": judge_concurrency,
        "message": "等待执行",
        "progress": default_progress("queued"),
    }
    SECRETS[job_id] = {
        "llm_base_url": base_url,
        "llm_model": model,
        "embedding_model": request.form.get("embedding_model", "text-embedding-v3").strip(),
        "llm_api_key": api_key,
    }
    with LOCK:
        jobs = read_jobs()
        jobs.append(job)
        write_jobs(jobs)
    JOB_QUEUE.put(job_id)
    return redirect(url_for("job_detail", job_id=job_id))


def process_feishu_message(
    *,
    event_id: str,
    message: dict[str, Any],
    chat_id: str,
    text: str,
) -> None:
    """Handle a callback after the HTTP acknowledgement has been returned."""
    try:
        command = parse_test_command(text)
        if command is None:
            # Natural-language classification may take several seconds. It must
            # never run on the Feishu callback request thread.
            send_feishu_text(chat_id, "已收到，正在识别测试请求，请稍候。")
            command = parse_test_command_with_llm(text)
        if command:
            source_ref, pr_number = command
            try:
                job = enqueue_source_job(
                    source_ref=source_ref,
                    pr_number=pr_number,
                    chat_id=chat_id,
                )
                code_source = "分支: develop"
                if source_ref == "pr" and pr_number is not None:
                    code_source += f" · PR {pr_number}"
                send_feishu_text(
                    chat_id,
                    f"任务已创建\nLoCoMo / conv-30\n{code_source}\n"
                    f"任务 ID：{job['id']}\n"
                    f"实时进度与日志：\n{job_detail_url(job['id'])}\n"
                    "服务器单并发排队执行，完成后自动回传准确率和结果文件。",
                )
                append_feishu_event_log(
                    event_id=event_id,
                    event_type="message",
                    chat_id=chat_id,
                    text=text,
                    outcome="job_created",
                    job_id=str(job["id"]),
                )
            except Exception as exc:
                append_feishu_event_log(
                    event_id=event_id,
                    event_type="message",
                    chat_id=chat_id,
                    text=text,
                    outcome=f"job_create_failed:{str(exc)[:200]}",
                )
                send_feishu_text(chat_id, f"任务创建失败: {str(exc)[:300]}")
            return

        # Accept natural variants such as "pr测试 查询 <id>" and answer from
        # the authoritative job record. Never send an explicit task-id lookup
        # to LLM, otherwise stale chat context can attribute one task's error
        # to another task.
        status_match = re.search(
            r"(?:状态|status|结果|result|查询|查看|进度)\s*[:：]?\s*([a-f0-9]{12})",
            text.lower(),
        )
        if not status_match:
            id_match = re.search(r"\b([a-f0-9]{12})\b", text.lower())
            status_match = id_match
        if status_match:
            job = get_job(status_match.group(1))
            if not job:
                reply = "找不到这个任务 ID。"
            else:
                is_result_request = bool(
                    re.search(r"^(?:结果|result)\b", text.strip(), flags=re.IGNORECASE)
                )
                if is_result_request and job.get("status") == "completed":
                    send_feishu_text(
                        chat_id,
                        f"正在重新上传任务 {job['id']} 的结果文件和多维表格记录，请稍候。",
                    )
                    threading.Thread(
                        target=notify_feishu_job,
                        args=(job["id"],),
                        daemon=True,
                        name=f"feishu-result-retry-{job['id']}",
                    ).start()
                    return
                reply = format_job_status(job)
            send_feishu_text(chat_id, reply)
            return

        if HARNESS_ENABLED:
            send_feishu_text(chat_id, "已交给 Harness 处理，我会在同一群聊里回复。")
            harness_prompt_and_reply(chat_id, text)
        else:
            send_feishu_text(chat_id, answer_feishu_question(chat_id, text))
    except Exception:
        app.logger.exception("failed to process Feishu message event=%s", event_id)
        try:
            send_feishu_text(chat_id, "机器人处理失败，请稍后重试；也可以发送“查询 <任务ID>”。")
        except Exception:
            app.logger.exception("failed to report Feishu message error")


@app.post("/feishu/events")
def feishu_events():
    payload = request.get_json(silent=True) or {}
    try:
        payload = decrypt_feishu_payload(payload)
    except RuntimeError as exc:
        return jsonify({"code": 1, "msg": str(exc)}), 400
    if payload.get("type") == "url_verification":
        token = payload.get("token")
        if FEISHU_VERIFICATION_TOKEN and token != FEISHU_VERIFICATION_TOKEN:
            abort(403)
        return jsonify({"challenge": payload.get("challenge", "")})

    header = payload.get("header") or {}
    event_id = str(header.get("event_id", ""))
    if event_id:
        with FEISHU_LOCK:
            if event_id in FEISHU_EVENT_IDS:
                return jsonify({"code": 0})
            FEISHU_EVENT_IDS.add(event_id)
            if len(FEISHU_EVENT_IDS) > 5000:
                FEISHU_EVENT_IDS.clear()
                FEISHU_EVENT_IDS.add(event_id)
    event_type = str(header.get("event_type") or payload.get("event_type") or "message")
    message, chat_id = feishu_message_from_payload(payload)
    if is_all_members_mention(message):
        app.logger.info(
            "Ignoring Feishu all-members mention: chat=%s event=%s",
            chat_id,
            event_id,
        )
        append_feishu_event_log(
            event_id=event_id,
            event_type=event_type,
            chat_id=chat_id,
            text="",
            outcome="ignored_all_members_mention",
        )
        return jsonify({"code": 0})
    text = parse_feishu_text(message)
    app.logger.info(
        "Feishu event received: event=%s type=%s chat=%s text=%s",
        event_id,
        event_type,
        chat_id,
        text[:160],
    )
    if not chat_id:
        append_feishu_event_log(
            event_id=event_id,
            event_type=event_type,
            chat_id="",
            text=text,
            outcome="ignored_missing_chat_id",
        )
        return jsonify({"code": 0})

    append_feishu_event_log(
        event_id=event_id,
        event_type=event_type,
        chat_id=chat_id,
        text=text,
        outcome="accepted",
    )
    threading.Thread(
        target=process_feishu_message,
        kwargs={
            "event_id": event_id,
            "message": message,
            "chat_id": chat_id,
            "text": text,
        },
        daemon=True,
        name=f"feishu-event-{event_id[-8:] or uuid.uuid4().hex[:8]}",
    ).start()
    # Acknowledge immediately. Feishu may retry callbacks that spend time
    # waiting on an LLM or the Feishu send-message API.
    return jsonify({"code": 0})



@app.get("/jobs/<job_id>")
def job_detail(job_id: str):
    if not require_access():
        return redirect(url_for("login"))
    job = get_job(job_id)
    if not job:
        abort(404)
    job = {
        **job,
        "summary": compact_job(job).get("summary") or {},
    }
    result_dir = RESULTS_DIR / job_id
    files = []
    if result_dir.exists():
        files = sorted(
            str(path.relative_to(result_dir))
            for path in result_dir.rglob("*")
            if path.is_file()
        )
    progress = job.get("progress") or default_progress(job.get("status", "queued"))
    return render_template(
        "job.html",
        job=job,
        progress=progress,
        files=files,
        eval_details=evaluation_details(job_id),
    )


@app.get("/api/jobs/<job_id>")
def job_api(job_id: str):
    if not require_access():
        abort(403)
    job = get_job(job_id)
    if not job:
        abort(404)
    return jsonify({**job, "summary": compact_job(job).get("summary") or {}})


@app.get("/api/jobs/<job_id>/evaluation")
def job_evaluation_api(job_id: str):
    if not require_access():
        abort(403)
    if not get_job(job_id):
        abort(404)
    return jsonify(evaluation_details(job_id))


@app.get("/jobs/<job_id>/files/<path:filename>")
def job_file(job_id: str, filename: str):
    if not require_access():
        abort(403)
    path = (RESULTS_DIR / job_id / filename).resolve()
    root = (RESULTS_DIR / job_id).resolve()
    if root not in path.parents or not path.is_file():
        abort(404)
    return send_file(path)


@app.get("/jobs/<job_id>/download/<kind>")
def job_download(job_id: str, kind: str):
    if not require_access():
        abort(403)
    job = get_job(job_id)
    if not job:
        abort(404)
    if kind not in {"injected-memory", "retrieved-memory"}:
        abort(404)
    try:
        package_path = result_artifact_path(job_id, kind)
    except (OSError, RuntimeError, ValueError):
        abort(404)
    return send_file(
        package_path,
        as_attachment=True,
        download_name=package_path.name,
        mimetype="application/zip",
    )


def initialize() -> None:
    cleanup_old_result_files()
    with LOCK:
        jobs = read_jobs()
        changed = False
        for job in jobs:
            if job.get("status") == "queued":
                if job.get("source_ref"):
                    job["message"] = "服务已恢复，任务等待重新执行"
                else:
                    job["status"] = "interrupted"
                    job["finished_at"] = now()
                    job["message"] = "服务重启，任务未继续执行"
                changed = True
            elif (
                job.get("source_ref")
                and job.get("status") == "interrupted"
                and job.get("message") == "服务重启时未找到评测容器"
            ):
                job["status"] = "queued"
                job["finished_at"] = None
                job["message"] = "服务已恢复，任务自动重新排队"
                job["progress"] = default_progress("prepare")
                changed = True
        if changed:
            write_jobs(jobs)
    for job in read_jobs():
        if job.get("status") == "queued" and job.get("source_ref"):
            JOB_QUEUE.put(job["id"])
    reattach_running_jobs()
    threading.Thread(target=worker, daemon=True, name="memory-eval-worker").start()
    threading.Thread(
        target=result_cleanup_worker,
        daemon=True,
        name="result-retention-worker",
    ).start()


initialize()
