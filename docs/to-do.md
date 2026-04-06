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
- [x] Mutation normalization (`normalize_mutation`) — canonical token set covering SNPs, indels, frameshifts, wildcards, HGVS-like notation
- [x] Phenotype and clinical phenotype normalization
- [x] IC50 column support (`ic50`, `ic_50`, `fold_ic50`)
- [x] Drug deduplication — case-insensitive; biological duplicate detection for `init-add`
- [x] Combination rule sets — `resistance_rule_set` + `resistance_rule_set_member` tables; TSV `rule_group` column
- [x] `init-add` — extend existing project with new rules and optional additional GenBank annotations
- [x] PubChem integration — best-effort drug CID, canonical URL, short description; fully non-fatal

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

### Codon-aware annotation

- [x] Consequence classification — synonymous, missense, stop-gained, stop-loss, start-lost, frameshift, insertion, deletion, unknown
- [x] Strand-aware annotation — forward and reverse CDS handled correctly
- [x] Combined SNP codon events — multiple high-AF SNPs in the same codon annotated as one event
- [x] Allele-frequency binning — high / intermediate / low; customizable thresholds

### Resistance rule matching

- [x] Single-mutation rule matching — exact and wildcard (`any`) per-position matching
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

---

## Next

Items are grouped by theme and ordered by priority within each group.
Priority: 🔴 high · 🟡 medium · 🟢 low

### Code quality and maintainability

- 🟡 Split `respro/core/profile.py` — extract shared helpers (CIGAR inversion, query-sequence
  resolution) used by both VCF and FASTA pipelines into `respro/core/profile_helpers.py`; keep
  VCF-specific remapping and rename to `vcf_profile.py`
- 🟡 Split FASTA annotation logic into `respro/core/fasta_annotation.py` — keep orchestration in
  `respro/core/fasta_profile.py` and extract codon/indel consequence annotation helpers to a
  dedicated module; preserve current FASTA semantics (IUPAC AF splitting and
  insertion/deletion/frameshift handling) and update tests/imports accordingly
- 🟡 Add `markupsafe` as an explicit dependency in `pyproject.toml` — it is imported directly in
  `respro/report/html.py` but currently only present as a transitive Jinja2 dependency; explicit
  dependency prevents breakage if Jinja2 ever drops it
- 🟢 Add mypy or pyright to the dev toolchain — type hints are comprehensive throughout the
  codebase; a type checker run in CI would catch drift and wrong annotations before they reach tests
- 🟢 Increase test coverage for `respro/io/vcf.py` (currently 57%) — custom VCF parser handles
  edge cases that are not yet exercised; add tests for multi-sample columns, malformed INFO fields,
  and missing AF/DP tags before switching to pysam; specifically add a regression test confirming
  that a VCF with no AF field produces variants with `allele_freq = 1.0`
- 🟡 VCF depth fallback — `_extract_depth` returns `0` when no `DP`/`FORMAT:DP` field is found;
  with the default `--min-depth 10` filter this silently drops all variants from depth-unaware VCFs;
  fix: use a sentinel (e.g. `depth = -1`) to mark "no depth information" and skip depth filtering
  for those variants; alternatively document that users should pass `--min-depth 0` with
  depth-free VCFs

### Reference matching for partial sequences

- 🔴 Fix coverage metric in `match_query_to_genes` — coverage is currently calculated as
  `cds_aligned / len(cds)` (CDS coverage), which rejects valid matches from partial sequences
  such as Sanger reads or amplicons that cover only part of a gene; for these inputs the query
  aligns perfectly but CDS coverage may be well below the default 0.90 threshold; the fix is to
  also compute query coverage = `aligned_query_bases / len(query)` and accept a match when
  **either** CDS coverage or query coverage meets the threshold — a short query that is fully
  consumed by the alignment is a valid match regardless of gene length; rename the `coverage`
  field in `GeneMatch` to `cds_coverage` and add `query_coverage`; update the DB schema column
  in `query_gene_mapping` and the `store_mappings` / `load_cached_mappings` helpers accordingly

### Rule-position coverage gaps

