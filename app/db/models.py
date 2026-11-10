"""Database schema.

The Excel tracker is a *view* of the `positions` table. Everything the agent
learns, writes or sends is recorded here so it can always explain itself.
"""
from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey,
                        Integer, String, Text, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ---------------------------------------------------------------- enums (str)
SOURCES = ("findaphd", "jobsac")
APPLY_METHODS = ("Email", "Portal", "Email + Portal")
FUNDING = ("full_international", "competition", "unclear", "home_only", "partial", "unknown")


class Position(Base):
    """One row of the tracker (either sheet)."""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))           # findaphd | jobsac
    title: Mapped[str] = mapped_column(Text)
    university: Mapped[str | None] = mapped_column(Text)
    supervisor_contact: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(Integer)

    deadline_text: Mapped[str | None] = mapped_column(String(64))   # as shown: "1 Dec 2026", "Rolling"
    deadline_date: Mapped[date | None] = mapped_column(Date)        # parsed, for logic

    apply_method: Mapped[str | None] = mapped_column(String(16))
    email_done: Mapped[bool] = mapped_column(Boolean, default=False)   # tracker "Email" ✓
    portal_done: Mapped[bool] = mapped_column(Boolean, default=False)  # tracker "Portal" ✓
    email_sent: Mapped[str | None] = mapped_column(String(32))
    reminder_1: Mapped[str | None] = mapped_column(String(32))
    reminder_2: Mapped[str | None] = mapped_column(String(32))
    portal_submitted: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[str | None] = mapped_column(Text)      # "Status / Next Action", newest first, " | " separated
    fit_note: Mapped[str | None] = mapped_column(Text)    # FindAPhD "Why it fits" / jobs.ac.uk "Funding (international)"
    application_route: Mapped[str | None] = mapped_column(Text)  # jobs.ac.uk only
    type: Mapped[str | None] = mapped_column(Text)               # jobs.ac.uk only
    link: Mapped[str | None] = mapped_column(Text)

    closed: Mapped[bool] = mapped_column(Boolean, default=False)  # greyed in tracker
    funding: Mapped[str] = mapped_column(String(24), default="unknown")
    folder_name: Mapped[str | None] = mapped_column(Text)
    sort_tiebreak: Mapped[int] = mapped_column(Integer, default=0)  # keeps the original order among equal scores

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    contacts: Mapped[list["Contact"]] = relationship(back_populates="position", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="position", cascade="all, delete-orphan")
    flags: Mapped[list["Flag"]] = relationship(back_populates="position", cascade="all, delete-orphan")

    def add_status(self, text: str, on: date | None = None):
        """Prepend a dated status update, keeping history after ' | ' (tracker convention)."""
        stamp = (on or date.today()).isoformat()
        new = f"{stamp}: {text}"
        self.status = f"{new} | {self.status}" if self.status else new


class Contact(Base):
    """A supervisor / contact person parsed from the tracker or emails."""
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[Position] = relationship(back_populates="contacts")


class ExcludedPosition(Base):
    """'Checked and excluded' list: seen, not suitable, with the reason."""
    __tablename__ = "excluded_positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Application(Base):
    """A formal application submitted through a portal."""
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    university: Mapped[str] = mapped_column(Text)
    project: Mapped[str] = mapped_column(Text)
    supervisor: Mapped[str | None] = mapped_column(Text)
    submitted_on: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(24), default="pending")  # pending | rejected | offer | withdrawn
    notes: Mapped[str | None] = mapped_column(Text)


class Document(Base):
    """A file in a position folder (CV, transcript, statement, proposal, email text...)."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    folder_name: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))   # cv | transcript | certificate | statement | proposal | email_text | other
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_final: Mapped[bool] = mapped_column(Boolean, default=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime)
    position: Mapped[Position | None] = relationship(back_populates="documents")


class Requirement(Base):
    """A document a position asked for, and where we learned it."""
    __tablename__ = "requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    kind: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(24))   # advert | email | portal | manual


class Submission(Base):
    """Which document version went where, when."""
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    channel: Mapped[str] = mapped_column(String(16))   # email | portal
    recipient: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime)


class EmailMessage(Base):
    __tablename__ = "emails"
    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(String(64), unique=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    direction: Mapped[str] = mapped_column(String(8))   # in | out
    sender: Mapped[str] = mapped_column(Text)
    recipients: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime)
    category: Mapped[str | None] = mapped_column(String(24))   # positive | rejection | question | request_docs | admin | other
    summary: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    message_id_header: Mapped[str | None] = mapped_column(Text)   # for In-Reply-To when replying
    processed: Mapped[bool] = mapped_column(Boolean, default=False)


class Draft(Base):
    """An email waiting for Farbod's decision in Telegram (Approve / Deny / Edit)."""
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    reply_to_email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id"))
    kind: Mapped[str] = mapped_column(String(16))          # reply | initial | reminder
    to: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(String(64))
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list | None] = mapped_column(JSON)  # file paths
    notes: Mapped[list | None] = mapped_column(JSON)        # what Farbod should know (e.g. funding question added)
    issues: Mapped[list | None] = mapped_column(JSON)       # text-check findings left after auto-fix
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | denied | sent | superseded
    version: Mapped[int] = mapped_column(Integer, default=1)
    sent_gmail_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Notification(Base):
    """Outbox for Telegram (phase 6). Written by every workflow, sent by the bot."""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))      # email | decision | task | report | error
    text: Mapped[str] = mapped_column(Text)            # Persian, for Farbod
    ref_type: Mapped[str | None] = mapped_column(String(24))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class Insight(Base):
    """Something learned (from an advert, a reply, a portal) that applies to a scope.

    Example: Mahmoodi said Southampton funding is UK/EU only -> scope university=Southampton.
    """
    __tablename__ = "insights"
    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(16))   # position | department | university | global
    scope_value: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24))       # advert | email | portal | manual | status_file
    source_ref: Mapped[str | None] = mapped_column(Text)
    learned_on: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Flag(Base):
    """A risk or open question on a position, with what the agent will do about it."""
    __tablename__ = "flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    kind: Mapped[str] = mapped_column(String(32))     # funding_unclear | application_limit | ai_policy | english_requirement | entry_requirement | ...
    message: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(16))   # inform | followup | decide | block
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | resolved
    due_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    position: Mapped[Position | None] = relationship(back_populates="flags")


class UserTask(Base):
    """Something only Farbod can do (log in, pay, try ESBMC, decide Oxford choices...)."""
    __tablename__ = "user_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    text: Mapped[str] = mapped_column(Text)
    due_on: Mapped[date | None] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(Base):
    """One execution of a workflow (daily run, inbox check, chat turn)."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    ok: Mapped[bool | None] = mapped_column(Boolean)
    report: Mapped[dict | None] = mapped_column(JSON)


class LLMCall(Base):
    """Every model call, with tokens and cost, for the budget guard and reports."""
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    task: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(48))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Meta(Base):
    """Free-form key/value store (e.g. the 'About this search' sheet text)."""
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | None] = mapped_column(JSON)
