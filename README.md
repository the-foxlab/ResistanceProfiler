![ResistanceProfiler logo](web/frontend/src/assets/logo.svg)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT) ![Supported Python versions](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13-2f6db3)

Pathogen agnostic antiviral resistance profiling command-line interface (CLI) for FASTA consensus sequences or VCF-derived variants. Also comes with a WebApp and pre-ported databases.

## Why use ResistanceProfiler (in short ResPro)?

- Think of it as a framework for genotypic antiviral resistance analysis instead of a single pathogen-specific workflow
- One harmonized report that classifies mutations and assists diagnostic interpretations
- Internal project references and curated mutation substitution rules stored in one reusable project database
- Companion [repository](https://github.com/jonas-fuchs/respro-db) auto-updates maintained databases and makes them ResPro compatible. Users can download and build curated databases directly via the CLI. No need to do much, just download and test your sequences.
- Support for custom rule sets. You have an in-house closed database? Transform it to a ResPro compatible database and enjoy simplified analyses.
- The database creation performs stringent QC and ensures that resistance rules stay coherent with the internal reference sequence.
- Query sequences and VCF-linked references can be mapped back to internal references before comparison
- Final profiling is performed on amino-acid mutations after reference normalization
- Store your results in an SQlite database and regenerate the html report. No need to store everything. ResPro can save your resluts in a compact way and lets you regenerate your results or update them when the database changes. Moreover you can add custom metadata to your results. (these functions are currently only supported by the CLI).
- Support for resistence formulas with logical operators (`parenthesis`, `OR`, `AND`, `XOR` and `NOT`) to allow definition of higher complexity rules.

Its lightning fast. No need to specify which pathogen or using a specific reference during profiling against the database. Everything goes automatically. The CLI is made to be incoorporated into existing NGS sequencing workflows and the WebApp is made for non-bioinformatic users.

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

> [!NOTE]
> If installation fails during a `mappy` build step (for example with `pip install .` or `pip install -e ".[dev]"`), see [docs/user/install.md](docs/user/install.md) for quick troubleshooting steps.

### 2) Initialize a project database

Either initialize your own dataset with your own [custom rules](docs/user/database-preparation.md):

```bash
respro init \
    --name "Docs Demo" \
    --genbank some_reference.gb \
    --rules rules.tsv \
    --formula-rules combinatorial_rules.tsv \
    --output myrespro.db \
```

or list available ported databases:

```bash
respro databases --list
```

and then download:

```bash
respro databases --download db_name --output my_folder/
```

For faster setup with less network-dependent enrichment, you can disable optional PubChem/PubMed
lookups during build:

```bash
respro databases --download db_name --no-additional-info --output my_folder/
```

In this step respro automatically downloads tsv rules and genbank files temporarily and then builds the respro compatible SQlite database from scratch. As ResPro automatically enriches the databases with Pubmed and Pubchem information database creation can take a bit. If you want to see what ResPro is doing, just add a `-vv` to your command: `respro -vv databases ...`. The database files are available [here](https://github.com/jonas-fuchs/respro-db/tree/main).

### 3) Run profiling

For fasta consensus sequences:

```bash
respro fasta \
    --project my_database.db \
    --fasta my_consensus_sequence.fasta \
    --output my_output \
    --results-db my_results.db \
    --export json \
    --export pdf
```

Or from vcf files:

```bash
respro vcf \
    --project my_database.db \
    --vcf my_ngs_result.vcf \
    --ref-fasta my_vcf_ref.fasta \
    --output my_output \
    --results-db my_results.db \
    --export json \
    --export pdf
```

For bundled, ready-to-run sample inputs, see [example_data/README](example_data/README).
The exported VCF example report should look like the published GitHub Pages demo report for this project.

### 4) Regenerate from stored run

```bash
respro regenerate \
    --project my_database.db \
    --results-db my_results.db \
    --run-id 1 \
    --output my_output
```

> [!TIP]
> You can also regenerate your result from a json file.

## Quickstart [Web App](docs/user/webapp-hosting.md) with docker

Build locally:

```bash
docker compose -f docker-compose.web.yml up --build
```

Use a prebuilt GHCR release image:

```bash
docker pull ghcr.io/jonas-fuchs/resistanceprofiler:<release-tag>
docker tag ghcr.io/jonas-fuchs/resistanceprofiler:<release-tag> respro-web:latest
docker compose -f docker-compose.web.yml up
```

Open the app at: `http://127.0.0.1:8000/`

> [!CAUTION]
> At initial startup, the app can download maintained databases if none are available in `./data/project_databases/`. To disable this, set `RESPRO_WEB_MAINTAINED_BOOTSTRAP=false` in `docker-compose.web.yml`.

## Detailed Documentation

### User Documentation

- [How to install ResPro](docs/user/install.md)
- [How to prepare a database](docs/user/database-preparation.md)
- [How to format the TSV](docs/user/rules-tsv-format.md)
- [How ResPro works](docs/user/how-respro-works.md)
- [CLI tutorial](docs/user/cli-detailed-tutorial.md)
- [How to run and host the web app (detailed)](docs/user/webapp-hosting.md)
- [Output interpretation guide (HTML, JSON, TSV)](docs/user/output-interpretation.md)
- [Comparision to other antiviral resistance testing tools](docs/user/tool-comparision.md)

### Development Documentation

- [Detailed development/contribution guidelines and architecture](docs/development/contribution-and-architecture.md)

## License

MIT
