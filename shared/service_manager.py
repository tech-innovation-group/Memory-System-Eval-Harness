"""Start and stop an external EchoMem CLI service for one-command runs."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class ManagedService:
    process: subprocess.Popen
    log_path: Path
    pid_path: Path


def _log_tail(log_path: Path, lines: int = 30) -> str:
    try:
        return "\n".join(
            log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        )
    except Exception:
        return ""


def _healthy(base_url: str, timeout_s: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=timeout_s) as response:
            return response.status == 200
    except Exception:
        return False


def _server_address(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"invalid ECHOMEM_BASE_URL: {base_url}")
    if parsed.scheme != "http":
        raise ValueError("automatic EchoMem startup only supports a local http:// URL")
    return parsed.hostname, parsed.port or 80


def _seed_template_embedding_cache(workspace: Path, config_path: Path | None) -> None:
    """Reuse provider-specific recall template vectors from the config workspace."""
    if config_path is None:
        return
    relative_path = Path("cache/recall/spec_template_embeddings.json")
    source = config_path.parent / relative_path
    destination = workspace / relative_path
    if source.exists() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _validate_echomem_resources(echomem_root: Path) -> None:
    """Fail early when the checkout is missing runtime prompt resources."""
    required = (
        echomem_root
        / "src"
        / "echomem"
        / "index_engine"
        / "engine"
        / "atomic_engine"
        / "core"
        / "resources"
        / "prompts"
        / "atomic"
        / "core_rules.txt"
    )
    if not required.is_file() or required.stat().st_size == 0:
        raise RuntimeError(
            "EchoMem source is incomplete: missing "
            f"{required}. Sync the full repository, including "
            "src/echomem/**/core/resources, before starting the service."
        )


def _child_environment(echomem_root: Path) -> dict[str, str]:
    """Prefer the current checkout over a stale globally installed package."""
    child_env = os.environ.copy()
    source_dir = str(echomem_root / "src")
    existing = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = (
        f"{source_dir}{os.pathsep}{existing}" if existing else source_dir
    )
    child_env["PYTHONUNBUFFERED"] = "1"
    return child_env


def start_echomem_service(project_root: str | Path, timeout_s: float = 180.0) -> ManagedService | None:
    base_url = os.environ.get("ECHOMEM_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    if _healthy(base_url):
        return None

    echomem_root_text = os.environ.get("ECHOMEM_ROOT", "").strip()
    workspace_text = os.environ.get("ECHOMEM_WORKSPACE", "").strip()
    if not echomem_root_text:
        raise RuntimeError("EchoMem is offline and ECHOMEM_ROOT is not configured")
    if not workspace_text:
        raise RuntimeError("EchoMem is offline and ECHOMEM_WORKSPACE is not configured")

    echomem_root = Path(echomem_root_text).expanduser().resolve()
    workspace = Path(workspace_text).expanduser().resolve()
    python_bin = echomem_root / ".venv" / "bin" / "python"
    if not python_bin.exists():
        raise RuntimeError(f"EchoMem Python not found: {python_bin}")
    _validate_echomem_resources(echomem_root)
    child_env = _child_environment(echomem_root)

    config_text = os.environ.get("ECHOMEM_CONFIG", "").strip()
    config_path = Path(config_text).expanduser().resolve() if config_text else None
    workspace.mkdir(parents=True, exist_ok=True)
    if not (workspace / "config.json").exists():
        init_command = [
            str(python_bin),
            "-m",
            "echomem.entrypoints.cli",
            "init",
            "--workspace",
            str(workspace),
        ]
        if config_path is not None:
            if not config_path.exists():
                raise RuntimeError(f"EchoMem config not found: {config_path}")
            init_command.extend(["--config", str(config_path)])
        try:
            subprocess.run(init_command, cwd=echomem_root, env=child_env, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"EchoMem workspace initialization failed: {exc}") from exc
    _seed_template_embedding_cache(workspace, config_path)

    host, port = _server_address(base_url)
    runtime_dir = Path(project_root).resolve() / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / f"echomem-{port}.log"
    pid_path = runtime_dir / f"echomem-{port}.pid"
    command = [
        str(python_bin),
        "-m",
        "echomem.entrypoints.cli",
        "server",
        "--host",
        host,
        "--port",
        str(port),
        "--workspace",
        str(workspace),
    ]
    log_file = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=echomem_root,
            env=child_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"EchoMem server could not be started: {exc}") from exc
    finally:
        log_file.close()
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")

    deadline = time.monotonic() + max(1.0, timeout_s)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = _log_tail(log_path)
            try:
                pid_path.unlink()
            except FileNotFoundError:
                pass
            raise RuntimeError(
                f"EchoMem exited during startup with code {process.returncode}. "
                f"Log: {log_path}\n{tail}"
            )
        if _healthy(base_url):
            return ManagedService(process=process, log_path=log_path, pid_path=pid_path)
        time.sleep(0.5)

    stop_echomem_service(ManagedService(process=process, log_path=log_path, pid_path=pid_path))
    tail = _log_tail(log_path)
    detail = f"\nLast log lines:\n{tail}" if tail else ""
    raise RuntimeError(
        f"EchoMem did not become healthy within {timeout_s:g}s. Log: {log_path}. "
        "Startup may be waiting for its configured model or embedding provider."
        f"{detail}"
    )


def stop_echomem_service(service: ManagedService | None) -> None:
    if service is None:
        return
    process = service.process
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                pass
    try:
        service.pid_path.unlink()
    except FileNotFoundError:
        pass
