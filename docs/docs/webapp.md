---
title: Web App
description: Run and host the ResPro web application
---

# Web App: Run and Host

This guide covers local development and public hosting of the ResPro web application.

The stack is intentionally minimal for local development:

- `redis`
- `respro-web` (FastAPI + bundled frontend)
- `respro-worker` (RQ worker)

## App layout and navigation

The web app is a single-page application with URL-based routing. The top bar
spans the full width and carries the primary navigation; contextual sidebars
appear below it only on workspace pages.

| Route | Page | What it is for |
|---|---|---|
| `/` | **Home** | Landing page: launch analysis, explore databases, database stats. |
| `/analysis` | **Analysis** | Submit VCF/FASTA/batch jobs and run them. |
| `/analysis/reports` | **Analysis → Reports** | Session results, downloads, and the comparison heatmap. |
| `/databases` | **Databases → Dashboard** | Interactive summary of the selected database. |
| `/databases/mutations` | **Databases → Browse Mutations** | Search and filter single and combination rules; TSV export. |
| `/databases/compare` | **Databases → Compare Databases** | Compare rule sets between databases. |
| `/about` | **About** | Scope, rule nomenclature, algorithms, CLI usage, governance. |

Deep links work: every route is served directly (the backend falls back to
`index.html` for unknown non-API paths). A first-visit coach card on
`/analysis` offers a short guided tour; it can also be started any time from
the `?` button in the top bar.

## Standard local development

1. Prepare the mounted data directory:

```bash
mkdir -p data
```

2. Start the stack:

```bash
docker compose -f docker-compose.web.yml up --build
```

3. Open the app and API checks:

- App: `http://127.0.0.1:8000/`
- Health: `http://127.0.0.1:8000/api/health`
- Readiness: `http://127.0.0.1:8000/api/readiness`

4. Optional detached mode:

```bash
docker compose -f docker-compose.web.yml up -d --build
```

5. Stop the stack:

```bash
docker compose -f docker-compose.web.yml down
```

The backend creates and uses these folders inside the mounted data root:

- `data/project_databases/`
- `data/uploads/`
- `data/results/`

If `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true`, missing maintained databases are downloaded into `data/project_databases/` at startup. The flag also triggers a checksum-based update check: each existing maintained database is compared against the companion manifest's `tsv_checksum`, and a changed database is rebuilt into a temp file and atomically swapped in. A weekly background thread re-runs the same check. Set `RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS` to control the interval (default `604800` seconds = 7 days; `0` disables the weekly thread). Update failures are logged and never block startup.

### Local frontend dev server (Vite `/api` proxy)

For UI development against a local backend, run the Vite dev server (`Node.js 24+`):

```bash
cd web/frontend
npm ci
npm run dev
```

`web/frontend/vite.config.js` listens on `http://127.0.0.1:5173` and proxies `/api` and `/legal` same-origin to `http://127.0.0.1:8000` (override with `RESPRO_API_PROXY_TARGET` or `VITE_API_PROXY_TARGET`), while `web/frontend/src/config.js` defaults `apiBase` to `''`.

!!! warning "Separate-origin browser sessions are unsupported"
    Do not point `VITE_RESPRO_API_BASE` at a different origin for browser sessions. Session cookies (`respro_session`) are same-origin and backend CORS disables credentials (`allow_credentials=False`); always use same-origin hosting or the Vite `/api` dev proxy.

### Running locally without Docker

Install the `respro` package/CLI entrypoint (`pyproject.toml`) and the web backend dependencies, start Redis (default `REDIS_URL=redis://127.0.0.1:6379/0`), ensure at least one `.db` file is in `data/project_databases/` (or set `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true`), and start both the API server and the RQ worker:

```bash
pip install -e .
pip install -r web/backend/requirements.txt

# Start Redis (e.g. redis-server)
redis-server

# Terminal 1 — FastAPI API server
python -m web.backend.main

# Terminal 2 — RQ worker (always use `python -m web.backend.worker`, NOT bare `rq worker`)
python -m web.backend.worker
```

## Configuration reference

All webapp settings are optional environment variables. Set them in a `.env` file next to your `docker-compose.web.yml`, or inline under `services.respro-web.environment` (and `services.respro-worker.environment` where noted). Defaults are tuned for local development — review every variable before public hosting.

