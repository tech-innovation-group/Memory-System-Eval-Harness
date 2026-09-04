#!/usr/bin/env python3
"""Real EchoMem failure/limit probe.

This intentionally uses the deployed service and real tenant credentials.
It does not synthesize 429/5xx responses or replace model dependencies.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import random
import statistics
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def esc(value: Any) -> str:
    return html.escape("-" if value in (None, "") else str(value))


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def response_reason_code(raw: str) -> str:
    """Extract a bounded server rejection code from a JSON response body."""
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("reason_code", "reasonCode", "error_code", "errorCode", "code"):
        value = payload.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value)
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("reason_code", "reasonCode", "error_code", "errorCode", "code"):
            value = error.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
    return ""


def response_error_detail(raw: str) -> str:
    """Keep a short, redaction-free service error explanation for audit."""
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return raw[:500]
    if not isinstance(payload, dict):
        return str(payload)[:500]
    for key in ("detail", "message", "error", "code", "reason_code"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)[:500]
    return ""


def header_reason_code(headers: Any) -> str:
    for key in ("X-Reason-Code", "X-Reason", "Reason-Code"):
        value = headers.get(key, "")
        if value:
            return str(value)
    return ""


def auth_key(tenant: dict[str, str]) -> str:
    """Resolve either an explicit key or the configured environment key."""
    direct = str(tenant.get("auth_key") or "").strip()
    if direct:
        return direct
    return os.environ.get(str(tenant.get("auth_key_env") or ""), "")


def error_class(status_code: int | None) -> str:
    if status_code is None:
        return "transport_error"
    if 400 <= status_code < 500:
        return "request_or_admission_4xx"
    if 500 <= status_code < 600:
        return "server_error"
    return ""


def classify_response(status_code: int | None, reason_code: str, detail: str) -> str:
    """Classify overload responses even when the service uses HTTP 400."""
    text = f"{reason_code} {detail}".lower()
    overload_markers = (
        "too many",
        "in flight",
        "rate limit",
        "queue full",
        "busy",
        "overload",
        "capacity",
    )
    if any(marker in text for marker in overload_markers):
        return "admission_rejected"
    return error_class(status_code)


def quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status_code") or "transport_error")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def fetch_metrics(
    base_url: str,
    tenant: dict[str, str],
    timeout_s: float,
    auth_header: str = "X-Auth-Key",
) -> dict[str, Any]:
    """Fetch a real Prometheus snapshot without changing target state."""
    started = time.monotonic()
    headers = {"Accept": "text/plain, application/openmetrics-text"}
    key = auth_key(tenant)
    headers["Authorization" if auth_header.lower() == "authorization" else auth_header] = (
        key if auth_header.lower() != "authorization" or key.lower().startswith("bearer ")
        else f"Bearer {key}"
    )
    req = urllib.request.Request(
        base_url.rstrip("/") + "/metrics",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            return {
                "status_code": response.status,
                "elapsed_s": round(time.monotonic() - started, 6),
                "raw": response.read().decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "status_code": exc.code,
            "elapsed_s": round(time.monotonic() - started, 6),
            "raw": raw,
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:
        return {
            "status_code": None,
            "elapsed_s": round(time.monotonic() - started, 6),
            "raw": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def metrics_coverage(raw: str) -> dict[str, Any]:
    """Summarize actual metric samples, excluding HELP/TYPE declarations."""
    families = {
        "echomem_lane_queued": ("queued", "lane"),
        "echomem_lane_wait_seconds": ("wait", "lane"),
        "echomem_lane_exec_seconds": ("exec", "lane"),
        "echomem_lane_rejected_total": ("rejected", "lane"),
        "echomem_engine_fanout_exec_seconds": ("exec", "engine"),
        "echomem_engine_fanout_skipped_total": ("skipped", "engine"),
    }
    present: set[str] = set()
    lane_quartets: dict[str, dict[str, bool]] = {}
    fanout_engines: dict[str, dict[str, bool]] = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        metric_name, _, label_text = line.partition("{")
        base_name = metric_name
        for suffix in ("_bucket", "_count", "_sum"):
            base_name = base_name.removesuffix(suffix)
        match = next(
            (
                (family, short, label_key)
                for family, (short, label_key) in families.items()
                if base_name == family
            ),
            None,
        )
        if match is None:
            continue
        family, short, label_key = match
        present.add(family)
        marker = f'{label_key}="'
        label_value = (
            label_text.split(marker, 1)[1].split('"', 1)[0]
            if marker in label_text
            else ""
        )
        if not label_value:
            continue
        if label_key == "lane":
            lane_quartets.setdefault(
                label_value,
                {"queued": False, "wait": False, "exec": False, "rejected": False},
            )[short] = True
        else:
            fanout_engines.setdefault(
                label_value, {"exec": False, "skipped": False}
            )[short] = True
    return {
        "present": {family: family in present for family in families},
        "missing": sorted(set(families) - present),
        "lane_quartets": lane_quartets,
        "fanout_engines": fanout_engines,
    }


def request(
    base_url: str,
    tenant: dict[str, str],
    path: str,
    body: dict[str, Any],
    timeout_s: float,
    kind: str,
    auth_header: str = "X-Auth-Key",
) -> dict[str, Any]:
    started = time.monotonic()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = auth_key(tenant)
    headers["Authorization" if auth_header.lower() == "authorization" else auth_header] = (
        key if auth_header.lower() != "authorization" or key.lower().startswith("bearer ")
        else f"Bearer {key}"
    )
    data = json.dumps(body).encode("utf-8")
    row: dict[str, Any] = {
        "kind": kind,
        "tenant": tenant["tenant_id"],
        "path": path,
        "started_at": iso_now(),
    }
    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + path, data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            row["status_code"] = response.status
            row["error_class"] = error_class(response.status)
            row["retry_after"] = response.headers.get("Retry-After", "")
            row["reason_code"] = (
                header_reason_code(response.headers) or response_reason_code(raw)
            )
            row["error_detail"] = response_error_detail(raw)
            row["error_class"] = classify_response(
                response.status, row["reason_code"], row["error_detail"]
            )
            row["body_size"] = len(raw)
            try:
                payload = json.loads(raw)
                row["result_count"] = (
                    len(payload.get("items", []))
                    if isinstance(payload, dict)
                    else None
                )
            except json.JSONDecodeError:
                row["result_count"] = None
    except urllib.error.HTTPError as exc:
        row["status_code"] = exc.code
        row["error_class"] = error_class(exc.code)
        row["retry_after"] = exc.headers.get("Retry-After", "")
        raw = exc.read().decode("utf-8", errors="replace")
        row["reason_code"] = (
            header_reason_code(exc.headers) or response_reason_code(raw)
        )
        row["error"] = f"HTTP {exc.code}"
        row["error_detail"] = response_error_detail(raw)
        row["error_class"] = classify_response(
            exc.code, row["reason_code"], row["error_detail"]
        )
        row["body_size"] = len(raw)
    except Exception as exc:
        row["status_code"] = None
        row["error_class"] = error_class(None)
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["elapsed_s"] = time.monotonic() - started
    return row


def commit_request(
    base_url: str,
    tenant: dict[str, str],
    session_id: str,
    timeout_s: float,
    auth_header: str = "X-Auth-Key",
) -> dict[str, Any]:
    """Add a real message before Commit so capacity probes exercise Commit."""
    started = time.monotonic()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = auth_key(tenant)
    headers["Authorization" if auth_header.lower() == "authorization" else auth_header] = (
        key if auth_header.lower() != "authorization" or key.lower().startswith("bearer ")
        else f"Bearer {key}"
    )
    message_id = f"limit-probe-{uuid.uuid4().hex}"
    message_body = {
        "message_id": message_id,
        "role": "user",
        "content": f"limit failure commit payload {uuid.uuid4().hex}",
    }
    row: dict[str, Any] = {
        "kind": "commit",
        "tenant": tenant["tenant_id"],
        "path": f"/api/sessions/{session_id}/commit",
        "started_at": iso_now(),
        "message_id": message_id,
    }
    try:
        message_url = (
            base_url.rstrip("/")
            + f"/api/sessions/{session_id}/messages"
        )
        message_req = urllib.request.Request(
            message_url,
            data=json.dumps(message_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(message_req, timeout=timeout_s) as response:
            message_raw = response.read().decode("utf-8", errors="replace")
            row["message_status_code"] = response.status
            row["message_body_size"] = len(message_raw)
        commit_url = base_url.rstrip("/") + f"/api/sessions/{session_id}/commit"
        commit_req = urllib.request.Request(
            commit_url,
            data=json.dumps({"agent_id": "echomem-limit-failure-probe"}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(commit_req, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            row["status_code"] = response.status
            row["error_class"] = error_class(response.status)
            row["retry_after"] = response.headers.get("Retry-After", "")
            row["reason_code"] = (
                header_reason_code(response.headers) or response_reason_code(raw)
            )
            row["error_detail"] = response_error_detail(raw)
            row["error_class"] = classify_response(
                response.status, row["reason_code"], row["error_detail"]
            )
            row["body_size"] = len(raw)
    except urllib.error.HTTPError as exc:
        row["status_code"] = exc.code
        row["error_class"] = error_class(exc.code)
        row["retry_after"] = exc.headers.get("Retry-After", "")
        raw = exc.read().decode("utf-8", errors="replace")
        row["reason_code"] = (
            header_reason_code(exc.headers) or response_reason_code(raw)
        )
        row["error"] = f"HTTP {exc.code}"
        row["error_detail"] = response_error_detail(raw)
        row["error_class"] = classify_response(
            exc.code, row["reason_code"], row["error_detail"]
        )
        row["body_size"] = len(raw)
    except Exception as exc:
        row["status_code"] = None
        row["error_class"] = error_class(None)
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["error_detail"] = ""
    row["elapsed_s"] = time.monotonic() - started
    return row


def load_tenants(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tenants = data.get("tenants") or []
    result = []
    for item in tenants:
        env_name = str(item.get("auth_key_env") or "")
        direct_key = str(item.get("auth_key") or "").strip()
        if direct_key or (env_name and os.environ.get(env_name)):
            result.append(
                {
                    "tenant_id": str(item.get("tenant_id") or ""),
                    "user_id": str(item.get("user_id") or ""),
                    "auth_key_env": env_name,
                    "auth_key": direct_key,
                }
            )
    if not result:
        raise RuntimeError("no tenant credentials are available")
    return result


def discover_sessions(root: Path, tenants: list[dict[str, str]]) -> dict[str, str]:
    found: dict[str, str] = {}
    wanted = {item["tenant_id"] for item in tenants}
    tenant_by_index = {
        str(index): item["tenant_id"]
        for index, item in enumerate(tenants)
    }
    for path in root.rglob("*.csv"):
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    tenant = row.get("tenant", "")
                    session_id = row.get("session_id", "")
                    # The formal-suite contract stores a zero-based tenant
                    # index in normalized CSVs, while older probe artifacts
                    # stored the tenant ID. Accept both representations.
                    tenant = tenant_by_index.get(tenant, tenant)
                    if tenant in wanted and session_id and tenant not in found:
                        found[tenant] = session_id
        except (OSError, UnicodeDecodeError):
            continue
    missing = wanted - found.keys()
    if missing:
        raise RuntimeError(f"no existing session found for tenants: {sorted(missing)}")
    return found


def create_sessions(
    base_url: str,
    tenants: list[dict[str, str]],
    timeout_s: float,
    auth_header: str = "X-Auth-Key",
) -> dict[str, str]:
    """Create sessions on the target being measured, avoiding cross-instance IDs."""
    sessions: dict[str, str] = {}
    for tenant in tenants:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = auth_key(tenant)
        headers["Authorization" if auth_header.lower() == "authorization" else auth_header] = (
            key if auth_header.lower() != "authorization" or key.lower().startswith("bearer ")
            else f"Bearer {key}"
        )
        body = {
            "agent_id": "echomem-limit-failure-probe",
            "metadata": {
                "title": f"limit-probe-{tenant['tenant_id']}",
                "tenant_id": tenant["tenant_id"],
                "user_id": tenant["user_id"],
            },
        }
        req = urllib.request.Request(
            base_url.rstrip("/") + "/api/sessions/open",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        session_id = ((payload.get("scope") or {}).get("session_id"))
        if not session_id:
            raise RuntimeError(
                f"session open returned no session_id for {tenant['tenant_id']}"
            )
        sessions[tenant["tenant_id"]] = str(session_id)
    return sessions


def run_wave(
    base_url: str,
    tenants: list[dict[str, str]],
    sessions: dict[str, str],
    *,
    kind: str,
    count: int,
    workers: int,
    timeout_s: float,
    path: str,
    auth_header: str = "X-Auth-Key",
) -> list[dict[str, Any]]:
    jobs: list[tuple[dict[str, str], dict[str, Any]]] = []
    for index in range(count):
        tenant = tenants[index % len(tenants)]
        if kind == "search":
            body = {
                "query": f"limit failure probe {uuid.uuid4().hex}",
                "agent_id": "echomem-limit-failure-probe",
                "session_id": sessions[tenant["tenant_id"]],
                "limit": 10,
                "include_explain": False,
                "include_debug": True,
            }
        elif kind == "commit":
            body = {"metadata": {"keep_recent_count": 0}}
        else:
            body = {
                "agent_id": "echomem-limit-failure-probe",
                "metadata": {
                    "title": f"limit-probe-{uuid.uuid4().hex}",
                    "account_id": tenant["tenant_id"],
                    "user_id": tenant["user_id"],
                    "tenant_id": tenant["tenant_id"],
                },
            }
        request_path = path
        if kind == "commit":
            request_path = f"/api/sessions/{sessions[tenant['tenant_id']]}/commit"
        jobs.append((tenant, body | {"_request_path": request_path}))
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(
                commit_request if kind == "commit" else request,
                base_url,
                tenant,
                (
                    sessions[tenant["tenant_id"]]
                    if kind == "commit"
                    else body.pop("_request_path", path)
                ),
                *(
                    (timeout_s, auth_header)
                    if kind == "commit"
                    else (body, timeout_s, kind, auth_header)
                ),
            )
            for tenant, body in jobs
        ]
        for future in as_completed(futures):
            rows.append(future.result())
    return rows


def render_chart(title: str, counts: dict[str, int]) -> str:
    total = max(1, sum(counts.values()))
    colors = {"2xx": "#197c62", "4xx": "#b7791f", "5xx": "#b6403b", "transport": "#7b4ea3"}
    bars = []
    x = 40
    width = 680 / max(1, len(counts))
    for key, value in counts.items():
        if str(key).startswith("2"):
            color = colors["2xx"]
        elif str(key).startswith("4"):
            color = colors["4xx"]
        elif str(key).startswith("5"):
            color = colors["5xx"]
        else:
            color = colors["transport"]
        height = 150 * value / total
        y = 180 - height
        bars.append(
            f"<rect x='{x:.1f}' y='{y:.1f}' width='{width * .65:.1f}' "
            f"height='{height:.1f}' fill='{color}'/><text x='{x + width * .32:.1f}' "
            f"y='198' text-anchor='middle'>{html.escape(str(key))}</text>"
            f"<text x='{x + width * .32:.1f}' y='{max(12, y - 5):.1f}' "
            f"text-anchor='middle'>{value}</text>"
        )
        x += width
    return (
        f"<div class='chart'><h3>{esc(title)}</h3><svg viewBox='0 0 760 220'>"
        "<line x1='30' y1='180' x2='730' y2='180' stroke='#ccd5dc'/>"
        + "".join(bars)
        + "</svg></div>"
    )


def write_report(
    out_dir: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "requests.json").open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    fields = sorted({key for row in rows for key in row})
    with (out_dir / "requests.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["kind"], []).append(row)
    cards = []
    charts = []
    sections = []
    for kind, group in groups.items():
        codes = status_counts(group)
        transport = sum(1 for row in group if not row.get("status_code"))
        errors = sum(
            1
            for row in group
            if not row.get("status_code") or int(row["status_code"]) >= 400
        )
        times = [float(row["elapsed_s"]) for row in group]
        retry_after = sum(bool(row.get("retry_after")) for row in group)
        cards.append(
            f"<div class='card'><small>{esc(kind)}</small><b>{len(group)}</b>"
            f"<span>{errors} HTTP/transport errors · {retry_after} Retry-After</span></div>"
        )
        charts.append(render_chart(f"{kind} response status", codes))
        bad = [
            row for row in group
            if not row.get("status_code") or int(row["status_code"]) >= 400
        ][:80]
        bad_rows = "".join(
            f"<tr><td>{esc(row.get('tenant'))}</td><td>{esc(row.get('status_code') or 'transport')}</td>"
            f"<td>{float(row.get('elapsed_s') or 0):.3f}s</td>"
            f"<td>{esc(row.get('retry_after'))}</td><td>{esc(row.get('reason_code'))}</td>"
            f"<td>{esc(row.get('error'))}</td><td>{esc(row.get('error_detail'))}</td></tr>"
            for row in bad
        ) or "<tr><td colspan='7'>没有 HTTP/transport 错误；不代表业务质量成功。</td></tr>"
        sections.append(
            f"<section><h2>{esc(kind)} 详细失败样本</h2>"
            f"<p>错误数 {errors}/{len(group)}，P50 {quantile(times,.5):.3f}s，"
            f"P95 {quantile(times,.95):.3f}s，最大 {max(times):.3f}s。</p>"
            "<div class='scroll'><table><thead><tr><th>租户</th><th>状态</th>"
            "<th>耗时</th><th>Retry-After</th><th>reason_code</th><th>错误</th>"
            "<th>服务端详情</th></tr></thead>"
            f"<tbody>{bad_rows}</tbody></table></div></section>"
        )
    manifest["finished_at"] = iso_now()
    manifest["status_counts"] = {kind: status_counts(group) for kind, group in groups.items()}
    manifest["error_counts"] = {
        kind: sum(
            1 for row in group
            if not row.get("status_code") or int(row["status_code"]) >= 400
        )
        for kind, group in groups.items()
    }
    (out_dir / "summary.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    html_doc = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>EchoMem 限流失败探针</title>
<style>
body{{margin:0;background:#f5f7f8;color:#17212b;font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
main{{max-width:1120px;margin:auto;padding:26px 18px 56px}}section,.hero,.card,.chart{{background:#fff;border:1px solid #e1e7eb;padding:18px;margin-top:14px}}
.hero{{border-left:5px solid #b6403b}}h1{{margin:0 0 5px;font-size:25px}}h2{{font-size:18px}}h3{{margin:0 0 8px;font-size:15px}}
.muted,small,span{{color:#687784}}.cards,.charts{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:14px}}
.card b{{display:block;font-size:28px;margin:4px 0}}.chart svg{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid #e7ecef;padding:8px;text-align:left;vertical-align:top}}th{{background:#f7f9fa}}
.scroll{{overflow:auto}}code{{background:#f0f3f5;padding:2px 4px}}@media(max-width:700px){{.cards,.charts{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class='hero'><h1>EchoMem 真实限流/失败探针</h1>
<div>只使用真实 HTTP、真实租户凭证和真实服务；不 mock 错误响应。</div>
<div class='muted'>开始：{esc(manifest.get('started_at'))} · 完成：{esc(manifest.get('finished_at'))}</div></div>
<div class='cards'>{''.join(cards)}</div>
<section><h2>测试口径</h2><p>Search 洪峰复用已有 session，只验证服务入口在高并发下的真实响应；
HTTP 200 但空召回仍属于业务质量失败，不能当作成功。入口洪峰验证 sessions/open 的限流、
5xx、连接超时和响应耗时。客户端没有 admission 限制。</p>
<p>配置：{esc(json.dumps({k:v for k,v in manifest.items() if k not in {'status_counts','error_counts'}}, ensure_ascii=False))}</p></section>
<div class='charts'>{''.join(charts)}</div>{''.join(sections)}
<section><h2>文件</h2><p><a href='requests.csv'>requests.csv</a> ·
<a href='requests.json'>requests.json</a> · <a href='summary.json'>summary.json</a></p></section>
</main></body></html>"""
    (out_dir / "report.html").write_text(html_doc, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant-config", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument(
        "--create-sessions",
        action="store_true",
        help="Create sessions on the target instead of reusing another run's CSV",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--search-count", type=int, default=1024)
    parser.add_argument("--open-count", type=int, default=512)
    parser.add_argument("--commit-count", type=int, default=0)
    parser.add_argument("--workers", type=int, default=256)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--auth-header", default="X-Auth-Key")
    args = parser.parse_args()
    tenants = load_tenants(args.tenant_config)
    sessions = (
        create_sessions(args.base_url, tenants, args.timeout_s, args.auth_header)
        if args.create_sessions
        else discover_sessions(args.session_root, tenants)
    )
    manifest = {
        "test_type": "real_limit_failure_probe",
        "started_at": iso_now(),
        "base_url": args.base_url,
        "tenants": [item["tenant_id"] for item in tenants],
        "session_source": "target_open" if args.create_sessions else "existing_result_csv",
        "search_count": args.search_count,
        "open_count": args.open_count,
        "workers": args.workers,
        "timeout_s": args.timeout_s,
        "client_admission": False,
        "search_sessions": sessions,
    }
    rows: list[dict[str, Any]] = []
    metrics_before = fetch_metrics(
        args.base_url, tenants[0], args.timeout_s, args.auth_header
    )
    rows.extend(
        run_wave(
            args.base_url, tenants, sessions, kind="search",
            count=args.search_count, workers=args.workers,
            timeout_s=args.timeout_s, path="/api/retrieval/search",
            auth_header=args.auth_header,
        )
    )
    if args.commit_count > 0:
        rows.extend(
            run_wave(
                args.base_url, tenants, sessions, kind="commit",
                count=args.commit_count, workers=args.workers,
                timeout_s=args.timeout_s, path="/commit",
                auth_header=args.auth_header,
            )
        )
    rows.extend(
        run_wave(
            args.base_url, tenants, sessions, kind="open",
            count=args.open_count, workers=args.workers,
            timeout_s=args.timeout_s, path="/api/sessions/open",
            auth_header=args.auth_header,
        )
    )
    metrics_after = fetch_metrics(
        args.base_url, tenants[0], args.timeout_s, args.auth_header
    )
    manifest["metrics_before"] = {
        key: value for key, value in metrics_before.items() if key != "raw"
    }
    manifest["metrics_after"] = {
        key: value for key, value in metrics_after.items() if key != "raw"
    }
    manifest["metrics_coverage_before"] = metrics_coverage(
        str(metrics_before.get("raw") or "")
    )
    manifest["metrics_coverage"] = metrics_coverage(
        str(metrics_after.get("raw") or "")
    )
    (args.out_dir / "metrics-before.txt").write_text(
        str(metrics_before.get("raw") or ""), encoding="utf-8"
    )
    (args.out_dir / "metrics-after.txt").write_text(
        str(metrics_after.get("raw") or ""), encoding="utf-8"
    )
    write_report(args.out_dir, manifest, rows)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
