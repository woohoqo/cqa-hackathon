"""Synthetic checks of the QA report/oracle, not tests of the application or an API."""

from copy import deepcopy
from decimal import Decimal
import json
import unittest

from qa.compare import (DEFAULT_CRITERIA, calculate_total, compare_report, score_answers,
                     weighted_contribution)


def answers(**changes):
    result = {'1': 'yes', '2': 'na', '3': 'na', '4': 15,
              '5': 'yes', '6': 7, '7': 'yes', '8': 'no'}
    result.update(changes)
    return result


def payload():
    return {
        'config_version': 'organizer-v1', 'checklist_hash': 'checklist-v1-hash',
        'mode': 'mock', 'reference_source': 'human_adjudicated', 'split': 'holdout',
        'manifest': [{'conversation_id': 'synthetic-ru', 'language': 'RU',
                      'split': 'holdout', 'transcript_hash': 'text-v1-hash'}],
        'references': [{'conversation_id': 'synthetic-ru', 'source': 'human_adjudicated',
                        'status': 'complete', 'checklist_hash': 'checklist-v1-hash',
                        'transcript_hash': 'text-v1-hash', 'answers': answers(), 'total': 85.38}],
        'attempts': [{'attempt_id': 'run-ru-1', 'conversation_id': 'synthetic-ru',
                      'attempt_number': 1, 'mode': 'mock', 'status': 'success',
                      'checklist_hash': 'checklist-v1-hash', 'input_hash': 'text-v1-hash',
                      'original_ai': {'answers': answers(), 'total': 85.38},
                      'latency_ms': 100, 'cost_usd': 0.001, 'error_category': None}],
    }


def add_dialogue(data, language, critical='no', with_attempt=True):
    cid = 'synthetic-' + language.lower()
    data['manifest'].append({'conversation_id': cid, 'language': language,
                             'split': 'holdout', 'transcript_hash': 'text-' + language})
    ref = deepcopy(data['references'][0])
    ref.update(conversation_id=cid, transcript_hash='text-' + language)
    ref['answers']['8'] = critical
    data['references'].append(ref)
    if with_attempt:
        attempt = deepcopy(data['attempts'][0])
        attempt.update(conversation_id=cid, attempt_id='run-' + language,
                       input_hash='text-' + language)
        attempt['original_ai']['answers']['8'] = critical
        data['attempts'].append(attempt)


class ScoringTests(unittest.TestCase):
    def test_permitted_scale_steps_and_na(self):
        for criterion, values in {'4': [0, 5, 10, 15, 20], '6': [0, 4, 7, 10]}.items():
            for value in values:
                with self.subTest(criterion=criterion, value=value):
                    self.assertIsInstance(score_answers(answers(**{criterion: value})), float)
        for key in ('2', '3', '4', '7'):
            with self.subTest(na=key):
                self.assertIsInstance(score_answers(answers(**{key: 'na'})), float)

    def test_invalid_scales_na_and_types_are_rejected(self):
        invalid = [('4', 7), ('6', 5), ('6', True), ('6', 7.0), ('1', 'na'),
                   ('5', 'na'), ('6', 'na'), ('8', 'na'), ('1', None), ('1', True),
                   ('4', float('nan')), ('6', float('inf'))]
        for key, value in invalid:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                score_answers(answers(**{key: value}))

    def test_na_redistributes_weights(self):
        all_full = {'1': 'yes', '2': 'yes', '3': 'yes', '4': 20,
                    '5': 'yes', '6': 10, '7': 'yes', '8': 'no'}
        self.assertEqual(score_answers(all_full), 100)
        all_full.update({'2': 'na', '3': 'na'})
        self.assertEqual(score_answers(all_full), 100)
        all_full['1'] = 'no'
        self.assertEqual(score_answers(all_full), 84.62)  # 55 / 65, not 55 / 100

    def test_criterion_six_contribution_is_ten_point_five(self):
        six = next(c for c in DEFAULT_CRITERIA if c['id'] == '6')
        self.assertEqual(weighted_contribution(six, 7), Decimal('10.5'))

    def test_required_eighty_five_point_thirty_eight_example(self):
        self.assertEqual(score_answers(answers()), 85.38)

    def test_critical_flag_does_not_change_total(self):
        self.assertEqual(score_answers(answers(**{'8': 'yes'})), 85.38)

    def test_no_applicable_weight_yields_no_total(self):
        custom = [{'id': 'optional', 'type': 'binary', 'allowed': ['yes', 'no'],
                   'allow_na': True, 'weight': 25, 'max_score': 1}]
        self.assertIsNone(calculate_total(custom, {'optional': 'na'}))
        custom[0]['weight'] = 0
        self.assertIsNone(calculate_total(custom, {'optional': 'yes'}))
        self.assertIsNone(calculate_total([], {}))

    def test_generic_oracle_rejects_invalid_weights_and_duplicate_ids(self):
        criterion = deepcopy(DEFAULT_CRITERIA[0])
        criterion['weight'] = -1
        with self.assertRaises(ValueError):
            calculate_total([criterion], {'1': 'yes'})
        with self.assertRaises(ValueError):
            calculate_total([DEFAULT_CRITERIA[0], DEFAULT_CRITERIA[0]], {'1': 'yes'})