### Server and binding

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_HOST` | `127.0.0.1` | Network interface the API binds to. Use `0.0.0.0` to listen on all interfaces (required when behind a reverse proxy in Docker). |
| `RESPRO_WEB_PORT` | `8000` | TCP port the API listens on. |

!!! warning "Public hosting requires a reverse proxy and `online` mode"
    `respro-web` has no built-in application-level token authentication. Leave `127.0.0.1:8000:8000` bound to loopback on the host (`RESPRO_WEB_HOST=0.0.0.0` inside the container), place a TLS-terminating reverse proxy (nginx/Caddy/Traefik) in front, configure access control (e.g. HTTP Basic Auth or SSO) at the proxy if needed, and set `RESPRO_WEB_DEPLOYMENT_MODE=online` with explicit `RESPRO_WEB_TRUSTED_PROXIES` and `RESPRO_WEB_CORS_ORIGINS`.

### Data and filesystem

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_DATA_DIR` | `/data` if it exists, otherwise `./data` | Root directory for `project_databases/`, `uploads/`, and `results/`. Created if missing. Must be writable. |
| `RESPRO_WEB_ALLOWED_ROOTS` | the three subdirectories of `RESPRO_WEB_DATA_DIR` | Comma-separated list of absolute paths the API is allowed to read from and write to. Override only if you mount upload or result directories outside the data root. |
| `RESPRO_WEB_RESULT_TTL` | `86400` (24 hours) | Time-to-live in seconds (integer `>= 1`) for files in `uploads/` and `results/` **and** for RQ job/artifact records in Redis (`ttl`, `result_ttl`, `failure_ttl`). A background sweep thread deletes expired files (falling back to `86400` with a warning if invalid). |
| `RESPRO_WEB_SESSION_TTL` | `604800` (7 days) | Time-to-live in seconds (integer `>= 1`) for anonymous session records in Redis. Sessions provide the ownership boundary for uploads, jobs, and artifacts, and their Redis TTL slides on valid requests (`local` mode falls back to in-memory storage if Redis is unreachable; `online` mode requires Redis). |

