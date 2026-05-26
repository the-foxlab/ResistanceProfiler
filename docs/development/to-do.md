# ResistanceProfiler — To-do

Planning source of truth. Review before substantial changes.
Mark items done and update priorities after each completed milestone.

---

## Done

### Core infrastructure

- [X] SQLite-backed project database (`project.db`) with versioned schema (`PROJECT_SCHEMA_VERSION`)
- [X] SQLite-backed results database (`results.db`) for persisting runs and regenerating reports
- [X] Project fingerprint (UUID) for cross-database run validation
- [X] CLI entry points: `respro init`, `respro add`, `respro vcf`, `respro fasta`, `respro regenerate`
- [X] Verbosity control via `-v` / `-vv` flags
- [X] No functions in `__init__.py` — only module docstrings; functions in named submodules
- [X] File validation helper (`utils/files.py` → `require_file`)
- [X] Strand validation moved into `respro/io/genbank.py`

### Project initialisation

- [X] GenBank parsing — multi-record files, multiple files via repeated `--genbank`
- [X] CDS extraction — gene/protein name, coordinates, strand, `codon_start`, NT slice, AA translation
- [X] Organism and taxonomy metadata stored per reference
- [X] Multi-reference support — multiple pathogens in one project database
- [X] Rules TSV parsing and validation (all required and optional columns)
- [X] Mutation normalization (`normalize_mutation`) — canonical token set covering SNPs, indels, frameshifts, and HGVS-like notation
- [X] INDEL rule storage model switched to explicit `position` + `reference` + `mutation` alleles (legacy rewrite notation still accepted on import)
- [X] Phenotype and clinical phenotype normalization
- [X] IC50 column support — `ic50`/`ic_50` and `fold_ic50`/`fold_ic_50` stored separately; both may coexist in one file; report columns shown only when values are present; empty optional columns (ic50, fold_ic50, clinical_phenotype, source) hidden per table section
- [X] Drug deduplication — case-insensitive; biological duplicate detection for `add`
- [X] Stable drug badge colors persisted in `drug.badge_color` during rules import for consistent report styling across runs/regeneration
- [X] Combination rule sets — `resistance_rule_set` + `resistance_rule_set_member` tables; TSV `rule_group` column
- [X] Formula-rule import scaffold — grouped atomic rules support `group_id` + unique `member_id`; `respro init` / `respro add` accept optional `--formula-rules` TSV with boolean `AND` / `OR` / `NOT` / `XOR` expressions, normalized formula storage, strict group-to-formula validation, and warning-only behavior when grouped rows are provided without a formula TSV
- [X] `add` — extend existing project with new rules and optional additional GenBank annotations
- [X] PubChem integration — best-effort drug CID, canonical URL, short description; fully non-fatal
- [X] Publication table — deduplicated `publication` table + `rule_publication` / `rule_set_publication` join tables; all publications from all combo-group members collected; PMID resolved to DOI via NCBI E-utilities; title fetched from CrossRef; `--drug-info` renamed to `--additional-info` covering both drugs and publications; citation-number bibliography section in HTML report

### Profiling — VCF mode

- [X] VCF ingestion — allele frequency, read depth, filter status
- [X] Allele-frequency and depth filtering (`--min-af`, `--min-depth`)
- [X] Reference FASTA alignment via minimap2 `mappy` backend with CIGAR maps
- [X] CIGAR-based coordinate remapping — VCF variants from user-reference to internal CDS coordinates
- [X] Alignment result caching in `project.db` (`query_reference`, `query_gene_mapping`)
- [X] Query-reference cache reuse on repeated FASTA inputs
- [X] `--cache` / `--no-cache` flag to control caching behaviour
- [X] REF allele verification against active query sequence during remap
- [X] Strand-aware VCF remap anchor model — nucleotide anchor projection and amino-acid anchor context are handled separately; reverse-orientation indels now switch anchor side correctly during remap

### Profiling — FASTA mode

