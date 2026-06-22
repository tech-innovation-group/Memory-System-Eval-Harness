# EchoMemory-MM Experiment Setup and Reproducibility

Date: 2026-06-14

## Purpose

This note turns the current nano experiment package into a paper-facing reproducibility appendix.

It does three things:

1. states exactly which scripts produce the current mechanism-level evidence
2. records what each experiment isolates and what it does **not** prove
3. provides a minimal re-run path tied to real files in the repository

This is intentionally narrower than a full benchmark appendix.
Its job is to make the current submission package auditable and reproducible.

---

## 1. Repository locations

### Main nano code directory

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano`

### Current paper-facing reports

- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_main_results_table_20260614.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_claim_evidence_matrix_20260614.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_paper_figures_mockups_20260614.html`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_submission_workbench_20260614.html`

---

## 2. Canonical nano implementations

### 2.1 Text-first canonical nano

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg.py`

Role:

- smallest faithful version of the text-first EchoMemory-TG direction
- append-only stream
- story-time normalization
- query-time anchor
- temporal tree
- graph-backed retrieval
- readiness gate

Use this when the goal is to explain the main architecture without multimodal details.

### 2.2 Unified MM+TG nano

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_unified_mm_tg.py`

Role:

- smallest unified teaching version of:
  - text stream
  - image evidence
  - story time vs mention time
  - temporal graph retrieval
  - readiness-aware answer gating

Use this when the goal is to explain the forward CVPR-shaped direction.

---

## 3. Experiments included in the current paper package

The current paper package uses four mechanism-level result lines.
These are the same lines summarized in the main results table.

### 3.1 Anchored temporal ablation

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation.py`
- output json:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation_results.json`
- output html:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_anchored_temporal_ablation_20260614.html`

What it isolates:

- anchored relative-time questions
- whether chronology-heavy questions should prefer a temporal-tree backbone

Current result:

- tree-only: `3 / 3`
- graph-only: `2 / 3`
- dual-backbone: `3 / 3`

What it supports:

- temporal questions prefer a chronology-aware primary backbone

What it does **not** support:

- broad superiority on all QA
- benchmark-scale time reasoning claims

### 3.2 Relation-backbone ablation

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation.py`
- output json:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_results.json`
- output html:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_report.html`

What it isolates:

- relation-heavy questions
- whether graph should be the primary backbone for relation-centric recall

Current result:

- tree-only: `0 / 3`
- graph-only: `3 / 3`
- dual-backbone: `3 / 3`

What it supports:

- relation-heavy questions prefer graph-first evidence paths

What it does **not** support:

- that graph should dominate every query family
- that graph-only is a complete long-horizon solution

### 3.3 Dual-backbone benchmark

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark.py`
- output json:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark_results.json`
- output html:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_benchmark_20260613.html`

What it isolates:

- a mixed toy benchmark across:
  - temporal
  - relational
  - temporal_relational
  - visual

Current result:

- tree-only: `3 / 12`
- graph-only: `5 / 12`
- dual-backbone: `8 / 12`

Per family:

- temporal: tree `3 / 3`, graph `0 / 3`, dual `3 / 3`
- relational: tree `0 / 3`, graph `1 / 3`, dual `1 / 3`
- temporal_relational: tree `0 / 3`, graph `1 / 3`, dual `1 / 3`
- visual: tree `0 / 3`, graph `3 / 3`, dual `3 / 3`

What it supports:

- different failure modes prefer different primary structures
- dual-backbone routing is more balanced overall than single-backbone variants

What it does **not** support:

- benchmark-scale performance claims
- mature multimodal system claims

### 3.4 Readiness ablation

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_experiment.py`
- output json:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_results.json`
- output html:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_report.html`

Systems compared:

- `baseline`
  - no temporal normalization
  - no graph-first retrieval
  - no readiness gate
- `temporal_graph`
  - temporal normalization
  - graph-first retrieval
  - no readiness gate
- `full`
  - temporal normalization
  - graph-first retrieval
  - readiness gate

Current result:

- baseline: `1 / 5`
- temporal_graph: `4 / 5`
- full: `5 / 5`

What it supports:

- persisted memory is not automatically QA-ready
- readiness gating changes correctness, not only user experience

What it does **not** support:

- production latency guarantees
- real distributed-system readiness guarantees

---

## 4. Why these experiments are valid for the paper

The current paper does **not** claim benchmark-scale SOTA.
The evidence is instead mechanism-level and paired to specific claims.

The pairing is:

1. temporal claim
   - anchored temporal ablation
2. relation claim
   - relation-backbone ablation
3. balanced routing claim
   - dual-backbone benchmark
4. readiness claim
   - readiness ablation

This means the current paper package is strongest when framed as:

> a code-backed architectural proposal with mechanism-level validation

not as:

> a benchmark-complete state-of-the-art system

---

## 5. Minimal re-run commands

From the repository root or any location with absolute paths:

```bash
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_experiment.py
```

For understanding the architecture itself:

```bash
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_unified_mm_tg.py
```

---

## 6. Exact numbers currently used in the paper package

These are the numbers that should remain consistent across the current paper materials unless the experiments are explicitly rerun and updated:

- anchored temporal ablation:
  - tree `3 / 3`
  - graph `2 / 3`
  - dual `3 / 3`
- relation-backbone ablation:
  - tree `0 / 3`
  - graph `3 / 3`
  - dual `3 / 3`
- dual-backbone benchmark:
  - tree `3 / 12`
  - graph `5 / 12`
  - dual `8 / 12`
- readiness ablation:
  - baseline `1 / 5`
  - temporal_graph `4 / 5`
  - full `5 / 5`

---

## 7. Current reproducibility strengths

1. the scripts are real and local
2. the outputs are already materialized as JSON and HTML
3. each experiment isolates one architectural question
4. the main results table can be traced back to concrete files

---

## 8. Current reproducibility limitations

1. these are still nano / toy experiments
2. there is no large-scale benchmark rerun script yet integrated into the paper package
3. there is no latency / cost appendix yet
4. multimodal evidence remains toy-scale rather than benchmark-scale

---

## 9. Practical recommendation for the paper

In the current draft, the most honest and strongest wording is:

> We provide mechanism-level evidence across four paired experiment lines, each tied to a distinct architectural claim.

This is stronger than calling the package preliminary, but safer than implying benchmark completion.
