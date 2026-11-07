# AI evaluator handoff

For integration-wide verification, read [the full QA recheck](../../../docs/qa/full-recheck.md)
and [the enhancement report](../../../docs/enhancements-report.md). Each report identifies
its tested revision and distinguishes mocked tests from actual model calls. The historical
reports near the end of this file describe earlier revisions, not the current test coverage
or a present network restriction. QA findings AI-QA-002/003 remain open accuracy limitations.

## Applicability before grading — prompt v1.1.1

Each criterion's internal model response now begins with a required `applicability` field.
For `allow_na=true`, it contains `na_condition_applies`, a brief factual `reasoning` and
nonempty original `evidence_message_ids`. The schema includes that criterion's current
`na_condition`, including newly added/replaced criteria. A true condition decision requires
`value="N/A"`; false requires a boolean/flag/configured scale answer. For `allow_na=false`,
the field must be explicitly null. The two booleans have different meanings: a true N/A
condition means inapplicable, not successful performance.

Validation rejects a contradictory condition/grade pair and uses the existing bounded,
same-connection format-repair path. It never rewrites a grade or recognizes special IDs,
languages or phrases. Applicability references and its brief explanation are projected into
the existing result evidence/reasoning; `raw_response` remains exact. Shared DTOs, scoring,
model, temperature, reserve policy and total attempt/time budgets are unchanged. Existing
stored evaluations retain their old prompt version and raw text; they are not revalidated
against the new wire protocol. Synthetic evaluator mocks must include the new field.

This addresses an observed structural gap: the old response could admit that an action was
unnecessary in free-text reasoning yet return true. Structural consistency still cannot
prove that the model's applicability decision is factually correct. Tests include applicable
positive/negative controls and dynamic boolean, scale and flag criteria; they do not establish
RU/KZ/EN accuracy. The integrator/QA report records the separately authorized, controlled live
old/new comparison. No real model call was made while implementing this change.

Version v1.1.1 further distinguishes the operator's initial information from the client's
need to learn an answer, conditional on the meaning of the current N/A rule. It does not
treat every informational request as N/A. For every answer type, reasoning and evidence
precede the final value in the generated schema. Scale value schemas include the complete
current step labels/descriptions, and instructions require the selected anchor to match
the cited facts independently of other dimensions. Absence claims must be checked against
the full transcript and the current rule's alternatives. These remain model instructions,
not semantic corrections performed by the validator. Model/thinking/temperature settings
are unchanged by this commit; any separate profile comparison must report its actual settings.

## Reserve connections (current implementation, 2026-11-08)

The evaluator supports **основной**, **резерв 1**, **резерв 2**, in that order.
Only credentials change: model, prompt version, generation settings, full input and
schema stay the same. There is one logical result and one shared attempt/time budget.
The success response text remains exact; failed calls are never presented as scores.

Configure secrets only in the existing server `.env`; never copy it into another
worktree or expose keys in frontend variables. Empty reserves leave primary operation
unchanged. No legacy reserve-key aliases existed before this change.

```dotenv
GEMINI_API_KEY=
GEMINI_API_KEY_BACKUP_1=
GEMINI_API_KEY_BACKUP_2=
GEMINI_QUOTA_GROUP=
GEMINI_BACKUP_1_QUOTA_GROUP=
GEMINI_BACKUP_2_QUOTA_GROUP=
AI_MAX_ATTEMPTS=3
AI_TRANSIENT_ATTEMPTS_PER_CONNECTION=2
AI_QUOTA_EXHAUSTION_COOLDOWN_SECONDS=3600
AI_TRANSIENT_COOLDOWN_SECONDS=5
```

Quota-group values are non-secret aliases for **verified shared quota/billing limits**,
for example `project-a`. Keys in the same Google project share quota; different owners
do not establish independence. Check the actual projects and applicable shared billing
limits before assigning distinct aliases. An empty group is `unknown`: its quota may
overlap **every** configured group. Exhausting an unknown group pauses all connections;
exhausting a known group also excludes unknown connections. Distinct verified groups
remain eligible. Identical credentials configured twice are used only once.

The in-process gate atomically leases each connection and quota group through response
classification, so concurrent jobs see a disabled key or exhausted group before another
request starts. Pacing applies both per connection and per overlapping quota group.
Known independent groups may run concurrently. State is shared across the backend queue,
but not across processes or machines; use one backend worker. Restarting loses this
in-memory state and does not restore Google quota.

