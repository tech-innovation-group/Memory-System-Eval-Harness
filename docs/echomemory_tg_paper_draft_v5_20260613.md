# EchoMemory-TG Paper Draft v5

Date: 2026-06-13

## Title

**EchoMemory-TG: Readiness-Aware Query-Planned Temporal Graph Memory for Long-Horizon Conversational Agents**

## 0. Positioning

This draft is the most honest and strongest paper line supported by the current repository state.

### Recommended venue family today

- ACL
- EMNLP
- ICLR
- NeurIPS

### Why this is not yet a true CVPR submission

The current system is still fundamentally **text-first**. It already has a credible long-term memory architecture, but the multimodal branch is still only partially prototyped at the nano level. A true CVPR version should be framed as **EchoMemory-MM**, where screenshot / OCR / document-region evidence becomes a first-class memory object in the real code path and real evaluation package.

So the clean paper strategy is:

- **EchoMemory-TG**: text-first stream-to-structure memory architecture
- **EchoMemory-MM**: multimodal extension for a later CVPR-shaped branch

## 1. Abstract

Long-horizon conversational agents require memory systems that can continually ingest interaction streams, preserve evolving user facts, and retrieve precise evidence across sessions. Existing memory systems often rely on monolithic vector stores, coarse summaries, or weakly controlled retrieval pipelines, which are brittle for temporal questions, relational reasoning, and profile-detail recall. We present **EchoMemory-TG**, a readiness-aware query-planned temporal graph memory architecture that incrementally projects append-only session streams into atomic facts, organized summaries, episodic structures, and a temporal fact graph composed of fact, event, and entity nodes. At retrieval time, EchoMemory-TG uses lightweight query planning to route temporal, relational, and profile-detail questions toward different evidence paths rather than relying on a single fused retrieval strategy. The system also distinguishes between *durably persisted messages* and *QA-ready memory state*, preventing partially consolidated memory from being treated as answerable evidence. We instantiate the method on the EchoMemory codebase, provide nano prototypes for intuition, and define a benchmark package spanning LoCoMo and LongMemEval. Current evidence includes real-code planner and readiness integration, verified retrieval-path tests, and nano ablations showing that readiness gating, event-time normalization, and graph-first routing improve temporal and relational retrieval quality. These results suggest that long-term agent memory should be treated as a stream-to-structure systems problem rather than a vector-store-only retrieval problem.

## 2. Introduction

Long-term memory failures in conversational agents are often structural rather than purely parametric. Agents do not only fail because they “forget”; they fail because the memory system stores the wrong abstraction, exposes memory at the wrong lifecycle stage, or routes retrieval through the wrong evidence plane.

Three failure modes are especially common:

1. **write-time vs story-time confusion**
2. **relational flattening into generic summaries**
3. **false-ready answering before downstream consolidation finishes**

These failures motivate a broader hypothesis:

> Long-horizon agent memory should be modeled as a stream-to-structure problem. Interaction streams should be incrementally projected into multiple memory planes, and retrieval should be routed according to query type rather than handled by a single vector-only policy.

The current EchoMemory repository already contains the skeleton of such a system:

- append-only session journaling
- incremental atom extraction
- organized memory projection
- graph synchronization
- layered search
- graph diffusion retrieval
- temporal query anchoring through `RequestContext.query_time` and anchored relative-time resolution

This paper candidate organizes those ingredients into a coherent architecture: **EchoMemory-TG**, a readiness-aware query-planned temporal graph memory system for long-horizon conversational agents.

## 3. Core claims

This paper should **not** claim:

- we merely attached a graph beside a vector store
- we only improved retrieval through prompt engineering
- we already built a fully multimodal production memory stack

This paper **should** claim:

1. interaction streams can be incrementally projected into structured memory planes
2. temporal and relational questions benefit from explicit event / graph routing
3. retrieval should be query-planned rather than monolithic
4. “artifact exists” is weaker than “memory is QA-ready”
5. stream-to-structure memory is a stronger systems framing than vector-only memory

## 4. Method

### 4.1 Memory planes

EchoMemory-TG uses four core planes plus an optional episodic plane:

1. **Session plane**
   - append-only source of truth
   - `messages.jsonl`, metadata, overview / abstract

