"""Getting pages from the job sites, politely.

Plain HTTP with a pause between requests and backoff on HTTP 429. A page that is not real
content (an access page instead of the advert) is reported as blocked, never parsed.
"""
import random
import time
from dataclasses import dataclass

# An honest name: the program says what it is instead of pretending to be Chrome.
UA = "PhDAssistant/0.1 (personal PhD search tool)"
_NOT_CONTENT = ("<title>just a moment...</title>", "verify you are human",
                "enable javascript and cookies to continue")


@dataclass
class Page:
    url: str
    status: int
    html: str
    method: str
    blocked: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.blocked and not self.error


def looks_blocked(status: int, html: str) -> bool:
    if status in (403, 503):
        return True
    head = html[:6000].lower()
    return any(m in head for m in _NOT_CONTENT)


class _Polite:
    def __init__(self, delay: float, sleep=time.sleep):
        self.delay, self._sleep, self._last = delay, sleep, 0.0

    def wait(self):
        gap = self.delay + random.uniform(0, self.delay / 3) - (time.monotonic() - self._last)
        if gap > 0:
            self._sleep(gap)
        self._last = time.monotonic()


class HttpFetcher:
    method = "http"

    def __init__(self, delay: float = 3.5, sleep=time.sleep, client=None):
        self._polite = _Polite(delay, sleep)
        self._sleep = sleep
        if client is None:
            import httpx
        self._client = client or httpx.Client(
            headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9",
                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            follow_redirects=True, timeout=30)

    def get(self, url: str) -> Page:
        for attempt in range(4):
            self._polite.wait()
            try:
                r = self._client.get(url)
            except Exception as e:                       # network error: report, don't crash the run
                return Page(url, 0, "", self.method, error=f"{type(e).__name__}: {e}")
            if r.status_code == 429 and attempt < 3:
                retry_after = r.headers.get("retry-after") or str(15 * (attempt + 1))
                if not retry_after.isdecimal() or int(retry_after) > 120:
                    return Page(str(r.url), 429, "", self.method, error="rate limited; retry next run")
                self._sleep(int(retry_after))
                continue
            text = r.text
            if r.content[:2] == b"\x1f\x8b":            # gzipped response
                import gzip
                text = gzip.decompress(r.content).decode("utf-8", "replace")
            blocked = r.headers.get("cf-mitigated") == "challenge" or looks_blocked(r.status_code, text)
            return Page(str(r.url), r.status_code, text, self.method, blocked)
        return Page(url, 429, "", self.method, error="rate limited")

    def close(self):
        self._client.close()
