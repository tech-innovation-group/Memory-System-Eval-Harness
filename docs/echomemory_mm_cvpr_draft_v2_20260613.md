# EchoMemory-MM Draft v2

Date: 2026-06-13

## Working Title

**EchoMemory-MM: Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Positioning

This draft is an **honest research draft**, not a claim of a completed submission-ready CVPR paper.

What is already supported by current repository evidence:

1. The main EchoMemory codebase now contains a real multimodal-oriented memory path:
   - image observations can be written into the session stream
   - image observations can be synchronized into graph memory as `image_evidence`
   - visual retrieval can be explicitly routed by `visual_lookup`
2. Unit tests already cover key multimodal graph behaviors:
   - `sync_image_observation`
   - `link_image_evidence_to_atom`
   - visual seed preference in graph retrieval
3. The research repository contains runnable toy experiments:
   - `nano_paper_method_tgmm.py`
   - `nano_paper_method_tgmm_ablation.py`
   - `nano_multimodal_ablation_experiment.py`
4. These toy experiments already support two narrow but meaningful system claims:
   - image evidence changes retrieval behavior on visual questions
   - OCR-only answers cannot be recovered by text-only memory in some cases
5. We now also have a graph-planning toy ablation:
   - lexical baseline: `1/4`
   - graph-first retrieval: `3/4`
   - graph-path retrieval: `4/4`
   - this supports the planner claim that temporal / relational / visual questions should not be treated as plain text matching problems

What is **not** yet established:

1. The real production path is not yet a fully mature multimodal memory pipeline
2. There is not yet a real benchmark-scale multimodal evaluation
3. Runtime multimodal grounding remains incomplete compared with the desired end-state
4. Therefore the strongest valid framing today is:
   - **a text-first temporal graph memory system with an implemented early multimodal extension**

---

## Abstract

Long-horizon personal agents increasingly operate over heterogeneous evidence streams that include text, screenshots, interface captures, and document snippets. Existing long-term memory systems for agents remain predominantly text-first: they preserve conversational facts, summaries, and sometimes relational structure, but they do not treat visual evidence as a first-class long-term memory object. This creates a recall gap for questions whose answers are only visible in screenshots, OCR-bearing images, or layout-grounded interface regions. We propose **EchoMemory-MM**, a multimodal temporal graph memory architecture that extends a stream-to-structure memory system with image-evidence nodes, multimodal grounding edges, and planner-guided retrieval over text and visual memory planes. EchoMemory-MM preserves the incremental structure of append-only session streams while allowing screenshots and other visual observations to enter the same temporal memory graph as facts, events, and entities. In the current repository state, we instantiate this design as both an early main-code extension and a set of runnable nano prototypes. Our toy studies support two core claims: visual queries should prioritize image-evidence nodes instead of text-only summaries, and OCR-only answers cannot be reliably recovered from text-only memory alone. These results motivate a full multimodal memory system in which visual grounding is part of long-term memory construction rather than an external attachment.

---

## 1. Introduction

Long-term memory has become a central systems problem for personal agents. Recent memory architectures have improved retrieval over long conversational histories, entity-centric facts, and temporal event traces. However, the dominant assumption is still text-first memory. Even when agents observe screenshots, dashboards, slides, scanned documents, or interface captures, these artifacts are usually stored as attachments, external blobs, or loosely referenced metadata rather than memory objects that participate in structured recall.

This is a serious limitation for real-world agents. Many personal-agent questions are intrinsically visual:

- What time was visible in the screenshot?
- Which city name appeared on the station board?
- What style reference did the user save for later?
- Which slide version or UI layout did the user point to?

The answer to these questions may not exist in the textual conversation at all. It may live only in OCR, image captioning, or layout-grounded visual evidence.

In this work, we explore a multimodal extension to a long-horizon memory system, EchoMemory. The existing EchoMemory line already provides a useful text-side systems foundation:

- append-only session streams
- incremental atom extraction
- organized memory projection
- graph synchronization
- planner-aware retrieval logic

Our central claim is that this structure can be extended so that **visual evidence becomes part of the memory graph itself**, rather than an external attachment interpreted only at response time.

We call this extension **EchoMemory-MM**.

### Contributions

At the current repository state, our contributions should be stated conservatively:

1. We define a **multimodal temporal graph memory architecture** in which screenshots and related visual observations are represented as `image_evidence` nodes connected to facts, events, and entities.
2. We implement an **early multimodal path in the main codebase**, including image-observation graph synchronization and explicit visual retrieval intent support.
3. We provide **runnable nano prototypes and toy ablations** showing that visual evidence changes retrieval behavior in cases where text-only memory is insufficient.
4. We identify the remaining systems gap between a promising multimodal prototype and a full benchmark-ready multimodal memory system.

