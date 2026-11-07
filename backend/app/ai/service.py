"""One Gemini evaluation with bounded repairs, strict evidence checks and usage audit."""
import asyncio
import random
from uuid import uuid4

from ..contracts import AIProviderError, ChecklistVersion, Conversation, EvaluationDraft
from . import provider
from .audit import check_audit_sink, record_attempt
from .config import AISettings
from .pacing import request_gate
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_input, build_schema
from .validation import InvalidResponse, validate_response


async def evaluate_dialogue(conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft:
    """Return a contract-v1 draft, or safe AIProviderError; never calculate scores.

    The immutable checklist snapshot and ALL transcript messages are sent on each attempt.
    Retries repair format/evidence or transient transport failures, not a second-model vote.
    See README.md in this package for configuration, audit fields and quality limitations.
    """
    settings = AISettings.from_env()
    conversation = Conversation.model_validate(conversation.model_dump(mode="python"))
    checklist = ChecklistVersion.model_validate(checklist.model_dump(mode="python"))
    system_instruction = SYSTEM_PROMPT
    user_content = build_input(conversation, checklist)
    schema = build_schema(checklist)
    if len(user_content.encode("utf-8")) > 8_000_000:
        raise AIProviderError("ai_input_too_large", "Транскрипт и чек-лист превышают лимит запроса AI.")
    check_audit_sink(settings.audit_path)
    run_id = str(uuid4())
    last_raw = None
    last_error = None
    feedback = ""

    def audit(attempt, status, reply=None, error_code=None):
        try:
            record_attempt(settings, run_id=run_id, conversation_id=conversation.id,
                           checklist_id=checklist.id, checklist_version=checklist.version,
                           prompt_version=PROMPT_VERSION, attempt=attempt, status=status,
                           reply=reply, error_code=error_code)
        except AIProviderError as exc:
            raise AIProviderError(exc.code, exc.message, retryable=exc.retryable,
                                  raw_response=last_raw) from None

    try:
        async with asyncio.timeout(settings.evaluation_timeout_seconds):
            for attempt in range(1, settings.max_attempts + 1):
                reply = None
                retry_after = None
                try:
                    await request_gate.acquire(
                        settings.model, settings.min_request_interval_seconds,
                        max_cooldown_wait=max(0.0, settings.evaluation_timeout_seconds - settings.timeout_seconds),
                    )
                except AIProviderError as exc:
                    # No HTTP request occurred: do not record invented usage/attempts.
                    raise AIProviderError(exc.code, exc.message, retryable=exc.retryable,
                                          raw_response=last_raw) from None
                try:
                    reply = await provider.generate_once(settings, system_instruction + feedback,
                                                         user_content, schema)
                except asyncio.CancelledError:
                    # A timed-out/cancelled request may have been billed. Unknown is not zero.
                    try:
                        audit(attempt, "cancelled", error_code="ai_cancelled")
                    except AIProviderError:
                        pass  # Never swallow task cancellation if the disk also fails.
                    raise
                except AIProviderError as exc:
                    audit(attempt, "provider_error", reply=getattr(exc, "reply", None), error_code=exc.code)
                    if exc.code in {"provider_rate_limit", "provider_daily_quota_exhausted"}:
                        retry_after = max(settings.rate_limit_cooldown_seconds, getattr(exc, "retry_after", None) or 0.0)
                        request_gate.defer(settings.model, retry_after, exc.code)
                    if not exc.retryable:
                        raise AIProviderError(exc.code, exc.message, retryable=False,
                                              raw_response=exc.raw_response if exc.raw_response is not None else last_raw) from None
                    last_error = exc
                    if retry_after is None:
                        retry_after = getattr(exc, "retry_after", None)
                else:
                    if reply.raw_text is not None:
                        last_raw = reply.raw_text
                    if reply.finish_reason == "MAX_TOKENS":
                        audit(attempt, "output_limit", reply, "ai_output_limit")
                        # Repeating the same capped request wastes a scarce free-tier call.
                        raise AIProviderError("ai_output_limit", "Gemini output limit reached. Increase GEMINI_MAX_OUTPUT_TOKENS.",
                                              raw_response=last_raw)
                    if reply.blocked:
                        audit(attempt, "blocked", reply, "ai_blocked")
                        raise AIProviderError("ai_blocked", "Провайдер не смог оценить этот диалог.",
                                              raw_response=last_raw)
                    try:
                        if reply.finish_reason != "STOP" or not reply.raw_text:
                            raise InvalidResponse("incomplete_response")
                        criteria = validate_response(reply.raw_text, conversation, checklist)
                    except InvalidResponse as exc:
                        audit(attempt, "invalid_response", reply, str(exc))
                        last_error = AIProviderError("invalid_ai_response", "AI вернул невалидную оценку.",
                                                     retryable=True, raw_response=last_raw)
                        # Feedback contains only OUR fixed validation code, never raw model text.
                        feedback = ("\nPrevious response failed validation (" + str(exc) + "). "
                                    "Re-evaluate the complete input and return a complete schema-valid result. "
                                    "Do not change or omit any criterion or invent message IDs.")
                    else:
                        audit(attempt, "completed", reply)
                        return EvaluationDraft(criteria=criteria, raw_response=reply.raw_text,
                                               provider=settings.provider,
                                               model=reply.model_version or settings.model,
                                               prompt_version=PROMPT_VERSION)

                if attempt == settings.max_attempts:
                    break
                delay = min(settings.retry_max_seconds, settings.retry_base_seconds * (2 ** (attempt - 1)))
                delay *= random.uniform(0.8, 1.0)
                if retry_after is not None:
                    # A long quota cooldown should return control, not be shortened and hammered.
                    if retry_after > settings.retry_max_seconds:
                        break
                    delay = max(delay, retry_after)
                await asyncio.sleep(delay)
    except TimeoutError:
        raise AIProviderError("ai_timeout", "Превышено время оценки AI.", retryable=True,
                              raw_response=last_raw) from None
    if last_error is not None:
        raise AIProviderError(last_error.code, last_error.message, retryable=last_error.retryable,
                              raw_response=last_raw) from None
    raise AIProviderError("provider_error", "AI не вернул оценку.")
