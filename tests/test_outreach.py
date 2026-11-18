"""Phase 5: first emails, reminders, statements/proposals and the Gmail drafts bridge."""
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.base import session_scope
from app.db.models import Contact, Document, Draft, EmailMessage, Meta, Notification, Position, UserTask
from app.llm.client import BudgetExceeded
from app.outreach.documents import DocText, Section
from app.outreach.gmail_sync import push_drafts, sync_drafts
from app.outreach.initial import InitialEmail, draft_initial, needs_initial
from app.outreach.recipients import recipient
from app.outreach.reminder import ReminderEmail, draft_due_reminders
from app.outreach.run import run_outreach
from tests.fakes import FakeLLM

TODAY = date(2026, 11, 18)
BODY = ("Dear Dr Smith,\n\nI am writing about your project on robots - it fits my work.\n\n"
        "Best regards,\nFarbod Fooladi")


@pytest.fixture
def folders(tmp_path, monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "positions_dir", tmp_path / "positions")
    monkeypatch.setattr(st, "documents_dir", tmp_path / "documents")
    monkeypatch.setattr(st, "findaphd_archive_dir", tmp_path / "findaphd")
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "Farbod_Fooladi_CV.pdf").write_bytes(b"%PDF-1.4 cv")
    return tmp_path


def _pos(s, **kw):
    base = dict(source="findaphd", title="Learning robots", university="University of Leeds", score=80,
                apply_method="Email", supervisor_contact="Dr Jane Smith (j.smith@leeds.ac.uk)",
                link="https://www.findaphd.com/phds/project/robots/?p1", deadline_date=date(2027, 1, 10))
    base.update(kw)
    p = Position(**base)
    s.add(p)
    s.flush()
    return p


def _email(**kw):
    return InitialEmail(**{"skip": False, "subject": "PhD enquiry: Learning robots", "body": BODY, **kw})


def _doc():
    return DocText(title="Research proposal", sections=[Section(heading="Aims", paragraphs=["Aim one."])])


class FakeGmail:
    def __init__(self):
        self.drafts, self.sent, self.deleted = {}, {}, set()

    def create_draft(self, to, subject, body, *, thread_id=None, in_reply_to=None, attachments=()):
        n = len(self.drafts) + 1
        self.drafts[f"d{n}"] = dict(to=to, subject=subject, thread=thread_id or f"t{n}",
                                    attachments=[a.name for a in attachments], in_reply_to=in_reply_to)
        return {"draft": f"d{n}", "message": f"m{n}", "thread": thread_id or f"t{n}"}

    def draft_exists(self, draft_id):
        return draft_id in self.drafts and draft_id not in self.deleted

    def sent_in_thread(self, thread_id, after_ms):
        return self.sent.get(thread_id)

    def send_draft(self, draft_id):
        self.sent[self.drafts[draft_id]["thread"]] = f"s-{draft_id}"
        return f"s-{draft_id}"


def test_recipient_prefers_the_supervisor_over_a_generic_address():
    p = Position(title="x", supervisor_contact="See advert")
    assert recipient(p, "Enquiries: pgr-admissions@leeds.ac.uk or Dr A Jones a.jones@leeds.ac.uk.") == \
        ("", "a.jones@leeds.ac.uk")
    assert recipient(p, "Contact pgr@leeds.ac.uk") == ("", "pgr@leeds.ac.uk")
    assert recipient(p, "no address") is None


def test_queue_skips_done_closed_late_portal_and_already_written(db, folders):
    with session_scope() as s:
        ok = _pos(s, title="A")
        _pos(s, title="closed", closed=True)
        _pos(s, title="emailed", email_done=True)
        _pos(s, title="late", deadline_date=date(2026, 11, 1))
        _pos(s, title="portal only", apply_method="Portal")
        old = _pos(s, title="text in folder")
        s.add(Document(position_id=old.id, folder_name="f", filename="Email_to_supervisor.txt", kind="email_text"))
        out = _pos(s, title="out mail")
        s.add(EmailMessage(gmail_id="g", thread_id="t", position_id=out.id, direction="out", sender="me",
                           sent_at=datetime(2026, 11, 1)))
        both = _pos(s, title="B", apply_method="Email + Portal", deadline_date=date(2026, 12, 1))
        assert [p.title for p in needs_initial(s, TODAY)] == ["B", "A"]
        assert ok.id


def test_first_email_draft_with_cv_and_contact(db, folders):
    with session_scope() as s:
        p = _pos(s)
        llm = FakeLLM({"initial_email": [_email(notes_fa=["یادداشت"])]})
        d = draft_initial(s, p, llm, TODAY)
        assert d.to == "j.smith@leeds.ac.uk" and d.kind == "initial" and " - " not in d.body
        assert len(d.attachments) == 1 and d.attachments[0].endswith("Farbod_Fooladi_CV.pdf")
        assert (folders / "positions" / p.folder_name / "Farbod_Fooladi_CV.pdf").exists()
        assert "Learning robots -- England -- Dr Jane Smith (Leeds)" == p.folder_name
        assert "Dr Jane Smith" in llm.calls[0][1] and not d.issues
        assert s.scalars(select(Notification).where(Notification.ref_type == "draft")).one()
        assert not needs_initial(s, TODAY)


