---
name: web-deployment-and-runtime-config
description: 'Handles web deployment and runtime configuration for ResistanceProfiler. Use when changing Docker/Compose setup, backend runtime environment variables, startup behavior, Redis/worker configuration, CORS/auth/rate-limit defaults, or production hosting settings.'
argument-hint: 'Deployment target, changed env vars, and whether this is review, hardening, or implementation.'
user-invocable: true
disable-model-invocation: false
---

# Web Deployment and Runtime Config

## Overview

This skill validates and updates web deployment/runtime configuration for the FastAPI + React stack in this repository. It focuses on safe defaults, deterministic startup behavior, and correct environment wiring between API, worker, and data volumes.

## When to Use

- Editing `docker-compose.web.yml` or `Dockerfile.web`
- Adding or changing web backend environment variables
- Updating startup workspace behavior (`project_databases/`, `uploads/`, `results/`)
- Changing Redis queue/worker runtime wiring
- Reviewing CORS, API token, upload rate limit, or timeout settings
- Hardening production hosting defaults

When NOT to use:
- Feature logic in `respro/core/`
- Pure frontend UI changes unrelated to runtime config
- Non-web CLI behavior changes

---

## Source of Truth

Always cross-check configuration changes against:

- `web/backend/config.py`
- `web/backend/defaults.toml`
- `docker-compose.web.yml`
- `docs/manual/docs/webapp.md`

Do not treat only one file as authoritative. Runtime behavior is defined by defaults + env overrides + compose wiring.

---

## Core Principles

1. Secure by default for non-local deployments.
2. Explicit and deterministic startup behavior.
3. Same data roots between API and worker containers.
4. Fail fast on invalid or unsafe runtime configuration.
5. Keep operational docs in sync with runtime changes.

---

## Runtime Configuration Review Process

### Step 1: Map the Runtime Surface

Identify what changed and where:

- Host/port bind settings
- Data directory and mounted volumes
- Results DB location
- Redis URL / queue / worker command
- Auth token, CORS origins, upload rate limit
- Job timeout and bootstrap behavior
- Allowed filesystem roots for upload/regenerate APIs

### Step 2: Validate Environment Contract

For each env var change, verify:

- Env key exists in `WebEnvKeys`
- A default exists (if expected) in `web/backend/defaults.toml`
- Compose file uses the expected key and semantics
- Docs mention it where users configure deployment

### Step 3: Validate API/Worker Parity

Check API and worker runtime parity:

- Same `REDIS_URL`
- Same mounted data volume root
- Compatible timeout semantics
- Queue name aligned with backend profile queue

### Step 4: Validate Security-Sensitive Defaults

Check that runtime defaults remain safe:

- `RESPRO_WEB_API_TOKEN` behavior documented and not bypassed
- `RESPRO_WEB_CORS_ORIGINS` allowlist semantics preserved
- Upload rate limit configurable and sane
- `RESPRO_WEB_ALLOWED_ROOTS` confinement remains enforceable
- No wildcard or overly broad exposure introduced accidentally

### Step 5: Validate Operational Behavior

- Startup directory bootstrap still creates deterministic subfolders
- Maintained DB bootstrap behavior (missing-only, fail behavior) remains consistent
- Health endpoint and app URL remain correct in docs/examples
- Background worker startup remains functional

### Step 6: Sync Docs

When deployment/runtime behavior changes, update in the same pass:

- `docs/manual/docs/webapp.md`
- `README.md` (if quickstart/runtime assumptions changed)
- Inline comments in `docker-compose.web.yml` when operationally relevant

---

## Repository-Specific Checklist

```
### Compose and Container Wiring
- [ ] API and worker use same data volume root
- [ ] API and worker use same Redis URL
- [ ] Port binding is intentional for local vs hosted deployment
- [ ] Worker command queue name matches backend queue expectations

### Backend Runtime Contract
- [ ] Env keys exist in web/backend/config.py (WebEnvKeys)
- [ ] Defaults are consistent with web/backend/defaults.toml
- [ ] Startup behavior matches documented data layout

### Security Runtime Defaults
- [ ] API token behavior is preserved and documented
- [ ] CORS behavior is explicit and not accidentally broadened
- [ ] Upload rate-limit behavior remains configurable and safe
- [ ] Allowed roots/path confinement behavior remains enforced

### Operational Reliability
- [ ] Job timeout semantics remain clear for API and worker
- [ ] Maintained bootstrap semantics remain consistent
- [ ] Health/app URLs and startup commands in docs are accurate

### Documentation
- [ ] webapp-hosting docs updated when behavior changed
- [ ] README updated if quickstart assumptions changed
```

---

## Red Flags

- API and worker pointing to different data roots or Redis instances
- Runtime env var added in compose but missing in backend config/defaults
- Security-related env var behavior changed without docs update
- Broad CORS or host binding change without explicit intention
- Queue/worker mismatch causing jobs never to execute
- Startup bootstrap behavior changed silently

## Output Format

When reviewing:
- Findings first, ordered by severity
- For each finding: what changed, risk, evidence, recommended fix
- End with a deployment validation summary

When implementing:
- Files changed
- Runtime behavior now expected
- Required operator actions (if any)
- Verification commands/checks
