# Progress Report - 2026-05-31 05:10 CST

## Summary
This segment made formal Judge execution more actionable, especially for LongMemEval 100. The result page now explains how many rows still need Judge, how many context tokens are likely involved, and a rough wall-clock estimate before the user starts judging.

## Implemented
- Added `Judge 估算` panel under the Judge preflight area.
- Estimate panel updates after `刷新结果` and after `Judge 预检`.
- Estimate includes:
  - pending Judge rows
  - average context tokens
  - estimated judge input tokens
  - rough wall time at `parallel=10`
- LongMemEval result view now shows:
  - `正式准确率: 待 Judge`
  - `Exact Match 参考: 7/100 · 7%`
  - `Pending: 100`
  - `Tokens: 198093`

## Verification
- `node --check static/app.js`: passed.
- `python3 -m py_compile server.py`: passed.
- `/health`: `ok locomo-eval-web`.
- Browser verification using the existing LongMemEval 100 run:
  - Selected `manual_longmemeval_100_check_v2`.
  - Refreshed result in 批量评测.
  - UI displayed:
    - pending rows: 100
    - average context tokens: 1,981
    - estimated judge input: 198,090
    - rough wall time: 30s - 2m 0s

## Next
- Add row-level controls for pending Judge examples.
- Add a safer Judge launch confirmation for large pending sets.
- Improve memory import completeness details in the workflow guide.
- Continue iterating through the backlog.
