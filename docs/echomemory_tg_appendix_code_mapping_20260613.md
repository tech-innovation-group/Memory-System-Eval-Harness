# EchoMemory-TG Appendix: Code, Nano, and Method Mapping

Date: 2026-06-13

## Purpose

This appendix explains how the EchoMemory-TG paper concepts map to:

1. real EchoMemory code
2. the canonical nano implementation
3. the intended paper method sections

It is meant to make the paper easier to verify. A reader should be able to move from method claim to code artifact without guessing.

## A. High-level mapping

| Paper concept | Real EchoMemory code | Canonical nano | What it proves |
|---|---|---|---|
| Append-only session stream | `SessionService.add_message()` writes `messages.jsonl` | `append_turn()` | Memory starts from durable interaction streams |
| Readiness state | `messages_persisted`, `atoms_ready`, `graph_ready`, `organized_ready`, `qa_ready` in `meta.json` | `ReadinessState` | Persisted messages are not automatically QA-visible |
| Atom extraction | `AtomFirstPipeline.ingest_message()` | `run_hot_path()` | Turns are projected into atomic facts |
| Temporal normalization | `RequestContext.query_time` + `EpisodeRetriever._resolve_temporal_range()` + `SearchService` temporal-tree anchor resolution | `_resolve_story_time()` | Story time should be separated from mention/write time |
| Temporal graph | graph sync, graph retriever, event/fact/entity nodes | `run_cold_path()` creates fact/event/entity nodes | Facts become structured graph evidence |
| Query planning | `SearchService.QueryPlan` | `plan_query()` | Retrieval should route by query type |
| Readiness-gated retrieval | `SearchService._current_session_qa_ready()` and L0/L1/L2 gating | `search()` blocks when `qa_ready=false` | Avoids false-ready answering |

## B. Method section mapping

### B.1 Session stream

Paper section:

- `3.2 Stream-to-structure projection`

Real code:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/session_service.py`

Canonical nano:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg.py`
- function: `append_turn()`

Key point:

The system should not treat memory as an unstructured vector collection. It starts from a replayable session log.

### B.2 Readiness-aware memory visibility

Paper section:

- `3.3 Readiness-aware memory states`

Real code:

- `session_service.py::_initial_readiness_state()`
- `session_service.py::add_message()`
- `atom_first_pipeline.py::_update_readiness_meta()`
- `search_service.py::_current_session_qa_ready()`

Canonical nano:

- `ReadinessState`
- `search()`

Key point:

A message can be durably persisted while still unsafe to use as QA evidence.

Canonical nano states:

```json
{
  "before_hot": {
    "messages_persisted": true,
    "atoms_ready": false,
    "graph_ready": false,
    "organized_ready": false,
    "qa_ready": false
  },
  "after_hot": {
    "messages_persisted": true,
    "atoms_ready": true,
    "graph_ready": false,
    "organized_ready": false,
    "qa_ready": false
  },
  "after_cold": {
    "messages_persisted": true,
    "atoms_ready": true,
    "graph_ready": true,
    "organized_ready": true,
    "qa_ready": true
  }
}
```

### B.3 Atom extraction

Paper section:

- `3.2 Stream-to-structure projection`

Real code:

- `/Users/chx/Code/echomemory/echo_memory/echomem/workers/atom_first_pipeline.py`
- `AtomFirstPipeline.ingest_message()`

Canonical nano:

- `run_hot_path()`
- `_extract_atoms()`

Key point:

The atom plane is the first structured layer. It is where the system begins to separate durable text from answerable facts.

### B.4 Story time vs mention time

Paper section:

- `3.4 Temporal fact graph`

Real code:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/temporal/query_resolver.py`

Canonical nano:

- `_resolve_story_time()`
- atom fields:
  - `mention_time`
  - `event_time_start`
  - `event_time_end`
  - `time_confidence`

Key point:

This is still stronger in the nano than in the production schema. The production code has query-time temporal normalization, but a full paper-grade implementation should make event time a first-class atom field.

### B.5 Temporal graph

Paper section:

- `3.4 Temporal fact graph`

Real code:

- graph sync / graph retriever path in EchoMemory
- `SearchService` graph-first path

Canonical nano:

- `run_cold_path()`
- nodes:
  - `fact:{atom_id}`
  - `event:{atom_id}`
  - `entity:{name}`
- edges:
  - `evidence_of`
  - `has_fact`
  - `involves`
  - `temporal_next`

Key point:

The graph is not merely a storage mirror. It is the intended retrieval surface for temporal and relational questions.

### B.6 Query-planned retrieval

Paper section:

- `3.5 Query planner`

Real code:

- `/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py`
- `QueryPlan`
- `_build_query_plan()`

Canonical nano:

- `plan_query()`
- fields:
  - `intent`
  - `target_layers`
  - `graph_first`
  - `prefer_event`
  - `prefer_fact`

Key point:

The system should not use the same retrieval route for every question.

Example:

- temporal questions -> event/fact first
- relational questions -> entity/event graph route
- factual questions -> fact/organized route

## C. Nano result interpretation

The canonical nano result file is:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg_output.json`

The important behavior:

1. It refuses to answer before `qa_ready=true`.
2. It resolves "yesterday" against story time rather than runtime time.
3. It routes temporal queries to event nodes.
4. It surfaces structured evidence such as:

```text
event:atom-003
Jon / started_learning / marketing_and_analytics_tools
event_time=2023-07
```

## D. Multimodal extension mapping

The CVPR branch is represented by:

- `/Users/chx/locomo-eval-web/docs/echomemory_mm_cvpr_draft_v1_20260613.md`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_temporal_graph.py`

Mapping:

| EchoMemory-MM concept | Multimodal nano | Future real code |
|---|---|---|
| Image observation | `append_image()` | screenshot/image ingest API |
| Image evidence node | `image_evidence` | graph node type |
| OCR/caption/tags | observation fields | visual metadata store |
| Visual grounding edge | `visual_evidence_of`, `supports_event` | graph sync extension |
| Visual planner | `intent="visual"` | multimodal `SearchService` planner path |

## E. What remains incomplete

This appendix should not overclaim.

Still incomplete:

1. The real production atom schema does not yet fully expose `event_time_start`, `event_time_end`, `mention_time`, `valid_until`, or `supersedes`.
2. The real multimodal branch is still nano-level, not integrated into the main EchoMemory code path.
3. The formal benchmark tables remain mostly empty.
4. The real `conv-30` evidence is currently a 5-question smoke run, not a formal evaluation.

## F. Recommended next verification

The next verification that would most improve the paper package:

1. Run a 20+ question LoCoMo `conv-30` subset.
2. Fill the first version of the benchmark table.
3. Add temporal / relational / profile error buckets.
4. If pursuing CVPR, implement a tiny real image-evidence ingest path and run a controlled visual-memory task.
