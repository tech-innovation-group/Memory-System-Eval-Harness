#!/usr/bin/env python3
"""LoCoMo benchmark evaluation script.

流程:
  1. 集中导入所有 sample 的 conversation sessions 到 EchoMem (open -> add_messages -> commit -> poll)
  2. 逐题 QA: search EchoMem -> build prompt -> LLM answer (仅检索不写入)
  3. LLM judge: CORRECT / WRONG

用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# 确保能 import shared 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins import load_agent_plugin
from benchmarks.locomo.dataset import load_dataset
from benchmarks.locomo.diagnosis import diagnose_run
from benchmarks.locomo.blackbox import write_artifacts as write_blackbox_artifacts
from benchmarks.locomo.import_memory import (
    ImportOptions,
    import_locomo_memory,
    resolve_session_mode,
)
from benchmarks.locomo.judge import (
    LOCOMO_JUDGE_SYSTEM,
    LOCOMO_JUDGE_TEMPLATE,
    judge_locomo_results,
)
from benchmarks.locomo.profiles import (
    VIKINGBOAT_0411_PROFILE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
)
from benchmarks.locomo.provenance import (
    inspect_memory_provenance,
    write_memory_provenance,
)
from benchmarks.locomo.memory_scope import (
    SessionPrefixMemoryClient,
)
from benchmarks.locomo.qa import (
    QAOptions,
    build_qa_tasks,
    run_locomo_qa,
    write_tool_audits,
)
from benchmarks.locomo.resume import (
    build_qa_resume_manifest,
    build_judge_resume_manifest,
    copy_resume_traces,
    find_judge_resume_csv,
    find_qa_resume_csv,
    load_judge_resume_state,
    load_qa_resume_state,
    restore_resume_traces,
    write_judge_resume_manifest,
    write_qa_resume_manifest,
)
from benchmarks.locomo.reporting import build_summary
from benchmarks.locomo.selection import parse_question_ids, select_questions
from shared.dataset_io import resolve_dataset_path
from shared.eval_base import (
    EvalRun,
    add_agent_plugin_args,
    add_eval_args,
    add_judge_args,
    build_config_from_args,
    results_root_for,
    validate_eval_config,
)
from shared.import_guard import require_complete_imports
from shared.llm_client import LLMClient
from shared.resume_identity import apply_resume_memory_identity


def _redact_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return text[:4] + "***" + text[-4:] if len(text) > 8 else "***"


class EpisodePreparationError(RuntimeError):
    """Raised when runtime inspection or episode preparation is inconclusive."""

    def __init__(self, message: str, preparation: dict[str, Any]):
        super().__init__(message)
        self.preparation = preparation


def episode_recall_enabled(runtime_payload: dict[str, Any]) -> bool:
    """Read the effective episode flag from the EchoMem runtime response."""
    engines = runtime_payload.get("engines")
    if not isinstance(engines, list):
        raise ValueError("EchoMem /runtime response has no valid engines list")
    for engine in engines:
        if (
            isinstance(engine, dict)
            and engine.get("engine_id") == "episode_engine"
        ):
            return engine.get("recall_enabled") is True
    return False


def _episode_engine_loaded(runtime_payload: dict[str, Any]) -> bool:
    engines = runtime_payload.get("engines")
    if not isinstance(engines, list):
        raise ValueError("EchoMem /runtime response has no valid engines list")
    return any(
        isinstance(engine, dict)
        and engine.get("engine_id") == "episode_engine"
        for engine in engines
    )


def _episode_generation_status(response: dict[str, Any]) -> str:
    """Extract a stable status while tolerating small API response variants."""
    candidates: list[Any] = [
        response.get("generation_status"),
        response.get("status"),
        response.get("state"),
    ]
    result = response.get("result")
    if isinstance(result, dict):
        candidates.extend(
            [result.get("generation_status"), result.get("status"), result.get("state")]
        )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return "generated"


def _episode_generation_failed(status: str) -> bool:
    return status in {
        "error",
        "failed",
        "failure",
        "rejected",
        "timeout",
        "timed_out",
    }


def prepare_episode_recall(memory_client, result_dir: Path, log) -> dict[str, Any]:
    """Inspect effective runtime state and prepare Episode exactly once."""
    started = time.perf_counter()
    preparation: dict[str, Any] = {
        "engine_loaded": False,
        "recall_enabled": False,
        "generation_triggered": False,
        "generation_status": "",
        "generation_duration_ms": 0,
        "skip_reason": "",
        "response": {},
    }

    try:
        runtime_payload = memory_client.runtime()
        if not isinstance(runtime_payload, dict):
            raise ValueError("EchoMem /runtime response must be a JSON object")
        preparation["runtime"] = runtime_payload
        preparation["engine_loaded"] = _episode_engine_loaded(runtime_payload)
        preparation["recall_enabled"] = episode_recall_enabled(runtime_payload)
    except Exception as exc:
        preparation["generation_status"] = "runtime_probe_failed"
        preparation["error"] = str(exc)
        preparation["generation_duration_ms"] = round(
            (time.perf_counter() - started) * 1000
        )
        (result_dir / "episode_preparation.json").write_text(
            json.dumps(preparation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise EpisodePreparationError(
            f"Episode runtime 状态探测失败: {exc}",
            preparation,
        ) from exc

    if not preparation["engine_loaded"]:
        preparation["generation_status"] = "skipped"
        preparation["skip_reason"] = "episode_engine_not_loaded"
    elif not preparation["recall_enabled"]:
        preparation["generation_status"] = "skipped"
        preparation["skip_reason"] = "episode_recall_disabled"
    else:
        preparation["generation_triggered"] = True
        try:
            response = memory_client.generate_episode()
            if not isinstance(response, dict) or not response:
                raise ValueError(
                    "Episode generate response must be a non-empty JSON object"
                )
            preparation["response"] = response
            preparation["generation_status"] = _episode_generation_status(response)
            if _episode_generation_failed(preparation["generation_status"]):
                raise RuntimeError(
                    "Episode generate returned failure status: "
                    f"{preparation['generation_status']}"
                )
        except Exception as exc:
            preparation["generation_status"] = "generation_failed"
            preparation["error"] = str(exc)
            preparation["generation_duration_ms"] = round(
                (time.perf_counter() - started) * 1000
            )
            (result_dir / "episode_preparation.json").write_text(
                json.dumps(preparation, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raise EpisodePreparationError(
                f"Episode 生成失败: {exc}",
                preparation,
            ) from exc

    preparation["generation_duration_ms"] = round(
        (time.perf_counter() - started) * 1000
    )
    log.info(
        "Episode preparation: loaded=%s recall_enabled=%s triggered=%s "
        "status=%s skip_reason=%s",
        preparation["engine_loaded"],
        preparation["recall_enabled"],
        preparation["generation_triggered"],
        preparation["generation_status"],
        preparation["skip_reason"],
    )
    (result_dir / "episode_preparation.json").write_text(
        json.dumps(preparation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return preparation


def _build_agent_options(args, config) -> dict[str, Any]:
    """Capture run-affecting plugin options for reproducible QA reports."""
    options: dict[str, Any] = {
        "agent_plugin": getattr(args, "agent_plugin", ""),
        "qa_profile": getattr(args, "qa_profile", None) or "",
        "tool_calling": bool(
            getattr(args, "tools", getattr(args, "tool_calling", True))
        ),
        "initial_retrieval_protocol": "mcp",
        "search_in_tools": bool(getattr(args, "search_in_tools", False)),
        "top_k": config.top_k,
        "memory_budget_chars": config.memory_budget_chars,
        "question_timeout_s": config.question_timeout_s,
        "llm_temperature": config.llm_temperature,
        "llm_timeout_s": config.llm_timeout_s,
        "llm_retries": config.llm_retries,
        "qa_concurrency": config.concurrency,
        "judge_concurrency": getattr(args, "judge_concurrency", None),
    }
    for name in (
        "mcp_url",
        "mcp_max_iterations",
        "mcp_read_mode",
        "user_memory_budget_chars",
        "agent_memory_budget_chars",
    ):
        if hasattr(args, name):
            options[name] = getattr(args, name)
    for name in ("mcp_auth_key", "echomem_auth_key"):
        if hasattr(args, name):
            value = getattr(args, name)
            options[f"{name}_configured"] = bool(value)
            options[f"{name}_redacted"] = _redact_secret(value)
    return options


def _write_agent_options_to_config(result_dir: Path, options: dict[str, Any]) -> None:
    config_path = result_dir / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["agent_options"] = options
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoCoMo benchmark evaluation")
    parser.add_argument("--dataset", default="", help="LoCoMo JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample_id)")
    parser.add_argument("--questions", type=int, default=0, help="限制 QA 数量 (0=all)")
    parser.add_argument(
        "--question-ids",
        default="",
        help="Comma-separated LoCoMo question ids; applied before --questions",
    )
    parser.add_argument(
        "--session-mode",
        choices=["auto", "locomo", "single"],
        default="auto",
        help="auto=单 sample 按原始 session, 多 sample 各自合并; locomo=原始 session; single=合并",
    )
    parser.add_argument("--max-sessions", type=int, default=0, help="每个 sample 最多导入多少个原始 session (0=全部)")
    # 共享参数
    add_agent_plugin_args(parser, default_plugin="vikingbot")
    add_eval_args(parser)
    qa = parser.add_argument_group("LoCoMo QA")
    qa.add_argument(
        "--qa-profile",
        choices=[
            VIKINGBOAT_0411_PROFILE,
            VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
        ],
        default=None,
        help=(
            "LoCoMo QA executor; vikingboat0411 adapts the v0.4.11 "
            "VikingBot agent behavior to EchoMemory tools; "
            "vikingboat0411-natural-no-tools keeps only complete initially "
            "retrieved memory excerpts"
        ),
    )
    qa.add_argument(
        "--qa-prompt-file",
        default="",
        help=(
            "Append a local UTF-8 text file to the selected profile's system "
            "prompt; the file content is not copied into repository metadata"
        ),
    )
    qa.add_argument(
        "--checkpoint-interval",
        type=int,
        default=10,
        help="Persist partial QA CSV after every N completed questions (0=off)",
    )
    qa.add_argument(
        "--resume",
        default="",
        help=(
            "Resume a prior locomo run directory or qa_results CSV: reuse the "
            "prior identity, skip already-injected import batches, reuse "
            "healthy QA answers, and reuse judge verdicts; only run the "
            "missing/unhealthy remainder. Metrics (tokens/latency/accuracy) "
            "are computed over the merged whole run."
        ),
    )
    qa.add_argument(
        "--resume-qa",
        default="",
        help=(
            "Resume QA from a prior LoCoMo run directory or qa_results CSV; "
            "reuses the prior identity and skips already-injected sessions "
            "(superseded by --resume)"
        ),
    )
    qa.add_argument(
        "--reuse-memory-from",
        default="",
        help=(
            "Reuse the identity and completed memory imports from a prior run, "
            "but execute a fresh QA/Judge pass with the current MCP mode "
            "(superseded by --resume)"
        ),
    )
    # judge 参数 (三个基础参数由共享 helper 声明, locomo 额外参数在此声明)
    add_judge_args(parser)
    g = parser.add_argument_group("Judge")
    g.add_argument(
        "--judge-concurrency",
        type=int,
        default=int(os.getenv("JUDGE_CONCURRENCY", "4")),
        help="Maximum concurrent Judge requests",
    )
    g.add_argument(
        "--judge-checkpoint-interval",
        type=int,
        default=10,
        help="Persist partial Judge CSV after every N completed questions (0=off)",
    )
    g.add_argument(
        "--resume-judge",
        default="",
        help=(
            "Resume matching Judge rows from a prior LoCoMo run directory "
            "or judge_results CSV (superseded by --resume)"
        ),
    )
    return parser


def load_qa_prompt_append(path_value: str) -> tuple[str, str, str]:
    value = str(path_value or "").strip()
    if not value:
        return "", "", ""
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"QA prompt file does not exist: {path}")
    prompt = path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"QA prompt file is empty: {path}")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, digest, path.name


def _load_prior_import_rows(resume_source: str) -> list[dict]:
    """Load import_results.csv from a prior run directory for resume."""
    import csv

    source = Path(resume_source)
    csv_path = (
        source / "import_results.csv"
        if source.is_dir()
        else source.parent / "import_results.csv"
    )
    if not csv_path.is_file():
        return []
    with csv_path.open("r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    args = build_parser().parse_args()
    config = build_config_from_args(args)
    (
        system_prompt_append,
        system_prompt_append_sha256,
        system_prompt_append_source,
    ) = load_qa_prompt_append(args.qa_prompt_file)
    config.sample_filter = args.sample
    config.question_limit = args.questions
    validate_eval_config(config)
    if getattr(args, "max_sessions", 0) < 0:
        raise ValueError("max sessions must be >= 0")
    if args.checkpoint_interval < 0:
        raise ValueError("checkpoint interval must be >= 0")
    if args.judge_concurrency < 1:
        raise ValueError("judge concurrency must be >= 1")
    if args.judge_checkpoint_interval < 0:
        raise ValueError("judge checkpoint interval must be >= 0")
    dataset_path = resolve_dataset_path("locomo", args.dataset)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)

    agent_options = _build_agent_options(args, config)

    # 创建评测运行
    run = EvalRun(
        benchmark_name="locomo",
        results_root=results_root_for(Path(__file__).parent, args.out_dir),
        config=config,
    )
    _write_agent_options_to_config(run.result_dir, agent_options)
    log = run.logger

    # 加载数据集
    log.info("加载 LoCoMo 数据集: %s", dataset_path)
    jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个 sample, %d 个 QA 问题", len(plans), len(jobs))
    session_mode = resolve_session_mode(args.session_mode, len(plans))
    log.info("LoCoMo session mode: %s", session_mode)

    jobs = select_questions(
        jobs,
        question_ids=question_ids,
        limit=config.question_limit,
    )
    if question_ids:
        log.info("按 question id 选择 %d 题", len(jobs))
    elif config.question_limit > 0:
        log.info("限制 QA 数量为 %d", len(jobs))
    if not plans or not jobs:
        message = "dataset/sample filter produced no LoCoMo samples or questions"
        run.save_summary({
            "status": "failed",
            "phase": "dataset",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "error": message,
        })
        raise ValueError(message)

    # 加载 agent 插件 (在记忆操作之前, setup 内部创建 memory_client)
    agent_config = {**vars(args), "benchmark_name": "locomo", "run_id": run.result_dir.name}
    # 统一 --resume 与 --resume-qa 都跳过身份隔离（插件读 config["resume_qa"]）
    agent_config["resume_qa"] = (
        args.resume or args.resume_qa or args.reuse_memory_from
    )
    agent_plugin = load_agent_plugin(args.agent_plugin, agent_config)
    agent_options["qa_profile"] = agent_plugin.qa_profile
    _write_agent_options_to_config(run.result_dir, agent_options)
    echomem = agent_plugin.memory_client
    raw_echomem = echomem
    echomem.health()
    memory_reuse_source = args.resume or args.resume_qa or args.reuse_memory_from
    if memory_reuse_source:
        apply_resume_memory_identity(echomem, memory_reuse_source, log)
    evaluation_identity = {
        "mode": (
            "resumed"
            if (args.resume or args.resume_qa)
            else "reused"
            if args.reuse_memory_from
            else "fresh"
        ),
        "tenant_id": echomem.account,
        "user_id": echomem.user_id,
        "auth_key": echomem.auth_key,
    }
    log.info(
        "Memory identity: %s tenant=%s user=%s",
        evaluation_identity.get("mode", "none"),
        evaluation_identity.get("tenant_id", ""),
        evaluation_identity.get("user_id", ""),
    )
    memory_session_prefix = ""
    if memory_reuse_source:
        sample = str(args.sample or "").strip()
        if re.fullmatch(r"conv-\d+", sample):
            memory_session_prefix = f"echomem-locomo-{sample}-"
    if memory_session_prefix and not args.reuse_memory_from:
        echomem = SessionPrefixMemoryClient(
            echomem,
            memory_session_prefix,
        )
        log.info(
            "Memory session scope: prefix=%s",
            memory_session_prefix,
        )

    # 尽早写 resume manifest（含身份）：即使导入中断，目录也留有身份供后续 --resume 复用。
    qa_options = QAOptions(
        profile=agent_plugin.qa_profile,
        checkpoint_interval=args.checkpoint_interval,
        top_k=config.top_k,
        memory_budget_chars=config.memory_budget_chars,
        tools_enabled=bool(
            getattr(args, "tools", getattr(args, "tool_calling", True))
        ),
        system_prompt_append=system_prompt_append,
        system_prompt_append_sha256=system_prompt_append_sha256,
        system_prompt_append_source=system_prompt_append_source,
        agent_options=agent_options,
    )
    qa_resume_manifest = build_qa_resume_manifest(
        dataset_path=dataset_path,
        sample_filter=args.sample,
        session_mode=session_mode,
        config=config,
        options=qa_options,
        memory_identity={
            "account": echomem.account,
            "user_id": echomem.user_id,
            "agent_id": echomem.agent_id,
            "auth_key": echomem.auth_key,
        },
    )
    write_qa_resume_manifest(run.result_dir, qa_resume_manifest)

    # -- 阶段 1: 导入记忆 --
    log.info("=" * 60)
    prior_import_rows = (
        _load_prior_import_rows(memory_reuse_source)
        if memory_reuse_source
        else None
    )
    if prior_import_rows is not None:
        log.info("阶段 1: 导入记忆 (resume, 跳过已完成 batches)")
    else:
        log.info("阶段 1: 导入记忆 (共 %d 个 sample)", len(plans))

    import_report = import_locomo_memory(
        plans,
        echomem,
        config,
        ImportOptions(
            session_mode=session_mode,
            max_sessions=args.max_sessions,
            resume_qa=bool(memory_reuse_source),
            sample_filter=args.sample,
            prior_import_rows=prior_import_rows,
        ),
        run.result_dir,
        log,
    )
    log.info(
        "导入完成: %d/%d 成功",
        import_report.completed,
        import_report.total,
    )
    try:
        require_complete_imports(
            import_report.rows,
            allow_incomplete=args.allow_diagnostics,
        )
    except RuntimeError as exc:
        run.save_summary({
            "status": "failed",
            "phase": "import",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "import_ok": import_report.completed,
            "import_total": import_report.total,
            "error": str(exc),
        })
        log.error("%s", exc)
        raise SystemExit(2) from exc

    memory_provenance = inspect_memory_provenance(
        raw_echomem,
        dataset_path=dataset_path,
        plans=plans,
        session_mode=session_mode,
        max_sessions=args.max_sessions,
    )
    memory_provenance["session_prefix"] = memory_session_prefix
    provenance_path = write_memory_provenance(
        run.result_dir,
        memory_provenance,
    )
    log.info(
        "Memory provenance: status=%s sessions=%d/%d artifact=%s",
        memory_provenance["status"],
        memory_provenance["actual_session_count"],
        memory_provenance["expected_session_count"],
        provenance_path,
    )
    if (
        memory_provenance["status"] != "matched"
        and not args.allow_diagnostics
    ):
        message = (
            "EchoMemory provenance mismatch: expected "
            f"{memory_provenance['expected_session_count']} sessions but found "
            f"{memory_provenance['actual_session_count']}; use "
            "--allow-diagnostics only for diagnostics"
        )
        run.save_summary({
            "status": "failed",
            "phase": "memory_provenance",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "memory_provenance": memory_provenance,
            "error": message,
        })
        log.error("%s", message)
        raise SystemExit(2)

    # -- 阶段 1.5: Episode preparation --
    log.info("=" * 60)
    log.info("阶段 1.5: Episode preparation")
    try:
        episode_preparation = prepare_episode_recall(
            echomem,
            run.result_dir,
            log,
        )
    except EpisodePreparationError as exc:
        run.save_summary({
            "status": "failed",
            "phase": "episode_preparation",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "import_ok": import_report.completed,
            "import_total": import_report.total,
            "episode_preparation": exc.preparation,
            "error": str(exc),
        })
        log.error("%s", exc)
        raise SystemExit(2) from exc

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_tasks = build_qa_tasks(
        jobs,
        import_report.sample_to_session_ids,
        config,
        qa_options,
        agent_id=echomem.agent_id,
    )
    qa_resume_state = None
    if args.resume or args.resume_qa:
        qa_resume_source = args.resume or args.resume_qa
        prior_qa_csv = find_qa_resume_csv(qa_resume_source)
        if prior_qa_csv is None:
            log.info(
                "QA resume: no prior QA results under %s (import-only run), "
                "running full QA",
                qa_resume_source,
            )
        else:
            qa_resume_state = load_qa_resume_state(
                qa_resume_source,
                tasks=qa_tasks,
                expected_manifest=qa_resume_manifest,
            )
            copied_traces = copy_resume_traces(
                qa_resume_state,
                run.result_dir,
            )
            log.info(
                "QA resume: source=%s reused=%d discarded=%d traces=%d",
                qa_resume_state.source_csv,
                len(qa_resume_state.results),
                len(qa_resume_state.discarded_question_ids),
                copied_traces,
            )
    qa_results = run_locomo_qa(
        qa_tasks,
        agent_plugin,
        config,
        qa_options,
        run.result_dir,
        log,
        existing_results=(
            qa_resume_state.results if qa_resume_state else None
        ),
    )
    if qa_resume_state:
        restored = restore_resume_traces(qa_results, run.result_dir)
        log.info("QA resume: restored %d traces from source run", restored)
        # 全量写 tool audits：让 resume 目录与从 0 运行的目录等价（含复用题的审计）
        write_tool_audits(run.result_dir, qa_results)

    # -- 阶段 3: LLM Judge --
    log.info("=" * 60)
    log.info("阶段 3: Judge (共 %d 题)", len(qa_results))

    judge_llm = LLMClient(
        base_url=args.judge_base_url or config.llm_base_url,
        api_key=args.judge_api_key or config.llm_api_key,
        model=args.judge_model or config.llm_model,
        temperature=0.0,
        max_tokens=512,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )
    judge_resume_manifest = build_judge_resume_manifest(
        base_url=judge_llm.base_url,
        model=judge_llm.model,
        system_prompt=LOCOMO_JUDGE_SYSTEM,
        prompt_template=LOCOMO_JUDGE_TEMPLATE,
    )
    write_judge_resume_manifest(
        run.result_dir,
        judge_resume_manifest,
    )
    judge_resume_state = None
    if args.resume or args.resume_judge:
        judge_resume_source = args.resume or args.resume_judge
        prior_judge_csv = find_judge_resume_csv(judge_resume_source)
        if prior_judge_csv is None:
            log.info(
                "Judge resume: no prior judge results under %s, running full judge",
                judge_resume_source,
            )
        else:
            judge_resume_state = load_judge_resume_state(
                judge_resume_source,
                expected_manifest=judge_resume_manifest,
            )
            log.info(
                "Judge resume: source=%s candidate_rows=%d",
                judge_resume_state.source_csv,
                len(judge_resume_state.rows),
            )

    judge_report = judge_locomo_results(
        qa_results,
        judge_llm,
        run.result_dir,
        log,
        concurrency=args.judge_concurrency,
        checkpoint_interval=args.judge_checkpoint_interval,
        existing_rows=(
            judge_resume_state.rows if judge_resume_state else None
        ),
    )
    log.info(
        "Judge 完成: %d CORRECT, %d WRONG, accuracy=%.2f%%",
        judge_report.correct,
        judge_report.wrong,
        judge_report.accuracy * 100,
    )
    diagnosis = diagnose_run(
        run.result_dir / "qa_results.csv",
        run.result_dir / "judge_results.csv",
        Path(dataset_path),
        args.sample,
        run.result_dir,
    )
    log.info(
        "诊断完成: failures=%d retryable=%d",
        diagnosis["failed"],
        len(diagnosis["retryable_question_ids"]),
    )

    # 收集 agent/记忆后端日志
    log_json = agent_plugin.getlog()
    (run.result_dir / "backend_logs.json").write_text(log_json, encoding="utf-8")

    # 保存 summary
    summary = build_summary(
        dataset_path=dataset_path,
        sample_filter=args.sample,
        total_samples=len(plans),
        total_questions=len(jobs),
        import_report=import_report,
        resume_qa=bool(memory_reuse_source),
        qa_results=qa_results,
        judge_report=judge_report,
        qa_options=qa_options,
        session_mode=session_mode,
        evaluation_identity=evaluation_identity,
        episode_preparation=episode_preparation,
    )
    summary["diagnosis"] = {
        "path": str(run.result_dir / "diagnosis.json"),
        "retrieval_traces": str(run.result_dir / "retrieval_traces.jsonl"),
        "retrieval_coverage": diagnosis["retrieval_coverage"],
        "failure_breakdown": diagnosis["failure_breakdown"],
        "retryable_question_ids": diagnosis["retryable_question_ids"],
        "missing_question_ids": diagnosis["missing_question_ids"],
    }
    summary["qa_parallelism"] = config.concurrency
    summary["resume"] = {
        "enabled": bool(memory_reuse_source),
        "source": str(memory_reuse_source or ""),
        "mode": evaluation_identity.get("mode"),
        "reused_qa": (
            len(qa_resume_state.results) if qa_resume_state else 0
        ),
        "discarded_qa": (
            qa_resume_state.discarded_question_ids
            if qa_resume_state
            else []
        ),
        "reused_import_batches": sum(
            1
            for row in import_report.rows
            if str(row.get("status") or "").strip().lower() == "reused"
        ),
        "reused_judge_rows": (
            len(judge_resume_state.rows) if judge_resume_state else 0
        ),
    }
    summary["qa_resume"] = {
        "enabled": bool(qa_resume_state),
        "source": (
            str(qa_resume_state.source_csv) if qa_resume_state else ""
        ),
        "reused": (
            len(qa_resume_state.results) if qa_resume_state else 0
        ),
        "discarded": (
            qa_resume_state.discarded_question_ids
            if qa_resume_state
            else []
        ),
    }
    summary["memory_reuse"] = {
        "enabled": bool(args.reuse_memory_from),
        "source": str(args.reuse_memory_from or ""),
    }
    summary["judge_parallelism"] = args.judge_concurrency
    summary["judge_checkpoint_interval"] = args.judge_checkpoint_interval
    summary["judge_resume"] = {
        "enabled": bool(judge_resume_state),
        "source": (
            str(judge_resume_state.source_csv)
            if judge_resume_state
            else ""
        ),
        "candidate_rows": (
            len(judge_resume_state.rows) if judge_resume_state else 0
        ),
    }
    summary["memory_provenance"] = {
        **memory_provenance,
        "artifact_path": str(provenance_path),
    }
    summary["run_started_at"] = run.started_at.isoformat()
    summary["run_finished_at"] = run.finished_at_iso()
    if qa_resume_state:
        # 续跑延续源运行的原始启动时间：批次耗时/吞吐按「原启动 → 本次结束」计算。
        source_summary_path = Path(qa_resume_state.source_csv).parent / "summary.json"
        if source_summary_path.is_file():
            try:
                with open(source_summary_path, encoding="utf-8") as f:
                    source_summary = json.load(f)
            except (OSError, ValueError) as exc:
                log.warning("读取续跑源 summary 失败: %s", exc)
                source_summary = {}
            source_started_at = source_summary.get("run_started_at")
            if source_started_at:
                summary["qa_resume"]["original_started_at"] = source_started_at
                summary["run_started_at"] = source_started_at
    blackbox = write_blackbox_artifacts(
        qa_rows=[result.to_csv_row() for result in qa_results],
        judge_rows=judge_report.rows,
        import_rows=import_report.rows,
        run_observation={
            "qa_parallelism": config.concurrency,
            "run_started_at": summary["run_started_at"],
            "run_finished_at": summary["run_finished_at"],
        },
        output_dir=run.result_dir,
    )
    summary["strict_blackbox"] = blackbox
    summary["strict_blackbox_metrics_path"] = blackbox["artifact_path"]
    summary["strict_blackbox_report_path"] = blackbox["report_path"]
    run.save_summary(summary)

    if summary["status"] != "completed":
        log.error("评测包含运行错误，结果不能作为正式分数")
        raise SystemExit(2)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info(
        "Accuracy: %.2f%% (%d/%d)",
        judge_report.accuracy * 100,
        judge_report.correct,
        len(judge_report.rows),
    )
    agent_plugin.teardown()


if __name__ == "__main__":
    main()
