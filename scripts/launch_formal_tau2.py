#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAU_ROOT = ROOT / "external" / "tau2-bench"
TAU_BIN = TAU_ROOT / ".venv" / "bin" / "tau2"
JUDGE_CONF = ROOT / "judge.conf"

FORMAL_RUNS = {
    "airline": "formal_tau2_airline_base_gpt55_20260606",
    "retail": "formal_tau2_retail_base_gpt55_20260606",
    "telecom": "formal_tau2_telecom_base_gpt55_20260606",
    "banking_knowledge": "formal_tau2_banking_knowledge_bm25_gpt55_20260606",
}

DOMAIN_RUN_ARGS = {
    "airline": ["--task-split-name", "base"],
    "retail": ["--task-split-name", "base"],
    "telecom": ["--task-split-name", "base"],
    "banking_knowledge": ["--retrieval-config", "bm25"],
}


def load_model_config(path: Path) -> tuple[str, str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    vlm = data.get("vlm") or {}
    model = str(vlm.get("model") or "gpt-5.5")
    api_base = str(vlm.get("api_base") or "").strip()
    api_key = str(vlm.get("api_key") or "").strip()
    if not api_base:
        raise ValueError("judge.conf missing vlm.api_base")
    if not api_key:
        raise ValueError("judge.conf missing vlm.api_key")
    return model, api_base, api_key


def log_line(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def run_domain(domain: str, run_name: str, args: argparse.Namespace) -> int:
    model, api_base, api_key = load_model_config(Path(args.config))
    save_dir = TAU_ROOT / "data" / "simulations" / run_name
    save_dir.mkdir(parents=True, exist_ok=True)
    log_path = save_dir / "run.log"
    llm_name = f"openai/{model}" if not model.startswith("openai/") else model
    llm_args = json.dumps({"temperature": 1, "api_base": api_base}, separators=(",", ":"))
    cmd = [
        str(TAU_BIN),
        "run",
        "--domain",
        domain,
        *DOMAIN_RUN_ARGS.get(domain, []),
        "--num-trials",
        "1",
        "--max-concurrency",
        str(args.max_concurrency),
        "--timeout",
        str(args.timeout),
        "--agent-llm",
        llm_name,
        "--user-llm",
        llm_name,
        "--agent-llm-args",
        llm_args,
        "--user-llm-args",
        llm_args,
        "--save-to",
        run_name,
        "--auto-resume",
        "--log-level",
        args.log_level,
    ]
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = api_key
    env.setdefault("PYTHONUNBUFFERED", "1")
    log_line(log_path, f"[launch] {datetime.now().isoformat(timespec='seconds')} domain={domain} run={run_name}")
    log_line(log_path, "[launch] command=" + " ".join(cmd))
    with log_path.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.run(cmd, cwd=TAU_ROOT, env=env, stdout=log_handle, stderr=subprocess.STDOUT)
    log_line(log_path, f"[launch] {datetime.now().isoformat(timespec='seconds')} exit_code={proc.returncode}")
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch formal tau2-bench base split runs with real LLM calls.")
    parser.add_argument("--config", default=str(JUDGE_CONF))
    parser.add_argument("--domains", nargs="+", default=["airline", "retail", "telecom", "banking_knowledge"], choices=sorted(FORMAL_RUNS))
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    if not TAU_BIN.exists():
        raise FileNotFoundError(f"tau2 executable not found: {TAU_BIN}")
    failures: list[tuple[str, int]] = []
    for domain in args.domains:
        code = run_domain(domain, FORMAL_RUNS[domain], args)
        if code != 0:
            failures.append((domain, code))
            break
    if failures:
        print(json.dumps({"status": "failed", "failures": failures}, ensure_ascii=False))
        return failures[0][1]
    print(json.dumps({"status": "started_or_completed", "domains": args.domains}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
