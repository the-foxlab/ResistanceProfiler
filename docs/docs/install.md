---
title: Installation
description: How to install ResistanceProfiler
---

# Installation

Choose the installation method that fits your use case:

- **CLI** — install with pip, Bioconda, or from source
- **Web app** — run with Docker or from source

## CLI installation

### via pip

```bash
git clone https://github.com/the-foxlab/ResistanceProfiler.git
cd ResistanceProfiler
pip install -e ".[dev]"
respro --version
```

### via Bioconda

```bash
conda create -n respro -c conda-forge -c bioconda respro
conda activate respro
respro --version
```

Bioconda is the recommended install path if you already use conda/mamba — it handles the `mappy` native dependency automatically without a C compiler.

### via BioContainers (Docker)

```bash
docker pull quay.io/biocontainers/respro
```

The BioContainers image provides a containerized CLI environment. Mount your data directories and run `respro` commands inside the container.

## Web app

### Docker (recommended)

Clone the repository and start the stack:

```bash
git clone https://github.com/the-foxlab/ResistanceProfiler.git
cd ResistanceProfiler
docker compose -f docker-compose.web.yml up --build
```

Or pull the released image directly. The image is published as a Docker image on GitHub Container Registry:

```bash
docker pull ghcr.io/the-foxlab/resistanceprofiler:latest
```

The stack includes the FastAPI backend, an RQ worker, and Redis. Open the app at `http://127.0.0.1:8000/`.
For configuration options (authentication, CORS, rate limiting, data directories), see [Web app](webapp.md).

!!! tip "Next steps"
    After installation, continue to [Quickstart](quickstart.md) or [Database Preparation](database-preparation.md).
