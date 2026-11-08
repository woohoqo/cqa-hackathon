"""Small synthetic unit cases only; never produce fake DEV/PROD submission files."""
from copy import deepcopy
import json

import pytest

from backend.app.default_checklist import official_checklist
from qa.audit_submission import _public_text, audit_submission


def synthetic_inputs():
    source = {"isSuccess": True, "data": {"dataset_group_id": "SYNTHETIC", "total_records": 1, "records": [{
        "conversation_id": "synthetic-source", "channel": "agent chat", "language": "RU",
        "transcript": [{"speaker": role, "text": f"SYNTHETIC text {i}", "timestamp": str(i)}
                       for i, role in enumerate(("system", "client", "bot", "operator", "client", "operator"), 1)],
    }]}}
    original = [True, "N/A", "N/A", 15.0, True, 7.0, True, False]
    names = ("contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical")
    criteria = [{"criterion_id": str(i), "value": value, "reasoning": "SYNTHETIC original AI explanation",
                 "evidence_message_ids": ["m1", "m3", "m4", "m6"]} for i, value in enumerate(original, 1)]
    conversation = {"id": "synthetic-internal", "external_id": "synthetic-source", "source": "synthetic-unit",
                    "messages": [{"id": f"m{i}", "role": message["speaker"], "text": message["text"], "timestamp": message["timestamp"]}
                                 for i, message in enumerate(source["data"]["records"][0]["transcript"], 1)]}
    raw = '  {"SYNTHETIC": "original model text"}\n'
    evaluation = {"id": "synthetic-evaluation", "batch_id": "synthetic-run", "conversation_id": "synthetic-internal",
                  "status": "completed", "checklist_snapshot": {**official_checklist().model_dump(mode="json"), "id": "synthetic-checklist", "version": 1},
                  "raw_response": raw, "ai_draft": {"criteria": criteria, "raw_response": raw, "model": "synthetic-model", "provider": "gemini"},
                  "original_score": {"total": 85.38, "gross_error": False},
                  "final_criteria": deepcopy(criteria), "final_score": {"total": 85.38, "gross_error": False}}
    payload = {"schema_version": "1.0", "team_name": "cuda cuda", "model": {"mode": "external_api", "name": "synthetic-model"},
               "results": [{"conversation_id": "synthetic-source", "status": "ok", "criteria": [
                   {"criterion_id": name, "value": None if value == "N/A" else value, "rationale": "SYNTHETIC original AI explanation",
                    "evidence_turns": [1, 3, 4, 6]} for name, value in zip(names, original)],
                   "score": {"final_score": 85.38, "critical_flag": False}}]}
    return payload, source, {"conversations": [conversation], "evaluations": [evaluation]}


def audit(inputs, **kwargs):
    return audit_submission(*inputs, dataset_kind=kwargs.pop("dataset_kind", "test"), **kwargs)


def codes(report):
    return {item["code"] for item in report["errors"]}


def test_independent_pdf_8538_and_all_role_evidence_with_no_mutation():
    inputs = synthetic_inputs()
    before = deepcopy(inputs)
    report = audit(inputs)
    assert report["valid"] and all(report["checks"].values())
    assert report["counts"] == {"source": 1, "results": 1, "ok": 1, "error": 0}
    assert inputs == before
    assert "SYNTHETIC text" not in json.dumps(report)
    assert "original model text" not in json.dumps(report)


def test_human_appeal_is_not_substituted_for_original_ai():
    inputs = synthetic_inputs()
    evaluation = inputs[2]["evaluations"][0]
    evaluation["final_criteria"][0].update(value=False, reasoning="SYNTHETIC human correction")
    evaluation["final_score"].update(total=70.0)
    assert audit(inputs)["valid"]  # Original output remains suitable even after review.
    inputs[0]["results"][0]["criteria"][0].update(value=False, rationale="SYNTHETIC human correction")
    inputs[0]["results"][0]["score"]["final_score"] = 70.0
    assert "not_original_ai_result" in codes(audit(inputs))


