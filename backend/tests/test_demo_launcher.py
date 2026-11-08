"""Public access is explicit; existing local credentials and .env stay intact."""
import json
import sys

from scripts import run_demo


def test_public_launch_does_not_read_or_modify_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(run_demo, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_demo", "--public"])
    monkeypatch.setattr(run_demo.os, "chdir", lambda _: None)
    monkeypatch.setenv("CQA_DEMO_PUBLIC", "0")
    monkeypatch.setenv("CQA_DEMO_PASSWORD", "previous-synthetic-password")
    (tmp_path / "private").mkdir()
    credentials = tmp_path / "private" / "demo-access.json"
    credentials.write_bytes(b"intentionally unreadable synthetic credential sentinel")
    env = tmp_path / ".env"
    env.write_bytes(b"synthetic sentinel")
    calls = []
    monkeypatch.setattr(run_demo.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    run_demo.main()
    assert run_demo.os.environ["CQA_DEMO_PUBLIC"] == "1"
    assert "CQA_DEMO_PASSWORD" not in run_demo.os.environ
    assert credentials.read_bytes() == b"intentionally unreadable synthetic credential sentinel"
    assert env.read_bytes() == b"synthetic sentinel"
    assert calls[0][1]["host"] == "127.0.0.1" and calls[0][1]["factory"] is True


def test_normal_launch_resets_inherited_public_access(tmp_path, monkeypatch):
    monkeypatch.setattr(run_demo, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_demo"])
    monkeypatch.setattr(run_demo.os, "chdir", lambda _: None)
    monkeypatch.setenv("CQA_DEMO_PUBLIC", "1")
    monkeypatch.setenv("CQA_DEMO_PASSWORD", "previous-synthetic-password")
    (tmp_path / "private").mkdir()
    credentials = tmp_path / "private" / "demo-access.json"
    saved = {"username": "demo", "password": "synthetic-existing-password-for-tests"}
    original = json.dumps(saved).encode()
    credentials.write_bytes(original)
    monkeypatch.setattr(run_demo.uvicorn, "run", lambda *args, **kwargs: None)
    run_demo.main()
    assert run_demo.os.environ["CQA_DEMO_PUBLIC"] == "0"
    assert run_demo.os.environ["CQA_DEMO_PASSWORD"] == saved["password"]
    assert credentials.read_bytes() == original
