# EchoMemory-MM Condensed Submission Draft v3

Date: 2026-06-15

## Title

**EchoMemory-MM: Contract-Driven Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Abstract

Long-horizon personal agents must remember evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, and increasingly ground responses in visual observations such as screenshots or OCR-bearing images. Existing memory systems often flatten these demands into a single retrieval pool, forcing chronology-heavy, relation-heavy, visual, and readiness-sensitive queries through the same evidence path. We propose **EchoMemory-MM**, a contract-driven dual-backbone memory architecture that organizes append-only interaction streams into an atomic plane, a temporal tree, a relation graph, and a readiness plane. EchoMemory-MM uses a shared evidence contract to connect query planning, retrieval gating, answer-time self-check, and supporting-evidence expansion. Temporal queries route to a chronology-aware tree, relation-heavy and visually grounded queries route to a graph backbone, and retrieval expansion is triggered by missing evidence families rather than by fixed retries. We further argue that event time should not be collapsed into write time, and include a new three-clock temporal-semantics ablation to isolate this failure mode. We ground the proposal in the current EchoMemory codebase and evaluate it with a code-backed package: canonical nano implementations, a new generalized method-prototype nano, family-specific ablations, a 21-case real-code subset over the current `SearchService`, a coverage-aware gating ablation, a type-aware second-pass ablation, and the new three-clock ablation. Across these mechanism-level studies, we observe a consistent pattern: chronology-heavy queries prefer temporal routing, relation-heavy queries prefer graph routing, readiness gating is a correctness mechanism rather than a UI convenience, confidence should not substitute for evidence sufficiency, and stable temporal answering requires explicit time semantics as well as explicit answer-time policy. While the current evidence does not support benchmark-scale superiority claims, it supports a narrower conclusion: long-horizon multimodal memory should be modeled as a contract-driven, planner-routed, stream-to-structure system with explicit answer-time policy and explicit time semantics.

## 1. Introduction

Long-horizon memory failures in personal agents are usually described as retrieval failures. This framing is too coarse. In practice, at least five distinct failures repeatedly appear:

1. temporal questions are answered from write time rather than story time
2. relation-heavy questions are answered from broad summaries rather than linked evidence
3. visually grounded questions are answered without first-class image evidence
4. newly persisted information is treated as answerable before consolidation completes
5. retrieval stops early because confidence is high even though the evidence family is incomplete

These are not the same failure. A question such as “What happened yesterday?” should not follow the same evidence path as “Who introduced Jon to Lena?”, and neither should be handled the same way as “Can the system answer now?” immediately after a write. Even after good retrieval, there remains an answer-time policy question: should the system answer now, expand supporting evidence, or abstain?

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an incremental atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, and planner-like retrieval logic. The opportunity is not to invent a new stack from scratch, but to turn these existing planes into a more explicit memory architecture.

We study the following thesis:

> Long-horizon memory should be modeled as a contract-driven, dual-backbone, readiness-aware, answer-time-governed stream-to-structure system.

This thesis leads to five design commitments:

- temporal and relation-heavy questions should not share the same primary backbone
- persisted memory should not be treated as automatically QA-ready
- event occurrence time should not be silently replaced by write time
- evidence sufficiency should be explicit rather than implicit
- retrieval expansion should depend on the missing evidence family

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we provide:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation family for explanation and ablation
- a generalized nano method prototype that separates method structure from benchmark surface form
- mechanism-level evidence that supports the systems claim above

## 2. Related Work

Recent benchmark and systems work can be grouped by pressure rather than by venue list.

**Benchmark pressure.** LoCoMo, LongMemEval, LongMemEval-V2, and recent diagnosis-oriented work such as Regimes, When Stored Evidence Stops Being Usable, and WhenLoss make clear that long-horizon memory is not one scalar capability. Temporal, relational, update-sensitive, and lifecycle-sensitive failures should be evaluated separately.

**Hierarchical and temporal retrieval.** RAPTOR, MemoRAG, ByteRover, TiMem, and related work show that long-horizon recall improves when memory is exposed through structured abstraction rather than a flat retrieval pool. This directly motivates the temporal-tree route in EchoMemory-MM.

**Graph and structured recall.** HippoRAG, GraphReader, Zep, LEGO-GraphRAG, H-Mem, and APEX-MEM suggest that relation-heavy or event-linked questions should be grounded in graph-structured recall rather than in summary-only evidence.

**Lifecycle and systems.** Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not only retrieval. It is also a lifecycle problem involving extraction cost, consolidation delay, routing policy, and evidence maintenance.

