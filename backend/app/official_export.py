"""Official Results JSON 1.0, independent of the internal diagnostic export.

Inputs are an explicitly selected, complete dataset and one selected evaluation per
conversation. This module performs no I/O, reads no configuration, and never calls AI.
Successful rows use the immutable automatic draft and original score, never review decisions.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Literal

from .contracts import ChecklistVersion, Conversation, CriterionResult, Evaluation
from .default_checklist import official_checklist
from .export_privacy import redact_source_names
from .scoring import calculate_score, validate_results

DatasetKind = Literal["dev", "prod", "test"]
OFFICIAL_IDS = (
    "contact", "discovery", "identification", "consultation",
    "empathy", "dialogue", "closure", "critical",
)
CRITERION_MAP = dict(zip((str(i) for i in range(1, 9)), OFFICIAL_IDS))
_REVERSE_MAP = {value: key for key, value in CRITERION_MAP.items()}
_NULLABLE = {"discovery", "identification", "consultation", "closure"}
_SCALES = {"consultation": {0, 5, 10, 15, 20}, "dialogue": {0, 4, 7, 10}}
_REFERENCE = ChecklistVersion(**official_checklist().model_dump(), id="official-export-reference", version=1)
_PROVIDER_MODES = {"gemini": "external_api", "external_api": "external_api", "local": "local", "hybrid": "hybrid"}


class OfficialExportError(ValueError):
    """A safe validation report; never embeds a transcript or raw provider response."""

    def __init__(self, report: dict):
        super().__init__("Official export is not ready; inspect the validation report.")
        self.report = report


def _issue(report, code, path, message, conversation_id=None, *, warning=False):
    item = {"code": code, "path": path, "message": message}
    if conversation_id is not None:
        item["conversation_id"] = conversation_id
    report["warnings" if warning else "errors"].append(item)


def _report(dataset_kind, expected=0):
    return {"valid": False, "dataset_kind": dataset_kind, "errors": [], "warnings": [],
            "counts": {"expected": expected, "actual": 0, "ok": 0, "error": 0}}


def _finish(report):
    report["valid"] = not report["errors"]
    return report


def _sources(conversations, dataset_kind, report):
    if type(dataset_kind) is not str or dataset_kind not in {"dev", "prod", "test"}:
        _issue(report, "invalid_dataset_kind", "dataset_kind", "Выберите dev, prod или test.")
    if not isinstance(conversations, (list, tuple)) or not conversations:
        _issue(report, "empty_dataset", "conversations", "Нужен полный непустой исходный набор диалогов.")
        return {}, {}
    report["counts"]["expected"] = len(conversations)
    required = {"dev": 65, "prod": 10}.get(dataset_kind) if type(dataset_kind) is str else None
    if required is not None and len(conversations) != required:
        _issue(report, "dataset_count", "conversations", f"Официальный набор {dataset_kind} должен содержать ровно {required} диалогов.")
    external, internal = {}, {}
    for index, conversation in enumerate(conversations):
        path = f"conversations[{index}]"
        if not isinstance(conversation, Conversation):
            _issue(report, "invalid_source", path, "Ожидался сохранённый исходный диалог.")
            continue
        # A manually added test transcript has no organizer ID. Its saved stable
        # internal ID defines the test manifest; never invent IDs for dev/prod.
        if dataset_kind == "test" and conversation.external_id is None:
            conversation = conversation.model_copy(update={"external_id": conversation.id})
        cid = conversation.external_id
        if not _text(cid):
            _issue(report, "missing_source_id", path + ".external_id", "Отсутствует исходный conversation_id; для официальных наборов внутренний ID не подставляется.")
            continue
        if cid in external or conversation.id in internal:
            _issue(report, "duplicate_source_id", path, "Исходный набор содержит повторяющийся ID.", cid)
            continue
        external[cid] = conversation
        internal[conversation.id] = conversation
    return external, internal


def _keys(value, keys, path, report, cid=None):
    if type(value) is not dict:
        _issue(report, "invalid_object", path, "Ожидался JSON-объект.", cid)
        return False
    if set(value) != set(keys):
        _issue(report, "object_fields", path, "Набор полей не соответствует официальному формату JSON 1.0.", cid)
    return True


def _text(value):
    if type(value) is not str or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _declared_models(expected_models, report):
    """Optional factual model metadata supplied by the caller, never a config default."""
    if expected_models is None:
        return None
    if not isinstance(expected_models, (list, tuple)) or not expected_models:
        _issue(report, "invalid_expected_models", "model", "Список фактически использованных моделей пуст или некорректен.")
        return {}
    result = {}
    for item in expected_models:
        name, mode = _get(item, "name"), _get(item, "mode")
        if not _text(name) or type(mode) is not str or mode not in {"local", "external_api", "hybrid"}:
            _issue(report, "invalid_expected_models", "model", "Для модели нужны фактическое имя и режим local/external_api/hybrid.")
        elif name in result and result[name] != mode:
            _issue(report, "conflicting_model_mode", "model", "Одна модель указана с несовместимыми режимами.")
        else:
            result[name] = mode
    return result


def _model_object(models):
    modes = set(models.values())
    mode = next(iter(modes)) if len(modes) == 1 else "hybrid"
    return {"mode": mode, "name": ", ".join(sorted(models))}


def _models(evaluations, declared, source_by_internal, report):
    models = {}

    def record(name, provider, cid):
        if not _text(name):
            _issue(report, "missing_model_metadata", "model", "У фактического запуска отсутствует сохранённое имя модели.", cid)
            return
        mode = _PROVIDER_MODES.get(provider)
        if declared is not None:
            if name not in declared:
                _issue(report, "model_provenance_mismatch", "model", "Модель запуска отсутствует в ожидаемых фактических метаданных.", cid)
                return
            if mode is not None and declared[name] != mode:
                _issue(report, "model_provenance_mismatch", "model", "Режим модели расходится с сохранённым провайдером.", cid)
                return
            mode = declared[name]
        if mode is None:
            _issue(report, "unknown_model_mode", "model", "Нельзя достоверно определить local/external_api/hybrid по сохранённому провайдеру.", cid)
            return
        if name in models and models[name] != mode:
            _issue(report, "conflicting_model_mode", "model", "Для одной модели сохранены разные режимы.", cid)
        models[name] = mode

    for evaluation in evaluations:
        conversation = source_by_internal.get(evaluation.conversation_id)
        cid = conversation.external_id if conversation else None
        draft = evaluation.ai_draft
        if draft is not None:
            names = _get(draft, "models_used", None) or [draft.model]
            if not isinstance(names, (list, tuple)):
                _issue(report, "missing_model_metadata", "model", "Список использованных моделей некорректен.", cid)
            else:
                if draft.model not in names:
                    _issue(report, "model_provenance_mismatch", "model", "Финальная модель отсутствует в сохранённом списке использованных моделей.", cid)
                for name in names:
                    record(name, draft.provider, cid)
        if evaluation.status != "completed":
            execution = _get(evaluation, "ai_execution") or _get(draft, "execution")
            if (draft is None and execution is None
                    and _get(evaluation.error, "code") not in {"provider_not_configured", "invalid_ai_configuration"}):
                _issue(report, "missing_model_metadata", "model", "У неудавшегося запуска нет фактических метаданных модели; восстановить их из текущих настроек нельзя.", cid)
            for attempt in _get(execution, "attempts", []) or []:
                if _get(attempt, "network_attempted") is True:
                    record(_get(attempt, "model"), _get(attempt, "provider"), cid)
    if not models:
        _issue(report, "missing_model_metadata", "model", "Нет сохранённых сведений о реально вызванной модели. Текущие настройки и вымышленное имя не используются.")
        return None
    if declared is not None and set(declared) != set(models):
        _issue(report, "model_provenance_mismatch", "model", "Ожидаемые модели не совпадают с моделями выбранных запусков.")
    return _model_object(models)


def _compatible(checklist):
    """Reject changed meanings as well as changed weights/types; never relabel them."""
    if not isinstance(checklist, ChecklistVersion) or len(checklist.criteria) != 8:
        return False
    current = {criterion.id: criterion for criterion in checklist.criteria}
    if set(current) != set(CRITERION_MAP):
        return False
    normalize = lambda text: " ".join((text or "").split())
    if normalize(checklist.description) != normalize(_REFERENCE.description):
        return False
    for expected in _REFERENCE.criteria:
        actual = current[expected.id]
        if (actual.answer_type != expected.answer_type or actual.weight != expected.weight
                or actual.allow_na != expected.allow_na):
            return False
        for field in ("title", "description", "na_condition"):
            if normalize(getattr(actual, field)) != normalize(getattr(expected, field)):
                return False
        actual_steps = {s.value: (normalize(s.label), normalize(s.description)) for s in actual.scale}
        expected_steps = {s.value: (normalize(s.label), normalize(s.description)) for s in expected.scale}
        if actual_steps != expected_steps:
            return False
    return True


def _selected(evaluations, source_by_internal, report):
    selected = {}
    if not isinstance(evaluations, (list, tuple)):
        _issue(report, "invalid_evaluations", "evaluations", "Нужен список явно выбранных оценок.")
        return selected
    for index, evaluation in enumerate(evaluations):
        path = f"evaluations[{index}]"
        if not isinstance(evaluation, Evaluation):
            _issue(report, "invalid_evaluation", path, "Ожидалась сохранённая оценка.")
            continue
        conversation = source_by_internal.get(evaluation.conversation_id)
        if conversation is None:
            _issue(report, "unexpected_evaluation", path, "Оценка относится к диалогу вне выбранного исходного набора.")
        elif evaluation.conversation_id in selected:
            _issue(report, "duplicate_evaluation", path, "Для диалога выбрано несколько оценок; выберите один запуск явно.", conversation.external_id)
        else:
            selected[evaluation.conversation_id] = evaluation
    for internal, conversation in source_by_internal.items():
        if internal not in selected:
            _issue(report, "missing_evaluation", "evaluations", "Диалог ещё не оценён; это не сохранённая ошибка обработки.", conversation.external_id)
    return selected


def _row(conversation, evaluation, report):
    cid = conversation.external_id
    path = "results"
    source_texts = [message.text for message in conversation.messages]
    if evaluation.status in {"queued", "running"}:
        _issue(report, "unfinished_evaluation", path, "Оценка ещё ожидает выполнения или выполняется; дождитесь завершения.", cid)
        return None
    if not _compatible(evaluation.checklist_snapshot):
        _issue(report, "incompatible_checklist", path, "Снимок чек-листа несовместим с официальными восемью критериями; изменённый смысл или шкалы нельзя переименовать при экспорте.", cid)
        return None
    if evaluation.status == "failed":
        if evaluation.error is None or not _text(evaluation.error.message):
            _issue(report, "missing_error_reason", path, "У неудавшейся оценки отсутствует сохранённая причина ошибки.", cid)
            return None
        _issue(report, "evaluation_error", path, "Диалог включён с сохранённой ошибкой обработки; это разрешено форматом и не является нулевой оценкой.", cid, warning=True)
        return {"conversation_id": cid, "status": "error", "criteria": [], "score": None,
                "error": redact_source_names(evaluation.error.message, source_texts)}
    if evaluation.status != "completed":
        _issue(report, "invalid_evaluation_status", path, "Неизвестный статус сохранённой оценки.", cid)
        return None
    if evaluation.ai_draft is None or evaluation.original_score is None:
        _issue(report, "incomplete_completed_evaluation", path, "Завершённая оценка не содержит исходного AI-результата или исходных автоматических баллов.", cid)
        return None
    # The appendix calls the numeric field final_score, but submission provenance
    # is the selected automatic attempt. Human corrections remain in the internal
    # archive/UI and must never silently replace model outputs for organizer scoring.
    criteria_source = evaluation.ai_draft.criteria
    score_source = evaluation.original_score
    try:
        validate_results(conversation, evaluation.checklist_snapshot, criteria_source)
        recomputed = calculate_score(evaluation.checklist_snapshot, criteria_source)
    except (ValueError, TypeError, ArithmeticError):
        _issue(report, "invalid_stored_results", path, "Исходные автоматические критерии, значения или ссылки не проходят проверку.", cid)
        return None
    if recomputed.total is None or score_source.total is None:
        _issue(report, "uncomputed_score", path, "Итог не рассчитан; его нельзя подменить нулём или успешным результатом.", cid)
        return None
    if (score_source.total != recomputed.total
            or score_source.gross_error is not recomputed.gross_error):
        _issue(report, "stored_score_mismatch", path, "Сохранённый исходный автоматический итог или грубая ошибка расходится с пересчётом AI-критериев.", cid)
        return None
    turns = {message.id: index for index, message in enumerate(conversation.messages, 1)}
    indexed = {result.criterion_id: result for result in criteria_source}
    criteria = []
    for internal, official in CRITERION_MAP.items():
        result = indexed[internal]
        value = result.value
        value = None if value == "N/A" else value if type(value) is bool else int(value)
        criteria.append({"criterion_id": official, "value": value,
                         "rationale": redact_source_names(result.reasoning, source_texts),
                         "evidence_turns": [turns[mid] for mid in result.evidence_message_ids]})
    return {"conversation_id": cid, "status": "ok", "criteria": criteria,
            "score": {"final_score": score_source.total, "critical_flag": score_source.gross_error}}


def _assemble(conversations, evaluations, team_name, dataset_kind, expected_models, report):
    _, internal = _sources(conversations, dataset_kind, report)
    if not _text(team_name):
        _issue(report, "missing_team_name", "team_name", "Укажите настоящее название команды.")
    selected = _selected(evaluations, internal, report)
    declared = _declared_models(expected_models, report)
    model = _models(list(selected.values()), declared, internal, report)
    rows = []
    for conversation in internal.values():
        evaluation = selected.get(conversation.id)
        if evaluation is not None:
            row = _row(conversation, evaluation, report)
            if row is not None:
                rows.append(row)
    return {"schema_version": "1.0", "team_name": team_name, "model": model, "results": rows}


def build_official_export(
    conversations: list[Conversation], evaluations: list[Evaluation], team_name: str,
    dataset_kind: DatasetKind, *, expected_models: Sequence[Mapping[str, str]] | None = None,
) -> dict:
    """Build only a complete, valid official payload; otherwise raise a report.

    ``conversations`` is the full authoritative source set. ``evaluations`` must
    contain exactly one explicitly selected evaluation for each source dialogue.
    Successful rows use ai_draft.criteria and original_score before human review;
    rationale/error text applies the documented source-name privacy projection.
    Failed evaluations are valid official error records, with warnings in validation.
    """
    report = _report(dataset_kind)
    payload = _assemble(conversations, evaluations, team_name, dataset_kind, expected_models, report)
    if report["errors"]:
        report["counts"].update(actual=len(payload["results"]),
                                ok=sum(r["status"] == "ok" for r in payload["results"]),
                                error=sum(r["status"] == "error" for r in payload["results"]))
        raise OfficialExportError(_finish(report))
    report = validate_official_export(payload, conversations, evaluations=evaluations,
                                     dataset_kind=dataset_kind, expected_models=expected_models)
    if not report["valid"]:
        raise OfficialExportError(report)
    return payload


def _strict_json(payload, report):
    if not isinstance(payload, (str, bytes, bytearray)):
        return payload
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError("duplicate key")
            obj[key] = value
        return obj
    def constant(value):
        raise ValueError("nonfinite constant")
    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        _issue(report, "invalid_json", "", "Нужен корректный JSON без повторяющихся ключей и нечисловых констант.")
        return None


def _validate_ok(row, conversation, path, report):
    cid = row.get("conversation_id")
    criteria = row.get("criteria")
    if type(criteria) is not list or len(criteria) != 8:
        _issue(report, "criterion_count", path + ".criteria", "Для status=ok нужны ровно восемь критериев.", cid)
        return
    before = len(report["errors"])
    indexed = {}
    for index, item in enumerate(criteria):
        item_path = f"{path}.criteria[{index}]"
        if not _keys(item, {"criterion_id", "value", "rationale", "evidence_turns"}, item_path, report, cid):
            continue
        criterion = item.get("criterion_id")
        if type(criterion) is not str or criterion not in OFFICIAL_IDS:
            _issue(report, "unknown_criterion", item_path, "Неизвестный официальный criterion_id.", cid)
            continue
        if criterion in indexed:
            _issue(report, "duplicate_criterion", item_path, "Критерий повторяется.", cid)
        indexed[criterion] = item
        value = item.get("value")
        valid_value = ((value is None and criterion in _NULLABLE)
                       or (criterion in _SCALES and type(value) in (int, float)
                           and value in _SCALES[criterion])
                       or (criterion not in _SCALES and type(value) is bool))
        if not valid_value:
            _issue(report, "invalid_criterion_value", item_path + ".value", "Недопустимый тип, N/A или ступень официальной шкалы.", cid)
        if not _text(item.get("rationale")):
            _issue(report, "missing_rationale", item_path + ".rationale", "Нужно непустое обоснование.", cid)
        evidence = item.get("evidence_turns")
        if type(evidence) is not list or not evidence:
            _issue(report, "missing_evidence", item_path + ".evidence_turns", "Нужны номера подтверждающих реплик исходного транскрипта.", cid)
        elif (any(type(turn) is not int or turn < 1 or
                  (conversation is not None and turn > len(conversation.messages)) for turn in evidence)
              or len(set(evidence)) != len(evidence)):
            _issue(report, "invalid_evidence_turns", item_path + ".evidence_turns", "Номера реплик должны быть уникальными целыми числами от 1 до длины полного транскрипта.", cid)
    if set(indexed) != set(OFFICIAL_IDS):
        _issue(report, "criterion_coverage", path + ".criteria", "Каждый из восьми официальных критериев должен присутствовать ровно один раз.", cid)
    score = row.get("score")
    if not _keys(score, {"final_score", "critical_flag"}, path + ".score", report, cid):
        return
    total = score.get("final_score")
    if (type(total) not in (int, float) or not 0 <= total <= 100 or not math.isfinite(total)
            or Decimal(str(total)) != Decimal(str(total)).quantize(Decimal("0.01"))):
        _issue(report, "invalid_final_score", path + ".score.final_score", "Итог должен быть числом от 0 до 100 с точностью не более двух знаков.", cid)
    if type(score.get("critical_flag")) is not bool:
        _issue(report, "invalid_critical_flag", path + ".score.critical_flag", "critical_flag должен быть JSON boolean.", cid)
    if len(report["errors"]) != before or conversation is None:
        return
    projected = [CriterionResult(
        criterion_id=_REVERSE_MAP[criterion], value="N/A" if indexed[criterion]["value"] is None else indexed[criterion]["value"],
        reasoning=indexed[criterion]["rationale"],
        evidence_message_ids=[conversation.messages[turn - 1].id for turn in indexed[criterion]["evidence_turns"]],
    ) for criterion in OFFICIAL_IDS]
    calculated = calculate_score(_REFERENCE, projected)
    if total != calculated.total:
        _issue(report, "score_mismatch", path + ".score.final_score", "Итог расходится с официальным пересчётом весов и N/A.", cid)
    if score["critical_flag"] is not indexed["critical"]["value"]:
        _issue(report, "critical_flag_mismatch", path + ".score.critical_flag", "critical_flag должен совпадать со значением critical; итог не обнуляется.", cid)


def validate_official_export(
    payload: object, conversations: list[Conversation], *, evaluations: list[Evaluation] | None = None,
    dataset_kind: DatasetKind = "test", expected_models: Sequence[Mapping[str, str]] | None = None,
    expected_team_name: str | None = None,
) -> dict:
    """Validate JSON 1.0 against source IDs, full transcript positions and scoring.

    A JSON string/UTF-8 bytes additionally detects duplicate object keys. A parsed
    object cannot retain duplicate-key information. Optional evaluations establish
    exact provenance against the selected original automatic results, excluding human decisions.
    """
    report = _report(dataset_kind)
    external, _ = _sources(conversations, dataset_kind, report)
    payload = _strict_json(payload, report)
    if not _keys(payload, {"schema_version", "team_name", "model", "results"}, "", report):
        return _finish(report)
    if payload.get("schema_version") != "1.0":
        _issue(report, "schema_version", "schema_version", "Ожидается строка 1.0.")
    if not _text(payload.get("team_name")):
        _issue(report, "missing_team_name", "team_name", "Укажите настоящее название команды.")
    if expected_team_name is not None and payload.get("team_name") != expected_team_name:
        _issue(report, "team_name_mismatch", "team_name", "Название команды расходится с указанным для экспорта.")
    model = payload.get("model")
    if _keys(model, {"mode", "name"}, "model", report):
        if (type(model.get("mode")) is not str or model.get("mode") not in {"local", "external_api", "hybrid"}
                or not _text(model.get("name"))):
            _issue(report, "invalid_model", "model", "Нужны фактическое имя модели и режим local/external_api/hybrid.")
    declared = _declared_models(expected_models, report)
    if declared and model != _model_object(declared):
        _issue(report, "model_provenance_mismatch", "model", "Экспорт не совпадает с фактическими метаданными моделей.")
    if evaluations is None and expected_models is None:
        _issue(report, "model_provenance_unverified", "model", "Формат модели проверен, но фактическое использование не подтверждено метаданными запусков.", warning=True)
    results = payload.get("results")
    if type(results) is not list:
        _issue(report, "invalid_results", "results", "results должен быть JSON-массивом.")
        return _finish(report)
    report["counts"]["actual"] = len(results)
    if len(results) != report["counts"]["expected"]:
        _issue(report, "result_count", "results", "Количество результатов не совпадает с полным исходным набором.")
    found = set()
    for index, row in enumerate(results):
        path = f"results[{index}]"
        if type(row) is not dict:
            _issue(report, "invalid_object", path, "Результат должен быть JSON-объектом.")
            continue
        cid = row.get("conversation_id")
        if not _text(cid):
            _issue(report, "invalid_conversation_id", path, "Нужен исходный conversation_id.")
            cid = None
        elif cid not in external:
            _issue(report, "unexpected_conversation_id", path, "ID отсутствует в исходном наборе.", cid)
        if cid in found:
            _issue(report, "duplicate_conversation_id", path, "Диалог повторяется в results.", cid)
        found.add(cid)
        status = row.get("status")
        keys = {"conversation_id", "status", "criteria", "score"}
        if status == "error":
            report["counts"]["error"] += 1
            _keys(row, keys | {"error"}, path, report, cid)
            if row.get("criteria") != [] or row.get("score") is not None or not _text(row.get("error")):
                _issue(report, "invalid_error_result", path, "Для error нужны пустой criteria, score=null и непустая причина error.", cid)
            else:
                _issue(report, "evaluation_error", path, "Диалог включён с сохранённой ошибкой обработки; это разрешено форматом и не является нулевой оценкой.", cid, warning=True)
        elif status == "ok":
            report["counts"]["ok"] += 1
            _keys(row, keys, path, report, cid)
            _validate_ok(row, external.get(cid), path, report)
        else:
            _issue(report, "invalid_status", path + ".status", "Официальный статус должен быть ok или error.", cid)
    for cid in external.keys() - found:
        _issue(report, "missing_conversation_id", "results", "Исходный диалог отсутствует в results.", cid)
    if evaluations is not None:
        source_report = _report(dataset_kind)
        expected = _assemble(conversations, evaluations, payload.get("team_name"), dataset_kind, expected_models, source_report)
        report["errors"].extend(source_report["errors"])
        if not source_report["errors"]:
            if model != expected["model"]:
                _issue(report, "model_provenance_mismatch", "model", "Модель экспорта не совпадает с моделями выбранных запусков.")
            actual_rows = {row.get("conversation_id"): row for row in results if type(row) is dict and _text(row.get("conversation_id"))}
            for row in expected["results"]:
                if actual_rows.get(row["conversation_id"]) != row:
                    _issue(report, "stored_result_mismatch", "results", "Экспорт отличается от выбранной исходной автоматической оценки, объяснений или ссылок.", row["conversation_id"])
    return _finish(report)


def official_results_schema() -> dict:
    """JSON Schema 2020-12 for the appendix's file format, using validator constants.

    Cross-document ID membership, provenance, transcript length and score arithmetic
    require validate_official_export; JSON Schema alone cannot establish those facts.
    """
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}

    def obj(properties):
        return {"type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}

    criteria = []
    for criterion in OFFICIAL_IDS:
        value = ({"type": "number", "enum": sorted(_SCALES[criterion])}
                 if criterion in _SCALES else {"type": "boolean"})
        if criterion in _NULLABLE:
            value = {"anyOf": [value, {"type": "null"}]}
        criteria.append(obj({"criterion_id": {"const": criterion}, "value": value,
                             "rationale": text, "evidence_turns": {
                                 "type": "array", "minItems": 1, "uniqueItems": True,
                                 "items": {"type": "integer", "minimum": 1}}}))
    criterion_array = {"type": "array", "minItems": 8, "maxItems": 8,
                       "items": {"oneOf": criteria}, "allOf": [
                           {"contains": {"properties": {"criterion_id": {"const": criterion}}},
                            "minContains": 1, "maxContains": 1} for criterion in OFFICIAL_IDS]}
    success = obj({"conversation_id": text, "status": {"const": "ok"},
                   "criteria": criterion_array,
                   "score": obj({"final_score": {"type": "number", "minimum": 0, "maximum": 100,
                                                    "multipleOf": 0.01},
                                 "critical_flag": {"type": "boolean"}})})
    failure = obj({"conversation_id": text, "status": {"const": "error"},
                   "criteria": {"type": "array", "maxItems": 0},
                   "score": {"type": "null"}, "error": text})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "CQA official results JSON 1.0",
            **obj({"schema_version": {"const": "1.0"}, "team_name": text,
                   "model": obj({"mode": {"enum": ["local", "external_api", "hybrid"]}, "name": text}),
                   "results": {"type": "array", "minItems": 1, "items": {"oneOf": [success, failure]}}})}
