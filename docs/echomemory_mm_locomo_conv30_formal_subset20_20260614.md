# EchoMemory-MM LoCoMo conv-30 Formal Subset-20

Date: 2026-06-14

## Purpose

The current EchoMemory-MM package now has a frozen benchmark protocol.
The next missing layer is a **paper-facing formal subset** that is:

1. stronger than a 5-question smoke run
2. still small enough to run repeatedly during development
3. balanced across the failure modes that matter most to the paper

This file defines that first subset:

> **LoCoMo conv-30 formal subset-20**

It is not the final benchmark table.
It is the first stable development subset for paper-facing evaluation.

---

## 1. Why conv-30

`conv-30` is already the most developed EchoMemory LoCoMo conversation in the current repository:

- real import run exists
- real QA smoke runs exist
- recall traces already exist
- failure patterns have already been inspected multiple times

That makes it the best first place to freeze a subset before scaling to more conversations.

---

## 2. Why 20 questions

The current repository has often used:

- `5` questions for smoke checks
- `5~10` questions for quick UI or local validation

That is useful for debugging, but still too narrow for a paper-facing development subset.

`20` questions is the first size that reasonably covers:

- temporal questions
- relation / multi-evidence questions
- reason / motivation questions
- detail-grounding questions

without being too expensive for repeated runs.

---

## 3. Selection principles

This subset is designed to stress the paper's main claims.

### 3.1 Temporal coverage

Include:

- explicit dates
- month-level dates
- timeline questions
- duration-style temporal reasoning

Reason:

- the paper claims temporal questions should prefer chronology-aware backbones

### 3.2 Relation / multi-evidence coverage

Include:

- both/common questions
- multi-event aggregation questions
- evidence spanning multiple dialog turns and sessions

Reason:

- the paper claims relation-heavy and multi-evidence questions should not be flattened into one retrieval route

### 3.3 Why / reason coverage

Include:

- motivation questions
- business-decision questions
- questions where passion alone is insufficient and event context must be included

Reason:

- these questions often expose failures in evidence composition, not just retrieval

### 3.4 Detail grounding coverage

Include:

- grounded detail questions
- visual/detail-linked questions
- attribute lookup that should not drift into generic summaries

Reason:

- these questions test whether the answer path is precise instead of merely plausible

---

## 4. Frozen subset definition

Machine-readable source:

- `/Users/chx/locomo-eval-web/configs/echomemory_mm_locomo_conv30_formal_subset20_20260614.json`

Question IDs:

1. `conv-30_qa0`
2. `conv-30_qa1`
3. `conv-30_qa3`
4. `conv-30_qa4`
5. `conv-30_qa7`
6. `conv-30_qa8`
7. `conv-30_qa13`
8. `conv-30_qa14`
9. `conv-30_qa17`
10. `conv-30_qa23`
11. `conv-30_qa24`
12. `conv-30_qa25`
13. `conv-30_qa29`
14. `conv-30_qa31`
15. `conv-30_qa32`
16. `conv-30_qa36`
17. `conv-30_qa43`
18. `conv-30_qa46`
19. `conv-30_qa58`
20. `conv-30_qa71`

Category mix:

- category `1`: 8 questions
- category `2`: 8 questions
- category `4`: 4 questions

---

## 5. Why these 20 specifically

### 5.1 Temporal anchors

Included:

- `qa0`, `qa1`, `qa7`, `qa8`, `qa13`, `qa14`, `qa32`, `qa36`

Why:

- they cover exact date, month, relative timeline, and event-time lookup

### 5.2 Multi-evidence / relation synthesis

Included:

- `qa3`, `qa17`, `qa23`, `qa24`, `qa25`, `qa29`, `qa31`

Why:

- these questions require combining multiple evidence points instead of copying one line

### 5.3 Reason / explanation stress

Included:

- `qa4`, `qa17`, `qa58`

Why:

- these questions expose whether the model retrieves and composes cause plus context

### 5.4 Detail precision

Included:

- `qa43`, `qa46`, `qa71`

Why:

- these questions punish vague but plausible answers

---

## 6. How to run it under the frozen protocol

### 6.1 Import

Use the benchmark protocol freeze:

- `/Users/chx/locomo-eval-web/docs/echomemory_mm_benchmark_protocol_freeze_20260614.md`

Import entry:

- `/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py`

### 6.2 QA and judge

Authoritative orchestration:

- `/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py`

For subset-20, the QA invocation must restrict questions to these IDs.

As of 2026-06-14, `echomemory_wait_and_eval.py` has been updated to forward
`--questions` into `echomemory_memory_qa.py`, so the authoritative orchestration
path can now execute the subset directly instead of requiring a separate manual QA-only rerun.

Recommended `--questions` value:

```text
conv-30_qa0,conv-30_qa1,conv-30_qa3,conv-30_qa4,conv-30_qa7,conv-30_qa8,conv-30_qa13,conv-30_qa14,conv-30_qa17,conv-30_qa23,conv-30_qa24,conv-30_qa25,conv-30_qa29,conv-30_qa31,conv-30_qa32,conv-30_qa36,conv-30_qa43,conv-30_qa46,conv-30_qa58,conv-30_qa71
```

---

## 7. What this subset should be used for

Use this subset for:

1. validating protocol-stable EchoMemory changes
2. comparing retrieval policy variants
3. comparing readiness and non-readiness paths
4. deciding whether a larger LoCoMo run is worth launching

Do not use this subset for:

1. final benchmark claims
2. broad SOTA claims
3. claims about multimodal benchmark completion

---

## 8. Expected paper-facing outputs

Every subset-20 run should emit:

- import summary
- QA CSV
- judge summary
- recall logs for all 20 questions if enabled
- run config snapshot
- a short summary page reporting:
  - accuracy
  - category breakdown
  - temporal subgroup accuracy
  - relation/multi-evidence subgroup accuracy
  - reason/detail subgroup accuracy

---

## 9. Next subset after this one

After subset-20 stabilizes, the next useful expansions are:

1. `conv-30 subset-40`
2. `multi-conversation subset`
3. `LongMemEval formal subset`

That order is intentional:

- first stabilize one conversation
- then expand question coverage
- then expand dataset scope

---

## Bottom line

This subset is the first paper-facing EchoMemory LoCoMo development slice that is:

- stronger than smoke
- still cheap enough to rerun
- aligned to the frozen benchmark protocol
- targeted at the paper's actual failure modes

That makes it the right next evaluation unit before full benchmark-scale runs.
