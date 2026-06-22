#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from echomemory_common import (
    DEFAULT_ECHOMEM_ROOT,
    ensure_echomem_imports,
    write_echomem_config,
    write_json,
)


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


async def consolidate(args: argparse.Namespace) -> dict[str, Any]:
    root = ensure_echomem_imports(args.echomem_root)
    from echomem.runtime.bootstrap import open_runtime
    from echomem.utils.domain.context import RequestContext
    from echomem.index_engine.graph.episode_sync import GraphEpisodeSync

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    workspace = str(Path(args.workspace).expanduser().resolve())
    account = str(args.account or "default")
    user_id = str(args.user_id or "default")
    agent_id = str(args.agent_id or "default")

    old_ingest_mode = os.environ.get("ECHOMEM_INGEST_MODE")
    os.environ["ECHOMEM_INGEST_MODE"] = str(args.ingest_mode or "full")
    try:
        config_path = (
            Path(args.echomem_config).expanduser().resolve()
            if args.echomem_config
            else write_echomem_config(
                out_dir,
                account,
                workspace,
                root,
                fallback_to_mock=bool(args.fallback_to_mock),
            )
        )
    finally:
        if old_ingest_mode is None:
            os.environ.pop("ECHOMEM_INGEST_MODE", None)
        else:
            os.environ["ECHOMEM_INGEST_MODE"] = old_ingest_mode

    started = time.time()
    runtime = await open_runtime(str(config_path))
    ctx = RequestContext(
        account_id=account,
        user_id=user_id,
        agent_id=agent_id,
        session_id="deferred-consolidation",
    )

    services = runtime.services
    atom_storage = getattr(services, "atom_storage", None)
    graph_sync = getattr(services, "graph_sync", None)
    organized_projector = getattr(services, "organized_projector", None)
    episode_service = getattr(services, "episode_service", None)
    graph_memory = getattr(services, "graph_memory", None)

    active_atoms = await atom_storage.list_active_atoms(ctx) if atom_storage is not None else []
    graph_synced = 0
    graph_errors: list[str] = []
    organized_report: dict[str, Any] = {}
    episode_report: dict[str, Any] = {}

    try:
        if args.run_graph and graph_sync is not None:
            for atom in active_atoms:
                try:
                    await graph_sync.sync_atom(atom, ctx)
                    graph_synced += 1
                except Exception as exc:
                    graph_errors.append(f"{atom.atom_id}: {type(exc).__name__}: {exc}")
                    if len(graph_errors) >= 20:
                        break

        if args.run_organized and organized_projector is not None and active_atoms:
            report = await organized_projector.project(active_atoms, ctx)
            organized_report = {
                "profile_updated": bool(getattr(report, "profile_updated", False)),
                "overview_updated": bool(getattr(report, "overview_updated", False)),
                "entities_added": int(getattr(report, "entities_added", 0) or 0),
                "events_added": int(getattr(report, "events_added", 0) or 0),
            }

        if args.run_episode and episode_service is not None:
            ep_result = await episode_service.project_from_organized(ctx)
            episode_report = {
                "episode_id": str(getattr(ep_result, "episode_id", "") or ""),
                "action": str(getattr(ep_result, "action", "") or ""),
                "decision_score": float(getattr(ep_result, "decision_score", 0.0) or 0.0),
                "signals": list(getattr(ep_result, "signals", ()) or ()),
            }
            if episode_report.get("episode_id") and graph_memory is not None:
                try:
                    ep = await episode_service.get_episode(episode_report["episode_id"], ctx)
                except Exception:
                    ep = None
                if ep is not None:
                    try:
                        graph_episode_sync = GraphEpisodeSync(
                            graph_svc=graph_memory,
                            episode_storage=getattr(episode_service, "_storage"),
                        )
                        await graph_episode_sync.sync_episode_to_graph(ep, ctx)
                        episode_report["graph_sync"] = True
                    except Exception as exc:
                        episode_report["graph_sync"] = False
                        episode_report["graph_sync_error"] = f"{type(exc).__name__}: {exc}"

        account_root = Path(workspace).expanduser().resolve() / account / account
        summary = {
            "backend": "echomemory",
            "workspace": workspace,
            "account": account,
            "user_id": user_id,
            "agent_id": agent_id,
            "echomem_root": str(root),
            "echomem_config": str(config_path),
            "ingest_mode_for_runtime": str(args.ingest_mode or "full"),
            "elapsed_s": round(time.time() - started, 3),
            "active_atom_count": len(active_atoms),
            "graph_phase_enabled": bool(args.run_graph),
            "organized_phase_enabled": bool(args.run_organized),
            "episode_phase_enabled": bool(args.run_episode),
            "graph_synced_atoms": graph_synced,
            "graph_errors": graph_errors,
            "organized_report": organized_report,
            "episode_report": episode_report,
            "artifact_counts": {
                "atoms": count_files(account_root / "memory" / ".structured" / "atoms"),
                "relations": count_files(account_root / "memory" / ".structured" / "relations"),
                "graph": count_files(account_root / "memory" / ".graph"),
                "overview": count_files(account_root / "memory" / "overview"),
                "profile": count_files(account_root / "memory" / "profile"),
                "entities": count_files(account_root / "memory" / "entities"),
                "events": count_files(account_root / "memory" / "events"),
                "episodes": count_files(account_root / "memory" / ".episodes"),
            },
        }
        write_json(out_dir / "echomemory_deferred_consolidation_summary.json", summary)
        return summary
    finally:
        try:
            await runtime.stop()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deferred graph/organized/episode consolidation for EchoMemory imports."
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--ingest-mode", choices=["full", "fast", "minimal"], default="full")
    parser.add_argument("--fallback-to-mock", action="store_true")
    parser.add_argument("--run-graph", action="store_true", default=True)
    parser.add_argument("--run-organized", action="store_true", default=True)
    parser.add_argument("--run-episode", action="store_true", default=True)
    parser.add_argument("--skip-graph", action="store_true")
    parser.add_argument("--skip-organized", action="store_true")
    parser.add_argument("--skip-episode", action="store_true")
    args = parser.parse_args()
    if args.skip_graph:
        args.run_graph = False
    if args.skip_organized:
        args.run_organized = False
    if args.skip_episode:
        args.run_episode = False
    return args


def main() -> None:
    args = parse_args()
    summary = asyncio.run(consolidate(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
