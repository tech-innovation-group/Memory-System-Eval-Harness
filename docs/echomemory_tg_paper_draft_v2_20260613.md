# EchoMemory-TG Paper Draft v2

Date: 2026-06-13

## Title candidates

1. EchoMemory-TG: Stream-to-Structure Temporal Graph Memory for Long-Horizon Conversational Agents
2. EchoMemory-TG: Query-Planned Temporal Graph Memory for Long-Horizon Conversational QA
3. EchoMemory-TG: Delta-Projected Temporal Graph Memory for Personal Long-Term Agents

Recommended current title:

**EchoMemory-TG: Query-Planned Temporal Graph Memory for Long-Horizon Conversational Agents**

Reason:
- Compared with the earlier "Delta-Projected" title, this version foregrounds the two deltas we already have the strongest evidence for:
  - stream-to-structure memory construction
  - planner-guided retrieval
- It still leaves room for the graph and temporal story.

## Positioning

### Honest venue fit today

Today, the project is naturally shaped like:

- ACL
- EMNLP
- ICLR
- NeurIPS

more than CVPR.

### Why it is not a natural CVPR paper yet

The current evidence chain is still overwhelmingly text-first:

- session stream is text
- atom extraction is text-grounded
- graph nodes are fact / event / entity from text
- retrieval is text memory retrieval

A true CVPR version needs multimodal evidence as a first-class memory object:

- screenshot / image / video evidence nodes
- multimodal event grounding
- multimodal temporal retrieval
- visual-memory QA benchmark or task

So the right short-term strategy is:

1. finish a clean text-first paper package
2. keep the CVPR branch as EchoMemory-MM

## Abstract v2

Long-horizon conversational agents require memory systems that can continuously ingest interaction streams, preserve evolving user facts, and retrieve temporally grounded evidence across sessions. Existing memory systems often rely on monolithic vector stores, coarse summaries, or shallow retrieval pipelines, which are brittle for temporal questions, relational reasoning, and profile-detail recall. We present **EchoMemory-TG**, a query-planned temporal graph memory architecture that incrementally projects append-only session streams into atomic facts, organized summaries, episodic structures, and a temporal fact graph composed of fact, event, and entity nodes. At retrieval time, EchoMemory-TG uses lightweight query planning to route temporal, relational, and profile-detail questions toward different evidence paths rather than relying on a single fused retrieval strategy. This design preserves atomic evidence while improving retrieval controllability and interpretability. We instantiate the method on the EchoMemory codebase, provide a nano implementation for intuition, and outline an evaluation package spanning LoCoMo and LongMemEval. Our current evidence shows that planner-guided retrieval and explicit event-time handling improve evidence selection on temporal and relational queries, suggesting that long-term memory should be treated as a stream-to-structure systems problem rather than a vector-store-only retrieval problem.

## 1. Introduction

### Problem framing

Personal and conversational agents do not fail only because they "forget." They often fail because the memory system does not preserve the right structure:

- event time is confused with write time
- profile facts are buried under event summaries
- relational questions are forced through the same retrieval path as direct factual questions
- retrieval systems return plausible but weakly grounded evidence

Long-horizon memory is therefore not just a storage problem; it is a **stream-to-structure problem**.

### Central hypothesis

The central hypothesis of this paper is:

> Long-term agent memory should be incrementally projected from interaction streams into multiple structured memory planes, and retrieval should be planned according to query type rather than performed through a monolithic vector-only pipeline.

### What EchoMemory already gives us

The current local EchoMemory codebase already contains the skeleton needed for this claim:

- append-only session journaling
- incremental atom extraction
- organized projection
- graph synchronization
- episode retrieval
- layered search with temporal anchor support

