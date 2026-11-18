"""`phd serve`: inbox checks from 08:00 to 20:00 in the main loop, discovery once a day in its own thread."""
import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.base import session_scope
from app.db.models import Notification, Run
from app.discovery import worker
from tests.fakes import FakeGmail, FakeLLM


def test_default_inbox_times_start_at_eight():
    assert Settings.model_fields["inbox_check_times"].default == ["08:00", "14:00", "17:00", "20:00"]


def test_inbox_check_records_a_run(db, monkeypatch):
    monkeypatch.setattr(worker, "GmailClient", lambda: FakeGmail([]))
    monkeypatch.setattr(worker, "LLMClient", lambda **kw: FakeLLM({}))
    report = worker.inbox_check()
    assert report.fetched == 0 and not report.errors
    with session_scope() as s:
        run = s.scalars(select(Run)).one()
        assert (run.kind, run.ok) == ("inbox", True) and run.finished_at is not None


def test_inbox_check_failure_is_reported(db, monkeypatch):
    def broken():
        raise RuntimeError("Gmail token is invalid")

    monkeypatch.setattr(worker, "GmailClient", broken)
    with pytest.raises(RuntimeError):
        worker.inbox_check()
    with session_scope() as s:
        assert s.scalars(select(Run.ok)).one() is False
        assert "اینباکس" in s.scalars(select(Notification.text)).one()


def test_serve_runs_inbox_and_discovery_separately(monkeypatch):
    started, scheduled = [], {}

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            self.target, self.daemon = target, daemon

        def start(self):
            started.append((self.target, self.daemon))

    monkeypatch.setattr(worker.threading, "Thread", FakeThread)
    monkeypatch.setattr(worker, "run_forever", lambda jobs, tz: scheduled.update(jobs))
    monkeypatch.setattr(worker.get_settings(), "inbox_check_times", ["08:00", "14:00", "17:00", "20:00"])
    worker.serve()
    assert started == [(worker._discovery_loop, True)]
    assert scheduled == {"inbox": (["08:00", "14:00", "17:00", "20:00"], worker.inbox_check)}
