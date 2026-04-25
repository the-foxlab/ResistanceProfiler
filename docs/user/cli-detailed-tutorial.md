# CLI Tutorial

This tutorial covers all primary CLI command groups:

- `databases`
- `init`
- `add`
- `vcf`
- `fasta`
- `regenerate`
- `classify`
- `sync`
- `manage database`
- `manage results`

The important workflow idea is that ResPro profiles against one internal project database. New sample data is first normalized to that internal reference space before amino-acid rules are matched.

## 1. Download a maintained database (`respro databases`)

List available pre-ported databases:

```bash
respro databases --list
```

Download a database by name:

```bash
respro databases --download db_name --output my_folder/
```

ResPro automatically downloads TSV rules and GenBank files, then builds a ResPro-compatible SQLite database from scratch. Database creation can take a moment because ResPro enriches entries with PubMed and PubChem information. Add `--VV` to see verbose progress:

```bash
respro --VV databases --download db_name --output my_folder/
```

## 2. Initialize a project database (`respro init`)

Use this when you have your own GenBank reference and rules TSV instead of a maintained database:

```bash
respro init \
  --name "Docs Demo" \
  --genbank some_reference.gb \
  --rules rules.tsv \
  --formula-rules combinatorial_rules.tsv \
  --output myrespro.db
```

If your dataset only contains atomic mutation rules, omit `--formula-rules`.

## 3. Extend or validate rules in an existing project (`respro add`)

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

## 4. Profile FASTA input (`respro fasta`)

```bash
respro fasta \
  --project myrespro.db \
  --fasta my_consensus_sequence.fasta \
  --output my_output \
  --results-db my_results.db \
  --export json
```

## 5. Profile VCF input (`respro vcf`)

```bash
respro vcf \
  --project myrespro.db \
  --vcf my_ngs_result.vcf \
  --ref-fasta my_vcf_ref.fasta \
  --output my_output \
  --results-db my_results.db \
  --min-af 0.01 \
  --min-depth 0 \
  --export json
```

## 6. Inspect project metadata and curated rules (`respro manage database`)

Project metadata:

```bash
respro manage database myrespro.db --info
```

Rules table:

```bash
respro manage database myrespro.db --rules
```

Rules table filtered by reference:

```bash
respro manage database myrespro.db --rules --reference NC_001806
```

## 7. Inspect and delete stored runs (`respro manage results`)

List runs:

```bash
respro manage results my_results.db --list
```

Delete one run without interactive confirmation:

```bash
respro manage results my_results.db --delete 1 --force
```

## 8. Re-annotate stored runs against updated rules (`respro sync`)

Sync one run:

```bash
respro sync \
  --results-db my_results.db \
  --project myrespro.db \
  --run-id 1
```

Sync all runs with matching project fingerprint:

```bash
respro sync \
  --results-db my_results.db \
  --project myrespro.db
```

## 9. Add manual interpretation fields (`respro classify`)

```bash
respro classify \
  --results-db my_results.db \
  --run-id 1 \
  --drug aciclovir \
  --phenotype resistant \
  --note "manual check"
```

## 10. Regenerate reports (`respro regenerate`)

From a stored run:

```bash
respro regenerate \
  --project myrespro.db \
  --results-db my_results.db \
  --run-id 1 \
  --output my_output \
  --export tabular
```

From a JSON export:

```bash
respro regenerate \
  --project myrespro.db \
  --json my_output/sample_variants.results.json \
  --output my_output
```
