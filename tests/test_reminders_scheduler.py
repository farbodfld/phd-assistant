from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.db.models import EmailMessage, Position
from app.scheduler import next_run

TZ = "Europe/London"
TIMES = ["09:00", "14:00", "17:00", "20:00"]


def test_next_run():
    z = ZoneInfo(TZ)
    assert next_run(datetime(2026, 11, 10, 8, 0, tzinfo=z), TIMES, TZ).hour == 9
    assert next_run(datetime(2026, 11, 10, 9, 0, tzinfo=z), TIMES, TZ).hour == 14
    n = next_run(datetime(2026, 11, 10, 20, 30, tzinfo=z), TIMES, TZ)
    assert (n.day, n.hour) == (11, 9)


def _pos(s, **kw):
    p = Position(source="findaphd", title=kw.pop("title", "P"), email_done=True, **kw)
    s.add(p)
    s.flush()
    return p


def _mail(s, p, direction, when, thread="t1"):
    s.add(EmailMessage(gmail_id=f"{thread}{direction}{when}", thread_id=thread, position_id=p.id, direction=direction,
                       sender="x", recipients="sup@uni.ac.uk", sent_at=when))


def test_reminders(db):
    from app.db.base import session_scope
    from app.mail.reminders import reminders_due
    today = date(2026, 11, 10)
    with session_scope() as s:
        due1 = _pos(s, title="no reply 28 days")
        _mail(s, due1, "out", datetime(2026, 10, 13))
        replied = _pos(s, title="replied")
        _mail(s, replied, "out", datetime(2026, 10, 13), "t2")
        _mail(s, replied, "in", datetime(2026, 10, 14), "t2")
        recent = _pos(s, title="recent")
        _mail(s, recent, "out", datetime(2026, 11, 5), "t3")
        second = _pos(s, title="second", reminder_1="2026-10-20", email_sent="2026-10-01")
        done = _pos(s, title="two sent", reminder_1="2026-10-01", reminder_2="2026-10-15")
        tracker_only = _pos(s, title="tracker date only", email_sent="2026-11-01")
        due = {d.position.title: d for d in reminders_due(s, today)}
        assert set(due) == {"no reply 28 days", "second"}
        assert due["no reply 28 days"].number == 1 and due["no reply 28 days"].thread_id == "t1"
        assert due["second"].number == 2 and due["second"].thread_id is None
