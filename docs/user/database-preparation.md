# Prepare a Database

ResistanceProfiler uses a project SQLite database (`project.db`) created from:

- at least one GenBank reference file
- one resistance [rules TSV](docs/user/rules-tsv-format.md)

If you are preparing your first database, make sure the rules file uses gene names that exist in the GenBank CDS annotations.

This database is the central asset of a ResPro workflow. It does not just store reference files. It defines the internal references, gene annotations, and curated rule set that later FASTA and VCF samples are compared against.

> [!IMPORTANT]
> Build `project.db` carefully and version it in your workflow. Most downstream interpretation quality depends on this curated project database.

## Create a new project database

```bash
respro init \
  --name "Docs Demo" \
  --genbank some_reference.gb \
  --rules rules.tsv \
  --output myrespro.db \
  --no-additional-info
```

Notes:

- `--genbank` can be repeated for multiple files.
- `--no-additional-info` skips network lookups for extra metadata.
- After initialization, later profiling runs use this database as the internal coordinate and rule source.

After this command succeeds, the file `myrespro.db` should exist.

## Inspect project metadata

```bash
respro manage database myrespro.db --info
```

## Inspect imported rules

```bash
respro manage database myrespro.db --rules
```

## Validate new rules without changing the database

```bash
respro add \
  --project myrespro.db \
  --rules rules.tsv \
  --validate
```

> [!TIP]
> Use `--validate` in CI or curation review before importing rules into a production project database.

For detailed column and mutation token requirements, see `docs/user/rules-tsv-format.md`.
