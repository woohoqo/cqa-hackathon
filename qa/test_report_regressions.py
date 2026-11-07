"""Synthetic report regressions; no real labels, API calls, or appeals as truth."""
import json

import pytest

from qa.compare import compare_report
from qa.export_run import adapt_export
from qa.render_report import render
from qa.test_compare import payload, add_dialogue
from qa.test_export_run import fixture


def test_saved_total_regression_is_visible_despite_correct_answers():
    data = payload()
    data['attempts'][0]['original_ai']['total'] = 0
    report = compare_report(data)
    primary = report['overall']['primary_first_attempt']
    assert primary['total_score']['mae'] == 0
    assert primary['stored_original_total_score']['mae'] == 85.38
    text = render(report)
    assert 'original_score.total: 85.38' in text
    assert 'по всем попыткам: 1 / 1' in text
    assert 'run-ru-1' in text
    assert 'пересчитанного QA по исходным ответам' in text


def test_absent_total_retains_denominator_and_is_not_zero():
    data = payload()
    del data['attempts'][0]['original_ai']['total']
    score = compare_report(data)['overall']['primary_first_attempt']['stored_original_total_score']
    assert score['mae'] is None
    assert score['pairs'] == 0 and score['labelled'] == 1
    assert score['missing_count'] == 1 and score['invalid_count'] == 0
    assert score['coverage_on_labelled'] == 0


@pytest.mark.parametrize('value', [None, True, '85.38', float('nan'), float('inf'), -1, 101])
def test_invalid_saved_totals_are_counted_and_sanitized(value):
    data = payload()
    data['attempts'][0]['original_ai']['total'] = value
    report = compare_report(data)
    score = report['overall']['primary_first_attempt']['stored_original_total_score']
    assert score['invalid_count'] == 1 and score['missing_count'] == 0
    assert score['mae'] is None and score['coverage_on_labelled'] == 0
    json.dumps(report, allow_nan=False)


def test_saved_total_coverage_keeps_failed_and_missing_dialogues():
    data = payload()
    add_dialogue(data, 'KZ')
    add_dialogue(data, 'EN')
    del data['attempts'][1]['original_ai']['total']
    data['attempts'][2]['status'] = 'failed'
    report = compare_report(data)
    score = report['overall']['primary_first_attempt']['stored_original_total_score']
    assert score['pairs'] == 1 and score['labelled'] == 3 and score['comparable'] == 2
    assert score['missing_count'] == 1 and score['unprocessed_labelled_count'] == 1
    assert score['coverage_on_labelled'] == pytest.approx(1 / 3)
    assert report['by_language']['EN']['primary_first_attempt']['stored_original_total_score']['mae'] is None


def test_no_references_means_no_mae_for_either_total():
    data = payload()
    data['references'] = []
    primary = compare_report(data)['overall']['primary_first_attempt']
    assert primary['total_score']['mae'] is None
    assert primary['stored_original_total_score']['mae'] is None


@pytest.mark.parametrize('code,is_api', [
    ('provider_rate_limit', True), ('provider_unavailable', True),
    ('provider_timeout', True), ('provider_connection_failed', True),
    ('provider_authentication_failed', True), ('provider_request_failed', True),
    ('provider_response_too_large', True), ('invalid_provider_response', True),
    ('provider_daily_quota_exhausted', True),
    ('provider_not_configured', False), ('invalid_ai_configuration', False),
    ('invalid_ai_response', False),
])
def test_provider_error_classification_through_export(code, is_api):
    exported, context = fixture()
    exported['evaluations'][0].update(
        status='failed', ai_draft=None, original_score=None,
        error={'code': code, 'message': 'SYNTHETIC safe failure'})
    report = compare_report(adapt_export(exported, context))
    for section in (report['overall'], report['by_language']['EN']):
        ops = section['operations']
        assert ops['attempts'] == ops['non_success_attempts'] == 1
        assert ops['first_attempt_successes'] == 0
        assert ops['error_categories'] == {code: 1}
        assert ops['api_error_attempts'] == int(is_api)
        assert ops['api_error_categories'] == ({code: 1} if is_api else {})
        assert ops['cost']['total_usd'] is None
