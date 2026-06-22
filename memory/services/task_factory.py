from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from memory import task_specs as task_spec_service
from memory.plugins.service import plugin_service


@dataclass(frozen=True)
class TaskFactoryContext:
    root: Path
    default_data: Path
    safe_path: Callable[..., Any]
    infer_dataset_format: Callable[..., Any]
    load_ov_defaults: Callable[..., Any]
    resolve_judge_token: Callable[..., Any]
    ensure_judge_columns: Callable[..., Any]


def build_single_command(
    kind: str,
    payload: dict[str, Any],
    run_dir: Path,
    config: Path,
    *,
    context: TaskFactoryContext,
) -> tuple[list[str], str, str]:
    if kind == "adapter":
        task_spec = task_spec_service.build_adapter_task(
            payload,
            run_dir,
            context.root,
            context.default_data,
            context.safe_path,
            context.infer_dataset_format,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "local_agent":
        task_spec = task_spec_service.build_local_agent_task(
            payload,
            run_dir,
            context.root,
            context.default_data,
            context.safe_path,
            context.infer_dataset_format,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "openviking_qa":
        defaults = context.load_ov_defaults(config)
        task_spec = plugin_service.build_locomo_qa_task(
            "openviking",
            payload,
            run_dir,
            config,
            context.root,
            context.default_data,
            defaults,
            context.safe_path,
            context.resolve_judge_token,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "openviking_import":
        task_spec = plugin_service.build_locomo_import_task("openviking", payload, run_dir, context.root, context.default_data, context.safe_path)
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "openviking_generic_qa":
        defaults = context.load_ov_defaults(config)
        task_spec = plugin_service.build_generic_qa_task(
            "openviking",
            payload,
            run_dir,
            config,
            context.root,
            context.default_data,
            defaults,
            context.safe_path,
            context.resolve_judge_token,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "echomemory_qa":
        defaults = context.load_ov_defaults(config)
        task_spec = plugin_service.build_locomo_qa_task(
            "echomemory",
            payload,
            run_dir,
            config,
            context.root,
            context.default_data,
            defaults,
            context.safe_path,
            context.resolve_judge_token,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "echomemory_generic_qa":
        defaults = context.load_ov_defaults(config)
        task_spec = plugin_service.build_generic_qa_task(
            "echomemory",
            payload,
            run_dir,
            config,
            context.root,
            context.default_data,
            defaults,
            context.safe_path,
            context.resolve_judge_token,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "echomemory_import":
        task_spec = plugin_service.build_locomo_import_task("echomemory", payload, run_dir, context.root, context.default_data, context.safe_path)
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "echomemory_qa_retry_failed":
        task_spec = task_spec_service.build_echomemory_qa_retry_failed_task(
            payload,
            run_dir,
            context.root,
            context.default_data,
            context.safe_path,
            config,
            context.resolve_judge_token,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "openviking_qa_retry_failed":
        task_spec = task_spec_service.build_openviking_qa_retry_failed_task(
            payload,
            run_dir,
            context.root,
            context.default_data,
            context.safe_path,
            config,
            context.resolve_judge_token,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "openviking_qa_retry_missing":
        task_spec = task_spec_service.build_openviking_qa_retry_missing_task(
            payload,
            run_dir,
            context.root,
            context.default_data,
            context.safe_path,
            config,
            context.resolve_judge_token,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "judge":
        task_spec = task_spec_service.build_judge_task(
            payload,
            config,
            context.root,
            context.safe_path,
            context.load_ov_defaults,
            context.resolve_judge_token,
            context.ensure_judge_columns,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    if kind == "stats":
        task_spec = task_spec_service.build_stats_task(
            payload,
            context.root,
            context.safe_path,
        )
        return task_spec.command, task_spec.output_file, task_spec.name

    raise ValueError(f"unknown task kind: {kind}")
