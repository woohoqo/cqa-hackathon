"""Synthetic failover contracts; every HTTP exchange is MockTransport, never real quota."""
import asyncio
from dataclasses import replace
import json
import time

import httpx
import pytest

from backend.app.ai import config, provider, service
from backend.app.ai.config import AIConnection, AISettings
from backend.app.ai.pacing import RequestGate
from backend.app.ai.tests.test_service import records
from backend.app.ai.tests.test_validation import checklist, conversation, response
from backend.app.contracts import AIProviderError

PRIMARY = "synthetic-primary-key"
BACKUP1 = "synthetic-backup-one-key"
BACKUP2 = "synthetic-backup-two-key"
RAW = " \n" + json.dumps(response(), ensure_ascii=False, indent=2) + "\r\n "


@pytest.fixture
def settings(monkeypatch, tmp_path):
    cfg = AISettings(
        api_key=PRIMARY, backup_api_key_1=BACKUP1, backup_api_key_2=BACKUP2,
        primary_quota_group="project-a", backup_1_quota_group="project-b", backup_2_quota_group="project-c",
        model="gemini-synthetic", retry_base_seconds=0, retry_max_seconds=0,
        audit_path=str(tmp_path / "synthetic-failover.jsonl"),
        input_price_per_million=1, cached_input_price_per_million=0.1, output_price_per_million=2,
        price_label="Synthetic test rates, not vendor pricing",
    )
    monkeypatch.setattr(service.AISettings, "from_env", lambda: cfg)
    monkeypatch.setattr(service, "request_gate", RequestGate())
    return cfg


def configure(monkeypatch, settings, **changes):
    cfg = replace(settings, **changes)
    monkeypatch.setattr(service.AISettings, "from_env", lambda: cfg)
    return cfg


def success(raw=RAW, *, finish="STOP"):
    return httpx.Response(200, json={
        "modelVersion": "gemini-synthetic-resolved", "responseId": "synthetic-only",
        "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 20,
                          "candidatesTokenCount": 30, "thoughtsTokenCount": 10, "totalTokenCount": 140},
        "candidates": [{"finishReason": finish, "content": {"parts": [{"text": raw}]}}],
    })


def daily_quota():
    return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED", "details": [{
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
    }]}})


