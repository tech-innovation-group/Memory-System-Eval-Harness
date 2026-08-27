#!/usr/bin/env python3
"""Run real EchoMem HTTP stress scenarios and emit machine-readable results.

This module intentionally uses only the Python standard library. It does not
mock EchoMem or the model service; a local fake server may be used only by
unit tests for the HTTP edge cases.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import statistics
import sys
import threading
import time
from collections import deque
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .clean_report import render as render_readable_report
    from .executive_report import render as render_executive_report
except ImportError:
    from clean_report import render as render_readable_report
    from executive_report import render as render_executive_report


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def linear_slope_per_minute(samples: list["ResourceSample"]) -> float | None:
    if len(samples) < 4:
        return None
    xs = [s.elapsed_s / 60.0 for s in samples]
    ys = [s.rss_mb for s in samples]
    x_bar, y_bar = statistics.mean(xs), statistics.mean(ys)
    denominator = sum((x - x_bar) ** 2 for x in xs)
    return None if denominator == 0 else sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / denominator


@dataclass
class HttpResult:
    method: str
    path: str
    status_code: int | None
    elapsed_s: float
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retry_after_s: float | None = None
    admission_wait_s: float = 0.0
    admission_queue_depth: int = 0
    admission_order: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    server_received_at: str = ""
    server_queue_entered_at: str = ""
    server_execution_started_at: str = ""
    server_finished_at: str = ""
    server_queue_depth: int | None = None
    server_active_workers: int | None = None
    server_terminal_status: str = ""
    server_identity: dict[str, Any] = field(default_factory=dict)
    client_request_id: str = ""

    @property
    def request_id(self) -> str:
        for key in ("x-request-id", "x-correlation-id", "request-id"):
            if self.headers.get(key):
                return self.headers[key]
        for key in ("request_id", "requestId", "trace_id", "traceId"):
            value = self.payload.get(key) if isinstance(self.payload, dict) else None
            if value:
                return str(value)
        # EchoMem versions that do not echo a request ID still need a stable
        # client-side correlation key for server logs and raw CSV evidence.
        return self.client_request_id


def _nested_observability(payload: dict[str, Any]) -> dict[str, Any]:
    """Find optional server timing data without assuming one response schema."""
    candidates: list[dict[str, Any]] = [payload]
    for key in ("telemetry", "observability", "debug", "meta", "metadata", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    merged: dict[str, Any] = {}
    for candidate in candidates:
        for key, value in candidate.items():
            merged.setdefault(str(key), value)
    return merged


def _server_observability(
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Extract optional server-side queue evidence from payloads or headers."""
    nested = _nested_observability(payload)
    aliases: dict[str, tuple[str, ...]] = {
        "server_received_at": (
            "received_at", "server_received_at", "serverReceivedAt",
            "x_received_at", "x-server-received-at",
        ),
        "server_queue_entered_at": (
            "queue_entered_at", "server_queue_entered_at", "queueEnteredAt",
            "x_queue_entered_at", "x-server-queue-entered-at",
        ),
        "server_execution_started_at": (
            "execution_started_at", "server_execution_started_at",
            "executionStartedAt", "started_at_server", "x-server-execution-started-at",
        ),
        "server_finished_at": (
            "finished_at", "server_finished_at", "finishedAt",
            "x_finished_at", "x-server-finished-at",
        ),
        "server_queue_depth": (
            "queue_depth", "server_queue_depth", "queueDepth", "x-queue-depth",
        ),
        "server_active_workers": (
            "active_workers", "server_active_workers", "activeWorkers",
            "x-active-workers",
        ),
        "server_terminal_status": (
            "terminal_status", "server_terminal_status", "terminalStatus",
        ),
    }
    result: dict[str, Any] = {}
    for target, names in aliases.items():
        value: Any = None
        for name in names:
            if name in nested:
                value = nested[name]
                break
            header_name = name.lower()
            if header_name in headers:
                value = headers[header_name]
                break
        if value is None or value == "":
            continue
        if target in {"server_queue_depth", "server_active_workers"}:
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                continue
        result[target] = str(value) if target.startswith("server_") and target not in {
            "server_queue_depth", "server_active_workers"
        } else value
    identity_containers: list[dict[str, Any]] = [payload]
    for key in ("identity", "server_identity", "scope", "telemetry", "observability", "meta", "metadata"):
        value = payload.get(key)
        if isinstance(value, dict):
            identity_containers.append(value)
    identity_aliases = {
        "tenant_id": ("tenant_id", "tenantId", "tenant"),
        "account_id": ("account_id", "accountId", "account"),
        "workspace_id": ("workspace_id", "workspaceId", "workspace"),
        "user_id": ("user_id", "userId", "user"),
        "organization_id": ("organization_id", "organizationId", "organization", "org_id"),
    }
    identity: dict[str, Any] = {}
    for target, names in identity_aliases.items():
        for container in identity_containers:
            for name in names:
                value = container.get(name)
                if value not in (None, ""):
                    identity[target] = value
                    break
            if target in identity:
                break
        if target not in identity:
            for name in names:
                value = headers.get(name.lower())
                if value not in (None, ""):
                    identity[target] = value
                    break
    if identity:
        result["server_identity"] = identity
    return result


@dataclass
class _AdmissionEntry:
    operation: str
    tenant: str
    sequence: int
    enqueued_at: float


class AdmissionController:
    """Client-side admission controller used to compare workload policies.

    This controls the load generator's request admission, not an internal
    EchoMem queue. The report labels it accordingly.
    """

    def __init__(
        self,
        policy: str,
        capacity: int = 1,
        *,
        search_capacity: int | None = None,
        commit_capacity: int | None = None,
    ):
        self.policy = policy
        self.capacity = max(1, capacity)
        self.lane_capacities = {
            "search": max(1, search_capacity or self.capacity),
            "commit": max(1, commit_capacity or self.capacity),
        }
        self._condition = threading.Condition()
        self._queue: deque[_AdmissionEntry] = deque()
        self._tenant_queues: dict[str, deque[_AdmissionEntry]] = {}
        self._tenant_order: deque[str] = deque()
        self._lane_queues: dict[str, deque[_AdmissionEntry]] = {
            "search": deque(),
            "commit": deque(),
        }
        self._lane_active = {"search": 0, "commit": 0}
        self._tenant_lane_queues: dict[str, dict[str, deque[_AdmissionEntry]]] = {
            "search": {},
            "commit": {},
        }
        self._tenant_lane_order: dict[str, deque[str]] = {
            "search": deque(),
            "commit": deque(),
        }
        self._active = 0
        self._sequence = 0
        self._active_entries: dict[int, _AdmissionEntry] = {}

    def acquire(self, operation: str, tenant: str) -> tuple[float, int, int]:
        enqueued_at = time.monotonic()
        with self._condition:
            self._sequence += 1
            entry = _AdmissionEntry(operation, tenant, self._sequence, enqueued_at)
            if self.policy == "search-priority" and operation == "search":
                # Insert after existing Search entries but before queued Commit
                # entries. Running work still consumes the shared capacity.
                insert_at = len(self._queue)
                for index, queued in enumerate(self._queue):
                    if queued.operation != "search":
                        insert_at = index
                        break
                self._queue.insert(insert_at, entry)
            elif self.policy == "dual-lane":
                self._lane_queues[operation].append(entry)
            elif self.policy == "dual-lane-tenant-fair":
                queue = self._tenant_lane_queues[operation].setdefault(tenant, deque())
                queue.append(entry)
                if tenant not in self._tenant_lane_order[operation]:
                    self._tenant_lane_order[operation].append(tenant)
            elif self.policy == "tenant-fair":
                queue = self._tenant_queues.setdefault(tenant, deque())
                queue.append(entry)
                if tenant not in self._tenant_order:
                    self._tenant_order.append(tenant)
            else:
                self._queue.append(entry)
            while not self._is_turn(entry):
                self._condition.wait()
            self._active += 1
            if self.policy in {"dual-lane", "dual-lane-tenant-fair"}:
                self._lane_active[operation] += 1
            self._active_entries[threading.get_ident()] = entry
            wait_s = time.monotonic() - enqueued_at
            depth = self._queue_depth()
            return wait_s, depth, entry.sequence

    def release(self, operation: str, tenant: str) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            entry = self._active_entries.pop(threading.get_ident(), None)
            if self.policy == "dual-lane":
                queue = self._lane_queues[operation]
                if entry in queue:
                    queue.remove(entry)
                self._lane_active[operation] = max(0, self._lane_active[operation] - 1)
            elif self.policy == "dual-lane-tenant-fair":
                queues = self._tenant_lane_queues[operation]
                queue = queues.get(tenant)
                if queue:
                    if entry in queue:
                        queue.remove(entry)
                    else:
                        # Be defensive about thread-id reuse. A missing
                        # token must not leave a tenant permanently at the
                        # head of the scheduler.
                        queue.popleft()
                    if not queue:
                        queues.pop(tenant, None)
                        try:
                            self._tenant_lane_order[operation].remove(tenant)
                        except ValueError:
                            pass
                    elif self._tenant_lane_order[operation] and self._tenant_lane_order[operation][0] == tenant:
                        self._tenant_lane_order[operation].rotate(-1)
                self._lane_active[operation] = max(0, self._lane_active[operation] - 1)
            elif self.policy == "tenant-fair":
                queue = self._tenant_queues.get(tenant)
                if queue:
                    if entry in queue:
                        queue.remove(entry)
                    else:
                        queue.popleft()
                    if not queue:
                        self._tenant_queues.pop(tenant, None)
                        try:
                            self._tenant_order.remove(tenant)
                        except ValueError:
                            pass
                    elif self._tenant_order and self._tenant_order[0] == tenant:
                        self._tenant_order.rotate(-1)
            elif entry in self._queue:
                self._queue.remove(entry)
            self._condition.notify_all()

    def _queue_depth(self) -> int:
        if self.policy == "dual-lane":
            return max(
                0,
                sum(len(queue) for queue in self._lane_queues.values()) - self._active,
            )
        if self.policy == "dual-lane-tenant-fair":
            return max(
                0,
                sum(
                    len(queue)
                    for lane in self._tenant_lane_queues.values()
                    for queue in lane.values()
                )
                - self._active,
            )
        if self.policy == "tenant-fair":
            return max(
                0,
                sum(len(queue) for queue in self._tenant_queues.values())
                - self._active,
            )
        return max(0, len(self._queue) - self._active)

    def _is_turn(self, entry: _AdmissionEntry) -> bool:
        if self.policy == "dual-lane":
            queue = self._lane_queues[entry.operation]
            return (
                self._lane_active[entry.operation]
                < self.lane_capacities[entry.operation]
                and bool(queue)
                and queue[0] is entry
            )
        if self.policy == "dual-lane-tenant-fair":
            if self._lane_active[entry.operation] >= self.lane_capacities[entry.operation]:
                return False
            order = self._tenant_lane_order[entry.operation]
            if not order or order[0] != entry.tenant:
                return False
            queue = self._tenant_lane_queues[entry.operation].get(entry.tenant)
            return bool(queue and queue[0] is entry)
        if self._active >= self.capacity:
            return False
        if self.policy == "tenant-fair":
            if not self._tenant_order or self._tenant_order[0] != entry.tenant:
                return False
            queue = self._tenant_queues.get(entry.tenant)
            return bool(queue and queue[0] is entry)
        return bool(self._queue and self._queue[0] is entry)


@dataclass(frozen=True)
class TenantSpec:
    """One independently authenticated tenant for a real isolation run."""

    tenant_id: str
    auth_key: str
    user_id: str = ""
    account_id: str = ""
    agent_id: str = "echomem-stress"
    auth_key_source: str = "explicit"


def load_tenant_specs(
    path: str | Path,
    environ: dict[str, str] | None = None,
) -> list[TenantSpec]:
    """Load tenant identities while keeping credentials out of report data."""
    env = environ if environ is not None else os.environ
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    entries = payload.get("tenants") if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise ValueError("tenant config must contain a non-empty tenants list")
    specs: list[TenantSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"tenant config entry {index} must be an object")
        tenant_id = str(item.get("tenant_id") or item.get("id") or "").strip()
        if not tenant_id:
            raise ValueError(f"tenant config entry {index} has no tenant_id")
        if tenant_id in seen:
            raise ValueError(f"duplicate tenant_id: {tenant_id}")
        seen.add(tenant_id)
        key_env = str(item.get("auth_key_env") or "").strip()
        auth_key = str(item.get("auth_key") or "").strip()
        source = "explicit"
        if key_env:
            auth_key = str(env.get(key_env) or "").strip()
            source = f"env:{key_env}"
        if not auth_key:
            raise ValueError(
                f"tenant {tenant_id} has no auth key; set {key_env or 'auth_key'}"
            )
        specs.append(
            TenantSpec(
                tenant_id=tenant_id,
                auth_key=auth_key,
                user_id=str(item.get("user_id") or "").strip(),
                account_id=str(item.get("account_id") or tenant_id).strip() or tenant_id,
                agent_id=str(item.get("agent_id") or "echomem-stress").strip(),
                auth_key_source=source,
            )
        )
    return specs


@dataclass
class CommitRecord:
    tenant: str
    session_id: str
    archive_id: str
    accepted_at: str
    completed_at: str = ""
    status: str = ""
    elapsed_s: float = 0.0
    message_ids: list[str] = field(default_factory=list)
    message_events: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    queued_at: str = ""
    started_at: str = ""
    queue_wait_s: float = 0.0
    service_s: float = 0.0
    end_to_end_s: float = 0.0
    queue_depth_at_enqueue: int = 0
    operation_id: str = ""
    admission_wait_s: float = 0.0
    admission_queue_depth: int = 0
    admission_order: int = 0
    request_id: str = ""
    status_code: int | None = None
    retry_after_s: float | None = None
    server_received_at: str = ""
    server_queue_entered_at: str = ""
    server_execution_started_at: str = ""
    server_finished_at: str = ""
    server_queue_depth: int | None = None
    server_active_workers: int | None = None
    server_terminal_status: str = ""
    scheduled_at: str = ""
    schedule_lateness_s: float = 0.0


@dataclass
class SearchRecord:
    tenant: str
    session_id: str
    started_at: str
    elapsed_s: float
    status_code: int | None
    result_count: int = 0
    error: str = ""
    queued_at: str = ""
    finished_at: str = ""
    queue_wait_s: float = 0.0
    service_s: float = 0.0
    end_to_end_s: float = 0.0
    queue_depth_at_enqueue: int = 0
    operation_id: str = ""
    admission_wait_s: float = 0.0
    admission_queue_depth: int = 0
    admission_order: int = 0
    request_id: str = ""
    retry_after_s: float | None = None
    server_received_at: str = ""
    server_queue_entered_at: str = ""
    server_execution_started_at: str = ""
    server_finished_at: str = ""
    server_queue_depth: int | None = None
    server_active_workers: int | None = None
    server_terminal_status: str = ""
    scheduled_at: str = ""
    schedule_lateness_s: float = 0.0


