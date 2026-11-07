# Independent AI review — synthetic transport only

Reviewed AI source: `feat/ai@d006fb9582a76255e04380d8d4cc2c68ead1770c`.
Integration base: `main@91c22d401224281e6592a034e8b1bc4bfedd0c47`.
Date: 2026-11-07. The AI files were overlaid only in a separate review worktree;
the QA commit does not include production AI files or change shared contracts.

## Executed verification

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:CQA_SKIP_ASYNC_TESTS='0'
python -m backend.app.ai.tests.run_offline_tests qa/test_ai_acceptance.py -q
```

Result: **12 passed in 0.98 seconds**. The prior 8624f57 snapshot separately passed
10 tests before quota pacing was added. All conversations, responses, prices and
credentials are explicitly synthetic. `AISettings.from_env` is replaced by a
synthetic configuration; no environment secret is read. Every HTTP request uses
`httpx.MockTransport`; no provider or organizer endpoint is contacted.

This is a **mock-only scheduler**, not a native event-loop/network compatibility
test. The AI branch's test runner uses Python task/timer/cancellation scheduling,
disables self-pipe setup, and rejects all selector I/O registrations. It must never
be used for production or live I/O. The host's native Windows socketpair block
remains unresolved. No native HTTP/browser/full application claim follows from
this result.

`qa/test_ai_acceptance.py` skips at collection when
`backend.app.ai.prompt` is absent, so foundation main with the AI stub remains
usable. `CQA_SKIP_ASYNC_TESTS=1` explicitly skips the nine asynchronous cases;
the three pure prompt/validation cases remain runnable once AI is integrated.

## What the tests establish

| Requested check | Observed result | Scope |
| --- | --- | --- |
| 8: changed criterion meaning and new ID | Current wording and new ID appear in generated input/schema and actual mocked HTTP payload; output IDs match current schema | Actual AI prompt/service/provider code, mocked response |
| 9: stale cache | Identical repeated evaluations invoke transport twice; changing text and checklist invokes it again with changed payload and a new run UUID | Local evaluator path; no claim about vendor caching |
| 11: provider failures | 401 and 429 return after one request; 500 uses two bounded attempts with this zero-retry-wait fixture; all attempted calls are audited | Actual transport/service with mocked HTTP statuses; 429 cooldown exceeds fixture retry budget |
| Daily quota | Two evaluation jobs both fail with daily quota, while only the first reaches HTTP and only one ledger entry exists | Actual provider/pacing/service with a synthetic typed QuotaFailure |
| Output cap | MAX_TOKENS is an explicit error after one request; the same cap is not retried | Actual service with synthetic response envelope |
| 11: invalid model output | Unknown evidence never returns a successful draft; invalid output is audited, retained as an error or followed by a separately recorded valid repair | Structural/evidence validation, not semantic correctness |
| 12: transcript instruction | The synthetic injection, including a message whose source role is `system`, stays in JSON inside the request's user content; systemInstruction and current schema remain controlled by evaluator code | Trust-boundary construction only |
| 12: retry feedback | Invalid adversarial model text is not copied into system instructions; retry feedback contains the fixed validation reason | Retry construction only |
| Usage/cost | Rejected and successful responses both remain in one run's ledger; configured synthetic rates produce the expected summed estimate; keys and transcript text are absent | Accounting mechanics only; not measured vendor billing |

The tests do **not** establish that a real model understands replacement wording,
ignores adversarial instructions, or assigns correct labels. Mock responses are
fixtures, not independent ground truth. Live acceptance of the exact JSON schema,
availability of the selected model, latency, actual prices and scoring accuracy
against humans/organizers remain unknown.

## QA-AI-001 — Audit cannot yet join reliably to application evaluations

Classification: instrumentation/integration gap. Owner: integrator for the shared
link/export fields; AI owner for timing and content fingerprints. No production
fix was applied in this review.

Reproduction:

1. Run `test_08_09_actual_http_payload_refreshes_and_repeated_calls_are_not_cached`.
2. Observe that every invocation writes a distinct `run_id` and numbered attempts.
3. Compare `backend/app/ai/audit.py::record_attempt` with
   `backend/app/contracts.py::EvaluationDraft` and `Evaluation`.

Expected, based on QA reporting requirements: each original application evaluation
can be joined unambiguously with all provider attempts, the exact text/checklist
version, elapsed time and known/unknown cost. Retries and failed requests remain
visible in the same run.

Actual in this SHA: the ledger records `run_id`, `attempt`, timestamp `at`, hashed
conversation/checklist IDs, checklist version, prompt/model identifiers, status,
error, usage, prices and estimated cost. It has **no evaluation ID/run linkage in
the shared result**, content `input_hash`, content `checklist_hash`, start/end pair
or elapsed latency. Hashing a conversation ID is not hashing its text. Several
evaluations of the same conversation and checklist cannot safely be joined merely
by their ID hashes or nearby timestamps. Audit timestamp `at` is not a duration.

Precise proposal for integrator review (not an adopted contract):

- Link the application evaluation ID to AI `run_id`, and preserve that linkage in
  immutable original evaluation/export metadata. A nullable `usage_run_id` on the
  shared AI draft is one option; agent 1 must approve the format.
- Add SHA-256 of canonical UTF-8 message data (ID, role, text, original timestamp,
  in original order) and canonical full checklist configuration to the run record.
  Define canonical serialization once and use it in QA/backend/AI.
- Record monotonic `latency_ms` per attempted provider call, including failures,
  plus whole-run elapsed time including backoff. Wall-clock start/end may be added
  separately; do not derive call latency from database `updated_at`.
- Keep all existing attempt/status/usage/price records. Aggregate costs only when
  the required attempts have known values; expose known subtotal and unknown count.

Until the linkage is integrated, `qa/export_run.py` still needs explicit recorded
sidecar provenance; joining the AI ledger by ID hash and timestamp would be a guess.
Its exported cost/latency remain null. No measured model accuracy or successful
live model call is claimed in this checkpoint.

In d006fb, provider_rate_limit/provider_daily_quota_exhausted can also originate
from the local request gate without HTTP. A backend job's error code therefore
cannot establish that an API request happened. The report labels these as records
with provider error/quota codes, preserves all jobs, and leaves provider call count
unknown. A linked ledger or explicit network_attempted field is required for exact
network counts. The free-tier profile's live model availability, project quota,
latency and billing were not tested by these synthetic checks.
