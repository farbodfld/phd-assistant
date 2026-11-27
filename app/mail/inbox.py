"""Inbox processing: backfill history once, then check at the scheduled times."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (Application, Draft, EmailMessage, Insight, Meta,
                           Notification, Position, UserTask)
from app.gmail.parse import ParsedEmail, parse_message
from app.knowledge.loader import build_context
from app.mail.analysis import (ANALYSE_INSTRUCTIONS, RELEVANCE_INSTRUCTIONS,
                               EmailAnalysis, Relevance)
from app.mail.matching import (match_by_university, match_position,
                                relevance, remember_contact)
from app.rules.text_checks import check_outgoing_text, fix_dash_connectors

LAST_CHECK = "last_inbox_check"


@dataclass
class InboxReport:
    fetched: int = 0
    stored: int = 0
    analysed: int = 0
    skipped: int = 0
    drafts: int = 0
    closed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _store(s: Session, e: ParsedEmail, position: Position | None, processed: bool) -> EmailMessage:
    row = EmailMessage(gmail_id=e.gmail_id, thread_id=e.thread_id, position_id=position.id if position else None,
                       direction=e.direction, sender=e.sender, recipients=", ".join(e.to + e.cc),
                       subject=e.subject, sent_at=e.sent_at, body=e.body, message_id_header=e.message_id,
                       processed=processed)
    s.add(row)
    s.flush()
    return row


def _already(s: Session, gmail_id: str) -> bool:
    return s.scalar(select(EmailMessage.id).where(EmailMessage.gmail_id == gmail_id)) is not None


# ------------------------------------------------------------------ backfill
def _is_relevant(llm, e: ParsedEmail) -> bool:
    """When the model cannot decide, keep the thread: losing a real email is worse than storing noise."""
    from app.llm.client import LLMOutputError
    try:
        v = llm.complete("email_relevance", f"From: {e.sender_name} <{e.sender}>\nTo: {', '.join(e.to)}\n"
                     f"Subject: {e.subject}\n\n{e.body[:1500]}",
                     context=build_context("email_relevance"), instructions=RELEVANCE_INSTRUCTIONS, output=Relevance)
    except LLMOutputError:
        return True
    return v.relevant


def reset_email_history(s: Session):
    """Forget imported email (and addresses learned from it); tracker contacts are rebuilt."""
    from app.db.models import Contact, Draft
    from app.tracker.parsing import parse_contacts
    for model in (Draft, Notification, EmailMessage, Contact):
        s.query(model).delete()
    for p in s.scalars(select(Position)):
        for name, email in parse_contacts(p.supervisor_contact):
            s.add(Contact(position_id=p.id, name=name, email=email))
    meta = s.get(Meta, LAST_CHECK)
    if meta:
        s.delete(meta)
    s.flush()


def backfill(s: Session, gmail, since: date, llm=None, progress=print) -> InboxReport:
    """Import past PhD conversations, thread by thread, without analysing them.

    A thread is kept when it matches a position or a known contact; other university
    threads are screened once by the cheap model (newsletters, Wi-Fi, job applications
    are dropped). Nothing is drafted or sent."""
    r = InboxReport()
    q = f"after:{since:%Y/%m/%d} (from:ac.uk OR to:ac.uk OR from:edu OR to:edu)"
    ids = [i for i in gmail.search(q) if not _already(s, i)]
    progress(f"{len(ids)} new candidate messages")
    threads: dict[str, list[ParsedEmail]] = {}
    for n, mid in enumerate(ids, 1):
        e = parse_message(gmail.get(mid), gmail.me)
        threads.setdefault(e.thread_id, []).append(e)
        if n % 50 == 0:
            progress(f"  {n}/{len(ids)} fetched")
    r.fetched = len(ids)
    progress(f"{len(threads)} threads, screening")
    for msgs in threads.values():
        msgs.sort(key=lambda m: m.sent_at)
        kinds = {relevance(s, m) for m in msgs}
        if kinds == {"skip"}:
            r.skipped += len(msgs)
            continue
        p = next((x for x in (match_position(s, m) for m in msgs) if x), None)
        if p is None and "known" not in kinds:
            first_in = next((m for m in msgs if m.direction == "in"), msgs[0])
            if llm is not None and not _is_relevant(llm, first_in):
                r.skipped += len(msgs)
                continue
            p = next((x for x in (match_by_university(s, m) for m in msgs) if x), None)
        for m in msgs:
            if p:
                remember_contact(s, p, m)
            row = _store(s, m, p, processed=True)
            if m.auto_reply:
                row.category = "auto_reply"
            r.stored += 1
        s.commit()
    return r


# ------------------------------------------------------------------ analysis
def _thread_history(s: Session, thread_id: str, exclude: str, limit: int = 4) -> str:
    rows = list(s.scalars(select(EmailMessage).where(EmailMessage.thread_id == thread_id,
                                                     EmailMessage.gmail_id != exclude)
                          .order_by(EmailMessage.sent_at)))[-limit:]
    return "\n\n".join(f"[{r.sent_at:%Y-%m-%d}] {'Farbod' if r.direction == 'out' else r.sender}:\n{(r.body or '')[:1500]}"
                       for r in rows) or "(none)"


def _prompt(s: Session, e: ParsedEmail, p: Position | None) -> str:
    if p:
        pos = (f"Title: {p.title}\nUniversity: {p.university}\nSupervisor/contact: {p.supervisor_contact}\n"
               f"Deadline: {p.deadline_text}\nApply method: {p.apply_method}\nClosed: {p.closed}\n"
               f"Status history: {(p.status or '')[:800]}")
    else:
        pos = "No matching position in the tracker."
    return (f"## Position\n{pos}\n\n## Earlier messages in this thread\n{_thread_history(s, e.thread_id, e.gmail_id)}\n\n"
            f"## NEW email\nFrom: {e.sender_name} <{e.sender}>\nDate: {e.sent_at:%Y-%m-%d %H:%M}\n"
            f"Subject: {e.subject}\n\n{e.body}")


def apply_analysis(s: Session, row: EmailMessage, e: ParsedEmail, p: Position | None, a: EmailAnalysis,
                   today: date) -> Draft | None:
    row.category, row.summary, row.processed = a.category, a.summary_fa, True
    title = p.title if p else e.subject
    if p:
        if a.close_position:
            p.closed = True
            p.add_status(f"CLOSED: {a.close_reason or a.status_note} ({e.sender_name})", today)
        else:
            p.add_status(f"{a.category} reply from {e.sender_name}: {a.status_note}", today)
        if a.new_apply_method:
            p.apply_method = a.new_apply_method
            if "Portal" in a.new_apply_method:      # phase 8: a pack is prepared in the next morning run
                s.merge(Meta(key=f"portal-invited:{p.id}", value=today.isoformat()))
        if a.category == "rejection":
            for app_ in s.scalars(select(Application).where(Application.position_id == p.id)):
                app_.outcome = "rejected"
    for ins in a.insights:
        scope_value = (p.title if (ins.scope_type == "position" and p) else ins.scope_value)[:120]
        s.add(Insight(scope_type=ins.scope_type, scope_value=scope_value, text=ins.text, source="email",
                      source_ref=e.sender_name, learned_on=e.sent_at.date()))
    for t in a.user_tasks:
        s.add(UserTask(position_id=p.id if p else None, text=t, due_on=p.deadline_date if p else None))

    draft = None
    if a.needs_reply and a.reply_body:
        body = fix_dash_connectors(a.reply_body)
        issues = [f"{i.kind}: {i.message} [{i.snippet}]" for i in check_outgoing_text(body)]
        subject = e.subject if e.subject.lower().startswith("re:") else f"Re: {e.subject}"
        draft = Draft(position_id=p.id if p else None, reply_to_email_id=row.id, kind="reply", to=e.sender,
                      subject=subject, body=body, thread_id=e.thread_id, in_reply_to=e.message_id,
                      attachments=[], notes=a.reply_notes, issues=issues)
        s.add(draft)
        s.flush()

    lines = [f"📧 {e.sender_name}: {title}", f"دسته: {a.category}", a.summary_fa]
    if a.close_position:
        lines.append("🔒 این موقعیت بسته شد.")
    if a.new_apply_method:
        lines.append(f"↪️ روش اپلای تغییر کرد به {a.new_apply_method}.")
        if "Portal" in a.new_apply_method:
            lines.append("🗂 به لیست پورتال‌ها اضافه شد.")
    lines += [f"✅ کار برای شما: {t}" for t in a.user_tasks]
    if draft:
        lines.append("✍️ پیش‌نویس جواب آماده است.")
    s.add(Notification(kind="email", text="\n".join(lines), ref_type="draft" if draft else "email",
                       ref_id=draft.id if draft else row.id))
    return draft


def check_inbox(s: Session, gmail, llm, now: datetime | None = None, lookback_days: int | None = None) -> InboxReport:
    now = now or datetime.now()
    r = InboxReport()
    meta = s.get(Meta, LAST_CHECK)
    if lookback_days is None:
        from app.config import get_settings
        lookback_days = get_settings().inbox_lookback_days
    window = now - timedelta(days=lookback_days)
    # Always re-read at least the last few days (a late-arriving or mislabelled reply is not missed);
    # messages already stored are skipped by gmail_id, so this costs no LLM calls.
    since = min(datetime.fromisoformat(meta.value), window) if meta else window
    since -= timedelta(minutes=10)                      # overlap; duplicates are skipped by gmail_id
    ids = gmail.search(f"after:{int(since.timestamp())} -in:drafts -in:chats")
    for mid in ids:
        r.fetched += 1
        if _already(s, mid):
            continue
        e = parse_message(gmail.get(mid), gmail.me)
        kind = relevance(s, e)
        if kind == "skip":
            r.skipped += 1
            continue
        if kind == "academic" and e.direction == "in":
            if not _is_relevant(llm, e):
                r.skipped += 1
                continue
        p = match_position(s, e) or (match_by_university(s, e) if e.direction == "in" else None)
        if p:
            remember_contact(s, p, e)
        row = _store(s, e, p, processed=(e.direction == "out" or e.auto_reply))
        r.stored += 1
        if e.auto_reply:
            row.category = "auto_reply"
        if e.direction == "out" or e.auto_reply:
            continue
        try:
            a = llm.complete("analyse_email", _prompt(s, e, p), context=build_context("analyse_email"),
                             instructions=ANALYSE_INSTRUCTIONS, output=EmailAnalysis)
        except Exception as exc:                        # keep going; the email stays unprocessed for the next run
            r.errors.append(f"{e.subject}: {exc}")
            continue
        r.analysed += 1
        if apply_analysis(s, row, e, p, a, now.date()):
            r.drafts += 1
        if a.close_position and p:
            r.closed.append(p.title)
    s.merge(Meta(key=LAST_CHECK, value=now.isoformat()))
    return r


def retry_unprocessed(s: Session, gmail, llm, today: date) -> int:
    """Re-analyse incoming emails whose analysis failed last time."""
    n = 0
    for row in s.scalars(select(EmailMessage).where(EmailMessage.processed.is_(False), EmailMessage.direction == "in")):
        e = parse_message(gmail.get(row.gmail_id), gmail.me)
        p = s.get(Position, row.position_id) if row.position_id else None
        a = llm.complete("analyse_email", _prompt(s, e, p), context=build_context("analyse_email"),
                         instructions=ANALYSE_INSTRUCTIONS, output=EmailAnalysis)
        apply_analysis(s, row, e, p, a, today)
        n += 1
    return n
