"""Offline structure/comparison tests using fabricated results, never live calls."""
from copy import deepcopy
import json

import pytest

from backend.app.contracts import ChecklistVersion, Conversation, CriterionResult, Evaluation, EvaluationDraft
from backend.app.default_checklist import official_checklist
from backend.app.scoring import calculate_score
from qa.semantic_regression import (
    FIXTURE_DIR, RegressionInputError, compare_runs, load_matrix, summarize_run, validate_matrix,
)


def archive():
    """Clearly synthetic model-output fixtures for testing the comparator itself."""
    fixtures, expectations = load_matrix()
    cases = {case["conversation_id"]: case for case in expectations["cases"]}
    snapshot = ChecklistVersion(**official_checklist().model_dump(), id="synthetic-checklist", version=1)
    output = {"conversations": [], "evaluations": []}
    for index, item in enumerate(fixtures["conversations"]):
        conversation = Conversation(**item, id=f"synthetic-internal-{index}")
        values = {"1": True, "2": True, "3": "N/A", "4": 20, "5": True, "6": 10, "7": True, "8": False}
        values.update(cases[conversation.external_id]["expected_criteria"])
        criteria = [CriterionResult(criterion_id=key, value=value, reasoning="SYNTHETIC comparator test output",
                                    evidence_message_ids=[conversation.messages[0].id]) for key, value in values.items()]
        score = calculate_score(snapshot, criteria)
        draft = EvaluationDraft(criteria=deepcopy(criteria), raw_response="SYNTHETIC comparator fixture; not a model call",
                                provider="synthetic-test", model="synthetic-model", prompt_version="synthetic-v1")
        evaluation = Evaluation(id=f"synthetic-evaluation-{index}", batch_id="synthetic-run", conversation_id=conversation.id,
                                checklist_snapshot=snapshot, status="completed", ai_draft=draft, raw_response=draft.raw_response,
                                final_criteria=criteria, final_score=score, original_score=deepcopy(score))
        output["conversations"].append(conversation.model_dump(mode="json"))
        output["evaluations"].append(evaluation.model_dump(mode="json"))
    return output


def set_value(saved, external_id, criterion_id, value):
    cid = next(item["id"] for item in saved["conversations"] if item["external_id"] == external_id)
    raw = next(item for item in saved["evaluations"] if item["conversation_id"] == cid)
    evaluation = Evaluation.model_validate(raw)
    for item in evaluation.ai_draft.criteria:
        if item.criterion_id == criterion_id:
            item.value = float(value) if type(value) is int else value
    evaluation.final_criteria = deepcopy(evaluation.ai_draft.criteria)
    evaluation.final_score = calculate_score(evaluation.checklist_snapshot, evaluation.final_criteria)
    evaluation.original_score = deepcopy(evaluation.final_score)
    raw.update(evaluation.model_dump(mode="json"))


def test_frozen_matrix_balanced_and_v1_remains_exact_prefix():
    fixtures, expectations = load_matrix()
    sources, cases = validate_matrix(fixtures, expectations)
    assert len(sources) == len(cases) == 9
    assert {(case["language"], case["scenario"]) for case in cases.values()} == {
        (language, scenario) for language in ("RU", "KZ", "EN")
        for scenario in ("clear_from_start", "clarification_needed", "missing_details_ignored")}
    old_inputs = json.loads((FIXTURE_DIR / "na-regression-conversations-v1.json").read_text(encoding="utf-8"))
    old_expectations = json.loads((FIXTURE_DIR / "na-regression-expectations-v1.json").read_text(encoding="utf-8"))
    assert fixtures["conversations"][:6] == old_inputs["conversations"]
    assert expectations["cases"][:6] == old_expectations["cases"]
    assert len(validate_matrix(old_inputs, old_expectations)[0]) == 6


def test_original_known_cases_keep_exact_transcripts_and_private_payment_flag():
    fixtures, expectations = load_matrix()
    root = FIXTURE_DIR.parents[1]
    originals = json.loads((root / "contracts/demo-conversations.json").read_text(encoding="utf-8"))["conversations"]
    ru = json.loads((FIXTURE_DIR / "live-semantic-regressions.json").read_text(encoding="utf-8"))["conversations"][0]
    for item in fixtures["conversations"][:3]:
        reference = next((candidate for candidate in originals if candidate["external_id"] == item["external_id"]), ru)
        assert item["messages"] == reference["messages"]
    en = next(case for case in expectations["cases"] if case["conversation_id"] == "demo-en-call-20261107")
    assert en["expected_criteria"]["8"] is True
    assert en["expected_criteria"]["2"] == "N/A"


@pytest.mark.parametrize("change", [
    lambda f, e: f["conversations"].pop(),
    lambda f, e: f["conversations"][0]["metadata"].update(synthetic=False),
    lambda f, e: e["cases"][0]["expected_criteria"].update({"2": True}),
    lambda f, e: e["cases"][-1]["expected_criteria"].update({"2": "N/A"}),
    lambda f, e: e["cases"][0]["expected_criteria"].update({"1": 1}),
    lambda f, e: e["cases"][0]["expected_criteria"].update({"6": 6}),
    lambda f, e: e["cases"][0].update(language="EN"),
    lambda f, e: e["cases"][0]["primary"].update(rationale=" "),
    lambda f, e: e["cases"][0]["expected_criteria"].update({"invented": True}),
])
def test_invalid_or_cheating_expectation_matrix_is_rejected(change):
    fixtures, expectations = load_matrix()
    change(fixtures, expectations)
    with pytest.raises(RegressionInputError):
        validate_matrix(fixtures, expectations)


