# EchoMemory-MM CVPR Main Submission Draft v16

Date: 2026-06-17

## Title

**EchoMemory-MM: Contract-Driven Dual-Backbone Multimodal Temporal Graph Memory with Topic-Centered Middle-Layer Retrieval**

## Abstract

Long-horizon personal agents must remember evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, track cross-session topic evolution, and increasingly ground responses in visual observations such as screenshots or OCR-bearing images. Existing memory systems often flatten these demands into a single retrieval pool, forcing chronology-heavy, relation-heavy, longitudinal, visual, and readiness-sensitive queries through the same evidence path. We propose **EchoMemory-MM**, a contract-driven memory architecture that organizes append-only interaction streams into atomic memories, a topic-centered middle layer, a temporal abstraction tree, a relation graph, a readiness plane, and a shared evidence contract. EchoMemory-MM routes relative-time questions to a chronology-aware temporal tree, relation-heavy and visually grounded questions to a graph backbone, and longitudinal cross-session questions to a topic-centered dossier layer. It further gates answer generation on lifecycle readiness, prevents premature stopping via coverage-aware retrieval gating, and expands supporting evidence using a type-aware second pass keyed to missing evidence families rather than fixed graph-only retries. We ground the proposal in the current EchoMemory codebase and support it with a code-backed evidence package spanning three-clock temporal ablations, dual-backbone ablations, topic-dossier middle-layer ablations, a no-hint topic-canonicalization ablation, a dossier-level dual ablation separating grouping from selection, a topic-dossier paraphrase stress benchmark, answer-time self-check experiments, a generic topic-induction benchmark, an answerability-gate benchmark, a 21-case real-code family subset over the current `SearchService`, and a query-family paraphrase robustness benchmark. Across these lines, a consistent pattern emerges: chronology-heavy questions prefer temporal routing, relation-heavy and visual questions prefer graph routing, longitudinal questions benefit from a topic-centered middle layer, persistence is weaker than answerability, contract completeness is necessary but not sufficient for answerability, confidence should not substitute for evidence sufficiency, and paraphrase robustness depends on generic query-family routing rather than benchmark-specific trigger words. While the current evidence does not establish benchmark-scale superiority, it supports a narrower but stronger conclusion: long-horizon memory should be modeled as a contract-driven, planner-routed, stream-to-structure system with explicit answer-time policy, explicit time semantics, explicit lifecycle/readiness state, and an explicit middle layer for cross-session topic evolution rather than as a flat unified retrieval pool.

## 1. Introduction

Long-horizon memory failures in personal agents are often described as retrieval failures. This framing is too coarse. In practice, at least five different failure modes recur:

1. a temporal question is answered from write time rather than story time
2. a relation-heavy question is answered from broad summaries rather than graph-connected evidence
3. a visually grounded question is answered without elevating image evidence to first-class memory
4. newly persisted information is treated as answerable before downstream consolidation completes
5. retrieval stops early because confidence is high even though the evidence family is incomplete

These are not the same problem. A question such as “What happened yesterday?” should not follow the same evidence path as “Who introduced Jon to Lena?”, and neither should be handled the same way as “Can the system answer now?” immediately after a write. Even after good retrieval, there remains an answer-time policy question: should the system answer, expand supporting evidence, or abstain?

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, and planner-like retrieval logic. The opportunity is therefore not to invent a new stack from scratch, but to turn these existing planes into a more explicit memory architecture and evaluate that architecture in a way that preserves family-specific behavior rather than flattening all success and failure into one pooled retrieval score.

We study the following thesis:

> Long-horizon memory should be modeled as a contract-driven, dual-backbone, readiness-aware, answer-time-governed stream-to-structure system.

This thesis yields five design commitments:

- temporal and relation-heavy questions should not share the same primary backbone
- persisted memory should not be treated as automatically QA-ready
- event occurrence time should not be silently replaced by write time
- evidence sufficiency should be explicit rather than implicit
- supporting retrieval should depend on the missing evidence family

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we contribute:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation family for explanation and ablation
- a generalized method-prototype nano that separates method structure from benchmark surface form
- a focused real-code bridge showing the same policy signals inside the current `SearchService`

Together, these support a narrower but stronger claim: flattening all memory demands into one retrieval assumption is an architectural mistake for long-horizon multimodal agents, and strong retrieval alone is still not enough without answer-time policy and answerability control.