**Policy and multimodal direction.** MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward two themes: memory actions should be policy-aware, and multimodal observations should become first-class memory.

EchoMemory-MM sits at the intersection of these lines. Its main distinction is not a new storage primitive, but an explicit contract tying routing, gating, self-check, and second-pass expansion together.

## 3. Problem Formulation

We distinguish four notions of time:

- **write time**: when a message is durably persisted
- **story time**: when the described event actually occurred
- **mention time**: when an earlier event is revisited inside the interaction stream
- **query time**: the anchor against which relative expressions such as “yesterday” should be resolved

We also distinguish four query families:

1. **temporal**: date lookup, relative-time, chronology, ordering
2. **relational**: spouse, introducer, helper, multi-hop linkage
3. **visual**: screenshot / OCR / photo-grounded evidence
4. **readiness-sensitive**: whether the system should answer now

Finally, we define a readiness lifecycle:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

The goal is to avoid flattening all these pressures into one retrieval path and one unconditional answer policy. In particular, it should prevent the collapse of story time, mention time, and write time into one overloaded timestamp.

## 4. Method

### 4.1 Memory planes

EchoMemory-MM uses five planes:

1. **Session stream**: append-only interaction and observation history
2. **Atomic plane**: fact, event, relation, and plan atoms
3. **Temporal tree**: chronology-oriented abstraction blocks
4. **Relation graph**: event, entity, fact, and image-evidence nodes with typed edges
5. **Readiness plane**: lifecycle state from persisted to answerable

### 4.2 Three-clock temporal semantics

Query-time anchoring is necessary but not sufficient. A memory system can still fail temporal questions if it stores only one timestamp and silently reuses write time as event time. EchoMemory-MM therefore separates:

- `story_time` for chronology and ordering
- `mention_time` for retrospective narration and revisit points
- `write_time` for durability and lifecycle state

The new three-clock nano ablation isolates this design question directly and shows that temporal correctness depends on memory schema as well as retrieval routing.

### 4.3 Dual-backbone routing

EchoMemory-MM defines two primary retrieval backbones:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy and visually grounded queries

The non-primary backbone can still contribute supporting evidence. A temporal query may still need graph support for participants or linked facts, but its primary route should remain chronology-aware.

### 4.4 Shared evidence contract

Dual-backbone routing alone is not enough if different parts of the stack disagree about what counts as sufficient evidence. In practice, planner logic, layer-skipping logic, and answer-time self-check tend to drift apart. One module may stop because confidence is high, while another still implicitly expects event or episode evidence.

We therefore define a lightweight **shared evidence contract**. The contract maps each query family to required evidence families. Typical examples are:

- temporal: `temporal_tree + event`
- relational: `entity + fact`
- visual: `image_evidence + fact`
- temporal-relational: `event + fact + temporal_tree`

The contract is intentionally generic. It does not depend on dataset-specific keyword lists or benchmark-specific entity vocabularies. Instead, it exposes:

- required evidence families
- present evidence families
- matched evidence families
- missing evidence families

This makes evidence sufficiency auditable.

### 4.5 Readiness-aware answerability

Persisted memory is weaker than answerable memory. A system should not answer simply because a write completed. It should answer only when the required memory structures are ready enough for faithful retrieval. EchoMemory-MM therefore treats readiness as a correctness mechanism, not merely an operational convenience.

### 4.6 Coverage-aware gating

The contract is useful even before answer generation. A high-confidence primary hit should not terminate retrieval if the planned evidence contract is still incomplete. We call this **coverage-aware gating**. Its role is to prevent the system from confusing confidence with sufficiency.

### 4.7 Type-aware second pass

If the contract exposes which evidence families are missing, retrieval expansion itself can become structured. Instead of issuing a fixed graph-only supplement, EchoMemory-MM performs a **type-aware second pass**:

- missing chronology support -> probe the temporal tree or episode-like support
- missing fact grounding -> probe the atomic / fact reader
- missing graph or entity linkage -> probe the graph reader
- missing image-grounded support -> probe image-evidence-bearing graph nodes

This turns second-pass expansion into a policy aligned with missing evidence, rather than a generic retry.

### 4.8 Generalized nano method prototype

To make the method easier to inspect, we also provide a generalized nano prototype that combines four ideas in one minimal system:

- three-clock time
- temporal-tree / graph dual-backbone routing
- a shared evidence contract
- contract-driven supporting retrieval

This prototype deliberately avoids benchmark-specific entities or keyword hacks. Its role is explanatory: it shows that the method remains coherent under generic event, relation, plan, and OCR-bearing image cases.

