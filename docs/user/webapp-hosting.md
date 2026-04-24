# Web App: Run and Host (Detailed)

This guide focuses on Docker deployment (recommended), followed by a native non-Docker path.

The web app uses the same core workflow as the CLI: profile samples against a curated internal
project database after reference normalization.

## Deployment modes

1. Docker Compose (recommended for hosting)
2. Native local run (useful for development/debugging)

## Docker Compose deployment (recommended)

The repository contains a full stack definition in `docker-compose.web.yml`:

- `redis`
- `respro-web` (FastAPI + static frontend)
- `respro-worker` (RQ worker)

### 1. Prepare data directory

```bash
mkdir -p data
```

The backend now organizes a deterministic startup workspace under the mounted data root.

At startup, these folders are created automatically:

- `data/project_databases/` — project database catalog (`*.db`), including maintained and custom DBs
- `data/uploads/` — temporary uploaded FASTA/VCF/BAM/JSON files
- `data/results/` — temporary/generated report artifacts (`*.report.html`, `*.results.json`, `*.mutations.tsv`) and `results.db`

Place at least one project DB file in `data/project_databases/` before startup when maintained bootstrap is disabled.

### 2. Build and start the stack

```bash
docker compose -f docker-compose.web.yml up --build
```

Default access:

- App: `http://127.0.0.1:8000/app/`
- Health: `http://127.0.0.1:8000/api/health`

### 3. Run in background

```bash
docker compose -f docker-compose.web.yml up -d --build
```

### 4. Stop the stack

```bash
docker compose -f docker-compose.web.yml down
```

### 5. Scale workers

```bash
docker compose -f docker-compose.web.yml up -d --scale respro-worker=2
```

## Docker image details

`Dockerfile.web` uses multi-stage build:

1. frontend build stage (Vite build)
2. Python build stage (installs `respro` and backend dependencies)
3. runtime stage (serves FastAPI and frontend dist)

Container startup command:

```bash
python -m web.backend.main
```

## Key environment variables (Docker and native)

- `RESPRO_WEB_HOST`: bind host
- `RESPRO_WEB_PORT`: bind port
- `RESPRO_WEB_DATA_DIR`: app data root
- `RESPRO_WEB_RESULTS_DB`: explicit results DB override
- `RESPRO_WEB_API_TOKEN`: API bearer token
- `RESPRO_WEB_CORS_ORIGINS`: comma-separated allowed origins
- `RESPRO_WEB_UPLOAD_RATE_LIMIT`: upload rate limit
- `RESPRO_WEB_MAINTAINED_BOOTSTRAP`: `true|false` (default `false`) startup bootstrap for maintained DBs
- `REDIS_URL`: RQ/Redis connection URL

### Maintained DB startup bootstrap

If `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true`, the backend checks maintained DB availability at startup and downloads any missing maintained databases into `project_databases/`.

Current semantics:

- scope: all maintained databases
- update behavior: missing-only (existing `.db` files are not overwritten)
- failure behavior: startup fails if bootstrap cannot complete successfully

## Native (non-Docker) deployment

Use this mode when you want direct control over Python and Node processes.

### 1. Install backend dependencies

```bash
pip install -r web/backend/requirements.txt
```

### 2. Build frontend assets

```bash
npm --prefix web/frontend install
npm --prefix web/frontend run build
```

### 3. Start backend

```bash
RESPRO_WEB_PORT=8011 python -m web.backend.main
```

If port `8000` is already in use, set another port via `RESPRO_WEB_PORT`.

## Production hosting recommendations

- Use Docker Compose as baseline deployment pattern.
- Place backend behind reverse proxy and TLS termination.
- Set `RESPRO_WEB_API_TOKEN` for non-local deployments.
- Use explicit `RESPRO_WEB_CORS_ORIGINS` allowlists.
- Keep `data/` on persistent storage and back up `project_databases/*.db` and `results/results.db`.
