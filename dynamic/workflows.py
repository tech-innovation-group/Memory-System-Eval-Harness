"""Generate and replay workflows for dynamic agent evaluation."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from tqdm import tqdm

from benchmarks.locomo.dataset import load_dataset
from dynamic.artifacts import save_results
from dynamic.metrics import collect_round_metrics, load_evaluator_config
from dynamic.simulator import MemoryDynamicEvaluator
from plugins.base import AgentPlugin
from shared.eval_base import EvalRun
from shared.llm_client import LLMClient


def _failed_round(
    round_data: dict[str, Any],
    prefetch_committed: bool,
    error: Exception,
) -> dict[str, Any]:
    query = str(round_data.get("query") or "")
    return {
        "round_id": round_data.get("id", ""),
        "query": query,
        "reply": "",
        "reply_length": 0,
        "query_length": len(query),
        "ttft_ms": None,
        "cached_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "prefetch_committed": prefetch_committed,
        "elapsed_s": None,
        "retrieval_latency_s": None,
        "llm_latency_s": None,
        "tool_call_count": 0,
        "iterations": 1,
        "is_new_session": bool(round_data.get("new_session")),
        "is_injection": False,
        "complexity": round_data.get("complexity", ""),
        "ground_facts": round_data.get("ground_facts", []),
        "error": str(error),
        "relevant_memory": "[]",
    }


def _ask_agent(
    args,
    agent_plugin: AgentPlugin,
    session_id: str,
    round_data: dict[str, Any],
) -> dict[str, Any]:
    """Send a query through the agent plugin and collect metrics."""
    context_path = "/"
    query = str(round_data.get("query") or "")
    prefetch_committed = False
    memory_items: list[dict[str, Any]] = []

    if agent_plugin.supports_typing_simulation and len(query) > 2:
        typing_result = agent_plugin.simulate_typing(
            session_id,
            context_path,
            query,
            args.typing_speed_ms,
            args.typing_jitter_ms,
        )
        if typing_result is not None:
            prefetch_committed = typing_result.committed
            memory_items = typing_result.memory_items

    try:
        response = agent_plugin.send_message(session_id, query, context_path)
    except Exception as exc:
        return _failed_round(round_data, prefetch_committed, exc)

    # If typing simulation didn't produce memory_items, fall back to
    # response.memory_items (e.g. vikingbot, echomem_mcp, openviking_mcp)
    if not memory_items:
        memory_items = response.memory_items or []

    return collect_round_metrics(round_data, response, memory_items)


def run_generate_mode(
    args,
    run: EvalRun,
    agent_plugin: AgentPlugin,
    llm: LLMClient,
) -> None:
    log = run.logger
    log.info("模式: generate (LLM 生成场景)")
    evaluator_config_dict = load_evaluator_config(args.evaluator_config)
    evaluator_config = {
        "num_memories": args.num_memories,
        "llm_config": {
            "model": args.scenario_model,
            "base_url": args.scenario_base_url,
            "api_key": args.scenario_api_key,
        },
    }
    if args.user_simulator_config:
        simulator_path = Path(args.user_simulator_config)
        if simulator_path.is_file():
            evaluator_config["user_simulator_config_yaml"] = (
                simulator_path.read_text(encoding="utf-8")
            )
        else:
            evaluator_config["user_simulator_config"] = (
                args.user_simulator_config
            )
    evaluator_path = Path(args.evaluator_config)
    if evaluator_path.is_file():
        evaluator_config["evaluator_config_yaml"] = evaluator_path.read_text(
            encoding="utf-8"
        )
    evaluator = MemoryDynamicEvaluator(evaluator_config)
    memories = evaluator.generate_background_memories().get("memories", [])
    facts = {
        str(fact.get("id") or ""): str(fact.get("text") or "")
        for fact in memories
        if fact.get("id") and fact.get("text")
    }
    log.info("theme=%s memories=%d", evaluator.theme, len(memories))

    backend = getattr(args, "memory_backend", "echomem")
    inject_start = time.monotonic()
    inject_session_id = agent_plugin.inject_memories(memories, backend=backend)
    inject_elapsed = time.monotonic() - inject_start
    print(f"[inject] {len(memories)} memories injected in {inject_elapsed:.1f}s (session={inject_session_id})")
    log.info("memory injection completed: %d memories in %.1fs", len(memories), inject_elapsed)

    rounds: list[dict[str, Any]] = []
    dataset_queries: list[dict[str, Any]] = []
    previous_queries: list[str] = []
    previous_replies: list[str] = []
    session_id = ""
    session_count = 0
    for round_index in tqdm(range(args.num_queries), desc="提问", unit="q"):
        generated = evaluator.generate_next_query({
            "round_index": round_index,
            "previous_queries": previous_queries,
            "previous_replies": previous_replies,
            "is_new_session": not session_id,
        })
        query = str(generated.get("query") or "")
        if not query:
            continue
        round_data = {
            "id": f"r{round_index}",
            "query": query,
            "ground_facts": generated.get("ground_facts", []),
            "new_session": generated.get("new_session_hint", False),
            "complexity": generated.get("complexity", "simple"),
            "is_injection": False,
        }
        dataset_queries.append({
            "query": query,
            "ground_facts": round_data["ground_facts"],
            "complexity": round_data["complexity"],
            "reasoning": generated.get("reasoning", ""),
            "new_session_hint": round_data["new_session"],
        })
        if (
            not session_id
            or round_data["new_session"]
            and random.random() < args.new_session_ratio
        ):
            session_count += 1
            session_id = agent_plugin.create_session(
                title=f"test-{evaluator.theme}-s{session_count}",
            )
        metrics = _ask_agent(args, agent_plugin, session_id, round_data)
        metrics["session_id"] = session_id
        rounds.append(metrics)
        previous_queries.append(query)
        previous_replies.append(str(metrics.get("reply") or ""))
        log.info(
            "Q[%d] ttft=%sms cached=%d reply_len=%d",
            round_index + 1,
            metrics["ttft_ms"],
            metrics["cached_tokens"],
            metrics["reply_length"],
        )

    save_results(
        run,
        rounds,
        facts,
        llm,
        {
            "mode": "generate",
            "num_memories": args.num_memories,
            "num_queries": args.num_queries,
            "agent_plugin": args.agent_plugin,
            "evaluator_config": args.evaluator_config,
            "user_simulator_config": args.user_simulator_config,
        },
        evaluator_config_dict,
        theme=evaluator.theme,
        background_memories=memories,
        dataset_queries=dataset_queries,
        inject_session_id=inject_session_id,
        inject_user_id=getattr(args, "user_id", ""),
    )


def run_replay_mode(
    args,
    run: EvalRun,
    agent_plugin: AgentPlugin,
    llm: LLMClient,
) -> None:
    log = run.logger
    log.info("模式: replay (回放数据集: %s)", args.dataset)
    evaluator_config = load_evaluator_config(args.evaluator_config)
    jobs, plans = load_dataset(
        args.dataset,
        sample_filter=args.sample,
    )
    if args.questions > 0:
        jobs = jobs[:args.questions]
    rounds: list[dict[str, Any]] = []
    facts: dict[str, str] = {}
    backend = getattr(args, "memory_backend", "echomem")
    for plan_index, plan in enumerate(
        tqdm(plans, desc="回放 sample", unit="sample")
    ):
        sample_id = str(
            plan.get("sample_id") or f"sample_{plan_index}"
        )
        events = plan.get("events") or []
        if not events:
            continue
        inject_start = time.monotonic()
        try:
            inject_session = agent_plugin.inject_memories(
                events, backend=backend,
            )
        except RuntimeError as exc:
            log.error(
                "记忆注入失败: sample=%s error=%s",
                sample_id,
                exc,
            )
            continue
        inject_elapsed = time.monotonic() - inject_start
        print(f"[inject] sample={sample_id} {len(events)} memories injected in {inject_elapsed:.1f}s (session={inject_session})")
        log.info("memory injection completed: sample=%s %d memories in %.1fs", sample_id, len(events), inject_elapsed)
        qa_session = agent_plugin.create_session(
            title=f"replay-qa-{sample_id}",
        )
        for job in (
            candidate for candidate in jobs
            if candidate.sample_id == sample_id
        ):
            round_data = {
                "id": job.question_id,
                "query": job.question,
                "ground_facts": [job.answer],
                "new_session": True,
                "is_injection": False,
                "complexity": job.category,
            }
            metrics = _ask_agent(
                args,
                agent_plugin,
                qa_session,
                round_data,
            )
            metrics.update({
                "session_id": qa_session,
                "question_id": job.question_id,
                "gold_answer": job.answer,
            })
            rounds.append(metrics)
            facts[job.question_id] = job.answer
            log.info(
                "QA[%s] ttft=%sms reply_len=%d",
                job.question_id,
                metrics["ttft_ms"],
                metrics["reply_length"],
            )
    save_results(
        run,
        rounds,
        facts,
        llm,
        {
            "mode": "replay",
            "dataset": args.dataset,
            "sample": args.sample,
            "questions": args.questions,
            "agent_plugin": args.agent_plugin,
            "evaluator_config": args.evaluator_config,
        },
        evaluator_config,
        theme="replay",
        inject_user_id=getattr(args, "user_id", ""),
    )
