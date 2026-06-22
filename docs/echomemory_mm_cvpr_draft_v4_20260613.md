# EchoMemory-MM Draft v4

Date: 2026-06-13

## Working title

**EchoMemory-MM: Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Status

This is the strongest draft so far for the CVPR-shaped direction, but it is still **not submission-ready**.

What v4 adds beyond earlier drafts:

1. a **30-paper related-work map** instead of a 10-paper sketch
2. a clearer split between:
   - validated recent primary sources
   - local older notes that may have contained stale links
3. a stronger **controlled nano benchmark**:
   - 12 cases
   - temporal / relational / temporal-relational / visual families
   - tree-only vs graph-only vs dual-backbone
4. a more explicit bridge from:
   - current EchoMemory code
   - near-term main-code upgrades
   - eventual CVPR-style multimodal evaluation

---

## Abstract

Long-horizon personal agents require memory systems that can preserve evolving user facts, reconstruct temporally ordered events, retrieve relation-centric evidence, and ground answers in screenshot or OCR-bearing observations when text alone is insufficient. Existing memory systems often rely on monolithic vector stores, flat summaries, or graph sidecars that are not promoted to first-class retrieval backbones. We propose **EchoMemory-MM**, a dual-backbone multimodal memory architecture that incrementally projects append-only interaction streams into atomic memory units, a temporal abstraction tree, a relation graph, and image-evidence nodes coordinated through a readiness-aware lifecycle. The core idea is simple: chronology-heavy questions should not traverse the same evidence path as relation-heavy or visual questions. EchoMemory-MM therefore uses query planning to route different query families toward different primary backbones, while using the other backbone for support. We ground the proposal in the current EchoMemory repository, show how the existing stream-to-atom-to-graph pipeline already supports the architectural skeleton, and provide a controlled nano benchmark comparing tree-only, graph-only, and planner-routed dual-backbone retrieval. The current evidence is not yet benchmark-scale proof, but it consistently supports the same mechanistic conclusion: time structure and relation structure solve different failure modes, and memory systems should model both explicitly rather than flattening them into one retrieval pool.

---

## 1. Introduction

Long-term memory failures in personal agents are often blamed on limited context windows or weak retrieval. That diagnosis is incomplete. In many realistic failures, the system does not simply "forget"; instead, it stores the wrong abstraction or uses the wrong retrieval path. A date question is forced through the same route as a spouse question. A relation question is answered from a month summary instead of an event graph. An OCR-dependent answer is searched as if it were a plain text fact. A newly written message is treated as answer-ready before downstream projections are complete.

These failures suggest a broader systems hypothesis:

> Long-horizon personal-agent memory should be treated as a stream-to-structure problem, not a flat retrieval problem.

The EchoMemory codebase already contains a promising skeleton for this direction:

1. append-only session journaling
2. incremental atom extraction
3. organized memory projection
4. graph synchronization
5. early multimodal image-evidence support
6. planner-like retrieval behavior in the search layer

However, the current system is still closer to a text-first temporal graph memory than to a full multimodal memory architecture. In particular, it still lacks:

- a fully mature temporal abstraction tree as a first-class retrieval structure
- a graph that consistently acts as primary recall backbone for relation and visual questions
- a readiness plane that clearly separates persistence from answerability
- benchmark-scale evidence for a multimodal memory line

At the same time, the repository has now crossed an important threshold: it no longer lacks
temporal-tree execution entirely. A minimal `temporal_tree` plane is now projected from event
atoms into year/month/day blocks, and the search layer has a first retrieval path that reads
these blocks directly when the planner selects `primary_backbone = temporal_tree`. The current
implementation also begins to read back a small number of backing atoms from each temporal block's
`derived_from` provenance, which moves the temporal backbone from pure summary retrieval toward
structured evidence composition. This remains an early implementation rather than a finished
subsystem, but it moves the temporal backbone from pure blueprint into executable main-code behavior.

