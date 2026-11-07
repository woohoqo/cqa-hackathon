"""Bounded Gemini attempts with queue-wide credential/quota isolation and audit."""
import asyncio
import hashlib
import json
import math
import random
import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from ..contracts import (AIConnectionAttempt, AIExecutionMetadata, AIProviderError,
                         ChecklistVersion, Conversation, EvaluationDraft)
from . import provider
from .audit import check_audit_sink, estimated_cost, record_attempt, usage_counts
from .config import AISettings
from .pacing import request_gate
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_input, build_schema
from .validation import InvalidResponse, validate_response

_QUOTA_ERRORS = {"provider_daily_quota_exhausted", "provider_quota_exhausted"}
_TRANSIENT_ERRORS = {"provider_timeout", "provider_connection_failed", "provider_unavailable", "ai_timeout"}


async def _pause(seconds: float) -> None:
    # Some event loops wake a timer one clock-resolution early. Never shorten a
    # required backoff merely because asyncio.sleep returned early.
    until = time.monotonic() + seconds
    while (remaining := until - time.monotonic()) > 0:
        await asyncio.sleep(remaining)


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _feedback(code: str) -> str:
    # Only our fixed validation code becomes an instruction; never failed model text.
    feedback = ("\nPrevious response failed validation (" + code + "). "
                "Re-evaluate the complete input and return a complete schema-valid result. "
                "Do not change or omit any criterion or invent message IDs.")
    if code == "noncontiguous_checked_region":
        feedback += (" checked_message_ids must contain EVERY message between its boundaries, "
                     "including client, bot and system messages, in original order. "
                     "Do not copy sparse evidence references into this field. "
                     "For observed/N/A decisions use [] if no checked region is needed.")
    elif code == "unexpected_na_explanation":
        feedback += (" Re-check applicability first: when an allowed N/A condition is met, "
                     "set value to the literal string N/A and evidence_kind to not_applicable. "
                     "Never use true to represent that no action was needed. "
                     "For every boolean/numeric answer na_condition_met must be null.")
    return feedback