### 4.9 Figure placeholders

- **Figure 1.** Session stream -> atom plane -> organized memory -> temporal tree / relation graph -> readiness plane -> contract-driven search.
- **Figure 2.** Query-time routing examples for temporal, relational, temporal-relational, and visual questions.
- **Figure 3.** Answer-time policy loop: primary retrieval -> contract check -> type-aware second pass -> answer / abstain.

## 5. Code Anchors

The current EchoMemory repository already contains most of the needed substrate:

- session lifecycle and append-only storage: `index_engine/session_service.py`
- atom-first incremental extraction: `workers/atom_first_pipeline.py`
- organized projection to overview / entities / events: `workers/organized_projector/projector.py`
- graph synchronization and image evidence: `index_engine/graph/sync.py`
- query routing and retrieval: `index_engine/search_service.py`
- planner logic: `index_engine/planner/query_planner.py`
- episode retrieval: `index_engine/episode/retriever.py`
- evidence contract: `index_engine/policy/evidence_contract.py`
- coverage-aware retrieval gating: `index_engine/policy/retrieval_gating.py`
- answer-time self-check: `index_engine/policy/self_check.py`

The key engineering gap is no longer “missing structure”, but stronger runtime integration between these planes.

## 6. Experiments

### 6.1 Main-results snapshot

| Evidence line | Compared variants | Main result | Why it matters |
| --- | --- | --- | --- |
| Three-clock temporal-semantics ablation | write-time only / event+mention / three-clock | `0/4` / `4/4` / `4/4` | Temporal correctness depends on preserving event time, not only on routing. |
| Generalized method-prototype nano | flat text / primary only / contract-aware | `4/6` / `2/6` / `6/6` | Once contract completeness is enforced, the key failure is wrong evidence shape rather than missing keywords. |
| Anchored temporal ablation | tree-only / graph-only / dual | `3/3` / `2/3` / `3/3` | Relative-time questions prefer chronology-aware routing. |
| Relation-backbone ablation | tree-only / graph-only / dual | `0/3` / `3/3` / `3/3` | Relation-heavy questions prefer graph grounding. |
| Dual-backbone benchmark | tree-only / graph-only / dual | `3/12` / `5/12` / `8/12` | Tree and graph cover different failure modes; routing beats either alone. |
| Readiness ablation | baseline / temporal_graph / full | `1/5` / `4/5` / `5/5` | Persistence is weaker than answerability. |
| Coverage-aware gating | confidence-only / coverage-aware | contract-complete `1/6` -> `2/6` | Confidence should not substitute for evidence sufficiency. |
| Type-aware second pass | one-pass / graph-only / type-aware | contract-complete `1/5` -> `3/5` -> `5/5` | Supporting retrieval works best when aligned with missing evidence families. |
| Multimodal contract ablation | one-pass / contract-aware | contract-complete `2/5` -> `5/5` | Visual queries can look relevant while still lacking owner, fact, or event grounding. |
| Real-code family subset | current `SearchService` | `21/21` family expectation pass | The same policy signals are visible in the real stack, not only in toy code. |

### 6.2 Mechanism-level evidence package

We evaluate EchoMemory-MM through a code-backed package rather than a single benchmark table. The package currently includes:

- three-clock temporal-semantics ablation
- generalized method-prototype nano
- anchored temporal ablation
- relation-backbone ablation
- dual-backbone benchmark
- readiness on/off ablation
- self-check v2 experiment
- 21-case real-code family subset
- coverage-aware gating ablation
- type-aware second-pass ablation
- multimodal contract ablation

### 6.3 What the current evidence actually proves

The evidence supports six reviewer-facing conclusions.

First, **temporal correctness is partly a schema problem before it is a routing problem**. The three-clock ablation shows that retrospective mention, relative-day, and before/after questions become unstable when write time is treated as a proxy for event occurrence.

Second, **evidence shape is a more revealing failure signal than surface keyword relevance**. The generalized method-prototype nano shows that flat retrieval can still look superficially correct while failing the contract, whereas contract-aware retrieval restores complete evidence across generic temporal, relational, and visual cases.

Third, **chronology-heavy and relation-heavy questions do not want the same primary reader**. The temporal and relation ablations point in opposite directions, and the dual-backbone benchmark shows that combining these routes through planning is more stable than collapsing them into a single flat retrieval pool.

Fourth, **readiness is part of correctness**. The readiness ablation shows that “message persisted” is not equivalent to “safe to answer”. This is not just an operational concern; it directly changes answer-time behavior.

