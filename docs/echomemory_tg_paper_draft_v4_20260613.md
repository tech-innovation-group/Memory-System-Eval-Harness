# EchoMemory-TG Paper Draft v4

Date: 2026-06-13

## Title

**EchoMemory-TG: Readiness-Aware Query-Planned Temporal Graph Memory for Long-Horizon Conversational Agents**

## Venue positioning

### Honest recommendation today

Based on the evidence currently available in the repository, the strongest submission line is:

- ACL
- EMNLP
- ICLR
- NeurIPS

This is because the current system is still **text-first**, even though its architecture is naturally extensible toward multimodal memory.

### CVPR path

If we want a true CVPR line, the paper must become **EchoMemory-MM** rather than only EchoMemory-TG. That requires:

1. image / screenshot / document-region evidence as first-class memory objects
2. multimodal grounding edges into the temporal graph
3. planner-guided multimodal retrieval rather than text-only routing
4. at least one benchmark or controlled evaluation where visual evidence is necessary

So the realistic storyline is:

- **EchoMemory-TG**: text-first systems paper that establishes the memory architecture
- **EchoMemory-MM**: follow-up multimodal memory paper that can more naturally target CVPR

## Abstract

Long-horizon conversational agents require memory systems that can continually ingest interaction streams, preserve evolving user facts, and retrieve precise evidence across sessions. Existing memory systems often rely on monolithic vector stores, coarse summaries, or weakly controlled retrieval pipelines, which are brittle for temporal questions, relational reasoning, and profile-detail recall. We present **EchoMemory-TG**, a readiness-aware query-planned temporal graph memory architecture that incrementally projects append-only session streams into atomic facts, organized summaries, episodic structures, and a temporal fact graph composed of fact, event, and entity nodes. At retrieval time, EchoMemory-TG uses lightweight query planning to route temporal, relational, and profile-detail questions toward different evidence paths rather than relying on a single fused retrieval strategy. The system also distinguishes between *durably persisted messages* and *QA-ready memory state*, preventing partially consolidated memory from being treated as answerable evidence. We instantiate the method on the EchoMemory codebase, provide nano prototypes for intuition, and assemble a benchmark plan spanning LoCoMo and LongMemEval. Our current evidence includes real-code planner integration, real-code readiness and temporal patches, verified search-path tests, and nano ablations showing that readiness gating, event-time normalization, and graph-first routing improve temporal and relational retrieval quality. These results suggest that long-term agent memory should be treated as a stream-to-structure systems problem rather than a vector-store-only retrieval problem.

## 1. Introduction

Long-term memory failures in conversational agents are often structural rather than purely parametric. Agents do not only fail because they “forget”; they fail because the memory system preserves the wrong abstraction. The system may confuse event time with write time, collapse relationships into generic summaries, or expose memory artifacts before downstream consolidation has finished.

This suggests a broader hypothesis:

> Long-horizon agent memory should be modeled as a stream-to-structure problem. Interaction streams should be incrementally projected into multiple memory planes, and retrieval should be routed according to query type rather than handled by a single vector-only policy.

The current EchoMemory codebase already contains the skeleton of such a system:

- append-only session journaling
- incremental atom extraction
- organized projection
- graph synchronization
- episode retrieval
- layered search
- temporal query anchoring

This draft organizes those ingredients into a coherent paper candidate: **EchoMemory-TG**, a readiness-aware query-planned temporal graph memory system for long-horizon conversational agents.

## 2. Core claims

This paper should **not** claim:

- we merely added another memory backend
- we simply attached a graph beside a vector store
- we only improved retrieval through prompt engineering

This paper **should** claim:

1. interaction streams can be incrementally projected into structured memory planes
2. temporal and relational questions benefit from explicit event / graph routing
3. retrieval should be query-planned rather than monolithic
4. “artifact exists” is weaker than “memory is QA-ready”
5. multimodal evidence should eventually become a first-class memory object, but the present paper is still text-first

## 3. Method

### 3.1 Memory planes

EchoMemory-TG uses four core memory planes plus an optional episodic plane:

1. **Session plane**
   - append-only source of truth
   - `messages.jsonl`, session metadata, overview / abstract

2. **Atomic plane**
   - statement-level facts
   - merge / invalidate / version semantics

3. **Structured plane**
   - organized memory such as profile, preference, entity, event, and summary views

4. **Temporal graph plane**
   - `fact:{atom_id}`
   - `event:{atom_id}`
   - `entity:{name}`
   - optionally relation nodes

5. **Episode plane**
   - higher-level story or experience grouping

### 3.2 Stream-to-structure projection

The incremental pipeline is:

1. append turn
2. locate new turns after cursor
3. extract candidate atoms
4. merge against active atoms
5. persist atoms
6. index vectors
7. sync graph
8. project organized memory
9. project episodes
10. update readiness state

This is the architectural reason the system is more than a retriever.

### 3.3 Readiness-aware memory states

One systems contribution is the separation between message durability and answerability.

Suggested readiness state:

- `messages_persisted`
- `atoms_ready`
- `graph_ready`
- `organized_ready`
- `episode_ready`
- `qa_ready`

The key argument is:

> A persisted message should not automatically become retrievable evidence until the minimum downstream memory consolidation path is complete.

