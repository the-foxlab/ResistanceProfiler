# ResistanceProfiler project structure

This document is the source of truth for how the repository is organized and where
new work should go.

For implementation priorities, see `docs/roadmap.md`.
For rules TSV formatting, allowed entries, and mutation notation, see
`docs/rules-tsv-format.md`.
For coding standards and Copilot-specific guidance, see
`.github/copilot-instructions.md`.

## Design goals reflected in the layout

- CLI-first workflow via `respro`.
- Core profiling logic kept in `respro/`.
- SQLite-backed project data and curated rules.
- Gene-slice sequence storage (CDS-level) rather than full-reference sequence blobs.
- Codon-aware interpretation at the amino acid level.
- Deterministic outputs and regression-oriented tests.

## Top-level repository layout

```text
ReistanceProfiler/
├── .github/
│   └── copilot-instructions.md
├── docs/
│   ├── roadmap.md
│   ├── project-structure.md
│   ├── rules-tsv-format.md
│   └── mutation-nomenclature.md
├── respro/
│   ├── cli.py
│   ├── core/
│   ├── db/
│   ├── io/
│   ├── report/
│   └── utils/
├── tests/
├── README.md
└── pyproject.toml
```

## Package responsibilities

### `respro/`

The main Python package. Keep it independently usable from the command line and from
future integrations.

#### `respro/cli.py`

Defines the public CLI entry points:

- `respro init`
- `respro init-add`
- `respro profile`
- `respro regenerate`

CLI code should coordinate the pipeline and input/output handling, but avoid embedding
heavy biological logic directly in argument handlers. `respro init` is for fresh
project creation, while `respro init-add` extends an existing DB with additional
rules and optional new GenBank annotations. `respro profile` optionally accepts
`--results-db`: if the path does not exist it is created, if it exists it is
validated before profiling continues. `respro regenerate` reads from an existing
results database; `--list` shows all stored runs, `--identifier` with `--project`
and `--out` regenerates the full report for a specific run after validating that
the project database fingerprint matches.

### `respro/core/`

Pure profiling logic. Changes here should usually come with focused regression tests.

- `annotation.py`: codon-aware consequence annotation, translation logic,
  coordinate helpers (`normalize_position`), mutation token normalization
  (`normalize_mutation`), and allele-frequency binning (`assign_af_bins`).
- `resistance_rules.py`: load resistance rules from DB and match amino acid
  observations against them. The current active logic covers atomic single-
  mutation rules; future combined/co-occurring rule-set matching is prepared in
  the schema but not yet enabled.
- `sequence_matching.py`: align user-provided query sequences (FASTA) against
  internal CDS annotations using Biopython's PairwiseAligner.  Produces CIGAR-
  based coordinate mappings between query positions and internal CDS positions.
  Only genes with resistance rules are screened by default.  Results are cached
  in the project DB (`query_reference`, `query_gene_mapping`) so repeat runs
  with the same reference skip re-alignment.
- `profile.py`: FASTA-based profiling and cached-query reuse — resolves either a
  user FASTA or a stored query header against internal CDS annotations, inverts
  CIGAR coordinate maps to remap VCF variants from user reference coordinates
  to internal genomic coordinates, checks VCF REF against the active query
  sequence, and transforms REF/ALT bases to the internal forward strand for
  downstream annotation.

Use `respro/core/` for rules that should stay usable without report or storage layers.

### `respro/db/`

SQLite schema and project/results database initialization logic.

- `schema.py`: project and results schema creation plus validation helpers.
- `models.py`: database-facing data structures and row mapping helpers,
  including future-facing combined rule-set containers.
- `init_project.py`: project creation, validation, and curated data loading;
  enforces the rules TSV schema documented in `docs/rules-tsv-format.md`.
- `results.py`: profiling run persistence — `save_run`, `load_run`, `list_runs`,
  `reconstruct_annotations`, and `project_fingerprint` for cross-DB validation.

Database boundaries:

- `project.db`: curated, versioned project data (references, genes, rules, drugs).
- `results.db`: run-scoped profiling outputs (`run`, `variant_result`).

The schema also reserves tables for future combined/co-occurring resistance
rules (`resistance_rule_set`, `resistance_rule_set_member`). These are a data-
model preparation only; current profiling still evaluates single-mutation rules.

### `respro/io/`

Input readers, format-specific parsing, and lightweight external data clients.

- `vcf.py`: VCF ingestion.
- `genbank.py`: GenBank parsing for project initialization.
- `reference.py`: reference loading from the project DB and FASTA utilities
  (`read_fasta`, `load_reference_sequence`), plus reference-resolution helpers
  and gene loading for a resolved reference.
