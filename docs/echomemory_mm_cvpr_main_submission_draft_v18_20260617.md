# EchoMemory-MM CVPR Main Submission Draft v18

Date: 2026-06-17

## Title

**EchoMemory-MM: Contract-Driven Dual-Backbone Multimodal Temporal Graph Memory with Topic-Centered Middle-Layer Retrieval**

## Abstract

Long-horizon agents must answer chronology-heavy, relation-heavy, longitudinal, visually grounded, and readiness-sensitive questions over evolving interaction streams. Existing memory systems often flatten these pressures into a single retrieval pool, forcing all queries through the same evidence path and answer policy. We propose **EchoMemory-MM**, a contract-driven memory architecture that organizes append-only interaction streams into atomic memories, a topic-centered middle layer, a temporal abstraction tree, a relation graph, and a readiness plane. EchoMemory-MM routes chronology-heavy questions to a temporal backbone, relation-heavy and visual questions to a graph backbone, and cross-session progress questions to a topic-dossier layer. It further uses a shared evidence contract to govern retrieval gating, answer-time self-check, type-aware second-pass expansion, and family-aware readiness checks. We ground the design in the current EchoMemory codebase and support it with a code-backed evidence package: three-clock temporal ablations, topic-dossier ablations, no-hint topic-canonicalization and dual ablations, dossier-level paraphrase stress, dual-backbone ablations, a relational path-grounding ablation, a typed path-constraint ablation, readiness and answerability benchmarks, a family-aware readiness ablation, a self-check executor ablation, a unified reference nano implementation, a v17 core structural ablation, paraphrase robustness tests, a nano visual-ingest bridge ablation, and a 21-case real-code family subset over the current `SearchService`. Across these lines, the same pattern recurs: time fidelity depends on explicit time semantics, longitudinal questions benefit from a middle layer, relation-heavy and visual questions prefer graph-structured recall, persisted memory is weaker than answerable memory, and generic query-family routing is more robust than benchmark-specific trigger rules. The new unified reference nano and v17 core ablation make this claim more concrete by showing that two seemingly modest design choices — preserving event time instead of collapsing to write time, and keeping a topic-centered middle layer instead of falling back to flat atoms — already explain a large fraction of the observed gains. The relational path-grounding ablation further sharpens the graph story by showing that a graph hit plus a fact hit is still weaker than an explicit path trace for relation-heavy questions. The typed path-constraint ablation then shows the next failure mode: even when a graph can return a path, not every high-salience shared neighbor is a semantically licensed bridge, so relation-heavy recall benefits from constraining both edge types and bridge-node types. The family-aware readiness ablation closes the systems loop by showing that readiness should not be a single global unlock: different query families require different memory planes to be consolidated before they become answerable. The new self-check executor ablation sharpens the answer-time-policy story further: a self-check that only diagnoses missing evidence is materially weaker than one that can expand supporting readers, abstain, or block on readiness. The visual-ingest bridge further shows that query-time multimodal routing is not enough: visual memory quality depends on whether image resources are structurally converted into searchable image evidence with OCR, owner links, and event/fact grounding. While the current package does not establish benchmark-scale superiority, it supports a narrower systems claim: long-horizon memory should be modeled as a planner-routed, stream-to-structure, answerability-aware system rather than as a flat unified retrieval pool.

## 1. Introduction

Long-horizon memory failures in personal agents are often described as retrieval failures. This is only partly true. In practice, at least five distinct failure modes recur:

1. event occurrence time is replaced by write time
2. relation-heavy questions are answered from broad summaries rather than structured evidence
3. visually grounded questions are answered without first-class image evidence
4. freshly persisted memory is treated as answerable before consolidation finishes
5. retrieval stops early because confidence looks high even when evidence families remain incomplete

These are not the same problem. A question such as “What happened yesterday?” should not follow the same primary evidence path as “Who introduced Jon to Lena?”, and neither should be handled in the same way as “Can the system answer now?” immediately after a write. Even when retrieval is strong, there remains an answer-time policy question: should the system answer, expand supporting evidence, or abstain?

The current EchoMemory repository already contains most of the structural substrate needed for a stronger decomposition: append-only session storage, atom-first extraction, organized memory projection, graph synchronization, temporal-tree projection, and planner-like retrieval logic. The opportunity is therefore not to invent a new stack from scratch, but to turn these existing planes into a more explicit memory architecture and evaluate that architecture at the level of query families and answerability rather than as a single pooled retrieval score.

