# EchoMemory-TG Paper Draft v3

Date: 2026-06-13

## Recommended title

**EchoMemory-TG: Query-Planned Temporal Graph Memory for Long-Horizon Conversational Agents**

## Venue stance

### Honest current fit

With the evidence available today, this work is naturally closest to:

- ACL
- EMNLP
- ICLR
- NeurIPS

It is **not yet a natural CVPR paper** because the main memory chain is still text-first.

### Why CVPR is still relevant

The architecture is extensible toward CVPR because it already has:

- stream ingestion
- structured memory projection
- explicit graph nodes
- planner-guided retrieval

What it lacks is a multimodal memory backbone:

- screenshot / image / video evidence nodes
- multimodal grounding edges
- multimodal retrieval routing
- visual-memory benchmark evidence

The right framing is therefore:

- **EchoMemory-TG** = text-first submission line
- **EchoMemory-MM** = future multimodal / CVPR line

## Abstract v3

Long-horizon conversational agents require memory systems that can continually ingest interaction streams, preserve evolving user facts, and retrieve precise evidence across sessions. Existing memory systems often rely on monolithic vector stores, coarse summaries, or weakly controlled retrieval pipelines, which are brittle for temporal questions, relational reasoning, and profile-detail recall. We present **EchoMemory-TG**, a query-planned temporal graph memory architecture that incrementally projects append-only session streams into atomic facts, organized summaries, episodic structures, and a temporal fact graph composed of fact, event, and entity nodes. At retrieval time, EchoMemory-TG uses lightweight query planning to route temporal, relational, and profile-detail questions toward different evidence paths rather than relying on a single fused retrieval strategy. We instantiate the method on the EchoMemory codebase, provide nano prototypes for intuition, and outline a benchmark package spanning LoCoMo and LongMemEval. Our current evidence includes real-code planner integration, a temporal toy ablation showing improved event-time preservation and planned retrieval, a readiness-temporal nano ablation showing that false-ready answering and write-time/story-time confusion can be reduced through readiness gating plus temporal normalization, and a multimodal toy ablation showing that screenshot/OCR answers cannot always be recovered from text-only memory. These results suggest that long-term agent memory should be treated as a stream-to-structure systems problem rather than a vector-store-only retrieval problem.

## 1. Introduction

Long-horizon memory failures are often structural rather than purely parametric. Conversational agents do not only fail because they "forget"; they fail because the memory system preserves the wrong abstraction. Event time is confused with write time. Relational questions are forced through the same retrieval path as direct factual questions. Profile details are submerged under summaries. And in multimodal settings, some answers exist only inside screenshot or OCR evidence, which text-only retrieval can never surface faithfully.

This suggests a more general hypothesis:

> Long-term agent memory should be modeled as a stream-to-structure problem. Interaction streams should be incrementally projected into multiple memory planes, and retrieval should be routed according to query type rather than handled by a monolithic vector-only search policy.

The current EchoMemory codebase already contains the skeleton of such a system:

- append-only session journaling
- incremental atom extraction
- organized projection
- graph synchronization
- episode retrieval
- layered search with temporal anchor support

This paper argues that these ingredients can be organized into a coherent memory architecture: **EchoMemory-TG**, a query-planned temporal graph memory system for long-horizon conversational agents.

## 2. Core claim

This paper should **not** claim:

- we built a new memory backend
- we added a graph beside a vector store
- we simply improved retrieval with more heuristics

It **should** claim:

1. interaction streams can be incrementally projected into structured memory planes
2. temporal and relational questions benefit from explicit graph/event routing
3. retrieval should be query-planned, not monolithic
4. multimodal evidence should eventually become a first-class memory object

## 3. Method

### 3.1 Memory planes

EchoMemory-TG is best described as four interacting memory planes plus an optional fifth:

1. **Session plane**
   - append-only source of truth
   - `messages.jsonl`

2. **Atomic plane**
   - statement-level facts
   - merge / invalidate / version semantics

3. **Structured plane**
   - organized summaries
   - profile / preference / entity / event organization

4. **Temporal graph plane**
   - `fact:{atom_id}`
   - `event:{atom_id}`
   - `entity:{name}`

5. **Episode plane** (optional but useful)
   - higher-level storyline or experience grouping

### 3.2 Stream-to-structure projection

The core pipeline is incremental:

1. append turn
2. locate turns after cursor
3. extract candidate atoms
4. merge against active atoms
5. persist atoms
6. index vectors
7. sync graph
8. project organized memory
9. project episodes

This is the architectural reason the system is more than a retriever.

### 3.3 Temporal fact graph

The graph plane should be explained through the semantics of its nodes and edges.

Nodes:

- `fact:{atom_id}` preserves statement-level precision
- `event:{atom_id}` foregrounds event-time semantics
- `entity:{name}` supports associative expansion

Edges:

- `evidence_of`
- `has_fact`
- `involves`
- `about`
- `temporal_next`

### 3.4 Query planner

The planner is the most concrete algorithmic contribution currently supported by real code.

Planner modes:

- temporal
- relational
- profile
- experience
- general

Routing policy:

- temporal -> event/fact first
- relational -> graph/entity first
- profile -> fact/profile first
- experience -> episode/event first

