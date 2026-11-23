"""The two-way assistant in Telegram: questions answered from the data, safe actions, never a send."""
from datetime import date
from types import SimpleNamespace as NS

from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Contact, Draft, LLMCall, Meta, Position, UserTask
from app.llm.client import BudgetExceeded
from tests.fakes import FakeLLM
from tests.test_outreach import TODAY, _email, _pos, folders  # noqa: F401
from tests.test_telegram import OWNER, SendingGmail, _bot, _cb, _draft, _msg


def _text(t):
    return NS(type="text", text=t)


def _use(tool, **inp):
    return NS(type="tool_use", id=f"tu_{tool}", name=tool, input=inp)


class Scripted(FakeLLM):
    """Replies in turn; records what the model was sent."""

    def __init__(self, turns, outputs=None):
        super().__init__(outputs or {})
        self.turns, self.sent = list(turns), []

    def converse(self, task, messages, *, system, tools):
        self.sent.append([dict(m) for m in messages])
        return NS(content=self.turns.pop(0), usage=None)


def _chat_bot(llm, gmail=None):
    bot, api = _bot(gmail, llm)
    return bot, api


def test_a_question_is_answered_from_the_data(db, folders):
    llm = Scripted([[_use("search_positions", query="Leeds")],
                    [_text("یه پوزیشن تو Leeds داری: #1 Learning robots، مهلت 2027-01-10.")]])
    bot, api = _chat_bot(llm)
    with session_scope() as s:
        _pos(s)
        s.commit()
        bot.handle(s, _msg("تو Leeds چی دارم؟"))
        tool_result = llm.sent[1][-1]["content"][0]
        assert tool_result["type"] == "tool_result" and "Learning robots" in tool_result["content"]
        assert "Learning robots" in api.sent[-1][1]
        assert s.get(Meta, "chat:history").value[-1]["role"] == "assistant"


def test_follow_up_questions_keep_the_conversation(db, folders):
    llm = Scripted([[_text("سلام")], [_text("بله")]])
    bot, _ = _chat_bot(llm)
    with session_scope() as s:
        bot.handle(s, _msg("سلام"))
        bot.handle(s, _msg("ادامه بده"))
        assert [m["content"] for m in llm.sent[1]] == ["سلام", "سلام", "ادامه بده"]
        bot.handle(s, _msg("/new"))
        assert s.get(Meta, "chat:history") is None


def test_reply_to_a_draft_talks_about_that_draft_and_can_edit_it(db, folders):
    new_body = "Dear Dr Smith,\n\nNew text for two.\n\nBest regards,\nFarbod Fooladi"
    llm = Scripted([[_use("edit_draft", draft_id=2, instruction=new_body)], [_text("عوضش کردم.")]])
    bot, api = _chat_bot(llm)
    with session_scope() as s:
        (_, d1), (_, d2) = _draft(s, title="one"), _draft(s, title="two")
        bot.handle(s, _cb("stage:initial"))
        msg = _msg("این رو کوتاه‌تر کن")
        msg["message"]["reply_to_message"] = {"message_id": s.get(Meta, f"tg:msg:{d2.id}").value}
        bot.handle(s, msg)
        assert "[He is replying to draft #2]" in llm.sent[0][-1]["content"]
        assert "New text for two" in d2.body and "New text" not in d1.body and d2.version == 2
        assert any("نسخه ویرایش‌شده" in t for _, t, _ in api.sent)


def test_a_found_address_unblocks_the_first_email_and_shows_it(db, folders):
    llm = Scripted([[_use("set_supervisor_email", position_id=1, email="A.Jones@leeds.ac.uk")],
                    [_use("draft_first_email", position_id=1)], [_text("draft آماده‌ست.")]],
                   {"initial_email": [_email()]})
    bot, api = _chat_bot(llm)
    with session_scope() as s:
        p = _pos(s, supervisor_contact="See advert")
        s.add(Meta(key=f"outreach:{p.id}", value="no supervisor email"))
        s.add(UserTask(position_id=p.id, text="آدرس ایمیل supervisor پیدا نشد"))
        s.commit()
        bot.handle(s, _msg("ایمیل استاد #1 رو پیدا کردم A.Jones@leeds.ac.uk براش بنویس"))
        d = s.scalars(select(Draft)).one()
        assert d.to == "a.jones@leeds.ac.uk" and d.status == "pending"          # drafted, never sent
        assert s.scalars(select(UserTask.done)).one() is True
        assert api.sent[-1][2][0][0] == ("✅ Approve", f"a:{d.id}")


def test_the_assistant_cannot_send(db, folders):
    from app.chat.agent import TOOLS
    names = {t["name"] for t in TOOLS}
    assert not any("send" in n or "approve" in n for n in names)


def test_budget_reached(db, folders):
    class Broke(Scripted):
        def converse(self, *a, **k):
            raise BudgetExceeded("daily")

    bot, api = _chat_bot(Broke([]))
    with session_scope() as s:
        bot.handle(s, _msg("سلام"))
        assert "بودجه" in api.sent[-1][1]


def test_afternoon_goes_straight_to_the_replies(db, folders):
    from app.db.models import Notification
    bot, api = _bot()
    with session_scope() as s:
        bot.flush(s)
        api.sent.clear()
        p = _pos(s)
        s.add(Draft(position_id=p.id, kind="reply", to="j.smith@leeds.ac.uk", subject="Re: PhD",
                    body="Dear Dr Smith,\n\nThank you, I will apply through the portal.\n\nBest regards,\nFarbod Fooladi"))
        s.add(Notification(kind="review", ref_type="pipeline", text="replies"))
        s.commit()
        bot.flush(s)
        texts = [t for _, t, _ in api.sent]
        assert "جواب‌هایی که برای ایمیل‌های این نوبت نوشتم" in texts[0] and "apply through the portal" in texts[2]
