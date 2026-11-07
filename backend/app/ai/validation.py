"""Strict structural checks; semantic correctness still needs human-labelled evaluation."""
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts import AnswerType, ChecklistVersion, Conversation, CriterionResult


class InvalidResponse(ValueError):
    """Safe machine-readable reason only; never include provider/transcript text."""


Text = Annotated[str, Field(min_length=1, max_length=1500, pattern=r"\S")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ExpectedAction(_Strict):
    message_id: str
    position: Literal["before", "in", "after"]
    description: Text


class _Decision(_Strict):
    value: bool | float | Literal["N/A"]
    reasoning: Text
    evidence_message_ids: list[str] = Field(min_length=1)
    evidence_kind: Literal["observed", "missing_action", "not_applicable"]
    checked_message_ids: list[str]
    expected_action: ExpectedAction | None
    na_condition_met: Text | None


class _Response(_Strict):
    criteria: dict[str, _Decision]


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InvalidResponse("duplicate_json_key")
        obj[key] = value
    return obj


def _no_constant(value):
    raise InvalidResponse("nonfinite_number")


def validate_response(raw: str, conversation: Conversation, checklist: ChecklistVersion) -> list[CriterionResult]:
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_no_constant)
        result = _Response.model_validate(parsed)
    except InvalidResponse:
        raise
    except (ValueError, RecursionError, TypeError):
        raise InvalidResponse("invalid_structure") from None
    if set(result.criteria) != {c.id for c in checklist.criteria}:
        raise InvalidResponse("criterion_coverage")

    order = {m.id: i for i, m in enumerate(conversation.messages)}
    projected = []
    for criterion in checklist.criteria:
        decision = result.criteria[criterion.id]
        value = decision.value
        if value == "N/A":
            if not criterion.allow_na:
                raise InvalidResponse("na_not_allowed")
            if decision.evidence_kind != "not_applicable" or not decision.na_condition_met:
                raise InvalidResponse("missing_na_explanation")
        else:
            if criterion.answer_type == AnswerType.SCALE:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise InvalidResponse("invalid_scale_type")
                if value not in [step.value for step in criterion.scale]:
                    raise InvalidResponse("invalid_scale_step")
            elif type(value) is not bool:
                raise InvalidResponse("invalid_boolean_type")
            if decision.na_condition_met is not None or decision.evidence_kind == "not_applicable":
                raise InvalidResponse("unexpected_na_explanation")

        for refs in (decision.evidence_message_ids, decision.checked_message_ids):
            if len(refs) != len(set(refs)) or any(ref not in order for ref in refs):
                raise InvalidResponse("invalid_evidence_reference")
        checked = decision.checked_message_ids
        if checked:
            positions = [order[ref] for ref in checked]
            if positions != list(range(positions[0], positions[-1] + 1)):
                raise InvalidResponse("noncontiguous_checked_region")

        evidence = list(decision.evidence_message_ids)
        reasoning = decision.reasoning
        action = decision.expected_action
        if decision.evidence_kind == "missing_action":
            if not checked or action is None or action.message_id not in checked:
                raise InvalidResponse("missing_action_location")
            positions_ru = {"before": "перед сообщением", "in": "в сообщении", "after": "после сообщения"}
            reasoning += (f" Проверенный участок: {checked[0]} — {checked[-1]}. "
                          f"Ожидалось {positions_ru[action.position]} {action.message_id}: "
                          f"{action.description}")
            evidence.extend([checked[0], checked[-1], action.message_id])
        elif action is not None:
            raise InvalidResponse("unexpected_action_location")
        if value == "N/A":
            reasoning += f" Условие N/A: {decision.na_condition_met}"
        projected.append(CriterionResult(
            criterion_id=criterion.id, value=value, reasoning=reasoning,
            evidence_message_ids=list(dict.fromkeys(evidence)),
        ))
    return projected
