---
title: Database Preparation
description: Create and extend a project database
---

# Database Preparation

ResistanceProfiler uses a project SQLite database (`project.db`) created from:

- at least one GenBank reference file
- one resistance [rules TSV](rules-format.md)

If you are preparing your first database, make sure the rules file uses feature names that exist in the GenBank CDS annotations.

This database is the central asset of a ResPro workflow. It does not just store reference files. It defines the internal references, feature annotations, and curated rule set that later FASTA and VCF samples are compared against.

!!! important "Build carefully and version it"
    Build `project.db` carefully and version it in your workflow. Most downstream interpretation quality depends on this curated project database.

## Create a new project database

```bash
respro init \
  --name "Docs Demo" \
  --genbank some_reference.gb \
  --rules rules.tsv \
  --output myrespro.db \
  --no-additional-info
```

Notes:

- `--genbank` can be repeated for multiple files.
- `--no-additional-info` skips network lookups for extra metadata.
- `--metadata` accepts a JSON file with curated project metadata. See the section below for the supported keys and value rules.
- `--example` optionally stores a single-record consensus FASTA shipped with the database. Users can then profile it via `respro fasta --example` or via the webapp "Example" button. Use `respro add --example` to overwrite and `respro add --no-example` to clear it.
- After initialization, later profiling runs use this database as the internal coordinate and rule source.

After this command succeeds, the file `myrespro.db` should exist.

### Phenotype input is strict

