from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _request_probe(url: str, headers: dict[str, str]) -> dict[str, Any]:
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=3) as resp:
            body = resp.read(800).decode("utf-8", errors="replace")
            return {"status": resp.status, "body": body}
    except HTTPError as exc:
        body = exc.read(800).decode("utf-8", errors="replace")
        return {"status": exc.code, "body": body, "http_error": True}


def _looks_like_openviking(path: str, status: int, body: str) -> bool:
    low = body.lower()
    if "locomo-eval-web" in low or "locomoevalweb" in low:
        return False
    if path == "/api/v1/admin/accounts" and status in {200, 401, 403}:
        return True
    if path in {"/health", "/api/v1/health"} and status < 500:
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        openviking_keys = {"auth_mode", "account_id", "user_id", "agent_id"}
        if openviking_keys.intersection(data.keys()):
            return True
        if data.get("healthy") is True and "version" in data and data.get("service") != "locomo-eval-web":
            return True
    return "openviking" in low or "viking" in low and path == "/docs"


def probe(host: str, port: str, api_key: str = "") -> dict[str, Any]:
    base = f"http://{host}:{port}"
    checks = ["/health", "/api/v1/health", "/docs", "/api/v1/admin/accounts"]
    details = []
    for path in checks:
        url = base + path
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        try:
            result = _request_probe(url, headers)
            status = int(result.get("status") or 0)
            body = str(result.get("body") or "")
            details.append({"path": path, "status": status, "body": body[:160]})
            if _looks_like_openviking(path, status, body):
                return {"ok": True, "url": base, "status": status, "path": path, "details": details}
        except Exception as exc:
            details.append({"path": path, "error": str(exc)})
    return {"ok": False, "url": base, "details": details}


def discover_ports(host: str = "127.0.0.1", ports: list[str] | None = None, api_key: str = "") -> dict[str, Any]:
    candidates = ports or ["19080", "1933", "1934", "1935", "1936", "1937", "1938", "1939", "1940"]
    results = []
    for port in candidates:
        if not str(port).isdigit():
            results.append({"port": str(port), "ok": False, "error": "invalid port"})
            continue
        probe_result = probe(host, str(port), api_key)
        results.append(
            {
                "port": str(port),
                "ok": bool(probe_result.get("ok")),
                "url": probe_result.get("url"),
                "status": probe_result.get("status"),
                "path": probe_result.get("path"),
                "details": probe_result.get("details", [])[:2],
            }
        )
    available = [item for item in results if item["ok"]]
    return {"host": host, "ports": results, "available": available, "recommended": available[0]["port"] if available else ""}


def now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def workspace_for_run(payload: dict[str, Any], run_dir: Path, safe_path) -> Path | None:
    if payload.get("workspace_mode") == "new_each_import":
        slug = f"openviking_workspace_locomo_{now_slug()}_{uuid.uuid4().hex[:6]}"
        workspace = Path.home() / slug
        workspace.mkdir(parents=True, exist_ok=True)
        payload["workspace"] = str(workspace)
        return workspace
    if payload.get("workspace"):
        workspace = safe_path(payload["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        payload["workspace"] = str(workspace)
        return workspace
    return None


def runtime_config_candidates(base_config: Path) -> list[Path]:
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
    add(base_config)
    add(Path.home() / ".openviking" / "ov.conf")
    return candidates


def make_runtime_config(
    payload: dict[str, Any],
    run_dir: Path,
    base_config: Path,
    memory_templates_dir: Path,
) -> Path:
    config_source = next((path for path in runtime_config_candidates(base_config) if path.exists()), base_config)
    try:
        cfg = read_json(config_source)
    except Exception:
        cfg = {}
    cfg.setdefault("storage", {})
    cfg["storage"]["workspace"] = str(payload["workspace"])
    cfg.setdefault("log", {})
    cfg["log"]["output"] = "stdout"

    if payload.get("vlm_base_url") or payload.get("vlm_api_key") or payload.get("vlm_model"):
        cfg.setdefault("vlm", {})
        if payload.get("vlm_base_url"):
            cfg["vlm"]["api_base"] = str(payload["vlm_base_url"])
        if payload.get("vlm_api_key"):
            cfg["vlm"]["api_key"] = str(payload["vlm_api_key"])
        if payload.get("vlm_model"):
            cfg["vlm"]["model"] = str(payload["vlm_model"])
        cfg["vlm"].setdefault("provider", "openai")
        cfg["vlm"].setdefault("thinking", False)
        cfg["vlm"].setdefault("max_concurrent", 4)

    custom_templates = Path(str(payload.get("memory_custom_templates_dir") or memory_templates_dir)).expanduser()
    if custom_templates.exists():
        cfg.setdefault("memory", {})
        cfg["memory"]["custom_templates_dir"] = str(custom_templates.resolve())
        payload["memory_custom_templates_dir"] = cfg["memory"]["custom_templates_dir"]

    runtime_config = run_dir / "openviking.runtime.conf"
    runtime_config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["openviking_runtime_config"] = str(runtime_config)
    return runtime_config


def restart_for_workspace(
    payload: dict[str, Any],
    run_dir: Path,
    config_path: Path,
    *,
    safe_path,
    openviking_python: Path,
    memory_templates_dir: Path,
) -> dict[str, Any]:
    host = str(payload.get("host") or "127.0.0.1")
    port = str(payload.get("port") or "19080")
    if not port.isdigit():
        raise ValueError(f"OpenViking port is invalid: {port}")

    workspace = workspace_for_run(payload, run_dir, safe_path)
    if not workspace:
        return {"workspace": "", "restarted": False}

    runtime_config = make_runtime_config(payload, run_dir, config_path, memory_templates_dir)
    log_file = run_dir / "openviking-server.log"
    payload["openviking_server_log"] = str(log_file)

    try:
        raw = subprocess.check_output(["lsof", "-tiTCP:" + port, "-sTCP:LISTEN"], text=True)
        for line in raw.splitlines():
            pid = int(line.strip())
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    except Exception:
        pass
    time.sleep(1.0)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    with log_file.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [
                str(openviking_python),
                "-m",
                "openviking.server.bootstrap",
                "--config",
                str(runtime_config),
                "--host",
                host,
                "--port",
                port,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    api_key = str(payload.get("root_api_key") or "")
    deadline = time.time() + 45
    last_probe: dict[str, Any] = {}
    while time.time() < deadline:
        last_probe = probe(host, port, api_key)
        if last_probe.get("ok"):
            payload["openviking_pid"] = proc.pid
            payload["server_url"] = f"http://{host}:{port}"
            return {
                "workspace": str(workspace),
                "runtime_config": str(runtime_config),
                "server_log": str(log_file),
                "pid": proc.pid,
                "restarted": True,
                "probe": last_probe,
            }
        if proc.poll() is not None:
            break
        time.sleep(1.5)
    raise RuntimeError(f"OpenViking failed to start on {host}:{port}; see {log_file}; last probe={last_probe}")