- [X] Consensus FASTA profiling — codon-walk, amino acid diff, no VCF required
- [X] IUPAC ambiguity expansion — all possible codons enumerated; fractional `allele_freq`
- [X] SNP, in-frame insertion, in-frame deletion, and frameshift detection from FASTA
- [X] FASTA-mode AF bins — adjusted thresholds for discrete IUPAC-derived frequencies
- [X] SNP-only annotation mode — removed INDEL annotation paths from VCF/FASTA workflows and related tests
- [X] VCF in-frame insertion, in-frame deletion, and frameshift annotation — `_annotate_insertion`, `_annotate_deletion`, `_annotate_frameshift` added to `annotate_vcf.py`; mid-codon indels are non-assessable (return None); frameshift uses `alt_aa='fsX'` sentinel for rule matching
- [X] VCF indel strand-aware query anchor — `remap_variants` now passes all variant types through (non-SNP skip removed); allele strand-flip uses `_transform_allele` (anchor complement + payload RC); `query_ref_codon` populated for all variant types; indel annotation handlers use query codon as anchor AA when available

### Alignment performance

- [X] mappy/minimap2 alignment backend standardized for profiling; benchmarked on HSV whole-genome (152 KB)
  and partial FASTAs; all 8 resistance genes found with equivalent identity scores; 440×–14 000× faster on
  large sequences; pairwise backend and `--aligner` selection were removed after equivalence validation;
  CIGAR convention verified compatible with downstream VCF remap and coordinate mapping; `mappy>=2.24`
  added as a dependency

### Codon-aware annotation

- [X] Consequence classification — synonymous, missense, stop-gained, stop-loss, start-lost, frameshift, insertion, deletion, unknown
- [X] Strand-aware annotation — forward and reverse CDS handled correctly
- [X] Combined SNP codon events — multiple high-AF SNPs in the same codon annotated as one event
- [X] Allele-frequency binning — high / intermediate / low; customizable thresholds

### Split/joined GenBank CDS support

- [X] Phase 1 (database representation): add split/joined CDS persistence with a `gene_segment` model/table, parse GenBank compound CDS parts, and keep contiguous genes unchanged.
- [X] Phase 2 (annotation logic) — VCF/FASTA coordinate mapping is segment-aware so only coding regions are assessed; envelope positions outside CDS segments are skipped as non-coding
- [X] Phase 3 (report representation): render split genes as graphical multi-part blocks only (no textual part labels), with 5-prime to 3-prime ordering following reference genomic orientation.
- [X] Phase 4 (results/regenerate): split-gene annotations and multi-block report rendering survive `results.db` persistence and `respro regenerate` roundtrips.
- [X] Regression coverage for split genes spans persistence/regenerate roundtrips, including an explicit negative-strand split CDS case.

### Resistance rule matching

- [X] Single-mutation rule matching — explicit per-position allele matching only (no wildcard token support)
- [X] Combination rule matching (`match_rule_sets`) — all members must co-occur to fire
- [X] Formula-rule first-class workflow outputs — `resistance_formula_rule` import is wired into profiling-time matching, report rendering, results DB persistence, regenerate, and WebUI
- [X] BLOSUM62 similarity scoring for matched substitutions (`core/similarity.py`)
- [X] End-to-end combo rule loading via TSV `init` path — `init_project` + `rule_group` rows tested through `load_rule_sets` (no manual SQL setup)

### Reporting and export

- [X] Standalone HTML report — Jinja2 template with inlined CSS and JS; no external assets required
- [X] Genome-overview + gene-level lollipop plot — matplotlib SVG/PNG, embedded in HTML
- [X] Mutation colour palette — consequence-typed, reused across plot and table
- [X] NT change column with changed-position highlighting (bold + underline) in FASTA mode
- [X] Client-side sortable table
- [X] Client-side table column filter controls (dropdown column selector + text filter)
- [X] Expandable per-row local coding-direction alignment snippets in HTML report tables with mutation highlighting
- [X] `|` match bars restored in row-level FASTA/VCF alignment snippets based on the final displayed ref/query rows
- [X] Drug metadata in report — PubChem URL as clickable link
- [X] Combo rule hits displayed in report
- [X] GitHub logo linked to repository; star badge shown
- [X] Optional CLI exports — `respro vcf` and `respro fasta` now support `--export json|tabular|pdf` while always writing HTML
- [X] JSON export — full report-context output (`*.results.json`) including summary tables, annotations, combo hits, and bibliography
- [X] Tabular export — database-hit table as TSV (`*.mutations.tsv`) with one row per matched database rule hit
- [X] Deterministic output filenames — safe stem derived from input VCF/FASTA name
- [X] Bilingual interpretation summary section in HTML report — concise EN/DE "Befundtext" above mutation overview with phenotype/clinical evidence counts per drug, IC50/Fold-IC50 range mentions, similarity evidence summary, and high-impact variant warning