def mock_clients(monkeypatch, handler):
    original = httpx.AsyncClient
    clients = []
    def factory(**kwargs):
        client = original(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(provider.httpx, "AsyncClient", factory)
    return clients


def evaluate():
    return asyncio.run(service.evaluate_dialogue(conversation(), checklist()))


def test_primary_success_does_not_touch_reserves(monkeypatch, settings):
    keys = []
    def handler(request):
        keys.append(request.headers["x-goog-api-key"])
        return success()
    mock_clients(monkeypatch, handler)
    draft = evaluate()
    assert keys == [PRIMARY]
    assert draft.raw_response == RAW
    assert draft.execution.selected_connection == "primary"
    assert draft.execution.failover_used is False
    assert draft.execution.total_tokens == 140
    assert draft.execution.cost_usd == pytest.approx(0.000162)
    assert draft.models_used == draft.execution.models_used == ["gemini-synthetic-resolved"]


@pytest.mark.parametrize("failed_keys,expected", [
    ({PRIMARY}, [PRIMARY, BACKUP1]),
    ({PRIMARY, BACKUP1}, [PRIMARY, BACKUP1, BACKUP2]),
])
def test_auth_failover_keeps_request_identical_and_clients_independent(monkeypatch, settings, failed_keys, expected):
    captured = []
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-process-value-must-not-change")
    def handler(request):
        key = request.headers["x-goog-api-key"]
        captured.append((key, str(request.url), json.loads(request.content)))
        return httpx.Response(401) if key in failed_keys else success()
    clients = mock_clients(monkeypatch, handler)
    draft = evaluate()
    assert [item[0] for item in captured] == expected
    assert all(item[1:] == captured[0][1:] for item in captured)
    assert len(clients) == len(expected) and len({id(item) for item in clients}) == len(expected)
    assert all(client.is_closed for client in clients)
    assert config.os.environ["GEMINI_API_KEY"] == "synthetic-process-value-must-not-change"
    assert draft.execution.failover_used is True
    assert draft.execution.selected_connection == ("backup_1" if len(expected) == 2 else "backup_2")
    assert [item.attempt for item in draft.execution.attempts] == list(range(1, len(expected) + 1))
    assert draft.execution.total_tokens is None and draft.execution.cost_usd is None
    assert draft.execution.attempts[-1].total_tokens == 140
    assert draft.models_used == ["gemini-synthetic-resolved"]
    serialized = json.dumps(records(settings)) + draft.model_dump_json()
    assert all(key not in serialized for key in (PRIMARY, BACKUP1, BACKUP2))


def test_invalid_credentials_disabled_across_jobs_until_configuration_changes(monkeypatch, settings):
    keys = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        keys.append(key)
        return success() if key == "synthetic-fixed-key" else httpx.Response(403)
    mock_clients(monkeypatch, handler)
    with pytest.raises(AIProviderError) as first:
        evaluate()
    assert first.value.code == "provider_connections_unavailable"
    assert len(first.value.execution.attempts) == 3
    with pytest.raises(AIProviderError) as second:
        evaluate()
    assert second.value.execution.attempts == []
    assert second.value.execution.models_used == []
    assert keys == [PRIMARY, BACKUP1, BACKUP2]
    configure(monkeypatch, settings, api_key="synthetic-fixed-key")
    assert evaluate().execution.selected_connection == "primary"
    assert keys[-1] == "synthetic-fixed-key"


def test_daily_quota_skips_every_key_in_same_group(monkeypatch, settings):
    cfg = configure(monkeypatch, settings, backup_1_quota_group="project-a")
    keys = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        keys.append(key)
        return daily_quota() if key == PRIMARY else success()
    mock_clients(monkeypatch, handler)
    assert evaluate().execution.selected_connection == "backup_2"
    assert keys == [PRIMARY, BACKUP2]
    assert evaluate().execution.selected_connection == "backup_2"
    assert keys == [PRIMARY, BACKUP2, BACKUP2]
    assert [item["connection_id"] for item in records(cfg)] == ["primary", "backup_2", "backup_2"]


@pytest.mark.parametrize("groups,expected", [
    (("", "", ""), [PRIMARY]),
    (("", "project-b", "project-c"), [PRIMARY]),
    (("project-a", "", "project-b"), [PRIMARY, BACKUP2]),
    (("project-a", "project-a", "project-a"), [PRIMARY]),
])
def test_unknown_or_shared_quota_is_never_assumed_independent(monkeypatch, settings, groups, expected):
    configure(monkeypatch, settings, primary_quota_group=groups[0], backup_1_quota_group=groups[1], backup_2_quota_group=groups[2])
    keys = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        keys.append(key)
        return daily_quota() if key == PRIMARY else success()
    mock_clients(monkeypatch, handler)
    if len(expected) == 1:
        with pytest.raises(AIProviderError) as error:
            evaluate()
        assert error.value.code == "provider_connections_unavailable"
        assert len(error.value.execution.attempts) == 1
    else:
        assert evaluate().execution.selected_connection == "backup_2"
    assert keys == expected


def test_temporary_rate_limit_obeys_retry_after_before_retrying_same_connection(monkeypatch, settings):
    configure(monkeypatch, settings, rate_limit_cooldown_seconds=1, retry_max_seconds=1.1,
              timeout_seconds=0.1, evaluation_timeout_seconds=3)
    requests = []
    def handler(request):
        requests.append((request.headers["x-goog-api-key"], time.monotonic()))
        return httpx.Response(429, headers={"Retry-After": "1"}) if len(requests) == 1 else success()
    mock_clients(monkeypatch, handler)
    output = evaluate()
    assert [key for key, _ in requests] == [PRIMARY, PRIMARY]
    assert requests[1][1] - requests[0][1] >= 0.99
    assert output.execution.failover_used is False


def test_long_rate_limit_pauses_group_and_uses_explicit_independent_reserve(monkeypatch, settings):
    keys = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        keys.append(key)
        return httpx.Response(429, headers={"Retry-After": "3600"}) if key == PRIMARY else success()
    mock_clients(monkeypatch, handler)
    assert evaluate().execution.selected_connection == "backup_1"
    assert keys == [PRIMARY, BACKUP1]


def test_network_failures_switch_with_pauses_and_total_attempt_limit(monkeypatch, settings):
    configure(monkeypatch, settings, transient_attempts_per_connection=1,
              retry_base_seconds=0.01, retry_max_seconds=0.01)
    requests = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        requests.append((key, time.monotonic()))
        return success() if key == BACKUP2 else httpx.Response(503)
    mock_clients(monkeypatch, handler)
    output = evaluate()
    assert [key for key, _ in requests] == [PRIMARY, BACKUP1, BACKUP2]
    assert all(b[1] - a[1] >= 0.007 for a, b in zip(requests, requests[1:]))
    assert len(output.execution.attempts) == 3


def test_global_cap_is_not_multiplied_by_three_connections(monkeypatch, settings):
    configure(monkeypatch, settings, max_attempts=2, transient_attempts_per_connection=1)
    calls = []
    def handler(request):
        calls.append(request.headers["x-goog-api-key"])
        return httpx.Response(503)
    mock_clients(monkeypatch, handler)
    with pytest.raises(AIProviderError) as error:
        evaluate()
    assert calls == [PRIMARY, BACKUP1]
    assert len(error.value.execution.attempts) == 2
    assert error.value.execution.cost_usd is None


def test_all_connections_unavailable_returns_no_result_and_counts_every_attempt(monkeypatch, settings):
    configure(monkeypatch, settings, transient_attempts_per_connection=1)
    calls = []
    def handler(request):
        calls.append(request.headers["x-goog-api-key"])
        raise httpx.ReadTimeout("SYNTHETIC private transport detail")
    mock_clients(monkeypatch, handler)
    with pytest.raises(AIProviderError) as error:
        evaluate()
    assert error.value.code == "provider_connections_unavailable" and error.value.retryable
    assert calls == [PRIMARY, BACKUP1, BACKUP2]
    assert len(error.value.execution.attempts) == 3
    assert error.value.execution.total_tokens is None and error.value.execution.cost_usd is None
    assert "private" not in str(error.value)


@pytest.mark.parametrize("kind,expected", [("bad_request", "provider_request_failed"),
                                          ("safety", "ai_blocked"), ("output_limit", "ai_output_limit")])
def test_bad_request_safety_and_output_limits_never_rotate_keys(monkeypatch, settings, kind, expected):
    keys = []
    def handler(request):
        keys.append(request.headers["x-goog-api-key"])
        if kind == "bad_request":
            return httpx.Response(400, text="Synthetic malformed request")
        return success(finish="SAFETY" if kind == "safety" else "MAX_TOKENS")
    mock_clients(monkeypatch, handler)
    with pytest.raises(AIProviderError) as error:
        evaluate()
    assert error.value.code == expected
    assert keys == [PRIMARY]
    assert len(error.value.execution.attempts) == 1


def test_invalid_response_repair_stays_on_same_key_with_global_budget(monkeypatch, settings):
    keys = []
    def handler(request):
        keys.append(request.headers["x-goog-api-key"])
        return success(raw=" {invalid synthetic json ")
    mock_clients(monkeypatch, handler)
    with pytest.raises(AIProviderError) as error:
        evaluate()
    assert keys == [PRIMARY] * settings.max_attempts
    assert error.value.code == "invalid_ai_response"
    assert error.value.raw_response == " {invalid synthetic json "
    assert error.value.execution.total_tokens == 140 * settings.max_attempts
    assert error.value.execution.cost_usd == pytest.approx(0.000162 * settings.max_attempts)


def test_total_deadline_includes_inflight_request_and_prevents_backup_spend(monkeypatch, settings):
    configure(monkeypatch, settings, evaluation_timeout_seconds=0.03)
    keys = []
    async def handler(request):
        keys.append(request.headers["x-goog-api-key"])
        await asyncio.sleep(1)
        return success()
    mock_clients(monkeypatch, handler)
    with pytest.raises(AIProviderError) as error:
        evaluate()
    assert error.value.code == "ai_timeout"
    assert keys == [PRIMARY]
    assert error.value.execution.attempts[0].status == "cancelled"
    assert error.value.execution.cost_usd is None
    assert error.value.execution.duration_ms < 1000


def test_concurrent_jobs_do_not_storm_disabled_primary(monkeypatch, settings):
    calls = []
    active = {}
    maximum = {}
    async def handler(request):
        key = request.headers["x-goog-api-key"]
        calls.append(key)
        active[key] = active.get(key, 0) + 1
        maximum[key] = max(maximum.get(key, 0), active[key])
        try:
            await asyncio.sleep(0.005)
            return httpx.Response(401) if key == PRIMARY else success()
        finally:
            active[key] -= 1
    mock_clients(monkeypatch, handler)
    async def run():
        return await asyncio.gather(*(service.evaluate_dialogue(conversation(), checklist()) for _ in range(6)))
    outputs = asyncio.run(run())
    assert calls.count(PRIMARY) == 1
    assert calls.count(BACKUP1) == 6 and BACKUP2 not in calls
    assert maximum == {PRIMARY: 1, BACKUP1: 1}
    assert all(item.execution.selected_connection == "backup_1" for item in outputs)


def test_same_group_cannot_run_in_parallel_but_known_independent_groups_can():
    async def run():
        gate = RequestGate()
        first = AIConnection("primary", PRIMARY, "a")
        same = AIConnection("backup_1", BACKUP1, "a")
        independent = AIConnection("backup_2", BACKUP2, "b")
        lease = await gate.acquire_connection(first, "m", 0)
        pending = asyncio.create_task(gate.acquire_connection(same, "m", 0))
        other = await asyncio.wait_for(gate.acquire_connection(independent, "m", 0), 0.2)
        await asyncio.sleep(0.01)
        assert not pending.done()
        gate.release_connection(lease)
        next_lease = await asyncio.wait_for(pending, 0.2)
        gate.release_connection(next_lease)
        gate.release_connection(other)
    asyncio.run(run())


def test_auth_failover_still_obeys_shared_group_start_interval(monkeypatch, settings):
    configure(monkeypatch, settings, backup_1_quota_group="project-a", min_request_interval_seconds=0.03)
    starts = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        starts.append((key, time.monotonic()))
        return httpx.Response(401) if key == PRIMARY else success()
    mock_clients(monkeypatch, handler)
    assert evaluate().execution.selected_connection == "backup_1"
    assert [key for key, _ in starts] == [PRIMARY, BACKUP1]
    assert starts[1][1] - starts[0][1] >= 0.029


def test_concurrent_jobs_observe_shared_exhaustion_before_spending(monkeypatch, settings):
    configure(monkeypatch, settings, backup_1_quota_group="project-a")
    keys = []
    async def handler(request):
        key = request.headers["x-goog-api-key"]
        keys.append(key)
        await asyncio.sleep(0.005)
        return daily_quota() if key == PRIMARY else success()
    mock_clients(monkeypatch, handler)
    async def run():
        return await asyncio.gather(*(service.evaluate_dialogue(conversation(), checklist()) for _ in range(5)))
    outputs = asyncio.run(run())
    assert keys.count(PRIMARY) == 1 and BACKUP1 not in keys
    assert keys.count(BACKUP2) == 5
    assert all(item.execution.selected_connection == "backup_2" for item in outputs)


def test_network_circuit_recovers_with_one_probe_after_cooldown(monkeypatch, settings):
    configure(monkeypatch, settings, transient_attempts_per_connection=1, transient_cooldown_seconds=0.03)
    keys = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        keys.append(key)
        return httpx.Response(503) if keys == [PRIMARY] else success()
    mock_clients(monkeypatch, handler)
    assert evaluate().execution.selected_connection == "backup_1"
    assert evaluate().execution.selected_connection == "backup_1"
    time.sleep(0.04)
    assert evaluate().execution.selected_connection == "primary"
    assert keys == [PRIMARY, BACKUP1, BACKUP1, PRIMARY]


def test_execution_links_every_audit_row_and_hashes_actual_content(monkeypatch, settings):
    mock_clients(monkeypatch, lambda request: success())
    original = conversation()
    rubric = checklist()
    first = asyncio.run(service.evaluate_dialogue(original, rubric))
    changed = original.model_copy(deep=True)
    changed.messages[-1].text += " Synthetic changed content."
    second = asyncio.run(service.evaluate_dialogue(changed, rubric))
    rows = records(settings)
    assert [row["run_id"] for row in rows] == [first.execution.run_id, second.execution.run_id]
    assert rows[0]["conversation_id_sha256"] == rows[1]["conversation_id_sha256"]
    assert rows[0]["input_sha256"] != rows[1]["input_sha256"]
    assert rows[0]["request_input_sha256"] != rows[1]["request_input_sha256"]
    assert rows[0]["checklist_sha256"] == rows[1]["checklist_sha256"]
    assert rows[0]["started_at"] and rows[0]["finished_at"] and rows[0]["duration_ms"] >= 0
    assert rows[0]["network_attempted"] is True


def test_optional_empty_reserves_and_duplicate_keys_do_not_break_primary():
    cfg = AISettings(api_key=PRIMARY, model="gemini-synthetic")
    assert [item.id for item in cfg.connections()] == ["primary"]
    duplicate = replace(cfg, backup_api_key_1=PRIMARY, backup_api_key_2=PRIMARY)
    assert [item.id for item in duplicate.connections()] == ["primary"]
    assert PRIMARY not in repr(duplicate) and PRIMARY not in repr(duplicate.connections())


def test_reserve_configuration_from_environment_is_explicit(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    for name, value in {"GEMINI_API_KEY": PRIMARY, "GEMINI_API_KEY_BACKUP_1": BACKUP1,
                        "GEMINI_API_KEY_BACKUP_2": "", "GEMINI_MODEL": "gemini-synthetic",
                        "GEMINI_QUOTA_GROUP": "project-a", "GEMINI_BACKUP_1_QUOTA_GROUP": "project-b",
                        "AI_TRANSIENT_ATTEMPTS_PER_CONNECTION": "1"}.items():
        monkeypatch.setenv(name, value)
    cfg = AISettings.from_env()
    assert [(item.id, item.quota_group) for item in cfg.connections()] == [("primary", "project-a"), ("backup_1", "project-b")]
    assert cfg.transient_attempts_per_connection == 1


def test_typed_invalid_key_on_http400_can_fail_over_but_message_text_cannot(monkeypatch, settings):
    keys = []
    def handler(request):
        key = request.headers["x-goog-api-key"]
        keys.append(key)
        if key != PRIMARY:
            return success()
        return httpx.Response(400, json={"error": {"details": [{
            "@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID", "domain": "googleapis.com",
        }]}})
    mock_clients(monkeypatch, handler)
    assert evaluate().execution.selected_connection == "backup_1"
    assert keys == [PRIMARY, BACKUP1]
