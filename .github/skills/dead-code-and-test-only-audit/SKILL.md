---
name: dead-code-and-test-only-audit
description: 'Audit a repository for dead code, stale modules, unused helpers, production code only exercised by tests, and code paths that are effectively not used outside tests. Use when reviewing maintainability and codebase health.'
argument-hint: 'Scope to inspect and any exclusions.'
user-invocable: true
disable-model-invocation: false
---

# Dead Code And Test-Only Audit

## When to Use

- Find dead code and stale modules.
- Check whether helpers are only used from tests.
- Validate whether production paths are actually reachable from runtime code.
- Review cleanup opportunities without changing public behavior.

## Procedure

1. Search for candidate symbols, helpers, and modules that look unused or stale.
2. Verify usages across production code and tests separately.
3. Distinguish:
   - Truly unused code.
   - Code used only by tests.
   - Code used indirectly through CLI, framework registration, imports, or reflection.
4. Flag only findings that have evidence.
5. For each finding, recommend the smallest safe cleanup:
   - remove it
   - inline it
   - move it
   - keep it but document why it exists

## Constraints

- Do not call code dead without checking runtime entry points.
- Do not treat test usage alone as sufficient evidence of production relevance.
- Prefer evidence over suspicion.
- Avoid style-only comments.

## Output Format

Return findings ordered by severity and confidence.

For each finding include:

- What appears unused.
- Whether it is fully dead or test-only.
- Evidence from production and test usage.
- Risk of removal.
- Recommended cleanup.
