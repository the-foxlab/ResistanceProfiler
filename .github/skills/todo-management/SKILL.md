---
name: todo-management
description: 'Manage docs/development/to-do.md in ResistanceProfiler. Use when adding new tasks, marking work done, checking whether a feature is already implemented, drafting new todo items from a plan, selecting next priorities, or keeping the planning file accurate after a change.'
argument-hint: 'What to add, update, check, or verify in docs/development/to-do.md.'
user-invocable: true
disable-model-invocation: false
---

# Todo Management

## Planning Source of Truth

`docs/development/to-do.md` is the single planning source of truth. Always read it before any substantial change.

## Structure

The file has three sections:

- **Done** — completed work, grouped by theme, marked `[x]`. Never remove entries; they serve as project history.
- **Next** — open work, grouped by theme, each item prefixed with a priority emoji:
  - 🔴 high — tackle first; blockers or foundational work
  - 🟡 medium — important but not urgent
  - 🟢 low — nice-to-have or dependent on higher-priority work
- **Deferred** (subsection of Next) — explicitly postponed items; keep them visible but do not pick them up unless requested.

## How to Select Work

1. Prefer 🔴 items in **Next** unless the user specifies otherwise.
2. When a group of items is marked "introduce together", implement them in one change.
3. If user instructions conflict with to-do priorities, follow the user and update the to-do afterwards.
4. If only 🟡 items are present, reevaluate the priorities for all tasks.

## How to Mark Work Done

When a feature is fully implemented:

1. Move its entry from **Next** to the matching theme group in **Done**, or create a new group if none fits.
2. Change `- 🔴/🟡/🟢` to `- [x]` and drop the priority emoji.
3. Keep the description concise — one line that summarises what was built.
4. Update the to-do in the same change as the implementation.

## How to Add New Items

1. Choose the correct theme group in **Next**, or add a new group if none fits.
2. Assign a priority emoji based on urgency and dependency order.
3. Write a single-sentence description that includes the affected module(s) and the observable behaviour change.
4. If the item is intentionally deferred, place it in the **Deferred** subsection with a brief rationale.

## How to Check if a Task is Already Done

1. Read the **Done** section and search for matching keywords, module names, or feature descriptions.
2. If a close match is found in **Done**, verify by reading the relevant source files or tests.
3. If verification confirms the feature exists, report it as already done and do not re-add it.
4. If the **Done** entry exists but source code evidence is missing, flag the discrepancy.

## How to Draft New Todo Items from a Plan

1. Read the plan or user request and decompose it into observable behaviour changes.
2. Check **Done** for each proposed item — skip anything already completed.
3. Check **Next** for exact or near-duplicate items — propose an update rather than a duplicate.
4. Assign priority based on dependencies, blocking risk, and user priority signals.
5. Write each item as a single sentence: affected module(s) + observable behaviour change.
6. Group related items under one theme heading rather than creating isolated entries.
7. Propose the additions for confirmation before editing `docs/development/to-do.md` unless explicitly told to write immediately.

## What Not to Do

- Do not remove **Done** entries — they are project history.
- Do not invent a "Now" section; the priority emoji replaces it.
- Do not leave the to-do stale after a change.
- Do not add duplicate items — check both **Done** and **Next** first.
- Do not write multi-sentence todo items; each item must be implementable from one sentence.
- Do not implement anything — only manage the to-do.

## Output Format

When proposing new todo items, output a preview block showing exactly how the entries will appear in `docs/development/to-do.md` before writing the file. For example:

```
### My Theme

- 🔴 Add `foo` parameter to `respro/core/bar.py` so that X is possible.
- 🟡 Extend `respro/db/results.py` to persist Y when Z occurs.
```

When marking items done, show the moved entry with its `[x]` prefix and the target **Done** group.
