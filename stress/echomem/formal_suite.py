#!/usr/bin/env python3
"""Run a repeatable, real multi-tenant EchoMem stress suite.

The suite is deliberately orchestration-only: each case is executed by the
existing runner, which keeps per-request CSV and raw server telemetry. The
suite adds scenario/repetition metadata and refuses to make a release claim
unless every run has independent tenant credentials.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POLICIES = (
    "fifo",
    "search-priority",
    "dual-lane",
    "tenant-fair",
    "dual-lane-tenant-fair",
)

SCENARIOS: dict[str, dict[str, Any]] = {
    "baseline": {
        "label": "单租户基线",
        "tenants": 1,
        "duration_s": 600,
        "search_rps": 2.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "mixed": {
        "label": "四租户均衡混合负载",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 8.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "commit-storm": {
        "label": "Commit 压力",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 4.0,
        "commit_rpm": 10.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "search-storm": {
        "label": "Search 压力",
        "tenants": 4,
        "duration_s": 600,
        "search_rps": 20.0,
        "commit_rpm": 1.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
    "soak": {
        "label": "长稳态",
        "tenants": 4,
        "duration_s": 1800,
        "search_rps": 8.0,
        "commit_rpm": 2.0,
        "sessions_per_tenant": 4,
        "messages_per_session": 3,
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tenants(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tenants = payload.get("tenants") if isinstance(payload, dict) else payload
    if not isinstance(tenants, list) or not tenants:
        raise ValueError("tenant config must contain a non-empty tenants list")
    return tenants


def write_subset(path: Path, tenants: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"tenants": tenants}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_case(
    runner: Path,
    case_root: Path,
    scenario: str,
    repetition: int,
    policy: str,
    config_path: Path,
    args: argparse.Namespace,
    case: dict[str, Any],
) -> dict[str, Any]:
    output = case_root / scenario / f"repeat-{repetition:02d}" / policy
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(runner),
        "--base-url",
        args.base_url,
        "--tenant-config",
        str(config_path),
        "--tenants",
        str(case["tenants"]),
        "--scheduler-policy",
        policy,
        "--duration-s",
        str(case["duration_s"]),
        "--search-rps",
        str(case["search_rps"]),
        "--commit-rpm",
        str(case["commit_rpm"]),
        "--sessions-per-tenant",
        str(case["sessions_per_tenant"]),
        "--messages-per-session",
        str(case["messages_per_session"]),
        "--commit-workers",
        str(args.commit_workers),
        "--search-workers",
        str(args.search_workers),
        "--search-admission-capacity",
        str(args.search_admission_capacity),
        "--commit-admission-capacity",
        str(args.commit_admission_capacity),
        "--admission-capacity",
        str(args.admission_capacity),
        "--out-dir",
        str(output),
    ]
    if args.auth_header:
        command += ["--auth-header", args.auth_header]
    if args.pid:
        command += ["--pid", str(args.pid)]
    if args.no_server_metrics:
        command.append("--no-server-metrics")
    if args.no_client_admission:
        command.append("--no-client-admission")
    if args.reset_command:
        completed_reset = subprocess.run(
            args.reset_command,
            shell=True,
            text=True,
            capture_output=True,
        )
        (output / "reset.stdout.log").write_text(
            completed_reset.stdout, encoding="utf-8"
        )
        (output / "reset.stderr.log").write_text(
            completed_reset.stderr, encoding="utf-8"
        )
        if completed_reset.returncode != 0:
            return {
                "scenario": scenario,
                "repetition": repetition,
                "policy": policy,
                "status": "RESET_FAILED",
                "returncode": completed_reset.returncode,
                "output_dir": str(output.resolve()),
            }
    completed = subprocess.run(command, text=True, capture_output=True)
    (output / "suite_runner.stdout.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    (output / "suite_runner.stderr.log").write_text(
        completed.stderr, encoding="utf-8"
    )
    summary_path = output / "summary.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "scenario": scenario,
        "scenario_label": case["label"],
        "repetition": repetition,
        "policy": policy,
        "status": summary.get("status", "NO_SUMMARY"),
        "runner_returncode": completed.returncode,
        "output_dir": str(output.resolve()),
        "summary": summary,
    }


def render_report(manifest: dict[str, Any], output_path: Path) -> None:
    import html

    runs = manifest.get("runs") or []
    rows = []
    for run in runs:
        summary = run.get("summary") or {}
        metrics = summary.get("metrics") or {}
        commit = metrics.get("commit") or {}
        search = metrics.get("search") or {}
        c = commit.get("completion") or {}
        s = search.get("latency") or {}
        details = summary.get("details") or {}
        isolation = details.get("isolation") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(run.get('scenario')))}</td>"
            f"<td>{html.escape(str(run.get('repetition')))}</td>"
            f"<td><b>{html.escape(str(run.get('policy')))}</b></td>"
            f"<td>{html.escape(str(run.get('status')))}</td>"
            f"<td>{html.escape(str(commit.get('completed', 0)))} / {html.escape(str(commit.get('submitted', 0)))}</td>"
            f"<td>{fmt_seconds(c.get('mean_s'))}</td><td>{fmt_seconds(c.get('p95_s'))}</td><td>{fmt_seconds(c.get('p99_s'))}</td>"
            f"<td>{html.escape(str(search.get('succeeded', 0)))} / {html.escape(str(search.get('submitted', 0)))}</td>"
            f"<td>{fmt_seconds(s.get('mean_s'))}</td><td>{fmt_seconds(s.get('p95_s'))}</td>"
            f"<td>{html.escape(str((metrics.get('admission') or {}).get('max_queue_depth', 0)))}</td>"
            f"<td>{html.escape(str(isolation.get('status', '未提供')))}</td>"
            f"<td><a href='{html.escape(str(Path(run.get('output_dir', '')).relative_to(Path(manifest['output_root'])) / 'report.html'))}'>详情</a></td>"
            "</tr>"
        )
    if not runs:
        rows.append("<tr><td colspan='14'>没有执行记录</td></tr>")
    aggregates = aggregate_runs(runs)
    aggregate_rows = []
    aggregate_details = []
    for item in aggregates:
        aggregate_rows.append(
            "<tr>"
            f"<td>{html.escape(item['scenario'])}</td><td><b>{html.escape(item['policy'])}</b></td>"
            f"<td>{item['repetitions']}</td>"
            f"<td>{item['commit_completed']} / {item['commit_submitted']}</td>"
            f"<td>{fmt_seconds(item['commit_mean'])}</td><td>{fmt_seconds(item['commit_p50'])}</td>"
            f"<td>{fmt_seconds(item['commit_p90'])}</td><td>{fmt_seconds(item['commit_p95'])}</td>"
            f"<td>{fmt_seconds(item['commit_p99'])}</td><td>{fmt_seconds(item['commit_max'])}</td>"
            f"<td>{item['commit_delayed']}</td>"
            f"<td>{item['search_succeeded']} / {item['search_submitted']}</td>"
            f"<td>{fmt_seconds(item['search_mean'])}</td><td>{fmt_seconds(item['search_p50'])}</td>"
            f"<td>{fmt_seconds(item['search_p90'])}</td><td>{fmt_seconds(item['search_p95'])}</td>"
            f"<td>{fmt_seconds(item['search_p99'])}</td><td>{fmt_seconds(item['search_max'])}</td>"
            f"<td>{item['search_delayed']}</td><td>{item['rate_limited']}</td>"
            "</tr>"
        )
        tenant_lines = []
        for tenant, values in sorted(item["tenant_rows"].items()):
            commit_means = values["commit"]
            search_means = values["search"]
            tenant_lines.append(
                f"<tr><td>{html.escape(tenant)}</td>"
                f"<td>{values['commit_completed']} / {values['commit_submitted']}</td>"
                f"<td>{fmt_seconds(statistics.mean(commit_means) if commit_means else None)}</td>"
                f"<td>{fmt_seconds(percentile(commit_means, 50))}</td>"
                f"<td>{fmt_seconds(percentile(commit_means, 95))}</td>"
                f"<td>{fmt_seconds(max(commit_means) if commit_means else None)}</td>"
                f"<td>{values['commit_delayed']}</td>"
                f"<td>{values['search_succeeded']} / {values['search_submitted']}</td>"
                f"<td>{fmt_seconds(statistics.mean(search_means) if search_means else None)}</td>"
                f"<td>{fmt_seconds(percentile(search_means, 50))}</td>"
                f"<td>{fmt_seconds(percentile(search_means, 95))}</td>"
                f"<td>{fmt_seconds(percentile(search_means, 99))}</td>"
                f"<td>{fmt_seconds(max(search_means) if search_means else None)}</td>"
                f"<td>{values['search_delayed']}</td></tr>"
            )
        aggregate_details.append(
            f"<details><summary>{html.escape(item['scenario'])} · {html.escape(item['policy'])} · "
            f"逐租户汇总</summary><div class='scroll'><table><thead><tr>"
            f"<th>租户</th><th>Commit 完成/提交</th><th>Commit 平均</th><th>Commit P50</th>"
            f"<th>Commit P95</th><th>Commit 最大</th><th>Commit 延迟</th>"
            f"<th>Search 成功/提交</th><th>Search 平均</th><th>Search P95</th>"
            f"<th>Search P50</th><th>Search P99</th><th>Search 最大</th>"
            f"<th>Search 延迟</th></tr></thead><tbody>"
            f"{''.join(tenant_lines) or '<tr><td colspan=14>没有逐租户数据</td></tr>'}"
            "</tbody></table></div></details>"
        )
    icon = (
        "<svg class='logo' viewBox='0 0 56 56' role='img' aria-label='压测报告'>"
        "<rect x='3' y='3' width='50' height='50' rx='13' fill='#17324d'/>"
        "<path d='M13 40V29M22 40V20M31 40V25M40 40V13' stroke='#72d5b7' stroke-width='4' stroke-linecap='round'/>"
        "<path d='M11 44h34M12 17l8-5 8 6 12-9' fill='none' stroke='#ff9d6e' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/></svg>"
    )
    document = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>EchoMem 正式多租户压测套件</title>
<style>
:root{{--bg:#f3f6f7;--paper:#fff;--ink:#17212b;--muted:#6d7b87;--line:#dce4e8;--green:#177b63;--amber:#9b6b16;--amber-bg:#fff7df}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1500px;margin:auto;padding:28px 20px 60px}}.top{{display:flex;align-items:center;gap:14px;margin-bottom:18px}}.logo{{width:52px;height:52px}}
h1{{margin:0;font-size:25px}}h2{{font-size:18px;margin:0 0 10px}}.muted,small{{color:var(--muted)}}.section{{background:var(--paper);border:1px solid var(--line);padding:18px 19px;margin-top:12px}}
.notice{{padding:12px 14px;background:var(--amber-bg);border-left:4px solid var(--amber);color:#6d5116;margin-bottom:12px}}
.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}}th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#fafbfc;color:var(--muted)}}a{{color:#286aa6}}
</style></head><body><main class='page'><header class='top'>{icon}<div><h1>EchoMem 正式多租户压测套件</h1><small>真实 HTTP / 真实模型 · {html.escape(manifest.get('created_at', ''))}</small></div></header>
<section class='section'><div class='notice'><b>重要边界：</b>本套件的策略控制压测端准入，不自动等同于 EchoMem 内部限流。只有服务端提供队列、429、Retry-After 和执行时间遥测，才能确认服务端调度。</div>
<p>正式结果要求每次运行使用独立认证租户。当前套件包含单租户基线、四租户均衡、Commit 压力、Search 压力和长稳态场景；每个场景按策略重复执行，保留全部逐请求 CSV、原始 /metrics 和独立报告。</p></section>
<section class='section'><h2>运行配置</h2><p class='muted'>服务：<code>{html.escape(str(manifest.get('base_url')))}</code> · 重复轮次：{html.escape(str(manifest.get('repeats')))} · 策略数：{len(POLICIES)}</p>
<div class='scroll'><table><thead><tr><th>场景</th><th>轮次</th><th>策略</th><th>状态</th><th>Commit 完成</th><th>Commit 平均</th><th>Commit P95</th><th>Commit P99</th><th>Search 成功</th><th>Search 平均</th><th>Search P95</th><th>最大队列</th><th>隔离</th><th>详情</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section class='section'><h2>跨轮次聚合数据</h2><p class='muted'>这里直接合并每轮 CSV 的请求级数据，避免只看单轮均值。延迟单位为秒；延迟数使用各轮配置的阈值。</p>
<div class='scroll'><table><thead><tr><th>场景</th><th>策略</th><th>轮次</th><th>Commit 完成/提交</th><th>Commit 平均</th><th>Commit P50</th><th>Commit P90</th><th>Commit P95</th><th>Commit P99</th><th>Commit 最大</th><th>Commit 延迟数</th><th>Search 成功/提交</th><th>Search 平均</th><th>Search P50</th><th>Search P90</th><th>Search P95</th><th>Search P99</th><th>Search 最大</th><th>Search 延迟数</th><th>429</th></tr></thead><tbody>{''.join(aggregate_rows) or '<tr><td colspan=20>没有可聚合数据</td></tr>'}</tbody></table></div>
{''.join(aggregate_details)}</section>
<section class='section'><h2>原始套件清单</h2><p class='muted'>完整参数与每轮输出目录记录在 <code>suite.json</code>。报告中的延迟单位为秒；失败请求不会被隐藏。</p></section>
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def fmt_seconds(value: Any) -> str:
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return "-"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def csv_values(path: Path, field: str) -> list[float]:
    if not path.is_file():
        return []
    values: list[float] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                value = float(row.get(field) or "")
            except (TypeError, ValueError):
                continue
            if value >= 0:
                values.append(value)
    return values


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def aggregate_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault(
            (str(run.get("scenario")), str(run.get("policy"))), []
        ).append(run)
    aggregates = []
    for (scenario, policy), group in sorted(groups.items()):
        commit_values: list[float] = []
        search_values: list[float] = []
        commit_submitted = commit_completed = commit_failed = 0
        search_submitted = search_succeeded = search_errors = 0
        commit_delayed = search_delayed = rate_limited = 0
        tenant_rows: dict[str, dict[str, list[float] | int]] = {}
        for run in group:
            summary = run.get("summary") or {}
            metrics = summary.get("metrics") or {}
            commit = metrics.get("commit") or {}
            search = metrics.get("search") or {}
            commit_submitted += int(commit.get("submitted") or 0)
            commit_completed += int(commit.get("completed") or 0)
            commit_failed += int(commit.get("failed") or 0)
            search_submitted += int(search.get("submitted") or 0)
            search_succeeded += int(search.get("succeeded") or 0)
            search_errors += int(search.get("errors") or 0)
            commit_delayed += int(commit.get("delayed_count") or 0)
            search_delayed += int(search.get("delayed_count") or 0)
            rate_limited += int(commit.get("rate_limited_count") or 0)
            rate_limited += int(search.get("rate_limited_count") or 0)
            out_dir = Path(run.get("output_dir", ""))
            commit_values.extend(csv_values(out_dir / "commit_results.csv", "end_to_end_s"))
            search_values.extend(csv_values(out_dir / "search_results.csv", "service_s"))
            # Per-run means are insufficient for a cross-run percentile. Use
            # raw request rows so a busy run cannot be underweighted.
            for row in read_rows(out_dir / "commit_results.csv"):
                tenant = str(row.get("tenant") or "-")
                target = tenant_rows.setdefault(
                    tenant,
                    {
                        "commit": [],
                        "search": [],
                        "commit_completed": 0,
                        "commit_submitted": 0,
                        "commit_delayed": 0,
                        "search_succeeded": 0,
                        "search_submitted": 0,
                        "search_delayed": 0,
                    },
                )
                target["commit_submitted"] += 1
                try:
                    commit_duration = float(
                        row.get("end_to_end_s") or row.get("elapsed_s") or 0
                    )
                except (TypeError, ValueError):
                    commit_duration = 0.0
                if str(row.get("status") or "") in {
                    "completed", "complete", "transcommit", "succeeded", "success"
                }:
                    target["commit"].append(commit_duration)
                    target["commit_completed"] += 1
                if commit_duration >= float(
                    (summary.get("parameters") or {}).get(
                        "commit_delay_threshold_s", 10.0
                    )
                ):
                    target["commit_delayed"] += 1
            for row in read_rows(out_dir / "search_results.csv"):
                tenant = str(row.get("tenant") or "-")
                target = tenant_rows.setdefault(
                    tenant,
                    {
                        "commit": [],
                        "search": [],
                        "commit_completed": 0,
                        "commit_submitted": 0,
                        "commit_delayed": 0,
                        "search_succeeded": 0,
                        "search_submitted": 0,
                        "search_delayed": 0,
                    },
                )
                target["search_submitted"] += 1
                try:
                    code = int(float(row.get("status_code") or 0))
                except (TypeError, ValueError):
                    code = 0
                if 200 <= code < 300:
                    target["search_succeeded"] += 1
                    try:
                        search_duration = float(
                            row.get("service_s") or row.get("elapsed_s") or 0
                        )
                        target["search"].append(search_duration)
                    except (TypeError, ValueError):
                        search_duration = 0.0
                    if search_duration >= float(
                        (summary.get("parameters") or {}).get(
                            "search_delay_threshold_s", 2.5
                        )
                    ):
                        target["search_delayed"] += 1
        aggregates.append(
            {
                "scenario": scenario,
                "policy": policy,
                "repetitions": len(group),
                "commit_submitted": commit_submitted,
                "commit_completed": commit_completed,
                "commit_failed": commit_failed,
                "commit_mean": statistics.mean(commit_values) if commit_values else None,
                "commit_p50": percentile(commit_values, 50),
                "commit_p90": percentile(commit_values, 90),
                "commit_p95": percentile(commit_values, 95),
                "commit_p99": percentile(commit_values, 99),
                "commit_max": max(commit_values) if commit_values else None,
                "search_submitted": search_submitted,
                "search_succeeded": search_succeeded,
                "search_errors": search_errors,
                "search_mean": statistics.mean(search_values) if search_values else None,
                "search_p50": percentile(search_values, 50),
                "search_p90": percentile(search_values, 90),
                "search_p95": percentile(search_values, 95),
                "search_p99": percentile(search_values, 99),
                "search_max": max(search_values) if search_values else None,
                "commit_delayed": commit_delayed,
                "search_delayed": search_delayed,
                "rate_limited": rate_limited,
                "tenant_rows": tenant_rows,
            }
        )
    return aggregates


def main() -> int:
    parser = argparse.ArgumentParser(description="Run formal real multi-tenant stress suite")
    parser.add_argument("--base-url", default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--tenant-config", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--scenarios", default="baseline,mixed,commit-storm,search-storm,soak")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--auth-header", default=os.getenv("ECHOMEM_AUTH_HEADER", "X-API-Key"))
    parser.add_argument("--commit-workers", type=int, default=8)
    parser.add_argument("--search-workers", type=int, default=32)
    parser.add_argument(
        "--no-client-admission",
        action="store_true",
        help="Observe EchoMem queueing without client-side admission scheduling.",
    )
    parser.add_argument("--admission-capacity", type=int, default=1)
    parser.add_argument("--search-admission-capacity", type=int, default=8)
    parser.add_argument("--commit-admission-capacity", type=int, default=1)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--reset-command", default="", help="Optional command run before every case")
    parser.add_argument("--no-server-metrics", action="store_true")
    args = parser.parse_args()

    scenario_names = [item.strip() for item in args.scenarios.split(",") if item.strip()]
    unknown = [item for item in scenario_names if item not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    tenant_path = Path(args.tenant_config).expanduser().resolve()
    all_tenants = load_tenants(tenant_path)
    required_tenants = max(SCENARIOS[name]["tenants"] for name in scenario_names)
    if len(all_tenants) < required_tenants:
        parser.error(
            f"tenant config has {len(all_tenants)} tenants, but selected scenarios require {required_tenants}"
        )
    root = Path(args.out_dir or f"results/stress/formal_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    root.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).with_name("runner.py")
    config_dir = root / "_tenant_configs"
    config_dir.mkdir(exist_ok=True)
    config_paths: dict[int, Path] = {}
    for count in sorted({SCENARIOS[name]["tenants"] for name in scenario_names}):
        config_paths[count] = config_dir / f"tenants-{count}.json"
        write_subset(config_paths[count], all_tenants[:count])

    manifest: dict[str, Any] = {
        "created_at": now_iso(),
        "base_url": args.base_url,
        "tenant_config": str(tenant_path),
        "output_root": str(root.resolve()),
        "scenarios": scenario_names,
        "repeats": args.repeats,
        "policies": list(POLICIES),
        "reset_command": args.reset_command,
        "client_admission_enabled": not args.no_client_admission,
        "server_observation_mode": bool(args.no_client_admission),
        "runs": [],
    }
    # Use a deterministic order so a rerun is easy to compare. The service
    # reset hook is the mechanism for keeping the data/index boundary fixed.
    for scenario in scenario_names:
        case = SCENARIOS[scenario]
        for repetition in range(1, args.repeats + 1):
            for policy in POLICIES:
                completed_runs = len(manifest["runs"])
                total_runs = len(scenario_names) * args.repeats * len(POLICIES)
                print(
                    f"FORMAL_PROGRESS {completed_runs}/{total_runs} "
                    f"scenario={scenario} repeat={repetition} policy={policy}",
                    flush=True,
                )
                run = run_case(
                    runner,
                    root,
                    scenario,
                    repetition,
                    policy,
                    config_paths[case["tenants"]],
                    args,
                    case,
                )
                manifest["runs"].append(run)
                print(
                    f"FORMAL_PROGRESS {len(manifest['runs'])}/{total_runs} "
                    f"scenario={scenario} repeat={repetition} policy={policy} "
                    f"status={run.get('status')}",
                    flush=True,
                )
                (root / "suite.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
    report_path = root / "suite.html"
    try:
        from .formal_data_report import render as render_data_report
    except ImportError:
        from formal_data_report import render as render_data_report
    render_data_report(root / "suite.json", report_path)
    statuses = [str(run.get("status") or "NO_SUMMARY") for run in manifest["runs"]]
    if any(status in {"ENVIRONMENT_ERROR", "RESET_FAILED", "NO_SUMMARY", "FAIL"} for status in statuses):
        overall = "FAIL"
    elif any(status == "INCONCLUSIVE" for status in statuses):
        overall = "INCONCLUSIVE"
    else:
        overall = "PASS"
    suite_summary = {
        "status": overall,
        "test_type": "formal_stress_suite",
        "base_url": args.base_url,
        "created_at": manifest["created_at"],
        "finished_at": now_iso(),
        "parameters": {
            "tenant_config": str(tenant_path),
            "scenarios": scenario_names,
            "repeats": args.repeats,
            "policies": list(POLICIES),
            "commit_workers": args.commit_workers,
            "search_workers": args.search_workers,
            "admission_capacity": args.admission_capacity,
            "search_admission_capacity": args.search_admission_capacity,
            "commit_admission_capacity": args.commit_admission_capacity,
        },
        "details": {
            "run_count": len(manifest["runs"]),
            "failed_runs": sum(status == "FAIL" for status in statuses),
            "inconclusive_runs": sum(status == "INCONCLUSIVE" for status in statuses),
            "environment_errors": sum(
                status in {"ENVIRONMENT_ERROR", "RESET_FAILED", "NO_SUMMARY"}
                for status in statuses
            ),
            "suite_report": "suite.html",
            "suite_manifest": "suite.json",
        },
        "aggregates": aggregate_runs(manifest["runs"]),
    }
    (root / "summary.json").write_text(
        json.dumps(suite_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report_path)
    return 0 if overall in {"PASS", "INCONCLUSIVE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
