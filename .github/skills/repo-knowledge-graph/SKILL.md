---
name: repo-knowledge-graph
description: 'Builds and maintains a repository interaction map for ResistanceProfiler. Use when module boundaries change, new cross-module call paths are introduced, or development architecture docs risk drifting from code. Produces and updates docs/development/detailed_respro_layout.md and docs/development/detailed_app_layout.md.'
argument-hint: 'Scope (respro, web, or full repo), thoroughness (quick/medium/deep), and whether to refresh docs or review drift only.'
user-invocable: true
disable-model-invocation: false
---

# Repo Knowledge Graph

## Overview

This skill maintains a practical architecture knowledge graph for ResistanceProfiler. It is for module interaction mapping and function-level flow tracing in change-critical areas, not for generating full static call graphs on every task.

Primary outputs:

- `docs/development/detailed_respro_layout.md`
- `docs/development/detailed_app_layout.md`

## When to Use

Use this skill when:

- A change introduces or removes module boundaries
- A workflow crosses `respro/io`, `respro/core`, `respro/db`, `respro/report`, or `respro/cli` in new ways
- Web API/service/job flow changes in `web/backend/` or frontend-to-backend contracts change
- Queue/job/runtime wiring changes (API -> RQ -> worker -> core/db/report)
- Architecture docs are likely to drift after a feature

Do NOT use this skill when:

- Change is local and internal to one module with unchanged interface
- Task is a small bug fix, formatting-only, or tests-only
- Docs-only wording updates with no behavior or architecture change

## Noise-Control Rule

Run this skill selectively.

- Trigger on structural changes, not routine edits.
- Prefer `quick` mode during implementation and `deep` mode before merge for large changes.
- Update only affected sections in the target docs; do not rewrite both files by default.

## Inputs and Modes

Input fields to collect:

- Scope: `respro`, `web`, or `full-repo`
- Objective: `drift-check` or `refresh-docs`
- Thoroughness: `quick`, `medium`, `deep`

Mode expectations:

- `quick`: high-level module edges and main entry flows only
- `medium`: module edges + key function chains for changed paths
- `deep`: expanded function chains, risk notes, and unresolved assumptions

## Procedure

1. Establish changed surface:
   - inspect changed files first
   - classify by layer (cli/io/core/db/report/web)
2. Build module interaction edges:
   - direct imports/calls where relevant
   - runtime orchestration edges (CLI command dispatch, API route -> queue -> job)
3. Trace key function chains for changed behavior:
   - include only meaningful execution paths
   - avoid huge low-value enumerations
4. Identify architecture drift:
   - compare discovered edges with existing development docs
   - flag outdated statements or missing boundaries
5. Update docs selectively:
   - update only impacted sections in `detailed_respro_layout.md` and/or `detailed_app_layout.md`
   - preserve stable terminology and module responsibilities
6. Report confidence and unknowns:
   - list assumptions and unverified dynamic paths

## Required Output

When running this skill, produce:

1. Interaction summary
   - changed modules
   - new/removed edges
2. Key flow chains
   - concise bullet list of function-level paths in scope
3. Doc impact
   - which file(s) were updated
   - which sections changed
4. Residual uncertainty
   - dynamic/runtime edges not fully verified

## Graph Style Guidance

Use mermaid diagrams for maintainable readability:

- One module-level graph per section
- Optional focused sequence/flow graph for critical pipelines
- Keep graph nodes stable over time to reduce churn

Prefer concise graphs over exhaustive graphs.

## Acceptance Checklist

- [ ] Scope and mode selected explicitly
- [ ] Only structural/interaction-relevant paths documented
- [ ] No stale architecture statements remain in touched sections
- [ ] Updated docs reflect current module boundaries and key function chains
- [ ] Unknowns or assumptions are listed explicitly

## Anti-Patterns

- Generating full call graphs for every PR
- Rewriting entire docs for small local changes
- Treating speculative flows as facts
- Duplicating content already covered in `contribution-and-architecture.md`
- Mandating this skill for all agents on all tasks
