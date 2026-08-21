#!/usr/bin/env python3
"""动态评测脚本: 仿真 Agent+记忆系统 线上真实效果。

两种模式:
  - generate: LLM 生成背景记忆 -> 注入记忆后端 -> 逐轮 QA 测试端到端召回+TTFT
  - replay: 回放数据集对话, 直接注入记忆后端 -> 新会话 QA 测试跨 session 召回

两种模式的注入阶段都直连记忆后端 (open_session -> add_message -> commit -> poll),
不经 Agent, 不触发 LLM 生成。QA 阶段走 Agent 完整管线 (含 prefill/TTFT, 由插件决定)。

支持不同 Agent 插件: 通过 --agent-plugin 选择, 插件特有参数由插件自身声明。
用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins import load_agent_plugin
from dynamic.artifacts import build_v2_quality_report as _build_v2_quality_report
from dynamic.workflows import run_generate_mode, run_replay_mode
from shared.eval_base import (
    EvalRun,
    add_agent_plugin_args,
    build_config_from_args,
    resolve_llm_credentials,
    results_root_for,
    validate_eval_config,
)
from shared.llm_client import LLMClient

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="动态评测: 仿真 Agent+记忆系统 线上效果")

    # 模式选择
    g = parser.add_argument_group("模式")
    g.add_argument("--dataset", default="", help="数据集路径 (指定则进入 replay 模式; 不指定则 generate 模式)")
    g.add_argument("--sample", default="all")
    g.add_argument("--questions", type=int, default=0)

    # 评测器配置 (两种模式共用)
    g = parser.add_argument_group("评测器配置")
    g.add_argument("--evaluator-config",
                   default=str(_CONFIGS_DIR / "evaluator_template.yaml"),
                   help="评测器配置 YAML，路径相对于 run_eval.py (默认 configs/evaluator_template.yaml)")

    # Generate 模式参数
    g = parser.add_argument_group("Generate 模式")
    g.add_argument("--num-memories", type=int, default=5, help="生成的背景记忆数")
    g.add_argument("--num-queries", type=int, default=10, help="生成的提问数")
    g.add_argument("--new-session-ratio", type=float, default=0.3)
    g.add_argument("--typing-speed-ms", type=int, default=200)
    g.add_argument("--typing-jitter-ms", type=int, default=20)
    g.add_argument("--user-simulator-config",
                   default=str(_CONFIGS_DIR / "user_simulator_default.yaml"),
                   help="用户模拟器配置，路径相对于 run_eval.py (默认 configs/user_simulator_default.yaml)")

    # 场景生成 LLM (仅 generate 模式使用, 用于生成背景记忆和 query)
    g = parser.add_argument_group("场景生成 LLM")
    g.add_argument("--scenario-model", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_MODEL", "deepseek-v4-flash"))
    g.add_argument("--scenario-base-url", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_BASE_URL", ""))
    g.add_argument("--scenario-api-key", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_API_KEY", ""))

    # Agent 插件 (声明 LLM / 记忆后端 / 插件特有参数)
    add_agent_plugin_args(parser, default_plugin="echo_agent")

    # 评测基础设施参数 (dynamic 不支持并发, 不声明 --concurrency)
    g = parser.add_argument_group("评测")
    g.add_argument("--out-dir", default="results", help="结果输出目录")

    return parser


def validate_dynamic_args(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []

    evaluator_path = Path(args.evaluator_config).expanduser()
    if not evaluator_path.is_file():
        errors.append(f"evaluator config not found: {evaluator_path}")
    if args.dataset:
        dataset_path = Path(args.dataset).expanduser()
        if not dataset_path.is_file():
            errors.append(f"dataset not found: {dataset_path}")
    else:
        for name, value in (
            ("scenario base URL", args.scenario_base_url),
            ("scenario model", args.scenario_model),
            ("scenario API key", args.scenario_api_key),
        ):
            if not str(value or "").strip():
                errors.append(f"missing {name}")
        simulator_path = Path(args.user_simulator_config).expanduser()
        if not simulator_path.is_file():
            errors.append(f"user simulator config not found: {simulator_path}")

    if args.questions < 0:
        errors.append("questions must be >= 0")
    if args.num_memories < 1:
        errors.append("num memories must be >= 1")
    if args.num_queries < 1:
        errors.append("num queries must be >= 1")
    if not 0 <= args.new_session_ratio <= 1:
        errors.append("new session ratio must be between 0 and 1")
    if args.typing_speed_ms < 0 or args.typing_jitter_ms < 0:
        errors.append("typing speed and jitter must be >= 0")
    return errors


def main() -> None:
    args = build_parser().parse_args()

    resolve_llm_credentials(args)

    config = build_config_from_args(args)
    validate_eval_config(config)

    errors = validate_dynamic_args(args)
    if errors:
        raise ValueError("; ".join(errors))

    results_root = results_root_for(Path(__file__).parent, args.out_dir)
    run = EvalRun(
        benchmark_name="dynamic",
        results_root=results_root,
        config=config,
    )
    log = run.logger

    agent_config = {**vars(args), "benchmark_name": "dynamic", "run_id": run.result_dir.name}
    try:
        agent_plugin = load_agent_plugin(args.agent_plugin, agent_config)
    except Exception as e:
        print(f"agent plugin 加载失败: {e}", file=sys.stderr)
        sys.exit(1)

    log.info("agent plugin loaded: %s", args.agent_plugin)

    try:
        agent_plugin.memory_client.health()
    except Exception as e:
        log.error("memory backend health check failed: %s", e)
        agent_plugin.teardown()
        sys.exit(1)
    log.info("memory backend health check passed")

    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        temperature=0.3,
        max_tokens=4096,
        timeout_s=config.llm_timeout_s,
        max_retries=config.llm_retries,
    )

    try:
        if args.dataset:
            run_replay_mode(args, run, agent_plugin, llm)
        else:
            run_generate_mode(args, run, agent_plugin, llm)
    finally:
        log_json = agent_plugin.getlog()
        (run.result_dir / "backend_logs.json").write_text(log_json, encoding="utf-8")
        agent_plugin.teardown()


if __name__ == "__main__":
    main()
