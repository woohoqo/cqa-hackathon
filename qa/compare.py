"""Compare immutable original AI responses with one explicitly selected reference.

Standard-library CLI: python compare.py input.json [--output report.json]
Input is an object containing config_version='organizer-v1', checklist_hash,
mode (mock/live), reference_source
(organizer_gt/human_adjudicated/model_proposal), split, and three arrays:
  manifest: conversation_id, language, split, transcript_hash
  references: conversation_id, source, status='complete', checklist_hash, transcript_hash,
              answers={'1':'yes', ..., '8':'no'}, optional total
  attempts: attempt_id, conversation_id, attempt_number (positive integer),
            mode, status ('success' or an error/pending status), checklist_hash,
            input_hash, original_ai={answers, optional total}, optional
            latency_ms, cost_usd, error_category

Answers use exact yes/no/na strings or integer scale values. Missing is not na.
Only the published default eight-criterion checklist is scored here. Its hash
binds references/runs; a custom rubric needs an explicit scoring adapter rather
than merely changing checklist_hash. score_answers is the adapter boundary.
Totals are recomputed from answers, never from a reported total or an appeal.
Only original_ai is read: final/final_ai/appeal fields cannot improve metrics.

Attempt_number=1 is primary, even when it failed or is missing. First valid success
is reported as a secondary retry view. No missing/invalid/failed attempt is
removed from coverage or end-to-end denominators. Conditional metrics expose
their denominators. No usable references means accuracy/MAE is null, not zero.
Model-proposal comparisons are explicitly named model_agreement, never truth.

This QA-only format is not a proposed production API contract. Inputs, raw
outputs and identifiers may be private: keep them outside the Git repository.
The generated report contains IDs and sanitized failure categories, not text,
audio URLs, prompts, exception messages, or original provider responses.
"""

import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path
import re
import statistics


WEIGHTS = {'1': 10, '2': 15, '3': 20, '4': 20, '5': 15, '6': 15, '7': 5, '8': 0}
SCALES = {'4': (0, 5, 10, 15, 20), '6': (0, 4, 7, 10)}
NA_ALLOWED = {'2', '3', '4', '7'}
SOURCES = {'organizer_gt', 'human_adjudicated', 'model_proposal'}
DEFAULT_CRITERIA = [
    {'id': key, 'type': 'scale' if key in SCALES else 'binary',
     'allowed': list(SCALES[key]) if key in SCALES else ['yes', 'no'],
     'allow_na': key in NA_ALLOWED, 'weight': weight,
     'max_score': max(SCALES[key]) if key in SCALES else 1}
    for key, weight in WEIGHTS.items()
]


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def validate_answers(answers):
    errors = []
    if not isinstance(answers, dict):
        return ['answers_not_object']
    if set(answers) != set(WEIGHTS):
        errors.append('missing_or_unknown_criterion_ids')
    for key in WEIGHTS:
        if key not in answers:
            continue
        value = answers[key]
        if value == 'na':
            if key not in NA_ALLOWED:
                errors.append('forbidden_na:' + key)
        elif key in SCALES:
            if type(value) is not int or value not in SCALES[key]:
                errors.append('invalid_scale:' + key)
        elif value not in ('yes', 'no'):
            errors.append('invalid_binary:' + key)
    return errors


def weighted_contribution(criterion, answer):
    """QA oracle contribution before N/A redistribution; returns Decimal."""
    if not isinstance(criterion, dict):
        raise ValueError('criterion must be an object')
    _require_text(criterion.get('id'), 'criterion.id')
    weight = criterion.get('weight')
    _optional_number(weight, 'criterion.weight')
    if weight is None:
        raise ValueError('criterion.weight is required')
    if type(criterion.get('allow_na')) is not bool:
        raise ValueError('criterion.allow_na must be boolean')
    allowed = criterion.get('allowed')
    kind = criterion.get('type')
    if not isinstance(allowed, list) or not allowed:
        raise ValueError('criterion.allowed must be a nonempty array')
    if kind == 'binary':
        if len(allowed) != 2 or set(allowed) != {'yes', 'no'}:
            raise ValueError('binary allowed values must be yes/no')
    elif kind == 'scale':
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in allowed):
            raise ValueError('scale values must be finite nonnegative numbers')
        if len(set(allowed)) != len(allowed):
            raise ValueError('duplicate scale values')
        maximum = criterion.get('max_score')
        if type(maximum) not in (int, float) or maximum <= 0 or maximum != max(allowed):
            raise ValueError('max_score must be the positive highest scale step')
    else:
        raise ValueError('criterion.type must be binary or scale')
    if answer == 'na':
        if not criterion['allow_na']:
            raise ValueError('forbidden N/A')
        return Decimal(0)
    if kind == 'binary':
        if answer not in ('yes', 'no'):
            raise ValueError('invalid binary answer')
        fraction = Decimal(int(answer == 'yes'))
    else:
        if type(answer) not in (int, float) or answer not in allowed:
            raise ValueError('invalid scale answer')
        fraction = Decimal(str(answer)) / Decimal(str(maximum))
    return Decimal(str(weight)) * fraction


