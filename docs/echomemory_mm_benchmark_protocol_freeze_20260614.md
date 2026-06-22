# EchoMemory-MM Benchmark Protocol Freeze

Date: 2026-06-14

## Purpose

The current EchoMemory-MM package already has:

1. real code structure
2. a 30-paper research map
3. nano implementations
4. mechanism-level experiments
5. paper drafts

What it still lacked was one explicit statement of:

1. which execution entrypoint counts as the benchmark protocol
2. which defaults are authoritative
3. which knobs are frozen for paper-facing runs
4. what counts as "memory ready" before QA starts

This file is that freeze.

It is intentionally narrow:

- it does not claim the benchmark is complete
- it does not invent a new runner
- it freezes the current repository's real execution path into one paper-facing protocol

---

## 1. Scope

This protocol freeze is for **EchoMemory benchmark runs** that aim to support the EchoMemory-MM paper direction.

Primary target datasets:

- LoCoMo
- LongMemEval

Primary target question families:

- temporal
- relation-heavy
- mixed multi-evidence
- readiness-sensitive

This freeze does **not** yet define a final multimodal benchmark protocol.
Multimodal evidence remains a paper direction with early code support, not a fully frozen benchmark track.

---

## 2. Authoritative execution path

### 2.1 Import path

Authoritative import entry:

- `/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py`

This is the write-path protocol source for LoCoMo-style import.

Important implication:

- import status is **not** just "messages were written"
- import success must be judged through the integrity and async-memory checks emitted by this path

### 2.2 QA + judge orchestration path

Authoritative benchmark orchestration entry:

- `/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py`

This file is the correct paper-facing entrypoint because it does all of the following:

1. waits for import summary status
2. applies an explicit settle window
3. checks async memory stabilization
4. optionally repairs sessions before QA
5. blocks QA if memory is still not ready
6. launches QA with pinned arguments
7. launches judge after QA

### 2.3 Why not treat `echomemory_memory_qa.py` defaults as the paper protocol

The direct QA runner:

- `/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py`

is still essential, but its raw parser defaults are **not sufficient** as the paper protocol by themselves.

Reason:

- the platform and orchestration layers may pass different values than the bare script defaults
- readiness-sensitive evaluation should use the orchestration path, not direct QA invocation alone

Therefore the benchmark protocol is defined as:

> import via `echomemory_locomo_import.py`, then evaluate via `echomemory_wait_and_eval.py`, which in turn calls `echomemory_memory_qa.py` and judge with pinned settings.

---

## 3. Frozen paper-facing settings

These settings are frozen for the current EchoMemory-MM paper path unless a later protocol version explicitly changes them.

### 3.1 Import-stage settings

Authoritative source:

- `echomemory_locomo_import.py`

Frozen defaults for paper-facing full import runs:

- `session_mode = "locomo"`
- `import_wait_mode = "full"`
- `commit_wait_s = 300`
- `commit_call_timeout_s = 300`
- `flush_call_timeout_s = 600`
- `flush_attempts = 3`

Interpretation:

- the paper protocol does **not** use fast import as the default benchmark path
- fast/deferred import may still be useful for engineering experiments, but not as the frozen benchmark protocol

### 3.2 Stabilization / readiness wait before QA

Authoritative source:

- `echomemory_wait_and_eval.py`

Frozen defaults:

- `settle_seconds = 180`
- `stabilize_timeout_seconds = 300`
- `stability_polls = 3`
- `poll_seconds = 30`
- `repair_before_qa = true`
- `repair_flush_call_timeout_s = 600`
- `repair_flush_attempts = 2`
- `repair_commit_wait_s = 300`

Interpretation:

- QA must not begin immediately after import completion
- the system is given a post-import settle window
- if readiness is still weak, repair is attempted before QA
- if memory is still not ready after stabilization and repair, QA is blocked

### 3.3 Answer model / judge model

Frozen current paper-facing defaults in orchestration:

- `answer_base_url = https://dashscope.aliyuncs.com/compatible-mode/v1`
- `judge_base_url = https://dashscope.aliyuncs.com/compatible-mode/v1`
- `answer_model = deepseek-v4-flash`
- `judge_model = deepseek-v4-flash`

Important note:

- these are the current real defaults in `echomemory_wait_and_eval.py`
- they can be replaced later by a new protocol version
- but a paper-facing run must record both answer model and judge model explicitly

### 3.4 Retrieval and prompting settings

Frozen current paper-facing defaults in orchestration:

- `prompt_mode = "vikingboat_lite"`
- `top_k = 30`
- `score_threshold = 0.1`
- `memory_budget_chars = 6000`
- `user_memory_budget_chars = 4000`
- `agent_memory_budget_chars = 2000`
- `retrieval_mode = "search"`
- `retrieval_ranker = "score"`
- `tool_set = "search_read"`
- `tool_search_limit = 20`
- `tool_min_score = 0.35`
- `tool_log_chars = 1200`
- `prefetch_read_count = 4`
- `prefetch_context_chars = 5000`
- `max_iterations = 8`
- `vikingboat_tool_loop = true`
- `vikingboat_compat = false`
- `initial_tool_prefetch = true`
- `fallback_to_one_shot = true`

