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

## Public hosting setup

For internet-facing deployment, keep `respro-web` reachable only through a reverse proxy and TLS.

1. Keep the app behind a reverse proxy (Caddy/nginx/Traefik).
2. Terminate TLS at the proxy (HTTPS required).
3. Set a strong `RESPRO_WEB_API_TOKEN`.
4. Set explicit `RESPRO_WEB_CORS_ORIGINS` for your frontend domain(s).
5. Configure `RESPRO_WEB_TRUSTED_PROXIES` only for known proxy IPs/CIDRs.

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
