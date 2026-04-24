# Rules TSV Format Reference

This document is the source of truth for curated rules TSV files used by `respro init`
and `respro add`.

## Overview

- The primary rules TSV defines atomic mutation rules, one row per mutation.
- Atomic rules can carry their own metadata or act mainly as building blocks for higher-order rules.
- Optional boolean combination rules can be provided in a second TSV via `--formula-rules`.
- Grouped combinatorial association is defined via `group_id` + `member_id` in the primary TSV and a matching `group_id` row in the formula TSV.

---

## Single Rules

### Required columns

| Column                   | Meaning                          | Constraints                                                         |
| ------------------------ | -------------------------------- | ------------------------------------------------------------------- |
| `member_id`            | Stable atomic member identifier  | Required for rows that belong to a combination group (`group_id`) |
| `gene`                 | CDS/gene name                    | Must match a gene loaded from GenBank                               |
| `reference_identifier` | Reference accession or name      | Must match a reference in the project DB                            |
| `position`             | Amino-acid position              | File-wide 0-based or 1-based, auto-detected                         |
| `reference`            | Reference amino acid at position | Checked against reference AA sequence                               |
| `mutation`             | Alternate amino-acid event token | Normalized to canonical mutation forms                              |
| `antiviral`            | Drug name                        | Stored normalized (lowercase)                                       |

### Optional columns

| Column                         | Meaning                               |
| ------------------------------ | ------------------------------------- |
| `phenotype`                  | Rule-level phenotype interpretation   |
| `clinical_phenotype`         | Clinical interpretation field         |
| `ic50` / `ic_50`           | Absolute IC50 value                   |
| `fold_ic50` / `fold_ic_50` | Fold IC50 value                       |
| `publication`                | DOI, PMID, or source publication text |
| `source`                     | Provenance label                      |
| `comment`                    | Free-text curator note                |
| `group_id`                   | Combination group key                 |
| `score`                      | Numeric quality/evidence score        |

Notes:

- `member_id` values must be unique when provided.
- `member_id` values must not use reserved boolean keywords such as `AND`, `OR`, `NOT`, or `XOR`.
- Rows with `group_id` must also provide `member_id` when `--formula-rules` is used.
- If grouped rows exist but `--formula-rules` is omitted, the project DB is still built and atomic rules are imported, but combinatorial rules are ignored with a warning.

### Coordinate rules (`position`)

- Positions are interpreted as amino-acid coordinates.
- Import auto-detects 0-based vs 1-based using the `reference` AA and GenBank translation.
- Use one coordinate system consistently across the entire TSV file.

### Mutation token rules (`mutation`)

Supported canonical categories:

1. Substitution: single AA (`V`) or rewrite notation (`A336V`)
2. Stop: `*`, `STOP`, rewrite with stop (`A336*`)
3. Frameshift: `fs`, `fsX`, `A336fs`, `A336frameshift`
4. Insertion: canonical and HGVS-like insertion forms
5. Deletion: canonical and HGVS-like deletion forms

Notes:

- Non-standard wildcard tokens (for example `X`, `any`) are not accepted as rule alleles.
- No-op rules (`mutation == reference`) are rejected.
- Mutation tokens are normalized before DB insertion to support deterministic matching.

### How mutation normalization works

Normalization means that different textual inputs describing the same biological event
are converted into one canonical representation before rules are stored and matched.

High-level processing order:

1. Read row context (`gene`, `reference_identifier`, `position`, `reference`).
2. Detect mutation category (substitution, stop, frameshift, insertion, deletion).
3. Normalize token spelling to canonical internal form.
4. Validate against reference protein context.
5. Store explicit allele state for deterministic matching.

### Normalization examples (input -> canonical interpretation)

#### Substitutions and stop events

| Input mutation | Example row context               | Canonical interpretation |
| -------------- | --------------------------------- | ------------------------ |
| `V`          | `reference=A`, `position=336` | substitution to `V`    |
| `A336V`      | `reference=A`, `position=336` | substitution to `V`    |
| `STOP`       | `reference=A`, `position=336` | stop mutation (`*`)    |
| `A336*`      | `reference=A`, `position=336` | stop mutation (`*`)    |

#### Frameshift events

Frameshift rules are normalized to the anchored form `XfsX`, where `X` is the
reference amino acid anchor from the row context.

| Input mutation  | Example row context                                   | Canonical interpretation               |
| --------------- | ----------------------------------------------------- | -------------------------------------- |
| `fs`          | `reference=K`, `position=715`, `mutation=KfsX` | frameshift after the K at position 715 |
| `frameshift`  | `reference=K`, `position=715`, `mutation=KfsX` | frameshift after the K at position 715 |
| `K715fs`      | `reference=K`, `position=715`, `mutation=KfsX` | frameshift after the K at position 715 |
| `K715fsATFF*` | `reference=K`, `position=715`, `mutation=KfsX` | frameshift after the K at position 715 |

Important note:

- Any trailing sequence-like suffix after `fs` is not treated as a separate allele.
  It is collapsed to the canonical frameshift sentinel form.

#### In-frame insertion events

