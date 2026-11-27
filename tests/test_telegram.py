"""Phase 6: the Telegram bot. Nothing reaches a professor without Approve."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Draft, Meta, Notification, Position
from app.outreach.gmail_sync import push_drafts
from app.outreach.initial import draft_initial, needs_initial
from app.telegram.api import chunks
from app.telegram.bot import Bot, EditedEmail
from tests.fakes import FakeLLM
from tests.test_outreach import FakeGmail, TODAY, _email, _pos, folders  # noqa: F401

OWNER = 777


class FakeAPI:
    def __init__(self):
        self.sent, self.closed, self.answered, self.updates = [], [], [], []

    def send(self, chat_id, text, rows=None):
        import html
        plain = html.unescape(text.translate({0x200F: None, 0x2066: None, 0x2069: None}))
        self.sent.append((chat_id, plain, rows))   # assertions read the text without bidi marks
        return len(self.sent)

    def edit_buttons(self, chat_id, message_id, rows=None):
        self.closed.append(message_id)

    def answer(self, callback_id, text=""):
        self.answered.append(callback_id)

    def typing(self, chat_id):
        pass

    def get_updates(self, offset, timeout=30):
        out, self.updates = self.updates, []
        return out


class SendingGmail(FakeGmail):
    def __init__(self):
        super().__init__()
        self.direct = []

    def send(self, to, subject, body, *, thread_id=None, in_reply_to=None, attachments=()):
        self.direct.append((to, subject, [a.name for a in attachments]))
        return "direct-1"

    def send_draft(self, draft_id):
        return "via-draft"

    def delete_draft(self, draft_id):
        self.deleted.add(draft_id)


def _bot(gmail=None, llm=None):
    api = FakeAPI()
    return Bot(api, OWNER, gmail_factory=lambda: gmail or SendingGmail(), llm_factory=lambda: llm,
               sleep=lambda *_: None), api


def _cb(data, who=OWNER):
    return {"callback_query": {"id": "c1", "from": {"id": who}, "data": data}}


def _msg(text, who=OWNER):
    return {"message": {"text": text, "from": {"id": who}, "chat": {"id": who}}}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(Bot, "today", lambda self: TODAY)


def _draft(s, **kw):
    p = _pos(s, **kw)
    d = draft_initial(s, p, FakeLLM({"initial_email": [_email()]}), TODAY)
    s.commit()
    return p, d


def test_first_flush_sets_a_baseline_and_shows_no_old_messages(db, folders):
    bot, api = _bot()
    with session_scope() as s:
        s.add(Notification(kind="report", text="old discovery report"))
        p, d = _draft(s)
        bot.flush(s)
        texts = "".join(t for _, t, _ in api.sent)
        assert "old discovery report" not in texts and "ربات" in texts and d.body not in texts


def test_steps_arrive_in_order_one_message_each_and_drafts_wait_for_the_review(db, folders):
    bot, api = _bot()
    with session_scope() as s:
        bot.flush(s)
        api.sent.clear()
        p, d = _draft(s)                                       # its notification is not pushed by itself
        s.add_all([Notification(kind="stage", ref_type="pipeline", text="📥 08:00 | شروع بررسی inbox"),
                   Notification(kind="email", ref_type="email", text="single inbox item"),
                   Notification(kind="report", ref_type="pipeline", text="📭 پیام جدیدی از استادها نیومده."),
                   Notification(kind="stage", ref_type="pipeline", text="✅ بررسی inbox تموم شد"),
                   Notification(kind="review", ref_type="pipeline", text="")])
        s.commit()
        bot.flush(s)
        texts = [t for _, t, _ in api.sent]
        assert len(texts) == 4 and "شروع بررسی inbox" in texts[0] and "single inbox item" not in "".join(texts)
        assert "پیام جدیدی" in texts[1] and "تموم شد" in texts[2]
        assert "جواب به ایمیل استادها: ۰" in texts[3] and "ایمیل‌های اولیه جدید: ۱" in texts[3]
        assert "پورتال" in texts[3] and api.sent[-1][2] == [[("▶️ شروع", "stage:initial"), ("بعداً", "later")]]
        assert bot.flush(s) == 0


def test_stages_in_order_numbered_and_checked_before_moving_on(db, folders):
    from datetime import datetime
    from app.db.models import EmailMessage
    from app.outreach.reminder import ReminderEmail, draft_due_reminders
    gmail = SendingGmail()
    bot, api = _bot(gmail)
    with session_scope() as s:
        p, d1 = _draft(s, title="new one")
        old = _pos(s, title="old", email_done=True, email_sent="2026-10-01")
        s.add(EmailMessage(gmail_id="g1", thread_id="th", position_id=old.id, direction="out", sender="me",
                           recipients="j.smith@leeds.ac.uk", subject="PhD enquiry", sent_at=datetime(2026, 10, 1)))
        body = ReminderEmail(body="Dear Dr Smith,\n\nAny update?\n\nBest regards,\nFarbod Fooladi")
        [r] = draft_due_reminders(s, FakeLLM({"reminder_email": [body]}), TODAY)
        assert bot.next_stage(s) == "initial"                       # new emails before reminders
        bot.handle(s, _cb("stage:initial"))
        texts = [t for _, t, _ in api.sent]
        assert "ایمیل‌های اولیه جدید" in texts[-3] and "۱ از ۱" in texts[-2] and "score: 80" in texts[-2]
        assert api.sent[-1][2] == [[("✅ این مرحله تموم شد", "done:initial")]]
        bot.handle(s, _cb("done:initial"))                          # not finished yet
        assert "هنوز" in api.sent[-1][1] and f"#{d1.id}" in api.sent[-1][1]
        bot.handle(s, _cb(f"a:{d1.id}"))
        bot.handle(s, _cb("done:initial"))
        assert "یادآوری‌ها" in api.sent[-1][1] and api.sent[-1][2][0][0] == ("▶️ بریم", "stage:reminder")
        bot.handle(s, _cb("stage:reminder"))
        assert "آخرین ایمیل یا یادآوری قبلی: 2026-10-01" in api.sent[-2][1]
        bot.handle(s, _cb(f"d:{r.id}"))
        bot.handle(s, _cb("done:reminder"))
        assert "همه ایمیل‌ها بررسی شدن" in api.sent[-1][1]


def test_hash_number_shows_that_draft(db, folders):
    bot, api = _bot()
    with session_scope() as s:
        p, d = _draft(s)
        bot.handle(s, _msg(f"#{d.id}"))
        assert d.body in api.sent[-1][1] and api.sent[-1][2] == [[("✅ Approve", f"a:{d.id}"),
                                                                 ("✏️ Edit", f"e:{d.id}"), ("❌ Deny", f"d:{d.id}")]]


def test_rtl_lines_keep_english_intact():
    from app.telegram.fmt import LRI, PDI, RLM, rtl
    line = rtl("گیرنده: j.smith@leeds.ac.uk")
    assert line.startswith(RLM) and f"{LRI}j.smith@leeds.ac.uk{PDI}" in line
    assert rtl("Dear Dr Smith,") == "Dear Dr Smith,"                      # the English email is untouched


def test_approve_sends_the_gmail_draft_and_records_it(db, folders):
    gmail = SendingGmail()
    bot, api = _bot(gmail)
    with session_scope() as s:
        p, d = _draft(s)
        push_drafts(s, gmail)
        bot.handle(s, _cb(f"a:{d.id}"))
        assert d.status == "sent" and d.sent_gmail_id == "via-draft"
        assert p.email_done and p.email_sent == TODAY.isoformat()
        assert "فرستاده شد" in api.sent[-1][1]
        bot.handle(s, _cb(f"a:{d.id}"))
        assert "قبلاً sent" in api.sent[-1][1]                  # never twice


def test_approve_without_gmail_draft_sends_directly_with_cv(db, folders):
    gmail = SendingGmail()
    bot, _ = _bot(gmail)
    with session_scope() as s:
        p, d = _draft(s)
        bot.handle(s, _cb(f"a:{d.id}"))
        assert gmail.direct == [("j.smith@leeds.ac.uk", d.subject, ["Farbod_Fooladi_CV.pdf"])]
        assert d.status == "sent"


def test_deny_is_final_and_deletes_the_gmail_draft(db, folders):
    gmail = SendingGmail()
    bot, _ = _bot(gmail)
    with session_scope() as s:
        p, d = _draft(s)
        push_drafts(s, gmail)
        bot.handle(s, _cb(f"d:{d.id}"))
        assert d.status == "denied" and "d1" in gmail.deleted
        assert not needs_initial(s, TODAY)                         # not drafted again tomorrow


def test_edit_with_full_text(db, folders):
    gmail = SendingGmail()
    bot, api = _bot(gmail)
    with session_scope() as s:
        p, d = _draft(s)
        push_drafts(s, gmail)
        bot.handle(s, _cb(f"e:{d.id}"))
        assert s.get(Meta, "tg:editing").value == d.id
        bot.handle(s, _msg("Dear Dr Smith,\n\nShorter text - with a dash.\n\nBest regards,\nFarbod Fooladi"))
        assert d.version == 2 and " - " not in d.body and "Shorter text" in d.body
        assert s.get(Meta, "tg:editing") is None and "d1" in gmail.deleted
        assert s.get(Meta, f"gmail-draft:{d.id}").value["draft"] == "d2"
        assert "نسخه ویرایش‌شده" in api.sent[-1][1]


def test_edit_by_instruction_uses_the_model(db, folders):
    llm = FakeLLM({"edit_email": [EditedEmail(subject="New subject", body="Dear Dr Smith,\n\nShort.\n\nBest regards,\nFarbod Fooladi")]})
    bot, _ = _bot(llm=llm)
    with session_scope() as s:
        p, d = _draft(s)
        bot.handle(s, _cb(f"e:{d.id}"))
        bot.handle(s, _msg("کوتاه‌ترش کن"))
        assert "کوتاه‌ترش کن" in llm.calls[0][1] and d.subject == "New subject" and d.body.endswith("Fooladi")


def test_approve_all(db, folders):
    gmail = SendingGmail()
    bot, api = _bot(gmail)
    with session_scope() as s:
        (_, d1), (_, d2) = _draft(s, title="one"), _draft(s, title="two")
        bot.handle(s, _cb("all:initial"))
        assert d1.status == d2.status == "pending" and "مطمئنی" in api.sent[-1][1]   # asks first
        bot.handle(s, _cb("allyes:initial"))
        assert d1.status == d2.status == "sent"
        assert any(t.count("فرستاده شد") == 2 for _, t, _ in api.sent)


def test_strangers_are_ignored_and_setup_reveals_the_chat_id(db, folders):
    bot, api = _bot()
    with session_scope() as s:
        p, d = _draft(s)
        bot.handle(s, _cb(f"a:{d.id}", who=999))
        bot.handle(s, _msg("/drafts", who=999))
        assert d.status == "pending" and api.sent == []
    setup = Bot(FakeAPI(), 0, sleep=lambda *_: None)
    with session_scope() as s:
        setup.handle(s, _msg("/start", who=4242))
        assert "TELEGRAM_OWNER_ID=4242" in setup.api.sent[0][1]


def test_denied_reminder_is_not_drafted_again(db, folders):
    from datetime import datetime
    from app.db.models import EmailMessage
    from app.outreach.reminder import ReminderEmail, draft_due_reminders
    bot, _ = _bot()
    with session_scope() as s:
        p = _pos(s, email_done=True)
        s.add(EmailMessage(gmail_id="g1", thread_id="th", position_id=p.id, direction="out", sender="me",
                           recipients="j.smith@leeds.ac.uk", subject="PhD enquiry", sent_at=datetime(2026, 10, 20)))
        body = ReminderEmail(body="Dear Dr Smith,\n\nAny update?\n\nBest regards,\nFarbod Fooladi")
        [d] = draft_due_reminders(s, FakeLLM({"reminder_email": [body, body]}), TODAY)
        bot.handle(s, _cb(f"d:{d.id}"))
        assert draft_due_reminders(s, FakeLLM({"reminder_email": [body]}), TODAY) == []


def test_long_messages_are_split():
    parts = chunks("line\n" * 2000)
    assert len(parts) == 3 and all(len(p) <= 4000 for p in parts)


def test_serve_starts_the_bot_when_configured(db, monkeypatch):
    from app.discovery import worker
    started = []

    class T:
        def __init__(self, target, name, daemon, args=()):
            self.name = name

        def start(self):
            started.append(self.name)

    monkeypatch.setattr(worker.threading, "Thread", T)
    monkeypatch.setattr(worker, "run_forever", lambda jobs, tz: None)
    import app.telegram.bot as tb
    monkeypatch.setattr(tb, "make_bot", lambda: Bot(FakeAPI(), OWNER))
    monkeypatch.setattr(worker, "morning", lambda: None)
    worker.serve()
    assert started == ["telegram"]


def test_no_second_reminder_the_day_after_one_was_sent_from_telegram(db, folders):
    """Regression (19 Nov): reminders sent on 18 Nov were drafted again on 19 Nov because the sent
    reminder was not yet in the email table."""
    from datetime import datetime
    from app.db.models import EmailMessage
    from app.mail.reminders import reminders_due
    from app.outreach.reminder import ReminderEmail, draft_due_reminders
    gmail = SendingGmail()
    bot, _ = _bot(gmail)
    with session_scope() as s:
        p = _pos(s, email_done=True, email_sent="2026-10-13")
        s.add(EmailMessage(gmail_id="g1", thread_id="th", position_id=p.id, direction="out", sender="me",
                           recipients="j.smith@leeds.ac.uk", subject="PhD enquiry", sent_at=datetime(2026, 10, 13)))
        body = ReminderEmail(body="Dear Dr Smith,\n\nAny update?\n\nBest regards,\nFarbod Fooladi")
        [d] = draft_due_reminders(s, FakeLLM({"reminder_email": [body]}), TODAY)
        bot.handle(s, _cb(f"a:{d.id}"))
        assert p.reminder_1 == TODAY.isoformat()
        assert reminders_due(s, date(2026, 11, 19)) == []
        later = max(TODAY, date.today()) + timedelta(days=15)   # the draft's own date is the real clock
        assert reminders_due(s, later)[0].number == 2


def test_a_reminder_in_a_shared_thread_counts_for_both_projects(db, folders):
    from datetime import datetime
    from app.db.models import EmailMessage
    from app.mail.reminders import reminders_due
    with session_scope() as s:
        a, b = _pos(s, title="A", email_done=True), _pos(s, title="B", email_done=True)
        for p in (a, b):
            s.add(EmailMessage(gmail_id=f"g{p.id}", thread_id="shared", position_id=p.id, direction="out",
                               sender="me", recipients="x@leeds.ac.uk", subject="s", sent_at=datetime(2026, 10, 13)))
        s.add(Draft(position_id=a.id, kind="reminder", to="x@leeds.ac.uk", subject="Re: s", body="b",
                    thread_id="shared", status="sent"))
        s.flush()
        assert reminders_due(s, TODAY) == []


def test_approve_refuses_a_draft_with_a_missing_attachment(db, folders):
    gmail = SendingGmail()
    bot, api = _bot(gmail)
    with session_scope() as s:
        p, d = _draft(s)
        d.issues = ["missing transcripts: the advert asks for them"]
        s.commit()
        bot.handle(s, _cb(f"a:{d.id}"))
        assert d.status == "pending" and gmail.direct == [] and "فرستاده نشد" in api.sent[-1][1]


def test_menu_lists_every_command():
    from app.telegram.bot import COMMANDS

    class MenuAPI(FakeAPI):
        def set_commands(self, commands):
            self.commands = commands

    api = MenuAPI()
    Bot(api, OWNER).setup_menu()
    assert [c for c, _ in api.commands] == ["drafts", "new", "status", "portals", "done", "skip", "tasks", "show", "cancel", "help"]
    assert all(d for _, d in COMMANDS)


def test_show_without_a_number_asks_for_it(db, folders):
    bot, api = _bot()
    with session_scope() as s:
        bot.handle(s, _msg("/show"))
        assert "#27" in api.sent[-1][1]