---

## 2. Related Work

This work sits at the intersection of long-term conversational memory, temporal graph memory, graph-based retrieval, and multimodal evidence reasoning.

### Long-term conversational memory benchmarks

**LoCoMo** established long-horizon conversational memory as a benchmark setting where agents must answer factual, temporal, and multi-hop questions over multi-session histories.  
**LongMemEval** further highlighted knowledge updates, temporal reasoning, and abstention as core evaluation axes.

These benchmarks motivate our insistence that memory systems must represent not only “what was said” but also “when it was true” and “which evidence supports it.”

### Text-first memory systems

Systems such as **Mem0** and other production memory layers show that atomic fact extraction and efficient retrieval can significantly improve personal agents. However, these systems are still primarily optimized for textual memories and semantic retrieval.

### Temporal and graph memory systems

Systems in the **Graphiti / temporal knowledge graph** family make an important point: long-term memory must represent temporal validity, versioning, and relationship evolution, not just static facts.

**HippoRAG**-style graph retrieval further demonstrates that graph-based recall can become a primary reasoning substrate for multi-hop problems rather than a secondary explanation layer.

### Multimodal memory gap

Despite strong progress in multimodal foundation models, relatively few long-term memory systems treat screenshots or visual observations as first-class persistent memory objects. Existing approaches often process images at inference time, but do not preserve them as structured long-term evidence linked to entities, events, and facts.

EchoMemory-MM is motivated by this gap.

---

## 3. EchoMemory-MM Overview

EchoMemory-MM extends a text-first stream-to-structure memory system into a multimodal temporal graph memory system.

### 3.1 Memory planes

We describe the architecture using five memory planes:

1. **Session plane**
   - append-only stream of text turns and multimodal observations
2. **Atomic text plane**
   - facts, events, preferences, plans, and relational atoms extracted from text
3. **Visual evidence plane**
   - screenshots, OCR-bearing images, interface captures, document regions
4. **Temporal graph plane**
   - graph nodes for facts, events, entities, and image evidence
5. **Structured memory plane**
   - organized blocks such as profile, timeline, plan summaries, or multimodal dossiers

### 3.2 Core design principle

The key design principle is:

> visual observations should be promoted to memory objects before question answering, not merely attached at answer time.

This allows retrieval to operate over evidence-bearing memory objects rather than over a text-only approximation of the user’s past observations.

---

## 4. Memory Construction

### 4.1 Session stream

Incoming observations are appended to an immutable session stream. In the current codebase, session messages can already carry:

- `created_at`
- `role_id`
- `obs_type`
- `resource_uri`
- `mime`
- `caption`
- `ocr`
- `linked_subject`
- `tags`

This is important because the multimodal problem starts at ingestion. If screenshots are not represented at write time, they cannot become stable long-term evidence later.

### 4.2 Atomic memory extraction

Text turns are incrementally converted into candidate facts, events, relations, or preferences by the atom extraction pipeline. This provides the textual fact substrate that later supports graph reasoning.

### 4.3 Visual evidence nodes

For image observations, EchoMemory-MM creates `image_evidence:{message_id}` graph nodes whose properties may include:

- caption
- OCR
- resource URI
- MIME type
- linked subject
- tags
- observation timestamp

This is the minimum viable representation of visual long-term memory.

### 4.4 Grounding edges

Visual evidence becomes more than an isolated blob when connected to other memory objects. The intended edge types include:

- `image_evidence -> shows -> entity`
- `image_evidence -> visual_evidence_of -> fact`
- `image_evidence -> supports_event -> event`

These edges turn visual evidence into graph-participating memory rather than passive storage.

---

## 5. Retrieval and Planning

The key retrieval question is not “Should we also search image captions?”

The real question is:

> When should the planner treat a query as visual-first?

### 5.1 Planner intents

A multimodal planner can distinguish between at least:

- `visual`
- `visual_temporal`
- `visual_relational`
- `text_temporal`
- `text_relational`
- `general`

### 5.2 Routing behavior

Example routing behaviors:

- screenshot/OCR query -> `image_evidence` first
- visual timestamp query -> `image_evidence` -> `event`
- style/layout query -> `image_evidence + fact`
- textual relational query -> `event + fact`, optionally using visual evidence as support

### 5.3 Early support in current main code

The current main repository already contains:

- `visual_lookup` as an explicit strategy
- graph retrieval paths that can prioritize `image_evidence`
- graph seed handling that allows image evidence to become first-class retrieval seeds

This does not yet make the full system benchmark-ready, but it is already beyond “paper-only” design.

---

## 6. Toy Evidence From Runnable Nano Experiments

We use toy studies not as benchmark claims, but as mechanistic evidence that the proposed structure matters.

