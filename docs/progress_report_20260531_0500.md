# Progress Report - 2026-05-31 05:00 CST

## Summary
This segment fixed an important evaluation semantics issue: LongMemEval exact-string matches are now clearly separated from formal Judge accuracy. The UI and reports no longer risk presenting a 7/100 exact-match reference as a formal judged score.

## Implemented
- Split formal `result` grading from `simple_grade` / exact-match reference in CSV summary parsing.
- `parse_csv_summary` now reports:
  - `accuracy`: formal Judge-only accuracy.
  - `result_counts`: formal Judge result counts.
  - `simple_correct`, `simple_accuracy`, `exact_match_reference`: string/reference metrics only.
- `compare_runs` no longer falls back from formal score to exact/simple score.
- Runs list now labels scores explicitly:
  - `formal 待 Judge`
  - `exact 7/100 7%`
- Run detail KPI now separates:
  - `正式准确率`
  - `Exact Match`
- Exported Markdown reports now use:
  - `Formal Judge score`
  - `Exact match reference: 7/100 · 7.0%`
- Workflow guide import step can use scanned disk/imported-memory state, not only browser-local state.

## Verification
- `node --check static/app.js`: passed.
- `python3 -m py_compile server.py`: passed.
- `/api/results` for LongMemEval 100:
  - rows: 100
  - formal graded: 0
  - formal accuracy: null
  - pending Judge: 100
  - exact reference: 7/100, 7.0%
- `/api/runs?limit=6` compare output:
  - `manual_longmemeval_100_check_v2`: `score=None`, `exact_match_reference=0.07`, `pending=100`
  - `manual_locomo_smoke_5`: `score=0.2`, `exact_match_reference=0.2`
- Browser verification:
  - Runs list shows `manual_longmemeval_100_check_v2 · formal 待 Judge · exact 7/100 7%`.
  - LoCoMo smoke shows `formal 20% · exact 1/5 20%`.

## Next
- Add LongMemEval formal Judge preflight and estimated cost/time display.
- Add row-level pending-Judge controls.
- Improve memory import completeness by showing disk-scanned session counts in the workflow step tooltip/detail.
- Continue backlog implementation.
