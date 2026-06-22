# EchoMemory v0.0.5 + LoCoMo conv-30 + GPT-5.5 Status (2026-06-09)

## What is verified

- Web app: `<WEB_BASE_URL>/`
- EchoMemory source version: `version_0.0.5`
- EchoMemory repo path: `<echomem-root>`
- EchoMemory account wired into the web UI:
  - account: `echomemory-v005-gpt55-conv30-full-20260608_214437`
  - workspace: `<echomem-workspace>`
- EchoMemory connection contract:
  - `curl -s <WEB_BASE_URL>/api/echomem-contract`
  - status: `ok`

## Current run evidence

### 1. v0.0.5 smoke QA is valid

- Run dir: `<repo-root>/runs/echomemory_v005_gpt55_conv30_qa_smoke_20260609_010625`
- QA summary: `<repo-root>/runs/echomemory_v005_gpt55_conv30_qa_smoke_20260609_010625/echomemory_qa/summary.json`
- Judge summary: `<repo-root>/runs/echomemory_v005_gpt55_conv30_qa_smoke_20260609_010625/echomemory_qa/judge_summary.json`
- HTML report: `<repo-root>/runs/echomemory_v005_gpt55_conv30_qa_smoke_20260609_010625/report.html`

Result:

- selected questions: `5`
- graded: `5`
- correct: `4`
- wrong: `1`
- accuracy: `80.0%`

### 2. v0.0.5 full conv-30 QA did not finish cleanly

- Run dir: `<repo-root>/runs/echomemory_v005_gpt55_conv30_fullqa_20260609_010957`
- QA summary: `<repo-root>/runs/echomemory_v005_gpt55_conv30_fullqa_20260609_010957/echomemory_qa/summary.json`
- Log: `<repo-root>/runs/echomemory_v005_gpt55_conv30_fullqa_20260609_010957/run.log`

Observed behavior:

- retrieval rows written: `81`
- answer model failures: `70`
- failure reason in log: `403 daily_points_exhausted`
- provider message says refresh time is `2026-06-10 00:05`

This run should not be treated as a valid full conv-30 score.

### 3. v0.0.5 import is only partially healthy

- Import summary: `<repo-root>/runs/echomemory_import_20260608_214437_303732/echomemory_import/echomemory_import_summary.json`
- Live integrity endpoint:
  - `curl -s '<WEB_BASE_URL>/api/memory-import-integrity?backend=echomemory&workspace=%2FUsers%2Fchx%2Fechomem_workspace_v005_gpt55_conv30_full_20260608_214437&account=echomemory-v005-gpt55-conv30-full-20260608_214437&sample=conv-30&user_id=default'`

Current integrity facts:

- submitted messages: `369/369`
- sessions: `19`
- account artifact files: `6140`
- commit incomplete: `3`
- atom flush incomplete: `19`
- integrity status: `incomplete`

Interpretation:

- the workspace is usable enough for smoke retrieval and QA
- this is not yet a clean formal import state

## Web behavior fixed on 2026-06-09

Before this pass:

- `echomemory_qa`, `openviking_qa`, and `judge` could start even when the current GPT-5.5 endpoint was already exhausted
- that could waste time and produce misleading empty or partial result files

After this pass:

- `server.py` now fail-fasts before task creation for:
  - `echomemory_qa`
  - `openviking_qa`
  - `openviking_generic_qa`
  - `openviking_qa_retry_failed`
  - `openviking_qa_retry_missing`
  - `judge`

Expected error shape:

- answer model preflight:
  - `答案模型预检失败：...`
- judge preflight:
  - `Judge预检失败：...`

Verified locally against `<WEB_BASE_URL>/api/tasks`:

- `echomemory_qa` now returns preflight failure immediately
- `openviking_qa` now returns preflight failure immediately
- `judge` now returns preflight failure immediately
- `/api/tasks` remains empty after those failed requests

## Custom agent integration plan

Reference implementation already exists in the plugin boundary:

- plugin contract: `<repo-root>/memory/plugins/contract.py`
- OpenViking plugin: `<repo-root>/memory/plugins/openviking/plugin.py`
- EchoMemory plugin: `<repo-root>/memory/plugins/echomemory/plugin.py`
- OpenViking agent workbench: `<repo-root>/memory/plugins/openviking/agent.py`
- EchoMemory agent workbench: `<repo-root>/memory/plugins/echomemory/agent.py`

Recommended shape for a current custom agent:

1. Keep the web/UI contract unchanged.
2. Expose the backend through the same plugin boundary as OpenViking.
3. Implement these two interactive methods:
   - `agent_chat(payload, defaults, config_path)`
   - `archive_chat(payload, defaults, output_dir)`
4. Keep formal LoCoMo scoring on the task builder path:
   - `build_locomo_import_task(...)`
   - `build_locomo_qa_task(...)`
5. Keep formal QA in VikingBoat-comparable mode:
   - `top_k = 30`
   - tool loop on
   - no raw transcript fallback
   - no hidden backend-only prompt branch

Practical split:

- `agent_chat`
  - readonly retrieval
  - builds context trace
  - calls the answer LLM
  - does not write memory
- `archive_chat`
  - only runs on explicit user save/archive action
  - performs `create_session / add_message / commit_session`
- `build_locomo_qa_task`
  - remains the formal benchmark path
  - uses the same comparable parameters as the OpenViking-aligned runs

This keeps interactive debugging and formal benchmark scoring separate, which matches the current OpenViking pattern.

## What is still needed for completion

1. A working GPT-5.5 token or compatible endpoint for Agent and Judge.
2. A clean full `conv-30` QA run with real answers across all `81` questions.
3. A full Judge pass on that run.
4. Preferably a repaired or fully clean EchoMemory import state for the v0.0.5 workspace.