- `pubchem.py`: thin, stdlib-only PubChem PUG REST client. Resolves drug names
  to PubChem CIDs, canonical URLs, short descriptions, and structure-image URLs.
  All failures
  (network unavailable, name not recognised, unexpected API response) return
  `None` so callers treat PubChem lookup as strictly best-effort.

Parsing and validation should live here when they are primarily format concerns.
Once data is normalized into domain objects, downstream logic should move into
`respro/core/`.

### `respro/report/`

Output model and rendering/export logic.

- `results_model.py`: canonical profiling result structures.
- `plots.py`: figures and plot-ready transformations.
- `html.py`: HTML rendering and table-data assembly.
- `templates/report.html.j2`: Jinja template for report layout and client-side table sorting.
- `static/report.css`: report styles (inlined at render time for standalone HTML output).
- `static/report.js`: client-side table sorting logic (inlined at render time for standalone HTML output).
- `export.py`: export orchestration for all report artifacts, plus TSV/CSV
  tabular helpers (`write_tsv`, `to_tsv_string`). The profiling workflow exports
  a standalone HTML report with an embedded mutation overview plot.

All report outputs should derive from the same result model so HTML and optional
machine-readable outputs stay consistent.

### `respro/utils/`

Small shared helpers that do not belong to domain modules.

Keep this package narrow. Prefer putting logic close to its domain unless it is reused
cleanly across the codebase.

## Tests layout

### `tests/`

The test suite mirrors the major backend responsibilities.

- `test_annotation.py`: codon translation and consequence behavior.
- `test_sequence_matching.py`: CDS alignment, CIGAR coordinate mapping, and DB caching.
- `test_profile_fasta.py`: FASTA-based profiling — coordinate remapping, cached
  query-header reuse, sanity checks, and CLI end-to-end with `--ref-fasta` or
  `--query-ref-header`.
- `test_reference_io.py`: reference matching and normalization expectations.
- `test_rules.py`: resistance rule matching behavior.
- `test_profile_cli.py`: CLI-level workflow coverage.
- `test_regenerate_cli.py`: `respro regenerate` — listing, report regeneration, and
  fingerprint mismatch rejection.
- `test_report_outputs.py`: deterministic report/export behavior.
- `test_results_db.py`: results DB schema, save/load round-trips, and fingerprint behavior.
- `test_init_project.py`: coordinate base detection and reference AA validation.
- `test_pubchem.py`: PubChem REST client and PubChem data loading (fully mocked, no network).
- `conftest.py`: shared fixtures.

When adding behavior:

- prefer a focused regression test near the affected area;
- add codon-edge-case coverage for annotation changes;
- keep report output checks deterministic;
- avoid broad smoke tests when a narrow scenario proves the behavior better.

## Documentation layout

### `docs/roadmap.md`

Planning source of truth for completed work, current `Now` priorities, and next goals.
Review this before substantial changes.

### `docs/rules-tsv-format.md`

Primary source of truth for rules TSV columns, allowed entries, mutation notation,
phenotype normalization, IC50 handling, and combination-rule formatting.
Update this whenever rules input behavior changes.

### `docs/mutation-nomenclature.md`

Short redirect page pointing readers to `docs/rules-tsv-format.md`.

### `docs/project-structure.md`

Update it when package responsibilities or repository layout change in a way
that affects where contributors should place code.

## How the main workflow maps to modules

A typical `respro profile` run flows through the repository like this:

1. CLI argument handling in `respro/cli.py`.
2. Input loading in `respro/io/`.
3. Reference resolution and variant processing in `respro/core/`.
4. Rule matching in `respro/core/resistance_rules.py`.
5. Result assembly in `respro/report/results_model.py` and related report modules.
6. Final export in `respro/report/export.py`.
7. Optional persistence to `results.db` via `respro/db/results.py`.

This separation helps keep logic testable and output generation consistent.

## Placement guidelines for new work

### Add code to `respro/core/` when

- the behavior changes biological interpretation;
- the logic should be reusable from CLI and future integrations;
- the code operates on normalized domain data rather than raw file parsing.

### Add code to `respro/io/` when

- the work is specific to a file format;
- the main responsibility is parsing, coercion, or raw input validation.

### Add code to `respro/report/` when

- the work changes presentation, export formatting, or output views;
- the result data already exists and only needs rendering/export.

### Add code to `respro/db/` when

- the change affects schema, curated project initialization, or bundle persistence.

### Add tests when

- behavior changes in any user-visible or scientifically relevant way;
- any new edge cases are discovered;
- export shape or report determinism changes.

## Current repository boundaries

- The current repository is backend- and CLI-focused.
- Keep the core package usable without assuming a UI layer.
- If a future app layer is added, it should depend on stable backend APIs rather than
  moving domain logic out of `respro/`.

## Maintenance rule

If you move files, split modules, or add major new subsystems, update this document in
that same change so the structure guide stays accurate.

