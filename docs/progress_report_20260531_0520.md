# Progress Report - 2026-05-31 05:20 CST

## Summary
This segment made pending Judge rows visible and actionable. Instead of only showing a pending count, the evaluation page now lists concrete pending samples with question, gold answer, agent response, and token estimate.

## Implemented
- Added `/api/pending-preview`.
- Pending preview uses formal Judge `result` only, not `simple_grade`, so exact matches still count as pending until formal Judge runs.
- Added `待 Judge 样本` panel in the evaluation page.
- Panel shows first 20 pending rows with:
  - question id
  - sample id
  - category
  - token estimate
  - question
  - gold answer
  - agent response
- Added direct controls:
  - `预检这些结果`
  - `开始 Judge 全部 pending`

## Verification
- `node --check static/app.js`: passed.
- `python3 -m py_compile server.py`: passed.
- `/api/pending-preview`:
  - LongMemEval 100: 100 pending rows.
  - LoCoMo judged smoke: 0 pending rows.
- Browser verification:
  - LongMemEval 100 pending panel shows `显示前 20 / 100 行`.
  - First pending examples include `e47becba`, `118b2229`, and `51a45a95`.
  - Panel displays gold answers and agent responses for inspection before Judge.
- `/health`: `ok locomo-eval-web`, recent runs available.

## Next
- Add a large-Judge safety confirmation state before launching 100 pending rows.
- Add row-level detail links from pending preview into evidence/context detail.
- Add export for pending-only CSV or report section.
- Continue backlog implementation.
