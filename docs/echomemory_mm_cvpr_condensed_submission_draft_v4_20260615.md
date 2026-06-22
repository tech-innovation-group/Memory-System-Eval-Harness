# EchoMemory-MM Condensed Submission Draft v4

Date: 2026-06-15

## Title

**EchoMemory-MM: Typed-Evidence Governance for Dual-Backbone Multimodal Temporal Graph Memory**

## Abstract

Long-horizon personal agents must preserve evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, and increasingly ground responses in screenshot or OCR-bearing observations. Existing memory systems often flatten these demands into a single retrieval pool, forcing chronology-heavy, relation-heavy, visual, and readiness-sensitive queries through the same evidence path. We propose **EchoMemory-MM**, a contract-driven dual-backbone memory architecture that organizes append-only interaction streams into an atomic plane, a temporal tree, a relation graph, image-evidence nodes, and a readiness plane. The central claim is that long-horizon memory should be governed by **typed evidence contracts** rather than by confidence-only retrieval heuristics. Temporal queries require chronology-oriented evidence and explicit story time; visual queries require first-class image evidence; relation-heavy queries require not only graph hits but also explicit path grounding. EchoMemory-MM therefore couples query planning, retrieval gating, answer-time self-check, and second-pass expansion through a shared evidence contract. We ground the proposal in the current EchoMemory repository and support it with a code-backed evidence package: canonical nano implementations, a generalized method-prototype nano, family-specific ablations, a 21-case real-code subset over the current `SearchService`, a coverage-aware gating ablation, a type-aware second-pass ablation, and a new relational path-grounding upgrade with tests. Across these mechanism-level studies, we observe a consistent pattern: chronology-heavy queries prefer temporal routing, relation-heavy queries prefer graph routing, readiness gating is a correctness mechanism rather than a UI convenience, confidence should not substitute for evidence sufficiency, and relation-heavy questions become more faithful when graph retrieval is required to surface explicit path evidence. While the current package does not yet establish benchmark-scale superiority, it supports a narrower and stronger conclusion: long-horizon multimodal memory should be modeled as a contract-driven, planner-routed, stream-to-structure system with explicit time semantics, explicit evidence types, and explicit answer-time policy.

## 1. Introduction

Long-horizon memory failures in personal agents are often described as retrieval failures. That diagnosis is too weak. In practice, at least six distinct failures repeatedly appear:

1. temporal questions are answered from write time rather than story time
2. relation-heavy questions are answered from broad summaries rather than linked evidence
3. visually grounded questions are answered without first-class image evidence
4. newly persisted information is treated as answerable before consolidation completes
5. retrieval stops early because confidence is high even though the evidence family is incomplete
6. graph retrieval looks relevant but never surfaces a concrete path showing why the relation answer is justified

These are not one failure. A question such as “What happened yesterday?” should not follow the same evidence route as “Who introduced Maya to Nora?”, and neither should be treated the same way as “What address was shown in the lease screenshot?” or “Can the system answer now?” immediately after a write.

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an incremental atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, query-time anchoring, a typed planner, a shared evidence-contract layer, and answer-time self-check. The opportunity is not to invent a different stack from scratch, but to turn these planes into a more explicit research architecture.

We study the following thesis:

> Long-horizon multimodal memory should be modeled as a typed-evidence-governed, dual-backbone, readiness-aware, answer-time-governed stream-to-structure system.

This thesis leads to six design commitments:

- temporal and relation-heavy questions should not share the same primary backbone
- persisted memory should not be treated as automatically QA-ready
- story time should not be silently replaced by write time
- evidence sufficiency should be explicit rather than implicit
- retrieval expansion should depend on the missing evidence family
- relation-heavy answers should expose path-level graph grounding rather than only graph relevance

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we provide:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation family for explanation and ablation
- a generalized nano method prototype that avoids benchmark-specific hardcoding
- a new typed-evidence governance line that now covers temporal, visual, and relational questions
- mechanism-level evidence that supports the architectural claim above

## 2. Related Work

Recent work exerts five kinds of pressure on long-horizon memory systems.

**Benchmark pressure.** LoCoMo, LongMemEval, LongMemEval-V2, and recent diagnosis-oriented work show that long-horizon memory is not one scalar capability. Temporal, relational, update-sensitive, abstention-sensitive, and lifecycle-sensitive failures should be evaluated separately.

