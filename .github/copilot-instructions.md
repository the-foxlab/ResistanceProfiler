# ResistanceProfiler — Copilot instructions

- Attention: Your primary language to talk and think in should be english independent of the user's IDE language setting.

## Primary references

- Repository structure and module responsibilities: `docs/development/contribution-and-architecture.md`
- Planning source of truth and current priorities: `to-do.md`
- Rules TSV formatting, allowed column values, and mutation notation: `docs/user/rules-tsv-format.md`
- Public project overview and usage examples: `README.md`

Keep this file focused on how code should be written in this repository.

## Project guardrails

- CLI-first: backend changes must remain usable through `respro`.
- Core independence: keep `respro/` usable without coupling to a UI layer.
- Scientific data: treat references, annotations, and rules as versioned data.
- Codon-aware: resistance interpretation stays amino-acid-centered.
- Lightweight storage: do not embed large external files in SQLite; store paths or metadata.
- VCF plus reference FASTA are required profiling inputs.
- No functions in `__init__.py`: package init files must only contain a module docstring.
  Place functions in named submodules (e.g. `respro/utils/files.py`) and import from there.
- If any feature is removed from the codebase, remove all related tests.
- If a feature is only loaded by tests remove it from the codebase.
- Never write imports into functions or classes. Always import from the top level.
- Try to write important functions with main functionality first and helper functions afterwards.

## Code style

| Element             | Convention                           |
| ------------------- | ------------------------------------ |
| Functions/variables | `snake_case`                       |
| Classes             | `PascalCase`                       |
| Constants           | `UPPER_SNAKE_CASE`                 |
| Internal helpers    | prefix with `_`                    |
| Strings             | single quotes (`'...'`)            |
| Docstrings          | triple double quotes (`"""..."""`) |
| Line length         | 100                                  |

### Constants

Avoid `UPPER_SNAKE_CASE` constants unless they are genuinely necessary:

- **Keep** constants that are reused across multiple functions/modules, represent a versioned
  value (e.g. `SCHEMA_VERSION`), or hold a large block of text that would clutter the call site
  (e.g. `SCHEMA_SQL`, `_HTML_TEMPLATE`).
- **Keep** compiled regex patterns at module level — this is a Python performance convention.
- **Keep** colour palettes or similar lookup tables used in more than one place.
- **Avoid** constants for short strings, small tuples, or sets that are only used once
  or whose meaning is obvious at the call site.

```python
# avoid — the name adds no information
_DRUG_KEYS = ('antiviral', 'drug')
drug_name = _get_value(row, *_DRUG_KEYS)

# prefer — the intent is clear inline
drug_name = _get_value(row, 'antiviral', 'drug')
```

## Import ordering

Group imports in this order, separated by blank lines:

```python
# built-in
import os
from pathlib import Path

# installed / third-party
import numpy as np
from Bio import SeqIO

# local package
from respro import config
from respro.core.annotate_vcf import annotate_variants
```

## Docstrings

Use concise reStructuredText-style parameter docs:

```python
def resolve_reference(contig: str, db_path: Path) -> Reference:
    """
    Match a VCF contig to an internal reference.

    :param contig: contig name from VCF header
    :param db_path: path to the project database
    :return: matched Reference object
    """
```

For module-level docstrings, a short description is enough:

```python
"""
This module handles codon-aware consequence annotation.
"""
```

## Type hints

- Add type hints to public functions and cross-module interfaces.
- Use `from __future__ import annotations` when forward references are needed.
- Prefer built-in generics (`list`, `dict`, `tuple`) over `typing` equivalents.
- Use `| None` instead of `Optional`.

```python
def load_rules(path: Path) -> list[Rule]:
    ...

def find_gene(name: str) -> Gene | None:
    ...
```

## Dataclasses

Use frozen dataclasses for immutable result containers:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class AminoAcidChange:
    """
    Single amino acid substitution.
    """

    gene: str
    position: int
    ref: str
    alt: str
