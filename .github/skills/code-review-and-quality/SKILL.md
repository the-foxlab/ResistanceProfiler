---
name: code-review-and-quality
description: 'Conducts multi-axis code review for ResistanceProfiler. Use before merging any change. Use when reviewing code written by yourself, another agent, or a human. Use when assessing code quality across correctness, readability, architecture, security, and performance before it enters the main branch.'
argument-hint: 'Scope (file, module, or full repo), review focus, and change description.'
user-invocable: true
disable-model-invocation: false
---

# Code Review and Quality

## Overview

Multi-dimensional code review with quality gates. Every change gets reviewed before merge. Review covers five axes: correctness, readability, architecture, security, and performance.

Approval standard: approve a change when it definitely improves overall code health, even if it is not perfect. Do not block a change because it is not exactly how you would have written it. If it improves the codebase and follows project conventions, approve it.

Do not approve a change that has any Critical issue. If you are uncertain about a potential issue, say so and recommend the narrowest investigation that would resolve the uncertainty rather than guessing.

## When to Use

- Before merging any PR or change
- After completing a feature implementation
- When another agent or model produced code that needs evaluation
- When refactoring existing code
- After any bug fix (review both the fix and the regression test)

## The Five-Axis Review

Every review evaluates code across these five dimensions.

### 1. Correctness

Does the code do what it claims to do?

- Does it match the spec or task requirements?
- Are edge cases handled (empty, boundary, ambiguous IUPAC, reverse strand, None)?
- Are error paths handled, not just the happy path?
- Are exceptions handled explicitly (no silent `except` blocks that swallow failures)?
- Are there off-by-one errors in codon-coordinate logic or CIGAR walks?
- Do tests actually test the right behavior?
- Is variant remap, allele-frequency filtering, and rule matching consistent with the documented algorithm?

### 2. Readability and Simplicity

Can another engineer understand this code without the author explaining it?

- Are names descriptive and consistent with project conventions (`snake_case`, `PascalCase`, `_` prefix for internal helpers)?
- Is control flow straightforward — no deeply nested branches, no clever one-liners?
- Could this be done more simply? More lines than needed is a problem, not a sign of thoroughness.
- Do abstractions earn their complexity? Do not generalize until the third use case.
- Are intent comments present where the logic is non-obvious (coordinate conversions, biological assumptions, strand-flip logic)?
- Do not comment obvious code — only non-obvious intent.
- No dead code artifacts: unused variables, backwards-compat shims, stale imports, commented-out blocks.

### 3. Architecture

Does the change fit the system's design?

- Format-specific parsing belongs in `respro/io/`.
- Domain interpretation and profiling logic belongs in `respro/core/`.
- Persistence and query helpers belong in `respro/db/`.
- Rendering and export belongs in `respro/report/`.
- CLI handlers belong in `respro/cli/` and must remain thin — no domain logic.
- Web transport and validation belongs in `web/backend/`; reuse `respro/` logic, do not reimplement.
- No functions in `__init__.py` — only module docstrings.
- No imports inside functions or classes — always top-level.
- Does the change follow these boundaries or blur them? If it crosses a boundary, is there a good reason?
- Are new dependencies flowing in the right direction (no circular imports)?
- Is there code duplication that should be shared?

### 4. Security

Does the change introduce vulnerabilities?

- Is user input validated and sanitised at system boundaries (upload endpoints, CLI file paths, VCF/FASTA content)?
- Are secrets kept out of code, logs, and version control?
- Are file path confinement rules respected (`RESPRO_WEB_ALLOWED_ROOTS`)?
- Are SQL queries parameterised — no string concatenation in DB calls?
- Are upload size limits, file-type validation, and binary-content checks preserved?
- Is auth enforcement applied consistently on protected endpoints?
- Is data from external sources (user uploads, NCBI, PubChem) treated as untrusted?

For detailed security guidance and the full checklist, use the `security-and-hardening` skill or the **Security Specialist** agent.

### 5. Performance

Does the change introduce performance problems?

- Any N+1 query patterns against `project.db` or `results.db`?
- Any unbounded loops over sequences or rule sets without early exit?
- Any large objects created in hot paths (alignment workers, per-gene annotation loops)?
- Are alignment results cached where caching is expected (`--cache` / `--no-cache`)?
- Any synchronous blocking operations that should be async in the web layer?
- Any missing pagination on list endpoints?

---

## Review Process

### Step 1: Understand the Context

Before looking at code, understand the intent:

- What is this change trying to accomplish?
- Which module(s) and CLI commands are affected?
- What is the expected observable behaviour change?

### Step 2: Review the Tests First

Tests reveal intent and coverage:

- Do tests exist for the change?
- Do they test behaviour, not implementation details?
- Are codon-level edge cases covered (reverse strand, overlapping ORFs, IUPAC ambiguity, N-stretch gaps)?
- Are deterministic output tests stable?
- Would the tests catch a regression if the code changed?

### Step 3: Review the Implementation

Walk through the code with the five axes in mind:

For each file changed:
1. Correctness — does this code do what the test says it should?
2. Readability — can I understand this without help?
3. Architecture — does this fit the module boundaries above?
4. Security — any vulnerabilities?
5. Performance — any bottlenecks?

When the diff changes module boundaries or cross-layer flows, run `repo-knowledge-graph` in `quick` mode to verify architecture drift and identify any required updates to development layout docs.

### Step 4: Categorize Findings

Label every finding with its severity:

| Label | Meaning |
|---|---|
| **Critical:** | Blocks merge — security vulnerability, data loss, broken functionality |
| *(no prefix)* | Required change — must address before merge |
| **Nit:** | Minor, optional — author may ignore |
| **Optional:** / **Consider:** | Suggestion — worth considering but not required |
| **FYI** | Informational only — no action needed |

Every Critical and Required finding should include a specific fix recommendation.

### Step 5: Verify the Verification

- Are tests run and passing?
- Is expected CLI behaviour verified?
- For report changes: is output deterministic?
- For schema changes: is migration tested against an existing DB?

---

## Dead Code Hygiene

After any refactoring or implementation change, check for orphaned code:

1. Identify code that is now unreachable or unused.
2. List it explicitly before removing.
3. Ask before deleting: "Should I remove these now-unused elements: [list]?"

For deeper dead-code investigation, use the `dead-code-and-test-only-audit` skill.

## Complexity Hygiene

- If a function is hard to read or a module is too long, flag it.
- For deeper compartmentalization and complexity analysis, use the `complexity-and-compartmentalization-audit` skill.

---

## Change Sizing

Small, focused changes are easier to review, faster to merge, and safer to deploy:

```
~100 lines changed    → Good. Reviewable in one pass.
~300 lines changed    → Acceptable for a single logical change.
~1000 lines changed   → Too large. Ask to split.
```

One change = one self-contained modification that addresses one thing, includes related tests, and leaves the system functional. Separate refactoring from feature work — if a change refactors existing code and adds new behaviour, that is two changes.

---

## The Review Checklist

```
## Review: [change title]

### Context
- [ ] I understand what this change does and why

### Correctness
- [ ] Change matches spec/task requirements
- [ ] Edge cases handled (boundary, empty, reverse strand, ambiguous IUPAC)
- [ ] Error paths handled
- [ ] No silent exception handlers (`except` blocks log, re-raise, or raise explicit domain errors)
- [ ] Tests cover the change, including regression tests for bug fixes

### Readability
- [ ] Names are consistent with project conventions
- [ ] Logic is straightforward with no unnecessary nesting
- [ ] Intent comments present for non-obvious logic
- [ ] No dead code artifacts

### Architecture
- [ ] Module boundary respected (io / core / db / report / cli)
- [ ] No functions in __init__.py
- [ ] No imports inside functions or classes
- [ ] No unnecessary coupling or duplication

### Security
- [ ] Input validated at boundaries
- [ ] File path confinement respected
- [ ] SQL parameterised
- [ ] Auth checks in place for web endpoints

### Performance
- [ ] No N+1 patterns in DB access
- [ ] No unbounded loops in hot paths
- [ ] Alignment caching respected

### Verification
- [ ] Tests pass
- [ ] Output is deterministic where expected
- [ ] Schema migration tested if applicable

### Verdict
- [ ] Approve — ready to merge
- [ ] Request changes — issues must be addressed
```

---

## Review Output Template

```
## Review Summary

**Verdict:** APPROVE | REQUEST CHANGES

**Overview:** [1-2 sentences summarizing the change and overall assessment]

### Critical Issues
- [file reference] [description and recommended fix]

### Required Changes
- [file reference] [description and recommended fix]

### Suggestions
- [file reference] [optional improvement]

### What's Done Well
- [specific positive observation]

### Verification Story
- Tests reviewed: [yes/no, observations]
- Build or focused validation checked: [yes/no, observations]
- Security checked: [yes/no, observations]
```

---

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "It works, that's good enough" | Working code that is unreadable, insecure, or architecturally misplaced creates debt that compounds. |
| "The tests pass, so it's good" | Tests are necessary but not sufficient. They do not catch architecture problems, security issues, or readability concerns. |
| "AI-generated code is probably fine" | AI code needs more scrutiny, not less. It is confident and plausible, even when wrong. |
| "We'll clean it up later" | Later never comes. The review is the quality gate — use it. |

## Red Flags

- Changes merged without any review
- Review that only checks if tests pass (ignoring other axes)
- Security-sensitive changes (upload, path resolution, auth) without security-focused review
- Large changes with no splitting strategy
- No regression tests with bug-fix PRs
- "I'll fix it later" — it never happens
- Backward-compatibility shims added without explicit user request

## Reviewer Rules

1. Review the tests first — they reveal intent and coverage.
2. Read the task, spec, or user request before reviewing implementation details.
3. Every Critical and Required finding must include a concrete fix recommendation.
4. Include at least one specific positive observation when the change does something well.
5. Do not guess. If evidence is incomplete, say what remains uncertain and what should be checked next.
