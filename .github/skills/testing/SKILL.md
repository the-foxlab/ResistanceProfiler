---
name: testing
description: 'Drives development with tests for ResistanceProfiler. Use when implementing any logic, fixing any bug, or changing any behavior. Use when writing pytest tests, adding regression tests for codon-level edge cases, adding fixtures, or proving a bug fix works.'
argument-hint: 'What to test: new feature, bug fix, edge case, or module name.'
user-invocable: true
disable-model-invocation: false
---

# Testing

## Overview

Write a failing test before writing the code that makes it pass. For bug fixes, reproduce the bug with a test before attempting a fix. Tests are proof — "seems right" is not done. A codebase with good tests is the fastest path to confident refactoring; a codebase without tests is a liability.

Run the full test suite with:

```bash
python -m pytest
```

Web-layer tests only:

```bash
python -m pytest tests/test_web_api.py -q
```

## When to Use

- Implementing any new logic or behaviour
- Fixing any bug (the Prove-It Pattern)
- Modifying existing functionality
- Adding codon-level, strand, or CIGAR edge case coverage
- Any change that could break existing behaviour

When NOT to use: pure configuration changes, documentation updates, or static content additions that have no behavioural impact.

---

## The TDD Cycle

```
    RED                GREEN              REFACTOR
 Write a test    Write minimal code    Clean up the
 that fails  ──→  to make it pass  ──→  implementation  ──→  (repeat)
      │                  │                    │
      ▼                  ▼                    ▼
   Test FAILS        Test PASSES         Tests still PASS
```

### Step 1: RED — Write a Failing Test

Write the test first. It must fail. A test that passes immediately proves nothing.

```python
class TestAminoAcidAnnotation:
    def test_reverse_strand_snp_produces_correct_alt_aa(self) -> None:
        # RED: fails because the handler is not yet implemented
        result = annotate_snp(gene=reverse_gene, position=42, ref='A', alt='G')
        assert result.alt_aa == 'V'
```

### Step 2: GREEN — Make It Pass

Write the minimum code to make the test pass. Do not over-engineer.

### Step 3: REFACTOR — Clean Up

With tests green, improve the code without changing behaviour:

- Extract shared logic
- Improve naming
- Remove duplication
- Optimize if necessary

Run tests after every refactor step to confirm nothing broke.

---

## The Prove-It Pattern (Bug Fixes)

When a bug is reported, do not start by trying to fix it. Start by writing a test that reproduces it.

```
Bug report arrives
       │
       ▼
  Write a test that demonstrates the bug
       │
       ▼
  Test FAILS (confirming the bug exists)
       │
       ▼
  Implement the fix
       │
       ▼
  Test PASSES (proving the fix works)
       │
       ▼
  Run full test suite (no regressions)
```

When fixing ambiguous interpretation (e.g. a codon-coordinate edge case), add a test that would fail without the fix.

---

## Test Structure

### Organize in Classes for Related Scenarios

Group related scenarios under a test class. Use descriptive names that read like a specification.

```python
import pytest
from respro.core.annotate_vcf import annotate_codon


class TestCodonAnnotation:
    def test_single_snp_changes_amino_acid(self) -> None:
        result = annotate_codon(...)
        assert result.alt == 'V'

    def test_synonymous_change_detected(self) -> None:
        result = annotate_codon(...)
        assert result.consequence == 'synonymous'

    def test_reverse_strand_snp_is_complement_corrected(self) -> None:
        ...
```

### Use Fixtures for Reusable Setup

```python
@pytest.fixture
def sample_vcf(tmp_path):
    vcf = tmp_path / 'sample.vcf'
    vcf.write_text('##fileformat=VCFv4.2\n...')
    return vcf
```

Use fixtures for: temporary files, in-memory DBs, minimal project DB setup, and mock external calls (PubChem, NCBI).

### Arrange-Act-Assert

Structure every test in three clear phases:

```python
def test_frameshift_emits_fsX_sentinel(self) -> None:
    # Arrange
    variant = make_variant(ref='ATG', alt='AT', position=10)

    # Act
    result = annotate_indel(variant, gene)

    # Assert
    assert result.alt_aa == 'fsX'
```

---

## Focus Areas for This Repository

Priority test scenarios, ordered by risk:

### 1. Codon-Level Edge Cases

- Reverse-strand SNPs — complement/RC handling
- Adjacent SNPs in the same codon — combined event annotation
- Mid-codon insertions and deletions — non-assessable (return `None`)
- Frameshifts — `fsX` sentinel, not a real AA
- IUPAC ambiguity expansion — all combinations enumerated, fractional AF
- Full-codon NNN — emits `CoverageGap`, not IUPAC variants
- Partial-N codons (1–2 N) — IUPAC-expanded, not a gap
- Overlapping ORF variants — one remapped call per matching CDS

