# EchoMemory Dual-Backbone Main-Code Blueprint

Date: 2026-06-13

## 0. Goal

This blueprint describes how to evolve the current EchoMemory main codebase from a **text-first temporal graph memory** into a **dual-backbone memory system**:

- a **temporal tree backbone** for chronology and coarse-to-fine recall
- a **relation graph backbone** for entity / event / visual evidence traversal

The purpose is not to replace the existing atom / graph system. The purpose is to make the current structure more query-planned, more benchmark-friendly, and more aligned with the strongest CVPR-shaped research line we now have.

---

## 1. Current code anchors

The main code already contains the right raw ingredients.

### 1.1 Session stream

File:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`

What it already gives:

- append-only session stream
- metadata and readiness-adjacent state
- overview / abstract generation path

### 1.2 Atomic substrate

File:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`

What it already gives:

- user-turn-only extraction
- incremental cursor
- atom merge
- vector indexing
- graph sync hook

### 1.3 Structured projection

File:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`

What it already gives:

- deterministic organized memory projection
- profile / overview / entities / events
- event records already containing:
  - `timestamp`
  - `date`
  - `yyyy`
  - `mm`
  - `dd`

This is the cleanest insertion point for a new **temporal_tree** memory family.

### 1.4 Graph sync

File:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`

What it already gives:

- atom -> graph node sync
- about / semantic_related / temporal_next / contains / same_entity paths
- early `image_evidence` path

This is already the relation / evidence backbone.

### 1.5 Retrieval orchestration

File:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`

What it already gives:

- intent recognition
- L0 / L1 / L2 layering
- episode retrieval
- atom retrieval
- graph diffusion
- text fallback
- compound query handling
- fusion

This file is also where the architectural pressure is highest: it is doing too many jobs at once.

---

## 2. Why dual-backbone is the right next step

The current graph path is useful, but graph alone is awkward for chronology-heavy queries such as:

- when exactly did X happen
- what happened before / after Y
- what was going on around a specific date

The current overview path is useful, but overview alone is weak for:

- relation questions
- event-to-entity traversal
- visual evidence grounding

So the proposal is not “replace graph with tree”.

The proposal is:

1. keep graph as the **relation and evidence backbone**
2. add a temporal tree as the **chronology backbone**
3. let the planner choose which backbone is primary per query family

---

## 3. New modules to add

## 3.1 Temporal tree projector

Suggested new file:

- `echomem/workers/organized_projector/temporal_tree_projector.py`

Suggested responsibility:

- consume event-like organized memory entries
- build year / month / day / optional episode abstraction blocks
- write them as organized memory entries under a new memory type

Suggested new memory type:

- `temporal_tree`

Suggested key patterns:

- `year:2023`
- `month:2023-03`
- `day:2023-03-21`
- optional:
  - `episode:<episode_id>`

Suggested data schema:

```python
{
  "key": "day:2023-03-21",
  "level": "day",
  "date_key": "2023-03-21",
  "title": "Day 2023-03-21",
  "content": "...",
  "derived_from": ["atom-001", "atom-004"],
  "event_refs": ["event-001", "event-004"],
  "entity_refs": ["Gina", "Lisbon"],
}
```

Why here:

- `OrganizedProjector` already owns deterministic projections
- event data is already normalized into `yyyy / mm / dd`
- this avoids inventing a parallel pipeline

## 3.2 Temporal tree retriever

Suggested new file:

- `echomem/index_engine/temporal_tree/retriever.py`

Suggested responsibility:

- load relevant temporal tree blocks
- prioritize chronology-oriented blocks for temporal queries
- expose `ContextItem`s compatible with `SearchService`

Suggested retrieval modes:

1. date-key match:
   - `2023-03-21`
   - `2023-03`
   - `2023`
2. relative time resolution:
   - yesterday / last week / earlier / after / before
3. entity-anchored chronology:
   - event + entity -> relevant tree block(s)

## 3.3 Query planner extraction

Suggested new files:

- `echomem/index_engine/planner/intent_classifier.py`
- `echomem/index_engine/planner/query_planner.py`
- `echomem/index_engine/planner/evidence_composer.py`

Why:

The current `SearchService` mixes:

- intent classification
- layer selection
- graph seed strategy
- retrieval execution
- fusion

These need to become explicit if we want:

- cleaner ablations
- easier benchmarking
- easier future multimodal extension

---

## 4. SearchService decomposition plan

This is the most important engineering step.

### 4.1 What stays in `SearchService`

Keep `SearchService` as the top-level orchestration boundary:

- request entrypoint
- budget management
- final logging / tracing
- compatibility with current callers

### 4.2 What should move out

#### A. Intent classification

Move out:

- `_template_classify()`
- `_classify_intent()`
- `_classify_intent_llm()`
- `_parse_llm_intent_response()`

Into:

- `IntentClassifier`

#### B. Query-family routing

New explicit object:

- `QueryPlan`

Suggested query families:

1. `temporal`
2. `relational`
3. `temporal_relational`
4. `visual`
5. `profile`
6. `general`

Suggested plan fields:

```python
QueryPlan(
  family="temporal_relational",
  primary_backbone="graph",
  supporting_backbones=["temporal_tree", "atom"],
  force_l2=True,
  graph_relation_filter=["involves", "causal", "temporal_next"],
  preferred_memory_types=["events", "entities", "temporal_tree"],
)
```

#### C. Candidate generators

Split retrieval execution into separate generators:

1. `L0Retriever`
2. `OverviewRetriever`
3. `TemporalTreeRetriever`
4. `EpisodeRetriever`
5. `AtomRetriever`
6. `GraphRetriever`
7. `TextFallbackRetriever`

#### D. Evidence composition

Add:

- `EvidenceComposer`

Responsibility:

- deduplicate hits
- preserve chronology when needed
- preserve relation path when needed
- choose what should appear in the final prompt evidence

This matters because “retrieval worked” and “prompt evidence is well-shaped” are different problems.

---

## 5. Backbone policy by query family

Recommended first-pass routing:

### 5.1 Temporal

Primary:

- `temporal_tree`

Support:

- `graph`
- `atom`

Use when:

- when / date / before / after / around / during / yesterday / last week

### 5.2 Relational

Primary:

- `graph`

Support:

- `temporal_tree`
- `atom`

Use when:

- who is related to whom
- which company / team / person
- who invited / who worked with / who supported

### 5.3 Temporal-relational

Primary:

- `graph`

Support:

- `temporal_tree`

Use when:

- what plan happened after event X
- what changed after joining company Y
- who was involved before / after event Z

### 5.4 Visual

Primary:

- `graph`

Support:

- `temporal_tree`

Why:

- image evidence already lives more naturally in graph structure than tree structure

### 5.5 General

Primary:

- overview / temporal_tree

Support:

- graph only when needed

---

## 6. Suggested no-breaking-change rollout

## Phase 0: planner extraction only

Do not change memory formats yet.

Tasks:

1. create `IntentClassifier`
2. create `QueryPlanner`
3. make `SearchService` call them
4. keep existing retrieval functions behind the new planner

Goal:

- improve architecture without changing stored memory

## Phase 1: temporal tree projection

Tasks:

1. add `temporal_tree` organized memory type
2. add projector from event memories
3. index temporal tree blocks for semantic lookup if needed

Goal:

- create the second backbone

## Phase 2: temporal tree retrieval

Tasks:

1. add `TemporalTreeRetriever`
2. integrate it into planner
3. use it as primary for temporal queries

Goal:

- move time queries off “deep search everywhere”

## Phase 3: prompt evidence shaping

Tasks:

1. add `EvidenceComposer`
2. ensure temporal answers preserve chronology order
3. ensure relation answers preserve path support

Goal:

- retrieval output becomes answer-friendly evidence, not just raw hits

## Phase 4: benchmark integration

Tasks:

1. run LoCoMo
2. run LongMemEval
3. add toy or real visual memory tasks
4. add ablations:
   - no tree
   - no graph
   - no readiness
   - no planner

Goal:

- turn the architecture into a benchmarkable paper claim

---

## 7. Suggested first concrete code patch

If only one real code patch should be attempted first, it should be:

1. extract `IntentClassifier`
2. add `QueryPlanner`
3. introduce a new `QueryPlan.primary_backbone`

Reason:

- this is low-risk
- it improves code clarity immediately
- it creates a landing zone for temporal tree later
- it makes future ablations natural

If a second patch is allowed, it should be:

4. add `temporal_tree` projection in `OrganizedProjector`

Reason:

- event normalization already exists there
- it is the cleanest place to build the new chronology backbone

---

## 8. What this means for the paper

This blueprint sharpens the paper story.

Instead of claiming:

> EchoMemory has graph memory and some multimodal support

we can aim to claim:

> EchoMemory evolves from a text-first temporal graph memory into a dual-backbone multimodal memory system, where a temporal abstraction tree and a relation graph jointly support query-planned long-horizon recall.

This is a much stronger and cleaner systems contribution.

