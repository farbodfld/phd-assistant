"""Who a first email goes to: the supervisor's own address, from the tracker, contacts or the advert."""
import re

from app.db.models import Position
from app.tracker.parsing import EMAIL_RE, parse_contacts

GENERIC = re.compile(r"(^|[._-])(pgr|phd|admissions?|graduate|postgrad|research|enquir\w*|info|study|doctoral|apply|"
                     r"recruit\w*|studentships?|office|school|dtp|cdt|dla)([._-]|$)|recruit|admission", re.I)


def is_generic(email: str) -> bool:
    """Office or programme inboxes (pgr-admissions@, made4manufacturing@, bbsrcdtp@) rather than a person.
    A person's address has a dot (j.smith@), or is short initials (mjw@)."""
    local = email.split("@")[0].lower()
    if GENERIC.search(local):
        return True
    return "." not in local and len(local) > 4 and not any(ch.isdigit() for ch in local[-2:])


def recipient(p: Position, advert: str = "") -> tuple[str, str] | None:
    """(name, email) of the supervisor to write to, or None when no address is known.
    A personal address wins over a generic one such as pgr-admissions@."""
    found: list[tuple[str, str]] = []
    for name, email in parse_contacts(p.supervisor_contact):
        if email:
            found.append((name.split(";")[0].strip() if not name.startswith("General contact") else "", email))
    for c in p.contacts:
        if c.email:
            found.append(((c.name or "").split(";")[0].strip() if "@" not in (c.name or "") else "", c.email))
    for email in EMAIL_RE.findall(advert or ""):
        found.append(("", email))
    seen, ordered = set(), []
    for name, email in found:
        key = email.lower().rstrip(".")
        if key not in seen:
            seen.add(key)
            ordered.append((name, key))
    personal = [x for x in ordered if not is_generic(x[1])]
    pick = (personal or ordered or [None])[0]
    return pick
