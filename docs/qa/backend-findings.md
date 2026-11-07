# Independent backend QA checkpoint

Checked source: `91c22d401224281e6592a034e8b1bc4bfedd0c47` (`main` foundation).
Date: 2026-11-07. These checks use only explicitly synthetic transcripts/evaluators.
No DEV/holdout transcript, organizer endpoint, model API, or UI was accessed by this review.

## Executed checks

Command in the available Windows environment:

```powershell
$env:CQA_SKIP_ASYNC_TESTS='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest qa/test_backend_acceptance.py qa/test_export_run.py -q
```

Result: **33 passed, 8 skipped, 1 warning**. The skip reason is explicit and is
enabled only by `CQA_SKIP_ASYNC_TESTS=1`; on ordinary environments the HTTP tests
run by default. Plugin autoload was disabled to reduce host-specific noise.

- 22 real-backend scoring/validation cases passed: allowed scale steps, invalid
  steps/types, forbidden N/A, applicable-weight redistribution, criterion 6's
  10.5 points, the 85.38 example, independent gross-error flag, null total when
  no applicable positive weight exists, and existing-message evidence references.
- One direct SQLite/service case passed: starting with a clearly synthetic
  completed evaluation, an accepted appeal keeps `ai_draft`, exact raw text and
  `original_score` unchanged and changes only the final score from 85.38 to 92.31.
  This case does not execute `evaluate()`, the worker or HTTP.
- Ten export-adapter cases passed: original-AI-only extraction, float scale-step
  conversion, required provenance, failed/missing jobs, immutable snapshot hash,
  rejection of changed checklist semantics, duplicates, conversation mapping and
  unknown cost/latency. Export-adapter input fixtures are synthetic.

Available environment: Python 3.12.1, FastAPI 0.115.5, Pydantic 2.10.3,
pytest 8.3.4, httpx 0.28.1, Starlette 0.41.3, AnyIO 4.16.0.
These are **not** the versions pinned in `requirements.lock`. This checkpoint
does not certify the clean README installation or the pinned runtime. The warning
is Starlette's deprecated `anyio.abc.BlockingPortal` alias.

## QA-ENV-001 — Async HTTP execution blocked before application startup

Classification: environment limitation, not a confirmed application defect.
Owner: QA/environment; integrator should repeat the command in the supported runtime.

Reproduction:

```powershell
python -m pytest qa/test_backend_acceptance.py::test_07_unknown_evidence_fails_the_real_job_without_a_score -vv -s -o faulthandler_timeout=15
```

Input: two synthetic messages and a test evaluator that references a nonexistent
message. Expected: the backend completes a job as `failed`, with
`invalid_ai_response`, no successful score, and preserved raw output. Basis:
shared contract requires existing-message evidence and explicit failure.

Actual in this host: before the app lifespan starts, TestClient's background
thread blocks at Windows Python's event-loop initialization:

`asyncio.new_event_loop -> ProactorEventLoop._make_self_pipe -> socket.socketpair -> accept`.

Faulthandler printed this stack after 15 seconds. The run was stopped rather
than reported as passing. Disabling pytest plugins does not address the location
of the observed block. No event-loop/socket/timeout behavior was patched to hide it.

The eight HTTP cases remain authored but unverified here: unknown evidence through
the actual job, changed checklist/new ID/text handoff, source conflict, appeal and
JSON export, rate-limit/exception/timeout failures, and the unconfigured provider.
The HTTP handoff test proves only backend-to-evaluator delivery if it passes;
it never proves that a real provider received a refreshed prompt or bypassed a cache.

## QA-METRICS-001 — Backend export lacks run provenance and model telemetry

Classification: integration dependency/proposed instrumentation, not an accuracy result.
Owner: integrator (export/run contract), AI owner (per-call usage and attempts).

Reproduction on an available runtime:

1. Import a synthetic conversation and submit an evaluation.
2. Obtain `/api/v1/exports/evaluations?format=json`.
3. Inspect the evaluation fields against `backend/app/contracts.py::Evaluation`.

Expected for a defensible QA report: recorded input/checklist identity, explicit
mock/live mode and attempt provenance, plus known latency and cost/usage or explicit
unknown values. All failures/retries must remain countable. Basis: QA requirements
and Student Guide page 4 (model and approximate cost across all calls).

Actual in the checked contract/route: the export has evaluation IDs, statuses,
snapshots, `ai_draft`, original/final scores and timestamps; it does not provide
input hashes, explicit run mode, attempt ordinals, per-provider-call retries,
latency, tokens or cost. This is established by source inspection. The HTTP
reproduction itself was blocked by QA-ENV-001, not executed successfully here.
`updated_at-created_at` cannot safely substitute for model latency: it also
includes queueing and can change after an accepted appeal.

`qa/export_run.py` therefore requires a sidecar captured at job submission. Each
entry contains `evaluation_id`, `backend_conversation_id`, manifest
`conversation_id`, `attempt_number`, `mode`, `checklist_hash`, `input_hash` and
`checklist_snapshot_hash`. It refuses to infer mode/order from row position or
provider name. Recorded jobs missing from the export stay `missing_export`.
Latency/cost remain null. Each exported attempt is a **backend evaluation job**;
internal provider-call count/retries remain unknown and must not be advertised
as measured API calls.

Exact adapter invocation and schema are in the module docstring:

```powershell
python -m qa.export_run private-export.json private-run-context.json --output private-comparison-input.json
```

The adapter uses only `ai_draft.criteria` and `original_score.total`; it never
substitutes `final_score`, final criteria, decisions or appeals. It supports only
the exact initial organizer checklist. Changed meanings or new IDs require a
separate reference set and scoring adapter. Real exports/context/annotations
must stay outside Git.

## Remaining scope

- Real AI scoring accuracy: unknown until human/organizer labels and live runs exist.
- Provider request changes, provider cache and transcript-instruction resistance:
  blocked by the unimplemented AI entry point at this checkpoint.
- UI evidence navigation, API error display and five-minute demo: not checked here.
- Full HTTP/worker lifecycle: eight explicit tests blocked by host event-loop setup.
- No production module, shared contract, dependency lock or other owner's file was modified.
