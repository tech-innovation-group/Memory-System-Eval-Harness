# EchoMemory-MM Draft v6

Date: 2026-06-14

## Working title

**EchoMemory-MM: Query-Time Anchored Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Status

This version is more submission-shaped than v5. It still does **not** claim benchmark-scale completion, but it now does a better job on four fronts:

1. it states the paper contribution more explicitly
2. it reorganizes related work by problem pressure instead of by paper list
3. it sharpens the method into a clearer systems story
4. it makes the claim boundary explicit enough for an actual submission discussion

The intended role of v6 is:

> a believable paper draft skeleton that can support real code work, nano evidence, and future benchmark expansion.

Reference scope note:

- the reading map is centered on **2024-2026** work
- a very small number of older but still highly relevant primary references are retained as
  explicit foundational carry-over items
- the main example is **Self-RAG (2023)**, which remains useful for adaptive retrieval and
  self-reflective evidence control

---

## Abstract

Long-horizon personal agents must preserve evolving user facts, reconstruct temporally ordered events, traverse relation-centric evidence, and increasingly ground answers in visual observations such as screenshots or OCR-bearing images. However, many memory systems still rely on a monolithic retrieval path, forcing chronology-heavy questions, relation-heavy questions, and visual questions through the same evidence route. We propose **EchoMemory-MM**, a query-time anchored dual-backbone memory architecture that incrementally projects append-only interaction streams into atomic memory units, a temporal abstraction tree, a relation graph, and image-evidence nodes under an explicit readiness lifecycle. The central hypothesis is simple: temporal questions and relation-heavy questions should not share the same primary retrieval backbone. EchoMemory-MM therefore routes anchored relative-time queries to a chronology-aware temporal tree, relation-heavy queries to a graph backbone, and composes supporting evidence from the complementary backbone. We ground the proposal in the current EchoMemory codebase, which already contains an atom-first pipeline, organized projection, graph synchronization, temporal-tree projection, and query-time anchoring in the main retrieval path. We additionally provide a canonical nano implementation and paired mechanism-level ablations for anchored temporal questions and relation-heavy questions. While the current evidence is not yet benchmark-scale proof, it consistently supports the same architectural conclusion: different memory families fail differently, and long-horizon agent memory should be modeled as a planner-routed stream-to-structure system rather than a flat unified retrieval pool.

---

## 1. Introduction

Long-horizon memory failures in personal agents are often described as retrieval failures. That description is too weak. In practice, many failures come from using the wrong memory abstraction or the wrong retrieval route:

- a date question is answered from write time rather than story time
- a spouse question is answered from a summary rather than a graph path
- a screenshot question is answered without promoting image evidence to first-class memory
- a freshly written message is treated as answerable before downstream memory consolidation completes

These are different failure modes, yet many systems still expose them through a single retrieval interface.

The EchoMemory repository already suggests a stronger decomposition:

1. append-only session journaling
2. incremental atom extraction
3. organized memory projection
4. graph synchronization
5. temporal-tree projection
6. query planning
7. readiness gating

This draft therefore takes the following position:

> long-horizon agent memory should be framed as a stream-to-structure systems problem, with query-family-aware primary backbones.

This becomes especially important when relative time matters. A question such as "what happened yesterday?" is usually not anchored to the machine's current wall-clock date; it is anchored to a historical point inside the interaction. The current EchoMemory code now supports this distinction through `RequestContext.query_time`, which already flows into temporal retrieval paths.

The paper direction explored here is therefore not "add a graph beside a vector store." It is:

> model long-horizon agent memory as a readiness-aware, query-time anchored, dual-backbone system where a temporal tree and a relation graph solve different failure modes.

---

## 2. Contributions

This draft is scoped around four paper contributions.

### 2.1 Systems contribution

We organize long-horizon memory as a **stream-to-structure architecture**:

- append-only session stream
- atomic memory substrate
- temporal abstraction tree
- relation graph
- image-evidence nodes
- readiness lifecycle

### 2.2 Retrieval contribution

We propose a **query-family-aware dual-backbone retrieval policy**:

- temporal questions prefer a chronology-aware temporal tree
- relation-heavy questions prefer a graph backbone
- the non-primary backbone provides supporting evidence rather than being ignored

### 2.3 Temporal grounding contribution

