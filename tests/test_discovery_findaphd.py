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
    monkeypatch.setattr(worker, "outreach", lambda: calls.append("outreach"))
    worker.daily_discovery()
    assert calls == ["jobsac", "findaphd", "outreach"]


def test_both_scheduled_reports_are_saved_separately(db, tmp_path, monkeypatch):
    from app.discovery.sources import JOBSAC
    monkeypatch.setattr(worker.get_settings(), "findaphd_archive_dir", tmp_path)
    jobs = Pages({JOBSAC.page_url(1): None})
    find = Pages({FIRST: None})
    monkeypatch.setattr(worker, "HttpFetcher", lambda: jobs)
    monkeypatch.setattr(worker, "FindAPhDBrowser", lambda: find)
    monkeypatch.setattr(worker, "LLMClient", lambda **kwargs: FakeLLM({}))
    monkeypatch.setattr(worker, "outreach", lambda: None)
    worker.daily_discovery()
    worker.daily_discovery()
    assert jobs.calls == [JOBSAC.page_url(1)] and find.calls == [FIRST]
    with session_scope() as s:
        assert {(r.kind, r.ok) for r in s.scalars(select(Run))} == {("jobsac", False), ("findaphd", False)}


# Programme listings and real-page regressions ------------------------------------------------------------

from pathlib import Path

from app.discovery.findaphd import Card, parse_listing, parse_program, parse_results, project_id

FIXTURES = Path(__file__).parent / "fixtures"
PROG_A = FINDAPHD.base + "/phds/program/crocus-dla/?i805p6738"
PROG_B = FINDAPHD.base + "/phds/program/crocus-dla/?i3230p6738"
PROG_CANON = FINDAPHD.base + "/phds/program/crocus-dla/?p6738"
WALLET = '<span class="icon-text"><i class="fa-regular fa-wallet"></i> Funded PhD Programme (Students Worldwide)</span>'
PROG_ROW = ('<div class="resultsRow phd-result"><a href="{u}"><h3><span title="t">CROCUS DLA</span></h3>'
            '<div class="phd-result__dept-inst--inst">Institute of Zoology</div>' + WALLET + '</a></div>')
PROG_LIST = ('<div id="SearchResults">' + PROG_ROW.format(u=PROG_A) + PROG_ROW.format(u=PROG_B)
             + f'<div class="resultsRow phd-result"><a href="{URL}">Robots</a></div></div>')
PROG_PAGE = f"""<link rel="canonical" href="{PROG_CANON}">
<meta property="og:title" content="FindAPhD : CROCUS DLA at University of Reading">
<div id="main-col"><div id="bespokePage"><h1> CROCUS DLA</h1><p>Up to 23 fully funded PhD studentships,
including machine learning projects. International students are welcome.</p>
<p><b>The deadline for applications is Monday 11th January 2027, 1600 GMT.</b></p></div></div>"""


def test_program_ids_merge_partner_institutions():
    assert project_id(PROG_A) == project_id(PROG_B) == project_id(PROG_CANON) == "prog6738"
    assert project_id(URL) == "123"
    assert project_id("https://example.com/phds/project/x/?p1") is None
    cards, has_next, unknown = parse_results(PROG_LIST, 1)
    assert list(cards) == ["prog6738", "123"] and not has_next and unknown == 0
    assert cards["prog6738"].url == PROG_A
    assert cards["prog6738"].funding == "Funded PhD Programme (Students Worldwide)"
    assert cards["prog6738"].institution == "Institute of Zoology"


def test_parse_program_page():
    card = Card(url=PROG_A, funding="Funded PhD Programme (Students Worldwide)", institution="Institute of Zoology")
    ad = parse_listing(PROG_PAGE, "prog6738", card)
    assert (ad.title, ad.employer, ad.url) == ("CROCUS DLA", "University of Reading", PROG_CANON)
    assert ad.closes_date == date(2027, 1, 11)
    assert ad.funding_for == card.funding and "machine learning" in ad.description
    assert ad.description.startswith("Doctoral programme listing")


def test_program_page_rejects_other_listing():
    try:
        parse_program(PROG_PAGE, "prog9999")
    except ValueError:
        return
    raise AssertionError("a page for another programme must not be accepted")


