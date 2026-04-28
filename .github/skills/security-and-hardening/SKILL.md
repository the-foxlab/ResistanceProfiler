---
name: security-and-hardening
description: 'Hardens ResistanceProfiler against vulnerabilities. Use when handling file uploads, path resolution, API authentication, rate limiting, SQL persistence, or external API integrations (NCBI, PubChem). Use when building any feature that accepts untrusted data, modifies CORS or auth config, or interacts with the filesystem.'
argument-hint: 'Feature, module, or endpoint to audit. Include scope: upload, path, auth, SQL, external API.'
user-invocable: true
disable-model-invocation: false
---

# Security and Hardening

## Overview

Security-first development for ResistanceProfiler. Treat every upload, file path, and external API response as hostile until validated. Security is a constraint on every line of code that touches user data, filesystem access, or external systems — not a phase to add later.

## When to Use

- Building or changing any upload endpoint (`/api/upload/*`)
- Adding or modifying path resolution or filesystem access
- Changing authentication, token handling, or CORS configuration
- Integrating with external services (NCBI E-utilities, PubChem)
- Adding or modifying rate limiting
- Writing SQL queries or schema changes
- Reviewing any change that accepts untrusted input at a system boundary

---

## The Three-Tier Boundary System

### Always Do (No Exceptions)

- Validate all external input at system boundaries: upload endpoints, CLI file path arguments, VCF/FASTA/BAM content.
- Parameterise all SQL queries — never concatenate user input into query strings.
- Enforce path confinement: all file access must stay within `RESPRO_WEB_ALLOWED_ROOTS` (defaults: `project_databases/`, `uploads/`, `results/`).
- Treat data from external APIs (NCBI, PubChem) as untrusted — validate structure before use.
- Keep secrets in environment variables; never hardcode tokens, API keys, or passwords.
- Apply auth token checks consistently on all protected endpoints when `RESPRO_WEB_API_TOKEN` is set.
- Enforce upload size limits and content-type checks before any parsing.

### Ask First (Requires Human Approval)

- Changing CORS configuration or allowed origin list (`RESPRO_WEB_CORS_ORIGINS`).
- Adding new external service integrations.
- Adding new upload endpoints or changing accepted file types.
- Modifying `RESPRO_WEB_ALLOWED_ROOTS` path confinement logic.
- Changing rate limiting values or keying strategy (`RESPRO_WEB_UPLOAD_RATE_LIMIT`).
- Granting elevated permissions or bypassing auth checks.

### Never Do

- Never commit secrets, tokens, or API keys to version control.
- Never log sensitive data (tokens, file contents, user-supplied paths in full).
- Never trust client-supplied paths directly — always resolve and confine server-side.
- Never use string concatenation for SQL queries.
- Never disable auth enforcement "temporarily" without a plan to re-enable.
- Never expose stack traces or internal error details in API responses.
- Never accept binary content as FASTA, VCF, or BAM without magic-byte or structural validation.

---

## Project-Specific Threat Areas

### 1. File Upload and Path Traversal

All upload endpoints must:

- Reject binary content before parsing (magic-byte check for BAM: `BAM\x01`; structural check for VCF: `##fileformat` + `#CHROM`; encoding check for FASTA).
- Enforce line-length caps on text inputs to prevent resource exhaustion.
- Resolve the final path server-side and verify it falls within an allowed root.
- Never use a user-supplied filename directly for storage — always derive a safe stem.

```python
# Good: resolve and confine
resolved = (allowed_root / user_filename).resolve()
if not str(resolved).startswith(str(allowed_root)):
    raise ValueError('Path outside allowed root')

# Bad: trust user input
open(user_supplied_path, 'wb')
```

### 2. SQL Parameterisation

All SQLite queries must use bound parameters — no f-strings or `%`-formatted query strings.

```python
# Good
cursor.execute('SELECT * FROM rule WHERE gene_id = ?', (gene_id,))

# Bad
cursor.execute(f'SELECT * FROM rule WHERE gene_id = "{gene_id}"')
```

### 3. Authentication and Token Enforcement

