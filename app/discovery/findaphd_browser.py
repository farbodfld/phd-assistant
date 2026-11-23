"""Fetch FindAPhD with the scraper's persistent, headed Chrome session."""
from contextlib import ExitStack
from shutil import which

from filelock import FileLock, Timeout
from patchright.sync_api import Error, TimeoutError as BrowserTimeout, sync_playwright

from app.config import get_settings
from app.discovery.fetch import Page, _Polite


class FindAPhDBrowser:
    method = "browser"

    def __init__(self):
        self._resources = ExitStack()
        self._page = None
        self._polite = _Polite(3.5)
        self._status = 0
        self._last_list = None

    def _open(self):
        folder = get_settings().findaphd_archive_dir
        folder.mkdir(parents=True, exist_ok=True)
        self._resources.enter_context(FileLock(folder / "browser.lock", timeout=0))
        driver = self._resources.enter_context(sync_playwright())
        # Chrome on the laptop and x86 Lightsail. Debian ships Chromium on ARM.
        chromium = which("chromium") if not which("google-chrome") else None
        browser = {"executable_path": chromium} if chromium else {"channel": "chrome"}
        context = driver.chromium.launch_persistent_context(
            folder / "browser-profile", headless=False, no_viewport=True, **browser)
        self._resources.callback(context.close)
        self._page = context.pages[0] if context.pages else context.new_page()
        self._page.on("response", self._record_status)

    def _record_status(self, response):
        if response.request.resource_type == "document" and response.frame == self._page.main_frame:
            self._status = response.status

    def get(self, url):
        if self._page is None:
            try:
                self._open()
            except (Error, Timeout, OSError) as exc:
                self.close()
                return Page(url, 0, "", self.method, error=f"browser startup: {exc}")
        self._polite.wait()
        detail = "/phds/project/" in url or "/phds/program" in url
        if "/phds/project/" in url:
            selector = ".phd-sections__description .phd-sections__content"
        elif detail:
            # Programme pages are free-form; the canonical link only exists on the real page, not a challenge.
            selector = 'link[rel~="canonical"][href*="/phds/program"]'
        else:
            selector = "#SearchResults"
        self._status = 0
        try:
            self._page.goto(url, referer=self._last_list, wait_until="domcontentloaded", timeout=45000)
            if self._status in (404, 410, 429):
                return Page(url, self._status, "", self.method)
            # A first navigation can be a 403 followed by an automatic reload.
            # Wait for actual content before deciding whether the run is blocked.
            target = self._page.locator(selector)
            if detail:                     # a taken-down advert shows a notice instead of its content
                target = target.or_(self._page.get_by_text("no longer listed", exact=False))
            target.first.wait_for(state="attached", timeout=get_settings().findaphd_page_wait_seconds * 1000)
            html = self._page.content()
        except BrowserTimeout:
            return Page(url, self._status, "", self.method, blocked=self._status == 403,
                        error="timed out waiting for FindAPhD content")
        except Error as exc:
            return Page(url, self._status, "", self.method, error=f"browser navigation: {exc}")
        if not detail:
            self._last_list = self._page.url
        return Page(self._page.url, self._status, html, self.method)

    def close(self):
        self._resources.close()
        self._page = None
