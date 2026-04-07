# Rules TSV format reference

This document is the source of truth for how a `respro` rules TSV must be
formatted.

It defines:

- required and optional columns;
- allowed values per column;
- how mutations are written;
- how phenotype and IC50 inputs are normalized during `respro init` and
  `respro init-add`.

## One row = one rule member

Each TSV row describes one amino-acid rule entry.

- A row without `rule_group` is a single resistance rule.
- A row with `rule_group` is one member of a combination rule set.
- All rows in the file must use tab separation.

## Required columns

The following columns are required for every rules TSV:

| Column | Meaning | Notes |
|---|---|---|
| `gene` | Gene / CDS identifier | Must match the GenBank-derived gene name in the project. |
| `reference_identifier` | Reference record identifier | Must match the project reference accession or reference name. |
| `position` | Amino-acid position | `respro` detects whether the file is 0-based or 1-based, but all rows in one file must be consistent. |
| `reference` | Reference amino acid | Required for biological validation against the stored GenBank translation. |
| `mutation` | Mutation token | Normalized by `respro.core.annotation.normalize_mutation`. See mutation section below. |
| `antiviral` | Drug name | Stored in lowercase in the database. |

## Optional columns

| Column                               | Meaning | Allowed behavior                                                                |
|--------------------------------------|---|---------------------------------------------------------------------------------|
| `phenotype`                          | Phenotypic interpretation | Optional. Normalized to `resistant`, `intermediate`, `sensitive`, or `unknown`. |
| `clinical_phenotype`                 | Alternative clinical phenotype column | Optional. Normalized independently from `phenotype`.                            |
| `ic50`                               | Absolute IC50 numeric value | Optional. Parsed as a float. Mutually exclusive with `ic_50`. Can coexist with `fold_ic50`. |
| `ic_50`                              | Alias for `ic50` | Optional. Mutually exclusive with `ic50`.                                       |
| `fold_ic50`                          | Fold-change IC50 value | Optional. Parsed as a float. Mutually exclusive with `fold_ic_50`. Can coexist with `ic50`. |
| `fold_ic_50`                         | Alias for `fold_ic50` | Optional. Mutually exclusive with `fold_ic50`.                                  |
| `publication`                        | Literature reference | Free text, typically DOI or PubMed identifier.                                  |
| `source`                             | Provenance label | Free text, e.g. source database name.                                           |
| `rule_group`                         | Combination-rule label | Rows with the same non-empty value become one combination rule set.             |
| `comment`                            | Free-text curator note | Optional. Any information the curator considers relevant; stored verbatim.      |

## Column-by-column rules

### `gene`

- Must exactly match a CDS / gene name loaded from GenBank.
- If the gene is absent from the project annotations, the row is skipped.

### `reference_identifier`

- Required for every row.
- Must match either:
  - the reference accession; or
  - the stored reference name.
- This field is required even when a gene name is unique in the project.

### `position`

- Interpreted as amino-acid position.
- The importer auto-detects whether the file is 0-based or 1-based by comparing
  `reference` against the stored translated sequence.
- Use one coordinate system consistently within a file.

### `reference`

- Required for every row.
- Must be the reference amino acid at the given position.
- The importer validates it against the GenBank-derived amino-acid sequence.
- A true mismatch is fatal.
- Positions beyond the end of the annotated protein are warned and skipped.

### `mutation`

This column stores the alternate state only. During import, `respro` normalizes
accepted forms to canonical internal tokens.

#### Preferred canonical writing (recommended for curation)

Use these forms when you curate or export rules manually:

1. **Substitution / stop**: single token (`L`, `*`, `any`)
2. **Frameshift**: `fsX`
3. **Insertion**: `REF + POSITION + ALT`, e.g. `F50FGG`
4. **Deletion with explicit block**: `DELETED_BLOCK + POSITION + ANCHOR`, e.g. `FGG50F`

The parser accepts additional input forms, but the canonical forms above are the
most robust and easiest to review.

