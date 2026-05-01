# Copilot instructions

## Primary references

- Repository structure and module responsibilities: `docs/development/contribution-and-architecture.md`
- Detailed module/function interaction layouts: `docs/development/detailed_respro_layout.md`, `docs/development/detailed_app_layout.md`
- Planning source of truth and current priorities: `docs/development/to-do.md`
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

- Align implementation with `docs/development/to-do.md` priorities unless direct user instructions say otherwise.
- When changing repository layout or module responsibilities, update
  `docs/development/contribution-and-architecture.md` in the same change.
- For architecture-relevant changes, update affected sections in
  `docs/development/detailed_respro_layout.md` and/or `docs/development/detailed_app_layout.md`.
  Use `repo-knowledge-graph` selectively for drift checks; do not run it on routine local edits.
- When behavior changes can make documentation inaccurate, update affected docs in the same
  change (at minimum `README.md`, relevant `docs/user/*`, and `docs/development/*` pages).
- In Markdown docs, do not introduce artificial manual line breaks in normal paragraphs.
  Keep prose as natural paragraphs and only break lines where structure requires it.
- Put format-specific parsing in `respro/io/`, domain interpretation in `respro/core/`,
  persistence concerns in `respro/db/`, and rendering/export concerns in `respro/report/`.
- Keep CLI handlers thin: orchestration belongs in `respro/cli.py`, while reusable logic
  belongs in package modules.

## Planning source of truth

`docs/development/to-do.md` is the single planning source of truth. Review it before any substantial change.
Full todo management rules (structure, priority selection, marking done, adding items) are defined
in the `todo-management` skill.

# Code review guidelines

Full code review is handled by the **Codebase Review Specialist** agent and its skills
(`code-review-and-quality`, `dead-code-and-test-only-audit`, `complexity-and-compartmentalization-audit`, `review-cleanup-playbook`).

When reviewing code, always check for:

- Coding standards and conventions defined in this file
- Logical consistency and correctness, especially in edge cases
- Proper error handling and informative error messages
- Proper use of type hints and docstrings
- Proper test coverage and quality of test cases
- Backward-compatibility code that was not explicitly requested — remove it
- Follow SOLID, KISS and DRY principles

## Testing

Full testing rules, TDD cycle, fixtures, focus areas, and anti-patterns are defined in the `testing` skill.
