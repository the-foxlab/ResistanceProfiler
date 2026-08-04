![ResistanceProfiler](docs/docs/assets/logo.svg)

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0) ![Supported Python versions](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13-2f6db3) ![Coverage](docs/docs/assets/coverage.svg)

Pathogen-agnostic antiviral resistance profiling from consensus sequences or VCF-derived variants.  

> [!TIP]
> Built your own database. Its super simple.

One harmonized report that classifies mutations and assists diagnostic interpretation against curated project databases. Comes as a CLI or a WebApp. Everything is open-source. Contributions are welcome!

### [Dokumentation](https://the-foxlab.github.io/ResistanceProfiler/cli-reference/) · [WebApp (live)](https://resistanceprofiler.uniklinik-freiburg.de) · [Installation](https://the-foxlab.github.io/ResistanceProfiler/install/) · [Databases](https://github.com/the-foxlab/respro-databases)

## Highlights

- Framework for genotypic antiviral resistance analysis — not limited to a single pathogen
- Get auto-curated project databases with built-in QC and reference normalization
- Amino-acid-centered rule matching with combination-rule support (AND, OR, NOT, XOR)
- Pre-ported maintained databases available via `respro databases --download`
- CLI-first design for workflow integration; companion WebApp for interactive use
- Deterministic report regeneration from stored results

## Quick start

Install the CLI:

```bash
git clone https://github.com/the-foxlab/ResistanceProfiler
pip install -e ".[dev]"
```

Download a maintained database (e.g. herpesDRG):

```bash
respro databases --list
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

→ **Full guide: [Installation](https://the-foxlab.github.io/ResistanceProfiler/install/) · [Quickstart](https://the-foxlab.github.io/ResistanceProfiler/quickstart/) · [CLI Reference](https://the-foxlab.github.io/ResistanceProfiler/cli-reference/)**

## Web app (host the complete WebApp via docker)

```bash
git clone https://github.com/the-foxlab/ResistanceProfiler
docker compose -f docker-compose.web.yml up --build
```

Open at `http://127.0.0.1:8000/`

→ **[Web app documentation](https://the-foxlab.github.io/ResistanceProfiler/webapp/)**

## License

AGPL v3.0 — source code only. External databases and references may carry separate licenses.
