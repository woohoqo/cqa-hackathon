"""Submission CLI with synthetic server data and MockTransport; no organizer or AI calls."""
from copy import deepcopy
import json

import httpx
import pytest

from backend.app.ai.prompt import PROMPT_VERSION
from backend.app.contracts import Dataset, EvaluationBatch, EvaluationRun, EvaluationStatus
from backend.app.official_export import build_official_export
from backend.app.scoring import calculate_score
from backend.tests.test_official_export import make_failed, synthetic_case
from scripts import submit_results as cli


def options(tmp_path, *extra):
    return cli.parser().parse_args([
        "run", "--kind", "prod", "--group-id", "SYNTHETIC_CONTROL",
        "--state-dir", str(tmp_path / "state"), "--output", str(tmp_path / "results.json"),
        "--poll-seconds", "0", *extra,
    ])


class Server:
    def __init__(self, count=10, kind="prod"):
        pairs = [synthetic_case(i) for i in range(count)]
        self.conversations, self.completed = map(list, zip(*pairs))
        self.checklist = self.completed[0].checklist_snapshot
        for evaluation in self.completed:
            evaluation.batch_id = "synthetic-batch"
            evaluation.dataset_id = "synthetic-dataset"
            evaluation.ai_draft.prompt_version = PROMPT_VERSION
            evaluation.checklist_snapshot = deepcopy(self.checklist)
        self.dataset = Dataset(id="synthetic-dataset", name="Synthetic control", kind=kind,
            group_id="DEV" if kind == "dev" else "SYNTHETIC_CONTROL", conversation_ids=[c.id for c in self.conversations])
        self.current = deepcopy(self.completed)
        self.attempts = deepcopy(self.current)
        self.calls = []
        self.keys = []
        self.created = False
        self.lose_enqueue = False
        self.interrupt_poll = False
        self.lose_retry = False
        self.bad_payload = None
        self.http_error = None
        self.preflight_valid = True
        self.running_polls = 0
        self.retry_fails = False

    def batch(self):
        run = EvaluationRun(id="synthetic-batch", dataset_id=self.dataset.id,
            conversation_ids=self.dataset.conversation_ids, checklist_snapshot=self.checklist)
        return EvaluationBatch(id=run.id, run=run, evaluations=self.current).model_dump(mode="json")

    def handle(self, request):
        path = request.url.path.removeprefix("/api/v1/")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if self.http_error:
            return httpx.Response(self.http_error, text="SECRET https://signed.example.invalid/?token=SECRET")
        if path == "imports/dev":
            assert "api_url" not in body
            return httpx.Response(200, json={"conversations": [c.model_dump(mode="json") for c in self.conversations],
                "dataset": self.dataset.model_dump(mode="json"), "imported": len(self.conversations), "existing": 0})
        if path == "checklists":
            changed = self.checklist.model_copy(deep=True)
            changed.id, changed.version = "custom", 99
            changed.criteria[0].description = "Synthetic changed meaning"
            return httpx.Response(200, json=[changed.model_dump(mode="json"), self.checklist.model_dump(mode="json")])
        if path.startswith("datasets/"):
            return httpx.Response(200, json=self.dataset.model_dump(mode="json"))
        if path.startswith("conversations/"):
            cid = path.split("/", 1)[1]
            return httpx.Response(200, json=next(c for c in self.conversations if c.id == cid).model_dump(mode="json"))
        if path == "evaluations":
            self.keys.append(body["idempotency_key"])
            assert body["checklist_id"] == self.checklist.id
            self.created = True
            if self.lose_enqueue:
                self.lose_enqueue = False
                raise httpx.ReadTimeout("SECRET signed URL")
            return httpx.Response(202, json=self.batch())
        if path.endswith("/retry"):
            old_ids = body["failed_evaluation_ids"]
            for i, evaluation in enumerate(self.current):
                if evaluation.id in old_ids and evaluation.status == "failed":
                    retried = deepcopy(self.completed[i])
                    retried.id = evaluation.id + "-retry"
                    retried.retry_of = evaluation.id
                    retried.attempt_number = evaluation.attempt_number + 1
                    if self.retry_fails:
                        make_failed(retried)
                        retried.status = EvaluationStatus.FAILED
                    self.current[i] = retried
                    self.attempts.append(deepcopy(retried))
            if self.lose_retry:
                self.lose_retry = False
                raise httpx.ReadTimeout("SECRET signed URL")
            return httpx.Response(200, json={"batch": self.batch(), "retried": 0, "skipped": 0})
        if path.endswith("/attempts"):
            return httpx.Response(200, json=[e.model_dump(mode="json") for e in self.attempts])
        if path.endswith("/official-export/validate"):
            return httpx.Response(200, json={"valid": self.preflight_valid, "errors": [], "warnings": []})
        if path.endswith("/official-export"):
            payload = build_official_export(self.conversations, self.current, "cuda cuda", self.dataset.kind)
            if self.bad_payload:
                self.bad_payload(payload)
            return httpx.Response(200, json=payload)
        if path == "batches/synthetic-batch":
            if self.interrupt_poll:
                self.interrupt_poll = False
                raise KeyboardInterrupt
            if self.running_polls:
                self.running_polls -= 1
                pending = self.batch()
                for row in pending["evaluations"]:
                    row.update(status="running", ai_draft=None, raw_response=None,
                               original_score=None, final_score=None, final_criteria=None)
                return httpx.Response(200, json=pending)
            return httpx.Response(200, json=self.batch())
        raise AssertionError(path)

    def run(self, opts, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(self.handle)) as client:
            return cli.execute(opts, client, emit=lambda text: None, **kwargs)


