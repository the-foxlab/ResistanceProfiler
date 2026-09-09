---
title: ResistanceProfiler Manual
description: Pathogen-agnostic antiviral resistance profiling — full documentation
---

<img src="assets/logo.svg" alt="ResistanceProfiler" class="respro-manual-logo" />

# Manual

ResistanceProfiler (ResPro) classifies mutations in a pathogen's genome and helps interpret them as antiviral resistance markers. It works from a consensus sequence (FASTA) or variant calls (VCF) and compares them against a curated project database, producing one harmonized report per sample.

It is pathogen-agnostic: you build or download a project database for the pathogen you care about, then profile samples against it. ResPro comes as a command-line tool (CLI) and as a web app.

[Get started →](quickstart.md){ .md-button .md-button--primary }
[Install](install.md){ .md-button }

!!! warning "Research use only"
    This software supports exploratory interpretation and does not replace accredited clinical diagnostics.

!!! info "No database curation"
    We do not maintain or curate resistance databases ourselves. We only provide up-to-date converted [versions](https://github.com/the-foxlab/respro-databases) of openly available databases and are not responsible for their content or maintenance.

## Why use ResPro?

- **Works with any pathogen** — a general framework for genotypic resistance analysis, not a single-pathogen workflow.
- **One harmonized report** — classifies mutations and assists diagnostic interpretation in a single output.
- **Reusable project database** — curated rules and references stored in one SQLite file you can version and share.
- **Maintained databases available** — download pre-built databases directly via the CLI, or build your own.
- **Custom rule sets** — transform in-house databases into ResPro-compatible format.
- **Codon-aware profiling** — reference-normalised amino-acid mutation matching with automatic reference selection.

## Get started

1. **Install** ResPro — see [Installation](install.md) for all methods, or the [Quickstart](quickstart.md) for a condensed three-step guide.
2. **Get a database** — download a maintained database with `respro databases --download`, or build your own from GenBank and rules TSV files. See [Database Preparation](database-preparation.md) and [Rules TSV Format](rules-format.md).
3. **Profile samples** — run `respro fasta` or `respro vcf` against your database, then read the results. See [CLI Reference](cli-reference.md) and [Output Interpretation](output.md).
