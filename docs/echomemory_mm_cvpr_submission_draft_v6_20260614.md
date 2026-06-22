# EchoMemory-MM CVPR Submission Draft v6

Date: 2026-06-14

## Title

**EchoMemory-MM: Query-Time Anchored Dual-Backbone Multimodal Temporal Graph Memory with Readiness-Aware Retrieval Self-Check**

## Abstract

Long-horizon personal agents must preserve evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, and increasingly ground responses in visual observations such as screenshots or OCR-bearing images. Existing memory systems often flatten these demands into one retrieval pool, forcing chronology-heavy, relation-heavy, visual, and readiness-sensitive queries through the same evidence path. We propose **EchoMemory-MM**, a query-time anchored dual-backbone memory architecture that organizes append-only interaction streams into atomic memories, a temporal abstraction tree, a relation graph, and a readiness plane. The core idea is that temporal questions and relation-heavy questions should not share the same primary retrieval backbone, and that persisted memory is not automatically answer-ready. EchoMemory-MM therefore routes relative-time questions to a chronology-aware temporal tree, relation-heavy and visually grounded queries to a graph backbone, gates answer generation on lifecycle readiness, and adds a lightweight retrieval self-check that decides whether primary-backbone evidence is sufficient, whether supporting evidence should be expanded, or whether the system should abstain. We ground this proposal in the current EchoMemory codebase, which already contains an atom-first pipeline, organized projection, graph synchronization, temporal-tree generation, and planner-like retrieval logic. We further provide a canonical nano implementation and five mechanism-level evaluation lines: anchored temporal ablations, relation-backbone ablations, a dual-backbone toy benchmark, a readiness on/off ablation, and a new self-check v2 experiment. Across these lines, temporal-only, graph-only, dual-backbone, readiness-gated, and self-check-enabled variants exhibit a consistent pattern: time questions prefer chronology-aware routing, relation-heavy and visual questions prefer graph-aware routing, readiness gating is required to convert stored evidence into answer-time correctness, and retrieval self-check improves abstention and supporting-evidence expansion behavior. While the current evidence does not establish benchmark-scale superiority, it supports a narrower but robust conclusion: long-horizon memory should be modeled as a planner-routed, stream-to-structure system with explicit answer-time policy rather than a flat unified retrieval pool.

## 1. Introduction

Long-horizon memory failures in personal agents are often described as retrieval failures. This framing is too coarse. In practice, at least five different failures repeatedly appear:

1. a temporal question is answered from write time rather than story time
2. a relation-heavy question is answered from broad summaries rather than graph-connected evidence
3. a visually grounded question is answered without elevating image evidence to first-class memory
4. a newly written message is treated as answerable before downstream consolidation completes
5. a system with partially relevant retrieval still answers too early instead of expanding supporting evidence or abstaining

These failures are usually exposed through a single retrieval interface, but they are not the same problem. A question such as “What happened yesterday?” should not be handled in the same way as “Who introduced Jon to Lena?”, and neither should be handled the same way as “Can the system answer yet?” immediately after a write completes. Even after good retrieval, there remains an answer-time policy question: should the system answer now, search one more structured view, or abstain?

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an incremental atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, and planner-like retrieval logic. The opportunity is therefore not to invent a new stack from scratch, but to turn these existing planes into a more explicit memory architecture.

We study the following thesis:

> Long-horizon memory should be modeled as a query-time anchored, dual-backbone, readiness-aware, self-checking stream-to-structure system.

The intuition is simple. Temporal questions should prefer a chronology-aware backbone. Relation-heavy and visually grounded questions should prefer a graph-aware backbone. Persisted memory should not be treated as automatically answer-ready. Finally, even after retrieval, the system should explicitly inspect whether the evidence shape matches the query family before answering. These pressures jointly motivate **EchoMemory-MM**, which organizes memory into a session stream, an atomic plane, a temporal tree, a relation graph, a readiness plane, and a lightweight answer-time self-check policy.

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we provide:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation family
- five mechanism-level evaluation lines that support the same systems conclusion