The temporal backbone also now has an explicit query-time anchor path in main code. Relative
queries such as "yesterday" and "last week" are no longer forced to resolve against runtime wall
clock time alone: `RequestContext.query_time` can anchor both episode retrieval and temporal-tree
key selection. This matters because long-horizon benchmarks and offline agent-memory evaluation
frequently ask temporally relative questions from a historical point in the conversation, not from
the machine's current date.

This draft therefore focuses on a realistic next step rather than an inflated claim:

> EchoMemory should evolve into a **dual-backbone multimodal temporal graph memory system**, where a temporal tree and a relation graph cooperate under query planning, while image observations become first-class memory evidence.

---

## 2. Problem framing

We focus on four recurrent failure modes in long-horizon personal-agent memory:

1. **temporal flattening**
   - event time collapses into write time or mention time
2. **relation under-routing**
   - relation-heavy questions are answered from flat summaries rather than graph evidence
3. **visual evidence loss**
   - screenshot/OCR facts are not represented as first-class memory objects
4. **readiness confusion**
   - newly written observations are treated as QA-ready before downstream projections complete

These become visible in questions such as:

- When did the user actually sign the lease?
- Who introduced the user to a future collaborator?
- What was visible in the screenshot on the arrival day?
- What plan was formed after a specific departure event?

The core claim of this draft is that these are not all the same retrieval problem.

---

## 3. Current EchoMemory structure

The current repository is already structurally richer than a typical memory-augmented chatbot.

### 3.1 Session stream

The session layer keeps an append-only record of interaction turns, typically through artifacts like:

- `messages.jsonl`
- `meta.json`
- `abstract.md`

This layer is the source of truth and makes asynchronous consolidation possible.

Primary code anchor:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/session_service.py`

### 3.2 Atomic plane

The most valuable current subsystem is the atom-first pipeline:

- parse new turns after a cursor
- extract atomic units
- merge against active atoms
- persist atom files
- index vectors
- sync graph

The atom schema already carries important fields:

- `statement`
- `subject / predicate / object`
- `created_at`
- `event_time`
- `state_kind`
- `evidence_refs`
- `salience_score`

Primary code anchors:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/atom_first_pipeline.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/utils/domain/atomic_memory.py`

### 3.3 Organized memory

`OrganizedProjector` currently derives:

- `profile`
- `overview`
- `entities`
- `events`

The current projector is deterministic and practical, but it is still closer to a structured organization layer than a true retrieval backbone.

Primary code anchor:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/workers/organized_projector/projector.py`

### 3.4 Graph plane

The graph layer is already real, not decorative.

It supports:

- atom nodes
- entity links
- event links
- temporal_next edges
- image evidence nodes
- image-to-entity and image-to-atom relations

Primary code anchor:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/graph/sync.py`

### 3.5 Search layer

The current retrieval logic is the most capable but also the most crowded layer. It mixes:

- intent recognition
- planner-like routing
- layered search
- graph expansion
- text fallback
- result fusion

This is why the repository is promising but not yet cleanly factored. The system has many of the right pieces, but the pieces are still too entangled.

Primary code anchors:

- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/search_service.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/planner/query_planner.py`
- `/Users/chx/Code/echomemory/echo_memory_v006/echomem/index_engine/planner/intent_classifier.py`

---

## 4. Method proposal: dual-backbone multimodal temporal graph memory

The method line proposed in this draft is:

> A long-horizon personal-agent memory system should maintain both a temporal abstraction backbone and a relation graph backbone, and route different query families to different primary evidence paths.

### 4.1 Memory planes

We recommend five planes:

1. **Session stream**
   - append-only text and image observations
2. **Atomic plane**
   - fact / event / relation / plan atoms
3. **Temporal tree**
   - year / month / day / episode abstraction blocks
4. **Relation graph**
   - entity / event / fact / image_evidence nodes with typed edges
5. **Readiness plane**
   - progress state for downstream answerability

### 4.2 Why temporal tree

The temporal tree is not just a storage optimization. It solves a distinct retrieval problem:

- graph answers: what is connected?
- tree answers: when did it happen, and what surrounds it chronologically?

This is particularly important for:

- date lookups
- before/after questions
- month or period summaries
- relative-time resolution

In the current repository state, this abstraction is already partially grounded in code.
The main repository now writes temporal blocks such as:

- `echo://{account_id}/memory/temporal_tree/year/{yyyy}.md`
- `echo://{account_id}/memory/temporal_tree/month/{yyyy-mm}.md`
- `echo://{account_id}/memory/temporal_tree/day/{yyyy-mm-dd}.md`

