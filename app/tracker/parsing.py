"""Small parsers shared by the importer and the agent."""
import re
from datetime import date, datetime

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def parse_deadline(text) -> date | None:
    """'1 Dec 2026', '21 Oct 2026, 5pm', '2026-12-08' -> date; 'Rolling' -> None."""
    if text is None:
        return None
    if isinstance(text, datetime):
        return text.date()
    if isinstance(text, date):
        return text
    s = str(text)
    if m := _DATE_RE.search(s):
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            return date(int(m.group(3)), mon, int(m.group(1)))
    if m := _ISO_RE.search(s):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def cell_text(v) -> str | None:
    """Normalise a cell value to the string the tracker shows."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    return s if s.strip() else None


def parse_contacts(text: str | None) -> list[tuple[str, str | None]]:
    """'Dr Tao Chen (t.chen@bham.ac.uk)' -> [('Dr Tao Chen', 't.chen@bham.ac.uk')].

    Multiple people separated by commas; emails may be attached to any of them.
    """
    if not text:
        return []
    emails = EMAIL_RE.findall(text)
    names_part = re.sub(r"\([^)]*\)", "", text)
    names = [n.strip() for n in re.split(r",| and ", names_part) if n.strip()]
    names = [n for n in names if re.search(r"(Dr|Prof|Professor|Assoc|Mr|Ms|Mrs)\b", n)]

    def owner(email: str) -> str | None:
        local = email.split("@")[0].lower()
        for n in names:
            parts = [w.lower() for w in re.findall(r"[A-Za-z]+", n) if len(w) > 2 and w not in ("Dr", "Prof", "Assoc")]
            if any(part in local for part in parts):
                return n
        return None

    out: dict[str, str | None] = {n: None for n in names}
    for e in emails:
        o = owner(e)
        if o is not None:
            out[o] = e
        elif len(names) == 1 and len(emails) == 1 and "." in e.split("@")[0] and not e.startswith(("graduate", "pgr", "admissions")):
            out[names[0]] = e
        else:
            out[f"General contact ({e})"] = e     # e.g. graduate.studies@...
    return list(out.items())


def classify_document(filename: str) -> str:
    f = filename.lower()
    if f.startswith("~$"):
        return "temp"
    if "cv" in re.split(r"[_\-. ]", f) or "_cv" in f:
        return "cv"
    if "transcript" in f or "hear" in f:
        return "transcript"
    if "certificate" in f:
        return "certificate"
    if "proposal" in f:
        return "proposal"
    if any(k in f for k in ("statement", "letter", "motivation", "essay")):
        return "statement"
    if f.startswith("email"):
        return "email_text"
    return "other"
