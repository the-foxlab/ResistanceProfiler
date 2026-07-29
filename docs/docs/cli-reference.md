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