### 2. Reference Resolution and Coordinate Remapping

- CIGAR-based variant remap from user-reference to internal coordinates
- Strand-aware anchor projection for indels
- Allele complement + payload reverse-complement on negative strand
- REF allele verification against active query sequence
- BAM-derived coverage gaps projected to codon coordinates

### 3. Resistance Rule Matching

- Single-mutation rule: exact per-position allele match
- Combination rule: all members must co-occur to fire
- Formula rule: `AND`/`OR`/`NOT`/`XOR` expressions evaluated correctly
- BLOSUM62 similarity scoring for non-exact substitutions
- Shared substitution in two rule sets — both fire when complete
- Rule with unknown gene name — skipped with warning, not abort
- Rule whose ref AA mismatches GenBank — skipped with warning, not abort

### 4. Deterministic Report Exports

- HTML, JSON, and TSV outputs are byte-stable across reruns
- `regenerate` from `results.db` produces identical report to original run
- `regenerate --json` produces identical report from JSON export
- Column hiding logic (IC50, fold-IC50, clinical phenotype) is deterministic

### 5. Schema Migrations

- Opening an older `results.db` migrates automatically without data loss
- New columns added with correct defaults
- `coverage_gap` table codon-range semantics preserved after migration

---

## Test Sizing

| Size | Resources | Target | Examples |
|---|---|---|---|
| Small | Single process, no I/O | Vast majority | Pure annotation logic, rule matching, codon walks |
| Medium | Localhost, tmp files, in-memory DB | Integration paths | CLI commands, DB persistence, report export |
| Large | External services | Avoid in CI | Manual only: NCBI fetch, PubChem resolution |

Mock external services (PubChem, NCBI E-utilities) in all automated tests. Use `fakeredis` and `Queue(is_async=False)` for web-layer job isolation.

---

## Writing Good Tests

### Test Behaviour, Not Implementation

Assert on outcomes. Do not assert on which internal methods were called — that makes tests break during refactoring even when behaviour is unchanged.

```python
# Good: tests what the function produces
assert result.alt_aa == 'V'

# Bad: tests internal call sequence
mock_translate.assert_called_once_with(codon)
```

### DAMP Over DRY

Each test should tell a complete story. Duplication in tests is acceptable when it makes each test independently readable without tracing shared helpers.

### One Concept Per Test

```python
# Good
def test_rejects_empty_gene_name(self): ...
def test_trims_whitespace_from_gene_name(self): ...

# Bad
def test_gene_name_validation(self):
    # tests both in one — harder to diagnose on failure
```

### Prefer Real Implementations Over Mocks

```
Preference order:
1. Real implementation  → highest confidence
2. Fake                 → in-memory version (e.g. tmp SQLite, fakeredis)
3. Stub                 → canned data, no behaviour
4. Mock (interaction)   → use only at external service boundaries
```

Mock only when the real implementation is slow, non-deterministic, or has uncontrollable side effects (external APIs, file system paths outside tmp).

---

## Test Anti-Patterns to Avoid

| Anti-pattern | Problem | Fix |
|---|---|---|
| Testing implementation details | Breaks on refactor even if behaviour is unchanged | Test inputs and outputs |
| Flaky tests (timing, order-dependent) | Erodes trust | Use deterministic assertions; isolate test state |
| No test isolation | Tests pass alone, fail together | Each test sets up and tears down its own state |
| Mocking everything | Tests pass, production breaks | Prefer real or fake over mock |
| Tests that always pass | Proves nothing | Verify the test fails before the fix |
| Skipping tests to make suite pass | Hides regressions | Fix the test or remove the feature |

---

## Verification Checklist

After completing any implementation:

```
- [ ] Every new behaviour has a corresponding test
- [ ] All tests pass: python -m pytest
- [ ] Bug fixes include a reproduction test that failed before the fix
- [ ] Test names describe the behaviour being verified
- [ ] No tests were skipped or disabled
- [ ] Deterministic outputs remain stable (report/export tests)
- [ ] If a feature was removed, its tests were also removed
```

---

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "I'll write tests after the code works" | You won't. Tests written after the fact test implementation, not behaviour. |
| "This is too simple to test" | Simple code gets complicated. The test documents the expected behaviour. |
| "I tested it manually" | Manual testing does not persist. Tomorrow's change may break it silently. |
| "The tests pass, so it's good" | Tests are necessary but not sufficient. They do not catch architecture problems or security issues. |

## Red Flags

- Writing code without any corresponding tests
- Tests that pass on the first run without a failing state first
- Bug fixes without reproduction tests
- Test names that do not describe the expected behaviour
- Skipping tests to make the suite pass
- Removing tests without removing the feature they cover
