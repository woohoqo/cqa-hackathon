"""Probe a running local backend with SYNTHETIC input and an expected provider error.

Start the unconfigured production backend in a dedicated local database, then run:
python -m qa.smoke_backend --base-url http://127.0.0.1:8765

This writes one synthetic conversation/evaluation to that database. It checks HTTP,
queue/error persistence and exports, not a successful model call or AI accuracy.
"""
import argparse
import csv
import io
import json
import time
import urllib.request
from urllib.parse import urlparse
from uuid import uuid4


def smoke(base_url, expected_error='provider_not_configured'):
    parsed = urlparse(base_url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Use a dedicated loopback HTTP server for this synthetic write probe')
    base_url = base_url.rstrip('/')

    def request(path, body=None, as_json=True):
        req = urllib.request.Request(base_url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            text = response.read().decode('utf-8')
            return json.loads(text) if as_json else text

    started = time.monotonic()
    assert request('/health')['status'] == 'ok', 'Unhealthy backend'
    checklist = next(c for c in request('/api/v1/checklists') if c['key'] == 'freedom-cqa')
    assert len(checklist['criteria']) == 8, 'Use the initial official checklist for this probe'
    conversation = request('/api/v1/conversations', {
        'source': 'synthetic-qa-http-smoke', 'external_id': 'synthetic-' + uuid4().hex,
        'metadata': {'synthetic': True, 'language': 'EN', 'channel': 'agent chat'},
        'messages': [
            {'id': 'smoke-m1', 'role': 'client', 'timestamp': '00:00',
             'text': 'SYNTHETIC QA: Where is the report?'},
            {'id': 'smoke-m2', 'role': 'operator', 'timestamp': '00:02',
             'text': 'SYNTHETIC QA: Hello, I am Test. Open Reports. Goodbye.'}]})
    batch = request('/api/v1/evaluations', {
        'conversation_ids': [conversation['id']], 'checklist_id': checklist['id']})
    evaluation_id = batch['evaluations'][0]['id']
    deadline = time.monotonic() + 20
    while True:
        evaluation = request('/api/v1/evaluations/' + evaluation_id)
        if evaluation['status'] in ('failed', 'completed'):
            break
        if time.monotonic() >= deadline:
            raise AssertionError('Evaluation did not finish within 20 seconds')
        time.sleep(0.1)
    assert evaluation['status'] == 'failed', 'Expected an explicit failure, not a successful evaluation'
    assert evaluation['error']['code'] == expected_error, 'Unexpected safe error code'
    for name in ('ai_draft', 'original_score', 'final_score'):
        assert evaluation[name] is None, 'Failure must not have a fabricated result: ' + name
    reopened = request('/api/v1/evaluations/' + evaluation_id)
    assert reopened == evaluation, 'Persisted evaluation changed on reopen'
    query = '?batch_id=' + batch['id']
    exported = request('/api/v1/exports/evaluations' + query + '&format=json')
    assert exported['evaluations'] == [evaluation], 'JSON export dropped/changed failed evaluation'
    rows = list(csv.DictReader(io.StringIO(request(
        '/api/v1/exports/evaluations' + query + '&format=csv', as_json=False))))
    assert len(rows) == 1 and rows[0]['status'] == 'failed', 'CSV export dropped failed evaluation'
    assert rows[0]['original_total'] == rows[0]['final_total'] == '', 'Failed totals must stay empty'
    return {'mode': 'local_http_unconfigured_production_provider', 'synthetic': True,
            'status': 'pass', 'evaluation_status': evaluation['status'],
            'error_code': evaluation['error']['code'], 'json_failed_record_preserved': True,
            'csv_failed_record_preserved': True, 'elapsed_seconds': round(time.monotonic()-started, 3),
            'real_model_api_called': False, 'accuracy_measured': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    print(json.dumps(smoke(args.base_url), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
