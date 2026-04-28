---
name: public-documentation
description: 'Maintains public documentation for ResistanceProfiler. Use when reviewing or updating README, docs/user, or public-facing development docs for technical accuracy, end-user readability, and drift from current CLI, web, database, or configuration behavior.'
argument-hint: 'Documentation scope, affected feature, and whether to review, update, or rewrite.'
user-invocable: true
disable-model-invocation: false
---

# Public Documentation

## Overview

Public documentation must be technically correct, current with the codebase, and readable for non-expert users. For user-facing documentation, correctness comes first, but readability is a close second: explain the workflow clearly enough that a non-expert can follow it without guessing, while still preserving the important technical detail.

## When to Use

- Reviewing `README.md` for drift from current behavior
- Updating anything under `docs/user/`
- Checking whether setup, hosting, CLI, output, or configuration docs are outdated
- Rewriting documentation that is technically correct but too developer-centric or hard to follow
- Updating public-facing workflow or architecture docs when a user-visible change makes them inaccurate

When NOT to use:
- Internal code comments
- Private planning notes
- Changelogs or release notes unless explicitly requested
- Source code or test changes

---

## Documentation Priorities

1. Technical correctness
2. User safety and operational clarity
3. End-user readability
4. Depth without jargon overload
5. Consistency across files

For non-development docs, prefer direct explanations, concrete examples, and explicit prerequisites over compressed expert shorthand.

---

## Scope

Primary scope:
- `README.md`
- `docs/user/`

Secondary scope:
- Public-facing sections of `docs/development/` when they affect user understanding of workflows or architecture
- Public-facing docs elsewhere in the repo when explicitly requested

Always verify claims against current code and configuration before editing.

---

## Review Process

### Step 1: Establish the Current Behavior

Before editing docs, verify the implementation surface:

- CLI behavior: `respro/cli/`
- Core workflow and outputs: `respro/core/`, `respro/report/`, `respro/db/`
- Web behavior and configuration: `web/backend/`, `web/frontend/`, `docker-compose.web.yml`
- Project-level examples and current messaging: `README.md`, `docs/user/*`, `docs/development/*`
- Repo conventions: `.github/copilot-instructions.md`

Do not trust existing documentation as evidence of current behavior.

### Step 2: Identify Documentation Gaps

Classify each issue as one of:

- Outdated behavior
- Missing documentation
- Technically correct but unclear wording
- Incorrect prerequisite or setup step
- Structurally confusing flow
- Too much assumed bioinformatics or developer knowledge
- Inconsistent terminology across files

### Step 3: Rewrite for Accuracy and Readability

When updating user-facing docs:

- Explain what the feature does before explaining edge details
- State prerequisites and required inputs explicitly
- Use concrete filenames, commands, and example flows
- Explain outputs and limitations, not just inputs
- Prefer short sections with informative headings
- Define domain-specific terms when non-experts may not know them
- Avoid artificial line breaks in normal paragraphs
- Keep examples aligned with the actual command names, flags, and env vars in the code

### Step 4: Sync Related Files

When a user-visible behavior changes, update all affected public docs in the same pass. At minimum, check whether the change also affects:

- `README.md`
- Relevant `docs/user/*`
- Relevant `docs/development/*`

### Step 5: Verify the Documentation Change

Check that:

- Every documented command, flag, file path, API expectation, or env var exists
- Examples are runnable or at least syntactically valid
- The document is readable without prior project knowledge
- The document does not omit critical warnings or limitations
- Terminology is consistent across the public docs set

---

## Writing Rules

### For End-User Docs

- Assume the reader may be technically capable but unfamiliar with bioinformatics workflows
- Prefer "what this is", "what you need", "what happens", and "what to watch for"
- Spell out required files and why they are needed
- Explain failure cases and common mistakes where they matter operationally
- Keep the prose calm, direct, and instructional

### For Development Docs

- Keep explanations accurate and concrete
- Focus on responsibilities, boundaries, and workflow implications
- Do not over-simplify architecture to the point of becoming misleading

### For Examples

- Use realistic command lines and paths
- Keep examples aligned with current CLI and web configuration behavior
- Avoid placeholder examples that would mislead a user into the wrong workflow

---

## Repository-Specific Checks

Always verify these areas when relevant:

- CLI command names and options in `respro/cli/`
- Required VCF + reference FASTA input pairing
- Results DB and regenerate workflows
- HTML / JSON / TSV export behavior
- Web hosting and environment variables in `docker-compose.web.yml` and `web/backend/`
- Maintained database workflow and download/init behavior
- Output interpretation language in `docs/user/output-interpretation.md`

---

## Documentation Checklist

```
### Accuracy
- [ ] Every documented command or flag exists
- [ ] Every documented env var exists
- [ ] Workflow matches current code behavior
- [ ] Limitations and prerequisites are accurate

### Readability
- [ ] Non-expert readers can follow the flow
- [ ] Jargon is explained or reduced
- [ ] Structure is easy to scan
- [ ] Examples are concrete and understandable

### Completeness
- [ ] Inputs are explicit
- [ ] Outputs are explained
- [ ] Common failure points or caveats are covered
- [ ] Related docs were updated in the same pass when needed

### Consistency
- [ ] Terminology matches across docs
- [ ] README and detailed docs do not conflict
- [ ] Public docs align with repo conventions
```

---

## Anti-Patterns to Avoid

- Preserving outdated docs because they are "close enough"
- Making docs shorter by removing essential operational detail
- Using internal developer shorthand in user docs
- Documenting features, flags, or files that do not exist
- Repeating the same explanation inconsistently across multiple docs
- Explaining only the happy path when users need to know required inputs or common failures

## Output Format

When reviewing only:
- List outdated or unclear sections first
- For each issue: what is wrong, why it matters, and which file should change

When editing:
- Update the docs directly
- Summarize what changed, what behavior it now documents correctly, and any remaining ambiguities

If no issues are found, state that explicitly and name what was checked
