#!/usr/bin/env python3
"""Run configuration-driven, real HTTP stress tests against any JSON service.

The runner deliberately has no service-specific assumptions. A target is
described by JSON: health checks, request templates, scenarios, assertions,
and optional process/metrics endpoints. All requests go to the real target.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import queue
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100
    low = math.floor(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def render_value(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**variables)
        except (KeyError, IndexError):
            return value
    if isinstance(value, list):
        return [render_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {str(k): render_value(v, variables) for k, v in value.items()}
    return value


def json_path(payload: Any, path: str) -> Any:
    """Resolve a small, predictable $.a.b[0] JSON path subset."""
    if not path or path == "$":
        return payload
    current = payload
    tokens = path.lstrip("$").lstrip(".").replace("[", ".").replace("]", "").split(".")
    for token in filter(None, tokens):
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return current


def check_assertions(payload: Any, assertions: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for assertion in assertions:
        path = str(assertion.get("path", "$"))
        actual = json_path(payload, path)
        if "equals" in assertion and actual != assertion["equals"]:
            failures.append(f"{path}: expected equals {text(assertion['equals'])}, got {text(actual)}")
        if "contains" in assertion and str(assertion["contains"]) not in text(actual):
            failures.append(f"{path}: expected contains {text(assertion['contains'])}, got {text(actual)}")
        if assertion.get("exists") is True and actual is None:
            failures.append(f"{path}: expected to exist")
        if assertion.get("exists") is False and actual is not None:
            failures.append(f"{path}: expected not to exist")
        if "min" in assertion:
            try:
                if float(actual) < float(assertion["min"]):
                    failures.append(f"{path}: expected >= {assertion['min']}, got {actual}")
            except (TypeError, ValueError):
                failures.append(f"{path}: expected numeric value, got {text(actual)}")
    return failures


def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("配置根节点必须是 JSON object")
    target = payload.get("target")
    if not isinstance(target, dict) or not target.get("base_url"):
        raise ValueError("配置必须包含 target.base_url")
    requests = payload.get("requests")
    if not isinstance(requests, dict) or not requests:
        raise ValueError("配置必须包含至少一个 requests 定义")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("配置必须包含至少一个 scenarios 定义")
    return payload


def resolve_variables(
    config: dict[str, Any],
    overrides: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve ${ENV_NAME} values without putting credentials in config files."""
    variables = dict(config.get("variables") or {})
    for key, value in list(variables.items()):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            variables[key] = os.getenv(value[2:-1], "")
    for item in overrides or []:
        if "=" not in item:
            raise ValueError(f"--var 必须是 NAME=VALUE: {item}")
        key, value = item.split("=", 1)
        if not key.strip():
            raise ValueError(f"--var 缺少变量名: {item}")
        variables[key.strip()] = value
    return variables


