# EchoMemory-MM Draft v5

Date: 2026-06-14

## Working title

**EchoMemory-MM: Query-Time Anchored Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Status

This is a more paper-shaped draft than v4. It keeps the same core direction, but it is reorganized around the strongest current evidence:

1. a validated 30-paper related-work map
2. main-code evidence for `query_time` anchored temporal retrieval
3. a canonical nano that explains the architecture in a single file
4. paired mechanism-level ablations:
   - anchored temporal questions
   - relation-heavy questions

It is still **not submission-ready** for CVPR, but it is now much closer to a believable draft skeleton.

---

## Abstract

Long-horizon personal agents must preserve evolving facts, reconstruct temporally ordered events, traverse relation-centric evidence, and increasingly ground answers in visual observations such as screenshots or OCR-bearing images. However, many memory systems still rely on a monolithic retrieval path, causing chronology-heavy questions, relation-heavy questions, and visual questions to collapse into the same evidence route. We propose **EchoMemory-MM**, a query-time anchored dual-backbone memory architecture that incrementally projects append-only interaction streams into atomic memory units, a temporal abstraction tree, a relation graph, and image-evidence nodes under an explicit readiness lifecycle. The central hypothesis is simple: temporal questions and relation questions should not be forced through the same primary backbone. EchoMemory-MM therefore routes anchored relative-time queries to a chronology-aware temporal tree, relation-heavy queries to a graph backbone, and uses supporting evidence from the complementary backbone. We ground the method in the current EchoMemory codebase, which now contains an atom-first pipeline, a temporal tree projector, graph synchronization, and query-time anchoring in the main retrieval path. We additionally provide a canonical nano implementation and two focused ablations that isolate the temporal and relational claims. The current evidence is mechanism-level rather than benchmark-scale, but it consistently supports the same conclusion: different memory families fail differently, and a query-family-aware dual-backbone memory is more faithful than a flat unified retrieval pool.

---

## 1. Introduction

Long-horizon memory failures are not all the same failure. In practice, an agent may:

- answer a date question from write time instead of story time
- answer a relation question from a summary instead of a graph path
- answer a screenshot question without treating the image as first-class evidence
- answer before downstream memory consolidation is complete

These are distinct failure modes, but they are often handled by the same retrieval path.

The EchoMemory codebase already suggests a better decomposition:

1. append-only session journaling
2. atom extraction
3. organized projection
4. graph sync
5. temporal tree projection
6. query planning
7. readiness gating

The draft claim is therefore not "memory is hard". The claim is:

> long-horizon memory should be modeled as a stream-to-structure system with query-family-aware primary backbones.

This becomes especially important once relative time matters. The main code now supports `RequestContext.query_time`, which means a question can be interpreted relative to the conversation's time anchor instead of the machine's runtime clock.

---

## 2. Current EchoMemory structure

### 2.1 Session stream

The source of truth is an append-only session stream:

- `messages.jsonl`
- `meta.json`
- `abstract.md`

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`

### 2.2 Atomic plane

The most important substrate is the atom-first pipeline:

- parse new turns after a cursor
- extract atoms
- merge against active atoms
- persist atoms
- index embeddings
- sync graph

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/utils/domain/atomic_memory.py`

### 2.3 Organized plane

The organized projector derives:

- profile
- overview
- entities
- events
- temporal_tree

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`

### 2.4 Graph plane

The graph layer already exists as a real structure, not a side note. It supports:

- atom nodes
- entity nodes
- event nodes
- image evidence nodes
- typed edges such as `has_fact`, `involves`, `temporal_next`, `shows`

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`

### 2.5 Search / planner plane

Search is no longer a single flat route. The current code has:

- intent classification
- query planning
- layered retrieval
- graph expansion
- temporal tree retrieval
- atom retrieval
- fusion

