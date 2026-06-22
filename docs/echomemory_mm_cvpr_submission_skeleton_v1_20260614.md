# EchoMemory-MM CVPR Submission Skeleton v1

Date: 2026-06-14

## Purpose

This is not the final paper text.
It is the submission-facing structure that turns the current research package into something closer to a real CVPR paper workflow.

---

## Proposed title

**EchoMemory-MM: Query-Time Anchored Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

---

## 8-page core structure

### 1. Introduction

Goal:

- establish that long-horizon memory failure is not one failure
- motivate temporal, relation, visual, and readiness pressures
- state the central hypothesis:
  - temporal and relation-heavy questions should not share the same primary retrieval backbone

Need one figure:

- teaser figure of the five-plane memory system

### 2. Related Work

Group by pressure, not by paper list:

1. benchmark pressure
2. hierarchical and temporal retrieval
3. graph and structured recall
4. memory lifecycle and systems
5. agentic policy and multimodal memory

Need one table:

- representative paper -> what it contributes -> what EchoMemory-MM adopts or differs on

### 3. Problem Formulation

Define:

- append-only interaction stream
- story time vs write time vs query time
- query families
- readiness states

Need:

- formal notation for memory planes and answerability state

### 4. Method

Subsections:

1. session stream
2. atomic plane
3. temporal tree
4. relation graph
5. readiness plane
6. query-family-aware dual-backbone routing
7. multimodal evidence path

Need two figures:

- full system architecture
- retrieval routing diagram by query family

### 5. Experimental Setup

Include:

- code anchor description
- nano benchmark description
- claim boundary
- current and future benchmark tracks

Need one table:

- experiment name / task family / what it isolates / current scale

### 6. Results

Current paper-ready result groups:

1. anchored temporal ablation
2. relation-backbone ablation
3. dual-backbone benchmark
4. readiness on/off ablation

Need one consolidated table:

- tree-only
- graph-only
- dual-backbone
- readiness-gated full

### 7. Discussion

Discuss:

- what is really shown
- what is not shown
- why readiness is a correctness axis
- why multimodal evidence remains early-stage

### 8. Limitations and Conclusion

Limitations should explicitly name:

- benchmark scale
- multimodal maturity
- latency profiling gaps
- incomplete production integration

Conclusion should make the narrower but solid claim:

> EchoMemory-MM is a credible, code-backed, mechanism-validated architecture for long-horizon multimodal memory, with benchmark-scale validation as the next step.

---

## Figures to prepare

### Figure 1. Teaser

Show:

- one temporal question
- one relation question
- one readiness failure
- why flat retrieval fails

### Figure 2. System architecture

Show:

- session stream
- atom extraction
- organized projection
- temporal tree
- relation graph
- readiness plane
- router

### Figure 3. Query routing examples

Three example queries:

- relative-time
- relation-heavy
- image-evidence

---

## Tables to prepare

### Table 1. Literature positioning

Columns:

- paper
- year
- pressure
- relevance to EchoMemory-MM

### Table 2. Claim-evidence matrix

Columns:

- claim
- code evidence
- experiment evidence
- support level

### Table 3. Main results

Columns:

- setting
- temporal
- relation
- mixed
- readiness
- overall

---

## Current artifacts to plug in

- Main draft:
  - `/Users/chx/locomo-eval-web/docs/echomemory_mm_cvpr_draft_v7_20260614.md`
- 30-paper map:
  - `/Users/chx/locomo-eval-web/docs/echomemory_mm_30paper_map_and_nano_benchmark_20260613.md`
- Claim-evidence matrix:
  - `/Users/chx/locomo-eval-web/docs/echomemory_mm_claim_evidence_matrix_20260614.md`
- Submission package:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_submission_package_v2_20260614.html`

---

## Practical next step

If pushing toward a real submission, the best sequence is:

1. freeze claim boundary
2. produce the figures
3. run one larger benchmark track
4. rewrite the current draft into strict page-budget form

Without that sequence, the work stays in “strong research package” territory rather than “submission-ready paper” territory.
