"""Server-side observation through the EchoMem Prometheus /metrics endpoint.

A background sampler thread GETs ``<base_url>/metrics`` every
``interval_s`` seconds and keeps every frame in memory. Derived helpers
compute counter deltas, gauge maxima/series, and histogram percentiles
from the frames. Fetch failures are tolerated: the sampler keeps running
and reports a failure count instead of aborting.

All metric names referenced here are the stable EchoMem names:
  echomem_recall_duration_seconds              (histogram, recall latency)
  echomem_http_request_duration_seconds        (histogram)
  echomem_http_requests_inflight               (gauge)
  echomem_session_commit_duration_seconds      (histogram)
  echomem_session_commit_queue_depth           (gauge)
  echomem_process_cpu_seconds_total       (counter, labels mode=user/system)
  echomem_process_resident_memory_bytes   (gauge)
  echomem_process_threads / echomem_python_threads / echomem_process_open_handles
  echomem_recall_requests_total                (counter)
  echomem_recall_engine_calls_total            (counter)
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from performance.metrics_calc import percentile

logger = logging.getLogger("performance.monitor")

# Stable EchoMem metric names (see src/echomem/metrics/*).
RECALL_DURATION = "echomem_recall_duration_seconds"
HTTP_DURATION = "echomem_http_request_duration_seconds"
HTTP_INFLIGHT = "echomem_http_requests_inflight"
COMMIT_DURATION = "echomem_session_commit_duration_seconds"
COMMIT_QUEUE_DEPTH = "echomem_session_commit_queue_depth"
CPU_SECONDS = "echomem_process_cpu_seconds_total"
RESIDENT_MEMORY = "echomem_process_resident_memory_bytes"
PROCESS_THREADS = "echomem_process_threads"
PYTHON_THREADS = "echomem_python_threads"
OPEN_HANDLES = "echomem_process_open_handles"
RECALL_TOTAL = "echomem_recall_requests_total"
RECALL_ENGINE_CALLS = "echomem_recall_engine_calls_total"

HISTOGRAMS = frozenset({RECALL_DURATION, HTTP_DURATION, COMMIT_DURATION})
COUNTERS = frozenset({CPU_SECONDS, RECALL_TOTAL, RECALL_ENGINE_CALLS})


@dataclass
class MetricsFrame:
    """Raw timestamped samples from one /metrics GET.

    ``samples`` maps metric name -> list of (labels_dict, value). Histogram
    series keep their ``_bucket``/``_sum``/``_count`` full names so the
    distribution survives parsing.
    """

    ts: float
    samples: dict[str, list[tuple[dict[str, str], float]]] = field(default_factory=dict)


def parse_prometheus_text(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse a Prometheus text exposition into name -> [(labels, value)].

    COMMENT/TYPE/HELP lines are skipped; histogram series are kept under
    their full names (``echomem_recall_duration_seconds_bucket`` etc.) so
    later helpers can reconstruct bucket distributions. Multiple samples of
    the same name+labels collapse to the last occurrence.
    """
    samples: dict[str, list[tuple[dict[str, str], float]]] = defaultdict(list)
    seen: dict[tuple[str, str], int] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "EOF":
            break
        if "{" in line:
            name, _, rest = line.partition("{")
            labels_part, sep2, value_part = rest.partition("} ")
            if sep2 != "} ":
                continue  # malformed sample, skip
        else:
            # label-less samples: "<name> <value>"
            name, sep3, value_part = line.partition(" ")
            if not sep3:
                continue
            labels_part = ""
        meta = name
        if labels_part:
            meta = f"{name}{{{labels_part}}}"
        key = (name, meta)
        try:
            value = float(value_part)
        except ValueError:
            continue
        if key in seen:
            samples[name][seen[key]] = (_parse_labels(labels_part), value)
        else:
            seen[key] = len(samples[name])
            samples[name].append((_parse_labels(labels_part), value))
    return dict(samples)


