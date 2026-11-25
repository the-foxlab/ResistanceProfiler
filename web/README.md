# Web UI

This folder contains the Web UI stack:

- `backend/`: FastAPI API layer that wraps existing `respro` domain logic.
- `frontend/`: React UI for profiling submission, database analytics, report viewing, and browsing endpoints.

The backend is intentionally thin and keeps CLI behavior untouched.

## Local backend runtime (without Docker)

Install both the `respro` package/CLI entrypoint (`pyproject.toml`) and the web backend dependencies:

```bash
pip install -e .
pip install -r web/backend/requirements.txt
```

Start Redis (the backend defaults to `REDIS_URL=redis://127.0.0.1:6379/0`):

```bash
redis-server
# or via Docker:
# docker run --rm -p 127.0.0.1:6379:6379 redis:7.4.10-alpine
```

Ensure at least one project `.db` exists in `data/project_databases/` (or set `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true` to download maintained databases on startup), then run the API server and the RQ worker in separate terminals:

```bash
python -m web.backend.main
```

```bash
python -m web.backend.worker
```

Always start the worker via `python -m web.backend.worker` (never bare `rq worker`, which defaults to pickle instead of `rq.serializers.JSONSerializer`); it drains both `profile_queue_name` and `batch_queue_name` (both default to `profiling` in `web/backend/defaults.toml`).

Important runtime variables (full reference in `docs/docs/webapp.md`):

- `RESPRO_WEB_RESULT_TTL` (default `86400`s, integer `>= 1`): governs both upload/result file sweeping and RQ job/artifact TTLs.
- Queue settings (`REDIS_URL`, `RESPRO_WEB_RESULT_TTL`, `RESPRO_WEB_JOB_TIMEOUT`, `RESPRO_WEB_JOB_RETRY_MAX`, `RESPRO_WEB_JOB_RETRY_INTERVALS`) must match between `web.backend.main` and `web.backend.worker`.
- `RESPRO_WEB_TRUSTED_PROXIES`: required (explicit IPs or canonical CIDRs, no `*` or `/0`) when `RESPRO_WEB_DEPLOYMENT_MODE=online`; behind a host reverse proxy into Docker, set it to the Docker Compose bridge gateway IP or canonical bridge subnet (e.g. `127.0.0.1,172.16.0.0/12`), not bare `127.0.0.1`.

## Frontend runtime & Vite `/api` dev proxy

Node.js 24+ is required to run frontend tests and build the frontend bundle.

```bash
cd web/frontend
npm ci
npm run dev
```

`web/frontend/vite.config.js` serves the UI on `http://127.0.0.1:5173` and proxies `/api` and `/legal` same-origin to `http://127.0.0.1:8000` (override with `RESPRO_API_PROXY_TARGET` or `VITE_API_PROXY_TARGET`), while `web/frontend/src/config.js` defaults `apiBase` to `''` (same-origin).

**Separate-origin browser sessions are unsupported:** do not configure `VITE_RESPRO_API_BASE` to a different origin for browser sessions. Session cookies (`respro_session`) are same-origin and backend CORS disables credentials (`allow_credentials=False`); always use same-origin hosting or the Vite `/api` dev proxy.
