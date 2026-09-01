---
title: Output Interpretation
description: Understanding HTML, JSON, and TSV outputs
---

# Output Interpretation Guide (HTML, JSON, TSV)

ResistanceProfiler produces one required output and optional structured exports.

!!! tip "Start with the HTML report"
    Use the HTML report first for interpretation context, then use JSON/TSV for automation and downstream processing.

## HTML report (`*.report.html`)

What to inspect first:

- sample and project identity
- total hits and matched rules
- per-feature mutation details and consequences
- phenotype and clinical phenotype context
- optional manual classifications

Best use:

- review by analysts and clinicians
- sharing a portable report artifact

### Detailed examples of important report tabs

<figure markdown>
![Summary tab](../assets/1_summary.png){: loading=lazy}
<figcaption>Primary summary that condenses findings.</figcaption>
</figure>

<figure markdown>
![Database tab](../assets/2_database_hits.png){: loading=lazy}
<figcaption>Detailed overview over all mutations that have rule hits in the selected database.</figcaption>
</figure>

<figure markdown>
![Mutation tab](../assets/3_all_mutations.png){: loading=lazy}
<figcaption>Detailed overview over all mutations independent on whether they have database hits.</figcaption>
</figure>



## JSON export (`*.results.json`)

Structured machine-readable export for automation and reproducibility.

Top-level sections include:

- `run`
- `variant_result`
- `coverage_gap`
- `formula_rule_hit`
- `sample_classification`

Best use:

- downstream pipelines
- archival and deterministic regeneration
- data integration with external systems

!!! important "Reproducible artifacts"
    JSON exports are intended as reproducible artifacts and can be used directly with `respro regenerate --json`.

Regenerate a report from JSON:

```bash
respro regenerate \
  --project myrespro.db \
  --json my_output/sample_variants.results.json \
  --output my_output
```

See [CLI Reference](cli-reference.md) for all regeneration options.

## TSV export (`*.results.tsv`)

A denormalized, tab-separated table of every annotated variant and its matched
resistance rules. One row is emitted per **(annotated variant × matched rule)**;
variants with no matching rule still appear with empty rule columns. This mirrors
the HTML *Database Hits* table but is flat and machine-readable.

Enable it with `--export tsv` (repeatable, combinable with `json`/`pdf`):

```bash
respro vcf \
  --project myrespro.db \
  --vcf sample.vcf \
  --ref-fasta reference.fa \
  --output my_output \
  --export tsv
```

### Columns

| Column | Description |
|---|---|
| `reference` | Matched internal reference name (VCF mode only; omitted in FASTA mode). Populated for multi-species VCF runs; empty for single-reference runs where the chrom is not the reference accession. |
| `gene` | Feature / gene name (display name applied when configured). |
| `nt_mut` | Nucleotide change on the internal reference, `ref{pos}alt` (1-based). Combined codon events use `ref_codon{codon_pos}alt_codon`. |
| `nt_mut_user` | Nucleotide change on the user-supplied reference (VCF coords before remap). VCF mode only; omitted in FASTA mode. |
| `aa_effect` | Amino-acid change `ref_aa{codon_pos}alt_aa` (1-based). `INS_any (...)` prefix for wildcard insertion rules. |
| `strand` | Coding strand of the feature (`+`/`-`), sourced from the feature record. |
| `af` | Allele frequency (raw float). |
| `af_bin` | AF bin label (e.g. `low`/`moderate`/`high`). |
| `depth` | Read depth at the variant (VCF mode only; omitted in FASTA mode). Empty for combined formula rows. |
| `consequence` | Consequence label (`missense`, `frameshift`, …). |
| `in_database` | `yes` when at least one rule matched (single or formula member); otherwise `no`. |
| `rule_type` | `single`, `formula`, `formula-member`, or `n/a` for non-hits. |
| `drug` | Drug name for the matched rule. `n/a` for non-hits. |
| `phenotype` | Rule phenotype (`resistant`/`intermediate`/`sensitive`/…). `n/a` for non-hits. |
| `clinical_phenotype` | Clinical phenotype. `n/a` for non-hits. |
| `ic50` | Rule IC50 value (string, may carry qualifiers). Empty for non-hits. |
| `fold_ic50` | Rule fold-IC50 value. Empty for non-hits. |
| `score` | Rule score. Empty for non-hits. |
| `source` | Rule source. `n/a` for non-hits. |
| `publications` | `|`-joined publication identifiers (DOI, or PubMed ID as fallback). Empty for non-hits. |

### Row semantics

- **Single rules**: one row per matching rule. A variant conferring resistance to
  two drugs produces **two rows**, each carrying that rule's own
  phenotype/IC50/fold-IC50/score.
- **Formula (combinatorial) rules**: one **combined row** per fired formula rule.
  Member mutations are joined with `;` in `gene`, `nt_mut`, `nt_mut_user`,
  `aa_effect`, `af`, `af_bin`, and `strand`. The phenotype/IC50/fold-IC50/score
  come from the formula rule set (the combined call), not the individual members.
- **Formula-member-only variants**: a variant that is only a formula member (no
  single rule of its own) gets a `rule_type=formula-member` row with
  `in_database=yes` and empty rule metric columns.
- **Effect-as-resistant**: metadata-only synthetic hits (e.g. frameshifts
  classified as resistant by algorithm config) appear as `rule_type=single` rows
  with `source=Metadata algorithm`.

!!! note "Web download"
    In the webapp, the TSV is produced for every profile/regenerate run alongside
    the HTML, PDF, and JSON artifacts. Download it from the *Analyze* tab
    (single-report action bar), the *Reports* tab (per-row TSV link), or the
    batch/session "Download all" zip bundles. Run the CLI with `--export tsv` for
    command-line use.
