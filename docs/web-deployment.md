# ResistanceProfiler Web Prototype Deployment

This document describes how to run the prototype Web UI in self-hosted mode.

## Scope of this prototype

- FastAPI backend accepts profiling requests and enqueues them as RQ jobs.
- An RQ worker process executes the jobs using `respro/` domain logic.
- A Redis instance acts as the job broker between the web process and the worker.
- Backend paths are startup-managed (project DB, results DB, output directory).
- React frontend provides FASTA/VCF submission, job polling, and rules browsing.
- Local/self-hosted usage is the target.

## Should this be packaged as Docker now?

Yes. Packaging this prototype in Docker is recommended now because it avoids local dependency drift
between Python and Node environments and gives a reproducible local deployment path for non-expert
users.

## Option 1: Native local development

### 1. Install Python dependencies

```bash
pip install -e .[dev]
pip install -r web/backend/requirements.txt
```

### 2. Start Redis

A local Redis instance is required. You may start one with Docker:

```bash
docker run --rm -p 127.0.0.1:6379:6379 redis:7-alpine
```

### 3. Start backend

```bash
python -m web.backend.main
```

Backend listens on `http://127.0.0.1:8000` by default.
Set `REDIS_URL` (default: `redis://127.0.0.1:6379/0`) if Redis is on a different host or port.

Optional startup configuration environment variables:

- `RESPRO_WEB_DATA_DIR` — root directory for all app data (`project.db`, `results.db`, reports, uploads); defaults to `./data` next to the repo root or `/data` when that mount exists (Docker)
- `RESPRO_WEB_PROJECT_DB` — override absolute path to `project.db` (default: `data/project.db`)
- `RESPRO_WEB_RESULTS_DB` — override absolute path to `results.db` (created automatically; default: `data/results.db`)
- `RESPRO_WEB_ALLOWED_ROOTS` — comma-separated allowed filesystem roots for path browsing (default: `RESPRO_WEB_DATA_DIR`)
- `RESPRO_WEB_API_TOKEN` — bearer token for protected endpoints
- `RESPRO_WEB_JOB_TIMEOUT` — default RQ job timeout in seconds

When `RESPRO_WEB_API_TOKEN` is set, the frontend must send it as bearer token.
For browser-opened report links, token can also be passed as `?token=...` query parameter.

### 4. Start worker (separate terminal)

```bash
rq worker --url redis://127.0.0.1:6379/0 profiling
```

The worker subscribes to the `profiling` queue and executes FASTA/VCF jobs.

### 5. Start frontend (separate terminal)

Native frontend development expects Node `>=12.22`. The current scaffold is pinned to a Vite
version that still works on older Linux hosts; if local Node tooling is problematic, use the
Docker workflow instead.

```bash
cd web/frontend
npm install
npm run dev
```

Frontend dev server runs on `http://127.0.0.1:5173`.

## Option 2: Self-hosted local runtime with Docker

### 1. Build image

```bash
docker build -f Dockerfile.web -t respro-web:prototype .
```

### 2. Run container

```bash
docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/data:/data" \
  -e RESPRO_WEB_HOST=0.0.0.0 \
  -e RESPRO_WEB_PORT=8000 \
  respro-web:prototype
```

Note: this single-container approach runs the web API but has no Redis or worker. Use Docker
Compose for a complete setup.

### 3. Open app

Open `http://127.0.0.1:8000/app`.

Mount `./data` to `/data` and place `project.db` inside it. The backend reads `project.db` from
`/data/project.db` by default and creates `results.db`, reports, and uploads in the same
directory. No additional environment variables are required for a standard deployment.

## Docker Compose shortcut

```bash
docker compose -f docker-compose.web.yml up --build
```

This starts the API, an RQ worker, and Redis together on `http://127.0.0.1:8000`.

## Operational notes

- Keep service bound to localhost unless authentication and hardening are implemented.
- The web backend is asynchronous: profiling requests are enqueued and polled by the frontend.
- Redis is used as the job broker; its data is ephemeral and does not need to be persisted.
- The `REDIS_URL` environment variable configures the connection for both the API server and
  the RQ worker.
- Startup path configuration is enforced at backend startup; invalid project DB paths fail fast.
- `results.db` is initialized automatically at startup.