### Results database and regeneration

- [X] `save_run` — persist a full profiling result to `results.db`
- [X] `load_run` — reconstruct a run from `results.db`
- [X] `list_runs` — tabular CLI listing of all stored runs
- [X] `reconstruct_annotations` — rebuild `AnnotatedVariant` objects from stored rows
- [X] Persist formula rule hits to `results.db` and restore them on `regenerate` via `formula_rule_hit` rows + reconstruction
- [X] `respro regenerate` — re-export report from stored run with project-fingerprint validation
- [X] `respro regenerate --json` — regenerate report artifacts directly from exported `*.results.json` with strict JSON validation and UUID mismatch guardrails
- [X] `respro regenerate --export json|tabular|pdf` — parity export options when regenerating from stored runs or JSON input

### Code quality and testing

- [X] Full type hints on all public APIs; `from __future__ import annotations` where needed
- [X] Frozen dataclasses for immutable result containers
- [X] Focused test suite: annotation, sequence matching, FASTA profiling, reference IO, rules, CLI, results DB, regenerate, report outputs, PubChem (fully mocked), init project
- [X] Pandas removed as dependency — stdlib `csv` used throughout
- [X] No ML references in codebase or documentation
- [X] Ruff clean — fixed F841 (unused variable in `plots.py`), W292 (missing newline in `profile.py`), I001 (import ordering); E501 excluded from CI enforcement (enforced by editor convention)
- [X] VCF AF fallback corrected — `_extract_af` now returns `1.0` (not `0.0`) when no AF field is
  present; prevents silent variant loss for germline, Sanger-derived, and simple-caller VCFs that
  carry no `AF`/`VAF`/`FREQ`/`AD` information
- [X] Split `respro/db/init_project.py` into `respro/db/project/` subpackage — `core.py` (orchestration), `genes.py` (GenBank loading), `drugs.py` (drug resolution + PubChem), `rules.py` (TSV parsing, validation, combo rules)
- [X] Move shared profiling orchestration helpers out of `respro/cli.py` into `respro/cli_helpers.py` — `_init_results_db_connection`, `_resolve_reference`, `_load_reference_data`, and `_finalize_and_export`; behavior unchanged
- [X] Split `respro/core/profile.py` — shared helpers (CIGAR inversion, query-sequence resolution) moved to `respro/core/profile_helpers.py`; VCF-specific remapping kept in `respro/core/vcf_profile.py`
- [X] Split FASTA annotation helpers into `respro/core/annotate_fasta.py` — orchestration (`profile_fasta_consensus`, `_profile_gene`) in `profile_fasta.py`; codon/indel annotation, IUPAC expansion, and consequence helpers extracted; VCF remapping in `annotate_vcf.py`
- [X] mappy alignment backend consolidation — pairwise backend helpers/options were removed; gene matching
  now uses a mappy-only implementation with verified CIGAR I/D convention and equivalent coordinate mapping;
  test coverage retained for mappy backend behavior after backend-option removal
- [X] Add `markupsafe>=2.1` as an explicit dependency in `pyproject.toml`
- [X] VCF depth fallback — `_extract_depth` now returns `-1` sentinel when no depth field is found; depth filter in `profile-vcf` skips depth checking for sentinel variants so depth-free VCFs are not silently discarded
- [X] Parallel gene alignment — `match_query_to_genes` now accepts `cores` parameter; per-gene alignment extracted into picklable `_align_gene_worker`; `--cores` added to both `profile-vcf` and `profile-fasta` (default 1)
- [X] Coverage metric fix — `GeneMatch.coverage` split into `cds_coverage` and `query_coverage`; a match is accepted when identity passes AND either coverage meets the threshold; enables Sanger reads and amplicons (short queries that fully consume but cover only part of a CDS); DB column renamed `coverage` → `cds_coverage`; `query_coverage` added as optional migration column
- [X] Centralize runtime literals into config modules — bundled CLI/core API URL + timeout defaults moved to `respro/config/defaults.toml` (packaged via `pyproject.toml`), web backend defaults moved to `web/backend/defaults.toml` with env-key + default loading in `web/backend/config.py`, and frontend API/profile defaults consolidated in `web/frontend/src/config.js`
- [X] FASTA nucleotide-level variant emission refactoring — replaced codon-based variant emission logic in `respro/core/fasta_to_vcf.py` with straightforward nucleotide-level VariantCall pipeline; removed ~200 lines of state-machine logic with `_assessable_nt_indices`, `_collect_deletion_runs`, `_emit_variants_from_coding`, and codon-specific helpers; renamed `profile_fasta_consensus()` → `fasta_to_vcf()` returning `(VariantCall[], CoverageGap[])`; added `is_fasta_mode` parameter to `annotate_variants()` for proper FASTA mode tracking; both VCF and FASTA now feed the same annotation pipeline; test cleanup eliminated 4 obsolete test classes and 73 obsolete test methods; architecture docs updated; all 612 tests pass

