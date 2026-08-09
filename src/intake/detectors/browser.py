"""Browser-render detector tier.

For career sites whose data is token-gated or client-rendered (apple,
tiktok, meta). A headless Chromium loads the human-facing page; the
rendered DOM is parsed with fixed patterns per site. No LLM.

Playwright is an optional dependency:
  pip install -e '.[browser]' && playwright install chromium

Parsing is split from fetching so parsers stay fixture-testable without
a browser.
"""

from __future__ import annotations

import re

from ..schema import RawDetection, Source
from .base import looks_like_swe_internship

APPLE_SEARCH_URL = "https://jobs.apple.com/en-us/search?team=internships-STDNT&sort=newest"
# Rendered DOM: result links carry /en-us/details/<positionId>/<slug>
APPLE_LINK_RE = re.compile(
    r'href="(?:https://jobs\.apple\.com)?(/en-us/details/(\d+)/([^"?#]+))[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")


def parse_apple_search(html: str) -> list[RawDetection]:
    out: list[RawDetection] = []
    seen: set[str] = set()
    for _path, pos_id, slug, inner in APPLE_LINK_RE.findall(html):
        title = TAG_RE.sub("", inner).strip()
        if not title:
            title = slug.replace("-", " ").title()
        if pos_id in seen or not looks_like_swe_internship(title):
            continue
        seen.add(pos_id)
        out.append(
            RawDetection(
                source=Source.CUSTOM,
                company="Apple",
                title=title,
                url=f"https://jobs.apple.com/en-us/details/{pos_id}/{slug}",
                payload={"position_id": pos_id},
            )
        )
    return out


def fetch_rendered(url: str, wait_selector: str, timeout_ms: int = 30_000) -> str:
    """Load url in headless Chromium, wait for the selector, return HTML.
    Raises RuntimeError with install guidance when playwright is absent."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "browser tier needs playwright: pip install -e '.[browser]' "
            "&& playwright install chromium"
        ) from e
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_selector(wait_selector, timeout=timeout_ms)
            return page.content()
        finally:
            browser.close()


class AppleBrowserDetector:
    name = "apple_browser"

    def poll(self) -> list[RawDetection]:
        html = fetch_rendered(APPLE_SEARCH_URL, 'a[href*="/details/"]')
        return parse_apple_search(html)


BROWSER_DETECTORS = {
    "apple": AppleBrowserDetector,
}
