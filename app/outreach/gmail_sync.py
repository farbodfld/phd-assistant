"""Until the Telegram bot exists: every pending draft also becomes a Gmail draft. Farbod sends it himself,
and the next check sees it in Sent and updates the tracker."""
import calendar
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Draft, Meta, Position


def _key(draft_id: int) -> str:
    return f"gmail-draft:{draft_id}"


def push_drafts(s: Session, gmail) -> int:
    """Create a Gmail draft for each pending draft that does not have one yet."""
    n = 0
    for d in s.scalars(select(Draft).where(Draft.status == "pending")):
        if s.get(Meta, _key(d.id)) or not d.to:
            continue
        files = [Path(a) for a in (d.attachments or []) if Path(a).exists()]
        ref = gmail.create_draft(d.to, d.subject, d.body, thread_id=d.thread_id, in_reply_to=d.in_reply_to,
                                 attachments=files)
        if not d.thread_id:
            d.thread_id = ref["thread"]
        s.add(Meta(key=_key(d.id), value=ref))
        s.commit()
        n += 1
    return n


def send_approved(s: Session, gmail, ids: list[int], today: date) -> dict:
    """Send the Gmail drafts behind these draft ids, only when Farbod has approved them by name or by
    "all", then record them like any sent draft. A draft deleted in Gmail is skipped."""
    sent, missing = [], []
    for d in s.scalars(select(Draft).where(Draft.id.in_(ids), Draft.status.in_(("pending", "approved")))
                       .order_by(Draft.id)):
        meta = s.get(Meta, _key(d.id))
        if not meta or not gmail.draft_exists(meta.value["draft"]) or any(
                str(i).startswith("missing") for i in (d.issues or [])):
            missing.append(d.id)
            continue
        try:
            gmail.send_draft(meta.value["draft"])
        except Exception:               # e.g. "Message not a draft": already sent from Gmail itself
            missing.append(d.id)
            continue
        sent.append(d.id)
    done = sync_drafts(s, gmail, today)         # records everything sent, here or from Gmail
    return {"sent": sent, "missing": missing, "recorded": done["sent"]}


def reminder_number(d: Draft) -> int | None:
    for note in d.notes or []:
        if str(note).startswith("reminder "):
            try:
                return int(str(note).split()[1])
            except (IndexError, ValueError):
                return None
    return None


def record_sent(s: Session, d: Draft, message_id: str, today: date):
    """The draft went out: mark it and write it on the tracker row."""
    d.status, d.sent_gmail_id = "sent", message_id
    p = s.get(Position, d.position_id) if d.position_id else None
    if not p:
        return
    stamp = today.isoformat()
    if d.kind == "initial":
        p.email_done, p.email_sent = True, p.email_sent or stamp
        p.add_status(f"First email sent to {d.to}.", on=today)
    elif d.kind == "reminder":
        for field in ("reminder_1", "reminder_2", "reminder_3", "reminder_4"):
            if not getattr(p, field, None):
                setattr(p, field, stamp)
                break
        p.add_status("Reminder sent.", on=today)
    else:
        p.add_status(f"Reply sent to {d.to}.", on=today)


def mark_denied(s: Session, d: Draft, today: date):
    """Farbod said no: never draft the same email again by itself."""
    d.status = "denied"
    p = s.get(Position, d.position_id) if d.position_id else None
    if d.kind == "initial" and p:
        s.merge(Meta(key=f"outreach:{p.id}", value="denied by Farbod"))
        p.add_status("First email draft denied by Farbod; no new draft unless asked.", on=today)
    elif d.kind == "reminder" and p:
        s.merge(Meta(key=f"reminder-denied:{p.id}:{reminder_number(d) or 1}", value=today.isoformat()))
        p.add_status("Reminder draft denied by Farbod.", on=today)


def _ms(d: Draft) -> int:
    return calendar.timegm(d.created_at.timetuple()) * 1000 if d.created_at else 0


def sync_drafts(s: Session, gmail, today: date) -> dict:
    """Mark drafts Farbod sent (and record it on the row) or deleted in Gmail."""
    sent, gone = [], []
    for d in s.scalars(select(Draft).where(Draft.status.in_(("pending", "approved")))):
        meta = s.get(Meta, _key(d.id))
        if not meta or not d.thread_id:
            continue
        msg = gmail.sent_in_thread(d.thread_id, _ms(d))
        if msg:
            record_sent(s, d, msg, today)
            sent.append(d.id)
        elif not gmail.draft_exists(meta.value["draft"]):
            mark_denied(s, d, today)
            gone.append(d.id)
        s.commit()
    return {"sent": sent, "deleted": gone}
