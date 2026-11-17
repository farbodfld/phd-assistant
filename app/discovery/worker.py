"""Daily discovery and one-shot runs for both sources."""
import logging
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
from app.llm.client import LLMClient
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


def serve():
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)

    # Catch today's missed run after a restart.
    if datetime.now(ZoneInfo(settings.timezone)).time() >= time.fromisoformat(settings.daily_run_time):
        daily_discovery()
    run_forever({"discovery": ([settings.daily_run_time], daily_discovery)}, settings.timezone)
