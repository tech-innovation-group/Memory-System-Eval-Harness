# EchoMemory-MM CVPR Appendix Draft v15

Date: 2026-06-17

## A. Scope of the Appendix

This appendix has four jobs:

1. hold the experiment detail omitted from the main paper
2. hold the code-to-method mapping
3. hold the 30-paper positioning and implementation-status map
4. make claim boundaries auditable

It is intentionally broader than the main paper. The main paper should stay thesis-driven; this appendix is where the package becomes reproducible and reviewable.

## B. Full Method Elaboration

### B.1 Session stream and append-only observations

EchoMemory-MM starts from an append-only interaction stream. This matters because later memory objects should be understood as structured projections of the stream, not replacements for it.

### B.2 Atomic plane

The atomic plane stores fact, event, relation, plan, and image-evidence-like units. It is the lowest structured layer that is still query-facing.

### B.3 Topic dossier plane

The topic dossier plane sits between global overview and flat atoms. Its purpose is to preserve within-topic continuity across sessions for longitudinal questions such as:

- what is the latest status of X?
- how did X evolve?
- what changed over time in the lease / visa / project?

### B.4 Temporal tree

The temporal tree is a chronology-oriented abstraction structure. Its main purpose is to make chronology a retrieval surface rather than a passive metadata field.

### B.5 Relation graph

The relation graph stores event, entity, fact, and image-evidence nodes with typed edges. Its role is to support relation-heavy, event-linked, and visually grounded retrieval.

### B.6 Readiness plane

The readiness plane distinguishes:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

This separates durability from answerability.

### B.7 Shared evidence contract

The shared evidence contract is the control object that ties planning, gating, self-check, and second-pass expansion together. It defines:

- required evidence families
- present evidence families
- missing evidence families
- whether the current retrieval is structurally sufficient

### B.8 Coverage-aware gating

Coverage-aware gating prevents high-confidence primary hits from stopping retrieval early when the planned evidence contract is still incomplete.

### B.9 Type-aware second pass

Type-aware second pass expands the reader implied by the missing evidence family, rather than always issuing a graph-only retry.

## C. Code Anchors

The main code anchors are:

- `/Users/chx/Code/echomemory/echo_memory/echomem/workers/organized_projector/projector.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/graph_seed_planner.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py`

These files already show that EchoMemory is not just “a vector store plus prompt template”; it is already close to a memory operating layer.

## D. Experiment Inventory

### D.1 Canonical nano reference

Single-file reference:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14.py`
- smoke test:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/test_nano_reference_impl_v14.py`
- reading guide:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_reference_v14_reading_guide_20260617.html`

Purpose:

- give a clean, generic entry point
- explain the method without benchmark-specific hacks

### D.2 Three-clock temporal-semantics ablation

Result:

- write-time only: `0/4`
- event+mention split: `4/4`
- three-clock: `4/4`

Takeaway:

- temporal correctness depends on preserving event occurrence time explicitly

### D.3 Generalized method-prototype nano

Result:

- flat text: `4/6`
- primary-only: `2/6`
- contract-aware: `6/6`

Takeaway:

- many failures are better understood as wrong evidence shape than as missing keywords

### D.4 Topic-dossier ablation

Result:

- overview-only: `1/5`
- atom-only: `3/5`
- topic-dossier: `4/5`

Related generalization benchmark:

- overview-only: `1/6`
- atom-only: `3/6`
- topic-dossier: `5/6`

Takeaway:

- a topic-centered middle layer is useful for cross-session progress/status questions

### D.5 Dual-backbone and relation routing

Anchored temporal:

- tree-only: `3/3`
- graph-only: `2/3`
- dual-backbone: `3/3`

Relation backbone:

- tree-only: `0/3`
- graph-only: `3/3`
- dual-backbone: `3/3`

12-case dual benchmark:

- tree-only: `3/12`
- graph-only: `5/12`
- dual-backbone: `8/12`

Takeaway:

- tree and graph solve different failure modes

### D.6 Readiness and answer-time policy

Readiness ablation:

- baseline: `1/5`
- temporal_graph: `4/5`
- full: `5/5`

Self-check v2:

- baseline: `4/8`
- self-check: `8/8`

Coverage-aware gating:

- keyword correctness: `6/6 = 6/6`
- contract completeness: `1/6 -> 2/6`

Type-aware second pass:

- one-pass: `1/5`
- graph-only second pass: `3/5`
- type-aware second pass: `5/5`

Takeaway:

- answer-time policy is not an implementation detail; it changes correctness

### D.7 Multimodal contract

Result:

- one-pass: `2/5`
- contract-aware: `5/5`

Takeaway:

- visual answers can look relevant while still lacking structural grounding

### D.8 Query-family paraphrase robustness

This benchmark is intentionally narrow. It keeps the same underlying memory and expected facts fixed, but rewrites each query family in multiple surface forms.

Result:

- baseline cues: answer-correct `8/15`, family-correct `13/15`
- improved generic cues: answer-correct `15/15`, family-correct `15/15`

Takeaway:

- generalization should come from generic query-family routing, not benchmark-specific trigger words

### D.9 Real-code bridge

The current `SearchService` family subset passes `21/21` against family-level expectations.

Takeaway:

- the policy ideas are already visible in the real stack, not only in toy code

## E. Query-Family Paraphrase Benchmark Notes

This benchmark is especially useful for claim discipline.

What it does show:

- family routing can improve without adding dataset-specific entity lists
- answer quality can improve because the right primary reader is chosen more often

What it does not show:

- benchmark-scale superiority
- robustness to arbitrary open-domain paraphrase
- learned semantic routing

## F. 30-Paper Code-Grounded Map

The 30-paper package should be read along six implementation axes:

1. time semantics
2. hierarchy / middle layer
3. graph / path grounding
4. lifecycle / readiness
5. policy / answer-time control
6. multimodal evidence

For each paper-level idea, the package distinguishes:

- already reflected in current code
- partially reflected
- still missing

This matters because it prevents the paper from sounding as if every cited idea has already been fully implemented.

## G. Artifact Pointers

### G.1 Main paper artifacts

- main draft source:
  - `/Users/chx/locomo-eval-web/docs/echomemory_mm_cvpr_main_submission_draft_v15_20260617.md`
- full v14 draft source:
  - `/Users/chx/locomo-eval-web/docs/echomemory_mm_cvpr_submission_draft_v14_20260616.md`
- concise results table:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_v14_results_table_20260616.html`

### G.2 Appendix-grade reports

- family benchmark panel:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_family_benchmark_panel_20260616.html`
- 30-paper appendix:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_30paper_codegrounded_appendix_20260616.html`
- structure + top10 map:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_structure_top10_improvement_map_20260617.html`

### G.3 Nano reference artifacts

- reference implementation HTML:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_reference_impl_v14_20260616.html`
- reading guide:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_reference_v14_reading_guide_20260617.html`
- paraphrase benchmark:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_reference_v14_paraphrase_benchmark_20260617.html`

## H. Claim Boundary

The best current label remains:

`paper-shaped, code-backed, benchmark-incomplete`

What can be honestly claimed now:

- a coherent, code-backed mechanism paper
- strong structural evidence for three-clock time, dual-backbone routing, topic-centered middle layer, readiness, contract-aware gating, type-aware second pass, and typed image evidence
- a genuine bridge from nano method evidence to current real-code behavior

What cannot yet be honestly claimed:

- benchmark-scale superiority
- mature multimodal benchmark performance
- deployment-grade latency/cost conclusions
