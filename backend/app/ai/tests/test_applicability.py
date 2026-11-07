"""Synthetic wire-contract regressions, not measured model accuracy or human labels."""
import asyncio
import copy
from dataclasses import replace
import json

import pytest

from backend.app.ai import service
from backend.app.ai.prompt import build_schema
from backend.app.ai.tests.test_service import records, reply, settings
from backend.app.ai.tests.test_validation import checklist, conversation, decision, response
from backend.app.ai.validation import InvalidResponse, validate_response
from backend.app.contracts import AIProviderError, ChecklistVersion


NEW_ID = "synthetic-new-applicability/Ж"
NEW_CONDITION = "The synthetic prerequisite was already satisfied before this exchange."


def dynamic_case(kind, value, *, allow_na=True):
    rubric = checklist().model_dump(mode="python")
    rubric["criteria"] = [{
        "id": NEW_ID, "title": "Synthetic replacement rule", "description": "Synthetic current performance rule",
        "weight": 0 if kind == "flag" else 12, "answer_type": kind,
        "allow_na": allow_na, "na_condition": NEW_CONDITION if allow_na else None,
        "scale": [{"value": 0, "label": "none"}, {"value": 3, "label": "partial"},
                  {"value": 9, "label": "complete"}] if kind == "scale" else [],
    }]
    result = decision(value, allow_na=allow_na)
    if value == "N/A":
        result.update(evidence_kind="not_applicable", na_condition_met="Synthetic prerequisite was already satisfied.")
    return ChecklistVersion.model_validate(rubric), {"criteria": {NEW_ID: result}}


def validate(rubric, data):
    return validate_response(json.dumps(data, ensure_ascii=False), conversation(), rubric)


def test_applicability_is_required_first_and_rebuilt_from_replaced_condition():
    rubric, _ = dynamic_case("scale", 3)
    generated = build_schema(rubric)["properties"]["criteria"]["properties"][NEW_ID]
    assert list(generated["properties"])[0] == generated["required"][0] == "applicability"
    assert generated["properties"]["applicability"]["additionalProperties"] is False
    assert NEW_CONDITION in generated["properties"]["applicability"]["description"]
    assert "No applicable request" not in json.dumps(generated)
    assert generated["properties"]["value"]["anyOf"][0]["enum"] == [0, 3, 9]
    unchanged = build_schema(checklist())["properties"]["criteria"]["properties"]
    for identifier in ("scale", "flag"):
        assert unchanged[identifier]["properties"]["applicability"] == {"type": "null"}
        assert "applicability" in unchanged[identifier]["required"]


@pytest.mark.parametrize("kind", ["boolean", "scale", "flag"])
@pytest.mark.parametrize("allow_na", [False, True])
def test_every_answer_schema_places_factual_evidence_before_final_value(kind, allow_na):
    rubric, _ = dynamic_case(kind, 3 if kind == "scale" else True, allow_na=allow_na)
    result = build_schema(rubric)["properties"]["criteria"]["properties"][NEW_ID]
    fields = list(result["properties"])
    assert fields == result["required"]
    assert fields[0] == "applicability" and fields[-1] == "value"
    assert fields.index("reasoning") < fields.index("evidence_message_ids") < fields.index("value")
    assert fields.index("checked_message_ids") < fields.index("value")


def test_scale_schema_preserves_replaced_anchor_meanings_and_nonmatching_weight():
    rubric, _ = dynamic_case("scale", 3)
    old_schema = build_schema(rubric)
    data = rubric.model_dump(mode="python")
    data["criteria"][0].update(weight=37, scale=[
        {"value": 0, "label": "Synthetic zero", "description": "Synthetic decisive prerequisite absent."},
        {"value": 2, "label": "Синтетическая середина", "description": "Synthetic first prerequisite present; second absent."},
        {"value": 8, "label": "Synthetic complete", "description": 'Both synthetic prerequisites, including literal "quotes", present.'},
    ])
    replaced = ChecklistVersion.model_validate(data)
    value = build_schema(replaced)["properties"]["criteria"]["properties"][NEW_ID]["properties"]["value"]["anyOf"][0]
    assert value["enum"] == [0, 2, 8] and 37 not in value["enum"]
    anchors = json.dumps([step.model_dump(mode="json") for step in replaced.criteria[0].scale],
                         ensure_ascii=False, separators=(",", ":"))
    assert anchors in value["description"]
    assert "Synthetic decisive prerequisite absent." not in json.dumps(old_schema)
    assert '"label":"partial"' not in value["description"]


@pytest.mark.parametrize("kind,value", [
    ("boolean", True), ("boolean", False), ("boolean", "N/A"),
    ("scale", 0), ("scale", 3), ("scale", 9), ("scale", "N/A"),
    ("flag", True), ("flag", False), ("flag", "N/A"),
])
def test_new_criterion_keeps_na_and_applicable_positive_negative_values(kind, value):
    rubric, data = dynamic_case(kind, value)
    original = copy.deepcopy(data)
    result = validate(rubric, data)[0]
    assert result.criterion_id == NEW_ID and result.value == value
    assert data == original
    assert "Применимость:" in result.reasoning


