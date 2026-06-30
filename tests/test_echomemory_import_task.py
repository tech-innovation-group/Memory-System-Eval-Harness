from __future__ import annotations

from pathlib import Path

from memory.plugins.echomemory.tasks import build_echomemory_import_command


def _safe_path(value: str) -> Path:
    return Path(value)


def test_develop_full_import_uses_shorter_staged_full_wait_defaults(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    root = tmp_path / "repo"
    develop_root = tmp_path / "EchoMem_develop"
    (develop_root / "src" / "echomem").mkdir(parents=True)
    (develop_root / "src" / "echo0").mkdir(parents=True)
    (develop_root / "pyproject.toml").write_text("", encoding="utf-8")
    default_data = tmp_path / "dataset.json"
    payload = {
        "data": str(default_data),
        "workspace": str(tmp_path / "workspace"),
        "echomem_root": str(develop_root),
    }

    spec = build_echomemory_import_command(
        payload,
        run_dir,
        root,
        default_data,
        safe_path=_safe_path,
    )

    command = spec.command
    assert command[command.index("--import-wait-mode") + 1] == "full"
    assert command[command.index("--commit-wait-s") + 1] == "12"
    assert command[command.index("--flush-call-timeout-s") + 1] == "20"
    assert command[command.index("--flush-attempts") + 1] == "1"


def test_develop_full_import_ignores_fast_override_timeouts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    root = tmp_path / "repo"
    develop_root = tmp_path / "EchoMem_develop"
    (develop_root / "src" / "echomem").mkdir(parents=True)
    (develop_root / "src" / "echo0").mkdir(parents=True)
    (develop_root / "pyproject.toml").write_text("", encoding="utf-8")
    default_data = tmp_path / "dataset.json"
    payload = {
        "data": str(default_data),
        "workspace": str(tmp_path / "workspace"),
        "echomem_root": str(develop_root),
        "import_wait_mode": "fast",
        "defer_artifact_wait": True,
        "commit_wait_s": 8,
        "flush_call_timeout_s": 15,
        "flush_attempts": 0,
    }

    spec = build_echomemory_import_command(
        payload,
        run_dir,
        root,
        default_data,
        safe_path=_safe_path,
    )

    command = spec.command
    assert command[command.index("--import-wait-mode") + 1] == "full"
    assert command[command.index("--commit-wait-s") + 1] == "12"
    assert command[command.index("--flush-call-timeout-s") + 1] == "20"
    assert command[command.index("--flush-attempts") + 1] == "1"
