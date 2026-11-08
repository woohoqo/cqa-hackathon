"""Source-aware privacy checks use only explicitly synthetic names and identifiers."""
from copy import deepcopy
import json

import pytest

from qa.review_source_privacy import review_source_privacy


def inputs(source_text, rationale):
    return ({"results": [{"conversation_id": "synthetic", "criteria": [{"rationale": rationale}]}]},
            {"data": {"records": [{"conversation_id": "synthetic", "transcript": [
                {"speaker": "client", "text": source_text}]}]}})


@pytest.mark.parametrize("source,rationale,kind", [
    ("SYNTHETIC Меня зовут Пример Тестов.", "Оператор назвал клиента Пример Тестов.", "explicit_person_name"),
    ("SYNTHETIC My name is Example Tester.", "Addressed Example Tester directly.", "explicit_person_name"),
    ("SYNTHETIC Менің атым Мысал Сынақ.", "Мысал Сынақ деп атады.", "explicit_person_name"),
    ("SYNTHETIC номер счёта: TEST123456", "Повторён TEST123456.", "explicit_account_or_identity_number"),
    ("SYNTHETIC ИИН: 000000000000", "Повторён 000000000000.", "explicit_account_or_identity_number"),
    ("SYNTHETIC home address: Example Street 123", "Copied Example Street 123.", "explicit_home_address"),
])
def test_concrete_source_identity_repetition_flagged_without_exposing_value(source, rationale, kind):
    data = inputs(source, rationale)
    before = deepcopy(data)
    report = review_source_privacy(*data)
    assert report["review_completed"] and report["scanned_fields"] == 1
    assert any(flag["kind"] == kind and flag["source_turn"] == 1 for flag in report["flags"])
    assert source not in json.dumps(report) and rationale not in json.dumps(report)
    assert data == before


def test_generic_long_quote_is_counted_not_promoted_to_personal_data_finding():
    text = "Оператор предложил помощь и объяснил правила подключения брокерского счёта."
    report = review_source_privacy(*inputs(text, f"SYNTHETIC «{text}»"))
    assert report["review_completed"] and report["long_source_overlaps"] == 1
    assert report["flags"] == []


def test_quoted_long_personal_fragment_has_source_position_but_no_text():
    text = "SYNTHETIC Меня зовут Пример Тестов и я хочу узнать статус заявки."
    report = review_source_privacy(*inputs(text, f"Оператор повторил: «{text}»"))
    assert report["flags"][0]["long_verbatim_overlap"] is True
    assert set(report["flags"][0]) == {"path", "source_turn", "kind", "source_role", "long_verbatim_overlap"}
    assert report["flags"][0]["source_role"] == "client"


def test_identity_in_source_but_absent_from_rationale_is_not_flagged():
    report = review_source_privacy(*inputs("SYNTHETIC My name is Example Tester.", "The agent addressed the customer politely."))
    assert report["source_identity_candidates"] == 1 and report["flags"] == []


@pytest.mark.parametrize("text", [
    "SYNTHETIC I am Unable to access my account.",
    "SYNTHETIC account number unknown",
    "SYNTHETIC my home address is private",
    "SYNTHETIC Freedom Broker Privacy Policy",
])
def test_generic_policy_or_missing_data_not_flagged(text):
    assert review_source_privacy(*inputs(text, text))["flags"] == []


def test_other_dialogue_source_is_never_used_as_evidence():
    payload, source = inputs("SYNTHETIC My name is Example Tester.", "Example Tester")
    payload["results"][0]["conversation_id"] = "not-in-source"
    report = review_source_privacy(payload, source)
    assert not report["review_completed"] and report["flags"] == []
