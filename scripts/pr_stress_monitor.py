#!/usr/bin/env python3
"""Collect low-overhead, secret-free host and queue telemetry for PR stress runs."""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


JOBS_PATH = Path(os.getenv("JOBS_PATH", "/opt/memory-eval-web/data/jobs.json"))
OUTPUT_PATH = Path(
    os.getenv(
        "OUTPUT_PATH",
        "/opt/memory-eval-harness/results/pr-stress-monitor.jsonl",
    )
)
CONTAINERS_PATH = Path(
    os.getenv(
        "CONTAINERS_PATH",
        "/opt/memory-eval-harness/results/pr-stress-containers.jsonl",
    )
)
INTERVAL_S = max(5.0, float(os.getenv("INTERVAL_S", "60")))


def parse_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        if normalized.endswith("+00:00"):
            return datetime.strptime(
                normalized[:-6], "%Y-%m-%dT%H:%M:%S.%f"
            ).replace(tzinfo=timezone.utc)
        return datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        try:
            return datetime.strptime(
                normalized, "%Y-%m-%dT%H:%M:%S%z"
            )
        except ValueError:
            return None


def read_jobs():
    try:
        payload = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    return jobs if isinstance(jobs, list) else []


def memory_snapshot():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return {}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": max(0, total - available),
        "swap_used_bytes": max(
            0, values.get("SwapTotal", 0) - values.get("SwapFree", 0)
        ),
    }


def container_snapshot():
    try:
        raw = subprocess.check_output(
            [
                "docker",
                "ps",
                "--format",
                "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.ID}}",
            ],
            universal_newlines=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in raw.splitlines():
        name, status, image, container_id = (
            line.split("\t", 3) + ["", "", "", ""]
        )[:4]
        if "memory-eval" in name or "echomem" in name or "stress" in name:
            row = {
                "name": name,
                "status": status,
                "image": image,
                "container_id": container_id,
            }
            try:
                inspect = subprocess.check_output(
                    [
                        "docker",
                        "inspect",
                        container_id,
                        "--format",
                        "{{.RestartCount}}",
                    ],
                    universal_newlines=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                ).strip()
                row["restart_count"] = int(inspect or 0)
            except (OSError, ValueError, subprocess.SubprocessError):
                row["restart_count"] = None
            try:
                stats = subprocess.check_output(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.PIDs}}",
                        container_id,
                    ],
                    universal_newlines=True,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                ).strip()
                cpu, memory, memory_percent, pids = (
                    stats.split("\t", 3) + ["", "", "", ""]
                )[:4]
                row.update(
                    {
                        "cpu_percent": cpu,
                        "memory_usage": memory,
                        "memory_percent": memory_percent,
                        "pids": int(pids) if pids.isdigit() else None,
                    }
                )
            except (OSError, ValueError, subprocess.SubprocessError):
                row.update(
                    {
                        "cpu_percent": None,
                        "memory_usage": None,
                        "memory_percent": None,
                        "pids": None,
                    }
                )
            rows.append(row)
    return rows


def sample():
    sampled_at = datetime.now(timezone.utc)
    disk = shutil.disk_usage("/")
    jobs = []
    for job in read_jobs()[-20:]:
        progress = job.get("progress") or {}
        updated_at = progress.get("updated_at") or job.get("started_at")
        progress_age_s = None
        if updated_at:
            parsed = parse_timestamp(updated_at)
            if parsed is not None:
                progress_age_s = max(0.0, (sampled_at - parsed).total_seconds())
        jobs.append(
            {
                key: job.get(key)
                for key in (
                    "id",
                    "pr_number",
                    "status",
                    "progress",
                    "started_at",
                    "finished_at",
                )
            }
        )
        jobs[-1].update(
            {
                "progress_updated_at": updated_at,
                "progress_age_s": progress_age_s,
                "stalled": (
                    job.get("status") == "running"
                    and progress_age_s is not None
                    and progress_age_s >= 900
                ),
            }
        )
    return {
        "timestamp": sampled_at.isoformat(),
        "memory": memory_snapshot(),
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_ratio": disk.used / disk.total if disk.total else None,
        },
        "jobs": jobs,
    }


def append_jsonl(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def main():
    while True:
        append_jsonl(OUTPUT_PATH, sample())
        append_jsonl(
            CONTAINERS_PATH,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "containers": container_snapshot(),
            },
        )
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