### Coverage analysis

- [X] N-stretch handling in FASTA mode — full-codon NNN treated as non-covered; emits `CoverageGap` entries instead of IUPAC-expanded variants; partial-N codons (1–2 N) remain IUPAC-expanded; processing continues past gaps; `profile_fasta_consensus` returns `(annotations, coverage_gaps)` tuple
- [X] Unassessed rule-position reporting — cross-reference resistance-rule codon positions with FASTA `CoverageGap` entries in the HTML report summary and per-gene section; lollipop plots now shade non-covered codon spans with low alpha and include a `non covered` legend item
- [X] Persist non-covered regions to `results.db` — `coverage_gap` table (gene_name, codon_pos per run) added to results schema; `save_run` writes gaps from `ProfilingResult.coverage_gaps`; `load_coverage_gaps` restores them; `regenerate` passes gaps into the reconstructed `ProfilingResult` so regenerated reports show the same unassessable-position warnings; existing databases are migrated automatically on open
- [X] Codon stretches for coverage gaps — `CoverageGap` now stores `codon_start`/`codon_end` (inclusive range) instead of individual `codon_pos`; consecutive non-covered codons are merged into one stretch in `annotate_fasta.py`; DB schema updated to `codon_start`/`codon_end` columns; `_count_unassessed_rule_positions` uses range-based lookup; plot drawing simplified by removing `_merge_consecutive_positions`
- [X] BAM-based VCF coverage projection — `profile-vcf` supports `--bam` and projects query BAM depth to internal codon coordinates via CIGAR mappings; codons with missing projection or depth below `--min-depth` are emitted as `CoverageGap` stretches and rendered/reported like FASTA non-covered regions
- [X] VCF parser migration to `pysam.VariantFile` — custom parser removed; VCF ingestion now uses pysam exclusively for multi-allelic records, AF extraction, and depth extraction

### Usability and workflow (data processing)

- [X] Add short CLI option aliases alongside existing long options — `-n`/`-g`/`-r` for `init`; `-g`/`-r` for `init-add`; `-f`/`-r`/`-s`/`-d`/`-c` for `profile-vcf`; `-f`/`-s`/`-d`/`-c` for `profile-fasta`; `-d`/`-l`/`-i` for `regenerate`; long options and behavior unchanged
- [X] Lenient rule loading — rules whose reference AA does not match the GenBank gene sequence are skipped with a warning instead of aborting; unknown gene names are also silently skipped with a warning; applies to single rules and combination rule group members
- [X] Canonical VCF orientation acceptance suite (E1-E7) — full matrix coverage for +/+ , +/- , -/+ , -/- constellations plus query insertion/deletion and mismatch-column projection cases
- [X] Combo-rule shared-substitution regression tests — `TestMatchRuleSets` verifies that two rule sets sharing one mutation both fire when complete (shared `AnnotatedVariant` appears in both hits), and only the fully satisfied set fires when one unique member is absent
- [X] Combo member uniqueness enforcement — duplicate `resistance_rule_set_member` entries are rejected via DB-level unique index `(rule_set_id, gene_id, position, mutation)` and pre-insert duplicate validation in `_insert_combo_rule_sets`

### CLI restructure and results management