When `RESPRO_WEB_API_TOKEN` is configured:

- Every protected endpoint must check the `Authorization: Bearer <token>` header.
- Rejection must return `401`, not `403` or a silent error.
- Token comparison must be constant-time to prevent timing attacks:

```python
import hmac
if not hmac.compare_digest(expected_token, provided_token):
    raise HTTPException(status_code=401)
```

### 4. CORS Configuration

- Default (no token set): restrict to localhost development origins only.
- With token: allow origins from `RESPRO_WEB_CORS_ORIGINS` (comma-separated); never default to `*` in production.
- Never derive allowed origins from request data.

### 5. Rate Limiting

- Upload endpoints must be rate-limited via `RESPRO_WEB_UPLOAD_RATE_LIMIT`.
- Token-authenticated requests: key by token.
- Unauthenticated requests: key by client IP.
- Do not disable rate limiting when testing — use `fakeredis` or async=False queue isolation instead.

### 6. External API Responses (NCBI, PubChem)

- Treat all responses as untrusted data.
- Validate structure (expected keys, types) before use in logic or persistence.
- Never pass external API response content directly into SQL or HTML without sanitisation.
- All external calls must be non-fatal: wrap in try/except and degrade gracefully.
- Never follow redirects to user-supplied URLs.

### 7. Error Responses

- Never expose stack traces, file paths, or internal DB details in API error responses.
- Return user-facing messages only: validation errors, job status, structured error codes.
- Log internal details server-side only.

---

## Dependency Security

Before adding a new dependency:

1. Does the existing stack already solve this?
2. Is it actively maintained? (Check last commit date, open security advisories.)
3. Does it have known vulnerabilities? (`pip audit` or `safety check`)
4. What is the license? (Must be compatible with MIT.)

Run before any release:

```bash
pip audit
```

Triage findings:

```
Critical / High + reachable in production  → fix immediately
Critical / High + dev-only or unreachable  → fix soon, not a blocker
Moderate                                   → fix in next release cycle
Low                                        → fix during regular dependency updates
```

---

## Security Review Checklist

```
### Upload and File Access
- [ ] Content-type validated before parsing (magic bytes / structural check)
- [ ] Upload path resolved and confined to allowed root
- [ ] Safe filename stem derived server-side
- [ ] Line-length and size caps enforced

### SQL
- [ ] All queries use bound parameters (no concatenation)
- [ ] No user-controlled string interpolated into any SQL string

### Authentication
- [ ] Protected endpoints check Bearer token when RESPRO_WEB_API_TOKEN is set
- [ ] Token comparison uses hmac.compare_digest
- [ ] Rejection returns 401

### CORS and Rate Limiting
- [ ] CORS origins restricted to known list, never wildcard in production
- [ ] Rate limiting active on upload endpoints
- [ ] Rate limiting keyed by token (authenticated) or IP (unauthenticated)

### External APIs
- [ ] NCBI and PubChem responses validated before use
- [ ] No external content passed directly to SQL or HTML
- [ ] All external calls wrapped in try/except and non-fatal

### Secrets and Logging
- [ ] No secrets, tokens, or keys in source code or git history
- [ ] Internal paths and stack traces not returned in API responses
- [ ] Sensitive details logged server-side only

### Dependencies
- [ ] pip audit shows no critical or high reachable vulnerabilities
```

---

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "This is an internal tool, security doesn't matter" | Internal tools get compromised. Attackers target the weakest link. |
| "We'll add security later" | Security retrofitting is 10× harder than building it in. |
| "No one would try to exploit this" | Automated scanners will find it. Security by obscurity is not security. |
| "The framework handles security" | Frameworks provide tools, not guarantees. You still need to use them correctly. |

## Red Flags

- User-supplied paths used directly in file system access
- SQL query strings built with f-strings or `%` formatting
- Auth token check missing on any protected endpoint
- CORS set to `*` with no token requirement
- Upload content parsed before type/structure validation
- External API response content inserted into SQL or rendered as HTML
- Stack traces or file paths returned in error responses
- Secrets present in any committed file