The `phenotype` and `clinical_phenotype` columns in the rules TSV are normalized against the rank vocabulary described in [Rules Format](rules-format.md#phenotype-normalization). In short: labels are stored verbatim (lowercased + whitespace-stripped), bare ranks `1`–`5` resolve to canonical fallback labels, empty cells mean *unknown* (rank 0), and any non-empty unknown label **hard-fails** during `respro init`.

> **Breaking change:** databases built before this version must be rebuilt — there is no automatic migration.

## Optional metadata JSON

`respro init --metadata` accepts a JSON file whose top-level value must be an object. The file is used to populate project metadata fields during database creation.

Supported canonical keys are `maintainers`, `contact`, `publication_pmid`, `website`, `description`, `maintainer_update`, `license`, and `tsv_checksum`.

Common aliases are accepted for a few keys: `maintainer` maps to `maintainers`, `publication` and `pmid` map to `publication_pmid`, `maintainer update` maps to `maintainer_update`, and `tsv checksum` maps to `tsv_checksum`.

Value rules are strict. `maintainers` may be either a string or a list of strings. `publication_pmid` must contain digits only. All other supported fields must be strings. Empty values are ignored, and unknown keys are rejected. When a PMID is provided, ResPro also tries to resolve the DOI automatically from PubMed when one is available.

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
      "method": "by_phenotype",
      "thresholds": {
        "resistant": 1,
        "intermediate": 1
      }
    }
  ]
}
```

> Algorithms are optional. See [Interpretation algorithms](#interpretation-algorithms) below for all supported types and configuration keys.

## Interpretation algorithms

`metadata.json` optionally supports a top-level `interpretation_algorithms` array. Each entry configures one algorithm by name. Each algorithm type may appear **at most once** in the list, and all four types can coexist.

Detailed algorithm descriptions are on the [Interpretation Algorithms](algorithms.md) page. Below is a summary of each type and its configuration keys.

### `drug_groups`

Assigns drugs to named groups (e.g. drug classes). This is only if you wish to group drugs in the final report.

- `groups` — required non-empty object; each key is a group name; each value is a non-empty list of drug name strings; a drug name may not appear in more than one group

### `drug_interpretation`

Turns per-drug evidence into a final report assessment. Threshold keys are **phenotype labels** (or bare ranks `1`–`5`) resolved via the [rank vocabulary](rules-format.md#phenotype-normalization), so the report shows the stored verbatim label coloured by its inferred rank. Multiple entries may coexist, each with a different `method` — useful when a database mixes evidence types (e.g. phenotype labels from one source, numeric scores from another).

Supported methods:

- `by_phenotype` — the highest-rank phenotype label among the drug's hits wins; contradictory wins only when no severity hit exists; hits with no severity/contradictory label yield `susceptible`. No `thresholds` key is accepted.
- `by_score` — sum score values per drug, compare against thresholds
- `by_ic50` — the highest-rank label whose breakpoint is met wins; otherwise the configured rank-1 label
- `by_fold_ic50` — same as `by_ic50`, using fold-IC50 values

Keys:

- `method` — required; one of `"by_phenotype"`, `"by_score"`, `"by_ic50"`, `"by_fold_ic50"`
- `thresholds` — required object mapping labels (or bare ranks) to thresholds; must include `"resistant"`; `"intermediate"` is optional. Labels resolve via the rank vocabulary, so multi-tier vocabularies like `{1, 3, 5}` work. **Not accepted for `by_phenotype`**.
- `by_score`: threshold values are positive integers
- `by_ic50` / `by_fold_ic50`: threshold values are positive numbers; if `intermediate` is set, `resistant > intermediate`; the config must include at least one rank-1 label (e.g. `susceptible`), returned when the value falls below all higher-rank breakpoints
- each method may appear at most once
- `drug_thresholds` — optional per-`(reference, drug)` overrides; not accepted for `by_phenotype`. See [Interpretation Algorithms](algorithms.md)

With multiple methods, the report shows one assessment column per method plus a final **Assessment** column. The final call is strongest-wins by inferred rank: rank 5 > … > rank 1, with `contradictory` (-1) winning over severity and `unknown` (0) weakest.

> **Note:** if a config declares a label not present in the rules sheet (e.g. `"sensitive"` vs the sheet's `"susceptible"` — same rank, different wording), `respro init` logs a non-fatal warning. Processing continues; fix the vocabulary if the mismatch is unintended.

### `drug_alias`

Defines canonical drug-name to short-alias mappings for report rendering.

- `groups` — required non-empty object; keys are canonical drug names; values are aliases
- each key and value must be a non-empty string
- alias values must be unique across canonical drug names

When configured, these mappings are written to the `drug.alias` column during `respro init` and used for report drug labels, for example `Aciclovir (ACV)`.

### `effect_as_resistant`

Defines report-only metadata interpretation for observed high-impact variant effects. This does not create curated database rule hits.

- `rules` — required non-empty list
- each rule must include `feature`, `effect`, `reference`, and `drug` as case-sensitive exact non-empty strings
- `effect` — required non-empty list of strings; each must be one of: `frameshift`, `stop_gained`, `stop_lost`, `start_lost`, `insertion`, `deletion`
- each (`feature`, `reference`, `drug`) tuple must be unique across the list

Each rule states: if a variant annotation in the given feature/reference has a consequence matching **any** of the listed effects, produce a metadata hit row with a resistant phenotype for the specified drug. The generated hit always carries a `resistant` value in the `phenotype` field; the `clinical_phenotype` field is left empty.

This metadata output is only produced when the project database has at least one curated rule with a known phenotype or clinical phenotype.

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
      "method": "by_phenotype",
      "thresholds": {
        "resistant": 1,
        "intermediate": 1
      }
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

Algorithms are validated at `respro init` time and stored in the `interpretation_algorithm` table of the project database. Existing databases without this table are migrated automatically on next open.

## Inspect project metadata

```bash
respro manage database myrespro.db --info
```

## Inspect imported rules

```bash
respro manage database myrespro.db --rules
```

## Validate new rules without changing the database

```bash
respro add \
  --project myrespro.db \
  --rules rules.tsv \
  --validate
```

!!! tip
    Use `--validate` in CI or curation review before importing rules into a production project database.

For detailed column and mutation token requirements, see [Rules TSV Format](rules-format.md).