## 2. Related Work

### 2.1 Benchmark pressure

Recent benchmarks such as LoCoMo and LongMemEval show that long-horizon memory is not one scalar capability. Temporal, relational, update-sensitive, and lifecycle-sensitive failures should be evaluated separately. This motivates our family-level framing.

### 2.2 Hierarchical and temporal retrieval

RAPTOR, MemoRAG, ByteRover, TiMem, and related work show that long-horizon recall improves when memory is exposed through structured abstraction rather than a flat retrieval pool. This directly motivates the temporal-tree route and the new topic-centered middle layer.

### 2.3 Graph and structured recall

HippoRAG, GraphReader, Zep, LEGO-GraphRAG, H-Mem, and related systems suggest that relation-heavy or event-linked questions should be grounded in graph-structured recall rather than in summary-only evidence.

### 2.4 Lifecycle and systems

Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not only retrieval. It is also a lifecycle problem involving consolidation delay, routing policy, and evidence maintenance. This directly motivates the readiness plane.

### 2.5 Policy and multimodal direction

MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward two further themes: memory actions should be policy-aware, and multimodal observations should become first-class memory. EchoMemory-MM sits at this intersection.

## 3. Problem Formulation

We distinguish four notions of time:

- **write time**: when a message is durably persisted
- **story time**: when the described event actually occurred
- **mention time**: when an earlier event is revisited inside the interaction stream
- **query time**: the anchor against which relative expressions such as “yesterday” should be resolved

We distinguish five query families:

1. **temporal**: date lookup, relative-time, chronology, ordering
2. **relational**: spouse, introducer, helper, multi-hop linkage
3. **longitudinal**: progress, latest status, cross-session topic evolution
4. **visual**: screenshot / OCR / photo-grounded evidence
5. **readiness-sensitive**: whether the system should answer now

We also define a readiness lifecycle:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

The design objective is to avoid flattening these pressures into one retrieval path and one unconditional answer policy.

## 4. Method

### 4.1 Memory planes

EchoMemory-MM uses six planes:

1. **Session stream**
2. **Atomic plane**
3. **Topic dossier plane**
4. **Temporal tree**
5. **Relation graph**
6. **Readiness plane**

### 4.2 Three-clock temporal semantics

Query-time anchoring is necessary but not sufficient. A memory system can still fail temporal questions if it stores only one timestamp and silently reuses write time as event time. EchoMemory-MM therefore separates:

- `story_time`
- `mention_time`
- `write_time`

The three-clock ablation below isolates this design question directly.

### 4.3 Dual-backbone routing with a middle-layer route

EchoMemory-MM defines:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy and visually grounded queries
- **topic dossier** for longitudinal progress and cross-session evolution queries

The non-primary route remains useful as supporting evidence, but the system should not force all question types through the same evidence path.

### 4.4 Shared evidence contract

Dual-backbone routing alone is not enough if different parts of the stack disagree about what counts as sufficient evidence. We therefore define a lightweight **shared evidence contract** that maps each query family to required evidence families.

Typical examples are:

- temporal: `temporal_tree + event + event_time`
- relational: `graph + fact + path_grounding`
- longitudinal: `topic_dossier + fact`
- visual: `image_evidence + fact`

The contract is intentionally generic and does not depend on dataset-specific entity lists or benchmark-specific keyword tables. This genericity matters both for retrieval and for later answer-time checks: a system should verify whether the retrieved evidence actually supports the requested relation type, answer type, and entity set, not merely whether it looks topically similar.

### 4.5 Readiness-aware answerability

Persisted memory is weaker than answerable memory. A system should not answer simply because a write completed. It should answer only when the relevant memory planes are ready enough for faithful retrieval. We further distinguish **contract-complete** from **answerable**: a result set may satisfy the planned evidence families while still failing to support the requested answer type.

### 4.6 Coverage-aware gating and type-aware second pass

The evidence contract is useful twice:

- before answer generation, to prevent confidence-only early stopping
- after primary retrieval, to decide which supporting reader should be expanded

Instead of issuing a fixed graph-only retry, EchoMemory-MM performs a **type-aware second pass** aligned with the missing evidence family. After retrieval, an answerability gate checks whether the candidate answer is actually supported by the evidence shape implied by the query family.

## 5. Code Anchors

The current EchoMemory repository already contains most of the required substrate:

- session lifecycle and append-only storage: `index_engine/session_service.py`
- atom-first extraction: `workers/atom_first_pipeline.py`
- organized projection and topic dossier support: `workers/organized_projector/projector.py`
- graph synchronization and image evidence: `index_engine/graph/sync.py`
- query routing and retrieval: `index_engine/search_service.py`
- planner logic: `index_engine/planner/query_planner.py`
- evidence contract: `index_engine/policy/evidence_contract.py`
- retrieval gating: `index_engine/policy/retrieval_gating.py`
- answer-time self-check: `index_engine/policy/self_check.py`

The key engineering gap is no longer missing structure, but stronger runtime integration between these planes.

## 6. Experiments

### 6.1 Unified findings at a glance

Before reading the full evidence table, the current package supports five higher-level findings.

| Design pressure | Best-supported structural response | Strongest evidence line | Representative result |
| --- | --- | --- | --- |
| Time is not one timestamp | Preserve `story_time`, `mention_time`, and `write_time`; route chronology-heavy queries to a temporal tree | Three-clock temporal-semantics ablation | `0/4 -> 4/4 -> 4/4` |
| Longitudinal questions need a middle layer | Add a topic-centered dossier layer between global overview and flat atoms | Topic-dossier ablation + generic topic induction + no-hint canonicalization + dual ablation + paraphrase stress | `1/5 -> 3/5 -> 4/5`; `0/4 -> 4/4`; `5/16 -> 14/16 -> 16/16` |
| One primary backbone is not enough | Use temporal tree for chronology-heavy queries and graph for relation-heavy / visual queries | Anchored temporal, relation-backbone, and dual-backbone ablations | temporal `3/3`, relation `3/3`, dual `8/12` |
| Retrieval success is weaker than answerability | Add readiness checks, evidence contracts, and answer-time self-check | Readiness ablation + self-check v2 + answerability gate | `1/5 -> 5/5`; `4/8 -> 8/8`; `2/6 -> 6/6` |
| Generalization should come from query-family routing, not benchmark cues | Keep routing generic and let missing evidence types drive second pass | Paraphrase benchmark + type-aware second pass | `8/15 -> 15/15`; contract-complete `1/5 -> 5/5` |

Taken together, these lines support a single systems claim: **long-horizon memory works better when the stack is treated as a planner-routed, stream-to-structure system with typed middle layers and answer-time control, rather than as a flat retrieval pool**.

### 6.2 Main-results snapshot

| Evidence line | Compared variants | Main result | Why it matters |
| --- | --- | --- | --- |
| Three-clock temporal-semantics ablation | write-time only / event+mention / three-clock | `0/4` / `4/4` / `4/4` | Temporal correctness depends on preserving event time, not only on routing. |
| Generalized method-prototype nano | flat text / primary only / contract-aware | `4/6` / `2/6` / `6/6` | Wrong evidence shape is a stronger failure signal than missing keywords. |
| Anchored temporal ablation | tree-only / graph-only / dual | `3/3` / `2/3` / `3/3` | Relative-time questions prefer chronology-aware routing. |
| Relation-backbone ablation | tree-only / graph-only / dual | `0/3` / `3/3` / `3/3` | Relation-heavy questions prefer graph grounding. |
| Topic-dossier ablation | overview-only / atom-only / topic-dossier | `1/5` / `3/5` / `4/5` | Longitudinal queries need a middle layer between overview and flat atoms. |
| Generic topic-induction benchmark | topic-hint / no topic-hint | `5/5` / `5/5` | Generic topic induction can preserve task behavior without topic-specific hinting, although induced topic labels remain coarse. |
| No-hint topic-canonicalization ablation | explicit hint / naive no hint / canonicalized no hint | `4/4` / `0/4` / `4/4` | The middle layer needs generic topic canonicalization plus longitudinal dossier selection, not only hint removal, when the same topic appears under multiple surface forms. |
| Topic-dossier dual ablation | naive+lexical / naive+longitudinal / canonicalized+lexical / canonicalized+longitudinal | `0/4` / `0/4` / `3/4` / `4/4` | Better selection cannot rescue fragmented dossiers; grouping creates the right dossier object, then longitudinal selection closes the final gap. |
| Topic-dossier paraphrase stress | naive+lexical / canonicalized+lexical / canonicalized+longitudinal | `5/16` / `14/16` / `16/16` | Middle-layer gains survive wording changes; grouping gives most of the lift, and longitudinal selection matters most on looser evolution families such as family-support updates. |
| Dual-backbone benchmark | tree-only / graph-only / dual | `3/12` / `5/12` / `8/12` | Tree and graph cover different failure modes; routing beats either alone. |
| Readiness ablation | baseline / temporal_graph / full | `1/5` / `4/5` / `5/5` | Persistence is weaker than answerability. |
| Self-check v2 | dual-backbone baseline / self-check | `4/8` / `8/8` | Retrieval still needs answer-time policy. |
| Coverage-aware gating | confidence-only / coverage-aware | contract-complete `1/6 -> 2/6` | Confidence should not substitute for evidence sufficiency. |
| Type-aware second pass | one-pass / graph-only / type-aware | contract-complete `1/5 -> 3/5 -> 5/5` | Supporting retrieval works best when aligned with missing evidence families. |
| Multimodal contract ablation | one-pass / contract-aware | contract-complete `2/5 -> 5/5` | Visual queries can look relevant while still lacking structural grounding. |
| Answerability-gate benchmark | legacy answerability / enforced gate | `2/6 -> 6/6` | Contract completeness does not guarantee answerability; unsupported questions should abstain. |
| Query-family paraphrase robustness | baseline cues / improved generic cues | answer-correct `8/15 -> 15/15`; family-correct `13/15 -> 15/15` | Generalization should come from generic family routing, not dataset-specific trigger words. |
| Real-code family subset | current `SearchService` | `21/21` family expectation pass | The same policy signals are visible in the real stack, not only in toy code. |

