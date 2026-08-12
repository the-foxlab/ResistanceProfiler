---
title: CLI Reference
description: Complete CLI command reference with examples
---

# CLI Reference

This reference covers all primary CLI command groups:

- `databases`
- `init`
- `add`
- `vcf`
- `fasta`
- `regenerate`
- `classify`
- `manage database`
- `manage results` (including sync via `--sync` option)

The important workflow idea is that ResPro profiles against one internal project database. New sample data is first normalized to that internal reference space before amino-acid rules are matched.

## Download a maintained database

List available pre-ported databases:

```bash
respro databases --list
```

Download a database by name:

```bash
respro databases --download db_name --output my_folder/
```

ResPro automatically downloads TSV rules and GenBank files, then builds a ResPro-compatible SQLite database from scratch. Database creation can take a moment because ResPro enriches entries with PubMed and PubChem information.

!!! tip "Verbose progress"
    Add `-vv` to see verbose progress:
    ```bash
    respro -vv databases --download db_name --output my_folder/
    ```

## Initialize a project database

Use this when you have your own GenBank reference and rules TSV instead of a maintained database:

```bash
respro init \
  --name "Docs Demo" \
  --genbank some_reference.gb \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv \
  --output myrespro.db
```

If your dataset only contains atomic mutation rules, omit `--formula-rules`.

For metadata and interpretation algorithm options, see [Database Preparation](database-preparation.md).

## Extend or validate rules in an existing project

Validate rules without writing changes:

```bash
respro add \
  --project myrespro.db \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv \
  --validate
```

Add rules and commit changes:

```bash
respro add \
  --project myrespro.db \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv
```

## Profile FASTA input

```bash
respro fasta \
  --project myrespro.db \
  --fasta my_consensus_sequence.fasta \
  --output my_output \
  --results-db my_results.db \
  --export json \
  --export pdf
```

## Profile VCF input

```bash
respro vcf \
  --project myrespro.db \
  --vcf my_ngs_result.vcf \
  --ref-fasta my_vcf_ref.fasta \
  --output my_output \
  --results-db my_results.db \
  --min-af 0.01 \
  --min-depth 0 \
  --export json \
  --export pdf
```

The VCF may be **multi-chrom** and the reference FASTA **multi-record**: each VCF
`CHROM` is matched to one FASTA record by header name. This supports targeted
sequencing (multiple queries aligning to one internal reference) and segmented
viruses (multiple queries aligning to different internal references) in a single
run.

### VCF reference

- Every `CHROM` observed in the VCF variant records must have a matching record
  header in the reference FASTA. If a VCF `CHROM` has no matching FASTA record,
  profiling fails with a clear error (this usually means the wrong reference file
  was provided).
- Extra FASTA records that are not named by any VCF `CHROM` are ignored — they are
  not aligned, cached, or reported. You may safely supply a multi-record FASTA that
  contains references for more than one species; only the records named by VCF
  `CHROM`s contribute to the report.
- A VCF-matched FASTA record that does not align to any internal feature is skipped
  with a warning; profiling continues as long as at least one other record maps
  successfully.
- Multi-species runs are allowed as long as the genes the query actually matches do
  not overlap across species. If the same gene is matched on two distinct species
  (e.g. HSV-1 `UL23` and HSV-2 `UL23`), profiling fails because resistance-relevant
  mutations cannot be attributed to a single species unambiguously. Same-species
  shared gene names are always allowed.

### Allele-frequency source (VCF mode)

ResistanceProfiler evaluates resistance rules against per-allele allele frequencies
(AF). For each ALT allele of a record it resolves the AF from the first available
source, in this fixed precedence:

1. `INFO/AF` (allele frequency for the ALT, indexed by ALT position)
2. `INFO/VAF`
3. `INFO/FREQ`
4. `FORMAT/AF` of the **first sample** (single-sample contract — see below)
5. `FORMAT/AD`-derived AF of the first sample: `AD[alt_idx + 1] / sum(AD)`

This precedence is intentional for **single-sample pathogen VCFs**, where `INFO/AF`
typically already reports the per-allele frequency for the single sequenced sample.
For multi-sample VCFs, `INFO/AF` may instead describe site- or population-level
frequencies; in that case prefer a single-sample VCF or supply `FORMAT/AF`/`FORMAT/AD`
explicitly, because ResistanceProfiler reads only the first sample for FORMAT-level
values.

#### Missing and malformed allele-specific arrays

Allele-specific fields (`AF`, `VAF`, `FREQ`, `AD`) are read positionally against the
ALT list. Missing entries (VCF `.`) and short arrays are never silently clamped to
the last available value. Per VCF semantics the reference allele frequency is
`1 - sum(ALT AF)`, so the frequency budget for alleles whose AF is unavailable is the
**residual** of the known alleles:

- A missing entry (`.` / `None`) or an index beyond the end of a short array is
  treated as "no AF for this allele". The residual `max(0, 1 - sum(known ALT AFs at
  this site))` is split **equally** among all missing alleles at that site and used
  as their AF.
- A cardinality warning is logged when an array is shorter than the ALT list.
- This keeps the per-site AF total at exactly 1.0 (or 0.0 when the known alleles
  already sum to ≥ 1) and is more conservative for resistance calling than assuming
  a missing allele is fully present.

Example: `ALT=C,G,T` with `AF=0.1,0.2` → `C=0.1`, `G=0.2`, `T=0.7` (residual
`1 - 0.1 - 0.2` split over the one missing allele). `ALT=C,G,T` with `AF=0.1,.,0.3`
→ `C=0.1`, `G=0.6` (residual `1 - 0.1 - 0.3`), `T=0.3`. A biallelic `ALT=C` with
`AF=.` → `C=1.0` (residual `1 - 0`).

#### Single-sample and multi-sample vcf files

ResistanceProfiler is designed for single-sample VCF input. When FORMAT-level values
are needed, the **first sample** in the VCF header is used unconditionally; sample
selection is not configurable. Multi-sample VCFs are not rejected, but only the first
sample contributes FORMAT/AF and FORMAT/AD values, which is rarely the intended
semantics for multi-sample data.

## Inspect project metadata and curated rules

Project metadata:

```bash
respro manage database myrespro.db --info
```

Rules table:

```bash
respro manage database myrespro.db --rules
```

Rules table filtered by reference:

```bash
respro manage database myrespro.db --rules --reference NC_001806
```

## Inspect and delete stored runs

List runs:

```bash
respro manage results my_results.db --list
```

Delete one run without interactive confirmation:

```bash
respro manage results my_results.db --delete 1 --force
```

## Re-annotate stored runs against updated rules

Sync all runs with matching project fingerprint:

```bash
respro manage results my_results.db --sync myrespro.db
```

## Add manual interpretation fields

```bash
respro classify \
  --results-db my_results.db \
  --run-id 1 \
  --drug aciclovir \
  --phenotype resistant \
  --note "manual check"
```

## Regenerate reports

From a stored run:

```bash
respro regenerate \
  --project myrespro.db \
  --results-db my_results.db \
  --run-id 1 \
  --output my_output \
  --export pdf
```

From a JSON export:

```bash
respro regenerate \
  --project myrespro.db \
  --json my_output/sample_variants.results.json \
  --output my_output
```

!!! tip "Regenerate from JSON"
    You can also regenerate your result from a JSON file. This is useful for archival and deterministic reproduction without needing the original results database.
