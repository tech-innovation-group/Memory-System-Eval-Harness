# Progress Report - 2026-05-31 02:35 CST

## Summary
This iteration moved the harness toward the requested 9-hour objective by adding a 100-item improvement backlog, wiring LongMemEval into the dataset registry, adding a LongMemEval 100-question local evaluation path, fixing misleading local accuracy reporting, and validating both LoCoMo and LongMemEval test execution.

## Implemented
- Added `<repo-root>/docs/improvement_backlog_100.md` with 100 concrete improvement items across evaluation flow, dataset support, OpenViking memory, agent QA, judge/analysis, UX, and ops.
- Added dataset registry entries for LoCoMo, LongMemEval-S, and LongMemEval-M.
- Added large-dataset lazy overview behavior so 264MB/2.6GB LongMemEval files do not freeze the dataset page.
- Added generic benchmark question API support so `/api/questions` is no longer LoCoMo-only.
- Added dataset cards to the UI.
- Added `LongMemEval 100 题` action in the evaluation page.
- Adjusted Local Agent summary semantics: local exact match is now `exact_match_count/rate`; formal `accuracy` stays null until Judge rows exist.
- Created hourly heartbeat automation: `locomo-eval-hourly-progress-report`.

## Verification Evidence
- `node --check <repo-root>/static/app.js`: passed.
- `python3 -m py_compile <repo-root>/server.py <repo-root>/scripts/local_memory_agent.py`: passed.
- `/api/datasets` now returns:
  - `locomo` -> 10 samples / 1540 questions.
  - `longmemeval-s` -> lazy large dataset, `dataset/longmemeval.sample.json`.
  - `longmemeval-m` -> lazy large dataset, `dataset/longmemeval.sample.json`.
- LongMemEval 100 local eval completed:
  - CSV: `<repo-root>/runs/manual_longmemeval_100_check_v2/local_agent/local_agent_results.csv`
  - Rows: 100
  - Summary: `<repo-root>/runs/manual_longmemeval_100_check_v2/local_agent/summary.json`
  - Accuracy: pending Judge, exact-match reference 7/100.
- LoCoMo smoke test completed:
  - CSV: `<repo-root>/runs/manual_locomo_smoke_5/local_agent/local_agent_results.csv`
  - Rows: 5
  - Accuracy: pending Judge, exact-match reference 1/5.

## Current Risks
- LongMemEval official file is large; full question browsing still needs a paged/streamed API before it should expose all rows in the browser.
- LongMemEval current runner is local retrieval smoke, not OpenViking memory import.
- Formal accuracy still requires Judge execution with a valid judge API key/config.
- OpenViking full LoCoMo run should still be verified separately after confirming active workspace/session alignment.

## Next Hour Plan
1. Add a paged question endpoint for large LongMemEval files.
2. Add visible dataset status badges and clearer lazy-dataset copy in UI.
3. Add run report export that automatically links CSV, summary, config, and logs.
4. Run Judge on a small LoCoMo or LongMemEval subset if judge credentials are available in config/environment.
5. Continue implementing high-priority items from the 100-item backlog.