We study the following thesis:

> Long-horizon memory should be modeled as a contract-driven, dual-backbone, readiness-aware, answer-time-governed stream-to-structure system.

This thesis yields five design commitments:

- chronology-heavy and relation-heavy questions should not share the same primary backbone
- persisted memory should not be treated as automatically QA-ready
- event occurrence time should not be silently replaced by write time
- evidence sufficiency should be explicit rather than implicit
- supporting retrieval should depend on the missing evidence family

Our contribution is deliberately scoped. We do not claim benchmark-scale superiority on LoCoMo or LongMemEval. Instead, we contribute:

- a code-backed analysis of the current EchoMemory structure
- a 30-paper positioning map centered on 2024-2026 work
- a canonical nano implementation family for explanation and ablation
- a set of generic mechanism benchmarks designed to avoid dataset-specific keyword hacks
- a unified reference nano implementation that exposes the full method skeleton in a single compact file
- a v17 core ablation that isolates the contribution of three-clock time and the topic-dossier middle layer
- a nano visual-ingest bridge showing why write-side multimodal structuring matters beyond query-time routing
- a focused real-code bridge showing the same policy signals inside the current `SearchService`

Together, these support a narrower but stronger claim: flattening all long-horizon memory demands into one retrieval assumption is an architectural mistake, and strong retrieval alone remains insufficient without explicit time semantics, readiness control, and answer-time policy.

## 2. Related Work

### 2.1 Benchmark pressure

Benchmarks such as LoCoMo and LongMemEval show that long-horizon memory is not one scalar capability. Temporal, relational, update-sensitive, and lifecycle-sensitive failures should be evaluated separately. This motivates our family-level framing.

### 2.2 Hierarchical and temporal retrieval

RAPTOR, MemoRAG, ByteRover, TiMem, and related work show that long-horizon recall improves when memory is exposed through structured abstraction rather than a flat retrieval pool. This directly motivates the temporal-tree route and the topic-centered middle layer.

### 2.3 Graph and structured recall

HippoRAG, GraphReader, Zep, LEGO-GraphRAG, H-Mem, and related systems suggest that relation-heavy or event-linked questions should be grounded in graph-structured recall rather than in summary-only evidence.

### 2.4 Lifecycle and systems

Mem0, LightMem, MemOS, Infini Memory, AgentIR, and ConvMemory emphasize that memory is not only retrieval. It is also a lifecycle problem involving consolidation delay, routing policy, and evidence maintenance. This directly motivates the readiness plane.

### 2.5 Policy and multimodal direction

MIRIX, Mem-T, E-mem, D-MEM, Field-Theoretic Memory, and Self-RAG point toward two further themes: memory actions should be policy-aware, and multimodal observations should become first-class memory. EchoMemory-MM sits at this intersection. In particular, the multimodal thread suggests that visual understanding should not be delayed until answer time; image resources need a write-side path that converts screenshots, OCR spans, owners, and linked events into stable memory objects.

## 3. Problem Formulation

We distinguish four notions of time:

- **write time**: when a message is durably persisted
- **story time**: when the described event actually occurred
- **mention time**: when an earlier event is revisited inside the interaction stream
- **query time**: the anchor against which relative expressions such as “yesterday” should be resolved

We distinguish five query families:

1. **temporal**: date lookup, relative-time, chronology, ordering
2. **relational**: spouse, introducer, helper, multi-hop linkage
3. **longitudinal**: progress, latest status, cross-session topic evolution
4. **visual**: screenshot / OCR / photo-grounded evidence
5. **readiness-sensitive**: whether the system should answer now

We also define a readiness lifecycle:

- persisted
- atoms ready
- graph ready
- organized ready
- tree ready
- QA ready

The design objective is to avoid collapsing these pressures into a single retrieval route and a single unconditional answer policy.

## 4. Method

### 4.1 Memory planes

EchoMemory-MM uses six planes:

1. **Session stream**
2. **Atomic plane**
3. **Topic dossier plane**
4. **Temporal tree**
5. **Relation graph**
6. **Readiness plane**

### 4.2 Three-clock temporal semantics

