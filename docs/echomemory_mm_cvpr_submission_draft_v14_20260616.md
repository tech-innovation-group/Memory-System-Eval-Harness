# EchoMemory-MM CVPR Submission Draft v14

Date: 2026-06-16 (late update)

## Title

**EchoMemory-MM: Contract-Driven Dual-Backbone Multimodal Temporal Graph Memory with Topic-Centered Middle-Layer Retrieval**

## Abstract

Long-horizon personal agents must preserve evolving user facts, reconstruct temporally ordered events, answer relation-heavy questions, track cross-session topic evolution, and increasingly ground responses in visual observations such as screenshots or OCR-bearing images. Existing memory systems often flatten these demands into one retrieval pool, forcing chronology-heavy, relation-heavy, longitudinal, visual, and readiness-sensitive queries through the same evidence path. We propose **EchoMemory-MM**, a contract-driven memory architecture that organizes append-only interaction streams into atomic memories, a topic-centered middle layer, a temporal abstraction tree, a relation graph, a readiness plane, and a shared evidence contract. The core idea is that temporal questions, relation-heavy questions, and longitudinal status/progress questions should not share the same primary retrieval backbone, persisted memory is not automatically answer-ready, event time should not be collapsed into write time, and evidence expansion should be guided by missing evidence types rather than by fixed graph-only retries. EchoMemory-MM therefore routes relative-time questions to a chronology-aware temporal tree, relation-heavy and visually grounded queries to a graph backbone, and longitudinal cross-session questions to a topic-centered dossier layer; it further gates answer generation on lifecycle readiness, uses coverage-aware retrieval gating to prevent premature stopping, and adds a type-aware second pass that expands only the supporting readers implied by the missing contract. We ground this proposal in the current EchoMemory codebase, where the new topic-dossier layer is now integrated into projection, planning, retrieval ordering, answer-time self-check, and second-pass support. We further provide a canonical nano implementation family and fourteen evidence lines: a generalized method-prototype nano, a unified Memory-OS-style dual-backbone nano, a three-clock temporal-semantics ablation, anchored temporal ablations, relation-backbone ablations, a dual-backbone toy benchmark, a readiness on/off ablation, a self-check v2 experiment, a 21-case real-code family subset benchmark over the current `SearchService`, a coverage-aware gating ablation, a type-aware second-pass ablation, a new topic-dossier ablation, a query-family paraphrase robustness benchmark, and a paper-facing experiment inventory that distinguishes complete, partial, and reference evaluation lines. Across these lines, temporal-only, graph-only, longitudinal-middle-layer, dual-backbone, readiness-gated, self-check-enabled, coverage-aware, type-aware, lifecycle-aware, paraphrase-robust, and generalized-method variants exhibit a consistent pattern: time questions prefer chronology-aware routing, relation-heavy and visual questions prefer graph-aware routing, longitudinal status questions benefit from a topic-centered middle layer, readiness gating is required to convert stored evidence into answer-time correctness, confidence should not substitute for evidence sufficiency, event time should not be confused with write time, lifecycle state should not be ignored once memory evolves, paraphrase robustness depends on family routing rather than benchmark-specific trigger words, and second-pass retrieval is most effective when it is aligned with the missing evidence family. While the current evidence does not establish benchmark-scale superiority, it supports a narrower but stronger conclusion: long-horizon memory should be modeled as a contract-driven, planner-routed, stream-to-structure system with explicit answer-time policy, explicit time semantics, explicit lifecycle/readiness state, and an explicit middle layer for cross-session topic evolution rather than as a flat unified retrieval pool.

**v14 note.** This revision does not claim a new benchmark-scale result. Its main change is packaging clarity: the method is now paired with a family-level benchmark panel that separates temporal, relational, longitudinal, visual, and readiness-sensitive behaviors, plus a code-grounded 30-paper appendix that makes explicit which ideas are already implemented, which are only partially reflected in code, and which remain open gaps.

## 1. Introduction

Long-horizon memory failures in personal agents are often described as retrieval failures. This framing is too coarse. In practice, at least five different failures repeatedly appear:

1. a temporal question is answered from write time rather than story time
2. a relation-heavy question is answered from broad summaries rather than graph-connected evidence
3. a visually grounded question is answered without elevating image evidence to first-class memory
4. a newly written message is treated as answerable before downstream consolidation completes
5. a system with partially relevant retrieval still answers too early instead of expanding supporting evidence or abstaining

These failures are usually exposed through a single retrieval interface, but they are not the same problem. A question such as “What happened yesterday?” should not be handled in the same way as “Who introduced Jon to Lena?”, and neither should be handled the same way as “Can the system answer yet?” immediately after a write completes. Even after good retrieval, there remains an answer-time policy question: should the system answer now, search one more structured view, or abstain?

The current EchoMemory repository already suggests a stronger decomposition. It contains an append-only session stream, an incremental atom-first pipeline, organized memory projection, graph synchronization, temporal-tree projection, and planner-like retrieval logic. The opportunity is therefore not to invent a new stack from scratch, but to turn these existing planes into a more explicit memory architecture and evaluate that architecture in a way that preserves family-specific behavior rather than flattening all success and failure into one pooled retrieval score.

We study the following thesis:

> Long-horizon memory should be modeled as a query-time anchored, dual-backbone, readiness-aware, self-checking stream-to-structure system.

