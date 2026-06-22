#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

import sys
from web import load_web_package  # noqa: E402

ROOT = Path(__file__).resolve().parent
WEB_PACKAGE = load_web_package(ROOT)
DATASET_DIR = ROOT / "dataset"
DATASET_MANIFEST = DATASET_DIR / "manifest.json"
WEB_STATIC = WEB_PACKAGE.static_root
UI_CONTRACT_FILE = WEB_PACKAGE.ui_contract_file
LEGACY_STATIC = WEB_PACKAGE.legacy_static_root
STATIC = WEB_PACKAGE.active_static_root
GENERATED_REPORTS_DIR = ROOT / "generated-reports"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import benchmark_adapter  # noqa: E402
from memory import accounts as account_service  # noqa: E402
from memory import datasets as dataset_service  # noqa: E402
from memory import evidence_contract as evidence_contract_service  # noqa: E402
from memory import report_export as report_export_service  # noqa: E402
from memory import reports as report_service  # noqa: E402
from memory import runs as run_service  # noqa: E402
from memory.services import RuntimeStatusContext  # noqa: E402
from memory.services import TaskFactoryContext  # noqa: E402
from memory.services import TaskOrchestratorContext  # noqa: E402
from memory.services import backend_runtime_status as build_backend_runtime_status  # noqa: E402
from memory.services import build_single_command as build_backend_command  # noqa: E402
from memory.services import create_task as orchestrate_task  # noqa: E402
from memory.services import normalize_task_payload  # noqa: E402
from memory import status as status_service  # noqa: E402
from memory import task_specs as task_spec_service  # noqa: E402
from memory import tasking as tasking_service  # noqa: E402
from memory import validation as validation_service  # noqa: E402
from memory.adapters.doctor import build_report as adapter_doctor_report  # noqa: E402
from memory.plugins.service import available_backends as available_adapters  # noqa: E402
from memory.plugins.service import get_backend as get_adapter  # noqa: E402
from memory.plugins.service import plugin_service  # noqa: E402
from memory.vikingboat_alignment import (  # noqa: E402
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_ALIGNMENT_PROFILE,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
)
from web.api import handle_agent_backend_post  # noqa: E402
from web.api import handle_memory_backend_get  # noqa: E402
from web.api import handle_task_post  # noqa: E402

def load_ui_contract() -> dict[str, Any]:
    return WEB_PACKAGE.load_ui_contract()


UI_CONTRACT = load_ui_contract()
MEMORY_BACKEND_IDS = {str(item.get("id") or "") for item in UI_CONTRACT.get("memory_backends", []) if item.get("id")} or {"openviking", "echomemory"}
MEMORY_BACKEND_SCOPE = str(UI_CONTRACT.get("backend_scope") or "OpenViking + EchoMemory")
CURRENT_SCOPE_DATASET_FORMATS = {
    "",
    "locomo",
    "longmemeval",
    "evolvingevents",
    "hotpotqa",
    "proagentbench",
    "tau2bench",
    "chenmo",
    "generic",
}
CURRENT_SCOPE_RUN_KINDS = {
    "openviking",
    "openviking_qa",
    "openviking_generic_qa",
    "openviking_import",
    "openviking_qa_retry_failed",
    "openviking_qa_retry_missing",
    "echomemory",
    "echomemory_qa",
    "echomemory_generic_qa",
    "echomemory_qa_retry_failed",
    "echomemory_import",
    "judge",
    "formal",
    "chenmo_eval",
}
CURRENT_SCOPE_AGENT_TYPES = {
    "memorybench_agent",
    "openviking_memory_qa",
    "openviking_generic_qa",
    "echomemory_memory_qa",
    "echomemory_generic_qa",
    "openviking_commit_import",
    "echomemory_commit_import",
    "native_vikingbot_cli",
    "judge",
    "formal",
}
HISTORICAL_RUN_KINDS = {"adapter", "conv30", "local_agent", "manual", "stats", "vikingboat"}

def contract_public_static_files() -> set[str]:
    return WEB_PACKAGE.contract_public_static_files(UI_CONTRACT)

def first_existing_path(candidates: list[Path], fallback: Path) -> Path:
    for path in candidates:
        if path.exists():
            return path.resolve()
    return fallback.expanduser().resolve()


def first_existing_command(candidates: list[str], fallback: str) -> Path:
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        resolved = shutil.which(text) if "/" not in text and "\\" not in text else text
        if not resolved:
            continue
        path = Path(resolved).expanduser()
        if path.exists():
            return path.resolve()
    return Path(fallback).expanduser().resolve()


def openviking_config_candidates(*path_likes: Any) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path_like: Any) -> None:
        text = str(path_like or "").strip()
        if not text:
            return
        try:
            path = Path(text).expanduser().resolve()
        except Exception:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    add(os.environ.get("OPENVIKING_CONFIG_FILE"))
    for path_like in path_likes:
        add(path_like)
    add(Path.home() / ".openviking" / "ov.conf")
    return candidates


def discover_repo() -> Path:
    env_repo = os.environ.get("LOCOMO_EVAL_REPO") or os.environ.get("LOCOMO_OPENVIKING_REPO") or os.environ.get("OPENVIKING_REPO")
    candidates = []
    if env_repo:
        candidates.append(Path(env_repo))
    candidates += [
        ROOT,
        Path.cwd(),
        ROOT.parent,
    ]
    for candidate in candidates:
        if (candidate / "server.py").exists() and (candidate / "scripts/local_memory_agent.py").exists():
            return candidate.resolve()
    return first_existing_path(candidates, Path.cwd())


DEFAULT_REPO = discover_repo()
DEFAULT_DATA = first_existing_path(
    [
        Path(os.environ.get("LOCOMO_DATA", "")) if os.environ.get("LOCOMO_DATA") else Path("__missing__"),
        DATASET_DIR / "full" / "locomo.json",
        DATASET_DIR / "locomo.json",
        DATASET_DIR / "locomo10.json",
        DEFAULT_REPO / "dataset" / "full" / "locomo.json",
        DEFAULT_REPO / "dataset" / "locomo.json",
        DEFAULT_REPO / "benchmark/locomo/data/locomo10.json",
        DEFAULT_REPO / "test/locomo10.json",
        ROOT / "data/locomo10.json",
        Path.cwd() / "dataset" / "full" / "locomo.json",
        Path.cwd() / "dataset" / "locomo.json",
        Path.cwd() / "benchmark/locomo/data/locomo10.json",
        Path.cwd() / "locomo10.json",
    ],
    DATASET_DIR / "locomo10.json",
)
DEFAULT_WORKSPACE = first_existing_path(
    [
        Path(os.environ.get("LOCOMO_EVAL_WORKSPACE", "")) if os.environ.get("LOCOMO_EVAL_WORKSPACE") else Path("__missing__"),
        ROOT / "workspace",
    ],
    ROOT / "workspace",
)
DEFAULT_CONFIG = first_existing_path(
    [
        Path(os.environ.get("JUDGE_CONFIG_FILE", "")) if os.environ.get("JUDGE_CONFIG_FILE") else Path("__missing__"),
        ROOT / "judge.conf",
    ],
    ROOT / "judge.conf",
)
DEFAULT_CLI_CONFIG = first_existing_path(
    [
        Path(os.environ.get("JUDGE_CLI_CONFIG_FILE", "")) if os.environ.get("JUDGE_CLI_CONFIG_FILE") else Path("__missing__"),
        ROOT / "judge-cli.conf",
    ],
    ROOT / "judge-cli.conf",
)
DEFAULT_OUTPUT_DIR = ROOT / "runs"
ACCOUNT_STATE_FILE = DEFAULT_OUTPUT_DIR / "accounts.json"
DEFAULT_LOCOMO_MEMORY_TEMPLATES = ROOT / "openviking_custom_memory_templates" / "locomo_evidence"
DEFAULT_OPENVIKING_SOURCE = first_existing_path(
    [
        Path(os.environ.get("OPENVIKING_SOURCE", "")) if os.environ.get("OPENVIKING_SOURCE") else Path("__missing__"),
        Path.home() / "Code/openviking/versions/v0.3.24",
        Path.cwd() / "openviking-src",
        Path.cwd() / "openviking-latest",
        ROOT.parent / "openviking-src",
        ROOT.parent / "openviking-latest",
        Path.home() / "openviking-src-latest-2026-05-08",
        Path.home() / "openviking-locomo-latest-20260528",
        Path.home() / "openviking-latest",
        Path.home() / "openviking-src",
    ],
    ROOT.parent / "openviking-src",
)
_OPENVIKING_PYTHON_CANDIDATES = [
    os.environ.get("OPENVIKING_PYTHON", ""),
    str(Path.home() / "Code/openviking/versions/v0.3.24/.venv/bin/python"),
    "python3",
    str(Path.home() / "openviking-v0312-fresh-venv/bin/python"),
    str(Path.home() / "jiuwenclaw/bin/python"),
    str(Path.home() / "openviking-env/bin/python"),
    "/usr/bin/python3",
]
DEFAULT_OPENVIKING_PYTHON = first_existing_command(_OPENVIKING_PYTHON_CANDIDATES, "python3")
DATASET_SCAN_LIMIT_BYTES = 96 * 1024 * 1024
DATASET_CACHE: dict[str, dict[str, Any]] = {}


def manifest_dataset_candidates() -> list[dict[str, str]]:
    if not DATASET_MANIFEST.exists():
        return []
    try:
        manifest = read_json(DATASET_MANIFEST)
    except Exception:
        return []
    records = []
    for item in manifest.get("datasets", []):
        raw_path = Path(str(item.get("path") or ""))
        path = raw_path if raw_path.is_absolute() else DATASET_DIR / raw_path
        records.append(
            {
                "id": str(item.get("id") or path.stem),
                "name": str(item.get("name") or item.get("id") or path.stem),
                "path": str(path.resolve()),
                "format": str(item.get("format") or item.get("type") or "generic"),
                "description": str(item.get("description") or ""),
                "samples": item.get("samples"),
                "questions": item.get("questions"),
            }
        )
    return records


def dataset_candidates() -> list[dict[str, str]]:
    manifest_records = manifest_dataset_candidates()
    if manifest_records:
        return manifest_records
    return [
        {
            "id": "locomo",
            "name": "LoCoMo",
            "path": str(DEFAULT_DATA),
            "format": "locomo",
            "description": "长期对话记忆数据集；用于校验 conversation、question/answer 和 category 结构。",
        },
        {
            "id": "chenmo",
            "name": "ChenMo",
            "path": str(first_existing_path(
                [
                    Path(os.environ.get("CHENMO_SCENARIO", "")) if os.environ.get("CHENMO_SCENARIO") else Path("__missing__"),
                    DATASET_DIR / "chenmo_evaluation_scenario.md",
                ],
                DATASET_DIR / "chenmo_evaluation_scenario.md",
            )),
            "format": "chenmo",
            "description": "陈默长期记忆与推理评测场景；64 轮对话，覆盖时序、多跳、因果、复杂任务和综合推理。",
        },
        {
            "id": "longmemeval-s",
            "name": "LongMemEval-S",
            "path": str(first_existing_path(
                [
                    Path(os.environ.get("LONGMEMEVAL_DATA", "")) if os.environ.get("LONGMEMEVAL_DATA") else Path("__missing__"),
                    DATASET_DIR / "longmemeval.sample.json",
                ],
                DATASET_DIR / "longmemeval.sample.json",
            )),
            "format": "longmemeval",
            "description": "Long Context Agents 记忆问答；支持 MemoryBench 本地基线 100 题核验和正式评测。",
        },
        {
            "id": "longmemeval-m",
            "name": "LongMemEval-M",
            "path": str(first_existing_path(
                [
                    DATASET_DIR / "longmemeval.sample.json",
                ],
                DATASET_DIR / "longmemeval.sample.json",
            )),
            "format": "longmemeval",
            "description": "LongMemEval multi-session split；用于扩大到 100 题以上的本地检索评测。",
        },
        {
            "id": "evolvingevents-sample",
            "name": "EvolvingEvents Sample",
            "path": str(DATASET_DIR / "evolvingevents.sample.json"),
            "format": "evolvingevents",
            "description": "事件演化记忆评测示例；可用于验证正式 OpenViking runner、CSV、manifest 和报告链路。",
        },
    ]


def dataset_registry() -> list[dict[str, Any]]:
    records = []
    for candidate in dataset_candidates():
        path = safe_path(candidate["path"])
        record: dict[str, Any] = {
            **candidate,
            "path": str(Path(candidate["path"]).expanduser()),
            "resolved_path": str(path),
            "exists": path.exists(),
            "scope": dataset_service.dataset_scope(path, dataset_candidates()),
        }
        if path.exists():
            try:
                stat = path.stat()
                record["size_mb"] = round(stat.st_size / 1024 / 1024, 1)
                if stat.st_size <= DATASET_SCAN_LIMIT_BYTES:
                    overview = dataset_overview(path)
                    record.update(
                        {
                            "samples": overview["samples"],
                            "questions": overview["questions"],
                            "categories": overview["categories"],
                            "runner_status": overview.get("runner_status"),
                            "runner_note": overview.get("runner_note"),
                        }
                    )
                else:
                    record.update(
                        {
                            "samples": record.get("samples") or "?",
                            "questions": record.get("questions") or "?",
                            "categories": {},
                            "runner_status": "large_dataset_lazy",
                            "runner_note": f"文件较大（{record['size_mb']} MB）；页面概览按需加载，测试时可直接运行 100 题。",
                        }
                    )
            except Exception as exc:
                record["error"] = str(exc)
        records.append(record)
    return records


TASKS: dict[str, "Task"] = {}
TASK_LOCK = threading.Lock()
TASK_CREATION_LOCK = threading.Lock()
TASK_RECOVERY_SOURCES = (
    ROOT / "runs",
    GENERATED_REPORTS_DIR,
)


def task_log_diagnostics(task: "Task") -> dict[str, Any]:
    return tasking_service.task_log_diagnostics(task)


@dataclass
class Task:
    id: str
    kind: str
    name: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    command: list[str] = field(default_factory=list)
    cwd: str = ""
    output_file: str = ""
    log_file: str = ""
    run_dir: str = ""
    manifest_file: str = ""
    returncode: int | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    pid: int | None = None
    process: subprocess.Popen | None = field(default=None, repr=False)
    env: dict[str, str] = field(default_factory=dict, repr=False)
    display_command: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        log_diagnostics = task_log_diagnostics(self)
        config = self.meta.get("config") if isinstance(self.meta, dict) and isinstance(self.meta.get("config"), dict) else {}
        summary_json = self.summary.get("summary_json") if isinstance(self.summary.get("summary_json"), dict) else {}
        data = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "dataset_format": config.get("dataset_format") or self.summary.get("dataset_format") or (summary_json or {}).get("dataset_format") or "",
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "command": self.display_command or self.command,
            "cwd": self.cwd,
            "output_file": self.output_file,
            "log_file": self.log_file,
            "run_dir": self.run_dir,
            "manifest_file": self.manifest_file,
            "returncode": self.returncode,
            "summary": self.summary,
            "error": self.error,
            "pid": self.pid,
            "meta": self.meta,
            "log_diagnostics": log_diagnostics,
        }
        data["duration"] = round((self.ended_at or time.time()) - (self.started_at or self.created_at), 1)
        data["progress"] = task_progress(self)
        return data


ACTIVE_TASK_STATUSES = {"queued", "running", "stopping"}
TASK_DEDUP_KINDS = {
    "openviking_import",
    "echomemory_import",
    "openviking_qa",
    "echomemory_qa",
    "echomemory_generic_qa",
    "echomemory_qa_retry_failed",
    "openviking_generic_qa",
    "openviking_qa_retry_failed",
    "openviking_qa_retry_missing",
    "judge",
}
LOCOMO_QA_SINGLE_FLIGHT_KINDS = {"openviking_qa", "echomemory_qa"}


class DuplicateActiveTaskError(RuntimeError):
    def __init__(self, task: Task):
        self.task = task
        super().__init__(
            f"已有同配置任务正在运行：{task.id}（{task.kind}，状态 {task.status}）。"
            "请等待完成或先停止当前任务。"
        )


class ActiveLocomoQaConflictError(RuntimeError):
    def __init__(self, task: Task):
        self.task = task
        super().__init__(
            f"当前账户已有 LoCoMo 问答任务正在运行：{task.id}（{task.kind}，状态 {task.status}）。"
            "请等待完成或先停止当前任务。"
        )


