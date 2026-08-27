#!/usr/bin/env python3
"""Probe real EchoMem tenant/global rate-limit boundaries.

This tool deliberately sends real HTTP requests with independently
authenticated tenants.  It does not emulate a rate limiter and does not
interpret a lack of HTTP 429 as proof that no internal queue exists.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .runner import EchoMemHTTP, HttpResult, load_tenant_specs
except ImportError:
    from runner import EchoMemHTTP, HttpResult, load_tenant_specs


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def stats(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean_s": statistics.mean(values) if values else None,
        "p50_s": percentile(values, 50),
        "p95_s": percentile(values, 95),
        "p99_s": percentile(values, 99),
        "max_s": max(values) if values else None,
    }


@dataclass
class ProbeRow:
    operation: str
    tenant: str
    request_id: str
    status_code: int | None
    status: str
    elapsed_s: float
    retry_after_s: float | None
    sent_at: str
    error: str = ""


def row(operation: str, tenant: str, result: HttpResult, sent_at: str) -> ProbeRow:
    return ProbeRow(
        operation=operation,
        tenant=tenant,
        request_id=result.request_id,
        status_code=result.status_code,
        status=str(result.status_code or result.error or "transport_error"),
        elapsed_s=result.elapsed_s,
        retry_after_s=result.retry_after_s,
        sent_at=sent_at,
        error=result.error,
    )


def write_csv(path: Path, rows: list[ProbeRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0])) if rows else [
            "operation", "tenant", "request_id", "status_code", "status",
            "elapsed_s", "retry_after_s", "sent_at", "error",
        ])
        writer.writeheader()
        writer.writerows(asdict(item) for item in rows)


def render_report(summary: dict[str, Any], output: Path) -> None:
    def esc(value: Any) -> str:
        import html
        return html.escape("-" if value in (None, "") else str(value))

    rows = []
    for tenant, item in sorted((summary.get("per_tenant") or {}).items()):
        rows.append(
            f"<tr><td><b>{esc(tenant)}</b></td>"
            f"<td>{esc(item['submitted'])}</td><td>{esc(item['success'])}</td>"
            f"<td>{esc(item['rate_limited'])}</td><td>{esc(item['errors'])}</td>"
            f"<td>{esc(item['latency']['mean_s'])}s</td>"
            f"<td>{esc(item['latency']['p95_s'])}s</td>"
            f"<td>{esc(item['retry_after']['mean_s'])}s</td>"
            f"<td>{esc(item['identity'])}</td></tr>"
        )
    operation_rows = "".join(
        f"<tr><td>{esc(name)}</td><td>{esc(item['submitted'])}</td>"
        f"<td>{esc(item['success'])}</td><td>{esc(item['rate_limited'])}</td>"
        f"<td>{esc(item['errors'])}</td><td>{esc(item['latency']['mean_s'])}s</td>"
        f"<td>{esc(item['latency']['p50_s'])}s</td><td>{esc(item['latency']['p95_s'])}s</td>"
        f"<td>{esc(item['latency']['p99_s'])}s</td><td>{esc(item['latency']['max_s'])}s</td>"
        f"<td>{esc(item['retry_after']['mean_s'])}s</td></tr>"
        for name, item in sorted((summary.get("operations") or {}).items())
    )
    delayed_rows = "".join(
        f"<tr><td>{esc(item['operation'])}</td><td>{esc(item['tenant'])}</td>"
        f"<td>{esc(item['sent_at'])}</td><td>{esc(item['request_id'])}</td>"
        f"<td>{esc(item['status_code'])}</td><td>{esc(item['elapsed_s'])}s</td>"
        f"<td>{esc(item['retry_after_s'])}s</td><td>{esc(item['error'])}</td></tr>"
        for item in summary.get("rate_limited_rows") or []
    )
    icon = (
        "<svg class='icon' viewBox='0 0 56 56' role='img' aria-label='限流探针'>"
        "<rect x='3' y='3' width='50' height='50' rx='13' fill='#17324d'/>"
        "<path d='M15 17h26M15 28h26M15 39h18' stroke='#72d5b7' stroke-width='4' stroke-linecap='round'/>"
        "<path d='M38 35v10M33 40h10' stroke='#ff9d6e' stroke-width='3' stroke-linecap='round'/>"
        "</svg>"
    )
    document = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>EchoMem 多租户限流边界报告</title>
<style>
:root{{--bg:#f3f6f7;--paper:#fff;--ink:#17212b;--muted:#6d7b87;--line:#dce4e8;--red:#b6403b;--amber:#9b6b16}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{max-width:1300px;margin:auto;padding:28px 18px 60px}}.top{{display:flex;gap:13px;align-items:center;margin-bottom:18px}}.icon{{width:52px;height:52px}}
h1{{margin:0;font-size:25px}}h2{{font-size:18px;margin:0 0 12px}}.muted{{color:var(--muted)}}
.notice,.section{{background:var(--paper);border:1px solid var(--line);border-radius:8px}}.notice{{padding:13px 15px;border-left:4px solid var(--amber);background:#fff7df;color:#6d5116;margin-bottom:12px}}
.section{{padding:18px 19px;margin-top:12px}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}}
th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#fafbfc;color:var(--muted)}}.red{{color:var(--red);font-weight:700}}
code{{background:#eef2f4;padding:2px 5px;border-radius:4px}}.footer{{margin-top:14px;color:var(--muted);font-size:12px}}
</style></head><body><main><header class='top'>{icon}<div><h1>EchoMem 多租户限流边界报告</h1>
<div class='muted'>真实 HTTP，不使用 Mock · {esc(summary.get('finished_at'))}</div></div></header>
<div class='notice'><b>判读边界：</b>没有出现 429 只代表本轮没有收到显式限流响应，不能证明服务端没有排队或没有配额。服务端身份字段缺失时，也不能证明真实租户隔离。</div>
<section class='section'><h2>本轮配置</h2><p>服务 <code>{esc(summary.get('base_url'))}</code> · 租户 {esc(summary.get('tenant_count'))} · 每租户 burst {esc(summary.get('burst_count'))} · 并发 {esc(summary.get('workers'))}</p></section>
<section class='section'><h2>按操作汇总</h2><div class='scroll'><table><thead><tr><th>操作</th><th>提交</th><th>成功/受理</th><th>429</th><th>其他错误</th><th>平均</th><th>P50</th><th>P95</th><th>P99</th><th>最大</th><th>Retry-After 平均</th></tr></thead>
<tbody>{operation_rows or '<tr><td colspan=11>没有请求</td></tr>'}</tbody></table></div></section>
<section class='section'><h2>按租户汇总</h2><div class='scroll'><table><thead><tr><th>租户</th><th>提交</th><th>成功/受理</th><th>429</th><th>错误</th><th>平均</th><th>P95</th><th>Retry-After 平均</th><th>服务端身份</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan=9>没有请求</td></tr>'}</tbody></table></div></section>
<section class='section'><h2>限流响应明细</h2><div class='scroll'><table><thead><tr><th>操作</th><th>租户</th><th>发送时间</th><th>Request ID</th><th>HTTP</th><th>耗时</th><th>Retry-After</th><th>错误</th></tr></thead>
<tbody>{delayed_rows or '<tr><td colspan=8>本轮没有 HTTP 429</td></tr>'}</tbody></table></div></section>
<div class='footer'>原始数据：<code>rate_limit_results.csv</code>、<code>summary.json</code>。本报告只对服务端实际返回的 HTTP 状态做结论。</div>
</main></body></html>"""
    output.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe real EchoMem rate-limit boundaries")
    parser.add_argument("--base-url", default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--tenant-config", required=True)
    parser.add_argument("--auth-header", default=os.getenv("ECHOMEM_AUTH_HEADER", "X-API-Key"))
    parser.add_argument("--tenants", type=int, default=4)
    parser.add_argument("--operation", choices=("search", "commit", "both"), default="search")
    parser.add_argument("--burst-count", type=int, default=20)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--timeout-s", type=float, default=40.0)
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    out_dir = Path(args.out_dir or f"results/stress/rate_limit_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = load_tenant_specs(Path(args.tenant_config))
    if len(specs) != args.tenants:
        raise SystemExit(f"tenant config has {len(specs)} tenants, expected {args.tenants}")
    clients = {
        spec.tenant_id: EchoMemHTTP(
            args.base_url,
            spec.auth_key,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            account_id=spec.account_id,
            agent_id="echomem-rate-limit-probe",
            auth_header=args.auth_header,
        )
        for spec in specs
    }
    tenants = [spec.tenant_id for spec in specs]
    health = {tenant: clients[tenant].health() for tenant in tenants}
    bad_health = {tenant: result.error or result.status_code for tenant, result in health.items()
                  if result.status_code is None or result.status_code >= 400}
    if bad_health:
        summary = {
            "status": "ENVIRONMENT_ERROR",
            "base_url": args.base_url,
            "tenant_count": len(tenants),
            "burst_count": args.burst_count,
            "workers": args.workers,
            "finished_at": now_iso(),
            "health_error": bad_health,
            "per_tenant": {},
            "operations": {},
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        render_report(summary, out_dir / "report.html")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2

    sessions = {
        tenant: clients[tenant].open_session(
            tenant, f"rate-limit-{tenant}-{uuid.uuid4().hex}"
        )[0]
        for tenant in tenants
    }
    identity = {
        tenant: dict(clients[tenant].last_identity)
        for tenant in tenants
    }
    jobs: list[tuple[str, str]] = []
    operations = ("search", "commit") if args.operation == "both" else (args.operation,)
    for operation in operations:
        jobs.extend((operation, tenant) for tenant in tenants for _ in range(max(1, args.burst_count)))

    def send(operation: str, tenant: str) -> ProbeRow:
        client = clients[tenant]
        sent_at = now_iso()
        if operation == "search":
            result = client.search(
                sessions[tenant],
                f"rate limit probe {tenant} {uuid.uuid4().hex}",
                timeout_s=args.timeout_s,
                include_debug=False,
            )
        else:
            message_id = f"rate-limit-{uuid.uuid4().hex}"
            message = client.add_message(
                sessions[tenant], message_id, f"EchoMem rate limit probe {tenant} {message_id}"
            )
            if message.status_code is None or message.status_code >= 400:
                result = message
            else:
                result = client.commit(sessions[tenant])
        return row(operation, tenant, result, sent_at)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(send, operation, tenant) for operation, tenant in jobs]
        rows = [future.result() for future in as_completed(futures)]
    finished = time.monotonic()
    rows.sort(key=lambda item: item.sent_at)

    by_operation: dict[str, list[ProbeRow]] = {}
    by_tenant: dict[str, list[ProbeRow]] = {}
    for item in rows:
        by_operation.setdefault(item.operation, []).append(item)
        by_tenant.setdefault(item.tenant, []).append(item)

    def aggregate(items: list[ProbeRow]) -> dict[str, Any]:
        success = [item for item in items if item.status_code is not None and 200 <= item.status_code < 300]
        rate_limited = [item for item in items if item.status_code == 429]
        errors = [item for item in items if item not in success and item not in rate_limited]
        return {
            "submitted": len(items),
            "success": len(success),
            "rate_limited": len(rate_limited),
            "errors": len(errors),
            "latency": stats([item.elapsed_s for item in items]),
            "retry_after": stats([item.retry_after_s for item in items if item.retry_after_s is not None]),
        }

    summary = {
        "status": "PASS" if rows else "INCONCLUSIVE",
        "base_url": args.base_url,
        "tenant_count": len(tenants),
        "burst_count": args.burst_count,
        "workers": args.workers,
        "operations_requested": list(operations),
        "started_at": now_iso(),
        "finished_at": now_iso(),
        "wall_elapsed_s": finished - started,
        "identity_observations": identity,
        "operations": {name: aggregate(items) for name, items in by_operation.items()},
        "per_tenant": {
            tenant: {
                **aggregate(items),
                "identity": json.dumps(identity.get(tenant) or {}, ensure_ascii=False, sort_keys=True),
            }
            for tenant, items in sorted(by_tenant.items())
        },
        "rate_limited_rows": [
            asdict(item) for item in rows if item.status_code == 429
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_dir / "rate_limit_results.csv", rows)
    render_report(summary, out_dir / "report.html")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