def _parse_labels(labels_part: str) -> dict[str, str]:
    """Parse ``k="v",k2="v2"`` into a dict (empty part -> {})."""
    if not labels_part:
        return {}
    labels: dict[str, str] = {}
    inside = labels_part.strip()
    if inside.startswith("{") and inside.endswith("}"):
        inside = inside[1:-1]
    for token in inside.split(","):
        token = token.strip()
        if not token:
            continue
        key, _, raw = token.partition("=")
        labels[key.strip()] = raw.strip().strip('"')
    return labels


@dataclass
class MetricsMonitor:
    """Background /metrics sampler plus derived analytics."""

    base_url: str
    interval_s: float = 2.0
    timeout_s: float = 5.0

    frames: list[MetricsFrame] = field(default_factory=list)
    fetch_ok: int = 0
    fetch_failures: int = 0
    last_error: str = ""

    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="perf-metrics-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_s)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval_s)

    def sample(self) -> MetricsFrame | None:
        url = f"{self.base_url}/metrics"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                text = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.fetch_failures += 1
            self.last_error = str(exc)
            logger.warning("metrics fetch failed: %s", exc)
            return None
        try:
            frame = MetricsFrame(ts=time.time(), samples=parse_prometheus_text(text))
        except Exception as exc:  # keep the sampler alive on any parse issue
            self.fetch_failures += 1
            self.last_error = f"parse: {exc}"
            logger.warning("metrics parse failed: %s", exc)
            return None
        self.fetch_ok += 1
        self.frames.append(frame)
        return frame

    # -- frame helpers ------------------------------------------------------

    def _frame_at_or_before(self, ts: float) -> MetricsFrame | None:
        picked: MetricsFrame | None = None
        for frame in self.frames:
            if frame.ts <= ts:
                picked = frame
            else:
                break
        return picked

    def _frame_at_or_after(self, ts: float) -> MetricsFrame | None:
        for frame in self.frames:
            if frame.ts >= ts:
                return frame
        return None

    def _value(self, frame: MetricsFrame, name: str) -> float:
        """Sum samples of *name* in one frame across label sets."""
        total = 0.0
        for _, value in frame.samples.get(name, []):
            total += value
        return total

    # -- derived analytics ---------------------------------------------------

    def _first_frame_in(self, t0: float, t1: float) -> MetricsFrame | None:
        for frame in self.frames:
            if t0 <= frame.ts <= t1:
                return frame
        return None

    def counter_delta(self, name: str, t0: float, t1: float) -> float | None:
        """(value at t1) - (value at t0) for a counter, summed across labels.

        Baseline is the earliest frame inside the window, falling back to
        the last frame before the window; end is the last frame at or
        before ``t1``. Either missing -> None.
        """
        after = self._frame_at_or_before(t1)
        if after is None:
            return None
        before = self._first_frame_in(t0, t1) or self._frame_at_or_before(t0)
        if before is None:
            return None
        return self._value(after, name) - self._value(before, name)

    def gauge_max(self, name: str, t0: float, t1: float) -> float | None:
        values = [self._value(f, name) for f in self.frames if t0 <= f.ts <= t1]
        return max(values) if values else None

    def gauge_series(self, name: str, t0: float, t1: float) -> list[tuple[float, float]]:
        return [
            (f.ts, self._value(f, name))
            for f in self.frames
            if t0 <= f.ts <= t1
        ]

    def cpu_utilization_series(self, t0: float, t1: float) -> list[tuple[float, float]]:
        """Per-frame CPU utilization (percent of one core) via frame deltas.

        Differences between consecutive frames divided by their wall-clock
        span; the first frame in the window has no delta and is skipped.
        """
        series: list[tuple[float, float]] = []
        prev_value: float | None = None
        prev_ts: float | None = None
        for frame in self.frames:
            if frame.ts < t0 or frame.ts > t1:
                continue
            value = self._value(frame, CPU_SECONDS)
            if prev_value is not None and prev_ts is not None and frame.ts > prev_ts:
                span = frame.ts - prev_ts
                if span > 0:
                    delta = value - prev_value
                    percent = max(0.0, delta) / span * 100.0
                    series.append((frame.ts, round(percent, 2)))
            prev_value, prev_ts = value, frame.ts
        return series

    def histogram_percentiles(
        self,
        name: str,
        t0: float,
        t1: float,
    ) -> dict[str, float | None]:
        """Estimate p50/p95/p99 (seconds) from the last frame in [t0, t1].

        Uses the cumulative bucket counts of the last frame (Prometheus
        histograms are cumulative), with the previous bucket's upper bound
        as the lower interpolation anchor.
        """
        frame = None
        for candidate in self.frames:
            if t0 <= candidate.ts <= t1:
                frame = candidate
        if frame is None:
            return {"p50": None, "p95": None, "p99": None}
        buckets = self._bucket_distribution(frame, name)
        if not buckets or buckets[-1][1] <= 0:
            return {"p50": None, "p95": None, "p99": None}
        total = buckets[-1][1]
        bounds = [b for b, _ in buckets]
        counts = [c for _, c in buckets]
        result: dict[str, float | None] = {}
        for label, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
            result[label] = self._bucket_percentile(bounds, counts, total, q)
        return result

    @staticmethod
    def _bucket_distribution(
        frame: MetricsFrame,
        name: str,
    ) -> list[tuple[float, float]]:
        """Cumulative bucket (upper bound, count) pairs for a histogram.

        Buckets of the same upper bound across all label sets are merged
        (summed), which yields the global latency distribution of the
        metric regardless of its outcome/status labels.
        """
        prefix = f"{name}_bucket"
        entries = frame.samples.get(prefix, [])
        if not entries:
            return []
        merged: dict[float, float] = {}
        for labels, value in entries:
            le = labels.get("le", "")
            if le == "+Inf" or le == "":
                continue
            try:
                bound = float(le)
            except ValueError:
                continue
            merged[bound] = merged.get(bound, 0.0) + value
        ordered = sorted(merged.items())
        return [(bound, count) for bound, count in ordered]

    @staticmethod
    def _bucket_percentile(
        bounds: list[float],
        counts: list[float],
        total: float,
        q: float,
    ) -> float | None:
        if total <= 0:
            return None
        target = q * total
        lower_bound = 0.0
        lower_count = 0.0
        for bound, count in zip(bounds, counts):
            if count >= target:
                if count == lower_count:
                    return bound
                frac = (target - lower_count) / (count - lower_count)
                return lower_bound + (bound - lower_bound) * frac
            lower_bound = bound
            lower_count = count
        return bounds[-1]

    def cpu_utilization(self, t0: float, t1: float) -> float | None:
        """CPU seconds delta divided by wall time (fraction of one core)."""
        delta = self.counter_delta(CPU_SECONDS, t0, t1)
        wall = t1 - t0
        if delta is None or wall <= 0:
            return None
        return round(delta / wall, 4)

    def recall_engine_calls_delta(self, t0: float, t1: float) -> float | None:
        return self.counter_delta(RECALL_ENGINE_CALLS, t0, t1)


