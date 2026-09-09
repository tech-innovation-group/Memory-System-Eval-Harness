"""从磁盘 case 目录重建部分验收报告，不发送任何压测请求。

用法：:

    python -m performance.targets.echomem.rebuild_report \
        --results-dir performance/targets/echomem/results/1788701530 \
        [--profile 4U8G] [--quick] [--timeout-s 7200]

压测进行中随时可执行：对每个已写完 summary.json 的 case 重建 run（磁盘
summary 缺 ``pr421_metric_coverage`` 时从 ``metrics_samples.csv`` 重算补挂），
合并已落盘的探针产物，评估 O1-O7 并渲染 ``objective-suite.json/html``。
部分 suite.json 写到 ``<results>/_partial/<profile>/``，不覆盖运行中的
``<suite>/suite.json``；完整报告由压测进程收尾时覆盖刷新。
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from pathlib import Path
from typing import Any

from performance.monitor import MetricsFrame
from performance.suite import _finalize_suite, _load_completed_run
from performance.targets.echomem.acceptance.evaluate import (
    evaluate_pr421_acceptance,
)
from performance.targets.echomem.acceptance.metrics import metric_coverage
from performance.targets.echomem.acceptance.objectives import (
    OBJECTIVES,
    objective_statuses,
)
from performance.targets.echomem.orchestrator.report import (
    write_objective_suite_html,
)
from performance.targets.echomem.orchestrator.suites import select_cases
from performance.util import now_iso

_FS_UNSAFE_CHARS = str.maketrans({c: "_" for c in '<>:"/\\|?*'})

# 已落盘探针产物文件名 -> suite 字段名（与 orchestrator/probes.py 产物一致）。
_PROBE_FILE_FIELDS = {
    "capability-probe.json": "capability_probe",
    "blackbox-contract-probe.json": "blackbox_contract_probe",
    "missing-cases.json": "missing_cases",
    "concurrent-commit.json": "concurrent_commit",
    "fault-isolation.json": "fault_isolation",
    "commit-recovery.json": "commit_recovery",
}


def _fs_safe_label(label: str) -> str:
    return label.translate(_FS_UNSAFE_CHARS)


def rebuild_metric_coverage(case_dir: Path) -> dict[str, Any] | None:
    """从 metrics_samples.csv 重算 PR421 B7 覆盖证据；无样本时返回 None。"""
    csv_path = case_dir / "metrics_samples.csv"
    if not csv_path.is_file():
        return None
    frames: list[MetricsFrame] = []
    with csv_path.open(encoding="utf-8") as handle:
        for ts, group_rows in itertools.groupby(
            csv.DictReader(handle), key=lambda r: r["ts"]
        ):
            samples: dict[str, list[tuple[dict[str, str], float]]] = {}
            for row in group_rows:
                labels = json.loads(row["labels"])
                samples.setdefault(row["metric"], []).append(
                    (labels, float(row["value"]))
                )
            frames.append(MetricsFrame(ts=float(ts), samples=samples))
    if not frames:
        return None
    monitor = type("_Monitor", (), {"frames": frames})()
    return metric_coverage(monitor, frames[0].ts, frames[-1].ts)


def collect_probe_artifacts(suite_dir: Path) -> dict[str, Any]:
    """把已落盘的探针产物合并进 suite（不重新执行探针）。"""
    artifacts: dict[str, Any] = {}
    for file_name, field in _PROBE_FILE_FIELDS.items():
        path = suite_dir / file_name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        artifacts[field] = {**payload, "path": str(path)}
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir", required=True,
        help="结果根目录（如 performance/targets/echomem/results/1788701530）",
    )
    parser.add_argument("--profile", default="", help="profile 名（默认取 _config 第一个）")
    parser.add_argument("--quick", action="store_true", help="原 run 为 quick 时使用 4u8g 目录")
    parser.add_argument("--timeout-s", type=float, default=7200.0, help="重建用的 case 超时")
    parser.add_argument(
        "--out-dir", default="",
        help="报告输出根（默认与 --results-dir 相同，覆盖 objective-suite.json/html）",
    )
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir).expanduser().resolve()
    config_dir = results_dir / "_config"
    profiles_path = config_dir / "instance-profiles.json"
    if not profiles_path.is_file():
        print(f"error: 未找到 {profiles_path}", file=sys.stderr)
        return 2
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))["profiles"]
    if args.profile:
        profiles = [p for p in profiles if p["name"] == args.profile]
    if not profiles:
        print(f"error: 没有匹配的 profile: {args.profile!r}", file=sys.stderr)
        return 2

    out_root = Path(args.out_dir).expanduser().resolve() if args.out_dir else results_dir
    profile_name = "4u8g" if args.quick else "complete"
    catalog = select_cases(profile_name, None)
    all_runs: list[dict[str, Any]] = []
    for profile in profiles:
        name = profile["name"]
        suite_dir = results_dir / name
        partial_dir = results_dir / "_partial" / name
        partial_dir.mkdir(parents=True, exist_ok=True)
        existing_suite_path = suite_dir / "suite.json"
        try:
            existing_suite = json.loads(existing_suite_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing_suite = {}
        model_preflight = existing_suite.get("preflight") or {}

        runs: list[dict[str, Any]] = []
        for case in catalog:
            case_dir = suite_dir / _fs_safe_label(case["label"])
            run = _load_completed_run(case, case_dir, args.timeout_s)
            if run is None:
                continue
            summary = run["summary"]
            details = summary.setdefault("details", {})
            if not details.get("pr421_metric_coverage"):
                coverage = rebuild_metric_coverage(case_dir)
                if coverage:
                    details["pr421_metric_coverage"] = coverage
            runs.append(run)

        manifest: dict[str, Any] = {
            "created_at": now_iso(),
            "base_url": str(profile.get("base_url") or "http://127.0.0.1:8010"),
            "profile": profile_name,
            "instance_profile": name,
            "tenant_config": str(profile.get("tenant_config") or ""),
            "preflight_config": str(profile.get("preflight_config") or ""),
            "allow_partial_tenants": bool(profile.get("allow_partial_tenants")),
            "metrics_enabled": bool(profile.get("metrics_enabled", True)),
            "resource_profile": profile.get("resource_profile") or {},
            "output_root": str(suite_dir.resolve()),
            "scenarios": [c["label"] for c in catalog],
            "repeats": 1,
            "policies": ["server-observe"],
            "duration_cap_s": 0.0,
            "server_observation_mode": True,
            "client_admission_enabled": False,
            "probe_artifacts": {},
            "runs": runs,
            "preflight": model_preflight,
        }
        manifest["acceptance"] = evaluate_pr421_acceptance(manifest)
        _finalize_suite(manifest, partial_dir)
        probe_artifacts = collect_probe_artifacts(suite_dir)
        suite = {**manifest, **probe_artifacts}

        completed_runs = sum(1 for r in runs if r.get("status") == "completed")
        submitted_runs = len(runs)
        profile_execution_status = "completed" if completed_runs > 0 else "not_run"
        output_profiles_entry = {
            **profile,
            **probe_artifacts,  # 探针执行结果覆盖同名配置字段（渲染读 profile 顶层）
            "memory_leak": manifest.get("memory_leak"),
            "name": name,
            "suite": str(partial_dir / "suite.json"),
            "profile_execution_status": profile_execution_status,
            "completed_runs": completed_runs,
            "submitted_runs": submitted_runs,
            "model_preflight": model_preflight,
            "objectives": objective_statuses({
                **suite,
                "profile_name": name,
                "instance_profiles": [{
                    "name": name,
                    "status": profile_execution_status,
                    "completed_runs": completed_runs,
                }],
            }),
        }
        all_runs.extend(runs)
        result = {
            "created_at": now_iso(),
            "profiles": [output_profiles_entry],
            "objectives": OBJECTIVES,
            "instance_profiles": [{
                "name": name,
                "status": profile_execution_status,
                "completed_runs": completed_runs,
                "submitted_runs": submitted_runs,
            }],
            "multi_spec_completed_count": 1 if completed_runs > 0 else 0,
            "real_model_preflight_complete": bool(model_preflight.get("ok")),
        }
        (out_root / "objective-suite.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_objective_suite_html(result, out_root / "objective-suite.html")
        print(f"[rebuild] {name}: {completed_runs}/{len(catalog)} cases completed")
        print(f"[rebuild] partial suite: {partial_dir / 'suite.json'}")
        print(f"[rebuild] report: {out_root / 'objective-suite.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