- [X] Flat CLI structure — `respro manage database`/`results`, `respro regenerate`, and `respro classify` as top-level commands; sync moved under `respro manage results --sync`; old `respro/cli/runs.py` and `respro/cli/rules.py` deleted; new `respro/cli/explore.py`, `respro/cli/regenerate.py`, `respro/cli/classify.py`, `respro/cli/sync.py` modules created
- [X] `respro manage database <db_path> --rules` — browse resistance rules in project database with optional `--reference` filter; smart column hiding (only show columns with at least one non-empty value)
- [X] `respro manage results <results_db_path> --list` — browse stored profiling runs with stale indicator (yellow if project was updated after run creation)
- [X] Project `updated_at` tracking — `updated_at` added to `project` table; bumped on `init-add`; `project_updated_at` snapshot stored on `run`; stale indicator in `manage results --list` when project was updated after the run was recorded
- [X] `sample_classification` table in `results.db` — `run_id`, `drug`, `phenotype`, `clinical_phenotype`, `ic50`, `fold_ic50`, `note`, `source`, `created_at`; auto-migration on DB open; `save_classification` / `load_classifications` in `respro/db/results.py`
- [X] `respro classify` — top-level command; `--run-id`, optional `--drug`, `--phenotype` / `--clinical-phenotype` / `--ic50` / `--fold-ic50`; at least one value required; appends `sample_classification` row
- [X] `respro regenerate` — top-level command; re-exports report from stored run with project-fingerprint validation
- [X] `respro manage results <results_db_path> --sync <project_db_path>` — re-annotates all stored runs against current project DB; replaces variant_result and combo-hit rows; updates resistance_hits; runs with fingerprint mismatches are skipped and reported
- [X] Surface sample classifications in report and JSON — dedicated "Manual classifications" section in HTML report and `sample_classifications` key in exported JSON, clearly separated from rule-based hits
- [X] Optional database metadata in `respro init` — `--metadata` accepts validated JSON with fixed keys (`maintainers`, `contact`, `publication_pmid`, `website`, `description`, `maintainer_update`, `license`, `tsv_checksum`); PMID values are DOI-enriched best-effort at creation time and stored on the project row
- [X] `respro manage database <db_path> --info` — added project metadata inspection mode that prints non-empty project identity and curated metadata fields
- [X] Interpretation algorithm metadata in `metadata.json` — `interpretation_algorithms` top-level array in metadata JSON accepts three coexisting algorithm types (`ic50_thresholds`, `drug_groups`, `drug_interpretation`); each is validated on import and stored in a new `interpretation_algorithm` table in `project.db`; `load_interpretation_algorithms` exposes the config to downstream consumers (report, scoring); full test coverage in `tests/test_algorithms.py`; documented in `docs/user/database-preparation.md`

### Web deployment and security (done)

- [X] Configurable CORS origins — `RESPRO_WEB_CORS_ORIGINS` now accepts a comma-separated origin list at startup; backend defaults to localhost development origins when no API token is set and to `*` only when `RESPRO_WEB_API_TOKEN` is configured; compose and deployment docs include configuration guidance
- [X] Upload rate limiting — web uploads are now rate limited via `slowapi`; `RESPRO_WEB_UPLOAD_RATE_LIMIT` configures the limit, token-authenticated requests are keyed by token, unauthenticated requests fall back to client IP, and deployment docs/compose include configuration guidance
- [X] Upload input validation hardening — FASTA/VCF upload validation now rejects binary content, enforces structural checks and line-length caps, and requires both `##fileformat` + `#CHROM` headers for VCF; BAM validation now verifies BGZF header structure (not just two-byte magic); parser-heavy profiling steps wrap FASTA/VCF/BAM parser failures into explicit user-facing errors
- [X] Add explicit startup policy validation for auth and CORS
- [X] Require explicit web API authentication in non-local deployments
- [X] Restrict profiling input paths to trusted roots
- [X] Remove token transport via query string
- [X] Remove query-token auth fallback from non-route surfaces
- [X] Tighten authenticated CORS defaults
- [X] Ephemeral web mode as the only default path (no login)
- [X] Remove mandatory `results.db` dependency from web profiling
- [X] Configurable worker concurrency and env-tunable CPU/memory limits in `docker-compose.web.yml`, with compose compatibility comments for local vs Swarm-style limit handling
- [X] HTTPS/reverse-proxy hosting guidance with ready-to-copy Caddy/nginx examples and deployment notes in `docs/user/webapp-hosting.md`
- [X] Optional trusted proxy support (`RESPRO_WEB_TRUSTED_PROXIES`) wired into uvicorn proxy settings so forwarded client IP headers are only trusted when explicitly configured

