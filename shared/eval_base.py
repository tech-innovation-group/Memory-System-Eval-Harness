"""Evaluation infrastructure: result directory, logging, config, plugin CLI.

Design intent: this module is the shared backbone for every benchmark
and dynamic run_eval. It provides:
  - EvalConfig: a dataclass holding benchmark-generic config fields. The
    fields are populated from the unified argparse namespace by
    build_config_from_args(), regardless of which plugin declared each arg.
  - add_agent_plugin_args: pre-parse sys.argv to discover which plugin was
    requested, then delegate to that plugin's add_arguments so its CLI args
    (LLM credentials, QA behavior, memory backend connection, commit
    timeouts, plugin-specific options) appear in --help and are parsed.
  - add_llm_args / add_qa_args: shared helpers that plugins call inside
    their add_arguments() to declare LLM and QA behavior params. Benchmark
    run_eval does NOT call these directly.
  - add_eval_args: declares benchmark-infra params (--concurrency,
    --out-dir, --allow-diagnostics). Called by every run_eval.
  - add_judge_args: declares the three judge-LLM params shared by all
    benchmarks that use LLM-based judging. Called by benchmark run_eval.
  - resolve_llm_credentials: complementary fallback between two sets of
    LLM credentials (e.g. scenario LLM vs answer LLM in dynamic mode).
  - EvalRun: manages the timestamped result directory and logging.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EvalConfig:
    """Common configuration for all benchmark evaluations.

    Fields are populated from the unified argparse namespace by
    build_config_from_args().  The args may have been declared by the
    benchmark (dataset, judge, concurrency), by the plugin (LLM, QA
    behavior), or by the memory backend (connection, commit timeouts).
    """

    # Plugins
    memory_backend: str = "echomem"
    agent_plugin: str = "bare_llm"

    # LLM for answering
    llm_base_url: str = ""
    llm_model: str = "doubao-seed-2.0-pro"
    llm_api_key: str = ""
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048

    # Retrieval
    top_k: int = 10
    memory_budget_chars: int = 8000

    # Concurrency
    concurrency: int = 4

    # Timeouts
    commit_timeout_s: float = 0.0
    commit_poll_interval_s: float = 2.0
    question_timeout_s: float = 120.0
    llm_timeout_s: float = 120.0
    llm_retries: int = 3

    # Dataset
    dataset_path: str = ""
    sample_filter: str = "all"
    question_limit: int = 0  # 0 = all

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        v = d.get("llm_api_key", "")
        if v:
            d["llm_api_key"] = v[:4] + "***" + v[-4:] if len(v) > 8 else "***"
        return d


class EvalRun:
    """Manages a single evaluation run: result directory, logging, summary.

    Each run gets a timestamped subdirectory under ``results_root``::

        results_root / 20260728_153022 /
            config.json
            run.log
            results.csv
            summary.json
    """

    def __init__(
        self,
        benchmark_name: str,
        results_root: str | Path = "results",
        config: EvalConfig | None = None,
    ):
        self.benchmark_name = benchmark_name
        self.config = config or EvalConfig()
        self.started_at = datetime.now(timezone.utc)
        self.started_monotonic = time.monotonic()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.result_dir = Path(results_root) / ts
        self.result_dir.mkdir(parents=True, exist_ok=True)

        self._setup_logging()
        self._save_config()

    def _setup_logging(self) -> None:
        """Configure file + console logging."""
        self.logger = logging.getLogger(f"eval.{self.benchmark_name}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        self.logger.propagate = False

        # File handler – full detail
        fh = logging.FileHandler(self.result_dir / "run.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        self.logger.addHandler(fh)

        # Console handler – INFO level, concise
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
        self.logger.addHandler(ch)

        # Also configure echomem_client logger
        for name in ("echomem_client", "llm_client", "eval"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.DEBUG)
            lg.handlers.clear()
            lg.addHandler(fh)
            lg.addHandler(ch)
            lg.propagate = False

    def _save_config(self) -> None:
        path = self.result_dir / "config.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "benchmark": self.benchmark_name,
                    "started_at": self.started_at.isoformat(),
                    "config": self.config.to_dict(),
                },
                f, indent=2, ensure_ascii=False,
            )

    def save_config(self) -> None:
        """Persist the current, possibly runtime-resolved configuration."""
        self._save_config()

    def log(self, msg: str, level: int = logging.INFO) -> None:
        self.logger.log(level, msg)

    def save_summary(self, summary: dict[str, Any]) -> None:
        summary["benchmark"] = self.benchmark_name
        summary.setdefault("run_started_at", self.started_at.isoformat())
        summary.setdefault("run_finished_at", self.finished_at_iso())
        summary["finished_at"] = summary["run_finished_at"]
        path = self.result_dir / "summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        self.log(f"Summary saved to {path}")

    def finished_at_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def elapsed_str(self, start: float) -> str:
        return f"{time.monotonic() - start:.1f}s"


# ------------------------------------------------------------------ #
#  CLI arg helpers                                                    #
# ------------------------------------------------------------------ #

def _scan_argv_for_plugin(flag: str, default: str) -> str:
    """Pre-parse sys.argv to find --flag VALUE or --flag=VALUE."""
    for index, arg in enumerate(sys.argv):
        if arg == flag and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return default


def add_agent_plugin_args(
    parser: argparse.ArgumentParser,
    default_plugin: str = "bare_llm",
) -> None:
    """Declare --agent-plugin and the selected plugin's CLI arguments."""
    parser.add_argument("--agent-plugin", default=default_plugin, help="Agent plugin name")
    plugin_name = _scan_argv_for_plugin("--agent-plugin", default_plugin)
    from plugins import get_plugin_class
    get_plugin_class(plugin_name).add_arguments(parser)


