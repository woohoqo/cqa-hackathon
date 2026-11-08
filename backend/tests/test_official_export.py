"""Synthetic fixtures only: official format/scoring, never organizer transcripts."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from backend.app.contracts import (
    AIConnectionAttempt, AIExecutionMetadata, AppealCreate, ChecklistVersion, Conversation,
    CriterionResult, ErrorDetail, Evaluation, EvaluationDraft, Message, ReviewDecisionCreate,
)
from backend.app.default_checklist import official_checklist
from backend.app.official_export import (
    OFFICIAL_IDS, OfficialExportError, build_official_export,
    official_results_schema, validate_official_export,
)
from backend.app.scoring import calculate_score
from backend.app.service import CQAService
from backend.app.settings import Settings
from backend.app.storage import Database


def synthetic_case(number=1):
    conversation = Conversation(
        id=f"internal-{number}", external_id=f"synthetic-source-{number}", source="synthetic-test",
        messages=[Message(id=f"stable-{number}-{i}", role=role, text=f"Synthetic message {i}")
                  for i, role in enumerate(["system", "client", "bot", "operator", "client", "operator"], 1)],
    )
    checklist = ChecklistVersion(**official_checklist().model_dump(), id="snapshot-v1", version=1)
    answers = [True, "N/A", "N/A", 15, True, 7, True, False]
    criteria = [CriterionResult(
        criterion_id=str(i), value=value, reasoning=f"Synthetic reasoning {i}",
        evidence_message_ids=[conversation.messages[position - 1].id for position in (1, 3, 4, 6)],
    ) for i, value in enumerate(answers, 1)]
    draft = EvaluationDraft(criteria=deepcopy(criteria), raw_response='  { "synthetic": true }\n',
                            provider="gemini", model="synthetic-model", prompt_version="synthetic-v1")
    score = calculate_score(checklist, criteria)
    evaluation = Evaluation(
        id=f"evaluation-{number}", batch_id="synthetic-run", conversation_id=conversation.id,
        checklist_snapshot=checklist, status="completed", ai_draft=draft,
        raw_response=draft.raw_response, original_score=deepcopy(score), final_score=score,
        final_criteria=criteria,
    )
    return conversation, evaluation


@pytest.fixture
def case():
    return synthetic_case()


def build(case, **kwargs):
    conversation, evaluation = case
    return build_official_export([conversation], [evaluation], "cuda cuda", kwargs.pop("dataset_kind", "test"), **kwargs)


def validate(payload, case, **kwargs):
    return validate_official_export(payload, [case[0]], **kwargs)


def errors(report):
    return {item["code"] for item in report["errors"]}


def assert_blocked(case, code, **kwargs):
    with pytest.raises(OfficialExportError) as exc:
        build(case, **kwargs)
    assert code in errors(exc.value.report)
    assert not exc.value.report["valid"]
    return exc.value.report


def execution(*models, network_attempted=True, provider="gemini"):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return AIExecutionMetadata(
        run_id="synthetic-run", duration_ms=1,
        attempts=[AIConnectionAttempt(
            attempt=i, connection_id="primary", quota_group="synthetic", provider=provider,
            model=model, started_at=now, finished_at=now, duration_ms=1, status="error",
            error_code="synthetic_error", network_attempted=network_attempted,
        ) for i, model in enumerate(models, 1)],
    )


def make_failed(evaluation, *, model="synthetic-failed-model"):
    evaluation.status = "failed"
    evaluation.ai_draft = None
    evaluation.final_criteria = None
    evaluation.final_score = None
    evaluation.original_score = None
    evaluation.error = ErrorDetail(code="invalid_response", message="Synthetic safe processing error")
    evaluation.ai_execution = execution(model) if model else None


def test_required_8538_exact_mapping_full_transcript_and_no_mutation(case):
    before = deepcopy(case)
    payload = build(case)
    row = payload["results"][0]
    assert list(payload) == ["schema_version", "team_name", "model", "results"]
    assert payload["schema_version"] == "1.0"
    assert payload["model"] == {"mode": "external_api", "name": "synthetic-model"}
    assert row["conversation_id"] == case[0].external_id != case[0].id
    assert [item["criterion_id"] for item in row["criteria"]] == list(OFFICIAL_IDS)
    assert [item["value"] for item in row["criteria"]] == [True, None, None, 15, True, 7, True, False]
    assert type(row["criteria"][3]["value"]) is int
    assert type(row["criteria"][5]["value"]) is int
    assert all(item["evidence_turns"] == [1, 3, 4, 6] for item in row["criteria"])
    assert row["score"] == {"final_score": 85.38, "critical_flag": False}
    assert case == before
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert "raw_response" not in serialized and "Synthetic message" not in serialized
    report = validate(serialized.encode("utf-8"), case, evaluations=[case[1]], expected_team_name="cuda cuda")
    assert report["valid"] and not report["warnings"]
    assert report["counts"] == {"expected": 1, "actual": 1, "ok": 1, "error": 0}


def test_human_changes_never_replace_original_automatic_submission(case):
    evaluation = case[1]
    expected = build(case)
    evaluation.final_criteria[0].value = False
    evaluation.final_criteria[0].reasoning = "Synthetic human review"
    evaluation.final_criteria[0].evidence_message_ids = [case[0].messages[1].id]
    evaluation.final_criteria[1].value = True  # A review changes N/A and its denominator.
    evaluation.final_criteria[-1].value = True  # A review changes the separate critical flag.
    evaluation.final_score = calculate_score(evaluation.checklist_snapshot, evaluation.final_criteria)
    stored = deepcopy(case)
    assert evaluation.final_score.total != evaluation.original_score.total
    payload = build(case)
    assert payload == expected and case == stored
    assert validate(payload, case, evaluations=[evaluation])["valid"]
    assert payload["results"][0]["criteria"][1]["value"] is None
    assert payload["results"][0]["score"] == {"final_score": 85.38, "critical_flag": False}


@pytest.mark.parametrize("outcome", ["accepted", "rejected"])
@pytest.mark.parametrize("criterion_id,value", [("1", False), ("8", True)])
def test_persisted_appeal_decision_keeps_official_original_and_internal_review(
    tmp_path, case, outcome, criterion_id, value,
):
    conversation, evaluation = case
    conversation.messages[3].text = "My name is Elara. Synthetic introduction."
    evaluation.ai_draft.criteria[0].reasoning = "Synthetic operator Elara introduced herself."
    evaluation.final_criteria[0].reasoning = evaluation.ai_draft.criteria[0].reasoning
    evaluation.raw_response = evaluation.ai_draft.raw_response = '  { "synthetic_name": "Elara" }\n'
    path = str(tmp_path / "synthetic-appeal.sqlite3")
    db = Database(path)
    db.initialize()
    with db.connect(write=True) as con:
        con.execute("INSERT INTO conversations VALUES (?,?,?,?)",
                    (conversation.id, conversation.source, conversation.external_id, conversation.model_dump_json()))
        con.execute("INSERT INTO evaluations VALUES (?,?,?,?,?)",
                    (evaluation.id, evaluation.batch_id, conversation.id, evaluation.status.value, evaluation.model_dump_json()))
    service = CQAService(db, Settings(path, [], 1, 1, ""))
    expected = build(case)
    assert expected["results"][0]["criteria"][0]["rationale"] == (
        "Synthetic operator [имя скрыто] introduced herself."
    )
    appeal = service.appeal(evaluation.id, AppealCreate(
        criterion_id=criterion_id, reason="Synthetic submission provenance check",
        author="synthetic-author", proposed_value=value,
    ))
    replacement = CriterionResult(criterion_id=criterion_id, value=value,
                                  reasoning="Synthetic human decision", evidence_message_ids=[conversation.messages[1].id])
    service.decide(appeal.id, ReviewDecisionCreate(
        outcome=outcome, reviewer="synthetic-reviewer", reason="Synthetic appeal decision",
        replacement=replacement if outcome == "accepted" else None,
    ))
    persisted = db.read("evaluations", evaluation.id)
    before_export = persisted.model_dump_json()
    assert build((conversation, persisted)) == expected
    assert validate(expected, case, evaluations=[persisted])["valid"]
    assert db.read("evaluations", evaluation.id).model_dump_json() == before_export
    assert persisted.ai_draft == evaluation.ai_draft
    assert persisted.raw_response == evaluation.raw_response
    assert persisted.original_score == evaluation.original_score
    assert persisted.checklist_snapshot == evaluation.checklist_snapshot
    reviewed = next(item for item in persisted.final_criteria if item.criterion_id == criterion_id)
    if outcome == "accepted":
        assert reviewed == replacement
        assert persisted.final_score.total == (70 if criterion_id == "1" else 85.38)
        assert persisted.final_score.gross_error is (criterion_id == "8")
    else:
        assert persisted.final_criteria == evaluation.final_criteria
        assert persisted.final_score == evaluation.final_score
    assert db.read("appeals", appeal.id).status == outcome
    assert service.decisions(evaluation.id)[0].outcome == outcome


@pytest.mark.parametrize("source_position", [0, 1, 2, 3])
def test_privacy_projection_uses_all_roles_without_changing_ai_or_other_fields(case, source_position):
    conversation, evaluation = case
    conversation.messages[source_position].text = "Меня зовут Лира. Synthetic introduction."
    evaluation.ai_draft.criteria[0].reasoning = "  Synthetic: «ЛИРА», with evidence.\n"
    evaluation.ai_draft.raw_response = evaluation.raw_response = ' { "synthetic": "Лира" }\r\n'
    before = deepcopy(case)
    payload = build(case)
    row = payload["results"][0]
    assert row["criteria"][0] == {
        "criterion_id": "contact", "value": True,
        "rationale": "  Synthetic: «[имя скрыто]», with evidence.\n", "evidence_turns": [1, 3, 4, 6],
    }
    assert row["score"] == {"final_score": 85.38, "critical_flag": False}
    assert case == before
    assert validate(payload, case, evaluations=[evaluation])["valid"]
    unprojected = deepcopy(payload)
    unprojected["results"][0]["criteria"][0]["rationale"] = evaluation.ai_draft.criteria[0].reasoning
    assert "stored_result_mismatch" in errors(validate(unprojected, case, evaluations=[evaluation]))
    tampered = deepcopy(payload)
    tampered["results"][0]["criteria"][0]["rationale"] = "Different synthetic rationale."
    assert "stored_result_mismatch" in errors(validate(tampered, case, evaluations=[evaluation]))


def test_privacy_projection_is_scoped_to_the_same_conversation():
    first, second = synthetic_case(1), synthetic_case(2)
    first[0].messages[1].text = "My name is Elara."
    for _, evaluation in (first, second):
        evaluation.ai_draft.criteria[0].reasoning = "Synthetic Elara mention."
    before = deepcopy((first, second))
    payload = build_official_export([first[0], second[0]], [first[1], second[1]], "cuda cuda", "test")
    assert [row["criteria"][0]["rationale"] for row in payload["results"]] == [
        "Synthetic [имя скрыто] mention.", "Synthetic Elara mention.",
    ]
    assert (first, second) == before


def test_failed_export_projects_error_only_and_preserves_private_record(case):
    conversation, evaluation = case
    conversation.messages[1].text = "Менің атым Қиял. Synthetic introduction."
    make_failed(evaluation)
    evaluation.error.message = "Synthetic failure for ҚИЯЛ; retry allowed."
    evaluation.raw_response = '  { "synthetic": "Қиял" }\n'
    before = deepcopy(case)
    payload = build(case)
    assert payload["results"][0] == {
        "conversation_id": conversation.external_id, "status": "error", "criteria": [], "score": None,
        "error": "Synthetic failure for [имя скрыто]; retry allowed.",
    }
    assert case == before
    assert validate(payload, case, evaluations=[evaluation])["valid"]


def test_critical_flag_does_not_zero_total(case):
    evaluation = case[1]
    evaluation.ai_draft.criteria[-1].value = True
    evaluation.original_score = calculate_score(evaluation.checklist_snapshot, evaluation.ai_draft.criteria)
    assert build(case)["results"][0]["score"] == {"final_score": 85.38, "critical_flag": True}


@pytest.mark.parametrize("kind,count", [("dev", 65), ("prod", 10), ("test", 2)])
def test_declared_dataset_counts_and_exact_full_source_set(kind, count):
    pairs = [synthetic_case(i) for i in range(count)]
    conversations, evaluations = map(list, zip(*pairs))
    payload = build_official_export(conversations, evaluations, "cuda cuda", kind)
    assert len(payload["results"]) == count
    assert validate_official_export(payload, conversations, evaluations=evaluations, dataset_kind=kind)["valid"]


@pytest.mark.parametrize("kind", ["dev", "prod"])
def test_official_counts_not_applied_to_test_datasets(case, kind):
    assert_blocked(case, "dataset_count", dataset_kind=kind)
    assert build(case)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_unfinished_evaluation_not_fabricated_as_error(case, status):
    case[1].status = status
    report = assert_blocked(case, "unfinished_evaluation")
    assert report["counts"]["actual"] == 0
    assert any(item.get("conversation_id") == case[0].external_id for item in report["errors"])


def test_missing_duplicate_and_unrelated_evaluations_rejected(case):
    conversation, evaluation = case
    for values, code in [([], "missing_evaluation"), ([evaluation, evaluation], "duplicate_evaluation"),
                         ([synthetic_case(2)[1]], "unexpected_evaluation")]:
        with pytest.raises(OfficialExportError) as exc:
            build_official_export([conversation], values, "cuda cuda", "test")
        assert code in errors(exc.value.report)


@pytest.mark.parametrize("field", ["ai_draft", "original_score"])
def test_completed_but_missing_data_blocks(case, field):
    setattr(case[1], field, None)
    assert_blocked(case, "incomplete_completed_evaluation")


@pytest.mark.parametrize("mutator,code", [
    (lambda e: setattr(e.original_score, "total", 0), "stored_score_mismatch"),
    (lambda e: setattr(e.original_score, "gross_error", True), "stored_score_mismatch"),
    (lambda e: setattr(e.original_score, "total", None), "uncomputed_score"),
    (lambda e: e.ai_draft.criteria.pop(), "invalid_stored_results"),
    (lambda e: e.ai_draft.criteria.append(deepcopy(e.ai_draft.criteria[0])), "invalid_stored_results"),
    (lambda e: setattr(e.ai_draft.criteria[0], "value", "N/A"), "invalid_stored_results"),
    (lambda e: setattr(e.ai_draft.criteria[5], "value", 6.0), "invalid_stored_results"),
    (lambda e: setattr(e.ai_draft.criteria[0], "evidence_message_ids", ["unknown"]), "invalid_stored_results"),
])
def test_corrupt_original_not_replaced_by_valid_final_review(case, mutator, code):
    mutator(case[1])
    assert_blocked(case, code)


@pytest.mark.parametrize("field", ["final_criteria", "final_score"])
def test_missing_human_final_fields_do_not_replace_or_block_valid_original(case, field):
    expected = build(case)
    setattr(case[1], field, None)
    assert build(case) == expected


def test_human_final_payload_fails_original_provenance_even_when_arithmetic_is_valid(case):
    payload = build(case)
    evaluation = case[1]
    evaluation.final_criteria[0].value = False
    evaluation.final_score = calculate_score(evaluation.checklist_snapshot, evaluation.final_criteria)
    payload["results"][0]["criteria"][0]["value"] = False
    payload["results"][0]["score"]["final_score"] = evaluation.final_score.total
    assert validate(payload, case)["valid"]  # Wire format and arithmetic alone are insufficient.
    assert "stored_result_mismatch" in errors(validate(payload, case, evaluations=[evaluation]))


@pytest.mark.parametrize("mutator", [
    lambda c: setattr(c.criteria[0], "weight", 11),
    lambda c: setattr(c.criteria[0], "title", "Changed meaning"),
    lambda c: setattr(c.criteria[0], "description", "Changed meaning"),
    lambda c: setattr(c, "description", "Changed semantics"),
    lambda c: setattr(c.criteria[0], "answer_type", "scale"),
    lambda c: setattr(c.criteria[1], "na_condition", "Changed applicability"),
    lambda c: setattr(c.criteria[1], "allow_na", False),
    lambda c: setattr(c.criteria[5].scale[-1], "value", 15),
    lambda c: setattr(c.criteria[5].scale[-1], "description", "Changed meaning"),
    lambda c: setattr(c.criteria[0], "id", "contact"),
    lambda c: c.criteria.pop(),
    lambda c: c.criteria.append(deepcopy(c.criteria[0])),
])
def test_changed_checklist_not_silently_remapped(case, mutator):
    mutator(case[1].checklist_snapshot)
    assert_blocked(case, "incompatible_checklist")


def test_equivalent_new_version_and_reordered_criteria_work(case):
    checklist = case[1].checklist_snapshot
    checklist.version = 7
    checklist.id = "equivalent-version"
    checklist.name = "UI display name"
    checklist.criteria.reverse()
    checklist.criteria[0].description += "\n "
    assert build(case)["results"][0]["score"]["final_score"] == 85.38


def test_source_id_is_mandatory_and_source_duplicates_block(case):
    conversation, evaluation = case
    conversation.external_id = None
    assert_blocked(case, "missing_source_id", dataset_kind="dev")
    assert_blocked(case, "missing_source_id", dataset_kind="prod")
    conversation.external_id = "same-source"
    with pytest.raises(OfficialExportError) as exc:
        build_official_export([conversation, conversation], [evaluation], "cuda cuda", "test")
    assert "duplicate_source_id" in errors(exc.value.report)


def test_manual_test_transcript_uses_saved_stable_id_without_mutating_source(case):
    case[0].external_id = None
    before = deepcopy(case)
    payload = build(case)
    assert payload["results"][0]["conversation_id"] == case[0].id
    assert validate(payload, case, evaluations=[case[1]])["valid"]
    assert case == before


def test_failure_is_valid_retained_with_warning_and_actual_attempt_model(case):
    make_failed(case[1])
    payload = build(case)
    assert payload["model"]["name"] == "synthetic-failed-model"
    assert payload["results"] == [{"conversation_id": case[0].external_id, "status": "error",
                                  "criteria": [], "score": None, "error": "Synthetic safe processing error"}]
    report = validate(payload, case, evaluations=[case[1]])
    assert report["valid"] and report["counts"]["error"] == 1
    assert [item["code"] for item in report["warnings"]] == ["evaluation_error"]


def test_failed_retry_models_only_include_actual_network_attempts(case):
    make_failed(case[1])
    case[1].ai_execution = execution("synthetic-first", "synthetic-second", "never-called")
    case[1].ai_execution.attempts[-1].network_attempted = False
    assert build(case)["model"] == {"mode": "external_api", "name": "synthetic-first, synthetic-second"}


@pytest.mark.parametrize("metadata", [None, execution("not-called", network_attempted=False)])
def test_no_actual_model_metadata_never_uses_config_or_fake_name(case, metadata, monkeypatch):
    make_failed(case[1], model=None)
    case[1].ai_execution = metadata
    monkeypatch.setenv("GEMINI_MODEL", "must-not-read-config")
    assert_blocked(case, "missing_model_metadata")


def test_unknown_failed_model_is_not_hidden_by_other_completed_evaluation():
    c1, e1 = synthetic_case(1)
    c2, e2 = synthetic_case(2)
    make_failed(e2, model=None)
    with pytest.raises(OfficialExportError) as exc:
        build_official_export([c1, c2], [e1, e2], "cuda cuda", "test")
    assert any(item["code"] == "missing_model_metadata" and item.get("conversation_id") == c2.external_id
               for item in exc.value.report["errors"])


def test_pre_network_configuration_failure_can_coexist_with_known_actual_model():
    c1, e1 = synthetic_case(1)
    c2, e2 = synthetic_case(2)
    make_failed(e2, model=None)
    e2.error.code = "provider_not_configured"
    payload = build_official_export([c1, c2], [e1, e2], "cuda cuda", "test")
    assert payload["model"]["name"] == "synthetic-model"
    assert payload["results"][1]["status"] == "error"


def test_multiple_models_and_hybrid_mode_come_from_saved_evaluations():
    c1, e1 = synthetic_case(1)
    c2, e2 = synthetic_case(2)
    e1.ai_draft.models_used = ["synthetic-model", "synthetic-fallback"]
    e2.ai_draft.provider = "local"
    e2.ai_draft.model = "synthetic-local"
    payload = build_official_export([c1, c2], [e1, e2], "cuda cuda", "test")
    assert payload["model"] == {"mode": "hybrid", "name": "synthetic-fallback, synthetic-local, synthetic-model"}


def test_unknown_provider_needs_explicit_factual_mode_and_cannot_override_model(case):
    case[1].ai_draft.provider = "synthetic-provider"
    assert_blocked(case, "unknown_model_mode")
    actual = [{"name": "synthetic-model", "mode": "local"}]
    assert build(case, expected_models=actual)["model"]["mode"] == "local"
    assert_blocked(case, "model_provenance_mismatch", expected_models=[{"name": "invented", "mode": "local"}])


def test_success_final_model_must_be_in_recorded_model_list(case):
    case[1].ai_draft.models_used = ["different-model"]
    assert_blocked(case, "model_provenance_mismatch")


@pytest.mark.parametrize("index,value", [
    (0, "true"), (0, 1), (0, 1.0), (0, None), (0, "N/A"),
    (1, "N/A"), (2, 0), (3, True), (3, 7), (3, "15"),
    (4, None), (5, None), (5, 15), (5, False), (7, None), (7, 0),
    (3, float("inf")), (3, float("nan")), (5, 7.01),
])
def test_official_value_types_and_numeric_steps_are_strict(case, index, value):
    payload = build(case)
    payload["results"][0]["criteria"][index]["value"] = value
    assert "invalid_criterion_value" in errors(validate(payload, case))


def test_numeric_scale_steps_accept_equivalent_json_float_values(case):
    payload = build(case)
    payload["results"][0]["criteria"][3]["value"] = 15.0
    payload["results"][0]["criteria"][5]["value"] = 7.0
    assert validate(payload, case, evaluations=[case[1]])["valid"]
    assert validate(json.dumps(payload), case, evaluations=[case[1]])["valid"]


@pytest.mark.parametrize("evidence,code", [
    ([], "missing_evidence"), (None, "missing_evidence"), ((1,), "missing_evidence"),
    ([0], "invalid_evidence_turns"), ([7], "invalid_evidence_turns"), ([True], "invalid_evidence_turns"),
    ([1.0], "invalid_evidence_turns"), (["1"], "invalid_evidence_turns"), ([1, 1], "invalid_evidence_turns"),
    ([{}], "invalid_evidence_turns"), ([[]], "invalid_evidence_turns"),
])
def test_evidence_positions_not_coerced_or_renumbered(case, evidence, code):
    payload = build(case)
    payload["results"][0]["criteria"][0]["evidence_turns"] = evidence
    assert code in errors(validate(payload, case))


@pytest.mark.parametrize("value", [None, " ", 4, "\ud800"])
def test_rationale_mandatory_nonblank_utf8(case, value):
    payload = build(case)
    payload["results"][0]["criteria"][0]["rationale"] = value
    assert "missing_rationale" in errors(validate(payload, case))


@pytest.mark.parametrize("score,code", [(True, "invalid_final_score"), ("85.38", "invalid_final_score"),
    (float("nan"), "invalid_final_score"), (float("inf"), "invalid_final_score"),
    (-1, "invalid_final_score"), (101, "invalid_final_score"), (10**500, "invalid_final_score"),
    (85.3846, "invalid_final_score"), (0, "score_mismatch"), (85.39, "score_mismatch")])
def test_score_cannot_be_coerced_zeroed_or_incorrectly_rounded(case, score, code):
    payload = build(case)
    payload["results"][0]["score"]["final_score"] = score
    assert code in errors(validate(payload, case))


def test_flag_must_be_strict_bool_and_equal_criterion(case):
    payload = build(case)
    payload["results"][0]["score"]["critical_flag"] = 0
    assert "invalid_critical_flag" in errors(validate(payload, case))
    payload["results"][0]["score"]["critical_flag"] = True
    assert "critical_flag_mismatch" in errors(validate(payload, case))


def test_duplicate_unknown_or_missing_criteria_rejected(case):
    payload = build(case)
    payload["results"][0]["criteria"][0]["criterion_id"] = "discovery"
    assert {"duplicate_criterion", "criterion_coverage"} <= errors(validate(payload, case))
    payload["results"][0]["criteria"][0]["criterion_id"] = "new-criterion"
    assert {"unknown_criterion", "criterion_coverage"} <= errors(validate(payload, case))
    payload["results"][0]["criteria"].pop()
    assert "criterion_count" in errors(validate(payload, case))


def test_same_count_wrong_id_is_not_a_valid_dataset(case):
    payload = build(case)
    payload["results"][0]["conversation_id"] = case[0].id
    assert {"unexpected_conversation_id", "missing_conversation_id"} <= errors(validate(payload, case))
    payload["results"] *= 2
    assert "duplicate_conversation_id" in errors(validate(payload, case))


@pytest.mark.parametrize("location", ["root", "model", "result", "criterion", "score"])
def test_formal_json_has_no_diagnostic_or_extra_fields(case, location):
    payload = build(case)
    row = payload["results"][0]
    target = {"root": payload, "model": payload["model"], "result": row,
              "criterion": row["criteria"][0], "score": row["score"]}[location]
    target["internal_diagnostic"] = "not allowed"
    assert "object_fields" in errors(validate(payload, case))


@pytest.mark.parametrize("text", ['{"schema_version":"1.0","schema_version":"1.0"}',
    '{"value":NaN}', '{"value":Infinity}', '{"value":1,}', '{/*comment*/}',
    '{"value":', b'\xff'])
def test_invalid_json_and_duplicate_keys_reported(case, text):
    assert "invalid_json" in errors(validate(text, case))


def test_utf16_file_rejected_and_utf8_accepted(case):
    encoded = json.dumps(build(case), ensure_ascii=False)
    assert "invalid_json" in errors(validate(encoded.encode("utf-16"), case))
    assert validate(encoded.encode("utf-8"), case)["valid"]


@pytest.mark.parametrize("value", [None, [], 1, True, "invalid"])
def test_invalid_document_returns_report_not_exception(case, value):
    assert not validate(value, case)["valid"]


def test_untrusted_model_fields_and_invalid_kind_return_reports(case):
    payload = build(case)
    payload["model"]["mode"] = []
    assert "invalid_model" in errors(validate(payload, case))
    assert "invalid_dataset_kind" in errors(validate(payload, case, dataset_kind=[]))


def test_source_provenance_detects_valid_but_stale_or_tampered_export(case):
    payload = build(case)
    payload["results"][0]["criteria"][0]["rationale"] = "Changed explanation"
    assert validate(payload, case)["valid"]
    assert "stored_result_mismatch" in errors(validate(payload, case, evaluations=[case[1]]))
    payload = build(case)
    payload["model"]["name"] = "invented-model"
    assert "model_provenance_mismatch" in errors(validate(payload, case, evaluations=[case[1]]))


def test_team_name_and_actual_model_must_match_explicit_expectation(case):
    payload = build(case)
    assert "team_name_mismatch" in errors(validate(payload, case, expected_team_name="Other team"))
    assert "model_provenance_mismatch" in errors(validate(payload, case, expected_models=[{"name": "wrong", "mode": "local"}]))
    assert validate(payload, case, expected_models=[{"name": "synthetic-model", "mode": "external_api"}])["valid"]


@pytest.mark.parametrize("mutator", [lambda r: r.update(score=0), lambda r: r.update(criteria=[{}]),
    lambda r: r.update(error=" "), lambda r: r.update(error=None), lambda r: r.update(debug="extra")])
def test_error_records_have_exact_official_shape(case, mutator):
    make_failed(case[1])
    payload = build(case)
    mutator(payload["results"][0])
    assert not validate(payload, case)["valid"]


def test_generated_schema_shares_official_codes_and_has_no_internal_fields():
    schema = official_results_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["schema_version", "team_name", "model", "results"]
    success, failure = schema["properties"]["results"]["items"]["oneOf"]
    definitions = success["properties"]["criteria"]["items"]["oneOf"]
    assert [item["properties"]["criterion_id"]["const"] for item in definitions] == list(OFFICIAL_IDS)
    assert definitions[5]["properties"]["value"] == {"type": "number", "enum": [0, 4, 7, 10]}
    assert len(success["properties"]["criteria"]["allOf"]) == 8
    assert failure["properties"]["score"] == {"type": "null"}
    assert "raw_response" not in json.dumps(schema)