Query-time anchoring is necessary but not sufficient. A memory system can still fail temporal questions if it stores only one timestamp and silently reuses write time as event time. EchoMemory-MM therefore separates `story_time`, `mention_time`, and `write_time`.

### 4.3 Dual-backbone routing with a middle-layer route

EchoMemory-MM defines:

- **temporal tree** for chronology-heavy queries
- **relation graph** for relation-heavy and visually grounded queries
- **topic dossier** for longitudinal progress and cross-session evolution queries

The non-primary route remains useful as supporting evidence, but the system should not force all question types through the same evidence path.

### 4.4 Shared evidence contract

Dual-backbone routing alone is not enough if different parts of the stack disagree about what counts as sufficient evidence. We therefore define a lightweight **shared evidence contract** that maps each query family to required evidence families.

Typical examples are:

- temporal: `temporal_tree + event + event_time`
- relational: `graph + fact + path_grounding`
- longitudinal: `topic_dossier + fact`
- visual: `image_evidence + fact`

The contract is intentionally generic. It does not depend on dataset-specific entity lists or benchmark-specific keyword tables. Instead, it asks whether the retrieved evidence actually supports the requested family, answer type, and structure.

### 4.5 Readiness-aware answerability

Persisted memory is weaker than answerable memory. A system should not answer simply because a write completed. It should answer only when the relevant memory planes are ready enough for faithful retrieval. We further distinguish **contract-complete** from **answerable**: a result set may satisfy the planned evidence families while still failing to support the requested answer type.

### 4.6 Coverage-aware gating and type-aware second pass

The evidence contract is useful twice:

- before answer generation, to prevent confidence-only early stopping
- after primary retrieval, to decide which supporting reader should be expanded

Instead of issuing a fixed graph-only retry, EchoMemory-MM performs a **type-aware second pass** aligned with the missing evidence family. After retrieval, an answerability gate checks whether the candidate answer is actually supported by the evidence shape implied by the query family.

### 4.7 Write-side multimodal structuring

Visual queries benefit from graph-backed retrieval only if image resources have already been turned into first-class memory objects. We therefore distinguish three write-side multimodal regimes:

- **no visual ingest**: images are stored externally but produce no searchable memory objects
- **surface visual ingest**: images become searchable nodes, but only with shallow caption text
- **structured visual ingest**: images produce searchable `image_evidence` with OCR spans, owner/entity links, and event/fact graph edges

This distinction is crucial because query-time multimodal routing can appear correct even while the write-side memory graph remains semantically thin.

## 5. Code Anchors

The current EchoMemory repository already contains most of the required substrate:

- session lifecycle and append-only storage: `index_engine/session_service.py`
- atom-first extraction: `workers/atom_first_pipeline.py`
- organized projection and topic dossier support: `workers/organized_projector/projector.py`
- graph synchronization and image evidence: `index_engine/graph/sync.py`
- query routing and retrieval: `index_engine/search_service.py`
- planner logic: `index_engine/planner/query_planner.py`
- graph seeding: `index_engine/planner/graph_seed_planner.py`
- evidence contract: `index_engine/policy/evidence_contract.py`
- retrieval gating: `index_engine/policy/retrieval_gating.py`
- answer-time self-check: `index_engine/policy/self_check.py`

The key engineering gap is no longer missing structure, but stronger runtime integration between these planes.

## 6. Experiments

### 6.1 Unified findings at a glance

| Design pressure | Best-supported structural response | Representative result |
| --- | --- | --- |
| Time is not one timestamp | Preserve `story_time`, `mention_time`, and `write_time`; route chronology-heavy queries to a temporal tree | `0/4 -> 4/4 -> 4/4` |
| Longitudinal questions need a middle layer | Add a topic-centered dossier layer between global overview and flat atoms | `1/5 -> 3/5 -> 4/5`; `0/4 -> 4/4`; `5/16 -> 14/16 -> 16/16` |
| One primary backbone is not enough | Use temporal tree for chronology-heavy queries and graph for relation-heavy / visual queries | temporal `3/3`, relation `3/3`, dual `8/12` |
| Relation-heavy questions need path grounding | Require explicit graph path evidence, not just graph hits plus facts | `graph+fact` reaches contract-complete too early; `graph+fact+path_grounding` forces an explicit relation trace |
| Relation-heavy questions need typed bridges | Constrain both bridge-node types and edge schemas for role-specific relation queries | naive shared-neighbor `0/3`; typed path constraint `3/3` |
| Readiness should be family-aware | Unlock QA according to the memory planes each query family depends on | strict `3/4`; core-global `3/4`; family-aware `4/4` |
| Retrieval success is weaker than answerability | Add readiness checks, evidence contracts, and answer-time self-check | `1/5 -> 5/5`; `4/8 -> 8/8`; `1/7 -> 1/7 -> 7/7`; `2/6 -> 6/6` |
| Generalization should come from query-family routing, not benchmark cues | Keep routing generic and let missing evidence types drive second pass | `8/15 -> 15/15`; contract `1/5 -> 5/5` |

