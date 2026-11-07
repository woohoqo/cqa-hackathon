# Статус QA — 7 ноября 2026

Проверяемая основа: `main@91c22d401224281e6592a034e8b1bc4bfedd0c47`. QA-ветка: `feat/qa`. Это промежуточный честный отчёт: наличие утилит и успешных синтетических тестов не означает измеренную точность AI.

| Проверка | Результат | Границы |
|---|---|---|
| Student Guide, Checklist, AGENTS.md, контракты | Изучены | Производственные контракты не изменены |
| Реальный DEV | 65 разговоров, 1 268 сообщений; структурных пропусков/неверных времён не найдено | Аудио не прослушивалось; полная анонимность не подтверждена |
| Эталоны организаторов | В DEV отсутствуют | Отдельный закрытый GT неизвестен |
| Человеческая разметка | Шаблон, 6 пилотных и выборка 26 подготовлены | Люди ещё не заполнили; группировка/holdout требуют подтверждения |
| Точность/MAE/грубые ошибки по реальным данным | Не рассчитаны | Нет согласованных меток и live AI-оценок |
| Локальная установка по README | BLOCKED окружением | Новый venv создан; pip не разрешает адрес индекса пакетов, getaddrinfo failed при setuptools |
| Чистая установка зависимостей в GitHub Actions | PASS | Ubuntu/Python3.11, точная команда pip с requirements.lock и 54 backend tests |
| 125 выбранных автоматических проверки | PASS | Backend scoring/importers + независимые backend acceptance, export adapter и QA utilities; доступные зависимости, не lock |
| 21 запись skip в основном запуске | BLOCKED | 8 HTTP/worker из-за окружения; 12 общих provider/UI probes без адаптера; 1 AI-модуль отсутствует в main. Не считать pass |
| Форма разметки | JS checks PASS | Включая SHA-256, устаревший manifest, evidence и экспорт; браузерный UX не проверен |
| HTTP backend tests | 54 backend/tests PASS в CI | Локальный общий запуск прерван на Windows socketpair; 8 новых QA HTTP tests в CI ещё не включены |
| AI через реальный API | Не запускался | В main заглушка provider_not_configured, API/стоимость/latency неизвестны |
| Отдельная ветка AI | 12 независимых проверок PASS | Точный feat/ai@d006fb9; MockTransport и mock-only scheduler, без сети/реальной модели |
| Отдельная ветка UI | 10 проверок display.ts PASS и source review | Точный feat/ui@8c596cd; не React/browser/build/E2E; найден пробел UI-QA-001 |
| UI и сквозной пользовательский процесс | BLOCKED | В изученном main frontend содержит только .gitkeep |
| Сценарий защиты | Подготовлен | Полная репетиция ещё не проведена |