def add_llm_args(parser) -> None:
    """Add common LLM CLI args.

    Shared helper called by each plugin's add_arguments() to declare the
    LLM credentials/params the plugin needs to build its LLMClient.
    Benchmark run_eval does NOT call this directly.
    """
    g = parser.add_argument_group("LLM")
    g.add_argument("--llm-base-url", default=os.getenv("LLM_BASE_URL", ""), help="LLM API base URL")
    g.add_argument("--llm-model", default=os.getenv("LLM_MODEL", "doubao-seed-2.0-pro"))
    g.add_argument("--llm-api-key", default=os.getenv("LLM_API_KEY", ""), help="LLM API key")
    g.add_argument("--llm-temperature", type=float, default=0.7)
    g.add_argument("--llm-max-tokens", type=int, default=2048)
    g.add_argument("--llm-timeout-s", type=float, default=120.0)
    g.add_argument("--llm-retries", type=int, default=3)


def add_qa_args(parser) -> None:
    """Add QA behavior CLI args (retrieval, prompt formatting, timeouts).

    Shared helper called by each plugin's add_arguments() to declare the
    QA params the plugin needs for memory retrieval and answer generation.
    Benchmark run_eval does NOT call this directly.
    """
    g = parser.add_argument_group("QA")
    g.add_argument("--top-k", type=int, default=10, help="Number of memory items to retrieve (TOPK)")
    g.add_argument("--memory-budget-chars", type=int, default=8000, help="Max chars of memory to inject into prompt")
    g.add_argument(
        "--question-timeout-s",
        type=float,
        default=120.0,
        help="End-to-end retrieval and answer timeout per question (0 = no extra limit)",
    )


def add_eval_args(parser) -> None:
    """Add benchmark infrastructure args shared by all run_eval scripts.

    Declares --concurrency, --out-dir, and --allow-diagnostics (continue
    past incomplete imports or memory provenance mismatches for diagnostic
    runs).
    """
    g = parser.add_argument_group("Evaluation")
    g.add_argument("--concurrency", type=int, default=4, help="Number of concurrent QA tasks")
    g.add_argument("--out-dir", default="results", help="Results root directory")
    g.add_argument(
        "--allow-diagnostics",
        action="store_true",
        help="Continue past incomplete memory imports or provenance mismatches (diagnostics only)",
    )