class ComparisonTests(unittest.TestCase):
    def test_backend_job_unit_does_not_imply_provider_call_count(self):
        data = payload()
        data['adapter_notes'] = {'attempt_unit': 'backend_evaluation_job'}
        report = compare_report(data)
        self.assertEqual(report['attempt_unit'], 'backend_evaluation_job')
        self.assertIsNone(report['provider_call_count'])

    def test_empty_report_is_not_claimed_model_quality(self):
        from qa.render_report import render
        data = payload(); data['references'] = []; data['attempts'] = []
        report = compare_report(data)
        rendered = render(report)
        self.assertIn('Точность не установлена', rendered)
        self.assertIn('Запусков нет', rendered)
        self.assertNotIn('0.00%', rendered)

    def test_perfect_synthetic_pair(self):
        report = compare_report(payload())
        primary = report['overall']['primary_first_attempt']
        self.assertEqual(primary['per_criterion']['6']['conditional_agreement'], 1)
        self.assertEqual(primary['total_score'], {'mae': 0, 'pairs': 1,
                         'labelled': 1, 'uncomputable_comparable': 0})
        self.assertEqual(report['overall']['operations']['first_attempt_success_rate'], 1)
        json.dumps(report, allow_nan=False)

    def test_failed_first_attempt_remains_primary_even_after_successful_retry(self):
        data = payload()
        retry = deepcopy(data['attempts'][0])
        retry.update(attempt_id='retry', attempt_number=2, latency_ms=50)
        first = data['attempts'][0]
        first.update(status='error', error_category='timeout', latency_ms=200)
        first.pop('original_ai')
        data['attempts'] = [retry, first]  # Array order must not select the retry.
        report = compare_report(data)['overall']
        primary = report['primary_first_attempt']
        self.assertIsNone(primary['per_criterion']['1']['conditional_agreement'])
        self.assertEqual(primary['per_criterion']['1']['end_to_end_correct'], 0)
        self.assertEqual(report['secondary_first_valid_success']['per_criterion']['1']['conditional_agreement'], 1)
        self.assertEqual(report['operations']['first_attempt_success_rate'], 0)
        self.assertEqual(report['operations']['eventual_success_rate'], 1)
        self.assertEqual(report['operations']['non_success_attempts'], 1)
        self.assertEqual(report['operations']['api_error_attempts'], 1)
        self.assertEqual(report['operations']['cost']['total_usd'], 0.002)
        self.assertEqual(report['operations']['latency']['attempt_time_sum_by_dialogue_ms']['synthetic-ru'], 250)

    def test_missing_first_attempt_is_not_replaced_by_retry(self):
        data = payload()
        data['attempts'][0]['attempt_number'] = 2
        report = compare_report(data)['overall']
        self.assertEqual(report['primary_first_attempt']['comparable'], 0)
        self.assertEqual(report['secondary_first_valid_success']['comparable'], 1)
        self.assertEqual(report['operations']['missing_primary_attempt_ids'], ['synthetic-ru'])
        self.assertEqual(report['operations']['attempt_number_gaps'], {'synthetic-ru': [1]})
        self.assertEqual(report['operations']['first_attempt_successes'], 0)

    def test_manifest_missing_attempts_affect_denominator_and_critical_coverage(self):
        data = payload()
        add_dialogue(data, 'KZ', critical='yes', with_attempt=False)
        report = compare_report(data)
        overall = report['overall']
        self.assertEqual(overall['operations']['first_attempt_success_rate'], 0.5)
        self.assertEqual(overall['operations']['not_started_ids'], ['synthetic-kz'])
        self.assertEqual(overall['primary_first_attempt']['critical']['unprocessed_reference_positive_ids'], ['synthetic-kz'])
        self.assertEqual(overall['primary_first_attempt']['per_criterion']['1']['end_to_end_correct'], 0.5)
        self.assertEqual(report['by_language']['KZ']['primary_first_attempt']['labelled'], 1)
        self.assertIsNone(report['by_language']['KZ']['primary_first_attempt']['total_score']['mae'])

    def test_no_reference_means_no_accuracy(self):
        data = payload()
        data['references'] = []
        report = compare_report(data)
        primary = report['overall']['primary_first_attempt']
        self.assertEqual(primary['labelled'], 0)
        self.assertIsNone(primary['per_criterion']['1']['conditional_agreement'])
        self.assertIsNone(primary['per_criterion']['1']['end_to_end_correct'])
        self.assertIsNone(primary['total_score']['mae'])
        self.assertEqual(report['reference_diagnostics']['missing_reference_ids'], ['synthetic-ru'])

    def test_no_mock_live_mixing(self):
        data = payload()
        data['mode'] = 'live'
        report = compare_report(data)
        self.assertEqual(report['overall']['operations']['attempts'], 0)
        self.assertEqual(report['excluded_attempts']['other_mode_attempt_ids'], ['run-ru-1'])
        self.assertEqual(report['overall']['primary_first_attempt']['per_criterion']['1']['end_to_end_correct'], 0)

    def test_na_errors_have_correct_denominators(self):
        data = payload()
        data['attempts'][0]['original_ai']['answers'].update({'2': 'yes', '7': 'na'})
        criteria = compare_report(data)['overall']['primary_first_attempt']['per_criterion']
        self.assertEqual(criteria['2']['na']['missed_na'], 1)
        self.assertEqual(criteria['2']['na']['missed_na_rate'], 1)
        self.assertEqual(criteria['7']['na']['false_na'], 1)
        self.assertEqual(criteria['7']['na']['false_na_rate'], 1)

    def test_forbidden_na_invalidates_whole_attempt_and_remains_visible(self):
        data = payload()
        data['attempts'][0]['original_ai']['answers']['1'] = 'na'
        report = compare_report(data)['overall']
        self.assertEqual(report['operations']['invalid_success_attempts'], 1)
        self.assertEqual(report['operations']['forbidden_na_by_criterion'], {'1': 1})
        self.assertEqual(report['primary_first_attempt']['comparable'], 0)

    def test_hash_mismatches_do_not_count_as_success(self):
        for key in ('input_hash', 'checklist_hash'):
            data = payload()
            data['attempts'][0][key] = 'changed'
            with self.subTest(key=key):
                report = compare_report(data)['overall']
                self.assertEqual(report['operations']['invalid_success_attempts'], 1)
                self.assertEqual(report['primary_first_attempt']['comparable'], 0)

    def test_reference_hash_mismatch_or_missing_is_not_ground_truth(self):
        for key in ('transcript_hash', 'checklist_hash'):
            for value in ('changed', None):
                data = payload()
                data['references'][0][key] = value
                with self.subTest(key=key, value=value):
                    report = compare_report(data)
                    self.assertEqual(report['overall']['primary_first_attempt']['labelled'], 0)
                    self.assertIn('synthetic-ru', report['reference_diagnostics']['invalid_or_unfinished'])

    def test_sources_are_separated_and_model_comparison_is_not_accuracy(self):
        data = payload()
        proposal = deepcopy(data['references'][0])
        proposal.update(source='model_proposal')
        proposal['answers']['1'] = 'no'
        data['references'].append(proposal)
        report = compare_report(data)
        self.assertEqual(report['overall']['primary_first_attempt']['per_criterion']['1']['conditional_agreement'], 1)
        data['reference_source'] = 'model_proposal'
        report = compare_report(data)
        self.assertEqual(report['comparison_kind'], 'model_agreement')
        self.assertEqual(report['overall']['primary_first_attempt']['per_criterion']['1']['conditional_agreement'], 0)

    def test_unfinished_annotation_is_not_labelled(self):
        data = payload()
        data['references'][0]['status'] = 'draft'
        report = compare_report(data)
        self.assertEqual(report['overall']['primary_first_attempt']['labelled'], 0)
        self.assertIn('reference_not_complete', report['reference_diagnostics']['invalid_or_unfinished']['synthetic-ru'])

    def test_appeal_does_not_change_original_ai_accuracy(self):
        data = payload()
        data['attempts'][0]['original_ai']['answers']['1'] = 'no'
        before = compare_report(data)
        data['attempts'][0]['final_ai'] = {'answers': answers(), 'total': 85.38}
        data['attempts'][0]['final'] = {'answers': answers(), 'total': 85.38}
        self.assertEqual(compare_report(data), before)
        data['attempts'][0].pop('original_ai')
        self.assertEqual(compare_report(data)['overall']['operations']['invalid_success_attempts'], 1)

    def test_reported_total_is_not_trusted_for_mae(self):
        data = payload()
        data['attempts'][0]['original_ai']['answers']['1'] = 'no'
        # Reported totals still claim a perfect match, but answers differ by 15.38 points.
        report = compare_report(data)['overall']
        self.assertAlmostEqual(report['primary_first_attempt']['total_score']['mae'], 15.38)
        self.assertEqual(report['operations']['reported_total_mismatch_attempt_ids'], ['run-ru-1'])

    def test_unknown_cost_is_not_zero_and_failed_cost_is_counted(self):
        data = payload()
        data['attempts'][0]['cost_usd'] = None
        cost = compare_report(data)['overall']['operations']['cost']
        self.assertIsNone(cost['total_usd'])
        self.assertEqual(cost['known_subtotal_usd'], 0)
        self.assertEqual(cost['coverage'], 0)
        data['attempts'][0].update(status='error', error_category='http_500', cost_usd=0.01)
        self.assertEqual(compare_report(data)['overall']['operations']['cost']['total_usd'], 0.01)

    def test_language_results_and_critical_confusion(self):
        data = payload()
        add_dialogue(data, 'KZ', critical='yes')
        add_dialogue(data, 'EN')
        data['attempts'][0]['original_ai']['answers']['8'] = 'yes'  # RU false positive
        data['attempts'][1]['original_ai']['answers']['8'] = 'no'   # KZ false negative
        report = compare_report(data)
        critical = report['overall']['primary_first_attempt']['critical']
        self.assertEqual((critical['tp'], critical['fp'], critical['fn'], critical['tn']), (0, 1, 1, 1))
        self.assertEqual(report['by_language']['RU']['primary_first_attempt']['critical']['fp'], 1)
        self.assertEqual(report['by_language']['KZ']['primary_first_attempt']['critical']['fn'], 1)
        self.assertEqual(report['by_language']['EN']['primary_first_attempt']['critical']['tn'], 1)

    def test_duplicates_are_rejected(self):
        for collection in ('manifest', 'references', 'attempts'):
            data = payload()
            data[collection].append(deepcopy(data[collection][0]))
            with self.subTest(collection=collection), self.assertRaises(ValueError):
                compare_report(data)
        data = payload()
        duplicate = deepcopy(data['attempts'][0])
        duplicate['attempt_id'] = 'different-id-same-attempt-number'
        data['attempts'].append(duplicate)
        with self.assertRaises(ValueError):
            compare_report(data)

    def test_other_split_and_orphan_ids_are_explicit(self):
        data = payload()
        add_dialogue(data, 'EN')
        data['manifest'][1]['split'] = 'tuning'
        orphan = deepcopy(data['attempts'][0])
        orphan.update(attempt_id='orphan', conversation_id='absent')
        data['attempts'].append(orphan)
        report = compare_report(data)
        self.assertEqual(report['overall']['operations']['planned'], 1)
        self.assertEqual(report['excluded_attempts']['other_split_attempt_ids'], ['run-EN'])
        self.assertEqual(report['excluded_attempts']['orphan_attempt_ids'], ['orphan'])

    def test_invalid_cost_and_latency_and_custom_report_version_are_rejected(self):
        for key, value in [('cost_usd', -1), ('cost_usd', float('nan')),
                           ('latency_ms', float('inf')), ('latency_ms', True)]:
            data = payload()
            data['attempts'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                compare_report(data)
        data = payload()
        data['config_version'] = 'custom-checklist'
        with self.assertRaises(ValueError):
            compare_report(data)

    def test_missing_criterion_invalidates_response(self):
        data = payload()
        del data['attempts'][0]['original_ai']['answers']['5']
        report = compare_report(data)['overall']
        self.assertEqual(report['operations']['invalid_success_attempts'], 1)
        self.assertEqual(report['primary_first_attempt']['per_criterion']['1']['end_to_end_correct'], 0)

    def test_reports_omit_raw_errors_and_provider_payloads(self):
        data = payload()
        data['attempts'][0].update(status='error', error_category='https://private.invalid/?key=secret')
        data['attempts'][0]['original_ai']['debug'] = 'DO_NOT_EXPORT'
        rendered = json.dumps(compare_report(data))
        self.assertNotIn('secret', rendered)
        self.assertNotIn('DO_NOT_EXPORT', rendered)


if __name__ == '__main__':
    unittest.main()
