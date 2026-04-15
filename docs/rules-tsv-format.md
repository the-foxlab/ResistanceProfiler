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
| `rule_group`                         | Combination-rule label | Rows with the same non-empty value become one combination rule set. Comma-separated values assign a row to multiple groups. |
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

This column stores the alternate amino-acid state. Input is normalized during import.

Canonical internal tokens are:

- single AA (`A`..`Y` from the 20 standard residues)
- `*` (stop)
- `KfsX` (anchored frameshift; anchor AA + `fsX`)
- insertion token like `F50FGG`
- deletion token like `FGG50F`

Allowed AA letters for specific residues are:

`A C D E F G H I K L M N P Q R S T V W Y`

Everything else (`X`, `B`, `J`, `Z`, `any`, etc.) is unsupported.

#### 1) Substitution (missense/synonymous/stop-loss target)

| Status | Forms |
|---|---|
| Allowed | single AA token (`L`, `E`, `M`), full rewrite form like `F67L` (stored as `L`) |
| Not allowed | `X`, `x`, `any`, tokens containing non-standard AA letters |

Notes:

- Full rewrite substitution (`F67L`) is accepted and reduced to the right-side token (`L`).
- `mutation == reference` is rejected as no-op during rule validation.

#### 2) Stop mutation

| Status | Forms |
|---|---|
| Allowed | `*`, `STOP`, `F67*`, `F67stop` |
| Not allowed | wildcard-like tokens (`any`, `X`) |

Stored as canonical `*`.

#### 3) Frameshift

| Status | Forms |
|---|---|
| Allowed | `fs`, `fsX`, `frameshift`, `F67fs`, `F67frameshift`, `F67fsATFF*`, `F67Ffs`, `FfsX` |
| Not allowed | malformed tokens that do not start with frameshift notation |

Stored as canonical anchored form `REFERENCE_AA + fsX` (e.g. `KfsX`). Any downstream sequence after `fs` is discarded.

#### 4) Insertion

| Status | Forms |
|---|---|
| Allowed | canonical `F50FGG`, HGVS-like `F50_F51insGG`, context form `insGG` (requires row `reference` and `position`) |
| Not allowed | `insGG` without `reference` or without `position`, malformed `ins` tokens |

Stored in insertion canonical style: `ANCHOR + POSITION + RESULT` (e.g. `F50FGG`).

#### 5) Deletion with explicit deleted block

| Status | Forms |
|---|---|
| Allowed | canonical `FGG50F`, HGVS-like `F50delGG` |
| Not allowed | prefix-deletion forms `delF67`, `del67`, bare `del` |

Stored in deletion canonical style: `DELETED_BLOCK + POSITION + ANCHOR` (e.g. `FGG50F`).

#### 6) Anchor-less deletion helper notation

| Status | Forms |
|---|---|
| Allowed | `{deleted_block}{position}del`, e.g. `Q35del`, `FG4del` |
| Not allowed | deletion starting at first residue (no upstream anchor), deleted block that does not match gene sequence |

Behavior:

- `position` in TSV is deletion start.
- Import resolves the upstream anchor from the GenBank AA sequence.
- Storage is converted to explicit deletion alleles (`reference` becomes anchor+deleted block, `mutation` becomes anchor).

#### Matching and validation behavior

- Substitution and stop rules use explicit allele matching.
- Frameshift rules are matched by frameshift state only (`*fsX`) and intentionally
  ignore anchor amino-acid identity.
- In-frame insertion and deletion rules are matched by codon position plus inserted/deleted
  payload, independent of anchor amino acid identity.
- When an in-frame indel payload matches but the anchor AA differs between rule and observation,
  `respro` emits a warning for debugging (for example to spot coordinate or anchoring issues).
- `*` is a specific stop event, not wildcard.
- No-op entries are rejected (`mutation` equals `reference`).
- Unsupported mutation tokens are skipped with warning during import.

### TSV to database storage mapping (core rule columns)

The table below shows how `reference_identifier` (spelling in TSV), `position`, `reference`, and
`mutation` are interpreted and stored in `resistance_rule` / `resistance_rule_set_member`.

| Mutation type | TSV `reference_identifier` | TSV `position` | TSV `reference` | TSV `mutation` | Stored DB `position` | Stored DB `reference` | Stored DB `mutation` |
|---|---:|---:|---|---|---:|---|---|
| Substitution (canonical) | `NC_001806` | `67` | `F` | `L` | `66` | `F` | `L` |
| Substitution (rewrite form) | `NC_001806` | `67` | `F` | `F67L` | `66` | `F` | `L` |
| Stop | `NC_001806` | `67` | `F` | `F67stop` | `66` | `F` | `*` |
| Frameshift | `NC_001806` | `67` | `F` | `F67fsATFF*` | `66` | `F` | `FfsX` |
| Insertion (canonical) | `NC_001806` | `50` | `F` | `F50FGG` | `49` | `F` | `FGG` |
| Insertion (HGVS-like) | `NC_001806` | `50` | `F` | `F50_F51insGG` | `49` | `F` | `FGG` |
| Deletion (canonical) | `NC_001806` | `50` | `FGG` | `FGG50F` | `49` | `FGG` | `F` |
| Deletion (HGVS-like) | `NC_001806` | `50` | `F` | `F50delGG` | `49` | `FGG` | `F` |
| Anchor-less deletion | `NC_001806` | `35` | `Q` | `Q35del` | `33` | `KQ` | `K` |

Storage notes:

- Stored DB `position` is 0-based.
- For single-AA substitutions and stop rules, DB keeps `reference` from TSV and stores only the alt token in `mutation`.
- For insertions/deletions, DB stores explicit resulting alleles: `reference` and `mutation` are expanded/split for exact matching.

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

- Empty or missing `rule_group` → single rule.
- Same non-empty `rule_group` across multiple rows → one combination rule set.
- A comma-separated list of labels assigns the row to **each** named group independently
  (e.g. `groupA, groupB` makes the row a member of both `groupA` and `groupB`).
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

A row may appear in multiple groups by providing a comma-separated label list.
In this example the shared `UL23 N92L` mutation belongs to two groups:

```tsv
gene	reference_identifier	position	reference	mutation	antiviral	phenotype	rule_group
UL23	NC_001806	92	N	L	Aciclovir	resistant	groupA, groupB
UL30	NC_001806	715	K	I	Aciclovir	resistant	groupA
UL30	NC_001806	300	D	E	Aciclovir	resistant	groupB
```

## Practical recommendation

If you curate rules manually, keep each row explicit:

- always include `reference_identifier`;
- always include `reference`;
- prefer canonical mutation forms when possible;
- use `phenotype` with canonical values (`resistant`, `intermediate`,
  `sensitive`, `unknown`) unless you need `clinical_phenotype` as a separate
  explicit source column.

