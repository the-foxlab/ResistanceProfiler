---
title: Interpretation Algorithms
description: Drug interpretation, IC50 thresholds, effect-as-resistant, and combination rules
---

# Interpretation Algorithms

Interpretation algorithms extend rule evaluation with additional logic. They are configured per project in the metadata JSON at `respro init` time and stored in the project database. See [Database Preparation](database-preparation.md) for how to add them.

## How ResPro evaluates resistance

ResPro evaluates resistance at three levels:

1. **Single rules** — each row in the primary rules TSV maps one mutation to an interpretation.
2. **Combination rules** — boolean formulas over single-rule `member_id` values (e.g. "mutation A AND mutation B").
3. **Interpretation algorithms** — project-level logic that aggregates matched rules into per-drug results in the report.

## Rule notation basics

Rules are amino-acid-centric. A notation such as <span class="respro-pill">A123V</span> means the reference amino acid A at position 123 changes to V.

| Type | Example | Meaning |
|---|---|---|
| Substitution | <span class="respro-pill">A123V</span> | Position 123 changed from A to V. |
| Anchored deletion | <span class="respro-pill">VG215V</span> | The G after position 215 is deleted. |
| Anchored insertion | <span class="respro-pill">V215VG</span> | Insertion of G after the V at position 215. |
| Frameshift | <span class="respro-pill">L201LfsX</span> | Reading-frame shift after the L at position 201. |
| Phenotype | <span class="respro-pill">sensitive / resistant</span> | In-vitro susceptibility interpretation. |
| Clinical phenotype | <span class="respro-pill">sensitive / resistant</span> | Treatment-oriented interpretation, where available. |

See [Rules TSV Format](rules-format.md) for the full mutation normalisation reference.

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

### `drug_interpretation` — turn rule hits into a per-drug assessment