Authoritative local code:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/session_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/episode/retriever.py`

### What is new in this paper

This paper should not claim "we built yet another memory backend."

It should claim four concrete deltas:

1. **Stream-to-structure memory construction**
2. **Temporal fact graph memory**
3. **Query-planned retrieval instead of monolithic fused retrieval**
4. **Readiness-aware long-term memory states**

## 2. Contributions

Suggested contributions section:

1. We present EchoMemory-TG, a text-first long-term memory architecture that incrementally projects append-only interaction streams into atomic facts, organized summaries, episodes, and a temporal fact graph.
2. We introduce a lightweight query planner that routes temporal, relational, and profile-detail questions toward different evidence paths, making graph and event evidence first-class retrieval targets.
3. We provide a nano implementation and toy ablation that isolate two central design choices: explicit event-time handling and planner-guided evidence routing.
4. We outline a benchmark-oriented evaluation protocol for LoCoMo and LongMemEval, connecting system-level retrieval behavior to answer quality, temporal consistency, and readiness-aware memory states.

## 3. Related Work

We should organize related work by problem family rather than paper-by-paper dump.

### 3.1 Long-term conversational memory benchmarks

- LoCoMo
- LongMemEval

Why they matter:
- they reveal temporal inconsistency
- they separate direct factual recall from evolving-state reasoning
- they expose the difference between storing information and retrieving the right evidence

### 3.2 Hierarchical retrieval and memory abstraction

- RAPTOR
- MemoRAG
- LightMem

Why they matter:
- they show that retrieval should be coarse-to-fine or staged
- they motivate moving beyond flat chunk stores
- they support our organized / episodic memory layers

### 3.3 Graph-based memory retrieval

- HippoRAG
- HippoRAG 2
- Mem0

Why they matter:
- graph is not just a storage sidecar
- graph should become an active retrieval backbone
- structure helps multi-hop and relational questions

### 3.4 Agentic / evolving memory

- A-MEM
- MIRIX
- MemMachine

Why they matter:
- memory should evolve
- evidence provenance should be preserved
- multimodal or multi-memory-type control is likely the next frontier

## 4. Method

### 4.1 Memory planes

EchoMemory-TG should be described as four interacting memory planes:

1. **Session plane**
   - append-only source of truth
   - `messages.jsonl`

2. **Atomic plane**
   - statement-level facts
   - supports versioning and merge/invalidation

3. **Structured plane**
   - organized summaries
   - profile/preferences/events/entities style organization

4. **Temporal graph plane**
   - explicit `fact / event / entity` nodes
   - retrieval-friendly relational structure

Optionally:

5. **Episode plane**
   - storyline-style grouping
   - useful for broader experience recall

### 4.2 Stream-to-structure projection

The method section should make clear that the pipeline is incremental:

1. append turn
2. parse new turns after cursor
3. extract candidate atoms
4. merge against active atoms
5. persist atoms
6. index vectors
7. sync graph
8. project organized memory
9. project episodes

This is exactly why "long-term memory" is not just retrieval. The key claim is that the system maintains multiple derived memory objects while preserving the original stream as truth.

### 4.3 Temporal fact graph

Current graph nodes and edges already point to a publishable story:

Nodes:

- `fact:{atom_id}`
- `event:{atom_id}`
- `entity:{name}`

Edges:

- `evidence_of`
- `has_fact`
- `involves`
- `about`
- `temporal_next`

Method claim:

> Event nodes are preferred for temporally anchored questions because they explicitly carry event-time semantics, while fact nodes preserve statement-level precision and entity nodes provide associative expansion.

### 4.4 Query planner

This should become the retrieval centerpiece.

Current planner modes we can honestly describe:

- temporal
- relational
- profile
- experience
- general

Routing idea:

- temporal -> event/fact first
- relational -> graph/entity first
- profile -> fact/profile first
- experience -> episode/event first

This is the most concrete algorithmic delta beyond "use more memory types."

### 4.5 Readiness-aware memory states

This is an underrated systems contribution.

Current motivation:
- retrieval artifacts can exist before the system is truly QA-ready
- files existing is weaker than semantic completion

Suggested readiness states:

- messages_persisted
- atoms_extracted
- vectors_indexed
- graph_synced
- organized_projected
- episode_projected
- qa_ready

This gives the paper a systems angle instead of being only retrieval heuristics.

## 5. Experiments

### 5.1 Main evaluation questions

The experiments should answer:

1. Does planner-guided retrieval improve evidence selection and answer accuracy?
2. Does explicit temporal graph structure reduce time/date failures?
3. Does readiness-aware gating reduce "import says done but memory is not actually usable" failures?

### 5.2 Benchmarks

Primary:

- LoCoMo
- LongMemEval

Optional later:

- personalized multi-session QA set
- multimodal memory QA if the CVPR branch advances

### 5.3 Baselines

Recommended baseline ladder:

1. summary-only
2. atom + vector
3. atom + graph without planner
4. atom + graph + planner
5. atom + graph + planner + readiness gate

### 5.4 Metrics

At least:

- answer accuracy
- temporal correctness
- evidence hit rate
- answerable-but-unknown rate
- false-ready rate
- ingestion latency
- retrieval latency

### 5.5 Ablations

Minimal good ablation table:

1. remove query planner
2. remove graph path
3. remove temporal resolver
4. remove event nodes
5. remove readiness gate

## 6. Current evidence we already have

### Real code evidence

Current local progress already supports several method claims:

- `search_service.py` now contains a real `QueryPlan`
- planner metadata is attached to results
- relational queries can run graph-first
- planner-aware result ordering is implemented

See:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_realcode_planner_progress_20260613.html`

