import asyncio
import json
from uuid import NAMESPACE_URL, uuid5

from .contracts import (
    AIProviderError, Appeal, AppealCreate, ChecklistCreate, ChecklistVersion, Conversation,
    ConversationInput, ErrorDetail, Evaluation, EvaluationBatch, EvaluationDraft, EvaluationRequest,
    EvaluationStatus, ImportResult, Message, ReviewDecision, ReviewDecisionCreate, new_id, utcnow,
)
from .errors import AppError
from .scoring import calculate_score, validate_results
from .settings import Settings
from .storage import Database


class CQAService:
    def __init__(self, db: Database, settings: Settings, evaluator=None):
        self.db = db
        self.settings = settings
        self.evaluator = evaluator

    def import_conversations(self, inputs: list[ConversationInput]) -> ImportResult:
        imported = 0
        conversations = []
        with self.db.connect(write=True) as con:
            for item in inputs:
                identity = json.dumps([item.source, item.external_id], ensure_ascii=False, separators=(",", ":"))
                identifier = str(uuid5(NAMESPACE_URL, identity)) if item.external_id else new_id()
                messages = [Message(**{**msg.model_dump(), "id": msg.id or str(uuid5(NAMESPACE_URL, f"cqa:{identifier}:message:{i}"))})
                            for i, msg in enumerate(item.messages)]
                if len({m.id for m in messages}) != len(messages):
                    raise AppError(422, "duplicate_message_ids", "ID сообщений внутри диалога должны быть уникальными.")
                conversation = Conversation(**{**item.model_dump(), "id": identifier, "messages": messages})
                old = con.execute("SELECT payload FROM conversations WHERE id=?", (identifier,)).fetchone()
                if old:
                    stored = Conversation.model_validate_json(old["payload"])
                    if stored.model_dump(exclude={"created_at"}) != conversation.model_dump(exclude={"created_at"}):
                        raise AppError(409, "source_conflict", "Этот source/external_id уже существует с другим содержимым.")
                    conversation = stored
                else:
                    con.execute("INSERT INTO conversations VALUES (?,?,?,?)", (identifier, item.source, item.external_id, conversation.model_dump_json()))
                    imported += 1
                conversations.append(conversation)
        return ImportResult(conversations=conversations, imported=imported, existing=len(inputs) - imported)

    def create_checklist(self, request: ChecklistCreate) -> ChecklistVersion:
        with self.db.connect(write=True) as con:
            version = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM checklists WHERE key=?", (request.key,)).fetchone()[0]
            checklist = ChecklistVersion(**request.model_dump(), id=new_id(), version=version)
            con.execute("INSERT INTO checklists VALUES (?,?,?,?)", (checklist.id, checklist.key, checklist.version, checklist.model_dump_json()))
        return checklist

    def enqueue(self, request: EvaluationRequest) -> EvaluationBatch:
        batch_id = new_id()
        records = []
        with self.db.connect(write=True) as con:
            checklist = self.db.get(con, "checklists", request.checklist_id)
            for cid in request.conversation_ids:
                self.db.get(con, "conversations", cid)
                evaluation = Evaluation(id=new_id(), batch_id=batch_id, conversation_id=cid,
                                        checklist_snapshot=checklist, status=EvaluationStatus.QUEUED)
                con.execute("INSERT INTO evaluations VALUES (?,?,?,?,?)", (evaluation.id, batch_id, cid, evaluation.status.value, evaluation.model_dump_json()))
                records.append(evaluation)
        return EvaluationBatch(id=batch_id, evaluations=records)

    def batch(self, identifier: str) -> EvaluationBatch:
        evaluations = self.db.list("evaluations", where="batch_id=?", args=(identifier,), limit=100)
        if not evaluations:
            raise AppError(404, "not_found", "Пакет оценок не найден.")
        return EvaluationBatch(id=identifier, evaluations=evaluations)

    def recover_interrupted(self):
        with self.db.connect(write=True) as con:
            rows = con.execute("SELECT payload FROM evaluations WHERE status='running'").fetchall()
            for row in rows:
                record = Evaluation.model_validate_json(row["payload"])
                record.status = EvaluationStatus.FAILED
                record.updated_at = utcnow()
                record.error = ErrorDetail(code="interrupted", message="Оценка прервана перезапуском сервера; запустите её повторно.", retryable=True)
                self.db.save_evaluation(con, record)

    def claim_next(self) -> Evaluation | None:
        with self.db.connect(write=True) as con:
            row = con.execute("SELECT payload FROM evaluations WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            record = Evaluation.model_validate_json(row["payload"])
            record.status = EvaluationStatus.RUNNING
            record.updated_at = utcnow()
            self.db.save_evaluation(con, record)
            return record

    async def worker(self):
        while True:
            record = self.claim_next()
            if record is None:
                await asyncio.sleep(0.15)
                continue
            await self.evaluate(record)

    async def evaluate(self, record: Evaluation):
        try:
            conversation = self.db.read("conversations", record.conversation_id)
            if self.evaluator is None:
                from .ai.service import evaluate_dialogue
                evaluator = evaluate_dialogue
            else:
                evaluator = self.evaluator
            result = await asyncio.wait_for(evaluator(conversation.model_copy(deep=True), record.checklist_snapshot.model_copy(deep=True)), timeout=self.settings.ai_timeout_seconds)
            if not isinstance(result, EvaluationDraft):
                raise AIProviderError("invalid_ai_response", "AI-модуль вернул результат вне согласованного контракта.")
            record.ai_draft = result
            record.raw_response = result.raw_response
            try:
                validate_results(conversation, record.checklist_snapshot, result.criteria)
                score = calculate_score(record.checklist_snapshot, result.criteria)
            except ValueError:
                raise AIProviderError("invalid_ai_response", "Ответ AI не прошёл проверку критериев, значений или ссылок на сообщения.") from None
            record.original_score = score
            record.final_score = score.model_copy(deep=True)
            record.final_criteria = [r.model_copy(deep=True) for r in result.criteria]
            record.status = EvaluationStatus.COMPLETED
        except AIProviderError as exc:
            if exc.raw_response is not None:
                record.raw_response = exc.raw_response
            record.status = EvaluationStatus.FAILED
            record.error = ErrorDetail(code=exc.code, message=exc.message, retryable=exc.retryable)
        except TimeoutError:
            record.status = EvaluationStatus.FAILED
            record.error = ErrorDetail(code="ai_timeout", message="Превышено время ожидания AI-провайдера.", retryable=True)
        except asyncio.CancelledError:
            record.status = EvaluationStatus.FAILED
            record.error = ErrorDetail(code="interrupted", message="Оценка прервана остановкой сервера.", retryable=True)
            raise
        except Exception:
            # Provider exceptions may contain signed URLs, prompts or API keys.
            record.status = EvaluationStatus.FAILED
            record.error = ErrorDetail(code="provider_error", message="Ошибка AI-провайдера. Проверьте его настройки и доступность.", retryable=True)
        finally:
            record.updated_at = utcnow()
            with self.db.connect(write=True) as con:
                self.db.save_evaluation(con, record)

    def appeal(self, evaluation_id: str, request: AppealCreate) -> Appeal:
        with self.db.connect(write=True) as con:
            record = self.db.get(con, "evaluations", evaluation_id)
            if record.status != EvaluationStatus.COMPLETED:
                raise AppError(409, "not_completed", "Апелляция доступна только для завершённой оценки.")
            criterion = next((c for c in record.checklist_snapshot.criteria if c.id == request.criterion_id), None)
            if criterion is None:
                raise AppError(422, "unknown_criterion", "Критерий отсутствует в снимке чек-листа.")
            if request.proposed_value is not None:
                self._validate_replacement(con, record, request.criterion_id, value=request.proposed_value)
            existing = con.execute("SELECT id FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'",
                                   (evaluation_id, request.criterion_id)).fetchone()
            if existing:
                raise AppError(409, "appeal_exists", "По этому критерию уже есть открытая апелляция.")
            appeal = Appeal(**request.model_dump(), id=new_id(), evaluation_id=evaluation_id)
            con.execute("INSERT INTO appeals VALUES (?,?,?,?,?)", (appeal.id, evaluation_id, appeal.criterion_id, appeal.status, appeal.model_dump_json()))
        return appeal

    def _validate_replacement(self, con, record, criterion_id, *, replacement=None, value=None):
        results = [r.model_copy(deep=True) for r in record.final_criteria]
        found = False
        for i, result in enumerate(results):
            if result.criterion_id == criterion_id:
                found = True
                results[i] = replacement if replacement is not None else result.model_copy(update={"value": value})
        if not found:
            raise AppError(422, "unknown_criterion", "Критерий отсутствует в результате.")
        try:
            validate_results(self.db.get(con, "conversations", record.conversation_id), record.checklist_snapshot, results)
        except ValueError:
            raise AppError(422, "invalid_replacement", "Исправление содержит недопустимое значение или ссылку на сообщение.") from None
        return results

    def decide(self, appeal_id: str, request: ReviewDecisionCreate) -> ReviewDecision:
        with self.db.connect(write=True) as con:
            appeal = self.db.get(con, "appeals", appeal_id)
            if appeal.status != "open":
                raise AppError(409, "already_decided", "Решение по апелляции уже принято.")
            record = self.db.get(con, "evaluations", appeal.evaluation_id)
            if request.replacement is not None:
                if request.replacement.criterion_id != appeal.criterion_id:
                    raise AppError(422, "criterion_mismatch", "Исправление должно относиться к критерию апелляции.")
                results = self._validate_replacement(con, record, appeal.criterion_id, replacement=request.replacement)
                record.final_criteria = results
                record.final_score = calculate_score(record.checklist_snapshot, results)
                record.updated_at = utcnow()
                self.db.save_evaluation(con, record)
            decision = ReviewDecision(**request.model_dump(), id=new_id(), appeal_id=appeal_id)
            appeal.status = request.outcome
            con.execute("INSERT INTO decisions VALUES (?,?,?)", (decision.id, appeal_id, decision.model_dump_json()))
            con.execute("UPDATE appeals SET status=?,payload=? WHERE id=?", (appeal.status, appeal.model_dump_json(), appeal_id))
        return decision

    def decisions(self, evaluation_id: str) -> list[ReviewDecision]:
        self.db.read("evaluations", evaluation_id)
        with self.db.connect() as con:
            rows = con.execute("SELECT d.payload FROM decisions d JOIN appeals a ON a.id=d.appeal_id WHERE a.evaluation_id=? ORDER BY d.rowid", (evaluation_id,)).fetchall()
        return [ReviewDecision.model_validate_json(r["payload"]) for r in rows]
