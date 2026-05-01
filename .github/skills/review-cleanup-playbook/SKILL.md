---
name: review-cleanup-playbook
description: 'Use when a review should include actionable code-cleanup recommendations with Clean Code-style rule tags and small, low-risk refactor steps. Designed for the Codebase Review Specialist.'
argument-hint: 'Scope to inspect and whether to prioritize quick wins, dead code removal, or function simplification.'
user-invocable: true
disable-model-invocation: false
---

# Review Cleanup Playbook

## Overview

This skill adds a cleanup-first pass to reviews. It is based on the Python clean-code skill style from `ertugrul-dmr/clean-code-skills` and adapted for ResistanceProfiler conventions.

Goal: produce cleanup suggestions that are concrete, low-risk, and directly actionable during code review.

## When to Use

- A change is correct but harder to maintain than necessary.
- The review should include cleanup opportunities beyond bug findings.
- You see duplication, dead code artifacts, confusing names, or overgrown functions.
- The author asks for cleanup suggestions or "quick wins".

When NOT to use:

- There are unresolved Critical correctness or security issues.
- Cleanup would require broad rewrites unrelated to the reviewed change.

## Source Rule Model

Use these rule tags in findings (adapted from Python clean-code skills):

- `C1-C5`: comments hygiene (no metadata, stale comments, redundant comments, commented-out code).
- `F1-F4`: function hygiene (too many parameters, output mutation, flag args, dead functions).
- `G5`: DRY; no duplicated logic.
- `G9`: remove dead code.
- `G16`: avoid obscured intent.
- `G23`: prefer polymorphism or dispatch over long selector chains when it simplifies logic.
- `G25`: replace magic numbers with named values when reused or non-obvious.
- `G30`: functions do one thing.
- `G36`: avoid train-wreck access chains.
- `N1-N7`: naming clarity and side-effect honesty.
- `P3`: type hints on public interfaces.
- `T5-T6`: boundary tests and near-bug regression coverage for cleanup-sensitive paths.

## Cleanup Priorities

Apply this order in reviews:

1. Correctness-preserving simplifications in touched lines.
2. Dead code and stale comment cleanup.
3. Naming and function-shape improvements.
4. Small DRY extractions with clear ownership.
5. Optional deeper refactors (only when explicitly requested).

## What to Look For

When running this cleanup pass, inspect the changed lines first and then the immediately adjacent code for concrete maintainability signals.

- Dead code: unused helpers, stale branches, commented-out code, compatibility shims, unused imports, write-only variables.
- Comment problems: stale comments, comments that restate the code, metadata comments, missing intent comments around non-obvious logic.
- Function shape issues: functions doing more than one job, long selector chains, flag arguments, too many parameters, output mutation that hides side effects.
- Naming problems: vague names, misleading names, names that hide side effects, inconsistent terminology across nearby code.
- DRY opportunities: duplicated conditionals, repeated parsing or normalization logic, repeated literals that are non-obvious or reused.
- Obscured intent: dense branching, magic values, hidden coupling between steps, train-wreck access chains, clever control flow that can be flattened.
- Boundary hygiene: logic placed in the wrong layer, CLI handlers accumulating domain logic, file-format parsing outside `respro/io`, persistence logic outside `respro/db`.
- Test-adjacent cleanup needs: missing regression coverage for a risky cleanup, boundary cases that would become safer if pinned by a small targeted test.

Prefer findings that can be fixed with a small local change. If a possible cleanup would require redesign, broad rewrites, or speculative abstraction, mention it only as an optional follow-up.

## Procedure

1. Confirm baseline quality first using `code-review-and-quality`.
2. Identify cleanup candidates in changed files and immediately adjacent code.
3. For each candidate, assign one primary rule tag (`G30`, `N1`, `C3`, etc.).
4. Propose the minimum safe cleanup step:
   - rename
   - delete dead code
   - extract a helper
   - split a flag-argument function
   - replace magic value
   - add a targeted regression/boundary test
5. Explain why the step is low-risk and what behavior must remain unchanged.

## ResistanceProfiler-Specific Constraints

- Keep module boundaries intact (`respro/io`, `respro/core`, `respro/db`, `respro/report`, `respro/cli`).
- Do not introduce backward-compatibility shims unless explicitly requested.
- Keep CLI handlers thin; move reusable logic into package modules.
- Do not add imports inside functions or classes.
- Keep cleanups small and reviewable; avoid broad unrelated refactors.

## Severity Mapping

- **Required cleanup**: high-confidence maintainability issue likely to cause defects or review churn.
- **Optional cleanup**: clear improvement but non-blocking.
- **Nit**: minor readability issue with trivial impact.

## Output Format

Return cleanup findings after primary bug/security findings.

For each cleanup finding include:

- Rule tag: `[G30]`, `[N1]`, `[C3]`, etc.
- Location: file reference.
- Problem: what hurts readability or maintainability.
- Minimal fix: smallest safe change.
- Risk note: why behavior should stay unchanged.

Example style:

- `[G30]` `respro/core/annotation.py`: `annotate_gene_variants` mixes filtering, transformation, and reporting formatting. Split formatting into a dedicated helper to keep annotation logic linear.

## Guardrails

- Do not request cleanup that obscures domain assumptions.
- Do not suggest abstraction layers without a concrete second use case.
- Do not recommend large rewrites as part of routine review.
- If uncertain, mark as uncertainty and suggest a narrow validation step.