def test_missing_cv_is_an_issue_not_a_silent_send(db, folders):
    (folders / "documents" / "Farbod_Fooladi_CV.pdf").unlink()
    with session_scope() as s:
        d = draft_initial(s, _pos(s), FakeLLM({"initial_email": [_email()]}), TODAY)
        assert d.attachments == [] and any("missing CV" in i for i in d.issues)


def test_no_address_creates_a_task_once(db, folders):
    with session_scope() as s:
        p = _pos(s, supervisor_contact="See advert")
        llm = FakeLLM({})
        assert draft_initial(s, p, llm, TODAY) is None and llm.calls == []
        assert s.scalars(select(UserTask)).one() and not needs_initial(s, TODAY)


def test_advert_says_do_not_email(db, folders):
    with session_scope() as s:
        p = _pos(s)
        llm = FakeLLM({"initial_email": [InitialEmail(skip=True, skip_reason="Apply directly; no enquiries")]})
        assert draft_initial(s, p, llm, TODAY) is None
        assert s.get(Meta, f"outreach:{p.id}").value.startswith("skip") and "No first email" in p.status


def test_proposal_attached_when_the_advert_asks(db, folders):
    with session_scope() as s:
        p = _pos(s)
        llm = FakeLLM({"initial_email": [_email(needs_proposal=True)], "research_proposal": [_doc()]})
        d = draft_initial(s, p, llm, TODAY)
        names = [Path(a).name for a in d.attachments]
        assert names == ["Farbod_Fooladi_CV.pdf", "Farbod_Fooladi_Research_Proposal_v1.pdf"]
        folder = folders / "positions" / p.folder_name
        assert (folder / "Farbod_Fooladi_Research_Proposal_v1.docx").exists()
        assert {x.kind for x in s.scalars(select(Document))} == {"cv", "proposal"}


def test_oxford_statement_is_a_reference_draft_and_not_attached(db, folders):
    with session_scope() as s:
        p = _pos(s, university="University of Oxford")
        llm = FakeLLM({"initial_email": [_email(needs_statement=True)], "personal_statement": [_doc()]})
        d = draft_initial(s, p, llm, TODAY)
        assert len(d.attachments) == 1
        folder = folders / "positions" / p.folder_name
        assert (folder / "Farbod_Fooladi_Personal_Statement_REFERENCE_DRAFT_v1.pdf").exists()
        assert any("AI" in t.text for t in s.scalars(select(UserTask)))


def test_gmail_bridge_records_sent_and_deleted(db, folders):
    gmail = FakeGmail()
    with session_scope() as s:
        p1, p2 = _pos(s, title="one"), _pos(s, title="two")
        llm = FakeLLM({"initial_email": [_email(), _email()]})
        d1, d2 = draft_initial(s, p1, llm, TODAY), draft_initial(s, p2, llm, TODAY)
        s.commit()
        assert push_drafts(s, gmail) == 2 and push_drafts(s, gmail) == 0
        assert gmail.drafts["d1"]["attachments"] == ["Farbod_Fooladi_CV.pdf"] and d1.thread_id == "t1"
        gmail.sent["t1"] = "sent1"
        gmail.deleted.add("d2")
        assert sync_drafts(s, gmail, TODAY) == {"sent": [d1.id], "deleted": [d2.id]}
        assert (d1.status, d2.status) == ("sent", "denied")
        assert p1.email_done and p1.email_sent == "2026-11-18" and not p2.email_done


def test_reminder_is_a_reply_in_the_same_thread(db, folders):
    with session_scope() as s:
        p = _pos(s, email_done=True)
        s.add(EmailMessage(gmail_id="g1", thread_id="th", position_id=p.id, direction="out", sender="me",
                           recipients="j.smith@leeds.ac.uk", subject="PhD enquiry: Learning robots",
                           sent_at=datetime(2026, 10, 20), message_id_header="<abc@mail>", body="Dear Dr Smith"))
        llm = FakeLLM({"reminder_email": [ReminderEmail(body="Dear Dr Smith,\n\nAny update?\n\nBest regards,\nFarbod Fooladi")]})
        [d] = draft_due_reminders(s, llm, TODAY)
        assert (d.thread_id, d.in_reply_to, d.subject) == ("th", "<abc@mail>", "Re: PhD enquiry: Learning robots")
        assert draft_due_reminders(s, llm, TODAY) == []               # one open reminder at a time
        gmail = FakeGmail()
        push_drafts(s, gmail)
        gmail.sent["th"] = "r1"
        sync_drafts(s, gmail, TODAY)
        assert p.reminder_1 == "2026-11-18"


