---
title: Quickstart
description: Get started with ResPro in three steps
---

# Quickstart

Get ResistanceProfiler running in three steps.

## 1. Install

Install ResPro via bioconda:

```bash
conda install respro
```

Verify the installation:

```bash
respro --version
```

## 2. Initialize or download a database

**Option A:** Download a maintained database:

```bash
respro databases --list
respro databases --download db_name --output my_folder/
```

**Option B:** Create your own project database from GenBank and rules TSV files:

```bash
respro init \
    --name "My Project" \
    --genbank some_reference.gb \
    --rules rules.tsv \
    --output myrespro.db
```

For faster setup with less network-dependent enrichment, add `--no-additional-info` to skip PubChem/PubMed lookups.

See [Database Preparation](database-preparation.md) for metadata options and [Rules TSV Format](rules-format.md) for rule file details.

## 3. Run profiling

Profile a FASTA consensus sequence:

```bash
respro fasta \
    --project myrespro.db \
    --fasta my_consensus_sequence.fasta \
    --output my_output \
    --results-db my_results.db \
    --export json
```

Or profile a VCF file with its reference FASTA:

```bash
respro vcf \
    --project myrespro.db \
    --vcf my_ngs_result.vcf \
    --ref-fasta my_vcf_ref.fasta \
    --output my_output \
    --results-db my_results.db \
    --export json
```

The VCF may be multi-chrom and the reference FASTA multi-record (one record per
`CHROM`), allowing segmented viruses or multiple targets to be profiled in one run.

See [CLI Reference](cli-reference.md) for all commands and flags, and [Output Interpretation](output.md) for reading results.