Taken together, these lines support a single systems claim: **long-horizon memory works better when the stack is treated as a planner-routed, stream-to-structure system with typed middle layers and answer-time control, rather than as a flat retrieval pool**.

### 6.2 Main evidence lines

| Evidence line | Compared variants | Main result | Why it matters |
| --- | --- | --- | --- |
| Three-clock temporal-semantics ablation | write-time only / event+mention / three-clock | `0/4` / `4/4` / `4/4` | Temporal correctness depends on preserving event time, not only on routing. |
| Interval temporal-arbitration ablation | write-time only / three-clock without arbitration / temporal arbitration | `0/3` / `1/3` / `3/3` | Three clocks are necessary but still incomplete; interval and order-sensitive questions need an explicit time-axis arbitration step. |
| Generalized method-prototype nano | flat text / primary only / contract-aware | `4/6` / `2/6` / `6/6` | Wrong evidence shape is a stronger failure signal than missing keywords. |
| Anchored temporal ablation | tree-only / graph-only / dual | `3/3` / `2/3` / `3/3` | Relative-time questions prefer chronology-aware routing. |
| Relation-backbone ablation | tree-only / graph-only / dual | `0/3` / `3/3` / `3/3` | Relation-heavy questions prefer graph grounding. |
| Relational path-grounding ablation | graph+fact / graph+fact+path_grounding | graph+fact stops early; path-grounded mode expands one more reader | Relation-heavy answers should prefer explicit path traces over ungrounded graph seeds. |
| Typed path-constraint ablation | naive shared-neighbor / typed path constraint | `0/3` / `3/3` | Shared-neighbor expansion confuses co-occurrence with semantically licensed bridges unless path types are constrained. |
| Family-aware readiness ablation | strict / core-global / family-aware | `3/4` / `3/4` / `4/4` | Readiness should depend on the memory planes required by the current query family, not only on a single global QA gate. |
| Self-check executor ablation | primary-direct / advisory-only / executive-policy | `1/7` / `1/7` / `7/7` | It is not enough for self-check to diagnose weak evidence; it must be allowed to expand, abstain, or block on readiness. |
| Topic-dossier ablation | overview-only / atom-only / topic-dossier | `1/5` / `3/5` / `4/5` | Longitudinal queries need a middle layer between overview and flat atoms. |
| Generic topic-induction benchmark | topic-hint / no topic-hint | `5/5` / `5/5` | Generic topic induction can preserve task behavior without topic-specific hinting. |
| No-hint topic-canonicalization ablation | explicit hint / naive no hint / canonicalized no hint | `4/4` / `0/4` / `4/4` | The middle layer needs generic canonicalization when the same topic appears under multiple surface forms. |
| Topic-dossier dual ablation | naive+lexical / naive+longitudinal / canonicalized+lexical / canonicalized+longitudinal | `0/4` / `0/4` / `3/4` / `4/4` | Better selection cannot rescue fragmented dossiers; grouping must come first. |
| Topic-dossier paraphrase stress | naive+lexical / canonicalized+lexical / canonicalized+longitudinal | `5/16` / `14/16` / `16/16` | Middle-layer gains survive wording changes rather than depending on benchmark cues. |
| Unified reference nano v17 | unified stream / atoms / dossier / temporal / graph / readiness / answerability | `6/6` demo cases | The full architectural story remains understandable and executable without benchmark-specific triggers. |
| Unified structure ablation | legacy_v17 / typed_answerability_gate / write_time_collapse / no_topic_dossier / no_answerability_gate | `5/6` / `6/6` / `3/6` / `3/6` / `5/6` | Time semantics and the topic-centered middle layer explain large structural gains; answerability improves when it is family-aware and typed rather than merely stricter. |
| v17 core structural ablation | full_v17 / write_time_only / atom_only_no_dossier | `4/4` / `2/4` / `2/4` | Two generic design choices explain much of the gain: preserve story time, and keep a topic-centered middle layer. |
| Dual-backbone benchmark | tree-only / graph-only / dual | `3/12` / `5/12` / `8/12` | Tree and graph cover different failure modes; routing beats either alone. |
| Readiness ablation | baseline / temporal_graph / full | `1/5` / `4/5` / `5/5` | Persistence is weaker than answerability. |
| Self-check v2 | dual-backbone baseline / self-check | `4/8` / `8/8` | Retrieval still needs answer-time policy. |
| Coverage-aware gating | confidence-only / coverage-aware | contract `1/6 -> 2/6` | Confidence should not substitute for evidence sufficiency. |
| Type-aware second pass | one-pass / graph-only / type-aware | contract `1/5 -> 3/5 -> 5/5` | Supporting retrieval works best when aligned with missing evidence families. |
| Multimodal contract ablation | one-pass / contract-aware | contract `2/5 -> 5/5` | Visual queries can look relevant while still lacking structural grounding. |
| Visual-ingest bridge | no visual ingest / surface ingest / structured ingest | `0/6 -> 1/6 -> 6/6` | Query-time multimodal routing is insufficient unless image resources are structurally converted into OCR- and link-bearing memory objects. |
| Answerability-gate benchmark | legacy answerability / enforced gate | `2/6 -> 6/6` | Contract completeness does not guarantee answerability. |
| Query-family paraphrase robustness | baseline cues / improved generic cues | `8/15 -> 15/15`; family `13/15 -> 15/15` | Generalization should come from generic family routing rather than dataset-specific trigger words. |
| Real-code family subset | current `SearchService` | `21/21` family expectation pass | The same policy signals are visible in the real stack, not only in toy code. |

