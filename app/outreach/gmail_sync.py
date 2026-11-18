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
        if not meta or not gmail.draft_exists(meta.value["draft"]):
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
            d.status, d.sent_gmail_id = "sent", msg
            p = s.get(Position, d.position_id) if d.position_id else None
            if p:
                stamp = today.isoformat()
                if d.kind == "initial":
                    p.email_done, p.email_sent = True, p.email_sent or stamp
                    p.add_status(f"First email sent to {d.to}.", on=today)
                elif d.kind == "reminder":
                    if not p.reminder_1:
                        p.reminder_1 = stamp
                    elif not p.reminder_2:
                        p.reminder_2 = stamp
                    p.add_status("Reminder sent.", on=today)
                else:
                    p.add_status(f"Reply sent to {d.to}.", on=today)
            sent.append(d.id)
        elif not gmail.draft_exists(meta.value["draft"]):
            d.status = "denied"
            gone.append(d.id)
        s.commit()
    return {"sent": sent, "deleted": gone}
