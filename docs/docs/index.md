---
title: ResistanceProfiler Manual
description: Pathogen-agnostic antiviral resistance profiling — full documentation
---

<img src="assets/logo.svg" alt="ResistanceProfiler" class="respro-manual-logo" />

# Manual

Pathogen-agnostic antiviral resistance profiling from consensus sequences or VCF-derived variants. One harmonized report that classifies mutations and assists diagnostic interpretations against curated project databases.

[Get started →](quickstart.md){ .md-button .md-button--primary }
[Install](install.md){ .md-button }

!!! warning "Research use only"
    This software supports exploratory interpretation and does not replace accredited clinical diagnostics.

!!! info "No database curation"
    We do not maintain or curate resistance databases ourselves. We only provide up-to-date converted [versions](https://github.com/the-foxlab/respro-databases) of openly available databases and are not responsible for their content or maintenance.

## Why use ResPro?

- **Framework for genotypic resistance analysis** — not a single pathogen-specific workflow
- **One harmonized report** classifies mutations and assists diagnostic interpretations
- **Reusable project database** — curated rules and references stored in one SQLite file
- **Maintained databases available** — download pre-ported databases directly via the CLI
- **Custom rule sets** — transform in-house databases into ResPro-compatible format
- **Codon-aware profiling** — reference-normalized amino-acid mutation matching with automatic reference selection

## Get started

1. **Install** ResPro — see [Installation](install.md) for full details or the [Quickstart](quickstart.md) for a condensed three-step guide.
2. **Prepare a database** — either download a maintained database or initialize one from your own GenBank and rules TSV files. See [Database Preparation](database-preparation.md) and [Rules TSV Format](rules-format.md).
3. **Profile samples** — run FASTA or VCF profiling and interpret results. See [CLI Reference](cli-reference.md) and [Output Interpretation](output.md).
