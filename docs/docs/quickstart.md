---
title: Quickstart
description: Get started with ResPro in three steps
---

# Quickstart

Get ResistanceProfiler running in three steps.

## 1. Install

Create a virtual environment and install ResPro:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify the installation:

```bash
respro --version
```

!!! note
    If installation fails during a `mappy` build step, see the [Installation](install.md) page for troubleshooting steps.

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

See [CLI Reference](cli-reference.md) for all commands and flags, and [Output Interpretation](output.md) for reading results.
