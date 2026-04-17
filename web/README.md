# Web Prototype

This folder contains the prototype Web UI stack:

- `backend/`: FastAPI API layer that wraps existing `respro` domain logic.
- `frontend/`: React UI for workspace selection, profiling submission, and browsing endpoints.

The backend is intentionally thin and keeps CLI behavior untouched.

For local backend startup, install web backend dependencies separately:

```bash
pip install -r web/backend/requirements.txt
python -m web.backend.main
```
