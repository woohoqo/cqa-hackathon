"""Compare two independent human exports; finalize only explicit human decisions.

This is a QA-only private-file workflow, not a production API or an automatic
ground-truth generator. It reads no transcripts and calls no model. Keep all
inputs/outputs outside Git: evidence comments can themselves contain private text.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from qa.compare import WEIGHTS, NA_ALLOWED, SCALES, ratio, score_answers


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' must be a nonempty string')


def allowed_answer(cid, answer):
    if answer == 'na':
        return cid in NA_ALLOWED
    if cid in SCALES:
        return type(answer) is int and answer in SCALES[cid]
    return type(answer) is str and answer in ('yes', 'no')


def validate_criteria(criteria, row, complete=False):
    if not isinstance(criteria, list) or len(criteria) != len(WEIGHTS):
        raise ValueError('Exactly eight criteria are required')
    answers = {}
    mids = {f"{row['conversation_id']}:m{i:04d}" for i in range(1, row['messages'] + 1)}
    for c in criteria:
        if not isinstance(c, dict) or c.get('criterion_id') not in WEIGHTS:
            raise ValueError('Unknown criterion')
        cid = c['criterion_id']
        if cid in answers:
            raise ValueError('Duplicate criterion')
        answer = c.get('answer')
        if (answer is None and complete) or (answer is not None and not allowed_answer(cid, answer)):
            raise ValueError('Invalid or missing answer: ' + cid)
        evidence = c.get('evidence')
        if not isinstance(evidence, list) or any(not isinstance(e, dict) or
                e.get('message_id') not in mids for e in evidence):
            raise ValueError('Evidence references an unknown message')
        if not isinstance(c.get('comment'), str):
            raise ValueError('Criterion comment must be text')
        if complete and (not evidence or not c['comment'].strip()):
            raise ValueError('Complete decisions require evidence and a reason for every criterion')
        if complete and c.get('disputed'):
            raise ValueError('Unresolved disputes cannot be finalized')
        answers[cid] = answer
    return answers


def validate_inputs(a, b, manifest):
    if not isinstance(manifest, dict) or manifest.get('config_version') != 'organizer-v1':
        raise ValueError('Only the original organizer-v1 checklist is supported')
    require_text(manifest.get('checklist_hash'), 'manifest.checklist_hash')
    rows = {}
    if not isinstance(manifest.get('records'), list):
        raise ValueError('manifest.records must be an array')
    for row in manifest['records']:
        if not isinstance(row, dict):
            raise ValueError('Manifest record must be an object')
        for key in ('conversation_id', 'language', 'split', 'transcript_hash'):
            require_text(row.get(key), 'manifest.' + key)
        if row['conversation_id'] in rows or type(row.get('messages')) is not int or row['messages'] < 1:
            raise ValueError('Duplicate manifest ID or invalid message count')
        rows[row['conversation_id']] = row
    indexes = []
    for export in (a, b):
        if not isinstance(export, dict) or export.get('source') != 'human_individual':
            raise ValueError('Inputs must be independent human_individual exports')
        require_text(export.get('annotator_id'), 'annotator_id')
        if export.get('checklist_hash') != manifest['checklist_hash']:
            raise ValueError('Export checklist hash does not match the manifest')
        if not isinstance(export.get('annotations'), list):
            raise ValueError('annotations must be an array')
        index = {}
        for entry in export['annotations']:
            if not isinstance(entry, dict):
                raise ValueError('Annotation must be an object')
            cid = entry.get('conversation_id')
            if cid not in rows or cid in index:
                raise ValueError('Unknown or duplicate annotation conversation ID')
            if entry.get('source') != 'human_individual' or entry.get('annotator_id') != export['annotator_id']:
                raise ValueError('Annotation source/annotator conflicts with export')
            if entry.get('checklist_hash') != manifest['checklist_hash'] or entry.get('transcript_hash') != rows[cid]['transcript_hash']:
                raise ValueError('Annotation hashes do not match the manifest')
            if entry.get('status') not in ('draft', 'disputed', 'complete'):
                raise ValueError('Unknown annotation status')
            answers = validate_criteria(entry.get('criteria'), rows[cid], entry['status'] == 'complete')
            if entry.get('answers') != answers:
                raise ValueError('Annotation answers conflict with its criterion rows')
            index[cid] = entry
        indexes.append(index)
    if a['annotator_id'] == b['annotator_id']:
        raise ValueError('Two distinct independent annotators are required')
    return rows, indexes


def compare_annotations(a, b, manifest, subset='pilot'):
    rows, indexes = validate_inputs(a, b, manifest)
    if subset not in ('pilot', 'tuning', 'holdout', 'all'):
        raise ValueError('Unknown subset')
    selected = [row for row in rows.values() if subset == 'all' or
                (subset == 'pilot' and row.get('pilot') is True) or row['split'] == subset]
    records = []
    for row in selected:
        cid = row['conversation_id']
        originals = [index.get(cid) for index in indexes]
        differences, incomplete = [], []
        for key in WEIGHTS:
            values = [entry['answers'][key] if entry else None for entry in originals]
            if None in values:
                incomplete.append(key)
            elif values[0] != values[1]:
                differences.append(key)
        records.append({
            'conversation_id': cid, 'language': row['language'], 'split': row['split'],
            'transcript_hash': row['transcript_hash'],
            'source_annotation_ids': ['sha256:' + digest(entry) if entry else None for entry in originals],
            'source_statuses': [entry['status'] if entry else 'missing' for entry in originals],
            'disagreement_criterion_ids': differences, 'incomplete_criterion_ids': incomplete,
            'decision': {'status': 'draft', 'reviewer_id': '', 'reviewed_at': '', 'rationale': '',
                'criteria': [{'criterion_id': key, 'answer': None, 'evidence': [], 'comment': ''}
                             for key in WEIGHTS]},
        })

    def summary(selection):
        result = {'planned': len(selection), 'per_criterion': {}}
        for key in WEIGHTS:
            pairs = [(indexes[0].get(r['conversation_id']), indexes[1].get(r['conversation_id'])) for r in selection]
            pairs = [(x['answers'][key], y['answers'][key]) for x, y in pairs
                     if x and y and x['answers'][key] is not None and y['answers'][key] is not None]
            differences = sum(x != y for x, y in pairs)
            result['per_criterion'][key] = {'pairs': len(pairs), 'disagreements': differences,
                'agreement': ratio(len(pairs) - differences, len(pairs)),
                'na_disagreements': sum((x == 'na') != (y == 'na') for x, y in pairs)}
        return result

    return {'qa_adjudication_version': 1, 'config_version': 'organizer-v1',
            'checklist_hash': manifest['checklist_hash'], 'manifest_hash': digest(manifest),
            'subset': subset, 'source_export_hashes': [digest(a), digest(b)],
            'annotators': [a['annotator_id'], b['annotator_id']],
            'comparison_kind': 'human_interannotator_agreement_not_ai_accuracy',
            'overall': summary(selected),
            'by_language': {language: summary([r for r in selected if r['language'] == language])
                            for language in sorted({r['language'] for r in selected})},
            'records': records}


def finalize(a, b, manifest, reviewed):
    """Re-read originals; no generated answer or unanimous pair becomes truth by itself."""
    expected = compare_annotations(a, b, manifest, reviewed.get('subset'))
    for key in ('qa_adjudication_version', 'config_version', 'checklist_hash', 'manifest_hash',
                'source_export_hashes', 'annotators', 'comparison_kind', 'overall', 'by_language'):
        if reviewed.get(key) != expected[key]:
            raise ValueError('Review source or report changed: ' + key)
    if not isinstance(reviewed.get('records'), list) or len(reviewed['records']) != len(expected['records']):
        raise ValueError('Review records changed or disappeared')
    rows = {row['conversation_id']: row for row in manifest['records']}
    references, pending = [], []
    for item, original in zip(reviewed['records'], expected['records']):
        if not isinstance(item, dict) or {k: v for k, v in item.items() if k != 'decision'} != {k: v for k, v in original.items() if k != 'decision'}:
            raise ValueError('Review record provenance changed')
        decision = item.get('decision')
        if not isinstance(decision, dict) or decision.get('status') not in ('draft', 'disputed', 'complete'):
            raise ValueError('Invalid decision status')
        cid = item['conversation_id']
        if decision['status'] != 'complete':
            pending.append(cid)
            continue
        if any(status not in ('complete', 'disputed') for status in item['source_statuses']):
            raise ValueError('Both independent annotations must be complete or disputed before adjudication')
        for key in ('reviewer_id', 'reviewed_at', 'rationale'):
            require_text(decision.get(key), 'decision.' + key)
        try:
            moment = datetime.fromisoformat(decision['reviewed_at'].replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError('reviewed_at must be ISO 8601 with timezone') from exc
        if moment.tzinfo is None:
            raise ValueError('reviewed_at must include timezone')
        answers = validate_criteria(decision.get('criteria'), rows[cid], complete=True)
        reference = {'conversation_id': cid, 'source': 'human_adjudicated', 'status': 'complete',
            'checklist_hash': manifest['checklist_hash'], 'transcript_hash': rows[cid]['transcript_hash'],
            'answers': answers, 'total': score_answers(answers),
            'source_annotation_ids': item['source_annotation_ids'], 'source_export_hashes': expected['source_export_hashes'],
            'reviewer_id': decision['reviewer_id'], 'reviewed_at': decision['reviewed_at'],
            'rationale': decision['rationale'], 'criteria': deepcopy(decision['criteria'])}
        reference['annotation_id'] = 'sha256:' + digest(reference)
        references.append(reference)
    return {'qa_adjudication_version': 1, 'source': 'human_adjudicated',
            'checklist_hash': manifest['checklist_hash'], 'manifest_hash': digest(manifest),
            'source_exports': [deepcopy(a), deepcopy(b)], 'source_export_hashes': expected['source_export_hashes'],
            'review': deepcopy(reviewed), 'references': references, 'pending_conversation_ids': pending}


def render_review(review):
    lines = ['# Независимая разметка A/B', '',
             'Это согласие двух людей, не точность AI и не Ground Truth организаторов.',
             'Совпадение ответов не создаёт согласованную метку автоматически.', '',
             '| Критерий | Пары | Разногласия | Из них N/A | Согласие |',
             '|---|---:|---:|---:|---:|']
    for key, item in review['overall']['per_criterion'].items():
        agreement = 'не рассчитано' if item['agreement'] is None else f"{item['agreement']:.2%}"
        lines.append(f"| {key} | {item['pairs']} | {item['disagreements']} | {item['na_disagreements']} | {agreement} |")
    lines += ['', '№8 в таблице — отдельные разногласия по грубым нарушениям.',
              'Статусы draft/disputed и пропуски остаются ниже; согласие по заполненным ответам не означает готовности меток.', '',
              '| Диалог | Язык | A / B | Разные критерии | Незаполненные |',
              '|---|---|---|---|---|']
    for row in review['records']:
        cid = row['conversation_id'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {cid} | {row['language']} | {' / '.join(row['source_statuses'])} | {', '.join(row['disagreement_criterion_ids']) or '—'} | {', '.join(row['incomplete_criterion_ids']) or '—'} |")
    lines += ['', 'Заполните только decision в review.json после человеческого обсуждения. '
              'Для complete нужны reviewer_id, reviewed_at с часовым поясом, rationale и восемь ответов с evidence/comment.',
              'Результаты и комментарии хранить приватно. Не передавать holdout AI-разработчику.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('compare', 'finalize'):
        sub = commands.add_parser(name)
        for key in ('annotator_a', 'annotator_b', 'manifest'):
            sub.add_argument(key, type=Path)
        sub.add_argument('--output', type=Path, required=True)
        if name == 'compare':
            sub.add_argument('--subset', choices=('pilot', 'tuning', 'holdout', 'all'), default='pilot')
            sub.add_argument('--report', type=Path)
        else:
            sub.add_argument('review', type=Path)
    args = parser.parse_args()
    input_paths = [getattr(args, key).resolve() for key in ('annotator_a', 'annotator_b', 'manifest')]
    if args.command == 'finalize':
        input_paths.append(args.review.resolve())
    output_paths = [args.output.resolve()]
    if args.command == 'compare' and args.report:
        output_paths.append(args.report.resolve())
    if len(set(output_paths)) != len(output_paths) or set(input_paths) & set(output_paths):
        parser.error('Output paths must be distinct and must not overwrite original inputs')
    read = lambda path: json.loads(path.read_text(encoding='utf-8-sig'))
    inputs = [read(getattr(args, key)) for key in ('annotator_a', 'annotator_b', 'manifest')]
    result = compare_annotations(*inputs, args.subset) if args.command == 'compare' else finalize(*inputs, read(args.review))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if args.command == 'compare' and args.report:
        args.report.write_text(render_review(result), encoding='utf-8')


if __name__ == '__main__':
    main()
