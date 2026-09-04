#!/usr/bin/env python3
"""Run a real, bounded load sweep and recovery probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .limit_failure_probe import (
        create_sessions,
        discover_sessions,
        load_tenants,
        run_wave,
        write_report,
    )
except ImportError:
    from limit_failure_probe import (
        create_sessions,
        discover_sessions,
        load_tenants,
        run_wave,
        write_report,
    )


def workers_for_level(level: int, configured_cap: int = 0) -> int:
    """Return the concurrency for one sweep level.

    A configured ``workers`` value is intentionally a ceiling.  Treating it
    as a fixed value makes every level identical and invalidates the sweep.
    """
    level = max(1, int(level))
    cap = int(configured_cap or 0)
    return max(1, min(level, cap)) if cap > 0 else level


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
    parser.add_argument("--levels", default="4,16,64,128,256")
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument("--search-count", type=int, default=0)
    parser.add_argument("--commit-count", type=int, default=0)
    parser.add_argument("--open-count", type=int, default=0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument(
        "--kinds",
        default="search,commit,open",
        help="要执行的请求类型，逗号分隔；例如 search,open 可跳过真实 Commit 模型调用",
    )
    args = parser.parse_args()

    tenants = load_tenants(args.tenant_config)
    sessions = (
        create_sessions(args.base_url, tenants, args.timeout_s, args.auth_header)
        if args.create_sessions
        else discover_sessions(args.session_root, tenants)
    )
    levels = [int(item.strip()) for item in args.levels.split(",") if item.strip()]
    kinds = [
        item.strip().lower()
        for item in args.kinds.split(",")
        if item.strip()
    ]
    unknown_kinds = sorted(set(kinds) - {"search", "commit", "open"})
    if not kinds or unknown_kinds:
        parser.error(
            "kinds must contain one or more of search,commit,open; "
            f"unknown: {', '.join(unknown_kinds)}"
        )
    rows = []
    for workers in levels:
        # ``--workers`` is an upper bound, not a replacement for the level.
        # Otherwise a configured value such as 256 would silently run every
        # 16/64/128/256 level at the same concurrency and the sweep would not
        # measure a capacity boundary at all.
        effective_workers = workers_for_level(workers, args.workers)
        default_count = min(512, max(32, effective_workers * 2))
        counts = {
            "search": args.search_count if args.search_count > 0 else default_count,
            "commit": args.commit_count if args.commit_count > 0 else default_count,
            "open": args.open_count if args.open_count > 0 else default_count,
        }
        for kind in kinds:
            path = "/api/retrieval/search" if kind == "search" else "/api/sessions/open"
            if kind == "commit":
                path = "/commit"
            level_rows = run_wave(
                args.base_url,
                tenants,
                sessions,
                kind=kind,
                count=counts[kind],
                workers=effective_workers,
                timeout_s=args.timeout_s,
                path=path,
                auth_header=args.auth_header,
            )
            for row in level_rows:
                row["kind"] = f"{kind}-workers-{workers}"
            rows.extend(level_rows)

    # A small post-load wave demonstrates whether the service recovers.
    recovery = run_wave(
        args.base_url,
        tenants,
        sessions,
        kind="search",
        count=16,
        workers=4,
        timeout_s=args.timeout_s,
        path="/api/retrieval/search",
        auth_header=args.auth_header,
    )
    for row in recovery:
        row["kind"] = "recovery-search-workers-4"
    rows.extend(recovery)
    manifest = {
        "test_type": "real_limit_failure_sweep",
        "base_url": args.base_url,
        "tenants": [item["tenant_id"] for item in tenants],
        "workers_levels": levels,
        "worker_override": args.workers or None,
        "counts": {
            "search": args.search_count or "auto",
            "commit": args.commit_count or "auto",
            "open": args.open_count or "auto",
        },
        "kinds": kinds,
        "timeout_s": args.timeout_s,
        "client_admission": False,
        "recovery_probe": "16 Search requests at 4 workers after the sweep",
        "session_source": "target_open" if args.create_sessions else "existing_result_csv",
    }
    write_report(args.out_dir, manifest, rows)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