@pytest.mark.parametrize("change", [
    lambda a: a["conversations"].pop(),
    lambda a: a["conversations"][0].update(external_id="not-in-frozen-set"),
    lambda a: a["conversations"][0]["messages"][0].update(text="Changed synthetic text"),
    lambda a: a["conversations"][0]["messages"][0].update(role="client"),
    lambda a: a["conversations"][0]["messages"][0].update(timestamp="01:00"),
    lambda a: a["conversations"][0]["metadata"].update(synthetic=False),
    lambda a: a["conversations"][0]["metadata"].update(channel="changed"),
    lambda a: a["evaluations"][0].update(conversation_id="unknown"),
    lambda a: a["evaluations"].__setitem__(0, deepcopy(a["evaluations"][1])),
    lambda a: a["evaluations"][0].update(status="queued"),
    lambda a: a["evaluations"][0].update(status="running"),
    lambda a: a["evaluations"][0].update(ai_draft=None),
    lambda a: a["evaluations"][0]["final_score"].update(total=0),
    lambda a: a["evaluations"][0]["checklist_snapshot"]["criteria"][0].update(description="Changed meaning"),
    lambda a: a["evaluations"][0]["final_criteria"][0].update(value=False),
])
def test_incomparable_saved_runs_never_produce_a_semantic_score(change):
    fixtures, expectations = load_matrix()
    saved = archive()
    change(saved)
    with pytest.raises(RegressionInputError):
        summarize_run(saved, fixtures, expectations)


def test_unchanged_runs_have_no_deltas_or_claimed_organizer_accuracy():
    saved = archive()
    before = deepcopy(saved)
    report = compare_runs(saved, saved)
    assert report["summary"]["case_count"] == 9
    assert report["summary"]["baseline_primary_mismatches"] == []
    assert report["summary"]["candidate_primary_mismatches"] == []
    assert report["summary"]["introduced_regressions"] == []
    assert all(not row["criterion_deltas"] for row in report["cases"])
    assert "not organizer ground truth" in report["interpretation"]
    assert "accuracy" not in report["summary"]
    assert "raw_response" not in json.dumps(report)
    assert "SYNTHETIC comparator test output" not in json.dumps(report)
    assert saved == before


def test_corrected_en_applicability_is_detected_even_when_both_scores_are_100():
    before, after = archive(), archive()
    set_value(before, "demo-en-call-20261107", "2", True)
    report = compare_runs(before, after)
    row = next(row for row in report["cases"] if row["conversation_id"] == "demo-en-call-20261107")
    assert row["baseline"]["score"] == row["candidate"]["score"] == 100
    assert row["resolved_mismatches"] == ["2"]
    assert row["criterion_deltas"] == [{"criterion_id": "2", "baseline": True, "candidate": "N/A", "expected": "N/A", "asserted": True}]
    assert row["baseline"]["critical_flag"] is row["candidate"]["critical_flag"] is True


def test_everything_na_cheat_fails_all_six_applicable_controls():
    before, after = archive(), archive()
    fixtures, expectations = load_matrix()
    for item in after["conversations"]:
        set_value(after, item["external_id"], "2", "N/A")
    report = compare_runs(before, after, fixtures, expectations)
    assert len(report["summary"]["candidate_primary_mismatches"]) == 6
    assert len(report["summary"]["introduced_regressions"]) == 6
    assert {row["language"] for row in report["summary"]["introduced_regressions"]} == {"RU", "KZ", "EN"}


def test_gross_flag_regression_is_reported_even_when_primary_remains_correct():
    before, after = archive(), archive()
    set_value(after, "demo-en-call-20261107", "8", False)
    report = compare_runs(before, after)
    assert report["summary"]["candidate_primary_mismatches"] == []
    assert report["summary"]["introduced_regressions"] == [{"conversation_id": "demo-en-call-20261107", "language": "EN", "criteria": ["8"]}]


def test_unasserted_subjective_changes_are_visible_without_invented_failures():
    before, after = archive(), archive()
    set_value(after, "synthetic-na-ru-missing-ignored-v2", "4", 5)
    report = compare_runs(before, after)
    row = next(row for row in report["cases"] if row["conversation_id"] == "synthetic-na-ru-missing-ignored-v2")
    assert row["criterion_deltas"] == [{"criterion_id": "4", "baseline": 20.0, "candidate": 5.0, "expected": None, "asserted": False}]
    assert row["introduced_mismatches"] == []


def test_failure_is_not_zero_or_a_matching_false_answer():
    before, after = archive(), archive()
    target = after["evaluations"][-1]
    target.update(status="failed", ai_draft=None, final_criteria=None, final_score=None,
                  error={"code": "synthetic_failure", "message": "Synthetic test error", "retryable": False})
    report = compare_runs(before, after)
    row = report["cases"][-1]
    assert row["candidate"]["score"] is None
    assert row["candidate"]["criteria"] is None
    assert not row["candidate"]["primary_matches"]
    assert report["summary"]["candidate_completed"] == 8
    assert report["summary"]["candidate_usage"]["cost_usd"] is None
    assert report["summary"]["candidate_usage"]["total_tokens"] is None
    assert report["summary"]["candidate_usage"]["unknown_execution_metadata"] == 9


def test_model_change_is_visible_as_a_protocol_difference():
    before, after = archive(), archive()
    after["evaluations"][0]["ai_draft"]["model"] = "other-synthetic-model"
    report = compare_runs(before, after)
    assert report["summary"]["completed_model_changes"] == [after["conversations"][0]["external_id"]]
