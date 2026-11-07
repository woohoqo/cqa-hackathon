"""Synthetic provider fixtures; no network and no claims of model accuracy."""
import asyncio
from dataclasses import replace
import json

import pytest

from backend.app.contracts import AIProviderError, EvaluationDraft
from backend.app.ai import service
from backend.app.ai.audit import estimated_cost, usage_counts
from backend.app.ai.config import AISettings
from backend.app.ai.pacing import RequestGate
from backend.app.ai.provider import ProviderReply, TransientProviderError
from backend.app.ai.tests.test_validation import checklist, conversation, response


@pytest.fixture
def settings(monkeypatch, tmp_path):
    cfg = AISettings(api_key="synthetic-test-key", model="gemini-synthetic", retry_base_seconds=0,
                     retry_max_seconds=0, audit_path=str(tmp_path / "usage.jsonl"))
    monkeypatch.setattr(service.AISettings, "from_env", lambda: cfg)
    monkeypatch.setattr(service, "request_gate", RequestGate())
    return cfg


def reply(text=None, *, finish="STOP", blocked=False):
    return ProviderReply(json.dumps(response(), ensure_ascii=False, indent=2) if text is None else text,
                         finish, "gemini-synthetic-resolved", "synthetic-response",
                         {"promptTokenCount": 100, "cachedContentTokenCount": 20,
                          "candidatesTokenCount": 30, "thoughtsTokenCount": 10, "totalTokenCount": 140}, blocked)


def records(settings):
    with open(settings.audit_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_single_success_preserves_raw_and_contract_roundtrip(monkeypatch, settings):
    raw = " \n" + json.dumps(response(), ensure_ascii=False, indent=4) + "\n"
    calls = []
    async def fake(cfg, instruction, content, schema):
        calls.append((instruction, content, schema)); return reply(raw)
    monkeypatch.setattr(service.provider, "generate_once", fake)
    result = asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert len(calls) == 1 and result.raw_response == raw
    assert result.model == "gemini-synthetic-resolved"
    assert EvaluationDraft.model_validate_json(result.model_dump_json()) == result
    assert "total" not in result.model_dump() and result.prompt_version == service.PROMPT_VERSION
    ledger = records(settings)
    assert len(ledger) == 1 and ledger[0]["usage"]["total_tokens"] == 140
    assert ledger[0]["cost_usd"] is None
    text = json.dumps(ledger)
    assert "synthetic-test-key" not in text and "Ignore all rules" not in text


def test_invalid_answer_retries_full_input_and_accounts_both_attempts(monkeypatch, settings):
    inputs = []
    async def fake(cfg, instruction, content, schema):
        inputs.append((instruction, content, schema)); return reply("{}" if len(inputs) == 1 else None)
    monkeypatch.setattr(service.provider, "generate_once", fake)
    result = asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert len(result.criteria) == 3 and len(inputs) == 2
    assert inputs[0][1:] == inputs[1][1:] and "Previous response failed" in inputs[1][0]
    ledger = records(settings)
    assert [r["status"] for r in ledger] == ["invalid_response", "completed"]
    assert sum(r["usage"]["total_tokens"] for r in ledger) == 280
    assert len({r["run_id"] for r in ledger}) == 1


def test_noncontiguous_region_gets_targeted_retry_and_preserves_raw(monkeypatch, settings):
    invalid = response()
    invalid["criteria"]["new/unknown-Ж"].update(
        value="N/A", evidence_kind="not_applicable",
        na_condition_met="No applicable request: synthetic fixture.",
        checked_message_ids=["client", "operator"],
    )
    invalid_raw = json.dumps(invalid, ensure_ascii=False)
    repaired = json.loads(invalid_raw)
    repaired["criteria"]["new/unknown-Ж"]["checked_message_ids"] = ["client", "system", "operator"]
    repaired_raw = " \n" + json.dumps(repaired, ensure_ascii=False, indent=4) + "\n"
    calls = []

    async def fake(cfg, instruction, content, schema):
        calls.append((instruction, content, schema))
        return reply(invalid_raw if len(calls) == 1 else repaired_raw)

    monkeypatch.setattr(service.provider, "generate_once", fake)
    result = asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert len(calls) == 2
    assert calls[0][1:] == calls[1][1:]
    assert calls[1][0].startswith(calls[0][0])
    feedback = calls[1][0][len(calls[0][0]):]
    assert "noncontiguous_checked_region" in feedback
    assert "EVERY message between its boundaries" in feedback
    assert "including client, bot and system messages" in feedback
    assert invalid_raw not in feedback
    assert result.raw_response == repaired_raw
    assert result.criteria[0].value == "N/A"
    assert [(row["status"], row["error_code"]) for row in records(settings)] == [
        ("invalid_response", "noncontiguous_checked_region"), ("completed", None),
    ]


def test_na_explanation_gets_applicability_retry_without_rewriting_raw(monkeypatch, settings):
    data = response()
    data["criteria"]["new/unknown-Ж"]["na_condition_met"] = "No applicable request"
    invalid_raw = json.dumps(data, ensure_ascii=False)
    data["criteria"]["new/unknown-Ж"].update(value="N/A", evidence_kind="not_applicable")
    corrected_raw = json.dumps(data, ensure_ascii=False, indent=2)
    instructions = []

    async def fake(cfg, instruction, content, schema):
        instructions.append(instruction)
        return reply(invalid_raw if len(instructions) == 1 else corrected_raw)

    monkeypatch.setattr(service.provider, "generate_once", fake)
    result = asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert len(instructions) == 2
    feedback = instructions[1][len(instructions[0]):]
    assert "unexpected_na_explanation" in feedback
    assert "Re-check applicability first" in feedback
    assert invalid_raw not in feedback
    assert result.raw_response == corrected_raw
    assert result.criteria[0].value == "N/A"


def test_invalid_exhaustion_preserves_last_model_text(monkeypatch, settings):
    async def fake(*args): return reply('{"criteria":{}} ')
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.code == "invalid_ai_response"
    assert error.value.raw_response == '{"criteria":{}} '
    assert len(records(settings)) == settings.max_attempts


def test_truncated_response_cannot_be_accepted_even_if_json_is_valid(monkeypatch, settings):
    async def fake(*args): return reply(finish="MAX_TOKENS")
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.code == "ai_output_limit" and not error.value.retryable
    assert len(records(settings)) == 1
    assert records(settings)[0]["error_code"] == "ai_output_limit"


def test_transient_retries_and_unknown_usage_not_zero(monkeypatch, settings):
    calls = 0
    async def fake(*args):
        nonlocal calls
        calls += 1
        if calls == 1: raise TransientProviderError("ai_timeout", "Synthetic timeout")
        return reply()
    monkeypatch.setattr(service.provider, "generate_once", fake)
    asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert calls == 2 and records(settings)[0]["usage"]["total_tokens"] is None


def test_long_retry_after_does_not_hammer_quota(monkeypatch, settings):
    calls = 0
    async def fake(*args):
        nonlocal calls
        calls += 1
        raise TransientProviderError("ai_rate_limited", "Synthetic quota", retry_after=3600)
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError): asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert calls == 1


