# Basic CLI Tutorial

This short tutorial runs one complete FASTA workflow.

This workflow demonstrates the core ResPro pattern: create one curated internal project database,
map sample inputs to that internal reference space, and then profile amino-acid rule matches.

What you will create:

- one project database (`project.db`)
- one results database (`results.db`)
- one HTML report and one JSON export

## 1. Initialize a project database

```bash
respro init \
  --name "Docs Demo" \
  --genbank data/demo-beta/inputs/reference_hsv1.gb \
  --rules data/demo-beta/inputs/rules_hsv1.tsv \
  --output data/demo-beta/project/project.db \
  --no-additional-info
```

## 2. Profile a consensus FASTA sample

```bash
respro fasta \
  --project data/demo-beta/project/project.db \
  --fasta data/demo-beta/inputs/sample_consensus.fasta \
  --output data/demo-beta/output \
  --results-db data/demo-beta/results/results.db \
  --export json
```

Expected output files (in `data/demo-beta/output`):

- `*.report.html`
- `*.results.json`

## 3. List stored runs

```bash
respro manage results data/demo-beta/results/results.db --list
```

You should see at least one run entry with an ID (usually `1` for the first run).

## 4. Regenerate the report from stored results

```bash
respro regenerate \
  --project data/demo-beta/project/project.db \
  --results-db data/demo-beta/results/results.db \
  --run-id 1 \
  --output data/demo-beta/output \
  --export tabular
```
