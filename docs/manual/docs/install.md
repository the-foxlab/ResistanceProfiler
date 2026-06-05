---
title: Installation
description: How to install ResistanceProfiler
---

# Installation

If you are new to Python tooling, this page gives the shortest safe path to a working install.

## Requirements

- Python 3.11+
- Linux, macOS, or Windows (WSL works)
- A working C/C++ build environment for optional native dependencies

## Recommended installation

Optional but recommended: create and activate a virtual environment first.

```bash
python -m venv .venv
source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

If you prefer a non-editable install, `pip install .` is also supported.

## Troubleshooting: `mappy` fails to install

On some systems, pip-based installs fail while building `mappy` (for example during `pip install .` or `pip install -e ".[dev]"`).

Try these steps:

1. Upgrade build tooling first:

```bash
python -m pip install --upgrade pip setuptools wheel
```

2. Ensure a working compiler/build environment is available (C/C++ toolchain and Python build headers), then rerun the install command.

3. Fallback: preinstall `mappy` in a conda/mamba environment, then install ResistanceProfiler with pip:

```bash
mamba create -n respro -c conda-forge -c bioconda python=3.12 mappy
mamba activate respro
pip install .
```

## Verify installation

```bash
respro --version
```

You should see a version string like `respro 0.1.0`.

If the command is not found, verify that your virtual environment is active and rerun the install command.

!!! tip "Next steps"
    After installation, continue to [Quickstart](quickstart.md) or [Database Preparation](database-preparation.md).
