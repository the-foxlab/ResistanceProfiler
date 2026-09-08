---
title: CLI Reference
description: Complete CLI command reference with examples
---

# CLI Reference

This reference covers every `respro` command. The commands are grouped by what they do:

- **Get a database:** [`databases`](#download-a-maintained-database), [`init`](#initialize-a-project-database), [`add`](#extend-or-validate-rules-in-an-existing-project)
- **Profile samples:** [`fasta`](#profile-fasta-input), [`vcf`](#profile-vcf-input)
- **Work with results:** [`regenerate`](#regenerate-reports), [`classify`](#add-manual-interpretation-fields)
- **Inspect and manage:** [`manage database`](#inspect-project-metadata-and-curated-rules), [`manage results`](#inspect-and-delete-stored-runs)

The core idea: ResPro profiles samples against one internal project database. New sample data is first normalised into that database's reference space, then amino-acid rules are matched.

## Download a maintained database

List available pre-built databases:

```bash
respro databases --list
```

Download a database by name:

```bash
respro databases --download db_name --output my_folder/
```

ResPro downloads the TSV rules and GenBank files, then builds a SQLite database from them. This can take a moment because ResPro enriches entries with PubMed and PubChem information by default. Add `--no-additional-info` to skip those network lookups for a faster build.

| Option | Description |
|---|---|
| `--list`, `-l` | List available maintained databases and their metadata. |
| `--download NAME`, `-d` | Database name to download (use a value from `--list`). |
| `--output PATH`, `-o` | Output path (directory or file). Defaults to `<database_name>.db`. |
| `--additional-info` / `--no-additional-info` | Fetch PubChem/PubMed enrichment during the build (default: on). |

!!! tip "Verbose progress"
    Add `-vv` to see verbose progress:
    ```bash
    respro -vv databases --download db_name --output my_folder/
    ```

## Initialize a project database

Use this when you have your own GenBank reference and rules TSV instead of a maintained database:

```bash
respro init \
  --name "Docs Demo" \
  --genbank some_reference.gb \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv \
  --output myrespro.db
```

If your dataset only contains single-mutation rules, omit `--formula-rules`.

| Option | Description |
|---|---|
| `--name TEXT`, `-n` | Project name. **Required.** |
| `--rules PATH`, `-r` | Resistance rules TSV. **Required.** |
| `--genbank PATH`, `-g` | GenBank file(s). Repeat for multiple files. |
| `--formula-rules PATH`, `-f` | Optional formula (combination) rules TSV. |
| `--output PATH`, `-o` | Output SQLite database path. Default: `project.db`. |
| `--metadata PATH`, `-m` | Optional metadata JSON file. See [Database Preparation](database-preparation.md#optional-metadata-json). |
| `--overwrite`, `-w` | Overwrite an existing database at the output path. |
| `--additional-info` / `--no-additional-info` | Query PubChem/PubMed for enrichment (default: on). |
| `--example PATH`, `-ex` | Store a single-record consensus FASTA as the database example. |

Optionally ship a per-database example consensus FASTA that users can profile
with a single command (see [Profile FASTA input](#profile-fasta-input)) and that
the web app exposes as an "Example" button. The example must be a single-record
FASTA:

```bash
respro init \
  --name "Docs Demo" \
  --genbank some_reference.gb \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv \
  --example example_consensus.fasta \
  --output myrespro.db
```

For metadata and interpretation algorithm options, see [Database Preparation](database-preparation.md).

## Extend or validate rules in an existing project

Validate rules without writing changes:

```bash
respro add \
  --project myrespro.db \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv \
  --validate
```

Add rules and commit changes:

```bash
respro add \
  --project myrespro.db \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv
```

| Option | Description |
|---|---|
| `--project PATH`, `-p` | Existing project database. **Required.** |
| `--rules PATH`, `-r` | Resistance rules TSV to add. **Required.** |
| `--formula-rules PATH`, `-f` | Optional formula (combination) rules TSV. |
| `--genbank PATH`, `-g` | Optional additional GenBank file(s). |
| `--additional-info` / `--no-additional-info` | Query PubChem/PubMed for enrichment (default: on). |
| `--validate`, `-v` | Validate rules and exit without writing changes. |
| `--example PATH`, `-ex` | Replace the stored example consensus FASTA with this file. |
| `--no-example` | Clear any stored example consensus FASTA. |

Use `--example example_consensus.fasta` to store or overwrite a per-database
example FASTA, or `--no-example` to clear a previously stored example:

```bash
respro add \
  --project myrespro.db \
  --rules rules.tsv \
  --example example_consensus.fasta
```

## Profile FASTA input

```bash
respro fasta \
  --project myrespro.db \
  --fasta my_consensus_sequence.fasta \
  --output my_output \
  --results-db my_results.db \
  --export json \
  --export pdf
```

| Option | Description |
|---|---|
| `--project PATH`, `-p` | Project database. **Required.** |
| `--fasta PATH`, `-f` | Input consensus FASTA sequence. Mutually exclusive with `--example`. |
| `--example` | Profile the example consensus FASTA stored in the project database. |
| `--sample TEXT`, `-s` | Sample name for the report. Default: `sample`. |
| `--output PATH`, `-o` | Output path (directory or HTML file path). Default: `output`. |
| `--results-db PATH`, `-d` | Optional results database path. Creates or appends to an existing SQLite results database. |
| `--threads N`, `-th` | Thread count for alignment calculations. Default: `1`. |
| `--cache` / `--no-cache` | Cache the FASTA reference mapping in the project database for report regeneration (default: off). |
| `--export FORMAT`, `-e` | Extra export format alongside HTML (`pdf`, `json`, `tsv`). Repeatable. |

To profile the example consensus FASTA stored in the project database (set via
`respro init --example` or `respro add --example`), use `--example` instead of
`--fasta`. The two options are mutually exclusive:

```bash
respro fasta \
  --project myrespro.db \
  --example \
  --output my_output
```

If no example is stored, the command fails with a message.

## Profile VCF input

```bash
respro vcf \
  --project myrespro.db \
  --vcf my_ngs_result.vcf \
  --ref-fasta my_vcf_ref.fasta \
  --output my_output \
  --results-db my_results.db \
  --min-af 0.01 \
  --min-depth 0 \
  --export json \
  --export pdf
```

| Option | Description |
|---|---|
| `--project PATH`, `-p` | Project database. **Required.** |
| `--vcf PATH`, `-f` | Input VCF file. **Required.** |
| `--ref-fasta PATH`, `-r` | Reference FASTA the VCF was called against. **Required.** |
| `--sample TEXT`, `-s` | Sample name for the report. Default: `sample`. |
| `--output PATH`, `-o` | Output path (directory or HTML file path). Default: `output`. |
| `--results-db PATH`, `-d` | Optional results database path. Creates or appends to an existing SQLite results database. |
| `--min-af FLOAT`, `-ma` | Minimum allele frequency filter. Default: `0.01`. |
| `--min-depth INT`, `-md` | Minimum read depth filter (used with `--bam`). Default: `10`. |
| `--bam PATH`, `-b` | Optional BAM aligned against the same query reference as the VCF. Used to mark non-covered codon stretches below `--min-depth`. |
| `--threads N`, `-th` | Thread count for alignment calculations. Default: `1`. |
| `--cache` / `--no-cache` | Reuse/store the FASTA reference mapping cache in the project database (default: off). |
| `--export FORMAT`, `-e` | Extra export format alongside HTML (`pdf`, `json`, `tsv`). Repeatable. |

The VCF may be **multi-chrom** and the reference FASTA **multi-record**: each VCF
`CHROM` is matched to one FASTA record by header name. This supports targeted
sequencing (multiple queries aligning to one internal reference) and segmented
viruses (multiple queries aligning to different internal references) in a single
run.

### VCF reference

- Every `CHROM` observed in the VCF variant records must have a matching record
  header in the reference FASTA. If a VCF `CHROM` has no matching FASTA record,
  profiling fails with a clear error (this usually means the wrong reference file
  was provided).
- Extra FASTA records that are not named by any VCF `CHROM` are ignored — they are
  not aligned, cached, or reported. You may safely supply a multi-record FASTA that
  contains references for more than one species; only the records named by VCF
  `CHROM`s contribute to the report.
- A VCF-matched FASTA record that does not align to any internal feature is skipped
  with a warning; profiling continues as long as at least one other record maps
  successfully.
- Multi-species runs are allowed as long as the genes the query actually matches do
  not overlap across species. If the same gene is matched on two distinct species
  (e.g. HSV-1 `UL23` and HSV-2 `UL23`), profiling fails because resistance-relevant
  mutations cannot be attributed to a single species unambiguously. Same-species
  shared gene names are always allowed.

### Allele-frequency source (VCF mode)

ResistanceProfiler evaluates resistance rules against per-allele allele frequencies
(AF). For each ALT allele of a record it resolves the AF from the first available
source, in this fixed precedence:

1. `INFO/AF` (allele frequency for the ALT, indexed by ALT position)
2. `INFO/VAF`
3. `INFO/FREQ`
4. `FORMAT/AF` of the **first sample** (single-sample contract — see below)
5. `FORMAT/AD`-derived AF of the first sample: `AD[alt_idx + 1] / sum(AD)`

This precedence is intentional for **single-sample pathogen VCFs**, where `INFO/AF`
typically already reports the per-allele frequency for the single sequenced sample.
For multi-sample VCFs, `INFO/AF` may instead describe site- or population-level
frequencies; in that case prefer a single-sample VCF or supply `FORMAT/AF`/`FORMAT/AD`
explicitly, because ResistanceProfiler reads only the first sample for FORMAT-level
values.

#### Missing and malformed allele-specific arrays

Allele-specific fields (`AF`, `VAF`, `FREQ`, `AD`) are read positionally against the
ALT list. Missing entries (VCF `.`) and short arrays are never silently clamped to
the last available value. Per VCF semantics the reference allele frequency is
`1 - sum(ALT AF)`, so the frequency budget for alleles whose AF is unavailable is the
**residual** of the known alleles:

- A missing entry (`.` / `None`) or an index beyond the end of a short array is
  treated as "no AF for this allele". The residual `max(0, 1 - sum(known ALT AFs at
  this site))` is split **equally** among all missing alleles at that site and used
  as their AF.
- A cardinality warning is logged when an array is shorter than the ALT list, and a
  missing-entry warning is logged when a present array contains a VCF `.` value.
- This keeps the per-site AF total at exactly 1.0 (or 0.0 when the known alleles
  already sum to ≥ 1) and is more conservative for resistance calling than assuming
  a missing allele is fully present.

Example: `ALT=C,G,T` with `AF=0.1,0.2` → `C=0.1`, `G=0.2`, `T=0.7` (residual
`1 - 0.1 - 0.2` split over the one missing allele). `ALT=C,G,T` with `AF=0.1,.,0.3`
→ `C=0.1`, `G=0.6` (residual `1 - 0.1 - 0.3`), `T=0.3`. A biallelic `ALT=C` with
`AF=.` → `C=1.0` (residual `1 - 0`).

#### Single-sample and multi-sample vcf files

ResistanceProfiler is designed for single-sample VCF input. When FORMAT-level values
are needed, the **first sample** in the VCF header is used unconditionally; sample
selection is not configurable. Multi-sample VCFs are not rejected, but only the first
sample contributes FORMAT/AF and FORMAT/AD values, which is rarely the intended
semantics for multi-sample data.

## Inspect project metadata and curated rules

Project metadata:

```bash
respro manage database myrespro.db --info
```

Rules table (both single and combination rules):

```bash
respro manage database myrespro.db --rules
```

Rules table filtered by reference (partial, case-insensitive match):

```bash
respro manage database myrespro.db --rules --reference NC_001806
```

List only single (atomic) rules or only combination (formula) rules:

```bash
respro manage database myrespro.db --list-single
respro manage database myrespro.db --list-combi
```

| Option | Description |
|---|---|
| `--info` | Show project metadata. |
| `--rules` | Show all resistance rules (single and combination). |
| `--list-single` | List only single (atomic) resistance rules. |
| `--list-combi` | List only combination (formula) resistance rules. |
| `--reference TEXT` | Optional reference filter (partial, case-insensitive). Use with `--rules`, `--list-single`, or `--list-combi`. |

## Inspect and delete stored runs

List runs:

```bash
respro manage results my_results.db --list
```

Delete one run without interactive confirmation:

```bash
respro manage results my_results.db --delete 1 --force
```

| Option | Description |
|---|---|
| `--list` | List stored profiling runs. |
| `--delete ID` | Delete one run by id. Prompts for confirmation unless `--force` is set. |
| `--sync PATH` | Re-annotate all stored runs against this project database (see below). |
| `--force`, `-f` | Skip the delete confirmation prompt. |

## Re-annotate stored runs against updated rules

When you update the rules in your project database, you can re-annotate all stored runs that share the same project fingerprint in one go:

```bash
respro manage results my_results.db --sync myrespro.db
```

This updates the rule hits in the results database so that regenerating a report reflects the latest rules.

## Add manual interpretation fields

Attach a manual classification to a stored run. If the report is regenerated from the same results database later, the classification is preserved and shown in the HTML report.

```bash
respro classify \
  --results-db my_results.db \
  --run-id 1 \
  --drug aciclovir \
  --phenotype resistant \
  --note "manual check"
```

At least one of `--phenotype`, `--clinical-phenotype`, `--ic50`, or `--fold-ic50` must be provided.

| Option | Description |
|---|---|
| `--results-db PATH`, `-d` | Results database. **Required.** |
| `--run-id INT`, `-i` | Run ID to classify. **Required.** |
| `--drug TEXT` | Drug name this classification applies to. **Required.** |
| `--phenotype TEXT` | Resistance phenotype label (e.g. `susceptible` / `intermediate` / `resistant` / `low-level resistance` / `unknown`) or a bare rank `1`–`5`. |
| `--clinical-phenotype TEXT` | Externally verified clinical phenotype label (same vocabulary as `--phenotype`) or a bare rank `1`–`5`. |
| `--ic50 TEXT` | IC50 value string. |
| `--fold-ic50 TEXT` | Fold-IC50 value string. |
| `--note TEXT` | Free-text note. |
| `--source TEXT` | Source or reference for this classification. |

## Optional export formats

All profiling commands write the HTML report by default. Pass `--export` (repeatable)
to additionally emit structured exports alongside it:

- `--export json` — machine-readable `*.results.json` (reproducible artifact; can be
  fed back via `respro regenerate --json`).
- `--export pdf` — summary-only `*.report.pdf`.
- `--export tsv` — denormalized `*.results.tsv` table of every variant and its matched
  rules. See the [Output guide](output.md#tsv-export-resultstsv) for the column layout
  and row semantics.

```bash
respro vcf \
  --project myrespro.db \
  --vcf my_ngs_result.vcf \
  --ref-fasta my_vcf_ref.fasta \
  --output my_output \
  --export json \
  --export tsv
```

## Regenerate reports

Rebuild a report from a stored run or from a JSON export. Use either `--results-db` with `--run-id`, **or** `--json` — not both.

From a stored run:

```bash
respro regenerate \
  --project myrespro.db \
  --results-db my_results.db \
  --run-id 1 \
  --output my_output \
  --export pdf
```

From a JSON export:

```bash
respro regenerate \
  --project myrespro.db \
  --json my_output/sample_variants.results.json \
  --output my_output
```

| Option | Description |
|---|---|
| `--project PATH`, `-p` | Project database. **Required.** |
| `--output PATH`, `-o` | Output path (directory or HTML file path). **Required.** |
| `--results-db PATH`, `-d` | Results database. Use with `--run-id`. |
| `--run-id INT`, `-i` | Run ID to regenerate. Use with `--results-db`. |
| `--json PATH`, `-j` | Results JSON export to regenerate from. |
| `--export FORMAT`, `-e` | Extra export format alongside HTML (`pdf`, `json`, `tsv`). Repeatable. |

!!! tip "Regenerate from JSON"
    Regenerating from a JSON file is useful for archival and deterministic reproduction without needing the original results database.
