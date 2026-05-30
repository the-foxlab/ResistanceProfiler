# Prepare a Database

ResistanceProfiler uses a project SQLite database (`project.db`) created from:

- at least one GenBank reference file
- one resistance [rules TSV](docs/user/rules-tsv-format.md)

If you are preparing your first database, make sure the rules file uses feature names that exist in the GenBank CDS annotations.

This database is the central asset of a ResPro workflow. It does not just store reference files. It defines the internal references, feature annotations, and curated rule set that later FASTA and VCF samples are compared against.

> [!IMPORTANT]
> Build `project.db` carefully and version it in your workflow. Most downstream interpretation quality depends on this curated project database.

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
- After initialization, later profiling runs use this database as the internal coordinate and rule source.

After this command succeeds, the file `myrespro.db` should exist.

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
  "tsv checksum": "sha256:abc123"
}
```

## Interpretation algorithms

`metadata.json` optionally supports a top-level `interpretation_algorithms` array. Each entry configures one algorithm by name. Each algorithm type may appear **at most once** in the list, and all five types can coexist.

### `ic50_thresholds`

Defines per-drug IC50 or fold-IC50 breakpoints. With tthis each rule that has a IC50 values associated will be classified for a phenotype during init.

- `use` — required; must be `"ic50"` or `"fold_ic50"`
- `thresholds` — required non-empty object; each key is a drug name; each value must have `"intermediate"` and `"resistant"` keys with positive numbers; `"resistant"` must be strictly greater than `"intermediate"`

### `drug_groups`

Assigns drugs to named groups (e.g. drug classes). This is only if you wish to group drugs in the final report.

- `groups` — required non-empty object; each key is a group name; each value is a non-empty list of drug name strings; a drug name may not appear in more than one group

### `drug_interpretation`

Specifies how per-drug evidence translates into a final interpretation in the report (`resistant`, `intermediate`, `sensitive`). Only one `drug_interpretation` entry is permitted per project, and its `method` field selects the strategy.

- `by_phenotype` — counts phenotype-labelled hits per drug and compares counts against thresholds
- `by_score` — sums score values per drug and compares totals against thresholds
- `by_ic50` — checks per-hit IC50 values per drug; if any value meets the resistant threshold the drug is resistant, otherwise if any value meets the intermediate threshold the drug is intermediate, otherwise sensitive
- `by_fold_ic50` — same logic as `by_ic50`, but using fold-IC50 values

- `method` — required; must be `"by_phenotype"`, `"by_score"`, `"by_ic50"`, or `"by_fold_ic50"`
- `thresholds` — required object; must include `"resistant"`; `"intermediate"` is optional
- for `by_phenotype` and `by_score`, threshold values must be positive integers
- for `by_ic50` and `by_fold_ic50`, threshold values must be positive numbers; if `intermediate` is set, `resistant` must be strictly greater than `intermediate`

### `drug_alias`

Defines canonical drug-name to short-alias mappings for report rendering.

- `groups` — required non-empty object; keys are canonical drug names; values are aliases
- each key and value must be a non-empty string
- alias values must be unique across canonical drug names

When configured, these mappings are written to the `drug.alias` column during `respro init`
and used for report drug labels, for example `Aciclovir (ACV)`.

### `frameshift_as_resistant`

Defines report-only metadata interpretation for observed frameshifts. This does not create curated database rule hits.

- `rules` — required non-empty list
- each rule must include `feature`, `reference`, and `drug` as case-sensitive exact non-empty strings
- each (`feature`, `reference`, `drug`) tuple must be unique across the list

Each matching observed frameshift produces a metadata hit row with a resistant phenotype. The generated hit always carries a `resistant` value in the `phenotype` field; the `clinical_phenotype` field is left empty.

### Example

```json
{
  "description": "HIV-1 integrase inhibitor resistance database",
  "interpretation_algorithms": [
    {
      "name": "ic50_thresholds",
      "use": "fold_ic50",
      "thresholds": {
        "ACV": {"intermediate": 3.0, "resistant": 10.0},
        "PCV": {"intermediate": 3.0, "resistant": 10.0}
      }
    },
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
      "name": "drug_alias",
      "groups": {
        "Aciclovir": "ACV",
        "Penciclovir": "PCV"
      }
    },
    {
      "name": "frameshift_as_resistant",
      "rules": [
        {
          "feature": "UL23",
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

> [!TIP]
> Use `--validate` in CI or curation review before importing rules into a production project database.

For detailed column and mutation token requirements, see `docs/user/rules-tsv-format.md`.
