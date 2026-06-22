# EchoMemory-MM Draft v3

Date: 2026-06-13

## Working Title

**EchoMemory-MM: Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Status

This is a stronger research draft than `v2`, but it is still **not** a submission-ready CVPR paper.

What this draft adds on top of the earlier version:

1. a cleaner **dual-backbone** method story:
   - temporal tree for chronology
   - relation graph for entity / event / evidence traversal
2. a more explicit mapping from recent high-impact work to concrete EchoMemory upgrades
3. a new runnable nano prototype:
   - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone.py`
4. a new runnable nano ablation:
   - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_ablation.py`
   - compares `tree-only`, `graph-only`, and `dual-backbone`
5. a clearer separation between:
   - what the current repository really supports
   - what must still be built before a real CVPR submission is credible

---

## 1. Positioning

The honest positioning is:

- **EchoMemory-TG** is the strongest paper line supported by the current main code.
- **EchoMemory-MM** is the strongest CVPR-shaped branch, but it still needs more real multimodal evaluation and stronger production-path evidence.

The research hypothesis for this branch is:

> Long-horizon personal-agent memory should not be text-only and should not be single-backbone. It should combine a temporal abstraction backbone for chronology with a graph backbone for entity, event, and visual evidence traversal.

That is the central upgrade from a “text-first temporal graph memory” story to a more CVPR-shaped multimodal memory story.

---

## 2. Problem Statement

Current long-term memory systems for personal agents are usually weak in at least one of four ways:

1. they preserve text but not visual evidence as first-class memory
2. they store events but not a usable temporal abstraction hierarchy
3. they have graph structure but do not treat graph retrieval as a primary evidence path
4. they mix persistence and answerability, so a newly written observation is treated as QA-ready too early

This becomes visible in realistic questions such as:

- What time was visible in the screenshot from the arrival day?
- Who was related to whom, and what event established that relation?
- What happened before or after a specific life event?
- Which future plan was formed after a specific event?

These are not purely textual retrieval problems. They require:

- chronology
- relation traversal
- evidence grounding
- readiness control

---

## 3. Why the current EchoMemory repository is promising

The current repository already contains the skeleton needed for this line:

1. append-only session stream
2. incremental atom extraction
3. organized memory projection
4. graph synchronization
5. early multimodal image-evidence path
6. planner-like retrieval behavior in the search layer

Key real-code anchors:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`

The real code is therefore already beyond a generic “RAG + memory” baseline.

What it still lacks is a cleaner **retrieval policy architecture** and a stronger multimodal evaluation path.

---

## 4. Ten recent high-impact references and what EchoMemory should borrow

Strictly speaking, not all of the references below are already formal “top conference full papers.” Some are high-impact recent arXiv or systems papers. They are chosen because they are the most useful primary sources for the design problem in front of us.

### 4.1 Benchmarks and task pressure

1. **LoCoMo**  
   Link: https://arxiv.org/abs/2402.17753  
   Use: long-horizon conversational QA with temporal, multi-hop, and relation-heavy pressure.  
   Borrow: EchoMemory should make **story time** a first-class field rather than relying too much on mention time or write time.

2. **LongMemEval**  
   Link: https://arxiv.org/abs/2410.10813  
   Use: long-term memory evaluation focused on updates, temporal reasoning, abstention, and consistency.  
   Borrow: EchoMemory should separate **persistence** from **qa_ready** using a clearer readiness state machine.

### 4.2 Graph and structure as retrieval backbone

3. **HippoRAG**  
   Link: https://arxiv.org/abs/2405.14831  
   Use: graph-based retrieval as a primary reasoning substrate.  
   Borrow: graph should not remain a sparse fallback. For relation-heavy and temporal-relational queries, graph traversal should become a first-choice route.

4. **RAPTOR**  
   Link: https://arxiv.org/abs/2401.18059  
   Use: hierarchical abstraction for long-context retrieval.  
   Borrow: EchoMemory’s organized memory should evolve toward a real **temporal abstraction tree**, not just flat overview summaries.

5. **MemoRAG**  
   Link: https://arxiv.org/abs/2409.05591  
   Use: memory-inspired coarse-to-fine knowledge discovery.  
   Borrow: retrieval should move through multiple granularities instead of mixing all evidence candidates in one flat pool.

### 4.3 Durable memory extraction and memory lifecycle

6. **Mem0**  
   Link: https://arxiv.org/abs/2504.19413  
   Use: durable user memory rather than raw transcript accumulation.  
   Borrow: keep atom extraction as the core substrate, but strengthen consolidation and filtering policies.

7. **LightMem**  
   Link: https://arxiv.org/abs/2510.18866  
   Use: online-light / offline-heavy long-term memory design.  
   Borrow: make hot-path ingestion cheaper and move more expensive graph / structured consolidation into explicit offline or cold-path stages.

8. **A-MEM**  
   Link: https://arxiv.org/abs/2502.12110  
   Use: agentic memory retrieval and structured recall.  
   Borrow: retrieval should be planner-driven and query-family-aware rather than one universal ranking policy.

### 4.4 Multimodal and typed memory direction

9. **MIRIX**  
   Link: https://arxiv.org/abs/2507.07957  
   Use: modular multi-memory systems for LLM agents.  
   Borrow: typed memories should be orchestrated explicitly, not just stored as different files.

10. **DimMem**  
    Link: https://arxiv.org/abs/2602.16334  
    Use: dimension-aware long-term memory for agents.  
    Borrow: time, person, place, plan, and modality should become explicit routing dimensions, not implicit lexical side effects.

---

## 5. Main method idea: dual-backbone multimodal temporal memory

The core method claim of this draft is:

> A strong long-horizon personal-agent memory should combine a temporal abstraction tree and a relation graph, both grounded in an append-only stream and linked to image evidence.

### 5.1 Memory planes

We define five planes:

1. **Session stream**
   - append-only text and image observations
2. **Atomic plane**
   - fact / event / relation / plan atoms
3. **Temporal tree**
   - year / month / day / episode abstraction blocks
4. **Relation graph**
   - entity / event / fact / image_evidence nodes and typed edges
5. **Readiness plane**
   - tracks which downstream projections are safe to answer from

### 5.2 Why dual-backbone instead of graph-only

Graph-first retrieval helps relation-heavy and multi-hop questions, but graph-only systems are often awkward for chronology-heavy queries.

The temporal tree solves a different problem:

- graph answers “what is connected?”
- tree answers “when did this happen, and what surrounds it chronologically?”

The combination matters because many personal-agent questions are both:

- temporally anchored
- relationally grounded

Example:

> What did Gina plan after leaving Figma?

This needs:

- event ordering from the temporal backbone
- plan / relation grounding from the graph backbone

### 5.3 Multimodal extension

Image observations become first-class nodes:

- `image_evidence:{message_id}`

With properties:

- caption
- OCR
- tags
- linked subject
- observation time
- story time

And edges:

- `image_evidence -> shows -> entity`
- `image_evidence -> supports_event -> event`
- `image_evidence -> visual_evidence_of -> fact`

This is the smallest credible multimodal memory step that still preserves long-term structure.

### 5.4 Readiness state machine

We recommend explicit states:

- `messages_persisted`
- `atoms_ready`
- `tree_ready`
- `graph_ready`
- `qa_ready`

This design directly answers the write-vs-answerability problem highlighted by LongMemEval-like settings.

---

## 6. Query planning

The planner should not only classify intent. It should choose a backbone.

Recommended query families:

1. **temporal**
   - primary: temporal tree
   - support: graph
2. **relational**
   - primary: graph
   - support: tree
3. **temporal_relational**
   - primary: graph
   - support: tree
4. **visual**
   - primary: graph with `image_evidence`
   - support: tree
5. **general**
   - primary: tree
   - support: graph

This is the cleanest way to explain why different questions should not all enter the same recall path.

---

## 7. Runnable nano evidence

The clearest new nano artifact for this method is:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone.py`

