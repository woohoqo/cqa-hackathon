"""Synthetic run/dataset/retry regression tests; no provider or organizer network calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app.contracts import (AIProviderError, AppealCreate, ConversationInput, EvaluationRequest,
    EvaluationStatus, RetryRequest, ReviewDecisionCreate)
from backend.app.default_checklist import official_checklist
from backend.app.errors import AppError
from backend.app.main import create_app
from backend.app.service import CQAService
from backend.app.settings import Settings
from backend.app.storage import Database
from backend.tests.test_api import draft, synthetic_transcript


def make_service(tmp_path, evaluator=None):
    db = Database(str(tmp_path / "runs.sqlite3"))
    db.initialize()
    service = CQAService(db, Settings(db.path, [], 1, 5, ""), evaluator=evaluator)
    checklist = service.create_checklist(official_checklist())
    return service, checklist


def imported(service, *names, kind="test", group=None):
    return service.import_conversations([ConversationInput.model_validate(synthetic_transcript(name)) for name in names],
                                       dataset_kind=kind, group_id=group)


def enqueue(service, checklist, imported_set, **extra):
    return service.enqueue(EvaluationRequest(dataset_id=imported_set.dataset.id,
        conversation_ids=[c.id for c in imported_set.conversations], checklist_id=checklist.id, **extra))


def process_next(service):
    record = service.claim_next()
    assert record is not None
    asyncio.run(service.evaluate(record))
    return service.db.read("evaluations", record.id)


def test_manifests_keep_imports_separate_and_reimport_does_not_duplicate(tmp_path):
    service, checklist = make_service(tmp_path)
    dev = imported(service, "synthetic-dev", kind="dev", group="DEV")
    test = imported(service, "synthetic-test")
    repeat = imported(service, "synthetic-dev", kind="dev", group="DEV")
    assert repeat.dataset == dev.dataset and repeat.imported == 0
    assert set(dev.dataset.conversation_ids).isdisjoint(test.dataset.conversation_ids)
    with pytest.raises(AppError, match="не принадлежат"):
        service.enqueue(EvaluationRequest(dataset_id=dev.dataset.id,
            conversation_ids=test.dataset.conversation_ids, checklist_id=checklist.id))
    assert service.db.list("evaluations") == [] and service.db.list("runs") == []


def test_start_request_is_idempotent_and_conflicting_key_is_rejected(tmp_path):
    service, checklist = make_service(tmp_path)
    dataset = imported(service, "synthetic-one")
    first = enqueue(service, checklist, dataset, idempotency_key="synthetic-request")
    repeated = enqueue(service, checklist, dataset, idempotency_key="synthetic-request")
    assert first == repeated
    assert len(service.attempts(first.id)) == 1
    other = imported(service, "synthetic-other")
    with pytest.raises(AppError) as error:
        enqueue(service, checklist, other, idempotency_key="synthetic-request")
    assert error.value.code == "idempotency_conflict"


def test_concurrent_retry_is_single_attempt_and_old_snapshot_stays_immutable(tmp_path):
    calls = []
    async def evaluator(conversation, checklist):
        calls.append((conversation.external_id, checklist.version))
        if conversation.external_id == "synthetic-failed":
            raise AIProviderError("synthetic_failure", "Synthetic failure", retryable=True)
        result = draft(conversation, checklist)
        # Completed low score/false/N/A/critical remains successful and is never automatically retried.
        for criterion in result.criteria:
            if criterion.criterion_id == "1": criterion.value = False
            if criterion.criterion_id == "8": criterion.value = True
        return result
    service, checklist = make_service(tmp_path, evaluator)
    dataset = imported(service, "synthetic-success", "synthetic-failed", "synthetic-running")
    batch = enqueue(service, checklist, dataset)
    success = process_next(service)
    failed = process_next(service)
    running = service.claim_next()
    assert success.status == EvaluationStatus.COMPLETED and success.final_score.gross_error
    assert failed.status == EvaluationStatus.FAILED and running.status == EvaluationStatus.RUNNING
    v2 = service.create_checklist(official_checklist().model_copy(update={"name": "Synthetic second checklist"}))
    assert v2.version == 2
    request = RetryRequest(failed_evaluation_ids=[e.id for e in (success, failed, running)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: service.retry_failed(batch.id, request), range(2)))
    assert sum(r.retried for r in responses) == 1
    assert service.db.read("evaluations", success.id) == success
    latest = next(e for e in service.batch(batch.id).evaluations if e.conversation_id == failed.conversation_id)
    assert latest.retry_of == failed.id and latest.attempt_number == 2
    assert latest.checklist_snapshot == checklist
    retried_failed = process_next(service)
    assert retried_failed.status == EvaluationStatus.FAILED
    # Even if the second attempt fails before a double-click arrives, the original request cannot retry again.
    assert service.retry_failed(batch.id, request).retried == 0
    assert len(service.attempts(batch.id)) == 4
    assert service.retry_failed(batch.id, RetryRequest(failed_evaluation_ids=[retried_failed.id])).retried == 1
    assert len(service.attempts(batch.id)) == 5
    assert calls == [("synthetic-success", 1), ("synthetic-failed", 1), ("synthetic-failed", 1)]


def test_retry_rejects_foreign_run_ids_atomically(tmp_path):
    service, checklist = make_service(tmp_path)
    first = enqueue(service, checklist, imported(service, "first"))
    second = enqueue(service, checklist, imported(service, "second"))
    with pytest.raises(AppError) as error:
        service.retry_failed(first.id, RetryRequest(failed_evaluation_ids=[second.evaluations[0].id]))
    assert error.value.code == "retry_run_mismatch"
    assert len(service.attempts(first.id)) == 1


def test_legacy_migration_is_repeatable_and_preserves_payloads(tmp_path):
    async def evaluator(conversation, checklist): return draft(conversation, checklist)
    service, checklist = make_service(tmp_path, evaluator)
    batch = enqueue(service, checklist, imported(service, "legacy-fixture"))
    record = process_next(service)
    with service.db.connect(write=True) as con:
        before = con.execute("SELECT payload FROM evaluations WHERE id=?", (record.id,)).fetchone()[0]
        con.execute("DELETE FROM run_requests")
        con.execute("DELETE FROM runs")
        con.execute("DELETE FROM datasets")
    service.migrate_legacy_runs()
    service.migrate_legacy_runs()
    with service.db.connect() as con:
        assert con.execute("SELECT payload FROM evaluations WHERE id=?", (record.id,)).fetchone()[0] == before
    assert service.batch(batch.id).evaluations == [record]
    assert len(service.db.list("runs")) == len(service.db.list("datasets")) == 1


def test_result_metadata_and_appeal_survive_restart_without_overwriting_ai(tmp_path, monkeypatch):
    async def evaluator(conversation, checklist):
        value = draft(conversation, checklist)
        value.model = "synthetic-original-model"
        value.models_used = ["synthetic-original-model"]
        return value
    service, checklist = make_service(tmp_path, evaluator)
    batch = enqueue(service, checklist, imported(service, "review-fixture"))
    record = process_next(service)
    original_time = record.completed_at
    appeal = service.appeal(record.id, AppealCreate(criterion_id="4", author="Synthetic reviewer", reason="Synthetic appeal"))
    replacement = next(c for c in record.final_criteria if c.criterion_id == "4").model_copy(update={"value": 10.0})
    service.decide(appeal.id, ReviewDecisionCreate(outcome="accepted", reviewer="Synthetic reviewer",
                   reason="Synthetic decision", replacement=replacement))
    monkeypatch.setenv("GEMINI_MODEL", "synthetic-new-settings")
    service.db.initialize()
    service.migrate_legacy_runs()
    current = service.batch(batch.id).evaluations[0]
    assert current.ai_draft == record.ai_draft and current.raw_response == record.raw_response
    assert current.original_score == record.original_score
    assert current.completed_at == original_time and current.updated_at >= original_time
    assert current.checklist_snapshot == checklist
    assert current.final_score.total != current.original_score.total
    assert service.decisions(record.id)[0].appeal_id == appeal.id


def test_run_routes_return_scoped_progress_and_attempts(tmp_path):
    async def evaluator(conversation, checklist): raise AIProviderError("synthetic", "Synthetic provider error")
    app = create_app(settings=Settings(str(tmp_path / "http.sqlite3"), [], 1, 5, ""), evaluator=evaluator)
    with TestClient(app) as client:
        imported_set = client.post("/api/v1/imports/json", json={"conversations": [synthetic_transcript()]}).json()
        dataset = imported_set["dataset"]
        assert dataset["kind"] == "test"
        checklist = client.get("/api/v1/checklists").json()[0]
        batch = client.post("/api/v1/evaluations", json={"dataset_id": dataset["id"],
            "conversation_ids": dataset["conversation_ids"], "checklist_id": checklist["id"],
            "idempotency_key": "synthetic-http"}).json()
        runs = client.get("/api/v1/batches", params={"dataset_id": dataset["id"]}).json()
        assert [r["id"] for r in runs] == [batch["id"]]
        current = client.get(f"/api/v1/batches/{batch['id']}").json()
        assert current["counts"]["total"] == 1
        assert len(client.get(f"/api/v1/batches/{batch['id']}/attempts").json()) == 1
