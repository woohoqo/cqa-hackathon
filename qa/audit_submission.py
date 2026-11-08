"""Independent, read-only audit of an official file against private source and AI.

No builder/calculator reuse, network calls, environment reads, or output generation.
The CLI writes only a diagnostic report; source transcripts/raw responses stay private.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
from pathlib import Path

IDS = ("contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical")
MAPPING = dict(zip((str(i) for i in range(1, 9)), IDS))
WEIGHTS = dict(zip(IDS, (10, 15, 20, 20, 15, 15, 5, 0)))
NULLABLE = {"discovery", "identification", "consultation", "closure"}
STEPS = {"consultation": (0, 5, 10, 15, 20), "dialogue": (0, 4, 7, 10)}


def strict_json(value):
    """A raw UTF-8 file permits duplicate-key detection before information is lost."""
    if type(value) not in (bytes, str):
        return value
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = item
        return result
    def reject_constant(_):
        raise ValueError("Nonfinite JSON constant")
    return json.loads(value.decode("utf-8") if type(value) is bytes else value,
                      object_pairs_hook=object_pairs, parse_constant=reject_constant)


def _text(value):
    if type(value) is not str or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _value(value, criterion):
    if value is None:
        return criterion in NULLABLE
    if criterion in STEPS:
        return type(value) in (int, float) and value in STEPS[criterion]
    return type(value) is bool


def _total(values):
    # Appendix arithmetic, independently implemented using exact decimal steps.
    with localcontext() as context:
        context.prec = 50
        applicable, earned = Decimal(0), Decimal(0)
        for cid in IDS:
            value, weight = values[cid], Decimal(WEIGHTS[cid])
            if value is None or not weight:
                continue
            applicable += weight
            earned += (Decimal(str(value)) / max(STEPS[cid]) * weight if cid in STEPS
                       else weight if value is True else Decimal(0))
        return None if not applicable else float((earned * 100 / applicable).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def audit_submission(payload, source, archive, *, dataset_kind="dev", team_name="cuda cuda"):
    """Validate exact official JSON, complete original IDs, and original AI provenance.

    ``source`` is the unmodified organizer envelope {isSuccess,data:{records,...}}.
    ``archive`` contains saved conversations and selected evaluations (or batch.evaluations).
    All outputs are diagnostic codes/IDs/counts, never submitted text or raw responses.
    """
    report = {"valid": False, "dataset_kind": dataset_kind, "errors": [], "warnings": [],
              "counts": {"source": 0, "results": 0, "ok": 0, "error": 0},
              "checks": {"original_ai_only": False, "full_transcript_positions": False,
                         "exact_source_ids": False, "independent_score_arithmetic": False}}

    def issue(code, path, cid=None, *, warning=False):
        entry = {"code": code, "path": path}
        if cid is not None:
            entry["conversation_id"] = cid
        report["warnings" if warning else "errors"].append(entry)

    def require(condition, code, path, cid=None):
        if not condition:
            issue(code, path, cid)
        return bool(condition)

    def finish():
        report["valid"] = not report["errors"]
        if report["valid"]:
            report["checks"] = dict.fromkeys(report["checks"], True)
        return report

    try:
        payload, source, archive = map(strict_json, (payload, source, archive))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        issue("invalid_utf8_json", "inputs")
        return finish()
    if not require(dataset_kind in ("dev", "prod", "test"), "invalid_dataset_kind", "dataset_kind"):
        return finish()
    if not require(type(source) is dict and source.get("isSuccess") is True and type(source.get("data")) is dict,
                   "invalid_source_envelope", "source"):
        return finish()
    data = source["data"]
    records = data.get("records")
    if not require(type(records) is list and bool(records), "invalid_source_records", "source.data.records"):
        return finish()
    report["counts"]["source"] = len(records)
    require(type(data.get("total_records")) is int and data["total_records"] == len(records),
            "source_total_mismatch", "source.data.total_records")
    require(_text(data.get("dataset_group_id")), "missing_source_group", "source.data.dataset_group_id")
    if dataset_kind == "dev":
        require(data.get("dataset_group_id") == "DEV", "dev_group_mismatch", "source.data.dataset_group_id")
    if dataset_kind in ("dev", "prod"):
        require(len(records) == {"dev": 65, "prod": 10}[dataset_kind], "official_source_count", "source.data.records")
    source_by_id = {}
    for index, row in enumerate(records):
        path = f"source.data.records[{index}]"
        if not require(type(row) is dict and _text(row.get("conversation_id")) and type(row.get("transcript")) is list
                       and bool(row["transcript"]), "invalid_source_record", path):
            continue
        cid = row["conversation_id"]
        require(cid not in source_by_id, "duplicate_source_id", path, cid)
        source_by_id[cid] = row
    if dataset_kind == "dev":
        channels = Counter(row.get("channel") if type(row.get("channel")) is str else "invalid"
                           for row in records if type(row) is dict)
        require(channels == {"agent chat": 60, "voice call": 5}, "dev_channel_mix", "source.data.records")
    if report["errors"]:
        return finish()

    if not require(type(archive) is dict and type(archive.get("conversations")) is list,
                   "invalid_archive", "archive"):
        return finish()
    evaluations = archive.get("evaluations", archive.get("batch", {}).get("evaluations") if type(archive.get("batch")) is dict else None)
    conversations = archive["conversations"]
    if not require(type(evaluations) is list, "invalid_selected_evaluations", "archive.evaluations"):
        return finish()
    require(len(conversations) == len(evaluations) == len(records), "archive_count_mismatch", "archive")
    stored_by_internal, stored_by_external = {}, {}
    for index, row in enumerate(conversations):
        path = f"archive.conversations[{index}]"
        if not require(type(row) is dict and _text(row.get("id")) and _text(row.get("external_id"))
                       and type(row.get("messages")) is list, "invalid_stored_conversation", path):
            continue
        cid = row["external_id"]
        if not require(cid in source_by_id, "stored_source_id_unknown", path, cid):
            continue
        require(row["id"] not in stored_by_internal and cid not in stored_by_external, "duplicate_stored_conversation", path, cid)
        stored_by_internal[row["id"]], stored_by_external[cid] = row, row
        original = source_by_id[cid]["transcript"]
        messages = row["messages"]
        same = len(messages) == len(original)
        if same:
            same = all(type(a) is dict and {"speaker", "text", "timestamp"} <= set(a)
                       and type(a["speaker"]) is str and type(a["text"]) is str
                       and (a["timestamp"] is None or type(a["timestamp"]) is str)
                       and type(b) is dict and _text(b.get("id"))
                       and a.get("speaker") == b.get("role") and a.get("text") == b.get("text")
                       and a.get("timestamp") == b.get("timestamp") for a, b in zip(original, messages))
        require(same, "source_transcript_changed", path + ".messages", cid)
        if all(type(m) is dict and _text(m.get("id")) for m in messages):
            require(len({m["id"] for m in messages}) == len(messages), "duplicate_message_id", path + ".messages", cid)
    require(set(stored_by_external) == set(source_by_id), "archive_source_ids", "archive.conversations")
    selected = {}
    batch_ids = set()
    for index, row in enumerate(evaluations):
        path = f"archive.evaluations[{index}]"
        if not require(type(row) is dict and _text(row.get("conversation_id")), "invalid_evaluation", path):
            continue
        stored = stored_by_internal.get(row["conversation_id"])
        if not require(stored is not None, "evaluation_outside_source", path):
            continue
        cid = stored["external_id"]
        require(cid not in selected, "duplicate_selected_evaluation", path, cid)
        selected[cid] = row
        if _text(row.get("batch_id")):
            batch_ids.add(row["batch_id"])
        else:
            issue("missing_run_id", path, cid)
    require(len(batch_ids) == 1, "mixed_selected_runs", "archive.evaluations")
    require(set(selected) == set(source_by_id), "selected_evaluation_coverage", "archive.evaluations")
    if report["errors"]:
        return finish()

    if not require(type(payload) is dict and set(payload) == {"schema_version", "team_name", "model", "results"},
                   "official_root_fields", "payload"):
        return finish()
    require(payload["schema_version"] == "1.0", "schema_version", "schema_version")
    require(_text(team_name) and payload["team_name"] == team_name, "team_name_mismatch", "team_name")
    model = payload["model"]
    require(type(model) is dict and set(model) == {"mode", "name"} and model.get("mode") in ("local", "external_api", "hybrid")
            and _text(model.get("name")), "invalid_model", "model")
    rows = payload["results"]
    if not require(type(rows) is list, "invalid_results", "results"):
        return finish()
    report["counts"]["results"] = len(rows)
    require(len(rows) == len(records), "result_count_mismatch", "results")
    found, models = set(), {}

    def model_record(name, provider, cid):
        mode = {"gemini": "external_api", "external_api": "external_api", "local": "local", "hybrid": "hybrid"}.get(provider) if type(provider) is str else None
        if require(_text(name) and mode is not None, "unknown_actual_model", "model", cid):
            if name in models:
                require(models[name] == mode, "conflicting_model_mode", "model", cid)
            models[name] = mode

    for index, row in enumerate(rows):
        path = f"results[{index}]"
        if not require(type(row) is dict and _text(row.get("conversation_id")), "invalid_result", path):
            continue
        cid = row["conversation_id"]
        if not require(cid in source_by_id, "extra_result_id", path, cid):
            continue
        require(cid not in found, "duplicate_result_id", path, cid)
        found.add(cid)
        evaluation = selected[cid]
        draft = evaluation.get("ai_draft")
        execution = evaluation.get("ai_execution") or (draft.get("execution") if type(draft) is dict else None)
        if type(draft) is dict:
            names = draft.get("models_used") or [draft.get("model")]
            if require(type(names) is list and draft.get("model") in names, "invalid_original_model_names", path, cid):
                for name in names:
                    model_record(name, draft.get("provider"), cid)
        if type(execution) is dict:
            attempts = execution.get("attempts", [])
            if not require(type(attempts) is list, "invalid_execution_attempts", path, cid):
                attempts = []
            for attempt in attempts:
                if type(attempt) is dict and attempt.get("network_attempted") is True:
                    model_record(attempt.get("model"), attempt.get("provider", "gemini"), cid)
        if evaluation.get("status") not in ("completed", "failed"):
            issue("unfinished_evaluation", path, cid)
            continue
        # Verify frozen rubric meaning as well as numerical definitions.
        from backend.app.default_checklist import official_checklist
        snapshot = evaluation.get("checklist_snapshot")
        require(type(snapshot) is dict and {k: v for k, v in snapshot.items() if k not in {"id", "version", "created_at"}}
                == official_checklist().model_dump(mode="json"), "nonofficial_checklist", path, cid)
        if evaluation["status"] == "failed":
            report["counts"]["error"] += 1
            error = evaluation.get("error")
            if not require(type(error) is dict, "invalid_stored_error", path, cid):
                error = {}
            require(set(row) == {"conversation_id", "status", "criteria", "score", "error"}
                    and row.get("status") == "error" and row.get("criteria") == [] and row.get("score") is None
                    and _text(row.get("error")) and row.get("error") == error.get("message"),
                    "error_result_mismatch", path, cid)
            issue("retained_processing_error", path, cid, warning=True)
            if draft is None and execution is None and error.get("code") not in {"provider_not_configured", "invalid_ai_configuration"}:
                issue("missing_failed_model_provenance", path, cid)
            continue
        report["counts"]["ok"] += 1
        require(set(row) == {"conversation_id", "status", "criteria", "score"} and row.get("status") == "ok", "success_fields", path, cid)
        if not require(type(draft) is dict and type(draft.get("criteria")) is list and _text(draft.get("raw_response")),
                       "missing_original_ai", path, cid):
            continue
        require(evaluation.get("raw_response") == draft["raw_response"], "raw_response_not_preserved", path, cid)
        original = draft["criteria"]
        if not require(len(original) == 8 and all(type(item) is dict and type(item.get("criterion_id")) is str for item in original),
                       "original_criterion_coverage", path, cid):
            continue
        ai = {item["criterion_id"]: item for item in original}
        require(len(ai) == 8 and set(ai) == set(MAPPING), "original_criterion_coverage", path, cid)
        criteria = row.get("criteria")
        if not require(type(criteria) is list and len(criteria) == 8, "official_criterion_count", path, cid):
            continue
        values, seen = {}, set()
        turns = {message["id"]: turn for turn, message in enumerate(stored_by_external[cid]["messages"], 1)}
        for criterion in criteria:
            if not require(type(criterion) is dict and type(criterion.get("criterion_id")) is str and criterion["criterion_id"] in IDS,
                           "invalid_official_criterion", path, cid):
                continue
            code = criterion["criterion_id"]
            require(code not in seen, "duplicate_official_criterion", path, cid)
            seen.add(code)
            require(set(criterion) == {"criterion_id", "value", "rationale", "evidence_turns"}, "criterion_fields", path, cid)
            value = criterion.get("value")
            if _value(value, code):
                values[code] = value
            else:
                issue("invalid_official_value", path, cid)
            require(_text(criterion.get("rationale")), "missing_rationale", path, cid)
            evidence = criterion.get("evidence_turns")
            require(type(evidence) is list and bool(evidence) and all(type(t) is int and 1 <= t <= len(turns) for t in evidence),
                    "invalid_evidence_turns", path, cid)
            internal = next(internal for internal, official in MAPPING.items() if official == code)
            result = ai.get(internal)
            if result is not None:
                expected_value = None if result.get("value") == "N/A" else result.get("value")
                # Reject numeric booleans even though Python True == 1.
                require(_value(expected_value, code) and (type(expected_value) is not bool or type(value) is bool),
                        "invalid_original_value", path, cid)
                require(value == expected_value and criterion.get("rationale") == result.get("reasoning"), "not_original_ai_result", path, cid)
                refs = result.get("evidence_message_ids")
                if require(type(refs) is list and bool(refs) and all(type(ref) is str and ref in turns for ref in refs),
                           "invalid_original_evidence", path, cid):
                    require(evidence == [turns[ref] for ref in refs], "not_original_full_transcript_evidence", path, cid)
        require(seen == set(IDS), "official_criterion_coverage", path, cid)
        score = row.get("score")
        if not require(type(score) is dict and set(score) == {"final_score", "critical_flag"}, "score_fields", path, cid):
            continue
        if set(values) == set(IDS):
            calculated = _total(values)
            total = score.get("final_score")
            require(type(total) in (int, float) and 0 <= total <= 100 and math.isfinite(total)
                    and total == calculated, "independent_score_mismatch", path, cid)
            require(type(score.get("critical_flag")) is bool and score["critical_flag"] is values["critical"], "critical_flag_mismatch", path, cid)
            original_score = evaluation.get("original_score")
            require(type(original_score) is dict and original_score.get("total") == calculated
                    and original_score.get("gross_error") is values["critical"], "original_score_mismatch", path, cid)
    require(found == set(source_by_id), "exact_result_id_set", "results")
    if require(bool(models), "missing_actual_models", "model"):
        modes = set(models.values())
        expected_model = {"mode": next(iter(modes)) if len(modes) == 1 else "hybrid", "name": ", ".join(sorted(models))}
        require(model == expected_model, "actual_model_mismatch", "model")
    return finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("source", type=Path, help="Private original organizer response")
    parser.add_argument("archive", type=Path, help="Private saved conversations and selected evaluations")
    parser.add_argument("--dataset-kind", choices=("dev", "prod", "test"), default="dev")
    parser.add_argument("--team-name", default="cuda cuda")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.resolve() in {path.resolve() for path in (args.results, args.source, args.archive)}:
        parser.exit(2, "Report must not overwrite any audit input.\n")
    try:
        blobs = [path.read_bytes() for path in (args.results, args.source, args.archive)]
    except OSError:
        parser.exit(2, "Audit input unavailable; no source or credentials printed.\n")
    report = audit_submission(*blobs, dataset_kind=args.dataset_kind, team_name=args.team_name)
    report["file_sha256"] = dict(zip(("results", "source", "archive"), (hashlib.sha256(blob).hexdigest() for blob in blobs)))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"valid": report["valid"], "counts": report["counts"], "error_count": len(report["errors"]),
                      "warning_count": len(report["warnings"]), "results_sha256": report["file_sha256"]["results"]}))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
