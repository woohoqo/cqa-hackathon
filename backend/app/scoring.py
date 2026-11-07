"""Validate model decisions and calculate scores from an immutable checklist snapshot.

No criterion IDs or official checklist wording are hardcoded here. Decimal arithmetic
avoids rounding intermediate points; only the overall percentage is rounded.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .contracts import (
    AnswerType,
    ChecklistVersion,
    Conversation,
    CriterionResult,
    CriterionScore,
    ScoreSummary,
    SectionScore,
)


def _validated_results(
    checklist: ChecklistVersion, results: Sequence[CriterionResult]
) -> dict[str, CriterionResult]:
    """Check answers without including potentially sensitive input in errors."""
    expected = {criterion.id: criterion for criterion in checklist.criteria}
    indexed: dict[str, CriterionResult] = {}
    for result in results:
        if result.criterion_id in indexed:
            raise ValueError("Duplicate criterion result")
        if result.criterion_id not in expected:
            raise ValueError("Unknown criterion result")
        indexed[result.criterion_id] = result
        criterion = expected[result.criterion_id]
        value = result.value
        if isinstance(value, str) and value == "N/A":
            if not criterion.allow_na:
                raise ValueError("N/A is not allowed for this criterion")
        elif criterion.answer_type in (AnswerType.BOOLEAN, AnswerType.FLAG):
            if type(value) is not bool:
                raise ValueError("Boolean and flag criteria require a boolean answer")
        elif criterion.answer_type == AnswerType.SCALE:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Scale criteria require a finite numeric answer")
            if Decimal(str(value)) not in {Decimal(str(step.value)) for step in criterion.scale}:
                raise ValueError("Scale answer is not a permitted step")
        else:
            raise ValueError("Unsupported criterion answer type")
    if set(indexed) != set(expected):
        raise ValueError("Evaluation must contain every checklist criterion exactly once")
    return indexed


def validate_results(
    conversation: Conversation,
    checklist: ChecklistVersion,
    results: Sequence[CriterionResult],
) -> None:
    """Validate completeness, answer types/steps and original message references.

    Whether the documented N/A condition is satisfied requires semantic review;
    this function checks whether N/A is permitted by the snapshot at all.
    """
    _validated_results(checklist, results)
    message_ids = {message.id for message in conversation.messages}
    if any(message_id not in message_ids for result in results for message_id in result.evidence_message_ids):
        raise ValueError("Evidence references an unknown message")


def calculate_score(
    checklist: ChecklistVersion, results: Sequence[CriterionResult]
) -> ScoreSummary:
    """Calculate criterion points, unrounded section scores and rounded total.

    This independently validates answers, so callers cannot obtain plausible scores
    from incomplete or invalid results even if they omit validate_results(). Message
    evidence still requires validate_results(), which has access to the conversation.
    """
    indexed = _validated_results(checklist, results)
    with localcontext() as context:
        context.prec = 50
        zero = Decimal(0)
        section_weights = {section.id: zero for section in checklist.sections}
        section_points = {section.id: zero for section in checklist.sections}
        criterion_scores: list[CriterionScore] = []
        gross_error = False

        for criterion in checklist.criteria:
            value = indexed[criterion.id].value
            weight = Decimal(str(criterion.weight))
            applicable = value != "N/A"
            points: Decimal | None = None
            if applicable and criterion.answer_type == AnswerType.FLAG:
                gross_error = gross_error or value is True
            elif applicable:
                if criterion.answer_type == AnswerType.BOOLEAN:
                    points = weight if value is True else zero
                else:
                    maximum = max(Decimal(str(step.value)) for step in criterion.scale)
                    points = Decimal(str(value)) / maximum * weight
                section_weights[criterion.section_id] += weight
                section_points[criterion.section_id] += points
            criterion_scores.append(
                CriterionScore(
                    criterion_id=criterion.id,
                    value=value,
                    applicable=applicable,
                    weight=criterion.weight,
                    earned_points=float(points) if points is not None else None,
                )
            )

        applicable_weight = sum(section_weights.values(), zero)
        earned_points = sum(section_points.values(), zero)
        sections = [
            SectionScore(
                section_id=section.id,
                applicable_weight=float(section_weights[section.id]),
                earned_points=float(section_points[section.id]),
                score=(
                    float(section_points[section.id] / section_weights[section.id] * 100)
                    if section_weights[section.id] > 0
                    else None
                ),
            )
            for section in checklist.sections
        ]
        total = (
            float((earned_points / applicable_weight * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if applicable_weight > 0
            else None
        )
        return ScoreSummary(
            criteria=criterion_scores,
            sections=sections,
            applicable_weight=float(applicable_weight),
            earned_points=float(earned_points),
            total=total,
            gross_error=gross_error,
        )
