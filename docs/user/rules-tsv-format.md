# Rules TSV Format Reference

This document is the source of truth for curated rules TSV files used by `respro init`
and `respro add`.

## Overview

- One TSV row defines one rule member.
- Rows without `rule_group` are single-mutation rules.
- Rows with the same `rule_group` are evaluated as one combination rule.

## Required columns

| Column | Meaning | Constraints |
| --- | --- | --- |
| `gene` | CDS/gene name | Must match a gene loaded from GenBank |
| `reference_identifier` | Reference accession or name | Must match a reference in the project DB |
| `position` | Amino-acid position | File-wide 0-based or 1-based, auto-detected |
| `reference` | Reference amino acid at position | Checked against reference AA sequence |
| `mutation` | Alternate amino-acid event token | Normalized to canonical mutation forms |
| `antiviral` | Drug name | Stored normalized (lowercase) |

## Optional columns

| Column | Meaning |
| --- | --- |
| `phenotype` | Rule-level phenotype interpretation |
| `clinical_phenotype` | Clinical interpretation field |
| `ic50` / `ic_50` | Absolute IC50 value |
| `fold_ic50` / `fold_ic_50` | Fold IC50 value |
| `publication` | DOI, PMID, or source publication text |
| `source` | Provenance label |
| `comment` | Free-text curator note |
| `rule_group` | Combination group key |

## Coordinate rules (`position`)

- Positions are interpreted as amino-acid coordinates.
- Import auto-detects 0-based vs 1-based using the `reference` AA and GenBank translation.
- Use one coordinate system consistently across the entire TSV file.

## Mutation token rules (`mutation`)

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

## How mutation normalization works

Normalization means that different textual inputs describing the same biological event
are converted into one canonical representation before rules are stored and matched.

High-level processing order:

1. Read row context (`gene`, `reference_identifier`, `position`, `reference`).
2. Detect mutation category (substitution, stop, frameshift, insertion, deletion).
3. Normalize token spelling to canonical internal form.
4. Validate against reference protein context.
5. Store explicit allele state for deterministic matching.

### Why this matters

- Curation files from different sources often use different notation styles.
- Normalization avoids false mismatches caused by formatting differences.
- Reported hits remain stable across imports and regenerations.

## Normalization examples (input -> canonical interpretation)

### Substitutions and stop events

| Input mutation | Example row context | Canonical interpretation |
| --- | --- | --- |
| `V` | `reference=A`, `position=336` | substitution to `V` |
| `A336V` | `reference=A`, `position=336` | substitution to `V` |
| `STOP` | `reference=A`, `position=336` | stop mutation (`*`) |
| `A336*` | `reference=A`, `position=336` | stop mutation (`*`) |

### Frameshift events

Frameshift rules are normalized to the anchored form `XfsX`, where `X` is the
reference amino acid anchor from the row context.

| Input mutation | Example row context | Canonical interpretation |
| --- | --- | --- |
| `fs` | `reference=K`, `position=715` | `KfsX` |
| `frameshift` | `reference=K`, `position=715` | `KfsX` |
| `K715fs` | `reference=K`, `position=715` | `KfsX` |
| `K715fsATFF*` | `reference=K`, `position=715` | `KfsX` |

Important note:

- Any trailing sequence-like suffix after `fs` is not treated as a separate allele.
  It is collapsed to the canonical frameshift sentinel form.

### In-frame insertion events

Insertion rules are normalized to a canonical insertion allele that preserves:

- anchor amino acid,
- anchor position,
- resulting inserted payload context.

| Input mutation | Example row context | Canonical interpretation |
| --- | --- | --- |
| `F50FGG` | `reference=F`, `position=50` | insertion, canonical form retained |
| `F50_F51insGG` | `reference=F`, `position=50` | insertion equivalent to canonical `F50FGG` |
| `insGG` | `reference=F`, `position=50` | insertion resolved from row context (equivalent to `F50FGG`) |

### In-frame deletion events

Deletion rules are normalized to explicit deleted-block representation so matching is exact.

| Input mutation | Example row context | Canonical interpretation |
| --- | --- | --- |
| `FGG50F` | `reference=FGG`, `position=50` | deletion, canonical form retained |
| `F50delGG` | `reference=F`, `position=50` | deletion resolved to deleted block + resulting anchor |
| `Q35del` | sequence context required | anchor-less helper form, resolved from reference sequence |

Anchor-less deletion note:

- Helper forms like `Q35del` depend on upstream sequence context.
- If the required anchor context is biologically inconsistent with the reference,
  the row is rejected or skipped according to validation rules.

## Complex and edge-case events

### Complex in-frame events

Some observed sample variants can be complex (for example mixed mid-codon
insertions/deletions). In reporting, these may appear as complex consequence classes.

For curated rules TSV:

- encode biologically specific events as explicit insertion or deletion alleles,
- avoid wildcard-like shorthand for complex events,
- prefer decomposed, clearly interpretable rule entries.

### Unsupported ambiguity patterns

Examples that are not accepted as mutation rules:

- wildcard-like tokens (`X`, `any`),
- malformed HGVS-like fragments that cannot be resolved,
- no-op entries where `mutation` equals `reference`.

## TSV-to-database interpretation

During import, in-frame indels are converted to explicit allele representations so matching is exact:

- insertion rules track anchor and inserted payload
- deletion rules track deleted block and resulting anchor state

For substitutions/stops, `reference` and normalized `mutation` are stored as direct allele states.

## Phenotype normalization

`phenotype` and `clinical_phenotype` are normalized independently to:

- `resistant`
- `intermediate`
- `sensitive`
- `unknown`

## IC50 parsing rules

- `ic50` and `ic_50` are aliases (use at most one in a file)
- `fold_ic50` and `fold_ic_50` are aliases (use at most one in a file)
- absolute and fold columns may coexist in the same TSV
- values are parsed numerically and stored in dedicated DB fields

## Publications and sources

`publication` accepts:

- DOI forms (`doi:10...`, `https://doi.org/...`, `doi.org/...`)
- PMID forms (`PMID:12345678`)
- free text

Import deduplicates publication entries and links them to rules/rule groups.

## Combination rules (`rule_group`)

- same non-empty `rule_group` value means members belong to one combination rule
- a combination rule fires only if all members are present in the sample
- group members should be internally consistent for drug/interpretation intent

## Minimal single-rule example

| gene | reference_identifier | position | reference | mutation | antiviral | phenotype |
| --- | --- | ---: | --- | --- | --- | --- |
| UL23 | NC_001806 | 336 | A | V | Aciclovir | resistant |

## Minimal combination-rule example

| gene | reference_identifier | position | reference | mutation | antiviral | phenotype | rule_group |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| UL23 | NC_001806 | 336 | A | V | Aciclovir | resistant | UL23_AV_plus_UL30_KI |
| UL30 | NC_001806 | 715 | K | I | Aciclovir | resistant | UL23_AV_plus_UL30_KI |

## Common validation failures

1. `gene` not found in imported GenBank annotations
2. `reference_identifier` not present in project references
3. inconsistent coordinate system inside one file
4. `reference` amino acid mismatch at the given position
5. malformed or unsupported mutation token
6. ambiguous helper notation that cannot be resolved in sequence context

## Practical recommendation

For reproducible curation workflows:

- keep `reference_identifier` explicit in every row
- keep mutation notation consistent across sources
- treat `rule_group` as a biological co-occurrence assertion, not a label only
- when possible, prefer canonical insertion/deletion forms to reduce ambiguity