def scene_resource_summary(logger: Any, monitor: MetricsMonitor, t0: float, t1: float) -> dict:
    """Resource snapshot for one scene window, tolerating missing metrics."""
    del logger  # reserved for future diagnostics of missing series
    cpu = monitor.cpu_utilization(t0, t1)
    rss_series = monitor.gauge_series(RESIDENT_MEMORY, t0, t1)
    return {
        "cpu_util_mean_fraction": cpu,
        "cpu_util_percent": round(cpu * 100, 2) if cpu is not None else None,
        "rss_max_bytes": (
            max(value for _, value in rss_series) if rss_series else None
        ),
        "threads_max": monitor.gauge_max(PROCESS_THREADS, t0, t1),
        "python_threads_max": monitor.gauge_max(PYTHON_THREADS, t0, t1),
        "handles_max": monitor.gauge_max(OPEN_HANDLES, t0, t1),
        "http_inflight_max": monitor.gauge_max(HTTP_INFLIGHT, t0, t1),
        "commit_queue_depth_max": monitor.gauge_max(COMMIT_QUEUE_DEPTH, t0, t1),
        "recall_duration": monitor.histogram_percentiles(RECALL_DURATION, t0, t1),
        "http_duration": monitor.histogram_percentiles(HTTP_DURATION, t0, t1),
        "commit_duration": monitor.histogram_percentiles(COMMIT_DURATION, t0, t1),
    }