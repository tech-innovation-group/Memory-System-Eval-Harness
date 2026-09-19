#!/usr/bin/env python3
"""Reproduce EchoMem ``memory_profile`` timing evidence from saved artifacts.

This is intentionally offline. It never sends requests to EchoMem or a model.
Use a JSONL server log for per-request stage samples, or a pair of Prometheus
snapshots for a bounded Histogram window. A single cumulative Prometheus
snapshot is reported as cumulative evidence and is never labelled as a
16/64-concurrency comparison.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


STAGE = "memory_profile"
HISTOGRAM = "echomem_memrouter_stage_duration_seconds"
SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"(?:\s+\d+)?$"
)
LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:\\.|[^"\\])*)"')


def number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def percentile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(len(ordered) * p / 100.0))
    return ordered[rank - 1]


def summary(values: list[float], *, unit: str) -> dict[str, Any]:
    if not values:
        return {"observations": 0, "unit": unit, "mean": None, "p50": None,
                "p95": None, "p99": None, "max": None}
    return {
        "observations": len(values),
        "unit": unit,
        "mean": round(sum(values) / len(values), 6),
        "p50": round(percentile(values, 50) or 0, 6),
        "p95": round(percentile(values, 95) or 0, 6),
        "p99": round(percentile(values, 99) or 0, 6),
        "max": round(max(values), 6),
    }


def scenario_name(event: dict[str, Any]) -> str:
    for key in ("scenario", "topology", "run_id", "case"):
        value = event.get(key)
        if value not in (None, ""):
            concurrency = event.get("concurrency") or event.get("slots")
            return f"{value}/C={concurrency}" if concurrency else str(value)
    return "unlabelled"


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if not isinstance(value, dict):
                continue
            if value.get("event") != "recall_stage_completed":
                continue
            if value.get("stage") != STAGE:
                continue
            duration = number(value.get("duration_ms"))
            if duration is None:
                continue
            rows.append({
                "duration_ms": duration,
                "queue_wait_ms": number(value.get("queue_wait_ms")),
                "status": str(value.get("status") or ""),
                "scenario": scenario_name(value),
                "trace_id_present": bool(value.get("trace_id")),
                "line": line_number,
            })
    return rows, invalid


def summarize_log(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"source": "structured_log", "path": str(path), "status": "MISSING",
                "reason": "file_not_found", "observations": 0}
    rows, invalid = read_jsonl(path)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["scenario"]].append(row)
    by_scenario = {}
    for name, group in sorted(grouped.items()):
        durations = [row["duration_ms"] for row in group]
        waits = [row["queue_wait_ms"] for row in group if row["queue_wait_ms"] is not None]
        by_scenario[name] = {
            "timing_ms": summary(durations, unit="ms"),
            "queue_wait_ms": summary(waits, unit="ms"),
            "statuses": _counts(row["status"] or "unknown" for row in group),
            "trace_ids_present": sum(1 for row in group if row["trace_id_present"]),
        }
    comparisons = _scenario_comparisons(by_scenario)
    return {
        "source": "structured_log",
        "path": str(path),
        "status": "MEASURED" if rows else "NO_SAMPLE",
        "stage": STAGE,
        "observations": len(rows),
        "invalid_json_lines": invalid,
        "by_scenario": by_scenario,
        "comparisons": comparisons,
        "comparison_ready": bool(comparisons),
        "comparison_note": (
            "same-topology JSONL samples include both C=16 and C=64"
            if comparisons
            else "JSONL samples are missing explicit C=16/C=64 scenario labels"
        ),
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for value in values:
        result[value] += 1
    return dict(sorted(result.items()))


def _scenario_comparisons(groups: dict[str, Any]) -> list[dict[str, Any]]:
    """Calculate 64/16 ratios only for the same explicitly named topology."""
    by_base: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for name, data in groups.items():
        match = re.match(r"^(?P<base>.*)/C=(?P<concurrency>16|64)$", name)
        if not match:
            continue
        by_base[match.group("base")][int(match.group("concurrency"))] = data

    comparisons: list[dict[str, Any]] = []
    for base, levels in sorted(by_base.items()):
        if 16 not in levels or 64 not in levels:
            continue
        low = levels[16]["timing_ms"]
        high = levels[64]["timing_ms"]
        ratios = {}
        for field in ("mean", "p50", "p95", "p99", "max"):
            denominator = low.get(field)
            numerator = high.get(field)
            ratios[field] = (
                round(numerator / denominator, 4)
                if denominator not in (None, 0) and numerator is not None
                else None
            )
        comparisons.append({
            "topology": base or "unlabelled",
            "low_concurrency": 16,
            "high_concurrency": 64,
            "timing_ratio_64_div_16": ratios,
        })
    return comparisons


def parse_prometheus(path: Path) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = SAMPLE_RE.match(line)
            if not match:
                continue
            value = number(match.group("value"))
            if value is None:
                continue
            labels = {key: _unescape_label(raw) for key, raw in LABEL_RE.findall(match.group("labels") or "")}
            key = (match.group("name"), tuple(sorted(labels.items())))
            samples[key] = value
    return samples


def _metric_series(
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    suffix: str,
) -> dict[tuple[tuple[str, str], ...], float]:
    return {labels: value for (name, labels), value in samples.items() if name == suffix}


def histogram_delta(before: Path, after: Path) -> dict[str, Any]:
    if not before.is_file() or not after.is_file():
        missing = [str(path) for path in (before, after) if not path.is_file()]
        return {"source": "prometheus_histogram_delta", "status": "MISSING",
                "reason": "snapshot_not_found", "missing": missing}
    old = parse_prometheus(before)
    new = parse_prometheus(after)
    buckets: dict[float, float] = {}
    for labels, after_value in _metric_series(new, f"{HISTOGRAM}_bucket").items():
        label_map = dict(labels)
        if label_map.get("stage") != STAGE:
            continue
        before_value = old.get((f"{HISTOGRAM}_bucket", labels), 0.0)
        delta = after_value - before_value
        if delta < 0:
            continue
        upper = label_map.get("le")
        if upper in (None, "+Inf"):
            continue
        try:
            buckets[float(upper)] = buckets.get(float(upper), 0.0) + delta
        except ValueError:
            continue
    count_series = _metric_series(new, f"{HISTOGRAM}_count")
    sum_series = _metric_series(new, f"{HISTOGRAM}_sum")
    count_delta = 0.0
    sum_delta = 0.0
    for labels, value in count_series.items():
        if dict(labels).get("stage") == STAGE:
            count_delta += max(0.0, value - old.get((f"{HISTOGRAM}_count", labels), 0.0))
    for labels, value in sum_series.items():
        if dict(labels).get("stage") == STAGE:
            sum_delta += max(0.0, value - old.get((f"{HISTOGRAM}_sum", labels), 0.0))
    # The +Inf bucket is represented by _count. Use it as the quantile
    # denominator even when the finite buckets do not contain every sample.
    total = count_delta
    if count_delta and not buckets:
        bucket_note = "no finite buckets; count used only for observations"
    else:
        bucket_note = "finite cumulative bucket deltas"
    percentiles = {
        label: _histogram_quantile(buckets, total, quantile)
        for label, quantile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
    }
    return {
        "source": "prometheus_histogram_delta",
        "status": "MEASURED" if count_delta > 0 else "NO_SAMPLE",
        "metric": HISTOGRAM,
        "stage": STAGE,
        "observations": int(count_delta),
        "mean_seconds": round(sum_delta / count_delta, 6) if count_delta else None,
        "percentiles_seconds": percentiles,
        "bucket_note": bucket_note,
        "comparison_ready": count_delta > 0,
        "before": str(before),
        "after": str(after),
    }


def _unescape_label(value: str) -> str:
    """Decode Prometheus label escapes without corrupting UTF-8 text."""
    return value.replace(r'\\"', '"').replace(r"\\\\", "\\").replace(r"\\n", "\n")


def _histogram_quantile(buckets: dict[float, float], total: float, q: float) -> float | None:
    if not buckets or total <= 0:
        return None
    target = total * q
    lower_bound = 0.0
    lower_count = 0.0
    for bound in sorted(buckets):
        count = buckets[bound]
        if count >= target:
            if count == lower_count:
                return bound
            fraction = (target - lower_count) / (count - lower_count)
            return lower_bound + (bound - lower_bound) * fraction
        lower_bound, lower_count = bound, count
    return max(buckets)


def cumulative_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"source": "prometheus_snapshot", "status": "MISSING", "path": str(path)}
    samples = parse_prometheus(path)
    count = sum(
        value for (name, labels), value in samples.items()
        if name == f"{HISTOGRAM}_count" and dict(labels).get("stage") == STAGE
    )
    total = sum(
        value for (name, labels), value in samples.items()
        if name == f"{HISTOGRAM}_sum" and dict(labels).get("stage") == STAGE
    )
    return {
        "source": "prometheus_snapshot",
        "status": "CUMULATIVE_ONLY" if count else "NO_SAMPLE",
        "path": str(path),
        "metric": HISTOGRAM,
        "stage": STAGE,
        "observations": int(count),
        "mean_seconds": round(total / count, 6) if count else None,
        "comparison_ready": False,
        "reason": "single snapshot has no 16/64 scenario boundary; provide --before and --after",
    }


def reference_rows(path: Path) -> list[dict[str, Any]]:
    """Extract the old report's displayed memory_profile rows for comparison only."""
    if not path.is_file():
        return []
    text = html.unescape(path.read_text(encoding="utf-8"))
    pattern = re.compile(
        r"<td><b>记忆画像 memory_profile</b></td><td>([\d,.]+) ms</td>"
        r"<td[^>]*>([\d,.]+) ms</td><td[^>]*>([\d.]+) 倍</td>"
    )
    return [
        {"displayed_16_ms": float(a.replace(",", "")),
         "displayed_64_ms": float(b.replace(",", "")),
         "displayed_ratio": float(c)}
        for a, b, c in pattern.findall(text)
    ]


