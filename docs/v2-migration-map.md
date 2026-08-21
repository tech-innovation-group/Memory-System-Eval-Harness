# v2 Migration Map

This repository keeps the v3 CLI foundation while migrating the working
behavior from `/Users/chx/Code/memory-benchmark-workbench` into clearer
ownership boundaries.

## Target Layout

| Destination | Responsibility |
| --- | --- |
| `plugins/` | Cross-dataset agent prompts, tool protocols, runtimes, and registry |
| `backends/` | Memory backend contracts and the EchoMemory plugin |
| `benchmarks/<dataset>/` | Dataset loading, import workflows, profiles, metrics, judge, retry, and reports |
| `shared/` | Backend-neutral HTTP, LLM, runtime, result, and utility code |

Agents and memory backends are separate concepts. VikingBot is an agent;
EchoMemory is the only backend in scope.

## Completed Mapping

| v2 source or historical source | Current destination |
| --- | --- |
| VikingBot system prompt and workspace bootstrap | `plugins/vikingbot/prompting.py`, `plugins/vikingbot/bootstrap/` |
| VikingBot read-only memory tool protocol | `plugins/vikingbot/tools.py` |
| OpenAI-compatible iterative tool loop | `plugins/vikingbot/runtime.py` |
| Reusable VikingBot entrypoint | `plugins/vikingbot/plugin.py` |
| Agent contract and lookup | `plugins/base.py`, `plugins/registry.py` |
| VikingBoat v0.4.11 prompt, five-tool protocol, and alignment settings | `plugins/vikingbot/vikingboat0411_prompting.py`, `plugins/vikingbot/tools.py`, `benchmarks/locomo/profiles/vikingboat0411.py` |
| v2 backend descriptor/config contract | `backends/base.py` |
| v2 backend registry | `backends/registry.py` |
| Existing EchoMemory HTTP search/read/list/glob implementation | `backends/echomemory/client.py` |
| EchoMemory backend plugin | `backends/echomemory/plugin.py` |
| Backend-neutral search and commit result types | `backends/types.py` |

The two VikingBoat v0.4.11 profiles carry no score claim.

## Backend Migration

| v2 source | Current destination |
| --- | --- |
| `memory/plugins/base.py` | `backends/base.py` |
| Reusable parts of `memory/plugins/contract.py` | `backends/base.py` validation |
| `memory/plugins/registry.py` | `backends/registry.py` |
| EchoMemory HTTP behavior from `memory/plugins/echomemory/*` and current v3 client | `backends/echomemory/` |

The v2 `service.py`, inspectors, web agent workbench, and task-command builders
were intentionally not copied. They mixed server/UI concerns with backend
transport. Dataset task construction belongs under `benchmarks/<dataset>/`;
generic process management stays in `shared/service_manager.py`.

The current benchmark CLI creates EchoMemory clients through the backend
registry. OpenViking backend, SDK, configuration, and dataset workflows are
explicitly out of scope. OpenViking names remain only inside the vendored
historical VikingBot prompt text needed for the LoCoMo reproduction profile.

## Dataset Migration

| v2 behavior | Current destination |
| --- | --- |
| `echomemory_locomo_import.py` | `benchmarks/locomo/import_memory.py` |
| LoCoMo parts of `echomemory_memory_qa.py` | `benchmarks/locomo/qa.py`, `benchmarks/locomo/profiles/` |
| LoCoMo prompts, query plans, retry, exact judge, diagnosis | `benchmarks/locomo/judge.py`, `retry.py`, `diagnosis.py` |
| Strict black-box metrics and standalone reports | `benchmarks/locomo/blackbox.py` |
| Run-to-run question and category comparison | `benchmarks/locomo/compare.py` |
| LongMemEval loading, official judge/evaluation, recovery, shard merging | `benchmarks/longmemeval/dataset.py`, `judge.py`, `evaluate.py`, `recovery.py`, `parallel.py` |
| HotpotQA answer, supporting-fact, and joint evaluation | `benchmarks/hotpotqa/evaluate.py` |
| HotpotQA global import behavior | `benchmarks/hotpotqa/import_memory.py` |
| HotpotQA failed/missing QA recovery and successful-row merge | `benchmarks/hotpotqa/recovery.py` |
| Generic JSON/JSONL dataset I/O | `shared/dataset_io.py` |
| Generic persisted-QA recovery primitives | `shared/recovery.py` |
| Generic/custom dataset dry-run adapter | `benchmarks/generic/adapter.py` |
| Dynamic user simulation and prompt loading | `dynamic/simulator.py`, `dynamic/prompt_config.py` |
| Dynamic EchoAgent transport, workflows, metrics, and artifacts | `dynamic/client.py`, `workflows.py`, `metrics.py`, `artifacts.py` |
| Backend/evidence contract checks | `scripts/backend_doctor.py`, `scripts/validate_evidence.py` |

The old web UI, web-task registry, workspace-bound command builders, and
OpenViking runtime integration were not migrated. Generic/custom dataset
dry-run parsing lives under `benchmarks/generic/adapter.py`;
`scripts/benchmark_adapter.py` is only a compatibility entrypoint.

The complete inclusion/exclusion audit and verification checklist is recorded
in `docs/v2-migration-audit.md`.

## Migration Rules

1. Preserve tested behavior and CLI compatibility before reorganizing internals.
2. Keep dataset policy out of reusable agents and backends.
3. Split large v2 runners by ownership instead of copying them wholesale.
4. Record historical reproduction settings in explicit dataset profiles.
5. Run compile, unit tests, dependency checks, shell checks, and diff checks
   after each migration slice.
