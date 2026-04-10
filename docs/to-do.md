# ResistanceProfiler — To-do

Planning source of truth. Review before substantial changes.
Mark items done and update priorities after each completed milestone.

---

## Done

### Core infrastructure

- [x] SQLite-backed project database (`project.db`) with versioned schema (`PROJECT_SCHEMA_VERSION`)
- [x] SQLite-backed results database (`results.db`) for persisting runs and regenerating reports
- [x] Project fingerprint (UUID) for cross-database run validation
- [x] CLI entry points: `respro init`, `respro init-add`, `respro profile-vcf`, `respro profile-fasta`, `respro regenerate`
- [x] Verbosity control via `-v` / `-vv` flags
- [x] No functions in `__init__.py` — only module docstrings; functions in named submodules
- [x] File validation helper (`utils/files.py` → `require_file`)
- [x] Strand validation moved into `respro/io/genbank.py`

### Project initialisation

- [x] GenBank parsing — multi-record files, multiple files via repeated `--genbank`
- [x] CDS extraction — gene/protein name, coordinates, strand, `codon_start`, NT slice, AA translation
- [x] Organism and taxonomy metadata stored per reference
- [x] Multi-reference support — multiple pathogens in one project database
- [x] Rules TSV parsing and validation (all required and optional columns)
- [x] Mutation normalization (`normalize_mutation`) — canonical token set covering SNPs, indels, frameshifts, and HGVS-like notation
- [x] INDEL rule storage model switched to explicit `position` + `reference` + `mutation` alleles (legacy rewrite notation still accepted on import)
- [x] Phenotype and clinical phenotype normalization
- [x] IC50 column support — `ic50`/`ic_50` and `fold_ic50`/`fold_ic_50` stored separately; both may coexist in one file; report columns shown only when values are present; empty optional columns (ic50, fold_ic50, clinical_phenotype, source) hidden per table section
- [x] Drug deduplication — case-insensitive; biological duplicate detection for `init-add`
- [x] Combination rule sets — `resistance_rule_set` + `resistance_rule_set_member` tables; TSV `rule_group` column
- [x] `init-add` — extend existing project with new rules and optional additional GenBank annotations
- [x] PubChem integration — best-effort drug CID, canonical URL, short description; fully non-fatal
- [x] Publication table — deduplicated `publication` table + `rule_publication` / `rule_set_publication` join tables; all publications from all combo-group members collected; PMID resolved to DOI via NCBI E-utilities; title fetched from CrossRef; `--drug-info` renamed to `--additional-info` covering both drugs and publications; citation-number bibliography section in HTML report

### Profiling — VCF mode

- [x] VCF ingestion — allele frequency, read depth, filter status
- [x] Allele-frequency and depth filtering (`--min-af`, `--min-depth`)
- [x] Reference FASTA alignment via Biopython `PairwiseAligner` with CIGAR maps
- [x] CIGAR-based coordinate remapping — VCF variants from user-reference to internal CDS coordinates
- [x] Alignment result caching in `project.db` (`query_reference`, `query_gene_mapping`)
- [x] `--query-ref-header` — reuse a previously cached reference alignment
- [x] `--cache` / `--no-cache` flag to control caching behaviour
- [x] REF allele verification against active query sequence during remap

### Profiling — FASTA mode

- [x] Consensus FASTA profiling — codon-walk, amino acid diff, no VCF required
- [x] IUPAC ambiguity expansion — all possible codons enumerated; fractional `allele_freq`
- [x] SNP, in-frame insertion, in-frame deletion, and frameshift detection from FASTA
- [x] FASTA-mode AF bins — adjusted thresholds for discrete IUPAC-derived frequencies
- [x] SNP-only annotation mode — removed INDEL annotation paths from VCF/FASTA workflows and related tests
- [x] VCF in-frame insertion, in-frame deletion, and frameshift annotation — `_annotate_insertion`, `_annotate_deletion`, `_annotate_frameshift` added to `annotate_vcf.py`; mid-codon indels are non-assessable (return None); frameshift uses `alt_aa='fsX'` sentinel for rule matching