def test_run_findaphd_screens_programmes_once(db, tmp_path):
    fetcher = Pages({FIRST: PROG_LIST, URL: AD, PROG_A: PROG_PAGE})
    llm = FakeLLM({"score_position": [_v(), _v()]})
    with session_scope() as s:
        rep = run_findaphd(s, fetcher, llm, tmp_path, TODAY)
        assert (rep.listed, rep.programs, rep.archived, rep.added, rep.errors) == (2, 1, 2, 2, [])
        assert PROG_B not in fetcher.calls
        links = {p.link for p in s.scalars(select(Position))}
        assert links == {URL, PROG_CANON}
        assert any("Funded PhD Programme (Students Worldwide)" in prompt for _, prompt in llm.calls)
    assert (tmp_path / "findaphd_project_prog6738.html").exists()


def test_real_results_page_keeps_projects_and_programmes():
    cards, has_next, unknown = parse_results((FIXTURES / "findaphd_list_page1.html").read_text("utf-8"), 1)
    assert has_next and unknown == 0
    assert len(cards) == 10                          # 8 projects + 2 programmes (7 cards, merged by programme)
    assert {c for c in cards if c.startswith("prog")} == {"prog6738", "prog7217"}
    assert cards["198682"].deadline == "18 December 2026"


def test_real_project_without_supervisor_profiles():
    html = (FIXTURES / "findaphd_project_202889.html").read_text("utf-8")
    ad = parse_project(html, "202889")
    assert ad.employer == "University of Hull" and ad.closes_date == date(2027, 1, 6)
    assert ad.description.startswith("Supervisors: Prof James Gilbert, Dr Domino Joyce, Dr Michael Smith\n")
    assert ad.funding_for == "Competition Funded PhD Project (Students Worldwide)"


def test_ids_with_department_prefix_and_programme_spelling():
    base = FINDAPHD.base
    assert project_id(base + "/phds/project/bisci/?d8229p200835") == project_id(base + "/phds/project/bisci/?p200835") == "200835"
    assert project_id(base + "/phds/programme/kcl-informatics/?p6338") == "prog6338"
    assert project_id(base + "/phds/program/x/?i805p6738&utm=1") == "prog6738"
    assert project_id(base + "/phds/project/x/") is None
    row = '<div class="resultsRow phd-result"><a href="{}">x</a></div>'
    html = ('<div id="SearchResults">' + row.format("/phds/project/bisci/?d8229p200835")
            + row.format("/phds/programme/kcl-informatics/?p6338") + "</div>")
    cards, _, unknown = parse_results(html, 1)
    assert list(cards) == ["200835", "prog6338"] and unknown == 0


def test_score_parts_out_of_range_are_clamped():
    v = _v(computing_share=45, skills_fit=-3, topic_proximity=20.4)
    assert (v.computing_share, v.skills_fit, v.topic_proximity, v.score) == (40, 0, 20, 60)


def test_refused_advert_is_recorded_for_manual_review_not_retried(db, tmp_path):
    from app.db.models import Meta
    from app.llm.client import LLMRefused

    class Refusing(FakeLLM):
        def complete(self, task, prompt, **kw):
            self.calls.append((task, prompt))
            raise LLMRefused("score_position: the model declined to answer (stop_reason=refusal)")

    fetcher = Pages({FIRST: LIST, URL: AD})
    llm = Refusing({})
    with session_scope() as s:
        first = run_findaphd(s, fetcher, llm, tmp_path, TODAY)
        second = run_findaphd(s, fetcher, llm, tmp_path, TODAY)
        assert not first.errors and len(first.review) == 1 and "Robots" in first.review[0]
        assert "دستی" in first.summary() and "بررسی کامل" in first.summary()
        assert len(llm.calls) == 1 and not second.review
        assert s.get(Meta, "findaphd:123").value == "needs-review"


def test_low_score_is_not_tracked(db, tmp_path):
    from app.db.models import Meta
    fetcher = Pages({FIRST: LIST, URL: AD})
    with session_scope() as s:
        rep = run_findaphd(s, fetcher, FakeLLM({"score_position": [_v(score=30)]}), tmp_path, TODAY)
        assert (rep.added, rep.low_fit) == (0, 1) and not s.scalars(select(Position)).all()
        assert s.get(Meta, "findaphd:123").value.startswith("low fit")