def test_human_final_cannot_fill_missing_original():
    inputs = synthetic_inputs()
    inputs[2]["evaluations"][0]["ai_draft"] = None
    assert "missing_original_ai" in codes(audit(inputs))


def test_critical_true_never_zeroes_score():
    inputs = synthetic_inputs()
    inputs[0]["results"][0]["criteria"][-1]["value"] = True
    inputs[0]["results"][0]["score"]["critical_flag"] = True
    evaluation = inputs[2]["evaluations"][0]
    evaluation["ai_draft"]["criteria"][-1]["value"] = True
    evaluation["original_score"]["gross_error"] = True
    assert audit(inputs)["valid"]
    inputs[0]["results"][0]["score"]["final_score"] = 0
    assert "independent_score_mismatch" in codes(audit(inputs))


@pytest.mark.parametrize("change,code", [
    (lambda p, s, a: p.update(schema_version="1"), "schema_version"),
    (lambda p, s, a: p.update(team_name="wrong-team"), "team_name_mismatch"),
    (lambda p, s, a: p.update(diagnostic="not-official"), "official_root_fields"),
    (lambda p, s, a: p["results"][0].update(conversation_id="synthetic-internal"), "extra_result_id"),
    (lambda p, s, a: p["results"].append(deepcopy(p["results"][0])), "duplicate_result_id"),
    (lambda p, s, a: p["results"].clear(), "exact_result_id_set"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(value=1), "invalid_official_value"),
    (lambda p, s, a: p["results"][0]["criteria"][3].update(value=True), "invalid_official_value"),
    (lambda p, s, a: p["results"][0]["criteria"][5].update(value=6), "invalid_official_value"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(value=None), "invalid_official_value"),
    (lambda p, s, a: p["results"][0]["criteria"][1].update(value="N/A"), "invalid_official_value"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(rationale=" "), "missing_rationale"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(evidence_turns=[1, 2]), "not_original_full_transcript_evidence"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(evidence_turns=[0]), "invalid_evidence_turns"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(evidence_turns=[True]), "invalid_evidence_turns"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(evidence_turns=[7]), "invalid_evidence_turns"),
    (lambda p, s, a: p["results"][0]["criteria"].pop(), "official_criterion_count"),
    (lambda p, s, a: p["results"][0]["criteria"][0].update(criterion_id="critical"), "duplicate_official_criterion"),
    (lambda p, s, a: p["results"][0]["score"].update(final_score=85.3846), "independent_score_mismatch"),
    (lambda p, s, a: p["results"][0]["score"].update(final_score=10**500), "independent_score_mismatch"),
    (lambda p, s, a: p["results"][0]["score"].update(critical_flag=0), "critical_flag_mismatch"),
    (lambda p, s, a: p["model"].update(name="configured-but-not-called"), "actual_model_mismatch"),
    (lambda p, s, a: a["evaluations"][0].update(raw_response="SYNTHETIC changed text"), "raw_response_not_preserved"),
    (lambda p, s, a: a["evaluations"][0]["original_score"].update(total=100), "original_score_mismatch"),
    (lambda p, s, a: a["evaluations"][0]["checklist_snapshot"]["criteria"][5].update(weight=10), "nonofficial_checklist"),
    (lambda p, s, a: a["evaluations"][0]["checklist_snapshot"]["criteria"][0].update(description="Changed meaning"), "nonofficial_checklist"),
    (lambda p, s, a: a["evaluations"][0].update(status="running"), "unfinished_evaluation"),
    (lambda p, s, a: a["conversations"][0]["messages"][0].update(role="operator"), "source_transcript_changed"),
    (lambda p, s, a: a["conversations"][0]["messages"][0].update(text="SYNTHETIC edited"), "source_transcript_changed"),
    (lambda p, s, a: a["conversations"][0]["messages"][0].update(timestamp="new"), "source_transcript_changed"),
    (lambda p, s, a: a["conversations"][0]["messages"].pop(2), "source_transcript_changed"),
    (lambda p, s, a: s["data"].update(total_records=2), "source_total_mismatch"),
])
def test_tampered_payload_or_source_cannot_pass(change, code):
    inputs = synthetic_inputs()
    change(*inputs)
    report = audit(inputs)
    assert not report["valid"] and code in codes(report)