class GenericHTTPClient:
    def __init__(self, config: dict[str, Any], variables: dict[str, Any]):
        self.config = config
        self.variables = variables
        target = config["target"]
        self.base_url = str(target["base_url"]).rstrip("/")
        self.default_headers = {
            str(k): str(render_value(v, variables))
            for k, v in (target.get("headers") or {}).items()
        }
        self.timeout_s = float(target.get("timeout_s", 30))
        self.capture_response_body = bool(target.get("capture_response_body", False))

    def request(self, request_name: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        spec = self.config["requests"].get(request_name)
        if not isinstance(spec, dict):
            return {
                "request_name": request_name,
                "status_code": None,
                "elapsed_s": 0,
                "error": f"unknown request template: {request_name}",
            }
        merged = {**self.variables, **(variables or {})}
        method = str(spec.get("method", "GET")).upper()
        path = str(render_value(spec.get("path", "/"), merged))
        url = f"{self.base_url}{path}" if path.startswith("/") else path
        headers = {**self.default_headers, **{
            str(k): str(render_value(v, merged))
            for k, v in (spec.get("headers") or {}).items()
        }}
        body = render_value(spec.get("body"), merged)
        data = None
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            else:
                data = str(body).encode("utf-8")
        request_id = f"generic-{uuid.uuid4().hex}"
        headers.setdefault("X-Request-ID", request_id)
        queued_at = now_iso()
        started = time.monotonic()
        started_at = now_iso()
        status_code: int | None = None
        response_headers: dict[str, str] = {}
        response_body = ""
        error = ""
        assertion_failures: list[str] = []
        try:
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                status_code = int(response.status)
                response_headers = {str(k): str(v) for k, v in response.headers.items()}
                response_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            response_headers = {str(k): str(v) for k, v in exc.headers.items()}
            response_body = exc.read().decode("utf-8", errors="replace")
            error = f"HTTP {exc.code}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        elapsed_s = time.monotonic() - started
        finished_at = now_iso()
        parsed: Any = None
        if response_body:
            try:
                parsed = json.loads(response_body)
            except json.JSONDecodeError:
                parsed = response_body
        expected_status = spec.get("expected_status", 200)
        if status_code is not None:
            allowed = expected_status if isinstance(expected_status, list) else [expected_status]
            if status_code not in [int(item) for item in allowed]:
                error = error or f"unexpected status {status_code}, expected {allowed}"
        assertion_failures = check_assertions(parsed, spec.get("assertions") or [])
        if assertion_failures:
            error = "; ".join(assertion_failures)
        return {
            "request_name": request_name,
            "method": method,
            "path": path,
            "url": url,
            "request_id": request_id,
            "queued_at": queued_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "status_code": status_code,
            "elapsed_s": elapsed_s,
            "response_bytes": len(response_body.encode("utf-8")),
            "error": error,
            "assertion_failures": assertion_failures,
            "response_body": (
                response_body[:2000]
                if self.capture_response_body or spec.get("capture_response_body", False)
                else ""
            ),
            "timestamp": now_iso(),
            "retry_after": response_headers.get("Retry-After", ""),
        }


def resource_sample(pid: int | None) -> dict[str, Any]:
    sample: dict[str, Any] = {"timestamp": now_iso()}
    if not pid or pid <= 0:
        sample["available"] = False
        return sample
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.split()
        rss_pages = int(fields[23])
        sample.update({
            "available": True,
            "pid": pid,
            "rss_mb": rss_pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024,
        })
    except (OSError, ValueError, IndexError):
        sample["available"] = False
    return sample


def stats(values: list[float]) -> dict[str, float | None]:
    return {
        "min_s": min(values) if values else None,
        "mean_s": statistics.mean(values) if values else None,
        "p50_s": percentile(values, 50),
        "p90_s": percentile(values, 90),
        "p95_s": percentile(values, 95),
        "p99_s": percentile(values, 99),
        "max_s": max(values) if values else None,
    }


def run_health(client: GenericHTTPClient, config: dict[str, Any]) -> dict[str, Any]:
    health = config.get("healthcheck") or {}
    request_name = str(health.get("request", "health"))
    if request_name not in config["requests"]:
        return {"status": ENVIRONMENT_ERROR, "error": f"healthcheck request not found: {request_name}"}
    result = client.request(request_name)
    ok = result.get("status_code") in (
        health.get("expected_status", config["requests"][request_name].get("expected_status", 200))
        if isinstance(health.get("expected_status", config["requests"][request_name].get("expected_status", 200)), list)
        else [health.get("expected_status", config["requests"][request_name].get("expected_status", 200))]
    ) and not result.get("error")
    return {"status": PASS if ok else ENVIRONMENT_ERROR, "result": result}


def run_scenario(
    client: GenericHTTPClient,
    scenario: dict[str, Any],
    out_rows: list[dict[str, Any]],
    rows_lock: threading.Lock,
    resource_rows: list[dict[str, Any]],
    resource_lock: threading.Lock,
    pid: int | None,
) -> dict[str, Any]:
    name = str(scenario.get("name", "scenario"))
    request_names = [str(item) for item in scenario.get("requests", [])]
    if not request_names:
        raise ValueError(f"scenario {name} has no requests")
    duration_s = max(0.0, float(scenario.get("duration_s", 0)))
    total_requests = scenario.get("total_requests")
    rps = max(0.0, float(scenario.get("rps", 0)))
    concurrency = max(1, int(scenario.get("concurrency", 1)))
    if total_requests is None:
        total_requests = max(len(request_names), math.ceil(duration_s * rps)) if duration_s or rps else len(request_names)
    total_requests = max(0, int(total_requests))
    started = time.monotonic()
    jobs: list[tuple[int, str]] = [(index, request_names[index % len(request_names)]) for index in range(total_requests)]
    if duration_s and rps:
        interval = 1.0 / rps
    else:
        interval = 0.0
    def invoke(item: tuple[int, str]) -> dict[str, Any]:
        index, request_name = item
        if interval:
            target = started + index * interval
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        result = client.request(request_name, {
            "scenario": name,
            "sequence": index,
            "run_id": client.variables.get("run_id", ""),
        })
        result["scenario"] = name
        result["sequence"] = index
        result["scheduled_at"] = now_iso()
        with rows_lock:
            out_rows.append(result)
        with resource_lock:
            resource_rows.append({
                "scenario": name,
                "sequence": index,
                **resource_sample(pid),
            })
        return result
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(invoke, jobs))
    elapsed_s = time.monotonic() - started
    latencies = [float(item["elapsed_s"]) for item in results]
    errors = [item for item in results if item.get("error") or item.get("status_code") is None]
    return {
        "name": name,
        "target_requests": total_requests,
        "submitted": len(results),
        "errors": len(errors),
        "error_rate": len(errors) / len(results) if results else None,
        "latency": stats(latencies),
        "elapsed_s": elapsed_s,
        "throughput_rps": len(results) / elapsed_s if elapsed_s else None,
        "status_codes": {
            str(code): sum(1 for item in results if item.get("status_code") == code)
            for code in sorted({item.get("status_code") for item in results if item.get("status_code") is not None})
        },
    }


