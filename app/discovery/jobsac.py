"""Parse jobs.ac.uk search results and advert pages."""
import re
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from app.tracker.parsing import parse_deadline

BASE = "https://www.jobs.ac.uk"
_CODE = re.compile(r"/job/([A-Z0-9]+)")


def job_code(url: str | None) -> str | None:
    m = _CODE.search(url or "")
    return m.group(1) if m else None


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def tidy_date(text: str) -> str:
    """'6th January 2027' -> '6 Jan 2027' (tracker style)."""
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text or "").strip()
    d = parse_deadline(t)
    return f"{d.day} {d:%b %Y}" if d else t


@dataclass
class Listing:
    code: str
    url: str
    title: str
    employer: str
    department: str = ""
    location: str = ""
    salary: str = ""
    closes: str = ""


@dataclass
class Advert:
    code: str
    url: str
    title: str
    employer: str
    location: str = ""
    funding_for: str = ""
    funding_amount: str = ""
    hours: str = ""
    placed_on: str = ""
    closes: str = ""
    closes_date: date | None = None
    apply_url: str = ""
    subjects: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ListPage:
    listings: list[Listing]
    page: int
    pages: int
    has_next: bool


def parse_list(html: str) -> ListPage:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for box in soup.select("div.j-search-result__result"):
        a = box.select_one("div.j-search-result__text > a[href*='/job/']")
        if not a:
            continue
        url = BASE + a["href"].split("?")[0]
        loc = next((_txt(d).removeprefix("Location:").strip() for d in box.select("div.j-search-result__text > div")
                    if _txt(d).startswith("Location:")), "")
        sal = _txt(box.select_one(".j-search-result__info")).removeprefix("Salary:").strip()
        closes = _txt(box.select_one(".j-search-result__date--blue"))
        out.append(Listing(job_code(url), url, _txt(a), _txt(box.select_one(".j-search-result__employer")),
                           _txt(box.select_one(".j-search-result__department")), loc, sal, closes))
    m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", soup.get_text(" "))
    page, pages = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
    nxt = any(_txt(a) == "Next" for a in soup.select(".j-search-content__pagination a"))
    return ListPage(out, page, pages, nxt)


def _page_title_parts(soup) -> tuple[str, str]:
    """'<title>PhD in X at University of Y</title>' -> ('PhD in X', 'University of Y')."""
    t = _txt(soup.title)
    if " at " in t:
        left, right = t.rsplit(" at ", 1)
        return left.strip(), right.strip()
    return t, ""


def _description(soup) -> str:
    """Standard adverts use #job-description; enhanced (employer-branded) adverts use their own
    containers, so fall back to the block with the most paragraphs."""
    for sel in ("#job-description", ".j-enhanced__advert-text"):
        d = soup.select_one(sel)
        if d and len(d.get_text(strip=True)) > 200:
            return d.get_text("\n", strip=True)
    blocks = soup.find_all(["div", "section", "article"])
    best = max(blocks, key=lambda e: len(e.find_all("p", recursive=False)), default=None)
    return best.get_text("\n", strip=True) if best and best.find_all("p", recursive=False) else ""


def parse_advert(html: str, url: str) -> Advert:
    soup = BeautifulSoup(html, "lxml")
    table = {}
    for th in soup.select("th"):                    # standard and enhanced adverts share these rows
        key = _txt(th)
        if key.endswith(":") and th.find_next_sibling("td") is not None:
            table.setdefault(key.rstrip(":").lower(), _txt(th.find_next_sibling("td")))
    apply = soup.select_one(".row-7 a[href]") or soup.select_one("a[href] #job-apply-button")
    apply_url = apply["href"] if apply and apply.name == "a" else ""
    subjects = [i.get("value", "") for i in soup.select("input.parent-category, input.child-category")]
    closes = tidy_date(table.get("closes", ""))
    page_title, page_employer = _page_title_parts(soup)
    return Advert(
        code=job_code(url) or "", url=url,
        title=_txt(soup.select_one("h1.j-advert__title")) or page_title,
        employer=_txt(soup.select_one(".j-advert__employer")) or page_employer,
        location=table.get("location", ""),
        funding_for=table.get("funding for", ""), funding_amount=table.get("funding amount", ""),
        hours=table.get("hours", ""), placed_on=tidy_date(table.get("placed on", "")), closes=closes,
        closes_date=parse_deadline(closes), apply_url=apply_url, subjects=[s for s in subjects if s],
        description=_description(soup))
