# EchoMemory-MM CVPR Submission Draft v4

Date: 2026-06-14

## Title

**EchoMemory-MM: Query-Time Anchored Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Abstract

Long-horizon personal agents must preserve evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, and increasingly ground responses in visual observations such as screenshots or OCR-bearing images. Existing memory systems often flatten these demands into one retrieval pool, forcing chronology-heavy, relation-heavy, and readiness-sensitive queries through the same evidence path. We propose **EchoMemory-MM**, a query-time anchored dual-backbone memory architecture that transforms append-only interaction streams into atomic memories, a temporal abstraction tree, a relation graph, and a readiness plane. The key idea is that temporal questions and relation-heavy questions should not share the same primary retrieval backbone, and that persisted memory is not automatically answer-ready. EchoMemory-MM therefore routes relative-time questions to a chronology-aware temporal tree, relation-heavy questions to a graph backbone, and gates answer generation on lifecycle readiness. We ground this proposal in the current EchoMemory repository, which already contains an atom-first pipeline, organized projection, graph synchronization, temporal-tree retrieval, and query-time anchoring in the main retrieval path. We further provide a canonical nano implementation and four mechanism-level evaluation lines: anchored temporal ablations, relation-backbone ablations, a dual-backbone toy benchmark, and a readiness on/off ablation. While the current evidence does not establish benchmark-scale superiority, it consistently supports the same architectural conclusion: long-horizon memory should be modeled as a planner-routed stream-to-structure system rather than a flat unified retrieval pool.

## 1. Introduction

Long-horizon memory failures in personal agents are often described as retrieval failures. This description is too coarse. In practice, several distinct failures recur:

1. a temporal question is answered from write time rather than story time
2. a relation-heavy question is answered from broad summary text rather than graph-connected evidence
3. a visually grounded question is answered without promoting image evidence to first-class memory
4. a newly written message is treated as answerable before downstream consolidation completes

These failure modes are usually exposed through one retrieval interface, but they are not the same problem. A question such as “What happened yesterday?” should not be handled in the same way as “Who introduced Jon to Lena?”, and neither should be handled the same way as “Can the system answer yet?” immediately after a write completes.

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an incremental atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, and planner-like retrieval logic. The opportunity is therefore not to invent a new stack from scratch, but to turn these existing planes into a more explicit memory architecture.

We study the following thesis:

> Long-horizon memory should be modeled as a query-time anchored, dual-backbone, readiness-aware stream-to-structure system.

The intuition is simple. Temporal questions should prefer a chronology-aware backbone. Relation-heavy questions should prefer a graph-aware backbone. Persisted memory should not be treated as automatically answer-ready. These pressures jointly motivate **EchoMemory-MM**, which organizes memory into a session stream, an atomic plane, a temporal tree, a relation graph, and a readiness plane.

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we provide:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation
- four mechanism-level evaluation lines that all support the same systems conclusion

Together, these support a narrower but grounded claim: flattening all memory demands into a single retrieval pool is an architectural mistake for long-horizon multimodal agents.

## 2. Related Work

Recent work places at least five distinct pressures on long-horizon memory systems.

**Benchmark pressure.** LoCoMo and LongMemEval show that temporal, relational, update-sensitive, and cross-session questions should be evaluated explicitly rather than collapsed into generic QA. LongMemEval-V2, Regimes, When Stored Evidence Stops Being Usable, and WhenLoss further suggest that memory quality should be decomposed into write correctness, retrieval usability, answer-time faithfulness, and lifecycle readiness.

**Hierarchical and temporal retrieval.** RAPTOR, MemoRAG, ByteRover, TiMem, and related hierarchical-memory work support the same principle: long-horizon recall improves when memory is exposed through structured abstraction rather than only flat retrieval. Time should become a navigation surface rather than a passive field.

**Graph and structured recall.** HippoRAG, GraphReader, From RAG to Memory, Zep, LEGO-GraphRAG, H-Mem, and APEX-MEM argue for graph-structured or semi-structured memory as an active recall substrate. Their shared lesson is that relation-heavy or event-linked questions should not be forced through the same route as chronology-heavy questions.

**Lifecycle and systems.** Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not just retrieval. It is also a lifecycle problem involving extraction cost, consolidation delay, routing policy, and evidence maintenance. This directly motivates a readiness plane.

**Multimodal direction.** MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward policy-aware memory actions and first-class multimodal evidence. EchoMemory-MM is positioned at this intersection, although our current multimodal evidence remains architectural rather than benchmark-complete.

## 3. Problem Formulation

We distinguish three notions of time:

- **write time**: when a message is durably persisted
- **story time**: when the described event actually occurred
- **query time**: the temporal anchor against which relative expressions such as “yesterday” should be resolved

We also distinguish three major query families:

1. **temporal queries**: date lookup, relative-time, ordering, chronology
2. **relation-heavy queries**: spouse, introducer, plan-after-event, multi-hop entity linkage
3. **readiness-sensitive queries**: whether the system should answer now or defer until memory becomes answer-ready

