"""One bounded Gemini REST request; the service owns all retries and validation.

API reference (checked 2026-11-07): https://ai.google.dev/api/generate-content
Uses the responseMimeType / responseJsonSchema compatibility fields still shown
in official generateContent structured-output examples. A migration to the newer
responseFormat should be tested with a real key before changing this transport.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..contracts import AIProviderError
from .config import AISettings

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"
_BLOCK_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


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
                if status in {408, 429} or 500 <= status <= 599:
                    raise TransientProviderError(
                        "provider_rate_limit" if status == 429 else "provider_unavailable",
                        "Gemini is temporarily unavailable or rate limited.",
                        retry_after=_retry_after(response.headers.get("Retry-After")),
                    )
                if status in {401, 403}:
                    raise AIProviderError("provider_authentication_failed", "Gemini rejected the configured credentials or project access.")
                if status != 200:
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
