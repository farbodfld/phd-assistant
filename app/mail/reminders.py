"""Which supervisors are due a reminder (SOP 20): 14 days without a reply, at most four reminders."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Draft, EmailMessage, Position
from app.tracker.parsing import parse_deadline

WAIT_DAYS = 14
REMINDER_FIELDS = ("reminder_1", "reminder_2", "reminder_3", "reminder_4")


def sent_reminders(p: Position) -> int:
    return sum(1 for f in REMINDER_FIELDS if getattr(p, f, None))


@dataclass
class DueReminder:
    position: Position
    number: int                 # 1 to max_reminders
    last_sent: date
    thread_id: str | None       # None: no thread found, a fresh email is needed
    to: str | None


def reminders_due(s: Session, today: date, wait_days: int = WAIT_DAYS,
                  max_reminders: int | None = None) -> list[DueReminder]:
    max_reminders = max_reminders or get_settings().max_reminders
    out = []
    for p in s.scalars(select(Position).where(Position.closed.is_(False))):
        if not p.email_done or p.portal_done:
            continue
        sent = sent_reminders(p)
        if sent >= max_reminders:
            continue                                   # all reminders sent: stop (SOP 20)
        mails = list(s.scalars(select(EmailMessage).where(EmailMessage.position_id == p.id)
                               .order_by(EmailMessage.sent_at)))
        outs = [m for m in mails if m.direction == "out"]
        ins = [m for m in mails if m.direction == "in" and m.category != "auto_reply"]
        if ins:
            continue                                   # they have replied: reminders are only for silence
        # Last contact: the newest of our stored emails, the tracker dates, and drafts sent through the
        # assistant (a sent draft may not be in the email table until the next inbox check).
        dates = [d for d in [parse_deadline(p.email_sent)] + [parse_deadline(getattr(p, f, None))
                                                              for f in REMINDER_FIELDS] if d]
        if outs:
            dates.append(outs[-1].sent_at.date())
        sent_drafts = s.scalars(select(Draft.created_at).where(Draft.position_id == p.id, Draft.status == "sent"))
        dates += [d.date() for d in sent_drafts if d]
        thread_id = outs[-1].thread_id if outs else None
        if thread_id:              # the same thread may carry another project's follow-ups (one supervisor)
            dates += [m.date() for m in s.scalars(select(EmailMessage.sent_at).where(
                EmailMessage.thread_id == thread_id, EmailMessage.direction == "out")) if m]
            dates += [m.date() for m in s.scalars(select(Draft.created_at).where(
                Draft.thread_id == thread_id, Draft.status == "sent")) if m]
        if not dates:
            continue
        last = max(dates)
        if (today - last).days < wait_days:
            continue
        thread = outs[-1].thread_id if outs else None
        # Our earlier follow-ups in the same thread count as reminders even when the tracker missed them.
        in_thread = sum(1 for m in outs if m.thread_id == thread) if thread else 0
        number = max(sent + 1, in_thread)
        if number > max_reminders:
            continue                                   # all reminders already in the thread: stop
        to = outs[-1].recipients.split(",")[0].strip() if outs and outs[-1].recipients else None
        out.append(DueReminder(p, number, last, thread, to))
    return sorted(out, key=lambda d: (d.position.deadline_date or date.max, -(d.position.score or 0)))