def diagnose(summary: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    scenarios = summary.get("scenarios") or []
    if summary.get("health", {}).get("status") != PASS:
        findings.append({"severity": "critical", "category": "availability", "finding": "健康检查失败，无法确认目标系统可用。"})
    for item in scenarios:
        if item.get("errors", 0):
            findings.append({"severity": "high", "category": "errors", "finding": f"{item['name']} 出现 {item['errors']}/{item['submitted']} 个失败请求。"})
        p95 = (item.get("latency") or {}).get("p95_s")
        limit = item.get("p95_limit_s")
        if p95 is not None and limit is not None and p95 > limit:
            findings.append({"severity": "high", "category": "latency", "finding": f"{item['name']} P95 {p95:.3f}s 超过门槛 {limit:.3f}s。"})
        if item.get("target_requests", 0) and item.get("submitted", 0) < item["target_requests"]:
            findings.append({"severity": "high", "category": "load-generation", "finding": f"{item['name']} 未达到目标请求数，不能把结果解释为完整压测。"})
    resources = summary.get("resources") or []
    rss = [float(row["rss_mb"]) for row in resources if row.get("available") and row.get("rss_mb") is not None]
    if len(rss) >= 2 and rss[-1] - rss[0] > float(summary.get("resource_growth_limit_mb", 200)):
        findings.append({"severity": "medium", "category": "resources", "finding": f"观测到 RSS 增长 {rss[-1] - rss[0]:.1f}MB，超过配置门槛。"})
    if not findings:
        findings.append({"severity": "info", "category": "result", "finding": "本轮未发现超过配置门槛的异常；这不等于系统不存在未覆盖的故障。"})
    return {"findings": findings}


def render_report(summary: dict[str, Any], out_path: Path) -> None:
    findings = summary.get("diagnosis", {}).get("findings", [])
    rows = []
    for item in summary.get("scenarios", []):
        latency = item.get("latency") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(text(item.get('name')))}</td>"
            f"<td>{html.escape(text(item.get('submitted')))}/{html.escape(text(item.get('target_requests')))}</td>"
            f"<td>{html.escape(text(item.get('errors')))}</td>"
            f"<td>{html.escape(text(latency.get('p95_s') or '-'))}s</td>"
            f"<td>{html.escape(text(item.get('throughput_rps') or '-'))} req/s</td>"
            "</tr>"
        )
    finding_html = "".join(
        f"<li class='{item['severity']}'><b>{item['severity']}</b> "
        f"{item['category']}: {item['finding']}</li>" for item in findings
    )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>通用系统压测报告</title>
