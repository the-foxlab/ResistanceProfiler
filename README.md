![ResistanceProfiler](docs/manual/docs/assets/logo.svg)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT) ![Supported Python versions](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13-2f6db3)

Pathogen-agnostic antiviral resistance profiling from consensus sequences or VCF-derived variants.

One harmonized report that classifies mutations and assists diagnostic interpretation against curated project databases.

## Highlights

- Framework for genotypic antiviral resistance analysis — not limited to a single pathogen
- Curated project databases with built-in QC and reference normalization
- Amino-acid-centered rule matching with combination-rule support (AND, OR, NOT, XOR)
- Pre-ported maintained databases available via `respro databases --download`
- CLI-first design for workflow integration; companion WebApp for interactive use
- Deterministic report regeneration from stored results

## Quick start

Install:

```bash
pip install -e ".[dev]"
```

Download a maintained database:

```bash
respro databases --download herpesdrg --output my_folder/
```

Profile:

```bash
respro fasta --project my_folder/herpesdrg.db --fasta sample.fasta --output results/
```

or

```bash
respro vcf --project my_folder/herpesdrg.db --vcf sample.vcf --ref-fasta ref.fasta --output results/
```

→ **Full guide: [Installation](https://jonas-fuchs.github.io/ResistanceProfiler/install/) · [Quickstart](https://jonas-fuchs.github.io/ResistanceProfiler/quickstart/) · [CLI Reference](https://jonas-fuchs.github.io/ResistanceProfiler/cli-reference/)**

## Web app

```bash
docker compose -f docker-compose.web.yml up --build
```

Open at `http://127.0.0.1:8000/`

→ **[Web app documentation](https://jonas-fuchs.github.io/ResistanceProfiler/webapp/)**

## Documentation

The full manual is at **[jonas-fuchs.github.io/ResistanceProfiler](https://jonas-fuchs.github.io/ResistanceProfiler/)**

Covers installation, database preparation, rules format, CLI commands, output interpretation, interpretation algorithms, and more.

## License

MIT — source code only. External databases and references may carry separate licenses.
