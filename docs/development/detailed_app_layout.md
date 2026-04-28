# Detailed App Layout

This document captures detailed runtime interactions for the web application stack (`web/frontend` and `web/backend`) and its integration with `respro/`.

## Update Policy

- Update this file when route/service/job wiring changes.
- Keep API contracts and queue boundaries explicit.
- Focus on interaction clarity, not endpoint-by-endpoint prose.

## Runtime Interaction Graph

```mermaid
flowchart LR
    FE["web/frontend"] --> API["web/backend/main.py"]
    API --> SVC["web/backend/services"]
    API --> Q["RQ Queue"]
    Q --> JOB["web/backend/jobs.py"]
    JOB --> CORE["respro/core"]
    JOB --> IO["respro/io"]
    CORE --> DB["respro/db"]
    JOB --> REPORT["respro/report"]
```

## Backend Request Paths

### Upload and Validation Flow

```mermaid
flowchart TD
    A["POST /api/upload/*"] --> B["web/backend/main.py"]
    B --> C["web/backend/services/upload.py"]
    C --> D["uploads/"]
```

### Profiling Job Submission and Execution

```mermaid
flowchart TD
    A["POST /api/profile/fasta|vcf"] --> B["web/backend/main.py"]
    B --> C["web/backend/queue.py"]
    C --> D["RQ Worker"]
    D --> E["web/backend/jobs.py"]
    E --> F["respro/core + respro/io"]
    F --> G["respro/db/results.py"]
    F --> H["respro/report/html.py"]
```

### Browse and Regeneration Paths

```mermaid
flowchart TD
    A["GET /api/databases|rules|mutations"] --> B["web/backend/services/browse.py"]
    B --> C["project_databases/*.db"]
    D["POST /api/regenerate/json"] --> E["web/backend/jobs.py"]
    E --> F["respro/report"]
```

## Security and Runtime Boundaries

- Auth, CORS, and rate limiting enforcement in `web/backend/main.py`
- Path confinement and startup-managed roots in `web/backend/startup_config.py`
- Environment defaults and key contracts in `web/backend/config.py` and `web/backend/defaults.toml`
- API and worker runtime parity through `docker-compose.web.yml`

## Drift Checklist for Changes in web/

- Did route -> service/job mapping change?
- Did API payload/response contracts change?
- Did queue/worker/runtime env wiring change?
- Are auth, CORS, rate-limit, and path-confinement assumptions still accurate?
- Do diagrams still reflect current request and job execution flows?