def build_result(args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {"stage": STAGE, "sources": [], "reference": []}
    if args.log:
        result["sources"].append(summarize_log(args.log))
    if args.before and args.after:
        result["sources"].append(histogram_delta(args.before, args.after))
    elif args.metrics:
        result["sources"].append(cumulative_snapshot(args.metrics))
    if args.reference:
        result["reference"] = reference_rows(args.reference)
    result["reproducible_16_64"] = any(
        source.get("comparison_ready") is True for source in result["sources"]
    )
    result["conclusion"] = (
        "可以按场景复测：输入包含显式 C=16/C=64 JSONL 样本或前后 Prometheus 快照。"
        if result["reproducible_16_64"]
        else "当前证据不能复现旧报告的 16/64 memory_profile 倍率；需要带场景边界的 JSONL 或 Prometheus 前后快照。"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="EchoMem JSONL log")
    parser.add_argument("--metrics", type=Path, help="one cumulative Prometheus snapshot")
    parser.add_argument("--before", type=Path, help="Prometheus snapshot at scenario start")
    parser.add_argument("--after", type=Path, help="Prometheus snapshot at scenario end")
    parser.add_argument("--reference", type=Path, help="old HTML report, for display-only comparison")
    parser.add_argument("--out", type=Path, help="write JSON result to this path")
    args = parser.parse_args()
    if not any((args.log, args.metrics, args.before and args.after)):
        parser.error("provide --log, --metrics, or both --before and --after")
    result = build_result(args)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
