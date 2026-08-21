# v2 Source Provenance

The migration uses three different evidence classes. They must not be mixed.

## v2 Tracked Head

- Repository: `/Users/chx/Code/memory-benchmark-workbench`
- Branch: `v2`
- Audited commit: `a146a246c2fcce128229d19e05c87228affd829d`

Tracked files at this commit are the authority for migrated v2 CLI, dataset,
metric, report, and recovery behavior. Experimental v2 QA profiles are not
registered in the current CLI.

## VikingBoat v0.4.11 Profiles

The default `vikingboat0411` profile follows the prompt, question envelope,
tool protocol, and iterative loop from OpenViking v0.4.11 while replacing
OpenViking memory operations with read-only EchoMemory `memory_*` tools.

`vikingboat0411-natural-no-tools` keeps the same initial retrieval basis but
injects only complete memory excerpts and exposes no tool schema.

## Uncommitted v2 Worktree

The v2 checkout contains many modified and untracked files, including dated
experiments and later local utilities. They are not treated as committed v2
behavior merely because they exist on disk.

An uncommitted file is migrated only when:

1. it implements a reusable CLI capability that is still required;
2. its behavior is independently inspected and tested; and
3. its destination follows `benchmarks/<dataset>/`, `plugins/<agent>/`,
   `backends/<backend>/`, or a genuinely backend-neutral `shared/` boundary.

OpenViking integration, web UI code, workspace inspectors, and dated
experiment-specific scripts remain excluded.
