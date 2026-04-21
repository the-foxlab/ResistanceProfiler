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

Place your `project.db` at `data/demo-web/project.db`.

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
- `RESPRO_WEB_PROJECT_DB`: explicit project DB override
- `RESPRO_WEB_RESULTS_DB`: explicit results DB override
- `RESPRO_WEB_API_TOKEN`: API bearer token
- `RESPRO_WEB_CORS_ORIGINS`: comma-separated allowed origins
- `RESPRO_WEB_UPLOAD_RATE_LIMIT`: upload rate limit
- `REDIS_URL`: RQ/Redis connection URL

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
- Keep `data/` on persistent storage and back up `project.db`/`results.db`.