### Codon-aware annotation

- [x] Consequence classification — synonymous, missense, stop-gained, stop-loss, start-lost, frameshift, insertion, deletion, unknown
- [x] Strand-aware annotation — forward and reverse CDS handled correctly
- [x] Combined SNP codon events — multiple high-AF SNPs in the same codon annotated as one event
- [x] Allele-frequency binning — high / intermediate / low; customizable thresholds

### Resistance rule matching

- [x] Single-mutation rule matching — explicit per-position allele matching only (no wildcard token support)
- [x] Combination rule matching (`match_rule_sets`) — all members must co-occur to fire
- [x] BLOSUM62 similarity scoring for matched substitutions (`core/similarity.py`)

### Reporting and export

- [x] Standalone HTML report — Jinja2 template with inlined CSS and JS; no external assets required
- [x] Genome-overview + gene-level lollipop plot — matplotlib SVG/PNG, embedded in HTML
- [x] Mutation colour palette — consequence-typed, reused across plot and table
- [x] NT change column with changed-position highlighting (bold + underline) in FASTA mode
- [x] Client-side sortable table
- [x] Drug metadata in report — PubChem URL as clickable link
- [x] Combo rule hits displayed in report
- [x] GitHub logo linked to repository; star badge shown
- [x] JSON export — full variant + combo rule hit data (`results.json`)
- [x] TSV export removed — dead code, never exposed in CLI; `write_tsv` / `to_tsv_string` and `export.py` deleted
- [x] Deterministic output filenames — safe stem derived from input VCF/FASTA name

### Results database and regeneration

- [x] `save_run` — persist a full profiling result to `results.db`
- [x] `load_run` — reconstruct a run from `results.db`
- [x] `list_runs` — tabular CLI listing of all stored runs
- [x] `reconstruct_annotations` — rebuild `AnnotatedVariant` objects from stored rows
- [x] `respro regenerate` — re-export report from stored run with project-fingerprint validation

### Code quality and testing

- [x] Full type hints on all public APIs; `from __future__ import annotations` where needed
- [x] Frozen dataclasses for immutable result containers
- [x] Focused test suite: annotation, sequence matching, FASTA profiling, reference IO, rules, CLI, results DB, regenerate, report outputs, PubChem (fully mocked), init project
- [x] Pandas removed as dependency — stdlib `csv` used throughout
- [x] No ML references in codebase or documentation
- [x] Ruff clean — fixed F841 (unused variable in `plots.py`), W292 (missing newline in `profile.py`), I001 (import ordering); E501 excluded from CI enforcement (enforced by editor convention)
- [x] VCF AF fallback corrected — `_extract_af` now returns `1.0` (not `0.0`) when no AF field is
  present; prevents silent variant loss for germline, Sanger-derived, and simple-caller VCFs that
  carry no `AF`/`VAF`/`FREQ`/`AD` information
- [x] Split `respro/db/init_project.py` into `respro/db/project/` subpackage — `core.py` (orchestration), `genes.py` (GenBank loading), `drugs.py` (drug resolution + PubChem), `rules.py` (TSV parsing, validation, combo rules)
- [x] Move shared profiling orchestration helpers out of `respro/cli.py` into `respro/cli_helpers.py` — `_init_results_db_connection`, `_resolve_reference`, `_load_reference_data`, and `_finalize_and_export`; behavior unchanged
- [x] Split `respro/core/profile.py` — shared helpers (CIGAR inversion, query-sequence resolution) moved to `respro/core/profile_helpers.py`; VCF-specific remapping kept in `respro/core/vcf_profile.py`
- [x] Split FASTA annotation helpers into `respro/core/annotate_fasta.py` — orchestration (`profile_fasta_consensus`, `_profile_gene`) in `profile_fasta.py`; codon/indel annotation, IUPAC expansion, and consequence helpers extracted; VCF remapping in `annotate_vcf.py`
- [x] Add `markupsafe>=2.1` as an explicit dependency in `pyproject.toml`
- [x] VCF depth fallback — `_extract_depth` now returns `-1` sentinel when no depth field is found; depth filter in `profile-vcf` skips depth checking for sentinel variants so depth-free VCFs are not silently discarded
- [x] Parallel gene alignment — `match_query_to_genes` now accepts `cores` parameter; per-gene alignment extracted into picklable `_align_gene_worker`; `--cores` added to both `profile-vcf` and `profile-fasta` (default 1)
- [x] Coverage metric fix — `GeneMatch.coverage` split into `cds_coverage` and `query_coverage`; a match is accepted when identity passes AND either coverage meets the threshold; enables Sanger reads and amplicons (short queries that fully consume but cover only part of a CDS); DB column renamed `coverage` → `cds_coverage`; `query_coverage` added as optional migration column

