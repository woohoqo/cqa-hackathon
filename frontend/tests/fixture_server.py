"""Opt-in synthetic UI test server, never used by the production application.

From repository root (with backend dependencies installed):
CQA_SYNTHETIC_UI_TEST=1 python -m uvicorn frontend.tests.fixture_server:app --port 8001
Database is temporary. Every result explicitly says it is synthetic; scoring and
appeal persistence run through the real FastAPI implementation.
"""
import asyncio
import json
import os
import tempfile

from backend.app.contracts import CriterionResult, EvaluationDraft
from backend.app.main import create_app

if os.getenv('CQA_SYNTHETIC_UI_TEST') != '1':
    raise RuntimeError('Set CQA_SYNTHETIC_UI_TEST=1 only for an isolated synthetic UI test.')

_database = tempfile.TemporaryDirectory(prefix='cqa-synthetic-ui-')


async def synthetic_evaluator(conversation, checklist):
    await asyncio.sleep(1.5)
    results = []
    for index, criterion in enumerate(checklist.criteria):
        if criterion.allow_na and index == 1:
            value = 'N/A'
        elif criterion.answer_type == 'flag':
            value = True
        elif criterion.answer_type == 'scale':
            value = sorted(step.value for step in criterion.scale)[-1]
        else:
            value = index % 2 == 0
        reason = 'СИНТЕТИЧЕСКИЙ ТЕСТ интерфейса, не ответ модели. '
        reason += ('Действие неприменимо по тестовому условию N/A.' if value == 'N/A' else 'Контрольный ответ для проверки доказательств и пересмотра.')
        results.append(CriterionResult(criterion_id=criterion.id, value=value, reasoning=reason,
                                      evidence_message_ids=[message.id for message in conversation.messages[-2:]]))
    return EvaluationDraft(criteria=results, raw_response=json.dumps([r.model_dump() for r in results], ensure_ascii=False),
                           provider='synthetic-ui-test', model='controlled-fixture', prompt_version='ui-test-v1')


app = create_app(database_path=os.path.join(_database.name, 'test.sqlite3'), evaluator=synthetic_evaluator)
