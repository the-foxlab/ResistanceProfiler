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
- [X] Drug records normalized and deduplicated case-insensitively during rules import for stable canonical naming across runs/regeneration
- [X] Formula-rule grouping model — atomic members are imported via TSV `member_id` and evaluated through the formula-rule workflow
- [X] Formula-rule import scaffold — atomic rules support unique `member_id`; `respro init` / `respro add` accept optional `--formula-rules` TSV with `group_id` formula identifiers and boolean `AND` / `OR` / `NOT` / `XOR` expressions, normalized formula storage, and member-id linkage via formula expressions
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
- [X] Sensitive mappy alignment settings — switched from `sr` preset (k=11/w=5) to `map-ont` (k=6/w=3/best_n=1) following Stanford HIVDB's approach; enables alignment of divergent sequences (~60–75% identity); settings externalized to `[alignment]` section in `defaults.toml`
- [X] Gap-open penalty (O1) externalized and tuned — `gap_open_penalty = 6` added to `[alignment]` in
  `defaults.toml` (map-ont default is 4); passed as O1 component of the `scoring` tuple in
  `_match_with_mappy()`; raising O1 from 4→6 suppresses compensating indel pairs (adjacent I/D
  in CIGAR that represent a single divergence event and cause spurious double-frameshift calls)
  without losing real indels; identity unchanged (0.757→0.755 on HIV FJ554792 test case);
  comprehensive test coverage in `TestMappyGapPenalty` (9 tests: forward/reverse frameshifts,
  triplet indels, divergent SNP-only, real indels in divergent context)

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
- [X] End-to-end formula-rule loading via TSV `init` path — atomic rows with `member_id` are validated and loaded during `init_project` (no manual SQL setup)
- [X] Generic insertion wildcard rule support — `INS_any` token matches any in-frame insertion at the given position; specific rules take precedence; allowed as a formula-rule member

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
- [X] Interpretation algorithm metadata in `metadata.json` — `interpretation_algorithms` top-level array in metadata JSON accepts coexisting algorithm types (`ic50_thresholds`, `drug_groups`, `drug_interpretation`, `drug_alias`); each is validated on import and stored in the `interpretation_algorithm` table in `project.db`; `load_interpretation_algorithms` exposes the config to downstream consumers (report, scoring); full test coverage in `tests/test_algorithms.py`; documented in `docs/user/database-preparation.md`
- [X] Drug-level cumulative score interpretation (Stanford-like)

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

- [X] `respro maintained.db` CLI command — `maintained.db --list` prints available databases (with full metadata panel) from the companion repository at `https://github.com/the-foxlab/respro-databases`; `maintained.db --download <name> --output <path>` fetches `rules.tsv`, `metadata.json`, and optional `formula-rules.tsv` from the repo, resolves unique `reference_identifier` accessions from the rules TSV, downloads the corresponding GenBank records from NCBI, and calls `respro init` with `--overwrite` to produce a ready-to-use `<name>.db` (directory paths use `<name>.db` by default); implemented in `respro/io/maintained_db.py` and `respro/cli/maintained_db.py` using stdlib `urllib.request`

### Web — Batch analysis

- [X] `RESPRO_WEB_MAX_BATCH_SIZE` env config key added to `defaults.toml` and `config.py`
- [X] `BatchProfileVcfPayload`, `BatchProfileFastaPayload`, `BatchSubmitResponse`, `BatchSampleEntry` models added to `models.py`
- [X] `POST /api/profile/batch/vcf` endpoint — rate-limited (2/min), max 25 samples, enqueues one `run_profile_vcf` job per sample
- [X] `POST /api/profile/batch/fasta` endpoint — same pattern, no shared reference FASTA
- [X] Web profiling jobs pass `use_cache=True` so `query_reference` alignment mappings are reused across batch samples sharing the same project database and reference FASTA
- [X] Batch tab in web dashboard — VCF and FASTA batch modes with multi-file upload (up to 25), shared reference FASTA for VCF, project selector, per-sample results table with status polling, live 429 rate-limit countdown, and "New batch" reset button
- [X] 5 new tests in `tests/test_web_api.py` covering batch submit success (VCF + FASTA), max-size enforcement (VCF + FASTA), and mismatched sample-name/path lengths

### Web — Multi-sample comparison

- [X] Multi-sample comparison heatmap feature — Reports tab supports selecting multiple session results (checkboxes with same-database + same-reference enforcement), "Compare selected" button triggers `POST /api/compare` endpoint returning a mutation × sample matrix with allele frequencies, coverage gaps, db-hit annotations, and feature annotation; Plotly.js client-side heatmap renders with viridis colorscale, grey coverage-gap sentinel, ★ db-hit markers, and categorical feature annotation row
- [X] `POST /api/compare` backend endpoint and `web/backend/services/compare.py` — `build_comparison_matrix` assembles union of mutation keys (sorted by feature, position, ref/alt AA), builds allele frequency matrix with coverage-gap and db-hit detection (combining `rule_match` and `formula_rule_hit` sources), returns `CompareResponse` with `samples`, `references`, `mutations`, `mutation_labels`, `features`, `feature_map`, and `matrix`
- [X] Same-database and same-reference validation — backend `_validate_same_database` and `_validate_same_reference` enforce homogeneous `project_name` and `reference_name` across selected results; frontend disables checkboxes from different databases and displays reference name in heatmap header
- [X] `ComparisonHeatmap.jsx` component — Plotly.js-dist-min heatmap with feature annotation track (y2 axis), cell-value legends, feature legend, and proper cleanup via `Plotly.purge()`
- [X] Download selected artifacts — "Download selected" button in Reports tab sends selected result paths to `/api/artifact-bundle` for ZIP download; "Download all" retained alongside
- [X] 18 tests in `tests/test_compare.py` covering matrix assembly, coverage gaps, formula-rule db-hit detection, path validation, same-database rejection, same-reference rejection, endpoint integration, and error responses
- [X] Optional lightweight run cache assessed as unnecessary — current ephemeral session model (React in-memory state + results.db persistence + filesystem artifacts) provides sufficient UX; Redis session cache would add complexity with negligible benefit