def test_valid_submission_uses_exact_official_snapshot_and_original_ai(tmp_path):
    server = Server()
    server.current[0].final_criteria[0].value = False
    server.current[0].final_score = calculate_score(server.checklist, server.current[0].final_criteria)
    payload = server.run(options(tmp_path))
    assert len(payload["results"]) == 10
    assert payload["results"][0]["criteria"][0]["value"] is True
    assert payload["results"][0]["score"]["final_score"] == 85.38
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8")) == payload
    assert len(list((tmp_path / "state/attempts").glob("*.json"))) == 1
    assert json.loads((tmp_path / "state/state.json").read_text())["batch_id"] == "synthetic-batch"


def test_resume_after_interrupt_does_not_import_or_enqueue_again(tmp_path):
    server = Server()
    server.interrupt_poll = True
    with pytest.raises(KeyboardInterrupt):
        server.run(options(tmp_path))
    assert not (tmp_path / "results.json").exists()
    server.calls.clear()
    server.run(options(tmp_path))
    assert not any(path in {"imports/dev", "evaluations"} for _, path, _ in server.calls)


def test_ambiguous_enqueue_replays_saved_idempotency_key(tmp_path):
    server = Server()
    server.lose_enqueue = True
    with pytest.raises(cli.SubmissionError, match="backend_connection_error"):
        server.run(options(tmp_path))
    assert server.created
    server.run(options(tmp_path))
    assert len(server.keys) == 2 and server.keys[0] == server.keys[1]
    assert sum(path == "imports/dev" for _, path, _ in server.calls) == 1


def test_failed_only_retry_replays_exact_attempt_ids_without_duplicate_spend(tmp_path):
    server = Server()
    old_id = server.current[1].id
    make_failed(server.current[1])
    server.current[1].status = EvaluationStatus.FAILED
    server.attempts = deepcopy(server.current)
    untouched = server.current[0].model_dump_json()
    server.lose_retry = True
    with pytest.raises(cli.SubmissionError):
        server.run(options(tmp_path))
    server.run(options(tmp_path))
    requests = [body for _, path, body in server.calls if path.endswith("/retry")]
    assert requests == [{"failed_evaluation_ids": [old_id]}] * 2
    assert server.current[0].model_dump_json() == untouched
    assert len(server.attempts) == 11
    archive = json.loads(next((tmp_path / "state/attempts").glob("*.json")).read_text(encoding="utf-8"))
    assert len(archive) == 11 and any(e["status"] == "failed" for e in archive)


def test_zero_retry_budget_exports_real_error_record_instead_of_zero_score(tmp_path):
    server = Server()
    make_failed(server.current[1])
    server.current[1].status = EvaluationStatus.FAILED
    server.attempts = deepcopy(server.current)
    result = server.run(options(tmp_path, "--retry-rounds", "0"))
    assert result["results"][1]["status"] == "error"
    assert result["results"][1]["score"] is None
    assert not any(path.endswith("/retry") for _, path, _ in server.calls)


@pytest.mark.parametrize("count", [0, 9, 11, 65])
def test_wrong_prod_count_never_enqueues_or_overwrites_existing_file(tmp_path, count):
    server = Server(count or 1)
    if count == 0:
        server.conversations = []
    existing = tmp_path / "results.json"
    existing.write_bytes(b"existing valid submission")
    with pytest.raises(cli.SubmissionError, match="source_manifest_mismatch"):
        server.run(options(tmp_path))
    assert existing.read_bytes() == b"existing valid submission"
    assert not any(path == "evaluations" for _, path, _ in server.calls)


@pytest.mark.parametrize("tamper", [
    lambda p: p["results"][0].update(conversation_id="wrong-original-id"),
    lambda p: p["results"][0]["score"].update(final_score=0),
    lambda p: p["results"][0]["criteria"][0].update(rationale="Different human explanation"),
])
def test_bad_download_never_overwrites_existing_result(tmp_path, tamper):
    server = Server()
    server.bad_payload = tamper
    (tmp_path / "results.json").write_bytes(b"previous output")
    with pytest.raises(cli.SubmissionError, match="local_export_invalid"):
        server.run(options(tmp_path))
    assert (tmp_path / "results.json").read_bytes() == b"previous output"


def test_stale_prompt_is_rejected_without_semantic_retry(tmp_path):
    server = Server()
    server.current[0].ai_draft.prompt_version = "synthetic-old-prompt"
    with pytest.raises(cli.SubmissionError, match="stale_prompt"):
        server.run(options(tmp_path))
    assert not any(path.endswith("/retry") for _, path, _ in server.calls)
    assert not (tmp_path / "results.json").exists()


