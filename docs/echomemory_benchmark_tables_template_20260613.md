# EchoMemory Benchmark Tables Template

Date: 2026-06-13

## Purpose

This file is a fillable template for the real benchmark section of the EchoMemory-TG paper.

It is intentionally split into:

1. headline benchmark table
2. ablation table
3. latency / systems table
4. error-analysis table
5. readiness-gate table

The goal is to make later experimental runs plug into the paper directly.

---

## Table 1. Main benchmark results

### LoCoMo

| Method | Accuracy | Temporal Correctness | Evidence Hit Rate | Unknown / Abstain Rate | Notes |
|---|---:|---:|---:|---:|---|
| Summary-heavy baseline |  |  |  |  |  |
| Atom + vector |  |  |  |  |  |
| Atom + graph (no planner) |  |  |  |  |  |
| Atom + graph + planner |  |  |  |  |  |
| Atom + graph + planner + readiness gate |  |  |  |  |  |

### LongMemEval

| Method | Accuracy | Temporal Correctness | Evidence Hit Rate | Unknown / Abstain Rate | Notes |
|---|---:|---:|---:|---:|---|
| Summary-heavy baseline |  |  |  |  |  |
| Atom + vector |  |  |  |  |  |
| Atom + graph (no planner) |  |  |  |  |  |
| Atom + graph + planner |  |  |  |  |  |
| Atom + graph + planner + readiness gate |  |  |  |  |  |

---

## Table 2. Ablation study

| Variant | LoCoMo Accuracy | LongMemEval Accuracy | Temporal Correctness | Evidence Hit Rate | Main Failure Pattern |
|---|---:|---:|---:|---:|---|
| Full model |  |  |  |  |  |
| w/o planner |  |  |  |  |  |
| w/o graph path |  |  |  |  |  |
| w/o temporal resolver |  |  |  |  |  |
| w/o event nodes |  |  |  |  |  |
| w/o readiness gate |  |  |  |  |  |

---

## Table 3. Systems / efficiency results

| Method | Import Latency | Retrieval Latency | Storage Size | QA-ready Delay | Notes |
|---|---:|---:|---:|---:|---|
| Summary-heavy baseline |  |  |  |  |  |
| Atom + vector |  |  |  |  |  |
| Atom + graph + planner |  |  |  |  |  |
| Atom + graph + planner + readiness gate |  |  |  |  |  |

---

## Table 4. Error analysis by question type

### LoCoMo

| Question Type | Total | Correct | Error Rate | Main Failure Pattern | Example IDs |
|---|---:|---:|---:|---|---|
| Temporal |  |  |  |  |  |
| Relational |  |  |  |  |  |
| Profile / Preference |  |  |  |  |  |
| Multi-hop |  |  |  |  |  |
| Unknown / Abstention |  |  |  |  |  |

### LongMemEval

| Question Type | Total | Correct | Error Rate | Main Failure Pattern | Example IDs |
|---|---:|---:|---:|---|---|
| Temporal |  |  |  |  |  |
| Session-linking |  |  |  |  |  |
| Update / Evolving fact |  |  |  |  |  |
| Entity / Relation |  |  |  |  |  |
| Unknown / Abstention |  |  |  |  |  |

---

## Table 5. Readiness-gate diagnostics

| Account / Run | Messages Persisted | Atoms Extracted | Graph Synced | Organized Projected | Episode Projected | QA-ready | False Ready? | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Run 1 |  |  |  |  |  |  |  |  |
| Run 2 |  |  |  |  |  |  |  |  |
| Run 3 |  |  |  |  |  |  |  |  |

---

## Recommended figure pairings

### Main table figure pairing

- Table 1 + architecture figure
- Table 2 + planner-routing figure
- Table 4 + temporal graph figure
- Table 5 + readiness-state pipeline figure

### If extending to the CVPR branch

Add one more table:

## Table 6. Multimodal extension

| Method | Visual QA Accuracy | OCR-dependent QA Accuracy | Temporal Visual QA | Image Evidence Hit Rate | Notes |
|---|---:|---:|---:|---:|---|
| Text-only memory |  |  |  |  |  |
| Text + image evidence nodes |  |  |  |  |  |
| Text + image evidence + visual planner |  |  |  |  |  |

This table should only be used once the multimodal branch exists beyond the current nano prototype.
