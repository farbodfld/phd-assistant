"""Reminder drafts (SOP 20): a short neutral reply in that project's own thread."""
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Draft, EmailMessage, Meta, Notification
from app.knowledge.loader import build_context
from app.mail.reminders import DueReminder, reminders_due
from app.rules.text_checks import check_outgoing_text, fix_dash_connectors


class ReminderEmail(BaseModel):
    body: str = Field(description="2 to 4 sentences plus greeting and sign-off")


INSTRUCTIONS = """Write reminder number {n} for this PhD enquiry, following SOP 20. It is a reply in the same
thread. Greeting with the supervisor's name if known, then only: whether they had a chance to read the earlier
email, and whether there is any update. No re-pitch, no mention of submitting, no offer of calls or proposals.
2 to 4 sentences. Sign off exactly:
Best regards,
Farbod Fooladi
Never use a dash as a sentence connector."""


def _open_reminder(s: Session, position_id: int) -> bool:
    return bool(s.scalar(select(Draft.id).where(Draft.position_id == position_id, Draft.kind == "reminder",
                                                Draft.status.in_(("pending", "approved"))).limit(1)))


def draft_reminder(s: Session, d: DueReminder, llm, today: date) -> Draft | None:
    p = d.position
    if _open_reminder(s, p.id):
        return None
    if not d.thread_id or not d.to:
        key = f"reminder-nothread:{p.id}:{d.number}"
        if not s.get(Meta, key):
            s.add(Meta(key=key, value=today.isoformat()))
            s.add(Notification(kind="task", ref_type="position", ref_id=p.id,
                               text=f"⏰ یادآوری {d.number} برای «{p.title}» موعدشه، ولی thread ایمیل قبلی پیدا نشد. "
                                    f"خودت از Gmail جواب بده."))
        return None
    last = s.scalars(select(EmailMessage).where(EmailMessage.thread_id == d.thread_id)
                     .order_by(EmailMessage.sent_at.desc())).first()
    subject = (last.subject or p.title) if last else p.title
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    prompt = (f"Project: {p.title} ({p.university})\nSupervisor: {p.supervisor_contact}\nTo: {d.to}\n"
              f"Our last email ({d.last_sent}):\n{(last.body or '')[:2500] if last else ''}")
    s.commit()        # no open write while the cost log is written from its own session (SQLite)
    r = llm.complete("reminder_email", prompt, context=build_context("reminder_email"),
                     instructions=INSTRUCTIONS.format(n=d.number), output=ReminderEmail)
    body = fix_dash_connectors(r.body)
    issues = [f"{i.kind}: {i.message} [{i.snippet}]" for i in check_outgoing_text(body, outreach=True)]
    draft = Draft(position_id=p.id, kind="reminder", to=d.to, subject=subject, body=body, thread_id=d.thread_id,
                  in_reply_to=last.message_id_header if last else None, attachments=[],
                  notes=[f"reminder {d.number}"], issues=issues)
    s.add(draft)
    s.flush()
    p.add_status(f"Reminder {d.number} drafted (draft #{draft.id}).", on=today)
    s.add(Notification(kind="email", ref_type="draft", ref_id=draft.id,
                       text=f"⏰ یادآوری {d.number} آماده‌ست: {p.title} ({p.university})\nبه: {d.to}"
                            + "".join(f"\n⚠️ {i}" for i in issues)))
    return draft


def draft_due_reminders(s: Session, llm, today: date) -> list[Draft]:
    out = []
    for d in reminders_due(s, today):
        draft = draft_reminder(s, d, llm, today)
        if draft:
            out.append(draft)
            s.commit()
    return out
