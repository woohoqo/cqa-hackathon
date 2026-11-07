"""HTTP integration checks using synthetic transcripts and test-only AI functions.

These tests never contact Gemini or the organizer API. Every asynchronous result is
polled through the public API rather than assuming POST completed the evaluation.
"""
import asyncio
import csv
import io
import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app.contracts import AIProviderError, CriterionResult, EvaluationDraft
from backend.app.main import create_app
from backend.app.settings import Settings

API = "/api/v1"
RAW = ' \n{"synthetic": "тестовый ответ модели"}\r\n  '
OFFICIAL_VALUES = {"1": True, "2": "N/A", "3": "N/A", "4": 15, "5": True, "6": 7, "7": True, "8": False}


def synthetic_transcript(external_id="synthetic-001", *, source="synthetic", message_ids=True):
    messages = [
        {"role": "CLIENT (original)", "text": "  Synthetic question\n", "timestamp": "07.11.2026 12:34:56 +0500"},
        {"role": "ОПЕРАТОР (original)", "text": "Synthetic answer", "timestamp": "2026-11-07T12:35:01.000+05:00"},
    ]
    if message_ids:
        for index, message in enumerate(messages, 1):
            message["id"] = f"m{index}"
    return {"source": source, "external_id": external_id, "messages": messages, "metadata": {"fixture": "synthetic"}}


def draft(conversation, checklist):
    return EvaluationDraft(
        criteria=[CriterionResult(
            criterion_id=criterion.id,
            value=OFFICIAL_VALUES.get(criterion.id, True),
            reasoning="Synthetic test explanation, including N/A context",
            evidence_message_ids=[conversation.messages[0].id],
        ) for criterion in checklist.criteria],
        raw_response=RAW, provider="test-only", model="synthetic-fixture", prompt_version="test-v1",
    )


async def synthetic_evaluator(conversation, checklist):
    return draft(conversation, checklist)


def application(tmp_path, *, evaluator=synthetic_evaluator, concurrency=2, timeout=5):
    path = str(tmp_path / "cqa-test.sqlite3")
    settings = Settings(database_path=path, cors_origins=[], ai_concurrency=concurrency,
                        ai_timeout_seconds=timeout, dev_api_url="")
    return create_app(database_path=path, evaluator=evaluator, settings=settings)


def post_conversation(client, payload=None):
    response = client.post(f"{API}/conversations", json=payload or synthetic_transcript())
    assert response.status_code == 201, response.text
    return response.json()


def official(client):
    response = client.get(f"{API}/checklists")
    assert response.status_code == 200
    return next(item for item in response.json() if item["key"] == "freedom-cqa")


def enqueue(client, conversations, checklist_id):
    response = client.post(f"{API}/evaluations", json={"conversation_ids": [item["id"] for item in conversations], "checklist_id": checklist_id})
    assert response.status_code == 202, response.text
    batch = response.json()
    assert all(item["status"] == "queued" for item in batch["evaluations"])
    return batch


