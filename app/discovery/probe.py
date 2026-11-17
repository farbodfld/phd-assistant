"""`phd probe-sites`: can jobs.ac.uk be read from this machine? Saves samples for parser work."""
import re
from pathlib import Path

from app.discovery.fetch import HttpFetcher, Page
from app.discovery.sources import JOBSAC


def _first_details(src, html: str, n: int = 2) -> list[str]:
    seen = []
    for m in re.finditer(src.detail_pattern, html):
        u = src.base + m.group(1).replace("&amp;", "&")
        if u not in seen:
            seen.append(u)
        if len(seen) == n:
            break
    return seen


def _line(site, kind, p: Page, path: Path | None):
    state = "OK" if p.ok else ("BLOCKED" if p.blocked else (p.error or f"HTTP {p.status}"))
    return f"{site:9} {p.method:8} {kind:7} {state:10} {len(p.html):>8} bytes  {path.name if path else ''}"


def _save(out_dir: Path, name: str, p: Page) -> Path | None:
    if not p.html:
        return None
    path = out_dir / name
    path.write_text(p.html, encoding="utf-8")
    return path


def probe(out_dir: Path, print=print) -> list[str]:
    """jobs.ac.uk search page and two adverts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []

    def report(site, kind, page, name):
        lines.append(_line(site, kind, page, _save(out_dir, name, page)))
        print(lines[-1])

    f = HttpFetcher(delay=3.5)
    try:
        page = f.get(JOBSAC.page_url(1))
        report("jobsac", "list", page, "jobsac_list.html")
        for i, u in enumerate(_first_details(JOBSAC, page.html) if page.ok else [], 1):
            report("jobsac", "detail", f.get(u), f"jobsac_detail{i}.html")
    finally:
        f.close()

    return lines