### WebUI (quality and testing)

- [X] FastAPI profiling endpoints with async job queue — `POST /api/profile/fasta` and `POST /api/profile/vcf` enqueue RQ jobs and return a `job_id`; `GET /api/jobs/{job_id}` exposes status/result; RQ worker executes jobs using `respro/` domain logic; Redis is the broker; `fakeredis` + `Queue(is_async=False)` used for test isolation
- [X] Rules browser API — `GET /api/rules` with optional reference filter backed by `respro.db.rules_queries.list_rules_for_display`
- [X] Report integration — existing HTML report served via `GET /api/report?path=...`; frontend opens it in a new tab
- [X] Frontend styling parity with existing report template
- [X] Local file/path UX — `/api/fs/list` filesystem browser; Browse buttons on all path inputs; app binds to localhost by default
- [X] Web install/start workflow — `web/backend/requirements.txt` for web deps; Docker Compose with `redis`, `respro-web`, and `respro-worker` services
- [X] Web UI architecture scaffold — FastAPI backend + React frontend in `web/`; web layer decoupled from PyPI packaging
- [X] Startup workspace bootstrap — `RESPRO_WEB_DATA_DIR` now creates deterministic subfolders (`project_databases/`, `uploads/`, `results/`) and keeps route-level path confinement aligned with those roots
- [X] Startup results DB initialization — `results/results.db` created/opened at backend startup
- [X] Remove workspace tile flow end-to-end — workspace form/UI, `/api/workspace/open`, and workspace payload fields removed
- [X] Wire startup config into profiling routes/jobs — startup config used by `/api/rules`, `/api/profile/fasta`, `/api/profile/vcf`; request bodies contain only analysis inputs
- [X] Web-layer tests for startup-only mode — startup-config fixture, auth header coverage, 14 tests passing
- [X] Prototype distribution path for bundled DB — `data/project_databases/*.db` is the catalog location; mounted via `./data:/data` in Docker
- [X] Multipart file upload endpoints — `POST /api/upload/fasta` and `POST /api/upload/vcf` with validation, temp storage in `data/uploads/`, auth enforcement
- [X] Streamed upload persistence in web backend — `/api/upload/fasta`, `/api/upload/vcf`, and `/api/upload/bam` now validate and persist uploads chunk-by-chunk to avoid loading whole files into memory; existing size caps and user-facing validation errors preserved
- [X] Autoload first database and mutations on app startup — frontend loads database list on mount, selects first DB automatically, triggers mutations load to avoid manual "Load" button click
- [X] Filter/sort mutations table with client-side search — column selector dropdown, text search input, reset button; compatible with report UI filter pattern; click headers to sort (↕ ↑ ↓ indicators)
- [X] Processing spinner during profiling jobs — animated spinner SVG appears on Run button while job is queued/running; disabled button state prevents secondary submissions
- [X] Upload progress tracking — XMLHttpRequest-based progress events display percentage bar during file uploads (FASTA/VCF/BAM); smooth transitions to 100% on completion
- [X] BAM file upload support — new `/api/upload/bam` endpoint with BAM magic byte validation (BAM\x01); supports files up to 1GB; integrated into VCF profiling card as optional coverage input
- [X] Job cancel endpoint and UI action — added `DELETE /api/jobs/{job_id}` to cancel queued/running RQ jobs (`job.cancel()` for queued and `job.kill_worker()` fallback-to-fail for started jobs), return `204/404` semantics, and a frontend cancel button shown only while jobs are queued/running
- [X] Unified dashboard shell and in-app report integration — frontend now uses a cohesive scientific dashboard with global database card, left mode sidebar (Profile VCF, Profile FASTA, Browse mutations, Report), app-level branding links/logo/favicon, and report viewing in an in-app modal instead of opening new browser tabs; report CSS harmonized with web app styling tokens
- [X] Database analytics tiles in Web UI — Database tab now shows structured metadata cards plus a responsive 2-column plot grid with per-reference/gene mutation-position charts and optional IC50/drug summary plots derived from the loaded rules
- [X] Report artifact downloads in web app — profiling jobs now emit HTML + JSON + tabular outputs with PDF generated by default; report panel adds direct JSON/tabular download buttons plus Download PDF, and backend `/api/artifact` serves non-HTML files from the allowed results directory (not the broader data directory)
- [X] Regenerate-from-JSON web flow — `POST /api/upload/json` + `POST /api/regenerate/json` with JSON schema validation, UUID mismatch feedback, and shared report artifact payloads
- [X] Dedicated "Regenerate from JSON" frontend tab — simple JSON upload + regenerate action; output rendered in the same report tile with JSON/tabular downloads
- [X] Project DB catalog and runtime selection in web app — `/api/databases` now enumerates all valid `project_databases/*.db` files, the database dropdown drives `/api/mutations` and profile/regenerate submissions via `database_id`, and startup supports optional maintained-db bootstrap (`RESPRO_WEB_MAINTAINED_BOOTSTRAP`, default off, missing-only)
- [X] `respro manage results <results_db_path> --delete <run_id>` — delete one stored run (including `variant_result`, `coverage_gap`, `formula_rule_hit`, and `sample_classification` rows) from `results.db` with optional `--force` confirmation bypass
- [X] Hide internal formula-component placeholder rows from user-facing rule/drug displays and internalize the marker handling
- [X] `respro add --validate` dry-run mode — execute full rules parsing/validation pipeline without persisting DB changes
- [X] Job status contract hardening — standardized and tested queued/running/succeeded/failed mapping with consistent failed-job and missing-job error payload behavior for `/api/jobs/{job_id}`
- [X] Queue runtime safeguards — added configurable queue timeout/retry defaults plus explicit enqueue/start/fail/finish lifecycle logging for background jobs
- [X] API readiness checks — added `/api/readiness` with Redis connectivity and startup workspace/project-db readiness diagnostics without exposing sensitive paths or credentials
- [X] CLI subprocess worker adapter (post-prototype) — execute profiling/regenerate through explicit `respro` subprocess commands in worker jobs instead of direct in-process Python calls
- [X] Frontend tests — Vitest + React Testing Library setup added to `web/frontend/`; covers critical user flows: file upload with progress tracking (mocked XHR), job polling state transitions (queued → running → succeeded/failed), and report display and selection; `npm test` runs the test suite locally, and CI integrates frontend tests into `tests.yml` alongside Python tests

