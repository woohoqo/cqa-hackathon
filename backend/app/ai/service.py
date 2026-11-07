"""Owned by AI agent after foundation handoff. Never return synthetic evaluations."""
from ..contracts import AIProviderError, ChecklistVersion, Conversation, EvaluationDraft


async def evaluate_dialogue(conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft:
    raise AIProviderError(
        "provider_not_configured",
        "Провайдер не настроен: установите AI-модуль и заполните GEMINI_API_KEY и GEMINI_MODEL в .env.",
    )
