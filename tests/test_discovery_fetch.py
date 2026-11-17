from app.discovery.fetch import HttpFetcher, looks_blocked
from app.discovery.probe import _first_details
from app.discovery.sources import FINDAPHD, JOBSAC


def test_page_urls():
    assert JOBSAC.page_url(1).endswith("startIndex=1") and JOBSAC.page_url(3).endswith("startIndex=51")
    assert FINDAPHD.page_url(2).endswith("PG=2")


def test_not_content_detection():
    assert looks_blocked(403, "<html>forbidden</html>")
    assert looks_blocked(200, "<html><head><title>Just a moment...</title></head></html>")
    assert not looks_blocked(200, "<html><title>PhD in AI</title></html>")


def test_detail_links():
    html = ('<a href="/phds/project/agentic-ai-for-x/?p199020">x</a>'
            '<a href="/phds/project/agentic-ai-for-x/?p199020">dup</a><a href="/phds/project/y/?p5">y</a>')
    assert _first_details(FINDAPHD, html) == ["https://www.findaphd.com/phds/project/agentic-ai-for-x/?p199020",
                                             "https://www.findaphd.com/phds/project/y/?p5"]
    assert _first_details(JOBSAC, '<a href="/job/DPQ123/phd-in-ai">a</a>') == ["https://www.jobs.ac.uk/job/DPQ123/phd-in-ai"]


class _Resp:
    def __init__(self, code, text="ok", headers=None):
        self.status_code, self.text, self.headers, self.url = code, text, headers or {}, "u"
        self.content = text.encode()


class _Client:
    def __init__(self, codes):
        self.codes = list(codes)

    def get(self, url):
        return _Resp(self.codes.pop(0))


def test_http_backs_off_on_429():
    slept = []
    f = HttpFetcher(delay=0, sleep=slept.append, client=_Client([429, 429, 200]))
    p = f.get("u")
    assert p.ok and [s for s in slept if s >= 15] == [15, 30]
