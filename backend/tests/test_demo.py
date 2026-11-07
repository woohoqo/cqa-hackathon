"""Security and data isolation of the opt-in shared demo, using synthetic inputs only."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import demo

PASSWORD = "synthetic-demo-password-for-tests"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setenv("CQA_DEMO_PASSWORD", PASSWORD)
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


def test_dev_import_cannot_fetch_private_organizer_data(client):
    response = client.post("/api/v1/imports/dev", json={}, auth=("demo", PASSWORD))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "demo_dev_import_disabled"


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
