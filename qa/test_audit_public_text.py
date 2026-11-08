"""Only invented public-text examples; no real contact details or credentials."""
from copy import deepcopy
import json

import pytest

from qa.audit_public_text import scan_public_text


def payload(text):
    return {"results": [{"criteria": [{"rationale": text}], "error": "SYNTHETIC processing error"}]}


@pytest.mark.parametrize("text,rule", [
    ("SYNTHETIC user@example.invalid", "email"),
    ("SYNTHETIC https://example.invalid/path?sig=invented", "url"),
    ("SYNTHETIC https://example.invalid/path?sig=invented", "credential_marker"),
    ("SYNTHETIC +7 (000) 000-00-00", "phone_or_long_identifier"),
    ("SYNTHETIC KZ00 0000 0000 0000 0000", "iban"),
    ("SYNTHETIC 4111 1111 1111 1111", "possible_payment_card"),
    ("SYNTHETIC пароль: invented-only", "credential_marker"),
    ("SYNTHETIC api_key=invented-only", "credential_marker"),
    ("SYNTHETIC -----BEGIN PRIVATE KEY-----", "credential_shape"),
    ("SYNTHETIC " + "AIza" + "x" * 35, "credential_shape"),
])
def test_sensitive_pattern_flagged_without_exposing_match(text, rule):
    data = payload(text)
    before = deepcopy(data)
    report = scan_public_text(data)
    assert report["scan_completed"]
    assert {"path": "results[0].criteria[0].rationale", "rule": rule} in report["flags"]
    assert text not in json.dumps(report)
    assert "example.invalid" not in json.dumps(report)
    assert data == before


def test_ordinary_rubric_numbers_and_multilingual_text_not_flagged():
    text = "SYNTHETIC Итог 85,38. Критерий 6: 7/10, вес 15. Деректер жеткілікті. No clarification needed."
    report = scan_public_text(payload(text))
    assert report["scan_completed"] and report["scanned_fields"] == 2
    assert report["flags"] == []


def test_non_luhn_long_number_is_not_asserted_to_be_card():
    report = scan_public_text(payload("SYNTHETIC 4111 1111 1111 1112"))
    assert all(flag["rule"] != "possible_payment_card" for flag in report["flags"])


def test_error_text_is_scanned_and_malformed_json_cannot_pass():
    data = {"results": [{"criteria": [], "error": "SYNTHETIC token=https://example.invalid"}]}
    report = scan_public_text(data)
    assert report["flags"] and all(flag["path"] == "results[0].error" for flag in report["flags"])
    assert not scan_public_text(b'{"results":[],"results":[]}')["scan_completed"]
