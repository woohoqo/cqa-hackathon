"""One bounded Gemini REST request; the service owns all retries and validation.

API reference (checked 2026-11-07): https://ai.google.dev/api/generate-content
Uses the responseMimeType / responseJsonSchema compatibility fields still shown
in official generateContent structured-output examples. A migration to the newer
responseFormat should be tested with a real key before changing this transport.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..contracts import AIProviderError
from .config import AISettings

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"
_BLOCK_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
_QUOTA_FAILURE = "type.googleapis.com/google.rpc.QuotaFailure"
_ERROR_INFO = "type.googleapis.com/google.rpc.ErrorInfo"
_DURATION = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,9})?s\Z")
_QUOTA_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,199}\Z")
_DAILY_QUOTA = re.compile(r"PerDay(?:[A-Z_-]|$)|(?:^|[_-])per[_-]day(?:[_-]|$)")
_AUTH_REASONS = {"API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_REVOKED", "API_KEY_SERVICE_BLOCKED",
                 "API_KEY_HTTP_REFERRER_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED", "API_KEY_UNAUTHORIZED"}
_EXHAUSTED_REASONS = {"BILLING_DISABLED", "BILLING_NOT_ACTIVE", "CREDIT_EXHAUSTED",
                      "CREDITS_EXHAUSTED", "SPEND_LIMIT_EXCEEDED", "PROJECT_SPEND_CAP_EXCEEDED"}


@dataclass(frozen=True)
class ProviderReply:
    raw_text: str | None
    finish_reason: str | None
    model_version: str | None
    response_id: str | None
    usage: dict
    blocked: bool = False


class TransientProviderError(AIProviderError):
    def __init__(self, code: str, message: str, *, retry_after: float | None = None):
        super().__init__(code, message, retryable=True)
        self.retry_after = retry_after


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except (ValueError, OverflowError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _quota_hints(body: bytes) -> tuple[float | None, bool]:
    """Extract only typed retry/quota hints; never retain provider error text.

    google.rpc.RetryInfo uses protobuf JSON Duration (seconds, up to 9 decimal
    places). QuotaFailure defines quotaId, but its service-specific naming is
    not a universal API guarantee: only explicit daily identifiers are matched.
    Unknown quota IDs retain the ordinary transient 429 behavior.
    """
    try:
        envelope = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        return None, False
    error = envelope.get("error") if isinstance(envelope, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return None, False
    delays = []
    daily = False
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if detail.get("@type") == _RETRY_INFO:
            raw = detail.get("retryDelay")
            if isinstance(raw, str) and _DURATION.fullmatch(raw):
                delay = float(raw[:-1])
                if 0 <= delay <= 315_576_000_000:
                    delays.append(delay)
        elif detail.get("@type") == _QUOTA_FAILURE:
            violations = detail.get("violations")
            if not isinstance(violations, list):
                continue
            for violation in violations:
                quota_id = violation.get("quotaId") if isinstance(violation, dict) else None
                if isinstance(quota_id, str) and _QUOTA_ID.fullmatch(quota_id) and _DAILY_QUOTA.search(quota_id):
                    daily = True
    return max(delays, default=None), daily


def _error_reasons(body: bytes) -> set[str]:
    """Use a small typed allow-list, never classify from private free-text messages."""
    try:
        envelope = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        return set()
    error = envelope.get("error") if isinstance(envelope, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return set()
    return {detail["reason"] for detail in details if isinstance(detail, dict)
            and detail.get("@type") == _ERROR_INFO
            and detail.get("domain") in {"googleapis.com", "generativelanguage.googleapis.com"}
            and isinstance(detail.get("reason"), str)
            and detail["reason"] in _AUTH_REASONS | _EXHAUSTED_REASONS}


async def _bounded_error_body(response: httpx.Response) -> bytes:
    body = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_ERROR_BYTES:
                return b""  # A partial error body must not influence key/group state.
            body.extend(chunk)
    except httpx.HTTPError:
        return b""
    return bytes(body)


async def _rate_limit_error(response: httpx.Response) -> AIProviderError:
    header_delay = _retry_after(response.headers.get("Retry-After"))
    body = await _bounded_error_body(response)
    body_delay, daily = _quota_hints(body)
    delays = [delay for delay in (header_delay, body_delay) if delay is not None]
    retry_after = max(delays, default=None)
    if daily or _error_reasons(body) & _EXHAUSTED_REASONS:
        error = AIProviderError(
            "provider_daily_quota_exhausted" if daily else "provider_quota_exhausted",
            "Квота или доступный лимит проекта Gemini исчерпаны. Проверьте группу квоты в AI Studio.",
            retryable=False,
        )
        # A reported delay is a lower bound, not a promise of a quota reset.
        # Do not invent a reset time when Google omits it.
        error.retry_after = retry_after
        return error
    return TransientProviderError(
        "provider_rate_limit", "Gemini is temporarily rate limited.", retry_after=retry_after,
    )


def _envelope_error() -> AIProviderError:
    return TransientProviderError("invalid_provider_response", "Gemini returned an invalid response envelope.")


def _optional_string(envelope: dict, key: str) -> str | None:
    value = envelope.get(key)
    if value is not None and (not isinstance(value, str) or len(value) > 300 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise _envelope_error()
    return value


def _reject_constant(value: str):
    raise ValueError("Non-JSON numeric value")


def _parse_reply(body: bytes) -> ProviderReply:
    try:
        envelope = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise _envelope_error() from None
    if not isinstance(envelope, dict):
        raise _envelope_error()
    # Even an unusable candidate can incur billable tokens. Keep native usage
    # available to the service's audit path without placing it in public errors.
    usage = envelope.get("usageMetadata", {})
    retained_metadata = {}
    for name in ("modelVersion", "responseId"):
        try:
            retained_metadata[name] = _optional_string(envelope, name)
        except AIProviderError:
            retained_metadata[name] = None
    retained = ProviderReply(None, None, retained_metadata["modelVersion"], retained_metadata["responseId"], usage if isinstance(usage, dict) else {})
    try:
        return _parse_envelope(envelope)
    except AIProviderError as error:
        error.reply = retained
        raise


def _parse_envelope(envelope: dict) -> ProviderReply:
    usage = envelope.get("usageMetadata", {})
    feedback = envelope.get("promptFeedback", {})
    if not isinstance(usage, dict) or not isinstance(feedback, dict):
        raise _envelope_error()
    block_reason = _optional_string(feedback, "blockReason")
    blocked = bool(block_reason and block_reason != "BLOCK_REASON_UNSPECIFIED")
    model_version = _optional_string(envelope, "modelVersion")
    response_id = _optional_string(envelope, "responseId")
    candidates = envelope.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 1:
        raise _envelope_error()
    if not candidates:
        if not blocked:
            raise _envelope_error()
        return ProviderReply(None, None, model_version, response_id, usage, True)
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise _envelope_error()
    finish_reason = _optional_string(candidate, "finishReason")
    content = candidate.get("content", {})
    if not isinstance(content, dict):
        raise _envelope_error()
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        raise _envelope_error()
    texts = []
    for part in parts:
        if not isinstance(part, dict):
            raise _envelope_error()
        thought = part.get("thought", False)
        if type(thought) is not bool:
            raise _envelope_error()
        text = part.get("text")
        if text is not None and not isinstance(text, str):
            raise _envelope_error()
        if text is not None and not thought:
            texts.append(text)
    return ProviderReply(
        "".join(texts) if texts else None, finish_reason, model_version,
        response_id, usage, blocked or finish_reason in _BLOCK_REASONS,
    )


async def generate_once(settings: AISettings, system_instruction: str, user_content: str, schema: dict) -> ProviderReply:
    """Send exactly one request, preserving visible model text without repair."""
    generation = {
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
        "maxOutputTokens": settings.max_output_tokens,
        "temperature": settings.temperature,
    }
    if settings.thinking_level is not None:
        generation["thinkingConfig"] = {"thinkingLevel": settings.thinking_level.upper()}
    elif settings.thinking_budget is not None:
        generation["thinkingConfig"] = {"thinkingBudget": settings.thinking_budget}
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": generation,
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds), follow_redirects=False, trust_env=False,
        ) as client:
            async with client.stream(
                "POST", f"{_ENDPOINT}{settings.model}:generateContent",
                headers={"x-goog-api-key": settings.api_key, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                status = response.status_code
                if status == 429:
                    raise await _rate_limit_error(response)
                if status == 408 or 500 <= status <= 599:
                    raise TransientProviderError(
                        "provider_unavailable",
                        "Gemini is temporarily unavailable or rate limited.",
                        retry_after=_retry_after(response.headers.get("Retry-After")),
                    )
                if status in {401, 403}:
                    raise AIProviderError("provider_authentication_failed", "Gemini отклонил ключ или доступ проекта.")
                if status != 200:
                    # Google can report an invalid API key as HTTP 400 with
                    # ErrorInfo, distinct from a malformed schema/model request.
                    if status == 400 and _error_reasons(await _bounded_error_body(response)) & _AUTH_REASONS:
                        raise AIProviderError("provider_authentication_failed", "Gemini отклонил ключ или доступ проекта.")
                    raise AIProviderError("provider_request_failed", "Gemini rejected the evaluation request. Check model access and configuration.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise AIProviderError("provider_response_too_large", "Gemini response exceeded the configured safety bound.")
                    body.extend(chunk)
                return _parse_reply(bytes(body))
    except httpx.TimeoutException:
        raise TransientProviderError("provider_timeout", "Gemini request timed out.") from None
    except httpx.TransportError:
        raise TransientProviderError("provider_connection_failed", "Could not complete the Gemini request.") from None
