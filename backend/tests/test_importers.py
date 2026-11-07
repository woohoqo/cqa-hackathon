"""Synthetic organizer envelopes; never embed signed API URLs or real records."""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.app.errors import AppError
from backend.app.importers import organizer_payload, organizer_url


def payload():
    return {"isSuccess": True, "error": "", "data": {"dataset_group_id": "DEV", "total_records": 1,
        "records": [{"conversation_id": "synthetic", "channel": "voice call", "language": "KZ",
                     "audio_url": "https://example.invalid/synthetic-audio",
                     "transcript": [{"speaker": "system", "text": "  synthetic\ntext  ", "timestamp": "2026-11-07T12:01:00+05:00"}]}]}}


def test_preserve_original_source_values():
    result = organizer_payload(payload(), "DEV")[0]
    assert result.messages[0].role == "system"
    assert result.messages[0].text == "  synthetic\ntext  "
    assert result.messages[0].timestamp == "2026-11-07T12:01:00+05:00"
    assert result.metadata["language"] == "KZ"
    assert result.metadata["audio_url"] == "https://example.invalid/synthetic-audio"
    assert result.source == "organizer:DEV"


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(isSuccess=False),
    lambda p: p["data"].update(dataset_group_id="WRONG"),
    lambda p: p["data"].update(total_records=2),
    lambda p: p["data"]["records"][0].update(transcript="not an array"),
    lambda p: p["data"]["records"][0]["transcript"][0].update(text=None),
])
def test_reject_malformed_envelope_without_reflecting_input(mutation):
    data = payload()
    mutation(data)
    with pytest.raises(AppError) as exc:
        organizer_payload(data, "DEV")
    assert exc.value.code == "invalid_dev_payload"
    assert "synthetic" not in exc.value.message


def test_control_group_replaces_only_group_parameter():
    # Not a credential: reserved example.invalid and a clearly fake signature.
    original = "https://example.invalid/api?sig=SYNTHETIC%2b%2F%3D&group_id=DEV&api-version=1"
    changed = organizer_url(original, "CONTROL_2")
    assert "sig=SYNTHETIC%2b%2F%3D" in changed
    assert parse_qs(urlsplit(changed).query)["group_id"] == ["CONTROL_2"]
    assert urlsplit(changed).hostname == "example.invalid"


def test_missing_configuration_is_explicit():
    with pytest.raises(AppError) as exc:
        organizer_url("", "DEV")
    assert exc.value.code == "dev_not_configured"


def test_reject_duplicate_organizer_ids():
    data = payload()
    data["data"]["records"].append(deepcopy(data["data"]["records"][0]))
    data["data"]["total_records"] = 2
    with pytest.raises(AppError):
        organizer_payload(data, "DEV")