Combines the matched rules for a drug into one overall result. Depending on your database, this can be based on phenotype labels, scores, IC50 values, or fold-change cutoffs. Threshold keys are **phenotype labels** (or bare ranks `1`–`5`) resolved via the [rank vocabulary](rules-format.md#phenotype-normalization); the report shows the stored verbatim label coloured by its inferred rank, so multi-tier vocabularies (e.g. `{1, 3, 5}`) work without special-casing.

**Methods:**

| Method | How it decides | Needs `thresholds`? |
|---|---|---|
| `by_phenotype` | The highest-rank phenotype label among the drug's hits wins. `contradictory` wins only when no severity hit exists. Hits with no label yield `susceptible`. | **No** — labels come from the rules, not the config. |
| `by_score` | Sums the `score` values per drug and compares the total against thresholds. | Yes |
| `by_ic50` | Checks each hit's IC50; the highest-rank label whose breakpoint is met wins, otherwise the rank-1 label. | Yes |
| `by_fold_ic50` | Same logic as `by_ic50`, using fold-IC50 values. | Yes |

**Configuration keys:**

- `method` — required; must be `"by_phenotype"`, `"by_score"`, `"by_ic50"`, or `"by_fold_ic50"`.
- `thresholds` — required for `by_score`, `by_ic50`, and `by_fold_ic50`; **not accepted** for `by_phenotype`. An object mapping phenotype labels (or bare ranks) to threshold values. Must include `"resistant"`; `"intermediate"` is optional. Labels are lowercased + whitespace-stripped and resolved via the rank vocabulary, so multi-tier configs work.
    - `by_score`: threshold values are positive integers.
    - `by_ic50` / `by_fold_ic50`: threshold values are positive numbers. If `intermediate` is set, `resistant` must be strictly greater than `intermediate`. The config must include at least one rank-1 label (e.g. `susceptible`), which is returned when the value falls below all higher-rank breakpoints.
- `drug_thresholds` — optional list of per-drug overrides; not accepted for `by_phenotype`. See [Per-drug / per-reference overrides](#per-drug--per-reference-overrides) below.

Each `method` may appear at most once; two entries with the same `method` are rejected.

When multiple methods are configured, the report shows a per-method assessment column (plain text) alongside the final **Assessment** column. The final assessment is strongest-wins by inferred rank: rank 5 (resistant) > … > rank 1 (susceptible), with `contradictory` (rank -1) winning over severity and `unknown` (rank 0) weakest. The most severe result across all methods becomes the final call.

When `drug_thresholds` overrides are configured, each per-method assessment cell in the report shows an info icon on hover naming the resolved thresholds and their source (override `(reference, drug)`, override `(drug)`, or global default). The table layout, badge styling, and final Assessment column are unchanged; without overrides the report renders identically to the global-only case.

Example:

```json
{
  "name": "drug_interpretation",
  "method": "by_phenotype"
}
```

A numeric example with a rank-1 label:

```json
{
  "name": "drug_interpretation",
  "method": "by_ic50",
  "thresholds": {
    "susceptible": 0.0,
    "intermediate": 3.0,
    "resistant": 10.0
  }
}
```

### Per-drug / per-reference overrides

`drug_interpretation` accepts an optional `drug_thresholds` list that overrides the global `thresholds` for specific drugs, optionally scoped to a single reference. This is useful when a drug needs finer breakpoints than the database-wide default, or when the same drug has different breakpoints across references (e.g. different viral species).

Each entry is an object with:

- `reference` — optional non-empty string; when present, the override applies only to rules/drugs whose reference matches (accession-version tolerant, e.g. `NC_001345.1` matches `NC_001345`)
- `drug` — required non-empty string; the drug name to override
- `thresholds` — required object with the same shape and constraints as the parent algorithm's `thresholds`:
  - must include `resistant`; `intermediate` is optional; integer for `by_score`, positive number for `by_ic50`/`by_fold_ic50` (with `resistant` > `intermediate` when `intermediate` is set)

Resolution precedence (most specific wins):

1. an override matching `(reference, drug)`
2. an override matching `(drug)` only (no `reference`)
3. the global `thresholds`

Duplicate `(reference, drug)` or `(drug)`-only keys are rejected. Two overrides whose `reference` values differ only by accession version (e.g. `NC_001345` and `NC_001345.1`) are treated as the same key and rejected as duplicates. Overrides with no matching drug or reference fall back to the next precedence level. The Database Dashboard condenses overrides for display: entries sharing the same `reference` (shown as `(all)` when absent) and the same threshold values collapse into one row with a sorted drug set, mirroring the `effect_as_resistant` condensing.

When a drug has hits under multiple references in a single report (e.g. a multi-species run), the reference used to resolve per-(reference, drug) overrides is selected deterministically as the alphabetically first reference name. This keeps the report output stable across process invocations (set iteration order is otherwise hash-seed dependent).

Example — `drug_interpretation` with a per-reference override:

```json
{
  "name": "drug_interpretation",
  "method": "by_score",
  "thresholds": {"resistant": 1, "intermediate": 1},
  "drug_thresholds": [
    {"reference": "ref", "drug": "ACV", "thresholds": {"resistant": 1, "intermediate": 1}}
  ]
}
```

### `effect_as_resistant` — treat high-impact effects as resistant

Produces report-only metadata hits when a variant has a high-impact consequence in a given feature and reference. This does **not** create curated rule hits — it only adds a resistant metadata row for the configured drug.

Configured high-impact variant effects (frameshift, stop_gained, stop_lost, start_lost, insertion, deletion) observed in a feature/reference pair are interpreted as <span class="respro-pill">phenotype='resistant'</span> for the configured drug. This algorithm does not set <span class="respro-pill">clinical_phenotype</span>.

Configuration keys:

- `rules` — required non-empty list. Each rule must include:
    - `feature` — case-sensitive exact feature name.
    - `reference` — case-sensitive exact reference name.
    - `drug` — case-sensitive exact drug name.
    - `effect` — a non-empty list of strings, each one of: `frameshift`, `stop_gained`, `stop_lost`, `start_lost`, `insertion`, `deletion`.
- Each (`feature`, `reference`, `drug`) tuple must be unique across the list.

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

### `drug_groups` — group drugs in the report

Assigns drugs to named groups (for example, drug classes) for display in the final report.

- `groups` — required non-empty object. Each key is a group name; each value is a non-empty list of drug name strings. A drug may not appear in more than one group.

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

### `drug_alias` — short drug names in the report

Maps canonical drug names to short aliases shown in the report (for example, `Aciclovir (ACV)`).

- `groups` — required non-empty object. Keys are canonical drug names; values are aliases. Each key and value must be a non-empty string, and aliases must be unique across drugs.

These mappings are written to the `drug.alias` column during `respro init`.

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

- **Phenotype** — in-vitro susceptibility interpretation.
- **Clinical phenotype** — treatment-oriented interpretation, where available.

Both are stored verbatim (lowercased + whitespace-stripped) and resolved to a rank via the [rank vocabulary](rules-format.md#phenotype-normalization) (1 = susceptible … 5 = resistant; sentinels 0 = unknown, -1 = contradictory). Severity comparison and report colouring use the inferred rank so multi-tier vocabularies (e.g. `{susceptible, low-level resistance, resistant}`) work uniformly.

## Full configuration example

For a complete metadata JSON with all interpretation algorithms, see [Database Preparation](database-preparation.md).
