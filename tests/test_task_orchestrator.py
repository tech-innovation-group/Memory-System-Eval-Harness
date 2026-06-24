from __future__ import annotations

from pathlib import Path

from memory.services.task_orchestrator import TaskOrchestratorContext, create_task


class DummyTask:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _safe_path(value: str) -> Path:
    return Path(value)


def _build_single_command(kind: str, payload: dict, run_dir: Path, config: Path):
    return ["/bin/echo", kind], str(run_dir / "out.txt"), f"{kind} task"


def test_echomemory_task_run_dir_includes_workspace_slug(tmp_path: Path) -> None:
    context = TaskOrchestratorContext(
        safe_path=_safe_path,
        default_repo=tmp_path / "repo",
        default_output_dir=tmp_path / "runs",
        default_config=tmp_path / "config.json",
        default_cli_config=tmp_path / "cli.json",
        resolve_judge_token=lambda payload, config: "",
        resolve_echomemory_runtime_env=lambda payload, config, judge_token: {
            "token": "embed-token",
            "chat_token": "chat-token",
        },
        skip_model_preflight=lambda payload: True,
        openai_compatible_chat_preflight=lambda *args, **kwargs: {"ok": True},
        ensure_task_model_preflight=lambda *args, **kwargs: None,
        now_slug=lambda: "20260623_202500",
        restart_openviking_for_workspace=lambda *args, **kwargs: {},
        prepare_connection_files=lambda payload, run_dir, config, cli_config: (config, cli_config),
        redact_manifest_payload=lambda payload: payload,
        build_single_command=_build_single_command,
        build_pipeline_script=lambda payload, run_dir, config: ([], "", ""),
        build_distributed_script=lambda payload, run_dir, config, cli_config: ([], "", ""),
        task_cls=DummyTask,
        redacted_command=lambda command: command,
        write_manifest=lambda task, payload, run_dir: None,
        register_task=lambda task: None,
        start_task_thread=lambda task: None,
    )

    task = create_task(
        "echomemory_import",
        {
            "workspace": str(tmp_path / "echomem_workspace_conv30-clean_20260623-181956"),
        },
        context=context,
        find_duplicate_active_task=lambda kind, payload: None,
        find_conflicting_active_locomo_qa=lambda kind, payload: None,
        duplicate_error_cls=RuntimeError,
        conflict_error_cls=RuntimeError,
    )

    run_dir_name = Path(task.run_dir).name
    assert run_dir_name.startswith("echomemory_import_20260623_202500_")
    assert "conv30-clean-20260623-181956" in run_dir_name


def test_openviking_task_normalizes_account_dir_workspace(tmp_path: Path) -> None:
    context = TaskOrchestratorContext(
        safe_path=_safe_path,
        default_repo=tmp_path / "repo",
        default_output_dir=tmp_path / "runs",
        default_config=tmp_path / "config.json",
        default_cli_config=tmp_path / "cli.json",
        resolve_judge_token=lambda payload, config: "",
        resolve_echomemory_runtime_env=lambda payload, config, judge_token: {},
        skip_model_preflight=lambda payload: True,
        openai_compatible_chat_preflight=lambda *args, **kwargs: {"ok": True},
        ensure_task_model_preflight=lambda *args, **kwargs: None,
        now_slug=lambda: "20260623_202500",
        restart_openviking_for_workspace=lambda *args, **kwargs: {},
        prepare_connection_files=lambda payload, run_dir, config, cli_config: (config, cli_config),
        redact_manifest_payload=lambda payload: payload,
        build_single_command=_build_single_command,
        build_pipeline_script=lambda payload, run_dir, config: ([], "", ""),
        build_distributed_script=lambda payload, run_dir, config, cli_config: ([], "", ""),
        task_cls=DummyTask,
        redacted_command=lambda command: command,
        write_manifest=lambda task, payload, run_dir: None,
        register_task=lambda task: None,
        start_task_thread=lambda task: None,
    )

    workspace = tmp_path / "openviking_workspace_demo"
    account_dir = workspace / "viking" / "acct"
    task = create_task(
        "openviking_qa",
        {
            "workspace": str(account_dir),
            "account": "acct",
        },
        context=context,
        find_duplicate_active_task=lambda kind, payload: None,
        find_conflicting_active_locomo_qa=lambda kind, payload: None,
        duplicate_error_cls=RuntimeError,
        conflict_error_cls=RuntimeError,
    )

    assert task.meta["config"]["workspace"] == str(workspace)
