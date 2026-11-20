---
title: Database Preparation
description: Create and extend a project database
---

# Database Preparation

A ResPro **project database** is a single SQLite file that holds your reference sequences, gene annotations, and curated resistance rules. Every profiling run compares new samples against this database, so it is the central asset of your workflow.

!!! important "Build it once, then version it"
    Take care building your project database and keep it under version control. The quality of every report depends on the rules and references you curate here.

## What you need

A project database is built from two inputs:

- **One or more GenBank reference files** — these define the reference sequences and the gene (CDS) annotations.
- **One rules TSV file** — your curated resistance rules. See [Rules TSV Format](rules-format.md) for the column layout.

!!! tip "Match feature names"
    If this is your first database, make sure every `feature` name in your rules TSV exists as a CDS or mat_peptide annotation in your GenBank file. ResPro checks this during import and rejects unknown features.

## Create a new project database

```bash
respro init \
  --name "Docs Demo" \
  --genbank some_reference.gb \
  --rules rules.tsv \
  --output myrespro.db \
  --no-additional-info
```

Options:

- `--genbank` — repeat this flag to include multiple GenBank files.
- `--no-additional-info` — skip network lookups for extra drug and publication metadata. Use this for a faster, offline build.
- `--metadata` — attach a JSON file with curated project metadata (see [Metadata JSON](#optional-metadata-json) below).
- `--example` — store a single-record consensus FASTA alongside the database. Users can then profile it with `respro fasta --example` or click the "Example" button in the web app. Use `respro add --example` to overwrite it later and `respro add --no-example` to remove it.

After the command finishes, the file `myrespro.db` exists and is ready for profiling.

### Phenotype labels are checked strictly

The `phenotype` and `clinical_phenotype` columns in the rules TSV are normalized against the rank vocabulary described in [Rules Format](rules-format.md#phenotype-normalization). In short: labels are stored verbatim (lowercased + whitespace-stripped), bare ranks `1`–`5` resolve to canonical fallback labels, empty cells mean *unknown* (rank 0), and any non-empty unknown label **hard-fails** during `respro init`.

### Drug metadata enrichment fallback chain

Without `--no-additional-info`, ResPro enriches each drug in the rules TSV with a description and a link via a three-stage fallback chain. The first stage that succeeds wins:

1. **PubChem compound** — resolves the drug name to a CID and stores the compound URL, a short description, and a 2D structure image URL.
2. **PubChem substance** — monoclonal antibodies and similar biologics have no PubChem compound record, so on a CID miss ResPro looks the name up as a PubChem *substance* instead. The lowest substance ID (SID) is used as the canonical record; the drug card then links to the substance page and shows a synonym as the description text. Substance records have no structure image, so no structure button is shown for these drugs.
3. **Wikipedia** — drugs with no PubChem record at all (neither compound nor substance) get a one-paragraph description from the unauthenticated Wikipedia REST summary endpoint. No PubChem link is stored in this case.

Drugs where the whole chain definitively finds nothing (an HTTP 404 at every stage) are flagged in the database (`pubchem_lookup_failed = 1`) and skipped on subsequent `respro add`/enrichment runs, so a drug that cannot be resolved does not re-trigger network lookups every time. Flagged drugs stay excluded until the flag is reset manually, e.g. with `UPDATE drug SET pubchem_lookup_failed = 0` in the database — after that, the next enrichment run retries the drug. Transient failures (no network access, unexpected API responses) do *not* set the flag: the drug is simply retried on the next run.

!!! note "Wikipedia attribution"
    Descriptions for CID-less drugs come from Wikipedia and are licensed under **CC BY-SA**; the stored text is used as-is without embedded attribution. When redistributing a database or report that includes such descriptions, attribute Wikipedia accordingly.

## Add rules to an existing database

You can extend a database with new rules (and optionally new GenBank references) without rebuilding it from scratch:

```bash
respro add \
  --project myrespro.db \
  --rules new_rules.tsv \
  --formula-rules new_formula.tsv
```

To only check whether a rules file is valid — without writing anything to the database — add `--validate`:

```bash
respro add \
  --project myrespro.db \
  --rules new_rules.tsv \
  --validate
```

!!! tip "Use --validate in review"
    Run `--validate` in your CI or curation review step before importing rules into a production database.

## Inspect a database

View project metadata:

```bash
respro manage database myrespro.db --info
```

List all curated rules:

```bash
respro manage database myrespro.db --rules
```

Filter rules by reference (partial, case-insensitive match):

```bash
respro manage database myrespro.db --rules --reference NC_001806
```

To list only single rules or only combination (formula) rules, use `--list-single` or `--list-combi` instead of `--rules`.

For detailed column and mutation token requirements, see [Rules TSV Format](rules-format.md).

## Optional metadata JSON

`respro init --metadata` accepts a JSON file that fills in project metadata fields when the database is created. The top-level value must be an object.

**Supported keys:** `maintainers`, `contact`, `publication_pmid`, `website`, `description`, `maintainer_update`, `license`, `tsv_checksum`.

A few common aliases are accepted: `maintainer` → `maintainers`, `publication` and `pmid` → `publication_pmid`, `maintainer update` → `maintainer_update`, `tsv checksum` → `tsv_checksum`.

**Value rules:**

- `maintainers` — a string or a list of strings.
- `publication_pmid` — digits only.
- All other supported fields — strings.
- Empty values are ignored; unknown keys are rejected.

When a PMID is provided, ResPro tries to resolve the DOI automatically from PubMed.

Example metadata file:

```json
{
  "maintainers": ["A Curator", "B Curator"],
  "contact": "team@example.org",
  "publication": "12345678",
  "website": "https://example.org/db",
  "description": "Curated antiviral resistance database.",
  "maintainer update": "2026-04-21",
  "license": "CC-BY-4.0",
  "tsv checksum": "sha256:abc123",
  "interpretation_algorithms": [
    {
      "name": "drug_interpretation",
      "method": "by_phenotype"
    }
  ]
}
```

> Algorithms are optional. See [Interpretation algorithms](#interpretation-algorithms) below for all supported types and configuration keys.

## Interpretation algorithms

The metadata JSON may include an `interpretation_algorithms` array configuring any combination of the four algorithm types below. All algorithms are validated at `respro init` time and stored in the project database.

| Algorithm | Purpose | Repeatable? |
|---|---|---|
| `drug_interpretation` | Combines matched rules per drug into an overall assessment (`by_phenotype`, `by_score`, `by_ic50`, or `by_fold_ic50`), with optional per-drug / per-reference `drug_thresholds` overrides. | Once per `method` |
| `drug_groups` | Groups drugs into named classes (e.g. *Nucleoside Analogues*) for report display. | At most once |
| `drug_alias` | Maps canonical drug names to short aliases shown in reports (e.g. `Aciclovir (ACV)`). | At most once |
| `effect_as_resistant` | Adds report-only `resistant` metadata rows when selected high-impact consequences occur in a configured feature/reference pair. | At most once |

See [Interpretation Algorithms](algorithms.md) for the complete schema, threshold rules, and resolution precedence for each algorithm.

!!! note "Label mismatches are warned, not fatal"
    If a `drug_interpretation` config uses a label that is not present in the rules sheet (for example `"sensitive"` instead of the sheet's `"susceptible"` — same rank, different wording), `respro init` logs a non-fatal warning and continues. Fix the vocabulary if the mismatch is unintended.

### Example

```json
{
  "description": "HSV database",
  "interpretation_algorithms": [
    {
      "name": "drug_groups",
      "groups": {
        "Nucleoside Analogues": ["ACV", "PCV"],
        "Pyrophosphate Analogues": ["FOS"]
      }
    },
    {
      "name": "drug_interpretation",
      "method": "by_phenotype"
    },
    {
      "name": "drug_interpretation",
      "method": "by_score",
      "thresholds": {
        "resistant": 5,
        "intermediate": 2
      }
    },
    {
      "name": "drug_alias",
      "groups": {
        "Aciclovir": "ACV",
        "Penciclovir": "PCV"
      }
    },
    {
      "name": "effect_as_resistant",
      "rules": [
        {
          "feature": "UL23",
          "effect": ["frameshift", "stop_gained", "stop_lost"],
          "reference": "NC_001806",
          "drug": "Aciclovir"
        }
      ]
    }
  ]
}
```

Algorithms are validated at `respro init` time and stored in the project database. If a database was created with an older incompatible schema, rebuild it with `respro init`.
