"""Backfill quality: auto replies, bulk mail, loose title matches, admissions by domain."""
from datetime import date, datetime

from sqlalchemy import select

from app.db.models import Contact, EmailMessage, Position
from app.mail.analysis import Relevance
from tests.fakes import FakeGmail, FakeLLM, make_msg

ME = "farbodfooladi@gmail.com"


def _positions(s):
    men = Position(source="findaphd", title="Multimodal Learning for Human-Centered Healthcare",
                   university="University of Cambridge", supervisor_contact="Dr Men", score=60,
                   email_done=True, email_sent="2026-10-01")
    men.contacts.append(Contact(name="Dr Men", email="qm@cam.ac.uk"))
    medsci = Position(source="jobsac", title="PhD in Medical Science AI", university="University of Cambridge",
                      supervisor_contact="", score=55, portal_done=True)
    clin = Position(source="findaphd", title="Clinical AI Decision Support", university="University of Exeter",
                    supervisor_contact="Dr Wijekoon", score=65, email_done=True, email_sent="2026-10-01")
    clin.contacts.append(Contact(name="Dr Wijekoon", email="a.wijekoon@exeter.ac.uk"))
    s.add_all([men, medsci, clin])
    s.flush()
    return men, medsci, clin


def test_parse_detects_auto_reply():
    from app.gmail.parse import parse_message
    a = parse_message(make_msg("a", "t", "x@exeter.ac.uk", ME, "Automatic reply: PhD enquiry", "away",
                               datetime(2026, 10, 2)), ME)
    b = parse_message(make_msg("b", "t", "x@exeter.ac.uk", ME, "Re: PhD", "away", datetime(2026, 10, 2),
                               headers={"Auto-Submitted": "auto-replied"}), ME)
    c = parse_message(make_msg("c", "t", "x@exeter.ac.uk", ME, "Re: PhD", "happy to talk", datetime(2026, 10, 2)), ME)
    assert a.auto_reply and b.auto_reply and not c.auto_reply


def test_backfill_quality(db):
    from app.db.base import session_scope
    from app.mail.inbox import backfill
    from app.mail.reminders import reminders_due
    with session_scope() as s:
        men, medsci, clin = _positions(s)
        msgs = [
            # outbound + auto reply only: reminder must still be due
            make_msg("c1", "tc", ME, "a.wijekoon@exeter.ac.uk", "PhD enquiry: Clinical AI Decision Support", "Dear",
                     datetime(2026, 10, 1)),
            make_msg("c2", "tc", "a.wijekoon@exeter.ac.uk", ME, "Automatic reply: PhD enquiry", "I am away",
                     datetime(2026, 10, 1, 1)),
            # payment confirmation must not attach to Men's position by the word "medical"
            make_msg("p1", "tp", "onlinepayments@findivsales.admin.cam.ac.uk", ME,
                     "Payment Confirmation: PhD in Medical Science AI", "Thanks for paying for Human-Centered care",
                     datetime(2026, 10, 3)),
            # marketing and Wi-Fi are dropped without the model
            make_msg("m1", "tm", "harvardonline@harvardonline.harvard.edu", ME, "Learn AI", "sale",
                     datetime(2026, 10, 4)),
            make_msg("w1", "tw", "itservices@essex.ac.uk", ME, "Wi-Fi password reset", "reset",
                     datetime(2026, 10, 4)),
            # unknown academic thread screened by the model, judged irrelevant
            make_msg("j1", "tj", "teesside-ktp@tees.ac.uk", ME, "Your KTP Associate application", "thanks",
                     datetime(2026, 10, 5)),
        ]
        llm = FakeLLM({"email_relevance": [Relevance(relevant=False, reason="staff job")]})
        r = backfill(s, FakeGmail(msgs), date(2026, 7, 1), llm=llm)
        rows = {m.gmail_id: m for m in s.scalars(select(EmailMessage))}
        assert set(rows) == {"c1", "c2", "p1"}
        assert rows["c2"].category == "auto_reply" and rows["c2"].position_id == clin.id
        assert rows["p1"].position_id == medsci.id
        assert r.skipped == 3
        due = {d.position.title for d in reminders_due(s, date(2026, 11, 10))}
        assert clin.title in due


def test_rebuild_resets_learned_contacts(db):
    from app.db.base import session_scope
    from app.mail.inbox import reset_email_history
    with session_scope() as s:
        men, _, _ = _positions(s)
        men.contacts.append(Contact(name="Extra", email="extra@cam.ac.uk"))
        s.add(EmailMessage(gmail_id="x", thread_id="t", direction="in", sender="a", recipients="b", subject="s",
                           sent_at=datetime(2026, 10, 1), body="", processed=True))
        s.flush()
        reset_email_history(s)
        assert s.scalar(select(EmailMessage.id)) is None
        assert "extra@cam.ac.uk" not in {c.email for c in s.scalars(select(Contact))}
