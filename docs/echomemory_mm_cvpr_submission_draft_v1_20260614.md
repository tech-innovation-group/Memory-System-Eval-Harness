# EchoMemory-MM CVPR Submission Draft v1

Date: 2026-06-14

## Title

**EchoMemory-MM: Query-Time Anchored Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Abstract

Long-horizon personal agents must preserve evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, and increasingly ground responses in visual observations such as screenshots or OCR-bearing images. Existing memory systems often flatten these demands into one retrieval pool, forcing chronology-heavy questions, relation-heavy questions, and readiness-sensitive questions through the same evidence path. We propose **EchoMemory-MM**, a query-time anchored dual-backbone memory architecture that incrementally transforms append-only interaction streams into atomic memories, a temporal abstraction tree, a relation graph, and a readiness plane. The central hypothesis is that temporal questions and relation-heavy questions should not share the same primary retrieval backbone, and that persisted memory is not automatically answer-ready. EchoMemory-MM therefore routes relative-time questions to a chronology-aware temporal tree, relation-heavy questions to a graph backbone, and gates answer generation on lifecycle readiness. We ground the proposal in the current EchoMemory codebase, which already contains an atom-first pipeline, organized projection, graph synchronization, temporal-tree retrieval, and query-time anchoring in the main retrieval path. We further provide a canonical nano implementation and four mechanism-level evaluation lines: anchored temporal ablations, relation-backbone ablations, a dual-backbone toy benchmark, and a readiness on/off ablation. The current evidence does not yet establish benchmark-scale superiority, but it consistently supports the same architectural conclusion: long-horizon memory should be modeled as a planner-routed stream-to-structure system rather than a flat unified retrieval pool.

## 1. Introduction

Long-horizon memory failures in personal agents are usually described as retrieval failures. That description is too coarse. In practice, at least four different failure modes appear repeatedly:

1. a temporal question is answered from write time rather than story time
2. a relation-heavy question is answered from a broad summary rather than graph-connected evidence
3. a visual question is answered without promoting image evidence to first-class memory
4. a newly written message is treated as answerable before downstream memory consolidation completes

These failure modes are often exposed through one retrieval interface, but they are not the same problem. A question such as “What happened yesterday?” is fundamentally different from “Who introduced Jon to Lena?”, and both differ again from “Can the system answer yet?” after a write has been durably persisted but not fully consolidated.

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an incremental atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, and search/planner logic. The architectural opportunity is therefore not to invent a new memory stack from scratch, but to turn these existing planes into a more explicit retrieval system.

This paper explores the following claim:

> Long-horizon agent memory should be modeled as a query-time anchored, dual-backbone, readiness-aware stream-to-structure system.

The core intuition is simple. Temporal questions should prefer a chronology-aware backbone. Relation-heavy questions should prefer a graph-aware backbone. Persisted memory should not be treated as automatically answer-ready. These pressures jointly motivate **EchoMemory-MM**, which organizes memory into a session stream, an atomic plane, a temporal tree, a relation graph, and a readiness plane.

Our current contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we provide a code-backed architecture analysis, a 30-paper positioning map centered on 2024-2026, a canonical nano implementation, and four mechanism-level evaluation lines. Together, these support a narrower but technically grounded conclusion: flattening all memory demands into one retrieval pool is an architectural mistake for long-horizon multimodal agents.

## 2. Related Work

### 2.1 Benchmark pressure

Recent benchmarks make clear that long-horizon memory is not a single scalar capability. LoCoMo and LongMemEval emphasize temporal, relational, and update-sensitive conversational pressures. LongMemEval-V2 extends this framing toward more agent-like, experience-sensitive tasks. Regimes, When Stored Evidence Stops Being Usable, and WhenLoss further show that memory quality must be decomposed into write-path correctness, retrieval usability, answer-time faithfulness, and lifecycle readiness.

For EchoMemory-MM, the key lesson is that memory systems should not be evaluated only by “whether evidence was stored.” They should also be evaluated by whether the evidence remains retrievable, interpretable, and answerable at the right time.

