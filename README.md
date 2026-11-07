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
Implemented routes and examples are in `contracts/README.md`; generated OpenAPI matches the application.
The official eight-criterion checklist is created on first startup. New versions never replace old ones.
Use one Uvicorn process (no `--workers`): the durable SQLite queue runs `AI_CONCURRENCY` async consumers.
On restart queued jobs resume; interrupted running jobs fail explicitly and can be resubmitted.

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

Student Guide v3 and Chat Quality Checklist are reflected in `contracts/official-requirements.md`
and `backend/app/default_checklist.py`. `POST /api/v1/imports/dev` fetches the organizer envelope;
an optional `{"group_id":"CONTROL"}` body selects a future group without exposing the signed URL.
JSON import accepts `{"conversations":[...]}`; new transcripts use `POST /api/v1/conversations`.
Reimporting the same source/external_id/content reuses IDs; changed content returns 409 without overwriting.

Start a batch with `POST /api/v1/evaluations`, then poll its batch/evaluation IDs until completed or failed.
Results include original and final scores, exact AI response and checklist snapshot. Accepted appeals
modify only final criteria/scores. JSON exports include reviewer audit history; CSV exports summary scores.
Scores are calculated with Decimal, normalizing N/A and rounding only the total. Gross errors remain flags.

Before hackathon submission the guide requires a public repository; this development repository remains
private as requested. Record the real Gemini model and approximate evaluation cost after AI integration.
The organizer reference scores are not supplied, so agreement with them cannot yet be measured.

## Development

```powershell
.venv\Scripts\python -m scripts.export_contracts
.venv\Scripts\python -m pytest
```

Data is local in ignored `data/`. Keep the development server bound to localhost. This prototype
does not yet provide authentication or production access control.
