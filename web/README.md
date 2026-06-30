# Web Package

This package is the home for the frontend application. The live UI is served
from `web/static/index.html`, `web/static/app-state.js`, `web/static/app-core.js`,
`web/static/app-format.js`, `web/static/app.js`, and `web/static/styles.css`.
The top-level `static/` directory is kept as a legacy backup during migration.

## User-Facing Sections

- Top account bar: create, switch, and delete local accounts. Each account
  keeps its own memory backend, workspace, and model settings in browser-local
  state and the backend account store.
- Sidebar:
  - 对话 - 人工评测
  - locomo评测
  - longmemeval评测
  - evolvingevents评测
  - hotpotqa评测
  - proagentbench评测
  - Tau2-bench评测
  - chenmo评测
  - 系统配置
- README view: lists the configuration another tester must provide before
  running LoCoMo evaluation with OpenViking or EchoMemory.

## External Tester Configuration Checklist

Do not put real API keys in this README, screenshots, reports, or committed
files. Keys are entered in the web UI at runtime.

1. Project runtime
   - Local project path for `locomo-eval-web`.
   - Python 3.9+.
   - Start command: `python3 server.py --host 127.0.0.1 --port 19181`.
   - Browser URL: `<WEB_BASE_URL>/`.
2. Dataset
   - LoCoMo JSON path on the tester machine.
   - Dataset validation should show conversation count, QA count, and category
     distribution even when the file is not the 10-conversation sample.
3. Memory backend
   - Current delivery only requires OpenViking or EchoMemory. No other
     backend is part of the current handoff.
   - OpenViking service URL, usually `http://127.0.0.1:<port>`.
   - EchoMemory SDK root when using EchoMemory; it must contain
     `packages/echomem/src` and `packages/echofs/src`.
   - Workspace path. Prefer a new clean workspace per experiment.
   - Account name. Account is the current isolation boundary.
4. Models
   - Answer model base URL, model name, and API key.
   - Judge model base URL, model name, and API key.
   - Embedding config if the selected memory backend requires it.
5. Outputs
   - Harness outputs live under `runs/`.
   - OpenViking memory files live under `workspace/viking/<account>/`.
   - EchoMemory memory files live under the configured EchoMemory workspace/account.
   - Reports should include config snapshot, runtime, model names, scores,
     token usage, context composition, evidence, and run paths.

## Memory Backend Contract Direction

The frontend talks to memory engines through server APIs backed by the public
`memory/plugins` contract. `memory/adapters` remains a compatibility bridge
over the plugin registry for existing API names.

- `memory/plugins/openviking`: OpenViking commit/import/search/report plugin.
- `memory/plugins/echomemory`: EchoMemory local SDK plugin for LoCoMo import, retrieval QA, and evidence reporting.

The older internal compatibility package is not part of the external handoff
surface. Current delivery remains OpenViking + EchoMemory only.

Future work should split the large live files into smaller view modules under
this package while keeping the server static root pointed at `web/static`.
