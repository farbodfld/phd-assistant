from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db.models import ExcludedPosition, Meta, Notification, Position
from app.discovery.fetch import Page
from app.discovery.jobsac import parse_advert, parse_list, tidy_date
from app.discovery.pipeline import run_jobsac, same_title
from app.discovery.screen import Screen
from tests.fakes import FakeLLM

FIX = Path(__file__).parent / "fixtures"
LIST = (FIX / "jobsac_list.html").read_text(encoding="utf-8")
UK_ONLY = (FIX / "jobsac_advert_uk_only.html").read_text(encoding="utf-8")
INTL = (FIX / "jobsac_advert_2.html").read_text(encoding="utf-8")
TODAY = date(2026, 11, 16)


def test_parse_list_and_advert():
    lp = parse_list(LIST)
    assert len(lp.listings) == 25 and lp.pages == 5 and lp.has_next
    first = lp.listings[0]
    assert first.code == "DTR061" and first.employer == "University of Bristol" and first.location == "Bristol"
    a = parse_advert(UK_ONLY, first.url)
    assert a.funding_for == "UK Students" and a.closes == "6 Jan 2027" and a.closes_date == date(2027, 1, 6)
    assert "Computer Sciences" in a.subjects and a.description
    b = parse_advert(INTL, "https://www.jobs.ac.uk/job/DTQ702/x")
    assert "International Students" in b.funding_for


def test_tidy_date_and_titles():
    assert tidy_date("31st January 2027") == "31 Jan 2027"
    assert same_title("PhD Studentship: Learning and Decision-Making Under Uncertainty in Public Health",
                      "Learning and Decision-Making Under Uncertainty in Public Health")
    assert not same_title("Medical AI for imaging", "Agentic AI for code")


class FakeFetcher:
    """Serves one list page (pretending it is the last) and two adverts; other adverts 404."""
    def __init__(self, removed_url=None):
        self.calls = []
        self.removed_url = removed_url

    def get(self, url):
        self.calls.append(url)
        if "/search/" in url:
            return Page(url, 200, LIST.replace("Page 1 of 5", "Page 1 of 1").replace(">\n                Next", ">x"), "http")
        if "DTR061" in url:
            return Page(url, 200, UK_ONLY, "http")
        if "DTQ" in url and url != self.removed_url:
            return Page(url, 200, INTL, "http")
        if url == self.removed_url:
            return Page(url, 404, "not found", "http")
        return Page(url, 200, INTL, "http")


def _v(**kw):
    base = dict(topic_relevant=True, score=66, funding="full_international",
                funding_note="Full tuition and UKRI stipend, open to international students.", england=True,
                supervisor_contact="See advert", apply_method="Portal", application_route="Bristol PGR portal",
                area="AI / decision making", note_fa="مرتبط است.")
    base.update(kw)
    return Screen(**base)


def test_run_adds_excludes_dedupes_and_greys(db):
    from app.db.base import session_scope
    with session_scope() as s:
        # already tracked (from FindAPhD): the same project must not be added twice
        s.add(Position(source="findaphd", title="Learning and Decision-Making Under Uncertainty in Public Health",
                       university="University of Bristol", score=60))
        # tracked jobs.ac.uk advert that has disappeared and was never acted on
        gone = Position(source="jobsac", title="Old advert", university="X",
                        link="https://www.jobs.ac.uk/job/DZZ999/old-advert")
        s.add(gone)
        s.flush()
        lp = parse_list(LIST)
        n_new = len(lp.listings) - 2       # minus the duplicate and the music one
        outputs = [_v(topic_relevant=False)]                         # DTR061 music: off topic
        outputs += [_v(funding="home_only", exclude_reason="Home fees only")]  # next one: excluded
        outputs += [_v(score=70 - i % 30) for i in range(n_new)]     # the rest: added
        llm = FakeLLM({"score_position": outputs})
        f = FakeFetcher(removed_url=gone.link)
        rep = run_jobsac(s, f, llm, today=TODAY, progress=lambda *_: None)

        assert rep.complete and rep.listings == 25
        assert rep.off_topic == 1 and len(rep.excluded) == 1 and len(rep.duplicates) == 1
        assert len(rep.new) == 25 - 3
        new = s.scalars(select(Position).where(Position.source == "jobsac", Position.closed.is_(False))).all()
        assert all(p.status.startswith("2026-11-16: NEW.") for p in new)
        assert gone.closed and "Listing removed" in gone.status and rep.removed == ["Old advert"]
        assert s.scalar(select(ExcludedPosition.reason)) == "Home fees only"
        note = s.scalars(select(Notification).where(Notification.ref_type == "discovery")).one().text
        assert "jobs.ac.uk" in note

        # second run: nothing is screened again
        llm2 = FakeLLM({"score_position": []})
        rep2 = run_jobsac(s, FakeFetcher(), llm2, today=TODAY, progress=lambda *_: None)
        assert llm2.calls == [] and rep2.new == [] and len(s.get(Meta, "seen:jobsac").value) == 25
