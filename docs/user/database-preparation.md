# Prepare a Database

ResistanceProfiler uses a project SQLite database (`project.db`) created from:

- at least one GenBank reference file
- one resistance [rules TSV](docs/user/rules-tsv-format.md)

If you are preparing your first database, make sure the rules file uses feature names that exist in the GenBank CDS annotations.

This database is the central asset of a ResPro workflow. It does not just store reference files. It defines the internal references, feature annotations, and curated rule set that later FASTA and VCF samples are compared against.

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
- `--metadata` accepts a JSON file with curated project metadata. See the section below for the supported keys and value rules.
- After initialization, later profiling runs use this database as the internal coordinate and rule source.

After this command succeeds, the file `myrespro.db` should exist.

## Optional metadata JSON

`respro init --metadata` accepts a JSON file whose top-level value must be an object. The file is used to populate project metadata fields during database creation.

Supported canonical keys are `maintainers`, `contact`, `publication_pmid`, `website`, `description`, `maintainer_update`, `license`, and `tsv_checksum`.

Common aliases are accepted for a few keys: `maintainer` maps to `maintainers`, `publication` and `pmid` map to `publication_pmid`, `maintainer update` maps to `maintainer_update`, and `tsv checksum` maps to `tsv_checksum`.

Value rules are strict. `maintainers` may be either a string or a list of strings. `publication_pmid` must contain digits only. All other supported fields must be strings. Empty values are ignored, and unknown keys are rejected. When a PMID is provided, ResPro also tries to resolve the DOI automatically from PubMed when one is available.

Example metadata file:

```json
{
  "maintainers": ["A Curator", "B Curator"],
  "contact": "team@example.org",
  "publication": "12345678",
  "website": "https://example.org/db",
  "description": "Curated antiviral resistance database.",
  "maintainer update": "2026-04-21",
  "license": "CC-BY-4.0",
  "tsv checksum": "sha256:abc123"
}
```

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