We make **query-time anchoring** explicit. Relative temporal expressions should resolve against a query anchor rather than runtime wall time.

### 2.4 Evidence contribution

We provide **mechanism-level evidence** through:

- main-code implementation anchors
- a canonical nano implementation
- paired ablations on anchored temporal queries and relation-heavy queries

This is not yet benchmark-scale proof, but it is enough to support an architectural paper claim.

---

## 3. Problem framing

We focus on four recurrent failure modes in long-horizon personal-agent memory.

### 3.1 Temporal flattening

Event time collapses into mention time or write time. This produces answers that are locally plausible but historically wrong.

### 3.2 Relation under-routing

Relation-heavy questions are answered from summaries or broad temporal blocks rather than from graph-connected evidence.

### 3.3 Visual evidence loss

Screenshot and OCR-bearing observations are stored as attachments or side notes rather than first-class recall objects.

### 3.4 Readiness confusion

A message being durably written is treated as equivalent to memory being answer-ready.

The core claim of the paper is that these failures should not be handled by one flat retrieval pool.

---

## 4. Current EchoMemory structure

The current repository is already richer than a typical vector-memory chatbot.

### 4.1 Session plane

The source of truth is an append-only session stream:

- `messages.jsonl`
- `meta.json`
- `abstract.md`

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`

### 4.2 Atomic plane

The atom-first pipeline already performs:

- incremental turn loading after cursor
- atom extraction
- merge against active atoms
- atom persistence
- vector indexing
- graph synchronization

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/utils/domain/atomic_memory.py`

### 4.3 Organized plane

The organized projector currently derives:

- `profile`
- `overview`
- `entities`
- `events`
- `temporal_tree`

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`

### 4.4 Graph plane

The graph layer already exists as real structure rather than decorative metadata. It supports:

- atom nodes
- entity nodes
- event nodes
- image-evidence nodes
- typed edges such as `about`, `semantic_related`, `temporal_next`, `shows`, and `visual_evidence_of`

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`

### 4.5 Search / planner plane

The search layer already mixes:

- intent classification
- planner-like routing
- layered retrieval
- temporal-tree retrieval
- atom retrieval
- graph diffusion
- fusion

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/planner/query_planner.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/episode/retriever.py`

The architectural opportunity is therefore not to invent these layers from scratch, but to make their roles cleaner and more explicit.

---

## 5. Related work

Rather than listing papers one by one, we group recent work by the system pressure it places on EchoMemory.

### 5.1 Benchmark pressure: long-horizon memory is not one scalar metric

LoCoMo and LongMemEval establish that temporal, relational, update-sensitive, and long-horizon conversational questions should be evaluated explicitly rather than collapsed into general QA. LongMemEval-V2, Regimes, When Stored Evidence Stops Being Usable, and WhenLoss make the further point that memory quality should be decomposed into at least:

- write-path correctness
- retrieval usability
- answer-time faithfulness
- lifecycle readiness

This directly motivates a readiness-aware architecture rather than a write-only memory service.

### 5.2 Hierarchical and temporal retrieval: time should be navigable structure

RAPTOR, MemoRAG, ByteRover, TiMem, and hierarchical-memory style work all support the same principle: long-horizon recall improves when memory is exposed through hierarchical abstraction rather than only flat retrieval. For EchoMemory, the most relevant lesson is that temporal information should become a navigation surface, not just a field attached to facts.

### 5.3 Graph and structured recall: graph should be a backbone, not a sidecar

HippoRAG, From RAG to Memory, Zep, LEGO-GraphRAG, H-Mem, and APEX-MEM argue for graph-structured or semi-structured memory as an active recall substrate. Their shared lesson is that relation-heavy or event-linked questions should not be forced through the same route as chronology-heavy questions.

### 5.4 Memory lifecycle and systems: durability is weaker than answerability

Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not just a retriever; it is also a lifecycle and systems problem involving extraction cost, consolidation delay, routing policy, and structured maintenance.

### 5.5 Agentic policy and multimodal direction

MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward two next-step themes:

- memory actions should be policy-aware rather than purely heuristic
- multimodal evidence should become first-class memory rather than auxiliary metadata

The proposed EchoMemory-MM direction is most naturally positioned at the intersection of these two themes.

---

## 6. Method

### 6.1 Five-plane memory model

EchoMemory-MM uses five planes:

1. **Session stream**
   - append-only text and image observations
2. **Atomic plane**
   - fact, event, relation, and plan atoms
3. **Temporal tree**
   - year / month / day / higher-level chronology blocks
4. **Relation graph**
   - entity, event, fact, and image-evidence nodes with typed edges
5. **Readiness plane**
   - lifecycle state from persisted to answerable

### 6.2 Query-time anchoring

Relative temporal expressions should be resolved against a query anchor:

- runtime wall time is wrong for historical offline evaluation
- mention time is wrong for story-time recall
- query time is the correct anchor for expressions such as "yesterday" and "last week"

In the current main code, this is already reflected by:

- `RequestContext.query_time`
- anchored episode retrieval
- anchored temporal-tree key selection

### 6.3 Dual-backbone routing

We define two primary backbones:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy, event-heavy, and visual queries

The planner decides:

- which backbone is primary
- which backbone is supporting
- whether event nodes or fact nodes should be preferred

### 6.4 Readiness lifecycle

The system should distinguish:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

This is a systems-level correctness constraint, not only an optimization or UI concern.

### 6.5 Multimodal grounding

Image evidence should become a first-class memory object:

- screenshot / photo / OCR observation enters the session stream
- it is projected to graph nodes such as `image_evidence:{message_id}`
- graph links connect image evidence to entities and supporting atoms

This is currently an early code path in the main repository and a clearer path in the nano prototypes. The paper should describe it as an emerging systems capability rather than a fully mature benchmark line.

---

## 7. Experiments

### 7.1 Canonical nano

The canonical nano demonstrates the smallest implementation that still captures:

- append-only stream
- story-time normalization
- query-time anchor
- temporal tree
- graph-backed retrieval
- readiness gating

Artifacts:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg.py`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_canonical_nano_temporal_anchor_20260614.html`

### 7.2 Anchored temporal ablation

This ablation asks whether anchored temporal questions prefer a chronology-aware backbone.

Current result:

- tree-only: 3 / 3
- graph-only: 2 / 3
- dual-backbone: 3 / 3

Interpretation:

- chronology-heavy relative-time questions prefer a temporal primary backbone

### 7.3 Relation backbone ablation

This ablation asks whether relation-heavy questions prefer a graph backbone.

Current result:

- tree-only: 0 / 3
- graph-only: 3 / 3
- dual-backbone: 3 / 3

Interpretation:

- relation-heavy questions prefer graph as primary backbone

### 7.4 Controlled dual-backbone benchmark

The 12-case toy benchmark is not a benchmark-scale paper result, but it gives a useful architectural stress test.

Current result:

- tree-only: 3 / 12
- graph-only: 5 / 12
- dual-backbone: 8 / 12

Per-family pattern:

- temporal -> tree strongest
- visual -> graph strongest
- mixed families -> dual more stable

### 7.5 What these experiments justify

Together, the experiments justify an architectural claim:

> temporal structure and relation structure solve different failure modes, and planner-routed dual-backbone retrieval is more faithful than flattening them into one route.

They do **not** yet justify benchmark-scale superiority claims.

---

## 8. Claim boundary

The paper should **not** currently claim:

- benchmark-scale SOTA on LoCoMo or LongMemEval
- production-grade multimodal readiness
- complete efficiency and latency characterization

The paper **can** currently claim:

- a coherent systems hypothesis
- main-code implementation anchors
- query-time anchored temporal retrieval in real code
- a canonical nano that explains the method clearly
- paired mechanism-level ablations that support the dual-backbone thesis

This boundary is a strength, not a weakness. It keeps the paper honest.

---

## 9. What the CVPR submission version would still need

1. a stronger real multimodal evaluation setting
2. larger benchmark-scale experiments
3. explicit readiness on/off ablations
4. latency / cost profiling
5. a more formal comparison between:
   - flat retrieval
   - tree-only
   - graph-only
   - dual-backbone

---

## 10. Conclusion

EchoMemory should evolve into a readiness-aware, query-time anchored, planner-routed dual-backbone memory architecture.

The current code and nano evidence already support the main methodological thesis:

> temporal questions and relation-heavy questions should not share the same primary retrieval backbone.

That is a sufficiently coherent and technically grounded direction for a serious paper line, and it is the most honest bridge from the current repository state toward a future CVPR-shaped multimodal submission.