### Web — Chart consolidation

- [X] Frontend chart library consolidation — migrated all recharts components (pie charts, IC50 scatter/bar, position stacked bars) to Plotly.js; removed recharts dependency; all charts now use a single rendering library

### Web — Legal notice / imprint

- [X] Support external imprint link + rename env var — renamed `RESPRO_WEB_IMPRESSUM_PATH` → `RESPRO_WEB_IMPRINT`; new `ImprintConfig(kind='path'|'url', html|url)` replaces `impressum_html`; `_resolve_imprint()` detects `scheme://` URLs (http/https only, others fail fast) vs local file paths (missing file fails fast); `build_legal_router` serves HTML (path) or 302-redirects (url); `/api/ui/legal` returns `{enabled, kind, url?}`; updated `web/backend/{config,defaults.toml,startup_config,main,routes/health}.py`. Backend tests: 6 cases (unset/path/url/missing-file/bad-scheme/env-resolve) pass. Scientific Review: APPROVED.
- [X] Frontend renders external imprint as a direct link — `useDashboardLogic.js` exposes `legalLink` (null | external URL | `${API_BASE}/legal`) via `_resolveLegalLink` helper (backward-compatible with stale backends); `DashboardView.jsx` footer `<a href={legalLink}>` opens in new tab; removed now-unused `API_BASE` prop; 6 new `_resolveLegalLink` unit tests pass. Scientific Review: APPROVED.
- [X] Document external-imprint option and env-var rename — `docs/docs/webapp.md` env table row, `.env` example, and "Legal notice / imprint" section rewritten with a URL-vs-path mode table and copy-pasteable examples for both modes; `docker-compose.web.yml` comments show both an external-URL and a mounted-HTML variant; `.gitignore` `impressum.html` → `imprint.html`. Scientific Review: APPROVED.
- [X] Switch software license from MIT to AGPL-3.0-only 


---

## Ready

### Feature: per-drug-reference-thresholds
- [x] ✅ Override schema for `drug_interpretation` thresholds — add an optional `drug_thresholds` list to the `drug_interpretation` config in `respro/db/algorithms.py`; each entry is `{reference?, drug, thresholds: {resistant, intermediate?}}`; extend `_validate_drug_interpretation` to validate it (same numeric/integer rules as the parent `thresholds` per method; `resistant` required, `intermediate` optional, resistant > intermediate for numeric methods; `drug` required non-empty string; `reference` optional non-empty string; no duplicate `(reference, drug)` or `(drug)` keys); keep the existing global `thresholds` as the fallback default; Affected modules: `respro/db/algorithms.py`; Acceptance: valid configs with `drug_thresholds` pass `validate_interpretation_algorithms`; invalid entries (missing `drug`, missing `resistant`, non-numeric for `by_ic50`, non-integer for `by_phenotype`, resistant <= intermediate, duplicate `(reference, drug)` tuples) raise `ValueError` with a descriptive message; existing configs without `drug_thresholds` still validate; Feature: per-drug-reference-thresholds (2026-08)
- [x] ✅ Resolution helper + `compute_drug_assessment` integration — add a `resolve_thresholds(config, reference_name, drug_name)` helper in `respro/db/algorithms.py` returning `(resistant, intermediate)` using the precedence `(reference, drug)` > `(drug)` > global `thresholds`; refactor `compute_drug_assessment` to accept the drug's `reference_name` and per-method call `_compute_single_method` with resolved thresholds instead of the global ones; Affected modules: `respro/db/algorithms.py`; Acceptance: a drug with a `(reference, drug)` override uses the override values; a drug with only a `(drug)` override uses those; a drug with no override uses the global `thresholds`; the override is only applied when `reference_name` matches (case-insensitive, accession-version tolerant via existing `_references_match_with_accession_version`); existing `TestComputeDrugAssessment` cases (no `drug_thresholds`) still pass unchanged; Feature: per-drug-reference-thresholds (2026-08)
- [x] ✅ Override schema for `ic50_thresholds` — add an optional `drug_thresholds` list to the `ic50_thresholds` config in `respro/db/algorithms.py`; each entry is `{reference?, drug, thresholds: {intermediate, resistant}}` (both keys required for `ic50_thresholds`, resistant > intermediate); extend `_validate_ic50_thresholds` accordingly; the existing per-drug `thresholds` dict remains the global fallback; Affected modules: `respro/db/algorithms.py`; Acceptance: valid `ic50_thresholds` configs with `drug_thresholds` pass validation; invalid entries raise `ValueError`; existing configs without `drug_thresholds` still validate; Feature: per-drug-reference-thresholds (2026-08)
- [x] ✅ `apply_ic50_threshold_classification` uses per-(reference, drug) overrides — extend `apply_ic50_threshold_classification` in `respro/db/algorithms.py` to resolve thresholds per rule via `resolve_thresholds(config, reference_name, drug_name)` (the query already joins `reference`); a rule whose `(reference, drug)` or `(drug)` override exists uses the override instead of `thresholds[drug_name]`; drugs with neither an override nor a global `thresholds` entry are still skipped; Affected modules: `respro/db/algorithms.py`; Acceptance: a rule with a `(reference, drug)` override is classified against the override breakpoints, not the global `thresholds[drug]`; a rule for the same drug under a different reference with no override falls back to the global entry; existing `TestApplyIc50ThresholdClassification` cases (no overrides) still pass; Feature: per-drug-reference-thresholds (2026-08)
- [x] ✅ Report: per-drug hover on all method Assessment columns — in `respro/report/html.py::_build_drug_interpretation_table`, build a per-`method_labels` entry whose `description` reflects the thresholds actually applied to each drug when `drug_thresholds` is present; approach: keep the single header hover showing the global default thresholds, and add a per-cell hover (info icon + panel) on each drug's method-assessment cell that lists the resolved `(resistant, intermediate)` thresholds and the resolution source (override `(reference, drug)` / `(drug)` / global) for that drug; this applies to every method column (`by_phenotype`, `by_score`, `by_ic50`, `by_fold_ic50`) — `by_score`/`by_phenotype` need no extra logic beyond the generic resolution since `_compute_single_method` already takes resolved thresholds; pass each drug's `reference_name` into `compute_drug_assessment` and attach a `resolved_thresholds` + `threshold_source` field per method assessment for template rendering; Affected modules: `respro/report/html.py`, `respro/report/templates/report.html.j2`; Acceptance: when no `drug_thresholds` are configured, the report renders identically to today (only the header hover, no per-cell icon) for all methods; when `drug_thresholds` are configured, each method-assessment cell (including `by_score` and `by_phenotype`) shows an info icon whose panel names the resolved thresholds and their source; the table layout, badge styling, and final Assessment column are unchanged; Feature: per-drug-reference-thresholds (2026-08)
- [x] ✅ Database Dashboard: condense `drug_thresholds` like `effect_as_resistant` — in `web/backend/services/browse.py::_extract_display_algorithms`, include the `drug_thresholds` list (when present) for both `ic50_thresholds` and `drug_interpretation` entries alongside the existing `method`/`thresholds`/`use` fields; in `web/frontend/src/components/tabs/DatabaseTab.jsx`, add a condensing renderer mirroring `_groupEffectRules`: group override entries that share the same `reference` (or "(all)") and the same `thresholds` values, collapsing their `drug` names into a sorted set; render a condensed table (Reference | Drugs | Thresholds) below the existing global Method/Thresholds row for each algorithm; Affected modules: `web/backend/services/browse.py`, `web/frontend/src/components/tabs/DatabaseTab.jsx`; Acceptance: a database with `drug_thresholds` shows a condensed overrides table where drugs sharing identical thresholds and reference collapse into one row; a database without `drug_thresholds` renders exactly as today; the global default thresholds row remains visible; Feature: per-drug-reference-thresholds (2026-08)
- [x] ✅ Tests: validation, resolution, report hover, dashboard condensing — extend `tests/test_algorithms.py` with cases for `drug_thresholds` validation (both algorithms), `resolve_thresholds` precedence, and `compute_drug_assessment`/`apply_ic50_threshold_classification` with overrides; extend `tests/test_report_outputs.py` (or a focused report test) to assert the per-cell hover payload is present only when `drug_thresholds` is configured and that the no-override path is byte-identical to the prior output; add a Vitest case (or unit test for the new condensing helper) covering the dashboard condensing; Affected modules: `tests/test_algorithms.py`, `tests/test_report_outputs.py`, `web/frontend` test suite; Acceptance: all new tests pass; the full existing suite still passes; Feature: per-drug-reference-thresholds (2026-08)
- [x] ✅ Docs: per-drug/per-reference thresholds — document the optional `drug_thresholds` key for `drug_interpretation` and `ic50_thresholds` in `docs/docs/algorithms.md` and `docs/docs/database-preparation.md`, including the precedence rule `(reference, drug)` > `(drug)` > global, the validation constraints, and a JSON example; note the report hover behavior and the Dashboard condensing; Affected modules: `docs/docs/algorithms.md`, `docs/docs/database-preparation.md`; Acceptance: both pages describe `drug_thresholds`, the precedence, and show a worked example matching the validation rules; Feature: per-drug-reference-thresholds (2026-08)

