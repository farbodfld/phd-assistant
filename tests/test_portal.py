"""Phase 8: portal packs (facts only, no model calls). The Portal session writes documents and fills the portal;
nothing here logs in, pays or submits. Farbod presses Submit and tells the bot with /done."""
import json
from datetime import timedelta

from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Application, EmailMessage, Meta, Notification, Position
from app.portal.queue import candidates, invited, mark_submitted, packed, skip, state
from app.portal.run import run_portals
from app.telegram.bot import Bot
from tests.test_outreach import TODAY, _pos, folders  # noqa: F401
from tests.test_telegram import OWNER, FakeAPI, _cb, _msg


def _portal(s, **kw):
    return _pos(s, apply_method="Portal", deadline_date=TODAY + timedelta(days=kw.pop("days", 8)), **kw)


def test_candidates_order_and_filters(db, folders):
    with session_scope() as s:
        far = _portal(s, title="far", days=60)
        low = _portal(s, title="low score", score=40)
        soon = _portal(s, title="soon", days=5)
        later = _portal(s, title="later", days=15)
        rolling_low = _pos(s, title="rolling 65", apply_method="Portal", deadline_date=None, score=65)
        emailed = _pos(s, title="invited", apply_method="Email + Portal", score=50, deadline_date=TODAY + timedelta(days=40))
        s.add(EmailMessage(gmail_id="g1", thread_id="t", position_id=emailed.id, direction="in", sender="x",
                           recipients="me", category="positive", sent_at=TODAY))
        done = _portal(s, title="done", portal_done=True)
        s.flush()
        titles = [p.title for p in candidates(s, TODAY)]
        assert titles == ["invited", "soon", "later"]             # invited first, then by deadline
        assert invited(s, emailed) and not invited(s, soon)


def test_pack_is_facts_only_and_never_private(db, folders, monkeypatch):
    import app.portal.pack as pack_mod
    monkeypatch.setattr(pack_mod, "transcripts_for", lambda s, p: [])
    with session_scope() as s:
        p = _portal(s)
        s.add(EmailMessage(gmail_id="g2", thread_id="t", position_id=p.id, direction="in", sender="Dr Jane Smith",
                           recipients="me", category="positive", summary="Please apply via the portal",
                           sent_at=TODAY))
        rep = run_portals(s, TODAY, limit=3)
        assert rep.packed == [p.id]
        st = state(s, p.id)
        text = open(st["pack"], encoding="utf-8").read()
        data = json.loads(open(st["json"], encoding="utf-8").read())
        assert data["id"] == p.id and data["deadline"]["days_left"] == 8 and data["files"]["cv"]
        assert "Please apply via the portal" in text and "Stop before Submit" in text
        assert "transcripts not found in the documents folder" in st["issues"]
        assert "passport_number" not in json.dumps(data) and "phone" not in data
        assert packed(s, TODAY) == [p] and candidates(s, TODAY) == []


def test_banned_university_flagged(db, folders):
    with session_scope() as s:
        p = _portal(s, university="University of Oxford")
        run_portals(s, TODAY)
        data = json.loads(open(state(s, p.id)["json"], encoding="utf-8").read())
        assert data["ai_writing_banned"] is True
        assert "does not accept AI-written" in open(state(s, p.id)["pack"], encoding="utf-8").read()


def test_morning_auto_packs_only_urgent_or_invited_and_nudges(db, folders):
    with session_scope() as s:
        urgent = _portal(s, title="urgent", days=7, score=80)
        _portal(s, title="not urgent", days=15, score=80)
        _portal(s, title="weak", days=3, score=62)
        rep = run_portals(s, TODAY)
        assert rep.packed == [urgent.id] and rep.waiting == 2
        assert urgent.id in rep.nudged                                    # 7 days left
        assert "پورتال #" in s.scalars(select(Notification)).all()[-1].text
        again = run_portals(s, TODAY)
        assert again.nudged == []                                         # once per day and threshold


def test_drive_upload_when_enabled(db, folders, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "drive_packs", True)

    class FakeDrive:
        calls = []

        def upload(self, pid, title, uni, files):
            self.calls.append((pid, [f.name for f in files]))
            return "FOLDER1"

    d = FakeDrive()
    with session_scope() as s:
        p = _portal(s)
        rep = run_portals(s, TODAY, drive=d)
        assert rep.uploaded == [p.id] and state(s, p.id)["drive_folder"] == "FOLDER1"
        assert d.calls == [(p.id, ["portal_pack.json", "Farbod_Fooladi_Portal_Pack.md"])]


