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
pip install respro
```

For development installs, clone the repository and install editable:

```bash
git clone https://github.com/the-foxlab/ResistanceProfiler.git
cd ResistanceProfiler
pip install -e ".[dev]"
```

### via Bioconda

```bash
conda create -n respro -c conda-forge -c bioconda respro
conda activate respro
```

Bioconda is the recommended install path if you already use conda/mamba — it handles the `mappy` native dependency automatically without a C compiler.

### Troubleshooting: `mappy` fails to install during installation with pip

On some systems, pip-based installs fail while building `mappy` (the minimap2 Python binding). Try these steps:

1. Upgrade build tooling first:

```bash
python -m pip install --upgrade pip setuptools wheel
```

2. Ensure a working compiler/build environment is available (C/C++ toolchain and Python build headers), then rerun the install command.

3. Fallback: preinstall `mappy` via conda/mamba, then install ResistanceProfiler with pip:

```bash
mamba create -n respro -c conda-forge -c bioconda python=3.12 mappy
mamba activate respro
pip install .
```

## Web app

### Docker (recommended)

The web app is published as a Docker image on GitHub Container Registry:

```
ghcr.io/the-foxlab/resistanceprofiler
```

Clone the repository and start the stack:

```bash
git clone https://github.com/the-foxlab/ResistanceProfiler.git
cd ResistanceProfiler
docker compose -f docker-compose.web.yml up --build
```

Or pull the released image directly:

```bash
docker pull ghcr.io/the-foxlab/resistanceprofiler:latest
```

The stack includes the FastAPI backend, an RQ worker, and Redis. Open the app at `http://127.0.0.1:8000/`.

For configuration options (authentication, CORS, rate limiting, data directories), see [Web app](webapp.md).

### From source

To run the web app without Docker, install the package and its web dependencies:

```bash
git clone https://github.com/the-foxlab/ResistanceProfiler.git
cd ResistanceProfiler
pip install -e ".[dev]"
pip install -r web/backend/requirements.txt
```

Then start the backend and worker as described in [Web app](webapp.md).

## Verify installation

```bash
respro --version
```

You should see a version string like `respro 0.1.0`.

If the command is not found, verify that your virtual environment is active and rerun the install command.

!!! tip "Next steps"
    After installation, continue to [Quickstart](quickstart.md) or [Database Preparation](database-preparation.md).
