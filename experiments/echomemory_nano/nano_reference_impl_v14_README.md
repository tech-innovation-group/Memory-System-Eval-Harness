# EchoMemory Nano Reference v14

This file is a single-file reference implementation for the paper story.

## What it keeps

- append-only observations
- three-clock time: `event_time`, `mention_time`, `write_time`
- atom extraction
- topic dossier middle layer
- temporal tree
- relation graph
- readiness gate
- contract-aware second pass

## Why it exists

It is not a benchmark runner. It is the smallest readable version of the method.
The goal is to show that EchoMemory is a stream-to-structure memory system,
not a flat vector store and not a dataset-specific keyword hack.

## How to run

```bash
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14.py
```

Outputs:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_reference_impl_v14_20260616.html`

## What to look at first

1. `append_text()` / `append_image()`
2. `_extract_atoms()`
3. `_build_dossiers()`
4. `_build_temporal_tree()`
5. `_build_graph()`
6. `plan()`
7. `retrieve()`

## Smoke behavior

The demo covers:

- temporal query
- relational query
- longitudinal query
- visual query
- readiness query

If the contract is missing, the answer becomes `unknown`.

## Why this is generic

No benchmark-specific keyword list is used to identify entities or topics.
The example cases are only illustrative, not task hacks.