### 6.3 What the current evidence proves

The current package supports twelve conclusions.

First, **temporal correctness is partly a schema problem before it is a routing problem**. The v17 core ablation makes this especially visible: simply collapsing `story_time` to `write_time` drops the compact test panel from `4/4` to `2/4`, even though the planner and graph paths remain available.

Second, **three clocks are necessary but not sufficient: chronology-heavy questions also need explicit temporal arbitration when they ask about order, intervals, or retrospective mentions**. The new interval temporal-arbitration ablation makes this visible in a compact form. A write-time-only variant fails all three cases (`0/3`), and a three-clock variant that still lacks explicit interval or before/after reasoning reaches only `1/3`. Only the temporal-arbitration variant reaches `3/3`, because it explicitly decides whether the question is asking for event time rather than mention time, the latest event before an anchor, or the event strictly between two anchors. This sharpens the time claim: preserving clocks is the substrate, but arbitration over those clocks is the answering policy.

Third, **chronology-heavy, relation-heavy, longitudinal, and visual questions do not want the same primary reader**.

Fourth, **a topic-centered middle layer is useful for cross-session progress and status questions, and its benefit can be preserved without benchmark-specific topic hints**. In the same v17 core ablation, removing the dossier layer drops the panel from `4/4` to `2/4`, with the losses concentrated exactly where the method predicts: longitudinal and latest-state questions.

Fifth, **retrieval success is weaker than answerability**.

Sixth, **confidence should not substitute for evidence sufficiency**.

The new unified structure ablation sharpens these first five points by putting them inside one shared memory rather than across separate task-specific demos. Starting from `legacy_v17` at `5/6`, simply collapsing event time into write time drops performance to `3/6`, and removing the topic dossier also drops to `3/6`. This matters because it shows that the biggest gains are not late prompt tricks; they already appear when the memory substrate preserves the right temporal semantics and exposes a stable middle layer for longitudinal recall. The same ablation also clarifies the answerability story: a naive removal of the gate stays at `5/6`, while a typed, family-aware gate reaches `6/6`. In other words, answerability policy helps most when it is aligned with query family and answer type, not when it is merely stricter.

Seventh, **paraphrase robustness is stronger when it comes from generic family routing, generic canonicalization, and longitudinal dossier selection rather than from query-surface cues**.