- 🟡 Track unassessed rule positions in the report — once per-position depth is available (from
  BAM or N-stretch FASTA handling), cross-reference every resistance rule position against the
  depth map for the aligned gene; a position is "not assessable" if depth < `--min-depth` or if
  the codon spans an N-run; surface this per gene in the HTML report (e.g.
  *"5 of 12 rule positions not assessable due to missing coverage"*) and include a count in the
  summary header; this turns the coverage signal into actionable clinical information

### Coverage analysis (introduce together)

- 🔴 BAM-based coverage — introduce `pysam` and add a `--bam` option to `profile-vcf`; compute
  per-position depth and pass it to the gene-panel plot to shade non-covered regions below
  `--min-depth`
- 🔴 N-stretch handling in FASTA mode — treat runs of N spanning a full codon as non-covered
  rather than IUPAC-expanded; emit a coverage-gap annotation instead of fractional allele variants;
  single-N positions within an otherwise unambiguous codon remain IUPAC-expanded
- 🔴 Switch VCF parsing to `pysam.VariantFile` once pysam is a dependency — removes our own
  edge-case handling and delegates to a well-maintained library; do this in the same change as
  the BAM coverage work to avoid adding pysam twice
- 🔴 Persist non-covered regions to `results.db` — coverage gaps must survive `regenerate`; add
  a `coverage_gap` table (or JSON column on the run row) storing the list of gene positions below
  `--min-depth` or spanned by N-runs; `save_run` writes this data and `reconstruct_annotations`
  restores it so regenerated reports show the same unassessable-position warnings as the original
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

- 🟡 Results database UUID — assign a stable UUID to each `results.db` at creation time
  (analogous to the project fingerprint); store it in a `results_db` metadata table
- 🔴 Persist a per-run reproducibility manifest — store an immutable `run_manifest` in
  `results.db` containing input checksums (VCF/FASTA and project DB), effective CLI parameters,
  `respro` version, Python version, and a ruleset snapshot hash; surface it in report metadata and
  `results.json` so runs are fully reproducible/auditable
- 🟡 Prevent duplicate runs in `results.db` — deduplicate `save_run` via a stable
  `run_fingerprint` (checksum) built from canonicalized resistance hits + `sample_name` + input
  basename + `reference_name` + `project_fingerprint`; on fingerprint match, reuse existing
  `run_id` instead of inserting a second identical run
- 🟡 Show run provenance in HTML report — when a run is saved to a results DB, embed the
  results-DB UUID and run ID in the report header so the report is self-describing

### Results curation and edit workflow

- 🔴 Add user-curated phenotype labels to stored results — allow users to add post-hoc
  `clinical_phenotype` and/or `phenotype` values derived from phenotypic testing for a stored run 
  persisted in `results.db` as explicit user-added fields (`clinical_phenotype_user`, `phenotype_user`) 
  without overwriting the original rule-derived classifications
- 🟡 Add edit-history provenance for user curation — store editor, timestamp, and optional reason
  for each manual classification change so curated labels are auditable and reproducible
- 🟡 Introduce an `respro edit` command group and move regeneration under it — keep current
  regeneration functionality but prepare a clearer UX such as `respro edit --regenerate <id>` (or
  subcommand form `respro edit regenerate --identifier <id>`), then add options to apply curated
  phenotype/classification updates in the same module

### Combination rules

- 🟡 Persist combo rule hits to `results.db` — extend `save_run` and `reconstruct_annotations`
  so that combination rule hits survive `regenerate`; requires a new `combo_rule_hit` table or
  JSON column in the results schema
- 🟡 End-to-end test for combo rules via TSV `init` path — existing tests build combo rules
  manually in SQLite; add a test that runs `init_project` from a TSV with `rule_group` entries
  and verifies the loaded rule sets match expectations
- 🟢 N-of-M / OR-logic for combination rules — current AND-only semantics cannot express
  "at least 2 of these 3 mutations"; add an optional `min_members` field to the rule set so
  curators can describe partial co-occurrence resistance

### Overlapping ORFs

