# EchoMemory-MM Condensed Submission Draft v1

Date: 2026-06-15

## Title

**EchoMemory-MM: Contract-Driven Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Abstract

Long-horizon personal agents must remember evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, and increasingly ground responses in visual observations such as screenshots or OCR-bearing images. Existing memory systems often flatten these demands into a single retrieval pool, forcing chronology-heavy, relation-heavy, visual, and readiness-sensitive queries through the same evidence path. We propose **EchoMemory-MM**, a contract-driven dual-backbone memory architecture that organizes append-only interaction streams into an atomic plane, a temporal tree, a relation graph, and a readiness plane. EchoMemory-MM uses a shared evidence contract to connect query planning, retrieval gating, answer-time self-check, and supporting-evidence expansion. Temporal queries route to a chronology-aware tree, relation-heavy and visually grounded queries route to a graph backbone, and retrieval expansion is triggered by missing evidence families rather than by fixed retries. We ground the proposal in the current EchoMemory codebase and evaluate it with a code-backed package: canonical nano implementations, family-specific ablations, a 21-case real-code subset over the current `SearchService`, a coverage-aware gating ablation, and a type-aware second-pass ablation. Across these mechanism-level studies, we observe a consistent pattern: chronology-heavy queries prefer temporal routing, relation-heavy queries prefer graph routing, readiness gating is a correctness mechanism rather than a UI convenience, confidence should not substitute for evidence sufficiency, and second-pass retrieval is strongest when aligned with the missing evidence family. While the current evidence does not support benchmark-scale superiority claims, it supports a narrower conclusion: long-horizon multimodal memory should be modeled as a contract-driven, planner-routed, stream-to-structure system with explicit answer-time policy.

## 1. Introduction

Long-horizon memory failures in personal agents are usually described as retrieval failures. This framing is too coarse. In practice, at least four distinct failures repeatedly appear:

1. temporal questions are answered from write time rather than story time
2. relation-heavy questions are answered from broad summaries rather than linked evidence
3. visually grounded questions are answered without first-class image evidence
4. newly persisted information is treated as answerable before consolidation completes

These are not the same failure. A question such as “What happened yesterday?” should not follow the same evidence path as “Who introduced Jon to Lena?”, and neither should be handled the same way as “Can the system answer now?” immediately after a write. Even after good retrieval, there remains an answer-time policy question: should the system answer now, expand supporting evidence, or abstain?

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an incremental atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, and planner-like retrieval logic. The opportunity is not to invent a new stack from scratch, but to turn these existing planes into a more explicit memory architecture.

We study the following thesis:

> Long-horizon memory should be modeled as a contract-driven, dual-backbone, readiness-aware, answer-time-governed stream-to-structure system.

This thesis leads to four design commitments:

- temporal and relation-heavy questions should not share the same primary backbone
- persisted memory should not be treated as automatically QA-ready
- evidence sufficiency should be explicit rather than implicit
- retrieval expansion should depend on the missing evidence family

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we provide:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation family for explanation and ablation
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

We distinguish three notions of time:

- **write time**: when a message is durably persisted
- **story time**: when the described event actually occurred
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

The goal is to avoid flattening all these pressures into one retrieval path and one unconditional answer policy.

## 4. Method

### 4.1 Memory planes

EchoMemory-MM uses five planes:

1. **Session stream**: append-only interaction and observation history
2. **Atomic plane**: fact, event, relation, and plan atoms
3. **Temporal tree**: chronology-oriented abstraction blocks
4. **Relation graph**: event, entity, fact, and image-evidence nodes with typed edges
5. **Readiness plane**: lifecycle state from persisted to answerable

### 4.2 Dual-backbone routing

EchoMemory-MM defines two primary retrieval backbones:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy and visually grounded queries

The non-primary backbone can still contribute supporting evidence. A temporal query may still need graph support for participants or linked facts, but its primary route should remain chronology-aware.

### 4.3 Shared evidence contract

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

### 4.4 Readiness-aware answerability

Persisted memory is weaker than answerable memory. A system should not answer simply because a write completed. It should answer only when the required memory structures are ready enough for faithful retrieval. EchoMemory-MM therefore treats readiness as a correctness mechanism, not merely an operational convenience.

### 4.5 Coverage-aware gating

The contract is useful even before answer generation. A high-confidence primary hit should not terminate retrieval if the planned evidence contract is still incomplete. We call this **coverage-aware gating**. Its role is to prevent the system from confusing confidence with sufficiency.

### 4.6 Type-aware second pass

If the contract exposes which evidence families are missing, retrieval expansion itself can become structured. Instead of issuing a fixed graph-only supplement, EchoMemory-MM performs a **type-aware second pass**:

- missing chronology support -> probe the temporal tree or episode-like support
- missing fact grounding -> probe the atomic / fact reader
- missing graph or entity linkage -> probe the graph reader
- missing image-grounded support -> probe image-evidence-bearing graph nodes

