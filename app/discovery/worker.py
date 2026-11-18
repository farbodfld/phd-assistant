"""Daily discovery for both sources, inbox checks at fixed times, and the `phd serve` loop."""
import logging
import threading
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from app.config import get_settings
from app.db.base import session_scope
from app.db.models import Meta, Notification, Run
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


def _discovery_loop(settings):
    # Catch today's missed run after a restart.
    if datetime.now(ZoneInfo(settings.timezone)).time() >= time.fromisoformat(settings.daily_run_time):
        daily_discovery()
    run_forever({"discovery": ([settings.daily_run_time], daily_discovery)}, settings.timezone)


def serve():
    """Discovery runs in its own thread: a long FindAPhD sweep must not delay the inbox checks."""
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=_discovery_loop, args=(settings,), name="discovery", daemon=True).start()
    run_forever({"inbox": (settings.inbox_check_times, inbox_check)}, settings.timezone)
