# Integration verification — 2026-11-07

This report supersedes the integration-status limitations in the participants' historical
handoff reports. Their original reports and commit histories are preserved.

## Integrated sources

Published main base: `91c22d401224281e6592a034e8b1bc4bfedd0c47`.

| Part | Published head reviewed and included |
| --- | --- |
| AI / PR 5 | `e31d1ff32cadd696ae848c15b76e75f188f3640b` |
| UI / PR 7 | `fe0819dad61129fe8bbe2a34f26d0a9588b90fb9` |
| Final UI design / PR 8 | `3ddf39fd3e13c87c76a9178ea45bcc9d051e1d7d` |
| QA / PR 4 | `13c013f35538237fda2605be130584d507c40613` |

Sequential merges preserve each branch's history. Integration fixes add UI export controls,
serve the built UI from FastAPI, align the root environment profile and CI, and prevent pytest
from using a real configured key. Common HTTP/Pydantic contracts remain unchanged.

Live Gemini exposed two strict-validation failures: sparse checked regions skipped a system
message, and some boolean answers incorrectly carried an N/A explanation. Prompt v1.0.2 and
schema descriptions clarify applicability and exact JSON encoding; bounded retries receive
specific safe feedback. Validators remain strict. No output is silently repaired or assigned
a fabricated score. Regression tests exercise both repairs with an explicit test-only provider.

## Automated checks actually run

- Windows, Python 3.11, locked Python installation; `pip check` passed.
- `python -m pytest backend/tests backend/app/ai/tests qa -q`: **364 passed,
  12 skipped, 38 subtests passed**. Skips belong to the optional unified QA adapter;
  backend and AI acceptance suites ran. One Starlette TestClient deprecation warning remains.
- `npx --yes pnpm@11 --dir frontend install --frozen-lockfile` passed with pnpm 11.28.11
  and Node 22.14.0. The initial noninteractive reinstall required `CI=true`; the locked retry passed.
- Frontend tests: **10 passed**; TypeScript checking and Vite production build passed.
- `node qa/test_annotation.cjs`: passed. Independent UI contract tests: **10 passed**.
- Contract generation and `git diff --check`: passed; generated schemas stayed unchanged.

These are synthetic structural/calculation tests, not measurements of model accuracy.
They include the official **85.38** example, arbitrary scale maxima, N/A normalization,
no applicable weighted criteria, gross-error flags without zeroing, immutable checklist
snapshots, invalid/missing evidence, and accepted/rejected appeals preserving original AI.

## Real server and browser checks

Ordinary FastAPI, SQLite, React/Vite and **real `gemini-3.5-flash-lite`** were used.
No test evaluator or response cache was installed into the application.
All live model input cases were the four explicitly synthetic transcripts now provided in
`contracts/demo-conversations.json`; they contain no expected grades or organizer labels.

| Scenario | Observed result |
| --- | --- |
| New RU transcript imported through UI | Original roles, system context, text, timestamps and IDs preserved |
| Missing key, then real retry | Explicit provider-not-configured failure; successful later evaluation kept prior history |
| Original checklist v1 | Real AI completed; criteria 2/3 N/A, criterion 6=10 with weight 15; total 100.00 |
| Evidence navigation | Click focused and highlighted operator message #3 in the actual transcript |
| Browser reload | Saved evaluation and history remained visible |
| Semantic edit through UI | Criterion 1 changed from greeting to explicit apology; saved immutable v2 |
| Real reevaluation with v2 | Same transcript: criterion 1=false, total 84.62; v1 stayed intact |
| Appeal and reviewer decision | Synthetic demonstration changed criterion 4 from 20 to 15; final total 76.92 |
| Original preservation | API comparisons confirmed byte-identical raw response, unchanged ai_draft, original_score and checklist snapshot after review |
| Changed RU text | A new transcript containing an apology received criterion 1=true and 100.00 under v2 |
| KZ / EN batch | Real calls completed; source language/text retained, allowed values and N/A validated |
| EN call transcript, gross violation | Private-payment proposal flagged true; weighted total remained 100.00, flag displayed separately |
| Failure and retry | Invalid model responses displayed as failed with no score; successful new runs preserved failures |
| JSON / CSV UI downloads | Downloaded files parsed: 10 evaluation records, JSON includes one appeal and decision; CSV retained UTF-8 BOM |
| One-port production build | Browser opened built UI on port 8000, showing saved 84.62 original / 76.92 final and original-AI switch |

Six live evaluations completed across prompt versions v1.0.1/v1.0.2. Three earlier real
evaluations failed validation (six HTTP attempts); their raw responses remain preserved.
One earlier negative test accidentally attempted HTTP when the local key became configured
and was cancelled by its deadline. Its outcome/usage is unknown. That test now explicitly
disables the key and the complete pytest suite blocks real HTTP transports.
The local attempt ledger therefore contains **13 entries: 6 completed, 6 invalid, 1 cancelled**.
The missing-key evaluation made no HTTP call. Do not interpret six successes as a measured
accuracy rate or hide failed attempts; these inputs were used for structural integration repair.
Costs remain unknown because a confirmed price snapshot/full usage is not available.

DEV import through the UI succeeded for **65 organizer dialogues** with local-only storage.
Filters expose RU/KZ/EN and five voice-call transcripts. A 78-message imported call opened
with its source timestamps and an audio control/link. Playback did not become ready in the
available browser (no duration/progress); successful real audio playback is **not claimed**.
No organizer ground truth or holdout content was used for prompt tuning.

## Remaining limits

- Organizer reference scores/human adjudicated labels are unavailable; agreement and MAE are unmeasured.
- Synthetic multilingual smoke checks do not establish broad RU/KZ/EN or call-domain accuracy.
- Real audio playback, precise audio seeking, speech recognition and Dynamics deployment are unverified/not implemented.
- No authentication: operator/reviewer switch is a local demo mode. Use localhost, one Uvicorn process.
- Quota/cooldown is per process; exhausted daily quota needs a later manual retry, not automatic deferred scheduling.
- Audit correlation/content hashes/per-HTTP latency improvements remain tracked in issue 3;
  do not treat the evaluation export as a complete provider billing ledger.
- Secrets, signed URLs, raw DEV transcripts, PDFs, databases and local audit/export files stay outside Git.

The root README supplies reproducible startup and a five-minute demonstration. The integration
PR and final main merge commit identify the published revision and CI outcome.