### Authentication and security

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_CORS_ORIGINS` | `http://127.0.0.1:5173`, `http://localhost:5173` | Comma-separated list of allowed origins. In `online` mode, wildcard `*` is rejected at startup and leaving this unset logs a warning. Separate-origin browser sessions are **unsupported** (`allow_credentials=False`); serve the SPA same-origin or use the Vite `/api` dev proxy. For public hosting, set your exact frontend origin, e.g. `https://respro.example.com`. |
| `RESPRO_WEB_TRUSTED_PROXIES` | *(empty — proxy headers ignored)* | Comma-separated list of proxy IPs or canonical CIDRs (no host bits, e.g. `172.16.0.0/12`, not `172.18.0.1/16`) whose `X-Forwarded-*` headers are trusted. **Required** in `online` mode (`*` and `/0` CIDRs are rejected). When `respro-web` runs in Docker behind a host reverse proxy, specify the Docker Compose bridge gateway IP or canonical bridge subnet — **not** `127.0.0.1` (see [nginx step-by-step](#nginx-step-by-step-local-network)). |
| `RESPRO_WEB_DEPLOYMENT_MODE` | `local` | Trust model: `local` (default; zero-config, API docs enabled, loopback) or `online` (behind a reverse proxy; requires `RESPRO_WEB_TRUSTED_PROXIES`, requires Redis or returns HTTP `503`, enforces `Origin`/`Sec-Fetch-Site` checks on mutating requests, disables API docs, and marks session cookies `Secure` for HTTPS). |

Authentication (such as HTTP Basic Auth or SSO) is enforced at the reverse proxy layer rather than via an application API token.

### Footer variables (optional)

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_IMPRINT` | *(empty — feature disabled)* | Legal notice / Impressum. Accepts either an absolute `http(s)://` URL (the footer links there and `/legal` redirects to it) or a local file path to an HTML file served at `/legal`. See [Legal notice / Impressum](#legal-notice-impressum-optional) for details. |
| `RESPRO_WEB_CONTACT_EMAIL` | *(empty — feature disabled)* | Contact e-mail address surfaced as a `mailto:` link in the app footer and on the About page. Must be a single valid e-mail address; an invalid value fails fast at startup. |

### Rate limiting and batch sizes

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_UPLOAD_RATE_LIMIT` | `25/minute` | slowapi rate-limit string for upload endpoints. Applied per client IP (derived from `X-Forwarded-For` when `RESPRO_WEB_TRUSTED_PROXIES` trusts the proxy). |
| `RESPRO_WEB_API_RATE_LIMIT` | `120/minute` | slowapi rate-limit string for general (non-upload) API endpoints. Applied per client IP. |
| `RESPRO_WEB_MAX_BATCH_SIZE` | `25` | Maximum number of samples accepted in a single batch profiling request and per-minute sample quota across single, batch, and regenerate submissions (returns HTTP `429` with `Retry-After`). Must be `> 0`. |

### Redis and job queue

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis connection URL used by the API, the RQ worker, and the rate limiter. **Set identically on `respro-web` and `respro-worker`.** |
| `RESPRO_WEB_JOB_TIMEOUT` | `3600` (1 hour) | Maximum runtime in seconds for a single profiling job before RQ marks it failed. Must be `>= 0`. |
| `RESPRO_WEB_JOB_RETRY_MAX` | `0` (no retries) | Maximum number of automatic retries for a failed job. Must be `>= 0`. |
| `RESPRO_WEB_JOB_RETRY_INTERVALS` | `30` | Comma-separated delay in seconds between retries. Applied cyclically when `RESPRO_WEB_JOB_RETRY_MAX` is greater than the number of intervals. All values must be `> 0`. |

!!! note "Worker reads queue settings too"
    `REDIS_URL`, `RESPRO_WEB_RESULT_TTL`, `RESPRO_WEB_JOB_TIMEOUT`, `RESPRO_WEB_JOB_RETRY_MAX`, and `RESPRO_WEB_JOB_RETRY_INTERVALS` must be set identically on `respro-worker` and `respro-web` so the worker and the API agree on timeouts, TTLs, and retries. Always start the worker via `python -m web.backend.worker` (never bare `rq worker`, which defaults to pickle instead of `JSONSerializer`); it drains both the single-profile and batch queues (`profile_queue_name` and `batch_queue_name`, both defaulting to `profiling` in `web/backend/defaults.toml`).

### Maintained databases

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_MAINTAINED_BOOTSTRAP` | `false` | When `true` (or `1`/`yes`/`on`), missing maintained databases are downloaded into `data/project_databases/` at startup, and a weekly background thread checks for updates. |
| `RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS` | `604800` (7 days) | Interval between maintained-database update checks. Set to `0` to disable the weekly thread (a one-time check still runs at startup when bootstrap is enabled). Invalid values fall back to the default. |

### Minimal `.env` example

For local development you can usually leave everything at defaults. For a public deployment, start from:

```bash
# Binding
RESPRO_WEB_HOST=0.0.0.0
RESPRO_WEB_PORT=8000

# Origin, proxy trust, and deployment mode — required for public hosting
RESPRO_WEB_CORS_ORIGINS=https://respro.example.com
# Behind a host reverse proxy into Docker, use the Compose bridge gateway IP
# or a canonical bridge subnet (not bare 127.0.0.1); verify with `docker network inspect`.
RESPRO_WEB_TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12
RESPRO_WEB_DEPLOYMENT_MODE=online

# Redis (set on both respro-web and respro-worker)
REDIS_URL=redis://redis:6379/0

# Optional: legal notice (URL or local HTML file path)
# RESPRO_WEB_IMPRINT=https://example.org/impressum
# RESPRO_WEB_IMPRINT=/data/impressum.html

# Optional: contact e-mail shown in the footer and on the About page
# RESPRO_WEB_CONTACT_EMAIL=contact@example.org

# Optional: enable maintained database auto-download and weekly updates
RESPRO_WEB_MAINTAINED_BOOTSTRAP=true
```

## Public hosting setup

For internet-facing deployment, keep `respro-web` reachable only through a reverse proxy and TLS.

1. Keep the app behind a reverse proxy (Caddy/nginx/Traefik) and bind `respro-web` only to `127.0.0.1:8000:8000` on the host.
2. Terminate TLS at the proxy (HTTPS required).
3. Set `RESPRO_WEB_DEPLOYMENT_MODE=online` and enforce any user authentication (e.g. nginx `auth_basic` or SSO) at the reverse proxy.
4. Set explicit `RESPRO_WEB_CORS_ORIGINS` for your public origin (wildcard `*` is rejected in `online` mode; separate-origin browser sessions are unsupported).
5. Configure `RESPRO_WEB_TRUSTED_PROXIES` with your proxy's Docker bridge gateway IP or canonical bridge subnet (not bare `127.0.0.1`; `*` and `/0` are rejected in `online` mode).
6. Optionally enable a legal notice (Impressum) — see below.

### Legal notice / Impressum (optional)

For public hosting in jurisdictions that require a legal notice (e.g. a DSGVO/§5 TMG
Impressum in Germany), ResistanceProfiler can serve a deployment-specific legal notice
and surface a "Legal notice" link in the app footer.

The feature is **off by default**. The repo ships with no impressum content — each hoster
provides their own and keeps it out of version control.

Set `RESPRO_WEB_IMPRINT` to either:

- an absolute `http(s)://` URL of an already-hosted impressum page — the footer links
  straight there and `/legal` redirects to it; or
- a local file path to an HTML file that the app serves at `/legal`.

Local file example — mount the file and point at it:

```yaml
services:
  respro-web:
    environment:
      - RESPRO_WEB_IMPRINT=/data/impressum.html
    volumes:
      - ./data:/data:rw
      - ./impressum.html:/data/impressum.html:ro
```

Then create `impressum.html` on the host next to your `docker-compose.web.yml`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Legal notice</title>
</head>
<body>
    <h1>Legal notice / Impressum</h1>
    <!-- Add the content required by your jurisdiction here, e.g. provider
         name, address, contact, and responsible person per §18 MStV. -->
</body>
</html>
```

Behaviour notes:

- The `/legal` route is **public** (exempt from session creation). If your reverse proxy
  enforces HTTP Basic Auth or SSO, explicitly allow unauthenticated access to `/legal`
  when barrier-free legal-notice access is required for DSGVO compliance.
- A local file is read once at startup. Editing it requires a container restart.
- If `RESPRO_WEB_IMPRINT` points at a missing or unreadable file, startup fails fast with
  a clear error. Leaving the variable unset silently disables the feature.
- The "Legal notice" link in the footer only appears when an imprint is configured.

### Caddy example

```text
respro.example.com {
    encode gzip

    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Host {host}
    }
}
```

### nginx example (HTTPS)

```nginx
server {
    listen 443 ssl http2;
    server_name respro.example.com;

    ssl_certificate /etc/letsencrypt/live/respro.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/respro.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }
}

