# Install ResistanceProfiler

If you are new to Python tooling, this page gives the shortest safe path to a working install.

## Requirements

- Python 3.11+
- Linux, macOS, or Windows (WSL works)
- A working C/C++ build environment for optional native dependencies

## Recommended installation (developer and power-user setup)

Optional but recommended: create and activate a virtual environment first.

```bash
python -m venv .venv
source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

## Web backend dependencies

If you use the web app backend directly, also install:

```bash
pip install -r web/backend/requirements.txt
```

## Verify installation

```bash
respro --version
```

You should see a version string like `respro 0.1.0`.

If the command is not found, verify that your virtual environment is active and rerun the install command.
