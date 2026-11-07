"""SQLite persistence. JSON payloads use the same Pydantic contracts as the API."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .contracts import Appeal, ChecklistVersion, Conversation, Dataset, Evaluation, EvaluationRun, ReviewDecision
from .errors import AppError

MODELS = {"conversations": Conversation, "checklists": ChecklistVersion, "evaluations": Evaluation,
          "appeals": Appeal, "decisions": ReviewDecision, "datasets": Dataset, "runs": EvaluationRun}


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self, *, write: bool = False):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT,
                    payload TEXT NOT NULL, UNIQUE(source, external_id));
                CREATE TABLE IF NOT EXISTS checklists (
                    id TEXT PRIMARY KEY, key TEXT NOT NULL, version INTEGER NOT NULL,
                    payload TEXT NOT NULL, UNIQUE(key, version));
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS evaluation_batch ON evaluations(batch_id);
                CREATE INDEX IF NOT EXISTS evaluation_status ON evaluations(status);
                CREATE TABLE IF NOT EXISTS appeals (
                    id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
                    criterion_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS open_appeal ON appeals(evaluation_id, criterion_id)
                    WHERE status='open';
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY, appeal_id TEXT NOT NULL UNIQUE REFERENCES appeals(id),
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS run_dataset ON runs(dataset_id);
                CREATE TABLE IF NOT EXISTS run_requests (
                    request_key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    request_payload TEXT NOT NULL);
            """)

    @staticmethod
    def get(con, table: str, identifier: str):
        model = MODELS[table]
        row = con.execute(f"SELECT payload FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Запись не найдена.")
        return model.model_validate_json(row["payload"])

    def read(self, table: str, identifier: str):
        with self.connect() as con:
            return self.get(con, table, identifier)

    def list(self, table: str, *, where: str = "1=1", args=(), limit: int = 100, offset: int = 0):
        model = MODELS[table]
        with self.connect() as con:
            rows = con.execute(f"SELECT payload FROM {table} WHERE {where} ORDER BY rowid LIMIT ? OFFSET ?",
                               (*args, limit, offset)).fetchall()
        return [model.model_validate_json(row["payload"]) for row in rows]

    @staticmethod
    def save_evaluation(con, evaluation: Evaluation):
        con.execute("UPDATE evaluations SET status=?,payload=? WHERE id=?",
                    (evaluation.status.value, evaluation.model_dump_json(), evaluation.id))