@pytest.mark.parametrize("text", [b'\xff', '{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1,}'])
def test_raw_json_duplicate_keys_and_nonfinite_constants_rejected(text):
    inputs = list(synthetic_inputs())
    inputs[0] = text
    assert "invalid_utf8_json" in codes(audit(inputs))


def test_failed_record_is_preserved_with_warning_and_actual_attempt_model():
    payload, source, archive = synthetic_inputs()
    evaluation = archive["evaluations"][0]
    evaluation.update(status="failed", ai_draft=None, error={"code": "provider_unavailable", "message": "SYNTHETIC provider error"},
                      ai_execution={"attempts": [{"model": "synthetic-called", "provider": "gemini", "network_attempted": True}]})
    payload["model"]["name"] = "synthetic-called"
    payload["results"][0] = {"conversation_id": "synthetic-source", "status": "error", "criteria": [], "score": None,
                             "error": "SYNTHETIC provider error"}
    report = audit((payload, source, archive))
    assert report["valid"] and report["counts"]["error"] == 1
    assert report["warnings"][0]["code"] == "retained_processing_error"
    payload["results"][0]["score"] = {"final_score": 0, "critical_flag": False}
    assert "error_result_mismatch" in codes(audit((payload, source, archive)))


def test_nonnetwork_attempt_cannot_supply_fake_model_provenance():
    inputs = synthetic_inputs()
    inputs[2]["evaluations"][0]["ai_execution"] = {"attempts": [{"model": "never-called", "provider": "gemini", "network_attempted": False}]}
    assert audit(inputs)["valid"]


def test_all_actual_attempt_models_and_modes_are_required():
    inputs = synthetic_inputs()
    inputs[2]["evaluations"][0]["ai_execution"] = {"attempts": [
        {"model": "synthetic-fallback", "provider": "local", "network_attempted": True},
        {"model": "synthetic-model", "provider": "gemini", "network_attempted": True},
    ]}
    assert "actual_model_mismatch" in codes(audit(inputs))
    inputs[0]["model"] = {"mode": "hybrid", "name": "synthetic-fallback, synthetic-model"}
    assert audit(inputs)["valid"]


def test_malformed_attempt_metadata_reports_error_instead_of_crashing():
    inputs = synthetic_inputs()
    inputs[2]["evaluations"][0]["ai_execution"] = {"attempts": None}
    assert "invalid_execution_attempts" in codes(audit(inputs))


def test_cli_does_not_overwrite_audit_inputs(tmp_path, monkeypatch):
    from qa.audit_submission import main
    paths = [tmp_path / name for name in ("results.json", "source.json", "archive.json")]
    for path, value in zip(paths, synthetic_inputs()):
        path.write_text(json.dumps(value), encoding="utf-8")
    before = [path.read_bytes() for path in paths]
    monkeypatch.setattr("sys.argv", ["audit", *(str(path) for path in paths), "--report", str(paths[1])])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert before == [path.read_bytes() for path in paths]


@pytest.mark.parametrize("kind", ["dev", "prod"])
def test_small_synthetic_set_is_never_certified_as_official_dataset(kind):
    report = audit(synthetic_inputs(), dataset_kind=kind)
    assert not report["valid"] and "official_source_count" in codes(report)


def test_batch_archive_shape_and_utf8_file_are_supported():
    payload, source, archive = synthetic_inputs()
    archive["batch"] = {"evaluations": archive.pop("evaluations")}
    report = audit(tuple(json.dumps(item, ensure_ascii=False).encode("utf-8") for item in (payload, source, archive)))
    assert report["valid"]


