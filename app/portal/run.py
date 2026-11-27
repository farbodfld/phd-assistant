"""The daily portal step: packs (facts only, no model calls) for invited and urgent positions, optional upload
to Google Drive, and deadline nudges for packs not yet submitted."""
import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Meta, Notification, Position
from app.portal.queue import candidates, invited, packed, set_state, state

log = logging.getLogger("portal")


@dataclass
class PortalReport:
    packed: list[int] = field(default_factory=list)
    uploaded: list[int] = field(default_factory=list)
    nudged: list[int] = field(default_factory=list)
    waiting: int = 0                     # candidates left for Farbod to pick in Telegram
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"portals: {len(self.packed)} packs ({len(self.uploaded)} on Drive), {self.waiting} to choose from, "
                f"{len(self.nudged)} deadline nudges, {len(self.errors)} errors")


def upload(s: Session, p: Position, pack, drive=None) -> str | None:
    """Put the pack on Google Drive when DRIVE_PACKS is on. Returns the Drive folder id, or None."""
    if not get_settings().drive_packs:
        return None
    from app.portal.drive import DrivePacks
    folder = (drive or DrivePacks()).upload(p.id, p.title, p.university, [pack.json_path, pack.path])
    set_state(s, p.id, {**(state(s, p.id) or {}), "drive_folder": folder})
    return folder


def prepare(s: Session, p: Position, today: date, drive=None):
    """Build (or rebuild) the pack, and upload it when Drive is on. An upload failure keeps the local pack."""
    from app.portal.pack import build_pack
    pack = build_pack(s, p, today)
    try:
        pack.drive_folder = upload(s, p, pack, drive)
    except Exception as exc:
        log.warning("Drive upload failed for #%s: %s", p.id, exc)
        pack.issues.append(f"Drive upload failed: {type(exc).__name__}: {str(exc)[:120]}")
        pack.drive_folder = None
    return pack


def run_portals(s: Session, today: date, limit: int | None = None, drive=None) -> PortalReport:
    st = get_settings()
    r = PortalReport()
    limit = st.portal_daily_packs if limit is None else limit
    auto = []
    for p in candidates(s, today):
        days = (p.deadline_date - today).days if p.deadline_date else None
        if invited(s, p) or (days is not None and days <= st.portal_auto_days and (p.score or 0) >= st.portal_auto_min_score):
            auto.append(p)
    for p in auto[:limit]:
        try:
            pack = prepare(s, p, today, drive)
            s.commit()
            r.packed.append(p.id)
            if getattr(pack, "drive_folder", None):
                r.uploaded.append(p.id)
            elif st.drive_packs:
                r.errors.append(f"#{p.id}: not on Drive")
        except Exception as exc:
            s.rollback()
            r.errors.append(f"#{p.id}: {type(exc).__name__}: {str(exc)[:120]}")
    r.waiting = len(candidates(s, today))
    for p in packed(s, today):
        if not p.deadline_date:
            continue
        days = (p.deadline_date - today).days
        if days in (7, 3, 1, 0) and not s.get(Meta, f"portal-nudge:{p.id}:{days}"):
            s.merge(Meta(key=f"portal-nudge:{p.id}:{days}", value=today.isoformat()))
            when = "امروز" if days == 0 else f"{days} روز دیگه"
            s.add(Notification(kind="task", ref_type="position", ref_id=p.id,
                               text=f"⏰ مهلت پورتال #{p.id} «{p.title}» ({p.university}) {when}ه. "
                                    f"توی سشن Portal بنویس: پورتال #{p.id}"))
            r.nudged.append(p.id)
    s.commit()
    return r
