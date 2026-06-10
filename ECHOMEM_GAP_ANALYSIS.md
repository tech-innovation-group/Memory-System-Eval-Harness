# EchoMemory v0.0.5 Integration Matrix

This file tracks the current EchoMemory integration after the platform was moved to the official `version_0.0.5` release.

## Scope

- Required EchoMemory source: `version_0.0.5`
- Default source path: `/Users/chx/Code/echomemory/echo_memory`
- Current project: `locomo-eval-web`
- Goal: keep OpenViking as the baseline backend, and use EchoMemory `version_0.0.5` as the only EchoMemory integration path.

## Current Harness Status

1. LoCoMo dataset loading and per-conversation question listing
2. EchoMemory import through local SDK `create_session` / `add_message` / `commit_session`
3. Import progress, logs, completion verification, run manifests
4. EchoMemory source version check in `系统配置`
5. Read-only agent chat panel with backend-specific context display
6. Relevant memory display and context trace
7. QA selection by exact question IDs
8. Judge stage with pending-vs-accuracy handling
9. Imported-memory scan for current workspace/account

## Version Guardrails

1. `server.py` reports the detected EchoMemory root, tag and commit.
2. The UI requires `version_0.0.5`; old `develop` or `version_0.0.4` source is treated as not comparable.
3. Handoff docs use:

```bash
git clone -b version_0.0.5 https://github.com/tech-innovation-group/echo_memory.git /absolute/path/to/echo_memory
```

## Remaining Work

1. Re-run non-LoCoMo EchoMemory baselines with `version_0.0.5` before showing them as current results.
2. Improve EchoMemory import integrity diagnostics around async atom extraction / vector flush.
3. Align EchoMemory Agent chat with the same tool-loop shape used by LoCoMo QA.
4. Keep old experimental runs as history only; do not use them as current baselines.