### Feature: batch-bam-coverage

VCF-mode batch upload currently accepts an optional BAM file only for single-VCF profiling. The batch
route (`POST /api/profile/batch/vcf`) hardcodes `bam_path=None` for every sample, so per-sample
coverage-gap analysis from BAM depth is unavailable in batch mode. This feature adds per-sample,
**optional** BAM support to batch VCF upload. Association is carried **positionally in the request
payload** (a `bam_paths` parallel array) rather than by filename: uploaded files are stored under
anonymous `tmpXXXXXX.{ext}` names on disk, so name-based matching is impossible, and the existing
batch contract already uses parallel arrays (`vcf_paths` ↔ `sample_names` ↔ `input_display_names`).
A `None` entry means "no BAM for that sample", preserving the optional-per-sample behaviour of the
single-VCF path. The job layer (`run_profile_vcf`, `_run_profile_vcf_subprocess`) already accepts and
wires `bam_path` → CLI `--bam`, so this feature touches only the payload model, the batch route's
per-sample validation/enqueue, the frontend batch manager + batch VCF UI, tests, and docs. FASTA
batch mode is unchanged. The existing `db_path` (batch) vs `database_id` (single) inconsistency is
out of scope.

- [ ] 🔍 Add `bam_paths` field to batch VCF payload model — add
  `bam_paths: list[str | None] | None = None` to `BatchProfileVcfPayload` in
  `web/backend/models.py`. When provided, it must be the same length as `vcf_paths`; a `None` entry
  means no BAM for that sample. Affected modules: `web/backend/models.py`. Acceptance: the field is
  present, defaults to `None`, and a payload with `bam_paths` of a different length than `vcf_paths`
  is rejected (handled in the route ticket); Feature: batch-bam-coverage
