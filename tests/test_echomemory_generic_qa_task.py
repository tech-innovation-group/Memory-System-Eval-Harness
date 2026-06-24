from __future__ import annotations

from pathlib import Path

from memory.plugins.echomemory.tasks import build_echomemory_generic_qa_command


def _safe_path(value: str) -> Path:
    return Path(value)


def _resolve_token(_payload: dict, _config: Path) -> str:
    return ""


def test_hotpotqa_generic_qa_forces_full_wait(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    root = tmp_path / "repo"
    config = tmp_path / "config.json"
    default_data = tmp_path / "dataset.json"
    payload = {
        "dataset_format": "hotpotqa",
        "data": str(default_data),
        "workspace": str(tmp_path / "workspace"),
        "defer_artifact_wait": True,
        "import_wait_mode": "fast",
    }

    spec = build_echomemory_generic_qa_command(
        payload,
        run_dir,
        config,
        root,
        default_data,
        defaults={},
        safe_path=_safe_path,
        resolve_judge_token=_resolve_token,
    )

    command = spec.command
    assert "--import-wait-mode" in command
    wait_mode = command[command.index("--import-wait-mode") + 1]
    assert wait_mode == "full"
    assert "--defer-artifact-wait" not in command
    assert spec.metadata is not None
    assert spec.metadata["strict_ready_required"] is True


def test_develop_generic_qa_uses_shorter_staged_full_wait_defaults(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    root = tmp_path / "repo"
    config = tmp_path / "config.json"
    default_data = tmp_path / "dataset.json"
    develop_root = tmp_path / "EchoMem_develop"
    (develop_root / "src" / "echomem").mkdir(parents=True)
    (develop_root / "src" / "echo0").mkdir(parents=True)
    (develop_root / "pyproject.toml").write_text("", encoding="utf-8")
    payload = {
        "dataset_format": "locomo",
        "data": str(default_data),
        "workspace": str(tmp_path / "workspace"),
        "echomem_root": str(develop_root),
    }

    spec = build_echomemory_generic_qa_command(
        payload,
        run_dir,
        config,
        root,
        default_data,
        defaults={},
        safe_path=_safe_path,
        resolve_judge_token=_resolve_token,
    )

    command = spec.command
    assert command[command.index("--import-wait-mode") + 1] == "full"
    assert command[command.index("--commit-wait-s") + 1] == "12"
    assert command[command.index("--flush-call-timeout-s") + 1] == "20"
    assert command[command.index("--flush-attempts") + 1] == "1"
