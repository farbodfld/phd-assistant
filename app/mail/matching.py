"""Decide whether an email belongs to the PhD search, and which position it is about."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Contact, EmailMessage, Position
from app.gmail.parse import ParsedEmail

ACADEMIC_DOMAIN = re.compile(r"(\.ac\.uk|\.edu|\.ca|\.ac\.[a-z]{2})$")
# Alert services and bulk senders that are never a supervisor/admissions conversation
IGNORE_SENDERS = re.compile(r"(@|\.)(jobs\.ac\.uk|findaphd\.com|findamasters\.com|linkedin\.com|indeed\.com)$")
# Bulk and service mailboxes at universities: marketing, alumni, IT, careers, HR job alerts
BULK_LOCAL = re.compile(r"^(alumni|marketing|news(letter)?|events?|careers?|jobs|recruitment|hr|hrsystems|"
                        r"itservices?|it-?help|servicedesk|wifi|eduroam|sport(s)?|library|accommodation|"
                        r"communications|comms|fundraising|giving|surveys?|harvardonline|online-learning|hr[a-z]*|gradoff|"
                        r"graduation|scadmin|screcept|careersinfo|studentservices)([._-].*)?@")
BULK_SUBJECT = re.compile(r"(wireless access request|password reset|verification code|graduation|"
                          r"direct debit|terms and conditions|e-recruitment)", re.I)
BULK_DOMAIN = re.compile(r"@(.*\.)?(harvardonline\.harvard\.edu|online\.hbs\.edu|emeritus\.org|getsmarter\.com)$")
_STOP = {"and", "for", "of", "the", "in", "with", "to", "phd", "re", "fw", "fwd", "project", "position",
         "studentship", "enquiry", "automatic", "reply", "confirmation", "your", "from", "inquiry", "prospective", "applicant", "application", "doctoral", "research"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2 and t not in _STOP}


def counterpart(e: ParsedEmail) -> list[str]:
    """The other side of the conversation: sender for incoming, recipients for outgoing."""
    return [e.sender] if e.direction == "in" else e.to + e.cc


def known_thread_position(s: Session, thread_id: str) -> tuple[bool, int | None]:
    rows = list(s.scalars(select(EmailMessage).where(EmailMessage.thread_id == thread_id)))
    if not rows:
        return False, None
    pid = next((r.position_id for r in rows if r.position_id), None)
    return True, pid


def relevance(s: Session, e: ParsedEmail) -> str:
    """'known' (thread or contact we know), 'academic' (unknown university sender), or 'skip'."""
    people = counterpart(e)
    if any(IGNORE_SENDERS.search(p) for p in people):
        return "skip"
    if e.direction == "in" and (BULK_LOCAL.search(e.sender) or BULK_DOMAIN.search(e.sender)
                                or BULK_SUBJECT.search(e.subject or "")):
        return "skip"
    known, _ = known_thread_position(s, e.thread_id)
    if known:
        return "known"
    emails = {c.email.lower() for c in s.scalars(select(Contact)) if c.email}
    if any(p in emails for p in people):
        return "known"
    if any(ACADEMIC_DOMAIN.search(p) for p in people):
        return "academic"
    return "skip"


def match_position(s: Session, e: ParsedEmail) -> Position | None:
    known, pid = known_thread_position(s, e.thread_id)
    if pid:
        return s.get(Position, pid)
    people = set(counterpart(e))
    positions = list(s.scalars(select(Position)))
    subj = _tokens(e.subject)          # subject only: bodies mention too many unrelated words

    def title_overlap(p: Position) -> float:
        t = _tokens(p.title)
        return len(t & subj) / max(1, len(t))

    by_contact = [c.position for c in s.scalars(select(Contact)) if c.email and c.email.lower() in people]
    if by_contact:
        return max(by_contact, key=title_overlap)

    # surname of a supervisor as a whole word of the address local part or the display name
    # (substring matching linked "onlinepayments@" to a supervisor called Men)
    hay = set(re.findall(r"[a-z]+", " ".join(x.split("@")[0] for x in people).lower()))
    hay |= set(re.findall(r"[a-z]+", (e.sender_name or "").lower()))
    scored = []
    for p in positions:
        surnames = [w.lower() for w in re.findall(r"\b[A-Z][a-z][A-Za-z\-]+", p.supervisor_contact or "")
                    if w not in ("Dr", "Prof", "Professor", "See", "Advert", "Assoc", "Contact", "General")]
        if surnames and any(sn in hay for sn in surnames):
            scored.append((0.5 + title_overlap(p), p))
        elif title_overlap(p) >= 0.75:
            scored.append((title_overlap(p), p))
    if scored:
        scored.sort(key=lambda t: -t[0])
        if scored[0][0] >= 0.6:
            return scored[0][1]
    return None


_DOMAIN_ALIASES = {"ox": "oxford", "cam": "cambridge", "soton": "southampton", "bham": "birmingham",
                   "ucl": "university college london", "qmul": "queen mary", "kcl": "king's college",
                   "tees": "teesside", "lse": "london school of economics", "imperial": "imperial"}


def university_from_domain(addr: str) -> str | None:
    """'graduate.admissions@admin.ox.ac.uk' -> 'oxford'."""
    m = re.search(r"([a-z0-9-]+)\.ac\.uk$", addr.split("@")[-1])
    if not m:
        return None
    return _DOMAIN_ALIASES.get(m.group(1), m.group(1))


def match_by_university(s: Session, e: ParsedEmail) -> Position | None:
    """Admissions and portal emails: the one position we applied to at that university."""
    uni = next((u for u in map(university_from_domain, counterpart(e)) if u), None)
    if not uni:
        return None
    here = [p for p in s.scalars(select(Position)) if uni in (p.university or "").lower()]
    for group in ([p for p in here if p.portal_done], [p for p in here if p.email_done and not p.portal_done]):
        if len(group) == 1:
            return group[0]
        if group:
            for words, need in ((_tokens(e.subject), 1), (_tokens(e.body[:2000]), 2)):
                best = max(group, key=lambda p: len(_tokens(p.title) & words))
                if len(_tokens(best.title) & words) >= need:
                    return best
            return None
    return None


def remember_contact(s: Session, p: Position, e: ParsedEmail):
    """Learn the supervisor's address from a matched conversation."""
    have = {(c.email or "").lower() for c in p.contacts}
    for addr in counterpart(e):
        if addr not in have and ACADEMIC_DOMAIN.search(addr) and not IGNORE_SENDERS.search(addr):
            p.contacts.append(Contact(name=e.sender_name if e.direction == "in" else addr, email=addr))
            have.add(addr)
