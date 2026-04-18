# ResistanceProfiler Web UI — Architecture

## Overview

The web UI is a self-contained prototype that wraps the existing `respro/` Python core behind a
REST API and serves a React single-page application. Profiling requests are executed
asynchronously by an RQ worker process; the web process only enqueues jobs and serves status.

```
Browser
  │  HTTP (port 8000)
  ▼
FastAPI (web/backend/main.py)
  ├── /app/*              → static React SPA (built frontend dist)
  ├── /api/health
  ├── /api/rules
  ├── /api/fs/list
  ├── /api/profile/fasta  → enqueue RQ job → return {job_id}
  ├── /api/profile/vcf    → enqueue RQ job → return {job_id}
  ├── /api/jobs/{job_id}  → poll job status and result
  └── /api/report
        │
       RQ / Redis (broker)
        │
       RQ Worker (web/backend/jobs.py)
        │
        ▼
   respro/ core  ←→  SQLite (project.db, results.db)
```

---

## Directory layout

```
web/
├── __init__.py
├── README.md
├── backend/
│   ├── __init__.py
│   ├── main.py          — FastAPI app factory and all route definitions
│   ├── models.py        — Pydantic request/response models
│   ├── queue.py         — RQ queue factory (FastAPI dependency)
│   ├── startup_config.py — validated startup path/auth configuration
│   ├── jobs.py          — RQ job functions for FASTA and VCF profiling
│   └── services/
│       ├── __init__.py
│       ├── browse.py    — read-only rules queries
│       └── profile.py   — FASTA and VCF profiling orchestration (called by jobs)
└── frontend/
    ├── package.json     — pinned to vite@2.9.18 (Node ≥12.22 compat)
    ├── vite.config.js   — base='/app' so built asset paths match mount point
    ├── index.html
    └── src/
        ├── main.jsx     — React 18 entry point
        ├── App.jsx      — single-file SPA (all screens)
        └── styles.css
```

---

## Backend

### App factory — `web/backend/main.py`

`create_app()` builds and returns the FastAPI instance using validated startup configuration.
The startup-config module enforces project/results/output path validity at startup and keeps
route handlers path-agnostic.

**Frontend static mount**

The built React `dist/` is mounted at `/app` using FastAPI's `StaticFiles` with `html=True`
(enables SPA fallback to `index.html`). The dist directory is resolved in this priority order:

1. `RESPRO_FRONTEND_DIST` environment variable — used in Docker where the installed package's
   `__file__` path diverges from the copied `dist/` location.
2. `Path(__file__).parents[1] / 'frontend' / 'dist'` — used in local development where the
   package is installed in editable mode (`pip install -e .`).

**CORS**

CORS is allowed for `http://127.0.0.1:5173` and `http://localhost:5173` only (the Vite dev
server). In Docker the frontend is served directly by FastAPI on port 8000 so no CORS is needed.

### Models — `web/backend/models.py`

All API payloads are Pydantic `BaseModel` subclasses.

| Model | Used by | Key fields |
|---|---|---|
| `ProfileFastaPayload` | `POST /api/profile/fasta` | `fasta_path`, `sample`, `threads`, `aligner` |
| `ProfileVcfPayload` | `POST /api/profile/vcf` | `vcf_path`, `ref_fasta_path`, `sample`, `min_af`, `min_depth`, `bam_path`, `threads`, `aligner` |
| `ApiEnvelope` | health, rules, fs | `status`, `data`, `error` |
| `JobSubmitResponse` | profile routes | `job_id`, `status` |
| `JobStatusResponse` | `/api/jobs/{job_id}` | `job_id`, `status`, `result`, `error` |

Startup-managed paths (`project.db`, `results.db`, `output_dir`) are no longer part of profile
request bodies. They are validated once and injected from startup config.

### Services

#### `startup_config.py` — startup configuration

`load_startup_config()` validates startup paths and settings:

- project DB existence and readability
- output directory writability
- automatic `results.db` initialization
- allowed filesystem roots for `/api/fs/list`
- optional API token for route protection

#### `browse.py` — read-only data queries

- `list_rules(project_db, reference_filter)` — delegates to `respro.db.rules_queries.list_rules_for_display`.
  `_normalize_reference_filter()` maps `''`, `'undefined'`, and `'null'` to `None` so browser
  state initialisation values never reach the database as literal strings.

#### `profile.py` — profiling orchestration

`profile_fasta()` and `profile_vcf()` contain the profiling pipeline logic. Both functions:

1. Open the project and results database connections.
2. Resolve the query FASTA/VCF to a reference via `resolve_fasta_query` / `pick_best_reference_id`.
3. Load reference data (gene models, rules).
4. Run the profiling pipeline.
5. Export the HTML report and save the run record.
6. Return a dict with `run_id`, `sample_name`, `report_html_path`, and a summary of findings.

