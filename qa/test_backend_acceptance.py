"""Independent QA acceptance of the real backend with SYNTHETIC test evaluators.

Run explicitly: python -m pytest qa/test_backend_acceptance.py -q
No organizer data, external API, production AI prompt, UI, or provider cache is
tested here. TestClient exercises the actual queue, SQLite, validation, scoring,
appeals and export. Cases 8/9 cover only backend -> evaluator handoff/re-evaluation.
Provider request freshness and prompt-injection resistance remain unverified.
"""

import asyncio
from copy import deepcopy
import os
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.contracts import (
    AIProviderError, AppealCreate, ChecklistVersion, Conversation, ConversationInput,
    CriterionResult, EvaluationDraft, EvaluationRequest, EvaluationStatus, Message,
    ReviewDecisionCreate,
)
from backend.app.default_checklist import official_checklist
from backend.app.main import create_app
from backend.app.scoring import calculate_score, validate_results
from backend.app.service import CQAService
from backend.app.settings import Settings
from backend.app.storage import Database


API = '/api/v1'
BASE = {'1': True, '2': 'N/A', '3': 'N/A', '4': 15,
        '5': True, '6': 7, '7': True, '8': False}
RAW = '  \n{"synthetic_qa_fixture":true,"preserve_whitespace":"exactly"}\r\n'
async_environment = pytest.mark.skipif(os.environ.get('CQA_SKIP_ASYNC_TESTS') == '1',
    reason='Explicit CQA_SKIP_ASYNC_TESTS=1: host asyncio socketpair initialization is blocked; HTTP/worker behavior is unverified')


def snapshot():
    return ChecklistVersion(**official_checklist().model_dump(), id='synthetic-checklist', version=1)


def results(overrides=None, evidence='synthetic-m2'):
    values = BASE | (overrides or {})
    return [CriterionResult(criterion_id=key, value=value, reasoning='SYNTHETIC QA evidence',
                            evidence_message_ids=[evidence]) for key, value in values.items()]


def transcript(text='SYNTHETIC operator answer', external_id='synthetic-qa-original'):
    return {'source': 'synthetic-qa', 'external_id': external_id,
            'metadata': {'synthetic': True, 'language': 'EN', 'channel': 'agent chat'},
            'messages': [{'id': 'synthetic-m1', 'role': 'client', 'text': 'SYNTHETIC question',
                          'timestamp': '00:01'},
                         {'id': 'synthetic-m2', 'role': 'operator', 'text': text,
                          'timestamp': '00:05'}]}


def draft(conversation, checklist, overrides=None):
    values = BASE | (overrides or {})
    return EvaluationDraft(
        criteria=[CriterionResult(criterion_id=c.id, value=values.get(c.id, True),
            reasoning='SYNTHETIC QA evaluator; not a model judgment',
            evidence_message_ids=[conversation.messages[-1].id]) for c in checklist.criteria],
        raw_response=RAW, provider='qa-test-only', model='synthetic-evaluator',
        prompt_version='qa-acceptance-v1')


async def evaluator(conversation, checklist):
    return draft(conversation, checklist)


def client_for(tmp_path, callback=evaluator, timeout=2):
    settings = Settings(database_path=str(tmp_path / 'qa-synthetic.sqlite3'), cors_origins=[],
                        ai_concurrency=1, ai_timeout_seconds=timeout, dev_api_url='')
    return TestClient(create_app(evaluator=callback, settings=settings))


def create_transcript(client, body=None):
    response = client.post(API + '/conversations', json=body or transcript())
    assert response.status_code == 201, response.text
    return response.json()


def get_official(client):
    response = client.get(API + '/checklists')
    assert response.status_code == 200
    return next(c for c in response.json() if c['key'] == 'freedom-cqa')


def evaluate(client, conversation, checklist):
    response = client.post(API + '/evaluations', json={
        'conversation_ids': [conversation['id']], 'checklist_id': checklist['id']})
    assert response.status_code == 202, response.text
    pending = response.json()['evaluations'][0]
    assert pending['status'] == 'queued'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(API + '/evaluations/' + pending['id'])
        assert response.status_code == 200
        record = response.json()
        if record['status'] in ('completed', 'failed'):
            return record
        time.sleep(0.01)
    pytest.fail('Synthetic backend job did not finish before the QA deadline')


