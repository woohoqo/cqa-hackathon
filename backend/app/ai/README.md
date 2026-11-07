# AI evaluator handoff

Entry point (contract v1, unchanged):

```python
from backend.app.ai.service import evaluate_dialogue

# conversation: backend.app.contracts.Conversation, including every source message
# checklist_snapshot: backend.app.contracts.ChecklistVersion loaded by the backend
draft = await evaluate_dialogue(conversation, checklist_snapshot)
```

The backend already calls this entry point. It owns persistence, score arithmetic,
appeals and final results. This module returns `EvaluationDraft` or the existing
safe `AIProviderError`. No web server, database or shared-contract edits are included.

## Configuration

Use environment variables or the existing root `.env` (never commit the key).
Existing process variables take precedence. No additional packages are required:
the provider uses the project's existing `httpx`, `pydantic` and `python-dotenv`.

| Variable | Default / purpose |
| --- | --- |
| `GEMINI_API_KEY` | Required; sent only in `x-goog-api-key` to Google's fixed HTTPS API |
| `GEMINI_MODEL` | Required supported model ID; candidate for live validation: `gemini-3.8-flash` |
| `AI_PROVIDER` | `gemini`; other values fail explicitly |
| `GEMINI_TIMEOUT_SECONDS` | `30`, timeout per HTTP operation |
| `AI_EVALUATION_TIMEOUT_SECONDS` | `100`, total evaluation deadline including retries |
| `AI_MAX_ATTEMPTS` | `3`, initial request plus at most two retries; configurable 1–5 |
| `AI_RETRY_BASE_SECONDS` | `1`, exponential backoff with jitter |
| `AI_RETRY_MAX_SECONDS` | `10`; longer Retry-After returns a safe retryable error immediately |
| `GEMINI_MAX_OUTPUT_TOKENS` | `8192`; may need adjustment for long checklists/thinking |
| `GEMINI_TEMPERATURE` | `1.0`, Google's recommended default for Gemini 3 |
| `GEMINI_THINKING_LEVEL` | Unset; model-specific. Use `low`, `medium` or `high` for 3.8 Flash |
| `GEMINI_THINKING_BUDGET` | Unset; alternative for budget-based models, not with thinking level |
| `AI_USAGE_LOG_PATH` | `data/ai_usage.jsonl`, relative to process working directory |
| `AI_INPUT_USD_PER_MILLION` | Unset; optional configured uncached input price |
| `AI_CACHED_INPUT_USD_PER_MILLION` | Unset; optional cached input price |
| `AI_OUTPUT_USD_PER_MILLION` | Unset; optional output price including thinking |
| `AI_PRICE_LABEL` | Optional price date/tier description, up to 200 characters |

Keep backend `AI_TIMEOUT_SECONDS` (currently 120) greater than the AI total deadline.
The backend separately limits concurrency through `AI_CONCURRENCY`. `GOOGLE_API_KEY`
is intentionally not an implicit fallback: use the explicit `GEMINI_API_KEY` setting.
Invalid credentials/configuration are not retried. Redirects and environment proxy
inheritance are disabled; outbound access to `generativelanguage.googleapis.com` is required.

## Evaluation and evidence

One successful model call is the primary path. Retries are bounded repairs for malformed
results or transient provider failures, not independent votes or an ensemble.
There are no few-shot examples, no LLM-generated ground truth and no hardcoded criterion IDs.

Every request includes the complete original message list (ID, role, text and original
timestamp including nulls), plus the saved checklist snapshot. Audio/service URLs and
arbitrary importer metadata are not sent. Oversized input is rejected, never truncated.
Bot/system messages remain evidence context; a bot greeting is not an operator action.
Business rules, allowed values, scales and N/A conditions come from the supplied criteria.
Specific current criteria override conflicting general checklist descriptions, so replacing
a criterion does not keep the old criterion's rules alive in the evaluator.

The generated schema is a closed object with one property per current criterion ID.
Validation rejects duplicate JSON keys, omitted/extra criteria, invalid types or scale steps,
forbidden/unexplained N/A, unknown message IDs, noncontiguous/reversed checked regions,
and missing anchors for omitted actions. It does not repair markdown or silently coerce
string/number booleans. A model response must finish with `STOP` before being accepted.