#### Canonical internal mutation tokens

| Canonical token | Meaning | Example display |
|---|---|---|
| `A`-`Z` | Specific alternate amino acid | `F67L` |
| `*` | Stop gained | `F67*` |
| `fsX` | Frameshift | `F67fsX` |
| `F50FGG` | Insertion | `F50FGG` |
| `FGG50F` | Deletion with explicit deleted block | `FGG50F` |
| `any` | Wildcard for any non-reference amino acid | `F67any` |

Allowed amino-acid letters are the 20 standard residues:

`A C D E F G H I K L M N P Q R S T V W Y`

Any other letter in mutation tokens is treated as unsupported.

#### Accepted substitution and stop inputs

| Input examples | Stored as |
|---|---|
| `F67L`, `f67l`, `*67L`, `F67F` | `L`, `L`, `L`, `F` |
| `F67*`, `F67stop`, `F67STOP` | `*` |

#### Accepted frameshift inputs

| Input examples | Stored as |
|---|---|
| `F67fs`, `F67frameshift`, `F67fsX`, `F67fsATFF*` | `fsX` |
| `fs`, `frameshift` | `fsX` |

Practical examples:

| Curator input | Stored token |
|---|---|
| `UL30 K539fs` | `fsX` |
| `UL30 K539frameshift` | `fsX` |
| `UL30 K539fsATGG*` | `fsX` |

Only the fact that a frameshift occurred is retained. Any downstream frameshift
sequence is discarded.

#### Accepted insertion and deletion inputs

| Input examples | Stored as |
|---|---|
| `F50FGG` | `F50FGG` |
| `F50_F51insGG` | `F50FGG` |
| `insGG` with `reference=F` and `position=50` | `F50FGG` |
| `FGG50F` | `FGG50F` |
| `F50delGG` | `FGG50F` |

Practical examples:

| Mutation type | Curator input | Stored token | Preferred canonical token |
|---|---|---|---|
| insertion | `F50FGG` | `F50FGG` | `F50FGG` |
| insertion | `F50_F51insGG` | `F50FGG` | `F50FGG` |
| insertion (context form) | `insGG` + `reference=F`, `position=50` | `F50FGG` | `F50FGG` |
| deletion (explicit) | `FGG50F` | `FGG50F` | `FGG50F` |
| deletion (explicit) | `F50delGG` | `FGG50F` | `FGG50F` |

Rejected deletion inputs (not allowed):

- `F67del`
- `delF67`
- `del67`
- `del`

Notes:

- Preferred insertion form: `REF + POSITION + ALT`, e.g. `F50FGG`.
- Preferred deletion form: `DELETED_BLOCK + POSITION + ANCHOR`, e.g. `FGG50F`.
- For best reproducibility, write insertions/deletions directly in canonical
  form instead of relying on parser conversion.

#### Accepted wildcard inputs

| Input examples | Stored as |
|---|---|
| `any`, `x`, `X` | `any` |

#### Matching behavior

- Exact rule matching uses the normalized token.
- Wildcard matching is only for canonical `any`.
- `*` is treated as a specific stop event and is not wildcard.
- No-op entries are rejected: after normalization, `mutation` must not be equal
  to `reference` (example: `reference=E`, `mutation=E`).
- Unsupported amino-acid tokens (outside the standard 20 letters, except `fsX`,
  `*`, and `any`) are skipped and reported as warnings during import.

### `antiviral`

- Required.
- Drug names are normalized to lowercase in the database.
- Duplicate rules are evaluated semantically using reference, position,
  reference amino acid, mutation, and drug.

### `phenotype` and `clinical_phenotype`

Both columns are optional.

Internally, values are normalized to one of:

- `resistant`
- `intermediate`
- `sensitive`
- `unknown`

Accepted flexible inputs are intentionally limited.