```

Add dunder methods (`__len__`, `__iter__`, `__contains__`) when the class represents
a collection.

## Coding standards

- Prefer explicit code over clever abstraction.
- Prefer simpler, linear implementations over complex branching or indirection when behavior is the same.
- Do not add fallback code paths for missing data — require the data to be present
  and fail fast if it is not. Compatibility shims and graceful degradation add
  hidden complexity and mask bugs during build-out.
- Keep changes small, local, and reviewable.
- Avoid unrelated refactors in the same change.
- Preserve existing public APIs unless the task clearly requires a change.
- Prefer library-native functionality over custom helpers: before adding a new
  helper/utility function, check whether an equivalent function already exists
  in dependencies we already rely on (e.g. Biopython).
- During current app build-out, do not enforce backward compatibility unless
  explicitly requested by the user.
- Keep helpers close to their domain module unless reuse is clear and immediate.
- Make deterministic behavior the default, especially in reporting and exports.

### Backward compatibility

- Do not add backward-compatibility layers unless explicitly requested by the user.
- Avoid proactive migration helpers, legacy fallbacks, alias parsers, or dual-schema code paths
  when the project is still in active build-out.
- Prefer a clean fail-fast behavior over hidden compatibility logic.
- If backward compatibility is requested later, implement it as a clearly scoped, isolated change.

### Comments

- Use comments sparingly and only where they improve readability.
- Prefer short logic-focused comments (why/intent), not line-by-line restatements (what).
- Add brief comments before non-obvious biological assumptions, coordinate conversions,
  and validation branches that could be misread.
- Keep comments concise (one to two lines when possible) and colocated with the code block.
- Remove or update outdated comments in the same change; comments must stay accurate.

## Editing guidance

- Align implementation with `to-do.md` priorities unless direct user instructions say otherwise.
- When changing repository layout or module responsibilities, update
  `docs/development/contribution-and-architecture.md` in the same change.
- When behavior changes can make documentation inaccurate, update affected docs in the same
  change (at minimum `README.md`, relevant `docs/user/*`, and `docs/development/*` pages).
- In Markdown docs, do not introduce artificial manual line breaks in normal paragraphs.
  Keep prose as natural paragraphs and only break lines where structure requires it.
- Put format-specific parsing in `respro/io/`, domain interpretation in `respro/core/`,
  persistence concerns in `respro/db/`, and rendering/export concerns in `respro/report/`.
- Keep CLI handlers thin: orchestration belongs in `respro/cli.py`, while reusable logic
  belongs in package modules.

## Planning source of truth

`to-do.md` is the single planning source of truth. Review it before any substantial change.

### Structure

The file has three sections:

- **Done** — completed work, grouped by theme, marked `[x]`. Do not remove entries; they serve as
  a project history.
- **Next** — open work, grouped by theme, each item prefixed with a priority emoji:
  - 🔴 high — tackle first; blockers or foundational work
  - 🟡 medium — important but not urgent
  - 🟢 low — nice-to-have or dependent on higher-priority work
- **Deferred** (subsection of Next) — items that are explicitly postponed; keep them visible but
  do not pick them up unless the user requests it.

### How to select work

1. Prefer 🔴 items in **Next** unless the user specifies otherwise.
2. When a group of items is marked "introduce together", implement them in one change.
3. If user instructions conflict with to-do priorities, follow the user and update the to-do
   afterwards.
4. If only 🟡 items are present, reevaluate the priorities for all tasks.

### How to mark work done

When a feature is fully implemented:

1. Move its entry from **Next** to the matching theme group in **Done**, or create a new group if
   none fits.
2. Change `- 🔴/🟡/🟢` to `- [x]` and drop the priority emoji.
3. Keep the description concise — one line that summarises what was built.
4. Update the to-do in the same commit/change as the implementation.

### How to add new items

1. Choose the correct theme group in **Next**, or add a new group if none fits.
2. Assign a priority emoji based on urgency and dependency order.
3. Write a single-sentence description that includes the affected module(s) and the observable
   behaviour change.
4. If the item is intentionally deferred, place it in the **Deferred** subsection with a brief
   rationale.

### What not to do

- Do not remove **Done** entries — they are project history.
- Do not invent a "Now" section; the priority emoji replaces it.
- Do not leave the to-do stale after a change — accuracy matters more than brevity.

# Code review guidelines

When reviewing code, check the whole repository for:

- Coding standards and conventions
- Unused features, functions, classes, modules or imports
- Group related functions into modules
- Check for logical consistency and correctness, especially in edge cases
- add comments where the intent or logic is not immediately clear
- Unnecessary complexity
- Simplify highly nested functions or methods
- Simplify and improve readability
- Follow SOLID, KISS and DRY principles
- Check for proper error handling and informative error messages
- Check for proper use of type hints and docstrings
- Check for proper test coverage and quality of test cases
- Remove any behaviour that is intended for backward compatibility if not
  being explicitly requested by the user
- Check for functions that are only called by tests and remove them including tests

## Testing

Mimic a test-driven environment and write test cases first for non-trivial tasks.
Organize tests in classes when grouping related scenarios:

```python
import pytest
from respro.core.annotate_vcf import annotate_codon


class TestCodonAnnotation:
  def test_single_snp_changes_amino_acid(self) -> None:
    result = annotate_codon(...)
    assert result.alt == 'V'

  def test_synonymous_change_detected(self) -> None:
    ...
```

Use fixtures for reusable setup:

```python
@pytest.fixture
def sample_vcf(tmp_path):
    vcf = tmp_path / 'sample.vcf'
    vcf.write_text(...)
    return vcf
```

Focus tests on:

- Codon-level edge cases (overlapping ORFs, reverse strand, adjacent variants)
- Reference resolution correctness
- Resistance rule matching
- Deterministic report exports

Testing expectations:

- Add regression tests for behavior changes, especially for codon edge cases.
- Prefer focused scenario tests over broad smoke tests.
- When fixing ambiguous interpretation, add a test that would fail without the fix.
- Keep outputs deterministic so report/export tests stay stable.
