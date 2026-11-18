"""Classify a position's funding for an international applicant from its text."""
import re

# (category, pattern, reason). First match wins, so order matters.
_RULES = [
    # An award that covers the home/international difference makes the position fully funded.
    ("full_international", r"(?:covering|covers|covered|cover|waived|waives|including)[^.;]{0,30}(?:international )?(?:fee|rate) difference|"
                           r"fees? at the international rate (?:are |is )?(?:waived|covered|paid)|"
                           r"(?:international )?fee difference (?:is |are )?(?:covered|waived|paid)",
     "international fee difference covered by an award"),
    ("home_only", r"home[- ](?:rate|fee|fees|tuition)[^.;]{0,25}only|home fee status (?:is )?(?:required|only)|"
                  r"uk (?:students|applicants|nationals) only|\buk only\b|british(?:/| or )european|"
                  r"china scholarship council|chinese national",
     "home or UK only"),
    ("partial", r"(?:tuition|fees?)[^.;]{0,30}(?:at|of) (?:the )?(?:home|uk)[- ](?:fee )?(?:rate|level)|"
                r"(?:home|uk)[- ]fee level|fees at the uk rate",
     "fees at the home/UK rate"),
    ("competition", r"international (?:places|students|applicants)[^.;]{0,20}(?:capped|limited)|"
                    r"limited number of (?:international|overseas)",
     "international places are limited, competitive"),
    ("partial", r"pay (?:the )?(?:fee )?difference|fee difference|difference between (?:home|uk) and international",
     "international students pay the fee difference"),
    ("home_only", r"self[- ]funded only|self[- ]funding required|not a funded", "self-funded"),
    ("competition", r"competition funded", "competition funded, not guaranteed"),
    ("unclear", r"funding of fees|international \(waiver\)|funding not specified|funding unclear|"
                r"confirm (?:full )?international fee|overseas places capped", "fees covered but international rate not stated"),
    ("full_international", r"students worldwide|international (?:students|applicants)[^.]{0,40}(?:eligible|welcome|fees)|"
                           r"(?:covers?|including) international (?:tuition )?fees|uk and international|"
                           r"home or international|international fee waiver", "international fees covered"),
]


def classify_funding(*texts: str | None) -> tuple[str, str]:
    t = " ".join(x for x in texts if x).lower()
    for category, pattern, reason in _RULES:
        if re.search(pattern, t):
            return category, reason
    return "unknown", "no funding information found"
