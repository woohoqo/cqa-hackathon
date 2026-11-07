"""Synthetic export-adapter checks; no real transcripts or provider calls."""
from copy import deepcopy

import pytest

from backend.app.contracts import ChecklistVersion
from backend.app.default_checklist import official_checklist
from qa.export_run import adapt_export, snapshot_hash


def fixture():
    checklist = ChecklistVersion(**official_checklist().model_dump(), id='synthetic-checklist', version=1)
    snapshot = checklist.model_dump(mode='json')
    row = {'id': 'synthetic-evaluation', 'conversation_id': 'synthetic-backend-conversation',
           'checklist_snapshot': snapshot, 'status': 'completed',
           'ai_draft': {'criteria': [{'criterion_id': '1', 'value': False},
                                    {'criterion_id': '2', 'value': 'N/A'},
                                    {'criterion_id': '6', 'value': 7.0}]},
           'original_score': {'total': 70.0}, 'final_score': {'total': 100.0},
           'final_criteria': [{'criterion_id': '1', 'value': True}], 'error': None}
    provenance = {'evaluation_id': row['id'], 'backend_conversation_id': row['conversation_id'],
                  'conversation_id': 'synthetic-dev-id', 'attempt_number': 1, 'mode': 'mock',
                  'checklist_hash': 'synthetic-checklist-hash', 'input_hash': 'synthetic-transcript-hash',
                  'checklist_snapshot_hash': snapshot_hash(snapshot)}
    context = {'config_version': 'organizer-v1', 'checklist_hash': provenance['checklist_hash'],
               'mode': 'mock', 'reference_source': 'human_adjudicated', 'split': 'holdout',
               'manifest': [{'conversation_id': 'synthetic-dev-id', 'language': 'EN',
                             'split': 'holdout', 'transcript_hash': provenance['input_hash']}],
               'references': [], 'evaluation_provenance': [provenance]}
    return {'schema_version': '1', 'evaluations': [row], 'appeals': [], 'decisions': []}, context


def test_uses_original_ai_and_keeps_unknown_cost_latency():
    exported, context = fixture()
    attempt = adapt_export(exported, context)['attempts'][0]
    assert attempt['original_ai'] == {'answers': {'1': 'no', '2': 'na', '6': 7}, 'total': 70.0}
    assert type(attempt['original_ai']['answers']['6']) is int
    assert attempt['conversation_id'] == 'synthetic-dev-id'
    assert attempt['cost_usd'] is None and attempt['latency_ms'] is None
    assert attempt['attempt_number'] == 1 and attempt['mode'] == 'mock'


def test_never_invents_provenance_or_attempt_number():
    exported, context = fixture()
    context['evaluation_provenance'] = []
    with pytest.raises(ValueError, match='without recorded provenance'):
        adapt_export(exported, context)
    exported, context = fixture()
    del context['evaluation_provenance'][0]['attempt_number']
    with pytest.raises(ValueError, match='attempt_number'):
        adapt_export(exported, context)


def test_completed_without_original_is_error_not_final_score_fallback():
    exported, context = fixture()
    exported['evaluations'][0]['ai_draft'] = None
    attempt = adapt_export(exported, context)['attempts'][0]
    assert attempt['status'] == 'error'
    assert attempt['original_ai'] is None
    assert attempt['error_category'] == 'missing_original_ai'


def test_failed_and_missing_export_jobs_are_retained():
    exported, context = fixture()
    exported['evaluations'][0].update(status='failed', ai_draft=None,
                                     error={'code': 'ai_timeout', 'message': 'SYNTHETIC'})
    attempt = adapt_export(exported, context)['attempts'][0]
    assert attempt['status'] == 'failed' and attempt['error_category'] == 'timeout'
    exported['evaluations'] = []
    missing = adapt_export(exported, context)['attempts'][0]
    assert missing['status'] == 'missing_export'
    assert missing['attempt_id'] == 'synthetic-evaluation'


def test_snapshot_hash_mismatch_retains_error_attempt():
    exported, context = fixture()
    context['evaluation_provenance'][0]['checklist_snapshot_hash'] = 'wrong'
    attempt = adapt_export(exported, context)['attempts'][0]
    assert attempt['status'] == 'error' and attempt['error_category'] == 'checklist_snapshot_mismatch'


def test_custom_criterion_meaning_cannot_claim_organizer_version():
    exported, context = fixture()
    exported['evaluations'][0]['checklist_snapshot']['criteria'][0]['description'] = 'Changed meaning'
    context['evaluation_provenance'][0]['checklist_snapshot_hash'] = snapshot_hash(exported['evaluations'][0]['checklist_snapshot'])
    with pytest.raises(ValueError, match='Custom/changed checklist'):
        adapt_export(exported, context)


def test_duplicate_original_criterion_is_not_silently_collapsed():
    exported, context = fixture()
    criteria = exported['evaluations'][0]['ai_draft']['criteria']
    criteria.append(deepcopy(criteria[0]))
    attempt = adapt_export(exported, context)['attempts'][0]
    assert attempt['status'] == 'error'
    assert attempt['error_category'] == 'duplicate_or_invalid_original_criterion'


@pytest.mark.parametrize('target', ['evaluations', 'provenance'])
def test_duplicate_export_or_provenance_is_rejected(target):
    exported, context = fixture()
    collection = exported['evaluations'] if target == 'evaluations' else context['evaluation_provenance']
    collection.append(deepcopy(collection[0]))
    with pytest.raises(ValueError, match='Duplicate'):
        adapt_export(exported, context)


def test_conflicting_backend_conversation_mapping_is_rejected():
    exported, context = fixture()
    context['evaluation_provenance'][0]['backend_conversation_id'] = 'wrong'
    with pytest.raises(ValueError, match='mapping conflicts'):
        adapt_export(exported, context)