### 3.5 QA timeout settings

Frozen current paper-facing QA timing:

- `timeout_s = 180`
- `question_timeout_s = 300`
- `model_retries = 5`

Interpretation:

- benchmark runs must record both per-call and per-question timeouts
- timeouts are part of the protocol, not accidental runtime noise

---

## 4. VikingBoat alignment constants that must remain visible

Authoritative source:

- `/Users/chx/locomo-eval-web/memory/vikingboat_alignment.py`

Frozen alignment constants:

- `VIKINGBOT_ALIGNMENT_PROFILE = "vikingboat_context_v1"`
- `VIKINGBOT_INITIAL_SEARCH_LIMIT = 30`
- `VIKINGBOT_INITIAL_MIN_SCORE = 0.1`
- `VIKINGBOT_USER_MEMORY_BUDGET_CHARS = 4000`
- `VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS = 2000`
- `VIKINGBOT_TOOL_SEARCH_LIMIT = 20`
- `VIKINGBOT_TOOL_MIN_SCORE = 0.35`
- `VIKINGBOT_MAX_ITERATIONS = 50`

Important clarification:

There is a difference between:

1. the **alignment constants** that describe the VikingBoat-style retrieval design target
2. the **orchestration defaults** currently used by `echomemory_wait_and_eval.py`

For paper-facing evaluation, both must be recorded:

- the design alignment target
- the actual executed settings

This matters because, for example:

- `echomemory_wait_and_eval.py` currently uses `max_iterations = 8`
- while `vikingboat_alignment.py` defines `VIKINGBOT_MAX_ITERATIONS = 50`

That discrepancy must be made explicit instead of being hidden.

---

## 5. Memory readiness rule

This is the most important protocol rule in the whole file.

QA is allowed to start only when the orchestration path determines memory is ready enough.

In the current repository, readiness is operationalized through:

- session count
- complete session coverage
- `abstract.md` coverage
- `overview.md` coverage
- atom count > 0
- graph count > 0
- vector count > 0

Authoritative implementation:

- `build_workspace_snapshot(...)`
- `snapshot_ready(...)`
- `wait_for_async_memory_stability(...)`
- `require_memory_ready_or_exit(...)`

from:

- `/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py`

Paper interpretation:

> persisted messages are not enough; benchmark QA begins only after the memory system reaches an explicit ready state.

This is the current strongest real-system bridge from the paper's readiness claim to actual execution behavior.

---

## 6. Frozen benchmark reporting fields

Every paper-facing run should record at least these fields:

### 6.1 Import fields

- dataset
- sample / subset
- session_mode
- session_start / session_end / max_sessions
- import_wait_mode
- commit_wait_s
- commit_call_timeout_s
- flush_call_timeout_s
- flush_attempts
- settle_seconds
- stabilize_timeout_seconds
- stability_polls
- repair_before_qa
- repair_flush_call_timeout_s
- repair_flush_attempts
- repair_commit_wait_s

### 6.2 QA / judge fields

- answer_base_url
- answer_model
- judge_base_url
- judge_model
- prompt_mode
- top_k
- score_threshold
- retrieval_mode
- retrieval_ranker
- tool_set
- tool_search_limit
- tool_min_score
- prefetch_read_count
- prefetch_context_chars
- max_iterations
- timeout_s
- question_timeout_s
- model_retries

### 6.3 Memory-structure fields

- user_memory_budget_chars
- agent_memory_budget_chars
- memory_budget_chars
- vikingboat_alignment_profile
- backend route
- readiness result
- repair status

---

## 7. What counts as the canonical EchoMemory-MM benchmark protocol now

For the current paper package, the canonical protocol is:

1. import with `echomemory_locomo_import.py`
2. wait and stabilize with `echomemory_wait_and_eval.py`
3. allow repair before QA
4. block QA if readiness is still insufficient
5. run QA in `vikingboat_lite + search` mode with:
   - `top_k = 30`
   - `score_threshold = 0.1`
   - `4000 + 2000` user/agent budget split
   - `tool_search_limit = 20`
   - `tool_min_score = 0.35`
6. run judge with explicit model recording

This is the protocol future EchoMemory-MM benchmark tables should cite unless a newer protocol version supersedes it.

---

## 8. What this freeze does not solve yet

This freeze does not yet provide:

1. a final LoCoMo full benchmark table
2. a final LongMemEval full benchmark table
3. a final multimodal benchmark protocol
4. a final latency/cost appendix
5. a final cross-model robustness study

It only solves one thing:

> from now on, paper-facing EchoMemory benchmark runs can be judged against one explicit protocol rather than a drifting set of defaults.

---

## 9. Recommended next steps

1. add a machine-readable protocol config file beside this note
2. run a LoCoMo formal subset under this exact protocol
3. run a LongMemEval formal subset under this exact protocol
4. emit one benchmark table page that cites this protocol freeze directly

---

## Bottom line

The current EchoMemory-MM package was already research-serious, but the benchmark layer still lacked one frozen protocol.

This file fixes that gap:

> not by inventing a new evaluation stack, but by freezing the current repository's real orchestration path into one explicit benchmark protocol.
