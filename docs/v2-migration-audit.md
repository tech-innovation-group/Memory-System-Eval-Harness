# v2 Migration Audit

This audit records what was moved from
`/Users/chx/Code/memory-benchmark-workbench` into the v3 CLI layout and what
was deliberately left behind.

Source classes and pinned commits are documented in
`docs/v2-source-provenance.md`.

## Migrated

| v2 area | v3 implementation | Verification |
| --- | --- | --- |
| Supported VikingBot prompts, read-only tool protocols, and iterative loop | `plugins/vikingbot/`, `benchmarks/locomo/profiles/` | profile provenance, prompt boundary, five-tool protocol, threshold, QA, and CLI tests |
| Per-question VikingBot prompt/tool traces and answer cleanup | `plugins/vikingbot/answers.py`, `benchmarks/locomo/agent_traces/` runtime artifacts | VikingBot trace and sanitizer tests |
| Incremental LoCoMo QA/Judge checkpoints and validated QA resume | `benchmarks/locomo/qa.py`, `benchmarks/locomo/judge.py`, `benchmarks/locomo/resume.py` | ordered checkpoint, strict resume compatibility, and CLI integration tests |
| EchoMemory backend descriptor, registry, and HTTP client | `backends/echomemory/` | backend contract tests and `scripts/backend_doctor.py` |
| LoCoMo import, QA, Judge, retry, diagnosis, reports | `benchmarks/locomo/` | workflow, Judge, CLI, and reporting tests |
| Strict black-box status, latency, retries, usage, throughput | `shared/qa.py`, `shared/llm_client.py`, `benchmarks/locomo/blackbox.py` | QA contract and reporting tests |
| LoCoMo run comparison | `benchmarks/locomo/compare.py` | comparison tests |
| LongMemEval import, official evaluation, sharding, recovery | `benchmarks/longmemeval/` | workflow and CLI integration tests |
| HotpotQA answer, supporting-fact, and joint metrics | `benchmarks/hotpotqa/` | workflow and explicit evidence metadata tests |
| HotpotQA failed/missing question recovery | `benchmarks/hotpotqa/recovery.py` | recovery selection and successful-row merge tests |
| Evidence contract validation | `shared/evidence.py`, `scripts/validate_evidence.py` | validation utility tests |
| Dynamic evaluator modules | `dynamic/` | dynamic workflow tests |

The formal paths use HTTP-visible behavior only. Internal EchoMemory token
counts, workspace state, and readiness are not inferred.

## Replaced By V3 Architecture

| v2 area | v3 replacement |
| --- | --- |
| Mixed dataset scripts and shared path assumptions | Dataset-owned modules under `benchmarks/<dataset>/` |
| Root-level generic adapter implementation | `benchmarks/generic/adapter.py` with a thin compatibility entrypoint |
| Agent logic embedded in benchmark scripts | Agent plugins under `plugins/<agent>/` |
| Direct EchoMemory client construction throughout scripts | `backends/echomemory/` registry and client boundary |
| Repeated failed/missing-row recovery helpers | Shared primitives in `shared/recovery.py` plus dataset commands |
| Root-level one-off launch scripts | Unified `eval.py` and `eval.sh` CLI dispatch |

## v2 Path Ledger

