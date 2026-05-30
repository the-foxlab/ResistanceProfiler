# Web UI

This folder contains the Web UI stack:

- `backend/`: FastAPI API layer that wraps existing `respro` domain logic.
- `frontend/`: React UI for profiling submission, database analytics, report viewing, and browsing endpoints.

The backend is intentionally thin and keeps CLI behavior untouched.

## Frontend runtime

Node.js 20+ is required to run frontend tests and build the frontend bundle.

For local backend startup, install web backend dependencies separately:

```bash
pip install -r web/backend/requirements.txt
python -m web.backend.main
```