and `SearchService` can directly read these blocks when temporal queries are routed to
the tree backbone, then read back a few supporting event atoms from provenance. The next
step is not inventing the tree from scratch, but deepening its retrieval quality and
evidence composition.

### 4.3 Why relation graph

The graph is the natural retrieval substrate for:

- relation questions
- event participation
- company / person / place linking
- image evidence grounding
- multi-hop expansion

The graph should therefore be a primary backbone for:

- relational
- temporal-relational
- visual

query families.

### 4.4 Why dual-backbone

Tree-only systems are strong on chronology and weak on associative traversal.
Graph-only systems are strong on relation traversal and weak on chronology organization.

A dual-backbone system uses both:

- temporal tree as primary for temporal queries
- relation graph as primary for relational and visual queries
- supporting backbone as secondary evidence

### 4.5 Why readiness-aware memory

A memory system should not collapse "written" into "answerable".

We therefore recommend explicit readiness states such as:

- `messages_persisted`
- `atoms_ready`
- `graph_ready`
- `tree_ready`
- `qa_ready`

This is architecturally important because it prevents false-ready behavior and gives a cleaner evaluation contract.

---

## 5. Query planning

The planner should choose a backbone, not merely a memory type label.

Recommended families:

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
   - primary: graph with image evidence
   - support: tree
5. **general**
   - primary: overview / temporal tree
   - support: graph

The main-code planner extraction already started moving in this direction:

- dedicated intent classifier
- dedicated query planner
- planner metadata appended to explanations

That does not yet complete the method, but it gives a real insertion point for future implementation.

---

## 6. Related work: 30-paper map

This section intentionally mixes:

- top-conference papers
- benchmark papers
- strong recent primary-source system papers

because the practical design problem requires all three.

### 6.1 Benchmark and evaluation pressure

1. LoCoMo — https://arxiv.org/abs/2402.17753  
2. LongMemEval — https://arxiv.org/abs/2410.10813  
3. LongMemEval-V2 — https://arxiv.org/abs/2605.12493  
4. Regimes — https://arxiv.org/abs/2606.10241  
5. When Stored Evidence Stops Being Usable — https://arxiv.org/abs/2605.07313  
6. WhenLoss — https://arxiv.org/abs/2605.24579  

Contribution to our narrative:

- they establish that memory evaluation must separate storage, retrieval, and answer usability
- they strongly support readiness-aware evaluation

### 6.2 Hierarchical and temporal retrieval

7. RAPTOR — https://arxiv.org/abs/2401.18059  
8. MemoRAG — https://arxiv.org/abs/2409.05591  
9. GraphReader — https://arxiv.org/abs/2406.14550  
10. ByteRover — https://arxiv.org/abs/2604.01599  
11. TiMem — https://arxiv.org/abs/2601.02845  
12. Hierarchical Memory — https://arxiv.org/abs/2507.22925  

Contribution to our narrative:

- they motivate multi-stage retrieval
- they support the temporal tree hypothesis
- they support coarse-to-fine route selection

### 6.3 Graph and structured recall

13. HippoRAG — https://arxiv.org/abs/2405.14831  
14. From RAG to Memory — https://arxiv.org/abs/2502.14802  
15. Zep — https://arxiv.org/abs/2501.13956  
16. LEGO-GraphRAG — https://arxiv.org/abs/2411.05844  
17. H-Mem — https://arxiv.org/abs/2605.15701  
18. APEX-MEM — https://arxiv.org/abs/2604.14362  