Together, these support a narrower but grounded claim: flattening all memory demands into a single retrieval pool is an architectural mistake for long-horizon multimodal agents, and strong retrieval alone is still not enough without answer-time policy.

## 2. Related Work

### 2.1 Benchmark pressure

Recent benchmarks make clear that long-horizon memory is not one scalar capability. LoCoMo and LongMemEval emphasize temporal, relational, update-sensitive, and cross-session conversational pressures. LongMemEval-V2 extends this framing toward more agent-like tasks. Regimes, When Stored Evidence Stops Being Usable, and WhenLoss further argue that memory quality should be decomposed into write-path correctness, retrieval usability, answer-time faithfulness, and lifecycle readiness.

For EchoMemory-MM, the key lesson is that memory systems should not be judged only by whether evidence was stored. They should also be judged by whether the evidence remains retrievable, interpretable, answerable at the right time, and supported by enough evidence to justify a response.

### 2.2 Hierarchical and temporal retrieval

RAPTOR, MemoRAG, ByteRover, TiMem, and related hierarchical-memory work support the same principle: long-horizon recall improves when memory is exposed through structured abstraction rather than only flat retrieval. Time should become a navigation surface rather than a passive metadata field.

This is directly relevant to EchoMemory because the current organized projection already emits a temporal tree. The architectural question is therefore not whether a tree should exist, but whether it should become the primary backbone for chronology-heavy queries.

### 2.3 Graph and structured recall

HippoRAG, GraphReader, From RAG to Memory, Zep, LEGO-GraphRAG, H-Mem, and APEX-MEM argue for graph-structured or semi-structured memory as an active recall substrate. Their shared lesson is that relation-heavy or event-linked questions should not be forced through the same route as chronology-heavy questions.

This is the second pressure behind EchoMemory-MM: relation-heavy and visual questions should prefer graph-first evidence paths, with temporal structure used as support rather than as the primary evidence route.

### 2.4 Lifecycle and systems

Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not just retrieval. It is also a lifecycle and systems problem involving extraction cost, consolidation delay, routing policy, and evidence maintenance.

This motivates the readiness plane in EchoMemory-MM. Durability is weaker than answerability. A system that answers immediately after persistence, without checking whether memory is truly answer-ready, is making a systems mistake.

### 2.5 Policy and multimodal direction

MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward two future-facing themes: memory actions should be policy-aware, and multimodal observations should become first-class memory. EchoMemory-MM sits naturally at this intersection. Our current multimodal evidence remains architectural rather than benchmark-complete, but it is already sufficient to motivate image-evidence-first retrieval and lightweight answer-time self-check.

## 3. Problem Formulation

We distinguish three notions of time:

- **write time**: when a message is durably persisted
- **story time**: when the described event actually occurred
- **query time**: the temporal anchor against which relative expressions such as “yesterday” should be resolved

We further distinguish four major query families:

1. **temporal queries**: date lookup, relative-time, ordering, chronology
2. **relation-heavy queries**: spouse, introducer, plan-after-event, multi-hop entity linkage
3. **visual queries**: OCR-bearing photos, screenshots, image-grounded evidence lookup
4. **readiness-sensitive queries**: whether the system should answer now or defer until memory becomes answer-ready

Finally, we define a readiness lifecycle:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

The design objective is to prevent these pressures from being flattened into one retrieval path and one unconditional answer policy.

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

### 4.5 Retrieval self-check

EchoMemory-MM adds a lightweight retrieval self-check between retrieval and answer generation. The self-check inspects whether the evidence shape matches the query family:

- temporal questions expect chronology-shaped evidence such as day or month tree blocks
- relation-heavy questions expect graph-linked event or entity evidence
- visual questions expect image-evidence nodes or direct OCR-bearing support

