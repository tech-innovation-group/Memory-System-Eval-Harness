# EchoMemory-TG Submission Blueprint

Date: 2026-06-13

## 1. Current position

EchoMemory is no longer just a vector-memory prototype. The current codebase already contains:

- append-only session journaling
- incremental atom extraction
- organized projection
- graph sync with `fact / event / entity`-style structure
- episode retrieval
- layered search with temporal anchor support

Authoritative local code:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/session_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/graph/sync.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/episode/retriever.py`

This means the project already has the skeleton of a publishable memory architecture.

## 2. The real paper angle

The strongest near-term paper angle is:

**EchoMemory-TG: Delta-Projected Temporal Graph Memory for Long-Horizon Conversational Agents**

This is currently a much more natural fit for:

- ACL
- EMNLP
- ICLR
- NeurIPS

than for CVPR.

### Why it is not naturally CVPR yet

The memory backbone is still predominantly text-grounded. The graph stores temporal and relational structure, but visual evidence is not yet a first-class memory object.

To make this genuinely CVPR-shaped, the system needs:

- multimodal evidence nodes
- image / screenshot / video grounding in the memory graph
- multimodal retrieval and temporal reasoning over visual evidence

In other words, the CVPR version should be:

**EchoMemory-MM: Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## 3. The method claim

The method claim should not be “we built another memory backend”.

It should be:

> We treat long-term agent memory as a stream-to-structure problem. Append-only interaction streams are incrementally projected into atomic facts, temporal event nodes, entity nodes, organized summaries, and episodic structures. Retrieval is guided by query type and temporal anchors rather than being handled by a monolithic vector-only store.

## 4. The concrete architectural deltas worth claiming

### Delta A. Readiness-aware memory states

Current local progress already suggests a useful system claim:

- `messages_persisted`
- `atoms_extracted`
- `vectors_indexed`
- `graph_synced`
- `organized_projected`
- `qa_ready`

The key insight is that retrieval artifacts existing is not the same thing as the memory system being truly ready for QA.

### Delta B. Temporal Fact Graph

The graph layer should become a real retrieval backbone, not just a synced sidecar.

The most important node/edge structure:

- `fact:{atom_id}`
- `event:{atom_id}`
- `entity:{name}`

with edges such as:

- `evidence_of`
- `has_fact`
- `involves`
- `about`
- `temporal_next`

### Delta C. Query planner instead of monolithic search

Right now, the most obvious code-level research bottleneck is that planning, execution, fallback, and fusion are too entangled.

The clean experimental story should split retrieval into:

1. planner
2. candidate generators
3. reader / assembler
4. rerank / fusion

## 5. What has already been built locally

### Structure and research reports

- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_structure_top10_gapmap_20260613.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_cvpr_paper_path_nano_v2_20260613.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_paper_package_20260612.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_paper_draft_v1_20260612.html`

### Nano implementations

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_temporal_graph.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_stream_graph_memory.py`

### New toy ablation

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_ablation_experiment.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_ablation_report.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_ablation_20260613.html`

This new toy ablation is not a benchmark result. It is a teaching and intuition artifact.

What it demonstrates:

- nano v1 confuses `event_time` with `created_at`
- nano v2 fixes that
- on the same v2 graph, planned retrieval beats flat lexical retrieval on the toy evidence-selection setup

Current toy result:

- extraction: v1 `0/2`, v2 `2/2`
- retrieval routing: flat `1/3`, planned `3/3`

## 6. The 10 most useful recent reference works

This project should keep grounding itself in the following line of work:

1. LoCoMo
2. LongMemEval
3. RAPTOR
4. HippoRAG
5. MemoRAG
6. LightMem
7. A-MEM
8. MIRIX
9. Mem0
10. MemMachine

These are already summarized in:

- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_cvpr_paper_path_nano_v2_20260613.html`

## 7. Minimal experiment package for a text-first submission

To become a credible text-first submission, the next clean experiment package should include:

### Main benchmarks

- LoCoMo
- LongMemEval

### Baselines

1. summary-heavy baseline
2. atom + vector baseline
3. atom + temporal graph
4. atom + temporal graph + planner

### Metrics

- answer accuracy
- temporal consistency / date correctness
- retrieval evidence hit rate
- QA-ready false positive rate
- ingestion latency
- retrieval latency

### Ablations

1. remove readiness gate
2. remove temporal resolver
3. remove graph path
4. replace planner with monolithic search
5. remove organized memory tree

## 8. Minimal experiment package for a real CVPR version

The CVPR version should not merely rename the text system.

It needs a multimodal memory chain:

1. interaction stream
2. visual evidence encoder
3. image / screenshot / video memory nodes
4. multimodal temporal graph
5. query planner that can explicitly route to visual evidence

Candidate evaluation directions:

- ScreenshotVQA-style settings
- personal screenshot timeline QA
- multimodal session-memory QA
- image-grounded long-horizon personal assistant tasks

## 9. The next 3 code changes that matter most

### P0

Integrate a real planner into the main search path.

### P1

Make temporal graph retrieval a first-class path for temporal and relational queries.

### P2

Add multimodal evidence projection if the target remains CVPR.

## 10. Recommended immediate next step

If the goal is the fastest path to a real submission:

1. finish the text-first EchoMemory-TG story
2. wire planner into main search
3. run clean LoCoMo + LongMemEval ablations
4. turn the best version into a polished systems paper

If the goal is specifically CVPR:

1. keep the text-first path as the control
2. branch into multimodal memory nodes
3. collect a visual memory benchmark or build one
4. rewrite the contribution around multimodal temporal grounding

## 11. Honest bottom line

Right now, EchoMemory already has:

- a real architecture worth writing about
- a growing body of analysis and design artifacts
- a real nano explanation path
- an initial toy ablation

What it still lacks for a true submission is not “more reports”.

It lacks:

- a planner integrated into the real search path
- clean benchmark ablations on the actual code path
- multimodal evidence if the destination remains CVPR
