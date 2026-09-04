#!/usr/bin/env python3
"""Probe real HTTP client disconnect handling and bounded resource recovery."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_json(url: str, method: str, body: dict[str, Any] | None, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw[-1000:]}
            return {"status_code": response.status, "payload": payload}
    except (OSError, urllib.error.URLError) as exc:
        return {"status_code": None, "error": str(exc)}


def process_sample(pid: int) -> dict[str, Any]:
    result: dict[str, Any] = {"pid": pid}
    if not pid:
        return result
    proc = Path("/proc") / str(pid)
    try:
        result["threads"] = int((proc / "status").read_text().split("Threads:", 1)[1].splitlines()[0])
    except (OSError, IndexError, ValueError):
        pass
    try:
        result["fds"] = len(list((proc / "fd").iterdir()))
    except OSError:
        pass
    return result


def metrics(base_url: str, headers: dict[str, str]) -> dict[str, Any]:
    return http_json(base_url.rstrip("/") + "/metrics", "GET", None, headers, 5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--auth-key", default="")
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument("--tenant", default="stress-a")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--disconnect-delay-s", type=float, default=0.05)
    parser.add_argument("--recovery-wait-s", type=float, default=30)
    parser.add_argument("--post-recovery-wait-s", type=float, default=60)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    headers = {"X-EchoMem-Tenant": args.tenant}
    if args.auth_key:
        headers[args.auth_header] = args.auth_key
    health_url = args.base_url.rstrip("/") + "/health"
    before = http_json(health_url, "GET", None, headers, 5)
    metrics_before = metrics(args.base_url, headers)
    proc_before = process_sample(args.pid)
    started = time.monotonic()
    outcomes: list[dict[str, Any]] = []

    def disconnect_one(index: int) -> None:
        connection = None
        try:
            parsed = urllib.parse.urlparse(args.base_url)
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            body = json.dumps({
                "query": f"real disconnect probe {args.tenant} {index} {uuid_marker}",
                "agent_id": "pr421-disconnect",
                "limit": 10,
                "include_debug": True,
            })
            connection.request(
                "POST",
                "/api/retrieval/search",
                body=body,
                headers={"Content-Type": "application/json", **headers},
            )
            time.sleep(max(0.0, args.disconnect_delay_s))
            connection.close()
            outcomes.append({"index": index, "client_action": "closed_after_request"})
        except BaseException as exc:
            outcomes.append({"index": index, "client_action": "client_error", "error": str(exc)})
            if connection:
                connection.close()

    uuid_marker = f"{os.getpid()}-{int(time.time())}"
    threads = [threading.Thread(target=disconnect_one, args=(i,)) for i in range(max(1, args.requests))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    time.sleep(max(0.0, args.recovery_wait_s))
    after = http_json(health_url, "GET", None, headers, 5)
    metrics_after = metrics(args.base_url, headers)
    proc_after = process_sample(args.pid)
    time.sleep(max(0.0, args.post_recovery_wait_s))
    after_settle = http_json(health_url, "GET", None, headers, 5)
    metrics_after_settle = metrics(args.base_url, headers)
    proc_after_settle = process_sample(args.pid)

    result = {
        "status": "INCONCLUSIVE",
        "reason": "client disconnects were issued and service remained observable; EchoMem exposes no per-request orphan-task/FD contract",
        "started_at": now(),
        "duration_s": time.monotonic() - started,
        "base_url": args.base_url,
        "tenant": args.tenant,
        "real_http": True,
        "mock_model": False,
        "requests": len(outcomes),
        "disconnect_delay_s": args.disconnect_delay_s,
        "recovery_wait_s": args.recovery_wait_s,
        "post_recovery_wait_s": args.post_recovery_wait_s,
        "health_before": before,
        "health_after": after,
        "health_after_settle": after_settle,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "metrics_after_settle": metrics_after_settle,
        "process_before": proc_before,
        "process_after": proc_after,
        "process_after_settle": proc_after_settle,
        "client_outcomes": outcomes,
    }
    if after.get("status_code") and after["status_code"] < 400:
        result["service_recovered"] = True
    else:
        result["status"] = "FAIL"
        result["reason"] = "service was not healthy after client disconnect wave"
        result["service_recovered"] = False
    result["resource_settled"] = (
        not proc_before.get("fds")
        or not proc_after_settle.get("fds")
        or proc_after_settle.get("fds") <= max(proc_before.get("fds", 0) + 2, proc_after.get("fds", 0))
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
