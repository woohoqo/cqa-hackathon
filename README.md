# CQA — Freedom Broker hackathon

FastAPI + Pydantic + SQLite backend; React/Vite UI and Gemini evaluation are separate team modules.
Repository is private. Only synthetic examples belong in Git.

## Start backend (Python 3.11+)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
.venv\Scripts\python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

On macOS/Linux use `.venv/bin/python` and `cp .env.example .env`.
Health: http://127.0.0.1:8000/health. Interactive API: http://127.0.0.1:8000/docs.
At the foundation checkpoint only health is implemented. Endpoint agreement is in `contracts/README.md`;
generated OpenAPI reflects actually implemented routes, not future capabilities.

## Shared contracts and team handoff

- Read `AGENTS.md`, `backend/app/contracts.py`, `contracts/README.md`, `contracts/models.schema.json`.
- Agent 2: implement `backend/app/ai/service.py`; all criteria/scale/N/A instructions come from checklist.
- Agent 3: implement `frontend/`; API base URL `http://127.0.0.1:8000`, prefix `/api/v1`.
- Agent 4: add independent checks under `qa/` and `docs/qa/`; coordinate shared format changes with agent 1.
- Start branches `feat/ai`, `feat/ui`, `feat/qa` from published main in separate clones/worktrees.
- Private repository access requires a GitHub collaborator invitation from the repository owner.

AI needs a real Gemini API key and model ID in local `.env`. The foundation deliberately returns
`provider_not_configured` until the AI implementation is integrated. No mock provider runs in production.
`DEV_API_URL` holds the complete signed API URL locally, never in code or Git.

Student Guide, Chat Quality Checklist and prior project files were not present at initialization.
Exact official checklist wording, scale steps, N/A conditions, DEV API envelope/authentication,
and export requirements remain pending until these documents are supplied. User-provided scoring rules
are authoritative for the generic calculator; a synthetic fixture is not an official checklist.

## Development

```powershell
.venv\Scripts\python -m scripts.export_contracts
.venv\Scripts\python -m pytest
```

Data is local in ignored `data/`. Keep the development server bound to localhost. This prototype
does not yet provide authentication or production access control.