@pytest.mark.parametrize('criterion,value', [('4', v) for v in (0, 5, 10, 15, 20)] +
                                            [('6', v) for v in (0, 4, 7, 10)])
def test_01_permitted_scale_steps(criterion, value):
    score = calculate_score(snapshot(), results({criterion: value}))
    assert next(c.value for c in score.criteria if c.criterion_id == criterion) == value


@pytest.mark.parametrize('criterion,value', [('4', 7), ('6', 5), ('6', True),
                                            ('1', 'N/A'), ('5', 'N/A'), ('6', 'N/A'), ('8', 'N/A')])
def test_01_invalid_steps_and_forbidden_na_are_rejected(criterion, value):
    with pytest.raises(ValueError):
        calculate_score(snapshot(), results({criterion: value}))


def test_02_na_redistributes_only_applicable_weights():
    score = calculate_score(snapshot(), results({'1': True, '4': 20, '6': 10}))
    assert score.applicable_weight == 65
    assert score.earned_points == 65
    assert score.total == 100
    score = calculate_score(snapshot(), results({'1': False, '4': 20, '6': 10}))
    assert score.total == 84.62  # 55 / 65 * 100, not 55 / 100.


def test_03_scale_seven_at_weight_fifteen_is_ten_point_five():
    score = calculate_score(snapshot(), results())
    six = next(c for c in score.criteria if c.criterion_id == '6')
    assert six.weight == 15
    assert six.earned_points == 10.5


def test_04_required_total_is_8538():
    score = calculate_score(snapshot(), results())
    assert score.applicable_weight == 65
    assert score.earned_points == 55.5
    assert score.total == 85.38


def test_05_gross_error_does_not_reduce_total():
    normal = calculate_score(snapshot(), results())
    critical = calculate_score(snapshot(), results({'8': True}))
    assert critical.total == normal.total == 85.38
    assert normal.gross_error is False and critical.gross_error is True


def test_06_no_applicable_weight_has_no_total():
    custom = ChecklistVersion(id='synthetic-optional', key='synthetic-optional',
        name='SYNTHETIC optional criterion', version=1,
        criteria=[{'id': 'optional', 'title': 'Synthetic optional', 'weight': 10,
                   'answer_type': 'boolean', 'allow_na': True,
                   'na_condition': 'Synthetic request absent'}])
    answer = CriterionResult(criterion_id='optional', value='N/A', reasoning='Synthetic absence',
                             evidence_message_ids=['synthetic-m1'])
    score = calculate_score(custom, [answer])
    assert score.total is None and score.applicable_weight == 0
    assert score.sections[0].score is None


def test_07_evidence_must_exist_in_original_conversation():
    conversation = Conversation(id='synthetic-conversation', messages=[
        Message(id='synthetic-m2', role='operator', text='SYNTHETIC answer')])
    validate_results(conversation, snapshot(), results())
    with pytest.raises(ValueError, match='unknown message'):
        validate_results(conversation, snapshot(), results(evidence='nonexistent-message'))


@async_environment
def test_07_unknown_evidence_fails_the_real_job_without_a_score(tmp_path):
    async def bad_evidence(conversation, checklist):
        output = draft(conversation, checklist)
        output.criteria[0].evidence_message_ids = ['nonexistent-message']
        return output
    with client_for(tmp_path, bad_evidence) as client:
        record = evaluate(client, create_transcript(client), get_official(client))
        assert record['status'] == 'failed'
        assert record['error']['code'] == 'invalid_ai_response'
        assert record['original_score'] is None and record['final_score'] is None
        assert record['raw_response'] == RAW