**Hierarchical and temporal retrieval.** RAPTOR, MemoRAG, ByteRover, TiMem, and related work show that long-horizon recall improves when memory is exposed through structured abstraction rather than a flat retrieval pool. This directly motivates the temporal-tree route in EchoMemory-MM.

**Graph and structured recall.** HippoRAG, GraphReader, Zep, LEGO-GraphRAG, H-Mem, and APEX-MEM suggest that relation-heavy or event-linked questions should be grounded in graph-structured recall rather than summary-only evidence.

**Lifecycle and systems.** Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not only retrieval. It is also a lifecycle problem involving extraction cost, consolidation delay, routing policy, and evidence maintenance.

**Policy and multimodal direction.** MIRIX, Mem-T, E-mem, D-MEM, Self-RAG, and recent multimodal memory lines suggest two themes: memory actions should be policy-aware, and multimodal observations should become first-class memory.

EchoMemory-MM sits at the intersection of these lines. Its main distinction is not a new storage primitive, but a shared typed-evidence contract that ties routing, gating, self-check, and second-pass expansion together.

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

Finally, we distinguish two levels of graph evidence:

- **graph relevance**: the graph returns related event/entity nodes
- **path grounding**: the graph returns an explicit relation path or path trace justifying why a relation answer is supported

The goal is to avoid flattening all these pressures into one retrieval path and one unconditional answer policy.

## 4. Method

### 4.1 Memory planes

EchoMemory-MM uses five planes:

1. **Session stream**: append-only interaction and observation history
2. **Atomic plane**: fact, event, relation, plan, and image atoms
3. **Temporal tree**: chronology-oriented abstraction blocks
4. **Relation graph**: event, entity, fact, and image-evidence nodes with typed edges
5. **Readiness plane**: lifecycle state from persisted to answerable

### 4.2 Three-clock temporal semantics

Query-time anchoring is necessary but not sufficient. A memory system can still fail temporal questions if it stores only one timestamp and silently reuses write time as event time. EchoMemory-MM therefore separates:

- `story_time` for chronology and ordering
- `mention_time` for retrospective narration and revisit points
- `write_time` for durability and lifecycle state

### 4.3 Dual-backbone routing

EchoMemory-MM defines two primary retrieval backbones:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy and visually grounded queries

The non-primary backbone can still contribute supporting evidence. A temporal query may still need graph support for participants or linked facts, but its primary route remains chronology-aware.

### 4.4 Typed evidence governance

Routing alone is not enough if different parts of the stack disagree about what counts as sufficient evidence. In practice, planner logic, layer-skipping logic, and answer-time self-check tend to drift apart.

We therefore define a lightweight **typed evidence contract**. The contract maps each query family to required evidence families. The important point is that the contract is no longer just about broad layers. It can include finer-grained evidence obligations:

- temporal: `temporal_tree + event + event_time`
- visual: `image_evidence + fact`
- relational: `graph + fact + path_grounding`
- temporal-relational: `event + fact + temporal_tree`

This means the system distinguishes between:

- a graph hit with no explicit relation path
- a temporal hit with mention-time but not story-time grounding
- a visual hit with OCR text but no first-class image evidence node

The contract is intentionally generic. It does not depend on benchmark-specific entities or query-specific keyword hacks. Instead, it exposes:

- required evidence families
- present evidence families
- matched evidence families
- missing evidence families
- path-grounding or event-time special checks when needed

### 4.5 Readiness-aware answerability

Persisted memory is weaker than answerable memory. A system should not answer simply because a write completed. It should answer only when the required memory structures are ready enough for faithful retrieval.

### 4.6 Coverage-aware gating

The contract is also useful before answer generation. A high-confidence primary hit should not terminate retrieval if the planned evidence contract is still incomplete. We call this **coverage-aware gating**.

### 4.7 Type-aware second pass

If the contract exposes which evidence families are missing, retrieval expansion itself can become structured. Instead of issuing a fixed retry, EchoMemory-MM performs a **type-aware second pass**:

- missing chronology support -> probe the temporal tree or episode-like support
- missing fact grounding -> probe the atomic / fact reader
- missing graph or entity linkage -> probe the graph reader
- missing image-grounded support -> probe image-evidence-bearing graph nodes
- missing path grounding -> probe graph paths rather than merely more graph relevance

This turns second-pass expansion into a policy aligned with missing evidence, rather than a generic retry.

## 5. Code Anchors