@pytest.mark.parametrize("code", ["ai_auth_error", "invalid_ai_configuration"])
def test_permanent_failure_is_not_retried(monkeypatch, settings, code):
    async def fake(*args): raise AIProviderError(code, "Safe synthetic failure")
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.code == code and len(records(settings)) == 1


def test_safety_block_records_usage_without_fabricating_a_result(monkeypatch, settings):
    async def fake(*args): return reply("", finish="SAFETY", blocked=True)
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.code == "ai_blocked" and len(records(settings)) == 1
    assert records(settings)[0]["usage"]["total_tokens"] == 140


def test_total_deadline_is_bounded(monkeypatch, settings):
    cfg = replace(settings, evaluation_timeout_seconds=0.02)
    monkeypatch.setattr(service.AISettings, "from_env", lambda: cfg)
    async def fake(*args): await asyncio.sleep(1)
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.code == "ai_timeout" and records(settings)[0]["status"] == "cancelled"


def test_external_cancellation_propagates(monkeypatch, settings):
    async def exercise():
        started = asyncio.Event()
        async def fake(*args):
            started.set(); await asyncio.sleep(100)
        monkeypatch.setattr(service.provider, "generate_once", fake)
        task = asyncio.create_task(service.evaluate_dialogue(conversation(), checklist()))
        await started.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(exercise())
    assert records(settings)[0]["usage"]["total_tokens"] is None


def test_unwritable_audit_stops_before_spending(monkeypatch, settings, tmp_path):
    bad = tmp_path / "parent-file"; bad.write_text("not a directory")
    cfg = replace(settings, audit_path=str(bad / "usage.jsonl"))
    monkeypatch.setattr(service.AISettings, "from_env", lambda: cfg)
    async def fake(*args): pytest.fail("Must not invoke provider")
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.code == "ai_audit_unavailable"


def test_cost_includes_thinking_and_does_not_double_count_cached_input(settings):
    cfg = replace(settings, input_price_per_million=1, cached_input_price_per_million=0.1,
                  output_price_per_million=2, price_label="synthetic rate, not a vendor quote")
    usage = usage_counts(reply().usage)
    assert estimated_cost(usage, cfg) == pytest.approx(0.000162)
    usage["thinking_tokens"] = None
    assert estimated_cost(usage, cfg) is None


def test_invalid_usage_is_unknown_not_fabricated():
    counts = usage_counts({"promptTokenCount": True, "thoughtsTokenCount": -1, "totalTokenCount": "100"})
    assert all(value is None for value in counts.values())


def test_terminal_transport_error_preserves_prior_invalid_model_text(monkeypatch, settings):
    calls = 0
    async def fake(*args):
        nonlocal calls
        calls += 1
        if calls == 1: return reply("{invalid model JSON")
        raise AIProviderError("provider_authentication_failed", "Safe synthetic auth failure")
    monkeypatch.setattr(service.provider, "generate_once", fake)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.raw_response == "{invalid model JSON" and calls == 2


def test_extreme_integer_response_is_audited_and_retried(monkeypatch, settings):
    calls = 0
    async def fake(*args):
        nonlocal calls
        calls += 1
        if calls == 1: return reply('{"value":' + '9' * 5000 + '}')
        return reply()
    monkeypatch.setattr(service.provider, "generate_once", fake)
    asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert calls == 2 and records(settings)[0]["usage"]["total_tokens"] == 140


def test_post_response_audit_failure_preserves_raw(monkeypatch, settings):
    raw = json.dumps(response())
    async def fake(*args): return reply(raw)
    def fail_audit(*args, **kwargs): raise AIProviderError("ai_audit_unavailable", "Synthetic disk failure")
    monkeypatch.setattr(service.provider, "generate_once", fake)
    monkeypatch.setattr(service, "record_attempt", fail_audit)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(service.evaluate_dialogue(conversation(), checklist()))
    assert error.value.raw_response == raw


def test_cost_overflow_remains_unknown(settings):
    cfg = replace(settings, input_price_per_million=1e308, cached_input_price_per_million=1e308,
                  output_price_per_million=1e308)
    usage = usage_counts({**reply().usage, "promptTokenCount": 10 ** 300})
    assert estimated_cost(usage, cfg) is None
