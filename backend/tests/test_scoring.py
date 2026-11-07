"""Synthetic calculator fixtures, not a copy of the official checklist or real chats."""
from copy import deepcopy

import pytest

from backend.app.contracts import (
    ChecklistVersion,
    Conversation,
    Criterion,
    CriterionResult,
    Message,
    ScaleStep,
    Section,
)
from backend.app.scoring import calculate_score, validate_results


def criterion(identifier="c1", *, weight=10, answer_type="boolean", scale=(), allow_na=False, section_id="s1"):
    return Criterion(
        id=identifier,
        section_id=section_id,
        title=f"Synthetic criterion {identifier}",
        weight=weight,
        answer_type=answer_type,
        scale=[ScaleStep(value=value, label=str(value)) for value in scale],
        allow_na=allow_na,
        na_condition="Synthetic condition absent" if allow_na else None,
    )


def checklist(*criteria, sections=None):
    return ChecklistVersion(
        id="synthetic-checklist-v1", key="synthetic", name="Synthetic calculator fixture", version=1,
        sections=sections or [Section(id="s1", title="Synthetic section")],
        criteria=list(criteria),
    )


def result(identifier="c1", value=True, evidence=None):
    return CriterionResult(
        criterion_id=identifier, value=value, reasoning="Synthetic test explanation",
        evidence_message_ids=evidence or [],
    )


@pytest.fixture
def conversation():
    return Conversation(
        id="synthetic-conversation",
        messages=[Message(id="m1", role="operator", text="Synthetic text", timestamp="original timestamp")],
    )


def test_required_8538_example(conversation):
    # Official weights/steps, synthetic answers and criterion wording.
    # Independently: 55.5 / 65 * 100 = 85.3846...
    snapshot = checklist(
        criterion("1", weight=10),
        criterion("2", weight=15, allow_na=True),
        criterion("3", weight=20, allow_na=True),
        criterion("4", weight=20, answer_type="scale", scale=(0, 5, 10, 15, 20), allow_na=True),
        criterion("5", weight=15),
        criterion("6", weight=15, answer_type="scale", scale=(0, 4, 7, 10)),
        criterion("7", weight=5, allow_na=True),
        criterion("8", weight=0, answer_type="flag"),
    )
    answers = [result(str(i), value, ["m1"]) for i, value in enumerate([True, "N/A", "N/A", 15, True, 7, True, False], 1)]
    validate_results(conversation, snapshot, answers)
    score = calculate_score(snapshot, answers)
    assert score.total == 85.38
    assert score.applicable_weight == 65
    assert score.earned_points == 55.5
    assert score.criteria[5].earned_points == 10.5
    assert score.criteria[1].earned_points is None
    assert score.criteria[1].applicable is False
    assert score.sections[0].score == pytest.approx(55.5 / 65 * 100)
    assert score.sections[0].score != score.total


@pytest.mark.parametrize("value,expected", [(True, 100.0), (False, 0.0)])
def test_boolean_scoring(value, expected):
    assert calculate_score(checklist(criterion(weight=31)), [result(value=value)]).total == expected


def test_na_excludes_weight_and_renormalizes():
    snapshot = checklist(criterion("a", weight=80, allow_na=True), criterion("b", weight=20))
    score = calculate_score(snapshot, [result("a", "N/A"), result("b", True)])
    assert score.total == 100
    assert score.applicable_weight == 20


@pytest.mark.parametrize("criteria,answers", [
    ([criterion(allow_na=True)], [result(value="N/A")]),
    ([criterion(weight=0)], [result()]),
    ([criterion(weight=0, answer_type="flag")], [result(value=True)]),
])
def test_no_applicable_positive_weight_is_not_zero_score(criteria, answers):
    score = calculate_score(checklist(*criteria), answers)
    assert score.total is None
    assert score.sections[0].score is None
    assert score.applicable_weight == 0


def test_gross_error_is_separate_and_never_zeroes_score():
    snapshot = checklist(criterion("quality", weight=100), criterion("critical", weight=0, answer_type="flag"))
    score = calculate_score(snapshot, [result("quality", True), result("critical", True)])
    assert score.total == 100
    assert score.gross_error is True
    assert score.criteria[1].earned_points is None


def test_false_or_na_flags_do_not_set_gross_error():
    snapshot = checklist(criterion("f1", weight=0, answer_type="flag"), criterion("f2", weight=0, answer_type="flag", allow_na=True))
    assert calculate_score(snapshot, [result("f1", False), result("f2", "N/A")]).gross_error is False


def test_only_total_is_rounded_half_up():
    snapshot = checklist(criterion(weight=1, answer_type="scale", scale=(0, 1, 800)))
    score = calculate_score(snapshot, [result(value=1)])
    assert score.total == 0.13
    assert score.earned_points == 0.00125
    assert score.sections[0].score == 0.125


def test_dynamic_criterion_and_multiple_sections():
    snapshot = checklist(
        criterion("arbitrary-added-id", weight=30, answer_type="scale", scale=(0, 1, 3), section_id="new-section"),
        criterion("another", weight=10),
        sections=[Section(id="s1", title="Original"), Section(id="new-section", title="Added"), Section(id="empty", title="Empty")],
    )
    score = calculate_score(snapshot, [result("another", False), result("arbitrary-added-id", 1)])
    assert score.total == 25
    assert [s.score for s in score.sections] == [0, pytest.approx(100 / 3), None]
    assert [c.criterion_id for c in score.criteria] == ["arbitrary-added-id", "another"]


@pytest.mark.parametrize("answers,match", [
    ([], "every checklist criterion"),
    ([result(), result()], "Duplicate"),
    ([result("unknown")], "Unknown"),
    ([result(value="N/A")], "N/A is not allowed"),
    ([result(value=1)], "require a boolean"),
])
def test_invalid_results_rejected_in_both_entry_points(conversation, answers, match):
    snapshot = checklist(criterion())
    with pytest.raises(ValueError, match=match):
        validate_results(conversation, snapshot, answers)
    with pytest.raises(ValueError, match=match):
        calculate_score(snapshot, answers)


@pytest.mark.parametrize("value", [True, False, 2, -1, 11])
def test_scale_rejects_boolean_and_unlisted_values(conversation, value):
    snapshot = checklist(criterion(answer_type="scale", scale=(0, 1, 10)))
    with pytest.raises(ValueError):
        validate_results(conversation, snapshot, [result(value=value)])


def test_flag_rejects_numeric_answer():
    snapshot = checklist(criterion(weight=0, answer_type="flag"))
    with pytest.raises(ValueError, match="boolean"):
        calculate_score(snapshot, [result(value=1)])


def test_unknown_evidence_is_rejected_without_echoing_input(conversation):
    with pytest.raises(ValueError, match="unknown message") as exc:
        validate_results(conversation, checklist(criterion()), [result(evidence=["private-do-not-echo"])])
    assert "private-do-not-echo" not in str(exc.value)


def test_inputs_are_unchanged(conversation):
    snapshot = checklist(criterion(answer_type="scale", scale=(0, 1, 3)))
    answers = [result(value=1, evidence=["m1"])]
    before = deepcopy((conversation, snapshot, answers))
    validate_results(conversation, snapshot, answers)
    calculate_score(snapshot, answers)
    assert (conversation, snapshot, answers) == before
