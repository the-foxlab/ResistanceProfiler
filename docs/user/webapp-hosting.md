# Web App: Run and Host

This guide should help to simplify web app startup.

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

- App: `http://127.0.0.1:8000/app/`
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

If `RESPRO_WEB_MAINTAINED_BOOTSTRAP=true`, missing maintained databases are downloaded into `data/project_databases/` at startup.

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

### nginx example

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

## Environment variables

Set required local defaults directly in `docker-compose.web.yml`.
Set secrets and deployment-specific values in `.env` (loaded by Docker Compose).

### Public deployment: required variables

| Variable                    | Purpose                                                                   | Example                                                    | Where to set |
| --------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------- | ------------ |
| `RESPRO_WEB_API_TOKEN`    | Enables API authentication for protected routes in non-local deployments. | `RESPRO_WEB_API_TOKEN=replace-with-a-long-random-secret` | `.env`     |
| `RESPRO_WEB_CORS_ORIGINS` | Explicit allowlist of frontend origins when token auth is enabled.        | `RESPRO_WEB_CORS_ORIGINS=https://respro.example.com`     | `.env`     |

### Public deployment: optional variables

| Variable                            | Purpose                                                                                                         | Default/Example                                         | Where to set                    |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ------------------------------- |
| `RESPRO_WEB_TRUSTED_PROXIES`      | Trusted reverse-proxy IPs/CIDRs for forwarded headers (`X-Forwarded-*`). Set only behind a known proxy.       | `127.0.0.1` or `10.0.0.0/8`                         | `.env`                        |
| `RESPRO_WEB_UPLOAD_RATE_LIMIT`    | Upload request throttle (`slowapi` syntax).                                                                   | default `5/minute`; example `10/minute`             | `.env`                        |
| `RESPRO_WEB_JOB_TIMEOUT`          | Queue job timeout in seconds.                                                                                   | default `3600`; example `7200`                      | `.env`                        |
| `RESPRO_WEB_JOB_RETRY_MAX`        | Retry count for failed queued jobs. Set > 0 only if you want automatic retries for transient failures.        | default `1`; example `2`                            | `.env`                        |
| `RESPRO_WEB_JOB_RETRY_INTERVALS`  | Comma-separated retry delays in seconds (used only when `RESPRO_WEB_JOB_RETRY_MAX` > 0).                      | default `30`; example `30,120`                      | `.env`                        |
| `RESPRO_WEB_DATA_DIR`             | Startup data root override (normally mounted as `/data` in compose).                                          | default `/data`; example `/data`                    | `.env` (only when overriding) |
| `RESPRO_WEB_ALLOWED_ROOTS`        | Comma-separated absolute path allowlist for upload/regenerate path checks.                                      | `/data/project_databases,/data/uploads,/data/results` | `.env` (only when overriding) |
| `RESPRO_WEB_MAINTAINED_BOOTSTRAP` | Downloads missing maintained DBs at startup. Often enabled for local convenience, optional in production.       | default `false`; local compose sets `true`          | compose file or `.env`        |
| `RESPRO_WEB_HOST`                 | Bind host inside container. Keep `0.0.0.0` in Docker and control exposure with published ports/reverse proxy. | `0.0.0.0`                                             | compose file                    |
| `RESPRO_WEB_PORT`                 | Bind port inside container.                                                                                     | `8000`                                                | compose file                    |
| `REDIS_URL`                       | Redis connection URL for API and worker.                                                                        | `redis://redis:6379/0`                                | compose file                    |

### How `.env` works with Docker Compose

- Docker Compose automatically reads `.env` from the same directory as `docker-compose.web.yml` when you run `docker compose`
- Values from `.env` are used for variable substitution in compose and can also be passed into containers when referenced in the compose file.
- If a variable is both in shell and `.env`, the shell value has higher precedence.

Quick check command:

```bash
docker compose -f docker-compose.web.yml config
```

This prints the fully resolved compose configuration so you can verify which `.env` values were applied.

### `.env` best practice for token handling

Do not hardcode tokens in `docker-compose.web.yml`. Keep secrets in `.env`, and keep `.env` out of version control.

Example `.env`:

```bash
RESPRO_WEB_API_TOKEN=replace-with-a-long-random-secret
RESPRO_WEB_CORS_ORIGINS=https://respro.example.com
RESPRO_WEB_TRUSTED_PROXIES=127.0.0.1
RESPRO_WEB_JOB_TIMEOUT=3600
```
