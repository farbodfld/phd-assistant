from datetime import datetime, timedelta

from app.db.models import Meta
from tests.fakes import FakeGmail, FakeLLM

NOW = datetime(2026, 11, 23, 14, 0)


def _after(q):
    return datetime.fromtimestamp(int(q.split("after:")[1].split()[0]))


def test_inbox_always_rereads_five_days(db):
    from app.db.base import session_scope
    from app.mail.inbox import LAST_CHECK, check_inbox
    with session_scope() as s:
        g = FakeGmail([])
        check_inbox(s, g, FakeLLM({}), NOW)                     # first run: five days back
        assert NOW - _after(g.queries[-1]) >= timedelta(days=5)
        s.merge(Meta(key=LAST_CHECK, value=(NOW - timedelta(hours=3)).isoformat()))
        g2 = FakeGmail([])
        check_inbox(s, g2, FakeLLM({}), NOW)                    # last check 3 hours ago: still five days
        assert NOW - _after(g2.queries[-1]) >= timedelta(days=5)


def test_old_database_gets_new_reminder_columns(tmp_path):
    import sqlite3
    from sqlalchemy import inspect
    from app.db import base
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, source VARCHAR(16), title TEXT, "
                "reminder_1 VARCHAR(32), reminder_2 VARCHAR(32))")
    con.commit(); con.close()
    eng = base.init_db(f"sqlite:///{path}")
    cols = {c["name"] for c in inspect(eng).get_columns("positions")}
    assert {"reminder_3", "reminder_4", "status"} <= cols
