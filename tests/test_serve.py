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


def test_serve_one_schedule_morning_then_inbox_only(db, monkeypatch):
    started, scheduled, ran = [], {}, []

    class FakeThread:
        def __init__(self, target, name, daemon, args=()):
            self.name = name

        def start(self):
            started.append(self.name)

    monkeypatch.setattr(worker.threading, "Thread", FakeThread)
    monkeypatch.setattr(worker, "run_forever", lambda jobs, tz: scheduled.update(jobs))
    monkeypatch.setattr(worker, "morning", lambda: ran.append("morning"))
    st = worker.get_settings()
    monkeypatch.setattr(st, "inbox_check_times", ["08:00", "14:00", "17:00", "20:00"])
    monkeypatch.setattr(st, "daily_run_time", "00:00")              # "now" is after the morning time
    import app.telegram.bot as tb
    monkeypatch.setattr(tb, "make_bot", lambda: None)
    worker.serve()
    assert started == [] and ran == ["morning"]                     # missed morning caught up
    assert scheduled["morning"] == (["00:00"], worker.morning)
    assert scheduled["inbox"][1] is worker.afternoon


def test_morning_runs_each_step_in_order_and_announces_it(db, monkeypatch):
    from sqlalchemy import select
    from app.db.models import Notification
    steps = []
    monkeypatch.setattr(worker, "inbox_check", lambda: steps.append("inbox"))
    monkeypatch.setattr(worker, "discover", lambda scheduled, source: steps.append(source))
    monkeypatch.setattr(worker, "outreach", lambda review=True: steps.append("outreach"))
    from app.portal.run import PortalReport
    monkeypatch.setattr(worker, "portals", lambda: (steps.append("portals"), PortalReport(packed=[1], waiting=4))[1])
    worker.morning()
    assert steps == ["inbox", "jobsac", "findaphd", "outreach", "portals"]
    with session_scope() as s:
        kinds = [(n.kind, n.text[:2]) for n in s.scalars(select(Notification).order_by(Notification.id))]
    texts = [k for k, _ in kinds]
    assert texts == ["stage", "report", "stage", "stage", "stage", "stage", "stage", "stage", "review"]
    with session_scope() as s:
        msgs = [n.text for n in s.scalars(select(Notification).order_by(Notification.id))]
    assert "شروع بررسی inbox" in msgs[0] and "پیام جدیدی" in msgs[1] and "تموم شد" in msgs[2]
    assert "jobs.ac.uk" in msgs[3] and "جستجوی دو سایت تموم شد" in msgs[4]


def test_afternoon_is_inbox_only(db, monkeypatch):
    steps = []
    monkeypatch.setattr(worker, "inbox_check", lambda: steps.append("inbox"))
    monkeypatch.setattr(worker, "discover", lambda **kw: steps.append("discover"))
    worker.afternoon()
    assert steps == ["inbox"]
