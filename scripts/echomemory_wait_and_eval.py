#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.plugins.echomemory.inspector import (
    count_files,
    current_session_snapshot,
    first_existing_root,
    preferred_memory_root,
)


SCRIPTS = ROOT / "scripts"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def run_and_log(cmd: list[str], log_path: Path, env: dict[str, str]) -> int:
    ensure_parent(log_path)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("$ " + " ".join(cmd) + "\n")
        handle.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )
        return proc.wait()


def expected_session_count(summary: dict[str, Any]) -> int:
    best = 0
    fallback = 0
    for record in summary.get("records") or []:
        # Prefer the actual imported/selected session count for the current run.
        for key in ("session_count", "session_limit", "progress_sessions_total"):
            try:
                best = max(best, int(record.get(key) or 0))
            except Exception:
                continue
        best = max(best, len(record.get("session_records") or []))
        try:
            fallback = max(fallback, int(record.get("original_session_count") or 0))
        except Exception:
            pass
    return best or fallback


def build_workspace_snapshot(workspace: Path, account: str, sample: str) -> dict[str, Any]:
    rows, totals = current_session_snapshot(workspace, account, sample)
    account_root = first_existing_root(workspace, account)
    session_count = len(rows)
    abstract_count = 0
    overview_count = 0
    for row in rows:
        session_id = str(row.get("session_id") or "").strip()
        if not session_id:
            continue
        projection_root = account_root / "engines" / "echo0_plugin" / "sessions" / session_id
        legacy_root = Path(str(row.get("session_path") or ""))
        abstract_path = projection_root / "abstract.md"
        overview_path = projection_root / "overview.md"
        if not abstract_path.exists():
            abstract_path = legacy_root / "abstract.md"
        if not overview_path.exists():
            overview_path = legacy_root / "overview.md"
        if abstract_path.exists():
            abstract_count += 1
        if overview_path.exists():
            overview_count += 1
    memory_root = preferred_memory_root(account_root)
    vector_roots = [
        account_root / "engines" / "echo0_plugin" / "vector_store",
        account_root.parent / "local" / "engines" / "echo0_plugin" / "vector_store" / account,
        account_root.parent / "system" / "vector_index",
    ]
    atom_count = count_atom_artifacts(memory_root)
    graph_count = count_files(memory_root / ".graph")
    vector_count = 0
    for vector_root in vector_roots:
        vector_count = max(vector_count, count_files(vector_root))
    complete_sessions = sum(1 for row in rows if row.get("ok"))
    snapshot = {
        "workspace": str(workspace),
        "account": account,
        "sample": sample,
        "session_count": session_count,
        "submitted_messages": int(totals.get("submitted") or 0),
        "complete_sessions": complete_sessions,
        "abstract_count": abstract_count,
        "overview_count": overview_count,
        "atom_count": atom_count,
        "graph_count": graph_count,
        "vector_count": vector_count,
    }
    snapshot["signature"] = "|".join(
        str(snapshot[key])
        for key in (
            "session_count",
            "submitted_messages",
            "complete_sessions",
            "abstract_count",
            "overview_count",
            "atom_count",
            "graph_count",
            "vector_count",
        )
    )
    return snapshot


def count_atom_artifacts(memory_root: Path) -> int:
    atom_dir = memory_root / ".structured" / "atoms"
    atom_bundle = memory_root / ".structured" / "atoms.json"
    count = count_files(atom_dir)
    if count > 0 or not atom_bundle.exists():
        return count
    try:
        payload = json.loads(atom_bundle.read_text(encoding="utf-8"))
    except Exception:
        return count
    if isinstance(payload, dict):
        atoms = payload.get("atoms")
        if isinstance(atoms, dict):
            return len(atoms)
        if isinstance(atoms, list):
            return len(atoms)
    if isinstance(payload, list):
        return len(payload)
    return count


