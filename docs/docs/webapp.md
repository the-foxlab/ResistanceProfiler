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

!!! warning "At least one project database is required to start"
    The web app validates on startup that `data/project_databases/` contains at least one `.db` file. If it's empty, `create_app()` raises `FileNotFoundError` before uvicorn binds the port — the container crash-loops and `/api/health` never responds. Ship a `.db` file into the mounted directory before first boot, or set `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true` to have it auto-downloaded at startup (requires internet access).

If `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true`, missing maintained databases are downloaded into `data/project_databases/` at startup. The flag also triggers a checksum-based update check: each existing maintained database is compared against the companion manifest's `tsv_checksum`, and a changed database is rebuilt into a temp file and atomically swapped in. A weekly background thread re-runs the same check. Set `RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS` to control the interval (default `604800` seconds = 7 days; `0` disables the weekly thread). Update failures are logged and never block startup.

## Network access requirements

The web app itself runs entirely offline once a project database is present —
profiling, report generation, and result delivery need **no outbound network
access**. External endpoints are only contacted during database build/bootstrap,
never during a running analysis. The report HTML contains `https://` links to
PubChem, PubMed, and DOI resolvers, but those are rendered as clickable links
in the user's browser, not fetched server-side.

### Endpoints contacted at build/bootstrap time only

These are reached by `respro init` / `respro maintained` / the
`RESPRO_WEB_MAINTAINED_BOOTSTRAP=true` startup path. An offline server that
ships pre-built `.db` files does **not** need access to any of them.

| Phase | Host | Purpose |
|---|---|---|
| Maintained-DB bootstrap & updates | `raw.githubusercontent.com` | Fetch `manifest.json`, `rules.tsv`, `metadata.json`, `formula-rules.tsv` from `the-foxlab/respro-databases` |
| Maintained-DB bootstrap | `eutils.ncbi.nlm.nih.gov` | Fetch GenBank reference records (`efetch.fcgi?db=nuccore`) referenced by a database's rules |
| `respro init` enrichment | `pubchem.ncbi.nlm.nih.gov` | Resolve drug names to CIDs, descriptions, titles, structure images (PUG REST) |
| `respro init` enrichment | `eutils.ncbi.nlm.nih.gov` | PubMed article summaries (`esummary.fcgi`), PMC ID conversion |
| `respro init` enrichment | `api.crossref.org` | DOI → publication metadata |
| `respro init` enrichment | `www.ncbi.nlm.nih.gov` | Protein page links (feature annotation) |

### Endpoints the running app never contacts

