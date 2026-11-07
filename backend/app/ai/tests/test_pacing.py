"""Synthetic timing and quota checks; no real Gemini calls or quota assumptions."""
import asyncio
from dataclasses import replace

import pytest

from backend.app.ai import pacing, service
from backend.app.ai.config import AISettings
from backend.app.ai.pacing import RequestGate
from backend.app.ai.provider import TransientProviderError
from backend.app.contracts import AIProviderError
from backend.app.ai.tests.test_service import settings, reply, records
from backend.app.ai.tests.test_validation import checklist, conversation


def test_concurrent_requests_and_retries_are_spaced():
    async def scenario():
        gate = RequestGate()
        starts = []
        async def call():
            await gate.acquire("model", 0.01)
            starts.append(asyncio.get_running_loop().time())
        await asyncio.gather(*(call() for _ in range(4)))
        assert all(b - a >= 0.009 for a, b in zip(starts, starts[1:]))
    asyncio.run(scenario())


def test_cooldown_is_shared_across_evaluations_and_does_not_shorten(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(pacing.time, "monotonic", lambda: clock[0])
    gate = RequestGate()
    gate.defer("model", 3600)
    gate.defer("model", 60)
    with pytest.raises(TransientProviderError) as error:
        asyncio.run(gate.acquire("model", 0))
    assert error.value.retry_after == 3600
    clock[0] = 3610
    asyncio.run(gate.acquire("model", 0))


def test_cancelled_wait_does_not_reserve_future_slot():
    async def scenario():
        gate = RequestGate()
        await gate.acquire("model", 0.1)
        next_start = gate._next_start["model"]
        pending = asyncio.create_task(gate.acquire("model", 0.1))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert gate._next_start["model"] == next_start
    asyncio.run(scenario())


@pytest.mark.parametrize("code, delay", [("provider_rate_limit", None), ("provider_rate_limit", 3600),
                                       ("provider_daily_quota_exhausted", 3600)])
def test_quota_stops_retries_and_blocks_later_calls_without_fake_usage(monkeypatch, settings, code, delay):
    # This evaluation cannot wait the cooldown and still leave an HTTP window.
    monkeypatch.setattr(service.AISettings, "from_env", lambda: replace(settings, evaluation_timeout_seconds=0.02))
    calls = []
    async def fake(*args):
        calls.append(1)
        if code == "provider_rate_limit":
            raise TransientProviderError(code, "Synthetic quota", retry_after=delay)
        error = AIProviderError(code, "Synthetic daily quota", retryable=False)
        error.retry_after = delay
        raise error
    monkeypatch.setattr(service.provider, "generate_once", fake)
    for _ in range(2):
        with pytest.raises(AIProviderError):
            asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert len(calls) == 1
    assert len(records(settings)) == 1
    assert records(settings)[0]["usage"]["total_tokens"] is None


def test_short_cooldown_is_awaited_so_queued_work_can_resume(monkeypatch, settings):
    calls = []
    async def fake(*args):
        calls.append(1)
        return reply()
    monkeypatch.setattr(service.provider, "generate_once", fake)
    service.request_gate.defer(settings.model, 0.02)
    asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert len(calls) == 1 and len(records(settings)) == 1
    assert records(settings)[0]["status"] == "completed"


def test_daily_cause_survives_shorter_minute_limit():
    gate = RequestGate()
    gate.defer("model", 3600, "provider_daily_quota_exhausted")
    gate.defer("model", 60)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(gate.acquire("model", 0, max_cooldown_wait=5000))
    assert error.value.code == "provider_daily_quota_exhausted" and not error.value.retryable
    assert error.value.retry_after > 3500


def test_waiting_for_pacing_is_inside_total_timeout(monkeypatch, settings):
    cfg = replace(settings, evaluation_timeout_seconds=0.01)
    monkeypatch.setattr(service.AISettings, "from_env", lambda: cfg)
    async def scenario():
        await service.request_gate.acquire(settings.model, 5)
        with pytest.raises(AIProviderError) as error:
            await service.evaluate_dialogue(conversation(), checklist())
        assert error.value.code == "ai_timeout"
    async def forbidden(*args):
        pytest.fail("Provider must not run before a pacing slot exists")
    monkeypatch.setattr(service.provider, "generate_once", forbidden)
    asyncio.run(scenario())
    assert records(settings) == []


@pytest.mark.parametrize("changes", [
    {"min_request_interval_seconds": -1}, {"min_request_interval_seconds": float("nan")},
    {"rate_limit_cooldown_seconds": 0}, {"rate_limit_cooldown_seconds": float("inf")},
])
def test_invalid_pacing_configuration(changes):
    with pytest.raises(AIProviderError):
        AISettings(api_key="synthetic-key", model="gemini-synthetic", **changes)


def test_pacing_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("AI_MIN_REQUEST_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("AI_RATE_LIMIT_COOLDOWN_SECONDS", "60")
    cfg = AISettings.from_env()
    assert cfg.min_request_interval_seconds == 15 and cfg.rate_limit_cooldown_seconds == 60