- 🟡 VCF remap: emit one remapped variant per matching CDS for overlapping ORFs —
  `remap_variants` in `respro/core/profile.py` currently `break`s after the first matching
  CIGAR map; if a variant position is covered by two gene alignments the second is silently
  dropped; the fix is to collect one remapped call per matching CDS and let `annotate_variants`
  disambiguate per gene (rare in practice for resistance databases, but a correctness issue for
  genuine overlapping reading frames)
- 🟡 Regression tests for overlapping ORF annotation — no test currently verifies that a
  variant falling inside two genes simultaneously produces correct, independent annotations for
  both; add tests for both the VCF and FASTA profiling paths

### Usability and workflow

- 🟡 Multi-VCF / multi-FASTA support — accept multiple `--vcf` / `--fasta` inputs in one
  invocation; run profiling per file and write one output directory per sample; useful for
  batch runs without shell scripting
- 🟡 Add short CLI option aliases alongside existing long options — introduce consistent short
  flags for frequently used arguments in `respro/cli.py` (where available and conflict-free)
  without changing current long-option names or behavior
- 🟡 Lenient rule loading — add a `--strict` flag (default off) that causes unknown gene names
  in the rules TSV to raise an error; without `--strict`, emit a warning and skip unmatched
  rules instead of aborting; keeps the fail-fast default for fresh projects while allowing
  shared rule files that span multiple references
- 🟢 Sanger AB1 input — add `respro profile-ab1` that reads an AB1 trace file directly via
  Biopython `SeqIO.read(..., 'abi')`, extracts the called sequence and per-base quality scores,
  filters low-quality positions as non-covered, then routes the result through the existing FASTA
  profiling pipeline; the main complexity is IUPAC ambiguity from overlapping forward/reverse
  traces and quality-based N-masking at the codon level
- 🟢 Summary / batch report — aggregate results across multiple runs stored in one `results.db`

### Public release

- 🔴 GitHub Actions CI — run the full test suite against all supported Python versions on every
  push to `main`; include a ruff lint check; add a PyPI publish workflow triggered by version tags
- 🔴 Signed release artifacts and provenance — publish wheel/sdist with SHA256 checksums and
  Sigstore attestation in GitHub Releases/PyPI release flow so users can verify artifact integrity
  and build provenance
- 🟡 Dependabot for dependencies and GitHub Actions — add `.github/dependabot.yml` with weekly
  update checks for `pip` and `github-actions`, grouped PRs where sensible, and automatic security
  update PRs enabled to reduce CVE exposure and dependency drift
- 🟡 Reproducibility gate in CI — run the same example profiling command twice in a fresh
  environment and assert deterministic outputs (`results.json` and report payload fields) to catch
  accidental nondeterminism before release
- 🔴 Professional documentation — concise README with a quick-start section; link out to separate
  Markdown pages for installation, database preparation (GenBank + TSV format), profiling (VCF and
  FASTA), regeneration, and output formats; follow the style of varVAMP
  (https://github.com/jonas-fuchs/varVAMP)
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
  known public resistance databases (e.g. HerpesdrG, HIVDB) and format them as ready-to-use .tsv files. 
  Also format them to .db files for the use with the respro databses CLI command.
- 🟡 Add `respro databases` CLI command — new command group that talks to the companion database
  repository via the GitHub Releases API; implement three options:
  `--list` (print available databases with version and description),
  `--download <identifier>` (fetch the TSV for a named database release to disk),
  `--path <dir>` (destination directory for the download, default: current directory);
  implement in a new `respro/io/databases.py` module using stdlib `urllib.request` to avoid new
  heavy dependencies; the companion repo must publish versioned GitHub releases with one
  structured TSV asset per pathogen/database; depends on the companion database repository
  existing and following a consistent asset naming convention

### Deferred

- 🟢 Switchable alignment backend — allow the user to choose between Biopython `PairwiseAligner`
  and `mappy` via `--aligner`; adds a C dependency and interface complexity that is not justified
  until the aligner proves to be a bottleneck in practice
- 🟢 Web UI / app layer — deferred; must depend on stable backend APIs without moving domain
  logic out of `respro/`

