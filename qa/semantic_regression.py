"""Offline comparison of frozen synthetic cases; no API calls or secret/config reads.

An archive is {"conversations": [saved Conversation, ...], "evaluations":
[saved Evaluation, ...]}. Supply exactly one selected evaluation for every case.
This is a rubric-derived regression check, never organizer/holdout accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.app.contracts import Conversation, ConversationInput, Evaluation
from backend.app.default_checklist import official_checklist
from backend.app.scoring import calculate_score, validate_results

FIXTURE_DIR = Path(__file__).with_name("fixtures")
INPUT_PATH = FIXTURE_DIR / "na-regression-conversations-v2.json"
EXPECTATION_PATH = FIXTURE_DIR / "na-regression-expectations-v2.json"
CRITERIA = tuple(str(i) for i in range(1, 9))


class RegressionInputError(ValueError):
    """A safe structural error, without raw responses or arbitrary input values."""


def _require(condition, message):
    if not condition:
        raise RegressionInputError(message)


def _json_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_matrix():
    return (json.loads(INPUT_PATH.read_text(encoding="utf-8")),
            json.loads(EXPECTATION_PATH.read_text(encoding="utf-8")))


def validate_matrix(fixtures, expectations):
    """Require balanced languages/scenarios and explicit, rubric-valid assertions."""
    try:
        inputs = [ConversationInput.model_validate(item) for item in fixtures["conversations"]]
        cases = expectations["cases"]
        _require(expectations["synthetic"] is True, "Expectations must be explicitly synthetic.")
        scenarios = {"na-regression-v1": {"clear_from_start", "clarification_needed"},
                     "na-regression-v2": {"clear_from_start", "clarification_needed", "missing_details_ignored"}}.get(expectations["fixture_version"])
        _require(scenarios is not None, "Unknown frozen matrix version.")
        _require(len(inputs) == len(cases) == 3 * len(scenarios), "Frozen matrix case count is inconsistent.")
        by_id = {item.external_id: item for item in inputs}
        _require(len(by_id) == len(inputs) and None not in by_id, "Fixture source IDs must be unique and present.")
        case_ids = [item["conversation_id"] for item in cases]
        _require(len(set(case_ids)) == len(cases) and set(case_ids) == set(by_id), "Expectations must match the complete source ID set.")
        pairs = set()
        for case in cases:
            record = by_id[case["conversation_id"]]
            _require(record.metadata.get("synthetic") is True, "Every transcript must be labeled synthetic.")
            _require(case["language"] == record.metadata.get("language") and case["language"] in {"RU", "KZ", "EN"}, "Invalid or mismatched language.")
            _require(case["scenario"] in scenarios, "Unknown regression scenario.")
            pair = (case["language"], case["scenario"])
            _require(pair not in pairs, "Duplicate language/scenario pair.")
            pairs.add(pair)
            values = case["expected_criteria"]
            _require("2" in values and set(values) <= set(CRITERIA), "Expectations must cover the primary criterion without unknown IDs.")
            for criterion in official_checklist().criteria:
                if criterion.id not in values:
                    continue
                value = values[criterion.id]
                valid = ((value == "N/A" and criterion.allow_na)
                         or (criterion.answer_type == "scale" and type(value) in (int, float)
                             and value in {step.value for step in criterion.scale})
                         or (criterion.answer_type != "scale" and type(value) is bool))
                _require(valid, "An expectation violates the official type, scale or N/A rules.")
            primary = case["primary"]
            _require(primary["criterion_id"] == "2" and primary["value"] == values["2"], "Primary expectation must agree with criterion 2.")
            _require(isinstance(primary["rationale"], str) and bool(primary["rationale"].strip()), "Expected applicability requires a rationale.")
            expected_primary = {"clear_from_start": "N/A", "clarification_needed": True, "missing_details_ignored": False}[case["scenario"]]
            _require(type(values["2"]) is type(expected_primary) and values["2"] == expected_primary,
                     "The matrix must distinguish N/A from applicable clarification.")
        return by_id, {item["conversation_id"]: item for item in cases}
    except RegressionInputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RegressionInputError("Invalid synthetic fixture/expectation structure.") from None


def _messages(record):
    return [item.model_dump(mode="json") for item in record.messages]


def _snapshot(evaluation):
    return evaluation.checklist_snapshot.model_dump(mode="json", exclude={"id", "version", "created_at"})


def _summary(evaluation, case):
    draft = evaluation.ai_draft
    execution = evaluation.ai_execution or (draft.execution if draft else None)
    attempts = [item.model_dump(mode="json") for item in execution.attempts] if execution else []
    values = {item.criterion_id: item.value for item in evaluation.final_criteria or []}
    completed = evaluation.status == "completed"
    mismatches = [criterion for criterion in case["expected_criteria"]
                  if not completed or values.get(criterion) != case["expected_criteria"][criterion]]
    return {
        "evaluation_id": evaluation.id, "status": evaluation.status.value,
        "error_code": evaluation.error.code if evaluation.error else None,
        "criteria": values if completed else None,
        "score": evaluation.final_score.total if completed and evaluation.final_score else None,
        "critical_flag": evaluation.final_score.gross_error if completed and evaluation.final_score else None,
        "expected_criterion_mismatches": mismatches,
        "primary_matches": completed and values.get("2") == case["primary"]["value"],
        "models_used": (draft.models_used or [draft.model]) if draft else execution.models_used if execution else [],
        "model": draft.model if draft else None,
        "provider": draft.provider if draft else None,
        "prompt_version": draft.prompt_version if draft else None,
        "execution_run_id": execution.run_id if execution else None,
        "attempts": [{key: item.get(key) for key in ("attempt", "model", "status", "network_attempted", "error_code")}
                     for item in attempts],
        "total_tokens": execution.total_tokens if execution else None,
        "cost_usd": execution.cost_usd if execution else None,
    }


def summarize_run(archive, fixtures, expectations):
    """Validate the saved source/result boundary before comparing any values."""
    source, cases = validate_matrix(fixtures, expectations)
    try:
        conversations = [Conversation.model_validate(item) for item in archive["conversations"]]
        evaluations = [Evaluation.model_validate(item) for item in archive["evaluations"]]
        _require(len(conversations) == len(evaluations) == len(source), "Archive must contain one source and one selected evaluation per case.")
        by_internal = {item.id: item for item in conversations}
        by_external = {item.external_id: item for item in conversations}
        _require(len(by_internal) == len(conversations) and set(by_external) == set(source), "Archive source IDs must match the exact frozen matrix.")
        _require(len({item.conversation_id for item in evaluations}) == len(evaluations), "Duplicate selected evaluations are ambiguous.")
        expected_snapshot = official_checklist().model_dump(mode="json")
        results = {}
        for evaluation in evaluations:
            _require(evaluation.conversation_id in by_internal, "Evaluation refers to a source outside the archive.")
            record = by_internal[evaluation.conversation_id]
            _require(record.metadata.get("synthetic") is True, "Archive contains a transcript not labeled synthetic.")
            _require(_messages(record) == _messages(source[record.external_id]), "Archive transcript differs from the frozen input.")
            _require(record.metadata.get("language") == source[record.external_id].metadata.get("language")
                     and record.metadata.get("channel") == source[record.external_id].metadata.get("channel"),
                     "Archive language/channel differs from the frozen input.")
            _require(_snapshot(evaluation) == expected_snapshot, "Evaluation uses a different rubric than the frozen official checklist.")
            _require(evaluation.status in {"completed", "failed"}, "An unfinished evaluation is not a semantic result.")
            if evaluation.status == "completed":
                _require(evaluation.ai_draft is not None and evaluation.final_criteria is not None and evaluation.final_score is not None,
                         "Completed evaluation lacks saved model criteria or score.")
                # Compare actual model results, not a human correction made afterwards.
                _require(evaluation.ai_draft.criteria == evaluation.final_criteria,
                         "A reviewed result cannot measure the model regression.")
                validate_results(record, evaluation.checklist_snapshot, evaluation.final_criteria)
                calculated = calculate_score(evaluation.checklist_snapshot, evaluation.final_criteria)
                _require(calculated.total == evaluation.final_score.total and calculated.gross_error == evaluation.final_score.gross_error,
                         "Saved final score does not match its criteria.")
            results[record.external_id] = _summary(evaluation, cases[record.external_id])
        _require(set(results) == set(source), "Saved results omit a frozen case.")
        return results
    except RegressionInputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RegressionInputError("Invalid saved conversation/evaluation archive.") from None


def _usage(results):
    identities = {(item["execution_run_id"], attempt["attempt"]) for item in results.values()
                  for attempt in item["attempts"] if attempt["network_attempted"] is True}
    return {
        "recorded_network_attempts": len(identities),
        "unknown_execution_metadata": sum(item["execution_run_id"] is None for item in results.values()),
        "total_tokens": sum(item["total_tokens"] for item in results.values())
                        if all(item["total_tokens"] is not None for item in results.values()) else None,
        "cost_usd": sum(item["cost_usd"] for item in results.values())
                    if all(item["cost_usd"] is not None for item in results.values()) else None,
    }


def compare_runs(baseline_archive, candidate_archive, fixtures=None, expectations=None):
    if fixtures is None or expectations is None:
        fixtures, expectations = load_matrix()
    baseline = summarize_run(baseline_archive, fixtures, expectations)
    candidate = summarize_run(candidate_archive, fixtures, expectations)
    rows = []
    for case in expectations["cases"]:
        cid = case["conversation_id"]
        before, after = baseline[cid], candidate[cid]
        before_values, after_values = before["criteria"] or {}, after["criteria"] or {}
        rows.append({
            "conversation_id": cid, "language": case["language"], "scenario": case["scenario"],
            "expected_criteria": case["expected_criteria"], "baseline": before, "candidate": after,
            "criterion_deltas": [{"criterion_id": criterion, "baseline": before_values.get(criterion),
                                   "candidate": after_values.get(criterion), "expected": case["expected_criteria"].get(criterion),
                                   "asserted": criterion in case["expected_criteria"]}
                                  for criterion in CRITERIA if before_values.get(criterion) != after_values.get(criterion)],
            "introduced_mismatches": sorted(set(after["expected_criterion_mismatches"]) - set(before["expected_criterion_mismatches"])),
            "resolved_mismatches": sorted(set(before["expected_criterion_mismatches"]) - set(after["expected_criterion_mismatches"])),
        })
    return {
        "fixture_version": expectations["fixture_version"], "input_sha256": _json_fingerprint(fixtures),
        "expectations_sha256": _json_fingerprint(expectations),
        "interpretation": "Synthetic rubric-derived regression checks only; not organizer ground truth or holdout accuracy.",
        "summary": {
            "case_count": len(rows),
            "baseline_completed": sum(row["baseline"]["status"] == "completed" for row in rows),
            "candidate_completed": sum(row["candidate"]["status"] == "completed" for row in rows),
            "baseline_primary_mismatches": [row["conversation_id"] for row in rows if not row["baseline"]["primary_matches"]],
            "candidate_primary_mismatches": [row["conversation_id"] for row in rows if not row["candidate"]["primary_matches"]],
            "introduced_regressions": [{"conversation_id": row["conversation_id"], "language": row["language"],
                                         "criteria": row["introduced_mismatches"]} for row in rows if row["introduced_mismatches"]],
            "completed_model_changes": [row["conversation_id"] for row in rows
                                        if row["baseline"]["status"] == row["candidate"]["status"] == "completed"
                                        and (row["baseline"]["provider"], row["baseline"]["model"])
                                        != (row["candidate"]["provider"], row["candidate"]["model"])],
            "baseline_usage": _usage(baseline), "candidate_usage": _usage(candidate),
        },
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = compare_runs(json.loads(args.baseline.read_text(encoding="utf-8")),
                              json.loads(args.candidate.read_text(encoding="utf-8")))
    except (RegressionInputError, OSError, ValueError) as exc:
        parser.exit(2, f"Cannot compare saved runs: {exc if isinstance(exc, RegressionInputError) else 'invalid or unavailable JSON archive'}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=True))


if __name__ == "__main__":
    main()