### 6.3 What the current evidence proves

The evidence supports eight reviewer-facing conclusions.

First, **temporal correctness is partly a schema problem before it is a routing problem**.

Second, **evidence shape is a more revealing failure signal than surface keyword relevance**.

Third, **chronology-heavy, relation-heavy, longitudinal, and visual questions do not want the same primary reader**.

Fourth, **readiness is part of correctness**.

Fifth, **answer-time policy is not optional**.

Sixth, **contract completeness is weaker than answerability**.

Seventh, **generic topic induction is plausible without benchmark-specific topic hints, but stable middle-layer behavior still depends on generic topic canonicalization and longitudinal dossier selection when the same topic appears under multiple surface forms; selection alone cannot rescue fragmented dossiers**.

Eighth, **paraphrase robustness should come from generic query-family routing rather than benchmark-specific wording rules**.

### 6.4 What the current evidence does not prove

The current package does **not** prove:

- benchmark-scale superiority on LoCoMo
- benchmark-scale superiority on LongMemEval
- production-ready multimodal performance
- deployment-grade latency or cost advantages

This claim boundary is intentional.

## 7. Real-Code Bridge

The nano evidence establishes mechanism plausibility, but it does not by itself prove that the same mechanism survives contact with the current EchoMemory retrieval stack. We therefore ran a focused 21-case family subset directly on the current `SearchService`.

Its value is not benchmark accuracy estimation. Its value is that the family-level design pressures argued in the paper become visible in the actual retrieval path.

The current subset passes `21/21` against family-level expectations. This does not justify benchmark-scale superiority claims, but it materially strengthens the systems claim that the policy ideas are already observable in the real stack.

## 8. Limitations

The current work has eight major limitations:

1. experiments remain small-scale and mechanism-oriented
2. the new topic-dossier middle layer is integrated but still minimal
3. multimodal evidence paths are still early-stage
4. latency and cost are not yet formalized at deployment scale
5. runtime integration across all planes remains incomplete
6. benchmark-scale LoCoMo / LongMemEval evidence is still future work
7. multimodal retrieval remains structurally promising but empirically thinner than the text-only and temporal-relational evidence lines
8. generic topic induction in the nano reference now preserves the toy benchmark behavior, but induced topic labels remain semantically coarser than explicit topic hints and still benefit from stronger canonicalization plus dossier-level longitudinal scoring

## 9. Conclusion

EchoMemory-MM advances a simple thesis:

> temporal questions, relation-heavy questions, longitudinal questions, readiness constraints, and answer-time evidence policy should not be collapsed into one flat retrieval assumption.

The current EchoMemory repository already contains the major structural planes needed for this direction, and the current nano experiments plus the real-code family subset support the same architectural conclusion from multiple angles. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.
