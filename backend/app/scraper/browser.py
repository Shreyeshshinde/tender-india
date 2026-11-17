"""
Browser-based fetcher (Playwright), the BACKUP engine.

Use it only for portals where plain HTTP does not return the data
(for example pages that load tenders with JavaScript).

It has the SAME methods as GePNICClient (get, org_list_url, base_url),
so crawl() and all parsers work with it without any change:

    client = BrowserClient()          # instead of GePNICClient()
    crawl(client, on_record=save)

Install once:
    pip install playwright
    playwright install chromium
"""
import logging
import time
from urllib.parse import urljoin

from .cppp import CPPP_BASE, ORG_LIST_PATH

log = logging.getLogger(__name__)


class BrowserClient:
    def __init__(self, base_url=CPPP_BASE, delay_seconds=2.0, headless=True, timeout_ms=45000):
        from playwright.sync_api import sync_playwright  # imported here so it stays optional

        self.base_url = base_url
        self.delay = delay_seconds
        self.timeout_ms = timeout_ms
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        # One context = one cookie jar, so session-based links keep working.
        self._context = self._browser.new_context(locale="en-IN")
        self._page = self._context.new_page()
        self._last = 0.0

    def reset_session(self):
        """New cookie jar = new portal session (old links expire)."""
        self._context.close()
        self._context = self._browser.new_context(locale="en-IN")
        self._page = self._context.new_page()

    def get(self, url, slow_retries=None):
        url = urljoin(self.base_url, url)
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
        self._last = time.time()
        return self._page.content()

    def get_response(self, url):
        """Download a file with the browser's cookies (same session as the pages)."""
        url = urljoin(self.base_url, url)
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self._context.request.get(url, timeout=self.timeout_ms)
        self._last = time.time()
        return _Resp(r.status, r.headers, r.body())

    def org_list_url(self):
        return urljoin(self.base_url, ORG_LIST_PATH)

    def close(self):
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _Resp:
    """Small stand-in for requests.Response so callers can treat both engines the same."""

    def __init__(self, status, headers, content):
        self.status_code = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.content = content

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")
