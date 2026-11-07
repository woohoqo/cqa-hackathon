"""Security and data isolation of the opt-in shared demo, using synthetic inputs only."""
import base64
import json
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from backend.app import demo

PASSWORD = "synthetic-demo-password-for-tests"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setenv("CQA_DEMO_PASSWORD", PASSWORD)
    monkeypatch.delenv("CQA_DEMO_ORGANIZER_ENDPOINT", raising=False)
    for key, value in {"DATABASE_PATH": str(tmp_path / "original.sqlite3"),
                       "DEV_API_URL": "https://example.invalid/must-not-fetch",
                       "AI_USAGE_LOG_PATH": str(tmp_path / "original.jsonl")}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "original.sqlite3").write_bytes(b"untouched original database sentinel")
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "demo-conversations.json").write_text(json.dumps({"conversations": [{
        "external_id": "synthetic-seed", "metadata": {"synthetic": True},
        "messages": [{"id": "m1", "role": "client", "text": "Synthetic fixture", "timestamp": "00:01"}],
    }]}), encoding="utf-8")
    frontend = tmp_path / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<html>Synthetic demo UI</html>", encoding="utf-8")
    (frontend / "asset.js").write_text("// synthetic asset", encoding="utf-8")
    app = demo.create_demo_app()
    with TestClient(app, base_url="https://demo.example") as value:
        yield value
    assert (tmp_path / "original.sqlite3").read_bytes() == b"untouched original database sentinel"
    assert app.state.service.db.path == str(tmp_path / "data" / "demo" / "cqa.sqlite3")


@pytest.mark.parametrize("path", ["/", "/asset.js", "/health", "/docs", "/openapi.json",
                                 "/api/v1/conversations", "/api/v1/exports/evaluations"])
def test_all_routes_require_auth(client, path):
    for credentials in [None, ("demo", "wrong"), ("wrong", PASSWORD)]:
        result = client.get(path, auth=credentials)
        assert result.status_code == 401
        assert "www-authenticate" not in result.headers
        assert result.headers["cache-control"] == "no-store"
        assert "Synthetic" not in result.text


@pytest.mark.parametrize("header", ["", "Basic !!!", "Basic " + base64.b64encode(b"no-colon").decode(),
                                    "Bearer invalid", "Basic " + "A" * 4096])
def test_malformed_auth_is_safe(client, header):
    assert client.get("/health", headers={"authorization": header}).status_code == 401


def test_authorized_ui_seed_export_and_reimport(client):
    client.auth = ("demo", PASSWORD)
    assert "Synthetic demo UI" in client.get("/").text
    assert client.get("/asset.js").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
    conversations = client.get("/api/v1/conversations").json()
    assert len(conversations) == 1 and conversations[0]["metadata"]["synthetic"] is True
    assert client.get("/api/v1/exports/evaluations").json()["evaluations"] == []
    payload = {"conversations": [{"external_id": "synthetic-import", "metadata": {"synthetic": True},
                                 "messages": [{"role": "operator", "text": "Synthetic new input"}]}]}
    result = client.post("/api/v1/imports/json", json=payload, headers={"origin": "https://demo.example"})
    assert result.status_code == 200
    assert len(client.get("/api/v1/conversations").json()) == 2
    assert client.get("/.env").status_code == 404
    assert client.get("/private/demo-access.json").status_code == 404


@pytest.mark.parametrize("headers", [
    {"origin": "https://evil.example"}, {"origin": "null"}, {"origin": "http://demo.example"},
    {"origin": "https://demo.example:444"}, {"sec-fetch-site": "cross-site"},
    {"origin": "https://evil.example", "x-forwarded-host": "evil.example"},
])
def test_cross_origin_mutations_rejected_before_data_changes(client, headers):
    client.auth = ("demo", PASSWORD)
    before = client.get("/api/v1/conversations").json()
    result = client.post("/api/v1/conversations", headers=headers,
                         json={"messages": [{"role": "client", "text": "must not be saved"}]})
    assert result.status_code == 403
    assert client.get("/api/v1/conversations").json() == before


def test_demo_never_uses_the_server_signed_link_as_a_fallback(client):
    response = client.post("/api/v1/imports/dev", json={}, auth=("demo", PASSWORD))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "demo_api_url_required"


@pytest.mark.parametrize("url", [
    "https://localhost/must-not-fetch", "https://127.0.0.1/must-not-fetch", "https://[::1]/must-not-fetch",
    "https://10.0.0.1/must-not-fetch", "https://example.invalid.attacker.invalid/must-not-fetch",
    "https://example.invalid:8443/must-not-fetch", "https://example.invalid/another-endpoint",
    "https://example.invalid/must-not-fetch/", "https://example.invalid/a/../must-not-fetch",
    "https://example.invalid/%6dust-not-fetch",
])
def test_demo_import_rejects_other_destinations_before_fetch(client, monkeypatch, url):
    async def forbidden(*args):
        pytest.fail("Untrusted endpoint must be rejected before network access")
    monkeypatch.setattr("backend.app.main.fetch_organizer", forbidden)
    response = client.post("/api/v1/imports/dev", json={"api_url": url}, auth=("demo", PASSWORD))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "demo_organizer_url_denied"
    assert url not in response.text