### 3.5 Readiness-aware memory states

This is the systems contribution that should not be lost.

Suggested states:

- messages_persisted
- atoms_extracted
- vectors_indexed
- graph_synced
- organized_projected
- episode_projected
- qa_ready

The paper should explicitly argue that "artifact exists" is weaker than "memory is QA-ready."

## 4. Related work

The most useful references are:

1. LoCoMo
2. LongMemEval
3. RAPTOR
4. HippoRAG
5. HippoRAG 2
6. MemoRAG
7. LightMem
8. A-MEM
9. MIRIX
10. Mem0 / MemMachine line

Their roles in the narrative:

- LoCoMo / LongMemEval define the benchmark pressure
- RAPTOR / MemoRAG / LightMem motivate staged retrieval and abstraction
- HippoRAG / HippoRAG 2 motivate graph-first retrieval
- A-MEM / MIRIX / Mem0 / MemMachine motivate evolving structure and multimodal extension

## 5. Current evidence

This is the section that should keep the paper honest.

### 5.1 Real code evidence

Current local evidence:

- `SearchService` contains a real `QueryPlan`
- planner metadata is injected into results
- relational queries can run graph-first
- planner-aware result ordering is implemented
- session meta now exposes coarse readiness state
- atom-first pipeline now writes readiness progression back to session meta
- temporal query resolver now covers a broader set of relative-time expressions

Relevant code and report:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/session_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_realcode_planner_progress_20260613.html`

### 5.2 Temporal nano evidence

Toy temporal ablation:

- extraction v1 `0/2`
- extraction v2 `2/2`
- flat retrieval `1/3`
- planned retrieval `3/3`

This is enough to support two limited claims:

1. explicit event-time handling matters
2. planner-guided retrieval changes evidence quality

Evidence:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_ablation_20260613.html`

### 5.3 Multimodal nano evidence

Toy multimodal ablation:

- text-only `3/3`
- multimodal `3/3`
- visual-only gain cases `2`

The point is **not** that multimodal always improves all questions. The point is narrower and stronger:

- for screenshot-specific questions, multimodal retrieval surfaces `image_evidence`
- for OCR-dependent answers, text-only retrieval misses the answer while multimodal retrieval succeeds
- for style/preference questions, multimodal memory complements text rather than replacing it

Evidence:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_multimodal_nano_ablation_20260613.html`

### 5.4 Readiness + temporal nano evidence

Toy readiness-temporal ablation:

- baseline `1/5`
- temporal_graph `4/5`
- full `5/5`

The systems differ as follows:

- `baseline`: no temporal normalization, no graph-first retrieval, no readiness gate
- `temporal_graph`: temporal normalization + graph-first retrieval
- `full`: temporal normalization + graph-first retrieval + readiness gate

This ablation supports three more specific claims:

1. relative-time expressions should be normalized into story time before final QA
2. `temporal_next`-style graph structure only matters if retrieval actually routes through it
3. readiness gating reduces false-ready answering, which is a systems correctness issue rather than only a UI concern

Evidence:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_readiness_ablation_20260613.html`

## 6. Experiments

### 6.1 Main questions

The formal benchmark section should answer:

1. Does planner-guided retrieval improve answer accuracy and evidence quality?
2. Does temporal graph structure reduce temporal/date failures?
3. Does readiness-aware gating reduce false-ready states?

### 6.2 Primary benchmarks

- LoCoMo
- LongMemEval

### 6.3 Baselines

Suggested ladder:

1. summary-heavy baseline
2. atom + vector
3. atom + graph without planner
4. atom + graph + planner
5. atom + graph + planner + readiness gate

### 6.4 Metrics

- answer accuracy
- temporal correctness
- evidence hit rate
- unknown / abstain rate
- false-ready rate
- ingestion latency
- retrieval latency

### 6.5 Ablations

- w/o planner
- w/o graph path
- w/o temporal resolver
- w/o event nodes
- w/o readiness gate

Use:

- `/Users/chx/locomo-eval-web/docs/echomemory_benchmark_tables_template_20260613.md`

## 7. Threats to validity

1. Strongest current quantitative evidence is toy-scale or component-scale.
2. Benchmark-scale LoCoMo / LongMemEval results on the latest planner-enabled path are still missing.
3. The current system remains text-first, so any CVPR claim is prospective.
4. Some gains may come from retrieval control rather than deeper memory construction; experiments must separate the two.

## 8. What is required for submission

### Text-first submission line

Before a serious text-first submission:

1. run LoCoMo on the real improved path
2. run LongMemEval on the real improved path
3. fill the main benchmark and ablation tables
4. add one readiness-gate result

### CVPR submission line

Before a serious CVPR submission:

1. add multimodal evidence nodes to the real system
2. add multimodal retrieval routing in the real system
3. acquire a visual-memory benchmark or build one
4. rewrite the contribution around multimodal temporal grounding

## 9. Honest bottom line

The project is now beyond the prototype stage in three concrete ways:

- there is real-code planner integration
- there are explanatory nano implementations
- there are toy ablations for both temporal and multimodal branches

What is still missing is not analysis.

It is:

- benchmark-grade evidence on the improved real path
- and, if CVPR remains the target, real multimodal system integration rather than only nano prototypes