| Input | Normalized to |
|---|---|
| `resistant`, `resistance`, `res`, `r`, `true`, `1` | `resistant` |
| `intermediate`, `interm`, `i` | `intermediate` |
| `sensitive`, `susceptible`, `sensi`, `sens`, `s`, `false`, `0` | `sensitive` |
| empty value, `None`, `unknown`, `na`, `n/a`, `nd` | `unknown` |

Rules:

- You may provide only `phenotype`.
- You may provide only `clinical_phenotype`.
- You may provide both.
- Both fields are normalized independently and stored separately.
- Empty values normalize to `unknown` in each field.

### `ic50`, `ic_50`, `fold_ic50`, `fold_ic_50`

- `ic50` / `ic_50` store an absolute IC50 value; only one of the two aliases is allowed per file.
- `fold_ic50` / `fold_ic_50` store a fold-change IC50 value; only one of the two aliases is allowed per file.
- Both `ic50` (or `ic_50`) and `fold_ic50` (or `fold_ic_50`) may be present simultaneously in the same file — they are stored in separate database columns and displayed in separate report columns.
- Values are parsed as floats.
- Empty values and `None` are allowed and stored as empty.
- Text around a number is tolerated when a numeric core can be extracted.

Examples:

| Input | Stored as |
|---|---|
| `8` | `8` |
| `8.5` | `8.5` |
| `>10x` | `10` |
| `8.5 fold` | `8.5` |
| empty | `` |
| `None` | `` |

If a non-empty value contains no parseable numeric component, import fails.

### `publication`

- Optional.
- Comma-separated list of publication references.
- Each entry is one of:
  - `PMID:<digits>` — PubMed identifier (e.g. `PMID:12345678`)
  - `doi:<10.xxx>` or `doi.org/10.xxx` or `https://doi.org/10.xxx` — DOI
  - Free text (stored as raw_input, no link generated)
- Entries are stored in a deduplicated `publication` table and linked to each rule via a join table.
- When `--additional-info` is enabled (default), `PMID:` entries are resolved to DOIs via NCBI
  E-utilities and titles are fetched from CrossRef.
- For combination rule groups, publications from **all** member rows are collected (union).

### `source`

- Optional free text.
- First non-empty value wins within a combination rule group.

### `comment`

- Optional free text.
- Any information the curator considers relevant; stored verbatim.
- First non-empty value wins within a combination rule group.
- Displayed in the HTML report when at least one rule carries a non-empty value.

### `rule_group`

This column enables combination rules.

- Empty or missing `rule_group` -> single rule.
- Same non-empty `rule_group` across multiple rows -> one combination rule set.
- At least two valid members are required per group.
- All rows in a group must agree on:
  - `antiviral`
  - normalized phenotype
- For IC50, the highest numeric value across the group is stored.
- `rule_group` is stored as `group_name` in the database.
- The combination fires only when all member mutations are present.

## Minimal valid single-rule example

```tsv
gene	reference_identifier	position	reference	mutation	antiviral	phenotype	ic50	fold_ic50	publication	source
UL23	NC_001806	67	T	V	Aciclovir	resistant	8.0	4.0	doi.org/10.1234/example	herpesdrg-db
```

## Minimal valid combination-rule example

```tsv
gene	reference_identifier	position	reference	mutation	antiviral	phenotype	ic50	publication	source	rule_group
UL23	NC_001806	92	N	L	Aciclovir	resistant	8.0	doi.org/10.1234/example	herpesdrg-db	UL23_N92L+UL30_K715I
UL30	NC_001806	715	K	I	Aciclovir	resistant	8.0	doi.org/10.1234/example	herpesdrg-db	UL23_N92L+UL30_K715I
```

## Practical recommendation

If you curate rules manually, keep each row explicit:

- always include `reference_identifier`;
- always include `reference`;
- prefer canonical mutation forms when possible;
- use `phenotype` with canonical values (`resistant`, `intermediate`,
  `sensitive`, `unknown`) unless you need `clinical_phenotype` as a separate
  explicit source column.