def test_demo_import_accepts_only_explicit_owner_endpoint_without_saving_signature(client, monkeypatch):
    seen = []
    def response(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"isSuccess": True, "data": {
            "dataset_group_id": "CONTROL_1", "total_records": 1, "records": [{
                "conversation_id": "synthetic-organizer-input", "channel": "agent chat", "language": "RU",
                "transcript": [{"speaker": "client", "text": "Synthetic input", "timestamp": "00:00"}]
            }]}})
    original = httpx.AsyncClient
    monkeypatch.setattr("backend.app.importers.httpx.AsyncClient", lambda **kw:
                        original(**kw, trust_env=False, transport=httpx.MockTransport(response)))
    url = "https://example.invalid:443/must-not-fetch?sig=SYNTHETIC%2b%2F&group_id=OLD"
    client.auth = ("demo", PASSWORD)
    result = client.post("/api/v1/imports/dev", json={"api_url": url, "group_id": "CONTROL_1"},
                         headers={"Origin": "https://demo.example"})
    assert result.status_code == 200 and result.json()["imported"] == 1
    assert client.post("/api/v1/imports/dev", json={"api_url": url, "group_id": "CONTROL_1"}).json()["existing"] == 1
    assert all(u == "https://example.invalid/must-not-fetch?sig=SYNTHETIC%2b%2F&group_id=CONTROL_1" for u in seen)
    assert "SYNTHETIC%2b" not in client.get("/api/v1/conversations").text
    assert client.post("/api/v1/imports/dev", json={}).status_code == 422
    assert len(seen) == 2
    db = client.app.state.service.db
    assert b"SYNTHETIC%2b" not in Path(db.path).read_bytes()


def test_unconfigured_demo_destination_stays_closed():
    from backend.app.errors import AppError
    for endpoint in ("", "http://example.invalid", "https://example.invalid:8443"):
        with pytest.raises(AppError) as error:
            demo.organizer_import_guard(endpoint)("https://example.invalid")
        assert error.value.code == "demo_organizer_not_configured"


def test_demo_without_password_refuses_to_start(monkeypatch):
    monkeypatch.setenv("CQA_DEMO_PASSWORD", "")
    with pytest.raises(RuntimeError, match="Start the protected demo"):
        demo.create_demo_app()


def test_browser_login_cookie_and_expiry(client, monkeypatch):
    result = client.get("/", headers={"accept": "text/html"})
    assert result.status_code == 200 and "Вход в демо" in result.text
    assert result.headers["referrer-policy"] == "same-origin"
    assert client.get("/api/v1/conversations", headers={"accept": "text/html"}).status_code == 401
    assert "www-authenticate" not in result.headers
    bad = client.post("/demo-login", data={"password": "wrong"}, headers={"Origin": "https://demo.example"})
    assert bad.status_code == 401 and "wrong" not in bad.text
    assert "www-authenticate" not in bad.headers
    good = client.post("/demo-login", data={"password": PASSWORD},
                       headers={"Origin": "https://demo.example"}, follow_redirects=False)
    assert good.status_code == 303 and good.headers["location"] == "/"
    cookie = good.headers["set-cookie"]
    assert demo.COOKIE_NAME in cookie
    for flag in ("Secure", "HttpOnly", "SameSite=strict", "Path=/", "Max-Age=28800"):
        assert flag in cookie
    assert "Domain=" not in cookie and PASSWORD not in cookie
    assert client.get("/api/v1/conversations").status_code == 200
    now = demo.time.time()
    monkeypatch.setattr(demo.time, "time", lambda: now + demo.SESSION_SECONDS + 1)
    assert client.get("/api/v1/conversations").status_code == 401


def test_login_rejects_cross_origin_and_oversized_form(client):
    assert client.post("/demo-login", data={"password": PASSWORD},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/v1/conversations").status_code == 401
    assert client.post("/demo-login", content=b"x" * 4097,
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
    assert client.post("/demo-login", json={"password": PASSWORD}).status_code == 415
    assert client.post("/demo-login", content=f"password={PASSWORD}&password={PASSWORD}",
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 401


@pytest.mark.parametrize("cookie", ["forged", "123.nonce.bad", "1.nonce." + "a" * 64, "x" * 300])
def test_invalid_session_never_grants_access(client, cookie):
    assert client.get("/api/v1/conversations", headers={"cookie": f"{demo.COOKIE_NAME}={cookie}"}).status_code == 401
