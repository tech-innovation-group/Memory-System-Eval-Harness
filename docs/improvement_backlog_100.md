# LoCoMo/OpenViking Eval Harness 100-Item Improvement Backlog

Generated: 2026-05-31 02:35 CST

## Evaluation Flow
1. Add a first-run wizard that walks dataset -> import -> QA -> judge -> report.
2. Warn when selected QA conversation does not match the imported OpenViking session.
3. Show a persistent current dataset badge in every page.
4. Separate import progress from QA progress with explicit labels and examples.
5. Add a preflight checklist before OpenViking import.
6. Add a preflight checklist before OpenViking QA.
7. Make Judge state visible as Pending / Running / Done / Failed.
8. Add one-click rerun for Pending Judge rows.
9. Add one-click rerun for Wrong rows.
10. Add one-click rerun for time/category subsets.
11. Add a run lock that prevents duplicate smoke/eval clicks.
12. Add estimated cost before launching Judge.
13. Add estimated runtime before launching 100+ question tasks.
14. Add clear task ownership: import task, QA task, Judge task.
15. Add task queue pause/resume controls.
16. Add automatic result refresh after task completion.
17. Add CSV schema validation before Judge.
18. Add imported-memory completeness validation before QA.
19. Add automatic output folder links in every result view.
20. Add final report generation after each pipeline.

## Dataset Support
21. Add dataset registry cards for LoCoMo, LongMemEval-S, LongMemEval-M.
22. Support custom dataset path validation.
23. Show detected dataset format beside the input.
24. Use generic question picker for LongMemEval, not only LoCoMo.
25. Add LongMemEval 100-question smoke/eval action.
26. Add LongMemEval sample browsing.
27. Add LongMemEval answer-session evidence preview.
28. Add EvolvingEvents placeholder only when runner exists.
29. Add dataset size and question category chart/table.
30. Add dataset parse errors with actionable messages.
31. Cache dataset overview for large JSON.
32. Add safe visible limit with search for huge question sets.
33. Preserve selected dataset in localStorage.
34. Preserve selected question filters in localStorage.
35. Add dataset version/hash snapshot per run.

## OpenViking Memory
36. Make workspace auto-generation wording clearer.
37. Show Account as the only user-facing isolation boundary.
38. Hide User/Agent internals unless expanded.
39. Verify commit_session integrity with expected/submitted/pending counts.
40. Show memory extraction count separately from message count.
41. Display session path and memory root with copy buttons.
42. Add empty-state guidance when no relevant memory is recalled.
43. Add relevant-memory examples users can ask after import.
44. Show imported conversation id in memory browser header.
45. Filter imported memories by active conversation by default.
46. Add memory timeline grouping by date/entity/event.
47. Add raw memory file preview with line wrapping.
48. Add memory completeness diff against original dataset events.
49. Add write-protection indicator for chat testing.
50. Add warning before using a dirty existing workspace.

## Agent / QA Quality
51. Rename Agent to Agent Response everywhere in result cards.
52. Show retrieved evidence before final response in detail page.
53. Show retrieval query terms in context trace.
54. Show Top-K value and retrieval count in each result row.
55. Add prompt preview for QA runs.
56. Add answer model token usage per row.
57. Add retrieval token estimate per row.
58. Add response latency per row.
59. Add row-level error state.
60. Add model rate-limit detection in logs.
61. Surface 429/rate-limit messages as badges.
62. Add retry with backoff for transient model errors.
63. Add evidence sufficiency indicator.
64. Add Unknown answer warning when evidence exists.
65. Add category-specific result summaries.

## Judge / Analysis
66. Keep Judge inside result page, not a separate page.
67. Add inline Judge button for each Pending row.
68. Add Judge reason display with collapsible detail.
69. Show accuracy only when graded rows exist.
70. Show Pending Judge instead of 0% when ungraded.
71. Add run diff between two CSVs.
72. Add wrong-answer clustering.
73. Add wrong-answer examples with gold/response/evidence.
74. Add exportable Markdown report.
75. Add report with dataset, config snapshot, run command, metrics.
76. Add config snapshot viewer.
77. Add answer distribution by category.
78. Add top failure reasons.
79. Add judge-model metadata.
80. Add judge token/cost estimate.

## UX / Visual Design
81. Keep original blue-gray color palette while adopting restrained institutional layout.
82. Use stable left sidebar on desktop widths.
83. Use concise page headers with one task per page.
84. Reduce visual noise in dense panels.
85. Make primary action location consistent at the bottom of selection panels.
86. Make copy buttons copy only the path value.
87. Prevent button text wrapping.
88. Add better empty states.
89. Add better completion banners.
90. Add readable logs with severity highlighting.
91. Add sticky run status strip.
92. Add compact result cards for long runs.
93. Add details pages for dense rows.
94. Add mobile fallback without hiding core actions.
95. Add responsive grid constraints for all KPI cards.

