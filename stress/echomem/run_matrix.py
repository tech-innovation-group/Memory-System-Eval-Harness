#!/usr/bin/env python3
"""Run the real EchoMem workload under several admission policies.

This compares load-generator admission policies. It does not infer EchoMem's
internal scheduler unless the service exposes equivalent queue telemetry.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .detailed_matrix_report import render as render_detailed_matrix_report
    from .audit_matrix_report import render as render_audit_matrix_report
except ImportError:
    from detailed_matrix_report import render as render_detailed_matrix_report
    from audit_matrix_report import render as render_audit_matrix_report


POLICIES = (
    "fifo",
    "search-priority",
    "dual-lane",
    "tenant-fair",
    "dual-lane-tenant-fair",
)


def fmt(value: Any, suffix: str = "s") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "-"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else "-"))


def render_report_safely(renderer: Any, matrix_path: Path, output_path: Path) -> None:
    """Never turn a completed workload into a failed run because of HTML rendering."""
    try:
        renderer(matrix_path, output_path)
    except Exception as exc:
        output_path.write_text(
            "<!doctype html><meta charset='utf-8'><title>报告生成异常</title>"
            f"<h1>报告生成异常</h1><pre>{html.escape(type(exc).__name__ + ': ' + str(exc) + '\\n\\n' + traceback.format_exc())}</pre>"
            f"<p>原始数据仍保存在 <code>{html.escape(str(matrix_path.parent))}</code>。</p>",
            encoding="utf-8",
        )


def render_matrix(summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in summaries:
        metrics = summary.get("metrics") or {}
        commit = metrics.get("commit") or {}
        search = metrics.get("search") or {}
        admission = metrics.get("admission") or {}
        cm = commit.get("completion") or {}
        sm = search.get("latency") or {}
        wait = admission.get("wait") or {}
        rows.append(
            "<tr>"
            f"<td><b>{esc((summary.get('parameters') or {}).get('scheduler_policy'))}</b></td>"
            f"<td>{esc(summary.get('status'))}</td>"
            f"<td>{esc(commit.get('completed'))}/{esc(commit.get('submitted'))}</td>"
            f"<td>{fmt(cm.get('mean_s'))}</td><td>{fmt(cm.get('p50_s'))}</td>"
            f"<td>{fmt(cm.get('p95_s'))}</td><td>{fmt(cm.get('p99_s'))}</td>"
            f"<td>{fmt(cm.get('max_s'))}</td>"
            f"<td>{fmt(sm.get('mean_s'))}</td><td>{fmt(sm.get('p95_s'))}</td>"
            f"<td>{fmt(search.get('throughput_rps'), ' RPS')}</td>"
            f"<td>{fmt(wait.get('mean_s'))}</td>"
            f"<td>{esc(admission.get('max_queue_depth', 0))}</td>"
            "</tr>"
        )

    detail_blocks = []
    for summary in summaries:
        policy = (summary.get("parameters") or {}).get("scheduler_policy", "-")
        metrics = summary.get("metrics") or {}
        tenant_rows = []
        for tenant, data in sorted((metrics.get("per_tenant") or {}).items()):
            c = (data.get("commit") or {}).get("completion") or {}
            s = (data.get("search") or {}).get("latency") or {}
            tenant_rows.append(
                f"<tr><td>{esc(tenant)}</td><td>{fmt(c.get('mean_s'))}</td>"
                f"<td>{fmt(c.get('p95_s'))}</td><td>{fmt(s.get('mean_s'))}</td>"
                f"<td>{fmt(s.get('p95_s'))}</td>"
                f"<td>{esc((data.get('commit') or {}).get('delayed_count', 0))}</td>"
                f"<td>{esc((data.get('search') or {}).get('delayed_count', 0))}</td></tr>"
            )
        isolation = (summary.get("details") or {}).get("isolation") or {}
        detail_blocks.append(
            f"<details><summary>{esc(policy)}：逐租户数据与隔离证据</summary>"
            "<h3>逐租户统计</h3><div class='scroll'><table><thead><tr>"
            "<th>租户</th><th>Commit 平均</th><th>Commit P95</th>"
            "<th>Search 平均</th><th>Search P95</th><th>Commit 超阈值</th>"
            "<th>Search 超阈值</th></tr></thead><tbody>"
            + ("".join(tenant_rows) or "<tr><td colspan='7'>无数据</td></tr>")
            + "</tbody></table></div><h3>隔离探针</h3><pre>"
            + esc(json.dumps(isolation, ensure_ascii=False, indent=2))
            + "</pre></details>"
        )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 压测策略矩阵</title>
<style>
body{{margin:0;background:#f5f7f8;color:#18232d;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{max-width:1500px;margin:auto;padding:28px 20px 60px}} h1{{margin:0 0 4px;font-size:26px}}
.muted{{color:#697684}} section{{background:#fff;border:1px solid #e2e8ed;padding:18px 20px;margin-top:14px}}
.scroll{{overflow:auto}} table{{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}}
th,td{{padding:9px 8px;border-bottom:1px solid #e6ebef;text-align:left}} th{{background:#fafbfc;color:#697684}}
details{{border-top:1px solid #e6ebef;padding:12px 0}} summary{{cursor:pointer;font-weight:750}}
pre{{background:#f5f7f8;padding:12px;overflow:auto}} .legend{{padding:12px 14px;border-left:4px solid #b07a18;background:#fff7df;color:#6b5017}}
</style></head><body><main>
<h1>EchoMem 压测策略矩阵</h1>
<div class="muted">生成时间：{esc(datetime.now().astimezone().isoformat())}</div>
<section><div class="legend">策略控制的是压测端请求准入，不等同于 EchoMem 服务内部调度。只有服务端提供队列/限流遥测时，才能对内部调度做因果结论。</div></section>
<section><h2>策略对比</h2><div class="scroll"><table><thead><tr>
<th>策略</th><th>状态</th><th>Commit</th><th>Commit 平均</th><th>P50</th><th>P95</th><th>P99</th><th>最大</th>
<th>Search 平均</th><th>Search P95</th><th>Search 吞吐</th><th>准入等待平均</th><th>最大队列</th>
</tr></thead><tbody>{''.join(rows) or '<tr><td colspan="13">无结果</td></tr>'}</tbody></table></div></section>
<section><h2>逐租户与隔离证据</h2>{''.join(detail_blocks) or '<p>无结果</p>'}</section>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--auth-header", default="X-API-Key")
    parser.add_argument("--tenant-config", default="")
    parser.add_argument("--tenants", type=int, default=4)
    parser.add_argument(
        "--allow-shared-identity",
        action="store_true",
        help="Allow non-isolation exploratory runs with one shared credential",
    )
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--search-rps", type=float, default=2.0)
    parser.add_argument("--sessions-per-tenant", type=int, default=2)
    parser.add_argument("--messages-per-session", type=int, default=3)
    parser.add_argument(
        "--commit-rpm",
        type=float,
        default=0.0,
        help="Fixed Commit arrivals per minute per tenant.",
    )
    parser.add_argument("--commit-workers", type=int, default=4)
    parser.add_argument("--search-workers", type=int, default=4)
    parser.add_argument(
        "--no-client-admission",
        action="store_true",
        help="Observe EchoMem queueing without client-side admission scheduling.",
    )
    parser.add_argument("--admission-capacity", type=int, default=1)
    parser.add_argument("--search-admission-capacity", type=int, default=4)
    parser.add_argument("--commit-admission-capacity", type=int, default=1)
    parser.add_argument("--pid", type=int, default=0)
    args = parser.parse_args()
    root = Path(args.out_dir or f"results/stress/matrix_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    runner = Path(__file__).with_name("runner.py")
    for policy in POLICIES:
        output = root / policy
        command = [
            sys.executable,
            str(runner),
            "--base-url",
            args.base_url,
            "--scheduler-policy",
            policy,
            "--auth-header",
            args.auth_header,
            "--admission-capacity",
            str(args.admission_capacity),
            "--search-admission-capacity",
            str(args.search_admission_capacity),
            "--commit-admission-capacity",
            str(args.commit_admission_capacity),
            "--duration-s",
            str(args.duration_s),
            "--search-rps",
            str(args.search_rps),
            "--sessions-per-tenant",
            str(args.sessions_per_tenant),
            "--tenants",
            str(args.tenants),
            "--messages-per-session",
            str(args.messages_per_session),
            "--commit-rpm",
            str(args.commit_rpm),
            "--commit-workers",
            str(args.commit_workers),
            "--search-workers",
            str(args.search_workers),
            "--out-dir",
            str(output),
        ]
        if args.allow_shared_identity:
            command.append("--allow-shared-identity")
        if args.no_client_admission:
            command.append("--no-client-admission")
        if args.tenant_config:
            command += ["--tenant-config", args.tenant_config]
        elif args.auth_key:
            command += ["--auth-key", args.auth_key]
        if args.pid:
            command += ["--pid", str(args.pid)]
        completed = subprocess.run(command, text=True, capture_output=True)
        (output / "runner.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (output / "runner.stderr.log").write_text(completed.stderr, encoding="utf-8")
        summary_path = output / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            actual_tenants = (summary.get("parameters") or {}).get("tenants")
            if actual_tenants != args.tenants:
                summary["status"] = "INCONCLUSIVE"
                summary.setdefault("details", {})["matrix_validation_error"] = (
                    f"expected tenants={args.tenants}, got tenants={actual_tenants}"
                )
            summaries.append(summary)
        else:
            summaries.append({
                "status": "ENVIRONMENT_ERROR",
                "parameters": {"scheduler_policy": policy},
                "details": {"runner_exit_code": completed.returncode},
            })
    matrix = {"policies": [summary.get("parameters", {}).get("scheduler_policy") for summary in summaries],
              "summaries": summaries}
    (root / "matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "matrix.html").write_text(render_matrix(summaries), encoding="utf-8")
    detailed_path = root / "matrix-detailed.html"
    render_report_safely(render_detailed_matrix_report, root / "matrix.json", detailed_path)
    audit_path = root / "matrix-audit.html"
    render_report_safely(render_audit_matrix_report, root / "matrix.json", audit_path)
    print(audit_path)
    return 0 if all(summary.get("status") not in {"ENVIRONMENT_ERROR"} for summary in summaries) else 2


if __name__ == "__main__":
    raise SystemExit(main())
