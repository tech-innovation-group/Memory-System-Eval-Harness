from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable


SafePath = Callable[[str], Path]
DatasetOverview = Callable[[Path], dict[str, Any]]


def _add(checks: list[dict[str, Any]], name: str, ok: bool, message: str) -> None:
    checks.append({"name": name, "ok": ok, "message": message})


def _validate_dataset(
    checks: list[dict[str, Any]],
    data: Path,
    dataset_overview: DatasetOverview,
) -> None:
    _add(checks, "dataset", data.exists(), str(data))
    if not data.exists():
        return
    try:
        overview = dataset_overview(data)
        _add(checks, "dataset_json", True, f"{overview['samples']} samples / {overview['questions']} questions")
        _add(checks, "dataset_runner", True, overview.get("runner_note") or "local agent ready")
    except Exception as exc:
        _add(checks, "dataset_json", False, str(exc))


def _validate_judge_csv(checks: list[dict[str, Any]], input_file: Path) -> None:
    _add(checks, "judge_input", input_file.exists(), str(input_file) if str(input_file) else "missing result CSV")
    if not input_file.exists():
        return
    try:
        with input_file.open(newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            fields = set(reader.fieldnames or [])
            rows = sum(1 for _ in reader)
        required = {"question", "answer", "response"}
        missing = sorted(required - fields)
        message = f"{rows} rows; missing {', '.join(missing)}" if missing else f"{rows} rows; required columns present"
        _add(checks, "judge_csv_schema", not missing, message)
        judge_fields = {"result", "reasoning"} & fields
        _add(
            checks,
            "judge_columns",
            True,
            "existing result/reasoning columns" if judge_fields else "result/reasoning will be added before Judge",
        )
    except Exception as exc:
        _add(checks, "judge_csv_schema", False, str(exc))


def validate_payload(
    payload: dict[str, Any],
    default_data: Path,
    default_output_dir: Path,
    safe_path: SafePath,
    dataset_overview: DatasetOverview,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    kind = str(payload.get("kind") or "")
    runner = str(payload.get("runner") or "local_agent")
    if kind in {"distributed"} or runner != "local_agent":
        _add(checks, "runner", False, "外部 runner 已移除；请使用 MemoryBench 本地基线或 OpenViking QA")

    data = safe_path(str(payload.get("data") or str(default_data)))
    _validate_dataset(checks, data, dataset_overview)

    if kind == "judge":
        input_file = safe_path(str(payload.get("input") or ""))
        _validate_judge_csv(checks, input_file)

    _add(checks, "local_agent", True, "MemoryBench 本地基线可运行；不需要外部 runner")
    output_dir = safe_path(str(payload.get("output_dir") or str(default_output_dir)))
    _add(checks, "output_dir", output_dir.exists() or output_dir.parent.exists(), str(output_dir))
    return {"ok": all(item["ok"] for item in checks), "checks": checks}
