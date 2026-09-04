---
name: echomem-eval-startup
description: Preflight, start, inspect, and recover the Memory-System-Eval-Harness server used for EchoMem develop or PR LoCoMo conv-30 evaluations. Use when deploying a new server, updating the evaluator, a Feishu bot does not reply, EchoMem fails health checks, a task exits before QA/Judge, or an agent must diagnose the platform without modifying EchoMem source.
---

# EchoMem Evaluation Startup

Keep EchoMem source unchanged. Fix only deployment, environment, Docker, model-service,
GitHub access, or evaluation-platform problems. If evidence points to the tested PR,
report it and stop.

## Fast Path

1. Run `scripts/preflight.sh --strict`.
2. Read `/opt/memory-eval-harness/deploy/ECHOMEM_EVAL_RUNBOOK.md` when the check fails.
3. Inspect the latest records in `incidents.jsonl` for a matching failure signature.
4. Verify the Web callback with `docker logs --since 10m memory-eval-web`.
5. Submit only after Web, Docker, model configuration, ports, and callback checks pass.

## Fixed Evaluation Contract

- Benchmark: LoCoMo `conv-30`, denominator `81`.
- Sequence: prepare source, start EchoMem, inject memory, QA, Judge, summarize.
- Source: latest `develop`, or GitHub's current `develop + open PR` merge snapshot.
- Queue: one task runs at a time; repeated commits are still evaluated again.
- Isolation: use a new source directory, workspace, tenant, session, cache directory,
  and result directory for every task.
- Reuse: share only immutable embedding warm-up files and dependency images.
- Runtime: `ECHOMEM_AUTO_COMMIT_THRESHOLD=20000` and
  `ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE=0.7`.
- Tools: disabled; MCP conversational reads disabled; retain the required first
  MCP `memory_query` retrieval.
- Failure accounting: Judge errors count as wrong answers in the 81-question denominator.

## Diagnosis Order

1. Confirm the tested develop, PR head, and merge commit.
2. Read `container.log`, then `echomem.inspect.*.json` and `echomem.logs.*.txt`.
3. Classify the failure as EchoMem code, EchoMem config, evaluation platform,
   dependency, model service, Docker/server, or Git merge.
4. State explicitly whether EchoMem must change.
5. Use only allowlisted recovery: retry, dependency rebuild and retry, or restart
   the unchanged EchoMem container.
6. Never edit, patch, commit, or silently work around the tested EchoMem source.

Read `references/failure-categories.md` for evidence requirements and recovery limits.