def name_projection_inputs():
    data = synthetic_inputs()
    payload, source, archive = data
    text = "SYNTHETIC My name is Example Tester."
    source["data"]["records"][0]["transcript"][3]["text"] = text
    archive["conversations"][0]["messages"][3]["text"] = text
    archive["evaluations"][0]["ai_draft"]["criteria"][0]["reasoning"] = "SYNTHETIC The agent introduced EXAMPLE-Tester, then helped."
    payload["results"][0]["criteria"][0]["rationale"] = "SYNTHETIC The agent introduced [имя скрыто], then helped."
    return data


def test_only_exact_source_name_projection_is_allowed_without_mutating_original():
    inputs = name_projection_inputs()
    before = deepcopy(inputs)
    report = audit(inputs)
    assert report["valid"] and report["privacy"]["fields_transformed"] == 1
    assert inputs == before


@pytest.mark.parametrize("rationale", [
    "SYNTHETIC The agent introduced EXAMPLE-Tester, then helped.",  # Must remove confirmed personal name.
    "SYNTHETIC The agent introduced [имя скрыто], then did not help.",  # Semantic edit.
    "SYNTHETIC The agent [имя скрыто], then helped.",  # Over-broad removal.
    "SYNTHETIC The agent introduced [ДРУГОЙ МАРКЕР], then helped.",
])
def test_unmasked_or_arbitrarily_changed_rationale_fails(rationale):
    inputs = name_projection_inputs()
    inputs[0]["results"][0]["criteria"][0]["rationale"] = rationale
    assert "not_original_ai_result" in codes(audit(inputs))


def test_name_not_confirmed_by_same_source_cannot_be_redacted():
    inputs = name_projection_inputs()
    inputs[1]["data"]["records"][0]["transcript"][3]["text"] = "SYNTHETIC Generic support text."
    inputs[2]["conversations"][0]["messages"][3]["text"] = "SYNTHETIC Generic support text."
    assert "not_original_ai_result" in codes(audit(inputs))


def test_projection_preserves_whole_words_punctuation_and_longest_name():
    messages = [{"text": "SYNTHETIC My name is Example."}, {"text": "SYNTHETIC My name is Example Tester."}]
    original = "  EXAMPLE / Tester, Example! Examples unchanged.\n"
    assert _public_text(original, messages) == "  [имя скрыто], [имя скрыто]! Examples unchanged.\n"
    assert _public_text(original, []) == original


def test_projection_uses_deterministic_overlap_tie_and_excludes_generic_introduction():
    messages = [{"text": "SYNTHETIC My name is Aaa Bbb."}, {"text": "SYNTHETIC My name is Bbb Ccc."}]
    assert _public_text("Aaa Bbb Ccc", messages) == "[имя скрыто] Ccc"
    assert _public_text("Aaa Bbb Ccc", list(reversed(messages))) == "[имя скрыто] Ccc"
    assert _public_text("Unable to proceed", [{"text": "SYNTHETIC I am Unable to proceed."}]) == "Unable to proceed"


def test_failed_error_requires_the_same_narrow_projection():
    payload, source, archive = name_projection_inputs()
    evaluation = archive["evaluations"][0]
    evaluation.update(status="failed", ai_draft=None,
                      error={"code": "provider_unavailable", "message": "SYNTHETIC Failed for Example Tester."},
                      ai_execution={"attempts": [{"model": "synthetic-model", "provider": "gemini", "network_attempted": True}]})
    payload["results"][0] = {"conversation_id": "synthetic-source", "status": "error", "criteria": [], "score": None,
                             "error": "SYNTHETIC Failed for [имя скрыто]."}
    assert audit((payload, source, archive))["valid"]
    payload["results"][0]["error"] = "SYNTHETIC Different failure for [имя скрыто]."
    assert "error_result_mismatch" in codes(audit((payload, source, archive)))
