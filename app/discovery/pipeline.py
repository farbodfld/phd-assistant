"""Daily discovery run for jobs.ac.uk: new positions in, removed adverts greyed, report out."""
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExcludedPosition, Meta, Notification, Position
from app.discovery.jobsac import Advert, job_code, parse_advert, parse_list
from app.discovery.screen import SCREEN_INSTRUCTIONS, Screen, advert_prompt
from app.discovery.sources import JOBSAC
from app.knowledge.loader import build_context, institutions_for
from app.rules.text_checks import fix_dash_connectors

SEEN_KEY = "seen:jobsac"
REMOVED = "Listing removed from jobs.ac.uk"
_STOP = {"phd", "studentship", "the", "and", "for", "of", "in", "a", "an", "to", "with", "on", "fully", "funded",
         "project", "doctoral", "research", "2026", "2027"}


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2 and w not in _STOP}


def same_title(a: str, b: str) -> bool:
    x, y = _words(a), _words(b)
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= 0.8


def university_of(employer: str) -> str:
    return employer.split(" - ")[0].strip()


@dataclass
class DiscoveryReport:
    pages: int = 0
    listings: int = 0
    complete: bool = False
    new: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    off_topic: int = 0
    duplicates: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    removed_active: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self):
        return report_fa(self)


def _seen(s: Session) -> dict:
    m = s.get(Meta, SEEN_KEY)
    if m is None:
        m = Meta(key=SEEN_KEY, value={})
        s.add(m)
        s.commit()  # The model client records usage through a separate DB connection.
    return m


def _remember(meta: Meta, code: str, verdict: str, title: str, today: date):
    v = dict(meta.value or {})
    v[code] = {"verdict": verdict, "title": title[:120], "on": today.isoformat()}
    meta.value = v                        # reassign so the JSON column is saved


def _collect(fetcher, rep: DiscoveryReport, max_pages: int | None):
    listings, n = [], 1
    while max_pages is None or n <= max_pages:
        page = fetcher.get(JOBSAC.page_url(n))
        if not page.ok:
            rep.errors.append(f"list page {n}: {'blocked' if page.blocked else page.error or page.status}")
            return listings
        lp = parse_list(page.html)
        if not lp.listings or lp.page != n:
            rep.errors.append(f"unexpected or empty results page {n}")
            return listings
        listings += lp.listings
        rep.pages = n
        if not lp.has_next or n >= lp.pages:
            rep.complete = True
            return listings
        n += 1
    rep.errors.append(f"stopped after {max_pages} pages")
    return listings


def _add_position(s: Session, a: Advert, v: Screen, today: date, source: str = "jobsac") -> Position:
    fix = fix_dash_connectors
    p = Position(source=source, title=a.title, university=university_of(a.employer),
                 supervisor_contact=fix(v.supervisor_contact), score=v.score, deadline_text=a.closes or None,
                 deadline_date=a.closes_date, apply_method=v.apply_method, fit_note=fix(v.funding_note),
                 application_route=fix(v.application_route), type=fix(v.area), link=a.url, funding=v.funding)
    site = "FindAPhD" if source == "findaphd" else "jobs.ac.uk"
    p.add_status(f"NEW. Found on {site}, funding for: {a.funding_for or 'not stated'}.", on=today)
    s.add(p)
    s.flush()
    return p


def _exclude(s: Session, a: Advert, reason: str, source: str = "jobsac"):
    order = (s.scalar(select(ExcludedPosition.sort_order).order_by(ExcludedPosition.sort_order.desc()).limit(1)) or 0) + 1
    s.add(ExcludedPosition(source=source, title=f"{university_of(a.employer)}: {a.title}", reason=reason,
                           sort_order=order))


def _check_removed(s: Session, fetcher, live_codes: set[str], rep: DiscoveryReport, today: date):
    """Tracker adverts missing from today's list may simply sit in another discipline, so open each one."""
    for p in s.scalars(select(Position).where(Position.source == "jobsac", Position.closed.is_(False))):
        code = job_code(p.link)
        if not code or code in live_codes or REMOVED in (p.status or ""):
            continue
        page = fetcher.get(p.link)
        if page.blocked or page.error or page.status in (429, 500, 502, 503):
            continue                       # cannot tell; try again tomorrow
        gone = page.status in (404, 410) or (page.ok and re.search(
            r"(?i)this (?:job|advert|vacancy) (?:has (?:now )?expired|is no longer available)", page.html))
        if not gone:
            continue
        if p.email_done or p.portal_done:
            p.add_status(f"{REMOVED} (already in progress, row kept active)", on=today)
            rep.removed_active.append(p.title)
        else:
            p.closed = True
            p.add_status(f"CLOSED: {REMOVED}", on=today)
            rep.removed.append(p.title)


