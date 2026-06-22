# LoCoMo Evidence-Preserving Memory Templates

These OpenViking memory schemas override selected built-in memory types through
`memory.custom_templates_dir`.

Purpose:

- Preserve concrete LoCoMo evidence during `commit_session`.
- Avoid replacing benchmark-relevant details with broad summaries.
- Keep the system clean: no gold QA answers or labels are injected into memory.

Enable in an OpenViking runtime config:

```json
{
  "memory": {
    "custom_templates_dir": "<repo-root>/openviking_custom_memory_templates/locomo_evidence"
  }
}
```

The web harness enables this directory for new OpenViking import tasks by
default. After importing, run the built-in Evidence Probe from the UI to compare
archive completeness, long-term memory extraction, and retrieval readiness.
