"""The advert text behind a tracker row, for writing to its supervisor."""
from pathlib import Path

from app.config import get_settings
from app.db.models import Position


def _findaphd(p: Position):
    from app.discovery.findaphd import parse_listing, project_id
    code = project_id(p.link)
    if not code:
        return None
    path = Path(get_settings().findaphd_archive_dir) / f"findaphd_project_{code}.html"
    if not path.exists():
        return None
    try:
        return parse_listing(path.read_text(encoding="utf-8"), code)
    except ValueError:
        return None


def _jobsac(p: Position, fetcher):
    from app.discovery.jobsac import job_code, parse_advert
    code = job_code(p.link)
    if not code:
        return None
    cache = Path(get_settings().positions_dir).parent / "adverts" / f"jobsac_{code}.html"
    if cache.exists():
        html = cache.read_text(encoding="utf-8")
    else:
        if fetcher is None:
            return None
        page = fetcher.get(p.link)
        if not page.ok:
            return None
        html = page.html
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(html, encoding="utf-8")
    a = parse_advert(html, p.link)
    return a if a.title and a.description else None


def advert_text(p: Position, fetcher=None) -> str:
    """The full advert when it can be read, otherwise what the tracker row knows."""
    a = _findaphd(p) if "findaphd.com" in (p.link or "") else _jobsac(p, fetcher) if "jobs.ac.uk" in (p.link or "") else None
    head = (f"Project: {p.title}\nUniversity: {p.university}\nSupervisor / contact: {p.supervisor_contact}\n"
            f"Deadline: {p.deadline_text or p.deadline_date or 'not stated'}\nApply method: {p.apply_method}\n"
            f"Funding (tracker): {p.funding}; {p.fit_note or ''}\nLink: {p.link}\n")
    if a is None:
        return head + "\n(Full advert text not available; use only the fields above.)"
    return (head + f"Funding label: {a.funding_for}\nFunding notes: {a.funding_amount}\n\n"
            f"Advert text:\n{a.description[:9000]}")