Primary code:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/planner/query_planner.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/planner/intent_classifier.py`

---

## 3. Method

### 3.1 Query-family-aware dual backbone

EchoMemory-MM uses two primary backbones:

- temporal tree for chronology
- relation graph for associative / visual / event traversal

The planner decides which backbone is primary and which one is supporting.

### 3.2 Query-time anchoring

Relative-time questions should be resolved against a query anchor, not runtime wall time.

Main-code anchor:

- `RequestContext.query_time`

This now affects:

- episode retrieval
- temporal-tree retrieval

### 3.3 Readiness lifecycle

We keep the correctness distinction between:

- persisted
- extracted
- projected
- answerable

This is an essential system property, not a UI detail.

---

## 4. Literature map

The 30-paper map remains the same validated base:

- benchmarks: LoCoMo, LongMemEval, LongMemEval-V2, Regimes, When Stored Evidence Stops Being Usable, WhenLoss
- hierarchy / temporal: RAPTOR, MemoRAG, GraphReader, ByteRover, TiMem, Hierarchical Memory
- graph / structured: HippoRAG, From RAG to Memory, Zep, LEGO-GraphRAG, H-Mem, APEX-MEM
- lifecycle / systems: Mem0, LightMem, MemOS, Infini Memory, AgentIR, ConvMemory
- policy / agentic / multimodal direction: MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, Self-RAG

The key reading takeaway is unchanged:

> temporal structure and relation structure solve different failure modes.

---

## 5. Experiments

### 5.1 Canonical nano

The canonical nano now includes:

- story-time normalization
- query-time anchor
- temporal tree
- graph-backed retrieval
- readiness gate

Artifacts:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg_output.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_canonical_nano_temporal_anchor_20260614.html`

### 5.2 Anchored temporal ablation

Focused on relative-time questions:

- `What happened yesterday?`
- `What happened last week about marketing and analytics tools?`
- `When was Jon in Rome?`

Current result:

- tree-only: 3 / 3
- graph-only: 2 / 3
- dual-backbone: 3 / 3

Artifacts:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_anchored_temporal_ablation_20260614.html`

Interpretation:

- tree is the right primary backbone for chronology-heavy questions
- graph is useful but not sufficient alone as a temporal navigation surface

### 5.3 Relation backbone ablation

Focused on relation-heavy questions:

- `Who is Gina married to?`
- `What did Gina plan after leaving Figma?`
- `Which company did Gina leave?`

Current result:

- tree-only: 0 / 3
- graph-only: 3 / 3
- dual-backbone: 3 / 3

Artifacts:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_report.html`

Interpretation:

- graph is the right primary backbone for relation-heavy questions
- tree-only can point to the right period, but it is the wrong primary retrieval surface

### 5.4 Paired evidence claim

Together, the two ablations support a paired claim:

- relative-time questions prefer a chronology-aware primary backbone
- relation-heavy questions prefer a graph-aware primary backbone

This is still mechanism-level evidence, not benchmark-scale proof, but it is a coherent experimental argument for query-family-aware routing.

---

## 6. Limitations

The current evidence does **not** justify the following claims:

- benchmark-scale SOTA on LoCoMo / LongMemEval
- fully mature multimodal performance
- production-grade efficiency claims

The current evidence **does** justify:

- architecture-level hypothesis
- main-code implementation anchors
- nano explanation
- paired mechanism ablations

This boundary should stay explicit in the paper.

---

## 7. What the CVPR version would still need

1. a real multimodal evaluation setting
2. stronger image evidence routing
3. larger-scale benchmark results
4. efficiency / latency profiling
5. cleaner ablations on:
   - tree only
   - graph only
   - dual backbone
   - readiness on / off

---

## 8. Conclusion

EchoMemory should evolve into a readiness-aware, query-time anchored, planner-routed dual-backbone memory architecture.

The current code and nanos already support the main methodological thesis:

> temporal questions and relation questions should not share the same primary retrieval backbone.