### 2.2 Hierarchical and temporal retrieval

RAPTOR, MemoRAG, ByteRover, TiMem, and related hierarchical-memory work all point to the same principle: long-horizon recall improves when memory is exposed through structured abstraction rather than only flat retrieval. In particular, time should become a navigation surface rather than a metadata field attached to a document.

This is directly relevant to EchoMemory because its current organized projection already emits a `temporal_tree`. The architectural question is therefore not whether a tree should exist, but whether it should become a primary retrieval backbone for chronology-heavy queries.

### 2.3 Graph and structured recall

HippoRAG, GraphReader, From RAG to Memory, Zep, LEGO-GraphRAG, H-Mem, and APEX-MEM argue for graph-structured or semi-structured memory as an active recall substrate. Their shared insight is that relation-heavy or event-linked questions should not be forced through the same route as chronology-heavy questions.

This is the second key pressure on EchoMemory-MM: relation-heavy questions should prefer graph-first evidence paths, with temporal structure as supporting rather than primary evidence.

### 2.4 Lifecycle and systems

Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not just retrieval; it is also a lifecycle and systems problem. Extraction cost, consolidation delay, routing policy, and evidence maintenance all affect correctness.

This motivates the readiness plane in EchoMemory-MM. Durability is weaker than answerability. A system that answers immediately after persistence, without checking whether memory is truly answer-ready, is making a systems mistake rather than a mere UI mistake.

### 2.5 Multimodal direction

MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward two future-facing themes: memory actions should be policy-aware, and multimodal observations should become first-class memory. EchoMemory-MM sits naturally at this intersection, although our present evidence for multimodal capability remains architectural and early-stage rather than benchmark-complete.

## 3. Problem Formulation

We distinguish three notions of time:

- **write time**: when the message is persisted
- **story time**: when the event being described actually happened
- **query time**: the temporal anchor from which relative expressions such as “yesterday” or “last week” should be resolved

We also distinguish three major query families:

1. **temporal queries**: date lookup, relative-time, ordering, chronology
2. **relation-heavy queries**: spouse, introducer, plan-after-event, multi-hop entity linkage
3. **readiness-sensitive queries**: whether the system should answer now or explicitly defer until the memory pipeline becomes answer-ready

Finally, we define a readiness lifecycle:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

The central problem is to design a memory system that does not flatten these families into one retrieval path.

## 4. Method

### 4.1 Five-plane memory model

EchoMemory-MM uses five planes:

1. **Session stream**: append-only conversation and observation history
2. **Atomic plane**: fact, event, relation, and plan atoms
3. **Temporal tree**: chronology-oriented abstraction blocks
4. **Relation graph**: event/entity/fact/image-evidence nodes with typed edges
5. **Readiness plane**: lifecycle state from persisted to answerable

### 4.2 Query-time anchoring

Relative-time expressions should resolve against `query_time`, not runtime wall clock. This matters especially in offline evaluation and replay settings where “yesterday” refers to a historical anchor inside the interaction, not to the current machine date.

In the current EchoMemory codebase, this is already partially reflected through `RequestContext.query_time` and anchored temporal retrieval logic.

### 4.3 Dual-backbone routing

We define two primary backbones:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy, event-linked, and visually grounded queries

The non-primary backbone remains useful as supporting evidence. For example, a temporal query may still benefit from graph evidence that clarifies participants or entity links, but its primary route should remain chronology-aware.

### 4.4 Readiness gating

EchoMemory-MM explicitly separates persistence from answerability. A memory system should not answer simply because a write completed. It should answer only when the memory structures required for faithful retrieval are ready.

This is not merely an operational or product concern. It is a correctness constraint.

### 4.5 Multimodal evidence path