### Public release (done)

- [X] Professional documentation — rewritten README with tested CLI/web quickstarts, linked user guides (install, database preparation, TSV format, workflow explanation, basic/detailed CLI tutorials, detailed web hosting, troubleshooting/FAQ, output interpretation), and development architecture/contribution documentation
- [X] Example onboarding dataset in `example_data/` documented for first-time users
- [X] README updates — link to `example_data/` and GitHub Pages example-report parity note
- [X] GitHub Actions test workflow on push to `master`
- [X] Mypy workflow plus mypy toolchain addition to development setup
- [X] GitHub Pages workflow for publishing the example report
- [X] Docker publish workflow triggered on release
- [X] Dependabot configuration for dependencies and GitHub Actions
- [X] GitHub Actions CI ruff lint check

### Databases

- [X] `respro maintained.db` CLI command — `maintained.db --list` prints available databases (with full metadata panel) from the companion repository at `https://github.com/jonas-fuchs/respro-db`; `maintained.db --download <name> --output <path>` fetches `rules.tsv`, `metadata.json`, and optional `formula-rules.tsv` from the repo, resolves unique `reference_identifier` accessions from the rules TSV, downloads the corresponding GenBank records from NCBI, and calls `respro init` with `--overwrite` to produce a ready-to-use `<name>.db` (directory paths use `<name>.db` by default); implemented in `respro/io/maintained_db.py` and `respro/cli/maintained_db.py` using stdlib `urllib.request`

### Web — Batch analysis

