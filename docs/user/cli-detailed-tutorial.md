# Detailed CLI Tutorial

This tutorial covers all primary CLI command groups:

- `init`
- `add`
- `vcf`
- `fasta`
- `regenerate`
- `classify`
- `sync`
- `manage database`
- `manage results`

The important workflow idea is that ResPro profiles against one internal project database.
New sample data is first normalized to that internal reference space before amino-acid rules are matched.

## 1. Initialize a project database (`respro init`)

```bash
respro init \
  --name "Docs Demo" \
  --genbank data/demo-gamma/inputs/reference_hsv1.gb \
  --rules data/demo-gamma/inputs/rules_hsv1.tsv \
  --output data/demo-gamma/project/project.db \
  --no-additional-info
```

## 2. Extend or validate rules in an existing project (`respro add`)

Validate rules without writing changes:

```bash
respro add \
  --project data/demo-gamma/project/project.db \
  --rules data/demo-gamma/inputs/rules_hsv1.tsv \
  --validate
```

Add rules and commit changes:

```bash
respro add \
  --project data/demo-gamma/project/project.db \
  --rules data/demo-gamma/inputs/rules_hsv1.tsv
```

## 3. Profile FASTA input (`respro fasta`)

```bash
respro fasta \
  --project data/demo-gamma/project/project.db \
  --fasta data/demo-gamma/inputs/sample_consensus.fasta \
  --output data/demo-gamma/output \
  --results-db data/demo-gamma/results/results.db \
  --export json
```

## 4. Profile VCF input (`respro vcf`)

```bash
respro vcf \
  --project data/demo-gamma/project/project.db \
  --vcf data/demo-gamma/inputs/sample_variants.vcf \
  --ref-fasta data/demo-gamma/inputs/sample_reference.fasta \
  --output data/demo-gamma/output-vcf \
  --results-db data/demo-gamma/results/results-vcf.db \
  --min-af 0.01 \
  --min-depth 0 \
  --export json
```

## 5. Inspect project metadata and curated rules (`respro manage database`)

Project metadata:

```bash
respro manage database data/demo-gamma/project/project.db --info
```

Rules table:

```bash
respro manage database data/demo-gamma/project/project.db --rules
```

Rules table filtered by reference:

```bash
respro manage database data/demo-gamma/project/project.db --rules --reference NC_001806
```

## 6. Inspect and delete stored runs (`respro manage results`)

List runs:

```bash
respro manage results data/demo-gamma/results/results.db --list
```

Delete one run without interactive confirmation:

```bash
respro manage results data/demo-gamma/results/results.db --delete 1 --force
```

## 7. Re-annotate stored runs against updated rules (`respro sync`)

Sync one run:

```bash
respro sync \
  --results-db data/demo-gamma/results/results.db \
  --project data/demo-gamma/project/project.db \
  --run-id 1
```

Sync all runs with matching project fingerprint:

```bash
respro sync \
  --results-db data/demo-gamma/results/results.db \
  --project data/demo-gamma/project/project.db
```

## 8. Add manual interpretation fields (`respro classify`)

```bash
respro classify \
  --results-db data/demo-gamma/results/results.db \
  --run-id 1 \
  --drug aciclovir \
  --phenotype resistant \
  --note "manual check"
```

## 9. Regenerate reports (`respro regenerate`)

From a stored run:

```bash
respro regenerate \
  --project data/demo-gamma/project/project.db \
  --results-db data/demo-gamma/results/results.db \
  --run-id 1 \
  --output data/demo-gamma/output \
  --export tabular
```

From JSON export:

```bash
respro regenerate \
  --project data/demo-gamma/project/project.db \
  --json data/demo-gamma/output-vcf/sample_variants.results.json \
  --output data/demo-gamma/output-vcf
```
