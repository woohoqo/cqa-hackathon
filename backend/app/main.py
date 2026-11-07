import asyncio
import csv
import io
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .contracts import (Appeal, AppealCreate, ChecklistCreate, ChecklistVersion, Conversation, Dataset,
                        ConversationInput, DevImportRequest, ErrorResponse, Evaluation, EvaluationBatch,
                        EvaluationRequest, EvaluationRun, ImportRequest, ImportResult, OfficialExportRequest,
                        RetryRequest, RetryResult, ReviewDecision, ReviewDecisionCreate)
from .default_checklist import official_checklist
from .errors import AppError
from .importers import fetch_organizer
from .service import CQAService
from .settings import Settings
from .storage import Database


def create_app(*, database_path: str | None = None, evaluator=None, settings: Settings | None = None,
               frontend_dist: str | None = None, organizer_url_guard: Callable[[str], None] | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    if database_path is not None:
        config = replace(config, database_path=database_path)
    db = Database(config.database_path)
    service = CQAService(db, config, evaluator=evaluator)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.initialize()
        if not db.list("checklists", where="key=?", args=("freedom-cqa",), limit=1):
            service.create_checklist(official_checklist())
        service.migrate_legacy_runs()
        service.recover_interrupted()
        workers = [asyncio.create_task(service.worker()) for _ in range(config.ai_concurrency)]
        try:
            yield
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    app = FastAPI(title="CQA evaluation API", version="1.0.0", lifespan=lifespan,
                  responses={status: {"model": ErrorResponse} for status in (403, 404, 409, 422, 502, 503)})
    app.state.service = service
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
                       expose_headers=["Content-Disposition"])

    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}})

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError):
        # Avoid reflecting private submitted values in error responses.
        fields = [".".join(str(part) for part in err["loc"]) for err in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "Некорректный запрос. Проверьте поля: " + ", ".join(fields), "retryable": False}})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "api_version": "1"}

    @app.post("/api/v1/conversations", response_model=Conversation, status_code=201)
    def create_conversation(body: ConversationInput):
        return service.import_conversations([body]).conversations[0]

    @app.get("/api/v1/conversations", response_model=list[Conversation])
    def list_conversations(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return db.list("conversations", limit=limit, offset=offset)

    @app.get("/api/v1/conversations/{conversation_id}", response_model=Conversation)
    def get_conversation(conversation_id: str):
        return db.read("conversations", conversation_id)

    @app.post("/api/v1/imports/json", response_model=ImportResult)
    def import_json(body: ImportRequest):
        return service.import_conversations(body.conversations, dataset_kind=body.dataset_kind, dataset_name=body.dataset_name)

    @app.post("/api/v1/imports/dev", response_model=ImportResult)
    async def import_dev(body: DevImportRequest = DevImportRequest()):
        # The override is request-scoped: never save it in settings, files or the database.
        url = body.api_url if body.api_url is not None else config.dev_api_url
        if organizer_url_guard is not None:
            organizer_url_guard(url)
        return service.import_conversations(await fetch_organizer(url, body.group_id),
            dataset_kind=body.dataset_kind or ("dev" if body.group_id == "DEV" else "test"),
            dataset_name=body.dataset_name, group_id=body.group_id)

    @app.get("/api/v1/datasets", response_model=list[Dataset])
    def list_datasets(limit: int = Query(1000, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return db.list("datasets", limit=limit, offset=offset)

    @app.get("/api/v1/datasets/{dataset_id}", response_model=Dataset)
    def get_dataset(dataset_id: str):
        return db.read("datasets", dataset_id)

    @app.post("/api/v1/checklists", response_model=ChecklistVersion, status_code=201)
    def create_checklist(body: ChecklistCreate):
        return service.create_checklist(body)

    @app.get("/api/v1/checklists", response_model=list[ChecklistVersion])
    def list_checklists(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return db.list("checklists", limit=limit, offset=offset)

    @app.get("/api/v1/checklists/{checklist_id}", response_model=ChecklistVersion)
    def get_checklist(checklist_id: str):
        return db.read("checklists", checklist_id)

    @app.post("/api/v1/evaluations", response_model=EvaluationBatch, status_code=202)
    def start_evaluation(body: EvaluationRequest):
        return service.enqueue(body)

    @app.get("/api/v1/evaluations", response_model=list[Evaluation])
    def list_evaluations(conversation_id: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return db.list("evaluations", where="conversation_id=?" if conversation_id else "1=1",
                       args=(conversation_id,) if conversation_id else (), limit=limit, offset=offset)

    @app.get("/api/v1/evaluations/{evaluation_id}", response_model=Evaluation)
    def get_evaluation(evaluation_id: str):
        return db.read("evaluations", evaluation_id)

    @app.get("/api/v1/batches/{batch_id}", response_model=EvaluationBatch)
    def get_batch(batch_id: str):
        return service.batch(batch_id)

    @app.get("/api/v1/batches", response_model=list[EvaluationRun])
    def list_runs(dataset_id: str | None = None, limit: int = Query(1000, ge=1, le=1000), offset: int = Query(0, ge=0)):
        if dataset_id:
            db.read("datasets", dataset_id)
        return db.list("runs", where="dataset_id=?" if dataset_id else "1=1",
                       args=(dataset_id,) if dataset_id else (), limit=limit, offset=offset)

    @app.get("/api/v1/batches/{batch_id}/attempts", response_model=list[Evaluation])
    def list_attempts(batch_id: str):
        return service.attempts(batch_id)

    @app.post("/api/v1/batches/{batch_id}/retry", response_model=RetryResult)
    def retry_failed(batch_id: str, body: RetryRequest):
        return service.retry_failed(batch_id, body)

    @app.post("/api/v1/batches/{batch_id}/official-export/validate")
    def validate_official_export(batch_id: str, body: OfficialExportRequest):
        from .official_export import build_official_export, validate_official_export as validate, OfficialExportError
        dataset, conversations, evaluations = service.official_inputs(batch_id)
        try:
            payload = body.payload if body.payload is not None else build_official_export(
                conversations, evaluations, body.team_name, dataset.kind)
            return validate(payload, conversations, evaluations=evaluations, dataset_kind=dataset.kind)
        except OfficialExportError as exc:
            return exc.report

    @app.post("/api/v1/batches/{batch_id}/official-export")
    def download_official_export(batch_id: str, body: OfficialExportRequest):
        from .official_export import build_official_export, OfficialExportError
        if body.payload is not None:
            raise AppError(422, "generated_export_only", "Выгружаются только сохранённые сервером результаты выбранного запуска.")
        dataset, conversations, evaluations = service.official_inputs(batch_id)
        try:
            payload = build_official_export(conversations, evaluations, body.team_name, dataset.kind)
        except OfficialExportError as exc:
            issues = exc.report.get("errors", [])
            message = "; ".join((f"{item['conversation_id']}: " if item.get("conversation_id") else "") + item["message"] for item in issues[:5])
            raise AppError(422, "official_export_invalid", message or "Официальный JSON не прошёл проверку.") from None
        return Response(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="results.json"'})

    @app.post("/api/v1/evaluations/{evaluation_id}/appeals", response_model=Appeal, status_code=201)
    def create_appeal(evaluation_id: str, body: AppealCreate):
        return service.appeal(evaluation_id, body)

    @app.get("/api/v1/evaluations/{evaluation_id}/appeals", response_model=list[Appeal])
    def list_appeals(evaluation_id: str):
        db.read("evaluations", evaluation_id)
        return db.list("appeals", where="evaluation_id=?", args=(evaluation_id,), limit=1000)

    @app.post("/api/v1/appeals/{appeal_id}/decisions", response_model=ReviewDecision, status_code=201)
    def decide_appeal(appeal_id: str, body: ReviewDecisionCreate):
        return service.decide(appeal_id, body)

    @app.get("/api/v1/evaluations/{evaluation_id}/decisions", response_model=list[ReviewDecision])
    def list_decisions(evaluation_id: str):
        return service.decisions(evaluation_id)

    @app.get("/api/v1/exports/evaluations")
    def export_evaluations(format: str = Query("json", pattern="^(json|csv)$"), batch_id: str | None = None):
        # One database snapshot for results and human audit history.
        with db.connect() as con:
            con.execute("BEGIN")
            rows = con.execute("SELECT payload FROM evaluations" + (" WHERE batch_id=?" if batch_id else "") + " ORDER BY rowid",
                               (batch_id,) if batch_id else ()).fetchall()
            evaluations = [Evaluation.model_validate_json(row["payload"]) for row in rows]
            if batch_id and not evaluations:
                raise AppError(404, "not_found", "Пакет оценок не найден.")
            ids = {item.id for item in evaluations}
            appeals = [Appeal.model_validate_json(row[0]) for row in con.execute("SELECT payload FROM appeals ORDER BY rowid")]
            appeals = [item for item in appeals if item.evaluation_id in ids]
            appeal_ids = {item.id for item in appeals}
            decisions = [ReviewDecision.model_validate_json(row[0]) for row in con.execute("SELECT payload FROM decisions ORDER BY rowid")]
            decisions = [item for item in decisions if item.appeal_id in appeal_ids]
        headers = {"Content-Disposition": f'attachment; filename="cqa-evaluations.{format}"'}
        if format == "json":
            content = {"schema_version": "1", "evaluations": [e.model_dump(mode="json") for e in evaluations],
                       "appeals": [a.model_dump(mode="json") for a in appeals], "decisions": [d.model_dump(mode="json") for d in decisions]}
            return Response(json.dumps(content, ensure_ascii=False), media_type="application/json", headers=headers)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["evaluation_id", "conversation_id", "checklist_id", "checklist_version", "status", "original_total", "final_total", "gross_error"])
        def safe_cell(value):
            text = str(value) if value is not None else ""
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
        for e in evaluations:
            writer.writerow([safe_cell(value) for value in [e.id, e.conversation_id, e.checklist_snapshot.id,
                e.checklist_snapshot.version, e.status.value, e.original_score.total if e.original_score else None,
                e.final_score.total if e.final_score else None, e.final_score.gross_error if e.final_score else None]])
        return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)

    # Build once with pnpm build; serve the UI and API from one origin for the demo.
    # Mount last so API, health and interactive docs keep their routes.
    static_path = Path(frontend_dist) if frontend_dist else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (static_path / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_path, html=True), name="frontend")
    return app


app = create_app()