<style>
body{{margin:0;background:#f5f7fa;color:#17202a;font:14px/1.6 system-ui,-apple-system,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:28px 18px 60px}} h1{{margin:0 0 6px}} h2{{font-size:17px;margin:0 0 12px}}
.muted{{color:#687582}} .panel{{background:#fff;border:1px solid #dfe5ea;border-radius:8px;padding:20px;margin-top:16px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #edf0f2;text-align:left}}
th{{background:#fafbfc;color:#687582}} ul{{margin:0;padding-left:22px}} li{{margin:8px 0}} .critical,.high{{color:#b42318}} .medium{{color:#9a6700}} .info{{color:#176b4d}}
code{{font-family:ui-monospace,monospace}} 
</style></head><body><main>
<h1>通用系统压测报告</h1><p class="muted">{html.escape(text(summary.get('target', {}).get('base_url', '')))} · {html.escape(text(summary.get('started_at', '')))}</p>
<section class="panel"><h2>结论：{html.escape(text(summary.get('status')))}</h2><p>健康检查：{html.escape(text(summary.get('health', {}).get('status')))}；本报告基于真实 HTTP 请求生成。</p></section>
<section class="panel"><h2>场景结果</h2><table><thead><tr><th>场景</th><th>实际/目标</th><th>错误</th><th>P95</th><th>吞吐</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section class="panel"><h2>故障诊断</h2><ul>{finding_html}</ul></section>
<section class="panel"><h2>原始产物</h2><p><code>summary.json</code>、<code>requests.csv</code>、<code>resources.csv</code> 保存在同一目录。</p></section>
</main></body></html>"""
    out_path.write_text(document, encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: text(row.get(key)) for key in keys})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configuration-driven real HTTP stress runner")
    parser.add_argument("--config", required=True, help="JSON target/scenario configuration")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Override a config variable; may be repeated",
    )
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir or f"results/stress/generic_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = now_iso()
    try:
        config = load_config(Path(args.config))
        variables = {**resolve_variables(config, args.var), "run_id": uuid.uuid4().hex}
        client = GenericHTTPClient(config, variables)
        health = run_health(client, config)
    except Exception as exc:
        summary = {
            "status": ENVIRONMENT_ERROR,
            "target": {},
            "started_at": started_at,
            "finished_at": now_iso(),
            "health": {"status": ENVIRONMENT_ERROR, "error": str(exc)},
            "scenarios": [],
            "diagnosis": {"findings": [{"severity": "critical", "category": "configuration", "finding": str(exc)}]},
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        render_report(summary, out_dir / "report.html")
        return 2
    request_rows: list[dict[str, Any]] = []
    resource_rows: list[dict[str, Any]] = []
    locks = (threading.Lock(), threading.Lock())
    scenarios: list[dict[str, Any]] = []
    if health["status"] == PASS:
        scenario_list = config.get("scenarios") or []
        for index, scenario in enumerate(scenario_list, start=1):
            result = run_scenario(client, scenario, request_rows, locks[0], resource_rows, locks[1], args.pid)
            result["p95_limit_s"] = scenario.get("p95_limit_s")
            scenarios.append(result)
            print(
                f"GENERIC_PROGRESS {index}/{len(scenario_list)} "
                f"scenario={result['name']} errors={result['errors']}",
                flush=True,
            )
    status = PASS
    if health["status"] != PASS:
        status = ENVIRONMENT_ERROR
    elif any(item.get("errors") or (item.get("p95_limit_s") and (item.get("latency") or {}).get("p95_s", 0) > item["p95_limit_s"]) for item in scenarios):
        status = FAIL
    summary = {
        "status": status,
        "target": config["target"],
        "started_at": started_at,
        "finished_at": now_iso(),
        "health": health,
        "scenarios": scenarios,
        "resources": resource_rows,
        "resource_growth_limit_mb": float(config.get("resource_growth_limit_mb", 200)),
    }
    summary["diagnosis"] = diagnose(summary)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_dir / "requests.csv", request_rows)
    write_csv(out_dir / "resources.csv", resource_rows)
    render_report(summary, out_dir / "report.html")
    print(json.dumps({
        "status": summary["status"],
        "out_dir": str(out_dir),
        "scenarios": scenarios,
        "diagnosis": summary["diagnosis"],
    }, ensure_ascii=False))
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
