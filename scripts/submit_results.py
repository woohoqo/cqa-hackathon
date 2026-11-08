"""Resume a local backend submission run; never load provider keys or signed URLs."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from backend.app.ai.prompt import PROMPT_VERSION
from backend.app.contracts import ChecklistVersion, Conversation, Dataset, Evaluation, EvaluationBatch, ImportResult
from backend.app.default_checklist import official_checklist
from backend.app.official_export import validate_official_export

ROOT = Path(__file__).resolve().parents[1]


class SubmissionError(Exception):
    """Only fixed safe messages; never include HTTP bodies, transcripts or URLs."""


def _fail(code):
    raise SubmissionError(code)


def atomic_json(path: Path, data):
    encoded = (json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".submission-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def state_lock(directory):
    """OS lock releases on process death; an old lock filename does not block resume."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _fail("state_in_use: another submission command owns this state directory")
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def local_base(value):
    try:
        parts = urlsplit(value)
        host = parts.hostname
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        valid = (local and parts.scheme in {"http", "https"} and not parts.username
                 and not parts.password and not parts.query and not parts.fragment
                 and parts.path in {"", "/"} and (parts.port is None or 0 < parts.port < 65536))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        _fail("invalid_api_base: use a loopback backend URL without credentials, query or path")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _definition(checklist):
    return checklist.model_dump(mode="json", exclude={"id", "version", "created_at"})


def _official(checklist):
    return _definition(checklist) == official_checklist().model_dump(mode="json")


def _hash_sources(conversations):
    values = [{"id": c.id, "external_id": c.external_id, "source": c.source,
               "messages": [m.model_dump(mode="json") for m in c.messages]} for c in conversations]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _sources(dataset, conversations, kind, group):
    expected = 65 if kind == "dev" else 10
    if (dataset.kind != kind or dataset.group_id != group or len(conversations) != expected
            or len(dataset.conversation_ids) != expected
            or len(set(dataset.conversation_ids)) != expected
            or [c.id for c in conversations] != dataset.conversation_ids
            or len({c.external_id for c in conversations}) != expected
            or any(not c.external_id or not c.external_id.strip() for c in conversations)):
        _fail("source_manifest_mismatch: wrong dataset kind, group, count or original IDs")


def _request(client, base, method, path, body=None):
    try:
        response = client.request(method, base + "/api/v1/" + path, json=body)
        if not 200 <= response.status_code < 300:
            _fail(f"backend_http_error: HTTP {response.status_code}; inspect the local backend")
        return response.json()
    except httpx.HTTPError:
        _fail("backend_connection_error: state retained; rerun the same command after recovery")
    except (ValueError, UnicodeError):
        _fail("backend_invalid_json: state retained; inspect the local backend")


def _batch(value, state):
    batch = EvaluationBatch.model_validate(value)
    expected = state["request"]["conversation_ids"]
    if (batch.run is None or batch.run.dataset_id != state["dataset_id"]
            or batch.run.conversation_ids != expected
            or batch.run.checklist_snapshot.id != state["request"]["checklist_id"]
            or not _official(batch.run.checklist_snapshot)
            or [e.conversation_id for e in batch.evaluations] != expected
            or (state.get("batch_id") is not None and batch.id != state["batch_id"])):
        _fail("batch_manifest_mismatch: selected run does not match saved sources and checklist")
    for evaluation in batch.evaluations:
        if evaluation.batch_id != batch.id or evaluation.checklist_snapshot != batch.run.checklist_snapshot:
            _fail("batch_manifest_mismatch: evaluation snapshot or run changed")
        if evaluation.ai_draft is not None and evaluation.ai_draft.prompt_version != PROMPT_VERSION:
            _fail("stale_prompt: use --new-run after updating the backend engine")
    return batch