Outputs:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone_report.html`

What it demonstrates:

1. stream observations as source of truth
2. atom extraction with story-time normalization
3. temporal tree for chronology
4. relation graph for entity / event / image evidence traversal
5. planner-based routing by query family
6. readiness gate before QA

Toy cases currently included:

- temporal date query
- relation query
- plan-after-event query
- visual screenshot query

This is still a toy system, but it is already much closer to a paper method diagram than a flat “memory demo”.

---

## 8. What experiments are already credible

### 8.1 Already credible

1. **graph-first toy ablation**
   - lexical baseline vs graph-first vs graph-path
2. **explicit planner toy ablation**
   - mixed retrieval vs explicit planner
3. **dual-backbone nano demo**
   - tree + graph + image evidence + readiness
4. **dual-backbone toy ablation**
   - `tree-only = 1/4`
   - `graph-only = 3/4`
   - `dual-backbone = 4/4`
   - interpretation:
     - tree alone is good for chronology-heavy queries
     - graph alone is good for relation / visual queries
     - the planner-guided combination is more stable across query families

These are mechanistic experiments, not benchmark claims.

### 8.2 Not yet strong enough

1. real multimodal benchmark-scale evaluation
2. real large-scale ablation on the production path
3. full CVPR-level comparison against strong multimodal memory baselines

---

## 9. What must be built before this can become a real CVPR submission

### 9.1 Engineering requirements

1. split `SearchService` into planner / candidate generation / evidence composition stages
2. add a temporal abstraction projector beyond the current flat organized memory
3. make graph-first retrieval explicit for relation-heavy and temporal-relational questions
4. make image evidence routing a first-class policy, not only a feature path
5. enforce readiness gating in the real runtime

### 9.2 Experimental requirements

1. benchmark package for:
   - LoCoMo
   - LongMemEval
   - screenshot or OCR-centric multimodal cases
2. ablations:
   - no tree
   - no graph
   - no image evidence
   - no readiness gate
   - no explicit planner
3. case studies:
   - time confusion
   - relation confusion
   - OCR-only answer
   - plan-after-event reasoning

---

## 10. Honest contribution claim today

Today the strongest honest claim is not:

> we already have a finished CVPR-ready multimodal memory system

The strongest honest claim is:

> EchoMemory already contains the right structural ingredients for a CVPR-shaped multimodal memory system, and the next decisive step is to turn its current text-first temporal graph architecture into a dual-backbone multimodal memory system with explicit planner routing and readiness control.

That is the real value of this draft.