def snapshot_ready(snapshot: dict[str, Any], expected_sessions_total: int) -> bool:
    session_count = int(snapshot.get("session_count") or 0)
    complete_sessions = int(snapshot.get("complete_sessions") or 0)
    abstract_count = int(snapshot.get("abstract_count") or 0)
    overview_count = int(snapshot.get("overview_count") or 0)
    atom_count = int(snapshot.get("atom_count") or 0)
    graph_count = int(snapshot.get("graph_count") or 0)
    vector_count = int(snapshot.get("vector_count") or 0)
    enough_sessions = session_count >= max(1, expected_sessions_total or session_count)
    summaries_ready = bool(
        enough_sessions
        and session_count > 0
        and abstract_count == session_count
        and overview_count == session_count
        and vector_count > 0
    )
    # EchoMemory's newer engine layout can be retrieval-ready before every
    # per-session abstract/overview file lands, as long as the vector store and
    # structured graph/atom artifacts are already materialized for this account.
    search_ready = bool(
        enough_sessions
        and session_count > 0
        and vector_count > 0
        and (atom_count > 0 or graph_count > 0 or complete_sessions > 0)
    )
    # Some benchmark flows, especially document-style HotpotQA imports, are
    # already retrieval-ready once summaries and vector artifacts exist. In
    # those runs, session rows may never flip to "ok" even though retrieval can
    # already see the imported documents, so do not hard-gate on complete_sessions.
    doc_ready = summaries_ready
    structured_ready = summaries_ready and atom_count > 0 and graph_count > 0
    return bool(doc_ready or structured_ready or search_ready)