@pytest.mark.parametrize("value", ["https://signed.example.invalid/?token=SECRET", "http://localhost/?secret=SECRET",
    "http://user:SECRET@127.0.0.1:8000", "http://127.0.0.1:8000/private", "https://example.org"])
def test_api_base_rejects_external_or_signed_urls_without_echo(value):
    with pytest.raises(cli.SubmissionError) as error:
        cli.local_base(value)
    assert "SECRET" not in str(error.value) and value not in str(error.value)


def test_http_failure_is_safe_and_does_not_log_private_response(tmp_path):
    server = Server()
    server.http_error = 503
    with pytest.raises(cli.SubmissionError) as error:
        server.run(options(tmp_path))
    assert "HTTP 503" in str(error.value)
    assert "SECRET" not in str(error.value) and "signed.example" not in str(error.value)


def test_changed_source_on_resume_blocks_export(tmp_path):
    server = Server()
    server.interrupt_poll = True
    with pytest.raises(KeyboardInterrupt):
        server.run(options(tmp_path))
    server.conversations[0].messages[0].text += " Synthetic changed source."
    with pytest.raises(cli.SubmissionError, match="source_changed"):
        server.run(options(tmp_path))


def test_new_run_requires_explicit_flag_and_preserves_old_state(tmp_path):
    server = Server()
    server.run(options(tmp_path))
    old_key = server.keys[0]
    server.run(options(tmp_path, "--new-run"))
    assert len(server.keys) == 2 and server.keys[-1] != old_key
    assert len(list((tmp_path / "state/history").glob("*.json"))) == 1


def test_only_one_command_can_own_state_directory(tmp_path):
    with cli.state_lock(tmp_path / "state"):
        with pytest.raises(cli.SubmissionError, match="state_in_use"):
            Server().run(options(tmp_path))


def test_dev_requires_and_accepts_all_65_original_ids(tmp_path):
    server = Server(65, kind="dev")
    result = server.run(options(tmp_path, "--kind", "dev", "--group-id", "DEV"))
    assert len(result["results"]) == 65
    assert {r["conversation_id"] for r in result["results"]} == {c.external_id for c in server.conversations}


def test_dev_wrong_count_blocks_before_model_spend(tmp_path):
    server = Server(10, kind="dev")
    with pytest.raises(cli.SubmissionError, match="source_manifest_mismatch"):
        server.run(options(tmp_path, "--kind", "dev", "--group-id", "DEV"))
    assert not server.created


def test_poll_waits_for_terminal_status_and_keeps_state_on_deadline(tmp_path):
    server = Server()
    server.running_polls = 2
    sleeps = []
    server.run(options(tmp_path), sleep=lambda n: sleeps.append(n))
    assert len(sleeps) == 2
    server.running_polls = 2
    now = [0.0]
    def advance(_):
        now[0] += 2
    (tmp_path / "results.json").write_bytes(b"previous valid results")
    with pytest.raises(cli.SubmissionError, match="wait_timeout"):
        server.run(options(tmp_path, "--wait-seconds", "1"), sleep=advance, clock=lambda: now[0])
    assert (tmp_path / "results.json").read_bytes() == b"previous valid results"


def test_retry_round_budget_does_not_reset_on_resume(tmp_path):
    server = Server()
    make_failed(server.current[1])
    server.current[1].status = EvaluationStatus.FAILED
    server.attempts = deepcopy(server.current)
    server.retry_fails = True
    assert server.run(options(tmp_path))["results"][1]["status"] == "error"
    assert server.run(options(tmp_path))["results"][1]["status"] == "error"
    assert sum(path.endswith("/retry") for _, path, _ in server.calls) == 1
    assert len(server.attempts) == 11


def test_completed_zero_score_is_not_semantically_retried(tmp_path):
    server = Server()
    record = server.current[0]
    for answer, criterion in zip(record.ai_draft.criteria, record.checklist_snapshot.criteria):
        answer.value = 0.0 if criterion.answer_type == "scale" else False
    record.original_score = calculate_score(record.checklist_snapshot, record.ai_draft.criteria)
    result = server.run(options(tmp_path))
    assert result["results"][0]["score"]["final_score"] == 0
    assert not any(path.endswith("/retry") for _, path, _ in server.calls)


def test_invalid_server_preflight_does_not_download_or_overwrite(tmp_path):
    server = Server()
    server.preflight_valid = False
    (tmp_path / "results.json").write_bytes(b"previous valid results")
    with pytest.raises(cli.SubmissionError, match="official_export_invalid"):
        server.run(options(tmp_path))
    assert not any(path.endswith("/official-export") for _, path, _ in server.calls)
    assert (tmp_path / "results.json").read_bytes() == b"previous valid results"


def test_unknown_signed_url_cli_argument_is_not_echoed(capsys):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["run", "--kind", "prod", "--group-id", "SYNTHETIC",
                                "--signed-url", "https://secret.invalid/?token=SECRET"])
    assert "SECRET" not in capsys.readouterr().err