The intuition is simple. Temporal questions should prefer a chronology-aware backbone. Relation-heavy and visually grounded questions should prefer a graph-aware backbone. Longitudinal “how did this evolve?” questions should prefer a topic-centered middle layer rather than either a global overview or flat atoms alone. Persisted memory should not be treated as automatically answer-ready. Event occurrence time should not be silently replaced by write time. Finally, even after retrieval, the system should explicitly inspect whether the evidence shape matches the query family before answering. These pressures jointly motivate **EchoMemory-MM**, which organizes memory into a session stream, an atomic plane, a topic-centered middle layer, a temporal tree, a relation graph, a readiness plane, a shared evidence contract, and a lightweight answer-time self-check policy.

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we provide:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation family
- a family-level benchmark panel
- a code-grounded 30-paper appendix
- a paper-facing experiment inventory that distinguishes complete versus partial benchmark lines

Together, these support a narrower but grounded claim: flattening all memory demands into a single retrieval pool is an architectural mistake for long-horizon multimodal agents, and strong retrieval alone is still not enough without answer-time policy.

Concretely, this draft now centers five paper-level contributions:

1. a **contract-driven retrieval architecture** that unifies query planning, retrieval gating, and answer-time self-check
2. a **dual-backbone memory design** in which chronology-heavy and relation-heavy questions no longer share the same primary evidence route
3. a **topic-centered middle layer** that sits between global overview and flat atoms for cross-session status / progress / evolution queries
4. a **readiness-aware and type-aware retrieval policy** that separates persistence from answerability and supplements only the missing evidence families
5. a **code-backed evaluation package** spanning 30-paper positioning, nano experiments, focused real-code behavior checks, a family-level benchmark panel, a canonical bibliography/action map, a code-grounded 30-paper appendix, and a paper-facing experiment inventory

## 2. Related Work

### 2.1 Benchmark pressure

Recent benchmarks make clear that long-horizon memory is not one scalar capability. LoCoMo and LongMemEval emphasize temporal, relational, update-sensitive, and cross-session conversational pressures. LongMemEval-V2 extends this framing toward more agent-like tasks. Regimes, When Stored Evidence Stops Being Usable, and WhenLoss further argue that memory quality should be decomposed into write-path correctness, retrieval usability, answer-time faithfulness, and lifecycle readiness.

For EchoMemory-MM, the key lesson is that memory systems should not be judged only by whether evidence was stored. They should also be judged by whether the evidence remains retrievable, interpretable, answerable at the right time, and supported by enough evidence to justify a response.

### 2.2 Hierarchical and temporal retrieval

RAPTOR, MemoRAG, ByteRover, TiMem, and related hierarchical-memory work support the same principle: long-horizon recall improves when memory is exposed through structured abstraction rather than only flat retrieval. Time should become a navigation surface rather than a passive metadata field. The same family of work also suggests that there is often a useful middle layer between a global overview and flat local evidence: topic-, dossier-, or cluster-centered memory objects that preserve within-topic coherence across long horizons.

This is directly relevant to EchoMemory because the current organized projection already emits a temporal tree. The architectural question is therefore not whether a tree should exist, but whether it should become the primary backbone for chronology-heavy queries.

### 2.3 Graph and structured recall

HippoRAG, GraphReader, From RAG to Memory, Zep, LEGO-GraphRAG, H-Mem, and APEX-MEM argue for graph-structured or semi-structured memory as an active recall substrate. Their shared lesson is that relation-heavy or event-linked questions should not be forced through the same route as chronology-heavy questions.

This is the second pressure behind EchoMemory-MM: relation-heavy and visual questions should prefer graph-first evidence paths, with temporal structure used as support rather than as the primary evidence route.

### 2.4 Lifecycle and systems

Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not just retrieval. It is also a lifecycle and systems problem involving extraction cost, consolidation delay, routing policy, and evidence maintenance.

This motivates the readiness plane in EchoMemory-MM. Durability is weaker than answerability. A system that answers immediately after persistence, without checking whether memory is truly answer-ready, is making a systems mistake.

### 2.5 Policy and multimodal direction

MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward two future-facing themes: memory actions should be policy-aware, and multimodal observations should become first-class memory. EchoMemory-MM sits naturally at this intersection. Our current multimodal evidence remains architectural rather than benchmark-complete, but it is already sufficient to motivate image-evidence-first retrieval and lightweight answer-time self-check.

## 3. Problem Formulation

We distinguish four notions of time:

- **write time**: when a message is durably persisted
- **story time**: when the described event actually occurred
- **mention time**: when an earlier event is revisited or narrated inside the interaction stream
- **query time**: the temporal anchor against which relative expressions such as “yesterday” should be resolved

We further distinguish five major query families:

1. **temporal queries**: date lookup, relative-time, ordering, chronology
2. **relation-heavy queries**: spouse, introducer, plan-after-event, multi-hop entity linkage
3. **longitudinal queries**: status, progress, latest state, how a topic evolved across sessions
4. **visual queries**: OCR-bearing photos, screenshots, image-grounded evidence lookup
5. **readiness-sensitive queries**: whether the system should answer now or defer until memory becomes answer-ready

Finally, we define a readiness lifecycle:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

The design objective is to prevent these pressures from being flattened into one retrieval path and one unconditional answer policy. In particular, it should prevent the common collapse of story time, mention time, and write time into one overloaded timestamp field.

## 4. Method

### 4.1 Six-plane memory model

EchoMemory-MM uses six planes:

1. **Session stream**: append-only interaction and observation history
2. **Atomic plane**: fact, event, relation, and plan atoms
3. **Topic dossier plane**: topic-centered longitudinal dossiers that summarize cross-session evolution
4. **Temporal tree**: chronology-oriented abstraction blocks
5. **Relation graph**: event, entity, fact, and image-evidence nodes with typed edges
6. **Readiness plane**: lifecycle state from persisted to answerable

