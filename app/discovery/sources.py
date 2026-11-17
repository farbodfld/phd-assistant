"""Search pages per site. Same filters Farbod used by hand (status-update.md, section 3)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str            # "jobsac" | "findaphd"  (matches Position.source)
    list_url: str       # "{page}" or "{start}" placeholder
    detail_pattern: str  # regex for detail links found on a list page
    page_size: int
    base: str

    def page_url(self, n: int) -> str:
        """n starts at 1."""
        if self.key == "findaphd" and n == 1:
            return self.list_url.removesuffix("&PG={page}")
        return self.list_url.format(page=n, start=(n - 1) * self.page_size + 1)


JOBSAC = Source(
    key="jobsac",
    list_url=("https://www.jobs.ac.uk/search/?jobTypeFacet%5B0%5D=phds"
              "&academicDisciplineFacet%5B0%5D=computer-sciences&pageSize=25&startIndex={start}"),
    detail_pattern=r'href="(/job/[A-Z0-9]+/[^"#?]*)"',
    page_size=25, base="https://www.jobs.ac.uk")

FINDAPHD = Source(
    key="findaphd",
    list_url="https://www.findaphd.com/phds/united-kingdom/computer-science/non-eu-students/?h1M7gc11&PG={page}",
    detail_pattern=r'href="(/phds/project/[^"#?]+/\?p\d+)"',
    page_size=15, base="https://www.findaphd.com")

SOURCES = {s.key: s for s in (JOBSAC, FINDAPHD)}

