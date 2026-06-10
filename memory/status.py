from __future__ import annotations

from datetime import datetime
from typing import Any


def recent_run_view(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": run.get("id"),
            "name": run.get("name"),
            "kind": run.get("kind"),
            "status": run.get("status"),
            "manifest_status": run.get("manifest_status"),
            "stale_running": run.get("stale_running"),
            "recoverable": run.get("recoverable"),
            "status_reason": run.get("status_reason"),
            "recovery_hint": run.get("recovery_hint"),
            "created_at": run.get("created_at"),
            "duration_s": run.get("duration_s"),
            "output_file": run.get("output_file"),
            "run_dir": run.get("run_dir"),
        }
        for run in runs
    ]


def dataset_view(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "path": item.get("path"),
            "exists": item.get("exists"),
            "format": item.get("format"),
            "samples": item.get("samples"),
            "questions": item.get("questions"),
            "lazy": item.get("lazy"),
        }
        for item in datasets
    ]


def build_health_status(
    *,
    service: str,
    version: str,
    root: str,
    static: str,
    runs_dir: str,
    default_dataset: str,
    datasets: list[dict[str, Any]],
    running_tasks: list[dict[str, Any]],
    recent_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": service,
        "version": version,
        "time": datetime.now().isoformat(timespec="seconds"),
        "root": root,
        "static": static,
        "runs_dir": runs_dir,
        "default_dataset": default_dataset,
        "datasets": dataset_view(datasets),
        "running_tasks": running_tasks,
        "recent_runs": recent_run_view(recent_runs),
    }