@pytest.mark.parametrize("value", [True, False, 3, "N/A"])
def test_condition_decision_and_grade_must_agree_in_both_directions(value):
    rubric, data = dynamic_case("scale" if type(value) is int else "boolean", value)
    data["criteria"][NEW_ID]["applicability"]["na_condition_applies"] = value != "N/A"
    with pytest.raises(InvalidResponse, match="^applicability_value_mismatch$"):
        validate(rubric, data)


@pytest.mark.parametrize("allow_na", [False, True])
def test_applicability_field_is_required_even_when_null(allow_na):
    rubric, data = dynamic_case("boolean", True, allow_na=allow_na)
    del data["criteria"][NEW_ID]["applicability"]
    with pytest.raises(InvalidResponse, match="^invalid_structure$"):
        validate(rubric, data)


@pytest.mark.parametrize("allow_na,code", [(True, "missing_applicability"), (False, "unexpected_applicability")])
def test_optional_object_cannot_replace_required_null_or_vice_versa(allow_na, code):
    rubric, data = dynamic_case("boolean", True, allow_na=allow_na)
    data["criteria"][NEW_ID]["applicability"] = None if allow_na else decision(allow_na=True)["applicability"]
    with pytest.raises(InvalidResponse, match=f"^{code}$"):
        validate(rubric, data)


@pytest.mark.parametrize("bad", ["true", "false", 1, 0, None])
def test_condition_boolean_never_coerces(bad):
    rubric, data = dynamic_case("boolean", True)
    data["criteria"][NEW_ID]["applicability"]["na_condition_applies"] = bad
    with pytest.raises(InvalidResponse, match="^invalid_structure$"):
        validate(rubric, data)


@pytest.mark.parametrize("refs", [[], ["different-conversation-id"], ["operator", "operator"]])
def test_applicability_references_must_be_nonempty_unique_original_ids(refs):
    rubric, data = dynamic_case("boolean", True)
    data["criteria"][NEW_ID]["applicability"]["evidence_message_ids"] = refs
    with pytest.raises(InvalidResponse):
        validate(rubric, data)


def test_applicability_evidence_is_projected_with_stable_deduplication():
    rubric, data = dynamic_case("boolean", True)
    data["criteria"][NEW_ID]["applicability"]["evidence_message_ids"] = ["client", "system", "operator"]
    assert validate(rubric, data)[0].evidence_message_ids == ["operator", "client", "system"]


@pytest.mark.parametrize("change", ["blank", "missing", "extra"])
def test_applicability_explanation_is_required_and_closed(change):
    rubric, data = dynamic_case("boolean", False)
    applicability = data["criteria"][NEW_ID]["applicability"]
    if change == "blank":
        applicability["reasoning"] = " \n "
    elif change == "missing":
        del applicability["reasoning"]
    else:
        applicability["invented_evidence_quote"] = "Synthetic untrusted quote"
    with pytest.raises(InvalidResponse, match="^invalid_structure$"):
        validate(rubric, data)


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_mismatch_repairs_same_connection_with_bounded_safe_feedback_and_exact_raw(
    monkeypatch, settings, repair_succeeds,
):
    cfg = replace(settings, max_attempts=2, backup_api_key_1="synthetic-unused-backup")
    monkeypatch.setattr(service.AISettings, "from_env", lambda: cfg)
    rubric, data = dynamic_case("boolean", True)
    item = data["criteria"][NEW_ID]
    item["applicability"].update(na_condition_applies=True, reasoning="SYNTHETIC_PRIVATE_MODEL_MARKER")
    invalid = " \n" + json.dumps(data, ensure_ascii=False, indent=3) + "\r\n "
    item.update(value="N/A", evidence_kind="not_applicable", na_condition_met="Synthetic prerequisite already satisfied.")
    repaired = " \n" + json.dumps(data, ensure_ascii=False, indent=4) + "\r\n "
    calls = []

    async def fake(current_settings, instruction, content, schema):
        calls.append((current_settings.api_key, instruction, content, schema))
        return reply(repaired if repair_succeeds and len(calls) == 2 else invalid)

    monkeypatch.setattr(service.provider, "generate_once", fake)
    if repair_succeeds:
        draft = asyncio.run(service.evaluate_dialogue(conversation(), rubric))
        assert draft.criteria[0].value == "N/A" and draft.raw_response == repaired
        assert draft.prompt_version == "cqa-single-pass-v1.1.1"
        assert draft.execution.total_tokens == 280
    else:
        with pytest.raises(AIProviderError) as error:
            asyncio.run(service.evaluate_dialogue(conversation(), rubric))
        assert error.value.code == "invalid_ai_response" and error.value.raw_response == invalid
        assert error.value.execution.total_tokens == 280
    assert len(calls) == 2
    assert [call[0] for call in calls] == [settings.api_key, settings.api_key]
    assert calls[0][2:] == calls[1][2:]
    feedback = calls[1][1][len(calls[0][1]):]
    assert "applicability_value_mismatch" in feedback and "Re-check applicability first" in feedback
    assert "SYNTHETIC_PRIVATE_MODEL_MARKER" not in feedback and invalid not in feedback
    assert [row["status"] for row in records(cfg)] == [
        "invalid_response", "completed" if repair_succeeds else "invalid_response",
    ]
