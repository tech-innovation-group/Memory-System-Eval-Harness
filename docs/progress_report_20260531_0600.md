# Progress Report - 2026-05-31 06:00 CST

## Summary
This segment made exported reports more useful for pending-Judge workflows. Reports now include pending-only CSV paths and concrete pending examples, so a report can be shared or reviewed without opening the web UI.

## Implemented
- Added pending-only section to exported Markdown reports.
- Report artifacts now include `Pending CSV`.
- Pending section includes:
  - pending row count
  - pending CSV path
  - pending examples with question id, sample id, category, token estimate, question, gold answer, and agent response
- Pending CSV is generated during report export when pending rows exist.
- Reports preserve the formal Judge vs exact-match distinction.

## Verification
- `node --check static/app.js`: passed.
- `python3 -m py_compile server.py`: passed.
- LongMemEval report:
  - path: `/Users/chx/locomo-eval-web/runs/manual_longmemeval_100_check_v2/report.md`
  - pending rows: 100
  - pending CSV: `/Users/chx/locomo-eval-web/runs/manual_longmemeval_100_check_v2/local_agent/local_agent_results.pending_judge.csv`
  - includes examples such as `e47becba`, `118b2229`, `51a45a95`
- LoCoMo smoke report:
  - path: `/Users/chx/locomo-eval-web/runs/manual_locomo_smoke_5/report.md`
  - pending rows: 0
  - pending CSV: `-`
- Pending CSV line counts:
  - LongMemEval pending CSV: 101 lines including header, 100 rows
  - LoCoMo pending CSV: 1 line header only
- `/health`: `ok locomo-eval-web`.

## Next
- Add pending panel filters by category/token range.
- Add selected-row Judge subset flow.
- Add report links from pending rows back to row index/question detail.
- Continue backlog implementation toward 09:00.