async def evaluate_dialogue(conversation: Conversation, checklist: ChecklistVersion) -> EvaluationDraft:
    """Evaluate once logically; every actual HTTP attempt shares one global budget.

    Backups change credentials only. Model, prompts, schema and generation settings
    stay identical. Safety refusals, invalid requests and output limits never cause
    a key switch. Format repair stays on the same connection.
    """
    started = time.monotonic()
    run_id = str(uuid4())
    attempts: list[AIConnectionAttempt] = []
    selected_connection = None
    last_raw = None
    last_error = None

    def execution() -> AIExecutionMetadata:
        tokens = [item.total_tokens for item in attempts]
        costs = [item.cost_usd for item in attempts]
        cost = sum(costs) if costs and all(value is not None for value in costs) else None
        if cost is not None and not math.isfinite(cost):
            cost = None
        return AIExecutionMetadata(
            run_id=run_id, selected_connection=selected_connection,
            failover_used=any(item.connection_id != "primary" for item in attempts),
            models_used=list(dict.fromkeys(item.model for item in attempts if item.status == "completed")),
            attempts=attempts.copy(), duration_ms=max(0.0, (time.monotonic() - started) * 1000),
            total_tokens=sum(tokens) if tokens and all(value is not None for value in tokens) else None,
            cost_usd=cost,
        )

    try:
        settings = AISettings.from_env()
        conversation = Conversation.model_validate(conversation.model_dump(mode="python"))
        checklist = ChecklistVersion.model_validate(checklist.model_dump(mode="python"))
        user_content = build_input(conversation, checklist)
        schema = build_schema(checklist)
        if len(user_content.encode("utf-8")) > 8_000_000:
            raise AIProviderError("ai_input_too_large", "Транскрипт и чек-лист превышают лимит запроса AI.")
        check_audit_sink(settings.audit_path)
        connections = settings.connections()
        input_hash = _fingerprint([message.model_dump(mode="json") for message in conversation.messages])
        checklist_hash = _fingerprint(checklist.model_dump(mode="json"))
        request_hash = hashlib.sha256(user_content.encode()).hexdigest()
        feedback = ""
        connection_index = 0

        remaining_budget = settings.evaluation_timeout_seconds - (time.monotonic() - started)
        if remaining_budget <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining_budget):
            while len(attempts) < settings.max_attempts and connection_index < len(connections):
                connection = connections[connection_index]
                remaining = settings.evaluation_timeout_seconds - (time.monotonic() - started)
                cooldown_wait = max(0.0, remaining - settings.timeout_seconds)
                if len(connections) > 1:
                    cooldown_wait = min(settings.retry_max_seconds, cooldown_wait)
                try:
                    lease = await request_gate.acquire_connection(
                        connection, settings.model, settings.min_request_interval_seconds,
                        max_cooldown_wait=cooldown_wait,
                    )
                except AIProviderError as exc:
                    # Local circuit/quota rejection is not a provider attempt.
                    last_error = exc
                    connection_index += 1
                    continue

                selected_connection = connection.id
                attempt_number = len(attempts) + 1
                attempt_started_at = datetime.now(timezone.utc)
                attempt_started = time.monotonic()
                instruction = SYSTEM_PROMPT + feedback
                selected_settings = replace(settings, api_key=connection.api_key)
                delay = min(settings.retry_max_seconds, settings.retry_base_seconds * (2 ** (attempt_number - 1)))
                delay *= random.uniform(0.8, 1.0)
                advance = False
                advance_delay = 0.0

                def audit(status, reply=None, error_code=None):
                    finished_at = datetime.now(timezone.utc)
                    duration_ms = max(0.0, (time.monotonic() - attempt_started) * 1000)
                    usage = usage_counts(reply.usage if reply else None)
                    attempts.append(AIConnectionAttempt(
                        attempt=attempt_number, connection_id=connection.id, quota_group=connection.quota_group,
                        provider=settings.provider, model=(reply.model_version if reply else None) or settings.model,
                        started_at=attempt_started_at, finished_at=finished_at, duration_ms=duration_ms,
                        status=status, error_code=error_code, network_attempted=True,
                        total_tokens=usage["total_tokens"], cost_usd=estimated_cost(usage, settings),
                    ))
                    record_attempt(
                        settings, run_id=run_id, conversation_id=conversation.id, checklist_id=checklist.id,
                        checklist_version=checklist.version, prompt_version=PROMPT_VERSION,
                        attempt=attempt_number, status=status, reply=reply, error_code=error_code,
                        connection_id=connection.id, quota_group=connection.quota_group,
                        started_at=attempt_started_at.isoformat(), finished_at=finished_at.isoformat(),
                        duration_ms=duration_ms, input_sha256=input_hash, checklist_sha256=checklist_hash,
                        request_input_sha256=request_hash,
                        system_instruction_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
                    )

                try:
                    try:
                        # generate_once creates its own client, sends one HTTP request and
                        # performs NO SDK/transport retry. Never mutate process credentials.
                        reply = await provider.generate_once(selected_settings, instruction, user_content, schema)
                    except asyncio.CancelledError:
                        try:
                            audit("cancelled", error_code="ai_cancelled")
                        except AIProviderError:
                            pass  # Cancellation still propagates if the audit disk also fails.
                        raise
                    except AIProviderError as exc:
                        if exc.raw_response is not None:
                            last_raw = exc.raw_response
                        audit("provider_error", getattr(exc, "reply", None), exc.code)
                        last_error = exc
                        if exc.code == "provider_authentication_failed":
                            request_gate.disable(connection, settings.model)
                            advance = True
                        elif exc.code in _QUOTA_ERRORS:
                            cooldown = max(settings.quota_exhaustion_cooldown_seconds,
                                           getattr(exc, "retry_after", None) or 0.0)
                            request_gate.defer_group(connection, settings.model, cooldown, exc.code)
                            advance = True
                        elif exc.code == "provider_rate_limit":
                            cooldown = max(settings.rate_limit_cooldown_seconds,
                                           getattr(exc, "retry_after", None) or 0.0)
                            request_gate.defer_group(connection, settings.model, cooldown, exc.code)
                            if cooldown > settings.retry_max_seconds:
                                advance = True
                            else:
                                delay = max(delay, cooldown)
                        elif exc.retryable:
                            retry_after = getattr(exc, "retry_after", None) or 0.0
                            delay = max(delay, retry_after)
                            if exc.code in _TRANSIENT_ERRORS:
                                advance = request_gate.transient_failure(
                                    connection, settings.model, threshold=settings.transient_attempts_per_connection,
                                    backoff=delay, circuit_seconds=settings.transient_cooldown_seconds, code=exc.code,
                                )
                                advance_delay = min(delay, settings.retry_max_seconds)
                            # A long server delay must never be shortened to fit the budget.
                            if delay > settings.retry_max_seconds:
                                advance = True
                        else:
                            raise
                    except Exception:
                        audit("provider_error", error_code="provider_error")
                        raise AIProviderError("provider_error", "Ошибка AI-провайдера. Проверьте настройки подключения.") from None
                    else:
                        if reply.raw_text is not None:
                            last_raw = reply.raw_text
                        if reply.finish_reason == "MAX_TOKENS":
                            audit("output_limit", reply, "ai_output_limit")
                            raise AIProviderError("ai_output_limit", "Достигнут лимит ответа Gemini. Проверьте GEMINI_MAX_OUTPUT_TOKENS.")
                        if reply.blocked:
                            audit("blocked", reply, "ai_blocked")
                            raise AIProviderError("ai_blocked", "Провайдер отказался оценивать этот диалог.")
                        try:
                            if reply.finish_reason != "STOP" or not reply.raw_text:
                                raise InvalidResponse("incomplete_response")
                            criteria = validate_response(reply.raw_text, conversation, checklist)
                        except InvalidResponse as exc:
                            audit("invalid_response", reply, str(exc))
                            last_error = AIProviderError("invalid_ai_response", "AI вернул невалидную оценку.", retryable=True)
                            feedback = _feedback(str(exc))
                        else:
                            audit("completed", reply)
                            request_gate.succeeded(connection, settings.model)
                            metadata = execution()
                            return EvaluationDraft(
                                criteria=criteria, raw_response=reply.raw_text, provider=settings.provider,
                                model=reply.model_version or settings.model, prompt_version=PROMPT_VERSION,
                                models_used=metadata.models_used, execution=metadata,
                            )
                finally:
                    request_gate.release_connection(lease)

                if advance:
                    connection_index += 1
                    if connection_index < len(connections) and len(attempts) < settings.max_attempts:
                        await _pause(advance_delay)
                elif len(attempts) < settings.max_attempts:
                    await _pause(delay)

        if last_error is not None:
            if len(connections) > 1 and connection_index >= len(connections):
                raise AIProviderError(
                    "provider_connections_unavailable",
                    "Все настроенные AI-подключения недоступны. Проверьте ключи и квоты, затем повторите оценку.",
                    retryable=last_error.retryable,
                )
            raise last_error
        raise AIProviderError("provider_error", "AI не вернул оценку.")
    except asyncio.CancelledError as exc:
        # Preserve cancellation semantics: the backend may be enforcing its own
        # deadline or shutting down. Its await wrapper can snapshot these fields
        # before wait_for converts cancellation into TimeoutError. The in-flight
        # attempt has already been audited and its connection lease released.
        exc.execution = execution()
        exc.raw_response = last_raw
        raise
    except TimeoutError:
        raise AIProviderError("ai_timeout", "Превышено общее время оценки AI.", retryable=True,
                              raw_response=last_raw, execution=execution()) from None
    except AIProviderError as exc:
        raise AIProviderError(exc.code, exc.message, retryable=exc.retryable,
                              raw_response=exc.raw_response if exc.raw_response is not None else last_raw,
                              execution=execution()) from None
