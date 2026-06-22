# Progress Report - 2026-05-31 03:15 CST

## Summary
This segment improved usability during long-running evaluations. The harness now exposes active task state, recent task history, artifact paths, persisted dataset selection, and searchable LongMemEval page previews. It also strengthened the handoff story with README/env updates and a passing preflight check.

## Implemented
- Added global active dataset pill in the page header.
- Persisted selected dataset path/format in localStorage.
- Added config snapshot viewer to Runs analysis.
- Changed config snapshot API to return both `path` and `config`.
- Added LongMemEval paged preview keyword filtering through `/api/questions-page?q=...`.
- Added richer report export content: token estimates and wrong examples.
- Added evaluation task strip showing current task status/id/output CSV.
- Added result artifact path list for CSV, summary, judge summary and wrong analysis.
- Added recent task list on the evaluation page, backed by `/api/tasks`.
- Updated README and env.example for LoCoMo/OpenViking/LongMemEval usage.
- Ran `./preflight.sh` successfully.

## Verification Evidence
- `node --check static/app.js`: passed.
- `python3 -m py_compile server.py`: passed.
- LongMemEval searchable paging:
  - query `commute` returned `118b2229 How long is my daily commute to work?`
- Report export contains:
  - `## Wrong Examples`
  - `Total injection tokens est`
  - `Reasoning:`
- Recent task list verified by launching `ui task strip smoke`:
  - Task: `<repo-root>/runs/local_agent_20260531_031143_6b2d0a`
  - Output CSV: `<repo-root>/runs/local_agent_20260531_031143_6b2d0a/local_agent/local_agent_results.csv`
  - Status: succeeded
- Preflight passed and recognized LoCoMo, LongMemEval-S, LongMemEval-M.

## Still Open
- Need a browser visual pass for the new task strip and recent task list.
- LongMemEval search is sequential paging from offset, not indexed full-text search.
- OpenViking full LoCoMo run still needs a fresh verified run with matching imported workspace/session.
- More backlog items remain to be implemented before 09:00.

## Next Steps
1. Visual polish for task strip, recent tasks, and result artifact list.
2. Add artifact/report shortcuts to Runs detail view.
3. Add mismatch warning when selected LoCoMo questions do not match imported memory conversation.
4. Continue implementing the highest-impact backlog items.
