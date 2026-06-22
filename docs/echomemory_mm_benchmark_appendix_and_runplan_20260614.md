# EchoMemory-MM Benchmark Appendix and Run Plan

Date: 2026-06-14

## Purpose

This note fills the largest remaining paper gap after the current nano package:

- how to honestly talk about benchmark-scale evidence
- what can already be executed in the local repository
- what should be run next for a stronger submission package

This is not a fake “results appendix”.
It is a benchmark-facing run plan and paper appendix scaffold built from the current repository state.

---

## 1. Current benchmark state

The current EchoMemory-MM package already contains:

1. code-backed structure analysis
2. a 30-paper map
3. canonical nano implementations
4. four mechanism-level experiment lines
5. submission-style drafts

What it does **not** yet contain is a clean benchmark-scale evidence layer that matches the ambition of a CVPR submission.

So the current benchmark status should be described as:

> benchmark-aware, but not benchmark-complete

That wording matters. It is more accurate than “preliminary”, but avoids overclaiming that LoCoMo or LongMemEval have already been run to paper-final scale.

---

## 2. What benchmark infrastructure already exists in the repository

### 2.1 LoCoMo import path

- `/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py`

Role:

- create sessions
- add messages
- commit sessions
- run integrity checks
- emit import-side status summaries

This is the main write-path entry for EchoMemory on LoCoMo-style conversation data.

### 2.2 QA runner

- `/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py`

Role:

- answer questions using the EchoMemory retrieval path
- currently acts as the main memory QA runner for LoCoMo-style evaluation

### 2.3 Wait-and-eval discipline

- `/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py`

Role:

- wait for import stabilization
- reduce false-ready evaluation
- then trigger QA and judge

This is especially important because the current paper makes readiness a correctness claim.

### 2.4 Dataset adapter layer

- `/Users/chx/locomo-eval-web/scripts/benchmark_adapter.py`

Role:

- unify LoCoMo / LongMemEval style samples into common execution structures
- normalize question / answer / query_time / turn organization

### 2.5 Official-style scoring path

- `/Users/chx/locomo-eval-web/scripts/longmemeval_official_eval.py`

Role:

- score generated answers rather than generate them
- useful as the scoring end of a benchmark pipeline

### 2.6 Platform / task integration

- `/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py`
- `/Users/chx/locomo-eval-web/memory/plugins/echomemory/plugin.py`
- `/Users/chx/locomo-eval-web/server.py`

Role:

- wire import / QA / judge execution into the web platform
- make benchmark runs visible from the current evaluation interface

---

## 3. What benchmark evidence currently exists

There are three levels of evidence in the repository.

### 3.1 Mechanism-level evidence

This is the strongest current layer.

Included:

- anchored temporal ablation
- relation-backbone ablation
- dual-backbone benchmark
- readiness ablation

These support:

- temporal-backbone claim
- graph-backbone claim
- dual-backbone balance claim
- readiness correctness claim

### 3.2 Small real-run or smoke evidence

Examples:

- conv30-related pages
- platform operator guides
- diagnostic pages
- status pages

These are useful because they show the system has already touched real evaluation workflows.

However, they are still not the same as a final benchmark appendix.

### 3.3 Missing paper-final evidence

Still missing:

- one clean LoCoMo benchmark table with fixed protocol
- one clean LongMemEval benchmark table with fixed protocol
- run-to-run settings frozen in one place
- latency / runtime cost appendix
- a stronger multimodal benchmark line

---

## 4. How to describe the benchmark status in the paper right now

The safest accurate wording is:

> We provide mechanism-level validation and benchmark-facing execution infrastructure, but not yet benchmark-scale completion.

Or, more explicit:

> The current package includes reproducible nano experiments and real benchmark execution paths, while large-scale LoCoMo and LongMemEval tables remain future work.

This wording is strong enough to sound serious, but honest enough to survive scrutiny.

---

## 5. Recommended benchmark appendix structure for the paper

When the paper grows from the current package into a stronger submission, the benchmark appendix should contain at least these sections.

### 5.1 Datasets

Include:

- LoCoMo
- LongMemEval
- optional multimodal extension track

For each:

- task family
- memory pressure type
- whether query_time is present
- whether multimodal evidence exists

### 5.2 Protocol

State:

- import policy
- readiness wait policy
- retrieval configuration
- answer generation configuration
- judge / scoring configuration

This is crucial because the current paper makes readiness a first-class claim.

### 5.3 Primary metrics

At minimum:

- answer accuracy
- temporal subgroup accuracy
- relation subgroup accuracy
- readiness-sensitive subgroup accuracy
- abstention or “not_ready” correctness where applicable

### 5.4 Secondary systems metrics

At minimum:

- import time
- QA latency
- failed-import rate
- readiness completion rate

### 5.5 Error taxonomy

Group failures into:

- story time vs write time confusion
- relation under-routing
- visual evidence loss
- false-ready answering

This taxonomy already matches the paper’s method story.

---

## 6. Recommended next benchmark run sequence

The next useful benchmark sequence is not “run everything at once”.
It should proceed in four stages.

### Stage A. Protocol freeze

Freeze:

- exact import path
- readiness wait policy
- QA runner
- scoring path
- logging schema

Deliverable:

- one frozen benchmark config page

### Stage B. Small formal subset

Run:

- LoCoMo subset with fixed protocol
- LongMemEval subset with fixed protocol

Goal:

- verify that the paper-facing metrics and subgroup buckets are stable

Deliverable:

- one paper-facing subset table

### Stage C. Full benchmark execution

Run:

- full LoCoMo track
- full LongMemEval track

Goal:

- produce real headline numbers

Deliverable:

- benchmark main table
- subgroup table
- readiness / systems table

### Stage D. Ablation tie-back

Goal:

- connect benchmark deltas back to the mechanism story

This matters because otherwise the paper becomes “a bunch of scores” instead of “a structured architecture argument”.

---

## 7. Minimal current run plan from repository evidence

For LoCoMo-like runs, the repository already suggests this path:

1. import with:
   - `echomemory_locomo_import.py`
2. wait for stable state with:
   - `echomemory_wait_and_eval.py`
3. answer with:
   - `echomemory_memory_qa.py`
4. score with:
   - local judge path or benchmark-specific scorer

For LongMemEval-like runs:

1. normalize data through:
   - `benchmark_adapter.py`
2. generate answers through EchoMemory QA path
3. score through:
   - `longmemeval_official_eval.py`

---

## 8. What this benchmark appendix would add to the current submission

It would improve the current paper package in three concrete ways.

### 8.1 It explains the gap honestly

Instead of vaguely saying “larger experiments are future work”, it states:

- what can already run
- what has already been validated
- what is still missing

### 8.2 It converts missing work into a run plan

That means collaborators or future-you can continue the paper without rediscovering the execution path.

### 8.3 It keeps the paper coherent

The method story says:

- time should be a backbone
- graph should be a backbone
- readiness changes correctness

The benchmark appendix should therefore evaluate exactly those axes rather than only overall accuracy.

---

## 9. Current strongest benchmark-facing conclusion

The strongest accurate benchmark-facing conclusion is:

> EchoMemory-MM already has the infrastructure and mechanism-level evidence needed for a serious benchmark campaign, but its present paper package should still be positioned as benchmark-facing rather than benchmark-complete.
