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

The metadata JSON may include an `interpretation_algorithms` array. Each entry configures one algorithm by name. Each algorithm type may appear **at most once**, and all four types can coexist in the same database.

Algorithms are validated at `respro init` time and stored in the project database. Full descriptions live on the [Interpretation Algorithms](algorithms.md) page; below is a concise reference for each type and its keys.

### `drug_interpretation` — turn rule hits into a per-drug assessment

Combines the matched rules for a drug into one overall call. You can configure multiple entries with different `method` values — useful when a database mixes evidence types (for example, phenotype labels from one source and numeric scores from another).

**Methods:**

| Method | How it decides | Needs `thresholds`? |
|---|---|---|
| `by_phenotype` | The highest-rank phenotype label among the drug's hits wins. `contradictory` wins only when no severity hit exists. Hits with no label yield `susceptible`. | **No** — labels come from the rules, not the config. |
| `by_score` | Sums the `score` values per drug and compares the total against thresholds. | Yes |
| `by_ic50` | Checks each hit's IC50; the highest-rank label whose breakpoint is met wins, otherwise the rank-1 label. | Yes |
| `by_fold_ic50` | Same logic as `by_ic50`, using fold-IC50 values. | Yes |

**Keys:**

- `method` — required; one of `"by_phenotype"`, `"by_score"`, `"by_ic50"`, `"by_fold_ic50"`.
- `thresholds` — required for `by_score`, `by_ic50`, and `by_fold_ic50`; **not accepted** for `by_phenotype`. An object mapping phenotype labels (or bare ranks `1`–`5`) to threshold values. Must include `"resistant"`; `"intermediate"` is optional. Labels resolve via the [rank vocabulary](rules-format.md#phenotype-normalization), so multi-tier vocabularies like `{1, 3, 5}` work.
    - `by_score`: threshold values are positive integers.
    - `by_ic50` / `by_fold_ic50`: threshold values are positive numbers. If `intermediate` is set, `resistant` must be greater than `intermediate`. The config must include at least one rank-1 label (e.g. `susceptible`), which is returned when the value falls below all higher-rank breakpoints.
- `drug_thresholds` — optional list of per-drug overrides (see [Per-drug / per-reference overrides](algorithms.md#per-drug--per-reference-overrides)). Not accepted for `by_phenotype`.

Each `method` may appear at most once; two entries with the same `method` are rejected.

When multiple methods are configured, the report shows one assessment column per method plus a final **Assessment** column. The final call is strongest-wins by inferred rank: rank 5 > … > rank 1, with `contradictory` (-1) winning over severity and `unknown` (0) weakest.

!!! note "Label mismatches are warned, not fatal"
    If a config uses a label that is not present in the rules sheet (for example `"sensitive"` instead of the sheet's `"susceptible"` — same rank, different wording), `respro init` logs a non-fatal warning and continues. Fix the vocabulary if the mismatch is unintended.

### `drug_groups` — group drugs in the report

Assigns drugs to named groups (for example, drug classes) for display in the final report.

- `groups` — required non-empty object. Each key is a group name; each value is a non-empty list of drug name strings. A drug may not appear in more than one group.

### `drug_alias` — short drug names in the report

Maps canonical drug names to short aliases shown in the report (for example, `Aciclovir (ACV)`).

- `groups` — required non-empty object. Keys are canonical drug names; values are aliases. Each key and value must be a non-empty string, and aliases must be unique across drugs.

These mappings are written to the `drug.alias` column during `respro init`.

### `effect_as_resistant` — treat high-impact effects as resistant

Produces report-only metadata hits when a variant has a high-impact consequence in a given feature and reference. This does **not** create curated rule hits — it only adds a resistant metadata row for the configured drug.

- `rules` — required non-empty list. Each rule must include:
    - `feature` — case-sensitive exact feature name.
    - `reference` — case-sensitive exact reference name.
    - `drug` — case-sensitive exact drug name.
    - `effect` — a non-empty list of strings, each one of: `frameshift`, `stop_gained`, `stop_lost`, `start_lost`, `insertion`, `deletion`.
- Each (`feature`, `reference`, `drug`) tuple must be unique across the list.

Each rule states: if a variant in the given feature/reference has a consequence matching **any** of the listed effects, a metadata hit row with phenotype `resistant` is produced for that drug. The `clinical_phenotype` field is left empty.

This output is only produced when the database already contains at least one curated rule with a known phenotype or clinical phenotype.

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

Algorithms are validated at `respro init` time and stored in the `interpretation_algorithm` table of the project database. If you open an older database that predates this table, ResPro adds it automatically.