def test_drive_failure_keeps_local_pack(db, folders, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "drive_packs", True)

    class Broken:
        def upload(self, *a):
            raise RuntimeError("no scope")

    with session_scope() as s:
        p = _portal(s)
        rep = run_portals(s, TODAY, drive=Broken())
        assert rep.packed == [p.id] and rep.errors == [f"#{p.id}: not on Drive"]
        assert open(state(s, p.id)["pack"], encoding="utf-8").read()


def test_programme_cap_closes_the_rest(db, folders):
    with session_scope() as s:
        a = _portal(s, title="NW-DMS project A", university="University of Liverpool")
        b = _portal(s, title="NW-DMS project B", university="University of Manchester")
        c = _portal(s, title="NW-DMS project C", university="University of Liverpool")
        other = _portal(s, title="Other project")
        assert mark_submitted(s, a, TODAY) == []
        closed = mark_submitted(s, b, TODAY, "R2")
        assert closed == [c.id] and c.closed and not other.closed


def test_submitted_and_skipped(db, folders):
    with session_scope() as s:
        p = _portal(s)
        q = _portal(s, title="q")
        mark_submitted(s, p, TODAY, "REF1")
        skip(s, q, TODAY)
        s.flush()
        assert p.portal_done and p.portal_submitted == TODAY.isoformat()
        assert s.scalars(select(Application)).one().reference == "REF1"
        assert candidates(s, TODAY) == [] and state(s, q.id)["status"] == "skipped"


def test_bot_portal_card_files_done_skip(db, folders, monkeypatch):
    import app.portal.pack as pack_mod
    monkeypatch.setattr(pack_mod, "transcripts_for", lambda s, p: [])
    monkeypatch.setattr(Bot, "today", lambda self: TODAY)

    class DocAPI(FakeAPI):
        docs = []

        def send_document(self, chat_id, path, caption=""):
            self.docs.append(str(path))
            return 99

    api = DocAPI()
    bot = Bot(api, OWNER, llm_factory=lambda: (_ for _ in ()).throw(AssertionError("no LLM")), sleep=lambda *_: None)
    with session_scope() as s:
        p = _portal(s, days=15)
        q = _portal(s, title="second", days=16)
        s.commit()
        bot.handle(s, _msg("/portals"))
        assert f"#{p.id}" in api.sent[-1][1] and api.sent[-1][2][0][0] == (f"📦 #{p.id}", f"pp:{p.id}")
        bot.handle(s, _cb(f"pp:{p.id}"))                                  # he picks it: the pack is built, no LLM
        assert "پک آماده شد" in api.sent[-1][1] and f"پورتال #{p.id}" in api.sent[-1][1]
        assert api.sent[-1][2][0] == [("📎 پک", f"pf:{p.id}"), ("⏭ اپلای نمی‌کنم", f"px:{p.id}")]
        bot.handle(s, _cb(f"pf:{p.id}"))
        assert any(d.endswith("Portal_Pack.md") for d in api.docs) and any(d.endswith(".json") for d in api.docs)
        bot.handle(s, _msg(f"/done {p.id} ABC123"))
        assert "Submit شد" in api.sent[-1][1] and s.get(Position, p.id).portal_done
        assert s.scalars(select(Application)).one().reference == "ABC123"
        bot.handle(s, _msg(f"/skip {q.id}"))
        assert state(s, q.id)["status"] == "skipped" and packed(s, TODAY) == []
        bot.handle(s, _msg("/done"))
        assert "/done 301" in api.sent[-1][1]


def test_supervisor_saying_portal_marks_invited(db, folders):
    from app.mail.analysis import EmailAnalysis
    from app.mail.inbox import apply_analysis
    from tests.test_inbox import _setup
    from tests.fakes import make_msg
    from app.gmail.parse import parse_message
    with session_scope() as s:
        p = _setup(s)
        from datetime import datetime
        e = parse_message(make_msg("m9", "t9", "Dr Zhipeng Wang <zhipeng.wang@manchester.ac.uk>",
                                   "farbodfooladi@gmail.com", "Re: PhD", "Please apply via the portal.",
                                   datetime(2026, 11, 18, 9)), "farbodfooladi@gmail.com")
        row = EmailMessage(gmail_id="m9", thread_id="t9", position_id=p.id, direction="in", sender="x",
                           recipients="me", sent_at=e.sent_at)
        s.add(row)
        a = EmailAnalysis(category="positive", summary_fa="گفت از پورتال اپلای کن.", status_note="apply via portal",
                          new_apply_method="Email + Portal")
        apply_analysis(s, row, e, p, a, TODAY)
        assert s.get(Meta, f"portal-invited:{p.id}") and invited(s, p)
