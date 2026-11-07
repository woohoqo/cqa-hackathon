"""Synthetic transport/configuration checks; no network requests or real dialogs."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from backend.app.ai import config, provider
from backend.app.ai.config import AISettings
from backend.app.ai.provider import TransientProviderError, generate_once
from backend.app.contracts import AIProviderError


@pytest.fixture
def settings():
    return AISettings(api_key="synthetic-secret", model="gemini-3.8-flash")


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(provider.httpx, "AsyncClient", factory)
    return options


def run(settings):
    return asyncio.run(generate_once(settings, "Synthetic system rules", "Synthetic input", {"type": "object"}))


def test_request_and_exact_output_usage(monkeypatch, settings):
    observed = []
    usage = {"promptTokenCount": 100, "candidatesTokenCount": 12, "thoughtsTokenCount": 30, "cachedContentTokenCount": 50, "totalTokenCount": 142}

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={
            "modelVersion": "gemini-3.8-flash", "responseId": "synthetic-response",
            "usageMetadata": usage,
            "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": "private thought", "thought": True},
                {"text": "  {\n"}, {"text": '"criteria": []}\n'},
            ]}}],
        })

    options = mock_client(monkeypatch, handler)
    reply = run(settings)
    assert reply.raw_text == '  {\n"criteria": []}\n'
    assert reply.usage == usage
    assert reply.model_version == "gemini-3.8-flash"
    assert reply.response_id == "synthetic-response"
    assert reply.finish_reason == "STOP" and not reply.blocked
    assert len(observed) == 1
    request = observed[0]
    assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert request.headers["x-goog-api-key"] == "synthetic-secret"
    assert "synthetic-secret" not in str(request.url)
    payload = json.loads(request.content)
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "Synthetic input"}]}]
    assert payload["systemInstruction"]["parts"][0]["text"] == "Synthetic system rules"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"] == {"type": "object"}
    assert payload["generationConfig"]["temperature"] == 1
    assert "thinkingConfig" not in payload["generationConfig"]
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert options[0]["timeout"].read == 30


@pytest.mark.parametrize("changes, expected", [
    ({"thinking_level": "medium"}, {"thinkingLevel": "MEDIUM"}),
    ({"thinking_budget": 1024}, {"thinkingBudget": 1024}),
])
def test_thinking_options(monkeypatch, settings, changes, expected):
    def handler(request):
        assert json.loads(request.content)["generationConfig"]["thinkingConfig"] == expected
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}]})
    mock_client(monkeypatch, handler)
    run(replace(settings, **changes))


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_is_single_safe_attempt(monkeypatch, settings, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "2.5"}, text="synthetic-secret private transcript")
    mock_client(monkeypatch, handler)
    with pytest.raises(TransientProviderError) as error:
        run(settings)
    assert error.value.retryable and error.value.retry_after == 2.5
    assert "synthetic-secret" not in str(error.value) and "transcript" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 302])
def test_permanent_http_does_not_retry_or_follow_redirect(monkeypatch, settings, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.invalid/secret"}, text="synthetic-secret")
    mock_client(monkeypatch, handler)
    with pytest.raises(AIProviderError) as error:
        run(settings)
    assert not error.value.retryable and "synthetic-secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("exception", [httpx.ReadTimeout("private key"), httpx.ConnectError("private key")])
def test_transport_errors_are_safe(monkeypatch, settings, exception):
    def handler(request):
        raise exception
    mock_client(monkeypatch, handler)
    with pytest.raises(TransientProviderError) as error:
        run(settings)
    assert "private key" not in str(error.value)


def test_cancellation_propagates(monkeypatch, settings):
    async def handler(request):
        raise asyncio.CancelledError()
    mock_client(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        run(settings)


@pytest.mark.parametrize("envelope, text, blocked", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, None, True),
    ({"candidates": [{"finishReason": "SAFETY"}]}, None, True),
    ({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"unfinished":'}]}}]}, '{"unfinished":', False),
    ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "```bad JSON```"}]}}]}, "```bad JSON```", False),
])
def test_unusable_text_retains_usage_for_service_validation(monkeypatch, settings, envelope, text, blocked):
    envelope["usageMetadata"] = {"totalTokenCount": 42}
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=envelope))
    reply = run(settings)
    assert reply.raw_text == text and reply.blocked == blocked
    assert reply.usage == {"totalTokenCount": 42}


@pytest.mark.parametrize("body", [
    b"not JSON", b"[]", b"{}", b'{"candidates":null}',
    b'{"candidates":[{},{}]}', b'{"candidates":[42]}',
    b'{"candidates":[{"content":{"parts":[{"text":42}]}}]}',
    b'{"candidates":[{"content":{"parts":[{"text":"x","thought":"false"}]}}]}',
    b'{"candidates":[{}],"usageMetadata":[]}', b'{"candidates":[{}],"modelVersion":42}',
    b'{"candidates":[{}],"usageMetadata":{"totalTokenCount":NaN}}',
])
def test_invalid_envelopes_fail_closed(monkeypatch, settings, body):
    mock_client(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(AIProviderError) as error:
        run(settings)
    assert error.value.code == "invalid_provider_response"
    assert error.value.raw_response is None
    assert error.value.retryable


def test_invalid_candidate_retains_reported_usage(monkeypatch, settings):
    envelope = {"candidates": "invalid", "usageMetadata": {"totalTokenCount": 42}, "modelVersion": "synthetic-model", "responseId": "synthetic-id"}
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=envelope))
    with pytest.raises(AIProviderError) as error:
        run(settings)
    assert error.value.reply.usage == {"totalTokenCount": 42}
    assert error.value.reply.model_version == "synthetic-model"
    assert error.value.reply.response_id == "synthetic-id"


@pytest.mark.parametrize("metadata", [{"modelVersion": "x" * 301}, {"responseId": "unsafe\nmetadata"}])
def test_metadata_is_bounded_and_does_not_discard_usage(monkeypatch, settings, metadata):
    envelope = {"candidates": [{}], "usageMetadata": {"totalTokenCount": 42}, **metadata}
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=envelope))
    with pytest.raises(AIProviderError) as error:
        run(settings)
    assert error.value.reply.usage == {"totalTokenCount": 42}
    assert error.value.reply.model_version is None and error.value.reply.response_id is None


def test_response_size_bound(monkeypatch, settings):
    monkeypatch.setattr(provider, "MAX_RESPONSE_BYTES", 16)
    mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 17))
    with pytest.raises(AIProviderError) as error:
        run(settings)
    assert error.value.code == "provider_response_too_large"


def test_retry_after_numeric_date_and_invalid():
    assert provider._retry_after("3") == 3
    assert provider._retry_after("-1") == 0
    assert provider._retry_after("nan") is None
    assert provider._retry_after("garbage") is None
    future = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert 28 <= provider._retry_after(format_datetime(future)) <= 30


@pytest.fixture
def clean_environment(monkeypatch, tmp_path):
    for key in list(config.os.environ):
        if key.startswith(("AI_", "GEMINI_")):
            monkeypatch.delenv(key)
    monkeypatch.setattr(config, "_ROOT", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")


def test_env_defaults_key_redaction_and_backend_settings(clean_environment, monkeypatch):
    monkeypatch.setenv("AI_CONCURRENCY", "2")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "90")
    settings = AISettings.from_env()
    assert settings.max_attempts == 3 and settings.evaluation_timeout_seconds == 100
    assert "synthetic-key" not in repr(settings)


def test_root_dotenv_does_not_override_environment(clean_environment, tmp_path, monkeypatch):
    # Register this otherwise absent variable for teardown: dotenv itself writes
    # into os.environ outside monkeypatch's tracking.
    monkeypatch.setenv("AI_MAX_ATTEMPTS", "temporary")
    monkeypatch.delenv("AI_MAX_ATTEMPTS")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=dotenv-secret\nAI_MAX_ATTEMPTS=2\n", encoding="utf-8")
    settings = AISettings.from_env()
    assert settings.api_key == "synthetic-key" and settings.max_attempts == 2


@pytest.mark.parametrize("name, value", [
    ("AI_PROVIDER", "other"), ("GEMINI_MODEL", "../secret"),
    ("GEMINI_MODEL", "https://example.invalid/model"),
    ("GEMINI_API_KEY", "key\nsecret"), ("GEMINI_TIMEOUT_SECONDS", "nan"),
    ("GEMINI_TIMEOUT_SECONDS", "-1"), ("AI_MAX_ATTEMPTS", "6"),
    ("AI_MAX_ATTEMPTS", "1.2"), ("AI_EVALUATION_TIMEOUT_SECONDS", "inf"),
    ("GEMINI_MAX_OUTPUT_TOKENS", "0"), ("GEMINI_TEMPERATURE", "nan"),
    ("GEMINI_THINKING_LEVEL", "unknown"), ("GEMINI_THINKING_BUDGET", "-2"),
    ("AI_INPUT_USD_PER_MILLION", "-1"), ("AI_CACHED_INPUT_USD_PER_MILLION", "inf"),
    ("AI_OUTPUT_USD_PER_MILLION", "nan"), ("AI_RETRY_MAX_SECONDS", "0.1"),
    ("AI_USAGE_LOG_PATH", ""),
])
def test_invalid_settings_are_safe(clean_environment, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(AIProviderError) as error:
        AISettings.from_env()
    assert "synthetic-key" not in str(error.value)


@pytest.mark.parametrize("name", ["GEMINI_MODEL", "GEMINI_API_KEY"])
def test_missing_required_config(clean_environment, monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(AIProviderError) as error:
        AISettings.from_env()
    assert error.value.code == "provider_not_configured" and name in str(error.value)


def test_thinking_settings_mutually_exclusive(clean_environment, monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "high")
    monkeypatch.setenv("GEMINI_THINKING_BUDGET", "1024")
    with pytest.raises(AIProviderError):
        AISettings.from_env()


def test_configured_cost_snapshot(clean_environment, monkeypatch):
    monkeypatch.setenv("AI_INPUT_USD_PER_MILLION", "0.75")
    monkeypatch.setenv("AI_CACHED_INPUT_USD_PER_MILLION", "0.075")
    monkeypatch.setenv("AI_OUTPUT_USD_PER_MILLION", "3.75")
    monkeypatch.setenv("AI_PRICE_LABEL", "synthetic paid standard 2026-11-07")
    settings = AISettings.from_env()
    assert settings.input_price_per_million == 0.75
    assert settings.cached_input_price_per_million == 0.075
    assert settings.output_price_per_million == 3.75
    assert settings.price_label == "synthetic paid standard 2026-11-07"