Contribution to our narrative:

- they make graph retrieval a core memory mechanism rather than an accessory
- they support hybrid structured memory

### 6.4 Memory lifecycle and systems

19. Mem0 — https://arxiv.org/abs/2504.19413  
20. LightMem — https://arxiv.org/abs/2510.18866  
21. MemOS — https://arxiv.org/abs/2505.22101  
22. Infini Memory — https://arxiv.org/abs/2606.10677  
23. AgentIR — https://arxiv.org/abs/2605.25092  
24. ConvMemory — https://arxiv.org/abs/2605.28062  

Contribution to our narrative:

- they treat memory as a lifecycle and systems problem
- they support hot-path vs cold-path separation
- they support governed memory planes

### 6.5 Agentic control and multimodal direction

25. MIRIX — https://arxiv.org/abs/2507.07957  
26. Mem-T — https://arxiv.org/abs/2601.23014  
27. E-mem — https://arxiv.org/abs/2601.21714  
28. D-MEM — https://arxiv.org/abs/2603.14597  
29. Field-Theoretic Memory — https://arxiv.org/abs/2602.21220  
30. Self-RAG — https://openreview.net/forum?id=hSyW5go0v8  

Contribution to our narrative:

- they support explicit memory policy
- they support typed and multimodal memory
- they support second-pass retrieval reflection

### 6.6 Important correction note

Some earlier local notes in this workspace included guessed or stale arXiv mappings. This draft deliberately uses the revalidated paper map assembled in:

- `/Users/chx/locomo-eval-web/docs/echomemory_mm_30paper_map_and_nano_benchmark_20260613.md`

This matters because a paper draft becomes brittle very quickly if the reference graph is wrong.

---

## 7. Experimental evidence so far

### 7.1 Nano benchmark

We now have a stronger controlled toy benchmark:

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark.py`
- results:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark_results.json`
- html:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_benchmark_20260613.html`

Current result:

- 12 cases total
- tree-only: 3 / 12
- graph-only: 5 / 12
- dual-backbone: 8 / 12

Per-family:

- temporal: dual matches tree and exceeds graph
- visual: dual matches graph and exceeds tree
- relational and temporal-relational: dual is stronger than tree, but still not saturated

Interpretation:

- this is not benchmark-scale proof
- it is strong mechanism-level evidence for the dual-backbone hypothesis

### 7.2 Earlier nano evidence still relevant

Earlier nanos remain useful for narrower claims:

- readiness and false-ready behavior
- event-time vs write-time distinction
- multimodal evidence necessity
- planner decomposition

These nanos are still valuable because they isolate specific architectural hypotheses before we scale them into the main codebase.

### 7.3 Anchored temporal ablation

We now also have a more focused nano ablation that tests a narrower but important claim:

> On anchored relative-time questions, a temporal tree is a more stable primary backbone than graph-only retrieval, while a dual-backbone route can preserve that stability and still expose graph evidence as support.

Artifacts:

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation.py`
- results:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation_results.json`
- html:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_anchored_temporal_ablation_20260614.html`

This ablation intentionally does not attempt to measure general benchmark quality.
It only evaluates anchored temporal questions such as:

- `What happened yesterday?`
- `What happened last week about marketing and analytics tools?`
- `When was Jon in Rome?`

with explicit `query_time` values.

Current result:

- 3 cases total
- tree-only: 3 / 3
- graph-only: 2 / 3
- dual-backbone: 3 / 3

Interpretation:

- tree-only is strongest when chronology navigation itself is the core problem
- graph-only can still recover facts, but is less reliable as the sole temporal navigation surface
- dual-backbone preserves the temporal stability of the tree path while still retaining graph evidence as secondary support

This is still only mechanism-level evidence, but it is more specific than the earlier mixed-family toy benchmark. It supports a cleaner claim: the primary backbone for relative-time questions should be chronology-aware.

### 7.4 Main-code evidence