Eighth, **relation-heavy answers benefit from explicit path grounding rather than from graph seeds and fact support alone**.

Ninth, **relation-heavy answers also benefit from typed path constraints, because co-occurrence bridges and role-specific bridges are not interchangeable**.

Tenth, **readiness should be family-aware: global core readiness is too coarse for queries that depend on slower organized or episodic planes**.

Eleventh, **multimodal memory quality depends on write-time structuring, not only on query-time routing**.

Twelfth, **answer-time self-check needs execution authority, not only diagnostics**. The self-check executor ablation makes this precise: `advisory_only` detects six weak-evidence cases but remains at `1/7`, whereas `executive_policy` reaches `7/7` by re-reading targeted layers, abstaining when path grounding is still absent, and blocking prematurely answerable cases behind readiness.

### 6.4 What the current evidence does not prove

The current package does **not** prove:

- benchmark-scale superiority on LoCoMo
- benchmark-scale superiority on LongMemEval
- production-ready multimodal performance
- deployment-grade latency or cost advantages

This claim boundary is intentional.

## 7. Real-Code Bridge

The nano evidence establishes mechanism plausibility, but it does not by itself prove that the same mechanism survives contact with the current EchoMemory retrieval stack. We therefore ran a focused 21-case family subset directly on the current `SearchService`.

Its value is not benchmark accuracy estimation. Its value is that the family-level design pressures argued in the paper become visible in the actual retrieval path.

The current subset passes `21/21` against family-level expectations. This does not justify benchmark-scale superiority claims, but it materially strengthens the systems claim that the policy ideas are already observable in the real stack.

For multimodal memory, the bridge is asymmetric: the current main code already exposes a structured query-time path through planner routing, evidence contracts, graph-backed `image_evidence`, and answer-time self-check, but the write-side visual understanding path remains thinner. This is precisely why the nano visual-ingest bridge matters: it isolates the missing write-side mechanism without relying on benchmark-specific cues.

The new unified nano and core ablation also strengthen the bridge from explanation to implementation. Earlier nanos already supported individual claims, but the v17 reference implementation compresses the full method into a single coherent path: stream, atoms, topic dossier, temporal blocks, graph-backed image evidence, readiness gating, contract-aware second pass, and answer finalization. The paired core ablation then removes only one structural commitment at a time. This makes the paper easier to audit: it is clearer which gains come from temporal semantics, which from the middle layer, and which from later policy additions.

There is also a narrower engineering lesson from the nano line: **answerability needs a stable runtime seam**. In the earlier v14 reference implementation, answerability is exposed through an explicit `_answerability_ok(...)` helper that is called near the end of `_answer(...)`. This makes it easy to ablate, strengthen, or replace the gate without entangling it with the rest of answer synthesis. In the cleaner v17 reference, the overall stack is easier to read, but some answer-family behavior is folded directly into `_answer(...)`, and the reusable answerability seam is correspondingly weaker. This is not a contradiction in the results; it explains why later answerability experiments needed a separate typed gate variant instead of simply toggling one reusable hook. For the real stack, the implication is straightforward: if answerability is expected to become an executive policy rather than a diagnostic note, it should remain a first-class interface rather than dissolving into family-specific answer templates.

## 8. Limitations

The current work has six major limitations:

1. experiments remain small-scale and mechanism-oriented
2. the topic-dossier middle layer is integrated but still minimal
3. multimodal evidence paths are still early-stage, especially on the real write-side stack even though the latest nano visual-ingest bridge now isolates this mechanism more clearly
4. latency and cost are not yet formalized at deployment scale
5. runtime integration across all planes remains incomplete
6. benchmark-scale LoCoMo / LongMemEval evidence is still future work

## 9. Conclusion

EchoMemory-MM advances a simple thesis:

> temporal questions, relation-heavy questions, longitudinal questions, readiness constraints, and answer-time evidence policy should not be collapsed into one flat retrieval assumption.

The current EchoMemory repository already contains the major structural planes needed for this direction, and the current nano experiments plus the real-code family subset support the same architectural conclusion from multiple angles. While benchmark-scale validation remains future work, the present package already establishes EchoMemory-MM as a credible, code-backed research direction for long-horizon multimodal memory.
