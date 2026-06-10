# Current Agent Context Alignment And LoCoMo Memory Probe

Generated: 2026-06-01

## Verdict

The current batch QA agent is close to a one-shot VikingBot-style RAG harness, but it is not fully equivalent to VikingBot/VikingBoat.

Aligned:

- Uses OpenViking `/api/v1/search/find` before answering.
- Uses `top_k=30` and `score_threshold=0.1`, matching VikingBot's LoCoMo memory context defaults.
- Injects LoCoMo `Current date: ...` when `query_time` exists.
- Batch QA enriches search hits by reading local memory files when `--workspace` is supplied.
- LoCoMo import uses one OpenViking session per LoCoMo session, preserves `role_id` speakers, assigns created_at timestamps, then calls `commit_session`.

Not fully aligned:

- VikingBot is a real agent loop and can call `openviking_search` repeatedly; current batch QA does one search, then one answer call.
- VikingBot context separates `user_memory` and `agent_memory`; current batch QA only uses user memory search.
- VikingBot loads current time, session metadata, user profile, and group-chat memory users; current chat workbench only has a smaller static charter plus recent browser turns.
- Chat workbench uses API abstracts only; batch QA reads full files, so chat and batch can see different evidence.
- Current imports usually write all LoCoMo memories under `user/default`; isolation depends on a fresh workspace/account, not per-speaker user spaces.

## Key Code Paths

- Batch QA context: `/Users/chx/locomo-eval-web/scripts/openviking_memory_qa.py`
- Web chat context preview: `/Users/chx/locomo-eval-web/server.py` (`build_agent_context_preview`)
- LoCoMo OpenViking import: `/Users/chx/locomo-eval-web/scripts/openviking_locomo_import.py`
- VikingBot reference context: `/Users/chx/openviking-src-latest-2026-05-08/bot/vikingbot/agent/context.py`
- VikingBot memory parser: `/Users/chx/openviking-src-latest-2026-05-08/bot/vikingbot/agent/memory.py`

## Probe Suite

Executable probe:

```bash
python3 /Users/chx/locomo-eval-web/scripts/locomo_memory_probe.py \
  --dataset dataset/locomo10.json \
  --sample conv-30 \
  --workspace /Users/chx/openviking_workspace_locomo_20260601_014238_5b2c49 \
  --openviking-url http://127.0.0.1:1933 \
  --account default \
  --user-id default \
  --agent-id default \
  --top-k 30
```

The probe does not call the answer LLM. It checks storage and retrieval only.

Test cases:

- Storage completeness: dataset message count equals archived OpenViking messages.
- Commit integrity: import summary is complete and pending messages are zero.
- Role preservation: archived messages preserve `role_id=Jon/Gina`.
- Memory files: `workspace/viking/<account>/user/<user>/memories` exists and contains `.md` memory files.
- `conv-30_qa0`: date fact, Jon lost banker job.
- `conv-30_qa5`: ideal dance studio, requires water + natural light + Marley flooring.
- `conv-30_qa29`: multi-hop visited cities, Paris + Rome.
- `conv-30_qa31`: temporal reasoning from job loss to opening.
- `conv-30_qa39/40`: speaker-specific preference, Gina/Jon contemporary dance.
- `conv-30_qa46`: fine detail, Marley flooring.
- `conv-30_qa78`: regression from previous wrong answer, positivity + determination.

## Current Probe Result

Latest report:

- JSON: `/Users/chx/locomo-eval-web/runs/locomo_memory_probe_20260601_172736/locomo_memory_probe.json`
- Markdown: `/Users/chx/locomo-eval-web/runs/locomo_memory_probe_20260601_172736/locomo_memory_probe.md`

Storage is healthy:

- `expected_messages=369`
- `archived_messages=369`
- `memory_files=46`
- `role_id preserved=True`
- `possible_cross_sample_pollution=False`

Retrieval issues found:

- `conv-30_qa5` fails: search misses `water`; API abstract also misses `Marley`.
- `conv-30_qa46` is `WARN_FILE_ONLY`: search hits the right file, but API abstract misses `Marley`; batch QA can recover by reading the file, chat workbench may miss it.
- `conv-30_qa78` fails: search misses `positivity`, and the gold evidence is not extracted into memory files.

## 2026-06-01 Follow-up Fix

Implemented after the first probe:

- Web chat context now passes `workspace` to `/api/agent/context`.
- Web chat top hits are enriched from local OpenViking memory files when the URI can be resolved.
- Relevant Memory cards now prefer full memory file content and show `content_source=memory_file`.
- Query expansion was added for LoCoMo-style questions, including ideal studio, flooring, mentor/guide, and visited-city patterns.
- Batch QA now uses the same query expansion and writes `retrieval_query_plan` plus `context_preview`.
- Import integrity now includes `LoCoMo Evidence Probe` with statuses:
  - `PASS`: exact gold evidence is in memory.
  - `PARTIAL`: some exact evidence is in memory, but not all.
  - `FACT ONLY`: expected fact words appear in memory, but exact evidence snippets are not present.
  - `ARCHIVE ONLY`: evidence is in archived session history but not in long-term memory.
  - `MISSING`: evidence is absent from both memory and archive checks.

Current API result for `conv-30`:

- `pass=4`
- `partial=1`
- `fact_only=2`
- `archive_only=1`
- `missing=0`

Interpretation:

- Storage/commit path is still healthy.
- `conv-30_qa46` is fixed for context: `Marley` is now present in both Web chat and batch QA context.
- `conv-30_qa5` remains `PARTIAL`: `water` is still not represented by exact gold evidence in memory, even though related facts exist.
- `conv-30_qa78` remains `ARCHIVE ONLY`: the raw archive contains `positivity and determination`, but long-term memory extraction did not preserve `positivity`.

## Fix Direction

- Make chat context read full memory files for top hits, the same way batch QA does.
- Add query expansion for structured LoCoMo questions, especially attribute lists like `ideal dance studio water natural light Marley flooring`.
- Add a gold-evidence extraction check after commit: for every selected QA, verify evidence snippets are present in memory files or at least in archived session history.
- Keep using fresh workspace/account per LoCoMo sample, or add sample-scoped user ids, to prevent cross-conv retrieval pollution.
- Add a second-pass retrieval path for failed probes: exact speaker/event search using evidence terms, then merge those hits into answer context.
