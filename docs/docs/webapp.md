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

## Configuration reference

All webapp settings are optional environment variables. Set them in a `.env` file next to your `docker-compose.web.yml`, or inline under `services.respro-web.environment` (and `services.respro-worker.environment` where noted). Defaults are tuned for local development — review every variable before public hosting.

### Server and binding

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_HOST` | `127.0.0.1` | Network interface the API binds to. Use `0.0.0.0` to listen on all interfaces (required when behind a reverse proxy in Docker). |
| `RESPRO_WEB_PORT` | `8000` | TCP port the API listens on. |

!!! warning "Public bind requires a token"
    Binding to anything other than `127.0.0.1`, `localhost`, or `0.0.0.0` without setting `RESPRO_WEB_API_TOKEN` fails fast at startup. `0.0.0.0` is allowed without a token only because it is the standard Docker bind address — combine it with a reverse proxy and a token for public hosting.

### Data and filesystem

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_DATA_DIR` | `/data` if it exists, otherwise `./data` | Root directory for `project_databases/`, `uploads/`, and `results/`. Created if missing. Must be writable. |
| `RESPRO_WEB_ALLOWED_ROOTS` | the three subdirectories of `RESPRO_WEB_DATA_DIR` | Comma-separated list of absolute paths the API is allowed to read from and write to. Override only if you mount upload or result directories outside the data root. |
| `RESPRO_WEB_RESULT_TTL` | `86400` (24 hours) | Time-to-live in seconds for files in `uploads/` and `results/`. A background sweep thread deletes files older than this value. |

### Authentication and security

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_API_TOKEN` | *(empty — auth disabled)* | Bearer token required for all protected API endpoints. Leave empty for local-only deployments; **set a strong random secret for any non-local deployment**. |
| `RESPRO_WEB_CORS_ORIGINS` | `http://127.0.0.1:5173`, `http://localhost:5173` | Comma-separated list of allowed origins for cross-origin requests. Must be set explicitly when `RESPRO_WEB_API_TOKEN` is set. For public hosting, list your exact frontend origin(s), e.g. `https://respro.example.com`. |
| `RESPRO_WEB_TRUSTED_PROXIES` | *(empty — proxy headers ignored)* | Comma-separated list of proxy IPs or CIDRs whose `X-Forwarded-*` headers are trusted. Set to your reverse proxy address for public hosting (see [nginx step-by-step](#nginx-step-by-step-local-network)). |
| `RESPRO_WEB_IMPRESSUM_PATH` | *(empty — feature disabled)* | Absolute path to an HTML file served at `/legal` as a legal notice / Impressum. See [Legal notice / Impressum](#legal-notice-impressum-optional) for details. |

### Rate limiting and batch sizes

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_UPLOAD_RATE_LIMIT` | `25/minute` | slowapi rate-limit string for upload endpoints. Applied per client identity (token hash, or client IP when no token is configured). |
| `RESPRO_WEB_MAX_BATCH_SIZE` | `25` | Maximum number of samples accepted in a single batch profiling request. Must be `> 0`. |

### Redis and job queue

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis connection URL used by the API, the RQ worker, and the rate limiter. **Set identically on `respro-web` and `respro-worker`.** |
| `RESPRO_WEB_JOB_TIMEOUT` | `3600` (1 hour) | Maximum runtime in seconds for a single profiling job before RQ marks it failed. Must be `>= 0`. |
| `RESPRO_WEB_JOB_RETRY_MAX` | `0` (no retries) | Maximum number of automatic retries for a failed job. Must be `>= 0`. |
| `RESPRO_WEB_JOB_RETRY_INTERVALS` | `30` | Comma-separated delay in seconds between retries. Applied cyclically when `RESPRO_WEB_JOB_RETRY_MAX` is greater than the number of intervals. All values must be `> 0`. |

!!! note "Worker reads queue settings too"
    `REDIS_URL`, `RESPRO_WEB_JOB_TIMEOUT`, `RESPRO_WEB_JOB_RETRY_MAX`, and `RESPRO_WEB_JOB_RETRY_INTERVALS` must be set on the `respro-worker` service as well as `respro-web` so the worker and the API agree on timeouts and retries.

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

# Auth and security — required for public hosting
RESPRO_WEB_API_TOKEN=replace-with-a-long-random-secret
RESPRO_WEB_CORS_ORIGINS=https://respro.example.com
RESPRO_WEB_TRUSTED_PROXIES=127.0.0.1

# Redis (set on both respro-web and respro-worker)
REDIS_URL=redis://redis:6379/0

# Optional: legal notice
# RESPRO_WEB_IMPRESSUM_PATH=/data/impressum.html

# Optional: enable maintained database auto-download and weekly updates
RESPRO_WEB_MAINTAINED_BOOTSTRAP=true
```

## Public hosting setup

For internet-facing deployment, keep `respro-web` reachable only through a reverse proxy and TLS.

1. Keep the app behind a reverse proxy (Caddy/nginx/Traefik).
2. Terminate TLS at the proxy (HTTPS required).
3. Set a strong `RESPRO_WEB_API_TOKEN`.
4. Set explicit `RESPRO_WEB_CORS_ORIGINS` for your frontend domain(s).
5. Configure `RESPRO_WEB_TRUSTED_PROXIES` only for known proxy IPs/CIDRs.
6. Optionally enable a legal notice (Impressum) — see below.

### Legal notice / Impressum (optional)

For public hosting in jurisdictions that require a legal notice (e.g. a DSGVO/§5 TMG
Impressum in Germany), ResistanceProfiler can serve a deployment-specific HTML page at
`/legal` and surface a "Legal notice" link in the app footer.

The feature is **off by default**. The repo ships with no impressum content — each hoster
provides their own and keeps it out of version control.

Enable it by pointing `RESPRO_WEB_IMPRESSUM_PATH` at an HTML file and mounting it into the
container:

```yaml
services:
  respro-web:
    environment:
      - RESPRO_WEB_IMPRESSUM_PATH=/data/impressum.html
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

- The `/legal` route is **public** (no API token required). This is intentional: a legal
  notice must be reachable without barriers for DSGVO compliance.
- The file is read once at startup. Editing it requires a container restart.
- If `RESPRO_WEB_IMPRESSUM_PATH` is set but the file is missing or unreadable, startup
  fails fast with a clear error. Leaving the variable unset silently disables the feature.
- The "Legal notice" link in the footer only appears when an impressum is configured.

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

6. **Configure backend trust and auth settings.**

Set in `.env`:

```bash
RESPRO_WEB_API_TOKEN=replace-with-a-long-random-secret
RESPRO_WEB_CORS_ORIGINS=http://respro.internal
RESPRO_WEB_TRUSTED_PROXIES=127.0.0.1
```

If clients will access the app by IP instead of hostname, set `RESPRO_WEB_CORS_ORIGINS` to that exact origin, for example `http://192.168.1.50`.

Then restart the stack:

```bash
docker compose -f docker-compose.web.yml up -d
```

7. **Validate end-to-end from another client on the same network.**