These functions are called by the RQ job wrappers in `jobs.py`, not directly from routes.

#### `queue.py` — job queue factory

`get_queue()` is a FastAPI dependency that returns an RQ `Queue` connected to Redis via the
`REDIS_URL` environment variable (default: `redis://127.0.0.1:6379/0`). Tests override this
dependency with a `fakeredis`-backed synchronous queue.

#### `jobs.py` — RQ job wrappers

`run_profile_fasta()` and `run_profile_vcf()` are top-level picklable functions that translate
string arguments (RQ-serializable) to `Path` objects and call the corresponding service
functions. These are the functions enqueued by the profile routes.

### API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/rules` | List resistance rules, optional `reference` filter |
| `GET` | `/api/fs/list` | List files/directories for in-app path picker |
| `POST` | `/api/profile/fasta` | Enqueue FASTA profiling job, return `{job_id, status}` |
| `POST` | `/api/profile/vcf` | Enqueue VCF profiling job, return `{job_id, status}` |
| `GET` | `/api/jobs/{job_id}` | Poll job status; `result` populated when `status == "succeeded"` |
| `GET` | `/api/report` | Serve a saved HTML report file by path |
| `GET` | `/api/branding/logo.svg` | Serve dashboard/report logo asset |
| `GET` | `/api/branding/favicon.svg` | Serve dashboard/report favicon asset |

Profile routes return `JobSubmitResponse` immediately. The frontend polls `/api/jobs/{job_id}`
every 2 seconds until the status is `succeeded` or `failed`.

`GET /api/report` is consumed by the frontend in an in-app modal (`iframe`) so reports stay in
the same dashboard window.

All other responses use `ApiEnvelope`:

```json
{ "status": "ok", "data": { ... }, "error": null }
```

Errors return HTTP 400 with the exception message in the FastAPI `detail` field.

### Security controls (prototype baseline)

- Optional bearer token auth via `RESPRO_WEB_API_TOKEN`
- Path containment checks for `/api/fs/list` against configured allowed roots
- Report serving restricted to configured output directory

---

## Frontend

The frontend is a single React component file (`App.jsx`) with one active mode shown at a time.
Mode selection is handled by a left sidebar, and no URL router is used in this prototype.

### Screens and modes

1. **Profile VCF** — upload/run flow for `POST /api/profile/vcf` + `GET /api/jobs/{job_id}`.
2. **Profile FASTA** — upload/run flow for `POST /api/profile/fasta` + `GET /api/jobs/{job_id}`.
3. **Browse mutations** — sortable/filterable mutations table from `GET /api/mutations`.
4. **Report** — in-app report selector that opens the selected report in a same-window modal.

A global top card holds database selection and metadata (active DB, schema version, mutation
count, supported organisms) and remains visible across all modes.

### API client helpers

`apiGet(path, params)` and `apiPost(path, body)` are thin wrappers around `fetch`. `apiGet`
filters out `undefined`, `null`, and `''` from the params object before building the query
string to prevent browser uninitialised state from being sent as a literal `"undefined"` value.

`API_BASE` defaults to `http://127.0.0.1:8000` and can be overridden with the
`VITE_RESPRO_API_BASE` environment variable at build time.

### Build and base path

Vite is configured with `base: '/app'` so all built asset references use `/app/assets/...` paths,
matching the FastAPI static mount point at `/app`.

The scaffold is pinned to `vite@2.9.18` and `@vitejs/plugin-react@1.3.2` for compatibility with
Node ≥12.22 (the default system Node on Ubuntu 22.04).

---

## Docker

The image is built in two stages (`Dockerfile.web`):

1. **`frontend-build`** (`node:22-alpine`) — `npm install && npm run build` produces `dist/`.
2. **`runtime`** (`python:3.12-slim`) — `build-essential` is installed for `mappy` compilation,
   then `pip install .` installs core `respro` and `pip install -r web/backend/requirements.txt`
   installs web-only backend dependencies outside core package metadata. The built `dist/` is
   copied from stage 1 into `/app/web/frontend/dist`. `RESPRO_FRONTEND_DIST` is set to this path
   so the app finds the frontend at startup.

The `docker-compose.web.yml` starts three services: `redis` (Redis 7 broker), `respro-web` (API
server), and `respro-worker` (RQ worker subscribed to the `profiling` queue). The worker reuses
the same `respro-web:prototype` image and overrides the command to `rq worker`.

The container exposes port 8000. Startup-managed project/output data is provided via volume mounts
at runtime; no data is baked into the image.