def poll(client, evaluation_id, *, statuses=("completed", "failed"), timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{API}/evaluations/{evaluation_id}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in statuses:
            return record
        time.sleep(0.02)
    pytest.fail(f"Evaluation did not reach {statuses}; last status: {record['status']}")


def completed_evaluation(client):
    conversation = post_conversation(client)
    checklist = official(client)
    batch = enqueue(client, [conversation], checklist["id"])
    evaluation = poll(client, batch["evaluations"][0]["id"])
    assert evaluation["status"] == "completed", evaluation["error"]
    return conversation, checklist, batch, evaluation


def test_import_preserves_original_fields_and_stable_ids(tmp_path):
    payload = synthetic_transcript(message_ids=False)
    with TestClient(application(tmp_path)) as client:
        assert client.get("/health").json()["status"] == "ok"
        created = post_conversation(client, payload)
        assert len({item["id"] for item in created["messages"]}) == 2
        for source, stored in zip(payload["messages"], created["messages"]):
            assert {key: stored[key] for key in source} == source
        replay = client.post(f"{API}/imports/json", json={"conversations": [payload]})
        assert replay.status_code == 200
        assert replay.json()["imported"] == 0
        assert replay.json()["existing"] == 1
        assert replay.json()["conversations"] == [created]
        assert client.get(f"{API}/conversations/{created['id']}").json() == created
        explicit = post_conversation(client, synthetic_transcript("synthetic-explicit"))
        assert [message["id"] for message in explicit["messages"]] == ["m1", "m2"]


def test_json_import_conflict_rolls_back_entire_batch(tmp_path):
    with TestClient(application(tmp_path)) as client:
        original = post_conversation(client)
        conflict = synthetic_transcript()
        conflict["messages"][0]["text"] = "Synthetic conflicting revision"
        response = client.post(f"{API}/imports/json", json={"conversations": [synthetic_transcript("new-before-conflict"), conflict]})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "source_conflict"
        assert client.get(f"{API}/conversations").json() == [original]


def test_ambiguous_source_external_id_pairs_do_not_collide(tmp_path):
    with TestClient(application(tmp_path)) as client:
        first = post_conversation(client, synthetic_transcript("c", source="a:b", message_ids=False))
        second = post_conversation(client, synthetic_transcript("b:c", source="a", message_ids=False))
        assert first["id"] != second["id"]
        assert first["messages"][0]["id"] != second["messages"][0]["id"]


def test_single_evaluation_official_example_and_raw_response(tmp_path):
    with TestClient(application(tmp_path)) as client:
        conversation, checklist, batch, evaluation = completed_evaluation(client)
        assert evaluation["conversation_id"] == conversation["id"]
        assert evaluation["checklist_snapshot"] == checklist
        assert evaluation["original_score"]["total"] == 85.38
        assert evaluation["original_score"]["applicable_weight"] == 65
        assert evaluation["original_score"]["earned_points"] == 55.5
        assert evaluation["final_score"] == evaluation["original_score"]
        assert evaluation["raw_response"] == evaluation["ai_draft"]["raw_response"] == RAW
        assert evaluation["final_score"]["gross_error"] is False
        assert client.get(f"{API}/batches/{batch['id']}").json()["evaluations"] == [evaluation]


def test_batch_queue_and_invalid_batch_are_atomic(tmp_path):
    with TestClient(application(tmp_path)) as client:
        conversations = [post_conversation(client, synthetic_transcript(f"synthetic-{i}")) for i in range(3)]
        checklist = official(client)
        invalid = client.post(f"{API}/evaluations", json={"conversation_ids": [conversations[0]["id"], "unknown-id"], "checklist_id": checklist["id"]})
        assert invalid.status_code == 404
        assert client.get(f"{API}/evaluations").json() == []
        batch = enqueue(client, conversations, checklist["id"])
        results = [poll(client, item["id"]) for item in batch["evaluations"]]
        assert all(item["status"] == "completed" for item in results)
        assert {item["conversation_id"] for item in results} == {item["id"] for item in conversations}
        assert all(item["batch_id"] == batch["id"] for item in results)
        filtered = client.get(f"{API}/evaluations", params={"conversation_id": conversations[0]["id"]}).json()
        assert len(filtered) == 1 and filtered[0]["conversation_id"] == conversations[0]["id"]


def test_new_checklist_version_keeps_snapshot_and_supports_new_criterion(tmp_path):
    with TestClient(application(tmp_path)) as client:
        conversation, first, _, evaluation = completed_evaluation(client)
        changed = {key: value for key, value in deepcopy(first).items() if key not in ("id", "version", "created_at")}
        changed["criteria"][0]["weight"] = 30
        changed["criteria"].append({"id": "new-dynamic-id", "section_id": changed["sections"][0]["id"],
                                   "title": "Synthetic new criterion", "weight": 5, "answer_type": "boolean"})
        response = client.post(f"{API}/checklists", json=changed)
        assert response.status_code == 201
        second = response.json()
        assert second["version"] == first["version"] + 1
        assert second["id"] != first["id"]
        assert client.get(f"{API}/evaluations/{evaluation['id']}").json() == evaluation
        assert client.get(f"{API}/checklists/{first['id']}").json() == first
        batch = enqueue(client, [conversation], second["id"])
        later = poll(client, batch["evaluations"][0]["id"])
        assert later["status"] == "completed"
        assert later["checklist_snapshot"] == second
        assert later["original_score"]["total"] == 89.44  # 80.5 / 90 * 100.
        assert "new-dynamic-id" in {item["criterion_id"] for item in later["final_criteria"]}


def test_appeal_accept_reject_audit_and_export_preserve_ai(tmp_path):
    with TestClient(application(tmp_path)) as client:
        _, _, batch, before = completed_evaluation(client)
        path = f"{API}/evaluations/{before['id']}"
        appeal_body = {"criterion_id": "6", "reason": "Synthetic appeal", "author": "synthetic-author", "proposed_value": 10}
        response = client.post(f"{path}/appeals", json=appeal_body)
        assert response.status_code == 201
        accepted_appeal = response.json()
        assert client.post(f"{path}/appeals", json=appeal_body).status_code == 409
        replacement = {"criterion_id": "6", "value": 10, "reasoning": "Synthetic manual correction", "evidence_message_ids": ["m2"]}
        decision = {"outcome": "accepted", "reviewer": "synthetic-reviewer", "reason": "Synthetic decision", "replacement": replacement}
        decision_path = f"{API}/appeals/{accepted_appeal['id']}/decisions"
        accepted = client.post(decision_path, json=decision)
        assert accepted.status_code == 201
        assert client.post(decision_path, json=decision).status_code == 409
        after = client.get(path).json()
        assert after["final_score"]["total"] == 92.31  # 60 / 65 * 100.
        for key in ("ai_draft", "raw_response", "original_score", "checklist_snapshot"):
            assert after[key] == before[key]
        rejected_appeal = client.post(f"{path}/appeals", json={"criterion_id": "1", "reason": "Synthetic rejected appeal", "author": "synthetic-author"}).json()
        rejected = client.post(f"{API}/appeals/{rejected_appeal['id']}/decisions", json={"outcome": "rejected", "reviewer": "synthetic-reviewer", "reason": "Original answer confirmed"})
        assert rejected.status_code == 201
        assert client.get(path).json() == after
        appeals = client.get(f"{path}/appeals").json()
        assert [item["status"] for item in appeals] == ["accepted", "rejected"]
        decisions = client.get(f"{path}/decisions").json()
        assert decisions == [accepted.json(), rejected.json()]
        exported = client.get(f"{API}/exports/evaluations", params={"batch_id": batch["id"]})
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
        assert exported.json()["evaluations"] == [after]
        assert exported.json()["appeals"] == appeals
        assert exported.json()["decisions"] == decisions
        csv_response = client.get(f"{API}/exports/evaluations", params={"format": "csv", "batch_id": batch["id"]})
        rows = list(csv.DictReader(io.StringIO(csv_response.text.lstrip("\ufeff"))))
        assert rows[0]["original_total"] == "85.38"
        assert rows[0]["final_total"] == "92.31"


@pytest.mark.parametrize("kind", ["evidence", "missing", "duplicate", "scale", "boolean"])
def test_invalid_model_output_is_failed_and_raw_is_preserved(tmp_path, kind):
    async def invalid_evaluator(conversation, checklist):
        output = draft(conversation, checklist)
        if kind == "evidence":
            output.criteria[0].evidence_message_ids = ["nonexistent-message"]
        elif kind == "missing":
            output.criteria.pop()
        elif kind == "duplicate":
            output.criteria.append(output.criteria[0].model_copy(deep=True))
        elif kind == "scale":
            output.criteria[5].value = 9.0
        elif kind == "boolean":
            output.criteria[0].value = 1.0
        return output

    with TestClient(application(tmp_path, evaluator=invalid_evaluator)) as client:
        conversation = post_conversation(client)
        batch = enqueue(client, [conversation], official(client)["id"])
        failed = poll(client, batch["evaluations"][0]["id"])
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "invalid_ai_response"
        assert failed["raw_response"] == failed["ai_draft"]["raw_response"] == RAW
        assert failed["original_score"] is None and failed["final_score"] is None
        assert client.post(f"{API}/evaluations/{failed['id']}/appeals", json={"criterion_id": "1", "reason": "Synthetic", "author": "synthetic"}).status_code == 409


def test_provider_parse_error_preserves_exact_raw_response(tmp_path):
    async def malformed_provider(conversation, checklist):
        raise AIProviderError("invalid_ai_response", "Synthetic malformed response", raw_response=RAW)

    with TestClient(application(tmp_path, evaluator=malformed_provider)) as client:
        conversation = post_conversation(client)
        batch = enqueue(client, [conversation], official(client)["id"])
        failed = poll(client, batch["evaluations"][0]["id"])
        assert failed["status"] == "failed"
        assert failed["raw_response"] == RAW
        assert failed["ai_draft"] is None
        assert failed["final_score"] is None


def test_unconfigured_provider_has_no_fabricated_result(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    with TestClient(application(tmp_path, evaluator=None)) as client:
        conversation = post_conversation(client)
        batch = enqueue(client, [conversation], official(client)["id"])
        failed = poll(client, batch["evaluations"][0]["id"])
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "provider_not_configured"
        assert failed["ai_draft"] is None and failed["raw_response"] is None
        assert failed["original_score"] is None and failed["final_score"] is None


def test_timeout_is_terminal_and_retryable(tmp_path):
    async def slow_evaluator(conversation, checklist):
        await asyncio.sleep(60)
        return draft(conversation, checklist)

    with TestClient(application(tmp_path, evaluator=slow_evaluator, timeout=0.03)) as client:
        conversation = post_conversation(client)
        batch = enqueue(client, [conversation], official(client)["id"])
        failed = poll(client, batch["evaluations"][0]["id"])
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "ai_timeout"
        assert failed["error"]["retryable"] is True
        assert failed["final_score"] is None


def test_restart_resumes_queued_jobs_and_keeps_completed_data(tmp_path):
    # Zero workers deliberately simulates a process stopping before queue consumption.
    with TestClient(application(tmp_path, concurrency=0)) as client:
        conversation = post_conversation(client, synthetic_transcript(message_ids=False))
        checklist = official(client)
        batch = enqueue(client, [conversation], checklist["id"])
        evaluation_id = batch["evaluations"][0]["id"]
        assert poll(client, evaluation_id, statuses=("queued",))["status"] == "queued"
    with TestClient(application(tmp_path)) as client:
        completed = poll(client, evaluation_id)
        assert completed["status"] == "completed"
        assert completed["checklist_snapshot"] == checklist
        assert client.get(f"{API}/conversations/{conversation['id']}").json() == conversation
        replay = client.post(f"{API}/imports/json", json={"conversations": [synthetic_transcript(message_ids=False)]})
        assert replay.json()["existing"] == 1 and replay.json()["imported"] == 0
    with TestClient(application(tmp_path)) as client:
        assert poll(client, evaluation_id) == completed
        assert len(client.get(f"{API}/checklists").json()) == 1


def test_restart_marks_interrupted_running_job_without_replaying_it(tmp_path):
    app = application(tmp_path, concurrency=0)
    with TestClient(app) as client:
        conversation = post_conversation(client)
        batch = enqueue(client, [conversation], official(client)["id"])
        evaluation_id = batch["evaluations"][0]["id"]
        # Simulate the durable state left by an abrupt process exit during an AI call.
        app.state.service.claim_next()
        assert poll(client, evaluation_id, statuses=("running",))["status"] == "running"
    with TestClient(application(tmp_path)) as client:
        failed = poll(client, evaluation_id)
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "interrupted"
        assert failed["error"]["retryable"] is True
        assert failed["ai_draft"] is None


@pytest.mark.parametrize("forge_evidence", [False, True])
def test_evaluator_cannot_mutate_stored_snapshot_or_evidence_source(tmp_path, forge_evidence):
    async def mutating_evaluator(conversation, checklist):
        output = draft(conversation, checklist)
        checklist.criteria[0].weight = 999
        conversation.messages[0].id = "fabricated-message"
        conversation.messages[0].text = "Synthetic mutated text"
        if forge_evidence:
            output.criteria[0].evidence_message_ids = [conversation.messages[0].id]
        return output

    with TestClient(application(tmp_path, evaluator=mutating_evaluator)) as client:
        conversation = post_conversation(client)
        checklist = official(client)
        batch = enqueue(client, [conversation], checklist["id"])
        record = poll(client, batch["evaluations"][0]["id"])
        assert record["checklist_snapshot"] == checklist
        assert client.get(f"{API}/conversations/{conversation['id']}").json() == conversation
        if forge_evidence:
            assert record["status"] == "failed"
            assert record["error"]["code"] == "invalid_ai_response"
        else:
            assert record["status"] == "completed"
            assert record["final_score"]["total"] == 85.38


def test_invalid_review_does_not_partially_update_evaluation(tmp_path):
    with TestClient(application(tmp_path)) as client:
        _, _, _, before = completed_evaluation(client)
        path = f"{API}/evaluations/{before['id']}"
        appeal = client.post(f"{path}/appeals", json={"criterion_id": "6", "reason": "Synthetic appeal", "author": "synthetic"}).json()
        decision_path = f"{API}/appeals/{appeal['id']}/decisions"
        decision = {"outcome": "accepted", "reviewer": "synthetic", "reason": "Synthetic correction", "replacement": {
            "criterion_id": "6", "value": 10, "reasoning": "Synthetic", "evidence_message_ids": ["nonexistent-message"]}}
        assert client.post(decision_path, json=decision).status_code == 422
        decision["replacement"]["evidence_message_ids"] = []
        assert client.post(decision_path, json=decision).status_code == 422
        assert client.get(path).json() == before
        assert client.get(f"{path}/decisions").json() == []
        assert client.get(f"{path}/appeals").json()[0]["status"] == "open"