Fifth, **answer-time policy is not optional**. Coverage-aware gating and type-aware second-pass ablations show that the important decision is not merely whether to retrieve more, but whether the system understands *what kind* of evidence is still missing.

Sixth, **multimodal relevance is weaker than multimodal completeness**. The new multimodal contract ablation shows that a screenshot or OCR hit can already make the answer look relevant while still missing owner/entity grounding, linked event support, or fact support. This matters for a CVPR-facing version of the system because image evidence should not be treated as a decorative add-on to text memory.

### 6.4 What the current evidence does not prove

The current package does **not** prove:

- benchmark-scale superiority on LoCoMo
- benchmark-scale superiority on LongMemEval
- production-ready multimodal performance
- latency or cost advantages in a deployment setting

This claim boundary is intentional. The current package is meant to support a strong systems paper argument before a larger benchmark campaign is complete.

## 7. Real-Code Bridge

The nano evidence establishes mechanism plausibility, but it does not by itself prove that the same mechanism survives contact with the current EchoMemory retrieval stack. We therefore ran a focused real-code family subset directly on the current `SearchService` after integrating the new evidence-review and supporting-backbone expansion logic.

The subset spans temporal, relational, temporal-relational, visual, and factual queries. Its value is not end-task accuracy estimation. Its value is different: it checks whether the family-level design pressures argued in the paper become visible in the actual retrieval path.

The current subset passes `21/21` against family-level expectations. This does not justify benchmark-scale superiority claims, but it materially strengthens the systems claim that the policy ideas are already observable in the real stack.

## 8. Discussion

The present evidence supports a mechanism-level claim, not a benchmark-scale claim. Specifically, it supports the claim that long-horizon multimodal memory should not be flattened into one retrieval assumption or one unconditional answer policy.

Three lessons stand out.

First, chronology and relation are structurally different pressures. They should not be forced through the same primary evidence route.

Second, durability and answerability are different states. Treating persisted memory as immediately answerable is a correctness mistake.

Third, answer-time policy matters even after retrieval. Strong primary evidence does not imply sufficient evidence, and “do another pass” is only truly useful when the expansion policy is aligned with the missing evidence family.

## 9. Limitations

The current work has clear limitations:

1. experiments remain mechanism-oriented rather than benchmark-scale
2. multimodal evidence paths are architecturally promising but empirically thin
3. latency and cost are not yet formalized
4. runtime integration across all memory planes is still incomplete
5. the real-code subset is a bridge artifact, not a final benchmark

These limitations are real, but they are also useful because they keep the claim boundary honest.

## 10. Artifact Pointers

- 30-paper map: `docs/echomemory_mm_30paper_map_and_nano_benchmark_20260613.md`
- full draft: `docs/echomemory_mm_cvpr_submission_draft_v9_20260615.md`
- condensed draft v3: `docs/echomemory_mm_cvpr_condensed_submission_draft_v3_20260615.md`
- figure pack: `web/static/generated-reports/echomemory_mm_figure_pack_20260615.html`
- nano package: `experiments/echomemory_nano/README.md`
- generalized nano method prototype: `web/static/generated-reports/echomemory_nano_generalizable_stream_graph_contract_20260615.html`
- generalized nano appendix: `web/static/generated-reports/echomemory_generalized_nano_appendix_20260615.html`
- generalized nano main-code bridge: `web/static/generated-reports/echomemory_generalized_nano_maincode_bridge_20260615.html`
- three-clock nano report: `web/static/generated-reports/echomemory_nano_three_clock_temporal_ablation_20260615.html`
- 30-paper code matrix: `web/static/generated-reports/echomem_30paper_code_matrix_and_threeclock_20260615.html`
- multimodal contract ablation: `web/static/generated-reports/echomemory_nano_multimodal_contract_ablation_20260615.html`
- submission package home: `web/static/generated-reports/echomemory_mm_submission_package_home_20260615.html`

## 11. Conclusion

EchoMemory-MM advances a simple but under-emphasized thesis:

> temporal questions, relation-heavy questions, readiness constraints, and answer-time evidence policy should not be collapsed into one flat retrieval assumption.

The current EchoMemory repository already contains the major structural planes needed for this direction, and the current nano experiments plus the real-code family subset support the same architectural conclusion from multiple angles. In particular, the newer coverage-aware gating and type-aware second-pass results suggest that the key policy question is not merely whether to expand retrieval, but whether expansion is aligned with the missing evidence family. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.