### Coverage analysis

- [x] N-stretch handling in FASTA mode — full-codon NNN treated as non-covered; emits `CoverageGap` entries instead of IUPAC-expanded variants; partial-N codons (1–2 N) remain IUPAC-expanded; processing continues past gaps; `profile_fasta_consensus` returns `(annotations, coverage_gaps)` tuple
- [x] Unassessed rule-position reporting — cross-reference resistance-rule codon positions with FASTA `CoverageGap` entries in the HTML report summary and per-gene section; lollipop plots now shade non-covered codon spans with low alpha and include a `non covered` legend item
- [x] Persist non-covered regions to `results.db` — `coverage_gap` table (gene_name, codon_pos per run) added to results schema; `save_run` writes gaps from `ProfilingResult.coverage_gaps`; `load_coverage_gaps` restores them; `regenerate` passes gaps into the reconstructed `ProfilingResult` so regenerated reports show the same unassessable-position warnings; existing databases are migrated automatically on open
- [x] Codon stretches for coverage gaps — `CoverageGap` now stores `codon_start`/`codon_end` (inclusive range) instead of individual `codon_pos`; consecutive non-covered codons are merged into one stretch in `annotate_fasta.py`; DB schema updated to `codon_start`/`codon_end` columns; `_count_unassessed_rule_positions` uses range-based lookup; plot drawing simplified by removing `_merge_consecutive_positions`

### Usability and workflow

- [x] Add short CLI option aliases alongside existing long options — `-n`/`-g`/`-r` for `init`; `-g`/`-r` for `init-add`; `-f`/`-r`/`-s`/`-d`/`-c` for `profile-vcf`; `-f`/`-s`/`-d`/`-c` for `profile-fasta`; `-d`/`-l`/`-i` for `regenerate`; long options and behavior unchanged
- [x] Lenient rule loading — rules whose reference AA does not match the GenBank gene sequence are skipped with a warning instead of aborting; unknown gene names are also silently skipped with a warning; applies to single rules and combination rule group members

---

## Next

Items are grouped by theme and ordered by priority within each group.
Priority: 🔴 high · 🟡 medium · 🟢 low

### Coverage analysis (introduce together)

- 🔴 BAM-based coverage — introduce `pysam` and add a `--bam` option to `profile-vcf`; compute
  per-position depth and pass it to the gene-panel plot to shade non-covered regions below
  `--min-depth`
- 🔴 Switch VCF parsing to `pysam.VariantFile` once pysam is a dependency — removes our own
  edge-case handling and delegates to a well-maintained library; do this in the same change as
  the BAM coverage work to avoid adding pysam twice
- 🟡 Sequence-matching performance follow-up — after the `pysam` coverage/VCF changes land,
  benchmark end-to-end `profile-vcf` runtime on multi-reference projects and decide whether
  mappy-based reference preselection should become default instead of optional
