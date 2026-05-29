---
name: improve-codebase-architecture
description: 'Find deepening opportunities in this repository by identifying shallow modules, weak seams, and tightly coupled flows. Use when improving architecture, testability, and long-term maintainability.'
argument-hint: 'Scope to analyze, known pain points, and whether to deliver candidates only or a prioritized recommendation.'
user-invocable: true
disable-model-invocation: false
---

# Improve Codebase Architecture

## Overview

Use this skill to surface architectural friction and propose deepening opportunities. The target is not style cleanup. The target is higher leverage interfaces, better locality of change, and safer future edits.

## Vocabulary

- Module: any unit with an interface and an implementation (function, class, package, feature slice)
- Interface: what callers must know to use a module (types, invariants, error modes, ordering, config)
- Implementation: what is hidden behind the interface
- Depth: how much useful behavior is hidden behind a small interface
- Seam: a place where behavior can change without editing callers in place
- Adapter: a concrete implementation at a seam
- Locality: related change concentrated in one place
- Leverage: value callers get from depth

## Inputs To Read First

- `docs/development/contribution-and-architecture.md`
- `docs/development/detailed_respro_layout.md`
- `docs/development/detailed_app_layout.md`
- `docs/development/to-do.md`
- `.github/copilot-instructions.md`

These documents are the domain and architecture baseline for this repository.

## When To Use

- The codebase is becoming harder to change safely
- A feature requires touching too many modules
- Tests are hard to write because the current interface exposes too much wiring
- There is repeated logic across CLI, core, report, or web layers
- A review surfaces coupling, shallow pass-through modules, or unclear seams

When NOT to use:

- Small local bug fixes that do not cross module boundaries
- Cosmetic refactors without structural payoff
- Changes where behavior cannot be validated with existing tests

## Procedure

1. Explore and map friction:
   - Follow a real user flow (CLI or web) and note where understanding requires excessive hopping.
   - Identify shallow modules by applying the deletion test: if deleting the module just spreads complexity to callers, it had value; if not, it may be pass-through noise.
2. Build architecture candidates:
   - Candidate title
   - Files involved
   - Problem (current friction)
   - Proposed seam/deepening move
   - Expected benefits in locality, leverage, and testability
   - Recommendation strength: `Strong`, `Worth exploring`, or `Speculative`
3. Prioritize:
   - Pick one top candidate with the best risk-to-value ratio.
   - State the minimum safe first step.
4. Hand off for execution:
   - If implementation is requested, move to `testing` + `diagnose` guided changes.
   - If work spans subsystem boundaries, use `zoom-out` before coding.

## Constraints

- Preserve behavior unless behavior change is explicitly requested.
- Prefer incremental deepening over broad rewrites.
- Do not introduce abstraction that exceeds real current need.
- Keep CLI-first and core independence guardrails intact.
- If architecture boundaries change, update the relevant development docs in the same change.

## Output Format

Return findings first, ordered by impact.

For each candidate include:

- Files/modules involved
- Problem and evidence
- Proposed deepening move
- Expected benefit (locality, leverage, testability)
- Recommendation strength
- Minimum safe first step

Then include:

- Top recommendation and why
- Risks or unknowns to validate before implementation
- Optional follow-ups if scope expands