Insertion rules are normalized to a canonical insertion allele that preserves:

- anchor amino acid,
- anchor position,
- resulting inserted payload context.

| Input mutation   | Example row context                                 | Canonical interpretation                                                |
| ---------------- | --------------------------------------------------- | ----------------------------------------------------------------------- |
| `F50FGG`       | `reference=F`, `position=50`, `mutation=FGG` | GG insertion after the F at position 50                                 |
| `F50_F51insGG` | `reference=F`, `position=50`, `mutation=FGG` | GG insertion after the F at position 50                                 |
| `insGG`        | `reference=F`, `position=50`, `mutation=FGG` | GG insertion after the F at position 50, anchor resolved from reference |

| Input mutation | Example row context                                 | Canonical interpretation                                           |
| -------------- | --------------------------------------------------- | ------------------------------------------------------------------ |
| `FGG50F`     | `reference=FGG`, `position=50`, `mutation=F` | GG deletion after F at position 50                                 |
| `F50delGG`   | `reference=FGG`, `position=50,` `mutation=F` | GG deletion after F at position 50                                 |
| `Q35del`     | `reference=FGG`, `position=50,` `mutation=F` | GG deletion after F at position 50, anchor resolved from reference |

Anchor-less deletion note:

- Helper forms like `Q35del` depend on upstream sequence context.
- If the required anchor context is biologically inconsistent with the reference, the row is rejected or skipped according to validation rules.

### Complex and edge-case events

#### Complex in-frame events

Some observed sample variants can be complex (for example mixed mid-codon insertions/deletions). In reporting, these may appear as complex consequence classes.

For curated rules TSV:

- encode biologically specific events as explicit insertion or deletion alleles,
- avoid wildcard-like shorthand for complex events,
- prefer decomposed, clearly interpretable rule entries.

#### Unsupported ambiguity patterns

Examples that are not accepted as mutation rules:

- wildcard-like tokens (`X`, `any`),
- malformed HGVS-like fragments that cannot be resolved,
- no-op entries where `mutation` equals `reference`.

### TSV-to-database interpretation

During import, in-frame indels are converted to explicit allele representations so matching is exact:

- insertion rules track anchor and inserted payload
- deletion rules track deleted block and resulting anchor state

For substitutions/stops, `reference` and normalized `mutation` are stored as direct allele states.

### Antiviral

- Required.
- Drug names are normalized to lowercase in the database.
- ResPro can try to fetch information to those drugs

### Phenotype normalization

`phenotype` and `clinical_phenotype` are normalized independently to:

- `resistant`
- `intermediate`
- `sensitive`
- `unknown`

Accepted flexible inputs are intentionally limited.

| Input                                                                        | Normalized to    |
| ---------------------------------------------------------------------------- | ---------------- |
| `resistant`, `resistance`, `res`, `r`, `true`, `1`               | `resistant`    |
| `intermediate`, `interm`, `i`                                          | `intermediate` |
| `sensitive`, `susceptible`, `sensi`, `sens`, `s`, `false`, `0` | `sensitive`    |
| `contradictory`, `contra`, `conflict`, `conflicting`              | `contradictory` |
| empty value,`None`, `unknown`, `na`, `n/a`, `nd`                   | `unknown`      |

Rules:

- You may provide only `phenotype`.
- You may provide only `clinical_phenotype`.
- You may provide both.
- Both fields are normalized independently and stored separately.
- Empty values normalize to `unknown` in each field.

### IC50 parsing rules

- `ic50` and `ic_50` are aliases (use at most one in a file)
- `fold_ic50` and `fold_ic_50` are aliases (use at most one in a file)
- absolute and fold columns may coexist in the same TSV
- values are parsed numerically and stored in dedicated DB fields

### Score

- Optional numeric field accepted in the `score` column.
- Accepts any finite numeric value (integer or decimal, including negative values).
- Useful for evidence scores, confidence scores, or any database-specific numeric quality metric.
- Shown in the HTML report as a dedicated column when at least one rule carries a non-empty value.
- The value is stored and propagated as-is; ResPro does not interpret or threshold it.

### Publications and sources

`publication` accepts:

- DOI forms (`doi:10...`, `https://doi.org/...`, `doi.org/...`)
- PMID forms (`PMID:12345678`)
- Multiple possible must be comma seperated
- free text

Import deduplicates publication entries and links them to atomic rules and formula rules.

### Source

- Optional free text.
- Helpful for indicating merged databases

### Comment

- Optional free text.
- Any information the curator considers relevant; stored verbatim.
- Displayed in the HTML report when at least one rule carries a non-empty value.

### Minimal single-rule example

| gene | reference_identifier | position | reference | mutation | antiviral | phenotype         |
| ---- | -------------------- | -------: | --------- | -------- | --------- | ----------------- |
| UL23 | NC_001806            |      336 | A         | V        | Aciclovir | resistant      |

### Minimal single-rule example for INDELs and frameshifts

Frameshift rules are normalized to anchor the reference amino acid:

| gene | reference_identifier | position | reference | mutation | antiviral | phenotype    |
| ---- | -------------------- | -------: | --------- | -------- | --------- | ------------ |
| UL30 | NC_001806            |      715 | K         | fs       | Aciclovir | resistant    |
| UL23 | NC_001806            |       50 | F         | FGG      | Aciclovir | intermediate |
| UL23 | NC_001806            |       73 | MGH       | M        | Aciclovir | resistant    |

Notes:

- Frameshift (`fs`): Normalized to anchor-form internally; trailing sequence context ignored.
- Insertion (`FGG`): Insertion of GG after the F anchor at position 50.
- Deletion (`M`): Deletion of the MGH block at position 73, resulting in M anchor.

---

## Formula rules TSV (`--formula-rules`)

The optional formula TSV defines higher-order resistance rules over atomic `member_id` values from the primary rules TSV.

### Required columns

| Column         | Meaning                      | Constraints                                                               |
| -------------- | ---------------------------- | ------------------------------------------------------------------------- |
| `group_id`   | Combination group identifier | Must match a `group_id` from the primary rules TSV                      |
| `antiviral`  | Drug name                    | Stored normalized like atomic rules                                       |
| `expression` | Boolean rule formula         | Uses atomic `member_id` values and `AND` / `OR` / `NOT` / `XOR` |

### Optional columns

| Column                         | Meaning                                |
| ------------------------------ | -------------------------------------- |
| `label`                      | Human-readable display label           |
| `phenotype`                  | Formula-level phenotype interpretation |
| `clinical_phenotype`         | Formula-level clinical interpretation  |
| `ic50` / `ic_50`           | Absolute IC50 value                    |
| `fold_ic50` / `fold_ic_50` | Fold IC50 value                        |
| `score`                      | Numeric quality/evidence score         |
| `publication`                | DOI, PMID, or source publication text  |
| `source`                     | Provenance label                       |
| `comment`                    | Free-text curator note                 |

### Expression rules

- Supported operators are `AND`, `OR`, `NOT`, and `XOR`.
- Parentheses are supported and should be used whenever precedence should be explicit.
- Atomic identifiers in `expression` must match `member_id` values from the primary rules TSV.
- Unsupported characters, duplicate group ids, duplicate normalized formulas for the same drug, and unknown atomic ids are rejected during import.
- Each `group_id` present in grouped atomic rows must have exactly one formula row.
- Duplicate atomic ids inside one formula are rejected.

### Minimal combination-rule example

#### Atomic rules (rules.tsv)

Define the individual mutations:

| gene | reference_identifier | position | reference | mutation | antiviral | phenotype | ic50 | fold_ic50 | group_id | member_id |
| ---- | -------------------- | -------: | --------- | -------- | --------- | --------- | ---- | --------- | -------- | --------- |
| UL23 | NC_001806            |      336 | A         | V        | Aciclovir | resistant | 32.5 | 4.1       | group_1  | mut_A     |
| UL30 | NC_001806            |      715 | K         | I        | Aciclovir | resistant | 28.0 | 3.5       | group_1  | mut_B     |
| UL30 | NC_001806            |      725 | A         | AGG      |           |           |      |           | group_1  | mut_C     |

#### Combination rule (formula.tsv)

Define a formula that requires both mutations:

| group_id | antiviral | expression                     | phenotype | clinical_phenotype |
| -------- | --------- | ------------------------------ | --------- | ------------------ |
| group_1  | Aciclovir | (mut_A OR mut_B) AND NOT mut_C | resistant | resistant          |

Importantly, positions can have multiple comma-seperated group_id classifiers. This means that one position can be its own single rule (if metadata is provided) but also part of multiple groups. If you have one position that has different effects for multiple drugs, it is enough to provide the group_id and member_id once. Metadata for the combination rules is strictly taken from the combination rule associated metadata.

### Annotation handling

When optional metadata columns are provided in the atomic rules TSV:

- **If provided** (`ic50`, `fold_ic50`, `phenotype`, `clinical_phenotype`, `source`, `publication`, `comment`):

  - Values are stored in the atomic rule row.
  - If the same mutation appears in both singular and formula contexts, the stored annotation applies to all uses.
  - Formula-level annotations (provided in the formula.tsv) override atomic annotations for that specific formula combination.
- **If omitted** (e.g., all rows lack `ic50`):

  - The field is stored as `NULL` or empty for that rule.
  - During profiling, the absence is treated as "no data available" and reported as `unknown` or blank in output.
  - Formulas can still reference the atomic rule; the combination's interpretation is determined by formula-level metadata.

---

## Common validation failures

1. `gene` not found in imported GenBank annotations
2. `reference_identifier` not present in project references
3. inconsistent coordinate system inside one file
4. `reference` amino acid mismatch at the given position
5. malformed or unsupported mutation token
6. ambiguous helper notation that cannot be resolved in sequence context

For reproducible curation workflows:

- keep `reference_identifier` explicit in every row
- keep mutation notation consistent across sources
- use `group_id` + `member_id` for combinatorial association and keep formula expressions explicit
- when possible, prefer canonical insertion/deletion forms to reduce ambiguity