@dataclass
class ResourceSample:
    elapsed_s: float
    timestamp: str
    rss_mb: float
    cpu_percent: float | None
    threads: int | None
    fds: int | None
    available_mb: float | None
    swap_used_mb: float | None


@dataclass
class ServerMetricSample:
    elapsed_s: float
    timestamp: str
    status_code: int | None
    metric_count: int
    metrics: dict[str, float] = field(default_factory=dict)
    error: str = ""


def parse_prometheus_metrics(raw: str) -> dict[str, float]:
    """Aggregate simple Prometheus samples by metric family."""
    values: dict[str, float] = {}
    pattern = re.compile(
        r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$"
    )
    for line in raw.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        name, value = match.groups()
        try:
            values[name] = values.get(name, 0.0) + float(value)
        except ValueError:
            continue
    return values


class ServerMetricsSampler:
    """Poll EchoMem /metrics and retain raw server telemetry."""

    def __init__(self, client: "EchoMemHTTP", interval_s: float):
        self.client = client
        self.interval_s = max(0.2, interval_s)
        self.samples: list[ServerMetricSample] = []
        self.raw_samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name="echomem-server-metrics", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(1.0, self.interval_s * 2))

    def _run(self) -> None:
        while not self._stop.is_set():
            elapsed = time.monotonic() - self._started
            response = self.client._request_raw("GET", "/metrics", timeout_s=5.0)
            raw = ""
            if isinstance(response.payload, dict):
                raw = str(response.payload.get("raw") or "")
            metrics = parse_prometheus_metrics(raw)
            sample = ServerMetricSample(
                elapsed_s=elapsed,
                timestamp=now_iso(),
                status_code=response.status_code,
                metric_count=len(metrics),
                metrics=metrics,
                error=response.error,
            )
            self.samples.append(sample)
            self.raw_samples.append({
                "elapsed_s": elapsed,
                "timestamp": sample.timestamp,
                "status_code": response.status_code,
                "error": response.error,
                "raw": raw,
            })
            self._stop.wait(self.interval_s)