| v2 committed path family | Disposition |
| --- | --- |
| `benchmark/locomo/echomemory/*` | Migrated into `benchmarks/locomo/`; wrapper scripts replaced by the unified CLI |
| `memory/vikingboat_alignment.py` | Superseded by the retained VikingBoat v0.4.11 profiles |
| `memory/plugins/echomemory/*` | HTTP transport and read-only agent behavior split between `backends/echomemory/` and `plugins/vikingbot/`; SDK/workspace inspection excluded |
| `memory/adapters/*`, plugin contracts and registries | Replaced by `backends/` contracts, registry, and backend doctor |
| `memory/datasets.py` | Formal loaders split by dataset; custom dry-run parsing moved to `benchmarks/generic/` |
| `memory/evidence_contract.py` | Migrated to `shared/evidence.py` and `scripts/validate_evidence.py` |
| `memory/strict_blackbox.py`, strict metric/report code | Migrated to `benchmarks/locomo/blackbox.py` with JSON and Markdown artifacts |
| `memory/reports.py`, `report_export.py`, `graph_report.py` | HTML/workbench presentation replaced by dataset-owned JSON/CSV/Markdown reporting; graph workspace inspection excluded |
| `memory/runs.py`, `status.py`, `tasking.py`, `task_specs.py`, `services/*` | Web task orchestration replaced by direct CLI execution, `EvalRun`, and `shared/service_manager.py` |
| `scripts/echomemory_locomo_import.py`, QA/prompt/tool helpers | Formal black-box behavior migrated into LoCoMo, VikingBot, and EchoMemory modules; local evidence augmentations excluded |
| `scripts/local_judge.py`, `local_stats.py` | Replaced by `benchmarks/locomo/evaluate.py`, `judge.py`, and `stats.py` |
| `scripts/hotpotqa_answer_eval.py` | Migrated to `benchmarks/hotpotqa/evaluate.py` |
| `scripts/longmemeval_official_eval.py`, parallel runner | Migrated to `benchmarks/longmemeval/evaluate.py`, `judge.py`, and `parallel.py` |
| EchoMemory failed/missing retry scripts | Replaced by dataset recovery commands using `shared/recovery.py` |
| Smoke and validation scripts | Replaced by the Python unit/integration suite and CI preflight |
| `server.py`, `web/`, `src/`, browser assets | Intentionally excluded because the target repository is CLI-only |
| OpenViking plugin, adapter, scripts, templates | Intentionally excluded because EchoMemory is the only backend |

## Intentionally Excluded

| v2 area | Reason |
| --- | --- |
| OpenViking backend, SDK, configuration, and workflows | EchoMemory is the only runtime backend in scope |
| OpenViking dataset adapters and service launchers | No OpenViking runtime is required |
| Web UI, server, task registry, and browser orchestration | The repository is CLI-only |
| EchoMemory SDK/workspace inspectors and repair scripts | Formal evaluation stays HTTP black-box |
| Workspace-reading wait/evaluate scripts | They violate the black-box boundary |
| Dated experiment matrix scripts | Historical settings are represented by explicit dataset profiles |
| Local heuristic agent baselines | The reproduction target is the historical VikingBot loop |

OpenViking names remain only where they are part of the exact historical
VikingBot prompt/bootstrap snapshot. They do not register a backend, import an
SDK, or enable an OpenViking workflow.

## Still Pending

| Item | Status |
| --- | --- |
| Paid LoCoMo conv-30 81-question QA and Judge run | Completed for retained profiles; local artifacts are excluded from the repository |
| Reproduction score claim | Retained profile references are memory-qualified |
| Dirty/untracked v2 experiment behavior | Excluded from the supported profile set |

## Behavioral Guarantees

1. `--resume-qa` reuses the prior run's identity and skips already-injected sessions.
2. Reused and newly injected memory use the same LoCoMo QA and Judge path.
3. Historical references remain distinct: July 13 `head_clean` `63/81`
   and July 17 rejudged `61/81`.
4. The committed v2 HEAD profile is separate and makes no score claim.
5. Agents live under `plugins/<agent>/`; dataset policy stays under
   `benchmarks/<dataset>/`; memory transports live under `backends/<backend>/`.
6. The default backend registry contains only `echomemory`.

## Final Verification

The migration is accepted after:

```bash
python -m compileall -q eval.py plugins backends benchmarks dynamic scripts shared tests
python -m unittest discover -s tests -v
python -m pip check
git diff --check
bash -n eval.sh
./eval.sh locomo --check --dataset /path/to/locomo10.json --sample conv-30
```
