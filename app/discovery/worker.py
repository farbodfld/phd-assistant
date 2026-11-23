"""Daily discovery for both sources, inbox checks at fixed times, and the `phd serve` loop."""
import logging
import threading
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from app.config import get_settings
from app.db.base import session_scope
from sqlalchemy import func, select

from app.db.models import Draft, Meta, Notification, Run
from app.discovery.fetch import HttpFetcher
from app.discovery.findaphd_browser import FindAPhDBrowser
from app.discovery.findaphd import run_findaphd
from app.discovery.pipeline import run_jobsac
from app.gmail.client import GmailClient
from app.llm.client import LLMClient
from app.mail.inbox import check_inbox, retry_unprocessed
from app.outreach.gmail_sync import push_drafts, sync_drafts
from app.outreach.run import run_outreach
from app.rules.engine import run_checks
from app.scheduler import run_forever


def discover(today: date | None = None, max_pages=None, scheduled=False, source="findaphd"):
    if source not in ("findaphd", "jobsac"):
        raise ValueError(f"unknown discovery source {source}")
    settings = get_settings()
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    folder = settings.findaphd_archive_dir
    folder.mkdir(parents=True, exist_ok=True)
    lock = FileLock(folder / "worker.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        logging.info("Discovery is already running")
        return None
    try:
        with session_scope() as s:
            marker = f"{source}:last-scheduled"
            last = s.get(Meta, marker)
            if scheduled and last and last.value == today.isoformat():
                return None
            run = Run(kind=source)
            s.add(run)
            s.commit()
            run_id = run.id
        fetcher = FindAPhDBrowser() if source == "findaphd" else HttpFetcher()
        try:
            with session_scope() as s:
                llm = LLMClient(run_id=run_id)
                if source == "jobsac":
                    report = run_jobsac(s, fetcher, llm, today, max_pages)
                else:
                    report = run_findaphd(s, fetcher, llm, folder, today, max_pages)
                if source == "jobsac" or report.added:
                    run_checks(s, today)
        except Exception as exc:
            with session_scope() as s:
                run = s.get(Run, run_id)
                run.ok, run.finished_at = False, datetime.now(timezone.utc)
                run.report = {"error": type(exc).__name__}
                s.add(Notification(kind="report", ref_type="discovery",
                                   text=f"{source} | بررسی ناقص | {type(exc).__name__}"))
            raise
        finally:
            fetcher.close()
        with session_scope() as s:
            run = s.get(Run, run_id)
            run.ok, run.finished_at = not report.errors, datetime.now(timezone.utc)
            run.report = asdict(report)
            if scheduled:
                s.merge(Meta(key=marker, value=today.isoformat()))
        logging.info("%s", report.summary())
        return report
    finally:
        lock.release()


def daily_discovery():
    for source in ("jobsac", "findaphd"):
        try:
            discover(scheduled=True, source=source)
        except Exception:
            logging.exception("%s discovery failed", source)
    try:
        outreach()                      # right after discovery: new positions get their first email drafts
    except Exception:
        logging.exception("outreach failed")


def outreach(today: date | None = None, limit: int | None = None):
    """Phase 5 daily step: reminders and first emails as drafts (and Gmail drafts until phase 6)."""
    settings = get_settings()
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    with session_scope() as s:
        run = Run(kind="outreach")
        s.add(run)
        s.commit()
        run_id = run.id
    gmail = None
    gmail_error = None
    if settings.gmail_drafts:
        try:
            gmail = GmailClient()
        except Exception as exc:              # drafts are still written to the database
            gmail_error = f"Gmail: {type(exc).__name__}: {exc}"
    fetcher = HttpFetcher()
    try:
        with session_scope() as s:
            report = run_outreach(s, LLMClient(run_id=run_id), today, gmail=gmail, fetcher=fetcher, limit=limit)
            if gmail_error:
                report.errors.insert(0, gmail_error)
    finally:
        fetcher.close()
    with session_scope() as s:
        run = s.get(Run, run_id)
        run.ok, run.finished_at = not report.errors, datetime.now(timezone.utc)
        run.report = asdict(report)
    logging.info("%s", report.summary())
    _say("review", "")                       # the bot now offers the drafts, category by category
    return report


def inbox_check(now: datetime | None = None):
    """One scheduled inbox check: store and analyse new PhD email, then retry earlier failures."""
    settings = get_settings()
    today = (now or datetime.now(ZoneInfo(settings.timezone))).date()
    with session_scope() as s:
        run = Run(kind="inbox")
        s.add(run)
        s.commit()
        run_id = run.id
    errors = []
    try:
        gmail = GmailClient()
        with session_scope() as s:
            llm = LLMClient(run_id=run_id)
            report = check_inbox(s, gmail, llm)
            s.commit()
            errors += report.errors
            try:
                retried = retry_unprocessed(s, gmail, llm, today)
            except Exception as exc:                 # the email stays unprocessed for the next check
                retried = 0
                errors.append(f"retry: {type(exc).__name__}: {exc}")
            if report.stored or retried:
                run_checks(s, today)
            if settings.gmail_drafts:
                try:                              # reply drafts reach Gmail Drafts; sent ones are recorded
                    sync_drafts(s, gmail, today)
                    push_drafts(s, gmail)
                except Exception as exc:
                    errors.append(f"Gmail drafts: {type(exc).__name__}: {exc}")
    except Exception as exc:
        with session_scope() as s:
            run = s.get(Run, run_id)
            run.ok, run.finished_at = False, datetime.now(timezone.utc)
            run.report = {"error": f"{type(exc).__name__}: {exc}"}
            s.add(Notification(kind="report", ref_type="inbox", text=f"بررسی اینباکس ناموفق | {type(exc).__name__}"))
        raise
    with session_scope() as s:
        run = s.get(Run, run_id)
        run.ok, run.finished_at = not errors, datetime.now(timezone.utc)
        run.report = {**asdict(report), "retried": retried, "errors": errors}
    logging.info("inbox: %s stored, %s analysed, %s drafts, %s retried, %s errors",
                 report.stored, report.analysed, report.drafts, retried, len(errors))
    return report


# ---------------------------------------------------------------------- the day, step by step
# 08:00: inbox -> both sites -> outreach -> review in Telegram (replies, new emails, reminders).
# 14:00, 17:00, 20:00: inbox only. One step at a time, each announced in Telegram, never interleaved.

def _say(kind: str, text: str):
    """A line for Telegram. kind 'stage' marks the start or end of a step; 'review' opens the review."""
    with session_scope() as s:
        s.add(Notification(kind=kind, ref_type="pipeline", text=text))


def _last_notification_id() -> int:
    with session_scope() as s:
        return s.scalar(select(func.max(Notification.id))) or 0


def _inbox_summary(after_id: int, report) -> str:
    """One message with what the supervisors wrote (collected from the inbox items, which the bot does not
    send on their own)."""
    with session_scope() as s:
        items = list(s.scalars(select(Notification).where(Notification.id > after_id, Notification.kind == "email")
                               .order_by(Notification.id)))
        drafts = sum(1 for n in items if n.ref_type == "draft")
        texts = [n.text for n in items]
    if not texts:
        return "📭 پیام جدیدی از استادها نیومده."
    head = f"📧 {len(texts)} ایمیل جدید از استادها"
    if drafts:
        head += f"، برای {drafts} تاش جواب نوشته شد (بعداً تو بخش جواب‌ها نشونت میدم)"
    return head + ":\n\n" + "\n\n".join(texts)


def inbox_round():
    settings = get_settings()
    hhmm = datetime.now(ZoneInfo(settings.timezone)).strftime("%H:%M")
    _say("stage", f"📥 {hhmm} | شروع بررسی inbox")
    start = _last_notification_id()
    try:
        report = inbox_check()
    except Exception as exc:
        logging.exception("inbox check failed")
        _say("stage", f"⚠️ بررسی inbox انجام نشد: {type(exc).__name__}")
        return None
    _say("report", _inbox_summary(start, report))
    _say("stage", "✅ بررسی inbox تموم شد")
    return report


def morning():
    """The whole morning run, in order. Drafts are not shown until everything is ready."""
    settings = get_settings()
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    inbox_round()
    _say("stage", "🔎 شروع جستجوی پوزیشن‌های جدید: jobs.ac.uk و FindAPhD")
    for source in ("jobsac", "findaphd"):
        try:
            discover(scheduled=True, source=source)
        except Exception as exc:
            logging.exception("%s discovery failed", source)
            _say("stage", f"⚠️ جستجوی {source} انجام نشد: {type(exc).__name__}")
    _say("stage", "✅ جستجوی دو سایت تموم شد")
    _say("stage", "✉️ شروع آماده کردن ایمیل‌ها (جواب‌ها، ایمیل‌های جدید، یادآوری‌ها)")
    try:
        outreach()                       # ends with the review invitation
    except Exception as exc:
        logging.exception("outreach failed")
        _say("stage", f"⚠️ آماده کردن ایمیل‌ها انجام نشد: {type(exc).__name__}")
        _say("review", "")
    with session_scope() as s:
        s.merge(Meta(key="pipeline:morning", value=today.isoformat()))


def afternoon():
    """14:00, 17:00, 20:00: inbox only; offer the replies for review if any were drafted."""
    inbox_round()
    with session_scope() as s:
        replies = s.scalar(select(func.count(Draft.id)).where(Draft.status == "pending", Draft.kind == "reply"))
    if replies:
        _say("review", "replies")               # straight to the replies written in this round


def serve():
    """One schedule, one step at a time. The Telegram bot runs alongside it."""
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)          # its request log prints the bot token
    from app.telegram.bot import make_bot
    bot = make_bot()
    if bot is not None:                       # phase 6: drafts wait for Approve in Telegram
        threading.Thread(target=bot.run_forever, name="telegram", daemon=True).start()
    else:
        logging.warning("TELEGRAM_BOT_TOKEN not set: no Telegram bot (drafts stay in Gmail Drafts)")
    now = datetime.now(ZoneInfo(settings.timezone))
    with session_scope() as s:
        done = s.get(Meta, "pipeline:morning")
    if now.time() >= time.fromisoformat(settings.daily_run_time) and not (done and done.value == now.date().isoformat()):
        morning()                             # catch up a missed morning after a restart
    later = [t for t in settings.inbox_check_times if t != settings.daily_run_time]
    run_forever({"morning": ([settings.daily_run_time], morning), "inbox": (later, afternoon)}, settings.timezone)