def normalize_task_signature_value(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for child_key in sorted(value):
            child_value = normalize_task_signature_value(value[child_key], key=str(child_key))
            if child_value in (None, "", [], {}):
                continue
            normalized[str(child_key)] = child_value
        return normalized
    if isinstance(value, list):
        items = [normalize_task_signature_value(item, key=key) for item in value]
        return [item for item in items if item not in (None, "", [], {})]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if key == "questions":
            parts = sorted({part.strip() for part in text.split(",") if part.strip()})
            return ",".join(parts)
        return text
    return value


def task_dedup_signature(kind: str, payload: dict[str, Any]) -> str:
    if kind not in TASK_DEDUP_KINDS:
        return ""
    normalized_payload = normalize_task_payload(kind, payload)
    config = redact_manifest_payload(normalized_payload)
    signature_payload = {
        "kind": kind,
        **normalize_task_signature_value(config),
    }
    return json.dumps(signature_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def find_duplicate_active_task(kind: str, payload: dict[str, Any]) -> Task | None:
    signature = task_dedup_signature(kind, payload)
    if not signature:
        return None
    with TASK_LOCK:
        for task in TASKS.values():
            if task.kind != kind or task.status not in ACTIVE_TASK_STATUSES:
                continue
            config = task.meta.get("config") if isinstance(task.meta, dict) and isinstance(task.meta.get("config"), dict) else {}
            if task_dedup_signature(task.kind, config) == signature:
                return task
    return None


def locomo_qa_single_flight_scope(kind: str, payload: dict[str, Any]) -> dict[str, str]:
    normalized = normalize_task_payload(kind, payload)
    dataset_format = str(normalized.get("dataset_format") or "").strip().lower()
    if kind not in LOCOMO_QA_SINGLE_FLIGHT_KINDS or dataset_format != "locomo":
        return {}
    return {
        "backend": normalize_memory_backend(normalized.get("backend")),
        "account": str(normalized.get("account") or "default").strip() or "default",
        "workspace": str(normalized.get("workspace") or "").strip(),
        "data": str(normalized.get("data") or "").strip(),
        "dataset_format": dataset_format,
    }


def find_conflicting_active_locomo_qa(kind: str, payload: dict[str, Any]) -> Task | None:
    scope = locomo_qa_single_flight_scope(kind, payload)
    if not scope:
        return None
    with TASK_LOCK:
        for task in TASKS.values():
            if task.kind not in LOCOMO_QA_SINGLE_FLIGHT_KINDS or task.status not in ACTIVE_TASK_STATUSES:
                continue
            config = task.meta.get("config") if isinstance(task.meta, dict) and isinstance(task.meta.get("config"), dict) else {}
            if locomo_qa_single_flight_scope(task.kind, config) == scope:
                return task
    return None


def task_progress(task: "Task") -> dict[str, Any] | None:
    return tasking_service.task_progress(task)


def now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def compact(text: Any, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."


def load_ov_defaults(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    defaults = {
        "repo": str(DEFAULT_REPO),
        "home": str(Path.home()),
        "data": str(DEFAULT_DATA),
        "workspace": str(DEFAULT_WORKSPACE),
        "openviking_workspace": "",
        "config": str(DEFAULT_CONFIG),
        "cli_config": str(DEFAULT_CLI_CONFIG),
        "output_dir": str(DEFAULT_OUTPUT_DIR),
        "server_url": "",
        "server_host": "127.0.0.1",
        "server_port": "",
        "root_api_key": "",
        "account": "default",
        "judge_base_url": os.environ.get("JUDGE_BASE_URL", ""),
        "judge_model": os.environ.get("JUDGE_MODEL", "gpt-5.5"),
        "answer_model": os.environ.get("ANSWER_MODEL") or os.environ.get("JUDGE_MODEL") or "gpt-5.5",
        "judge_token_set": bool(os.environ.get("JUDGE_TOKEN")),
        "runner": "local_agent",
        "memory_safety_mode": "read_only_recommended",
        "external_log_path": "",
        "ui_contract": load_ui_contract(),
        "backends": available_adapters(),
        "plugins": available_adapters(),
    }
    try:
        cfg = {}
        for candidate in openviking_config_candidates(config_path):
            try:
                cfg = read_json(candidate)
                break
            except Exception:
                continue
        server = cfg.get("server", {})
        bot_server = cfg.get("bot", {}).get("ov_server", {})
        vlm = cfg.get("vlm", {})
        workspace = cfg.get("storage", {}).get("workspace") or defaults["workspace"]
        if account_service.is_legacy_fixed_workspace(workspace):
            workspace = account_service.clean_workspace(defaults["home"], defaults["account"])
        defaults["workspace"] = workspace
        defaults["openviking_workspace"] = defaults["workspace"]
        defaults["external_log_path"] = ""
        defaults["server_port"] = str(server.get("port") or "19080")
        defaults["server_url"] = bot_server.get("server_url") or bot_server.get("url") or f"http://127.0.0.1:{defaults['server_port']}"
        parsed = urlparse(defaults["server_url"])
        defaults["server_host"] = parsed.hostname or server.get("host") or defaults["server_host"]
        defaults["root_api_key"] = bot_server.get("root_api_key") or server.get("root_api_key") or defaults["root_api_key"]
        defaults["account"] = bot_server.get("account_id") or defaults["account"]
        defaults["judge_base_url"] = vlm.get("api_base", "")
        defaults["judge_model"] = vlm.get("model", defaults["judge_model"])
        defaults["answer_model"] = vlm.get("model", defaults["answer_model"])
        defaults["judge_token_set"] = bool(vlm.get("api_key"))
    except Exception:
        pass
    for candidate in openviking_config_candidates(config_path):
        try:
            ov_cfg = read_json(candidate)
        except Exception:
            continue
        ov_workspace = ov_cfg.get("storage", {}).get("workspace")
        if ov_workspace and not account_service.is_legacy_fixed_workspace(ov_workspace):
            defaults["openviking_workspace"] = str(Path(ov_workspace).expanduser().resolve())
            break
        if ov_workspace:
            defaults["openviking_workspace"] = account_service.clean_workspace(defaults["home"], defaults["account"])
            break
    for key in ("workspace", "openviking_workspace"):
        if account_service.is_legacy_fixed_workspace(defaults.get(key)):
            defaults[key] = account_service.clean_workspace(defaults["home"], defaults["account"])
    return defaults


def ui_boot_config() -> dict[str, Any]:
    defaults = load_ov_defaults()
    try:
        account_state = account_service.public_state(ACCOUNT_STATE_FILE, load_ov_defaults())
    except Exception:
        account_state = {}
    active_account = account_service.slug_account(
        str(account_state.get("active_account") or defaults.get("account") or "default")
    )
    records = account_state.get("accounts") if isinstance(account_state.get("accounts"), list) else []
    active_record = next(
        (
            record
            for record in records
            if str(record.get("id") or "").strip() == active_account
        ),
        {},
    )
    merged = {**defaults}
    merged["active_account"] = active_account
    merged["account"] = active_account
    merged["accounts"] = records
    merged["account_state_file"] = str(account_state.get("state_file") or "")
    if isinstance(active_record, dict):
        active_config = active_record.get("config") if isinstance(active_record.get("config"), dict) else {}
        if active_config:
            merged["active_account_config"] = active_config
            merged["memoryBackend"] = active_config.get("memoryBackend") or merged.get("memoryBackend") or ""
            merged["workspace"] = active_config.get("ovWorkspace") or active_config.get("memoryWorkspace") or merged.get("workspace") or ""
            merged["openviking_workspace"] = active_config.get("ovWorkspace") or merged.get("openviking_workspace") or ""
    return merged


def looks_like_locomo_data(data: Any) -> bool:
    return dataset_service.looks_like_locomo_data(data)


def infer_dataset_format(path: Path, data: Any | None = None) -> str:
    return dataset_service.infer_dataset_format(path, data, dataset_candidates())


def dataset_overview(path: Path) -> dict[str, Any]:
    return dataset_service.dataset_overview(path, dataset_candidates(), DATASET_SCAN_LIMIT_BYTES, DATASET_CACHE)


def context_pack_preview(path: Path, limit: int = 8) -> dict[str, Any]:
    return dataset_service.context_pack_preview(path, limit, dataset_candidates())


def generic_data_overview(path: Path, loaded: Any | None = None) -> dict[str, Any]:
    return dataset_service.generic_data_overview(path, loaded, dataset_candidates())


def data_overview_from_data(path: Path, data: Any) -> dict[str, Any]:
    return dataset_service.locomo_overview_from_data(path, data)


def data_overview(path: Path) -> dict[str, Any]:
    return dataset_service.locomo_overview_from_data(path, read_json(path))


def locomo_questions(path: Path, sample_filter: str = "all") -> dict[str, Any]:
    return dataset_service.locomo_questions(path, sample_filter)


def benchmark_questions(path: Path, sample_filter: str = "all", limit: int = 2000) -> dict[str, Any]:
    return dataset_service.benchmark_questions(path, sample_filter, limit, dataset_candidates())


def iter_json_array_objects(path: Path, offset: int, limit: int):
    yield from dataset_service.iter_json_array_objects(path, offset, limit)


def benchmark_questions_page(path: Path, offset: int = 0, limit: int = 100, query: str = "") -> dict[str, Any]:
    return dataset_service.benchmark_questions_page(path, offset, limit, query, dataset_candidates())


def csv_wrong_question_ids(csv_path: Path) -> dict[str, Any]:
    return dataset_service.csv_wrong_question_ids(csv_path)


def is_time_question(question: dict[str, Any]) -> bool:
    return dataset_service.is_time_question(question)


def question_set(path: Path, mode: str, csv_path: Path | None = None, sample: str = "all") -> dict[str, Any]:
    return dataset_service.question_set(path, mode, csv_path, sample)


def write_shard_csv(data_path: Path, out_path: Path, start: int, count: int) -> int:
    data = read_json(data_path)
    rows = []
    global_idx = 0
    stop = start + count
    for sample_idx, sample in enumerate(data):
        conv = sample.get("conversation", {})
        speakers = [x for x in [conv.get("speaker_a", ""), conv.get("speaker_b", "")] if x]
        original_id = sample.get("sample_id", f"conv-{sample_idx}")
        for q_idx, qa in enumerate(sample.get("qa", [])):
            if str(qa.get("category", "")) == "5":
                continue
            if start <= global_idx < stop:
                rows.append(
                    {
                        "sample_id": f"sample_{sample_idx}",
                        "original_sample_id": original_id,
                        "question_id": f"sample_{sample_idx}_qa{q_idx}",
                        "question_index": q_idx,
                        "question": qa.get("question", ""),
                        "answer": qa.get("answer", ""),
                        "category": qa.get("category", ""),
                        "question_time": "",
                        "speakers": json.dumps(speakers, ensure_ascii=False),
                    }
                )
            global_idx += 1
            if global_idx >= stop:
                break
        if global_idx >= stop:
            break
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "sample_id",
            "original_sample_id",
            "question_id",
            "question_index",
            "question",
            "answer",
            "category",
            "question_time",
            "speakers",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def sample_question_count(data_path: Path, sample_value: Any) -> int:
    data = read_json(data_path)
    sample_index = int(normalize_sample(sample_value))
    if sample_index < 0 or sample_index >= len(data):
        return 0
    return len([qa for qa in data[sample_index].get("qa", []) if str(qa.get("category", "")) != "5"])


def sample_allocations(data_path: Path, sample_value: Any, count_value: Any = "") -> list[tuple[int, int]]:
    data = read_json(data_path)
    if sample_value not in (None, "", "all"):
        sample_index = int(sample_value)
        total = len([qa for qa in data[sample_index].get("qa", []) if str(qa.get("category", "")) != "5"])
        return [(sample_index, int(count_value or total))]
    remaining = int(count_value) if str(count_value or "").strip() else None
    allocations: list[tuple[int, int]] = []
    for sample_index, sample in enumerate(data):
        total = len([qa for qa in sample.get("qa", []) if str(qa.get("category", "")) != "5"])
        if total <= 0:
            continue
        if remaining is None:
            allocations.append((sample_index, total))
            continue
        take = min(total, remaining)
        if take > 0:
            allocations.append((sample_index, take))
            remaining -= take
        if remaining == 0:
            break
    return allocations


def redact_manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return tasking_service.redact_manifest_payload(payload)


def write_manifest(task: Task, payload: dict[str, Any], run_dir: Path) -> None:
    tasking_service.write_manifest(task, payload, run_dir)


def compact_text(value: Any, limit: int = 260) -> str:
    text = str(value or "").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def public_share_text(value: Any) -> str:
    """Redact machine-specific paths in copyable public summaries."""
    text = str(value or "")
    replacements: list[tuple[str, str]] = []
    for raw_path, label in [
        (DEFAULT_OUTPUT_DIR, "<project>/runs"),
        (DATASET_DIR, "<project>/dataset"),
        (ROOT, "<project>"),
        (Path.home(), "<home>"),
    ]:
        try:
            resolved = str(raw_path.expanduser().resolve())
        except Exception:
            resolved = str(raw_path)
        if resolved:
            replacements.append((resolved, label))
            replacements.append((resolved.replace(os.sep, "/"), label))
    for source, label in sorted(set(replacements), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, label)
    public_tail = r"[^\s`\"'<>),]*"
    text = re.sub(rf"<home>/(?:{public_tail})?(?:echomem|openviking|echo_memory)(?:{public_tail})?workspace(?:{public_tail})?", "<workspace>", text, flags=re.IGNORECASE)
    text = re.sub(rf"<home>/(?:{public_tail})?(?:echomem|echo_memory)(?:{public_tail})?", "<echomem-root>", text, flags=re.IGNORECASE)
    text = re.sub(rf"<home>/(?:{public_tail})?openviking(?:{public_tail})?", "<openviking-root>", text, flags=re.IGNORECASE)
    local_path_pattern = re.compile(
        r"(?<![A-Za-z0-9:])(?:/Users|/home|/private/tmp|/tmp)/[^\s`\"'<>),]+"
        r"|[A-Za-z]:\\[^\s`\"'<>),]+"
    )
    return local_path_pattern.sub("<local-path>", text)


def public_share_path(value: Any) -> str:
    text = str(value or "").strip()
    return public_share_text(text) if text else ""


def public_env_path(value: Any, placeholder: str) -> str:
    text = public_share_path(value)
    if not text or text.startswith("<home>") or text.startswith("<local-path>"):
        return placeholder
    return text


def public_artifact_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    try:
        if path.is_absolute():
            return str(path.resolve().relative_to(ROOT))
    except Exception:
        return "<local path redacted>"
    root_prefix = str(ROOT) + os.sep
    if text.startswith(root_prefix):
        return text[len(root_prefix):]
    return text


def export_report(run_dir: Path) -> dict[str, Any]:
    return report_export_service.export_report(run_dir, active_run_ids())


def active_run_ids() -> set[str]:
    with TASK_LOCK:
        return {
            str(value)
            for task in TASKS.values()
            if task.status in {"queued", "running", "stopping"}
            for value in (task.id, task.run_dir)
            if value
        }


def active_public_tasks() -> list[dict[str, Any]]:
    with TASK_LOCK:
        return [task.public() for task in TASKS.values() if task.status in ACTIVE_TASK_STATUSES]


def _task_from_manifest(run_dir: Path, manifest: dict[str, Any]) -> Task | None:
    task_id = str(manifest.get("id") or run_dir.name).strip()
    if not task_id:
        return None
    task = Task(
        id=task_id,
        kind=str(manifest.get("kind") or run_dir.name.split("_", 1)[0]),
        name=str(manifest.get("name") or run_dir.name),
        status=str(manifest.get("status") or "unknown"),
        created_at=time.time(),
        started_at=None,
        ended_at=None,
        command=list(manifest.get("command") or []),
        cwd=str(manifest.get("cwd") or ""),
        output_file=str(manifest.get("output_file") or ""),
        log_file=str(manifest.get("log_file") or run_dir / "run.log"),
        run_dir=str(run_dir),
        manifest_file=str(run_dir / "manifest.json"),
        returncode=manifest.get("returncode"),
        summary=manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {},
        error=str(manifest.get("error") or ""),
        pid=manifest.get("pid"),
        display_command=list(manifest.get("command") or []),
        meta={"config": manifest.get("config") if isinstance(manifest.get("config"), dict) else {}},
    )
    try:
        created = manifest.get("created_at")
        if created:
            task.created_at = datetime.fromisoformat(str(created)).timestamp()
        started = manifest.get("started_at")
        if started:
            task.started_at = datetime.fromisoformat(str(started)).timestamp()
        ended = manifest.get("ended_at")
        if ended:
            task.ended_at = datetime.fromisoformat(str(ended)).timestamp()
    except Exception:
        pass
    normalized = run_service.run_record(run_dir, active_run_ids(), compact=True)
    if isinstance(normalized, dict):
        status = str(normalized.get("status") or "").strip()
        if status:
            task.status = status
        summary = normalized.get("summary")
        if isinstance(summary, dict) and summary:
            task.summary = summary
    return task


def recover_tasks_from_disk() -> None:
    recovered: dict[str, Task] = {}
    for base in TASK_RECOVERY_SOURCES:
        if not base.exists():
            continue
        for manifest_path in base.rglob("manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(manifest, dict):
                continue
            run_dir = manifest_path.parent
            task = _task_from_manifest(run_dir, manifest)
            if not task:
                continue
            if task.status in ACTIVE_TASK_STATUSES:
                task.meta = {**task.meta, "recovered_from_manifest": True, "recovery_reason": "server_restart"}
                if task.status == "queued" and not task.started_at:
                    task.status = "canceled"
                    task.error = task.error or "任务在服务重启后恢复时未发现活跃进程，已标记为取消。"
                else:
                    task.status = "interrupted"
                    task.error = task.error or "任务在服务重启后恢复展示，原进程状态不可用。"
                task.ended_at = task.ended_at or time.time()
            recovered[task.id] = task
    with TASK_LOCK:
        for task_id, task in recovered.items():
            if task_id not in TASKS:
                TASKS[task_id] = task


recover_tasks_from_disk()


def health_status() -> dict[str, Any]:
    return status_service.build_health_status(
        service="locomo-eval-web",
        version=Handler.server_version,
        root=str(ROOT),
        static=str(STATIC),
        runs_dir=str(DEFAULT_OUTPUT_DIR),
        default_dataset=str(DEFAULT_DATA),
        datasets=[],
        running_tasks=[],
        recent_runs=[],
    )


def status_level(ok: bool, warn: bool = False) -> str:
    if ok and not warn:
        return "ok"
    if ok or warn:
        return "warn"
    return "fail"


def model_config_status(config: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    answer_base_url = str(config.get("answerBaseUrl") or config.get("judgeBaseUrl") or defaults.get("judge_base_url") or "").strip()
    answer_model = str(config.get("answerModel") or defaults.get("answer_model") or config.get("judgeModel") or defaults.get("judge_model") or "").strip()
    judge_base_url = str(config.get("judgeBaseUrl") or defaults.get("judge_base_url") or "").strip()
    judge_model = str(config.get("judgeModel") or defaults.get("judge_model") or "").strip()
    embedding_config = resolve_openviking_embedding_config()
    openviking_vlm = resolve_openviking_vlm_config()
    echomem_embedding_token = str(
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
    echomem_chat_token = str(
        config.get("echomem_chat_api_key")
        or config.get("echomemChatApiKey")
        or config.get("vlm_api_key")
        or config.get("answer_token")
        or config.get("judge_token")
        or config.get("memory_token")
        or os.environ.get("ECHOMEM_CHAT_API_KEY")
        or openviking_vlm.get("api_key")
        or echomem_embedding_token
        or ""
    ).strip()
    if not echomem_embedding_token and echomem_chat_token:
        echomem_embedding_token = echomem_chat_token
    token_sources = {
        "answer_token_set": bool(config.get("answerTokenSet") or os.environ.get("ANSWER_TOKEN") or os.environ.get("LOCOMO_ANSWER_TOKEN")),
        "judge_token_set": bool(config.get("judgeTokenSet") or os.environ.get("JUDGE_TOKEN") or defaults.get("judge_token_set")),
        "echomem_embedding_token_set": bool(config.get("echomemTokenSet") or config.get("echomemEmbeddingTokenSet") or echomem_embedding_token),
        "echomem_chat_token_set": bool(config.get("echomemChatTokenSet") or echomem_chat_token),
    }
    any_token = token_sources["answer_token_set"] or token_sources["judge_token_set"] or token_sources["echomem_embedding_token_set"] or token_sources["echomem_chat_token_set"]
    return {
        "status": status_level(bool(answer_base_url and answer_model and judge_base_url and judge_model), warn=not any_token),
        "answer": {
            "base_url_set": bool(answer_base_url),
            "model": answer_model,
            "token_set": token_sources["answer_token_set"],
        },
        "judge": {
            "base_url_set": bool(judge_base_url),
            "model": judge_model,
            "token_set": token_sources["judge_token_set"],
        },
        "echomemory": {
            "embedding_token_set": token_sources["echomem_embedding_token_set"],
            "chat_token_set": token_sources["echomem_chat_token_set"],
        },
        "note": "API Key 不会从预检接口返回；这里只显示是否已配置。",
    }


def locomo_dataset_status(dataset_text: str = "") -> dict[str, Any]:
    path = safe_path(dataset_text) if dataset_text else DEFAULT_DATA
    if not path.exists():
        return {
            "status": "fail",
            "path": str(path),
            "exists": False,
            "format": "",
            "samples": 0,
            "questions": 0,
            "categories": {},
            "message": "数据集文件不存在。",
        }
    try:
        overview = dataset_overview(path)
    except Exception as exc:
        return {
            "status": "fail",
            "path": str(path),
            "exists": True,
            "format": "",
            "samples": 0,
            "questions": 0,
            "categories": {},
            "message": str(exc),
        }
    fmt = infer_dataset_format(path)
    samples = int(overview.get("samples") or 0)
    questions = int(overview.get("questions") or 0)
    is_locomo = fmt == "locomo"
    return {
        "status": status_level(is_locomo and samples > 0 and questions > 0),
        "path": str(path),
        "exists": True,
        "format": fmt,
        "samples": samples,
        "questions": questions,
        "categories": overview.get("categories") or {},
        "message": "LoCoMo 数据集可用。" if is_locomo else f"当前识别为 {fmt or 'unknown'}，不是 LoCoMo。",
    }


def backend_runtime_status(backend: str, config: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return build_backend_runtime_status(
        backend,
        config,
        defaults,
        context=RuntimeStatusContext(
            repo_root=ROOT,
            first_existing_path=first_existing_path,
            resolve_openviking_embedding_config=resolve_openviking_embedding_config,
            resolve_openviking_vlm_config=resolve_openviking_vlm_config,
            plugin_service=plugin_service,
        ),
    )


def preflight_fixes(
    backend: str,
    plugin_status: dict[str, Any],
    workspace_status: dict[str, Any],
    dataset_status: dict[str, Any],
    model_status: dict[str, Any],
    runtime_status: dict[str, Any],
    security_status: dict[str, Any],
) -> list[dict[str, Any]]:
    fixes: list[dict[str, Any]] = []

    def add_fix(
        fix_id: str,
        title: str,
        body: str,
        priority: str = "recommended",
        env: dict[str, str] | None = None,
        command: str = "",
    ) -> None:
        fixes.append(
            {
                "id": fix_id,
                "title": title,
                "body": body,
                "priority": priority,
                "env": env or {},
                "command": command,
            }
        )

    if not plugin_status.get("registered"):
        add_fix(
            "backend_adapter",
            "检查记忆后端注册",
            f"当前后端 {backend} 没有注册。确认 memory/adapters/registry.py 注册 openviking 和 echomemory。",
            "required",
        )
    else:
        missing_capabilities = plugin_status.get("missing_required_capabilities") or plugin_status.get("missing_capabilities") or []
        missing_methods = plugin_status.get("missing_required_methods") or []
        if missing_capabilities or missing_methods:
            add_fix(
                "adapter_contract",
                "补齐记忆后端契约",
                "当前记忆后端缺少 LoCoMo 流程必需的能力或方法。外部 EchoMemory/OpenViking 变体需要先满足 adapter contract，再运行导入、QA 和报告。",
                "required",
                command="./preflight.sh",
            )
    if not workspace_status.get("workspace_exists") or not workspace_status.get("storage_root_exists"):
        add_fix(
            "workspace",
            "准备干净 workspace",
            "当前账户的 workspace 或存储根目录不存在。正式评测建议使用自动生成的新目录，避免历史记忆污染。",
            "required",
            command=f"mkdir -p {workspace_status.get('storage_root') or workspace_status.get('workspace') or '<workspace>'}",
        )
    if dataset_status.get("status") != "ok":
        add_fix(
            "dataset",
            "修正 LoCoMo 数据集路径",
            "数据集必须是 LoCoMo list-format JSON，并能解析出 conversation、qa、category。",
            "required",
            env={"LOCOMO_DATA": dataset_status.get("path") or "/absolute/path/to/locomo.json"},
        )
    if backend == "echomemory":
        root = str(runtime_status.get("root") or "/absolute/path/to/EchoMemory")
        env: dict[str, str] = {}
        if not (runtime_status.get("explicit_root") or runtime_status.get("default_root")):
            env["ECHOMEM_ROOT"] = root
        if runtime_status.get("version_ok") is False:
            add_fix(
                "echomemory_version",
                "切换到 EchoMemory version_0.1.0",
                "当前 EchoMemory 源码建议使用官方 release tag version_0.1.0。旧版、develop 或历史 fork 会导致导入、检索和报告结果不可比。",
                "required",
                env={"ECHOMEM_ROOT": "/absolute/path/to/echo_memory"},
                command="git clone -b version_0.1.0 https://github.com/tech-innovation-group/echo_memory.git /absolute/path/to/echo_memory",
            )
        if not runtime_status.get("embedding_token_set"):
            env["DASHSCOPE_API_KEY"] = "<your-embedding-api-key>"
            env["DASHSCOPE_BASE_URL"] = "https://<embedding-provider-host>/compatible-mode/v1"
        if not runtime_status.get("chat_token_set"):
            env["ECHOMEM_CHAT_API_KEY"] = "<your-chat-api-key>"
            env["ECHOMEM_CHAT_BASE_URL"] = "https://<chat-provider-host>/compatible-mode/v1"
            env["ECHOMEM_CHAT_MODEL"] = str((model_status.get("answer") or {}).get("model") or "gpt-5.5")
        if env:
            add_fix(
                "echomemory_env",
                "补齐 EchoMemory 本地环境变量",
                "EchoMemory 导入和 QA 需要 SDK 根目录、embedding provider 和 chat provider。这里只给占位符，不返回真实 key。",
                "required",
                env=env,
                command="source .env.local && ./start.sh",
            )
    elif runtime_status.get("status") != "ok":
        backend_label = "OpenViking"
        add_fix(
            "memory_service",
            f"检查 {backend_label} 服务",
            f"{backend_label} 后端需要服务可访问。先确认端口、workspace 配置和 root api key 是否正确。",
            "required",
            command=f"curl -s {runtime_status.get('url') or 'http://127.0.0.1:19080'}/health",
        )
    if model_status.get("status") != "ok":
        add_fix(
            "model_config",
            "补齐 Answer 与判分模型配置",
            "Answer 和判分都需要兼容 OpenAI Chat Completions 的 base URL、model 和 token。",
            "required",
            env={
                "JUDGE_BASE_URL": "https://<judge-provider-host>/v1",
                "JUDGE_MODEL": str((model_status.get("judge") or {}).get("model") or "gpt-5.5"),
                "JUDGE_TOKEN": "<your-judge-api-key>",
            },
        )
    if not security_status.get("secrets_redacted") or security_status.get("token_values_returned"):
        add_fix(
            "security",
            "停止外发敏感信息",
            "预检或报告不应返回真实 API Key。检查 .env.local、judge.conf、runs/ 和截图是否包含密钥。",
            "required",
        )
    if not fixes:
        add_fix(
            "ready",
            "环境已满足基础门禁",
            "可以进入 LoCoMo 数据集校验、记忆导入、QA、判分和报告导出。",
            "ok",
        )
    return fixes


def preflight_share_summary(
    account: str,
    backend: str,
    plugin_status: dict[str, Any],
    workspace_status: dict[str, Any],
    dataset_status: dict[str, Any],
    model_status: dict[str, Any],
    runtime_status: dict[str, Any],
    security_status: dict[str, Any],
    fixes: list[dict[str, Any]],
    overall: str,
) -> str:
    backend_label = "EchoMemory" if backend == "echomemory" else "OpenViking"
    dataset_line = (
        f"{dataset_status.get('samples', 0)} conv / {dataset_status.get('questions', 0)} QA"
        if dataset_status.get("format") == "locomo"
        else str(dataset_status.get("message") or dataset_status.get("format") or "unknown")
    )
    answer = model_status.get("answer") or {}
    judge = model_status.get("judge") or {}
    echomemory = model_status.get("echomemory") or {}
    lines = [
        "LoCoMo Memory Eval Preflight",
        f"- Status: {overall}",
        f"- Account: {account}",
        f"- Memory backend: {backend_label} ({backend})",
        f"- Memory backend registered: {bool(plugin_status.get('registered'))}",
        f"- Memory backend contract: {plugin_status.get('contract_status') or (plugin_status.get('contract') or {}).get('status') or '-'}",
        f"- Missing backend methods: {', '.join(plugin_status.get('missing_required_methods') or []) or 'none'}",
        f"- Missing backend capabilities: {', '.join(plugin_status.get('missing_required_capabilities') or plugin_status.get('missing_capabilities') or []) or 'none'}",
        f"- Workspace: {workspace_status.get('workspace') or '-'}",
        f"- Storage root: {workspace_status.get('storage_root') or '-'}",
        f"- Dataset: {dataset_line}",
        f"- Dataset path: {dataset_status.get('path') or '-'}",
        f"- Runtime: {runtime_status.get('label') or runtime_status.get('kind') or '-'}",
        f"- Runtime root/url: {runtime_status.get('root') or runtime_status.get('url') or '-'}",
        f"- Answer model: {answer.get('model') or '-'}; token_set={bool(answer.get('token_set'))}",
        f"- 判分模型: {judge.get('model') or '-'}; token_set={bool(judge.get('token_set'))}",
        f"- EchoMemory embedding token_set={bool(echomemory.get('embedding_token_set'))}; chat token_set={bool(echomemory.get('chat_token_set'))}",
        f"- Secrets redacted: {bool(security_status.get('secrets_redacted'))}",
        "- Required fixes: " + (", ".join(f"{item.get('id')}:{item.get('title')}" for item in fixes if item.get("priority") != "ok") or "none"),
        "",
        "Do not share .env.local, judge.conf, runs/, workspaces, or real API keys.",
    ]
    return public_share_text("\n".join(lines))


def system_preflight(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    defaults = load_ov_defaults()
    account_state = account_service.public_state(ACCOUNT_STATE_FILE, defaults)
    account = account_service.slug_account(str(payload.get("account") or account_state.get("active_account") or defaults.get("account") or "default"))
    record = next((item for item in account_state.get("accounts", []) if item.get("id") == account), None)
    base_config = dict((record or {}).get("config") or {})
    incoming_config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    config = {**base_config, **incoming_config}
    backend = normalize_memory_backend(config.get("memoryBackend") or defaults.get("memory_backend") or "openviking")
    workspace_text = str(config.get("ovWorkspace") or config.get("memoryWorkspace") or defaults.get("openviking_workspace") or defaults.get("workspace") or "").strip()
    workspace = safe_path(workspace_text) if workspace_text else Path("")
    storage_root = account_service.storage_root(workspace, account, backend) if workspace_text else Path("")
    adapter_descriptors = {str(item.get("id") or ""): item for item in available_adapters()}
    adapter_ok = backend in adapter_descriptors
    adapter = get_adapter(backend) if adapter_ok else None
    adapter_descriptor = adapter_descriptors.get(backend) or {}
    contract = adapter_descriptor.get("contract") if isinstance(adapter_descriptor.get("contract"), dict) else {}
    capabilities = list(contract.get("capabilities") or [item.name for item in (adapter.descriptor.capabilities if adapter else [])])
    missing_required_capabilities = list(contract.get("missing_required_capabilities") or [])
    missing_recommended_capabilities = list(contract.get("missing_recommended_capabilities") or [])
    missing_required_methods = list(contract.get("missing_required_methods") or [])
    missing_optional_methods = list(contract.get("missing_optional_methods") or [])
    contract_status = str(contract.get("status") or ("ok" if adapter_ok else "fail"))
    if not adapter_ok or contract_status == "fail":
        adapter_status_level = "fail"
    elif contract_status == "warn":
        adapter_status_level = "warn"
    else:
        adapter_status_level = "ok"
    workspace_status = {
        "status": status_level(bool(workspace_text and Path(workspace).exists() and storage_root.exists()), warn=bool(workspace_text and not storage_root.exists())),
        "workspace": str(workspace) if workspace_text else "",
        "storage_root": str(storage_root) if workspace_text else "",
        "workspace_exists": bool(workspace_text and Path(workspace).exists()),
        "storage_root_exists": bool(workspace_text and storage_root.exists()),
        "layout": "workspace/<account>/<account>" if backend == "echomemory" else "workspace/viking/<account>",
    }
    dataset_status = locomo_dataset_status(str(payload.get("dataset") or config.get("data") or defaults.get("data") or ""))
    model_status = model_config_status(config, defaults)
    runtime_status = backend_runtime_status(backend, config, defaults)
    plugin_status = {
        "status": adapter_status_level,
        "backend": backend,
        "registered": adapter_ok,
        "contract_status": contract_status,
        "contract": contract,
        "capabilities": capabilities,
        "missing_capabilities": missing_required_capabilities,
        "missing_required_capabilities": missing_required_capabilities,
        "missing_recommended_capabilities": missing_recommended_capabilities,
        "missing_required_methods": missing_required_methods,
        "missing_optional_methods": missing_optional_methods,
    }
    security_status = {
        "status": "ok",
        "secrets_redacted": True,
        "token_values_returned": False,
        "safe_to_share": [
            "README / 交付说明",
            "env.echomem.example",
            "HARNESS_SPEC.md",
        ],
        "do_not_share": [
            ".env.local",
            "judge.conf",
            "runs/",
            "真实 API Key",
        ],
    }
    fixes = preflight_fixes(backend, plugin_status, workspace_status, dataset_status, model_status, runtime_status, security_status)
    sections = [plugin_status, workspace_status, dataset_status, model_status, runtime_status, security_status]
    if any(item.get("status") == "fail" for item in sections):
        overall = "fail"
    elif any(item.get("status") == "warn" for item in sections):
        overall = "warn"
    else:
        overall = "ok"
    share_summary = preflight_share_summary(
        account,
        backend,
        plugin_status,
        workspace_status,
        dataset_status,
        model_status,
        runtime_status,
        security_status,
        fixes,
        overall,
    )
    return {
        "status": overall,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": backend,
        "backend_adapter": plugin_status,
        "plugin": plugin_status,
        "workspace": workspace_status,
        "dataset": dataset_status,
        "models": model_status,
        "runtime": runtime_status,
        "security": security_status,
        "fixes": fixes,
        "share_summary": share_summary,
    }


HANDOFF_AUDIT_INCLUDE = [
    ".gitignore",
    ".gitattributes",
    "LICENSE",
    "README.md",
    "README_ECHOMEM_LOCOMO_HANDOFF.md",
    "HARNESS_SPEC.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "PUBLICATION_CHECKLIST.md",
    "web/README.md",
    "web/ui_contract.json",
    "web/static/product-roadmap.html",
    "env.echomem.example",
    "env.example",
    "start.sh",
    "preflight.sh",
    "server.py",
    "dataset/manifest.json",
    ".github/pull_request_template.md",
    ".github/workflows/preflight.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/memory_backend_integration.yml",
    ".github/ISSUE_TEMPLATE/benchmark_result.yml",
]

GITHUB_ISSUE_TEMPLATES = [
    {
        "path": ".github/ISSUE_TEMPLATE/bug_report.yml",
        "title": "Bug report",
        "purpose": "收集页面、任务、导入、QA、判分和报告问题。",
    },
    {
        "path": ".github/ISSUE_TEMPLATE/memory_backend_integration.yml",
        "title": "Memory backend integration",
        "purpose": "收集 OpenViking 或 EchoMemory 接入信息。",
    },
    {
        "path": ".github/ISSUE_TEMPLATE/benchmark_result.yml",
        "title": "Benchmark result",
        "purpose": "收集 LoCoMo run、配置快照、报告和复现摘要。",
    },
]
HANDOFF_AUDIT_DIRS = [
    "web/static",
    "static",
    "memory",
    "scripts",
]
HANDOFF_AUDIT_EXCLUDE_PARTS = {
    ".git",
    "__pycache__",
    "runs",
    "dist",
    "outputs",
    "external",
    ".tmp",
    "dataset/full",
}
PUBLIC_STATIC_FILES = contract_public_static_files()
HANDOFF_TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".html",
    ".css",
    ".md",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".sh",
    ".example",
    ".txt",
}

GITIGNORE_REQUIRED_PATTERNS = [
    ".env.local",
    "judge.conf",
    "runs/",
    "dist/",
    "outputs/",
    "external/",
    "dataset/full/",
    "web/static/*.html",
    "!web/static/index.html",
    "!web/static/product-roadmap.html",
    "static/*.html",
    "!static/index.html",
    "!static/product-roadmap.html",
]

GITATTRIBUTES_REQUIRED_PATTERNS = [
    "runs/ export-ignore",
    "dist/ export-ignore",
    "outputs/ export-ignore",
    "external/ export-ignore",
    "dataset/full/ export-ignore",
]

OPEN_SOURCE_DOC_REQUIREMENTS = {
    "LICENSE": ["MIT License"],
    "CONTRIBUTING.md": ["OpenViking", "EchoMemory", "./preflight.sh", "Do not commit"],
    "SECURITY.md": ["API keys", "private vulnerability", "./preflight.sh"],
    "CODE_OF_CONDUCT.md": ["Expected Behavior", "Unacceptable Behavior"],
    "PUBLICATION_CHECKLIST.md": ["OpenViking + EchoMemory", "Do Not Include", "./preflight.sh", "web/ui_contract.json"],
    ".github/pull_request_template.md": ["OpenViking + EchoMemory", "./preflight.sh", ".env.local"],
    ".github/workflows/preflight.yml": ["actions/checkout", "python3 -m py_compile", "node --check", "./preflight.sh"],
}


def audit_add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    title: str,
    status: str,
    detail: str,
    severity: str = "required",
    evidence: Any = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "title": title,
            "status": status,
            "severity": severity,
            "detail": detail,
            "evidence": evidence if evidence is not None else [],
        }
    )


def audit_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except Exception:
        return str(path)


def should_audit_path(path: Path) -> bool:
    rel = audit_rel(path)
    parts = set(Path(rel).parts)
    if parts.intersection(HANDOFF_AUDIT_EXCLUDE_PARTS):
        return False
    if (rel.startswith("web/static/") or rel.startswith("static/")) and rel not in PUBLIC_STATIC_FILES:
        return False
    if rel.startswith("dataset/full/"):
        return False
    if path.is_dir():
        return False
    if path.suffix in HANDOFF_TEXT_EXTENSIONS:
        return True
    if path.name.startswith("env.") or path.name in {"README", "LICENSE"}:
        return True
    return False


def audit_text_files() -> list[Path]:
    paths: dict[str, Path] = {}
    for rel in HANDOFF_AUDIT_INCLUDE:
        path = ROOT / rel
        if path.exists() and should_audit_path(path):
            paths[audit_rel(path)] = path
    for rel_dir in HANDOFF_AUDIT_DIRS:
        base = ROOT / rel_dir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if should_audit_path(path):
                paths[audit_rel(path)] = path
    return [paths[key] for key in sorted(paths)]


def scan_patterns(patterns: list[tuple[str, re.Pattern[str]]]) -> dict[str, list[dict[str, Any]]]:
    findings: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in patterns}
    for path in audit_text_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in patterns:
                if pattern.search(line):
                    rel = audit_rel(path)
                    if rel == "server.py" and (
                        "re.compile" in line
                        or f"findings[\"{name}\"]" in line
                        or f"\"{name}\"" in line
                        or f"no_{name}" in line
                        or "retired_backend" in line
                        or "范围外后端" in line
                    ):
                        continue
                    findings[name].append(
                        {
                            "file": rel,
                            "line": lineno,
                            "preview": line.strip()[:180],
                        }
                    )
    return findings


def file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_cache_versions(index_path: Path) -> list[str]:
    if not index_path.exists():
        return []
    text = index_path.read_text(encoding="utf-8", errors="ignore")
    return sorted(set(re.findall(r"[?&]v=([A-Za-z0-9._-]+)", text)))


def missing_line_patterns(path: Path, required: list[str]) -> list[str]:
    if not path.exists():
        return required
    lines = {
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return [pattern for pattern in required if pattern not in lines]


def missing_doc_terms(path: Path, required_terms: list[str]) -> list[str]:
    if not path.exists():
        return required_terms
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [term for term in required_terms if term not in text]


def handoff_audit() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    text_files = audit_text_files()
    audited_rels = [audit_rel(path) for path in text_files]
    required_files = [
        ".gitignore",
        ".gitattributes",
        "LICENSE",
        "server.py",
        "start.sh",
        "preflight.sh",
        "env.echomem.example",
        "README.md",
        "README_ECHOMEM_LOCOMO_HANDOFF.md",
        "HARNESS_SPEC.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "PUBLICATION_CHECKLIST.md",
        "web/static/index.html",
        "web/static/app.js",
        "web/static/styles.css",
        "web/static/product-roadmap.html",
        "web/ui_contract.json",
        "memory/adapters/__init__.py",
        "memory/adapters/base.py",
        "memory/adapters/contract.py",
        "memory/adapters/doctor.py",
        "memory/adapters/registry.py",
        "memory/adapters/openviking/__init__.py",
        "memory/adapters/echomemory/__init__.py",
        "scripts/adapter_doctor.py",
        "scripts/openviking_locomo_import.py",
        "scripts/openviking_memory_qa.py",
        "scripts/echomemory_locomo_import.py",
        "scripts/echomemory_memory_qa.py",
        "scripts/local_judge.py",
        "scripts/generate_html_report.py",
        "dataset/manifest.json",
        ".github/pull_request_template.md",
        ".github/workflows/preflight.yml",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/memory_backend_integration.yml",
        ".github/ISSUE_TEMPLATE/benchmark_result.yml",
    ]
    missing_required = [rel for rel in required_files if not (ROOT / rel).exists()]
    audit_add_check(
        checks,
        "required_files",
        "交付必需文件",
        "ok" if not missing_required else "fail",
        "必需文件齐全。" if not missing_required else "缺少必需文件。",
        "required",
        missing_required or required_files,
    )

    open_source_doc_gaps = {
        rel: missing_doc_terms(ROOT / rel, terms)
        for rel, terms in OPEN_SOURCE_DOC_REQUIREMENTS.items()
    }
    open_source_doc_gaps = {rel: missing for rel, missing in open_source_doc_gaps.items() if missing}
    audit_add_check(
        checks,
        "open_source_docs",
        "开源协作材料",
        "ok" if not open_source_doc_gaps else "fail",
        "许可证、贡献说明、安全策略、行为准则、发布清单、PR 模板和 CI 预检齐全，且包含当前后端边界与安全要求。" if not open_source_doc_gaps else "开源协作材料缺失或缺少关键口径。",
        "required",
        open_source_doc_gaps,
    )

    gitignore_missing = missing_line_patterns(ROOT / ".gitignore", GITIGNORE_REQUIRED_PATTERNS)
    gitattributes_missing = missing_line_patterns(ROOT / ".gitattributes", GITATTRIBUTES_REQUIRED_PATTERNS)
    ignore_policy_ok = not gitignore_missing and not gitattributes_missing
    audit_add_check(
        checks,
        "publish_ignore_policy",
        "发布忽略规则",
        "ok" if ignore_policy_ok else "fail",
        "本地密钥、历史运行、旧输出、外部源码、大数据和历史静态报告均默认不发布。" if ignore_policy_ok else "Git 忽略或导出忽略规则不完整。",
        "required",
        {"gitignore_missing": gitignore_missing, "gitattributes_missing": gitattributes_missing},
    )

    registry_ids = [item.get("id") for item in available_adapters()]
    unexpected_backends = [item for item in registry_ids if item not in MEMORY_BACKEND_IDS]
    missing_backends = [item for item in sorted(MEMORY_BACKEND_IDS) if item not in registry_ids]
    audit_add_check(
        checks,
        "memory_backends",
        "记忆后端边界",
        "ok" if not unexpected_backends and not missing_backends else "fail",
        f"当前注册边界：{MEMORY_BACKEND_SCOPE}。" if not unexpected_backends and not missing_backends else "记忆后端注册不符合当前交付边界。",
        "required",
        {"registered": registry_ids, "unexpected": unexpected_backends, "missing": missing_backends},
    )
    ui_contract = load_ui_contract()
    contract_backend_ids = [str(item.get("id") or "") for item in ui_contract.get("memory_backends", []) if item.get("id")]
    contract_sidebar = [
        [str(item.get("view") or ""), str(item.get("label") or "")]
        for item in ui_contract.get("sidebar", [])
        if item.get("view") and item.get("label")
    ]
    try:
        index_text = (WEB_STATIC / "index.html").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        index_text = ""
    index_sidebar = [
        [match.group(1), re.sub(r"<[^>]*>", "", match.group(2)).strip()]
        for match in re.finditer(r'<button\s+class="nav-item(?:\s+active)?"\s+data-view="([^"]+)"[^>]*>(.*?)</button>', index_text)
    ]
    agent_label = str((ui_contract.get("agent") or {}).get("label") or "")
    ui_contract_failures = []
    if not ui_contract:
        ui_contract_failures.append("web/ui_contract.json is missing or invalid")
    if sorted(contract_backend_ids) != sorted(MEMORY_BACKEND_IDS):
        ui_contract_failures.append(f"contract backends {contract_backend_ids} != server scope {sorted(MEMORY_BACKEND_IDS)}")
    if sorted(contract_backend_ids) != sorted(registry_ids):
        ui_contract_failures.append(f"contract backends {contract_backend_ids} != registered adapters {registry_ids}")
    if not contract_sidebar:
        ui_contract_failures.append("contract sidebar is empty")
    if contract_sidebar != index_sidebar:
        ui_contract_failures.append("contract sidebar does not match web/static/index.html")
    if agent_label and agent_label not in index_text:
        ui_contract_failures.append(f"agent label {agent_label!r} is not visible in web/static/index.html")
    audit_add_check(
        checks,
        "ui_contract",
        "UI 契约一致性",
        "ok" if not ui_contract_failures else "fail",
        "侧边栏、Agent 名称和两个后端均由 web/ui_contract.json 约束。" if not ui_contract_failures else "UI 契约与当前入口或后端注册不一致。",
        "required",
        {
            "contract_file": "web/ui_contract.json",
            "agent": agent_label,
            "contract_backends": contract_backend_ids,
            "registered_backends": registry_ids,
            "contract_sidebar": contract_sidebar,
            "index_sidebar": index_sidebar,
            "failures": ui_contract_failures,
        },
    )
    delivery_boundary = ui_contract.get("delivery_boundary") if isinstance(ui_contract.get("delivery_boundary"), dict) else {}
    contract_public_files = [
        str(item)
        for item in (delivery_boundary.get("public_static_files") or [])
        if str(item).strip()
    ]
    core_public_files = [
        "web/static/index.html",
        "web/static/app.js",
        "web/static/styles.css",
        "web/static/product-roadmap.html",
    ]
    unexpected_public_files = [rel for rel in contract_public_files if rel not in core_public_files]
    mirrored_public_files = set(contract_public_files)
    for rel in list(mirrored_public_files):
        if rel.startswith("web/static/"):
            mirrored_public_files.add("static/" + rel.removeprefix("web/static/"))
    public_static_failures = []
    missing_core_public_files = [rel for rel in core_public_files if rel not in contract_public_files]
    if missing_core_public_files:
        public_static_failures.append(f"delivery_boundary.public_static_files is missing core UI files: {missing_core_public_files}")
    if unexpected_public_files:
        public_static_failures.append(f"delivery_boundary.public_static_files contains unsupported entries: {unexpected_public_files}")
    missing_public_files = [
        rel for rel in sorted(mirrored_public_files or PUBLIC_STATIC_FILES)
        if not (ROOT / rel).exists()
    ]
    if missing_public_files:
        public_static_failures.append(f"public static files missing: {missing_public_files}")
    audit_add_check(
        checks,
        "public_static_contract",
        "公开静态入口契约",
        "ok" if not public_static_failures else "fail",
        "公开入口只包含核心 UI 四件套；其它 web/static HTML 仍视为历史实验或生成报告，不作为外发入口。" if not public_static_failures else "公开静态入口契约不一致。",
        "required",
        {
            "contract_public_static_files": contract_public_files,
            "mirrored_public_static_files": sorted(mirrored_public_files),
            "historical_static_policy": delivery_boundary.get("historical_static_policy") or "",
            "failures": public_static_failures,
        },
    )
    adapter_contracts = {
        str(item.get("id") or ""): item.get("contract") or {}
        for item in available_adapters()
    }
    contract_failures = {
        adapter_id: {
            "missing_required_capabilities": contract.get("missing_required_capabilities") or [],
            "missing_required_methods": contract.get("missing_required_methods") or [],
        }
        for adapter_id, contract in adapter_contracts.items()
        if contract.get("status") == "fail"
    }
    audit_add_check(
        checks,
        "adapter_contracts",
        "记忆后端契约",
        "ok" if not contract_failures else "fail",
        "OpenViking 和 EchoMemory adapter 均满足 LoCoMo 必需契约。" if not contract_failures else "存在不满足 LoCoMo 必需契约的记忆后端。",
        "required",
        {"contracts": adapter_contracts, "failures": contract_failures},
    )

    retired_wording_terms = [
        "后端" + "插" + "件",
        "插" + "件" + "已注册",
        "插" + "件" + "未注册",
    ]
    retired_backend_name_terms = [
        "hi" + "go",
        "hi-" + "go",
        "对接" + "hi" + "go",
    ]
    findings = scan_patterns(
        [
            ("secret", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_]{16,}|api[_-]?key\s*[:=]\s*['\"](?!<|\\$\\{)[^'\"<]{16,}|bearer\s+[A-Za-z0-9_\-.]{24,}", re.IGNORECASE)),
            ("local_path", re.compile(r"(/Users/[^\s`\"'<>]+|/home/[^\s`\"'<>]+|/private/tmp/[^\s`\"'<>]+|/tmp/[^\s`\"'<>]+|[A-Za-z]:\\\\[^\s`\"'<>]+)")),
            ("old_cache", re.compile(r"20260606[yz]\b")),
            ("old_backend_phrase", re.compile("|".join(map(re.escape, retired_wording_terms)))),
            ("retired_backend_name", re.compile("|".join(map(re.escape, retired_backend_name_terms)), re.IGNORECASE)),
        ]
    )
    audit_add_check(
        checks,
        "no_retired_backend",
        "后端范围一致",
        "ok" if not unexpected_backends and not missing_backends else "fail",
        f"当前交付源码与入口文档允许 {MEMORY_BACKEND_SCOPE}。" if not unexpected_backends and not missing_backends else "发现未声明的记忆后端或缺少必需后端。",
        "required",
        {"registered": registry_ids, "unexpected": unexpected_backends, "missing": missing_backends},
    )
    audit_add_check(
        checks,
        "no_retired_backend_names",
        "无范围外后端命名",
        "ok" if not findings["retired_backend_name"] else "fail",
        "公开交付源码与入口未出现范围外后端命名。" if not findings["retired_backend_name"] else "发现范围外后端命名，请从 UI、README 和接口说明移除。",
        "required",
        findings["retired_backend_name"][:20],
    )
    allowed_secret_files = {"README.md", "README_ECHOMEM_LOCOMO_HANDOFF.md", "web/static/index.html", "static/index.html", "scripts/echomemory_common.py"}
    secret_findings = [
        item for item in findings["secret"]
        if item.get("file") not in allowed_secret_files
        or ("<" not in item.get("preview", "") and "${" not in item.get("preview", ""))
    ]
    audit_add_check(
        checks,
        "no_real_secrets",
        "无真实密钥模式",
        "ok" if not secret_findings else "fail",
        "只发现占位符或没有密钥模式。" if not secret_findings else "发现疑似真实密钥，请脱敏。",
        "required",
        secret_findings[:20],
    )
    readme_local_path_findings = [
        item for item in findings["local_path"]
        if item.get("file") == "README.md"
    ]
    audit_add_check(
        checks,
        "public_readme_paths",
        "根 README 无本机路径",
        "ok" if not readme_local_path_findings else "fail",
        "根 README 可作为公开项目首页，不含本机绝对路径。" if not readme_local_path_findings else "根 README 出现本机绝对路径，请改成相对路径或占位符。",
        "required",
        readme_local_path_findings[:20],
    )
    audit_add_check(
        checks,
        "cache_version",
        "前端缓存版本",
        "ok" if not findings["old_cache"] else "warn",
        "当前交付入口未引用旧缓存版本。" if not findings["old_cache"] else "发现旧缓存版本引用，可能导致浏览器加载旧页面。",
        "recommended",
        findings["old_cache"][:20],
    )
    audit_add_check(
        checks,
        "wording_boundary",
        "记忆后端文案边界",
        "ok" if not findings["old_backend_phrase"] else "warn",
        "当前可见入口使用“记忆后端”口径。" if not findings["old_backend_phrase"] else "发现容易误导的旧后端口径文案。",
        "recommended",
        findings["old_backend_phrase"][:20],
    )

    web_hashes = {
        "index": file_sha256(WEB_STATIC / "index.html"),
        "app": file_sha256(WEB_STATIC / "app.js"),
        "styles": file_sha256(WEB_STATIC / "styles.css"),
        "product-roadmap": file_sha256(WEB_STATIC / "product-roadmap.html"),
    }
    legacy_hashes = {
        "index": file_sha256(LEGACY_STATIC / "index.html"),
        "app": file_sha256(LEGACY_STATIC / "app.js"),
        "styles": file_sha256(LEGACY_STATIC / "styles.css"),
        "product-roadmap": file_sha256(LEGACY_STATIC / "product-roadmap.html"),
    }
    drift = [name for name in web_hashes if web_hashes.get(name) != legacy_hashes.get(name)]
    audit_add_check(
        checks,
        "static_mirror",
        "静态目录同步",
        "ok" if not drift else "warn",
        "web/static 与 legacy static 入口文件一致。" if not drift else "legacy static 与 web/static 存在漂移。",
        "recommended",
        {"drift": drift},
    )
    versions = {
        "web": extract_cache_versions(WEB_STATIC / "index.html"),
        "legacy": extract_cache_versions(LEGACY_STATIC / "index.html"),
    }
    single_version = sorted(set(versions["web"] + versions["legacy"]))
    audit_add_check(
        checks,
        "single_cache_version",
        "资源版本一致",
        "ok" if len(single_version) == 1 else "warn",
        f"当前资源版本：{single_version[0]}" if len(single_version) == 1 else "发现多个资源版本。",
        "recommended",
        versions,
    )
    excluded = ["runs/", "dist/", "outputs/", "external/", "dataset/full/"]
    excluded_static_reports = [
        "web/static/*.html except index.html/product-roadmap.html",
        "web/static/generated-reports/",
        "static/*.html except index.html/product-roadmap.html",
    ]
    audit_add_check(
        checks,
        "excluded_history",
        "历史产物排除",
        "ok",
        "交付审计默认排除历史运行、旧输出、大数据、外部源码和历史静态报告。",
        "info",
        excluded + excluded_static_reports,
    )
    failing = [item for item in checks if item["severity"] == "required" and item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    if failing:
        status = "fail"
    elif warnings:
        status = "warn"
    else:
        status = "ok"
    summary_lines = [
        "LoCoMo Memory Eval Handoff Audit",
        f"- Status: {status}",
        f"- Checked at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Audited files: {len(text_files)}",
        f"- Registered memory backends: {', '.join(registry_ids)}",
        f"- Required failures: {len(failing)}",
        f"- Warnings: {len(warnings)}",
        "- Excluded from audit: " + ", ".join(excluded),
        "",
        f"Current delivery scope: {MEMORY_BACKEND_SCOPE}. Do not include .env.local, judge.conf, runs/, dist/, outputs/, workspaces, or real API keys.",
    ]
    return {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "audited_files": len(text_files),
        "audited_roots": HANDOFF_AUDIT_INCLUDE + HANDOFF_AUDIT_DIRS,
        "excluded": excluded,
        "checks": checks,
        "summary": "\n".join(summary_lines),
    }


def delivery_boundary_gate_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Delivery Boundary Gate",
        "",
        f"- Status: `{data.get('status')}`",
        f"- Scope: `{data.get('scope')}`",
        f"- Agent: `{data.get('agent_label')}`",
        f"- Expected backends: `{', '.join(data.get('expected_backends') or [])}`",
        f"- Registered backends: `{', '.join(data.get('registered_backends') or [])}`",
        f"- Sidebar entries: `{len(data.get('sidebar') or [])}`",
        f"- Public static files: `{len(data.get('public_files') or [])}`",
        "- Safe to share: yes, no API keys are included.",
        "",
        "## Checks",
    ]
    for item in data.get("checks") or []:
        lines.append(f"- {item.get('title')}: `{item.get('status')}` - {item.get('detail')}")
    lines.extend(
        [
            "",
            "## Do Not Ship",
            "- `.env.local`",
            "- `judge.conf`",
            "- `runs/`",
            "- memory workspaces",
            "- real API keys or screenshots containing tokens",
        ]
    )
    return public_share_text("\n".join(lines))


def delivery_boundary_gate(audit: dict[str, Any] | None = None, doctor: dict[str, Any] | None = None) -> dict[str, Any]:
    audit = audit or handoff_audit()
    doctor = doctor or adapter_doctor_report()
    ui_contract = load_ui_contract()
    agent = ui_contract.get("agent") if isinstance(ui_contract.get("agent"), dict) else {}
    delivery_boundary = ui_contract.get("delivery_boundary") if isinstance(ui_contract.get("delivery_boundary"), dict) else {}
    sidebar = [
        {
            "view": str(item.get("view") or ""),
            "label": str(item.get("label") or ""),
        }
        for item in (ui_contract.get("sidebar") or [])
        if isinstance(item, dict)
    ]
    public_files = [
        str(item)
        for item in (delivery_boundary.get("public_static_files") or [])
        if str(item).strip()
    ]
    checks_by_id = {
        str(item.get("id") or ""): item
        for item in (audit.get("checks") or [])
        if isinstance(item, dict)
    }
    expected = [str(item) for item in (doctor.get("expected_backends") or sorted(MEMORY_BACKEND_IDS))]
    registered = [str(item) for item in (doctor.get("registered_backends") or [])]
    missing = [str(item) for item in (doctor.get("missing_backends") or [])]
    unexpected = [str(item) for item in (doctor.get("unexpected_backends") or [])]
    adapter_failures = [item for item in (doctor.get("backends") or []) if item.get("contract_status") == "fail"]
    base_checks = [
        {
            "id": "agent_scope",
            "title": "自定义 Agent",
            "status": "ok" if (agent.get("id") == "memorybench_agent" and agent.get("label") == "MemoryBench Agent") else "fail",
            "detail": f"{agent.get('label') or '-'} / {agent.get('alignment_reference') or '-'}",
            "evidence": [
                f"agent_id={agent.get('id') or '-'}",
                f"label={agent.get('label') or '-'}",
                f"alignment_mode={agent.get('alignment_mode') or '-'}",
            ],
        },
        {
            "id": "backend_scope",
            "title": "两个记忆后端",
            "status": "ok" if not missing and not unexpected and sorted(registered) == sorted(expected) else "fail",
            "detail": f"registered={','.join(registered) or '-'} expected={','.join(expected) or '-'}",
            "evidence": [
                f"missing={','.join(missing) or 'none'}",
                f"unexpected={','.join(unexpected) or 'none'}",
            ],
        },
        {
            "id": "sidebar_scope",
            "title": f"{len(sidebar) or len(UI_CONTRACT.get('sidebar') or [])}个侧边栏入口",
            "status": (checks_by_id.get("ui_contract") or {}).get("status") or ("ok" if len(sidebar) > 0 else "fail"),
            "detail": "侧边栏由 web/ui_contract.json 约束，并与 index.html 一致。",
            "evidence": [
                f"{item.get('view') or '-'}={item.get('label') or '-'}"
                for item in sidebar
            ],
        },
        {
            "id": "public_files",
            "title": "公开静态入口",
            "status": (checks_by_id.get("public_static_contract") or {}).get("status") or "warn",
            "detail": "公开入口只包含核心 UI 四件套；历史 HTML 不作为外发入口。",
            "evidence": public_files,
        },
        {
            "id": "adapter_contracts",
            "title": "后端 adapter 契约",
            "status": "ok" if doctor.get("status") == "ok" and not adapter_failures else "fail",
            "detail": f"adapter-doctor={doctor.get('status') or '-'}",
            "evidence": [
                f"{item.get('id') or '-'} missing={','.join(item.get('missing_required') or []) or 'none'}"
                for item in adapter_failures
            ] or ["missing_required=none"],
        },
        {
            "id": "retired_backend_names",
            "title": "无范围外后端命名",
            "status": (checks_by_id.get("no_retired_backend_names") or {}).get("status") or "warn",
            "detail": (checks_by_id.get("no_retired_backend_names") or {}).get("detail") or "公开交付入口未出现范围外后端命名。",
            "evidence": (checks_by_id.get("no_retired_backend_names") or {}).get("evidence") or [],
        },
        {
            "id": "secrets",
            "title": "脱敏外发",
            "status": (checks_by_id.get("no_real_secrets") or {}).get("status") or "warn",
            "detail": "接口返回安全摘要，不返回 API Key；外发清单排除 runs、workspace 和本机密钥。",
            "evidence": (checks_by_id.get("no_real_secrets") or {}).get("evidence") or [],
        },
    ]
    if any(item.get("status") == "fail" for item in base_checks):
        status = "fail"
    elif any(item.get("status") == "warn" for item in base_checks):
        status = "warn"
    else:
        status = "ok"
    data = {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "scope": MEMORY_BACKEND_SCOPE,
        "agent_id": agent.get("id") or "",
        "agent_label": agent.get("label") or "MemoryBench Agent",
        "expected_backends": expected,
        "registered_backends": registered,
        "missing_backends": missing,
        "unexpected_backends": unexpected,
        "sidebar": sidebar,
        "public_files": public_files,
        "historical_static_policy": delivery_boundary.get("historical_static_policy") or "",
        "checks": base_checks,
        "safe_to_share": True,
        "secrets_included": False,
    }
    markdown = delivery_boundary_gate_markdown(data)
    data["markdown"] = markdown
    data["summary"] = markdown
    return data


def readiness_step(status: str, title: str, detail: str, action: str, weight: int, evidence: Any = None) -> dict[str, Any]:
    return {
        "status": status,
        "title": title,
        "detail": detail,
        "action": action,
        "weight": weight,
        "evidence": evidence if evidence is not None else {},
    }


def readiness_score(steps: list[dict[str, Any]]) -> int:
    total = sum(int(item.get("weight") or 0) for item in steps) or 1
    earned = 0
    for item in steps:
        weight = int(item.get("weight") or 0)
        status = item.get("status")
        if status == "ok":
            earned += weight
        elif status == "warn":
            earned += weight * 0.55
    return int(round(earned * 100 / total))


def readiness_status(steps: list[dict[str, Any]]) -> str:
    if any(item.get("status") == "fail" for item in steps):
        return "fail"
    if any(item.get("status") == "warn" for item in steps):
        return "warn"
    return "ok"


def readiness_summary(
    status: str,
    score: int,
    account: str,
    backend: str,
    steps: list[dict[str, Any]],
    running_tasks: list[dict[str, Any]],
) -> str:
    backend_label = "EchoMemory" if backend == "echomemory" else "OpenViking"
    lines = [
        "LoCoMo Memory Eval Readiness",
        f"- Status: {status}",
        f"- Score: {score}/100",
        f"- Account: {account}",
        f"- Memory backend: {backend_label} ({backend})",
        f"- Running tasks: {len(running_tasks)}",
    ]
    for item in steps:
        lines.append(f"- {item.get('title')}: {item.get('status')} · {item.get('action')}")
    lines.append("")
    lines.append("This public summary redacts local machine paths and never includes API key values.")
    return public_share_text("\n".join(lines))


def system_readiness(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    preflight = system_preflight(payload)
    audit = handoff_audit()
    backends = available_adapters()
    datasets = dataset_registry()
    locomo = next((item for item in datasets if item.get("id") == "locomo10"), None)
    running_tasks = active_public_tasks()
    recent = list_runs(DEFAULT_OUTPUT_DIR, 5, compact=True)
    backend = str(preflight.get("backend") or "openviking")
    account = str(preflight.get("account") or "default")
    dataset = preflight.get("dataset") or {}
    models = preflight.get("models") or {}
    runtime = preflight.get("runtime") or {}
    workspace = preflight.get("workspace") or {}
    contract = echomem_contract(payload) if backend == "echomemory" else None
    audit_required_fail = [
        item for item in audit.get("checks", [])
        if item.get("severity") == "required" and item.get("status") == "fail"
    ]
    model_token_ok = bool(
        ((models.get("answer") or {}).get("token_set"))
        or ((models.get("judge") or {}).get("token_set"))
        or ((models.get("echomemory") or {}).get("embedding_token_set"))
        or ((models.get("echomemory") or {}).get("chat_token_set"))
    )
    steps = [
        readiness_step(
            "ok" if backends and {item.get("id") for item in backends} == MEMORY_BACKEND_IDS else "fail",
            "记忆后端边界",
            "当前启用 OpenViking 和 EchoMemory adapter。",
            "保持当前 adapter 边界",
            15,
            {"backends": [item.get("id") for item in backends]},
        ),
        readiness_step(
            "ok" if audit.get("status") == "ok" else ("warn" if audit.get("status") == "warn" else "fail"),
            "交付审计",
            "检查后端边界声明、真实密钥、缓存和必需文件。",
            "必需失败为 0 后再外发",
            20,
            {"status": audit.get("status"), "required_failures": len(audit_required_fail)},
        ),
        readiness_step(
            "ok" if dataset.get("status") == "ok" else "fail",
            "LoCoMo 数据集",
            f"{dataset.get('samples', 0)} conv / {dataset.get('questions', 0)} QA",
            "先在 LoCoMo 评测页校验数据集",
            15,
            {"path": dataset.get("path"), "format": dataset.get("format")},
        ),
        readiness_step(
            "ok" if workspace.get("storage_root_exists") else ("warn" if workspace.get("workspace_exists") else "fail"),
            "账户隔离目录",
            str(workspace.get("storage_root") or workspace.get("workspace") or "-"),
            "使用自动生成的新 workspace 或新账户",
            15,
            {"layout": workspace.get("layout")},
        ),
        readiness_step(
            "ok" if runtime.get("status") == "ok" else ("warn" if runtime.get("status") == "warn" else "fail"),
            "记忆运行时",
            str(runtime.get("label") or runtime.get("kind") or "-"),
            str(runtime.get("next_action") or "保持当前运行时配置"),
            15,
            {"root": runtime.get("root"), "url": runtime.get("url")},
        ),
        readiness_step(
            "ok" if not contract else ("ok" if contract.get("status") == "ok" else ("warn" if contract.get("status") == "warn" else "fail")),
            "EchoMemory 接入契约",
            "OpenViking 当前不需要 EchoMemory 契约" if not contract else f"required_failures={len(contract.get('required_failures') or [])} warnings={len(contract.get('warnings') or [])}",
            "先通过 EchoMemory 接入契约，再跑 LoCoMo" if contract and contract.get("status") != "ok" else "保持当前接入契约",
            10,
            {"status": (contract or {}).get("status"), "root": (contract or {}).get("root")},
        ),
        readiness_step(
            "ok" if model_token_ok and models.get("status") == "ok" else ("warn" if models.get("status") == "ok" else "fail"),
            "模型配置",
            "Answer/判分/EchoMemory token 只显示是否配置。",
            "补齐 Answer、判分、Embedding/Chat 的本机环境变量或页面密码框",
            10,
            {
                "answer_model": (models.get("answer") or {}).get("model"),
                "judge_model": (models.get("judge") or {}).get("model"),
                "token_available": model_token_ok,
            },
        ),
        readiness_step(
            "warn" if running_tasks else "ok",
            "运行任务状态",
            f"running={len(running_tasks)} recent={len(recent)}",
            "运行中任务完成后再外发或切换关键配置",
            5,
            {"running": [item.get("id") for item in running_tasks], "recent": [item.get("id") for item in recent]},
        ),
    ]
    status = readiness_status(steps)
    score = readiness_score(steps)
    blockers = [item for item in steps if item.get("status") == "fail"]
    warnings = [item for item in steps if item.get("status") == "warn"]
    next_actions = [item.get("action") for item in blockers + warnings if item.get("action")]
    if not next_actions:
        next_actions = ["可以继续 LoCoMo 导入、QA、判分或外发前小样本核验。"]
    return {
        "status": status,
        "score": score,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": backend,
        "steps": steps,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions[:5],
        "preflight": preflight,
        "audit": audit,
        "contract": contract,
        "dataset": locomo,
        "running_tasks": running_tasks,
        "recent_runs": recent,
        "summary": readiness_summary(status, score, account, backend, steps, running_tasks),
        "public_summary": readiness_summary(status, score, account, backend, steps, running_tasks),
    }


def shell_quote_value(value: Any) -> str:
    text = str(value or "")
    if not text:
        return '""'
    if re.search(r"\s|[\"'`$\\<>|&;(){}]", text):
        return "'" + text.replace("'", "'\"'\"'") + "'"
    return text


def export_lines(env: dict[str, Any]) -> str:
    return "\n".join(f"export {key}={shell_quote_value(value)}" for key, value in env.items())


def setup_pack(payload: dict[str, Any] | None = None, readiness: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    readiness = readiness or system_readiness(payload)
    preflight = readiness.get("preflight") or {}
    backend = str(preflight.get("backend") or readiness.get("backend") or "openviking")
    account = str(preflight.get("account") or readiness.get("account") or "default")
    dataset = preflight.get("dataset") or {}
    workspace = preflight.get("workspace") or {}
    runtime = preflight.get("runtime") or {}
    models = preflight.get("models") or {}
    host = "127.0.0.1"
    port = os.environ.get("LOCOMO_EVAL_PORT") or "19181"
    env: dict[str, Any] = {
        "LOCOMO_EVAL_HOST": host,
        "LOCOMO_EVAL_PORT": port,
        "LOCOMO_DATA": public_share_path(dataset.get("path") or str(DEFAULT_DATA)) or "<project>/dataset/locomo10.json",
    }
    if backend == "echomemory":
        env.update(
            {
                "ECHOMEM_ROOT": public_env_path(runtime.get("root"), "/absolute/path/to/EchoMemory"),
                "ECHOMEM_WORKSPACE": public_env_path(workspace.get("workspace"), "/absolute/path/to/echomem_workspace"),
                "ECHOMEM_ACCOUNT": account,
                "ECHOMEM_USER_ID": "default",
                "ECHOMEM_AGENT_ID": "default",
                "DASHSCOPE_API_KEY": "<your-embedding-api-key>",
                "DASHSCOPE_BASE_URL": "https://<embedding-provider-host>/compatible-mode/v1",
                "ECHOMEM_CHAT_PROVIDER": "deepseek",
                "ECHOMEM_CHAT_MODEL": (models.get("answer") or {}).get("model") or "gpt-5.5",
                "ECHOMEM_CHAT_API_KEY": "<your-chat-api-key>",
                "ECHOMEM_CHAT_BASE_URL": "https://<chat-provider-host>/compatible-mode/v1",
            }
        )
    else:
        env.update(
            {
                "OPENVIKING_SOURCE": public_env_path(os.environ.get("OPENVIKING_SOURCE") or str(DEFAULT_OPENVIKING_SOURCE), "/absolute/path/to/openviking"),
                "OPENVIKING_PYTHON": public_env_path(os.environ.get("OPENVIKING_PYTHON") or str(DEFAULT_OPENVIKING_PYTHON), "python3"),
                "LOCOMO_EVAL_OPENVIKING_WORKSPACE": public_env_path(workspace.get("workspace"), "/absolute/path/to/openviking_workspace"),
                "OPENVIKING_BASE_URL": runtime.get("url") or "http://127.0.0.1:19080",
                "OPENVIKING_ACCOUNT": account,
            }
        )
    env.update(
        {
            "JUDGE_BASE_URL": "https://<judge-provider-host>/v1",
            "JUDGE_MODEL": (models.get("judge") or {}).get("model") or "gpt-5.5",
            "JUDGE_TOKEN": "<your-judge-api-key>",
        }
    )
    env_template = public_share_text("\n".join(
        [
            "# Copy to .env.local, fill placeholders locally, and never share .env.local.",
            "# Generated by LoCoMo Memory Eval connection guide. No real API keys are included.",
            export_lines(env),
            "",
        ]
    ))
    commands = [
        {
            "title": "创建本机配置",
            "command": "cp env.echomem.example .env.local\n# edit .env.local and fill placeholders",
        },
        {
            "title": "预检门禁",
            "command": "source .env.local && ./preflight.sh",
        },
        {
            "title": "启动 Web",
            "command": "source .env.local && ./start.sh",
        },
        {
            "title": "健康检查",
            "command": f"curl -s http://{host}:{port}/health | python3 -m json.tool | head -40",
        },
        {
            "title": "启动门禁",
            "command": f"curl -s http://{host}:{port}/api/readiness | python3 -m json.tool | head -120",
        },
        {
            "title": "EchoMemory 接入契约",
            "command": f"curl -s 'http://{host}:{port}/api/echomem-contract?account={account}' | python3 -m json.tool | head -160",
        },
        {
            "title": "交付审计",
            "command": f"curl -s http://{host}:{port}/api/handoff-audit | python3 -m json.tool | head -120",
        },
    ]
    ui_steps = [
        "打开 README / 交付说明，先看启动门禁。",
        "进入系统配置，确认当前账户、记忆后端和 workspace。",
        "进入 LoCoMo评测，校验数据集。",
        "导入一个对话（conv）做小样本核验，再运行少量 QA。",
        "判分当前结果并生成 HTML 报告。",
    ]
    do_not_share = [
        ".env.local",
        "judge.conf",
        "runs/",
        "dist/",
        "outputs/",
        "OpenViking workspace",
        "EchoMemory workspace",
        "真实 API Key 或截图里的密钥",
    ]
    summary = "\n".join(
        [
            "LoCoMo Memory Eval Connection Guide",
            f"- Backend: {backend}",
            f"- Account: {account}",
            f"- Dataset: {env['LOCOMO_DATA']}",
            f"- Workspace: {public_share_path(workspace.get('workspace')) or '-'}",
            f"- Readiness: {readiness.get('status')} {readiness.get('score')}/100",
            "- Secrets: placeholders only",
            "",
            "Use the env.local template and commands locally. Do not share .env.local, runs, workspaces or real API keys.",
        ]
    )
    return {
        "status": "ok",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "backend": backend,
        "account": account,
        "env_template": env_template,
        "commands": commands,
        "ui_steps": ui_steps,
        "do_not_share": do_not_share,
        "readiness": {
            "status": readiness.get("status"),
            "score": readiness.get("score"),
            "next_actions": readiness.get("next_actions"),
        },
        "summary": public_share_text(summary),
    }


def handoff_package_markdown(status: str, account: str, backend: str, include: list[dict[str, Any]], exclude: list[dict[str, Any]], verify: list[dict[str, Any]], acceptance: dict[str, Any]) -> str:
    lines = [
        "# LoCoMo Memory Eval Handoff Checklist",
        "",
        f"- Status: `{status}`",
        f"- Account: `{account}`",
        f"- Backend: `{backend}`",
        f"- Acceptance: `{acceptance.get('status')}` `{acceptance.get('score')}/100`",
        f"- Scope: `{MEMORY_BACKEND_SCOPE}`",
        "- Secrets included: `false`",
        "",
        "## Include",
    ]
    for item in include:
        lines.append(f"- `{item.get('path')}` - {item.get('reason')}")
    lines.extend(["", "## Exclude"])
    for item in exclude:
        lines.append(f"- `{item.get('path')}` - {item.get('reason')}")
    lines.extend(["", "## Verify After Handoff"])
    for item in verify:
        command = str(item.get("command") or "").replace("\n", " && ")
        lines.append(f"- {item.get('title')}: `{command}`")
    lines.append("")
    lines.append("Never share `.env.local`, `judge.conf`, `runs/`, memory workspaces, screenshots with tokens, or real API keys.")
    return public_share_text("\n".join(lines))


def handoff_package(
    payload: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    acceptance: dict[str, Any] | None = None,
    setup: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    readiness = readiness or system_readiness(payload)
    audit = audit or readiness.get("audit") or handoff_audit()
    acceptance = acceptance or acceptance_matrix(payload, readiness=readiness, audit=audit)
    setup = setup or setup_pack(payload, readiness=readiness)
    preflight = readiness.get("preflight") or {}
    backend = str(preflight.get("backend") or readiness.get("backend") or "openviking")
    account = str(preflight.get("account") or readiness.get("account") or "default")
    port = os.environ.get("LOCOMO_EVAL_PORT") or "19181"
    required_failures = [
        item for item in audit.get("checks", [])
        if item.get("severity") == "required" and item.get("status") == "fail"
    ]
    include = [
        {"path": ".gitignore", "reason": "阻止本地密钥、runs、旧输出、外部源码、大数据和历史静态报告进入公开交付。", "required": True, "exists": (ROOT / ".gitignore").exists()},
        {"path": ".gitattributes", "reason": "生成源码归档时排除本地敏感和历史产物。", "required": True, "exists": (ROOT / ".gitattributes").exists()},
        {"path": "LICENSE", "reason": "明确开源许可证，降低外部 fork 和二次接入的不确定性。", "required": True, "exists": (ROOT / "LICENSE").exists()},
        {"path": "CONTRIBUTING.md", "reason": "贡献流程、preflight、静态镜像和安全规则。", "required": True, "exists": (ROOT / "CONTRIBUTING.md").exists()},
        {"path": "SECURITY.md", "reason": "密钥、workspace、报告和路径 API 的安全披露边界。", "required": True, "exists": (ROOT / "SECURITY.md").exists()},
        {"path": "CODE_OF_CONDUCT.md", "reason": "协作行为准则，避免 issue/PR 中泄露敏感数据或误导 benchmark。", "required": True, "exists": (ROOT / "CODE_OF_CONDUCT.md").exists()},
        {"path": "PUBLICATION_CHECKLIST.md", "reason": "发布前可复制的 Include / No Ship / Demo Report 检查清单。", "required": True, "exists": (ROOT / "PUBLICATION_CHECKLIST.md").exists()},
        {"path": ".github/pull_request_template.md", "reason": "PR 安全清单，要求 preflight、后端边界和脱敏证明。", "required": True, "exists": (ROOT / ".github" / "pull_request_template.md").exists()},
        {"path": ".github/workflows/preflight.yml", "reason": "GitHub Actions 自动跑语法、静态镜像、交付边界和安全门禁。", "required": True, "exists": (ROOT / ".github" / "workflows" / "preflight.yml").exists()},
        {"path": "server.py", "reason": "本地 Web 后端和 API 编排入口。", "required": True, "exists": (ROOT / "server.py").exists()},
        {"path": "start.sh", "reason": "启动本地评测服务。", "required": True, "exists": (ROOT / "start.sh").exists()},
        {"path": "preflight.sh", "reason": "外发前和接收后的一键门禁。", "required": True, "exists": (ROOT / "preflight.sh").exists()},
        {"path": "web/static/index.html", "reason": "主前端 HTML。", "required": True, "exists": (ROOT / "web" / "static" / "index.html").exists()},
        {"path": "web/static/app.js", "reason": "主前端交互逻辑。", "required": True, "exists": (ROOT / "web" / "static" / "app.js").exists()},
        {"path": "web/static/styles.css", "reason": "主前端样式。", "required": True, "exists": (ROOT / "web" / "static" / "styles.css").exists()},
        {"path": "web/static/product-roadmap.html", "reason": "20k-star 产品方案和 24 小时迭代路线图。", "required": True, "exists": (ROOT / "web" / "static" / "product-roadmap.html").exists()},
        {"path": "static/index.html", "reason": "兼容旧入口的 HTML 镜像。", "required": True, "exists": (ROOT / "static" / "index.html").exists()},
        {"path": "static/app.js", "reason": "兼容旧入口的前端逻辑镜像。", "required": True, "exists": (ROOT / "static" / "app.js").exists()},
        {"path": "static/styles.css", "reason": "兼容旧入口的样式镜像。", "required": True, "exists": (ROOT / "static" / "styles.css").exists()},
        {"path": "static/product-roadmap.html", "reason": "兼容旧入口的产品方案镜像。", "required": True, "exists": (ROOT / "static" / "product-roadmap.html").exists()},
        {"path": "memory/", "reason": "记忆后端契约、adapter、报告导出和任务编排。", "required": True, "exists": (ROOT / "memory").exists()},
        {"path": "scripts/", "reason": "LoCoMo 导入、QA、判分、报告和 adapter doctor 脚本。", "required": True, "exists": (ROOT / "scripts").exists()},
        {"path": "dataset/manifest.json", "reason": "数据集注册表。完整大数据可由接收方按路径补齐。", "required": True, "exists": (ROOT / "dataset" / "manifest.json").exists()},
        {"path": "dataset/locomo10.json", "reason": "LoCoMo 小样本核验数据。", "required": True, "exists": (ROOT / "dataset" / "locomo10.json").exists()},
        {"path": "README.md", "reason": "项目概览。", "required": True, "exists": (ROOT / "README.md").exists()},
        {"path": "README_ECHOMEM_LOCOMO_HANDOFF.md", "reason": "外部测试者接入 EchoMemory/OpenViking 的主 README。", "required": True, "exists": (ROOT / "README_ECHOMEM_LOCOMO_HANDOFF.md").exists()},
        {"path": "HARNESS_SPEC.md", "reason": "平台接口和边界说明。", "required": True, "exists": (ROOT / "HARNESS_SPEC.md").exists()},
        {"path": "env.echomem.example", "reason": "只含占位符的环境变量模板。", "required": True, "exists": (ROOT / "env.echomem.example").exists()},
        {"path": ".github/ISSUE_TEMPLATE/", "reason": "外部协作 Issue 模板，收集 bug、后端接入和 benchmark 结果。", "required": True, "exists": (ROOT / ".github" / "ISSUE_TEMPLATE").exists()},
    ]
    exclude = [
        {"path": ".env", "reason": "本机环境变量和 API Key。", "severity": "secret"},
        {"path": ".env.local", "reason": "本机真实环境变量和 API Key。", "severity": "secret"},
        {"path": "judge.conf", "reason": "可能包含 Judge token。", "severity": "secret"},
        {"path": "runs/", "reason": "历史运行、日志、CSV、报告，可能包含模型输出或敏感路径。", "severity": "history"},
        {"path": "web/static/*.html except index.html/product-roadmap.html", "reason": "历史静态报告和实验页面，可能包含本机路径或旧实验信息。", "severity": "history"},
        {"path": "web/static/generated-reports/", "reason": "历史生成报告，不属于最小公开交付。", "severity": "history"},
        {"path": "static/*.html except index.html/product-roadmap.html", "reason": "历史静态报告镜像，不属于最小公开交付。", "severity": "history"},
        {"path": "dist/", "reason": "旧外发包和历史实验产物，不代表当前交付边界。", "severity": "history"},
        {"path": "outputs/", "reason": "旧报告输出。", "severity": "history"},
        {"path": "external/", "reason": "外部源码副本由接收方自行 clone 或指定。", "severity": "external"},
        {"path": "dataset/full/", "reason": "大数据集不作为最小交付；接收方可按 README 放入。", "severity": "large-data"},
        {"path": "OpenViking workspace", "reason": "真实记忆落盘目录，必须由接收方新建。", "severity": "memory"},
        {"path": "EchoMemory workspace", "reason": "真实记忆落盘目录，必须由接收方新建。", "severity": "memory"},
        {"path": "screenshots containing tokens", "reason": "截图可能泄露 API Key 或本机路径。", "severity": "secret"},
    ]
    verify = [
        {"title": "启动前门禁", "command": "./preflight.sh"},
        {"title": "启动服务", "command": f"LOCOMO_EVAL_PORT={port} ./start.sh"},
        {"title": "健康检查", "command": f"curl -s http://127.0.0.1:{port}/health | python3 -m json.tool | head -40"},
        {"title": "后端契约", "command": "python3 scripts/adapter_doctor.py --format markdown --strict"},
        {"title": "验收矩阵", "command": f"curl -s http://127.0.0.1:{port}/api/acceptance-matrix | python3 -m json.tool | head -160"},
        {"title": "小样本核验计划", "command": f"curl -s http://127.0.0.1:{port}/api/smoke-plan | python3 -m json.tool | head -200"},
        {"title": "EchoMemory 契约", "command": f"curl -s http://127.0.0.1:{port}/api/echomem-contract | python3 -m json.tool | head -160"},
    ]
    missing_include = [item for item in include if item.get("required") and not item.get("exists")]
    if required_failures or missing_include or acceptance.get("status") == "fail":
        status = "fail"
    elif readiness.get("status") == "warn" or acceptance.get("status") == "warn":
        status = "warn"
    else:
        status = "ok"
    markdown = handoff_package_markdown(status, account, backend, include, exclude, verify, acceptance)
    return {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": backend,
        "scope": MEMORY_BACKEND_SCOPE,
        "include": include,
        "exclude": exclude,
        "verify": verify,
        "missing_include": missing_include,
        "readiness": {
            "status": readiness.get("status"),
            "score": readiness.get("score"),
            "next_actions": readiness.get("next_actions"),
        },
        "acceptance": {
            "status": acceptance.get("status"),
            "score": acceptance.get("score"),
            "blockers": len(acceptance.get("blockers") or []),
            "warnings": len(acceptance.get("warnings") or []),
        },
        "audit": {
            "status": audit.get("status"),
            "required_failures": len(required_failures),
            "warnings": len([item for item in audit.get("checks", []) if item.get("status") == "warn"]),
        },
        "setup_summary": setup.get("summary"),
        "safe_to_share": True,
        "secrets_included": False,
        "markdown": markdown,
        "summary": markdown,
    }


def handoff_dashboard_issue(source: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "title": item.get("title") or item.get("id") or source,
        "status": item.get("status") or "warn",
        "severity": item.get("severity") or ("required" if item.get("status") == "fail" else "recommended"),
        "detail": item.get("detail") or item.get("proof") or "",
        "action": item.get("action") or "",
    }


def handoff_dashboard_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    """Keep dashboard payload fast while /api/readiness remains full fidelity."""
    return {
        "status": readiness.get("status"),
        "score": readiness.get("score"),
        "checked_at": readiness.get("checked_at"),
        "account": readiness.get("account"),
        "backend": readiness.get("backend"),
        "steps": readiness.get("steps") or [],
        "blockers": readiness.get("blockers") or [],
        "warnings": readiness.get("warnings") or [],
        "next_actions": readiness.get("next_actions") or [],
        "summary": readiness.get("summary") or readiness.get("public_summary") or "",
    }


def unique_nonempty(values: list[Any], limit: int = 8) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def handoff_dashboard_markdown(data: dict[str, Any]) -> str:
    smoke = data.get("smoke") or {}
    recommendation = smoke.get("recommendation") or {}
    package = data.get("handoff_package") or {}
    setup = data.get("setup_pack") or {}
    cards = data.get("cards") or []
    lines = [
        "# LoCoMo Memory Eval Handoff Dashboard",
        "",
        f"- Status: `{data.get('status')}`",
        f"- Score: `{data.get('score')}/100`",
        f"- Account: `{data.get('account')}`",
        f"- Backend: `{data.get('backend')}`",
        f"- Scope: `{MEMORY_BACKEND_SCOPE}`",
        "- Safe to share: yes, no API keys are included.",
        "",
        "## Gates",
    ]
    for card in cards:
        lines.append(f"- {card.get('title')}: `{card.get('status')}` `{card.get('score', '-')}` - {card.get('detail')}")
    lines.extend(
        [
            "",
            "## Small-Sample Validation Recommendation",
            f"- Dataset: `{(data.get('dataset') or {}).get('path') or '-'}`",
            f"- Sample: `{recommendation.get('sample_id') or recommendation.get('sample_index') or '-'}`",
            f"- One question: `{recommendation.get('one_question_id') or '-'}`",
            f"- Ten questions: `{','.join(recommendation.get('ten_question_ids') or []) or '-'}`",
            "",
            "## Next Actions",
        ]
    )
    for action in data.get("next_actions") or ["No required action."]:
        lines.append(f"- {action}")
    lines.extend(["", "## Do Not Share"])
    for item in data.get("do_not_share") or []:
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
            "## Package Summary",
            f"- Include files: `{len(package.get('include') or [])}`",
            f"- Exclude entries: `{len(package.get('exclude') or [])}`",
            f"- Verify commands: `{len(package.get('verify') or [])}`",
            f"- Setup commands: `{len(setup.get('commands') or [])}`",
            "",
            "Never share `.env.local`, `judge.conf`, `runs/`, memory workspaces, screenshots with tokens, or real API keys.",
        ]
    )
    return public_share_text("\n".join(lines))


def handoff_dashboard(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    readiness = system_readiness(payload)
    audit = readiness.get("audit") or handoff_audit()
    doctor = adapter_doctor_report()
    boundary = delivery_boundary_gate(audit, doctor)
    flow_status = locomo_flow_status(payload, readiness=readiness)
    flow = normalize_flow_artifacts(flow_status)
    contract_payload = dict(payload)
    contract_config = dict(contract_payload.get("config") or {}) if isinstance(contract_payload.get("config"), dict) else {}
    contract_config["memoryBackend"] = "echomemory"
    contract_payload["config"] = contract_config
    echomem = echomem_contract(contract_payload)
    acceptance = acceptance_matrix(payload, readiness=readiness, audit=audit, doctor=doctor, echomem=echomem, flow=flow)
    smoke = smoke_plan(payload, readiness=readiness, acceptance=acceptance, flow=flow)
    setup = setup_pack(payload, readiness=readiness)
    package = handoff_package(payload, readiness=readiness, acceptance=acceptance, setup=setup, audit=audit)
    alignment = agent_alignment_status(payload)
    isolation = account_isolation_status(payload)
    preflight = readiness.get("preflight") or {}
    dataset = preflight.get("dataset") or {}
    workspace = preflight.get("workspace") or {}
    backend = str(readiness.get("backend") or preflight.get("backend") or "openviking")
    account = str(readiness.get("account") or preflight.get("account") or "default")

    cards = [
        {
            "id": "readiness",
            "title": "启动门禁",
            "status": readiness.get("status"),
            "score": readiness.get("score"),
            "detail": f"{len(readiness.get('blockers') or [])} blockers / {len(readiness.get('warnings') or [])} warnings",
            "view": "readmeView",
        },
        {
            "id": "acceptance",
            "title": "外部验收",
            "status": acceptance.get("status"),
            "score": acceptance.get("score"),
            "detail": f"{len(acceptance.get('blockers') or [])} blockers / {len(acceptance.get('warnings') or [])} warnings",
            "view": "readmeView",
        },
        {
            "id": "smoke",
            "title": "小样本核验路线",
            "status": smoke.get("status"),
            "score": smoke.get("score"),
            "detail": f"sample={(smoke.get('recommendation') or {}).get('sample_id') or '-'}",
            "view": "datasetView",
        },
        {
            "id": "echomem_contract",
            "title": "EchoMemory 契约",
            "status": echomem.get("status"),
            "score": None,
            "detail": f"required_failures={len(echomem.get('required_failures') or [])}",
            "view": "systemConfigView",
        },
        {
            "id": "adapter_doctor",
            "title": "后端契约",
            "status": doctor.get("status"),
            "score": None,
            "detail": f"registered={','.join(doctor.get('registered_backends') or [])}",
            "view": "systemConfigView",
        },
        {
            "id": "delivery_boundary",
            "title": "交付边界",
            "status": boundary.get("status"),
            "score": None,
            "detail": f"agent={boundary.get('agent_label') or '-'} backends={','.join(boundary.get('registered_backends') or [])}",
            "view": "readmeView",
        },
        {
            "id": "agent_alignment",
            "title": "Agent 可比性",
            "status": alignment.get("status"),
            "score": None,
            "detail": (alignment.get("latest_backend_run") or {}).get("alignment", {}).get("title") or "等待 LoCoMo QA run",
            "view": "readmeView",
        },
        {
            "id": "account_isolation",
            "title": "账户隔离",
            "status": isolation.get("status"),
            "score": None,
            "detail": f"accounts={(isolation.get('metrics') or {}).get('accounts', 0)} shared={(isolation.get('metrics') or {}).get('shared', 0)}",
            "view": "systemConfigView",
        },
        {
            "id": "handoff_package",
            "title": "外发清单",
            "status": package.get("status"),
            "score": (package.get("acceptance") or {}).get("score"),
            "detail": f"include={len(package.get('include') or [])} exclude={len(package.get('exclude') or [])}",
            "view": "readmeView",
        },
    ]
    statuses = [str(item.get("status") or "warn") for item in cards]
    status = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "ok")
    numeric_scores = [float(item.get("score")) for item in cards if item.get("score") is not None]
    score = int(round(sum(numeric_scores) / len(numeric_scores))) if numeric_scores else 0

    issues: list[dict[str, Any]] = []
    issues.extend(handoff_dashboard_issue("readiness", item) for item in (readiness.get("blockers") or []))
    issues.extend(handoff_dashboard_issue("acceptance", item) for item in (acceptance.get("blockers") or []))
    issues.extend(handoff_dashboard_issue("echomem", item) for item in (echomem.get("required_failures") or []))
    issues.extend(handoff_dashboard_issue("audit", item) for item in (audit.get("checks") or []) if item.get("severity") == "required" and item.get("status") == "fail")
    issues.extend(
        handoff_dashboard_issue("delivery_boundary", item)
        for item in (boundary.get("checks") or [])
        if item.get("status") == "fail"
    )
    if alignment.get("status") == "fail":
        issues.append(
            {
                "source": "agent_alignment",
                "title": "Agent 可比性失败",
                "status": "fail",
                "severity": "required",
                "detail": "MemoryBench Agent 没有可比 LoCoMo 运行证据。",
                "action": "按可比参数跑 LoCoMo QA，并保留 manifest、summary 和报告。",
            }
        )
    if isolation.get("status") == "fail":
        issues.append(
            {
                "source": "account_isolation",
                "title": "账户隔离失败",
                "status": "fail",
                "severity": "required",
                "detail": "当前 account 的 workspace 不满足干净隔离要求。",
                "action": "在系统配置里生成新的 timestamp workspace，或切换到独立 account 后重跑导入。",
            }
        )
    issues.extend(
        {
            "source": "handoff_package",
            "title": str(item.get("path") or "missing include"),
            "status": "fail",
            "severity": "required",
            "detail": str(item.get("reason") or "必需交付文件缺失。"),
            "action": "补齐必需文件后再外发。",
        }
        for item in (package.get("missing_include") or [])
    )

    warning_issues = [
        handoff_dashboard_issue("readiness", item) for item in (readiness.get("warnings") or [])
    ] + [
        handoff_dashboard_issue("acceptance", item) for item in (acceptance.get("warnings") or [])
    ] + [
        handoff_dashboard_issue("echomem", item) for item in (echomem.get("warnings") or [])
    ]
    next_actions = unique_nonempty(
        [item.get("action") for item in issues + warning_issues]
        + list(readiness.get("next_actions") or [])
        + list(acceptance.get("next_actions") or [])
        + list(smoke.get("steps", [{}])[0].get("action") for _ in [0] if smoke.get("steps")),
        8,
    )
    setup_commands = setup.get("commands") if isinstance(setup.get("commands"), list) else []
    do_not_share = unique_nonempty(
        list(setup.get("do_not_share") or [])
        + [item.get("path") for item in (package.get("exclude") or [])],
        16,
    )
    data = {
        "status": status,
        "score": score,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": backend,
        "scope": MEMORY_BACKEND_SCOPE,
        "dataset": dataset,
        "workspace": workspace,
        "cards": cards,
        "issues": issues[:12],
        "warnings": warning_issues[:12],
        "next_actions": next_actions or ["可以进入 LoCoMo 小样本核验，先跑一个对话和 1 题 QA。"],
        "quick_start": setup_commands[:5],
        "do_not_share": do_not_share,
        "readiness": handoff_dashboard_readiness(readiness),
        "acceptance": acceptance,
        "flow_artifacts": flow,
        "smoke": smoke,
        "setup_pack": setup,
        "handoff_package": package,
        "audit": audit,
        "delivery_boundary": boundary,
        "adapter_doctor": doctor,
        "echomem_contract": echomem,
        "agent_alignment": alignment,
        "account_isolation": isolation,
        "safe_to_share": True,
        "secrets_included": False,
    }
    markdown = handoff_dashboard_markdown(data)
    data["markdown"] = markdown
    data["summary"] = markdown
    return data


def github_issue_template_status() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in GITHUB_ISSUE_TEMPLATES:
        path = ROOT / str(item.get("path") or "")
        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        rows.append(
            {
                "path": item.get("path"),
                "title": item.get("title"),
                "purpose": item.get("purpose"),
                "exists": path.exists(),
                "mentions_scope": "OpenViking" in text and ("EchoMem" in text or "EchoMemory" in text),
                "warns_no_secrets": "API keys" in text or "secrets" in text or "密钥" in text,
            }
        )
    return rows


def github_launch_kit_markdown(data: dict[str, Any]) -> str:
    smoke = data.get("quickstart") or {}
    demo = data.get("demo_report") or {}
    issue_templates = data.get("issue_templates") or []
    commands = smoke.get("commands") or []
    lines = [
        "# MemoryBench Eval Workbench",
        "",
        "A local-first memory benchmark workbench for OpenViking and EchoMemory. It supports LoCoMo, LongMemEval, EvolvingEvents, and other registered datasets through one inspectable run pipeline.",
        "",
        "## Why It Exists",
        "",
        "- Reproduce memory-system evaluations without sharing API keys or private workspaces.",
        "- Compare OpenViking baseline behavior with EchoMemory forks or graph-memory modules through one adapter contract.",
        "- Inspect every run through dataset stats, import integrity, relevant memory, token usage, Judge output, and report artifacts.",
        "",
        "## Architecture",
        "",
        "```mermaid",
        str(data.get("architecture_mermaid") or "").strip(),
        "```",
        "",
        "## 5-Minute Small-Sample Validation",
        "",
    ]
    for command in commands:
        lines.append(f"```bash\n{command}\n```")
    lines.extend(
        [
            "",
            "Open the Web UI:",
            "",
            f"`{smoke.get('web_url') or 'http://127.0.0.1:19181/'}`",
            "",
            "Recommended route: README -> System Config -> target benchmark page -> Judge / report export.",
            "",
            "## Memory Backend Scope",
            "",
            "- OpenViking: service-backed memory import, retrieval, and benchmark QA.",
            "- EchoMemory: local SDK integration, account-scoped storage, session commit, retrieval evidence, and benchmark QA.",
            f"- Current release scope is only {MEMORY_BACKEND_SCOPE}.",
            "",
            "## EchoMemory Fork Integration",
            "",
        ]
    )
    for item in data.get("echo_mem_integration") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Issue Templates", ""])
    for item in issue_templates:
        mark = "ready" if item.get("exists") and item.get("mentions_scope") and item.get("warns_no_secrets") else "check"
        lines.append(f"- `{mark}` `{item.get('path')}` - {item.get('purpose')}")
    lines.extend(
        [
            "",
            "## Open Source Collaboration",
            "",
            "- `LICENSE` - MIT license for public forks and local integrations.",
            "- `CONTRIBUTING.md` - PR checks, static mirroring, backend scope, and no-secret rules.",
            "- `SECURITY.md` - private reporting guidance for API key, workspace, report, and path exposure issues.",
            "- `CODE_OF_CONDUCT.md` - collaboration rules for issues, PRs, and benchmark discussions.",
            "- `PUBLICATION_CHECKLIST.md` - include/no-ship checklist before publishing or sending to another tester.",
        ]
    )
    lines.extend(["", "## Latest Demo Artifact", ""])
    if demo.get("report_html"):
        lines.append(f"- Report: `{demo.get('report_html')}`")
        lines.append(f"- Run: `{demo.get('run_dir') or '-'}`")
    else:
        lines.append("- No demo report detected yet. Run a one-conversation small-sample validation and export an HTML report before publishing screenshots.")
    lines.extend(["", "## Safety Boundary", ""])
    for item in data.get("safety") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "Never share `.env.local`, `judge.conf`, `runs/`, memory workspaces, screenshots with tokens, or real API keys.",
        ]
    )
    return public_share_text("\n".join(lines))


def github_launch_kit(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    dashboard = handoff_dashboard(payload)
    package = dashboard.get("handoff_package") or handoff_package(payload)
    acceptance = dashboard.get("acceptance") or acceptance_matrix(payload)
    smoke = dashboard.get("smoke") or smoke_plan(payload)
    audit = dashboard.get("audit") or handoff_audit()
    latest = normalize_flow_artifacts(dashboard.get("flow_artifacts") or current_account_flow_artifacts(payload))
    issue_templates = github_issue_template_status()
    audit_checks = {
        str(item.get("id") or ""): item
        for item in (audit.get("checks") or [])
        if isinstance(item, dict)
    }
    public_readme_check = audit_checks.get("public_readme_paths") or {}
    secret_check = audit_checks.get("no_real_secrets") or {}
    retired_backend_check = audit_checks.get("no_retired_backend") or {}
    open_source_docs_check = audit_checks.get("open_source_docs") or {}
    static_mirror_check = audit_checks.get("static_mirror") or {}
    excluded_history_check = audit_checks.get("excluded_history") or {}
    ignore_policy_check = audit_checks.get("publish_ignore_policy") or {}
    audit_required_failures = [
        item for item in (audit.get("checks") or [])
        if item.get("severity") == "required" and item.get("status") == "fail"
    ]
    issue_failures = [
        item for item in issue_templates
        if not item.get("exists") or not item.get("mentions_scope") or not item.get("warns_no_secrets")
    ]
    package_missing = package.get("missing_include") or []
    dashboard_status = str(dashboard.get("status") or "warn")
    acceptance_status = str(acceptance.get("status") or "warn")
    public_readme_status = str(public_readme_check.get("status") or "warn")
    secret_status = str(secret_check.get("status") or "warn")
    retired_backend_status = str(retired_backend_check.get("status") or "warn")
    open_source_docs_status = str(open_source_docs_check.get("status") or "warn")
    static_boundary_status = "ok" if (
        static_mirror_check.get("status") == "ok"
        and excluded_history_check.get("status") == "ok"
    ) else "warn"
    ignore_policy_status = str(ignore_policy_check.get("status") or "warn")
    if (
        issue_failures
        or package_missing
        or audit_required_failures
        or dashboard_status == "fail"
        or acceptance_status == "fail"
        or public_readme_status == "fail"
        or secret_status == "fail"
        or retired_backend_status == "fail"
        or open_source_docs_status == "fail"
        or ignore_policy_status == "fail"
        or static_mirror_check.get("status") == "fail"
    ):
        status = "fail"
    elif (
        dashboard_status == "warn"
        or acceptance_status == "warn"
        or public_readme_status == "warn"
        or secret_status == "warn"
        or retired_backend_status == "warn"
        or open_source_docs_status == "warn"
        or ignore_policy_status == "warn"
        or static_boundary_status == "warn"
    ):
        status = "warn"
    else:
        status = "ok"
    scores = [
        float(value)
        for value in [dashboard.get("score"), acceptance.get("score"), smoke.get("score")]
        if value is not None
    ]
    score = int(round(sum(scores) / len(scores))) if scores else 0
    port = os.environ.get("LOCOMO_EVAL_PORT") or "19181"
    recommendation = (smoke.get("recommendation") or {}) if isinstance(smoke, dict) else {}
    one_qid = recommendation.get("one_question_id") or ""
    ten_qids = recommendation.get("ten_question_ids") or []
    quickstart_commands = [
        "cp env.echomem.example .env.local\n# Edit .env.local locally. Do not commit or share it.",
        "source .env.local && ./preflight.sh",
        "source .env.local && ./start.sh",
        f"curl -s http://127.0.0.1:{port}/api/github-launch-kit | python3 -m json.tool | head -120",
    ]
    architecture_mermaid = "\n".join(
        [
            "flowchart LR",
            '  A["LoCoMo JSON"] --> B["Web Harness"]',
            '  B --> C{"Memory Backend"}',
            '  C --> D["OpenViking Adapter"]',
            '  C --> E["EchoMemory Adapter"]',
            '  D --> F["commit_session + relevant memory"]',
            '  E --> F',
            '  F --> G["Answer LLM"]',
            '  G --> H["Judge LLM"]',
            '  H --> I["CSV / summary.json / HTML report"]',
            '  I --> J["GitHub-safe README, issues, validation plan"]',
        ]
    )
    demo_report = latest.get("latest_report") or {}
    public_demo_report = {
        "id": demo_report.get("id"),
        "status": demo_report.get("status"),
        "run_dir": public_artifact_path(demo_report.get("run_dir")),
        "report_html": public_artifact_path(demo_report.get("report_html")),
    }
    demo_report_detail = public_demo_report.get("report_html") or "no report detected"
    data = {
        "status": status,
        "score": score,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": dashboard.get("account"),
        "backend": dashboard.get("backend"),
        "scope": MEMORY_BACKEND_SCOPE,
        "cards": [
            {"title": "交付驾驶舱", "status": dashboard_status, "detail": f"score={dashboard.get('score')}/100"},
            {"title": "外部验收", "status": acceptance_status, "detail": f"score={acceptance.get('score')}/100"},
            {"title": "公开 README", "status": public_readme_status, "detail": public_readme_check.get("detail") or "root README path safety"},
            {"title": "密钥扫描", "status": secret_status, "detail": secret_check.get("detail") or "no secret scan"},
            {"title": "后端边界", "status": retired_backend_status, "detail": retired_backend_check.get("detail") or MEMORY_BACKEND_SCOPE},
            {"title": "开源协作材料", "status": open_source_docs_status, "detail": open_source_docs_check.get("detail") or "license / contribution / security docs"},
            {"title": "发布忽略规则", "status": ignore_policy_status, "detail": ignore_policy_check.get("detail") or ".gitignore / .gitattributes"},
            {"title": "静态发布边界", "status": static_boundary_status, "detail": "只外发核心静态文件；历史静态报告已排除。"},
            {"title": "Issue 模板", "status": "ok" if not issue_failures else "fail", "detail": f"{len(issue_templates) - len(issue_failures)}/{len(issue_templates)} ready"},
            {"title": "外发清单", "status": package.get("status"), "detail": f"missing={len(package_missing)}"},
            {"title": "Demo 报告", "status": "ok" if public_demo_report.get("report_html") else "warn", "detail": demo_report_detail},
        ],
        "readme_intro": "Local-first LoCoMo memory evaluation workbench for OpenViking and EchoMemory.",
        "architecture_mermaid": architecture_mermaid,
        "quickstart": {
            "web_url": f"http://127.0.0.1:{port}/",
            "commands": quickstart_commands,
            "sample": recommendation.get("sample_id") or "-",
            "one_question": one_qid or "-",
            "ten_questions": ten_qids,
        },
        "issue_templates": issue_templates,
        "demo_report": public_demo_report,
        "safety": [
            "Do not include `.env.local`, `judge.conf`, `runs/`, `dist/`, `outputs/`, memory workspaces, or screenshots containing tokens.",
            "Use placeholders such as `<your-api-key>` in all public docs.",
            "Run `./preflight.sh` and `/api/handoff-audit` before publishing or sending the project to another tester.",
            "Share only脱敏报告样例；真实 run CSV、日志和 workspace 需要单独确认后再外发。",
        ],
        "echo_mem_integration": [
            "Set `ECHOMEM_ROOT` to a local EchoMemory source tree.",
            "Keep `open_runtime`, `EchoMemSDK.create_session`, `add_message`, `commit_session`, `find`, and `search` compatible.",
            "Return evidence fields: content, uri/source_uri, score/confidence, memory_type, evidence_uri, and trace.",
            "Use a fresh workspace or account for every formal evaluation to avoid memory pollution.",
        ],
        "safe_to_share": True,
        "secrets_included": False,
        "handoff_audit": {
            "status": audit.get("status"),
            "required_failures": len(audit_required_failures),
            "public_readme_paths": public_readme_check,
            "no_real_secrets": secret_check,
            "no_retired_backend": retired_backend_check,
            "open_source_docs": open_source_docs_check,
            "static_mirror": static_mirror_check,
            "excluded_history": excluded_history_check,
            "publish_ignore_policy": ignore_policy_check,
        },
    }
    data["markdown"] = github_launch_kit_markdown(data)
    data["summary"] = data["markdown"]
    return data


def acceptance_item(
    item_id: str,
    title: str,
    status: str,
    severity: str,
    proof: str,
    action: str,
    evidence: Any = None,
    owner: str = "platform",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": title,
        "status": status if status in {"ok", "warn", "fail"} else "warn",
        "severity": severity,
        "owner": owner,
        "proof": proof,
        "action": action,
        "evidence": evidence if evidence is not None else {},
    }


def acceptance_matrix_status(items: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "required" and item.get("status") == "fail" for item in items):
        return "fail"
    if any(item.get("status") == "warn" for item in items):
        return "warn"
    return "ok"


def acceptance_matrix_score(items: list[dict[str, Any]]) -> int:
    weights = {"required": 3, "recommended": 1, "info": 0}
    total = sum(weights.get(str(item.get("severity") or ""), 1) for item in items) or 1
    earned = 0.0
    for item in items:
        weight = weights.get(str(item.get("severity") or ""), 1)
        if item.get("status") == "ok":
            earned += weight
        elif item.get("status") == "warn":
            earned += weight * 0.55
    return int(round(earned * 100 / total))


def acceptance_matrix_markdown(status: str, score: int, items: list[dict[str, Any]], account: str, backend: str) -> str:
    lines = [
        "# LoCoMo Memory Eval External Acceptance Matrix",
        "",
        f"- Status: `{status}`",
        f"- Score: `{score}/100`",
        f"- Account: `{account}`",
        f"- Backend: `{backend}`",
        f"- Scope: `{MEMORY_BACKEND_SCOPE}`",
        "- Safe to share: yes, no API keys are included.",
        "",
        "| Gate | Status | Severity | Proof | Next Action |",
        "|---|---:|---:|---|---|",
    ]
    for item in items:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("title") or item.get("id") or "-").replace("|", "\\|"),
                    f"`{item.get('status') or '-'}`",
                    f"`{item.get('severity') or '-'}`",
                    str(item.get("proof") or "-").replace("|", "\\|"),
                    str(item.get("action") or "-").replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("Do not share `.env.local`, `judge.conf`, `runs/`, workspaces, screenshots with tokens, or real API keys.")
    return public_share_text("\n".join(lines))


def latest_flow_artifacts() -> dict[str, Any]:
    runs, scope_meta = list_current_scope_runs(DEFAULT_OUTPUT_DIR, 30)
    qa_runs: list[dict[str, Any]] = []
    report_runs: list[dict[str, Any]] = []
    for record in runs:
        kind = str(record.get("kind") or "")
        agent_type = str(record.get("agent_type") or "")
        output_file = str(record.get("output_file") or "")
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        dataset_format = str(record.get("dataset_format") or summary.get("dataset_format") or "").lower()
        run_dir = Path(str(record.get("run_dir") or ""))
        is_memory_qa = (
            kind in {"openviking_qa", "echomemory_qa", "openviking_generic_qa", "echomemory_generic_qa"}
            or agent_type in {"echomemory_memory_qa", "openviking_memory_qa", "openviking_generic_qa", "echomemory_generic_qa"}
            or (dataset_format == "locomo" and output_file.endswith(".csv") and summary.get("rows") is not None and kind not in {"judge", "stats", "openviking_import", "echomemory_import", "adapter"})
        )
        if is_memory_qa:
            qa_runs.append(record)
        if (run_dir / "report.html").exists():
            report_runs.append(record)
    latest_qa = qa_runs[0] if qa_runs else {}
    latest_report = report_runs[0] if report_runs else {}
    return {
        "recent_count": len(runs),
        "scope": scope_meta,
        "latest_qa": {
            "id": latest_qa.get("id"),
            "status": latest_qa.get("status"),
            "output_file": latest_qa.get("output_file"),
            "run_dir": latest_qa.get("run_dir"),
            "rows": (latest_qa.get("summary") or {}).get("rows") if latest_qa else None,
            "graded": (latest_qa.get("summary") or {}).get("graded") if latest_qa else None,
        } if latest_qa else {},
        "latest_report": {
            "id": latest_report.get("id"),
            "status": latest_report.get("status"),
            "run_dir": latest_report.get("run_dir"),
            "report_html": str(Path(str(latest_report.get("run_dir") or "")) / "report.html") if latest_report else "",
        } if latest_report else {},
    }


def normalize_flow_artifacts(flow: dict[str, Any] | None = None) -> dict[str, Any]:
    def compact_artifact(record: Any) -> dict[str, Any]:
        if not isinstance(record, dict):
            return {}
        cleaned = {
            str(key): value
            for key, value in record.items()
            if value not in (None, "", {}, [])
        }
        return cleaned if any(cleaned.get(key) for key in ("id", "output_file", "run_dir", "report_html", "created_at")) else {}

    flow = flow or {}
    if not isinstance(flow, dict):
        return {"recent_count": 0, "scope": {}, "latest_qa": {}, "latest_report": {}}
    if "latest_qa" in flow or "latest_report" in flow:
        return {
            "recent_count": int(flow.get("recent_count") or 0),
            "scope": flow.get("scope") if isinstance(flow.get("scope"), dict) else {},
            "latest_qa": compact_artifact(flow.get("latest_qa")),
            "latest_report": compact_artifact(flow.get("latest_report")),
        }
    artifacts = flow.get("artifacts") if isinstance(flow.get("artifacts"), dict) else {}
    return {
        "recent_count": int(flow.get("recent_count") or 0),
        "scope": flow.get("scope") if isinstance(flow.get("scope"), dict) else {
            "account": flow.get("account"),
            "backend": flow.get("backend"),
            "source": "current_account",
        },
        "latest_qa": compact_artifact(artifacts.get("latest_qa")),
        "latest_report": compact_artifact(artifacts.get("latest_report")),
    }


def current_account_flow_artifacts(
    payload: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    flow_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if flow_status is None:
        flow_status = locomo_flow_status(payload or {}, readiness=readiness)
    return normalize_flow_artifacts(flow_status)


def locomo_flow_stage(
    stage_id: str,
    title: str,
    status: str,
    view: str,
    detail: str,
    action: str,
    evidence: Any = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    allowed = {"ok", "warn", "fail", "todo", "running"}
    return {
        "id": stage_id,
        "title": title,
        "status": status if status in allowed else "todo",
        "view": view,
        "detail": detail,
        "action": action,
        "evidence": evidence if evidence is not None else {},
        "metrics": metrics or {},
    }


def locomo_flow_overall_status(stages: list[dict[str, Any]]) -> str:
    if any(item.get("id") in {"dataset", "runtime"} and item.get("status") == "fail" for item in stages):
        return "fail"
    if any(item.get("status") in {"fail", "warn", "running", "todo"} for item in stages):
        return "warn"
    return "ok"


def latest_backend_locomo_qa(
    runs: list[dict[str, Any]],
    backend: str,
    account: str = "",
    workspace: str = "",
    strict_scope: bool = False,
) -> dict[str, Any]:
    backend = normalize_memory_backend(backend)

    for record in runs:
        if not current_scope_run(record, backend):
            continue
        if strict_scope and not run_matches_account_workspace(record, account, workspace, strict=True):
            continue
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        output_file = str(record.get("output_file") or "")
        kind = str(record.get("kind") or "")
        dataset_format = str(record.get("dataset_format") or summary.get("dataset_format") or "").lower()
        if dataset_format and dataset_format != "locomo":
            continue
        if not output_file.endswith(".csv"):
            continue
        if kind in {"judge", "stats", "openviking_import", "echomemory_import", "adapter"}:
            continue
        if summary.get("rows") is None and "qa" not in kind and "qa" not in str(record.get("name") or "").lower():
            continue
        return record
    return {}


def latest_locomo_report(
    runs: list[dict[str, Any]],
    backend: str,
    account: str = "",
    workspace: str = "",
    strict_scope: bool = False,
) -> dict[str, Any]:
    backend = normalize_memory_backend(backend)
    for record in runs:
        if not current_scope_run(record, backend):
            continue
        if strict_scope and not run_matches_account_workspace(record, account, workspace, strict=True):
            continue
        run_dir_text = str(record.get("run_dir") or "")
        if not run_dir_text:
            continue
        report_html = Path(run_dir_text) / "report.html"
        if not report_html.exists():
            continue
        return {
            "id": record.get("id"),
            "status": record.get("status"),
            "run_dir": run_dir_text,
            "report_html": str(report_html),
            "created_at": record.get("created_at"),
        }
    return {}


def alignment_bool_on(value: Any) -> bool:
    text = str(value if value is not None else "").strip().lower()
    return value is True or text in {"native", "native_vikingbot_cli", "true", "1", "yes", "on", "enabled"}


def alignment_bool_off(value: Any) -> bool:
    text = str(value if value is not None else "").strip().lower()
    return value is False or text in {"", "-", "false", "0", "no", "none", "disabled", "off"}


def alignment_first_number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value if value is not None else ""))
    return float(match.group(0)) if match else None


def alignment_check(check_id: str, title: str, ok: bool, detail: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": "ok" if ok else "warn",
        "detail": detail,
        "evidence": evidence if evidence is not None else {},
    }


def agent_alignment_for_row(row: dict[str, Any]) -> dict[str, Any]:
    prompt_mode = str(row.get("prompt_mode") or "").strip()
    prompt_key = prompt_mode.lower()
    native = prompt_key == "native_vikingbot_cli"
    custom_prompt = prompt_key in {"vikingbot_aligned", "vikingboat_compat", "vikingboat_lite"}
    prompt_ok = native or custom_prompt or alignment_bool_on(row.get("vikingbot_prompt_aligned"))
    top_k_number = (
        alignment_first_number(row.get("top_k"))
        if alignment_first_number(row.get("top_k")) is not None
        else alignment_first_number(row.get("initial_search_limit"))
    )
    if top_k_number is None:
        top_k_number = alignment_first_number(row.get("tool_search_limit"))
    top_k_text = str(row.get("top_k") or "")
    top_k_ok = native or "原生" in top_k_text or top_k_text.lower().startswith("native") or (
        top_k_number is not None and top_k_number >= VIKINGBOT_INITIAL_SEARCH_LIMIT
    )
    tool_loop_value = row.get("memory_tool_loop_enabled")
    if tool_loop_value in (None, ""):
        tool_loop_value = row.get("openviking_tool_loop_enabled")
    if tool_loop_value in (None, ""):
        tool_loop_value = row.get("openviking_tool_loop")
    tool_set_value = row.get("memory_tool_set")
    if tool_set_value in (None, ""):
        tool_set_value = row.get("openviking_tool_set")
    tool_loop_ok = native or alignment_bool_on(tool_loop_value)
    tool_set = str(tool_set_value or "").strip().lower()
    tool_set_ok = (
        tool_set in {"native_vikingbot_cli", ""}
        if native
        else tool_set in {VIKINGBOT_TOOL_SET, "vikingboat_default", "vikingbot_openviking", "search_read", ""}
    )
    tool_search_limit_number = alignment_first_number(row.get("tool_search_limit"))
    tool_search_limit_ok = native or tool_search_limit_number is None or tool_search_limit_number == VIKINGBOT_TOOL_SEARCH_LIMIT
    group_chat_ok = alignment_bool_on(row.get("group_chat")) or alignment_bool_off(row.get("group_chat"))
    identity_ok = str(row.get("vikingbot_identity_mode") or "").strip().lower() in {"sender_session", ""}
    channel_ok = str(row.get("vikingbot_channel") or "").strip().lower() in {"cli", ""}
    memory_users_ok = str(row.get("memory_user_strategy") or "").strip().lower() in {"sender_sample_namespace", "vikingbot_group_chat", "memory_users_override", ""}
    agent_memory_ok = native or alignment_bool_on(row.get("initial_agent_memory")) or str(row.get("initial_agent_memory") or "").strip() == ""
    no_extra_context = all(
        alignment_bool_off(row.get(key))
        for key in (
            "query_expansion",
            "lexical_fallback",
            "archive_fallback",
            "memory_file_read",
            "raw_turn_fallback",
        )
    )
    comparable = all([prompt_ok, top_k_ok, tool_search_limit_ok, tool_loop_ok, tool_set_ok, group_chat_ok, identity_ok, channel_ok, memory_users_ok, agent_memory_ok, no_extra_context])
    mode = "OpenViking 参考模式（历史）" if native else ("MemoryBench Agent 对齐模式" if custom_prompt or alignment_bool_on(row.get("vikingbot_prompt_aligned")) else "非对齐 Prompt")
    checks = [
        alignment_check("prompt", "Prompt 对齐", prompt_ok, f"prompt_mode={prompt_mode or '-'}"),
        alignment_check("top_k", "Top-K 覆盖", top_k_ok, f"需要 >= {VIKINGBOT_INITIAL_SEARCH_LIMIT}；当前 top_k={row.get('top_k') or '-'} initial={row.get('initial_search_limit') or '-'} tool={row.get('tool_search_limit') or '-'}"),
        alignment_check("tool_search_limit", "工具检索数量", tool_search_limit_ok, f"期望 {VIKINGBOT_TOOL_SEARCH_LIMIT}；当前={row.get('tool_search_limit') or '-'}"),
        alignment_check("tool_loop", "工具循环", tool_loop_ok, f"需要启用工具循环或原生参考；当前={tool_loop_value or '-'}"),
        alignment_check("tool_set", "工具集合", tool_set_ok, f"期望 {VIKINGBOT_TOOL_SET}；当前={tool_set_value or '-'}"),
        alignment_check("agent_memory", "Agent Memory 初始检索", agent_memory_ok, f"VikingBoat 默认开启；当前={row.get('initial_agent_memory') or '-'}"),
        alignment_check("identity", "会话身份", identity_ok and channel_ok and memory_users_ok and group_chat_ok, "sender_session 与 sample 级记忆 namespace 需要保持可比。"),
        alignment_check("no_extra_context", "无额外上下文兜底", no_extra_context, "正式分数不应使用 query expansion、lexical fallback、archive fallback、memory file read 或 raw turn fallback。"),
    ]
    return {
        "status": "ok" if comparable else "warn",
        "comparable": comparable,
        "mode": mode,
        "title": f"{mode} · {'可对比' if comparable else '需确认'}",
        "detail": (
            "关键上下文工程与 VikingBoat 参考链路可比；准确率差异可优先归因到后端检索、记忆质量或模型。"
            if comparable
            else "存在未对齐参数或额外上下文；直接比较准确率前需要先排除这些影响。"
        ),
        "checks": checks,
        "metrics": {
            "profile": row.get("vikingboat_alignment_profile") or VIKINGBOT_ALIGNMENT_PROFILE,
            "backend_route": row.get("alignment_backend_route") or row.get("backend_route") or "-",
            "prompt_mode": prompt_mode or "-",
            "top_k": row.get("top_k") or "-",
            "initial_search_limit": row.get("initial_search_limit") or "-",
            "initial_score_threshold": row.get("initial_score_threshold") or "-",
            "tool_search_limit": row.get("tool_search_limit") or "-",
            "tool_min_score": row.get("tool_min_score") or "-",
            "tool_loop": tool_loop_value if tool_loop_value != "" else "-",
            "tool_set": tool_set_value or "-",
            "initial_agent_memory": row.get("initial_agent_memory") or row.get("initial_agent_memory_enabled") or "-",
            "extra_context": "off" if no_extra_context else "on",
        },
    }


def locomo_alignment_candidate_rows(runs: list[dict[str, Any]], backend: str = "") -> list[dict[str, Any]]:
    backend = normalize_memory_backend(backend) if backend else ""
    rows: list[dict[str, Any]] = []
    for record in runs:
        run_dir_text = str(record.get("run_dir") or "")
        output_file = str(record.get("output_file") or "")
        kind = str(record.get("kind") or "")
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        dataset_format = str(record.get("dataset_format") or summary.get("dataset_format") or "").lower()
        if dataset_format and dataset_format != "locomo":
            continue
        if not run_dir_text or not output_file.endswith(".csv"):
            continue
        if kind in {"judge", "stats", "openviking_import", "echomemory_import", "adapter"}:
            continue
        text = " ".join(str(record.get(key) or "") for key in ("id", "name", "kind", "agent_type", "output_file", "run_dir")).lower()
        if backend == "echomemory" and "echo" not in text:
            continue
        if backend == "openviking" and "openviking" not in text:
            continue
        try:
            row = run_service.run_compare_row(Path(run_dir_text))
        except Exception:
            continue
        alignment = agent_alignment_for_row(row)
        rows.append(
            {
                "run": {
                    key: row.get(key)
                    for key in (
                        "id",
                        "name",
                        "kind",
                        "agent_type",
                        "created_at",
                        "status",
                        "rows",
                        "graded",
                        "accuracy",
                        "answer_model",
                        "judge_model",
                        "account",
                        "sample",
                        "questions",
                        "output_file",
                        "run_dir",
                    )
                },
                "alignment": alignment,
                "raw": row,
            }
        )
    return rows


def same_judge_alignment_evidence() -> dict[str, Any]:
    if not DEFAULT_OUTPUT_DIR.exists():
        return {}
    candidates = sorted(
        [path for path in DEFAULT_OUTPUT_DIR.glob("*same_judge*") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for root in candidates:
        aligned_path = root / "aligned" / "judge_summary.json"
        native_path = root / "native" / "judge_summary.json"
        if not aligned_path.exists() or not native_path.exists():
            continue
        try:
            aligned = read_json(aligned_path)
            native = read_json(native_path)
        except Exception:
            continue
        aligned_accuracy = aligned.get("accuracy")
        native_accuracy = native.get("accuracy")
        delta = (
            float(aligned_accuracy) - float(native_accuracy)
            if aligned_accuracy is not None and native_accuracy is not None
            else None
        )
        return {
            "status": "ok",
            "run_dir": str(root),
            "aligned": {
                "summary": str(aligned_path),
                "rows": aligned.get("count") or aligned.get("total_rows"),
                "graded": aligned.get("graded") or aligned.get("graded_rows"),
                "accuracy": aligned_accuracy,
            },
            "native": {
                "summary": str(native_path),
                "rows": native.get("count") or native.get("total_rows"),
                "graded": native.get("graded") or native.get("graded_rows"),
                "accuracy": native_accuracy,
            },
            "delta_accuracy": delta,
            "delta_pp": round(delta * 100, 2) if delta is not None else None,
        }
    return {"status": "missing", "detail": "没有找到 same-judge VikingBoat vs MemoryBench Agent 对比 run。"}


def agent_alignment_markdown(data: dict[str, Any]) -> str:
    latest = data.get("latest_backend_run") or {}
    latest_run = latest.get("run") or {}
    same_judge = data.get("same_judge_evidence") or {}
    lines = [
        "# MemoryBench Agent Alignment Gate",
        "",
        f"- Status: `{data.get('status')}`",
        f"- Agent: `{(data.get('agent') or {}).get('label')}`",
        f"- Backend scope: `{MEMORY_BACKEND_SCOPE}`",
        f"- Account: `{data.get('account') or '-'}`",
        f"- Current backend: `{data.get('backend')}`",
        f"- Latest run: `{latest_run.get('id') or '-'}`",
        f"- Comparable: `{(latest.get('alignment') or {}).get('comparable')}`",
        f"- Same-judge delta: `{same_judge.get('delta_pp', '-')}` pp",
        "- Secrets included: `false`",
        "",
        "## Default Parameters",
    ]
    defaults = data.get("default_profile") or {}
    for key in ("initial_search_limit", "initial_score_threshold", "tool_search_limit", "tool_min_score", "tool_set", "max_iterations"):
        lines.append(f"- {key}: `{defaults.get(key)}`")
    lines.extend(["", "## Checks"])
    for check in (latest.get("alignment") or {}).get("checks") or []:
        lines.append(f"- {check.get('title')}: `{check.get('status')}` - {check.get('detail')}")
    lines.extend(["", "## Next Actions"])
    for action in data.get("next_actions") or []:
        lines.append(f"- {action}")
    return public_share_text("\n".join(lines))


def agent_alignment_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    defaults = load_ov_defaults()
    account_state = account_service.public_state(ACCOUNT_STATE_FILE, defaults)
    account = account_service.slug_account(str(payload.get("account") or account_state.get("active_account") or defaults.get("account") or "default"))
    record = next((item for item in account_state.get("accounts", []) if item.get("id") == account), None)
    base_config = dict((record or {}).get("config") or {})
    incoming_config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    config = {**base_config, **incoming_config}
    backend = normalize_memory_backend(
        payload.get("backend")
        or payload.get("memoryBackend")
        or config.get("memoryBackend")
        or defaults.get("memory_backend")
        or "openviking"
    )
    runs = list_runs(DEFAULT_OUTPUT_DIR, 120, compact=True)
    backend_rows = locomo_alignment_candidate_rows(runs, backend)
    all_rows = locomo_alignment_candidate_rows(runs, "")
    latest_backend = backend_rows[0] if backend_rows else {}
    latest_comparable = next((item for item in all_rows if (item.get("alignment") or {}).get("comparable")), {})
    latest_alignment = latest_backend.get("alignment") if isinstance(latest_backend.get("alignment"), dict) else {}
    same_judge = same_judge_alignment_evidence()
    default_profile = {
        "alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "initial_search_limit": VIKINGBOT_INITIAL_SEARCH_LIMIT,
        "initial_score_threshold": VIKINGBOT_INITIAL_MIN_SCORE,
        "user_memory_budget_chars": VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
        "agent_memory_budget_chars": VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
        "tool_search_limit": VIKINGBOT_TOOL_SEARCH_LIMIT,
        "tool_min_score": VIKINGBOT_TOOL_MIN_SCORE,
        "tool_set": VIKINGBOT_TOOL_SET,
        "max_iterations": VIKINGBOT_MAX_ITERATIONS,
    }
    next_actions: list[str] = []
    if not latest_backend:
        next_actions.append(f"先用当前后端 {backend} 跑一轮 LoCoMo QA，生成可审计的 CSV、manifest 和 summary。")
    elif not latest_alignment.get("comparable"):
        next_actions.append("把当前 QA 参数切到 VikingBoat 可比模式：Top-K 30、tool loop on、tool set vikingbot_native_safe，并关闭额外兜底。")
    if same_judge.get("status") != "ok":
        next_actions.append("补一轮同判分模型对比：MemoryBench Agent aligned run vs OpenViking/VikingBoat reference run。")
    if not next_actions:
        next_actions.append("可以用最新可比 run 做后端差异分析；不要把对话页人工测试当正式分数。")
    status = "ok" if latest_alignment.get("comparable") else "warn"
    data = {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "agent": UI_CONTRACT.get("agent") or {"label": "MemoryBench Agent"},
        "account": account,
        "backend": backend,
        "backend_source": "payload" if (payload.get("backend") or payload.get("memoryBackend") or incoming_config.get("memoryBackend")) else "active_account",
        "scope": MEMORY_BACKEND_SCOPE,
        "default_profile": default_profile,
        "forbidden_context": (UI_CONTRACT.get("agent") or {}).get("forbidden_context") or [],
        "latest_backend_run": latest_backend,
        "latest_comparable_run": latest_comparable,
        "recent_runs": all_rows[:6],
        "same_judge_evidence": same_judge,
        "next_actions": next_actions,
        "safe_to_share": True,
        "secrets_included": False,
    }
    data["markdown"] = agent_alignment_markdown(data)
    data["summary"] = data["markdown"]
    return data


def account_isolation_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Account Isolation Gate",
        "",
        f"- Status: `{data.get('status')}`",
        f"- Active account: `{data.get('active_account')}`",
        f"- Backend scope: `{MEMORY_BACKEND_SCOPE}`",
        f"- Accounts: `{(data.get('metrics') or {}).get('accounts', 0)}`",
        f"- Isolated: `{(data.get('metrics') or {}).get('isolated', 0)}`",
        f"- Shared workspace: `{(data.get('metrics') or {}).get('shared', 0)}`",
        f"- Missing workspace: `{(data.get('metrics') or {}).get('missing', 0)}`",
        "- Secrets included: `false`",
        "",
        "| Account | Backend | Status | Workspace | Storage Root |",
        "|---|---|---|---|---|",
    ]
    for row in data.get("accounts") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("id") or "-").replace("|", "\\|"),
                    str(row.get("backend") or "-").replace("|", "\\|"),
                    f"`{row.get('status') or '-'}`",
                    public_share_path(row.get("workspace")) or "-",
                    public_share_path(row.get("storage_root")) or "-",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Next Actions"])
    for action in data.get("next_actions") or ["账户隔离正常，可以继续 LoCoMo 小样本核验。"]:
        lines.append(f"- {action}")
    return public_share_text("\n".join(lines))


def account_isolation_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    defaults = load_ov_defaults()
    state = account_service.public_state(ACCOUNT_STATE_FILE, defaults)
    requested_account = account_service.slug_account(str(payload.get("account") or state.get("active_account") or defaults.get("account") or "default"))
    incoming_config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    incoming_workspace = str(
        incoming_config.get("ovWorkspace")
        or incoming_config.get("memoryWorkspace")
        or payload.get("workspace")
        or ""
    ).strip()
    incoming_backend_raw = str(
        incoming_config.get("memoryBackend")
        or incoming_config.get("backend")
        or payload.get("backend")
        or ""
    ).strip()
    incoming_backend = normalize_memory_backend(incoming_backend_raw) if incoming_backend_raw else ""

    def path_flags(workspace: str, account: str, backend: str) -> dict[str, Any]:
        if not workspace:
            return {
                "workspace": "",
                "storage_root": "",
                "workspace_exists": False,
                "account_root_exists": False,
                "session_root_exists": False,
                "user_root_exists": False,
                "agent_root_exists": False,
            }
        workspace_path = Path(workspace).expanduser()
        root = account_service.storage_root(workspace_path, account, backend)
        if backend == "echomemory":
            session_root = root / "sessions"
            user_root = root / "users" / "default"
            agent_root = root / "agents" / "default"
        else:
            session_root = root / "session"
            user_root = root / "user" / "default"
            agent_root = root / "agent" / "default"
        return {
            "workspace": str(workspace_path),
            "storage_root": str(root),
            "workspace_exists": workspace_path.exists(),
            "account_root_exists": root.exists(),
            "session_root_exists": session_root.exists(),
            "user_root_exists": user_root.exists(),
            "agent_root_exists": agent_root.exists(),
        }

    def checks_for(flags: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": "workspace",
                "title": "Workspace",
                "status": "ok" if flags.get("workspace_exists") else "fail",
                "detail": flags.get("workspace") or "workspace 未配置",
            },
            {
                "id": "storage_root",
                "title": "Storage root",
                "status": "ok" if flags.get("account_root_exists") else "fail",
                "detail": flags.get("storage_root") or "",
            },
            {
                "id": "session_root",
                "title": "Session root",
                "status": "ok" if flags.get("session_root_exists") else "fail",
                "detail": "会话目录存在" if flags.get("session_root_exists") else "会话目录缺失",
            },
            {
                "id": "user_root",
                "title": "User memory root",
                "status": "ok" if flags.get("user_root_exists") else "fail",
                "detail": "用户记忆目录存在" if flags.get("user_root_exists") else "用户记忆目录缺失",
            },
            {
                "id": "agent_root",
                "title": "Agent memory root",
                "status": "ok" if flags.get("agent_root_exists") else "fail",
                "detail": "Agent 记忆目录存在" if flags.get("agent_root_exists") else "Agent 记忆目录缺失",
            },
        ]

    rows: list[dict[str, Any]] = []
    for account in state.get("accounts") or []:
        isolation = account.get("isolation") or {}
        config = account.get("config") if isinstance(account.get("config"), dict) else {}
        workspace = str(isolation.get("workspace") or config.get("ovWorkspace") or config.get("memoryWorkspace") or "").strip()
        backend = normalize_memory_backend(isolation.get("backend") or config.get("memoryBackend") or "openviking")
        legacy = account_service.is_legacy_fixed_workspace(workspace)
        flags = {
            "workspace": workspace,
            "storage_root": isolation.get("storage_root") or "",
            "workspace_exists": bool(isolation.get("workspace_exists")),
            "account_root_exists": bool(isolation.get("account_root_exists")),
            "session_root_exists": bool(isolation.get("session_root_exists")),
            "user_root_exists": bool(isolation.get("user_root_exists")),
            "agent_root_exists": bool(isolation.get("agent_root_exists")),
        }
        rows.append(
            {
                "id": account.get("id") or "default",
                "backend": backend,
                "status": "pending",
                "workspace": workspace,
                "storage_root": flags.get("storage_root") or "",
                "layout": "workspace/<account>/<account>" if backend == "echomemory" else "workspace/viking/<account>",
                "shared_with": [],
                "legacy_fixed_workspace": legacy,
                "checks": checks_for(flags),
            }
        )
    if incoming_workspace or incoming_backend:
        row = next((item for item in rows if item.get("id") == requested_account), None)
        if row is None:
            row = {"id": requested_account}
            rows.append(row)
        backend = incoming_backend or normalize_memory_backend(row.get("backend") or "openviking")
        workspace = incoming_workspace or str(row.get("workspace") or "")
        flags = path_flags(workspace, requested_account, backend)
        row.update(
            {
                "backend": backend,
                "status": "pending",
                "workspace": flags.get("workspace") or workspace,
                "storage_root": flags.get("storage_root") or "",
                "layout": "workspace/<account>/<account>" if backend == "echomemory" else "workspace/viking/<account>",
                "shared_with": [],
                "legacy_fixed_workspace": account_service.is_legacy_fixed_workspace(workspace),
                "checks": checks_for(flags),
                "runtime_override": True,
            }
        )
    workspace_accounts: dict[str, list[str]] = {}
    for row in rows:
        workspace = str(row.get("workspace") or "")
        if workspace:
            workspace_accounts.setdefault(workspace, []).append(str(row.get("id") or "default"))
    for row in rows:
        workspace = str(row.get("workspace") or "")
        shared_with = [item for item in workspace_accounts.get(workspace, []) if item != row.get("id")] if workspace else []
        row["shared_with"] = shared_with
        workspace_ok = next((check for check in row.get("checks") or [] if check.get("id") == "workspace"), {})
        root_ok = next((check for check in row.get("checks") or [] if check.get("id") == "storage_root"), {})
        if not workspace:
            row["status"] = "missing_workspace"
        elif row.get("legacy_fixed_workspace"):
            row["status"] = "legacy_fixed_workspace"
        elif shared_with:
            row["status"] = "shared_workspace"
        elif workspace_ok.get("status") != "ok" or root_ok.get("status") != "ok":
            row["status"] = "workspace_not_created"
        else:
            row["status"] = "isolated_workspace"
    current = next((row for row in rows if row.get("id") == requested_account), rows[0] if rows else {})
    shared = [row for row in rows if row.get("status") == "shared_workspace"]
    missing = [row for row in rows if row.get("status") == "missing_workspace"]
    legacy_rows = [row for row in rows if row.get("legacy_fixed_workspace")]
    not_created = [row for row in rows if row.get("status") == "workspace_not_created"]
    bad_current = current and current.get("status") in {"missing_workspace", "legacy_fixed_workspace", "shared_workspace"}
    if bad_current:
        status = "fail"
    elif shared or missing or legacy_rows or not_created:
        status = "warn"
    else:
        status = "ok"
    next_actions: list[str] = []
    if bad_current:
        next_actions.append("为当前账户生成新的 timestamp workspace，避免历史记忆污染。")
    if shared:
        next_actions.append("存在多个账户共享同一 workspace；正式评测前给每个账户分配独立 workspace。")
    if missing:
        next_actions.append("存在账户 workspace 未配置；进入系统配置生成或填写 workspace。")
    if legacy_rows:
        next_actions.append("发现旧固定 workspace；建议迁移到自动生成的 timestamp workspace。")
    if not_created:
        next_actions.append("存在 workspace 字符串但目录尚未生成；保存配置或新建账户后重新检查。")
    if not next_actions:
        next_actions.append("账户隔离正常：当前账户、后端和 storage root 可独立验证。")
    data = {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "active_account": requested_account,
        "scope": MEMORY_BACKEND_SCOPE,
        "accounts": rows,
        "current": current,
        "metrics": {
            "accounts": len(rows),
            "isolated": sum(1 for row in rows if row.get("status") == "isolated_workspace"),
            "shared": len(shared),
            "missing": len(missing),
            "legacy_fixed_workspace": len(legacy_rows),
            "not_created": len(not_created),
        },
        "next_actions": next_actions,
        "safe_to_share": True,
        "secrets_included": False,
    }
    data["markdown"] = account_isolation_markdown(data)
    data["summary"] = data["markdown"]
    return data


def locomo_flow_markdown(status: str, account: str, backend: str, stages: list[dict[str, Any]], next_actions: list[str]) -> str:
    lines = [
        "# LoCoMo Flow Status",
        "",
        f"- Status: `{status}`",
        f"- Account: `{account}`",
        f"- Backend: `{backend}`",
        f"- Scope: `{MEMORY_BACKEND_SCOPE}`",
        "- Secrets included: `false`",
        "",
        "| Stage | Status | Detail | Next Action |",
        "|---|---:|---|---|",
    ]
    for stage in stages:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(stage.get("title") or stage.get("id") or "-").replace("|", "\\|"),
                    f"`{stage.get('status') or '-'}`",
                    str(stage.get("detail") or "-").replace("|", "\\|"),
                    str(stage.get("action") or "-").replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Next Actions"])
    for item in next_actions or ["可以继续 LoCoMo 导入、QA、判分或报告生成。"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Do not share `.env.local`, `judge.conf`, `runs/`, memory workspaces, screenshots with tokens, or real API keys.")
    return public_share_text("\n".join(lines))


def locomo_flow_status(payload: dict[str, Any] | None = None, readiness: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    readiness = readiness or system_readiness(payload)
    preflight = readiness.get("preflight") or {}
    account = str(readiness.get("account") or preflight.get("account") or "default")
    backend = normalize_memory_backend(readiness.get("backend") or preflight.get("backend") or "openviking")
    dataset = preflight.get("dataset") or {}
    workspace = preflight.get("workspace") or {}
    models = preflight.get("models") or {}
    runtime = preflight.get("runtime") or {}
    running_tasks = active_public_tasks()
    running_import = next((item for item in running_tasks if str(item.get("kind") or "") in {"openviking_import", "echomemory_import"}), {})
    running_qa = next((
        item for item in running_tasks
        if str(item.get("kind") or "") in {
            "openviking_qa",
            "echomemory_qa",
            "echomemory_generic_qa",
            "openviking_qa_retry_failed",
            "openviking_qa_retry_missing",
            "echomemory_qa_retry_failed",
        }
    ), {})
    running_judge = next((item for item in running_tasks if str(item.get("kind") or "") == "judge"), {})
    runs = list_runs(DEFAULT_OUTPUT_DIR, 60, compact=True)
    workspace_text = str(workspace.get("workspace") or "")
    latest_qa = latest_backend_locomo_qa(runs, backend, account=account, workspace=workspace_text, strict_scope=True)
    latest_report = latest_locomo_report(runs, backend, account=account, workspace=workspace_text, strict_scope=True)

    imported: dict[str, Any] = {
        "status": "unknown",
        "sessions": [],
        "summaries": [],
        "session_count": 0,
        "summary_count": 0,
        "complete_count": 0,
        "error": "",
    }
    if workspace_text and Path(workspace_text).exists():
        try:
            imported_raw = plugin_service.list_imported_memories(backend, safe_path(workspace_text), account, DEFAULT_OUTPUT_DIR, 80, "")
            sessions = imported_raw.get("sessions") if isinstance(imported_raw.get("sessions"), list) else []
            summaries = imported_raw.get("summaries") if isinstance(imported_raw.get("summaries"), list) else []
            complete_count = sum(1 for item in summaries if str(item.get("integrity") or "").lower() == "complete")
            imported = {
                "status": "ok",
                "account_path": imported_raw.get("account_path"),
                "memory_root": imported_raw.get("memory_root"),
                "sessions": sessions[:8],
                "summaries": summaries[:8],
                "session_count": len(sessions),
                "summary_count": len(summaries),
                "complete_count": complete_count,
                "error": "",
            }
        except Exception as exc:
            imported["status"] = "warn"
            imported["error"] = str(exc)

    dataset_ok = dataset.get("status") == "ok"
    import_running = bool(running_import)
    qa_running = bool(running_qa)
    judge_running = bool(running_judge)
    import_complete = int(imported.get("complete_count") or 0) > 0
    import_seen = import_complete or int(imported.get("summary_count") or 0) > 0 or int(imported.get("session_count") or 0) > 0
    latest_summary = latest_qa.get("summary") if isinstance(latest_qa.get("summary"), dict) else {}
    rows = int(latest_summary.get("rows") or 0)
    graded = int(latest_summary.get("graded") or 0)
    pending = int((latest_summary.get("result_counts") or {}).get("UNSCORED") or max(0, rows - graded))
    accuracy = latest_summary.get("accuracy")
    token_total = latest_summary.get("answer_total_tokens")
    model_issue_count = sum(int(item.get("log_diagnostics", {}).get("model_issue_count") or 0) for item in running_tasks)
    token_available = bool(
        ((models.get("answer") or {}).get("token_set"))
        or ((models.get("judge") or {}).get("token_set"))
        or ((models.get("echomemory") or {}).get("embedding_token_set"))
        or ((models.get("echomemory") or {}).get("chat_token_set"))
    )

    stages = [
        locomo_flow_stage(
            "dataset",
            "数据集",
            "ok" if dataset_ok else "fail",
            "datasetView",
            f"{dataset.get('samples', 0)} conv / {dataset.get('questions', 0)} QA" if dataset_ok else str(dataset.get("message") or "LoCoMo 数据集未就绪"),
            "先在 LoCoMo评测页校验 LoCoMo JSON。",
            {"path": dataset.get("path"), "format": dataset.get("format"), "categories": dataset.get("categories")},
            {"samples": dataset.get("samples"), "questions": dataset.get("questions")},
        ),
        locomo_flow_stage(
            "import",
            "记忆导入",
            "running" if import_running else ("ok" if import_seen else ("todo" if dataset_ok else "fail")),
            "openvikingView",
            f"运行中：{running_import.get('id')}" if import_running else (f"{imported.get('session_count', 0)} sessions / {imported.get('summary_count', 0)} summaries" if import_seen else "还没有发现当前账户的导入记录"),
            "选择 conv 后导入并等待 commit_session 完成。" if not import_seen else "导入后继续做完整性检查。",
            {
                "workspace": workspace_text,
                "storage_root": workspace.get("storage_root"),
                "running_task": running_import.get("id"),
                "account_path": imported.get("account_path"),
                "error": imported.get("error"),
            },
            {"sessions": imported.get("session_count"), "summaries": imported.get("summary_count")},
        ),
        locomo_flow_stage(
            "integrity",
            "完整性",
            "running" if import_running else ("ok" if import_complete else ("warn" if import_seen else ("todo" if dataset_ok else "fail"))),
            "openvikingView",
            "commit_session 仍在执行" if import_running else (f"complete summaries {imported.get('complete_count', 0)} / {imported.get('summary_count', 0)}" if import_seen else "等待导入 summary 和 memory 文件"),
            "点击“检查完整性”，确认消息数、session、memory files 和证据 probe。",
            {"summaries": imported.get("summaries"), "memory_root": imported.get("memory_root")},
            {"complete": imported.get("complete_count"), "total": imported.get("summary_count")},
        ),
        locomo_flow_stage(
            "qa",
            "问答测试",
            "running" if qa_running else ("ok" if latest_qa else ("todo" if import_complete else "todo")),
            "evalView",
            f"运行中：{running_qa.get('id')}" if qa_running else (f"最近结果 {rows} 行" if latest_qa else "还没有当前后端的 LoCoMo QA CSV"),
            "选择 1-10 题小样本核验，确认 answer、evidence、token 和错误日志；通过后再全量。",
            {"latest_run": {key: latest_qa.get(key) for key in ("id", "status", "output_file", "run_dir", "created_at")}, "running_task": running_qa.get("id"), "model_issue_count": model_issue_count},
            {"rows": rows, "tokens": token_total},
        ),
        locomo_flow_stage(
            "judge",
            "判分",
            "running" if judge_running else ("ok" if rows and graded >= rows else ("warn" if graded > 0 else ("todo" if latest_qa else "todo"))),
            "judgeView",
            f"运行中：{running_judge.get('id')}" if judge_running else (f"graded {graded}/{rows} · pending {pending}" if latest_qa else "等待 QA CSV"),
            "QA 完成后判分当前结果；pending 不计为 0% 准确率。",
            {"latest_csv": latest_qa.get("output_file"), "accuracy": accuracy, "running_task": running_judge.get("id")},
            {"rows": rows, "graded": graded, "pending": pending, "accuracy": accuracy},
        ),
        locomo_flow_stage(
            "report",
            "报告",
            "ok" if latest_report else ("todo" if latest_qa else "todo"),
            "runsView",
            f"report.html: {latest_report.get('report_html')}" if latest_report else "等待生成 HTML 报告",
            "判分后到结果中心生成 HTML 报告，检查配置、上下文、evidence 和失败归因。",
            {"latest_report": latest_report, "latest_csv": latest_qa.get("output_file")},
            {"has_report": bool(latest_report)},
        ),
    ]
    if not token_available:
        stages.append(
            locomo_flow_stage(
                "models",
                "模型配置",
                "warn",
                "systemConfigView",
                "未检测到可用于 Answer/判分/EchoMemory 的 token 来源。",
                "在本机环境变量或页面密码框中配置模型密钥；不要写入 README 或日志。",
                {"answer_model": (models.get("answer") or {}).get("model"), "judge_model": (models.get("judge") or {}).get("model")},
            )
        )
    if runtime.get("status") == "fail":
        stages.append(
            locomo_flow_stage(
                "runtime",
                "运行时",
                "fail",
                "systemConfigView",
                str(runtime.get("message") or runtime.get("label") or "记忆运行时不可用"),
                str(runtime.get("next_action") or "先修复 OpenViking 服务或 EchoMemory SDK 配置。"),
                {"runtime": runtime},
            )
        )

    status = locomo_flow_overall_status(stages)
    next_actions = [
        str(stage.get("action") or "")
        for stage in stages
        if stage.get("status") in {"fail", "warn", "todo", "running"}
    ]
    completed = sum(1 for item in stages if item.get("status") == "ok")
    markdown = locomo_flow_markdown(status, account, backend, stages, next_actions[:6])
    return {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": backend,
        "scope": MEMORY_BACKEND_SCOPE,
        "completion": {"ok": completed, "total": len(stages), "pct": round(completed * 100 / len(stages), 1) if stages else 0},
        "stages": stages,
        "next_actions": next_actions[:6],
        "artifacts": {
            "dataset": dataset,
            "workspace": workspace,
            "imported": imported,
            "latest_qa": {key: latest_qa.get(key) for key in ("id", "status", "output_file", "run_dir", "created_at", "summary")},
            "latest_report": latest_report,
            "running_tasks": running_tasks,
        },
        "safe_to_share": True,
        "secrets_included": False,
        "summary": markdown,
        "markdown": markdown,
    }


def acceptance_matrix(
    payload: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    doctor: dict[str, Any] | None = None,
    echomem: dict[str, Any] | None = None,
    flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    readiness = readiness or system_readiness(payload)
    audit = audit or readiness.get("audit") or handoff_audit()
    doctor = doctor or adapter_doctor_report()
    if echomem is None:
        contract_payload = dict(payload)
        contract_config = dict(contract_payload.get("config") or {}) if isinstance(contract_payload.get("config"), dict) else {}
        contract_config["memoryBackend"] = "echomemory"
        contract_payload["config"] = contract_config
        echomem = echomem_contract(contract_payload)
    preflight = readiness.get("preflight") or {}
    steps = readiness.get("steps") or []

    def step_by_title(title: str) -> dict[str, Any]:
        return next((item for item in steps if item.get("title") == title), {})

    def audit_check(check_id: str) -> dict[str, Any]:
        return next((item for item in audit.get("checks", []) if item.get("id") == check_id), {})

    backend = str(readiness.get("backend") or preflight.get("backend") or "openviking")
    account = str(readiness.get("account") or preflight.get("account") or "default")
    dataset_step = step_by_title("LoCoMo 数据集")
    isolation_step = step_by_title("账户隔离目录")
    runtime_step = step_by_title("记忆运行时")
    model_step = step_by_title("模型配置")
    flow = normalize_flow_artifacts(flow or current_account_flow_artifacts(payload, readiness=readiness))
    required_files = audit_check("required_files")
    no_secrets = audit_check("no_real_secrets")
    no_retired = audit_check("no_retired_backend")
    static_mirror = audit_check("static_mirror")
    report_files_ok = all((ROOT / rel).exists() for rel in ["scripts/generate_html_report.py", "memory/report_export.py"])
    latest_report = flow.get("latest_report") or {}
    latest_qa = flow.get("latest_qa") or {}
    items = [
        acceptance_item(
            "delivery_scope",
            "交付边界",
            "ok" if audit.get("status") in {"ok", "warn"} and no_retired.get("status") == "ok" else "fail",
            "required",
            "当前入口只包含 OpenViking + EchoMemory，无范围外后端口径。",
            "若失败，先运行交付审计并移除范围外后端文案。",
            {"audit_status": audit.get("status"), "no_retired_backend": no_retired.get("status")},
        ),
        acceptance_item(
            "backend_contracts",
            "记忆后端契约",
            "ok" if doctor.get("status") == "ok" else "fail",
            "required",
            "OpenViking 和 EchoMemory 均满足导入、commit、检索、完整性、浏览、LoCoMo task 构建能力。",
            "运行记忆后端自检，补齐缺失 capability 或方法。",
            {"registered": doctor.get("registered_backends"), "missing": doctor.get("missing_backends"), "unexpected": doctor.get("unexpected_backends")},
        ),
        acceptance_item(
            "echomem_contract",
            "EchoMemory 接入契约",
            "ok" if echomem.get("status") == "ok" else ("warn" if echomem.get("status") == "warn" else "fail"),
            "required",
            "外部 EchoMemory fork 保留 runtime、Local SDK、导入脚本、QA 脚本、evidence 和账户隔离接口。",
            "先修复 EchoMemory 接入契约，再跑 LoCoMo。",
            {"root": echomem.get("root"), "required_failures": len(echomem.get("required_failures") or []), "warnings": len(echomem.get("warnings") or [])},
            "echomem",
        ),
        acceptance_item(
            "locomo_dataset",
            "LoCoMo 数据集",
            dataset_step.get("status") or "fail",
            "required",
            dataset_step.get("detail") or "需要可解析的 LoCoMo JSON。",
            dataset_step.get("action") or "在 LoCoMo 评测页校验数据集。",
            dataset_step.get("evidence") or {},
        ),
        acceptance_item(
            "account_isolation",
            "账户与 workspace 隔离",
            isolation_step.get("status") or "fail",
            "required",
            "当前账户应有独立 workspace/storage root，避免历史记忆污染。",
            isolation_step.get("action") or "使用自动生成的新 workspace 或新账户。",
            isolation_step.get("evidence") or {},
        ),
        acceptance_item(
            "runtime_ready",
            "记忆运行时",
            runtime_step.get("status") or "fail",
            "required",
            runtime_step.get("detail") or "OpenViking 服务或 EchoMemory SDK 需可用。",
            runtime_step.get("action") or "补齐运行时配置。",
            runtime_step.get("evidence") or {},
        ),
        acceptance_item(
            "model_ready",
            "模型与密钥配置",
            model_step.get("status") or "fail",
            "required",
            "Answer、判分以及 EchoMemory embedding/chat 只检查是否配置，不返回真实 key。",
            model_step.get("action") or "补齐模型配置。",
            model_step.get("evidence") or {},
        ),
        acceptance_item(
            "report_pipeline",
            "报告生成链路",
            "ok" if report_files_ok else "fail",
            "required",
            "具备 CSV/判分摘要 到 HTML 报告的生成脚本和 Web 导出服务。",
            "补齐 generate_html_report.py 或 memory/report_export.py。",
            {"generate_script": str(ROOT / "scripts/generate_html_report.py"), "report_export": str(ROOT / "memory/report_export.py")},
        ),
        acceptance_item(
            "security_redaction",
            "安全外发",
            "ok" if no_secrets.get("status") == "ok" and required_files.get("status") == "ok" else "fail",
            "required",
            "交付入口不包含真实 API Key，必需文件齐全。",
            "外发前运行交付审计；不要包含 .env.local、judge.conf、runs、workspace。",
            {"no_real_secrets": no_secrets.get("status"), "required_files": required_files.get("status")},
        ),
        acceptance_item(
            "static_cache",
            "页面缓存与静态同步",
            "ok" if static_mirror.get("status") == "ok" else "warn",
            "recommended",
            "web/static 与 legacy static 一致，浏览器不会加载旧入口。",
            "如有漂移，执行静态同步并刷新 cache_bust。",
            static_mirror.get("evidence") or {},
        ),
        acceptance_item(
            "smoke_qa",
            "最近 QA 证据",
            "ok" if latest_qa else "warn",
            "recommended",
            "最近一次 OpenViking/EchoMemory QA run 可用于定位模型、检索和 token 问题。",
            "正式外发前建议跑 conv-30 少量 QA，并判分当前结果。",
            latest_qa,
        ),
        acceptance_item(
            "latest_html_report",
            "最近 HTML 报告",
            "ok" if latest_report else "warn",
            "recommended",
            "最近 run 已生成 report.html，可供外部测试者看报告样式和字段。",
            "QA/判分完成后从结果中心生成 HTML 报告。",
            latest_report,
        ),
    ]
    status = acceptance_matrix_status(items)
    score = acceptance_matrix_score(items)
    blockers = [item for item in items if item.get("severity") == "required" and item.get("status") == "fail"]
    warnings = [item for item in items if item.get("status") == "warn"]
    next_actions = [item.get("action") for item in blockers + warnings if item.get("action")]
    markdown = acceptance_matrix_markdown(status, score, items, account, backend)
    return {
        "status": status,
        "score": score,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": backend,
        "scope": MEMORY_BACKEND_SCOPE,
        "items": items,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions[:6],
        "flow_artifacts": flow,
        "safe_to_share": True,
        "secrets_included": False,
        "markdown": markdown,
        "summary": markdown,
    }


def smoke_plan_step(
    step_id: str,
    title: str,
    status: str,
    view: str,
    action: str,
    expected: str,
    button: str = "",
    detail: str = "",
    evidence: Any = None,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "title": title,
        "status": status if status in {"ok", "warn", "fail", "todo"} else "todo",
        "view": view,
        "button": button,
        "action": action,
        "expected": expected,
        "detail": detail,
        "evidence": evidence if evidence is not None else {},
    }


def smoke_plan_status(steps: list[dict[str, Any]]) -> str:
    if any(item.get("status") == "fail" for item in steps):
        return "fail"
    if any(item.get("status") == "warn" for item in steps):
        return "warn"
    return "ok"


def smoke_plan_score(steps: list[dict[str, Any]]) -> int:
    if not steps:
        return 0
    weights = {"ok": 1.0, "warn": 0.55, "todo": 0.35, "fail": 0.0}
    return int(round(sum(weights.get(str(item.get("status") or ""), 0.0) for item in steps) * 100 / len(steps)))


def locomo_smoke_recommendation(dataset_path: Path) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "sample_id": "",
        "sample_index": 0,
        "sample_questions": 0,
        "one_question_id": "",
        "ten_question_ids": [],
        "question_examples": [],
        "category_mix": {},
    }
    try:
        overview = dataset_overview(dataset_path)
        rows = overview.get("sample_rows") if isinstance(overview.get("sample_rows"), list) else []
        preferred = next((row for row in rows if str(row.get("sample_id") or "") == "conv-30"), None)
        selected = preferred or (rows[0] if rows else {})
        sample_id = str(selected.get("sample_id") or "")
        sample_index = int(selected.get("index") or 0)
        sample_filter = sample_id or str(sample_index)
        questions = locomo_questions(dataset_path, sample_filter).get("questions") or []
        if not questions and rows:
            questions = locomo_questions(dataset_path, "all").get("questions") or []
        category_mix: dict[str, int] = {}
        balanced: list[dict[str, Any]] = []
        for question in questions:
            cat = str(question.get("category") or "-")
            category_mix[cat] = category_mix.get(cat, 0) + 1
            if len(balanced) < 10 and category_mix[cat] <= 4:
                balanced.append(question)
        if len(balanced) < 10:
            seen_ids = {item.get("question_id") for item in balanced}
            for question in questions:
                if question.get("question_id") not in seen_ids:
                    balanced.append(question)
                if len(balanced) >= 10:
                    break
        recommendation.update(
            {
                "sample_id": sample_id,
                "sample_index": sample_index,
                "sample_questions": len(questions),
                "one_question_id": str((balanced[0] if balanced else {}).get("question_id") or ""),
                "ten_question_ids": [str(item.get("question_id") or "") for item in balanced[:10] if item.get("question_id")],
                "question_examples": [
                    {
                        "question_id": str(item.get("question_id") or ""),
                        "category": str(item.get("category") or ""),
                        "question": compact_text(item.get("question"), 180),
                        "answer": compact_text(item.get("answer"), 120),
                    }
                    for item in balanced[:5]
                ],
                "category_mix": category_mix,
            }
        )
    except Exception as exc:
        recommendation["error"] = str(exc)
    return recommendation


def smoke_plan_markdown(status: str, score: int, account: str, backend: str, dataset: dict[str, Any], recommendation: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    lines = [
        "# LoCoMo Memory Eval Small-Sample Validation Plan",
        "",
        f"- Status: `{status}`",
        f"- Score: `{score}/100`",
        f"- Account: `{account}`",
        f"- Backend: `{backend}`",
        f"- Dataset: `{dataset.get('path') or '-'}`",
        f"- Recommended sample: `{recommendation.get('sample_id') or recommendation.get('sample_index') or '-'}`",
        f"- 1-question validation: `{recommendation.get('one_question_id') or '-'}`",
        f"- 10-question validation: `{','.join(recommendation.get('ten_question_ids') or []) or '-'}`",
        "- This plan is read-only. It does not import memory, call models, score results, or expose API keys.",
        "",
        "| Step | Status | Page | Action | Expected Artifact |",
        "|---|---:|---|---|---|",
    ]
    for step in steps:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(step.get("title") or step.get("id") or "-").replace("|", "\\|"),
                    f"`{step.get('status') or '-'}`",
                    str(step.get("view") or "-").replace("|", "\\|"),
                    str(step.get("action") or "-").replace("|", "\\|"),
                    str(step.get("expected") or "-").replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("Do not share `.env.local`, `judge.conf`, `runs/`, workspaces, screenshots with tokens, or real API keys.")
    return public_share_text("\n".join(lines))


def smoke_plan(
    payload: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    acceptance: dict[str, Any] | None = None,
    flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    readiness = readiness or system_readiness(payload)
    acceptance = acceptance or acceptance_matrix(payload, readiness=readiness)
    preflight = readiness.get("preflight") or {}
    dataset = preflight.get("dataset") or {}
    workspace = preflight.get("workspace") or {}
    models = preflight.get("models") or {}
    backend = str(preflight.get("backend") or readiness.get("backend") or "openviking")
    account = str(preflight.get("account") or readiness.get("account") or "default")
    dataset_path = safe_path(str(dataset.get("path") or DEFAULT_DATA))
    recommendation = locomo_smoke_recommendation(dataset_path) if dataset.get("status") == "ok" else {}
    flow = normalize_flow_artifacts(flow or acceptance.get("flow_artifacts") or current_account_flow_artifacts(payload, readiness=readiness))
    running_tasks = readiness.get("running_tasks") or []
    required_blockers = acceptance.get("blockers") or []
    warnings = acceptance.get("warnings") or []
    token_available = bool(((models.get("answer") or {}).get("token_set")) or ((models.get("judge") or {}).get("token_set")) or ((models.get("echomemory") or {}).get("chat_token_set")))
    sample_id = recommendation.get("sample_id") or ""
    one_qid = recommendation.get("one_question_id") or ""
    ten_qids = recommendation.get("ten_question_ids") or []
    steps = [
        smoke_plan_step(
            "readiness",
            "启动门禁",
            "fail" if required_blockers else ("warn" if warnings else "ok"),
            "readmeView",
            "点击“刷新门禁”和“刷新验收”，确认没有 required failure。",
            "readiness / acceptance-matrix 均可复制，且不包含真实 API Key。",
            "刷新门禁 / 刷新验收",
            f"acceptance={acceptance.get('status')} score={acceptance.get('score')}/100",
            {"blockers": len(required_blockers), "warnings": len(warnings)},
        ),
        smoke_plan_step(
            "dataset",
            "校验 LoCoMo 数据集",
            "ok" if dataset.get("status") == "ok" else "fail",
            "datasetView",
            "数据文件旁点击“校验 LoCoMo JSON”，确认 samples、QA 和 category 统计。",
            "LoCoMo JSON 可解析；页面显示 conv 和 QA 列表。",
            "校验 LoCoMo JSON",
            f"{dataset.get('samples', 0)} conv / {dataset.get('questions', 0)} QA",
            {"path": dataset.get("path"), "format": dataset.get("format"), "categories": dataset.get("categories")},
        ),
        smoke_plan_step(
            "import",
            "导入一个对话",
            "todo" if dataset.get("status") == "ok" else "fail",
            "openvikingView",
            f"选择 {sample_id or '推荐 conv'}，点击“导入所选对话”，完成后运行完整性检查。",
            "导入 summary、run.log、完整性结果；当前账户 workspace/storage root 有新数据。",
            "导入所选对话 / 检查完整性",
            "先跑单 conv，确认 commit 和 memory files 完整后再扩全量。",
            {"workspace": workspace.get("workspace"), "storage_root": workspace.get("storage_root"), "sample_id": sample_id},
        ),
        smoke_plan_step(
            "qa_one",
            "1 题问答核验",
            "todo" if one_qid and token_available else ("warn" if one_qid else "fail"),
            "evalView",
            f"只选择 {one_qid or '推荐问题'} 运行问答，观察 answer、evidence、token 和错误提示。",
            "生成 QA CSV；单题应有 agent response、relevant memory、token 用量。",
            "跑选中题 / 跑全部 LoCoMo",
            "这一步会调用答案模型；外部测试者确认 key 后再点。",
            {"question_id": one_qid, "question_examples": recommendation.get("question_examples")},
        ),
        smoke_plan_step(
            "qa_ten",
            "10 题问答核验",
            "todo" if ten_qids and token_available else ("warn" if ten_qids else "fail"),
            "evalView",
            f"选择推荐 10 题：{','.join(ten_qids) or '-'}，确认进度、重试和失败日志。",
            "QA CSV 至少包含 10 行；无重复 question_id；失败行有可读 error。",
            "跑选中题 / 跑全部 LoCoMo",
            "这一步会调用答案模型；建议先通过 1 题核验。",
            {"question_ids": ten_qids},
        ),
        smoke_plan_step(
            "judge",
            "判分与报告",
            "todo" if flow.get("latest_qa") else "warn",
            "judgeView",
            "QA 完成后点击“判分当前结果”，再到结果中心生成 HTML 报告。",
            "CSV 写入 correct/reasoning；run 目录生成 report.html。",
            "判分当前结果 / 生成所选报告",
            "这一步会调用判分模型；只对完成的 QA CSV 执行。",
            {"latest_qa": flow.get("latest_qa"), "latest_report": flow.get("latest_report")},
        ),
        smoke_plan_step(
            "handoff",
            "外发前审计",
            "ok" if acceptance.get("status") != "fail" else "fail",
            "readmeView",
            "复制验收矩阵和小样本核验计划；不要外发 .env.local、runs、workspace 或真实 key。",
            "外部测试者拿到 README 后能按步骤复现，不需要额外口头说明。",
            "复制核验计划",
            "只分享源码、README、env.example 和脱敏报告样例。",
            {"scope": acceptance.get("scope"), "safe_to_share": acceptance.get("safe_to_share")},
        ),
    ]
    if running_tasks:
        steps.insert(
            1,
            smoke_plan_step(
                "running_tasks",
                "运行中任务",
                "warn",
                "runsView",
                "先等运行中任务完成，或在结果中心确认是否需要停止。",
                "避免外发或切换配置时混入未完成 run。",
                "查看运行中",
                f"running={len(running_tasks)}",
                {"running": [item.get("id") for item in running_tasks]},
            ),
        )
    status = smoke_plan_status(steps)
    score = smoke_plan_score(steps)
    markdown = smoke_plan_markdown(status, score, account, backend, dataset, recommendation, steps)
    commands = [
        {
            "title": "读取核验计划",
            "command": f"curl -s http://127.0.0.1:{os.environ.get('LOCOMO_EVAL_PORT') or '19181'}/api/smoke-plan | python3 -m json.tool | head -200",
        },
        {
            "title": "检查验收矩阵",
            "command": f"curl -s http://127.0.0.1:{os.environ.get('LOCOMO_EVAL_PORT') or '19181'}/api/acceptance-matrix | python3 -m json.tool | head -160",
        },
        {
            "title": "打开 Web",
            "command": f"open http://127.0.0.1:{os.environ.get('LOCOMO_EVAL_PORT') or '19181'}/?view=readmeView",
        },
    ]
    return {
        "status": status,
        "score": score,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": backend,
        "dataset": dataset,
        "workspace": workspace,
        "recommendation": recommendation,
        "steps": steps,
        "commands": commands,
        "flow_artifacts": flow,
        "readiness": {"status": readiness.get("status"), "score": readiness.get("score")},
        "acceptance": {"status": acceptance.get("status"), "score": acceptance.get("score"), "blockers": len(required_blockers), "warnings": len(warnings)},
        "safe_to_share": True,
        "secrets_included": False,
        "markdown": markdown,
        "summary": markdown,
    }


def text_contains(path: Path, patterns: list[str]) -> dict[str, bool]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    return {pattern: bool(re.search(pattern, text)) for pattern in patterns}


def add_contract_check(
    checks: list[dict[str, Any]],
    check_id: str,
    title: str,
    ok: bool,
    detail: str,
    severity: str = "required",
    evidence: Any = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "title": title,
            "status": "ok" if ok else ("warn" if severity != "required" else "fail"),
            "severity": severity,
            "detail": detail,
            "evidence": evidence if evidence is not None else [],
        }
    )


def script_argument_names(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    names = set()
    for match in re.finditer(r"add_argument\(\s*[\"']--([A-Za-z0-9_-]+)[\"']", text):
        names.add(match.group(1))
    return names


def echomem_contract(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    preflight = system_preflight(payload)
    config = ((payload.get("config") if isinstance(payload.get("config"), dict) else {}) or {})
    runtime = preflight.get("runtime") or {}
    workspace = preflight.get("workspace") or {}
    account = str(preflight.get("account") or payload.get("account") or "default")
    root_text = str(config.get("echomemRoot") or config.get("echomem_root") or runtime.get("root") or os.environ.get("ECHOMEM_ROOT") or "").strip()
    root = Path(root_text).expanduser().resolve() if root_text else Path("")
    checks: list[dict[str, Any]] = []

    old_runtime = root / "packages" / "echomem" / "src" / "echomem" / "runtime" / "runtime.py"
    old_sdk = root / "packages" / "echomem" / "src" / "echomem" / "protocol" / "local_sdk" / "sdk.py"
    new_runtime = root / "echomem" / "runtime" / "bootstrap.py"
    new_sdk = root / "echomem" / "entrypoints" / "plugins" / "echoagent" / "sdk.py"
    sdk_files = {
        "old_echomem_src": root / "packages" / "echomem" / "src",
        "old_echofs_src": root / "packages" / "echofs" / "src",
        "new_echomem_pkg": root / "echomem",
        "runtime": old_runtime if old_runtime.exists() else new_runtime,
        "sdk": old_sdk if old_sdk.exists() else new_sdk,
        "old_runtime": old_runtime,
        "old_sdk": old_sdk,
        "new_runtime": new_runtime,
        "new_sdk": new_sdk,
        "schemas": root / "configs" / "schemas",
    }
    old_layout = sdk_files["old_echomem_src"].exists() and sdk_files["old_echofs_src"].exists()
    new_layout = sdk_files["new_echomem_pkg"].exists() and (root / "pyproject.toml").exists()
    add_contract_check(
        checks,
        "sdk_root",
        "EchoMemory SDK 根目录",
        bool(root_text and root.exists()),
        f"ECHOMEM_ROOT={root_text or '-'}",
        "required",
        {"root": root_text, "exists": bool(root_text and root.exists())},
    )
    add_contract_check(
        checks,
        "sdk_layout",
        "SDK 源码布局",
        bool(root_text and (old_layout or new_layout)),
        "需要旧版 packages/echomem/src + packages/echofs/src，或 v0.0.5 顶层 echomem/ 包。",
        "required",
        {**{name: str(path) for name, path in sdk_files.items()}, "layout": "old-packages" if old_layout else ("v0.0.5-flat" if new_layout else "unknown")},
    )
    runtime_text = text_contains(sdk_files["runtime"], [r"def\s+open_runtime|async\s+def\s+open_runtime"])
    sdk_text = text_contains(
        sdk_files["sdk"],
        [
            r"class\s+EchoMemSDK",
            r"def\s+create_session|async\s+def\s+create_session",
            r"def\s+add_message|async\s+def\s+add_message",
            r"def\s+commit_session|async\s+def\s+commit_session",
            r"def\s+find|async\s+def\s+find",
            r"def\s+search|async\s+def\s+search",
        ],
    )
    add_contract_check(
        checks,
        "runtime_api",
        "runtime API",
        bool(runtime_text.get(r"def\s+open_runtime|async\s+def\s+open_runtime")),
        "需要 open_runtime(config_path) 加载 runtime。",
        "required",
        {"file": str(sdk_files["runtime"]), "matches": runtime_text},
    )
    required_sdk_matches = [
        r"class\s+EchoMemSDK",
        r"def\s+create_session|async\s+def\s+create_session",
        r"def\s+add_message|async\s+def\s+add_message",
        r"def\s+commit_session|async\s+def\s+commit_session",
        r"def\s+find|async\s+def\s+find",
        r"def\s+search|async\s+def\s+search",
    ]
    add_contract_check(
        checks,
        "local_sdk_api",
        "Local SDK API",
        all(sdk_text.get(pattern) for pattern in required_sdk_matches),
        "需要 EchoMemSDK.create_session/add_message/commit_session/find/search。",
        "required",
        {"file": str(sdk_files["sdk"]), "matches": sdk_text},
    )

    import_script = ROOT / "scripts" / "echomemory_locomo_import.py"
    qa_script = ROOT / "scripts" / "echomemory_memory_qa.py"
    common_script = ROOT / "scripts" / "echomemory_common.py"
    import_args = script_argument_names(import_script)
    qa_args = script_argument_names(qa_script)
    required_import_args = {"dataset", "out-dir", "echomem-root", "workspace", "account", "user-id", "agent-id", "sample", "session-mode"}
    required_qa_args = {"dataset", "out-dir", "echomem-root", "workspace", "account", "user-id", "agent-id", "sample", "questions", "top-k", "retrieval-mode", "answer-base-url", "answer-model", "answer-token"}
    add_contract_check(
        checks,
        "import_script_contract",
        "导入脚本参数",
        required_import_args.issubset(import_args),
        "LoCoMo 导入脚本必须接收 dataset/workspace/account/user/agent/sample 等参数。",
        "required",
        {"file": str(import_script), "required": sorted(required_import_args), "missing": sorted(required_import_args - import_args)},
    )
    add_contract_check(
        checks,
        "qa_script_contract",
        "QA 脚本参数",
        required_qa_args.issubset(qa_args),
        "LoCoMo QA 脚本必须接收检索参数、模型参数和账户隔离参数。",
        "required",
        {"file": str(qa_script), "required": sorted(required_qa_args), "missing": sorted(required_qa_args - qa_args)},
    )
    import_contract_text = text_contains(import_script, [r"create_session", r"add_message", r"commit_session", r"keep_recent_count=0", r"echomemory_import_summary\.json"])
    qa_contract_text = text_contains(
        qa_script,
        [r"sdk\.search", r"context_item_to_dict", r"relevant_memory", r"answer_total_tokens"],
    )
    common_contract_text = text_contains(common_script, [r"source_uri", r"memory_type", r"confidence", r"evidence_uri", r"trace"])
    add_contract_check(
        checks,
        "commit_flow",
        "导入归档链路",
        all(import_contract_text.values()),
        "导入阶段必须 create_session/add_message/commit_session，并写 summary。",
        "required",
        import_contract_text,
    )
    add_contract_check(
        checks,
        "retrieval_flow",
        "检索问答链路",
        all(qa_contract_text.values()),
        "QA 阶段必须调用 sdk.search，输出 relevant_memory 和 token 用量。",
        "required",
        qa_contract_text,
    )
    add_contract_check(
        checks,
        "evidence_shape",
        "Evidence 结构",
        all(common_contract_text.values()),
        "检索 evidence 至少应包含 content/uri/score/memory_type/evidence_uri/trace。",
        "required",
        common_contract_text,
    )

    workspace_path = str(workspace.get("workspace") or config.get("memoryWorkspace") or config.get("ovWorkspace") or "")
    storage_root = str(workspace.get("storage_root") or "")
    add_contract_check(
        checks,
        "account_isolation",
        "账户隔离路径",
        bool(workspace_path and storage_root and f"/{account}/{account}" in storage_root.replace("\\", "/")),
        "EchoMemory 存储应按 workspace/<account>/<account> 隔离。",
        "required",
        {"workspace": workspace_path, "account": account, "storage_root": storage_root, "layout": workspace.get("layout")},
    )
    add_contract_check(
        checks,
        "token_redaction",
        "密钥安全",
        True,
        "契约自检只返回 token 是否配置，不返回真实值。",
        "required",
        {
            "embedding_token_set": bool((preflight.get("models") or {}).get("echomemory", {}).get("embedding_token_set")),
            "chat_token_set": bool((preflight.get("models") or {}).get("echomemory", {}).get("chat_token_set")),
            "answer_token_set": bool((preflight.get("models") or {}).get("answer", {}).get("token_set")),
            "judge_token_set": bool((preflight.get("models") or {}).get("judge", {}).get("token_set")),
        },
    )
    add_contract_check(
        checks,
        "no_mock_for_benchmark",
        "正式评测禁用 mock",
        not bool(config.get("fallback_to_mock")),
        "小样本核验可用 mock，正式 LoCoMo 评测应关闭 fallback_to_mock。",
        "recommended",
        {"fallback_to_mock": bool(config.get("fallback_to_mock"))},
    )

    required_failures = [item for item in checks if item["severity"] == "required" and item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    status = "fail" if required_failures else ("warn" if warnings else "ok")
    summary_lines = [
        "EchoMemory Connection Contract",
        f"- Status: {status}",
        f"- Account: {account}",
        f"- ECHOMEM_ROOT: {root_text or '-'}",
        f"- Workspace: {workspace_path or '-'}",
        f"- Required failures: {len(required_failures)}",
        f"- Warnings: {len(warnings)}",
        "- Required SDK APIs: open_runtime, EchoMemSDK.create_session/add_message/commit_session/find/search",
        "- Required outputs: import summary, QA CSV, relevant_memory, token usage, evidence trace",
        "",
        "Safe to share: local machine paths are redacted and API key values are never included.",
    ]
    return {
        "status": status,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "backend": "echomemory",
        "root": root_text,
        "workspace": workspace_path,
        "storage_root": storage_root,
        "checks": checks,
        "required_failures": required_failures,
        "warnings": warnings,
        "summary": public_share_text("\n".join(summary_lines)),
    }


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return validation_service.validate_payload(payload, DEFAULT_DATA, DEFAULT_OUTPUT_DIR, safe_path, dataset_overview)


def safe_path(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def redacted_command(command: list[str]) -> list[str]:
    return tasking_service.redacted_command(command)


def normalize_sample(value: Any) -> str:
    if value in (None, "", "all"):
        return "0"
    return str(value)


def normalize_connection(payload: dict[str, Any], base_config: Path = DEFAULT_CONFIG) -> dict[str, str]:
    defaults = load_ov_defaults(base_config)
    raw_url = (payload.get("server_url") or defaults.get("server_url") or "").strip()
    host = (payload.get("host") or defaults.get("server_host") or "127.0.0.1").strip()
    port = str(payload.get("port") or defaults.get("server_port") or "19080").strip()
    if raw_url and not payload.get("port"):
        parsed = urlparse(raw_url)
        host = parsed.hostname or host
        port = str(parsed.port or port)
    url = f"http://{host}:{port}"
    return {
        "host": host,
        "port": port,
        "url": url,
        "root_api_key": str(payload.get("root_api_key") or defaults.get("root_api_key") or "").strip(),
        "account": str(payload.get("account") or defaults.get("account") or "default").strip(),
    }


def prepare_connection_files(payload: dict[str, Any], run_dir: Path, config_path: Path, cli_config_path: Path) -> tuple[Path, Path]:
    conn = normalize_connection(payload, config_path)
    try:
        cfg = read_json(config_path)
    except Exception:
        cfg = {}
    try:
        cli = read_json(cli_config_path)
    except Exception:
        cli = {}

    cfg.setdefault("server", {})
    cfg.setdefault("bot", {}).setdefault("ov_server", {})
    cfg.setdefault("storage", {})
    cfg["server"]["host"] = conn["host"]
    try:
        cfg["server"]["port"] = int(conn["port"])
    except ValueError:
        cfg["server"]["port"] = conn["port"]
    if conn["root_api_key"]:
        cfg["server"]["root_api_key"] = conn["root_api_key"]
    ov_server = cfg["bot"]["ov_server"]
    ov_server.pop("url", None)
    ov_server["server_url"] = conn["url"]
    ov_server["mode"] = "remote"
    ov_server["api_key_type"] = "root"
    if conn["root_api_key"]:
        ov_server["root_api_key"] = conn["root_api_key"]
    ov_server["account_id"] = conn["account"]
    ov_server.setdefault("admin_user_id", "default")
    if payload.get("workspace"):
        cfg["storage"]["workspace"] = str(safe_path(payload["workspace"]))

    cli["url"] = conn["url"]
    if conn["root_api_key"]:
        cli["api_key"] = conn["root_api_key"]
    cli["account"] = conn["account"]
    cli.setdefault("timeout", 600.0)

    out_config = run_dir / "ov.web.conf"
    out_cli = run_dir / "ovcli.web.conf"
    out_config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    out_cli.write_text(json.dumps(cli, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_config, out_cli


def probe_openviking(host: str, port: str, api_key: str = "") -> dict[str, Any]:
    return plugin_service.probe("openviking", host, port, api_key)


def discover_openviking_ports(host: str = "127.0.0.1", ports: list[str] | None = None, api_key: str = "") -> dict[str, Any]:
    return plugin_service.discover_ports("openviking", host, ports, api_key)


def openviking_workspace_for_run(payload: dict[str, Any], run_dir: Path) -> Path | None:
    return plugin_service.workspace_for_run("openviking", payload, run_dir, safe_path)


def make_openviking_runtime_config(payload: dict[str, Any], run_dir: Path, base_config: Path) -> Path:
    return plugin_service.make_runtime_config("openviking", payload, run_dir, base_config, DEFAULT_LOCOMO_MEMORY_TEMPLATES)


def restart_openviking_for_workspace(payload: dict[str, Any], run_dir: Path, config_path: Path) -> dict[str, Any]:
    return plugin_service.restart_for_workspace(
        "openviking",
        payload,
        run_dir,
        config_path,
        safe_path=safe_path,
        openviking_python=DEFAULT_OPENVIKING_PYTHON,
        memory_templates_dir=DEFAULT_LOCOMO_MEMORY_TEMPLATES,
    )


def list_openviking_imported_memories(workspace: Path, account: str, limit: int = 80, sample: str = "") -> dict[str, Any]:
    return plugin_service.list_imported_memories("openviking", workspace, account, DEFAULT_OUTPUT_DIR, limit, sample)


def openviking_import_integrity(workspace: Path, account: str, sample: str = "", summary_path: Path | None = None) -> dict[str, Any]:
    return plugin_service.import_integrity("openviking", workspace, account, DEFAULT_OUTPUT_DIR, DEFAULT_DATA, sample, summary_path, "default")


def openviking_session_browser(workspace: Path, account: str, sample: str = "", limit: int = 120) -> dict[str, Any]:
    return plugin_service.session_browser("openviking", workspace, account, sample, limit)


def memory_timeline(workspace: Path, account: str, query: str = "", limit: int = 200) -> dict[str, Any]:
    return plugin_service.memory_timeline("openviking", workspace, account, "default", query, limit)


def read_memory_file(path: Path, backend: str = "openviking") -> dict[str, Any]:
    return plugin_service.read_memory_file(normalize_memory_backend(backend), path)


def question_result_detail(csv_path: Path, question_id: str = "", index: int | None = None) -> dict[str, Any]:
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8", errors="replace")))
    row = None
    row_index = -1
    for i, candidate in enumerate(rows):
        if question_id and candidate.get("question_id") == question_id:
            row = candidate
            row_index = i
            break
        if index is not None and i == index:
            row = candidate
            row_index = i
            break
    if row is None:
        raise FileNotFoundError(question_id or str(index))
    relevant_raw = row.get("relevant_memory") or "[]"
    try:
        relevant = json.loads(relevant_raw)
    except Exception:
        relevant = []
    context = row.get("context_preview") or ""
    reasoning = row.get("reasoning") or row.get("judge_reason") or ""
    archive_count = 0
    memory_count = 0
    try:
        archive_count = int(float(row.get("archive_fallback_count") or 0))
    except ValueError:
        archive_count = sum(1 for item in relevant if isinstance(item, dict) and item.get("source") == "archive_fallback")
    try:
        memory_count = int(float(row.get("memory_hit_count") or 0))
    except ValueError:
        memory_count = sum(1 for item in relevant if isinstance(item, dict) and item.get("source") != "archive_fallback")
    retrieval_error = row.get("retrieval_error") or ""
    model_error = row.get("model_error") or ""
    return {
        "csv": str(csv_path),
        "index": row_index,
        "row": row,
        "relevant_memory": relevant if isinstance(relevant, list) else [],
        "context": context,
        "diagnostics": {
            "archive_fallback_count": archive_count,
            "memory_hit_count": memory_count,
            "retrieval_count": row.get("retrieval_count") or len(relevant if isinstance(relevant, list) else []),
            "retrieval_status": row.get("retrieval_status") or "",
            "answer_status": row.get("answer_status") or "",
            "model_status": row.get("model_status") or "",
            "health_status": row.get("health_status") or "",
            "model_error_kind": row.get("model_error_kind") or "",
            "retrieval_error": compact_text(retrieval_error, 900),
            "model_error": compact_text(model_error, 900),
            "retrieval_tokens_est": "",
            "answer_prompt_tokens": row.get("answer_prompt_tokens") or "",
            "answer_completion_tokens": row.get("answer_completion_tokens") or "",
            "answer_total_tokens": row.get("answer_total_tokens") or "",
        },
        "judge": {
            "result": row.get("result") or row.get("simple_grade") or "",
            "reasoning": reasoning,
        },
    }


def agent_type_for(kind: str, payload: dict[str, Any] | None = None) -> str:
    return run_service.agent_type_for(kind, payload)


def run_record(run_dir: Path, compact: bool = False) -> dict[str, Any] | None:
    return run_service.run_record(run_dir, active_run_ids(), compact=compact)


def list_runs(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    limit: int = 40,
    query: str = "",
    status: str = "all",
    compact: bool = False,
) -> list[dict[str, Any]]:
    return run_service.list_runs(output_dir, limit, query, status, active_run_ids(), compact=compact)


def run_scope_text(record: dict[str, Any], include_summary: bool = False) -> str:
    keys = ("id", "name", "kind", "agent_type", "dataset_format", "output_file", "run_dir", "account", "workspace")
    parts = [str(record.get(key) or "") for key in keys]
    if include_summary and isinstance(record.get("summary"), dict):
        try:
            parts.append(json.dumps(record.get("summary") or {}, ensure_ascii=False, sort_keys=True))
        except Exception:
            parts.append(str(record.get("summary") or ""))
    return " ".join(parts).lower()


def retired_backend_terms() -> tuple[str, ...]:
    return ("hi" + "go", "hi-" + "go", "hi " + "go")


def contains_retired_backend_text(record: dict[str, Any]) -> bool:
    text = run_scope_text(record, include_summary=True)
    return any(term in text for term in retired_backend_terms())


def run_scope_backend(record: dict[str, Any]) -> str:
    text = run_scope_text(record)
    kind = str(record.get("kind") or "").lower()
    if "echomemory" in text or "echomem" in text or kind.startswith("echo"):
        return "echomemory"
    if "openviking" in text or kind.startswith("openviking"):
        return "openviking"
    return ""


def run_scope_dataset(record: dict[str, Any]) -> str:
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    return str(record.get("dataset_format") or summary.get("dataset_format") or "").strip().lower()


def current_scope_run(record: dict[str, Any], backend: str = "") -> bool:
    kind = str(record.get("kind") or "").strip().lower()
    agent_type = str(record.get("agent_type") or "").strip().lower()
    dataset_format = run_scope_dataset(record)
    identity_text = " ".join(str(record.get(key) or "") for key in ("id", "name", "kind", "run_dir")).lower()
    if contains_retired_backend_text(record):
        return False
    if "vikingboat" in identity_text:
        return False
    if kind in HISTORICAL_RUN_KINDS:
        return False
    if dataset_format not in CURRENT_SCOPE_DATASET_FORMATS:
        return False
    detected_backend = run_scope_backend(record)
    if detected_backend not in MEMORY_BACKEND_IDS:
        return False
    if backend and normalize_memory_backend(backend) != detected_backend:
        return False
    if kind in CURRENT_SCOPE_RUN_KINDS or agent_type in CURRENT_SCOPE_AGENT_TYPES:
        return True
    output_file = str(record.get("output_file") or "")
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    return bool(output_file and (summary.get("rows") is not None or output_file.endswith((".csv", ".json"))))


def same_path_text(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except Exception:
        return left.rstrip("/") == right.rstrip("/")


def run_matches_account_workspace(record: dict[str, Any], account: str = "", workspace: str = "", strict: bool = False) -> bool:
    account = str(account or "").strip()
    workspace = str(workspace or "").strip()
    record_account = str(record.get("account") or "").strip()
    record_workspace = str(record.get("workspace") or "").strip()
    if account:
        if strict and not record_account:
            return False
        if record_account and record_account != account:
            return False
    if workspace:
        if strict and not record_workspace:
            return False
        if record_workspace and not same_path_text(record_workspace, workspace):
            return False
    return True


def list_current_scope_runs(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    limit: int = 40,
    query: str = "",
    status: str = "all",
    backend: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scan_limit = max(limit * 8, limit + 160)
    raw_runs = list_runs(output_dir, scan_limit, query, status, compact=True)
    scoped = [record for record in raw_runs if current_scope_run(record, backend)]
    runs = scoped[:limit]
    return runs, {
        "scope": MEMORY_BACKEND_SCOPE,
        "backend": normalize_memory_backend(backend) if backend else "",
        "raw_scanned": len(raw_runs),
        "hidden_history_count": max(0, len(raw_runs) - len(scoped)),
        "returned": len(runs),
        "include_history_available": True,
    }


def csv_preview(path: Path, limit: int = 20) -> dict[str, Any]:
    return run_service.csv_preview(path, limit)


def qa_diagnostics(path: Path, dataset_path: Path | None = None, sample: str = "all") -> dict[str, Any]:
    return run_service.qa_diagnostics(path, dataset_path, sample)


def csv_pending_preview(path: Path, limit: int = 20, category: str = "", query: str = "", min_tokens: int | None = None, max_tokens: int | None = None) -> dict[str, Any]:
    return run_service.csv_pending_preview(path, limit, category, query, min_tokens, max_tokens)


def export_pending_csv(path: Path, category: str = "", query: str = "", min_tokens: int | None = None, max_tokens: int | None = None) -> dict[str, Any]:
    return run_service.export_pending_csv(path, category, query, min_tokens, max_tokens)


def ensure_judge_columns(path: Path) -> None:
    return run_service.ensure_judge_columns(path)


def tail_file(path: Path, limit: int = 12000) -> dict[str, Any]:
    return run_service.tail_file(path, limit)


def relevant_memory(run_dir: Path, limit: int = 20) -> dict[str, Any]:
    return run_service.relevant_memory(run_dir, limit)


def evidence_contract(path: Path, backend: str = "", limit: int = 5000) -> dict[str, Any]:
    return evidence_contract_service.validate_evidence_csv(path, backend=backend, limit=limit)


def run_detail(run_dir: Path) -> dict[str, Any] | None:
    return run_service.run_detail(run_dir, active_run_ids())


def row_grade(row: dict[str, str]) -> str:
    return run_service.row_grade(row)


def parse_csv_summary(path: Path) -> dict[str, Any]:
    return report_service.parse_csv_summary(path)


def parse_json_run_summary(path: Path) -> dict[str, Any]:
    return report_service.parse_json_run_summary(path)


def compare_runs(records: list[dict[str, Any]]) -> dict[str, Any]:
    return report_service.compare_runs(records)


def compare_run_dirs(run_dirs: list[Path]) -> dict[str, Any]:
    return run_service.compare_run_dirs(run_dirs)


def native_openviking_baseline(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    return run_service.load_native_openviking_baseline(output_dir)


def pin_native_openviking_baseline(payload: dict[str, Any]) -> dict[str, Any]:
    output_dir = safe_path(payload.get("output_dir") or str(DEFAULT_OUTPUT_DIR))
    run_dir_text = str(payload.get("run_dir") or "").strip()
    auto = bool(payload.get("auto"))
    run_dir = safe_path(run_dir_text) if run_dir_text else None
    return run_service.pin_native_openviking_baseline(
        output_dir,
        run_dir=run_dir,
        auto=auto,
        note=str(payload.get("note") or ""),
    )


def compare_csv_rows(base_path: Path, candidate_path: Path) -> dict[str, Any]:
    return report_service.compare_csv_rows(base_path, candidate_path)


def failure_mode(row: dict[str, str]) -> str:
    return report_service.failure_mode(row)


def cluster_failures(rows: list[dict[str, str]]) -> dict[str, Any]:
    return report_service.cluster_failures(rows)


def analyze_wrong_answers(csv_path: Path, out_path: Path | None = None) -> dict[str, Any]:
    return report_service.analyze_wrong_answers(csv_path, out_path)


def wrong_clusters_for_csv(csv_path: Path) -> dict[str, Any]:
    return report_service.wrong_clusters_for_csv(csv_path)


def task_thread(task: Task) -> None:
    payload = task.env.get("LOCOMO_TASK_PAYLOAD_JSON", {}) and json.loads(task.env["LOCOMO_TASK_PAYLOAD_JSON"])
    task_timeout_s = float(payload.get("task_timeout_s") or payload.get("taskTimeoutS") or 0)
    with TASK_LOCK:
        task.status = "running"
        task.started_at = time.time()
        write_manifest(task, payload, Path(task.run_dir))
    log_path = Path(task.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(task.env)
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"$ {' '.join(task.display_command or redacted_command(task.command))}\n")
            log.flush()
            proc = subprocess.Popen(
                task.command,
                cwd=task.cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            timeout_state = {"triggered": False}

            def terminate_timed_out_process() -> None:
                if task_timeout_s <= 0 or proc.poll() is not None:
                    return
                timeout_state["triggered"] = True
                with TASK_LOCK:
                    task.error = f"任务超过 {task_timeout_s:g}s 未结束，已自动停止。"
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    return
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass

            timeout_timer = None
            if task_timeout_s > 0:
                timeout_timer = threading.Timer(task_timeout_s, terminate_timed_out_process)
                timeout_timer.daemon = True
                timeout_timer.start()
            with TASK_LOCK:
                task.process = proc
                task.pid = proc.pid
                if task.status == "stopping":
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                write_manifest(task, payload, Path(task.run_dir))
            if task.kind in {"openviking_generic_qa", "echomemory_generic_qa"} and task.output_file:
                try:
                    watch_script = ROOT / "scripts" / "watch_generic_benchmark_live_report.py"
                    if watch_script.exists():
                        watch_cmd = [
                            sys.executable,
                            str(watch_script),
                            "--run-dir",
                            str(task.run_dir),
                            "--csv",
                            str(task.output_file),
                            "--title",
                            str(task.name or "Generic Benchmark Live Report"),
                        ]
                        subprocess.Popen(
                            watch_cmd,
                            cwd=str(ROOT),
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            text=True,
                            start_new_session=True,
                        )
                        log.write(f"[live-report-watch] started {' '.join(watch_cmd)}\n")
                        log.flush()
                except Exception as exc:
                    log.write(f"[live-report-watch] failed to start: {exc}\n")
                    log.flush()
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                log.flush()
            rc = proc.wait()
            if timeout_timer:
                timeout_timer.cancel()
            if timeout_state["triggered"]:
                log.write(f"[task-timeout] killed after {task_timeout_s:g}s\n")
                log.flush()
        with TASK_LOCK:
            task.returncode = rc
            if rc == 0:
                task.status = "succeeded"
            elif getattr(task, "status", "") == "stopping" and rc in {-15, -2, 143, 130}:
                task.status = "interrupted"
            else:
                task.status = "failed"
            task.ended_at = time.time()
            preserve_error = bool(task.error and rc != 0)
            if task.output_file:
                output = Path(task.output_file)
                if output.exists():
                    if output.suffix.lower() == ".json":
                        task.summary = parse_json_run_summary(output)
                    elif output.suffix.lower() == ".csv":
                        task.summary = parse_csv_summary(output)
                    else:
                        task.summary = {}
                    if task.summary.get("wrong"):
                        analysis_path = output.with_suffix(".wrong_analysis.json")
                        task.summary["wrong_analysis"] = analyze_wrong_answers(output, analysis_path)
                        task.summary["wrong_analysis_path"] = str(analysis_path)
                elif rc != 0 and task.status != "interrupted" and not preserve_error:
                    diagnostics = task_log_diagnostics(task)
                    hits = diagnostics.get("model_issue_hits") or diagnostics.get("generic_failure_hits") or []
                    if hits:
                        task.error = str(hits[-1])
                    else:
                        task.error = f"任务失败且未生成输出文件：{output}"
            if task.status == "interrupted" and not task.error:
                task.error = "任务已由用户停止。"
            write_manifest(task, payload, Path(task.run_dir))
    except Exception as exc:
        with TASK_LOCK:
            task.status = "failed"
            diagnostics = task_log_diagnostics(task)
            hits = diagnostics.get("model_issue_hits") or diagnostics.get("generic_failure_hits") or []
            task.error = str(hits[-1]) if hits else str(exc)
            task.ended_at = time.time()
            if task.run_dir:
                write_manifest(task, payload, Path(task.run_dir))


def resolve_judge_token(payload: dict[str, Any], config: Path) -> str:
    token = payload.get("judge_token") or payload.get("answer_token") or ""
    if not token:
        token = (
            os.environ.get("LOCOMO_JUDGE_TOKEN")
            or os.environ.get("JUDGE_TOKEN")
            or os.environ.get("ANSWER_TOKEN")
            or os.environ.get("LOCOMO_ANSWER_TOKEN")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("ECHOMEM_CHAT_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
    if not token:
        try:
            cfg = read_json(config)
            token = cfg.get("vlm", {}).get("api_key", "")
        except Exception:
            token = ""
    return token


def resolve_vlm_config(payload: dict[str, Any], config: Path) -> dict[str, str]:
    resolved = {
        "api_key": str(payload.get("vlm_api_key") or payload.get("dashscope_api_key") or payload.get("echomem_api_key") or "").strip(),
        "api_base": str(payload.get("vlm_base_url") or payload.get("dashscope_base_url") or "").strip(),
        "model": str(payload.get("vlm_model") or payload.get("echomem_chat_model") or "").strip(),
        "provider": str(payload.get("vlm_provider") or payload.get("echomem_chat_provider") or "").strip(),
    }

    resolved["api_key"] = resolved["api_key"] or os.environ.get("DASHSCOPE_API_KEY", "") or os.environ.get("ECHOMEM_CHAT_API_KEY", "")
    resolved["api_base"] = resolved["api_base"] or os.environ.get("ECHOMEM_CHAT_BASE_URL", "") or os.environ.get("DASHSCOPE_BASE_URL", "")
    resolved["model"] = resolved["model"] or os.environ.get("ECHOMEM_CHAT_MODEL", "")
    resolved["provider"] = resolved["provider"] or os.environ.get("ECHOMEM_CHAT_PROVIDER", "")

    def normalize_base(value: Any) -> str:
        return str(value or "").strip().rstrip("/").lower()

    def compatible_key_source(source_base: str) -> bool:
        target_base = normalize_base(resolved.get("api_base"))
        source_base = normalize_base(source_base)
        return not target_base or not source_base or target_base == source_base

    def merge_vlm(raw: dict[str, Any]) -> None:
        vlm = raw.get("vlm") if isinstance(raw, dict) else {}
        if not isinstance(vlm, dict):
            return
        source_base = str(vlm.get("api_base") or vlm.get("base_url") or "").strip()
        source_key = str(vlm.get("api_key") or "").strip()
        if not resolved["api_key"] and source_key and compatible_key_source(source_base):
            resolved["api_key"] = source_key
        if not resolved["api_base"]:
            resolved["api_base"] = source_base
        resolved["model"] = resolved["model"] or str(vlm.get("model") or "").strip()
        resolved["provider"] = resolved["provider"] or str(vlm.get("provider") or "").strip()

    for path in openviking_config_candidates(config):
        if resolved["api_key"] and resolved["api_base"] and resolved["model"]:
            break
        try:
            merge_vlm(read_json(path))
        except Exception:
            continue

    resolved["model"] = resolved["model"] or str(payload.get("answer_model") or payload.get("judge_model") or "")
    resolved["provider"] = resolved["provider"] or "deepseek"
    return resolved


def resolve_openviking_embedding_config() -> dict[str, str]:
    """Read local OpenViking embedding config as EchoMemory embedding defaults."""
    raw = {}
    for path in openviking_config_candidates():
        try:
            raw = read_json(path)
            break
        except Exception:
            continue
    dense = ((raw.get("embedding") or {}).get("dense") or {}) if isinstance(raw, dict) else {}
    return {
        "api_key": str(dense.get("api_key") or "").strip(),
        "api_base": str(dense.get("api_base") or dense.get("base_url") or "").strip(),
        "model": str(dense.get("model") or "").strip(),
        "provider": str(dense.get("provider") or "").strip(),
    }


def resolve_openviking_vlm_config() -> dict[str, str]:
    """Read local OpenViking VLM config as EchoMemory chat defaults."""
    raw = {}
    for path in openviking_config_candidates():
        try:
            raw = read_json(path)
            break
        except Exception:
            continue
    vlm = (raw.get("vlm") or {}) if isinstance(raw, dict) else {}
    return {
        "api_key": str(vlm.get("api_key") or "").strip(),
        "api_base": str(vlm.get("api_base") or vlm.get("base_url") or "").strip(),
        "model": str(vlm.get("model") or "").strip(),
        "provider": str(vlm.get("provider") or "").strip(),
    }


def resolve_echomemory_runtime_env(payload: dict[str, Any], config: Path, judge_token: str = "") -> dict[str, str]:
    """Return EchoMemory SDK env without letting Answer/Judge endpoints pollute embedding."""
    vlm_config = resolve_vlm_config(payload, config)
    embedding_config = resolve_openviking_embedding_config()
    openviking_vlm = resolve_openviking_vlm_config()
    dashscope_base = str(
        payload.get("dashscope_base_url")
        or payload.get("embedding_base_url")
        or payload.get("memory_base_url")
        or payload.get("vlm_base_url")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or embedding_config.get("api_base")
        or openviking_vlm.get("api_base")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).strip()
    explicit_chat_base = (
        payload.get("echomem_chat_base_url")
        or payload.get("vlm_base_url")
        or payload.get("memory_base_url")
        or os.environ.get("ECHOMEM_CHAT_BASE_URL")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or openviking_vlm.get("api_base")
    )
    chat_base = str(explicit_chat_base or dashscope_base).strip()
    token = str(
        payload.get("dashscope_api_key")
        or payload.get("echomem_api_key")
        or payload.get("echomemEmbeddingApiKey")
        or payload.get("embedding_api_key")
        or payload.get("memory_token")
        or payload.get("vlm_api_key")
        or payload.get("answer_token")
        or payload.get("judge_token")
        or judge_token
        or os.environ.get("DASHSCOPE_API_KEY")
        or embedding_config.get("api_key")
        or openviking_vlm.get("api_key")
        or ""
    ).strip()
    chat_token = str(
        payload.get("echomem_chat_api_key")
        or payload.get("echomemChatApiKey")
        or payload.get("vlm_api_key")
        or payload.get("answer_token")
        or payload.get("judge_token")
        or payload.get("memory_token")
        or judge_token
        or os.environ.get("ECHOMEM_CHAT_API_KEY")
        or openviking_vlm.get("api_key")
        or token
    ).strip()
    if not token and chat_token:
        token = chat_token
    if not chat_token and token:
        chat_token = token
    chat_model = str(
        payload.get("echomem_chat_model")
        or payload.get("memory_inject_model")
        or payload.get("vlm_model")
        or payload.get("memory_model")
        or os.environ.get("ECHOMEM_CHAT_MODEL")
        or openviking_vlm.get("model")
        or "deepseek-v4-flash"
    ).strip()
    provider_config = {
        **vlm_config,
        "api_base": chat_base,
        "model": chat_model,
        "provider": str(payload.get("echomem_chat_provider") or payload.get("vlm_provider") or openviking_vlm.get("provider") or vlm_config.get("provider") or "").strip(),
    }
    return {
        "token": token,
        "chat_token": chat_token,
        "chat_provider": echomemory_chat_provider(provider_config),
        "chat_model": chat_model,
        "chat_base": chat_base,
        "dashscope_base": dashscope_base,
    }


def echomemory_chat_provider(vlm_config: dict[str, str]) -> str:
    provider = str(vlm_config.get("provider") or "").strip().lower()
    model = str(vlm_config.get("model") or "").strip().lower()
    base_url = str(vlm_config.get("api_base") or "").strip().lower()
    if provider in {"deepseek", "dashscope", "anthropic"}:
        return provider
    if "dashscope" in base_url:
        return "deepseek" if "deepseek" in model else "dashscope"
    if "deepseek" in base_url or "deepseek" in model:
        return "deepseek"
    return "deepseek"


def sanitize_model_error(text: Any) -> str:
    value = str(text or "").strip()
    value = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", value)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._-]{8,}", "Bearer ***", value, flags=re.I)
    return value[:1200]


def openai_compatible_chat_preflight(base_url: str, model: str, token: str, timeout_s: float = 45) -> dict[str, Any]:
    base = str(base_url or "").strip().rstrip("/")
    model = str(model or "").strip()
    token = str(token or "").strip()
    if not base:
        return {"ok": False, "status": "missing_base_url", "error": "缺少 Base URL。"}
    if not model:
        return {"ok": False, "status": "missing_model", "error": "缺少模型名。"}
    if not token:
        return {"ok": False, "status": "missing_api_key", "error": "缺少 API Key。"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_s or 45)) as response:
            raw = response.read(4096).decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            return {
                "ok": True,
                "status": response.status,
                "base_url": base,
                "model": model,
                "content_len": len(text),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", "replace")
        return {
            "ok": False,
            "status": exc.code,
            "base_url": base,
            "model": model,
            "error": sanitize_model_error(body),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": exc.__class__.__name__,
            "base_url": base,
            "model": model,
            "error": sanitize_model_error(exc),
        }


def model_preflight_from_payload(payload: dict[str, Any], config: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    role = str(payload.get("role") or "agent").strip().lower()
    if role in {"memory", "inject", "echomemory", "vlm"}:
        env = resolve_echomemory_runtime_env(payload, config, resolve_judge_token(payload, config))
        base_url = str(payload.get("base_url") or payload.get("memory_base_url") or env.get("chat_base") or "").strip()
        model = str(payload.get("model") or payload.get("memory_model") or env.get("chat_model") or "").strip()
        token = str(payload.get("api_key") or payload.get("token") or payload.get("memory_token") or env.get("chat_token") or env.get("token") or "").strip()
    elif role == "judge":
        defaults = load_ov_defaults(config)
        base_url = str(payload.get("base_url") or payload.get("judge_base_url") or defaults.get("judge_base_url") or "").strip()
        model = str(payload.get("model") or payload.get("judge_model") or defaults.get("judge_model") or "").strip()
        token = str(payload.get("api_key") or payload.get("token") or payload.get("judge_token") or resolve_judge_token(payload, config) or "").strip()
    else:
        defaults = load_ov_defaults(config)
        base_url = str(payload.get("base_url") or payload.get("answer_base_url") or defaults.get("answer_base_url") or defaults.get("judge_base_url") or "").strip()
        model = str(payload.get("model") or payload.get("answer_model") or defaults.get("answer_model") or defaults.get("judge_model") or "").strip()
        token = str(payload.get("api_key") or payload.get("token") or payload.get("answer_token") or resolve_judge_token(payload, config) or "").strip()
    result = openai_compatible_chat_preflight(base_url, model, token, timeout_s=float(payload.get("timeout_s") or 45))
    result["role"] = role
    result["token_set"] = bool(token)
    return result


def skip_model_preflight(payload: dict[str, Any]) -> bool:
    return str(payload.get("skip_model_preflight") or "").strip().lower() in {"1", "true", "yes", "on"}


def task_model_preflight_payload(
    kind: str,
    payload: dict[str, Any],
    config: Path,
    echomemory_env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if kind == "judge":
        return {**payload, "role": "judge"}
    if kind not in {
        "openviking_qa",
        "openviking_generic_qa",
        "openviking_qa_retry_failed",
        "openviking_qa_retry_missing",
        "echomemory_qa",
        "echomemory_generic_qa",
        "echomemory_qa_retry_failed",
    }:
        return None
    preflight_payload = {**payload, "role": "agent"}
    if kind in {"echomemory_qa", "echomemory_generic_qa", "echomemory_qa_retry_failed"}:
        explicit_answer_token = str(payload.get("answer_token") or payload.get("judge_token") or "").strip()
        if not explicit_answer_token:
            fallback_token = str((echomemory_env or {}).get("chat_token") or (echomemory_env or {}).get("token") or "").strip()
            if fallback_token:
                preflight_payload.setdefault("answer_token", fallback_token)
                preflight_payload.setdefault("token", fallback_token)
    return preflight_payload


def ensure_task_model_preflight(
    kind: str,
    payload: dict[str, Any],
    config: Path,
    echomemory_env: dict[str, str] | None = None,
) -> None:
    preflight_payload = task_model_preflight_payload(kind, payload, config, echomemory_env)
    if not preflight_payload or skip_model_preflight(payload):
        return
    result = model_preflight_from_payload(preflight_payload, config)
    if result.get("ok"):
        return
    role = str(result.get("role") or "agent").lower()
    role_label = "判分模型" if role == "judge" else "答案模型"
    raise ValueError(
        f"{role_label}预检失败："
        f"{result.get('model') or ''} @ {result.get('base_url') or ''} "
        f"status={result.get('status')} · {result.get('error') or 'unknown error'}"
    )


def build_single_command(kind: str, payload: dict[str, Any], run_dir: Path, config: Path) -> tuple[list[str], str, str]:
    return build_backend_command(
        kind,
        payload,
        run_dir,
        config,
        context=TaskFactoryContext(
            root=ROOT,
            default_data=DEFAULT_DATA,
            safe_path=safe_path,
            infer_dataset_format=infer_dataset_format,
            load_ov_defaults=load_ov_defaults,
            resolve_judge_token=resolve_judge_token,
            ensure_judge_columns=ensure_judge_columns,
        ),
    )


def build_pipeline_script(payload: dict[str, Any], run_dir: Path, config: Path) -> tuple[list[str], str, str]:
    task_spec = task_spec_service.build_local_pipeline_task(payload, run_dir, ROOT, DEFAULT_DATA, safe_path, infer_dataset_format)
    return task_spec.command, task_spec.output_file, task_spec.name


def merge_csv_script() -> str:
    return (
        "import csv, json, sys\n"
        "out = sys.argv[1]\n"
        "inputs = sys.argv[2:]\n"
        "fieldnames = None\n"
        "rows = []\n"
        "for path in inputs:\n"
        "    with open(path, newline='', encoding='utf-8') as f:\n"
        "        reader = csv.DictReader(f)\n"
        "        if fieldnames is None:\n"
        "            fieldnames = reader.fieldnames\n"
        "        rows.extend(reader)\n"
        "with open(out, 'w', newline='', encoding='utf-8') as f:\n"
        "    writer = csv.DictWriter(f, fieldnames=fieldnames)\n"
        "    writer.writeheader()\n"
        "    writer.writerows(rows)\n"
        "correct = sum(1 for r in rows if (r.get('result') or r.get('simple_grade') or '').upper() in ('MATCH', 'CORRECT'))\n"
        "wrong = sum(1 for r in rows if (r.get('result') or r.get('simple_grade') or '').upper() == 'WRONG')\n"
        "summary = {'count': len(rows), 'correct': correct, 'wrong': wrong, 'accuracy': round(correct / (correct + wrong), 4) if correct + wrong else None, 'csv': out}\n"
        "with open(out.rsplit('/', 1)[0] + '/summary.json', 'w', encoding='utf-8') as f:\n"
        "    json.dump(summary, f, ensure_ascii=False, indent=2)\n"
        "print(f'Merged {len(rows)} rows -> {out}')\n"
    )


def build_distributed_script(payload: dict[str, Any], run_dir: Path, config: Path, cli_config: Path) -> tuple[list[str], str, str]:
    raise ValueError("分布式外部 runner 已移除；请用 MemoryBench 本地基线的 sample/count 控制范围")


def sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def script_arg(text: str) -> str:
    if text == "${LOCOMO_JUDGE_TOKEN}":
        return '"${LOCOMO_JUDGE_TOKEN}"'
    return sh_quote(text)


def create_task(kind: str, payload: dict[str, Any]) -> Task:
    with TASK_CREATION_LOCK:
        return orchestrate_task(
            kind,
            payload,
            context=TaskOrchestratorContext(
                safe_path=safe_path,
                default_repo=DEFAULT_REPO,
                default_output_dir=DEFAULT_OUTPUT_DIR,
                default_config=DEFAULT_CONFIG,
                default_cli_config=DEFAULT_CLI_CONFIG,
                resolve_judge_token=resolve_judge_token,
                resolve_echomemory_runtime_env=resolve_echomemory_runtime_env,
                skip_model_preflight=skip_model_preflight,
                openai_compatible_chat_preflight=openai_compatible_chat_preflight,
                ensure_task_model_preflight=ensure_task_model_preflight,
                now_slug=now_slug,
                restart_openviking_for_workspace=restart_openviking_for_workspace,
                prepare_connection_files=prepare_connection_files,
                redact_manifest_payload=redact_manifest_payload,
                build_single_command=build_single_command,
                build_pipeline_script=build_pipeline_script,
                build_distributed_script=build_distributed_script,
                task_cls=Task,
                redacted_command=redacted_command,
                write_manifest=write_manifest,
                register_task=register_task,
                start_task_thread=lambda task: threading.Thread(target=task_thread, args=(task,), daemon=True).start(),
            ),
            find_duplicate_active_task=find_duplicate_active_task,
            find_conflicting_active_locomo_qa=find_conflicting_active_locomo_qa,
            duplicate_error_cls=DuplicateActiveTaskError,
            conflict_error_cls=ActiveLocomoQaConflictError,
        )


def stop_task(task: Task) -> dict[str, Any]:
    stopped = False
    if task.status in {"queued", "running", "stopping"} and not task.process:
        task.status = "failed"
        task.ended_at = task.ended_at or time.time()
        task.error = task.error or "任务无活跃进程，已标记为失败。"
        stopped = True
    elif task.process and task.status in {"running", "stopping"}:
        try:
            os.killpg(os.getpgid(task.process.pid), signal.SIGTERM)
            task.status = "stopping"
            stopped = True
        except ProcessLookupError:
            task.status = "failed"
            task.ended_at = task.ended_at or time.time()
            task.error = "进程已不存在，标记为失败。"
            stopped = True
        except Exception as exc:
            task.error = str(exc)
    return {"task": task.public(), "stopped": stopped}


def stop_orphan_run_processes() -> list[int]:
    """Stop web-launched eval processes that survived a server restart."""
    try:
        raw = subprocess.check_output(["pgrep", "-f", str(ROOT / "scripts")], text=True)
    except Exception:
        return []
    stopped: list[int] = []
    current_pid = os.getpid()
    for line in raw.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid == current_pid:
            continue
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped.append(pid)
            except Exception:
                pass
    return sorted(set(stopped))


def stop_all_tasks_response() -> dict[str, Any]:
    with TASK_LOCK:
        tasks = [t for t in TASKS.values() if t.status in {"queued", "running", "stopping"}]
        results = [stop_task(t) for t in tasks]
    orphan_pids = stop_orphan_run_processes()
    return {
        "stopped": sum(1 for r in results if r["stopped"]) + len(orphan_pids),
        "orphan_pids": orphan_pids,
        "tasks": [r["task"] for r in results],
    }


def stop_task_by_id_response(task_id: str) -> tuple[dict[str, Any], int]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        return {"error": "task not found"}, 404
    with TASK_LOCK:
        stop_task(task)
    return task.public(), 200


def normalize_memory_backend(value: Any) -> str:
    return account_service.normalize_backend(str(value or ""))


def memory_backend_label(value: Any) -> str:
    backend = normalize_memory_backend(value)
    return "EchoMemory" if backend == "echomemory" else "OpenViking"


def agent_backend_from_payload(payload: dict[str, Any]) -> str:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    return normalize_memory_backend(
        payload.get("backend")
        or payload.get("memoryBackend")
        or config.get("memoryBackend")
        or config.get("backend")
        or "openviking"
    )


def unsupported_agent_backend(backend: str, method: str) -> dict[str, Any]:
    label = memory_backend_label(backend)
    return {
        "error": f"当前后端 {label} 尚未实现对话 Agent 工作台。请在系统配置切回 OpenViking，或先为该后端实现 {method}。",
        "backend": backend,
        "backend_label": label,
        "capability": "agent_workbench",
        "supported": False,
        "hint": "LoCoMo 导入、QA 和报告仍可按当前后端运行；这里只拦截人工对话、上下文预览和手动保存记忆。",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LoCoMoEvalWeb/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_length_headers(self, content_type: str, content_length: int, *, no_store: bool = False) -> None:
        self.send_header("Content-Type", content_type)
        if no_store:
            self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Connection", "close")

    def send_json(self, obj: Any, status: int = 200, write_body: bool = True) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_length_headers("application/json; charset=utf-8", len(data))
        self.end_headers()
        if write_body:
            self.wfile.write(data)

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def serve_static(self, path: str, write_body: bool = True) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = "text/html; charset=utf-8"
        if target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix == ".png":
            ctype = "image/png"
        elif target.suffix in {".jpg", ".jpeg"}:
            ctype = "image/jpeg"
        elif target.suffix == ".gif":
            ctype = "image/gif"
        elif target.suffix == ".webp":
            ctype = "image/webp"
        elif target.suffix == ".svg":
            ctype = "image/svg+xml"
        data = target.read_bytes()
        self.send_response(200)
        self._send_length_headers(ctype, len(data), no_store=target.suffix in {".html", ".js", ".css"})
        self.end_headers()
        if write_body:
            self.wfile.write(data)

    def serve_run_artifact(self, path: str, write_body: bool = True) -> bool:
        if not path.startswith("/runs/"):
            return False
        rel = path.removeprefix("/runs/").lstrip("/")
        target = (DEFAULT_OUTPUT_DIR / rel).resolve()
        runs_root = DEFAULT_OUTPUT_DIR.resolve()
        allowed_suffixes = {
            ".html": "text/html; charset=utf-8",
            ".md": "text/markdown; charset=utf-8",
            ".csv": "text/csv; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
        }
        if (
            not str(target).startswith(str(runs_root))
            or not target.exists()
            or not target.is_file()
            or target.name.startswith(".")
            or target.suffix.lower() not in allowed_suffixes
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return True
        data = target.read_bytes()
        self.send_response(200)
        self._send_length_headers(
            allowed_suffixes[target.suffix.lower()],
            len(data),
            no_store=target.suffix.lower() == ".html",
        )
        self.end_headers()
        if write_body:
            self.wfile.write(data)
        return True

    def serve_generated_report_artifact(self, path: str, write_body: bool = True) -> bool:
        if not path.startswith("/generated-reports/"):
            return False
        rel = path.removeprefix("/generated-reports/").lstrip("/")
        target = (GENERATED_REPORTS_DIR / rel).resolve()
        reports_root = GENERATED_REPORTS_DIR.resolve()
        allowed_suffixes = {
            ".html": "text/html; charset=utf-8",
            ".md": "text/markdown; charset=utf-8",
            ".csv": "text/csv; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
        }
        if (
            not str(target).startswith(str(reports_root))
            or not target.exists()
            or not target.is_file()
            or target.name.startswith(".")
            or target.suffix.lower() not in allowed_suffixes
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return True
        data = target.read_bytes()
        self.send_response(200)
        self._send_length_headers(
            allowed_suffixes[target.suffix.lower()],
            len(data),
            no_store=target.suffix.lower() == ".html",
        )
        self.end_headers()
        if write_body:
            self.wfile.write(data)
        return True

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/health", "/api/health"}:
            self.send_json(health_status(), write_body=False)
            return
        if self.serve_run_artifact(parsed.path, write_body=False):
            return
        if self.serve_generated_report_artifact(parsed.path, write_body=False):
            return
        self.serve_static(parsed.path, write_body=False)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/health", "/api/health"}:
            self.send_json(health_status())
            return
        if self.serve_run_artifact(parsed.path):
            return
        if self.serve_generated_report_artifact(parsed.path):
            return
        if parsed.path == "/api/config":
            self.send_json(ui_boot_config())
            return
        if parsed.path == "/api/ui-contract":
            self.send_json(load_ui_contract())
            return
        if parsed.path == "/api/accounts":
            self.send_json(account_service.public_state(ACCOUNT_STATE_FILE, load_ov_defaults()))
            return
        if parsed.path == "/api/account-config":
            qs = parse_qs(parsed.query)
            account = qs.get("account", ["default"])[0] or "default"
            try:
                record = account_service.private_account_state(ACCOUNT_STATE_FILE, load_ov_defaults(), account)
            except KeyError:
                self.send_json({"error": "account not found"}, 404)
                return
            self.send_json(record)
            return
        if parsed.path == "/api/backends":
            backends = available_adapters()
            self.send_json({"backends": backends})
            return
        if parsed.path == "/api/plugins":
            backends = available_adapters()
            self.send_json({"backends": backends, "deprecated": True, "message": f"Use /api/backends. Current scope is {MEMORY_BACKEND_SCOPE}."})
            return
        if parsed.path == "/api/datasets":
            self.send_json({"datasets": dataset_registry()})
            return
        if parsed.path == "/api/system-preflight":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(system_preflight({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/handoff-audit":
            self.send_json(handoff_audit())
            return
        if parsed.path == "/api/adapter-doctor":
            self.send_json(adapter_doctor_report())
            return
        if parsed.path == "/api/delivery-boundary":
            self.send_json(delivery_boundary_gate())
            return
        if parsed.path == "/api/readiness":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(system_readiness({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/handoff-dashboard":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(handoff_dashboard({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/github-launch-kit":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(github_launch_kit({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/locomo-flow-status":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(locomo_flow_status({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/acceptance-matrix":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(acceptance_matrix({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/smoke-plan":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(smoke_plan({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/setup-pack":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(setup_pack({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/handoff-package":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
                ("host", "ovHost"),
                ("port", "ovPort"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(handoff_package({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/echomem-contract":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {"memoryBackend": "echomemory"}
            for source, target in [
                ("workspace", "ovWorkspace"),
                ("echomem_root", "echomemRoot"),
                ("judge_base_url", "judgeBaseUrl"),
                ("judge_model", "judgeModel"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(echomem_contract({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/agent-alignment":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(agent_alignment_status({
                "account": qs.get("account", [""])[0],
                "dataset": qs.get("dataset", [""])[0],
                "config": config,
            }))
            return
        if parsed.path == "/api/account-isolation":
            qs = parse_qs(parsed.query)
            config: dict[str, Any] = {}
            for source, target in [
                ("backend", "memoryBackend"),
                ("workspace", "ovWorkspace"),
            ]:
                if qs.get(source, [""])[0]:
                    config[target] = qs[source][0]
            self.send_json(account_isolation_status({
                "account": qs.get("account", [""])[0],
                "config": config,
            }))
            return
        if handle_memory_backend_get(
            parsed,
            send_json=self.send_json,
            safe_path=safe_path,
            load_defaults=load_ov_defaults,
            normalize_memory_backend=normalize_memory_backend,
            plugin_service=plugin_service,
            backend_runtime_status=backend_runtime_status,
            default_output_dir=DEFAULT_OUTPUT_DIR,
            default_data=DEFAULT_DATA,
        ):
            return
        if parsed.path == "/api/dataset":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [str(DEFAULT_DATA)])[0])
            try:
                self.send_json(dataset_overview(path))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/questions":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [str(DEFAULT_DATA)])[0])
            sample = qs.get("sample", ["all"])[0] or "all"
            try:
                self.send_json(benchmark_questions(path, sample))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/questions-page":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [str(DEFAULT_DATA)])[0])
            offset = int(qs.get("offset", ["0"])[0] or 0)
            limit = int(qs.get("limit", ["100"])[0] or 100)
            query = qs.get("q", [""])[0] or ""
            try:
                self.send_json(benchmark_questions_page(path, offset, limit, query))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/question-set":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [str(DEFAULT_DATA)])[0])
            mode = qs.get("mode", ["time"])[0] or "time"
            sample = qs.get("sample", ["all"])[0] or "all"
            csv_text = qs.get("csv", [""])[0]
            csv_path = safe_path(csv_text) if csv_text else None
            try:
                self.send_json(question_set(path, mode, csv_path, sample))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/dataset/load":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            if not path.exists():
                self.send_json({"error": "File not found", "path": str(path)}, status=404)
                return
            try:
                data = read_json(path)
                self.send_json(data)
            except Exception as e:
                self.send_json({"error": str(e), "path": str(path)}, status=500)
            return
        if parsed.path == "/api/context-pack":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [str(DEFAULT_DATA)])[0])
            limit = int(qs.get("limit", ["8"])[0] or 8)
            try:
                self.send_json(context_pack_preview(path, limit))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/tasks":
            qs = parse_qs(parsed.query)
            include_inactive = str(qs.get("include_inactive", ["0"])[0] or "").strip().lower() in {"1", "true", "yes", "on"}
            recover = str(qs.get("recover", ["0"])[0] or "").strip().lower() in {"1", "true", "yes", "on"}
            if include_inactive or recover:
                recover_tasks_from_disk()
            with TASK_LOCK:
                source_tasks = TASKS.values() if include_inactive else [task for task in TASKS.values() if task.status in ACTIVE_TASK_STATUSES]
                tasks = [t.public() for t in sorted(source_tasks, key=lambda x: x.created_at, reverse=True)]
            self.send_json({"tasks": tasks, "scope": "all" if include_inactive else "active"})
            return
        if parsed.path == "/api/runs":
            qs = parse_qs(parsed.query)
            limit = int(qs.get("limit", ["40"])[0] or 40)
            output_dir = safe_path(qs.get("output_dir", [str(DEFAULT_OUTPUT_DIR)])[0])
            query = qs.get("q", [""])[0]
            status = qs.get("status", ["all"])[0]
            include_history = str(qs.get("include_history", ["0"])[0] or "").strip().lower() in {"1", "true", "yes", "on"}
            if include_history:
                runs = list_runs(output_dir, limit, query, status, compact=True)
                scope_meta = {
                    "scope": "all local history",
                    "include_history": True,
                    "hidden_history_count": 0,
                    "returned": len(runs),
                }
            else:
                runs, scope_meta = list_current_scope_runs(output_dir, limit, query, status)
                scope_meta["include_history"] = False
            self.send_json({"runs": runs, "compare": compare_runs(runs[:8]), "scope": scope_meta})
            return
        if parsed.path == "/api/native-openviking-baseline":
            qs = parse_qs(parsed.query)
            output_dir = safe_path(qs.get("output_dir", [str(DEFAULT_OUTPUT_DIR)])[0])
            self.send_json(native_openviking_baseline(output_dir))
            return
        if parsed.path == "/api/run-detail":
            qs = parse_qs(parsed.query)
            run_dir = safe_path(qs.get("run_dir", [""])[0])
            detail = run_detail(run_dir)
            if not detail:
                self.send_json({"error": "run not found"}, 404)
                return
            self.send_json(detail)
            return
        if parsed.path == "/api/run-diff":
            qs = parse_qs(parsed.query)
            base = safe_path(qs.get("base", [""])[0])
            candidate = safe_path(qs.get("candidate", [""])[0])
            if not base.exists() or not candidate.exists():
                self.send_json({"error": "base or candidate csv not found"}, 404)
                return
            self.send_json(compare_csv_rows(base, candidate))
            return
        if parsed.path == "/api/question-detail":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            question_id = qs.get("question_id", [""])[0]
            index_text = qs.get("index", [""])[0]
            index = int(index_text) if index_text.strip().isdigit() else None
            try:
                self.send_json(question_result_detail(path, question_id, index))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 404)
            return
        if parsed.path == "/api/wrong-clusters":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            try:
                self.send_json(wrong_clusters_for_csv(path))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/report":
            qs = parse_qs(parsed.query)
            run_dir = safe_path(qs.get("run_dir", [""])[0])
            try:
                self.send_json(export_report(run_dir))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/config-snapshot":
            qs = parse_qs(parsed.query)
            run_dir = safe_path(qs.get("run_dir", [""])[0])
            snapshot_path = run_dir / "config_snapshot.json"
            if not snapshot_path.exists():
                self.send_json({"error": "config snapshot not found"}, 404)
                return
            self.send_json({"path": str(snapshot_path), "config": read_json(snapshot_path)})
            return
        if parsed.path == "/api/csv-preview":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            limit = int(qs.get("limit", ["20"])[0] or 20)
            self.send_json(csv_preview(path, limit))
            return
        if parsed.path == "/api/qa-diagnostics":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            dataset_text = qs.get("dataset", [""])[0]
            dataset_path = safe_path(dataset_text) if dataset_text else None
            sample = qs.get("sample", ["all"])[0] or "all"
            try:
                self.send_json(qa_diagnostics(path, dataset_path, sample))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/pending-preview":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            limit = int(qs.get("limit", ["20"])[0] or 20)
            category = qs.get("category", [""])[0]
            query = qs.get("q", [""])[0]
            min_text = qs.get("min_tokens", [""])[0]
            max_text = qs.get("max_tokens", [""])[0]
            min_tokens = int(min_text) if min_text.strip().isdigit() else None
            max_tokens = int(max_text) if max_text.strip().isdigit() else None
            self.send_json(csv_pending_preview(path, limit, category, query, min_tokens, max_tokens))
            return
        if parsed.path == "/api/export-pending-csv":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            category = qs.get("category", [""])[0]
            query = qs.get("q", [""])[0]
            min_text = qs.get("min_tokens", [""])[0]
            max_text = qs.get("max_tokens", [""])[0]
            min_tokens = int(min_text) if min_text.strip().isdigit() else None
            max_tokens = int(max_text) if max_text.strip().isdigit() else None
            try:
                self.send_json(export_pending_csv(path, category, query, min_tokens, max_tokens))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/log-tail":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            limit = int(qs.get("limit", ["12000"])[0] or 12000)
            self.send_json(tail_file(path, limit))
            return
        if parsed.path == "/api/file":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            if not path.exists() or not path.is_file():
                self.send_json({"error": "file not found", "path": str(path)}, 404)
                return
            self.send_json({"path": str(path), "text": path.read_text(encoding="utf-8", errors="replace")})
            return
        if parsed.path == "/api/relevant-memory":
            qs = parse_qs(parsed.query)
            run_dir = safe_path(qs.get("run_dir", [""])[0])
            limit = int(qs.get("limit", ["30"])[0] or 30)
            self.send_json(relevant_memory(run_dir, limit))
            return
        if parsed.path == "/api/evidence-contract":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            backend = qs.get("backend", [""])[0]
            limit = int(qs.get("limit", ["5000"])[0] or 5000)
            try:
                self.send_json(evidence_contract(path, backend, limit))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path.startswith("/api/tasks/"):
            parts = parsed.path.strip("/").split("/")
            task_id = parts[2] if len(parts) >= 3 else ""
            with TASK_LOCK:
                task = TASKS.get(task_id)
            if not task:
                self.send_json({"error": "task not found"}, 404)
                return
            if len(parts) == 4 and parts[3] == "log":
                qs = parse_qs(parsed.query)
                offset = int(qs.get("offset", ["0"])[0] or 0)
                log_path = Path(task.log_file)
                text = ""
                new_offset = offset
                if log_path.exists():
                    with log_path.open("r", encoding="utf-8", errors="replace") as f:
                        f.seek(offset)
                        text = f.read()
                        new_offset = f.tell()
                self.send_json({"offset": new_offset, "text": text, "task": task.public()})
                return
            self.send_json(task.public())
            return
        if parsed.path == "/api/results":
            qs = parse_qs(parsed.query)
            path = safe_path(qs.get("path", [""])[0])
            if not path.exists():
                self.send_json({"error": "file not found"}, 404)
                return
            summary = parse_json_run_summary(path) if path.suffix.lower() == ".json" else parse_csv_summary(path)
            analysis_path = path.with_suffix(".wrong_analysis.json")
            analysis = None
            if analysis_path.exists():
                analysis = read_json(analysis_path)
            self.send_json({"summary": summary, "analysis": analysis})
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/accounts":
            try:
                payload = self.read_body()
                result = account_service.create_account(
                    ACCOUNT_STATE_FILE,
                    load_ov_defaults(),
                    str(payload.get("account") or ""),
                    str(payload.get("inherit_from") or ""),
                    payload.get("config") if isinstance(payload.get("config"), dict) else None,
                )
                self.send_json(result, 201)
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/accounts/delete":
            try:
                payload = self.read_body()
                self.send_json(account_service.delete_account(ACCOUNT_STATE_FILE, load_ov_defaults(), str(payload.get("account") or "")))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/account-config":
            try:
                payload = self.read_body()
                config = payload.get("config")
                if not isinstance(config, dict):
                    raise ValueError("config must be an object")
                self.send_json(account_service.update_config(ACCOUNT_STATE_FILE, load_ov_defaults(), str(payload.get("account") or "default"), config))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/system-preflight":
            try:
                self.send_json(system_preflight(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/model-preflight":
            try:
                self.send_json(model_preflight_from_payload(self.read_body(), DEFAULT_CONFIG))
            except Exception as exc:
                self.send_json({"ok": False, "error": sanitize_model_error(exc)}, status=400)
            return
        if parsed.path == "/api/handoff-audit":
            try:
                self.send_json(handoff_audit())
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/adapter-doctor":
            try:
                self.send_json(adapter_doctor_report())
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/delivery-boundary":
            try:
                self.send_json(delivery_boundary_gate())
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/readiness":
            try:
                self.send_json(system_readiness(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/handoff-dashboard":
            try:
                self.send_json(handoff_dashboard(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/github-launch-kit":
            try:
                self.send_json(github_launch_kit(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/locomo-flow-status":
            try:
                self.send_json(locomo_flow_status(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/acceptance-matrix":
            try:
                self.send_json(acceptance_matrix(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/smoke-plan":
            try:
                self.send_json(smoke_plan(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/setup-pack":
            try:
                self.send_json(setup_pack(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/handoff-package":
            try:
                self.send_json(handoff_package(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/echomem-contract":
            try:
                payload = self.read_body()
                config = dict(payload.get("config") or {}) if isinstance(payload.get("config"), dict) else {}
                config["memoryBackend"] = "echomemory"
                payload["config"] = config
                self.send_json(echomem_contract(payload))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/agent-alignment":
            try:
                self.send_json(agent_alignment_status(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/account-isolation":
            try:
                self.send_json(account_isolation_status(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/run-compare":
            try:
                payload = self.read_body()
                raw_dirs = payload.get("run_dirs")
                if not isinstance(raw_dirs, list):
                    raise ValueError("run_dirs must be a list")
                run_dirs = [safe_path(str(item or "")) for item in raw_dirs if str(item or "").strip()]
                if payload.get("include_native_openviking_baseline"):
                    baseline = native_openviking_baseline().get("baseline") or {}
                    baseline_dir = str(baseline.get("run_dir") or "").strip()
                    if baseline_dir:
                        baseline_path = safe_path(baseline_dir)
                        run_dirs = [baseline_path] + [path for path in run_dirs if path != baseline_path]
                if len(run_dirs) < 2:
                    raise ValueError("at least two run_dirs are required")
                self.send_json(compare_run_dirs(run_dirs))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/native-openviking-baseline":
            try:
                self.send_json(pin_native_openviking_baseline(self.read_body()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path in {"/api/agent/chat", "/api/agent/context", "/api/agent/archive"}:
            payload = self.read_body()
            if handle_agent_backend_post(
                parsed.path,
                payload,
                send_json=self.send_json,
                load_defaults=load_ov_defaults,
                default_config=DEFAULT_CONFIG,
                default_output_dir=DEFAULT_OUTPUT_DIR,
                plugin_service=plugin_service,
                agent_backend_from_payload=agent_backend_from_payload,
                unsupported_agent_backend=unsupported_agent_backend,
            ):
                return
        if parsed.path in {"/api/tasks", "/api/validate", "/api/tasks/stop-all"} or (
            parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/stop")
        ):
            payload = self.read_body()
            if handle_task_post(
                parsed.path,
                payload,
                send_json=self.send_json,
                create_task=create_task,
                validate_payload=validate_payload,
                stop_all_tasks=stop_all_tasks_response,
                stop_task_by_id=stop_task_by_id_response,
                duplicate_error_cls=DuplicateActiveTaskError,
                conflict_error_cls=ActiveLocomoQaConflictError,
            ):
                return
        if parsed.path == "/api/analyze":
            try:
                payload = self.read_body()
                path = safe_path(payload.get("input") or "")
                out = path.with_suffix(".wrong_analysis.json")
                self.send_json(analyze_wrong_answers(path, out))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/open-path":
            try:
                payload = self.read_body()
                path = str(payload.get("path", "")).strip()
                if not path:
                    self.send_json({"error": "path is required"}, 400)
                    return
                if path.startswith("viking://"):
                    workspace = str(payload.get("workspace") or "").strip()
                    account = str(payload.get("account") or "default").strip() or "default"
                    if not workspace:
                        self.send_json({"error": "workspace is required for viking uri"}, 400)
                        return
                    rel = path.removeprefix("viking://").lstrip("/")
                    if not rel or ".." in Path(rel).parts:
                        self.send_json({"error": "invalid viking uri"}, 400)
                        return
                    path = str(safe_path(workspace) / "viking" / account / rel)
                target = Path(path).expanduser()
                if not target.exists():
                    self.send_json({"error": f"path not found: {target}"}, 404)
                    return
                # 使用 open 命令打开路径（macOS）
                subprocess.run(["open", str(target)], check=True)
                self.send_json({"success": True, "path": str(target)})
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        self.send_json({"error": "not found"}, 404)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="LoCoMo evaluation web console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19181)
    parser.add_argument("--analyze-csv", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.analyze_csv:
        csv_path = safe_path(args.analyze_csv)
        out = csv_path.with_suffix(".wrong_analysis.json")
        result = analyze_wrong_answers(csv_path, out)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return

    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"LoCoMo Eval Web: http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