### 4.2 Three-clock temporal semantics

Relative-time expressions should resolve against `query_time`, not runtime wall clock. This matters especially in replay and offline evaluation settings where “yesterday” refers to a historical anchor inside the interaction rather than to the machine’s current date.

However, query-time anchoring alone is not enough if memory storage itself compresses multiple time semantics into one field such as `created_at`. EchoMemory-MM therefore separates:

- `story_time`: the actual event date used for chronology and ordering
- `mention_time`: the conversational revisit point used for retrospective narration and recency reasoning
- `write_time`: the durability timestamp used for lifecycle and readiness

This distinction is not cosmetic. Retrospective mentions, “yesterday”, “last week”, and “before/after” queries all become unstable when event occurrence time is replaced by write time. Our three-clock temporal-semantics ablation below isolates this failure mode directly.

### 4.3 Topic-centered middle layer

The current codebase already contains a global overview and a flat atomic plane. Our new results suggest these two levels are not enough for a class of common long-horizon questions: “what is the latest status of X?”, “how did X evolve?”, or “what happened with the lease / visa / project over time?”. A global overview is too broad, while flat atoms are often too fragmented.

EchoMemory-MM therefore adds a **topic-centered middle layer** in the form of lightweight topic dossiers. A topic dossier is a compact, timeline-backed aggregation of atoms that share a generic topic anchor. It is intentionally simple and generic: it does not rely on benchmark-specific entities or keyword templates, and it does not require a separate LLM summarization stage. Instead, it groups related atoms into a middle-layer memory object that preserves within-topic coherence across sessions.

This plane plays a different role from both the temporal tree and the relation graph:

- the **temporal tree** is optimized for chronology navigation
- the **relation graph** is optimized for entity/event linkage and path grounding
- the **topic dossier** is optimized for longitudinal topic continuity across multiple sessions

### 4.4 Dual-backbone routing with a middle-layer route

EchoMemory-MM defines two primary retrieval backbones and one middle-layer route:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy, event-linked, and visually grounded queries
- **topic dossier** for longitudinal, progress, latest-status, and cross-session evolution queries

The non-primary backbone remains useful as supporting evidence. A temporal query may still benefit from graph evidence that clarifies participants, but its primary route should remain chronology-aware. A longitudinal query may still need atom or temporal support, but its primary route should remain dossier-centered.

### 4.5 Readiness gating

EchoMemory-MM explicitly separates persistence from answerability. A memory system should not answer simply because a write completed. It should answer only when the memory structures required for faithful retrieval are ready.

### 4.6 Shared evidence contract

EchoMemory-MM makes one systems claim that is easy to miss in architectural sketches: dual-backbone retrieval is not enough if different parts of the system silently disagree about what counts as sufficient evidence. In practice, planner logic, layer-skipping logic, and answer-time self-check often drift apart. One module may think a temporal query is sufficiently covered by a summary-like fact, while another implicitly expects event or episode evidence.

We therefore define a lightweight **evidence contract** between query planning and answer-time policy. The contract maps a query family to a required evidence signature, for example:

- temporal: event + fact + episode support
- relational: graph + entity + event + fact support
- longitudinal: topic_dossier + fact support
- profile: fact + profile + entity support

The contract is intentionally generic rather than benchmark-specific. It does not rely on dataset keywords or task-specific entity lists. Instead, it defines what evidence types should be present for a given retrieval family, computes which types are currently present, and exposes matched versus missing evidence to both gating and self-check. This turns evidence sufficiency from an implicit heuristic into an auditable intermediate object.

### 4.7 Retrieval self-check

EchoMemory-MM adds a lightweight retrieval self-check between retrieval and answer generation. The self-check inspects whether the evidence shape matches the query family:

- temporal questions expect chronology-shaped evidence such as day or month tree blocks
- relation-heavy questions expect graph-linked event or entity evidence
- longitudinal questions expect a topic-centered dossier plus fact grounding
- visual questions expect image-evidence nodes or direct OCR-bearing support

If the primary backbone does not provide the expected evidence shape, the system expands to supporting backbones. If the evidence remains structurally weak after expansion, the system abstains with `unknown` instead of forcing an answer. This design is intentionally lighter than a second full reasoning pass. Its goal is not to maximize complexity, but to make answer-time policy explicit and auditable.

### 4.8 Coverage-aware gating and type-aware second pass

The evidence contract is useful twice: before answer generation and before deep retrieval expansion. First, a high-confidence primary hit should not be allowed to terminate retrieval if the planned evidence contract is still incomplete. We call this **coverage-aware gating**. Its role is to prevent the system from confusing confidence with sufficiency.

Second, once the contract exposes which evidence families remain missing, retrieval expansion itself can become structured. Instead of applying a fixed graph-only supplement, EchoMemory-MM uses a **type-aware second pass**:

- missing temporal support -> probe the temporal tree / episode support
- missing longitudinal support -> probe the topic dossier reader
- missing fact grounding -> probe the atomic / fact reader
- missing graph or entity linkage -> probe the graph reader
- missing image-grounded support -> probe image-evidence-bearing graph nodes

This policy is deliberately generic. It is not keyed to any specific benchmark or entity inventory. It only depends on the contract between the query family and the required evidence families.

### 4.9 Generalized nano method prototype

To keep the method line legible, we also provide a new **generalized nano method prototype** that places four ideas into one minimal system:

- three-clock time (`event_time`, `mention_time`, `write_time`)
- planner-routed dual-backbone retrieval (`temporal_tree` vs `graph`)
- a shared evidence contract over query families
- contract-driven supporting retrieval that expands the missing reader rather than issuing a fixed retry

This prototype is intentionally not tied to LoCoMo entities, benchmark-specific templates, or dataset-specific keyword rules. Its purpose is narrower and more explanatory: to show that the proposed architecture remains coherent even when the examples are replaced by generic event, relation, plan, and OCR-bearing image cases. In other words, it is meant to separate *method structure* from *benchmark surface form*.

### 4.10 Multimodal evidence path

Image-bearing observations such as screenshots, photos, and OCR content should enter the session stream, become graph-linked evidence objects, and participate in retrieval rather than remaining auxiliary attachments. The current codebase already contains an early `image_evidence` path in graph synchronization, which motivates the multimodal direction of EchoMemory-MM even though benchmark evidence remains incomplete.

## 5. Code Anchors

The current EchoMemory repository already contains most of the structural substrate required by EchoMemory-MM:

- session lifecycle and append-only storage:
  - `session_service.py`
- atom-first incremental extraction:
  - `atom_first_pipeline.py`
- organized projection to profile / overview / topic dossier / entities / events / temporal tree:
  - `organized_projector/projector.py`
- graph synchronization and image evidence:
  - `graph/sync.py`
- search, routing, temporal retrieval, topic-dossier retrieval, and planner logic:
  - `search_service.py`
  - `query_planner.py`
  - `episode/retriever.py`
- shared evidence contract across gating and self-check:
  - `index_engine/policy/evidence_contract.py`
  - `index_engine/policy/retrieval_gating.py`
  - `index_engine/policy/self_check.py`

The key engineering gap is not absence of structure, but tighter integration between these planes at query time, harder temporal semantics, a stronger middle layer for cross-session evolution, and more explicit answer-time policy after retrieval.

The code-backed story is also stronger than in the earlier draft series. In addition to the architectural code anchors above, the current main-code path now has accompanying patch-level evidence from the temporal-tree integration, three-clock temporal semantics, write-governance improvements, memory-group indexing, and the shared evidence-contract path across retrieval gating and answer-time self-check. The latest focused unit suite covering these paths passes **106 tests**, which matters because it turns part of the paper’s systems claim from “plausible architecture” into “architecture with explicit regression guardrails.”

## 6. Experiments

### 6.1 Canonical nano implementation

We provide a canonical nano implementation that captures the smallest faithful version of EchoMemory-MM: append-only stream, story-time normalization, query-time anchor, temporal tree, graph-backed retrieval, and readiness gating.

### 6.2 Three-clock temporal-semantics ablation

This new ablation isolates one narrow but important question: what happens if a memory system stores only write time, versus separating event time from mention time, versus keeping all three clocks explicit?

- write-time only: **0 / 4**
- event + mention split: **4 / 4**
- three-clock: **4 / 4**

The point of this result is not the raw count. It exposes a structural failure mode. Retrospective mention, “yesterday”, “last week”, and “before the keynote day” style questions all become unstable when write time is treated as a proxy for event occurrence. This directly supports the claim that temporal memory quality is not only about routing to a chronology-aware reader; it also depends on preserving the right clocks in the memory schema.

### 6.3 Generalized method-prototype nano

Beyond single-axis ablations, we also provide a new generalized nano prototype that combines:

- three-clock time
- temporal-tree / graph dual-backbone routing
- image evidence as a first-class memory object
- evidence-contract coverage checks
- contract-driven supporting retrieval

We evaluate it on six deliberately generic cases:

- retrospective event-date recovery
- helper / relation lookup
- plan-after-event lookup
- relative-day chronology
- relative-week chronology
- OCR-grounded visual lookup

The summary pattern is:

- flat text: **4 / 6**
- primary reader only: **2 / 6**
- contract-aware retrieval: **6 / 6**

These counts should not be read as a benchmark score. Their purpose is methodological. They show that once we demand both keyword relevance and contract completeness, the main failure is no longer “nothing was found”, but “the wrong evidence shape was returned.” This is exactly the systems pressure that EchoMemory-MM is trying to formalize.

### 6.4 Unified Memory-OS dual-backbone nano

The generalized-method nano above shows that contract-aware retrieval can be stated in a benchmark-agnostic way. We then added a second, more integrated nano that is closer to the paper's systems story: a **Memory-OS-style dual-backbone nano** that puts six mechanisms into one minimal system:

- write governance over the append-only stream
- three-clock time (`story_time`, `mention_time`, `write_time`)
- a readiness receipt with staged answerability
- lightweight lifecycle state (`active` / `superseded`)
- temporal-tree and relation-graph dual backbones
- contract-aware, type-aware second-pass retrieval

This prototype is not meant as a new benchmark line. Its role is to make the method section more coherent by showing that the major claims can coexist inside one compact system rather than only across many smaller ablations.

We evaluate three retrieval modes on six generic cases:

- flat text: **5 / 6**
- dual-backbone: **6 / 6**
- contract-aware: **6 / 6**

The headline is not the raw score difference. The more important result is structural:

- flat text remains answer-capable on easy lexical cases, but it never satisfies the evidence contract
- dual-backbone fixes the primary retrieval route but still leaves some contract gaps visible
- contract-aware retrieval preserves the stronger primary route and further improves contract completeness by selecting the missing supporting reader rather than issuing a blind retry

This prototype also strengthens the paper's systems argument because it ties together three lines that were previously discussed mostly in isolation: readiness, lifecycle, and typed second-pass expansion.

