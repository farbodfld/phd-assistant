"""Which positions need a portal application, and the state of each pack (Meta 'portal:<id>')."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Application, EmailMessage, Meta, Position

PORTAL_METHODS = ("Portal", "Email + Portal")


def key(pid: int) -> str:
    return f"portal:{pid}"


def state(s: Session, pid: int) -> dict | None:
    m = s.get(Meta, key(pid))
    return m.value if m and isinstance(m.value, dict) else None


def set_state(s: Session, pid: int, value: dict):
    s.merge(Meta(key=key(pid), value=value))


def invited(s: Session, p: Position) -> bool:
    """The supervisor encouraged him or told him to apply via the portal."""
    if s.get(Meta, f"portal-invited:{p.id}"):
        return True
    return bool(s.scalar(select(EmailMessage.id).where(
        EmailMessage.position_id == p.id, EmailMessage.direction == "in",
        EmailMessage.category.in_(("positive", "request_docs"))).limit(1)))


def _open(p: Position, today: date) -> bool:
    if p.closed or p.portal_done or p.portal_submitted or p.apply_method not in PORTAL_METHODS:
        return False
    return not (p.deadline_date and p.deadline_date < today)


def candidates(s: Session, today: date, horizon_days: int | None = None, min_score: int | None = None) -> list[Position]:
    """Portal positions with no pack yet: invited ones first, then by deadline. Rolling ones need a high score."""
    st = get_settings()
    horizon = horizon_days if horizon_days is not None else st.portal_horizon_days
    floor = min_score if min_score is not None else st.portal_min_score
    out = []
    for p in s.scalars(select(Position).where(Position.closed.is_(False))):
        if not _open(p, today) or state(s, p.id):
            continue
        inv = invited(s, p)
        if not inv and (p.score or 0) < floor:
            continue
        if p.deadline_date:
            if (p.deadline_date - today).days > horizon and not inv:
                continue
        elif not inv and (p.score or 0) < max(floor, 70):
            continue
        out.append((not inv, p.deadline_date or date.max, -(p.score or 0), p.id, p))
    return [x[-1] for x in sorted(out, key=lambda x: x[:4])]


def packed(s: Session, today: date) -> list[Position]:
    """Packs ready and not yet submitted, by deadline."""
    out = []
    for m in s.scalars(select(Meta).where(Meta.key.like("portal:%"))):
        if not isinstance(m.value, dict) or m.value.get("status") != "packed":
            continue
        p = s.get(Position, int(m.key.split(":")[1]))
        if p and _open(p, today):
            out.append(p)
    return sorted(out, key=lambda p: (p.deadline_date or date.max, p.id))


def mark_submitted(s: Session, p: Position, today: date, reference: str | None = None) -> list[int]:
    """Record his submission. When this fills a programme's cap, its other positions are closed (ids returned)."""
    from app.portal.programmes import close_capped
    p.portal_done, p.portal_submitted = True, p.portal_submitted or today.isoformat()
    s.add(Application(position_id=p.id, university=p.university or "", project=p.title,
                      supervisor=p.supervisor_contact, submitted_on=today, reference=reference))
    st = state(s, p.id) or {}
    set_state(s, p.id, {**st, "status": "submitted", "submitted_on": today.isoformat(),
                        **({"reference": reference} if reference else {})})
    p.add_status("Portal application submitted by Farbod" + (f" (ref {reference})." if reference else "."), on=today)
    s.flush()
    return close_capped(s, p, today)


def skip(s: Session, p: Position, today: date):
    st = state(s, p.id) or {}
    set_state(s, p.id, {**st, "status": "skipped", "skipped_on": today.isoformat()})
    p.add_status("Portal application skipped by Farbod.", on=today)
