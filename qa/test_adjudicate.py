"""Synthetic human-workflow fixtures: not actual human labels or accuracy data."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from qa.adjudicate import compare_annotations, digest, finalize, render_review
from qa.compare import compare_report
from qa.test_compare import answers, payload


def fixture():
    manifest = {'config_version': 'organizer-v1', 'checklist_hash': 'synthetic-rubric',
                'records': [{'conversation_id': 'synthetic-ru', 'language': 'RU', 'split': 'tuning',
                             'pilot': True, 'transcript_hash': 'synthetic-transcript', 'messages': 3}]}
    exports = []
    for name in ('human-A', 'human-B'):
        entry = {'conversation_id': 'synthetic-ru', 'source': 'human_individual', 'status': 'complete',
                 'annotator_id': name, 'checklist_hash': manifest['checklist_hash'],
                 'transcript_hash': manifest['records'][0]['transcript_hash'], 'answers': answers(),
                 'total': 85.38, 'criteria': [{'criterion_id': cid, 'answer': answer,
                    'evidence': [{'message_id': 'synthetic-ru:m0001'}], 'comment': 'SYNTHETIC reason',
                    'disputed': False, 'uncertainty_reason': ''} for cid, answer in answers().items()]}
        exports.append({'qa_annotations_version': 1, 'source': 'human_individual',
                        'annotator_id': name, 'checklist_hash': manifest['checklist_hash'], 'annotations': [entry]})
    return *exports, manifest


def completed_review(a, b, manifest):
    review = compare_annotations(a, b, manifest)
    review['records'][0]['decision'].update(status='complete', reviewer_id='human-reviewer',
        reviewed_at='2026-11-07T12:30:00+05:00', rationale='SYNTHETIC human resolution',
        criteria=deepcopy(a['annotations'][0]['criteria']))
    return review


def test_matching_individuals_do_not_automatically_create_reference():
    a, b, manifest = fixture()
    review = compare_annotations(a, b, manifest)
    assert review['overall']['per_criterion']['8']['agreement'] == 1
    assert review['records'][0]['decision']['criteria'][0]['answer'] is None
    result = finalize(a, b, manifest, review)
    assert result['references'] == []
    assert result['pending_conversation_ids'] == ['synthetic-ru']


def test_explicit_finalization_keeps_originals_hashes_and_adapts_to_metrics():
    a, b, manifest = fixture()
    frozen = deepcopy((a, b, manifest))
    result = finalize(a, b, manifest, completed_review(a, b, manifest))
    assert (a, b, manifest) == frozen
    assert result['source_exports'] == [a, b]
    assert result['source_export_hashes'] == [digest(a), digest(b)]
    ref = result['references'][0]
    assert ref['source'] == 'human_adjudicated' and ref['total'] == 85.38
    assert len(ref['source_annotation_ids']) == 2 and ref['source_annotation_ids'][0] != ref['source_annotation_ids'][1]
    data = payload()
    data.update(checklist_hash=manifest['checklist_hash'], split='tuning', references=result['references'],
                manifest=manifest['records'])
    data['attempts'][0].update(checklist_hash=manifest['checklist_hash'], input_hash=manifest['records'][0]['transcript_hash'])
    assert compare_report(data)['overall']['primary_first_attempt']['labelled'] == 1


def test_na_and_gross_disagreements_are_explicit():
    a, b, manifest = fixture()
    for cid, value in [('2', 'yes'), ('8', 'yes')]:
        b['annotations'][0]['answers'][cid] = value
        next(c for c in b['annotations'][0]['criteria'] if c['criterion_id'] == cid)['answer'] = value
    review = compare_annotations(a, b, manifest)
    assert review['records'][0]['disagreement_criterion_ids'] == ['2', '8']
    assert review['overall']['per_criterion']['2']['na_disagreements'] == 1
    assert review['overall']['per_criterion']['8']['disagreements'] == 1
    assert review['by_language']['RU']['planned'] == 1
    assert 'не точность AI' in render_review(review)


def test_missing_annotation_remains_visible_and_cannot_be_finalized():
    a, b, manifest = fixture()
    b['annotations'] = []
    review = compare_annotations(a, b, manifest)
    assert review['overall']['planned'] == 1
    assert review['overall']['per_criterion']['1']['agreement'] is None
    assert review['records'][0]['source_statuses'] == ['complete', 'missing']
    with pytest.raises(ValueError, match='Both independent'):
        finalize(a, b, manifest, completed_review(a, b, manifest))


@pytest.mark.parametrize('problem', ['same_annotator', 'model_source', 'hash', 'answer_conflict', 'unknown_evidence', 'invalid_scale'])
def test_invalid_independent_inputs_are_rejected(problem):
    a, b, manifest = fixture()
    if problem == 'same_annotator':
        b = deepcopy(a)
    elif problem == 'model_source':
        b['source'] = 'model_proposal'
    elif problem == 'hash':
        b['annotations'][0]['transcript_hash'] = 'changed'
    elif problem == 'answer_conflict':
        b['annotations'][0]['answers']['1'] = 'no'
    elif problem == 'unknown_evidence':
        b['annotations'][0]['criteria'][0]['evidence'][0]['message_id'] = 'synthetic-ru:m9999'
    else:
        b['annotations'][0]['criteria'][5]['answer'] = 6
    with pytest.raises(ValueError):
        compare_annotations(a, b, manifest)


@pytest.mark.parametrize('problem', ['source_hash', 'original_edit', 'manifest_edit', 'record_removed',
                                  'reviewer_missing', 'rationale_missing', 'timezone_missing',
                                  'evidence_missing', 'illegal_na', 'pending_dispute'])
def test_invalid_adjudication_is_not_promoted_to_truth(problem):
    a, b, manifest = fixture()
    review = completed_review(a, b, manifest)
    decision = review['records'][0]['decision']
    if problem == 'source_hash': review['source_export_hashes'][0] = 'changed'
    elif problem == 'original_edit': a['annotations'][0]['criteria'][0]['comment'] = 'changed'
    elif problem == 'manifest_edit': manifest['records'][0]['messages'] += 1
    elif problem == 'record_removed': review['records'] = []
    elif problem == 'reviewer_missing': decision['reviewer_id'] = ''
    elif problem == 'rationale_missing': decision['rationale'] = ''
    elif problem == 'timezone_missing': decision['reviewed_at'] = '2026-11-07T12:30:00'
    elif problem == 'evidence_missing': decision['criteria'][0]['evidence'] = []
    elif problem == 'illegal_na': decision['criteria'][0]['answer'] = 'na'
    elif problem == 'pending_dispute': decision['criteria'][0]['disputed'] = True
    with pytest.raises(ValueError):
        finalize(a, b, manifest, review)


def test_pilot_excludes_nonpilot_and_holdout():
    a, b, manifest = fixture()
    second = deepcopy(manifest['records'][0])
    second.update(conversation_id='synthetic-en', language='EN', split='holdout', pilot=False)
    manifest['records'].append(second)
    assert compare_annotations(a, b, manifest)['overall']['planned'] == 1
    assert compare_annotations(a, b, manifest, 'holdout')['overall']['planned'] == 1
    assert compare_annotations(a, b, manifest, 'all')['overall']['planned'] == 2


def test_cli_compares_and_finalizes_private_files(tmp_path):
    a, b, manifest = fixture()
    for filename, obj in [('a.json', a), ('b.json', b), ('manifest.json', manifest)]:
        (tmp_path / filename).write_text(json.dumps(obj), encoding='utf-8')
    args = [str(tmp_path / n) for n in ('a.json', 'b.json', 'manifest.json')]
    review_path = tmp_path / 'review.json'
    output_path = tmp_path / 'references.json'
    subprocess.run([sys.executable, '-m', 'qa.adjudicate', 'compare', *args,
                    '--output', str(review_path), '--report', str(tmp_path / 'review.md')], check=True)
    assert json.loads(review_path.read_text(encoding='utf-8'))['overall']['planned'] == 1
    review_path.write_text(json.dumps(completed_review(a, b, manifest)), encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'qa.adjudicate', 'finalize', *args, str(review_path),
                    '--output', str(output_path)], check=True)
    assert len(json.loads(output_path.read_text(encoding='utf-8'))['references']) == 1
    initial = (tmp_path / 'a.json').read_bytes()
    rejected = subprocess.run([sys.executable, '-m', 'qa.adjudicate', 'compare', *args,
                    '--output', str(tmp_path / 'a.json')], capture_output=True)
    assert rejected.returncode != 0
    assert (tmp_path / 'a.json').read_bytes() == initial