2. **Atomic plane**
   - statement-level facts
   - merge / invalidate / version semantics

3. **Structured plane**
   - organized memory such as profile, preference, entity, event, summary views

4. **Temporal graph plane**
   - `fact:{atom_id}`
   - `event:{atom_id}`
   - `entity:{name}`

5. **Episode plane**
   - higher-level experience grouping

### 4.2 Stream-to-structure projection

Incremental pipeline:

1. append turn
2. locate new turns after cursor
3. extract candidate atoms
4. merge against active atoms
5. persist atoms
6. index vectors
7. sync graph
8. project organized memory
9. optionally project episodes
10. update readiness state

This is why the system is more than a retriever.

### 4.3 Readiness-aware memory states

One systems contribution is the separation between message durability and answerability.

Suggested readiness state machine:

- `messages_persisted`
- `atoms_ready`
- `graph_ready`
- `organized_ready`
- `episode_ready`
- `qa_ready`

Key argument:

> A persisted message should not automatically become retrievable evidence until the minimum downstream consolidation path has completed.

### 4.4 Temporal fact graph

Node semantics:

- `fact:{atom_id}`: preserves statement-level fidelity
- `event:{atom_id}`: foregrounds story-time semantics
- `entity:{name}`: supports associative expansion

Edge semantics:

- `evidence_of`
- `has_fact`
- `involves`
- `about`
- `temporal_next`

### 4.5 Query planner

Planner modes:

- temporal
- relational
- profile
- experience
- general

Routing policy:

- temporal -> event / fact first
- relational -> graph / entity first
- profile -> fact / profile first
- experience -> episode / event first

## 5. Relation to recent work

The most useful recent references are not all identical “memory papers”; they define different pressure points for the system.

1. **LoCoMo**  
   motivates long-horizon conversational QA with temporal and relational pressure.

2. **LongMemEval**  
   motivates readiness, update behavior, abstention, and long-term consistency.

3. **RAPTOR**  
   motivates multi-level abstraction rather than flat chunk recall.

4. **HippoRAG**  
   motivates graph-first retrieval where structured memory is not a sidecar.

5. **HippoRAG 2 / memory-routing lines**  
   motivate query-aware routing rather than one fused retrieval mode.

6. **MemoRAG / hierarchical memory lines**  
   motivate coarse-to-fine recall and abstraction hierarchy.

7. **LightMem**  
   motivates online-light / offline-heavy separation and memory lifecycle design.

8. **Mem0**  
   motivates extracting durable user facts rather than storing raw turns only.

9. **A-MEM**  
   motivates associative traversal and agentic memory expansion.

10. **MIRIX / modular memory systems**  
   motivate typed multi-plane memory schema and system decomposition.

## 6. Implementation evidence in the current repository

### 6.1 Real-code architectural evidence

Key files:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/atom/retriever.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/provider_adaptor/graph_index/query.py`

These files establish that EchoMemory is already:

- append-only
- atom-first
- organized-memory aware
- graph-synchronized
- layered-retrieval capable

### 6.2 What is already true

- planner logic exists in real code
- graph-assisted retrieval exists in real code
- readiness is meaningful in both code and nano path
- temporal reasoning is a first-class concern

### 6.3 What is not yet fully true

- graph is not always the dominant recall backbone
- organized memory is still closer to markdown aggregation than typed state delta merge
- benchmark-level evidence is still weaker than component-level evidence
- multimodal memory is not yet implemented as a real production path

## 7. Experimental package

### 7.1 Main benchmarks

- LoCoMo
- LongMemEval

### 7.2 Baselines

1. summary-heavy baseline
2. atom + vector baseline
3. atom + graph without planner
4. atom + graph + planner
5. atom + graph + planner + readiness gate

### 7.3 Metrics

- answer accuracy
- temporal correctness
- evidence hit rate
- false-ready rate
- ingestion latency
- retrieval latency

### 7.4 Required ablations

1. remove planner
2. remove temporal resolver
3. remove graph path
4. remove event nodes
5. remove readiness gate

## 8. Current experimental evidence

### 8.1 Nano temporal / planner ablation

Evidence:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_ablation_results.json`

Results:

- extraction v1: `0/2`
- extraction v2: `2/2`
- flat retrieval: `1/3`
- planned retrieval: `3/3`

