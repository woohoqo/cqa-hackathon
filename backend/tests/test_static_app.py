from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_built_ui_and_api_share_one_origin(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html><title>Synthetic UI build</title></html>", encoding="utf-8")
    (frontend / "asset.js").write_text("// synthetic asset", encoding="utf-8")
    app = create_app(database_path=str(tmp_path / "test.db"), frontend_dist=str(frontend))
    with TestClient(app) as client:
        assert "Synthetic UI build" in client.get("/").text
        assert client.get("/asset.js").status_code == 200
        assert client.get("/health").json()["status"] == "ok"
        assert len(client.get("/api/v1/checklists").json()[0]["criteria"]) == 8
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/../.env").status_code == 404