### 6.4b Topic-dossier ablation

We added a new generic ablation that targets a question not covered by the earlier tree-versus-graph framing:

> when the same topic evolves across multiple sessions, is it enough to rely on a global overview or flat atoms alone, or is a topic-centered middle layer necessary?

The ablation compares three settings:

- **overview-only**: **1 / 5**
- **atom-only**: **3 / 5**
- **topic-dossier**: **4 / 5**

The result matters because it identifies a missing structural layer rather than a missing keyword rule. A global overview is too broad and mixes unrelated topics; flat atoms can recover local facts but often fail to preserve within-topic evolution; a topic dossier restores this missing continuity. This is why the new topic-dossier plane is best understood as a generic middle-layer memory object rather than a benchmark-specific patch.

### 6.5 Anchored temporal ablation

This ablation tests whether relative-time questions prefer a chronology-aware primary backbone:

- tree-only: **3 / 3**
- graph-only: **2 / 3**
- dual-backbone: **3 / 3**

This supports the claim that temporal questions prefer a chronology-aware route.

### 6.6 Relation-backbone ablation

This ablation tests whether relation-heavy questions prefer a graph-aware primary backbone:

- tree-only: **0 / 3**
- graph-only: **3 / 3**
- dual-backbone: **3 / 3**

This supports the claim that relation-heavy questions prefer graph as the primary backbone.

### 6.7 Dual-backbone benchmark

The 12-case dual-backbone benchmark is not benchmark-scale, but it provides an architectural stress test:

- tree-only: **3 / 12**
- graph-only: **5 / 12**
- dual-backbone: **8 / 12**

This suggests that tree and graph cover different failure modes and that dual-backbone routing is more balanced overall.

### 6.8 Readiness ablation

This ablation tests whether persisted memory should be treated as immediately answerable:

- baseline: **1 / 5**
- temporal_graph: **4 / 5**
- full: **5 / 5**

This supports the claim that persisted memory is not automatically QA-ready and that lifecycle gating changes correctness rather than merely user experience.

### 6.9 Self-check v2 experiment

The new self-check v2 experiment tests whether a dual-backbone system should always answer directly from primary-backbone retrieval, or whether it should explicitly inspect evidence shape, expand supporting evidence, or abstain.

- dual-backbone baseline: **4 / 8**
- dual-backbone + self-check: **8 / 8**

The improvement cases are especially informative:

- spouse / relation lookup
- helper lookup
- OCR-grounded lease photo lookup
- OCR-grounded arrival photo lookup

In other words, retrieval self-check helps not because it adds a new memory backbone, but because it improves answer-time policy after structured retrieval.

### 6.10 Real-code self-check smoke and focused ablation

The nano evidence above establishes mechanism plausibility, but it does not by itself prove that the same mechanism survives contact with the current EchoMemory retrieval stack. We therefore ran a focused real-code smoke, and then aggregated it into a small real-code mini subset, directly on the current `SearchService` after integrating the new evidence-review and supporting-backbone expansion logic.

This real-code mini subset is intentionally small and should not be read as a benchmark table. Its purpose is narrower: to test whether self-check and graph-plus-atom complementarity become visible in the actual code path rather than remaining confined to the nano prototype.

The results support three concrete observations:

- **Temporal case**: with self-check disabled, the system returns chronology-shaped evidence but exposes no explicit answer-time audit; with self-check enabled, it adds one more supporting graph evidence item and emits an explicit `review=ok` decision.
- **Relational graph-only case**: after fixing English single-token entity seeding, the graph route becomes strong enough to reach `review=ok`, but the result still exposes `missing=fact_grounding`, showing that relation-path success does not automatically imply fact grounding.
- **Relational graph+atom case**: once atom grounding is added, the remaining `fact_grounding` gap disappears, demonstrating that graph and atom are complementary rather than redundant in the real system.

This focused real-code ablation matters for the paper because it upgrades the self-check story from “supported only by a toy implementation” to “already observable in the actual EchoMemory retrieval loop.” It still does not justify benchmark-scale superiority, but it does materially strengthen the systems claim that dual-backbone retrieval alone is not enough and that answer-time evidence policy changes real retrieval behavior.

### 6.11 21-case real-code family subset benchmark

Beyond the earlier smoke, we constructed a 21-case real-code family subset directly on the current `SearchService`. The subset spans temporal, relational, temporal-relational, longitudinal, visual, and factual query families, and was designed to test whether the proposed evidence-review mechanism becomes visible in the actual retrieval path rather than only in the nano prototype.

The benchmark summary is:

- cases: **21**
- passed against family-level expectations: **21 / 21**
- review marked sufficient: **11 / 21**
- intentionally weak or fail-style structural cases: **10 / 21**

Per-family coverage is:

- none / control: **1 case**
- temporal: **6 cases**
- relational: **4 cases**
- temporal-relational: **3 cases**
- longitudinal: **newly supported as a first-class route in main code**
- visual: **3 cases**
- factual: **4 cases**

This benchmark should not be read as an end-task QA accuracy number. Its role is narrower and more system-facing. It verifies that the proposed family-level retrieval and self-check mechanisms are now visible in the actual code path. In particular:

- temporal questions now expose explicit chronology-shaped evidence and can pull in graph support
- relation-heavy questions cleanly reveal the graph-only versus graph-plus-atom contrast around fact grounding
- temporal-relational, longitudinal, visual, and factual questions surface distinct missing-signal and expansion behaviors, confirming that the evidence contract is not merely rhetorical
- the intentionally weak cases now cover not only missing temporal anchors, but also overview-only temporal evidence, missing relation paths, overview-only relation evidence, missing factual grounding, graph-only factual hints, and missing image-grounded evidence
- while expanding this subset, we also uncovered and fixed a real code-path issue: overview text that merely mentioned “OCR” or “screenshot” could previously be over-counted as visual evidence, which would overestimate visual readiness