- 🟢 Within-codon quasi-species phasing via BAM — once BAM support is in place, for codons that
  carry two or three VCF-called SNPs check whether those mutations co-occur on the same reads
  using `pysam.AlignmentFile.fetch()` over the codon window (≤3 nt, always on a single read);
  mark multi-SNP codon events as "co-occurring confirmed" when read evidence supports all
  substitutions simultaneously, or "possibly separate haplotypes" when reads show the mutations
  in mutually exclusive sets; do not attempt long-range phasing beyond codon boundaries —
  complexity grows unbounded and the within-codon case already covers the clinically relevant
  combined-codon-effect scenario; the existing AF-based combined codon annotation must remain
  the default and BAM phasing is applied only when `--bam` is provided

### Traceability

- 🟡 Per-run manifest and deduplication — store an immutable `run_manifest` JSON blob on the
  `run` row in `results.db`; the manifest captures everything needed to reproduce and deduplicate
  a run: input checksums (VCF/FASTA SHA-256, project DB SHA-256), effective CLI parameters,
  `respro` version, Python version, and a ruleset snapshot hash (SHA-256 of all canonical rule
  rows for the resolved reference); derive a stable `run_fingerprint` (SHA-256 of the canonical
  manifest) and store it alongside the manifest; on `save_run`, query for an existing row with the
  same fingerprint — if found, skip the insert and automatically regenerate the report from the
  stored run (same output as a fresh run, zero re-profiling cost) instead of just returning the
  `run_id` silently; surface the manifest in `results.json` and in the HTML report metadata so
  every exported artefact is self-describing and auditable
- 🟢 Results database UUID — assign a stable UUID to each `results.db` at creation time
  (analogous to the project fingerprint); store it in a `results_db` metadata table; prerequisite
  for run provenance in the HTML report
- 🟢 Show run provenance in HTML report — embed the results-DB UUID and run ID in the report
  header so the report is self-describing; depends on results database UUID

### Combination rules

- 🔴 Persist combo rule hits to `results.db` — extend `save_run` so combination rule hits survive
  `regenerate`; regenerated reports currently silently drop all combo hits; add a
  `combo_rule_hit` table or JSON column in the results schema and restore hits in
  `reconstruct_annotations`
- 🔴 End-to-end test for combo rules via TSV `init` path — existing tests build combo rules
  manually in SQLite; add a test that runs `init_project` from a TSV with `rule_group` entries
  and verifies the loaded rule sets match expectations; required before extending combo rule
  persistence
- 🟡 Regression tests for combo rules with shared substitutions — no test currently verifies
  that a variant shared between two rule sets causes both to fire independently with the shared
  `AnnotatedVariant` appearing in both `ComboRuleHit.matched_variants`; add focused tests in
  `TestMatchRuleSets` covering: (a) two rule sets that share one member mutation both fire when
  all members are present, (b) only the rule set whose unique member is present fires when the
  shared mutation is present but one rule set's unique member is absent
- 🟢 Uniqueness constraint on `resistance_rule_set_member` — the schema lacks a UNIQUE
  constraint on `(rule_set_id, gene_id, position, mutation)`; a malformed rule set with
  duplicate members would allow a single variant to satisfy multiple member slots,
  letting a rule fire with fewer distinct mutations than intended; add a DB-level UNIQUE
  constraint and dedup validation in `_insert_combo_rule_sets` before inserting members
- 🟢 N-of-M / OR-logic for combination rules — current AND-only semantics cannot express
  "at least 2 of these 3 mutations"; add an optional `min_members` field to the rule set so
  curators can describe partial co-occurrence resistance

### Overlapping ORFs

- 🟡 VCF remap: emit one remapped variant per matching CDS for overlapping ORFs —
  `remap_variants` in `respro/core/profile_vcf.py` currently `break`s after the first matching
  CIGAR map; if a variant position is covered by two gene alignments the second is silently
  dropped; the fix is to collect one remapped call per matching CDS and let `annotate_variants`
  disambiguate per gene (rare in practice for resistance databases, but a correctness issue for
  genuine overlapping reading frames)
