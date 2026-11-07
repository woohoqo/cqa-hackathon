"""Official download routes with synthetic conversations and simulated model responses."""
import json
from copy import deepcopy

from fastapi.testclient import TestClient

from backend.app.contracts import AIProviderError
from backend.app.main import create_app
from backend.app.settings import Settings
from backend.tests.test_api import draft, poll, synthetic_transcript
from backend.tests.test_official_export import execution


def test_official_preflight_download_retry_and_archive_stay_run_scoped(tmp_path):
    calls = {}
    async def evaluator(conversation, checklist):
        calls[conversation.id] = calls.get(conversation.id, 0) + 1
        if conversation.external_id == "synthetic-error" and calls[conversation.id] == 1:
            raise AIProviderError("synthetic_network_error", "Synthetic unavailable provider", retryable=True,
                                  execution=execution("synthetic-model"))
        result = draft(conversation, checklist)
        result.provider = "gemini"
        result.model = "synthetic-model"
        return result
    app = create_app(settings=Settings(str(tmp_path / "official-api.sqlite3"), [], 1, 5, ""), evaluator=evaluator)
    with TestClient(app) as client:
        loaded = client.post("/api/v1/imports/json", json={"dataset_name": "Synthetic export test", "conversations": [
            synthetic_transcript("synthetic-ok"), synthetic_transcript("synthetic-error")]}).json()
        checklist = client.get("/api/v1/checklists").json()[0]
        body = {"dataset_id": loaded["dataset"]["id"], "conversation_ids": loaded["dataset"]["conversation_ids"],
                "checklist_id": checklist["id"], "idempotency_key": "synthetic-export-run"}
        batch = client.post("/api/v1/evaluations", json=body).json()
        records = [poll(client, record["id"]) for record in batch["evaluations"]]
        root = f"/api/v1/batches/{batch['id']}"
        report = client.post(root + "/official-export/validate", json={"team_name": "cuda cuda"}).json()
        assert report["valid"] and report["counts"] == {"expected": 2, "actual": 2, "ok": 1, "error": 1}
        assert report["warnings"] and not report["errors"]
        exported = client.post(root + "/official-export", json={"team_name": "cuda cuda"})
        assert exported.status_code == 200 and 'filename="results.json"' in exported.headers["content-disposition"]
        payload = exported.json()
        assert payload["schema_version"] == "1.0" and payload["team_name"] == "cuda cuda"
        assert payload["model"] == {"mode": "external_api", "name": "synthetic-model"}
        assert payload["results"][0]["score"]["final_score"] == 85.38
        assert payload["results"][1]["status"] == "error" and payload["results"][1]["score"] is None
        broken = deepcopy(payload)
        broken["results"][0]["criteria"][0]["value"] = "true"
        invalid = client.post(root + "/official-export/validate", json={"team_name": "cuda cuda", "payload": broken}).json()
        assert not invalid["valid"]
        assert any(item.get("conversation_id") == "synthetic-ok" for item in invalid["errors"])
        bad_team = client.post(root + "/official-export/validate", json={"team_name": "other", "payload": payload}).json()
        assert not bad_team["valid"]
        # A tested custom JSON is not silently substituted for the saved server result on download.
        assert client.post(root + "/official-export", json={"payload": broken}).status_code == 422

        failed = next(record for record in records if record["status"] == "failed")
        completed = next(record for record in records if record["status"] == "completed")
        request = {"failed_evaluation_ids": [failed["id"]]}
        retried = client.post(root + "/retry", json=request).json()
        assert retried["retried"] == 1
        latest = next(record for record in retried["batch"]["evaluations"] if record["retry_of"] == failed["id"])
        assert poll(client, latest["id"])["status"] == "completed"
        assert client.post(root + "/retry", json=request).json()["retried"] == 0
        assert client.get(f"/api/v1/evaluations/{completed['id']}").json() == completed
        final = client.post(root + "/official-export", json={}).json()
        assert len(final["results"]) == 2 and all(r["status"] == "ok" for r in final["results"])
        archive = client.get("/api/v1/exports/evaluations", params={"batch_id": batch["id"]}).json()
        assert len(archive["evaluations"]) == 3

        # A new partial run cannot borrow the missing dialogue from the previous completed run.
        another = client.post("/api/v1/evaluations", json={**body, "idempotency_key": "synthetic-partial-run",
            "conversation_ids": body["conversation_ids"][:1]}).json()
        poll(client, another["evaluations"][0]["id"])
        partial = client.post(f"/api/v1/batches/{another['id']}/official-export/validate", json={}).json()
        assert not partial["valid"]
        assert any(item.get("conversation_id") == "synthetic-error" and item["code"] == "missing_evaluation" for item in partial["errors"])
        assert client.post(f"/api/v1/batches/{another['id']}/official-export", json={}).status_code == 422
        assert json.loads(client.post(root + "/official-export", json={}).content) == final