def calculate_total(criteria, answers):
    """Generic QA oracle. It is not a production scorer or a custom-rubric report adapter."""
    if not isinstance(criteria, list) or not isinstance(answers, dict):
        raise ValueError('criteria/answers must be list/object')
    earned, denominator, seen = Decimal(0), Decimal(0), set()
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise ValueError('criterion must be an object')
        cid = criterion.get('id')
        _require_text(cid, 'criterion.id')
        if cid in seen:
            raise ValueError('duplicate criterion id')
        seen.add(cid)
        if cid not in answers:
            raise ValueError('missing criterion answer')
        earned += weighted_contribution(criterion, answers[cid])
        if answers[cid] != 'na':
            denominator += Decimal(str(criterion['weight']))
    if seen != set(answers):
        raise ValueError('unknown criterion answer')
    if denominator == 0:
        return None
    return float((earned * Decimal(100) / denominator).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP))


def score_answers(answers):
    """Default-checklist scoring oracle; only organizer-v1 is supported by reports."""
    errors = validate_answers(answers)
    if errors:
        raise ValueError(', '.join(errors))
    return calculate_total(DEFAULT_CRITERIA, answers)


def _require_text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(name + ' must be a nonempty string')


def _optional_number(value, name):
    if value is not None and (type(value) not in (int, float) or
                              not math.isfinite(value) or value < 0):
        raise ValueError(name + ' must be a finite nonnegative number or null')


def _safe_category(value):
    # Keep exception bodies, URLs, keys and other free text out of report categories.
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value) else 'other'


def _is_api_error(attempt):
    category = attempt.get('error_category') or ''
    return attempt.get('status') != 'success' and (
        category in {'api_error', 'provider_error', 'timeout', 'rate_limit', 'rate_limited',
                     'network_error', 'authentication_error', 'connection_error'} or
        bool(re.fullmatch(r'http_[45][0-9][0-9]', category)))


def _reported_total_mismatch(record, calculated):
    if 'total' not in record:
        return False
    value = record['total']
    if calculated is None:
        return value is not None
    return (type(value) not in (int, float) or not math.isfinite(value) or
            abs(value - calculated) > 0.000001)


def _assess_attempt(attempt, item, checklist_hash):
    problems = []
    if attempt.get('status') != 'success':
        problems.append('provider_status_not_success')
    if attempt.get('checklist_hash') != checklist_hash:
        problems.append('checklist_hash_mismatch')
    if attempt.get('input_hash') != item['transcript_hash']:
        problems.append('input_hash_mismatch')
    original = attempt.get('original_ai')
    answers = original.get('answers') if isinstance(original, dict) else None
    answer_errors = validate_answers(answers)
    problems.extend(answer_errors)
    valid = not problems
    calculated = score_answers(answers) if valid else None
    return {
        'attempt_id': attempt['attempt_id'],
        'attempt_number': attempt['attempt_number'],
        'provider_status': _safe_category(attempt.get('status')),
        'valid_success': valid,
        'problems': problems,
        'answers': answers,
        'total': calculated,
        'reported_total_mismatch': valid and _reported_total_mismatch(original, calculated),
    }