### 3.4 Temporal fact graph

The graph plane is organized around node semantics:

**Nodes**

- `fact:{atom_id}`: preserves statement-level fidelity
- `event:{atom_id}`: foregrounds story-time semantics
- `entity:{name}`: supports associative expansion
- optionally `relation:{atom_id}`: foregrounds relational structure

**Edges**

- `evidence_of`
- `has_fact`
- `involves`
- `about`
- `temporal_next`
- relation participation edges

### 3.5 Query planner

The planner is the clearest algorithmic contribution already reflected in real code.

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

### 3.6 Toward multimodal memory

The CVPR-relevant extension should add:

- `image_evidence:{id}` nodes
- OCR/tag/caption fields
- grounding edges from image evidence to fact/event/entity nodes
- visual query planning

This extension is currently supported only at the nano-prototype level, not yet as a full real-system path.

## 4. Related work

The most relevant recent references for the narrative are:

1. LoCoMo
2. LongMemEval
3. RAPTOR
4. HippoRAG / graph-based retrieval lines
5. MemoRAG / hierarchical memory lines
6. LightMem
7. Mem0
8. A-MEM
9. MIRIX
10. LEGO-GraphRAG / modular graph retrieval lines

Their roles in the story:

- LoCoMo / LongMemEval define the benchmark pressure
- RAPTOR / MemoRAG / LightMem motivate staged abstraction and retrieval
- graph retrieval systems motivate event/entity routing
- Mem0 / A-MEM / MIRIX motivate evolving memory structure and production-grade memory concerns

## 5. Implementation status in the current codebase

Current real-code evidence:

- `SearchService` contains a real `QueryPlan`
- query-plan metadata is inserted into search results
- relational queries can run graph-first
- planner-aware result ordering is implemented
- session metadata now exposes readiness state
- `AtomFirstPipeline` persists readiness progression back to session metadata
- `TemporalQueryResolver` covers a broader set of relative-time expressions
- current-session retrieval can be gated by `qa_ready`

Key code:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/session_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py`

## 6. Current evidence

### 6.1 Verified real-code tests

The following tests have been run and passed on the current code:

- `test_search_session_readiness.py` -> `3/3`
- `test_search_query_planner.py` -> `4/4`
- `test_search_graph_integration.py` -> `8/8`
- `test_search_episode_integration.py` -> `5/5`
- `test_search_termination.py` -> `17/17`
- `test_temporal_query_resolver.py` -> `5/5`

Total verified points in this round: **42**

These tests do **not** replace benchmark evidence, but they do establish that the proposed system changes are not merely narrative claims.

### 6.2 Nano temporal evidence

Toy temporal ablation:

- extraction v1: `0/2`
- extraction v2: `2/2`
- flat retrieval: `1/3`
- planned retrieval: `3/3`

Supported claims:

1. explicit event-time handling matters
2. planner-guided retrieval changes evidence quality

### 6.3 Nano readiness + temporal + graph evidence

Readiness-temporal nano ablation:

- baseline: `1/5`
- temporal_graph: `4/5`
- full: `5/5`

This supports three narrow but useful claims:

1. false-ready answering is a real failure mode
2. write-time vs story-time confusion is a real failure mode
3. graph-first event retrieval helps temporal chain questions

### 6.4 Multimodal nano evidence

Current multimodal nano evidence should be framed conservatively:

- it shows that some answers are only available through image/screenshot evidence
- it motivates a multimodal branch
- it does **not** yet prove a full CVPR-ready system

## 7. Experiments to complete next

### 7.1 Benchmark experiments

Priority experiments:

1. LoCoMo subset
   - baseline vs planner vs planner+readiness
   - report temporal, relational, profile-detail breakdowns

2. LongMemEval subset
   - focus on session updates, abstention, and time consistency

### 7.2 Real-system ablations

Needed ablations:

- remove query planner
- remove temporal normalization
- remove graph path
- remove event nodes
- remove readiness gate

### 7.3 Systems metrics

Should measure:

- answer accuracy
- temporal correctness
- evidence hit rate
- false-ready rate
- ingestion latency
- retrieval latency

## 8. Limitations

The current paper candidate still has real limitations:

1. benchmark-scale evidence is incomplete
2. multimodal memory remains a nano/prototype direction
3. event-time semantics are stronger at query time than at full memory-schema level
4. graph-first retrieval is implemented, but not yet proven at large benchmark scale
5. this is not yet a natural CVPR paper without the multimodal branch

## 9. Paper strategy

### Strategy A: strongest current submission

Submit **EchoMemory-TG** as a text-first systems / retrieval paper to:

- ACL
- EMNLP
- ICLR
- NeurIPS

### Strategy B: longer CVPR path

Build **EchoMemory-MM** by adding:

- multimodal evidence nodes
- grounded visual-memory retrieval
- document/screenshot memory experiments
- multimodal benchmark evidence

Then frame it as a multimodal long-term memory architecture paper.

## 10. Bottom line

The strongest honest message today is:

> EchoMemory is already evolving into a stream-to-structure memory architecture, and the most promising contribution is not “better prompting,” but readiness-aware temporal graph memory with query-planned retrieval.

The next step is no longer to invent the architecture from scratch. It is to turn component-level evidence into benchmark-level evidence.
