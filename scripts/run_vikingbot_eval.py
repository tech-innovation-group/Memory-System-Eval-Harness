#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

import benchmark_adapter


MODEL_ERROR_RE = re.compile(r"^\s*(error calling llm|\[cmd error\]|\[parse error\]|\[timeout\])", re.I)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def vikingbot_supports_memory_user(candidate: str) -> bool:
    try:
        result = subprocess.run(
            [candidate, "chat", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and "--memory-user" in (result.stdout or "")


def find_vikingbot(require_memory_user: bool = False) -> str:
    env = os.environ.get("VIKINGBOT_BIN", "") or os.environ.get("VIKINGBOAT_BIN", "")
    candidates = [
        env,
        str(Path.home() / "openviking-locomo-latest-20260528/.venv/bin/vikingbot"),
        str(Path.home() / "openviking-locomo-latest-20260528/.venv/bin/vikingboat"),
        str(Path.home() / "openviking-v0312-fresh-venv/bin/vikingbot"),
        str(Path.home() / "openviking-v0312-fresh-venv/bin/vikingboat"),
        str(Path.home() / "openviking-latest/.venv/bin/vikingbot"),
        str(Path.home() / "openviking-latest/.venv/bin/vikingboat"),
        "vikingbot",
        "vikingboat",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "--help"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
            if result.returncode == 0:
                if require_memory_user and not vikingbot_supports_memory_user(candidate):
                    continue
                return candidate
        except Exception:
            continue
    return ""


def job_prompt(job: benchmark_adapter.Job) -> str:
    if job.query_time:
        return f"Current date: {job.query_time}. Answer the question directly: {job.question}"
    return f"Answer the question directly: {job.question}"


def parse_memory_users(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        raw = value
    else:
        text = str(value or "").strip()
        try:
            decoded = json.loads(text)
            raw = decoded if isinstance(decoded, list) else [decoded]
        except Exception:
            raw = re.split(r"[,，;；\n]+", text)
    users: list[str] = []
    for item in raw:
        user = str(item or "").strip()
        if user and user not in users:
            users.append(user)
    return users


def parse_cli_json(output: str) -> dict[str, Any] | None:
    text = str(output or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text, strict=False)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1], strict=False)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


def infer_vikingbot_provider(model: str, api_base: str) -> str:
    raw_model = str(model or "").strip()
    raw_base = str(api_base or "").strip().lower()
    if "/" in raw_model:
        return raw_model.split("/", 1)[0]
    lowered = raw_model.lower()
    if "openrouter" in raw_base:
        return "openrouter"
    if "aihubmix" in raw_base:
        return "aihubmix"
    if "ark" in raw_base or "volces" in raw_base or "volcengine" in lowered or "doubao" in lowered:
        return "volcengine"
    if "dashscope" in raw_base or "qwen" in lowered:
        return "dashscope"
    if "deepseek" in lowered:
        return "deepseek"
    if lowered.startswith("gpt") or raw_base.endswith("/v1") or "/v1/" in raw_base:
        return "openai"
    return ""


def env_answer_token() -> str:
    return (
        os.environ.get("LOCOMO_JUDGE_TOKEN")
        or os.environ.get("JUDGE_TOKEN")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )


def prepare_vikingbot_config(args: argparse.Namespace, out_dir: Path) -> str:
    """Create a run-local ov.conf so Web payload model/server settings reach native VikingBot."""
    raw_config = str(args.config or os.environ.get("OPENVIKING_CONFIG_FILE") or "").strip()
    cfg: dict[str, Any] = {}
    if raw_config:
        path = Path(raw_config).expanduser()
        if path.exists():
            try:
                cfg = read_json(path)
            except Exception:
                cfg = {}
    cfg.setdefault("vlm", {})
    ov_server = cfg.setdefault("bot", {}).setdefault("ov_server", {})
    cfg.setdefault("storage", {})
    if args.answer_base_url:
        cfg["vlm"]["api_base"] = args.answer_base_url
    elif not cfg["vlm"].get("api_base") and os.environ.get("JUDGE_BASE_URL"):
        cfg["vlm"]["api_base"] = os.environ["JUDGE_BASE_URL"]
    if args.answer_model:
        cfg["vlm"]["model"] = args.answer_model
    elif not cfg["vlm"].get("model"):
        cfg["vlm"]["model"] = os.environ.get("ANSWER_MODEL") or os.environ.get("JUDGE_MODEL") or "gpt-5.5"
    provider = str(getattr(args, "answer_provider", "") or cfg["vlm"].get("provider") or "").strip()
    if not provider:
        provider = infer_vikingbot_provider(str(cfg["vlm"].get("model") or ""), str(cfg["vlm"].get("api_base") or ""))
    if provider:
        cfg["vlm"]["provider"] = provider
    if args.answer_token:
        cfg["vlm"]["api_key"] = args.answer_token
    elif not cfg["vlm"].get("api_key") and env_answer_token():
        cfg["vlm"]["api_key"] = env_answer_token()
    # Match VikingBot's native config schema. Older harness code used `url`,
    # but vikingbot.config.schema.OpenVikingConfig reads `server_url`.
    if ov_server.get("url") and not ov_server.get("server_url"):
        ov_server["server_url"] = ov_server.get("url")
    ov_server.pop("url", None)
    ov_server["mode"] = "remote"
    ov_server["api_key_type"] = "root"
    if args.openviking_url:
        ov_server["server_url"] = args.openviking_url.rstrip("/")
    if args.account:
        ov_server["account_id"] = args.account
    if args.openviking_api_key:
        ov_server["root_api_key"] = args.openviking_api_key
    if args.workspace:
        cfg["storage"]["workspace"] = str(Path(args.workspace).expanduser().resolve())
    config_path = out_dir / "vikingbot_native.ov.conf"
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(config_path)


def native_identity(args: argparse.Namespace, job: benchmark_adapter.Job) -> tuple[str, str, list[str]]:
    sample_id = str(job.original_sample_id or job.sample_id)
    session_id = str(getattr(job, "native_question_id", "") or job.question_id)
    memory_users = parse_memory_users(job.memory_users) if args.group_chat else []
    return sample_id, session_id, memory_users


def run_native_vikingbot_chat(
    args: argparse.Namespace,
    job: benchmark_adapter.Job,
    vikingbot_bin: str,
    config_path: str,
) -> tuple[str, dict[str, Any], float, int, list[Any], str]:
    sample_id, session_id, memory_users = native_identity(args, job)
    base_cmd = [vikingbot_bin, "chat"]
    if config_path:
        base_cmd += ["--config", config_path]

    reset_cmd = [*base_cmd, "-m", "/new", "-e", "--sender", sample_id, "--session", session_id]
    for user in memory_users:
        reset_cmd += ["--memory-user", user]
    try:
        subprocess.run(reset_cmd, capture_output=True, text=True, timeout=min(max(args.timeout_s, 60), 300), check=False)
    except Exception:
        pass

    cmd = [*base_cmd, "-m", job_prompt(job), "-e", "--sender", sample_id, "--session", session_id]
    for user in memory_users:
        cmd += ["--memory-user", user]
    started = time.time()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=max(args.timeout_s, 60),
        check=False,
    )
    elapsed = time.time() - started
    combined = (proc.stdout or "").strip()
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        return (
            f"[CMD ERROR] {stderr or combined}",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            elapsed,
            0,
            [],
            "failed",
        )
    payload = parse_cli_json(combined)
    if not payload:
        return (
            f"[PARSE ERROR] {combined}",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            elapsed,
            0,
            [],
            "failed",
        )
    usage = payload.get("token_usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    answer = str(payload.get("text") or "").strip()
    status = "failed" if MODEL_ERROR_RE.search(answer) else "ok"
    return (
        answer,
        {
            "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
            "total_tokens": usage.get("total_tokens")
            or ((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)),
        },
        float(payload.get("time_cost") or elapsed),
        int(payload.get("iteration") or 0),
        payload.get("tools_used_names") if isinstance(payload.get("tools_used_names"), list) else [],
        status,
    )


def native_row(
    args: argparse.Namespace,
    job: benchmark_adapter.Job,
    response: str,
    token_usage: dict[str, Any],
    time_cost: float,
    iteration: int,
    tools_used_names: list[Any],
    status: str,
    vikingbot_bin: str,
) -> dict[str, str]:
    row = {key: str(value) for key, value in asdict(job).items()}
    simple = benchmark_adapter.simple_grade(job.answer, response)
    tool_names = [str(item) for item in tools_used_names]
    row.update(
        {
            "response": response,
            "simple_grade": simple,
            "result": "",
            "reasoning": "native VikingBot CLI output; pending judge",
            "time_cost": f"{time_cost:.4f}",
            "token_usage": json.dumps(token_usage, ensure_ascii=False),
            "iteration": str(iteration),
            "tools_used_names": json.dumps(tool_names, ensure_ascii=False),
            "tool_call_count": str(len(tool_names)),
            "tool_call_name_counts": json.dumps(dict(Counter(tool_names)), ensure_ascii=False),
            "tools_used": json.dumps(tool_names, ensure_ascii=False),
            "answer_prompt_tokens": str(token_usage.get("prompt_tokens") or 0),
            "answer_completion_tokens": str(token_usage.get("completion_tokens") or 0),
            "answer_total_tokens": str(token_usage.get("total_tokens") or 0),
            "eval_engine": "native_vikingbot_cli",
            "vikingbot_bin": vikingbot_bin,
            "native_prompt": job_prompt(job),
            "prompt_mode": "native_vikingbot_cli",
            "vikingbot_prompt_aligned": "true",
            "vikingbot_channel": "cli",
            "vikingbot_identity_mode": "sender_session",
            "group_chat": str(bool(args.group_chat)).lower(),
            "native_sender": str(job.original_sample_id or job.sample_id),
            "native_session": str(getattr(job, "native_question_id", "") or job.question_id),
            "native_question_id": str(getattr(job, "native_question_id", "") or ""),
            "native_memory_users": json.dumps(parse_memory_users(job.memory_users) if args.group_chat else [], ensure_ascii=False),
            "memory_user_strategy": "vikingbot_group_chat" if args.group_chat else "sender_only",
            "openviking_tool_loop_enabled": "native",
            "openviking_tool_set": "native_vikingbot_cli",
            "openviking_content_read_enabled": "native",
            "retrieval_status": "ok",
            "answer_status": status,
            "model_status": status,
            "health_status": "ok" if status == "ok" else "api_error",
            "relevant_memory": "[]",
            "retrieval_count": "",
            "retrieval_tokens_est": "",
        }
    )
    return row


def answer_job(args: argparse.Namespace, job: benchmark_adapter.Job, index: int, vikingbot_bin: str) -> dict[str, str]:
    if args.engine == "openviking_memory":
        import openviking_memory_qa

        print(f"[vikingbot] {index}/{args.total_jobs} {job.question_id} diagnostic wrapper", flush=True)
        row = openviking_memory_qa.answer_question(args, job)
        row["eval_engine"] = "openviking_memory_diagnostic"
        row["vikingbot_bin"] = vikingbot_bin or ""
        return row
    if not vikingbot_bin:
        raise RuntimeError("native VikingBot CLI not found. Set VIKINGBOT_BIN.")
    sample_id, session_id, memory_users = native_identity(args, job)
    print(
        f"[vikingbot] {index}/{args.total_jobs} {job.question_id} native CLI "
        f"sender={sample_id} session={session_id} memory_users={memory_users}",
        flush=True,
    )
    attempts = max(1, int(getattr(args, "native_retries", 0) or 0) + 1)
    last: tuple[str, dict[str, Any], float, int, list[Any], str] | None = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            print(
                f"[vikingbot] retry {attempt}/{attempts} {job.question_id} after native api_error",
                flush=True,
            )
            time.sleep(max(0.0, float(getattr(args, "native_retry_sleep", 0) or 0)))
        last = run_native_vikingbot_chat(
            args,
            job,
            vikingbot_bin,
            args.native_config,
        )
        response, _token_usage, _time_cost, _iteration, _tools_used, status = last
        if status == "ok" and not MODEL_ERROR_RE.search(str(response or "")):
            break
    assert last is not None
    response, token_usage, time_cost, iteration, tools_used, status = last
    row = native_row(args, job, response, token_usage, time_cost, iteration, tools_used, status, vikingbot_bin)
    row["native_attempts"] = str(attempt)
    row["native_retry_count"] = str(max(0, attempt - 1))
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a VikingBot-style LoCoMo eval over OpenViking memories.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--config", default="")
    parser.add_argument("--sample", default="1")
    parser.add_argument("--questions", default="")
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--engine", choices=["auto", "vikingbot", "openviking_memory"], default="auto")
    parser.add_argument("--openviking-url", default="http://127.0.0.1:1933")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--openviking-api-key", default="")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--lexical-fallback", action="store_true", default=False)
    parser.add_argument("--lexical-top-k", type=int, default=8)
    parser.add_argument("--group-chat", dest="group_chat", action="store_true", default=True)
    parser.add_argument("--no-group-chat", dest="group_chat", action="store_false")
    parser.add_argument("--answer-base-url", default="")
    parser.add_argument("--answer-model", default="")
    parser.add_argument("--answer-provider", default="")
    parser.add_argument("--answer-token", default="")
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--judge-token", default="")
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument("--native-retries", type=int, default=2)
    parser.add_argument("--native-retry-sleep", type=float, default=3.0)
    args = parser.parse_args()

    if args.judge_base_url and not args.answer_base_url:
        args.answer_base_url = args.judge_base_url
    if args.judge_model:
        args.answer_model = args.judge_model
    if args.judge_token and not args.answer_token:
        args.answer_token = args.judge_token

    data = read_json(Path(args.dataset).expanduser().resolve())
    question_filter = {q.strip() for q in args.questions.split(",") if q.strip()}
    jobs, _plans = benchmark_adapter.locomo_jobs(data, None, args.sample, question_filter or None)
    if args.random_count:
        rnd = random.Random(args.random_seed)
        jobs = rnd.sample(jobs, min(args.random_count, len(jobs)))
    args.total_jobs = len(jobs)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = Path(args.output).expanduser().resolve() if args.output else out_dir / "vikingbot_eval.csv"
    args.native_config = prepare_vikingbot_config(args, out_dir)
    vikingbot_bin = find_vikingbot(require_memory_user=bool(args.group_chat))
    if args.engine in {"auto", "vikingbot"} and not vikingbot_bin:
        if args.group_chat:
            raise SystemExit("native VikingBot CLI with --memory-user support not found. Set VIKINGBOT_BIN to the LoCoMo VikingBot environment.")
        raise SystemExit("native VikingBot CLI not found. Set VIKINGBOT_BIN.")

    print(
        f"[vikingbot] dataset={args.dataset} sample={args.sample} questions={len(jobs)} "
        f"engine={args.engine} cli={'yes' if vikingbot_bin else 'no'} openviking={args.openviking_url}",
        flush=True,
    )
    print("[vikingbot] mode=native vikingbot chat; question-isolated /new + --sender + --session", flush=True)

    started = time.time()
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    indexed: list[tuple[int, dict[str, str]]] = []
    workers = max(1, int(args.parallel or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(answer_job, args, job, i, vikingbot_bin): i for i, job in enumerate(jobs, 1)}
        for done, future in enumerate(as_completed(futures), 1):
            idx = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                job = jobs[idx - 1]
                row = {
                    **{key: str(value) for key, value in asdict(job).items()},
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[VIKINGBOT EVAL ERROR] {exc}",
                    "time_cost": "0",
                    "memory_uri": "viking://user/default/memories/",
                    "relevant_memory": "[]",
                    "retrieval_count": "0",
                    "retrieval_tokens_est": "0",
                    "answer_prompt_tokens": "0",
                    "answer_completion_tokens": "0",
                    "answer_total_tokens": "0",
                    "eval_engine": "native_vikingbot_cli",
                    "answer_status": "failed",
                    "model_status": "failed",
                    "health_status": "api_error",
                    "native_prompt": job_prompt(job),
                }
            indexed.append((idx, row))
            fieldnames = list(dict.fromkeys(fieldnames + list(row.keys())))
            rows = [item for _, item in sorted(indexed)]
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            print(f"[qa] {done}/{len(jobs)} {row.get('question_id') or idx} -> {row.get('simple_grade') or 'NEEDS_JUDGE'}", flush=True)

    rows = [item for _, item in sorted(indexed)]
    summary = {
        "count": len(rows),
        "output_csv": str(csv_path),
        "engine": "native_vikingbot_cli" if args.engine != "openviking_memory" else "openviking_memory_diagnostic",
        "native_cli_detected": bool(vikingbot_bin),
        "native_config": str(args.native_config),
        "prompt_mode": "native_vikingbot_cli",
        "vikingbot_prompt_aligned": True,
        "vikingbot_channel": "cli",
        "vikingbot_identity_mode": "sender_session",
        "group_chat": bool(args.group_chat),
        "memory_user_strategy": "vikingbot_group_chat" if args.group_chat else "sender_only",
        "initial_agent_memory_enabled": False,
        "openviking_tool_loop_enabled": "native",
        "openviking_tool_set": "native_vikingbot_cli",
        "openviking_content_read_enabled": "native",
        "top_k": "native_vikingbot_internal",
        "query_expansion_enabled": False,
        "lexical_fallback_enabled": False,
        "archive_fallback_enabled": False,
        "memory_file_read_enabled": False,
        "duration_s": round(time.time() - started, 3),
        "answer_prompt_tokens": sum(int(r.get("answer_prompt_tokens") or 0) for r in rows),
        "answer_completion_tokens": sum(int(r.get("answer_completion_tokens") or 0) for r in rows),
        "answer_total_tokens": sum(int(r.get("answer_total_tokens") or 0) for r in rows),
        "retrieval_tokens_est": sum(int(r.get("retrieval_tokens_est") or 0) for r in rows),
        "avg_retrieval_count": round(sum(int(r.get("retrieval_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "iteration_total": sum(int(r.get("iteration") or 0) for r in rows),
        "avg_iteration": round(sum(int(r.get("iteration") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "tool_call_total": sum(int(r.get("tool_call_count") or 0) for r in rows),
        "tool_call_rows": sum(1 for r in rows if int(r.get("tool_call_count") or 0) > 0),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
