"""Which supervisors are due a reminder (SOP 20): 14 days without a reply, at most two reminders."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmailMessage, Position
from app.tracker.parsing import parse_deadline

WAIT_DAYS = 14


@dataclass
class DueReminder:
    position: Position
    number: int                 # 1 or 2
    last_sent: date
    thread_id: str | None       # None: no thread found, a fresh email is needed
    to: str | None


def reminders_due(s: Session, today: date, wait_days: int = WAIT_DAYS) -> list[DueReminder]:
    out = []
    for p in s.scalars(select(Position).where(Position.closed.is_(False))):
        if not p.email_done or p.portal_done:
            continue
        if p.reminder_1 and p.reminder_2:
            continue                                   # two reminders sent: stop (SOP 20)
        mails = list(s.scalars(select(EmailMessage).where(EmailMessage.position_id == p.id)
                               .order_by(EmailMessage.sent_at)))
        outs = [m for m in mails if m.direction == "out"]
        ins = [m for m in mails if m.direction == "in" and m.category != "auto_reply"]
        if ins:
            continue                                   # they have replied: reminders are only for silence
        if outs:
            last = outs[-1].sent_at.date()
        else:
            dates = [d for d in (parse_deadline(p.reminder_1), parse_deadline(p.email_sent)) if d]
            if not dates:
                continue
            last = max(dates)
        if (today - last).days < wait_days:
            continue
        thread = outs[-1].thread_id if outs else None
        # Our earlier follow-ups in the same thread count as reminders even when the tracker missed them.
        in_thread = sum(1 for m in outs if m.thread_id == thread) if thread else 0
        number = max(2 if p.reminder_1 else 1, in_thread)
        if number > 2:
            continue                                   # two reminders already in the thread: stop
        to = outs[-1].recipients.split(",")[0].strip() if outs and outs[-1].recipients else None
        out.append(DueReminder(p, number, last, thread, to))
    return sorted(out, key=lambda d: (d.position.deadline_date or date.max, -(d.position.score or 0)))
