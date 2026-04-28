---
name: complexity-and-compartmentalization-audit
description: 'Audit a repository for overly complex functions, long scripts, deeply nested logic, weak module boundaries, missing intent comments, and tiny single-use helpers that should be inlined or absorbed. Use when reviewing readability and maintainability.'
argument-hint: 'Scope to inspect and whether to emphasize refactoring or bug risk.'
user-invocable: true
disable-model-invocation: false
---

# Complexity And Compartmentalization Audit

## Overview

Simplify code by reducing complexity while preserving behavior. The goal is not fewer lines; it is faster comprehension, safer modifications, and cleaner boundaries. A recommended simplification should pass this test: a new contributor should understand the flow faster than before.

## When to Use

- Find functions that are too long or too nested.
- Identify scripts or modules that should be split into clearer units.
- Flag missing intent comments around non-obvious logic.
- Find tiny one-off helpers that add indirection without reuse.

When NOT to use:

- Code is already clear and consistent.
- You do not yet understand the code path (understand first, then simplify).
- Suggested simplification would change behavior or weaken error handling.
- The task scope is narrow and the refactor would create unrelated diff noise.

## Core Principles

1. Preserve behavior exactly.
2. Follow repository conventions and existing patterns.
3. Prefer clarity over cleverness.
4. Maintain balance: avoid over-simplification.
5. Scope recommendations to changed or high-risk areas unless asked to broaden.

### Preserve Behavior Exactly

For each recommendation, verify that it preserves:

- Inputs and outputs
- Error behavior and edge handling
- Side effects and execution ordering

If preservation is uncertain, flag uncertainty explicitly and recommend a targeted check.

### Understand Before Touching (Chesterton's Fence)

Before recommending removal or inlining:

- Identify what the code is responsible for.
- Identify callers and downstream dependencies.
- Check likely reasons the structure exists (testability, extensibility, performance, historical constraints).
- Avoid removing structure you cannot explain yet.

## Procedure

1. Understand context first:
   - what the unit does
   - why it may be structured this way
   - which tests and callers define expected behavior
2. Identify simplification opportunities:
   - deep nesting (3+ levels)
   - long functions with mixed responsibilities
   - long procedural modules/scripts
   - repeated conditionals across call sites
   - boolean flag parameters that hide intent
   - nested ternaries or dense one-liners
   - helper functions used once with little naming value
   - unclear or misleading names
   - missing intent comments in non-obvious logic
3. Classify each issue as one of:
   - control-flow complexity
   - compartmentalization boundary issue
   - naming/readability issue
   - unnecessary indirection
   - documentation/intent gap
4. Recommend incremental refactors, not broad rewrites:
   - one focused change at a time
   - separate simplification from feature work
   - prefer local, reviewable changes
5. Verify suggested outcomes:
   - behavior preserved
   - readability improved
   - boundaries clarified
   - no error handling removed

## Constraints

- Do not suggest refactors without naming the exact pain point.
- Do not recommend decomposition that adds more abstraction than it removes.
- Treat correctness and clarity as higher priority than style.
- Keep recommendations local and pragmatic.
- Do not optimize for line count alone.
- Do not recommend simplification outside scope unless explicitly requested.

## Red Flags

- Suggested simplification requires changing tests to pass (likely behavior change).
- "Simplified" code is harder to follow than original.
- Error handling removed just to make code shorter.
- Large unscoped refactor suggestions mixed into unrelated work.
- Renames based on preference rather than repository conventions.

## Verification

After proposing a simplification plan, confirm:

- Existing behavior can be preserved exactly.
- Recommendations are incremental and reviewable.
- No recommendation weakens error handling or validation.
- Recommendations align with project structure conventions.

## Output Format

Return findings first, ordered by severity and impact.

For each finding include:

- What is too complex.
- Why the current structure is costly or risky.
- Whether the issue is long-file, long-function, nesting, indirection, or missing-comment related.
- Recommended refactor boundary.
- Minimum safe simplification step.
- Expected benefit.

Then include:

- "Do first" list (highest ROI, lowest risk simplifications)
- Risks or unknowns requiring validation before refactor
- Optional follow-up simplifications if scope is expanded