def run_jobsac(s: Session, fetcher, llm, today: date | None = None, max_pages: int | None = 10,
               progress=print) -> DiscoveryReport:
    today = today or date.today()
    rep = DiscoveryReport()
    listings = _collect(fetcher, rep, max_pages)
    rep.listings = len(listings)
    progress(f"{len(listings)} listings on {rep.pages} page(s)")

    meta = _seen(s)
    positions = list(s.scalars(select(Position)))
    known_codes = {job_code(p.link) for p in positions if job_code(p.link)}
    excluded_titles = [e.title for e in s.scalars(select(ExcludedPosition))]

    for l in listings:
        if l.code in known_codes or l.code in (meta.value or {}):
            continue
        dup = next((p for p in positions if same_title(p.title, l.title)), None) or \
            next((t for t in excluded_titles if same_title(t, l.title)), None)
        if dup is not None:
            label = dup.title if isinstance(dup, Position) else dup
            _remember(meta, l.code, f"duplicate of: {label[:80]}", l.title, today)
            rep.duplicates.append(l.title)
            s.commit()
            continue
        page = fetcher.get(l.url)
        if not page.ok:
            rep.errors.append(f"{l.code}: {'blocked' if page.blocked else page.error or page.status}")
            continue
        a = parse_advert(page.html, l.url)
        if not a.title or not a.description:
            rep.errors.append(f"{l.code}: missing advert title or description")
            continue
        outside = next((i for i in institutions_for(a.employer + " " + a.location) if i.get("exclude_reason")), None)
        if outside:
            _exclude(s, a, outside["exclude_reason"])
            _remember(meta, l.code, "excluded: outside England", a.title, today)
            rep.excluded.append(a.title)
            s.commit()
            continue
        try:
            v = llm.complete("score_position", advert_prompt(a), context=build_context("score_position"),
                             instructions=SCREEN_INSTRUCTIONS, output=Screen)
        except Exception as e:             # one bad advert must not stop the run
            rep.errors.append(f"{l.code}: screening failed ({type(e).__name__})")
            continue
        if not v.topic_relevant:
            _remember(meta, l.code, "off-topic", a.title, today)
            rep.off_topic += 1
        elif not v.england or v.funding in ("home_only", "partial", "self_funded"):
            reason = v.exclude_reason or ("Outside England" if not v.england else v.funding_note)
            _exclude(s, a, fix_dash_connectors(reason))
            _remember(meta, l.code, f"excluded: {reason}", a.title, today)
            rep.excluded.append(a.title)
        else:
            p = _add_position(s, a, v, today)
            positions.append(p)
            _remember(meta, l.code, f"added #{p.id}", a.title, today)
            rep.new.append(f"{a.title} ({university_of(a.employer)}, score {v.score})")
        s.commit()

    if rep.complete:
        _check_removed(s, fetcher, {l.code for l in listings}, rep, today)
    s.add(Notification(kind="report", text=report_fa(rep), ref_type="discovery"))
    s.commit()
    return rep


def report_fa(r: DiscoveryReport) -> str:
    lines = [f"گزارش jobs.ac.uk: {r.listings} آگهی در {r.pages} صفحه بررسی شد."]
    if r.new:
        lines.append(f"{len(r.new)} موقعیت جدید به ترکر اضافه شد:")
        lines += [f"• {t}" for t in r.new]
    else:
        lines.append("موقعیت جدید مناسبی پیدا نشد.")
    if r.excluded:
        lines.append(f"{len(r.excluded)} مورد به خاطر funding یا محل تحصیل کنار گذاشته شد (در لیست Checked and excluded).")
    if r.off_topic:
        lines.append(f"{r.off_topic} آگهی به حوزه‌ی کاری‌ات ربط نداشت.")
    if r.removed:
        lines.append(f"{len(r.removed)} آگهی از سایت برداشته شد و ردیفش خاکستری شد:")
        lines += [f"• {t}" for t in r.removed]
    if r.removed_active:
        lines.append("این آگهی‌ها برداشته شدن ولی چون روشون کار کردی، ردیفشون فعال ماند:")
        lines += [f"• {t}" for t in r.removed_active]
    if not r.complete or r.errors:
        lines.append(f"⚠️ همه‌چیز بررسی نشد: {'؛ '.join(r.errors[:5])}")
    return "\n".join(lines)