- [ ] 🔍 Validate and wire per-sample BAM paths in the batch VCF route — in
  `web/backend/routes/profile.py::profile_batch_vcf_route`, after resolving `vcf_paths`/`sample_names`
  lengths, normalize `payload.bam_paths`: if `None`, treat as an all-`None` list of
  `len(payload.vcf_paths)`; otherwise require `len(bam_paths) == len(vcf_paths)` and raise
  `HTTPException(422, 'bam_paths and vcf_paths must have the same length.')` on mismatch. Add a
  helper `_validate_batch_bam_paths(bam_paths, allowed_roots, is_path_within_allowed_roots)` that
  resolves each non-`None` entry via `Path(...).expanduser().resolve()`, enforces
  `is_path_within_allowed_roots` (400 'BAM path is outside allowed upload directory.') and
  `.is_file()` (404 'BAM file not found.'), mirroring the single-VCF logic at `profile.py:91-99`.
  In the enqueue loop, replace the hardcoded `bam_path=None` with the resolved per-index BAM path
  (or `None`). Affected modules: `web/backend/routes/profile.py`. Acceptance: a batch request with a
  full-length `bam_paths` list enqueues each `run_profile_vcf` job with the correct per-sample
  `bam_path`; a request with mismatched lengths returns 422; an out-of-root or missing BAM returns
  400/404 with no partial jobs enqueued; a request with `bam_paths=None` behaves exactly as today
  (all `bam_path=None`); a request with mixed `None`/path entries enqueues jobs with BAM only where
  provided; Feature: batch-bam-coverage
- [ ] 🔍 Frontend: multi-select BAM upload with stem auto-pairing + per-row override in batch VCF
  manager — in `web/frontend/src/hooks/useBatchManager.js`, extend each `batchVcfFiles` entry to
  `{path, name, size, bamPath, bamName}` (default `bamPath: null`, `bamName: null`). Add a
  `addBatchBamFiles(files)` action that accepts a **multi-select** BAM file list, uploads each via
  the existing `/api/upload/bam` endpoint, and **auto-pairs** each uploaded BAM to the VCF row whose
  filename stem (basename minus extension, via the existing `formatPathStem` helper in `api.js`)
  equals the BAM's stem (e.g. `sample1.vcf` ↔ `sample1.bam`); store `bamPath`/`bamName` on the
  matched entry and call `addUploadedPath` on the returned path. Pairing rules: matching is
  case-insensitive on stems; a BAM whose stem matches no VCF row is reported back to the caller as
  an **unmatched** result (do not silently drop it) so the UI can surface a message; a BAM whose
  stem matches a VCF row that **already** has a BAM is reported as a **collision** (do not overwrite
  the existing pairing silently) so the UI can ask the user to confirm or use per-row override; a
  VCF row may also be paired by an explicit per-row attach (see `attachBatchBam`). Add
  `attachBatchBam(vcfIndex, file)` (single-file per-row upload that overwrites whatever BAM is on
  that row) and `removeBatchBam(vcfIndex)` (clears only the BAM fields on that row). Ensure
  `removeBatchFile(index)` still drops the whole row including its BAM. In `submitBatch`'s VCF
  branch, include `bam_paths: batchVcfFiles.map((f) => f.bamPath ?? null)` in the request body.
  Affected modules: `web/frontend/src/hooks/useBatchManager.js`. Acceptance: multi-selecting N BAMs
  whose stems match N of the uploaded VCF rows pairs each BAM to its row with no extra clicks; a BAM
  with no matching VCF stem is returned as unmatched (not silently dropped); a BAM whose stem
  matches an already-paired row is returned as a collision (existing pairing preserved); the
  per-row `attachBatchBam` overwrites a row's BAM regardless of auto-pairing; `removeBatchBam`
  clears only the BAM; removing a VCF row drops its BAM too; the submitted body contains a
  `bam_paths` array of the same length as `vcf_paths` with `null` for rows without a BAM; Feature:
  batch-bam-coverage
- [ ] 🔍 Frontend: BAM controls in batch VCF UI — in
  `web/frontend/src/components/tabs/AnalyzeTab.jsx`'s batch VCF section, add (1) a **multi-select**
  BAM file input (`<input type="file" multiple accept=".bam">`) alongside the existing VCF files
  input, wired to `addBatchBamFiles(event.target.files)`; after the auto-pairing call, surface a
  short summary of results — counts of paired / unmatched / collision — and, when there are
  unmatched or collision cases, list the affected filenames so the user can resolve them with the
  per-row control; (2) in the `batch-uploaded-file-list` block, add a per-row BAM cell showing the
  attached BAM name (or "No BAM"), a per-row `<input type="file" accept=".bam">` wired to
  `attachBatchBam(index, file)` for explicit override, and a "Remove BAM" control wired to
  `removeBatchBam(index)` (shown only when the row has a BAM). Disable all BAM controls while
  `batchSubmitting`. Affected modules: `web/frontend/src/components/tabs/AnalyzeTab.jsx`.
  Acceptance: multi-selecting BAMs auto-pairs them to matching VCF rows and shows a paired/
  unmatched/collision summary; unmatched and collision filenames are listed; each VCF row shows its
  BAM name (or "No BAM") and supports per-row attach (override) and remove; all BAM controls are
  disabled during submission; rows without a BAM still submit successfully; Feature:
  batch-bam-coverage
- [ ] 🔍 Frontend tests: batch BAM auto-pairing and per-row override — extend the Vitest suite in
  `web/frontend/` with unit tests for `useBatchManager`'s new BAM actions (mock `apiUpload`):
  (a) multi-select of N BAMs whose stems match N VCF rows pairs each to the correct row; (b) a BAM
  with no matching VCF stem is reported unmatched and no row is changed; (c) a BAM whose stem
  matches an already-paired row is reported as a collision and the existing pairing is preserved;
  (d) `attachBatchBam(index, file)` overwrites a row's BAM regardless of prior auto-pairing;
  (e) `removeBatchBam(index)` clears only that row's BAM; (f) `removeBatchFile(index)` drops the
  row and its BAM; (g) `submitBatch`'s VCF branch sends a `bam_paths` array equal in length to
  `vcf_paths` with `null` for unpaired rows. Affected modules: `web/frontend/` (Vitest specs).
  Acceptance: all new frontend tests pass; `npm test` is green; Feature: batch-bam-coverage
