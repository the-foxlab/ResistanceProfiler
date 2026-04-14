# Annotation algorithms

This document is the official reference for how ResistanceProfiler annotates:

- VCF variants (`profile-vcf`)
- FASTA consensus sequences (`profile-fasta`)

It explains the algorithmic steps and how both paths relate to internal project references.

## Shared coordinate and reference model

Both pipelines use the same internal model:

- Internal reference coordinates are the canonical reporting coordinates.
- Coordinates are genomic forward (5'->3') for the selected internal reference.
- Gene strand controls coding interpretation, not the global coordinate frame.

This means both VCF and FASTA results are ultimately expressed on the same internal coordinate system used by rule matching.

## Rule model

Rules are stored and matched as explicit allele states per gene and codon position.

- Substitutions: compare resulting alternate AA state.
- Insertions: compare resulting expanded AA state.
- Deletions: compare explicit deleted reference AA block and resulting anchor AA.
- Frameshifts: use explicit sentinel (`fsX`).

Relevant implementation:

- schema: `respro/db/schema.py`
- matching: `respro/core/rules.py`
- TSV normalization: `docs/rules-tsv-format.md`

This strict model is deterministic and avoids wildcard ambiguity.

## VCF annotation algorithm

Entry points:

- CLI orchestration: `respro/cli.py`
- parser: `respro/io/vcf.py`
- remap: `respro/core/vcf_remap.py`
- consequence annotation: `respro/core/annotation.py`
- rule matching: `respro/core/rules.py`

### Step 1: Parse VCF into variant calls

`parse_vcf` reads each record and builds `VariantCall` entries:

- convert POS to 0-based
- normalize nucleotides (`U` to `T`)
- split multiallelic ALT
- extract AF and depth with fallback logic

### Step 2: Filter by AF/depth

`profile-vcf` filters parsed calls by `--min-af` and `--min-depth`.

### Step 3: Resolve query reference against internal references

The VCF calling reference FASTA is aligned to project genes.

- best internal reference is selected
- per-gene CIGAR + strand mapping is produced (or reused from cache)

### Step 4: Remap query-space variants to internal coordinates

For each variant and matching gene alignment in `remap_variants`:

1. Build query->CDS map from CIGAR.
2. Validate VCF REF anchor against the query base.
3. Determine CDS anchor position (including reverse-orientation indel anchor switching).
4. Convert CDS position to internal genomic position.
5. Transform REF/ALT into internal forward orientation.
6. Extract codon context (`query_ref_codon`) in CDS orientation for downstream AA interpretation.

#### Step 4a: Normalize anchor-changed indels before remap

Some callers may encode an anchor substitution and an indel payload in one record,
for example `ATTT -> G` (anchor `A->G` plus deletion of `TTT`).

If this is remapped as a single event, anchor switching in reverse-orientation
projection can hide the anchor substitution signal. To preserve deterministic
interpretation, such records are split before remap into:

1. anchor SNP: `A -> G`
2. canonical indel with aligned anchor: `ATTT -> A`

This split is only applied to indels where `ref[0] != alt[0]`.

Why this is done:

- preserves the biologically meaningful anchor SNP,
- keeps the indel event in canonical anchor form,
- prevents silent information loss during anchor switching,
- keeps rule comparison deterministic.

Key point:

- nucleotide anchor projection is solved in remap
- amino-acid consequence is solved in annotation

### Step 5: Codon-aware consequence annotation

`annotate_variants` maps each remapped call to gene coding context and emits:

- SNP / insertion / deletion / frameshift / inframe_complex
- reference and alternate codon/AA states
- consequence class

For indels, VCF-anchor semantics are applied in coding coordinates before consequence classification.

### Step 6: Rule matching

Annotated calls are matched against loaded rules (`match_rules`) and optional rule sets (`match_rule_sets`).

## FASTA annotation algorithm

Entry points:

- CLI orchestration: `respro/cli.py`
- FASTA profiling: `respro/core/fasta_profile.py`
- consequence model and rules are shared with VCF outputs

### Step 1: Align query sequence to matched genes

`profile_fasta_consensus` uses gene matches from the same sequence matching engine.

For each matched gene:

- extract the query region
- orient to coding strand if needed
- reconstruct gapped ref/query strings from CIGAR

### Step 2: Walk codons in reading frame

`_annotate_from_alignment` iterates codons and classifies events:

- ungapped codon differences: SNP path (including IUPAC expansion)
- boundary insertions: insertion or frameshift
- full-codon deletions: deletion (runs merged)
- partial-codon gaps: frameshift
- mixed/mid-codon insertion patterns: inframe_complex
- `NNN` codons or uncovered regions: coverage gaps

### Step 3: Build annotation objects

FASTA path emits `AnnotatedVariant` objects equivalent in structure to VCF path so rule matching and reporting are shared.

## VCF vs FASTA consistency

Current status:

- same consequence vocabulary
- same rule matcher
- same internal coordinate frame
- same inframe_complex anchor behavior (anchor `ref_aa` retained, `alt_aa='?'`)

Expected differences by design:

- VCF path remaps pre-called variants
- FASTA path infers variants directly from alignment and tracks coverage gaps

## Practical summary

Use this mental model:

- VCF mode: map query-space calls onto internal reference, then annotate.
- FASTA mode: infer calls directly in internal coding context from alignment.
- both end in the same rule-matching layer with explicit allele semantics.