If the primary backbone does not provide the expected evidence shape, the system expands to supporting backbones. If the evidence remains structurally weak after expansion, the system abstains with `unknown` instead of forcing an answer. This design is intentionally lighter than a second full reasoning pass. Its goal is not to maximize complexity, but to make answer-time policy explicit and auditable.

### 4.6 Multimodal evidence path

Image-bearing observations such as screenshots, photos, and OCR content should enter the session stream, become graph-linked evidence objects, and participate in retrieval rather than remaining auxiliary attachments. The current codebase already contains an early `image_evidence` path in graph synchronization, which motivates the multimodal direction of EchoMemory-MM even though benchmark evidence remains incomplete.

## 5. Code Anchors

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
  - `query_planner.py`
  - `episode/retriever.py`

The key engineering gap is not absence of structure, but tighter integration between these planes at query time and more explicit answer-time policy after retrieval.

## 6. Experiments

### 6.1 Canonical nano implementation

We provide a canonical nano implementation that captures the smallest faithful version of EchoMemory-MM: append-only stream, story-time normalization, query-time anchor, temporal tree, graph-backed retrieval, and readiness gating.

### 6.2 Anchored temporal ablation

This ablation tests whether relative-time questions prefer a chronology-aware primary backbone:

- tree-only: **3 / 3**
- graph-only: **2 / 3**
- dual-backbone: **3 / 3**

This supports the claim that temporal questions prefer a chronology-aware route.

### 6.3 Relation-backbone ablation

This ablation tests whether relation-heavy questions prefer a graph-aware primary backbone:

- tree-only: **0 / 3**
- graph-only: **3 / 3**
- dual-backbone: **3 / 3**

This supports the claim that relation-heavy questions prefer graph as the primary backbone.

### 6.4 Dual-backbone benchmark

The 12-case dual-backbone benchmark is not benchmark-scale, but it provides an architectural stress test:

- tree-only: **3 / 12**
- graph-only: **5 / 12**
- dual-backbone: **8 / 12**

This suggests that tree and graph cover different failure modes and that dual-backbone routing is more balanced overall.

### 6.5 Readiness ablation

This ablation tests whether persisted memory should be treated as immediately answerable:

- baseline: **1 / 5**
- temporal_graph: **4 / 5**
- full: **5 / 5**

This supports the claim that persisted memory is not automatically QA-ready and that lifecycle gating changes correctness rather than merely user experience.

### 6.6 Self-check v2 experiment

The new self-check v2 experiment tests whether a dual-backbone system should always answer directly from primary-backbone retrieval, or whether it should explicitly inspect evidence shape, expand supporting evidence, or abstain.

- dual-backbone baseline: **4 / 8**
- dual-backbone + self-check: **8 / 8**

The improvement cases are especially informative:

- spouse / relation lookup
- helper lookup
- OCR-grounded lease photo lookup
- OCR-grounded arrival photo lookup

In other words, retrieval self-check helps not because it adds a new memory backbone, but because it improves answer-time policy after structured retrieval.

### 6.7 Real-code self-check smoke and focused ablation

The nano evidence above establishes mechanism plausibility, but it does not by itself prove that the same mechanism survives contact with the current EchoMemory retrieval stack. We therefore ran a focused real-code smoke, and then aggregated it into a small real-code mini subset, directly on the current `SearchService` after integrating the new evidence-review and supporting-backbone expansion logic.

This real-code mini subset is intentionally small and should not be read as a benchmark table. Its purpose is narrower: to test whether self-check and graph-plus-atom complementarity become visible in the actual code path rather than remaining confined to the nano prototype.

The results support three concrete observations:

- **Temporal case**: with self-check disabled, the system returns chronology-shaped evidence but exposes no explicit answer-time audit; with self-check enabled, it adds one more supporting graph evidence item and emits an explicit `review=ok` decision.
- **Relational graph-only case**: after fixing English single-token entity seeding, the graph route becomes strong enough to reach `review=ok`, but the result still exposes `missing=fact_grounding`, showing that relation-path success does not automatically imply fact grounding.
- **Relational graph+atom case**: once atom grounding is added, the remaining `fact_grounding` gap disappears, demonstrating that graph and atom are complementary rather than redundant in the real system.

