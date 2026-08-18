#!/usr/bin/env python3
"""HotpotQA benchmark evaluation script.

流程:
  1. 导入记忆 (两种模式):
     - per_question (默认): 每题各自导入自己的 context passages
     - global: 所有题的 passages 合并导入一个共享 session
  2. 逐题 QA: search EchoMem -> build prompt -> LLM answer
  3. 官方 F1/EM 评测 (无需 LLM judge)

用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins import load_agent_plugin
from benchmarks.hotpotqa.dataset import load_dataset
from benchmarks.hotpotqa.diagnosis import diagnose_run
from benchmarks.hotpotqa.evaluate import evaluate_hotpotqa, load_references
from benchmarks.hotpotqa.import_memory import import_hotpotqa_memory
from benchmarks.hotpotqa.qa import (
    build_qa_tasks,
    run_hotpotqa_qa,
    write_tool_audits,
)
from benchmarks.hotpotqa.reporting import build_summary
from benchmarks.hotpotqa.resume import (
    build_resume_manifest,
    copy_resume_traces,
    load_resume_manifest,
    restore_resume_traces,
    validate_resume_manifest,
    write_resume_manifest,
)
from benchmarks.hotpotqa.selection import (
    parse_question_ids,
    select_jobs_and_plans,
)
from shared.dataset_io import resolve_dataset_path
from shared.eval_base import (
    EvalRun,
    add_agent_plugin_args,
    add_eval_args,
    build_config_from_args,
    results_root_for,
    validate_eval_config,
)
from shared.import_guard import require_complete_imports
from shared.resume_identity import apply_resume_memory_identity
from shared.resume_qa import (
    find_qa_resume_csv,
    load_prior_import_rows,
    load_resume_qa_results,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HotpotQA benchmark evaluation")
    parser.add_argument("--dataset", default="", help="HotpotQA JSON 数据集路径 (不指定则自动查找或下载)")
    parser.add_argument("--sample", default="all", help="筛选 sample (all 或 sample index/id)")
    parser.add_argument("--questions", type=int, default=0, help="限制 QA 数量 (0=all)")
    parser.add_argument(
        "--question-ids",
        default="",
        help="Comma-separated question/native/sample ids",
    )
    parser.add_argument("--import-mode", default="per_question",
                        choices=["per_question", "global", "documents"],
                        help="导入模式: per_question=每题各自导入; global=合并共享 session; documents=文档资源语料(RAG)")
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=10,
        help="Persist partial QA CSV after every N completed questions (0=off)",
    )
    parser.add_argument(
        "--resume",
        default="",
        help=(
            "Resume a prior hotpotqa run directory or qa_results CSV: reuse "
            "the prior identity, skip already-completed import batches, and "
            "reuse healthy QA answers; only run the missing/unhealthy "
            "remainder. Metrics (tokens/latency/F1) are computed over the "
            "merged whole run."
        ),
    )
    parser.add_argument(
        "--reuse-memory-from",
        default="",
        help=(
            "Reuse the identity and completed memory imports from a prior "
            "run, but execute a fresh QA pass (superseded by --resume)"
        ),
    )
    add_agent_plugin_args(parser, default_plugin="vikingbot")
    add_eval_args(parser)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = build_config_from_args(args)
    config.sample_filter = args.sample
    config.question_limit = args.questions
    validate_eval_config(config)
    dataset_path = resolve_dataset_path("hotpotqa", args.dataset)
    config.dataset_path = dataset_path
    question_ids = parse_question_ids(args.question_ids)

    run = EvalRun(
        benchmark_name="hotpotqa",
        results_root=results_root_for(Path(__file__).parent, args.out_dir),
        config=config,
    )
    log = run.logger

    # 加载数据集
    log.info("加载 HotpotQA 数据集: %s", dataset_path)
    jobs, plans = load_dataset(dataset_path, sample_filter=args.sample)
    log.info("共 %d 个问题", len(jobs))

    jobs, plans = select_jobs_and_plans(
        jobs,
        plans,
        question_ids=question_ids,
        limit=config.question_limit,
    )
    if question_ids:
        log.info("按 question id 选择 %d 题", len(jobs))
    elif config.question_limit > 0:
        log.info("限制 QA 数量为 %d", len(jobs))
    if not jobs or not plans:
        message = "dataset/sample filter produced no HotpotQA questions"
        run.save_summary({
            "status": "failed",
            "phase": "dataset",
            "dataset": dataset_path,
            "sample_filter": args.sample,
            "error": message,
        })
        raise ValueError(message)

    # 加载 agent 插件 (在记忆操作之前, setup 内部创建 memory_client)
    agent_config = {**vars(args), "benchmark_name": "hotpotqa", "run_id": run.result_dir.name}
    # 统一 --resume / --reuse-memory-from 跳过身份隔离（插件读 config["resume_qa"]）
    reuse_source = args.resume or args.reuse_memory_from
    agent_config["resume_qa"] = reuse_source
    agent_plugin = load_agent_plugin(args.agent_plugin, agent_config)
    echomem = agent_plugin.memory_client
    echomem.health()
    if reuse_source:
        apply_resume_memory_identity(echomem, reuse_source, log)
    evaluation_identity = {
        "mode": (
            "resumed"
            if args.resume
            else "reused"
            if args.reuse_memory_from
            else "fresh"
        ),
        "tenant_id": echomem.account,
        "user_id": echomem.user_id,
    }
    log.info(
        "Memory identity: %s tenant=%s user=%s",
        evaluation_identity.get("mode", "none"),
        evaluation_identity.get("tenant_id", ""),
        evaluation_identity.get("user_id", ""),
    )

    # 尽早写 resume manifest（含身份）：即使导入中断，目录也留有身份供后续 --resume 复用。
    resume_manifest = build_resume_manifest(
        dataset_path=dataset_path,
        import_mode=args.import_mode,
        config=config,
        memory_identity={
            "account": echomem.account,
            "user_id": echomem.user_id,
            "auth_key": echomem.auth_key,
        },
    )
    write_resume_manifest(run.result_dir, resume_manifest)

    # -- 阶段 1: 导入记忆 --
    log.info("=" * 60)
    log.info("阶段 1: 导入记忆 (模式=%s)", args.import_mode)
    prior_import_rows = (
        load_prior_import_rows(reuse_source) if reuse_source else None
    )
    if prior_import_rows is not None:
        log.info("阶段 1: 导入记忆 (resume, 跳过已完成 batches)")
    import_report = import_hotpotqa_memory(
        jobs,
        plans,
        echomem,
        config,
        run.result_dir,
        log,
        import_mode=args.import_mode,
        prior_import_rows=prior_import_rows,
        reuse_memory=bool(args.reuse_memory_from),
    )
    log.info(
        "导入完成: %d/%d 成功",
        import_report.completed,
        import_report.total,
    )
    if args.import_mode == "documents":
        if not hasattr(agent_plugin, "path_title_map"):
            log.error(
                "documents 模式需要支持文档资源检索的插件（提供 path_title_map，"
                "如 vikingbot 或 echomem_mcp）；当前插件 %s 不支持",
                args.agent_plugin,
            )
            raise SystemExit(2)
        agent_plugin.path_title_map = import_report.document_path_titles
        log.info(
            "文档语料注入完成: %d 篇唯一文档, path→title 映射 %d 条",
            import_report.rows[0].get("messages", 0) if import_report.rows else 0,
            len(import_report.document_path_titles),
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
            "import_ok": import_report.completed,
            "import_total": import_report.total,
            "error": str(exc),
        })
        log.error("%s", exc)
        raise SystemExit(2) from exc

    # -- 阶段 2: 逐题 QA --
    log.info("=" * 60)
    log.info("阶段 2: QA (共 %d 题, 并发=%d)", len(jobs), config.concurrency)

    qa_tasks = build_qa_tasks(
        jobs,
        import_report.question_to_session,
        config,
        agent_id=echomem.agent_id,
    )
    qa_resume_state = None
    if args.resume:
        prior_qa_csv = find_qa_resume_csv(args.resume)
        if prior_qa_csv is None:
            log.info(
                "QA resume: no prior QA results under %s (import-only run), "
                "running full QA",
                args.resume,
            )
        else:
            source_dir = Path(args.resume).expanduser().resolve()
            validate_resume_manifest(
                resume_manifest,
                load_resume_manifest(
                    source_dir if source_dir.is_dir() else source_dir.parent
                ),
            )
            qa_resume_state = load_resume_qa_results(args.resume, qa_tasks)
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
    qa_results = run_hotpotqa_qa(
        qa_tasks,
        agent_plugin,
        config,
        run.result_dir,
        log,
        existing_results=(
            qa_resume_state.results if qa_resume_state else None
        ),
        checkpoint_interval=args.checkpoint_interval,
    )
    if qa_resume_state:
        restored = restore_resume_traces(qa_results, run.result_dir)
        log.info("QA resume: restored %d traces from source run", restored)
        # 全量写 tool audits：让 resume 目录与从 0 运行的目录等价（含复用题的审计）
        write_tool_audits(run.result_dir, qa_results)

    # -- 阶段 3: 官方 answer/supporting-fact/joint 评测 --
    log.info("=" * 60)
    log.info("阶段 3: HotpotQA 官方指标")
    references = load_references(Path(dataset_path))
    evaluation_report = evaluate_hotpotqa(
        qa_results,
        references,
        run.result_dir,
    )
    log.info(
        "评测完成: answer F1=%.4f EM=%.4f, support F1=%.4f EM=%.4f, "
        "joint F1=%.4f EM=%.4f",
        evaluation_report.answer_f1,
        evaluation_report.answer_em,
        evaluation_report.supporting_facts_f1,
        evaluation_report.supporting_facts_em,
        evaluation_report.joint_f1,
        evaluation_report.joint_em,
    )

    diagnosis = diagnose_run(
        run.result_dir / "qa_results.csv",
        run.result_dir / "eval_results.csv",
        Path(dataset_path),
        args.sample,
        config.question_limit,
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

    summary = build_summary(
        dataset_path=dataset_path,
        import_mode=args.import_mode,
        jobs=jobs,
        import_report=import_report,
        qa_results=qa_results,
        evaluation_report=evaluation_report,
        evaluation_identity=evaluation_identity,
        resumed=bool(reuse_source),
    )
    summary["memory_reuse"] = {
        "enabled": bool(args.reuse_memory_from),
        "source": str(args.reuse_memory_from or ""),
    }
    summary["resume"] = {
        "enabled": bool(args.resume),
        "source": str(args.resume or ""),
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
    }
    if args.resume:
        # 续跑延续源运行的原始启动时间：批次耗时/吞吐按「原启动 → 本次结束」计算。
        source_summary_path = Path(args.resume) / "summary.json"
        if source_summary_path.is_file():
            try:
                with open(source_summary_path, encoding="utf-8") as f:
                    source_summary = json.load(f)
            except (OSError, ValueError) as exc:
                log.warning("读取续跑源 summary 失败: %s", exc)
                source_summary = {}
            source_started_at = source_summary.get("run_started_at")
            if source_started_at:
                summary["resume"]["original_started_at"] = source_started_at
                summary["run_started_at"] = source_started_at
    summary["diagnosis"] = {
        "path": str(run.result_dir / "diagnosis.json"),
        "retrieval_traces": str(run.result_dir / "retrieval_traces.jsonl"),
        "retrieval_coverage": diagnosis["retrieval_coverage"],
        "failure_breakdown": diagnosis["failure_breakdown"],
        "retryable_question_ids": diagnosis["retryable_question_ids"],
        "missing_question_ids": diagnosis["missing_question_ids"],
    }
    run.save_summary(summary)

    if summary["status"] != "completed":
        log.error("评测包含运行错误，结果不能作为正式分数")
        raise SystemExit(2)

    log.info("=" * 60)
    log.info("评测完成! 结果目录: %s", run.result_dir)
    log.info(
        "answer_F1=%.4f answer_EM=%.4f joint_F1=%.4f joint_EM=%.4f "
        "(%d questions)",
        evaluation_report.answer_f1,
        evaluation_report.answer_em,
        evaluation_report.joint_f1,
        evaluation_report.joint_em,
        len(qa_results),
    )
    agent_plugin.teardown()


if __name__ == "__main__":
    main()
