"""What the strong model returns for every supervisor or admissions email."""
from typing import Literal

from pydantic import BaseModel, Field


class InsightOut(BaseModel):
    scope_type: Literal["position", "department", "university"]
    scope_value: str = Field(description="Position title, department name, or university name")
    text: str = Field(description="The lesson in one or two sentences, in English")


class EmailAnalysis(BaseModel):
    category: Literal["positive", "rejection", "question", "request_docs", "admin", "other"]
    summary_fa: str = Field(description="Two or three sentences in Persian for Farbod")
    status_note: str = Field(description="Short English note for the tracker Status column")
    insights: list[InsightOut] = Field(default_factory=list)
    close_position: bool = False
    close_reason: str | None = None
    new_apply_method: Literal["Email", "Portal", "Email + Portal"] | None = None
    user_tasks: list[str] = Field(default_factory=list, description="Things only Farbod can do, in Persian")
    needs_reply: bool = False
    reply_body: str | None = Field(default=None, description="Full English reply, plain text, signed 'Best regards,\\nFarbod Fooladi'")
    reply_notes: list[str] = Field(default_factory=list, description="What Farbod should know about the reply, in Persian")


class Relevance(BaseModel):
    relevant: bool
    reason: str


ANALYSE_INSTRUCTIONS = """You handle Farbod's PhD-search email. Analyse the NEW email below and fill every field.

- category: positive (encouraging, invites application), rejection (no, not shortlisted, not taking forward),
  question (they ask him something), request_docs (they ask for documents), admin (portal/receipt/reference
  notices), other.
- insights: only lessons that change how we treat this position, its department or the whole university
  (funding rules, "apply via the portal only", "read X first", deadlines, eligibility). Pick the widest scope
  that is clearly true. Do not repeat what the earlier thread already said.
- close_position: true only for a clear rejection, or when they ask him not to apply.
- new_apply_method: set only when the email changes how to apply (e.g. "no individual discussions" -> Portal).
- user_tasks: concrete actions only Farbod can take (try software, read papers, log in, pay, submit).
- needs_reply / reply_body: reply when politeness or the conversation requires it (questions, positive
  replies, document requests, rejections deserve a short thank-you that may ask about other funded positions).
  Admin notices and automated emails need no reply. Follow the SOPs: confident tone, no dash connectors,
  only facts from the verified profile, never ask whether he should apply. If documents are requested, say they
  are attached and list them in reply_notes.
- summary_fa, user_tasks, reply_notes: Persian. status_note, insights, reply_body: English.
"""

RELEVANCE_INSTRUCTIONS = """Farbod is applying for funded PhD positions in the UK. Decide whether this email from a
university address is part of that search (a supervisor, admissions, a doctoral college, an application portal,
a referee about his references, a payment for a PhD application fee). Not relevant: newsletters, marketing, online
courses, events, alumni or sports mail, IT and Wi-Fi or password notices, careers services, applications for staff
jobs (KTP associate, research assistant, HR recruitment), and his own taught-degree admin at the University of
Essex where he studied. Keep "reason" to one short sentence."""