def execute(options, client, *, sleep=time.sleep, clock=time.monotonic, emit=print):
    """All network access is to the explicitly selected loopback backend."""
    base = local_base(options.api_base)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", options.group_id):
        _fail("invalid_group_id: supply the exact group identifier issued by the organizers")
    if not options.team_name.strip() or len(options.team_name) > 200:
        _fail("invalid_team_name")
    if (not 0 <= options.retry_rounds <= 3 or not 0 <= options.poll_seconds <= 60
            or not math.isfinite(options.wait_seconds) or not 0 < options.wait_seconds <= 43200):
        _fail("invalid_limits")
    directory = Path(options.state_dir or f"data/submission/{options.kind}").resolve()
    destination = Path(options.output or f"results/{options.kind}/results.json").resolve()
    if directory.is_relative_to(ROOT) and not any(directory.is_relative_to(ROOT / p) for p in ("data", "private")):
        _fail("unsafe_state_directory: private state must be under ignored data/ or private/")
    if destination.is_relative_to(directory):
        _fail("output_inside_state: choose a separate results output path")
    binding = {"version": 1, "kind": options.kind, "group_id": options.group_id,
               "api_base": base, "team_name": options.team_name, "prompt_version": PROMPT_VERSION}
    deadline = clock() + options.wait_seconds
    with state_lock(directory):
        state_path = directory / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        if state is not None and options.new_run:
            atomic_json(directory / "history" / (str(uuid4()) + ".json"), state)
            state = None
        if state is None:
            state = {**binding, "idempotency_key": str(uuid4()), "dataset_id": None,
                     "request": None, "batch_id": None, "retry_rounds_used": 0, "retry_pending": None}
            atomic_json(state_path, state)
        elif any(state.get(key) != value for key, value in binding.items()):
            _fail("state_mismatch: use the original command or explicit --new-run")
        if type(state.get("retry_rounds_used")) is not int or not 0 <= state["retry_rounds_used"] <= 3:
            _fail("invalid_retry_state")

        def save():
            atomic_json(state_path, state)

        def request(method, path, body=None):
            if clock() >= deadline:
                _fail("wait_timeout: state retained; rerun the same command to resume")
            return _request(client, base, method, path, body)

        if state["dataset_id"] is None:
            imported = ImportResult.model_validate(request("POST", "imports/dev", {
                "group_id": options.group_id, "dataset_kind": options.kind,
                "dataset_name": f"Submission {options.kind}",
            }))
            dataset, conversations = imported.dataset, imported.conversations
            if dataset is None:
                _fail("missing_dataset_manifest")
            _sources(dataset, conversations, options.kind, options.group_id)
            state.update(dataset_id=dataset.id, source_sha256=_hash_sources(conversations))
            save()
        else:
            dataset = Dataset.model_validate(request("GET", "datasets/" + quote(state["dataset_id"], safe="")))
            conversations = [Conversation.model_validate(request("GET", "conversations/" + quote(cid, safe="")))
                             for cid in dataset.conversation_ids]
            _sources(dataset, conversations, options.kind, options.group_id)
            if _hash_sources(conversations) != state["source_sha256"]:
                _fail("source_changed: saved source manifest or transcript changed")
        if state["request"] is None:
            candidates, offset = [], 0
            while True:
                page = request("GET", f"checklists?limit=1000&offset={offset}")
                candidates.extend(ChecklistVersion.model_validate(item) for item in page)
                if len(page) < 1000:
                    break
                offset += len(page)
            exact = [item for item in candidates if _official(item)]
            if not exact:
                _fail("official_checklist_missing: current default definition was not found")
            checklist = min(exact, key=lambda item: (item.version, item.created_at, item.id))
            state["request"] = {"dataset_id": dataset.id, "conversation_ids": dataset.conversation_ids,
                                "checklist_id": checklist.id, "idempotency_key": state["idempotency_key"]}
            save()  # Before POST: ambiguous network failure replays the SAME idempotency key.
        if state["batch_id"] is None:
            batch = _batch(request("POST", "evaluations", state["request"]), state)
            state["batch_id"] = batch.id
            save()
        run_path = "batches/" + quote(state["batch_id"], safe="")
        previous = None
        while True:
            if state["retry_pending"]:
                request("POST", run_path + "/retry", {"failed_evaluation_ids": state["retry_pending"]})
                state["retry_pending"] = None
                save()  # Replaying exact old failed IDs cannot retry a newer attempt twice.
            batch = _batch(request("GET", run_path), state)
            counts = Counter(e.status.value for e in batch.evaluations)
            progress = tuple(counts.get(key, 0) for key in ("queued", "running", "completed", "failed"))
            if progress != previous:
                emit("queued=%d running=%d completed=%d failed=%d" % progress)
                previous = progress
            if progress[0] or progress[1]:
                sleep(min(options.poll_seconds, max(0, deadline - clock())))
                continue
            failed = [e.id for e in batch.evaluations if e.status == "failed"]
            if failed and state["retry_rounds_used"] < options.retry_rounds:
                state["retry_pending"] = failed
                state["retry_rounds_used"] += 1
                save()
                continue
            break
        attempts_data = request("GET", run_path + "/attempts")
        attempts = [Evaluation.model_validate(item) for item in attempts_data]
        if any(e.batch_id != batch.id or e.conversation_id not in dataset.conversation_ids for e in attempts):
            _fail("attempt_manifest_mismatch")
        if (len({e.id for e in attempts}) != len(attempts)
                or not {e.id for e in batch.evaluations} <= {e.id for e in attempts}):
            _fail("attempt_history_incomplete")
        archive = directory / "attempts" / (hashlib.sha256(batch.id.encode()).hexdigest() + ".json")
        atomic_json(archive, attempts_data)  # Separate per-run private audit; retain older runs too.
        report = request("POST", run_path + "/official-export/validate", {"team_name": options.team_name})
        atomic_json(directory / "validation-server.json", report)
        if report.get("valid") is not True:
            _fail("official_export_invalid: inspect the private validation report")
        payload = request("POST", run_path + "/official-export", {"team_name": options.team_name})
        local_report = validate_official_export(payload, conversations, evaluations=batch.evaluations,
            dataset_kind=options.kind, expected_team_name=options.team_name)
        atomic_json(directory / "validation-local.json", local_report)
        if not local_report["valid"]:
            _fail("local_export_invalid: original AI provenance, IDs or arithmetic did not validate")
        atomic_json(destination, payload)
        state["exported_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
        save()
        emit(f"Validated {len(payload['results'])} results; error records: {counts.get('failed', 0)}. File saved.")
        return payload


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid CLI arguments. Use --help; do not pass keys or signed URLs.\n")


def parser():
    root = SafeParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Import, evaluate or resume, validate and atomically export")
    run.add_argument("--kind", choices=("dev", "prod"), required=True)
    run.add_argument("--group-id", required=True)
    run.add_argument("--api-base", default="http://127.0.0.1:8000")
    run.add_argument("--team-name", default="cuda cuda")
    run.add_argument("--state-dir")
    run.add_argument("--output")
    run.add_argument("--new-run", action="store_true")
    run.add_argument("--retry-rounds", type=int, default=1, help="Total failed-only retry rounds for this saved run (0..3)")
    run.add_argument("--poll-seconds", type=float, default=2)
    run.add_argument("--wait-seconds", type=float, default=3600)
    return root


def main(argv=None):
    options = parser().parse_args(argv)
    try:
        with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
            execute(options, client)
        return 0
    except KeyboardInterrupt:
        print("Interrupted. State retained; rerun the same command to resume.", file=sys.stderr)
        return 130
    except SubmissionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, ValidationError):
        print("Invalid local state, backend response or filesystem operation; existing results preserved.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