The current EchoMemory repository already contains most of the needed substrate:

- session lifecycle and append-only storage: `index_engine/session_service.py`
- atom-first incremental extraction: `workers/atom_first_pipeline.py`
- organized projection to overview / entities / events: `workers/organized_projector/projector.py`
- graph synchronization and image evidence: `index_engine/graph/sync.py`
- query routing and retrieval: `index_engine/search_service.py`
- planner logic: `index_engine/planner/query_planner.py`
- evidence contract: `index_engine/policy/evidence_contract.py`
- answer-time self-check: `index_engine/policy/self_check.py`

The newest concrete integration step is the **relational path-grounding upgrade**, which now promotes graph path traces to a first-class required evidence type in the main code path.

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
| Relational path-grounding contract ablation | graph+fact / graph+fact+path | path grounding absent -> present | Relation questions should not stop at graph relevance without explicit path evidence. |
| Real-code family subset | current `SearchService` | `21/21` family expectation pass | The same policy signals are visible in the real stack, not only in toy code. |

### 6.2 New relational path-grounding upgrade

This newest line is intentionally narrow but methodologically important. It asks:

> should a relation-heavy question be considered sufficiently supported when the graph returns related nodes and the atomic plane returns a fact, even if the graph never surfaces an explicit relation path?

The answer from the new code and nano evidence is no.

In the main code path:

- `query_planner.py` now requires `("graph", "fact", "path_grounding")` for relational queries
- `evidence_contract.py` now checks graph path traces explicitly
- `search_service.py` now treats missing `path_grounding` as a graph-side evidence deficit
- `self_check.py` now warns when relation-heavy queries have graph hits but no explicit path grounding

This line is valuable because it extends the same contract idea already used for:

- `event_time` in temporal queries
- `image_evidence` in visual queries

The result is a more unified typed-evidence-governance story rather than three unrelated heuristics.

### 6.3 What the current evidence actually proves

The current package supports six reviewer-facing conclusions.

First, **temporal correctness is partly a schema problem before it is a routing problem**.

Second, **evidence shape is a more revealing failure signal than surface keyword relevance**.

Third, **chronology-heavy and relation-heavy questions do not want the same primary reader**.

Fourth, **readiness is part of correctness**.

Fifth, **answer-time policy is not optional**.

Sixth, **relation-heavy answering benefits from explicit path-grounding requirements**. A graph hit is not the same thing as a path-justified answer.

## 7. What This Draft Still Needs

This condensed draft is stronger than the previous version because the method story is now more unified. But it is still not submission-ready.

What is still missing for a real CVPR submission:

1. a stronger multimodal evaluation line beyond OCR-bearing screenshots
2. a larger benchmark-scale experiment package
3. latency / cost analysis for online versus offline consolidation
4. a clearer visual-memory projection story
5. a final camera-ready figure and table pack

What is now much stronger:

1. the typed-evidence-governance claim
2. the bridge from current code to method language
3. the nano method-prototype explanation path
4. the explicit relation between temporal, visual, and relational contracts

## 8. Artifact Pointers

- 30-paper map: `docs/echomemory_mm_30paper_map_and_nano_benchmark_20260613.md`
- condensed draft v3: `docs/echomemory_mm_cvpr_condensed_submission_draft_v3_20260615.md`
- condensed draft v4: `docs/echomemory_mm_cvpr_condensed_submission_draft_v4_20260615.md`
- generalized nano method prototype: `web/static/generated-reports/echomemory_nano_generalizable_stream_graph_contract_20260615.html`
- path-grounding upgrade note: `web/static/generated-reports/echomemory_relational_path_grounding_upgrade_20260615.html`
- path-grounding nano ablation: `web/static/generated-reports/echomemory_nano_relational_path_grounding_contract_ablation_20260615.html`
- top10 structure report: `web/static/generated-reports/echomemory_structure_top10_improvement_plan_20260615.html`

## 9. Conclusion

EchoMemory-MM is converging on a cleaner research thesis:

> long-horizon multimodal memory should be governed by typed evidence contracts over planner-routed temporal and graph backbones, under explicit readiness and answer-time policy.

The current codebase already contains most of the structural substrate for this thesis. The current nano experiments, ablations, and the newest relational path-grounding upgrade make the method more coherent than before. The result is still a mechanism-backed research direction rather than a finished benchmark paper, but it is now much closer to a believable submission story than a flat “memory backend” narrative.