Supported claim:

- event-time normalization matters
- planner-guided retrieval changes evidence quality

### 8.2 Nano readiness ablation

Evidence:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_results.json`

Representative outcome:

- baseline answers too early
- temporal_graph answers too early
- full system correctly returns `not_ready` before cold path finishes

Supported claim:

- false-ready answering is a real systems failure mode

### 8.3 Unified nano

Evidence:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_unified_mm_tg.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_unified_mm_tg_output.json`

Supported claim:

- text-first TG and future MM/CVPR branch can be explained through one compact conceptual implementation

### 8.4 Main-code planner gap probe

Evidence:

- `/Users/chx/locomo-eval-web/scripts/echomemory_search_planner_gap_probe_20260613.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_search_planner_gap_probe_20260613/planner_gap_probe.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_search_planner_gap_probe_20260613/planner_gap_probe.html`

Observed result:

- inspected representative query families: `5`
- currently aligned with graph-first expectation: `2`
- remaining planner gaps: `3`

The qualitative pattern is more important than the raw count:

1. **visual queries are already the most mature planner path**
   - explicit `visual_lookup`
   - `image_evidence` seed preference
   - visual relation filters
   - dedicated unit-test coverage
2. **temporal queries are only partially upgraded**
   - current code forces deeper retrieval through `_FORCE_L2_KEYWORDS`
   - but this is not equivalent to event-first or graph-first routing
3. **relational queries still rely too much on conditional graph fallback**
   - current graph trigger is often tied to `intent.memory_types`, visual intent, or sparse L2
   - this is weaker than an explicit relational planner

Supported claim:

- EchoMemory already contains a real planner skeleton in the production path
- planner maturity is not uniform across query families
- the next high-value real-code upgrade is to bring temporal and relational questions closer to the maturity already visible on the visual path

### 8.5 Explicit planner nano and separation ablation

Evidence:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_tg.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_tg_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_ablation.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_ablation_results.json`

Observed result:

- mixed baseline: `1/4`
- explicit planner separation: `3/4`

What this ablation shows is subtle but important:

1. The gain is not only from “using a graph.”
2. The gain also comes from **separating planner, retriever, and evidence composer responsibilities**.
3. This separation makes it much easier to assign different evidence paths to different query families:
   - temporal -> event-first
   - temporal+relation -> event + relation chain
   - relation -> entity/event chain
   - plan -> block-first hybrid path

The remaining failure on a relation-only case is also informative rather than embarrassing:

- it shows that explicit planner separation is already helpful
- but relation expansion still needs a better edge-aware filtering policy

Supported claim:

- splitting `SearchService` into planner / retriever / composer is not cosmetic refactoring
- it changes evidence selection behavior on temporal and relational questions
- it creates a cleaner path for both real-code upgrades and paper ablations

## 9. Why this is not yet enough for CVPR

A true CVPR version should be renamed **EchoMemory-MM** and add:

1. image / screenshot / region ingest in the real code path
2. image evidence nodes in the real graph path
3. multimodal planner in the real search path
4. at least one real multimodal benchmark or controlled task

Current multimodal evidence is still prototype-level:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_temporal_graph.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_ablation_results.json`

That is enough to motivate the branch, but not enough yet to support a full CVPR claim.

## 10. Most important next steps

### P0

Run benchmark-level experiments on the planner-enabled real code path:

- LoCoMo
- LongMemEval

### P1

Strengthen the architecture itself:

- unify `story_time / mention_time / write_time`
- make graph-first routing more explicit for temporal and relational queries
- split planner from `SearchService` for clearer ablations
- add a dedicated temporal/relational planner probe to regression CI so visual maturity does not remain the only well-guarded path

### P2

If the goal remains CVPR:

- implement real multimodal ingest
- add image evidence graph nodes
- define a controlled multimodal memory benchmark

## 11. Honest bottom line

Today the strongest publishable story is:

> EchoMemory-TG is a text-first, readiness-aware, query-planned temporal graph memory architecture for long-horizon conversational agents.

Today the strongest honest CVPR statement is:

> EchoMemory-MM is a promising and partially prototyped multimodal extension, but it still needs real-system multimodal implementation and benchmark evidence before it should be pitched as a full CVPR submission.