server {
    listen 80;
    server_name respro.example.com;
    return 301 https://$host$request_uri;
}
```

### nginx step-by-step (local network)

Use this when your app is currently reachable directly on a LAN or intranet host and you want nginx in front of it.

1. **Keep `respro-web` private to the host.**

In `docker-compose.web.yml`, bind the app to loopback only so clients cannot bypass nginx:

```yaml
services:
    respro-web:
        ports:
            - '127.0.0.1:8000:8000'
```

Then restart:

```bash
docker compose -f docker-compose.web.yml up -d --build
```

2. **Install nginx on the same machine as Docker.**

Example for Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y nginx
```

3. **Choose the hostname clients should use.**

Use either:

- a fixed LAN IP such as `http://192.168.1.50/`
- an internal DNS name such as `http://respro.internal/`
- a host entry on client machines if no internal DNS is available

4. **Create an nginx site config for HTTP on the local network.**

Create `/etc/nginx/sites-available/respro`:

```nginx
server {
    listen 80;
    server_name respro.internal;

    client_max_body_size 1024m;
    proxy_read_timeout 3600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }
}
```

!!! note
    Replace `respro.internal` with the hostname or IP that clients will use. `client_max_body_size` is important for larger uploads. `proxy_read_timeout` avoids premature timeouts for longer profiling requests.

5. **Enable the site and reload nginx.**

```bash
sudo ln -s /etc/nginx/sites-available/respro /etc/nginx/sites-enabled/respro
sudo nginx -t
sudo systemctl reload nginx
```

If you use another firewall, allow inbound TCP traffic to the nginx port you selected.

6. **Configure backend trust and origin settings.**

Set in `.env` (if you want access control on the LAN proxy, configure `auth_basic` in nginx; `respro-web` does not use an application API token):

```bash
RESPRO_WEB_CORS_ORIGINS=http://respro.internal
# Host nginx proxies to Docker's 127.0.0.1:8000 port over the Compose bridge
# gateway IP (not 127.0.0.1). Inspect the exact gateway with:
#   docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' \
#     "$(docker compose -f docker-compose.web.yml ps -q respro-web)"
# or `docker network inspect <compose_network>`, or use canonical CIDRs with no
# host bits (e.g. 127.0.0.1,172.16.0.0/12).
RESPRO_WEB_TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12
```

!!! note "Keep `DEPLOYMENT_MODE=local` for plain HTTP"
    This setup uses plain HTTP on a local network, so leave `RESPRO_WEB_DEPLOYMENT_MODE` at its default (`local`). In `online` mode the session cookie is marked `Secure`, which browsers reject over plain HTTP (except `http://localhost`) — sessions would silently break.

If clients will access the app by IP instead of hostname, set `RESPRO_WEB_CORS_ORIGINS` to that exact origin, for example `http://192.168.1.50`.

Then restart the stack:

```bash
docker compose -f docker-compose.web.yml up -d
```

7. **Validate end-to-end from another client on the same network.**
