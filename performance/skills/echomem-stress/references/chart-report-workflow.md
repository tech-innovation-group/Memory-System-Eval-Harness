# Portable chart-report workflow

## 1. Let any coding assistant execute the same implementation

Ask the assistant to read `performance/skills/echomem-stress/SKILL.md` and its
referenced files from the checked-out harness. This works without automatic skill
installation. Use the assistant's normal terminal and file tools; Codex browser,
MCP, SSH, and a particular home directory are not prerequisites. Without shell
access, return the commands and state that they were not executed.

The assistant (for example Kimi or Codex) orchestrates the test. The real model
called by EchoMem is separately configured in the service. Do not replace that
model, use a fake provider, or change its credentials merely to generate charts.

Use this example request:

> Read performance/skills/echomem-stress/SKILL.md in this repository. Test my
> local EchoMem using the selected profile and M1-M3 scope. Show the command
> before starting, keep failures in the denominator, and generate report.html
> with the repository renderer. Put the conclusion first, use charts and exact
> values, explain the test method and module-level problems, and fold technical
> evidence. Do not use a remote server or invent missing measurements.

For existing results, instead ask:

> Only regenerate the chart report from RESULT_DIRECTORY; do not rerun tests or
> call models. Choose the renderer that matches the persisted evidence. Compare
> with BASELINE_DIRECTORY only if supplied, and explain configuration differences.

## 2. Resolve paths and scope before running

Work from the repository root. Use its configured Python environment, usually
`.venv/bin/python`; do not embed another developer's absolute paths. Record the
Git revision and verify the selected module's `--help` before execution. If the
renderer is absent, request a revision containing it; do not silently switch
branches or recreate the HTML yourself.

Follow `performance/targets/echomem/README.md` for local service setup, profile,
and secret env-file preparation. Use actual local resources, not assumed 4U8G.
Never copy API keys or tenant credentials into a report or distributable archive.

For M1-M3, after preparation:

```bash
.venv/bin/python -m performance.targets.echomem.observation_run --help
.venv/bin/python -m performance.targets.echomem.observation_run \
  --profiles "$PROFILE" --env-file "$ENV_FILE" \
  --metrics M1,M2,M3 --out-dir "$OUTPUT"
```

PROFILE, ENV_FILE, and OUTPUT must be resolved to the user's actual paths first.
The canonical output is `$OUTPUT/report.html`. Use the six-metric display contract
in `interactive-workflow.md` for its charts. Do not pass six-metric evidence into
the bounded Commit renderer. Do not use `--resume` for report-only requests: it
may execute unfinished workloads.

## 3. Reproduce the Commit diagnostic chart report

This is the renderer used for the 16/64 concurrency comparison, not a full M1-M6
report. It reads existing data and makes no model calls:

```bash
.venv/bin/python -m scripts.build_commit_diagnostic_report --help
.venv/bin/python -m scripts.build_commit_diagnostic_report \
  --root "$OUTPUT" --out "$OUTPUT/report.html"
```

Optional comparison with an earlier diagnostic run:

```bash
.venv/bin/python -m scripts.build_commit_diagnostic_report \
  --root "$OUTPUT" --compare-root "$BASELINE" --out "$OUTPUT/report.html"
```

Each root must contain exactly one `topology-N` directory. Required measured
inputs are `diagnostic.json` and `concurrency-topology.json`; also preserve
`model-preflight.json`, `service-diagnostics.json`, `deployment-parameters.json`,
`container-evidence.json`, `manifest.json`, `execution.json`, and `resources.json`
when collected. Missing evidence is missing, not zero. A blocked preflight cannot
produce measured concurrency charts. Do not fabricate files to satisfy the schema.

The current bounded diagnostic is a specific four-user heterogeneous workload,
not a universal deployment command. `scripts/run_commit_diagnostic.py` exposes a
Python function and is not a standalone CLI. Do not pretend that invoking that
file starts a test. Its report currently contains scenario-specific resource,
timeout, and cleanup wording: verify against the new manifest before delivery.
If those facts differ, fix the renderer to consume measured metadata first;
never claim that another user's machine was the original 4U8G server.

## 4. Required reading order and chart semantics

1. Overall conclusion: completed, waiting, rejected, degraded, or unverified;
   actual concurrency, request counts, and measured duration.
2. Commit outcome chart: completed / server-failed / observation-timeout /
   unknown. Show HTTP202 acceptance separately. Call observations and unique
   tasks have different denominators; show both when available.
3. Search quality chart: healthy hit / degraded hit / miss / missing evidence.
   HTTP200 is not sufficient. Separate transport and provider errors where known.
4. Comparison bars: completion fraction, Commit operation P95, Search P95,
   healthy-hit fraction. Display units and exact values; label each scale.
   Show differences in model, input length, resources, configuration, duration,
   and workload size. Do not attribute a changed result solely to concurrency
   when other variables changed.
5. Plain-language explanation by module: queue/admission, recall/routing,
   memory engine, external provider, or evidence collector. Distinguish observed
   errors from hypotheses. No quota error observed is not proof of unlimited quota.
6. Test method and folded technical evidence. Explain users, sessions,
   per-session concurrency, Commit text size, Search question/fact setup, and
   timeout window. Explain that P95 is not the average or pure model duration.

Charts must retain exact tables/legends and textual status, not color alone.
Grouped cells are not a timeline. Do not derive internal module time by subtracting
end-to-end measurements. Do not hardcode previous run values into new reports.

## 5. Verify and deliver

Confirm report.html exists, is newly generated, and matches the input run and
counts. Inspect desktop and mobile widths when browser tooling is available:
no horizontal overflow, readable chart labels, functioning evidence disclosures.
Without browser tooling, say visual verification was not performed. Do not make
it a reason to omit the generated artifact.

For changes to the bounded renderer, run:

```bash
.venv/bin/python -m pytest tests/test_commit_diagnostic_dashboard.py \
  tests/test_commit_failure_evidence.py tests/test_scoped_probe_report.py -q
```

Return the absolute HTML path (a clickable link when supported), two or three
measured findings, and any gaps. Static HTML needs no development server. A test
failure still gets a truthful report; missing charts backed by available data
are `REPORT_CONTRACT_GAP`, not permission to invent results or write ad-hoc HTML.