def wait_for_async_memory_stability(
    *,
    workspace: Path,
    account: str,
    sample: str,
    expected_sessions_total: int,
    stabilize_timeout_seconds: int,
    poll_seconds: int,
    stability_polls: int,
    status_path: Path,
    import_summary: Path,
    import_status: str,
) -> dict[str, Any]:
    deadline = time.time() + max(0, int(stabilize_timeout_seconds))
    stable_hits = 0
    previous_signature = ""
    previous_snapshot: dict[str, Any] | None = None
    last_snapshot: dict[str, Any] = {}
    while True:
        snapshot = build_workspace_snapshot(workspace, account, sample)
        signature = str(snapshot.get("signature") or "")
        ready = snapshot_ready(snapshot, expected_sessions_total)
        if signature and signature == previous_signature:
            stable_hits += 1
        else:
            stable_hits = 1
        previous_signature = signature
        previous_snapshot = snapshot
        last_snapshot = snapshot
        timed_out = time.time() >= deadline
        status = {
            "stage": "waiting_async_memory_settle",
            "import_summary": str(import_summary),
            "import_status": import_status,
            "expected_sessions": expected_sessions_total,
            "stable_hits": stable_hits,
            "required_stable_hits": int(max(1, stability_polls)),
            "stabilize_timeout_seconds": int(max(0, stabilize_timeout_seconds)),
            "timed_out": timed_out,
            "snapshot": snapshot,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        if ready and stable_hits >= max(1, int(stability_polls)):
            return {
                "ready": True,
                "timed_out": False,
                "stable_hits": stable_hits,
                "snapshot": snapshot,
            }
        if timed_out:
            return {
                "ready": ready,
                "timed_out": True,
                "stable_hits": stable_hits,
                "snapshot": snapshot,
                "previous_snapshot": previous_snapshot,
            }
        time.sleep(max(5, int(poll_seconds)))


def write_status(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    payload = dict(payload)
    payload.setdefault("checked_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def require_memory_ready_or_exit(
    *,
    status_path: Path,
    stage: str,
    import_summary: Path,
    import_status: str,
    stabilize_result: dict[str, Any],
    expected_sessions: int,
    allow_partial: bool = False,
) -> None:
    ready = bool(stabilize_result.get("ready"))
    if ready or allow_partial:
        return
    write_status(
        status_path,
        {
            "stage": stage,
            "import_summary": str(import_summary),
            "import_status": import_status,
            "expected_sessions": expected_sessions,
            "memory_settle": stabilize_result,
        },
    )
    raise SystemExit(3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait for EchoMemory import, then run QA and judge.")
    parser.add_argument("--import-summary", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--echomem-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--qa-out-dir", required=True)
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--sample", default="conv-30")
    parser.add_argument("--questions", default="")
    parser.add_argument("--settle-seconds", type=int, default=180)
    parser.add_argument("--stabilize-timeout-seconds", type=int, default=300)
    parser.add_argument("--stability-polls", type=int, default=3)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--repair-before-qa", action="store_true", default=True)
    parser.add_argument("--no-repair-before-qa", dest="repair_before_qa", action="store_false")
    parser.add_argument("--repair-flush-call-timeout-s", type=int, default=600)
    parser.add_argument("--repair-flush-attempts", type=int, default=2)
    parser.add_argument("--repair-commit-wait-s", type=int, default=300)
    parser.add_argument("--answer-base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--answer-model", default="deepseek-v4-flash")
    parser.add_argument("--answer-token", default="")
    parser.add_argument("--judge-base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--judge-model", default="deepseek-v4-flash")
    parser.add_argument("--judge-token", default="")
    parser.add_argument("--prompt-mode", choices=["vikingboat_lite", "vikingboat_compat", "one_shot"], default="vikingboat_lite")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--score-threshold", type=float, default=0.1)
    parser.add_argument("--memory-budget-chars", type=int, default=6000)
    parser.add_argument("--user-memory-budget-chars", type=int, default=4000)
    parser.add_argument("--agent-memory-budget-chars", type=int, default=2000)
    parser.add_argument("--retrieval-mode", choices=["find", "search", "both", "local"], default="search")
    parser.add_argument("--retrieval-ranker", choices=["diversified", "score"], default="score")
    parser.add_argument("--retrieval-uri-dedup", dest="retrieval_uri_dedup", action="store_true", default=True)
    parser.add_argument("--no-retrieval-uri-dedup", dest="retrieval_uri_dedup", action="store_false")
    parser.add_argument("--tool-set", choices=["vikingboat_default", "search_read", "search_only", "vikingbot_native_safe"], default="search_read")
    parser.add_argument("--tool-search-limit", type=int, default=20)
    parser.add_argument("--tool-min-score", type=float, default=0.35)
    parser.add_argument("--tool-log-chars", type=int, default=1200)
    parser.add_argument("--prefetch-read-count", type=int, default=4)
    parser.add_argument("--prefetch-context-chars", type=int, default=5000)
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--search-overview-enrichment", dest="search_overview_enrichment", action="store_true", default=True)
    parser.add_argument("--no-search-overview-enrichment", dest="search_overview_enrichment", action="store_false")
    parser.add_argument("--vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_true", default=True)
    parser.add_argument("--no-vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_false")
    parser.add_argument("--vikingboat-compat", dest="vikingboat_compat", action="store_true", default=False)
    parser.add_argument("--no-vikingboat-compat", dest="vikingboat_compat", action="store_false")
    parser.add_argument("--initial-tool-prefetch", dest="initial_tool_prefetch", action="store_true", default=True)
    parser.add_argument("--no-initial-tool-prefetch", dest="initial_tool_prefetch", action="store_false")
    parser.add_argument("--fallback-to-one-shot", dest="fallback_to_one_shot", action="store_true", default=True)
    parser.add_argument("--no-fallback-to-one-shot", dest="fallback_to_one_shot", action="store_false")
    args = parser.parse_args()

    import_summary = Path(args.import_summary).expanduser().resolve()
    qa_out_dir = Path(args.qa_out_dir).expanduser().resolve()
    qa_out_dir.mkdir(parents=True, exist_ok=True)
    status_path = qa_out_dir / "auto_eval_status.json"
    qa_log = qa_out_dir / "auto_eval_qa.log"
    judge_log = qa_out_dir / "auto_eval_judge.log"
    qa_csv = qa_out_dir / "echomemory_memory_qa_results.csv"

    proceed_statuses = {"ECHOMEMORY_IMPORT_DONE", "ECHOMEMORY_IMPORT_PARTIAL", "ECHOMEMORY_IMPORT_ASYNC_SETTLING"}
    fail_statuses = {"ECHOMEMORY_IMPORT_INCOMPLETE"}
    while True:
        write_status(
            status_path,
            {
                "stage": "waiting_import",
                "import_summary": str(import_summary),
            },
        )
        if import_summary.exists():
            summary = read_json(import_summary)
            import_status = str(summary.get("status") or "").upper()
            if import_status in proceed_statuses:
                break
            if import_status in fail_statuses:
                write_status(
                    status_path,
                    {
                        "stage": "import_failed",
                        "import_summary": str(import_summary),
                        "import_status": import_status,
                    },
                )
                raise SystemExit(2)
        time.sleep(max(5, int(args.poll_seconds)))

    summary = read_json(import_summary)
    import_status = str(summary.get("status") or "").upper()
    expected_sessions = expected_session_count(summary)
    write_status(
        status_path,
        {
            "stage": "settling_after_import",
            "import_summary": str(import_summary),
            "import_status": import_status,
            "sample": args.sample,
            "settle_seconds": int(args.settle_seconds),
            "expected_sessions": expected_sessions,
        },
    )
    time.sleep(max(0, int(args.settle_seconds)))
    stabilize_result = wait_for_async_memory_stability(
        workspace=Path(args.workspace).expanduser().resolve(),
        account=args.account,
        sample=args.sample,
        expected_sessions_total=expected_sessions,
        stabilize_timeout_seconds=int(args.stabilize_timeout_seconds),
        poll_seconds=int(args.poll_seconds),
        stability_polls=int(args.stability_polls),
        status_path=status_path,
        import_summary=import_summary,
        import_status=import_status,
    )

    env = dict(os.environ)
    answer_token = args.answer_token or os.environ.get("DASHSCOPE_API_KEY", "")
    if answer_token:
        env["DASHSCOPE_API_KEY"] = answer_token
    repair_summary_path = qa_out_dir / "echomemory_repair_summary.json"
    repair_log = qa_out_dir / "auto_eval_repair.log"
    repair_code = 0
    if args.repair_before_qa:
        repair_cmd = [
            args.python_bin,
            str(SCRIPTS / "echomemory_repair_sessions.py"),
            "--out-dir",
            str(qa_out_dir),
            "--echomem-root",
            args.echomem_root,
            "--workspace",
            args.workspace,
            "--account",
            args.account,
            "--user-id",
            args.user_id,
            "--agent-id",
            args.agent_id,
            "--sample",
            args.sample,
            "--commit-wait-s",
            str(args.repair_commit_wait_s),
            "--flush-call-timeout-s",
            str(args.repair_flush_call_timeout_s),
            "--flush-attempts",
            str(args.repair_flush_attempts),
        ]
        write_status(
            status_path,
            {
                "stage": "running_repair",
                "repair_log": str(repair_log),
                "repair_cmd": repair_cmd,
                "memory_settle": stabilize_result,
            },
        )
        repair_code = run_and_log(repair_cmd, repair_log, env)

    if not stabilize_result.get("ready") and args.repair_before_qa and repair_code == 0:
        write_status(
            status_path,
            {
                "stage": "rechecking_after_repair",
                "import_summary": str(import_summary),
                "import_status": import_status,
                "sample": args.sample,
                "expected_sessions": expected_sessions,
                "repair_log": str(repair_log),
            },
        )
        stabilize_result = wait_for_async_memory_stability(
            workspace=Path(args.workspace).expanduser().resolve(),
            account=args.account,
            sample=args.sample,
            expected_sessions_total=expected_sessions,
            stabilize_timeout_seconds=min(int(args.stabilize_timeout_seconds), 180),
            poll_seconds=max(5, int(args.poll_seconds)),
            stability_polls=max(1, int(args.stability_polls)),
            status_path=status_path,
            import_summary=import_summary,
            import_status=import_status,
        )

    require_memory_ready_or_exit(
        status_path=status_path,
        stage="qa_blocked_memory_not_ready",
        import_summary=import_summary,
        import_status=import_status,
        stabilize_result=stabilize_result,
        expected_sessions=expected_sessions,
    )

    qa_cmd = [
        args.python_bin,
        str(SCRIPTS / "echomemory_memory_qa.py"),
        "--dataset",
        args.dataset,
        "--out-dir",
        str(qa_out_dir),
        "--sample",
        args.sample,
        "--echomem-root",
        args.echomem_root,
        "--workspace",
        args.workspace,
        "--account",
        args.account,
        "--user-id",
        args.user_id,
        "--agent-id",
        args.agent_id,
        "--prompt-mode",
        args.prompt_mode,
        "--top-k",
        str(args.top_k),
        "--score-threshold",
        str(args.score_threshold),
        "--memory-budget-chars",
        str(args.memory_budget_chars),
        "--user-memory-budget-chars",
        str(args.user_memory_budget_chars),
        "--agent-memory-budget-chars",
        str(args.agent_memory_budget_chars),
        "--retrieval-mode",
        args.retrieval_mode,
        "--retrieval-ranker",
        args.retrieval_ranker,
        "--answer-base-url",
        args.answer_base_url,
        "--answer-model",
        args.answer_model,
        "--model-retries",
        "5",
        "--timeout-s",
        "180",
        "--question-timeout-s",
        "300",
        "--tool-set",
        args.tool_set,
        "--tool-search-limit",
        str(args.tool_search_limit),
        "--tool-min-score",
        str(args.tool_min_score),
        "--tool-log-chars",
        str(args.tool_log_chars),
        "--prefetch-read-count",
        str(args.prefetch_read_count),
        "--prefetch-context-chars",
        str(args.prefetch_context_chars),
        "--max-iterations",
        str(args.max_iterations),
    ]
    if args.answer_token:
        qa_cmd.extend(["--answer-token", args.answer_token])
    if str(args.questions or "").strip():
        qa_cmd.extend(["--questions", str(args.questions).strip()])
    if args.retrieval_mode == "local":
        qa_cmd.extend([
            "--local-session-summaries",
            "--local-atoms",
            "--no-local-messages",
            "--local-timeline-hints",
            "--local-memory-artifacts",
        ])
    else:
        qa_cmd.extend([
            "--no-local-session-summaries",
            "--no-local-atoms",
            "--no-local-messages",
            "--no-local-timeline-hints",
            "--no-local-memory-artifacts",
        ])
    qa_cmd.extend(
        [
            "--retrieval-uri-dedup" if args.retrieval_uri_dedup else "--no-retrieval-uri-dedup",
            "--search-overview-enrichment" if args.search_overview_enrichment else "--no-search-overview-enrichment",
            "--vikingboat-tool-loop" if args.vikingboat_tool_loop else "--no-vikingboat-tool-loop",
            "--vikingboat-compat" if args.vikingboat_compat else "--no-vikingboat-compat",
            "--initial-tool-prefetch" if args.initial_tool_prefetch else "--no-initial-tool-prefetch",
            "--fallback-to-one-shot" if args.fallback_to_one_shot else "--no-fallback-to-one-shot",
        ]
    )
    write_status(
        status_path,
        {
            "stage": "running_qa",
            "qa_out_dir": str(qa_out_dir),
            "qa_log": str(qa_log),
            "qa_cmd": qa_cmd,
            "memory_settle": stabilize_result,
            "repair_before_qa": bool(args.repair_before_qa),
            "repair_exit_code": repair_code,
            "repair_summary": str(repair_summary_path) if repair_summary_path.exists() else "",
        },
    )
    qa_code = run_and_log(qa_cmd, qa_log, env)

    write_status(
        status_path,
        {
            "stage": "running_judge" if qa_code == 0 and qa_csv.exists() else "qa_failed",
            "qa_exit_code": qa_code,
            "qa_csv": str(qa_csv),
        },
    )
    if qa_code != 0 or not qa_csv.exists():
        raise SystemExit(qa_code or 1)

    judge_env = dict(env)
    judge_token = args.judge_token or os.environ.get("DASHSCOPE_API_KEY", "")
    if judge_token:
        judge_env["DASHSCOPE_API_KEY"] = judge_token
    judge_cmd = [
        args.python_bin,
        str(SCRIPTS / "local_judge.py"),
        "--input",
        str(qa_csv),
        "--base-url",
        args.judge_base_url,
        "--model",
        args.judge_model,
        "--parallel",
        "10",
        "--timeout-s",
        "90",
        "--retries",
        "5",
    ]
    write_status(
        status_path,
        {
            "stage": "running_judge",
            "judge_log": str(judge_log),
            "judge_cmd": judge_cmd,
        },
    )
    judge_code = run_and_log(judge_cmd, judge_log, judge_env)

    write_status(
        status_path,
        {
            "stage": "done" if judge_code == 0 else "judge_failed",
            "qa_exit_code": qa_code,
            "judge_exit_code": judge_code,
            "qa_csv": str(qa_csv),
            "judge_summary": str(qa_out_dir / "judge_summary.json"),
        },
    )
    raise SystemExit(judge_code)


if __name__ == "__main__":
    main()