- 🟡 Regression tests for overlapping ORF annotation — no test currently verifies that a
  variant falling inside two genes simultaneously produces correct, independent annotations for
  both; add tests for both the VCF and FASTA profiling paths

### Reference selection performance (multi-reference)

- 🔴 mappy-backed reference preselection — in projects with multiple references, avoid full
  per-gene alignment across every reference by first selecting likely candidate references with
  mappy/minimizer mapping, then run the existing detailed alignment only on selected candidates;
  prioritize this directly after `pysam` integration to prevent multi-reference runtime blowups;
  reassess whether multiprocessing remains useful after moving to mappy
- 🟢 Aligner policy after `pysam` adoption — once `pysam` is required for coverage and VCF
  parsing, reassess C-extension footprint and packaging path (PyPI/Bioconda) and keep `--aligner`
  behavior explicit while transitioning toward a mappy-first default if benchmarks support it

### Usability and workflow

- 🟡 Multi-VCF / multi-FASTA support — accept multiple `--vcf` / `--fasta` inputs in one
  invocation; run profiling per file and write one output directory per sample; useful for
  batch runs without shell scripting
- 🟢 Sanger AB1 input — add `respro profile-ab1` that reads an AB1 trace file via
  `SeqIO.read(..., 'abi')` and derives a quality-aware consensus sequence that feeds directly into
  the existing FASTA profiling pipeline; the quality model uses raw trace peak data
  (`abif_raw['DATA9–12']` for G/A/T/C channels, `abif_raw['PLOC1']` for per-base peak positions)
  and applies a three-tier classification per base position:
  (1) **Confident call** — the dominant channel contributes >80% of total peak height at that
  position: emit the called base unchanged;
  (2) **Short ambiguous stretch (1–3 consecutive positions)** flanked on both sides by confident
  calls — one or more secondary channels exceed a relative threshold (~33% of the dominant peak):
  determine the set of active channels and map them to the correct IUPAC degenerate code (e.g.
  A+G → R, C+T → Y, A+C+G → V); the existing `_expand_iupac_codon` and allele-frequency binning
  already handle these correctly, so a two-peak overlap at 50% naturally yields AF=0.5, capturing
  clinically relevant minority variants in mixed viral populations;
  (3) **Non-covered (N)** — absolute peak height below a noise floor (e.g. 5% of read maximum)
  regardless of ratio, or an ambiguous stretch longer than 3 positions: emit `N`; existing
  N-stretch coverage gap handling in FASTA mode already covers these correctly;
  thresholds (noise floor, relative secondary-peak cutoff) should be configurable as CLI flags
  (`--ab1-min-signal`, `--ab1-ambiguity-cutoff`) with conservative defaults; Phred quality
  (`letter_annotations['phred_quality']`) can serve as a fast pre-filter (Phred < 20 → ambiguous)
  before the trace-peak analysis for positions that passed Phred but still show secondary peaks
- 🟢 Summary / batch report — aggregate results across multiple runs stored in one `results.db`

### Code quality and maintainability

- 🟢 Add mypy or pyright to the dev toolchain — type hints are comprehensive throughout the
  codebase; a type checker run in CI would catch drift and wrong annotations before they reach tests
- 🟢 Increase test coverage for `respro/io/vcf.py` (currently 57%)

### Public release

- 🔴 GitHub Actions CI — run the full test suite against all supported Python versions on every
  push to `main`; include a ruff lint check; add a PyPI publish workflow triggered by version tags