We also have real-code evidence that the repository is already moving in the right direction:

- planner extraction started
- search explanation now includes query-plan metadata
- graph/image evidence layer already exists
- atom-first pipeline remains the strongest structural asset
- `RequestContext.query_time` now anchors relative-time interpretation in main code
- episode retrieval and temporal-tree retrieval now share the same query-time anchoring direction

This is why the paper can claim "architecture with implementation anchors" rather than merely "future design sketch."

### 7.5 Relation-heavy ablation

We also now have the complementary small ablation for relation-heavy questions.

Artifacts:

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation.py`
- results:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_results.json`
- html:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_report.html`

This ablation intentionally focuses on graph-shaped questions such as:

- `Who is Gina married to?`
- `What did Gina plan after leaving Figma?`
- `Which company did Gina leave?`

Current result:

- 3 cases total
- tree-only: 0 / 3
- graph-only: 3 / 3
- dual-backbone: 3 / 3

Interpretation:

- tree-only can still surface the right timeline region, but it is the wrong primary retrieval surface for relation-centric questions
- graph-only is the most natural backbone for participant, spouse, and company-of-event questions
- dual-backbone preserves graph-first behavior while still retaining timeline support as a secondary evidence surface

Together with the anchored temporal ablation, this creates a paired mechanism-level result:

- relative-time questions prefer a chronology-aware primary backbone
- relation-heavy questions prefer a graph-aware primary backbone

This is still not large-benchmark evidence, but it is a cleaner experimental argument for query-family-aware backbone routing than relying on a single mixed toy table alone.

---

## 8. What the current evidence supports

The current evidence supports the following limited but meaningful claims:

1. long-horizon agent memory should be modeled as stream-to-structure projection
2. chronology and relation traversal are distinct retrieval problems
3. a dual-backbone design is more appropriate than a flat unified evidence pool
4. readiness gating is a systems correctness issue, not just an engineering convenience
5. screenshot/OCR evidence should become a first-class memory object in the multimodal path

The current evidence does **not yet** support the following strong claims:

1. benchmark-scale state-of-the-art on LoCoMo or LongMemEval
2. mature multimodal agent-memory performance on a large public benchmark
3. full production-grade dual-backbone retriever in the main codebase

This distinction is essential if the final paper is to remain credible.

---

## 9. Implementation roadmap

The most direct implementation roadmap is:

1. keep the current atom-first substrate
2. add a real temporal tree projector after organized projection
3. continue factoring `SearchService` into:
   - intent classifier
   - planner
   - executor
   - fusion
4. promote graph-first retrieval for relational and visual families
5. add readiness manifests and lifecycle gating
6. promote image evidence into first-class planned retrieval

The most important principle is:

> do not add dataset-specific hacks; add retrieval structure that generalizes across memory benchmarks.

---

## 10. Submission path

### 10.1 What would make this a believable CVPR submission

At minimum:

1. main-code temporal tree implemented
2. multimodal evidence genuinely routed by planner
3. benchmark evidence beyond toy nanos
4. at least one real visual-memory evaluation setting
5. stronger ablations on:
   - tree only
   - graph only
   - dual backbone
   - with and without readiness

### 10.2 What can already be drafted

Already draftable:

- problem framing
- architecture
- related work
- method motivation
- initial toy benchmark evidence
- implementation-grounded roadmap

Not yet draftable as final claims:

- full benchmark superiority
- mature multimodal retrieval gains
- strong efficiency claims

---

## 11. Bottom line

The expansion from 10 to 30 papers did not dilute the direction. It made the direction sharper.

The strongest conclusion remains:

> EchoMemory should evolve into a readiness-aware, planner-routed, dual-backbone memory architecture in which a temporal tree and a relation graph cooperate, while image evidence becomes a first-class memory object.

This is currently the most coherent path from:

- the real EchoMemory repository
- the nano explanations
- the benchmark pressure from recent memory work

to a future paper that could plausibly target a CVPR-shaped multimodal memory story.
