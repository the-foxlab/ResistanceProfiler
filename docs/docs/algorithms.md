---
title: Interpretation Algorithms
description: Drug interpretation, IC50 thresholds, effect-as-resistant, and combination rules
---

# Interpretation Algorithms

Interpretation algorithms extend rule evaluation with additional logic. They are configured per project in the metadata JSON at `respro init` time and stored in the project database.

## Overview

ResPro evaluates resistance at multiple levels:

1. **Single rules** — each row in the primary rules TSV is an atomic mutation-to-interpretation mapping.
2. **Combination rules** — boolean formulas over atomic `member_id` values from grouped rules.
3. **Interpretation algorithms** — project-level logic that aggregates matched rules into per-drug results.

## Rule nomenclature basics

Rules are amino-acid-centric. A notation such as <span class="respro-pill">A123V</span> means reference amino acid A at position 123 changes to V.

| Type | Example | Meaning |
|---|---|---|
| Substitution | <span class="respro-pill">A123V</span> | Position 123 changed from A to V. |
| Anchored deletion | <span class="respro-pill">VG215V</span> | The G after position 215 is deleted. |
| Anchored insertion | <span class="respro-pill">V215VG</span> | Insertion of G after the V at position 215. |
| Frameshift | <span class="respro-pill">L201LfsX</span> | Reading-frame shift after the L at position 201. |
| Phenotype | <span class="respro-pill">sensitive / resistant</span> | Captures in-vitro susceptibility interpretation. |
| Clinical phenotype | <span class="respro-pill">sensitive / resistant</span> | Captures treatment-oriented interpretation where available. |

See [Rules TSV Format](rules-format.md) for the full mutation normalization reference.

## Combination rules

Combination rules allow interpretation based on boolean logic across multiple mutation members. They are defined separately from single rules (in the `--formula-rules` TSV) and evaluated with operators AND, OR, NOT, and XOR.

Combination members are evaluated based on a fixed member allele-frequency threshold (AF > 0.75 by default).

<div class="respro-operator-row" markdown>
<span class="respro-operator-pill respro-operator-pill-and">AND</span>

All specified mutations must be present.

`A AND B`
</div>

<div class="respro-operator-row" markdown>
<span class="respro-operator-pill respro-operator-pill-or">OR</span>

At least one specified mutation must be present.

`A OR B`
</div>

<div class="respro-operator-row" markdown>
<span class="respro-operator-pill respro-operator-pill-not">NOT</span>

The specified mutation must not be present.

`A AND NOT B`
</div>

<div class="respro-operator-row" markdown>
<span class="respro-operator-pill respro-operator-pill-xor">XOR</span>

Exactly one specified mutation must be present.

`A XOR B`
</div>

Single rules represent one mutation-to-interpretation mapping. Combination rules fire only when their formula conditions are satisfied. Parentheses are supported and should be used whenever precedence should be explicit.

## Interpretation algorithms

### `effect_as_resistant`

Defines report-only metadata interpretation for observed high-impact variant effects. This does not create curated database rule hits.

Configured high-impact variant effects (frameshift, stop_gained, stop_lost, start_lost, insertion, deletion) observed in a feature/reference pair are interpreted as <span class="respro-pill">phenotype='resistant'</span> for the configured drug. This algorithm does not set <span class="respro-pill">clinical_phenotype</span>.

Configuration keys:

- `rules` — required non-empty list
- each rule must include `feature`, `effect`, `reference`, and `drug` as case-sensitive exact non-empty strings
- `effect` — required non-empty list of strings; each must be one of: `frameshift`, `stop_gained`, `stop_lost`, `start_lost`, `insertion`, `deletion`
- each (`feature`, `reference`, `drug`) tuple must be unique across the list

This metadata output is only produced when the project database has at least one curated rule with a known phenotype or clinical phenotype.

Example:

```json
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
```

### `drug_interpretation`

Combines matched rules into one overall drug result. Depending on the database, this can be based on phenotype labels, scores, IC50 values, or fold-change cutoffs.

Supported methods:

- **`by_phenotype`** — counts phenotype-labelled hits per drug and compares counts against thresholds
- **`by_score`** — sums score values per drug and compares totals against thresholds
- **`by_ic50`** — checks per-hit IC50 values per drug; if any value meets the resistant threshold the drug is resistant, otherwise if any value meets the intermediate threshold the drug is intermediate, otherwise sensitive
- **`by_fold_ic50`** — same logic as `by_ic50`, but using fold-IC50 values

Configuration keys:

- `method` — required; must be `"by_phenotype"`, `"by_score"`, `"by_ic50"`, or `"by_fold_ic50"`
- `thresholds` — required object; must include `"resistant"`; `"intermediate"` is optional
- for `by_phenotype` and `by_score`, threshold values must be positive integers
- for `by_ic50` and `by_fold_ic50`, threshold values must be positive numbers; if `intermediate` is set, `resistant` must be strictly greater than `intermediate`
- each method may appear at most once; two entries with the same `method` are rejected

When multiple methods are configured, the report shows a per-method assessment column (plain text) alongside the final **Assessment** column. The final assessment uses strongest-wins resolution: `resistant` > `contradictory` > `intermediate` > `sensitive`. The most resistant result across all methods is taken as the final call.

Example:

```json
{
  "name": "drug_interpretation",
  "method": "by_phenotype",
  "thresholds": {
    "resistant": 1,
    "intermediate": 1
  }
}
```

### `ic50_thresholds`

Defines per-drug IC50 or fold-IC50 breakpoints. With this, each rule that has an IC50 value associated will be classified for a phenotype during init.

- `use` — required; must be `"ic50"` or `"fold_ic50"`
- `thresholds` — required non-empty object; each key is a drug name; each value must have `"intermediate"` and `"resistant"` keys with positive numbers; `"resistant"` must be strictly greater than `"intermediate"`

Example:

```json
{
  "name": "ic50_thresholds",
  "use": "fold_ic50",
  "thresholds": {
    "ACV": {"intermediate": 3.0, "resistant": 10.0},
    "PCV": {"intermediate": 3.0, "resistant": 10.0}
  }
}
```

### `drug_groups`

Assigns drugs to named groups (e.g. drug classes). This is only if you wish to group drugs in the final report.

- `groups` — required non-empty object; each key is a group name; each value is a non-empty list of drug name strings; a drug name may not appear in more than one group

Example:

```json
{
  "name": "drug_groups",
  "groups": {
    "Nucleoside Analogues": ["ACV", "PCV"],
    "Pyrophosphate Analogues": ["FOS"]
  }
}
```

### `drug_alias`

Defines canonical drug-name to short-alias mappings for report rendering.

- `groups` — required non-empty object; keys are canonical drug names; values are aliases
- each key and value must be a non-empty string
- alias values must be unique across canonical drug names

When configured, these mappings are written to the `drug.alias` column during `respro init` and used for report drug labels, for example `Aciclovir (ACV)`.

Example:

```json
{
  "name": "drug_alias",
  "groups": {
    "Aciclovir": "ACV",
    "Penciclovir": "PCV"
  }
}
```

## Phenotype and clinical phenotype

Two independent phenotype fields are tracked per rule:

- **Phenotype** — captures in-vitro susceptibility interpretation (resistant, intermediate, sensitive, unknown).
- **Clinical phenotype** — captures treatment-oriented interpretation where available.

Both are normalized independently from flexible input values. See [Rules TSV Format](rules-format.md) for the full normalization table.

## Full configuration example

For a complete metadata JSON with all interpretation algorithms, see [Database Preparation](database-preparation.md).
