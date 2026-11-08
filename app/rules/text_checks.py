"""Code checks run on every text before Farbod sees it."""
import re
from dataclasses import dataclass

from app.knowledge.loader import load_profile


@dataclass
class Issue:
    kind: str       # dash | fabrication | tone | forbidden
    message: str
    snippet: str


def _snip(text: str, m: re.Match, width: int = 30) -> str:
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return text[a:b].replace("\n", " ")


# em dash anywhere; en dash or hyphen only when used between words with spaces.
# Number ranges like "2019-2024" or "2019–2024" are allowed.
_DASH = re.compile(r"—|(?<=\S)\s+[–-]\s+(?=\S)|(?<=[A-Za-z])–(?=[A-Za-z])")


def find_dash_connectors(text: str) -> list[Issue]:
    return [Issue("dash", "dash used as a sentence connector; use a comma or period", _snip(text, m))
            for m in _DASH.finditer(text)]


def fix_dash_connectors(text: str) -> str:
    """Safe automatic fix: replace connector dashes with a comma."""
    text = re.sub(r"\s*—\s*", ", ", text)
    text = re.sub(r"(?<=\S)\s+[–-]\s+(?=\S)", ", ", text)
    return text


_TONE = [
    (r"\bshould i (?:apply|strengthen)\b", "asks the supervisor whether to apply"),
    (r"\bdo you (?:recommend|think) (?:that )?i (?:should )?apply\b", "asks the supervisor whether to apply"),
    (r"\bis it worth (?:my )?applying\b", "asks the supervisor whether to apply"),
    (r"\bi am not sure (?:if|whether) i\b", "sounds unsure of himself"),
]


def find_tone_problems(text: str) -> list[Issue]:
    out = []
    for pat, msg in _TONE:
        for m in re.finditer(pat, text, re.I):
            out.append(Issue("tone", msg, _snip(text, m)))
    return out


def find_fabrications(text: str) -> list[Issue]:
    """Claims that contradict profile.yaml. Not exhaustive; the strong model also self-checks."""
    profile = load_profile()
    rules: list[tuple[str, str]] = [
        (r"(?:BSc|B\.Sc\.?|Bachelor'?s?)[^.\n]{0,40}Computer Science", "his BSc is Computer Engineering, not Computer Science"),
        (r"Slim Chickens", "the Slim Chickens job must not appear in academic applications"),
        (r"\bfirst[- ]class\b", "he does not have a first-class degree"),
        (r"(?:MSc|Master'?s)[^.\n]{0,40}\bDistinction\b|\bDistinction\b[^.\n]{0,40}(?:MSc|Master)", "his MSc is a Merit, not a Distinction"),
        (r"designed (?:the )?relational schemas?", "overstates his database work"),
        (r"\bIELTS\b[^.\n]{0,15}\d", "he has no IELTS score on record"),
    ]
    if not profile.get("publications"):
        rules.append((r"\b(?:I|my|we)\b[^.\n]{0,40}\b(?:published|publication|co-authored|paper (?:was )?accepted)\b",
                      "he has no publications"))
    for course in profile["education"][1].get("courses_to_avoid", []):
        rules.append((re.escape(course), f"do not cite {course} (low grade)"))
    out = []
    for pat, msg in rules:
        for m in re.finditer(pat, text, re.I):
            out.append(Issue("fabrication", msg, _snip(text, m)))
    return out


def check_outgoing_text(text: str, *, outreach: bool = False) -> list[Issue]:
    issues = find_dash_connectors(text) + find_fabrications(text)
    if outreach:
        issues += find_tone_problems(text)
    return issues
