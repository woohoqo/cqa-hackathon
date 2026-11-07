"""Shared version 1 contracts. Only the backend/integration owner changes this file."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Identifier = Annotated[str, Field(min_length=1, max_length=200)]
AnswerValue = StrictBool | StrictFloat | Literal["N/A"]


class MessageInput(Contract):
    id: Identifier | None = None
    role: Annotated[str, Field(min_length=1)]
    text: str
    timestamp: str | None = None  # Original representation, never parsed/reformatted.


class Message(MessageInput):
    id: Identifier


class ConversationInput(Contract):
    external_id: Identifier | None = None
    source: Identifier = "manual"
    messages: list[MessageInput] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Conversation(ConversationInput):
    id: Identifier
    messages: list[Message] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def unique_messages(self):
        ids = [m.id for m in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("Message IDs must be unique within the conversation")
        return self


class AnswerType(str, Enum):
    BOOLEAN = "boolean"
    SCALE = "scale"
    FLAG = "flag"


class ScaleStep(Contract):
    value: float = Field(ge=0)
    label: str
    description: str = ""


class Section(Contract):
    id: Identifier
    title: str = Field(min_length=1)


class Criterion(Contract):
    id: Identifier
    section_id: Identifier = "general"
    title: str = Field(min_length=1)
    description: str = ""
    weight: float = Field(ge=0)
    answer_type: AnswerType
    scale: list[ScaleStep] = Field(default_factory=list)
    allow_na: bool = False
    na_condition: str | None = None

    @model_validator(mode="after")
    def valid_definition(self):
        values = [s.value for s in self.scale]
        if self.answer_type == AnswerType.SCALE:
            if not values or max(values) <= 0 or len(values) != len(set(values)):
                raise ValueError("Scale requires unique nonnegative steps and a positive maximum")
        elif values:
            raise ValueError("Only scale criteria may define scale steps")
        if self.answer_type == AnswerType.FLAG and self.weight != 0:
            raise ValueError("A gross-error flag must have zero weight")
        if self.allow_na and not (self.na_condition and self.na_condition.strip()):
            raise ValueError("N/A requires a documented condition")
        return self


class ChecklistCreate(Contract):
    key: Identifier
    name: str = Field(min_length=1)
    description: str = ""
    sections: list[Section] = Field(default_factory=lambda: [Section(id="general", title="Общие")])
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_references(self):
        if not self.sections:
            self.sections = [Section(id="general", title="Общие")]
        sections = [s.id for s in self.sections]
        criteria = [c.id for c in self.criteria]
        if len(set(sections)) != len(sections) or len(set(criteria)) != len(criteria):
            raise ValueError("Section and criterion IDs must be unique")
        if any(c.section_id not in sections for c in self.criteria):
            raise ValueError("Unknown criterion section")
        return self


class ChecklistVersion(ChecklistCreate):
    id: Identifier
    version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utcnow)


class CriterionResult(Contract):
    criterion_id: Identifier
    value: AnswerValue
    reasoning: str = Field(min_length=1, pattern=r"\S")
    evidence_message_ids: list[Identifier] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class EvaluationDraft(Contract):
    criteria: list[CriterionResult]
    raw_response: str = Field(min_length=1, description="Exact original model response, without reserialization")
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)


class CriterionScore(Contract):
    criterion_id: Identifier
    value: AnswerValue
    applicable: bool
    weight: float
    earned_points: float | None


class SectionScore(Contract):
    section_id: Identifier
    applicable_weight: float
    earned_points: float
    score: float | None  # Unrounded section percentage.


class ScoreSummary(Contract):
    criteria: list[CriterionScore]
    sections: list[SectionScore]
    applicable_weight: float
    earned_points: float
    total: float | None  # Only this value is rounded, to two decimal places.
    gross_error: bool


class EvaluationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorDetail(Contract):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(Contract):
    error: ErrorDetail


class Evaluation(Contract):
    id: Identifier
    batch_id: Identifier
    conversation_id: Identifier
    checklist_snapshot: ChecklistVersion
    status: EvaluationStatus
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    ai_draft: EvaluationDraft | None = None
    raw_response: str | None = None
    original_score: ScoreSummary | None = None
    final_score: ScoreSummary | None = None
    final_criteria: list[CriterionResult] | None = None
    error: ErrorDetail | None = None


class EvaluationRequest(Contract):
    conversation_ids: list[Identifier] = Field(min_length=1, max_length=100)
    checklist_id: Identifier

    @model_validator(mode="after")
    def unique_ids(self):
        if len(set(self.conversation_ids)) != len(self.conversation_ids):
            raise ValueError("Conversation IDs must be unique")
        return self


class EvaluationBatch(Contract):
    id: Identifier
    evaluations: list[Evaluation]


class AppealCreate(Contract):
    criterion_id: Identifier
    reason: str = Field(min_length=1, pattern=r"\S")
    author: str = Field(min_length=1)
    proposed_value: AnswerValue | None = None


class Appeal(AppealCreate):
    id: Identifier
    evaluation_id: Identifier
    status: Literal["open", "accepted", "rejected"] = "open"
    created_at: datetime = Field(default_factory=utcnow)


class ReviewDecisionCreate(Contract):
    outcome: Literal["accepted", "rejected"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1, pattern=r"\S")
    replacement: CriterionResult | None = None

    @model_validator(mode="after")
    def require_replacement(self):
        if (self.outcome == "accepted") != (self.replacement is not None):
            raise ValueError("Accepted decisions require a replacement; rejected decisions forbid it")
        return self


class ReviewDecision(ReviewDecisionCreate):
    id: Identifier
    appeal_id: Identifier
    created_at: datetime = Field(default_factory=utcnow)


class ImportRequest(Contract):
    conversations: list[ConversationInput] = Field(min_length=1, max_length=1000)


class DevImportRequest(Contract):
    group_id: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")] = "DEV"


class ImportResult(Contract):
    conversations: list[Conversation]
    imported: int
    existing: int


class AIProviderError(Exception):
    """Provider errors must use a safe message (no keys, URLs or transcript content)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, raw_response: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.raw_response = raw_response