class EchoMemHTTP:
    def __init__(
        self,
        base_url: str,
        auth_key: str = "",
        timeout_s: float = 60.0,
        *,
        tenant_id: str = "",
        user_id: str = "",
        account_id: str = "",
        agent_id: str = "echomem-stress",
        admission: AdmissionController | None = None,
        auth_header: str = "X-API-Key",
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_key = auth_key
        self.timeout_s = timeout_s
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.account_id = account_id or tenant_id
        self.agent_id = agent_id
        self.admission = admission
        self.auth_header = auth_header
        self.last_identity: dict[str, Any] = {}

    def _request_raw(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> HttpResult:
        started = time.monotonic()
        client_request_id = f"stress-{uuid.uuid4().hex}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        # Keep the generated ID on every real request so a deployment can
        # correlate the harness row with access logs/traces even when the
        # response body has no observability block.
        headers["X-Request-ID"] = client_request_id
        headers["X-EchoMem-Stress-Request-ID"] = client_request_id
        if self.auth_key:
            if self.auth_header.lower() == "authorization":
                headers["Authorization"] = (
                    self.auth_key
                    if self.auth_key.lower().startswith("bearer ")
                    else f"Bearer {self.auth_key}"
                )
            else:
                headers[self.auth_header] = self.auth_key
        data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s or self.timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {"raw": raw[:10000]}
                headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                observability = _server_observability(payload, headers)
                return HttpResult(
                    method, path, response.status, time.monotonic() - started,
                    payload, headers=headers, client_request_id=client_request_id,
                    **observability,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw[:1000]}
            retry_after = exc.headers.get("Retry-After")
            try:
                retry_after_value = float(retry_after) if retry_after else None
            except ValueError:
                retry_after_value = None
            headers = {
                str(key).lower(): str(value)
                for key, value in exc.headers.items()
            }
            observability = _server_observability(payload, headers)
            return HttpResult(
                method, path, exc.code, time.monotonic() - started,
                payload, f"HTTP {exc.code}", retry_after_value, headers=headers,
                client_request_id=client_request_id, **observability,
            )
        except Exception as exc:  # transport errors are environment errors at scenario level
            return HttpResult(
                method,
                path,
                None,
                time.monotonic() - started,
                {},
                f"{type(exc).__name__}: {exc}",
                client_request_id=client_request_id,
            )

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        *,
        operation: str = "setup",
    ) -> HttpResult:
        tenant = self.tenant_id or self.account_id or "default"
        wait_s = queue_depth = order = 0
        if self.admission and operation in {"commit", "search"}:
            wait_s, queue_depth, order = self.admission.acquire(operation, tenant)
        try:
            result = self._request_raw(method, path, body, timeout_s)
        finally:
            if self.admission and operation in {"commit", "search"}:
                self.admission.release(operation, tenant)
        result.admission_wait_s = wait_s
        result.admission_queue_depth = queue_depth
        result.admission_order = order
        return result

    def health(self) -> HttpResult:
        return self.request("GET", "/health")

    def service_diagnosis(self) -> str:
        """Return a safe endpoint diagnosis for common wrong-port failures."""
        response = self._request_raw("GET", "/api-doc/openapi.json", timeout_s=5.0)
        payload = response.payload if isinstance(response.payload, dict) else {}
        title = str((payload.get("info") or {}).get("title") or "").strip()
        if title:
            return f"openapi title={title}"
        if response.status_code == 404:
            return "no /api-doc/openapi.json"
        return f"openapi status={response.status_code or 'transport_error'}"

    def open_session(self, tenant: str, session_name: str) -> tuple[str, str]:
        result = self.request("POST", "/api/sessions/open", {
            "agent_id": self.agent_id,
            "metadata": {
                "title": session_name,
            },
        })
        if result.status_code is None or result.status_code >= 400:
            diagnosis = ""
            if result.status_code == 404:
                try:
                    diagnosis = f"; endpoint diagnosis: {self.service_diagnosis()}"
                except Exception as exc:
                    diagnosis = f"; endpoint diagnosis unavailable: {type(exc).__name__}"
            raise RuntimeError(
                f"open session failed for {tenant}: "
                f"{result.error or result.payload}{diagnosis}"
            )
        scope = result.payload.get("scope") or {}
        identity: dict[str, Any] = dict(result.server_identity)
        for key in (
            "tenant_id",
            "account_id",
            "workspace_id",
            "user_id",
            "organization_id",
        ):
            value = result.payload.get(key)
            if value in (None, ""):
                value = scope.get(key) if isinstance(scope, dict) else None
            if value not in (None, ""):
                identity[key] = value
        self.last_identity = identity
        session_id = result.payload.get("session_id") or result.payload.get("id") or scope.get("session_id")
        if not session_id:
            raise RuntimeError(f"open session returned no session_id: {result.payload}")
        return str(session_id), tenant

    def add_message(self, session_id: str, message_id: str, content: str) -> HttpResult:
        return self.request("POST", f"/api/sessions/{session_id}/messages", {
            "role": "user", "content": content, "metadata": {"stress_message_id": message_id},
        })

    def commit(self, session_id: str) -> HttpResult:
        return self.request(
            "POST",
            f"/api/sessions/{session_id}/commit",
            {"metadata": {"keep_recent_count": 0}},
            operation="commit",
        )

    def commit_status(self, session_id: str, archive_id: str) -> HttpResult:
        return self.request(
            "GET",
            f"/api/sessions/{session_id}/commits/{archive_id}",
            # Polling observes an existing commit and must not consume a
            # mutation-lane admission slot or inflate commit queue latency.
            operation="commit_poll",
        )

    def search(
        self,
        session_id: str,
        query: str,
        timeout_s: float,
        *,
        include_debug: bool = True,
    ) -> HttpResult:
        return self.request("POST", "/api/retrieval/search", {
            "query": query, "agent_id": self.agent_id, "session_id": session_id,
            "limit": 10, "include_explain": False, "include_debug": include_debug,
        }, timeout_s=timeout_s, operation="search")


def client_for(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    tenant: str,
) -> EchoMemHTTP:
    if isinstance(client_or_clients, dict):
        try:
            return client_or_clients[tenant]
        except KeyError as exc:
            raise RuntimeError(f"no authenticated client configured for tenant {tenant}") from exc
    return client_or_clients


def payload_contains(payload: dict[str, Any], text: str) -> bool:
    """Search returned evidence without assuming one response schema."""
    try:
        return text in json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return False


def retrieval_items(payload: dict[str, Any]) -> list[Any]:
    """Return retrieval evidence while excluding request/debug metadata."""
    items: list[Any] = []
    containers = [payload]
    for key in ("result", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in ("items", "results", "memories", "matches", "evidence"):
            value = container.get(key)
            if isinstance(value, list):
                items.extend(value)
    return items


def retrieval_contains(payload: dict[str, Any], text: str) -> bool:
    """Check marker presence only in returned retrieval items."""
    try:
        return text in json.dumps(
            retrieval_items(payload),
            ensure_ascii=False,
            default=str,
        )
    except (TypeError, ValueError):
        return False


def isolation_probe_query(writer: str, marker: str = "") -> str:
    """Build a precise probe query while keeping debug echo fields disabled."""
    marker_clause = f" The exact marker is {marker}." if marker else ""
    return (
        f"What private isolation marker did tenant {writer} write? "
        "Return only the matching memory content."
        f"{marker_clause}"
    )


def isolation_probe_counts(probes: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize isolation outcomes without collapsing error categories."""
    same_tenant = [probe for probe in probes if probe.get("same_tenant")]
    cross_tenant = [probe for probe in probes if not probe.get("same_tenant")]
    same_hits = sum(bool(probe.get("marker_found")) for probe in same_tenant)
    cross_false_positives = sum(
        bool(probe.get("marker_found")) for probe in cross_tenant
    )
    return {
        "same_tenant_probe_count": len(same_tenant),
        "same_tenant_hit_count": same_hits,
        "same_tenant_false_negative_count": len(same_tenant) - same_hits,
        "cross_tenant_probe_count": len(cross_tenant),
        "cross_tenant_false_positive_count": cross_false_positives,
        "cross_tenant_clean_count": len(cross_tenant) - cross_false_positives,
        "same_tenant_hit_rate": (
            same_hits / len(same_tenant) if same_tenant else None
        ),
        "cross_tenant_false_positive_rate": (
            cross_false_positives / len(cross_tenant) if cross_tenant else None
        ),
    }


def run_isolation_probe(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    tenants: list[str],
    retries: int = 3,
    retry_interval_s: float = 2.0,
    markers_per_tenant: int = 5,
) -> dict[str, Any]:
    """Probe every directed tenant pair with unique markers.

    A single writer/reader pair is not enough: isolation must hold for every
    writer and every other authenticated tenant.
    """
    if not isinstance(client_or_clients, dict) or len(tenants) < 2:
        return {
            "status": INCONCLUSIVE,
            "reason": "requires at least two independently authenticated tenants",
        }
    probes: list[dict[str, Any]] = []
    marker_count = max(1, int(markers_per_tenant))
    writers: dict[str, dict[str, Any]] = {}
    identity_observations: dict[str, dict[str, Any]] = {}
    for writer in tenants:
        writer_client = client_for(client_or_clients, writer)
        markers = [
            f"echomem-isolation-{writer}-{uuid.uuid4().hex}"
            for _ in range(marker_count)
        ]
        writer_session, _ = writer_client.open_session(
            writer, f"isolation-writer-{writer}-{uuid.uuid4().hex}"
        )
        identity_observations[writer] = dict(writer_client.last_identity)
        for marker in markers:
            message_id = f"isolation-{uuid.uuid4().hex}"
            add_result = writer_client.add_message(
                writer_session,
                message_id,
                f"Tenant {writer} private marker {marker}",
            )
            if add_result.status_code is None or add_result.status_code >= 400:
                return {
                    "status": ENVIRONMENT_ERROR,
                    "reason": "writer add_message failed",
                    "writer": writer,
                    "marker": marker,
                    "add_status": add_result.status_code,
                    "add_error": add_result.error,
                    "probes": probes,
                }
        commit_result = writer_client.commit(writer_session)
        archive_id = extract_archive(commit_result.payload)
        if commit_result.status_code is None or commit_result.status_code >= 400 or not archive_id:
            return {
                "status": ENVIRONMENT_ERROR,
                "reason": "writer commit was not accepted",
                "writer": writer,
                "commit_status": commit_result.status_code,
                "commit_error": commit_result.error,
                "probes": probes,
            }
        record = CommitRecord(
            writer,
            writer_session,
            archive_id,
            now_iso(),
            status="accepted",
            service_s=commit_result.elapsed_s,
            operation_id=f"isolation-commit-{uuid.uuid4().hex}",
            request_id=commit_result.request_id,
            status_code=commit_result.status_code,
            retry_after_s=commit_result.retry_after_s,
            server_received_at=commit_result.server_received_at,
            server_queue_entered_at=commit_result.server_queue_entered_at,
            server_execution_started_at=commit_result.server_execution_started_at,
            server_finished_at=commit_result.server_finished_at,
            server_queue_depth=commit_result.server_queue_depth,
            server_active_workers=commit_result.server_active_workers,
            server_terminal_status=commit_result.server_terminal_status,
        )
        poll_commit(client_or_clients, record, timeout_s=600.0, interval_s=2.0)
        if record.status not in {"completed", "complete", "transcommit", "succeeded", "success"}:
            return {
                "status": ENVIRONMENT_ERROR,
                "reason": "writer commit did not complete",
                "writer": writer,
                "commit_status": record.status,
                "commit_error": record.error,
                "probes": probes,
            }
        writers[writer] = {
            "markers": markers,
            "session_id": writer_session,
            "archive_id": archive_id,
            "commit_request_id": commit_result.request_id,
        }

    for writer in tenants:
        writer_data = writers[writer]
        for marker_index, marker in enumerate(writer_data["markers"], start=1):
            for reader in tenants:
                reader_client = client_for(client_or_clients, reader)
                reader_session = (
                    writer_data["session_id"]
                    if reader == writer
                    else reader_client.open_session(
                        reader, f"isolation-reader-{writer}-{marker_index}-{uuid.uuid4().hex}"
                    )[0]
                )
                attempts: list[dict[str, Any]] = []
                attempt_count = max(1, retries if reader == writer else 1)
                result = None
                found = False
                for attempt in range(attempt_count):
                    result = reader_client.search(
                        reader_session,
                        isolation_probe_query(writer, marker),
                        timeout_s=40.0,
                        include_debug=False,
                    )
                    found = (
                        result.status_code is not None
                        and 200 <= result.status_code < 300
                        and retrieval_contains(result.payload, marker)
                    )
                    attempts.append({
                        "attempt": attempt + 1,
                        "status_code": result.status_code,
                        "latency_s": result.elapsed_s,
                        "request_id": result.request_id,
                        "marker_found": found,
                        "error": result.error,
                    })
                    if found or reader != writer or attempt + 1 >= attempt_count:
                        break
                    time.sleep(max(0.0, retry_interval_s))
                assert result is not None
                probes.append({
                    "writer": writer,
                    "reader": reader,
                    "marker": marker,
                    "marker_index": marker_index,
                    "writer_commit_request_id": writer_data["commit_request_id"],
                    "same_tenant": writer == reader,
                    "status_code": result.status_code,
                    "latency_s": result.elapsed_s,
                    "request_id": result.request_id,
                    "retry_after_s": result.retry_after_s,
                    "marker_found": found,
                    "expected": writer == reader,
                    "error": result.error,
                    "attempts": attempts,
                })
    invalid = [
        probe for probe in probes
        if probe["marker_found"] != probe["expected"]
    ]
    stable_identity_fields = (
        "tenant_id",
        "account_id",
        "workspace_id",
        "user_id",
        "organization_id",
    )
    identity_mapping_status = (
        "VERIFIED_DISTINCT"
        if len(identity_observations) >= 2
        and all(
            any(observation.get(field_name) not in (None, "")
                for field_name in stable_identity_fields)
            for observation in identity_observations.values()
        )
        and any(
            len({
                str(observation.get(field_name))
                for observation in identity_observations.values()
                if observation.get(field_name) not in (None, "")
            }) == len(identity_observations)
            for field_name in stable_identity_fields
        )
        else "UNVERIFIED"
    )
    if invalid or len(probes) != len(tenants) * len(tenants) * marker_count:
        status = FAIL
    elif identity_mapping_status != "VERIFIED_DISTINCT":
        status = INCONCLUSIVE
    else:
        status = PASS
    return {
        "status": status,
        "probe_count": len(probes),
        "markers_per_tenant": marker_count,
        "expected_probe_count": len(tenants) * len(tenants) * marker_count,
        "invalid_probe_count": len(invalid),
        **isolation_probe_counts(probes),
        "identity_observations": identity_observations,
        "identity_mapping_status": identity_mapping_status,
        "probes": probes,
        "expected": "every same-tenant probe finds its marker; every cross-tenant probe does not",
    }


class ResourceSampler:
    def __init__(self, pid: int | None, interval_s: float):
        self.pid, self.interval_s = pid, max(0.2, interval_s)
        self.samples: list[ResourceSample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._last_cpu: tuple[int, float] | None = None

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="echomem-resource-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(1.0, self.interval_s * 2))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self._read())
            self._stop.wait(self.interval_s)

    def _read(self) -> ResourceSample:
        elapsed = time.monotonic() - self._started
        rss_mb = cpu = None
        threads = fds = None
        if self.pid:
            proc = Path(f"/proc/{self.pid}")
            try:
                rss_mb = int((proc / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
                stat = (proc / "stat").read_text().split()
                total_ticks = int(stat[13]) + int(stat[14])
                stamp = time.monotonic()
                if self._last_cpu:
                    old_ticks, old_stamp = self._last_cpu
                    cpu = max(0.0, (total_ticks - old_ticks) / os.sysconf("SC_CLK_TCK") / max(0.001, stamp - old_stamp) * 100)
                self._last_cpu = (total_ticks, stamp)
                threads = int(stat[19])
                fds = len(list((proc / "fd").iterdir()))
            except (FileNotFoundError, PermissionError, IndexError, ValueError):
                pass
        available_mb = swap_mb = None
        try:
            meminfo = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                meminfo[key] = float(value.strip().split()[0]) / 1024
            available_mb, swap_total = meminfo.get("MemAvailable"), meminfo.get("SwapTotal")
            swap_free = meminfo.get("SwapFree")
            swap_mb = swap_total - swap_free if swap_total is not None and swap_free is not None else None
        except (FileNotFoundError, ValueError):
            pass
        return ResourceSample(elapsed, now_iso(), rss_mb or 0.0, cpu, threads, fds, available_mb, swap_mb)


def status_from(payload: dict[str, Any]) -> str:
    raw = payload.get("status")
    if isinstance(raw, dict):
        raw = raw.get("status") or raw.get("stage") or raw.get("state")
    return str(raw or payload.get("stage") or payload.get("state") or "").lower()


def extract_archive(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if not isinstance(result, dict):
        result = {}
    return str(payload.get("archive_id") or payload.get("task_id") or result.get("archive_id") or result.get("task_id") or payload.get("id") or "")


def poll_commit(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    record: CommitRecord,
    timeout_s: float,
    interval_s: float,
) -> None:
    client = client_for(client_or_clients, record.tenant)
    started = time.monotonic()
    workload_started: float | None = None
    while time.monotonic() - started <= timeout_s:
        response = client.commit_status(record.session_id, record.archive_id)
        record.admission_wait_s += response.admission_wait_s
        record.admission_queue_depth = max(
            record.admission_queue_depth, response.admission_queue_depth
        )
        record.admission_order = max(record.admission_order, response.admission_order)
        for field_name in (
            "server_received_at",
            "server_queue_entered_at",
            "server_execution_started_at",
            "server_finished_at",
            "server_terminal_status",
        ):
            value = getattr(response, field_name)
            if value:
                setattr(record, field_name, value)
        if response.server_queue_depth is not None:
            record.server_queue_depth = response.server_queue_depth
        if response.server_active_workers is not None:
            record.server_active_workers = response.server_active_workers
        state = status_from(response.payload)
        if response.status_code is None:
            record.status, record.error = "transport_error", response.error
            break
        if response.status_code >= 400 or state in {"failed", "transfail", "error"}:
            record.status, record.error = state or response.error, response.error or json.dumps(response.payload)
            break
        if state in {"completed", "complete", "transcommit", "succeeded", "success"}:
            record.status = state
            break
        time.sleep(interval_s)
    if not record.status:
        record.status, record.error = "timeout", f"commit did not complete within {timeout_s:g}s"
    record.completed_at = now_iso()
    record.elapsed_s = time.monotonic() - started
    # service_s already includes the POST /commit request. Add status polling
    # time so the report reflects end-to-end commit completion latency.
    record.service_s += record.elapsed_s
    record.end_to_end_s = record.queue_wait_s + record.service_s


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def scenario_search(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    sessions: list[tuple[str, str]],
    duration_s: float,
    rps: float,
    timeout_s: float,
    workers: int = 2,
) -> list[SearchRecord]:
    """Generate a fixed-rate Search arrival stream.

    The old implementation waited for each HTTP response before scheduling the
    next request, so slow Search responses silently reduced the offered load.
    Here arrivals are scheduled against a monotonic clock and requests execute
    in a bounded worker pool; queue wait and queue depth are recorded.
    """
    if not sessions or duration_s <= 0:
        return []
    futures = {}
    completed_records: list[SearchRecord] = []
    start_at = time.monotonic()
    start_wall = time.time()
    interval = 1.0 / max(0.01, rps)
    target_count = max(1, math.ceil(duration_s * rps))
    submitted = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        while submitted < target_count:
            target_at = start_at + submitted * interval
            scheduled_at = datetime.fromtimestamp(
                start_wall + submitted * interval, timezone.utc
            ).isoformat()
            delay = target_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            # Futures remain in the dictionary until the final collection.
            # Remove completed work first so this queue-depth estimate only
            # describes requests still waiting for a worker.
            for future in list(futures):
                if future.done():
                    completed_records.append(future.result())
                    futures.pop(future, None)
            tenant, session_id = sessions[submitted % len(sessions)]
            queued_mono = time.monotonic()
            queued_at = now_iso()
            queue_depth = max(0, len(futures) - max(1, workers) + 1)

            def search_one(
                tenant_name: str = tenant,
                sid: str = session_id,
                enqueued_mono: float = queued_mono,
                enqueued_at: str = queued_at,
                depth: int = queue_depth,
                scheduled_time: str = scheduled_at,
                scheduled_lateness: float = max(0.0, queued_mono - target_at),
            ) -> SearchRecord:
                started_mono = time.monotonic()
                started_at = now_iso()
                operation_id = f"search-{uuid.uuid4().hex}"
                client = client_for(client_or_clients, tenant_name)
                response = client.search(
                    sid, f"stress query {uuid.uuid4().hex[:8]}", timeout_s
                )
                finished_at = now_iso()
                # EchoMemHTTP.response.elapsed_s measures the actual HTTP
                # request.  The worker-side interval also contains client
                # admission wait, so do not label it as server service time.
                service_s = response.elapsed_s
                queue_wait_s = max(0.0, started_mono - enqueued_mono)
                count = (
                    response.payload.get("items")
                    or response.payload.get("result", {}).get("items", [])
                    if isinstance(response.payload, dict)
                    else []
                )
                return SearchRecord(
                    tenant_name,
                    sid,
                    started_at,
                    response.elapsed_s,
                    response.status_code,
                    len(count) if isinstance(count, list) else 0,
                    response.error,
                    queued_at=enqueued_at,
                    finished_at=finished_at,
                    queue_wait_s=queue_wait_s,
                    service_s=service_s,
                    end_to_end_s=queue_wait_s + service_s,
                    scheduled_at=scheduled_time,
                    schedule_lateness_s=scheduled_lateness,
                    queue_depth_at_enqueue=depth,
                    operation_id=operation_id,
                    admission_wait_s=response.admission_wait_s,
                    admission_queue_depth=response.admission_queue_depth,
                    admission_order=response.admission_order,
                    request_id=response.request_id,
                    retry_after_s=response.retry_after_s,
                    server_received_at=response.server_received_at,
                    server_queue_entered_at=response.server_queue_entered_at,
                    server_execution_started_at=response.server_execution_started_at,
                    server_finished_at=response.server_finished_at,
                    server_queue_depth=response.server_queue_depth,
                    server_active_workers=response.server_active_workers,
                    server_terminal_status=response.server_terminal_status,
                )

            futures[executor.submit(search_one)] = submitted
            submitted += 1
        completed_records.extend(future.result() for future in futures)
    return completed_records


def provision_sessions(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    tenants: list[str],
    sessions_per_tenant: int,
) -> list[tuple[str, str]]:
    sessions: list[tuple[str, str]] = []
    for tenant in tenants:
        client = client_for(client_or_clients, tenant)
        for index in range(sessions_per_tenant):
            sessions.append((tenant, client.open_session(tenant, f"stress-{tenant}-{index}")[0]))
    return sessions


def run_commits(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    sessions: list[tuple[str, str]],
    messages_per_session: int,
    commit_timeout_s: float,
    poll_interval_s: float,
    workers: int,
) -> list[CommitRecord]:
    records: list[CommitRecord] = []
    for tenant, session_id in sessions:
        client = client_for(client_or_clients, tenant)
        message_ids = []
        message_events: list[dict[str, Any]] = []
        for index in range(messages_per_session):
            message_id = f"stress-{uuid.uuid4().hex}"
            content = f"EchoMem stress message {tenant} {index} " + ("x" * 1200)
            sent_at = now_iso()
            response = client.add_message(session_id, message_id, content)
            message_events.append({
                "tenant": tenant,
                "session_id": session_id,
                "message_id": message_id,
                "index": index,
                "sent_at": sent_at,
                "content_preview": content[:160],
                "content_chars": len(content),
                "status": "sent" if response.status_code is not None and response.status_code < 400 else "failed",
                "status_code": response.status_code,
                "request_id": response.request_id,
                "error": response.error or "",
            })
            if response.status_code is None or response.status_code >= 400:
                records.append(CommitRecord(tenant, session_id, "", now_iso(), status="message_failed", error=response.error or str(response.status_code), message_ids=[message_id], message_events=message_events[-1:]))
            else:
                message_ids.append(message_id)
        if message_ids:
            response = client.commit(session_id)
            archive_id = extract_archive(response.payload)
            records.append(CommitRecord(tenant, session_id, archive_id, now_iso(), message_ids=message_ids,
                                        message_events=message_events,
                                        status="accepted" if response.status_code and response.status_code < 400 and archive_id else "commit_rejected",
                                        error=response.error,
                                        operation_id=f"commit-{uuid.uuid4().hex}"))
    pending = [record for record in records if record.status == "accepted" and record.archive_id]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(
                poll_commit,
                client_or_clients,
                record,
                commit_timeout_s,
                poll_interval_s,
            )
            for record in pending
        ]
        for future in as_completed(futures):
            future.result()
    return records


def run_parallel_workload(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    sessions: list[tuple[str, str]],
    messages_per_session: int,
    duration_s: float,
    search_rps: float,
    commit_timeout_s: float,
    poll_interval_s: float,
    commit_workers: int,
    search_timeout_s: float,
    search_workers: int,
    commit_rpm: float = 0.0,
) -> tuple[list[CommitRecord], list[SearchRecord]]:
    """Run the write/commit and search lanes concurrently.

    Messages are prepared before the timed phase so the measured interval
    contains real commit work and real search work at the same time.
    """
    prepared: list[tuple[str, str, list[str], list[dict[str, Any]]]] = []
    for tenant, session_id in sessions:
        message_ids: list[str] = []
        message_events: list[dict[str, Any]] = []
        for index in range(messages_per_session):
            message_id = f"stress-{uuid.uuid4().hex}"
            client = client_for(client_or_clients, tenant)
            content = f"EchoMem stress message {tenant} {index} " + ("x" * 1200)
            sent_at = now_iso()
            response = client.add_message(
                session_id,
                message_id,
                content,
            )
            message_events.append({
                "tenant": tenant,
                "session_id": session_id,
                "message_id": message_id,
                "index": index,
                "sent_at": sent_at,
                "content_preview": content[:160],
                "content_chars": len(content),
                "status": "sent" if response.status_code is not None and response.status_code < 400 else "failed",
                "status_code": response.status_code,
                "request_id": response.request_id,
                "error": response.error or "",
            })
            if response.status_code is None or response.status_code >= 400:
                raise RuntimeError(
                    f"message preparation failed for {tenant}/{session_id}: "
                    f"{response.error or response.status_code}"
                )
            message_ids.append(message_id)
        prepared.append((tenant, session_id, message_ids, message_events))

    def commit_prepared() -> list[CommitRecord]:
        records: list[CommitRecord] = []
        with ThreadPoolExecutor(max_workers=max(1, commit_workers)) as executor:
            accepted: list[CommitRecord] = []
            futures = {}
            for tenant, session_id, message_ids, message_events in prepared:
                queued_mono = time.monotonic()
                queued_at = now_iso()
                queue_depth = len(futures)

                def submit_commit(
                    sid: str = session_id,
                    tenant_name: str = tenant,
                ) -> tuple[HttpResult, float, str]:
                    started_mono = time.monotonic()
                    response = client_for(client_or_clients, tenant_name).commit(sid)
                    return response, started_mono, now_iso()

                futures[executor.submit(submit_commit)] = (
                    tenant,
                    session_id,
                    message_ids,
                    message_events,
                    queued_mono,
                    queued_at,
                    queue_depth,
                )
            for future in as_completed(futures):
                tenant, session_id, message_ids, message_events, queued_mono, queued_at, queue_depth = futures[future]
                response, started_mono, started_at = future.result()
                archive_id = extract_archive(response.payload)
                record = CommitRecord(
                    tenant,
                    session_id,
                    archive_id,
                    now_iso(),
                    message_ids=message_ids,
                    message_events=message_events,
                    status="accepted"
                    if response.status_code
                    and response.status_code < 400
                    and archive_id
                    else "commit_rejected",
                    error=response.error,
                    queued_at=queued_at,
                    started_at=started_at,
                    queue_wait_s=max(0.0, started_mono - queued_mono),
                    service_s=response.elapsed_s,
                    queue_depth_at_enqueue=queue_depth,
                    operation_id=f"commit-{uuid.uuid4().hex}",
                    admission_wait_s=response.admission_wait_s,
                    admission_queue_depth=response.admission_queue_depth,
                    admission_order=response.admission_order,
                    request_id=response.request_id,
                    status_code=response.status_code,
                    retry_after_s=response.retry_after_s,
                    server_received_at=response.server_received_at,
                    server_queue_entered_at=response.server_queue_entered_at,
                    server_execution_started_at=response.server_execution_started_at,
                    server_finished_at=response.server_finished_at,
                    server_queue_depth=response.server_queue_depth,
                    server_active_workers=response.server_active_workers,
                    server_terminal_status=response.server_terminal_status,
                )
                records.append(record)
                if record.status == "accepted":
                    accepted.append(record)
            polls = [
                executor.submit(
                    poll_commit,
                    client_or_clients,
                    record,
                    commit_timeout_s,
                    poll_interval_s,
                )
                for record in accepted
            ]
            for future in as_completed(polls):
                future.result()
        return records

    def commit_stream() -> list[CommitRecord]:
        return run_commit_stream(
            client_or_clients,
            sorted({tenant for tenant, _ in sessions}),
            duration_s,
            commit_rpm,
            messages_per_session,
            commit_timeout_s,
            poll_interval_s,
            commit_workers,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        commit_future = executor.submit(
            commit_stream if commit_rpm > 0 else commit_prepared
        )
        searches = scenario_search(
            client_or_clients, sessions, duration_s, search_rps, search_timeout_s,
            workers=search_workers,
        )
        commits = commit_future.result()
    return commits, searches


def run_commit_stream(
    client_or_clients: EchoMemHTTP | dict[str, EchoMemHTTP],
    tenants: list[str],
    duration_s: float,
    commit_rpm: float,
    messages_per_commit: int,
    commit_timeout_s: float,
    poll_interval_s: float,
    workers: int,
) -> list[CommitRecord]:
    """Submit Commit requests at a fixed per-tenant arrival rate.

    Sessions and messages are prepared before the timed interval. Each commit
    uses a dedicated session, so repeated commits cannot accidentally overlap
    on the same session or turn the workload into an invalid mutation race.
    """
    if duration_s <= 0 or commit_rpm <= 0 or not tenants:
        return []
    jobs: list[tuple[float, str, str, list[str], list[dict[str, Any]]]] = []
    for tenant in tenants:
        count = max(1, math.ceil(commit_rpm * duration_s / 60.0))
        client = client_for(client_or_clients, tenant)
        for index in range(count):
            session_id = client.open_session(
                tenant, f"stress-commit-{tenant}-{index}"
            )[0]
            message_ids: list[str] = []
            message_events: list[dict[str, Any]] = []
            for message_index in range(messages_per_commit):
                message_id = f"stress-{uuid.uuid4().hex}"
                content = (
                    f"EchoMem fixed-rate commit {tenant} {index} {message_index} "
                    + ("x" * 1200)
                )
                sent_at = now_iso()
                response = client.add_message(
                    session_id,
                    message_id,
                    content,
                )
                message_events.append({
                    "tenant": tenant,
                    "session_id": session_id,
                    "message_id": message_id,
                    "index": message_index,
                    "sent_at": sent_at,
                    "content_preview": content[:160],
                    "content_chars": len(content),
                    "status": "sent" if response.status_code is not None and response.status_code < 400 else "failed",
                    "status_code": response.status_code,
                    "request_id": response.request_id,
                    "error": response.error or "",
                })
                if response.status_code is None or response.status_code >= 400:
                    raise RuntimeError(
                        f"commit message preparation failed for {tenant}/{session_id}: "
                        f"{response.error or response.status_code}"
                    )
                message_ids.append(message_id)
            offset = index * 60.0 / commit_rpm
            jobs.append((offset, tenant, session_id, message_ids, message_events))
    jobs.sort(key=lambda item: item[0])

    records: list[CommitRecord] = []
    start = time.monotonic()
    start_wall = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as commit_executor:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as poll_executor:
            futures = {}
            for offset, tenant, session_id, message_ids, message_events in jobs:
                target = start + offset
                scheduled_at = datetime.fromtimestamp(
                    start_wall + offset, timezone.utc
                ).isoformat()
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                queued_mono = time.monotonic()
                queued_at = now_iso()
                queue_depth = max(0, len(futures) - max(1, workers) + 1)

                def submit_commit(
                    tenant_name: str = tenant,
                    sid: str = session_id,
                ) -> tuple[HttpResult, float, str]:
                    started_mono = time.monotonic()
                    response = client_for(client_or_clients, tenant_name).commit(sid)
                    return response, started_mono, now_iso()

                futures[commit_executor.submit(submit_commit)] = (
                    tenant,
                    session_id,
                    message_ids,
                    message_events,
                    queued_mono,
                    queued_at,
                    queue_depth,
                    scheduled_at,
                    target,
                )
            poll_futures = []
            for future in as_completed(futures):
                (
                    tenant,
                    session_id,
                    message_ids,
                    message_events,
                    queued_mono,
                    queued_at,
                    queue_depth,
                    scheduled_at,
                    target,
                ) = futures[future]
                response, started_mono, started_at = future.result()
                archive_id = extract_archive(response.payload)
                record = CommitRecord(
                    tenant,
                    session_id,
                    archive_id,
                    now_iso(),
                    message_ids=message_ids,
                    message_events=message_events,
                    status=(
                        "accepted"
                        if response.status_code and response.status_code < 400 and archive_id
                        else "commit_rejected"
                    ),
                    error=response.error,
                    queued_at=queued_at,
                    started_at=started_at,
                    queue_wait_s=max(0.0, started_mono - queued_mono),
                    service_s=response.elapsed_s,
                    queue_depth_at_enqueue=queue_depth,
                    scheduled_at=scheduled_at,
                    schedule_lateness_s=max(0.0, queued_mono - target),
                    operation_id=f"commit-{uuid.uuid4().hex}",
                    admission_wait_s=response.admission_wait_s,
                    admission_queue_depth=response.admission_queue_depth,
                    admission_order=response.admission_order,
                    request_id=response.request_id,
                    status_code=response.status_code,
                    retry_after_s=response.retry_after_s,
                    server_received_at=response.server_received_at,
                    server_queue_entered_at=response.server_queue_entered_at,
                    server_execution_started_at=response.server_execution_started_at,
                    server_finished_at=response.server_finished_at,
                    server_queue_depth=response.server_queue_depth,
                    server_active_workers=response.server_active_workers,
                    server_terminal_status=response.server_terminal_status,
                )
                records.append(record)
                if record.status == "accepted":
                    poll_futures.append(
                        poll_executor.submit(
                            poll_commit,
                            client_or_clients,
                            record,
                            commit_timeout_s,
                            poll_interval_s,
                        )
                    )
            for future in poll_futures:
                future.result()
    return records


def scenario_status(
    records: list[CommitRecord],
    searches: list[SearchRecord],
    min_samples: int,
    p95_limit_s: float,
    *,
    target_commit: int | None = None,
    target_search: int | None = None,
    minimum_target_ratio: float = 0.99,
) -> tuple[str, dict[str, Any]]:
    commit_status, commit_details = commit_delivery_status(
        records,
        target_commit=target_commit,
        minimum_target_ratio=minimum_target_ratio,
    )
    search_status, search_details = search_latency_status(
        searches,
        min_samples=min_samples,
        p95_limit_s=p95_limit_s,
        target_search=target_search,
        minimum_target_ratio=minimum_target_ratio,
    )
    details = {**commit_details, **search_details}
    if INCONCLUSIVE in {commit_status, search_status}:
        return INCONCLUSIVE, details
    if FAIL in {commit_status, search_status}:
        return FAIL, details
    return PASS, details


def commit_delivery_status(
    records: list[CommitRecord],
    *,
    target_commit: int | None = None,
    minimum_target_ratio: float = 0.99,
) -> tuple[str, dict[str, Any]]:
    """Evaluate Commit completion without depending on Search samples."""
    success_states = {"completed", "complete", "transcommit", "succeeded", "success"}
    failures = [
        record for record in records if record.status not in success_states
    ]
    target_gaps: dict[str, int] = {}
    if target_commit is not None:
        target_gaps["commit"] = max(0, target_commit - len(records))
    if target_commit is not None and target_commit > 0 and (
        len(records) < math.ceil(target_commit * minimum_target_ratio)
    ):
        return INCONCLUSIVE, {
            "reason": "offered load target was not reached; latency is not representative",
            "commit_failures": len(failures),
            "target_gaps": target_gaps,
            "minimum_target_ratio": minimum_target_ratio,
        }
    if not records:
        return INCONCLUSIVE, {
            "reason": "insufficient Commit samples",
            "commit_failures": 0,
            "target_gaps": target_gaps,
        }
    return (
        FAIL if failures else PASS,
        {
            "commit_total": len(records),
            "commit_failures": len(failures),
            "target_gaps": target_gaps,
        },
    )


def search_latency_status(
    searches: list[SearchRecord],
    *,
    min_samples: int,
    p95_limit_s: float,
    target_search: int | None = None,
    minimum_target_ratio: float = 0.99,
) -> tuple[str, dict[str, Any]]:
    """Evaluate Search success and latency independently from Commit."""
    search_latencies = [
        record.elapsed_s
        for record in searches
        if record.status_code and 200 <= record.status_code < 300
    ]
    search_errors = [
        record for record in searches if not record.status_code or record.status_code >= 400
    ]
    target_gaps = {
        "search": max(0, target_search - len(searches))
    } if target_search is not None else {}
    target_invalid = (
        target_search is not None
        and target_search > 0
        and len(searches) < math.ceil(target_search * minimum_target_ratio)
    )
    details = {
        "search_total": len(searches),
        "search_errors": len(search_errors),
        "search_p95_s": percentile(search_latencies, 95),
        "search_limit_s": p95_limit_s,
        "target_gaps": target_gaps,
    }
    if not searches or len(searches) < min_samples:
        details["reason"] = "insufficient Search samples"
        return INCONCLUSIVE, details
    if target_invalid:
        details["reason"] = "offered load target was not reached; latency is not representative"
        details["minimum_target_ratio"] = minimum_target_ratio
        return INCONCLUSIVE, details
    if search_errors or details["search_p95_s"] is None:
        return FAIL, details
    return (
        PASS if details["search_p95_s"] <= p95_limit_s else FAIL,
        details,
    )


def numeric_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "mean_s": None,
            "min_s": None,
            "p50_s": None,
            "p90_s": None,
            "p95_s": None,
            "p99_s": None,
            "max_s": None,
            "total_s": 0.0,
        }
    return {
        "count": len(values),
        "mean_s": statistics.mean(values),
        "min_s": min(values),
        "p50_s": percentile(values, 50),
        "p90_s": percentile(values, 90),
        "p95_s": percentile(values, 95),
        "p99_s": percentile(values, 99),
        "max_s": max(values),
        "total_s": sum(values),
    }


def timestamp_delta(start: Any, end: Any) -> float | None:
    """Return an ISO timestamp delta, or None when the service did not emit it."""
    started = parse_iso_timestamp(str(start or ""))
    finished = parse_iso_timestamp(str(end or ""))
    if not started or not finished:
        return None
    value = (finished - started).total_seconds()
    return value if value >= 0 else None


def fairness_index(values: list[float]) -> float | None:
    """Jain's fairness index; values must be non-negative rates or shares."""
    usable = [float(value) for value in values if value is not None and value >= 0]
    if not usable or sum(value * value for value in usable) == 0:
        return None
    total = sum(usable)
    return total * total / (len(usable) * sum(value * value for value in usable))


def sequence_stats(values: list[str]) -> dict[str, Any]:
    """Summarize runs and switches in an observed operation sequence."""
    if not values:
        return {
            "count": 0,
            "runs": 0,
            "switches": 0,
            "max_streak": 0,
            "first": None,
            "last": None,
        }
    runs = 1
    switches = 0
    max_streak = 1
    current_streak = 1
    for previous, current in zip(values, values[1:]):
        if current == previous:
            current_streak += 1
        else:
            runs += 1
            switches += 1
            max_streak = max(max_streak, current_streak)
            current_streak = 1
    max_streak = max(max_streak, current_streak)
    return {
        "count": len(values),
        "runs": runs,
        "switches": switches,
        "max_streak": max_streak,
        "first": values[0],
        "last": values[-1],
    }


def scheduling_observation(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare request arrival order with the service's execution-start order.

    A client-side timestamp cannot prove server scheduling.  This function
    only emits server-order metrics for rows that contain the server execution
    timestamp, and reports coverage separately when the service omits it.
    """
    arrival_rows = sorted(
        timeline,
        key=lambda item: (
            parse_iso_timestamp(str(item.get("queued_at") or "")) is None,
            parse_iso_timestamp(str(item.get("queued_at") or ""))
            or datetime.max.replace(tzinfo=timezone.utc),
        ),
    )
    server_rows = [
        item for item in timeline
        if parse_iso_timestamp(str(item.get("server_execution_started_at") or ""))
    ]
    server_rows.sort(
        key=lambda item: parse_iso_timestamp(
            str(item.get("server_execution_started_at") or "")
        )
        or datetime.max.replace(tzinfo=timezone.utc)
    )
    inversions = 0
    comparable_pairs = 0
    arrival_position = {
        id(item): index for index, item in enumerate(arrival_rows)
    }
    for left_index, left in enumerate(server_rows):
        for right in server_rows[left_index + 1:]:
            left_arrival = arrival_position.get(id(left))
            right_arrival = arrival_position.get(id(right))
            if left_arrival is None or right_arrival is None:
                continue
            comparable_pairs += 1
            if left_arrival > right_arrival:
                inversions += 1
    arrival_ops = [
        str(item.get("operation"))
        for item in arrival_rows
        if item.get("operation") in {"commit", "search"}
    ]
    server_ops = [
        str(item.get("operation"))
        for item in server_rows
        if item.get("operation") in {"commit", "search"}
    ]
    operation_start_counts: dict[str, int] = {}
    for item in server_rows:
        key = str(item.get("operation") or "-")
        operation_start_counts[key] = operation_start_counts.get(key, 0) + 1
    search_started_ahead_of_commit = 0
    commit_search_pairs = 0
    for commit in server_rows:
        if commit.get("operation") != "commit":
            continue
        commit_start = parse_iso_timestamp(
            str(commit.get("server_execution_started_at") or "")
        )
        if not commit_start:
            continue
        for search in server_rows:
            if search.get("operation") != "search":
                continue
            search_start = parse_iso_timestamp(
                str(search.get("server_execution_started_at") or "")
            )
            if not search_start:
                continue
            commit_search_pairs += 1
            if search_start < commit_start:
                search_started_ahead_of_commit += 1
    return {
        "arrival_order_count": len(arrival_rows),
        "arrival_operation_sequence": sequence_stats(arrival_ops),
        "server_start_order_count": len(server_rows),
        "server_start_coverage": (
            len(server_rows) / len(timeline) if timeline else None
        ),
        "server_start_operation_sequence": sequence_stats(server_ops),
        "server_start_sequence": [
            {
                "order": index,
                "operation": item.get("operation"),
                "tenant": item.get("tenant"),
                "request_id": item.get("request_id"),
                "server_execution_started_at": item.get(
                    "server_execution_started_at"
                ),
            }
            for index, item in enumerate(server_rows, start=1)
        ],
        "arrival_vs_server_start_inversions": inversions,
        "arrival_vs_server_start_comparable_pairs": comparable_pairs,
        "arrival_vs_server_start_inversion_rate": (
            inversions / comparable_pairs if comparable_pairs else None
        ),
        "server_start_counts_by_operation": operation_start_counts,
        "search_started_ahead_of_commit_count": search_started_ahead_of_commit,
        "commit_search_comparable_pairs": commit_search_pairs,
        "server_scheduling_conclusion": (
            "evidence_available"
            if server_rows and len(server_rows) == len(timeline)
            else "insufficient_server_timing"
        ),
    }


def workload_metrics(
    commits: list[CommitRecord],
    searches: list[SearchRecord],
    tenants: list[str],
    duration_s: float,
    commit_delay_threshold_s: float,
    search_delay_threshold_s: float,
    time_bucket_s: float = 10.0,
) -> dict[str, Any]:
    """Build operator-facing timing and per-tenant measurements."""
    commit_success = [
        record for record in commits
        if record.status in {"completed", "complete", "transcommit", "succeeded", "success"}
    ]
    search_success = [
        record for record in searches
        if record.status_code is not None and 200 <= record.status_code < 300
    ]
    commit_delayed = [
        record for record in commits
        if record.end_to_end_s >= commit_delay_threshold_s
    ]
    search_delayed = [
        record for record in searches
        if record.service_s >= search_delay_threshold_s
    ]

    def status_counts(records: list[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            key = (
                str(record.status_code)
                if record.status_code is not None
                else "transport_error"
            )
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def retry_after_values(records: list[Any]) -> list[float]:
        return [
            float(record.retry_after_s)
            for record in records
            if record.retry_after_s is not None
        ]

    def server_observation(records: list[Any]) -> dict[str, Any]:
        """Aggregate server-side timings when EchoMem exposes them."""
        observed = [
            record for record in records
            if any(
                getattr(record, field_name, "")
                for field_name in (
                    "server_received_at",
                    "server_queue_entered_at",
                    "server_execution_started_at",
                    "server_finished_at",
                )
            )
        ]
        queue_values = [
            delta
            for record in records
            if (
                delta := timestamp_delta(
                    getattr(record, "server_queue_entered_at", ""),
                    getattr(record, "server_execution_started_at", ""),
                )
            ) is not None
        ]
        execution_values = [
            delta
            for record in records
            if (
                delta := timestamp_delta(
                    getattr(record, "server_execution_started_at", ""),
                    getattr(record, "server_finished_at", ""),
                )
            ) is not None
        ]
        end_to_end_values = [
            delta
            for record in records
            if (
                delta := timestamp_delta(
                    getattr(record, "server_received_at", ""),
                    getattr(record, "server_finished_at", ""),
                )
            ) is not None
        ]
        queue_depths = [
            getattr(record, "server_queue_depth")
            for record in records
            if getattr(record, "server_queue_depth", None) is not None
        ]
        active_workers = [
            getattr(record, "server_active_workers")
            for record in records
            if getattr(record, "server_active_workers", None) is not None
        ]
        timing_complete = bool(records) and len(observed) == len(records)
        queue_complete = bool(records) and len(queue_depths) == len(records)
        return {
            "observed_count": len(observed),
            "total_count": len(records),
            "missing_count": len(records) - len(observed),
            "queue_depth_observed_count": len(queue_depths),
            "active_workers_observed_count": len(active_workers),
            "queue_wait": numeric_stats(queue_values),
            "execution": numeric_stats(execution_values),
            "end_to_end": numeric_stats(end_to_end_values),
            "queue_depth": numeric_stats([float(value) for value in queue_depths]),
            "active_workers": numeric_stats([float(value) for value in active_workers]),
            "complete": timing_complete,
            "full_coverage": timing_complete and queue_complete,
        }

    def tenant_metrics(tenant: str) -> dict[str, Any]:
        tenant_commits = [record for record in commits if record.tenant == tenant]
        tenant_searches = [record for record in searches if record.tenant == tenant]
        tenant_commit_success = [record for record in commit_success if record.tenant == tenant]
        tenant_search_success = [record for record in search_success if record.tenant == tenant]
        def http_status_counts(records: list[Any]) -> dict[str, int]:
            counts: dict[str, int] = {}
            for record in records:
                code = (
                    str(record.status_code)
                    if record.status_code is not None
                    else "transport_error"
                )
                counts[code] = counts.get(code, 0) + 1
            return dict(sorted(counts.items()))
        return {
            "commit": {
                "submitted": len(tenant_commits),
                "completed": len(tenant_commit_success),
                "failed": len(tenant_commits) - len(tenant_commit_success),
                "http_status_counts": http_status_counts(tenant_commits),
                "rate_limited_count": sum(
                    record.status_code == 429 for record in tenant_commits
                ),
                "retry_after": numeric_stats(retry_after_values(tenant_commits)),
                "queue_wait": numeric_stats([record.queue_wait_s for record in tenant_commits]),
                "completion": numeric_stats([record.end_to_end_s for record in tenant_commit_success]),
                "service": numeric_stats([record.service_s for record in tenant_commit_success]),
                "server": server_observation(tenant_commits),
                "admission_wait": numeric_stats(
                    [record.admission_wait_s for record in tenant_commits]
                ),
                "schedule_lateness": numeric_stats(
                    [record.schedule_lateness_s for record in tenant_commits]
                ),
                "delayed_count": sum(
                    record.end_to_end_s >= commit_delay_threshold_s
                    for record in tenant_commits
                ),
                "delayed_at": [
                    record.completed_at
                    for record in tenant_commits
                    if record.end_to_end_s >= commit_delay_threshold_s
                ],
                "delayed": [
                    {
                        "request_id": record.request_id,
                        "queued_at": record.queued_at,
                        "started_at": record.started_at,
                        "completed_at": record.completed_at,
                        "duration_s": record.end_to_end_s,
                        "queue_wait_s": record.queue_wait_s,
                        "status": record.status,
                        "status_code": record.status_code,
                    }
                    for record in tenant_commits
                    if record.end_to_end_s >= commit_delay_threshold_s
                ],
            },
            "search": {
                "submitted": len(tenant_searches),
                "succeeded": len(tenant_search_success),
                "errors": len(tenant_searches) - len(tenant_search_success),
                "http_status_counts": http_status_counts(tenant_searches),
                "rate_limited_count": sum(
                    record.status_code == 429 for record in tenant_searches
                ),
                "retry_after": numeric_stats(retry_after_values(tenant_searches)),
                "latency": numeric_stats([record.service_s for record in tenant_search_success]),
                "server": server_observation(tenant_searches),
                "admission_wait": numeric_stats(
                    [record.admission_wait_s for record in tenant_searches]
                ),
                "schedule_lateness": numeric_stats(
                    [record.schedule_lateness_s for record in tenant_searches]
                ),
                "delayed_count": sum(
                    record.service_s >= search_delay_threshold_s
                    for record in tenant_searches
                ),
                "delayed": [
                    {
                        "request_id": record.request_id,
                        "queued_at": record.queued_at,
                        "started_at": record.started_at,
                        "finished_at": record.finished_at,
                        "duration_s": record.service_s,
                        "queue_wait_s": record.queue_wait_s,
                        "status_code": record.status_code,
                        "error": record.error,
                    }
                    for record in tenant_searches
                    if record.service_s >= search_delay_threshold_s
                ],
            },
        }

    per_tenant = {tenant: tenant_metrics(tenant) for tenant in sorted(set(tenants))}
    tenant_commit_p95 = {
        tenant: values["commit"]["completion"]["p95_s"]
        for tenant, values in per_tenant.items()
        if values["commit"]["completion"]["p95_s"] is not None
    }
    tenant_search_p95 = {
        tenant: values["search"]["latency"]["p95_s"]
        for tenant, values in per_tenant.items()
        if values["search"]["latency"]["p95_s"] is not None
    }
    def max_min_ratio(values: dict[str, float]) -> float | None:
        usable = [value for value in values.values() if value is not None and value > 0]
        return max(usable) / min(usable) if len(usable) >= 2 else None

    search_values = [record.service_s for record in search_success]
    admission_events = sorted(
        [
            {
                "order": record.admission_order,
                "operation": "commit",
                "tenant": record.tenant,
                "queued_at": record.queued_at,
                "started_at": record.started_at,
                "completed_at": record.completed_at,
                "wait_s": record.admission_wait_s,
                "queue_depth": record.admission_queue_depth,
                "end_to_end_s": record.end_to_end_s,
                "status": record.status,
            }
            for record in commits
            if record.admission_order
        ]
        + [
            {
                "order": record.admission_order,
                "operation": "search",
                "tenant": record.tenant,
                "queued_at": record.queued_at,
                "started_at": record.started_at,
                "completed_at": record.finished_at,
                "wait_s": record.admission_wait_s,
                "queue_depth": record.admission_queue_depth,
                "end_to_end_s": record.end_to_end_s,
                "status": str(record.status_code or record.error or "transport_error"),
            }
            for record in searches
            if record.admission_order
        ],
        key=lambda item: item["order"],
    )

    # Keep a complete, time-ordered workload trace in summary.json.  The
    # CSVs remain the source of truth, while this compact projection lets the
    # HTML report answer "when did tenant X slow down?" without parsing files.
    all_records: list[tuple[str, Any, str]] = [
        ("commit", record, record.queued_at or record.started_at)
        for record in commits
    ] + [
        ("search", record, record.queued_at or record.started_at)
        for record in searches
    ]
    anchor_candidates = [
        parsed
        for _, _, timestamp in all_records
        if (parsed := parse_iso_timestamp(timestamp)) is not None
    ]
    anchor = min(anchor_candidates) if anchor_candidates else None
    timeline: list[dict[str, Any]] = []
    for operation, record, timestamp in all_records:
        queued = parse_iso_timestamp(timestamp)
        offset_s = (queued - anchor).total_seconds() if queued and anchor else None
        if operation == "commit":
            status = record.status
            request_duration_s = record.end_to_end_s
            completed_at = record.completed_at
            status_code = record.status_code
            queue_wait_s = record.queue_wait_s
            admission_wait_s = record.admission_wait_s
            queue_depth = record.admission_queue_depth
            request_id = record.request_id
            threshold_s = commit_delay_threshold_s
        else:
            status = str(record.status_code or record.error or "transport_error")
            request_duration_s = record.end_to_end_s
            completed_at = record.finished_at
            status_code = record.status_code
            queue_wait_s = record.queue_wait_s
            admission_wait_s = record.admission_wait_s
            queue_depth = record.admission_queue_depth
            request_id = record.request_id
            threshold_s = search_delay_threshold_s
        timeline.append(
            {
                "operation": operation,
                "tenant": record.tenant,
                "session_id": record.session_id,
                "queued_at": timestamp,
                "scheduled_at": record.scheduled_at,
                "started_at": record.started_at,
                "completed_at": completed_at,
                "workload_offset_s": offset_s,
                "duration_s": request_duration_s,
                "queue_wait_s": queue_wait_s,
                "admission_wait_s": admission_wait_s,
                "schedule_lateness_s": record.schedule_lateness_s,
                "queue_depth": queue_depth,
                "admission_order": record.admission_order,
                "status": status,
                "status_code": status_code,
                "request_id": request_id,
                "server_received_at": record.server_received_at,
                "server_queue_entered_at": record.server_queue_entered_at,
                "server_execution_started_at": record.server_execution_started_at,
                "server_finished_at": record.server_finished_at,
                "server_queue_depth": record.server_queue_depth,
                "server_active_workers": record.server_active_workers,
                "server_terminal_status": record.server_terminal_status,
                "server_queue_wait_s": timestamp_delta(
                    record.server_queue_entered_at,
                    record.server_execution_started_at,
                ),
                "server_execution_s": timestamp_delta(
                    record.server_execution_started_at,
                    record.server_finished_at,
                ),
                "server_end_to_end_s": timestamp_delta(
                    record.server_received_at,
                    record.server_finished_at,
                ),
                "delayed": request_duration_s >= threshold_s,
            }
        )
    timeline.sort(
        key=lambda item: (
            item["workload_offset_s"] is None,
            item["workload_offset_s"] or 0,
            item["admission_order"] or 0,
        )
    )

    # Quantify the observed arrival/admission order.  With client admission
    # disabled this describes only the load generator's arrival stream, not
    # EchoMem's internal scheduler.
    def sequence_stats(values: list[str]) -> dict[str, Any]:
        if not values:
            return {
                "count": 0,
                "runs": 0,
                "switches": 0,
                "max_streak": 0,
                "first": None,
                "last": None,
            }
        runs = 1
        switches = 0
        max_streak = 1
        current_streak = 1
        for previous, current in zip(values, values[1:]):
            if current == previous:
                current_streak += 1
            else:
                runs += 1
                switches += 1
                max_streak = max(max_streak, current_streak)
                current_streak = 1
        max_streak = max(max_streak, current_streak)
        return {
            "count": len(values),
            "runs": runs,
            "switches": switches,
            "max_streak": max_streak,
            "first": values[0],
            "last": values[-1],
        }

    operation_sequence = [
        str(item["operation"])
        for item in timeline
        if item.get("operation") in {"commit", "search"}
    ]
    tenant_sequence = [
        str(item["tenant"])
        for item in timeline
        if item.get("tenant")
    ]
    delayed_by_tenant: dict[str, list[dict[str, Any]]] = {}
    for item in timeline:
        if item.get("delayed"):
            delayed_by_tenant.setdefault(str(item.get("tenant") or "-"), []).append(
                {
                    "operation": item.get("operation"),
                    "request_id": item.get("request_id"),
                    "queued_at": item.get("queued_at"),
                    "started_at": item.get("started_at"),
                    "completed_at": item.get("completed_at"),
                    "duration_s": item.get("duration_s"),
                    "queue_wait_s": item.get("queue_wait_s"),
                    "admission_wait_s": item.get("admission_wait_s"),
                    "queue_depth": item.get("queue_depth"),
                    "status": item.get("status"),
                    "status_code": item.get("status_code"),
                }
            )

    # Short time buckets expose burst/queue buildup that a single aggregate P95
    # hides.  Buckets are based on request admission time, not completion
    # time, so late completions remain attributable to their arrival period.
    bucket_width_s = max(1.0, float(time_bucket_s))
    bucket_count = max(
        1,
        math.ceil(
            max([item["workload_offset_s"] or 0 for item in timeline] or [0])
            / bucket_width_s
        )
        + 1,
    )
    time_buckets: list[dict[str, Any]] = []
    for bucket_index in range(bucket_count):
        start_s = bucket_index * bucket_width_s
        end_s = start_s + bucket_width_s
        bucket = [
            item
            for item in timeline
            if item["workload_offset_s"] is not None
            and start_s <= item["workload_offset_s"] < end_s
        ]
        commit_items = [item for item in bucket if item["operation"] == "commit"]
        search_items = [item for item in bucket if item["operation"] == "search"]
        time_buckets.append(
            {
                "bucket": bucket_index,
                "start_s": start_s,
                "end_s": end_s,
                "window_s": bucket_width_s,
                "requests": len(bucket),
                "tenants": sorted({item["tenant"] for item in bucket}),
                "commit": {
                    "submitted": len(commit_items),
                    "completed": sum(item["status"] in {"completed", "complete", "transcommit", "succeeded", "success"} for item in commit_items),
                    "delayed": sum(item["delayed"] for item in commit_items),
                    "latency": numeric_stats(
                        [item["duration_s"] for item in commit_items if item["status"] in {"completed", "complete", "transcommit", "succeeded", "success"}]
                    ),
                },
                "search": {
                    "submitted": len(search_items),
                    "succeeded": sum(item["status_code"] is not None and 200 <= item["status_code"] < 300 for item in search_items),
                    "delayed": sum(item["delayed"] for item in search_items),
                    "latency": numeric_stats(
                        [item["duration_s"] for item in search_items if item["status_code"] is not None and 200 <= item["status_code"] < 300]
                    ),
                },
            }
        )
    return {
        "workload_duration_s": duration_s,
        "commit": {
            "submitted": len(commits),
            "completed": len(commit_success),
            "failed": len(commits) - len(commit_success),
            "success_rate": len(commit_success) / len(commits) if commits else None,
            "http_status_counts": status_counts(commits),
            "rate_limited_count": sum(record.status_code == 429 for record in commits),
            "retry_after": numeric_stats(retry_after_values(commits)),
            "queue_wait": numeric_stats([record.queue_wait_s for record in commits]),
            "completion": numeric_stats([record.end_to_end_s for record in commit_success]),
            "service": numeric_stats([record.service_s for record in commit_success]),
            "server": server_observation(commits),
            "admission_wait": numeric_stats(
                [record.admission_wait_s for record in commits]
            ),
            "schedule_lateness": numeric_stats(
                [record.schedule_lateness_s for record in commits]
            ),
            "max_schedule_lateness_s": max(
                [record.schedule_lateness_s for record in commits] or [0.0]
            ),
            "max_admission_queue_depth": max(
                [record.admission_queue_depth for record in commits] or [0]
            ),
            "delayed_threshold_s": commit_delay_threshold_s,
            "delayed_count": len(commit_delayed),
            "delayed": [
                {
                    "tenant": record.tenant,
                    "session_id": record.session_id,
                    "archive_id": record.archive_id,
                    "completion_s": record.end_to_end_s,
                    "queue_wait_s": record.queue_wait_s,
                    "started_at": record.started_at,
                    "completed_at": record.completed_at,
                    "admission_wait_s": record.admission_wait_s,
                    "admission_order": record.admission_order,
                    "queue_depth": record.admission_queue_depth,
                    "request_id": record.request_id,
                    "status": record.status,
                    "status_code": record.status_code,
                    "retry_after_s": record.retry_after_s,
                    "server_received_at": record.server_received_at,
                    "server_queue_entered_at": record.server_queue_entered_at,
                    "server_execution_started_at": record.server_execution_started_at,
                    "server_finished_at": record.server_finished_at,
                    "server_queue_depth": record.server_queue_depth,
                    "server_active_workers": record.server_active_workers,
                    "server_terminal_status": record.server_terminal_status,
                    "server_queue_wait_s": timestamp_delta(
                        record.server_queue_entered_at,
                        record.server_execution_started_at,
                    ),
                    "server_execution_s": timestamp_delta(
                        record.server_execution_started_at,
                        record.server_finished_at,
                    ),
                }
                for record in commit_delayed
            ],
        },
        "search": {
            "submitted": len(searches),
            "succeeded": len(search_success),
            "errors": len(searches) - len(search_success),
            "success_rate": len(search_success) / len(searches) if searches else None,
            "http_status_counts": status_counts(searches),
            "rate_limited_count": sum(record.status_code == 429 for record in searches),
            "retry_after": numeric_stats(retry_after_values(searches)),
            "latency": numeric_stats(search_values),
            "server": server_observation(searches),
            # These rates use the configured arrival window, not the shorter
            # time spent draining futures after the workload ended.
            "throughput_rps": len(searches) / duration_s if duration_s > 0 else None,
            "completed_throughput_rps": len(search_success) / duration_s if duration_s > 0 else None,
            "submitted_over_configured_window_rps": (
                len(searches) / duration_s if duration_s > 0 else None
            ),
            "completed_over_configured_window_rps": (
                len(search_success) / duration_s if duration_s > 0 else None
            ),
            "admission_wait": numeric_stats(
                [record.admission_wait_s for record in searches]
            ),
            "schedule_lateness": numeric_stats(
                [record.schedule_lateness_s for record in searches]
            ),
            "max_schedule_lateness_s": max(
                [record.schedule_lateness_s for record in searches] or [0.0]
            ),
            "max_admission_queue_depth": max(
                [record.admission_queue_depth for record in searches] or [0]
            ),
            "delayed_threshold_s": search_delay_threshold_s,
            "delayed_count": len(search_delayed),
            "delayed": [
                {
                    "tenant": record.tenant,
                    "session_id": record.session_id,
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                    "latency_s": record.service_s,
                    "admission_wait_s": record.admission_wait_s,
                    "admission_order": record.admission_order,
                    "queue_depth": record.admission_queue_depth,
                    "request_id": record.request_id,
                    "status_code": record.status_code,
                    "retry_after_s": record.retry_after_s,
                    "server_received_at": record.server_received_at,
                    "server_queue_entered_at": record.server_queue_entered_at,
                    "server_execution_started_at": record.server_execution_started_at,
                    "server_finished_at": record.server_finished_at,
                    "server_queue_depth": record.server_queue_depth,
                    "server_active_workers": record.server_active_workers,
                    "server_terminal_status": record.server_terminal_status,
                    "server_queue_wait_s": timestamp_delta(
                        record.server_queue_entered_at,
                        record.server_execution_started_at,
                    ),
                    "server_execution_s": timestamp_delta(
                        record.server_execution_started_at,
                        record.server_finished_at,
                    ),
                    "error": record.error,
                }
                for record in search_delayed
            ],
        },
        "admission": {
            "events": admission_events,
            "max_queue_depth": max(
                [item["queue_depth"] for item in admission_events] or [0]
            ),
            "wait": numeric_stats(
                [item["wait_s"] for item in admission_events]
            ),
        },
        "timeline": timeline,
        "time_buckets": time_buckets,
        "per_tenant": per_tenant,
        "tenant_p95": {
            "commit_completion_s": tenant_commit_p95,
            "search_latency_s": tenant_search_p95,
        },
        "fairness": {
            "search_latency_p95_max_min_ratio": max_min_ratio(tenant_search_p95),
            "commit_completion_p95_max_min_ratio": max_min_ratio(tenant_commit_p95),
            "search_latency_p95_jain": fairness_index(list(tenant_search_p95.values())),
            "commit_completion_p95_jain": fairness_index(list(tenant_commit_p95.values())),
            "search_completed_per_tenant": {
                tenant: values["search"]["succeeded"]
                for tenant, values in per_tenant.items()
            },
            "commit_completed_per_tenant": {
                tenant: values["commit"]["completed"]
                for tenant, values in per_tenant.items()
            },
        },
        "scheduling": {
            "operation_sequence": sequence_stats(operation_sequence),
            "tenant_sequence": sequence_stats(tenant_sequence),
            "delayed_by_tenant": delayed_by_tenant,
            "interpretation": (
                "arrival_order_only_when_no_client_admission"
                if not any(item.get("admission_order") for item in timeline)
                else "client_admission_order"
            ),
            **scheduling_observation(timeline),
        },
    }


def build_report(summary: dict[str, Any], out_path: Path) -> None:
    def text(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value if value is not None else "-")

    def fmt_seconds(value: Any) -> str:
        try:
            return f"{float(value):.2f} s"
        except (TypeError, ValueError):
            return "-"

    status = str(summary.get("status") or "UNKNOWN").upper()
    scenarios = summary.get("scenario_status") or {}
    scenario_labels = {
        "commit_delivery": "Commit 最终必达",
        "search_priority": "Search 与 Commit 并发",
        "tenant_fairness": "多租户公平性",
        "resource_observation": "资源观测",
        "environment": "运行环境",
    }
    scenario_notes = {
        "commit_delivery": "检查已受理的 commit 是否最终完成。",
        "search_priority": "检查并发期间 search 是否成功且延迟达标。",
        "tenant_fairness": "比较不同独立租户的 P95，单租户无法判定。",
        "resource_observation": "采集 RSS、CPU、线程和文件描述符趋势。",
        "environment": "检查服务启动、网络和鉴权是否正常。",
    }
    scenario_rows = "".join(
        f"<div class='scenario'><div class='scenario-icon "
        f"{html.escape(str(value).lower())}'>{'✓' if value == PASS else '!' if value in {FAIL, ENVIRONMENT_ERROR} else '—'}</div>"
        f"<div><strong>{html.escape(scenario_labels.get(name, name))}</strong>"
        f"<p>{html.escape(scenario_notes.get(name, '该项测试的执行结果。'))}</p></div>"
        f"<b class='pill {html.escape(str(value))}'>{html.escape(str(value))}</b></div>"
        for name, value in scenarios.items()
    )
    resource_points = summary.get("resource_points", [])

    def chart(metric: str, color: str) -> str:
        points = [
            (float(item.get("elapsed_s", 0)), float(item.get(metric, 0)))
            for item in resource_points
            if item.get(metric) is not None
        ]
        if len(points) < 2:
            return f"<p class='muted'>没有足够的 {html.escape(metric)} 采样点。</p>"
        width, height, pad = 760, 190, 28
        max_x = max(point[0] for point in points) or 1
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        span = max(max_y - min_y, 1.0)
        coords = " ".join(
            f"{pad + x / max_x * (width - 2 * pad):.1f},{height - pad - (y - min_y) / span * (height - 2 * pad):.1f}"
            for x, y in points
        )
        return (
            f"<svg viewBox='0 0 {width} {height}' role='img' "
            f"aria-label='{html.escape(metric)} chart'><line x1='{pad}' "
            f"y1='{height-pad}' x2='{width-pad}' y2='{height-pad}' "
            f"stroke='#d8dee6'/><polyline points='{coords}' fill='none' "
            f"stroke='{color}' stroke-width='3' stroke-linecap='round' "
            f"stroke-linejoin='round'/></svg>"
        )
    details = summary.get("details") or {}
    metrics = summary.get("metrics") or {}
    commit_metrics = metrics.get("commit") or {}
    search_metrics = metrics.get("search") or {}
    per_tenant = metrics.get("per_tenant") or {}

    def stat_value(group: dict[str, Any], name: str) -> str:
        return fmt_seconds((group or {}).get(name))

    def rate_value(value: Any) -> str:
        try:
            return f"{float(value) * 100:.2f}%"
        except (TypeError, ValueError):
            return "-"

    tenant_rows = "".join(
        f"<tr><td><strong>{html.escape(str(tenant))}</strong></td>"
        f"<td>{html.escape(text((data.get('commit') or {}).get('submitted')))} / "
        f"{html.escape(text((data.get('commit') or {}).get('completed')))}</td>"
        f"<td>{html.escape(stat_value((data.get('commit') or {}).get('completion') or {}, 'mean_s'))}</td>"
        f"<td>{html.escape(stat_value((data.get('commit') or {}).get('completion') or {}, 'p95_s'))}</td>"
        f"<td>{html.escape(stat_value((data.get('commit') or {}).get('queue_wait') or {}, 'mean_s'))}</td>"
        f"<td>{html.escape(stat_value((data.get('search') or {}).get('latency') or {}, 'p95_s'))}</td>"
        f"<td>{html.escape(text((data.get('search') or {}).get('errors')))}</td></tr>"
        for tenant, data in sorted(per_tenant.items())
    )
    delayed_commits = commit_metrics.get("delayed") or []
    delayed_searches = search_metrics.get("delayed") or []
    delayed_rows = "".join(
        f"<tr><td>Commit</td><td>{html.escape(text(item.get('tenant')))}</td>"
        f"<td>{html.escape(text(item.get('session_id')))}</td>"
        f"<td>{html.escape(fmt_seconds(item.get('completion_s')))}</td>"
        f"<td>{html.escape(fmt_seconds(item.get('queue_wait_s')))}</td>"
        f"<td>{html.escape(text(item.get('status')))}</td></tr>"
        for item in delayed_commits
    ) + "".join(
        f"<tr><td>Search</td><td>{html.escape(text(item.get('tenant')))}</td>"
        f"<td>{html.escape(text(item.get('session_id')))}</td>"
        f"<td>{html.escape(fmt_seconds(item.get('latency_s')))}</td>"
        f"<td>-</td><td>{html.escape(text(item.get('status_code') or item.get('error')))}</td></tr>"
        for item in delayed_searches
    )
    commit_total = details.get("commit_total", 0)
    commit_failures = details.get("commit_failures", 0)
    commit_success = (
        int(commit_total) - int(commit_failures)
        if str(commit_total).isdigit() and str(commit_failures).isdigit()
        else "-"
    )
    charts = (
        f"<div class='chart-card'><div class='chart-title'>RSS 内存</div>"
        f"<div class='chart-value'>{html.escape(text(details.get('rss_slope_mb_min')))} MB/min 趋势</div>"
        f"{chart('rss_mb', '#d05a4e')}</div>"
        f"<div class='chart-card'><div class='chart-title'>CPU 使用率</div>"
        f"<div class='chart-value'>采样 {html.escape(text(details.get('samples')))} 个点</div>"
        f"{chart('cpu_percent', '#267d68')}</div>"
    )
    params = summary.get("parameters") or {}
    mode = (
        f"{text(params.get('duration_s'))} 秒 · "
        f"{text(params.get('search_rps'))} RPS · "
        f"{text(params.get('tenants'))} 租户"
    )
    icon = """<svg class="brand-icon" viewBox="0 0 48 48" aria-hidden="true">
      <path d="M10 14.5 24 7l14 7.5v19L24 41l-14-7.5z" fill="none" stroke="currentColor" stroke-width="3"/>
      <path d="M10 14.5 24 22l14-7.5M24 22v19M17 18.5v9l7 4 7-4v-9" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>
    </svg>"""
    document = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 压测报告</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#f4f6f8;color:#17202a;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1120px;margin:0 auto;padding:34px 20px 60px}} .top{{display:flex;align-items:center;gap:13px;margin-bottom:22px}}
.brand-icon{{width:42px;height:42px;color:#267d68}} h1{{font-size:27px;line-height:1.15;margin:0}} .muted{{color:#6c7884}}
.hero{{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;background:#fff;border:1px solid #dfe5ea;border-radius:10px;padding:26px 28px;margin-bottom:16px}}
.hero p{{margin:7px 0 0;color:#6c7884}} .status{{font-size:32px;font-weight:800;line-height:1.1}} .status.PASS{{color:#267d68}} .status.FAIL,.status.ENVIRONMENT_ERROR{{color:#c34d45}} .status.INCONCLUSIVE{{color:#a16b22}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}} .metric{{background:#fff;border:1px solid #dfe5ea;border-radius:9px;padding:17px 18px}}
.metric-label{{font-size:12px;color:#6c7884}} .metric-value{{font-size:22px;font-weight:800;margin-top:6px}} .metric-note{{font-size:12px;color:#6c7884;margin-top:3px}}
.section{{background:#fff;border:1px solid #dfe5ea;border-radius:10px;padding:21px 22px;margin-top:16px}} .section h2{{font-size:17px;margin:0 0 14px}}
.scenario{{display:flex;align-items:center;gap:12px;padding:13px 0;border-top:1px solid #edf0f2}} .scenario:first-of-type{{border-top:0}}
.scenario-icon{{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;font-weight:800;background:#eef1f3;color:#6c7884;flex:0 0 auto}}
.scenario-icon.pass{{background:#e5f4ee;color:#267d68}} .scenario-icon.fail,.scenario-icon.environment_error{{background:#fde9e7;color:#c34d45}} .scenario-icon.inconclusive{{background:#fff3d9;color:#a16b22}}
.scenario strong{{font-size:14px}} .scenario p{{margin:2px 0 0;color:#6c7884;font-size:12px}} .scenario>b{{margin-left:auto}}
.pill{{font-size:11px;padding:3px 8px;border-radius:999px;white-space:nowrap}} .pill.PASS{{background:#e5f4ee;color:#267d68}} .pill.FAIL,.pill.ENVIRONMENT_ERROR{{background:#fde9e7;color:#c34d45}} .pill.INCONCLUSIVE{{background:#fff3d9;color:#a16b22}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} .chart-card{{border:1px solid #edf0f2;border-radius:8px;padding:14px}} .chart-title{{font-weight:700}} .chart-value{{font-size:12px;color:#6c7884;margin:3px 0 4px}} svg:not(.brand-icon){{display:block;width:100%;height:auto}}
.facts{{display:grid;grid-template-columns:repeat(2,1fr);gap:0 26px}} .fact{{display:flex;justify-content:space-between;gap:15px;padding:9px 0;border-bottom:1px solid #edf0f2}} .fact span:first-child{{color:#6c7884}} .fact span:last-child{{font-weight:650;text-align:right;overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{padding:9px 8px;border-bottom:1px solid #edf0f2;text-align:left;vertical-align:top}} th{{color:#6c7884;font-weight:650;background:#fafbfc}}
code{{background:#f0f3f5;padding:2px 5px;border-radius:4px}} .footer{{font-size:12px;color:#6c7884;margin-top:16px}}
@media (max-width:700px){{.hero{{display:block}}.status{{margin-top:16px}}.grid{{grid-template-columns:repeat(2,1fr)}}.charts,.facts{{grid-template-columns:1fr}}}}
</style>
<body><main class="wrap">
<div class="top">{icon}<div><h1>EchoMem 真实服务压测报告</h1><div class="muted">PR397 方案 · {html.escape(summary.get("finished_at", ""))}</div></div></div>
<section class="hero"><div><div class="muted">总判定</div><div class="status {html.escape(status)}">{html.escape(status)}</div></div><div class="muted">目标：<code>{html.escape(text(summary.get("base_url")))}</code><br>{html.escape(mode)}</div></section>
<section class="grid">
<div class="metric"><div class="metric-label">Commit 成功</div><div class="metric-value">{html.escape(text(commit_success))}/{html.escape(text(commit_total))}</div><div class="metric-note">最终状态已对账</div></div>
<div class="metric"><div class="metric-label">Search 请求</div><div class="metric-value">{html.escape(text(details.get("search_total")))}</div><div class="metric-note">错误 {html.escape(text(details.get("search_errors"))) } 次</div></div>
<div class="metric"><div class="metric-label">Search P95</div><div class="metric-value">{html.escape(fmt_seconds(details.get("search_p95_s")))}</div><div class="metric-note">门槛 {html.escape(fmt_seconds(details.get("search_limit_s")))}</div></div>
<div class="metric"><div class="metric-label">RSS 斜率</div><div class="metric-value">{html.escape(text(details.get("rss_slope_mb_min")))} <small>MB/min</small></div><div class="metric-note">采样 {html.escape(text(details.get("samples")))} 个点</div></div>
</section>
<section class="section"><h2>测试项</h2>{scenario_rows or "<p class='muted'>没有场景结果。</p>"}</section>
<section class="section"><h2>详细数值</h2>
<div class="facts">
<div class="fact"><span>Commit 平均完成时间</span><span>{html.escape(stat_value(commit_metrics.get("completion") or {}, "mean_s"))}</span></div>
<div class="fact"><span>Commit P95 / P99</span><span>{html.escape(stat_value(commit_metrics.get("completion") or {}, "p95_s"))} / {html.escape(stat_value(commit_metrics.get("completion") or {}, "p99_s"))}</span></div>
<div class="fact"><span>Commit 平均排队时间</span><span>{html.escape(stat_value(commit_metrics.get("queue_wait") or {}, "mean_s"))}</span></div>
<div class="fact"><span>Commit 延迟阈值 / 超阈值</span><span>{html.escape(fmt_seconds(commit_metrics.get("delayed_threshold_s")))} / {html.escape(text(commit_metrics.get("delayed_count")))}</span></div>
<div class="fact"><span>Search 平均延迟</span><span>{html.escape(stat_value(search_metrics.get("latency") or {}, "mean_s"))}</span></div>
<div class="fact"><span>Search P95 / P99</span><span>{html.escape(stat_value(search_metrics.get("latency") or {}, "p95_s"))} / {html.escape(stat_value(search_metrics.get("latency") or {}, "p99_s"))}</span></div>
<div class="fact"><span>Search 吞吐量</span><span>{html.escape(text(search_metrics.get("throughput_rps")))} RPS</span></div>
<div class="fact"><span>Search 延迟阈值 / 超阈值</span><span>{html.escape(fmt_seconds(search_metrics.get("delayed_threshold_s")))} / {html.escape(text(search_metrics.get("delayed_count")))}</span></div>
</div></section>
<section class="section"><h2>逐租户数据</h2>
<div class="table-wrap"><table><thead><tr><th>租户</th><th>Commit 提交/完成</th><th>Commit 平均完成</th><th>Commit P95</th><th>平均排队</th><th>Search P95</th><th>Search 错误</th></tr></thead>
<tbody>{tenant_rows or "<tr><td colspan='7'>没有逐租户数据</td></tr>"}</tbody></table></div></section>
<section class="section"><h2>延迟事件</h2>
<div class="table-wrap"><table><thead><tr><th>类型</th><th>租户</th><th>Session</th><th>耗时</th><th>排队</th><th>状态</th></tr></thead>
<tbody>{delayed_rows or "<tr><td colspan='6'>没有超过阈值的请求</td></tr>"}</tbody></table></div></section>
<section class="section"><h2>资源趋势</h2><div class="charts">{charts}</div></section>
<section class="section"><h2>运行配置</h2><div class="facts">
<div class="fact"><span>服务地址</span><span>{html.escape(text(summary.get("base_url")))}</span></div>
<div class="fact"><span>运行时长</span><span>{html.escape(text(params.get("duration_s")))} 秒</span></div>
<div class="fact"><span>Search 频率</span><span>{html.escape(text(params.get("search_rps")))} RPS</span></div>
<div class="fact"><span>租户 / Session</span><span>{html.escape(text(params.get("tenants")))} / {html.escape(text(params.get("sessions_per_tenant")))}</span></div>
<div class="fact"><span>Commit 并发</span><span>{html.escape(text(params.get("commit_workers")))}</span></div>
<div class="fact"><span>调度策略</span><span>{html.escape(text(params.get("scheduler_policy")))}</span></div>
<div class="fact"><span>Commit / Search 限流</span><span>{html.escape(text(params.get("commit_delay_threshold_s")))} 秒 / {html.escape(text(params.get("search_delay_threshold_s")))} 秒</span></div>
<div class="fact"><span>是否使用 Mock</span><span>否，真实 HTTP / 真实模型</span></div>
</div></section>
<div class="footer">说明：INCONCLUSIVE 表示测试已经执行，但当前证据不足以判定该项，例如只有一个独立租户。原始 CSV 和 summary.json 保存在同一结果目录。</div>
</main></body>
</html>"""
    out_path.write_text(document, encoding="utf-8")


def build_executive_report(summary: dict[str, Any], out_path: Path) -> None:
    """Write the data-first operator report beside the legacy report."""
    report_path = out_path.parent / "report_executive.html"
    report_path.write_text(
        render_executive_report(summary, out_path.parent),
        encoding="utf-8",
    )


def build_readable_report(summary: dict[str, Any], out_path: Path) -> None:
    """Write the operator-facing report with detailed evidence sections."""
    request_rows: list[dict[str, Any]] = []
    for filename, operation in (
        ("commit_results.csv", "commit"),
        ("search_results.csv", "search"),
    ):
        csv_path = out_path.parent / filename
        if not csv_path.is_file():
            continue
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    row["operation"] = operation
                    request_rows.append(row)
        except (OSError, csv.Error):
            continue
    summary["_request_rows"] = request_rows
    out_path.write_text(render_readable_report(summary), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real EchoMem HTTP stress runner")
    parser.add_argument("--base-url", default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--auth-key", default=os.getenv("ECHOMEM_AUTH_KEY", ""))
    parser.add_argument(
        "--auth-header",
        default=os.getenv("ECHOMEM_AUTH_HEADER", "X-API-Key"),
        help="Authentication header, e.g. X-API-Key or Authorization",
    )
    parser.add_argument(
        "--tenant-config",
        default=os.getenv("STRESS_TENANT_CONFIG", ""),
        help="JSON file containing independently authenticated tenants",
    )
    parser.add_argument(
        "--allow-shared-identity",
        action="store_true",
        help="Allow labeled tenants to share one credential; isolation is not evaluated",
    )
    parser.add_argument("--tenant", action="append", help="Tenant label; repeat for multiple tenants")
    parser.add_argument("--tenants", type=int, default=1)
    parser.add_argument("--sessions-per-tenant", type=int, default=2)
    parser.add_argument("--scenario", choices=("baseline", "commit-storm", "fairness", "all"), default="all")
    parser.add_argument("--duration-s", type=float, default=60.0)
    parser.add_argument("--search-rps", type=float, default=1.0)
    parser.add_argument("--messages-per-session", type=int, default=3)
    parser.add_argument(
        "--commit-rpm",
        type=float,
        default=0.0,
        help="Fixed Commit arrivals per minute per tenant; 0 keeps one Commit per prepared session.",
    )
    parser.add_argument("--commit-timeout-s", type=float, default=600.0)
    parser.add_argument("--commit-poll-interval-s", type=float, default=2.0)
    parser.add_argument("--search-timeout-s", type=float, default=40.0)
    parser.add_argument("--search-p95-limit-s", type=float, default=2.5)
    parser.add_argument("--search-degradation-factor", type=float, default=2.0)
    parser.add_argument(
        "--isolation-retries",
        type=int,
        default=3,
        help="Same-tenant isolation search attempts before declaring a miss.",
    )
    parser.add_argument(
        "--isolation-retry-interval-s",
        type=float,
        default=2.0,
        help="Wait between same-tenant isolation search attempts.",
    )
    parser.add_argument(
        "--isolation-markers-per-tenant",
        type=int,
        default=5,
        help="Distinct committed markers written per tenant for isolation probes.",
    )
    parser.add_argument("--min-samples", type=int, default=4)
    parser.add_argument("--commit-workers", type=int, default=4)
    parser.add_argument("--search-workers", type=int, default=4)
    parser.add_argument(
        "--search-admission-capacity",
        type=int,
        default=4,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--commit-admission-capacity",
        type=int,
        default=1,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--admission-capacity",
        type=int,
        default=1,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-client-admission",
        action="store_true",
        help=(
            "Do not gate requests with the client-side scheduler. Use this to "
            "observe EchoMem's own queueing; executor worker queues are still recorded."
        ),
    )
    parser.add_argument(
        "--scheduler-policy",
        choices=("server-observe",),
        default="server-observe",
        help=(
            "Formal platform mode. Requests are sent concurrently without "
            "client-side admission scheduling so EchoMem controls queueing."
        ),
    )
    parser.add_argument(
        "--commit-delay-threshold-s",
        type=float,
        default=10.0,
        help="Report commits at or above this end-to-end latency as delayed.",
    )
    parser.add_argument(
        "--search-delay-threshold-s",
        type=float,
        default=2.5,
        help="Report searches at or above this latency as delayed.",
    )
    parser.add_argument("--sample-interval-s", type=float, default=10.0)
    parser.add_argument(
        "--server-metrics-interval-s",
        type=float,
        default=5.0,
        help="Poll EchoMem /metrics at this interval and retain raw responses.",
    )
    parser.add_argument(
        "--no-server-metrics",
        action="store_true",
        help="Disable /metrics polling; server-side queue conclusions remain unavailable.",
    )
    parser.add_argument("--pid", type=int, help="EchoMem PID for /proc resource sampling")
    parser.add_argument("--no-metrics", action="store_true", help="Mark resource observation INCONCLUSIVE")
    parser.add_argument("--out-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.scheduler_policy == "server-observe":
        args.no_client_admission = True
    out_dir = Path(args.out_dir or f"results/stress/echomem_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    clients: EchoMemHTTP | dict[str, EchoMemHTTP]
    identity_mode = "single_auth_key"
    tenant_auth_sources: dict[str, str] = {}
    if args.tenants > 1 and not args.tenant_config and not args.allow_shared_identity:
        summary = {
            "status": ENVIRONMENT_ERROR,
            "scenario_status": {
                "environment": ENVIRONMENT_ERROR,
                "tenant_isolation": INCONCLUSIVE,
            },
            "base_url": args.base_url,
            "finished_at": now_iso(),
            "parameters": vars(args),
            "details": {
                "error": (
                    "multi-tenant run requires --tenant-config with one independent "
                    "credential per tenant; use --allow-shared-identity only for "
                    "non-isolation exploratory runs"
                ),
                "identity_mode": identity_mode,
            },
        }
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        build_readable_report(summary, out_dir / "report.html")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2
    try:
        admission = None
        if not args.no_client_admission:
            admission = AdmissionController(
                args.scheduler_policy,
                capacity=args.admission_capacity,
                search_capacity=args.search_admission_capacity,
                commit_capacity=args.commit_admission_capacity,
            )
        if args.tenant_config:
            specs = load_tenant_specs(args.tenant_config)
            if len(specs) != args.tenants:
                raise ValueError(
                    f"tenant config has {len(specs)} tenants, expected {args.tenants}"
                )
            tenants = [spec.tenant_id for spec in specs]
            clients = {
                spec.tenant_id: EchoMemHTTP(
                    args.base_url,
                    spec.auth_key,
                    tenant_id=spec.tenant_id,
                    user_id=spec.user_id,
                    account_id=spec.account_id,
                    agent_id=spec.agent_id,
                    admission=admission,
                    auth_header=args.auth_header,
                )
                for spec in specs
            }
            tenant_auth_sources = {
                spec.tenant_id: spec.auth_key_source for spec in specs
            }
            identity_mode = "independent_auth_keys"
        else:
            tenants = args.tenant or [f"tenant-{i+1}" for i in range(args.tenants)]
            client = EchoMemHTTP(
                args.base_url,
                args.auth_key,
                admission=admission,
                auth_header=args.auth_header,
            )
            clients = client
    except Exception as exc:
        summary = {
            "status": ENVIRONMENT_ERROR,
            "scenario_status": {"environment": ENVIRONMENT_ERROR},
            "base_url": args.base_url,
            "finished_at": now_iso(),
            "parameters": vars(args),
            "details": {"error": f"{type(exc).__name__}: {exc}"},
        }
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        build_readable_report(summary, out_dir / "report.html")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2

    health_results = {
        tenant: client_for(clients, tenant).health()
        for tenant in tenants
    }
    unhealthy = {
        tenant: {
            "status_code": result.status_code,
            "error": result.error or result.payload,
        }
        for tenant, result in health_results.items()
        if result.status_code is None or result.status_code >= 400
    }
    if unhealthy:
        summary = {"status": ENVIRONMENT_ERROR, "scenario_status": {"environment": ENVIRONMENT_ERROR},
                   "base_url": args.base_url, "finished_at": now_iso(), "parameters": vars(args),
                   "details": {
                       "health_error": unhealthy,
                       "identity_mode": identity_mode,
                       "tenant_auth_sources": tenant_auth_sources,
                   }}
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        build_readable_report(summary, out_dir / "report.html")
        build_executive_report(summary, out_dir / "report.html")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2
    sampler = None if args.no_metrics else ResourceSampler(args.pid, args.sample_interval_s)
    server_sampler = (
        None
        if args.no_server_metrics
        else ServerMetricsSampler(
            client_for(clients, tenants[0]),
            args.server_metrics_interval_s,
        )
    )
    if sampler:
        sampler.start()
    if server_sampler:
        server_sampler.start()
    started = time.monotonic()
    commit_records: list[CommitRecord] = []
    search_records: list[SearchRecord] = []
    isolation: dict[str, Any] = {
        "status": INCONCLUSIVE,
        "reason": "single authentication identity; labels are not real tenants",
    }
    try:
        isolation = run_isolation_probe(
            clients,
            tenants,
            retries=args.isolation_retries,
            retry_interval_s=args.isolation_retry_interval_s,
            markers_per_tenant=args.isolation_markers_per_tenant,
        )
        sessions = provision_sessions(clients, tenants, args.sessions_per_tenant)
        workload_started = time.monotonic()
        if args.scenario in {"baseline", "commit-storm", "all"}:
            commit_records, search_records = run_parallel_workload(
                clients,
                sessions,
                args.messages_per_session,
                args.duration_s,
                args.search_rps,
                args.commit_timeout_s,
                args.commit_poll_interval_s,
                args.commit_workers,
                args.search_timeout_s,
                args.search_workers,
                args.commit_rpm,
            )
        elif args.scenario == "fairness":
            search_records = scenario_search(
                clients,
                sessions,
                args.duration_s,
                args.search_rps,
                args.search_timeout_s,
                workers=args.search_workers,
            )
    except Exception as exc:
        summary = {"status": ENVIRONMENT_ERROR, "scenario_status": {"environment": ENVIRONMENT_ERROR},
                   "base_url": args.base_url, "finished_at": now_iso(), "parameters": vars(args),
                   "details": {
                       "error": f"{type(exc).__name__}: {exc}",
                       "identity_mode": identity_mode,
                       "tenant_auth_sources": tenant_auth_sources,
                       "isolation": isolation,
                   }}
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        build_readable_report(summary, out_dir / "report.html")
        build_executive_report(summary, out_dir / "report.html")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if sampler:
            sampler.stop()
        if server_sampler:
            server_sampler.stop()
        return 2
    finally:
        if sampler:
            sampler.stop()
        if server_sampler:
            server_sampler.stop()
    target_search = (
        max(0, math.ceil(args.duration_s * args.search_rps))
        if args.duration_s > 0 and args.search_rps > 0
        else 0
    )
    if args.commit_rpm > 0:
        target_commit = (
            len(tenants) * max(1, math.ceil(args.commit_rpm * args.duration_s / 60.0))
            if args.duration_s > 0
            else 0
        )
    else:
        target_commit = (
            len(sessions)
            if args.scenario in {"baseline", "commit-storm", "all"}
            else 0
        )
    combined_status, combined_details = scenario_status(
        commit_records,
        search_records,
        args.min_samples,
        args.search_p95_limit_s,
        target_commit=target_commit,
        target_search=target_search,
    )
    commit_status, commit_details = commit_delivery_status(
        commit_records,
        target_commit=target_commit,
    )
    search_status, search_details = search_latency_status(
        search_records,
        min_samples=args.min_samples,
        p95_limit_s=args.search_p95_limit_s,
        target_search=target_search,
    )
    fairness_status = INCONCLUSIVE
    fairness_details: dict[str, Any] = {
        "reason": "requires at least two independently authenticated tenants"
    }
    if identity_mode == "independent_auth_keys" and len(set(tenants)) >= 2:
        tenant_p95 = {}
        for tenant in tenants:
            values = [r.elapsed_s for r in search_records if r.tenant == tenant and r.status_code and r.status_code < 400]
            tenant_p95[tenant] = percentile(values, 95) if len(values) >= args.min_samples else None
        valid = [v for v in tenant_p95.values() if v is not None and v > 0]
        if len(valid) >= 2:
            ratio = max(valid) / min(valid)
            fairness_status = PASS if ratio < 3.0 else FAIL
            fairness_details = {"tenant_p95_s": tenant_p95, "max_min_ratio": ratio, "limit": 3.0}
    resource_status = INCONCLUSIVE if args.no_metrics or not sampler or len(sampler.samples) < args.min_samples else PASS
    resource_details = {
        "samples": len(sampler.samples) if sampler else 0,
        "rss_slope_mb_min": linear_slope_per_minute(sampler.samples) if sampler else None,
        "resource_csv": "resource_samples.csv",
        "server_metrics_samples": len(server_sampler.samples) if server_sampler else 0,
        "server_metrics_available": bool(
            server_sampler and any(sample.metric_count for sample in server_sampler.samples)
        ),
        "server_queue_metrics_available": bool(
            server_sampler
            and any(
                any(
                    token in name.lower()
                    for token in ("queue", "rate_limit", "ratelimit", "retry_after")
                )
                for sample in server_sampler.samples
                for name in sample.metrics
            )
        ),
        "server_metrics_csv": "server_metrics.csv",
        "server_metrics_raw": "server_metrics.jsonl",
    }
    observed_records = commit_records + search_records
    server_observation_complete = bool(observed_records) and all(
        all(
            getattr(record, field_name, "")
            for field_name in (
                "server_received_at",
                "server_queue_entered_at",
                "server_execution_started_at",
                "server_finished_at",
            )
        )
        and getattr(record, "server_queue_depth", None) is not None
        and getattr(record, "server_active_workers", None) is not None
        for record in observed_records
    )
    statuses = {
        "commit_delivery": commit_status,
        "search_priority": search_status,
        "tenant_fairness": fairness_status,
        "tenant_isolation": isolation.get("status", INCONCLUSIVE),
        "resource_observation": resource_status,
        "server_scheduling_observation": (
            PASS if server_observation_complete else INCONCLUSIVE
        ),
    }
    overall = FAIL if FAIL in statuses.values() else (INCONCLUSIVE if INCONCLUSIVE in statuses.values() else PASS)
    finished_monotonic = time.monotonic()
    total_elapsed_s = finished_monotonic - started
    workload_elapsed_s = (
        finished_monotonic - workload_started
        if workload_started is not None
        else None
    )
    metrics = workload_metrics(
        commit_records,
        search_records,
        tenants,
        args.duration_s,
        args.commit_delay_threshold_s,
        args.search_delay_threshold_s,
    )
    metrics["wall_elapsed_s"] = workload_elapsed_s
    metrics["total_elapsed_s"] = total_elapsed_s
    metrics["arrival_window_s"] = args.duration_s
    timeline_timestamps = [
        parsed
        for item in metrics.get("timeline", [])
        for value in (item.get("queued_at"), item.get("completed_at"))
        if (parsed := parse_iso_timestamp(str(value or ""))) is not None
    ]
    metrics["completion_window_s"] = (
        (max(timeline_timestamps) - min(timeline_timestamps)).total_seconds()
        if len(timeline_timestamps) >= 2
        else None
    )
    message_events: list[dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    for record in commit_records:
        for event in record.message_events:
            message_id = str(event.get("message_id") or "")
            if message_id and message_id in seen_message_ids:
                continue
            if message_id:
                seen_message_ids.add(message_id)
            message_events.append(event)
    metrics["message_events"] = message_events
    metrics["targets"] = {
        "commit_submitted": target_commit,
        "search_submitted": target_search,
        "commit_gap": target_commit - len(commit_records),
        "search_gap": target_search - len(search_records),
        "commit_gap_rate": (
            (target_commit - len(commit_records)) / target_commit
            if target_commit
            else 0.0
        ),
        "search_gap_rate": (
            (target_search - len(search_records)) / target_search
            if target_search
            else 0.0
        ),
    }
    commit_details = {**combined_details, **commit_details}
    search_details = {**combined_details, **search_details}
    summary = {"status": overall, "scenario_status": statuses, "base_url": args.base_url, "started_at": datetime.fromtimestamp(time.time() - (time.monotonic() - started), timezone.utc).isoformat(),
               "finished_at": now_iso(), "parameters": vars(args), "details": {**commit_details, **fairness_details, **resource_details,
                                                                                "identity_mode": identity_mode,
                                                                                "tenant_auth_sources": tenant_auth_sources,
                                                                                "isolation": isolation,
                                                                                "server_observation_complete": server_observation_complete},
               "metrics": metrics,
               "resource_points": [asdict(sample) for sample in (sampler.samples if sampler else [])]}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_dir / "commit_results.csv", [asdict(record) for record in commit_records])
    write_csv(out_dir / "message_events.csv", message_events)
    (out_dir / "message_events.json").write_text(
        json.dumps(message_events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(out_dir / "search_results.csv", [asdict(record) for record in search_records])
    write_csv(out_dir / "resource_samples.csv", [asdict(record) for record in (sampler.samples if sampler else [])])
    write_csv(
        out_dir / "server_metrics.csv",
        [
            {
                "elapsed_s": sample.elapsed_s,
                "timestamp": sample.timestamp,
                "status_code": sample.status_code,
                "metric_count": sample.metric_count,
                "metrics": json.dumps(sample.metrics, ensure_ascii=False, sort_keys=True),
                "error": sample.error,
            }
            for sample in (server_sampler.samples if server_sampler else [])
        ],
    )
    (out_dir / "server_metrics.jsonl").write_text(
        "".join(
            json.dumps(sample, ensure_ascii=False) + "\n"
            for sample in (server_sampler.raw_samples if server_sampler else [])
        ),
        encoding="utf-8",
    )
    build_readable_report(summary, out_dir / "report.html")
    build_executive_report(summary, out_dir / "report.html")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if overall == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