Internal evidence fields are `evidence_kind`, `checked_message_ids`, `expected_action`
(`message_id`, `position=before|in|after`, `description`) and `na_condition_met`.
These fields stay in the exact model text in `raw_response`. Until the integrator adds
shared fields, the projection includes checked-region endpoints, expected location and N/A
explanation in existing `reasoning` and `evidence_message_ids`. UI quotations must still
come from the original messages by ID. No free-text quotation is used as source evidence.
`confidence` is left unset; it is not a calibrated probability.

## Usage and cost

The current shared contract has no usage field. Each actual provider attempt is appended to
the local JSONL audit, including rejected responses and blocked responses. It records a run
UUID, hashed conversation/checklist IDs, checklist version, prompt version, requested/resolved
model, response ID, finish reason, status, available token counts and price snapshot.
It contains no transcript, prompt, original identifier, API key or source URL.
Hash an ID with SHA-256 to correlate it with this ledger. A new evaluation gets a new run UUID.

Unknown/missing counts (including timeouts) are `null`, never an invented zero. Completed
invalid responses may be billable and remain in the ledger. Billing after a timed-out request
can remain unknown. Application-level cancellation is propagated, not converted into a score.
An unwritable audit file fails before the first provider request; a later write failure also
fails the evaluation. Single-process concurrent calls are serialized for audit writes. Deploy
multiple backend processes with separate audit paths or integrate a database-backed ledger.

When all needed counts AND configured rates are present:

```
cost_usd = ((prompt - cached_input) * input_rate
            + cached_input * cached_rate
            + (output + thinking) * output_rate) / 1_000_000
```

No vendor price is hardcoded. Cost is an estimate from actual reported usage and the configured
tier/date, not an invoice. If metadata or prices are incomplete, cost remains `null`.
Sum attempts per run; report unknown attempts separately, never as a measured free evaluation.

## Verification

Run from the repository root:

```sh
python -m pytest backend/tests backend/app/ai/tests -q
git diff --check
```

Current root pytest configuration only discovers `backend/tests`; the explicit AI test path
above is required. Integrator action: add `backend/app/ai/tests` to testpaths/CI when merging.
All AI tests use clearly labelled synthetic data and mocked HTTP responses. They establish
transport, schema, validation, accounting and integration behavior, not scoring accuracy.

Completed checks in this implementation:
- 115 AI tests passed using a test-only event loop that runs Python's actual task/timer/
  cancellation scheduler but forbids every I/O registration. This avoids the host's blocked
  Windows `socketpair()` setup; it is NOT native event-loop or real-network validation.
- 34 existing backend scoring/importer/contract-export tests passed normally.
- Python compilation and Git whitespace checks passed. Available installed dependencies
  were used; a clean installation from `requirements.lock` was not verified here.

Reproduce the offline mocked run (do not use this runner for production or real I/O):

```sh
python -m backend.app.ai.tests.run_offline_tests
```

The full default-loop suite and backend HTTP integration still need Linux CI. Normal Windows
async tests could not start because even an empty event loop hung inside `socketpair/accept`.

No real Gemini API request has been performed during this implementation. The environment
has no Gemini key and terminal network access is unavailable. Availability of the configured
model, server acceptance of the exact schema, latency, real token usage/cost and semantic
accuracy on human labels remain unverified.

QA coordination and proposed contract additions:
https://github.com/woohoqo/cqa-hackathon/issues/2
Development/holdout split is not yet confirmed. No dataset records were used for tuning.
Keep holdout transcripts and labels out of few-shot, prompt iteration and development fixtures.
Only compare a second model call after measuring improvements against human annotations.

## Official documentation checked 2026-11-07

- Model: https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- Structured output: https://ai.google.dev/gemini-api/docs/structured-output
- REST, usage and finish reasons: https://ai.google.dev/api/generate-content
- Errors: https://ai.google.dev/gemini-api/docs/api-errors
- Sampling/thinking: https://ai.google.dev/gemini-api/docs/gemini-3
- Pricing/lifecycle: https://ai.google.dev/gemini-api/docs/pricing and https://ai.google.dev/gemini-api/docs/deprecations

Transport uses documented `generateContent` compatibility fields `responseMimeType` and
`responseJsonSchema`. Current reference documents also introduce `responseFormat`; migration
to that shape must be verified with a live synthetic smoke test before switching. JSON schema
guarantees syntax/shape, not factual evidence or correct decisions; application checks and
human-labelled evaluation remain necessary. Prompt-injection defenses are layered but are
not a guarantee that a model will always ignore an adversarial transcript.