### 6.12 Consolidated main result

Taken together, the current mechanism-level results support a six-row interpretation:

- **Tree-only** is strongest on temporal-only questions but fails badly on relation-heavy and readiness-sensitive settings.
- **Graph-only** is strongest on relation-heavy questions, and stronger than tree-only on mixed settings, but still cannot unify all query families by itself.
- **Dual-backbone** produces the most balanced temporal / relation / mixed behavior, but still inherits answer-too-early and under-checked evidence failures.
- **Readiness-gated full** combines dual-backbone routing with explicit answerability constraints, giving a stronger correctness story.
- **Self-check-enabled full** adds the final answer-time policy layer: answer if evidence shape is sufficient, expand if it is not, and abstain if it remains weak.
- **Real-code family subset evidence** shows that the same policy signal is no longer confined to toy code and can already be observed in the actual retrieval loop.

Across all six lines, the pattern is consistent:

- temporal questions prefer temporal tree
- relation-heavy questions prefer graph
- dual-backbone provides the most balanced retrieval story
- readiness gating is required to turn stored evidence into answer-time correctness
- retrieval self-check is required to turn strong retrieval into more reliable answer policy
- confidence and sufficiency should not be conflated
- second-pass retrieval is strongest when it is aligned with the missing evidence family

### 6.13 Coverage-aware gating ablation

The previous self-check and second-pass experiments establish that answer-time policy matters after retrieval. A closely related systems question is whether the stack should let a high-confidence primary hit terminate search early, or whether it should still inspect whether the planned evidence contract is complete. This is a narrower question than end-task accuracy, but it is highly relevant to the current EchoMemory refactor because the real `RetrievalGatingPolicy` now exposes explicit `coverage_gap=...` outcomes rather than relying only on confidence.

We therefore ran a six-case nano ablation comparing:

- **confidence-only gating**: stop when the primary backbone already produces a sufficiently high score
- **coverage-aware gating**: even with a strong primary score, continue if the planned evidence contract is incomplete

The results are intentionally modest and should be read that way:

- keyword-level relevance stayed the same: **6 / 6** vs **6 / 6**
- contract-complete cases improved from **1 / 6** to **2 / 6**
- the clearest improvement was **temporal_join_date**, where confidence-only stopping preserved the surface date but stopped with only tree evidence, while coverage-aware gating added supporting event evidence and completed the contract

This ablation matters for two reasons. First, it supports the narrower systems claim that confidence and evidence sufficiency should not be treated as interchangeable. Second, it exposes where the current design is still weak. In particular, several visual cases still fail the full contract because image-evidence retrieval is not yet surfaced strongly enough in the returned evidence set, even though surface answer relevance remains correct. This is useful negative evidence rather than a failure of the experiment: it shows that the contract can make unresolved retrieval weaknesses visible instead of hiding them behind superficially plausible answers.

### 6.14 Type-aware second-pass ablation

The next question is whether "do another pass" is itself the right abstraction. Our recent real-code refactor suggests a stronger claim: second-pass retrieval should not be hard-coded as graph-only expansion. Instead, it should inspect *which* evidence types remain missing and then expand the corresponding supporting reader. This is the motivation behind the new type-aware second-pass path in the current `SearchService`, where missing `episode`, `fact/event`, and `graph/entity` evidence now route to different supplementary retrievers.

We modeled this idea in a five-case nano ablation that compares:

- **one pass**
- **graph-only second pass**
- **type-aware second pass**

For this experiment we also strengthen the temporal-relational contract so that ordered relation questions require not only graph/event support but also explicit chronology support (`temporal_tree`). This is important because many realistic plan-after-event questions are underspecified if they only expose relation-path evidence without any chronology anchor.

The results are clearer than in the previous gating ablation:

- one pass: **1 / 5** contract-complete
- graph-only second pass: **3 / 5**
- type-aware second pass: **5 / 5**

The improvement pattern is also interpretable:

- **graph-only over one-pass** fixes tree-first temporal questions such as `temporal_join_date` and `temporal_leave_date`, because adding graph/event support is exactly what those questions were missing
- **type-aware over graph-only** fixes ordered graph-primary questions such as `temporal_relational_plan` and `temporal_relational_after`, because their missing signal is not more graph evidence but chronology support, so the correct supplement is the temporal tree rather than another graph probe

This is the strongest mechanism-level evidence so far for the contract-driven reading of EchoMemory-MM. It suggests that the real design question is not simply whether to expand retrieval, but whether the expansion policy is aligned with the missing evidence family. In other words, a second pass becomes meaningfully smarter only when it is *type-aware*.

### 6.15 Paper-facing experiment inventory and benchmark status

As the package grew, another issue became impossible to ignore: not every result line plays the same role. Some lines are complete mechanism evidence, some are complete but only as reference baselines, and some are only partially complete because the protocol is frozen while the final QA/judge outputs are still missing. To avoid overstating the state of the evidence, we assembled a paper-facing experiment inventory that classifies each line by role and completion state.

### 6.16 Query-family paraphrase robustness

One remaining concern after the earlier nano suite was whether some improvements were still too close to benchmark phrasing. To probe this directly, we added a small **query-family paraphrase robustness benchmark** on the `nano_reference_impl_v14` line. The benchmark keeps the same underlying memory and expected facts fixed, but rewrites each query family with multiple surface forms:

- temporal: “when”, “on what date”, “what date marks the start”
- relational: “who helped”, “through whom”, “who assisted”
- longitudinal: “how did X evolve”, “what changed over time”, “give the timeline of updates”
- visual: “what was shown”, “what appears in the image”, “which address is visible”
- readiness: “can you answer now”, “is the memory ready”, “can the system answer at this point”

The goal is intentionally narrow. This is not a benchmark-accuracy claim. It is a structural check on whether query-family routing still works when the wording shifts.

The result is useful for the paper’s generalization claim:

- baseline reference cues: **8 / 15** answer-correct, **13 / 15** family-correct
- improved generic family cues: **15 / 15** answer-correct, **15 / 15** family-correct

The failure mode in the baseline is revealing: most misses are not “memory missing” failures. Instead, the system often routes a paraphrase into the wrong family, such as treating “What changed over time...” as temporal instead of longitudinal, or “Can the system answer at this point?” as general instead of readiness. The improved version fixes this without adding any benchmark-specific entity list or dataset keyword table. In other words, the gain comes from **more generic query-family routing and answer shaping**, not from task-specific memorization.

The current inventory now separates seven lines:

- **Nano mechanism suite**: complete; supports the core architectural claims
- **Unified Memory-OS dual-backbone nano**: complete; supports the integrated method narrative around readiness, lifecycle, dual backbones, and contract-aware second pass
- **21-case real-code family subset**: complete; supports the bridge from toy code to the actual `SearchService`
- **Multimodal real smoke**: complete; appendix-grade feasibility evidence for image-grounded retrieval
- **Planner gap probe**: complete; diagnostic only
- **LoCoMo conv-30 formal subset-20**: protocol frozen but still partial, because QA/judge artifacts are not yet complete
- **LongMemEval reference baseline**: complete, but explicitly a reference baseline rather than an EchoMemory-MM main result

This distinction matters for claim discipline. It lets the paper say, very plainly, which lines can already support method-level tables and which lines still belong in the “future benchmark completion” bucket. In other words, it upgrades the evidence package from “many files exist” to “the role of each evidence line is explicit and auditable.”

The same discipline is now reflected in two new packaging artifacts. First, a canonical 30-paper bibliography/action map compresses the literature into six implementation axes: time, hierarchy, graph, lifecycle, policy, and multimodality. Second, an objective-audit page separates what is already complete at the structure, nano, and real-code levels from what is still incomplete at the benchmark-table level. These additions do not create new method evidence by themselves, but they materially improve the paper's auditability and reduce the risk of overstating progress.

### 6.16 Family-level benchmark panel

By this point the package had enough small experiments that another raw ablation page would have made the evidence harder to read rather than easier. We therefore assembled a **family-level benchmark panel** that treats the current nano suite as one paper-facing experiment board instead of many disconnected notes.

The panel groups the evidence into five families:

- **temporal semantics**
- **relational grounding**
- **longitudinal topic evolution**
- **visual / multimodal grounding**
- **readiness and answer-time policy**

Its role is not to introduce new numbers. Instead, it clarifies which structural claims already have repeated support across experiments:

- three-clock time is necessary for true event-date questions
- dual-backbone routing is more stable than single-backbone routing across mixed families
- graph path grounding matters for relation-heavy questions
- topic dossiers help on cross-session progress and status questions
- readiness is a correctness gate rather than a logging field
- confidence-only stopping is weaker than coverage-aware stopping
- second-pass retrieval is materially stronger when it is aligned with the missing evidence family

This panel is useful paper-side because it turns a growing set of generic nanos into a single family-structured reading of the method. In other words, it lets the paper argue from *behavior classes* rather than from a pile of unrelated small wins.

### 6.17 Code-grounded 30-paper appendix

We also added a **code-grounded 30-paper appendix**. The point of this appendix is not to inflate the related-work section. Its real purpose is to make the literature traceable at implementation level:

- which papers motivate the **time** line
- which motivate the **hierarchy / middle-layer** line
- which motivate the **graph / path-grounding** line
- which motivate the **lifecycle / readiness** line
- which motivate the **policy / second-pass** line
- which motivate the **multimodal** line

More importantly, the appendix marks each idea as one of:

- **already reflected in current code**
- **partially reflected**
- **still missing**

This improves claim discipline in two ways. First, it prevents the paper from sounding as if every cited idea has already been fully implemented. Second, it turns the literature map into an engineering roadmap: future contributors can see exactly which papers already changed the code path and which ones still only exist as design pressure.

## 7. Discussion

The present evidence justifies a mechanism-level claim, not a benchmark-scale claim. Specifically, it justifies the claim that long-horizon memory should not be flattened into one retrieval assumption or one unconditional answer policy.

It does **not** yet justify:

- benchmark-scale SOTA claims on LoCoMo or LongMemEval
- mature multimodal benchmark claims
- production-grade latency or cost conclusions

This boundary is a strength rather than a weakness. It lets the current package function as a credible bridge between architecture design and benchmark-scale validation.

Readiness is especially important here. Many memory systems discuss write durability and retrieval quality, but far fewer make answerability state explicit. Our readiness ablation suggests that this distinction is not cosmetic. It changes correctness.

The self-check result adds a second systems lesson. Even after good retrieval, the system still needs an explicit answer-time policy. A planner-routed memory architecture without any retrieval self-check is stronger than a flat retrieval pool, but it can still answer from structurally weak evidence. This is exactly the kind of gap that benchmark-level error analysis often reveals but architecture diagrams often hide.