Finally, we define a readiness lifecycle:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

The design objective is to prevent these pressures from being flattened into one retrieval path.

## 4. Method

### 4.1 Five-plane memory model

EchoMemory-MM uses five planes:

1. **Session stream**: append-only interaction and observation history
2. **Atomic plane**: fact, event, relation, and plan atoms
3. **Temporal tree**: chronology-oriented abstraction blocks
4. **Relation graph**: event, entity, fact, and image-evidence nodes with typed edges
5. **Readiness plane**: lifecycle state from persisted to answerable

### 4.2 Query-time anchoring

Relative-time expressions should resolve against `query_time`, not runtime wall clock. This matters especially in replay and offline evaluation settings where “yesterday” refers to a historical anchor inside the interaction rather than to the machine’s current date.

### 4.3 Dual-backbone routing

EchoMemory-MM defines two primary retrieval backbones:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy, event-linked, and visually grounded queries

The non-primary backbone remains useful as supporting evidence. A temporal query may still benefit from graph evidence that clarifies participants, but its primary route should remain chronology-aware.

### 4.4 Readiness gating

EchoMemory-MM explicitly separates persistence from answerability. A memory system should not answer simply because a write completed. It should answer only when the memory structures required for faithful retrieval are ready.

### 4.5 Multimodal evidence path

Image-bearing observations such as screenshots, photos, and OCR content should enter the session stream, become graph-linked evidence objects, and participate in retrieval rather than remaining auxiliary attachments. The current codebase already contains an early `image_evidence` path in graph synchronization, which motivates the multimodal direction of EchoMemory-MM even though benchmark evidence remains incomplete.

## 5. Implementation Anchors

The current EchoMemory repository already contains most of the structural substrate required by EchoMemory-MM:

- session lifecycle and append-only storage:
  - `session_service.py`
- atom-first incremental extraction:
  - `atom_first_pipeline.py`
- organized projection to profile / overview / entities / events / temporal tree:
  - `organized_projector/projector.py`
- graph synchronization and image evidence:
  - `graph/sync.py`
- search, routing, temporal retrieval, and planner logic:
  - `search_service.py`
  - `episode/retriever.py`

The key engineering gap is not absence of structure, but tighter integration between these planes at query time.

## 6. Experiments

### 6.1 Canonical nano implementation

We provide a canonical nano implementation that captures the smallest faithful version of EchoMemory-MM: append-only stream, story-time normalization, query-time anchor, temporal tree, graph-backed retrieval, and readiness gating.

### 6.2 Anchored temporal ablation

This ablation tests whether relative-time questions prefer a chronology-aware primary backbone:

- tree-only: **3 / 3**
- graph-only: **2 / 3**
- dual-backbone: **3 / 3**

### 6.3 Relation-backbone ablation

This ablation tests whether relation-heavy questions prefer a graph-aware primary backbone:

- tree-only: **0 / 3**
- graph-only: **3 / 3**
- dual-backbone: **3 / 3**

### 6.4 Dual-backbone benchmark

The 12-case dual-backbone benchmark is not benchmark-scale, but it provides an architectural stress test:

- tree-only: **3 / 12**
- graph-only: **5 / 12**
- dual-backbone: **8 / 12**

### 6.5 Readiness ablation

This ablation tests whether persisted memory should be treated as immediately answerable:

- baseline: **1 / 5**
- temporal_graph: **4 / 5**
- full: **5 / 5**

### 6.6 Consolidated interpretation

Across all four lines, the pattern is consistent:

- temporal questions prefer temporal tree
- relation-heavy questions prefer graph
- dual-backbone provides the most balanced retrieval story
- readiness gating is required to turn strong retrieval into answer-time correctness

## 7. Discussion

The present evidence justifies a mechanism-level claim, not a benchmark-scale claim. Specifically, it justifies the claim that long-horizon memory should not be flattened into one retrieval assumption.

It does **not** yet justify:

- benchmark-scale SOTA claims on LoCoMo or LongMemEval
- mature multimodal benchmark claims
- production-grade latency or cost conclusions

This boundary is a strength rather than a weakness. It lets the current package function as a credible bridge between architecture design and benchmark-scale validation.

Readiness is especially important here. Many memory systems discuss write durability and retrieval quality, but far fewer make answerability state explicit. Our readiness ablation suggests that this distinction is not cosmetic. It changes correctness.

## 8. Limitations

The current work has four major limitations:

1. experiments remain small-scale and mechanism-oriented
2. multimodal evidence paths are early-stage
3. latency and cost are not yet formalized
4. runtime integration across planes remains incomplete

## 9. Conclusion

EchoMemory-MM advances a simple but under-emphasized thesis:

> temporal questions, relation-heavy questions, and memory-readiness constraints should not be collapsed into one flat retrieval assumption.

The current EchoMemory repository already contains the major structural planes needed for this direction, and the current nano experiments support the same architectural conclusion from multiple angles. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.