def _metrics(items, references, predictions):
    ids = [item['conversation_id'] for item in items]
    labelled = [cid for cid in ids if cid in references]
    comparable = [cid for cid in labelled if predictions.get(cid) is not None]
    result = {'planned': len(ids), 'labelled': len(labelled),
              'valid_predictions': sum(predictions.get(cid) is not None for cid in ids),
              'comparable': len(comparable),
              'reference_coverage': ratio(len(labelled), len(ids)),
              'prediction_coverage_on_labelled': ratio(len(comparable), len(labelled)),
              'unprocessed_labelled_ids': [cid for cid in labelled if predictions.get(cid) is None],
              'per_criterion': {}}
    for key in WEIGHTS:
        matched = sum(references[cid]['answers'][key] == predictions[cid]['answers'][key]
                      for cid in comparable)
        na_tp = sum(references[cid]['answers'][key] == 'na' and
                    predictions[cid]['answers'][key] == 'na' for cid in comparable)
        na_fp = sum(references[cid]['answers'][key] != 'na' and
                    predictions[cid]['answers'][key] == 'na' for cid in comparable)
        na_fn = sum(references[cid]['answers'][key] == 'na' and
                    predictions[cid]['answers'][key] != 'na' for cid in comparable)
        na_tn = len(comparable) - na_tp - na_fp - na_fn
        expected_na = sum(references[cid]['answers'][key] == 'na' for cid in labelled)
        result['per_criterion'][key] = {
            'matches': matched, 'comparable': len(comparable), 'labelled': len(labelled),
            'conditional_agreement': ratio(matched, len(comparable)),
            'end_to_end_correct': ratio(matched, len(labelled)),
            'na': {'tp': na_tp, 'false_na': na_fp, 'missed_na': na_fn, 'tn': na_tn,
                   'false_na_rate': ratio(na_fp, na_fp + na_tn),
                   'missed_na_rate': ratio(na_fn, na_fn + na_tp),
                   'reference_na_count': expected_na,
                   'unprocessed_reference_na': expected_na - na_tp - na_fn},
        }
    pairs = [cid for cid in comparable if references[cid]['total'] is not None and
             predictions[cid]['total'] is not None]
    result['total_score'] = {
        'mae': statistics.mean(abs(references[cid]['total'] - predictions[cid]['total'])
                               for cid in pairs) if pairs else None,
        'pairs': len(pairs), 'labelled': len(labelled),
        'uncomputable_comparable': len(comparable) - len(pairs)}
    tp = fp = fn = tn = 0
    for cid in comparable:
        actual = references[cid]['answers']['8'] == 'yes'
        predicted = predictions[cid]['answers']['8'] == 'yes'
        tp += actual and predicted
        fp += not actual and predicted
        fn += actual and not predicted
        tn += not actual and not predicted
    result['critical'] = {
        'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
        'precision': ratio(tp, tp + fp), 'recall': ratio(tp, tp + fn),
        'unprocessed_reference_positive_ids': [cid for cid in labelled
            if references[cid]['answers']['8'] == 'yes' and predictions.get(cid) is None],
    }
    return result


def _operations(items, attempts, assessed):
    ids = {item['conversation_id'] for item in items}
    selected = [a for a in attempts if a['conversation_id'] in ids]
    per_id = {cid: sorted([a for a in selected if a['conversation_id'] == cid],
                         key=lambda a: a['attempt_number']) for cid in ids}
    first_ok = sum(bool(group) and group[0]['attempt_number'] == 1 and
                   assessed[group[0]['attempt_id']]['valid_success']
                   for group in per_id.values())
    any_ok = sum(any(assessed[a['attempt_id']]['valid_success'] for a in group)
                 for group in per_id.values())
    known_costs = [a['cost_usd'] for a in selected if a.get('cost_usd') is not None]
    latencies = sorted(a['latency_ms'] for a in selected if a.get('latency_ms') is not None)
    errors = Counter(_safe_category(a.get('error_category') or 'unspecified') for a in selected
                     if a.get('status') != 'success')
    api_errors = Counter(_safe_category(a['error_category']) for a in selected if _is_api_error(a))
    forbidden = Counter(problem.split(':', 1)[1]
                        for a in selected for problem in assessed[a['attempt_id']]['problems']
                        if problem.startswith('forbidden_na:'))
    serial_elapsed = {cid: sum(a['latency_ms'] for a in group)
                      for cid, group in per_id.items() if group and
                      all(a.get('latency_ms') is not None for a in group)}
    return {
        'planned': len(ids), 'attempted': sum(bool(group) for group in per_id.values()),
        'not_started_ids': sorted(cid for cid, group in per_id.items() if not group),
        'missing_primary_attempt_ids': sorted(cid for cid, group in per_id.items()
            if group and group[0]['attempt_number'] != 1),
        'attempt_number_gaps': {cid: [number for number in range(1, group[-1]['attempt_number'] + 1)
            if number not in {a['attempt_number'] for a in group}]
            for cid, group in per_id.items() if group and
            len(group) != group[-1]['attempt_number']},
        'attempts': len(selected), 'first_attempt_successes': first_ok,
        'first_attempt_success_rate': ratio(first_ok, len(ids)),
        'eventual_successes': any_ok, 'eventual_success_rate': ratio(any_ok, len(ids)),
        'non_success_attempts': sum(errors.values()), 'error_categories': dict(errors),
        'api_error_attempts': sum(api_errors.values()), 'api_error_categories': dict(api_errors),
        'error_category_note': 'API classification recognizes timeout, rate_limit/rate_limited, network/connection/authentication/provider/api_error and http_4xx/http_5xx; all other non-success states remain in error_categories.',
        'invalid_success_attempts': sum(a.get('status') == 'success' and
            not assessed[a['attempt_id']]['valid_success'] for a in selected),
        'forbidden_na_by_criterion': dict(forbidden),
        'reported_total_mismatch_attempt_ids': [a['attempt_id'] for a in selected
            if assessed[a['attempt_id']]['reported_total_mismatch']],
        'cost': {'total_usd': sum(known_costs) if len(known_costs) == len(selected) else None,
                 'known_subtotal_usd': sum(known_costs), 'known_attempts': len(known_costs),
                 'attempts': len(selected), 'coverage': ratio(len(known_costs), len(selected)),
                 'per_planned_dialogue_usd': ratio(sum(known_costs), len(ids))
                    if len(known_costs) == len(selected) else None},
        'latency': {'known_attempts': len(latencies), 'attempts': len(selected),
                    'median_ms': statistics.median(latencies) if latencies else None,
                    'p95_ms': latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None,
                    'max_ms': max(latencies) if latencies else None,
                    'attempt_time_sum_by_dialogue_ms': serial_elapsed,
                    'note': 'Sum of reported attempt durations; excludes retry backoff, queue and UI time.'},
        'attempt_outcomes': [{key: value for key, value in assessed[a['attempt_id']].items()
                             if key not in ('answers', 'total')}
                            | {'conversation_id': a['conversation_id']} for a in selected],
    }


