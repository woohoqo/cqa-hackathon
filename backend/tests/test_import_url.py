"""Only synthetic URLs and data; explicit mock transport prevents outbound requests."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings
from backend.app.importers import organizer_url


def settings(tmp_path, url=''):
    return Settings(str(tmp_path / 'test.sqlite3'), [], 1, 5, url)


def transport_fixture(monkeypatch):
    seen = []

    def response(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={'isSuccess': True, 'data': {
            'dataset_group_id': request.url.params['group_id'], 'total_records': 1,
            'records': [{'conversation_id': 'synthetic-request-url', 'channel': 'agent chat', 'language': 'RU',
                'transcript': [{'speaker': 'client', 'text': 'Synthetic question', 'timestamp': '00:01'}]}]}})

    client = httpx.AsyncClient
    monkeypatch.setattr('backend.app.importers.httpx.AsyncClient',
                        lambda **kw: client(**kw, trust_env=False, transport=httpx.MockTransport(response)))
    return seen


def test_override_works_without_server_config_and_is_not_persisted(tmp_path, monkeypatch):
    seen = transport_fixture(monkeypatch)
    config = settings(tmp_path)
    url = 'https://example.invalid/api?sig=SYNTHETIC%2b%2F%3D&group_id=OLD&v=1'
    with TestClient(create_app(settings=config)) as client:
        first = client.post('/api/v1/imports/dev', json={'api_url': '  ' + url + '  ', 'group_id': 'DEV'})
        assert first.status_code == 200
        assert first.json()['imported'] == 1
        repeated = client.post('/api/v1/imports/dev', json={'api_url': url})
        assert repeated.status_code == 200 and repeated.json()['existing'] == 1
        fallback = client.post('/api/v1/imports/dev', json={})
        assert fallback.status_code == 503 and fallback.json()['error']['code'] == 'dev_not_configured'
        assert config.dev_api_url == ''
        assert all('sig=SYNTHETIC%2b%2F%3D' in u and u.endswith('v=1&group_id=DEV') for u in seen)
        assert 'SYNTHETIC%2b' not in json.dumps(client.get('/api/v1/conversations').json())
        assert client.get('/api/v1/evaluations').json() == []
    assert b'SYNTHETIC%2b' not in (tmp_path / 'test.sqlite3').read_bytes()


@pytest.mark.parametrize('fallback_body', [{}, {'api_url': None}, {'api_url': '   '}])
def test_override_does_not_change_following_fallback(tmp_path, monkeypatch, fallback_body):
    seen = transport_fixture(monkeypatch)
    config = settings(tmp_path, 'https://configured.invalid/a')
    with TestClient(create_app(settings=config)) as client:
        assert client.post('/api/v1/imports/dev', json={'api_url': 'https://temporary.invalid/b'}).status_code == 200
        assert client.post('/api/v1/imports/dev', json=fallback_body).status_code == 200
    assert seen == ['https://temporary.invalid/b?group_id=DEV', 'https://configured.invalid/a?group_id=DEV']
    assert config.dev_api_url == 'https://configured.invalid/a'


@pytest.mark.parametrize('url', ['http://example.invalid', 'https://u:secret@example.invalid', 'https://[',
    'https://example.invalid:bad', 'https://example.invalid:65536', 'https://example.invalid:0',
    'https://example.invalid/a b', 'https://example.invalid/a\nb', 'https://example.invalid/a\\b',
    'https://example.invalid/#fragment', '/relative', 'https://', 'https://example.invalid/' + 'x' * 8192])
def test_bad_input_has_safe_field_error_without_url(tmp_path, url):
    with TestClient(create_app(settings=settings(tmp_path))) as client:
        response = client.post('/api/v1/imports/dev', json={'api_url': url})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'invalid_request'
    assert 'body.api_url' in response.json()['error']['message']
    assert 'example.invalid' not in response.text and 'secret' not in response.text


def test_malformed_server_configuration_is_controlled():
    from backend.app.errors import AppError
    for url in ['https://[', 'https://example.invalid:bad']:
        with pytest.raises(AppError) as error:
            organizer_url(url, 'DEV')
        assert error.value.code == 'invalid_dev_configuration'
