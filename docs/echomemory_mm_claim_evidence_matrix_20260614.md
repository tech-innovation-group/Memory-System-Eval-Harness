# EchoMemory-MM Claim-Evidence Matrix

Date: 2026-06-14

## Why this file exists

The current EchoMemory-MM package has a decent draft, a 30-paper map, and several nano experiments.
What it still lacked was a single place answering:

1. what exactly are we claiming
2. what evidence supports each claim
3. what claim level is justified now
4. what is still missing before a real CVPR submission

This file is that bridge.

---

## Claim matrix

| ID | Claim | Current evidence | Current status | What still blocks stronger wording |
|---|---|---|---|---|
| C1 | Long-horizon memory failure is not one scalar retrieval failure; temporal, relation, and lifecycle failures differ. | Main-code structure analysis plus benchmark pressure from LoCoMo, LongMemEval, LongMemEval-V2, WhenLoss, and When Stored Evidence Stops Being Usable. | Supported as systems framing. | Needs broader empirical decomposition on real benchmarks. |
| C2 | Relative-time queries should resolve against `query_time`, not runtime wall clock. | Main-code `RequestContext.query_time` path, anchored temporal retrieval code, anchored temporal nano ablation. | Strongly supported. | Needs larger benchmark-scale temporal evaluation. |
| C3 | Temporal questions and relation-heavy questions should not share the same primary retrieval backbone. | Anchored temporal ablation, relation-backbone ablation, dual-backbone toy benchmark, code path showing temporal tree + graph both exist. | Strongly supported at mechanism level. | Needs full benchmark comparison against flat / tree-only / graph-only baselines. |
| C4 | Dual-backbone routing is more faithful than flattening all evidence into one retrieval pool. | 12-case dual-backbone benchmark and paired family-specific ablations. | Supported at mechanism level. | Needs larger datasets and stronger statistical coverage. |
| C5 | Memory durability is weaker than answerability; persisted messages are not automatically QA-ready. | Readiness on/off ablation (`baseline 1/5`, `temporal_graph 4/5`, `full 5/5`). | Strongly supported at mechanism level. | Needs readiness evaluation beyond nano. |
| C6 | Readiness is a correctness mechanism, not just a UI or ops feature. | Same readiness ablation plus main-code readiness lifecycle narrative. | Supported. | Needs real-system readiness state logging and user-facing error analysis. |
| C7 | EchoMemory already has the right architectural substrate for a stream-to-structure memory system. | Session plane, atom-first pipeline, organized projector, graph sync, temporal tree, planner/search. | Strongly supported by code inspection. | Needs tighter integration between these planes in runtime policy. |
| C8 | Image evidence should be first-class memory, not auxiliary metadata. | Main-code `image_evidence` path in graph sync and multimodal direction from recent papers. | Architecturally motivated, early code support exists. | Needs real multimodal benchmark evidence. |
| C9 | EchoMemory-MM is a plausible CVPR-shaped research direction. | Coherent draft, 30-paper grounding, nano implementation, four experiment lines, honest claim boundary. | Supported as a research direction. | Not yet a complete CVPR submission due to scale and multimodal gaps. |
| C10 | EchoMemory-MM already beats benchmark baselines or reaches SOTA. | None. | Not supported. | Would require real benchmark runs and comparison tables. |

---

## Evidence inventory

### Code anchors

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/episode/retriever.py`

### Experiment anchors

- Anchored temporal ablation:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_anchored_temporal_ablation_20260614.html`
- Relation-backbone ablation:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_report.html`
- Dual-backbone benchmark:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_benchmark_20260613.html`
- Readiness on/off ablation:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_report.html`

### Paper and literature anchors

- Main draft:
  - `/Users/chx/locomo-eval-web/docs/echomemory_mm_cvpr_draft_v7_20260614.md`
- 30-paper map:
  - `/Users/chx/locomo-eval-web/docs/echomemory_mm_30paper_map_and_nano_benchmark_20260613.md`
- Reference verification:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_core_reference_verification_20260614.html`

---

## Honest claim boundary

### Safe to say now

- EchoMemory is moving toward a query-time anchored, planner-routed, dual-backbone memory architecture.
- The current code already contains the major structural planes needed for that direction.
- Mechanism-level experiments support three core design pressures:
  - temporal anchoring
  - graph-first relation routing
  - readiness lifecycle gating

### Not safe to say now

- benchmark-scale superiority on LoCoMo or LongMemEval
- mature multimodal benchmark performance
- production-complete cost / latency conclusions
- end-to-end proof that all architectural planes are already optimally integrated

---

## What to do next

### Highest-value next experiments

1. real benchmark temporal split:
   - date questions
   - ordering questions
   - relative-time questions
2. flat vs tree-only vs graph-only vs dual-backbone comparison
3. readiness logging on real import/query traces
4. real multimodal evaluation with screenshots / OCR evidence

### Highest-value paper upgrades

1. move from research draft to 8-page submission skeleton
2. add a figure showing the five-plane architecture
3. add a table separating:
   - claim
   - code evidence
   - experiment evidence
   - current confidence level

---

## Bottom line

The right current position is:

> EchoMemory-MM is already a credible research direction with code-backed structure and mechanism-level evidence, but it is not yet a benchmark-complete CVPR submission.

That is a stronger and more useful statement than either under-selling the work or over-claiming completion.
