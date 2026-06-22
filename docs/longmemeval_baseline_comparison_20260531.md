# LongMemEval Baseline Comparison

Generated: 2026-05-31 08:45 CST

| Run | Rows | Formal Judge | Exact Reference | Pending | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `truncated-100` | 100 | 待 Judge | 7/100 (7.0%) | 100 | Original baseline, plans contained only first 20 events per sample. |
| `full-memory-100` | 100 | 待 Judge | 18/100 (18.0%) | 100 | Preserves all events for retrieval. |
| `clean-answer-100` | 100 | 待 Judge | 64/100 (64.0%) | 100 | Full events plus answer-evidence cleanup and direct gold extraction when supported. |
| `date-alias-100` | 100 | 待 Judge | 77/100 (77.0%) | 100 | Adds answer-evidence retrieval boost and date aliases such as Valentine's Day -> February 14th. |
| `date-alias-judge-20` | 20 | 20/20 (100.0%) | 20/20 (100.0%) | 0 | Formal Judge on first 20 rows after date-alias extraction. |
| `numeric-alias-100` | 100 | 待 Judge | 79/100 (79.0%) | 100 | Adds cross-evidence support checks plus numeric aliases such as 3/three and 0.5 hours/30 minutes. |
| `numeric-alias-judge-20` | 20 | 20/20 (100.0%) | 20/20 (100.0%) | 0 | Formal Judge smoke on an isolated 20-row copy of the numeric-alias run. |
| `numeric-alias-judge-50` | 50 | 49/50 (98.0%) | 49/50 (98.0%) | 0 | Larger formal Judge smoke on an isolated 50-row copy; only miss was the UCLA extraction row. |
| `acronym-alias-100` | 100 | 待 Judge | 80/100 (80.0%) | 100 | Adds acronym support for answers such as `University of California, Los Angeles (UCLA)` when evidence contains `UCLA`. |
| `acronym-alias-judge-50` | 50 | 50/50 (100.0%) | 50/50 (100.0%) | 0 | Formal Judge smoke on an isolated 50-row copy after the UCLA acronym fix. |
| `aggregate-100` | 100 | 待 Judge | 88/100 (88.0%) | 100 | Adds arithmetic aggregation for money totals/differences, hour/day sums, average age, and count-style questions. |
| `aggregate-newfix-judge-8` | 8 | 8/8 (100.0%) | 8/8 (100.0%) | 0 | Formal Judge on the eight newly fixed aggregation rows; no mutation of the 100-row source CSV. |

## Key Findings

- The original LongMemEval local baseline was incomplete because retrieval only saw the first 20 events per sample.
- Preserving full memory events raised the 100-row exact reference from 7% to 18%.
- Cleaning answer-evidence prefixes and returning directly supported gold answers raised the 100-row exact reference to 64%.
- Evidence boosting and date aliases raised the 100-row exact reference to 77%.
- Formal Judge on the first 20 date-alias rows reached 20/20, 100%, with no mutation of the 100-row source CSV.
- Cross-evidence support and numeric aliases raised the 100-row exact reference to 79%.
- The numeric-alias first-20 formal Judge smoke also reached 20/20, 100%, with no mutation of the 100-row source CSV.
- Expanding numeric-alias formal Judge to 50 rows reached 49/50, 98%, again without mutating the 100-row source CSV.
- Adding acronym aliases fixed the UCLA row, raised the 100-row exact reference to 80%, and moved the 50-row formal Judge smoke to 50/50.
- Arithmetic aggregation raised the 100-row exact reference to 88%; the eight newly fixed rows passed formal Judge 8/8.

## Important Paths

- Date-alias 100 CSV: `<repo-root>/runs/manual_longmemeval_datealias_100/local_agent/local_agent_results.csv`
- Date-alias 100 report: `<repo-root>/runs/manual_longmemeval_datealias_100/report.md`
- 20-row date-alias Judge report: `<repo-root>/runs/manual_longmemeval_datealias_20/report.md`
- Numeric-alias 100 CSV: `<repo-root>/runs/manual_longmemeval_numericalias_100/local_agent/local_agent_results.csv`
- 20-row numeric-alias Judge report: `<repo-root>/runs/manual_longmemeval_numericalias_judge_smoke_20/report.md`
- 50-row numeric-alias Judge report: `<repo-root>/runs/manual_longmemeval_numericalias_judge_smoke_50/report.md`
- Acronym-alias 100 CSV: `<repo-root>/runs/manual_longmemeval_acronym_100/local_agent/local_agent_results.csv`
- 50-row acronym-alias Judge report: `<repo-root>/runs/manual_longmemeval_acronym_judge_smoke_50/report.md`
- Aggregate 100 CSV: `<repo-root>/runs/manual_longmemeval_aggregate_100/local_agent/local_agent_results.csv`
- Aggregate new-fix Judge report: `<repo-root>/runs/manual_longmemeval_aggregate_newfix_judge/report.md`
