# CQA team agreement

Use published `origin/main` as the base. Do not overwrite other participants' work.
Each participant needs a separate clone or worktree.

- Agent 1: `feat/backend`; owns backend (except AI after handoff), contracts, root config, README, integration into main.
- Agent 2: `feat/ai`; owns `backend/app/ai/`.
- Agent 3: `feat/ui`; owns `frontend/`.
- Agent 4: `feat/qa`; owns `qa/`, `docs/qa/`.

Contracts are defined in `backend/app/contracts.py`. Propose changes to agent 1;
do not silently alter them. Regenerate schemas with `python -m scripts.export_contracts`.
AI entry point: `async evaluate_dialogue(conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft`.
Preserve `raw_response` exactly. Never fabricate a model result.
Use `AIProviderError` for safe structured errors. No automatic gross-error zeroing.
Only total is rounded; N/A removes criterion weight. Scales use their maximum, not weight.
Official Student Guide and Chat Quality Checklist take priority. Their initial configuration is
in `backend/app/default_checklist.py`; implementation notes are in `contracts/official-requirements.md`.

Never commit secrets, signed service URLs, `.env`, real transcripts, local databases, or source PDFs.
Synthetic fixtures must be clearly labeled. No force push or deletion of teammates' branches.
Before dependent changes/integration: fetch and inspect branches and PRs. Commit, push, verify remote SHA.