| Condition | Behavior |
| --- | --- |
| 401/403; typed invalid-key 400 | Disable that credential/model configuration until it changes or the application restarts; try the next eligible connection |
| Explicit daily/exhausted quota | Suspend the overlapping quota group for at least the configured suspension and reported delay; skip its other keys |
| Temporary 429 | Respect the longer Retry-After/RetryInfo/application cooldown; retry only within the shared budget, otherwise consider an independent eligible group |
| Network timeout or 5xx/408 | Bounded retries with pauses; open a connection circuit after the configured consecutive-failure threshold, then consider the next eligible connection |
| Bad request/model/schema configuration | Fail immediately; changing keys does not repair the request |
| Safety refusal or output limit | Fail immediately without switching credentials |
| Invalid model JSON/evidence | Bounded format repair on the same connection, never key-shopping for a better answer |

The quota suspension is an application policy, **not a prediction of Google reset time**.
Unknown 429 causes remain temporary rate limits; private free-text error messages are
not used to invent quota/authentication classifications. Long server delays are never
shortened for retrying the same connection. An unavailable pool returns a structured
error with no fabricated criteria; after fixing credentials/quota, the caller may retry.

`AI_MAX_ATTEMPTS` counts all actual requests across all keys, not attempts per key. It
includes format repairs and transport failures. With a budget of 2, the third connection
cannot be reached after two HTTP attempts. A budget of 3 can reach all three for immediate
auth/quota failures; with two transient tries per connection, up to 5 may be needed to
reach the final reserve. `AI_EVALUATION_TIMEOUT_SECONDS` includes queue-gate waits,
pacing, backoff and all requests. Keep backend `AI_TIMEOUT_SECONDS` above that deadline.

Transport remains direct HTTPX REST: each attempt owns a separate client and immutable
credential selection. There is no Google SDK retry loop and no global credential mutation.
HTTPX's default transport performs zero connection retries. If switching to Google Gen AI
SDK later, explicitly set `HttpRetryOptions(attempts=1)` or integrate every SDK retry into
this same budget; its documented default is five attempts and must not multiply ours.

### Execution metadata and accounting

`EvaluationDraft.execution` and `AIProviderError.execution` carry `run_id`, selected
connection, failover flag, elapsed evaluator duration and all actual attempts. The backend
owns storing/exporting this metadata. Each attempt has its connection/group, requested or
resolved model, start/end, duration, safe error/status and available total tokens/cost.
Successful `models_used` contains the resolved model of the completed response (requested
model only if the provider omits its version); it does not count an alias and resolved
version as two successful models. Failed targets remain explicit in attempt metadata.

The same `run_id` joins the local append-only audit. Audit records now include immutable
SHA-256 hashes of actual messages, full checklist, outgoing input and system instruction,
plus duration and available token components/pricing. No keys, transcripts, raw responses
or private error bodies enter this ledger. Local circuit rejection creates no fake HTTP
attempt or token entry. Cancellations during requests remain attempts with unknown usage.
Aggregate tokens/cost are `null` if any contributing attempt is unknown; known per-attempt
values remain available. Evaluator duration includes waits and is distinct from each
provider-attempt duration.

External cancellation retains normal `asyncio.CancelledError` semantics and adds
`execution` and exact last `raw_response` attributes to that exception. The backend await
wrapper must save those attributes before re-raising: `asyncio.wait_for` otherwise converts
the cancellation into a plain timeout. Cancellation during a gate wait creates no HTTP
attempt; cancellation during a request records unknown usage and releases its lease.

### Reserve verification and limits

`backend/app/ai/tests/test_failover.py` uses synthetic keys and `httpx.MockTransport` for
primary failure, first-reserve failure, shared/unknown exhausted quota, complete outage,
bounded Retry-After, circuit recovery, concurrent jobs, immutable payload/settings,
safe refusals, total deadlines and audit linkage. It never exhausts a real quota.
Run these together with the existing provider/validation tests and independent QA checks:

```sh
python -m pytest backend/app/ai/tests qa/test_ai_acceptance.py backend/tests/test_api.py -q
```

No real reserve availability test was performed for this implementation. Empty reserves
mean reserve operation is **not configured**, even when all mock tests pass. The integrator
will separately report any authorized short availability check. Failover does not establish
evaluation accuracy or fix the published semantic findings AI-QA-002/003; those remain
subject to human-labelled evaluation. It does not change the model, rubric or prompts.