This turns second-pass expansion into a policy aligned with missing evidence, rather than a generic retry.

## 5. Code Anchors

The current EchoMemory repository already contains most of the needed substrate:

- session lifecycle and append-only storage:
  - `session_service.py`
- atom-first incremental extraction:
  - `atom_first_pipeline.py`
- organized projection to overview / entities / events / temporal tree:
  - `organized_projector/projector.py`
- graph synchronization and image evidence:
  - `graph/sync.py`
- query routing and retrieval:
  - `search_service.py`
  - `query_planner.py`
  - `episode/retriever.py`
- contract-aware policy layer:
  - `index_engine/policy/evidence_contract.py`
  - `index_engine/policy/retrieval_gating.py`
  - `index_engine/policy/self_check.py`

The key engineering gap is no longer “missing structure”, but stronger runtime integration between these planes.

## 6. Experiments

### 6.1 Mechanism-level evidence package

We evaluate EchoMemory-MM through a code-backed package rather than a single benchmark table. The package currently includes:

- anchored temporal ablation
- relation-backbone ablation
- dual-backbone benchmark
- readiness on/off ablation
- self-check v2 experiment
- 21-case real-code family subset
- coverage-aware gating ablation
- type-aware second-pass ablation

### 6.2 Key findings

**Temporal routing.** In the anchored temporal ablation, tree-only and dual-backbone both outperform graph-only on relative-time questions. This supports the claim that chronology-heavy queries prefer a temporal backbone.

**Relation routing.** In the relation-backbone ablation, graph-only and dual-backbone outperform tree-only on relation-heavy questions. This supports the claim that relation-heavy queries prefer graph-grounded evidence.

**Balanced retrieval.** In the dual-backbone benchmark, tree-only, graph-only, and dual-backbone reach `3/12`, `5/12`, and `8/12`, respectively. This suggests that tree and graph cover different failure modes, and that routing between them is more stable than using either alone.

**Readiness as correctness.** In the readiness ablation, `baseline`, `temporal_graph`, and `full` reach `1/5`, `4/5`, and `5/5`. This supports the claim that persistence is weaker than answerability.

**Coverage-aware gating.** In the nano gating ablation, keyword-level relevance stays fixed at `6/6`, but contract-complete cases improve from `1/6` to `2/6`. The gain is small but instructive: confidence alone can preserve surface relevance while still leaving the evidence contract incomplete.

**Type-aware second pass.** The strongest new mechanism-level result comes from the type-aware second-pass ablation. On a five-case study:

- one pass: `1/5` contract-complete
- graph-only second pass: `3/5`
- type-aware second pass: `5/5`

The improvement pattern is interpretable. Graph-only expansion fixes tree-first temporal questions by adding event support. Type-aware expansion further fixes graph-primary temporal-relational questions because the missing signal is chronology support rather than more graph evidence. This is the clearest evidence so far that second-pass expansion should be aligned with the missing evidence family.

### 6.3 Real-code behavior

Beyond the nano package, we also ran a 21-case real-code family subset directly against the current `SearchService`. The subset spans temporal, relational, temporal-relational, visual, and factual queries. It is intentionally smaller than a benchmark table, but it confirms that the same policy signals are already visible in the actual retrieval path rather than only in toy code.

The current subset passes `21/21` against family-level expectations. This does not justify benchmark-scale superiority claims, but it materially strengthens the systems claim that the policy ideas are already observable in the real stack.

## 7. Discussion

The present evidence supports a mechanism-level claim, not a benchmark-scale claim. Specifically, it supports the claim that long-horizon multimodal memory should not be flattened into one retrieval assumption or one unconditional answer policy.

Three lessons stand out.

First, chronology and relation are structurally different pressures. They should not be forced through the same primary evidence route.

Second, durability and answerability are different states. Treating persisted memory as immediately answerable is a correctness mistake.

Third, answer-time policy matters even after retrieval. Strong primary evidence does not imply sufficient evidence, and “do another pass” is only truly useful when the expansion policy is aligned with the missing evidence family.

## 8. Limitations

The current work has clear limitations:

1. experiments remain mechanism-oriented rather than benchmark-scale
2. multimodal evidence paths are architecturally promising but empirically thin
3. latency and cost are not yet formalized
4. runtime integration across all memory planes is still incomplete
5. real-code evidence is currently a focused subset rather than a full benchmark track

These limitations are real, but they are also useful because they keep the claim boundary honest.

## 9. Conclusion

EchoMemory-MM advances a simple but under-emphasized thesis:

> temporal questions, relation-heavy questions, readiness constraints, and answer-time evidence policy should not be collapsed into one flat retrieval assumption.

The current EchoMemory repository already contains the major structural planes needed for this direction, and the current nano experiments plus the real-code family subset support the same architectural conclusion from multiple angles. In particular, the newer coverage-aware gating and type-aware second-pass results suggest that the key policy question is not merely whether to expand retrieval, but whether expansion is aligned with the missing evidence family. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.