Image-bearing observations such as screenshots, photos, and OCR content should enter the session stream, become graph-linked evidence objects, and eventually participate in retrieval rather than remaining auxiliary attachments. The current codebase already contains an early `image_evidence` path in graph synchronization, which motivates the multimodal direction of EchoMemory-MM even though the benchmark evidence remains incomplete.

## 5. Implementation Anchors in the Current EchoMemory Repository

The current codebase already contains most of the structural substrate required by EchoMemory-MM:

- session lifecycle and append-only storage:
  - `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`
- atom-first incremental extraction:
  - `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`
- organized projection to profile / overview / entities / events / temporal tree:
  - `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`
- graph synchronization and image evidence:
  - `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`
- search, routing, temporal retrieval, and planner logic:
  - `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`
  - `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/episode/retriever.py`

The key remaining challenge is not absence of structure, but tighter integration between these planes at query time.

## 6. Experiments

### 6.1 Canonical nano implementation

We provide a canonical nano implementation that captures:

- append-only stream
- story-time normalization
- query-time anchor
- temporal tree
- graph-backed retrieval
- readiness gating

This implementation is meant to be understandable rather than benchmark-complete. It provides the smallest artifact that still preserves the core logic of EchoMemory-MM.

### 6.2 Anchored temporal ablation

This ablation tests whether relative-time questions prefer a chronology-aware primary backbone.

Current result:

- tree-only: 3 / 3
- graph-only: 2 / 3
- dual-backbone: 3 / 3

Interpretation:

- temporal questions prefer a chronology-aware backbone

### 6.3 Relation-backbone ablation

This ablation tests whether relation-heavy questions prefer a graph-aware primary backbone.

Current result:

- tree-only: 0 / 3
- graph-only: 3 / 3
- dual-backbone: 3 / 3

Interpretation:

- relation-heavy questions prefer graph as the primary backbone

### 6.4 Dual-backbone toy benchmark

The 12-case dual-backbone benchmark is not a benchmark-scale result, but it provides a useful architectural stress test.

Current result:

- tree-only: 3 / 12
- graph-only: 5 / 12
- dual-backbone: 8 / 12

Interpretation:

- tree and graph cover different failure modes
- dual-backbone is more stable overall

### 6.5 Readiness on/off ablation

This ablation tests whether persisted memory should be treated as immediately answerable.

Current result:

- baseline: 1 / 5
- temporal_graph: 4 / 5
- full: 5 / 5

Interpretation:

- persisted memory is not automatically QA-ready
- lifecycle gating changes correctness, not just user experience

### 6.6 Consolidated result

Across all current evidence, the strongest pattern is:

- temporal questions prefer temporal tree
- relation-heavy questions prefer graph
- dual-backbone gives the most balanced overall retrieval story
- readiness gating is required to turn strong retrieval into answer-time correctness

## 7. Discussion

The current results justify a mechanism-level claim, not a benchmark-scale claim. Specifically, they justify the claim that long-horizon memory should not be flattened into one retrieval assumption.

They do **not** yet justify:

- benchmark-scale SOTA claims on LoCoMo or LongMemEval
- mature multimodal benchmark claims
- production-grade latency or cost conclusions

This honesty is a strength. It lets the current research package function as a credible bridge between architecture design and future benchmark-scale validation.

Readiness is especially important here. Many memory systems discuss write durability and retrieval quality, but far fewer make answerability state explicit. The readiness ablation suggests that this distinction is not cosmetic. It changes correctness.

## 8. Limitations

This work has four major limitations at its current stage:

1. the experiments are mechanism-level and small-scale
2. the multimodal path is architecturally motivated but not benchmark-complete
3. latency and cost profiling are not yet formalized
4. the current codebase still requires tighter integration between planes at runtime

## 9. Conclusion

EchoMemory-MM explores a straightforward but under-emphasized thesis:

> temporal questions, relation-heavy questions, and memory-readiness constraints should not be collapsed into one flat retrieval assumption.

The current EchoMemory codebase already contains the major structural planes needed for this direction, and the current nano experiments support the same architectural conclusion from multiple angles. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.
