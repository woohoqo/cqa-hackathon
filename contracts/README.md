# Shared API contract v1

Source of truth: `backend/app/contracts.py`. `models.schema.json` is generated from all Pydantic models;
`openapi.json` from implemented FastAPI routes. Regenerate with `python -m scripts.export_contracts`.
API prefix: `/api/v1`. All bodies/results are JSON unless exporting CSV. Errors use
`{"error":{"code":"...","message":"...","retryable":false}}`; invalid requests return HTTP 422.

## Implemented endpoints

| Method | Path | Request | Response |
|---|---|---|---|
| GET | /health | — | status, api_version |
| POST | /api/v1/conversations | ConversationInput | Conversation (201) |
| GET | /api/v1/conversations | limit, offset | Conversation[] |
| GET | /api/v1/conversations/{id} | — | Conversation |
| POST | /api/v1/imports/json | ImportRequest | ImportResult |
| POST | /api/v1/imports/dev | optional DevImportRequest (`group_id`, default DEV); URL from environment | ImportResult |
| POST | /api/v1/checklists | ChecklistCreate | ChecklistVersion (201) |
| GET | /api/v1/checklists | — | ChecklistVersion[] |
| GET | /api/v1/checklists/{id} | — | ChecklistVersion |
| POST | /api/v1/evaluations | EvaluationRequest | EvaluationBatch (202) |
| GET | /api/v1/evaluations | conversation_id?, limit, offset | Evaluation[] |
| GET | /api/v1/evaluations/{id} | — | Evaluation |
| GET | /api/v1/batches/{id} | — | EvaluationBatch |
| POST | /api/v1/evaluations/{id}/appeals | AppealCreate | Appeal (201) |
| GET | /api/v1/evaluations/{id}/appeals | — | Appeal[] |
| POST | /api/v1/appeals/{id}/decisions | ReviewDecisionCreate | ReviewDecision (201) |
| GET | /api/v1/evaluations/{id}/decisions | — | ReviewDecision[] |
| GET | /api/v1/exports/evaluations | format=json\|csv, batch_id? | downloadable file |

Poll batch/evaluation until `completed` or `failed`; failed evaluation includes a safe `error`.
Each evaluation embeds its immutable `checklist_snapshot`. New checklist submissions with the same
`key` create a new monotonically increasing version; existing versions are never edited.

## AI handoff

```python
from backend.app.contracts import Conversation, ChecklistVersion, EvaluationDraft
async def evaluate_dialogue(conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft: ...
```

Output every criterion exactly once, referencing at least one original message ID per criterion, including N/A and missing actions. Values: JSON `true`/`false`
for boolean and flag, a defined numeric scale step for scale, literal `"N/A"` only when allowed.
`raw_response` is the exact response text, not `json.dumps(parsed_result)`. Include provider, model,
prompt_version. For malformed provider output, raise `AIProviderError("invalid_ai_response", safe_message,
raw_response=original_text)`. Never put keys, service URLs or transcript content in an error message.
The backend validates output and calculates all scores; AI must not calculate or override totals.
If no sections are submitted, the backend uses section `general` / «Общие»; omitted criterion
section_id also defaults to `general`. List endpoints accept `limit` (1–1000) and `offset`.
JSON export contains `schema_version`, `evaluations`, `appeals`, `decisions`; CSV is a summary of totals.
Organizer imports preserve `channel`, `language`, `audio_url`, `dataset_group_id` in conversation metadata.
See `official-requirements.md` for rules supplied in the two PDFs.

## Scoring and appeals

Boolean true earns weight, false earns zero. Scale earns `value / max(scale) * weight`.
N/A excludes the weight. Total = earned points / applicable weight * 100, rounded only once to
two decimals. Sections contain unrounded percentages. No applicable positive weight => `total=null`.
`weight` and `earned_points` are original, pre-normalization values. For displaying normalized
criterion weight/contribution, multiply them by `100 / applicable_weight`; never re-round intermediate
values before calculating the total. Gross-error flag points are null.
Flag criteria require zero weight; their true values set `gross_error`, never zero the score.
An accepted review supplies a full replacement CriterionResult; rejected decisions cannot supply one.
AI draft and raw response stay unchanged. Accepted decisions overlay criteria only in the final result.

## Synthetic request examples

Create transcript:
```json
{"source":"manual","external_id":"synthetic-001","messages":[{"id":"m1","role":"client","text":"Тестовый вопрос","timestamp":"2026-11-07T12:00:00+05:00"},{"id":"m2","role":"operator","text":"Тестовый ответ","timestamp":null}]}
```
Returned Conversation adds `id` and `created_at`; absent message IDs are assigned stable IDs.

Start evaluation:
```json
{"conversation_ids":["<conversation.id>"],"checklist_id":"<checklist.id>"}
```
Create appeal:
```json
{"criterion_id":"6","reason":"Прошу проверить выбранную ступень","author":"demo-reviewer","proposed_value":7}
```
Accept appeal:
```json
{"outcome":"accepted","reviewer":"demo-supervisor","reason":"Подтверждено по реплике","replacement":{"criterion_id":"6","value":7,"reasoning":"Подтверждено вручную","evidence_message_ids":["m2"]}}
```


## Разовая ссылка API организаторов

`POST /api/v1/imports/dev` принимает `{ "group_id": "DEV", "api_url": "https://example.invalid/dataset" }`.
`api_url` — необязательная HTTPS-ссылка до 8192 символов, без userinfo, фрагмента и внутренних пробелов.
Пробелы по краям удаляются; пустое значение и `null` равнозначны отсутствию поля и используют `DEV_API_URL`.
Ссылка из запроса имеет приоритет только для этого импорта; настройки сервера не меняются.
Подписанные query-параметры сохраняются; заменяется только `group_id`.
Введённая ссылка не сохраняется в БД, `.env` или browser storage и не возвращается клиенту в ошибках.
Неверная ссылка в запросе даёт `422 invalid_request` с именем поля `body.api_url`.
Пользователь должен передавать ссылку только доверенному backend. Основной сервер предназначен для localhost;
защищённое общее демо продолжает запрещать этот импорт, включая запросы с `api_url`.
Согласование изменения общего контракта: issue #15; окончательная интеграция — участником 1.