def add_judge_args(parser) -> None:
    """Add the three judge-LLM CLI args shared by all LLM-judged benchmarks.

    Each falls back to the corresponding --llm-* value when left empty.
    Benchmarks with additional judge params (e.g. --judge-concurrency)
    declare those locally after calling this helper.
    """
    g = parser.add_argument_group("Judge")
    g.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", ""), help="Judge LLM 模型名 (默认同 --llm-model)")
    g.add_argument("--judge-api-key", default=os.getenv("JUDGE_TOKEN", ""), help="Judge API key (默认同 --llm-api-key)")
    g.add_argument("--judge-base-url", default=os.getenv("JUDGE_BASE_URL", ""), help="Judge base URL (默认同 --llm-base-url)")


def resolve_llm_credentials(args) -> None:
    """Fill in missing LLM credentials by complementary fallback.

    When two sets of LLM credentials are present (e.g. scenario LLM and
    answer LLM in dynamic mode), if one side has a value the other lacks,
    copy it over.  Mutates *args* in place.
    """
    if getattr(args, "scenario_base_url", "") and not getattr(args, "llm_base_url", ""):
        args.llm_base_url = args.scenario_base_url
    elif getattr(args, "llm_base_url", "") and not getattr(args, "scenario_base_url", ""):
        args.scenario_base_url = args.llm_base_url

    if getattr(args, "scenario_api_key", "") and not getattr(args, "llm_api_key", ""):
        args.llm_api_key = args.scenario_api_key
    elif getattr(args, "llm_api_key", "") and not getattr(args, "scenario_api_key", ""):
        args.scenario_api_key = args.llm_api_key


def build_config_from_args(args) -> EvalConfig:
    """Build an EvalConfig from parsed argparse args.

    Uses getattr so it works even when a plugin doesn't declare every param
    (e.g., echo_agent in dynamic mode doesn't declare QA params).
    """
    return EvalConfig(
        memory_backend=getattr(args, "memory_backend", "echomem"),
        agent_plugin=getattr(args, "agent_plugin", "bare_llm"),
        llm_base_url=getattr(args, "llm_base_url", ""),
        llm_model=getattr(args, "llm_model", "doubao-seed-2.0-pro"),
        llm_api_key=getattr(args, "llm_api_key", ""),
        llm_temperature=getattr(args, "llm_temperature", 0.7),
        llm_max_tokens=getattr(args, "llm_max_tokens", 2048),
        top_k=getattr(args, "top_k", 10),
        memory_budget_chars=getattr(args, "memory_budget_chars", 8000),
        concurrency=getattr(args, "concurrency", 4),
        commit_timeout_s=getattr(args, "commit_timeout_s", 0.0),
        commit_poll_interval_s=getattr(args, "commit_poll_interval_s", 2.0),
        question_timeout_s=getattr(args, "question_timeout_s", 120.0),
        llm_timeout_s=getattr(args, "llm_timeout_s", 120.0),
        llm_retries=getattr(args, "llm_retries", 3),
    )


def results_root_for(benchmark_dir: str | Path, out_dir: str) -> Path:
    """Resolve the result root while preserving the historical default."""
    value = str(out_dir or "results").strip()
    if value == "results":
        return Path(benchmark_dir) / "results"
    return Path(value).expanduser()


def validate_eval_config(config: EvalConfig) -> None:
    errors: list[str] = []
    if not config.llm_base_url.strip():
        errors.append("missing LLM base URL")
    if not config.llm_model.strip():
        errors.append("missing LLM model")
    if not config.llm_api_key.strip():
        errors.append("missing LLM API key")
    if config.concurrency < 1:
        errors.append("concurrency must be >= 1")
    if config.question_timeout_s < 0:
        errors.append("question timeout must be >= 0")
    if config.commit_timeout_s < 0:
        errors.append("commit timeout must be >= 0")
    if config.commit_poll_interval_s <= 0:
        errors.append("commit poll interval must be > 0")
    if config.llm_timeout_s <= 0:
        errors.append("LLM timeout must be > 0")
    if config.llm_retries < 1:
        errors.append("LLM retries must be >= 1")
    if config.llm_max_tokens < 1:
        errors.append("LLM max tokens must be >= 1")
    if config.top_k < 1:
        errors.append("top-k must be >= 1")
    if config.memory_budget_chars < 1:
        errors.append("memory budget must be >= 1")
    if config.question_limit < 0:
        errors.append("questions must be >= 0")
    if errors:
        raise ValueError("; ".join(errors))