def compare_report(payload):
    if not isinstance(payload, dict):
        raise ValueError('input must be an object')
    for key in ('config_version', 'checklist_hash', 'mode', 'reference_source', 'split'):
        _require_text(payload.get(key), key)
    if payload['config_version'] != 'organizer-v1':
        raise ValueError('reports support config_version=organizer-v1 only; custom rubric needs an adapter')
    if payload['mode'] not in ('mock', 'live'):
        raise ValueError('mode must be mock or live')
    if payload['reference_source'] not in SOURCES:
        raise ValueError('unsupported reference_source')
    for key in ('manifest', 'references', 'attempts'):
        if not isinstance(payload.get(key), list) or not all(isinstance(x, dict) for x in payload[key]):
            raise ValueError(key + ' must be an array of objects')
    manifest = {}
    for item in payload['manifest']:
        for key in ('conversation_id', 'language', 'split', 'transcript_hash'):
            _require_text(item.get(key), 'manifest.' + key)
        cid = item['conversation_id']
        if cid in manifest:
            raise ValueError('duplicate manifest conversation_id')
        manifest[cid] = item
    seen_attempts, seen_numbers = set(), set()
    for attempt in payload['attempts']:
        for key in ('attempt_id', 'conversation_id', 'mode', 'status'):
            _require_text(attempt.get(key), 'attempt.' + key)
        if attempt['mode'] not in ('mock', 'live'):
            raise ValueError('attempt.mode must be mock or live')
        number = attempt.get('attempt_number')
        if type(number) is not int or number < 1:
            raise ValueError('attempt_number must be a positive integer')
        identity = (attempt['conversation_id'], attempt['mode'], number)
        if attempt['attempt_id'] in seen_attempts or identity in seen_numbers:
            raise ValueError('duplicate attempt_id or conversation/mode/attempt_number')
        seen_attempts.add(attempt['attempt_id'])
        seen_numbers.add(identity)
        _optional_number(attempt.get('latency_ms'), 'latency_ms')
        _optional_number(attempt.get('cost_usd'), 'cost_usd')
        if attempt.get('error_category') is not None:
            _require_text(attempt['error_category'], 'error_category')
    items = [item for item in manifest.values() if item['split'] == payload['split']]
    ids = {item['conversation_id'] for item in items}
    references, reference_issues, seen_refs = {}, {}, set()
    selected_source_count = 0
    for ref in payload['references']:
        _require_text(ref.get('conversation_id'), 'reference.conversation_id')
        _require_text(ref.get('source'), 'reference.source')
        if ref.get('checklist_hash') is not None:
            _require_text(ref['checklist_hash'], 'reference.checklist_hash')
        identity = (ref['conversation_id'], ref['source'], ref.get('checklist_hash'))
        if identity in seen_refs:
            raise ValueError('duplicate reference for conversation/source/checklist')
        seen_refs.add(identity)
        cid = ref['conversation_id']
        if ref['source'] != payload['reference_source'] or cid not in ids:
            continue
        selected_source_count += 1
        if cid in reference_issues or cid in references:
            raise ValueError('ambiguous reference versions for selected conversation/source')
        problems = []
        if ref.get('status') != 'complete':
            problems.append('reference_not_complete')
        if ref.get('checklist_hash') != payload['checklist_hash']:
            problems.append('reference_checklist_hash_mismatch')
        if ref.get('transcript_hash') != manifest[cid]['transcript_hash']:
            problems.append('reference_transcript_hash_mismatch')
        problems.extend(validate_answers(ref.get('answers')))
        if problems:
            reference_issues[cid] = problems
        else:
            total = score_answers(ref['answers'])
            references[cid] = {'answers': ref['answers'], 'total': total,
                               'reported_total_mismatch': _reported_total_mismatch(ref, total)}
    attempts = [a for a in payload['attempts'] if a['mode'] == payload['mode']]
    assessed = {a['attempt_id']: _assess_attempt(a, manifest[a['conversation_id']], payload['checklist_hash'])
                for a in attempts if a['conversation_id'] in ids}
    primary, secondary = {}, {}
    for cid in ids:
        group = sorted([a for a in attempts if a['conversation_id'] == cid], key=lambda a: a['attempt_number'])
        if group and group[0]['attempt_number'] == 1 and assessed[group[0]['attempt_id']]['valid_success']:
            primary[cid] = assessed[group[0]['attempt_id']]
        valid = [assessed[a['attempt_id']] for a in group if assessed[a['attempt_id']]['valid_success']]
        if valid:
            secondary[cid] = valid[0]

    def section(subset):
        return {'primary_first_attempt': _metrics(subset, references, primary),
                'secondary_first_valid_success': _metrics(subset, references, secondary),
                'operations': _operations(subset, attempts, assessed)}

    return {
        'schema_version': 1, 'config_version': payload['config_version'],
        'mode': payload['mode'], 'split': payload['split'],
        'checklist_hash': payload['checklist_hash'], 'reference_source': payload['reference_source'],
        'comparison_kind': 'model_agreement' if payload['reference_source'] == 'model_proposal' else 'reference_accuracy',
        'scoring_rubric': 'default_organizer_checklist_8_criteria',
        'attempt_unit': ('backend_evaluation_job' if isinstance(payload.get('adapter_notes'), dict)
                         and payload['adapter_notes'].get('attempt_unit') == 'backend_evaluation_job'
                         else 'recorded_attempt'),
        'provider_call_count': None,
        'notes': ['Accuracy is not established when labelled=0.',
                  'Only immutable original_ai responses are compared; appeal outcomes are ignored.',
                  'Mock results do not establish live model quality.',
                  'Provider call count/internal retries are unknown unless a complete provider ledger is supplied; backend jobs are not provider calls.'],
        'reference_diagnostics': {
            'selected_source_records': selected_source_count, 'usable_records': len(references),
            'invalid_or_unfinished': reference_issues,
            'missing_reference_ids': sorted(ids - set(references) - set(reference_issues)),
            'reported_total_mismatch_ids': sorted(cid for cid, ref in references.items() if ref['reported_total_mismatch']),
            'orphan_reference_ids': sorted({r['conversation_id'] for r in payload['references']} - set(manifest)),
        },
        'excluded_attempts': {
            'other_mode_attempt_ids': [a['attempt_id'] for a in payload['attempts'] if a['mode'] != payload['mode']],
            'other_split_attempt_ids': [a['attempt_id'] for a in attempts if a['conversation_id'] in manifest and a['conversation_id'] not in ids],
            'orphan_attempt_ids': [a['attempt_id'] for a in attempts if a['conversation_id'] not in manifest],
            'orphan_attempt_outcomes': [{'attempt_id': a['attempt_id'],
                'conversation_id': a['conversation_id'], 'provider_status': _safe_category(a['status']),
                'error_category': _safe_category(a.get('error_category') or 'unspecified'),
                'latency_ms': a.get('latency_ms'), 'cost_usd': a.get('cost_usd')}
                for a in attempts if a['conversation_id'] not in manifest],
        },
        'overall': section(items),
        'by_language': {language: section([i for i in items if i['language'] == language])
                        for language in sorted({i['language'] for i in items})},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = compare_report(json.loads(args.input.read_text(encoding='utf-8-sig')))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')


if __name__ == '__main__':
    main()
