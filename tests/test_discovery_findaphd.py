from datetime import date

from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Notification, Position, Run
from app.discovery.fetch import Page
from app.discovery.findaphd import parse_project, run_findaphd
from app.discovery.sources import FINDAPHD
from app.discovery import worker
from tests.fakes import FakeLLM
from tests.test_discovery_jobsac import _v

URL = FINDAPHD.base + "/phds/project/robots/?p123"
FIRST = FINDAPHD.list_url.split("&PG=")[0]
LIST = f'<div id="SearchResults"><div class="resultsRow"><a href="{URL}">Robots</a></div></div>'
AD = f"""<link rel="canonical" href="{URL}"><meta property="og:title"
content="Robots at University of Leeds on FindAPhD.com"><h1>Robots</h1>
<div class="phd-sections__description"><div class="phd-sections__content">Project body</div></div>
<div class="phd-sections__funding-notes"><div class="phd-sections__content">Full fees</div></div>
<span class="key-info__content"><i class="fa-calendar"></i>Tuesday, June 01, 2027</span>"""
TODAY = date(2026, 11, 17)


class Pages:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        html = self.pages[url]
        return Page(url, 200 if html else 403, html or "challenge", "http", blocked=html is None)

    def close(self):
        pass


def test_archive_both_pages_and_resume_without_duplicate(db, tmp_path):
    next_url = FINDAPHD.page_url(2)
    fetcher = Pages({FIRST: LIST + f'<link rel="next" href="{next_url}">', next_url: LIST, URL: AD})
    llm = FakeLLM({"score_position": [_v()]})
    with session_scope() as s:
        first = run_findaphd(s, fetcher, llm, tmp_path, TODAY)
        second = run_findaphd(s, fetcher, llm, tmp_path, TODAY)
        assert (first.pages, first.listed, first.added, second.added) == (2, 1, 1, 0)
        assert not first.errors and not second.errors
        assert fetcher.calls.count(URL) == 1 and len(llm.calls) == 1
        p = s.scalars(select(Position)).one()
        assert p.source == "findaphd" and p.university == "University of Leeds"
        assert p.deadline_date == date(2027, 6, 1)
        assert parse_project(AD, "123").funding_amount == "Full fees"
        assert (tmp_path / "findaphd_project_123.html").read_text("utf-8") == AD


def test_scheduled_block_recorded_once_and_next_day_retries(db, tmp_path, monkeypatch):
    monkeypatch.setattr(worker.get_settings(), "findaphd_archive_dir", tmp_path)
    pages = Pages({FIRST: None})
    monkeypatch.setattr(worker, "FindAPhDBrowser", lambda: pages)
    monkeypatch.setattr(worker, "LLMClient", lambda **kwargs: FakeLLM({}))
    with session_scope() as s:
        s.add(Position(source="findaphd", title="Existing", link=URL))
    assert worker.discover(TODAY, scheduled=True).errors
    assert worker.discover(TODAY, scheduled=True) is None
    assert worker.discover(date(2026, 11, 18), scheduled=True).errors
    assert pages.calls == [FIRST, FIRST]
    with session_scope() as s:
        assert all(r.ok is False for r in s.scalars(select(Run)))
        assert "403" in s.scalars(select(Notification.text)).first()
        assert not s.scalars(select(Position)).one().closed
    assert not list(tmp_path.glob("*.html"))


def test_daily_discovery_runs_second_source_after_first_raises(monkeypatch):
    calls = []

    def run(scheduled, source):
        assert scheduled
        calls.append(source)
        if source == "jobsac":
            raise RuntimeError("first source failed")

    monkeypatch.setattr(worker, "discover", run)
    worker.daily_discovery()
    assert calls == ["jobsac", "findaphd"]


def test_both_scheduled_reports_are_saved_separately(db, tmp_path, monkeypatch):
    from app.discovery.sources import JOBSAC
    monkeypatch.setattr(worker.get_settings(), "findaphd_archive_dir", tmp_path)
    jobs = Pages({JOBSAC.page_url(1): None})
    find = Pages({FIRST: None})
    monkeypatch.setattr(worker, "HttpFetcher", lambda: jobs)
    monkeypatch.setattr(worker, "FindAPhDBrowser", lambda: find)
    monkeypatch.setattr(worker, "LLMClient", lambda **kwargs: FakeLLM({}))
    worker.daily_discovery()
    worker.daily_discovery()
    assert jobs.calls == [JOBSAC.page_url(1)] and find.calls == [FIRST]
    with session_scope() as s:
        assert {(r.kind, r.ok) for r in s.scalars(select(Run))} == {("jobsac", False), ("findaphd", False)}
