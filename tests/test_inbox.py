from datetime import date, datetime

from sqlalchemy import select

from app.db.models import (Application, Contact, Draft, EmailMessage, Insight,
                           Notification, Position, UserTask)
from app.mail.analysis import EmailAnalysis, InsightOut, Relevance
from tests.fakes import FakeGmail, FakeLLM, make_msg

ME = "farbodfooladi@gmail.com"
NOW = datetime(2026, 11, 10, 9, 0)


def _setup(s):
    p = Position(source="findaphd", title="Securing Agentic AI: Trustworthy Delegation",
                 university="University of Manchester", supervisor_contact="Dr Zhipeng Wang",
                 score=70, email_done=True, email_sent="2026-10-13", apply_method="Email + Portal")
    p.contacts.append(Contact(name="Dr Zhipeng Wang", email="zhipeng.wang@manchester.ac.uk"))
    s.add(p)
    s.add(Position(source="findaphd", title="Learning to Optimize for Data Science",
                   university="University of Birmingham", supervisor_contact="Dr Junqi Tang", score=40))
    s.flush()
    return p


def _analysis(**kw):
    base = dict(category="positive", summary_fa="استاد علاقه نشان داده.", status_note="Positive, apply via portal")
    base.update(kw)
    return EmailAnalysis(**base)


def test_rejection_closes_position_and_records_everything(db):
    from app.db.base import session_scope
    from app.mail.inbox import check_inbox
    with session_scope() as s:
        p = _setup(s)
        s.add(Application(position_id=p.id, university="University of Manchester", project="x", outcome="pending"))
        gmail = FakeGmail([make_msg("in1", "t1", "Zhipeng Wang <zhipeng.wang@manchester.ac.uk>", ME,
                                    "Re: Securing Agentic AI", "Sorry, the position is filled.", datetime(2026, 11, 9))])
        llm = FakeLLM({"analyse_email": [_analysis(
            category="rejection", close_position=True, close_reason="Position filled",
            insights=[InsightOut(scope_type="university", scope_value="Manchester",
                                 text="School scholarships for 2027 are already allocated.")],
            needs_reply=True, reply_body="Dear Dr Wang,\n\nThank you — I appreciate it.\n\nBest regards,\nFarbod Fooladi",
            user_tasks=["پورتال منچستر را چک کنید"])]})
        r = check_inbox(s, gmail, llm, NOW)
        assert r.analysed == 1 and r.drafts == 1 and r.closed == [p.title]
        assert p.closed and p.status.startswith("2026-11-10: CLOSED: Position filled")
        assert s.scalar(select(Application.outcome)) == "rejected"
        ins = s.scalars(select(Insight)).one()
        assert ins.scope_type == "university" and ins.source_ref == "Zhipeng Wang"
        d = s.scalars(select(Draft)).one()
        assert "—" not in d.body and d.subject == "Re: Securing Agentic AI" and d.in_reply_to == "<in1@mail>"
        assert d.to == "zhipeng.wang@manchester.ac.uk" and d.status == "pending"
        assert s.scalars(select(UserTask)).one().text.startswith("پورتال")
        note = s.scalars(select(Notification)).one()
        assert "پیش‌نویس" in note.text and note.ref_type == "draft"
        assert llm.calls[0][0] == "analyse_email"


def test_apply_method_change(db):
    from app.db.base import session_scope
    from app.mail.inbox import check_inbox
    with session_scope() as s:
        p = _setup(s)
        gmail = FakeGmail([make_msg("in2", "t2", "zhipeng.wang@manchester.ac.uk", ME, "Re: PhD",
                                    "I do not hold individual discussions.", datetime(2026, 11, 9))])
        llm = FakeLLM({"analyse_email": [_analysis(category="other", new_apply_method="Portal")]})
        check_inbox(s, gmail, llm, NOW)
        assert p.apply_method == "Portal" and not p.closed


def test_alerts_are_skipped_and_unknown_academics_are_screened(db):
    from app.db.base import session_scope
    from app.mail.inbox import check_inbox
    with session_scope() as s:
        _setup(s)
        gmail = FakeGmail([
            make_msg("a1", "t3", "noreply@jobs.ac.uk", ME, "New PhDs", "x", datetime(2026, 11, 9)),
            make_msg("a2", "t4", "dr.smith@essex.ac.uk", ME, "Seminar next week", "x", datetime(2026, 11, 9)),
            make_msg("a4", "t6", "events@essex.ac.uk", ME, "Alumni party", "x", datetime(2026, 11, 9)),
            make_msg("a3", "t5", "someone@gmail.com", ME, "Hello", "x", datetime(2026, 11, 9)),
        ])
        llm = FakeLLM({"email_relevance": [Relevance(relevant=False, reason="event")]})
        r = check_inbox(s, gmail, llm, NOW)
        assert r.skipped == 4 and r.stored == 0
        assert [c[0] for c in llm.calls] == ["email_relevance"]      # bulk mailboxes never reach the LLM


def test_idempotent(db):
    from app.db.base import session_scope
    from app.mail.inbox import check_inbox
    with session_scope() as s:
        _setup(s)
        msg = make_msg("in3", "t6", "zhipeng.wang@manchester.ac.uk", ME, "Re: x", "ok", datetime(2026, 11, 9))
        llm = FakeLLM({"analyse_email": [_analysis()]})
        check_inbox(s, FakeGmail([msg]), llm, NOW)
        r = check_inbox(s, FakeGmail([msg]), llm, NOW)
        assert r.stored == 0 and len(llm.calls) == 1


def test_backfill_links_by_surname_and_learns_contact(db):
    from app.db.base import session_scope
    from app.mail.inbox import backfill
    with session_scope() as s:
        _setup(s)
        gmail = FakeGmail([make_msg("o1", "t7", ME, "junqi.tang@bham.ac.uk",
                                    "PhD enquiry: Learning to Optimize for Data Science", "Dear Dr Tang", datetime(2026, 8, 26))])
        r = backfill(s, gmail, date(2026, 7, 1))
        row = s.scalars(select(EmailMessage)).one()
        p = s.get(Position, row.position_id)
        assert r.stored == 1 and p.title.startswith("Learning to Optimize") and row.processed
        assert "junqi.tang@bham.ac.uk" in [c.email for c in p.contacts]