def test_budget_stops_and_leaves_the_rest_for_tomorrow(db, folders):
    class Budget(FakeLLM):
        def complete(self, task, prompt, **kw):
            if self.calls:
                raise BudgetExceeded("daily budget")
            return super().complete(task, prompt, **kw)

    with session_scope() as s:
        for t in ("a", "b", "c"):
            _pos(s, title=t)
        s.commit()
        r = run_outreach(s, Budget({"initial_email": [_email()]}), TODAY, gmail=FakeGmail())
        assert (r.initial, r.waiting, r.pushed) == (1, 2, 1) and "budget" in r.errors[0]
        assert len(s.scalars(select(Draft)).all()) == 1


def test_generic_addresses_and_semicolon_names():
    from app.outreach.recipients import is_generic
    assert is_generic("nw-dmsrecruitment@liverpool.ac.uk") and is_generic("pgr-admissions@leeds.ac.uk")
    assert not is_generic("p.muhle-karbe@bham.ac.uk") and not is_generic("j.smith@leeds.ac.uk")
    p = Position(title="x", supervisor_contact="Dr A Jones ; Dr B Lee (a.jones@leeds.ac.uk)")
    assert recipient(p) == ("Dr A Jones", "a.jones@leeds.ac.uk")


def test_cost_log_can_be_written_while_a_workflow_session_reads(db):
    """Regression: 'database is locked' when the LLM log committed while the outreach session was open."""
    from app.db.models import LLMCall
    with session_scope() as s:
        list(s.scalars(select(Position)))            # the workflow session now holds a read transaction
        s.add(Position(source="findaphd", title="x"))
        s.flush()                                     # and a pending write
        s.commit()
        list(s.scalars(select(Position)))
        with session_scope() as other:
            other.add(LLMCall(task="t", model="m"))
        assert s.scalar(select(LLMCall.task)) == "t"


def test_statement_and_proposal_in_one_draft_do_not_lock_the_cost_log(db, folders):
    from app.db.models import LLMCall

    class Logging(FakeLLM):
        def complete(self, task, prompt, **kw):
            with session_scope() as other:           # like LLMClient._record
                other.add(LLMCall(task=task, model="m"))
            return super().complete(task, prompt, **kw)

    with session_scope() as s:
        p = _pos(s)
        llm = Logging({"initial_email": [_email(needs_statement=True, needs_proposal=True)],
                       "personal_statement": [_doc()], "research_proposal": [_doc()]})
        d = draft_initial(s, p, llm, TODAY)
        assert len(d.attachments) == 3
        assert len(s.scalars(select(LLMCall)).all()) == 3


def test_folder_name_is_clean(db, folders):
    from app.outreach.documents import folder_for
    with session_scope() as s:
        p = _pos(s, title="Mapping immune organotropism: how inflammation targets",
                 supervisor_contact="Dr Juma El-awaisi ; Dr Laura Bravo Merodio", university="University of Birmingham")
        assert folder_for(p).name == "Mapping immune organotropism how inflammation targets -- England -- Dr Juma El-awaisi (Birmingham)"


def test_send_approved_drafts_only(db, folders):
    from app.outreach.gmail_sync import send_approved
    gmail = FakeGmail()
    with session_scope() as s:
        p1, p2, p3 = _pos(s, title="one"), _pos(s, title="two"), _pos(s, title="three")
        llm = FakeLLM({"initial_email": [_email(), _email(), _email()]})
        d1, d2, d3 = (draft_initial(s, p, llm, TODAY) for p in (p1, p2, p3))
        s.commit()
        push_drafts(s, gmail)
        gmail.deleted.add("d3")
        r = send_approved(s, gmail, [d1.id, d3.id], TODAY)
        assert r == {"sent": [d1.id], "missing": [d3.id], "recorded": [d1.id]}
        assert d1.status == "sent" and p1.email_done and d2.status == "pending" and not p2.email_done


def test_send_continues_past_a_draft_already_sent_from_gmail(db, folders):
    from app.outreach.gmail_sync import send_approved

    class Picky(FakeGmail):
        def send_draft(self, draft_id):
            if draft_id == "d1":
                self.sent["t1"] = "by-hand"
                raise RuntimeError("Message not a draft")
            return super().send_draft(draft_id)

    gmail = Picky()
    with session_scope() as s:
        p1, p2 = _pos(s, title="one"), _pos(s, title="two")
        llm = FakeLLM({"initial_email": [_email(), _email()]})
        d1, d2 = draft_initial(s, p1, llm, TODAY), draft_initial(s, p2, llm, TODAY)
        s.commit()
        push_drafts(s, gmail)
        r = send_approved(s, gmail, [d1.id, d2.id], TODAY)
        assert r["sent"] == [d2.id] and sorted(r["recorded"]) == [d1.id, d2.id]
        assert p1.email_done and p2.email_done