- [ ] 🔍 Tests: batch BAM coverage end-to-end — extend `tests/test_web_api.py`
  `TestBatchProfileEndpoints` with cases: (a) a batch request with a full-length `bam_paths` list
  where each entry points to a valid in-root BAM asserts each enqueued `run_profile_vcf` job received
  the matching `bam_path` (inspect the queued job args via the existing fakeredis/isolated-queue
  fixtures); (b) a request with `bam_paths` shorter than `vcf_paths` returns 422 and enqueues no
  jobs; (c) a request with an out-of-root BAM path returns 400 and enqueues no jobs; (d) a request
  with a missing BAM file returns 404 and enqueues no jobs; (e) a request with mixed `None`/valid
  BAM entries enqueues jobs with `bam_path` set only where provided; (f) a request with `bam_paths`
  omitted entirely behaves as today (all `bam_path=None`). Add a reusable fixture/helper for a valid
  in-root BAM (small BGZF file) if one does not already exist. (Frontend auto-pairing logic — stem
  match, unmatched, collision — is covered by the Vitest suite in the frontend ticket, not here.)
  Affected modules: `tests/test_web_api.py`, `tests/conftest.py`. Acceptance: all new tests pass;
  the full existing suite still passes; `ruff check .` is clean; Feature: batch-bam-coverage
- [ ] 🔍 Docs: BAM coverage in batch VCF upload — update `docs/docs/webapp.md` (batch section) and
  `docs/docs/how-it-works.md` (VCF+BAM coverage section) to state that batch VCF upload supports an
  optional per-sample BAM, paired positionally per uploaded VCF, and that rows without a BAM skip
  coverage-gap analysis (same behaviour as single VCF without `--bam`). Note that matching is by
  upload order/row, not by filename. Affected modules: `docs/docs/webapp.md`,
  `docs/docs/how-it-works.md`. Acceptance: both docs describe per-sample optional BAM in batch VCF
  mode and the positional (not filename) pairing; Feature: batch-bam-coverage

### Feature: multi-species-reporting

Reporting redesign for multi-reference runs, driven by the user decision (2026-07): **always assume
references are derived from one species; reject only when distinct species share a gene name.** Same
species + different genes → no per-reference labelling. Different species + different genes →
reference id column in tables, multi-reference header, per-reference feature attribution. Different
species + same gene → hard reject. One genome overview per **internal reference** (collapse
ReferenceGroups that share a `reference_id`), then the related feature panels. Remove the
multi-species warning banner and the profiled-references section introduced in `multi-vcf-support` —
proper handling makes them redundant. Drug interpretation stays one report. VCF mode only; FASTA
mode unchanged. `RESULTS_SCHEMA_VERSION` stays at 1 (no migration).