@async_environment
def test_08_09_backend_handoff_gets_changed_meaning_new_id_and_new_text(tmp_path):
    calls = []
    async def observing_callback(conversation, checklist):
        calls.append((conversation.model_dump(mode='json'), checklist.model_dump(mode='json')))
        # Deliberate fixture behavior, not a claim about a model understanding the rule.
        changed_rule = checklist.criteria[0].description == 'SYNTHETIC replacement meaning'
        changed_text = conversation.messages[-1].text == 'SYNTHETIC changed answer'
        return draft(conversation, checklist, {'1': not (changed_rule or changed_text)})
    with client_for(tmp_path, observing_callback) as client:
        original_conversation = create_transcript(client)
        original_checklist = get_official(client)
        first = evaluate(client, original_conversation, original_checklist)
        updated_body = {k: deepcopy(v) for k, v in original_checklist.items()
                        if k not in ('id', 'version', 'created_at')}
        updated_body['criteria'][0]['description'] = 'SYNTHETIC replacement meaning'
        updated_body['criteria'].append({'id': 'synthetic-added-id', 'title': 'SYNTHETIC new criterion',
            'description': 'SYNTHETIC newly configured criterion', 'weight': 0,
            'answer_type': 'boolean', 'section_id': original_checklist['sections'][0]['id']})
        response = client.post(API + '/checklists', json=updated_body)
        assert response.status_code == 201, response.text
        updated_checklist = response.json()
        second = evaluate(client, original_conversation, updated_checklist)
        changed_conversation = create_transcript(client, transcript(
            text='SYNTHETIC changed answer', external_id='synthetic-qa-changed'))
        third = evaluate(client, changed_conversation, original_checklist)
        assert len(calls) == 3  # Backend did not serve a prior result instead of invoking callback.
        assert calls[1][1]['criteria'][0]['description'] == 'SYNTHETIC replacement meaning'
        assert calls[1][1]['criteria'][-1]['id'] == 'synthetic-added-id'
        assert calls[2][0]['messages'][-1]['text'] == 'SYNTHETIC changed answer'
        assert first['original_score']['total'] == 85.38
        assert second['original_score']['total'] == third['original_score']['total'] == 70
        assert second['id'] != first['id'] != third['id']
        assert second['checklist_snapshot'] == updated_checklist
        assert client.get(API + '/evaluations/' + first['id']).json()['checklist_snapshot'] == original_checklist
        assert 'synthetic-added-id' in {c['criterion_id'] for c in second['ai_draft']['criteria']}


@async_environment
def test_09_same_external_id_changed_text_is_an_explicit_conflict(tmp_path):
    with client_for(tmp_path) as client:
        conversation = create_transcript(client)
        response = client.post(API + '/conversations', json=transcript(text='SYNTHETIC changed text'))
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'source_conflict'
        assert client.get(API + '/conversations/' + conversation['id']).json() == conversation


@async_environment
def test_10_appeal_preserves_ai_and_recomputes_final_export(tmp_path):
    with client_for(tmp_path) as client:
        before = evaluate(client, create_transcript(client), get_official(client))
        path = API + '/evaluations/' + before['id']
        response = client.post(path + '/appeals', json={'criterion_id': '6',
            'reason': 'SYNTHETIC disagreement', 'author': 'qa-rater', 'proposed_value': 10})
        assert response.status_code == 201, response.text
        appeal = response.json()
        decision = {'outcome': 'accepted', 'reviewer': 'qa-reviewer',
            'reason': 'SYNTHETIC reviewed evidence', 'replacement': {'criterion_id': '6', 'value': 10,
            'reasoning': 'SYNTHETIC correction', 'evidence_message_ids': ['synthetic-m2']}}
        response = client.post(API + '/appeals/' + appeal['id'] + '/decisions', json=decision)
        assert response.status_code == 201, response.text
        after = client.get(path).json()
        assert after['ai_draft'] == before['ai_draft']
        assert after['raw_response'] == before['raw_response'] == RAW
        assert after['original_score'] == before['original_score']
        assert after['original_score']['total'] == 85.38
        assert after['final_score']['total'] == 92.31
        exported = client.get(API + '/exports/evaluations', params={'batch_id': before['batch_id']}).json()
        assert exported['evaluations'][0]['ai_draft'] == before['ai_draft']
        assert exported['evaluations'][0]['original_score']['total'] == 85.38
        assert exported['evaluations'][0]['final_score']['total'] == 92.31
        assert exported['appeals'][0]['status'] == 'accepted'
        assert len(exported['decisions']) == 1


