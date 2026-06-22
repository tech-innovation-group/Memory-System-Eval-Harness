#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from echomemory_common import DEFAULT_ECHOMEM_ROOT


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def looks_like_echomem_root(path: Path) -> bool:
    return (
        ((path / "packages" / "echomem" / "src").exists() and (path / "packages" / "echofs" / "src").exists())
        or ((path / "echomem").exists() and (path / "pyproject.toml").exists())
    )


def resolve_python_bin(raw: str, echomem_root: str) -> str:
    text = str(raw or "").strip()
    root = Path(echomem_root).expanduser().resolve()
    generic_names = {
        "python",
        "python3",
        "python3.9",
        "python3.10",
        "python3.11",
        "python3.12",
    }
    candidates: list[Path] = []
    if text:
        if "/" in text or text.startswith("."):
            path = Path(text).expanduser()
            # Treat the process default interpreter like "auto" so the runner
            # can still prefer EchoMemory's own .venv instead of silently
            # locking onto the system Python.
            if path.exists() and path.resolve() != Path(sys.executable).resolve():
                return str(path.resolve())
        elif text not in generic_names:
            return text
    if looks_like_echomem_root(root):
        candidates.append(root / ".venv/bin/python")
    candidates.extend(
        [
            Path.home() / "Code" / "echomemory" / "echo_memory/.venv/bin/python",
            Path.home() / "Code" / "echomemory" / "echo_memory_v006/.venv/bin/python",
            Path.home() / "Code" / "echomemory" / "echo_memory_v007/.venv/bin/python",
            Path.home() / "Code" / "echomemory" / "echo_memory_v007_tag/.venv/bin/python",
            Path.home() / "openviking-env/bin/python",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_and_log(cmd: list[str], log_path: Path, env: dict[str, str]) -> int:
    ensure_dir(log_path.parent)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("$ " + shell_join(cmd) + "\n")
        handle.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )
        return proc.wait()


def preflight_python_bin(python_bin: str, echomem_root: str) -> dict[str, Any]:
    cmd = [
        python_bin,
        "-c",
        (
            "import importlib.util, json, pathlib, sys; "
            "root=pathlib.Path(sys.argv[1]).expanduser().resolve(); "
            "payload={"
            "'python':sys.executable,"
            "'version':sys.version.split()[0],"
            "'root_exists':root.exists(),"
            "'tenacity':bool(importlib.util.find_spec('tenacity')),"
            "'echomem_protocol':bool(importlib.util.find_spec('echomem.protocol')),"
            "'echomem_sdk':bool(importlib.util.find_spec('echomem.entrypoints.plugins.echoagent.sdk'))"
            "}; "
            "print(json.dumps(payload, ensure_ascii=False))"
        ),
        echomem_root,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    payload: dict[str, Any]
    try:
        payload = json.loads(stdout) if stdout else {}
    except Exception:
        payload = {}
    payload.update(
        {
            "returncode": proc.returncode,
            "stdout": stdout[-1200:],
            "stderr": stderr[-1200:],
            "ok": bool(
                proc.returncode == 0
                and payload.get("tenacity")
                and (payload.get("echomem_protocol") or payload.get("echomem_sdk"))
            ),
        }
    )
    return payload


def build_import_cmd(
    *,
    python_bin: str,
    dataset: str,
    run_dir: Path,
    workspace: str,
    account: str,
    user_id: str,
    agent_id: str,
    echomem_root: str,
    protocol: dict[str, Any],
) -> list[str]:
    import_cfg = protocol["import"]
    cmd = [
        python_bin,
        str(ROOT / "scripts" / "echomemory_locomo_import.py"),
        "--dataset",
        dataset,
        "--out-dir",
        str(run_dir / "echomemory_import"),
        "--echomem-root",
        echomem_root,
        "--workspace",
        workspace,
        "--account",
        account,
        "--user-id",
        user_id,
        "--agent-id",
        agent_id,
        "--sample",
        "conv-30",
        "--session-mode",
        str(import_cfg["session_mode"]),
        "--import-wait-mode",
        str(import_cfg["import_wait_mode"]),
        "--commit-wait-s",
        str(import_cfg["commit_wait_s"]),
        "--commit-call-timeout-s",
        str(import_cfg["commit_call_timeout_s"]),
        "--flush-call-timeout-s",
        str(import_cfg["flush_call_timeout_s"]),
        "--flush-attempts",
        str(import_cfg["flush_attempts"]),
    ]
    if import_cfg.get("defer_artifact_wait"):
        cmd.append("--defer-artifact-wait")
    if import_cfg.get("skip_session_commit"):
        cmd.append("--skip-session-commit")
    if import_cfg.get("continue_on_session_error"):
        cmd.append("--continue-on-session-error")
    if import_cfg.get("fallback_to_mock"):
        cmd.append("--fallback-to-mock")
    return cmd


def build_eval_cmd(
    *,
    python_bin: str,
    dataset: str,
    run_dir: Path,
    workspace: str,
    account: str,
    user_id: str,
    agent_id: str,
    echomem_root: str,
    protocol: dict[str, Any],
    subset: dict[str, Any],
    answer_token: str,
    judge_token: str,
) -> list[str]:
    stabilize_cfg = protocol["stabilization"]
    qa_cfg = protocol["qa"]
    cmd = [
        python_bin,
        str(ROOT / "scripts" / "echomemory_wait_and_eval.py"),
        "--import-summary",
        str(run_dir / "echomemory_import" / "echomemory_import_summary.json"),
        "--dataset",
        dataset,
        "--echomem-root",
        echomem_root,
        "--workspace",
        workspace,
        "--account",
        account,
        "--user-id",
        user_id,
        "--agent-id",
        agent_id,
        "--qa-out-dir",
        str(run_dir / "echomemory_qa"),
        "--sample",
        str(subset.get("sample_id") or "conv-30"),
        "--questions",
        ",".join(subset.get("question_ids") or ()),
        "--settle-seconds",
        str(stabilize_cfg["settle_seconds"]),
        "--stabilize-timeout-seconds",
        str(stabilize_cfg["stabilize_timeout_seconds"]),
        "--stability-polls",
        str(stabilize_cfg["stability_polls"]),
        "--poll-seconds",
        str(stabilize_cfg["poll_seconds"]),
        "--repair-flush-call-timeout-s",
        str(stabilize_cfg["repair_flush_call_timeout_s"]),
        "--repair-flush-attempts",
        str(stabilize_cfg["repair_flush_attempts"]),
        "--repair-commit-wait-s",
        str(stabilize_cfg["repair_commit_wait_s"]),
        "--answer-base-url",
        str(qa_cfg["answer_base_url"]),
        "--answer-model",
        str(qa_cfg["answer_model"]),
        "--answer-token",
        answer_token,
        "--judge-base-url",
        str(qa_cfg["judge_base_url"]),
        "--judge-model",
        str(qa_cfg["judge_model"]),
        "--judge-token",
        judge_token,
        "--prompt-mode",
        str(qa_cfg["prompt_mode"]),
        "--top-k",
        str(qa_cfg["top_k"]),
        "--score-threshold",
        str(qa_cfg["score_threshold"]),
        "--memory-budget-chars",
        str(qa_cfg["memory_budget_chars"]),
        "--user-memory-budget-chars",
        str(qa_cfg["user_memory_budget_chars"]),
        "--agent-memory-budget-chars",
        str(qa_cfg["agent_memory_budget_chars"]),
        "--retrieval-mode",
        str(qa_cfg["retrieval_mode"]),
        "--retrieval-ranker",
        str(qa_cfg["retrieval_ranker"]),
        "--tool-set",
        str(qa_cfg["tool_set"]),
        "--tool-search-limit",
        str(qa_cfg["tool_search_limit"]),
        "--tool-min-score",
        str(qa_cfg["tool_min_score"]),
        "--tool-log-chars",
        str(qa_cfg["tool_log_chars"]),
        "--prefetch-read-count",
        str(qa_cfg["prefetch_read_count"]),
        "--prefetch-context-chars",
        str(qa_cfg["prefetch_context_chars"]),
        "--max-iterations",
        str(qa_cfg["max_iterations"]),
    ]
    if stabilize_cfg.get("repair_before_qa", True):
        cmd.append("--repair-before-qa")
    else:
        cmd.append("--no-repair-before-qa")
    cmd.append("--vikingboat-tool-loop" if qa_cfg.get("vikingboat_tool_loop", True) else "--no-vikingboat-tool-loop")
    cmd.append("--vikingboat-compat" if qa_cfg.get("vikingboat_compat", False) else "--no-vikingboat-compat")
    cmd.append("--initial-tool-prefetch" if qa_cfg.get("initial_tool_prefetch", True) else "--no-initial-tool-prefetch")
    cmd.append("--fallback-to-one-shot" if qa_cfg.get("fallback_to_one_shot", True) else "--no-fallback-to-one-shot")
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EchoMemory-MM LoCoMo subset benchmark using the frozen protocol.")
    parser.add_argument("--protocol", default=str(ROOT / "configs" / "echomemory_mm_benchmark_protocol_freeze_20260614.json"))
    parser.add_argument("--subset", default=str(ROOT / "configs" / "echomemory_mm_locomo_conv30_formal_subset20_20260614.json"))
    parser.add_argument("--dataset", default=str(ROOT / "dataset" / "locomo10.json"))
    parser.add_argument("--run-dir", default=str(ROOT / "runs" / "echomemory_mm_conv30_subset20_20260614"))
    parser.add_argument("--workspace", default="/private/tmp/echomemory_mm_conv30_subset20_20260614")
    parser.add_argument("--account", default="echomemory-mm-conv30-subset20")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--python-bin", default="")
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--answer-token", default=os.environ.get("DASHSCOPE_API_KEY", ""))
    parser.add_argument("--judge-token", default=os.environ.get("DASHSCOPE_API_KEY", ""))
    parser.add_argument("--print-only", action="store_true", default=False)
    parser.add_argument("--run-import", action="store_true", default=False)
    parser.add_argument("--run-all", action="store_true", default=False)
    args = parser.parse_args()

    protocol = read_json(Path(args.protocol).expanduser().resolve())
    subset = read_json(Path(args.subset).expanduser().resolve())
    run_dir = Path(args.run_dir).expanduser().resolve()
    ensure_dir(run_dir)
    python_bin = resolve_python_bin(args.python_bin, args.echomem_root)
    echomem_root = str(Path(args.echomem_root).expanduser().resolve())

    import_cmd = build_import_cmd(
        python_bin=python_bin,
        dataset=str(Path(args.dataset).expanduser().resolve()),
        run_dir=run_dir,
        workspace=args.workspace,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        echomem_root=echomem_root,
        protocol=protocol,
    )
    eval_cmd = build_eval_cmd(
        python_bin=python_bin,
        dataset=str(Path(args.dataset).expanduser().resolve()),
        run_dir=run_dir,
        workspace=args.workspace,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        echomem_root=echomem_root,
        protocol=protocol,
        subset=subset,
        answer_token=str(args.answer_token or ""),
        judge_token=str(args.judge_token or ""),
    )

    manifest = {
        "protocol": str(Path(args.protocol).expanduser().resolve()),
        "subset": str(Path(args.subset).expanduser().resolve()),
        "dataset": str(Path(args.dataset).expanduser().resolve()),
        "run_dir": str(run_dir),
        "workspace": str(Path(args.workspace).expanduser()),
        "account": args.account,
        "user_id": args.user_id,
        "agent_id": args.agent_id,
        "question_count": len(subset.get("question_ids") or ()),
        "question_ids": subset.get("question_ids") or [],
        "resolved_python_bin": python_bin,
        "python_preflight": preflight_python_bin(python_bin, echomem_root),
        "import_cmd": import_cmd,
        "eval_cmd": eval_cmd,
    }
    write_json(run_dir / "subset20_manifest.json", manifest)

    print("Import command:")
    print(shell_join(import_cmd))
    print()
    print("Eval command:")
    print(shell_join(eval_cmd))

    python_preflight = manifest["python_preflight"]
    if not python_preflight.get("ok"):
        print()
        print("Python preflight failed:")
        print(json.dumps(python_preflight, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    if args.print_only or (not args.run_import and not args.run_all):
        return

    env = dict(os.environ)
    if args.answer_token:
        env["DASHSCOPE_API_KEY"] = args.answer_token
    import_log = run_dir / "subset20_import.log"
    eval_log = run_dir / "subset20_wait_and_eval.log"

    if args.run_import or args.run_all:
        code = run_and_log(import_cmd, import_log, env)
        if code != 0:
            raise SystemExit(code)

    if args.run_all:
        code = run_and_log(eval_cmd, eval_log, env)
        if code != 0:
            raise SystemExit(code)


if __name__ == "__main__":
    main()