- [x] ✅ Validation gate: reject cross-species gene-name collisions — in
  `respro/cli/profile_helpers.py::assemble_multi_reference_result`, after the `ReferenceGroup`s are
  built, compute `organism_by_ref_id = {g.reference_id: g.organism for g in groups}` and the
  matched feature names per group; if any feature name appears on groups belonging to **more than
  one distinct organism**, raise `click.ClickException` with a message naming the colliding gene(s)
  and the organisms (e.g. "gene 'UL23' matched references from multiple species
  (Human alphaherpesvirus 1, Human alphaherpesvirus 2) — ambiguous cross-species hit; refusing to
  report"). Same-species runs always pass regardless of shared gene names. The gate runs before any
  plot/report generation so a rejected run produces no report. Affected modules:
  `respro/cli/profile_helpers.py`. Acceptance: `example/multi-test-2` (HSV-1 UL23 + HSV-2 UL23) is
  rejected with a `click.ClickException` naming UL23 and both organisms; `example/multi-test-1`
  (two HSV-1 chroms) passes; a single-reference run is unaffected; a two-reference same-species run
  with a shared gene name passes; Feature: multi-species-reporting (2026-07)
- [x] ✅ Plots: one genome overview per internal reference — in
  `respro/report/plots.py::_build_multi_reference_lollipop_figure`, iterate over **distinct
  `reference_id`** values (collapsing ReferenceGroups that share a `reference_id`, e.g. the
  targeted-sequencing case in `multi-test-1` where two chroms map to one HSV-1 reference) instead of
  over `result.references`; for each distinct reference draw exactly one genome overview followed by
  the feature panels whose features belong to that reference; scope feature-panel lookup by
  `(reference_id, feature_name)` rather than by `feature_name` alone so panels never cross-contaminate
  across references. The single-reference path (`_build_lollipop_figure` when
  `len(result.references) == 1`) stays byte-identical. Affected modules: `respro/report/plots.py`.
  Acceptance: `multi-test-1` produces a figure with exactly one genome overview (not two) and the
  UL23 + UL30 panels once each; a two-distinct-reference run produces one overview per reference with
  each reference's panels grouped under it; a single-reference run produces a figure identical to
  today; Feature: multi-species-reporting (2026-07)
- [x] ✅ Report header: state multiple references when multi-species — in
  `respro/report/html.py::build_report_context`, replace the single `organism`/`reference` header
  fields with logic that, when `len({g.organism for g in result.references}) > 1`, builds a header
  stating multiple references/organisms (e.g. "Multiple references: Human alphaherpesvirus 1,
  Human alphaherpesvirus 2"); when single-species (including same-species multi-reference), keep the
  existing single-organism/single-reference header. Thread an `is_multi_species` flag
  (`len({g.organism for g in result.references}) > 1`) into the context for downstream conditional
  rendering. Affected modules: `respro/report/html.py`, `respro/report/templates/report.html.j2`.
  Acceptance: a multi-species run renders a header naming multiple organisms; a same-species
  multi-reference run renders the single-organism header (no "multiple references" text); a
  single-reference run is byte-identical to today; Feature: multi-species-reporting (2026-07)
- [x] ✅ Report: remove multi-species warning banner and profiled-references section — delete
  `_multi_species_warning` and `_references_summary` from `respro/report/html.py` and remove the
  `multi_species_warning` banner block and the `references_summary` "Profiled references" section
  from `respro/report/templates/report.html.j2`; remove the corresponding keys from
  `build_report_context`'s returned dict. Affected modules: `respro/report/html.py`,
  `respro/report/templates/report.html.j2`. Acceptance: no warning banner and no profiled-references
  section appear in any report (single-species, same-species multi-reference, or multi-species);
  existing tests referencing `multi_species_warning` / `references_summary` are removed/updated;
  Feature: multi-species-reporting (2026-07)
- [x] ✅ Report: reference id column in Database Hits / All Mutations / Sequence Feature
  Information tables — add a "Reference" column to the Database Hits, All Mutations, and Sequence
  Feature Information tables in `respro/report/templates/report.html.j2` and the corresponding
  row builders in `respro/report/html.py`; the column shows the `reference_name` (or `reference_id`)
  for each row and is **rendered only when `context.is_multi_species` is true** (same-species runs,
  including same-species multi-reference, show no such column to keep reports unchanged). Affected
  modules: `respro/report/html.py`, `respro/report/templates/report.html.j2`. Acceptance: a
  multi-species run shows the Reference column in all three tables with the correct per-row
  reference; a same-species multi-reference run shows no Reference column; a single-reference run is
  byte-identical to today; Feature: multi-species-reporting (2026-07)
- [x] ✅ Report: affected sequence features per-reference attribution — in
  `respro/report/html.py::_build_mutation_profile`, when `is_multi_species` is true, group mutations
  by `(reference_id, feature_name)` instead of by `feature_name` alone and include the reference id
  in each entry's badge/label so features from different species are visually distinguished; when
  single-species, keep the existing feature-name-only grouping. Affected modules:
  `respro/report/html.py`, `respro/report/templates/report.html.j2`. Acceptance: a multi-species run
  renders affected features with per-reference attribution (no conflation of same-named genes across
  species); a same-species run renders affected features exactly as today; Feature: multi-species-reporting (2026-07)
- [x] ✅ Report: interpretation summary multi-species handling — in
  `respro/report/html.py::_build_summary_narrative`, replace the single
  `organism_name = escape(result.organism)` attribution with logic that, when `is_multi_species`,
  attributes resistance-relevant features per organism/reference (the drug interpretation table
  remains one combined report); when single-species, keep the existing narrative. Affected modules:
  `respro/report/html.py`. Acceptance: a multi-species run produces a narrative that attributes
  features to the correct organism/reference without implying a single species; a single-species run
  produces a narrative byte-identical to today; Feature: multi-species-reporting (2026-07)
- [x] ✅ Tests and validation: multi-species reporting regression suite — extend
  `tests/test_profile_multi_vcf.py` (and fixtures in `tests/conftest.py`) to cover: (a)
  `multi-test-1` (two HSV-1 chroms, one reference_id) → passes, one genome overview, no Reference
  column, single-organism header; (b) `multi-test-2` (HSV-1 UL23 + HSV-2 UL23) → rejected by the
  validation gate with a `click.ClickException` naming UL23 and both organisms; (c) a constructed
  multi-species run with **non-colliding** gene names (e.g. HSV-1 UL23 + HSV-2 UL54, or two
  synthetic organisms with disjoint feature names) → passes, shows the Reference column in all three
  tables, multi-reference header, per-reference feature attribution, one genome overview per
  reference; (d) a same-species two-reference run with a shared gene name → passes, no Reference
  column, single-organism header; (e) single-reference regression unchanged. Affected modules:
  `tests/test_profile_multi_vcf.py`, `tests/conftest.py`, `tests/test_report_outputs.py`. Acceptance:
  all new tests pass; the full existing suite still passes; `ruff check .` is clean; Feature:
  multi-species-reporting (2026-07)

### Feature: multi-vcf-support

VCF-mode only — accept a multi-chrom VCF plus a multi-record reference FASTA where each CHROM
matches exactly one FASTA record by header. Two matching regimes are supported: multiple FASTA
records aligning to the same internal reference (targeted sequencing of one pathogen's genes),
and records aligning to different internal references (segmented viruses). FASTA mode is
unchanged. Annotation pipeline (`annotate_variants`) is not touched. One HTML report is always
produced, with per-reference subplots and sections; a warning (not an error) is issued when the
matched references span different species. Survives `results.db` storage and `respro regenerate`.

- [x] ✅ Foundation: multi-record query resolution and per-CHROM matching — relax
  `respro/core/query.py::resolve_fasta_query` to accept a multi-record FASTA and return
  `list[QueryRecord]` (one per FASTA record) where each `QueryRecord` carries
  `(query_name, query_sequence, feature_matches)`; keep the existing single-record path as a
  one-element list; reuse `pick_best_reference_id` + `select_matches_for_reference` per record so
  multi-match-to-one-reference is handled as today; cache semantics preserved (one
  `query_reference` row per FASTA record). Affected modules: `respro/core/query.py`,
  `respro/core/alignment.py`, `respro/db/cache.py`. Acceptance: a 2-record FASTA where both
  records align to one internal reference returns 2 `QueryRecord`s both pointing at the same
  `reference_id`; a 2-record FASTA where records align to two different references returns 2
  `QueryRecord`s with distinct `reference_id`s; the single-record regression path still passes
  all existing `tests/test_profile_*.py` tests; Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Foundation: per-CHROM variant routing and remap — in `respro/cli/vcf.py`, group parsed
  variants by `variant.chrom` and pair each group with the `QueryRecord` whose `query_name`
  matches that CHROM; variants whose CHROM has no matching FASTA record are logged as a warning
  and dropped; if **no** CHROM matches any FASTA record, raise a hard error
  (`click.ClickException`) listing the unmatched CHROMs and FASTA headers; call
  `remap_variants(variants_for_chrom, matches_for_chrom, query_sequence_for_chrom)` per CHROM and
  concatenate the remapped variants; `annotate_variants` is then called once on the concatenated
  list with the union of features across matched references (do NOT modify
  `annotate_variants`). Affected modules: `respro/cli/vcf.py`, `respro/core/vcf_remap.py`
  (only its caller, not the function). Acceptance: a multi-chrom VCF with 2 CHROMs each matching
  a different FASTA record yields remapped variants on both internal references in one
  `annotate_variants` call; a VCF with one unmatched CHROM produces a `logger.warning` and
  continues; a VCF with all CHROMs unmatched raises `click.ClickException`; Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Foundation: multi-reference data structure — extend `respro/db/models.py::ProfilingResult`
  to carry multiple matched references by adding a `references: list[ReferenceGroup]` field where
  `ReferenceGroup` is a new frozen dataclass holding `(reference_name, reference_id, organism,
  reference_length_nt, query_name, query_sequence, feature_matches, features, rules,
  formula_rules, rule_feature_names)`; **remove** the existing single-reference scalar fields
  (`reference_name`, `reference_length_nt`, `query_sequence`, `feature_matches`) from
  `ProfilingResult` — no backward-compatibility shim is needed (per user decision 2026-07-21),
  so all readers are migrated to `result.references[i].<field>` in the same feature; `annotations`,
  `coverage_gaps`, and `formula_hits` remain flat lists on `ProfilingResult` (each
  `AnnotatedVariant` already carries `feature_name`, and features are unique per reference, so
  per-reference grouping is derivable). Affected modules: `respro/db/models.py`, plus every
  reader of the removed scalar fields (report layer, results DB, regenerate, JSON export — all
  migrated in their own tickets within this feature). Acceptance: `ProfilingResult` exposes only
  `references: list[ReferenceGroup]` for per-reference data (no `reference_name` /
  `reference_length_nt` / `query_sequence` / `feature_matches` scalars); a single-reference run
  constructs a one-element `references` list; a two-reference run constructs two `ReferenceGroup`s
  with disjoint `feature_matches`; `tests/test_results_db.py` and `tests/test_report_outputs.py`
  are updated in this ticket to read from `result.references[0]` and pass; Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Core: multi-reference rule matching and result assembly — in
  `respro/cli/profile_helpers.py`, replace the single-`ref_id` flow in `_resolve_reference` and
  `_load_reference_data` with a per-`QueryRecord` loop that builds one `ReferenceGroup` per
  matched internal reference (loading features/rules/formula_rules per `reference_id`); run
  `match_rules` and `match_formula_rules` per reference against the annotations whose
  `feature_name` belongs to that reference's features; assemble a single `ProfilingResult`
  populated **only** with the `references: list[ReferenceGroup]` (no scalar reference fields —
  see ticket 3) plus the flat `annotations`/`formula_hits`/`coverage_gaps` lists; require
  at least one `QueryRecord` to have aligned to an internal reference that has rules loaded
  (`rule_feature_names` non-empty) — otherwise raise `click.ClickException` ("no matched
  reference has resistance rules in the project database"); orphaned references (aligned to a
  reference with no rules, or no alignment) are kept and reported with a warning, not an error.
  Affected modules: `respro/cli/profile_helpers.py`, `respro/cli/vcf.py`. Acceptance: a 2-record
  submission where only one reference has rules completes successfully and the other reference's
  features appear in the report without rule hits; a submission where no matched reference has
  rules raises `click.ClickException`; a single-record submission behaves identically to today;
  Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Core: multi-reference BAM coverage — extend
  `respro/core/vcf_coverage.py::compute_coverage_gaps_from_bam` to accept a per-CHROM mapping
  (`dict[str, tuple[str, str, list[FeatureMatch]]]` of chrom → `(query_name, query_sequence,
  matches)`) and loop over each CHROM, using `_resolve_bam_contig` per CHROM (pysam exposes
  per-contig `fetch`); concatenate the resulting `CoverageGap` lists; keep the existing
  single-CHROM signature as a thin wrapper that builds a one-entry dict so existing callers and
  tests are unchanged. Affected modules: `respro/core/vcf_coverage.py`, `respro/cli/vcf.py`.
  Acceptance: a 2-CHROM BAM + 2-record FASTA yields `CoverageGap`s on features of both
  references; a single-CHROM BAM still produces identical output to today (existing
  `tests/test_vcf_coverage.py` passes); a BAM whose contig name has no matching FASTA record
  logs a warning and skips that contig; Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Reporting: per-reference subplots and report sections — extend
  `respro/report/plots.py::_build_lollipop_figure` to draw one genome-overview + feature-panel
  group per `ReferenceGroup` in `result.references`, stacked vertically with a per-reference
  title (reference name + organism); the single-reference path produces a figure visually
  identical to today; extend `respro/report/html.py::build_report_context` so the Summary,
  Database Hits, All Mutations, and Sequence Feature Information tabs render one section per
  reference (collapsible headers labelled with reference name and organism), sharing the existing
  per-section rendering helpers; when `result.references` spans more than one distinct
  `organism`, emit a visible warning banner in the report header ("matched references span
  multiple species — results are reported per reference") — this is a warning only, the single
  HTML is always produced. Affected modules: `respro/report/plots.py`, `respro/report/html.py`,
  `respro/report/templates/`. Acceptance: a 2-reference run produces one HTML file with two
  genome-overview sections and two feature-panel groups in the lollipop figure, and per-reference
  sections in each tab; a single-reference run produces a report byte-identical (modulo
  timestamp) to today; the multi-species warning banner is present iff `len({g.organism for g in
  result.references}) > 1`; Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Persistence: results DB schema and save/load for multi-reference runs — bump
  `RESULTS_SCHEMA_VERSION` to 2 in `respro/db/schema.py` and add a nullable `reference_name`
  column to `variant_result`, `coverage_gap`, and `formula_rule_hit` (auto-migrated on open via
  the existing `_RESULTS_OPTIONAL_TABLES_SQL` / column-add path); in
  `respro/db/results.py::save_run`, write each `ReferenceGroup`'s `reference_name` (resolved
  via `ProfilingResult.references` — there is no scalar `result.reference_name` anymore, see
  ticket 3) into the new column for every row belonging to that group, by joining on
  `feature_name → ReferenceGroup.features`; in `load_run` / `load_coverage_gaps` /
  `load_formula_rule_hits`, return the new column in each row dict; the `run` table keeps a single
  `reference_name` populated with the primary reference (first `ReferenceGroup`) for `list_runs`
  display continuity only. Affected modules: `respro/db/schema.py`, `respro/db/results.py`.
  Acceptance: a 2-reference run saves and reloads with each `variant_result` row carrying the
  correct `reference_name`; opening an existing v1 results DB auto-migrates to v2 without data
  loss (existing `tests/test_results_db.py` migration tests extended and passing); a
  single-reference run round-trips with `variant_result.reference_name` equal to
  `result.references[0].reference_name`; Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Regeneration: multi-reference regenerate — update `respro/cli/regenerate.py` to read the
  distinct `reference_name` values from the stored `variant_result` rows, look up each in the
  `reference` table, and load `features`/`rules`/`formula_rules` per reference; reconstruct one
  `ReferenceGroup` per distinct reference and populate `ProfilingResult.references` (the only
  per-reference carrier — no scalar fields, per ticket 3); pass the full reference list into
  `export_results` so the regenerated HTML/JSON/PDF match the original multi-reference report;
  apply the same path to `regenerate --json` by extending `load_run_from_json` and `write_json`
  to serialise the `references` list and per-variant `reference_name`. Affected modules:
  `respro/cli/regenerate.py`, `respro/db/results.py`, `respro/report/non_html_exports.py`.
  Acceptance: a stored 2-reference run regenerated from `results.db` produces a report with the
  same per-reference sections and subplots as the original; `regenerate --json` on a
  multi-reference `*.results.json` round-trips the `references` list; a stored single-reference
  run regenerates to a report with one `ReferenceGroup` and per-reference sections equivalent to
  today (no scalar-field fallback path exercised); Feature: multi-vcf-support — completed 2026-07
- [x] ✅ Webapp: surface multi-chrom VCF support — update the VCF and Reference FASTA tooltip
  text in `web/frontend/src/components/tabs/AnalyzeTab.jsx` to state that the VCF may be
  multi-chrom and the reference FASTA may be multi-record with one record per CHROM; update the
  `respro vcf` example in `web/frontend/src/components/tabs/AboutTab.jsx` and the
  `docs/docs/cli-reference.md` / `docs/docs/quickstart.md` to mention multi-chrom support; no
  backend route, payload, or job-function changes (the existing `ProfileVcfPayload` already
  passes one VCF + one reference FASTA through to `respro vcf`); the upload validator in
  `web/backend/services/upload.py` already accepts multi-record FASTA and multi-chrom VCF, so no
  validation change is needed. Affected modules: `web/frontend/src/components/tabs/AnalyzeTab.jsx`,
  `web/frontend/src/components/tabs/AboutTab.jsx`, `docs/docs/cli-reference.md`,
  `docs/docs/quickstart.md`. Acceptance: the Analyze tab tooltip and About tab example mention
  multi-chrom VCF + multi-record reference FASTA; a manual end-to-end submission of a 2-chrom
  VCF + 2-record FASTA through the webapp produces the multi-reference HTML report; Feature:
  multi-vcf-support — completed 2026-07
- [x] ✅ Tests and validation: multi-vcf regression suite — add a `tests/test_profile_multi_vcf.py`
  covering: (a) 2 FASTA records aligning to one internal reference (targeted-sequencing case);
  (b) 2 FASTA records aligning to two different internal references (segmented-virus case);
  (c) VCF with one unmatched CHROM → warning + continued run; (d) VCF with all CHROMs unmatched
  → `click.ClickException`; (e) no matched reference has rules → `click.ClickException`; (f)
  2-CHROM BAM coverage projection; (g) `save_run` → `load_run` → `regenerate` round-trip for a
  2-reference run; (h) multi-species warning banner present iff organisms differ; (i) JSON
  export → `regenerate --json` round-trip preserves the `references` list; (j) single-record
  regression (existing tests unchanged). Build the multi-reference project DB in a fixture by
  reusing the existing `write_genbank` helper from `tests/conftest.py` with two GenBank records
  whose features carry rules. Affected modules: `tests/test_profile_multi_vcf.py`,
  `tests/conftest.py`. Acceptance: all new tests pass; the full existing suite still passes;
  Feature: multi-vcf-support — completed 2026-07

## Next

Items are grouped by theme and ordered by priority within each group.
Priority: 🔴 high · 🟡 medium · 🟢 low

### Usability and workflow

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

### Public release

- 🔴 Bioconda package — write a Bioconda recipe (`meta.yaml`) and submit a PR to
  bioconda-recipes; Bioconda is the standard distribution channel for bioinformatics CLI tools
  and avoids requiring users to have a working pip/Python setup; dependency on pysam makes
  Bioconda the natural distribution path once pysam is a requirement
- 🟡 Established wet-lab protocols — protocols tab in the web app linking to sequencing
  protocols on protocols.io; protocols are decoupled from project databases (no metadata.json
  or SQLite changes) and fetched from a separate `protocols.json` in the respro-db companion
  repo at startup; the JSON is keyed by display name (not pathogen) and each entry carries
  `display_name`, `pathogen`, `targets` (gene/region list), `description`, and a
  `protocols_io_uri` outbound link; iframe embedding is blocked by protocols.io
  (`X-Frame-Options: SAMEORIGIN`), so the initial implementation renders metadata cards with
  outbound links; a later enhancement can use the protocols.io v4 API
  (`GET /api/v4/protocols/{id}?content_format=html`, requires a free client access token,
  100 req/min limit) to fetch rendered protocol content server-side; backend adds a
  `fetch_protocols()` call in startup (fail-soft, like maintained-DB bootstrap) and a
  `GET /api/protocols` endpoint; frontend adds a new `ProtocolsTab.jsx` with a display-name
  dropdown and metadata cards, wired as a new entry in the `MODES` sidebar; no CLI surface,
  no `respro/` core changes
