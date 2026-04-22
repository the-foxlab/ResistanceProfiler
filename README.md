![ResistanceProfiler logo](respro/report/static/logo.svg)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT) ![Supported Python versions](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-2f6db3)

Framework for broadly applicable antiviral resistance profiling from FASTA consensus sequences or VCF-derived variants.

## Why ResistanceProfiler (in short ResPro)

- Broadly applicable framework instead of a single pathogen- or database-specific workflow
- Internal project references and curated rules stored in one reusable project database
- Query sequences and VCF-linked references can be mapped back to internal references before comparison
- Final profiling is performed on amino-acid mutations after reference normalization
- CLI-first workflows for initialization, profiling, and regeneration, with an optional web application
- Support for resistence formulas with logical operators allow definition of higher complexity rules that includes depend mutations to be present

Many resistance tools already perform amino-acid or codon-based interpretation. The main value of ResPro is that it lets you curate rules once against internal references and then compare new samples against those rules. It provides a clear and structured framework that can be used for any pathogen. The database autocurates itself (so checks if rules are valid given the provided reference) and new sequences are automatically matched against all references. Afterwards, the best matching one is selected for resistance rule comparision. Its lightning fast. No need to specify which pathogen or using a specific reference. Everything goes automatically.

In the end you get a informative html report that highlights these results. The good thing is that the CLI is made to be incoorporated into existing NGS sequencing workflows and the WebApp is made for non-bioinformatic users.

> [!TIP]
> ResPro is strongest when the same curated project database is reused across runs. Treat `project.db` as your internal reference contract.

> [!CAUTION]
> Rule quality still depends on the quality of the curated TSV source. ResPro validates and normalizes rule entries, but it does not replace biological curation.

> [!IMPORTANT]
> This tool relies on already curated and maintained databases and provides a compatibility layer for users to maintained database like [HerpesDRG](https://github.com/ojcharles/herpesdrg-db).

## Quickstart (CLI)

### 1) Install

```bash
pip install -e ".[dev]"
```

### 2) Initialize a project database

```bash
respro init \
    --name "Docs Demo" \
    --genbank data/demo-alpha/inputs/reference_hsv1.gb \
    --rules data/demo-alpha/inputs/rules_hsv1.tsv \
    --formula-rules data/demo-alpha/inputs/formula_rules_hsv1.tsv \
    --output data/demo-alpha/project/project.db \
    --no-additional-info
```

The `--formula-rules` TSV is optional. Use it when a pathogen requires boolean combination logic over atomic mutation rules.

### 3) Run FASTA profiling

```bash
respro fasta \
    --project data/demo-alpha/project/project.db \
    --fasta data/demo-alpha/inputs/sample_consensus.fasta \
    --output data/demo-alpha/output \
    --results-db data/demo-alpha/results/results.db \
    --export json
```

### 4) Regenerate from stored run

```bash
respro regenerate \
    --project data/demo-alpha/project/project.db \
    --results-db data/demo-alpha/results/results.db \
    --run-id 1 \
    --output data/demo-alpha/output \
    --export tabular
```

## Quickstart (Web App)

### 1) Install backend dependencies

```bash
pip install -r web/backend/requirements.txt
```

### 2) Build frontend assets

```bash
npm --prefix web/frontend install
npm --prefix web/frontend run build
```

### 3) Start the backend

```bash
RESPRO_WEB_PORT=8011 python -m web.backend.main
```

Quick smoke checks:

- `GET http://127.0.0.1:8011/api/health` returned status `ok`
- `GET http://127.0.0.1:8011/app/` returned `200`

## Documentation

### User Documentation

- [How to install ResPro](docs/user/install.md)
- [How to prepare a database](docs/user/database-preparation.md)
- [How to format the TSV](docs/user/rules-tsv-format.md)
- [How ResPro works](docs/user/how-respro-works.md)
- [Basic CLI tutorial](docs/user/cli-basic-tutorial.md)
- [Detailed CLI tutorial](docs/user/cli-detailed-tutorial.md)
- [How to run and host the web app (detailed)](docs/user/webapp-hosting.md)
- [Troubleshooting and FAQ](docs/user/troubleshooting-faq.md)
- [Output interpretation guide (HTML, JSON, TSV)](docs/user/output-interpretation.md)

### Development Documentation

- [Detailed development/contribution guidelines and architecture](docs/development/contribution-and-architecture.md)

## License

MIT