@pytest.mark.parametrize('failure,expected_code', [('rate_limit', 'rate_limit'),
    ('unhandled_exception', 'provider_error'), ('timeout', 'ai_timeout')])
@async_environment
def test_11_provider_failures_are_failed_jobs_and_exported(tmp_path, failure, expected_code):
    async def failing_callback(conversation, checklist):
        if failure == 'rate_limit':
            raise AIProviderError('rate_limit', 'SYNTHETIC rate limit', retryable=True)
        if failure == 'timeout':
            await asyncio.sleep(0.1)
        raise RuntimeError('SYNTHETIC private exception payload; must not be exposed')
    with client_for(tmp_path, failing_callback, timeout=0.02) as client:
        record = evaluate(client, create_transcript(client), get_official(client))
        assert record['status'] == 'failed'
        assert record['error']['code'] == expected_code
        assert record['original_score'] is None and record['final_score'] is None
        assert record['ai_draft'] is None
        assert 'private exception payload' not in record['error']['message']
        response = client.post(API + '/evaluations/' + record['id'] + '/appeals',
            json={'criterion_id': '6', 'reason': 'SYNTHETIC attempted appeal', 'author': 'qa-rater'})
        assert response.status_code == 409
        exported = client.get(API + '/exports/evaluations').json()['evaluations']
        assert len(exported) == 1 and exported[0]['status'] == 'failed'


@async_environment
def test_11_unconfigured_production_entrypoint_has_no_fake_success(tmp_path):
    with client_for(tmp_path, callback=None) as client:
        record = evaluate(client, create_transcript(client), get_official(client))
        assert record['status'] == 'failed'
        assert record['error']['code'] == 'provider_not_configured'
        assert record['ai_draft'] is None and record['original_score'] is None


def test_10_persisted_synthetic_evaluation_appeal_preserves_originals(tmp_path):
    """Direct real persistence/service check; initial completed fixture is synthetic.

    This intentionally does not claim execution of evaluate(), worker(), or HTTP.
    """
    db = Database(str(tmp_path / 'qa-direct-service.sqlite3'))
    db.initialize()
    settings = Settings(database_path=db.path, cors_origins=[], ai_concurrency=1,
                        ai_timeout_seconds=2, dev_api_url='')
    service = CQAService(db, settings)
    checklist = service.create_checklist(official_checklist())
    conversation = service.import_conversations([ConversationInput.model_validate(transcript())]).conversations[0]
    batch = service.enqueue(EvaluationRequest(conversation_ids=[conversation.id], checklist_id=checklist.id))
    record = batch.evaluations[0]
    record.ai_draft = draft(conversation, checklist)
    record.raw_response = record.ai_draft.raw_response
    record.original_score = calculate_score(checklist, record.ai_draft.criteria)
    record.final_score = record.original_score.model_copy(deep=True)
    record.final_criteria = [c.model_copy(deep=True) for c in record.ai_draft.criteria]
    record.status = EvaluationStatus.COMPLETED
    with db.connect(write=True) as connection:
        db.save_evaluation(connection, record)
    before = db.read('evaluations', record.id).model_dump(mode='json')
    appeal = service.appeal(record.id, AppealCreate(criterion_id='6', reason='SYNTHETIC appeal', author='qa-rater'))
    replacement = CriterionResult(criterion_id='6', value=10, reasoning='SYNTHETIC correction',
                                  evidence_message_ids=['synthetic-m2'])
    service.decide(appeal.id, ReviewDecisionCreate(outcome='accepted', reviewer='qa-reviewer',
                   reason='SYNTHETIC review', replacement=replacement))
    after = db.read('evaluations', record.id).model_dump(mode='json')
    assert after['ai_draft'] == before['ai_draft']
    assert after['raw_response'] == before['raw_response'] == RAW
    assert after['original_score'] == before['original_score']
    assert after['original_score']['total'] == 85.38
    assert after['final_score']['total'] == 92.31
    assert db.read('appeals', appeal.id).status == 'accepted'
    assert len(service.decisions(record.id)) == 1
