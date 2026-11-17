"""One cheap model call per new advert: topic fit, funding for an international student, score."""
from typing import Literal

from pydantic import BaseModel, Field


class Screen(BaseModel):
    topic_relevant: bool = Field(description="Could the applicant's background plausibly fit this project?")
    score: int = Field(ge=0, le=100, description="Fit score per the scoring SOP")
    funding: Literal["full_international", "competition", "unclear", "home_only", "partial", "self_funded"]
    funding_note: str = Field(description="One English line for the tracker: what is funded and for whom")
    england: bool = Field(description="Is the place of study in England?")
    supervisor_contact: str = Field(description="Supervisor names and emails exactly as in the advert, or 'See advert'")
    apply_method: Literal["Email", "Portal", "Email + Portal"]
    application_route: str = Field(description="Short English line: how to apply")
    area: str = Field(description="Two to five word research area label, e.g. 'AI / health data'")
    note_fa: str = Field(description="One short Persian sentence explaining the fit or exclusion")
    exclude_reason: str | None = Field(default=None, description="Short English reason when funding or location rules it out")


SCREEN_INSTRUCTIONS = """You screen one jobs.ac.uk PhD advert for an international applicant who needs full funding:
international-rate tuition plus a stipend in England. Use only the advert text and the verified facts above.

Funding: the advert's "Funding for" field is strong evidence. "UK Students" alone means home_only unless the text
clearly says international fees are covered. If international students must pay a fee difference, it is partial.
"Competition funded" or "competitive worldwide funding" is competition. Fees covered but the international rate is
not stated: unclear.
Score with the scoring SOP: unrelated fields (music, geology, wet lab, pure maths) are not topic_relevant even with
"machine learning" in the title, unless the computational side is central.
apply_method: "Email + Portal" when a named supervisor invites informal enquiries before applying, "Portal" when only
an application system is given, "Email" when applications go by email.
Never invent names, emails, deadlines or requirements. In every English field use commas or periods, never a dash as
a sentence connector."""


def advert_prompt(a) -> str:
    return (f"Title: {a.title}\nEmployer: {a.employer}\nLocation: {a.location}\nFunding for: {a.funding_for}\n"
            f"Funding amount: {a.funding_amount}\nHours: {a.hours}\nCloses: {a.closes}\n"
            f"Subjects: {', '.join(a.subjects)}\nApply link: {a.apply_url}\n\n{a.description[:7000]}")
