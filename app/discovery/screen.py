"""One cheap model call per new advert: topic fit, funding for an international student, score."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.rules.funding import classify_funding

NOT_FUNDED = ("home_only", "partial", "self_funded")


class Screen(BaseModel):
    topic_relevant: bool = Field(description="False ONLY when the project has no meaningful computational part or "
                                             "requires a degree he does not have. Any field counts. When in doubt, true.")
    computing_share: int = Field(ge=0, le=40, description="Scoring SOP part 1")
    skills_fit: int = Field(ge=0, le=40, description="Scoring SOP part 2")
    topic_proximity: int = Field(ge=0, le=20, description="Scoring SOP part 3")
    score: int = Field(default=0, ge=0, le=100, description="Leave 0: it is the sum of the three parts")
    funding: Literal["full_international", "competition", "unclear", "home_only", "partial", "self_funded"]
    funding_note: str = Field(description="One English line for the tracker: what is funded and for whom")
    england: bool = Field(description="Is the place of study in England?")
    supervisor_contact: str = Field(description="Supervisor names and emails exactly as in the advert, or 'See advert'")
    apply_method: Literal["Email", "Portal", "Email + Portal"]
    application_route: str = Field(description="Short English line: how to apply")
    area: str = Field(description="Two to five word research area label, e.g. 'AI / health data'")
    note_fa: str = Field(description="One short Persian sentence explaining the fit or exclusion")
    exclude_reason: str | None = Field(default=None, description="Short English reason when funding or location rules it out")

    @model_validator(mode="after")
    def _sum_parts(self):
        self.score = self.computing_share + self.skills_fit + self.topic_proximity   # never trusted from the model
        return self

    @classmethod
    def with_score(cls, score: int, **kw) -> "Screen":
        """A Screen whose parts add up to a stored score (rechecks and tests)."""
        cs = min(40, score)
        sf = min(40, max(0, score - 40))
        return cls(computing_share=cs, skills_fit=sf, topic_proximity=max(0, score - 80), **kw)


SCREEN_INSTRUCTIONS = """You screen one jobs.ac.uk PhD advert for an international applicant who needs full funding:
international-rate tuition plus a stipend in England. Use only the advert text and the verified facts above.

Funding: the advert's "Funding for" field is strong evidence. "UK Students" alone means home_only unless the text
clearly says international fees are covered. If international students must pay a fee difference, it is partial.
"Competition funded" or "competitive worldwide funding" is competition. Fees covered but the international rate is
not stated: unclear.
Fees "at the home rate", "at the UK rate" or "at home-fee level": partial, never unclear.
A limited or capped number of international places that are fully funded (international fees covered or waived)
is competition, not partial. partial only when the funding itself stops at the home or UK fee.
Relevance and score follow the scoring SOP: interdisciplinary projects count whenever a substantial part of the work
is computational, whatever the field. Simulation, HPC, bioinformatics, computational biology, formal methods,
programming languages, data analysis and modelling in code are all computational, even in a domain he has not
worked in: an unfamiliar domain lowers topic_proximity, it does not make the project irrelevant. Set topic_relevant
to false only for wet lab, fieldwork, pure mathematics or theory with no coding, law, humanities, or when a degree he
does not have is stated as essential. When in doubt, set it to true. Give the three score parts (computing_share, skills_fit, topic_proximity);
the total is added up for you.
apply_method: "Email + Portal" when a named supervisor invites informal enquiries before applying, "Portal" when only
an application system is given, "Email" when applications go by email.
Never invent names, emails, deadlines or requirements. In every English field use commas or periods, never a dash as
a sentence connector."""


def advert_prompt(a) -> str:
    return (f"Title: {a.title}\nEmployer: {a.employer}\nLocation: {a.location}\nFunding for: {a.funding_for}\n"
            f"Funding amount: {a.funding_amount}\nHours: {a.hours}\nCloses: {a.closes}\n"
            f"Subjects: {', '.join(a.subjects)}\nApply link: {a.apply_url}\n\n{a.description[:7000]}")


def decide(v: Screen, *advert_text: str) -> tuple[str, str]:
    """('add' | 'exclude' | 'off_topic', reason). There is no minimum score. The model's funding view is
    checked against the deterministic funding rules, and either one saying 'not funded' excludes."""
    if not v.topic_relevant:
        return "off_topic", "no substantial computational part"
    if not v.england:
        return "exclude", v.exclude_reason or "Outside England"
    rule, why = classify_funding(*advert_text, v.funding_note)
    if v.funding in NOT_FUNDED or rule in NOT_FUNDED:
        return "exclude", v.exclude_reason or v.funding_note or why
    return "add", ""