The new 21-case real-code subset strengthens this point further. It shows that self-check is not only a paper-side abstraction: it now changes evidence composition and visible review decisions in the actual `SearchService` across multiple query families. In particular, the relational graph-only versus relational graph-plus-atom contrast makes explicit that graph connectivity and fact grounding should be evaluated separately.

The broader lesson is that memory architecture should be evaluated as a systems problem rather than only as a retrieval recipe. EchoMemory-MM is promising precisely because it treats temporal routing, graph routing, topic-centered middle-layer retrieval, readiness, contract-aware gating, and type-aware answer-time expansion as interacting constraints rather than as separate add-ons.

The new experiment inventory adds a more practical lesson: a serious paper package needs to distinguish between **mechanism evidence**, **integrated method evidence**, **real-code bridge evidence**, **reference baselines**, and **partially frozen benchmark runs**. Without this separation, it becomes too easy to mix protocol readiness with actual result readiness, or to present a baseline run as if it were the method’s own main score. The inventory therefore functions as part of the methodology, not merely as bookkeeping.

The broader packaging lesson is similar: a serious systems paper benefits from a stable canonical bibliography and an explicit objective audit. These artifacts make it easier for future contributors or reviewers to see which claims are already code-backed, which are only mechanism-backed, and which still require benchmark closure.

We also added a small systems-side cost profile around the unified Memory-OS nano. This is intentionally modest evidence: it does not estimate production latency, but it does show that contract-aware second-pass retrieval raises cost in a structured way rather than exploding it. In the current generic micro-benchmark, the contract-aware path increases average reader count only from **1.0** to **1.5** and raises average latency from roughly **85.7 us** to **117.0 us** relative to dual-backbone-only retrieval, while improving contract-complete cases from **3 / 6** to **4 / 6**. This is useful precisely because it is not a heroic claim. It gives the paper one systems-side anchor for discussing the cost of evidence completeness without pretending that the current package already includes deployment-grade latency tables.

## 8. Limitations

The current work has seven major limitations:

1. experiments remain small-scale and mechanism-oriented
2. the new topic-dossier middle layer is integrated but still minimal
3. multimodal evidence paths are early-stage
4. latency and cost are not yet formalized
5. runtime integration across planes remains incomplete
6. contract-aware gating and type-aware second pass are currently demonstrated mainly through nano experiments plus focused real-code behavior, not full benchmark tables
7. benchmark-scale LoCoMo / LongMemEval evidence is still future work
8. multimodal retrieval remains structurally promising but empirically thin compared with the text-only and temporal-relational evidence lines

## 9. Conclusion

EchoMemory-MM advances a simple but under-emphasized thesis:

> temporal questions, relation-heavy questions, memory-readiness constraints, and answer-time evidence policy should not be collapsed into one flat retrieval assumption.

The current EchoMemory repository already contains the major structural planes needed for this direction, and the current nano experiments plus the 21-case real-code family subset support the same architectural conclusion from multiple angles. The newest addition strengthens that story: a lightweight topic-dossier middle layer now exists both as a generic nano ablation and as a minimal main-code integration, closing a previously visible gap between broad overview memory and flat local evidence. In particular, the newer coverage-aware gating and type-aware second-pass ablations clarify that the real policy problem is not just whether to expand retrieval, but whether the system expands in a way that matches the missing evidence family. The new paper-facing experiment inventory, canonical bibliography/action map, objective-audit packaging, and topic-dossier integration strengthen this package further by making both the role and the current completion state of each evidence line explicit. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.

## 10. Artifact Pointers

- 30-paper map: `docs/echomemory_mm_30paper_map_and_nano_benchmark_20260613.md`
- full draft: `docs/echomemory_mm_cvpr_submission_draft_v13_20260616.md`
- condensed draft: `docs/echomemory_mm_cvpr_condensed_submission_draft_v3_20260615.md`
- figure pack: `web/static/generated-reports/echomemory_mm_figure_pack_20260615.html`
- generalized nano method prototype: `web/static/generated-reports/echomemory_nano_generalizable_stream_graph_contract_20260615.html`
- unified Memory-OS dual-backbone nano: `web/static/generated-reports/echomemory_nano_memory_os_dual_backbone_20260615.html`
- unified Memory-OS cost profile: `web/static/generated-reports/echomemory_nano_memory_os_cost_profile_20260615.html`
- unified Memory-OS bridge note: `web/static/generated-reports/echomemory_nano_memory_os_bridge_20260615.html`
- generalized nano appendix: `web/static/generated-reports/echomemory_generalized_nano_appendix_20260615.html`
- generalized nano main-code bridge: `web/static/generated-reports/echomemory_generalized_nano_maincode_bridge_20260615.html`
- three-clock nano report: `web/static/generated-reports/echomemory_nano_three_clock_temporal_ablation_20260615.html`
- 30-paper code matrix: `web/static/generated-reports/echomem_30paper_code_matrix_and_threeclock_20260615.html`
- canonical 30-paper bibliography: `web/static/generated-reports/echomemory_30paper_canonical_bibliography_20260615.html`
- research evidence board: `web/static/generated-reports/echomemory_research_evidence_board_20260615.html`
- paper experiment inventory: `web/static/generated-reports/echomemory_paper_experiment_inventory_20260615.html`
- objective audit: `web/static/generated-reports/echomemory_structure_30paper_objective_audit_20260615.html`
- submission package home: `web/static/generated-reports/echomemory_mm_submission_package_home_20260615.html`