This focused real-code ablation matters for the paper because it upgrades the self-check story from “supported only by a toy implementation” to “already observable in the actual EchoMemory retrieval loop.” It still does not justify benchmark-scale superiority, but it does materially strengthen the systems claim that dual-backbone retrieval alone is not enough and that answer-time evidence policy changes real retrieval behavior.

### 6.8 Consolidated main result

Taken together, the current mechanism-level results support a five-row interpretation:

- **Tree-only** is strongest on temporal-only questions but fails badly on relation-heavy and readiness-sensitive settings.
- **Graph-only** is strongest on relation-heavy questions, and stronger than tree-only on mixed settings, but still cannot unify all query families by itself.
- **Dual-backbone** produces the most balanced temporal / relation / mixed behavior, but still inherits answer-too-early and under-checked evidence failures.
- **Readiness-gated full** combines dual-backbone routing with explicit answerability constraints, giving a stronger correctness story.
- **Self-check-enabled full** adds the final answer-time policy layer: answer if evidence shape is sufficient, expand if it is not, and abstain if it remains weak.

Across all five lines, the pattern is consistent:

- temporal questions prefer temporal tree
- relation-heavy questions prefer graph
- dual-backbone provides the most balanced retrieval story
- readiness gating is required to turn stored evidence into answer-time correctness
- retrieval self-check is required to turn strong retrieval into more reliable answer policy

## 7. Discussion

The present evidence justifies a mechanism-level claim, not a benchmark-scale claim. Specifically, it justifies the claim that long-horizon memory should not be flattened into one retrieval assumption or one unconditional answer policy.

It does **not** yet justify:

- benchmark-scale SOTA claims on LoCoMo or LongMemEval
- mature multimodal benchmark claims
- production-grade latency or cost conclusions

This boundary is a strength rather than a weakness. It lets the current package function as a credible bridge between architecture design and benchmark-scale validation.

Readiness is especially important here. Many memory systems discuss write durability and retrieval quality, but far fewer make answerability state explicit. Our readiness ablation suggests that this distinction is not cosmetic. It changes correctness.

The self-check result adds a second systems lesson. Even after good retrieval, the system still needs an explicit answer-time policy. A planner-routed memory architecture without any retrieval self-check is stronger than a flat retrieval pool, but it can still answer from structurally weak evidence. This is exactly the kind of gap that benchmark-level error analysis often reveals but architecture diagrams often hide.

The new real-code smoke strengthens this point further. It shows that self-check is not only a paper-side abstraction: it now changes evidence composition and visible review decisions in the actual `SearchService`. In particular, the relational graph-only versus relational graph-plus-atom contrast makes explicit that graph connectivity and fact grounding should be evaluated separately.

The broader lesson is that memory architecture should be evaluated as a systems problem rather than only as a retrieval recipe. EchoMemory-MM is promising precisely because it treats temporal routing, graph routing, readiness, and answer-time self-check as interacting constraints rather than as separate add-ons.

## 8. Limitations

The current work has five major limitations:

1. experiments remain small-scale and mechanism-oriented
2. multimodal evidence paths are early-stage
3. latency and cost are not yet formalized
4. runtime integration across planes remains incomplete
5. self-check is currently demonstrated only in nano-scale experiments

## 9. Conclusion

EchoMemory-MM advances a simple but under-emphasized thesis:

> temporal questions, relation-heavy questions, memory-readiness constraints, and answer-time evidence policy should not be collapsed into one flat retrieval assumption.

The current EchoMemory repository already contains the major structural planes needed for this direction, and the current nano experiments support the same architectural conclusion from multiple angles. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.
