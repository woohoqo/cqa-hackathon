"""Adapt a backend JSON export plus a recorded run-context sidecar to QA input.

Usage from repository root:
  python -m qa.export_run export.json run-context.json --output comparison-input.json

The backend export alone is insufficient: it has no input hash, test/live mode,
attempt ordinal, per-call latency, tokens, or price. Never infer those fields
from row order, created_at/updated_at, model names, appeals, or final_score.

run-context contains compare.py's config_version='organizer-v1', checklist_hash,
mode, reference_source, split, manifest, references, plus evaluation_provenance:
  [{evaluation_id, backend_conversation_id, conversation_id, attempt_number,
    mode, checklist_hash, input_hash, checklist_snapshot_hash}]
conversation_id is the manifest ID; backend_conversation_id is the UUID returned
by the backend. Capture this mapping, the attempt ordinal, and hashes when the
job is submitted. checklist_snapshot_hash is snapshot_hash(snapshot) below.
Every exported evaluation must have provenance; duplicate IDs are rejected.
Provenance whose evaluation is absent remains visible with status=missing_export.

Only ai_draft.criteria and original_score.total are read. Human final_criteria,
final_score, appeals, and decisions are never substituted for original AI output.
Backend timestamps are NOT model latency. latency_ms/cost_usd are always null.
Each output attempt is a backend evaluation job, not a claim that the provider
made one call. Internal provider retries and total call count remain unknown.

This adapter deliberately supports the exact initial organizer checklist only.
It compares snapshot content with the repository's official_checklist() and
rejects changed/custom definitions; a matching ID/hash alone is not sufficient.
Keep exports, provenance, references, and comparison input private/outside Git.
"""

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path


def snapshot_hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _required_text(item, key):
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError('Missing/invalid provenance field: ' + key)
    return value


def _convert_answers(ai_draft):
    if not isinstance(ai_draft, dict) or not isinstance(ai_draft.get('criteria'), list):
        return None, 'missing_original_ai'
    answers = {}
    for criterion in ai_draft['criteria']:
        if not isinstance(criterion, dict):
            return None, 'invalid_original_criteria'
        cid = criterion.get('criterion_id')
        if not isinstance(cid, str) or not cid or cid in answers:
            return None, 'duplicate_or_invalid_original_criterion'
        value = criterion.get('value')
        if type(value) is bool:
            value = 'yes' if value else 'no'
        elif value == 'N/A':
            value = 'na'
        elif type(value) is float and math.isfinite(value) and value.is_integer():
            value = int(value)
        answers[cid] = value
    return answers, None


def adapt_export(exported, context):
    from backend.app.default_checklist import official_checklist

    if not isinstance(exported, dict) or exported.get('schema_version') != '1':
        raise ValueError('Expected backend export schema_version=1')
    if not isinstance(context, dict) or context.get('config_version') != 'organizer-v1':
        raise ValueError('Only config_version=organizer-v1 is supported')
    for key in ('checklist_hash', 'mode', 'reference_source', 'split'):
        _required_text(context, key)
    if context['mode'] not in ('mock', 'live'):
        raise ValueError('Explicit mock/live mode is required')
    for key in ('manifest', 'references', 'evaluation_provenance'):
        if not isinstance(context.get(key), list):
            raise ValueError('Missing run-context list: ' + key)
    rows = exported.get('evaluations')
    if not isinstance(rows, list):
        raise ValueError('Export evaluations must be an array')
    indexed, provenances, seen_attempts = {}, {}, set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Export evaluation must be an object')
        eid = _required_text(row, 'id')
        if eid in indexed:
            raise ValueError('Duplicate exported evaluation ID')
        indexed[eid] = row
    for item in context['evaluation_provenance']:
        if not isinstance(item, dict):
            raise ValueError('Provenance must be an object')
        for key in ('evaluation_id', 'backend_conversation_id', 'conversation_id', 'mode',
                    'checklist_hash', 'input_hash', 'checklist_snapshot_hash'):
            _required_text(item, key)
        if item['mode'] not in ('mock', 'live'):
            raise ValueError('Explicit provenance mock/live mode is required')
        if type(item.get('attempt_number')) is not int or item['attempt_number'] < 1:
            raise ValueError('Explicit positive attempt_number is required')
        identity = (item['conversation_id'], item['mode'], item['attempt_number'])
        eid = item['evaluation_id']
        if eid in provenances or identity in seen_attempts:
            raise ValueError('Duplicate provenance evaluation or attempt')
        provenances[eid] = item
        seen_attempts.add(identity)
    if set(indexed) - set(provenances):
        raise ValueError('Export contains evaluations without recorded provenance; refusing to invent mode or attempt order')

    expected_definition = official_checklist().model_dump(mode='json')
    attempts = []
    for eid, provenance in provenances.items():
        row = indexed.get(eid)
        attempt = {key: provenance[key] for key in (
            'conversation_id', 'attempt_number', 'mode', 'checklist_hash', 'input_hash')}
        attempt.update(attempt_id=eid, latency_ms=None, cost_usd=None,
                       original_ai=None, status='missing_export', error_category='missing_export')
        if row is None:
            attempts.append(attempt)
            continue
        if row.get('conversation_id') != provenance['backend_conversation_id']:
            raise ValueError('Backend conversation mapping conflicts with recorded provenance')
        snapshot = row.get('checklist_snapshot')
        if not isinstance(snapshot, dict):
            raise ValueError('Export is missing its immutable checklist snapshot')
        definition = {k: v for k, v in snapshot.items() if k not in ('id', 'version', 'created_at')}
        if definition != expected_definition:
            raise ValueError('Custom/changed checklist snapshot cannot be compared as organizer-v1')
        if snapshot_hash(snapshot) != provenance['checklist_snapshot_hash']:
            attempt.update(status='error', error_category='checklist_snapshot_mismatch')
            attempts.append(attempt)
            continue
        backend_status = row.get('status')
        if backend_status not in ('queued', 'running', 'completed', 'failed'):
            raise ValueError('Unknown backend evaluation status')
        attempt['status'] = 'success' if backend_status == 'completed' else backend_status
        error = row.get('error')
        attempt['error_category'] = error.get('code') if isinstance(error, dict) else None
        if attempt['error_category'] == 'ai_timeout':
            attempt['error_category'] = 'timeout'
        answers, original_error = _convert_answers(row.get('ai_draft'))
        if answers is not None:
            attempt['original_ai'] = {'answers': answers}
            if isinstance(row.get('original_score'), dict) and 'total' in row['original_score']:
                attempt['original_ai']['total'] = row['original_score']['total']
        elif backend_status == 'completed':
            attempt.update(status='error', error_category=original_error)
        attempts.append(attempt)
    result = {key: deepcopy(context[key]) for key in ('config_version', 'checklist_hash', 'mode',
              'reference_source', 'split', 'manifest', 'references')}
    result['attempts'] = attempts
    result['adapter_notes'] = {
        'attempt_unit': 'backend_evaluation_job',
        'provider_call_count': None,
        'latency_and_cost': 'Not present in backend export; unknown, never estimated from timestamps.',
        'provenance': 'Supplied sidecar required; not inferred from export order.',
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path)
    parser.add_argument('context', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = adapt_export(json.loads(args.export.read_text(encoding='utf-8-sig')),
                          json.loads(args.context.read_text(encoding='utf-8-sig')))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
