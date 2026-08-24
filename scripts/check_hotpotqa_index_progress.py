#!/usr/bin/env python3
"""Read-only progress checker for HotpotQA documents-mode indexing.

Watches how far EchoMem has gotten indexing the document corpus of a
hotpotqa eval run, without touching EchoMem: it only queries the existing
read-only HTTP endpoints (/fs/glob, /api/resources/index) using the run's
own identity from qa_resume_manifest.json.

Usage:
  python scripts/check_hotpotqa_index_progress.py [RUN_DIR] [--interval 5]
    RUN_DIR    result dir under benchmarks/hotpotqa/results (default: newest)
    --interval N   poll every N seconds and print rate/ETA (0 = one-shot)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backends.echomem.client import EchoMemClient

DONE_STATUSES = {"completed", "degraded", "empty"}
FAILED_STATUSES = {"failed", "error"}
_RESOURCES_PREFIX = "echo://resources/"


def _newest_run_dir(results_root: Path) -> Path:
    candidates = sorted(
        (p for p in results_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for candidate in candidates:
        if (candidate / "qa_resume_manifest.json").is_file():
            return candidate
    raise SystemExit(f"no run dir with qa_resume_manifest.json under {results_root}")


def _load_manifest(run_dir: Path) -> dict:
    manifest_path = run_dir / "qa_resume_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"no qa_resume_manifest.json in {run_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _snapshot(client: EchoMemClient, paths: list[str]) -> dict:
    def check(path: str) -> tuple[str, str, int]:
        try:
            status = client.resource_index_status(path)
            current = str(status.get("status") or "").lower()
            detail = status.get("detail") or {}
            chunks = 0
            if isinstance(detail, dict):
                try:
                    chunks = int(detail.get("chunk_count") or 0)
                except (TypeError, ValueError):
                    chunks = 0
            return path, current, chunks
        except Exception as exc:
            return path, f"error:{type(exc).__name__}", 0

    done = 0
    failed = 0
    working = 0
    chunk_count = 0
    broken: list[str] = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        for path, current, chunks in pool.map(check, paths):
            if current in DONE_STATUSES:
                done += 1
                chunk_count += chunks
            elif current in FAILED_STATUSES or current.startswith("error:"):
                failed += 1
                broken.append(path)
            else:
                working += 1
    return {
        "total": len(paths),
        "done": done,
        "working": working,
        "failed": failed,
        "chunks": chunk_count,
        "broken": broken[:5],
    }


def _print_snapshot(run_dir: Path, manifest: dict, snap: dict, rate: float = 0.0) -> None:
    identity = manifest.get("memory_identity") or {}
    pct = (snap["done"] / snap["total"] * 100) if snap["total"] else 0.0
    line = (
        f"[index] run={run_dir.name} tenant={identity.get('account') or '?'} "
        f"docs={snap['total']} done={snap['done']} ({pct:.1f}%) "
        f"working={snap['working']} failed={snap['failed']} "
        f"chunks={snap['chunks']}"
    )
    if rate > 0 and snap["working"] > 0:
        eta_s = snap["working"] / rate
        line += f"  ~{rate:.1f} docs/s  ETA {eta_s:.0f}s"
    print(line, flush=True)
    if snap["broken"]:
        print("  failed sample:", ", ".join(snap["broken"]), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", nargs="?", default="", help="run dir (default: newest)")
    parser.add_argument("--interval", type=float, default=0.0, help="watch interval seconds (0=one-shot)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010", help="EchoMem base URL")
    args = parser.parse_args()

    results_root = _PROJECT_ROOT / "benchmarks" / "hotpotqa" / "results"
    run_dir = Path(args.run_dir).expanduser() if args.run_dir else _newest_run_dir(results_root)
    manifest = _load_manifest(run_dir)
    identity = manifest.get("memory_identity") or {}

    client = EchoMemClient(
        base_url=args.base_url,
        auth_key=str(identity.get("auth_key") or ""),
        account=str(identity.get("account") or ""),
        user_id=str(identity.get("user_id") or ""),
    )

    entries = client.fs_glob("echo://resources/user/hotpotqa/**")
    if not entries:
        entries = client.fs_glob("echo://resources/user/hotpotqa/*")
    paths = []
    for entry in entries:
        uri = str(entry.get("uri") or "")
        if uri.startswith(_RESOURCES_PREFIX):
            paths.append(uri[len(_RESOURCES_PREFIX):])
    paths = sorted(set(paths))
    if not paths:
        print("[index] no resources found under echo://resources/user/hotpotqa/ for this identity", flush=True)
        raise SystemExit(2)

    first = _snapshot(client, paths)
    _print_snapshot(run_dir, manifest, first)
    if args.interval <= 0:
        return

    last_time = time.monotonic()
    last_done = first["done"]
    while True:
        time.sleep(args.interval)
        snap = _snapshot(client, paths)
        now = time.monotonic()
        rate = (snap["done"] - last_done) / max(now - last_time, 1e-6)
        _print_snapshot(run_dir, manifest, snap, rate=rate)
        last_time, last_done = now, snap["done"]


if __name__ == "__main__":
    main()