Фактически выполненная финальная команда: **125 passed, 21 skipped, 1 warning**, 2,04 с:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:CQA_SKIP_ASYNC_TESTS='1'
python -m pytest backend/tests/test_scoring.py backend/tests/test_importers.py qa -q
node qa/test_annotation.cjs
```

Среда: Windows, Python 3.12; FastAPI 0.115.5, Pydantic 2.10.3, HTTPX 0.28.1, pytest 8.3.4, Uvicorn 0.32.1. Эти версии соответствуют диапазонам pyproject, но отличаются от requirements.lock; воспроизводимость locked-установки здесь не доказана.

Отдельно подтверждён [GitHub Actions run 42369375042](https://github.com/woohoqo/cqa-hackathon/actions/runs/42369375042) для head `4cd8b52d8eb50f3e61eca33101fb4541012fffc7`: Ubuntu, Python3.11, `pip install -c requirements.lock -e '.[dev]'` SUCCESS, `python -m pytest -q` — **54 passed, 1 warning in 4.42s**, git diff check SUCCESS. Проверены статусы steps и лог. Это стандартная backend suite с test-only evaluators, не live AI. Новые qa tests не входят в этот CI из-за текущего testpaths; агенту1 в issue #3 отправлено предложение расширить команду. Полный standalone Uvicorn+UI запуск по README этим результатом не утверждается.

Исходная попытка: `python -m venv .venv-clean`, затем команда README установки с ограниченными retries/timeout. Ошибка DNS не доказывает, что зафиксированные версии пакетов не существуют. Не изменять зависимости из-за этого результата.

Первые GitHub-запросы вернули 404; после исправления доступа пользователем подключение подтвердило pull/push и main. Терминальный Git продолжил получать DNS failure. Снимок main получен через GitHub-интеграцию, все blob/tree/commit SHA проверены; рабочая ветка основана на точном опубликованном коммите. Публикация выполняется через GitHub Git API без force и без изменения main.

Независимые backend acceptance и QA export adapter добавлены; детали в backend-findings.md. Из 125 выполненных проверок 23 относятся к независимым проверкам реального backend, 10 — к исходным проверкам адаптера экспорта, 23 — к дополнительным регрессиям отчёта; остальные — существующие scoring/importer tests и QA utilities. Полная backend/tests/test_api.py suite сюда не входит: исходный запуск был прерван.

При финальном review исправлены два дефекта собственных QA-утилит: Markdown раньше показывал только QA-пересчитанный MAE и мог скрыть неверный сохранённый итог; реальные provider_* коды не попадали в счётчик API errors. Добавлены отдельный MAE original_score.total с покрытием, явные расхождения и классификация кодов через настоящий export adapter. Регрессии воспроизводят total=0 при эталоне85,38, пропуски/NaN/bool/неудачные попытки и коды провайдера. Краткое отключение executor прервало промежуточные правки; после восстановления приведённый финальный запуск выполнен успешно.

Перед подготовкой к интеграции повторно прочитаны опубликованные ветки: main@91c22d4, feat/backend@b707493, feat/ai@d006fb9, feat/ui@8c596cd. AI/UI проверены в отдельных worktree с точными исходниками; их production-код не включён в QA-ветку. [AI review](ai-review.md): 12 независимых synthetic transport tests PASS. [UI review](ui-review.md): 10 pure-function tests PASS, просмотр компонентов и запросов. Эти числа относятся к разным наборам; 54 CI и 125 local пересекаются, их нельзя суммировать.

Воспроизведён пробел UI-QA-001: штатный frontend test script завершился с кодом0 и tests0 — [issue #6](https://github.com/woohoqo/cqa-hackathon/issues/6), ответственный участник3. Подтверждён QA-AI-001: AI ledger уже хранит попытки/usage/cost, но нет однозначной связи с evaluation/export, хешей содержимого и latency — предложение агентам1/2 в [issue #3](https://github.com/woohoqo/cqa-hackathon/issues/3). Это пробелы проверки/инструментирования, не доказанные ошибки оценок реальных разговоров. GitHub [PR #4](https://github.com/woohoqo/cqa-hackathon/pull/4) содержит только QA.

Браузерная проверка формы: открытие локального `file://` заблокировано URL policy браузера; обход не предпринимался. Проверены только JS/DOM harness, не реальная работа пользователя в браузере. Скриншотов проверки UI нет.

Далее на интегрированной версии: native HTTP/worker QA suite, реальный API, evidence navigation и полный сценарий по README; проверить обновлённый ledger/export после согласования контракта. Для live injection проверять смысл ответа, а не только JSON-валидность. Только после согласования разметки выполнять измерение точности. Текущее состояние без меток/запусков показано в measurement-readiness.md.

## Покрытие 12 требований

| № | Что подтверждено | Что ещё требуется |
|---|---|---|
| 1 | Допустимые шкалы, типы и запрет N/A в настоящем scorer | Семантическая применимость N/A по человеческим меткам |
| 2 | Перераспределение применимого веса | Отображение в UI |
| 3 | №6=7 при весе15 даёт10,5 | Отображение в UI |
| 4 | Итог85,38 | Реальный AI не должен сам подменять расчёт |
| 5 | Грубая ошибка не меняет итог | Отдельный флаг в UI |
| 6 | Допустимый custom checklist без применимого веса даёт null | UI не показывает0/NaN как успешный итог |
| 7 | Валидация существующих message IDs; AI invalid evidence остаётся ошибкой в MockTransport | Нажатие на evidence, фрагмент аудио, ошибка через job |
| 8 | Фактическое HTTP-тело AI-провайдера меняется с новым смыслом/ID; MockTransport | Backend handoff HTTP test и семантическое live поведение |
| 9 | Hash текста включает роль/порядок/время; AI повторно вызывает transport, изменения входят в payload | HTTP повторный запуск приложения; поведение внешнего provider cache |
| 10 | Реальный SQLite/service сохраняет original и отдельно меняет final с85,38 на92,31 при №6→10 | Полный UI/HTTP процесс |
| 11 | Сравниватель не теряет failed/missing; AI MockTransport 401/429/500 остаются ошибками, попытки сохраняются | Native HTTP API errors, реальные timeout/429/500 и отображение UI |
| 12 | Фактический AI request отделяет transcript от systemInstruction; retry не превращает ответ модели в правила | Live injection run; mock не доказывает устойчивость модели |