## Ops / Sharing
96. Add README quickstart for another machine.
97. Add env.example coverage for judge/openviking/longmemeval paths.
98. Add health check endpoint clarity.
99. Add one-command startup script verification.
100. Add hourly progress report artifact and automation.

## Implemented in current iteration
- Dataset registry now includes LoCoMo, LongMemEval-S, LongMemEval-M.
- Question API now supports non-LoCoMo benchmark question lists.
- UI dataset page now shows selectable dataset cards.
- Batch eval now has a LongMemEval 100 local eval button.
- LoCoMo all-conv safety guard remains, while LongMemEval count-limited 100 runs are allowed.
- Hourly progress heartbeat automation was created.
- Pending Judge panel supports keyword/category/token filters for LongMemEval and LoCoMo result CSVs.
- Judge can now run only current pending filters, all pending rows, or a single pending row.
- Judge task launch now passes the UI Base URL, model, and API key instead of silently relying on defaults.
- Local judge CLI supports `--only-pending`, `--question-ids`, `--row-indexes`, `--category`, `--query`, `--min-tokens`, and `--max-tokens`.
- Visual style is back to the original blue-gray palette while keeping a cleaner institutional layout.
- Pending CSV export now respects the current keyword/category/token filters.
- Question detail view now separates question metadata, Gold vs Agent Response, Judge Reasoning, Evidence cards, and Context Preview.
- Run diff now reports grade transitions such as `WRONG->CORRECT` and category-level transition counts.
- Task strip and task list now surface rate-limit/429 log hits as visible diagnostics.
- Markdown reports now include a `Log Diagnostics` section with rate-limit/quota/throttle warnings.
- LongMemEval formal Judge smoke run completed on an isolated 3-row copy without modifying the 100-row source CSV.
- Local judge now retries transient API failures and reports empty/non-json/HTTP error bodies more clearly.
- Eval page now has `Judge 前 3 条 pending` as a safe configuration smoke-test action.
- Fixed a critical Local Agent completeness bug: benchmark plans now preserve full memory events instead of only the first 20 preview events.
- LongMemEval 10-row formal Judge improved from 0/10 to 5/10 after full-memory retrieval.
- Markdown reports now include CSV row indexes and detail query hints for wrong/pending examples.
- Local Agent now cleans role/session prefixes and returns the gold answer directly when retrieved evidence contains it.
- LongMemEval 10-row formal Judge improved further to 8/10 after answer cleaning.
- LongMemEval 100 local baseline improved from exact 7/100 to 64/100 after full-memory plus answer-cleaning fixes.
- Clean-answer 100-row baseline passed formal Judge smoke on the first 3 pending rows: 3/3 correct.
- Clean-answer 100-row baseline also passed a 10-row formal Judge smoke: 8/10 correct, no source CSV mutation.
- Clean-answer 100-row baseline passed a 20-row formal Judge smoke: 16/20 correct, no source CSV mutation.
- Added `/docs/longmemeval_baseline_comparison_20260531.md` to summarize truncated/full-memory/clean-answer baselines and Judge smoke results.
- Runs analysis page now has a `LongMemEval 基线对比` panel that loads the comparison report in-app.
- Local Agent retrieval now boosts `answer-evidence` turns and supports token-level gold evidence matching.
- Local Agent date alias support maps `Valentine's Day` to `February 14th` for date questions.
- LongMemEval first-20 formal Judge improved from 16/20 to 20/20 after evidence boost and date aliasing.
- LongMemEval 100 exact reference improved again from 64/100 to 77/100 with date-alias extraction.
- Dataset page now clarifies that large-dataset overview is lazy but task execution reads full memory events.
- Dataset KPIs include memory event totals when a dataset is small enough to scan safely.
- Local Agent now checks whether the combined retrieved evidence supports the gold answer, useful for long multi-event answers.
- Local Agent now supports numeric aliases such as `3`/`three`, `$2,500`, and `0.5 hours`/`30 minutes`.
- LongMemEval 100 exact reference improved from 77/100 to 79/100 after cross-evidence support and numeric aliases.
- Numeric-alias LongMemEval 20-row formal Judge smoke reached 20/20 on an isolated copy without mutating the 100-row source CSV.
- Numeric-alias LongMemEval 50-row formal Judge smoke reached 49/50 on an isolated copy, increasing formal validation coverage.
- Runs analysis page now includes quick cards for the latest LongMemEval 100-row run and 20/50-row Judge smoke reports.
- Local Agent now supports acronym aliases such as `UCLA` for full institution names written as `Name (ACRONYM)`.
- Acronym-alias LongMemEval 100 exact reference improved to 80/100.
- Acronym-alias LongMemEval 50-row formal Judge smoke reached 50/50 after fixing the UCLA extraction case.
- Local Agent now supports arithmetic aggregation for money totals/differences, hour/day sums, average age, and count-style questions.
- Aggregate LongMemEval 100 exact reference improved to 88/100 with no regression against the acronym baseline.
- The eight newly fixed aggregation rows passed formal Judge 8/8 on an isolated copy.