### 6.1 Structural ablation

The nano method ablation compares:

1. flat facts only
2. typed blocks only
3. full temporal graph + multimodal evidence

Observed result:

- Flat facts only: `3/4`
- Typed blocks only: `3/4`
- Full TG+MM: `4/4`

The stable difference appears on the visual question. This supports the interpretation that visual evidence nodes are not decorative: they change retrieval success where text memory alone has no direct answer.

### 6.2 Multimodal toy study

The multimodal toy experiment compares text-only retrieval with multimodal retrieval.

Observed result summary:

- `text_only_correct = 3`
- `multimodal_correct = 3`
- `visual_only_gain_cases = 2`

This is a useful result because it shows the correct multimodal story:

- multimodality does not need to dominate text-only on every query
- the actual value appears when evidence routing changes on visually grounded questions

In other words, the win is not “everything gets better.”  
The win is “the correct evidence path becomes available.”

### 6.3 Graph-first planning ablation

We also ran a small toy ablation designed to answer a different systems question:

> Is graph structure merely helpful context, or should temporal / relational / visual questions explicitly route through a graph-first planner?

We compare three retrieval modes:

1. **Lexical baseline**
   - block/fact text matching only
2. **Graph-first**
   - planner prioritizes `event` or `image_evidence` nodes according to query type
3. **Graph-path**
   - graph-first retrieval plus simple path-style expansion over adjacent evidence

Observed result:

- `lexical_correct = 1/4`
- `graph_first_correct = 3/4`
- `graph_path_correct = 4/4`

The important interpretation is not just that graph retrieval helps.  
The stronger interpretation is:

- plain text matching is not enough for temporal and visual queries
- graph-first routing gives a substantial gain by changing the primary evidence entry point
- path-aware graph evidence can further improve multi-hop or relational questions

This matters for EchoMemory because the current main code already contains graph memory and graph retrieval hooks, but the graph layer is still not consistently the default path for these question types. The toy result therefore gives a concrete research reason to push the production planner toward graph-first behavior rather than treating graph recall as an optional fallback.

---

## 7. Current Main-Code Evidence

The present codebase already supports important pieces of the EchoMemory-MM story:

1. **Image observation ingestion fields** exist in session writing.
2. **`sync_image_observation()`** creates `image_evidence` graph nodes.
3. **`link_image_evidence_to_atom()`** creates visual grounding edges.
4. **`visual_lookup`** exists as a recognized retrieval strategy.
5. Unit tests verify:
   - image node creation
   - visual evidence edge creation
   - visual query preference for image evidence seeds
6. The nano repository now also includes a planner-style graph-first ablation showing that temporal / relational / visual queries benefit when graph nodes are treated as first-class retrieval seeds instead of backup evidence.

These facts matter because they move the work from a purely speculative architecture into an implemented early systems branch.

---

## 8. Limitations

The current repository state also has important limitations.

### 8.1 No benchmark-scale multimodal evaluation

We do not yet have a benchmark-scale multimodal memory evaluation in the real system.

### 8.2 Runtime grounding is incomplete

Although grounding edges exist in design and partial implementation, real runtime multimodal grounding remains less mature than the desired final architecture.

### 8.3 Evidence is still strongest at toy/prototype level

The strongest current support for the multimodal claims comes from:

- runnable nano prototypes
- toy ablations
- unit-test evidence

This is valuable but not enough for a submission-ready empirical claim.

### 8.4 The main branch remains text-first

The core EchoMemory system is still best characterized as a text-first temporal graph memory system with an early multimodal extension path.

---

## 9. Experimental Roadmap Toward a Real Submission

To turn EchoMemory-MM into a real submission candidate, we would prioritize:

1. **Real image observation ingest in the production memory path**
2. **Stable runtime grounding from image evidence to facts/events/entities**
3. **A controlled multimodal long-term memory benchmark**
4. **Planner-trace analysis**
5. **Ablations over:**
   - text-only
   - text + image metadata only
   - text + image evidence nodes
   - text + image evidence nodes + multimodal planner

---

## 10. Conclusion

EchoMemory-MM explores a simple but important idea: screenshots and other visual observations should not be treated as transient attachments outside memory. They should become persistent, structured, temporally grounded memory objects that participate in recall alongside facts, events, and entities.

At the current repository state, the strongest valid claim is not that EchoMemory-MM is a finished benchmark-winning multimodal memory system. The strongest valid claim is:

> EchoMemory already contains a strong text-first temporal memory substrate, and the repository now demonstrates an implemented early multimodal extension in which visual evidence can enter graph memory and alter retrieval behavior on visual queries.

That is a meaningful and defensible step toward a true multimodal long-term memory system for personal agents.