Primary documentation checked for this implementation:
[Google project rate limits](https://ai.google.dev/gemini-api/docs/rate-limits),
[GenerateContent errors](https://ai.google.dev/gemini-api/docs/generate-content/api-errors),
[Google RetryInfo/QuotaFailure](https://github.com/googleapis/googleapis/blob/master/google/rpc/error_details.proto),
[Google SDK retry options](https://googleapis.github.io/python-genai/genai.html#genai.types.HttpRetryOptions),
[HTTPX transport retries](https://www.python-httpx.org/advanced/transports/).

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
| `GEMINI_MODEL` | Required supported model ID; team choice: `gemini-3.5-flash-lite` |
| `AI_PROVIDER` | `gemini`; other values fail explicitly |
| `GEMINI_TIMEOUT_SECONDS` | `30`, timeout per HTTP operation |
| `AI_EVALUATION_TIMEOUT_SECONDS` | `100`, total evaluation deadline including retries |
| `AI_MAX_ATTEMPTS` | `3`, total actual requests across all connections and format repairs; configurable 1–5 |
| `AI_RETRY_BASE_SECONDS` | `1`, exponential backoff with jitter |
| `AI_RETRY_MAX_SECONDS` | `10`; longer Retry-After suspends that connection/group and permits only an eligible reserve |
| `AI_MIN_REQUEST_INTERVAL_SECONDS` | `0`; free-tier profile uses `15` between request starts, including retries |
| `AI_RATE_LIMIT_COOLDOWN_SECONDS` | `60`; minimum shared cooldown on HTTP 429, extended by Google's delay |
| `GEMINI_MAX_OUTPUT_TOKENS` | `8192`; may need adjustment for long checklists/thinking |
| `GEMINI_TEMPERATURE` | `1.0`, Google's recommended default for Gemini 3 |
| `GEMINI_THINKING_LEVEL` | Unset; free-tier profile uses `minimal`, supported by 3.5 Flash-Lite |
| `GEMINI_THINKING_BUDGET` | Unset; alternative for budget-based models, not with thinking level |
| `AI_USAGE_LOG_PATH` | `data/ai_usage.jsonl`, relative to process working directory |
| `AI_INPUT_USD_PER_MILLION` | Unset; optional configured uncached input price |
| `AI_CACHED_INPUT_USD_PER_MILLION` | Unset; optional cached input price |
| `AI_OUTPUT_USD_PER_MILLION` | Unset; optional output price including thinking |
| `AI_PRICE_LABEL` | Optional price date/tier description, up to 200 characters |

Keep backend `AI_TIMEOUT_SECONDS` (currently 120) greater than the AI total deadline.
The backend separately limits concurrency through `AI_CONCURRENCY`. `GOOGLE_API_KEY`
is intentionally not an implicit fallback: use the explicit `GEMINI_API_KEY` setting.
Invalid credentials are disabled before considering an eligible reserve; invalid request
configuration fails without switching keys. Redirects and environment proxy
inheritance are disabled; outbound access to `generativelanguage.googleapis.com` is required.

### Free-tier profile

Run `python -m scripts.init_env` from the repository root, then set `GEMINI_API_KEY`
in that checkout's `.env` once. This command never replaces an existing `.env`.
Do not copy a sample over an existing secret file. If applying a different profile,
edit only the relevant non-secret settings and keep the existing key and DEV URL.
The original free-tier profile selected `gemini-3.5-flash-lite`, minimal thinking, backend
concurrency 1, at most two attempts and 15 seconds between request starts. Existing settings
are preserved; inspect the current root example/profile for a fresh installation. Two
attempts cannot reach a third key after two failures. This is conservative application pacing,
NOT a claim that Google's quota is 4 RPM. Check the project's active RPM/input TPM/RPD
in https://aistudio.google.com/usage?timeRange=last-28-days&tab=rate-limit.
No fixed daily quota is assumed. Limits are per project, shared across its API keys/apps;
RPD resets at midnight Pacific according to Google's documentation.

Run a single backend worker. Pacing/cooldown is in memory, shared per connection and
overlapping model/quota group in this process, and resets on restart. Multiple workers/hosts need an integrator-owned
shared limiter. Waiting counts against the total evaluation timeout and is cancellable.
There is no extra `countTokens`, independent judging pass, search, cache or paid fallback.
The complete dialogue/checklist still goes to the model; input TPM is enforced by Google,
not estimated from characters. Large inputs may exhaust it despite low request frequency.

On 429, the evaluator reads bounded structured `google.rpc.RetryInfo` and uses the longer
of that delay, `Retry-After` and the configured cooldown. Later evaluations wait a short
cooldown when it leaves an HTTP timeout window inside the total deadline; long/daily
cooldowns fail locally without an API call or an invented usage entry. A known `PerDay` QuotaFailure
stops retries for that evaluation; this classification is an identifier convention, not
a guaranteed list of Google's quota names. Without a server delay only the configured
cooldown applies; the app does not pretend to know a project's remaining daily allowance.
The current backend marks a rejected evaluation failed, including long/daily cooldowns.
Integrator follow-up: persist a deferred job/next-attempt time for automatic queue recovery;
until then, retry those failed evaluations after quota recovery.
Truncated `MAX_TOKENS` output fails once with `ai_output_limit`; repeating the same cap
would waste quota. Increase the output cap for longer checklists instead of dropping criteria.

The free profile uses zero unit prices (the confirmed Free Tier), not a measured $0 run.
These variables cannot activate Free Tier or prevent charges on a billing-enabled project.
Unknown usage remains null. Minimal thinking and pacing have not yet been compared on
human labels; the profile is an operational starting point, not a demonstrated quality gain.

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

The shared contract stores execution metadata in `EvaluationDraft.execution` and
`Evaluation.ai_execution`, including attempts and aggregate tokens/cost. Each actual provider
attempt is also appended to the local JSONL audit, including rejected responses and blocked
responses. The audit records a run
UUID, hashed conversation/checklist IDs, checklist version, prompt version, requested/resolved
model, response ID, finish reason, status, available token counts and price snapshot.
It contains no transcript, prompt, original identifier, API key or source URL.
Use the execution `run_id` to correlate it with this ledger. Hashes of IDs and input snapshots
provide additional correlation. A new evaluation gets a new run UUID.

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
python -m pytest -q
git diff --check
```

Root pytest discovery includes `backend/tests`, `backend/app/ai/tests` and `qa`.
The Linux workflow `.github/workflows/backend.yml` explicitly runs those same three paths.
This describes the configured coverage; the enhancement report records the latest checks
actually completed and their revision. Do not reuse an older passing CI run as verification
of newer code.
All AI tests use clearly labelled synthetic data and mocked HTTP responses. They establish
transport, schema, validation, accounting and integration behavior, not scoring accuracy.

The reserve implementation was checked with
`python -m pytest backend/app/ai/tests qa/test_ai_acceptance.py backend/tests/test_api.py -q`:
240 passed, with one existing Starlette/httpx deprecation warning. This narrower result
overlaps the full suite; do not add the counts together. No real reserve call was made.

## Historical verification — initial implementation, 2026-11-07

The following results belong to the initial implementation and prompt versions named below.
They are retained as history, not the current acceptance report. Later real RU/KZ/EN,
changed-criterion and adversarial-input checks are documented in
[the full QA recheck](../../../docs/qa/full-recheck.md), including semantic failures.
Current integration work and authorized availability checks are recorded in
[the enhancement report](../../../docs/enhancements-report.md).

Checks completed at that earlier stage:
- 173 AI tests passed on the normal Windows asyncio event loop with
  `python -m pytest backend/app/ai/tests -q` on 2026-11-07 after the execution environment
  recovered. HTTP remains mocked: this verifies native scheduling, not real model output.
- The same 173 tests previously passed with the test-only no-I/O event-loop runner while
  the host blocked `socketpair()` setup. That runner is not for production or real I/O.
- 34 existing backend scoring/importer/contract-export tests passed normally.
- Linux GitHub CI passed the existing 54 backend tests and installation from
  `requirements.lock` on 8624f57 (run 42369317120). That historical revision excluded AI tests;
  the current workflow includes them and QA.
- Python compilation and Git whitespace checks passed.

Reproduce the offline mocked run (do not use this runner for production or real I/O):

```sh
python -m backend.app.ai.tests.run_offline_tests
```

At that stage AI tests still needed Linux CI inclusion. This integration work is now complete.

### Historical blocked smoke, 2026-11-07

A user-provided key was configured locally on 2026-11-07. One live smoke evaluation with a
synthetic Russian dialogue, the initial eight-criterion configuration and a replacement
criterion ID was attempted (AI_MAX_ATTEMPTS=1). It failed with provider_connection_failed
before an HTTP response. A separate unauthenticated HTTPS diagnostic confirmed Windows
socket permission error 10013 to generativelanguage.googleapis.com:443. DNS and native
asyncio initialization passed. This is an execution-environment network restriction, not
evidence of a rejected API key, nonexistent model or exhausted quota.

The blocked attempt remains in the local ledger with null response/model metadata, null
token counts and null cost. It is a separate run from the successful check below. The secret
and local smoke files are ignored by Git and were not published.

### Historical successful live smoke, 2026-11-07

The user ran the prepared smoke script in ordinary Windows PowerShell, outside the restricted
agent execution environment, at 17:23 Asia/Qyzylorda. The supplied output and the resulting
local JSON artifact agree. One attempt completed with STOP using gemini-3.5-flash-lite,
prompt cqa-single-pass-v1.0.0, in 5.766 seconds (whole evaluator, not an average latency).
Reported usage: 2,416 prompt + 930 output = 3,346 total tokens. Cached-input, thinking and
tool-input fields were not reported and remain null. The configured Free Tier rates are zero,
but cost_usd remains null because the conservative cost calculator needs those missing
counts; no invoice or billing-tier verification is claimed.

The exact dynamically generated eight-criterion schema was accepted by the real API.
Application validation accepted every current ID/value and evidence reference, including
synthetic-contact-new-id. The operator did not receive credit for the bot greeting; lack
of clarification and identity verification received explained N/A on this general-info
dialogue. Consultation and clarity returned 20 and 10 respectively; closure passed and no
gross violation was flagged. Missing-action region/anchor and original raw_response were
preserved. A Russian case-ending defect in the projected anchor description was corrected
after reviewing the output; model text and decisions are unchanged.

Qualitative observations for human review (not ground-truth labels or accuracy metrics):
- The consultation decision calls the answer accurate but cites only client/operator text.
  The supporting synthetic schedule in system message s1 is omitted from its evidence IDs.
- The missing greeting anchor names the right first operator message o1, but uses before
  where the criterion calls for an action inside that message. Structural checks cannot
  establish this semantic placement.
- The empathy explanation cites absence of client dissatisfaction, which by itself does
  not establish operator empathy. The offer of help in o2 is a more concrete basis for review.
- The replacement ID is new, but its meaning is close to the original contact criterion;
  radically changed meanings had only mocked transport coverage at that stage; later live
  checks and their failures are recorded in the full QA recheck.

No prompt tuning or independent model vote was introduced from these observations.

This verifies the configured key/model and schema on ONE synthetic Russian example, not
accuracy against human labels. No fresh model call was made by the agent after the network
denial in that initial session. At that stage Kazakh/English behavior, adversarial transcripts
and changed checklist semantics were unverified; later live checks are in the full QA recheck.
Broad latency/cost statistics, accuracy against human labels and real reserve/quota recovery
are not established by either smoke test. The recorded network restriction describes that
session only; it is not a diagnosis of the current execution environment.

QA coordination and proposed contract additions:
https://github.com/woohoqo/cqa-hackathon/issues/2
Development/holdout split is not yet confirmed. No dataset records were used for tuning.
Keep holdout transcripts and labels out of few-shot, prompt iteration and development fixtures.
Only compare a second model call after measuring improvements against human annotations.

## Official documentation checked 2026-11-07

- Model: https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
- Structured output: https://ai.google.dev/gemini-api/docs/structured-output
- REST, usage and finish reasons: https://ai.google.dev/api/generate-content
- Errors: https://ai.google.dev/gemini-api/docs/api-errors
- Sampling/thinking: https://ai.google.dev/gemini-api/docs/gemini-3
- Thinking levels: https://ai.google.dev/gemini-api/docs/thinking
- Project rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Structured error details: https://github.com/googleapis/googleapis/blob/master/google/rpc/error_details.proto
- Pricing/lifecycle: https://ai.google.dev/gemini-api/docs/pricing and https://ai.google.dev/gemini-api/docs/deprecations

Transport uses documented `generateContent` compatibility fields `responseMimeType` and
`responseJsonSchema`. Current reference documents also introduce `responseFormat`; migration
to that shape must be verified with a live synthetic smoke test before switching. JSON schema
guarantees syntax/shape, not factual evidence or correct decisions; application checks and
human-labelled evaluation remain necessary. Prompt-injection defenses are layered but are
not a guarantee that a model will always ignore an adversarial transcript.