### Nano evidence

We already have a small but clean intuition artifact:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_ablation_results.json`

Current toy results:

- extraction: v1 `0/2`, v2 `2/2`
- retrieval: flat `1/3`, planned `3/3`

This is not benchmark-grade evidence, but it is enough to support the narrative that:

- event time matters
- planner-guided routing matters

We now also have a multimodal nano branch:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_temporal_graph.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_demo_output.json`

This multimodal nano is not a submission result either, but it is useful for one very specific reason:

- it makes the CVPR branch concrete
- it shows how screenshot or image evidence can become first-class memory nodes
- it demonstrates, on toy visual-memory queries, that text-only memory cannot recover OCR-only answers such as visible timestamps

### Research / structure evidence

- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_structure_top10_improvement_20260613.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_cvpr_paper_path_nano_v2_20260613.html`

## 7. Threats to validity

We should state these plainly:

1. Current strongest quantitative evidence is still toy-scale or component-scale.
2. Benchmark-scale LoCoMo / LongMemEval ablations on the exact latest planner path are not complete yet.
3. The system is text-first, so any CVPR framing remains prospective until multimodal evidence nodes exist.
4. Some gains may come from retrieval control rather than from memory construction alone; experiments must separate the two.

## 8. Figure plan

Recommended figures:

1. **Architecture figure**
   - session stream -> atom -> organized -> graph -> episode -> planner-guided retrieval

2. **Temporal graph figure**
   - example with fact/event/entity and edges

3. **Planner routing figure**
   - temporal query vs relational query vs profile query

4. **Nano illustration**
   - event time vs created_at confusion

5. **CVPR extension figure**
   - screenshot/image evidence projected into multimodal temporal graph

## 9. What must happen before a serious submission

### For a strong text-first submission

1. Finish clean LoCoMo + LongMemEval ablations on the actual planner-enabled code path.
2. Separate answer metrics from evidence metrics.
3. Show at least one readiness-gate result.
4. Tighten the method section around planner + temporal graph, not "everything in the codebase."

### For a real CVPR version

1. Add multimodal evidence nodes.
2. Add multimodal retrieval routing.
3. Add a visual-memory benchmark or task.
4. Rewrite the contribution around multimodal temporal grounding.

The new multimodal nano should be viewed as the smallest explanatory prototype for steps 1 and 2, not as evidence that steps 3 and 4 are complete.

## 10. Honest bottom line

The project is now beyond the "interesting prototype" stage:

- there is real code
- there is a nano explanation path
- there is a paper angle
- there is a plausible experiment ladder

But it is not submission-ready yet.

The biggest remaining gap is not more analysis.

It is:

- benchmark-grade experiments on the real improved path
- and, if CVPR remains the target, multimodal evidence integration
