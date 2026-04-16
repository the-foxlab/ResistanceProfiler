# ResistanceProfiler

A pathogen-agnostic framework for curated, codon-aware antiviral resistance profiling
from Fasta sequence or NGS-derived variants.

## Installation

```bash
pip install -e ".[dev]"
```

## Quick start

### 1. Initialize a project

```bash
respro init \
    --name "My HIV Project" \
    --genbank references.gb \
    --rules rules.tsv \
    --output project.db
```

`--genbank` can be provided in two ways:

- one GenBank file containing multiple records; or
- multiple GenBank files by repeating `--genbank`.

Example with two separate input files:

```bash
respro init \
    --name "My HSV Project" \
    --genbank ref_a.gb \
    --genbank ref_b.gb \
    --rules rules.tsv \
    --output project.db
```

During initialization `respro` automatically queries
[PubChem](https://pubchem.ncbi.nlm.nih.gov/) to attach a CID, a canonical URL,
and a short description to every drug found in the rules file. This information
is stored in the project database and surfaced in the resistance report as a
clickable link. No API key is required.

**PubChem lookup is best-effort and never blocks database creation:**

- If a drug name is not recognised by PubChem, the drug is stored without
  PubChem data and a warning is logged.
- If the network is unavailable, all drugs are stored without PubChem data and
  the project is built normally.

To skip PubChem lookup entirely (e.g. in offline environments or CI pipelines):

```bash
respro init \
    --name "My HIV Project" \
    --genbank references.gb \
    --rules rules.tsv \
    --output project.db \
    --no-drug-info
```

### 2. Profile a sample

```bash
respro vcf \
    --project project.db \
    --vcf sample.vcf \
    --ref-fasta sample_ref.fasta \
    --output report/
```

For consensus FASTA input, use:

```bash
respro fasta \
    --project project.db \
    --fasta sample_consensus.fasta \
    --output report/
```

Repeat runs automatically reuse cached query-reference mappings when the same
FASTA header and sequence are provided again.

### 3. Add rules to an existing project

If the project database already contains the relevant reference and gene
annotations, you can add more rules without supplying another GenBank file:

```bash
respro add \
    --project project.db \
    --rules more_rules.tsv
```

If you are adding rules together with new references/genes, you can also provide
additional GenBank input:

```bash
respro add \
    --project project.db \
    --genbank additional_refs.gb \
    --rules more_rules.tsv
```

During `add`, rule duplicates are detected biologically rather than by
comment fields. A rule is treated as already present if the same reference,
position, reference amino acid, mutation, and drug already exist in the
database. Existing rows are kept; incoming `ic50`, `publication`, `source`, or
other commentary fields do not overwrite them. Drug names are stored in
lowercase to avoid case-only duplicates.

### 4. Export a portable project bundle

```bash
respro export \
    --project project.db \
    --output project_bundle.zip
```

## Input formats

### GenBank input

Project initialisation uses one or more GenBank inputs via `--genbank`.
Each provided file may itself contain one or more records. For each record,
`respro` stores:

- the reference identifier and accession;
- the organism / species label where available from the GenBank metadata;
- taxonomy where available from the GenBank metadata;
- the reference length;
- all CDS features as genes, including protein/product name, coordinates, strand,
  `codon_start`, CDS nucleotide slices, and amino-acid translations.

The project itself is therefore not restricted to a single pathogen. Multi-record
GenBank files or multiple separate GenBank files can represent multiple related
pathogens or references in one database.

Gene identifiers used in the rules file must match the CDS identifiers extracted
from the GenBank annotations for the corresponding reference.

Quality checks during `respro init`:

- every rules gene must exist in the GenBank CDS annotations;
- for multi-record GenBank files, ambiguous genes require `reference_identifier`
  in the rules TSV;
- unsupported compound CDS locations fail fast.

### Rules TSV

Rules are provided as a tab-separated file with one row per rule member.

Required columns:
`gene`, `reference_identifier`, `position`, `reference`, `mutation`, `antiviral`

Optional columns:
`phenotype`, `clinical_phenotype`, one IC50 column (`ic50` or `ic_50` or
`fold_ic50`), `publication`, `source`, `rule_group`

For the full source of truth, including allowed values per column, mutation
notation, phenotype normalization, IC50 parsing, and combination-rule syntax,
see:

- `docs/rules-tsv-format.md`

## License

MIT