The web/worker containers do not make outbound calls during profiling or report
generation. All drug metadata, publication data, and GenBank records are baked
into the `.db` file at build time. The report HTML links out to these hosts in
the **client browser** (the user's machine, not the server):

- `pubchem.ncbi.nlm.nih.gov` — drug compound pages and 2D structure images
- `pubmed.ncbi.nlm.nih.gov` — publication pages
- `doi.org` — DOI resolution

### Docker image retrieval

The CI-published image lives at `ghcr.io/the-foxlab/resistanceprofiler`. To pull
it directly on a server (skipping the build/scp workflow), the server needs
egress to:

| Host | Port | Purpose |
|---|---|---|
| `ghcr.io` | `443` | Pull the ResPro web/worker image |
| `registry-1.docker.io` | `443` | Pull `redis:7-alpine` (or your chosen Redis image) |

If the GHCR package is private, also allow authenticated pulls via
`docker login ghcr.io` with a PAT that has `read:packages`.

### Firewall summary for an online server

A server that pulls images directly and runs `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true`
needs outbound HTTPS to: `ghcr.io`, `registry-1.docker.io`,
`raw.githubusercontent.com`, `eutils.ncbi.nlm.nih.gov`, `pubchem.ncbi.nlm.nih.gov`,
`www.ncbi.nlm.nih.gov`, `api.crossref.org`. A server that ships pre-built images
and `.db` files needs **no** outbound access at all.

## Configuration reference

All webapp settings are optional environment variables. Set them in a `.env` file next to your `docker-compose.web.yml`, or inline under `services.respro-web.environment` (and `services.respro-worker.environment` where noted). Defaults are tuned for local development — review every variable before public hosting.

### Server and binding

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_HOST` | `127.0.0.1` | Network interface the API binds to. Use `0.0.0.0` to listen on all interfaces (required when behind a reverse proxy in Docker). |
| `RESPRO_WEB_PORT` | `8000` | TCP port the API listens on. |

### Deployment modes

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_DEPLOYMENT_MODE` | `local` | Controls the startup policy and security defaults. `local` (default): zero-config, no proxy required, API docs enabled, `Secure` omitted from the session cookie (HTTP on loopback). `online`: requires `RESPRO_WEB_TRUSTED_PROXIES`; API docs disabled; `Secure` set on the session cookie. Any other value fails fast at startup with a clear `RuntimeError`. |

Startup fails with a clear `RuntimeError` if the selected mode's requirements are not met. `/docs`, `/redoc`, and `/openapi.json` return 404 in `online` mode.

### Data and filesystem

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_DATA_DIR` | `/data` if it exists, otherwise `./data` | Root directory for `project_databases/`, `uploads/`, and `results/`. Created if missing. Must be writable. |
| `RESPRO_WEB_ALLOWED_ROOTS` | the three subdirectories of `RESPRO_WEB_DATA_DIR` | Comma-separated list of absolute paths the API is allowed to read from and write to. Override only if you mount upload or result directories outside the data root. |
| `RESPRO_WEB_RESULT_TTL` | `86400` (24 hours) | Time-to-live in seconds for files in `uploads/` and `results/`. A background sweep thread deletes files older than this value. |
| `RESPRO_WEB_SESSION_TTL` | `604800` (7 days) | Time-to-live in seconds for session records in Redis and the session cookie `Max-Age`. |

### Security and browser origin

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_CORS_ORIGINS` | `http://127.0.0.1:5173`, `http://localhost:5173` | Comma-separated list of allowed origins for cross-origin requests. For public hosting, list your exact frontend origin(s), e.g. `https://respro.example.com`. |
| `RESPRO_WEB_TRUSTED_PROXIES` | *(empty — proxy headers ignored)* | Comma-separated list of proxy IPs or CIDRs whose `X-Forwarded-*` headers are trusted. **Required in `online` mode.** Set to your reverse proxy address for public hosting (see [nginx step-by-step](#nginx-step-by-step-local-network)). |
| `RESPRO_WEB_IMPRINT` | *(empty — feature disabled)* | Imprint / legal notice. Accepts either an absolute `http://` / `https://` URL pointing at an already-hosted imprint page (the footer links straight to it) **or** an absolute path to a local HTML file served at `/legal`. See [Legal notice / imprint](#legal-notice-imprint-optional) for details. |

### Session-ownership model

ResPro issues an opaque session cookie (`respro_session`) on the first request. The cookie is `HttpOnly` and `SameSite=Lax`; the `Secure` attribute is set only in non-local deployment modes (behind a TLS-terminating proxy). The raw cookie value carries ≥256 bits of randomness and is never stored server-side — only its SHA-256 hash is persisted in Redis (or an in-memory fallback when Redis is unavailable in local mode).

Every upload, queued job, and output artifact is recorded against the owning session:

- `upload:<id>` → owner session hash, canonical path, file type, expiry
- `job:<id>` → owner session hash, input upload IDs, status, expiry
- `artifact:<id>` → owner session hash, canonical path, media type, expiry

API responses return opaque IDs (`upload_id`, `artifact_id`) instead of absolute filesystem paths. Profile, regenerate, compare, artifact, report, and cleanup routes resolve these IDs server-side to validated, path-confined files. Ownership is enforced on every access: a request from session B for a job/artifact created under session A returns **404** (not 403, to avoid confirming existence to non-owners).

**Browser authentication.** ResPro does not ship its own login screen. For institutional deployments, **whole-origin proxy authentication** (Nginx `auth_basic` / SSO) is the browser-auth model — it is configured at the reverse proxy and can be added or removed with one line during alpha testing. The session cookie protects per-user data isolation within a shared deployment.

| Model | Use case | Mechanism |
|---|---|---|
| **Session cookie** | Multi-user browser access to a shared deployment | Opaque `respro_session` cookie, ownership-enforced ID resolution |
| **Proxy auth (Nginx/SSO)** | Institutional deployments | Whole-origin authentication at the reverse proxy (optional, additive layer) |

### Rate limiting and batch sizes

| Variable | Default | Description |
|---|---|---|
| `RESPRO_WEB_UPLOAD_RATE_LIMIT` | `25/minute` | slowapi rate-limit string for upload endpoints. Applied per client IP. |
| `RESPRO_WEB_API_RATE_LIMIT` | `120/minute` | slowapi rate-limit string for non-upload API routes (job status, profile, regenerate, artifact, catalog, session cleanup). Applied per client IP. Tighten for public hosting to resist brute-force/scraping. |
| `RESPRO_WEB_MAX_BATCH_SIZE` | `25` | Maximum number of samples accepted in a single batch profiling request. Must be `> 0`. |

In batch VCF mode, an optional BAM can be attached to each sample for per-sample coverage-gap analysis. BAMs are auto-paired to VCFs by filename stem on multi-select upload (e.g. `sample1.vcf` ↔ `sample1.bam`), and any pairing can be overridden per row. A sample without a BAM skips coverage analysis, matching single-VCF behaviour without `--bam`.

### Redis and job queue

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis connection URL used by the API, the RQ worker, and the rate limiter. **Set identically on `respro-web` and `respro-worker`.** Include the password: `redis://:<password>@host:6379/0`. |
| `RESPRO_REDIS_PASSWORD` | `change-me-local-only` | Password required by the Redis service (`--requirepass`). The reference compose file uses this in both the Redis `command` and the `REDIS_URL` on both app services. **Set a strong secret for any non-loopback deployment.** |
| `RESPRO_WEB_JOB_TIMEOUT` | `3600` (1 hour) | Maximum runtime in seconds for a single profiling job before RQ marks it failed. Must be `>= 0`. |
| `RESPRO_WEB_JOB_RETRY_MAX` | `0` (no retries) | Maximum number of automatic retries for a failed job. Must be `>= 0`. |
| `RESPRO_WEB_JOB_RETRY_INTERVALS` | `30` | Comma-separated delay in seconds between retries. Applied cyclically when `RESPRO_WEB_JOB_RETRY_MAX` is greater than the number of intervals. All values must be `> 0`. |

!!! warning "Redis is an execution trust boundary"
    RQ's default serializer uses `pickle.dumps`/`pickle.loads`, so anything able to **write** to Redis can achieve worker code execution via a crafted pickle payload. The reference `docker-compose.web.yml` therefore requires a Redis password by default (`RESPRO_REDIS_PASSWORD`). Both the app (`web/backend/queue.py`) and the worker (`web/backend/worker.py`, started via `python -m web.backend.worker`) use `rq.serializers.JSONSerializer` instead of the pickle-based default — **both sides must agree on the serializer**, otherwise every job fails with `DeserializationError: invalid load key, '['`. Job arguments in this codebase are primitive strings/numbers/lists/dicts, which JSON round-trips losslessly. For off-host Redis, additionally:

    - **ACL-based least privilege**: create a Redis ACL user with access limited to the keys RQ uses (`respro:*`, `rq:*`) rather than the default full-access user.
    - **TLS**: enable Redis TLS (`--tls-port`, `--tls-cert-file`, `--tls-key-file`) and set `REDIS_URL` to `rediss://...` so the password and job payloads are encrypted in transit.
    - **No public port**: Redis must never be published on a public interface (the compose file does not publish it).

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

# Deployment mode and browser origin — required for public hosting
RESPRO_WEB_DEPLOYMENT_MODE=online
RESPRO_WEB_CORS_ORIGINS=https://respro.example.com
RESPRO_WEB_TRUSTED_PROXIES=127.0.0.1

# Redis (set on both respro-web and respro-worker; password must match the redis service)
RESPRO_REDIS_PASSWORD=replace-with-a-long-random-secret
REDIS_URL=redis://:${RESPRO_REDIS_PASSWORD}@redis:6379/0

# Optional: legal notice / imprint
# Either an absolute URL to an already-hosted imprint page:
# RESPRO_WEB_IMPRINT=https://example.org/impressum
# …or an absolute path to a local HTML file served at /legal:
# RESPRO_WEB_IMPRINT=/data/imprint.html

# Optional: enable maintained database auto-download and weekly updates
RESPRO_WEB_MAINTAINED_BOOTSTRAP=true
```

## Public hosting setup

For internet-facing deployment, keep `respro-web` reachable only through a reverse proxy and TLS.

1. Keep the app behind a reverse proxy (Caddy/nginx/Traefik).
2. Terminate TLS at the proxy (HTTPS required).
3. Set `RESPRO_WEB_DEPLOYMENT_MODE=online`.
4. Set explicit `RESPRO_WEB_CORS_ORIGINS` for your frontend domain(s).
5. Configure `RESPRO_WEB_TRUSTED_PROXIES` for your proxy IP/CIDR.
6. Optionally enable browser auth at the proxy (nginx `auth_basic` / SSO) — an additive layer you can add or remove with one line.
7. Optionally enable a legal notice / imprint — see below.

### Additive security layers

The two deployment routes (`local`, `online`) set the baseline. The layers below are **optional and additive** — each is independent, configured at the proxy or via an env var, and can be added or removed without touching the others.

| Layer | What it does | When to set | How |
|---|---|---|---|
| **Browser auth** | Gates the whole origin behind a login prompt | Institutional / public hosting during alpha | nginx `auth_basic` / SSO at the reverse proxy (one `auth_basic` directive to add/remove) |
| **CORS** | Restricts which origins may call the API from a browser | Only if frontend and API are on different origins | `RESPRO_WEB_CORS_ORIGINS=https://respro.example.com` |
| **Rate limits** | Caps upload/API request frequency per client IP | Public hosting; tune for your load | `RESPRO_WEB_UPLOAD_RATE_LIMIT`, `RESPRO_WEB_API_RATE_LIMIT` |
| **Imprint** | Shows a legal-notice link in the footer | Jurisdictions that require it (e.g. DSGVO/§5 TMG) | `RESPRO_WEB_IMPRINT=<url-or-path>` |

### Legal notice / imprint (optional)

For public hosting in jurisdictions that require a legal notice (e.g. a DSGVO/§5 TMG
Impressum in Germany), ResistanceProfiler surfaces a "Legal notice" link in the app
footer. The feature supports two modes, selected automatically by the value of
`RESPRO_WEB_IMPRINT`:

| Value shape | Mode | Behaviour |
|---|---|---|
| absolute `http://` / `https://` URL | **URL mode** | The footer link points **directly** at the external imprint page (opens in a new tab). The `/legal` route 302-redirects to the same URL, so a bookmarked `/legal` link still lands on the hosted page. |
| any other non-empty value | **path mode** | The value is treated as a local file path. The HTML is read once at startup and served at `/legal`; the footer links to `/legal`. |
| unset / empty | disabled | No footer link; `/legal` returns 404. |

The feature is **off by default**. The repo ships with no imprint content — each hoster
provides their own and keeps it out of version control.

#### URL mode — link to an already-hosted imprint

If your imprint already lives on another site (e.g. your institution's central legal page),
point `RESPRO_WEB_IMPRINT` at its URL. No file needs to be mounted:

```yaml
services:
  respro-web:
    environment:
      - RESPRO_WEB_IMPRINT=https://www.example.org/impressum
```

#### Path mode — self-hosted HTML

To host the imprint HTML from the app itself, point `RESPRO_WEB_IMPRINT` at a local file
and mount it into the container:

```yaml
services:
  respro-web:
    environment:
      - RESPRO_WEB_IMPRINT=/data/imprint.html
    volumes:
      - ./data:/data:rw
      - ./imprint.html:/data/imprint.html:ro
```

Then create `imprint.html` on the host next to your `docker-compose.web.yml`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Legal notice</title>
</head>
<body>
    <h1>Legal notice / imprint</h1>
    <!-- Add the content required by your jurisdiction here, e.g. provider
         name, address, contact, and responsible person per §18 MStV. -->
</body>
</html>
```

Behaviour notes:

- The `/legal` route is **public** (no API token required). This is intentional: a legal
  notice must be reachable without barriers for DSGVO compliance.
- In path mode the file is read once at startup. Editing it requires a container restart.
- If `RESPRO_WEB_IMPRINT` is set to a path that is missing or unreadable, startup fails
  fast with a clear error. A URL with a non-`http(s)` scheme (e.g. `ftp://`) also fails
  fast. Leaving the variable unset silently disables the feature.
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

    # ── Global timeouts and connection limits ───────────────────────────
    # Bound how long nginx waits for client headers/bodies and for sending a
    # response, and limit concurrent connections per client IP to resist slowloris
    # style resource exhaustion.
    client_body_timeout   60s;   # max time to receive the request body
    client_header_timeout 30s;   # max time to receive the request headers
    send_timeout          60s;   # max time between successive writes to the client
    limit_conn            addr 10;  # max 10 concurrent connections per IP (see limit_conn_zone below)

    # Define the per-IP connection-limiting zone (place at http{} level in
    # /etc/nginx/nginx.conf, or here if your build allows server-level zones).
    # limit_conn_zone $binary_remote_addr zone=addr:10m;

    proxy_read_timeout 3600s;  # profiling jobs can run for a while

    # ── Per-location body-size limits ───────────────────────────────────
    # Small JSON API endpoints must not accept multi-hundred-MB bodies; only the
    # BAM upload route needs a very large limit. This caps abuse surface area
    # without breaking legitimate large uploads.

    # BAM uploads can legitimately be ~1 GB.
    location /api/upload/bam {
        client_max_body_size 1024m;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }

    # Other uploads (FASTA/VCF/JSON) are at most a few MB.
    location /api/upload/ {
        client_max_body_size 8m;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }

    # All other API routes accept only small JSON payloads.
    location /api/ {
        client_max_body_size 2m;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }

    # Frontend and everything else.
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
    Replace `respro.internal` with the hostname or IP that clients will use. The per-location `client_max_body_size` values are important: small JSON API endpoints should not accept multi-hundred-MB bodies, so only the BAM upload route allows up to `1024m`, other uploads are capped at `8m`, and all remaining API routes at `2m`. `proxy_read_timeout` avoids premature timeouts for longer profiling requests, while `client_body_timeout` / `client_header_timeout` / `send_timeout` / `limit_conn` harden against slow-client resource exhaustion.

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
RESPRO_WEB_DEPLOYMENT_MODE=online
RESPRO_WEB_CORS_ORIGINS=http://respro.internal
RESPRO_WEB_TRUSTED_PROXIES=127.0.0.1
```

If clients will access the app by IP instead of hostname, set `RESPRO_WEB_CORS_ORIGINS` to that exact origin, for example `http://192.168.1.50`.

Then restart the stack:

```bash
docker compose -f docker-compose.web.yml up -d
```

7. **Validate end-to-end from another client on the same network.**

## Production hosting checklist

Before exposing the app to the internet, work through every item below. The
annotated `docker-compose.web.yml` ships with all of these settings present but
`#`-commented — uncomment and edit the ones that apply to your deployment.

### Reverse proxy and TLS

- [ ] The app is reachable **only** through a reverse proxy (Caddy/nginx/Traefik).
      Bind `respro-web` to `127.0.0.1:8000` (or an internal interface) so clients
      cannot bypass the proxy.
- [ ] TLS is terminated at the proxy; HTTP redirects to HTTPS.
- [ ] `RESPRO_WEB_TRUSTED_PROXIES` is set to the proxy's IP/CIDR so the backend
      honours `X-Forwarded-*` headers only from the proxy.
- [ ] `client_max_body_size` (nginx) or equivalent is raised for larger uploads.

### Authentication

- [ ] `RESPRO_WEB_DEPLOYMENT_MODE` is set to `online` for production
      deployments behind a reverse proxy. This disables API docs and sets the
      `Secure` flag on the session cookie.
- [ ] `RESPRO_WEB_CORS_ORIGINS` lists your exact frontend origin(s), e.g.
      `https://respro.example.com`.
- [ ] For institutional multi-user deployments, whole-origin proxy authentication
      (Nginx `auth_basic` / SSO) is the recommended browser-auth model. It is an
      additive layer configured at the reverse proxy; the session cookie provides
      per-user data isolation.

### Redis

- [ ] Redis requires a password. `RESPRO_REDIS_PASSWORD` is set to a strong
      random secret (the reference compose file requires it by default).
- [ ] `REDIS_URL` on **both** `respro-web` and `respro-worker` includes the
      password: `redis://:<password>@redis:6379/0`.
- [ ] Redis is not exposed on a public port (the compose file does not publish it).
- [ ] For off-host Redis: ACL-based least-privilege access and TLS are configured
      (see [Redis and job queue](#redis-and-job-queue)).
- [ ] RQ uses `JSONSerializer` on **both** the app (`web/backend/queue.py`) and
      the worker (`web/backend/worker.py`, started via `python -m web.backend.worker`)
      so Redis cannot be used for pickle-based worker code execution. The compose
      files start the worker through this entrypoint — do not revert to the bare
      `rq worker` CLI (it defaults to pickle and causes `DeserializationError`).

### Container hardening

- [ ] The web and worker containers run as a **non-root** user. `Dockerfile.web`
      creates a dedicated `respro` user (UID/GID 1001). The container still starts
      as root via `docker/entrypoint.web.sh`, which `chown`s the bind-mounted
      `/data` volume for the current host directory, then drops privileges to
      `respro` via `gosu` before the application ever runs — a build-time `chown`
      alone is not sufficient because a bind mount replaces the image's `/data`
      directory with the host directory's own ownership at container start.
- [ ] If you pin a different UID/GID, update the `Dockerfile.web` `useradd`/`groupadd`
      lines; the entrypoint's `chown` picks up the new UID/GID automatically.
- [ ] The production compose file (`production/docker-compose.web.yml`) sets
      `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `read_only: true`,
      `tmpfs: [/tmp:noexec,nosuid]`, and `mem_limit` on every service.
- [ ] The worker's `project_databases` directory is mounted read-only (the worker
      only reads reference data). For the web service, enable the read-only
      project_databases mount only when `RESPRO_WEB_MAINTAINED_BOOTSTRAP` is
      disabled (the auto-update thread writes to that directory).

### Rate limiting

- [ ] `RESPRO_WEB_UPLOAD_RATE_LIMIT` and `RESPRO_WEB_API_RATE_LIMIT` are tuned for
      your load. Defaults (`25/minute` uploads, `120/minute` other API routes) suit
      a small team; tighten for larger or public deployments.
- [ ] `RESPRO_WEB_MAX_BATCH_SIZE` matches your expected batch sizes.

### Legal and operational

- [ ] `RESPRO_WEB_IMPRINT` is set if your jurisdiction requires a legal notice.
- [ ] `RESPRO_WEB_MAINTAINED_DB_UPDATE_INTERVAL_SECONDS` is reviewed (default 7 days).
- [ ] Result TTL (`RESPRO_WEB_RESULT_TTL`) matches your data-retention policy.
- [ ] Logs are shipped to a central collector and do not contain secrets.
