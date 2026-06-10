# LoCoMo OpenViking Evidence Template Plan

Generated: 2026-06-02

## Goal

Improve OpenViking `commit_session` extraction for LoCoMo without injecting
gold QA answers into memory.

The harness now provides an optional but default-enabled memory schema override:

```text
/Users/chx/locomo-eval-web/openviking_custom_memory_templates/locomo_evidence
```

When the Web UI starts a new OpenViking import task, the generated
`openviking.runtime.conf` includes:

```json
{
  "memory": {
    "custom_templates_dir": "/Users/chx/locomo-eval-web/openviking_custom_memory_templates/locomo_evidence"
  }
}
```

This affects memory extraction only. It does not modify the LoCoMo dataset, the
gold answers, or the Judge.

## What The Templates Change

- `events.yaml`
  - Preserves dates, speaker attribution, places, materials, objects, visual
    captions, query terms, emotions, preferences, and causal links.
  - Explicitly tells extraction not to compress attribute lists like
    `by the water`, `natural light`, `Marley flooring`.

- `entities.yaml`
  - Keeps speaker-specific entity cards richer and more fine-grained.
  - Preserves recurring facts such as visited cities, business plans, school
    events, hobbies, and relationship facts.

- `preferences.yaml`
  - Captures exact preference/activity terms and shared preferences.
  - Helps cases such as `How do Jon and Gina both like to destress?`.

## Regression Test

Use a fresh workspace for every comparison run.

1. Import `conv-30` through the Web UI.
2. Run "检查完整性".
3. Compare Evidence Probe counts.

Current baseline before template re-import:

```text
pass=4
partial=1
fact_only=2
archive_only=1
missing=0
```

Target after template re-import:

```text
pass should increase
archive_only should become 0
partial/fact_only should decrease
missing must stay 0
```

High-risk questions to inspect:

- `conv-30_qa5`: ideal studio should preserve water, natural light, Marley.
- `conv-30_qa39`: Gina + Contemporary.
- `conv-30_qa40`: Jon + Contemporary.
- `conv-30_qa78`: positivity and determination should enter long-term memory.

## External Graph Memory Integration

External testers may modify OpenViking to add a graph memory module. The harness
contract is:

- Import path still uses `add_message` + `commit_session`.
- QA path can still retrieve memory through OpenViking search/find or an
  equivalent evidence-returning endpoint.
- Result rows should keep `relevant_memory`, `context_preview`, token usage,
  and Judge fields.
- Evidence Probe should still be run after import to separate storage,
  extraction, and retrieval bugs.

The graph module should improve evidence completeness, not bypass the benchmark
by reading LoCoMo gold answers.
