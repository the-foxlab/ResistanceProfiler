# ResistanceProfiler Roadmap

This roadmap tracks what has been achieved and what is planned next.
It is intentionally lightweight and should be updated in small, reviewable changes.

## v0.1 status (done)

- CLI-first package with `respro init`, `respro profile`, `respro regenerate`
- SQLite project model for curated references, genes, drugs, and resistance rules
- Codon-aware amino acid consequence annotation (forward and reverse strand)
- Rule-based resistance matching (exact and wildcard matching)
- Future-facing DB schema for combined/co-occurring resistance rule sets
- Allele-frequency binning with project defaults
- Report/export layer for HTML plus machine-readable outputs (JSON/TSV/SVG)
- Regression-oriented test suite for annotation, rules, reference resolution, CLI, and report exports

## Now (v0.2)

### Goal 1: strengthen biological edge-case handling
- ✓ Full variant-type annotation (SNPs, in-frame insertions/deletions, frameshifts)
  using CDS-codon splitting approach (inspired by BAMdash); legacy fallback removed
- Extend codon logic for adjacent variants that affect the same codon
- Improve handling/documentation for overlapping ORFs in reports
- ✓ Design and implement matching for combined/co-occurring resistance mutations
  using the reserved `resistance_rule_set` schema tables — `rule_group` column
  strategy adopted; loading and `all_of` matching implemented

### Goal 2: improve profiling robustness
- Harden filtering and validation around VCF parsing and INFO/FORMAT fallbacks
- ✓ Query-codon-aware annotation: codon position detected from internal reference,
  codon bases extracted from user-provided FASTA for amino acid translation;
  removed CDS-vs-query base sanity check (alignment thresholds are sufficient)
- ✓ Dual-reference annotation: alt_aa derived from user-provided ref_codon (actual
  sample biology), ref_aa anchored to internal CDS (rule matching baseline);
  prevents false calls from silent nucleotide divergence between user and internal
  reference; `annotate_variants` no longer requires a ref_sequence parameter
- Add explicit ambiguity notes in outputs when codon interpretation is uncertain
- Support richer rules TSV metadata (`reference_identifier`, `antiviral`, `ic50`, `publication`)
- Validate rules strictly against GenBank-derived reference/gene annotations during project init

### Goal 3: improve project usability
- Add a `docs/` user guide for input preparation and common workflows
- Add example project data (tiny GenBank, rules TSV)
- Derive organism/species metadata from GenBank per reference instead of requiring a single project-level pathogen
    - ✓ Separate curated `project.db` from run-scoped `results.db` initialization/validation
    - ✓ Populate `results.db` during `respro profile` with run and variant rows
    - ✓ Add `respro regenerate` command: `--list` displays stored runs; `--identifier` with
      `--project` and `--out` regenerates a full report with project-fingerprint validation
    - ✓ Remove `respro export` (bundle packaging) command and `bundle.py`

### Goal 4: flexible input reference handling
- ✓ Automatic CDS matching — align user query sequence to internal genes using
  Biopython PairwiseAligner; screen only genes with resistance rules
- ✓ CIGAR-based coordinate mapping between user reference and internal CDS
- ✓ DB caching of query references and mappings for fast repeat runs
- ✓ Wire sequence matching into `respro profile` for FASTA-based workflows
  (`--ref-fasta` remaps VCF coordinates via inverted CIGAR maps, with
  sanity checks on reference base agreement)
- ✓ Allow `respro profile` to reuse a previously cached query reference via its
  stored FASTA header (`--query-ref-header`) when the FASTA sequence is not
  provided

### Goal 5: FASTA consensus sequence input
- ✓ `respro profile --fasta consensus.fasta` — new mutually exclusive input mode
  (no VCF required)
- ✓ Query FASTA globally aligned to each matched internal gene CDS using
  Biopython PairwiseAligner; alignment walks in reference reading frame
- ✓ All variant types detected: SNPs (missense, synonymous, stop-gained,
  start-lost), in-frame insertions, in-frame deletions, frameshifts
- ✓ Frameshift detected when insertion or deletion length is not divisible by 3;
  subsequent codons are not processed for the affected gene
- ✓ IUPAC ambiguous bases expanded to all possible codons; each unique
  non-reference amino acid emitted as a separate variant with equal probability
  (allele_freq = 1 / number_of_possible_AAs)
- ✓ Synthetic VariantCall records carry the 0-based genomic codon-start position
  on the internal reference for consistent rule matching and report display

### Acceptance criteria for v0.2
- New regression tests cover adjacent codon variants and overlapping-ORF scenarios
- Combined-rule-set matching behavior is specified and covered by focused tests
- `respro profile` remains deterministic for unchanged inputs
- HTML and machine-readable outputs include explicit ambiguity/provenance fields

## Next (v0.3)

### Goal 1: codon ambiguity handling without read-backed inputs
- Improve handling of nearby multi-variant codon events from VCF/FASTA-only inputs
- Add explicit confidence/ambiguity notes in report tables for uncertain codon outcomes

### Goal 2: project portability and reproducibility
- Add stronger bundle metadata/versioning and import validation
- Add checksum/provenance tracking for profiling inputs and exports
- Maintain curated database bundles in a separate repository (outside the core codebase)
- Add automated normalization/validation/publishing pipelines (scheduled GitHub Actions)
- Publish an index manifest of maintained databases for direct loading by URL

### Acceptance criteria for v0.3
- End-to-end tests verify deterministic codon interpretation behavior for supported inputs
- Exported bundles are reproducible and include clear schema/project version metadata
- At least one maintained external database publishes versioned bundles + checksums + index entry

## Later (v0.4+)

### Goal 1: lightweight UI on top of stable backend
- Add minimal upload/profile/export workflow
- Keep core package independent from UI runtime concerns

### Goal 2: ML framework (only after curated labels)
- Add phenotype-linked ingestion and baseline feature pipelines
- Add baseline interpretable models and leakage-aware evaluation reports

### Acceptance criteria for v0.4+
- Backend APIs used by UI are stable and covered by integration tests
- ML features remain optional and do not affect core rule-based profiling reliability

## Maintenance rule

When priorities change, update this roadmap first, then implement code changes aligned with it.