- [X] `RESPRO_WEB_MAX_BATCH_SIZE` env config key added to `defaults.toml` and `config.py`
- [X] `BatchProfileVcfPayload`, `BatchProfileFastaPayload`, `BatchSubmitResponse`, `BatchSampleEntry` models added to `models.py`
- [X] `POST /api/profile/batch/vcf` endpoint — rate-limited (2/min), max 25 samples, enqueues one `run_profile_vcf` job per sample
- [X] `POST /api/profile/batch/fasta` endpoint — same pattern, no shared reference FASTA
- [X] Web profiling jobs pass `use_cache=True` so `query_reference` alignment mappings are reused across batch samples sharing the same project database and reference FASTA
- [X] Batch tab in web dashboard — VCF and FASTA batch modes with multi-file upload (up to 25), shared reference FASTA for VCF, project selector, per-sample results table with status polling, live 429 rate-limit countdown, and "New batch" reset button
- [X] 5 new tests in `tests/test_web_api.py` covering batch submit success (VCF + FASTA), max-size enforcement (VCF + FASTA), and mismatched sample-name/path lengths

---

## Next

Items are grouped by theme and ordered by priority within each group.
Priority: 🔴 high · 🟡 medium · 🟢 low

### Overlapping ORFs

- 🟡 Regression tests for overlapping ORF annotation — no test currently verifies that a
  variant falling inside two genes simultaneously produces correct, independent annotations for
  both across all profiling paths; VCF remap-level coverage now exists in `tests/test_profile_vcf.py`
  and FASTA-path coverage is still required

### Usability and workflow

- 🟡 Multi-chrom VCF and multi-record query FASTA support — a single VCF may carry variants
  across multiple CHROM identifiers (e.g. segmented viruses, amplicon panels spanning disjoint
  regions); the `--ref-fasta` supplied to `profile-vcf` must then be a multi-record FASTA with
  one sequence per CHROM; `parse_vcf` already stores the CHROM field per variant; the alignment
  step in `resolve_fasta_query` must be extended to accept a multi-record FASTA and return one
  set of CIGAR mappings per record; `remap_variants` routes each variant to the CIGAR map whose
  query name matches the variant's CHROM; all remapped variants from all chroms are then fed into
  the existing `annotate_variants` + rule-matching pipeline unchanged and aggregated into a single
  `ProfilingResult`; the report should note the number of distinct chroms processed
- 🟡 Regression tests for multi-chrom VCF correctness — must be written before and validated
  after the multi-chrom implementation to ensure no breakage of existing single-chrom behaviour;
  required coverage: (1) per-variant local alignment snippets (mini-alignments) rendered in the
  HTML report are correctly anchored to the right query sequence and CDS when multiple CHROM
  entries are present; (2) BAM-based coverage gaps (`compute_coverage_gaps_from_bam`) project
  depth per CHROM and emit `CoverageGap` entries correctly for each gene regardless of which
  chrom the gene's query FASTA record came from; (3) a single-chrom VCF with the existing test
  reference still produces byte-identical report output as before the change (guard against
  inadvertent regression)
- 🟡 Drug-level cumulative score interpretation (Stanford-like) — add score aggregation across
  all matched single and formula rules per drug, expose per-drug totals in report/JSON output,
  and support optional metadata-driven score-to-classification threshold maps (global defaults
  with optional per-drug overrides) so cumulative scores can be translated into resistance
  classes when curated mappings are provided
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

### Web deployment and security

- 🟢 Optional lightweight run cache for active session UX — if needed for frontend refresh
  resilience, keep a short-lived in-memory or Redis-backed session cache keyed by browser session
  ID (no durable per-user storage)
- 🟡 Paste sequence ?

### Public release

- 🔴 GitHub Actions CI — add a PyPI publish workflow triggered by version tags
- 🔴 Bioconda package — write a Bioconda recipe (`meta.yaml`) and submit a PR to
  bioconda-recipes; Bioconda is the standard distribution channel for bioinformatics CLI tools
  and avoids requiring users to have a working pip/Python setup; dependency on pysam makes
  Bioconda the natural distribution path once pysam is a requirement
- 🟡 Established wet-lab protocols

### Smaller Issues

- 🟡 only show relevant combinations and not all (remove badges etc)?
- 🟡 overlapping orfs minialignments
- 🟡 init short arguments
- 🟡 show scores in combinations and singles
- 🟡 show all mutations in the lolliplot even if they are only part of combinations
- 🟡 "Phenotypes are interpreted as resistant = reduced susceptibility, sensitive = expected susceptibility, intermediate = partial or uncertain effect, and contradictory = conflicting evidence." --> only when relevant