- 🔴 Professional documentation — concise README with a quick-start section; link out to separate
  Markdown pages for installation, database preparation (GenBank + TSV format), profiling (VCF and
  FASTA), regeneration, and output formats; follow the style of varVAMP
  (https://github.com/jonas-fuchs/varVAMP)
- 🟡 Dependabot for dependencies and GitHub Actions — add `.github/dependabot.yml` with weekly
  update checks for `pip` and `github-actions`, grouped PRs where sensible, and automatic security
  update PRs enabled to reduce CVE exposure and dependency drift
- 🟡 Reproducibility gate in CI — run the same example profiling command twice in a fresh
  environment and assert deterministic outputs (`results.json` and report payload fields) to catch
  accidental nondeterminism before release
- 🟡 Signed release artifacts and provenance — publish wheel/sdist with SHA256 checksums and
  Sigstore attestation in GitHub Releases/PyPI release flow so users can verify artifact integrity
  and build provenance; depends on CI being in place
- 🟡 Example data for new users — add a small, self-contained example dataset (GenBank file,
  rules TSV, and a matching VCF or consensus FASTA) to the repository so users can follow the
  quick-start guide end-to-end without sourcing their own data; keep the files small enough to
  live in `example/` without bloating the repo (ideally < 1 MB total)
- 🟡 GitHub Pages example report — add a GitHub Actions workflow triggered on each versioned
  release that runs `respro` against the example data, renders the HTML report, and publishes it
  to GitHub Pages; gives prospective users a live, always-current preview of the report output
  without downloading anything
- 🟡 Bioconda package — write a Bioconda recipe (`meta.yaml`) and submit a PR to
  bioconda-recipes; Bioconda is the standard distribution channel for bioinformatics CLI tools
  and avoids requiring users to have a working pip/Python setup; dependency on pysam makes
  Bioconda the natural distribution path once pysam is a requirement
- 🟡 Add `CHANGELOG.md` — document version history with a Keep-a-Changelog format entry for each
  release; required for PyPI credibility and for users tracking what changed between database
  snapshots
- 🟢 README header badges — add coverage, Python version, license, PyPI version, and
  Bioconda/conda version badges to the README header; coverage badge requires a codecov or
  coveralls integration in CI; PyPI and Bioconda badges are available once packages are published
- 🟢 PyPI release — changelog, version bump, and hatch-based build after CI and docs are in place

### Databases

- 🟡 Companion database repository — separate public GitHub repo with automated bots that scrape
  known public resistance databases (e.g. HerpesdrG, HIVDB) and format them as ready-to-use .tsv
  files; also pre-build `.db` files for direct use with the `respro databases` CLI command; the
  repo's CI must monitor two triggers independently: (1) upstream database content changes, which
  trigger a TSV/DB rebuild for the current `PROJECT_SCHEMA_VERSION`, and (2) new `respro` releases,
  which must be checked for a `PROJECT_SCHEMA_VERSION` bump — if the schema version has increased,
  all databases must be rebuilt against the new schema and released as new assets; old `.db` assets
  built against earlier schema versions must be retained in prior releases (not deleted) so that
  users pinned to an older `respro` version can still download a compatible database; each release
  asset filename and metadata must embed the `PROJECT_SCHEMA_VERSION` it was built with (e.g.
  `hsv1_schema1.db`) so that both humans and the CLI can identify compatibility at a glance
- 🟡 Add `respro databases` CLI command — new command group that talks to the companion database
  repository via the GitHub Releases API; implement three options:
  `--list` (print available databases with version and description),
  `--download <identifier>` (fetch the TSV or DB for a named database release to disk),
  `--path <dir>` (destination directory for the download, default: current directory);
  `--list` must filter assets by the running `PROJECT_SCHEMA_VERSION` and only show databases
  whose schema version matches — databases built for a different schema are silently omitted from
  the list (a `--all` flag can expose them with a compatibility warning); implement in a new
  `respro/io/databases.py` module using stdlib `urllib.request` to avoid new heavy dependencies;
  depends on the companion database repository existing and following a consistent asset naming
  convention that encodes `PROJECT_SCHEMA_VERSION`

### Deferred

- 🟢 User-curated phenotype labels, edit-history provenance, and `respro edit` command group —
  deferred together; these three items form a coherent edit workflow that should be designed as
  a unit; not needed during core build-out and adds significant schema and UX complexity; revisit
  once the core profiling and reporting pipeline is stable and released
- 🟢 Web UI / app layer — deferred; must depend on stable backend APIs without moving domain
  logic out of `respro/`

