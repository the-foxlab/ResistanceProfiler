---
title: Contributing
description: How to contribute, report issues, and get in touch
---

# Contributing

Contributions are very welcome, especially curated rule datasets, bug reports, reproducible test cases, and code improvements.

## Bug reports and feature requests

Open an issue on [GitHub](https://github.com/the-foxlab/ResistanceProfiler) with:

- A clear description of the problem or feature
- Steps to reproduce (for bugs)
- Expected vs. actual behavior
- ResPro version and Python version

## Pull requests

1. Fork the repository.
2. Create a feature branch.
3. Make your changes with tests.
4. Open a pull request against the `master` branch.

Curated rule datasets are especially welcome — see [Database Preparation](database-preparation.md) and [Rules TSV Format](rules-format.md) for the expected file formats.

## Design principles

These principles guide development and code review:

- **CLI-first** — backend changes must remain usable through `respro`.
- **Core independence** — domain logic lives in `respro/`, not in web-only layers.
- **Deterministic outputs** — reporting and regeneration flows must be reproducible.
- **Framework-first design** — the project database is the stable internal reference layer for profiling.
- **Codon-aware** — resistance interpretation stays amino-acid-centered.
- **Fail fast** — require data to be present; no silent fallbacks or compatibility shims.
- **Thin CLI handlers** — orchestration belongs in `respro/cli/`; reusable logic belongs in package modules (`respro/core/`, `respro/io/`, `respro/db/`, `respro/report/`).

## Module layout

- `respro/cli` — CLI command registration and orchestration
- `respro/core` — reference-normalized profiling, amino-acid interpretation, and rule matching
- `respro/io` — format-specific parsing (GenBank, VCF, FASTA, TSV) and external data retrieval
- `respro/db` — schema, migrations, and persistence/query helpers
- `respro/report` — HTML/JSON/TSV/PDF export and plotting
- `web/backend` — FastAPI API, job queue, and startup config
- `web/frontend` — React frontend

## Local quality gates

```bash
python -m pytest
```

## Direct contact

For direct contact, email [Jonas Fuchs](mailto:jonas.fuchs@uniklinik-freiburg.de).
