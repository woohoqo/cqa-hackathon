"""Generate typed, deterministic, explicitly synthetic request/response examples."""
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.contracts import (ChecklistVersion, Conversation, ConversationInput, CriterionResult,
                                   Evaluation, EvaluationDraft, Message)
from backend.app.default_checklist import official_checklist
from backend.app.scoring import calculate_score


def generate() -> str:
    date = datetime(2026, 11, 7, tzinfo=timezone.utc)
    conversation = Conversation(id="synthetic-conversation", source="synthetic-example", external_id="synthetic-001",
        created_at=date, messages=[Message(id="m1", role="client", text="Синтетический вопрос для примера API.", timestamp="00:00"),
                                  Message(id="m2", role="operator", text="Синтетический ответ для примера API.", timestamp="00:02")])
    checklist = ChecklistVersion(**official_checklist().model_dump(), id="synthetic-checklist-v1", version=1, created_at=date)
    criteria = [CriterionResult(criterion_id=str(i), value=value, reasoning="Синтетический пример формата, не оценка модели.",
                               evidence_message_ids=["m2"]) for i, value in enumerate([True, "N/A", "N/A", 15, True, 7, True, False], start=1)]
    raw = json.dumps({"synthetic_example_only": True, "criteria": [c.model_dump() for c in criteria]}, ensure_ascii=False)
    draft = EvaluationDraft(criteria=criteria, raw_response=raw, provider="synthetic-example-only", model="no-model-called", prompt_version="docs-v1")
    score = calculate_score(checklist, criteria)
    evaluation = Evaluation(id="synthetic-evaluation", batch_id="synthetic-batch", conversation_id=conversation.id,
        checklist_snapshot=checklist, status="completed", created_at=date, updated_at=date, ai_draft=draft, raw_response=raw,
        original_score=score, final_score=score, final_criteria=criteria)
    result = {"notice": "Only synthetic documentation examples; no model call or real records.",
              "conversation_request": conversation.model_dump(mode="json", exclude={"id", "created_at"}),
              "conversation_response": conversation.model_dump(mode="json"),
              "checklist_request": official_checklist().model_dump(mode="json"),
              "evaluation_request": {"conversation_ids": [conversation.id], "checklist_id": checklist.id},
              "evaluation_response": evaluation.model_dump(mode="json")}
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    (Path(__file__).resolve().parents[1] / "contracts" / "examples.json").write_text(generate(), encoding="utf-8")
